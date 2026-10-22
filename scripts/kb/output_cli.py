"""kb output save／link／drop-draft（produce 第 2 點，D-164、D-168、D-170）：成品進知識庫唯一的路，本體在 _lib/output。

成功句與拒絕句一字不差（拒絕 exit 3、第一行就是原因）；人讀輸出不印清冊編號，要編號取 --json。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進那一句的回合。鎖等 30 秒，等不到「等不到鎖，沒有寫入」exit 3。
"""
import argparse
import json
import os

from kb import journal_cli
from kb._lib import inventory, output, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
SAVED = "已放進 {shown}{extra}（說「退回」會拿掉）"
LINKED = "已登記線上文件「{title}」（下次整理會做卡）"
DROPPED = "草稿收起來了：{name}（說「退回」會回來）"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="output_command", metavar="<子子命令>", required=True)
    sv = subs.add_parser("save", help="把做好的檔放進 AI 產出", description="工作區的檔用搬的、知識庫外的用複製的；先備份、登記，退回拿得掉。")
    sv.add_argument("file", help="工作區或知識庫外的檔")
    sv.add_argument("--to", required=True, help="放到哪：AI 產出/<型別>/<檔名>")
    sv.add_argument("--from", dest="source", help="改自哪份原檔（卡編號、uri 或路徑）")
    sv.add_argument("--draft", help="定稿前的草稿（AI 產出裡的（草稿）.md），收起來")
    sv.add_argument("--link", help="這份是哪個線上文件下載回來的（https://claude.ai/…）")
    lk = subs.add_parser("link", help="登記一份線上文件", description="Claude artifact（Docs、Slides、網頁）記進清冊，下次整理做卡。")
    lk.add_argument("url", help="https://claude.ai/artifact/…")
    lk.add_argument("--title", required=True, help="標題")
    lk.add_argument("--type", dest="type_", help="放在 AI 產出的哪一類（例：報告）")
    lk.add_argument("--entity", action="extend", nargs="+", default=[], help="掛的實體名稱，可以多個")
    lk.add_argument("--basis", action="extend", nargs="+", default=[], help="依據的檔案卡編號（f-…），可以多個")
    dd = subs.add_parser("drop-draft", help="草稿不要了", description="草稿備進回合後拿掉；說退回會回來。")
    dd.add_argument("draft", help="AI 產出裡的（草稿）.md")
    for p in (sv, lk, dd):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, line, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else line)


def _shown(rel):
    return rel.replace("/", "\\") if os.name == "nt" else rel


def _save(vault, args, as_json, conversation, turn_id):
    res = output.save(vault, args.file, args.to, turn_id, os.getcwd(), from_=args.source, draft=args.draft,
                      link=args.link)
    extra = (f"，改自「{res['from_label']}」" if res["from"] else "") + ("，草稿收起來了" if res["draft"] else "")
    line = SAVED.format(shown=_shown(res["path"]), extra=extra)
    changed = [{"path": res["path"], "action": "create"}] + (
        [{"path": res["draft"], "action": "delete"}] if res["draft"] else [])
    journal_cli.record_program_or_warn(vault, line.split("（說")[0], changed + [
        {"path": inventory.INVENTORY_POSIX, "action": "modify"}], "output save", conversation)
    _out(as_json, line, dict(res, ok=True, message=line))
    return 0


def _link(vault, args, as_json, conversation, turn_id):
    e = output.link(vault, args.url.strip(), args.title, turn_id, type_=args.type_, names=args.entity, basis=args.basis)
    line = LINKED.format(title=e["title"])
    journal_cli.record_program_or_warn(vault, line.split("（")[0], [{"path": inventory.INVENTORY_POSIX, "action": "modify"}],
                                       "output link", conversation)
    _out(as_json, line, {"ok": True, "id": e["id"], "url": args.url.strip(), "title": e["title"], "message": line})
    return 0


def _drop(vault, args, as_json, conversation, turn_id):
    rel = output.drop_draft(vault, args.draft, turn_id, os.getcwd())
    line = DROPPED.format(name=rel.rsplit("/", 1)[-1])
    journal_cli.record_program_or_warn(vault, line.split("（")[0], [{"path": rel, "action": "delete"}],
                                       "output drop-draft", conversation)
    _out(as_json, line, {"ok": True, "draft": rel, "message": line})
    return 0


HANDLERS = {"save": _save, "link": _link, "drop-draft": _drop}


def run(args):
    as_json = getattr(args, "json", False)
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    try:
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return HANDLERS[args.output_command](vault, args, as_json, conversation, turn_id)
    except output.Refused as e:
        _out(as_json, str(e), {"ok": False, "message": str(e)})
        return 3
    except TimeoutError:
        _out(as_json, LOCK_BUSY, {"ok": False, "message": LOCK_BUSY})
        return 3
    except ValueError as e:
        line = f"狀態檔讀不到：{e}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
    except OSError as e:
        from kb.finding_cli import rel_of
        line = f"檔案被別的程式開著或沒有權限：{rel_of(vault, e)}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
