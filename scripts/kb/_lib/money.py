"""帳 .system/money.json（記帳 2，issue #27；D-271～D-277、D-282、D-284、D-338、D-339、D-342、D-343）。

正本在流水檔 .system/streams/money/（streams.KINDS 登記 money，收回欄 status cancelled，history 只往後加，D-337、D-339）；
.system/money.json 是合起來的衍生副本，照日程那樣整份讀進、整份交回（io.read_json／write_json_atomic 認得檔名）。
一筆：id（m- 加 8 碼 hex）、dir（in｜out｜none 只動數量）、amount（none 一律 0；None 是金額未知，只准 open）、status（open｜done｜
cancelled）、date（收付那天，open 是 None）、due（到期日）、entities、lines（[{"entity","qty","unit","unit_price","place"}]，qty、unit
照她講的原樣、unit_price 是那個單位的價，算的時候才照 Units 換成基本單位，D-344 補；沒寫單位或沒有 unit 欄是 ""）、advanced_by（墊的人，D-338）、text、note、split_from、repeat（{every, from}）、repeat_of、source（confirmed｜inferred｜
corrected）、origin（{"card","fp"}｜{"statement"}｜None）、cards（補的出處卡）、created_turn、history。帳全隊看得到、不分誰的，
不另存記的人（D-339：誰改的照流水檔每行的「是誰」答）。
寫都經 _mutate：鎖、讀、先吃她對帳頁的手改、fn、有變才備進回合再寫、after_write 重畫帳頁；放開鎖之後重畫 系統/日程.md
（日程頁也列帳的到期件，記帳 3）。寫之前看設定檔第十節：關的時候
拒絕所有寫入、只記我講的拒絕帶卡的（D-343，保險；句子給 AI 不給她）；實體改名合併、purge 這些跟著動的不擋。
庫存、收付合計、未收付都由這裡算（D-271、D-284），AI 不心算。
帳頁（記帳 3，D-283、D-284、D-342）：系統/帳.md 總覽（還沒收付的、庫存、每季比較、每年）與 系統/帳/<YYYY>.md 每年一頁（全年、
月份段：合計、照型別的表、商品、預設收起來的明細）。內容沒變不寫（舊年份的頁只有那年的帳變了才寫）；手改照 agenda 那套
（hand.guarded／consume、renders.diff）：明細與還沒收付的那幾行刪掉＝取消、改日期、改金額照做，表與合計改了不算、下次重算，
看不懂的放進「你加的」交給 AI（D-299）。實體名字寫成連到卡實際位置的連結（D-280，agenda.linker）。
"""
import copy
import datetime as dt
import re
import secrets
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from kb._lib import config
from kb._lib.io import FileLock, read_json, write_json_atomic

MONEY_POSIX = ".system/money.json"
SCHEMA_VERSION = 1
DIRS = {"收": "in", "付": "out", "無": "none", "in": "in", "out": "out", "none": "none"}
DIR_TEXT = {"in": "收", "out": "付", "none": ""}
STATUSES = ("open", "done", "cancelled")
REPEATS = {"每年": "year", "每月": "month", "每週": "week", "year": "year", "month": "month", "week": "week"}
NO_REPEAT = "不重複"
FIELDS_SUFFIX = ":fields"  # 清冊 money_fp：不是「開」的時候只抽了數字欄，改成開要重抽（D-343）
OTHER = "其他"
MINUS = "−"
OFF_MSG = "帳是關的，錢的話記成口述"
SPOKEN_MSG = "這個知識庫只記她講的錢"
DIR_MSG = "方向要是 收、付、無 之一"
AMOUNT_UNKNOWN_DONE = "金額未知只准還沒收付的；收付了要給 --amount"
SETTLE_UNKNOWN = "這筆金額未知，收付了要給 --amount"
NOT_OPEN = "這筆不是還沒收付的"
ITEM_MSG = "品項要寫「<商品> <±數量>[單位][@單價][在<地點>]」，例：精華液 -2瓶@1800在北店"
PLACE_MSG = "「{name}」不是地點（地點的型別：{types}）"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"
ADV_SINGLE = "單人知識庫沒有墊的人"
ADV_MEMBER = "「{}」不是成員名單上的人（設定檔第五節）"
ADV_NO_ME = "這台還沒記是誰：先問他叫什麼、kb who set <名字>，再記"
ME_WORDS = ("我", "me", "自己")
UNPLACED = "未指定"
REPEAT_MSG = "重複要是 每年、每月、每週、不重複 之一"
REPEAT_PARTIAL = "重複的那筆要整筆收付；少收付的部分另記一筆"
REPEAT_NEEDS_DUE = "重複的要給 --due（下一次的到期日）"
EDIT_DATE_OPEN = "還沒收付的這筆不能只改收付日；收付了用 kb money settle <編號> --date <那天>"
MERGE_KEEP_CANCELLED = "要留的那筆已經取消了；先 kb money reopen 再併，或換一筆留"
STATEMENT_NO_MONEY = "這句口述沒有錢的記號"
STATEMENT_DONE = "這句口述已經補進帳了"
UNSURE_NEEDS_NEW = "--unsure 要跟 --new 一起給"
_ITEM = re.compile(r"^(?P<name>.+?)\s+(?P<qty>[+-]?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<unit>(?!在)[^\s@0-9+\-]+?)?\s*"
                   r"(?:@\s*(?P<price>[0-9][0-9,]*(?:\.[0-9]+)?萬?))?\s*(?:在\s*(?P<place>\S.*?))?\s*$")


class MoneyRefused(ValueError):
    """kb money 的拒絕：訊息是給 AI 看的那句；payload 是 --json 時多給的（candidates、refused）。"""

    def __init__(self, message, **payload):
        super().__init__(message)
        self.payload = payload


def _today():
    from kb._lib import agenda
    return agenda._today()


def _path(vault):
    return Path(vault) / MONEY_POSIX


def _key(name):
    from kb._lib import entities
    return entities.key(name)


# ---------- 讀 ----------

def empty():
    return {"schema_version": SCHEMA_VERSION, "items": []}


def load(vault):
    """沒檔回空的；壞 JSON、形狀不對、版本比這版新丟 ValueError，訊息帶 .system/money.json。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{MONEY_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or any(not isinstance(i, dict) for i in data["items"]):
        raise ValueError(f"{MONEY_POSIX}（形狀不對）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{MONEY_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "items": data["items"]}


def _find(data, mid):
    it = next((i for i in data["items"] if i.get("id") == mid), None)
    if it is None:
        raise MoneyRefused(f"帳裡沒有 {mid}")
    return it


def get(vault, mid):
    return _find(load(vault), mid)


def _profile(vault):
    from kb._lib import profile
    try:
        return profile.load(vault) or {}
    except ValueError:
        return {}


def mode(vault, prof=None):
    """第十節的狀態 on｜spoken｜off（沒有第十節讀成 spoken）。"""
    from kb._lib import profile
    return profile.money_mode(prof if prof is not None else _profile(vault))


def exists(vault):
    from kb._lib import streams
    return _path(vault).exists() or streams.exists(vault, "money")


# ---------- 寫 ----------

def after_write(vault, turn_id, data, skip=()):
    """寫完（還拿著鎖）重畫帳頁；skip 是她那份沒存成、這次不重寫的頁。畫不出來只記 log，不讓 AI 以為沒記到又加一次。"""
    try:
        render(vault, turn_id, data, hand={}, skip=skip)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"帳.md 重畫失敗：{type(e).__name__}: {e}", source="money_render")


def _after_unlock(vault, turn_id):
    """放開帳的鎖之後重畫日程頁（列帳的到期件）；日程正拿著它自己的鎖（日程頁手改套用到帳）時不叫，日程那邊寫完自己會重畫。"""
    from kb._lib import agenda
    if agenda.holding(vault) or not (Path(vault) / agenda.MD_POSIX).exists():
        return
    try:
        agenda.render(vault, turn_id)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"日程.md 重畫失敗：{type(e).__name__}: {e}", source="money_render")


def check_mode(vault, card=False):
    """關的時候擋所有寫入、只記我講的擋帶卡的（D-343）。"""
    m = mode(vault)
    if m == "off":
        raise MoneyRefused(OFF_MSG, refused="off")
    if card and m == "spoken":
        raise MoneyRefused(SPOKEN_MSG, refused="spoken")


def _mutate(vault, turn_id, fn, check=None, hand_out=None):
    """鎖住讀改寫：fn(data) 回 (結果, 有沒有改)；有改才備份、寫、after_write。check 是 "write"／"card" 時先看開關。
    fn 之前先吃她對帳頁的手改（照 agenda._mutate）；hand_out 是清單時把處理報告放進去。放開鎖之後重畫日程頁。"""
    if check:
        check_mode(vault, card=check == "card")
    from kb._lib import hand as hand_lib
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        hand, failed = _consume_hand(vault, data, turn_id)
        if hand_out is not None:
            hand_out.append(hand)
        result, changed = fn(data)
        changed = changed or isinstance(hand, dict) or hand == hand_lib.UNRECOGNIZED
        if changed:
            from kb._lib import turn
            turn.backup(vault, MONEY_POSIX, turn_id)
            write_json_atomic(p, data)
            after_write(vault, turn_id, data, skip=failed)
    if changed:
        _after_unlock(vault, turn_id)
    return result


def _new_id(data):
    taken = {i.get("id") for i in data["items"]}
    while True:
        new = "m-" + secrets.token_hex(4)
        if new not in taken:
            return new


def _log(it, change, by, turn_id):
    it.setdefault("history", []).append({"date": _today(), "change": change, "by": by, "turn": turn_id})


def _date(text):
    from kb._lib import agenda
    try:
        return agenda.parse_date(text).isoformat()
    except agenda.AgendaRefused as e:
        raise MoneyRefused(str(e)) from None


def _amount(text):
    """金額字串 → int／float；None 是未知。"""
    if text is None:
        return None
    from kb._lib import numbers
    try:
        return numbers.plain(numbers.parse_number(str(text).strip(), negative=False))
    except numbers.NumberRefused as e:
        raise MoneyRefused(str(e)) from None


def _dir(text):
    d = DIRS.get(str(text or "").strip())
    if d is None:
        raise MoneyRefused(DIR_MSG)
    return d


def _every(value):
    value = (value or "").strip()
    if value == NO_REPEAT:
        return None
    if value not in REPEATS:
        raise MoneyRefused(REPEAT_MSG)
    return REPEATS[value]


def _ents(vault):
    from kb._lib import entities
    return entities.load(vault)


def _entity(ents, name):
    from kb._lib import entities
    e, _hit = entities.find(ents, str(name).strip())
    if e is None or e.get("status") != "active":
        raise MoneyRefused(NO_ENTITY.format(str(name).strip()))
    return e


def _names(ents, names):
    out = []
    for n in names or []:
        e = _entity(ents, n)
        if all(_key(x) != _key(e["name"]) for x in out):
            out.append(e["name"])
    return out


def parse_item(text):
    """「精華液 -2瓶@1,800在北店」→ (商品, 數量字串, 單位或 None, 單價字串或 None, 地點或 None)；看不懂丟 MoneyRefused。"""
    m = _ITEM.match(str(text or "").strip())
    if not m:
        raise MoneyRefused(ITEM_MSG)
    return m.group("name").strip(), m.group("qty"), m.group("unit") or None, m.group("price"), m.group("place")


def _type_fields(prof, type_name):
    from kb._lib import profile
    return profile.fields_for(prof, type_name)


def base_unit(prof, type_name):
    """這個型別的基本單位：安全庫存那欄的單位，沒有就第一個「每…」欄的單位；都沒有回 ""。"""
    rows = _type_fields(prof, type_name)
    for f in rows:
        if f.get("field") == "安全庫存" and f.get("unit"):
            return f["unit"]
    return next((f["unit"] for f in rows if str(f.get("field", "")).startswith("每") and f.get("unit")), "")


def _field_unit(prof, type_name, field):
    """第九節這個型別那一欄的單位；第九節沒這欄回 None。"""
    return next(((f.get("unit") or "") for f in _type_fields(prof, type_name) if f.get("field") == field), None)


def _pack(entry, field):
    """實體上「每…」的值；第九節拿掉那欄時值進了 old（from 是 None，不是她說清掉），照用最後那個（記帳2 測試第 4 輪 #23 (b)）。"""
    from kb._lib import entities
    v = entities.field_value(entry, field) if entry else None
    if v is None and entry:
        cur = next((c for n, c in entities.fields_of(entry).items() if entities.key(n) == entities.key(field)), None)
        if cur and cur.get("from") is None and cur.get("old"):
            v = cur["old"][-1].get("value")
    return Fraction(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def factor(prof, entry, unit, base):
    """一<unit> 是幾個基本單位（Fraction，D-344 補）：沒寫單位或就是基本單位是 1；實體有「每<unit>」的值就是它；
    基本單位是大的（每<基本單位> 的單位是 unit）就倒過來除；換不了回 None。"""
    if not unit or unit == base:
        return Fraction(1)
    if not base or not entry:
        return None
    typ = entry.get("type")
    v = _pack(entry, "每" + unit)
    if v is not None and _field_unit(prof, typ, "每" + unit) in (None, "", base):
        return v
    v = _pack(entry, "每" + base)
    if v is not None and _field_unit(prof, typ, "每" + base) == unit:
        return 1 / v
    return None


def _frac(v):
    return Fraction(str(v if v is not None else 0))


def _num(fr):
    """Fraction → JSON 的數：除得盡的照原數，除不盡的取 4 位小數（只在顯示、合計時，帳裡存的是她講的數）。"""
    from kb._lib import numbers
    fr = Fraction(fr)
    d = Decimal(fr.numerator) / Decimal(fr.denominator)
    if d * fr.denominator != fr.numerator:
        d = d.quantize(Decimal("0.0001"))
    return numbers.plain(d)


class Units:
    """一個商品的單位換算（D-344 補）：base 是第九節的基本單位（"" 是沒有）；to_base 把一個品項換成 (數, 組)，
    換得了的組是 base（沒寫單位的也是），換不了的組是它自己的單位。
    D-344 三補：第九節的單位是型別的預設；said 是帳裡這個商品各筆照記的順序講的單位，第一筆有寫單位、換不到第九節的單位、
    之後每筆都換得到它（含沒寫單位的）→ 那個單位就是它自己的基本單位（own，type_base 留第九節那個）。"""

    def __init__(self, prof, entry, said=()):
        self.entry = entry
        self.type_base = self.base = base_unit(prof, entry.get("type")) if entry else ""
        self.prof = prof
        said = list(said)
        first = said[0] if said else ""
        self.own = bool(self.base and first and factor(prof, entry, first, self.base) is None
                        and all(factor(prof, entry, u, first) is not None for u in said))
        if self.own:
            self.base = first

    def to_base(self, qty, unit):
        unit = unit or ""
        f = factor(self.prof, self.entry, unit, self.base)
        return (_frac(qty) * f, self.base) if f is not None else (_frac(qty), unit)

    def main(self, groups):
        """算庫存、毛利時的基本單位：第九節有就是它；沒有而這個商品只有一組（只講過一種單位）就是那組，安全庫存、毛利、
        多品項估的金額照算（記帳2 測試第 4 輪 #18）；兩組以上換不了是 ""。"""
        groups = list(dict.fromkeys(groups))
        return self.base or (groups[0] if len(groups) == 1 else "")


_SAID = object()


def unit_cache(data, skip=None):
    """_units 的 cache，帶著帳裡每個商品照記的順序講過的單位（D-344 三補看它自己的基本單位）；skip 是不算的那筆編號。
    沒帶這個的 cache（{}）照第九節算。"""
    said = {}
    for it in _live(data):
        if skip is None or it.get("id") != skip:
            for ln in it.get("lines") or []:
                said.setdefault(_key(ln.get("entity") or ""), []).append(ln.get("unit") or "")
    return {_SAID: said}


def _units(prof, ents, name, cache=None):
    from kb._lib import entities
    k = _key(name or "")
    if cache is not None and k in cache:
        return cache[k]
    said = (cache or {}).get(_SAID, {}).get(k, ())
    got = Units(prof, entities.find(ents, name)[0] if name else None, said)
    if cache is not None:
        cache[k] = got
    return got


def _show_q(fr, unit=""):
    from kb._lib import numbers
    return numbers.show(_num(fr)) + (f" {unit}" if unit else "")


def groups_text(groups, base, prof=None, typ=None):
    """[(組的單位, 數)]（主要的那組在前）→「12 瓶＋2 箱（不知道一箱幾瓶）」；只有一組照那組顯示（D-344 補：換不了的分開列）。
    分開的那組淨值是 0 不寫（第 5 輪 #31）；大小看不出寫「（不知道盒跟瓶怎麼換）」不猜方向（第 5 輪 #27）。"""
    if not groups:
        return _show_q(Fraction(0), base)
    groups = groups[:1] + [(u, q) for u, q in groups[1:] if q]
    many = len(groups) > 1
    parts = []
    for i, (u, q) in enumerate(groups):
        text = _show_q(abs(q) if i else q, u) + ("（沒寫單位）" if many and not u else "")
        parts.append(text if not i else (MINUS if q < 0 else "＋") + text)
    odd = [u for u, _q in groups if u and u != base]
    if base and odd:
        return "".join(parts) + "（不知道" + "、".join(_dunno_bare(prof, typ, u, base) for u in odd) + "）"
    return "".join(parts) + ("（單位不同，沒有加在一起）" if many else "")


def _pick_main(groups, base):
    """主要的那組：基本單位那組；沒有就第一組（只有一組照那組）。"""
    if base in groups or not groups:
        return base
    return next(iter(groups))


def _unit_label(u):
    return u or "沒寫單位"


def _ask_pack(prof, typ, name, u, r):
    """問她「大的裝幾個小的」那一步（記帳2 測試第 4 輪 #17）：第九節已有「每u（單位 r）」或「每r（單位 u）」就知道哪個大、
    直接問、設那欄；都沒有就讓 AI 問她哪個大，不猜。設的一律是 每<大的>=<整數>、欄的單位是小的。"""
    got = _order(prof, typ, u, r)
    if got is None:
        return (f"問她一次{u}、{r}哪個大、一個大的裝幾個小的（像一箱幾瓶），答了先 kb profile add 數字欄 型別={typ} "
                f"欄名=每<大的> 單位=<小的>，再 kb entity field {name} 每<大的>=<整數> --said")
    big, small = got
    return f"問她一次一{big}幾{small}，答了 kb entity field {name} 每{big}=<整數> --said"


def _unit_msg(prof, units, name, prior, new_units, bad):
    """換不過去的退回句（D-344 補；#16 先 profile add 再 entity field；第 4 輪 #17 只問大的裝幾個小的）。prior 是
    [(單位, 帳的編號)]，new_units 是這次的單位，bad 是要問的單位。"""
    base, typ = units.base, (units.entry or {}).get("type")
    old = [u for u, _mid in prior]
    head = (f"「{name}」以前記過的單位：{'、'.join(dict.fromkeys(map(_unit_label, old))) or '（沒有）'}；"
            f"這次是：{'、'.join(dict.fromkeys(map(_unit_label, new_units)))}；換不成同一個單位，沒有寫。")
    steps, named, pairs = [], [u for u in old + list(new_units) if u], set()
    for u in dict.fromkeys(x for x in bad if x):
        g = units.to_base(1, u)[1]
        r = base or next((x for x in named if units.to_base(1, x)[1] != g), "")
        pair = frozenset((g, units.to_base(1, r)[1]))
        if r and len(pair) == 2 and pair not in pairs:  # 第 5 輪 #25：跟自己同一組的不問，箱瓶、瓶箱只問一次
            pairs.add(pair)
            steps.append(_ask_pack(prof, typ, name, u, r))
    blank = list(dict.fromkeys(mid for u, mid in prior if not u))
    if not base and blank and any(new_units):
        steps.append(f"之前沒寫單位的（{'、'.join(blank)}）問她是什麼單位，"
                     f"kb money edit <編號> --item \"<那筆全部的品項，補上單位>\"")
    tail = "；然後照原句再記" if steps else ""
    if not base and any(not u for u in new_units) and any(old):
        steps.append(f"這次沒寫單位：是{'、'.join(dict.fromkeys(u for u in old if u))}的話 --item 補上單位再記，不確定先問她")
    return head + "；".join(steps) + tail


def _check_count(prof, ents, data, line):
    """盤點寫入前（D-344 再補：盤點例外）：要對得上合計，以前的筆加上這次換不到同一組就退回、教 AI 問一次。記帳、改帳不因單位擋
    （換不了的照記、分開列，成功句帶 unit_asks）。"""
    k = _key(line["entity"])
    prior = [(ln.get("unit") or "", it.get("id")) for it in _live(data) for ln in it.get("lines") or []
             if _key(ln.get("entity") or "") == k]
    units = _units(prof, ents, line["entity"], unit_cache(data))
    said = [line.get("unit") or ""]
    groups = {units.to_base(1, u)[1] for u, _m in prior}
    if len(groups | {units.to_base(1, u)[1] for u in said}) > 1:
        bad = [u for u in said if units.to_base(1, u)[1] not in groups] or [u for u, _m in prior]
        raise MoneyRefused(_unit_msg(prof, units, line["entity"], prior, said, bad))


def _order(prof, typ, u, r):
    """u、r 哪個大：第九節有「每u（單位 r）」是 u 大、「每r（單位 u）」是 r 大，回 (大, 小)；看不出回 None（不猜）。"""
    if _field_unit(prof, typ, "每" + u) == r:
        return u, r
    if _field_unit(prof, typ, "每" + r) == u:
        return r, u
    return None


def _dunno_bare(prof, typ, u, r):
    got = _order(prof, typ, u, r) if prof is not None else None
    return f"一{got[0]}幾{got[1]}" if got else f"{u}跟{r}怎麼換"


def _dunno(prof, typ, u, r):
    """換不了的那句：第九節看得出哪個大才寫「（不知道一箱幾瓶）」；看不出不猜：「（不知道箱跟瓶怎麼換）」（第 4 輪 #17、第 5 輪 #27）。"""
    return f"（不知道{_dunno_bare(prof, typ, u, r)}）"


ASK_TAIL = "；她不知道就放著，之後不再問（只會分開列，不會加錯）"


def unit_asks(vault, it, prof=None, ents=None, data=None):
    """記好、改好之後（D-344 再補：記帳不因單位擋）：這筆的品項有換不到同一組的單位 → [{"entity","unit","with","mark","shown","ask"}]，
    一個商品一個單位一條。有基本單位：換不到基本單位的（成功句的品項已帶 mark，shown 是 True）；沒有：換不到這個商品最早那組的
    （只講過一種單位不問）。D-344 三補：只用它自己那一種單位的不問；基本單位照這筆之前的帳看（之前只用盒，這筆講瓶就問）。ask 教 AI 問她一次（設 每<大的>=<整數>、欄的單位是小的；之前沒寫單位的教 kb money edit 補）。"""
    prof = _profile(vault) if prof is None else prof
    ents = _ents(vault) if ents is None else ents
    data = load(vault) if data is None else data
    first, blank = {}, {}
    for x in _live(data):
        for ln in x.get("lines") or []:
            k = _key(ln.get("entity") or "")
            first.setdefault(k, ln.get("unit") or "")
            if not ln.get("unit") and x.get("id") != it.get("id"):
                blank.setdefault(k, []).append(x.get("id"))
    out, seen, cache, pre = [], set(), unit_cache(data), unit_cache(data, it.get("id"))
    for ln in it.get("lines") or []:
        name, u, k = ln["entity"], ln.get("unit") or "", _key(ln["entity"])
        after = _units(prof, ents, name, cache)
        if after.own:  # D-344 三補：只用它自己那一種單位，不問
            continue
        units = _units(prof, ents, name, pre)  # 照這筆之前算：之前只用盒、這筆講瓶也要問一次
        g = units.to_base(1, u)[1]
        ref = units.base or units.to_base(1, first.get(k, u))[1]
        if g == ref or (k, g) in seen:
            continue
        seen.add((k, g))
        typ = (units.entry or {}).get("type")
        if u and ref:
            mark, ask = _dunno(prof, typ, u, ref), _ask_pack(prof, typ, name, u, ref)
        elif u:
            ids = "、".join(dict.fromkeys(blank.get(k, [])))
            mark = "（之前有沒寫單位的）"
            ask = f"之前沒寫單位的（{ids}）問她是什麼單位，kb money edit <編號> --item \"<那筆全部的品項，補上單位>\""
        else:
            mark = f"（沒寫單位，不知道是不是{ref}）"
            ask = f"這筆{name}沒寫單位：問她是不是{ref}，是的話 kb money edit {it.get('id')} --item \"<這筆全部的品項，補上單位>\""
        shown = bool(after.base) and after.to_base(1, u)[1] != after.base  # 成功句的品項字已帶 mark
        out.append({"entity": name, "unit": u, "with": ref, "mark": mark, "shown": shown, "ask": ask + ASK_TAIL})
    return out


def line_text(prof, ents, ln, cache=None):
    """一個品項的數量與單位（D-344 補）：「+2 箱（48 瓶）」；沒寫單位算基本單位；換不了的「+2 箱（不知道一箱幾瓶）」。"""
    units = _units(prof, ents, ln.get("entity"), cache)
    unit, q = ln.get("unit") or "", _frac(ln.get("qty"))
    base = units.base
    if not unit or unit == base:
        return _qty_text(q, base)
    got, group = units.to_base(q, unit)
    if group == base and base:
        return f"{_qty_text(q, unit)}（{_show_q(abs(got), base)}）"
    return _qty_text(q, unit) + (_dunno(prof, (units.entry or {}).get("type"), unit, base) if base else "")


def _place(vault, prof, ents, name, place_types):
    """地點：沒有地點型別的知識庫一律 None；給了要是地點型別的實體；沒給照記的人平常在哪（D-342）。"""
    if not place_types:
        return None
    if name:
        e = _entity(ents, name)
        if _key(e.get("type")) not in {_key(t) for t in place_types}:
            raise MoneyRefused(PLACE_MSG.format(name=e["name"], types="、".join(place_types)))
        return e["name"]
    from kb._lib import people, profile
    me = people.me(vault)
    return profile.usual_place(prof, me) if me else None


def _lines(vault, raw_items, prof=None, ents=None):
    """品項換成 lines：照她講的原樣存 qty、unit（沒講是 ""），單價是她講那個單位的價（D-344 補，寫入不換算）；
    不因單位擋（D-344 再補），換不了的寫完由 unit_asks 教 AI 問一次。"""
    from kb._lib import numbers, profile
    prof = _profile(vault) if prof is None else prof
    ents = _ents(vault) if ents is None else ents
    place_types = profile.place_types(prof)
    out = []
    for raw in raw_items or []:
        name, qty, unit, price, place = parse_item(raw)
        e = _entity(ents, name)
        try:
            n = numbers.parse_number(qty.lstrip("+"))
            unit_price = None if price is None else numbers.plain(numbers.parse_number(price, negative=False))
        except numbers.NumberRefused as e2:
            raise MoneyRefused(str(e2)) from None
        out.append({"entity": e["name"], "qty": numbers.plain(n), "unit": unit or "", "unit_price": unit_price,
                    "place": _place(vault, prof, ents, place, place_types)})
    return out


def member(vault, name):
    """「我」（或空字）換成這台的人（people.me，確認報告 #2）；其他照給的。單人或這台還沒記是誰 → MoneyRefused。"""
    if str(name).strip() not in ME_WORDS + ("",):
        return name
    from kb._lib import people
    if not people.multi(vault):
        raise MoneyRefused(ADV_SINGLE)
    me = people.me(vault)
    if not me:
        raise MoneyRefused(ADV_NO_ME)
    return me


def _advanced(vault, name):
    if name is None:
        return None
    from kb._lib import people
    if not people.multi(vault):
        raise MoneyRefused(ADV_SINGLE)
    name = member(vault, name)
    members = people.members(vault)
    hit = next((m for m in members if _key(m) == _key(name)), None)
    if hit is None and people.me(vault) == name:
        return name  # 這台的人（kb who set 記的）就算第五節還沒那列也認
    if hit is None:
        raise MoneyRefused(ADV_MEMBER.format(name))
    return hit


def _ref_day(it):
    return it.get("date") or it.get("due")


def _overlap(a, b):
    ka, kb_ = {_key(x) for x in a or []}, {_key(x) for x in b or []}
    return bool(ka & kb_) if ka or kb_ else True


def _row(it, is_open):
    return {"id": it.get("id"), "dir": it.get("dir"), "amount": it.get("amount"), "status": it.get("status"),
            "date": it.get("date"), "due": it.get("due"), "entities": list(it.get("entities") or []), "text": it.get("text"),
            "open": is_open}


def find_candidates(vault, data, dir_, amount, day, names, exclude=()):
    """D-276：同實體（都沒掛也算）、同方向，金額相同、日期差在 money_dupe_days 天內的；同實體同方向還沒收付的也列（open: True）。"""
    if dir_ == "none":
        return []
    days = config.get(vault, "money_dupe_days")
    out = []
    for it in data["items"]:
        if it.get("id") in exclude or it.get("status") == "cancelled" or it.get("dir") != dir_:
            continue
        if not _overlap(names, it.get("entities")):
            continue
        if it.get("status") == "open":
            out.append(_row(it, True))
            continue
        ref = _ref_day(it)
        if amount is not None and it.get("amount") is not None and Decimal(str(it["amount"])) == Decimal(str(amount)) \
                and ref and day and abs((dt.date.fromisoformat(ref) - dt.date.fromisoformat(day)).days) <= days:
            out.append(_row(it, False))
    return out


def candidates(vault, dir_, amount=None, date=None, due=None, entities=()):
    """kb money candidates：只列不寫。"""
    names = _names(_ents(vault), entities)
    day = _date(date) if date else (_date(due) if due else _today())
    return find_candidates(vault, load(vault), _dir(dir_), _amount(amount), day, names)


def _statement(vault, sid):
    from kb._lib import statements
    s = statements.find(statements.load(vault), sid)
    if s is None:
        raise MoneyRefused(statements.NOT_FOUND.format(sid))
    mark = s.get("money")
    if not isinstance(mark, dict):
        raise MoneyRefused(STATEMENT_NO_MONEY)
    if mark.get("ledger"):
        raise MoneyRefused(STATEMENT_DONE)
    return s


def add(vault, turn_id, dir_, amount=None, date=None, due=None, entities=(), items=(), advanced_by=None, text="", note="",
        repeat=None, new=False, unsure=None, card=None, statement=None, by="ai"):
    """記一筆。回 (筆, 新建了嗎)。驗完才寫；有候選又沒帶 new → MoneyRefused（payload refused=candidates、candidates）。
    card 給了 → 來源 inferred、origin 記卡與指紋，同一張卡已有同方向同金額同日的不重複、回既有。statement 給了 → 照那句口述的日期記、
    origin.statement，寫完口述的記號補 ledger。unsure（要跟 new）→ 寫完開一條 money_dupe 待處理（呼叫端 CLI 開）。"""
    from kb._lib import agenda
    if unsure and not new:
        raise MoneyRefused(UNSURE_NEEDS_NEW)
    d = _dir(dir_)
    amt = 0 if d == "none" else _amount(amount)
    adv = _advanced(vault, advanced_by)
    st = _statement(vault, statement) if statement else None
    day = _date(date) if date else None
    if st is not None and day is None and not due:
        day = st.get("date") or _today()
    due_ = _date(due) if due else None
    if d == "none":
        status, day = "done", day or _today()
    elif day:
        status = "done"
    elif due_ or adv or card:
        status = "open"  # 製卡讀到沒寫日期的款項：記還沒收付、沒到期日，不當今天收了（確認報告 #5）
    else:
        status, day = "done", _today()
    if amt is None and status != "open":
        raise MoneyRefused(AMOUNT_UNKNOWN_DONE)
    prof, ents = _profile(vault), _ents(vault)
    names = _names(ents, entities)
    lines = _lines(vault, items, prof, ents)
    every = _every(repeat) if repeat else None
    if every and not due_:
        raise MoneyRefused(REPEAT_NEEDS_DUE)
    text = " ".join(str(text or "").split())
    note = " ".join(str(note or "").split())

    def fn(data):
        if unsure:
            _find(data, unsure)  # 寫之前先驗，編號錯了不寫（不然 AI 重跑會記兩次）
        origin, source = None, "confirmed"
        if card:
            found = agenda._card(vault, card, no_card="這份還沒有檔案卡，先寫卡再記帳")
            origin, source = {"card": card, "fp": agenda.card_fp(found["front"])}, "inferred"
            same = next((i for i in data["items"] if i.get("status") != "cancelled" and (i.get("origin") or {}).get("card") == card
                         and i.get("dir") == d and i.get("amount") == amt and _ref_day(i) == (day or due_)), None)
            if same is not None:
                return (same, False), False
        elif st is not None:
            origin = {"statement": st["id"]}
        if not new:
            found_c = find_candidates(vault, data, d, amt, day or due_ or _today(), names)
            if found_c:
                raise MoneyRefused("有可能是同一筆的，先看 candidates；確定是另一筆再帶 --new", refused="candidates",
                                   candidates=found_c)
        it = {"id": _new_id(data), "dir": d, "amount": amt, "status": status, "date": day if status == "done" else None,
              "due": due_, "entities": names, "lines": lines, "advanced_by": adv, "text": text, "note": note,
              "split_from": None, "repeat": {"every": every, "from": due_ or day} if every else None, "repeat_of": None,
              "source": source, "origin": origin, "cards": [], "created_turn": turn_id, "history": []}
        _log(it, "記下", by, turn_id)
        data["items"].append(it)
        return (it, True), True
    got = _mutate(vault, turn_id, fn, check="card" if card else "write")
    if st is not None and got[1]:
        from kb._lib import statements
        statements.set_ledger(vault, st["id"], got[0]["id"], turn_id)
    return got


EDITABLE = ("amount", "date", "due", "entities", "lines", "advanced_by", "text", "note")


def _show(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "、".join(x if isinstance(x, str) else f"{x.get('entity')} {x.get('qty')}{x.get('unit') or ''}" for x in v)
    return str(v)


def edit(vault, mid, turn_id, by="ai", amount=None, date=None, due=None, entities=None, items=None, advanced_by=None, text=None,
         note=None):
    """改欄（None 當沒給）。來源推測的改了 → corrected。回 (改之後的筆, 改之前的筆)。"""
    new = {}
    if amount is not None:
        new["amount"] = _amount(amount)
    if date is not None:
        new["date"] = _date(date)
    if due is not None:
        new["due"] = _date(due)
    if entities:
        new["entities"] = _names(_ents(vault), entities)
    if items:
        new["lines"] = _lines(vault, items)
    if advanced_by is not None:
        new["advanced_by"] = _advanced(vault, advanced_by)
    if text is not None:
        new["text"] = " ".join(str(text).split())
    if note is not None:
        new["note"] = " ".join(str(note).split())

    return _mutate(vault, turn_id, _edit_fn(mid, new, turn_id, by), check="write")


def _edit_fn(mid, new, turn_id, by):
    """edit 在鎖裡改資料的那一段（new 已經驗過、轉好）；帳頁手改套用也用它。"""
    def fn(data):
        want = dict(new)
        it = _find(data, mid)
        before = copy.deepcopy(it)
        if it.get("dir") == "none" and "amount" in want:
            want.pop("amount")
        if want.get("amount", it.get("amount")) is None and it.get("status") == "done":
            raise MoneyRefused(AMOUNT_UNKNOWN_DONE)
        if "date" in want and it.get("status") == "open" and it.get("dir") != "none":
            raise MoneyRefused(EDIT_DATE_OPEN)
        changes = []
        for k in EDITABLE:
            if k in want and want[k] != it.get(k):
                changes.append(f"{k} {_show(it.get(k))} → {_show(want[k])}")
                it[k] = want[k]
                if k == "due" and it.get("repeat"):
                    it["repeat"] = {**it["repeat"], "from": want[k]}
        if not changes:
            return (it, before), False
        if it.get("source") == "inferred":
            it["source"] = "corrected"
        _log(it, "；".join(changes), by, turn_id)
        return (it, before), True
    return fn


def settle(vault, mid, turn_id, amount=None, date=None, due=None, by="ai", card=None):
    """收付了（D-275）：金額少於原額就拆——原筆改已收付那部分（品項留在這筆），另生一筆還沒收付的差額（split_from 指回）；
    有 repeat 的另生一筆已收付（repeat_of）、原筆的到期移到下一次（D-501 那套）。回 (原筆, 拆出的或 None, 重複生的或 None)。"""
    from kb._lib import agenda
    paid = _amount(amount)
    day = _date(date) if date else _today()
    due_ = _date(due) if due else None
    if card:  # 製卡讀到匯款單、收據：出處記在收付的那筆（拆出的還沒收付那筆不帶）
        agenda._card(vault, card, no_card="這份還沒有檔案卡，先寫卡再記帳")

    def _cite(row):
        if card and card not in (row.get("cards") or []) and (row.get("origin") or {}).get("card") != card:
            row["cards"] = list(row.get("cards") or []) + [card]

    def fn(data):
        it = _find(data, mid)
        if it.get("status") != "open":
            raise MoneyRefused(NOT_OPEN)
        whole = it.get("amount")
        got = whole if paid is None else paid
        if got is None:
            raise MoneyRefused(SETTLE_UNKNOWN)
        if it.get("repeat"):
            if whole is not None and Decimal(str(got)) < Decimal(str(whole)):
                raise MoneyRefused(REPEAT_PARTIAL)
            done = copy.deepcopy(it)
            done.update({"id": _new_id(data), "status": "done", "date": day, "amount": got, "repeat": None, "repeat_of": mid,
                         "created_turn": turn_id, "history": []})
            _cite(done)
            _log(done, f"記下（{mid} 那次收付了）", by, turn_id)
            data["items"].append(done)
            was = it.get("due") or day
            it["due"] = agenda.next_after(it["repeat"], was)
            _log(it, f"{was} 那次收付了，下次 {it['due']}", by, turn_id)
            return (it, None, done), True
        rest = None
        if whole is not None and Decimal(str(got)) < Decimal(str(whole)):
            from kb._lib import numbers
            rest = copy.deepcopy(it)
            rest.update({"id": _new_id(data), "amount": numbers.plain(Decimal(str(whole)) - Decimal(str(got))), "lines": [],
                         "split_from": mid, "due": due_ or it.get("due"), "created_turn": turn_id, "history": []})
            _log(rest, f"拆自 {mid}（原 {whole}，收付了 {got}）", by, turn_id)
            data["items"].append(rest)
        it["status"], it["date"] = "done", day
        _cite(it)
        if got != whole:
            it["amount"] = got
        _log(it, f"status open → done（{day}，{got}）", by, turn_id)
        return (it, rest, None), True
    return _mutate(vault, turn_id, fn, check="card" if card else "write")


def set_status(vault, mid, status, turn_id, by="ai"):
    """cancel（cancelled）／reopen（open）。回筆。"""
    if status not in STATUSES:
        raise ValueError(status)
    return _mutate(vault, turn_id, _status_fn(mid, status, turn_id, by), check="write")


def _status_fn(mid, status, turn_id, by):
    """set_status 在鎖裡改資料的那一段；帳頁手改套用（刪掉一行＝取消）也用它。"""
    def fn(data):
        it = _find(data, mid)
        old = it.get("status")
        want = status
        if want == "open":  # 重開：取消前收付了的（有收付日）回到已收付，否則回到還沒收付；已收付的重開＝改回還沒收付
            if old == "cancelled" and (it.get("date") or it.get("dir") == "none"):
                want = "done"
            elif old == "done":
                it["date"] = None
        if want == old:
            return it, False
        it["status"] = want
        _log(it, f"status {old} → {want}", by, turn_id)
        return it, True
    return fn


def attach(vault, mid, card, turn_id, by="ai"):
    """補出處（D-276：明顯是同一筆，讀到收據）：cards 多這張卡。回筆。"""
    from kb._lib import agenda
    agenda._card(vault, card, no_card="這份還沒有檔案卡，先寫卡再補出處")

    def fn(data):
        it = _find(data, mid)
        if card in (it.get("cards") or []) or (it.get("origin") or {}).get("card") == card:
            return it, False
        it["cards"] = list(it.get("cards") or []) + [card]
        _log(it, f"補出處 {card}", by, turn_id)
        return it, True
    return _mutate(vault, turn_id, fn, check="card")


def merge(vault, keep, drop, turn_id, by="ai"):
    """兩筆是同一筆：drop 改 cancelled（記併進哪筆），它的出處卡併進 keep。回 (keep, drop)。"""
    if keep == drop:
        raise MoneyRefused("要給兩筆不同的帳")

    def fn(data):
        a, b = _find(data, keep), _find(data, drop)
        if a.get("status") == "cancelled":
            raise MoneyRefused(MERGE_KEEP_CANCELLED)
        if b.get("status") == "cancelled":
            raise MoneyRefused("要併掉的那筆已經取消了")
        extra = list(b.get("cards") or []) + ([b["origin"]["card"]] if (b.get("origin") or {}).get("card") else [])
        have = set(a.get("cards") or []) | ({a["origin"]["card"]} if (a.get("origin") or {}).get("card") else set())
        add_cards = [c for c in dict.fromkeys(extra) if c not in have]
        if add_cards:
            a["cards"] = list(a.get("cards") or []) + add_cards
        _log(a, f"併進 {drop}", by, turn_id)
        old = b.get("status")
        b["status"] = "cancelled"
        _log(b, f"status {old} → cancelled（跟 {keep} 是同一筆）", by, turn_id)
        return (a, b), True
    return _mutate(vault, turn_id, fn, check="write")


COUNT_INEXACT = "算出來的差換不成整數的單位，沒有寫；照她講的單位再說一次實際數量"


def _exact(q):
    """這個數存得下（小數最多 4 位、除得盡）。"""
    return (q * 10000).denominator == 1


def count(vault, product, actual, turn_id, place=None, by="ai"):
    """盤點（小事預設）：照地點算出差多少，記一筆 dir=none（金額 0）不直接改庫存。實際數量可帶單位，照同一套換（D-344 補）：
    差存成她講的那個單位，換不成整數就存基本單位或以前用過的單位，都不行退回（不留小數誤差）；說明寫「盤點，實際 1 箱（24 瓶）」。
    對得上回 (None, 0)；否則 (筆, 差)，差是基本單位的數。"""
    from kb._lib import numbers, profile
    prof, ents = _profile(vault), _ents(vault)
    e = _entity(ents, product)
    m = re.match(r"^\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(\S*)\s*$", str(actual))
    if not m:
        raise MoneyRefused(numbers.BAD_NUMBER.format(actual))
    try:
        n = numbers.parse_number(m.group(1))
    except numbers.NumberRefused as e2:
        raise MoneyRefused(str(e2)) from None
    said = m.group(2) or ""
    where = _place(vault, prof, ents, place, profile.place_types(prof)) if place else None
    k = _key(e["name"])

    def fn(data):
        _check_count(prof, ents, data, {"entity": e["name"], "unit": said})
        units = _units(prof, ents, e["name"], unit_cache(data))  # D-344 三補：只用過盒的照盒算
        real, group = units.to_base(n, said)
        now, used = Fraction(0), []
        for it in _live(data):
            for ln in it.get("lines") or []:
                if _key(ln.get("entity") or "") == k and (not where or ln.get("place") == where):
                    now += units.to_base(ln.get("qty"), ln.get("unit"))[0]
                    used.append(ln.get("unit") or "")
        diff = real - now
        if diff == 0:
            return (None, 0), False
        for u in dict.fromkeys([said, group, *used]):
            f = units.to_base(1, u)
            q = diff / f[0] if f[1] == group else None
            if q is not None and _exact(q):
                break
        else:
            raise MoneyRefused(COUNT_INEXACT)
        said_text = _show_q(n, said) + (f"（{_show_q(real, group)}）" if said and said != group and group else "")
        it = {"id": _new_id(data), "dir": "none", "amount": 0, "status": "done", "date": _today(), "due": None, "entities": [],
              "lines": [{"entity": e["name"], "qty": _num(q), "unit": u, "unit_price": None, "place": where}],
              "advanced_by": None, "text": f"盤點，實際 {said_text}", "note": "", "split_from": None, "repeat": None,
              "repeat_of": None, "source": "confirmed", "origin": None, "cards": [], "created_turn": turn_id, "history": []}
        _log(it, f"盤點：{_show_q(now, group)} → {said_text}", by, turn_id)
        data["items"].append(it)
        return (it, _num(diff)), True
    return _mutate(vault, turn_id, fn, check="write")


# ---------- 算 ----------

def _live(data):
    return [i for i in data["items"] if i.get("status") != "cancelled"]


def _place_names(prof, ents):
    from kb._lib import profile
    types = {_key(t) for t in profile.place_types(prof)}
    if not types:
        return []
    return sorted(e["name"] for e in ents["entities"] if e.get("status") == "active" and _key(e.get("type")) in types)


def stock(vault, entity=None, place=None, data=None, prof=None, ents=None):
    """{"products": [{"name","unit","total","places": {地點: 數},"min","base","apart","text","places_text"}]}：所有沒取消的筆
    （不管收付了沒，新定 5）；每個品項照 Units 換成基本單位（D-344 補），換得了的加在 total、places（單位 unit），換不了的分開放
    apart（[{"unit","qty","places"}]），text 是「12 瓶＋2 箱（不知道一箱幾瓶）」、places_text 是各地點的同一套字；只有換不了的
    一組時 unit 就是那組的單位。places 列每個地點型別的實體（沒動靜的是 0），有沒講地點的進出多一格「未指定」（確認報告 #10）；
    entity 給了只列那個商品，place 給了只列在那裡有進出的、places 只留那一格。"""
    from kb._lib import entities
    data = load(vault) if data is None else data
    prof = _profile(vault) if prof is None else prof
    ents = _ents(vault) if ents is None else ents
    want = _entity(ents, entity)["name"] if entity else None
    at = _entity(ents, place)["name"] if place else None
    cache, per = unit_cache(data), {}
    for it in _live(data):
        for ln in it.get("lines") or []:
            units = _units(prof, ents, ln.get("entity"), cache)
            q, g = units.to_base(ln.get("qty"), ln.get("unit"))
            rec = per.setdefault(ln.get("entity"), {})
            grp = rec.setdefault(g, {"total": Fraction(0), "places": {}})
            grp["total"] += q
            if ln.get("place"):
                grp["places"][ln["place"]] = grp["places"].get(ln["place"], Fraction(0)) + q
    names = _place_names(prof, ents)
    out = []
    for name in sorted(per):
        if want and _key(name) != _key(want):
            continue
        rec = per[name]
        if at and not any(at in g["places"] for g in rec.values()):
            continue
        base = _units(prof, ents, name, cache).main(rec)
        main = _pick_main(rec, base)
        order = [main] + [g for g in rec if g != main]
        where = [at] if at else list(dict.fromkeys(names + [p for g in rec.values() for p in g["places"]]))
        cells = {g: {p: rec[g]["places"].get(p, Fraction(0)) if g in rec else Fraction(0) for p in where} for g in order}
        if names and not at:  # 沒講地點的進出另成「未指定」，各地點加起來才等於合計（確認報告 #10）
            off = {g: rec[g]["total"] - sum(rec[g]["places"].values(), Fraction(0)) if g in rec else Fraction(0) for g in order}
            if any(off.values()):
                where.append(UNPLACED)
                for g in order:
                    cells[g][UNPLACED] = off[g]
        total = rec[main]["total"] if main in rec else Fraction(0)
        e = entities.find(ents, name)[0]
        typ = (e or {}).get("type")
        out.append({"name": name, "unit": main, "total": _num(total),
                    "places": {p: _num(v) for p, v in cells[main].items()},
                    "min": entities.field_value(e, "安全庫存") if e else None, "base": base,
                    "apart": [{"unit": g, "qty": _num(rec[g]["total"]), "places": {p: _num(v) for p, v in cells[g].items()}}
                              for g in order[1:]],
                    "text": groups_text([(g, rec[g]["total"]) for g in order if g in rec], base, prof, typ),
                    "places_text": {p: groups_text([(g, cells[g][p]) for g in order if cells[g][p] or g == main == base], base,
                                                   prof, typ)
                                    for p in where}})
    return {"products": out}


def places_line(row):
    """stock 一列的各地點：「北店 12、南店 5」；那個地點有換不了的才用 places_text（「北店 12 瓶＋2 箱（不知道一箱幾瓶）」）。"""
    from kb._lib import numbers
    return "、".join(f"{p} {row['places_text'][p] if any(a['places'].get(p) for a in row['apart']) else numbers.show(v)}"
                    for p, v in row["places"].items())


def low_stock(vault, names, data=None):
    """合計低於「安全庫存」的商品（D-282；只看合計，D-342 (4)）；沒有安全庫存欄的不列。只比換得到基本單位的那組
    （D-344 補；有換不了的一起帶在 text 裡讓她看到有分開）。"""
    out = []
    keys = {_key(n) for n in names}
    for p in stock(vault, data=data)["products"]:
        if _key(p["name"]) in keys and p["min"] is not None and p["unit"] == p["base"] \
                and Decimal(str(p["total"])) < Decimal(str(p["min"])):
            out.append({"entity": p["name"], "stock": p["total"], "min": p["min"], "unit": p["unit"], "text": p["text"]})
    return out


def _touches(it, k):
    return (any(_key(n) == k for n in it.get("entities") or [])
            or any(_key(ln.get("entity")) == k or _key(ln.get("place") or "") == k for ln in it.get("lines") or []))


def _in_range(day, frm, to):
    return bool(day) and (frm is None or day >= frm) and (to is None or day <= to)


def _price(ents, name, field):
    from kb._lib import entities
    e, _hit = entities.find(ents, name)
    return entities.field_value(e, field) if e else None


MONEY_UNITS = ("元", "塊", "幣", "圓", "$", "NTD", "TWD", "USD")


def _unit_price(prof, ents, units, name, field, calc):
    """售價、進價換成一個 calc 的價（記帳2 測試第 5 輪 #24）：第九節那欄的單位是錢（元、塊、…幣；「元/瓶」看斜線後面）或沒寫，
    就是基本單位的價（照舊）；是商品的單位（瓶）就照 factor 換到 calc，換不了回 None（寧可不算，不拿瓶價乘箱數）。"""
    p = _price(ents, name, field)
    if p is None:
        return None
    u = re.split(r"[/／]", _field_unit(prof, (units.entry or {}).get("type"), field) or "")[-1].strip()
    if not u or u.upper().endswith(MONEY_UNITS):
        return _frac(p)
    if units.own and u == units.type_base:  # D-344 三補：第九節寫瓶、膠囊只用盒 → 照它自己的單位讀
        u = units.base
    f = factor(prof, units.entry, u, calc)
    return None if f is None else _frac(p) / f


def _products(items, ents, prof=None, data=None):
    """賣出（收的那筆、數量是負的）：數量、金額（有單價照單價；一個品項的整筆就是它；多品項沒單價照售價分、標估的；沒售價只報數量）、
    毛利（估）＝賣出數量 ×（售價 − 進價），沒進價不算。分不出的那幾筆不算進金額、筆數放 missing（一筆都算不出 amount 才是 None）。數量照 Units 換成基本單位（D-344 補）：單價是她講那個單位的價（金額＝單價 ×
    她講的數），售價、進價照 _unit_price 換成基本單位的價（第 5 輪 #24，換不了不算）；換不了的數量分開放 apart、不算毛利、不拿來分金額。"""
    prof = prof or {}
    cache, agg, seen = unit_cache({"items": items} if data is None else data), {}, {}
    for it in items:
        for ln in it.get("lines") or []:
            g = _units(prof, ents, ln.get("entity"), cache).to_base(1, ln.get("unit"))[1]
            seen.setdefault(ln.get("entity"), {})[g] = True

    def calc_base(name):
        return _units(prof, ents, name, cache).main(seen.get(name, ()))

    def base_qty(ln):
        units = _units(prof, ents, ln["entity"], cache)
        q, g = units.to_base(-_frac(ln["qty"]), ln.get("unit"))
        return q, g, calc_base(ln["entity"])

    for it in items:
        if it.get("dir") != "in" or it.get("status") != "done":
            continue
        sold = [ln for ln in it.get("lines") or [] if Decimal(str(ln.get("qty") or 0)) < 0]
        if not sold:
            continue
        total = _frac(it.get("amount") or 0)
        priced = sum((_frac(ln["unit_price"]) * -_frac(ln["qty"]) for ln in sold if ln.get("unit_price") is not None), Fraction(0))
        rest = [ln for ln in sold if ln.get("unit_price") is None]
        weights = {}
        for ln in rest:
            q, g, base = base_qty(ln)
            p = _unit_price(prof, ents, _units(prof, ents, ln["entity"], cache), ln["entity"], "售價", base)
            weights[id(ln)] = None if p is None or g != base else p * q
        known = sum((w for w in weights.values() if w is not None), Fraction(0))
        for ln in sold:
            rec = agg.setdefault(ln["entity"], {"groups": {}, "amount": Fraction(0), "estimated": False, "missing": 0, "counted": 0})
            q, g, _base = base_qty(ln)
            rec["groups"][g] = rec["groups"].get(g, Fraction(0)) + q
            rec["counted"] += 1
            if ln.get("unit_price") is not None:
                rec["amount"] += _frac(ln["unit_price"]) * -_frac(ln["qty"])
            elif len(sold) == 1:
                rec["amount"] += total
            elif weights.get(id(ln)) is not None and known and len(rest) == sum(1 for w in weights.values() if w is not None):
                rec["amount"] += (total - priced) * weights[id(ln)] / known
                rec["estimated"] = True
            else:  # 分不出這個品項的錢：只這筆不算，其他照算（確認報告 #7）
                rec["counted"] -= 1
                rec["missing"] += 1
    out = []
    for name, rec in sorted(agg.items()):
        base, calc = _units(prof, ents, name, cache).base, calc_base(name)
        main = _pick_main(rec["groups"], calc)
        order = [main] + [g for g in rec["groups"] if g != main]
        units = _units(prof, ents, name, cache)
        sell, cost = (_unit_price(prof, ents, units, name, f, calc) for f in ("售價", "進價"))
        margin = (_num(rec["groups"][calc] * (sell - cost))
                  if sell is not None and cost is not None and calc in rec["groups"] else None)
        amount = None if not rec["counted"] else (_num(Fraction(round(rec["amount"]))) if rec["estimated"] else _num(rec["amount"]))
        out.append({"name": name, "qty": _num(rec["groups"].get(main, Fraction(0))), "unit": main, "base": base,
                    "apart": [{"unit": g, "qty": _num(rec["groups"][g])} for g in order[1:]],
                    "text": groups_text([(g, rec["groups"][g]) for g in order], base, prof, (units.entry or {}).get("type")),
                    "amount": amount, "estimated": rec["estimated"], "missing": rec["missing"], "margin": margin})
    return out


def _pick(items, entity_key=None, advanced_by=None):
    out = items
    if entity_key:
        out = [i for i in out if _touches(i, entity_key)]
    if advanced_by:
        out = [i for i in out if _key(i.get("advanced_by") or "") == _key(advanced_by)]
    return out


def _sums(vault, items, frm, to, ents):
    from kb._lib import entities, numbers
    tot = {"in": Decimal(0), "out": Decimal(0), "open_in": Decimal(0), "open_out": Decimal(0)}
    by_type, rows = {}, []
    for it in items:
        amt = it.get("amount")
        if it.get("status") == "done" and _in_range(it.get("date"), frm, to) and it.get("dir") in ("in", "out"):
            tot[it["dir"]] += Decimal(str(amt or 0))
            rows.append(it)
            first = (it.get("entities") or [None])[0]
            e = entities.find(ents, first)[0] if first else None
            typ = e.get("type") if e else OTHER
            rec = by_type.setdefault(typ, {}).setdefault(first or OTHER, {"name": first or OTHER, "in": Decimal(0), "out": Decimal(0)})
            rec[it["dir"]] += Decimal(str(amt or 0))
        elif it.get("status") == "open" and it.get("dir") in ("in", "out"):  # 未收付不管到期日在不在區間（確認報告 #1）
            tot["open_" + it["dir"]] += Decimal(str(amt or 0))
            rows.append(it)
    n = config.get(vault, "money_rank_n")
    ranked = {t: [{k: numbers.plain(v) if isinstance(v, Decimal) else v for k, v in r.items()}
                  for r in sorted(rs.values(), key=lambda r: -(r["in"] + r["out"]))[:n]] for t, rs in by_type.items()}
    got = {k: numbers.plain(v) for k, v in tot.items()}
    got["net"] = numbers.plain(tot["in"] - tot["out"])
    return got, ranked, rows


def _diff(a, b):
    out = {}
    for k in ("in", "out", "net"):
        x, y = Decimal(str(a[k])), Decimal(str(b[k]))
        from kb._lib import numbers
        out[k] = {"diff": numbers.plain(x - y), "pct": None if y == 0 else round(float((x - y) / abs(y) * 100), 1)}
    return out


def summary(vault, frm, to, entity=None, vs=None, advanced_by=None):
    """kb money sum（D-284 (4)）：照收付日算收、付、淨；未收、未付是現在還沒收付的全部（不限區間，逾期、沒到期日、以後到期的
    都算，確認報告 #1）；照型別前幾名；
    商品賣出；vs=(frm, to) 另算一段給差與百分比。帳關著時照標過的口述算（statements_summary）。"""
    if mode(vault) == "off":
        return statements_summary(vault, frm, to, entity=entity, vs=vs)
    ents = _ents(vault)
    k = _key(_entity(ents, entity)["name"]) if entity else None
    data = load(vault)
    items = _pick(_live(data), k, advanced_by)
    got, ranked, rows = _sums(vault, items, frm, to, ents)
    out = {"from": frm, "to": to, "source": "money", **got, "by_type": ranked,
           "products": _products([i for i in items if _in_range(i.get("date"), frm, to)], ents, _profile(vault), data), "vs": None, "diff": None,
           "items": sorted(rows, key=lambda i: (_ref_day(i) or "", i.get("id", "")))}
    if vs:
        other, _r, _rows = _sums(vault, items, vs[0], vs[1], ents)
        out["vs"] = {"from": vs[0], "to": vs[1], **other}
        out["diff"] = _diff(got, other)
    return out


def marked_statements(vault, frm=None, to=None, entity=None):
    """帳關著時標過錢的口述（D-343）：active、money 是物件；照口述的日期篩。"""
    from kb._lib import statements
    k = _key(_entity(_ents(vault), entity)["name"]) if entity else None
    out = []
    for s in statements.load(vault)["statements"]:
        if s.get("status") != "active" or not isinstance(s.get("money"), dict) or not _in_range(s.get("date"), frm, to):
            continue
        if k and not any(_key(n) == k for n in s.get("entities") or []):
            continue
        out.append(s)
    return out


def statements_summary(vault, frm, to, entity=None, vs=None):
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
        got = {"in": numbers.plain(tot["in"]), "out": numbers.plain(tot["out"]), "net": numbers.plain(tot["in"] - tot["out"]),
               "open_in": 0, "open_out": 0}
        return got, rows
    got, rows = sums(frm, to)
    out = {"from": frm, "to": to, "source": "statements", **got, "by_type": {}, "products": [], "vs": None, "diff": None,
           "items": rows}
    if vs:
        other, _rows = sums(vs[0], vs[1])
        out["vs"] = {"from": vs[0], "to": vs[1], **other}
        out["diff"] = _diff(got, other)
    return out


def backfill(vault):
    """改開時補帳（D-343）：標過、還沒進帳（money.ledger 是空的）的口述。"""
    return [s for s in marked_statements(vault) if not s["money"].get("ledger")]


def open_items(vault, data=None):
    data = load(vault) if data is None else data
    return sorted((i for i in data["items"] if i.get("status") == "open"), key=lambda i: (i.get("due") or "9999", i.get("id", "")))


def for_entity(vault, name):
    """kb lookup 的 money（D-278 的內容）：合計（累計收、付，未收、未付）、未收付全部、最近 money_recent_n 筆已收付、庫存
    （商品是那個商品各地點與合計；地點型別的實體是那裡的各商品）。"""
    from kb._lib import numbers, profile
    ents, prof = _ents(vault), _profile(vault)
    e = _entity(ents, name)
    data = load(vault)
    k = _key(e["name"])
    items = [i for i in _live(data) if _touches(i, k)]
    tot = {"in": Decimal(0), "out": Decimal(0), "open_in": Decimal(0), "open_out": Decimal(0)}
    for it in items:
        if it.get("dir") not in ("in", "out"):
            continue
        key_ = it["dir"] if it.get("status") == "done" else "open_" + it["dir"]
        tot[key_] += Decimal(str(it.get("amount") or 0))
    done = sorted((i for i in items if i.get("status") == "done" and i.get("dir") in ("in", "out")),
                  key=lambda i: (i.get("date") or "", i.get("id", "")), reverse=True)
    out = {k2: numbers.plain(v) for k2, v in tot.items()}
    out.update(items=len(items), open=open_items(vault, {"items": items}), recent=done[:config.get(vault, "money_recent_n")],
               total_done=len(done))
    if _key(e.get("type")) in {_key(t) for t in profile.place_types(prof)}:
        out["stock"] = stock(vault, place=e["name"], data=data, prof=prof, ents=ents)["products"]
    else:
        rows = stock(vault, e["name"], data=data, prof=prof, ents=ents)["products"]
        out["stock"] = rows[0] if rows else None
    return out


def owed_to(vault, member, data=None):
    """墊的人還沒還的（D-338）：{"member","total","items"}。"""
    from kb._lib import numbers
    data = load(vault) if data is None else data
    rows = [i for i in data["items"] if i.get("status") == "open" and _key(i.get("advanced_by") or "") == _key(member)]
    total = sum((Decimal(str(i.get("amount") or 0)) for i in rows), Decimal(0))
    return {"member": member, "total": numbers.plain(total), "items": rows}


def related_by_date(vault, names, day, exclude=()):
    """D-273 (3)：同一個實體、到期日是那天、還沒收付的帳（日程改日期時列出來問）。"""
    if not day or not exists(vault):
        return []
    keys = {_key(n) for n in names or []}
    return [i for i in load(vault)["items"] if i.get("status") == "open" and i.get("due") == day and i.get("id") not in exclude
            and keys and any(_key(n) in keys for n in i.get("entities") or [])]


def related_agenda(vault, names, day):
    """D-273 (3)：同一個實體、日期是那天、還開著的日程（改帳的到期日時列出來問）。"""
    from kb._lib import agenda
    if not day:
        return []
    keys = {_key(n) for n in names or []}
    return [i for i in agenda.load(vault)["items"] if i.get("status") == "open" and i.get("date") == day
            and keys and any(_key(n) in keys for n in i.get("entities") or [])]


def related_line(it):
    """「信義王還有一筆 11/30 到期的尾款 120,000」。"""
    from kb._lib import agenda, numbers
    who = "、".join(it.get("entities") or []) or "帳裡"
    amt = numbers.show(it["amount"]) if it.get("amount") is not None else "金額未知"
    return f"{who}還有一筆 {agenda.md_of(it['due'])} 到期的{it.get('text') or DIR_TEXT.get(it.get('dir'), '')} {amt}"


# ---------- 成功句 ----------

def _qty_text(q, unit):
    q = _frac(q)
    return (MINUS if q < 0 else "+") + _show_q(abs(q), unit)


REMOVED_MARK = "（已拿掉）"


def _label(ents, name):
    """帳裡的名字；那個實體拿掉了加註「（已拿掉）」（記帳 2 要做的事 1：名字留著、錢不刪）。"""
    from kb._lib import entities
    e = entities.find(ents, name)[0] if name else None
    if e is not None and e.get("status") == "removed" and _key(e.get("name")) == _key(name):
        return f"{name}{REMOVED_MARK}"
    return name


def describe(vault, it, prof=None, ents=None, cache=None):
    """「10/30 收 5,000 賣出（李小姐；精華液 −2 瓶、膠囊 −1 瓶，北店）」；列很多筆的呼叫端把 prof、ents 讀一次帶進來。
    品項的數量照 line_text（D-344 補：「精華液 +2 箱（48 瓶）」）。"""
    from kb._lib import agenda, numbers
    prof = _profile(vault) if prof is None else prof
    ents = _ents(vault) if ents is None else ents
    amt = numbers.show(it["amount"]) if it.get("amount") is not None else "金額未知"
    d = it.get("dir")
    if d == "none":
        head = f"{agenda.md_of(it['date'])} {it.get('text') or '數量'}"
    elif it.get("status") != "done" or not it.get("date"):
        head = f"{'未收' if d == 'in' else '未付'} {amt}" + (f"，{agenda.md_of(it['due'])} 到期" if it.get("due") else "")
        if it.get("text"):
            head += f" {it['text']}"
    else:
        head = f"{agenda.md_of(it['date'])} {DIR_TEXT[d]} {amt}" + (f" {it['text']}" if it.get("text") else "")
    parts = [_label(ents, n) for n in it.get("entities") or []]
    lines = it.get("lines") or []
    places = {ln.get("place") for ln in lines}
    one = len(places) == 1 and None not in places
    cache = unit_cache(load(vault)) if cache is None else cache
    shown = [f"{_label(ents, ln['entity'])} {line_text(prof, ents, ln, cache)}"
             + (f"（{_label(ents, ln['place'])}）" if ln.get("place") and not one else "") for ln in lines]
    inside = "；".join(x for x in ["、".join(parts), "、".join(shown) + (f"，{_label(ents, lines[0]['place'])}" if one else "")] if x)
    if it.get("advanced_by"):
        inside += f"{'；' if inside else ''}{it['advanced_by']}墊的"
    return head + (f"（{inside}）" if inside else "")


TELL_LISTED = 5


def tell_lines(vault, turn_id=None):
    """回答末尾說的（照 agenda.tell_lines）：這一回合從檔案記的帳。讀不到就沒有。"""
    from kb._lib import agenda
    turn_id = turn_id or agenda.current_turn(vault)
    if not turn_id or not exists(vault):
        return []
    try:
        items = load(vault)["items"]
    except ValueError:
        return []
    added = [i for i in items if i.get("created_turn") == turn_id and (i.get("origin") or {}).get("card")
             and i.get("status") != "cancelled"]
    if not added:
        return []
    if len(added) > TELL_LISTED:
        return [f"我從檔案記了 {len(added)} 筆帳，說「列帳」我念給你聽"]
    prof, ents = _profile(vault), _ents(vault)
    return [f"我從檔案記了 {len(added)} 筆帳：" + "、".join(describe(vault, i, prof, ents) for i in added)]


# ---------- 製卡抽錢（D-343、D-140 那套） ----------

def _scan_value(fp, on):
    return fp if on else fp + FIELDS_SUFFIX


def unscanned(vault, data=None, groups=None, entries=None, skip=()):
    """money_unscanned：active 的檔案卡、清冊的 money_fp 跟這次該有的不一樣。開著時要 fp 本身；不是開的時候只抽數字欄，
    fp 或 fp:fields 都算抽過（改成開時只抽過數字欄的重列）；不是開、又沒有數字欄的不列（零成本）。每項同 agenda.unscanned，
    existing 是出處是這張卡、沒取消的帳。"""
    from kb._lib import agenda, cards, inventory
    prof = _profile(vault)
    on = mode(vault, prof) == "on"
    if not on and not (prof.get("fields") or []):
        return {"money_unscanned": []}
    data = load(vault) if data is None else data
    groups = cards._scan_groups(vault)[0] if groups is None else groups
    entries = inventory.load(vault)["entries"] if entries is None else entries
    by_id = {e.get("id"): e for e in entries}
    out = []
    for cid, group in sorted(groups.items(), key=lambda kv: kv[1][0]["path"]):
        entry, card = by_id.get(cid), group[0]
        fp = agenda.card_fp(card["front"])
        if entry is None or entry.get("status") != "active" or not fp or cid in skip or entry.get("archived"):
            continue
        have = entry.get("money_fp")
        if have == fp or (not on and have == _scan_value(fp, False)):
            continue
        existing = [{"id": i["id"], "dir": i.get("dir"), "amount": i.get("amount"), "date": _ref_day(i), "text": i.get("text")}
                    for i in data["items"] if i.get("status") != "cancelled" and (i.get("origin") or {}).get("card") == cid]
        title = card["front"].get("標題")
        out.append({"id": cid, "card_path": card["path"], "title": title if isinstance(title, str) else "", "existing": existing})
    return {"money_unscanned": out}


def mark_scanned(vault, card_id, turn_id):
    """kb money scanned：這張卡的錢與數字欄抽過了，清冊記指紋（不是開的時候記 fp:fields）。"""
    from kb._lib import agenda, inventory
    try:
        card = agenda._card(vault, card_id, no_card="這份還沒有檔案卡，先寫卡再抽")
    except agenda.AgendaRefused as e:
        raise MoneyRefused(str(e)) from None
    fp = agenda.card_fp(card["front"])
    changed = inventory.set_money_fp(vault, card_id, _scan_value(fp, mode(vault) == "on"), turn_id)
    title = card["front"].get("標題")
    return {"id": card_id, "title": title if isinstance(title, str) else "", "fp": fp, "changed": changed}


# ---------- 跟著動（實體改名合併、purge；不看開關） ----------

def rename_entity(vault, mapping, turn_id, undo=None):
    """entities.rename／merge 呼叫：entities、lines.entity、lines.place 裡 key 在 mapping 的換成新名。沒有帳什麼都不做。
    拿掉的實體不叫這裡：帳裡的名字留著（錢不刪）。"""
    if not exists(vault):
        return []

    def swap(n):
        return mapping.get(_key(n), n) if n else n

    def fn(data):
        hits = []
        for it in data["items"]:
            names = it.get("entities") or []
            lines = it.get("lines") or []
            if not any(_key(n) in mapping for n in names) and not any(
                    _key(ln.get("entity") or "") in mapping or _key(ln.get("place") or "") in mapping for ln in lines):
                continue
            new = []
            for n in names:
                n = swap(n)
                if all(_key(x) != _key(n) for x in new):
                    new.append(n)
            it["entities"] = new
            for ln in lines:
                ln["entity"], ln["place"] = swap(ln.get("entity")), swap(ln.get("place"))
            hits.append(it)
        if hits and undo is not None:
            undo.file(MONEY_POSIX)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)


def purge_cards(vault, card_ids, turn_id):
    """purge 清掉這些卡：出處是這些卡、來源推測的整筆拿掉；她說過的（confirmed、corrected）留著、出處清掉。回動到幾筆。"""
    if not card_ids or not exists(vault):
        return 0

    def fn(data):
        n = drop_cards(data, set(card_ids))
        return n, bool(n)
    return _mutate(vault, turn_id, fn)


def drop_cards(data, ids):
    """purge_cards 與回合夾舊版共用（purge.JSON_DROPS）：改 data，回動到幾筆；形狀不對回 0。"""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return 0
    n, keep = 0, []
    for it in data["items"]:
        if not isinstance(it, dict):
            keep.append(it)
            continue
        card = (it.get("origin") or {}).get("card") if isinstance(it.get("origin"), dict) else None
        extra = [c for c in it.get("cards") or [] if c in ids]
        if card in ids and it.get("source") == "inferred":
            n += 1
            continue
        if card in ids:
            it["origin"] = None
        if extra:
            it["cards"] = [c for c in it.get("cards") or [] if c not in ids]
        if card in ids or extra:
            n += 1
        keep.append(it)
    data["items"] = keep
    return n


# ---------- 帳頁（記帳 3，D-283、D-284、D-342、D-280） ----------

MD_POSIX = "系統/帳.md"
YEAR_DIR = "系統/帳"
PAGE_NOTE = "（程式產生，改了下一句話會照你改的更新；報表與庫存的數字是算的，改了不算）"
YEAR_NOTE = "（程式產生，改了下一句話會照你改的更新；報表的數字是算的，改了不算）"
OPEN_TITLE = "還沒收付的"
STOCK_TITLE = "庫存"
QUARTER_TITLE = "每季（淨）"
YEARS_TITLE = "每年"
FULL_YEAR = "全年"
QUARTERS = ("第一季", "第二季", "第三季", "第四季")
TOTAL_PREFIX = "合計："
DETAIL_HEAD = "> [!note]- 明細（{n} 筆）"
NOTHING = "（沒有）"
EMPTY_YEAR = "（這年沒有收付的帳）"
UNKNOWN_AMOUNT = "（金額未知）"
INFERRED_MARK = "〔推測〕"
HAND_BY = "hand"
WEEK = "一二三四五六日"
OVERVIEW_IGNORE = (STOCK_TITLE, QUARTER_TITLE, YEARS_TITLE)  # 手改比對不看的段（數字是算的）
YEAR_IGNORE = (FULL_YEAR,)
_YEAR_REL = re.compile(r"^系統/帳/(\d{4})\.md$")
_MD = re.compile(r"(?<![0-9/])(\d{1,2})/(\d{1,2})(?![0-9/])")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_WDAY = re.compile(r"（週?[一二三四五六日]）")
_LATE = re.compile(r"\s*已過 \d+ 天\s*$")
_AMT = re.compile(r"(?<=[收付] )[0-9][0-9,]*(?:\.[0-9]+)?")


def year_rel(year):
    return f"{YEAR_DIR}/{year}.md"


def is_page(rel):
    """帳的頁：總覽或「系統/帳/ 底下」的年頁（不一頁一頁列，D-284）。"""
    rel = str(rel or "").replace("\\", "/")
    return rel == MD_POSIX or bool(_YEAR_REL.match(rel))


def page_rels(vault):
    """磁碟上現有的帳頁（總覽在前、年頁照年）。"""
    out = [MD_POSIX] if (Path(vault) / MD_POSIX).is_file() else []
    folder = Path(vault) / YEAR_DIR
    if folder.is_dir():
        out += sorted(f"{YEAR_DIR}/{f.name}" for f in folder.glob("*.md") if _YEAR_REL.match(f"{YEAR_DIR}/{f.name}"))
    return out


def _wd(day):
    d = dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}（{WEEK[d.weekday()]}）"


def agenda_md(day):
    d = dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}"


def _amt_text(it):
    from kb._lib import numbers
    return numbers.show(it["amount"]) if it.get("amount") is not None else None


def _who(it, link):
    return "、".join(link(n) for n in it.get("entities") or [])


def _items_text(it, prof, ents, times=False, cache=None):
    """品項：「精華液 +2 箱（48 瓶）、膠囊 −1 瓶，北店」照 line_text（D-344 補）；times：卡底下那段寫「精華液 ×2」，
    講的不是基本單位的帶上她講的單位（「×1 箱」）。"""
    lines = it.get("lines") or []
    if not lines:
        return ""
    places = {ln.get("place") for ln in lines}
    one = len(places) == 1 and None not in places
    shown, cache = [], {} if cache is None else cache
    for ln in lines:
        name = _label(ents, ln.get("entity"))
        if times:
            unit = ln.get("unit") or ""
            unit = unit if unit != _units(prof, ents, ln.get("entity"), cache).base else ""
            q = f"{name} ×{_show_q(abs(_frac(ln.get('qty'))), unit)}"
        else:
            q = f"{name} {line_text(prof, ents, ln, cache)}"
        shown.append(q + (f"（{_label(ents, ln['place'])}）" if ln.get("place") and not one else ""))
    return "、".join(shown) + (f"，{_label(ents, lines[0]['place'])}" if one else "")


def late_days(it, today):
    due = it.get("due")
    if it.get("status") != "open" or not due or not today or due >= today:
        return 0
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(due)).days


def open_line(it, link, today):
    """總覽「還沒收付的」一行：「- 11/20（五）收 50,000 [[…|信義王]] 第二期　已過 3 天」；報帳「- 付 3,200 文具（小王墊）」；
    金額未知「- （金額未知）收 [[…|王先生]] 尾款　到期 12/31」。"""
    d = DIR_TEXT.get(it.get("dir"), "")
    tail = " ".join(x for x in (_who(it, link), it.get("text") or "") if x)
    adv = f"（{it['advanced_by']}墊）" if it.get("advanced_by") else ""
    amt = _amt_text(it)
    if amt is None:
        due = f"　到期 {agenda_md(it['due'])}" if it.get("due") else ""
        return f"- {UNKNOWN_AMOUNT}{d}" + (f" {tail}" if tail else "") + adv + due
    late = late_days(it, today)
    return (f"- {_wd(it['due']) if it.get('due') else ''}{d} {amt}" + (f" {tail}" if tail else "") + adv
            + (f"　已過 {late} 天" if late else ""))


def agenda_line(it, link):
    """日程頁上帳的到期件（D-273（2））：「- 2026-11-20（週五）收 50,000 [[…|信義王]] 第二期」，跟日程那行同一個寫法。"""
    from kb._lib import agenda
    d = DIR_TEXT.get(it.get("dir"), "")
    amt = _amt_text(it) or "金額未知"
    tail = " ".join(x for x in (_who(it, link), it.get("text") or "") if x)
    adv = f"（{it['advanced_by']}墊）" if it.get("advanced_by") else ""
    return f"- {agenda.with_weekday(it['due'])}{d} {amt}" + (f" {tail}" if tail else "") + adv


def detail_line(it, link, prof, ents, by_id=None, cache=None):
    """年頁明細一行（callout 裡）：「> - 11/5（四）收 5,000 [[…|李小姐]] 賣出（精華液 −2 瓶，北店；小王墊；拆自 10/30 那筆）」。"""
    by_id = by_id or {}
    if it.get("dir") == "none":
        body = it.get("text") or "數量"
    else:
        body = f"{DIR_TEXT.get(it.get('dir'), '')} {_amt_text(it) or '金額未知'}"
        who = _who(it, link)
        body += (f" {who}" if who else "") + (f" {it['text']}" if it.get("text") else "")
    inside = [x for x in (_items_text(it, prof, ents, cache=cache),) if x]
    if it.get("advanced_by"):
        inside.append(f"{it['advanced_by']}墊")
    src = by_id.get(it.get("split_from")) if it.get("split_from") else None
    if src is not None and _ref_day(src):
        day = _ref_day(src)
        same = (it.get("date") or "")[:4] == day[:4]
        inside.append(f"拆自 {agenda_md(day)} 那筆" + ("" if same else f"（[[{year_rel(day[:4])[:-3]}|{day[:4]}]]）"))
    mark = ""
    if it.get("source") == "inferred":
        card = (it.get("origin") or {}).get("card")
        mark = INFERRED_MARK + (f"〔{card}〕" if card else "")
    return f"> - {_wd(it['date'])}{body}" + (f"（{'；'.join(inside)}）" if inside else "") + mark


def _skip(line):
    """帳頁上算出來的行（表、合計、明細的標題）：她改了不算，下次照帳重算。"""
    s = line[2:] if line.startswith("> ") else line
    return s.startswith(("|", TOTAL_PREFIX, "[!note]"))


def _total_line(got):
    from kb._lib import numbers
    return TOTAL_PREFIX + "｜".join([f"收 {numbers.show(got['in'])}", f"付 {numbers.show(got['out'])}",
                                     f"淨 {numbers.show(got['net'])}"])


def _type_tables(items, ents, link, rank_n):
    """照型別的小表：每個型別前 rank_n 名，其他合成一行；沒掛實體的錢歸「其他」（D-283）。"""
    from kb._lib import agenda, entities, numbers
    by_type = {}
    for it in items:
        if it.get("status") != "done" or it.get("dir") not in ("in", "out"):
            continue
        first = (it.get("entities") or [None])[0]
        e = entities.find(ents, first)[0] if first else None
        typ = (e.get("type") if e else None) or OTHER  # 沒型別的實體也歸「其他」，不讓排序壞掉
        rec = by_type.setdefault(typ, {}).setdefault(first or OTHER, {"in": Decimal(0), "out": Decimal(0)})
        rec[it["dir"]] += Decimal(str(it.get("amount") or 0))
    out = []
    for typ in sorted(by_type, key=lambda t: (t == OTHER, t)):
        rows = sorted(by_type[typ].items(), key=lambda kv: (-(kv[1]["in"] + kv[1]["out"]), kv[0]))
        top, rest = rows[:rank_n], rows[rank_n:]
        lines = [f"### {typ}", f"| {typ} | 收 | 付 |", "|---|---|---|"]
        for name, r in top:
            label = name if name == OTHER else link(name)
            lines.append(f"| {label} | {numbers.show(r['in'])} | {numbers.show(r['out'])} |")
        if rest:
            lines.append(f"| 其他（{len(rest)}） | {numbers.show(sum((r['in'] for _n, r in rest), Decimal(0)))} | "
                         f"{numbers.show(sum((r['out'] for _n, r in rest), Decimal(0)))} |")
        out.append("\n".join(agenda.table_cell(x) for x in lines))  # 表格裡的連結 | 要跳開（確認 #4）
    return out


def missing_text(p):
    return f"（{p['missing']} 筆沒算進去）" if p.get("missing") and p.get("amount") is not None else ""


def _product_table(products, link):
    """商品賣出：數量、金額（多品項沒單價的標「估」，有分不出的標「N 筆沒算進去」）、毛利（估）；整月都沒有毛利就不出那欄（D-284 (3)）。"""
    from kb._lib import agenda, numbers
    if not products:
        return ""
    margin = any(p.get("margin") is not None for p in products)
    head = "| 商品 | 賣出 | 金額 |" + (" 毛利（估） |" if margin else "")
    lines = ["### 商品", head, "|---|---|---|" + ("---|" if margin else "")]
    for p in products:
        amt = "" if p.get("amount") is None else numbers.show(p["amount"]) + ("（估）" if p.get("estimated") else "") + missing_text(p)
        plain = not p.get("apart") and p.get("unit") == p.get("base")  # D-344 補：基本單位的照舊只寫數，其他寫單位
        row = f"| {link(p['name'])} | {numbers.show(p['qty']) if plain else p['text']} | {amt} |"
        if margin:
            row += f" {'' if p.get('margin') is None else numbers.show(p['margin'])} |"
        lines.append(agenda.table_cell(row))
    return "\n".join(lines)


def _stock_table(data, prof, ents, link):
    """庫存表：商品 × 地點，最後一欄合計；沒有地點型別的只有合計；地點一筆都沒有也列 0（D-342）。數字照 stock 的 text、
    places_text（D-344 補：換不了的分開列）。"""
    from kb._lib import agenda
    rows = stock(None, data=data, prof=prof, ents=ents)["products"]
    if not rows:
        return NOTHING
    names = _place_names(prof, ents)
    if any(UNPLACED in r["places"] for r in rows):
        names.append(UNPLACED)
    lines = ["| 商品 | " + "".join(f"{n} | " for n in names) + "合計 |", "|---|" + "---|" * (len(names) + 1)]
    for r in rows:
        zero = _show_q(Fraction(0), r.get("unit") or "")
        cells = "".join(f"{r['places_text'].get(n, zero)} | " for n in names)
        lines.append(agenda.table_cell(f"| {link(r['name'])} | {cells}{r['text']} |"))
    return "\n".join(lines)


def _quarter_of(day):
    return (int(day[5:7]) - 1) // 3


def _quarter_table(live, today):
    """每季（淨）：最近三年（今年與前兩年）；同一季同一列，加全年；今年全年那格標「到 <最後一筆的日期>」。"""
    from kb._lib import numbers
    this = int(today[:4])
    years = [this - 2, this - 1, this]
    net = {}
    last = None
    for it in live:
        day = it.get("date")
        if it.get("status") != "done" or it.get("dir") not in ("in", "out") or not day or int(day[:4]) not in years:
            continue
        sign = 1 if it["dir"] == "in" else -1
        for key in ((int(day[:4]), _quarter_of(day)), (int(day[:4]), None)):
            net[key] = net.get(key, Decimal(0)) + sign * Decimal(str(it.get("amount") or 0))
        if int(day[:4]) == this and (last is None or day > last):
            last = day
    lines = ["| 季 | " + " | ".join(str(y) for y in years) + " |", "|---|" + "---|" * len(years)]
    for q, label in list(enumerate(QUARTERS)) + [(None, FULL_YEAR)]:
        cells = []
        for y in years:
            v = net.get((y, q))
            cell = "" if v is None else numbers.show(v)
            if q is None and y == this and v is not None and last:
                cell += f"（到 {agenda_md(last)}）"
            cells.append(cell)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _years(vault, data):
    """要畫的年：帳裡有收付日的年（含取消的，取消光了那頁要換成空的）、加上磁碟上已有的年頁；新的在前。"""
    have = {i["date"][:4] for i in data["items"] if isinstance(i.get("date"), str) and re.match(r"\d{4}-", i["date"])}
    have |= {_YEAR_REL.match(r).group(1) for r in page_rels(vault) if _YEAR_REL.match(r)}
    return sorted(have, reverse=True)


def _month_end(month):
    first = dt.date.fromisoformat(month + "-01")
    return ((first + dt.timedelta(days=31)).replace(day=1) - dt.timedelta(days=1)).isoformat()


def render_pages(vault, data, today=None, link=None, prof=None, ents=None):
    """{頁: (文字, [(條目那行, 編號)])}：總覽與每一年。只用帶進來的 data（purge 照舊版重畫也用），不讀磁碟上的帳。"""
    from kb._lib import agenda, numbers, renders
    today = today or _today()
    prof = _profile(vault) if prof is None else prof
    ents = _ents(vault) if ents is None else ents
    link = agenda.linker(vault, ents, mark_removed=True) if link is None else link
    live = _live(data)
    by_id = {i.get("id"): i for i in data["items"]}
    ucache = unit_cache(data)
    years = _years(vault, data)
    pages = {}
    opens = open_items(vault, {"items": live})
    open_rows = [(open_line(i, link, today), i.get("id")) for i in opens if i.get("dir") in ("in", "out")]
    year_lines = []
    for y in years:
        got, _r, _rows = _sums(vault, live, f"{y}-01-01", f"{y}-12-31", ents)
        year_lines.append(f"- [[{year_rel(y)[:-3]}|{y}]]　收 {numbers.show(got['in'])}｜付 {numbers.show(got['out'])}｜"
                          f"淨 {numbers.show(got['net'])}")
    text = (f"# 帳\n{PAGE_NOTE}\n\n## {OPEN_TITLE}\n" + ("\n".join(r for r, _i in open_rows) or NOTHING)
            + f"\n\n## {STOCK_TITLE}\n" + _stock_table(data, prof, ents, link)
            + f"\n\n## {QUARTER_TITLE}\n" + _quarter_table(live, today)
            + f"\n\n## {YEARS_TITLE}\n" + ("\n".join(year_lines) or NOTHING) + "\n")
    pages[MD_POSIX] = (text + renders.carried_section(vault, MD_POSIX), open_rows)
    n = config.get(vault, "money_rank_n")
    for y in years:
        rel = year_rel(y)
        frm, to = f"{y}-01-01", f"{y}-12-31"
        done = sorted((i for i in live if i.get("status") == "done" and _in_range(i.get("date"), frm, to)),
                      key=lambda i: (i.get("date") or "", i.get("id", "")))
        rows = []
        parts = [f"# 帳 {y}\n{YEAR_NOTE}\n"]
        if not done:
            parts.append(EMPTY_YEAR + "\n")
        else:
            got, _r, _rows = _sums(vault, done, frm, to, ents)
            block = [f"## {FULL_YEAR}\n{_total_line(got)}"] + _type_tables(done, ents, link, n)
            prod = _product_table(_products(done, ents, prof, data), link)
            if prod:
                block.append(prod)
            parts.append("\n\n".join(block) + "\n")
            for month in sorted({i["date"][:7] for i in done}, reverse=True):
                mine = [i for i in done if i["date"][:7] == month]
                mgot, _r, _rows = _sums(vault, mine, month + "-01", _month_end(month), ents)
                block = [f"## {int(month[5:])} 月\n{_total_line(mgot)}"] + _type_tables(mine, ents, link, n)
                prod = _product_table(_products(mine, ents, prof, data), link)
                if prod:
                    block.append(prod)
                lines = [detail_line(i, link, prof, ents, by_id, ucache) for i in mine]
                rows += [(ln, i.get("id")) for ln, i in zip(lines, mine)]
                block.append(DETAIL_HEAD.format(n=len(mine)) + "\n" + "\n".join(lines))
                parts.append("\n\n".join(block) + "\n")
        pages[rel] = ("\n".join(parts) + renders.carried_section(vault, rel), rows)
    return pages


def render_text(vault, data, today=None):
    """總覽的文字（purge 照舊版重畫用）。"""
    return render_pages(vault, data, today)[MD_POSIX][0]


def render(vault, turn_id=None, data=None, today=None, hand=None, skip=()):
    """寫帳頁：只寫內容變了的頁（舊年份的頁只有那年的帳變了才寫）；有 turn_id 先備進回合；寫完記指紋與條目。回寫了哪幾頁。
    手改已經由呼叫端處理過就帶 hand（_mutate 在鎖裡）；沒帶先照 apply_hand 處理一遍。skip 是這次不重寫的頁（她那份沒存成）。"""
    from kb._lib import hand as hand_lib, renders
    from kb._lib.io import write_text_atomic
    if hand is None:
        if apply_hand(vault, turn_id) == hand_lib.KEEP_FAILED:
            return []
        data = None  # 手改套用可能改了資料：重讀
    data = load(vault) if data is None else data
    today = today or _today()
    written = []
    for rel, (text, lines) in render_pages(vault, data, today).items():
        if rel in set(skip):
            continue
        target = Path(vault) / rel
        try:
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                renders.record(vault, rel, text, day=today, lines=lines)
                continue
        except (OSError, UnicodeDecodeError):
            pass
        if turn_id:
            from kb._lib import turn
            turn.backup(vault, rel, turn_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, text)
        renders.record(vault, rel, text, day=today, lines=lines)
        written.append(rel)
    return written


def apply_hand(vault, turn_id):
    """kb renders apply 系統/帳.md（或年頁）：照她在帳頁上改的做（所有帳頁一起）。回報告、None 或 hand.KEEP_FAILED。"""
    out = []
    _mutate(vault, turn_id, lambda data: (None, False), hand_out=out)
    return out[0] if out else None


def _norm(line):
    return _LATE.sub("", _WDAY.sub("", line))


def _shape(line):
    return _AMT.sub("$", _MD.sub("#", _ISO.sub("#", line)))


def hand_change(orig, body):
    """她改了某行的字：只改了日期或金額就回 {"date": 她寫的新日期或 None, "amount": 新金額或 None}，否則 None。"""
    a, b = _norm(orig), _norm(body)
    if a == b or _shape(a) != _shape(b):
        return None
    da = _ISO.findall(a) + ["/".join(x) for x in _MD.findall(_ISO.sub("#", a))]
    db = _ISO.findall(b) + ["/".join(x) for x in _MD.findall(_ISO.sub("#", b))]
    ma, mb = _AMT.findall(a), _AMT.findall(b)
    if da[1:] != db[1:] or ma[1:] != mb[1:]:  # 只認那筆自己的日期與金額（每行第一個）；別處的數字（拆自、說明裡的）交給 AI
        return None
    out = {"date": db[0] if da[:1] != db[:1] and db else None, "amount": mb[0] if ma[:1] != mb[:1] and mb else None}
    return out if out["date"] or out["amount"] else None


def _hand_day(text, it):
    """她寫的日期（YYYY-MM-DD 或 M/D、M-D：沒寫年用那筆原本的年）→ ISO。"""
    from kb._lib import agenda
    try:
        return agenda._hand_date(text, {"date": it.get("date") or it.get("due") or _today()})
    except agenda.AgendaRefused as e:
        raise MoneyRefused(str(e)) from None


def short(it):
    """工作日誌與回報裡說的那筆：「11/5 收 5,000 李小姐 賣出」。"""
    day = it.get("date") or it.get("due")
    parts = [agenda_md(day) if day else "", DIR_TEXT.get(it.get("dir"), ""), _amt_text(it) or "",
             "、".join(it.get("entities") or []), it.get("text") or ""]
    return " ".join(x for x in parts if x)


def _strip(line):
    return line[2:] if line.startswith("- ") else line


def _apply_page(data, got, lines, turn_id, off=False):
    """帳頁的手改在資料上做（歷程 by hand）：刪掉一行＝取消、改日期（已收付的改收付日、還沒的改到期日）、改金額（新定 1）。
    做不了的記成認不出的（交給 AI，D-299）；改了字的認得出就從 got["add"] 拿掉。回 (做了什麼, 認不出的)。"""
    from kb._lib import renders
    by_id = {i.get("id"): i for i in data["items"]}
    by_line = {_strip(renders.unlink(ln)): pid for ln, pid in lines}
    applied, extra = [], []

    def edit(it, change):
        new = {}
        if change.get("date"):
            new["due" if it.get("status") == "open" else "date"] = _hand_day(change["date"], it)
        if change.get("amount"):
            new["amount"] = _amount(change["amount"])
        _r, changed = _edit_fn(it["id"], new, turn_id, HAND_BY)(data)
        if changed:
            applied.append(f"改了『{short(it)}』")

    if off:  # 帳關著（D-343）：帳頁上的改動不照做，整段交給 AI（同日程頁上帳的那行被拒的做法）
        why = {"checked": "你打了勾", "removed": "你刪掉了", "changed_date": "你改了日期"}
        extra = [{"kind": "changed", "text": f"{_strip(c['line'])}（{why[k]}；帳是關的）", "orig": _strip(c["line"])}
                 for k in ("checked", "removed", "changed_date") for c in got[k]]
        return [], extra

    for c in got["checked"]:
        extra.append({"kind": "changed", "text": f"{_strip(c['line'])}（你打了勾）", "orig": _strip(c["line"])})
    for c in got["removed"]:
        it = by_id.get(c["id"])
        if it is not None and it.get("status") != "cancelled":
            _status_fn(it["id"], "cancelled", turn_id, HAND_BY)(data)
            applied.append(f"取消『{short(it)}』")
    for c in got["changed_date"]:
        it = by_id.get(c["id"])
        if it is None or it.get("status") == "cancelled":
            continue
        try:
            edit(it, {"date": c["to"]})
        except (MoneyRefused, ValueError):
            extra.append({"kind": "changed", "text": f"{_strip(c['line'])}（你改的日期：{c['to']}）", "orig": _strip(c["line"])})
    keep = []
    for a in got["add"]:
        it = by_id.get(by_line.get(a.get("orig"))) if a.get("kind") == "changed" else None
        change = hand_change(a["orig"], a["text"]) if it is not None and it.get("status") != "cancelled" else None
        if change is None:
            keep.append(a)
            continue
        try:
            edit(it, change)
        except (MoneyRefused, ValueError):
            keep.append(a)
    got["add"] = keep
    return applied, extra


def _merge_reports(reports):
    """幾頁的報告合成一份；有一頁她那份沒存成（KEEP_FAILED）就整份報 KEEP_FAILED（呼叫端這次不重寫、不往下做）。"""
    from kb._lib import hand as hand_lib
    if hand_lib.KEEP_FAILED in reports:
        return hand_lib.KEEP_FAILED
    dicts = [r for r in reports if isinstance(r, dict)]
    if dicts:
        return {"applied": [x for r in dicts for x in r["applied"]], "carried": [x for r in dicts for x in r["carried"]],
                "dropped": sum(r["dropped"] for r in dicts), "saved": dicts[0]["saved"]}
    return hand_lib.UNRECOGNIZED if hand_lib.UNRECOGNIZED in reports else None


def _consume_hand(vault, data, turn_id):
    """帳頁跟程式上次寫的不一樣：一頁一頁比出她改了什麼（照 agenda._consume_hand；比的是上次真的寫進去的那份），
    認得出的做在 data 上。回 (合起來的報告, 她那份沒存成、這次不重寫的頁)。"""
    from kb._lib import hand, pending, renders
    reports, failed = [], set()
    ids = {i.get("id") for i in data["items"]}
    off = None
    for rel in page_rels(vault):
        hers = renders.check(vault, rel)
        if hers is None:
            continue
        if off is None:  # 有被手改的頁才讀開關
            off = mode(vault) == "off"
        base_text, lines = renders.base(vault, rel) or ("", [])
        ignore = OVERVIEW_IGNORE if rel == MD_POSIX else YEAR_IGNORE

        def run(st, rel=rel, hers=hers, base_text=base_text, lines=lines, ignore=ignore):
            return hand.consume(vault, rel, hers, base_text, lines, ids, lambda g: _apply_page(data, g, lines, turn_id, off),
                                ignore_sections=ignore, state=st, skip=_skip)
        got = hand.guarded(vault, rel, hers, data, run,
                           lambda saved, diff, rel=rel: pending.add_handedit(vault, rel, saved, diff, turn_id))
        if got == hand.KEEP_FAILED:
            failed.add(rel)
        reports.append(got)
    return _merge_reports(reports), failed


def agenda_items(vault, data=None):
    """日程頁、首頁、每句開頭讀的帳（D-273（2））：還沒收付、有到期日的收與付（帳全隊看得到，D-339）。讀不到回空的。"""
    try:
        data = load(vault) if data is None else data
    except ValueError:
        return []
    return [i for i in data["items"] if i.get("status") == "open" and i.get("dir") in ("in", "out") and i.get("due")]


# ---------- 卡底下「日程與帳」的帳那半（記帳 3，D-340；外掛只畫，字由這裡排好） ----------

def panel_open_line(it, today):
    """「未收 1,800 月底付另一半（到期 11/30，已過 3 天）」。"""
    head = ("未收" if it.get("dir") == "in" else "未付") + " " + (_amt_text(it) or "（金額未知）")
    if it.get("text"):
        head += f" {it['text']}"
    inside = []
    if it.get("advanced_by"):
        inside.append(f"{it['advanced_by']}墊")
    if it.get("due"):
        inside.append(f"到期 {agenda_md(it['due'])}")
    late = late_days(it, today)
    if late:
        inside.append(f"已過 {late} 天")
    return head + (f"（{'，'.join(inside)}）" if inside else "")


def panel_done_line(it, prof, ents, cache=None):
    """「10/30（五）收 5,000 精華液 ×2、膠囊 ×1」。"""
    body = it.get("text") or "數量" if it.get("dir") == "none" else f"{DIR_TEXT.get(it.get('dir'), '')} {_amt_text(it) or ''}"
    parts = [x for x in (it.get("text") if it.get("dir") != "none" else "", _items_text(it, prof, ents, times=True, cache=cache)) if x]
    return f"{_wd(it['date'])}{body}" + (f" {' '.join(parts)}" if parts else "")


def panel(vault, name, today=None):
    """{"money": {...}|None, "stock": {...}|None}：合計一行、未收付全部、最近 money_recent_n 筆已收付（各帶 line 與要開的頁、那一行）；
    商品的合計那行換成庫存（各店）與賣出累計，地點型別的實體給那裡的各商品庫存。沒有任何筆、沒有庫存的回兩個 None。"""
    from kb._lib import agenda, numbers
    today = today or _today()
    ents, prof = _ents(vault), _profile(vault)
    got = for_entity(vault, name)
    link = agenda.linker(vault, ents, mark_removed=True)
    data = load(vault)
    by_id = {i.get("id"): i for i in data["items"]}
    ucache = unit_cache(data)
    stock_ = got.get("stock")
    stock_out = None
    if isinstance(stock_, list):
        if stock_:
            stock_out = {"products": stock_, "line": "庫存：" + "、".join(
                f"{p['name']} {next(iter(p['places_text'].values()), p['text'])}" for p in stock_)}
    elif stock_:
        places = places_line(stock_)
        stock_out = dict(stock_, line=f"庫存 {stock_['text']}" + (f"（{places}）" if places else ""))
    if not got["items"]:
        return {"money": None, "stock": stock_out}
    parts = [(f"累計收 {numbers.show(got['in'])}", got["in"]), (f"累計付 {numbers.show(got['out'])}", got["out"]),
             (f"未收 {numbers.show(got['open_in'])}", got["open_in"]), (f"未付 {numbers.show(got['open_out'])}", got["open_out"])]
    head = "｜".join(t for t, v in parts if v) or "累計收 0"
    if stock_out is not None and "total" in stock_out:
        sold = [p for p in _products(_live(data), ents, prof, data) if _key(p["name"]) == _key(stock_out["name"])]
        head = stock_out["line"] + (f"｜賣出累計 {sold[0]['text']}" if sold else "")
    money_out = {
        "in": got["in"], "out": got["out"], "open_in": got["open_in"], "open_out": got["open_out"], "head": head,
        "open": [{"id": i.get("id"), "due": i.get("due"), "late": late_days(i, today) > 0, "line": panel_open_line(i, today),
                  "page": MD_POSIX, "match": open_line(i, link, today)} for i in got["open"] if i.get("dir") in ("in", "out")],
        "recent": [{"id": i.get("id"), "date": i.get("date"), "line": panel_done_line(i, prof, ents, ucache),
                    "page": year_rel(i["date"][:4]), "match": detail_line(i, link, prof, ents, by_id, ucache)} for i in got["recent"]],
        "total_n": got["total_done"], "page": MD_POSIX}
    return {"money": money_out, "stock": stock_out}
