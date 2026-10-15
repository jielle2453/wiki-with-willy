"""守門判斷：純函式，不碰 stdin／stdout，給 hooks/guard.py 與 `kb guard decide` 共用。

決策表（D-046、D-073；specs/2026-10-12-守門.md）：路徑先 normalize，區由上到下第一個命中。
三態：allow／deny 印 JSON 給 Claude Code；pass 不表態，走預設流程。
第一輪修正：Z10b（目標範圍底下有排除夾，S3）、kb.py 放行以 plugin 自己的 __file__ 為準且檢查直譯器（S1）。
事務層（specs/2026-10-13-事務層.md）：Z1、Z3、Z5 的指令寫入 deny shell_write；檔案工具寫這三區放行後
由 hooks/guard.py 呼叫 kb._lib.turn.before_write 做備份與大改動攔截，這裡維持純判斷。
骨架與來源登記（D-079 補、I-032）：憑證三道在區之前、都只 deny、關不掉；樣式與走訪住 kb._lib.credentials，這裡只管順序：
(1) 目標路徑的段：來源根或總資料夾以下的段，其他路徑只比最後一段；(2) 指令字串每個 token 的檔名部分；
(3) 讀取範圍是資料夾（Grep、指令抽出的夾、萬用字元前綴、cd 目標）且在來源或總資料夾裡：走訪找憑證檔名，
超過 config credential_scan_max_entries 也擋。kb.py 單一呼叫的放行仍在最前面。
"""
import glob as globlib
import os
import re
from collections import namedtuple
from pathlib import Path

from kb._lib import cmdparse, config, credentials, sources as sources_lib
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
# 有 "shell_write" 的區：Bash／PowerShell 寫入用這欄（指令寫不經回合備份也不經 PostToolUse 登記，D-046 補、I-027）。
SHELL_WRITE = ("deny", "shell_write", "用指令改的退不回來，請用檔案工具改。")
ZONES = {
    "Z1": {"name": "綜合卡", "read": ("allow", "", "綜合卡"), "write": ("allow", "", "綜合卡"), "shell_write": SHELL_WRITE},
    "Z2": {"name": "實體夾原檔", "read": ("allow", "", "實體夾原檔"),
           "write": ("deny", "entity_file", "實體夾裡只有同名的綜合卡能改，其他都是原檔只讀；要改內容我用檔案工具另存一份到 AI 產出。")},
    "Z3": {"name": "AI 產出", "read": ("allow", "", "AI 產出"), "write": ("allow", "", "AI 產出"), "shell_write": SHELL_WRITE},
    "Z4": {"name": "收件匣", "read": ("allow", "", "收件匣"),
           "write": ("deny", "inbox", "收件匣只收東西、不手寫，要把檔案放進來說「收進來」，我走收件流程。")},
    "Z5": {"name": "檔案索引", "read": ("allow", "", "檔案索引"), "write": ("allow", "", "檔案索引"), "shell_write": SHELL_WRITE},
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
VAULT_ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8")
SOURCE_ZONES = ("Z9", "Z10", "Z10b")
CREDENTIAL_REASON = "檔名像密碼或金鑰（{seg}），一律不讀也不寫，這條關不掉。"
CREDENTIAL_INSIDE_REASON = "這個範圍裡有檔名像密碼或金鑰的檔（{rel}），請縮到不含它的子資料夾或指定單一檔。"
CREDENTIAL_LIMIT_REASON = "範圍太大，守門來不及檢查有沒有密碼檔，請縮小範圍。"


def _credential_base(path, zone, vault_n, sources):
    """第一道的比對起點：總資料夾、路徑最深的來源根；Z11 回 None（只比最後一段）；Z10b 在來源根之上回 ""（沒有段可比）。"""
    if zone in VAULT_ZONES:
        return vault_n
    if zone in SOURCE_ZONES:
        best = None
        for src in sources.entries:
            if inside(path, src["root"]) and (best is None or len(src["root"]) > len(best["root"])):
                best = src
        return best["root"] if best else ""
    return None


def credential_hit(path, zone, vault_n, sources):
    """第一道（D-079 補、M1）：path 已 normalize。回命中的段或 None。"""
    base = _credential_base(path, zone, vault_n, sources)
    if base is None:
        segments = [path.rsplit("/", 1)[-1]]
    elif not base or path == base or not inside(path, base):
        return None
    else:
        segments = path[len(base) + 1:].split("/")
    return credentials.match_segment(segments)


_NOT_FILENAME_CHARS = set("*?[]^$()|")  # 正規式與萬用字元：含這些的 token 不是檔名（N3）
_REGEX_ONLY_CHARS = set("^$()|+")  # 是正規式、不是萬用字元的字元（P2）：不存在又含這些的「路徑」是搜尋樣式，不當檔名比
_WILDCARD_CHARS = set("*?[")
# 範圍搜尋類指令（寫死）：子指令是管線第一段、沒給任何讀取路徑時走訪效用 cwd（N1、P1、P4）
SEARCH_ALWAYS = {"rg", "find", "tree"}  # 本來就遞迴
GREP_FAMILY = {"grep", "egrep", "fgrep", "findstr", "select-string", "sls"}  # 要帶 -r／-R／--recursive／/s
LIST_VERBS = {"ls", "dir", "get-childitem", "gci"}  # 要帶遞迴旗標


def token_hit(command):
    """第二道（H1；裁定 1 收窄、N3 再收）：指令每個 token 去引號、取 / 與 \\ 的最後一段，只有像檔名的才比樣式
    （含 . 或命中 id_rsa*、id_ed25519*）；- 與 $ 開頭（旗標、變數）、含空白或正規式／萬用字元字元的 token 跳過。
    不解析路徑、不看 cwd。回命中的那段或 None。"""
    cleaned = cmdparse.DISCARD_OUTPUT.sub(" ", command)
    for text, _quoted, _after_redirect in cmdparse._tokens(cleaned):  # cmdparse 的 token 切法與路徑抽取一致
        if not text or text[0] in "-$" or any(ch.isspace() or ch in _NOT_FILENAME_CHARS for ch in text):
            continue
        last = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if credentials.looks_like_filename(last) and credentials.match_segment((last,)) is not None:
            return last
    return None


def _excluded_roots(sources):
    return [ex["root"] for src in sources.entries for ex in src["excludes"]]


def folder_scan(vault, path, sources):
    """第三道（H1、N4）：讀取範圍是資料夾時走訪找憑證檔名，排除夾不進去（交 Z10b 用自己的理由句）。回 (reason_code, reason) 或 (None, None)。"""
    hit, truncated = credentials.scan_dir(path, config.get(vault, "credential_scan_max_entries"), _excluded_roots(sources))
    if hit is not None:
        return "credential_inside", CREDENTIAL_INSIDE_REASON.format(rel=hit)
    if truncated:
        return "credential_scan_limit", CREDENTIAL_LIMIT_REASON
    return None, None


def _scan_zone(path, zone, vault_n, sources):
    """第三道要不要走訪這個夾：在來源或總資料夾裡用它的區；Z11 但底下含來源根或總資料夾也走訪（N2）；其他回 None。"""
    if zone != "Z11":
        return zone
    return _ancestor_zone(path, vault_n, sources)[0]


def _is_recursive_flag(verb, token):
    """這個旗標讓 verb 變成範圍搜尋：grep 家族 -r／-R／--recursive（可併 -rn）、findstr／dir 的 /s、ls -R、Get-ChildItem -Recurse／-r。"""
    low = token.lower()
    if verb in GREP_FAMILY:
        return low in ("--recursive", "/s") or (token.startswith("-") and not token.startswith("--") and any(c in "rR" for c in token[1:]))
    if verb == "ls":
        return low == "--recursive" or (token.startswith("-") and not token.startswith("--") and "R" in token)
    if verb == "dir":
        return low == "/s"
    return low in ("-r", "-recurse") or low.startswith("-rec")


def _stages(command):
    """把指令切成子指令與管線段：回 [(子指令序號, 段序號, 段字串)]。引號外的 ; && || & 與換行切子指令，| 切管線段。引號保留給 _tokens 去。"""
    out, buf, quote, prev = [], [], None, ""
    sub = stage = 0
    i = 0

    def flush():
        text = "".join(buf).strip()
        if text:
            out.append((sub, stage, text))
        buf.clear()

    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in ";\n" or command.startswith("&&", i) or command.startswith("||", i) or ch == "&":
            flush()
            sub, stage = sub + 1, 0
            if command.startswith("&&", i) or command.startswith("||", i):
                i += 1
        elif ch == "|":
            flush()
            stage += 1
        else:
            buf.append(ch)
        prev = ch
        i += 1
    flush()
    return out


def _verb_of(tokens):
    text = tokens[0][0] if tokens else ""
    return text, text.lower().replace("\\", "/").rsplit("/", 1)[-1]


def _analyze(command, cwd):
    """第三道的材料（N1、P1、P4、S2）。回 (read_dirs, write_targets)，都是正規化路徑。
    read_dirs：`.`／`..`、效用 cwd（cd 之後）底下存在的裸夾名、引號外裸萬用字元的效用 cwd、
    範圍搜尋類子指令（管線第一段）沒給任何讀取路徑時的效用 cwd。write_targets：導向目標、寫入動詞那一段的路徑，不走訪。"""
    cleaned = cmdparse.DISCARD_OUTPUT.sub(" ", command)
    base = normalize(cwd, None)
    read_dirs, write_targets = [], set()
    subs = {}  # 子指令序號 -> {"target": 有沒有讀取目標, "search": 範圍搜尋沒給路徑時要走訪的 cwd}
    for sub, stage, text in _stages(cleaned):
        info = subs.setdefault(sub, {"target": False, "search": None})
        tokens = cmdparse._tokens(text)
        verb_token, verb = _verb_of(tokens)
        bare = cmdparse.outside_quotes(text)
        verb_write = bool(cmdparse.WRITE_WORDS.search(bare if bare is not None else text))
        expect_cd, recursive = verb in cmdparse._CD, False
        for tok, quoted, after_redirect in tokens[1:]:
            if expect_cd:
                if tok.startswith("-"):
                    continue
                expect_cd = False
                if tok and "$" not in tok and "%" not in tok:
                    base = normalize(tok, base)
                continue
            if not tok or tok[0] in "$%":
                continue
            if after_redirect:
                write_targets.add(normalize(tok, base))
                continue
            if tok.startswith("-") or (verb in ("findstr", "dir") and tok.startswith("/") and len(tok) == 2):
                recursive = recursive or _is_recursive_flag(verb, tok)
                continue
            if tok in (".", ".."):
                p = normalize(tok, base)
            elif not quoted and any(ch in _WILDCARD_CHARS for ch in tok):
                info["target"] = True
                if "/" not in tok and "\\" not in tok and not verb_write:
                    read_dirs.append(base)  # 裸萬用字元（cat *）：前綴夾就是效用 cwd；帶斜線的由 glob_tokens 處理
                continue
            elif "/" in tok or "\\" in tok or tok.startswith("~"):
                p = normalize(tok, base)
            elif any(ch in _NOT_FILENAME_CHARS for ch in tok):
                continue
            else:
                p = normalize(tok, base)
                if not os.path.exists(p):
                    continue
            if verb_write:
                write_targets.add(p)
                continue
            info["target"] = True
            if os.path.isdir(p):
                read_dirs.append(p)
        if stage == 0 and not verb_write and (verb in SEARCH_ALWAYS or (verb in GREP_FAMILY | LIST_VERBS and recursive)):
            info["search"] = base
    for info in subs.values():
        if info["search"] and not info["target"]:
            read_dirs.append(info["search"])
    return read_dirs, write_targets


def _original_segment(target, seg):
    """理由句印原始大小寫的那一段：normalize 在 Windows 會小寫，從 payload 原字串找回來。"""
    for part in str(target).replace("\\", "/").split("/"):
        if part.lower() == seg.lower():
            return part
    return seg


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
    vault_n = normalize(vault, None)
    zone, alias = classify(path, vault_n, sources)
    seg = credential_hit(path, zone, vault_n, sources)
    if seg is not None:  # 憑證第一道先於區（D-079 補）
        reason = CREDENTIAL_REASON.format(seg=_original_segment(target, seg))
        return Decision("deny", zone, "credential", reason, [_row(tool, "deny", zone, "credential", target)], vault)
    if tool == "Grep" and zone != "Z10" and os.path.isdir(path):  # 第三道：Grep 整夾會讀內容；Glob 只列檔名不走訪；起點是排除夾交 Z10（P3）
        scan_zone = _scan_zone(path, zone, vault_n, sources)
        if scan_zone:
            code, reason = folder_scan(vault, path, sources)
            if code:
                return Decision("deny", scan_zone, code, reason, [_row(tool, "deny", scan_zone, code, target)], vault)
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
    # 萬用字元：前綴資料夾（接 cd 之後的 cwd，只算一次）與實際展開的每一個命中都不能落在排除夾（或含排除夾）
    heads = []
    glob_hits = []
    for token in cmdparse.glob_tokens(command, cwd):
        head = _pattern_head(token)
        if head.endswith(":"):
            head += "/"
        if head and head not in heads:
            heads.append(head)
        glob_hits.extend(_expand_glob(token))
    head_classified = [(p, *classify(p, vault_n, sources)) for p in heads]
    glob_classified = head_classified + [(p, *classify(p, vault_n, sources)) for p in glob_hits]
    # 憑證三道先於區（D-079 補）。第一道：抽出的每個路徑、萬用字元前綴夾與展開命中的段（P2）
    for p, zone, _alias in classified + glob_classified:
        if not os.path.exists(p) and any(ch in _REGEX_ONLY_CHARS for ch in p):
            continue  # N3／P2：引號裡帶副檔名的正規式（"密碼[0-9]+\.log"）會被抽成路徑；不存在又含正規式字元的不當檔名比
        seg = credential_hit(p, zone, vault_n, sources)
        if seg is not None:
            return deny(zone, "credential", CREDENTIAL_REASON.format(seg=_original_segment(command, seg)))
    # 第二道：裸 token 的檔名部分（cat id_rsa、type .env）
    seg = token_hit(command)
    if seg is not None:
        return deny("", "credential", CREDENTIAL_REASON.format(seg=seg))
    # 第三道：讀取範圍是資料夾（抽出的路徑、cd 目標、萬用字元前綴夾、.／..、裸夾名、範圍搜尋沒給路徑的 cwd）。
    # 有寫入時讀取範圍照走訪，只有寫入目標不走訪（S2）；起點是排除夾或排除單檔交 Z10（P3）
    read_dirs, write_targets = _analyze(command, cwd)
    extra = [(p, *classify(p, vault_n, sources)) for p in read_dirs]
    scanned = []
    for p, zone, _alias in classified + head_classified + extra:
        if p in scanned or p in write_targets or zone == "Z10" or not os.path.isdir(p):
            continue
        scanned.append(p)
        scan_zone = _scan_zone(p, zone, vault_n, sources)
        if not scan_zone:
            continue
        code, reason = folder_scan(vault, p, sources)
        if code:
            return deny(scan_zone, code, reason)
    # 直接命中排除夾（Z10）比「範圍含排除夾」（Z10b）具體，先報它
    for _p, zone, alias in classified + glob_classified:
        if zone == "Z10":
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)

    for p, zone, alias in classified:
        columns = ("read",)
        if write:
            columns = ("read", "shell_write" if "shell_write" in ZONES[zone] else "write")
        for column in columns:
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


def decide(payload):
    """照 tool_name 分流。回 Decision(decision, zone, reason_code, reason, ledger_rows, vault)。"""
    tool = payload.get("tool_name", "") or ""
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    vault = find_vault(cwd)

    if tool in SHELL_TOOLS:
        command = ti.get("command") or ""
        root = str(plugin_root())  # 以自己的 __file__ 為準，不信 CLAUDE_PLUGIN_ROOT（S1）
        sources = sources_lib.load(vault) if vault is not None else None  # 先讀：sources.json 壞掉時 kb.py 也不自動放行
        if cmdparse.is_single_plugin_call(command, root, cwd):
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
