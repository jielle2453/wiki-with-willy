"""PreToolUse 守門入口。讀 stdin 的 payload，交給 kb.guard.decide，印 allow／deny／ask 的 JSON 或不印，永遠 exit 0。

守門內部出錯：寫 .system/hook-errors.log 一行；知識庫裡一律印 deny（fail closed，D-034、I-101；Bash／PowerShell 也擋，
密碼與守門 D-186 第 4 點），知識庫外不表態。hook 本身起不來或逾時由 hooks.json 的 onFailure: block 擋。
事務層：檔案工具寫 Z1／Z3／Z5 放行後先 kb._lib.turn.before_write 備份；大改動（big_change）或備份失敗（backup_failed）改印 deny。
子代理（card-maker，D-195）：payload 帶 agent_id、batches.json 的 agents 有它 → 檔案工具寫入的備份與大改動計數算它記的回合；
放行的單一 kb 呼叫回 allow 加 updatedInput（整個 tool_input 照抄、只換 command：kb.py 路徑後面插 --turn <回合> --agent <id>）。
主對話、沒記過的子代理照舊；kb.guard 的判斷不改，插的值照 plan.shell_arg 加引號，給不出就不改寫。
永不以非 0 結束，永不丟例外到 Claude Code。
hook 不是從 scripts/ 跑的，所以這裡動 sys.path；hooks/*.py 是唯一准動 sys.path 的地方（CLAUDE.md 三）。
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
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
BACKUP_ZONES = ("Z1", "Z3", "Z5")  # 綜合卡、AI 產出、檔案索引：寫前回合備份（D-046 補）
ERROR_REASON = "守門出錯，這次不放行。說「檢查」我看原因"


def emit(decision, reason, updated=None):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    if updated is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    payload = {}
    tool = ""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        tool = str(payload.get("tool_name", "") or "") if isinstance(payload, dict) else ""
        from kb import guard
        from kb._lib import ledger

        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        d = guard.decide(payload)
        rows = list(d.ledger_rows)
        decision, reason = d.decision, d.reason
        updated = None
        agent_turn = None
        if decision == "allow" and d.vault is not None and payload.get("agent_id"):
            from kb._lib import turn as turn_lib
            agent_turn = turn_lib.agent_turn(d.vault, payload.get("session_id"), payload.get("agent_id"))
        if agent_turn and tool in SHELL_TOOLS and d.reason == guard.KB_CALL_REASON:
            updated = with_turn(payload.get("tool_input") or {}, agent_turn, payload.get("agent_id"))
        if decision == "allow" and tool in WRITE_TOOLS and d.zone in BACKUP_ZONES and d.vault is not None:
            # 事務層：寫綜合卡、AI 產出、檔案索引放行前先回合備份；大改動或備份失敗就改 deny（D-074、D-075）
            from kb._lib import turn

            ti = payload.get("tool_input") or {}
            target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
            ok, code, why = turn.before_write(d.vault, str(target), payload.get("cwd") or os.getcwd(), d.zone,
                                              payload.get("session_id"), agent_turn)
            if not ok:
                decision, reason = "deny", why
                rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": code, "head": str(target)})
        if d.vault is not None:
            try:
                for row in rows:
                    ledger.append(d.vault, row)
                ledger.touch_status(d.vault)
            except BaseException as e:  # noqa: BLE001  留痕失敗不改決定
                ledger.log_error(d.vault, f"{type(e).__name__}: {str(e)[:200]}")
        if decision in ("allow", "deny", "ask"):  # ask：開新口子的密碼動作，Claude Code 跳允許詢問（D-212）
            emit(decision, reason, updated if decision == "allow" else None)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _on_error(e, payload, tool)


def with_turn(tool_input, turn_id, agent_id):
    """單一 kb 呼叫的 tool_input 照抄，command 在 kb.py 路徑後面插 --turn <回合> --agent <id>；插不了（認不出路徑、
    值給不出引號）回 None，照原樣放行。"""
    from kb._lib import cmdparse, plan

    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    m = cmdparse._PYTHON.match(command)
    t, a = plan.shell_arg(turn_id), plan.shell_arg(agent_id)
    if m is None or t is None or a is None:
        return None
    at = m.end(2)
    return dict(tool_input, command=f"{command[:at]} --turn {t} --agent {a}{command[at:]}")


def _on_error(e, payload, tool):
    vault = None
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}")
        if vault is not None:
            head = ""
            ti = payload.get("tool_input") or {}
            if isinstance(ti, dict):
                head = ti.get("command") or ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
            ledger.append(vault, {"tool": tool, "decision": "deny", "zone": "", "reason_code": "guard_error", "head": head})
    except BaseException:  # noqa: BLE001
        pass
    if vault is None:
        return  # 不在知識庫裡：任何錯誤一律不表態，只留 %TEMP% 的錯誤檔（P2）
    try:
        emit("deny", ERROR_REASON)
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
