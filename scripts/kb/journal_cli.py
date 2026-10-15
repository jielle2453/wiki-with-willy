"""kb journal recent／render：印最近幾段工作日誌（回答「最近改了什麼」，D-065）；從 jsonl 重渲染 md（D-067，給 lint 與排除題用）。
record_program_or_warn 給 init、folders、versions 寫程式段（D-085）：只在 CLI 自己的回合（session_id 空）才寫，
寫失敗印一行到 stderr，不改 exit code。
"""
import argparse
import json
import os
import re
import sys

from kb import guard
from kb._lib import journal, turn

NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"
EMPTY = "還沒有工作日誌"


def _month(text):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        raise argparse.ArgumentTypeError(f"月份要寫成 YYYY-MM：{text}")
    return text


def add_arguments(sub):
    subs = sub.add_subparsers(dest="journal_command", metavar="<子子命令>", required=True)
    rc = subs.add_parser("recent", help="由新到舊印最近幾段：時間｜做了", description="由新到舊印最近幾段工作日誌。")
    rc.add_argument("--n", type=int, default=5, help="印幾段，預設 5")
    rc.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    rd = subs.add_parser("render", help="從 .system/journal 重寫 系統/工作日誌 的月檔", description="重寫工作日誌月檔。")
    rd.add_argument("month", nargs="?", type=_month, help="YYYY-MM；沒給就全部有紀錄的月")
    rd.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _line(row):
    when = str(row.get("time", "")).replace("T", " ")
    if row.get("kind") == "program":
        return f"{when}｜程式｜做了：{journal._flat(row.get('note'))}"
    note = journal._flat(row.get("note")) if row.get("done") else journal.IN_PROGRESS
    return f"{when}｜做了：{note}"


def _recent(vault, n, as_json):
    rows = journal.recent(vault, n)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if not rows:
        print(EMPTY)
        return 0
    for row in rows:
        print(_line(row))
    return 0


def _render(vault, month, as_json):
    have = journal.months(vault)
    months = ([month] if month in have else []) if month else have
    for m in months:
        journal.render(vault, m)
    if as_json:
        print(json.dumps({"months": months}, ensure_ascii=False))
    else:
        print(f"已重寫 {len(months)} 個月")
    return 0


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    as_json = getattr(args, "json", False)
    if args.journal_command == "recent":
        return _recent(vault, args.n, as_json)
    return _render(vault, args.month, as_json)


def in_conversation(vault):
    """目前回合是對話開的（有 session_id）：CLI 的改動併進那一句，不另寫程式段（D-085）。不開新回合。"""
    data = turn._read_turn(turn._current_file(vault))
    return bool(data and data.get("session_id"))


def record_program_or_warn(vault, note, changed, command, turn_id=None):
    """程式段：對話裡叫的不寫；寫失敗（鎖、IO）印一行到 stderr，不擋指令。"""
    try:
        if in_conversation(vault):
            return
        journal.record_program(vault, note, changed, command, turn_id=turn_id)
    except Exception as e:  # noqa: BLE001  日誌沒記到不能讓指令失敗
        print(f"工作日誌沒記到：{type(e).__name__}: {e}", file=sys.stderr)
