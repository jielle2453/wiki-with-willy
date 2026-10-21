"""口述清單 .system/statements.json（schema 1，D-146；note 改知識第 1 點）。

一條一句客戶在對話裡說的事實或決定：id（s- 加 8 碼 hex）、text、entities（登記的實體名稱，客戶自己也可以；
rename／merge 由 entities 呼叫這裡一起換，移除時照留）、date、turn、status（active｜superseded｜withdrawn）、
replaced_by（被哪一條取代）。被取代、收回的條目留著（綜合卡紀事會引用它），清理交給 lint。
綜合卡的「整理到」收 s- 行（指紋欄寫 -），出處寫〔s-…〕，所以有新口述或口述被取代時，程式把那個實體列進待更新綜合卡。
不渲染 md（工作日誌已記）。每次寫都在 statements.json 的 FileLock 裡：先 turn.backup 再原子寫（D-074）；只經 kb statement
與 entities 寫。
"""
import secrets
from datetime import date
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

STATEMENTS_POSIX = ".system/statements.json"
SCHEMA_VERSION = 1
STATUSES = ("active", "superseded", "withdrawn")
TEXT_MAX = 200
BAD_CHARS = '"$`\\'  # 一句口述會被貼進雙引號的指令（同 plan.FREE_TEXT_RULE）
TEXT_MSG = f"口述要一句話，最多 {TEXT_MAX} 字"
CHARS_MSG = "口述裡不能有 \" $ ` \\ 這四種字元"
NOT_FOUND = "沒有這條口述：{}"
SUPERSEDED_MSG = "這條已經被取代了，改最新的那條"
WITHDRAWN_MSG = "這條已經收回了"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"


class StatementRefused(ValueError):
    """kb statement 的拒絕：訊息就是給人看的那句。"""


def _today():
    return date.today().isoformat()


def _path(vault):
    return Path(vault) / STATEMENTS_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "statements": []}


def load(vault):
    """沒檔回空的；壞 JSON、不是物件、statements 不是清單、元素不是物件、版本比這版新丟 ValueError，訊息帶檔名。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{STATEMENTS_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict):
        raise ValueError(f"{STATEMENTS_POSIX}（不是物件）")
    items = data.get("statements")
    if not isinstance(items, list):
        raise ValueError(f"{STATEMENTS_POSIX}（沒有 statements 清單）")
    if any(not isinstance(i, dict) for i in items):
        raise ValueError(f"{STATEMENTS_POSIX}（有一條不是物件）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{STATEMENTS_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "statements": items}


def find(data, sid):
    return next((s for s in data["statements"] if s.get("id") == sid), None)


def active_for(data, name):
    """掛著 name（登記的名稱）的有效口述，照清單順序。"""
    from kb._lib import entities
    k = entities.key(name)
    return [s for s in data["statements"] if s.get("status") == "active"
            and any(entities.key(n) == k for n in s.get("entities") or [])]


# ---------- 寫 ----------

def _mutate(vault, turn_id, fn):
    """鎖住讀改寫：fn(data) 回 (結果, 有沒有改)；有改才備份、寫。回結果。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        result, changed = fn(data)
        if changed:
            from kb._lib import turn
            turn.backup(vault, STATEMENTS_POSIX, turn_id)
            write_json_atomic(p, data)
    return result


def _new_id(data):
    taken = {s.get("id") for s in data["statements"]}
    while True:
        new = "s-" + secrets.token_hex(4)
        if new not in taken:
            return new


def check_text(text):
    """一句（不換行、不空）、最多 TEXT_MAX 字、不含 BAD_CHARS；回壓過空白的字串，不行丟 StatementRefused。"""
    raw = str(text or "")
    if "\n" in raw or "\r" in raw:
        raise StatementRefused(TEXT_MSG)
    text = " ".join(raw.split())
    if not text or len(text) > TEXT_MAX:
        raise StatementRefused(TEXT_MSG)
    if any(c in text for c in BAD_CHARS):
        raise StatementRefused(CHARS_MSG)
    return text


def names_of(vault, names):
    """實體名稱 → 登記的名稱（別名、合併過的轉到目標，去重）；沒登記或已移除的丟 StatementRefused（客戶自己可以）。"""
    from kb._lib import entities
    data = entities.load(vault)
    out = []
    for name in names:
        entry, _hit = entities.find(data, name)
        if entry is None or entry.get("status") != "active":
            raise StatementRefused(NO_ENTITY.format(name))
        if entry["name"] not in out:
            out.append(entry["name"])
    return out


def _need_active(data, sid):
    it = find(data, sid)
    if it is None:
        raise StatementRefused(NOT_FOUND.format(sid))
    if it.get("status") == "superseded":
        raise StatementRefused(SUPERSEDED_MSG)
    if it.get("status") != "active":
        raise StatementRefused(WITHDRAWN_MSG)
    return it


def add(vault, text, entities, turn_id, replaces=None):
    """回 (新條目, 被取代的條目或 None)。驗完才寫：一句、字元、實體都登記且 active、replaces 是有效的口述；
    replaces 那條標 superseded、記 replaced_by。"""
    text = check_text(text)
    names = names_of(vault, list(entities or []))
    if not names:
        raise StatementRefused(NO_ENTITY.format(""))

    def fn(data):
        old = _need_active(data, replaces) if replaces else None
        it = {"id": _new_id(data), "text": text, "entities": names, "date": _today(), "turn": turn_id,
              "status": "active", "replaced_by": None}
        data["statements"].append(it)
        if old is not None:
            old["status"] = "superseded"
            old["replaced_by"] = it["id"]
        return (it, old), True
    return _mutate(vault, turn_id, fn)


def withdraw(vault, sid, turn_id):
    """客戶說「沒這回事」：有效的口述改 withdrawn。回條目。"""
    def fn(data):
        it = _need_active(data, sid)
        it["status"] = "withdrawn"
        return it, True
    return _mutate(vault, turn_id, fn)


def rename_entity(vault, mapping, turn_id, undo=None):
    """entities.rename／merge 呼叫：entities 裡 key 在 mapping 的名稱換成新名（去重，順序照原樣），任何狀態的條目都換。
    沒有 statements.json 什麼都不做。undo 是 entities._Undo：寫之前記舊內容。回動到的條目。"""
    from kb._lib import entities
    if not _path(vault).exists():
        return []

    def fn(data):
        hits = []
        for it in data["statements"]:
            names = it.get("entities") or []
            if not any(entities.key(n) in mapping for n in names):
                continue
            new = []
            for n in names:
                n = mapping.get(entities.key(n), n)
                if all(entities.key(x) != entities.key(n) for x in new):
                    new.append(n)
            it["entities"] = new
            hits.append(it)
        if hits and undo is not None:
            undo.file(STATEMENTS_POSIX)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)
