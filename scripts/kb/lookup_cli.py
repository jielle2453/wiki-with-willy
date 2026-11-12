"""kb lookup <名稱或別名>（answer 第 1 點，D-153）：一次給齊一個實體的材料，不開回合、不記日誌。
#46：順帶「回答末尾要問的事」（--json 的 asks，同 kb asks --about；人讀最後幾行），answer 不必再叫 kb asks；
跟 kb asks 一樣會寫 .system/asks_seen.json（同 session 問過的不再問）、關掉到期的題。同一句只有第一次 lookup 帶。

人讀第一行是摘要（實體、型別與狀態、各類份數、還沒整理幾份），之後每類一段；不印內部編號（日程確認後裁定 6），
要編號取 --json。名稱沒登記：列提到它的卡，都沒有就一句「沒有登記這個名稱，也沒有卡提到它」，有製作中的檔再一行列出來（#37）。
"""
import argparse
import json

from kb._lib import agenda, card_index, cards, entities, rulings
from kb._lib import lookup as lookup_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault


def _marks(c):
    out = "〔要重做〕" if c["redo"] else ("〔原檔改過〕" if c["stale"] else "")
    return out + ("〔你說別信〕" if c["ruled_out"] else "") + ("〔已封存〕" if c.get("archived") else "")


def add_arguments(sub):
    sub.add_argument("name", nargs="?", default="", help="實體名稱或別名（沒登記也可以，會列提到它的卡）")
    sub.add_argument("--id", help="清冊編號 f-…：只查這份原檔在這台的位置（Obsidian 外掛的開原檔連結用），回 open 與卡的路徑")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _kind(e):
    if e.get("self"):
        return "你自己"
    text = e.get("type") or ""
    if e.get("progress"):
        text += f"，{entities.PROGRESS_TEXT.get(e['progress'], e['progress'])}"
    return text


def _summary(got):
    e = got["entity"]
    cards = got["cards"]
    parts = [f"綜合卡{'有' if got['synthesis'] else '沒有'}"]
    flags = []
    for n, text in ((sum(1 for c in cards if c["stale"] and not c["redo"]), "原檔改過"),
                    (sum(1 for c in cards if c["redo"]), "要重做")):
        if n:
            flags.append(f"{n} 張{text}")
    banned = sum(1 for c in cards if c["ruled_out"])
    if banned:
        flags.append(f"{banned} 張你說別信")
    parts.append(f"檔案卡 {len(cards)} 張" + (f"（{'、'.join(flags)}）" if flags else ""))
    for key, label, unit in (("statements", "口述", "條"), ("rulings", "裁示", "條"), ("findings", "推算", "筆"),
                             ("agenda", "日程", "條")):
        if got[key]:
            parts.append(f"{label} {len(got[key])} {unit}")
    line = f"{e['name']}（{_kind(e)}{'，已封存' if got.get('archived') else ''}）：" + "、".join(parts)
    return line


def _card_line(c):
    detail = "，".join(x for x in (c["card_type"], c["read"]) if x)
    return f"- {c['date']} {c['title']}" + (f"（{detail}）" if detail else "") + _marks(c)


def wip_lines(got):
    """lookup、find 找不到時附的製作中檔一行（#37）；沒有回 []。"""
    wip, total = got.get("in_progress") or [], got.get("in_progress_total", 0)
    if not wip:
        return []
    more = f"（還有 {total - len(wip)} 份沒列）" if total > len(wip) else ""
    return [f"還沒整理的 {total} 份可能有提到（像的讀原檔答，標「還沒整理，照原檔」）："
            + "、".join(cards.wip_label(r) for r in wip) + more]


def _lines(got):
    if got["entity"] is None:
        names = "、".join(x["name"] + (f"（{x['type']}）" if x.get("type") else "") for x in got.get("similar") or [])
        similar = [f"名稱有這個字的：{names}；要哪個用全名再查"] if names else []
        if not got["mentions"]:
            return [lookup_lib.NOTHING] + similar + wip_lines(got)
        out = [f"沒有登記「{got['query']}」；有 {got['mentions_total']} 張卡提到它"] + similar
        out += [f"- {m['title']}（{m['where']}）" for m in got["mentions"]]
        if got["mentions_total"] > len(got["mentions"]):
            out.append(f"……還有 {got['mentions_total'] - len(got['mentions'])} 張")
        return out
    e = got["entity"]
    out = [f"沒有登記「{got['query']}」，照「{got['partial']}」查："] if got.get("partial") else []
    out.append(_summary(got))
    if e["aliases"]:
        out.append("別名：" + "、".join(e["aliases"]))
    if got["synthesis"]:
        rng = "、".join(f"{a}～{a + n - 1}" for a, n in got.get("read_ranges") or [])
        out.append(f"綜合卡：{got['synthesis']}" + (f"（讀第 {rng} 行；紀事要用再 Grep）" if rng else ""))
    tags = [f"{t['field']} {'、'.join(t['words'])}" for t in got.get("tags") or [] if t["words"]]
    if tags:
        out.append("標籤：" + "；".join(tags))
    if got["cards"]:
        out += ["檔案卡："] + [_card_line(c) for c in got["cards"]]
    if got["statements"]:
        out += ["口述："] + [f"- {s['date']} {s['text']}" + (f"（{s['said']}）" if s.get("said") else "")  # 多人帶誰說的（#43）
                           for s in got["statements"]]
    if got["rulings"]:
        out.append("裁示：")
        for r in got["rulings"]:
            on = "".join(f"「{c['title'] or rulings.MISSING_CARD}」" for c in r["cards"])
            out.append(f"- {rulings.KIND_TEXT.get(r['kind'], r['kind'])}：{r['text']}" + (f"（{on}）" if on else ""))
    if got["findings"]:
        out.append("推算：")
        for c in got["findings"]:  # 依據變了的不印舊結果（B-100）
            head = c["redo"] if c["stale"] else f"{c['text']}：{c['how']}"
            out.append(f"- {head}（算於 {c['date']}）")
    if got["agenda"]:
        out.append("日程：")
        for i in got["agenda"]:
            when = agenda.with_weekday(i["date"])
            if i.get("start"):
                when = f"{agenda.with_weekday(i['start'])}～{when}"
            out.append(f"- {when}{i.get('time', '') + ' ' if i.get('time') else ''}{i['kind']} {i['text']}")
    if got.get("in_progress"):  # 製作中（D-525）：還沒整理，問到讀原檔
        out.append("製作中：" + "、".join(cards.wip_label(r) for r in got["in_progress"]))
    return out


def _by_id(vault, args):
    """--id：{"id","open","card_path"}。open 同首頁的 open 物件（path 絕對路徑正斜線｜url｜None 在這台找不到），只讀。"""
    from kb._lib import cards, home, inventory
    from kb._lib import sources as sources_lib
    fid = args.id.strip()
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid and e.get("status") == "active"), None)
    group = cards._scan_groups(vault)[0].get(fid)
    got = {"id": fid, "open": home._open_target(vault, sources_lib.load(vault), entry) if entry else None,
           "card_path": group[0]["path"] if group else None}
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    else:
        target = got["open"] or {}
        print(target.get("path") or target.get("url") or "這台電腦找不到這份原檔")
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    with card_index.once(vault):  # 只讀：卡只走一遍（#57）
        return _run(vault, args)


def _run(vault, args):
    if getattr(args, "id", None):
        try:
            return _by_id(vault, args)
        except ValueError as e:
            print(f"狀態檔讀不到：{e}")
            return 3
    if not args.name.strip():
        print("要給實體名稱，或用 --id 查一份原檔的位置")
        return 3
    try:
        from kb._lib import home
        with home._one_read():  # 只讀，卡只掃一遍（原本一次 4 遍以上，#38）
            got = lookup_lib.lookup(vault, args.name.strip())
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except Exception as e:  # noqa: BLE001  OSError 這類：一句白話，不印 traceback 與絕對路徑（answer 裁定 22）
        print(f"查不出來（{type(e).__name__}），沒有結果")
        return 3
    _touch(vault, got)
    got["asks"] = _end_asks(vault, got)
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("\n".join(_lines(got) + _end_lines(got)))
    return 0


ASKS_FLAG = "asks_given"  # 回合旗標：這一句的 lookup 已經帶過要問的事


def _end_asks(vault, got):
    """回答末尾要問的事（#46，同 kb asks --about <查的實體> 的 --json，回過的照樣記進 asks_seen），answer 不必再叫 kb asks。
    同一句只有第一次帶出題的 lookup 帶（回合記 asks_given），之後是 None，不然一句裡好幾題都被記成問過；沒帶出題不標，
    同句後面 lookup 別的實體照樣帶。旗標讀寫都是 mark 那個回合（對話裡還沒答完的），終端機與答完的回合每次都帶。
    --turn（子代理、card-maker）不帶：它不回答客戶，帶了會把主對話沒問的題記成問過。算不出來（鎖忙、狀態檔壞）也是 None，不擋查詢。"""
    from kb import asks_cli
    from kb._lib import turn
    if turn.OVERRIDE:
        return None
    try:
        data = turn.last(vault)
        if data and data.get("session_id") and not data.get("done") and data.get(ASKS_FLAG):
            return None
        out = asks_cli.about(vault, [got["entity"]["name"] if got["entity"] else got["query"]])
    except Exception:  # noqa: BLE001
        return None
    if asks_cli.end_lines(out):
        try:
            turn.mark(vault, ASKS_FLAG)
        except Exception:  # noqa: BLE001  標不到：題已記成問過，照樣回，不吞掉
            pass
    return out


def _end_lines(got):
    from kb import asks_cli
    return asks_cli.end_lines(got["asks"]) if got.get("asks") else []


def _touch(vault, got):
    """對話裡查過的實體與它的卡記進回合 touched（lint 題，D-225：回答末尾只問跟這一句有關的）；記不到不擋查詢。"""
    if got["entity"] is None:
        return
    try:
        from kb._lib import turn
        e, _hit = entities.find(entities.load(vault), got["entity"]["name"])
        turn.touch(vault, entities=[e["id"]] if e else [], files=[c["id"] for c in got["cards"] if c.get("id")])
    except Exception:  # noqa: BLE001
        pass
