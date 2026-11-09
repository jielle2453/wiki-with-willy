"""自我檢查（換版前先驗新版，D-230）：kb selftest 的本體。檢查它自己所在的這份 plugin（paths.plugin_root()），不碰 cwd 的知識庫。
換版之前，舊版用她的 Python 跑新版的這支（_lib/upgrade.verify）；發行程式打包時也跑（tools/release.py）。

步驟照順序，第一個不過就停：import（scripts/kb/ 底下每個模組）→ version（plugin.json）→ vault（系統暫存夾用這份的 kb init 建小知識庫，
清單網址設成空的）→ hooks（hooks.json 每個註冊的 command 用子行程跑一次，不經 shell）→ upgrade（kb.py upgrade --background）→ obsidian（obsidian/ 的三個檔在、manifest.json 是合法 JSON、版號等於 plugin.json，不跑 node，#22）。
收尾一律刪掉暫存小知識庫。回 {"ok","version","step","file","line","why"}；自己出錯也回 ok false（why 是例外名稱），不丟。
why 不含絕對路徑、遮過值（credentials.mask_secrets）。子行程經 _run，逾時殺整棵行程樹。
"""
import argparse
import ast
import contextlib
import importlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

# 只 import 標準函式庫：kb 的模組留到 import 那步之後才拿（某個模組壞了，這支照樣回 step import）
ROOT_FROM_FILE = Path(__file__).resolve().parents[3]  # scripts/kb/_lib/selftest.py → plugin 根
TEMP_PREFIX = "wiki-with-willy-selftest-"
VAULT_NAME = "知識庫"
SESSION = "selftest"
STEPS = ("import", "version", "vault", "hooks", "upgrade", "obsidian")
MARKER = "【wiki-with-willy】kb 指令"  # 開場第一行的前綴（出錯那行也有「【wiki-with-willy】」，所以比到「kb 指令」）
PLAIN_FILE = "系統/客戶設定檔.md"  # 小知識庫裡一份普通的檔：守門讀它不能 deny（裁定 9）
GUARD_ERROR_HEAD = "守門出錯"
ERROR_LOG_REL = ".system/hook-errors.log"  # hook 吞了例外會寫這裡（裁定 10）
_HOOK_FILE = re.compile(r"[A-Za-z0-9_]+\.py", re.ASCII)
_TB_FILE = re.compile(r'File "([^"]+)", line (\d+)')
_ABS_PATH = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|(?<![\w.])/)[^\s"\'，。、；：）)]*')


class Failed(Exception):
    """這一步不過：why 是一句話，file、line 取得到才有。"""

    def __init__(self, why, file=None, line=None):
        super().__init__(why)
        self.why, self.file, self.line = why, file, line


# ---------- 呼叫層（逾時殺整棵行程樹） ----------

def _run(argv, payload, timeout, cwd, env):
    """跑一個子行程，stdin 給 payload（bytes 或 None）；回 (結束碼, stdout, stderr)；逾時殺整棵樹再丟 TimeoutExpired。"""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cwd), env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out, err = p.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.communicate()
        raise
    return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        p.kill()


# ---------- 小工具 ----------

def clean_why(text):
    """一句話：壓成一行、絕對路徑換成「…」、遮值。"""
    one = _ABS_PATH.sub("…", " ".join(str(text).split()))
    try:
        from kb._lib import credentials
        one = credentials.mask_secrets(one)
    except Exception:  # noqa: BLE001  credentials 自己 import 不到（import 那步不過）：只換路徑
        pass
    return one[:300]


def _rel(path, root):
    """root 底下的檔回相對路徑（/ 分隔）；不在底下、<frozen …> 這類回 None。兩邊都走 realpath（8.3 短名、大小寫）。"""
    if not path or str(path).startswith("<"):
        return None
    try:
        a, r = os.path.realpath(str(path)), os.path.realpath(str(root)).rstrip("\\/")
    except (OSError, ValueError):
        return None
    if not os.path.normcase(a).startswith(os.path.normcase(r) + os.sep):
        return None
    return a[len(r) + 1:].replace("\\", "/")


def where_in(stderr, root):
    """stderr 最後一段 traceback 裡、在 root 底下的最後一個 File "…", line N；沒有回 (None, None)。"""
    text = str(stderr or "")
    start = text.rfind("Traceback (most recent call last):")
    found = (None, None)
    for path, line in _TB_FILE.findall(text[start:] if start >= 0 else text):
        rel = _rel(path, root)
        if rel is not None:
            found = (rel, int(line))
    return found


def _where_exc(e, root):
    """例外的檔與行：SyntaxError 用它自己的 filename／lineno，其他取 traceback 在 root 底下的最後一格。"""
    if isinstance(e, SyntaxError) and e.filename:
        rel = _rel(e.filename, root)
        if rel is not None:
            return rel, e.lineno
    found = (None, None)
    for frame in traceback.extract_tb(e.__traceback__):
        rel = _rel(frame.filename, root)
        if rel is not None:
            found = (rel, frame.lineno)
    return found


def _exc_why(e):
    msg = str(e.msg if isinstance(e, SyntaxError) else e)
    return clean_why(f"{type(e).__name__}: {msg}" if msg else type(e).__name__)


def command_parts(command):
    """hooks.json 的 command 字串 → (hooks/ 底下的檔名, 參數清單)；看不懂（不是 hooks/<名>.py、有 ..、引號沒關）回 None。"""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        norm = tok.replace("\\", "/")
        if "/hooks/" in norm:
            name = norm.rsplit("/hooks/", 1)[1]
            if not _HOOK_FILE.fullmatch(name):
                return None
            return name, tokens[i + 1:]
    return None


# ---------- 各步驟 ----------

def _step_import(root):
    base = root / "scripts"
    for p in sorted((base / "kb").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        mod = ".".join(p.relative_to(base).with_suffix("").parts)
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        try:
            importlib.import_module(mod)
        except BaseException as e:  # noqa: BLE001  SystemExit 也算 import 不到
            file, line = _where_exc(e, root)
            raise Failed(_exc_why(e), file or p.relative_to(root).as_posix(), line) from None


def _step_version():
    from kb._lib import paths, upgrade
    try:
        manifest = paths.read_manifest()
    except (OSError, ValueError) as e:
        raise Failed(f".claude-plugin/plugin.json 讀不到（{type(e).__name__}）", ".claude-plugin/plugin.json") from None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if upgrade.parse_version(version) is None:
        raise Failed(".claude-plugin/plugin.json 的版本看不懂", ".claude-plugin/plugin.json")
    return version


def _step_vault(base):
    from kb import init
    vault = base / VAULT_NAME
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = init.run(argparse.Namespace(path=str(vault), check=False, json=False))
    if code not in (0, None) or not (vault / ".system" / "sources.json").is_file():
        raise Failed(f"kb init 建不起小知識庫：{clean_why(buf.getvalue().strip()) or '結束碼 ' + str(code)}")
    (vault / ".system" / "config.json").write_text(
        json.dumps({"update_manifest_url": "", "update_rollout_url": ""}), encoding="utf-8")
    (base / "外面.txt").write_text("小知識庫外面的檔\n", encoding="utf-8")
    return vault


def _hook_env(root):
    env = dict(os.environ)
    env.update(CLAUDE_PLUGIN_ROOT=str(root), WIKI_WITH_WILLY_NO_BACKGROUND="1", PYTHONIOENCODING="utf-8")
    return env


def _cases(event, vault, outside):
    """一個事件要跑的 (說明, payload, cwd, 要看什麼)；要看的：None 只看結束碼、"context"、"deny"、"not_deny"。"""
    def base(cwd, **kw):
        return dict({"hook_event_name": event, "session_id": SESSION, "cwd": str(cwd)}, **kw)
    v, out = str(vault), str(outside)
    if event == "SessionStart":
        return [("開場", base(vault, source="startup"), vault, "context")]
    if event == "UserPromptSubmit":
        return [("每句話開回合", base(vault, prompt="自我檢查"), vault, None)]
    if event == "PreToolUse":
        return [("守門", base(vault, tool_name="Write",
                             tool_input={"file_path": str(Path(v) / ".system" / "sources.json"), "content": "{}"}), vault, "deny"),
                ("守門", base(vault, tool_name="Read", tool_input={"file_path": str(Path(v) / PLAIN_FILE)}), vault, "read_in_vault"),
                ("守門", base(outside.parent, tool_name="Read", tool_input={"file_path": out}), outside.parent, "not_deny")]
    if event == "PostToolUse":
        return [("寫後登記", base(vault, tool_name="Write", tool_input={"file_path": out, "content": "x"},
                                 tool_response={"filePath": out, "success": True}), vault, None)]
    if event == "Stop":
        return [("答完記日誌", base(vault, last_assistant_message="好"), vault, None)]
    if event == "SessionEnd":
        return [("對話結束", base(vault, reason="other"), vault, None)]
    if event in ("SubagentStart", "SubagentStop"):
        return [("子代理", base(vault, agent_type=SESSION, agent_id=SESSION), vault, None)]
    return [(event, {"hook_event_name": event, "session_id": SESSION, "cwd": v}, vault, None)]


def _decision(stdout):
    """守門輸出的 (決定, 理由)；讀不懂回 (None, None)。"""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None, None
    spec = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    if not isinstance(spec, dict):
        return None, None
    return spec.get("permissionDecision"), spec.get("permissionDecisionReason")


def guard_error_reason(root):
    """這份 hooks/guard.py 的 ERROR_REASON（守門自己出錯時 deny 的理由，裁定 9）；讀不到回「守門出錯」這個開頭。"""
    try:
        tree = ast.parse((Path(root) / "hooks" / "guard.py").read_bytes())
    except (OSError, SyntaxError, ValueError):
        return GUARD_ERROR_HEAD
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ERROR_REASON" for t in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return node.value.value
    return GUARD_ERROR_HEAD


def _is_guard_error(reason, error_reason):
    return isinstance(reason, str) and (reason == error_reason or reason.startswith(GUARD_ERROR_HEAD))


def _log_lines(vault):
    """小知識庫 .system/hook-errors.log 現在的行（沒有回空清單）。"""
    try:
        return (Path(vault) / ERROR_LOG_REL).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _context(stdout):
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    spec = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    ctx = spec.get("additionalContext") if isinstance(spec, dict) else None
    return ctx if isinstance(ctx, str) else None


def _registered(root):
    """hooks.json 註冊的 (事件, command) 照檔案順序。"""
    try:
        data = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Failed(f"hooks/hooks.json 讀不到（{type(e).__name__}）", "hooks/hooks.json") from None
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        raise Failed("hooks/hooks.json 沒有 hooks", "hooks/hooks.json")
    out = []
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            for h in (group.get("hooks") if isinstance(group, dict) else None) or []:
                if isinstance(h, dict) and h.get("type") == "command":
                    out.append((event, str(h.get("command") or "")))
    return out


def _step_hooks(root, vault, outside, seconds):
    env = _hook_env(root)
    error_reason = guard_error_reason(root)
    for event, command in _registered(root):
        parts = command_parts(command)
        if parts is None:
            raise Failed(f"hooks/hooks.json 的 {event} 指令看不懂", "hooks/hooks.json")
        name, args = parts
        rel = f"hooks/{name}"
        script = root / "hooks" / name
        if not script.is_file():
            raise Failed(f"hooks/hooks.json 的 {event} 指到的 {rel} 不在", "hooks/hooks.json")
        for label, payload, cwd, expect in _cases(event, vault, outside):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            logged = len(_log_lines(vault))
            try:
                code, out, err = _run([sys.executable, str(script), *args], data, seconds, cwd, env)
            except subprocess.TimeoutExpired:
                raise Failed(f"{label}（{rel}）逾時：超過 {seconds} 秒沒結束") from None
            file, line = where_in(err, root)
            if code != 0:
                raise Failed(f"{label}（{rel}）結束碼 {code}", file, line)
            decision, reason = _decision(out)
            if expect == "context" and not (_context(out) or "").startswith(MARKER):
                raise Failed(f"{label}（{rel}）沒有帶出「{MARKER}」那行", file, line)
            if expect == "deny" and (decision != "deny" or _is_guard_error(reason, error_reason)):
                why = "守門自己出錯" if decision == "deny" else "該擋沒擋"
                raise Failed(f"{label}（{rel}）{why}：寫小知識庫的 .system/sources.json", file, line)
            if expect == "read_in_vault" and decision == "deny":
                why = "守門自己出錯" if _is_guard_error(reason, error_reason) else "擋了不該擋的"
                raise Failed(f"{label}（{rel}）{why}：讀小知識庫裡的 {PLAIN_FILE}", file, line)
            if expect == "not_deny" and out.strip() and decision == "deny":
                raise Failed(f"{label}（{rel}）擋了不該擋的：讀知識庫外面的檔", file, line)
            new = _log_lines(vault)[logged:]
            if new:
                raise Failed(f"{label}（{rel}）寫了 {ERROR_LOG_REL}：{new[0]}", file, line)


def _step_upgrade(root, vault, seconds):
    argv = [sys.executable, str(root / "scripts" / "kb.py"), "upgrade", "--background"]
    try:
        code, _out, err = _run(argv, None, seconds, vault, _hook_env(root))
    except subprocess.TimeoutExpired:
        raise Failed(f"kb upgrade --background 逾時：超過 {seconds} 秒沒結束") from None
    if code != 0:
        file, line = where_in(err, root)
        raise Failed(f"kb upgrade --background 結束碼 {code}", file, line)


OBSIDIAN_FILES = ("manifest.json", "main.js", "styles.css")


def _step_obsidian(root):
    """Obsidian 首頁外掛（#22）：obsidian/ 底下三個檔都在、manifest.json 是合法 JSON 而且版號等於 plugin.json 的。不跑 node（客戶機沒有）。"""
    from kb._lib import paths
    for name in OBSIDIAN_FILES:
        if not (root / "obsidian" / name).is_file():
            raise Failed(f"obsidian/{name} 不在", f"obsidian/{name}")
    try:
        manifest = json.loads((root / "obsidian" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Failed("obsidian/manifest.json 不是合法的 JSON", "obsidian/manifest.json") from None
    if not isinstance(manifest, dict) or manifest.get("version") != paths.read_manifest().get("version"):
        raise Failed("obsidian/manifest.json 的版號跟 plugin.json 不一樣", "obsidian/manifest.json")


def _remove(base):
    """刪暫存小知識庫；Windows 上剛結束的子行程或防毒偶爾還握著檔，刪不掉就等一下再試幾次。"""
    for _ in range(10):
        shutil.rmtree(base, ignore_errors=True)
        if not os.path.exists(base):
            return
        time.sleep(0.2)


# ---------- 主體 ----------

def run():
    """跑全部步驟，回結果物件（不丟）。"""
    result = {"ok": False, "version": None, "step": None, "file": None, "line": None, "why": None}
    step = "import"
    base = None
    try:
        _step_import(ROOT_FROM_FILE)
        from kb._lib import config, paths
        root = paths.plugin_root()
        step = "version"
        result["version"] = _step_version()
        step = "vault"
        base = Path(os.path.realpath(tempfile.mkdtemp(prefix=TEMP_PREFIX)))
        vault = _step_vault(base)
        seconds = config.get(vault, "selftest_hook_seconds")
        step = "hooks"
        _step_hooks(root, vault, base / "外面.txt", seconds)
        step = "upgrade"
        _step_upgrade(root, vault, seconds)
        step = "obsidian"
        _step_obsidian(root)
        result["ok"] = True
    except Failed as e:
        result.update(step=step, file=e.file, line=e.line, why=clean_why(e.why))
    except BaseException as e:  # noqa: BLE001  自己出錯也回 ok false，不丟 traceback
        result.update(step=step, why=type(e).__name__)
    finally:
        if base is not None:
            _remove(base)
    if result["ok"]:
        result.update(step=None, file=None, line=None, why=None)
    return result
