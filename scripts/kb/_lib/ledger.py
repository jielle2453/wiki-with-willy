"""留痕：守門事件帳 .system/ledger.tsv（一行一事件，只記 deny、unparsed、unresolved、guard_error）、
guard-status 一機一檔每日一次、hook-errors.log。所有寫入失敗都吞掉，留痕不能讓守門本身掛掉。
"""
import json
import os
import platform
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

LEDGER_REL = ".system/ledger.tsv"
STATUS_DIR_REL = ".system/guard-status"
ERROR_LOG_REL = ".system/hook-errors.log"
FALLBACK_ERROR_LOG = "wiki-with-willy-hook-errors.log"
HEADER = ["time", "tool", "decision", "zone", "reason_code", "path_or_command_head"]
HEAD_MAX = 120


_LINE_BREAKS = re.compile("[\t\r\n\x0b\x0c\x1c\x1d\x1e\x85  ]")  # str.splitlines 會斷的全部（L1）


def _clean(text):
    return _LINE_BREAKS.sub(" ", str(text or ""))


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def fallback_error_log():
    return Path(tempfile.gettempdir()) / FALLBACK_ERROR_LOG


def log_error(vault, text):
    """追加一行 `時間\\tguard\\t訊息`。找不到 vault 就寫到 %TEMP%。"""
    target = (Path(vault) / ERROR_LOG_REL) if vault else fallback_error_log()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{_now()}\tguard\t{_clean(text)[:200]}\n")
    except OSError:
        pass


def append(vault, row):
    """row：{tool, decision, zone, reason_code, head}。檔不存在就建含表頭。"""
    target = Path(vault) / LEDGER_REL
    line = "\t".join([
        _now(), _clean(row.get("tool")), _clean(row.get("decision")), _clean(row.get("zone")),
        _clean(row.get("reason_code")), _clean(row.get("head"))[:HEAD_MAX],
    ])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        new = not target.exists()
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("\t".join(HEADER) + "\n")
            f.write(line + "\n")
    except OSError as e:
        log_error(vault, f"{type(e).__name__}: 寫 ledger 失敗 {e}")


def _safe_host(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "") or "unknown"


def touch_status(vault):
    """.system/guard-status/<主機名>.json：{date, python, host, plugin_version}，同一天只寫一次。"""
    host = platform.node()
    target = Path(vault) / STATUS_DIR_REL / f"{_safe_host(host)}.json"
    today = date.today().isoformat()
    try:
        with open(target, encoding="utf-8") as f:
            if json.load(f).get("date") == today:
                return
    except Exception:  # noqa: BLE001  狀態檔壞了就重寫，不能影響決定
        pass
    try:
        from kb._lib.paths import read_manifest
        version = read_manifest()["version"]
    except Exception:  # noqa: BLE001  留痕不能掛
        version = "unknown"
    data = {"date": today, "python": platform.python_version(), "host": host, "plugin_version": version}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    except OSError as e:
        log_error(vault, f"{type(e).__name__}: 寫 guard-status 失敗 {e}")
