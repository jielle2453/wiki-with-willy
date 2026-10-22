"""kb source-edit move／rename／trash／where／open（manage 第二版，D-173～D-178）：幫客戶搬檔、改檔名、丟資源回收筒、
推薦去處、開檔案總管。本體在 _lib/source_edit。

要動的東西用卡編號（f-…）、清冊 uri（file://<別名>/<相對路徑>）或路徑指，照 kb convert 同一套；夾用路徑或 uri。
move、rename、trash 收 --check：只算不動，印給 AI 講影響的幾行。人讀輸出不印編號，--json 有。
拒絕一律一行原因、exit 3、什麼都沒動。move、rename、trash 在 sources.json 的鎖裡做（不讀的子夾 spec 要跟著改寫）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進那一句。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import source_edit as se
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib import turn
from kb._lib.io import FileLock

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行，或加 --vault"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="edit_command", metavar="<子子命令>", required=True)
    mv = subs.add_parser("move", help="搬一份檔或一個夾", description="把一份檔或一個夾搬到登記的夾或實體夾；同一顆硬碟才代搬。")
    mv.add_argument("target", help="卡編號（f-…）、清冊 uri 或路徑")
    mv.add_argument("--to", required=True, help="目標夾（路徑或 uri）")
    mv.add_argument("--replace", action="store_true", help="目標有同名的就先把它丟進資源回收筒（客戶明確說取代才加）")
    rn = subs.add_parser("rename", help="改檔名或夾名", description="改一份檔或一個夾的名字；副檔名不改。")
    rn.add_argument("target", help="卡編號（f-…）、清冊 uri 或路徑")
    rn.add_argument("new_name", help="新名字（可以不寫副檔名）")
    tr = subs.add_parser("trash", help="丟進資源回收筒", description="把一份檔丟進資源回收筒，卡拿掉；只收檔。")
    tr.add_argument("target", help="卡編號（f-…）、清冊 uri 或路徑")
    wh = subs.add_parser("where", help="這個實體的檔放在哪些夾（只讀）", description="列這個實體掛的檔放在哪些夾、各幾份。")
    wh.add_argument("--entity", required=True, help="實體名稱或別名")
    wh.add_argument("--card-type", help="只算這個卡型")
    op = subs.add_parser("open", help="在檔案總管打開夾", description="在檔案總管打開一或兩個夾（只開不寫）。")
    op.add_argument("folders", nargs="+", help="夾的路徑或 uri，最多兩個")
    for p in (mv, rn, tr):
        p.add_argument("--check", action="store_true", help="只算影響，不動")
    for p in (mv, rn, tr, wh, op):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _vault(args):
    found = guard.find_vault(args.vault or os.getcwd())
    return Path(os.path.realpath(str(found))) if found is not None else None


def _public(plan):
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def _move(vault, args, as_json, conversation, op):
    cwd = os.getcwd()
    with FileLock(Path(vault) / sources_lib.SOURCES_REL):
        sources = sources_lib.load(vault)
        if op == "rename":
            plan = se.plan_move(vault, sources, args.target, cwd, new_name=args.new_name)
        else:
            plan = se.plan_move(vault, sources, args.target, cwd, dest=args.to)
        if args.check:
            lines = se.check_lines(plan, op)
            print(json.dumps(dict(_public(plan), lines=lines), ensure_ascii=False) if as_json else "\n".join(lines))
            return 0
        turn_id = turn.current(vault)["turn"]
        got = se.do_move(vault, plan, turn_id, op, replace=getattr(args, "replace", False))
    sentence = se.move_sentence(plan, got, op)
    replaced = got["replaced"]
    lines = [sentence]
    if replaced is not None:
        lines.append(f"原本的「{plan['new_name']}」丟進資源回收筒了" + ("，卡拿掉了" if replaced["card_removed"] else ""))
    journal_cli.record_program_or_warn(vault, sentence, got["changed"], f"source-edit {op}", conversation)
    if as_json:
        print(json.dumps(dict(_public(plan), to_uri=got["to_uri"], cards_moved=got["cards_moved"],
                              replaced=bool(replaced), sentence=sentence), ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0


def _trash(vault, args, as_json, conversation):
    with FileLock(Path(vault) / sources_lib.SOURCES_REL):
        sources = sources_lib.load(vault)
        plan = se.plan_trash(vault, sources, args.target, os.getcwd())
        if args.check:
            lines = se.trash_lines(plan)
            print(json.dumps(dict(plan, lines=lines), ensure_ascii=False) if as_json else "\n".join(lines))
            return 0
        got = se.do_trash(vault, plan, turn.current(vault)["turn"])
    sentence = se.trash_sentence(plan, got)
    journal_cli.record_program_or_warn(vault, sentence, got["changed"], "source-edit trash", conversation)
    if as_json:
        print(json.dumps(dict(plan, card_removed=got["card_removed"], agenda_cancelled=got["cancelled"],
                              sentence=sentence), ensure_ascii=False))
    else:
        print(sentence)
    return 0


def _where(vault, args, as_json):
    got = se.where(vault, args.entity.strip(), args.card_type.strip() if args.card_type else None)
    print(json.dumps(got, ensure_ascii=False) if as_json else "\n".join(se.where_lines(got)))
    return 0


def _open(vault, args, as_json):
    paths = se.check_open(vault, sources_lib.load(vault), args.folders, os.getcwd())
    se.open_folders(paths)
    if as_json:
        print(json.dumps({"opened": paths}, ensure_ascii=False))
    else:
        print("已打開：" + "、".join(Path(p).name or p for p in paths))
    return 0


def run(args):
    vault = _vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.edit_command
    try:
        if command == "where":
            return _where(vault, args, as_json)
        if command == "open":
            return _open(vault, args, as_json)
        conversation = journal_cli.in_conversation(vault)
        if command == "trash":
            return _trash(vault, args, as_json, conversation)
        return _move(vault, args, as_json, conversation, command)
    except se.Refused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print(se.LOCKED)
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有動：{e.strerror or type(e).__name__}")
        return 3
