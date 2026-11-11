"""升級（升級打包 要做的事 1，D-215～D-219、D-217）：kb upgrade 的本體，跟開場的背景查新版共用。

流程（D-215）：客戶說好 → kb upgrade：大升級先拍快照（backup.snapshot "upgrade"）、寫 upgrade-history.json 的 pending、
找桌面版內建的 Claude Code 跑 `plugin update wiki-with-willy@<市集> --scope <範圍>`（plugin 前面不加任何旗標，B-409 spike：--debug 會開出一個對話），
驗 installed_plugins.json 真的換了版（GitHub 快取還給舊清單時沒換到，說「新版剛出來」、pending 清掉、快照留著）→ 請她開新對話。
新對話開場（hooks/session.py）認出換了版 → kb upgrade --finish：補骨架、對帳、狀態檔遷移、卡檔頭、記歷史，幾秒內做完（D-054）。
小修（前兩位相同）：開場每天背景查一次（--background），有她可以拿的小修就在同一個子行程換版，不拍快照、不轉格式（D-217）。
退回（清單比現在舊）：先 --check 看升級前的快照與 restore check 的結果，她說好才 --downgrade：restore apply 還原快照、再換版。
名單（D-216）：rollout.json 的那一版 for 是 "all"、或含這個知識庫的 vault_id 才算她可以拿；其他當沒有新版。
抓不到清單、名單一律安靜（D-219）：只記進 env.json 的 update_check.error，不印、不出錯。
換版之前先驗（D-230，verify）：抓清單那一版的 zip（_download）、核 sha256、檢查 zip slip、用她的 Python 編譯每個 .py、跑新版自帶的
kb selftest；不過就不換。背景小修與對話裡的 kb upgrade 都驗（--downgrade 不驗）；背景驗不過記進 update_check.verify_failed，
同一版、同一個 Python 不再每天重抓。
外部的東西都經 _get（網路）、_download（下載 zip）、_call（claude.exe、新版的自我檢查）、_popen（背景子行程）、_which，
測試換替身，不連網、不跑真的 Claude。
鎖：跟備份、還原同一把（.system/revert.lock，backup.acquire）；背景整理還有動靜時擋（backup.batch_running）。
只呼叫 backup、restore、vault 的函式，不改它們。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from kb._lib import backup, config, env, migrate, paths, skeleton
from kb._lib import vault as vault_lib
from kb._lib.io import FileLock, read_json, write_json_atomic

HISTORY_REL = ".system/upgrade-history.json"
HISTORY_SCHEMA = 1
PLUGIN_NAME = "wiki-with-willy"
FETCH_MAX_BYTES = 1_000_000
NOTE_MAX_CHARS = 200  # 遠端那句話的硬上限（--check、--json、env.json 都用這份）；開場另截 update_note_max
KINDS = ("patch", "minor", "major", "downgrade")

UP_TO_DATE = "目前是最新版（{version}）"
NOT_YET = "新版剛出來，幾分鐘後再說一次"
NO_CLAUDE = "這台找不到 Claude 的更新程式：自己找 claude.exe（%LOCALAPPDATA%、Program Files），找到就照它的路徑再跑；找不到才請服務人員。"
UPDATE_FAILED = "更新沒成功：{why}"
HISTORY_BROKEN = "更新沒成功：升級紀錄（.system/upgrade-history.json）讀不懂，請服務人員看一下；什麼都沒動"
NO_SNAPSHOT = "升級前的快照已經不在，不能退回；請服務人員幫忙"
OFFLINE_ASK = "現在連不上更新的地方，晚點再問。"
OFFLINE_RETRY = "現在連不上更新的地方，晚點再說一次"
SUCCESS = "好了，開一個新對話就是新版（{version}）。"
DOWNGRADED = "已回到 {version} 的知識庫，也換回 {version}；開一個新對話就好。"
USE_DOWNGRADE = "這一版（{version}）撤回了；要退回先用 kb upgrade --check 看會怎樣，她說好再 kb upgrade --downgrade"
DOWNGRADE_HALF = "知識庫已經還原回去了，只有外掛還沒換回舊版；請服務人員幫忙，不要再退回一次"  # 還原成功、換版才失敗（B-438）
LATEST_LINE = "目前是最新版（{version}）。"
NEWER_LINE = "現在 {plugin}；最新 {latest}（{note}），說「升級」就幫你升。"
NEWER_LINE_NO_NOTE = "現在 {plugin}；最新 {latest}，說「升級」就幫你升。"
VAULT_BEHIND_LINE = "知識庫還停在 {vault}，新對話開場會自動補轉格式。"
WITHDRAWN_LINE = "這一版 {plugin} 撤回了，清單指回 {latest}；要退回會先還原升級前的快照（{snapshot}），再換回舊版。"
WITHDRAWN_NO_SNAPSHOT = "這一版 {plugin} 撤回了，清單指回 {latest}；但升級前的快照已經不在，不能退回，請服務人員幫忙。"

FINISH_LINE = "已換到 {version}：先跑 kb upgrade --finish 再做別的。"
NEW_VERSION_LINE = "有新版 {version}：{note}；客戶說「升級」再帶他做，這行只講一次。"
NEW_VERSION_LINE_NO_NOTE = "有新版 {version}；客戶說「升級」再帶他做，這行只講一次。"
WITHDRAWN_OPENING = "這一版 {version} 撤回了；用起來有問題的話說「退回舊版」。"
PENDING_OPENING = "上次升級到 {version} 還沒換好：客戶說「升級」就再試一次，這行只講一次。"
REUSE_SNAPSHOT_SECONDS = 24 * 3600  # 沿用上次沒換成的升級前快照，只限一天內拍的

DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024  # 一份 zip 的上限（現在一版不到 2 MB）
VERIFY_PREFIX = "wiki-with-willy-verify-"
NO_SOURCE = "清單沒有這一版的下載網址"
VERIFY_BG = "新版 {version} 在這台電腦跑不起來（{where}），沒有換"
VERIFY_REFUSED = "新版 {version} 在這台電腦跑不起來（{where}），先不換；什麼都沒動"
VERIFY_LINE = "新版 {version} 在這台電腦跑不起來（{where}），先沒換"
DOWNLOAD_LATER = "現在下載不到新版，晚點再說一次；什麼都沒動"  # 下載不到、逾時這類跟新版好壞無關的（裁定 12）
VERIFY_RETRY_BG = "新版 {version} 這次沒驗成（{where}），沒有換；下次照常再試"


class UpdateError(Exception):
    """換版這一步做不到：訊息是給人看的那句（一字不差）。"""


class Offline(Exception):
    """抓不到清單或名單（連不上、逾時、內容看不懂）：訊息是一句原因，只記不印（D-219）。"""


class NotConfigured(Offline):
    """清單網址還是空字串（第一次發行才填，D-171）：背景不查。"""


# ---------- 呼叫層（測試換掉） ----------

def _get(url, timeout):
    """抓一個 https 網址回文字（UTF-8）；連不上、逾時、不是 https 都丟 OSError／ValueError。"""
    if not str(url).startswith("https://"):
        raise ValueError("只收 https 網址")
    req = urllib.request.Request(url, headers={"User-Agent": "wiki-with-willy"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    with opener.open(req, timeout=timeout) as r:
        return r.read(FETCH_MAX_BYTES).decode("utf-8")


def _download(url, dest, timeout):
    """抓一個 https 網址的檔寫到 dest（超過 DOWNLOAD_MAX_BYTES 丟 ValueError）；連不上、不是 https 丟 OSError／ValueError。
    timeout 是總時間（裁定 15）：每讀一塊都看一次，超過丟 TimeoutError（伺服器慢慢吐也不會拖過它）。"""
    if not str(url).startswith("https://"):
        raise ValueError("只收 https 網址")
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={"User-Agent": "wiki-with-willy"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    got = 0
    with opener.open(req, timeout=timeout) as r, open(dest, "wb") as f:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("下載超過時間")
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > DOWNLOAD_MAX_BYTES:
                raise ValueError("檔案太大")
            f.write(chunk)


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """轉址也只跟著 https（清單被轉到 http 的地方就當連不上）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).startswith("https://"):
            raise urllib.error.URLError("只跟著 https 的轉址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _call(argv, timeout, env=None, cwd=None):
    """跑外部程式，回 (結束碼, 輸出文字)；stdout 與 stderr 合併、一律用 UTF-8 解。找不到程式丟 FileNotFoundError，
    逾時殺整棵行程樹再丟 subprocess.TimeoutExpired（照 _lib/office 的做法）。不在對話的目錄跑。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(env if env is not None else os.environ, NoDefaultCurrentDirectoryInExePath="1")  # 在專案夾跑時，子行程不先找那個夾
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=cwd or tempfile.gettempdir(),
                         creationflags=flags, stdin=subprocess.DEVNULL)
    try:
        out, _err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.communicate()
        raise
    return p.returncode, out.decode("utf-8", errors="replace")


def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        p.kill()


def _popen(argv, **kw):
    """開背景子行程（不等它）；測試換掉它記下參數。"""
    return subprocess.Popen(argv, **kw)


def _which(name):
    """PATH 上找程式，跳過目前目錄與相對路徑的 PATH 項。Python 3.12 以前 shutil.which 在 Windows 一定先找目前目錄
    （傳 path= 也一樣），而 kb upgrade 的目前目錄是知識庫：放一個 claude.cmd 就會被當更新程式跑。所以自己走 PATH。"""
    here = os.path.normcase(os.path.abspath(os.getcwd()))
    exts = [""] if os.path.splitext(name)[1] or os.name != "nt" else \
        [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not os.path.isabs(d) or os.path.normcase(os.path.abspath(d)) == here:
            continue
        for ext in exts:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def plugin_version():
    return str((paths.read_manifest() or {}).get("version", "0"))


def _plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root) if root else paths.plugin_root()


def installed_plugins_path():
    return Path(os.path.expanduser("~")) / ".claude" / "plugins" / "installed_plugins.json"


def _changelog():
    """這一版自己的 CHANGELOG.md（zip 裡有，D-213）；讀不到回空字串。"""
    try:
        return (paths.plugin_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------- 版本 ----------

_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)


def parse_version(text):
    """"0.20.1" → (0, 20, 1)；不是剛好三段 ASCII 整數（沒有前導零、結尾不帶換行）回 None。
    整串比對：$ 會放過結尾換行（會帶進開場那行），\\d 會放過全形與阿拉伯數字，前導零跟安裝紀錄的版本字串永遠對不上。"""
    m = _VERSION.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def kind(current, latest):
    """patch｜minor｜major｜downgrade｜none（D-217）：前兩位相同、第三位較大是小修；前兩位較大是大升級（第一位變是 major，
    0.x 時第二位當大版本所以是 minor）；清單比現在舊是退回；同版或看不懂回 none。"""
    cur, new = parse_version(current), parse_version(latest)
    if cur is None or new is None or cur == new:
        return "none"
    if new < cur:
        return "downgrade"
    if new[:2] == cur[:2]:
        return "patch"
    return "major" if new[0] > cur[0] else "minor"


def note_of(changelog_text, version):
    """CHANGELOG 裡 `## <版本> — …` 那一段的「給客戶的一句話」；沒有那一版、沒有那行回 None。"""
    head = re.search(rf"^## {re.escape(str(version))} — .*$", changelog_text or "", re.MULTILINE)
    if not head:
        return None
    rest = changelog_text[head.end():]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    section = rest[:nxt.start()] if nxt else rest
    m = re.search(r"^- \*\*給客戶的一句話\*\*：(.+)$", section, re.MULTILINE)
    return m.group(1).strip() if m else None


# ---------- 清單與名單 ----------

def allowed(roster, version, vault_id):
    """名單那一版的 for：「all」、或清單含這個 vault_id 才算她可以拿；其他（none、沒那一版、看不懂）都不算。"""
    versions = roster.get("versions") if isinstance(roster, dict) else None
    entry = versions.get(version) if isinstance(versions, dict) else None
    who = entry.get("for") if isinstance(entry, dict) else None
    if who == "all":
        return True
    return bool(vault_id) and isinstance(who, list) and vault_id in [w for w in who if isinstance(w, str)]


def _json_get(url, seconds):
    try:
        return json.loads(_get(url, seconds))
    except (OSError, ValueError) as e:  # TimeoutError 與 URLError 都是 OSError
        raise Offline(f"{url} 讀不到（{type(e).__name__}）") from None


def clean_note(text):
    """會進開場與對話的那句話：壓成一行、去掉控制與方向字元（isprintable 不收 Cc、Cf），截 NOTE_MAX_CHARS；不是字串或空的回 None。
    抓下來時與開場印之前都過一次（env.json 是本機檔，也可能被改過或從別台帶來）。"""
    if not isinstance(text, str):
        return None
    words = "".join(c if c.isprintable() else " " for c in text).split()
    return " ".join(words)[:NOTE_MAX_CHARS].strip() or None


def _source_of(entry):
    """清單那一筆的 source（tools/release.py 寫的 {"source": "archive", "url", "sha256"}）→ {"url", "sha256"}；不是 archive、缺欄回 None。"""
    src = entry.get("source") if isinstance(entry, dict) else None
    if not isinstance(src, dict) or src.get("source") != "archive":
        return None
    url, digest = src.get("url"), src.get("sha256")
    if not (isinstance(url, str) and url and isinstance(digest, str) and digest):
        return None
    return {"url": url, "sha256": digest}


def fetch(vault):
    """抓清單與名單。回 {"latest", "allowed", "note", "source"}；網址空丟 NotConfigured，抓不到或看不懂丟 Offline。"""
    manifest_url = config.get(vault, "update_manifest_url")
    rollout_url = config.get(vault, "update_rollout_url")
    if not manifest_url:
        raise NotConfigured("清單網址還沒設定")
    seconds = config.get(vault, "update_fetch_seconds")
    data = _json_get(manifest_url, seconds)
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entry = next((p for p in plugins if isinstance(p, dict) and p.get("name") == PLUGIN_NAME), None) \
        if isinstance(plugins, list) else None
    latest = entry.get("version") if entry else None
    if parse_version(latest) is None:
        raise Offline("清單裡找不到 wiki-with-willy 的版本")
    roster = _json_get(rollout_url, seconds) if rollout_url else {}
    try:
        vid = vault_lib.vault_id(vault)
    except ValueError:
        vid = None
    note = None
    versions = roster.get("versions") if isinstance(roster, dict) else None
    row = versions.get(latest) if isinstance(versions, dict) else None
    if isinstance(row, dict):
        note = clean_note(row.get("note"))
    return {"latest": latest, "allowed": allowed(roster, latest, vid), "note": note, "source": _source_of(entry)}


# ---------- 換版之前先驗（D-230） ----------

def python_version():
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


STEP_NAMES = {"download": "下載", "compile": "編譯", "selftest": "自我檢查", "import": "載入程式", "version": "版本號",
              "vault": "建暫存知識庫", "hooks": "跑 hook", "upgrade": "背景更新"}  # 給人看的句子用中文（裁定 4）；--json 的 step 照舊英文


def where(res):
    """「hooks/guard.py 第 12 行：SyntaxError」或「跑 hook 那步：原因」；原因是 why 冒號前的例外名稱（有的話）。"""
    why = str(res.get("why") or "")
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*): ", why)
    reason = m.group(1) if m else why
    if res.get("file") and res.get("line"):
        head = f"{res['file']} 第 {res['line']} 行"
    elif res.get("file"):
        head = str(res["file"])
    else:
        name = STEP_NAMES.get(res.get("step"), "自我檢查")
        head = f"{name}{' ' if name[-1].isascii() else ''}那步"  # 「下載那步」「跑 hook 那步」
    return f"{head}：{reason}" if reason else head


def _unsafe_member(name):
    return backup._unsafe_member(name)


def _compile_all(root):
    """解出來的每個 .py 用目前這個 Python 編譯（只在記憶體）；第一個錯回 (相對路徑, 行號, why)，都過回 None。"""
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        try:
            compile(p.read_bytes(), rel, "exec", dont_inherit=True)
        except SyntaxError as e:
            return rel, e.lineno, f"{type(e).__name__}: {e.msg}"
        except ValueError as e:  # 原始碼有 NUL 這類
            return rel, None, f"{type(e).__name__}: {e}"
    return None


def _selftest_output(text):
    """子行程輸出裡最後一行讀得懂的 JSON 物件；沒有回 None。"""
    for ln in reversed(str(text).splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                data = json.loads(ln)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return None


def verify(vault, latest, source):
    """換版之前在這台電腦驗新版（D-230）：抓 zip、核 sha256、zip slip、編譯、跑新版的 kb selftest。
    回 {"ok","version","python","step","file","line","why","transient"}；step 是 download｜compile｜selftest，或新版自我檢查回的那一步。
    transient（裁定 11）：跟新版好壞無關的失敗（下載不到、sha256 不對、逾時、起不來）是 true，隔天照常再試；新版本身的問題
    （編譯、新版的自我檢查回不過、沒有自我檢查、輸出看不懂、版本不對）是 false，記成 verify_failed。不丟。
    新版的自我檢查在 verify 自己的暫存夾裡跑（TMP／TEMP 也指過去），被殺掉留下的東西跟著一起刪（裁定 14）。"""
    res = {"ok": False, "version": latest, "python": python_version(), "step": None, "file": None, "line": None, "why": None,
           "transient": False}

    def fail(step, why, file=None, line=None, transient=False):
        res.update(step=step, why=why, file=file, line=line, transient=transient)
        return res
    if source is None:
        return fail("download", NO_SOURCE, transient=True)
    seconds = config.get(vault, "update_verify_seconds")
    deadline = time.monotonic() + seconds
    tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix=VERIFY_PREFIX)))
    try:
        zip_path = tmp / "new.zip"
        try:
            _download(source["url"], zip_path, seconds)
            data = zip_path.read_bytes()
        except (OSError, ValueError) as e:
            return fail("download", f"下載不到（{type(e).__name__}）", transient=True)
        if hashlib.sha256(data).hexdigest() != str(source["sha256"]).lower():
            return fail("download", "下載的檔跟清單的指紋對不上", transient=True)
        root = tmp / "plugin"
        try:
            with zipfile.ZipFile(str(zip_path)) as z:
                names = z.namelist()
                if any(_unsafe_member(n) for n in names):
                    return fail("download", "zip 裡有不安全的路徑", transient=True)
                z.extractall(str(root))
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError):
            return fail("download", "不是完整的 zip 檔", transient=True)
        bad = _compile_all(root)
        if bad:
            return fail("compile", bad[2], bad[0], bad[1])
        left = deadline - time.monotonic()
        if left <= 0:
            return fail("selftest", f"超過 {seconds} 秒", transient=True)
        child_tmp = tmp / "temp"
        child_tmp.mkdir()
        child_env = dict(os.environ, PYTHONIOENCODING="utf-8", TMP=str(child_tmp), TEMP=str(child_tmp), TMPDIR=str(child_tmp))
        argv = [sys.executable, str(root / "scripts" / "kb.py"), "selftest", "--json"]
        try:
            code, out = _call(argv, left, env=child_env, cwd=str(tmp))
        except subprocess.TimeoutExpired:
            return fail("selftest", f"自我檢查超過 {seconds} 秒", transient=True)
        except OSError as e:
            return fail("selftest", f"自我檢查起不來（{type(e).__name__}）", transient=True)
        got = _selftest_output(out)
        if got is None:
            return fail("selftest", "這一版沒有自我檢查" if code == 2 else f"自我檢查的輸出看不懂（結束碼 {code}）")
        if got.get("ok") is not True:
            step = got.get("step") if got.get("step") in ("import", "version", "vault", "hooks", "upgrade") else "selftest"
            file = _child_file(got.get("file"))
            line = got.get("line") if file and isinstance(got.get("line"), int) and not isinstance(got.get("line"), bool) else None
            return fail(step, _clean_why(got.get("why") or "自我檢查沒過"), file, line)
        if got.get("version") != latest:
            return fail("selftest", _clean_why(f"新版自己說是 {got.get('version')}，不是 {latest}"))
        res["ok"] = True
        return res
    except Exception as e:  # noqa: BLE001  驗不了也是不過，不丟；隔天再試
        return fail(res["step"] or "selftest", type(e).__name__, transient=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _child_file(file):
    """新版回的 file 只收 plugin 根底下的相對路徑（/ 分隔、沒有 ..、不是絕對、沒有磁碟代號、沒有反斜線）；其他回 None（裁定 17）。"""
    if not isinstance(file, str) or not file or "\\" in file or ":" in file or _unsafe_member(file):
        return None
    return file[:200]


def _clean_why(text):
    """新版回的 why 再清一次：絕對路徑拿掉、遮值、壓一行（裁定 17，跟 kb selftest 自己的 clean_why 同一支）。"""
    from kb._lib import selftest
    return selftest.clean_why(str(text))


# ---------- 歷史（.system/upgrade-history.json） ----------

def _history_path(vault):
    return Path(vault) / HISTORY_REL


def load_history(vault):
    """沒檔回 None；壞檔、形狀不對丟 ValueError（不覆寫，跟 vault.json 同一套）。"""
    try:
        data = read_json(_history_path(vault))
    except ValueError as e:
        raise ValueError(f"{HISTORY_REL}（JSON 壞了：{e}）") from e
    if data is None:
        return None
    if (not isinstance(data, dict) or not isinstance(data.get("current"), str)
            or not isinstance(data.get("history", []), list)):
        raise ValueError(f"{HISTORY_REL}（格式不對）")
    data.setdefault("pending", None)
    data.setdefault("history", [])
    return data


def write_history(vault, data):
    p = _history_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        write_json_atomic(p, dict(data, schema_version=HISTORY_SCHEMA))


def ensure_history(vault):
    """沒有就照現在跑的版本記成目前版本（第一次跑這版的既有知識庫，I-113）；有就讀。壞檔不覆寫。"""
    got = load_history(vault)
    if got is not None:
        return got
    p = _history_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        got = load_history(vault)
        if got is not None:
            return got
        got = {"schema_version": HISTORY_SCHEMA, "current": plugin_version(), "pending": None, "history": []}
        write_json_atomic(p, got)
    return got


def _snapshot_for_downgrade(vault, hist):
    """最近一筆升到現在這版的紀錄記的升級前快照（檔還在才算）；沒有回 None。"""
    plugin = plugin_version()
    for row in reversed((hist or {}).get("history") or []):
        if isinstance(row, dict) and row.get("to") == plugin and row.get("kind") != "downgrade":
            name = row.get("snapshot")
            if name and (Path(vault) / backup.SNAPSHOT_REL / name).is_file():
                return name
            return None
    return None


# ---------- 找內建 Claude Code、市集名與範圍 ----------

def find_claude():
    """桌面版內建的 claude.exe：%LOCALAPPDATA%\\Claude-Data\\claude_*\\claude-code\\<版本>\\<…>\\claude.exe，版本夾多個取版本號最大的
    （數字比大小，不是字串）；固定深度的萬用字元，不遞迴（spike：掃整個 Claude-Data 會很慢）；找不到用 PATH 上的 claude；都沒有回 None。"""
    base = os.environ.get("LOCALAPPDATA")
    found = []
    if base:
        found = list((Path(base) / "Claude-Data").glob("claude_*/claude-code/*/*/claude.exe"))

    def order(p):
        nums = re.findall(r"\d+", p.parent.parent.name)
        return (tuple(int(n) for n in nums), str(p))
    if found:
        return str(max(found, key=order))
    return _which("claude")


def _read_installed():
    try:
        data = json.loads(installed_plugins_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else None


def _same_path(a, b):
    def norm(p):
        return os.path.normcase(os.path.realpath(str(p)))
    return norm(a) == norm(b)


def plugin_identity(vault=None):
    """從 CLAUDE_PLUGIN_ROOT（…/cache/<市集>/wiki-with-willy/<版本>）取市集名，範圍從 installed_plugins.json 裡
    installPath 等於目前 plugin 根的那筆取（客戶是 user；確認時是 local，local 還帶 projectPath）。不寫死。
    同一個版本夾可能有好幾筆（local 裝在好幾個夾，桌面版確認 B-3）：先挑 projectPath 是這個知識庫的，再挑 user 的，最後才是第一筆。"""
    root = Path(_plugin_root())
    if root.parent.name != PLUGIN_NAME or root.parent.parent.parent.name != "cache":
        raise UpdateError(UPDATE_FAILED.format(why="這個外掛不是從市集裝的"))
    market = root.parent.parent.name
    plugins = _read_installed()
    if plugins is None:
        raise UpdateError(UPDATE_FAILED.format(why="讀不到外掛的安裝紀錄"))
    if not re.fullmatch(r"[A-Za-z0-9._-]+", market, re.ASCII):
        raise UpdateError(UPDATE_FAILED.format(why="市集名稱看不懂"))
    found = []
    for rec in plugins.get(f"{PLUGIN_NAME}@{market}") or []:
        if isinstance(rec, dict) and rec.get("installPath") and _same_path(rec["installPath"], root):
            scope = rec.get("scope") or "user"
            if scope not in ("user", "project", "local"):
                raise UpdateError(UPDATE_FAILED.format(why="安裝紀錄的範圍看不懂"))
            found.append({"market": market, "scope": scope, "project": rec.get("projectPath")})
    if not found:
        raise UpdateError(UPDATE_FAILED.format(why="安裝紀錄裡找不到這個外掛"))
    if vault is not None:
        for f in found:
            if f["project"] and _same_path(f["project"], vault):
                return f
    return next((f for f in found if f["scope"] == "user"), found[0])


def installed_version(identity):
    """重讀 installed_plugins.json 同一筆（市集、範圍、projectPath 相同）的 version；沒有回 None。"""
    plugins = _read_installed() or {}
    for rec in plugins.get(f"{PLUGIN_NAME}@{identity['market']}") or []:
        if (isinstance(rec, dict) and rec.get("scope") == identity["scope"]
                and rec.get("projectPath") == identity.get("project")):
            return rec.get("version")
    return None


def _first_line(text, limit=120):
    for ln in str(text).splitlines():
        if ln.strip():
            return ln.strip()[:limit]
    return "沒有說明"


def run_update(identity, seconds=60):
    """叫內建 Claude Code 跑 plugin update。指令列：[exe, "plugin", "update", "wiki-with-willy@<市集>", "--scope", "<範圍>"]，
    plugin 前面不加任何旗標。失敗丟 UpdateError（一句話）。"""
    exe = find_claude()
    if not exe:
        raise UpdateError(NO_CLAUDE)
    argv = [exe, "plugin", "update", f"{PLUGIN_NAME}@{identity['market']}", "--scope", identity["scope"]]
    project = identity.get("project")
    cwd = project if identity["scope"] in ("local", "project") and project and os.path.isdir(project) else None
    try:
        code, out = _call(argv, seconds, cwd=cwd)
    except FileNotFoundError:
        raise UpdateError(NO_CLAUDE) from None
    except subprocess.TimeoutExpired:
        raise UpdateError(UPDATE_FAILED.format(why=f"等太久了（超過 {seconds} 秒）")) from None
    except OSError as e:
        raise UpdateError(UPDATE_FAILED.format(why=type(e).__name__)) from None
    if code != 0:
        raise UpdateError(UPDATE_FAILED.format(why=_first_line(out)))


# ---------- 查（--check） ----------

def check(vault):
    """當場查一次（什麼都不寫）。回 spec 的 --check --json 那個物件；退回時多 restore（restore check 的結果）；
    verify_failed 是背景記的驗不過（只在那一版就是清單現在這一版時給）。"""
    return _check(vault)[0]


def _check(vault):
    """check 的本體，另回清單那一版的 source（kb upgrade 驗新版要用，--check 不印）。"""
    plugin = plugin_version()
    try:
        hist = load_history(vault)
    except ValueError:
        hist = None
    out = {"plugin": plugin, "vault": hist["current"] if hist else plugin, "latest": None, "kind": None, "allowed": False,
           "note": None, "snapshot_for_downgrade": None, "error": None, "verify_failed": None}
    try:
        got = fetch(vault)
    except Offline as e:
        out["error"] = str(e)
        return out, None
    out.update(latest=got["latest"], allowed=got["allowed"], note=got["note"], kind=kind(plugin, got["latest"]))
    failed = (env.update_check(vault) or {}).get("verify_failed")
    if (isinstance(failed, dict) and failed.get("version") == got["latest"] and out["allowed"]
            and out["kind"] in ("patch", "minor", "major")):  # 已裝的不比它舊、或說最新版時不給（裁定 13）
        out["verify_failed"] = failed
    if out["kind"] == "downgrade" and out["allowed"]:
        name = _snapshot_for_downgrade(vault, hist)
        out["snapshot_for_downgrade"] = name
        if name:
            from kb._lib import restore
            try:
                out["restore"] = restore.check(vault, name)
            except (backup.Refused, OSError, ValueError):
                pass
    return out, got["source"]


def check_lines(res):
    """--check 的人讀輸出；--json 才有的欄位（restore）AI 自己讀。"""
    if res["error"]:
        return [OFFLINE_ASK]
    lines = []
    if res["kind"] == "downgrade" and res["allowed"]:
        if res["snapshot_for_downgrade"]:
            lines.append(WITHDRAWN_LINE.format(plugin=res["plugin"], latest=res["latest"], snapshot=res["snapshot_for_downgrade"]))
            if res.get("restore"):
                from kb._lib import restore
                lines += restore.lines(res["restore"])
        else:
            lines.append(WITHDRAWN_NO_SNAPSHOT.format(plugin=res["plugin"], latest=res["latest"]))
    elif not res["allowed"] or res["kind"] in (None, "none", "downgrade"):
        lines.append(LATEST_LINE.format(version=res["plugin"]))
    elif res["note"]:
        lines.append(NEWER_LINE.format(plugin=res["plugin"], latest=res["latest"], note=res["note"]))
    else:
        lines.append(NEWER_LINE_NO_NOTE.format(plugin=res["plugin"], latest=res["latest"]))
    if res.get("verify_failed"):
        lines.append(VERIFY_LINE.format(version=res["latest"], where=where(res["verify_failed"])))
    if res["vault"] != res["plugin"]:
        lines.append(VAULT_BEHIND_LINE.format(vault=res["vault"]))
    return lines


# ---------- 升級與退回 ----------

def _guard_ready(vault, what):
    """動手之前擋掉：背景整理還有動靜、找不到更新程式、不知道這個外掛怎麼裝的。回 identity。"""
    if backup.batch_running(vault):
        raise backup.Refused(backup.BATCH_RUNNING.format(what=what))
    if not find_claude():
        raise backup.Refused(NO_CLAUDE)
    try:
        return plugin_identity(vault)
    except UpdateError as e:
        raise backup.Refused(str(e)) from None


def _check_for_action(vault):
    """kb upgrade／--downgrade 開頭那次 --check；連不上丟那句。回 (check 的結果, 清單那一版的 source)。"""
    info, source = _check(vault)
    if info["error"]:
        raise backup.Refused(OFFLINE_RETRY)
    return info, source


def _clear_verify_failed(vault, installed):
    """update_check 記的驗不過，那一版不比 installed 新就清掉（換成功、已經裝上那一版或更新的，裁定 13）；其他欄留著。寫不了不擋。"""
    try:
        chk = env.update_check(vault)
        failed = (chk or {}).get("verify_failed")
        if isinstance(failed, dict) and kind(installed, failed.get("version")) not in ("patch", "minor", "major"):
            env.set_update_check(vault, dict(chk, verify_failed=None))
    except (OSError, ValueError):
        pass


def _clear_pending(vault):
    try:
        hist = load_history(vault)
        if hist is not None and hist.get("pending") is not None:
            hist["pending"] = None
            write_history(vault, hist)
    except (ValueError, OSError):
        pass


def _room_for_snapshot(vault):
    """拍升級前快照之前比空間（跟備份、還原同一套算法：未壓縮總大小對磁碟剩餘）；不夠丟 backup.Refused，什麼都還沒動。"""
    need = backup.collect(vault)[2]
    free = backup._free_bytes(vault)
    if need > free:
        raise backup.Refused(UPDATE_FAILED.format(why=backup.NO_SPACE.format(need=backup._mb_up(need), free=free // (1024 * 1024))))


def upgrade(vault):
    """kb upgrade：先在這台電腦驗新版（D-230，不管 update_check 記過什麼每次都驗），大升級再拍快照、記 pending、換版；小修直接換。
    回 {"sentence","kind","to","snapshot"}；不行丟 backup.Refused（那一句）。"""
    info, source = _check_for_action(vault)
    if info["kind"] == "downgrade" and info["allowed"]:
        raise backup.Refused(USE_DOWNGRADE.format(version=info["plugin"]))
    if not info["allowed"] or info["kind"] in (None, "none", "downgrade"):
        raise backup.Refused(UP_TO_DATE.format(version=info["plugin"]))
    k, latest = info["kind"], info["latest"]
    identity = _guard_ready(vault, "升級")
    seconds = config.get(vault, "update_run_seconds")
    snapshot = None
    with backup.acquire(vault):
        checked = verify(vault, latest, source)
        if not checked["ok"]:
            if checked.get("transient"):
                raise backup.Refused(DOWNLOAD_LATER)
            raise backup.Refused(VERIFY_REFUSED.format(version=latest, where=where(checked)))
        if k in ("minor", "major"):
            try:
                hist = ensure_history(vault)
            except (OSError, ValueError):
                raise backup.Refused(HISTORY_BROKEN) from None
            prev = hist.get("pending") if isinstance(hist.get("pending"), dict) else {}
            kept = _snapshot_file(vault, prev.get("snapshot")) if prev.get("to") == latest else None
            if kept is not None and time.time() - kept.stat().st_mtime < REUSE_SNAPSHOT_SECONDS:
                snapshot = kept  # 上次升級到同一版、快照還在而且是一天內拍的：不再拍一份（確認 B-3）
            else:
                kept = None
                _room_for_snapshot(vault)
        try:
            if k in ("minor", "major"):
                snapshot = snapshot or backup.snapshot(vault, "upgrade")
                started = prev.get("started") if kept is not None else None
                hist["pending"] = {"to": latest, "snapshot": snapshot.name,
                                   "started": started or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
                write_history(vault, hist)
        except backup.Refused:
            raise
        except (OSError, ValueError) as e:
            why = (backup.oserror_sentence(e) if isinstance(e, OSError) else None) or type(e).__name__
            raise backup.Refused(UPDATE_FAILED.format(why=f"快照沒拍成：{why}")) from None
        try:
            run_update(identity, seconds)
            got = installed_version(identity)
        except UpdateError as e:
            _clear_pending(vault)
            raise backup.Refused(str(e)) from None
        if got != latest:  # GitHub 快取還給舊清單：版本沒換到；快照留著
            _clear_pending(vault)
            raise backup.Refused(NOT_YET)
    _clear_verify_failed(vault, latest)
    return {"sentence": SUCCESS.format(version=latest), "kind": k, "to": latest,
            "snapshot": snapshot.name if snapshot else None}


def downgrade(vault, conversation=False):
    """kb upgrade --downgrade：restore apply 還原升級前的快照 → 換版（降版）。回 {"sentence","to","snapshot"}。
    快照不在、背景整理在跑、鎖被占、不是退回的狀態各丟那一句，什麼都沒動。要回去的是她之前跑過的版本，不驗（D-230）。"""
    info, _source = _check_for_action(vault)
    if not (info["kind"] == "downgrade" and info["allowed"]):
        raise backup.Refused(UP_TO_DATE.format(version=info["plugin"]))
    name = info["snapshot_for_downgrade"]
    if not name:
        raise backup.Refused(NO_SNAPSHOT)
    identity = _guard_ready(vault, "升級")
    latest = info["latest"]
    from kb._lib import restore
    restore.apply(vault, name, conversation)  # 自己搶鎖；鎖被占丟 Refused（BUSY）
    try:
        run_update(identity, config.get(vault, "update_run_seconds"))
        got = installed_version(identity)
    except UpdateError as e:
        raise backup.Refused(f"{e}\n{DOWNGRADE_HALF}") from None
    if got != latest:
        raise backup.Refused(f"{NOT_YET}\n{DOWNGRADE_HALF}")
    hist = ensure_history(vault)
    hist["current"], hist["pending"] = latest, None
    hist["history"].append({"from": info["plugin"], "to": latest, "date": _today(), "kind": "downgrade",
                            "snapshot": name, "by": "customer"})
    write_history(vault, hist)
    return {"sentence": DOWNGRADED.format(version=latest), "to": latest, "snapshot": name}


# ---------- 新對話開場的轉格式（--finish） ----------

def _run_init(vault):
    """補骨架（kb init 同一支，只補缺的）。"""
    import argparse
    import contextlib
    import io
    from kb import init
    with contextlib.redirect_stdout(io.StringIO()):
        init.run(argparse.Namespace(path=str(vault), check=False, json=False))


_INDEX_TARGET = re.compile(r"\]\(([^)]*)")


def _index_stale(vault):
    """總目錄.md 要不要重寫（#66 甲裁定 2）：不在、壞了，或有連結目標不是 ./ 開頭（舊寫法）。"""
    from kb._lib import cards
    if cards.index_broken(vault):
        return True
    text = (Path(vault) / cards.INDEX_POSIX).read_text(encoding="utf-8")
    return any(not m.startswith("./") for m in _INDEX_TARGET.findall(text))


def _relink(vault, turn_id):
    """程式頁重產一次（D-541）：日程頁、總目錄、綜合卡「相關檔」的連結換成這一版的寫法。日程與綜合卡內容沒變不寫；
    總目錄每次叫都會重寫，所以只在需要時叫（_index_stale）。寫前都備進回合。哪一支出錯記 hook-errors.log 後繼續，不擋升級。"""
    from kb._lib import agenda, cards, ledger, synthesis
    for what, run in (("日程.md", lambda: agenda.render(vault, turn_id)),
                      ("總目錄.md", lambda: _index_stale(vault) and cards.render_index(vault, turn_id)),
                      ("綜合卡的相關檔", lambda: synthesis.render_cache(vault, None, turn_id))):
        try:
            run()
        except Exception as e:  # noqa: BLE001
            ledger.log_error(vault, f"升級後重寫{what}的連結沒成：{type(e).__name__}: {e}", source="upgrade")


def _snapshot_file(vault, name):
    """pending 記的快照名 → 快照夾裡那個 .zip（只收單純的檔名，../ 這類一律不認）；不在回 None。"""
    if not isinstance(name, str) or not name.endswith(".zip") or Path(name).name != name or name in (".", ".."):
        return None
    p = Path(vault) / backup.SNAPSHOT_REL / name
    return p if p.is_file() else None


def _pending_snapshot(vault, hist, plugin):
    """對得上這次換版（pending.to 等於現在版本）而且快照檔還在的 pending；回快照名，否則 None。"""
    pending = hist.get("pending")
    if isinstance(pending, dict) and pending.get("to") == plugin and _snapshot_file(vault, pending.get("snapshot")):
        return pending["snapshot"]
    return None


UPGRADE_LINE = "第一層的「_個人」現在跟其他夾一樣：你說整理我才整理；裡面有不想讓我讀的，跟我說一句設成不讀。"
NOTE_BOTH = "系統/隨手記.md 已經有了，_個人/隨手記.md 沒有搬過去；首頁便條現在用 系統/隨手記.md"
NOTE_NOT_MOVED = "_個人/隨手記.md 沒搬到 系統/（{why}）；首頁便條現在用 系統/隨手記.md，要留舊的請自己搬過去"
LINKED = "它或 系統/ 是連結"
OLD_AREA_UNTIL = "0.33.0"  # 這一版（含）以前第一層的 _個人 是彈性區（#51 撤回，D-532）


def _old_area(vault, cur):
    """D-532：從 OLD_AREA_UNTIL（含）以前升上來、第一層有 _個人 時收尾，回 notes：隨手記.md 搬到 系統/隨手記.md
    （那裡已有就兩份都不動、回 NOTE_BOTH；它或 系統/ 是連結、搬不成都回 NOTE_NOT_MOVED）；還有別的東西回 UPGRADE_LINE，只剩 .gitkeep 就刪它、拿掉夾。
    她放的檔不動；升級前的版本看不懂、是連結、是型別夾（舊庫的 dir 叫它）都不動（寧可少做）。"""
    from kb._lib import inventory, profile
    was = parse_version(cur)
    root = Path(vault) / "_個人"
    if was is None or was > parse_version(OLD_AREA_UNTIL) or not root.is_dir() or inventory._is_link(root):
        return []
    try:
        if profile.type_of_folder(vault, "_個人") is not None:
            return []
    except (ValueError, OSError):
        return []
    notes = []
    note, dest = root / "隨手記.md", Path(vault) / "系統" / "隨手記.md"
    if note.is_file() or inventory._is_link(note):
        if inventory._is_link(note) or inventory._is_link(dest.parent):
            notes.append(NOTE_NOT_MOVED.format(why=LINKED))  # 連結不跟：搬過去可能落在知識庫外（security-review）
        elif dest.exists():
            notes.append(NOTE_BOTH)
        else:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(note, dest)
            except OSError as e:
                notes.append(NOTE_NOT_MOVED.format(why=backup.oserror_sentence(e) or type(e).__name__))
    try:
        rest = [p for p in root.iterdir() if p.name != ".gitkeep"]
        if not rest:
            (root / ".gitkeep").unlink(missing_ok=True)
            root.rmdir()
    except OSError:
        rest = True
    if rest:
        notes.append(UPGRADE_LINE)
    return notes


def finish(vault, turn_id):
    """kb upgrade --finish。回 {"already": True, "to": X}（一樣版本、第一次記錄）或
    {"already": False, "from","to","kind","snapshot","migrated","migrate_skipped","migrate_notes","cards_migrated",
    "skeleton_created","skeleton_replaced","skeleton_ask","note","by"}。鎖被占丟 backup.Refused，什麼都沒動。
    已裝的版本不比 update_check 記的驗不過那一版舊時，那筆清掉（裁定 13：例如她在外面換上了那一版）。"""
    plugin = plugin_version()
    _clear_verify_failed(vault, plugin)
    hist = load_history(vault)
    if hist is None:
        ensure_history(vault)
        return {"already": True, "to": plugin}
    cur = hist["current"]
    if cur == plugin:
        return {"already": True, "to": plugin}
    k = kind(cur, plugin)
    k = "major" if k == "none" else k  # 紀錄的版本看不懂：照大升級（先拍快照）處理
    with backup.acquire(vault):
        result = {"already": False, "from": cur, "to": plugin, "kind": k, "snapshot": None, "migrated": [],
                  "migrate_skipped": [], "migrate_notes": [], "cards_migrated": 0, "skeleton_created": [], "skeleton_replaced": [],
                  "skeleton_ask": [], "left_in_output": []}
        snapshot = _pending_snapshot(vault, hist, plugin)
        by = "customer" if snapshot else "outside"
        if k in ("minor", "major") and snapshot is None:
            _room_for_snapshot(vault)
            try:
                snapshot = backup.snapshot(vault, "upgrade").name
            except (OSError, ValueError) as e:
                why = (backup.oserror_sentence(e) if isinstance(e, OSError) else None) or type(e).__name__
                raise backup.Refused(UPDATE_FAILED.format(why=f"快照沒拍成：{why}")) from None
        if k == "patch":
            snapshot = None
            chk = env.update_check(vault) or {}
            if chk.get("auto_patched") == plugin:
                by = "auto"
        if k != "downgrade":
            mig = migrate.apply(vault, turn_id)  # 先於 kb init：init 自己也會升設定檔 schema，但不備進回合（驗收 8 要退得回）
            had = {rel for rel in skeleton.files() if (Path(vault) / rel).exists()}
            _run_init(vault)
            _relink(vault, turn_id)
            sk = skeleton.reconcile(vault, turn_id, from_version=cur)
            old_area = _old_area(vault, cur)  # init 之後：系統/ 已在
            created = sorted({rel for rel in skeleton.files() if rel not in had and (Path(vault) / rel).exists()}
                             | set(sk["created"]))  # 缺的骨架檔多半是 init 補的，reconcile 看到時已經在了
            result.update(migrated=mig["migrated"], migrate_skipped=mig["skipped"], cards_migrated=mig["cards"],
                          left_in_output=mig.get("left_in_output") or [],
                          migrate_notes=(mig.get("notes") or []) + old_area,
                          skeleton_created=created, skeleton_replaced=sk["replaced"], skeleton_ask=sk["ask"])
        result["snapshot"] = snapshot if k != "patch" else None
        hist["current"], hist["pending"] = plugin, None
        hist["history"].append({"from": cur, "to": plugin, "date": _today(), "kind": k,
                                "snapshot": result["snapshot"], "by": by})
        write_history(vault, hist)
    result["by"] = by
    result["note"] = note_of(_changelog(), plugin)
    return result


# ---------- 背景查新版與小修（開場叫的子行程） ----------

BLANK_CHECK = {"latest": None, "kind": None, "allowed": False, "note": None, "auto_patched": None, "error": None,
               "verify_failed": None}


def _save_check(vault, **fields):
    """整份換掉：沒給的欄回到 BLANK_CHECK（verify_failed 也是：換成功、清單換了新版本、不再是小修都清掉）。"""
    old = env.update_check(vault) or {}
    if old.get("told") and "told" not in fields:
        fields["told"] = old["told"]  # 開場講過的記號不跟著整份換掉
    fields.setdefault("checked_with", plugin_version())  # 哪一版查的；開場判撤回只信現在這版查的（裁定 27）
    env.set_update_check(vault, dict(BLANK_CHECK, date=_today(), **fields))


def _save_error(vault, e):
    """連不上：只記錯誤與日期，上一次查到的結果留著（開場只用上一次的；驗不過的紀錄也留著）。"""
    prev = env.update_check(vault) or {}
    keep = {k: prev.get(k) for k in ("latest", "kind", "allowed", "note", "verify_failed")}
    _save_check(vault, error=str(e) or type(e).__name__, checked_with=prev.get("checked_with"),  # 沒有也照傳 None（裁定 27）
                **{k: v for k, v in keep.items() if v is not None})


def background(vault):
    """kb upgrade --background：查清單與名單、結果寫 env.json 的 update_check；有她可以拿的小修就接著換版（記 auto_patched）。
    清單網址空就不查（什麼都不寫）；任何出錯只記進 error、不丟（D-219）。"""
    try:
        got = fetch(vault)
    except NotConfigured:
        return
    except Offline as e:
        _save_error(vault, e)
        return
    except Exception as e:  # noqa: BLE001  背景程式不能出聲
        _save_error(vault, e)
        return
    try:
        plugin = plugin_version()
        k = kind(plugin, got["latest"])
        fields = {"latest": got["latest"], "kind": k, "allowed": got["allowed"], "note": got["note"]}
        if k == "patch" and got["allowed"]:
            fields.update(_auto_patch(vault, got["latest"], got["source"]))
        _save_check(vault, **fields)
    except Exception as e:  # noqa: BLE001
        _save_error(vault, e)


def _auto_patch(vault, latest, source):
    """換小修：先驗新版（D-230），驗不過不換：新版本身的問題回 {"verify_failed", "error"}，下載不到、逾時這類只回 {"error"}
    （裁定 11，下次照常再試）；update_check 已經記著同一版、同一個 Python 驗不過的，不再抓、不再驗，直接沿用。背景整理有動靜或鎖被占就這次不換（明天再說）；成功回 {"auto_patched": latest}，失敗回 {"error": 句子}。"""
    failed = (env.update_check(vault) or {}).get("verify_failed")
    if isinstance(failed, dict) and failed.get("version") == latest and failed.get("python") == python_version():
        return {"verify_failed": failed, "error": VERIFY_BG.format(version=latest, where=where(failed))}
    if backup.batch_running(vault):
        return {}
    try:
        with backup.acquire(vault):
            identity = plugin_identity(vault)
            checked = verify(vault, latest, source)
            if not checked["ok"]:
                if checked.get("transient"):  # 下載不到、逾時這類：只記 error，下次照常再試（裁定 11）
                    return {"error": VERIFY_RETRY_BG.format(version=latest, where=where(checked))}
                failed = {k: v for k, v in checked.items() if k != "transient"}
                return {"verify_failed": failed, "error": VERIFY_BG.format(version=latest, where=where(failed))}
            run_update(identity, config.get(vault, "update_run_seconds"))
            if installed_version(identity) != latest:
                return {"error": NOT_YET}
    except backup.Refused:
        return {}
    except UpdateError as e:
        return {"error": str(e)}
    return {"auto_patched": latest}


# ---------- 開場（hooks/session.py 叫） ----------

def _spawn_background(vault):
    """背景開一個 kb upgrade --background（不等它、不拖慢開場）；Windows 脫離開場的行程與視窗。"""
    argv = [sys.executable, str(paths.plugin_root() / "scripts" / "kb.py"), "upgrade", "--background"]
    kw = {"cwd": str(vault), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    _popen(argv, **kw)


def _maybe_spawn(vault, chk):
    """清單網址有填、日期不是今天才開；先記今天（另一個對話開場不重複叫），上一次的結果留著。出錯吞掉。
    環境變數 WIKI_WITH_WILLY_NO_BACKGROUND 有值時不開（測試：網址填了以後黑箱跑開場不連網，跟背景健檢同一個開關）。"""
    if os.environ.get("WIKI_WITH_WILLY_NO_BACKGROUND"):
        return
    if not config.get(vault, "update_manifest_url") or (chk or {}).get("date") == _today():
        return
    try:
        env.set_update_check(vault, dict(chk or BLANK_CHECK, date=_today()))
        _spawn_background(vault)
    except Exception:  # noqa: BLE001  起不來不影響開場
        pass


def _mark_told(vault, chk, value):
    """開場那句講過了：在這台的 update_check 記 told（版本字串；撤回記 withdrawn:<這台的版本>），回新的 chk。"""
    chk = dict(chk or BLANK_CHECK, told=value)
    env.set_update_check(vault, chk)
    return chk


def opening(vault):
    """開場判斷，不連網、不等背景。回 {"finish": 要不要 --finish, "line": 開場那句或 None}。
    沒有 upgrade-history.json 就記成現在版本、不印換版那行（第一次跑這版的既有知識庫）；壞檔丟 ValueError（那行不印、寫 log）。
    優先序：換了版要 --finish > 升級記了卻還沒換成 > 有她可以拿的大升級 > 清單被指回舊版（撤回）。"""
    plugin = plugin_version()
    hist = ensure_history(vault)
    finish_needed = hist["current"] != plugin
    chk = env.update_check(vault)
    line = None
    pending = hist.get("pending") if isinstance(hist.get("pending"), dict) else {}
    pending_to = pending.get("to") if isinstance(pending.get("to"), str) and parse_version(pending.get("to")) else None
    if pending_to and kind(plugin, pending_to) not in ("minor", "major"):
        pending_to = None  # 只有比現在新的大升級才講「還沒換好」（本機檔被改過也不印怪字）
    if finish_needed:
        line = FINISH_LINE.format(version=plugin)
    elif pending_to and pending_to != plugin:
        if (chk or {}).get("told") != pending_to:  # 放寬乙 D-322 第 5 件：同一個版本只講一次
            line, chk = PENDING_OPENING.format(version=pending_to), _mark_told(vault, chk, pending_to)
    elif chk and chk.get("allowed") and isinstance(chk.get("latest"), str):
        k = kind(plugin, chk["latest"])
        if k in ("minor", "major"):
            note = clean_note(chk.get("note"))
            note = note[:config.get(vault, "update_note_max")].strip().rstrip("。.！!") or None if note else None
            if chk.get("told") != chk["latest"]:
                line = (NEW_VERSION_LINE.format(version=chk["latest"], note=note) if note
                        else NEW_VERSION_LINE_NO_NOTE.format(version=chk["latest"]))
                chk = _mark_told(vault, chk, chk["latest"])
        elif k == "downgrade" and chk.get("checked_with") == plugin and chk.get("told") != f"withdrawn:{plugin}":
            line = WITHDRAWN_OPENING.format(version=plugin)
            chk = _mark_told(vault, chk, f"withdrawn:{plugin}")
    _maybe_spawn(vault, chk)
    return {"finish": finish_needed, "line": line}
