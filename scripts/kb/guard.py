"""守門判斷：純函式，不碰 stdin／stdout，給 hooks/guard.py 與 `kb guard decide` 共用。

決策表（D-046、D-073；specs/2026-10-12-守門.md）：路徑先 normalize，區由上到下第一個命中。
三態：allow／deny 印 JSON 給 Claude Code；pass 不表態，走預設流程。
第一輪修正：Z10b（目標範圍底下有排除夾，S3）、kb.py 放行以 plugin 自己的 __file__ 為準且檢查直譯器（S1）。
事務層（specs/2026-10-13-事務層.md）：Z1、Z3、Z5 的指令寫入 deny shell_write；檔案工具寫這三區放行後
由 hooks/guard.py 呼叫 kb._lib.turn.before_write 做備份與大改動攔截，這裡維持純判斷。
骨架與來源登記（D-079 補、D-081、I-032）：憑證三道在區之前、都只 deny；樣式與走訪住 kb._lib.credentials，這裡只管順序與生效範圍：
(1) 目標路徑的段：來源根以下的段，知識庫外只比最後一段；(2) 指令字串每個 token 的檔名部分；
(3) 讀取範圍是資料夾（Grep、指令抽出的夾、萬用字元前綴、cd 目標、.／..、裸夾名、cwd 在 raw 裡的範圍搜尋）：走訪找憑證檔名，
超過 config credential_scan_max_entries 也擋。生效範圍（D-081）：登記來源、含來源或總資料夾的上層夾、知識庫外；
總資料夾內部（Z1～Z8）不比、不走訪，走訪上層夾時跳過總資料夾整棵；第二道與走訪 cwd 只在效用 cwd 不在總資料夾裡時做。
kb.py 單一呼叫的放行仍在最前面。
produce（第 3 點、實作回報裁定 1、3）：Z2、Z9 擋寫的理由句指到 kb office copy／source-edit move，Z10／Z10b 照舊；
檔案工具 Write／Edit／MultiEdit 寫 .system/temp/office/ 底下放行（工作區，不備份不登記）：路徑先 normalize（.. 跳出去照 Z7），
磁碟上存在的部分再 realpath 一次，連結跳出去的也照 Z7。
密碼與守門（D-183～D-186）：
- 照 shell 語法拆（D-186）：heredoc 與 here-string 內文是資料（cmdparse.split_bodies），只抽絕對路徑照讀判斷；
  每一段照動詞分讀寫（cmdparse.stage_kind）：已知只讀指令只有導向才是寫，直譯器只看程式碼有沒有寫檔，殼照舊整段看；
  寫入欄只套真正被寫的路徑（導向目標、寫入那段的參數；寫入那段沒有自己的路徑時整個子指令），其餘照讀（收 B-024）。
- AI 產出（D-186 補）：寫進去、非 kb 執行檔的參數或效用 cwd、餵給直譯器的內文碰到就擋（OUTPUT_SHELL）；
  已知只讀指令、echo 的字串、餵給 cat／tee 的內文裡提到的當讀。
- 只列檔名、只切目錄（D-185、B-093 (b)）：第三道不走訪；cd 的目標不算 Z10b；排除夾照擋。
- 「可以讀」裁示（D-183、D-184）：憑證命中時才查 rulings.json 與清冊，命中的那一份是有效裁示掛的條目就放讀；寫照各區；
  排除最優先；資料夾範圍裡的密碼檔全部有裁示才放行。
- 工具夾與 plugin 夾不比憑證樣式（B-088、B-093 (a)，credentials.exempt）。
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
# produce：指令碰到 AI 產出一律擋（讀也擋）；成品經 kb output save 放進來、轉 PDF 與看版面經 kb office（D-170）
OUTPUT_SHELL = ("deny", "output_shell", "AI 產出的檔用 kb output save 放進來；要轉 PDF 或看版面用 kb office")
SOURCE_WRITE_REASON = "客戶原檔只讀；要改先用 kb office copy 拿一份副本"
# B-093 (e)：Z9 擋寫不只是改內容，也有人想放新檔進去
SOURCE_FOLDER_REASON = "客戶原檔的夾只讀；要放新檔進去用 kb source-edit move（或請客戶自己放），要改內容先用 kb office copy 拿副本"
WORKSPACE_POSIX = ".system/temp/office"  # kb office 的工作區：檔案工具寫放行（實作回報裁定 1）
WORKSPACE_TOOLS = ("Write", "Edit", "MultiEdit")
LO_PROFILE_DIR = "_lo_profile"  # kb office 固定的 LibreOffice 設定夾（office.LO_PROFILE），不算工作區


def _odd_segments(target):
    """路徑中間有段結尾是空白或點（.system /temp）：normalize 會修掉，Windows 卻會建出另一個夾，不給工作區例外。"""
    segs = str(target).replace("\\", "/").split("/")[:-1]
    return any(seg not in ("", ".", "..") and not seg.endswith(":") and seg != seg.rstrip(" .") for seg in segs)


ZONES = {
    "Z1": {"name": "綜合卡", "read": ("allow", "", "綜合卡"), "write": ("allow", "", "綜合卡"), "shell_write": SHELL_WRITE},
    "Z2": {"name": "實體夾原檔", "read": ("allow", "", "實體夾原檔"),
           "write": ("deny", "entity_file", "實體夾裡只有同名的綜合卡能改；其他是" + SOURCE_WRITE_REASON)},
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
           "write": ("deny", "readonly_source", SOURCE_FOLDER_REASON)},
    "Z10": {"name": "排除的來源", "read": ("deny", "excluded", "這個夾你說過不讀；要重新納入，跟我說「把它加回來」。"),
            "write": ("deny", "excluded", "這個夾你說過不讀，也不會寫；要重新納入，跟我說「把它加回來」。")},
    "Z10b": {"name": "範圍含排除夾", "read": ("deny", "excluded_inside", "這個範圍底下有你說過不讀的夾，請縮到不含它的子資料夾。"),
             "write": ("deny", "excluded_inside", "這個範圍底下有你說過不讀的夾，不能整個動；請縮到不含它的子資料夾。")},
    "Z11": {"name": "其他", "read": ("pass", "", ""), "write": ("pass", "", "")},
}
VAULT_ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8")
SOURCE_ZONES = ("Z9", "Z10", "Z10b")
CREDENTIAL_REASON = "檔名像密碼或金鑰（{seg}），客戶沒說這份可以讀，不讀也不寫。"
CREDENTIAL_INSIDE_REASON = "這個範圍裡有檔名像密碼或金鑰的檔（{rel}），客戶沒說可以讀；請縮到不含它的子資料夾或指定單一檔。"
CREDENTIAL_LIMIT_REASON = "範圍太大，守門來不及檢查有沒有密碼檔，請縮小範圍。"


def _credential_base(path, zone, vault_n, sources):
    """第一道的比對起點：路徑最深的來源根；Z11 回 None（只比最後一段）；Z10b 在來源根之上回 ""（沒有段可比）。
    總資料夾內部（Z1～Z8）也回 ""：D-081 內部豁免，不比。"""
    if zone in VAULT_ZONES:
        return ""
    if zone in SOURCE_ZONES:
        best = None
        for src in sources.entries:
            if inside(path, src["root"]) and (best is None or len(src["root"]) > len(best["root"])):
                best = src
        return best["root"] if best else ""
    return None


def credential_hit(path, zone, vault_n, sources):
    """第一道（D-079 補、M1）：path 已 normalize。回命中的段或 None。
    produce 的工具夾、plugin 自己的夾不比（B-088、B-093 (a)：cacert.pem、credentials.py 不是密碼檔）。"""
    if credentials.exempt(path):
        return None
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
# 範圍搜尋類指令（寫死；第四輪收窄）：效用 cwd 在 raw 裡、子指令任一處有搜尋動詞、那一段沒有存在的讀取路徑 → 走訪效用 cwd
SEARCH_ALWAYS = {"rg", "find", "tree", "select-string", "sls"}  # 本來就遞迴或讀整個管線
GREP_FAMILY = {"grep", "egrep", "fgrep", "findstr"}  # 要帶 -r／-R／--recursive／/s
LIST_VERBS = {"ls", "dir", "get-childitem", "gci"}  # 要帶 -r／-R／-Recurse／/s


def _token_filename_hit(text):
    """第二道對一個 token：去引號、取 / 與 \\ 的最後一段，只有像檔名的才比樣式（含 . 或命中 id_rsa*、id_ed25519*）；
    沒斜線又 - 或 $／% 開頭（旗標、變數）、含空白或正規式／萬用字元字元的 token 跳過；$HOME/.ssh/id_rsa 這種帶斜線的照比。
    回命中的那段或 None。"""
    if not text or any(ch.isspace() for ch in text):
        return None
    has_sep = "/" in text or "\\" in text
    if text[0] in "-$%" and not has_sep:
        return None
    last = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if any(ch in _NOT_FILENAME_CHARS for ch in (last if has_sep else text)):
        return None  # 正規式／萬用字元只看檔名那段：$HOME/.ssh/id_rsa 的 $ 在變數裡，不算
    if credentials.looks_like_filename(last) and credentials.match_segment((last,)) is not None:
        return last
    return None


def token_hit(command):
    """第二道（H1；裁定 1 收窄、N3 再收）對整句：不解析路徑、不看 cwd。decide 走的是 _analyze 裡按效用 cwd 判的版本（D-081）。"""
    cleaned = cmdparse.DISCARD_OUTPUT.sub(" ", command)
    for text, _quoted, _after_redirect, _first, _qchar in cmdparse._tokens(cleaned):
        seg = _token_filename_hit(text)
        if seg is not None:
            return seg
    return None


def _excluded_roots(sources):
    return [ex["root"] for src in sources.entries for ex in src["excludes"]]


def folder_scan(vault, path, sources):
    """第三道（H1、N4、D-081）：讀取範圍是資料夾時走訪找憑證檔名；排除夾與排除單檔不進去（交 Z10／Z10b 用自己的理由句），
    總資料夾整棵、工具夾與 plugin 夾（B-088）也跳過。範圍裡的密碼檔全部都有「可以讀」裁示才放行（D-183）。
    回 (reason_code, reason) 或 (None, None)。"""
    skip = _excluded_roots(sources) + [normalize(str(vault), None)] + credentials.exempt_roots()
    hits, _files, truncated, _m = credentials._scan(path, config.get(vault, "credential_scan_max_entries"), False, None, skip)
    if hits:
        ids = readable_ids(vault)
        left = [h for h in hits if not ruled_readable(vault, sources, normalize(os.path.join(str(path), h), None), ids)]
        if left:
            return "credential_inside", CREDENTIAL_INSIDE_REASON.format(rel=left[0])
    if truncated:
        return "credential_scan_limit", CREDENTIAL_LIMIT_REASON
    return None, None


def _scan_zone(path, zone, vault_n, sources):
    """第三道要不要走訪這個夾：總資料夾內部不走訪（D-081）；來源裡用它的區；Z11 但底下含來源根或總資料夾也走訪（N2）；其他回 None。"""
    if zone in VAULT_ZONES:
        return None
    if zone != "Z11":
        return zone
    return _ancestor_zone(path, vault_n, sources)[0]


def _is_recursive_flag(verb, token):
    """這個旗標讓 verb 變成範圍搜尋：grep 家族 -r／-R／--recursive（可併 -rn）、findstr／dir 的 /s、ls／dir -R／-r、Get-ChildItem -Recurse／-r。"""
    low = token.lower()
    if verb in GREP_FAMILY:
        return low in ("--recursive", "/s") or (token.startswith("-") and not token.startswith("--") and any(c in "rR" for c in token[1:]))
    if verb in ("ls", "dir"):
        return low in ("--recursive", "/s", "-r", "-recurse") or (token.startswith("-") and not token.startswith("--") and "R" in token)
    return low in ("-r", "-recurse") or low.startswith("-rec")


def _tokens_q(cmd, globs_in_quotes=False, powershell=None):
    """跟 cmdparse._tokens 同一套切法，多記一個「是不是萬用字元 token」（Q7：Bash 只認引號外；V1：PowerShell 的 cmdlet 自己會展開引號裡的，照收；
    第十輪 A6：要含 *，或 ?／[ 加路徑分隔符且不是網址）。token 含 {a,b} 先展開成各組合（第十輪 A1）。
    回 [(字串, 有引號, 是萬用字元, 緊接在 > 後面)]。"""
    out, buf, ubuf, quote, quoted, after_redirect, prev = [], [], [], None, False, False, ""

    def flush():
        nonlocal buf, ubuf, quoted, after_redirect
        if buf or quoted:
            text, unquoted = "".join(buf), "".join(ubuf)
            for form in cmdparse.brace_expand(text):
                out.append((form, quoted, cmdparse.is_wildcard(form, None if globs_in_quotes else unquoted), after_redirect))
            head = cmdparse.brace_head(text, powershell=globs_in_quotes) if "{" in text else None  # globs_in_quotes 只在 PowerShell 為真
            if head is not None:
                out.append((head, quoted, False, False))  # 第十一輪保底：{ 之前的資料夾也當路徑（讀取就走訪）
            after_redirect = False
        buf, ubuf, quoted = [], [], False

    powershell = globs_in_quotes if powershell is None else powershell
    skip = 0
    for i, ch in enumerate(cmd):
        if skip:
            skip -= 1
            prev = ch
            continue
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            quoted = True
        elif not powershell and cmdparse.escaped_break(cmd, i):
            skip = 1  # find 的 \;、行尾 \ 續行：單獨的 \ 不是磁碟根（測試 28 N4）
            if cmd[i + 1] not in "\r\n":
                buf.append(cmd[i + 1])
                ubuf.append(cmd[i + 1])
            elif cmd.startswith("\r\n", i + 1):
                skip = 2
        elif ch == " " and prev == "\\":
            buf.append(ch)
            ubuf.append(ch)
        elif cmdparse.redirect_joint(cmd, i):
            flush()
            after_redirect = True  # >|、>&檔、&>檔：後面那個是導向目標（F2-2）
        elif ch.isspace() or ch in cmdparse._TOKEN_BREAK:
            flush()
            if ch == ">":
                after_redirect = True
            elif ch in cmdparse._TOKEN_BREAK:
                after_redirect = False
        else:
            buf.append(ch)
            ubuf.append(ch)
        prev = ch
    flush()
    return out


def _stages(command, powershell=False):
    """把指令切成子指令與管線段：回 [(子指令序號, 段序號, 段字串)]。引號外的 ; && || & 與換行切子指令，| 切管線段。引號保留給 _tokens 去。
    引號與跳脫照 cmdparse.outside_quotes（測試 28 F2-1：Bash 單引號裡、PowerShell 的 \\ 都是普通字）；
    導向裡的 & 與 |（>|、>&檔、&>檔）不切（F2-2）。"""
    out, buf, quote = [], [], None
    esc = "`" if powershell else "\\"
    sub = stage = 0
    i, n = 0, len(command)

    def flush():
        text = "".join(buf).strip()
        if text:
            out.append((sub, stage, text))
        buf.clear()

    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == esc and quote in ('"', "$'") and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote[-1]:
                quote = None
        elif ch == esc and i + 1 < n:
            buf.append(command[i:i + 2])
            i += 2
            continue
        elif not powershell and command.startswith("$'", i):
            quote = "$'"
            buf.append("$'")
            i += 2
            continue
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif cmdparse.redirect_joint(command, i):
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
        i += 1
    flush()
    return out


_CD_VERBS = cmdparse._CD | {"push-location"}  # V6：Push-Location 也是 cd
_SINGLE_SWITCH = re.compile(r"^/[a-zA-Z]$")  # V5／X3：開關式指令或 PowerShell 的單字母 /x 是旗標


def _norm(tok, base):
    """接效用 cwd 正規化；效用 cwd 推不出來（base 是 None）時相對路徑回 None。"""
    if base is None and not cmdparse._ABS_START.match(tok):
        return None
    return normalize(tok, base)


def _outside_vault(base, vault_n):
    """效用 cwd 不在總資料夾裡（推不出來也算不在，V6：不豁免）。"""
    return base is None or not inside(base, vault_n)


def _plan_stages(command, bodies, powershell=False):
    """D-186：每一段的動詞、種類（write／read）、有沒有導向，與它屬於哪個子指令。直譯器與殼的程式碼範圍是自己這段、
    同一個子指令裡任何一段的 heredoc 內文、與管線前面幾段（echo 程式碼 | python、cat <<EOF | bash）。回 [dict]。"""
    stages = _stages(command, powershell)
    by_sub = {}
    for sub, stage, text in stages:
        by_sub.setdefault(sub, []).append((stage, text))
    out = []
    for sub, stage, text in stages:
        toks = _tokens_q(cmdparse.strip_marks(text), powershell=powershell)
        words = [t for t, _q, _g, _a in toks]
        args = [t for t, _q, _g, a in toks if not a]  # 導向目標以外（只讀動詞數位置參數用，F2-5）
        verb = cmdparse.verb_of(words)
        runs = cmdparse.inner_verb(verb, words)  # xargs sh -c、find -exec bash -c：照裡面那個殼或直譯器判（測試 28 N1）
        same = by_sub[sub]
        code = ""
        if runs in cmdparse.INTERPRETERS or runs in cmdparse.SHELL_WRAPPERS:
            # 整句的內文都算它的程式碼：here-string／heredoc 可能先放進變數再餵（$c = @'…'@ 換行 $c | python -，測試 28）
            code = "\n".join(bodies)
            code += "\n" + "\n".join(t for s, t in same if s < stage)
            fed = "\n".join([text] + [bodies[m] for m in cmdparse.data_marks(text) if m < len(bodies)]
                            + [t for s, t in same if s < stage])
            if cmdparse._VAR_INSIDE.search(fed):
                # 程式碼經變數餵（c='rm T'; bash <<< "$c"、echo "$c" | bash、bash -c "$c"）：變數在別處設，整句都算它的程式碼（測試 28 N5）
                code += "\n" + cmdparse.strip_marks(command)
                var_code = True
            else:
                var_code = False
        else:
            var_code = False
        bare = cmdparse.outside_quotes(cmdparse.strip_marks(text), powershell)
        out.append({"sub": sub, "stage": stage, "text": text, "verb": verb, "words": words, "runs": runs, "var_code": var_code,
                    "kind": cmdparse.stage_kind(runs, cmdparse.strip_marks(text), code, args, powershell),
                    "redirect": bare is None or ">" in bare, "marks": cmdparse.data_marks(text)})
    return out


_VALUE_PARAM = re.compile(r"-(?:value|inputobject)\s*:?\s*$", re.IGNORECASE)


def _substituted_args(text, runs, powershell):
    """寫入那段的參數是指令替換（$(…)、反引號、<(…)；PowerShell 的 (…)、$(…)、@(…)）：目標推不出來（測試 28 N6）。
    替換本身另外遞迴判；這裡把替換換成一個佔位字後這段還是寫，才算目標來自替換（kb.py version @(Remove-Item x) 的寫在替換裡，不算）。
    PowerShell 的 {…}、@{…} 不是參數值；接在 -Value、-InputObject 後面的是要寫的內容，不是目標。拆不出來也算。"""
    try:
        subs = cmdparse.substitutions(text, powershell)
    except cmdparse.Unbalanced:
        return True
    spans = []
    for pos, inner in subs:
        if powershell and not (text.startswith(("$(", "@("), pos) or text[pos] == "("):
            continue
        if powershell and _VALUE_PARAM.search(text[:pos]):
            continue
        end = text.find(inner, pos) + len(inner) + 1
        if not any(a <= pos < b for a, b in spans):
            spans.append((pos, end))
    if not spans:
        return False
    blanked, last = [], 0
    for a, b in sorted(spans):
        blanked.append(text[last:a] + " KBSUBST ")
        last = b
    blanked.append(text[last:])
    return cmdparse.stage_kind(runs, "".join(blanked), "", None, powershell) == "write"


def body_paths(body):
    """heredoc／here-string 內文裡的絕對路徑（D-186 (d)：內文照舊抽路徑判斷讀），同 cmdparse.code_paths。"""
    return cmdparse.code_paths(body)


def _analyze(command, cwd, vault_n, tool="Bash", bodies=()):
    """第二、三道的材料（N1、P1、P4、S2、D-081 第四輪、第五輪 V1 V2 V5 V6），加照 shell 語法拆的角色（密碼與守門 D-186）。
    command 已經把 heredoc／here-string 內文換成記號（cmdparse.split_bodies）。回 dict：
    read_dirs：要走訪的夾（`.`／`..`、效用 cwd 底下存在的裸夾名、裸萬用字元的效用 cwd、
      cwd 在總資料夾外且該管線段有搜尋動詞又沒有存在的讀取路徑時的效用 cwd）；
    implicit_dirs：read_dirs 裡不是指令點名、是隱含的範圍（grep -r 的效用 cwd、cat * 的效用 cwd、. 與 ..），要另外過 Z10／Z10b；
    read_paths：存在的讀取路徑（檔或夾）；
    write_targets：真正被寫的路徑（D-186）：導向目標、寫入那一段的路徑；寫入那段沒有自己的路徑（| xargs rm、| Remove-Item）
      就是同一個子指令裡所有的路徑；
    write_globs：寫入那一段的萬用字元；unparsed：有寫入那一段卻找不到任何目標；has_write：有任何寫入（含導向）；
    globs：含萬用字元的 token（Bash 只認引號外，PowerShell 引號內也收），接效用 cwd 正規化；cd 到萬用字元路徑的目標也收（V2）；
    cd_targets：cd 的目標（只切目錄不讀內容：不走第三道、不算 Z10b，D-185 與 B-093 (b)；Z10 照擋）；
    list_only：整句只有列檔名、切目錄、印目前目錄（D-185），沒有管線、導向、內文：第三道不走訪；
    body_reads：內文裡的絕對路徑（照讀判斷）；token：第二道命中的段（只在效用 cwd 不在總資料夾裡、或推不出效用 cwd 時比）。
    效用 cwd 跨管線段、跨子指令延續。"""
    cleaned = cmdparse.DISCARD_OUTPUT.sub(" ", command)
    base = normalize(cwd, None)
    out = {"read_dirs": [], "implicit_dirs": [], "read_paths": set(), "write_targets": set(), "write_globs": [],
           "globs": [], "cd_dirs": [], "cd_targets": set(), "token": None, "unparsed": False, "has_write": False,
           "list_only": False, "body_reads": [], "stages": []}
    plans = _plan_stages(cleaned, bodies, tool == "PowerShell")
    out["stages"] = plans
    out["list_only"] = bool(plans) and not bodies and all(
        not p["redirect"] and cmdparse.list_only(p["verb"], p["words"]) for p in plans) and all(p["stage"] == 0 for p in plans)
    sub_paths, sub_globs, sub_write_empty = {}, {}, {}
    uncertain, cd_written = [], set()

    def add_write(p, _tok):
        out["write_targets"].add(p)

    for plan in plans:
        text = plan["text"]
        stage_text = cmdparse.strip_marks(text)
        tokens = _tokens_q(stage_text, globs_in_quotes=(tool == "PowerShell"))
        verb = plan["verb"]
        stage_write = plan["kind"] == "write"
        write_verb = stage_write  # 只有寫入那一段才讓隱含的讀取範圍不走訪
        if stage_write or any(a for _t, _q, _g, a in tokens):
            out["has_write"] = True
        if stage_write and plan["stage"] > 0 and (tool == "PowerShell" or verb in cmdparse.PIPE_ARG_VERBS) \
                and verb not in cmdparse.CONTENT_SINKS:
            # 目標從前一段的輸出來（| xargs rm、| Remove-Item、| % { rm $_ }、kb … | xargs rm -rf）：推不出會寫到哪，一律擋，
            # 前一段是 kb 也一樣（測試 28 N2）。| Set-Content x 這類寫的是內容、目標是點名的檔，照一般判
            out["unparsed"] = True
        if stage_write and (verb in cmdparse.PIPE_ARG_VERBS or _substituted_args(stage_text, plan["runs"], tool == "PowerShell")):
            # 目標來自指令替換或檔案清單（rm -rf $(kb … folders find …)、rm $(cat list)、xargs -a list rm）：跟 N2 同一條（測試 28 N6）
            out["unparsed"] = True
        switch_ok = tool == "PowerShell" or verb in cmdparse.SWITCH_VERBS  # X3
        stage_base = base
        if stage_write and stage_base in out["cd_targets"]:
            cd_written.add(stage_base)  # cd 進去之後要寫：那個夾照一般規則判（cd 的 Z10b 豁免只給只切目錄、讀、列的）
        own, stage_reads = [], []
        if verb not in _CD_VERBS:
            try:
                own = [p for raw, p in cmdparse._walk(stage_text, stage_base or cwd, powershell=(tool == "PowerShell"))
                       if stage_base is not None or cmdparse._ABS_START.match(raw)]
            except (OSError, ValueError):
                own = []
        for m in plan["marks"]:
            if m < len(bodies):
                for raw in body_paths(bodies[m]):
                    p = _norm(raw, stage_base)
                    if p is not None:
                        out["body_reads"].append(p)
        if stage_write and (plan["runs"] in cmdparse.INTERPRETERS or plan["runs"] in cmdparse.SHELL_WRAPPERS):
            for body in bodies:  # 直譯器的程式碼要寫：整句內文裡的路徑都是目標（經變數餵的也算）
                own.extend(p for p in (_norm(raw, stage_base) for raw in body_paths(body)) if p is not None)
            if plan["var_code"]:  # 程式碼經變數餵：整句裡的絕對路徑都是目標（N5）
                own.extend(p for p in (_norm(raw, stage_base) for raw in cmdparse.code_paths(cmdparse.strip_marks(cleaned)))
                           if p is not None)
        expect_cd, has_read_path = False, False
        verbs, flags, stage_globs, redirect_targets = [], [], [], []
        for tok, quoted, glob_out, after_redirect in tokens:
            low = tok.lower()
            if expect_cd:
                if tok.startswith("-"):
                    continue
                expect_cd = False
                if not tok:
                    continue
                if "$" in tok or "%" in tok:
                    base = None  # $HOME、%USERPROFILE% 展不開：效用 cwd 推不出來（V6）
                elif glob_out:
                    p = _norm(tok, base)
                    hits = [h for h in _expand_glob(p, hidden=True)] if p is not None else []
                    dirs = [h for h in hits if os.path.isdir(h)]
                    exact = len(dirs) == 1
                    if not dirs and p is not None and _pattern_head(p):
                        dirs = [_pattern_head(p)]  # 第十輪 A2：[…] 字元類展不開（薪[^x]）就退回前綴夾做區判斷與走訪
                    out["cd_dirs"].extend(dirs)  # cd 到萬用字元路徑：用展開後的夾走訪與區判斷（W4），效用 cwd 當成第一個
                    if exact:
                        out["cd_targets"].update(dirs)
                        uncertain = []
                    else:
                        uncertain = list(dirs)  # 展不開或不只一個：不知道真的切到哪，之後寫入時這些夾都算目標、不給 cd 的 Z10b 豁免
                    base = dirs[0] if dirs else None
                else:
                    base = _norm(tok, base)
                    uncertain = []
                    if base is not None:
                        out["cd_targets"].add(base)
                continue
            if low in _CD_VERBS:
                expect_cd = True
                continue
            if not tok or tok == "=":
                continue
            if after_redirect:
                p = _norm(tok, base)
                if p is not None:
                    add_write(p, tok)
                    redirect_targets.append(p)
                    if glob_out:
                        out["globs"].append(p)  # 第十輪 A3：導向目標含萬用字元（> ../客*/a.txt），前綴夾與展開命中也進區判斷
                        out["write_globs"].append(p)
                continue
            word = low.replace("\\", "/").rsplit("/", 1)[-1]
            if word in SEARCH_ALWAYS or word in GREP_FAMILY or word in LIST_VERBS:
                verbs.append(word)  # 動詞任一處都算（time、sudo、LC_ALL=C、$x = 前綴不用特別處理）
                continue
            if tok.startswith("-") or (switch_ok and _SINGLE_SWITCH.match(tok)):
                flags.append(tok)
                continue
            if out["token"] is None and (_outside_vault(base, vault_n) or "$" in tok or "%" in tok):
                hit = _token_filename_hit(tok)  # 第二道：效用 cwd 不在總資料夾裡、推不出來，或 token 自己帶展不開的變數時做
                if hit is not None:
                    p = _norm(tok, base) if not ("$" in tok or "%" in tok) else None
                    if p is None or not credentials.exempt(p):
                        out["token"] = hit
                        out["token_path"] = p
                        out["token_write"] = stage_write
            if tok[0] in "$%":
                continue
            if tok in (".", ".."):
                p = _norm(tok, base)
                if p is None:
                    continue
                if stage_write:
                    continue  # rm -rf .、cp x .、tar czf x .：寫入段的 . 是寫入目標（_walk 已收進 own）
                has_read_path = True
                out["read_paths"].add(p)
                stage_reads.append(p)
                out["read_dirs"].append(p)  # Q8：讀取那段（含 tee 之前、tar c 的輸入）的 . 與 .. 是讀取範圍
                out["implicit_dirs"].append(p)
                continue
            if glob_out:
                has_read_path = True
                p = _norm(tok, base)
                if p is not None:
                    out["globs"].append(p)
                    stage_globs.append(p)
                if "/" not in tok and "\\" not in tok and not write_verb and base is not None:
                    out["read_dirs"].append(base)  # 裸萬用字元（cat *）：前綴夾就是效用 cwd
                    out["implicit_dirs"].append(base)
                continue
            if stage_write and tool != "PowerShell" and not quoted and ("?" in tok or "[" in tok) and "://" not in tok:
                p = _norm(tok, base)
                if p is not None:
                    own.extend(_expand_glob(p))  # Bash 會展開沒分隔符的 ?、[（rm -rf raw?、mv .sys?em）：寫入那段照展開命中算目標
                continue
            if any(ch in _NOT_FILENAME_CHARS for ch in tok) and not quoted:
                continue
            p = _norm(tok, base)
            if p is None:
                continue
            if stage_write:
                continue  # 寫入段的參數：目標由 cmdparse._walk 抽（新檔、裸檔名照它的規則），不在這裡當讀
            if not os.path.exists(p):
                continue  # 樣式參數（"2026/10"、TODO/FIXME）不存在就不算路徑
            has_read_path = True
            out["read_paths"].add(p)
            stage_reads.append(p)
            if os.path.isdir(p):
                out["read_dirs"].append(p)
        search = any(v in SEARCH_ALWAYS or any(_is_recursive_flag(v, f) for f in flags) for v in verbs)
        if search and not has_read_path and not write_verb and base is not None and not inside(base, vault_n):
            out["read_dirs"].append(base)
            out["implicit_dirs"].append(base)
        elif search and not has_read_path and not write_verb and base is not None:
            out["implicit_dirs"].append(base)  # 總資料夾裡的隱含範圍：不走訪（D-081），但 Z10／Z10b 照判
        if not search and not has_read_path and base is not None and (verb in LIST_VERBS or verb == "tree"):
            out["implicit_dirs"].append(base)  # cd 之後的 ls、gci：列的是效用 cwd，跟 ls <那個夾> 一樣過 Z10／Z10b（只列檔名不走訪）
        if stage_write and uncertain:
            own.extend(uncertain)  # cd 到展不開的萬用字元之後才寫：那些夾整個算目標（第九輪 Z1、驗收 90）
        own = [p for p in dict.fromkeys(own) if p not in redirect_targets]
        sub_paths.setdefault(plan["sub"], []).extend(own + stage_reads)
        sub_globs.setdefault(plan["sub"], []).extend(stage_globs)
        if stage_write:
            if own or stage_globs:
                out["write_targets"].update(own)
                out["write_globs"].extend(stage_globs)
            else:
                sub_write_empty.setdefault(plan["sub"], True)
        plan["base"] = stage_base
    # 寫入那段沒有自己的路徑：吃管線前面的（ls x | xargs rm、gci x | Remove-Item、% { rm $_ }），整個子指令的路徑都算目標
    for sub in sub_write_empty:
        paths = list(dict.fromkeys(sub_paths.get(sub, [])))
        globs = sub_globs.get(sub, [])
        if paths or globs:
            out["write_targets"].update(paths)
            out["write_globs"].extend(globs)
        else:
            out["unparsed"] = True
    out["cd_targets"] -= cd_written
    out["final_base"] = base  # 這段指令跑完時的效用 cwd（指令替換遞迴用）
    return out


def _original_segment(target, seg):
    """理由句印原始大小寫的那一段：normalize 在 Windows 會小寫，從 payload 原字串找回來。"""
    for part in str(target).replace("\\", "/").split("/"):
        if part.lower() == seg.lower():
            return part
    return seg


UNPARSED_REASON = "看不出這個指令會寫到哪，請用檔案工具或告訴我路徑。"
KB_CALL_REASON = "知識庫腳本"
CURLY_REASON = "PowerShell 會把彎引號（“ ” ‘ ’）當成引號拆開指令；改用「」或直引號，或改用 Bash 再跑"
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
    return [_row(tool, "pass", "", "unresolved", f"{u['alias']} {u['spec']} {head}") for u in sources.unresolved]


def in_workspace(path, vault_n):
    """normalize 過的 path 在工作區 .system/temp/office/ 底下（不含工作區根本身）：先照字串前綴判（測試後修正裁定 12：
    新庫還沒有這個夾也要放行）；工作區夾已存在時，磁碟上存在的部分 realpath 後還要在裡面（工作區裡的連結跳出去的不算）；
    還不存在時，存在的最近上層不能是被連結導走的。"""
    root = vault_n.rstrip("/") + "/" + WORKSPACE_POSIX
    if path == root or not inside(path, root):
        return False
    if path[len(root) + 1:].split("/", 1)[0] == LO_PROFILE_DIR:
        return False  # LibreOffice 的設定（巨集、信任路徑）不給檔案工具改（收尾 security-review）
    if not os.path.exists(root):
        anc = _nearest_existing(root)
        return anc is not None and normalize(os.path.realpath(anc), None) == anc
    cur = _nearest_existing(path)
    if cur is None:
        return False
    real = normalize(os.path.realpath(cur), None)
    real_root = normalize(os.path.realpath(root), None)
    return real == real_root or inside(real, real_root)


def _nearest_existing(path):
    """normalize 過的 path 往上找第一個磁碟上存在的；找不到回 None。"""
    cur = path
    while not os.path.exists(cur):
        head = cur.rsplit("/", 1)[0] if "/" in cur else ""
        if not head or head == cur:
            return None
        cur = head
    return cur


def _file_tool(tool, target, cwd, vault, sources):
    column = "read" if tool in READ_TOOLS else "write"
    path = normalize(target, cwd)
    vault_n = normalize(vault, None)
    zone, alias = classify(path, vault_n, sources)
    if tool in WORKSPACE_TOOLS and zone == "Z7" and not _odd_segments(target) and in_workspace(path, vault_n):
        return Decision("allow", zone, "", "工作區", [], vault)  # 實作回報裁定 1：不經回合備份（hook 只備 Z1／Z3／Z5）
    seg = credential_hit(path, zone, vault_n, sources)
    if seg is not None and not (column == "read" and zone != "Z10" and ruled_readable(vault, sources, path)):
        # 憑證第一道先於區（D-079 補）；客戶說「這份可以讀」的那一份只放讀（D-183），排除的照擋（D-184 不讀最優先）
        reason = CREDENTIAL_REASON.format(seg=_original_segment(target, seg))
        return Decision("deny", zone, "credential", reason, [_row(tool, "deny", zone, "credential", target)], vault)
    if tool == "Grep" and zone != "Z10" and os.path.isdir(path):  # 第三道：Grep 整夾會讀內容；Glob 只列檔名不走訪；起點是排除夾交 Z10（P3）；總資料夾內部不走訪（D-081）
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
    m = re.search(r"[*?\[]", pattern)  # 大括號已在 cmdparse.brace_expand 展開（A1）
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


def _expand_glob(token, hidden=False):
    """shell 會展開成哪些路徑。hidden：PowerShell 的 * 連 .env 這種點開頭的檔也算（bash 預設不算），Python 3.11 起才有 include_hidden。"""
    try:
        try:
            hits = globlib.glob(token, recursive=True, include_hidden=hidden)
        except TypeError:
            hits = globlib.glob(token, recursive=True)
    except (OSError, ValueError, re.error):
        return []
    return [normalize(h, None) for h in hits[:GLOB_HITS_MAX]]


def _touches_output(info, bodies, vault_n, sources, tool):
    """produce 與 D-186 補：指令碰到 AI 產出（Z3）。只有真正會寫或看不出會不會寫的才算：
    (1) 寫入目標在 Z3；(2) 不是已知只讀的那一段（node、python 腳本、soffice、cp…）的參數或效用 cwd 在 Z3；
    (3) 餵給直譯器或殼的 heredoc 內文提到 AI 產出。已知只讀指令（cat、grep、Get-Content、Test-Path…）、echo 的字串、
    餵給 cat／tee 的 heredoc 內文裡提到的不算（讀）。只多擋、不放行任何東西。"""
    for p in info["write_targets"]:
        if inside(p, vault_n) and classify(p, vault_n, sources)[0] == "Z3":
            return True
    for plan in info["stages"]:
        verb = plan["verb"]
        if verb in _CD_VERBS or (verb in cmdparse.READONLY_VERBS and plan["kind"] != "write"):
            continue
        base = plan.get("base")
        if base is not None and inside(base, vault_n) and classify(base, vault_n, sources)[0] == "Z3":
            return True
        if verb in cmdparse.INTERPRETERS or verb in cmdparse.SHELL_WRAPPERS:
            for body in bodies:  # 整句的內文（經變數餵直譯器的也算，測試 28）
                if "ai 產出" in body.casefold():
                    return True
                for raw in body_paths(body):
                    p = _norm(raw, base)
                    if p is not None and inside(p, vault_n) and classify(p, vault_n, sources)[0] == "Z3":
                        return True
        for tok, _quoted, _glob, _after in _tokens_q(cmdparse.strip_marks(plan["text"]), globs_in_quotes=(tool == "PowerShell")):
            if not tok:
                continue
            value = [tok.split("=", 1)[1]] if "=" in tok else []
            if tok[:1] in "-/" and ":" in tok:  # PowerShell 的 -Path:值、Windows 程式的 /out:值
                value.append(tok.split(":", 1)[1])
            forms = value if tok.startswith("-") else [tok] + value  # --out=值 只看值
            forms += [cmdparse._ESCAPE.sub("", f) for f in forms]  # bash 把 AI\ 產出 讀成 AI 產出（同 cmdparse 的 N-c）
            for form in forms:
                if not form or form[0] in "$%":
                    continue
                p = _norm(form, base)
                if p is not None and inside(p, vault_n) and classify(p, vault_n, sources)[0] == "Z3":
                    return True
    return False


def readable_ids(vault):
    """有效「可以讀」裁示掛的清冊編號（D-183、D-184）；rulings.json 讀不到當沒有（照擋）。"""
    try:
        from kb._lib import rulings
        return rulings.readable_ids(vault)
    except Exception:  # noqa: BLE001  讀不到就照擋
        return set()


def ruled_readable(vault, sources, path, ids=None):
    """這個路徑是清冊裡一份有「可以讀」裁示的檔（裁示跟著清冊條目走，搬、改名、改內容照樣認得，D-184）。
    排除的、不在清冊的、清冊讀不到的都回 False（照擋）。只在憑證命中時叫（平常不多讀檔）。"""
    ids = readable_ids(vault) if ids is None else ids
    if not ids:
        return False
    try:
        from kb._lib import inventory
        uri = inventory.uri_of(vault, sources, path)
        entry = inventory.entry_for(vault, uri) if uri else None
    except Exception:  # noqa: BLE001  清冊讀不到：照擋
        return False
    return entry is not None and entry.get("status") == "active" and entry.get("id") in ids


SUBST_DEPTH_MAX = 4


def _inner_commands(clean, bodies, powershell):
    """會被執行的內層指令（測試 28）：回 [(在 clean 裡的位置, 內文)]；heredoc／here-string 會展開的內文裡的也算（位置是記號）。
    拆不出來丟 cmdparse.Unbalanced。"""
    found = cmdparse.substitutions(clean, powershell)
    for idx, body in enumerate(bodies):
        if idx < len(bodies.expands) and bodies.expands[idx]:
            pos = clean.find(cmdparse.DATA_MARK.format(idx))
            found += [(max(pos, 0), s) for _p, s in cmdparse.substitutions(body, powershell, literal=True)]
    return found


def _shell_tool(tool, command, cwd, vault, sources, depth=0):
    powershell = tool == "PowerShell"
    clean, bodies = cmdparse.split_bodies(command, powershell=powershell)
    flat = cmdparse.strip_marks(clean)
    vault_n = normalize(vault, None)
    rows, saw_unresolved = [], False

    def deny(zone, code, reason):
        return Decision("deny", zone, code, reason, [_row(tool, "deny", zone, code, command)], vault)

    def unparsed():
        return Decision("deny", "", "unparsed", UNPARSED_REASON, [_row(tool, "deny", "", "unparsed", command)], vault)

    # 指令替換與展開（$(…)、反引號、<(…)、PowerShell 的 (…)、{…}）裡是會執行的指令：外層是 echo、ls 也一樣，
    # 照同一套遞迴判斷，效用 cwd 用替換出現的位置之前算出來的那個；拆不出來照 unparsed 擋（測試 28，D-186）
    try:
        inner = _inner_commands(clean, bodies, powershell)
    except cmdparse.Unbalanced:
        return unparsed()
    for pos, text in inner:
        if not text.strip():
            continue
        if depth >= SUBST_DEPTH_MAX:
            return unparsed()
        base = _analyze(clean[:pos], cwd, vault_n, tool, bodies)["final_base"]
        d = _shell_tool(tool, text, base or cwd, vault, sources, depth + 1)
        if d.decision == "deny":
            return deny(d.zone, d.reason_code, d.reason)
        if base is None and _analyze(*cmdparse.split_bodies(text, powershell=powershell)[:1], cwd, vault_n, tool)["has_write"]:
            return unparsed()  # 效用 cwd 推不出來（cd $X 之後）又要寫：看不出寫到哪

    info = _analyze(clean, cwd, vault_n, tool, bodies)
    paths = cmdparse.extract_paths(flat, cwd, powershell=powershell)
    # D-186 (d)：內文照舊抽路徑判斷讀（憑證、排除）；只是提到一個夾不算讀它整夾，不走第三道
    body_only = [p for p in dict.fromkeys(info["body_reads"]) if p not in paths]
    paths += body_only
    writes = info["write_targets"]
    classified = [(p, *classify(p, vault_n, sources)) for p in paths]
    # 萬用字元（Bash 只認引號外，PowerShell 引號內也算，Q7、V1）：前綴資料夾（接 cd 之後的 cwd）與實際展開的每一個命中都不能落在排除夾（或含排除夾）
    heads, glob_hits, write_hits = [], [], []
    for token in info["globs"]:
        head = _pattern_head(token)
        if head.endswith(":"):
            head += "/"
        if head and head not in heads:
            heads.append(head)
        hits = _expand_glob(token, hidden=powershell)
        glob_hits.extend(hits)
        if token in info["write_globs"]:
            write_hits.extend(hits + ([head] if head else []))
    prefix_classified = [(p, *classify(p, vault_n, sources)) for p in heads]
    cd_classified = [(p, *classify(p, vault_n, sources)) for p in info["cd_dirs"]]
    head_classified = prefix_classified + cd_classified  # 第三道走訪：前綴夾與 cd 到萬用字元的展開夾（W4）
    # 區判斷與 Z10 用：展開命中與 cd 展開夾比前綴夾具體，排前面（第九輪 Z1：rm -rf ../客戶* 報 readonly_source，不報前綴夾的 Z10b）
    glob_classified = [(p, *classify(p, vault_n, sources)) for p in glob_hits] + cd_classified + prefix_classified
    ids = None

    def readable(p):
        nonlocal ids
        if p in writes:
            return False  # 「可以讀」只放讀；寫照各區的規則（D-183）
        if ids is None:
            ids = readable_ids(vault)
        return ruled_readable(vault, sources, p, ids)

    # 憑證三道先於區（D-079 補、D-081）。第一道：抽出的每個路徑、萬用字元前綴夾與展開命中的段（P2）；命中排除項目的交 Z10（Q5）
    for p, zone, _alias in classified + glob_classified:
        if zone == "Z10" or (not os.path.exists(p) and any(ch in _REGEX_ONLY_CHARS for ch in p)):
            continue  # N3／P2：引號裡帶副檔名的正規式（"密碼[0-9]+\.log"）會被抽成路徑；不存在又含正規式字元的不當檔名比
        seg = credential_hit(p, zone, vault_n, sources)
        if seg is not None and not readable(p):
            return deny(zone, "credential", CREDENTIAL_REASON.format(seg=_original_segment(command, seg)))
    # 第二道：裸 token 的檔名部分（cat id_rsa、type .env），效用 cwd 不在總資料夾裡才做
    if info["token"] is not None:
        tp = info.get("token_path")
        if tp is None or info.get("token_write") or not readable(tp):
            return deny("", "credential", CREDENTIAL_REASON.format(seg=info["token"]))
    # 第三道：讀取範圍是資料夾（抽出的路徑、cd 目標、萬用字元前綴夾、.／..、裸夾名、cwd 在 raw 裡的範圍搜尋）。
    # 只列檔名或只切目錄的不走訪（D-185）；有寫入時讀取範圍照走訪，只有寫入目標不走訪（S2）；起點是排除夾或排除單檔交 Z10（P3）；
    # 總資料夾內部不走訪（D-081）
    if not info["list_only"]:
        extra = [(p, *classify(p, vault_n, sources)) for p in info["read_dirs"]]
        scanned = []
        for p, zone, _alias in classified + head_classified + extra:
            if p in scanned or p in writes or p in body_only or zone == "Z10" or not os.path.isdir(p):
                continue  # 真正的磁碟根照走訪，撞上限就「範圍太大」（W2）；/c 這種開關 cmdparse 已不當路徑（V5）
            scanned.append(p)
            scan_zone = _scan_zone(p, zone, vault_n, sources)
            if not scan_zone:
                continue
            code, reason = folder_scan(vault, p, sources)
            if code:
                return deny(scan_zone, code, reason)
    # 直接命中排除夾（Z10）比「範圍含排除夾」（Z10b）具體，先報它；隱含的範圍（grep -r 的效用 cwd、cat * 的效用 cwd）也算
    implicit = [(p, *classify(p, vault_n, sources)) for p in dict.fromkeys(info["implicit_dirs"])]
    for _p, zone, alias in classified + glob_classified + implicit:
        if zone == "Z10":
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)

    # produce：寫進 AI 產出、或非 kb 執行檔的參數碰到 AI 產出就擋（D-186 補；單一 kb 呼叫在 decide 最前面已放行）
    try:
        touched = _touches_output(info, bodies, vault_n, sources, tool)
    except Exception:  # noqa: BLE001  hook 對指令出錯不表態（會變成放行），這裡寧可擋（收尾 security-review）
        touched = True
    if touched:
        _d, code, reason = OUTPUT_SHELL
        return deny("Z3", code, reason)

    # D-186：每個路徑照讀判斷；只有真正被寫的（導向目標、寫入那段的參數、管線後面才寫時整個子指令）套寫入欄
    # （第八輪「整條 is_write 為真全部套寫入欄」的誤擋收在這裡，B-024）。只切目錄的目標不算 Z10b（只切目錄不讀內容）
    def write_column(p, zone, alias):
        for column in ("read", "shell_write" if "shell_write" in ZONES[zone] else "write"):
            decision, code, reason = _verdict(zone, column, alias)
            if decision == "deny":
                return deny(zone, code, reason)
        if zone == "Z11":
            up_zone, up_alias = _ancestor_zone(p, vault_n, sources)
            if up_zone:
                _d, code, reason = _verdict(up_zone, "write", up_alias)
                return deny(up_zone, code, reason)
        return None

    checked = set()
    cd_only = info["list_only"]  # cd 的 Z10b 豁免只給整句只切目錄、列檔名的（cd 進去再跑程式照擋）
    for p, zone, alias in classified:  # 照抽出的順序，每個路徑先讀、是寫的目標再寫（理由句跟第八輪以前一樣具體）
        decision, code, reason = _verdict(zone, "read", alias)
        if decision == "deny" and not (zone == "Z10b" and cd_only and p in info["cd_targets"] and p not in writes):
            return deny(zone, code, reason)
        if p in writes:
            checked.add(p)
            got = write_column(p, zone, alias)
            if got:
                return got
        if zone == "Z11" and sources.unresolved:
            saw_unresolved = True
    for p in [w for w in sorted(writes) if w not in checked] + [h for h in dict.fromkeys(write_hits)]:
        zone, alias = classify(p, vault_n, sources)
        got = write_column(p, zone, alias)
        if got:
            return got
        if zone == "Z11" and sources.unresolved:
            saw_unresolved = True
    for p, zone, alias in glob_classified:
        if zone == "Z10b" and not (cd_only and p in info["cd_targets"] and p not in writes):
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)
    for _p, zone, alias in implicit:  # 隱含的範圍（cd 進含排除夾的夾再 grep -r、tar .）：cd 本身放行，讀那個範圍照擋
        if zone == "Z10b":
            _d, code, reason = _verdict(zone, "read", alias)
            return deny(zone, code, reason)
    if info["has_write"] and (info["unparsed"] or cmdparse.unresolved_paths(flat, powershell=powershell)):
        # 寫入那段找不到目標，或有帶 $變數／%變數% 展不開的路徑（第九輪 Z3）：看不出會寫到哪，一律擋
        return unparsed()
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
        if (tool == "PowerShell" and cmdparse.CURLY_QUOTES & set(command)
                and cmdparse.KB_SCRIPT.rsplit("/", 1)[-1] in command.casefold()):
            # PowerShell 把彎引號當引號：任何子命令的文字參數都拆得開、能夾帶別的陳述式，直接擋；Bash 裡是普通字，照常認（第四輪）
            return Decision("deny", "", "curly_quote", CURLY_REASON, [_row(tool, "deny", "", "curly_quote", command)], vault)
        if cmdparse.is_single_plugin_call(command, root, cwd, tool):
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
            target_n = normalize(str(target), cwd)
            # 第十一輪補：path 含萬用字元時，萬用字元前的前綴夾先做區判斷（Glob path "../raw/行政/*" 要 Z10b）
            if cmdparse.is_wildcard(str(target)):
                d = _file_tool(tool, _pattern_head(target_n), cwd, vault, sources)
                if d.decision == "deny":
                    return d
            # 第十一輪保底：path 或樣式含 { 時，{ 之前的資料夾先做區判斷（Glob path "../raw/行政/{薪資,x}/*" 要 Z10b）
            for text, base in ((str(target), cwd), (str(ti.get("pattern") or ""), target_n), (str(ti.get("glob") or ""), target_n)):
                head = cmdparse.brace_head(text) if "{" in text else None
                if head is not None:
                    d = _file_tool(tool, normalize(head, base), cwd, vault, sources)
                    if d.decision == "deny":
                        return d
            # pattern／glob 帶絕對路徑或 .. 時，它的固定前綴也是讀的目標（審閱補）
            pattern = str(ti.get("pattern") or ti.get("glob") or "").replace("\\", "/")
            if pattern and (re.match(r"^([a-zA-Z]:/|/)", pattern) or ".." in pattern.split("/")):
                head = normalize(_pattern_head(pattern), target_n)
                d = _file_tool(tool, head, cwd, vault, sources)
                if d.decision == "deny":
                    return d
        return _file_tool(tool, str(target), cwd, vault, sources)

    return Decision("pass", "", "", "", [], vault)
