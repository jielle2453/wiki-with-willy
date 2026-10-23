"""kb unlock 本體（密碼與守門第 2 點；D-180、D-181、D-050）：密碼存 Windows 憑證管理員，知識庫裡不留任何密碼。

憑證管理員：ctypes 叫 advapi32 的 CredWriteW／CredReadW／CredDeleteW／CredEnumerateW（CRED_TYPE_GENERIC、
CRED_PERSIST_LOCAL_MACHINE），標準函式庫就做得到。每筆的 TargetName 是 wiki-with-willy/<知識庫編號>/<種類>/<名稱>
（知識庫編號照 _lib/vault.ensure_vault_id，同一台電腦兩個知識庫不混）。種類兩種：「記下的密碼」（門禁這類客戶要記的）、
「PDF 密碼」（kb convert --saved 用）。同一個名稱只留一筆（換種類也是換掉）。值不進任何檔、不進錯誤訊息。
非 Windows 一律 Refused「這台電腦不是 Windows，存不了密碼」。
.system/unlock.json（schema 1）記授權過的種類與開過的檔，沒有任何密碼：
{"schema_version": 1, "kinds": [{label, credential: "PDF 密碼/<名稱>", examples: [id], date, turn}],
 "opened": [{id, label, date, turn}]}；讀改寫在鎖裡、寫前備進回合。
外部呼叫集中在 BACKEND（測試換成記憶體裡的替身）。
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

PREFIX = "wiki-with-willy"
KIND_NOTE = "記下的密碼"
KIND_PDF = "PDF 密碼"
KINDS = (KIND_NOTE, KIND_PDF)
UNLOCK_POSIX = ".system/unlock.json"
SCHEMA_VERSION = 1
NAME_MAX = 60
VALUE_MAX = 500
NOT_WINDOWS = "這台電腦不是 Windows，存不了密碼"
WRITE_FAILED = "憑證管理員寫不進去（{}）"
NOT_SAVED = "這台電腦沒有記「{}」"
BAD_NAME = "名稱不能空白、最多 60 字，也不能有 / \\ \" $ ` 這幾個字元"
BAD_VALUE = "密碼不能空白、最多 500 字"
BAD_KIND = "種類要是 記下的密碼、PDF 密碼 之一"
NOT_PDF = "「{}」不是 PDF 密碼"
NO_LABEL = "沒有「{}」這一種"
NO_FILE = "清冊沒有這份檔：{}"
_BAD_NAME_CHARS = re.compile(r"[/\\\"$`\x00-\x1f]")


class Refused(ValueError):
    """kb unlock 的拒絕：訊息就是給人看的那句（不含密碼）。"""


# ---------- 憑證管理員 ----------

class _WinCred:
    """advapi32 的四個函式。只在 Windows 用；錯誤一律丟 Refused（訊息是錯誤碼，不含值）。"""
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
        self.CRED = CREDENTIALW
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.write_ = api.CredWriteW
        self.write_.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self.write_.restype = wintypes.BOOL
        self.read_ = api.CredReadW
        self.read_.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self.read_.restype = wintypes.BOOL
        self.delete_ = api.CredDeleteW
        self.delete_.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.delete_.restype = wintypes.BOOL
        self.enum_ = api.CredEnumerateW
        self.enum_.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                               ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIALW)))]
        self.enum_.restype = wintypes.BOOL
        self.free_ = api.CredFree
        self.free_.argtypes = [ctypes.c_void_p]
        self.free_.restype = None

    def _error(self):
        return f"錯誤碼 {self.ctypes.get_last_error()}"

    def write(self, target, secret, user):
        blob = secret.encode("utf-16-le")
        buf = (self.ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self.CRED()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = PREFIX
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = self.ctypes.cast(buf, self.ctypes.POINTER(self.ctypes.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = user
        if not self.write_(self.ctypes.byref(cred), 0):
            raise Refused(WRITE_FAILED.format(self._error()))

    def read(self, target):
        out = self.ctypes.POINTER(self.CRED)()
        if not self.read_(target, self.CRED_TYPE_GENERIC, 0, self.ctypes.byref(out)):
            if self.ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                return None
            raise Refused(f"憑證管理員讀不到（{self._error()}）")
        try:
            c = out.contents
            data = self.ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            return data.decode("utf-16-le")
        finally:
            self.free_(out)

    def delete(self, target):
        if not self.delete_(target, self.CRED_TYPE_GENERIC, 0):
            if self.ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                return False
            raise Refused(f"憑證管理員刪不掉（{self._error()}）")
        return True

    def targets(self, prefix):
        from ctypes import wintypes
        count = wintypes.DWORD()
        arr = self.ctypes.POINTER(self.ctypes.POINTER(self.CRED))()
        if not self.enum_(prefix + "*", 0, self.ctypes.byref(count), self.ctypes.byref(arr)):
            if self.ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                return []
            raise Refused(f"憑證管理員列不出來（{self._error()}）")
        try:
            return [arr[i].contents.TargetName for i in range(count.value)]
        finally:
            self.free_(arr)


class MemoryBackend:
    """測試用替身：同樣四個方法，存在記憶體。"""

    def __init__(self):
        self.store = {}

    def write(self, target, secret, user):
        self.store[target] = secret

    def read(self, target):
        return self.store.get(target)

    def delete(self, target):
        return self.store.pop(target, None) is not None

    def targets(self, prefix):
        return [t for t in self.store if t.startswith(prefix)]


BACKEND = None  # None：Windows 用 _WinCred，其他 Refused(NOT_WINDOWS)；測試換成 MemoryBackend()


def _backend():
    global BACKEND
    if BACKEND is not None:
        return BACKEND
    if sys.platform != "win32":
        raise Refused(NOT_WINDOWS)
    BACKEND = _WinCred()
    return BACKEND


def _base(vault):
    from kb._lib import vault as vault_lib
    return f"{PREFIX}/{vault_lib.ensure_vault_id(vault)}/"


def check_name(name):
    name = str(name or "").strip()
    if not name or len(name) > NAME_MAX or _BAD_NAME_CHARS.search(name):
        raise Refused(BAD_NAME)
    return name


def _kind(kind):
    kind = str(kind or "").strip()
    if kind not in KINDS:
        raise Refused(BAD_KIND)
    return kind


def entries(vault):
    """這個知識庫存在這台電腦的密碼：[(種類, 名稱)]，照名稱排；不讀值。"""
    base = _base(vault)
    out = []
    for target in _backend().targets(base):
        rest = target[len(base):]
        kind, _, name = rest.partition("/")
        if kind in KINDS and name:
            out.append((kind, name))
    return sorted(out, key=lambda kn: kn[1])


def find(vault, name):
    """回 (種類, TargetName) 或 None。"""
    name = check_name(name)
    base = _base(vault)
    for kind in KINDS:
        target = f"{base}{kind}/{name}"
        if (kind, name) in entries(vault):
            return kind, target
    return None


def save(vault, kind, name, value):
    """存；同名（不論種類）換掉。回換掉了嗎。"""
    kind, name = _kind(kind), check_name(name)
    value = str(value if value is not None else "")
    if not value.strip() or len(value) > VALUE_MAX:
        raise Refused(BAD_VALUE)
    old = find(vault, name)
    target = f"{_base(vault)}{kind}/{name}"
    _backend().write(target, value, name)
    if old and old[1] != target:
        _backend().delete(old[1])
    return old is not None


def get(vault, name):
    """回 (種類, 值)；沒有丟 Refused(NOT_SAVED)。"""
    hit = find(vault, name)
    if hit is None:
        raise Refused(NOT_SAVED.format(check_name(name)))
    value = _backend().read(hit[1])
    if value is None:
        raise Refused(NOT_SAVED.format(check_name(name)))
    return hit[0], value


SAVED_VALUE_MIN = 4  # 短於這個的值不拿來比（1、ab 這種到處都有，會誤判）


def saved_values(vault):
    """這個知識庫存在憑證管理員的值（4 個字以上），給 Stop 比對回答用（桌面版確認後縱深防禦）。只讀：
    知識庫還沒有編號（從沒存過）回空清單，不建 vault.json。讀不到丟 Refused 或 OSError，呼叫端跳過、寫 log。"""
    from kb._lib import vault as vault_lib
    vid = vault_lib.vault_id(vault)
    if not vid or (BACKEND is None and sys.platform != "win32"):
        return []  # 沒編號或不是 Windows：本來就存不了，不算讀不到
    backend = _backend()
    values = []
    for target in backend.targets(f"{PREFIX}/{vid}/"):
        value = backend.read(target)
        if value and len(value) >= SAVED_VALUE_MIN:
            values.append(value)
    return values


def forget(vault, name, turn_id):
    """刪那一筆，連用它授權的種類一起拿掉（unlock.json）。回拿掉的種類 label 清單。"""
    hit = find(vault, name)
    if hit is None:
        raise Refused(NOT_SAVED.format(check_name(name)))
    _backend().delete(hit[1])
    credential = f"{hit[0]}/{check_name(name)}"

    def fn(data):
        gone = [k["label"] for k in data["kinds"] if k.get("credential") == credential]
        data["kinds"] = [k for k in data["kinds"] if k.get("credential") != credential]
        return gone, bool(gone)
    return _mutate(vault, turn_id, fn)


# ---------- .system/unlock.json ----------

def _path(vault):
    return Path(vault) / UNLOCK_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "kinds": [], "opened": []}


def load(vault):
    """沒檔回空的；形狀不對丟 ValueError（訊息帶檔名）。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{UNLOCK_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if (not isinstance(data, dict) or not isinstance(data.get("kinds"), list) or not isinstance(data.get("opened"), list)
            or any(not isinstance(k, dict) for k in data["kinds"] + data["opened"])):
        raise ValueError(f"{UNLOCK_POSIX}（形狀不對）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{UNLOCK_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "kinds": data["kinds"], "opened": data["opened"]}


def _mutate(vault, turn_id, fn):
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        result, changed = fn(data)
        if changed:
            from kb._lib import turn
            turn.backup(vault, UNLOCK_POSIX, turn_id)
            write_json_atomic(p, data)
    return result


def _today():
    return date.today().isoformat()


def _example_ids(vault, examples):
    from kb._lib import rulings
    ids = []
    for ex in examples:
        try:
            ids.append(rulings.file_entry(vault, ex)["id"])
        except rulings.RulingRefused:
            raise Refused(NO_FILE.format(ex)) from None
    return list(dict.fromkeys(ids))


def authorize(vault, name, label, examples, turn_id):
    """記一種（D-181 第 2 點）：這一種的 PDF 用「name」那組密碼開。label 同名換掉。回種類條目。"""
    from kb._lib import statements
    hit = find(vault, name)
    if hit is None:
        raise Refused(NOT_SAVED.format(check_name(name)))
    if hit[0] != KIND_PDF:
        raise Refused(NOT_PDF.format(check_name(name)))
    label = statements.check_text(label, "種類名稱", Refused)
    ids = _example_ids(vault, examples or [])
    item = {"label": label, "credential": f"{KIND_PDF}/{check_name(name)}", "examples": ids, "date": _today(), "turn": turn_id}

    def fn(data):
        data["kinds"] = [k for k in data["kinds"] if k.get("label") != label] + [item]
        return item, True
    return _mutate(vault, turn_id, fn)


def revoke(vault, label, turn_id):
    def fn(data):
        if not any(k.get("label") == label for k in data["kinds"]):
            raise Refused(NO_LABEL.format(label))
        data["kinds"] = [k for k in data["kinds"] if k.get("label") != label]
        return label, True
    return _mutate(vault, turn_id, fn)


def kind_for(vault, name):
    """「name」那組 PDF 密碼授權過的種類（第一個）；沒有回 None。unlock.json 讀不到當沒有。"""
    try:
        kinds = load(vault)["kinds"]
    except ValueError:
        return None
    credential = f"{KIND_PDF}/{name}"
    return next((k for k in kinds if k.get("credential") == credential), None)


def record_opened(vault, fid, label, turn_id):
    """kb convert --saved 開成功：記進 opened（同一份同一種不重記）。"""
    def fn(data):
        if any(o.get("id") == fid and o.get("label") == label for o in data["opened"]):
            return False, False
        data["opened"].append({"id": fid, "label": label, "date": _today(), "turn": turn_id})
        return True, True
    return _mutate(vault, turn_id, fn)


def opened_in_turn(vault, turn_id):
    """這一回合用存好的密碼開了哪些：[{id, label}]（給 tell）；讀不到回空。"""
    try:
        return [o for o in load(vault)["opened"] if o.get("turn") == turn_id]
    except ValueError:
        return []


def os_is_windows():
    return os.name == "nt"
