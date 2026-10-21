"""kb statement add／withdraw／list（note 改知識第 3 點，D-146）。

口述清單只准這裡與 entities 寫（經 _lib/statements）：每次在 statements.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，
沒有寫入」exit 3），寫前備進回合。拒絕的第一行就是原因、exit 3；成功句與拒絕句一字不差。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。人讀輸出不印 s-… 編號（日程確認後裁定 6），要編號取 --json。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import statements, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": statements.STATEMENTS_POSIX, "action": "modify"}]
STATUS_SUFFIX = {"superseded": "（已被取代）", "withdrawn": "（已收回）"}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="statement_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="記一條客戶口述", description="記一句客戶說的事實或決定，掛在登記過的實體上。")
    ad.add_argument("--entity", action="append", required=True, help="掛的實體名稱，可以重複（客戶自己也可以）")
    ad.add_argument("--text", required=True, help="一句話，最多 200 字")
    ad.add_argument("--replaces", help="改的是哪一條舊口述（s-…）：舊的記成被這條取代")
    wd = subs.add_parser("withdraw", help="收回一條口述", description="客戶說沒這回事：這條記成已收回（紀錄留著）。")
    wd.add_argument("id", help="口述編號（s-…）")
    ls = subs.add_parser("list", help="列出口述", description="預設列有效的；--all 含被取代與收回的。")
    ls.add_argument("--entity", help="只列掛在這個實體上的")
    ls.add_argument("--all", action="store_true", help="全部列出")
    for p in (ad, wd, ls):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _add(vault, args, as_json, conversation, turn_id):
    it, old = statements.add(vault, args.text, [n.strip() for n in args.entity], turn_id,
                             replaces=args.replaces.strip() if args.replaces else None)
    if old is None:
        line, note = f"已記下：{it['text']}（{'、'.join(it['entities'])}）", f"記下口述：{it['text']}"
    else:
        line, note = f"已改：{old['text']} → {it['text']}", f"改口述：{old['text']} → {it['text']}"
    journal_cli.record_program_or_warn(vault, note, CHANGED, "statement add", conversation)
    _out(as_json, [line], {"statement": it, "replaced": old})
    return 0


def _withdraw(vault, args, as_json, conversation, turn_id):
    it = statements.withdraw(vault, args.id.strip(), turn_id)
    journal_cli.record_program_or_warn(vault, f"收回口述：{it['text']}", CHANGED, "statement withdraw", conversation)
    _out(as_json, [f"已收回：{it['text']}"], {"statement": it})
    return 0


def _list(vault, args, as_json):
    from kb._lib import entities
    data = statements.load(vault)
    items = data["statements"] if args.all else [s for s in data["statements"] if s.get("status") == "active"]
    if args.entity:
        k = entities.key(args.entity.strip())
        found, _hit = entities.find(entities.load(vault), args.entity.strip())
        if found is not None:
            k = entities.key(found["name"])
        items = [s for s in items if any(entities.key(n) == k for n in s.get("entities") or [])]
    order = {id(s): i for i, s in enumerate(data["statements"])}  # 同一天照記的先後，新的在上
    items = sorted(items, key=lambda s: (s.get("date") or "", order[id(s)]), reverse=True)
    if as_json:
        print(json.dumps({"statements": items}, ensure_ascii=False))
        return 0
    if not items:
        print("口述清單是空的")
        return 0
    for s in items:
        print(f"- {s.get('date')} {s.get('text')}（{'、'.join(s.get('entities') or [])}）{STATUS_SUFFIX.get(s.get('status'), '')}")
    return 0


WRITERS = {"add": _add, "withdraw": _withdraw}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.statement_command
    try:
        if command == "list":
            return _list(vault, args, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except statements.StatementRefused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
