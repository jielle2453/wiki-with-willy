"""kb find "<字>" [--json]（Obsidian 智慧搜尋，#22，D-257）：把一串字拆成實體、卡型、標籤、時間詞，剩下的字比卡標題與內文；
只讀卡與綜合卡，不讀原檔。人讀一行一筆（標題、卡型、日期、原檔所在的夾），給終端機與對話用；--json 給外掛（格式見 spec 第 2 節）。
"""
import argparse
import json

from kb._lib import find
from kb.lookup_cli import wip_lines
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NOTHING = "沒有找到"


def add_arguments(sub):
    sub.add_argument("query", help="要找的字，例：王先生 報價 上個月")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _where(item):
    target = item.get("open") or {}
    if target.get("url"):
        return target["url"]
    path = str(target.get("path") or "").replace("\\", "/")
    return path.rpartition("/")[0].rpartition("/")[2] if path else ""


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    try:
        out = find.find(vault, args.query)
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"讀不到：{type(e).__name__}: {e}")
        return 3
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if out["parsed"]:
        print("照這樣找：" + "、".join(p["label"] for p in out["parsed"]))
    if out["entities"]:
        print("實體：" + "、".join(e["name"] for e in out["entities"]))
    for f in out["files"]:
        where = _where(f)
        print(f"{f['title']}（{f['type']}，{f['date']}）" + (f" — {where}" if where else ""))
    if not out["files"] and not out["entities"]:
        print(NOTHING)
    elif out["more"]:
        print(f"還有 {out['total'] - len(out['files'])} 份沒列出來，加幾個字縮小範圍")
    for line in wip_lines(out):  # 0 筆時附製作中的檔（#37）
        print(line)
    return 0
