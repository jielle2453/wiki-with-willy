"""kb journal recent／render：印最近幾段工作日誌（回答「最近改了什麼」「上次聊過什麼」，D-065、D-158）；
從 jsonl 重渲染 md（D-067，給 lint 與排除題用）。
recent 每段兩到三行：時間｜你說（前 30 字）、結果（前 30 字）｜做了、有改動才有「改了」（照路徑分類）；程式段寫「你在終端機跑的」。
--changed、--find <詞>、--since <日期> 可疊加；--json 照舊給全部欄位。
record_program_or_warn 給 init、folders、versions 寫程式段（D-085）：對話裡叫的（有 sid 且還沒答完）不寫，
代號一律用現在時間組（L2）；寫失敗印一行到 stderr，不改 exit code。CLI 鎖等 30 秒，等不到印一句 exit 3（M4）。
"""
import argparse
import json
import os
import re
import sys

from kb import guard
from kb._lib import journal, turn
from kb._lib import io as kio

NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"
EMPTY = "還沒有工作日誌"
NO_MATCH = "沒有符合的段"
HEAD_CHARS = 30
INDENT = "　"
CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到工作日誌的鎖（別的指令正在寫），沒有寫入；請稍後再試"


def _positive(text):
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"要是 1 以上的整數：{text}")
    return n


def _month(text):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        raise argparse.ArgumentTypeError(f"月份要寫成 YYYY-MM：{text}")
    return text


def _date(text):
    from datetime import datetime
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期要寫成 YYYY-MM-DD：{text}") from None
    return text


def add_arguments(sub):
    subs = sub.add_subparsers(dest="journal_command", metavar="<子子命令>", required=True)
    rc = subs.add_parser("recent", help="由新到舊印最近幾段：你說、結果、做了、改了",
                         description="由新到舊印最近幾段工作日誌；篩法可疊加。")
    rc.add_argument("--n", type=_positive, default=5, help="印幾段（1 以上），預設 5")
    rc.add_argument("--changed", action="store_true", help="只列有改動的段")
    rc.add_argument("--find", help="只列問、結果、做了裡有這個詞的段")
    rc.add_argument("--since", type=_date, help="只列這天（YYYY-MM-DD）以後的段")
    rc.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    rd = subs.add_parser("render", help="從 .system/journal 重寫 系統/工作日誌 的月檔", description="重寫工作日誌月檔。")
    rd.add_argument("month", nargs="?", type=_month, help="YYYY-MM；沒給就全部有紀錄的月")
    rd.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _head(text):
    text = journal._flat(text)
    return text[:HEAD_CHARS] + "…" if len(text) > HEAD_CHARS else text


def _lines(row):
    when = str(row.get("time", "")).replace("T", " ")[:16]
    changed = journal.changed_text(row.get("changed"))
    tail = [f"{INDENT}改了：{changed}"] if changed else []
    if row.get("kind") == "program":
        return [f"{when}｜你在終端機跑的：kb {row.get('command', '')}".rstrip(),
                f"{INDENT}做了：{journal._flat(row.get('note'))}"] + tail
    head = f"{when}｜你說：{_head(row.get('prompt'))}"
    if not row.get("done"):
        return [head, f"{INDENT}結果：{journal.IN_PROGRESS}"]
    return [head, f"{INDENT}結果：{_head(row.get('result'))}｜做了：{journal._flat(row.get('note'))}"] + tail


def _own_turn(vault):
    """對話裡這一句自己的工作日誌那行（turn hook 已經寫了開口）：--find 不算它，不然「以前問過」永遠成立（answer 裁定 12）。"""
    try:
        data = turn._read_turn(turn._current_file(vault))
    except OSError:
        return None
    return turn.journal_key(data) if data and data.get("session_id") and not data.get("done") else None


def _recent(vault, args, as_json):
    filtered = args.changed or args.find or args.since
    skip = _own_turn(vault) if args.find else None
    rows = journal.recent(vault, args.n, changed=args.changed, find=args.find, since=args.since, skip=skip)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if not rows:
        print(NO_MATCH if filtered and journal.recent(vault, 1) else EMPTY)
        return 0
    for row in rows:
        print("\n".join(_lines(row)))
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
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        if args.journal_command == "recent":
            return _recent(vault, args, as_json)
        return _render(vault, args.month, as_json)
    except TimeoutError:
        print(LOCK_BUSY)
        return 3


def in_conversation(vault):
    """對話裡叫的（D-085 補）：current.json 有 session_id 且 done 為 false，改動併進那一句、Stop 會補記，不另寫程式段。
    對話答完（done true）後在終端機跑的不算。只讀不開新回合；versions 在動作前判斷。"""
    try:
        data = turn._read_turn(turn._current_file(vault))
    except OSError:
        return False
    return bool(data and data.get("session_id") and not data.get("done"))


def record_program_or_warn(vault, note, changed, command, conversation):
    """程式段：conversation（in_conversation 的結果）為真不寫；代號由 record_program 用現在時間組（L2）。
    寫失敗（鎖、IO）印一行到 stderr，不擋指令。"""
    try:
        if conversation:
            return
        journal.record_program(vault, note, changed, command)
    except Exception as e:  # noqa: BLE001  日誌沒記到不能讓指令失敗
        print(f"工作日誌沒記到：{type(e).__name__}: {e}", file=sys.stderr)
