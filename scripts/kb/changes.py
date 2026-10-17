"""kb changes：跟清冊上次掃描比，列新增、修改、搬動、不見；只讀不寫、不開回合（D-094、specs/2026-10-16-清冊.md 第 6 點）。

預設全比（算指紋不受讀取預算，仍受 fingerprint_max_bytes）；--quick 走開場同一條路。沒有清冊時當全部新增。
每類最多 10 個名字再「……等 N 份」，--all 全列；之前就不見的另一行（審閱裁定 20）。--json 印 Report 去掉 entries。
清冊讀不到第一行「清冊讀不到：<原因>」exit 3；找不到知識庫 exit 3。
"""
import argparse
import json

from kb import inventory_cli
from kb._lib import inventory
from kb._lib import sources as sources_lib

SHOW = 10
HEADER = "跟上次掃描比（還沒套用）："
INCOMPLETE = "（來源很大還沒比完，數字是比到的部分）"
NOTHING = "跟上次掃描時相同，沒有變動。"
FOOTER = "要套用請跑 kb inventory scan。"


def add_arguments(sub):
    sub.add_argument("--quick", action="store_true", help="快速比對（開場用的那條路）")
    sub.add_argument("--all", action="store_true", help="列出全部檔名（預設每類最多 10 個）")
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


def run(args):
    vault = inventory_cli.find_vault(args)
    if vault is None:
        print(inventory_cli.NOT_IN_VAULT)
        return 3
    try:
        inventory.load(vault)
    except ValueError as e:
        print(f"清冊讀不到：{e}")
        return 3
    try:
        report = inventory.scan(vault, sources_lib.load(vault), quick=args.quick)
    except ValueError as e:
        print(f"{sources_lib.SOURCES_REL} 讀不了，請先修好它：{type(e).__name__}: {e}")
        return 3
    if getattr(args, "json", False):
        print(json.dumps(inventory.public(report), ensure_ascii=False))
        return 0
    print("\n".join(lines(report, 10 ** 9 if args.all else SHOW)))
    return 0
