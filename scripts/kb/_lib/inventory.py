"""清冊 .system/inventory.json（schema 2，D-093、D-094、D-095、D-096；最小版 D-076 的延伸）。

一筆一份內容：id（f- 加 8 碼 hex；舊的 out- 不改）、origin（client_file｜intake｜ai_output）、
locations（[{uri, size, mtime}]，uri 是 file://<來源別名>/<相對路徑>，正斜線、原始大小寫；同指紋多位置一筆，第一個是主位置）、
fingerprints（[{sha256, text_sha256?, time, turn}]，最後一筆是現行；text_sha256 只有 OOXML 算得出來才有，D-096）、
read（ok｜placeholder｜credential｜too_large｜error）、status（active｜missing｜excluded｜turn_reverted｜deleted｜superseded）、
first_seen、created_turn、missing_since。選填欄 skip_kind、agenda_fp、redo 由各自的 set_ 寫，scan／apply／reconcile 原樣保留；
redo（note 改知識第 5 點）記的是標記當時卡檔的 sha256 前 12 碼，卡重寫過就不算，下一次寫清冊時拿掉（_drop_stale_redo）。
load 時把 schema 1 在記憶體遷移成 2（I-049），形狀不對一律丟 ValueError（I-046）；寫一律寫 2。

scan 是純函式（不寫檔、不碰回合）：走訪來源與總資料夾三區，跟清冊比出新增、修改、搬動、重複、不見；
apply 在清冊的 FileLock 裡先備進回合（D-074）再寫，順手把這次新指紋與新條目的回合填上；
reconcile 給 folders exclude／remove：排除或拿掉的位置從條目拿掉，全沒了就 excluded。
位置比對用 uri_key（casefold、正斜線）。位置全沒了（missing／excluded）時保留最後的位置清單，好列名字、比大小、恢復。
開不了的夾、快速模式時間到沒走到的部分，底下已知位置原樣不動，不算不見。
指紋先用位元組 sha256（B-018），OOXML 另算內容指紋。讀改寫整段包 FileLock（I-080）。
"""
import copy
import fnmatch
import hashlib
import os
import secrets
import stat as stat_lib
import time
from datetime import datetime
from pathlib import Path

from kb._lib import config, content_fp, credentials
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, read_json, write_json_atomic
from kb._lib.paths import inside, long_path, normalize

INVENTORY_REL = Path(".system") / "inventory.json"
INVENTORY_POSIX = ".system/inventory.json"
SCHEMA_VERSION = 2
EMPTY = {"schema_version": SCHEMA_VERSION, "entries": []}

KNOWLEDGE, INBOX, OUTPUT = "知識庫", "收件匣", "AI 產出"  # 總資料夾自身的三個保留別名（D-042）
RESERVED_CF = {a.casefold() for a in (KNOWLEDGE, INBOX, OUTPUT)}
# 知識庫根第一層不當型別夾的保留夾（跟 guard.RESERVED 同一組；隱藏夾另由「. 開頭」跳過）
VAULT_RESERVED_DIRS = {"ai 產出", "收件匣", "檔案索引", "系統"}
SKIP_NAMES = ("~$*", "*.tmp", "Thumbs.db", "desktop.ini", ".DS_Store")
HIDDEN_OR_SYSTEM = 0x2 | 0x4
REPARSE_POINT = 0x400
# 雲端占位檔（照舊 kbcommon.is_cloud_placeholder，I-020）：讀它會觸發下載
PLACEHOLDER_ATTRS = 0x40000 | 0x400000 | 0x1000
LIVE = ("active", "turn_reverted")  # 位置索引收這兩種；turn_reverted 的成品檔還在 AI 產出
DORMANT = ("missing", "excluded")  # 位置全沒了：保留最後的位置清單
READ_PROBLEMS = ("placeholder", "credential", "too_large", "error")
EXAMPLES_MAX = 3
HASH_CHUNK = 1 << 20  # 算位元組指紋一次讀 1 MB，每塊之間可以喊停

_clock = time.monotonic  # 測試換掉它觸發時間預算


def _path(vault):
    return Path(vault) / INVENTORY_REL


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def mtime_str(mtime):
    """修改時間取整秒：YYYY-MM-DDTHH:MM:SS。"""
    return datetime.fromtimestamp(int(mtime)).strftime("%Y-%m-%dT%H:%M:%S")


def make_uri(alias, rel):
    return f"file://{alias}/{rel}"


def split_uri(uri):
    """file://<別名>/<相對路徑> → (別名, 相對路徑)。"""
    body = str(uri)[len("file://"):] if str(uri).startswith("file://") else str(uri)
    alias, _, rel = body.partition("/")
    return alias, rel


def uri_key(uri):
    return str(uri).replace("\\", "/").casefold()


def _fs_key(path):
    """skip_roots 的比對鍵：正斜線、去尾斜線，Windows 小寫（跟 normalize 的結果同形，不走 realpath）。"""
    s = str(path).replace("\\", "/").rstrip("/")
    return s.lower() if os.name == "nt" else s


# ---------- 讀寫與遷移 ----------

def load(vault):
    """回 {"schema_version": 2, "entries": [...]}。檔不在回空的；壞 JSON、形狀不對、版本比 2 新丟 ValueError。"""
    data = read_json(_path(vault))
    if data is None:
        return copy.deepcopy(EMPTY)
    if not isinstance(data, dict):
        raise ValueError("清冊不是物件")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("清冊沒有 entries 清單")
    if any(not isinstance(e, dict) for e in entries):
        raise ValueError("清冊有一筆不是物件")
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"清冊版本 {version!r} 比這版 plugin 新")
    if version < SCHEMA_VERSION:
        entries = [_migrate(vault, e) for e in entries]
    return {"schema_version": SCHEMA_VERSION, "entries": entries}


def _migrate(vault, entry):
    """schema 1 → 2：位置字串包成物件（檔在補大小時間，不在 null）、created 改名 first_seen、補 read 與 missing_since。"""
    e = dict(entry)
    locs = []
    for loc in e.get("locations") or []:
        if isinstance(loc, dict):
            locs.append(loc)
            continue
        uri = str(loc)
        size = mtime = None
        try:
            st = os.stat(long_path(Path(vault) / uri[len("file://"):]))
            size, mtime = st.st_size, mtime_str(st.st_mtime)
        except (OSError, ValueError):
            pass
        locs.append({"uri": uri, "size": size, "mtime": mtime})
    e["locations"] = locs
    if "first_seen" not in e:
        e["first_seen"] = e.get("created")
    e.pop("created", None)
    e.setdefault("fingerprints", [])
    e.setdefault("read", "ok")
    e.setdefault("missing_since", None)
    return e


def _write(vault, entries):
    """呼叫端持有清冊的鎖。"""
    _drop_stale_redo(vault, entries)
    write_json_atomic(_path(vault), {"schema_version": SCHEMA_VERSION, "entries": entries})


def card_sha(vault, rel):
    """檔案卡檔的 sha256 前 12 碼（redo 記號比對用）；讀不了回 None。"""
    try:
        return hashlib.sha256((Path(vault) / rel).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def _drop_stale_redo(vault, entries):
    """有 redo 的條目：卡不見了、或卡檔現在的 sha256 前 12 碼跟記號不同（重寫過）就拿掉這欄。沒有記號的時候不掃卡。"""
    marked = [e for e in entries if e.get("redo")]
    if not marked:
        return
    from kb._lib import cards
    groups = cards._scan_groups(vault)[0]
    for e in marked:
        group = groups.get(e.get("id"))
        if not group or card_sha(vault, group[0]["path"]) != e["redo"]:
            e.pop("redo", None)


def sha256_of(path, should_stop=None):
    """分塊讀；第一塊之後每讀一塊前問 should_stop，喊停回 None（開場的時間預算，審閱裁定 34）。"""
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        for i, chunk in enumerate(iter(lambda: f.read(HASH_CHUNK), b"")):
            if i and should_stop is not None and should_stop():
                return None
            h.update(chunk)
    return h.hexdigest()


def _new_id(taken):
    while True:
        new = "f-" + secrets.token_hex(4)
        if new not in taken:
            return new


def _loc_of(path, uri):
    st = os.stat(long_path(path))
    return {"uri": uri, "size": st.st_size, "mtime": mtime_str(st.st_mtime)}


def _set_read(entry, read, error=None):
    entry["read"] = read
    if read == "error":
        entry["error"] = error or ""
    else:
        entry.pop("error", None)


# ---------- AI 產出成品登記（D-076） ----------

def _manifest_action(vault, rel, turn_id):
    from kb._lib import turn
    for e in turn.entries(vault, turn_id):
        if e.get("path") == rel:
            return e.get("action")
    return None


def _has_location(entry, key):
    return any(uri_key(loc.get("uri", "")) == key for loc in entry.get("locations", []))


def register_output(vault, rel, turn_id):
    """同 location 有 status active 的條目：最後一筆指紋相同就不動，不同就追加一筆（位置大小時間跟著更新），回那一筆。
    沒有 active 條目：這回合 manifest 記 create 才新建；曾登記過（turn_reverted／deleted）又再改也新建；
    從沒登記過的既有檔（modify）不登記、回 None（第一輪 M2／M4）。
    """
    uri = "file://" + str(rel).replace("\\", "/")
    key = uri_key(uri)
    target = Path(vault) / rel
    digest = sha256_of(target)
    now = _now()
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        same = [e for e in data["entries"] if _has_location(e, key)]
        active = [e for e in same if e.get("status") == "active"]
        if active:
            entry = active[-1]
            fps = entry.setdefault("fingerprints", [])
            if fps and fps[-1].get("sha256") == digest:
                return entry
            fps.append({"sha256": digest, "time": now, "turn": turn_id})
            entry["locations"] = [_loc_of(target, loc["uri"]) if uri_key(loc.get("uri", "")) == key else loc
                                  for loc in entry["locations"]]
            _write(vault, data["entries"])
            return entry
        action = _manifest_action(vault, rel, turn_id)
        if not (action == "create" or (action == "modify" and same)):
            return None
        for old in same:  # 同位置後來有新成品：舊的非 active 條目改 superseded（第二輪 R2-3）
            if old.get("status") != "active":
                old["status"] = "superseded"
        entry = {
            "id": _new_id({e.get("id") for e in data["entries"]}),
            "origin": "ai_output",
            "locations": [_loc_of(target, uri)],
            "fingerprints": [{"sha256": digest, "time": now, "turn": turn_id}],
            "read": "ok",
            "status": "active",
            "first_seen": now,
            "created_turn": turn_id,
            "missing_since": None,
        }
        data["entries"].append(entry)
        _write(vault, data["entries"])
        return entry


def outputs_of_turn(vault, turn_id):
    return [e for e in load(vault)["entries"] if e.get("created_turn") == turn_id and e.get("status") == "active"]


def set_status(vault, ids, status):
    ids = set(ids)
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        for entry in data["entries"]:
            if entry.get("id") in ids:
                entry["status"] = status
        _write(vault, data["entries"])


def set_skip(vault, ids, kind, locked=False):
    """選填欄 skip_kind（客戶設定檔說「不整理」的種類，D-113）：kind 是 None 就拿掉欄。呼叫端先備份。
    locked：呼叫端已持有清冊的鎖（cards skip 整段包在鎖裡，測試後修正裁定 13）。
    scan／apply／reconcile 對既有條目原樣保留它，不在 _migrate 裡補。"""
    ids = set(ids)
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)

    def change():
        data = load(vault)
        for entry in data["entries"]:
            if entry.get("id") in ids:
                if kind is None:
                    entry.pop("skip_kind", None)
                else:
                    entry["skip_kind"] = kind
        _write(vault, data["entries"])
    if locked:
        change()
        return
    with FileLock(p):
        change()


def set_agenda_fp(vault, fid, fp, turn_id):
    """選填欄 agenda_fp（D-140）：製卡者對這張卡抽完日期時，那張卡的指紋（前 12 碼，同日程條目的 origin.fp）。
    卡重做指紋就變，記號自然不算。scan／apply／reconcile 對既有條目原樣保留它（同 skip_kind）。
    鎖裡備份再寫，值沒變什麼都不做；回有沒有寫。清冊沒有這個編號丟 KeyError。"""
    p = _path(vault)
    with FileLock(p):
        data = load(vault)
        entry = next((e for e in data["entries"] if e.get("id") == fid), None)
        if entry is None:
            raise KeyError(fid)
        if entry.get("agenda_fp") == fp:
            return False
        from kb._lib import turn
        turn.backup(vault, INVENTORY_POSIX, turn_id)
        entry["agenda_fp"] = fp
        _write(vault, data["entries"])
    return True


def set_redo(vault, fid, value, turn_id):
    """選填欄 redo（kb cards redo，D-147 第一種）：value 是那張卡檔現在的 sha256 前 12 碼。鎖裡備份再寫，值沒變什麼都不做；
    回有沒有寫。清冊沒有這個編號丟 KeyError。"""
    p = _path(vault)
    with FileLock(p):
        data = load(vault)
        entry = next((e for e in data["entries"] if e.get("id") == fid), None)
        if entry is None:
            raise KeyError(fid)
        if entry.get("redo") == value:
            return False
        from kb._lib import turn
        turn.backup(vault, INVENTORY_POSIX, turn_id)
        entry["redo"] = value
        _write(vault, data["entries"])
    return True


def unskip_kinds(vault, kinds, turn_id=None):
    """skip_kind 是 kinds 之一的條目拿掉該欄，回動到的 id；有動到才在鎖裡備份（給了 turn_id）、寫。"""
    kinds = set(kinds)
    p = _path(vault)
    if not kinds or not p.exists():
        return []
    with FileLock(p):
        data = load(vault)
        hits = [e for e in data["entries"] if e.get("skip_kind") in kinds]
        if not hits:
            return []
        if turn_id:
            from kb._lib import turn
            turn.backup(vault, INVENTORY_POSIX, turn_id)
        for e in hits:
            e.pop("skip_kind", None)
        _write(vault, data["entries"])
    return [e["id"] for e in hits]


# ---------- 走訪 ----------

def _skipped_name(name):
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pat.lower()) for pat in SKIP_NAMES)


def walk(root, skip_roots=(), should_stop=None):
    """os.scandir 迭代（不遞迴呼叫）、經 long_path，回 (相對路徑, stat, None) 或 (相對路徑, None, 錯誤一句)。
    跳過 . 開頭、隱藏或系統屬性、重解析點（不跟不列，I-021；帶占位屬性的檔例外，交給 read_kind 判 placeholder）、
    skip_roots（排除夾、排除單檔、總資料夾）、暫存名。開不了的夾 yield 錯誤繼續走。
    should_stop 每走完一個夾問一次，回真就停。同一個夾裡照名字排，結果可重現。"""
    base = str(root).replace("\\", "/").rstrip("/")
    skip = {_fs_key(s) for s in skip_roots}
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        here = f"{base}/{rel_dir}" if rel_dir else base
        try:
            with os.scandir(long_path(here)) as it:
                items = sorted(it, key=lambda item: item.name)
        except OSError as e:
            yield rel_dir, None, f"無法開啟資料夾：{e.strerror or e}"
            continue
        subdirs = []
        for item in items:
            name = item.name
            if name.startswith("."):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if skip and _fs_key(f"{base}/{rel}") in skip:
                continue
            try:
                st = item.stat(follow_symlinks=False)
                is_link = item.is_symlink()
            except OSError as e:
                yield rel, None, f"無法讀取：{e.strerror or e}"
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            if is_link or attrs & HIDDEN_OR_SYSTEM:
                continue
            if stat_lib.S_ISDIR(st.st_mode):
                if not attrs & REPARSE_POINT:
                    subdirs.append(rel)
            elif stat_lib.S_ISREG(st.st_mode):
                if attrs & REPARSE_POINT and not attrs & PLACEHOLDER_ATTRS:
                    continue
                if not _skipped_name(name):
                    yield rel, st, None
        stack.extend(reversed(subdirs))
        if should_stop is not None and should_stop():
            return


def read_kind(rel_segments, st, size_cap, credential=True):
    """回 (read, error)：占位檔 → placeholder；來源根以下的段命中憑證樣式 → credential（總資料夾內部 credential=False，D-081）；
    超過 size_cap → too_large；其餘 ok。error 只有算指紋失敗才有，這裡一律 None。"""
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & PLACEHOLDER_ATTRS:
        return "placeholder", None
    if credential and credentials.match_segment(rel_segments) is not None:
        return "credential", None
    if st.st_size > size_cap:
        return "too_large", None
    return "ok", None


def _raw_specs(vault):
    try:
        raw = sources_lib.load_raw(vault)
    except (OSError, ValueError):
        return {}
    return {(s.get("id") or s.get("alias", "")): s.get("spec", "") for s in raw["sources"]}


def _is_link(path):
    """跟 walk 一樣的重解析點判斷（symlink、junction、屬性 0x400）。"""
    try:
        st = os.lstat(long_path(path))
    except OSError:
        return False
    return stat_lib.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_POINT)


def roots(vault, sources):
    """回 (要掃的根, 這台電腦找不到的來源, 錯誤)。根：每個登記來源（排除與總資料夾不進去）、知識庫（型別夾）、收件匣、AI 產出。
    spec 展不開（只算 kind source，審閱裁定 2）或來源根不是資料夾 → missing_sources [{alias, spec, id}]。
    收件匣、AI 產出的根是連結就不掃（會走到總資料夾外、又不查憑證），錯誤記「<區> 是連結，不掃」（審閱裁定 38）。"""
    vault_s = str(vault)
    vault_n = normalize(vault_s, None)
    specs = _raw_specs(vault)
    out, missing, errors = [], [], []
    for u in sources.unresolved:
        if u.get("kind") == "source":
            missing.append({"alias": u["alias"], "spec": u["spec"], "id": u.get("id", "")})
    for src in sources.entries:
        if not os.path.isdir(long_path(src["root"])):
            missing.append({"alias": src["alias"], "spec": specs.get(src["id"] or src["alias"], src["root"]),
                            "id": src["id"]})
            continue
        skip = [ex["root"] for ex in src["excludes"]]
        if inside(vault_n, src["root"]):
            skip.append(vault_n)
        out.append({"alias": src["alias"], "path": src["root"], "origin": "client_file", "kind": "source",
                    "skip": skip, "credential": True})
    out.append({"alias": KNOWLEDGE, "path": vault_s, "origin": "client_file", "kind": "knowledge", "skip": [],
                "credential": False})
    for alias, origin in ((INBOX, "intake"), (OUTPUT, "ai_output")):
        area = os.path.join(vault_s, alias)
        if _is_link(area):
            errors.append({"path": f"file://{alias}", "error": f"{alias} 是連結，不掃"})
        elif os.path.isdir(long_path(area)):
            out.append({"alias": alias, "path": area, "origin": origin, "kind": "area", "skip": [], "credential": False})
    return out, missing, errors


def _is_card(rel, base=None):
    """型別夾第三層的 .md：檔名等於夾名，或檔頭前 4 KB 解析得到「編號」且以 e- 開頭（夾被改名、卡還是舊名，
    實體與綜合卡第 8 點），都是綜合卡、不入冊。讀不了的照舊入冊。"""
    parts = rel.split("/")
    if len(parts) != 3 or not parts[2].casefold().endswith(".md"):
        return False
    if parts[2].casefold() == (parts[1] + ".md").casefold():
        return True
    if base is None:
        return False
    from kb._lib.entities import head_id
    eid = head_id(long_path(os.path.join(base, *parts)))
    return bool(eid and eid.startswith("e-"))


def _iter_root(root, stopper):
    if root["kind"] != "knowledge":
        yield from walk(root["path"], root["skip"], stopper)
        return
    base = root["path"]
    try:
        with os.scandir(long_path(base)) as it:
            tops = sorted(it, key=lambda item: item.name)
    except OSError as e:
        yield "", None, f"無法開啟資料夾：{e.strerror or e}"
        return
    for item in tops:
        if stopper.stopped:
            return
        name = item.name
        if name.startswith(".") or name.casefold() in VAULT_RESERVED_DIRS:
            continue
        try:
            st = item.stat(follow_symlinks=False)
            is_link = item.is_symlink()
        except OSError:
            continue
        attrs = getattr(st, "st_file_attributes", 0)
        if attrs & HIDDEN_OR_SYSTEM:
            continue
        if is_link or attrs & REPARSE_POINT:  # 型別夾是連結：不掃，記一項（審閱裁定 38）
            yield name, None, f"{KNOWLEDGE}/{name} 是連結，不掃"
            continue
        if not stat_lib.S_ISDIR(st.st_mode):
            continue
        for rel, fst, err in walk(os.path.join(base, name), (), stopper):
            full = f"{name}/{rel}" if rel else name
            if err is None and _is_card(full, base):
                continue  # 綜合卡是 AI 筆記，不入冊
            yield full, fst, err


def _same_loc(dormant_hit, rec):
    e, loc = dormant_hit
    return (e.get("status") in DORMANT and loc.get("size") == rec["loc"]["size"]
            and loc.get("mtime") == rec["loc"]["mtime"])


class _Stopper:
    """快速模式的時間預算：每走完一個夾問一次，經過時間 >= 上限就停（審閱裁定 19），停了就一直停。"""

    def __init__(self, seconds):
        self.seconds = seconds
        self.start = _clock()
        self.stopped = False

    def __call__(self):
        if not self.stopped and self.seconds is not None and _clock() - self.start >= self.seconds:
            self.stopped = True
        return self.stopped


# ---------- 比對 ----------

def scan(vault, sources, *, quick=False, only_roots=None, now=None):
    """D-094 的比對，不寫檔、不碰回合（審閱裁定 6）。回 Report dict（見 spec「資料與介面格式」，多 still_missing、restored）。
    quick：開場用；新檔只在大小等於這次不見的位置、休眠條目或任何已知位置時才算指紋，受讀取預算與時間預算。
    only_roots：別名清單，只掃那些根；total、by_source、added 等都只算掃到的（審閱裁定 8）。"""
    now = now or _now()
    size_cap = config.get(vault, "fingerprint_max_bytes")
    budget = {"left": config.get(vault, "changes_quick_hash_budget_bytes") if quick else None, "out": False}
    stopper = _Stopper(config.get(vault, "changes_quick_seconds") if quick else None)
    entries = copy.deepcopy(load(vault)["entries"])
    all_roots, missing_sources, root_errors = roots(vault, sources)
    if only_roots is not None:
        wanted = {a.casefold() for a in only_roots}
        all_roots = [r for r in all_roots if r["alias"].casefold() in wanted]
        missing_sources = [m for m in missing_sources if m["alias"].casefold() in wanted]
        root_errors = [e for e in root_errors if split_uri(e["path"] + "/")[0].casefold() in wanted]
    report = {"added": [], "modified": [], "missing": [], "still_missing": [], "moved": [], "duplicates": [],
              "restored": [], "unchanged": 0, "total": 0, "by_source": {},
              "read_counts": {k: 0 for k in READ_PROBLEMS}, "read_examples": {}, "errors": [],
              "missing_sources": missing_sources, "incomplete": False, "entries": entries}

    # 1. 走訪
    found, walked, protected = [], set(), []
    for err in root_errors:  # 是連結的區：記錯誤，底下已知位置原樣不動
        report["errors"].append(err)
        protected.append(uri_key(err["path"]))
    for root in all_roots:
        if stopper.stopped:
            break
        alias = root["alias"]
        for rel, st, err in _iter_root(root, stopper):
            if err is not None:
                where = f"file://{alias}/{rel}" if rel else f"file://{alias}"
                report["errors"].append({"path": where, "error": err})
                protected.append(uri_key(where))
                continue
            segs = rel.split("/")
            read, _ = read_kind(segs, st, size_cap, credential=root["credential"])
            uri = make_uri(alias, rel)
            found.append({"uri": uri, "key": uri_key(uri), "path": os.path.join(root["path"], *segs), "st": st,
                          "read": read, "error": None, "root": root,
                          "loc": {"uri": uri, "size": st.st_size, "mtime": mtime_str(st.st_mtime)}})
        if not stopper.stopped:
            walked.add(alias.casefold())

    def checked(key):
        """這個位置這次有確實看過它在不在：根走完了、不在開不了的夾底下。"""
        if split_uri(key)[0] not in walked:
            return False
        return not any(key == p or key.startswith(p + "/") for p in protected)

    def out_of_time():
        """快速模式：每算一個指紋前查剩餘時間（審閱裁定 31）；到了就不再算，這次 incomplete。"""
        return stopper()

    def digest_of(rec):
        size = rec["st"].st_size
        if out_of_time():
            return None
        if budget["left"] is not None:
            if budget["out"] or size > budget["left"]:
                budget["out"] = True  # 讀取總量到頂後不再算（I-081）
                return None
            budget["left"] -= size
        try:
            digest = sha256_of(rec["path"], should_stop=stopper)
        except OSError as e:
            rec["read"], rec["error"] = "error", f"無法讀取：{e.strerror or e}"
            return None
        return digest  # 算到一半時間到是 None：放棄這個檔，這次 incomplete

    def text_of(rec):
        return None if out_of_time() else content_fp.text_fingerprint(rec["path"], should_stop=stopper)

    def fingerprint(digest, text):
        fp = {"sha256": digest}
        if text:
            fp["text_sha256"] = text
        fp.update(time=now, turn=None)
        return fp

    live = {}
    for e in entries:
        if e.get("status") in LIVE:
            for i, loc in enumerate(e.get("locations") or []):
                live.setdefault(uri_key(loc.get("uri", "")), e)

    # 2. 已知位置
    seen, unknown, changed = set(), [], {}
    for rec in found:
        e = live.get(rec["key"])
        if e is None:
            unknown.append(rec)
            continue
        seen.add(rec["key"])
        idx = next(i for i, loc in enumerate(e["locations"]) if uri_key(loc.get("uri", "")) == rec["key"])
        old = e["locations"][idx]
        same = old.get("size") == rec["loc"]["size"] and old.get("mtime") == rec["loc"]["mtime"]
        e["locations"][idx] = rec["loc"]  # 只改大小寫的改名：鍵相同算不動，存新大小寫（審閱裁定 12）
        if rec["read"] != "ok":
            _set_read(e, rec["read"])
            if same:
                report["unchanged"] += 1
            else:
                report["modified"].append(rec["uri"])
            continue
        fps = e.setdefault("fingerprints", [])
        if same:
            _set_read(e, "ok")
            if not fps and not quick:  # 之前是占位檔或太大、這次讀得到：補一筆指紋，不算變動
                digest = digest_of(rec)
                if digest:
                    fps.append(fingerprint(digest, text_of(rec)))
            report["unchanged"] += 1
            continue
        digest = digest_of(rec)
        if rec["read"] == "error":
            _set_read(e, "error", rec["error"])
            continue
        _set_read(e, "ok")
        if digest is None:  # 讀取預算用完：變了就當修改
            report["modified"].append(rec["uri"])
            continue
        last = fps[-1] if fps else None
        if last and last.get("sha256") == digest:
            report["unchanged"] += 1  # 假變動：只更新位置大小時間
            continue
        text = text_of(rec)
        if last and text and last.get("text_sha256") == text:
            last["sha256"] = digest  # OOXML 另存內容沒變（D-096）：現行那筆原地換新，不追加、不報
            report["unchanged"] += 1
            continue
        changed.setdefault(id(e), (e, []))[1].append((rec, digest, text))
        report["modified"].append(rec["uri"])

    # 2b. 內容真的變了：這筆看到的位置全改成同一份 → 追加指紋；只改了其中幾處 → 改了的拆成新條目（審閱裁定 27）
    taken = {e.get("id") for e in entries}
    for e, changes in changed.values():
        seen_here = sum(1 for loc in e["locations"] if uri_key(loc.get("uri", "")) in seen)
        groups = {}
        for rec, digest, text in changes:
            groups.setdefault(digest, []).append((rec, text))
        if len(groups) == 1 and len(changes) == seen_here:
            digest, ((_rec, text), *_rest) = next(iter(groups.items()))
            e["fingerprints"].append(fingerprint(digest, text))
            continue
        moved_keys = {rec["key"] for rec, _d, _t in changes}
        e["locations"] = [loc for loc in e["locations"] if uri_key(loc.get("uri", "")) not in moved_keys]
        if not e["locations"]:
            e["status"] = "superseded"  # 位置全被拆走：原內容已不在任何地方（審閱裁定 39）
        for digest, recs in groups.items():
            split = {"id": _new_id(taken), "origin": e.get("origin", "client_file"),
                     "locations": [rec["loc"] for rec, _t in recs], "fingerprints": [fingerprint(digest, recs[0][1])],
                     "read": "ok", "status": "active", "first_seen": now, "created_turn": None, "missing_since": None}
            taken.add(split["id"])
            entries.append(split)

    # 3. 未知位置：搬動、恢復、重複、新增
    unseen = set()
    for e in entries:
        if e.get("status") == "active":
            for loc in e["locations"]:
                k = uri_key(loc.get("uri", ""))
                if k not in seen and checked(k):
                    unseen.add(k)
    by_fp, dormant, sizes = {}, {}, set()
    for e in entries:
        status = e.get("status")
        if status not in LIVE + DORMANT:
            continue
        if e.get("fingerprints"):
            by_fp.setdefault(e["fingerprints"][-1].get("sha256"), []).append(e)
        for loc in e.get("locations") or []:
            sizes.add(loc.get("size"))
            if status in DORMANT:
                dormant.setdefault(uri_key(loc.get("uri", "")), (e, loc))

    def reactivate(e, rec):
        """休眠條目（missing／excluded）回來：位置換成這次看到的，回 (舊主位置, 原狀態)。"""
        old_status, old_uri = e.get("status"), (e["locations"][0]["uri"] if e["locations"] else rec["uri"])
        e["locations"] = [rec["loc"]]
        e["status"], e["missing_since"] = "active", None
        if old_status == "missing":
            report["moved"].append([old_uri, rec["uri"]])
        else:
            report["restored"].append(rec["uri"])

    for rec in unknown:
        digest = text = None
        if rec["read"] == "ok" and (not quick or rec["st"].st_size in sizes):
            digest = digest_of(rec)
            if digest:
                text = text_of(rec)
        target = None
        if digest:
            cands = by_fp.get(digest, [])
            target = next((e for e in cands if e.get("status") == "active"
                           and any(uri_key(l["uri"]) in unseen for l in e["locations"])), None)
            if target is not None:
                idx = next(i for i, l in enumerate(target["locations"]) if uri_key(l["uri"]) in unseen)
                old_uri = target["locations"][idx]["uri"]
                unseen.discard(uri_key(old_uri))
                target["locations"][idx] = rec["loc"]
                report["moved"].append([old_uri, rec["uri"]])
            else:
                target = next((e for s in ("missing", "excluded") for e in cands if e.get("status") == s), None)
                if target is not None:
                    reactivate(target, rec)
                else:
                    target = next((e for e in cands if e.get("status") in LIVE), None)
                    if target is not None:
                        target["locations"].append(rec["loc"])
                        report["duplicates"].append([rec["uri"], target["id"]])
        elif rec["key"] in dormant and _same_loc(dormant[rec["key"]], rec):
            target = dormant[rec["key"]][0]  # 沒指紋（憑證、占位、太大）的休眠條目：同位置、同大小時間再出現就恢復（審閱裁定 21）
            reactivate(target, rec)
        if target is not None:
            if target.get("status") == "active":
                _set_read(target, rec["read"], rec["error"])
            continue
        entry = {
            "id": _new_id(taken),
            "origin": rec["root"]["origin"],
            "locations": [rec["loc"]],
            "fingerprints": [fingerprint(digest, text)] if digest else [],
            "read": rec["read"],
            "status": "active",
            "first_seen": now,
            "created_turn": None,
            "missing_since": None,
        }
        if rec["read"] == "error":
            entry["error"] = rec["error"]
        taken.add(entry["id"])
        entries.append(entry)
        if digest:
            by_fp.setdefault(digest, []).append(entry)
        report["added"].append(rec["uri"])

    # 4. 這次確實沒看到的位置：拿掉；條目沒有位置剩下 → missing（保留最後的位置清單）
    #    時間到了（走訪或算指紋時）就不判不見：搬動可能還沒認出來（審閱裁定 25、31）
    report["incomplete"] = stopper.stopped or budget["out"]  # 讀取預算用完也算沒比完（審閱裁定 39）
    was_missing = {id(e) for e in entries if e.get("status") == "missing"}
    for e in entries:
        if e.get("status") != "active" or report["incomplete"]:
            continue
        lost = [loc for loc in e["locations"] if uri_key(loc.get("uri", "")) in unseen]
        if not lost:
            continue
        if len(lost) == len(e["locations"]):
            e["status"], e["missing_since"] = "missing", now
            report["missing"].append(e["locations"][0]["uri"])
        else:
            e["locations"] = [loc for loc in e["locations"] if uri_key(loc.get("uri", "")) not in unseen]
    for e in entries:
        if id(e) in was_missing and e.get("status") == "missing" and e["locations"]:
            if split_uri(uri_key(e["locations"][0]["uri"]))[0] in walked:
                report["still_missing"].append(e["locations"][0]["uri"])

    # 5. 數字
    report["total"] = len(found)
    for rec in found:
        alias = rec["root"]["alias"]
        stats = report["by_source"].setdefault(alias, {"files": 0, "bytes": 0})
        stats["files"] += 1
        stats["bytes"] += rec["st"].st_size
        if rec["read"] in READ_PROBLEMS:
            report["read_counts"][rec["read"]] += 1
            examples = report["read_examples"].setdefault(rec["read"], [])
            if len(examples) < EXAMPLES_MAX:
                examples.append(rec["uri"])
    return report


def public(report):
    """給 --json 印的：去掉 entries。"""
    return {k: v for k, v in report.items() if k != "entries"}


def has_changes(vault, report):
    return report["entries"] != load(vault)["entries"]


def apply(vault, report, turn_id):
    """持清冊的鎖：先備進回合（D-074，備份失敗丟 OSError 不寫），再寫 report 的 entries；
    這次新追加的指紋與新條目（turn／created_turn 是 None）填上 turn_id，舊的不動（審閱裁定 6）。
    scan 之後才登記的成品（id 不在 report 裡）照樣留著。
    寫完、出了清冊的鎖之後接待處理（ingest 製卡第 5 點）：照清冊狀態開／關 missing_file，這次 modified 的條目關掉 unreadable；
    這段任何例外只記 hook-errors.log、不往上丟（folders add 的交易不受影響）。回這次有沒有改到待處理。"""
    from kb._lib import turn
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        turn.backup(vault, INVENTORY_POSIX, turn_id)
        entries = copy.deepcopy(report["entries"])
        have = {e.get("id") for e in entries}
        entries += [e for e in load(vault)["entries"] if e.get("id") not in have]
        for e in entries:
            if e.get("created_turn") is None:
                e["created_turn"] = turn_id
            for fp in e.get("fingerprints") or []:
                if fp.get("turn") is None:
                    fp["turn"] = turn_id
        _write(vault, entries)
    return _sync_pending(vault, entries, report.get("modified") or [], turn_id)


def _sync_pending(vault, entries, modified, turn_id):
    from kb._lib import ledger, pending
    touched = False
    try:
        touched = pending.sync_missing(vault, entries, turn_id)
        keys = {uri_key(u) for u in modified}
        ids = [e["id"] for e in entries if e.get("status") == "active"
               and any(uri_key(loc.get("uri", "")) in keys for loc in e.get("locations") or [])]
        for fid in ids:
            touched = pending.auto_done(vault, fid, "unreadable", "檔案換了，重新讀", turn_id,
                                        statuses=("open", "dismissed")) or touched  # 跳過過的也重讀（裁定 31）
    except Exception as e:  # noqa: BLE001  待處理沒更新不能讓清冊的交易失敗；下次 scan 會補
        ledger.log_error(vault, f"待處理沒更新：{type(e).__name__}: {e}", source="inventory")
    return touched


def _exclude_kind(src, key):
    """key 落進這個來源的哪一條排除：回那條的 kind（hidden｜unused），沒落進回 None。"""
    for ex in src["excludes"]:
        if inside(key, ex["root"]):
            return ex.get("kind") or "hidden"
    return None


def reconcile(vault, sources, turn_id):
    """給 folders exclude／remove：active、missing、excluded 條目的每個位置對新登記表。
    - 落進 hidden 排除（審閱裁定 40）：位置直接拿掉、不留檔名；條目沒位置剩下就整筆刪。
    - 來源拿掉了、落進 unused 排除（裁定 21）：active／missing 條目位置全沒了 → excluded，保留最後的位置清單。
    保留別名的位置不動（審閱裁定 11）；spec 展不開的來源不算拿掉。有改才在同一把鎖裡備份、寫。
    回 {"excluded": 轉成 excluded 的條目數, "removed": 整筆刪掉的條目數}。"""
    from kb._lib import turn
    p = _path(vault)
    if not p.exists():
        return {"excluded": 0, "removed": 0}
    by_alias = {s["alias"].casefold(): s for s in sources.entries}
    unresolved = {u["alias"].casefold() for u in sources.unresolved if u.get("kind") == "source"}
    excluded, removed, changed = 0, 0, False
    with FileLock(p):
        data = load(vault)
        kept_entries = []
        for e in data["entries"]:
            status = e.get("status")
            if status not in ("active", "missing", "excluded"):
                kept_entries.append(e)
                continue
            keep, dormant, hidden = [], [], 0  # dormant：來源拿掉或 unused 排除的位置
            for loc in e.get("locations") or []:
                alias, rel = split_uri(loc.get("uri", ""))
                a = alias.casefold()
                src = by_alias.get(a)
                if a in RESERVED_CF or a in unresolved:
                    keep.append(loc)
                elif src is None:
                    dormant.append(loc)
                else:
                    kind = _exclude_kind(src, _fs_key(f"{src['root']}/{rel}"))
                    if kind == "hidden":
                        hidden += 1
                    elif kind is None:
                        keep.append(loc)
                    else:
                        dormant.append(loc)
            if status == "excluded":  # 已經休眠：只拿掉 hidden 的位置
                keep, dormant = keep + dormant, []
            if not hidden and not dormant:
                kept_entries.append(e)
                continue
            changed = True
            if keep:
                e["locations"] = keep
            elif dormant:
                e["locations"], e["status"], e["missing_since"] = dormant, "excluded", None
                excluded += 1
            else:
                removed += 1
                continue  # 整筆都在 hidden 排除裡：不留痕
            kept_entries.append(e)
        if changed:
            turn.backup(vault, INVENTORY_POSIX, turn_id)
            _write(vault, kept_entries)
    return {"excluded": excluded, "removed": removed}


def uri_of(vault, sources, path):
    """磁碟路徑 → 清冊 uri（file://<別名>/<相對路徑>）。總資料夾裡的先認三區（收件匣、AI 產出、知識庫），
    再找包住它的來源（最長的根）；都不是回 None。相對路徑可能是 normalize 過的小寫：比對一律用 uri_key。"""
    target = normalize(str(path), None)
    vault_n = normalize(str(vault), None)
    if inside(target, vault_n):
        rel = target[len(vault_n):].lstrip("/")
        first, _, rest = rel.partition("/")
        for alias in (INBOX, OUTPUT):
            if first == alias.casefold() or first == alias:
                return make_uri(alias, rest) if rest else None
        if first in VAULT_RESERVED_DIRS or first.startswith(".") or not rest:
            return None
        return make_uri(KNOWLEDGE, rel)
    best = None
    for src in sources.entries:
        if inside(target, src["root"]) and target != src["root"] and (best is None or len(src["root"]) > len(best["root"])):
            best = src
    if best is None:
        return None
    return make_uri(best["alias"], target[len(best["root"]):].lstrip("/"))


def entry_for(vault, uri, entries=None):
    """uri 反查清冊條目（kb convert 用）：位置鍵相同的，active 優先，其次 missing／excluded，再其次任何一筆；沒有回 None。"""
    key = uri_key(uri)
    entries = load(vault)["entries"] if entries is None else entries
    hits = [e for e in entries if any(uri_key(loc.get("uri", "")) == key for loc in e.get("locations") or [])]
    for statuses in (("active",), DORMANT):
        for e in hits:
            if e.get("status") in statuses:
                return e
    return hits[-1] if hits else None


def path_of(vault, sources, uri):
    """uri 反查絕對路徑；別名對不到回 None。"""
    alias, rel = split_uri(uri)
    parts = [p for p in rel.split("/") if p]
    a = alias.casefold()
    if a == KNOWLEDGE.casefold():
        return Path(vault).joinpath(*parts)
    if a in (INBOX.casefold(), OUTPUT.casefold()):
        return Path(vault).joinpath(INBOX if a == INBOX.casefold() else OUTPUT, *parts)
    for src in sources.entries:
        if src["alias"].casefold() == a:
            return Path(src["root"]).joinpath(*parts)
    return None
