"""kb home --json 的本體（Obsidian 首頁，#22，D-252）：首頁一次要的東西一支給齊，只讀、不開回合。

格式 willy-home/1（spec「資料與介面格式」第 1 節）：問候句（只寫問候，裁定 32）、件數、時間軸（每個事類實體一列，marks 是現成的圖形）、待辦三組、
要你確認的（跟 kb asks 同一支）、其他要處理的、最近。外掛只畫，不自己推日期、不判逾期（D-252）：逾期、今天、這週照 agenda 既有的
算法（counts 的條件），marks 的 late 照 D-129。日程第二題的欄位（baseline、critical、slack、derived、children、links）這題一律給預設值。
任何一區讀不到只記進 warnings、那一區給空的，其他區照畫。
"""
import contextlib
import copy
import datetime as dt
import os
from pathlib import Path

from kb._lib import agenda, cards, config, entities, inventory, ledger, pending, plan, profile, turn
from kb._lib import sources as sources_lib
from kb._lib.paths import read_manifest

SCHEMA = "willy-home/1"
OTHER_KEY = "_other"
OTHER_LABEL = "其他"
# 一句話只寫問候（裁定 32）：早上、下午、晚上各一組，每句 8 字內，照日期挑；不講要做什麼、不看逾期；不叫 Claude
CHEER_MORNING = ("早安", "早安，今天也加油", "早安，慢慢來", "早，先喝口水")
CHEER_AFTERNOON = ("午安", "午安，繼續加油", "午安，慢慢來", "午安，喝口水")
CHEER_EVENING = ("辛苦了", "今天辛苦了", "晚安", "辛苦了，慢慢來")
STATUS_PROGRESS = {"ongoing": "active", "paused": "paused", "closed": "closed"}
HARD_KINDS = ("deadline", "meeting")


def _day(text):
    try:
        return dt.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def cheer(day, hour):
    """問候一句：hour 0~23，5 點到 11 點早上、12 點到 17 點下午、其餘晚上；同一天同一時段同一句。"""
    group = CHEER_MORNING if 5 <= hour < 12 else CHEER_AFTERNOON if 12 <= hour < 18 else CHEER_EVENING
    return group[dt.date.fromisoformat(day).toordinal() % len(group)]


def _mark(it, today, card=None):
    """日程條目 → 一個 mark（也是待辦那一件的格式）。bar：期間或有 start；end 是日期（期間結案用 ended）。
    card 是點這一件該開的卡（_card_of 的結果，沒有就 None）。"""
    kind = it.get("kind")
    status = it.get("status")
    start = it.get("start") if isinstance(it.get("start"), str) else None
    end = it.get("date")
    if kind == "period" and status == "done" and it.get("ended"):
        end = it["ended"]
    late = status == "open" and kind in agenda.TRACKED and isinstance(it.get("date"), str) and it["date"] < today
    rep = agenda.REPEAT_TEXT.get((it.get("repeat") or {}).get("every")) if isinstance(it.get("repeat"), dict) else None
    return {
        "id": it.get("id"), "shape": "bar" if kind == "period" or start else "point", "kind": kind,
        "start": start, "end": end, "date": it.get("date"), "time": it.get("time"), "text": it.get("text"),
        "status": status, "late": late, "inferred": it.get("source") == "inferred",
        "repeat": rep, "hard": kind in HARD_KINDS,
        "entities": list(it.get("entities") or []),
        "baseline": None, "critical": False, "slack": None, "derived": False, "children": [],
        "card": card,
    }


def _card_resolver(vault, ent_data):
    """回 it → 點下去該開的卡：條目 origin 記的那張檔案卡 → 沒有就掛的第一個實體的綜合卡 → 都沒有 None（外掛才開日程.md 那一行）。
    結果 {"path": 相對路徑, "kind": "origin"|"synthesis"}；卡不在了（清冊沒有、檔不見）不算有。"""
    groups = cards._scan_groups(vault)[0]
    syn = {}

    def synthesis(name):
        e = entities.find(ent_data, name)[0]
        if e is None or e.get("status") != "active" or not e.get("type") or entities.is_self(e):
            return None
        if e["id"] not in syn:
            syn[e["id"]] = entities.locate(vault, e, ent_data)
        return syn[e["id"]]

    def resolve(it):
        cid = (it.get("origin") or {}).get("card") if isinstance(it.get("origin"), dict) else None
        if cid in groups:
            return {"path": groups[cid][0]["path"], "kind": "origin"}
        for name in it.get("entities") or []:
            path = synthesis(name)
            if path:
                return {"path": path, "kind": "synthesis"}
            break  # 只看第一個實體
        return None
    return resolve


def _todo(vault, items, today, card_of=None):
    """待辦三組：已經過了（TRACKED 逾期）、今天、這週（今天之後 lead_days 天內）；條件跟 agenda.counts 一樣。"""
    now = dt.date.fromisoformat(today)
    groups = {"overdue": [], "today": [], "week": []}
    for it in sorted(items, key=agenda._order):
        d = agenda._day(it)
        if it.get("status") != "open" or it.get("kind") == "period" or d is None:
            continue
        if d < now:
            if it.get("kind") in agenda.TRACKED:
                groups["overdue"].append(_mark(it, today, card_of(it) if card_of else None))
        elif d == now:
            groups["today"].append(_mark(it, today, card_of(it) if card_of else None))
        elif d <= now + dt.timedelta(days=agenda._window(vault, it)):
            groups["week"].append(_mark(it, today, card_of(it) if card_of else None))
    return groups


def _matter_rows(vault, ent_data):
    """{實體名稱 key: 條目}：status active 的事類實體（包含別名，日程條目掛的名稱照登記名稱，別名也認）。"""
    out = {}
    for e in ent_data["entities"]:
        if e.get("status") == "active" and e.get("type") and entities.is_matter(vault, e):
            for word in entities.words(e):
                out.setdefault(entities.key(word), e)
    return out


def _in_window(it, lo, hi):
    """open 的都算（逾期的也要看到）；done 的只留 lo 之後完成的（方便看見剛打勾的）；取消的不畫。"""
    status = it.get("status")
    d = it.get("date")
    if not isinstance(d, str) or agenda._day(it) is None or status == "cancelled":
        return False
    start = it.get("start") if isinstance(it.get("start"), str) else d
    if status == "done":
        ref = it.get("ended") if it.get("kind") == "period" and it.get("ended") else d
        return ref >= lo and start <= hi
    return start <= hi


def _timeline(vault, items, ent_data, today, card_of=None, lo=None, hi=None):
    """lo、hi 是外掛捲到別段時指定的起訖（裁定 34）；沒給就是今天前 7 天到後 home_timeline_days 天。"""
    now = dt.date.fromisoformat(today)
    lo = lo or (now - dt.timedelta(days=7)).isoformat()
    hi = hi or (now + dt.timedelta(days=config.get(vault, "home_timeline_days"))).isoformat()
    matters = _matter_rows(vault, ent_data)
    buckets = {}
    for it in items:
        if not _in_window(it, lo, hi):
            continue
        owner = next((matters[entities.key(n)] for n in it.get("entities") or [] if entities.key(n) in matters), None)
        buckets.setdefault(owner["id"] if owner else OTHER_KEY, (owner, []))[1].append(it)
    histories = agenda._histories(vault)
    rows = []
    for key, (owner, its) in buckets.items():
        marks = [_mark(i, today, card_of(i) if card_of else None) for i in sorted(its, key=agenda._order)]
        pauses = []
        if owner is not None:
            pauses = [{"start": a, "end": b} for a, b in agenda._pauses(histories, owner["name"], today) if b >= lo and a <= hi]
        first = min((m["end"] for m in marks if m["status"] == "open"), default="9999-99-99")
        rows.append((key == OTHER_KEY, first, owner["name"] if owner else "", {
            "key": key, "label": owner["name"] if owner else OTHER_LABEL, "type": owner.get("type") if owner else None,
            "progress": STATUS_PROGRESS.get(owner.get("progress"), "active") if owner else "active",
            "marks": marks, "pauses": pauses, "links": []}))
    rows.sort(key=lambda r: r[:3])  # 其他放最後，其餘照最近一個還沒做的日期、再照名稱
    return {"from": lo, "to": hi, "rows": [r[3] for r in rows]}


def _argv(do):
    """結果的 do（一條 kb 指令字串）→ 給外掛的指令陣列（不含 kb）；不是剛好一條、拆不開都是 None（只有 AI 才做得了）。"""
    import shlex
    if not isinstance(do, list) or len(do) != 1 or not isinstance(do[0], str):
        return None
    try:
        parts = shlex.split(do[0])
    except ValueError:
        return None
    return parts[1:] if parts[:1] == ["kb"] and len(parts) > 1 else None


SET_ASIDE_LABELS = ("先放著", "還沒，下次再問", "之後再說")  # 這幾個答案的意思都是「這題先不管」（裁定 34），首頁一律換成統一的「先放著」（裁定 39）
ASIDE_LABEL = "先放著"
KNOWN_LABEL = "知道了"
INFO_KINDS = ("pending_unreadable", "pending_script_touched")  # 只是告訴她的：一張卡只有「知道了」（pending dismiss）
RENAME_KINDS = ("drift_folder_renamed", "drift_both_renamed", "drift_card_renamed", "drift_moved_type")
NO_LIST_KINDS = ("overdue",)  # 在待辦處理，這區不列也不算進「跟 Claude 一起看」
NEW_DATE_LABEL = "改到某天…"
MERGE_LABEL = "跟…同一個"
RENAME_LABEL = "改名…"
PLACE_LABEL = "放到…"
KEEP_CARD_LABEL = "卡留著"
KEEP_CARD_WORDS = "客戶說卡留著"
GROUP_OFTEN = "常放"
GROUP_ALL = "全部"


def _result(label, argv, fill=None, strict=False, note=None, after=None):
    out = {"label": label, "argv": argv, "fill": fill, "strict": strict, "note": note}
    if after:
        out["after"] = after  # 這個結果做完要接著跑的指令
    return out


def _convert(r):
    """kb asks 的一個結果 → 首頁的結果。fill 多一個 input：date｜text（select 的由各題自己換成帶 options 的）。"""
    fills = r.get("fill") or []
    fill = None
    if fills and isinstance(fills[0], dict):
        fill = {"name": fills[0].get("name"), "rule": fills[0].get("rule"),
                "input": "date" if fills[0].get("rule") == plan.DATE_RULE else "text"}
    argv = r["argv"] if "argv" in r else _argv(r.get("do"))
    return _result(r.get("label"), argv, fill, bool(r.get("strict")), r.get("note"), r.get("after"))


def _fill(name, rule, kind, options=None):
    out = {"name": name, "rule": rule, "input": kind}
    if options is not None:
        out["options"] = options
    return out


def _id_of(a):
    return str(a.get("id") or "").partition(":")[2]


def _doable(results):
    """答案裡指令自己就做得完的：argv 有值（要填的也算，填了就跑）。"""
    return [r for r in results if r["argv"] is not None]


def _conflict_answers(a, results):
    got = _doable(results)
    aid = _id_of(a)
    if len(got) < 2 or not aid:
        return None
    return got + [_result(NEW_DATE_LABEL, ["agenda", "edit", aid, "--date", "{新日期}"], _fill("新日期", plan.DATE_RULE, "date"))]


def _name_answers(vault, a, results):
    """名字對；改名…（rename 再 confirm）；跟…同一個（同型別、active 的其他實體選單）。"""
    try:
        data = entities.load(vault)
    except ValueError:
        return None
    e = next((x for x in data["entities"] if x.get("id") == _id_of(a)), None)
    if e is None or not results or results[0]["argv"] is None:
        return None
    name = e["name"]
    same = [x["name"] for x in data["entities"] if x.get("status") == "active" and x.get("type") == e.get("type")
            and x.get("id") != e["id"] and not entities.is_self(x)]
    out = [results[0],
           _result(RENAME_LABEL, ["entity", "rename", name, "--to", "{新名字}"], _fill("新名字", plan.NAME_RULE, "text"),
                   after=["entity", "confirm", "{新名字}"])]
    if same:
        out.append(_result(MERGE_LABEL, ["entity", "merge", name, "--into", "{同一個}"],
                           _fill("同一個", plan.NAME_RULE, "select", [{"label": n, "value": n} for n in same])))
    return out


def _rename_answers(results):
    """實體夾改名、搬走：照現在的；沒有指令的（先把 X 加進設定檔）不列，剩的不足兩個整題移下面。"""
    got = _doable(results)
    return got if len(got) >= 2 else None


def _inbox_answers(vault, results):
    """一個選單「放到…」：候選的夾在前（照現在的排法），分隔線下是全部實體夾。"""
    moves = [r["argv"] for r in results if r["argv"] and r["argv"][:2] == ["source-edit", "move"] and len(r["argv"]) >= 5]
    src = moves[0][2] if moves else None
    if src is None:
        return None
    options = [{"label": m[4], "value": m[4], "group": GROUP_OFTEN} for m in moves]
    seen = {o["value"] for o in options}
    try:
        for e in entities.load(vault)["entities"]:
            if e.get("status") != "active" or not e.get("type") or entities.is_self(e):
                continue
            folder = f"{profile.folder_of(vault, e['type'])}/{e['name']}"
            if folder not in seen:
                seen.add(folder)
                options.append({"label": folder, "value": folder, "group": GROUP_ALL})
    except ValueError:
        pass
    return [_result(PLACE_LABEL, ["source-edit", "move", src, "--to", "{放到}"], _fill("放到", "夾的相對路徑", "select", options))]


def _missing_answers(a, results):
    pid = _id_of(a)
    drop = next((r for r in results if r["argv"] and r["argv"][:2] == ["cards", "drop"]), None)
    if drop is None or not pid:
        return None
    return [drop, _result(KEEP_CARD_LABEL, ["pending", "resolve", pid, "--how", KEEP_CARD_WORDS])]


def _info_answers(a):
    pid = _id_of(a)
    return [_result(KNOWN_LABEL, ["pending", "dismiss", pid])] if pid else None


def _answers(vault, a, results):
    """這題在首頁的答案（卡上做完）；None = 要 Claude 接手，整題移到最下面那行。表外的種類一律 None。"""
    kind = a.get("kind")
    if kind == "agenda_conflict":
        return _conflict_answers(a, results)
    if kind in ("close", "ruling_recheck"):
        got = _doable(results)
        return got if len(got) == len(results) else None
    if kind == "name":
        return _name_answers(vault, a, results)
    if kind in RENAME_KINDS:
        return _rename_answers(results)
    if kind == "inbox_place":
        return _inbox_answers(vault, results)
    if kind == "missing":
        return _missing_answers(a, results)
    if kind in INFO_KINDS:
        return _info_answers(a)
    return None  # drift_missing（重寫綜合卡要 Claude）、批次、草稿、密碼檔、手改、標籤、健檢、報告、兩份寫法不同、carried…


def _inferred_agenda_ids(vault):
    """健檢「推測的日程放太久」那件的編號：改在日程確認，這區不列也不算。"""
    try:
        return {i["id"] for i in pending.load(vault)["items"]
                if i.get("status") == "open" and i.get("kind") == "lint" and i.get("key") == "inferred:agenda"}
    except (ValueError, OSError):
        return set()


def _confirm(vault, asks):
    """(要你確認的, 要跟 Claude 一起看的)：前者每題的答案都是首頁照 argv 跑就做完的；後者是 [{key, kind, text}]（裁定 39，D-268）。
    先放著（asks set-aside）是首頁加的、非急的才有，告知的（知道了）沒有。"""
    out, claude = [], []
    inferred = _inferred_agenda_ids(vault)
    for a in asks:
        if a.get("kind") in NO_LIST_KINDS or (a.get("kind") == "pending_lint" and _id_of(a) in inferred):
            continue
        results = _answers(vault, a, [_convert(r) for r in a.get("results") or []])
        key = a.get("key")
        if results is None:
            claude.append({"key": key, "kind": a.get("kind"), "text": a.get("question")})
            continue
        results = [r for r in results if r["label"] not in SET_ASIDE_LABELS]
        if a.get("kind") not in INFO_KINDS and not a.get("urgent") and key:
            results.append(_result(ASIDE_LABEL, ["asks", "set-aside", key]))
        out.append({"key": key, "kind": a.get("kind"), "text": a.get("question"), "urgent": bool(a.get("urgent")),
                    "results": results})
    return out, claude


def _others(vault, asked_keys):
    """pending open 而不在 asks 裡的（先放著的也不列：那是她決定先不看）。"""
    out = []
    for it in pending.load(vault)["items"]:
        if it.get("status") != "open" or it.get("set_aside") or it.get("id") in asked_keys:
            continue
        out.append({"id": it["id"], "kind": it.get("kind"), "text": pending.line_of(it), "dismiss": True})
    return out


def _open_target(vault, sources, entry):
    """原檔怎麼開：link 條目是網址；其他是清冊裡第一個磁碟上真的在的位置（絕對路徑，正斜線）。找不到回 None。"""
    if entry.get("kind") == "link":
        loc = (entry.get("locations") or [{}])[0].get("uri")
        return {"url": loc} if loc else None
    for loc in entry.get("locations") or []:
        path = inventory.path_of(vault, sources, loc.get("uri", ""))
        if path is not None and os.path.exists(path):
            return {"path": str(Path(path)).replace("\\", "/")}
    return None


def _recent(vault, opened, sources):
    """最近：她從首頁開過的（--opened 的順序）與最近放進來的（first_seen 最新），同一張卡留一筆，照 at 新到舊，最多 home_recent_max。"""
    cap = config.get(vault, "home_recent_max")
    entries = {e.get("id"): e for e in inventory.load(vault)["entries"] if e.get("status") == "active"}
    groups = cards._scan_groups(vault)[0]
    want = [(cid, "opened") for cid in opened or [] if cid in entries and cid in groups]  # 她從首頁開過的，照外掛給的順序（新到舊）
    added = sorted((e for e in entries.values() if e.get("id") in groups and e.get("first_seen")),
                   key=lambda e: str(e["first_seen"]), reverse=True)[:cap]
    want += [(e["id"], "added") for e in added]
    seen, rows = set(), []
    for cid, why in want:  # 開過的在前：--opened 只帶編號沒有時間，她主動點開的算最新
        if cid in seen:
            continue
        seen.add(cid)
        entry, card = entries[cid], groups[cid][0]
        front = card["front"]
        target = _open_target(vault, sources, entry)
        rows.append({"card": cid, "title": cards._title(card), "type": cards._text_or(front, "卡型"),
                     "date": cards._text_or(front, "文件日期", "未載"), "why": why,
                     "at": str(entry.get("first_seen") or "") if why == "added" else "",
                     "open": target, "card_path": card["path"]})
        if len(rows) >= cap:
            break
    return rows


def build(vault, opened=(), today=None, hour=None, lo=None, hi=None):
    """willy-home/1。opened 是外掛記的卡編號（新到舊）；hour 是問候句挑時段用的鐘點（沒給就是現在）；
    lo、hi 是時間軸的起訖日期（裁定 34，外掛左右捲到已載入的範圍外時用；其他區不受影響）。"""
    with _one_read():
        return _build(vault, opened, today, dt.datetime.now().hour if hour is None else hour, lo, hi)


def _build(vault, opened, today, hour, lo=None, hi=None):
    today = today or agenda._today()
    warnings = []

    def safe(label, fn, default):
        try:
            return fn()
        except (ValueError, OSError, TimeoutError) as e:
            warnings.append(f"{label}讀不到：{e}")
            ledger.log_error(vault, f"kb home {label}沒讀到：{type(e).__name__}: {e}", source="home")
            return default

    items = safe("日程", lambda: agenda.visible(vault, agenda.load(vault)["items"]), [])
    ent_data = safe("實體清單", lambda: entities.load(vault), {"entities": []})
    sources = safe("來源清單", lambda: sources_lib.load(vault), None)  # 壞了只「最近」那區空白
    got = agenda.counts(vault, {"items": items}, today)
    card_of = safe("卡", lambda: _card_resolver(vault, ent_data), None)  # 讀不到只是點下去開不了卡，外掛退回開日程.md
    todo = _todo(vault, items, today, card_of)
    asks = safe("要問你的事", lambda: _asks(vault), [])
    asked = {a.get("key") for a in asks}
    others = safe("待處理", lambda: _others(vault, asked), [])
    confirm, claude = _confirm(vault, asks)
    out = {
        "schema": SCHEMA, "version": read_manifest()["version"], "today": today,
        "cheer": cheer(today, hour),
        "counts": {"overdue": got["overdue"], "today": got["today"], "week": got["upcoming"], "confirm": len(confirm),
                   "other": len(others), "claude": len(claude) + len(others)},
        "timeline": _timeline(vault, items, ent_data, today, card_of, lo, hi),
        "todo": todo, "confirm": confirm, "claude": claude, "others": others,
        "recent": [] if sources is None else safe("最近", lambda: _recent(vault, list(opened or []), sources), []),
    }
    if warnings:
        out["warnings"] = warnings
    return out


@contextlib.contextmanager
def _one_read():
    """這次呼叫的讀取只做一遍：cards.todo 一路下去把同樣的東西讀了又讀（卡 _scan_groups 十幾次、config.get 上千次各讀一次檔、
    entities.mounts 七次、cards.open_link 每份原檔一次 realpath），1000 張卡的庫要好幾秒。kb home 是只讀的一次呼叫，
    中間什麼都不會變，所以第一次的結果直接重用（turn.current 換成不開新回合的）；離開時一律還原，不影響同一個行程裡別的呼叫（測試會連著跑很多次）。"""
    saved = (cards._scan_groups, cards.open_link, config.get, entities.mounts, turn.current)
    scan, link, get, mounts, _current = saved
    memo = {"scan": {}, "link": {}, "get": {}, "mounts": {}}

    def cached_scan(vault, deadline=None):
        key = str(vault)
        if key not in memo["scan"]:
            memo["scan"][key] = scan(vault, deadline)
        groups, broken = memo["scan"][key]
        return {k: list(v) for k, v in groups.items()}, list(broken)

    def cached_link(path):
        key = str(path)
        if key not in memo["link"]:
            memo["link"][key] = link(path)
        return memo["link"][key]

    def cached_get(vault, key):
        k = (str(vault), key)
        if k not in memo["get"]:
            memo["get"][k] = get(vault, key)
        return memo["get"][k]

    def cached_mounts(vault, data=None, mentions=False):
        key = (str(vault), bool(mentions))
        if key not in memo["mounts"]:
            memo["mounts"][key] = mounts(vault, data, mentions)
        return copy.deepcopy(memo["mounts"][key])
    def no_new_turn(vault, session_id=None):
        """plan 算 tell（這一回合做了什麼）時叫 turn.current，那會開回合；kb home 只讀、不用 tell，給一個空的現況。"""
        return turn.now(vault) or {"turn": "", "session_id": "", "started": ""}
    cards._scan_groups, cards.open_link, config.get, entities.mounts, turn.current = (
        cached_scan, cached_link, cached_get, cached_mounts, no_new_turn)
    try:
        yield
    finally:
        cards._scan_groups, cards.open_link, config.get, entities.mounts, turn.current = saved


def _asks(vault):
    """跟 kb asks 同一支（asks_cli._collect：cards.todo 的 asks 加沒有整理題的待處理，先放著的不列，照急的排）。"""
    from kb import asks_cli
    return asks_cli._collect(vault)[0]
