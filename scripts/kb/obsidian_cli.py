"""kb obsidian status／open [--json]（Obsidian 首頁，#22，D-259）：Obsidian 裝了沒、這個知識庫登記了沒、外掛放了沒；open 替她把知識庫加進 Obsidian 並打開。

open：沒裝 Obsidian 說去哪下載（不替她裝）；已登記的直接 obsidian://open?path=<知識庫> 打開；沒登記的，
Obsidian 沒在跑就先把知識庫寫進 %APPDATA%/obsidian/obsidian.json（id 16 碼 hex、ts 現在）再打開，在跑就說「先關掉 Obsidian 再說一次」exit 3
（它在跑時會把自己的設定寫回去，我們寫的會被蓋掉）。外掛檔在檢查、登記都過了才放（同 kb init；放不成只記一句、不擋打開）。第一次打開 Obsidian 會問要不要信任這個資料夾的外掛，請她按「信任」。
終端機跑的記一段工作日誌程式段，對話裡叫的併進回合。

上層 vault（#66 丙，D-542）：知識庫在她原本的 Obsidian 資料夾裡、Obsidian 已登記那個資料夾（知識庫自己沒登記）→ status 多回 outer，
open 放兩份外掛、打開上層，不登記知識庫（不寫 obsidian.json，所以 Obsidian 在跑也不擋）。上層沒登記照舊。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import obsidian
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NOT_INSTALLED = "這台電腦還沒裝 Obsidian：請到 obsidian.md 下載安裝，裝好再說一次"
CLOSE_FIRST = "先關掉 Obsidian 再說一次（它開著的時候我沒辦法把這個知識庫加進去）"
TRUST_NOTE = "第一次打開會問要不要信任這個資料夾的外掛，請按「信任」"
OUTER_LINE = "這個知識庫在你原本的 Obsidian 資料夾（{name}）裡面，會從那裡打開"
OUTER_OPENED = "已打開 Obsidian（你原本的資料夾 {name}），按左邊的首頁按鈕就看得到。"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="obsidian_command", metavar="<子子命令>", required=True)
    st = subs.add_parser("status", help="Obsidian 裝了沒、知識庫登記了沒、外掛放了沒", description="只讀，三項各一行。")
    op = subs.add_parser("open", help="把知識庫加進 Obsidian 並打開", description="替她登記並打開知識庫，不用她自己挑資料夾。")
    for p in (st, op):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _yes(flag, yes, no):
    return yes if flag else no


def _status(vault, as_json):
    got = obsidian.status(vault)
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
    else:
        print(_yes(got["installed"], "Obsidian：這台電腦有裝", "Obsidian：這台電腦還沒裝"))
        print(_yes(got["registered"], "這個知識庫：Obsidian 已經登記", "這個知識庫：Obsidian 還沒登記"))
        print(_yes(got["plugin"], "首頁外掛：已經放好", "首頁外掛：還沒放好（kb init 或 kb obsidian open 會放）"))
        if got["outer"]:
            print(OUTER_LINE.format(name=got["outer"]["name"]))
    return 0


def _open(vault, as_json, conversation):
    if not obsidian.installed():
        print(NOT_INSTALLED)
        return 3
    outer = obsidian.outer_registered(vault)  # 上層 vault 已登記：開上層，不登記知識庫（#66 丙）
    registered = outer is not None or obsidian.own_registered(vault)
    added = False
    if not registered:  # 先檢查、先登記，擋下來的時候知識庫裡不留外掛檔；外掛檔最後才放
        if obsidian._running():
            print(CLOSE_FIRST)
            return 3
        try:
            obsidian.register(vault)
        except (ValueError, OSError) as e:
            print(f"沒辦法把知識庫加進 Obsidian：{e}")
            return 3
        added = True
    wrote, notes = [], []
    try:
        wrote, notes = obsidian.place(vault)
    except OSError as e:  # 同 kb init：外掛沒放成不擋打開
        notes = [f"Obsidian 外掛沒放成：{e}"]
    if outer is not None:
        notes += obsidian.place_outer(vault)[1]  # 上層那份不進回合、不列在 wrote（庫外的檔）
    changed = [{"path": rel, "action": "modify"} for rel in wrote]
    try:
        obsidian._startfile(obsidian.open_url(outer[0] if outer is not None else vault))
    except OSError as e:
        print(f"沒辦法打開 Obsidian：{e}")
        return 3
    if outer is not None:
        line = OUTER_OPENED.format(name=outer[0].name) + TRUST_NOTE
    else:
        line = ("已把知識庫加進 Obsidian 並打開。" if added else "已打開 Obsidian。") + TRUST_NOTE
    if wrote or added:
        journal_cli.record_program_or_warn(vault, "替她打開 Obsidian" + ("（先把知識庫加進去）" if added else ""), changed,
                                           "obsidian open", conversation)
    if as_json:
        print(json.dumps({"opened": True, "registered_now": added, "wrote": wrote, "notes": notes,
                          "outer": outer[0].name if outer is not None else None}, ensure_ascii=False))
    else:
        print("\n".join([line] + notes))
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    as_json = getattr(args, "json", False)
    if args.obsidian_command == "status":
        return _status(vault, as_json)
    return _open(vault, as_json, journal_cli.in_conversation(vault))
