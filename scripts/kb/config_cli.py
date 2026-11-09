"""kb config show／set／reset：使用者說一句就改的門檻與頻率（放寬甲 D-322 第 2 件）。

show 列使用者可調的鍵（USER_KEYS，在 _lib/config.py）：現值、預設、_why 的白話；--json 回 {"keys": [{"key","value","default","why"}]}。
set <鍵> <值>：型別照預設檢查（數字、true／false），寫 .system/config.json（寫前備進回合）；不在 USER_KEYS 的鍵
（憑證走訪上限、指紋上限、逾時…）回「「{鍵}」是保護用的，不給改」。reset <鍵> 拿掉覆寫、回預設。
終端機跑的記一段工作日誌程式段（D-085），command 是 config set／config reset。
"""
import argparse
import json
from pathlib import Path

from kb import journal_cli
from kb._lib import config, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
PROTECTED = "「{key}」是保護用的，不給改"
BAD_VALUE = "「{key}」要填{kind}，「{value}」不行"
CONFIG_POSIX = ".system/config.json"
KIND_WORD = {int: "整數", float: "數字", bool: "true 或 false", str: "文字"}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="config_command", metavar="<子子命令>", required=True)
    sh = subs.add_parser("show", help="列出可以調的門檻", description="列使用者可調的鍵：現值、預設、白話說明。")
    sh.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    st = subs.add_parser("set", help="改一個門檻", description="改一個使用者可調的鍵（寫 .system/config.json，寫前備份）。")
    st.add_argument("key", help="鍵名（kb config show 看得到）")
    st.add_argument("value", help="新的值")
    st.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    rs = subs.add_parser("reset", help="改回預設", description="拿掉這個鍵的覆寫，回到預設值。")
    rs.add_argument("key", help="鍵名")
    rs.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _rows(vault):
    base = config.defaults()
    return [{"key": k, "value": config.get(vault, k), "default": base[k], "why": base.get(f"_why_{k}", "")}
            for k in config.USER_KEYS if k in base]


def _parse(default, raw):
    """把命令列的字串照預設的型別轉成值；轉不了回 None。bool 不當 int。"""
    text = raw.strip()
    if isinstance(default, bool):
        low = text.lower()
        return True if low in ("true", "yes", "1", "是") else False if low in ("false", "no", "0", "否") else None
    try:
        if isinstance(default, int):
            return int(text) if text.lstrip("-").isdigit() else None
        if isinstance(default, float):
            return float(text)
    except ValueError:
        return None
    return text if isinstance(default, str) else None


def _client(vault):
    got = kio.read_json(Path(vault) / config.CLIENT_REL, default={})
    return got if isinstance(got, dict) else {}


def _write(vault, data, turn_id):
    turn.backup(vault, CONFIG_POSIX, turn_id)
    kio.write_json_atomic(Path(vault) / config.CLIENT_REL, data)


def _show(vault, as_json):
    rows = _rows(vault)
    if as_json:
        print(json.dumps({"keys": rows}, ensure_ascii=False))
        return 0
    for r in rows:
        mark = "" if r["value"] == r["default"] else "（改過）"
        print(f"{r['key']} = {r['value']}（預設 {r['default']}）{mark}：{r['why']}")
    return 0


def _set(vault, key, raw, as_json, conversation):
    base = config.defaults()
    if key not in config.USER_KEYS or key not in base:
        print(PROTECTED.format(key=key))
        return 3
    value = _parse(base[key], raw)
    if value is None:
        print(BAD_VALUE.format(key=key, kind=KIND_WORD.get(type(base[key]), "照預設的型別"), value=raw))
        return 3
    turn_id = turn.current(vault)["turn"]
    with kio.FileLock(Path(vault) / config.CLIENT_REL):
        data = _client(vault)
        data[key] = value
        _write(vault, data, turn_id)
    line = f"已把「{key}」改成 {value}（預設 {base[key]}）；說退回上一版改得回去"
    journal_cli.record_program_or_warn(vault, line, [{"path": CONFIG_POSIX, "action": "modify"}], "config set", conversation)
    print(json.dumps({"key": key, "value": value, "default": base[key], "message": line}, ensure_ascii=False)
          if as_json else line)
    return 0


def _reset(vault, key, as_json, conversation):
    base = config.defaults()
    if key not in config.USER_KEYS or key not in base:
        print(PROTECTED.format(key=key))
        return 3
    turn_id = turn.current(vault)["turn"]
    with kio.FileLock(Path(vault) / config.CLIENT_REL):
        data = _client(vault)
        had = key in data
        if had:
            data.pop(key)
            _write(vault, data, turn_id)
    line = f"「{key}」改回預設 {base[key]}" if had else f"「{key}」本來就是預設 {base[key]}"
    if had:
        journal_cli.record_program_or_warn(vault, line, [{"path": CONFIG_POSIX, "action": "modify"}], "config reset",
                                           conversation)
    print(json.dumps({"key": key, "value": base[key], "default": base[key], "message": line}, ensure_ascii=False)
          if as_json else line)
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.config_command
    try:
        if command == "show":
            return _show(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        if command == "set":
            return _set(vault, args.key.strip(), args.value, as_json, conversation)
        return _reset(vault, args.key.strip(), as_json, conversation)
    except TimeoutError:
        print("等不到鎖，沒有寫入")
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
