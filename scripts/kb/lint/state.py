""".system/lint.json（schema 1，lint 題）：健檢自己的狀態。

{"schema_version": 1, "last_background": "2026-10-20", "last_spawn": "2026-10-20", "last_report": "2026-09",
 "synthesis_fix": [{"path": "案子/信義王/信義王.md", "reason": "出處點不開", "ids": ["f-1a2b3c4d"], "sha": "<卡檔 sha256 前 12 碼>"}],
 "month_done": [{"date": "2026-10-20", "text": "清掉 40 個回合備份（空出 12 MB）"}], "last_counts": {"decide": 5, "set_aside": 6, "done": 3}}
last_background：上次背景（或她說「幫我檢查」）跑完的那天；last_spawn：開場上次開背景子行程的那天（兩個對話不重複開）；
last_report：最後一份月底定稿報告的月份（第一次跑記成上個月、不寫）；synthesis_fix：標成要更新的綜合卡，sha 是標的時候卡檔的指紋——
卡被改寫（整理時重寫）指紋就變、這筆不再算（fixes 只回還算數的），下次健檢從檔案拿掉；沒有綜合卡的實體要補標籤，path 寫實體編號（e-…）、
sha 是正本 tags 的指紋（D-505 裁定 4），標過之後 tags 變了就不算；month_done：還沒寫進定稿報告的「替你做了」；
last_counts：上一份定稿報告的件數，一句話跟它比；last_preference：上次跑偏好月檢的那天（D-504，滿 preference_lint_days 天才再跑）。壞檔當空的重建（健檢狀態掉了只少比較，不擋檢查）。
"""
import hashlib
from pathlib import Path

from kb._lib import ledger
from kb._lib.io import FileLock, read_json, write_json_atomic

LINT_POSIX = ".system/lint.json"
SCHEMA_VERSION = 1
KEYS = {"last_background": None, "last_spawn": None, "last_report": None, "synthesis_fix": [], "month_done": [],
        "last_counts": None, "last_preference": None, "last_shared": None}
TYPES = {"last_background": str, "last_spawn": str, "last_report": str, "synthesis_fix": list, "month_done": list,
         "last_counts": dict, "last_preference": str, "last_shared": str}


def _path(vault):
    return Path(vault) / LINT_POSIX


def exists(vault):
    return _path(vault).is_file()


def load(vault):
    try:
        data = read_json(_path(vault))
    except (ValueError, OSError):
        data = None
    data = data if isinstance(data, dict) else {}
    out = {"schema_version": SCHEMA_VERSION}
    for k, default in KEYS.items():
        v = data.get(k, default)
        out[k] = v if isinstance(v, TYPES[k]) else (list(default) if isinstance(default, list) else default)  # 型別不對當沒有（T-14）
    out["synthesis_fix"] = [f for f in out["synthesis_fix"] if isinstance(f, dict) and f.get("path")]
    out["month_done"] = [d for d in out["month_done"] if isinstance(d, dict) and d.get("text")]
    return out


def save(vault, data, turn_id=None):
    """寫 lint.json；turn_id 給了先備進回合（她說退回時標的要更新也一起退）。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, LINT_POSIX, turn_id)
    write_json_atomic(p, dict(data, schema_version=SCHEMA_VERSION))


def change(vault, fn, turn_id=None, timeout=None):
    """鎖住讀改寫：fn(data) 回真才寫。回 fn 的結果。timeout 給了照它等鎖（開場只等一下）。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p, timeout=timeout):
        data = load(vault)
        got = fn(data)
        if got:
            save(vault, data, turn_id)
    return got


def card_sha(vault, rel):
    """卡檔 sha256 前 12 碼；rel 是實體編號（e-…）時是那個實體正本 tags 的指紋（實體不在回 None）。"""
    if str(rel).startswith("e-") and "/" not in str(rel):
        from kb._lib import entities
        try:
            entry = next((e for e in entities.load(vault)["entities"] if e.get("id") == rel), None)
        except ValueError:
            return None
        return entities.tags_fingerprint(entry) if entry is not None else None
    try:
        return hashlib.sha256((Path(vault) / rel).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def add_fix(vault, path, reason, ids, turn_id=None):
    """標一張綜合卡要更新（出處點不開、有空段、設定檔改了）：同一張卡原因與編號併在一筆；path 是實體編號時是沒有綜合卡的實體要補標籤。回有沒有新增或多了原因、編號
    （沒有就不寫、不算「替你做了」，測試 T-7）。"""
    sha = card_sha(vault, path)

    def fn(data):
        old = next((f for f in data["synthesis_fix"] if f["path"] == path), None)
        if old is not None and old.get("sha") == sha:
            reasons = [r for r in str(old.get("reason") or "").split("、") if r]
            merged = sorted(set(old.get("ids") or []) | set(ids or []))
            if reason in reasons and merged == sorted(set(old.get("ids") or [])):
                return False
            if reason not in reasons:
                reasons.append(reason)
            old["reason"] = "、".join(reasons)
            old["ids"] = merged
            return True
        data["synthesis_fix"] = [f for f in data["synthesis_fix"] if f["path"] != path]
        data["synthesis_fix"].append({"path": path, "reason": reason, "ids": sorted(set(ids or [])), "sha": sha})
        return True
    return bool(change(vault, fn, turn_id))


def fixes(vault):
    """還算數的要更新綜合卡（卡還在、指紋跟標的時候一樣）；讀不到回空。"""
    try:
        data = load(vault)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"lint.json 讀不到：{type(e).__name__}: {e}", source="lint")
        return []
    return [f for f in data["synthesis_fix"] if f.get("sha") and card_sha(vault, f["path"]) == f["sha"]]


def prune(vault, turn_id=None):
    """拿掉不算數的（卡被改寫了、不見了）。回拿掉幾筆。"""
    def fn(data):
        keep = [f for f in data["synthesis_fix"] if f.get("sha") and card_sha(vault, f["path"]) == f["sha"]]
        gone = len(data["synthesis_fix"]) - len(keep)
        data["synthesis_fix"] = keep
        return gone
    return change(vault, fn, turn_id) or 0


def _moved(path, mapping):
    """path 在 mapping 的某個舊夾底下就換成新夾（比對不分大小寫）；不在回 None。"""
    for old, new in mapping:
        if str(path).casefold().startswith(old.casefold() + "/"):
            return new + str(path)[len(old):]
    return None


def follow_dirs(vault, mapping, turn_id=None):
    """型別夾換了名字（型別夾編號，D-247）：synthesis_fix 的 path 在 [(舊夾, 新夾)] 底下的換成新夾。回有沒有改。"""
    def fn(data):
        hit = False
        for f in data["synthesis_fix"]:
            new = _moved(f["path"], mapping)
            if new is not None:
                f["path"], hit = new, True
        return hit
    return bool(change(vault, fn, turn_id))
