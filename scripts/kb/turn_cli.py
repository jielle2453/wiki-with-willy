"""kb turn current／approve：看目前回合；客戶同意後解除這一回合的綜合卡張數上限（D-075）。
CLI 由模型呼叫時拿不到 session_id，用 .system/turns/current.json（最近一句話的回合，B-019）。
"""
import argparse
import json
import os

from kb import guard
from kb._lib import turn

NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="turn_command", metavar="<子子命令>", required=True)
    cur = subs.add_parser("current", help="印目前回合：代號、問、已備份檔數、上限狀態", description="印目前回合。")
    cur.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    ap = subs.add_parser("approve", help="客戶同意後解除這一回合的綜合卡張數上限", description="解除這一回合的綜合卡張數上限。")
    ap.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    as_json = getattr(args, "json", False)
    if args.turn_command == "approve":
        t = turn.approve(vault)
        if as_json:
            print(json.dumps({"turn": t["turn"], "approved": True}, ensure_ascii=False))
        else:
            print("這一題解除綜合卡張數上限")
        return 0
    t = turn.current(vault)
    n = len(turn.entries(vault, t["turn"]))
    if as_json:
        print(json.dumps({"turn": t["turn"], "prompt_head": t.get("prompt_head", ""), "backed_up": n,
                          "approved": bool(t.get("approved"))}, ensure_ascii=False))
    else:
        limit = "已解除上限" if t.get("approved") else "未解除上限"
        print(f"{t['turn']}｜問：「{t.get('prompt_head', '')}」｜已備份 {n} 檔｜{limit}")
    return 0
