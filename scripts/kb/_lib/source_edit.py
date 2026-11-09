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
import shutil
from pathlib import Path

from kb import guard
from kb._lib import credentials, inventory, turn
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, write_json_atomic
from kb._lib.paths import decode_uri, inside, normalize, resolve_spec

ID = re.compile(r"^(f|out)-[0-9a-f]+$")
RECALL_ON_DATA_ACCESS = 0x400000
ONEDRIVE_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
OPEN_MAX = 2
BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
SIGNS_MAX = 3
BAD_NAME_CHARS = '\\/:*?"<>|'

# 拒絕句（exit 3，一字不差；spec 與 a2 回覆的疑義第 1 條）
EXISTS = "目標已經有「{name}」"
# 放寬甲：同名回 conflict（內容一不一樣、怎麼選），拒絕句寫給 AI
CONFLICT = ("「{dest}」已經有「{name}」（內容{same}）：一樣的話不用搬；要取代加 --replace（原本那份先進資源回收筒）；"
            "要另取名加 --name <新名>。")
OPEN = "「{name}」開著，關掉才動得了：請使用者關掉那個檔（沒存的內容會不見，不要替他關），關好再跑一次。"
EXCLUDED = "「{name}」使用者說過不讀，不搬進去也不搬出來。"
CREDENTIAL = "這個檔名像密碼或金鑰，不動。"
NOT_FOUND = "找不到這個檔：{target}"
NO_FOLDER = "找不到這個夾：{target}"
LOCKED = "等不到鎖，沒有動"
SYSTEM = "「{name}」是知識庫自己用的地方，不搬進去也不搬出來"
SYNTHESIS_NAME = "「{name}」跟綜合卡同名，換個名字"
EXTENSION = "副檔名不改：{old} → {new}"
BAD_NAME = "新名字不能是空的，也不能含 \\ / : * ? \" < > |"
SAME_PLACE = "「{name}」已經在「{dest}」裡了"
INTO_ITSELF = "不能把夾搬進它自己裡面"
BIN_UNAVAILABLE = "資源回收筒用不了（{why}），沒有刪"
BIN_TOO_BIG = "這份 {size}，資源回收筒放不下"
NO_ROOM = ("「{name}」進不了資源回收筒，這台電腦也放不下先留的一份（還差 {need}），刪了救不回來："
           "先用白話問使用者確定要刪嗎，說確定才加 --permanent。")
OPEN_TOO_MANY = "一次最多開兩個夾"
HIDDEN_NAME = "這種名字知識庫不會讀，換個名字"
NOT_MOVED = "沒有動：{why}"
PARTIAL = "已搬好，但後面沒做完；說「退回」可以退回"
REPLACED_NOT_MOVED = "原本的「{name}」已經丟進資源回收筒，要從那裡拿回來；這份沒搬過去（{why}），還在原位"
REPLACED_STUCK = "原本的「{name}」已經丟進資源回收筒，要從那裡拿回來；這份搬到一半，這是 plugin 程式出錯：照核心規則請服務人員看。"
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


class Conflict(Refused):
    """同名：句子之外帶 conflict 列（name、dest、same_content、existing），--json 與 results 用。"""

    def __init__(self, text, rows):
        super().__init__(text)
        self.rows = rows


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
        for alias in (inventory.HOLD,):
            if first == alias.casefold():
                return inventory.make_uri(alias, "/".join(parts[1:])) if len(parts) > 1 else f"file://{alias}"
        return inventory.make_uri(inventory.KNOWLEDGE, "/".join(parts))
    src = _source_of(sources, path)
    if src is None:  # 本來就在知識庫外沒登記的地方的檔（outside）：清冊記的是可攜 spec
        out = inventory.outside_uri(path)
        return out if out and inventory.entry_for(vault, out) is not None else None
    rel = os.path.relpath(_real(path), _real(src["root"])).replace("\\", "/")
    return f"file://{src['alias']}" if rel == "." else inventory.make_uri(src["alias"], rel)


def _profile_data(vault):
    from kb._lib import profile
    try:
        return profile.load(vault) or {}
    except ValueError:
        return {}


def _types(vault):
    """型別夾名的集合（小寫）：照設定檔記的夾名（型別夾編號，沒有 dir 的是型別名）。"""
    from kb._lib import profile
    data = _profile_data(vault)
    return {str(profile.type_dir(data, t.get("name", "")) or "").casefold() for t in data.get("types") or []}


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
    """要動的檔或夾在准許的位置嗎；不行丟 Refused。放寬甲：只擋 willy 的區與知識庫根本身、不讀的、檔名像密碼的。"""
    path = Path(path)
    name = path.name
    zone = _zone(vault, sources, path)
    vn, pn = normalize(str(vault), None), normalize(str(path), None)
    if zone in ("Z5", "Z6", "Z7") or guard.is_willy_file(zone, pn, vn):
        raise Refused(guard.willy_reason(zone, pn, vn, name or inventory.KNOWLEDGE))
    if zone == "Z10":
        raise Refused(EXCLUDED.format(name=name))
    if zone == "Z9" and _credential(sources, path):
        raise Refused(CREDENTIAL)


def check_to(vault, sources, dest, target_text, make=False):
    """目的地夾要存在、在准許的位置；不行丟 Refused。make：登記實體的 <型別>/<名稱> 還沒有夾、搬的時候先建（收件匣裁定 1），
    不存在照收。"""
    dest = Path(dest)
    if not dest.is_dir() and not make:
        raise Refused(NO_FOLDER.format(target=target_text))
    zone = _zone(vault, sources, dest)
    vn, pn = normalize(str(vault), None), normalize(str(dest), None)
    if zone in ("Z5", "Z6", "Z7") or guard.is_willy_file(zone, pn, vn):
        raise Refused(guard.willy_reason(zone, pn, vn, dest.name or inventory.KNOWLEDGE))
    if zone == "Z10":
        raise Refused(EXCLUDED.format(name=dest.name))
    parts = _parts(vault, dest)
    if _unread_dir(vault, sources, dest, parts):
        raise Refused(HIDDEN_NAME)


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
    """目標三種寫法（同 kb convert）：清冊編號、uri（% 編碼也收）、路徑。回 (條目或 None, 絕對路徑)；不存在丟 Refused。"""
    text = decode_uri(str(target).strip())
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
    text = decode_uri(str(target).strip())
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
    return onedrive_root(path) is not None


def onedrive_root(path):
    """path 所在的 OneDrive 根（個人、公司各一個）；不在任何 OneDrive 裡回 None。"""
    pn = normalize(str(path), None)
    return next((r for r in sorted(onedrive_roots(), key=len, reverse=True) if inside(pn, r)), None)


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


def _entries_of_alias(vault, alias):
    """位置在這個來源別名底下的條目（整個來源根搬走時用）。"""
    a = alias.casefold()
    return [e for e in inventory.load(vault)["entries"]
            if any(inventory.split_uri(loc.get("uri", ""))[0].casefold() == a for loc in e.get("locations") or [])]


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

def _entity_folder(vault, dest, cwd):
    """--to 是實體清單裡 active 實體的 <型別夾>/<名稱>（也收 知識庫/<型別夾>/<名稱> 與絕對路徑）、型別夾在：
    回 (實際的相對路徑 <型別夾>/<登記的名稱>, 照字面解出的路徑)；不是回 (None, None)。
    型別夾編號：<型別夾> 收實際的夾名（01 案子），也收型別名（案子）。"""
    from kb._lib import entities, profile
    text = str(dest or "").strip().replace("\\", "/")
    head, _sep, rest = text.partition("/")
    if os.path.isabs(text):
        path = Path(text)
    elif head.casefold() == inventory.KNOWLEDGE.casefold() and rest:
        path = Path(vault) / rest
    else:
        path = Path(cwd) / text
    parts = _parts(vault, path)
    if not parts or len(parts) != 2:
        return None, None
    prof = _profile_data(vault)
    type_ = profile.dir_type(prof, parts[0]) or (parts[0] if profile.type_dir(prof, parts[0]) else None)
    folder = profile.type_dir(prof, type_) if type_ else None
    if folder is None or not (Path(vault) / folder).is_dir():
        return None, None
    try:
        data = entities.load(vault)
    except ValueError:
        return None, None
    e, _hit = entities.find(data, parts[1])
    if (e is None or e.get("status") != "active" or entities.is_self(e) or not e.get("type")
            or entities.key(e["type"]) != entities.key(type_) or entities.key(e["name"]) != entities.key(parts[1])):
        return None, None
    return f"{folder}/{e['name']}", path


def missing_entity_dir(vault, dest, cwd):
    """--to 是登記實體的 <型別夾>/<名稱>、那個夾還不在：回要建的相對路徑（實際的型別夾名與登記的名稱）；
    其他回 None，照舊由 resolve_folder 判（收件匣裁定 1：只建這一層）。"""
    rel, _path = _entity_folder(vault, dest, cwd)
    return rel if rel and not os.path.lexists(Path(vault) / rel) else None


def entity_folder(vault, dest, cwd):
    """--to 用型別名寫實體夾（案子/信義王）、實際的夾（01 案子/信義王）已經在：回實際的相對路徑（型別夾編號裁定 5）。
    照字面就找得到的（實際的夾名、別名/相對路徑）回 None，照舊由 resolve_folder 判。"""
    rel, path = _entity_folder(vault, dest, cwd)
    if rel is None or os.path.lexists(path) or not (Path(vault) / rel).is_dir():
        return None
    return rel


def _periodic_source(entries):
    """定期交來的（kb intake --from --name，D-241）：回來源名；不是回 None。"""
    for e in entries:
        info = (e or {}).get("intake") or {}
        if info.get("from") and info.get("name"):
            return info["from"]
    return None


def plan_move(vault, sources, target, cwd, dest=None, new_name=None, to_name=None):
    """move（dest）或 rename（new_name）的事前檢查與影響，不動任何東西。不行丟 Refused。"""
    entry, src = resolve(vault, sources, target, cwd)
    check_from(vault, sources, src)
    folder = src.is_dir()
    make_dir = None
    if new_name is not None:
        name = _check_name(src, new_name, folder)
        dest_dir = src.parent
    else:
        make_dir = missing_entity_dir(vault, dest, cwd)
        known = None if make_dir else entity_folder(vault, dest, cwd)
        dest_dir = Path(vault) / (make_dir or known) if make_dir or known else resolve_folder(vault, sources, dest, cwd)
        check_to(vault, sources, dest_dir, str(dest), make=bool(make_dir))
        name = _check_name(src, to_name, folder) if to_name else src.name
        dn, sn = normalize(str(dest_dir), None), normalize(str(src), None)
        if folder and inside(dn, sn):
            raise Refused(INTO_ITSELF)
        if dn == normalize(str(src.parent), None) and name == src.name:
            raise Refused(SAME_PLACE.format(name=src.name, dest=dest_dir.name))
    dst = Path(dest_dir) / name
    dparts = _parts(vault, dst)
    if dparts is not None and len(dparts) == 3 and name.casefold() == (dparts[1] + ".md").casefold():
        raise Refused(SYNTHESIS_NAME.format(name=name))
    if _zone(vault, sources, dest_dir) == "Z9" and _credential(sources, dst):
        raise Refused(CREDENTIAL)
    moved_ex = excludes_under(vault, src) if folder else []
    source_root = next((s for s in sources.entries if folder and normalize(str(src), None) == s["root"]), None)
    if moved_ex and dparts is not None and source_root is None:  # 夾裡有不讀的子夾，搬進知識庫就沒有排除守著了（來源根整個搬進去：排除跟著記進 vault_excludes）
        raise Refused(EXCLUDED.format(name=moved_ex[0][2].rpartition("/")[2]))
    if folder and dparts is not None and _parts(vault, src) is None and credential_inside(vault, src):
        raise Refused(CREDENTIAL)  # 同理：知識庫裡不查憑證樣式
    try:
        cross_disk = _dev(src.parent) != _dev(dest_dir.parent if make_dir else dest_dir)
    except OSError as e:
        raise Refused(NOT_FOUND.format(target=target)) from e
    # 登記的來源根本身搬（放寬甲）：搬到沒登記的地方，自動換登記的位置；搬進知識庫或別的來源裡（absorb），
    # 這筆登記拿掉、清冊位置換成新位置、卡跟著搬、不重做（設計自定 9）
    absorb = source_root is not None and (dparts is not None or any(
        inside(normalize(str(dst), None), s["root"]) for s in sources.entries if s is not source_root))
    from_uri = uri_of(vault, sources, src)
    to_dir_uri = uri_of(vault, sources, dest_dir)
    outside = False
    if to_dir_uri is None and dparts is None and _source_of(sources, dest_dir) is None:
        outside = True  # 搬到知識庫外沒登記的地方（桌面）：清冊記成 file://~/…，狀態 outside，不掃描、卡留著
        to_dir_uri = inventory.outside_uri(dest_dir if not make_dir else Path(dest_dir).parent)
        if to_dir_uri is None:  # 路徑解不開（家目錄外也記絕對路徑 file:///…，不拒絕）
            raise Refused(NOT_FOUND.format(target=str(dest)))
    existing = os.path.lexists(dst) and normalize(str(dst), None) != normalize(str(src), None)
    inside_entries = (_entries_of_alias(vault, source_root["alias"]) if source_root is not None
                      else _entries_under(vault, from_uri, folder) if from_uri else [])
    card_ids = [e["id"] for e in inside_entries if e.get("status") == "active"]
    plan = {"src": str(src), "dst": str(dst), "dest_dir": str(dest_dir), "name": src.name, "new_name": name,
            "kind": "folder" if folder else "file", "from_uri": from_uri, "to_dir_uri": to_dir_uri,
            "entry_id": entry["id"] if entry and not folder else None, "existing": bool(existing),
            "existing_is_dir": bool(existing and os.path.isdir(dst)),
            "files": len(_files_under(src)), "cards": len(_cards_of(vault, card_ids)),
            "excludes": [rest for _s, _e, rest in moved_ex], "_moved_ex": moved_ex,
            "signs": [Path(f).name for f in _files_under(src) if Path(f).name.startswith("~$")][:SIGNS_MAX],
            "source_root": source_root["alias"] if source_root is not None else None,
            "absorb": absorb, "outside": outside, "make_dir": make_dir, "cross_disk": cross_disk, "periodic": _periodic_source(inside_entries if folder else [entry] if entry else [])}
    plan["same_content"] = bool(existing and not folder and os.path.isfile(dst) and _same_content(src, dst))
    plan["existing_uri"] = uri_of(vault, sources, dst) if existing else None
    plan["dest_rel"] = _dest_rel(to_dir_uri)
    plan["download"] = None
    src_root, dst_root = onedrive_root(src), onedrive_root(dest_dir)
    # 只在雲端、而且兩邊在同一個 OneDrive 根才說搬不會下載（第六輪：Dropbox 一類、個人與公司兩個根之間不算）
    plan["cloud_only"] = not folder and src_root is not None and src_root == dst_root and cloud_only(src)
    if src_root is not None and src_root != dst_root:
        n, size = cloud_download(src)
        if n:
            plan["download"] = {"files": n, "bytes": size}
    return plan


def _same_content(a, b):
    try:
        return os.path.getsize(a) == os.path.getsize(b) and inventory.sha256_of(a) == inventory.sha256_of(b)
    except OSError:
        return False


def _dest_rel(to_dir_uri):
    """conflict 的 dest：知識庫裡是相對路徑（01 案子/王宅），來源裡是 別名/相對路徑。"""
    if not to_dir_uri:
        return ""
    alias, rel = inventory.split_uri(to_dir_uri)
    return rel if alias == inventory.KNOWLEDGE and rel else (f"{alias}/{rel}" if rel else alias)


def conflict_rows(plan):
    return [{"name": plan["new_name"], "dest": plan["dest_rel"], "same_content": bool(plan["same_content"]),
             "existing": plan["existing_uri"]}]


def conflict_sentence(plan):
    return CONFLICT.format(dest=plan["dest_rel"].rpartition("/")[2] or plan["dest_rel"] or Path(plan["dest_dir"]).name,
                           name=plan["new_name"], same="一樣" if plan["same_content"] else "不一樣")


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
    if plan.get("make_dir"):
        lines.append(f"{plan['make_dir']} 還沒有夾，會先建一個")
    if plan["kind"] == "file":
        lines.append("卡會跟著改" if plan["cards"] else "這份還沒有卡")
    if plan.get("periodic") and op != "rename":
        lines.append(f"這份是 {plan['periodic']} 定期交來的；搬走之後下次交來會直接覆蓋搬過去的那份")
    if plan["excludes"]:
        lines.append("裡面不讀的夾：" + "、".join(plan["excludes"]))
    if plan["existing"]:
        lines.append(conflict_sentence(plan))
    if plan.get("cross_disk"):
        lines.append("不同顆硬碟：先複製過去、比對一樣才把原檔丟進資源回收筒")
    if plan["signs"]:
        lines.append("可能有檔開著：" + "、".join(plan["signs"]))
    if plan["download"]:
        d = plan["download"]
        lines.append(f"{d['files']} 份只在雲端，搬出 OneDrive 會先全部下載（約 {size_text(d['bytes'])}）")
    elif plan.get("cloud_only"):
        lines.append("這份只在雲端；同一個 OneDrive 裡搬不會下載")
    return lines


def _place(alias, rel):
    return f"{alias}/{rel}" if rel else alias


def move_sentence(plan, got, op="move"):
    """搬、改名的成功句（人讀不印編號）。兩邊最後一段同名（從 大安陳 搬到 大安陳）就改印「別名/相對路徑」（第五輪）；
    整個夾也說「退回」會搬回去。"""
    if op == "rename":
        return f"已改名：{plan['name']} → {plan['new_name']}（說「退回」會改回來）"
    a, b = folder_word(plan["from_uri"] or ""), dest_word(plan["to_dir_uri"] or "")
    if a.casefold() == b.casefold():
        fa, frel = inventory.split_uri(plan["from_uri"] or "")
        ta, trel = inventory.split_uri(plan["to_dir_uri"] or "")
        a, b = _place(fa, frel.rpartition("/")[0]), _place(ta, trel)
    if plan["kind"] == "folder":
        out = f"已搬好：{plan['name']}（{plan['files']} 份）從 {a} 搬到 {b}"
        if plan["excludes"]:
            out += "；不讀的「" + "」「".join(x.rpartition("/")[2] for x in plan["excludes"]) + "」跟著搬"
        return out + "（說「退回」會搬回去）"
    return f"已搬好：{plan['name']} 從 {a} 搬到 {b}（說「退回」會搬回去）"


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
    """丟回收筒之前：開著的說關掉；不行丟 Refused（唯讀的 do_trash 先取消唯讀）。"""
    if os.path.isfile(path) and not _readonly(path) and not _writable(path):
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


def _transfer(src, dst, plan):
    """搬一份：同一顆硬碟 os.rename；不同顆先複製（原檔留著，最後由 do_move 比對一樣才拿掉）。"""
    if not plan.get("cross_disk"):
        _rename(src, dst)
        return
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        if not _same_tree(src, dst):
            raise OSError("複製過去的內容跟原本的對不上")
    except BaseException:
        _remove_path(dst)
        raise


def _untransfer(dst, src, plan):
    """倒回：同一顆硬碟改回名字；不同顆原檔還在，只拿掉複製過去的那份。"""
    if plan.get("cross_disk"):
        _remove_path(dst)
    else:
        _rename(dst, src)


def _remove_path(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _same_tree(a, b):
    """兩邊（檔或夾）每份檔的內容指紋一樣。"""
    if not os.path.isdir(a):
        return os.path.isfile(b) and _same_content(a, b)
    fa = sorted(os.path.relpath(f, a) for f in _files_under(a))
    fb = sorted(os.path.relpath(f, b) for f in _files_under(b))
    return fa == fb and all(_same_content(os.path.join(a, r), os.path.join(b, r)) for r in fa)


def do_move(vault, plan, turn_id, op, replace=False, permanent=False):
    """真的搬。呼叫端已持 sources.json 的鎖。回 {"to_uri","replaced","changed","cards_moved"}；不行丟 Refused。
    改名之前任何一步不行：什麼都沒動。改名之後任何一步失敗：先把檔搬回原位、再照 undo 還原改過的檔與登記表，
    說「沒有動：…」；搬不回去才說「已搬好，但後面沒做完」（moves.jsonl 那行留著，說「退回」搬得回來）。"""
    from kb._lib import cards, entities, pending
    src, dst = plan["src"], plan["dst"]
    if plan["existing"] and not replace:
        raise Conflict(conflict_sentence(plan), conflict_rows(plan))
    if plan["existing"] and (plan["kind"] == "folder" or plan["existing_is_dir"]):
        raise Refused(EXISTS.format(name=plan["new_name"]))
    sp = Path(vault) / sources_lib.SOURCES_REL
    original = sp.read_bytes()
    # 來源根整個搬：不讀的子夾的 spec 是相對根的，跟著根走；搬進別處（absorb）的由 _follow_registry 一次記好
    versions = (_exclude_versions(vault, plan["_moved_ex"], dst)
                if plan["_moved_ex"] and not plan.get("source_root") else None)
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
    made = Path(vault) / plan["make_dir"] if plan.get("make_dir") else None

    def unmake():
        if made is not None:
            try:
                made.rmdir()
            except OSError:
                pass
    replaced = None
    try:
        if made is not None:  # 實體夾還沒有：先建這一層（收件匣裁定 1）
            made.mkdir()
        if plan["existing"]:
            replaced = _replace_into(vault, plan, turn_id)
        else:
            _transfer(src, dst, plan)
    except Refused:
        unmake()
        restore_sources()
        raise
    except PermissionError as e:
        unmake()
        restore_sources()
        raise Refused(OPEN.format(name=plan["name"])) from e
    except OSError as e:
        unmake()
        restore_sources()
        raise Refused(NOT_MOVED.format(why=_why(e))) from e

    changed = list(replaced["changed"]) if replaced else []
    undo = entities._Undo(vault)
    folder = plan["kind"] == "folder"
    got = {"moved": [], "updated": [], "dropped": [], "changed": [], "touched": []}
    try:
        changed += _follow_registry(vault, plan, turn_id, undo)
        sources = sources_lib.load(vault)
        to_uri = uri_of(vault, sources, dst)
        if to_uri is None and plan.get("outside"):  # 知識庫外沒登記的地方：可攜 spec（file://~/…）
            to_uri = (plan["to_dir_uri"].rstrip("/") + "/" + plan["new_name"]) if plan.get("to_dir_uri") else None
        undo.file(turn.VERSIONS_REL.as_posix() + f"/{turn_id}/moves.jsonl")
        if made is not None:  # 資料夾帳：退回時先搬回檔、再拿掉空夾
            undo.folder(turn_id, "make", plan["make_dir"])
        if plan.get("source_root"):  # 來源根整個搬：別名會變或消失，搬動帳記絕對路徑（file:///…），退回照樣搬得回來
            from_root = inventory.outside_uri(src)
            to_root = to_uri if plan.get("absorb") else inventory.outside_uri(dst)
            if from_root and to_root:
                turn.record_move(vault, turn_id, op, "folder", from_root, to_root)
        elif plan["from_uri"] and to_uri:
            turn.record_move(vault, turn_id, op, plan["kind"], plan["from_uri"], to_uri)
        if versions:
            undo.file(sources_lib.SOURCES_REL)
            write_json_atomic(sp, versions[1])
            changed.append({"path": sources_lib.SOURCES_REL, "action": "modify"})
            sources = sources_lib.load(vault)
        undo.file(inventory.INVENTORY_POSIX)
        if plan.get("absorb"):  # 這批條目的位置從來源 uri 換成新位置（編號不變）；來源登記已拿掉，不掃舊別名
            hit = inventory.rebase_alias(vault, plan["source_root"], to_uri, turn_id) if to_uri else []
            from_uri_for_roots = None
        else:
            hit = (inventory.move_locations(vault, plan["from_uri"], to_uri, turn_id, folder,
                                            status=inventory.OUTSIDE if plan.get("outside") else None)
                   if plan["from_uri"] and to_uri else [])
            from_uri_for_roots = plan["from_uri"]
        roots = sorted({inventory.split_uri(u)[0] for u in (from_uri_for_roots, to_uri) if u
                        and inventory.split_uri(u)[0] not in (inventory.HOME_ALIAS, "")})  # 知識庫外沒登記的地方不掃描
        report = inventory.scan(vault, sources, only_roots=roots) if roots else {"total": 0, "restored": [], "modified": [],
                                                                                 "added": [], "missing": [], "moved": [], "read_counts": {}}
        if plan.get("outside") and hit:  # 卡留著，只把位置欄與開原檔指到新位置
            outc = cards.follow_outside(vault, sources, hit, turn_id, undo)
            changed += [{"path": rel, "action": "modify"} for rel in outc]
        if hit or (roots and inventory.has_changes(vault, report)):
            undo.file(pending.PENDING_POSIX)
            undo.file(pending.MD_POSIX)
            if roots and inventory.has_changes(vault, report):
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
        undo.flush()
    except Exception as e:  # noqa: BLE001  改名之後的任何失敗都要倒回去或說實話
        try:
            _untransfer(dst, src, plan)
        except Exception:  # noqa: BLE001
            raise Refused(PARTIAL) from e
        unmake()
        from kb._lib import ledger
        for step in (restore_sources, undo.rollback):  # 各自做：一個失敗另一個照做（不讀的夾不能因此變可讀）
            try:
                step()
            except Exception as again:  # noqa: BLE001  檔已在原位；清冊下次掃描會對上
                ledger.log_error(vault, f"source-edit 倒回時沒還原完：{type(again).__name__}: {again}", source="source-edit")
        if replaced is not None:
            raise Refused(REPLACED_NOT_MOVED.format(name=plan["new_name"], why=_why(e))) from e
        raise Refused(NOT_MOVED.format(why=_why(e))) from e
    original = None
    if plan.get("cross_disk"):  # 不同顆硬碟：複製比對過了、清冊也跟上了，才拿掉原檔
        original = {"kept": None, "refused": None}
        try:
            original["kept"] = _discard(vault, sources_lib.load(vault), src, turn_id, permanent=permanent)
        except Refused as e:  # 放不下備份：原檔不動、兩份都留，交給 AI 問使用者
            original["refused"] = str(e)
    return {"to_uri": to_uri, "replaced": replaced, "changed": changed, "original": original,
            "cards_moved": len(got["moved"]) + len(got["updated"])}


def _follow_registry(vault, plan, turn_id, undo):
    """整個型別夾（第一層）改名、登記來源的根搬走（放寬甲，spec 五、六節）：profile.json 的 dir／sources.json 的 spec 跟著換
    （同 kb folders relocate／型別夾改名）；寫前備進回合、記進 undo。回 changed。"""
    from kb._lib import config, profile
    from kb._lib.paths import compress_spec
    changed = []
    if plan["kind"] != "folder":
        return changed
    src, dst = Path(plan["src"]), Path(plan["dst"])
    if plan.get("source_root"):
        raw = sources_lib.load_raw(vault)
        entry = next((s for s in raw["sources"] if str(s.get("alias", "")).casefold() == plan["source_root"].casefold()), None)
        if entry is not None:
            turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
            undo.file(sources_lib.SOURCES_REL)
            if plan.get("absorb"):  # 搬進知識庫或別的來源：這筆登記拿掉（卡留著），不讀的子夾記到新的主人
                _carry_excludes(vault, raw, plan, entry)
                raw["sources"] = [s for s in raw["sources"] if s is not entry]
                write_json_atomic(Path(vault) / sources_lib.SOURCES_REL, raw)
                changed.append({"path": sources_lib.SOURCES_REL, "action": "modify"})
                return changed
            entry["spec"] = compress_spec(dst, vault, config.get(vault, "source_spec_max_up"))
            write_json_atomic(Path(vault) / sources_lib.SOURCES_REL, raw)
            changed.append({"path": sources_lib.SOURCES_REL, "action": "modify"})
        return changed
    sparts, dparts = _parts(vault, src), _parts(vault, dst)
    if sparts is None or dparts is None or len(sparts) != 1 or len(dparts) != 1:
        return changed
    try:
        prof = profile.load(vault)
    except ValueError:
        return changed
    row = next((t for t in (prof or {}).get("types") or [] if (t.get("dir") or t.get("name", "")).casefold() == sparts[0].casefold()), None)
    if row is None:
        return changed
    turn.backup(vault, profile.PROFILE_POSIX, turn_id)
    undo.file(profile.PROFILE_POSIX)
    row["dir"] = dparts[0]
    write_json_atomic(Path(vault) / profile.PROFILE_POSIX, prof)
    changed.append({"path": profile.PROFILE_POSIX, "action": "modify"})
    from kb import profile_cli  # lint.json、batches.json 裡指到舊夾的路徑跟著換（同型別夾改名）
    changed += profile_cli._follow_paths(vault, [(sparts[0], dparts[0])], turn_id, undo)
    return changed


def _carry_excludes(vault, raw, plan, entry):
    """來源根整個搬進別處：它底下不讀的子夾改記到新位置的主人——別的來源（spec 相對那個來源的根），或知識庫（sources.json 頂層
    vault_excludes，spec 相對知識庫根）。改的是 raw（呼叫端寫）。"""
    sources_now = sources_lib.Sources(vault, raw)
    dst = Path(plan["dst"])
    alias = str(entry.get("alias", "")).casefold()
    for src_entry, ex, rest in plan["_moved_ex"]:
        if str(src_entry.get("alias", "")).casefold() != alias:
            continue
        new_abs = os.path.join(str(dst), *rest.split("/"))
        dest = _source_of(sources_now, new_abs)
        if dest is not None and dest["alias"].casefold() != alias:
            spec = os.path.relpath(_real(new_abs), _real(dest["root"])).replace("\\", "/")
            owner = next(s for s in raw["sources"] if (s.get("id") or s.get("alias")) == (dest["id"] or dest["alias"]))
            owner.setdefault("excludes", []).append(dict(ex, spec=spec))
        elif _parts(vault, new_abs) is not None:
            spec = os.path.relpath(_real(new_abs), _real(vault)).replace("\\", "/")
            raw.setdefault("vault_excludes", []).append(dict(ex, spec=spec))


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
        if e and e.get("type") and (Path(vault) / synthesis.card_rel(e, vault)).is_file():
            rel = synthesis.card_rel(e, vault)
            if rel not in out:
                out.append(rel)
    return out


def _replace_into(vault, plan, turn_id):
    """--replace：先把這份移到目標夾裡的暫名（驗過改名做得到、同一顆硬碟），再把目標丟回收筒，最後換成目標的名字。
    丟之前出事就把這份搬回原位；丟了之後換名失敗，說舊的那份已經進回收筒。回 do_trash 的結果。"""
    src, dst = Path(plan["src"]), Path(plan["dst"])
    _check_deletable(dst, plan["new_name"])
    tmp = Path(plan["dest_dir"]) / f".kb-move-{secrets.token_hex(4)}{src.suffix}"
    _transfer(src, tmp, plan)  # PermissionError 由呼叫端說
    try:
        tplan = plan_trash(vault, sources_lib.load(vault), dst, os.getcwd())
        replaced = do_trash(vault, tplan, turn_id)
    except BaseException:
        _untransfer(tmp, src, plan)
        raise
    try:
        _rename(tmp, dst)
    except OSError as e:
        try:
            _untransfer(tmp, src, plan)
        except OSError:
            raise Refused(REPLACED_STUCK.format(name=plan["new_name"])) from e
        raise Refused(REPLACED_NOT_MOVED.format(name=plan["new_name"], why=_why(e))) from e
    return replaced


# ---------- 丟資源回收筒 ----------

def plan_trash(vault, sources, target, cwd):
    """trash 的事前檢查與後果，不動任何東西。不行丟 Refused。收檔也收夾（放寬甲）：夾裡每份檔各算一份計畫，空夾直接拿掉；
    回收筒進不進得去不在這裡判（do_trash 進不了就先留一份進回合備份）。"""
    entry, path = resolve(vault, sources, target, cwd)
    check_from(vault, sources, path)
    uri = uri_of(vault, sources, path)
    if path.is_dir():
        files = _files_under(path)
        subs = []
        for f in files:
            sub_uri = uri_of(vault, sources, Path(f))
            sub_entry = inventory.entry_for(vault, sub_uri) if sub_uri else None
            subs.append(_file_plan(vault, sources, sub_entry, Path(f)))
        has_dirs = any(dirs for _r, dirs, _f in os.walk(path))
        return {"path": str(path), "name": path.name, "uri": uri, "kind": "folder", "empty": not files and not has_dirs,
                "files": len(files), "subs": subs, "size": sum(x["size"] for x in subs), "entry_id": None, "cards": [],
                "card": None, "synthesis": [], "others": 0, "touched": [], "where": _where_of(uri, path),
                "onedrive": "cloud" if any(x["onedrive"] == "cloud" for x in subs) else ("local" if in_onedrive(path) else None)}
    if entry is None and uri:
        entry = inventory.entry_for(vault, uri)
    return _file_plan(vault, sources, entry, path)


def _where_of(uri, path):
    alias, rel = inventory.split_uri(uri or "")
    return "\\".join([alias] + rel.split("/")[:-1]) if uri else str(path.parent)


def _file_plan(vault, sources, entry, path):
    from kb._lib import cards
    uri = uri_of(vault, sources, path)
    size = os.path.getsize(path)
    cloud = cloud_only(path)
    others = [loc for loc in (entry or {}).get("locations") or []
              if uri and inventory.uri_key(loc.get("uri", "")) != inventory.uri_key(uri)]
    group = (_cards_of(vault, [entry["id"]]).get(entry["id"]) or []) if entry and not others else []
    names = _synthesis_names(vault, cards.entity_names_of(group))
    return {"path": str(path), "name": path.name, "uri": uri, "kind": "file", "where": _where_of(uri, path),
            "entry_id": entry["id"] if entry else None,
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
        if e and e.get("status") == "active" and e.get("type") and (Path(vault) / synthesis.card_rel(e, vault)).is_file():
            if e["name"] not in out:
                out.append(e["name"])
    return out


TRASH_THIRD = {None: "說「退回上一版」救不回來，刪錯要從資源回收筒拿",
               "local": "刪錯要從資源回收筒拿；別台電腦與 OneDrive 上的那份也會一起刪",
               "cloud": "這份只在雲端：會從你所有裝置一起刪，要救得到 OneDrive 網頁的回收筒"}


def trash_lines(plan):
    if plan["kind"] == "folder":
        if plan["empty"]:
            return [f"要拿掉空夾：{plan['name']}（{plan['where']}）", "說「退回上一版」會建回來"]
        n = sum(1 for x in plan["subs"] if x["card"])
        return [f"要丟進資源回收筒：{plan['name']} 整個夾（{plan['files']} 份，{plan['where']}）",
                f"其中 {n} 份的卡會拿掉" if n else "裡面的檔沒有卡", TRASH_THIRD[plan["onedrive"]]]
    second = "同一份內容在別處還有，卡留著" if plan["others"] else ("那張卡會拿掉" if plan["card"] else "這份沒有卡")
    if plan["synthesis"]:
        second += "；綜合卡要更新：" + "、".join(plan["synthesis"])
    return [f"要丟進資源回收筒：{plan['name']}（{plan['where']}）", second, TRASH_THIRD[plan["onedrive"]]]


def trash_sentence(plan, result):
    """成功句只講刪了什麼、救不救得回來（D-305）；卡的細節只在 --json。"""
    kept = result.get("kept")
    if kept == "removed":
        return f"已拿掉空夾：{plan['name']}（說「退回」會建回來）"
    if kept == "permanent":
        return f"已刪：{plan['name']}（沒有留，救不回來）"
    if plan["onedrive"] == "cloud":
        return f"已刪：{plan['name']}（這份只在雲端，從使用者所有裝置一起刪了，要救得到 OneDrive 網頁的回收筒）"
    head = f"已丟進資源回收筒：{plan['name']}" if kept != "backup" else f"已刪：{plan['name']}（先留了一份，說「退回」會放回來）"
    if plan["onedrive"] == "local":
        head += "；別台電腦與 OneDrive 上的那份也一起刪了"
    return head


def _size_of(path):
    return sum(os.path.getsize(f) for f in _files_under(path) if os.path.exists(f))


def _clear_readonly(path):
    """唯讀的先取消唯讀（放寬甲）；夾就逐份。"""
    import stat
    for f in _files_under(path):
        try:
            if not os.access(f, os.W_OK):
                os.chmod(f, os.stat(f).st_mode | stat.S_IWRITE)
        except OSError:
            pass


def _backup_key(vault, sources, path):
    """備份的鍵：知識庫裡是相對路徑；登記的來源是 _src/<別名>/<相對路徑>。"""
    parts = _parts(vault, path)
    if parts is not None:
        return "/".join(parts)
    uri = uri_of(vault, sources, path)
    alias, rel = inventory.split_uri(uri)
    return f"_src/{alias}/{rel}"


def _backup_tree(vault, sources, path, turn_id):
    """把這份（夾就逐份）留進回合備份，比內容指紋一樣；回 True，有一份對不上回 False。"""
    ok = True
    for f in _files_under(path):
        if _parts(vault, f) is not None:
            turn.backup(vault, "/".join(_parts(vault, f)), turn_id, action="delete")
        else:
            turn.backup_source(vault, f, None, turn_id, sources)
        kept = turn.turn_dir(vault, turn_id) / "files" / _backup_key(vault, sources, f)
        ok = ok and kept.is_file() and _same_content(f, kept)
    return ok


def _discard(vault, sources, path, turn_id, permanent=False):
    """把原檔（或夾）拿掉、救得回來：先丟資源回收筒；進不了（隨身碟、網路磁碟沒有回收筒、超過上限）就先留一份進回合備份、
    比內容指紋一樣再刪；連備份都放不下回 Refused（NO_ROOM，AI 問使用者），說確定才 permanent。回 "trash"｜"backup"｜"permanent"。"""
    from kb._lib import config
    path = str(path)
    name = Path(path).name
    size = _size_of(path)
    try:
        if _drive_type(path) != DRIVE_FIXED:
            raise BinUnavailable(REMOVABLE)
        if not os.path.isdir(path) and not cloud_only(path):
            used, cap = recycle_room(path)
            if used + size > cap:
                raise BinUnavailable(BIN_TOO_BIG.format(size=size_text(size)))
        shell_trash(path)
        return "trash"
    except BinUnavailable:
        pass
    free = shutil.disk_usage(str(vault)).free
    need = size + int(config.get(vault, "trash_backup_margin_mb")) * 1024 * 1024
    if free >= need:
        if not _backup_tree(vault, sources, path, turn_id):
            raise Refused(NOT_MOVED.format(why="留的那份跟原檔對不上，沒有刪"))
        _remove_path(path)
        return "backup"
    if permanent:
        _remove_path(path)
        return "permanent"
    raise Refused(NO_ROOM.format(name=name, need=size_text(need - free)))


def _forget(vault, plan, turn_id):
    """檔被拿掉之後清冊與卡跟上（kb cards drop 那條）：條目 deleted、卡拿掉（備份）、待處理關掉、推測日程取消。
    回 (changed, card_removed, cancelled)。"""
    from kb._lib import agenda, cards, pending
    changed, card_removed, cancelled = [], False, []
    fid = plan["entry_id"]
    if fid is None:
        return changed, False, []
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
    return changed, card_removed, [i["id"] for i in cancelled]


def do_trash(vault, plan, turn_id, permanent=False):
    """丟資源回收筒（進不了先留進回合備份，見 _discard），再照 kb cards drop 那條跟上。同一份內容還在別處（清冊多個位置）
    只拿掉這個位置。收夾：整夾一次拿掉，夾裡每份檔的清冊與卡跟上；空夾直接 rmdir、回合記一行。
    回 {"changed","card_removed","cancelled","kept","permanent"}（kept：trash｜backup｜permanent｜removed）。"""
    sources = sources_lib.load(vault)
    path = plan["path"]
    if plan["kind"] == "folder":
        return _trash_folder(vault, sources, plan, turn_id, permanent)
    _check_deletable(path, plan["name"])
    _clear_readonly(path)
    kept = _discard(vault, sources, path, turn_id, permanent)
    if plan["uri"] and kept in ("trash", "permanent"):
        turn.record_move(vault, turn_id, "trash", "file", plan["uri"], None)
    changed, card_removed, cancelled = _forget(vault, plan, turn_id)
    return {"changed": changed, "card_removed": card_removed, "cancelled": cancelled, "kept": kept,
            "permanent": kept == "permanent"}


def remove_empty_folder(vault, sources, path, turn_id):
    """空夾直接拿掉（os.rmdir）、回合記一行（知識庫裡記 folders.jsonl 的 remove，退回會建回來）。夾不空回 False。"""
    path = Path(path)
    if any(True for _ in os.scandir(path)):
        return False
    _clear_readonly(path)
    try:
        os.rmdir(path)
    except OSError as e:
        raise Refused(NOT_MOVED.format(why=_why(e))) from e
    parts = _parts(vault, path)
    if parts is not None:
        turn.record_folder(vault, turn_id, "remove", "/".join(parts))
    else:
        uri = uri_of(vault, sources, path)
        if uri:
            turn.record_move(vault, turn_id, "trash", "folder", uri, None)
    return True


def _trash_folder(vault, sources, plan, turn_id, permanent):
    path = plan["path"]
    if plan["empty"]:
        remove_empty_folder(vault, sources, path, turn_id)
        return {"changed": [], "card_removed": False, "cancelled": [], "kept": "removed", "permanent": False}
    for sub in plan["subs"]:
        _check_deletable(sub["path"], sub["name"])
    _clear_readonly(path)
    kept = _discard(vault, sources, path, turn_id, permanent)
    if plan["uri"] and kept in ("trash", "permanent"):
        turn.record_move(vault, turn_id, "trash", "folder", plan["uri"], None)
    changed, removed, cancelled = [], False, []
    for sub in plan["subs"]:
        c, r, x = _forget(vault, sub, turn_id)
        changed += c
        removed = removed or r
        cancelled += x
    return {"changed": changed, "card_removed": removed, "cancelled": cancelled, "kept": kept,
            "permanent": kept == "permanent"}


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
