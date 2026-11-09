"""kb upgrade [--check|--finish|--downgrade|--background] [--json]、kb upgrade skeleton <相對路徑> --keep|--replace
（升級打包 要做的事 4，D-215～D-219）：升級與新對話開場的轉格式；本體在 _lib/upgrade。

--check 當場查（連網、不寫任何東西，連不上也 exit 0）；直接跑是升級（大升級先拍快照再叫內建 Claude Code 換版，小修直接換）；
--downgrade 退回（先 --check 看升級前的快照與 restore check 的結果，客戶說好才跑）；--finish 新對話開場叫，補骨架、遷移、記歷史；
--background 開場開的背景子行程，查新版與套小修，不印任何東西、永遠 exit 0；skeleton 回答她「骨架檔保留還是換新」。
換版之前先在這台電腦驗新版（D-230）：直接跑與背景小修都驗，驗不過不換；直接跑回「新版 X 在這台電腦跑不起來（…），先不換；什麼都沒動」
exit 3；--check 在背景驗不過的那一版就是清單現在那一版時多一行，--json 多 verify_failed。--downgrade 不驗。
成功句與拒絕句一字不差（exit 3）。不收 --vault，總資料夾只從 cwd 往上找（跟 convert 一樣）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的不記（AI 用 kb turn note）。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import backup, skeleton, turn, upgrade
from kb._lib import io as kio

NOT_A_VAULT = backup.NOT_A_VAULT
ONE_MODE = "一次只能用一個：--check、--finish、--downgrade、--background，或 skeleton"
LOCK_TIMEOUT = 30
ALREADY = "已經是 {version}"
FIRST_UP = "已升到 {to}"
FIRST_PATCH = "已自動套用小修正 {to}"
KEPT = "好了，保留你改的 {rel}。"
REPLACED = "好了，{rel} 換成新版了；你改的那份備在這一回合裡，說「退回」就回來。"
ASK_LINE = ("要問她：{rel} 她改過、新版也改了；保留她的、換成新的、或兩個都看？"
            "她說好了跑 kb upgrade skeleton \"{rel}\" --keep（保留）或 --replace（換新，她的那份備起來）")


def add_arguments(sub):
    sub.add_argument("--check", action="store_true", help="當場查有沒有新版（連網；什麼都不寫）")
    sub.add_argument("--finish", action="store_true", help="新對話開場：換了版之後補骨架、遷移狀態檔、記歷史")
    sub.add_argument("--downgrade", action="store_true", help="退回：先還原升級前的快照再換回舊版（要先 --check、她說好）")
    sub.add_argument("--background", action="store_true", help="開場背景用：查新版、套小修，不印任何東西")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    subs = sub.add_subparsers(dest="upgrade_command", metavar="<子子命令>", required=False)
    sk = subs.add_parser("skeleton", help="骨架檔她改過、新版也改了：保留或換新",
                         description="--keep 保留她那份（記下，新版這個檔下次又改了才會再問）；--replace 換成新版（她那份備進回合）。")
    sk.add_argument("rel", help="骨架檔的相對路徑，例：CLAUDE.md")
    group = sk.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep", action="store_true")
    group.add_argument("--replace", action="store_true")
    sk.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, line, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else line)


def _finish_lines(got):
    """人讀：第一行「已升到 X：一句話」（小修「已自動套用小修正 X：…」），之後一行一件，要問的放最後。"""
    head = (FIRST_PATCH if got["kind"] == "patch" else FIRST_UP).format(to=got["to"])
    lines = [head + (f"：{got['note']}" if got["note"] else "")]
    lines += [f"轉成新格式：{rel}" for rel in got["migrated"]]
    lines += list(got.get("migrate_notes") or [])  # 型別夾補上編號：…（型別夾編號，實作疑義裁定 1）
    if got["cards_migrated"]:
        lines.append(f"卡的檔頭跟上了 {got['cards_migrated']} 張")
    lines += [f"補上骨架：{rel}" for rel in got["skeleton_created"]]
    lines += [f"骨架換成新版：{rel}" for rel in got["skeleton_replaced"]]
    lines += [f"沒轉成：{s['rel']}（{s['why']}）" for s in got["migrate_skipped"]]
    lines += [ASK_LINE.format(rel=rel) for rel in got["skeleton_ask"]]
    return lines


def _finish(vault, as_json, conversation):
    got = upgrade.finish(vault, turn.current(vault)["turn"])
    if got["already"]:
        _out(as_json, ALREADY.format(version=got["to"]), dict(got, ok=True, message=ALREADY.format(version=got["to"])))
        return 0
    lines = _finish_lines(got)
    changed = ([{"path": rel, "action": "modify"} for rel in got["migrated"] + got["skeleton_replaced"]]
               + [{"path": rel, "action": "create"} for rel in got["skeleton_created"]])
    journal_cli.record_program_or_warn(vault, lines[0], changed, "upgrade --finish", conversation)
    _out(as_json, "\n".join(lines), dict(got, ok=True, message=lines[0]))
    return 0


def _skeleton(vault, args, as_json, conversation):
    if args.keep:
        skeleton.keep(vault, args.rel)
        line = KEPT.format(rel=args.rel)
    else:
        skeleton.replace(vault, args.rel, turn.current(vault)["turn"])
        line = REPLACED.format(rel=args.rel)
        journal_cli.record_program_or_warn(vault, f"骨架檔 {args.rel} 換成新版", [{"path": args.rel, "action": "modify"}],
                                           "upgrade skeleton", conversation)
    _out(as_json, line, {"ok": True, "message": line})
    return 0


def run(args):
    as_json = getattr(args, "json", False)
    modes = [m for m in ("check", "finish", "downgrade", "background") if getattr(args, m)]
    if args.upgrade_command:
        modes.append("skeleton")
    found = guard.find_vault(os.getcwd())
    if found is None:
        if args.background:  # 開場子行程在知識庫外起來也不出聲
            return 0
        _out(as_json, NOT_A_VAULT, {"ok": False, "message": NOT_A_VAULT})
        return 3
    if len(modes) > 1:
        _out(as_json, ONE_MODE, {"ok": False, "message": ONE_MODE})
        return 3
    vault = Path(os.path.realpath(str(found)))
    kio.LOCK_TIMEOUT = LOCK_TIMEOUT
    if args.background:
        try:
            upgrade.background(vault)
        except BaseException:  # noqa: BLE001  背景程式永遠不出聲、不出錯（D-219）
            pass
        return 0
    try:
        if args.check:
            res = upgrade.check(vault)
            _out(as_json, "\n".join(upgrade.check_lines(res)), res)
            return 0
        conversation = journal_cli.in_conversation(vault)
        if args.finish:
            return _finish(vault, as_json, conversation)
        if args.upgrade_command == "skeleton":
            return _skeleton(vault, args, as_json, conversation)
        got = upgrade.downgrade(vault, conversation) if args.downgrade else upgrade.upgrade(vault)
    except (backup.Refused, skeleton.Refused) as e:
        _out(as_json, str(e), {"ok": False, "message": str(e)})
        return 3
    except TimeoutError:
        line = "等不到鎖，沒有寫入"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
    except (ValueError, OSError) as e:
        line = f"狀態檔讀不到或寫不進去：{e}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
    journal_cli.record_program_or_warn(vault, got["sentence"].rstrip("。"), [],
                                       "upgrade --downgrade" if args.downgrade else "upgrade", conversation)
    _out(as_json, got["sentence"], dict(got, ok=True, message=got["sentence"]))
    return 0
