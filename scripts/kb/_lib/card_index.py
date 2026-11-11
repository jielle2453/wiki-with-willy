"""卡片索引（#57，D-345）：檔案卡的檔頭與內文存進一個 SQLite 快取，讀全部的卡時不必每張都開檔解析。

索引是快取，放知識庫外（WIKI_WITH_WILLY_INDEX_DIR，沒有就 %LOCALAPPDATA%\\wiki-with-willy\\index），不進 git、每台自己建；只存卡上有的東西，
刪掉什麼都不會少。每次要讀全部的卡先走一遍 檔案索引/ 比每張的修改時間與大小（changed），只重讀有變的、拿掉不見的，一個交易寫回。
沒有、壞了、鎖住、讀不了都照舊逐卡讀（cards._scan_groups_files），不報錯；groups 回 None 就是「索引這次不能用」。
sqlite3 只在這支用。
"""
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

CARDS_DIR = "檔案索引"
SCHEMA_VERSION = "1"
BOM = chr(0xFEFF)
LOCK_TIMEOUT = 3  # 另一個程式在寫時最多等幾秒，等不到這次逐卡讀、不寫索引
RECENT_NS = 2 * 10 ** 9  # 剛改不到 2 秒就被記進索引的，下次一定重讀（NTFS 計時器粒度、FAT/exFAT 2 秒精度）
_logged = False  # 同一個指令（行程）只記一行 hook-errors.log
_fresh = {}  # {知識庫: True}：這個行程裡 groups 剛用索引算過（body_hits 只在這之後用）

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS cards (
  path     TEXT PRIMARY KEY,
  mtime_ns INTEGER NOT NULL,
  size     INTEGER NOT NULL,
  seen_ns  INTEGER NOT NULL,
  front    TEXT,
  error    TEXT,
  title    TEXT NOT NULL,
  body     TEXT NOT NULL
);
"""


class Unusable(Exception):
    """索引這次不能用；args[0] 是一句原因（kb index rebuild 印出來）。quiet：正常的情況（沒有知識庫編號、另一個程式正在寫），不記 log。"""

    def __init__(self, reason, quiet=False):
        super().__init__(reason)
        self.quiet = quiet


class _Unread(Exception):
    """這張卡這次讀不到（OSError）：不寫進索引。"""


class _Corrupt(Exception):
    """索引檔壞了或版本不對：刪掉重建。"""


# ---------- 位置 ----------

def index_dir():
    env = os.environ.get("WIKI_WITH_WILLY_INDEX_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")  # 同 office.tools_dir
    return Path(base) / "wiki-with-willy" / "index"


def db_path(vault):
    """<知識庫編號>-<路徑 sha1 前 8 碼>.sqlite；沒有知識庫編號回 None（不建索引）。每次現算，不記進任何狀態檔。"""
    from kb._lib import vault as vault_lib
    try:
        vid = vault_lib.vault_id(vault)
    except (ValueError, OSError):
        vid = None
    if not vid:
        return None
    real = os.path.realpath(str(vault))
    if sys.platform == "win32":
        real = real.casefold()
    return index_dir() / f"{vid}-{hashlib.sha1(real.encode('utf-8')).hexdigest()[:8]}.sqlite"


# ---------- 哪幾張變了 ----------

def changed(vault, rows, deadline=None):
    """回 (新增或變了的路徑, 不見的路徑, 這次看到的全部 {路徑: (mtime_ns, size)})。rows 是索引現有的 {路徑: (mtime_ns, size, seen_ns)}。
    走 檔案索引/（os.scandir 遞迴；跳過 . 開頭的夾與檔、只收 .md，同 cards._walk_cards）。每走一個夾看一次 deadline，過了丟 TimeoutError。
    「哪幾張變了」只在這個函式；以後要改問 git 只換它。"""
    seen = {}
    stack = [(Path(vault) / CARDS_DIR, CARDS_DIR)]
    while stack:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("讀卡讀太久")
        folder, rel = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                if not e.is_symlink():
                    stack.append((e.path, f"{rel}/{e.name}"))
            elif e.name.lower().endswith(".md"):
                try:
                    st = e.stat()
                    seen[f"{rel}/{e.name}"] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    seen[f"{rel}/{e.name}"] = (-1, -1)  # 讀不到屬性：每次都重讀（跟逐卡讀一樣記成讀不了）
    new = []
    for path, (mtime, size) in seen.items():
        old = rows.get(path)
        if old is None or mtime < 0 or (old[0], old[1]) != (mtime, size) or old[0] >= old[2] - RECENT_NS:
            new.append(path)
    gone = [p for p in rows if p not in seen]
    return new, gone, seen


# ---------- 開索引 ----------

def _connect(vault):
    path = db_path(vault)
    if path is None:
        raise Unusable("這個知識庫沒有編號（.system/vault.json）", quiet=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Unusable(f"索引夾建不了：{type(e).__name__}: {e}") from e
    con = sqlite3.connect(str(path), timeout=LOCK_TIMEOUT, isolation_level=None)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not names:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        elif not {"meta", "cards"} <= names:
            raise _Corrupt("缺表")
        ver = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        if not ver or ver[0] != SCHEMA_VERSION:
            raise _Corrupt("版本不對")
    except BaseException:
        con.close()
        raise
    return con, path


def _drop(path):
    """刪索引檔（含 -wal、-shm）；刪不掉（別的程式開著）回 False。"""
    ok = True
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(path) + suffix)
        except FileNotFoundError:
            pass
        except OSError:
            ok = False
    return ok


def _log(vault, text):
    global _logged
    if _logged:
        return
    _logged = True
    from kb._lib import ledger
    ledger.log_error(vault, text, source="index")


def _locked(e):
    return isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower()


# ---------- 讀全部 ----------

def _read_card(vault, rel):
    """回 (front 的 JSON, error, 正規化標題, 正規化內文)；讀法同 cards._scan_groups_files 與 find._body。"""
    import re
    from kb._lib import cards, entities
    try:
        text = (Path(vault) / rel).read_text(encoding="utf-8")
        front = cards.parse_front(text)
    except OSError as e:  # 被鎖、拒讀、OneDrive 還沒下載：檔案本身可能沒問題，不存進索引，這次進 broken、下次再讀
        raise _Unread(f"檔頭解析不了：{e}") from e
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"檔頭解析不了：{e}", "", ""
    title = front.get("標題")
    text = text.lstrip(BOM)
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    body = entities.key(text[m.end():] if m else text)
    return json.dumps(front, ensure_ascii=False), None, entities.key(title) if isinstance(title, str) else "", body


def _groups(vault, deadline, retry=True):
    """用索引算 (groups, broken)；不能用丟 Unusable。壞了刪掉重來一次。"""
    try:
        con, path = _connect(vault)
    except _Corrupt:
        return _redo(vault, deadline, retry)
    except sqlite3.DatabaseError as e:
        if _locked(e):
            raise Unusable("索引被另一個程式鎖住", quiet=True) from e
        if isinstance(e, sqlite3.OperationalError):
            raise Unusable(f"索引開不了：{e}") from e
        return _redo(vault, deadline, retry)
    except sqlite3.Error as e:
        raise Unusable(f"索引開不了：{e}") from e
    try:
        return _run(vault, con, deadline)
    except TimeoutError:
        raise
    except sqlite3.DatabaseError as e:
        if _locked(e):
            raise Unusable("索引被另一個程式鎖住", quiet=True) from e
        if isinstance(e, sqlite3.OperationalError):
            raise Unusable(f"索引讀寫失敗：{e}") from e
        con.close()
        con = None
        return _redo(vault, deadline, retry)
    except (sqlite3.Error, OSError, UnicodeError) as e:
        raise Unusable(f"索引讀寫失敗：{type(e).__name__}: {e}") from e
    finally:
        if con is not None:
            con.close()


def _redo(vault, deadline, retry):
    path = db_path(vault)
    if path is not None:
        _drop(path)
    if not retry:
        raise Unusable("索引壞了，刪掉重建也不行")
    return _groups(vault, deadline, retry=False)


def _run(vault, con, deadline):
    from kb._lib import cards
    have = {}
    for path, mtime, size, seen_ns, front, error in con.execute("SELECT path, mtime_ns, size, seen_ns, front, error FROM cards"):
        have[path] = (mtime, size, seen_ns, front, error)
    new, gone, seen = changed(vault, {p: v[:3] for p, v in have.items()}, deadline)
    fresh, late, unread = [], None, {}
    for rel in sorted(new):
        if deadline is not None and time.monotonic() > deadline:
            late = TimeoutError("讀卡讀太久")
            break
        stamp = time.time_ns()
        try:
            front, error, title, body = _read_card(vault, rel)
        except _Unread as e:
            unread[rel] = str(e)
            continue
        fresh.append((rel, seen[rel][0], seen[rel][1], stamp, front, error, title, body))
    gone = gone + [p for p in unread if p in have]  # 舊的那列不能留：修改時間沒變下次會直接用它
    if fresh or gone:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.executemany("INSERT OR REPLACE INTO cards (path, mtime_ns, size, seen_ns, front, error, title, body) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", fresh)
            con.executemany("DELETE FROM cards WHERE path = ?", [(p,) for p in gone])
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    if late is not None:
        raise late
    for row in fresh:
        have[row[0]] = (row[1], row[2], row[3], row[4], row[5])
    groups, broken = {}, []
    for rel in sorted(seen):
        if rel in unread:
            broken.append({"path": rel, "reason": unread[rel]})
            continue
        _m, _s, _n, front, error = have[rel]
        if error is not None or front is None:
            broken.append({"path": rel, "reason": error})
            continue
        front = json.loads(front)
        cid = front.get("編號")
        if not isinstance(cid, str) or not cid:
            broken.append({"path": rel, "reason": "檔頭「編號」的格式不對" if "編號" in front else "檔頭少了「編號」"})
        else:
            groups.setdefault(cid, []).append({"path": rel, "front": front})
    return groups, broken


def groups(vault, deadline=None):
    """回 (groups, broken)，格式、排序、理由句跟 cards._scan_groups_files 一模一樣；索引這次不能用回 None（呼叫的人改逐卡讀）。
    deadline 過了丟 TimeoutError（已重讀的先寫回）。"""
    key = str(vault)
    try:
        got = _groups(vault, deadline)
    except Unusable as e:
        _fresh[key] = False
        if not e.quiet:
            _log(vault, f"索引不能用，這次逐卡讀：{e}")
        return None
    _fresh[key] = True
    return got


def rebuild(vault):
    """刪掉索引檔（含 -wal、-shm）重建，回 (卡數, 秒數)；不能用丟 Unusable。"""
    path = db_path(vault)
    if path is None:
        raise Unusable("這個知識庫沒有編號（.system/vault.json），不建索引")
    start = time.monotonic()
    if not _drop(path):
        raise Unusable("索引正在被別的程式使用，等一下再重建")
    found, broken = _groups(vault, None)
    return sum(len(g) for g in found.values()) + len(broken), time.monotonic() - start


# ---------- 找字 ----------

def body_hits(vault, keys):
    """{key: set(路徑)}：正規化後的內文含 key 的卡。key 已經是 entities.key 過的。只在同一個指令剛用索引算過 groups 之後用
    （索引是新的）；否則、或索引不能用回 None（呼叫的人照舊讀檔）。"""
    if not _fresh.get(str(vault)):
        return None
    path = db_path(vault)
    if path is None or not path.exists():
        return None
    try:
        con = sqlite3.connect(str(path), timeout=LOCK_TIMEOUT, isolation_level=None)
        try:
            return {k: {r[0] for r in con.execute("SELECT path FROM cards WHERE instr(body, ?) > 0", (k,))} for k in keys}
        finally:
            con.close()
    except (sqlite3.Error, OSError, UnicodeError):
        return None


# ---------- 一次指令只走一遍 ----------

@contextlib.contextmanager
def once(vault):
    """在裡面，cards._scan_groups 第一次照常算、之後回同一份結果，不再走目錄。只給只讀的指令用（中間什麼都不會變）；離開時還原。"""
    from kb._lib import cards
    real = cards._scan_groups
    memo = {}

    def cached(v, deadline=None):
        k = str(v)
        if k not in memo:
            memo[k] = real(v, deadline)
        found, broken = memo[k]
        return {c: list(rows) for c, rows in found.items()}, list(broken)

    cards._scan_groups = cached
    try:
        yield
    finally:
        cards._scan_groups = real
