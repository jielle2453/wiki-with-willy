"""知識庫的固定編號 .system/vault.json（schema 1；密碼與守門，設計 session 2026-10-23 定）：{"schema_version": 1, "vault_id": "kb-<8 碼 hex>"}。

憑證管理員每筆的名字帶這個編號（wiki-with-willy/<vault_id>/…），同一台電腦兩個知識庫不混。
這個檔不備進回合，versions revert 不動它：退回補編號的那一句時編號照留，存好的密碼才對得上。
kb init 建新庫時寫、kb unlock 第一次要用時補（ensure_vault_id），備份還原與換電腦那題也叫它幫舊知識庫補。
檔壞了（不是 JSON、編號格式不對）不覆寫，丟 ValueError：覆寫會換掉編號，存好的密碼就對不上了。
"""
import re
import secrets
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

VAULT_POSIX = ".system/vault.json"
SCHEMA_VERSION = 1
_ID = re.compile(r"^kb-[0-9a-f]{8}$")


def _path(vault):
    return Path(vault) / VAULT_POSIX


def _read(vault):
    """回編號或 None（沒檔）；形狀不對丟 ValueError，訊息帶檔名。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{VAULT_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return None
    vid = data.get("vault_id") if isinstance(data, dict) else None
    if not isinstance(vid, str) or not _ID.match(vid):
        raise ValueError(f"{VAULT_POSIX}（沒有知識庫編號）")
    return vid


def vault_id(vault):
    """只讀：回編號，沒檔回 None。"""
    return _read(vault)


def ensure_vault_id(vault):
    """檔不在就建（鎖裡再看一次，兩個程式同時補只會有一個編號），在就讀；回編號。不備進回合。"""
    vid = _read(vault)
    if vid:
        return vid
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        vid = _read(vault)
        if vid:
            return vid
        vid = "kb-" + secrets.token_hex(4)
        write_json_atomic(p, {"schema_version": SCHEMA_VERSION, "vault_id": vid})
    return vid
