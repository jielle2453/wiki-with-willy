"""實體清單 .system/entities.json（schema 2，D-114～D-124、D-126、D-127；實體與綜合卡第 1 點、日程第 3 點）。

一筆一個客戶管的對象、事、物、主題：id（e- 加 8 碼 hex）、type（型別表的名稱；客戶自己是 null）、name、aliases、
self（false｜company｜person，D-120）、status（active｜removed｜merged，merged 帶 merged_into）、source（D-041 四選一，另有 ai_judged：AI 看掛的卡有把握、
直接登記的，算確認、不問名字，D-202；accept 寫，帶 judged_turn）、
date、card（寫過綜合卡沒有）、created_turn。名稱在整個知識庫唯一（不分型別，NFKC 後不分大小寫，別名也算），
所以檔案卡檔頭 實體 欄寫名稱就是鍵；改名與合併由這裡把所有檔案卡的那一欄與日程條目的實體名稱一起改。
schema 2（日程第 3 點）：大類是「事」的實體多 progress（ongoing｜closed｜paused）、progress_source、progress_history；
schema 1 讀進來在記憶體補上，寫的時候寫 2。實體本身在不在是 status，事類進行到哪是 progress，兩件事。
只准程式寫、讀改寫整段在 FileLock 裡（I-080），寫前備進回合（D-074）。綜合卡的搬、刪也在這裡（在同一把鎖裡），
檔頭格式與驗證住 synthesis.py。drift 只讀：認出客戶在檔案總管改了實體夾名、卡名、刪夾（D-057）。
note 改知識：alias 加與拿掉別名（D-148）；rename／merge 連口述與裁示的實體名稱一起換（同日程，同一套 _Undo），remove 照留。
日程匯出（D-500 第二輪）：存過 entities.json 的函式放開鎖之後叫 _export_agenda，匯出的實體名單立刻跟上（T-2）。
照標籤篩（標籤字典第四節，D-235）：parse_tag_args 讀 --tag 欄=詞[,詞]、tag_filter 照正本的 tags 篩，只讀。
schema 3（標籤記在實體上，D-505）：每筆多 tags（{欄: 詞 | [詞…]}；"" 與 [] 是看過、沒有；沒有這個鍵是還沒標過）、
tags_said（她親口定的欄）與 tags_at（tags 最後寫入的時間，健檢比卡的修改時間，裁定 5）。schema 2 讀進來不補（沒有 tags 就是還沒標過），
寫的時候寫 3。set_tags 是唯一寫 tags 的地方：照第八節驗、她說過的欄只有 said 才改；綜合卡檔頭的標籤行是它的渲染。
"""
import os
import posixpath
import re
import secrets
import unicodedata
from datetime import date
from pathlib import Path

from kb._lib import config, profile
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

ENTITIES_POSIX = ".system/entities.json"
SCHEMA_VERSION = 3
PROGRESS = ("ongoing", "closed", "paused")
PROGRESS_TEXT = {"ongoing": "進行中", "closed": "結案", "paused": "擱置"}
CLOSE_KIND = "close"
SHELL_CHARS = profile.SHELL_CHARS  # 名字會被貼進雙引號的指令（日程釐清）；型別名規則也擋了，同一套（note 改規則測試後修正裁定 16）
SELF_KINDS = {"公司": "company", "本人": "person"}
HEAD_BYTES = 4096
NOT_FOUND = "實體清單沒有「{}」"
AI_JUDGED = "ai_judged"  # D-202：AI 判斷有把握直接登記；synthesis、batches 只把 inferred 當沒確認，所以它算確認
ACCEPT_NOT_FOUND = "實體清單沒有：{}"


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


def _save(vault, data, turn_id, undo=None, by=None):
    """呼叫端持有鎖：先備進回合再寫；undo 給了就記舊內容（中途出錯還原用）。by="program"：程式照字典改的標籤（回答末尾不講，裁定 12）。"""
    from kb._lib import turn
    turn.backup(vault, ENTITIES_POSIX, turn_id, by=by)
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


# ---------- 照標籤篩（D-235） ----------

TAG_FORMAT = "--tag 要寫成 欄=詞（多個詞用逗號分開）"
TAG_NO_FIELD = "第八節沒有「{field}」這個標籤欄；有：{have}"
TAG_NO_WORD = "「{field}」的字典沒有「{word}」；有：{have}"
TAG_NO_TYPE = "第八節沒有哪個型別同時有這些欄"
TAG_NO_CARD = "「{name}」沒有綜合卡"
_TAG_SPLIT = re.compile(r"[,，、]")


def parse_tag_args(raw):
    """--tag 欄=詞[,詞]（逗號或頓號）→ [(欄, [詞…])]，同一欄寫兩次併在一起；寫法不對丟 EntityRefused（TAG_FORMAT）。"""
    out = []
    for arg in raw or []:
        field, sep, rest = str(arg).partition("=")
        words = [w.strip() for w in _TAG_SPLIT.split(rest) if w.strip()]
        if not sep or not field.strip() or not words:
            raise EntityRefused(TAG_FORMAT)
        hit = next((x for x in out if key(x[0]) == key(field.strip())), None)
        if hit is None:
            out.append((field.strip(), words))
        else:
            hit[1].extend(w for w in words if key(w) not in {key(x) for x in hit[1]})
    return out


def tag_filter(vault, conds, data=None):
    """照標籤篩（kb entity list --tag）：不同欄是且、同欄的詞是或，比對照 key。只看 active、不是客戶自己的實體；
    型別沒有這些欄的不列。照正本的 tags（D-505）：那一欄有詞且對得上算符合；沒有那一欄算還沒標過；看過（"" 或 []）或詞沒對上算不符合。
    回 {"tag": [{"field","words"}]（照字典的寫法）, "counts": [{"type","total","matched","untagged"}], "matched": [編號]}；
    欄或詞不在第八節、沒有型別同時有這些欄丟 EntityRefused。"""
    prof = _profile(vault)
    rows = prof.get("tags") or []
    fields = list(dict.fromkeys(t["field"] for t in rows))
    tag = []
    for field, words in conds:
        mine = [t for t in rows if key(t["field"]) == key(field)]
        if not mine:
            raise EntityRefused(TAG_NO_FIELD.format(field=field, have="、".join(fields) or "（還沒有）"))
        known = list(dict.fromkeys(w for t in mine for w in t["words"]))
        shown = []
        for w in words:
            hit = next((x for x in known if key(x) == key(w)), None)
            if hit is None:
                raise EntityRefused(TAG_NO_WORD.format(field=mine[0]["field"], word=w, have="、".join(known)))
            shown.append(hit)
        tag.append({"field": mine[0]["field"], "words": shown})
    data = data if data is not None else load(vault)
    counts, matched = [], []
    for t in prof.get("types") or []:
        have = {key(r["field"]) for r in profile.tags_for(prof, t["name"])}
        if not all(key(c["field"]) in have for c in tag):
            continue
        members = [e for e in data["entities"] if e.get("status") == "active" and not is_self(e) and e.get("type")
                   and key(e["type"]) == key(t["name"])]
        hits, untagged = [], 0
        for e in members:
            state = _tag_state(e.get("tags"), tag)
            if state == "match":
                hits.append(e["id"])
            elif state == "untagged":
                untagged += 1
        counts.append({"type": t["name"], "total": len(members), "matched": len(hits), "untagged": untagged})
        matched += hits
    if not counts:  # 欄分屬不同型別（裁定 16）
        raise EntityRefused(TAG_NO_TYPE)
    return {"tag": tag, "counts": counts, "matched": matched}


def _tag_state(tags, tag):
    missing = False
    for c in tag:
        v = tag_value(tags, c["field"])
        if v is None:  # 正本沒有這一欄：還沒標過；"" 與 [] 是看過、沒有（D-505）
            missing = True
            continue
        if not {key(w) for w in tag_words(v)} & {key(w) for w in c["words"]}:
            return "no"
    return "untagged" if missing else "match"


# ---------- 標籤正本（D-505） ----------

def tag_words(v):
    """標籤的值攤成詞的清單（單選的一個詞也算一個；"" 與 [] 是空的）。"""
    if isinstance(v, list):
        return list(v)
    return [v] if v not in (None, "") else []


def tag_value(tags, field):
    """tags 裡 field 那一欄的值（比對照 key）；沒有這一欄回 None。"""
    for k, v in (tags or {}).items():
        if key(k) == key(field):
            return v
    return None


def said_fields(entry):
    return {key(f) for f in entry.get("tags_said") or []}


def tag_refusal(row, words):
    """這一欄的詞不能收的那句（同 synthesis._tag_problems 那套）；能收回 None。"""
    if not row.get("multi") and len(words) > 1:
        return f"「{row['field']}」只能選一個詞"
    known = {key(w) for w in row.get("words") or []}
    for w in words:
        if key(w) not in known:
            return (f"「{w}」不在「{row['field']}」的字典裡：先問客戶要不要加"
                    f"（kb profile add 標籤 型別={row['type']} 欄名={row['field']} 詞={w}），不要自己加")
    return None


def tag_normal(row, v):
    """照字典的寫法與單選／多選：多選回清單、單選回一個詞（沒有是 ""）。"""
    spelled = {key(w): w for w in row.get("words") or []}
    words = list(dict.fromkeys(spelled.get(key(w), w) for w in tag_words(v)))
    if row.get("multi"):
        return words
    return words[0] if words else ""


def _same_tag(a, b):
    return a is not None and b is not None and [key(w) for w in tag_words(a)] == [key(w) for w in tag_words(b)] \
        and isinstance(a, list) == isinstance(b, list)


def _now_stamp():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def set_tags(vault, name_or_id, updates, turn_id, said=False, undo=None, by=None):
    """寫標籤正本（kb entity tag、綜合卡 hook 抄檔頭、健檢抄她手改的）。updates {欄: 詞 | [詞…] | "" | []}：欄要是這個型別在第八節的欄、
    詞在字典、單選只一個，錯了丟 EntityRefused。said=True 把欄加進 tags_said；False 時跳過 tags_said 裡的欄（回在 kept）。
    值與 tags_said 都沒變不寫；寫的時候記 tags_at。回 {"entity","changed": [欄…],"kept": [欄…],"card": 綜合卡在哪或 None}。"""
    prof = _profile(vault)
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, name_or_id)
        _need_plain(entry, "標標籤")
        rows = {key(t["field"]): t for t in profile.tags_for(prof, entry.get("type"))}
        clean = []
        for field, v in updates.items():
            row = rows.get(key(field))
            if row is None:
                have = "、".join(t["field"] for t in rows.values()) or "（還沒有）"
                raise EntityRefused(TAG_NO_FIELD.format(field=field, have=have))
            why = tag_refusal(row, tag_words(v))
            if why:
                raise EntityRefused(why)
            clean.append((row["field"], tag_normal(row, v)))
        tags = dict(entry.get("tags") or {})
        said_now = list(entry.get("tags_said") or [])
        mine = said_fields(entry)
        changed, kept, dirty = [], [], False
        for field, v in clean:
            if not said and key(field) in mine:
                kept.append(field)
                continue
            if said and key(field) not in mine:
                said_now.append(field)
                mine.add(key(field))
                dirty = True
            old_key = next((k for k in tags if key(k) == key(field)), None)
            if old_key is not None and _same_tag(tags[old_key], v):
                continue
            if old_key is not None:
                del tags[old_key]
            tags[field] = v
            changed.append(field)
            dirty = True
        if dirty:
            entry["tags"] = {t["field"]: tags[t["field"]] for t in rows.values() if t["field"] in tags}
            entry["tags"].update({k: v for k, v in tags.items() if k not in entry["tags"]})
            entry["tags_said"] = said_now
            entry["tags_at"] = _now_stamp()
            _save(vault, data, turn_id, undo, by=by)
    if dirty:
        _export_agenda(vault)
    card = locate(vault, entry) if entry.get("type") else None
    return {"entity": entry, "changed": changed, "kept": kept, "card": card}


def retag_entities(vault, type_name, field, fn, ids, turn_id, undo, new_field=None, drop=None):
    """kb profile tag、apply 手改改字典（D-505 決策 5）：ids 這幾個實體的 tags 那一欄照 fn 改（fn(現在的值) 回新值；回 drop 拿掉那一欄、
    tags_said 也拿掉）；new_field 給了換鍵（tags_said 跟著換）。單選改到沒有詞寫 ""。程式照字典改的，備份 by program（裁定 12）。
    回改到的實體名稱。"""
    ids = set(ids)
    if not ids:
        return []
    with lock(vault):
        data = load(vault)
        names = []
        for e in data["entities"]:
            if e.get("id") not in ids or key(e.get("type")) != key(type_name):
                continue
            tags = dict(e.get("tags") or {})
            old_key = next((k for k in tags if key(k) == key(field)), None)
            if old_key is None:
                continue
            new = fn(tags[old_key])
            said = list(e.get("tags_said") or [])
            if new is drop:
                del tags[old_key]
                said = [f for f in said if key(f) != key(field)]
            else:
                if new == [] and not isinstance(tags[old_key], list):
                    new = ""
                name = new_field or old_key
                tags = {(name if k == old_key else k): (new if k == old_key else v) for k, v in tags.items()}
                if new_field:
                    said = [new_field if key(f) == key(field) else f for f in said]
            if tags == (e.get("tags") or {}) and said == list(e.get("tags_said") or []):
                continue
            e["tags"], e["tags_said"], e["tags_at"] = tags, said, _now_stamp()
            names.append(e["name"])
        if names:
            _save(vault, data, turn_id, undo, by="program")
    if names:
        _export_agenda(vault)
    return names


TAG_EDIT_PROBLEM = "{name}的卡上「{word}」不在「{field}」的字典裡：要加進字典還是改回 {back}？"
TAG_EDIT_SINGLE = "{name}的卡上「{field}」寫了 {n} 個詞，這一欄只能選一個：要留哪一個？"


def card_newer(vault, rel, entry):
    """卡的修改時間晚於正本最後寫入（tags_at，裁定 5）；正本沒寫過也算。"""
    from datetime import datetime
    stamp = entry.get("tags_at")
    if not stamp:
        return True
    try:
        return datetime.fromtimestamp((Path(vault) / rel).stat().st_mtime) > datetime.fromisoformat(str(stamp))
    except (OSError, ValueError):
        return False


def card_diffs(vault, entry, prof, text=None, rel=None):
    """這張綜合卡檔頭的標籤行跟正本不同的欄：(合字典的 {欄: 值}, 不合的 [(第八節那列, 卡上的值)])；空著的那一行（還沒標過）不算。
    正本有詞、卡上整行拿掉的，是她說「沒有」（裁定 12）：算合字典，值是 []（單選 ""）。"""
    from kb._lib import synthesis
    rows = {key(t["field"]): t for t in profile.tags_for(prof, entry.get("type"))}
    if not rows:
        return {}, []
    try:
        if text is None:
            text = (Path(vault) / (rel or locate(vault, entry))).read_text(encoding="utf-8")
        front = synthesis.read_front(text)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return {}, []
    good, bad = {}, []
    for f, v in synthesis.card_tags(front, prof, entry.get("type")):
        if v == "":
            continue
        row = rows[key(f)]
        if tag_refusal(row, tag_words(v)) is not None:  # 先判合不合字典：單選寫兩個詞時 tag_normal 只留第一個，會跟正本比成一樣
            bad.append((row, v))
            continue
        canon = tag_value(entry.get("tags"), f)
        if canon is not None and [key(w) for w in tag_words(tag_normal(row, canon))] == \
                [key(w) for w in tag_words(tag_normal(row, v))]:
            continue
        good[row["field"]] = v
    on_card = {key(k) for k in front}
    for k, row in rows.items():
        if k not in on_card and tag_words(tag_value(entry.get("tags"), row["field"])):
            good[row["field"]] = [] if row.get("multi") else ""
    return good, bad


def tag_edit_problem(entry, row, v):
    """卡上不合字典的那一欄要問她的那句（六-2）。"""
    words = tag_words(v)
    if not row.get("multi") and len(words) > 1:
        return TAG_EDIT_SINGLE.format(name=entry["name"], field=row["field"], n=len(words))
    known = {key(w) for w in row.get("words") or []}
    extra = [w for w in words if key(w) not in known]
    back = "、".join(tag_words(tag_value(entry.get("tags"), row["field"]))) or "沒有"
    return TAG_EDIT_PROBLEM.format(name=entry["name"], word="、".join(extra), field=row["field"], back=back)


def _edited(vault, data, prof):
    """[(實體, 卡的相對路徑)]：有綜合卡的 active 實體、型別有標籤欄、卡比正本新的（只看修改時間，不讀檔）。"""
    out = []
    for e in data["entities"]:
        if not e.get("card") or e.get("status") != "active" or is_self(e) or not e.get("type"):
            continue
        if not profile.tags_for(prof, e.get("type")):
            continue
        rel = locate(vault, e)
        if rel and card_newer(vault, rel, e):
            out.append((e, rel))
    return out


def tag_edits(vault, data=None):
    """她在 Obsidian 手改綜合卡的標籤行（D-505 六-1、裁定 6）：卡的修改時間晚於 tags_at、檔頭標籤行跟正本不同的。只讀；
    先比修改時間，晚的才讀檔頭。回 [{"name","fields": [欄…],"ok": 全部合字典嗎,"problem": 不合字典的那幾句或 None}]。"""
    data = data if data is not None else load(vault)
    prof = _profile(vault)
    out = []
    for e, rel in _edited(vault, data, prof):
        good, bad = card_diffs(vault, e, prof, rel=rel)
        if good or bad:
            out.append({"name": e["name"], "fields": list(good) + [row["field"] for row, _v in bad], "ok": not bad,
                        "problem": "；".join(tag_edit_problem(e, row, v) for row, v in bad) or None})
    return out


def copy_card_edits(vault, refs, turn_id, by=None):
    """kb entity tag --from-card、健檢（六-2、六-4）：refs 這幾個（None 是 tag_edits 全部）卡上合字典的標籤行照她改的寫進正本、
    算她說的；不合字典的不寫；整行拿掉的記成沒有，卡上那一行照正本寫回空的（備進回合 by program，裁定 12）。
    回 [{"entity","written": [欄…],"bad": [(第八節那列, 卡上的值)],"problems": [句…]}]，沒得記的不列。給了名稱但沒有綜合卡丟 EntityRefused。"""
    data = load(vault)
    prof = _profile(vault)
    if refs is None:
        pairs = [(e, rel) for e, rel in _edited(vault, data, prof)]
    else:
        pairs = []
        for ref in refs:
            e, _hit = _need(data, ref)
            rel = locate(vault, e) if e.get("card") and e.get("type") else None
            if not rel:
                raise EntityRefused(TAG_NO_CARD.format(name=e["name"]))
            pairs.append((e, rel))
    out = []
    for e, rel in pairs:
        good, bad = card_diffs(vault, e, prof, rel=rel)
        if not good and not bad:
            continue
        entry = e
        if good:
            entry = set_tags(vault, e["id"], good, turn_id, said=True, by=by)["entity"]
            _rewrite_dropped(vault, rel, entry, turn_id)
        out.append({"entity": entry, "written": list(good), "bad": bad,
                    "problems": [tag_edit_problem(entry, row, v) for row, v in bad]})
    return out


def _rewrite_dropped(vault, rel, entry, turn_id):
    """卡上少了的標籤行照正本補回去（她整行拿掉的寫回空的，裁定 12）。"""
    from kb._lib import synthesis, turn
    p = Path(vault) / rel
    text = p.read_text(encoding="utf-8")
    new = synthesis.write_tag_lines(text, entry.get("tags"), synthesis.missing_tag_fields(text, entry, _profile(vault)))
    if new != text:
        turn.backup(vault, rel, turn_id, by="program")
        write_text_atomic(p, new)


def tag_status(entry, prof):
    """這個實體型別的欄裡，正本還沒有的（補標籤用）。"""
    have = {key(k) for k in (entry.get("tags") or {})}
    return [t["field"] for t in profile.tags_for(prof, entry.get("type")) if key(t["field"]) not in have]


def tags_fingerprint(entry):
    """正本 tags 的指紋（健檢記「補標籤」的 sha，tags 改了這筆就不算，裁定 4）。"""
    import hashlib
    import json
    text = json.dumps(entry.get("tags") or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def merge_tags(dst, src):
    """合併實體時 tags 照 merge 的規則合（D-505 一-4）：多選取聯集（目標的在前）；單選以目標為準、目標沒有才拿來源的；tags_said 聯集。"""
    if not src.get("tags") and not src.get("tags_said"):
        return
    tags = dict(dst.get("tags") or {})
    for field, v in (src.get("tags") or {}).items():
        have = next((k for k in tags if key(k) == key(field)), None)
        if have is None:
            tags[field] = v
        elif isinstance(tags[have], list) and isinstance(v, list):
            tags[have] = list(dict.fromkeys(tags[have] + [w for w in v if key(w) not in {key(x) for x in tags[have]}]))
    dst["tags"] = tags
    said = list(dst.get("tags_said") or [])
    said += [f for f in src.get("tags_said") or [] if key(f) not in {key(x) for x in said}]
    dst["tags_said"] = said
    dst["tags_at"] = _now_stamp()


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
        raise EntityRefused(f"「{entry['name']}」是你自己（關於你），用 Edit 改設定檔第 5 節（關於你那一列），再跑 kb profile apply。")
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
    _export_agenda(vault)
    return entry, True


def confirm(vault, ref, turn_id):
    """source 改 confirmed、date 今天；回條目。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        entry["source"] = "confirmed"
        entry["date"] = _today()
        _save(vault, data, turn_id)
    _export_agenda(vault)
    return entry


def accept(vault, ref, turn_id):
    """AI 判斷有把握（D-202）：source 從 inferred 改 ai_judged、記 judged_turn。回 (條目, 怎樣)：
    "accepted" 改了；"judged" 本來就是 AI 判斷（不寫）；"confirmed" 客戶確認過或訪談來的（不寫）。
    沒登記丟 EntityRefused（ACCEPT_NOT_FOUND）；已移除的照 _need_plain 拒絕。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = find(data, ref)
        if entry is None:
            raise EntityRefused(ACCEPT_NOT_FOUND.format(ref))
        if entry.get("source") == AI_JUDGED:
            return entry, "judged"
        if entry.get("source") != "inferred":
            return entry, "confirmed"
        _need_plain(entry, "登記")
        entry["source"] = AI_JUDGED
        entry["date"] = _today()
        entry["judged_turn"] = turn_id
        _save(vault, data, turn_id)
    _export_agenda(vault)
    return entry, "accepted"


def judged_in(vault, turn_id):
    """這個回合 AI 判斷登記的實體名稱（tell 那句，D-202）；實體清單讀不到回空。"""
    try:
        data = load(vault)
    except ValueError:
        return []
    return [e["name"] for e in data["entities"] if e.get("status") == "active" and e.get("source") == AI_JUDGED
            and e.get("judged_turn") == turn_id]


def set_card(vault, entity_id, value, turn_id):
    """PostToolUse 寫過合法綜合卡後 card 改 true；值沒變不寫。回有沒有改。"""
    with lock(vault):
        data = load(vault)
        entry = next((e for e in data["entities"] if e.get("id") == entity_id), None)
        if entry is None or bool(entry.get("card")) == bool(value):
            return False
        entry["card"] = bool(value)
        _save(vault, data, turn_id)
    _export_agenda(vault)
    return True


# ---------- 掛載 ----------

def mounts(vault, data=None, mentions=False):
    """檔案索引/ 全部卡的檔頭 實體 → {實體名稱: [{"id","path","title","fp","date","card_type","relation"}]}；relation 是 main；清冊封存的檔多 "archived": True。
    mentions=True 再把檔頭 提到 欄的也放進去（relation 是 mention，放寬丙 7，同一張卡同一實體主要優先）；預設只算主要的。只算清冊條目 active 的檔案卡
    （同編號多張取第一張）與 active 的實體；名稱照登記的（別名、合併過的轉到目標）；fp 是內容指紋（沒有取指紋）前 12 碼。
    B-069：原檔不見（missing）、「是不是刪了」還沒答（missing_file 待處理 open）的照算掛載，客戶答之前卡先留著。"""
    from kb._lib import cards, inventory, pending
    data = data if data is not None else load(vault)
    try:
        entries = inventory.load(vault)["entries"]
    except ValueError:
        entries = []
    try:
        asking = {(i.get("file_ids") or [None])[0] for i in pending.load(vault)["items"]
                  if i.get("kind") == "missing_file" and i.get("status") == "open"}
    except ValueError:
        asking = set()
    active = {e.get("id") for e in entries
              if e.get("status") == "active" or (e.get("status") == "missing" and e.get("id") in asking)}
    archived = {e.get("id") for e in entries if e.get("archived")}
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
                "date": front.get("文件日期") if isinstance(front.get("文件日期"), str) else "",
                "card_type": front.get("卡型") if isinstance(front.get("卡型"), str) else "",
                "relation": "main"}
        if cid in archived:  # 封存的檔：綜合卡「相關檔」放進「其他」框（放寬丙 12）
            item["archived"] = True
        seen = set()
        for name in names:
            entry, _hit = find(data, name)
            if entry is None or entry.get("status") != "active" or entry.get("id") in seen:
                continue
            seen.add(entry.get("id"))
            out.setdefault(entry["name"], []).append(dict(item))
        if mentions and isinstance(front.get("提到"), list):
            for name in front["提到"]:
                entry, _hit = find(data, name)
                if entry is None or entry.get("status") != "active" or entry.get("id") in seen:
                    continue
                seen.add(entry.get("id"))
                out.setdefault(entry["name"], []).append(dict(item, relation="mention"))
    return out


# ---------- 綜合卡位置 ----------

def card_rel(entry, vault=None):
    """綜合卡該在的位置 <型別夾>/<名稱>/<名稱>.md。型別夾照設定檔記的（profile.folder_of，型別夾編號 D-246）；
    vault 沒給（或型別不在表）用型別名當夾名。"""
    folder = profile.folder_of(vault, entry.get("type")) if vault is not None else entry.get("type")
    return f"{folder}/{entry.get('name')}/{entry.get('name')}.md"


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


def _drift_kind(entry, found, prof=None):
    if not found:
        return "missing"
    top, folder, name = found.split("/")
    stem = name[:-3]
    if key(profile.dir_type(prof, top) or top) != key(entry.get("type")):  # 上層夾照設定檔認是哪個型別（型別夾編號）
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
    """只讀。card true、active、不是客戶自己的實體，綜合卡不在 <型別夾>/<名稱>/<名稱>.md 時，掃型別夾第三層找檔頭編號是它的；
    回 [{"entity_id","name","type","kind","found","found_type"}]，kind：folder_renamed｜card_renamed｜both_renamed｜moved_type｜missing；
    found_type 是 found 的上層夾照設定檔認出的型別名（不是型別夾回 None）。"""
    data = data if data is not None else load(vault)
    out, index, prof = [], None, None
    for e in data["entities"]:
        if not e.get("card") or e.get("status") != "active" or is_self(e) or not e.get("type"):
            continue
        if (Path(vault) / card_rel(e, vault)).is_file():
            continue
        if index is None:
            index, prof = synthesis_files(vault), _profile(vault)
        found = (index.get(e.get("id")) or [None])[0]
        kind = _drift_kind(e, found, prof)
        if kind:
            out.append({"entity_id": e["id"], "name": e["name"], "type": e.get("type"), "kind": kind, "found": found,
                        "found_type": profile.dir_type(prof, found.split("/")[0]) if found else None})
    return out


def locate(vault, entry, data=None):
    """這個實體的綜合卡現在在哪（相對路徑）：card_rel 在就是它，否則 drift 找到的；都沒有回 None。"""
    if (Path(vault) / card_rel(entry, vault)).is_file():
        return card_rel(entry, vault)
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
    搬夾或改檔名之後記 (來源, 目的)，拿掉空夾之後記夾。中途出錯就倒著做回去。回合備份照記，不還原（帳本）。
    資料夾帳（D-142）先記在 folder_log，整段做完才 flush 進回合的 folders.jsonl：還原了的搬動不會留下退回時對不上的帳。"""

    def __init__(self, vault):
        self.v = Path(vault)
        self.steps = []
        self.folder_log = []

    def folder(self, turn_id, op, src, dst=None):
        self.folder_log.append((turn_id, op, src, dst))

    def flush(self):
        from kb._lib import turn
        while self.folder_log:
            turn_id, op, src, dst = self.folder_log.pop(0)
            turn.record_folder(self.v, turn_id, op, src, dst)

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
        got = act(undo)
        undo.flush()
        return got
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
    from kb._lib import card_base
    card_base.forget(vault, rel)
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
    from kb._lib import cards, inventory, turn
    changed, shas = [], {}
    for rel in cards._walk_cards(vault):
        p = Path(vault) / rel
        try:
            text = p.read_text(encoding="utf-8")
            front = cards.parse_front(text)
            names = front.get("實體")
        except (ValueError, OSError, UnicodeDecodeError):
            continue
        mention = front.get("提到") if isinstance(front.get("提到"), list) else []
        hit_main = isinstance(names, list) and any(key(n) in mapping for n in names)
        hit_mention = any(key(n) in mapping for n in mention)
        if not (hit_main or hit_mention):
            continue

        def mapped(old):
            new = []
            for n in old:
                n = mapping.get(key(n), n)
                if all(key(x) != key(n) for x in new):
                    new.append(n)
            return new
        before = inventory.card_sha(vault, rel)
        turn.backup(vault, rel, turn_id, by="program")
        out_text = text
        if hit_main:
            out_text = cards.rewrite_entities(out_text, mapped(names))
        if hit_mention:
            out_text = cards.rewrite_mentions(out_text, mapped(mention))
        _write(vault, rel, out_text, undo)
        if isinstance(front.get("編號"), str):
            shas[front["編號"]] = (before, inventory.card_sha(vault, rel))
        changed.append((rel, names if isinstance(names, list) else []))
    inventory.follow_redo(vault, shas, turn_id, undo)  # 重做記號跟著換（測試第一輪後裁定 6）
    return changed


# ---------- 改名、合併、移除、重寫、清 ----------

def _occupied(vault, loc, target):
    if loc and key(loc) != key(target) and (Path(vault) / target).exists():
        raise EntityRefused(f"「{target}」已經有檔，沒有改名；先把它搬走")


def rename(vault, ref, new_name, new_type, turn_id, undo=None):
    """kb entity rename：名稱（舊名進別名）、型別；有綜合卡就搬到 <新型別>/<新名>/<新名>.md（目標夾不在就整個夾改名，在就只搬卡）、
    檔頭的名稱／別名／型別／大類與正文標題改寫；全部檔案卡的 實體 欄換新名。整段在鎖裡。
    會擋的條件先驗完才動手；動手順序：搬夾與卡 → 檔案卡 → entities.json，中途出錯照 _Undo 還原（測試後修正裁定 8）。
    undo 給了（kb profile type merge 一次換很多個）：步驟記進呼叫端的 undo，出錯與資料夾帳都由呼叫端收。
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
        target = card_rel(entry, vault)
        _occupied(vault, loc, target)

        def act(undo):
            changed, moved = [], 0
            _agenda_snapshot(vault, undo)
            _knowledge_snapshot(vault, undo)
            if loc:
                from kb._lib import card_base
                text = (Path(vault) / loc).read_text(encoding="utf-8")
                text = synthesis.replace_title(synthesis.replace_front(text, _card_fields(prof, entry)), old_name,
                                               entry["name"])
                synced = card_base.in_sync(vault, loc)  # 她有沒處理的手改就不換基準（D-270）
                moved = _move_card(vault, loc, target, text, turn_id, changed, undo)
                fake = "---\n---\n"  # replace_title 認得檔頭之後的第一個「# 舊名」
                card_base.rekey(vault, loc, target, synced, lambda body: synthesis.replace_title(
                    fake + body, old_name, entry["name"])[len(fake):])  # 她有沒處理的手改：基準的標題也換，程式換的字不算她的
            touched = retarget_cards(vault, {key(old_name): new_name}, turn_id, undo) if old_name != new_name else []
            changed += [{"path": rel, "action": "modify"} for rel, _names in touched]
            if key(old_name) != key(new_name):
                changed += _agenda_follow(vault, {key(old_name): new_name}, turn_id)
                changed += _knowledge_follow(vault, {key(old_name): new_name}, turn_id)
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            changed += _agenda_md(vault, turn_id)
            return touched, changed, moved

        touched, changed, moved_files = act(undo) if undo is not None else _guarded(vault, act)
    _export_agenda(vault)
    return {"entity": entry, "old_name": old_name, "via_alias": hit == "alias", "cards": [r for r, _n in touched],
            "changed": changed, "moved_files": moved_files, "card": target if loc else None}


def _move_card(vault, loc, target, text, turn_id, changed, undo=None):
    """綜合卡從 loc 搬到 target 並寫成 text。夾不同、目標夾不在 → 整個夾改名（os.rename）；目標夾在 → 只搬卡，舊夾空了拿掉。
    備份：卡沒換位置是 modify；換了位置舊路徑 delete、新路徑 create（都在動手之前，退回才回得去）。每一步記進 undo；
    整夾 os.rename 的兩處記進 undo 的資料夾帳（D-142，修 B-062），做完由 _guarded 或呼叫端寫進回合。回跟著夾搬的其他檔數。"""
    from kb._lib import turn
    v = Path(vault)
    undo = undo if undo is not None else _Undo(vault)
    cur_folder, tgt_folder = loc.rpartition("/")[0], target.rpartition("/")[0]
    same_folder = key(cur_folder) == key(tgt_folder)
    if key(loc) == key(target):  # 同一個位置（頂多大小寫不同）：改大小寫後照 modify 備
        if cur_folder != tgt_folder:
            os.rename(v / cur_folder, v / tgt_folder)
            undo.moved(cur_folder, tgt_folder)
            undo.folder(turn_id, "move", cur_folder, tgt_folder)
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
        undo.folder(turn_id, "move", cur_folder, tgt_folder)
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


def retype(vault, old_type, new_type, turn_id, undo):
    """kb profile type rename（note 改規則第 2 點）：型別是 old_type 的每一筆（任何狀態）改成 new_type，備份後寫；
    active、有綜合卡的把檔頭型別與大類跟著改（by program）。夾已經由呼叫端改好名、設定檔已寫好新型別。回給日誌的 changed。"""
    prof = _profile(vault)
    changed = []
    with lock(vault):
        data = load(vault)
        hits = [e for e in data["entities"] if e.get("type") and key(e["type"]) == key(old_type)]
        if not hits:
            return changed
        for e in hits:
            e["type"] = new_type
        for e in hits:
            if e.get("status") == "active" and e.get("card"):
                _refresh_fields(vault, e, prof, turn_id, changed, undo)
        _save(vault, data, turn_id, undo)
    _export_agenda(vault)
    return changed + [{"path": ENTITIES_POSIX, "action": "modify"}]


def merge(vault, src_ref, dst_ref, turn_id):
    """kb entity merge：來源併進目標（同型別、都 active、都不是客戶自己）。目標別名加來源名稱與別名、目標綜合卡檔頭跟著改
    （之後是 synthesis_stale，測試後修正裁定 9）；檔案卡 實體 欄來源換目標（去重）；來源的綜合卡刪掉（夾裡有其他檔就擋）；
    來源 merged、merged_into 目標。會擋的先驗完；動手順序：刪來源卡 → 目標卡檔頭 → 檔案卡 → entities.json，中途出錯還原。
    放寬甲（D-322 第 24 件）：型別不同先把來源換成目標的型別（同 rename --type，不拒絕）；來源夾裡還有別的檔也不擋，
    卡刪完夾留著，檔由呼叫端（kb entity merge）照 source-edit move 搬進目標的夾（同名回 conflict、空了拿掉）。
    回 {"source","target","added","changed","target_card","src_folder","dst_folder","retyped"}。"""
    prof = _profile(vault)
    retyped = None
    pre = load(vault)
    s0, _h = find(pre, src_ref)
    d0, _h = find(pre, dst_ref)
    if (s0 and d0 and s0.get("id") != d0.get("id") and not is_self(s0) and not is_self(d0)
            and s0.get("status") == "active" and d0.get("status") == "active"
            and key(s0.get("type")) != key(d0.get("type"))):
        loc0 = locate(vault, s0)
        pre_folder = (loc0 or card_rel(s0, vault)).rpartition("/")[0]
        got = rename(vault, s0["id"], s0["name"], d0["type"], turn_id)
        retyped = {"from": s0["type"], "to": d0["type"], "moved_files": got["moved_files"], "changed": got["changed"]}
    with lock(vault):
        data = load(vault)
        src, _h1 = _need(data, src_ref)
        dst, _h2 = _need(data, dst_ref)
        if src.get("id") == dst.get("id"):
            raise EntityRefused(f"「{src_ref}」跟「{dst_ref}」是同一個實體")
        _need_plain(src, "合併")
        _need_plain(dst, "合併")
        loc = locate(vault, src)
        folder = (loc or card_rel(src, vault)).rpartition("/")[0]
        if retyped and not loc:  # 沒有綜合卡的實體：rename 沒搬夾，檔還在原型別的夾
            folder = pre_folder
        dst_loc = locate(vault, dst)
        dst_folder = (dst_loc or card_rel(dst, vault)).rpartition("/")[0]
        before = mounts(vault, data)
        had_dst = {m["id"] for m in before.get(dst["name"], [])}
        added = [m["id"] for m in before.get(src["name"], []) if m["id"] not in had_dst]
        for w in words(src):
            if key(w) != key(dst["name"]) and all(key(a) != key(w) for a in dst.get("aliases") or []):
                dst.setdefault("aliases", []).append(w)
        src["status"] = "merged"
        src["merged_into"] = dst["id"]
        merge_tags(dst, src)

        def act(undo):
            changed = []
            _agenda_snapshot(vault, undo)
            _knowledge_snapshot(vault, undo)
            if loc:
                _delete_card(vault, loc, turn_id, undo)
                changed.append({"path": loc, "action": "delete"})
            elif (Path(vault) / folder).is_dir():
                _rmdir_if_empty(vault, folder, undo)
            _refresh_fields(vault, dst, prof, turn_id, changed, undo)
            changed += [{"path": rel, "action": "modify"}
                        for rel, _n in retarget_cards(vault, {key(src["name"]): dst["name"]}, turn_id, undo)]
            changed += _agenda_follow(vault, {key(src["name"]): dst["name"]}, turn_id)
            changed += _knowledge_follow(vault, {key(src["name"]): dst["name"]}, turn_id)
            changed += _drop_close_items(vault, src, f"實體已合併進「{dst['name']}」", turn_id, undo)
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            changed += _agenda_md(vault, turn_id)
            return changed

        changed = _guarded(vault, act)
        target_card = bool(dst.get("card")) and locate(vault, dst) is not None
    _export_agenda(vault)
    if retyped:
        changed = list(retyped["changed"]) + changed
    return {"source": src, "target": dst, "added": added, "changed": changed, "target_card": target_card,
            "src_folder": folder, "dst_folder": dst_folder, "retyped": retyped}


def remove(vault, ref, turn_id):
    """kb entity remove：status removed（知識不刪、檔案卡上的掛載留著）；綜合卡還在就刪，夾裡沒別的檔就 rmdir。
    事類的 open 期間條目照結案那條路結束（B-469，不是取消）。中途出錯還原。"""
    from kb._lib import agenda
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        _need_plain(entry, "移除")
        loc = locate(vault, entry)
        matter = is_matter(vault, entry)
        entry["status"] = "removed"

        def act(undo):
            changed = []
            if loc:
                _delete_card(vault, loc, turn_id, undo)
                changed.append({"path": loc, "action": "delete"})
            if matter:
                _agenda_snapshot(vault, undo)
                if agenda.close_periods(vault, entry["name"], _today(), turn_id):
                    changed += [{"path": agenda.AGENDA_POSIX, "action": "modify"},
                                {"path": agenda.MD_POSIX, "action": "modify"}]
            changed += _drop_close_items(vault, entry, "實體已移除", turn_id, undo)
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            return changed

        changed = _guarded(vault, act)
    _export_agenda(vault)
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
    _export_agenda(vault)
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
        if entry is not None and entry.get("type") and key(rel) == key(card_rel(entry, vault)):
            raise EntityRefused("這張綜合卡位置是對的，不用清")
        left = _delete_card(vault, rel, turn_id)
    return {"path": rel, "left": left}


# ---------- 別名（note 改知識第 4 點，D-148） ----------

def _about_hit(prof, word):
    """設定檔第五節（關於你）的名稱或別名撞到 word：回那一列的名稱，沒有回 None（還沒同步進實體清單的也擋）。"""
    for row in prof.get("about") or []:
        if any(key(w) == key(word) for w in [row.get("name")] + list(row.get("aliases") or [])):
            return row.get("name")
    return None


def _alias_refusal(vault, data, entry, word, prof):
    """加別名的撞名檢查：回 (拒絕句, 該合併的實體)，都沒有回 (None, None)。撞到另一個 active 一般實體的名稱 → 改問合併。"""
    why = _char_problem(vault, word)
    if why:
        return f"名稱「{word}」不能用（{why}）", None
    if _about_hit(prof, word):
        return f"「{word}」是你自己（關於你）", None
    hit = _collision(data, word, ignore=entry)
    if hit is None:
        return None, None
    if is_self(hit):
        return f"「{word}」是你自己（關於你）", None
    named = next((e for e in data["entities"] if key(e.get("name")) == key(word)), None)
    if named is not None and named.get("status") == "active":
        return None, named
    if named is not None:
        return name_problem(vault, data, word, ignore=entry), None
    return f"「{word}」跟「{hit.get('name')}」的別名撞了", None


def _body_of(text):
    lines = str(text).lstrip("\ufeff").splitlines()
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return ""


def _suspect_cards(vault, data, entry, word):
    """掛在這個實體、正文出現 word 的檔案卡 [{"id","path","title"}]（拿掉別名後逐張看是不是掛錯了）。"""
    out = []
    for m in mounts(vault, data).get(entry["name"], []):
        try:
            body = _body_of((Path(vault) / m["path"]).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if key(word) in key(body):
            out.append({"id": m["id"], "path": m["path"], "title": m.get("title") or ""})
    return out


def alias(vault, ref, add=None, remove=None, turn_id=None):
    """kb entity alias：加或拿掉一般實體的別名（客戶自己的在設定檔第五節改）。加：字元規則同名稱、撞到第五節、別的實體名稱或別名
    就拒絕；撞到的是另一個 active 實體的名稱 → 不寫、回 merge_with（客戶說同一個就合併）；已經是它的名稱或別名 → 不寫。
    拿掉：只收它現有的別名，回 suspect_cards。寫前備份；有綜合卡就重寫檔頭的別名欄（by program）。整段在鎖裡、出錯還原。
    回 {"entity","added","removed","merge_with","suspect_cards","changed"}。"""
    if (add is None) == (remove is None):
        raise ValueError("alias 要給 add 或 remove 其中一個")
    prof = _profile(vault)
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        _need_plain(entry, "加別名" if add is not None else "拿掉別名")
        out = {"entity": entry, "added": False, "removed": False, "merge_with": None, "suspect_cards": [], "changed": []}
        if add is not None:
            word = str(add).strip()
            if any(key(w) == key(word) for w in words(entry)):
                return out
            why, merge_with = _alias_refusal(vault, data, entry, word, prof)
            if why:
                raise EntityRefused(why)
            if merge_with is not None:
                return dict(out, merge_with=merge_with)
            entry.setdefault("aliases", []).append(word)
            out["added"] = True
        else:
            word = str(remove).strip()
            if all(key(a) != key(word) for a in entry.get("aliases") or []):
                raise EntityRefused(f"「{entry['name']}」沒有「{word}」這個別名")
            entry["aliases"] = [a for a in entry["aliases"] if key(a) != key(word)]
            out["removed"] = True
            out["suspect_cards"] = _suspect_cards(vault, data, entry, word)

        def act(undo):
            changed = []
            _save(vault, data, turn_id, undo)
            changed.append({"path": ENTITIES_POSIX, "action": "modify"})
            _refresh_fields(vault, entry, prof, turn_id, changed, undo)
            return changed

        out["changed"] = _guarded(vault, act)
    _export_agenda(vault)
    return out


# ---------- 口述與裁示跟著走（note 改知識第 4 點） ----------

def _knowledge_snapshot(vault, undo):
    """口述清單、裁示與裁示.md、推算清單在動手之前記進 undo（同 _agenda_snapshot）；沒有的檔不記。"""
    from kb._lib import findings, rulings, statements
    for rel in (statements.STATEMENTS_POSIX, rulings.RULINGS_POSIX, rulings.MD_POSIX, findings.FINDINGS_POSIX):
        if (Path(vault) / rel).exists():
            undo.file(rel)


def _knowledge_follow(vault, mapping, turn_id):
    """口述、裁示與推算（B-091）掛的實體名稱跟著換；回給日誌的 changed。"""
    from kb._lib import findings, rulings, statements
    changed = []
    if statements.rename_entity(vault, mapping, turn_id):
        changed.append({"path": statements.STATEMENTS_POSIX, "action": "modify"})
    if rulings.rename_entity(vault, mapping, turn_id):
        changed += [{"path": rulings.RULINGS_POSIX, "action": "modify"}, {"path": rulings.MD_POSIX, "action": "modify"}]
    if findings.rename_entity(vault, mapping, turn_id):
        changed.append({"path": findings.FINDINGS_POSIX, "action": "modify"})
    return changed


# ---------- 日程跟著走（日程第 3 點） ----------

def _drop_close_items(vault, entry, how, turn_id, undo):
    """實體移除或合併掉：它 open 的 close 待處理一起 dismiss（確認後裁定 7a），不然永遠留在待處理.md、asks 不問、客戶清不掉。
    先把待處理兩檔記進 undo（動手出錯一起還原）；回給日誌的 changed。"""
    from kb._lib import pending
    if not (Path(vault) / pending.PENDING_POSIX).exists():
        return []
    undo.file(pending.PENDING_POSIX)
    undo.file(pending.MD_POSIX)
    if pending.dismiss_entity(vault, entry["id"], CLOSE_KIND, how, turn_id):
        return [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
    return []


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


def _export_agenda(vault):
    """entities.json 存好、放開實體的鎖之後叫（D-500 第二輪，T-2）：日程匯出的實體名單立刻跟上；改名合併動到的日程也在這裡匯出。
    匯出已吞錯，不改呼叫端的行為與回傳。"""
    from kb._lib import agenda
    agenda.safe_export(vault)


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
    _export_agenda(vault)
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


# ---------- 封存（放寬丙 12，D-332） ----------

def set_archived(vault, ref, info, turn_id):
    """kb archive 實體：選填欄 archived（{"date","reason","turn"}），info 是 None 就拿掉。跟事類的結案狀態並存、不改它。
    鎖裡備份再寫，沒變不寫；回 (條目, 有沒有寫)。客戶自己、已移除的丟 EntityRefused。"""
    with lock(vault):
        data = load(vault)
        entry, _hit = _need(data, ref)
        if is_self(entry):
            raise EntityRefused(f"「{entry['name']}」是你自己，不能封存")
        if entry.get("status") != "active":
            raise EntityRefused(f"「{entry['name']}」已移除，不用封存")
        if bool(entry.get("archived")) != (info is None):  # 已封存的再封存、沒封存的拿掉，都不動
            return entry, False
        if info is None:
            entry.pop("archived", None)
        else:
            entry["archived"] = dict(info)
        _save(vault, data, turn_id)
    return entry, True


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
    if dirty:
        _export_agenda(vault)
    return out
