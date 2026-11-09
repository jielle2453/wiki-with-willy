"""kb restore list／check／apply（備份還原與換電腦 要做的事 5，D-205）：還原由 AI 先演練（check）、客戶說好才動手（apply）。

list [--from <夾>]：這台電腦記的備份夾與 --from 裡合格的 zip，加上知識庫裡的快照；新的在前，標出不能用的與原因。
check <zip 路徑或快照名>：只讀；比現在與那份，會不見的與會回來的；擋住時第一行是原因、exit 3。
apply <zip 路徑或快照名>：先拍快照、解開驗過、換進去；任何一步失敗全部移回、什麼都沒動。「剛剛的還原不要了」= apply 最新的快照。
沒有知識庫的 cwd 也能跑：cwd 是空資料夾就在那裡還原（開場那時沒有知識庫，桌面版這次會跳「允許嗎」）。拒絕句一字不差（exit 3）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的不記。
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from kb import journal_cli
from kb._lib import backup, clean, restore

EMPTY_LIST = "沒有找到備份；備份放哪跟我說，或用 --from 指定備份夾"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="restore_command", metavar="<子子命令>", required=True)
    ls = subs.add_parser("list", help="列備份與快照", description="列備份與快照，新的在前。")
    ls.add_argument("--from", dest="from_dir", help="另外看這個備份夾（空資料夾還原、備份在隨身碟時用）")
    ck = subs.add_parser("check", help="演練：會回到什麼樣子、什麼會不見", description="只讀，不動任何東西。")
    ck.add_argument("source", help="zip 路徑，或知識庫裡的快照名")
    ap = subs.add_parser("apply", help="還原（先拍快照）", description="先拍快照、解開驗過、換進去；失敗全部移回。")
    ap.add_argument("source", help="zip 路徑，或知識庫裡的快照名")
    for p in (ls, ck, ap):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, line, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else line)


def _mmdd(created):
    try:
        return datetime.strptime(str(created), "%Y-%m-%dT%H:%M:%S").strftime("%m-%d %H:%M")
    except ValueError:
        return "不知道哪天"


KIND_WORD = {"backup": "備份", "snapshot": "快照"}


def not_finished_line(step):
    """換入成功之後某一步沒做完：「還原好了，但有一步沒做完：<哪一步>（原因），<怎麼補>」（第三輪 N-7）。"""
    return f"還原好了，但有一步沒做完：{step}"


def _note_place(vault):
    """這題的指令第一次跑時記位置、補 vault.json（測試 T-2）；寫不了不擋指令。"""
    from kb._lib import moved
    try:
        moved.note_place(vault)
    except (OSError, ValueError):
        pass


def _list_line(g):
    who = f"{g['machine']} 做的" if g.get("machine") else "不知道哪台做的"
    line = f"{_mmdd(g['created'])}｜{KIND_WORD.get(g['kind'], g['kind'])}｜{who}｜{clean.mb_text(g['bytes'])}"
    if not g["usable"]:
        line += f"｜不能用：{g['why']}"
    return line


def _list(cwd, args, as_json):
    got = restore.list_for(cwd, args.from_dir)
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
    else:
        print("\n".join(_list_line(g) for g in got) if got else EMPTY_LIST)
    return 0


def _check(cwd, args, as_json):
    got = restore.check(cwd, args.source)
    _out(as_json, "\n".join(restore.lines(got)), got)
    return 3 if got["refuse"] else 0


def _apply(cwd, args, as_json):
    vault, _target = restore.target_of(cwd)
    conversation = journal_cli.in_conversation(vault) if vault is not None else False
    got = restore.apply(cwd, args.source, conversation)
    if vault is not None:
        _note_place(vault)  # 成功才記（拒絕時什麼都不動）
    lines = [got["sentence"]]
    for name in got["hidden_reapplied"]:
        lines.append(f"你設成不想讓 AI 看到的「{name}」留著、也再清了一次"
                     + ("" if got["snapshot"] else "（還原前的快照也一起清掉了）"))
    if got["hidden_left"]:
        lines.append(f"不想讓 AI 看到的夾還沒清完：{got['hidden_left']}")
    for step in got.get("not_finished") or []:
        lines.append(not_finished_line(step))  # 換入成功之後才出錯的：照實說還原好了、哪一步沒做完（N-3）
    if got["build_step"]:
        lines.append(f"建置停在「{got['build_step']}」那一步，下次打 build 從那裡接著做")
    _out(as_json, "\n".join(lines), dict(got, ok=True, message=got["sentence"]))
    return 0


HANDLERS = {"list": _list, "check": _check, "apply": _apply}


def run(args):
    as_json = getattr(args, "json", False)
    cwd = Path(os.getcwd())
    try:
        return HANDLERS[args.restore_command](cwd, args, as_json)
    except backup.Refused as e:
        _out(as_json, str(e), {"ok": False, "message": str(e)})
        return 3
    except (ValueError, OSError) as e:
        line = f"還原沒有做成：{type(e).__name__}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
