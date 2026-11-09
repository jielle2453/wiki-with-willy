"""整理 ingest 的介面（D-132、D-134、D-135）：cards.todo 的結果算成 plan、asks、tell 三樣，併進 --json。

plan：照順序要做的事，每步 {"step","items","do","read"}；多量先問時只留 sync。做卡、抽日程、寫綜合卡收成一步 dispatch
      （kb cards dispatch --json，交給背景的 card-maker，D-196～D-198）；link 條目、檔名像密碼的、記過有密碼的留一步 cards 給主對話。
asks：排好序、編好號的問題，每題 {"id","kind","question","results":[{"n","label","do","strict","fill"}]}；
      順序：實體夾或綜合卡不見 → 原檔不見 → 裁示遇到原檔改版（ruling_recheck）→ 逾期、日程有出入、結案 → 草稿還在等（draft，
      produce）→ 收件匣的檔要放哪（inbox_place，收件匣第 5 點）→ 名字確認；
      號碼從 1 一路編下去不管挑哪幾題，do 的指令以 kb 開頭，要客戶給的值寫成 {新名字} 這種空格，fill 列有哪些。
tell：回答末尾要講的現成句子；兩份寫法不同的（待處理 conflict，不進整理題）件數只由 kb asks 的 others_line 講（lint 題，D-225）。
密碼與守門（D-181、D-182、D-183）：plan 多 unlock_same_kind（授權過的種類在、待處理有新的「有密碼」PDF：列檔與每一種的例子給 AI 判斷，
排在寫卡之前、份數算進 ingest_auto_max）；asks 多 credential_same_kind（有帶 label 的「可以讀」裁示時，每份新的檔名像密碼的檔一題，
排在原檔不見之後）；tell 的「有密碼」PDF 與 Word／Excel 分開講、不製卡的密碼檔接「要讓我讀哪一份跟我說」、這一回合用存好的密碼開了的講一句。
announce：要整理的份數在 ingest_auto_max 以內時，開始整理前先說的一句（份數、大約多久、要停說一聲，D-199、D-200），否則 None；
      kb asks（tell_all 是 False）不帶這鍵。
名字（D-202，build 第 7 點）：有到門檻還沒確認的新名字（synthesis_new 沒確認的、unconfirmed_names）時，plan 最前面多一步 names：
AI 讀掛的卡判斷，有把握的 kb entity accept（記成 AI 判斷，算確認），有疑慮的不動、留給 asks 的名字題；
AI 判斷登記的（ai_judged）不算沒確認，不出名字題。tell 多一句這一回合 AI 判斷登記了哪幾個。
回答末尾的問法（lint 題，D-225、D-226）：每題多 key（穩定的鍵：住在 pending.json 的是 p- 編號，其他是題號加問句的指紋，
對象變了問句就變、鍵跟著變）、about（{"entities","files"}：這題的對象，檔案卡掛的實體也算）、urgent（有時間性：逾期、
日程條目離到期剩 set_aside_wake_days 天）；先放著的（pending 的 set_aside 還沒該回來、.system/set_aside.json 記的鍵）不列，
有時間性的照列；build 多回 set_aside（藏了幾題）。pending_asks 把 pending.json 裡沒有整理題的那幾種（讀不到、寫法不同、
程式動到的、健檢、報告、手改）也做成題，給 kb asks 的範圍與 --all 用；order 照急的排。
只讀：不寫任何檔，不開原檔。
"""
import hashlib
import math
import os
import re
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from kb._lib import agenda, batches, cards, entities, inventory, pending, profile, rulings, turn
from kb._lib.paths import plugin_root

SYNC = "kb cards sync"
CARDING = "references/carding.md"
FREE_TEXT_RULE = "一句白話，不含 \" $ ` \\ 這四種字元"  # 這四個字元放進雙引號會壞引號或被展開
NAME_RULE = "一個名字，不含 \" $ ` \\ 這四種字元；有空白就加雙引號"  # 名字也會被貼進指令（entities.name_problem 同樣擋）
DATE_RULE = "YYYY-MM-DD"
AGENDA_RECHECK = "agenda_recheck"
AGENDA_UNSCANNED = "agenda_unscanned"
PURGE_STEPS = ("purge_synthesis", "purge")
DISPATCH = "kb cards dispatch --json"
RESUME = "resume"
RESUME_NOTE = "這個對話說過停：客戶說「整理」「繼續整理」才跑 do 接著做；沒說就不做，照常回答"
NAMES = "names"
NAMES_DO = "到門檻的新名字：有把握的 kb entity accept，有疑慮的留給 asks"  # D-202
JUDGED_TELL = "名字有把握的我直接登記了 {n} 個（{names}）；要看名單或有不對的跟我說"
JUDGED_SHOWN = 5
TAGGED_TELL = "{name}標了：{what}"  # 標籤字典第五節（D-238）：這一回合標籤正本多了的詞（D-505，沒有綜合卡的也講）
TAGGED_MORE = "另外 {n} 個也照字典標了"
TAGGED_SHOWN = 3
ANNOUNCE = "有 {n} 份要整理，大約{how_long}；要停的話跟我說一聲"  # 門檻內開始前先說（D-199、D-200）：背景整理時打字就停得下來
BATCH_QUESTION = "有 {n} 份要整理，大約{how_long}；份數多，會用掉比平常多的額度。現在做嗎？"  # D-199：只講時間，用量一句話
UNREADABLE_LISTED = 5
PENDING_FILE = "記在 系統/待處理.md"
# 讀不到的原因（convert 會記進待處理的那幾種）→ 客戶怎麼辦；這是講法的唯一來源，commands.md 只指向 tell
PDF_LOCKED_ADVICE = "給我密碼就能打開，要我記住也可以"  # 密碼與守門：只解 PDF（D-182）
OFFICE_LOCKED_ADVICE = "這種檔這版還打不開，用 Office 打開另存一份沒密碼的再放進來"
CREDENTIAL_TAIL = "；要讓我讀哪一份跟我說「X 可以讀」"
SAME_KIND_OPENED = "「{name}」我判斷跟之前的{label}是同一種，用那組密碼打開了"
SAME_KIND_QUESTION = "又有一份「{name}」，跟之前的{label}一樣可以讀嗎？"
SAME_KIND_NOTE = ("先判斷這份是不是 labels 裡的哪一種（看檔名、夾、例子）；是就用那一種的 label 問與記，"
                  "都不是就跑「不是同一種」那個結果、不問她")
UNLOCK_STEP = "unlock_same_kind"
UNREADABLE_ADVICE = (
    ("有密碼", PDF_LOCKED_ADVICE),
    ("舊格式", "用 Word／Excel 另存成新格式就讀得到"),
    ("不支援的格式", "這種格式還讀不了，跟我說先跳過，或換一份檔"),
    ("只在雲端", "請在檔案上按右鍵選保留在這台電腦上，我再整理"),
    ("還沒裝 PDF 元件", "請服務人員跑 pip install pypdfium2，裝好後我會自動整理"),
    ("PDF 元件出錯", "這台電腦擋了讀 PDF 的元件；請服務人員跑 kb build env 看 PDF 那一項"),  # build：工具夾的元件在子行程起不來
    ("檔案壞了", "換一份，或跟我說先跳過"),
    ("檔案太大", "這版讀不了，跟我說先跳過"),
    ("編碼", "換一份檔，或跟我說先跳過"),
    ("線上文件打不開", "到 claude.ai 確認那份還在、有分享給這個帳號，或跟我說先跳過"),  # B-415：kb cards unreadable 記的
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


CURLY_QUOTES = frozenset(chr(c) for c in range(0x2018, 0x2020))  # ‘’‚‛“”„‟：PowerShell 也當引號（第二輪修）


def shell_arg(arg):
    """指令參數：安全字元（含中文）照寫，有空白或特殊字元加雙引號、裡面的 " \\ 前面加反斜線；有 $ 或反引號改用單引號
    （雙引號裡 PowerShell 會展開，manage 第二版測試）；同時又有單引號就給不出來，回 None。"""
    arg = str(arg)
    if CURLY_QUOTES & set(arg):  # 彎引號比照「有單引號」：單雙引號都包不住，不給指令
        return None
    if "$" in arg or "`" in arg:
        return None if "'" in arg else f"'{arg}'"
    if _SAFE.fullmatch(arg):
        return arg
    return '"' + _ESCAPE.sub(r"\\\1", arg) + '"'


_URI_ENCODE = {'"': "%22", "$": "%24", "`": "%60", "\\": "%5C", "%": "%25"}


def uri_arg(path):
    """「別名/相對路徑」寫成 % 編碼的清冊 uri（file://別名/相對路徑；" $ ` \\ % 與彎引號編碼），kb 收路徑的參數都解得回來
    （paths.decode_uri）。名字有這些字元時 kb 給的指令一律用這種寫法，不再叫使用者去改名（放寬甲）。"""
    out = []
    for ch in str(path).replace("\\", "/"):
        out.append(_URI_ENCODE.get(ch) or (urllib.parse.quote(ch) if ch in CURLY_QUOTES else ch))
    text = "".join(out)
    return text if text.startswith("file://") else "file://" + text


def path_arg(path):
    """路徑參數：一般照 shell_arg；給不出來（引號配不起來）就改給 % 編碼的 uri。"""
    return shell_arg(path) or shell_arg(uri_arg(path))


def quote(arg):
    """Slot 照印；其他照 shell_arg，給不出來丟 ValueError（呼叫端事先擋掉這種名字）。"""
    if isinstance(arg, Slot):
        return str(arg)
    got = shell_arg(arg)
    if got is None:
        raise ValueError(f"參數同時有 $ 或反引號和單引號：{arg}")
    return got


def cmd(*parts):
    """kb 指令字串；有參數給不出引號（shell_arg 回 None）就回 None，呼叫端那一項不給指令、帶 note（第二輪修）。"""
    try:
        return "kb " + " ".join(quote(p) for p in parts)
    except ValueError:
        return None


def build(out, vault, config, tell_all=True):
    """回 {"plan","asks","tell","announce"}；config 是 kb._lib.config（取 ingest_auto_max）。
    tell_all 是 False 時 tell 只帶這一回合做了什麼（加進日程、照新版改了），整理狀態那些句子不帶（D-141，kb asks 用），
    也不帶 announce。"""
    state = _session_state(vault)
    full = _plan(out, vault, state)
    asks = _asks(out, vault, config, full, state)
    batch = bool(asks) and asks[0]["id"] == "batch"
    steps = [s for s in full if s["step"] in ("sync",) + PURGE_STEPS] if batch else full  # 清殘留批次時也留（疑義第 8 條）
    asks, hidden = visible(vault, annotate(vault, asks, config))  # 先放著的不列（D-226），有時間性的照列
    if not tell_all:
        return {"plan": steps, "asks": _number(asks), "tell": agenda.tell_lines(vault) + _money_tell(vault) + _tagged_tell(vault),
                "set_aside": hidden}
    waiting = _waiting(out, vault)
    announce = ANNOUNCE.format(n=waiting, how_long=_how_long(waiting, vault, config)) if waiting and not batch else None
    return {"plan": steps, "asks": _number(asks), "tell": _tell(out, vault), "announce": announce, "set_aside": hidden}


def _waiting(out, vault):
    """要整理的份數（門檻與 announce 共用）：new＋stale＋unlinked＋agenda_unscanned＋同一種的加密 PDF；前四種跟 dispatch
    同一套排除（線上文件、檔名像密碼的、有密碼的、還沒清的排除夾不算，card-maker 第一輪修）。"""
    return batches.waiting(vault, out) + len(out.get(UNLOCK_STEP) or [])


def _how_long(waiting, vault, config):
    """秒數 = 份數 × card_seconds_per_file + card_batch_overhead_seconds（D-199，spike 第 7 題）；不到 60 秒說一兩分鐘。"""
    seconds = waiting * config.get(vault, "card_seconds_per_file") + config.get(vault, "card_batch_overhead_seconds")
    return "一兩分鐘" if seconds < 60 else f" {math.ceil(seconds / 60)} 分鐘"


# ---------- plan ----------

def _step(step, items, do, read, note=None):
    out = {"step": step, "items": items, "do": do, "read": read}
    if note:
        out["note"] = note
    return out


def _synthesis_rules():
    return str(plugin_root() / "rules" / "synthesis.md")


MAIN_CARDS = ("照 files 自己做（這幾份不交給製卡助手）：編號是線上文件、檔名像密碼的、有密碼的，照 new、stale 的材料寫卡；"
              "路徑是找不到屬於哪個實體的壞綜合卡，照 synthesis_broken 的 reason 與 references/synthesis-broken.md 處理，處理不了就跟客戶說")


def _names(out, vault):
    """還沒確認的新名字（synthesis_new 沒確認的、壞或要更新的綜合卡的實體沒確認的）：[{"entity_id","name","type","question"
    （有的話）,"cards"}]；cards 是掛的檔案卡標題，給 AI 判斷有沒有把握（D-202）。"""
    got = [{"entity_id": i["entity_id"], "name": i["name"], "type": i.get("type"),
            "cards": [c.get("title") for c in i.get("cards") or []]}
           for i in out.get("synthesis_new") or [] if not i.get("confirmed")]
    got += [{"entity_id": i["entity_id"], "name": i["name"], "type": i.get("type"), "cards": []}
            for i in batches.unconfirmed_names(vault, out)]
    return got


def _session_state(vault):
    """這個對話的整理狀態（B 段落差 4、6）：kb.py --session 給的，沒有就讀 current.json 的那個對話；回合旗標從那個對話的回合檔讀。
    只讀，不開回合。"""
    sid = turn.SESSION
    cur = turn.now(vault) or {}
    return batches.session_state(vault, sid or cur.get("session_id"), cur)


def _plan(out, vault, state=None):
    steps = _purge_steps(out)
    names = _names(out, vault)
    if names:  # 名字先判斷：有把握的登記了，這一句的 dispatch 就把它們的綜合卡一起派（D-202）
        step = _step(NAMES, len(names), NAMES_DO, f"{CARDING} 第十一節")
        step["names"] = names
        steps.append(step)
    sync = len(out.get("moved") or []) + len(out.get("duplicates") or [])
    if sync:
        steps.append(_step("sync", sync, SYNC, None))
    if out.get(UNLOCK_STEP):  # 解開才寫得了卡：排在寫卡之前（D-181 第 2 點）
        step = _step(UNLOCK_STEP, len(out[UNLOCK_STEP]),
                     "對每份判斷是哪一種，是就跑 kb convert <id> --saved <那一種的密碼名稱>；不是任何一種的不動", f"{CARDING} 第十節")
        step.update(files=out[UNLOCK_STEP], kinds=out.get("unlock_kinds") or [])
        steps.append(step)
    parts = batches.split(vault, out)
    sent = sum(len(parts[k]) for k in batches.KINDS)
    if sent and state == "stopped":  # 說過停：最前面給 resume，客戶說整理才跑（B 段落差 6，D-200）
        step = _step(RESUME, sent, DISPATCH, None)
        step["note"] = RESUME_NOTE
        steps.insert(0, step)
    elif sent:  # 做卡、抽日程、寫綜合卡一律交給 card-maker（D-196、D-198）；清單由 dispatch 切
        steps.append(_step("dispatch", sent, DISPATCH, None))
    if parts["main"]:  # card-maker 讀不到線上文件、不碰密碼檔：留給主對話（B-415、card-maker 交界第 1 點）
        step = _step("cards", len(parts["main"]), MAIN_CARDS, f"{CARDING} 一～六節")
        step["files"] = [key for _b, key, _i in parts["main"]]
        steps.append(step)
    return steps


def _purge_steps(out):
    """manage 第二版（D-174）：有還沒清的「不想讓 AI 看到」排除時排最前面：先重寫引用那些卡的綜合卡，再 kb folders purge；
    不算進 ingest_auto_max。"""
    pend = out.get("purge_pending") or []
    steps = []
    rewrite = [s for p in pend for s in p.get("synthesis") or []]
    if rewrite:
        steps.append(_step("purge_synthesis", len(rewrite), "照清單重寫綜合卡，拿掉那夾的卡的內容（purge_pending）",
                           _synthesis_rules()))
    for p in pend:
        steps.append(_step("purge", 1, f"kb folders purge {path_arg(p['name'])}", None))
    return steps


# ---------- asks ----------

def _fill(name, rule):
    return {"name": name, "rule": rule}


NO_CMD_NOTE = "名字裡有引號一類的特殊符號，指令給不出來：路徑改用 file:// 加 %22、%24、%60、%5C、%25 編碼的 uri 寫法；其他照這個結果用白話說給使用者聽"


def _result(label, do=(), strict=False, fill=(), **extra):
    do = list(do)
    if any(d is None for d in do):  # 給不出指令：這一項不跑指令，帶 note（kb asks、cards todo 不能因此 exit 3）
        do = []
        extra = dict(extra, note=extra.get("note") or NO_CMD_NOTE)
    return dict({"n": 0, "label": label, "do": do, "strict": strict, "fill": list(fill)}, **extra)  # extra 可帶 argv／after（給外掛，Obsidian 首頁）


def _ask(ask_id, kind, question, results):
    return {"id": ask_id, "kind": kind, "question": question, "results": results}


def _asks(out, vault, config, full_plan, state=None):
    asks = []
    waiting = _waiting(out, vault)
    # 這個對話已經在整理、或說過停（客戶先前答應過）：不再問批次題（B 段落差 4）；說過停的接著做靠 plan 的 resume
    if waiting > config.get(vault, "ingest_auto_max") and state is None:
        asks.append(_batch(waiting, full_plan, vault, config))
    drifts = out.get("entity_drift") or []
    asks.extend(_drift(d, out) for d in drifts if d["kind"] == "missing" and _ask_missing_drift(d))
    asks.extend(_drift(d, out) for d in drifts if d["kind"] != "missing")
    asks.extend(_missing(m) for m in out.get("missing") or [])
    asks.extend(_credential_asks(vault, out))  # 密碼與守門：排在原檔不見之後、逾期之前
    asks.extend(_ruling_asks(vault))  # 「別信」蓋在新版上會讓綜合卡漏掉新內容，排在逾期之前（note 改知識釐清）
    asks.extend(_agenda_asks(vault))
    asks.extend(_draft_asks(vault))
    asks.extend(_inbox_asks(vault))
    asks.extend(_name(i) for i in out.get("synthesis_new") or [] if not i.get("confirmed"))
    asks.extend(_name(i) for i in batches.unconfirmed_names(vault, out))  # 壞的、要更新的綜合卡也先問名字（card-maker R5）
    return asks


def _batch(waiting, full_plan, vault, config):
    return _ask("batch", "batch", BATCH_QUESTION.format(n=waiting, how_long=_how_long(waiting, vault, config)), [
        _result("現在做", note="客戶說做就照 then 的步驟做，做的過程這題不再問", then=full_plan),
        _result("之後再說", note="這個對話不再問")])


def _rename(name, to, *more):
    return cmd("entity", "rename", name, "--to", to, *more)


def _rename_results(name, new, label):
    return [_result(label, [_rename(name, new)]),
            _result(f"改回{name}", [_rename(name, name)]),
            _result("改成別的名字", [_rename(name, Slot("新名字"))], fill=[_fill("新名字", NAME_RULE)])]


DRIFT_RECENT_DAYS = 30


def _ask_missing_drift(d):
    """綜合卡不見（放寬丙 6-1，D-311）：交 AI 判斷，只有進行中（progress 空的也算，跟 entities 一致）、last_activity 30 天內、
    similar 與 same_name_new 空、deleted_with 0（沒有線索）才照 D-310 問一題；其他不出題。"""
    last = d.get("last_activity")
    if (d.get("progress") or "ongoing") != "ongoing" or not last or d.get("similar") or d.get("same_name_new") or d.get("deleted_with"):
        return False
    try:
        return (date.today() - date.fromisoformat(last)).days <= DRIFT_RECENT_DAYS
    except ValueError:
        return False


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
        top = d.get("found_type") or parts[0]  # 型別夾編號：--type 給型別名，不是夾名
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


# ---------- 同一種的密碼檔（密碼與守門，D-181 第 1 點） ----------

def credential_kinds(vault):
    """有帶 label 的有效「可以讀」裁示：[{"label","examples":[檔名]}]（同 label 併起來）；讀不到回空。"""
    try:
        data = rulings.load(vault)
    except ValueError:
        return []
    names = rulings.file_names(vault)
    kinds = {}
    for r in data["rulings"]:
        if r.get("status") == "active" and r.get("kind") == "credential_read" and r.get("label"):
            k = kinds.setdefault(r["label"], {"label": r["label"], "examples": []})
            k["examples"] += [names.get(f) or f for f in r.get("files") or [] if (names.get(f) or f) not in k["examples"]]
    return list(kinds.values())


def _credential_asks(vault, out):
    """每份新的檔名像密碼的檔（清冊 read credential、沒有裁示、沒說過不要）一題，附 labels 給 AI 判斷是不是同一種。"""
    kinds = credential_kinds(vault)
    if not kinds:
        return []
    try:
        entries = inventory.load(vault)["entries"]
        readable = rulings.readable_ids(vault)
    except (ValueError, OSError):
        return []
    asks = []
    for e in entries:
        if (e.get("status") != "active" or e.get("read") != "credential" or e.get("id") in readable
                or e.get("credential_declined") or not e.get("locations")):
            continue
        name = rulings.file_name(e)
        fid, label = e["id"], kinds[0]["label"]
        ask = _ask(f"credential:{fid}", "credential_same_kind", SAME_KIND_QUESTION.format(name=name, label=label), [
            _result("可以讀", [cmd("ruling", "add", "--kind", "可以讀", "--file", fid, "--label", Slot("那一種"), "--text")
                              + ' "{客戶的話}"'],
                    fill=[_fill("那一種", NAME_RULE), _fill("客戶的話", FREE_TEXT_RULE)]),
            _result("不要", [cmd("ruling", "decline", "--file", fid)]),
            _result("不是同一種（不問）", [cmd("ruling", "decline", "--file", fid, "--quiet")],
                    note="你判斷不是 labels 裡任何一種時跑這個，不問她，之後也不再列")])
        ask.update(file={"id": fid, "name": name, "folder": _folder(e)}, labels=kinds, note=SAME_KIND_NOTE)
        asks.append(ask)
    return asks


def _folder(entry):
    """「別名/相對夾」（manage 的寫法）。"""
    alias, rel = inventory.split_uri(entry["locations"][0]["uri"])
    folder = rel.rpartition("/")[0]
    return f"{alias}/{folder}" if folder else alias


def locked_same_kind(vault, out):
    """unlock_same_kind 的材料（D-181 第 2 點）：授權過的種類在、待處理有 open 的「有密碼」PDF（這一回合還沒開過）。
    回 (files, kinds)；unlock.json 或清冊讀不到回 ([], [])。"""
    from kb._lib import unlock
    try:
        kinds = unlock.load(vault)["kinds"]
    except ValueError:
        return [], []
    if not kinds:
        return [], []
    locked = [u for u in out.get("unreadable") or [] if str(u.get("reason") or "").startswith("有密碼")
              and str(u.get("uri") or "").lower().endswith(".pdf")]
    if not locked:
        return [], []
    try:
        by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    except (ValueError, OSError):
        return [], []
    files = []
    for u in locked:
        e = by_id.get(u["id"])
        if e is None or not e.get("locations"):
            continue
        loc = e["locations"][0]
        files.append({"id": e["id"], "name": rulings.file_name(e), "folder": _folder(e), "size": loc.get("size"),
                      "date": str(loc.get("mtime") or "")[:10]})
    shown = []
    for k in kinds:
        examples = [{"name": rulings.file_name(by_id[x]), "folder": _folder(by_id[x])} for x in k.get("examples") or []
                    if x in by_id and by_id[x].get("locations")]
        shown.append({"label": k.get("label"), "credential": str(k.get("credential") or "").partition("/")[2],
                      "examples": examples})
    return files, shown


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
    if it.get("repeat"):  # 重複的：一條代表下一次，cancel 與改日期都是整串（D-501）
        return _ask(f"overdue:{aid}", "overdue", f"{agenda.md_of(it['date'])} {_with_entities(it)}{agenda.repeat_suffix(it)}過了，完成了嗎？", [
            _result("完成了", [cmd("agenda", "done", aid)]),
            _result("這次不用，跳到下一次", [cmd("agenda", "done", aid)]),
            _result("以後都不要了", [cmd("agenda", "cancel", aid)], strict=True),
            _result("還沒，下次再問", note="這個對話不再問")])
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
        known = [titles[f] for f in evidence if titles.get(f)]
        basis = "、".join(known)  # 讀不到標題的不列編號，數份數接在最後（B-467）
        if len(known) < len(evidence):
            more = len(evidence) - len(known)
            basis = f"{basis}，另外 {more} 份檔案" if known else f"{more} 份檔案"
        question = item.get("text", "") + f"（依據：{basis}）"
        out.append(_ask(f"close:{item['id']}", "close", question, [
            _result("對，結案了", [cmd("entity", "progress", name, "結案", *flags)]),
            _result("還沒", [cmd("entity", "progress", name, "進行中", *flags, "--note", "客戶說還沒結案")]),
            _result("先暫停了", [cmd("entity", "progress", name, "擱置", *flags)])]))
    return out


# ---------- 草稿還在等（produce 第 5 點，D-163） ----------

DRAFT_QUESTION = "{title} 的草稿還在等你看"
_DATE_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")


def drafts(vault):
    """暫存區底下的（草稿）.md，回總資料夾相對路徑（照路徑排）；跳過 . 開頭的夾與檔、不跟連結。"""
    root = Path(vault) / inventory.HOLD
    out = []
    if not root.is_dir() or os.path.islink(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if name.endswith(cards.DRAFT_SUFFIX) and not name.startswith("."):
                out.append(Path(dirpath, name).relative_to(vault).as_posix())
    return sorted(out)


def draft_title(rel):
    """「AI 產出/報告/2026-10-21 內湖李 進度報告（草稿）.md」→「內湖李 進度報告」：去掉（草稿）.md 與開頭的日期。"""
    name = rel.rsplit("/", 1)[-1][:-len(cards.DRAFT_SUFFIX)]
    return _DATE_HEAD.sub("", name) or name


def _draft_asks(vault):
    """跨對話的草稿：定稿與再改照 SKILL 做（do 空），不要了交 kb output drop-draft（strict）。
    這一回合才寫的草稿不問（剛給客戶看的那份不用回答末尾再問一次）；讀不到回合就照列。"""
    try:  # 只讀 current.json，不開新回合（plan 不寫檔）
        now = turn.now(vault)
        touched = {e.get("path") for e in turn.entries(vault, now["turn"])} if now else set()
    except (OSError, ValueError):
        touched = set()
    out = []
    for rel in drafts(vault):
        if rel in touched:
            continue
        out.append(_ask(f"draft:{rel}", "draft", DRAFT_QUESTION.format(title=draft_title(rel)), [
            _result("定稿"), _result("再改"), _result("不要了", [cmd("output", "drop-draft", rel)], strict=True)]))
    return out


# ---------- 收件匣的檔要放哪（收件匣第 5 點，D-244、D-178） ----------

INBOX_QUESTION = "{title}（暫存區）要放到哪裡？"
INBOX_PLACES = 3
INBOX_LATER = "先放著"
NOT_DESTINATIONS = (inventory.HOLD.casefold(),)


def _inbox_places(vault, names, where_of):
    """候選的夾：每個實體的檔放在哪些夾（source_edit.where 的份數），收件匣與 AI 產出不算，加總照份數排、最多 INBOX_PLACES 個；
    都沒有（沒資料夾的客戶）給實體夾 <型別夾>/<名稱>（型別夾照設定檔，有編號）。知識庫裡的夾去掉「知識庫/」（kb 從知識庫根跑，照相對路徑認）。"""
    counts = {}
    for name in names:
        for row in where_of(name)["folders"]:
            folder = row["folder"]
            if folder.split("/", 1)[0].casefold() in NOT_DESTINATIONS:
                continue
            if folder.split("/", 1)[0] == inventory.KNOWLEDGE and "/" in folder:
                folder = folder.split("/", 1)[1]
            counts[folder] = counts.get(folder, 0) + row["count"]
    if counts:
        return [f for f, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:INBOX_PLACES]
    data = entities.load(vault)
    out = []
    for name in names:
        e, _hit = entities.find(data, name)
        folder = f"{profile.folder_of(vault, e['type'])}/{e['name']}" if e is not None and e.get("type") else None
        if folder and folder not in out:
            out.append(folder)
    return out[:INBOX_PLACES]


def _inbox_top(entry):
    """條目在收件匣最上層就回檔名，否則 None（來源子夾裡的定期交來的不問，D-241）。"""
    alias, rel = inventory.split_uri(((entry.get("locations") or [{}])[0]).get("uri", ""))
    return rel if alias.casefold() == inventory.HOLD.casefold() and rel and "/" not in rel else None


def _inbox_asks(vault):
    """收件匣最上層、有卡、卡有掛實體的檔各一題：候選的夾一個一個結果（do 是 kb source-edit move），最後「先放著」。
    來源子夾裡的（定期交來的，D-241）不問。跟這一句有沒有關照 touched（kb intake 在對話裡記了收的那份）。讀不到就不問。"""
    from kb._lib import source_edit
    try:
        rows = [e for e in inventory.load(vault)["entries"] if e.get("status") == "active" and e.get("origin") == "intake"
                and _inbox_top(e)]
        groups = cards._scan_groups(vault)[0] if rows else {}
    except (ValueError, OSError):
        return []
    cache = {}

    def where_of(name):
        if name not in cache:
            try:
                cache[name] = source_edit.where(vault, name)
            except (source_edit.Refused, ValueError):
                cache[name] = {"folders": []}
        return cache[name]
    out = []
    for e in rows:
        rel = _inbox_top(e)
        if e["id"] not in groups:
            continue
        front = groups[e["id"]][0]["front"]
        names = [n for n in front.get("實體") or [] if isinstance(n, str) and n]
        title = front.get("標題") if isinstance(front.get("標題"), str) else rel
        if not names:
            continue
        try:
            places = _inbox_places(vault, names, where_of)
        except ValueError:
            continue
        src = f"{inventory.HOLD}/{rel}"
        out.append(_ask(f"inbox:{e['id']}", "inbox_place", INBOX_QUESTION.format(title=title),
                        [_result(f"放到 {f}", [cmd("source-edit", "move", src, "--to", f)]) for f in places]
                        + [_result(INBOX_LATER)]))
    return out


def _money_tell(vault):
    """這一回合從檔案記的帳（記帳 2，照 agenda.tell_lines）；讀不到就沒有。"""
    try:
        from kb._lib import money
        return money.tell_lines(vault)
    except Exception:  # noqa: BLE001
        return []


def _number(asks):
    n = 0
    for ask in asks:
        for r in ask["results"]:
            n += 1
            r["n"] = n
    return asks


def renumber(asks):
    """挑過之後選項從 1 重新連號（kb asks 的範圍與 --all）。"""
    return _number(asks)


# ---------- 回答末尾的問法（lint 題，D-225、D-226） ----------

SET_ASIDE_POSIX = ".system/set_aside.json"
PENDING_KEYED = ("missing", "close")  # 這兩種題目住在 pending.json，key 就是 p- 編號
PENDING_ONLY = ("handedit", "tag_edit", "lint", "unreadable", "script_touched", "conflict", "money_dupe", "money_unsure", "report")  # 沒有整理題的待處理，照這個順序
# 急的排前面：有時間性的 → 整理題照 _asks 的順序 → 沒有整理題的待處理
_ORDER = ("batch", "drift", "missing", "credential_same_kind", "ruling_recheck", "overdue", "agenda_conflict", "close", "draft",
          "inbox_place", "name") + tuple(f"pending_{k}" for k in PENDING_ONLY) + ("carried",)
PENDING_HOW = "{客戶的話}"
MONEY_FILL = "帳的欄"
MONEY_FILL_RULE = ("照那張卡與她的話寫 --amount、--date（收付那天）或 --due（到期日）、--entity、--item、--text，沒有的不寫；"
                   "字有 $ 這類符號用單引號包")


def _digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def set_aside_keys(vault):
    """.system/set_aside.json 記的鍵 {key: {at, by, question}}（不住在 pending.json 的問題先放著）；沒檔、壞檔回空。"""
    try:
        from kb._lib.io import read_json
        data = read_json(Path(vault) / SET_ASIDE_POSIX)
    except (ValueError, OSError):
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    return {k: v for k, v in keys.items() if isinstance(v, dict)} if isinstance(keys, dict) else {}


class _About:
    """算 about 要的東西，要用才讀：實體名稱 → 編號、卡 → 掛的實體、日程與裁示條目、待處理條目。讀不到的那一類給空的。"""

    def __init__(self, vault):
        self.vault = vault
        self._ent = self._cards = self._agenda = self._rulings = self._pending = None

    def entities(self):
        if self._ent is None:
            try:
                self._ent = entities.load(self.vault)
            except ValueError:
                self._ent = {"entities": []}
        return self._ent

    def ids_of(self, names):
        out = []
        for name in names or []:
            e, _hit = entities.find(self.entities(), name)
            if e is not None and e["id"] not in out:
                out.append(e["id"])
        return out

    def card_entities(self, fid):
        if self._cards is None:
            self._cards = {}
            try:
                for name, ms in entities.mounts(self.vault, self.entities()).items():
                    eid = self.ids_of([name])
                    for m in ms:
                        self._cards.setdefault(m["id"], []).extend(eid)
            except Exception:  # noqa: BLE001  卡讀不到：只用題目自己帶的對象
                self._cards = {}
        return self._cards.get(fid, [])

    def agenda_item(self, aid):
        if self._agenda is None:
            try:
                self._agenda = {i.get("id"): i for i in agenda.load(self.vault)["items"]}
            except ValueError:
                self._agenda = {}
        return self._agenda.get(aid)

    def ruling(self, rid):
        if self._rulings is None:
            try:
                self._rulings = {r.get("id"): r for r in rulings.load(self.vault)["rulings"]}
            except ValueError:
                self._rulings = {}
        return self._rulings.get(rid)

    def pending_item(self, pid):
        if self._pending is None:
            try:
                self._pending = {i.get("id"): i for i in pending.load(self.vault)["items"]}
            except ValueError:
                self._pending = {}
        return self._pending.get(pid)


def _about_of(ask, ctx):
    """這題的對象 {"entities","files"}：照題號認是哪一種對象，檔案卡掛的實體一起算。"""
    kind, _sep, target = str(ask["id"]).partition(":")
    ents, files = [], []
    if kind in ("drift", "name"):
        ents.append(target)
    elif kind in ("credential", "inbox"):
        files.append(target)
    elif kind in ("missing", "close", "pending"):
        item = ctx.pending_item(target) or {}
        ents += list(item.get("entity_ids") or [])
        files += [f for f in item.get("file_ids") or [] if str(f).startswith("f-")]
    elif kind in ("overdue", "conflict"):
        item = ctx.agenda_item(target) or {}
        ents += ctx.ids_of(item.get("entities"))
        card = (item.get("origin") or {}).get("card") or ((item.get("proposal") or {}).get("card"))
        if card:
            files.append(card)
    elif kind == "ruling":
        r = ctx.ruling(target) or {}
        ents += ctx.ids_of(r.get("entities"))
        files += list(rulings.card_ids(r)) if r else []
    for f in files:
        ents += ctx.card_entities(f)
    return {"entities": list(dict.fromkeys(ents)), "files": list(dict.fromkeys(files))}


def _key_of(vault, ask):
    """穩定的鍵：住在 pending.json 的是 p- 編號；其他是題號加問句的指紋（草稿另加內容指紋，改了內容鍵就變）。"""
    kind, _sep, target = str(ask["id"]).partition(":")
    if kind in ("missing", "close", "pending", "carried"):
        return target
    extra = ""
    if kind == "draft":
        try:
            extra = _digest((Path(vault) / target).read_bytes())
        except OSError:
            extra = ""
    return f"{ask['id']}@{_digest(ask['question'] + extra)}"


def _urgent(ask, ctx, limit, today=None):
    """有時間性（D-225、D-129）：逾期一律算；日程有出入的條目或新日期、待處理的 data.dates 落在今天到 set_aside_wake_days 天後之間
    （過了的日期不算：逾期的日程另有逾期題，測試 T-3）。"""
    today = today or agenda._today()
    kind, _sep, target = str(ask["id"]).partition(":")
    if ask["kind"] == "overdue":
        return True
    if kind == "conflict":
        item = ctx.agenda_item(target) or {}
        days = [item.get("date"), (item.get("proposal") or {}).get("date")]
        return any(isinstance(d, str) and today <= d <= limit for d in days)
    if kind == "pending":
        item = ctx.pending_item(target) or {}
        return any(today <= str(d) <= limit for d in (item.get("data") or {}).get("dates") or [])
    return False


def _limit(vault, config_mod=None):
    from kb._lib import config as config_lib
    days = (config_mod or config_lib).get(vault, "set_aside_wake_days")
    return (date.fromisoformat(agenda._today()) + timedelta(days=days)).isoformat()


def annotate(vault, asks, config_mod=None):
    """每題補 key、about、urgent（原地改，也回同一份）。"""
    ctx = _About(vault)
    limit = _limit(vault, config_mod)
    for ask in asks:
        ask["key"] = _key_of(vault, ask)
        ask["about"] = _about_of(ask, ctx)
        ask["urgent"] = _urgent(ask, ctx, limit)
    return asks


def visible(vault, asks):
    """去掉先放著的（有時間性的照留）：pending 的 set_aside 還沒該回來、set_aside.json 記的鍵。回 (留下的, 藏了幾題)。
    跟封存的檔或實體有關的題先拿掉，不算藏了的（放寬丙 12，D-332）。"""
    from kb._lib import archive
    af, ae = archive.sets(vault)
    if af or ae:
        asks = [a for a in asks if not archive.hides((a.get("about") or {}).get("files"), (a.get("about") or {}).get("entities"), af, ae)]
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []
    back = pending.due_back(vault, items) if any(i.get("set_aside") for i in items) else set()
    aside = {i["id"] for i in items if i.get("status") == "open" and i.get("set_aside") and i["id"] not in back}
    keys = set(set_aside_keys(vault))
    shown = [a for a in asks if a.get("urgent") or (a.get("key") not in aside and a.get("key") not in keys)]
    return shown, len(asks) - len(shown)


def _pending_results(item):
    pid, kind = item["id"], item.get("kind")
    resolve = _result("處理好了", [cmd("pending", "resolve", pid, "--how") + f' "{PENDING_HOW}"'],
                      fill=[_fill("客戶的話", FREE_TEXT_RULE)])
    drop = _result("不用管", [cmd("pending", "dismiss", pid)])
    if kind == "report":
        month = (item.get("data") or {}).get("month") or ""
        return [_result("看報告", [cmd("pending", "resolve", pid, "--how", "看過了")],
                        note=f"讀 系統/健檢報告/{month}.md 用白話講重點（照 lint skill），講完跑 do")]
    if kind == "handedit":
        return [_result("照你改的做", note="kb lint handedit --json 看她改了什麼，照意思用 kb 指令做、看不懂的才問，做完 kb lint handedit --done "
                                       + pid), drop]
    if kind in ("lint", "tag_edit"):
        got = [_result(r.get("label", ""), r.get("do") or [], note=r.get("note")) for r in (item.get("data") or {}).get("results") or []
               if isinstance(r, dict) and r.get("label")]
        return (got or [resolve]) + [drop]
    if kind == "conflict":
        return [_result("說哪個對", note="照 note skill 記下她說的（口述或裁示），記完 kb pending resolve " + pid), drop]
    data = item.get("data") or {}
    if kind == "money_dupe":  # 記帳 2：D-276 看不出是不是同一筆
        return [_result("是同一筆", [cmd("money", "merge", data.get("keep", ""), data.get("other", ""))]),  # merge 自己關這件
                _result("不是", [cmd("pending", "resolve", pid, "--how", "不是同一筆，兩筆都留")])]
    if kind == "money_unsure":  # 記帳 2：D-274 看不出流向；do 帶 --resolves，記完程式就關這件（確認報告 #6）
        card = data.get("card", "")
        fill = [_fill(MONEY_FILL, MONEY_FILL_RULE)]
        adds = [(label, cmd("money", "add", "--card", card, "--dir", d, "--resolves", pid)) for label, d in (("她收的", "收"), ("她付的", "付"))]
        return [_result(label, [c and c + " {" + MONEY_FILL + "}"], fill=fill, argv=None)  # 首頁不直接跑（帳的欄要 AI 填）
                for label, c in adds] + [
                _result("不是她的錢", [cmd("pending", "resolve", pid, "--how", "不是她的錢，不記")])]
    return [resolve, drop]


def pending_asks(vault, items=None, config_mod=None):
    """pending.json 裡沒有整理題的那幾種（PENDING_ONLY）做成題：題號 pending:<編號>、kind pending_<種類>、問句是 待處理.md 那一行。
    補好 key、about、urgent，先放著的不列。回 (題, 藏了幾題)。"""
    if items is None:
        try:
            items = pending.load(vault)["items"]
        except ValueError:
            return [], 0
    rank = {k: i for i, k in enumerate(PENDING_ONLY)}
    rows = sorted((i for i in items if i.get("status") == "open" and i.get("kind") in PENDING_ONLY),
                  key=lambda i: (rank[i["kind"]], str(i.get("created") or "")))
    asks = [_ask(f"pending:{i['id']}", f"pending_{i['kind']}", pending.line_of(i), _pending_results(i)) for i in rows]
    return visible(vault, annotate(vault, asks, config_mod))


def urgent_pending_ask(vault, pid):
    """這件待處理（p- 編號）有時間性嗎：有就回它做成的題（帶結果），沒有回 None。只讀 pending.json，不跑整理（第二輪 T-27）。"""
    asks, _hidden = pending_asks(vault)
    return next((a for a in asks if a.get("key") == pid and a.get("urgent")), None)


def order(asks):
    """照急的排：有時間性的在前，其他照 _ORDER（整理題的原順序、沒有整理題的待處理在後），同種保持原順序。"""
    rank = {k: i for i, k in enumerate(_ORDER)}
    return sorted(asks, key=lambda a: (not a.get("urgent"), rank.get(a.get("kind"), len(_ORDER))))


def pending_view_questions(vault, out=None):
    """待處理.md「整理時要問你的」那段（D-226）：整理題裡不住在 pending.json 的問句（先放著的、批次題不列）。健檢算好交給 pending。
    out 是 cards.collect 的結果（沒給自己算）。"""
    from kb._lib import config as config_lib
    from kb._lib import sources as sources_lib
    out = out if out is not None else cards.collect(vault, sources_lib.load(vault))
    got = build(out, vault, config_lib, tell_all=False)
    return [a["question"] for a in got["asks"] if a["kind"] not in PENDING_KEYED + ("batch",)]


# ---------- tell ----------

def _tell(out, vault):
    lines = []
    unreadable = out.get("unreadable") or []
    if unreadable:
        titles = _link_titles(vault, unreadable)
        for item in unreadable[:UNREADABLE_LISTED]:
            name = titles.get(item["id"]) or item["uri"].rsplit("/", 1)[-1]
            lines.append(_unreadable_line(name, item.get("reason")))
        if len(unreadable) > UNREADABLE_LISTED:
            lines.append(f"另有 {len(unreadable) - UNREADABLE_LISTED} 份讀不到")
        lines[-1] += f"；{PENDING_FILE}"
    skipped = out.get("skipped") or {}
    counted = [f"{text} {skipped[k]}" for k, text in SKIPPED_TEXT if skipped.get(k)]
    if counted:
        lines.append("不製卡：" + "、".join(counted) + (CREDENTIAL_TAIL if skipped.get("credential") else ""))
    if skipped.get("placeholder"):
        lines.append(CLOUD_SENTENCE)
    lines.extend(_by_setting(skipped.get("skipped") or 0, vault))
    for type_, n in (out.get("missing_types") or {}).items():
        lines.append(f"有 {n} 份看起來跟 {type_} 有關，但設定檔裡沒有『{type_}』這一類，"
                     "要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用")
    if out.get("pdf_restored"):
        lines.append(f"PDF 元件裝好了，{out['pdf_restored']} 份 PDF 回到待製卡")
    if out.get("unbacked"):
        lines.append(UNBACKED_TELL.format(n=out["unbacked"]))
    lines.extend(agenda.tell_lines(vault))
    lines.extend(_money_tell(vault))
    lines.extend(_opened_tell(vault))
    lines.extend(_judged_tell(vault))
    lines.extend(_tagged_tell(vault))
    return lines


def _tagged_tell(vault):
    """這一回合（加這個對話這一句派出去、這一句收尾的整理回合）標籤正本跟那一回合的備份比多了的詞（D-505）：一個實體一句，
    超過 TAGGED_SHOWN 個併成一句；拿掉的不講（D-238）。只讀；讀不到不講。"""
    from kb._lib import synthesis
    try:
        got = synthesis.tag_changes(vault, _tag_turns(vault))
    except (ValueError, OSError):
        return []
    lines = [TAGGED_TELL.format(name=name, what="；".join(f"{f} {'、'.join(ws)}" for f, ws in added))
             for name, added in got[:TAGGED_SHOWN]]
    if len(got) > TAGGED_SHOWN:
        lines.append(TAGGED_MORE.format(n=len(got) - TAGGED_SHOWN))
    return lines


def _tag_turns(vault):
    """目前回合，加這個對話的整理 job 回合裡、在這一句開始之後才收尾的（card-maker 寫卡記在 job 回合）。"""
    now = turn.current(vault)
    tids = [now["turn"]]
    sid, started = now.get("session_id"), now.get("started") or ""
    if not sid:
        return tids
    try:
        jobs = batches.load(vault)["jobs"]
    except ValueError:
        return tids
    for j in jobs:
        if (j.get("session_id") == sid and j.get("kind") != batches.ADOPT and j.get("turn") not in tids
                and j.get("status") == "closed" and str(j.get("closed") or "") >= started):
            tids.append(j["turn"])
    return sorted(tids)


def _judged_tell(vault):
    """這一回合 AI 判斷登記的名字（D-202）：一句講幾個、列前幾個、錯了怎麼說。"""
    try:
        names = entities.judged_in(vault, turn.current(vault)["turn"])
    except (ValueError, OSError):
        return []
    if not names:
        return []
    shown = "、".join(names[:JUDGED_SHOWN]) + ("…" if len(names) > JUDGED_SHOWN else "")
    return [JUDGED_TELL.format(n=len(names), names=shown)]


def _link_titles(vault, unreadable):
    """線上文件（link 條目）讀不到時講它的標題，不講網址尾巴（B-415）；清冊讀不到就照舊。"""
    if all(inventory.is_file_uri(i.get("uri") or "") for i in unreadable):
        return {}
    try:
        return {e.get("id"): e["title"] for e in inventory.load(vault)["entries"]
                if inventory.is_link(e) and e.get("title")}
    except (ValueError, OSError):
        return {}


def _opened_tell(vault):
    """這一回合用存好的密碼開了同一種的（unlock.json 的 opened）：每份一句（D-181 第 2 點「直接開、末尾講」）。"""
    from kb._lib import unlock
    try:
        now = turn.now(vault)
        opened = unlock.opened_in_turn(vault, now["turn"]) if now else []
        names = rulings.file_names(vault) if opened else {}
    except (OSError, ValueError, KeyError):
        return []
    return [SAME_KIND_OPENED.format(name=names.get(o.get("id")) or o.get("id"), label=o.get("label"))
            for o in opened if o.get("label")]


UNBACKED_TELL = "暫存區有 {n} 份沒經程式放進來，這幾份退回拿不掉"  # produce 第 5 點：只數這次待製卡裡的


def _unreadable_line(name, reason):
    if not reason:
        return f"{name} 讀不到"
    if reason.startswith("有密碼") and not name.lower().endswith(".pdf"):
        return f"{name} 讀不到：{reason}；{OFFICE_LOCKED_ADVICE}"  # 加密的 Word、Excel 這版打不開（D-182）
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
