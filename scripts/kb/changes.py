"""kb changes：跟清冊上次掃描比，列新增、修改、搬動、不見；只讀不寫、不開回合（D-094、specs/2026-10-16-清冊.md 第 6 點）。

預設全比（算指紋不受讀取預算，仍受 fingerprint_max_bytes）；--quick 走開場同一條路。沒有清冊時當全部新增。
每類最多 10 個名字再「……等 N 份」，--all 全列；之前就不見的另一行（審閱裁定 20）。--json 印 Report 去掉 entries。
--moved 只列搬動（整夾搬算一件，cards.moved_changes），--json 時印 {moved, incomplete}（放寬丙 5-2）。
清冊讀不到第一行「清冊讀不到：<原因>」exit 3；找不到知識庫 exit 3。
背景比對（D-536）：比完寫清單 .system/local/changes.json（changes_list；--quick 沒比完不寫）。--background 是電腦排程跑的：
全比、不設時間上限、另算製作中、卡的最新修改時間與手改過的卡（card_base.edits，補基準）；不印、任何例外寫 hook-errors.log（source scan）、exit 0；另一個在跑就結束。
"""
import argparse
import json
import os
import time
from pathlib import Path

from kb import inventory_cli
from kb._lib import changes_list, inventory, ledger
from kb._lib import sources as sources_lib

SHOW = 10
HEADER = "跟上次掃描比（還沒套用）："
INCOMPLETE = "（來源很大還沒比完，數字是比到的部分）"
NOTHING = "跟上次掃描時相同，沒有變動。"
FOOTER = "要套用請跑 kb inventory scan。"


def add_arguments(sub):
    sub.add_argument("--quick", action="store_true", help="快速比對（開場用的那條路）")
    sub.add_argument("--all", action="store_true", help="列出全部檔名（預設每類最多 10 個）")
    sub.add_argument("--moved", action="store_true", help="只列認出的搬動（整夾搬算一件，帶卡上的實體），給 AI 判斷要不要改掛")
    sub.add_argument("--background", action="store_true", help="電腦排程用：比完寫清單、不印（AI 不用跑）")
    sub.add_argument("--task", help=argparse.SUPPRESS)  # 排程自己的名稱：--background 找不到庫時刪掉它（裁定 12a）
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _names(items, show):
    shown = "、".join(items[:show])
    return shown + (f"……等 {len(items)} 份" if len(items) > show else "")


def lines(report, show=SHOW):
    name = inventory_cli.name_of
    kinds = (
        ("新增", [name(u) for u in report["added"]]),
        ("修改", [name(u) for u in report["modified"]]),
        ("搬動或改名", [f"{name(a)} → {name(b)}" for a, b in report["moved"]]),
        ("不見", [name(u) for u in report["missing"]]),
        ("之前就不見、還沒處理", [name(u) for u in report["still_missing"]]),
    )
    body = [f"- {label} {len(items)} 份：{_names(items, show)}" for label, items in kinds if items]
    if report["missing_sources"]:
        body.append(f"- 這台電腦找不到來源：{inventory_cli.missing_text(report['missing_sources'])}")
    if not body and not report["incomplete"]:
        return [NOTHING]
    # 只剩之前就不見、找不到的來源時套用也不會變，不叫人去跑 scan（審閱裁定 39）
    applicable = any(report[k] for k in ("added", "modified", "moved", "missing")) or report["incomplete"]
    return [HEADER] + ([INCOMPLETE] if report["incomplete"] else []) + body + ([FOOTER] if applicable else [])


def _moved(vault, report, as_json):
    """--moved：認出的搬動（放寬丙 5-2）。程式不判斷算不算換分類，資料給 AI 判斷。"""
    from kb._lib import cards
    items = cards.moved_changes(vault, report["moved"])
    if as_json:
        print(json.dumps({"moved": items, "incomplete": report["incomplete"]}, ensure_ascii=False))
        return 0
    if not items:
        print("沒有認出搬動。")
        return 0
    for i in items:
        extra = f"（{i['count']} 份）" if i["kind"] == "folder" else ""
        print(f"- {inventory_cli.name_of(i['from'])} → {inventory_cli.name_of(i['to'])}{extra}")
    return 0


def _orphan(args):
    """找不到庫（刪了、搬了、不是庫了）：照 --task 刪掉排程自己（裁定 12、12a），記到系統暫存夾的 log（庫不在）。"""
    from kb._lib import schedule
    task = getattr(args, "task", None)
    gone = schedule.forget(task)
    ledger.log_error(None, f"背景比對找不到知識庫 {args.vault}，{'刪了排程 ' + task if gone else '排程沒刪'}", source="scan")


def _background(args):
    """排程跑的：不印、exit 0；例外記 hook-errors.log（source scan）。"""
    vault = None
    try:
        vault = inventory_cli.find_vault(args)
        # --vault 本身要是庫：往上找到外層的庫不算（那是別的庫）
        if vault is None or (args.vault and Path(os.path.realpath(args.vault)) != vault):
            vault = None
            _orphan(args)
            return 0
        if not changes_list.acquire(vault):
            return 0
        try:
            from datetime import datetime
            from kb._lib import card_base, cards
            start, began = time.monotonic(), datetime.now().isoformat(timespec="seconds")
            sources = sources_lib.load(vault)
            report = inventory.scan(vault, sources)
            wip = changes_list.wip_summary(cards.wip(vault, sources))
            card_edits = [e["path"] for e in card_base.edits(vault)["edits"]]  # 不設時限、順便補基準（裁定 19）
            stamp = changes_list.card_base_stamp(vault)  # 補完基準之後的版本：每句開頭看它變了沒（裁定 21）
            if not changes_list.newer_than(vault, began):  # 比的期間 AI 當場比過或套用了：這份已經舊了，等下一次
                changes_list.write(vault, report, "background", time.monotonic() - start, wip, changes_list.cards_mtime(vault),
                                   card_edits, stamp)
        finally:
            changes_list.release(vault)
    except Exception as e:  # noqa: BLE001  背景跑的，沒人看得到輸出
        ledger.log_error(vault, f"背景比對沒跑完：{type(e).__name__}: {str(e)[:200]}", source="scan")
    return 0


def _save(vault, report, seconds):
    """AI 當場比的也更新清單（沒比完不寫）；寫不了只記 log，不擋這個指令。"""
    if report["incomplete"]:
        return
    try:
        changes_list.write_carried(vault, report, "changes", seconds)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"清單沒寫成：{type(e).__name__}: {e}", source="scan")


def run(args):
    if getattr(args, "background", False):
        return _background(args)
    vault = inventory_cli.find_vault(args)
    if vault is None:
        print(inventory_cli.NOT_IN_VAULT)
        return 3
    try:
        inventory.load(vault)
    except ValueError as e:
        print(f"清冊讀不到：{e}")
        return 3
    start = time.monotonic()
    try:
        report = inventory.scan(vault, sources_lib.load(vault), quick=args.quick)
    except ValueError as e:
        print(f"{sources_lib.SOURCES_REL} 讀不了，請先修好它：{type(e).__name__}: {e}")
        return 3
    _save(vault, report, time.monotonic() - start)
    if getattr(args, "moved", False):
        return _moved(vault, report, getattr(args, "json", False))
    if getattr(args, "json", False):
        print(json.dumps(inventory.public(report), ensure_ascii=False))
        return 0
    print("\n".join(lines(report, 10 ** 9 if args.all else SHOW)))
    return 0
