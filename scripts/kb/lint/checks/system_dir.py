"""清單 9（B-055、B-030）：.system/ 底下有捷徑夾或連結（程式會寫到別的地方）→ 待處理一件（請服務人員看）；
工作日誌卡在沒寫完的那行超過一天（中斷補記沒寫成）→ 用地板句補完、done true（自己做）。
系統/ 底下程式認得的檔（含建置當天寫一次的建置報告）不查內容。"""
import os
from datetime import date, datetime
from pathlib import Path

from kb._lib import journal, turn
from kb.lint import runner

SCOPE = "local"
NAME = "系統夾"
STUCK_HOURS = 24


def _is_link(p):
    try:
        if p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p)):
            return True
        return bool(getattr(os.lstat(p), "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def _links(vault):
    root = Path(vault) / ".system"
    out = []
    for dirpath, dirs, files in os.walk(root):
        for name in list(dirs) + list(files):
            p = Path(dirpath) / name
            if _is_link(p):
                out.append(p.relative_to(vault).as_posix())
        dirs[:] = [d for d in dirs if not _is_link(Path(dirpath) / d)]
    return sorted(out)


def _now(ctx):
    """現在：今天就用現在的時間；測試給了別的日期用那天中午。"""
    return datetime.now() if ctx.today == date.today().isoformat() else datetime.fromisoformat(ctx.today + "T12:00:00")


def _stuck(ctx):
    now = _now(ctx)
    rows = []
    for month in journal.months(ctx.vault):
        for r in journal.read_lines(ctx.vault, month):
            if r.get("kind") != "turn" or r.get("done"):
                continue
            try:
                when = datetime.fromisoformat(str(r.get("time")))
            except ValueError:
                continue
            if (now - when).total_seconds() > STUCK_HOURS * 3600:
                rows.append(r)
    return rows


def _complete(rows):
    def apply(ctx):
        n = 0
        for r in rows:
            changed = [{"path": e["path"], "action": e.get("action")} for e in turn.entries(ctx.vault, r["turn"])]
            if journal.update_turn(ctx.vault, r["turn"], done=True, result=journal.INTERRUPTED_RESULT, interrupted=True,
                                   note=journal.floor_note(ctx.vault, r["turn"]), note_by="program", changed=changed,
                                   ended=_now(ctx).strftime("%Y-%m-%dT%H:%M:%S")):
                n += 1
        return f"補完 {n} 句沒記完的工作日誌" if n else None
    return apply


def run(ctx):
    out = []
    links = _links(ctx.vault)
    if links:
        out.append(runner.finding("system_dir", "decide", "system_link",
                                  f"知識庫的系統夾裡有捷徑（{'、'.join(links[:3])}），程式可能寫到別的地方；請服務人員看一下",
                                  data={"paths": links}))
    stuck = _stuck(ctx)
    if stuck:
        out.append(runner.finding("system_dir", "fix", "journal_stuck", "", data={"turns": [r["turn"] for r in stuck]},
                                  apply=_complete(stuck)))
    return out
