"""kb moved [--check] [--json]（備份還原與換電腦 要做的事 5，D-206、D-208）：知識庫換了位置或換了電腦，在新位置補好。

開場看到「換了位置」「這台電腦第一次開」就跑這個：補相對位置的來源 spec、重寫卡的開原檔、列出這台找不到的來源、
舊位置還有一份的提醒（不代刪）、換電腦時列要補的元件與要重記的密碼。--check 只列不動；沒有要補的只記位置。
改檔前備進回合（退回救得回）；對話裡叫的不用先問、併進回合，終端機跑的記程式段（D-085）。人讀一行一件，AI 照著逐件處理。
總資料夾只從 cwd 往上找（不收 --vault）。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import io as kio
from kb._lib import moved, turn

NOT_A_VAULT = "這裡不是知識庫"
LOCK_BUSY = "等不到鎖，沒有寫入"
CLI_LOCK_TIMEOUT = 30


def add_arguments(sub):
    sub.add_argument("--check", action="store_true", help="只列要補什麼，不動任何東西")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, line, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else line)


def lines(got, applied):
    """人讀：一行一件。applied 是做完了（--check 用「會」）。"""
    st = got["state"]
    if st == "same":
        return ["位置沒變，沒有要補的"]
    if st == "first":
        return ["第一次看這個知識庫，位置記下了" if applied else "第一次看這個知識庫，位置還沒記（不加 --check 會記下）"]
    out = []
    if st == "moved":
        out.append(f"知識庫換了位置：{got['from']} → {got['to']}")
    else:
        out.append("這台電腦第一次開這個知識庫" + (f"（上次在 {got['from']}）" if got.get("from") else ""))
    did = "換成新的了" if applied else "會換成新的"
    for alias in got["sources_rewritten"]:
        out.append(f"來源「{alias}」的位置{did}")
    if got["cards_relinked"]:
        out.append(f"卡的開原檔連結{'更新了' if applied else '會更新'} {got['cards_relinked']} 張")
    if got["missing_sources"]:
        out.append("這台找不到的來源：" + "、".join(got["missing_sources"])
                   + "；請她一個一個說新位置，我用 kb folders relocate 接上")
    if got["old_copy"]:
        out.append(f"舊位置還有一份：{got['old_copy']}；新的確認沒問題後，問使用者一句要不要刪舊的，說好就 kb source-edit trash {got['old_copy']}。")
    for item in got["env"]:
        out.append(item["text"] + (f"（{item['fix']}）" if item.get("fix") else ""))
    if got["passwords_missing"]:
        out.append("這台電腦的憑證管理員沒有這些密碼，要重新記：" + "、".join(got["passwords_missing"]))
    if got.get("obsidian_reopen"):
        out.append("Obsidian 要重新打開：kb obsidian open")  # 新位置 Obsidian 還沒登記（#22）
    if len(out) == 1:
        out.append("沒有要補的")
    return out


def run(args):
    as_json = getattr(args, "json", False)
    found = guard.find_vault(os.getcwd())
    if found is None:
        _out(as_json, NOT_A_VAULT, {"ok": False, "message": NOT_A_VAULT})
        return 3
    vault = Path(os.path.realpath(str(found)))
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    try:
        if args.check:
            got = moved.check(vault)
            _out(as_json, "\n".join(lines(got, False)), got)
            return 0
        conversation = journal_cli.in_conversation(vault)
        got = moved.apply(vault, turn.current(vault)["turn"])
    except TimeoutError:
        _out(as_json, LOCK_BUSY, {"ok": False, "message": LOCK_BUSY})
        return 3
    except (ValueError, OSError) as e:
        line = f"狀態檔讀不到或寫不進去：{e}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
    if got["state"] in ("moved", "new_machine"):
        note = (f"知識庫換了位置，補好了：來源 {len(got['sources_rewritten'])} 個、卡 {got['cards_relinked']} 張"
                if got["state"] == "moved" else
                f"這台電腦第一次開這個知識庫，補好了：來源 {len(got['sources_rewritten'])} 個、卡 {got['cards_relinked']} 張")
        journal_cli.record_program_or_warn(vault, note, got.get("changed", []), "moved", conversation)
    payload = {k: v for k, v in got.items() if k != "changed"}
    _out(as_json, "\n".join(lines(got, True)), dict(payload, ok=True))
    return 0
