"""檔案讀寫：一律 UTF-8、LF；寫入先寫 .tmp 再 os.replace，寫一半不會留下壞檔。
os.replace 被暫時拒絕（防毒或索引正在讀，I-018）重試最多 20 次、每次 0.1 秒。
FileLock：建鎖檔實作的簡單檔案鎖，給同時寫同一份狀態檔的程序互斥（I-080）。
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

REPLACE_RETRIES = 20
REPLACE_WAIT = 0.1
LOCK_TIMEOUT = 3  # hook timeout 10 秒，鎖等不到就要讓 before_write 回 backup_failed（第一輪 M1）
STALE_LOCK_SECONDS = 30


def read_text(path, encoding="utf-8"):
    with open(path, encoding=encoding, newline="") as f:
        return f.read()


def read_json(path, default=None):
    """讀 JSON；沒有檔回 default。.system/*.json 讀不懂（ValueError）時先試 recover_json（放寬丙 6-3，D-311(2)）：
    換回一份讀得懂的就用它，換不回來才照舊丟 ValueError。"""
    from kb._lib import streams
    v = streams.virtual_kind(path)
    if v is not None:
        doc = streams.read_doc(*v)
        if doc is not None:
            return doc
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        got = _recover_for(path)
        if got is None:
            raise
        return got


BROKEN_REL = ".system/broken"
_RECOVERING = set()  # 正在還原的檔：還原過程中又讀到同一個檔不再還原（避免繞圈）


def _recover_for(path):
    p = Path(path)
    if p.suffix != ".json" or p.parent.name != ".system":
        return None
    vault = p.parent.parent
    try:
        if not recover_json(vault, f".system/{p.name}")["ok"]:
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _readable(raw):
    """備份裡的內容讀得懂（UTF-8、JSON、物件）才算。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        return False
    return isinstance(data, dict)


def _candidates(vault, rel):
    """由新到舊找這個檔的備份：[(來源說明, 位元組)]（用到才讀）：回合備份 versions/*/files/<rel> → 快照 → 最近的 kb backup zip。"""
    import zipfile
    from kb._lib import backup, turn
    root = Path(vault) / turn.VERSIONS_REL
    if root.is_dir():
        for d in sorted((x for x in root.iterdir() if x.is_dir()), key=lambda x: x.name, reverse=True):
            f = d / "files" / rel
            if f.is_file():
                try:
                    yield "回合備份", f.read_bytes()
                except OSError:
                    pass
    zips = [("快照", p) for p in backup.snapshots(vault)]
    try:
        zips += [("備份", Path(g["path"])) for g in backup.list_backups(vault) if g["kind"] != "snapshot" and g["usable"]]
    except (OSError, ValueError):
        pass
    for label, z in zips:
        try:
            with zipfile.ZipFile(str(z)) as zf:
                yield label, zf.read(rel)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, RuntimeError):
            continue


def _rebuild_inventory(vault):
    """沒有備份：從檔案卡檔頭（編號、出處、原檔、指紋）重建清冊；位置的大小與時間留空，下次掃描補。沒有任何卡回 None。"""
    from kb._lib import cards
    groups, _broken = cards._scan_groups(vault)
    reverse = {v: k for k, v in cards.ORIGIN_TEXT.items()}
    entries = []
    for cid in sorted(groups):
        front = groups[cid][0]["front"]
        locs = front.get("原檔") if isinstance(front.get("原檔"), list) else []
        fp = front.get("指紋")
        entries.append({"id": cid, "origin": reverse.get(front.get("出處"), "client_file"),
                        "locations": [{"uri": u, "size": None, "mtime": None} for u in locs],
                        "first_seen": front.get("製卡日期"),
                        "fingerprints": [{"sha256": fp, "time": None, "turn": None}] if isinstance(fp, str) and fp else [],
                        "status": "active", "read": "ok", "missing_since": None})
    from kb._lib import inventory
    return {"schema_version": inventory.SCHEMA_VERSION, "entries": entries} if entries else None


def _rebuild_entities(vault):
    """沒有備份：從綜合卡檔頭（編號、型別、名稱、別名）重建實體清單，全部算 active、inferred、有綜合卡。沒有任何綜合卡回 None。
    只在檔案卡實體欄出現、沒有綜合卡的名字不重建（型別不明）。"""
    from kb._lib import cards, entities
    rows = []
    for eid, rels in sorted(entities.synthesis_files(vault).items()):
        try:
            front = cards.parse_front((Path(vault) / rels[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(front.get("名稱"), str) or not isinstance(front.get("型別"), str):
            continue
        aliases = front.get("別名") if isinstance(front.get("別名"), list) else []
        rows.append({"id": eid, "type": front["型別"], "name": front["名稱"], "aliases": aliases, "self": False,
                     "status": "active", "source": "inferred", "date": datetime.now().strftime("%Y-%m-%d"),
                     "card": True, "created_turn": None})
    return {"schema_version": entities.SCHEMA_VERSION, "entities": rows} if rows else None


REBUILDERS = {".system/inventory.json": _rebuild_inventory, ".system/entities.json": _rebuild_entities}
CANT_RECOVER = "{name}壞了，備份裡也找不到讀得懂的一份，這份沒辦法自己重建"


def recover_json(vault, rel):
    """.system/*.json 讀不懂時自己修（放寬丙 6-3，D-311(2)）：照順序找最近一份讀得懂的（回合備份由新到舊 → 快照 → 最近的 kb backup zip），
    找到就把壞的留在 .system/broken/<時間>-<檔名>、換回去、記工作日誌程式段；都找不到：inventory.json 從檔案卡、entities.json 從綜合卡重建
    （沒有卡可重建就不動），其他回錯誤句。回 {"ok","source"（回合備份｜快照｜備份｜重建）,"broken"（壞的留在哪）,"message"}。
    不持鎖（原子寫），同一個檔還原中再被讀到直接回失敗。"""
    vault = Path(vault)
    key = (str(vault), rel)
    name = Path(rel).name
    if key in _RECOVERING:
        return {"ok": False, "source": None, "broken": None, "message": CANT_RECOVER.format(name=name)}
    _RECOVERING.add(key)
    try:
        target = vault / rel
        data = source = None
        try:
            for label, raw in _candidates(vault, rel):
                if _readable(raw):
                    data, source = raw.decode("utf-8"), label
                    break
            if data is None and rel in REBUILDERS:
                built = REBUILDERS[rel](vault)
                if built is not None:
                    data, source = json.dumps(built, ensure_ascii=False, indent=2) + "\n", "重建"
        except (OSError, ValueError):
            data = None
        if data is None:
            return {"ok": False, "source": None, "broken": None, "message": CANT_RECOVER.format(name=name)}
        broken = None
        if target.is_file():
            broken = f"{BROKEN_REL}/{datetime.now():%Y%m%d-%H%M%S}-{name}"
            (vault / BROKEN_REL).mkdir(parents=True, exist_ok=True)
            (vault / broken).write_bytes(target.read_bytes())
        write_text_atomic(target, data)
        _log(vault, rel, source, broken)
        return {"ok": True, "source": source, "broken": broken, "message": f"{name}壞了，換回{source}的那份"}
    except OSError:
        return {"ok": False, "source": None, "broken": None, "message": CANT_RECOVER.format(name=name)}
    finally:
        _RECOVERING.discard(key)


def _log(vault, rel, source, broken):
    """工作日誌程式段；記不了不影響還原。"""
    try:
        from kb._lib import journal
        word = journal.SYSTEM_WORDS.get(rel, rel)
        how = "從卡重建了一份" if source == "重建" else f"換回{source}裡最近一份讀得懂的"
        journal.record_program(vault, f"{word}的檔壞了，{how}" + (f"；壞的那份留在 {broken}" if broken else ""),
                               [{"path": rel, "action": "modify"}], "recover")
    except Exception:  # noqa: BLE001
        pass


def write_text_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for i in range(REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data):
    from kb._lib import streams
    v = streams.virtual_kind(path)
    if v is not None and isinstance(data, dict):
        streams.write_doc(*v, data)
        return
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class FileLock:
    """with FileLock(path): …。鎖檔是 <path>.lock，O_CREAT|O_EXCL 保證只有一個程序建得成。
    超過 stale 秒的鎖視為殘留（程式中途被中斷）清掉；等超過 timeout 秒丟 TimeoutError（是 OSError 的子類）。
    Windows 上 O_EXCL 碰到待刪除的鎖檔會丟 PermissionError，當成有人持鎖繼續等（L6）。
    """

    def __init__(self, path, timeout=None, stale=None):
        self.path = str(path) + ".lock"
        self.timeout = LOCK_TIMEOUT if timeout is None else timeout  # 呼叫時才讀模組值：CLI 可調成 30（R2-L3）
        self.stale = STALE_LOCK_SECONDS if stale is None else stale
        self.fd = None

    def __enter__(self):
        start = time.time()
        while True:
            if time.time() - start > self.timeout:  # 逾時檢查在每一圈最前面（R2-1）
                raise TimeoutError(f"等待檔案鎖逾時：{self.path}")
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                pass
            except PermissionError:
                if not os.path.exists(self.path):
                    raise  # 鎖檔根本建不起來（回合夾不可寫）：直接丟給呼叫端，不當成有人持鎖（R2-1）
            try:
                if time.time() - os.path.getmtime(self.path) > self.stale:
                    try:
                        os.remove(self.path)
                    except FileNotFoundError:
                        pass  # 別的程序先清掉了
                    continue
            except OSError:
                pass  # 鎖檔剛好消失或讀不到 mtime：照走逾時檢查與 sleep
            time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
