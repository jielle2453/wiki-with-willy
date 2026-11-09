"""kb home --json [--opened f-…,f-…] [--from YYYY-MM-DD] [--to YYYY-MM-DD]（Obsidian 首頁，#22，D-252）：首頁一次要的東西一支給齊，格式 willy-home/1。
只讀、不開回合；給外掛用，沒帶 --json 不做（人讀模式不做）。--opened 是外掛記的、她從首頁開過的卡編號（新到舊），不在清冊 active 的略過。
--from／--to（選填，裁定 34）：時間軸畫哪一段；沒帶照舊（今天前 7 天到 home_timeline_days 天後）。外掛左右捲到已載入的範圍外時再叫一次。
"""
import argparse
import datetime as dt
import json

from kb._lib import home
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NEEDS_JSON = "這個指令給外掛用，要帶 --json"
BAD_RANGE = "--from、--to 要寫成 YYYY-MM-DD 的日期，而且 --from 不晚於 --to"


def add_arguments(sub):
    sub.add_argument("--opened", default="", help="她從首頁開過的卡編號，逗號分隔，新到舊")
    sub.add_argument("--from", dest="from_day", default=None, help="時間軸起日 YYYY-MM-DD（選填）")
    sub.add_argument("--to", dest="to_day", default=None, help="時間軸訖日 YYYY-MM-DD（選填）")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _day(text):
    """沒給回 None；YYYY-MM-DD 回原字串；其他回 False。"""
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text).isoformat() if len(text) == 10 else False
    except ValueError:
        return False


def run(args):
    if not getattr(args, "json", False):
        print(NEEDS_JSON)
        return 3
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    lo, hi = _day(args.from_day), _day(args.to_day)
    if lo is False or hi is False or (lo and hi and lo > hi):
        print(BAD_RANGE)
        return 3
    opened = [x.strip() for x in str(args.opened or "").split(",") if x.strip()]
    print(json.dumps(home.build(vault, opened, lo=lo, hi=hi), ensure_ascii=False))
    return 0
