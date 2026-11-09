"""備份與快照（備份還原與換電腦 要做的事 1，D-203、D-204、D-211）：整個知識庫寫成一個 zip，AI 在對話裡直接跑。

內容：知識庫整個，排除 .system/temp/、.system/snapshots/、鎖檔（*.lock）；空夾也記（型別夾）；zip 根目錄多一個
wiki-with-willy-backup.json（schema 1：kind、reason、created、plugin_version、machine、vault_name、vault_id、files、bytes）。
先寫成同目錄的 .tmp、寫完驗一次（testzip、zip slip、meta）再改名；失敗不留 .tmp。長路徑照 paths.long_path。
放哪：env.json 這台電腦的 backup_dir；沒有回 need_to，AI 問客戶，帶 --to 再跑、程式記住。網路位置（\\ 或 // 開頭）不收：
防 AI 被藏在檔案裡的指令騙去把整個知識庫寫到別台（D-204 補）；雲端同步程式的夾（OneDrive、Dropbox）與對應好的磁碟機是本機路徑，照收。
留 backup_keep 份：只算這個夾裡檔名合格、根目錄有 meta、vault_id 相同的 zip；客戶自己放的別的檔不碰。
背景整理（card-maker）只擋還有動靜的批次，判斷用 batches 同一套（live、_idle）：視窗當掉留下的舊批次不擋。
鎖是 .system/revert.lock（跟退回共用），搶不到不等。快照（snapshot）跟備份寫同一種 zip，放 .system/snapshots/，留 snapshots_keep 份。
備份本身不改知識庫，不進回合備份；寫 zip 的程式只讀知識庫。
"""
import contextlib
import errno
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from kb._lib import config, env
from kb._lib import io as kio
from kb._lib import paths
from kb._lib import sources as sources_lib
from kb._lib import vault as vault_lib

META_NAME = "wiki-with-willy-backup.json"
SNAPSHOT_REL = ".system/snapshots"
LOCK_TRY = 0.2  # 跟 versions.REVERT_LOCK_TRY 同一個，搶不到不排隊
LOCK_REL = ".system/revert"  # 同 versions.revert_lock_path（鎖檔是 .system/revert.lock）
EXCLUDED_DIRS = (".system/temp", ".system/snapshots", ".system/local/cache")  # 快取可重算（多人共用 1）
BACKUP_NAME = re.compile(r"^.+備份 (\d{4}-\d{2}-\d{2})(?: \((\d+)\))?\.zip$")
SNAPSHOT_NAME = re.compile(r"^快照 (\d{4}-\d{2}-\d{2}) (\d{4})（(還原前|升級前)）(?: \((\d+)\))?\.zip$")
REASON_TEXT = {"restore": "還原前", "upgrade": "升級前"}
KINDS = ("backup", "snapshot")

NEED_TO = "還沒說備份要放哪"
NETWORK_PATH = "備份要放在這台電腦的資料夾或隨身碟；網路上的位置請先對應成磁碟機"
IN_VAULT = "備份不能放在知識庫裡面"
IN_SOURCE = "備份不能放在登記的資料夾「{alias}」裡，不然會被當成新檔整理"
NOT_WRITABLE = "那個位置寫不進去：{path}"
NO_SPACE = "空間不夠：要 {need} MB，那裡只剩 {free} MB"
BATCH_RUNNING = "背景整理還在跑，先停掉再{what}"
BUSY = "另一個備份、還原或退回正在跑，等它做完"
NOT_A_VAULT = "這裡不是知識庫"
NOT_OURS = "這份不是 wiki-with-willy 的備份"
BROKEN = "這份備份壞了（{why}），換一份"
NEWER = "這份備份是比較新的版本（{version}）做的，先升級再還原"
FILE_OPEN = "有檔案開著（{name}），關掉再說一次；什麼都沒動"
NOT_FOUND = "找不到這份：{}"


class Refused(Exception):
    """拒絕：訊息就是要給人看的那句（exit 3、一字不差）。"""


def plugin_version():
    return str((paths.read_manifest() or {}).get("version", "0"))


def _version_tuple(text):
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:3]) or (0,)


def lock_path(vault):
    return Path(vault) / LOCK_REL


@contextlib.contextmanager
def acquire(vault):
    """with acquire(vault): 搶備份、還原、清理、退回共用的鎖（搶不到不等，丟 Refused(BUSY)）。"""
    lock = kio.FileLock(lock_path(vault), timeout=LOCK_TRY)
    try:
        lock.__enter__()
    except TimeoutError:
        raise Refused(BUSY) from None
    try:
        yield lock
    finally:
        lock.__exit__(None, None, None)


def batch_running(vault):
    """還有動靜的整理批次（open、沒過期、last_seen 沒超過閒置分鐘數）：判斷照 batches 同一套，讀不到當沒有。"""
    from kb._lib import batches
    try:
        data = batches.load(vault)
    except batches.Unreadable:
        return False
    now = batches._now()
    agents = batches._agents(data)
    minutes = config.get(vault, "card_batch_expire_minutes")
    return any(batches.live(b, now, minutes, agents) and not batches._idle(b, now) for b in data["batches"])


# ---------- 走訪與寫 zip ----------

def _is_link(path):
    try:
        if os.path.islink(path):
            return True
        return bool(getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0) & 0x400)  # reparse point
    except OSError:
        return False


def _excluded(rel):
    return any(rel == d or rel.startswith(d + "/") for d in EXCLUDED_DIRS) or rel.endswith(".lock")


def collect(vault):
    """要放進 zip 的：(檔案 [(絕對路徑, zip 內路徑)], 空夾 [zip 內路徑/], 未壓縮總位元組)。連結與排除項目略過。"""
    root = Path(vault)
    files, empty, total = [], [], 0
    for dirpath, dirs, names in os.walk(str(root)):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirs[:] = [d for d in dirs if not _is_link(here / d)
                   and not _excluded((rel_dir + "/" + d).lstrip("/"))]
        kept = 0
        for name in names:
            rel = (rel_dir + "/" + name).lstrip("/")
            if _excluded(rel) or _is_link(here / name):
                continue
            try:
                size = os.stat(paths.long_path(here / name)).st_size
            except OSError:
                continue  # 走訪到一半不見了：不算
            files.append((here / name, rel))
            total += size
            kept += 1
        if rel_dir and not dirs and not kept:
            empty.append(rel_dir + "/")
    return files, empty, total


def write_zip(vault, dest_path, meta):
    """把知識庫寫成 dest_path 這個 zip（呼叫端決定要不要先寫 .tmp）；meta 補上 files、bytes 寫在根目錄。回 {"files","bytes"}。
    meta 最後才寫、用真的寫進去的份數（走訪到寫入之間不見的檔略過，不算）；修改時間早於 1980 的檔也照寫（strict_timestamps=False，T-5）。
    檔案被別的程式開著讀不了丟 Refused（FILE_OPEN），已寫的半成品由呼叫端清掉。"""
    files, empty, _total = collect(vault)
    written, size = 0, 0
    with zipfile.ZipFile(str(dest_path), "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for rel in empty:
            z.writestr(rel, "")
        for src, rel in files:
            try:
                z.write(paths.long_path(src), rel)
            except FileNotFoundError:
                continue  # 走訪之後被刪掉了：不算
            except PermissionError:
                raise Refused(FILE_OPEN.format(name=Path(rel).name)) from None
            written += 1
            size += z.getinfo(rel).file_size
        z.writestr(META_NAME, json.dumps(dict(meta, files=written, bytes=size), ensure_ascii=False, indent=2) + "\n")
    return {"files": written, "bytes": size}


def read_meta(zip_path):
    """zip 根目錄的 meta（dict）；不是 zip、沒有 meta、讀不懂回 None。不驗內容。"""
    try:
        with zipfile.ZipFile(str(zip_path)) as z:
            data = json.loads(z.read(META_NAME).decode("utf-8"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None
    return data if isinstance(data, dict) and data.get("kind") in KINDS else None


def _unsafe_member(name):
    n = name.replace("\\", "/")
    return (n.startswith("/") or bool(re.match(r"^[A-Za-z]:", n)) or ".." in n.split("/")
            or n.startswith("//"))


def verify(zip_path, full=True):
    """驗一份 zip：壞了（BadZipFile、testzip）、不安全的路徑（zip slip）、不是我們的備份（沒有 meta）、版本比現在新。
    過了回 meta；不過丟 Refused（句子照 spec）。full 是 False 只讀 meta 與中央目錄、不跑 testzip（列清單用，T-12；完整驗證留給 check、apply）。"""
    try:
        with zipfile.ZipFile(str(zip_path)) as z:
            bad = z.testzip() if full else None
            names = z.namelist()
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError) as e:
        raise Refused(BROKEN.format(why=_why_zip(e))) from None
    if bad:
        raise Refused(BROKEN.format(why=f"{Path(bad).name} 讀不出來"))
    evil = next((n for n in names if _unsafe_member(n)), None)
    if evil:
        raise Refused(BROKEN.format(why="裡面有不安全的路徑"))
    meta = read_meta(zip_path)
    if meta is None:
        raise Refused(NOT_OURS)
    if _version_tuple(meta.get("plugin_version")) > _version_tuple(plugin_version()):
        raise Refused(NEWER.format(version=meta.get("plugin_version")))
    return meta


def _why_zip(e):
    return "不是完整的 zip 檔" if isinstance(e, zipfile.BadZipFile) else "檔案讀不出來"


def oserror_sentence(e, need_mb=None, free_mb=None, source=None, source_path=None):
    """OSError 依 errno 分三種白話（T-6）：檔案開著（帶檔名）、空間不夠、找不到那份；其他回 None 讓呼叫端說「沒有做成」。
    FileNotFoundError 只有找不到的正是來源（source_path）才說「找不到這份」，解壓縮或搬動時別的檔不見了照一般那句（N-4）。"""
    code = getattr(e, "errno", None)
    if code == errno.ENOSPC:
        if need_mb is not None and free_mb is not None:
            return NO_SPACE.format(need=need_mb, free=free_mb)
        return "空間不夠，騰出空間再說一次；什麼都沒動"
    if isinstance(e, FileNotFoundError) and source is not None and source_path is not None:
        missing = getattr(e, "filename", None)
        if missing and _norm(missing) == _norm(source_path):
            return NOT_FOUND.format(source)
        return None
    if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32, 33):
        name = Path(str(getattr(e, "filename", None) or getattr(e, "filename2", None) or "")).name or "某個檔案"
        return FILE_OPEN.format(name=name)
    return None


# ---------- 放哪、拒絕 ----------

def _norm(p):
    return paths.normalize(str(p), None)


def _nearest(path):
    p = Path(path)
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    return p


def _can_write(path):
    """目的地寫得進去嗎：最近的現有上層夾試建一個暫存檔。"""
    anc = _nearest(path)
    if anc is None or not anc.is_dir():
        return False
    try:
        fd, name = tempfile.mkstemp(prefix=".wwb-", dir=str(anc))
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def _free_bytes(path):
    anc = _nearest(path)
    try:
        return shutil.disk_usage(str(anc)).free if anc is not None else 0
    except OSError:
        return 0


def _sync_folder(to):
    roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
    n = _norm(to)
    return any(r and paths.inside(n, _norm(r)) for r in roots)


def _mb_up(n):
    return max(1, math.ceil(n / (1024 * 1024)))


_LOCAL_PREFIX = re.compile(r"^//[?.]/[A-Za-z]:(/|$)")


def is_network_path(to):
    """網路位置（UNC，含 @SSL 那種經網路傳出去的寫法），備份不收（D-204 補）。Windows 把 \\ 與 / 混寫也當 UNC（\\/host/share、/\\host\\share），
    所以先把 \\ 換成 / 再看開頭是不是 //（N-1）；\\?\\C:\\、\\.\\C:\\ 這種本機長路徑前綴放行，\\?\\UNC\\ 照擋。"""
    s = str(to).strip().strip("\"'").replace("\\", "/")
    if not s.startswith("//"):
        return False
    return not _LOCAL_PREFIX.match(s)


def _refusal(vault, to, total):
    """放哪有問題回那一句，沒問題回 None（不含鎖：鎖在動手時才搶）。"""
    if is_network_path(to):
        return NETWORK_PATH
    n = _norm(to)
    if paths.inside(n, _norm(vault)):
        return IN_VAULT
    for src in sources_lib.load(vault).entries:
        if paths.inside(n, src["root"]):
            return IN_SOURCE.format(alias=src["alias"])
    if batch_running(vault):
        return BATCH_RUNNING.format(what="備份")
    shown = Path(to).resolve().as_posix()
    if not _can_write(to):
        return NOT_WRITABLE.format(path=shown)
    free = _free_bytes(to)
    if total > free:
        return NO_SPACE.format(need=_mb_up(total), free=free // (1024 * 1024))
    return None


def check(vault, to=None):
    """--check：{"to","need_to","files","bytes","sync_folder","refuse"}；不寫知識庫、不建目的地夾、不記 backup_dir。"""
    target = to or env.backup_dir(vault)
    files, _empty, total = collect(vault)
    got = {"to": None, "need_to": target is None, "files": len(files), "bytes": total,
           "sync_folder": False, "refuse": None}
    if target is None:
        return got
    got["to"] = str(target).strip() if is_network_path(target) else Path(target).resolve().as_posix()
    got["sync_folder"] = False if is_network_path(target) else _sync_folder(target)
    got["refuse"] = _refusal(vault, target, total)
    return got


# ---------- 命名與留份數 ----------

def backup_name(vault, taken, day=None):
    day = day or datetime.now().strftime("%Y-%m-%d")
    base = f"{Path(vault).name}備份 {day}"
    name, n = base + ".zip", 1
    while name in taken:
        n += 1
        name = f"{base} ({n}).zip"
    return name


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _backup_key(path):
    """新舊排序：名字裡的日期，同一天看檔案修改時間（名字的 (2) 會因為舊的被刪而重用，不能當順序）。"""
    m = BACKUP_NAME.match(path.name)
    return (m.group(1), _mtime(path)) if m else None


def _snapshot_key(path):
    m = SNAPSHOT_NAME.match(path.name)
    return (m.group(1), m.group(2), _mtime(path)) if m else None


def keep(directory, vault_id, n):
    """留最近 n 份：只算 directory 裡檔名合格、meta 在、kind 是 backup 而且 vault_id 相同的 zip；更舊的刪。回刪掉的檔名。"""
    mine = []
    for p in Path(directory).glob("*.zip"):
        key = _backup_key(p)
        meta = read_meta(p) if key else None
        if key and meta and meta.get("kind") == "backup" and meta.get("vault_id") == vault_id:
            mine.append((key, p))
    mine.sort(key=lambda kp: kp[0], reverse=True)
    removed = []
    for _key, p in mine[n:]:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def _meta_for(vault, kind, reason):
    return {"schema_version": 1, "kind": kind, "reason": reason, "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "plugin_version": plugin_version(), "machine": env.machine_name(), "vault_name": Path(vault).name,
            "vault_id": vault_lib.ensure_vault_id(vault)}


def _write_verified(vault, dest, meta):
    """先寫同目錄的 .tmp、驗過再改名；任何一步失敗拿掉 .tmp。"""
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        write_zip(vault, tmp, meta)
        verify(tmp)
        for i in range(kio.REPLACE_RETRIES):
            try:
                os.replace(tmp, dest)
                break
            except PermissionError:
                if i == kio.REPLACE_RETRIES - 1:
                    raise
                import time
                time.sleep(kio.REPLACE_WAIT)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def backup(vault, to=None):
    """做一份備份。回 {"path","files","bytes","removed","clean"}；clean 是清理的結果（做不了回 None，不影響備份）。
    放哪記進 env.json（成功之後）。拒絕丟 Refused。"""
    target = to or env.backup_dir(vault)
    if target is None:
        raise Refused(NEED_TO)
    files, _empty, total = collect(vault)
    refuse = _refusal(vault, target, total)
    if refuse:
        raise Refused(refuse)
    try:  # N-6：寫 zip 之前先記位置、補 vault.json，第一份備份裡才有位置紀錄；寫不了不擋備份
        from kb._lib import moved
        moved.note_place(vault)
    except (OSError, ValueError):
        pass
    dest_dir = Path(target)
    meta = _meta_for(vault, "backup", None)
    with acquire(vault):
        dest_dir.mkdir(parents=True, exist_ok=True)
        taken = {p.name for p in dest_dir.iterdir()}
        dest = dest_dir / backup_name(vault, taken)
        _write_verified(vault, dest, meta)
        done = read_meta(dest) or {}
        env.set_backup_dir(vault, dest_dir.resolve().as_posix())
        removed = keep(dest_dir, meta["vault_id"], config.get(vault, "backup_keep"))
    cleaned = None
    try:
        from kb._lib import clean
        cleaned = clean.run(vault)
    except Exception:  # noqa: BLE001  清理做不了不影響備份（Refused 也一樣：鎖被占著，下次再清）
        cleaned = None
    try:
        size = dest.stat().st_size
    except OSError:
        size = total
    return {"path": dest.resolve().as_posix(), "files": done.get("files", len(files)), "bytes": size,
            "removed": removed, "clean": cleaned}


# ---------- 快照 ----------

def snapshot(vault, reason, rotate=True, protect=()):
    """拍快照（還原前、升級前）：跟備份同一種 zip，放 .system/snapshots/，留 snapshots_keep 份。回快照路徑。
    呼叫端（restore、升級）已拿著鎖；這裡不搶。rotate 是 False 先不輪替（還原要等成功了才輪替，失敗才不會擠掉較舊有用的那份，T-10）；
    protect 是輪替時不刪的快照（來源是快照時，T-6）。"""
    if reason not in REASON_TEXT:
        raise ValueError(f"快照的原因只收 restore、upgrade：{reason}")
    folder = Path(vault) / SNAPSHOT_REL
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    base = f"快照 {now:%Y-%m-%d %H%M}（{REASON_TEXT[reason]}）"
    taken = {p.name for p in folder.iterdir()}
    name, n = base + ".zip", 1
    while name in taken:
        n += 1
        name = f"{base} ({n}).zip"
    dest = folder / name
    _write_verified(vault, dest, _meta_for(vault, "snapshot", reason))
    if rotate:
        rotate_snapshots(vault, protect)
    return dest


def rotate_snapshots(vault, protect=()):
    """快照超過 snapshots_keep 份就刪最舊的；protect 裡的不刪（可能因此多留一份）。"""
    keep_names = {Path(p).name for p in protect}
    for p in snapshots(vault)[config.get(vault, "snapshots_keep"):]:
        if p.name in keep_names:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def snapshots(vault):
    """快照路徑，新的在前。"""
    folder = Path(vault) / SNAPSHOT_REL
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob("*.zip") if _snapshot_key(p)), key=_snapshot_key, reverse=True)


# ---------- 列出 ----------

def _describe(path, kind_hint):
    path = Path(path)
    meta = read_meta(path) or {}
    try:
        verify(path, full=False)
        usable, why = True, None
    except Refused as e:
        usable, why = False, str(e)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"name": path.name, "path": path.resolve().as_posix(), "kind": meta.get("kind") or kind_hint,
            "created": meta.get("created"), "machine": meta.get("machine"), "plugin_version": meta.get("plugin_version"),
            "bytes": size, "usable": usable, "why": why}


def list_backups(vault, extra_dir=None):
    """這台電腦記的備份夾與 extra_dir 裡檔名合格的 zip，加上 .system/snapshots/ 的快照；新的在前。
    vault 是 None（空資料夾還原）時只看 extra_dir。每份帶 usable 與 why。"""
    dirs, seen = [], set()
    for d in ((env.backup_dir(vault) if vault else None), extra_dir):
        if d and _norm(d) not in seen and Path(d).is_dir():
            seen.add(_norm(d))
            dirs.append(Path(d))
    out = [_describe(p, "backup") for d in dirs for p in sorted(d.glob("*.zip")) if BACKUP_NAME.match(p.name)]
    if vault:
        out += [_describe(p, "snapshot") for p in snapshots(vault)]
    out.sort(key=lambda g: (g["created"] or "", g["name"]), reverse=True)
    return out
