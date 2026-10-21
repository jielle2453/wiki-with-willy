"""kb pending list／add／resolve／dismiss（ingest 製卡第 5 點，D-102）：看等客戶決定的事、記下客戶怎麼回。

list 只印 open；resolve <id> --how "<一句>" 記 done，dismiss <id> 記 dismissed（客戶說不用管），兩者印「已記下」並重渲染 系統/待處理.md。
add --kind conflict --card <f-…> --card <f-…> [--entity <名>]… --text "<一句>"（answer 第 6 點，D-155）：answer 核對時發現兩份原檔
寫法不同；兩張卡要有檔案卡、不能是同一張，同一對已有 open 的回既有那條（「已經記過了」）。人讀不印編號。
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
KIND_TEXT = {"missing_file": "原檔不見", "unreadable": "讀不到", "close": "要確認結案", "conflict": "寫法不同"}
SAME_CARD = "兩張要是不同的卡"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"
TEXT_LABEL = "說明"


class AddRefused(ValueError):
    """kb pending add 的拒絕：訊息就是給人看的那句。"""
CHANGED = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="pending_command", metavar="<子子命令>", required=True)
    ls = subs.add_parser("list", help="列出等你決定的事", description="列出還沒處理的待處理條目。")
    rs = subs.add_parser("resolve", help="記下怎麼處理了", description="把一條待處理記成已處理，--how 寫一句怎麼處理。")
    rs.add_argument("id", help="待處理編號（p-…）")
    rs.add_argument("--how", required=True, help="一句：怎麼處理的")
    dm = subs.add_parser("dismiss", help="記成不用管", description="把一條待處理記成不用管。")
    dm.add_argument("id", help="待處理編號（p-…）")
    ad = subs.add_parser("add", help="記一件兩份寫法不同的", description="兩份原檔寫法不同：記一條待處理，等客戶說哪個對。")
    ad.add_argument("--kind", required=True, choices=["conflict"], help="這版只收 conflict（兩份寫法不同）")
    ad.add_argument("--card", action="append", default=[], help="寫法不同的檔案卡編號（f-…），兩張")
    ad.add_argument("--entity", action="append", default=[], help="相關的實體名稱，可以重複")
    ad.add_argument("--text", required=True, help="一句：哪裡寫得不同")
    for p in (ls, rs, dm, ad):
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


def _add(vault, args, as_json):
    from kb._lib import cards, entities, statements
    ids = [c.strip() for c in args.card]
    if len(ids) != 2 or ids[0] == ids[1]:
        raise AddRefused(SAME_CARD)
    titles = []
    for fid in ids:
        try:
            titles.append(cards._title(cards.card_of(vault, fid)))
        except cards.CardRefused as e:
            raise AddRefused(str(e)) from None
    ent = entities.load(vault)
    entity_ids = []
    for name in args.entity:
        e, _hit = entities.find(ent, name.strip())
        if e is None or e.get("status") != "active":
            raise AddRefused(NO_ENTITY.format(name.strip()))
        if e["id"] not in entity_ids:
            entity_ids.append(e["id"])
    text = statements.check_text(args.text, TEXT_LABEL, AddRefused)
    conversation = journal_cli.in_conversation(vault)
    item, created = pending.add_conflict(vault, ids, text, turn.current(vault)["turn"], entity_ids=entity_ids,
                                         titles=titles)
    if created:
        journal_cli.record_program_or_warn(vault, f"記進待處理：{item['text']}", CHANGED, "pending add", conversation)
    line = f"已記進待處理：{item['text']}" if created else f"已經記過了：{item['text']}"
    if as_json:
        print(json.dumps({"item": item, "created": created}, ensure_ascii=False))
    else:
        print(line)
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
        if args.pending_command == "add":
            return _add(vault, args, as_json)
        return _close(vault, args, as_json)
    except AddRefused as e:
        print(str(e))
        return 3
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
        from kb.finding_cli import rel_of
        print(f"狀態檔讀不到：{rel_of(vault, e)}")
        return 3
