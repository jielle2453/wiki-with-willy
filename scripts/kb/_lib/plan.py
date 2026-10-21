"""整理 ingest 的介面（D-132、D-134、D-135）：cards.todo 的結果算成 plan、asks、tell 三樣，併進 --json。

plan：照順序要做的事，每步 {"step","items","do","read"}；多量先問時只留 sync。
asks：排好序、編好號的問題，每題 {"id","kind","question","results":[{"n","label","do","strict","fill"}]}；
      順序：實體夾或綜合卡不見 → 原檔不見 → 裁示遇到原檔改版（ruling_recheck）→ 逾期、日程有出入、結案 → 名字確認；
      號碼從 1 一路編下去不管挑哪幾題，do 的指令以 kb 開頭，要客戶給的值寫成 {新名字} 這種空格，fill 列有哪些。
tell：回答末尾要講的現成句子。
announce：要整理的份數在 ingest_auto_max 以內時，開始整理前先說的一句（份數、大約多久、要停按停止，D-100 修訂），否則 None；
      kb asks（tell_all 是 False）不帶這鍵。
只讀：不寫任何檔，不開原檔。
"""
import math
import re

from kb._lib import agenda, cards, entities, inventory, pending, rulings
from kb._lib.paths import plugin_root

SYNC = "kb cards sync"
CARDING = "references/carding.md"
FREE_TEXT_RULE = "一句白話，不含 \" $ ` \\ 這四種字元"  # 這四個字元放進雙引號會壞引號或被展開
NAME_RULE = "一個名字，不含 \" $ ` \\ 這四種字元；有空白就加雙引號"  # 名字也會被貼進指令（entities.name_problem 同樣擋）
DATE_RULE = "YYYY-MM-DD"
AGENDA_RECHECK = "agenda_recheck"
AGENDA_UNSCANNED = "agenda_unscanned"
BATCH_SECONDS = 10  # 一份抓 10 秒（D-100）
ANNOUNCE = "有 {n} 份要整理，大約{how_long}；要停的話按停止"  # 門檻內開始前先說（D-100 修訂）：整理中打字要等回合結束，停止鈕才停得下來
UNREADABLE_LISTED = 5
PENDING_FILE = "記在 系統/待處理.md"
# 讀不到的原因（convert 會記進待處理的那幾種）→ 客戶怎麼辦；這是講法的唯一來源，commands.md 只指向 tell
UNREADABLE_ADVICE = (
    ("有密碼", "另存一份沒密碼的，或跟我說先跳過"),
    ("舊格式", "用 Word／Excel 另存成新格式就讀得到"),
    ("不支援的格式", "這種格式還讀不了，跟我說先跳過，或換一份檔"),
    ("只在雲端", "請在檔案上按右鍵選保留在這台電腦上，我再整理"),
    ("還沒裝 PDF 元件", "請服務人員跑 pip install pypdfium2，裝好後我會自動整理"),
    ("檔案壞了", "換一份，或跟我說先跳過"),
    ("檔案太大", "這版讀不了，跟我說先跳過"),
    ("編碼", "換一份檔，或跟我說先跳過"),
)
SKIPPED_TEXT = (("credential", "檔名像密碼或金鑰"), ("placeholder", "只在雲端"), ("too_large", "太大"),
                ("error", "讀不到"))
CLOUD_SENTENCE = "請在檔案上按右鍵選保留在這台電腦上，我再整理"
_SAFE = re.compile(r"[\w./:@+=,\-]+")
_ESCAPE = re.compile(r'(["\\$`])')


class Slot(str):
    """要客戶給的值，印出來是 {名}，不加引號。"""

    def __new__(cls, name):
        return str.__new__(cls, "{" + name + "}")


def quote(arg):
    """指令參數：安全字元（含中文）照寫，有空白或特殊字元加雙引號、裡面的 " \\ $ ` 前面加反斜線。"""
    if isinstance(arg, Slot):
        return str(arg)
    arg = str(arg)
    if _SAFE.fullmatch(arg):
        return arg
    return '"' + _ESCAPE.sub(r"\\\1", arg) + '"'


def cmd(*parts):
    return "kb " + " ".join(quote(p) for p in parts)


def build(out, vault, config, tell_all=True):
    """回 {"plan","asks","tell","announce"}；config 是 kb._lib.config（取 ingest_auto_max）。
    tell_all 是 False 時 tell 只帶這一回合做了什麼（加進日程、照新版改了），整理狀態那些句子不帶（D-141，kb asks 用），
    也不帶 announce。"""
    full = _plan(out)
    asks = _asks(out, vault, config, full)
    batch = bool(asks) and asks[0]["id"] == "batch"
    steps = [s for s in full if s["step"] == "sync"] if batch else full
    if not tell_all:
        return {"plan": steps, "asks": _number(asks), "tell": agenda.tell_lines(vault)}
    waiting = _waiting(out)
    announce = ANNOUNCE.format(n=waiting, how_long=_how_long(waiting)) if waiting and not batch else None
    return {"plan": steps, "asks": _number(asks), "tell": _tell(out, vault), "announce": announce}


def _waiting(out):
    """要整理的份數（門檻與 announce 共用）：new＋stale＋unlinked＋agenda_unscanned。"""
    return sum(len(out.get(k) or []) for k in ("new", "stale", "unlinked", AGENDA_UNSCANNED))


def _how_long(waiting):
    seconds = waiting * BATCH_SECONDS
    return "一兩分鐘" if seconds < 60 else f" {math.ceil(seconds / 60)} 分鐘"


# ---------- plan ----------

def _step(step, items, do, read):
    return {"step": step, "items": items, "do": do, "read": read}


def _synthesis_rules():
    return str(plugin_root() / "rules" / "synthesis.md")


def _plan(out):
    steps = []
    sync = len(out.get("moved") or []) + len(out.get("duplicates") or [])
    if sync:
        steps.append(_step("sync", sync, SYNC, None))
    carding = (len(out.get("new") or []) + len(out.get("stale") or [])
               + sum(1 for b in out.get("broken") or [] if b.get("header")))
    if carding:
        steps.append(_step("cards", carding, "照清單寫卡（new、stale、broken）", f"{CARDING} 一～六節"))
    if out.get(AGENDA_RECHECK):
        steps.append(_step(AGENDA_RECHECK, len(out[AGENDA_RECHECK]), "照清單重對日程（agenda_recheck）", f"{CARDING} 第九節"))
    if out.get(AGENDA_UNSCANNED):  # 先重對再抽，重做的卡不會多出重複的條目（D-140）
        steps.append(_step(AGENDA_UNSCANNED, len(out[AGENDA_UNSCANNED]), "照清單抽日期（agenda_unscanned）", f"{CARDING} 第九節"))
    for key, read, text in (("unlinked", f"{CARDING} 第七節", "照清單補掛實體（unlinked）"),
                            ("synthesis_broken", f"{CARDING} 第八節", "照清單處理放錯位置的綜合卡（synthesis_broken）"),
                            ("synthesis_stale", _synthesis_rules(), "照清單改綜合卡（synthesis_stale）")):
        if out.get(key):
            steps.append(_step(key, len(out[key]), text, read))
    confirmed = [i for i in out.get("synthesis_new") or [] if i.get("confirmed")]
    if confirmed:
        steps.append(_step("synthesis_new", len(confirmed), "照清單寫綜合卡（synthesis_new）", _synthesis_rules()))
    return steps


# ---------- asks ----------

def _fill(name, rule):
    return {"name": name, "rule": rule}


def _result(label, do=(), strict=False, fill=(), **extra):
    return dict({"n": 0, "label": label, "do": list(do), "strict": strict, "fill": list(fill)}, **extra)


def _ask(ask_id, kind, question, results):
    return {"id": ask_id, "kind": kind, "question": question, "results": results}


def _asks(out, vault, config, full_plan):
    asks = []
    waiting = _waiting(out)
    if waiting > config.get(vault, "ingest_auto_max"):
        asks.append(_batch(waiting, full_plan))
    drifts = out.get("entity_drift") or []
    asks.extend(_drift(d, out) for d in drifts if d["kind"] == "missing")
    asks.extend(_drift(d, out) for d in drifts if d["kind"] != "missing")
    asks.extend(_missing(m) for m in out.get("missing") or [])
    asks.extend(_ruling_asks(vault))  # 「別信」蓋在新版上會讓綜合卡漏掉新內容，排在逾期之前（note 改知識釐清）
    asks.extend(_agenda_asks(vault))
    asks.extend(_name(i) for i in out.get("synthesis_new") or [] if not i.get("confirmed"))
    return asks


def _batch(waiting, full_plan):
    return _ask("batch", "batch", f"有 {waiting} 份要整理，現在做嗎？大約{_how_long(waiting)}", [
        _result("現在做", note="客戶說做就照 then 的步驟做，做的過程這題不再問", then=full_plan),
        _result("之後再說", note="這個對話不再問")])


def _rename(name, to, *more):
    return cmd("entity", "rename", name, "--to", to, *more)


def _rename_results(name, new, label):
    return [_result(label, [_rename(name, new)]),
            _result(f"改回{name}", [_rename(name, name)]),
            _result("改成別的名字", [_rename(name, Slot("新名字"))], fill=[_fill("新名字", NAME_RULE)])]


def _drift(d, out):
    name, found = d["name"], d.get("found") or ""
    parts = found.split("/")
    kind = d["kind"]
    if kind in ("folder_renamed", "both_renamed"):
        results = _rename_results(name, parts[1], f"跟著改名叫{parts[1]}")
    elif kind == "card_renamed":
        stem = parts[2][:-3]
        results = _rename_results(name, stem, f"用新檔名{stem}")
    elif kind == "moved_type":
        top = parts[0]
        known = {entities.key(t["name"]) for t in (out.get("profile") or {}).get("types") or []}
        if entities.key(top) in known:
            first = _result(f"跟著改到{top}", [_rename(name, name, "--type", top)])
        else:
            first = _result(f"先把{top}加進設定檔",
                            note=f"型別表沒有「{top}」：請客戶在客戶設定檔第一節加一列，下次開對話我會問要不要套用；這個結果不跑指令")
        results = [first, _result(f"搬回{d['type']}", [_rename(name, name)])]
    else:
        results = [_result("不要了", [cmd("entity", "remove", name)], strict=True),
                   _result("刪錯了，我從資源回收筒還原"),
                   _result("刪錯了，照檔案卡重寫一張", [cmd("entity", "rebuild", name)])]
    return _ask(f"drift:{d['entity_id']}", f"drift_{kind}", d["question"], results)


def _missing(m):
    pid = m["pending_id"]
    return _ask(f"missing:{pid}", "missing", m["question"], [
        _result("刪了，卡也拿掉", [cmd("cards", "drop", m["id"])], strict=True),
        _result("沒刪，先留著", [cmd("pending", "resolve", pid, "--how") + ' "{客戶的話}"'],
                fill=[_fill("客戶的話", FREE_TEXT_RULE)]),
        _result("不用管", [cmd("pending", "dismiss", pid)])])


def _name(item):
    name = item["name"]
    return _ask(f"name:{item['entity_id']}", "name", item["question"], [
        _result("名字對", [cmd("entity", "confirm", name)]),
        _result("跟已有的另一個是同一個", [cmd("entity", "merge", name, "--into", Slot("同一個"))],
                fill=[_fill("同一個", NAME_RULE)]),
        _result("名字改成別的", [_rename(name, Slot("新名字")), cmd("entity", "confirm", Slot("新名字"))],
                fill=[_fill("新名字", NAME_RULE)])])


# ---------- 裁示遇到原檔改版（note 改知識第 7 點，D-150） ----------

RULING_QUESTIONS = {
    "ruled_out": ("「{files}」改過了，你之前說別信，現在可以信了嗎？", "可以了", "還是別信"),
    "spoken_wins": ("「{files}」改過了，你之前說以你說的為準，現在還是嗎？", "改用新版", "還是以我說的為準"),
}


def _ruling_asks(vault):
    """掛卡的有效裁示、卡指紋變了：收回或改記新指紋。rulings.json 讀不到就不問。"""
    try:
        data = rulings.load(vault)
    except ValueError:
        return []
    out = []
    for hit in rulings.drifted(vault, data):
        r = hit["ruling"]
        question, yes, no = RULING_QUESTIONS[r["kind"]]
        files = "」「".join(c["file"] for c in hit["cards"])
        out.append(_ask(f"ruling:{r['id']}", "ruling_recheck", question.format(files=files), [
            _result(yes, [cmd("ruling", "remove", r["id"])]), _result(no, [cmd("ruling", "recheck", r["id"])])]))
    return out


# ---------- 日程的三種題（日程第 5 點：逾期、日程有出入、結案） ----------

def _agenda_asks(vault):
    """逾期（程式自己認得）、日程有出入（條目有 proposal）、結案（待處理 kind close）。狀態檔讀不到就不問，開場那行會記 log。"""
    out = []
    titles = None
    try:
        data = agenda.load(vault)
    except ValueError:
        data = {"items": []}
    out.extend(_overdue(it) for it in agenda.overdue_items(data))
    proposals = agenda.proposal_items(data)
    if proposals:
        titles = agenda.card_titles(vault)
        out.extend(_conflict(it, titles.get(it["proposal"].get("card"))) for it in proposals)
    out.extend(_close_asks(vault, titles))
    return out


def _with_entities(it):
    names = it.get("entities") or []
    return it["text"] + (f"（{'、'.join(names)}）" if names else "")


def _overdue(it):
    aid = it["id"]
    return _ask(f"overdue:{aid}", "overdue", f"{agenda.md_of(it['date'])} {_with_entities(it)}過了，完成了嗎？", [
        _result("完成了", [cmd("agenda", "done", aid)]),
        _result("不用了", [cmd("agenda", "cancel", aid)], strict=True),
        _result("還沒，改到某天", [cmd("agenda", "edit", aid, "--date", Slot("新日期"))], fill=[_fill("新日期", DATE_RULE)]),
        _result("還沒，下次再問", note="這個對話不再問")])


def _conflict(it, title):
    aid, prop = it["id"], it["proposal"]
    newer = f"新版{title}寫" if title else "新版寫"
    if prop["date"] != it["date"]:
        old, new = agenda.md_of(it["date"]), agenda.md_of(prop["date"])
        question = f"{it['text']}你之前說 {old}，{newer} {new}，要用哪個？"
        edit = ["agenda", "edit", aid, "--date", prop["date"]]
        if prop["text"] != it["text"]:
            edit += ["--text", prop["text"]]
        labels = (f"用新版 {new}", f"維持 {old}")
    else:
        question = f"{it['text']}你之前說「{it['text']}」，{newer}「{prop['text']}」，要用哪個？"
        edit = ["agenda", "edit", aid, "--text", prop["text"]]
        labels = (f"用新版「{prop['text']}」", f"維持「{it['text']}」")
    return _ask(f"conflict:{aid}", "agenda_conflict", question, [
        _result(labels[0], [cmd(*edit)]), _result(labels[1], [cmd("agenda", "recheck", aid)])])


def _close_asks(vault, titles):
    try:
        items = [i for i in pending.load(vault)["items"] if i.get("kind") == "close" and i.get("status") == "open"]
        if not items:
            return []
        names = {e.get("id"): e for e in entities.load(vault)["entities"]}
    except ValueError:
        return []
    titles = agenda.card_titles(vault) if titles is None else titles
    out = []
    for item in items:
        entity = next((names[i] for i in item.get("entity_ids") or [] if i in names), None)
        if entity is None or entity.get("status") != "active":
            continue
        evidence = list(item.get("file_ids") or [])
        flags = [a for f in evidence for a in ("--evidence", f)]
        name = entity["name"]
        question = item.get("text", "") + f"（依據：{'、'.join(titles.get(f, f) for f in evidence)}）"
        out.append(_ask(f"close:{item['id']}", "close", question, [
            _result("對，結案了", [cmd("entity", "progress", name, "結案", *flags)]),
            _result("還沒", [cmd("entity", "progress", name, "進行中", *flags, "--note", "客戶說還沒結案")]),
            _result("先暫停了", [cmd("entity", "progress", name, "擱置", *flags)])]))
    return out


def _number(asks):
    n = 0
    for ask in asks:
        for r in ask["results"]:
            n += 1
            r["n"] = n
    return asks


# ---------- tell ----------

def _tell(out, vault):
    lines = []
    unreadable = out.get("unreadable") or []
    if unreadable:
        for item in unreadable[:UNREADABLE_LISTED]:
            name = item["uri"].rsplit("/", 1)[-1]
            lines.append(_unreadable_line(name, item.get("reason")))
        if len(unreadable) > UNREADABLE_LISTED:
            lines.append(f"另有 {len(unreadable) - UNREADABLE_LISTED} 份讀不到")
        lines[-1] += f"；{PENDING_FILE}"
    skipped = out.get("skipped") or {}
    counted = [f"{text} {skipped[k]}" for k, text in SKIPPED_TEXT if skipped.get(k)]
    if counted:
        lines.append("不製卡：" + "、".join(counted))
    if skipped.get("placeholder"):
        lines.append(CLOUD_SENTENCE)
    lines.extend(_by_setting(skipped.get("skipped") or 0, vault))
    for type_, n in (out.get("missing_types") or {}).items():
        lines.append(f"有 {n} 份看起來跟 {type_} 有關，但設定檔裡沒有『{type_}』這一類，"
                     "要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用")
    if out.get("pdf_restored"):
        lines.append(f"PDF 元件裝好了，{out['pdf_restored']} 份 PDF 回到待製卡")
    lines.extend(agenda.tell_lines(vault))
    return lines


def _unreadable_line(name, reason):
    if not reason:
        return f"{name} 讀不到"
    advice = next((text for head, text in UNREADABLE_ADVICE if reason.startswith(head)), None)
    return f"{name} 讀不到：{reason}；{advice}" if advice else f"{name} 讀不到：{reason}"


def _by_setting(total, vault):
    """依設定不整理：按清冊的 skip_kind 分種類（算法同 cards.todo）；清冊讀不到或對不上就只講份數。"""
    if not total:
        return []
    kinds = {}
    try:
        entries = inventory.load(vault)["entries"]
    except (ValueError, OSError, KeyError):
        entries = []
    for e in entries:
        if (e.get("status") == "active" and e.get("locations") and e.get("read") not in cards.SKIPPED_READS
                and e.get("skip_kind")):
            kinds[e["skip_kind"]] = kinds.get(e["skip_kind"], 0) + 1
    if sum(kinds.values()) != total:
        return [f"{total} 份依你的設定不整理"]
    return [f"{kind}{'' if kind.endswith('類') else '類'} {n} 份依你的設定不整理" for kind, n in kinds.items()]
