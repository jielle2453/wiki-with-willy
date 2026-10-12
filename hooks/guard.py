"""PreToolUse 守門入口。讀 stdin 的 payload，交給 kb.guard.decide，印 allow／deny 的 JSON 或不印，永遠 exit 0。

守門內部出錯：寫 .system/hook-errors.log 一行；檔案工具印 deny（fail closed，D-034、I-101），Bash／PowerShell 不印。
永不以非 0 結束，永不丟例外到 Claude Code。
hook 不是從 scripts/ 跑的，所以這裡動 sys.path；這是 kb.py 之外唯一准動 sys.path 的地方（CLAUDE.md 三）。
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

FILE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
ERROR_REASON = "守門出錯，這次不放行。說「檢查」我看原因"


def emit(decision, reason):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    payload = {}
    tool = ""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        tool = str(payload.get("tool_name", "") or "")
        from kb import guard
        from kb._lib import ledger

        d = guard.decide(payload)
        if d.vault is not None:
            for row in d.ledger_rows:
                ledger.append(d.vault, row)
            ledger.touch_status(d.vault)
        if d.decision in ("allow", "deny"):
            emit(d.decision, d.reason)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _on_error(e, payload, tool)


def _on_error(e, payload, tool):
    vault = None
    try:
        from kb import guard
        from kb._lib import ledger

        vault = guard.find_vault((payload or {}).get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}")
        if vault is not None:
            head = ""
            ti = (payload or {}).get("tool_input") or {}
            if isinstance(ti, dict):
                head = ti.get("command") or ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
            ledger.append(vault, {"tool": tool, "decision": "pass" if tool in SHELL_TOOLS else "deny", "zone": "",
                                  "reason_code": "guard_error", "head": head})
    except BaseException:  # noqa: BLE001
        pass
    if tool not in SHELL_TOOLS:
        try:
            emit("deny", ERROR_REASON)
        except BaseException:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
