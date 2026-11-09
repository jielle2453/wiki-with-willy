"""kb lint [--background] [--json]、kb lint handedit [--json] [--done <編號>]（lint 題，D-220～D-227）。

kb lint（她說「幫我檢查」）：跑全部檢查、自己修、要她決定的進待處理、寫這個月到今天的報告（同月重寫）；人讀第一行一句話，
接「替你做了：…」一行一件、「要你決定的 N 件（最急：…），說『看待處理』」、整理擱著的有就一行「整理還有：…」。
--background（開場背景子行程）：同樣檢查與修、本月第一次跑時寫上個月的定稿報告；不印、永遠 exit 0（出錯只記 log）。
handedit：列她手改程式產生的 md 的差異（待處理 handedit 那幾件）：checked（打勾的條目）、removed（刪掉的）、changed_date（改日期的）、
other（看不出對到哪條的），條目帶各自的編號（待處理 p-、日程 a-、裁示 r-）；AI 照意思用既有指令做，other 才問她，做完 --done <編號>。
人讀不印編號。拒絕與做不到 exit 3：另一個健檢在跑「另一個健檢正在跑，等它做完」；--done 找不到「沒有這件待處理」。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import io as kio
from kb._lib import ledger, pending, turn
from kb.inventory_cli import NOT_IN_VAULT, find_vault
from kb.lint import runner

CLI_LOCK_TIMEOUT = 30
DONE_LINE = "記下了：照你改的做完了"
DONE_HOW = "照她改的做完了"
NO_HANDEDIT = "沒有你手改過還沒處理的檔"
DIFF_KINDS = (("checked", "打勾 {n} 條"), ("removed", "刪掉 {n} 條"), ("changed_date", "改日期 {n} 條"), ("other", "其他 {n} 行"))
INGEST_TEXT = (("unsorted", "待整理 {n} 份"), ("unlinked", "沒掛實體 {n} 張"), ("synthesis_stale", "綜合卡待更新 {n} 張"),
               ("duplicates", "同一份檔在兩處 {n} 份"))  # 整理做得了的才講；讀不到、寫法不同這類在待處理（桌面版確認後）


def add_arguments(sub):
    sub.add_argument("action", nargs="?", choices=["handedit"], help="handedit：列你手改程式產生的檔的差異")
    sub.add_argument("--background", action="store_true", help="開場背景跑（不印、不寫對話）")
    sub.add_argument("--done", help="handedit：這件照她改的做完了（待處理編號 p-…）")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _lines(got):
    lines = [got["summary"]]
    lines += [f"替你做了：{t}" for t in got["done"]]
    if got["decide"]:
        urgent = f"（最急：{got['most_urgent']}）" if got.get("most_urgent") else ""
        lines.append(f"要你決定的 {got['decide']} 件{urgent}，說『看待處理』")
    left = "、".join(t.format(n=got["ingest"][k]) for k, t in INGEST_TEXT if got["ingest"].get(k))
    if left:
        lines.append(f"整理還有：{left}")
    return lines


def _check(vault, as_json, background):
    try:
        got = runner.run(vault, background=background)
    except runner.Busy as e:
        if background:
            return 0
        print(str(e))
        return 3
    if background:
        return 0
    out = {k: got[k] for k in ("month", "report", "summary", "done", "decide", "most_urgent", "set_aside", "ingest", "failed_checks")}
    print(json.dumps(out, ensure_ascii=False) if as_json else "\n".join(_lines(got)))
    return 0


def _handedits(vault, as_json):
    items = []
    for i in pending.load(vault)["items"]:
        if i.get("kind") != "handedit" or i.get("status") != "open":
            continue
        data = i.get("data") or {}
        diff = data.get("diff") or {}
        items.append({"pending": i["id"], "file": data.get("file"), "saved": data.get("saved"),
                      **{k: diff.get(k) or [] for k, _t in DIFF_KINDS}})
    if as_json:
        print(json.dumps({"items": items}, ensure_ascii=False))
        return 0
    if not items:
        print(NO_HANDEDIT)
        return 0
    for it in items:
        parts = "、".join(t.format(n=len(it[k])) for k, t in DIFF_KINDS if it[k])
        print(f"你改過「{it['file']}」：{parts or '看不出改了什麼'}")
    return 0


def _done(vault, pid, as_json):
    item = next((i for i in pending.load(vault)["items"] if i.get("id") == pid and i.get("kind") == "handedit"
                 and i.get("status") == "open"), None)
    if item is None:
        print(pending.NOT_FOUND)
        return 3
    conversation = journal_cli.in_conversation(vault)
    pending.resolve(vault, pid, DONE_HOW, turn.current(vault)["turn"])
    changed = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
    journal_cli.record_program_or_warn(vault, f"照她手改的做完了：{(item.get('data') or {}).get('file')}", changed,
                                       "lint handedit --done", conversation)
    print(json.dumps({"pending": pid, "done": True}, ensure_ascii=False) if as_json else DONE_LINE)
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        if args.background:
            return 0
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        if args.action == "handedit":
            return _done(vault, args.done.strip(), as_json) if args.done else _handedits(vault, as_json)
        return _check(vault, as_json, args.background)
    except Exception as e:  # noqa: BLE001  背景健檢永遠 exit 0；對話裡一句白話
        ledger.log_error(vault, f"kb lint 出錯：{type(e).__name__}: {e}", source="lint")
        if args.background:
            return 0
        if isinstance(e, (ValueError, OSError)):
            print(f"健檢沒跑完（{type(e).__name__}），詳見 .system/hook-errors.log")
            return 3
        raise
