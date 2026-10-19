"""實體清單 .system/entities.json（schema 2，D-114～D-124、D-126、D-127；實體與綜合卡第 1 點、日程第 3 點）。

一筆一個客戶管的對象、事、物、主題：id（e- 加 8 碼 hex）、type（型別表的名稱；客戶自己是 null）、name、aliases、
self（false｜company｜person，D-120）、status（active｜removed｜merged，merged 帶 merged_into）、source（D-041 四選一）、
date、card（寫過綜合卡沒有）、created_turn。名稱在整個知識庫唯一（不分型別，NFKC 後不分大小寫，別名也算），
所以檔案卡檔頭 實體 欄寫名稱就是鍵；改名與合併由這裡把所有檔案卡的那一欄與日程條目的實體名稱一起改。
schema 2（日程第 3 點）：大類是「事」的實體多 progress（ongoing｜closed｜paused）、progress_source、progress_history；
schema 1 讀進來在記憶體補上，寫的時候寫 2。實體本身在不在是 status，事類進行到哪是 progress，兩件事。
只准程式寫、讀改寫整段在 FileLock 裡（I-080），寫前備進回合（D-074）。綜合卡的搬、刪也在這裡（在同一把鎖裡），
檔頭格式與驗證住 synthesis.py。drift 只讀：認出客戶在檔案總管改了實體夾名、卡名、刪夾（D-057）。
"""
import os
import posixpath
import secrets
import unicodedata
from datetime import date
from pathlib import Path

from kb._lib import config, profile
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

ENTITIES_POSIX = ".system/entities.json"
SCHEMA_VERSION = 2
PROGRESS = ("ongoing", "closed", "paused")
PROGRESS_TEXT = {"ongoing": "進行中", "closed": "結案", "paused": "擱置"}
CLOSE_KIND = "close"
SHELL_CHARS = '$`'  # 名字會被貼進雙引號的指令：" 與 \ 型別名規則已擋，這兩個另擋（日程釐清，整理 ingest 測試第二輪）
SELF_KINDS = {"公司": "company", "本人": "person"}
HEAD_BYTES = 4096
NOT_FOUND = "實體清單沒有「{}」"


class EntityRefused(ValueError):
    """kb entity 的拒絕：訊息就是給人看的那句。"""


def _today():
    return date.today().isoformat()


def _path(vault):
    return Path(vault) / ENTITIES_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "entities": []}


class Unreadable(ValueError):
    """entities.json 讀不了：訊息是「.system/entities.json（<原因>）」（測試後修正裁定 11）。"""


def load(vault):
    """沒檔回空的；壞 JSON、不是物件、entities 不是清單、元素不是物件、版本比這版新丟 Unreadable（ValueError），
    訊息帶檔名，CLI 照「狀態檔讀不到：…」印。"""
    try:
        data = _load(vault)
    except ValueError as e:
        raise Unreadable(f"{ENTITIES_POSIX}（{e}）") from e
    _fill_progress(vault, data)
    return data


def _load(vault):
    data = read_json(_path(vault))
    if data is None:
        return empty()
    if not isinstance(data, dict):
        raise ValueError("實體清單不是物件")
    items = data.get("entities")
    if not isinstance(items, list):
        raise ValueError("實體清單沒有 entities 清單")
    if any(not isinstance(e, dict) for e in items):
        raise ValueError("實體清單有一筆不是物件")
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"實體清單版本 {version!r} 比這版 plugin 新")
    return {"schema_version": SCHEMA_VERSION, "entities": items}


def _fill_progress(vault, data):
    """schema 1 的條目、或型別後來才變成事類的條目：大類是「事」的補 progress ongoing／inferred／空歷程（只在記憶體，下次寫入才落檔）。"""
    needs = [e for e in data["entities"] if e.get("type") and "progress" not in e]
    if not needs:
        return
    prof = _profile(vault)
    for e in needs:
        row = type_of(vault, e["type"], prof)
        if row is not None and row.get("category") == "matter":
            e.update(_new_progress())


def _new_progress():
    return {"progress": "ongoing", "progress_source": "inferred", "progress_history": []}

def lock(vault):
    _path(vault).parent.mkdir(parents=True, exist_ok=True)
    return FileLock(_path(vault))


def _save(vault, data, turn_id, undo=None):
    """呼叫端持有鎖：先備進回合再寫；undo 給了就記舊內容（中途出錯還原用）。"""
    from kb._lib import turn
    turn.backup(vault, ENTITIES_POSIX, turn_id)
    if undo is not None:
        undo.file(ENTITIES_POSIX)
    write_json_atomic(_path(vault), {"schema_version": SCHEMA_VERSION, "entities": data["entities"]})


# ---------- 找 ----------

def key(name):
    """名稱比對用：NFKC 後 casefold。"""
    return unicodedata.normalize("NFKC", str(name or "")).casefold()


def words(entry):
    return [entry.get("name") or ""] + list(entry.get("aliases") or [])


def _resolve(data, entry):
    """merged 的條目沿著 merged_into 找到目標；斷鏈或繞圈就停在最後找得到的那筆。"""
    by_id = {e.get("id"): e for e in data["entities"]}
    seen = set()
    while entry.get("status") == "merged" and entry.get("merged_into") in by_id and entry.get("id") not in seen:
        seen.add(entry.get("id"))
        entry = by_id[entry["merged_into"]]
    return entry


def find(data, name_or_id):
    """先比 id，再比名稱，再比別名；回 (條目, "id"|"name"|"alias") 或 (None, None)。merged 的轉到目標。"""
    text = str(name_or_id or "").strip()
    for e in data["entities"]:
        if e.get("id") == text:
            return _resolve(data, e), "id"
    k = key(text)
    for e in data["entities"]:
        if key(e.get("name")) == k:
            return _resolve(data, e), "name"
    for e in data["entities"]:
        if any(key(a) == k for a in e.get("aliases") or []):
            return _resolve(data, e), "alias"
    return None, None


def is_self(entry):
    return bool(entry.get("self"))


def _collision(data, name, ignore=None):
    """名稱撞到的條目（名稱或別名，任何狀態；merged 的轉到目標），自己（ignore）不算。"""
    k = key(name)
    for e in data["entities"]:
        if ignore is not None and e.get("id") == ignore.get("id"):
            continue
        if any(key(w) == k for w in words(e)):
            target = _resolve(data, e)
            if ignore is not None and target.get("id") == ignore.get("id"):
                continue
            return target
    return None


def _char_problem(vault, name):
    why = profile.type_name_problem(name)
    if not why and (profile._LIST_CHARS.search(name) or any(c in name for c in SHELL_CHARS)):
        why = "含不能用的字元"
    if not why and profile._WINDOWS_RESERVED.match(name):
        why = "Windows 保留名"
    max_chars = config.get(vault, "card_title_max_chars")
    if not why and len(name) > max_chars:
        why = f"太長，最多 {max_chars} 字"
    return why


def name_problem(vault, data, name, ignore=None):
    """名稱不能用的那句，能用回 None。字元規則同型別名（不能用的字元、點、空白、Windows 保留名、card_title_max_chars）；
    撞到條目的名稱或別名「已有「X」（型別）」；撞到客戶自己「「X」是你自己（關於你）」。"""
    why = _char_problem(vault, name)
    if why:
        return f"名稱「{name}」不能用（{why}）"
    hit = _collision(data, name, ignore)
    if hit is None:
        return None
    if is_self(hit):
        return f"「{name}」是你自己（關於你）"
    return f"已有「{hit.get('name')}」（{hit.get('type')}）"


def refusal(vault, data, name, ignore=None):
    """name_problem 加上給客戶的下一步（kb entity add／rename 的拒絕句）。"""
    problem = name_problem(vault, data, name, ignore)
    if problem is None:
        return None
    hit = _collision(data, name, ignore)
    if hit is not None and is_self(hit):
        return f"{problem}，掛「{hit.get('name')}」"
    if hit is not None:
        return f"{problem}；是同一件就掛它，不是就加區分詞（例：{name}-大安）"
    return problem


def _profile(vault):
    try:
        return profile.load(vault) or profile.defaults()
    except ValueError:
        return profile.defaults()


def type_of(vault, type_, prof=None):
    """型別表裡的那一列（不分全半形大小寫）；沒有回 None。"""
    prof = prof or _profile(vault)
    return next((t for t in prof.get("types") or [] if key(t["name"]) == key(type_)), None)


def type_refusal(type_):
    return f"型別「{type_}」不在型別表；在設定檔第一節加一列再套用"


def _new_id(data):
    taken = {e.get("id") for e in data["entities"]}
    while True:
        new = "e-" + secrets.token_hex(4)
        if new not in taken:
            return new


def _need(data, ref):
    entry, hit = find(data, ref)
    if entry is None:
        raise EntityRefused(NOT_FOUND.format(ref))
    return entry, hit


def _need_plain(entry, verb):
    """一般實體（不是客戶自己、active）才能 rename／merge／remove。"""
    if is_self(entry):
        raise EntityRefused(f"「{entry['name']}」是你自己（關於你），要改在客戶設定檔第五節改")
    if entry.get("status") != "active":
        raise EntityRefused(f"「{entry['name']}」已移除，不能{verb}")


# ---------- 登記與確認 ----------

def add(vault, type_, name, turn_id):
    """回 (條目, 新建了嗎)。型別要在型別表；同型別同名已有（active）回既有、不寫（冪等）；名稱不能用丟 EntityRefused。"""
    row = type_of(vault, type_)
    if row is None:
        raise EntityRefused(type_refusal(type_))
    with lock(vault):
        data = load(vault)
        for e in data["entities"]:
            if (e.get("status") == "active" and not is_self(e) and key(e.get("type")) == key(row["name"])
                    and key(e.get("name")) == key(name)):
                return e, False
        why = refusal(vault, data, name)
        if why:
            raise EntityRefused(why)
        entry = {"id": _new_id(data), "type": row["name"], "name": name, "aliases": [], "self": False,
                 "status": "active", "source": "inferred", "date": _today(), "card": False, "created_turn": turn_id}
        if row.get("category") == "matter":
            entry.update(_new_progress())
        data["entities"].append(entry)
        _save(vault, data, turn_id)
    return entry, True


def confirm(vault, ref, turn_id):
    """source 改 confirmed、date 今天；回條目。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        entry["source"] = "confirmed"
        entry["date"] = _today()
        _save(vault, data, turn_id)
    return entry


def set_card(vault, entity_id, value, turn_id):
    """PostToolUse 寫過合法綜合卡後 card 改 true；值沒變不寫。回有沒有改。"""
    with lock(vault):
        data = load(vault)
        entry = next((e for e in data["entities"] if e.get("id") == entity_id), None)
        if entry is None or bool(entry.get("card")) == bool(value):
            return False
        entry["card"] = bool(value)
        _save(vault, data, turn_id)
    return True


# ---------- 掛載 ----------

def mounts(vault, data=None):
    """檔案索引/ 全部卡的檔頭 實體 → {實體名稱: [{"id","path","title","fp","date"}]}。只算清冊條目 active 的檔案卡
    （同編號多張取第一張）與 active 的實體；名稱照登記的（別名、合併過的轉到目標）；fp 是內容指紋（沒有取指紋）前 12 碼。"""
    from kb._lib import cards, inventory
    data = data if data is not None else load(vault)
    try:
        active = {e.get("id") for e in inventory.load(vault)["entries"] if e.get("status") == "active"}
    except ValueError:
        active = set()
    groups, _broken = cards._scan_groups(vault)
    out = {}
    for cid in sorted(groups):
        if cid not in active:
            continue
        card = groups[cid][0]
        front = card["front"]
        names = front.get("實體")
        if not isinstance(names, list):
            continue
        fp = front.get("內容指紋") or front.get("指紋")
        item = {"id": cid, "path": card["path"], "title": front.get("標題") if isinstance(front.get("標題"), str) else "",
                "fp": fp[:12] if isinstance(fp, str) else "",
                "date": front.get("文件日期") if isinstance(front.get("文件日期"), str) else ""}
        seen = set()
        for name in names:
            entry, _hit = find(data, name)
            if entry is None or entry.get("status") != "active" or entry.get("id") in seen:
                continue
            seen.add(entry.get("id"))
            out.setdefault(entry["name"], []).append(dict(item))
    return out


# ---------- 綜合卡位置 ----------

def card_rel(entry):
    return f"{entry.get('type')}/{entry.get('name')}/{entry.get('name')}.md"


def head_id(path):
    """檔頭前 4 KB 的「編號」值；第一行不是 ---、沒有編號、讀不了回 None（I-046：不丟例外）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return None
    lines = head.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        k, sep, value = line.partition(":")
        if sep and k.strip() == "編號":
            return value.strip().strip('"') or None
    return None


def synthesis_files(vault):
    """型別夾第三層、檔頭編號是 e- 開頭的 .md → {編號: [相對路徑, …]}（照路徑排）。第一層用 profile._first_level_dirs 的跳過規則。"""
    out = {}
    for top in profile._first_level_dirs(vault):
        try:
            subs = sorted(os.scandir(Path(vault) / top), key=lambda i: i.name)
        except OSError:
            continue
        for sub in subs:
            if sub.name.startswith(".") or not sub.is_dir(follow_symlinks=False):
                continue
            try:
                files = sorted(os.scandir(sub.path), key=lambda i: i.name)
            except OSError:
                continue
            for f in files:
                if f.name.startswith(".") or not f.name.casefold().endswith(".md") or not f.is_file(follow_symlinks=False):
                    continue
                eid = head_id(f.path)
                if eid and eid.startswith("e-"):
                    out.setdefault(eid, []).append(f"{top}/{sub.name}/{f.name}")
    return out


def _drift_kind(entry, found):
    if not found:
        return "missing"
    top, folder, name = found.split("/")
    stem = name[:-3]
    if key(top) != key(entry.get("type")):
        return "moved_type"
    folder_ok, file_ok = key(folder) == key(entry["name"]), key(stem) == key(entry["name"])
    if folder_ok and file_ok:
        return None
    if folder_ok:
        return "card_renamed"
    if file_ok:
        return "folder_renamed"
    if key(folder) == key(stem):
        return "both_renamed"
    return "folder_renamed"


def drift(vault, data=None):
    """只讀。card true、active、不是客戶自己的實體，綜合卡不在 <型別>/<名稱>/<名稱>.md 時，掃型別夾第三層找檔頭編號是它的；
    回 [{"entity_id","name","type","kind","found"}]，kind：folder_renamed｜card_renamed｜both_renamed｜moved_type｜missing。"""
    data = data if data is not None else load(vault)
    out, index = [], None
    for e in data["entities"]:
        if not e.get("card") or e.get("status") != "active" or is_self(e) or not e.get("type"):
            continue
        if (Path(vault) / card_rel(e)).is_file():
            continue
        if index is None:
            index = synthesis_files(vault)
        found = (index.get(e.get("id")) or [None])[0]
        kind = _drift_kind(e, found)
        if kind:
            out.append({"entity_id": e["id"], "name": e["name"], "type": e.get("type"), "kind": kind, "found": found})
    return out


def locate(vault, entry, data=None):
    """這個實體的綜合卡現在在哪（相對路徑）：card_rel 在就是它，否則 drift 找到的；都沒有回 None。"""
    if (Path(vault) / card_rel(entry)).is_file():
        return card_rel(entry)
    if not entry.get("type"):
        return None
    return (synthesis_files(vault).get(entry.get("id")) or [None])[0]


def _files_under(folder, skip=None):
    """資料夾底下全部的檔（遞迴），不含 skip（絕對路徑）。"""
    out = []
    for dirpath, _dirs, names in os.walk(folder):
        for n in names:
            p = os.path.join(dirpath, n)
            if skip is None or os.path.normcase(os.path.abspath(p)) != os.path.normcase(os.path.abspath(skip)):
                out.append(p)
    return out


def _rmdir_if_empty(vault, folder_rel, undo=None):
    d = Path(vault) / folder_rel
    try:
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            if undo is not None:
                undo.rmdir(folder_rel)
            return True
    except OSError:
        pass
    return False


class PartialWrite(EntityRefused):
    """寫到一半失敗、還原也沒成功：訊息就是給人看的那句（不能說「沒有寫入」）。"""


class _Undo:
    """這個指令動過的檔與夾，照發生順序記（測試後修正裁定 8）：改、建、刪檔之前記舊內容（沒有是 None），
    搬夾或改檔名之後記 (來源, 目的)，拿掉空夾之後記夾。中途出錯就倒著做回去。回合備份照記，不還原（帳本）。"""

    def __init__(self, vault):
        self.v = Path(vault)
        self.steps = []

    def file(self, rel):
        p = self.v / rel
        self.steps.append(("file", rel, p.read_bytes() if p.is_file() else None))

    def moved(self, src, dst):
        self.steps.append(("move", src, dst))

    def rmdir(self, rel):
        self.steps.append(("rmdir", rel, None))

    def rollback(self):
        for kind, a, b in reversed(self.steps):
            if kind == "file":
                p = self.v / a
                if b is None:
                    if p.is_file():
                        os.remove(p)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b)
            elif kind == "move":
                os.rename(self.v / b, self.v / a)
            else:
                (self.v / a).mkdir(parents=True, exist_ok=True)


def _guarded(vault, act):
    """act(undo) 動手；中途任何例外就照 undo 還原再丟回去（OSError 由 CLI 印「沒有寫入：…」）。
    還原本身也失敗才丟 PartialWrite：檔可能留一半，請客戶說「退回上一版」。"""
    undo = _Undo(vault)
    try:
        return act(undo)
    except Exception as e:  # noqa: BLE001
        try:
            undo.rollback()
        except OSError as again:
            raise PartialWrite(f"寫到一半失敗、沒能全部還原（{type(e).__name__}: {e}；還原時 {type(again).__name__}）；"
                               "說「退回上一版」救得回來") from e
        raise


def _delete_card(vault, rel, turn_id, undo=None):
    """綜合卡備成 delete 後刪；夾空了 rmdir。回夾裡剩幾個檔。"""
    from kb._lib import turn
    turn.backup(vault, rel, turn_id, action="delete", by="program")
    if undo is not None:
        undo.file(rel)
    os.remove(Path(vault) / rel)
    folder = rel.rpartition("/")[0]
    if _rmdir_if_empty(vault, folder, undo):
        return 0
    return len(_files_under(Path(vault) / folder))


def _write(vault, rel, text, undo):
    if undo is not None:
        undo.file(rel)
    write_text_atomic(Path(vault) / rel, text)


def _card_fields(prof, entry):
    from kb._lib import synthesis
    return {"名稱": entry["name"], "別名": list(entry.get("aliases") or []), "型別": entry.get("type"),
            "大類": synthesis.category_text(prof, entry.get("type"))}


def _refresh_fields(vault, entry, prof, turn_id, changed, undo):
    """程式改了實體的名稱、別名、型別（測試後修正裁定 9）：它的綜合卡檔頭那幾欄跟著改（by program）；客戶自己的不碰。"""
    from kb._lib import synthesis, turn
    if is_self(entry) or not entry.get("type"):
        return
    loc = locate(vault, entry)
    if not loc:
        return
    text = (Path(vault) / loc).read_text(encoding="utf-8")
    new = synthesis.replace_front(text, _card_fields(prof, entry))
    if new != text:
        turn.backup(vault, loc, turn_id, by="program")
        _write(vault, loc, new, undo)
        changed.append({"path": loc, "action": "modify"})


# ---------- 檔案卡的 實體 欄 ----------

def retarget_cards(vault, mapping, turn_id, undo=None):
    """檔案索引/ 每張卡的 實體 欄，名稱 key 在 mapping 裡的換成 mapping 的值（去重，順序照原樣）；改到的卡先備進回合。
    回 [(相對路徑, 換之前的名稱清單)]。"""
    from kb._lib import cards, turn
    changed = []
    for rel in cards._walk_cards(vault):
        p = Path(vault) / rel
        try:
            text = p.read_text(encoding="utf-8")
            names = cards.parse_front(text).get("實體")
        except (ValueError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(names, list) or not any(key(n) in mapping for n in names):
            continue
        new = []
        for n in names:
            n = mapping.get(key(n), n)
            if all(key(x) != key(n) for x in new):
                new.append(n)
        turn.backup(vault, rel, turn_id, by="program")
        _write(vault, rel, cards.rewrite_entities(text, new), undo)
        changed.append((rel, names))
    return changed


# ---------- 改名、合併、移除、重寫、清 ----------

def _occupied(vault, loc, target):
    if loc and key(loc) != key(target) and (Path(vault) / target).exists():
        raise EntityRefused(f"「{target}」已經有檔，沒有改名；先把它搬走")


def rename(vault, ref, new_name, new_type, turn_id):
    """kb entity rename：名稱（舊名進別名）、型別；有綜合卡就搬到 <新型別>/<新名>/<新名>.md（目標夾不在就整個夾改名，在就只搬卡）、
    檔頭的名稱／別名／型別／大類與正文標題改寫；全部檔案卡的 實體 欄換新名。整段在鎖裡。
    會擋的條件先驗完才動手；動手順序：搬夾與卡 → 檔案卡 → entities.json，中途出錯照 _Undo 還原（測試後修正裁定 8）。
    回 {"entity", "old_name", "via_alias", "cards"（改到的檔案卡）, "changed"（日誌用）, "moved_files", "card"}。"""
    from kb._lib import synthesis
    prof = _profile(vault)
    with lock(vault):
        data = load(vault)
        entry, hit = _need(data, ref)
        _need_plain(entry, "改名")
        row = type_of(vault, new_type, prof) if new_type else type_of(vault, entry.get("type"), prof)
        if new_type and row is None:
            raise EntityRefused(type_refusal(new_type))
        why = refusal(vault, data, new_name, ignore=entry)
        if why:
            raise EntityRefused(why)
        old_name = entry["name"]
        loc = locate(vault, entry) if entry.get("card") else None
        entry["name"] = new_name
        entry["aliases"] = [a for a in entry.get("aliases") or [] if key(a) != key(new_name)]
        if key(old_name) != key(new_name) and all(key(a) != key(old_name) for a in entry["aliases"]):
            entry["aliases"].append(old_name)
        if row is not None:
            entry["type"] = row["name"]
        target = card_rel(entry)
        _occupied(vault, loc, target)

        def act(undo):
            changed, moved = [], 0
            _agenda_snapshot(vault, undo)
            if loc:
                text = (Path(vault) / loc).read_text(encoding="utf-8")
                text = synthesis.replace_title(synthesis.replace_front(text, _card_fields(prof, entry)), old_name,
                                               entry["name"])
                moved = _move_card(vault, loc, target, text, turn_id, changed, undo)
            touched = retarget_cards(vault, {key(old_name): new_name}, turn_id, undo) if old_name != new_name else []
            changed += [{"path": rel, "action": "modify"} for rel, _names in touched]
            if key(old_name) != key(new_name):
                changed += _agenda_follow(vault, {key(old_name): new_name}, turn_id)
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            changed += _agenda_md(vault, turn_id)
            return touched, changed, moved

        touched, changed, moved_files = _guarded(vault, act)
    return {"entity": entry, "old_name": old_name, "via_alias": hit == "alias", "cards": [r for r, _n in touched],
            "changed": changed, "moved_files": moved_files, "card": target if loc else None}


def _move_card(vault, loc, target, text, turn_id, changed, undo=None):
    """綜合卡從 loc 搬到 target 並寫成 text。夾不同、目標夾不在 → 整個夾改名（os.rename）；目標夾在 → 只搬卡，舊夾空了拿掉。
    備份：卡沒換位置是 modify；換了位置舊路徑 delete、新路徑 create（都在動手之前，退回才回得去）。每一步記進 undo。
    回跟著夾搬的其他檔數。"""
    from kb._lib import turn
    v = Path(vault)
    undo = undo if undo is not None else _Undo(vault)
    cur_folder, tgt_folder = loc.rpartition("/")[0], target.rpartition("/")[0]
    same_folder = key(cur_folder) == key(tgt_folder)
    if key(loc) == key(target):  # 同一個位置（頂多大小寫不同）：改大小寫後照 modify 備
        if cur_folder != tgt_folder:
            os.rename(v / cur_folder, v / tgt_folder)
            undo.moved(cur_folder, tgt_folder)
        interim = f"{tgt_folder}/{loc.rpartition('/')[2]}"
        if interim != target:
            os.replace(v / interim, v / target)
            undo.moved(interim, target)
        if (v / target).read_text(encoding="utf-8") != text:
            turn.backup(vault, target, turn_id, by="program")
            _write(vault, target, text, undo)
            changed.append({"path": target, "action": "modify"})
        return 0
    _occupied(vault, loc, target)
    turn.backup(vault, loc, turn_id, action="delete", by="program")
    turn.backup(vault, target, turn_id, action="create", by="program")
    moved, interim = 0, loc
    if cur_folder != tgt_folder and (same_folder or not (v / tgt_folder).exists()):
        (v / tgt_folder).parent.mkdir(parents=True, exist_ok=True)
        os.rename(v / cur_folder, v / tgt_folder)
        undo.moved(cur_folder, tgt_folder)
        interim = f"{tgt_folder}/{loc.rpartition('/')[2]}"
        if not same_folder:
            moved = len(_files_under(v / tgt_folder, skip=v / interim))
    if key(interim) == key(target):
        if interim != target:
            os.replace(v / interim, v / target)
            undo.moved(interim, target)
    else:
        (v / target).parent.mkdir(parents=True, exist_ok=True)
        _write(vault, target, text, undo)
        undo.file(interim)
        os.remove(v / interim)
        if not same_folder:
            _rmdir_if_empty(vault, cur_folder, undo)
    _write(vault, target, text, undo)
    changed += [{"path": loc, "action": "delete"}, {"path": target, "action": "create"}]
    return moved


def merge(vault, src_ref, dst_ref, turn_id):
    """kb entity merge：來源併進目標（同型別、都 active、都不是客戶自己）。目標別名加來源名稱與別名、目標綜合卡檔頭跟著改
    （之後是 synthesis_stale，測試後修正裁定 9）；檔案卡 實體 欄來源換目標（去重）；來源的綜合卡刪掉（夾裡有其他檔就擋）；
    來源 merged、merged_into 目標。會擋的先驗完；動手順序：刪來源卡 → 目標卡檔頭 → 檔案卡 → entities.json，中途出錯還原。
    回 {"source","target","added","changed","target_card"}。"""
    prof = _profile(vault)
    with lock(vault):
        data = load(vault)
        src, _h1 = _need(data, src_ref)
        dst, _h2 = _need(data, dst_ref)
        if src.get("id") == dst.get("id"):
            raise EntityRefused(f"「{src_ref}」跟「{dst_ref}」是同一個實體")
        _need_plain(src, "合併")
        _need_plain(dst, "合併")
        if key(src.get("type")) != key(dst.get("type")):
            raise EntityRefused(f"型別不同：先 kb entity rename {src['name']} --to {src['name']} --type {dst.get('type')}")
        loc = locate(vault, src)
        folder = (loc or card_rel(src)).rpartition("/")[0]
        if (Path(vault) / folder).is_dir():
            others = _files_under(Path(vault) / folder, skip=(Path(vault) / loc) if loc else None)
            if others:
                raise EntityRefused(f"「{src['name']}」的夾裡還有 {len(others)} 個檔，先把檔搬到「{dst['name']}」再合併")
        before = mounts(vault, data)
        had_dst = {m["id"] for m in before.get(dst["name"], [])}
        added = [m["id"] for m in before.get(src["name"], []) if m["id"] not in had_dst]
        for w in words(src):
            if key(w) != key(dst["name"]) and all(key(a) != key(w) for a in dst.get("aliases") or []):
                dst.setdefault("aliases", []).append(w)
        src["status"] = "merged"
        src["merged_into"] = dst["id"]

        def act(undo):
            changed = []
            _agenda_snapshot(vault, undo)
            if loc:
                _delete_card(vault, loc, turn_id, undo)
                changed.append({"path": loc, "action": "delete"})
            elif (Path(vault) / folder).is_dir():
                _rmdir_if_empty(vault, folder, undo)
            _refresh_fields(vault, dst, prof, turn_id, changed, undo)
            changed += [{"path": rel, "action": "modify"}
                        for rel, _n in retarget_cards(vault, {key(src["name"]): dst["name"]}, turn_id, undo)]
            changed += _agenda_follow(vault, {key(src["name"]): dst["name"]}, turn_id)
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            changed += _agenda_md(vault, turn_id)
            return changed

        changed = _guarded(vault, act)
        target_card = bool(dst.get("card")) and locate(vault, dst) is not None
    return {"source": src, "target": dst, "added": added, "changed": changed, "target_card": target_card}


def remove(vault, ref, turn_id):
    """kb entity remove：status removed（知識不刪、檔案卡上的掛載留著）；綜合卡還在就刪，夾裡沒別的檔就 rmdir。中途出錯還原。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        _need_plain(entry, "移除")
        loc = locate(vault, entry)
        entry["status"] = "removed"

        def act(undo):
            changed = []
            if loc:
                _delete_card(vault, loc, turn_id, undo)
                changed.append({"path": loc, "action": "delete"})
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            return changed

        changed = _guarded(vault, act)
    return {"entity": entry, "changed": changed}


def rebuild(vault, ref, turn_id):
    """kb entity rebuild：只准 drift 認出 missing 的實體，card 改 false（下次 todo 回到待建綜合卡）。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        kinds = {d["entity_id"]: d["kind"] for d in drift(vault, data)}
        if kinds.get(entry.get("id")) != "missing":
            raise EntityRefused(f"「{entry['name']}」的綜合卡還在，不用重寫")
        entry["card"] = False
        _save(vault, data, turn_id)
    return {"entity": entry, "changed": [{"path": ENTITIES_POSIX, "action": "modify"}]}


def clean(vault, rel, turn_id):
    """kb entity clean：型別夾第三層、檔頭編號 e- 開頭、又不在那個實體自己的 card_rel 的 .md，備成 delete 後刪；夾空了拿掉。
    回 {"path", "left"（夾裡還剩幾個檔）}。"""
    rel = posixpath.normpath(str(rel).replace("\\", "/")).lstrip("/") if rel else ""
    with lock(vault):
        data = load(vault)
        parts = rel.split("/")
        if rel.startswith("..") or any(x in ("", ".", "..") for x in parts):
            raise EntityRefused("這不是綜合卡，不能用 clean")
        top_ok = len(parts) == 3 and key(parts[0]) in {key(n) for n in profile._first_level_dirs(vault)}
        eid = head_id(Path(vault) / rel) if top_ok and rel.casefold().endswith(".md") else None
        if not eid or not eid.startswith("e-"):
            raise EntityRefused("這不是綜合卡，不能用 clean")
        entry = next((e for e in data["entities"] if e.get("id") == eid), None)
        if entry is not None and entry.get("type") and key(rel) == key(card_rel(entry)):
            raise EntityRefused("這張綜合卡位置是對的，不用清")
        left = _delete_card(vault, rel, turn_id)
    return {"path": rel, "left": left}


# ---------- 日程跟著走（日程第 3 點） ----------

def _agenda_snapshot(vault, undo):
    """日程檔與 md 在動手之前記進 undo：實體這一步出錯，日程也照樣還原（驗收 9）。沒有日程檔就什麼都不記。"""
    from kb._lib import agenda
    if (Path(vault) / agenda.AGENDA_POSIX).exists():
        undo.file(agenda.AGENDA_POSIX)
        undo.file(agenda.MD_POSIX)


def _agenda_follow(vault, mapping, turn_id):
    """日程條目掛的實體名稱跟著換；回給日誌的 changed。"""
    from kb._lib import agenda
    if agenda.rename_entity(vault, mapping, turn_id):
        return [{"path": agenda.AGENDA_POSIX, "action": "modify"}, {"path": agenda.MD_POSIX, "action": "modify"}]
    return []


def _agenda_md(vault, turn_id):
    """實體清單寫完之後重渲染日程.md（擱置段、名稱住實體清單）；內容沒變不寫。回給日誌的 changed。"""
    from kb._lib import agenda
    if (Path(vault) / agenda.AGENDA_POSIX).exists() and agenda.render(vault, turn_id):
        return [{"path": agenda.MD_POSIX, "action": "modify"}]
    return []


# ---------- 事類狀態（日程第 3 點，D-126、D-127） ----------

def is_matter(vault, entry):
    row = type_of(vault, entry.get("type")) if entry.get("type") else None
    return row is not None and row.get("category") == "matter"


def _need_matter(vault, data, ref):
    entry, _hit = _need(data, ref)
    if not is_matter(vault, entry):
        raise EntityRefused(f"「{entry['name']}」不是「事」類，沒有狀態")
    if entry.get("status") != "active":
        raise EntityRefused(f"「{entry['name']}」已移除，不能改狀態")
    return entry


def set_progress(vault, ref, progress, turn_id, evidence=None, note=None, source="confirmed"):
    """kb entity progress：事類實體的進行中／結案／擱置。歷程加一筆（同狀態再設一次也只加歷程，「客戶說還沒結案」用這個）；
    改 closed 時同一把鎖裡叫 agenda.close_periods 結束它 open 的期間條目；寫完把這個實體 open 的 close 待處理 auto_done。
    回 {"entity","was","closed_periods","reopened"（結案改回進行中，期間條目不重開）,"pending_closed"}。"""
    from kb._lib import agenda, pending
    if progress not in PROGRESS:
        raise ValueError(f"progress 要是 {'、'.join(PROGRESS)} 之一：{progress!r}")
    evidence = list(dict.fromkeys(evidence or []))
    with lock(vault):
        data = load(vault)
        entry = _need_matter(vault, data, ref)
        was = entry.get("progress") or "ongoing"
        entry["progress"], entry["progress_source"] = progress, source
        entry.setdefault("progress_history", []).append(
            {"date": _today(), "progress": progress, "source": source, "evidence": evidence, "note": note or ""})

        def act(undo):
            _agenda_snapshot(vault, undo)
            closed = agenda.close_periods(vault, entry["name"], _today(), turn_id) if progress == "closed" else []
            _save(vault, data, turn_id, undo)
            _agenda_md(vault, turn_id)
            return closed

        closed = _guarded(vault, act)
    how = f"客戶 {_today()} 回答：{PROGRESS_TEXT[progress]}"
    done = False
    try:
        done = pending.auto_done_entity(vault, entry["id"], CLOSE_KIND, how, turn_id)
    except ValueError:  # pending.json 壞了：狀態已改好，待處理下次再關
        pass
    return {"entity": entry, "was": was, "closed_periods": closed, "reopened": was == "closed" and progress == "ongoing",
            "pending_closed": done}


def asked_evidence(vault, entry):
    """問過的結案依據（不再問，D-127）：狀態歷程裡全部的 evidence，加上這個實體 close 待處理（open 與已處理的）裡的依據。"""
    from kb._lib import pending
    out = []
    for h in entry.get("progress_history") or []:
        out.extend(h.get("evidence") or [])
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []
    for i in items:
        if i.get("kind") == CLOSE_KIND and entry.get("id") in (i.get("entity_ids") or []):
            out.extend(i.get("file_ids") or [])
    return list(dict.fromkeys(out))


def suggest_close(vault, ref, evidence, turn_id):
    """kb entity suggest-close：製卡者讀到結束證據，記一條 close 待處理，由程式在回答末尾問（D-127）。
    只收事類、progress 是 ongoing 的實體；依據都問過了 → 不記（skipped asked）；不是進行中 → 不記（skipped not_ongoing）。
    回 {"entity","recorded","skipped","pending_id"}。"""
    from kb._lib import inventory, pending
    evidence = list(dict.fromkeys(evidence or []))
    if not evidence:
        raise EntityRefused("要給依據：--evidence f-…")
    entry = _need_matter(vault, load(vault), ref)
    known = {e.get("id") for e in inventory.load(vault)["entries"]}
    for fid in evidence:
        if fid not in known:
            raise EntityRefused(f"清冊沒有 {fid}")
    if (entry.get("progress") or "ongoing") != "ongoing":
        return {"entity": entry, "recorded": False, "skipped": "not_ongoing", "pending_id": None}
    asked = set(asked_evidence(vault, entry))
    fresh = [f for f in evidence if f not in asked]
    if not fresh:
        return {"entity": entry, "recorded": False, "skipped": "asked", "pending_id": None}
    pid = pending.add(vault, CLOSE_KIND, fresh, f"「{entry['name']}」看起來已經結束了，要記成結案嗎？",
                      "依據" + "".join(f"〔{f}〕" for f in fresh), turn_id, entity_ids=[entry["id"]])
    return {"entity": entry, "recorded": True, "skipped": None, "pending_id": pid}


# ---------- 客戶自己（D-120） ----------

def sync_self(vault, about, turn_id):
    """kb profile apply 呼叫：設定檔第五節每列對 entities.json。沒有就新增 self 條目（type null、source 照那列）；
    名稱或別名撞到既有一般條目 → 那筆改成 self（type null、名稱照第五節、舊名與別名併入）；第五節拿掉的列 → 那筆改回一般條目，
    type 是 null 就 removed（沒有型別不能建卡）。有動到才備份、寫。回 {"added","converted","released","notes"}。"""
    out = {"added": [], "converted": [], "released": [], "notes": []}
    with lock(vault):
        data = load(vault)
        dirty = False
        matched = set()
        for row in about:
            kind = row.get("kind")
            aliases = [a for a in row.get("aliases") or [] if key(a) != key(row["name"])]
            row_keys = {key(w) for w in [row["name"]] + aliases}
            entry = next((e for e in data["entities"] if is_self(e) and key(e.get("name")) == key(row["name"])), None)
            if entry is None:
                entry = next((e for e in data["entities"] if not is_self(e) and e.get("status") != "merged"
                              and any(key(w) in row_keys for w in words(e))), None)
                if entry is not None:
                    merged_aliases = list(aliases)
                    for w in words(entry):
                        if key(w) != key(row["name"]) and all(key(a) != key(w) for a in merged_aliases):
                            merged_aliases.append(w)
                    entry.update({"name": row["name"], "type": None, "self": kind, "aliases": merged_aliases,
                                  "status": "active", "source": row.get("source") or entry.get("source")})
                    out["converted"].append(row["name"])
                    out["notes"].append(f"「{row['name']}」改記成你自己；它原有的綜合卡之後不再更新")
                else:
                    entry = {"id": _new_id(data), "type": None, "name": row["name"], "aliases": aliases, "self": kind,
                             "status": "active", "source": row.get("source") or "interview",
                             "date": row.get("date") or _today(), "card": False, "created_turn": turn_id}
                    data["entities"].append(entry)
                    out["added"].append(row["name"])
                dirty = True
            else:
                want = {"self": kind, "aliases": aliases, "status": "active"}
                if any(entry.get(k) != v for k, v in want.items()):
                    entry.update(want)
                    dirty = True
            matched.add(entry["id"])
        for e in data["entities"]:
            if is_self(e) and e.get("id") not in matched:
                e["self"] = False
                if not e.get("type"):
                    e["status"] = "removed"
                out["released"].append(e["name"])
                dirty = True
        if dirty:
            _save(vault, data, turn_id)
    return out
