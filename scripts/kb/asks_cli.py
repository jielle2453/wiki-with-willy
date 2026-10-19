"""kb asks [--json]（日程第 2 點，D-134、D-135）：只讀，回要問客戶的事 asks 與回答末尾要說的 tell。

跟 kb cards todo --json 裡的 asks、tell 同一支函式（plan.build，跑一次 cards.todo 再取這兩鍵），編號一樣從 1 連號；
給沒有跑 ingest 的時候用：開場有逾期、note 回答末尾。人讀照編號印問題與選項，沒有就印「沒有要問的」。
"""
import argparse
import json

from kb._lib import cards
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NOTHING = "沒有要問的"


def add_arguments(sub):
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    try:
        data = cards.todo(vault, sources_lib.load(vault))
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"讀不到：{type(e).__name__}: {e}")
        return 3
    out = {"asks": data["asks"], "tell": data["tell"]}
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False))
        return 0
    lines = []
    for ask in out["asks"]:
        lines.append(ask["question"])
        lines.extend(f"  {r['n']}. {r['label']}" for r in ask["results"])
    print("\n".join(lines or [NOTHING]))
    if out["tell"]:
        print("回答末尾要說：")
        print("\n".join(out["tell"]))
    return 0
