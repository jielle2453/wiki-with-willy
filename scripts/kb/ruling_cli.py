"""kb ruling add／remove／recheck／list（note 改知識第 3 點，D-149、D-150）。

裁示只准這裡與 entities 寫（經 _lib/rulings）：每次在 rulings.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，沒有寫入」
exit 3），寫前備進回合，寫完重渲染 系統/裁示.md。拒絕的第一行就是原因、exit 3；成功句與拒絕句一字不差。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。人讀輸出不印 r-…、f-… 編號，要編號取 --json；
list 最後一行印有效裁示幾條（封頂交 lint，D-150）。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import agenda, rulings, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": rulings.RULINGS_POSIX, "action": "modify"}, {"path": rulings.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="ruling_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="記一條個案裁示", description="客戶說「只有這件這樣」：口述為準掛實體或卡，不採用掛卡。")
    ad.add_argument("--kind", required=True, help="種類：口述為準、不採用")
    ad.add_argument("--entity", action="append", help="掛的實體名稱，可以重複（口述為準才用）")
    ad.add_argument("--card", action="append", help="掛的檔案卡編號（f-…），可以重複")
    ad.add_argument("--text", required=True, help="客戶的原話")
    rm = subs.add_parser("remove", help="收回一條裁示", description="客戶說可以了：這條記成已收回（紀錄留著）。")
    rm.add_argument("id", help="裁示編號（r-…）")
    rc = subs.add_parser("recheck", help="原檔新版看過了，裁示照舊", description="客戶說還是照舊：記下掛的卡現在的版本，不再問。")
    rc.add_argument("id", help="裁示編號（r-…）")
    ls = subs.add_parser("list", help="列出有效的裁示", description="列出有效的裁示，最後一行是條數。")
    for p in (ad, rm, rc, ls):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _targets(vault, it):
    return rulings.targets(it, agenda.card_titles(vault))


def _kind(it):
    return rulings.KIND_TEXT.get(it.get("kind"), it.get("kind"))


def _add(vault, args, as_json, conversation, turn_id):
    it = rulings.add(vault, args.kind, [n.strip() for n in args.entity or []], [c.strip() for c in args.card or []],
                     args.text, turn_id)
    targets = _targets(vault, it)
    line = f"已記下裁示（{_kind(it)}）：{targets}" + (f"—{it['text']}" if it["kind"] == "ruled_out" else "")
    journal_cli.record_program_or_warn(vault, f"記下裁示（{_kind(it)}）：{targets}", CHANGED, "ruling add", conversation)
    _out(as_json, [line], {"ruling": it})
    return 0


def _remove(vault, args, as_json, conversation, turn_id):
    it = rulings.remove(vault, args.id.strip(), turn_id)
    targets = _targets(vault, it)
    journal_cli.record_program_or_warn(vault, f"收回裁示：{targets}", CHANGED, "ruling remove", conversation)
    _out(as_json, [f"已收回裁示：{targets}—{it['text']}"], {"ruling": it})
    return 0


def _recheck(vault, args, as_json, conversation, turn_id):
    it = rulings.recheck(vault, args.id.strip(), turn_id)
    targets = _targets(vault, it)
    journal_cli.record_program_or_warn(vault, f"裁示對過新版：{targets}", CHANGED, "ruling recheck", conversation)
    _out(as_json, [f"已對過新版，還是{_kind(it)}：{targets}"], {"ruling": it})
    return 0


def _list(vault, as_json):
    items = [r for r in rulings.load(vault)["rulings"] if rulings.is_active(r)]
    if as_json:
        print(json.dumps({"rulings": items}, ensure_ascii=False))
        return 0
    titles = agenda.card_titles(vault)
    for r in items:
        print(f"- {_kind(r)}：{rulings.targets(r, titles)}—{r.get('text')}（{r.get('date')}）")
    print(f"有效裁示 {len(items)} 條")
    return 0


WRITERS = {"add": _add, "remove": _remove, "recheck": _recheck}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.ruling_command
    try:
        if command == "list":
            return _list(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except rulings.RulingRefused as e:
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
