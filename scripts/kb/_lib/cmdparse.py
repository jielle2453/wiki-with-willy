"""指令字串解析：抽路徑、判寫入、判「整句只有一個 kb.py 呼叫」。

字串比對是第二道防線：刻意把路徑拆開的指令擋不住（I-031），所以守門對抽不到路徑的寫入一律擋（unparsed）。
照舊 guard.py 的 outside_quotes、norm_cmd、WRITE_WORDS、REDIRECT、DISCARD_OUTPUT、single_plugin_call、ABS_PATH 改介面。
審閱後補：引號裡的包裝指令（bash -c、cmd /c、powershell -c）整句都看；cd 之後的相對路徑接 cd 的目標；
token 裡夾的絕對路徑（python -c、P=…）抽出來；萬用字元 token 另外回報前綴。
"""
import os
import re

from kb._lib.paths import normalize

KB_SCRIPT = "scripts/kb.py"

# 只是把輸出丟掉，不算寫入：2>/dev/null、>/dev/null、2>&1、> nul、> $null、| Out-Null
DISCARD_OUTPUT = re.compile(r"\d?>\s*&\s*\d|\d?>\s*/dev/null|\d?>\s*\$null|\d?>\s*nul\b|\|\s*out-null", re.IGNORECASE)
# 寫入動詞：Unix、cmd、PowerShell cmdlet 與常用別名、會落地的工具
WRITE_WORDS = re.compile(
    r"(\b(?:rm|mv|cp|tee|touch|mkdir|rmdir|del|erase|move|copy|ren|rename|truncate|dd|rsync|wget|tar|unzip|install|ln|"
    r"chmod|chown|ri|rd|md|sc|ac|ni|cpi|mi|rni|iex)\b|"
    r"\bsed\s+(?:-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i|\bcurl\b[^|;&]*\s(?:-[a-zA-Z]*o|-O|--output)\b|-delete\b|"
    r"\bgit\s+(?:clean|checkout|restore|reset|stash|rm|mv)\b|"
    r"remove-item|move-item|copy-item|rename-item|new-item|set-content|add-content|out-file|clear-content|"
    r"set-itemproperty|export-csv|export-clixml|invoke-expression|start-process)",
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
# 引號外不能出現的字元：串接（; & |）、導向（< >）、換行；反引號與 $( 在引號裡也會執行，一律不行
SHELL_META = set(";&|<>\n\r")
_TOKEN_BREAK = set(";|&<>()")
_URL = re.compile(r"^(?!file:)\w+://")
_OPT_PREFIX = re.compile(r"^--?[\w-]+=")
_FILENAME = re.compile(r"\.\w{1,6}$")
_GLOB = re.compile(r"[*?\[{]")
_CD = {"cd", "pushd", "set-location", "sl", "chdir"}
# token 裡夾的絕對路徑：C:\…、C:/…、/c/…
ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?<![\w.~-])/[a-zA-Z]/)[^\"'\s|;&<>()]*")
_PYTHON = re.compile(
    r"^\s*(?:\S*[\\/])?(?:python3?|py)(?:\.exe)?(?:\s+-(?:[IuBsE]+|X\s+\S+))*\s+(\"[^\"]+\"|'[^']+'|\S+)(?:\s|$)",
    re.IGNORECASE,
)


def outside_quotes(cmd):
    """去掉引號裡的內容（引號裡的 > ; | 都只是文字）。引號沒有成對時回 None。"""
    out, quote = [], None
    for ch in cmd:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        else:
            out.append(ch)
    return None if quote else "".join(out)


def norm_cmd(cmd):
    """小寫、\\\\ 與 \\ 一律改成 /。"""
    return cmd.lower().replace("\\\\", "/").replace("\\", "/")


def _tokens(cmd):
    """回 [(字串, 有引號, 緊接在 > 後面)]。引號裡的空白與符號保留；引號沒成對就一路到底。"""
    out, buf, quote, quoted, after_redirect = [], [], None, False, False

    def flush():
        nonlocal buf, quoted, after_redirect
        if buf or quoted:
            out.append(("".join(buf), quoted, after_redirect))
            after_redirect = False
        buf, quoted = [], False

    for ch in cmd:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            quoted = True
        elif ch.isspace() or ch in _TOKEN_BREAK:
            flush()
            if ch == ">":
                after_redirect = True
            elif ch in _TOKEN_BREAK:
                after_redirect = False
        else:
            buf.append(ch)
    flush()
    return out


def _candidates(text, quoted, after_redirect, after_cd):
    """一個 token 裡可能是路徑的字串。夾在中間的絕對路徑先抽；沒有就看整個 token 像不像路徑。"""
    if not text or _URL.match(text):
        return []
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
    return []


def _walk(command, cwd):
    """依序產出 (原字串, 正規化路徑)，cd 之後的相對路徑接 cd 的目標。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    base, expect_cd_target, after_cd = cwd, False, False
    for text, quoted, after_redirect in _tokens(cleaned):
        if expect_cd_target:
            expect_cd_target = False
            if text and not text.startswith("-") and "$" not in text and "%" not in text:
                base = normalize(text, base)
                after_cd = True
                yield text, base
                continue
        if text.lower() in _CD:
            expect_cd_target = True
            continue
        for cand in _candidates(text, quoted, after_redirect, after_cd):
            yield cand, normalize(cand, base)


def extract_paths(command, cwd):
    """抽路徑：引號內字串、含 / 或 \\ 的 token、導向目標、token 裡夾的絕對路徑；去符號後正規化。變數（$X、%X%）不算。"""
    found = []
    for _raw, p in _walk(command, cwd):
        if p not in found:
            found.append(p)
    return found


def glob_prefixes(command, cwd):
    """含萬用字元（* ? [ {）的 token，回萬用字元前最後一個資料夾（正規化）。shell 會展開它，守門要看它蓋到哪。"""
    out = []
    for raw, _p in _walk(command, cwd):
        m = _GLOB.search(raw)
        if not m:
            continue
        head = raw[:m.start()].replace("\\", "/")
        head = head.rsplit("/", 1)[0] if "/" in head else "."
        out.append(normalize(head, cwd) if head != "." else normalize(cwd, None))
    return out


def is_write(command):
    """引號外有 > 或寫入動詞；有包裝指令（bash -c、cmd /c、powershell -c…）時整句都看；程式碼裡的 open(…,'w') 之類引號內也算。
    導到 null 不算。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    bare = outside_quotes(cleaned)
    scope = cleaned if (bare is None or WRAPPER.search(bare)) else bare
    return bool(REDIRECT.search(scope) or WRITE_WORDS.search(scope) or WRITE_CODE.search(cleaned))


def is_single_plugin_call(command, plugin_root, cwd=None):
    """整句是不是只有「python <plugin>/scripts/kb.py …」這一個呼叫。串接、導向、反引號、$( ) 都不算。"""
    if "`" in command or "$(" in command:
        return False
    bare = outside_quotes(command)
    if bare is None or any(ch in SHELL_META for ch in bare):
        return False
    m = _PYTHON.match(command)
    if not m:
        return False
    script = m.group(1).strip("\"'")
    cwd = cwd or os.getcwd()
    return normalize(script, cwd) == normalize(os.path.join(str(plugin_root), KB_SCRIPT), cwd)
