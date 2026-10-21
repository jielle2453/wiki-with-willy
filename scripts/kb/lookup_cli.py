"""kb lookup <名稱或別名>（answer 第 1 點，D-153）：一次給齊一個實體的材料，只讀、不開回合、不記日誌。

人讀第一行是摘要（實體、型別與狀態、各類份數、還沒整理幾份），之後每類一段；不印內部編號（日程確認後裁定 6），
要編號取 --json。名稱沒登記：列提到它的卡，都沒有就一句「沒有登記這個名稱，也沒有卡提到它」。
"""
import argparse
import json

from kb._lib import agenda, entities, findings, rulings
from kb._lib import lookup as lookup_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault


def _marks(c):
    out = "〔要重做〕" if c["redo"] else ("〔原檔改過〕" if c["stale"] else "")
    return out + ("〔你說別信〕" if c["ruled_out"] else "")


def add_arguments(sub):
    sub.add_argument("name", help="實體名稱或別名（沒登記也可以，會列提到它的卡）")
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
    line = f"{e['name']}（{_kind(e)}）：" + "、".join(parts)
    if got["unsorted"]:
        line += f"；還沒整理 {got['unsorted']} 份"
    return line


def _card_line(c):
    detail = "，".join(x for x in (c["card_type"], c["read"]) if x)
    return f"- {c['date']} {c['title']}" + (f"（{detail}）" if detail else "") + _marks(c)


def _lines(got):
    if got["entity"] is None:
        tail = f"；還沒整理 {got['unsorted']} 份" if got["unsorted"] else ""
        if not got["mentions"]:
            return [lookup_lib.NOTHING + tail]
        out = [f"沒有登記「{got['query']}」；有 {got['mentions_total']} 張卡提到它{tail}"]
        out += [f"- {m['title']}（{m['where']}）" for m in got["mentions"]]
        if got["mentions_total"] > len(got["mentions"]):
            out.append(f"……還有 {got['mentions_total'] - len(got['mentions'])} 張")
        return out
    e = got["entity"]
    out = [_summary(got)]
    if e["aliases"]:
        out.append("別名：" + "、".join(e["aliases"]))
    if got["synthesis"]:
        out.append(f"綜合卡：{got['synthesis']}")
    if got["cards"]:
        out += ["檔案卡："] + [_card_line(c) for c in got["cards"]]
    if got["statements"]:
        out += ["口述："] + [f"- {s['date']} {s['text']}" for s in got["statements"]]
    if got["rulings"]:
        out.append("裁示：")
        for r in got["rulings"]:
            on = "".join(f"「{c['title'] or rulings.MISSING_CARD}」" for c in r["cards"])
            out.append(f"- {rulings.KIND_TEXT.get(r['kind'], r['kind'])}：{r['text']}" + (f"（{on}）" if on else ""))
    if got["findings"]:
        out.append("推算：")
        for c in got["findings"]:
            mark = f"〔{findings.STALE_TEXT}〕" if c["stale"] else ""
            out.append(f"- {c['text']}：{c['how']}（算於 {c['date']}）{mark}")
    if got["agenda"]:
        out.append("日程：")
        for i in got["agenda"]:
            when = agenda.with_weekday(i["date"])
            if i.get("start"):
                when = f"{agenda.with_weekday(i['start'])}～{when}"
            out.append(f"- {when}{i.get('time', '') + ' ' if i.get('time') else ''}{i['kind']} {i['text']}")
    return out


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    try:
        got = lookup_lib.lookup(vault, args.name.strip())
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("\n".join(_lines(got)))
    return 0
