"""kb pending list／resolve／dismiss（ingest 製卡第 5 點，D-102）：看等客戶決定的事、記下客戶怎麼回。

list 只印 open；resolve <id> --how "<一句>" 記 done，dismiss <id> 記 dismissed（客戶說不用管），兩者印「已記下」並重渲染 系統/待處理.md。
寫一律經 _lib/pending（先備進目前回合）。對不存在或已處理過的 id 第一行說原因、exit 3。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import pending, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
KIND_TEXT = {"missing_file": "原檔不見", "unreadable": "讀不到"}
CHANGED = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="pending_command", metavar="<子子命令>", required=True)
    ls = subs.add_parser("list", help="列出等你決定的事", description="列出還沒處理的待處理條目。")
    rs = subs.add_parser("resolve", help="記下怎麼處理了", description="把一條待處理記成已處理，--how 寫一句怎麼處理。")
    rs.add_argument("id", help="待處理編號（p-…）")
    rs.add_argument("--how", required=True, help="一句：怎麼處理的")
    dm = subs.add_parser("dismiss", help="記成不用管", description="把一條待處理記成不用管。")
    dm.add_argument("id", help="待處理編號（p-…）")
    for p in (ls, rs, dm):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _text(item):
    reason = item.get("reason")
    return item.get("text", "") + (f"：{reason}" if item.get("kind") == "unreadable" and reason else "")


def _list(vault, as_json):
    opened = [i for i in pending.load(vault)["items"] if i.get("status") == "open"]
    if as_json:
        print(json.dumps(opened, ensure_ascii=False))
        return 0
    if not opened:
        print(pending.NOTHING.rstrip("。"))
        return 0
    for item in opened:
        print(f"- {item['id']} {KIND_TEXT.get(item.get('kind'), item.get('kind'))} {_text(item)}")
    return 0


def _close(vault, args, as_json):
    conversation = journal_cli.in_conversation(vault)
    turn_id = turn.current(vault)["turn"]
    if args.pending_command == "resolve":
        item = pending.resolve(vault, args.id, args.how, turn_id)
        how = args.how
    else:
        item = pending.dismiss(vault, args.id, turn_id)
        how = "不用管"
    journal_cli.record_program_or_warn(vault, f"待處理 {args.id}：{how}", CHANGED, f"pending {args.pending_command}",
                                       conversation)
    if as_json:
        print(json.dumps(item, ensure_ascii=False))
    else:
        print("已記下")
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        if args.pending_command == "list":
            return _list(vault, as_json)
        return _close(vault, args, as_json)
    except KeyError as e:
        print(e.args[0] if e.args else str(e))
        return 3
    except ValueError as e:
        print(f"待處理讀不到：{e}")
        return 3
    except TimeoutError:
        print("等不到待處理的鎖（別的指令正在改它），沒有寫入；請稍後再試")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
