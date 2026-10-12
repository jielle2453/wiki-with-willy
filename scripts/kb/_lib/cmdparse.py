"""指令字串解析：抽路徑、判寫入、判「整句只有一個 kb.py 呼叫」。

字串比對是第二道防線：刻意把路徑拆開的指令擋不住（I-031），所以守門對抽不到路徑的寫入一律擋（unparsed）。
照舊 guard.py 的 outside_quotes、norm_cmd、WRITE_WORDS、REDIRECT、DISCARD_OUTPUT、single_plugin_call 改介面。
"""
import os
import re

from kb._lib.paths import normalize

KB_SCRIPT = "scripts/kb.py"

# 只是把輸出丟掉，不算寫入：2>/dev/null、>/dev/null、2>&1、> nul、> $null、| Out-Null
DISCARD_OUTPUT = re.compile(r"\d?>\s*&\s*\d|\d?>\s*/dev/null|\d?>\s*\$null|\d?>\s*nul\b|\|\s*out-null", re.IGNORECASE)
# 引號外的寫入動詞（引號裡的是文字，例如 commit 訊息）
WRITE_WORDS = re.compile(
    r"(\b(?:rm|mv|cp|tee|touch|mkdir|rmdir|del|erase|move|copy|ren|rename|truncate)\b|\bsed\s+-[a-zA-Z]*i|"
    r"remove-item|move-item|copy-item|rename-item|new-item|set-content|add-content|out-file|clear-content)",
    re.IGNORECASE,
)
# 程式碼裡的寫入（python -c、inline script）：引號內也算
WRITE_CODE = re.compile(
    r"(open\([^)]*['\"][wax+]|\.write_text|\.write_bytes|writealltext|writeallbytes|shutil\.|os\.remove|"
    r"os\.rename|os\.unlink|os\.makedirs|os\.mkdir|\bunlink\()",
    re.IGNORECASE,
)
REDIRECT = re.compile(r">")
# 引號外不能出現的字元：串接（; & |）、導向（< >）、換行；反引號與 $( 在引號裡也會執行，一律不行
SHELL_META = set(";&|<>\n\r")
_TOKEN_BREAK = set(";|&<>()")
_URL = re.compile(r"^\w+://")
_OPT_PREFIX = re.compile(r"^--?[\w-]+=")
_FILENAME = re.compile(r"\.\w{1,6}$")
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


def _looks_like_path(text, quoted, after_redirect):
    if not text or "$" in text or "%" in text or _URL.match(text):
        return False
    if "/" in text or "\\" in text:
        return not (text.startswith("-") and not _OPT_PREFIX.match(text))
    if after_redirect:
        return True
    return quoted and bool(_FILENAME.search(text))


def extract_paths(command, cwd):
    """抽引號內字串、含 / 或 \\ 的 token、導向目標，去掉符號後正規化。變數（$X、%X%）不算路徑。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    found = []
    for text, quoted, after_redirect in _tokens(cleaned):
        if not _looks_like_path(text, quoted, after_redirect):
            continue
        text = _OPT_PREFIX.sub("", text)
        p = normalize(text, cwd)
        if p not in found:
            found.append(p)
    return found


def is_write(command):
    """引號外有 > 或寫入動詞；程式碼裡的 open(…,'w') 之類引號內也算。導到 null 不算。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    bare = outside_quotes(cleaned)
    scope = cleaned if bare is None else bare
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
