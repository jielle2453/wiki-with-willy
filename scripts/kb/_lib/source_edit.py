"""kb source-edit 的本體（manage 第二版，D-173～D-178）：幫客戶搬檔、改檔名、丟資源回收筒、推薦去處、開檔案總管。

准許的位置 kb 自己查（守門對單一 kb 呼叫直接放行，照 convert.gate 的寫法）：
- 從哪裡搬：登記且沒設成不讀的來源（Z9，來源根本身不收）、實體夾裡綜合卡以外的檔（Z2，整個實體夾不收）、AI 產出（Z3）、收件匣（Z4）；
- 搬到哪裡：登記且沒設成不讀的來源裡已存在的夾、實體夾（<型別>/<名稱>/ 與底下的夾；不能叫做跟綜合卡同名的 .md）；
- 不收：排除的（Z10）、知識庫外沒登記的（Z11）、.system、系統、檔案索引、型別夾根、知識庫根、檔名像密碼或金鑰的（憑證樣式，
  總資料夾內部豁免，D-081）。
同一顆硬碟（上層夾的 st_dev 相同）才代搬，用一次 os.rename：全有或全無；Windows 有檔開著丟 PermissionError 就什麼都沒動。
搬完跟上（B-095）：先在回合夾 moves.jsonl 記一行（清冊 uri，D-175），清冊直接換位置（不靠掃描認搬動）、夾裡不讀的子夾 spec 改寫，
再掃那幾個根補大小與時間、cards.sync 搬卡、重寫總目錄與綜合卡快取。全部先備進回合。
丟資源回收筒（D-173）用 ctypes 叫 SHFileOperationW；放不下（不跳視窗的呼叫會直接永久刪）或回收筒用不了就拒絕，不改成直接刪。
OneDrive（B-095）：只在雲端看 FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS，在 OneDrive 裡看 %OneDrive% 那三個環境變數。
外部呼叫（os.rename、回收筒、os.startfile、屬性、磁碟種類）集中在模組層的幾個名字，測試換替身。
第一輪修（測試報告）：夾裡不讀的子夾 spec 先寫成新舊兩條都在、再改名、最後拿掉舊的，不讀的夾沒有一刻變成可讀；
改名之後任何一步失敗就把改名與改過的檔倒回去（倒不回才說「已搬好，但後面沒做完」）；--replace 先把這份移到目標夾裡的
暫名（驗過改名做得到）才丟目標；丟回收筒只收固定磁碟、唯讀另一句；新名字或目標夾是 .、~$ 開頭或隱藏的拒絕；
副檔名照已知清單判，其他自動補原副檔名；給 AI 跑的指令遇到 $、反引號改用單引號。
"""
import copy
import os
import re
import secrets
from pathlib import Path

from kb import guard
from kb._lib import credentials, inventory, turn
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, write_json_atomic
from kb._lib.paths import inside, normalize, resolve_spec

ID = re.compile(r"^(f|out)-[0-9a-f]+$")
RECALL_ON_DATA_ACCESS = 0x400000
ONEDRIVE_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
OPEN_MAX = 2
BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
SIGNS_MAX = 3
BAD_NAME_CHARS = '\\/:*?"<>|'

# 拒絕句（exit 3，一字不差；spec 與 a2 回覆的疑義第 1 條）
EXISTS = "目標已經有「{name}」"
OPEN = "有檔開著（{name}），關掉再試"
OTHER_DISK = "不同顆硬碟不代搬；可以幫你打開兩個夾自己拖：{cmd}"
OTHER_DISK_NO_CMD = "不同顆硬碟不代搬，請在檔案總管自己拖；夾名有特殊符號，開夾的指令給不出來，要我幫你開請先改夾名"
UNREGISTERED = "「{name}」沒有登記，要先把它加進來才能搬過去"
EXCLUDED = "「{name}」設成不讀，不搬進去也不搬出來"
CREDENTIAL = "這個檔名像密碼或金鑰，不動"
ENTITY_FOLDER = "整個實體夾的改名或換型別用 kb entity"
TYPE_FOLDER = "整個型別夾的改名用 kb profile type"
NOT_FOUND = "找不到這個檔：{target}"
NO_FOLDER = "找不到這個夾：{target}"
LOCKED = "等不到鎖，沒有動"
SYSTEM = "「{name}」是知識庫自己用的地方，不搬進去也不搬出來"
NOT_INTO = "不搬進「{name}」；要放哪個夾跟我說"
SOURCE_ROOT = "整個登記的資料夾換位置用 kb folders relocate"
SYNTHESIS_NAME = "「{name}」跟綜合卡同名，換個名字"
EXTENSION = "副檔名不改：{old} → {new}"
BAD_NAME = "新名字不能是空的，也不能含 \\ / : * ? \" < > |"
SAME_PLACE = "「{name}」已經在「{dest}」裡了"
INTO_ITSELF = "不能把夾搬進它自己裡面"
TRASH_FOLDER = "整個夾請在檔案總管刪"
BIN_UNAVAILABLE = "資源回收筒用不了（{why}），沒有刪"
BIN_TOO_BIG = "這份 {size}，資源回收筒放不下，刪了救不回來；要刪請在檔案總管自己刪"
OPEN_TOO_MANY = "一次最多開兩個夾"
READONLY = "這份是唯讀的，先取消唯讀再刪"
HIDDEN_NAME = "這種名字知識庫不會讀，換個名字"
NOT_MOVED = "沒有動：{why}"
PARTIAL = "已搬好，但後面沒做完；說「退回」可以退回"
REPLACED_NOT_MOVED = "原本的「{name}」已經丟進資源回收筒，要從那裡拿回來；這份沒搬過去（{why}），還在原位"
REPLACED_STUCK = "原本的「{name}」已經丟進資源回收筒，要從那裡拿回來；這份搬到一半，請服務人員看"
REMOVABLE = "隨身碟或網路磁碟"
DEVICE_NAME = "這種名字 Windows 不收，換個名字"
CURLY_QUOTES = frozenset(chr(c) for c in range(0x2018, 0x2020))
# Windows 保留裝置名（帶副檔名也算）：建得出來卻打不開、刪不掉
DEVICE_NAMES = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)}
                | {f"LPT{i}" for i in range(1, 10)} | {f"{p}{s}" for p in ("COM", "LPT") for s in "¹²³"})
DRIVE_FIXED = 3
# 已知副檔名（kb 認得的加上常見的 Office、圖、影音）：新名字的尾巴是這些之一又跟原本不同，才算要換副檔名（拒絕）
KNOWN_EXTS = {
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".csv", ".ppt", ".pptx",
    ".pptm", ".pps", ".ppsx", ".pot", ".potx", ".pdf", ".txt", ".md", ".rtf", ".odt", ".ods", ".odp", ".pages", ".numbers",
    ".key", ".json", ".xml", ".html", ".htm", ".eml", ".msg", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".webp", ".svg", ".raw", ".psd", ".ai", ".dwg", ".dxf", ".skp", ".mp3", ".wav", ".m4a", ".aac",
    ".flac", ".wma", ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".zip", ".rar", ".7z"}
NO_ENTITY = "實體清單沒有「{name}」，先 kb entity add"


class Refused(Exception):
    """給人看的那句就是訊息。"""


class BinUnavailable(Exception):
    """資源回收筒用不了：訊息是括號裡的原因。"""


# ---------- 外部呼叫（測試換替身） ----------

def _rename(src, dst):
    os.rename(src, dst)


def _startfile(path):
    os.startfile(path)  # noqa: S606  只開檔案總管，不跑檔


def _attrs(path):
    try:
        return getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return 0


def _dev(path):
    return os.stat(path).st_dev


def _drive_type(path):
    """GetDriveTypeW：3 是固定磁碟；隨身碟（2）、網路磁碟（4）丟回收筒不一定進得去（FOF_ALLOWUNDO 會直接永久刪）。"""
    if os.name != "nt":
        return DRIVE_FIXED
    import ctypes
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0] + "\\"
    return int(ctypes.windll.kernel32.GetDriveTypeW(drive))


def shell_arg(text):
    """給 AI 跑的指令裡的一個參數：一般用雙引號；有 $ 或反引號改單引號；同時又有單引號、或有彎引號（PowerShell 當引號）回 None。"""
    text = str(text)
    if set(text) & CURLY_QUOTES:
        return None
    if "$" in text or "`" in text:
        return None if "'" in text else f"'{text}'"
    return f'"{text}"'


def other_disk(src_dir, dst_dir):
    a, b = shell_arg(_real(src_dir)), shell_arg(_real(dst_dir))
    if a is None or b is None:
        return OTHER_DISK_NO_CMD
    return OTHER_DISK.format(cmd=f"kb source-edit open {a} {b}")


def recycle_room(path):
    """回 (回收筒已用位元組, 上限位元組)；用不了丟 BinUnavailable。上限讀登錄 BitBucket\\Volume\\{GUID} 的 MaxCapacity（MB），
    NukeOnDelete=1 當用不了；沒有這個鍵用磁碟總量的 5%（Windows 預設）。已用從 SHQueryRecycleBinW 取，它失敗（網路磁碟）也算用不了。"""
    if os.name != "nt":
        raise BinUnavailable("不是 Windows")
    import ctypes
    import shutil
    import winreg
    from ctypes import wintypes
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0] + "\\"

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    if ctypes.windll.shell32.SHQueryRecycleBinW(drive, ctypes.byref(info)) != 0:
        raise BinUnavailable("這顆磁碟沒有資源回收筒")
    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(drive, buf, len(buf)):
        raise BinUnavailable("查不到這顆磁碟")
    guid = buf.value[buf.value.find("{"):buf.value.find("}") + 1]
    if not guid:
        raise BinUnavailable("查不到這顆磁碟")
    cap = None
    try:
        key = BITBUCKET + "\\" + guid
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            try:
                if winreg.QueryValueEx(k, "NukeOnDelete")[0] == 1:
                    raise BinUnavailable("資源回收筒設成直接刪除")
            except FileNotFoundError:
                pass
            try:
                cap = int(winreg.QueryValueEx(k, "MaxCapacity")[0]) * 1024 * 1024
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    if cap is None:
        cap = shutil.disk_usage(drive).total // 20
    return int(info.i64Size), cap


def shell_trash(path):
    """SHFileOperationW FO_DELETE、FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT|FOF_NOERRORUI：丟資源回收筒。失敗丟 BinUnavailable。"""
    if os.name != "nt":
        raise BinUnavailable("不是 Windows")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = os.path.abspath(str(path)) + "\0"  # 結尾要兩個 \0：ctypes 自己再補一個
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0 or op.fAnyOperationsAborted:
        raise BinUnavailable(f"代碼 {code}")
    if os.path.exists(path):
        raise BinUnavailable("檔還在原位")


# ---------- 位置 ----------

def _real(path):
    """存在的部分用磁碟上的大小寫，不存在的尾段原樣接回（folders._realcase 同一套，回原生分隔）。"""
    cur, tail = os.path.abspath(str(path)), []
    while not os.path.exists(cur):
        head, name = os.path.split(cur)
        if not name or head == cur:
            return os.path.abspath(str(path))
        tail.insert(0, name)
        cur = head
    real = os.path.realpath(cur)
    return os.path.join(real, *tail) if tail else real


def _parts(vault, path):
    """總資料夾裡的相對段（原始大小寫）；不在裡面回 None，是根本身回 []。"""
    v, p = _real(vault), _real(path)
    vn, pn = normalize(v, None), normalize(p, None)
    if not inside(pn, vn):
        return None
    if pn == vn:
        return []
    return os.path.relpath(p, v).replace("\\", "/").split("/")


def _source_of(sources, path):
    """包住 path 的登記來源（最長的根）；回 Sources 的條目或 None。"""
    pn = normalize(str(path), None)
    best = None
    for src in sources.entries:
        if inside(pn, src["root"]) and (best is None or len(src["root"]) > len(best["root"])):
            best = src
    return best


def uri_of(vault, sources, path):
    """磁碟路徑 → 清冊 uri，大小寫照磁碟（不存在的尾段照給的）。三區照 inventory.uri_of 認；不屬於任何根回 None。"""
    parts = _parts(vault, path)
    if parts is not None:
        if not parts:
            return None
        first = parts[0].casefold()
        for alias in (inventory.INBOX, inventory.OUTPUT):
            if first == alias.casefold():
                return inventory.make_uri(alias, "/".join(parts[1:])) if len(parts) > 1 else f"file://{alias}"
        return inventory.make_uri(inventory.KNOWLEDGE, "/".join(parts))
    src = _source_of(sources, path)
    if src is None:
        return None
    rel = os.path.relpath(_real(path), _real(src["root"])).replace("\\", "/")
    return f"file://{src['alias']}" if rel == "." else inventory.make_uri(src["alias"], rel)


def _types(vault):
    from kb._lib import profile
    try:
        data = profile.load(vault) or {}
    except ValueError:
        data = {}
    return {str(t.get("name", "")).casefold() for t in data.get("types") or []}


def _zone(vault, sources, path):
    zone, _alias = guard.classify(normalize(str(path), None), normalize(str(vault), None), sources)
    if zone == "Z10b":  # 底下有不讀的子夾：本身是登記的就照登記的看（搬整個夾時 spec 跟著改寫）
        status, _src = sources.lookup(normalize(str(path), None))
        zone = "Z9" if status == "registered" else "Z11"
    return zone


def _credential(sources, path):
    src = _source_of(sources, path)
    if src is None:
        return False
    pn = normalize(str(path), None)
    segs = pn[len(src["root"]):].strip("/").split("/")
    return credentials.match_segment(segs) is not None


def credential_inside(vault, path):
    """夾底下有檔名像密碼或金鑰的檔或夾（走訪有上限，走不完也算有）：整個夾搬進知識庫前查，
    因為知識庫裡不查憑證樣式（D-081），搬進去守門就不擋了（收尾 security-review）。"""
    from kb._lib import config
    hit, truncated = credentials.scan_dir(path, config.get(vault, "credential_scan_max_entries"))
    return hit is not None or truncated


def check_from(vault, sources, path):
    """要動的檔或夾在准許的位置嗎；不行丟 Refused。"""
    path = Path(path)
    name = path.name
    parts = _parts(vault, path)
    if parts == []:
        raise Refused(SYSTEM.format(name=inventory.KNOWLEDGE))
    zone = _zone(vault, sources, path)
    if zone == "Z10":
        raise Refused(EXCLUDED.format(name=name))
    if zone == "Z11":
        raise Refused(UNREGISTERED.format(name=name))
    if zone == "Z9":
        if any(normalize(str(path), None) == s["root"] for s in sources.entries):
            raise Refused(SOURCE_ROOT)
        if _credential(sources, path):
            raise Refused(CREDENTIAL)
        return
    if zone == "Z2":
        if len(parts) == 2 and path.is_dir():
            raise Refused(ENTITY_FOLDER)
        return
    if zone in ("Z3", "Z4") and len(parts) > 1:
        return
    if zone == "Z8" and len(parts) == 1 and path.is_dir() and parts[0].casefold() in _types(vault):
        raise Refused(TYPE_FOLDER)
    raise Refused(SYSTEM.format(name=name if zone == "Z1" else parts[0]))


def check_to(vault, sources, dest, target_text):
    """目的地夾要存在、在准許的位置；不行丟 Refused。"""
    dest = Path(dest)
    if not dest.is_dir():
        raise Refused(NO_FOLDER.format(target=target_text))
    parts = _parts(vault, dest)
    if parts == []:
        raise Refused(SYSTEM.format(name=inventory.KNOWLEDGE))
    zone = _zone(vault, sources, dest)
    if zone == "Z10":
        raise Refused(EXCLUDED.format(name=dest.name))
    if zone == "Z11":
        raise Refused(UNREGISTERED.format(name=dest.name))
    if zone in ("Z9", "Z2"):
        if _unread_dir(vault, sources, dest, parts):
            raise Refused(HIDDEN_NAME)
        return
    if zone in ("Z3", "Z4"):
        raise Refused(NOT_INTO.format(name=parts[0]))
    raise Refused(SYSTEM.format(name=parts[0]))


def _alias_form(vault, sources, text):
    """「別名/相對路徑」（kb source-edit where 印的、知識庫/…、AI 產出/…）：第一段是登記的別名或三區就照 uri 解，否則回 None。"""
    first = text.replace("\\", "/").split("/", 1)[0].casefold()
    names = {s["alias"].casefold() for s in sources.entries} | {a.casefold() for a in sources_lib.RESERVED_ALIASES}
    if first not in names:
        return None
    return inventory.path_of(vault, sources, "file://" + text.replace("\\", "/"))


def _unread_name(name):
    return name.startswith(".") or name.startswith("~$")


def _unread_dir(vault, sources, dest, parts):
    """目標夾（從它的根往下每一段）有 . 或 ~$ 開頭、或隱藏屬性的：清冊走訪會跳過，搬進去就變成「不見了」。"""
    if parts is not None:
        base, segs = Path(_real(vault)), parts
    else:
        src = _source_of(sources, dest)
        if src is None:
            return False
        base = Path(_real(src["root"]))
        segs = os.path.relpath(_real(dest), base).replace("\\", "/").split("/")
    cur = base
    for seg in segs:
        if seg in ("", "."):
            continue
        cur = cur / seg
        if _unread_name(seg) or _attrs(str(cur)) & 0x2:
            return True
    return False


def resolve(vault, sources, target, cwd):
    """目標三種寫法（同 kb convert）：清冊編號、uri、路徑。回 (條目或 None, 絕對路徑)；不存在丟 Refused。"""
    text = str(target).strip()
    entry, path = None, None
    if ID.match(text):
        entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == text), None)
        if entry is not None and entry.get("locations"):
            path = inventory.path_of(vault, sources, entry["locations"][0]["uri"])
    elif text.startswith("file://"):
        path = inventory.path_of(vault, sources, text)
    elif os.path.isabs(text):
        path = Path(text)
    else:
        path = _alias_form(vault, sources, text)
        if path is None or not os.path.exists(path):
            path = Path(cwd) / text
    if path is None or not os.path.exists(path):
        raise Refused(NOT_FOUND.format(target=text))
    path = Path(_real(path))
    if entry is None:
        uri = uri_of(vault, sources, path)
        entry = inventory.entry_for(vault, uri) if uri else None
    return entry, path


def resolve_folder(vault, sources, target, cwd):
    text = str(target).strip()
    if text.startswith("file://"):
        path = inventory.path_of(vault, sources, text)
    elif os.path.isabs(text):
        path = Path(text)
    else:
        path = _alias_form(vault, sources, text)
        if path is None or not os.path.isdir(path):
            path = Path(cwd) / text
    if path is None or not os.path.isdir(path):
        raise Refused(NO_FOLDER.format(target=text))
    return Path(_real(path))


# ---------- 給人看的 ----------

def folder_word(uri):
    """「從 A 搬到 B」的 A／B：uri 所在的夾名；在根上就是別名。"""
    alias, rel = inventory.split_uri(uri)
    parent = rel.rpartition("/")[0] if rel else ""
    return parent.rpartition("/")[2] if parent else alias


def dest_word(uri):
    alias, rel = inventory.split_uri(uri)
    return rel.rpartition("/")[2] if rel else alias


def size_text(n):
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    return f"{max(n, 0) / 1024 ** 2:.1f} MB"


# ---------- OneDrive ----------

def onedrive_roots():
    return [normalize(os.environ[v], None) for v in ONEDRIVE_VARS if os.environ.get(v)]


def in_onedrive(path):
    pn = normalize(str(path), None)
    return any(inside(pn, r) for r in onedrive_roots())


def cloud_only(path):
    return bool(_attrs(str(path)) & RECALL_ON_DATA_ACCESS)


def _files_under(path):
    """夾底下全部檔（不跟連結），回 [絕對路徑]；檔本身回 [它]。"""
    if not os.path.isdir(path):
        return [str(path)]
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        out.extend(os.path.join(dirpath, f) for f in filenames)
    return out


def cloud_download(path):
    """只在雲端的檔份數與大小（中繼資料加總，不下載）。"""
    n = total = 0
    for f in _files_under(path):
        if cloud_only(f):
            n += 1
            try:
                total += os.stat(f).st_size
            except OSError:
                pass
    return n, total


# ---------- 卡與排除 ----------

def _cards_of(vault, ids):
    from kb._lib import cards
    groups = cards._scan_groups(vault)[0]
    return {i: groups[i] for i in ids if i in groups}


def _entries_under(vault, uri, folder):
    """位置是這個 uri（檔）或在它底下（夾）的條目。"""
    key = inventory.uri_key(uri)
    out = []
    for e in inventory.load(vault)["entries"]:
        for loc in e.get("locations") or []:
            u = loc.get("uri", "")
            if inventory.uri_key(u) == key or (folder and inventory._under(u, uri) is not None):
                out.append(e)
                break
    return out


def excludes_under(vault, path):
    """夾底下（不含自己）的排除列：[(來源原始條目, 排除列, 相對 path 的段)]，段用磁碟上的大小寫。"""
    raw = sources_lib.load_raw(vault)
    pn = normalize(str(path), None)
    real = _real(path)
    out = []
    for src in raw["sources"]:
        root = resolve_spec(src["spec"], str(vault)) if src.get("spec") else None
        if root is None:
            continue
        for ex in src.get("excludes") or []:
            ex_root = resolve_spec(ex.get("spec", ""), root) if ex.get("spec") else None
            if ex_root and ex_root != pn and inside(ex_root, pn):
                rest = os.path.relpath(_real(ex_root), real).replace("\\", "/")
                out.append((src, ex, rest))
    return out


# ---------- 搬、改名 ----------

def plan_move(vault, sources, target, cwd, dest=None, new_name=None):
    """move（dest）或 rename（new_name）的事前檢查與影響，不動任何東西。不行丟 Refused。"""
    entry, src = resolve(vault, sources, target, cwd)
    check_from(vault, sources, src)
    folder = src.is_dir()
    if new_name is not None:
        name = _check_name(src, new_name, folder)
        dest_dir = src.parent
    else:
        dest_dir = resolve_folder(vault, sources, dest, cwd)
        check_to(vault, sources, dest_dir, str(dest))
        name = src.name
        dn, sn = normalize(str(dest_dir), None), normalize(str(src), None)
        if folder and inside(dn, sn):
            raise Refused(INTO_ITSELF)
        if dn == normalize(str(src.parent), None):
            raise Refused(SAME_PLACE.format(name=src.name, dest=dest_dir.name))
    dst = Path(dest_dir) / name
    dparts = _parts(vault, dst)
    if dparts is not None and len(dparts) == 3 and name.casefold() == (dparts[1] + ".md").casefold():
        raise Refused(SYNTHESIS_NAME.format(name=name))
    if _zone(vault, sources, dest_dir) == "Z9" and _credential(sources, dst):
        raise Refused(CREDENTIAL)
    moved_ex = excludes_under(vault, src) if folder else []
    if moved_ex and dparts is not None:  # 夾裡有不讀的子夾，搬進知識庫就沒有排除守著了
        raise Refused(EXCLUDED.format(name=moved_ex[0][2].rpartition("/")[2]))
    if folder and dparts is not None and _parts(vault, src) is None and credential_inside(vault, src):
        raise Refused(CREDENTIAL)  # 同理：知識庫裡不查憑證樣式
    try:
        if _dev(src.parent) != _dev(dest_dir):
            raise Refused(other_disk(src.parent, dest_dir))
    except OSError as e:
        raise Refused(NOT_FOUND.format(target=target)) from e
    from_uri = uri_of(vault, sources, src)
    to_dir_uri = uri_of(vault, sources, dest_dir)
    existing = os.path.lexists(dst) and normalize(str(dst), None) != normalize(str(src), None)
    inside_entries = _entries_under(vault, from_uri, folder) if from_uri else []
    card_ids = [e["id"] for e in inside_entries if e.get("status") == "active"]
    plan = {"src": str(src), "dst": str(dst), "dest_dir": str(dest_dir), "name": src.name, "new_name": name,
            "kind": "folder" if folder else "file", "from_uri": from_uri, "to_dir_uri": to_dir_uri,
            "entry_id": entry["id"] if entry and not folder else None, "existing": bool(existing),
            "existing_is_dir": bool(existing and os.path.isdir(dst)),
            "files": len(_files_under(src)), "cards": len(_cards_of(vault, card_ids)),
            "excludes": [rest for _s, _e, rest in moved_ex], "_moved_ex": moved_ex,
            "signs": [Path(f).name for f in _files_under(src) if Path(f).name.startswith("~$")][:SIGNS_MAX]}
    plan["download"] = None
    if in_onedrive(src) and not in_onedrive(dest_dir):
        n, size = cloud_download(src)
        if n:
            plan["download"] = {"files": n, "bytes": size}
    return plan


def _check_name(src, new_name, folder):
    """新名字：不能空、不能含 Windows 不收的字；. 或 ~$ 開頭的知識庫不會讀。檔的副檔名：以原副檔名結尾照收；尾巴是已知副檔名
    又跟原本不同才拒絕；其他（「2026.10 報價」「Dr. Wang」）自動補原副檔名。"""
    name = str(new_name or "").strip().rstrip(" .")
    if not name or name in (".", "..") or any(ch in BAD_NAME_CHARS or ord(ch) < 32 for ch in name):
        raise Refused(BAD_NAME)
    if _unread_name(name):
        raise Refused(HIDDEN_NAME)
    if name.split(".")[0].strip().upper() in DEVICE_NAMES:
        raise Refused(DEVICE_NAME)
    if folder:
        return name
    old_ext = src.suffix
    if old_ext and name.casefold().endswith(old_ext.casefold()):
        return name
    new_ext = Path(name).suffix
    if new_ext and new_ext.casefold() in KNOWN_EXTS and new_ext.casefold() != old_ext.casefold():
        raise Refused(EXTENSION.format(old=old_ext or "（沒有）", new=new_ext))
    return name + old_ext


def check_lines(plan, op):
    """--check 給 AI 講影響的幾行（不印編號）。"""
    to_word = dest_word(plan["to_dir_uri"] or "")
    from_word = folder_word(plan["from_uri"] or "")
    if op == "rename":
        lines = [f"要改名：{plan['name']} → {plan['new_name']}"]
    elif plan["kind"] == "folder":
        lines = [f"要搬：{plan['name']}（{plan['files']} 份，卡 {plan['cards']} 張）從 {from_word} 搬到 {to_word}"]
    else:
        lines = [f"要搬：{plan['name']} 從 {from_word} 搬到 {to_word}"]
    if plan["kind"] == "file":
        lines.append("卡會跟著改" if plan["cards"] else "這份還沒有卡")
    if plan["excludes"]:
        lines.append("裡面不讀的夾：" + "、".join(plan["excludes"]))
    if plan["existing"]:
        lines.append(EXISTS.format(name=plan["new_name"]))
    if plan["signs"]:
        lines.append("可能有檔開著：" + "、".join(plan["signs"]))
    if plan["download"]:
        d = plan["download"]
        lines.append(f"{d['files']} 份只在雲端，搬出 OneDrive 會先全部下載（約 {size_text(d['bytes'])}）")
    return lines


def _readonly(path):
    return os.path.isfile(path) and not os.access(path, os.W_OK)


def _writable(path):
    if os.path.isdir(path):
        return True
    try:
        with open(path, "r+b"):
            return True
    except PermissionError:
        return False


def _check_deletable(path, name):
    """丟回收筒之前：唯讀另一句，開著的說關掉；不行丟 Refused。"""
    if _readonly(path):
        raise Refused(READONLY)
    if not _writable(path):
        raise Refused(OPEN.format(name=name))


def _why(e):
    return getattr(e, "strerror", None) or type(e).__name__


def _exclude_versions(vault, moved, new_root):
    """搬整個夾、裡面有不讀的子夾：回 (新舊兩條都在的登記表, 只剩新的登記表)。改名之前先寫前者，那一刻兩邊都不讀；
    搬好後寫後者。跨來源就把那條放到新來源。"""
    raw = sources_lib.load_raw(vault)
    sources = sources_lib.Sources(vault, raw)
    both, final = copy.deepcopy(raw), copy.deepcopy(raw)

    def key(s):
        return s.get("id") or s.get("alias")
    for src, ex, rest in moved:
        new_abs = os.path.join(str(new_root), *rest.split("/"))
        dest = _source_of(sources, new_abs)
        if dest is None:
            continue
        spec = os.path.relpath(_real(new_abs), _real(dest["root"])).replace("\\", "/")
        for target in (both, final):
            by = {key(s): s for s in target["sources"]}
            if target is final:
                old = by[key(src)]
                old["excludes"] = [r for r in old.get("excludes") or [] if r != ex]
            by[dest["id"] or dest["alias"]].setdefault("excludes", []).append(dict(ex, spec=spec))
    return both, final


def do_move(vault, plan, turn_id, op, replace=False):
    """真的搬。呼叫端已持 sources.json 的鎖。回 {"to_uri","replaced","changed","cards_moved"}；不行丟 Refused。
    改名之前任何一步不行：什麼都沒動。改名之後任何一步失敗：先把檔搬回原位、再照 undo 還原改過的檔與登記表，
    說「沒有動：…」；搬不回去才說「已搬好，但後面沒做完」（moves.jsonl 那行留著，說「退回」搬得回來）。"""
    from kb._lib import cards, entities, pending
    src, dst = plan["src"], plan["dst"]
    if plan["existing"] and (not replace or plan["kind"] == "folder" or plan["existing_is_dir"]):
        raise Refused(EXISTS.format(name=plan["new_name"]))
    sp = Path(vault) / sources_lib.SOURCES_REL
    original = sp.read_bytes()
    versions = _exclude_versions(vault, plan["_moved_ex"], dst) if plan["_moved_ex"] else None
    if versions:  # 新舊兩條都在：改名那一刻兩邊都不讀
        try:
            turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
            write_json_atomic(sp, versions[0])
        except OSError as e:
            raise Refused(NOT_MOVED.format(why=_why(e))) from e

    def restore_sources():
        if versions:
            from kb._lib.io import write_text_atomic
            write_text_atomic(sp, original.decode("utf-8"))
    replaced = None
    try:
        if plan["existing"]:
            replaced = _replace_into(vault, plan, turn_id)
        else:
            _rename(src, dst)
    except Refused:
        restore_sources()
        raise
    except PermissionError as e:
        restore_sources()
        raise Refused(OPEN.format(name=plan["name"])) from e
    except OSError as e:
        restore_sources()
        if getattr(e, "errno", None) == 18 or getattr(e, "winerror", None) == 17:  # EXDEV、ERROR_NOT_SAME_DEVICE
            raise Refused(other_disk(Path(src).parent, plan["dest_dir"])) from e
        raise Refused(NOT_MOVED.format(why=_why(e))) from e

    changed = list(replaced["changed"]) if replaced else []
    undo = entities._Undo(vault)
    folder = plan["kind"] == "folder"
    got = {"moved": [], "updated": [], "dropped": [], "changed": [], "touched": []}
    try:
        sources = sources_lib.load(vault)
        to_uri = uri_of(vault, sources, dst)
        undo.file(turn.VERSIONS_REL.as_posix() + f"/{turn_id}/moves.jsonl")
        if plan["from_uri"] and to_uri:
            turn.record_move(vault, turn_id, op, plan["kind"], plan["from_uri"], to_uri)
        if versions:
            undo.file(sources_lib.SOURCES_REL)
            write_json_atomic(sp, versions[1])
            changed.append({"path": sources_lib.SOURCES_REL, "action": "modify"})
            sources = sources_lib.load(vault)
        undo.file(inventory.INVENTORY_POSIX)
        hit = inventory.move_locations(vault, plan["from_uri"], to_uri, turn_id, folder) if plan["from_uri"] and to_uri else []
        roots = sorted({inventory.split_uri(u)[0] for u in (plan["from_uri"], to_uri) if u})
        report = inventory.scan(vault, sources, only_roots=roots)
        if hit or inventory.has_changes(vault, report):
            undo.file(pending.PENDING_POSIX)
            undo.file(pending.MD_POSIX)
            inventory.apply(vault, report, turn_id)
            changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
        got = cards.sync(vault, sources, turn_id, undo)
        if got["moved"] or got["updated"] or got["dropped"]:
            changed += got["changed"]
            undo.file(cards.INDEX_POSIX)
            cards.render_index(vault, turn_id)
            changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
        for rel in _synthesis_rels(vault, got["touched"]):  # 綜合卡快取也收進倒回的範圍（第二輪修）
            undo.file(rel)
        changed += [{"path": rel, "action": "modify"} for rel in cards.refresh_synthesis(vault, got["touched"], turn_id)]
    except Exception as e:  # noqa: BLE001  改名之後的任何失敗都要倒回去或說實話
        try:
            _rename(dst, src)
        except Exception:  # noqa: BLE001
            raise Refused(PARTIAL) from e
        from kb._lib import ledger
        for step in (restore_sources, undo.rollback):  # 各自做：一個失敗另一個照做（不讀的夾不能因此變可讀）
            try:
                step()
            except Exception as again:  # noqa: BLE001  檔已在原位；清冊下次掃描會對上
                ledger.log_error(vault, f"source-edit 倒回時沒還原完：{type(again).__name__}: {again}", source="source-edit")
        if replaced is not None:
            raise Refused(REPLACED_NOT_MOVED.format(name=plan["new_name"], why=_why(e))) from e
        raise Refused(NOT_MOVED.format(why=_why(e))) from e
    return {"to_uri": to_uri, "replaced": replaced, "changed": changed,
            "cards_moved": len(got["moved"]) + len(got["updated"])}


def _synthesis_rels(vault, names):
    """這些實體的綜合卡相對路徑（有的才列）。"""
    from kb._lib import entities, synthesis
    try:
        data = entities.load(vault)
    except ValueError:
        return []
    out = []
    for n in names:
        e, _hit = entities.find(data, n)
        if e and e.get("type") and (Path(vault) / synthesis.card_rel(e)).is_file():
            rel = synthesis.card_rel(e)
            if rel not in out:
                out.append(rel)
    return out


def _replace_into(vault, plan, turn_id):
    """--replace：先把這份移到目標夾裡的暫名（驗過改名做得到、同一顆硬碟），再把目標丟回收筒，最後換成目標的名字。
    丟之前出事就把這份搬回原位；丟了之後換名失敗，說舊的那份已經進回收筒。回 do_trash 的結果。"""
    src, dst = Path(plan["src"]), Path(plan["dst"])
    _check_deletable(dst, plan["new_name"])
    tmp = Path(plan["dest_dir"]) / f".kb-move-{secrets.token_hex(4)}{src.suffix}"
    _rename(src, tmp)  # PermissionError、跨硬碟由呼叫端說
    try:
        tplan = plan_trash(vault, sources_lib.load(vault), dst, os.getcwd())
        replaced = do_trash(vault, tplan, turn_id)
    except BaseException:
        _rename(tmp, src)
        raise
    try:
        _rename(tmp, dst)
    except OSError as e:
        try:
            _rename(tmp, src)
        except OSError:
            raise Refused(REPLACED_STUCK.format(name=plan["new_name"])) from e
        raise Refused(REPLACED_NOT_MOVED.format(name=plan["new_name"], why=_why(e))) from e
    return replaced


# ---------- 丟資源回收筒 ----------

def plan_trash(vault, sources, target, cwd):
    """trash 的事前檢查與後果，不動任何東西。不行丟 Refused。"""
    from kb._lib import cards
    entry, path = resolve(vault, sources, target, cwd)
    if path.is_dir():
        raise Refused(TRASH_FOLDER)
    check_from(vault, sources, path)
    uri = uri_of(vault, sources, path)
    if entry is None and uri:
        entry = inventory.entry_for(vault, uri)
    size = os.path.getsize(path)
    if _drive_type(path) != DRIVE_FIXED:  # 隨身碟、網路磁碟：不跳視窗的呼叫可能直接永久刪
        raise Refused(BIN_UNAVAILABLE.format(why=REMOVABLE))
    cloud = cloud_only(path)
    if not cloud:
        try:
            used, cap = recycle_room(path)
        except BinUnavailable as e:
            raise Refused(BIN_UNAVAILABLE.format(why=e)) from e
        if used + size > cap:
            raise Refused(BIN_TOO_BIG.format(size=size_text(size)))
    others = [loc for loc in (entry or {}).get("locations") or []
              if uri and inventory.uri_key(loc.get("uri", "")) != inventory.uri_key(uri)]
    group = (_cards_of(vault, [entry["id"]]).get(entry["id"]) or []) if entry and not others else []
    names = _synthesis_names(vault, cards.entity_names_of(group))
    alias, rel = inventory.split_uri(uri or "")
    where = "\\".join([alias] + rel.split("/")[:-1]) if uri else str(path.parent)
    return {"path": str(path), "name": path.name, "uri": uri, "where": where, "entry_id": entry["id"] if entry else None,
            "card": group[0]["path"] if group else None, "cards": [c["path"] for c in group], "synthesis": names,
            "touched": cards.entity_names_of(group), "others": len(others), "size": size,
            "onedrive": "cloud" if cloud else ("local" if in_onedrive(path) else None)}


def _synthesis_names(vault, names):
    """這幾個實體裡有綜合卡的（名稱照登記的）。"""
    from kb._lib import entities, synthesis
    out = []
    try:
        data = entities.load(vault)
    except ValueError:
        return out
    for n in names:
        e, _hit = entities.find(data, n)
        if e and e.get("status") == "active" and e.get("type") and (Path(vault) / synthesis.card_rel(e)).is_file():
            if e["name"] not in out:
                out.append(e["name"])
    return out


TRASH_THIRD = {None: "說「退回上一版」救不回來，刪錯要從資源回收筒拿",
               "local": "刪錯要從資源回收筒拿；別台電腦與 OneDrive 上的那份也會一起刪",
               "cloud": "這份只在雲端：會從你所有裝置一起刪，要救得到 OneDrive 網頁的回收筒"}


def trash_lines(plan):
    second = "同一份內容在別處還有，卡留著" if plan["others"] else ("那張卡會拿掉" if plan["card"] else "這份沒有卡")
    if plan["synthesis"]:
        second += "；綜合卡要更新：" + "、".join(plan["synthesis"])
    return [f"要丟進資源回收筒：{plan['name']}（{plan['where']}）", second, TRASH_THIRD[plan["onedrive"]]]


def trash_sentence(plan, result):
    if plan["onedrive"] == "cloud":
        head = f"已刪：{plan['name']}"
    else:
        head = f"已丟進資源回收筒：{plan['name']}"
    if result["card_removed"]:
        head += "；卡拿掉了"
    if plan["onedrive"] == "cloud":
        head += "；這份只在雲端，從你所有裝置一起刪了，要救得到 OneDrive 網頁的回收筒"
    elif plan["onedrive"] == "local":
        head += "；別台電腦與 OneDrive 上的那份也一起刪了，刪錯要從資源回收筒拿"
    return head


def do_trash(vault, plan, turn_id):
    """丟資源回收筒，再照 kb cards drop 那條跟上：條目 deleted、卡拿掉（備份）、待處理關掉、推測日程取消。
    同一份內容還在別處（清冊多個位置）只拿掉這個位置，卡照 sync 跟上。回 {"changed","card_removed","cancelled"}。"""
    from kb._lib import agenda, cards, pending
    _check_deletable(plan["path"], plan["name"])
    try:
        shell_trash(plan["path"])
    except BinUnavailable as e:
        raise Refused(BIN_UNAVAILABLE.format(why=e)) from e
    changed, card_removed, cancelled = [], False, []
    if plan["uri"]:
        turn.record_move(vault, turn_id, "trash", "file", plan["uri"], None)
    fid = plan["entry_id"]
    if fid is None:
        return {"changed": changed, "card_removed": False, "cancelled": []}
    key = inventory.uri_key(plan["uri"] or "")
    with FileLock(Path(vault) / inventory.INVENTORY_POSIX):
        turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
        data = inventory.load(vault)
        for e in data["entries"]:
            if e.get("id") != fid:
                continue
            rest = [loc for loc in e.get("locations") or [] if inventory.uri_key(loc.get("uri", "")) != key]
            if rest:
                e["locations"] = rest
            else:
                e["status"] = "deleted"
        if not plan["others"]:
            for rel in plan["cards"]:
                cards.remove_card(vault, rel, turn_id)
                changed.append({"path": rel, "action": "delete"})
                card_removed = True
        inventory._write(vault, data["entries"])
    changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    if plan["others"]:
        got = cards.sync(vault, sources_lib.load(vault), turn_id)
        changed += got["changed"]
    else:
        how = "客戶說刪了（丟進資源回收筒）"
        for kind in ("missing_file", "unreadable"):
            try:
                if pending.auto_done(vault, fid, kind, how, turn_id):
                    changed += [{"path": pending.PENDING_POSIX, "action": "modify"},
                                {"path": pending.MD_POSIX, "action": "modify"}]
            except ValueError:
                pass  # pending.json 壞了：下次 scan 補
        try:
            cancelled = agenda.cancel_from_card(vault, fid, turn_id)
        except (ValueError, OSError):
            cancelled = []
        if cancelled:
            changed += [{"path": agenda.AGENDA_POSIX, "action": "modify"}, {"path": agenda.MD_POSIX, "action": "modify"}]
    cards.render_index(vault, turn_id)
    changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
    changed += [{"path": rel, "action": "modify"} for rel in cards.refresh_synthesis(vault, plan["touched"], turn_id)]
    return {"changed": changed, "card_removed": card_removed, "cancelled": [i["id"] for i in cancelled]}


# ---------- 推薦去處（D-178） ----------

def where(vault, name, card_type=None):
    """這個實體掛的檔案卡，依原檔所在的夾分組：夾（別名/相對路徑）、份數、各卡型份數、最近一份的日期（清冊 first_seen）。只讀。
    回 {"entity","folders":[{"folder","count","types","latest"}]}；實體沒登記丟 Refused。"""
    from kb._lib import cards, entities
    data = entities.load(vault)
    e, _hit = entities.find(data, name)
    if e is None or e.get("status") != "active":
        raise Refused(NO_ENTITY.format(name=name))
    mounted = entities.mounts(vault, data).get(e["name"], [])
    by_id = {x.get("id"): x for x in inventory.load(vault)["entries"]}
    groups = cards._scan_groups(vault)[0]
    folders = {}
    for m in mounted:
        entry = by_id.get(m["id"])
        if not entry or entry.get("status") != "active" or not entry.get("locations"):
            continue
        front = (groups.get(m["id"]) or [{"front": {}}])[0]["front"]
        kind = front.get("卡型") if isinstance(front.get("卡型"), str) else ""
        if card_type and kind != card_type:
            continue
        alias, rel = inventory.split_uri(entry["locations"][0]["uri"])
        parent = rel.rpartition("/")[0]
        folder = f"{alias}/{parent}" if parent else alias
        row = folders.setdefault(folder, {"folder": folder, "count": 0, "types": {}, "latest": ""})
        row["count"] += 1
        if kind:
            row["types"][kind] = row["types"].get(kind, 0) + 1
        seen = str(entry.get("first_seen") or "")[:10]
        row["latest"] = max(row["latest"], seen)
    rows = sorted(folders.values(), key=lambda r: (-r["count"], -int(r["latest"].replace("-", "") or 0), r["folder"]))
    return {"entity": e["name"], "folders": rows}


def where_lines(got):
    if not got["folders"]:
        return [f"{got['entity']}還沒有掛任何檔"]
    lines = [f"{got['entity']}的檔放在："]
    for r in got["folders"]:
        kinds = "、".join(f"{k} {n}" for k, n in sorted(r["types"].items(), key=lambda kv: (-kv[1], kv[0])))
        latest = f"，最近 {r['latest']}" if r["latest"] else ""
        lines.append(f"- {r['folder']}：{r['count']} 份" + (f"（{kinds}）" if kinds else "") + latest)
    return lines


# ---------- 開檔案總管 ----------

def check_open(vault, sources, targets, cwd):
    """最多兩個夾；在准許的位置或是知識庫外客戶給的路徑（只開不寫）。回絕對路徑清單；不行丟 Refused。"""
    if len(targets) > OPEN_MAX:
        raise Refused(OPEN_TOO_MANY)
    out = []
    for t in targets:
        path = resolve_folder(vault, sources, t, cwd)
        parts = _parts(vault, path)
        if parts == []:
            raise Refused(SYSTEM.format(name=inventory.KNOWLEDGE))
        zone = _zone(vault, sources, path)
        if zone == "Z10":
            raise Refused(EXCLUDED.format(name=path.name))
        if zone in ("Z1", "Z5", "Z6", "Z7", "Z8"):
            raise Refused(SYSTEM.format(name=parts[0]))
        out.append(str(path))
    return out


def open_folders(paths):
    for p in paths:
        _startfile(p)
