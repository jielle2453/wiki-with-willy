"""清單 7（原 17，D-207）：清理與大小。清理照前一份（_lib/clean.run，跟備份還原同一把鎖，搶不到、背景整理在跑就這次不清、
報告寫一句）；收件匣份數、知識庫總大小是數字；綜合卡 AI 會讀的那幾段（read_ranges，檔頭、相關檔、紀事不算）超過 lint_synthesis_max_chars 字 → 標要更新（原因「太長」），
下次整理時 willy 自己精簡，不問使用者（D-527）。"""
import os
from pathlib import Path

from kb._lib import backup, clean, inventory, synthesis
from kb.lint import runner
from kb.lint.checks import _synthesis
from kb.lint.checks.system_dir import _is_link

SCOPE = "local"  # 多人共用 3（D-304）：每台各自做；沒標 SCOPE 的是 shared，多人時只一台跑
NAME = "清理與大小"
REASON = "太長"
BUSY_TEXT = "清理這次沒做：另一個備份、還原或退回正在跑，下週再清"
BATCH_TEXT = "清理這次沒做：背景整理在跑，下週再清"


def _clean(ctx):
    if backup.batch_running(ctx.vault):
        raise runner.Skipped(BATCH_TEXT)
    try:
        got = clean.run(ctx.vault)
    except backup.Refused:
        raise runner.Skipped(BUSY_TEXT) from None
    parts = []
    if got["turns_removed"]:
        parts.append(f"{got['turns_removed']} 個回合備份")
    if got["snapshots_removed"]:
        parts.append(f"{got['snapshots_removed']} 份快照")
    if not parts:
        return None
    return f"清掉 {'、'.join(parts)}，空出 {clean.mb_text(got['freed_bytes'])}"


def _walk(root, skip_hidden=True):
    count = size = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not (skip_hidden and d.startswith(".")) and not _is_link(Path(dirpath) / d)]  # junction 也不走（T-13）
        for name in files:
            if skip_hidden and name.startswith("."):
                continue
            count += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return count, size


def run(ctx):
    out = [runner.finding("sizes", "fix", "clean", "", apply=_clean)]
    inbox, _bytes = _walk(Path(ctx.vault) / inventory.HOLD)
    _n, total = _walk(ctx.vault, skip_hidden=False)
    data = {"inbox": inbox, "vault_bytes": total, "inbox_over": inbox > ctx.config("lint_inbox_max")}
    from kb._lib import git
    if git.is_repo(ctx.vault) and git.multi(ctx.vault):  # 多人共用 3：repo 大小與追蹤中最大的 5 份
        data["repo_size"] = git.repo_size(ctx.vault)
        data["repo_largest"] = [{"path": p, "bytes": b} for p, b in git.largest_tracked(ctx.vault, 5)]
    out.append(runner.finding("sizes", "number", "sizes", "", data=data))
    limit = ctx.config("lint_synthesis_max_chars")
    big = []
    for e, rel, text in ctx.synthesis():
        if not e.get("archived") and synthesis.read_chars(text) > limit:  # 封存的實體整理時跳過，標了也不會改寫（D-332）
            big.append({"path": rel, "name": e["name"]})
    if big:
        out.append(_synthesis.fix_finding("sizes", "synthesis_big", REASON, big))
    return out
