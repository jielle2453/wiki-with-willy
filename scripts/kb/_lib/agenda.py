"""日程 .system/agenda.json（schema 1，D-062、D-125～D-130）與渲染視圖 系統/日程.md（D-067：JSON 為真）。

一條一個日期或一段時間：id（a- 加 8 碼 hex）、kind（deadline｜meeting｜reminder｜period；promise 是 #68 前的舊值，寫入時轉成 deadline）、start（有就是一條 bar，D-126）、
date、ended（只有期間用）、time、text、entities（實體名稱，同檔案卡；rename／merge 由 entities 呼叫這裡一起換）、status（open｜done｜cancelled）、
source（D-041：interview｜inferred｜confirmed｜corrected）、origin（製卡抽出的才有：{card, fp}，fp 是那張檔案卡的 fp 前 12 碼，D-130）、
lead_days、created_turn、history（每筆 {date, change, by, turn}）；有 proposal 的是「原檔新版跟客戶說的不同，等客戶選」。
每次寫都在 agenda.json 的 FileLock 裡：先 turn.backup 再原子寫，寫完重渲染 md（先備份，內容沒變不寫）。
重渲染前比對 日程.md 有沒有被手改（_lib/renders，D-223）：改了條目就存她那份、待處理開一條 handedit；比對用改之前的資料
與上次寫的那天重算她看到的那份。
只經 kb agenda 與 entities 寫；今天的日期集中在 _today（測試換掉它），逾期、今天、接下來的算法只看 open（D-129）。
日程匯出（D-500）：寫日程或實體的路徑放開鎖之後 export（_mutate、entities 存檔後；render 不叫，T-1）：設定了
agenda_export_dir 就在匯出自己的鎖裡重讀 agenda.json 與實體，把 willy-agenda/1 寫到 <夾>/willy-agenda.json（T-3；
exported_at 以外都一樣就不寫）；失敗不擋日程，寫 hook-errors.log（source agenda_export），每次結果記在 .system/agenda_export.json。
重複（D-501）：repeat {every year|month|week, from 錨點}，一條永遠代表「下一次」。會議、提醒過了就移到下一次（load 在記憶體移，
_mutate 寫回並記歷程）；到期、期間要 done 才移、狀態維持 open。改日期錨點跟著換。
#65 拿掉記帳：日程頁不再列帳的到期件，日程不記錢。實體名字寫成連到卡實際位置的連結（linker）。
"""
import calendar
import copy
import datetime as dt
import hashlib
import json
import re
import secrets
import unicodedata
from pathlib import Path

from kb._lib import config
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

AGENDA_POSIX = ".system/agenda.json"
MD_POSIX = "系統/日程.md"
SCHEMA_VERSION = 1
KINDS = {"到期": "deadline", "會議": "meeting", "提醒": "reminder", "期間": "period"}
LEGACY_KINDS = {"promise": "deadline"}  # #68 拿掉的種類 → 現在的；舊資料讀到照它看（D-547）
KIND_TEXT = {v: k for k, v in KINDS.items()}
KIND_TEXT.update({old: KIND_TEXT[new] for old, new in LEGACY_KINDS.items()})
TRACKED = ("deadline", "promise")  # 過了還 open 才算逾期（D-129）；promise 是還沒轉的舊值，照到期追
PASSING = ("meeting", "reminder")  # 過了就算已過去
STATUSES = ("open", "done", "cancelled")
SOURCES = ("interview", "inferred", "confirmed", "corrected")
EDITABLE = ("date", "start", "time", "text", "entities", "lead_days", "repeat", "whose", "owner")
REPEATS = {"每年": "year", "每月": "month", "每週": "week"}
REPEAT_TEXT = {v: k for k, v in REPEATS.items()}
NO_REPEAT = "不重複"
REPEAT_MSG = "重複要是 每年、每月、每週、不重複 之一"
CONFIRM_CHANGE = "source inferred → confirmed"  # 推測的那條她說對（kb agenda edit 不帶欄位，lint 桌面版確認後）
STATUS_TEXT = {"done": "完成", "cancelled": "已取消"}
PAST_OPEN = "已過去"
WEEKDAYS = "一二三四五六日"
INFERRED_MARK = "〔推測〕"
NO_ITEMS = "（沒有）"
OWNER_NONE = ("無", "沒有", "空")
OWNER_SINGLE = "單人知識庫不用負責人"
OWNER_BLANK = "負責人要寫成員名字；要空著寫 無"
OWNER_CARD_EMPTY = "原檔沒寫負責人就不帶 --owner"
BY_SINGLE = "單人知識庫不用 --by"  # 子項（D-552）
WHO_UNKNOWN = "這台還不知道是誰，先 kb who set <名字>，或帶 --by"
CHECK_NONE = "這件還沒有子項"
SUB_MAX = 50
SUB_TEXT_MAX = 200
SUB_EMPTY = "子項要有內容"
TAG_MAX = 10  # 標籤與註記（D-553）
TAG_LEN = 20
NOTE_MAX = 1000
TAG_EMPTY = "標籤要有字"
TAG_LONG = f"標籤最多 {TAG_LEN} 字"
TAG_COMMA = "一個標籤不能有逗號或頓號；幾個標籤分開給 --tag"
TAG_MANY = f"一件最多 {TAG_MAX} 個標籤"
TAG_BOTH = "同一個標籤不能同時加又拿掉"
NOTE_LONG = f"註記最多 {NOTE_MAX} 字"
RESTORE_FULL = "放回要用子項的完整編號（c-…），從 list --json 的 conflicts 或歷程取"
SUB_LONG = f"子項最多 {SUB_TEXT_MAX} 字"
COMMENT_MAX = 500
COMMENT_COUNT = 200  # 一個子項最多幾則留言
COMMENT_EMPTY = "留言要有內容"
COMMENT_LONG = f"留言最多 {COMMENT_MAX} 字"
HAND_BY = "hand"  # 她直接改日程.md、程式照做的歷程（D-263）
HEADER = ("# 日程\n"
          "這是什麼：要做的事與日期，程式從 .system/agenda.json 整理出來；〔推測〕是我從檔案讀到的，不對就跟我說。\n"
          "怎麼用：跟我說「9/25 下午有個會」「交屋改 12/10」「第二期款付了」就會改這裡。\n"
          "由程式產生；在這裡打勾或刪掉一行，我下次會照著做。\n")  # D-223：照她打勾、改日期做
EMPTY_SENTENCE = "日程是空的；說「9/25 下午有個會」我就記下來。"
DATE_MSG = "日期要寫 YYYY-MM-DD，而且是真的日期"
TIME_MSG = "時間要寫 HH:MM"
KIND_MSG = "種類要是 到期、會議、提醒、期間 之一"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{2}:\d{2}")
_CHANGE_DATE = re.compile(r"date (\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2})")


class AgendaRefused(ValueError):
    """kb agenda 的拒絕：訊息就是給人看的那句。"""


def linker(vault, ents=None, page=MD_POSIX):
    """實體名字 → 程式頁上的寫法（D-280、D-541）：有綜合卡寫 [[<從 page 所在的夾到卡實際位置的相對路徑，去掉 .md>|<名稱>]]
    （卡搬過夾也對，entities.locate；page 是連結寫在哪一頁，預設 系統/ 底下那幾頁），
    沒有卡的、不在清單的照寫名字。同一頁查過的名字記住，不重複找卡。"""
    from kb._lib import entities, paths
    try:
        data = entities.load(vault) if ents is None else ents
    except ValueError:
        data = {"entities": []}
    memo = {}

    def link(name):
        if name in memo:
            return memo[name]
        out = name
        try:
            e = entities.find(data, name)[0]
            if e is not None and e.get("status") == "active" and e.get("type") and not entities.is_self(e):
                where = entities.locate(vault, e, data)
                if where:
                    to = paths.rel_link(page, where)
                    out = f"[[{to[:-3] if to.endswith('.md') else to}|{name}]]"
        except (OSError, ValueError):
            out = name
        memo[name] = out
        return out
    return link


_WIKILINK = re.compile(r"\[\[[^\[\]]*\]\]")


def table_cell(text):
    r"""md 表格的一行（或一格）：連結 [[路徑|名字]] 裡的 | 寫成 \|，不然 Obsidian 把它當欄分隔、格子錯位（記帳 3 確認 #4）。
    已經寫成 \| 的不重複加；連結外的字不動。程式產生的表格都過這個。"""
    return _WIKILINK.sub(lambda m: re.sub(r"(?<!\\)\|", r"\\|", m.group(0)), str(text))


def _today():
    return dt.date.today().isoformat()


def _path(vault):
    return Path(vault) / AGENDA_POSIX


# ---------- 讀 ----------

def empty():
    return {"schema_version": SCHEMA_VERSION, "items": []}


def load(vault):
    """讀日程，重複的條目已移到下一次（只在記憶體，D-501）。其餘同 _load_raw。"""
    data = _load_raw(vault)
    today = _today()
    for it in data["items"]:
        _roll(it, today)
    return data


def _load_raw(vault):
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
    if kind not in KINDS.values():  # 不查 KIND_TEXT：它含舊值 promise（#68 拒收）
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

def _mutate(vault, turn_id, fn, hand_out=None):
    """鎖住讀改寫：fn(data) 回 (結果, 有沒有改)；有改才備份、寫、渲染。回結果。
    fn 之前先處理她對 日程.md 的手改（Obsidian 首頁，#22，D-263）：認得出的照做、認不出的留在頁面上；hand_out 是清單時把處理報告放進去。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load_raw(vault)
        before = copy.deepcopy(data)
        rolled = _roll_all(data, turn_id)
        hand = _consume_hand(vault, data, turn_id)
        if hand_out is not None:
            hand_out.append(hand)
        # 手改比對之後才轉：比對要用她看到的那份（#68）；沒有回合（kb init 補畫日程.md）不轉，備份要回合
        legacy = bool(turn_id) and _convert_legacy(data)
        result, changed = fn(data)
        changed = changed or rolled or legacy or isinstance(hand, dict) or hand == "unrecognized"  # hand.UNRECOGNIZED：頁面照常重寫
        if changed:
            from kb._lib import turn
            turn.backup(vault, AGENDA_POSIX, turn_id)
            write_json_atomic(p, data)
            try:  # 資料已存好；md 重畫不了只記 log，不讓 AI 以為沒記到又加一次（B-470）
                render(vault, turn_id, data, base=before, hand=hand or {})
            except Exception as e:  # noqa: BLE001
                from kb._lib import ledger
                ledger.log_error(vault, f"日程.md 重畫失敗：{type(e).__name__}: {e}", source="agenda_render")
    if changed:
        safe_export(vault)  # 放開 agenda.json 的鎖之後（T-1）
    return result


def _convert_legacy(data):
    """拿掉的舊種類原地換成現在的（#68：promise → deadline）；不加歷程。回有沒有換。"""
    hits = [i for i in data["items"] if i.get("kind") in LEGACY_KINDS]
    for it in hits:
        it["kind"] = LEGACY_KINDS[it["kind"]]
    return bool(hits)


def apply_hand(vault, turn_id):
    """kb renders apply 系統/日程.md：照她手改的做。回報告 dict、None（沒有要做的）或 hand.KEEP_FAILED。"""
    out = []
    _mutate(vault, turn_id, lambda data: (None, False), hand_out=out)
    return out[0] if out else None


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


# ---------- 重複（D-501） ----------

def _every(value):
    """每年／每月／每週 → year／month／week；不重複 → None。"""
    value = (value or "").strip()
    if value == NO_REPEAT:
        return None
    if value not in REPEATS:
        raise AgendaRefused(REPEAT_MSG)
    return REPEATS[value]


def _shift(anchor, every, n):
    """錨點加 n 個週期；月底與 2/29 夾到當月最後一天，下次仍照錨點的日。"""
    if every == "week":
        return anchor + dt.timedelta(weeks=n)
    y, m = divmod(anchor.month - 1 + n * (12 if every == "year" else 1), 12)
    y, m = anchor.year + y, m + 1
    return anchor.replace(year=y, month=m, day=min(anchor.day, calendar.monthrange(y, m)[1]))


def next_after(rep, day):
    """錨點加整數個週期裡第一個晚於 day 的日期（ISO）。"""
    anchor, after = dt.date.fromisoformat(rep["from"]), dt.date.fromisoformat(day)
    if rep["every"] == "week":
        n = (after - anchor).days // 7
    else:
        n = ((after.year - anchor.year) * 12 + after.month - anchor.month) // (12 if rep["every"] == "year" else 1)
    n = max(0, n - 1)
    while _shift(anchor, rep["every"], n) <= after:
        n += 1
    return _shift(anchor, rep["every"], n).isoformat()


def _move(it, day):
    """日期改到 day；有開始日的整段平移同樣天數。"""
    if it.get("start"):
        delta = dt.date.fromisoformat(day) - dt.date.fromisoformat(it["date"])
        it["start"] = (dt.date.fromisoformat(it["start"]) + delta).isoformat()
    it["date"] = day


def _roll(it, today):
    """開著、重複、過了就算已過去的（會議、提醒）而且日期已過 → 移到今天或之後的下一次；回舊日期，沒移回 None。"""
    rep = it.get("repeat")
    if not rep or it.get("status") != "open" or it.get("kind") not in PASSING or it.get("date", "") >= today:
        return None
    old = it["date"]
    yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    _move(it, next_after(rep, yesterday))
    _next_round(it)
    return old


def _next_round(it):
    """移到下一次：回合數加 1，上一回合的勾一律算沒勾（不逐項改子項，留言留著；D-552 (5)、(11)）。回歷程要接的那段。"""
    r = it.get("round", 0)
    it["round"] = (r if isinstance(r, int) and not isinstance(r, bool) and r >= 0 else 0) + 1  # 只認 int（測試裁定 2）
    return "，子項重設" if _live(it) else ""


def _roll_all(data, turn_id):
    today, moved = _today(), False
    for it in data["items"]:
        old = _roll(it, today)
        if old:
            _log(it, f"重複：{old} 過了，移到 {it['date']}" + ("，子項重設" if _live(it) else ""), "program", turn_id)
            moved = True
    return moved


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


def add(vault, kind, date, text, entities, turn_id, start=None, time=None, card=None, source=None, lead_days=None, repeat=None,
        whose=None, owner=None, tags=None, note=None):
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
    every = _every(repeat) if repeat is not None else None
    who = _whose(whose)
    owners = _owner(vault, owner) if owner is not None else None
    if card and owners == []:
        raise AgendaRefused(OWNER_CARD_EMPTY)
    tags = _tags_of(tags) if tags else None
    note = _note(note) if note is not None else None
    source = source or "confirmed"
    if source not in SOURCES:
        raise AgendaRefused(f"來源要是 {'、'.join(SOURCES)} 之一")

    def fn(data):
        origin = None
        if card:
            found = _card(vault, card)
            source_ = "inferred"
            origin = {"card": card, "fp": card_fp(found["front"])}
            if owners is not None:
                origin["owner"] = owners
            same = next((i for i in data["items"] if i.get("status") == "open" and (i.get("origin") or {}).get("card") == card
                         and i.get("kind") == kind and i.get("date") == day and i.get("text") == text), None)
            if same is not None:
                return (same, False), False
        else:
            source_ = source
        it = {"id": _new_id(data), "kind": kind, "start": begin, "date": day, "ended": None, "time": clock, "text": text,
              "entities": names, "status": "open", "source": source_, "origin": origin, "lead_days": lead,
              "created_turn": turn_id, "history": []}
        if every:
            it["repeat"] = {"every": every, "from": day}
        if who and not card:  # 製卡抽的一律全隊（多人共用 2 第 5 點）
            it["whose"] = who
        if owners is not None:
            it["owner"] = owners
        if tags:
            it["tags"] = tags
        if note:
            it["note"] = note
        data["items"].append(it)
        return (it, True), True
    return _mutate(vault, turn_id, fn)


def _tag(value):
    """標籤整理（D-553）：NFKC、空白併一個、開頭的 # 去掉；拒絕空的、逗號頓號、太長。"""
    from kb._lib import streams
    text = " ".join(streams.clean_text(" ".join(unicodedata.normalize("NFKC", str(value or "")).split())).split()).lstrip("#").strip()
    if not text:
        raise AgendaRefused(TAG_EMPTY)
    if any(c in text for c in ",，、"):
        raise AgendaRefused(TAG_COMMA)
    if len(text) > TAG_LEN:
        raise AgendaRefused(TAG_LONG)
    return text


def tag_key(text):
    """比對用的寫法：大小寫不分（全形半形在 _tag 的 NFKC 已經併了）。"""
    return str(text).casefold()


def _tags_of(values):
    """--tag 給的幾個 → 整理過、同鍵只留第一個；超過上限拒絕。"""
    out = {}
    for v in values:
        t = _tag(v)
        out.setdefault(tag_key(t), t)
    if len(out) > TAG_MAX:
        raise AgendaRefused(TAG_MANY)
    return [out[k] for k in sorted(out)]


def _note(value):
    """註記整理（D-553）：換行統一成 \n、換行與 tab 以外的控制字元拿掉、前後空白去掉；空的回 None（清掉）。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    from kb._lib import streams
    text = streams.clean_text(text, keep_newline=True).strip()  # Cc 與雙向控制字元拿掉、留換行（測試裁定 7）
    if len(text) > NOTE_MAX:
        raise AgendaRefused(NOTE_LONG)
    return text or None


def show(value, key=None):
    if key == "repeat" and not value:
        return NO_REPEAT
    if isinstance(value, dict):  # repeat
        return REPEAT_TEXT.get(value.get("every"), "")
    if isinstance(value, list):
        return "、".join(value)
    return "" if value is None else str(value)


def _whose(value):
    """誰的：None／全隊 → None（不存，當 team）；名字清單 → 清單。"""
    if value is None:
        return None
    from kb._lib import people
    got = value if isinstance(value, list) else people.parse_whose(value)
    return None if got == people.TEAM else got


def _owner(vault, value):
    """負責人（D-549）：名字字串或清單 → 成員正名清單（去重）；無、沒有、空 → []。單人知識庫、對不上成員的拒絕。"""
    from kb._lib import people
    if not people.multi(vault):
        raise AgendaRefused(OWNER_SINGLE)
    raw = value if isinstance(value, list) else str(value).replace("、", ",").replace("，", ",").split(",")
    names = [str(x).strip() for x in raw if str(x).strip()]
    if not names:
        raise AgendaRefused(OWNER_BLANK)
    if len(names) == 1 and names[0] in OWNER_NONE:
        return []
    out = []
    for name in names:
        got = _member(vault, name)
        if got not in out:
            out.append(got)
    return out


def _member(vault, name):
    """成員名字或別名 → 正名；對不上丟 AgendaRefused（負責人、子項的 --by 共用）。"""
    from kb._lib import people
    got = people.member_of(vault, name)
    if got is None:
        members = people.members(vault)
        head = f"團隊成員沒有「{name}」；成員：{'、'.join(members)}" if members else "設定檔還沒登記團隊成員"
        raise AgendaRefused(f"{head}。是同事的話先 kb profile add 關於你 名稱={name} 是什麼=本人")
    return got


def visible(vault, items):
    """這台這個人看得到的日程：單人、全隊、或自己在 whose 裡（D-289）。"""
    from kb._lib import people
    mine = people.me(vault)
    if mine is None:
        return list(items)
    return [i for i in items if people.visible(i.get("whose"), mine)]


def _edit_fn(vault, aid, turn_id, by, card, new):
    """edit 在鎖裡改資料的那一段（new 已經驗過、轉好）；手改套用（_apply_hand）也用它。"""
    def fn(data):
        it = _find(data, aid)
        if it.get("kind") == "period" and "entities" in new and len(new["entities"]) != 1:
            raise AgendaRefused("期間只掛一個實體")  # 同 add 的規矩，期間只掛一個實體（B-468）
        fp = None
        if card:
            if (it.get("origin") or {}).get("card") != card:
                raise AgendaRefused("這條不是從這張卡抽的")
            fp = card_fp(_card(vault, card)["front"])
        begin, day = new.get("start", it.get("start")), new.get("date", it.get("date"))
        if begin and begin > day:
            raise AgendaRefused("開始不能晚於日期")
        want = dict(new)
        if "whose" in want:
            want["whose"] = None if (it.get("origin") or card) else _whose(want["whose"])  # 製卡抽的一律全隊
        origin_moved = False
        if "owner" in want and card:  # 給的是新版原檔寫的負責人：跟上次一樣就保留她手改的（D-549 第 4 點）
            if set(want["owner"]) == set(it["origin"].get("owner") or ()) and "owner" in it["origin"]:  # 順序不算改
                del want["owner"]
            else:
                it["origin"]["owner"], origin_moved = want["owner"], True
        if "repeat" in want:
            want["repeat"] = {"every": want["repeat"], "from": day} if want["repeat"] else None
        elif "date" in want and it.get("repeat"):
            want["repeat"] = {**it["repeat"], "from": day}
        changes, owner_only, forced = [], False, False
        for key in EDITABLE:
            if key in want and want[key] != it.get(key):
                if key != "repeat" or show(want[key], key) != show(it.get(key), key):  # 只換錨點不另記
                    changes.append(f"{key} {show(it.get(key), key)} → {show(want[key], key)}")
                if want[key] is None:
                    it.pop(key, None)
                else:
                    it[key] = want[key]
        ops = want.get("_tags")
        if ops:  # 標籤（D-553）：拿掉、加上；同鍵不重複、不改寫法
            keys = {tag_key(t): t for t in it.get("tags") or []}
            plus, minus = [], []
            for k in list(keys) if ops["clear"] else ops["remove"]:
                if k in keys:
                    minus.append(keys.pop(k))
            for t in ops["add"]:
                if tag_key(t) not in keys:
                    keys[tag_key(t)] = t
                    plus.append(t)
            if len(keys) > TAG_MAX:
                raise AgendaRefused(TAG_MANY)
            if plus or minus:
                if keys:
                    it["tags"] = [keys[k] for k in sorted(keys)]
                else:
                    it.pop("tags", None)
                changes.append("標籤 " + " ".join([f"+{t}" for t in plus] + [f"-{t}" for t in minus]))
        if "_note" in want:  # 註記（D-553）：整段換；撞到時值一樣也寫一行定下來
            if want["_note"] != it.get("note"):
                if want["_note"] is None:
                    it.pop("note", None)
                else:
                    it["note"] = want["_note"]
                changes.append("註記 改了")
            if _note_clash(vault, aid):
                it["_force"], forced = ["note"], True
        if changes and all(c.startswith(("owner ", "標籤 ", "註記 ")) for c in changes):
            owner_only = True  # 只改負責人、標籤、註記不算改內容：推測的照樣是推測（D-549 第 5 點、D-553）
        dirty = bool(changes) or "proposal" in it or origin_moved or forced
        if changes:
            if it.get("source") == "inferred" and not card and not owner_only:
                it["source"] = "corrected"
            _log(it, "；".join(changes), by, turn_id)
        elif it.get("source") == "inferred" and not card and (not new or set(new) - {"owner", "_tags", "_note"}):
            it["source"] = "confirmed"
            _log(it, CONFIRM_CHANGE, by, turn_id)
            dirty = True
        if fp is not None and it["origin"].get("fp") != fp:
            it["origin"]["fp"] = fp
            dirty = True
        it.pop("proposal", None)
        return it, dirty
    return fn


def _note_clash(vault, aid):
    from kb._lib import streams
    return any(c.get("id") == aid and c.get("field") == "note" for c in streams.conflicts_of(vault, "agenda"))


def edit(vault, aid, turn_id, by="ai", card=None, tags_add=None, tags_remove=None, tags_clear=False, note=None, **fields):
    """改 date、start、time、text、entities、lead_days、repeat（None 當沒給；repeat「不重複」是拿掉；改日期錨點跟著換）。來源是 inferred 而這次不是帶 card 的重對 → corrected；
    card 給了（只能是出處那張卡）= 照新版重對：來源不變、origin.fp 更新。有 proposal 一律清掉。回條目。
    來源是 inferred、沒有改任何欄（她說推測的那條是對的）→ 記成 confirmed、歷程記一筆（lint 桌面版確認後）。"""
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
    if "repeat" in new:
        new["repeat"] = _every(new["repeat"])
    if "entities" in new:
        new["entities"] = _names(vault, new["entities"])
        if not new["entities"]:
            del new["entities"]
    if "owner" in new:
        new["owner"] = _owner(vault, new["owner"])
        if card and new["owner"] == []:
            raise AgendaRefused(OWNER_CARD_EMPTY)
    if tags_add or tags_remove or tags_clear:  # 標籤（D-553）：加、拿掉（沒有的略過）、全清
        plus = [_tag(t) for t in tags_add or []]
        minus = [tag_key(_tag(t)) for t in tags_remove or []]
        if {tag_key(t) for t in plus} & set(minus):
            raise AgendaRefused(TAG_BOTH)
        new["_tags"] = {"add": plus, "remove": minus, "clear": bool(tags_clear)}
    if note is not None:
        new["_note"] = _note(note)

    return _mutate(vault, turn_id, _edit_fn(vault, aid, turn_id, by, card, new))


def _status_fn(aid, status, turn_id, by):
    """set_status 在鎖裡改資料的那一段；手改套用（_apply_hand）也用它。"""
    def fn(data):
        it = _find(data, aid)
        old = it.get("status")
        if old == status:
            return it, False
        if status == "done" and old == "open" and it.get("repeat"):  # 完成這一次：移到下一次、維持 open（D-501）
            was = it["date"]
            _move(it, next_after(it["repeat"], was))
            _log(it, f"完成 {was} 那次，下次 {it['date']}{_next_round(it)}", by, turn_id)
            return it, True
        it["status"] = status
        change = f"status {old} → {status}"
        if it.get("kind") == "period":
            if status == "open":
                it["ended"] = None
            elif status == "done" and not it.get("ended"):  # 過去區照 ended 畫（確認後裁定 7b）
                it["ended"] = _today()
                change = f"ended {it['ended']}；{change}"
        _log(it, change, by, turn_id)
        return it, True
    return fn


def set_status(vault, aid, status, turn_id, by="ai"):
    if status not in STATUSES:
        raise ValueError(f"狀態要是 {'、'.join(STATUSES)} 之一：{status!r}")
    return _mutate(vault, turn_id, _status_fn(aid, status, turn_id, by))


# ---------- 子項（D-552）：每項 id（c- 加 6 碼 hex）、text、pos、done（勾了多 done_by、done_at，勾或取消都記 done_round），
# 留言 comments（m- 加 6 碼 hex）；拿掉的子項、留言帶 del 留在整份文件裡（restore、退回、多人合併用），對外一律濾掉。
# 不進指紋、不經 edit；多人同時改照子項的每一欄合併（streams.NESTED）。

def _all(it):
    subs = it.get("checklist")
    return [c for c in subs if isinstance(c, dict)] if isinstance(subs, list) else []


def _pos(c):
    v = c.get("pos")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _live(it):
    """沒拿掉的子項，照 pos、再照 id。"""
    return sorted((c for c in _all(it) if not c.get("del")), key=lambda c: (_pos(c), str(c.get("id"))))


def _done(it, c):
    """有效狀態：勾了、而且是這一回合勾的（D-552 (11)：重複日程移到下一次就加一回合）。"""
    return bool(c.get("done")) and c.get("done_round", 0) == it.get("round", 0)


def _notes(c):
    return sorted((m for m in c.get("comments") or [] if isinstance(m, dict) and not m.get("del")),
                  key=lambda m: (str(m.get("at") or ""), str(m.get("id"))))


def public_checklist(it, restored=False):
    """對外的子項（list --json、匯出、成功句 --json）：不列拿掉的，勾用有效狀態，不帶 pos、done_round。
    restored：放回過的項帶 restored_by、restored_at（list --json 用；匯出不帶，確認裁定 6）。"""
    out = []
    for c in _live(it):
        on = _done(it, c)
        row = {"id": c.get("id"), "text": c.get("text"), "done": on, "done_by": c.get("done_by") if on else None,
               "done_at": c.get("done_at") if on else None,
               "comments": [{"id": m.get("id"), "by": m.get("by"), "at": m.get("at"), "text": m.get("text")} for m in _notes(c)]}
        if restored and (c.get("restored_by") or c.get("restored_at")):
            row.update(restored_by=c.get("restored_by"), restored_at=c.get("restored_at"))
        out.append(row)
    return out


def removed_checklist(it):
    """拿掉的子項：誰、什麼時候拿掉（單人 removed_by 是 null；第一版拿掉的沒記人）。"""
    gone = sorted((c for c in _all(it) if c.get("del")), key=lambda c: (_pos(c), str(c.get("id"))))
    return [{"id": c.get("id"), "text": c.get("text"), "removed_by": c.get("removed_by"), "removed_at": c.get("removed_at")}
            for c in gone]


def public(it):
    """對外的條目：checklist 換成對外的形狀（沒有子項就不帶），有拿掉的多 checklist_removed，不帶 round。"""
    out = {k: v for k, v in it.items() if k not in ("checklist", "round", "_force")}
    subs = public_checklist(it, restored=True)
    if subs:
        out["checklist"] = subs
    gone = removed_checklist(it)
    if gone:
        out["checklist_removed"] = gone
    return out


def progress(it):
    """(已勾, 總數)：只算沒拿掉的、勾用有效狀態；沒有子項回 None。"""
    live = _live(it)
    return (sum(1 for c in live if _done(it, c)), len(live)) if live else None


def _new_ref(rows, prefix):
    """c-／m- 加 6 碼 hex，隨機（兩台同時加不會撞）；這份裡已有的（含拿掉的）重抽。"""
    taken = {r.get("id") for r in rows}
    while True:
        got = f"{prefix}-{secrets.token_hex(3)}"
        if got not in taken:
            return got


def _short(value, empty, limit, too_long):
    from kb._lib import streams
    text = " ".join(streams.clean_text(" ".join(str(value or "").split())).split())  # 壓空白照 _text；再拿掉控制字元（測試裁定 7）
    if not text:
        raise AgendaRefused(empty)
    if len(text) > limit:
        raise AgendaRefused(too_long)
    return text


def _sub(it, cid, gone_ok=False):
    """子項收完整編號（c-…，含拿掉的）或數字 N（目前第 N 項）。拿掉的只有 gone_ok（remove、restore）收。"""
    live, ref = _live(it), str(cid).strip()
    if ref.isdigit():
        got = live[int(ref) - 1] if 1 <= int(ref) <= len(live) else None
    else:
        got = next((c for c in _all(it) if c.get("id") == ref), None)
    if got is None:
        if not live:
            raise AgendaRefused(CHECK_NONE)
        raise AgendaRefused(f"這件沒有子項 {ref}；有：" + "、".join(f"{n} {c.get('text')}" for n, c in enumerate(live, 1)))
    if got.get("del") and not gone_ok:
        raise AgendaRefused(f"「{got.get('text')}」已經被拿掉了；要放回用 kb agenda check restore {it.get('id')} {got.get('id')}")
    return got


def _who(vault, who, need=True):
    """勾、留言記的名字：給了 --by 照成員比對轉正名；沒給是這台的人；單人回 None（不記名字）。
    need False（取消勾，不記名字）時多人這台沒設身分也不擋。"""
    from kb._lib import people
    if who is not None:
        if not people.multi(vault):
            raise AgendaRefused(BY_SINGLE)
        return _member(vault, str(who).strip())
    if not people.multi(vault):
        return None
    mine = people.me(vault)
    if mine is None and need:
        raise AgendaRefused(WHO_UNKNOWN)
    return mine


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _clashes(vault, aid):
    """這條子項兩邊改得不一樣的欄（checklist:<子項>:<欄>）。"""
    from kb._lib import streams
    return {c.get("field") for c in streams.conflicts_of(vault, "agenda")
            if c.get("id") == aid and str(c.get("field", "")).startswith("checklist:")}


def clash_count(conflicts, aid):
    return sum(1 for c in conflicts if c.get("id") == aid and str(c.get("field", "")).startswith("checklist:"))


def _settle(sub, field, clash):
    """撞到的就是這一欄：值一樣也寫一行（_force 給 streams.diff），那處撞到就解掉。"""
    if f"checklist:{sub.get('id')}:{field}" in clash:
        sub["_force"] = [field]
        return True
    return False


def _check(vault, aid, turn_id, fn):
    """子項的讀改寫（走 _mutate，有改才備份、寫）：fn(it, 撞到的欄) 回 (額外結果 dict, 有沒有改)；回 {"item", "changed", **額外}。
    完成、取消的條目照樣可以動子項。"""
    def outer(data):
        it = _find(data, aid)
        extra, changed = fn(it, _clashes(vault, aid))
        return {"item": it, "changed": changed, **extra}, changed
    return _mutate(vault, turn_id, outer)


def check_add(vault, aid, texts, turn_id, by="ai"):
    """加一到多項（接在最後），一個不合就都不加。回多帶 added（新加的子項）。"""
    texts = [_short(t, SUB_EMPTY, SUB_TEXT_MAX, SUB_LONG) for t in (texts or [""])]

    def fn(it, _clash):
        if len(_live(it)) + len(texts) > SUB_MAX:
            raise AgendaRefused(f"一件最多 {SUB_MAX} 個子項")
        subs = it.setdefault("checklist", [])
        top, added = max((_pos(c) for c in _all(it)), default=0), []
        for text in texts:
            top += 1
            sub = {"id": _new_ref(subs, "c"), "text": text, "pos": top, "done": False}
            subs.append(sub)
            added.append(sub)
            _log(it, f"check + {text}", by, turn_id)
        return {"added": added}, True
    return _check(vault, aid, turn_id, fn)


def check_set(vault, aid, cid, done, turn_id, by="ai", who=None):
    """勾（done True）或取消勾：勾的記 done_by（單人不記）、done_at，取消就清掉；都記 done_round。
    有效狀態已經是那樣不改（changed False），撞到的是這一欄例外（resolved True）。"""
    name = _who(vault, who, need=bool(done))

    def fn(it, clash):
        sub = _sub(it, cid)
        force = _settle(sub, "done", clash)
        if _done(it, sub) == bool(done) and not force:
            return {"sub": sub}, False
        sub.update(done=bool(done), done_round=it.get("round", 0))
        sub.pop("done_by", None)
        sub.pop("done_at", None)
        if done:
            if name:
                sub["done_by"] = name
            sub["done_at"] = _now()
        _log(it, f"check {sub['text']} {'done' if done else 'undo'}", by, turn_id)
        return {"sub": sub, "resolved": force}, True
    return _check(vault, aid, turn_id, fn)


def check_remove(vault, aid, cid, turn_id, by="ai", who=None):
    """拿掉（標 del，勾、留言留著；記 removed_by（單人不記）、removed_at，清掉 restored_*）；
    已經拿掉不改，撞到的是拿掉這一欄例外（確定拿掉）。"""
    name = _who(vault, who)

    def fn(it, clash):
        sub = _sub(it, cid, gone_ok=True)
        force = _settle(sub, "del", clash)
        if sub.get("del") and not force:
            return {"sub": sub}, False
        sub["del"] = True
        for key in ("removed_by", "restored_by", "restored_at"):
            sub.pop(key, None)
        if name:
            sub["removed_by"] = name
        sub["removed_at"] = _now()
        _log(it, f"check - {sub['text']}", by, turn_id)
        return {"sub": sub, "resolved": force}, True
    return _check(vault, aid, turn_id, fn)


def check_restore(vault, aid, cid, turn_id, by="ai", who=None):
    """放回拿掉的那項（勾、留言、位置跟拿掉前一樣；記 restored_by、restored_at，清掉 removed_*）；
    本來就在不改，撞到的是拿掉這一欄例外。"""
    if str(cid).strip().isdigit():  # 數字只指得到沒拿掉的項：放回只收完整編號
        raise AgendaRefused(RESTORE_FULL)
    name = _who(vault, who)

    def fn(it, clash):
        sub = _sub(it, cid, gone_ok=True)
        force = _settle(sub, "del", clash)
        if not sub.get("del") and not force:
            return {"sub": sub}, False
        for key in ("del", "removed_by", "removed_at", "restored_by"):
            sub.pop(key, None)
        if name:
            sub["restored_by"] = name
        sub["restored_at"] = _now()
        _log(it, f"check 放回 {sub['text']}", by, turn_id)
        return {"sub": sub, "resolved": force}, True
    return _check(vault, aid, turn_id, fn)


def check_edit(vault, aid, cid, text, turn_id, by="ai"):
    """只換字；勾的狀態、誰勾的、留言、編號不動。字一樣不改（撞到的是字例外）。回多帶 old（原本的字）。"""
    text = _short(text, SUB_EMPTY, SUB_TEXT_MAX, SUB_LONG)

    def fn(it, clash):
        sub = _sub(it, cid)
        old, force = sub.get("text"), _settle(sub, "text", clash)
        if old == text and not force:
            return {"sub": sub, "old": old}, False
        sub["text"] = text
        _log(it, f"check 改字 {old} → {text}", by, turn_id)
        return {"sub": sub, "old": old, "resolved": force}, True
    return _check(vault, aid, turn_id, fn)


def check_move(vault, aid, cid, to, turn_id, by="ai"):
    """移到第 to 項（1 起算）：只改那一項的 pos（夾在新位置前後兩項中間；最前減 1、最後加 1）；
    兩項間距小於 1e-6 才整份重編 1、2、3…。已在那裡不改。"""
    def fn(it, _clash):
        sub = _sub(it, cid)
        live = _live(it)
        if isinstance(to, bool) or not isinstance(to, int) or not 1 <= to <= len(live):
            raise AgendaRefused(f"位置要是 1 到 {len(live)}")
        if live.index(sub) == to - 1:
            return {"sub": sub}, False
        others = [c for c in live if c is not sub]
        prev = others[to - 2] if to >= 2 else None
        nxt = others[to - 1] if to - 1 < len(others) else None
        if prev is not None and nxt is not None:
            if _pos(nxt) - _pos(prev) < 1e-6:
                for n, c in enumerate(others[:to - 1] + [sub] + others[to - 1:], 1):
                    c["pos"] = n
            else:
                sub["pos"] = (_pos(prev) + _pos(nxt)) / 2
        else:
            sub["pos"] = _pos(prev) + 1 if prev is not None else _pos(nxt) - 1
        _log(it, f"check {sub['text']} 移到第 {to}", by, turn_id)
        return {"sub": sub}, True
    return _check(vault, aid, turn_id, fn)


def check_comment(vault, aid, cid, text, turn_id, by="ai", who=None):
    """子項底下留一則言（m-…，誰、何時、內容；單人不記誰）。歷程不記內容。回多帶 comment、count（現有幾則）。"""
    text = _short(text, COMMENT_EMPTY, COMMENT_MAX, COMMENT_LONG)
    name = _who(vault, who)

    def fn(it, _clash):
        sub = _sub(it, cid)
        if len(_notes(sub)) >= COMMENT_COUNT:
            raise AgendaRefused(f"「{sub['text']}」的留言已經 {COMMENT_COUNT} 則，先拿掉一些舊的")
        notes = sub.setdefault("comments", [])
        note = {"id": _new_ref(notes, "m")}
        if name:
            note["by"] = name
        note.update(at=_now(), text=text)
        notes.append(note)
        _log(it, f"check {sub['text']} 留言 {note['id']}", by, turn_id)
        return {"sub": sub, "comment": note, "count": len(_notes(sub))}, True
    return _check(vault, aid, turn_id, fn)


def check_uncomment(vault, aid, cid, mid, turn_id, by="ai"):
    """拿掉一則留言（標 del；誰寫的都能拿）：收完整編號或數字 N（那項目前第 N 則）。已經拿掉不改。"""
    def fn(it, _clash):
        sub = _sub(it, cid)
        ref = str(mid).strip()
        if ref.isdigit():
            live = _notes(sub)
            note = live[int(ref) - 1] if 1 <= int(ref) <= len(live) else None
        else:
            note = next((m for m in sub.get("comments") or [] if isinstance(m, dict) and m.get("id") == ref), None)
        if note is None:
            raise AgendaRefused(f"「{sub['text']}」沒有留言 {ref}")
        if note.get("del"):
            return {"sub": sub, "comment": note}, False
        note["del"] = True
        _log(it, f"check {sub['text']} 拿掉留言 {note['id']}", by, turn_id)
        return {"sub": sub, "comment": note}, True
    return _check(vault, aid, turn_id, fn)


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
        if (entry is None or entry.get("status") != "active" or not fp or entry.get("agenda_fp") == fp or cid in skip
                or entry.get("archived")):  # 封存的檔不從它抽日期（放寬丙 12）
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
    upcoming 是今天之後 N 天內（N 是條目的 lead_days，沒有就 agenda_window_days）。會議與提醒過了不算逾期。
    """
    now = dt.date.fromisoformat(today or _today())
    out = {"overdue": 0, "today": 0, "upcoming": 0}
    for it in visible(vault, data["items"]):
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
        data = turn.now(vault)
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

def _entities_suffix(it, link=None):
    names = it.get("entities") or []
    return f"（{'、'.join(link(n) if link else n for n in names)}）" if names else ""


def repeat_suffix(it):
    every = (it.get("repeat") or {}).get("every")
    return f"（{REPEAT_TEXT[every]}）" if every in REPEAT_TEXT else ""


def _line(it, link=None):
    when = with_weekday(it["date"]) + (f"{it['time']} " if it.get("time") else "")
    mark = INFERRED_MARK if it.get("source") == "inferred" else ""
    return (f"- {when}{KIND_TEXT.get(it.get('kind'), it.get('kind'))} {it.get('text')}{_entities_suffix(it, link)}"
            f"{repeat_suffix(it)}{mark}")


def _order(it):
    return (it.get("date", ""), it.get("time") or "", it.get("text", ""), it.get("id", ""))


def _past_row(it, now, cutoff, link=None):
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
    return ref, f"- {ref} {KIND_TEXT.get(it.get('kind'), it.get('kind'))} {it.get('text')}{_entities_suffix(it, link)}{label}"


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
        ledger.log_error(vault, f"日程讀不到實體清單：{e}", source="agenda")
        return {}


def _live_rows(vault, items):
    """上面四區要排的：open 的非期間條目；照日期排。"""
    rows = [i for i in items if i.get("status") == "open" and i.get("kind") != "period" and _day(i) is not None]
    return sorted(rows, key=lambda i: (i.get("date", ""), i.get("time") or "", i.get("text") or "", i.get("id", "")))


def render_text(vault, data, today=None, link=None):
    now = today or _today()
    items = visible(vault, data["items"])
    live = _live_rows(vault, items)
    if not items and not live:
        return HEADER + f"\n{EMPTY_SENTENCE}\n"
    link = linker(vault) if link is None else link
    past_days = config.get(vault, "agenda_past_days")
    window = config.get(vault, "agenda_window_days")
    cutoff = (dt.date.fromisoformat(now) - dt.timedelta(days=past_days)).isoformat()
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
    past = sorted((r for r in (_past_row(i, now, cutoff, link) for i in items) if r), key=lambda r: r[0], reverse=True)

    def block(title, lines):
        return f"## {title}\n" + "\n".join(lines or [NO_ITEMS])
    blocks = [block("逾期", [_line(i, link) for i in overdue]), block("今天", [_line(i, link) for i in today_]),
              block(f"接下來 {window} 天", [_line(i, link) for i in soon]), block("之後", [_line(i, link) for i in later])]
    blocks.append(block(f"過去 {past_days} 天", [line for _d, line in past]))
    from kb._lib import renders
    return HEADER + "\n" + "\n\n".join(blocks) + "\n" + renders.carried_section(vault, MD_POSIX)


def item_lines(vault, data, today=None, link=None):
    """[(日程.md 裡那一行, 編號)]：上面四區與過去區（手改比對用）。"""
    now = today or _today()
    link = linker(vault) if link is None else link
    cutoff = (dt.date.fromisoformat(now) - dt.timedelta(days=config.get(vault, "agenda_past_days"))).isoformat()
    out = []
    for it in visible(vault, data["items"]):
        if it.get("status") == "open" and it.get("kind") != "period" and _day(it) is not None:
            out.append((_line(it, link), it.get("id")))
        row = _past_row(it, now, cutoff, link)
        if row:
            out.append((row[1], it.get("id")))
    return out


def _hand_date(to, it):
    """她寫的新日期（YYYY-MM-DD，或沒寫年的 M/D、M-D：用那條原本的年）→ ISO；看不懂丟 AgendaRefused。"""
    text = str(to).strip()
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", text)
    if m:
        text = f"{str(it.get('date'))[:4]}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return parse_date(text).isoformat()


def _apply_hand(vault, data, got, turn_id):
    """她在日程.md 打勾（完成）、刪掉一行（取消）、改日期：直接改 data（歷程 by hand）。回 (做了什麼的句子, 做不了而改記成認不出的)。
    已經不是 open 的、日期看不懂或不合規矩的不硬做，記成「你改成…」留在頁面上。"""
    from kb._lib import renders
    by_id = {i.get("id"): i for i in data["items"]}
    applied, extra = [], []
    for c in got["checked"]:
        it = by_id.get(c["id"])
        if it is not None and it.get("status") == "open":
            _status_fn(it["id"], "done", turn_id, HAND_BY)(data)
            applied.append(f"記成完成『{it['text']}』")
    for c in got["removed"]:
        it = by_id.get(c["id"])
        if it is not None and it.get("status") == "open":
            _status_fn(it["id"], "cancelled", turn_id, HAND_BY)(data)
            applied.append(f"取消『{it['text']}』")
    for c in got["changed_date"]:
        it = by_id.get(c["id"])
        if it is None or it.get("status") != "open":
            continue
        try:
            day = _hand_date(c["to"], it)
            was = it["date"]
            _edit_fn(vault, it["id"], turn_id, HAND_BY, None, {"date": day})(data)
            applied.append(f"『{it['text']}』改到 {md_of(day)}（原本 {md_of(was)}）")
        except (AgendaRefused, ValueError):
            extra.append({"kind": "changed", "text": f"{renders.unlink(c['line'])[2:]}（你改的日期：{c['to']}）",
                          "orig": renders.unlink(c["line"])[2:]})
    return applied, extra


def _consume_hand(vault, data, turn_id):
    """日程.md 跟程式上次寫的不一樣：用上次真的寫進去的那份（沒記過就用改之前的資料、上次寫的那天重算）比出她改了什麼，
    認得出的做在 data 上（D-263）。回報告 dict、None（沒被手改、或看不出改了條目）或 KEEP_FAILED（她那份沒存成，這次不重寫 md）。"""
    from kb._lib import hand, renders
    hers = renders.check(vault, MD_POSIX)
    if hers is None:
        return None
    known = renders.base(vault, MD_POSIX)  # 上一次真的寫進去的那份（T-29）；沒記過才重算
    try:
        day = renders.recorded_day(vault, MD_POSIX) or _today()
        base_text, lines = known if known else (render_text(vault, data, day), item_lines(vault, data, day))
    except Exception:  # noqa: BLE001  算不出她看到的那份：只比得出「其他」
        base_text, lines = "", []
    from kb._lib import pending
    ids = {i.get("id") for i in data["items"]}
    return hand.guarded(
        vault, MD_POSIX, hers, data,
        lambda st: hand.consume(vault, MD_POSIX, hers, base_text, lines, ids,
                                lambda got: _apply_hand(vault, data, got, turn_id), state=st),
        lambda saved, diff: pending.add_handedit(vault, MD_POSIX, saved, diff, turn_id))


def render(vault, turn_id=None, data=None, today=None, base=None, hand=None):
    """寫 系統/日程.md；內容沒變不寫；有 turn_id 先備進回合。回有沒有寫。
    手改已經由呼叫端處理過就帶 hand（_mutate 在鎖裡處理，D-263）；沒帶（沒拿著鎖的呼叫端）先照 apply_hand 處理一遍。
    她那份沒存成（KEEP_FAILED）就不蓋掉（測試 T-10）。寫完記指紋。
    不匯出：寫日程的路徑放開鎖之後自己 safe_export（D-500、T-1）。base 留著給舊呼叫端，不用了。"""
    from kb._lib import hand as hand_lib, renders
    if hand is None:
        hand = apply_hand(vault, turn_id)
        data = None  # 手改套用可能改了資料：重讀
    data = data if data is not None else load(vault)
    if hand == hand_lib.KEEP_FAILED:
        return False
    link = linker(vault)
    text = render_text(vault, data, today, link)
    target = Path(vault) / MD_POSIX
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            renders.record(vault, MD_POSIX, text, day=today or _today(), lines=item_lines(vault, data, today, link))
            return False
    except (OSError, UnicodeDecodeError):
        pass
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, MD_POSIX, turn_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    renders.record(vault, MD_POSIX, text, day=today or _today(), lines=item_lines(vault, data, today, link))
    return True


# ---------- 匯出（D-500） ----------

EXPORT_NAME = "willy-agenda.json"
EXPORT_SCHEMA = "willy-agenda/1"
EXPORT_STATE_POSIX = ".system/agenda_export.json"
EXPORT_FIELDS = ("id", "kind", "start", "date", "time", "text", "entities", "status", "source", "lead_days", "repeat", "owner",
                 "checklist", "tags", "note")
FP_FIELDS = ("kind", "date", "start", "time", "text", "entities", "status", "lead_days")  # 手機寫回靠它判衝突，改了要升 schema
EXPORT_NO_DIR = "資料夾不存在"
EXPORT_NO_WRITE = "資料夾寫不進去"
EXPORT_FAILED = "程式出錯，詳見 .system/hook-errors.log"


def item_fp(it):
    """條目指紋：FP_FIELDS 照鍵排序的 JSON 取 sha256 前 12 碼。"""
    body = json.dumps({k: it.get(k) for k in FP_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def export_payload(vault, data):
    """willy-agenda/1：全部條目（含完成、取消）只取 EXPORT_FIELDS 加 fp；實體只列 active、不是她自己的。"""
    from kb._lib import entities, paths
    items = []
    for it in visible(vault, data["items"]):
        row = {k: it.get(k) for k in EXPORT_FIELDS}
        row["repeat"] = (it.get("repeat") or {}).get("every")  # D-501：只給週期，不進指紋
        row["owner"] = list(it.get("owner") or [])  # D-549：一律清單，不進指紋
        row["checklist"] = public_checklist(it)  # D-552：一律清單、有效狀態、不列拿掉的，不進指紋
        row["tags"] = list(it.get("tags") or [])  # D-553：一律清單、註記沒有是 null，都不進指紋
        row["fp"] = item_fp(it)
        items.append(row)
    names = [{"id": e.get("id"), "name": e.get("name"), "type": e.get("type")} for e in entities.load(vault)["entities"]
             if e.get("status") == "active" and not entities.is_self(e)]
    return {"schema": EXPORT_SCHEMA, "exported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "willy_version": (paths.read_manifest() or {}).get("version"),
            "fp": hashlib.sha256("".join(r["fp"] for r in items).encode("utf-8")).hexdigest()[:12],
            "items": items, "entities": names}


def _same_export(target, payload):
    """資料夾裡那份跟這次的除了 exported_at 都一樣嗎；讀不到、壞的都當不一樣。"""
    try:
        old = read_json(target)
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "exported_at"}  # noqa: E731
    return strip(old) == strip(payload)


def export_state(vault):
    """.system/agenda_export.json；沒有或讀不到回空的。"""
    try:
        state = read_json(Path(vault) / EXPORT_STATE_POSIX)
    except (OSError, ValueError):
        state = None
    return state if isinstance(state, dict) else {}


def update_export_state(vault, **fields):
    """讀改寫匯出狀態檔（呼叫端持有匯出的鎖，就是這個檔的鎖）；值是 None 的鍵拿掉。失敗只記 log。"""
    p = Path(vault) / EXPORT_STATE_POSIX
    try:
        state = export_state(vault)
        state.update(fields)
        state = {k: v for k, v in state.items() if v is not None}
        state["schema_version"] = 1
        write_json_atomic(p, state)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"{EXPORT_STATE_POSIX} 沒寫成：{type(e).__name__}: {e}", source="agenda_export")


def _now_stamp():
    return dt.datetime.now().astimezone().isoformat(timespec="microseconds")  # 同一秒的成功與失敗也分得出先後


def export(vault):
    """設定了 agenda_export_dir 就匯出；沒設定回 None、什麼都不寫。成功回 {"ok": True, "dir", "written", "items"}；
    任何例外吞掉、寫 hook-errors.log，回 {"ok": False, "dir", "reason"}（reason 是給她聽的白話）。
    整段在匯出自己的鎖裡（agenda_export.json 的，不是 agenda.json 的），鎖裡才讀 agenda.json 與實體、不收呼叫端的資料：
    兩個寫入的匯出排隊，後一個讀到的一定是最新內容（T-3）。"""
    from kb._lib import ledger
    folder = ""
    try:
        folder = config.get(vault, "agenda_export_dir").strip()
        if not folder:
            return None
        lock_path = Path(vault) / EXPORT_STATE_POSIX
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(lock_path):
            return _export_locked(vault, folder)
    except Exception as e:  # noqa: BLE001  拿不到匯出的鎖：只記 log，狀態檔不碰
        ledger.log_error(vault, f"日程匯出到 {folder} 失敗：{type(e).__name__}: {e}", source="agenda_export")
        return {"ok": False, "dir": folder, "reason": EXPORT_FAILED}


def _export_locked(vault, folder):
    """export 的本體，呼叫端持有匯出的鎖。"""
    from kb._lib import ledger
    try:
        if not Path(folder).is_dir():
            raise FileNotFoundError(f"{folder} 不是資料夾或不在")
        payload = export_payload(vault, load(vault))
        target = Path(folder) / EXPORT_NAME
        written = not _same_export(target, payload)
        if written:
            write_text_atomic(target, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")  # 嚴格 JSON（測試裁定 3）
    except Exception as e:  # noqa: BLE001  匯出只是一份視圖，不能擋日程（D-500）
        if isinstance(e, FileNotFoundError):
            reason = EXPORT_NO_DIR
        elif isinstance(e, OSError):
            reason = EXPORT_NO_WRITE
        else:
            reason = EXPORT_FAILED
        ledger.log_error(vault, f"日程匯出到 {folder} 失敗：{type(e).__name__}: {e}", source="agenda_export")
        update_export_state(vault, last_error_at=_now_stamp(), last_error=reason)
        return {"ok": False, "dir": folder, "reason": reason}
    update_export_state(vault, last_ok_at=_now_stamp())
    return {"ok": True, "dir": folder, "written": written, "items": len(payload["items"])}


def safe_export(vault):
    """寫日程或實體的路徑放開鎖之後叫（T-1）：export 已吞錯，這層再保證呼叫端的行為與回傳不變。"""
    try:
        export(vault)
    except Exception:  # noqa: BLE001
        pass
