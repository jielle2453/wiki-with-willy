"""Codex 轉接（codex 分支，spec 2026-11-07-Codex版）：把 Codex 的 hook payload 轉成守門與寫後登記認得的格式，scripts/kb/ 不改。

認法：PreToolUse／PostToolUse／UserPromptSubmit 的 payload 有 turn_id（Codex 的 schema 必填，Claude Code 沒有）。
normalize：apply_patch 拆成逐檔的 Write（新增）／Edit（更新、刪除、搬出、搬入）；Windows 上的 Bash 改叫 PowerShell
（Codex 在 Windows 跑的是 PowerShell）；view_image 改叫 Read；拆不出檔名的 patch 回 None（守門在知識庫裡 deny）。
密碼動作（D-212）：Codex 的 PreToolUse 不支援 ask，改成 deny 並在 session 檔記 codex_consent_pending；客戶下一句是同意
（is_agreement，只看 UserPromptSubmit 的 prompt，AI 寫的字到不了這裡），turn.py 記 codex_consent，同一個指令再跑一次才放行，用一次就清掉。
"""
import hashlib
import os
import re
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

WINDOWS = os.name == "nt"
PATCH_RE = re.compile(r"^[ \t]*\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
PATCH_TOOL = "apply_patch"
CONSENT_REASON = ("這個密碼動作要客戶同意：先用一句話跟客戶說要做什麼、問她可不可以；"
                  "她回同意之後，再跑一次一模一樣的指令。")
PATCH_REASON = "看不出這次改的是哪些檔，這次沒放行：改用一個檔一個 patch 再試一次。"
PENDING, GRANTED = "codex_consent_pending", "codex_consent"

AGREE = ("我同意", "沒問題", "同意", "繼續", "可以", "確定", "允許", "去做", "做吧", "okay", "ok", "yes", "好", "是", "對", "行", "嗯")  # 長的在前
FILLERS = ("的", "啊", "呀", "喔", "哦", "囉", "了", "吧", "唷", "耶", "嘛", "請", "就", "please")
SHELLS = ("powershell", "pwsh", "cmd", "bash", "sh", "zsh")
SHELL_FLAGS = ("-command", "-c", "/c", "-lc", "-ic")
PS_VALUE_OPTS = ("-executionpolicy", "-windowstyle", "-outputformat", "-inputformat", "-workingdirectory",
                 "-configurationname", "-version", "-psconsolefile")  # PowerShell 帶值的選項：值不算第一個非旗標（可縮寫）
PS_VALUE_ALIASES = ("-ep", "-ex", "-w", "-wd", "-o", "-of", "-if", "-v")
SH_VALUE_OPTS = ("-o", "+o", "-O", "+O", "--rcfile", "--init-file")  # bash 類帶值的旗標
CD_RE = re.compile(r"""(?:^|[;&|\n(]|&&)\s*(?:cd|Set-Location|sl|pushd|chdir)\s+("[^"]*"|'[^']*'|[^\s;&|)]+)""", re.I)


def is_codex(payload):
    return isinstance(payload, dict) and "turn_id" in payload


def _command_text(command):
    """Codex 的指令可能是陣列：第一個是殼（["powershell.exe", "-Command", "…"]、["bash", "-lc", "…"]）就取殼旗標後面那段
    （好幾段用空白接，同 PowerShell）；不是殼照 Windows 的規則加引號接成一行（review 2：不在陣列任意位置找 -c）。"""
    if isinstance(command, list):
        import subprocess

        parts = [str(x) for x in command]
        shell = os.path.splitext(os.path.basename(parts[0]))[0].lower() if parts else ""
        if shell in SHELLS:
            i = 1
            while i < len(parts) - 1:
                low = parts[i].lower()
                if low in SHELL_FLAGS:
                    # PowerShell、cmd 把後面幾段接成指令；bash -c 只有下一段是指令，再後面是 $0、$1
                    return " ".join(parts[i + 1:]) if shell in ("powershell", "pwsh", "cmd") else parts[i + 1]
                if not low.startswith(("-", "/")):
                    break  # 第一個非旗標是腳本（bash evil.sh -c …）：後面的 -c 是腳本的參數，不是殼的（review 三輪 4）
                i += 2 if _takes_value(shell, parts[i]) else 1  # -ExecutionPolicy Bypass 的值跳過（review 二輪 2、四輪 2）
        return subprocess.list2cmdline(parts)
    return command if isinstance(command, str) else ""


def _takes_value(shell, flag):
    low = flag.lower()
    if shell in ("powershell", "pwsh"):
        return low in PS_VALUE_ALIASES or (len(low) >= 3 and any(o.startswith(low) for o in PS_VALUE_OPTS))
    return flag in SH_VALUE_OPTS


def patch_text(tool_input):
    """apply_patch 的整段 patch：Codex 放 command（第三方也看過 patch、input、陣列），都收。"""
    for key in ("command", "patch", "input"):
        v = tool_input.get(key)
        if isinstance(v, list):
            v = next((x for x in reversed(v) if isinstance(x, str) and "*** Begin Patch" in x), None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def patch_targets(text, cwd):
    """[(工具名, 絕對路徑)]：Add File→Write，Update／Delete File→Edit，Move to→Write（搬入的新位置）。拆不出回 []。"""
    out = []
    for kind, path in PATCH_RE.findall(text or ""):
        path = path.strip().strip('"')
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        out.append(("Write" if kind in ("Add File", "Move to") else "Edit", os.path.normpath(path)))
    return out


def normalize(payload):
    """Codex payload → [現有格式的 payload]；apply_patch 拆不出檔回 None。其他工具原樣一份。"""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    ti = ti if isinstance(ti, dict) else {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool == PATCH_TOOL:
        targets = patch_targets(patch_text(ti), cwd)
        if not targets:
            return None
        return [dict(payload, tool_name=name, tool_input={"file_path": path}) for name, path in targets]
    if tool == "Bash":
        command = _command_text(ti.get("command"))
        name = "PowerShell" if WINDOWS else "Bash"
        items = [dict(payload, tool_name=name, tool_input=dict(ti, command=command))]
        if "*** Begin Patch" in command:
            # 從殼叫 apply_patch：整條照殼指令判，另外 patch 裡的每個檔也照 Write／Edit 判（只會多擋，不會少判；review 7、三輪 1～3）
            # 相對路徑照 cwd 判，指令裡 cd 到的夾也各判一次（review 四輪 1：cd <知識庫> && apply_patch 不能因為 cwd 在外面就少判）
            bases = [cwd] + [os.path.join(cwd, d.strip("\"'")) for d in CD_RE.findall(command)]
            seen = set()
            for base in bases:
                for n, p in patch_targets(command, base):
                    if (n, p) not in seen:
                        seen.add((n, p))
                        items.append(dict(payload, tool_name=n, tool_input={"file_path": p}))
        return items
    if tool == "view_image":
        return [dict(payload, tool_name="Read", tool_input={"file_path": str(ti.get("path") or ti.get("file_path") or "")})]
    return [payload]


def combine(results):
    """逐檔的 (decision, reason, updated) 併成一個：有 deny 就 deny；只有一份照它；全部 allow 才 allow；其他不表態。"""
    for r in results:
        if r[0] == "deny":
            return "deny", r[1], None
    for r in results:
        if r[0] == "ask":  # 密碼動作夾在多份裡也要走同意（不能因為合併成不表態就放行）
            return r
    if len(results) == 1:
        return results[0]
    if results and all(r[0] == "allow" for r in results):
        return "allow", results[0][1], None
    return None, "", None


def is_agreement(prompt):
    """客戶這一句是不是單純的同意：去掉空白與標點後，整句只由同意的字（AGREE）與語助詞（FILLERS）組成，至少一個同意字
    （review 3：「這是什麼」「是誰」含「是」但有別的字，不算；「不要」「等一下」「可以嗎」也因此不算）。"""
    t = re.sub(r"[\s，。！!、,.~～…」「]", "", str(prompt or "")).casefold()
    rest = t
    for a in AGREE:
        rest = rest.replace(a, "")
    if rest == t:
        return False
    for f in FILLERS:
        rest = rest.replace(f, "")
    return rest == ""


def _hash(command):
    return hashlib.sha256(_command_text(command).strip().encode("utf-8")).hexdigest()[:16]


def take_consent(vault, session_id, command):
    """這個對話目前的回合有沒有客戶對這個指令的同意：有就用掉回 True；沒有就記成待同意回 False。"""
    from kb._lib import turn
    from kb._lib.io import FileLock

    if not session_id:
        return False
    h = _hash(command)
    with FileLock(turn._session_file(vault, session_id)):
        data = turn._read_turn(turn._session_file(vault, session_id))
        if data is None:
            return False
        if data.get(GRANTED) == h and not data.get("done"):
            turn._set(vault, session_id, **{GRANTED: None, PENDING: None})
            return True
        turn._set(vault, session_id, **{PENDING: h})
        return False


def grant(vault, session_id, previous, prompt):
    """UserPromptSubmit（turn.py 開完新回合後）：上一句有待同意的指令、這一句客戶說同意 → 新回合記 codex_consent。回有沒有記。"""
    from kb._lib import turn

    pending = (previous or {}).get(PENDING)
    if not (session_id and pending and is_agreement(prompt)):
        return False
    return turn._set(vault, session_id, **{GRANTED: pending}) is not None
