"""指令字串解析：抽路徑、判寫入、判「整句只有一個 kb.py 呼叫」。

字串比對是第二道防線：刻意把路徑拆開的指令擋不住（I-031），所以守門對抽不到路徑的寫入一律擋（unparsed）。
照舊 guard.py 的 outside_quotes、norm_cmd、WRITE_WORDS、REDIRECT、DISCARD_OUTPUT、single_plugin_call、ABS_PATH 改介面。
審閱後補：引號裡的包裝指令（bash -c、cmd /c、powershell -c）整句都看；cd 之後的相對路徑接 cd 的目標；
token 裡夾的絕對路徑（python -c、P=…）抽出來；萬用字元 token 另外回報。
第一輪修正：直譯器只接受裸字或不在知識庫／來源底下的絕對路徑（S1）；引號外的 ( ) 也算串接（S2）；
寫入動詞前一個字元不能是 . 字母 數字 / \\（B1，.md 不是 md）；相對 token 要解析得到才算路徑（B3）。
"""
import os
import re

from kb._lib.paths import normalize

KB_SCRIPT = "scripts/kb.py"

# 只是把輸出丟掉，不算寫入：2>/dev/null、>/dev/null、2>&1、> nul、> $null、| Out-Null
DISCARD_OUTPUT = re.compile(r"\d?>\s*&\s*\d|\d?>\s*/dev/null|\d?>\s*\$null|\d?>\s*nul\b|\|\s*out-null", re.IGNORECASE)
# 寫入動詞只在 token 開頭：前一個字元不能是 . 字母 數字 _ -（B1：.md、--rm 不算）；
# 前面是 \ 或 / 也算動詞（N-b：\rm、/bin/rm、Microsoft.PowerShell.Management\Remove-Item）
_W = r"(?<![.\w-])"
WRITE_WORDS = re.compile(
    _W + r"(?:rm|mv|cp|tee|touch|mkdir|rmdir|del|erase|move|copy|ren|rename|truncate|dd|rsync|wget|tar|unzip|install|ln|"
    r"chmod|chown|ri|rd|md|sc|ac|ni|cpi|mi|rni|iex|robocopy|xcopy|zip|7z|compress-archive|expand-archive|shred|unlink|tee-object)\b|"
    + _W + r"sed(?:\s+-[a-zA-Z]+)*\s+(?:-[a-zA-Z]*i|--in-place)|" + _W + r"perl(?:\s+-[a-zA-Z]+)*\s+-[a-zA-Z]*i|"
    + _W + r"curl\b[^|;&]*\s(?:-[a-zA-Z]*o|-O|--output)\b|-delete\b|"
    + _W + r"git\s+(?:clean|checkout|restore|reset|stash|rm|mv)\b|"
    + _W + r"(?:remove-item|move-item|copy-item|rename-item|new-item|set-content|add-content|out-file|clear-content|"
    r"set-itemproperty|export-csv|export-clixml|invoke-expression|start-process)\b",
    re.IGNORECASE,
)
# 程式碼裡的寫入（python -c、inline script）：引號內也算
WRITE_CODE = re.compile(
    r"(open\([^)]*['\"][wax+]|\.write_text|\.write_bytes|writealltext|writeallbytes|shutil\.|os\.remove|"
    r"os\.rename|os\.unlink|os\.makedirs|os\.mkdir|\bunlink\(|writefilesync|unlinksync|renamesync|mkdirsync)",
    re.IGNORECASE,
)
# 會把引號裡的字串當指令跑的包裝：出現時整句（含引號內）都看
WRAPPER = re.compile(
    r"(?:^|[\s;&|(])(?:bash|sh|zsh|dash|ksh|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?|eval|exec|source|"
    r"python3?|py|node|perl|ruby|php)\b",
    re.IGNORECASE,
)
REDIRECT = re.compile(r">")
# 引號外不能出現的字元：串接（; & |）、導向（< >）、換行、子運算式（( )，含 $( 與 PowerShell 的 @( ）；反引號在引號裡也會執行
SHELL_META = set(";&|<>()\n\r")
_TOKEN_BREAK = set(";|&<>()")
_URL = re.compile(r"^(?!file:)\w+://")
_OPT_PREFIX = re.compile(r"^--?[\w-]+=")
_FILENAME = re.compile(r"\.\w{1,6}$")
# 第九輪 Z4：不存在的裸 token 只有這些副檔名才算新檔（v1.2、example.com 不算）
_NEW_FILE_EXT = re.compile(r"\.(?:md|txt|docx|doc|xlsx|xls|csv|pdf|pptx|json|yaml|yml|ini|log|html|png|jpg|jpeg|zip)$", re.IGNORECASE)
_GLOB_CHARS = re.compile(r"[*?\[]")  # 第十輪 A6：是不是萬用字元 token 先過 is_wildcard，這只找第一個萬用字元的位置；大括號先經 brace_expand
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")  # 第十輪 A1：{a,b} 展開
BRACE_LIMIT = 20
# 第十輪 A5：展不開的變數——整個 token 是 $X、${X}、$X/…、$X\…（PowerShell 的 $env:X 也在內）；%X%、%X%\… 另算
_VAR_WHOLE = re.compile(r"^\$(?:\{(?P<b>[A-Za-z_][\w:]*)\}|(?P<n>[A-Za-z_][\w:]*))(?:[\\/].*)?$")
_PCT_WHOLE = re.compile(r"^%[A-Za-z_]\w*%(?:[\\/].*)?$")
_VAR_INSIDE = re.compile(r"\$\{?[A-Za-z_]|%[A-Za-z_]\w*%")
PS_AUTO_VARS = {"false", "true", "null", "_", "psitem", "args", "lastexitcode"}  # PowerShell 自動變數不是展不開的路徑；$? 本來就不像變數名
_ABS_START = re.compile(r"^(?:[A-Za-z]:|/|\\\\|~|file:)")
_CD = {"cd", "pushd", "set-location", "sl", "chdir"}
# token 裡夾的絕對路徑：C:\…、C:/…、/c/…
ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?<![\w.~-])/[a-zA-Z]/)[^\"'\s|;&<>()]*")
_PYTHON = re.compile(
    r"^\s*(\"[^\"]+\"|'[^']+'|\S+)(?:\s+-(?:[IuBsE]+|X\s+\S+))*\s+(\"[^\"]+\"|'[^']+'|\S+)(?:\s|$)",
    re.IGNORECASE,
)
_BARE_INTERP = {"python", "python3", "py", "python.exe", "python3.exe", "py.exe"}
# bash 的跳脫：\ 後接非 ASCII 或空白時，\ 只是跳脫（薪\資 → 薪資、AI\ 產出 → AI 產出）
_ESCAPE = re.compile(r"\\(?=[^\x00-\x7f]| )")


def outside_quotes(cmd):
    """去掉引號裡的內容（引號裡的 > ; | 都只是文字）。引號沒有成對時回 None。
    反斜線跳脫的引號（\\" 與 \\'）不是引號邊界（N-a）。"""
    out, quote, prev = [], None, ""
    for ch in cmd:
        escaped = ch in "\"'" and prev == "\\"
        if quote:
            if ch == quote and not escaped:
                quote = None
        elif ch in "\"'" and not escaped:
            quote = ch
        else:
            out.append(ch)
        prev = ch
    return None if quote else "".join(out)


def norm_cmd(cmd):
    """小寫、\\\\ 與 \\ 一律改成 /。"""
    return cmd.lower().replace("\\\\", "/").replace("\\", "/")


# Windows 開關式指令：只有這些（或 PowerShell）後面的單字母 /x 才是旗標；bash 的 grep -r X /c 裡 /c 是 C 槽根（第七輪 X3）
SWITCH_VERBS = {"cmd", "cmd.exe", "findstr", "robocopy", "xcopy", "dir", "attrib", "icacls"}


def _tokens(cmd):
    """回 [(字串, 有引號, 緊接在 > 後面, 是不是一段的第一個 token, 第一個引號字元或空字串)]。引號裡的空白與符號保留；引號沒成對就一路到底。"""
    out, buf, quote, quoted, after_redirect, first, qchar = [], [], None, False, False, True, ""

    def flush():
        nonlocal buf, quoted, after_redirect, first, qchar
        if buf or quoted:
            out.append(("".join(buf), quoted, after_redirect, first, qchar))
            after_redirect = False
            first = False
        buf, quoted, qchar = [], False, ""

    prev = ""
    for ch in cmd:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            quoted = True
            qchar = qchar or ch
        elif ch == " " and prev == "\\":
            buf.append(ch)  # 跳脫的空白是 token 的一部分（AI\ 產出），N-c 再去掉 \
        elif ch.isspace() or ch in _TOKEN_BREAK:
            flush()
            if ch == ">":
                after_redirect = True
            elif ch in _TOKEN_BREAK:
                after_redirect = False
                first = True  # ; | & ( ) 之後是新的一段
        else:
            buf.append(ch)
        prev = ch
    flush()
    return out


def brace_expand(text, limit=BRACE_LIMIT):
    """第十輪 A1：token 含 {a,b} 就展開成各組合（cat ../raw/行政/{薪資,x}/s.xlsx、rm -rf ../r{2,3}），上限 limit 組；沒逗號的大括號不動。"""
    out = [text]
    while True:
        nxt, changed = [], False
        for t in out:
            m = _BRACE.search(t)
            if not m:
                nxt.append(t)
                continue
            changed = True
            nxt.extend(t[:m.start()] + alt + t[m.end():] for alt in m.group(1).split(","))
        out = nxt[:limit]
        if not changed or len(nxt) > limit:
            return out


def brace_head(text, powershell=False):
    """第十一輪保底：token 含 { 就回 { 之前的資料夾（跟原守門 eba223d 的萬用字元前綴夾一樣）——有分隔符取最後一個夾，沒有就是 .；
    { 在開頭（ForEach-Object { $_ }）或前面是 $（${X}）回 None；前面是 @ 只有 PowerShell 才回 None（@{a=1} 是雜湊表，第十二輪），
    Bash 的 @{…,…} 照走保底。展開截斷（超過上限）、{N..M} 不展開都不影響這個保底。"""
    idx = text.find("{")
    if idx <= 0:
        return None
    head = text[:idx]
    if head[-1] == "$" or (powershell and head[-1] == "@"):
        return None
    if "/" in head or "\\" in head:
        folder = re.split(r"[\\/]", head)
        folder = "/".join(folder[:-1]) if "/" in head else "\\".join(folder[:-1])
        return folder or ("/" if head.startswith("/") else None)
    return "."


def is_wildcard(text, unquoted=None):
    """第十輪 A6：萬用字元候選要含 *，或同時含 ?／[ 與路徑分隔符且不是網址（://）。
    unquoted：只算引號外的字元（Bash 引號裡的 * ? [ 是文字，Q7）；分隔符與網址看整個 token。"""
    chars = text if unquoted is None else unquoted
    if "*" in chars:
        return True
    return ("?" in chars or "[" in chars) and ("/" in text or "\\" in text) and "://" not in text


def _bare_under(text, base):
    """第九輪 Z2：裸 token（原樣或去跳脫）接效用 cwd 後存在就是路徑（rm -rf 案子、mv 收件匣 /tmp/）。"""
    for form in dict.fromkeys((text, _ESCAPE.sub("", text))):
        try:
            if os.path.exists(normalize(form, base)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _candidates(text, quoted, after_redirect, after_cd, switch_ok=False, write_base=None):
    """一個 token 裡可能是路徑的字串。夾在中間的絕對路徑先抽；沒有就看整個 token 像不像路徑。
    write_base：寫入指令裡的裸 token（不是段動詞）要看效用 cwd——存在的算路徑，不存在的要已知副檔名才算新檔（第八輪裁定 1、第九輪 Z2 Z4）。"""
    if not text or _URL.match(text):
        return []
    if text in (".", ".."):
        return [text]  # 不帶斜線的 . 與 .. 也是路徑，要經區判斷（骨架與來源登記第三輪 S1：grep -r X .. 走 Z10b）
    if switch_ok and re.match(r"^/[a-zA-Z]$", text):
        return []  # cmd /c、findstr /s 的單字母開關不是路徑（第六輪 V5；第七輪 X3：只對開關式指令或 PowerShell）
    matches = list(ABS_PATH.finditer(text))
    if matches and matches[0].start() > 0:
        # 夾在中間的絕對路徑（python -c "open('C:/…')"、P=C:/…）
        return [m.group(0).rstrip(",;:)'\"") for m in matches if "$" not in m.group(0) and "%" not in m.group(0)]
    if matches:
        return [] if ("$" in text or "%" in text) else [text]  # 整個 token 就是路徑（引號裡可含空白）
    if "$" in text or "%" in text:
        return []
    if text.startswith("-") and not _OPT_PREFIX.match(text):
        return []
    if "/" in text or "\\" in text or after_redirect:
        return [_OPT_PREFIX.sub("", text)]
    if (quoted or after_cd) and _FILENAME.search(text):
        return [text]
    if write_base is not None and (_bare_under(text, write_base) or _NEW_FILE_EXT.search(text)):
        return [text]
    return []


def _relative_resolves(cand, path):
    """B3：相對 token 要解析後存在，或父目錄存在且最後一段像檔名（有副檔名或無空白）。含萬用字元的看前綴資料夾存不存在。"""
    m = _GLOB_CHARS.search(path) if is_wildcard(path) else None
    if m:
        head = path[:m.start()]
        head = head.rsplit("/", 1)[0] if "/" in head else head
        return bool(head) and os.path.isdir(head)
    try:
        if os.path.exists(path):
            return True
        parent, _, last = path.rpartition("/")
        return bool(parent) and os.path.isdir(parent) and bool(_FILENAME.search(last) or " " not in last)
    except (OSError, ValueError):
        return False


def _walk(command, cwd, powershell=False):
    """依序產出 (原字串, 正規化路徑)，cd 之後的相對路徑接 cd 的目標。powershell：單字母 /x 一律當旗標（PowerShell 沒有 Git Bash 寫法）。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    base, expect_cd_target, after_cd, verb = cwd, False, False, ""
    writing = is_write(command)  # 寫入指令裡像檔名的裸 token（cp /tmp/a 新檔.md）也當路徑，要經區判斷（骨架與來源登記第八輪）
    for token, quoted, after_redirect, first, _qchar in _tokens(cleaned):
        if first:
            verb = token.lower().replace("\\", "/").rsplit("/", 1)[-1]
        # 第十一輪保底：{ 之前的資料夾一律是候選，經區判斷（Z10／Z10b／上層）；排在展開之後，展開命中的理由（readonly_source）比前綴夾具體
        head = brace_head(token, powershell) if "{" in token and not (first and not expect_cd_target) else None
        head_p = normalize(head, base) if head is not None else None
        if expect_cd_target:
            if token.startswith("-"):
                continue  # Set-Location -Path X、cd -- X：旗標跳過，下一個才是目標
            expect_cd_target = False
            if token and "$" not in token and "%" not in token:
                forms = brace_expand(token)  # cd ../r{2,3}：每個展開都是目標，效用 cwd 取第一個（A1）
                targets = [normalize(form, base) for form in forms]
                base = targets[0]
                after_cd = True
                for form, target in zip(forms, targets):
                    yield form, target
                if head is not None and (_ABS_START.match(head) or _relative_resolves(head, head_p)):
                    yield head, head_p
                continue
        if token.lower() in _CD:
            expect_cd_target = True
            continue
        for text in brace_expand(token):  # 第十輪 A1：{a,b} 先展開再抽
            for cand in _candidates(text, quoted, after_redirect, after_cd, powershell or verb in SWITCH_VERBS,
                                    write_base=(base if writing and not first else None)):
                # N-c：原樣，與去掉「\ 後接非 ASCII 或空白」跳脫的形態（bash 把 薪\資 讀成 薪資）兩種都比對
                for form in dict.fromkeys((cand, _ESCAPE.sub("", cand))):
                    p = normalize(form, base)
                    if _ABS_START.match(form) or _relative_resolves(form, p):
                        yield form, p
        if head is not None and (_ABS_START.match(head) or _relative_resolves(head, head_p)):
            yield head, head_p


def extract_paths(command, cwd, powershell=False):
    """抽路徑：引號內字串、含 / 或 \\ 的 token、導向目標、token 裡夾的絕對路徑；去符號後正規化。變數（$X、%X%）不算。"""
    found = []
    for _raw, p in _walk(command, cwd, powershell):
        if p not in found:
            found.append(p)
    return found


def glob_tokens(command, cwd):
    """含萬用字元（* ? [ {）的 token，正規化後回傳（保留萬用字元），給呼叫端 glob 展開。"""
    out = []
    for raw, p in _walk(command, cwd):
        if is_wildcard(raw) and p not in out:
            out.append(p)
    return out


def glob_prefixes(command, cwd):
    """含萬用字元的 token，回萬用字元前最後一個資料夾（正規化）。shell 會展開它，守門要看它蓋到哪。"""
    out = []
    for raw, _p in _walk(command, cwd):
        m = _GLOB_CHARS.search(raw) if is_wildcard(raw) else None
        if not m:
            continue
        head = raw[:m.start()].replace("\\", "/")
        head = head.rsplit("/", 1)[0] if "/" in head else "."
        out.append(normalize(head, cwd) if head != "." else normalize(cwd, None))
    return out


def unresolved_paths(command, powershell=False):
    """第九輪 Z3、第十輪 A5：展不開的變數路徑 token——(a) 整個 token 是 $X、${X}、$X/…、$X\\…，或 %X%、%X%\\…（裸變數，第九輪裁定 3）；
    (b) 帶變數又含 / 或 \\ 的、或是導向目標的 token（C:/raw/$X、> "out$Y"）。單引號裡（Bash 與 PowerShell 都不展開）、網址、
    丟掉輸出的 >$null、| Out-Null，與 PowerShell 自動變數（$false $true $null $_ $PSItem $args $LASTEXITCODE）不算；
    +%Y%m%d、--format=%H、-Confirm:$false 這些不是整個 token 是變數的也不算。呼叫端對寫入指令有就 deny unparsed（I-031）；讀取指令不看。"""
    out = []
    for text, _quoted, after_redirect, _first, qchar in _tokens(DISCARD_OUTPUT.sub(" ", command)):
        if not text or qchar == "'" or _URL.match(text):
            continue
        m = _VAR_WHOLE.match(text)
        if m:
            name = (m.group("b") or m.group("n")).lower()
            if powershell and name in PS_AUTO_VARS:
                continue
            out.append(text)
        elif _PCT_WHOLE.match(text):
            out.append(text)
        elif _VAR_INSIDE.search(text) and ("/" in text or "\\" in text or after_redirect):
            out.append(text)
    return out


def is_write(command):
    """引號外有 > 或寫入動詞；有包裝指令（bash -c、cmd /c、powershell -c…）時整句都看；程式碼裡的 open(…,'w') 之類引號內也算。
    導到 null 不算。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    bare = outside_quotes(cleaned)
    scope = cleaned if (bare is None or WRAPPER.search(bare)) else bare
    return bool(REDIRECT.search(scope) or WRITE_WORDS.search(scope) or WRITE_CODE.search(cleaned))


def _interpreter_ok(token):
    """S1：直譯器只接受裸字 python／python3／py（可帶 .exe）。任何含路徑分隔符的直譯器一律不算（第二輪 S1 殘）。"""
    return token.lower() in _BARE_INTERP


CURLY_QUOTES = frozenset(chr(c) for c in range(0x2018, 0x2020))  # ‘’‚‛“”„‟


def is_single_plugin_call(command, plugin_root, cwd=None):
    """整句是不是只有「python <plugin>/scripts/kb.py …」這一個呼叫。串接、導向、子運算式、反引號都不算；
    引號外出現任何反斜線也不算（N-a：kb.py 路徑在引號內，用不到跳脫）。"""
    if "`" in command or "$(" in command:
        return False
    if CURLY_QUOTES & set(command):  # 彎引號 PowerShell 也當引號，outside_quotes 看不出來（manage 第二版第三輪）
        return False
    bare = outside_quotes(command)
    if bare is None or "\\" in bare or any(ch in SHELL_META for ch in bare):
        return False
    m = _PYTHON.match(command)
    if not m:
        return False
    cwd = cwd or os.getcwd()
    if not _interpreter_ok(m.group(1).strip("\"'")):
        return False
    script = m.group(2).strip("\"'")
    return normalize(script, cwd) == normalize(os.path.join(str(plugin_root), KB_SCRIPT), cwd)
