"""SessionEnd（B 段第 6 步）：對話結束（關視窗、/exit、/clear、/resume 換走、登出）時，把那個對話 open 的整理 job 與批次放掉
（batches.release_ended：同接手建置，但不記 revoked、不記 stopped_turn），項目回到可派，新開的對話不用等認領過期。
當掉、強制關收不到這個事件的，靠批次的 last_seen（card_batch_idle_minutes）保底。
Claude Code 給 SessionEnd 的時間預設 1.5 秒、plugin 的 timeout 拉不長：只 import batches，不碰別的狀態檔。
沒有決定權、輸出會被丟掉：任何例外寫 .system/hook-errors.log（source session_end），永遠 exit 0、不輸出。
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


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        sid = payload.get("session_id")
        if not sid:
            return
        from kb import guard

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return
        from kb._lib import batches

        batches.release_ended(vault, sid)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is not None:
            ledger.log_error(vault, f"{type(e).__name__}: {e}", source="session_end")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
