"""SubagentStart／SubagentStop 入口（card-maker，D-195、B-032）：python subagent.py start|stop。

start：agent_type 是 wiki-with-willy:card-maker 才動作。batches.json 的 agents 記 agent_id → 回合（這個 session open 的 job 的回合，
沒有 job 取目前回合），守門照它把子代理的寫入算回派工那一句；回 additionalContext：開場段的「kb 指令」那行加 rules/core.md 全文
（跟 session.py 同一組函式，不另抄）。hooks.json 的 matcher 也只認 card-maker（不帶 matcher 連內建子代理也注入，spike 第 1 題）。
stop：那個子代理記上的 open 批次改 done、agents 那筆記 stopped，再對它的回合補工作日誌的「改了」（journal.refresh_changed）。
不在知識庫裡不動作。stdin 先照 UTF-8 讀（B-093）；任何例外寫 .system/hook-errors.log（source subagent），永遠 exit 0。
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
sys.path.insert(0, HERE)

AGENT_TYPE = "wiki-with-willy:card-maker"


def main(event):
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        if payload.get("agent_type") != AGENT_TYPE or not payload.get("agent_id"):
            return
        from kb import guard

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return
        from kb._lib import batches

        sid, aid = payload.get("session_id"), payload.get("agent_id")
        if event == "start":
            batches.agent_start(vault, sid, aid)
            emit(context(vault))
        elif event == "stop":
            tid = batches.agent_stop(vault, sid, aid)
            if tid:
                from kb._lib import journal

                journal.refresh_changed(vault, tid)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def context(vault):
    """開場段同一組：kb 指令那行、空一行、core.md 全文。"""
    import session

    return session.line_kb(vault) + "\n\n" + session.read_core()


def emit(text):
    out = {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": text}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="subagent")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
    sys.exit(0)
