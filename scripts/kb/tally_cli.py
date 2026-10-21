"""kb tally --row "<f-…> <數字> [位置]"…（answer 第 5 點，D-159）：加總由程式算，AI 不心算。只讀、不開回合、不記日誌。

每一筆是一張檔案卡、一個數字、卡上的〔位置〕（可省）。數字收千分位逗號、小數、負號、「萬」字尾（乘一萬），其他拒絕；
卡要在清冊、原檔 active、有檔案卡（同 cards.card_of）。回逐筆表（卡標題、數字、位置）與筆數、合計、平均；
人讀不印編號（日程確認後裁定 6），數字一律印成千分位。拒絕的第一行就是原因、exit 3，一字不差。
"""
import argparse
import json
import re
from decimal import ROUND_HALF_UP, Decimal

from kb._lib import cards
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NO_ROWS = "至少要一筆"
BAD_NUMBER = "看不懂這個數字：{}"
_NUMBER = re.compile(r"^(-?)([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]+)?(萬?)$")  # 只收半形數字（\d 會收全形）
AVERAGE_PLACES = Decimal("0.01")


class TallyRefused(ValueError):
    """kb tally 的拒絕：訊息就是給人看的那句。"""


def add_arguments(sub):
    sub.add_argument("--row", action="append", default=[],
                     help="一筆：「<卡編號> <數字> [位置]」，可以重複；數字可寫 1,280,000、54萬、-3.5")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def parse_number(text):
    """「1,280,000」「54萬」「-3.5」→ Decimal；看不懂丟 TallyRefused。"""
    m = _NUMBER.match(text)
    if not m:
        raise TallyRefused(BAD_NUMBER.format(text))
    sign, whole, frac, wan = m.groups()
    value = Decimal(sign + whole.replace(",", "") + (frac or ""))
    return value * 10000 if wan else value


def show(value):
    """千分位；整數不帶小數，有小數的去掉尾巴的 0。"""
    if value == value.to_integral_value():
        return f"{int(value):,}"
    text = f"{value:,f}".rstrip("0").rstrip(".")
    return text


def _plain(value):
    return int(value) if value == value.to_integral_value() else float(value)


def tally(vault, rows):
    """回 {"rows":[{id,title,number,text,where}],"count","total","average"}；照順序驗，第一個錯丟 TallyRefused。"""
    if not rows:
        raise TallyRefused(NO_ROWS)
    out = []
    for raw in rows:
        parts = str(raw).split(None, 2)
        if not parts:
            raise TallyRefused(NO_ROWS)
        fid = parts[0]
        if len(parts) < 2:
            raise TallyRefused(BAD_NUMBER.format(""))
        if len(parts) > 2 and parts[2].startswith("萬"):  # 「54 萬」：萬跟數字分開會被當成位置，合計差一萬倍，拒絕
            raise TallyRefused(BAD_NUMBER.format(f"{parts[1]} 萬"))
        value = parse_number(parts[1])
        try:
            card = cards.card_of(vault, fid)
        except cards.CardRefused as e:
            raise TallyRefused(str(e)) from None
        out.append({"id": fid, "title": cards._title(card), "value": value, "where": parts[2].strip() if len(parts) > 2 else ""})
    total = sum((r["value"] for r in out), Decimal(0))
    average = (total / len(out)).quantize(AVERAGE_PLACES, rounding=ROUND_HALF_UP)
    if average == average.to_integral_value():
        average = average.to_integral_value()
    return {"rows": [{"id": r["id"], "title": r["title"], "number": _plain(r["value"]), "text": show(r["value"]),
                      "where": r["where"]} for r in out],
            "count": len(out), "total": _plain(total), "total_text": show(total),
            "average": _plain(average), "average_text": show(average)}


def _cell(text):
    return str(text).replace("|", "｜")


def lines(got):
    out = ["| 卡 | 數字 | 位置 |", "|---|---|---|"]
    out += [f"| {_cell(r['title'])} | {r['text']} | {_cell(r['where'])} |" for r in got["rows"]]
    out.append(f"共 {got['count']} 筆，合計 {got['total_text']}，平均 {got['average_text']}")
    return out


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    try:
        got = tally(vault, args.row)
    except TallyRefused as e:
        print(str(e))
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("\n".join(lines(got)))
    return 0
