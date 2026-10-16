"""SessionStart 開場注入入口（D-087、D-060）：對話一開始、resume、clear、摺疊後，把狀態行與 rules/core.md 全文帶進 additionalContext。

狀態行固定順序、回 None 的不印；第一行固定以「【wiki-with-willy】」開頭，知識庫 CLAUDE.md 的哨兵句認這個字串（D-088）。
輸出 ensure_ascii=True：純 ASCII 不碰 cp950（I-001）。不碰 guard-status、ledger、回合、日誌。
任何例外寫 .system/hook-errors.log（source session），不輸出，永遠 exit 0。
hook 不是從 scripts/ 跑的，所以這裡動 sys.path（hooks/*.py 是唯一例外，CLAUDE.md 三）。
"""
import json
import os
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

COMPACT_LINE = "對話剛摺疊，規則已重新帶入；做到一半的事以工作日誌為準。"


def line_kb(vault):
    """永遠有值：kb 指令的完整呼叫前綴（D-013、D-019 不寫進檔，只在這裡印）。"""
    from kb._lib import paths

    return f'【wiki-with-willy】kb 指令：python "{paths.plugin_root().as_posix()}/scripts/kb.py" <子命令>'


def line_version(vault):
    """等 upgrade 題。"""
    return None


def line_changes(vault):
    """等清冊題。"""
    return None


def line_agenda(vault):
    """等日程題。"""
    return None


def line_backup(vault):
    """等備份題。"""
    return None


def line_health(vault):
    """等 lint 題。"""
    return None


def line_compact(vault, source):
    """本題：只在摺疊後印。"""
    return COMPACT_LINE if source == "compact" else None


def status_lines(vault, source):
    lines = [line_kb(vault), line_version(vault), line_changes(vault), line_agenda(vault),
             line_backup(vault), line_health(vault), line_compact(vault, source)]
    return [x for x in lines if x is not None]


def read_core():
    from kb._lib import paths

    return (paths.plugin_root() / "rules" / "core.md").read_text(encoding="utf-8")


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        from kb import guard

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return
        context = "\n".join(status_lines(vault, payload.get("source"))) + "\n\n" + read_core()
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
        sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="session")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
