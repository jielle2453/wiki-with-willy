"""PreToolUse 守門入口。讀 stdin 的 payload，交給 kb.guard.decide，印 allow／deny／ask 的 JSON 或不印，永遠 exit 0。

守門內部出錯：寫 .system/hook-errors.log 一行；知識庫裡一律印 deny（fail closed，D-034、I-101；Bash／PowerShell 也擋，
密碼與守門 D-186 第 4 點），知識庫外不表態。hook 本身起不來或逾時由 hooks.json 的 onFailure: block 擋。
事務層：檔案工具寫 Z1／Z4／Z5 放行後先 kb._lib.turn.before_write 備份；大改動（big_change）或備份失敗（backup_failed）改印 deny。
子代理（card-maker，D-195）：payload 帶 agent_id、batches.json 的 agents 有它 → 檔案工具寫入的備份與大改動計數算它記的回合；
放行的單一 kb 呼叫回 allow 加 updatedInput（整個 tool_input 照抄、只換 command：kb.py 路徑後面插 --turn <回合> --agent <id>）。
主對話、沒記過的子代理照舊；kb.guard 的判斷不改，插的值照 plan.shell_arg 加引號，給不出就不改寫。
card-maker 第一輪修：記過的子代理只准白名單裡的 kb 子命令（AGENT_ALLOW；money add／settle／attach 沒帶 --card、帶 --from-statement 擋，記帳 2；convert 不收 --password／--saved／--explicit，entity tag 與 entity field 不收 --said／--from-card，記帳 1b），
也不准讀 .system/unlock.json 與 .system/temp 的密碼檔暫存，其餘 deny「製卡助手不能做這件事」（S3）；指令自己帶了
--turn／--agent／--session 的（子代理在哪都算，主對話看子命令前的）deny，不改寫（S4）；主對話（沒有 agent_id）的每個單一 kb 呼叫
插 --session <sid>（D-506：寫入記在這個對話的回合、退回只退這個對話的；兩個視窗開同一個知識庫不串），已經帶 --turn 的與 selftest 不插
（#20 裁定 1、3）；守門回 ask 的密碼動作（D-212）也一樣插，ask 帶 updatedInput（裁定 5）。
build 第一輪修 S8：記過的子代理目前認領的是讀舊 wiki（adopt）的批次時，Write／Edit 這類寫檔工具一律 deny。
build 桌面版確認修：記過的子代理用 Write／Edit／MultiEdit／NotebookEdit 寫到知識庫外面一律 deny（之前把卡寫到知識庫的上一層）。
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
sys.path.insert(0, HERE)
try:
    import _codex  # noqa: E402  Codex 轉接（codex 分支）
except BaseException:  # noqa: BLE001  轉接載不進來：main 照守門出錯處理（知識庫裡 deny），不讓 Codex 因 hook 起不來而放行（review 6）
    _codex = None

FILE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
BACKUP_ZONES = ("Z1", "Z2", "Z4", "Z5", "Z6", "Z8", "Z9")  # 放行的檔案工具寫前都先回合備份（D-046 補；放寬甲 D-306：使用者的區與客戶設定檔也備）
ERROR_REASON = "守門出錯，這次沒放行，已記在 hook-errors.log：改跑 kb selftest 看原因，修不好照核心規則請服務人員。"
AGENT_DENY = "製卡助手不能做這件事"
SELF_FLAGS = "kb 指令不能自己帶 --turn、--agent、--session 或 --via"
GLOBAL_VALUE_FLAGS = ("--turn", "--agent", "--session", "--via")  # --via 只有 Obsidian 外掛自己帶（#22，D-255），模型帶的照 SELF_FLAGS 擋
# card-maker 用得到的（agents/card-maker.md、skills/ingest/references/ 的規則檔）：(子命令, 子子命令)；子子命令 "" 是沒有子子命令的
AGENT_ALLOW = {("cards", "batch"), ("cards", "list"), ("cards", "todo"), ("cards", "wip"), ("cards", "skip"), ("convert", ""), ("version", ""),
               ("agenda", "add"), ("agenda", "edit"), ("agenda", "recheck"), ("agenda", "scanned"), ("agenda", "cancel"),
               ("agenda", "list"), ("entity", "add"), ("entity", "suggest-close"), ("entity", "clean"), ("entity", "list"),
               ("entity", "tag"), ("entity", "render"), ("entity", "log"), ("entity", "field"),
               ("money", "add"), ("money", "attach"), ("money", "settle"), ("money", "unsure"), ("money", "scanned"),
               ("money", "list"), ("money", "candidates")}  # 製卡抽錢（記帳 2）；cancel、merge、edit 這些只在主對話
NO_SUB = ("convert", "version")
CONVERT_DENY = ("--password", "--saved", "--explicit")
SECRET_FLAGS = ("--password", "--value")  # 值是密碼的旗標：改寫會把值印進 hook 的輸出（#20 裁定 5 實作疑義）
TAG_DENY = ("--said", "--from-card")  # 代表她的意思（她親口說的、她在卡上改的），只在主對話用（標籤記在實體上裁定 10；數字欄同，記帳 1b）
MONEY_NEED_CARD = (("money", "add"), ("money", "settle"), ("money", "attach"))  # 製卡寫帳一定是從卡讀到的（記帳 2 測試報告中 #1）
MONEY_DENY = ("--from-statement",)  # 補她口述的錢：主對話的事
MONEY_CARD_REASON = "製卡助手寫帳要帶 --card <這張卡的編號>；她口述的錢由主對話記"
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
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        if _codex is None:
            raise ImportError("hooks/_codex.py 載不進來")
        if not _codex.is_codex(payload):
            decision, reason, updated, _vault = _judge(payload, tool)
        else:
            decision, reason, updated = _codex_main(payload, tool)
        if decision in ("allow", "deny", "ask"):  # ask：開新口子的密碼動作，Claude Code 跳允許詢問（D-212）；Codex 不會走到 ask
            emit(decision, reason, updated if decision in ("allow", "ask") else None)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _on_error(e, payload, tool)


def _codex_main(payload, tool):
    """Codex（codex 分支）：apply_patch 逐檔判、合併；ask 換成「客戶下一句同意才放行」（_codex 檔頭）。"""
    from kb import guard
    from kb._lib import ledger

    items = _codex.normalize(payload)
    if items is None:  # 拆不出檔名的 patch：知識庫裡一律擋
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return None, "", None
        ledger.append(vault, {"tool": tool, "decision": "deny", "zone": "", "reason_code": "patch_unparsed",
                              "head": _codex.patch_text(payload.get("tool_input") or {})[:200]})
        return "deny", _codex.PATCH_REASON, None
    results = [_judge(p, str(p.get("tool_name") or "")) for p in items]
    decision, reason, updated = _codex.combine([r[:3] for r in results])
    vault = results[0][3]
    if decision == "ask" and vault is not None:
        command = (items[0].get("tool_input") or {}).get("command")
        if _codex.take_consent(vault, payload.get("session_id"), command):
            decision = "allow"
        else:
            ledger.append(vault, {"tool": tool, "decision": "deny", "zone": "", "reason_code": "consent_needed",
                                  "head": str(command or "")[:200]})
            decision, reason, updated = "deny", _codex.CONSENT_REASON + reason, None
    elif decision == "ask":
        decision, updated = "deny", None
    return decision, reason, updated


def _judge(payload, tool):
    """一份（現有格式的）payload 的判斷：回 (decision, reason, updatedInput, vault)，留痕寫好，不輸出。"""
    from kb import guard
    from kb._lib import ledger

    d = guard.decide(payload)
    rows = list(d.ledger_rows)
    decision, reason = d.decision, d.reason
    updated = None
    agent_turn = None
    if d.vault is not None and payload.get("agent_id"):
        from kb._lib import turn as turn_lib
        agent_turn = turn_lib.agent_turn(d.vault, payload.get("session_id"), payload.get("agent_id"))
    if d.vault is not None and agent_turn and tool in WRITE_TOOLS and decision != "deny" and _outside(payload, tool, d.vault):
        decision, reason = "deny", AGENT_DENY  # 子代理寫知識庫外面：一律不准（build 桌面版確認修）
        ti = payload.get("tool_input") or {}
        rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": "agent_scope",
                     "head": str(ti.get("file_path") or ti.get("notebook_path") or "")})
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
        # 事務層：寫綜合卡、暫存區、檔案索引放行前先回合備份；大改動或備份失敗就改 deny（D-074、D-075）
        from kb._lib import turn

        ti = payload.get("tool_input") or {}
        target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
        ok, code, why = turn.before_write(d.vault, str(target), payload.get("cwd") or os.getcwd(), d.zone,
                                          payload.get("session_id"), agent_turn)
        if not ok:
            decision, reason = "deny", why
            rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": code, "head": str(target)})
    if decision == "ask" and d.vault is not None and tool in SHELL_TOOLS and not agent_turn:
        tokens = kb_args((payload.get("tool_input") or {}).get("command"), tool)
        if tokens is not None and _has_flag(split_global(tokens)[0]):
            decision, reason = "deny", SELF_FLAGS  # 跟 allow 那條一樣：自己帶 --turn／--agent／--session 的不准（#20 第二輪 review）
            rows.append({"tool": tool, "decision": "deny", "zone": d.zone, "reason_code": "agent_scope",
                         "head": str((payload.get("tool_input") or {}).get("command") or "")})
        else:
            updated = with_session(payload, tool, tokens)  # 密碼動作也記在這個對話的回合（secret_shown 標對句，#20 裁定 5）
    if decision in ("allow", "ask") and d.vault is not None and tool in SHELL_TOOLS and not agent_turn:
        _mark_secret(d.vault, payload, tool)
    if d.vault is not None:
        try:
            for row in rows:
                ledger.append(d.vault, row)
            ledger.touch_status(d.vault)
        except BaseException as e:  # noqa: BLE001  留痕失敗不改決定
            ledger.log_error(d.vault, f"{type(e).__name__}: {str(e)[:200]}")
    return decision, reason, updated, d.vault


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


def _positionals(tokens, sub, value_flags=("--vault",)):
    """子子命令 sub 後面不是旗標、也不是旗標值的字（--vault x 的 x 不算；-- 之後的都算）。"""
    rest = tokens[tokens.index(sub) + 1:] if sub in tokens else []
    out, skip, dash = [], False, False
    for t in rest:
        if dash:
            out.append(t)
        elif t == "--":
            dash = True
        elif skip:
            skip = False
        elif t.startswith("-"):
            skip = t in value_flags
        else:
            out.append(t)
    return out


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
    if agent_turn and tool in WRITE_TOOLS and _adopt_agent(d.vault, payload.get("agent_id")):
        return AGENT_DENY, None  # 讀舊 wiki 的批次不寫任何檔
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
        if (cmd, sub) in (("entity", "tag"), ("entity", "field")) and _has_flag(tokens, TAG_DENY):
            return AGENT_DENY, None
        if cmd == "money" and _has_flag(tokens, MONEY_DENY):
            return AGENT_DENY, None
        if (cmd, sub) in MONEY_NEED_CARD and not _has_flag(tokens, ("--card",)):
            return MONEY_CARD_REASON, None
        if (cmd, sub) == ("entity", "render") and not _positionals(tokens, sub):
            return AGENT_DENY, None  # 不帶名稱是重寫全部綜合卡，製卡助手用不到（D-521 裁定 18）
        return None, with_turn(ti, agent_turn, payload.get("agent_id"))
    if tokens is None:
        return None, None
    if _has_flag(split_global(tokens)[0]):
        return SELF_FLAGS, None
    return None, with_session(payload, tool, tokens)


def with_session(payload, tool, tokens):
    """主對話（沒有 agent_id、有 session_id）的單一 kb 呼叫插 --session <sid>（D-506：每個呼叫都記在這個對話的回合）；
    帶 --turn 的照它（#20 裁定 1）、selftest 不碰回合也要走 cli.main 不建 parser 的那條路（裁定 3），都不插。
    子命令前自己帶 --turn／--agent／--session、帶密碼值（--password、--value）、拆不開（tokens 是 None）、插不了回 None。"""
    ti = payload.get("tool_input") or {}
    sid = payload.get("session_id")
    if payload.get("agent_id") or not sid:
        return None
    if tokens is None:
        return None
    head, cmd, _sub = split_global(tokens)
    if _has_flag(head) or cmd == "selftest" or _has_flag(tokens, ("--turn",)):
        return None
    if _has_flag(tokens, SECRET_FLAGS):
        return None  # updatedInput 會把整串（含密碼值）印進 hook 的輸出（D-212 守著不帶密碼值），不改寫
    return with_flags(ti, ("--session", sid))


def _mark_secret(vault, payload, tool):
    """帶密碼值（--password、--value）的主對話 kb 呼叫：指令不改寫（值不進 hook 輸出），放行或 ask 前直接替 payload 那個對話的
    目前回合標 secret_shown（#20 裁定 12），Stop 不記那句回答。多標可以接受；標不上只記 log、不改決定。"""
    from kb._lib import ledger
    sid = payload.get("session_id")  # 記過的子代理（agent_turn）呼叫端已排除；一般子代理的呼叫算主對話那句
    if not sid:
        return
    tokens = kb_args((payload.get("tool_input") or {}).get("command"), tool)
    if tokens is None or not _has_flag(tokens, SECRET_FLAGS):
        return
    try:
        from kb._lib import turn
        turn.mark_session(vault, sid, "secret_shown")
    except Exception as e:  # noqa: BLE001  標不上不擋這次呼叫
        ledger.log_error(vault, f"secret_shown 沒標到：{type(e).__name__}: {str(e)[:200]}")


def _outside(payload, tool, vault):
    """寫檔工具的目標不在知識庫裡（normalize 之後比，.. 跳出去也算）；認不出路徑當外面。"""
    from kb._lib.paths import inside, normalize

    ti = payload.get("tool_input") or {}
    target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
    if not target:
        return True
    try:
        cwd = payload.get("cwd") or os.getcwd()
        return not inside(normalize(str(target), cwd), normalize(str(vault), None))
    except (OSError, ValueError):
        return True


def _adopt_agent(vault, agent_id):
    from kb._lib import batches
    return bool(agent_id) and batches.adopt_agent(vault, agent_id)


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
