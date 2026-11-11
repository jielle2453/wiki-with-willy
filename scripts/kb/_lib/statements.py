"""口述清單 .system/statements.json（schema 1，D-146；note 改知識第 1 點）。

一條一句客戶在對話裡說的事實或決定：id（s- 加 8 碼 hex）、text、entities（登記的實體名稱，客戶自己也可以；
rename／merge 由 entities 呼叫這裡一起換，移除時照留）、date、turn、status（active｜superseded｜withdrawn）、
replaced_by（被哪一條取代）。被取代、收回的條目留著（綜合卡紀事會引用它）；沒有綜合卡引用的由健檢移到
.system/statements-archive.json（schema 1，同樣的條目加 archived 日期，lint 題）。收回取代過別條的那條時回傳被取代的（D-224），
她說要恢復就 restore（被取代、而取代它的那條已收回才收；在封存檔裡的搬回來）。
綜合卡的「整理到」收 s- 行（指紋欄寫 -），出處寫〔s-…〕，所以有新口述或口述被取代時，程式把那個實體列進待更新綜合卡。
不渲染 md（工作日誌已記）。每次寫都在 statements.json 的 FileLock 裡：先 turn.backup 再原子寫（D-074）；只經 kb statement
與 entities 寫。
"""
import secrets
from datetime import date
from pathlib import Path

from kb._lib import profile
from kb._lib.io import FileLock, read_json, write_json_atomic

STATEMENTS_POSIX = ".system/statements.json"
ARCHIVE_POSIX = ".system/statements-archive.json"
SCHEMA_VERSION = 1
STATUSES = ("active", "superseded", "withdrawn")
TEXT_MAX = 200
BAD_CHARS = '"$`\\'  # 一句口述會被貼進雙引號的指令（同 plan.FREE_TEXT_RULE）
TEXT_MSG = f"{{}}要一句話，最多 {TEXT_MAX} 字"
CHARS_MSG = "{}裡不能有 \" $ ` \\ 這四種字元"
SECRET_MSG = "{}裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員"  # 密碼存憑證管理員（密碼與守門 D-180）；偏好同一套（測試第一輪後裁定 3）
NOT_FOUND = "沒有這條口述：{}"
SUPERSEDED_MSG = "這條已經被取代了，改最新的那條"
WITHDRAWN_MSG = "這條已經收回了"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"
NOT_RESTORABLE = "這條沒有被取代，或取代它的那條還有效"


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


def check_text(text, label="口述", refused=StatementRefused):
    """一句（不換行、不空）、最多 TEXT_MAX 字、不含密碼（profile.preference_problem 同一套）與 BAD_CHARS；回壓過空白的字串，
    不行丟 refused（拒絕句的開頭是 label；裁示原話也走這裡，測試第一輪後裁定 3、4）。"""
    raw = str(text or "")
    if "\n" in raw or "\r" in raw:
        raise refused(TEXT_MSG.format(label))
    text = " ".join(raw.split())
    if not text or len(text) > TEXT_MAX:
        raise refused(TEXT_MSG.format(label))
    why = profile.preference_problem(text)
    if why == profile.PREFERENCE_SECRET:
        raise refused(SECRET_MSG.format(label))
    if why or any(c in text for c in BAD_CHARS):
        raise refused(CHARS_MSG.format(label))
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


MONEY_MSG = "--money 要寫 收 或 付 加金額，例：--money 收 5000"


MONEY_DIRS = {"收": "in", "付": "out", "in": "in", "out": "out"}


def money_mark(direction, amount):
    """錢的話的內部記號（D-343）：{"dir","amount","ledger": None}；方向要收或付、金額看得懂，不然丟 StatementRefused。"""
    from kb._lib import numbers
    d = MONEY_DIRS.get(str(direction or "").strip())
    if d is None:
        raise StatementRefused(MONEY_MSG)
    try:
        value = numbers.plain(numbers.parse_number(str(amount).strip(), negative=False))
    except numbers.NumberRefused as e:
        raise StatementRefused(str(e)) from None
    return {"dir": d, "amount": value, "ledger": None}


def add(vault, text, entities, turn_id, replaces=None, money=None):
    """回 (新條目, 被取代的條目或 None)。驗完才寫：一句、字元、實體都登記且 active、replaces 是有效的口述；
    replaces 那條標 superseded、記 replaced_by。money 是 money_mark 的結果（帳關著時錢的話，D-343）：帶它的可以不掛實體
    （使用者 11-09，只放寬這一種）。"""
    text = check_text(text)
    names = names_of(vault, list(entities or []))
    if not names and money is None:
        raise StatementRefused(NO_ENTITY.format(""))
    from kb._lib import people
    who = people.me(vault)

    def fn(data):
        old = _need_active(data, replaces) if replaces else None
        it = {"id": _new_id(data), "text": text, "entities": names, "date": _today(), "turn": turn_id,
              "status": "active", "replaced_by": None}
        if who:
            it["who"] = who  # 多人共用 2：誰說的（單人不寫，升級前的也沒有）
        if money is not None:
            it["money"] = dict(money)  # 內部記號：不顯示在卡、日程頁、首頁（D-343）
        data["statements"].append(it)
        if old is not None:
            old["status"] = "superseded"
            old["replaced_by"] = it["id"]
        return (it, old), True
    got = _mutate(vault, turn_id, fn)
    wake_entities(vault, names, turn_id)
    return got


def set_ledger(vault, sid, mid, turn_id):
    """改開時補進帳（D-343）：口述的錢記號補上 ledger（帳那筆的編號）。沒有記號什麼都不做。回條目。"""
    def fn(data):
        it = find(data, sid)
        if it is None:
            raise StatementRefused(NOT_FOUND.format(sid))
        mark = it.get("money")
        if not isinstance(mark, dict) or mark.get("ledger") == mid:
            return it, False
        it["money"] = dict(mark, ledger=mid)
        return it, True
    return _mutate(vault, turn_id, fn)


def said_by(s, multi):
    """出處文字（多人共用 2 新定 4）：單人「你 11/01 說的」；多人「小王 11/01 說的」，升級前沒有名字的只寫日期「11/01 說的」。"""
    day = str(s.get("date") or "")
    md = f"{day[5:7]}/{day[8:10]}" if len(day) >= 10 else day
    who = s.get("who") if multi else "你"
    return f"{who} {md} 說的" if who else f"{md} 說的"


def wake_entities(vault, names, turn_id):
    """實體有新口述或新裁示：跟它有關的先放著回到前面（D-226）；出錯只記 log。"""
    try:
        from kb._lib import entities, pending
        ent = entities.load(vault)
        ids = [e["id"] for e in (entities.find(ent, n)[0] for n in names) if e is not None]
        pending.wake(vault, entity_ids=ids, turn_id=turn_id)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"先放著沒拿回來：{type(e).__name__}: {e}", source="statements")


def withdraw(vault, sid, turn_id):
    """客戶說「沒這回事」：有效的口述改 withdrawn。回條目。"""
    def fn(data):
        it = _need_active(data, sid)
        it["status"] = "withdrawn"
        return it, True
    return _mutate(vault, turn_id, fn)


def replaced_of(vault, sid):
    """被 sid 取代的那條（statements.json 或封存檔裡 replaced_by 是 sid 的），沒有回 None（D-224）。"""
    for data in (load(vault), load_archive(vault)):
        hit = next((s for s in data["statements"] if s.get("replaced_by") == sid), None)
        if hit is not None:
            return hit
    return None


def restore(vault, sid, turn_id):
    """她說原本那條要恢復（D-224）：只收被取代、而取代它的那條已收回的；改回有效、replaced_by 清掉，在封存檔裡的搬回來
    （兩檔都先備進回合）。先寫清單、再從封存檔拿掉（B-454）：中間出錯兩邊都有那條，不會兩邊都沒有。
    不行丟 StatementRefused(NOT_RESTORABLE)。回條目。綜合卡照 D-146 自己會列待更新（依據變了）。"""
    archived = []

    def fn(data):
        it = find(data, sid)
        if it is None:
            it = find(load_archive(vault), sid)
            if it is not None:
                archived.append(sid)
        if it is None or it.get("status") != "superseded":
            raise StatementRefused(NOT_RESTORABLE)
        newer = find(data, it.get("replaced_by")) or find(load_archive(vault), it.get("replaced_by"))
        if newer is None or newer.get("status") != "withdrawn":
            raise StatementRefused(NOT_RESTORABLE)
        if archived:
            it = {k: v for k, v in it.items() if k != "archived"}
            data["statements"].append(it)
        it["status"], it["replaced_by"] = "active", None
        return it, True
    got = _mutate(vault, turn_id, fn)
    if archived:
        try:
            _drop_archived(vault, sid, turn_id)
        except Exception as e:  # noqa: BLE001  清單已經改好：封存檔那條留著（重複）只記 log，不讓指令報錯
            from kb._lib import ledger
            ledger.log_error(vault, f"恢復後封存檔沒拿掉 {sid}：{type(e).__name__}: {e}", source="statements")
    return got


def _drop_archived(vault, sid, turn_id):
    """封存檔拿掉那條（restore 寫好清單之後）；先備進回合。跟 archive() 一樣拿 statements.json 的鎖，兩邊不會互相蓋掉。"""
    from kb._lib import turn
    p = Path(vault) / ARCHIVE_POSIX
    with FileLock(_path(vault)):
        arch = load_archive(vault)
        turn.backup(vault, ARCHIVE_POSIX, turn_id)
        arch["statements"] = [x for x in arch["statements"] if x.get("id") != sid]
        write_json_atomic(p, arch)


# ---------- 封存（健檢，lint 題） ----------

def load_archive(vault):
    """封存檔；沒檔回空的，壞了丟 ValueError（帶檔名）。"""
    try:
        data = read_json(Path(vault) / ARCHIVE_POSIX)
    except ValueError as e:
        raise ValueError(f"{ARCHIVE_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict) or not isinstance(data.get("statements"), list):
        raise ValueError(f"{ARCHIVE_POSIX}（沒有 statements 清單）")
    return {"schema_version": SCHEMA_VERSION, "statements": [s for s in data["statements"] if isinstance(s, dict)]}


def archive(vault, ids, turn_id):
    """把這幾條（只收被取代或收回的）移到封存檔、加 archived 日期；兩檔先備進回合。回搬了幾條。"""
    ids = set(ids)

    def fn(data):
        moving = [s for s in data["statements"] if s.get("id") in ids and s.get("status") in ("superseded", "withdrawn")]
        if not moving:
            return 0, False
        from kb._lib import turn
        arch = load_archive(vault)
        turn.backup(vault, ARCHIVE_POSIX, turn_id)
        arch["statements"].extend(dict(s, archived=_today()) for s in moving)
        write_json_atomic(Path(vault) / ARCHIVE_POSIX, arch)
        data["statements"] = [s for s in data["statements"] if s not in moving]
        return len(moving), True
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
    got = _mutate(vault, turn_id, fn)
    _rename_archive(vault, mapping, turn_id, undo)
    return got


def _rename_archive(vault, mapping, turn_id, undo):
    """封存檔裡的條目也換名（之後恢復回來才對得上現在的名稱，測試 T-6）。沒有封存檔什麼都不做。"""
    from kb._lib import entities
    p = Path(vault) / ARCHIVE_POSIX
    if not p.exists():
        return
    with FileLock(p):
        arch = load_archive(vault)
        hit = False
        for it in arch["statements"]:
            names = it.get("entities") or []
            if not any(entities.key(n) in mapping for n in names):
                continue
            new = []
            for n in names:
                n = mapping.get(entities.key(n), n)
                if all(entities.key(x) != entities.key(n) for x in new):
                    new.append(n)
            it["entities"] = new
            hit = True
        if hit:
            if undo is not None:
                undo.file(ARCHIVE_POSIX)
            from kb._lib import turn
            turn.backup(vault, ARCHIVE_POSIX, turn_id)
            write_json_atomic(p, arch)


# ---------- 加總（#65：原記帳的 sum 帳關著那條，D-343） ----------

def _in_range(day, frm, to):
    return bool(day) and (frm is None or day >= frm) and (to is None or day <= to)


def marked_statements(vault, frm=None, to=None, entity=None):
    """標過錢的口述（D-343）：active、money 是物件；照口述的日期篩，entity 只留掛了它的。"""
    from kb._lib import entities
    k = None
    if entity:
        e, _hit = entities.find(entities.load(vault), str(entity).strip())
        if e is None or e.get("status") != "active":
            raise StatementRefused(NO_ENTITY.format(str(entity).strip()))
        k = entities.key(e["name"])
    out = []
    for s in load(vault)["statements"]:
        if s.get("status") != "active" or not isinstance(s.get("money"), dict) or not _in_range(s.get("date"), frm, to):
            continue
        if k and not any(entities.key(n) == k for n in s.get("entities") or []):
            continue
        out.append(s)
    return out


def _diff(a, b):
    from decimal import Decimal
    from kb._lib import numbers
    out = {}
    for k in ("in", "out", "net"):
        x, y = Decimal(str(a[k])), Decimal(str(b[k]))
        out[k] = {"diff": numbers.plain(x - y), "pct": None if y == 0 else round(float((x - y) / abs(y) * 100), 1)}
    return out


def statements_summary(vault, frm, to, entity=None, vs=None):
    """kb statement sum：區間內標過錢的口述，收、付、淨與逐筆；vs=(frm, to) 另算一段給差與百分比。"""
    from decimal import Decimal
    from kb._lib import numbers

    def sums(a, b):
        tot = {"in": Decimal(0), "out": Decimal(0)}
        rows = []
        for s in marked_statements(vault, a, b, entity):
            d = s["money"].get("dir")
            if d in tot:
                tot[d] += Decimal(str(s["money"].get("amount") or 0))
                rows.append({"id": s["id"], "text": s.get("text"), "date": s.get("date"), "dir": d,
                             "amount": s["money"].get("amount"), "entities": list(s.get("entities") or [])})
        return {"in": numbers.plain(tot["in"]), "out": numbers.plain(tot["out"]),
                "net": numbers.plain(tot["in"] - tot["out"])}, rows
    got, rows = sums(frm, to)
    out = {"from": frm, "to": to, **got, "vs": None, "diff": None, "items": rows}
    if vs:
        other, _rows = sums(vs[0], vs[1])
        out["vs"] = {"from": vs[0], "to": vs[1], **other}
        out["diff"] = _diff(got, other)
    return out
