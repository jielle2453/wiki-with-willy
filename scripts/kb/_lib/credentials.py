"""憑證檔名樣式（D-079、D-079 補、D-081、I-032）：樣式寫死在這一份，不讀 config、不讀客戶設定；預設擋，例外是個案裁示（D-081）。
守門與 kb folders 共用。

match_segment：一串路徑段任一段對任一樣式 fnmatch（小寫比對）就回那一段。
scan_dir：守門對「讀取範圍是資料夾」走訪找憑證檔名，容錯（os.walk onerror 略過、不跟隨連結，I-021），
夾名與檔名都比，命中回第一個命中的相對路徑；走訪項數超過 max_entries 就停、truncated=True（fail closed 由呼叫端擋）；
skip 裡的夾與檔（排除項目、總資料夾整棵）不進去、不比、不算。
scan_all：kb folders add 用，收全部命中、數檔案份數，碰到含 stop_marker 的夾就停（另一個知識庫）。
mask_secrets：回合 prompt_head 的遮值（第 5b 節、B-027），關鍵詞後的值換成 ███。
不含「憑證」：會計憑證、報關憑證是客戶的正常文件。寧可多擋、多遮（D-034，誤擋率併 B-110）。
"""
import bisect
import fnmatch
import os
import re

CREDENTIAL_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.ppk", "*.kdbx", "id_rsa*", "id_ed25519*",
    "*password*", "*passwd*", "*secret*", "*credential*", "*密碼*",
)


# 沒副檔名也算檔名的樣式（金鑰檔慣例沒有副檔名）
KEYFILE_PATTERNS = ("id_rsa*", "id_ed25519*")
# 遮值關鍵詞（第 5b 節、第五輪 V4）。比對前先 NFKC、小寫、去空白（D-187），所以 api key 與 apikey 同一個
SECRET_WORDS = ("密碼", "帳密", "帳號", "password", "passwd", "pw", "token", "金鑰", "secret", "api key", "apikey",
                "api_key", "api-key", "密码", "passphrase", "pin", "pin碼", "驗證碼", "otp")
MASK = "███"
# 短字要整字邊界（typing 裡的 pin、stopwatch 裡的 pw 不算，看原字串的前後字）；長字允許前綴（PGPASSWORD=、accessToken=）
SHORT_WORDS = ("pw", "pin", "otp")
# 帳密、帳號密碼後面可以跟兩個值（帳號、密碼各一）
PAIR_WORDS = ("帳密",)
# 程式固定句（D-187 (c)）：裡面的關鍵字不遮。只遮值之後這些本來就不會被遮，列著當保險、也給測試守
SAFE_PHRASES = (
    "讀不到：有密碼", "檔名像密碼或金鑰", "（這句回答有密碼，沒記）", "這組密碼沒說過可以用在別的檔", "密碼不對",
    "這台電腦不是 Windows，存不了密碼", "給我密碼就能打開，要我記住也可以", "不製卡：檔名像密碼或金鑰",
    "裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員",
    "需要客戶按允許才會執行",  # 守門 ask 的前綴（D-212）；客戶貼回時不能被當成值（確認落差 A）
)
_SPACE = " \t　"
# 關鍵字到值之間（D-187 (b)）：可以先有幾個虛字（我設成、都用、分別是），再一個以上的分隔；或只有空白（單一換行也算）
_FILLER = r"(?:我|他|她|你|妳|們|的|都|已經|現在|新|預設|分別|一律|就|先|要)"
_SEP = r"(?:[:：=＝]|如下|是|為|为|設成|設為|設定成|設定為|改成|改為|換成|換為|用|叫做|叫|(?:is|are|was)(?=\s))"
_CONNECT = re.compile(rf"[{_SPACE}]*(?:{_FILLER}[{_SPACE}]*)*(?:{_SEP}[{_SPACE}]*\n?[{_SPACE}]*)+|[{_SPACE}]+|[{_SPACE}]*\n[{_SPACE}]*",
                      re.IGNORECASE)
_STOP_CHARS = "，。、；！？：「」『』（）【】〈〉《》…"
_VALUE = re.compile(rf"\"[^\"\n]+\"|'[^'\n]+'|「[^」\n]+」|[^\s{_STOP_CHARS}]+")
# 值是疑問詞（密碼是多少）不遮；Bearer、Basic 後面那個才是值
_QUESTION = re.compile(r"^(?:多少|什麼|什么|啥|幾|几|哪個|哪个|呢|嗎|吗|\?|？)")
_SCHEMES = ("bearer", "basic")
# 值的位置先是 markdown 記號（## 結論、- 、>）：往後最多看 _SKIP_MAX 個字，遮第一個不含中日韓字的（工作日誌抽結論段前的保險）
_MARKUP = re.compile(r"^[#>*\-|=_`~]+$")
_SKIP_MAX = 3
# 兩個值之間（帳號密碼分別是 jie、abc123）；第二個值不含中日韓字（「密碼 abc、之後再改」的之後不算值）
_NEXT_VALUE = re.compile(rf"(?:[{_SPACE}]*[、，,/／][{_SPACE}]*|[{_SPACE}]+)(?P<v>[^\s{_STOP_CHARS}㐀-鿿]+)")
# 值在前（D-187 (b)）：「<值> 是他的密碼」「hunter2 is my password」，<值> 緊接在這段之前。
# 測試 28 F4：在反轉的字串上從關鍵字的位置往前比（字也反著寫），一個關鍵字只看它前面一小段，整句是線性時間
_VALUE_MAX = 256
_VALUE_FIRST_REV = re.compile(rf"(?:[{_SPACE}]*(?:他|她|我|你|妳|們|的|ym|sih|reh|eht|ruo|ruoy))*[{_SPACE}]*(?:是|si)[{_SPACE}]*"
                              rf"(?P<v>[^\s{_STOP_CHARS}]{{1,{_VALUE_MAX}}})", re.IGNORECASE)
_NEGATION = ("不", "並非", "并非")  # 值在前的「<值>是密碼」前一個字是否定時不遮
_RUN = re.compile(rf"[^\s{_STOP_CHARS}]+")
_BLANK = re.compile(r"\s*")
_BLANK_SPACE = re.compile(rf"[{_SPACE}]*")
# kb 指令的值旗標：被守門擋下時 ledger 記指令開頭（密碼與守門開工前補第 2 條）
_FLAG_VALUE = re.compile(r"(?P<kw>--(?:value|password))(?P<sep>=|\s+)(?P<v>\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
_MERGE_GAP = re.compile(rf"^[{_SPACE}和與及跟、/／]*$")


def _norm_key(word):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKC", word).casefold() if not ch.isspace())


_KEYWORDS = sorted({_norm_key(w) for w in SECRET_WORDS}, key=len, reverse=True)
_SHORT = {_norm_key(w) for w in SHORT_WORDS}
_KW = re.compile("|".join(re.escape(w) for w in _KEYWORDS))


def _normalized(text):
    """回 (比對用字串, 每個字對回原字串的位置)：每個字 NFKC、小寫，空白丟掉（D-187 (a)）。"""
    import unicodedata
    chars, where = [], []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if not c.isspace():
                chars.append(c)
                where.append(i)
    return "".join(chars), where


def _spans(text, needles):
    out = []
    for needle in needles:
        if not needle:
            continue
        start = text.find(needle)
        while start >= 0:
            out.append((start, start + len(needle)))
            start = text.find(needle, start + 1)
    return out


def _inside(spans, a, b):
    """[a, b) 跟任何受保護的區段重疊。"""
    return any(a < e and s < b for s, e in spans)


def _keywords(text, norm, where):
    """回 [(原字串起點, 原字串終點, 可以跟幾個值)]：相鄰（中間只有空白或和、與、、、/）的關鍵字併成一個。"""
    found = []
    for m in _KW.finditer(norm):
        a, b = where[m.start()], where[m.end() - 1] + 1
        if m.group() in _SHORT:
            before = text[a - 1] if a > 0 else ""
            after = text[b] if b < len(text) else ""
            if (before.isascii() and before.isalnum()) or (after.isascii() and after.isalpha()):
                continue
        values = 2 if m.group() in PAIR_WORDS else 1
        if found and _MERGE_GAP.match(text[found[-1][1]:a]):
            pa, _pb, pv = found[-1]
            found[-1] = (pa, b, pv + values)
        else:
            found.append((a, b, values))
    return found


def _after_markup(text, pos):
    """值的位置是 markdown 記號時，往後找第一個不含中日韓字、也不是記號的字；找不到回 None。"""
    for _ in range(_SKIP_MAX):
        start = _BLANK.match(text, pos).end()
        tok = _VALUE.match(text, start)
        if not tok:
            return None
        if not _MARKUP.match(tok.group()) and not re.search("[㐀-鿿]", tok.group()):
            return tok
        pos = tok.end()
    return None


INVENTORY_POSIX = ".system/inventory.json"
_NAMES_CACHE = {}


def known_names(vault):
    """清冊裡的檔名（D-187 (c)）：只回含遮值關鍵字的（其他的本來就不會被遮），給日誌與回合的遮值當不遮的字。
    直接讀 inventory.json（inventory 會 import 這裡，不能反過來）；讀不到回空清單（照舊遮）。照修改時間快取。"""
    import json
    if not vault:
        return []
    p = os.path.join(str(vault), *INVENTORY_POSIX.split("/"))
    try:
        stamp = os.stat(p).st_mtime_ns
    except OSError:
        return []
    hit = _NAMES_CACHE.get(p)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(p, encoding="utf-8") as f:
            entries = json.load(f).get("entries") or []
        names = {str(loc.get("uri", "")).rsplit("/", 1)[-1] for e in entries if isinstance(e, dict)
                 for loc in e.get("locations") or [] if isinstance(loc, dict)}
    except (OSError, ValueError, AttributeError):
        return []
    out = sorted((n for n in names if n and _KW.search(_norm_key(n))), key=len, reverse=True)
    _NAMES_CACHE[p] = (stamp, out)
    return out


def mask_secrets(text, known_names=None):
    """只遮值本身（D-187）：關鍵字（NFKC、去空白比對）後接分隔的那一段換成 ███，到下一個空白或標點為止；
    「<值> 是他的密碼」遮 <值>；kb 指令的 --value、--password 遮後面那個參數。
    known_names（清冊裡的檔名）與 SAFE_PHRASES 裡的字不遮。沒命中原樣回；空字串、None 原樣回。"""
    if not text:
        return text
    text = str(text)
    protected = _spans(text, list(known_names or ()) + list(SAFE_PHRASES))
    cuts = []

    def cut(a, b):
        if b > a and not _inside(protected, a, b):
            cuts.append((a, b))

    for m in _FLAG_VALUE.finditer(text):
        cut(m.start("v"), m.end("v"))
    norm, where = _normalized(text)
    rev, n, runs = text[::-1], len(text), None
    done = 0
    for a, b, values in _keywords(text, norm, where):
        if a < done or _inside(protected, a, b):
            continue
        before = _VALUE_FIRST_REV.match(rev, n - a)
        if before:
            va, vb = n - before.end("v"), n - before.start("v")
            v = text[va:vb]
            # 「都不是密碼」「不是密碼」是否定，不是值（確認落差 B）
            if not v.endswith(_NEGATION) and not _QUESTION.match(v) and not _KW.search(v.casefold()) and not _KW.search(_norm_key(v)):
                if before.end("v") - before.start("v") == _VALUE_MAX:  # 很長的字：往前補到字的開頭（字的起點只算一次）
                    runs = runs if runs is not None else [m.start() for m in _RUN.finditer(text)]
                    va = runs[bisect.bisect_right(runs, va) - 1]
                cut(va, vb)
        link = _CONNECT.match(text, b)
        if not link or link.end() == b:
            continue
        pos = link.end()
        value = _VALUE.match(text, pos)
        if value and _MARKUP.match(value.group()):
            value = _after_markup(text, value.end())
        if not value or _QUESTION.match(value.group()):
            continue
        end = value.end()
        if value.group().casefold() in _SCHEMES:  # Bearer xyz：連後面那個一起
            nxt = _VALUE.match(text, _BLANK_SPACE.match(text, end).end())
            end = nxt.end() if nxt else end
        cut(pos, end)
        for _ in range(values - 1):
            more = _NEXT_VALUE.match(text, end)
            if not more:
                break
            cut(more.start("v"), more.end("v"))
            end = more.end("v")
        done = end
    if not cuts:
        return text
    out, last = [], 0
    for a, b in sorted(cuts):
        if a < last:
            a = last
        if b <= a:
            continue
        out.append(text[last:a] + MASK)
        last = b
    return "".join(out) + text[last:]


def exempt_roots():
    """不比憑證樣式的夾（B-088、B-093 (a)）：produce 的工具夾（%LOCALAPPDATA%\\wiki-with-willy\\tools，照 office.tools_dir 的算法，
    每次從環境變數算）、plugin 自己的夾、~/.claude/plugins。回 normalize 過的路徑。"""
    from kb._lib.paths import normalize, plugin_root
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    roots = [os.path.join(base, "wiki-with-willy", "tools"), os.path.join(os.path.expanduser("~"), ".claude", "plugins")]
    try:
        roots.append(str(plugin_root()))
    except FileNotFoundError:
        pass
    return [normalize(r, None) for r in roots]


def exempt(path):
    """normalize 過的 path 在 exempt_roots 底下。"""
    from kb._lib.paths import inside
    return any(inside(path, r) for r in exempt_roots())


def looks_like_filename(segment):
    """指令裸 token 收窄用：含 . 的，或命中沒副檔名的金鑰樣式；passwords、secret 這種單字不算檔名。"""
    s = str(segment)
    return "." in s or any(fnmatch.fnmatchcase(s.lower(), pat) for pat in KEYFILE_PATTERNS)


def match_segment(segments):
    """任一段命中就回那一段（原字串）；沒有回 None。"""
    for seg in segments:
        if not seg:
            continue
        low = str(seg).lower()
        if any(fnmatch.fnmatchcase(low, pat) for pat in CREDENTIAL_PATTERNS):
            return seg
    return None


def _key(path):
    """skip 清單的比對鍵：正斜線，Windows 小寫（跟 paths.normalize 的結果同形，但不走 realpath，走訪 5000 項不能每項一次系統呼叫）。"""
    s = str(path).replace("\\", "/").rstrip("/")
    return s.lower() if os.name == "nt" else s


def _scan(path, max_entries, first_only, stop_marker, skip=()):
    """回 (hits, files, truncated, marker_dir)。命中的夾不再往下走（整夾都算憑證）；skip 裡的夾（排除夾）整個不進去、不比。"""
    root = str(path)
    hits, files, seen = [], 0, 0
    marker = stop_marker.replace("\\", "/").split("/") if stop_marker else None
    skip_keys = {_key(s) for s in skip}

    def onerror(_e):
        pass

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        if marker and os.path.isfile(os.path.join(dirpath, *marker)):
            return hits, files, False, dirpath
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for name in list(dirnames):
            if skip_keys and _key(os.path.join(dirpath, name)) in skip_keys:
                dirnames.remove(name)  # 排除夾、總資料夾整棵：不進去、不比、不算項數
                continue
            seen += 1
            if max_entries is not None and seen > max_entries:
                return hits, files, True, None
            if match_segment((name,)) is not None:
                hits.append(prefix + name)
                dirnames.remove(name)
                if first_only:
                    return hits, files, False, None
        for name in filenames:
            if skip_keys and _key(os.path.join(dirpath, name)) in skip_keys:
                continue  # 排除的單檔：不比、不算
            seen += 1
            if max_entries is not None and seen > max_entries:
                return hits, files, True, None
            files += 1
            if match_segment((name,)) is not None:
                hits.append(prefix + name)
                if first_only:
                    return hits, files, False, None
    return hits, files, False, None


def scan_dir(path, max_entries, skip=()):
    """回 (hit, truncated)：hit 是第一個命中的相對路徑（正斜線）或 None。skip 是不進去的夾（排除夾，N4），正規化過的路徑。"""
    hits, _files, truncated, _marker = _scan(path, max_entries, True, None, skip)
    return (hits[0] if hits else None), truncated


def scan_all(path, max_entries=None, stop_marker=None):
    """回 (hits, files, truncated, marker_dir)：全部命中的相對路徑、檔案份數、有沒有截斷、含標記檔的夾（沒有就 None）。"""
    return _scan(path, max_entries, False, stop_marker)
