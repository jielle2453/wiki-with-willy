"""清單 3（原 12，D-034）：守門留痕。這週被擋幾次、跟上週比（數字）；守門最後跑的日期比最近的對話舊兩天以上 → 待處理一件
（請服務人員看：守門沒跑，改檔就沒有備份與擋線）。ledger.tsv 只數 deny；guard-status 取各台電腦最新的那天。"""
import json
from datetime import date, timedelta
from pathlib import Path

from kb._lib import journal, ledger
from kb.lint import runner

SCOPE = "local"
NAME = "守門"
STALE_DAYS = 2  # 對話那天跟守門最後跑的那天差幾天以上才算沒在跑（那天的對話可能沒用到工具）


def _denies(vault, today):
    p = Path(vault) / ledger.LEDGER_REL
    week = prev = 0
    if not p.is_file():
        return 0, 0
    now = date.fromisoformat(today)
    with open(p, encoding="utf-8") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 3 or cols[2] != "deny":
                continue
            try:
                d = date.fromisoformat(cols[0][:10])
            except ValueError:
                continue
            age = (now - d).days
            if 0 <= age < 7:
                week += 1
            elif 7 <= age < 14:
                prev += 1
    return week, prev


def _last_guard(vault):
    d = Path(vault) / ledger.STATUS_DIR_REL
    days = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            days.append(str(json.loads(p.read_text(encoding="utf-8")).get("date") or ""))
        except (OSError, ValueError, AttributeError):
            continue
    days = [x for x in days if len(x) == 10]
    return max(days) if days else None


def _had_journal(vault, last_guard):
    """照理該有工作日誌嗎：守門跑過，或開過守門沒在跑那件（新建的知識庫第一次開場還沒有日誌，那不算讀不到）。"""
    if last_guard:
        return True
    try:
        from kb._lib import pending
        return any(i.get("key") == "guard_stale" for i in pending.load(vault)["items"])
    except ValueError:
        return True


def _last_conversation(vault, last_guard=None):
    """最近一段對話的那天；該有日誌卻一個月都沒有就丟出去（讀不到資料：這項算沒查成，不關它的件，T-28）。"""
    if not journal.months(vault):
        if _had_journal(vault, last_guard):
            raise ValueError("工作日誌讀不到")
        return None
    for month in reversed(journal.months(vault)):
        rows = [r for r in journal.read_lines(vault, month) if r.get("kind") == "turn" and r.get("time")]
        if rows:
            return max(str(r["time"])[:10] for r in rows)
    return None


def run(ctx):
    week, prev = _denies(ctx.vault, ctx.today)
    last = _last_guard(ctx.vault)
    out = [runner.finding("guard", "number", "guard", "", data={"denied_week": week, "denied_prev": prev, "last": last})]
    talk = _last_conversation(ctx.vault, last)
    if talk and (last is None or date.fromisoformat(talk) - date.fromisoformat(last) >= timedelta(days=STALE_DAYS)):
        when = f"最後一次是 {last}" if last else "一直沒有跑的紀錄"
        out.append(runner.finding("guard", "decide", "guard_stale",
                                  f"守門好像沒在跑（{when}，最近的對話是 {talk}）；請服務人員看一下",
                                  data={"last": last, "conversation": talk}))
    return out
