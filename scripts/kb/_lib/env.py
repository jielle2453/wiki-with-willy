"""按機器記的提示記號 .system/env.json（schema 1，D-021 補、D-094 補）：找不到的來源每台電腦只在開場報一次。
{"schema_version": 1, "machines": {"<機器名>": {"missing_sources_reported": {"<來源 id 或別名>": "YYYY-MM-DD"},
                                              "vault_path": "D:/知識庫", "backup_dir": "E:/備份"}}}
vault_path、backup_dir 是備份還原與換電腦加的選填欄（D-206、D-204）：這台電腦上次記的知識庫位置（realpath、正斜線）與備份放哪；
跟 missing_sources_reported 各管各的，改一個不動另一個。
update_check 是升級打包加的選填欄（背景查新版的結果）：{"date","latest","kind","allowed","note","auto_patched","error","verify_failed"}，
讀不到、形狀不對一律當沒查過（開場只用上一次查的結果，D-072、D-216）。verify_failed（換版前先驗新版，D-230）是背景驗新版沒過的那份
（upgrade.verify 的回傳，null 是沒有）；同一版、同一個 Python 背景不再重抓，換成功或清單換了新版本時清掉。
daily_done 是選填欄（YYYY-MM-DD）：這台電腦每天第一句要做的那批最近做完的日期（放寬丙 3b，D-327）。舊的 inbox_reminded 欄（D-045）不再用，讀到就略過。
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


def _machine(data, create=False):
    """這台電腦的紀錄（物件）；沒有回 None，create 時補一份空的。"""
    machine = data["machines"].get(machine_name())
    if not isinstance(machine, dict):
        if not create:
            return None
        machine = {}
        data["machines"][machine_name()] = machine
    return machine


def _marks(data, create=False):
    machine = _machine(data, create)
    if machine is None or (not isinstance(machine.get(KEY), dict) and not create):
        return {}
    if not isinstance(machine.get(KEY), dict):
        machine[KEY] = {}
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


def _text_of(vault, key):
    machine = _machine(_load(vault))
    value = machine.get(key) if machine else None
    return value if isinstance(value, str) and value else None


def _set_text(vault, key, value):
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        _machine(data, create=True)[key] = str(value)
        write_json_atomic(p, data)


def vault_path(vault):
    """這台電腦記的知識庫位置；沒記過回 None。只讀。"""
    return _text_of(vault, "vault_path")


def set_vault_path(vault, path):
    _set_text(vault, "vault_path", path)


def backup_dir(vault):
    """這台電腦記的備份夾；沒記過回 None。"""
    return _text_of(vault, "backup_dir")


def set_backup_dir(vault, path):
    _set_text(vault, "backup_dir", path)


def other_machines(vault):
    """別台電腦有紀錄的機器名（照檔案裡的順序）；這台自己不算。"""
    data = _load(vault)
    return [name for name, rec in data["machines"].items() if name != machine_name() and isinstance(rec, dict)]


def vault_paths_elsewhere(vault):
    """別台電腦記的知識庫位置（沒記的略過）。"""
    data = _load(vault)
    return [rec["vault_path"] for name, rec in data["machines"].items()
            if name != machine_name() and isinstance(rec, dict) and isinstance(rec.get("vault_path"), str)
            and rec["vault_path"]]


def update_check(vault):
    """這台電腦上一次查新版的結果（物件）；沒查過、形狀不對回 None。只讀。"""
    machine = _machine(_load(vault))
    value = machine.get("update_check") if machine else None
    return dict(value) if isinstance(value, dict) else None


def set_update_check(vault, value):
    """整份換掉這台電腦的查新版結果（背景查新版與開場記日期共用）。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        _machine(data, create=True)["update_check"] = dict(value)
        write_json_atomic(p, data)


def daily_done(vault):
    """這台電腦每天第一句要做的那批（日程、版本、背景健檢、搬家）最近做完的日期（YYYY-MM-DD）；沒記過回 None。只讀。
    舊的 inbox_reminded 欄（放寬丙 3 拿掉收件匣提醒）讀到就略過，不遷移。"""
    return _text_of(vault, "daily_done")


def set_daily_done(vault, day):
    _set_text(vault, "daily_done", day)


def machine_id(vault):
    """這台的流水檔代號（m- 加 8 碼 hex）：env.json 這台那一筆的 machine_id，沒有就產生寫回（多人共用 1）。"""
    got = _text_of(vault, "machine_id")
    if got:
        return got
    import secrets
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        machine = _machine(data, create=True)
        if not (isinstance(machine.get("machine_id"), str) and machine["machine_id"]):
            machine["machine_id"] = "m-" + secrets.token_hex(4)
            write_json_atomic(p, data)
        return machine["machine_id"]


def set_machine_id(vault, value):
    _set_text(vault, "machine_id", value)


def agenda_export_dir(vault):
    """這台的日程匯出夾（每台路徑不同，多人共用 1）；沒設回 None。"""
    return _text_of(vault, "agenda_export_dir")


def set_agenda_export_dir(vault, path):
    """空字串＝拿掉。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        machine = _machine(data, create=True)
        if path:
            machine["agenda_export_dir"] = str(path)
        else:
            machine.pop("agenda_export_dir", None)
        write_json_atomic(p, data)


def source_paths(vault):
    """這台各來源的實際位置 {來源 id: 路徑}（第 2 份才寫，這份只讀）。"""
    machine = _machine(_load(vault))
    value = machine.get("source_paths") if machine else None
    return dict(value) if isinstance(value, dict) else {}


def person(vault):
    """這台電腦的人名（多人共用 2）；沒記過回 None。只讀。"""
    return _text_of(vault, "person")


def set_person(vault, name):
    _set_text(vault, "person", name)


def set_source_path(vault, src_id, path):
    """記這台電腦某來源的位置；path 是 None 就拿掉（kb folders locate，D-292）。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = _load(vault)
        machine = _machine(data, create=True)
        paths = machine.get("source_paths")
        if not isinstance(paths, dict):
            paths = machine["source_paths"] = {}
        if path is None:
            paths.pop(src_id, None)
        else:
            paths[src_id] = str(path)
        write_json_atomic(p, data)
