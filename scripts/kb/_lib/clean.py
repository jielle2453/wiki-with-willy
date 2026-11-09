"""清理（備份還原與換電腦 要做的事 3，D-207、D-056、D-211）：回合備份與快照放太久的清掉。

回合備份（.system/versions/<回合>/）：超過 versions_keep_days 天、或超過 versions_keep_turns 個就從最舊的清，
但至少留最近 versions_min_turns 個；目前回合（turns/current.json）、還沒答完的回合（turns/*.json 的 done 不是 true）與 card-maker 還有動靜的整理 job 的回合（batches.json 的 open job，批次 last_seen 在 card_batch_idle_minutes 內，測試 T-9、N-5；當機留下的 job 不永遠保護）不清。
快照超過 snapshots_keep 份清最舊的。清掉的回合不進回合備份（它本身就是備份）。
plan 只算、不動；run 持 backup 同一把鎖（.system/revert.lock）做，搶不到不等。kb backup 成功後叫 run，健檢（lint 題）之後也叫它。
結果：{"turns_removed","earliest":{"time","prompt_head"}|None,"snapshots_removed","freed_bytes"}；earliest 是清完之後最早還能退回的那一句。
"""
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from kb._lib import backup, config, turn
from kb._lib import io as kio
from kb._lib import paths

TURNS_REL = Path(".system") / "turns"


def _started(folder):
    """回合夾的開始時間（turn.json 的 started，沒有就用夾名的時間）；都讀不懂回 None。"""
    try:
        info = kio.read_json(folder / "turn.json", {}) or {}
    except (ValueError, OSError):
        info = {}
    text = info.get("started") if isinstance(info, dict) else None
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%S"), info
    except ValueError:
        return turn._turn_time(folder.name), (info if isinstance(info, dict) else {})


def _open_job_turns(vault):
    """card-maker 還開著的整理 job 的回合代號（很長的整理，派工那句的回合備份不能被清掉）；batches.json 讀不到當沒有。"""
    from kb._lib import batches
    try:
        data = batches.load(vault)
    except batches.Unreadable:
        return set()
    now = batches._now()
    out = set()
    for j in data["jobs"]:
        if j.get("status") != "open" or not j.get("turn"):
            continue
        mine = [b for b in data["batches"] if b.get("job") == j.get("job") and b.get("status") == "open"]
        if mine:
            active = any(not batches._idle(b, now) for b in mine)  # 還有一批有動靜（last_seen 在閒置分鐘內）
        else:
            made = batches._parse(j.get("created"))  # 還沒有批次：看 job 本身是不是剛開
            active = made is not None and now - made <= timedelta(minutes=batches._idle_minutes(None))
        if active:
            out.add(str(j["turn"]))
    return out


def _protected(vault):
    """目前回合、還沒答完的回合與還開著的 job 的回合代號。"""
    out = _open_job_turns(vault)
    folder = Path(vault) / TURNS_REL
    if not folder.is_dir():
        return out
    for p in folder.glob("*.json"):
        try:
            data = kio.read_json(p)
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and data.get("turn") and (p.name == "current.json" or data.get("done") is False):
            out.add(str(data["turn"]))
    return out


def _size(path):
    total = 0
    for dirpath, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                total += os.stat(paths.long_path(Path(dirpath) / name)).st_size
            except OSError:
                pass
    return total


def plan(vault, now=None):
    """算要清什麼，不動任何東西：回 {"turns": [回合夾 Path]（由舊到新）, "snapshots": [Path], "earliest", "freed_bytes"}。"""
    now = now or datetime.now()
    days = config.get(vault, "versions_keep_days")
    keep_turns = config.get(vault, "versions_keep_turns")
    min_turns = config.get(vault, "versions_min_turns")
    root = Path(vault) / turn.VERSIONS_REL
    rows = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir():
            started, info = _started(d)
            rows.append((started or datetime.min, d, info))
    rows.sort(key=lambda r: (r[0], r[1].name))  # 由舊到新
    protected = _protected(vault)
    cutoff = now - timedelta(days=days)
    # Obsidian 的回合（--via obsidian，D-264）不算進回合數上限與下限，只照天數清；其餘照舊
    ours = [r for r in rows if r[2].get("source") != "obsidian"]
    total = len(ours)
    candidates = [r[1] for i, r in enumerate(ours)
                  if r[1].name not in protected and (r[0] < cutoff or total - i > keep_turns)]
    doomed = candidates[:max(0, total - min_turns)]
    doomed += [r[1] for r in rows if r[2].get("source") == "obsidian" and r[1].name not in protected and r[0] < cutoff]
    gone = {d.name for d in doomed}
    left = [r for r in ours if r[1].name not in gone]  # 「最早還能退回的那一句」是對話與終端機的，Obsidian 的沒有那句話
    earliest = None
    if left:
        first = left[0]
        earliest = {"time": first[0].strftime("%Y-%m-%dT%H:%M:%S") if first[0] != datetime.min else None,
                    "prompt_head": str(first[2].get("prompt_head", ""))}
    snaps = backup.snapshots(vault)[config.get(vault, "snapshots_keep"):]
    freed = sum(_size(d) for d in doomed) + sum(p.stat().st_size for p in snaps if p.exists())
    return {"turns": doomed, "snapshots": snaps, "earliest": earliest, "freed_bytes": freed}


def _result(got):
    return {"turns_removed": len(got["turns"]), "earliest": got["earliest"], "snapshots_removed": len(got["snapshots"]),
            "freed_bytes": got["freed_bytes"]}


def _remove_tree(path):
    shutil.rmtree(paths.long_path(path), ignore_errors=False)


def run(vault, check=False):
    """做清理（check 只算）。回結果 dict；鎖被占著丟 backup.Refused。刪不掉的回合夾略過、不算進數字。"""
    if check:
        return _result(plan(vault))
    with backup.acquire(vault):
        got = plan(vault)
        done_turns = []
        for d in got["turns"]:
            try:
                _remove_tree(d)
                done_turns.append(d)
            except OSError:
                pass
        done_snaps = []
        for p in got["snapshots"]:
            try:
                p.unlink()
                done_snaps.append(p)
            except OSError:
                pass
        return _result(dict(got, turns=done_turns, snapshots=done_snaps))


def mb_text(n):
    """位元組 → 「8 MB」（不足 1 MB 但有東西算 1）。"""
    return f"{max(1, round(n / (1024 * 1024))) if n else 0} MB"


def earliest_text(earliest):
    """「09-23 10:02 那句「把這份合約存起來」」；沒有回 None。"""
    if not earliest:
        return None
    try:
        when = datetime.strptime(str(earliest.get("time")), "%Y-%m-%dT%H:%M:%S").strftime("%m-%d %H:%M")
    except ValueError:
        when = "更早"
    head = " ".join(str(earliest.get("prompt_head") or "").split())
    return f"{when} 那句「{head}」" if head else f"{when} 那一次"


def sentence(result):
    """人讀的一句：清了什麼、空出多少、最早還能退回到哪一句。"""
    tail = earliest_text(result["earliest"])
    back = f"最早還能退回到 {tail}" if tail else "現在沒有回合備份可以退回"
    parts = []
    if result["turns_removed"]:
        parts.append(f"{result['turns_removed']} 個回合備份")
    if result["snapshots_removed"]:
        parts.append(f"{result['snapshots_removed']} 份快照")
    if not parts:
        return f"沒有要清的；{back}"
    return f"清掉 {'、'.join(parts)}，空出 {mb_text(result['freed_bytes'])}；{back}。"
