"""kb sync untrack [--check] [--json]（#78 第一段，D-554 (3)）：被同步（git）追蹤、但知識庫的 .gitignore 列了的 willy 自己的檔（.system/、渲染頁，refresh.classify）停止追蹤。
檔留在這台的硬碟上；只 commit 這些路徑（別的已暫存改動不帶），不 push（同步外掛會推）。git 一律經 _lib/git.py，守門不用開例外。
知識庫在自己的 repo 或別人 repo 的子夾都行（實作疑義 1）。正在合併或 rebase 拒絕（exit 3）。總資料夾只從 cwd 往上找。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard
from kb._lib import git, refresh

NOT_A_VAULT = "這裡不是知識庫"
NOTHING = "沒有被同步追蹤的 willy 檔"
BUSY = "同步正在合併，先等它做完"
DONE = "已停止追蹤 {n} 個檔（檔都還在這台）。其他電腦開 willy 時會自己處理，不用先做什麼。"
FAILED = "停止追蹤沒做成，檔都沒動；請服務人員看 hook-errors.log"
PARTIAL = "停止追蹤只做了一半（同步紀錄改好了，這台還記著要同步它們），檔都還在；請服務人員看 hook-errors.log"
MESSAGE = "willy：停止追蹤本機與衍生檔（D-554）"
SHOWN = 10


def add_arguments(sub):
    subs = sub.add_subparsers(dest="sync_command", metavar="<子子命令>", required=True)
    un = subs.add_parser("untrack", help="停止追蹤被同步收進去的 willy 本機與衍生檔",
                         description="被 git 追蹤、但 .gitignore 列了的 willy 檔停止追蹤：檔留在這台，只 commit 這些，不 push。")
    un.add_argument("--check", action="store_true", help="只列不做")
    un.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else "\n".join(lines))


def _close_ask(vault):
    """做成或沒有要處理的：關掉 sync_untrack 待處理（確認 #8；照健檢「已不成立」關，復發會再開）。出錯只記 log。"""
    try:
        from kb._lib import pending, turn
        pending.close_lint_gone(vault, lambda i: i.get("key"), {refresh.UNTRACK_KEY}, (), turn.current(vault)["turn"])
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"kb sync untrack 關待處理失敗：{type(e).__name__}: {str(e)[:100]}", source="sync")


def run(args):
    as_json = getattr(args, "json", False)
    found = guard.find_vault(os.getcwd())
    if found is None:
        _out(as_json, [NOT_A_VAULT], {"ok": False, "message": NOT_A_VAULT})
        return 3
    vault = Path(os.path.realpath(str(found)))
    got = refresh.untrack_summary(vault)
    base = {k: got[k] for k in ("n", "local", "copies", "rendered", "paths")}
    if not got["n"]:
        _close_ask(vault)
        _out(as_json, [NOTHING], dict(base, ok=True, done=False, message=NOTHING))
        return 0
    if args.check:
        lines = [refresh.check_line(got)] + [f"- {p}" for p in got["paths"][:SHOWN]]
        if got["n"] > SHOWN:
            lines.append(f"…還有 {got['n'] - SHOWN} 個")
        _out(as_json, lines, dict(base, ok=True, done=False, message=lines[0]))
        return 0
    if git.busy(vault):
        _out(as_json, [BUSY], dict(base, ok=False, done=False, message=BUSY))
        return 3
    done = git.untrack_commit(vault, got["paths"], MESSAGE)
    if done == "nothing":
        _close_ask(vault)
        _out(as_json, [NOTHING], dict(base, ok=True, done=False, message=NOTHING))
        return 0
    if done == "partial":
        from kb._lib import ledger
        ledger.log_error(vault, f"kb sync untrack：commit 做好了，這台的 index 沒拿掉（{got['n']} 個檔）", source="sync")
        _out(as_json, [PARTIAL], dict(base, ok=False, done=True, message=PARTIAL))
        return 3
    if done != "ok":
        from kb._lib import ledger
        ledger.log_error(vault, f"kb sync untrack 失敗（{got['n']} 個檔）", source="sync")
        _out(as_json, [FAILED], dict(base, ok=False, done=False, message=FAILED))
        return 3
    _close_ask(vault)
    msg = DONE.format(n=got["n"])
    _out(as_json, [msg], dict(base, ok=True, done=True, message=msg))
    return 0
