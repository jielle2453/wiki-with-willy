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
    mv = subs.add_parser("move", help="搬檔或夾", description="把檔或夾搬到使用者的任何夾（willy 自己的區除外）；可以一次給多個。")
    mv.add_argument("target", nargs="+", help="卡編號（f-…）、清冊 uri 或路徑，可以多個")
    mv.add_argument("--to", required=True, help="目標夾（路徑或 uri）")
    mv.add_argument("--contents", action="store_true", help="只搬夾裡的東西，搬完空了的原夾拿掉")
    mv.add_argument("--name", help="搬過去順便改名（只收一個目標）")
    mv.add_argument("--replace", action="store_true", help="目標有同名的就先把它丟進資源回收筒（使用者明確說取代才加）")
    rn = subs.add_parser("rename", help="改檔名或夾名", description="改一份檔或一個夾的名字；副檔名不改。")
    rn.add_argument("target", help="卡編號（f-…）、清冊 uri 或路徑")
    rn.add_argument("new_name", help="新名字（可以不寫副檔名）")
    tr = subs.add_parser("trash", help="丟進資源回收筒", description="把檔或夾丟進資源回收筒（進不了就先留一份進回合備份）；可以一次給多個。")
    tr.add_argument("target", nargs="+", help="卡編號（f-…）、清冊 uri 或路徑，可以多個")
    tr.add_argument("--permanent", action="store_true", help="連備份都放不下、使用者說確定刪時才加")
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


def _targets(args):
    t = args.target
    return list(t) if isinstance(t, (list, tuple)) else [t]


def _emit_refused(e, as_json):
    if as_json:
        out = {"refused": str(e)}
        if isinstance(e, se.Conflict):
            out["conflict"] = e.rows
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(str(e))


def _display(vault, text):
    """結果裡 target 的寫法：存在的絕對路徑換成 uri（在知識庫或登記的來源裡）。"""
    try:
        if os.path.isabs(str(text)) and os.path.exists(text):
            return se.uri_of(vault, sources_lib.load(vault), Path(text)) or str(text)
    except Exception:  # noqa: BLE001
        pass
    return str(text)


def _dest_folder_name(vault, dest, cwd):
    try:
        return se.resolve_folder(vault, sources_lib.load(vault), dest, cwd).name
    except se.Refused:
        return None


def _vault_rel(vault, sources, target, cwd):
    """目標在知識庫底下就回相對路徑（posix），否則 None。"""
    try:
        path = se.resolve(vault, sources, target, cwd)[1]
        return Path(os.path.realpath(str(path))).relative_to(os.path.realpath(str(vault))).as_posix()
    except (se.Refused, ValueError, OSError, TypeError):
        return None


def _move_one(vault, args, op, cwd, target, turn_id, dest=None, to_name=None, replace=False):
    """搬一個東西：回 (結果 dict, 句子)；做不成丟 Refused（Conflict 帶 conflict 列）。--check 回 (含 lines 的 plan, None)。"""
    sources = sources_lib.load(vault)
    if op == "rename":
        plan = se.plan_move(vault, sources, target, cwd, new_name=args.new_name)
    else:
        plan = se.plan_move(vault, sources, target, cwd, dest=dest, to_name=to_name)
    if args.check:
        return dict(_public(plan), lines=se.check_lines(plan, op)), None
    rel_before = _vault_rel(vault, sources, target, cwd)
    got = se.do_move(vault, plan, turn_id, op, replace=replace)
    if rel_before:  # 多人共用 3 第 7 點：搬走的是之前因為太大被排除在 git 外的檔，exclude 那行拿掉（不在 repo 裡是空操作）
        from kb._lib import git
        git.exclude_remove(vault, rel_before)
    sentence = se.move_sentence(plan, got, op)
    res = dict(_public(plan), to_uri=got["to_uri"], cards_moved=got["cards_moved"], replaced=bool(got["replaced"]),
               sentence=sentence, _got=got)
    return res, sentence


def _move(vault, args, as_json, conversation, op):
    cwd = os.getcwd()
    targets = _targets(args)
    if op == "rename":
        targets = targets[:1]
    to_name = getattr(args, "name", None)
    if to_name and (len(targets) != 1 or getattr(args, "contents", False)):
        print("--name 只收一個目標，也不能跟 --contents 一起用")
        return 3
    results, lines, failed = [], [], False
    contents_auto = False
    multi = len(targets) > 1 or getattr(args, "contents", False)
    with FileLock(Path(vault) / sources_lib.SOURCES_REL):
        turn_id = None if args.check else turn.current(vault)["turn"]
        for target in targets:
            use_contents = getattr(args, "contents", False)
            items, src_path = [target], None
            if op == "move":
                try:
                    src_path = se.resolve(vault, sources_lib.load(vault), target, cwd)[1]
                except se.Refused:
                    src_path = None
                if src_path is not None and src_path.is_dir():
                    dest_name = _dest_folder_name(vault, args.to, cwd)
                    if not use_contents and dest_name and dest_name.casefold() == src_path.name.casefold() and not to_name:
                        use_contents, contents_auto = True, True
                    if use_contents:
                        multi = True
                        items = [str(c) for c in sorted(src_path.iterdir(), key=lambda x: x.name.casefold())]
                else:
                    use_contents = False
            for item in items:
                row = {"target": _display(vault, item)}
                try:
                    res, sentence = _move_one(vault, args, op, cwd, item, turn_id, dest=getattr(args, "to", None),
                                              to_name=to_name, replace=getattr(args, "replace", False))
                except se.Conflict as e:
                    row.update(ok=False, refused=str(e), conflict=e.rows)
                    results.append(row)
                    failed = True
                    continue
                except se.Refused as e:
                    row.update(ok=False, refused=str(e))
                    results.append(row)
                    failed = True
                    continue
                if args.check:
                    row.update(ok=True, **res)
                    results.append(row)
                    continue
                got = res.pop("_got")
                row.update(res, ok=True, to=got["to_uri"])
                if got["replaced"] is not None:
                    row["replaced"] = True
                    lines.append(sentence)
                    lines.append(f"原本的「{res['new_name']}」丟進資源回收筒了" + ("，卡拿掉了" if got["replaced"]["card_removed"] else ""))
                else:
                    lines.append(sentence)
                if got.get("original") and got["original"].get("refused"):
                    row["original_left"] = got["original"]["refused"]
                    lines.append(row["original_left"])
                    failed = True
                results.append(row)
                journal_cli.record_program_or_warn(vault, sentence, got["changed"], f"source-edit {op}", conversation)
            if use_contents and src_path is not None and not args.check:
                try:
                    se.remove_empty_folder(vault, sources_lib.load(vault), src_path, turn_id)
                except se.Refused:
                    pass
    return _report(args, as_json, results, lines, failed, multi, contents_auto)


def _report(args, as_json, results, lines, failed, multi, contents_auto=False):
    single = not multi and len(results) == 1
    if args.check:
        flat = [x for r in results for x in (r.get("lines") or [r.get("refused", "")])]
        if as_json:
            print(json.dumps({k: v for k, v in results[0].items() if k != "target"} if single else {"results": results},
                             ensure_ascii=False))
        else:
            print("\n".join(flat))
        return 3 if failed else 0
    if single:
        r = results[0]
        if as_json:
            if r["ok"]:
                print(json.dumps({k: v for k, v in r.items() if k not in ("target", "ok")}, ensure_ascii=False))
            else:
                print(json.dumps({"refused": r["refused"], **({"conflict": r["conflict"]} if r.get("conflict") else {})},
                                 ensure_ascii=False))
        else:
            print("\n".join(lines) if r["ok"] else r["refused"])
        return 3 if failed else 0
    out = {"results": results}
    conflicts = [c for r in results for c in r.get("conflict") or []]
    if conflicts:
        out["conflict"] = conflicts
    if contents_auto:
        out["contents_auto"] = True
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("\n".join(lines + [r["refused"] for r in results if not r["ok"]]))
    return 3 if failed else 0


def _trash(vault, args, as_json, conversation):
    targets = _targets(args)
    results, lines, failed = [], [], False
    multi = len(targets) > 1
    with FileLock(Path(vault) / sources_lib.SOURCES_REL):
        for target in targets:
            sources = sources_lib.load(vault)
            row = {"target": _display(vault, target)}
            try:
                plan = se.plan_trash(vault, sources, target, os.getcwd())
                if args.check:
                    row.update(ok=True, lines=se.trash_lines(plan),
                               **{k: v for k, v in _public(plan).items() if k != "subs"})
                    results.append(row)
                    continue
                got = se.do_trash(vault, plan, turn.current(vault)["turn"], permanent=args.permanent)
            except se.Refused as e:
                row.update(ok=False, refused=str(e))
                results.append(row)
                failed = True
                continue
            sentence = se.trash_sentence(plan, got)
            row.update(ok=True, kept=got["kept"], sentence=sentence, card_removed=got["card_removed"],
                       agenda_cancelled=got["cancelled"])
            if got["permanent"]:
                row["permanent"] = True
            results.append(row)
            lines.append(sentence)
            journal_cli.record_program_or_warn(vault, sentence, got["changed"], "source-edit trash", conversation)
    return _report(args, as_json, results, lines, failed, multi)


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
        _emit_refused(e, as_json)
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
