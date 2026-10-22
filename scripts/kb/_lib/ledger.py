"""留痕：守門事件帳 .system/ledger.tsv（一行一事件，只記 deny、unparsed、unresolved、guard_error）、
guard-status 一機一檔每日一次、hook-errors.log。所有寫入失敗都吞掉，留痕不能讓守門本身掛掉。
ledger.tsv 與 hook-errors.log 的追加跟 kb folders purge 的讀改寫用同一把鎖（<檔>.lock，manage 第二版第二輪修），
追加這邊只等 APPEND_LOCK_WAIT 秒，等不到照樣追加（hook 有時限；那時 purge 剛好在改寫，最多掉這一行，跟沒鎖時一樣）。
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
APPEND_LOCK_WAIT = 2


def _append_line(target, text, header=None):
    """拿鎖（等不到就不拿）後追加一行；header 給了且檔不存在先寫表頭。失敗丟 OSError 給呼叫端吞。"""
    from kb._lib.io import FileLock
    lock = FileLock(target, timeout=APPEND_LOCK_WAIT)
    try:
        lock.__enter__()
        held = True
    except OSError:
        held = False
    try:
        new = not target.exists()
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            if header and new:
                f.write(header + "\n")
            f.write(text + "\n")
    finally:
        if held:
            lock.__exit__(None, None, None)


_LINE_BREAKS = re.compile("[\t\r\n\x0b\x0c\x1c\x1d\x1e\x85  ]")  # str.splitlines 會斷的全部（L1）


def _clean(text):
    return _LINE_BREAKS.sub(" ", str(text or ""))


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def fallback_error_log():
    return Path(tempfile.gettempdir()) / FALLBACK_ERROR_LOG


def log_error(vault, text, source="guard"):
    """追加一行 `時間\\t<source>\\t訊息`，source 是哪支 hook（guard／turn／output_log）。找不到 vault 就寫到 %TEMP%。"""
    target = (Path(vault) / ERROR_LOG_REL) if vault else fallback_error_log()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _append_line(target, f"{_now()}\t{_clean(source)}\t{_clean(text)[:200]}")
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
        _append_line(target, line, "\t".join(HEADER))
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
