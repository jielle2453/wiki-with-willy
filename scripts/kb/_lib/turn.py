"""回合：每句話一個回合（D-074）。目前回合按 session 存 .system/turns/<sid>.json，另有 current.json 給 CLI。
回合備份住 .system/versions/<turn>/：turn.json、manifest.jsonl（一行一檔）、files/<rel>（modify 與 delete 的舊版）。
大改動攔截（D-075）：同回合用檔案工具修改既有綜合卡達 big_change_synthesis_max 張就擋，approve 後解除。
相對路徑保留原始大小寫（I-027：paths.normalize 會小寫，備份與還原不能用它）。
"""
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from kb._lib import config, ledger
from kb._lib.io import FileLock, read_json, write_json_atomic

TURNS_REL = Path(".system") / "turns"
VERSIONS_REL = Path(".system") / "versions"
CURRENT_NAME = "current.json"
PROMPT_HEAD_MAX = 80
BIG_CHANGE_REASON = ("這一題已經改了 {n} 張綜合卡，先停下來告訴客戶還有幾張、為什麼；"
                     "客戶同意後用知識庫腳本 turn approve 解除再繼續。")
BACKUP_FAILED_REASON = "備份失敗，這次不寫。說「檢查」我看原因"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _now():
    return datetime.now()


def _safe_sid(session_id):
    return _UNSAFE.sub("_", str(session_id))


def _session_file(vault, session_id):
    return Path(vault) / TURNS_REL / f"{_safe_sid(session_id)}.json"


def _current_file(vault):
    return Path(vault) / TURNS_REL / CURRENT_NAME


def start(vault, session_id, prompt):
    """開新回合：寫 turns/<sid>.json（有 sid 才寫）與 current.json。回回合 dict。"""
    now = _now()
    head = " ".join(str(prompt or "").split())[:PROMPT_HEAD_MAX]
    tag = _safe_sid(session_id)[:4] if session_id else "0000"
    taken = {t.get("turn") for t in (_read_turn(_session_file(vault, session_id)) if session_id else None,
                                     _read_turn(_current_file(vault))) if t}
    while f"{now:%Y%m%d-%H%M%S}-{tag}" in taken or (Path(vault) / VERSIONS_REL / f"{now:%Y%m%d-%H%M%S}-{tag}").exists():
        now += timedelta(seconds=1)  # 同一秒兩句話：代號不能撞
    data = {
        "schema_version": 1,
        "turn": f"{now:%Y%m%d-%H%M%S}-{tag}",
        "session_id": str(session_id) if session_id else "",
        "started": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt_head": head,
        "approved": False,
    }
    (Path(vault) / TURNS_REL).mkdir(parents=True, exist_ok=True)
    if session_id:
        write_json_atomic(_session_file(vault, session_id), data)
    write_json_atomic(_current_file(vault), data)
    return data


def current(vault, session_id=None):
    """有 sid 讀 turns/<sid>.json；沒有或檔不存在讀 current.json；都沒有就開一個（hook 沒跑到也要有回合）。"""
    if session_id:
        data = _read_turn(_session_file(vault, session_id))
        if data:
            return data
    data = _read_turn(_current_file(vault))
    if data:
        return data
    return start(vault, session_id, "")


def _read_turn(path):
    try:
        data = read_json(path)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("turn") else None


def _write_turn(vault, data):
    sid = data.get("session_id")
    if sid:
        write_json_atomic(_session_file(vault, sid), data)
    cur = _read_turn(_current_file(vault))
    if not sid or (cur and cur.get("turn") == data["turn"]):
        write_json_atomic(_current_file(vault), data)


def approve(vault, session_id=None):
    data = current(vault, session_id)
    data["approved"] = True
    _write_turn(vault, data)
    return data


def _realcase(p):
    """存在的部分以 realpath 為準（磁碟上的大小寫、junction），不存在的尾段原樣接回。"""
    cur, tail = p, []
    while not os.path.exists(cur):
        head, name = os.path.split(cur)
        if not name or head == cur:
            return p
        tail.insert(0, name)
        cur = head
    return os.path.join(os.path.realpath(cur), *tail) if tail else os.path.realpath(cur)


def rel_path(vault, target, cwd):
    """目標相對總資料夾的路徑，正斜線、原始大小寫。不在總資料夾裡丟 ValueError。"""
    target = str(target).replace("\\", "/")
    if not os.path.isabs(target):
        target = os.path.join(str(cwd or os.getcwd()), target)
    real_t = _realcase(os.path.abspath(target))
    real_v = os.path.realpath(str(vault))
    nc_t, nc_v = os.path.normcase(real_t), os.path.normcase(real_v).rstrip("\\/")
    if not nc_t.startswith(nc_v + os.sep):
        raise ValueError(f"不在總資料夾裡：{target}")
    return real_t[len(nc_v) + 1:].replace("\\", "/")


def turn_dir(vault, turn_id, info=None):
    """.system/versions/<turn_id>/，第一次建時寫 turn.json。"""
    vdir = Path(vault) / VERSIONS_REL / turn_id
    vdir.mkdir(parents=True, exist_ok=True)
    meta = vdir / "turn.json"
    if not meta.exists():
        info = info or _find_turn(vault, turn_id) or {}
        write_json_atomic(meta, {
            "schema_version": 1, "turn": turn_id,
            "session_id": info.get("session_id", ""),
            "started": info.get("started", _now().strftime("%Y-%m-%dT%H:%M:%S")),
            "prompt_head": info.get("prompt_head", ""),
        })
    return vdir


def _find_turn(vault, turn_id):
    d = Path(vault) / TURNS_REL
    if not d.is_dir():
        return None
    for p in [d / CURRENT_NAME, *sorted(d.glob("*.json"))]:
        data = _read_turn(p)
        if data and data.get("turn") == turn_id:
            return data
    return None


def entries(vault, turn_id):
    m = Path(vault) / VERSIONS_REL / turn_id / "manifest.jsonl"
    if not m.is_file():
        return []
    with open(m, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def backup(vault, rel, turn_id, action=None):
    """寫入前把舊版複製到 versions/<turn_id>/files/<rel>，manifest 追加一行。同回合同路徑只做第一次。
    action 預設存在 modify、不存在 create；呼叫端刪檔前傳 delete。複製失敗丟 OSError。
    """
    if any(e.get("path") == rel for e in entries(vault, turn_id)):
        return
    target = Path(vault) / rel
    exists = target.is_file()
    action = action or ("modify" if exists else "create")
    vdir = turn_dir(vault, turn_id)
    if exists:
        dst = vdir / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, dst)
    manifest = vdir / "manifest.jsonl"
    row = {"path": rel, "action": action, "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
    with FileLock(manifest):
        with open(manifest, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _is_synthesis(vault, rel):
    from kb import guard
    from kb._lib import sources as sources_lib
    from kb._lib.paths import normalize
    vault_n = normalize(str(vault), None)
    zone, _alias = guard.classify(normalize(str(Path(vault) / rel), None), vault_n, sources_lib.load(vault))
    return zone == "Z1"


def modified_synthesis_count(vault, turn_id):
    return sum(1 for e in entries(vault, turn_id) if e.get("action") == "modify" and _is_synthesis(vault, e["path"]))


def before_write(vault, target, cwd, zone, session_id):
    """守門放行後、真的寫之前：大改動門檻（只數 Z1 modify）、回合備份。回 (ok, code, reason)。"""
    t = current(vault, session_id)
    rel = rel_path(vault, target, cwd)
    if zone == "Z1" and (Path(vault) / rel).is_file() and not t.get("approved"):
        if not any(e.get("path") == rel for e in entries(vault, t["turn"])):
            n = modified_synthesis_count(vault, t["turn"])
            if n >= config.get(vault, "big_change_synthesis_max"):
                return False, "big_change", BIG_CHANGE_REASON.format(n=n)
    try:
        backup(vault, rel, t["turn"], None)
    except OSError as e:
        ledger.log_error(vault, f"backup_failed {rel}: {type(e).__name__}: {e}")
        return False, "backup_failed", BACKUP_FAILED_REASON
    return True, "", ""
