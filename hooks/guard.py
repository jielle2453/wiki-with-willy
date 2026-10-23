"""PreToolUse 守門入口。讀 stdin 的 payload，交給 kb.guard.decide，印 allow／deny／ask 的 JSON 或不印，永遠 exit 0。

守門內部出錯：寫 .system/hook-errors.log 一行；知識庫裡一律印 deny（fail closed，D-034、I-101；Bash／PowerShell 也擋，
密碼與守門 D-186 第 4 點），知識庫外不表態。hook 本身起不來或逾時由 hooks.json 的 onFailure: block 擋。
事務層：檔案工具寫 Z1／Z3／Z5 放行後先 kb._lib.turn.before_write 備份；大改動（big_change）或備份失敗（backup_failed）改印 deny。
子代理（card-maker，D-195）：payload 帶 agent_id、batches.json 的 agents 有它 → 檔案工具寫入的備份與大改動計數算它記的回合；
放行的單一 kb 呼叫回 allow 加 updatedInput（整個 tool_input 照抄、只換 command：kb.py 路徑後面插 --turn <回合> --agent <id>）。
主對話、沒記過的子代理照舊；kb.guard 的判斷不改，插的值照 plan.shell_arg 加引號，給不出就不改寫。
card-maker 第一輪修：記過的子代理只准白名單裡的 kb 子命令（AGENT_ALLOW；convert 不收 --password／--saved／--explicit），
也不准讀 .system/unlock.json 與 .system/temp 的密碼檔暫存，其餘 deny「製卡助手不能做這件事」（S3）；指令自己帶了
--turn／--agent／--session 的（子代理在哪都算，主對話看子命令前的）deny，不改寫（S4）；主對話的 cards dispatch／status／stop
插 --session <sid>（兩個視窗開同一個知識庫不串）。
第二輪修：--t、--a、--s 開頭而且是這三個旗標前綴的（--tu=、--ses、--ag=…）一樣算；記過的子代理跑 Bash／PowerShell，
不是「單一 kb 呼叫而且在白名單裡」的一律 deny（&&、管線、環境變數前綴、cd 再接、python -c 都算），客戶按過一律允許 python 也繞不過。
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
AGENT_DENY = "製卡助手不能做這件事"
SELF_FLAGS = "kb 指令不能自己帶 --turn、--agent 或 --session"
GLOBAL_VALUE_FLAGS = ("--turn", "--agent", "--session")
# card-maker 用得到的（agents/card-maker.md、carding.md）：(子命令, 子子命令)；子子命令 "" 是沒有子子命令的
AGENT_ALLOW = {("cards", "batch"), ("cards", "list"), ("cards", "todo"), ("cards", "skip"), ("convert", ""), ("version", ""),
               ("agenda", "add"), ("agenda", "edit"), ("agenda", "recheck"), ("agenda", "scanned"), ("agenda", "cancel"),
               ("agenda", "list"), ("entity", "add"), ("entity", "suggest-close"), ("entity", "clean"), ("entity", "list")}
NO_SUB = ("convert", "version")
CONVERT_DENY = ("--password", "--saved", "--explicit")
SESSION_SUBS = {("cards", "dispatch"), ("cards", "status"), ("cards", "stop")}
QUOTES = "\"'"


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
        if d.vault is not None and payload.get("agent_id"):
            from kb._lib import turn as turn_lib
            agent_turn = turn_lib.agent_turn(d.vault, payload.get("session_id"), payload.get("agent_id"))
        if d.vault is not None and agent_turn and tool in SHELL_TOOLS and decision != "deny" and decision != "allow":
            decision, reason = "deny", AGENT_DENY  # 守門沒意見或要詢問的 shell 指令：子代理一律不准（第二輪修 R3）
            rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": "agent_scope",
                         "head": str((payload.get("tool_input") or {}).get("command") or "")})
        if decision == "allow" and d.vault is not None:
            why, updated = scope(payload, tool, d, agent_turn)
            if why:
                decision, reason, updated = "deny", why, None
                ti = payload.get("tool_input") or {}
                rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": "agent_scope",
                             "head": str(ti.get("command") or ti.get("file_path") or ti.get("path") or "")})
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


def kb_args(command, tool):
    """單一 kb 呼叫裡 kb.py 路徑之後的參數；拆不開回 None。PowerShell 照非 POSIX 拆再剝引號。"""
    import shlex

    from kb._lib import cmdparse
    m = cmdparse._PYTHON.match(command or "")
    if m is None:
        return None
    try:
        tokens = shlex.split(command[m.end(2):], posix=(tool == "Bash"))
    except ValueError:
        return None
    if tool != "Bash":
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in QUOTES else t for t in tokens]
    return tokens


def split_global(tokens):
    """(子命令前的全域參數, 子命令, 子子命令)。子子命令是子命令後第一個不是 - 開頭的字；convert、version 沒有。"""
    i, head = 0, []
    while i < len(tokens) and tokens[i].startswith("-"):
        head.append(tokens[i])
        if tokens[i] in GLOBAL_VALUE_FLAGS and i + 1 < len(tokens):
            head.append(tokens[i + 1])
            i += 1
        i += 1
    cmd = tokens[i] if i < len(tokens) else ""
    sub = ""
    if cmd not in NO_SUB:
        sub = next((t for t in tokens[i + 1:] if not t.startswith("-")), "")
    return head, cmd, sub


def _flag_name(token):
    return token.split("=", 1)[0] if token.startswith("--") else None


def _has_flag(tokens, flags=GLOBAL_VALUE_FLAGS):
    """tokens 裡有沒有這些旗標：全名、全名=值，或它們的前綴（至少到第一個字母，--tu、--ses=…；argparse 預設會把縮寫當全名，
    第二輪修）。"""
    for t in tokens:
        name = _flag_name(t)
        if name and len(name) >= 3 and any(f.startswith(name) for f in flags):
            return True
    return False


def scope(payload, tool, d, agent_turn):
    """放行之後再看一層（card-maker 第一輪修 S3、S4）：回 (deny 的理由或 None, updatedInput 或 None)。"""
    from kb import guard

    ti = payload.get("tool_input") or {}
    if agent_turn and tool in FILE_TOOLS and _secret_target(payload, tool, d.vault):
        return AGENT_DENY, None
    if tool not in SHELL_TOOLS:
        return None, None
    if d.reason != guard.KB_CALL_REASON:
        return (AGENT_DENY if agent_turn else None), None  # 子代理只准單一 kb 呼叫（第二輪修 R3）
    tokens = kb_args(ti.get("command"), tool)
    if agent_turn:
        if tokens is None:
            return AGENT_DENY, None
        if _has_flag(tokens):
            return SELF_FLAGS, None
        _head, cmd, sub = split_global(tokens)
        if (cmd, sub) not in AGENT_ALLOW:
            return AGENT_DENY, None
        if cmd == "convert" and _has_flag(tokens, CONVERT_DENY):
            return AGENT_DENY, None
        return None, with_turn(ti, agent_turn, payload.get("agent_id"))
    if tokens is None:
        return None, None
    head, cmd, sub = split_global(tokens)
    if _has_flag(head):
        return SELF_FLAGS, None
    sid = payload.get("session_id")
    if not payload.get("agent_id") and sid and (cmd, sub) in SESSION_SUBS:
        return None, with_flags(ti, ("--session", sid))
    return None, None


def _secret_target(payload, tool, vault):
    """子代理讀 .system/unlock.json 或 .system/temp 的密碼檔暫存（*.secret*）嗎；Glob／Grep 指到 .system 或樣式提到它們也算。"""
    from kb._lib.paths import normalize

    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    target = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or (cwd if tool in ("Glob", "Grep") else "")
    if not target:
        return False
    t, v = normalize(str(target), cwd), normalize(str(vault), None)
    rel = t[len(v) + 1:] if t.startswith(v + "/") else ("" if t == v else None)
    if rel is None:
        return False
    if rel == ".system/unlock.json" or (rel.startswith(".system/temp/") and ".secret" in rel):
        return True
    if tool in ("Glob", "Grep"):
        pattern = f"{ti.get('pattern') or ''} {ti.get('glob') or ''}".lower()
        return rel == ".system" or rel.startswith(".system/") or "secret" in pattern or "unlock" in pattern
    return False


def with_flags(tool_input, *pairs):
    """tool_input 照抄，command 在 kb.py 路徑後面插 pairs（每對 (旗標, 值)）；插不了（認不出路徑、值給不出引號）回 None，
    照原樣放行。"""
    from kb._lib import cmdparse, plan

    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    m = cmdparse._PYTHON.match(command)
    values = [plan.shell_arg(v) for _f, v in pairs]
    if m is None or any(v is None for v in values):
        return None
    at = m.end(2)
    extra = "".join(f" {f} {v}" for (f, _v), v in zip(pairs, values))
    return dict(tool_input, command=f"{command[:at]}{extra}{command[at:]}")


def with_turn(tool_input, turn_id, agent_id):
    """子代理的單一 kb 呼叫：kb.py 路徑後面插 --turn <回合> --agent <id>（D-195）。"""
    return with_flags(tool_input, ("--turn", turn_id), ("--agent", agent_id))


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
