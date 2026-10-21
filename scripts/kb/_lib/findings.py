"""推算清單 .system/findings.json（schema 1，D-154、D-155；answer 第 3 點）。

一筆一個 AI 從多張卡算出來、客戶說「記下來」的結果：id（c- 加 8 碼 hex）、text（結果一句）、how（算法一句）、
entities（登記的實體名稱，客戶自己也可以）、basis（[{id, fp}]，fp 是記的時候那張檔案卡的指紋前 12 碼）、
mounts_fp（記的時候掛的實體掛著的卡編號集合的雜湊；掛客戶自己的算全庫的卡）、date、turn、
status（active｜superseded｜withdrawn）、replaced_by。被取代、收回的留著，清理交 lint。
過期（stale）：依據卡指紋變了（或卡不見）、依據卡被客戶說別信（不採用，answer 裁定 15），或那個集合變了（掛的實體多了新卡）；
過期的不直接用，重算記新的一筆取代。記的時候依據含不採用的卡拒絕。
綜合卡的「整理到」收 c- 行（指紋欄寫 -），所以有新推算或推算被取代時，那個實體列進待更新綜合卡（synthesis 接）。
實體改名、合併不改這裡記的名稱：查的時候照 entities.find 轉到現在的名稱（改名時舊名進別名）。
不渲染 md。每次寫都在 findings.json 的 FileLock 裡：先 turn.backup 再原子寫（D-074）；只經 kb finding 寫。
"""
import hashlib
import secrets
from datetime import date
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

FINDINGS_POSIX = ".system/findings.json"
SCHEMA_VERSION = 1
STATUSES = ("active", "superseded", "withdrawn")
LABEL = "推算"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"
NO_BASIS = "依據至少要一張卡（--basis）"
NOT_FOUND = "沒有這筆推算：{}"
SUPERSEDED_MSG = "這筆已經被取代了，改最新的那筆"
WITHDRAWN_MSG = "這筆已經收回了"
STALE_TEXT = "依據有變，待重算"
RULED_OUT_MSG = "「{}」你說過別信，不能當依據"


class FindingRefused(ValueError):
    """kb finding 的拒絕：訊息就是給人看的那句。"""


def _today():
    return date.today().isoformat()


def _path(vault):
    return Path(vault) / FINDINGS_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "findings": []}


def load(vault):
    """沒檔回空的；壞 JSON、不是物件、findings 不是清單、元素不是物件、版本比這版新丟 ValueError，訊息帶檔名。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{FINDINGS_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict):
        raise ValueError(f"{FINDINGS_POSIX}（不是物件）")
    items = data.get("findings")
    if not isinstance(items, list):
        raise ValueError(f"{FINDINGS_POSIX}（沒有 findings 清單）")
    if any(not isinstance(i, dict) for i in items):
        raise ValueError(f"{FINDINGS_POSIX}（有一筆不是物件）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{FINDINGS_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "findings": items}


def find(data, cid):
    return next((c for c in data["findings"] if c.get("id") == cid), None)


def _current_name(ent, name):
    """記的名稱 → 現在登記的名稱（改名、合併轉過去）；ent 沒給或查不到回原名。"""
    if ent is None:
        return name
    from kb._lib import entities
    found, _hit = entities.find(ent, name)
    return found["name"] if found is not None else name


def active_for(data, name, ent=None):
    """掛著 name（登記的名稱）的有效推算，照清單順序。ent 是 entities.load 的結果：給了就把記的名稱轉成現在的名稱再比。"""
    from kb._lib import entities
    k = entities.key(name)
    return [c for c in data["findings"] if c.get("status") == "active"
            and any(entities.key(_current_name(ent, n)) == k for n in c.get("entities") or [])]


# ---------- 過期 ----------

def context(vault, ent=None, mounts=None, groups=None):
    """算 stale 要的東西讀一次給整批用：實體清單、掛載、檔案卡、全庫 active 的卡編號、不採用的卡編號。"""
    from kb._lib import cards, entities, inventory, rulings
    ent = ent if ent is not None else entities.load(vault)
    groups = groups if groups is not None else cards._scan_groups(vault)[0]
    mounts = mounts if mounts is not None else entities.mounts(vault, ent)
    try:
        active = {e.get("id") for e in inventory.load(vault)["entries"] if e.get("status") == "active"}
    except ValueError:
        active = set()
    return {"ent": ent, "groups": groups, "mounts": mounts, "all": {cid for cid in groups if cid in active},
            "ruled_out": cards.ruled_out_ids(rulings.load(vault))}


def _mount_ids(names, ctx):
    from kb._lib import entities
    ids = set()
    for name in names:
        e, _hit = entities.find(ctx["ent"], name)
        if e is None:
            continue
        if entities.is_self(e):
            ids |= ctx["all"]
        else:
            ids |= {m["id"] for m in ctx["mounts"].get(e["name"], [])}
    return ids


def _hash(ids):
    return hashlib.sha256(",".join(sorted(ids)).encode("utf-8")).hexdigest()[:12]


def _card_fp(ctx, cid):
    from kb._lib import agenda
    group = ctx["groups"].get(cid)
    return agenda.card_fp(group[0]["front"]) if group else None


def stale(vault, item, ctx=None):
    """依據卡的指紋跟記下的不同（或卡不見了）、依據卡被說別信，或掛的實體掛著的卡編號集合變了（掛客戶自己的：全庫的卡）。"""
    ctx = ctx if ctx is not None else context(vault)
    for b in item.get("basis") or []:
        if not isinstance(b, dict) or _card_fp(ctx, b.get("id")) != b.get("fp") or b.get("id") in ctx["ruled_out"]:
            return True
    return _hash(_mount_ids(item.get("entities") or [], ctx)) != item.get("mounts_fp")


def brief(vault, item, titles, ctx):
    """給 lookup 與綜合卡待辦：{"id","text","how","basis":[{id,title}],"date","stale"}。"""
    return {"id": item["id"], "text": item.get("text"), "how": item.get("how"),
            "basis": [{"id": b.get("id"), "title": titles.get(b.get("id"))} for b in item.get("basis") or []
                      if isinstance(b, dict)],
            "date": item.get("date"), "stale": stale(vault, item, ctx)}


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
            turn.backup(vault, FINDINGS_POSIX, turn_id)
            write_json_atomic(p, data)
    return result


def _new_id(data):
    taken = {c.get("id") for c in data["findings"]}
    while True:
        new = "c-" + secrets.token_hex(4)
        if new not in taken:
            return new


def _need_active(data, cid):
    it = find(data, cid)
    if it is None:
        raise FindingRefused(NOT_FOUND.format(cid))
    if it.get("status") == "superseded":
        raise FindingRefused(SUPERSEDED_MSG)
    if it.get("status") != "active":
        raise FindingRefused(WITHDRAWN_MSG)
    return it


def _names(vault, names):
    from kb._lib import statements
    try:
        return statements.names_of(vault, names)
    except statements.StatementRefused as e:
        raise FindingRefused(str(e)) from None


def _basis(vault, ids):
    """每張依據卡要在清冊 active 且有檔案卡（同 cards.card_of）、不是客戶說別信的；回 [{id, fp}]，去重照順序。"""
    from kb._lib import agenda, cards, rulings
    out = []
    ruled_out = cards.ruled_out_ids(rulings.load(vault))
    for cid in dict.fromkeys(ids):
        try:
            card = cards.card_of(vault, cid)
        except cards.CardRefused as e:
            raise FindingRefused(str(e)) from None
        if cid in ruled_out:
            raise FindingRefused(RULED_OUT_MSG.format(cards._title(card)))
        out.append({"id": cid, "fp": agenda.card_fp(card["front"])})
    return out


def add(vault, text, how, entities, basis, turn_id, replaces=None):
    """回 (新條目, 被取代的條目或 None)。驗完才寫：結果與算法同口述的檢查（一句、200 字、不收密碼與四種字元）、
    實體登記且 active（客戶自己可以）、依據至少一張且都在清冊 active 有檔案卡、replaces 是有效的推算；
    replaces 那筆標 superseded、記 replaced_by。"""
    from kb._lib import statements
    text = statements.check_text(text, LABEL, FindingRefused)
    how = statements.check_text(how, LABEL, FindingRefused)
    names = _names(vault, list(entities or []))
    if not names:
        raise FindingRefused(NO_ENTITY.format(""))
    if not basis:
        raise FindingRefused(NO_BASIS)
    hooked = _basis(vault, list(basis))
    mounts_fp = _hash(_mount_ids(names, context(vault)))

    def fn(data):
        old = _need_active(data, replaces) if replaces else None
        it = {"id": _new_id(data), "text": text, "how": how, "entities": names, "basis": hooked, "mounts_fp": mounts_fp,
              "date": _today(), "turn": turn_id, "status": "active", "replaced_by": None}
        data["findings"].append(it)
        if old is not None:
            old["status"] = "superseded"
            old["replaced_by"] = it["id"]
        return (it, old), True
    return _mutate(vault, turn_id, fn)


def withdraw(vault, cid, turn_id):
    """客戶說這筆不用了：有效的推算改 withdrawn。回條目。"""
    def fn(data):
        it = _need_active(data, cid)
        it["status"] = "withdrawn"
        return it, True
    return _mutate(vault, turn_id, fn)
