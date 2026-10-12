"""守門判斷：純函式，不碰 stdin／stdout，給 hooks/guard.py 與 `kb guard decide` 共用。

決策表（D-046、D-073；specs/2026-10-12-守門.md）：路徑先 normalize，區由上到下第一個命中。
三態：allow／deny 印 JSON 給 Claude Code；pass 不表態，走預設流程。
第一輪修正：Z10b（目標範圍底下有排除夾，S3）、kb.py 放行以 plugin 自己的 __file__ 為準且檢查直譯器（S1）。
"""
import glob as globlib
import os
import re
from collections import namedtuple
from pathlib import Path

from kb._lib import cmdparse, sources as sources_lib
from kb._lib.paths import inside, normalize, plugin_root

Decision = namedtuple("Decision", "decision zone reason_code reason ledger_rows vault")

READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
FILE_TOOLS = READ_TOOLS + WRITE_TOOLS
GLOB_HITS_MAX = 5000

RESERVED = {"ai 產出": "Z3", "收件匣": "Z4", "檔案索引": "Z5", "系統": "Z6", ".system": "Z7", ".claude": "Z7"}
if os.name != "nt":
    RESERVED["AI 產出"] = "Z3"

# 區 -> {"name", "read": (decision, reason_code, reason), "write": (...)}。理由全中文、一句、說為什麼與可以怎麼做。
ZONES = {
    "Z1": {"name": "綜合卡", "read": ("allow", "", "綜合卡"), "write": ("allow", "", "綜合卡")},
    "Z2": {"name": "實體夾原檔", "read": ("allow", "", "實體夾原檔"),
           "write": ("deny", "entity_file", "實體夾裡只有同名的綜合卡能改，其他都是原檔只讀；要改內容我用檔案工具另存一份到 AI 產出。")},
    "Z3": {"name": "AI 產出", "read": ("allow", "", "AI 產出"), "write": ("allow", "", "AI 產出")},
    "Z4": {"name": "收件匣", "read": ("allow", "", "收件匣"),
           "write": ("deny", "inbox", "收件匣只收東西、不手寫，要把檔案放進來說「收進來」，我走收件流程。")},
    "Z5": {"name": "檔案索引", "read": ("allow", "", "檔案索引"), "write": ("allow", "", "檔案索引")},
    "Z6": {"name": "系統", "read": ("allow", "", "系統"),
           "write": ("deny", "system_render", "系統區是程式渲染的，直接改會被蓋掉；跟我說要改什麼，我從源頭改。")},
    "Z7": {"name": "設定與狀態", "read": ("allow", "", "設定與狀態"),
           "write": ("deny", "protected", "這是知識庫的設定與狀態檔，只有知識庫腳本能改；要加資料夾或改設定，跟我說要改什麼。")},
    "Z8": {"name": "總資料夾第一層", "read": ("allow", "", "總資料夾第一層"),
           "write": ("deny", "protected", "總資料夾第一層與型別夾根不直接放檔；請寫進 AI 產出或對應的綜合卡。")},
    "Z9": {"name": "登記的來源", "read": ("allow", "", "登記的來源"),
           "write": ("deny", "readonly_source", "客戶原檔只讀。要改內容我用檔案工具另存一份")},
    "Z10": {"name": "排除的來源", "read": ("deny", "excluded", "這個夾你說過不讀；要重新納入，跟我說「把它加回來」。"),
            "write": ("deny", "excluded", "這個夾你說過不讀，也不會寫；要重新納入，跟我說「把它加回來」。")},
    "Z10b": {"name": "範圍含排除夾", "read": ("deny", "excluded_inside", "這個範圍底下有你說過不讀的夾，請縮到不含它的子資料夾。"),
             "write": ("deny", "excluded_inside", "這個範圍底下有你說過不讀的夾，不能整個動；請縮到不含它的子資料夾。")},
    "Z11": {"name": "其他", "read": ("pass", "", ""), "write": ("pass", "", "")},
}
UNPARSED_REASON = "看不出這個指令會寫到哪，請用檔案工具或告訴我路徑。"
KB_CALL_REASON = "知識庫腳本"
ERROR_REASON = "守門出錯，這次不放行。說「檢查」我看原因"


def find_vault(cwd):
    """從 cwd 往上找第一個含 .system/sources.json 的目錄；找不到回 None（不是在知識庫裡開的對話）。"""
    if not cwd:
        return None
    try:
        p = Path(normalize(cwd, None))
    except (OSError, ValueError):
        return None
    for d in [p, *p.parents]:
        if (d / sources_lib.SOURCES_REL).is_file():
            return d
    return None


def classify(path, vault, sources):
    """path、vault 都已 normalize。回 (zone, alias)；alias 只有 Z9／Z10／Z10b 有。"""
    if inside(path, vault):
        if path == vault:
            return "Z8", None
        parts = path[len(vault) + 1:].split("/")
        first = parts[0]
        if first in RESERVED:
            return RESERVED[first], None
        if len(parts) == 1:
            return ("Z7" if first == "claude.md" else "Z8"), None
        if len(parts) == 2 and os.path.isfile(path):
            return "Z8", None  # 型別夾根直接放的檔
        if len(parts) == 3 and parts[2] == parts[1] + ".md":
            return "Z1", None
        return "Z2", None
    status, src = sources.lookup(path)
    if status == "excluded":
        return "Z10", src["alias"]
    alias = sources.excluded_under(path)
    if alias:
        return "Z10b", alias
    if status == "registered":
        return "Z9", src["alias"]
    return "Z11", None


def _row(tool, decision, zone, reason_code, head):
    return {"tool": tool, "decision": decision, "zone": zone, "reason_code": reason_code, "head": head}


def _verdict(zone, column, alias):
    decision, code, reason = ZONES[zone][column]
    if alias and decision == "allow":
        reason = f"{reason}（{alias}）"
    return decision, code, reason


def _unresolved_rows(tool, sources, head):
    return [_row(tool, "pass", "", "unresolved", f"{alias} {spec} {head}") for alias, spec in sources.unresolved]


def _file_tool(tool, target, cwd, vault, sources):
    column = "read" if tool in READ_TOOLS else "write"
    path = normalize(target, cwd)
    zone, alias = classify(path, normalize(vault, None), sources)
    decision, code, reason = _verdict(zone, column, alias)
    rows = []
    if decision == "deny":
        rows = [_row(tool, "deny", zone, code, target)]
    elif zone == "Z11" and sources.unresolved:
        rows = _unresolved_rows(tool, sources, target)
    return Decision(decision, zone, code, reason, rows, vault)


def _pattern_head(pattern):
    """萬用字元前的固定前綴資料夾。"""
    m = re.search(r"[*?\[{]", pattern)
    head = pattern[:m.start()] if m else pattern
    return head.rsplit("/", 1)[0] if "/" in head else head


def _ancestor_zone(path, vault_n, sources):
    """path 是不是某個來源根或總資料夾的上層（rm -rf 整個上層）。是就回它會動到的區。排除夾的上層由 classify 的 Z10b 處理。"""
    for src in sources.entries:
        if inside(src["root"], path):
            return "Z9", src["alias"]
    if inside(vault_n, path):
        return "Z7", None
    return None, None


def _expand_glob(token):
    try:
        hits = globlib.glob(token, recursive=True)
    except (OSError, ValueError, re.error):
        return []
    return [normalize(h, None) for h in hits[:GLOB_HITS_MAX]]


def _shell_tool(tool, command, cwd, vault, sources):
    paths = cmdparse.extract_paths(command, cwd)
    write = cmdparse.is_write(command)
    vault_n = normalize(vault, None)
    rows, saw_unresolved = [], False

    def deny(zone, code, reason):
        return Decision("deny", zone, code, reason, [_row(tool, "deny", zone, code, command)], vault)

    classified = [(p, *classify(p, vault_n, sources)) for p in paths]
    # 萬用字元：前綴資料夾與實際展開的每一個命中都不能落在排除夾（或含排除夾）
    glob_candidates = list(cmdparse.glob_prefixes(command, cwd))
    for token in cmdparse.glob_tokens(command, cwd):
        glob_candidates.extend(_expand_glob(token))
    glob_classified = [(p, *classify(p, vault_n, sources)) for p in glob_candidates]
    # 直接命中排除夾（Z10）比「範圍含排除夾」（Z10b）具體，先報它
    for _p, zone, alias in classified + glob_classified:
        if zone == "Z10":
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)

    for p, zone, alias in classified:
        for column in (("read", "write") if write else ("read",)):
            decision, code, reason = _verdict(zone, column, alias)
            if decision == "deny":
                return deny(zone, code, reason)
        if write and zone == "Z11":
            up_zone, up_alias = _ancestor_zone(p, vault_n, sources)
            if up_zone:
                _d, code, reason = _verdict(up_zone, "write", up_alias)
                return deny(up_zone, code, reason)
        if zone == "Z11" and sources.unresolved:
            saw_unresolved = True
    for _p, zone, alias in glob_classified:
        if zone == "Z10b":
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)
    if write and not paths:
        return Decision("deny", "", "unparsed", UNPARSED_REASON, [_row(tool, "deny", "", "unparsed", command)], vault)
    if saw_unresolved:
        rows = _unresolved_rows(tool, sources, command)
    return Decision("pass", "", "", "", rows, vault)


def _forbidden_interpreter_dirs(vault, sources):
    """S1：直譯器不能放在知識庫或任何來源底下（AI 寫得進去的地方）。"""
    dirs = [normalize(vault, None)]
    for src in sources.entries:
        dirs.append(src["root"])
        dirs.extend(ex["root"] for ex in src["excludes"])
    return dirs


def decide(payload):
    """照 tool_name 分流。回 Decision(decision, zone, reason_code, reason, ledger_rows, vault)。"""
    tool = payload.get("tool_name", "") or ""
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    vault = find_vault(cwd)

    if tool in SHELL_TOOLS:
        command = ti.get("command") or ""
        root = str(plugin_root())  # 以自己的 __file__ 為準，不信 CLAUDE_PLUGIN_ROOT（S1）
        sources = sources_lib.load(vault) if vault is not None else None
        forbidden = _forbidden_interpreter_dirs(vault, sources) if vault is not None else ()
        if cmdparse.is_single_plugin_call(command, root, cwd, forbidden_dirs=forbidden):
            return Decision("allow", "", "", KB_CALL_REASON, [], vault)
        if vault is None:
            return Decision("pass", "", "", "", [], None)
        return _shell_tool(tool, command, cwd, vault, sources)

    if tool in FILE_TOOLS:
        if tool == "NotebookEdit":
            target = ti.get("notebook_path")
        elif tool in ("Glob", "Grep"):
            target = ti.get("path") or cwd
        else:
            target = ti.get("file_path")
        if vault is None or not target:
            return Decision("pass", "", "", "", [], vault)
        sources = sources_lib.load(vault)
        if tool in ("Glob", "Grep"):
            # pattern／glob 帶絕對路徑或 .. 時，它的固定前綴也是讀的目標（審閱補）
            pattern = str(ti.get("pattern") or ti.get("glob") or "").replace("\\", "/")
            if pattern and (re.match(r"^([a-zA-Z]:/|/)", pattern) or ".." in pattern.split("/")):
                head = normalize(_pattern_head(pattern), normalize(str(target), cwd))
                d = _file_tool(tool, head, cwd, vault, sources)
                if d.decision == "deny":
                    return d
        return _file_tool(tool, str(target), cwd, vault, sources)

    return Decision("pass", "", "", "", [], vault)
