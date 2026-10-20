"""日程 .system/agenda.json（schema 1，D-062、D-125～D-130）與渲染視圖 系統/日程.md（D-067：JSON 為真）。

一條一個日期或一段時間：id（a- 加 8 碼 hex）、kind（deadline｜meeting｜promise｜reminder｜period）、start（有就是一條 bar，D-126）、
date、ended（只有期間用）、time、text、entities（實體名稱，同檔案卡；rename／merge 由 entities 呼叫這裡一起換）、status（open｜done｜cancelled）、
source（D-041：interview｜inferred｜confirmed｜corrected）、origin（製卡抽出的才有：{card, fp}，fp 是那張檔案卡的 fp 前 12 碼，D-130）、
lead_days、created_turn、history（每筆 {date, change, by, turn}）；有 proposal 的是「原檔新版跟客戶說的不同，等客戶選」。
每次寫都在 agenda.json 的 FileLock 裡：先 turn.backup 再原子寫，寫完重渲染 md（先備份，內容沒變不寫）。
只經 kb agenda 與 entities 寫；今天的日期集中在 _today（測試換掉它），逾期、今天、接下來的算法只看 open（D-129）。
"""
import datetime as dt
import re
import secrets
from pathlib import Path

from kb._lib import config
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

AGENDA_POSIX = ".system/agenda.json"
MD_POSIX = "系統/日程.md"
SCHEMA_VERSION = 1
KINDS = {"到期": "deadline", "會議": "meeting", "承諾": "promise", "提醒": "reminder", "期間": "period"}
KIND_TEXT = {v: k for k, v in KINDS.items()}
TRACKED = ("deadline", "promise")  # 過了還 open 才算逾期（D-129）
PASSING = ("meeting", "reminder")  # 過了就算已過去
STATUSES = ("open", "done", "cancelled")
SOURCES = ("interview", "inferred", "confirmed", "corrected")
EDITABLE = ("date", "start", "time", "text", "entities", "lead_days")
STATUS_TEXT = {"done": "完成", "cancelled": "已取消"}
PAST_OPEN = "已過去"
WEEKDAYS = "一二三四五六日"
INFERRED_MARK = "〔推測〕"
NO_ITEMS = "（沒有）"
HEADER = ("# 日程\n"
          "這是什麼：要做的事與日期，程式從 .system/agenda.json 整理出來；〔推測〕是我從檔案讀到的，不對就跟我說。\n"
          "怎麼用：跟我說「9/25 下午有個會」「交屋改 12/10」「第二期款付了」就會改這裡。\n"
          "由程式產生，改了會被覆蓋。\n")
EMPTY_SENTENCE = "日程是空的；說「9/25 下午有個會」我就記下來。"
DATE_MSG = "日期要寫 YYYY-MM-DD，而且是真的日期"
TIME_MSG = "時間要寫 HH:MM"
KIND_MSG = "種類要是 到期、會議、承諾、提醒、期間 之一"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{2}:\d{2}")
_CHANGE_DATE = re.compile(r"date (\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2})")


class AgendaRefused(ValueError):
    """kb agenda 的拒絕：訊息就是給人看的那句。"""


def _today():
    return dt.date.today().isoformat()


def _path(vault):
    return Path(vault) / AGENDA_POSIX


# ---------- 讀 ----------

def empty():
    return {"schema_version": SCHEMA_VERSION, "items": []}


def load(vault):
    """沒檔回空的；壞 JSON、不是物件、items 不是清單、元素不是物件、版本比這版新丟 ValueError，訊息帶 .system/agenda.json。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{AGENDA_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict):
        raise ValueError(f"{AGENDA_POSIX}（不是物件）")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{AGENDA_POSIX}（沒有 items 清單）")
    if any(not isinstance(i, dict) for i in items):
        raise ValueError(f"{AGENDA_POSIX}（有一條不是物件）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{AGENDA_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "items": items}


def parse_date(text):
    """只收 YYYY-MM-DD，真的解析（2026-02-30 也錯，I-088）；回 datetime.date。"""
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        raise AgendaRefused(DATE_MSG)
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise AgendaRefused(DATE_MSG) from None


def parse_time(text):
    """只收 HH:MM（00:00～23:59）；回原字串。"""
    if not isinstance(text, str) or not _TIME.fullmatch(text):
        raise AgendaRefused(TIME_MSG)
    try:
        dt.datetime.strptime(text, "%H:%M")
    except ValueError:
        raise AgendaRefused(TIME_MSG) from None
    return text


def kind_of(kind):
    """中文或英文種類名 → 英文值；不認得丟 AgendaRefused。"""
    kind = KINDS.get(kind, kind)
    if kind not in KIND_TEXT:
        raise AgendaRefused(KIND_MSG)
    return kind


def _find(data, aid):
    it = next((i for i in data["items"] if i.get("id") == aid), None)
    if it is None:
        raise AgendaRefused(f"日程沒有 {aid}")
    return it


def get(vault, aid):
    return _find(load(vault), aid)


# ---------- 寫 ----------

def _mutate(vault, turn_id, fn):
    """鎖住讀改寫：fn(data) 回 (結果, 有沒有改)；有改才備份、寫、渲染。回結果。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        result, changed = fn(data)
        if changed:
            from kb._lib import turn
            turn.backup(vault, AGENDA_POSIX, turn_id)
            write_json_atomic(p, data)
            render(vault, turn_id, data)
    return result


def _new_id(data):
    taken = {i.get("id") for i in data["items"]}
    while True:
        new = "a-" + secrets.token_hex(4)
        if new not in taken:
            return new


def _log(it, change, by, turn_id):
    it.setdefault("history", []).append({"date": _today(), "change": change, "by": by, "turn": turn_id})


def _names(vault, names):
    """實體名稱 → 登記的名稱（別名、合併過的轉到目標，去重）；清單裡沒有的丟 AgendaRefused。"""
    from kb._lib import entities
    data = entities.load(vault)
    out = []
    for name in names:
        entry, _hit = entities.find(data, name)
        if entry is None:
            raise AgendaRefused(f"實體清單沒有「{name}」，先 kb entity add")
        if entry["name"] not in out:
            out.append(entry["name"])
    return out


def card_fp(front):
    fp = front.get("內容指紋") or front.get("指紋")
    return fp[:12] if isinstance(fp, str) else ""


def _card(vault, card_id, no_card="這份還沒有檔案卡，先寫卡再加日程"):
    """回 {"path","front"}：卡要在清冊、原檔 active、檔案卡寫好了；no_card 是沒有卡時的拒絕句（加日程與記抽過各有各的說法）。"""
    from kb._lib import cards, inventory
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == card_id), None)
    if entry is None:
        raise AgendaRefused(f"清冊沒有 {card_id}")
    if entry.get("status") != "active":
        raise AgendaRefused("這張卡的原檔已不在")
    group = cards._scan_groups(vault)[0].get(card_id)
    if not group:
        raise AgendaRefused(no_card)
    return group[0]


def _lead(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgendaRefused("提前天數要是 0 以上的整數")
    return value


def _text(value):
    text = " ".join(str(value or "").split())
    if not text:
        raise AgendaRefused("日程要有一句說明")
    return text


def add(vault, kind, date, text, entities, turn_id, start=None, time=None, card=None, source=None, lead_days=None):
    """回 (條目, 新建了嗎)。驗完才寫；card 有給 → 來源 inferred、origin 記那張卡與它現在的 fp，
    同一張卡已有同種類、同日期、同文字的 open 條目就不重複、回既有。"""
    kind = kind_of(kind)
    day = parse_date(date).isoformat()
    begin = parse_date(start).isoformat() if start else None
    clock = parse_time(time) if time else None
    if begin and begin > day:
        raise AgendaRefused("開始不能晚於日期")
    entities = list(entities or [])
    if kind == "period" and (not begin or len(entities) != 1):
        raise AgendaRefused("期間要有 --start，而且只掛一個實體")
    names = _names(vault, entities)
    text = _text(text)
    lead = _lead(lead_days)
    source = source or "confirmed"
    if source not in SOURCES:
        raise AgendaRefused(f"來源要是 {'、'.join(SOURCES)} 之一")

    def fn(data):
        origin = None
        if card:
            found = _card(vault, card)
            source_ = "inferred"
            origin = {"card": card, "fp": card_fp(found["front"])}
            same = next((i for i in data["items"] if i.get("status") == "open" and (i.get("origin") or {}).get("card") == card
                         and i.get("kind") == kind and i.get("date") == day and i.get("text") == text), None)
            if same is not None:
                return (same, False), False
        else:
            source_ = source
        it = {"id": _new_id(data), "kind": kind, "start": begin, "date": day, "ended": None, "time": clock, "text": text,
              "entities": names, "status": "open", "source": source_, "origin": origin, "lead_days": lead,
              "created_turn": turn_id, "history": []}
        data["items"].append(it)
        return (it, True), True
    return _mutate(vault, turn_id, fn)


def show(value):
    if isinstance(value, list):
        return "、".join(value)
    return "" if value is None else str(value)


def edit(vault, aid, turn_id, by="ai", card=None, **fields):
    """改 date、start、time、text、entities、lead_days（None 當沒給）。來源是 inferred 而這次不是帶 card 的重對 → corrected；
    card 給了（只能是出處那張卡）= 照新版重對：來源不變、origin.fp 更新。有 proposal 一律清掉。回條目。"""
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"edit 不收這些欄位：{sorted(unknown)}")
    new = {k: v for k, v in fields.items() if v is not None}
    if "date" in new:
        new["date"] = parse_date(new["date"]).isoformat()
    if "start" in new:
        new["start"] = parse_date(new["start"]).isoformat()
    if "time" in new:
        new["time"] = parse_time(new["time"])
    if "text" in new:
        new["text"] = _text(new["text"])
    if "lead_days" in new:
        new["lead_days"] = _lead(new["lead_days"])
    if "entities" in new:
        new["entities"] = _names(vault, new["entities"])
        if not new["entities"]:
            del new["entities"]

    def fn(data):
        it = _find(data, aid)
        fp = None
        if card:
            if (it.get("origin") or {}).get("card") != card:
                raise AgendaRefused("這條不是從這張卡抽的")
            fp = card_fp(_card(vault, card)["front"])
        begin, day = new.get("start", it.get("start")), new.get("date", it.get("date"))
        if begin and begin > day:
            raise AgendaRefused("開始不能晚於日期")
        changes = []
        for key in EDITABLE:
            if key in new and new[key] != it.get(key):
                changes.append(f"{key} {show(it.get(key))} → {show(new[key])}")
                it[key] = new[key]
        dirty = bool(changes) or "proposal" in it
        if changes:
            if it.get("source") == "inferred" and not card:
                it["source"] = "corrected"
            _log(it, "；".join(changes), by, turn_id)
        if fp is not None and it["origin"].get("fp") != fp:
            it["origin"]["fp"] = fp
            dirty = True
        it.pop("proposal", None)
        return it, dirty
    return _mutate(vault, turn_id, fn)


def set_status(vault, aid, status, turn_id, by="ai"):
    if status not in STATUSES:
        raise ValueError(f"狀態要是 {'、'.join(STATUSES)} 之一：{status!r}")

    def fn(data):
        it = _find(data, aid)
        old = it.get("status")
        if old == status:
            return it, False
        it["status"] = status
        change = f"status {old} → {status}"
        if it.get("kind") == "period":
            if status == "open":
                it["ended"] = None
            elif status == "done" and not it.get("ended"):  # 甘特圖與過去區照 ended 畫（確認後裁定 7b）
                it["ended"] = _today()
                change = f"ended {it['ended']}；{change}"
        _log(it, change, by, turn_id)
        return it, True
    return _mutate(vault, turn_id, fn)


def recheck(vault, aid, turn_id, propose_date=None, propose_text=None, by="ai"):
    """製卡者看過新版：沒帶提議 → 只把 origin.fp 換成那張卡現在的 fp（這條不用改）；帶提議 → 只收來源 confirmed 或 corrected 的，
    記在 proposal 等客戶選。回條目。"""
    if propose_date is not None:
        propose_date = parse_date(propose_date).isoformat()

    def fn(data):
        it = _find(data, aid)
        origin = it.get("origin")
        if not origin:
            raise AgendaRefused("這條不是從檔案卡抽的，沒有可以對的版本")
        want = (propose_date and propose_date != it.get("date")) or (propose_text and _text(propose_text) != it.get("text"))
        if want and it.get("source") == "inferred":
            raise AgendaRefused("推測的條目直接 edit，不用提議")
        fp = card_fp(_card(vault, origin["card"])["front"])
        had = "proposal" in it
        it.pop("proposal", None)
        if want:
            it["proposal"] = {"date": propose_date or it["date"], "text": _text(propose_text) if propose_text else it["text"],
                              "card": origin["card"], "fp": fp}
        dirty = had or bool(want) or origin.get("fp") != fp
        origin["fp"] = fp
        return it, dirty
    return _mutate(vault, turn_id, fn)


def cancel_from_card(vault, card_id, turn_id, by="program"):
    """cards drop 拿掉檔案卡時呼叫：出處是它、來源 inferred、還是 open 的條目改 cancelled；回動到的條目。沒有檔就什麼都不寫。"""
    if not _path(vault).exists():
        return []

    def fn(data):
        hits = [i for i in data["items"] if i.get("status") == "open" and i.get("source") == "inferred"
                and (i.get("origin") or {}).get("card") == card_id]
        for it in hits:
            it["status"] = "cancelled"
            _log(it, "status open → cancelled（原檔已刪）", by, turn_id)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)


def rename_entity(vault, mapping, turn_id, undo=None):
    """entities.rename／merge 呼叫：entities 清單裡 key 在 mapping 的名稱換成新名（去重，順序照原樣）。沒有 agenda.json 什麼都不做。
    undo 是 entities._Undo：寫之前先記 agenda.json 與 md 的舊內容，中途出錯一起還原。回動到的條目。"""
    from kb._lib import entities
    if not _path(vault).exists():
        return []

    def fn(data):
        hits = []
        for it in data["items"]:
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
            undo.file(AGENDA_POSIX)
            undo.file(MD_POSIX)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)


def close_periods(vault, entity_name, date, turn_id, by="program"):
    """entities.set_progress 結案時呼叫：該實體 open 的期間條目填 ended、status done。回動到的條目。沒有檔就什麼都不寫。"""
    from kb._lib import entities
    if not _path(vault).exists():
        return []

    def fn(data):
        hits = [i for i in data["items"] if i.get("kind") == "period" and i.get("status") == "open"
                and any(entities.key(n) == entities.key(entity_name) for n in i.get("entities") or [])]
        for it in hits:
            it["ended"], it["status"] = date, "done"
            _log(it, f"ended {date}；status open → done", by, turn_id)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)


def refresh_md(vault, turn_id=None):
    """實體狀態變了（擱置段在甘特圖上）：有 agenda.json 才重渲染 md，沒有就不建檔。"""
    if _path(vault).exists():
        render(vault, turn_id)


# ---------- 給 cards.todo、開場、plan ----------

def buckets(vault, data):
    """agenda_recheck：open、有出處的條目，出處卡現在的 fp 不等於 origin.fp（D-130）；每項帶條目、卡的路徑、來源。
    卡不見（原檔還在但卡被刪、或沒有卡）的不列。"""
    from kb._lib import cards
    rows = [i for i in data["items"] if i.get("status") == "open" and isinstance(i.get("origin"), dict)]
    out = []
    if not rows:
        return {"agenda_recheck": out}
    groups = cards._scan_groups(vault)[0]
    for it in rows:
        group = groups.get(it["origin"].get("card"))
        if not group or card_fp(group[0]["front"]) == it["origin"].get("fp"):
            continue
        out.append({"id": it["id"], "card": it["origin"]["card"], "card_path": group[0]["path"],
                    "kind": KIND_TEXT.get(it.get("kind"), it.get("kind")), "date": it.get("date"), "text": it.get("text"),
                    "source": it.get("source")})
    return {"agenda_recheck": out}


def unscanned(vault, data, groups=None, entries=None, skip=()):
    """agenda_unscanned（D-140）：active 的檔案卡、清冊的 agenda_fp 不等於卡現在的指紋（沒有也算）。每項 {"id": 卡編號,
    "card_path", "title", "existing"}，existing 是出處是這張卡、還 open 的條目 [{"id","kind","date","text"}]，
    製卡者看得出哪些已經在、只加沒有的。cards.todo 把掃好的 groups 與清冊條目帶進來，沒給才自己讀；skip 是檔頭有問題、
    等著重寫的卡編號（重寫前不抽）。"""
    from kb._lib import cards, inventory
    groups = cards._scan_groups(vault)[0] if groups is None else groups
    entries = inventory.load(vault)["entries"] if entries is None else entries
    by_id = {e.get("id"): e for e in entries}
    out = []
    for cid, group in sorted(groups.items(), key=lambda kv: kv[1][0]["path"]):
        entry, card = by_id.get(cid), group[0]
        fp = card_fp(card["front"])
        if entry is None or entry.get("status") != "active" or not fp or entry.get("agenda_fp") == fp or cid in skip:
            continue
        existing = [{"id": i["id"], "kind": KIND_TEXT.get(i.get("kind"), i.get("kind")), "date": i.get("date"), "text": i.get("text")}
                    for i in data["items"] if i.get("status") == "open" and (i.get("origin") or {}).get("card") == cid]
        title = card["front"].get("標題")
        out.append({"id": cid, "card_path": card["path"], "title": title if isinstance(title, str) else "", "existing": existing})
    return {"agenda_unscanned": out}


def mark_scanned(vault, card_id, turn_id):
    """kb agenda scanned：製卡者對這張卡抽完日期（含沒抽到任何日期），清冊記這張卡現在的指紋。
    回 {"id","title","fp","changed"}；清冊沒有、原檔不在、還沒有檔案卡丟 AgendaRefused。"""
    from kb._lib import inventory
    card = _card(vault, card_id, no_card="這份還沒有檔案卡，先寫卡再抽日期")
    fp = card_fp(card["front"])
    changed = inventory.set_agenda_fp(vault, card_id, fp, turn_id)
    title = card["front"].get("標題")
    return {"id": card_id, "title": title if isinstance(title, str) else "", "fp": fp, "changed": changed}


def _window(vault, it):
    lead = it.get("lead_days")
    return lead if isinstance(lead, int) and not isinstance(lead, bool) else config.get(vault, "agenda_window_days")


def _day(it):
    try:
        return dt.date.fromisoformat(it.get("date"))
    except (TypeError, ValueError):
        return None


def counts(vault, data, today=None):
    """{"overdue","today","upcoming"}：只看 open 的非期間條目（D-129）。overdue 是 TRACKED 日期已過；today 是日期等於今天；
    upcoming 是今天之後 N 天內（N 是條目的 lead_days，沒有就 agenda_window_days）。會議與提醒過了不算逾期。"""
    now = dt.date.fromisoformat(today or _today())
    out = {"overdue": 0, "today": 0, "upcoming": 0}
    for it in data["items"]:
        d = _day(it)
        if it.get("status") != "open" or it.get("kind") == "period" or d is None:
            continue
        if d < now:
            if it.get("kind") in TRACKED:
                out["overdue"] += 1
        elif d == now:
            out["today"] += 1
        elif d <= now + dt.timedelta(days=_window(vault, it)):
            out["upcoming"] += 1
    return out


def overdue_items(data, today=None):
    """open、TRACKED、日期已過的條目，照日期排（plan 的 overdue 題用）。"""
    now = today or _today()
    rows = [i for i in data["items"] if i.get("status") == "open" and i.get("kind") in TRACKED
            and _day(i) is not None and i["date"] < now]
    return sorted(rows, key=lambda i: (i["date"], i.get("time") or "", i["id"]))


def proposal_items(data):
    """open 而且有 proposal 的條目（plan 的 agenda_conflict 題用）。"""
    return [i for i in data["items"] if i.get("status") == "open" and isinstance(i.get("proposal"), dict)]


def with_weekday(day):
    """YYYY-MM-DD → 「YYYY-MM-DD（週X）」，星期由程式算（成功句與日程.md 共用，AI 不自己推）。"""
    return f"{day}（週{WEEKDAYS[dt.date.fromisoformat(day).weekday()]}）"


def md_of(day):
    """YYYY-MM-DD → M/D（不補零）。"""
    d = dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}"


TELL_LISTED = 5


def current_turn(vault):
    """目前回合代號；只讀 current.json，沒有就回 None（plan.build 是只讀的，不能為了問回合開新的）。"""
    from kb._lib import turn
    try:
        data = turn._read_turn(turn._current_file(vault))
    except OSError:
        return None
    return data["turn"] if data else None


def tell_lines(vault, turn_id=None):
    """回答末尾要說的兩句（D-128、D-130）：這一回合製卡加進日程的條目、這一回合照新版改了日期的推測條目。讀不到就沒有。"""
    turn_id = turn_id or current_turn(vault)
    if not turn_id:
        return []
    try:
        items = load(vault)["items"]
    except ValueError:
        return []
    lines = []
    added = sorted((i for i in items if i.get("created_turn") == turn_id and i.get("origin") and i.get("status") == "open"),
                   key=lambda i: (i.get("date", ""), i.get("text", "")))
    if added:
        if len(added) > TELL_LISTED:
            lines.append(f"加進日程 {len(added)} 條，看 {MD_POSIX}")
        else:
            lines.append("加進日程：" + "、".join(f"{_span(i)} {i['text']}" for i in added))
    lines.extend(_reworked_lines(vault, items, turn_id))
    return lines


def _span(it):
    if it.get("kind") == "period" and it.get("start"):
        return f"{md_of(it['start'])}～{md_of(it['date'])}"
    return md_of(it["date"])


def _reworked_lines(vault, items, turn_id):
    out, titles = [], None
    for it in items:
        if it.get("source") != "inferred" or it.get("created_turn") == turn_id:
            continue
        for h in it.get("history") or []:
            m = _CHANGE_DATE.search(h.get("change") or "") if h.get("turn") == turn_id else None
            if m:
                titles = card_titles(vault) if titles is None else titles
                title = titles.get((it.get("origin") or {}).get("card"))
                out.append(f"{it['text']}從 {md_of(m.group(1))} 改成 {md_of(m.group(2))} 了" + (f"（{title}）" if title else ""))
                break
    return out


def card_titles(vault):
    """{檔案卡編號: 標題}，掃一次檔案索引/；掃不動就空的（講不出標題就不講，不擋告知與提問）。"""
    try:
        from kb._lib import cards
        return {cid: g[0]["front"]["標題"] for cid, g in cards._scan_groups(vault)[0].items()
                if isinstance(g[0]["front"].get("標題"), str) and g[0]["front"]["標題"].strip()}
    except Exception:  # noqa: BLE001
        return {}


# ---------- 渲染 ----------

def _entities_suffix(it):
    names = it.get("entities") or []
    return f"（{'、'.join(names)}）" if names else ""


def _line(it):
    d = dt.date.fromisoformat(it["date"])
    when = with_weekday(it["date"]) + (f"{it['time']} " if it.get("time") else "")
    mark = INFERRED_MARK if it.get("source") == "inferred" else ""
    return f"- {when}{KIND_TEXT.get(it.get('kind'), it.get('kind'))} {it.get('text')}{_entities_suffix(it)}{mark}"


def _order(it):
    return (it.get("date", ""), it.get("time") or "", it.get("text", ""), it.get("id", ""))


def _past_row(it, now, cutoff):
    """過去區：(排序用日期, 句子) 或 None。完成與取消只看日期不早於 cutoff；open 的只有會議與提醒、日期已過才算「已過去」。"""
    status = it.get("status")
    ref = it.get("ended") if it.get("kind") == "period" and status == "done" and it.get("ended") else it.get("date")
    if not isinstance(ref, str) or not _DATE.fullmatch(ref) or ref < cutoff:
        return None
    if status in STATUS_TEXT:
        label = STATUS_TEXT[status]
    elif status == "open" and it.get("kind") in PASSING and ref < now:
        label = PAST_OPEN
    else:
        return None
    return ref, f"- {ref} {KIND_TEXT.get(it.get('kind'), it.get('kind'))} {it.get('text')}{_entities_suffix(it)}{label}"


def _chart_text(text):
    text = " ".join(str(text).split())
    return text.replace(":", "：").replace(";", "；").replace("#", "＃")


def _pauses(histories, name, now):
    """某個實體 progress_history 裡每段擱置 [(開始, 結束)]：到下一筆歷程的日期為止，還在擱置就到今天。"""
    hist = sorted(histories.get(name) or [], key=lambda h: str(h.get("date")))
    out = []
    for i, h in enumerate(hist):
        if h.get("progress") != "paused" or not isinstance(h.get("date"), str):
            continue
        end = hist[i + 1].get("date") if i + 1 < len(hist) else now
        out.append((h["date"], max(end, h["date"])))
    return out


def _histories(vault):
    from kb._lib import entities, ledger
    try:
        return {e.get("name"): e.get("progress_history") or [] for e in entities.load(vault)["entities"]
                if e.get("status") == "active" and e.get("progress_history")}
    except ValueError as e:
        ledger.log_error(vault, f"日程甘特圖讀不到實體清單：{e}", source="agenda")
        return {}


def _gantt(vault, items, now, cutoff):
    sections = {}  # 實體名稱或 None → {"bars": [], "marks": []}

    def slot(it):
        names = it.get("entities") or []
        return sections.setdefault(names[0] if names else None, {"bars": [], "marks": []})
    for it in items:
        status, kind, d = it.get("status"), it.get("kind"), it.get("date")
        if status == "cancelled" or not isinstance(d, str):
            continue
        text = _chart_text(it.get("text"))
        if kind == "period":
            start = it.get("start")
            if not start or (status == "done" and (it.get("ended") or d) < cutoff):
                continue
            if status == "done":
                rows = [(start, f"{text} :done, {start}, {it.get('ended') or d}")]
            else:
                rows = [(start, f"{text} :active, {start}, {d}")]
                if d < now:
                    rows.append((start, f"逾預計結束 :crit, {d}, {now}"))
            slot(it)["bars"].append((start, d, rows))
            continue
        if status == "open" and kind in PASSING and d < cutoff:
            continue
        if status == "done" and d < cutoff:
            continue
        tag = "crit, " if status == "open" and kind in TRACKED and d < now else ""
        slot(it)["marks"].append((_order(it), f"{text} :{tag}milestone, {d}, 0d"))
    if not sections:
        return []
    histories = _histories(vault)
    lines = ["```mermaid", "gantt", "  dateFormat YYYY-MM-DD", "  axisFormat %m/%d"]
    for name in sorted((n for n in sections if n is not None), key=str) + ([None] if None in sections else []):
        sec = sections[name]
        lines.append(f"  section {_chart_text(name) if name else '其他'}")
        for _start, _end, rows in sorted(sec["bars"], key=lambda b: (b[0], b[1])):
            lines.extend(f"  {row}" for _s, row in rows)
        if sec["bars"] and name is not None:
            for begin, end in _pauses(histories, name, now):
                lines.append(f"  擱置 :done, {begin}, {end if end > begin else '1d'}")
        lines.extend(f"  {row}" for _o, row in sorted(sec["marks"], key=lambda m: m[0]))
    lines.append("```")
    return lines


def render_text(vault, data, today=None):
    now = today or _today()
    items = data["items"]
    if not items:
        return HEADER + f"\n{EMPTY_SENTENCE}\n"
    past_days = config.get(vault, "agenda_past_days")
    window = config.get(vault, "agenda_window_days")
    cutoff = (dt.date.fromisoformat(now) - dt.timedelta(days=past_days)).isoformat()
    live = sorted((i for i in items if i.get("status") == "open" and i.get("kind") != "period" and _day(i) is not None),
                  key=_order)
    overdue, today_, soon, later = [], [], [], []
    for it in live:
        d = _day(it)
        if it["date"] < now:
            if it.get("kind") in TRACKED:
                overdue.append(it)
        elif it["date"] == now:
            today_.append(it)
        elif d <= dt.date.fromisoformat(now) + dt.timedelta(days=_window(vault, it)):
            soon.append(it)
        else:
            later.append(it)
    past = sorted((r for r in (_past_row(i, now, cutoff) for i in items) if r), key=lambda r: r[0], reverse=True)

    def block(title, lines):
        return f"## {title}\n" + "\n".join(lines or [NO_ITEMS])
    blocks = [block("逾期", [_line(i) for i in overdue]), block("今天", [_line(i) for i in today_]),
              block(f"接下來 {window} 天", [_line(i) for i in soon]), block("之後", [_line(i) for i in later])]
    chart = _gantt(vault, items, now, cutoff)
    if chart:
        blocks.append("## 時程圖\n" + "\n".join(chart))
    blocks.append(block(f"過去 {past_days} 天", [line for _d, line in past]))
    return HEADER + "\n" + "\n\n".join(blocks) + "\n"


def render(vault, turn_id=None, data=None, today=None):
    """寫 系統/日程.md；內容沒變不寫；有 turn_id 先備進回合。回有沒有寫。"""
    data = data if data is not None else load(vault)
    text = render_text(vault, data, today)
    target = Path(vault) / MD_POSIX
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, MD_POSIX, turn_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    return True
