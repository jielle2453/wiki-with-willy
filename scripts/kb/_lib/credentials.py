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
import fnmatch
import os
import re

CREDENTIAL_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.ppk", "*.kdbx", "id_rsa*", "id_ed25519*",
    "*password*", "*passwd*", "*secret*", "*credential*", "*密碼*",
)


# 沒副檔名也算檔名的樣式（金鑰檔慣例沒有副檔名）
KEYFILE_PATTERNS = ("id_rsa*", "id_ed25519*")
# 遮值關鍵詞（第 5b 節）：關鍵詞留著，後面的值換成 ███。長的排前面，password 不被 pw 搶走
SECRET_WORDS = ("密碼", "帳密", "帳號", "password", "passwd", "pw", "token", "金鑰", "secret", "api key", "apikey")
MASK = "███"
_WORDS_ALT = "|".join(re.escape(w) for w in sorted(SECRET_WORDS, key=len, reverse=True))
_PUNCT = "，。；、,;!?！？\n"
# 關鍵詞、連接（是／為／:／：／=／空白任意組合）、值（到下一個標點、下一個關鍵詞或句尾之前）
_SECRET = re.compile(
    rf"(?P<kw>{_WORDS_ALT})(?P<sep>[是為:：=\s]*)(?P<val>[^{_PUNCT}]*?)(?=\s*(?:{_WORDS_ALT}|[{_PUNCT}]|$))",
    re.IGNORECASE,
)


def mask_secrets(text):
    """不分大小寫找關鍵詞，把它後面的值換成 ███；關鍵詞本身留著，連接只是空白時保留一個空白。沒命中原樣回。"""
    if not text:
        return text

    def repl(m):
        if not m.group("val"):
            return m.group(0)
        sep = " " if m.group("sep") and m.group("sep").isspace() else ""
        return m.group("kw") + sep + MASK

    return _SECRET.sub(repl, str(text))


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
