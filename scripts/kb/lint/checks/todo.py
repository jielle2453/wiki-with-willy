"""第二類（D-220）：整理擱著的事查整體、交給 ingest。用 cards.collect（跟 kb cards todo 同一套分桶，不寫檔）與待處理本身算件數：
待整理（new＋stale，同開場「還有 N 份沒整理完」）、沒掛實體、綜合卡待更新（含健檢標的）、原檔不見、同一份檔在兩處、讀不到、
日期過了沒銷、兩份寫法不同。報告寫數字；她說「幫我檢查」時 AI 講完交 ingest。"""
from kb._lib import pending
from kb.lint import runner

NAME = "整理擱著的"
AFTER_UPKEEP = True  # 關掉不成立的待處理之後才數（測試 T-24）


def run(ctx):
    ctx.forget("collect")  # 前面幾項可能剛標了綜合卡要更新
    out = ctx.collect()
    items = pending.load(ctx.vault)["items"]
    data = {"unsorted": len(out.get("new") or []) + len(out.get("stale") or []), "unlinked": len(out.get("unlinked") or []),
            "synthesis_stale": len(out.get("synthesis_stale") or []), "missing": len(out.get("missing") or []),
            "duplicates": len(out.get("duplicates") or []), "unreadable": len(out.get("unreadable") or []),
            "overdue": (out.get("agenda_counts") or {}).get("overdue", 0),
            "conflicts": sum(1 for i in items if i.get("kind") == "conflict" and i.get("status") == "open")}
    return [runner.finding("todo", "ingest", "todo", "", data=data)]
