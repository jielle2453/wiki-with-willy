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
skill 夾（D-533、D-538）：知識庫 .claude/skills/ 底下（不含夾本身）是 Z13，在 RESERVED 判 Z7 之前；跳出判斷照工作區（in_skills），
跳出去、中間段結尾空白或點的照 Z7。willy 不管這區：讀、檔案工具寫、指令寫都照 Z11 pass（不看內容、不備份）。
密碼與守門（D-183～D-186）：
- 照 shell 語法拆（D-186）：heredoc 與 here-string 內文是資料（cmdparse.split_bodies），只抽絕對路徑照讀判斷；
  每一段照動詞分讀寫（cmdparse.stage_kind）：已知只讀指令只有導向才是寫，直譯器只看程式碼有沒有寫檔，殼照舊整段看；
  寫入欄只套真正被寫的路徑（導向目標、寫入那段的參數；寫入那段沒有自己的路徑時整個子指令），其餘照讀（收 B-024）。
- AI 產出（D-186 補）：放寬甲起併進暫存區（Z4），照使用者區規則判（OUTPUT_SHELL 拿掉了）。
- 只列檔名、只切目錄（D-185、B-093 (b)）：第三道不走訪；cd 的目標不算 Z10b；排除夾照擋。
- 「可以讀」裁示（D-183、D-184）：憑證命中時才查 rulings.json 與清冊，命中的那一份是有效裁示掛的條目就放讀；寫照各區；
  排除最優先；資料夾範圍裡的密碼檔全部有裁示才放行。
- 工具夾與 plugin 夾不比憑證樣式（B-088、B-093 (a)，credentials.exempt）。
開檔（D-514）：Start-Process、Invoke-Item、start、explorer 開的是文件、資料夾或網址（cmdparse.open_stage）→ 目標照讀判、不算碰 AI 產出，
開一個夾不走第三道也不算 Z10b；-RedirectStandardOutput／Error 照寫判；開的是程式 → 程式檔在知識庫或來源裡擋 run_file，
其餘把「程式名加參數」那行當內層指令遞迴判。COM 附著到開著的 Office 改（D-515）守門不表態：沒有路徑的 .Save()、Range.Delete()、
InsertAfter 不管，Documents.Open 是讀；附著時拿來比對的路徑（-eq、-like、==）照一般規則判，沒有另外的豁免（放寬甲）。
守門補洞（#25、D-348）：跟全名寫法同一套判斷，新擋的理由句都指到會先留一份的路——
-EncodedCommand（-e、-ec、-en…，-、-- 或 / 開頭，值可用冒號接）解開當 -Command 的內容遞迴判，解不開擋 unparsed；別名 clc、epcsv（content）、sp（照
Set-ItemProperty）；iwr、irm、Invoke-WebRequest、Invoke-RestMethod、PowerShell 的 curl、wget 帶 -OutFile 是寫（目標是它的值）；
.NET 檔案靜態呼叫（[System.IO.File]、[IO.Directory]、([type]'System.IO.File')，cmdparse.file_calls）與 [IO.FileInfo]::new('<p>').Delete()、
(Get-Item '<p>').Delete() 照刪、寫、搬、複製判（_file_call_check）；Office 帶字面路徑的另存（SaveAs、SaveAs2、SaveCopyAs、
ExportAsFixedFormat；Excel 匯出的路徑在第二個參數、python 可用 FileName=）既有檔 content、新檔放行（PowerShell 在 _check_plan，python 在 _interp_check）；這兩種的 ::方法(、.方法( 括號
是方法參數不是指令替換（_substituted_args 的 _METHOD_CALL；別的方法照舊）。沒有字面路徑的（$f.Delete()、變數）照放寬甲放行，等 #67。
"""
import glob as globlib
import os
import re
from collections import namedtuple
from pathlib import Path

from kb._lib import cmdparse, config, credentials, sources as sources_lib
from kb._lib.areas import HOLD_DIR
from kb._lib.paths import inside, normalize, plugin_root

Decision = namedtuple("Decision", "decision zone reason_code reason ledger_rows vault")

READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
FILE_TOOLS = READ_TOOLS + WRITE_TOOLS
GLOB_HITS_MAX = 5000

RESERVED = {HOLD_DIR.casefold(): "Z4", "檔案索引": "Z5", "系統": "Z6", ".system": "Z7", ".claude": "Z7", ".codex": "Z7", ".obsidian": "Z7"}  # 暫存區（原收件匣與 AI 產出，D-324）是 Z4；Z3 不再出現
if os.name != "nt":
    RESERVED[HOLD_DIR] = "Z4"

# 區 -> {"name", "read": (decision, reason_code, reason), "write": (...)}。理由全中文、一句、說為什麼與可以怎麼做。
# 放寬甲：理由寫給 AI（不是使用者看的），每句以 AI_TAIL 結尾；使用者的區（USER_ZONES）指令寫照「使用者區規則」
# （_user_rules）：建、搬、改名放行，只有救不回來的（刪、蓋掉同名檔、改既有檔內容）才擋，並教 AI 改用的做法。
AI_TAIL = "不用跟使用者提這一步。"
USER_ZONES = ("Z2", "Z4", "Z8", "Z9")  # 使用者的檔所在的區：指令寫照使用者區規則、檔案工具寫放行（hook 先備份）
WILLY_ZONES = ("Z1", "Z5", "Z6", "Z7")  # willy 自己的檔：指令寫一律 willy；檔案工具寫只有 Z1、Z5、客戶設定檔放行
PROFILE_MD = "系統/客戶設定檔.md"
INDEX_MD = "總目錄.md"
WILLY_REASON = "「{name}」是 willy 自己的檔：{hint}。" + AI_TAIL
HINT_EDIT = "用 Edit 或 Write 改"
HINT_PROFILE = "用 Edit 改，改完跑 kb profile apply"
HINT_RENDERED = "是程式產生的，從源頭的 kb 指令改"
HINT_KB = "用對應的 kb 指令改"
DELETE_REASON = ("這會刪掉「{name}」，刪了救不回來：改用 kb source-edit trash <路徑>（可以一次給多個、整個夾也收），"
                 "會先進資源回收筒。" + AI_TAIL)
OVERWRITE_REASON = ("「{dest}」已經有同名的「{name}」，這樣會蓋掉它：改用 kb source-edit move <來源> --to <夾>，"
                    "同名時它會告訴你內容一不一樣，再選 --replace 或 --name。" + AI_TAIL)
CONTENT_REASON = ("這會直接改「{name}」的內容、改之前留不了一份：文字檔用 Edit 或 Write 改（會先留一份），"
                  "Word、Excel 這類用 kb office。下載或匯出會蓋掉既有檔：存成新檔名，或先 kb source-edit trash 舊的再存。"
                  "要存的是她開著的那份 Office 檔：用附著那份的 .Save()，不要另存到同一路徑。" + AI_TAIL)  # 後兩句：守門補洞裁定 12
BATCH_REASON = ("批次搬檔看不出會不會蓋掉同名檔：mv、cp 加 -n，Move-Item 不加 -Force，Copy-Item 先用 Test-Path 跳過已有的；"
                "或用 kb source-edit move 一次給多個檔。" + AI_TAIL)
CODE_REASON = ("這段程式會刪檔或覆蓋檔，來不及先留一份：刪檔用 kb source-edit trash，寫或改檔用 Edit／Write，"
               "搬檔改名（os.rename、shutil.move）可以照跑。" + AI_TAIL)
EXCLUDED_REASON = "「{alias}」使用者說過不讀，不讀也不寫；使用者要重新納入時跑 kb folders include <路徑>。" + AI_TAIL
EXCLUDED_INSIDE_REASON = "這個範圍底下有使用者說過不讀的夾，縮到不含它的子資料夾再做。" + AI_TAIL
SCRIPT_UNREAD = "script_unread"  # 腳本檔讀不到或超過 guard_script_max_bytes：照 D-306(4) 放行，ledger 記一行
RUN_FILE = ("deny", "run_file", "知識庫與原檔夾裡的程式或巨集檔不直接執行；要打開給使用者看，用 PDF、Word 這類文件。" + AI_TAIL)
_RUN_EXT = ("", ".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".lnk", ".wsf", ".wsh", ".vbe", ".jse", ".msc", ".cpl")  # 裸程式名在效用 cwd 底下找的副檔名
# 備份還原與換電腦（D-206）：AI 自己下指令搬或複製整個知識庫（寫入目標是總資料夾本身），理由句說客戶自己搬怎麼做
VAULT_MOVE_CODE = "vault_move"
VAULT_MOVE_REASON = "整個知識庫要搬家：請使用者關掉這個對話、用檔案總管剪下貼上，再到新位置開對話。"
MOVE_COPY_VERBS = {"mv", "move", "move-item", "mi", "cp", "copy", "copy-item", "cpi", "robocopy", "xcopy", "rsync"}
WORKSPACE_POSIX = ".system/temp/office"  # kb office 的工作區：檔案工具寫放行（實作回報裁定 1）
WORKSPACE_TOOLS = ("Write", "Edit", "MultiEdit")
SKILLS_POSIX = ".claude/skills"  # 客戶的 skill（D-533、D-538）：底下是 Z13，willy 不管，讀寫都 pass（照 Z11）
LO_PROFILE_DIR = "_lo_profile"  # kb office 固定的 LibreOffice 設定夾（office.LO_PROFILE），不算工作區


def _odd_segments(target):
    """路徑中間有段結尾是空白或點（.system /temp）：normalize 會修掉，Windows 卻會建出另一個夾，不給工作區例外。"""
    segs = str(target).replace("\\", "/").split("/")[:-1]
    return any(seg not in ("", ".", "..") and not seg.endswith(":") and seg != seg.rstrip(" .") for seg in segs)


def _zone(name, **kw):
    return {"name": name, "read": ("allow", "", name), "write": ("allow", "", name), **kw}


# 檔案工具寫：使用者區放行（hook 先備份）；willy 自己的檔（Z6 除客戶設定檔、Z7、知識庫根、總目錄）willy。指令寫另走 _user_rules／willy
ZONES = {
    "Z1": _zone("綜合卡"),
    "Z2": _zone("實體夾原檔"),
    "Z4": _zone(HOLD_DIR),
    "Z5": _zone("檔案索引"),
    "Z6": _zone("系統"),  # 除客戶設定檔外的檔案工具寫由 is_willy_file 改判 willy
    "Z7": _zone("設定與狀態"),
    "Z8": _zone("總資料夾第一層"),
    "Z9": _zone("登記的來源"),
    "Z10": {"name": "排除的來源", "read": ("deny", "excluded", EXCLUDED_REASON), "write": ("deny", "excluded", EXCLUDED_REASON)},
    "Z10b": {"name": "範圍含排除夾", "read": ("deny", "excluded_inside", EXCLUDED_INSIDE_REASON),
             "write": ("deny", "excluded_inside", EXCLUDED_INSIDE_REASON)},
    "Z11": {"name": "其他", "read": ("pass", "", ""), "write": ("pass", "", "")},
    "Z13": {"name": "skill", "read": ("pass", "", ""), "write": ("pass", "", "")},  # D-538：照 Z11
}
VAULT_ZONES = ("Z1", "Z2", "Z4", "Z5", "Z6", "Z7", "Z8", "Z13")
SOURCE_ZONES = ("Z9", "Z10", "Z10b")
CREDENTIAL_REASON = "檔名像密碼或金鑰（{seg}），使用者沒說過這份可以讀，不讀也不寫。"
CREDENTIAL_INSIDE_REASON = "這個範圍裡有檔名像密碼或金鑰的檔（{rel}），使用者沒說過可以讀；縮到不含它的子資料夾或指定單一檔。"
CREDENTIAL_LIMIT_REASON = "範圍太大，守門來不及檢查有沒有密碼檔，縮小範圍再做。"


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
        # 跑什麼看不出來（測試 28 第四輪）：Bash 的動詞本身是變數（cmd='rm'; $cmd T），或殼的程式碼是間接展開（eval "${!v}"）
        opaque = (not powershell and verb.startswith("$")) or (
            runs in cmdparse.SHELL_WRAPPERS and "${!" in text + "\n" + code)
        bare = cmdparse.outside_quotes(cmdparse.strip_marks(text), powershell)
        out.append({"sub": sub, "stage": stage, "text": text, "verb": verb, "words": words, "args": args, "code": code,
                    "runs": runs, "var_code": var_code, "opaque": opaque,
                    "kind": cmdparse.stage_kind(runs, cmdparse.strip_marks(text), code, args, powershell),
                    "redirect": bare is None or ">" in bare, "marks": cmdparse.data_marks(text)})
    return out


_VALUE_PARAM = re.compile(r"-(?:value|inputobject)\s*:?\s*$", re.IGNORECASE)


# Set-Content／Add-Content：第一個位置參數是 -Path、第二個是 -Value；這些具名參數後面跟一個值（測試 28 第四輪 R4）
_CONTENT_CMDLETS = {"set-content", "sc", "add-content", "ac"}
_CONTENT_VALUED = {"-path", "-literalpath", "-pspath", "-lp", "-value", "-encoding", "-filter", "-include", "-exclude",
                   "-credential", "-stream", "-delimiter"}


def _content_value(before):
    """PowerShell 的 Set-Content／Add-Content：before 是替換之前的字，替換落在第二個以後的位置參數（-Value）就回 True；
    落在第一個位置參數、-Path 這類後面，或認不出來回 False（照目標算）。"""
    words = [t for t, _q, _g, _a in _tokens_q(before, globs_in_quotes=True)]
    if not words or cmdparse.verb_of(words) not in _CONTENT_CMDLETS:
        return False
    rest = words[next(k for k, w in enumerate(words) if cmdparse._base(w) in _CONTENT_CMDLETS) + 1:]
    positional, path_named, k = 0, False, 0
    while k < len(rest):
        w = rest[k].lower()
        if w.startswith("-"):
            name = w.split(":", 1)[0]
            path_named = path_named or name in ("-path", "-literalpath", "-pspath", "-lp")
            if name in _CONTENT_VALUED and ":" not in w:
                if k + 1 == len(rest):
                    return name == "-value"  # 替換緊接在這個具名參數後面
                k += 2
                continue
            k += 1
            continue
        positional += 1
        k += 1
    return path_named or positional >= 1  # -Path 已經具名給了，位置參數就綁到 -Value


# PowerShell 的 .方法(、::方法(（括號緊接在方法名後面）是方法參數：只認 .NET 檔案型別的靜態呼叫與 Office 帶路徑的另存，
# 別的方法（Remove-Item $p.Replace('a','b')）照舊當看不出目標（code review：全部放掉會讓變數方法算出來的目標變成放行）
_METHOD_CALL = re.compile(r"(?:" + cmdparse._NET_TYPE + r"\s*::\s*\w+|\." + cmdparse.OFFICE_SAVE_METHODS + r")$", re.IGNORECASE)


def _substituted_args(text, runs, powershell):
    """寫入那段的參數是指令替換（$(…)、反引號、<(…)；PowerShell 的 (…)、$(…)、@(…)）：目標推不出來（測試 28 N6）。
    替換本身另外遞迴判；這裡把替換換成一個佔位字後這段還是寫，才算目標來自替換（kb.py version @(Remove-Item x) 的寫在替換裡，不算）。
    PowerShell 的 {…}、@{…} 不是參數值；接在 -Value、-InputObject 後面的是要寫的內容，不是目標。拆不出來也算。"""
    try:
        subs = cmdparse.substitutions(text, powershell)
    except cmdparse.Unbalanced:
        return True
    spans, seen = [], []
    for pos, inner in subs:
        end = text.find(inner, pos) + len(inner) + 1
        if any(a <= pos < b for a, b in seen):
            continue  # 包在外層裡的（-Value ((Get-Date).ToString('o')) 的 (Get-Date)）跟著外層算
        seen.append((pos, end))
        if powershell and not (text.startswith(("$(", "@("), pos) or text[pos] == "("):
            continue
        if powershell and text[pos] == "(" and _METHOD_CALL.search(text[:pos]):
            continue  # .方法(、::方法( 的括號是方法參數，字面路徑留成目標（守門補洞 #25）
        if powershell and (_VALUE_PARAM.search(text[:pos]) or _content_value(text[:pos])):
            continue
        spans.append((pos, end))
    if not spans:
        return False
    blanked, last = [], 0
    for a, b in sorted(spans):
        blanked.append(text[last:a] + " KBSUBST ")
        last = b
    blanked.append(text[last:])
    return cmdparse.stage_kind(runs, "".join(blanked), "", None, powershell) == "write"


# 列檔名後面接的只處理文字的管線（桌面版確認 #10、#11，D-185）：不讀檔案內容，整句照只列檔名放行
TEXT_FILTERS = {"Bash": {"head", "tail", "sort", "wc", "grep", "egrep", "fgrep", "uniq", "cut"},
                "PowerShell": {"select-object", "select", "sort-object", "sort", "measure-object", "measure", "where-object",
                               "where", "?"}}
# 白名單（28 第七輪）：filter 段任何地方出現 shell 會展開、跳脫或導向輸入的字就不算（網銀密碼.tx?、$'…'、~、\、< 檔）
_FILTER_SHELL_CHARS = set("?[]*{}~$\\`<")
_FLAG_NUMBER = re.compile(r"^[+-]?\d+(?:[,-]\d*)*$")
# 每個 filter 准的旗標（28 第七輪 H1 補）：(不帶值的字母, {帶值的字母: 值的種類})；長旗標一律不收。
# 值的種類：num 數字或數字清單（1-3,5）、char 一個字、pattern grep 的樣式
_FILTER_FLAGS = {
    "head": ("", {"n": "num", "c": "num"}), "tail": ("", {"n": "num", "c": "num"}),
    "sort": ("rnuf", {"k": "num", "t": "char"}), "wc": ("lcwm", {}), "cut": ("", {"d": "char", "f": "num", "c": "num"}),
    "uniq": ("cdui", {}), "grep": ("ivcnEFwohlqx", {"e": "pattern"}),
}
_FILTER_FLAGS["egrep"] = _FILTER_FLAGS["fgrep"] = _FILTER_FLAGS["grep"]


def _flag_value_ok(kind, value):
    return (kind == "pattern" and value != "") or (kind == "char" and len(value) == 1) or (
        kind == "num" and bool(_FLAG_NUMBER.match(value)))


def _text_filter(plan, cwd, tool):
    """管線後段只處理前一段的文字，不讀任何檔（桌面版確認 #10、#11；28 第七輪改白名單）：
    Bash：只收 _FILTER_FLAGS 列的旗標與它們的值，加 grep 的一個樣式（-e 給了就沒有）；head、tail 另收 -N。
    任何地方有 ? [ ] * { } ~ $ 反斜線 反引號 <（輸入導向）就不算；長旗標、其他旗標、其他非旗標參數（含 cd 後的相對路徑）都不算。
    PowerShell：出現 {（腳本區塊）、反引號、括號、[、::、<，或引號外有 ? * 就不算；不帶區塊的屬性名稱照放。
    兩邊的動詞都要是打出來的原樣名稱（不帶副檔名、路徑、& 或 .\\）。"""
    verb, text = plan["verb"], cmdparse.strip_marks(plan["text"])
    if plan["kind"] != "read" or verb not in TEXT_FILTERS.get(tool, ()) or plan["marks"]:
        return False
    # 動詞要是打出來的原樣名稱（28 第八輪 H3）：sort.exe、C:\…\sort.exe、.\sort.exe、& sort、/usr/bin/head、./grep
    # 是別的程式（Windows 的 sort.exe 會展開 ? 讀檔），不算 filter
    lead = text.lstrip()
    first = lead.split(None, 1)[0] if lead else ""
    if lead.startswith(("&", ".")) or first.lower() != verb:
        return False
    if tool == "PowerShell":
        bare = cmdparse.outside_quotes(text, True)
        if bare is None or any(ch in text for ch in "{`<") or any(x in bare for x in ("(", "[", "::")):
            return False
        return not any(ch in bare.lstrip()[len(first):] for ch in "?*")  # 引號外的萬用字元（-like '*.pdf' 在引號裡不算）
    if any(ch in _FILTER_SHELL_CHARS for ch in text):
        return False
    tokens = _tokens_q(text)
    idx = next((k for k, (t, _q, _g, _a) in enumerate(tokens) if cmdparse._base(t) == verb), None)
    if idx is None:
        return False
    plain, valued = _FILTER_FLAGS[verb]
    grep = verb in ("grep", "egrep", "fgrep")
    pattern_given, positional, expect = False, 0, None
    for tok, _quoted, _g, _a in tokens[idx + 1:]:
        if expect is not None:
            if not _flag_value_ok(expect, tok):
                return False
            expect = None
            continue
        if tok.startswith("--"):
            return False
        if tok.startswith("-") and len(tok) > 1:
            letters = tok[1:]
            if verb in ("head", "tail") and letters.isdigit():
                continue  # head -5
            for k, c in enumerate(letters):
                if c in plain:
                    continue
                if c not in valued:
                    return False
                pattern_given = pattern_given or valued[c] == "pattern"
                rest = letters[k + 1:]
                if rest:
                    if not _flag_value_ok(valued[c], rest):
                        return False  # 值黏在旗標後（-c1-3、-d,）
                else:
                    expect = valued[c]
                break
            continue
        positional += 1
    return expect is None and positional <= (1 if grep and not pattern_given else 0)


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
    body_reads：內文裡的絕對路徑（照讀判斷）；token：第二道命中的段（只在效用 cwd 不在總資料夾裡、或推不出效用 cwd 時比）；
    open_exempt：開檔段（D-514）要開的文件與夾，別段沒碰、不是寫入目標也不是 cd 目標的（不走第三道、不算 Z10b）。
    效用 cwd 跨管線段、跨子指令延續。"""
    cleaned = cmdparse.DISCARD_OUTPUT.sub(" ", command)
    base = normalize(cwd, None)
    out = {"read_dirs": [], "implicit_dirs": [], "read_paths": set(), "write_targets": set(), "write_globs": [],
           "globs": [], "cd_dirs": [], "cd_targets": set(), "token": None, "unparsed": False, "has_write": False,
           "list_only": False, "body_reads": [], "stages": [], "open_exempt": set()}
    open_targets, other_paths = set(), set()
    plans = _plan_stages(cleaned, bodies, tool == "PowerShell")
    out["stages"] = plans
    out["list_only"] = bool(plans) and not bodies and all(
        not p["redirect"] and (cmdparse.list_only(p["verb"], p["words"]) if p["stage"] == 0 else _text_filter(p, cwd, tool))
        for p in plans)
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
        interp = plan["runs"] in cmdparse.INTERPRETERS or plan["runs"] in cmdparse.SHELL_WRAPPERS  # 放寬甲 U6：程式碼另判
        plan["interp"] = interp
        if plan["opaque"]:
            out["has_write"] = out["unparsed"] = True  # 跑的是什麼看不出來：當成寫到不明的地方
            plan["unknown"] = True
        if stage_write and plan["stage"] > 0 and (tool == "PowerShell" or verb in cmdparse.PIPE_ARG_VERBS) \
                and verb not in cmdparse.CONTENT_SINKS:
            # 目標從前一段的輸出來（| xargs rm、| Remove-Item、| % { rm $_ }、kb … | xargs rm -rf）：推不出會寫到哪，一律擋，
            # 前一段是 kb 也一樣（測試 28 N2）。| Set-Content x 這類寫的是內容、目標是點名的檔，照一般判
            out["unparsed"] = True
            plan["unknown"] = True
        if stage_write and (verb in cmdparse.PIPE_ARG_VERBS or _substituted_args(stage_text, plan["runs"], tool == "PowerShell")):
            # 目標來自指令替換或檔案清單（rm -rf $(kb … folders find …)、rm $(cat list)、xargs -a list rm）：跟 N2 同一條（測試 28 N6）
            out["unparsed"] = True
            plan["unknown"] = True
        switch_ok = tool == "PowerShell" or verb in cmdparse.SWITCH_VERBS  # X3
        stage_base = base
        opened = cmdparse.open_stage(verb, cmdparse.raw_words(stage_text, tool == "PowerShell"), tool == "PowerShell",
                                     stage_base or cwd)
        plan["open"] = opened
        open_kind = opened is not None and opened["kind"] == "open"
        if opened is not None and (opened["opaque"] or (tool == "PowerShell" and plan["stage"] > 0) or (
                stage_base is None and any(not cmdparse.OPEN_URL.match(t) and not cmdparse._ABS_START.match(t)
                                           for t in opened["targets"] + opened["writes"] + opened["reads"]))):
            # 要開的、程式的參數、導向有變數或替換，要開的從管線前面來（gci x | ii），或 cd $X 之後的相對路徑：看不出會開什麼
            out["has_write"] = out["unparsed"] = True
            plan["unknown"] = True
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
        if opened is not None:
            # 開檔段（D-514）：寫入目標只有導向；要開的文件與夾、-RedirectStandardInput 照讀
            own = [p for p in (_norm(raw, stage_base) for raw in opened["writes"]) if p is not None]
            for raw in opened["reads"] + (opened["targets"] if open_kind else []):
                p = None if cmdparse.OPEN_URL.match(raw) else _norm(raw, stage_base)
                if p is not None:
                    out["read_paths"].add(p)
                    stage_reads.append(p)
                    if open_kind and raw in opened["targets"]:
                        open_targets.add(p)
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
                if not open_kind:  # explorer .：開一個夾不是讀整夾
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
            if os.path.isdir(p) and not open_kind:
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
        if not open_kind:
            other_paths.update(own + stage_reads + redirect_targets + [_pattern_head(g) for g in stage_globs])
        sub_paths.setdefault(plan["sub"], []).extend(own + stage_reads)
        sub_globs.setdefault(plan["sub"], []).extend(stage_globs)
        if stage_write:
            if own or stage_globs:
                out["write_targets"].update(own)
                out["write_globs"].extend(stage_globs)
            elif not interp:
                sub_write_empty.setdefault(plan["sub"], True)
                plan["unknown"] = True
        elif redirect_targets:
            pass
        if any(any(c in t for c in "$%`") for t, _q, _g, a in tokens if a):
            plan["unknown"] = True  # 導向目標帶變數
        if stage_write and not interp and cmdparse.unresolved_paths(stage_text, powershell=(tool == "PowerShell")):
            plan["unknown"] = True  # 路徑裡有展不開的變數
        plan["own"], plan["redirects"], plan["globs"], plan["reads"] = own, redirect_targets, stage_globs, stage_reads
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
    # 開夾的豁免只給同一句沒有別段讀到的夾：別段的讀取範圍含這個夾或在它底下，照一般規則走訪（測試第 1 輪裁定 9）
    others = other_paths | out["write_targets"] | out["cd_targets"] | set(out["read_dirs"] + out["implicit_dirs"])
    out["open_exempt"] = {t for t in open_targets if not any(inside(o, t) or inside(t, o) for o in others)}
    out["final_base"] = base  # 這段指令跑完時的效用 cwd（指令替換遞迴用）
    return out


def _original_segment(target, seg):
    """理由句印原始大小寫的那一段：normalize 在 Windows 會小寫，從 payload 原字串找回來。"""
    for part in str(target).replace("\\", "/").split("/"):
        if part.lower() == seg.lower():
            return part
    return seg


KB_CALL_REASON = "知識庫腳本"
# D-212：開新口子的密碼動作要客戶按允許（PreToolUse 回 ask）；理由句只用名稱、檔名，不帶密碼值
SECRET_ACTION = "secret_action"
SECRET_COMMANDS = ("unlock", "ruling", "convert")
ASK_GET = "要把存好的「{}」密碼顯示出來"
ASK_READ = "要讓 AI 讀「{}」"
ASK_READ_ANY = "要讓 AI 讀一份檔名像密碼的檔"
ASK_AUTHORIZE = "要讓「{}」這一種的檔都用「{}」這組密碼自動開"
ASK_EXPLICIT = "要用存好的「{}」密碼開「{}」這一份"
ASK_UNKNOWN = "認不出這個知識庫指令要做什麼，可能是密碼相關的動作；請確認再按允許"
ASK_PREFIX = "需要客戶按允許才會執行："
NO_SUCH_FILE = "no_such_file"
NO_SUCH_FILE_REASON = "找不到這份檔"
_FILE_ID = re.compile(r"^f-[0-9a-f]{8}$")
CURLY_REASON = "PowerShell 會把彎引號（“ ” ‘ ’）當成引號拆開指令，這次沒放行：把指令裡的彎引號換成「」或直引號，或改用 Bash 再跑一次"
ERROR_REASON = "守門出錯，這次沒放行，已記在 hook-errors.log：改跑 kb selftest 看原因，修不好照核心規則請服務人員。"


def find_vault(cwd):
    """從 cwd 往上找第一個含 .system/sources.json（或來源流水檔夾）的目錄；找不到回 None（不是在知識庫裡開的對話）。
    新 clone 只有流水檔、沒有 .gitignore 擋掉的衍生副本：先從流水檔補出副本再認（#39）。"""
    if not cwd:
        return None
    try:
        p = Path(normalize(cwd, None))
    except (OSError, ValueError):
        return None
    for d in [p, *p.parents]:
        if (d / sources_lib.SOURCES_REL).is_file():
            return d
        from kb._lib import streams
        if streams.exists(d, "sources"):  # 空的流水檔夾不算
            try:  # load 快取命中時不寫副本，所以直接寫
                streams._mirror(d, "sources", streams.load(d, "sources"))
            except Exception:  # noqa: BLE001  補不出來照樣認：之後讀登記表的地方照舊報讀不了
                pass
            return d
    return None


OUTER_DEPTH = 2  # 上層 vault 往上找幾層（同 kb._lib.obsidian.OUTER_DEPTH）
OUTER_PLUGIN_REL = ".obsidian/plugins/wiki-with-willy"


def _outer_plugin_of(vault):
    cur = vault.rstrip("/")
    for _ in range(OUTER_DEPTH):
        if "/" not in cur:
            return None
        cur = cur.rsplit("/", 1)[0]
        if os.path.isdir(cur + "/.obsidian"):
            return normalize(cur + "/" + OUTER_PLUGIN_REL, None)
    return None


_OUTER_SEEN = None  # decide 進行中是 {vault: [外掛夾]}（同一次判斷找過的重用，第 11 條）；平常是 None：直接叫 classify 的永遠現找


def outer_plugin_dirs(vault):
    """上層 vault 那份外掛夾（#66 丙，D-545）：vault 已 normalize；從知識庫根往上最多兩層、最近一個有 .obsidian/ 資料夾的夾底下的
    .obsidian/plugins/wiki-with-willy（照 normalize 在 Windows 的小寫）。知識庫路徑照字面找一次、realpath 後再找一次（kb 放外掛用的是
    realpath；路徑中間有 junction／symlink 時兩邊不同，裁定 2），回找到的（0～2 個，不重複）。每次判斷現找一次（一次 realpath、
    最多四次 isdir），同一次判斷裡重用，不寫狀態檔。"""
    if _OUTER_SEEN is not None and vault in _OUTER_SEEN:
        return _OUTER_SEEN[vault]
    out = []
    try:
        real = normalize(os.path.realpath(vault), None)
    except (OSError, ValueError):
        real = vault
    for v in dict.fromkeys((vault, real)):
        got = _outer_plugin_of(v)
        if got is not None and got not in out:
            out.append(got)
    if _OUTER_SEEN is not None:
        _OUTER_SEEN[vault] = out
    return out


def _in_outer_plugin(path, vault):
    """path 是上層 vault 那份外掛夾本身或底下，或正好是它上面那兩個夾本身（<上>/.obsidian/plugins、<上>/.obsidian）。
    那兩個夾底下其他的檔（app.json、別的外掛）不算（#66 丙、裁定 7：整夾刪掉、改名、換掉，main.js 就沒了）。"""
    for outer in outer_plugin_dirs(vault):
        plugins = outer.rsplit("/", 1)[0]
        if inside(path, outer) or path == plugins or path == plugins.rsplit("/", 1)[0]:
            return True
    return False


def classify(path, vault, sources):
    """path、vault 都已 normalize。回 (zone, alias)；alias 只有 Z9／Z10／Z10b 有。
    知識庫外只多認上層 vault 那份外掛：外掛夾本身與底下、它上面那兩個夾本身照 Z7（Obsidian 開啟時會執行的程式，#66 丙）。"""
    if inside(path, vault):
        if path == vault:
            return "Z8", None
        if getattr(sources, "vault_excludes", None):  # 放寬甲一.6：知識庫裡使用者設成不讀的夾，先於第一層名字判
            if sources.vault_excluded(path):
                return "Z10", "知識庫"
            if sources.vault_excluded_under(path):
                return "Z10b", "知識庫"
        parts = path[len(vault) + 1:].split("/")
        first = parts[0]
        if first == ".claude" and len(parts) > 2 and parts[1] == "skills" and in_skills(path, vault):
            return "Z13", None
        if first in RESERVED:
            return RESERVED[first], None
        if len(parts) == 1:
            return ("Z7" if first == "claude.md" else "Z8"), None
        if len(parts) == 2 and os.path.isfile(path):
            return "Z8", None  # 型別夾根直接放的檔
        if len(parts) == 3 and parts[2] == parts[1] + ".md":
            return "Z1", None
        return "Z2", None
    if _in_outer_plugin(path, vault):
        return "Z7", None
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
    elif decision == "deny" and "{alias}" in reason:
        reason = reason.replace("{alias}", alias or "")
    return decision, code, reason


def _profile_path(vault_n):
    return vault_n.rstrip("/") + "/" + PROFILE_MD


def is_willy_file(zone, path, vault_n):
    """這個路徑是 willy 自己的檔（不給檔案工具寫以外的人改）：Z7、Z6 除客戶設定檔、知識庫根本身與總目錄（Z8）。
    Z1 綜合卡、Z5 檔案索引檔案工具可以寫，但指令寫仍是 willy（_willy_shell_zone）。"""
    if zone == "Z7":
        return True
    if zone == "Z6":
        return path != _profile_path(vault_n)
    if zone == "Z8":
        return path == vault_n or path == vault_n.rstrip("/") + "/" + INDEX_MD
    return False


def _willy_shell_zone(zone, path, vault_n):
    """指令寫這個路徑一律 willy：is_willy_file 之外加 Z1 綜合卡、Z5 檔案索引、客戶設定檔（指令寫也不給，教用 Edit）。"""
    return zone in WILLY_ZONES or is_willy_file(zone, path, vault_n)


def willy_hint(zone, path, vault_n):
    if zone in ("Z1", "Z5"):
        return HINT_EDIT
    if zone == "Z6" and path == _profile_path(vault_n):
        return HINT_PROFILE
    if zone == "Z7":
        return HINT_KB
    return HINT_RENDERED


def _display_name(text, path):
    """理由句印的名字：從原字串找回原始大小寫（normalize 在 Windows 會小寫），找不到用路徑最後一段。"""
    last = path.rstrip("/").rsplit("/", 1)[-1]
    m = re.search(re.escape(last), str(text), re.IGNORECASE) if last else None
    return m.group(0) if m else last


def willy_reason(zone, path, vault_n, text):
    return WILLY_REASON.format(name=_display_name(text, path), hint=willy_hint(zone, path, vault_n))


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


def in_skills(path, vault_n):
    """normalize 過的 path 在 .claude/skills/ 底下（不含 skills 夾本身），做法照 in_workspace：字串前綴（新庫還沒有夾也算）；
    skills 夾已存在時，磁碟上存在的部分 realpath 後還要在裡面（連結、junction 跳出去的不算）；還不存在時，存在的最近上層不能被連結導走。"""
    root = vault_n.rstrip("/") + "/" + SKILLS_POSIX
    if path == root or not inside(path, root):
        return False
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
    if zone == "Z13" and _odd_segments(target):
        zone = "Z7"  # 中間段結尾是空白或點：Windows 會建出 skills 以外的夾，照 Z7（同工作區）
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
    if column == "write" and is_willy_file(zone, path, vault_n):
        decision, code, reason = "deny", "willy", willy_reason(zone, path, vault_n, target)
    rows = []
    if decision == "deny":
        rows = [_row(tool, "deny", zone, code, target)]
    elif zone == "Z11" and sources.unresolved:
        rows = _unresolved_rows(tool, sources, target)
    return Decision(decision, zone, code, reason, rows, vault)


# 目的地用旗標指定的寫法（T-11）：-t／--target-directory 只認小寫 -t（cp 的大寫 -T 是「不當夾」，意思相反）；-Destination 不分大小寫、可縮寫
_DEST_FLAG = re.compile(r"""(?:^|\s)(?:-[A-Za-z]*t|--target-directory|(?i:-Dest\w*))(?:=|\s+)("[^"]*"|'[^']*'|\S+)""")
# rsync 的 -t 是保留時間、不是目的地；rsync 只認 --target-directory 以外沒有目的地旗標（N-8）：只有 cp、mv 這類才把 -t 當目的地
_T_FLAG_VERBS = {"cp", "mv", "copy", "move", "install", "ln"}
_DEST_FLAG_LONG = re.compile(r"""(?:^|\s)(?:--target-directory|(?i:-Dest\w*))(?:=|\s+)("[^"]*"|'[^']*'|\S+)""")
# PowerShell 的參數不是短選項併寫（-Path、-Force 結尾的 t 不算 -t）：只認 -Destination
_DEST_FLAG_PS = re.compile(r"""(?:^|\s)(?i:-Dest\w*)(?:=|\s+)("[^"]*"|'[^']*'|\S+)""")


def _destination_by_flag(text, cwd, powershell=False):
    """寫法裡用旗標指定了目的地就回它（正規化）；沒有回 None。-t 可以併在短選項裡（cp -rt、cp -at，N-2），大寫 -T 意思相反不算。"""
    verb = cmdparse.verb_of(text.split())
    pattern = _DEST_FLAG_PS if powershell else (_DEST_FLAG if verb in _T_FLAG_VERBS else _DEST_FLAG_LONG)
    m = pattern.search(text)
    if not m:
        return None
    return normalize(m.group(1).strip("\"'"), cwd)


def _moves_root(flat, cwd, vault_n, powershell):
    """搬或複製的那段，總資料夾本身是來源（不是目的地，也就不是「放進總資料夾」）：mv／Move-Item／cp -r／robocopy 整個知識庫。
    目的地用旗標指定（mv -t、cp --target-directory、-Destination）時，其餘路徑都是來源（來源可以排在最後，T-11）；
    沒有旗標時最後一個路徑是目的地。目的地是總資料夾（cp a.txt .）不算；cd 之後才寫的 . 用原本的 cwd 算，
    看不出來就當不是（理由句退回 Z8 那句，照樣擋）。"""
    for _sub, _stage, text in _stages(flat, powershell):
        if cmdparse.verb_of(text.split()) in MOVE_COPY_VERBS:
            paths = cmdparse.extract_paths(text, cwd, powershell=powershell)
            dest = _destination_by_flag(text, cwd, powershell)
            if dest is not None:
                sources = list(paths)
                if dest in sources:
                    sources.remove(dest)
            else:
                sources = paths[:-1]
            if vault_n in sources:
                return True
    return False


def _hidden_exclude_under(sources, path_n):
    """path 底下（含自己）有沒有「不想讓 AI 看到」的排除（kind hidden，不管清完了沒；沒寫 kind 當 hidden，跟 sources 讀法一致）。"""
    return any(ex.get("kind", "hidden") == "hidden" and inside(ex["root"], path_n)
               for src in sources.entries for ex in src["excludes"])


def _single_filename(pattern):
    """Glob 樣式是單一檔名：沒有 *?[{、沒有目錄段（/ \\ : ..）、不是 . 或 ..；這種樣式只在 path 那一層找（B-425）。"""
    if not isinstance(pattern, str) or not pattern.strip() or pattern.strip() in (".", ".."):
        return False
    return not any(ch in pattern for ch in "*?[]{}/\\:")


def _pattern_head(pattern):
    """萬用字元前的固定前綴資料夾。"""
    m = re.search(r"[*?\[]", pattern)  # 大括號已在 cmdparse.brace_expand 展開（A1）
    head = pattern[:m.start()] if m else pattern
    return head.rsplit("/", 1)[0] if "/" in head else head


def above_willy(path, vault_n):
    """path 是知識庫的上層，或上層 vault 那份外掛夾的上層（<上>/.obsidian、<上>/.obsidian/plugins 與更上面）：整夾刪掉、改名、搬走，
    裡面 willy 的檔跟著走。path、vault_n 都已 normalize。_ancestor_zone 與 kb source-edit（#66 丙裁定 10）共用。"""
    return inside(vault_n, path) or any(inside(outer, path) for outer in outer_plugin_dirs(vault_n))


def _ancestor_zone(path, vault_n, sources):
    """path 是不是某個來源根或總資料夾的上層（rm -rf 整個上層）。是就回它會動到的區。排除夾的上層由 classify 的 Z10b 處理。
    上層 vault 那份外掛夾的上層（<上>/.obsidian、<上>/.obsidian/plugins）也算：整夾換掉或刪掉，main.js 就沒了（#66 丙裁定 1）。"""
    for src in sources.entries:
        if inside(src["root"], path):
            return "Z9", src["alias"]
    if above_willy(path, vault_n):
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


def _program_file(raw, base):
    """開檔段要跑的程式檔（D-514）：帶路徑的（相對或絕對，存不存在都算）接效用 cwd；裸名稱只在效用 cwd 底下真的有這個檔
    （含補 .exe、.bat 這些）才算，沒有就是從 PATH 找的系統程式（powershell、cmd）。網址回 None。"""
    if cmdparse.OPEN_URL.match(raw) or cmdparse._URL.match(raw):
        return None
    if "/" in raw or "\\" in raw or cmdparse._ABS_START.match(raw):
        return _norm(raw, base)
    if base is None:
        return None
    for ext in _RUN_EXT:
        try:
            p = normalize(raw + ext, base)
            if os.path.isfile(p):
                return p
        except (OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------------------------------------------
# 放寬甲：使用者區規則（U1～U6，D-306）。指令在使用者的區（USER_ZONES）建、搬、改名、複製到沒有同名的地方、刪空夾一律放行；
# 只有救不回來的才擋：刪檔與有東西的夾（delete）、蓋掉同名檔（overwrite）、改既有檔內容（content）、看不出目標的批次搬（batch）、
# 直譯器程式碼的刪檔與覆蓋寫法（code）。判斷用指令的動詞與目的地現況，不追蹤執行時才發生的事。
_DELETE_VERBS = {"rm", "del", "erase", "ri", "remove-item", "unlink", "shred", "rd", "rmdir"}
_MOVE_VERBS = {"mv", "move", "move-item", "mi"}
_COPY_VERBS = {"cp", "copy", "copy-item", "cpi", "robocopy", "xcopy", "rsync", "install"}
_CONTENT_VERBS = {"set-content", "sc", "add-content", "ac", "out-file", "tee", "tee-object", "truncate", "dd", "export-csv",
                  "export-clixml", "clear-content", "clc", "epcsv"}  # clc、epcsv：別名照全名（守門補洞 #25）
_PASS_VERBS = {"mkdir", "md", "touch", "ren", "rename", "rename-item", "rni", "ln", "chmod", "chown", "attrib", "icacls", "cd",
               "set-itemproperty", "sp", "new-item", "ni", "pushd", "popd", "set-location", "sl", "chdir"}
_KNOWN_VERBS = _DELETE_VERBS | _MOVE_VERBS | _COPY_VERBS | _CONTENT_VERBS | _PASS_VERBS | {"sed", "perl", "git", "find"}
_SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "{", "}", "(", ")", "!", "foreach", "for"}
_NO_CLOBBER = {"-n", "--no-clobber", "--ignore-existing"}
_PS_VALUED = {"-path", "-literalpath", "-pspath", "-lp", "-destination", "-include", "-exclude", "-filter", "-credential", "-stream",
              "-newname", "-value", "-encoding"}
_ENTRIES_MAX = 2000
UNPARSED_REASON = "指令拆不開（引號或括號沒配好，或巢狀太深）：改寫成簡單的指令再跑。" + AI_TAIL


def _eff_verb(plan):
    """這一段實際的動詞（跳過 do、then 這類殼的關鍵字與 xargs、find -exec 這類包裝）：回 (動詞, 它在 args 裡的位置)。"""
    words = plan["args"] or plan["words"]
    for i, w in enumerate(words):
        b = cmdparse._base(w)
        if b in _SHELL_KEYWORDS:
            continue
        if b in _KNOWN_VERBS:
            if b == "git":
                nxt = next((cmdparse._base(x) for x in words[i + 1:] if not x.startswith("-")), "")
                return "git " + nxt, i
            return b, i
    return plan["verb"], 0


def _is_empty_dir(p):
    try:
        with os.scandir(p) as it:
            return next(it, None) is None
    except OSError:
        return False


def _exists_with_content(p):
    """刪了會丟東西：存在的檔、有東西的夾。空夾與不存在的不算。"""
    if os.path.isdir(p):
        return not _is_empty_dir(p)
    return os.path.lexists(p)


def _collides(src, dst):
    """夾對夾複製會蓋到同名檔：src 底下有檔在 dst 對應位置已經有檔（最多看 _ENTRIES_MAX 項，超過當有）。"""
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        for name in files:
            n += 1
            if n > _ENTRIES_MAX:
                return True
            if os.path.isfile(os.path.join(dst, rel, name)):
                return True
    return False


def _user_zone_of(p, vault_n, sources):
    """p 落在使用者的區回區名；ANC 是來源或知識庫的上層夾；其他回 None。"""
    zone, _alias = classify(p, vault_n, sources)
    if zone in USER_ZONES:
        return zone
    if zone == "Z11" and _ancestor_zone(p, vault_n, sources)[0]:
        return "ANC"
    return None


class _U5:
    """看不出目標：之後看整句有沒有碰到使用者區再決定（_check_plan 的回傳，Decision 本身也是 tuple，所以另用一個型別）。"""

    def __init__(self, what):
        self.what = what


class _Ctx:
    """_user_rules 一次判斷要用的東西。"""

    def __init__(self, tool, command, vault, vault_n, sources, rows):
        self.tool, self.command, self.vault, self.vault_n, self.sources = tool, command, vault, vault_n, sources
        self.powershell = tool == "PowerShell"
        self.rows = rows

    def deny(self, zone, code, reason):
        return Decision("deny", zone, code, reason, [_row(self.tool, "deny", zone, code, self.command)], self.vault)

    def delete(self, p, zone):
        return self.deny(zone, "delete", DELETE_REASON.format(name=_display_name(self.command, p)))

    def content(self, p, zone):
        return self.deny(zone, "content", CONTENT_REASON.format(name=_display_name(self.command, p)))

    def overwrite(self, dest, target, zone):
        return self.deny(zone, "overwrite", OVERWRITE_REASON.format(dest=_display_name(self.command, dest),
                                                                     name=_display_name(self.command, target)))


def _resolved(raw, base):
    """一個路徑字（可含萬用字元）展開成 [路徑]；帶變數或替換回 None（看不出）；萬用字元展不出東西回 []。"""
    if not raw or any(c in raw for c in "$%`"):
        return None
    p = _norm(raw, base)
    if p is None:
        return None
    return _expand_glob(p, hidden=True) if cmdparse.is_wildcard(raw) else [p]


def _mv_parse(plan, ctx, idx):
    """mv、cp、Move-Item、Copy-Item、robocopy、xcopy、rsync、install 的參數：回 (來源字清單, 目的地字或 None, 旗標小寫清單, force)。"""
    allw = plan["args"] or plan["words"]
    words = allw[idx + 1:]
    verb = cmdparse._base(allw[idx])
    flags, force, named, k = [], False, {}, 0
    if ctx.powershell and verb not in ("robocopy", "xcopy", "rsync"):
        positional = []
        while k < len(words):
            w = words[k]
            low = w.lower()
            if low.startswith("-") and len(low) > 1 and not low[1].isdigit():
                name, colon, val = low.partition(":")
                flags.append(name)
                if name in ("-force", "-f"):
                    force = (val not in ("$false", "false", "0")) if colon else True
                elif name in _PS_VALUED:
                    if colon and val:
                        named[name] = w.partition(":")[2]
                    elif k + 1 < len(words):
                        named[name] = words[k + 1]
                        k += 1
                k += 1
                continue
            positional.append(w)
            k += 1
        src = [named[n] for n in ("-path", "-literalpath", "-pspath", "-lp") if n in named]
        if not src and positional:
            src = [positional.pop(0)]
        dest = named.get("-destination") or (positional.pop(0) if positional else None)
        return src + positional, dest, flags, force
    pos, dest = [], None
    while k < len(words):
        w = words[k]
        if w == "--":
            pos += words[k + 1:]
            break
        if w.startswith("--"):
            name, eq, val = w.partition("=")
            flags.append(name.lower())
            if name == "--target-directory":
                dest = val if eq else (words[k + 1] if k + 1 < len(words) else None)
                k += 0 if eq else 1
            k += 1
            continue
        if verb in ("robocopy", "xcopy") and w.startswith("/") and len(w) > 1 and "/" not in w[1:] and not os.path.exists(w):
            flags.append(w.lower())
            k += 1
            continue
        if w.startswith("-") and len(w) > 1 and not w[1:].isdigit():
            flags.append(w)
            if "t" in w[1:] and verb in ("cp", "mv", "install", "move", "copy"):
                tail = w[1:].split("t", 1)[1]
                if tail:
                    dest = tail
                elif k + 1 < len(words):
                    dest = words[k + 1]
                    k += 1
            k += 1
            continue
        pos.append(w)
        k += 1
    if dest is None and pos and verb != "robocopy":
        dest = pos.pop()
    return pos, dest, flags, force


def _no_clobber(verb, flags, force, ctx):
    """帶了不覆蓋的旗標、或 Move-Item 沒加 -Force（同名只會報錯、不會蓋掉）。"""
    flat = {f.lower() for f in flags}
    if ctx.powershell and verb in _MOVE_VERBS:
        return not force
    if flat & _NO_CLOBBER:
        return True
    if verb in ("cp", "mv", "install", "move", "copy") and not ctx.powershell:  # PowerShell 的 -Destination 之類具名參數含 n，不是 -n
        for f in flags:
            if f.startswith("-") and not f.startswith("--") and "n" in f[1:]:
                return True
    if verb in ("xcopy", "move", "copy") and "/-y" in flat:
        return True
    if verb == "robocopy" and {"/xc", "/xn", "/xo"} <= flat:
        return True
    return False


def _move_copy_check(plan, ctx, verb, idx):
    """U3：搬或複製到已有同名檔的地方 → overwrite；看不出目標回 "unknown"（呼叫端照 U5 的 batch）。回 Decision、"unknown" 或 None。"""
    base = plan.get("base")
    sources, dest, flags, force = _mv_parse(plan, ctx, idx)
    flat = {f.lower() for f in flags}
    if verb == "robocopy" and flat & {"/mir", "/purge"} and len(sources) >= 2:
        for p in _resolved(sources[1], base) or []:  # 鏡像會刪目的地多出來的檔
            z = _user_zone_of(p, ctx.vault_n, ctx.sources)
            if z and _exists_with_content(p):
                return ctx.delete(p, z)
    if verb == "rsync" and any(f.startswith("--delete") for f in flat) and dest:
        for p in _resolved(dest, base) or []:
            z = _user_zone_of(p, ctx.vault_n, ctx.sources)
            if z and _exists_with_content(p):
                return ctx.delete(p, z)
    if _no_clobber(verb, flags, force, ctx):
        return None
    contents_only_all = False
    if verb == "robocopy" and len(sources) >= 2:  # robocopy <來源夾> <目的夾> [檔名…]
        sources, dest, contents_only_all = sources[:1], sources[1], True
    dests = _resolved(dest, base) if dest else None
    if dests is None or len(dests) != 1 or not sources:
        return "unknown"
    dest_p = dests[0]
    dest_is_dir = os.path.isdir(dest_p)
    for raw in sources:
        got = _resolved(raw, base)
        if got is None:
            return "unknown"
        contents_only = contents_only_all or (verb == "rsync" and raw.endswith(("/", "\\")))
        for src in got:
            if os.path.isdir(src) and contents_only:  # rsync 來源/ 與 robocopy：複製的是裡面的東西
                z = _user_zone_of(dest_p, ctx.vault_n, ctx.sources)
                if z and dest_is_dir and _collides(src, dest_p):
                    return ctx.overwrite(dest_p, os.path.basename(src.rstrip("/")), z)
                continue
            target = normalize(os.path.join(dest_p, os.path.basename(src)), None) if dest_is_dir else dest_p
            z = _user_zone_of(target, ctx.vault_n, ctx.sources)
            if z is None or not os.path.lexists(target):
                continue
            if os.path.isfile(target):
                return ctx.overwrite(os.path.dirname(target) if dest_is_dir else target, os.path.basename(target), z)
            if os.path.isdir(target) and os.path.isdir(src) and verb in _COPY_VERBS and _collides(src, target):
                return ctx.overwrite(os.path.dirname(target), os.path.basename(target), z)
    return None


def _target_paths(plan, ctx):
    """這一段的目標路徑（正規化）：從參數抽到的、萬用字元展開的。"""
    out = list(plan.get("own") or [])
    for g in plan.get("globs") or []:
        out.extend(_expand_glob(g, hidden=ctx.powershell))
    return list(dict.fromkeys(out))


def _u5_words(plan, ctx):
    """U5：看不出目標時整句找動詞。回 "delete"｜"content"｜"batch"｜None；plan 的文字加它的程式碼一起看。"""
    text = cmdparse.strip_marks(plan["text"]) + "\n" + (plan.get("code") or "")
    toks = _tokens_q(text, globs_in_quotes=ctx.powershell)
    words = [cmdparse._base(t) for t, _q, _g, _a in toks]
    low = text.lower()
    if any(w in _DELETE_VERBS for w in words) or re.search(r"(?:^|\s)-delete\b", low) or re.search(r"\bgit\s+(?:rm|clean)\b", low):
        return "delete"
    bare = cmdparse.outside_quotes(cmdparse.strip_marks(plan["text"]), ctx.powershell)
    if (any(w in _CONTENT_VERBS for w in words) or re.search(r"\bsed\s+(?:-[a-zA-Z]+\s+)*(?:-[a-zA-Z]*i|--in-place)", low)
            or re.search(r"\bperl\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*i", low) or cmdparse.REDIRECT.search(bare if bare is not None else ">")):
        return "content"
    movers = [w for w in words if w in _MOVE_VERBS | _COPY_VERBS]
    if movers:
        flags = [t for t, _q, _g, _a in toks if t.startswith("-")]
        verb = movers[0]
        if ctx.powershell and verb in _MOVE_VERBS:
            return "batch" if any(f.lower() == "-force" for f in flags) else None
        if _no_clobber(verb, flags, False, ctx):
            return None
        return "batch"
    return None


def _user_files(raw, base, ctx, anc=True):
    """一個字面路徑接效用 cwd 展開後，落在使用者區的 [(路徑, 區)]；anc=False 不收 ANC（來源或知識庫的上層夾）。"""
    out = []
    for p in (_resolved(raw, base) or []) if raw else []:
        z = _user_zone_of(p, ctx.vault_n, ctx.sources)
        if z and (anc or z != "ANC"):
            out.append((p, z))
    return out


def _file_call_check(calls, saves, base, ctx):
    """守門補洞 #25（D-348）：PowerShell 的 .NET 檔案呼叫與 Office 帶路徑的另存，照對應的全名寫法判。
    Delete → 照 Remove-Item（delete）；寫的方法、Office 另存 → 既有檔 content、新檔放行；Move、Copy → 覆蓋旗標 $true 時
    目的地已有檔 overwrite，沒帶旗標同名只會報錯，放行（照 Move-Item 不加 -Force）。參數不是字面路徑的看不出目標，照放寬甲放行（決策 6）。
    字面路徑是 willy 自己的檔照 willy（'<夾>', $true 的逗號會黏在抽出的路徑上，這裡照參數本身再判一次）。"""
    named = []  # 每個呼叫點名的路徑參數：Replace 三個、Move／Copy 前兩個、其餘第一個（WriteAllText 的第二個是內容，不是路徑）
    for action, args in calls:
        named += args if action == "replace" else args[:2] if action in ("move", "copy") else args[:1]
    for raw, is_save in [(cmdparse.literal_arg(a), False) for a in named] + [(s, True) for s in saves]:
        p = _norm(raw, base) if raw and not any(c in raw for c in "$%`") else None
        zone = classify(p, ctx.vault_n, ctx.sources)[0] if p is not None else None
        if is_save and p is not None and in_workspace(p, ctx.vault_n):
            continue  # 另存、匯出到工作區：寫入欄照裁定 11 判（_workspace_saves）
        if zone and _willy_shell_zone(zone, p, ctx.vault_n):
            return ctx.deny(zone, "willy", willy_reason(zone, p, ctx.vault_n, ctx.command))
    for action, args in calls:
        lits = [cmdparse.literal_arg(a) for a in args]
        if action == "delete":
            for p, z in _user_files(lits[0] if lits else None, base, ctx):
                if _exists_with_content(p):
                    return ctx.delete(p, z)
        elif action in ("write", "replace"):  # Replace(來源, 目的地, 備份)：三個都會動到；其餘的第一個參數是目標
            got = _existing_content(lits if action == "replace" else lits[:1], base, ctx)
            if got is not None:
                return got
        elif len(args) >= 3 and args[2].strip().lower() in ("$true", "1") and len(lits) >= 2:
            for p, z in _user_files(lits[1], base, ctx):
                if os.path.isfile(p):
                    return ctx.overwrite(os.path.dirname(p), os.path.basename(p), z)
    return _existing_content(saves, base, ctx)


def _workspace_saves(info, vault_n, powershell):
    """測試第 1 輪後裁定 11：只做另存、匯出、下載的那些段（PowerShell 的 Office 另存與 -OutFile、直譯器的 Office 另存）寫到工作區
    .system/temp/office/ 的目標：新檔照決策 3 放行、既有的照蓋掉判。同一句別段（刪、搬、Set-Content、導向…）也寫到的路徑不算（照舊 willy）。"""
    saves, blocked = set(), set()
    for plan in info["stages"]:
        text = cmdparse.strip_marks(plan["text"])
        raws = []
        code = plan.get("code") or ""
        if plan["kind"] == "write" and plan.get("interp") and not (cmdparse.WRITE_CODE.search(text) or cmdparse.WRITE_CODE.search(code)):
            raws = cmdparse.office_save_targets(text + "\n" + code)  # 程式碼裡除了另存沒有別的寫（os.rename、shutil… 不給豁免）
        elif (plan["kind"] == "write" and powershell and not plan.get("interp") and not cmdparse.file_calls(text)
              and plan["verb"] not in _KNOWN_VERBS):
            raws = cmdparse.office_save_targets(text) + cmdparse.outfile_targets(text, True)
        mine = {p for p in (_norm(r, plan.get("base")) for r in raws) if p is not None and in_workspace(p, vault_n)}
        saves |= mine
        others = list(plan.get("redirects") or []) + ([] if mine or plan["kind"] != "write" else list(plan.get("own") or []))
        blocked |= {o.rstrip(",") for o in others}
    return saves - blocked


def _targets_outside_calls(plan, ctx):
    """裁定 13：全名寫入動詞跟 .NET 呼叫或 Office 另存在同一段時，它的目標是 _target_paths 去掉只出現在那些呼叫括號裡的字
    （WriteAllText 的內容、Copy 的來源、只在括號裡的萬用字元或 [ ] 不是它的目標）；兩邊都有的照留；萬用字元照 _analyze 的切法（_tokens_q）比。逗號黏在路徑上的照拿掉比（B-542）。"""
    rest, removed = cmdparse.strip_file_calls(cmdparse.strip_marks(plan["text"]))
    anchor = plan.get("base") or ctx.vault_n  # 只拿來比對同一個路徑；效用 cwd 推不出來時 own 本來就只有絕對路徑

    def norm_set(text):
        return {p.rstrip(",") for p in cmdparse.extract_paths(text, anchor, powershell=True)}

    def glob_set(text):  # 跟 _analyze 收 plan["globs"] 同一套切法（_tokens_q），才對得上
        found = (_norm(t, plan.get("base")) for t, _q, g, _a in _tokens_q(text, globs_in_quotes=ctx.powershell) if g)
        return {p.rstrip(",") for p in found if p is not None}

    outside, inside = norm_set(rest), norm_set(removed)
    drop = {p for p in (plan.get("own") or []) if p.rstrip(",") in inside and p.rstrip(",") not in outside}
    out = [p for p in (plan.get("own") or []) if p not in drop]
    g_out, g_in = glob_set(rest), glob_set(removed)
    for g in plan.get("globs") or []:
        if g.rstrip(",") in g_in and g.rstrip(",") not in g_out:
            continue  # 只出現在呼叫括號裡的萬用字元（WriteAllText 的內容 '*'）不展開
        out.extend(_expand_glob(g, hidden=ctx.powershell))
    return list(dict.fromkeys(out))


def _existing_content(raws, base, ctx):
    """這些字面路徑裡第一個是使用者區既有檔的回 content；都不是回 None（Office 另存、寫的方法：新檔放行）。"""
    for raw in raws:
        for p, z in _user_files(raw, base, ctx, anc=False):
            if os.path.isfile(p):
                return ctx.content(p, z)
    return None


def _check_plan(plan, ctx):
    """一段指令照使用者區規則判。回 Decision、("u5", 動詞或 None)（看不出目標，之後看整句有沒有碰到使用者區）或 None。"""
    verb, idx = _eff_verb(plan)
    base = plan.get("base")
    # 導向（>、>>）到既有檔 → content；到新檔 → 放行
    for p in plan.get("redirects") or []:
        z = _user_zone_of(p, ctx.vault_n, ctx.sources)
        if z and os.path.isfile(p):
            return ctx.content(p, z)
    # .NET 檔案呼叫與 Office 另存（守門補洞 #25）：點名的字面路徑先判，這段別處看不出目標（$x = …、.Open('a').SaveAs2('b')）也一樣；
    # 沒擋的看不出目標照 U5，看得出的不再走下面的「其餘的寫」（WriteAllText 的內容、Copy 沒覆蓋旗標的來源不是要改的檔）
    # 這段的動詞是全名寫入動詞（Set-Content '<p>' ([IO.File]::WriteAllText(…))）時，判完 .NET 那部分照樣走下面（測試第 1 輪後裁定 13）
    # 這段的動詞看第一個字（plan["verb"]），不看 .NET 參數裡剛好是 copy、cp 的字串
    file_call, has_calls = False, False  # file_call：這段的寫只有 .NET 呼叫或 Office 另存，判完就不再走「其餘的寫」
    if ctx.powershell and plan["kind"] == "write":
        calls, saves = cmdparse.file_calls(plan["text"]), cmdparse.office_save_targets(plan["text"])
        has_calls = bool(calls or saves)
        file_call = has_calls and plan["verb"] not in _KNOWN_VERBS
        got = _file_call_check(calls, saves, base, ctx) if has_calls else None
        if got is not None:
            return got
    if plan.get("unknown"):
        return _U5(_u5_words(plan, ctx))
    if plan["kind"] != "write" or file_call:
        return None
    targets = _target_paths(plan, ctx) if not has_calls else _targets_outside_calls(plan, ctx)
    find_del = verb == "find" and re.search(r"(?:^|\s)(?:-delete\b|-(?:exec|execdir|ok|okdir)\s+(?:rm|unlink|shred)\b)", plan["text"])
    if verb in _DELETE_VERBS or verb in ("git rm", "git clean") or find_del:
        tg = targets or ([base] if verb == "git clean" and base else [])
        for p in tg:
            z = _user_zone_of(p, ctx.vault_n, ctx.sources)
            if z and _exists_with_content(p):
                return ctx.delete(p, z)
        return None
    if verb in _MOVE_VERBS or verb in _COPY_VERBS:
        got = _move_copy_check(plan, ctx, verb, idx)
        return _U5("batch") if got == "unknown" else got
    if verb in _PASS_VERBS and not (verb in ("new-item", "ni") and re.search(r"-force", plan["text"], re.IGNORECASE)):
        return None
    if verb == "dd":
        for w in plan["args"]:
            if w.lower().startswith("of="):
                for p in _resolved(w[3:], base) or []:
                    z = _user_zone_of(p, ctx.vault_n, ctx.sources)
                    if z and os.path.isfile(p):
                        return ctx.content(p, z)
        return None
    # 其餘的寫（Set-Content、tee、sed -i、perl -i、truncate、curl -o、tar、unzip、git checkout…）：目標是既有的檔 → content
    for p in targets:
        z = _user_zone_of(p, ctx.vault_n, ctx.sources)
        if z and z != "ANC" and os.path.isfile(p):
            return ctx.content(p, z)
    return None


_SUBPROC_CALL = re.compile(r"os\.(?:system|popen)\s*\(|subprocess\.\w+\s*\(|\bexecSync\s*\(|\bspawnSync\s*\(|child_process")
_SUBPROC = re.compile(
    r"(?:os\.(?:system|popen)|subprocess\.(?:run|call|check_call|check_output|Popen)|execSync|spawnSync)\s*\(\s*"
    r"(?:(?P<q>['\"])(?P<s>(?:(?!(?P=q)).)*)(?P=q)|\[(?P<lst>[^\]\n]*)\])")
_STR_LIT = re.compile(r"""(['"])((?:(?!\1).)*)\1""")
_REL_PATH_IN_CODE = re.compile(r"""['"]([^'"\n]*[/\\][^'"\n]*|[^'"\n/\\\s]+\.\w{1,6})['"]""")

_ESCAPE_ONLY = re.compile(r"\\[ntr0abfv]")


def _code_commands(code):
    """直譯器程式碼裡跑別的指令的字串常數（os.system("…")、subprocess.run(["rm", "x"])）：回 ([指令字串], 有沒有不是常數的呼叫)。"""
    cmds, seen = [], 0
    for m in _SUBPROC.finditer(code):
        if m.group("s") is not None:
            cmds.append(m.group("s"))
            seen += 1
            continue
        lst = m.group("lst") or ""
        lits = [x[1] for x in _STR_LIT.findall(lst)]
        if lits and len(lits) == len([p for p in lst.split(",") if p.strip()]):
            cmds.append(" ".join(f'"{x}"' if " " in x else x for x in lits))
            seen += 1
    return cmds, len(_SUBPROC_CALL.findall(code)) > seen


def _code_touches(code, base, ctx, args=None):
    """程式碼可能碰到知識庫或來源：效用 cwd 在知識庫或來源裡（或推不出來）、程式碼裡有指到它們的路徑、或有相對路徑（spec 設計自定）。
    例外（放寬甲 11-02 再定 4）：cwd 在知識庫或來源裡，但程式碼沒有字面路徑也沒有相對路徑、而且給的參數全是知識庫與來源外的絕對路徑，放行。"""
    if base is None:
        return True
    for raw in cmdparse.code_paths(code):
        p = _norm(raw, base)
        if p is not None and (classify(p, ctx.vault_n, ctx.sources)[0] != "Z11" or _ancestor_zone(p, ctx.vault_n, ctx.sources)[0]):
            return True
    for m in _REL_PATH_IN_CODE.finditer(code):
        s = m.group(1)
        plain = _ESCAPE_ONLY.sub("", s)
        if plain != s and not re.search(r"[/\\]|\.\w{1,6}$", plain):
            continue  # '\n'、'\t' 這種跳脫字元不是路徑
        if not cmdparse._ABS_START.match(s) and not cmdparse._URL.match(s):
            return True
    if classify(base, ctx.vault_n, ctx.sources)[0] != "Z11" or _ancestor_zone(base, ctx.vault_n, ctx.sources)[0]:
        if args is None:
            return True
        for _q, lit in _STR_LIT.findall(code):
            if lit and len(lit) <= 255 and "\n" not in lit and not cmdparse._ABS_START.match(lit) \
                    and not cmdparse._URL.match(lit) and os.path.lexists(os.path.join(base, lit)):
                return True  # 字串常數剛好是 cwd 底下存在的名字（沒斜線沒副檔名的檔或夾）
        for w in args:
            if not cmdparse._ABS_START.match(w):
                if re.search(r"[/\\]", w) or re.search(r"\.[A-Za-z0-9]{1,6}$", w):
                    return True  # 相對路徑
                continue  # 不像路徑的參數（編號、旗標值）
            p = _norm(w, base)
            if p is None or classify(p, ctx.vault_n, ctx.sources)[0] != "Z11" or _ancestor_zone(p, ctx.vault_n, ctx.sources)[0]:
                return True
    return False


def _arg_words(plan, inline, script_path):
    """直譯器命令列上給程式的參數：-c 的程式碼字串之後、或腳本檔之後的詞（旗標不算）。"""
    args = plan["args"]
    rest = []
    if inline:
        for i, w in enumerate(args):
            if w.lower() in cmdparse.CODE_FLAGS and i + 1 < len(args):
                # 程式碼字串裡有 {a,b} 會被拆成好幾個詞（brace 展開）：跟程式碼開頭長段一樣的詞都不是參數
                rest = [x for x in args[i + 1:] if len(os.path.commonprefix([x, inline])) < 20 and x != inline]
                break
    elif script_path:
        for i, w in enumerate(args[1:], 1):
            if not w.startswith("-") and w.lower().endswith(cmdparse.SCRIPT_EXTS):
                rest = args[i + 1:]
                break
    return [w for w in rest if not w.startswith("-")]


def _script_of(plan, base, ctx):
    """第一個存在的腳本檔參數：回 (路徑或 None, 內容或 None, 狀態 ok｜unread｜none)。大小超過 guard_script_max_bytes 或讀不到是 unread；
    plugin 自己的腳本不看。"""
    for w in plan["args"][1:]:
        if w.startswith("-") or not w.lower().endswith(cmdparse.SCRIPT_EXTS):
            continue
        p = _norm(w, base)
        if p is None or not os.path.isfile(p) or inside(p, normalize(str(plugin_root()), None)):
            continue
        try:
            if os.path.getsize(p) > config.get(ctx.vault, "guard_script_max_bytes"):
                return p, None, "unread"
            with open(p, encoding="utf-8", errors="replace") as f:
                return p, f.read(), "ok"
        except OSError:
            return p, None, "unread"
    return None, None, "none"


def _code_arg(plan):
    """殼與直譯器的 -c、-e、-Command 後面的程式碼字串（cmd 的 /c、/k 後面全部）；沒有回 ''。"""
    args = plan["args"]
    for i, w in enumerate(args):
        if w.lower() in cmdparse.CODE_FLAGS and i + 1 < len(args):
            if w.lower() in ("/c", "/k"):  # cmd 的 /c 後面是整句指令：有空白的詞（路徑）要把引號補回去，不然拆成兩個詞看不出目標
                rest = args[i + 1:]
                if len(rest) == 1:  # cmd /c "整句指令"
                    return rest[0]
                return " ".join(f'"{x}"' if re.search(r"\s", x) else x for x in rest)
            return args[i + 1]
    return ""


def _guess_name(text):
    """看不出目標時理由句裡的名字：第一個像檔名或路徑的字，沒有就說「這些檔」。"""
    for t, _q, _g, _a in _tokens_q(cmdparse.strip_marks(text)):
        if t and not t.startswith(("-", "$", "%")) and ("/" in t or "\\" in t or "." in t.rsplit("/", 1)[-1]):
            return t.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return "這些檔"


def _u5_decision(what, ctx, text, zone=""):
    if what == "delete":
        return ctx.deny(zone, "delete", DELETE_REASON.format(name=_guess_name(text)))
    if what == "content":
        return ctx.deny(zone, "content", CONTENT_REASON.format(name=_guess_name(text)))
    return ctx.deny(zone, "batch", BATCH_REASON)


def _interp_check(plan, ctx, recurse):
    """U6：直譯器與殼的程式碼。回 Decision 或 None；放行但腳本讀不到時在 ctx.rows 記一行 script_unread。
    recurse(tool, text, base) 把內層指令照整套判斷（回 Decision）。"""
    base = plan.get("base")
    runs = plan["runs"]
    path, script, status = _script_of(plan, base, ctx)
    if status == "unread":
        ctx.rows.append(_row(ctx.tool, "pass", "", SCRIPT_UNREAD, ctx.command))
    inline, extra = _code_arg(plan), []
    if runs in cmdparse._PS_HOSTS:  # -EncodedCommand：解開當 -Command 的內容判，解不開擋 unparsed（守門補洞 #25）
        try:
            decoded = cmdparse.encoded_command(plan["args"])
        except cmdparse.Unbalanced:
            return ctx.deny("", "unparsed", UNPARSED_REASON)
        if decoded is not None:  # 同一行另外帶的 -c／-Command 內容也照判（不讓解開的指令蓋掉它）
            args = plan["args"]
            inline = decoded
            extra = [args[i + 1] for i, w in enumerate(args) if w.lower() in ("-c", "-command") and i + 1 < len(args)]
    bodies = plan.get("code") or ""
    shell_like = runs in cmdparse.SHELL_WRAPPERS
    ext = (path or "").lower()
    snippets = [s for s in (inline, *extra, bodies if bodies.strip() else "", script) if s]
    sh_tool = "PowerShell" if (runs in ("powershell", "pwsh", "cmd") or ext.endswith((".ps1", ".bat", ".cmd"))) else "Bash"
    for snip in snippets:
        if shell_like or (ext.endswith(cmdparse.SHELL_SCRIPT_EXTS) and snip is script):
            d = recurse(sh_tool, snip, base)
            if d is not None and d.decision == "deny":
                return d
            continue
        cmds, nonconst = _code_commands(snip)
        for c in cmds:
            d = recurse("PowerShell" if runs in ("powershell", "pwsh") else "Bash", c, base)
            if d is not None and d.decision == "deny":
                return d
        # Office 附著後帶路徑的另存：既有檔 content、新檔放行（守門補洞 #25）；腳本檔的內容照現狀（spec 不做）
        got = _existing_content(cmdparse.office_save_targets(snip), base, ctx) if snip is not script else None
        if got is not None:
            return got
        argw = _arg_words(plan, inline, path)
        if cmdparse.DESTRUCTIVE_CODE.search(snip) and _code_touches(snip, base, ctx, argw):
            return ctx.deny("", "code", CODE_REASON)
        if nonconst and _code_touches(snip, base, ctx, argw):
            what = _u5_words({"text": snip, "code": ""}, ctx)
            if what:
                return _u5_decision(what, ctx, snip)
    if shell_like and not snippets:
        what = _u5_words({"text": plan["text"], "code": bodies}, ctx)
        if what and _code_touches(plan["text"], base, ctx):
            return _u5_decision(what, ctx, plan["text"])
    return None


def _perl_inplace(plan, ctx):
    """perl 帶 -i（-pi、-i.bak）：參數裡存在的使用者檔照 U4 回 content；沒有回 None。"""
    if plan["runs"] != "perl":
        return None
    args = plan["args"][1:]
    if not any(re.fullmatch(r"-[a-zA-Z]*i\S*", w) for w in args):
        return None
    skip = False
    for w in args:
        if skip:
            skip = False
            continue
        if w in ("-e", "-E"):
            skip = True
            continue
        if w.startswith("-"):
            continue
        for p in _resolved(w, plan.get("base")) or []:
            z = _user_zone_of(p, ctx.vault_n, ctx.sources)
            if z and z != "ANC" and os.path.isfile(p):
                return ctx.content(p, z)
    return None


def _user_rules(info, ctx, base0, classified_zones, recurse):
    """每一段寫入照使用者區規則判；回 Decision 或 None。
    U5 只在整句碰到的路徑（含效用 cwd）有使用者區、或推不出效用 cwd 時才套用；cwd 在 willy 自己的區又看不出目標，擋 willy。"""
    touch_user = base0 is None
    willy_base = None
    for plan in info["stages"]:
        if plan["verb"] in _CD_VERBS:
            continue
        b = plan.get("base", base0)
        if b is None:
            touch_user = True
        else:
            if _user_zone_of(b, ctx.vault_n, ctx.sources):
                touch_user = True
            z, _a = classify(b, ctx.vault_n, ctx.sources)
            if z in WILLY_ZONES and willy_base is None:
                willy_base = (z, b)
        for p in (plan.get("own") or []) + (plan.get("redirects") or []) + (plan.get("reads") or []):
            if _user_zone_of(p, ctx.vault_n, ctx.sources):
                touch_user = True
    if any(z in USER_ZONES for z in classified_zones):
        touch_user = True
    pending = []
    for plan in info["stages"]:
        if plan.get("interp"):
            d = _perl_inplace(plan, ctx)  # perl -pi -e …：直譯器的 -i 是改既有檔內容（U4），不是程式碼判斷
            if d is not None:
                return d
            d = _interp_check(plan, ctx, recurse)
            if d is not None:
                return d
            continue
        got = _check_plan(plan, ctx)
        if isinstance(got, _U5):
            pending.append((plan, got.what))
        elif got is not None:
            return got
    for plan, what in pending:
        if willy_base is not None and plan.get("unknown"):
            z, b = willy_base
            return ctx.deny(z, "willy", willy_reason(z, b, ctx.vault_n, ctx.command))
        if what and touch_user:
            return _u5_decision(what, ctx, plan["text"])
    return None


def _shell_tool(tool, command, cwd, vault, sources, depth=0):
    powershell = tool == "PowerShell"
    clean, bodies = cmdparse.split_bodies(command, powershell=powershell)
    flat = cmdparse.strip_marks(clean)
    vault_n = normalize(vault, None)
    rows, saw_unresolved = [], False
    ctx = _Ctx(tool, command, vault, vault_n, sources, rows)

    def deny(zone, code, reason):
        return Decision("deny", zone, code, reason, [_row(tool, "deny", zone, code, command)], vault)

    def unparsed():  # 指令拆不開（引號或括號沒配好、巢狀太深）：看不出裡面是什麼，擋（跟看不出寫到哪的 U5 不同）
        return Decision("deny", "", "unparsed", UNPARSED_REASON, [_row(tool, "deny", "", "unparsed", command)], vault)

    def recurse(tool2, text2, base2):  # U6：腳本檔、-c 字串、subprocess 常數裡的指令照整套判斷
        if depth >= SUBST_DEPTH_MAX:
            return unparsed()
        return _shell_tool(tool2, text2, base2 or cwd, vault, sources, depth + 1)

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
            what = _u5_words({"text": text, "code": ""}, ctx)  # 效用 cwd 推不出來（cd $X 之後）又要寫：照 U5 看動詞
            if what:
                return _u5_decision(what, ctx, text)

    info = _analyze(clean, cwd, vault_n, tool, bodies)
    # 開檔段開的是程式（D-514）：程式檔在知識庫或來源裡擋 run_file；其餘把程式名加參數那行照直接下那行判（內層 deny 就 deny）
    for plan in info["stages"]:
        opened = plan.get("open")
        if opened is not None and opened.get("exe"):  # 完整路徑寫的動詞（…\explorer.exe）本身在知識庫或來源裡（裁定 11）
            p = _program_file(opened["exe"], plan.get("base"))
            zone = classify(p, vault_n, sources)[0] if p is not None else None
            if zone in VAULT_ZONES or zone in SOURCE_ZONES:
                return deny(zone, RUN_FILE[1], RUN_FILE[2])
        if opened is None or opened["kind"] != "run" or opened["opaque"]:
            continue
        base = plan.get("base")
        run_cwd = _norm(opened["cwd"], base) if opened["cwd"] else base
        forced = opened["verb_flag"] is not None and opened["verb_flag"].lower() not in cmdparse.OPEN_VERB_FLAGS
        programs = [t for t in opened["targets"] if forced or not cmdparse._openable(t, base)]
        if base is None and any(not cmdparse.OPEN_URL.match(t) and not cmdparse._ABS_START.match(t) for t in programs):
            return unparsed()  # cd $X 之後跑相對的程式：看不出跑的是哪個檔
        for raw in programs:
            for p in dict.fromkeys(_program_file(raw, b) for b in (base, run_cwd)):
                zone = classify(p, vault_n, sources)[0] if p is not None else None
                if zone in VAULT_ZONES or zone in SOURCE_ZONES:
                    return deny(zone, RUN_FILE[1], RUN_FILE[2])
        # 程式的參數裡點名知識庫或來源裡的腳本（cmd /c <x.bat>、wscript <x.wsf>、powershell -c "& '<x.ps1>'"）：同樣不直接執行
        for p in cmdparse.extract_paths(opened["program"] or "", run_cwd or cwd, powershell=powershell):
            zone = classify(p, vault_n, sources)[0]
            if (zone in VAULT_ZONES or zone in SOURCE_ZONES) and not cmdparse._openable(p, None):
                return deny(zone, RUN_FILE[1], RUN_FILE[2])
        if not opened["program"]:
            continue
        if depth >= SUBST_DEPTH_MAX:
            return unparsed()
        d = _shell_tool(tool, opened["program"], run_cwd or cwd, vault, sources, depth + 1)
        if d.decision == "deny":
            return deny(d.zone, d.reason_code, d.reason)
        if run_cwd is None and _analyze(*cmdparse.split_bodies(opened["program"], powershell=powershell)[:1], cwd, vault_n,
                                        tool)["has_write"]:
            what = _u5_words({"text": opened["program"], "code": ""}, ctx)  # 效用 cwd 推不出來又要寫：照 U5 看動詞
            if what:
                return _u5_decision(what, ctx, opened["program"])
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
            if p in scanned or p in writes or p in body_only or zone == "Z10" or not os.path.isdir(p) or (
                    p in info["open_exempt"] and p not in info["read_dirs"]):
                continue  # 真正的磁碟根照走訪，撞上限就「範圍太大」（W2）；/c 這種開關 cmdparse 已不當路徑（V5）；開一個夾不走訪（D-514）
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

    # D-186：每個路徑照讀判斷；只有真正被寫的（導向目標、寫入那段的參數、管線後面才寫時整個子指令）套寫入欄
    # （第八輪「整條 is_write 為真全部套寫入欄」的誤擋收在這裡，B-024）。只切目錄的目標不算 Z10b（只切目錄不讀內容）
    moves_root = _moves_root(flat, cwd, vault_n, powershell)
    ws_saves = _workspace_saves(info, vault_n, powershell) if writes else set()

    def write_column(p, zone, alias):
        ws = p if p in ws_saves else p.rstrip(",") if p.rstrip(",") in ws_saves else None  # 'a.pdf', 17 的逗號會黏在路徑上（B-542）
        if ws is not None:
            if os.path.lexists(ws):  # 既有的檔或夾：照蓋掉判
                return deny(zone, "content", CONTENT_REASON.format(name=_display_name(command, ws)))
            return None
        if zone == "Z8" and p == vault_n and moves_root:
            return deny(zone, VAULT_MOVE_CODE, VAULT_MOVE_REASON)  # 寫入目標是總資料夾本身：搬家，不是「第一層不放檔」
        decision, code, reason = _verdict(zone, "read", alias)
        if decision == "deny":
            return deny(zone, code, reason)
        if zone == "Z8" and p == vault_n:  # 知識庫本身要刪：擋，教用 kb 搬家與還原的做法；cp x .、tar -C . 這類只是放東西進第一層，照使用者區規則（U1）
            if _u5_words({"text": command, "code": ""}, ctx) == "delete":
                return deny(zone, "delete", DELETE_REASON.format(name=_display_name(command, p)))
            return None
        if _willy_shell_zone(zone, p, vault_n):  # willy 自己的檔：指令寫一律擋，教 AI 用對應的做法（放寬甲）
            return deny(zone, "willy", willy_reason(zone, p, vault_n, command))
        if zone == "Z11":
            up_zone, up_alias = _ancestor_zone(p, vault_n, sources)
            if up_zone:
                return deny(up_zone, "delete" if up_zone == "Z9" else "willy",
                            DELETE_REASON.format(name=_display_name(command, p)) if up_zone == "Z9"
                            else willy_reason("Z7", p, vault_n, command))
        return None  # 使用者的區：照 _user_rules 判

    checked = set()
    cd_only = info["list_only"]  # cd 的 Z10b 豁免只給整句只切目錄、列檔名的（cd 進去再跑程式照擋）
    for p, zone, alias in classified:  # 照抽出的順序，每個路徑先讀、是寫的目標再寫（理由句跟第八輪以前一樣具體）
        decision, code, reason = _verdict(zone, "read", alias)
        if decision == "deny" and not (zone == "Z10b" and cd_only and p in info["cd_targets"] and p not in writes) and not (
                zone == "Z10b" and p in info["open_exempt"]):  # 開一個含排除夾的夾不是讀它（D-514）
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
    # 放寬甲：使用者區規則（U1～U6）；看不出目標的（原本的 unparsed）照 U5 看動詞
    got = _user_rules(info, ctx, normalize(cwd, None), [z for _p, z, _a in classified], recurse)
    if got is not None:
        return got
    if saw_unresolved:
        rows = _unresolved_rows(tool, sources, command)
    return Decision("pass", "", "", "", rows, vault)


def _file_label(vault, arg):
    """理由句裡的檔名：清冊編號換成清冊裡的檔名（找不到回 None，編號不給客戶看）；uri、路徑取最後一段。"""
    text = str(arg or "").strip()
    if _FILE_ID.match(text):
        try:
            import json
            with open(os.path.join(str(vault), ".system", "inventory.json"), encoding="utf-8") as f:
                entries = json.load(f).get("entries") or []
            for e in entries:
                if isinstance(e, dict) and e.get("id") == text and e.get("locations"):
                    return str(e["locations"][0].get("uri", "")).rsplit("/", 1)[-1] or None
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return None
    return text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or None


class NoSuchFile(Exception):
    """「可以讀」給的檔對不到清冊（第五輪 S2）：守門直接擋，不跳允許詢問。"""


def _read_label(vault, cwd, arg):
    """「可以讀」理由句的檔：照 kb ruling add 同一套（rulings.file_entry）對到清冊條目，回「別名/上一層夾/檔名」（同名檔分得出來，
    第五輪 S2）；磁碟路徑先接對話的 cwd。對不到丟 rulings.RulingRefused。"""
    from kb._lib import inventory, rulings
    text = str(arg or "").strip()
    if not text.startswith("file://") and not (_FILE_ID.match(text) or text.startswith("out-")):
        text = normalize(text, cwd)
    entry = rulings.file_entry(vault, text)
    alias, rel = inventory.split_uri(entry["locations"][0]["uri"])
    return "/".join([alias] + rel.split("/")[-2:])


def secret_action(command, tool, vault, cwd=None):
    """D-212：單一 kb 呼叫是不是四種開新口子的密碼動作（kb unlock get、kb ruling add --kind 可以讀、kb unlock authorize、
    kb convert --saved … --explicit）。是回理由句，不是回 None。參數照 kb 自己的 argparse 認（縮寫、--x=值、順序都一樣）；
    子命令那個字、或 unlock／ruling／convert 的參數裡有看不出實際值的字（變數、萬用字元），認不出來，照 ask。
    argparse 拆不過的不問：守門用的就是 kb.py 跑的那一份 argparse，拆不過 kb 就什麼都不做。
    #50：後面接 # 註解時殼不會把註解傳給 kb：每個可能的註解開頭都截掉再認一次，任何一種是密碼動作就問。"""
    for cut in [None] + cmdparse.comment_cuts(command, tool):
        ask = _secret_action_one(command if cut is None else command[:cut].rstrip(), tool, vault, cwd)
        if ask is not None:
            return ask
    return None


def _secret_action_one(command, tool, vault, cwd=None):
    parsed = cmdparse.kb_argv(command, tool)
    if parsed is None:
        return ASK_UNKNOWN
    from kb import cli
    first = next((k for k, (a, _u) in enumerate(parsed) if not a.startswith("-")), None)
    if first is not None and parsed[first][1]:
        return ASK_UNKNOWN  # 子命令本身看不出來
    lead_ok = all(not u and (a in ("-h", "--help") or (a.startswith("--") and "--json".startswith(a) and len(a) > 2))
                  for a, u in parsed[:first or 0])
    if first is not None and lead_ok and parsed[first][0] in cli.COMMANDS and parsed[first][0] not in SECRET_COMMANDS:
        return None  # 認得、又不是這三個子命令：照常放行，不用載入整個 argparse
    if any(u for _a, u in parsed):
        return ASK_UNKNOWN
    argv = [a for a, _u in parsed]
    import contextlib
    import io
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            args = cli.build_parser().parse_args(argv)
    except SystemExit:
        return None  # --help 只印說明；拆不過的 kb 也拆不過，什麼都不做
    command_name = getattr(args, "command", None)

    def m(word):  # 只對代入的名稱遮值；固定句不過遮值，「可能」這類字才不會被當成值遮掉（B-424 (1)）
        return credentials.mask_secrets(str(word), credentials.known_names(vault))
    if command_name == "unlock" and getattr(args, "unlock_command", None) == "get":
        return ASK_GET.format(m(args.name))
    if command_name == "unlock" and getattr(args, "unlock_command", None) == "authorize":
        return ASK_AUTHORIZE.format(m(args.label), m(args.name))
    if command_name == "ruling" and getattr(args, "ruling_command", None) == "add":
        from kb._lib import rulings
        try:
            kind = rulings.kind_of(args.kind)  # 跟 kb ruling add 同一套（中文、英文值都認）
        except Exception:  # noqa: BLE001  不認得的種類 kb 會拒絕，照 ask 也無妨
            kind = None
        if kind in ("spoken_wins", "ruled_out"):
            return None
        if not args.file:
            return ASK_READ_ANY
        try:
            names = [_read_label(vault, cwd, f) for f in args.file]
        except rulings.RulingRefused:
            raise NoSuchFile() from None  # 對不到清冊：kb 也會拒絕，不跳詢問（第五輪 S2）
        except Exception:  # noqa: BLE001  清冊讀不到：認不出是哪份，照問
            return ASK_READ_ANY
        return ASK_READ.format("、".join(m(n) for n in dict.fromkeys(names)))
    if command_name == "convert" and args.saved is not None and args.explicit:
        label = _file_label(vault, args.target)
        return ASK_EXPLICIT.format(m(args.saved), m(label) if label else "那份檔")
    return None


def secret_in(command, tool, vault, cwd, root):
    """#50：串在一起的句子裡任何一段是密碼動作就回理由句（整句沒有 kb.py 不看，舊設計）。
    主判斷不拿註解：照串接、換行、管線切段（引號沒成對又有這三個字照問）。有 kb.py 的段是單一 kb 呼叫就照 secret_action 認；
    不是單一呼叫的段，有 unlock／ruling／convert（拆字接回去比）、或 kb.py 後面第一個字認不出，照 ASK_UNKNOWN
    （包在 bash -c、env、變數前綴、路徑放變數、夾了導向都算）。另一種切法照殼拿掉註解，只用來找出是哪個動作講理由，
    不會讓本來要問的變成不問。工具不知道時兩種殼都看。"""
    from kb import cli
    name = cmdparse.KB_SCRIPT.rsplit("/", 1)[-1]
    words = set(SECRET_COMMANDS)

    def plain(text):  # 殼會接回去的拆字（unl\ock、"un""lock"、unl`ock）先接回去再比（第二輪審查 1）
        return re.sub(r"[\\`'\"{}]", "", text.casefold())

    def has_word(text):
        return bool(words & set(re.findall(r"[a-z]+", plain(text))))

    def unclear_sub(seg):  # 每個 kb.py 後面的第一個字：- 開頭（全域選項會吃值，猜不準子命令）、或不是認得的非密碼子命令，算認不出
        text = plain(seg)
        for m in re.finditer(re.escape(name), text):
            rest = text[m.end():].split()
            if rest and (rest[0].startswith("-") or rest[0] not in cli.COMMANDS or rest[0] in words):
                return True
        return False
    if name not in command.casefold():
        return None
    unknown = False
    for powershell in ((False,) if tool == "Bash" else (True,) if tool == "PowerShell" else (False, True)):
        for comments in (True, False):
            segs = cmdparse.segments(command, powershell, comments)
            if segs is None:
                unknown = unknown or (not comments and has_word(command))
                continue
            for seg in segs:
                single = name in seg.casefold() and cmdparse.is_single_plugin_call(seg, root, cwd, tool)
                if single:
                    ask = secret_action(seg, tool, vault, cwd)
                    if ask is not None:
                        return ask
                elif not comments and (has_word(seg) or (name in seg.casefold() and unclear_sub(seg))):
                    unknown = True  # 不是乾淨的單一呼叫卻有這三個字（路徑放變數也算）、或 kb.py 後面認不出：寧可問
    return ASK_UNKNOWN if unknown else None


def decide(payload):
    """照 tool_name 分流。回 Decision(decision, zone, reason_code, reason, ledger_rows, vault)。"""
    global _OUTER_SEEN
    _OUTER_SEEN = {}  # 上層 vault 每次判斷重新找、這一次裡重用；結束就關掉
    try:
        return _decide(payload)
    finally:
        _OUTER_SEEN = None


def _decide(payload):
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
        single = cmdparse.is_single_plugin_call(command, root, cwd, tool)
        if single or cmdparse.KB_SCRIPT.rsplit("/", 1)[-1] in command.casefold():
            try:  # 單一呼叫照 secret_action（含後面接註解，#50）；串在一起的照 secret_in 逐段看
                ask = secret_action(command, tool, vault, cwd) if single else secret_in(command, tool, vault, cwd, root)
            except NoSuchFile:
                return Decision("deny", "", NO_SUCH_FILE, NO_SUCH_FILE_REASON,
                                [_row(tool, "deny", "", NO_SUCH_FILE, command)], vault)
            if ask is not None:  # D-212：開新口子的密碼動作要客戶按允許；前綴讓 AI 知道要停下來請客戶按（第五輪 S1）
                reason = ASK_PREFIX + ask  # 代入的名稱在 secret_action 裡遮過（B-424 (1)）
                return Decision("ask", "", SECRET_ACTION, reason, [_row(tool, "ask", "", SECRET_ACTION, command)], vault)
        if single:
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
            # B-425 (a)（T-7 後）：Glob 樣式是單一檔名、而且 path 底下的排除夾全是 unused 時，不判 Z10b，
            # 改判「path／檔名」這個檔（像密碼的檔名照走憑證那道、檔名剛好是不讀夾的名字照 Z10）；path 本身在不讀夾裡照 Z10。
            # Glob 的單一檔名實測也會往下找、列出不讀夾裡同名檔的路徑：unused 可以接受，hidden 連檔名都算要清的（D-174），照舊擋 Z10b
            if (tool == "Glob" and _single_filename(ti.get("pattern")) and "{" not in str(target)
                    and not cmdparse.is_wildcard(str(target))
                    and classify(target_n, normalize(vault, None), sources)[0] == "Z10b"
                    and not _hidden_exclude_under(sources, target_n)):
                return _file_tool(tool, target_n + "/" + str(ti["pattern"]), cwd, vault, sources)
        return _file_tool(tool, str(target), cwd, vault, sources)

    return Decision("pass", "", "", "", [], vault)
