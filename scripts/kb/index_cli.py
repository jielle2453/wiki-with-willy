"""kb index rebuild [--json]（#57，D-345）：重建卡片索引（放在知識庫外的快取，每台自己建）。
找的結果跟卡對不上、或服務人員要重建時用，平常不用跑（每次讀卡索引自己會跟上）。人讀「索引重建好了：N 張卡，X 秒」；--json 印 {"cards": N, "seconds": X}。
索引不能用（沒有知識庫編號、索引夾寫不進去）印原因、exit 1。
"""
import argparse
import json

from kb._lib import card_index
from kb.inventory_cli import NOT_IN_VAULT, find_vault


def add_arguments(sub):
    subs = sub.add_subparsers(dest="index_command", metavar="<子子命令>", required=True)
    rb = subs.add_parser("rebuild", help="重建卡片索引", description="刪掉這個知識庫的卡片索引、照現在的卡重建；知識庫裡的檔一個都不動。")
    rb.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    rb.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    try:
        count, seconds = card_index.rebuild(vault)
    except card_index.Unusable as e:
        print(str(e))
        return 1
    except Exception as e:  # noqa: BLE001  TimeoutError 之外的 OSError 這類：一句白話
        print(f"索引沒建成：{type(e).__name__}: {e}")
        return 1
    seconds = round(seconds, 1)
    if getattr(args, "json", False):
        print(json.dumps({"cards": count, "seconds": seconds}, ensure_ascii=False))
    else:
        print(f"索引重建好了：{count} 張卡，{seconds} 秒")
    return 0
