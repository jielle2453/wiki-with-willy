"""按機器記的提示記號 .system/env.json（schema 1，D-021 補、D-094 補）：找不到的來源每台電腦只在開場報一次。
{"schema_version": 1, "machines": {"<機器名>": {"missing_sources_reported": {"<來源 id 或別名>": "YYYY-MM-DD"}}}}
讀改寫包 FileLock；壞 JSON 或形狀不對當空的重建（這個檔只有提示記號，別讓它拖垮開場，I-046）。
"""
import json
import platform
from datetime import date
from pathlib import Path

from kb._lib.io import FileLock, write_json_atomic

ENV_REL = Path(".system") / "env.json"
KEY = "missing_sources_reported"


def machine_name():
    return platform.node() or "unknown"


def _path(vault):
    return Path(vault) / ENV_REL


def _load(vault):
    try:
        with open(_path(vault), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("machines"), dict):
        return {"schema_version": 1, "machines": {}}
    return data


def _marks(data, create=False):
    machine = data["machines"].get(machine_name())
    if not isinstance(machine, dict) or not isinstance(machine.get(KEY), dict):
        if not create:
            return {}
        machine = {KEY: {}}
        data["machines"][machine_name()] = machine
    return machine[KEY]


def missing_reported(vault, src_id):
    return src_id in _marks(_load(vault))


def mark_missing(vault, src_ids):
    """記成已報過；已有的保留第一次的日期。"""
    src_ids = list(src_ids)
    if not src_ids:
        return
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        marks = _marks(data, create=True)
        for src_id in src_ids:
            marks.setdefault(src_id, date.today().isoformat())
        write_json_atomic(p, data)


def clear_missing(vault, src_ids):
    """來源又找得到：清掉記號，之後再失蹤再報一次。沒有要清的不寫檔。"""
    src_ids = list(src_ids)
    if not src_ids or not any(missing_reported(vault, s) for s in src_ids):
        return
    p = _path(vault)
    with FileLock(p):
        data = _load(vault)
        marks = _marks(data)
        for src_id in src_ids:
            marks.pop(src_id, None)
        write_json_atomic(p, data)
