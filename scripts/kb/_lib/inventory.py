"""清冊最小版（D-076）：.system/inventory.json，一筆一份成品。
id、origin（先只有 ai_output）、locations（相對總資料夾的 file:// spec）、created_turn、fingerprints（sha256 加時間）、
status（active｜turn_reverted｜deleted）。指紋先用位元組 sha256（B-018）。讀改寫整段包 FileLock（I-080）。
"""
import hashlib
import secrets
from datetime import datetime
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

INVENTORY_REL = Path(".system") / "inventory.json"
EMPTY = {"schema_version": 1, "entries": []}


def _path(vault):
    return Path(vault) / INVENTORY_REL


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load(vault):
    data = read_json(_path(vault))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"schema_version": 1, "entries": []}
    return data


def save(vault, data):
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        write_json_atomic(p, data)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_id(taken):
    while True:
        new = "out-" + secrets.token_hex(4)
        if new not in taken:
            return new


def register_output(vault, rel, turn_id):
    """同 location 已有條目：最後一筆指紋相同就不動，不同就追加一筆；沒有就新建。回那一筆。"""
    location = "file://" + str(rel).replace("\\", "/")
    digest = sha256_of(Path(vault) / rel)
    now = _now()
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        for entry in data["entries"]:
            if location in entry.get("locations", []):
                fps = entry.setdefault("fingerprints", [])
                if fps and fps[-1].get("sha256") == digest:
                    return entry
                fps.append({"sha256": digest, "time": now, "turn": turn_id})
                write_json_atomic(p, data)
                return entry
        entry = {
            "id": _new_id({e.get("id") for e in data["entries"]}),
            "origin": "ai_output",
            "locations": [location],
            "created_turn": turn_id,
            "created": now,
            "fingerprints": [{"sha256": digest, "time": now, "turn": turn_id}],
            "status": "active",
        }
        data["entries"].append(entry)
        write_json_atomic(p, data)
        return entry


def outputs_of_turn(vault, turn_id):
    return [e for e in load(vault)["entries"] if e.get("created_turn") == turn_id and e.get("status") == "active"]


def set_status(vault, ids, status):
    ids = set(ids)
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        for entry in data["entries"]:
            if entry.get("id") in ids:
                entry["status"] = status
        write_json_atomic(p, data)
