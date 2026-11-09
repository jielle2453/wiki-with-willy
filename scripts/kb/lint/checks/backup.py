"""清單 8（原 1，D-209）：備份。上次備份幾天前（前一份的備份清單，只算「備份」不算快照，可以用的）；還沒備份過或超過 30 天，
報告的建議下一步寫一句。回合備份夾寫不寫得進去（寫一個暫存檔試）：寫不進去進待處理（改了東西就沒辦法退回）。"""
import os
import tempfile
from pathlib import Path

from kb._lib import backup as backup_lib
from kb._lib import turn
from kb.lint import runner

SCOPE = "local"
NAME = "備份"
OLD_DAYS = 30
NEVER = "還沒備份過，說「幫我備份」就好"
OLD = "上次備份是 {n} 天前，說「幫我備份」就好"


def _writable(folder):
    """回合備份夾寫得進去嗎（建一個暫存檔再刪）；測試換掉它。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=str(folder), prefix=".lint-")
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def run(ctx):
    rows = [b for b in backup_lib.list_backups(ctx.vault) if b.get("kind") == "backup" and b.get("usable") and b.get("created")]
    days = min((ctx.days_since(b["created"]) for b in rows), default=None) if rows else None
    suggest = NEVER if days is None else (OLD.format(n=days) if days > OLD_DAYS else None)
    out = [runner.finding("backup", "number", "backup", "", data={"last_backup_days": days, "suggest": suggest})]
    if not _writable(Path(ctx.vault) / turn.VERSIONS_REL):
        out.append(runner.finding("backup", "decide", "versions_unwritable",
                                  "回合備份夾寫不進去，改了東西沒辦法退回；請服務人員看一下"))
    return out
