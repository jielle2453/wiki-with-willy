"""UserPromptSubmit 回合入口：客戶每說一句話開一個新回合（D-074），之後這句話造成的改動都備份到同一個回合夾。

不開回合的情況：不在知識庫裡；prompt 開頭是 <task-notification（I-035）、<cross-session-message、<system-reminder（L-3）；
payload 有 source 且不是 user／sdk。
工作日誌（D-083）：開新回合時替這個 session 上一句沒等到 Stop 的回合補記「中斷」，並寫這一句的第一次；
同月一把鎖（hook 整支要在 5 秒內），失敗只寫 log，回合照開。
任何例外寫 .system/hook-errors.log，永遠 exit 0、不輸出，不能擋住客戶提問。
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

USER_SOURCES = ("user", "sdk")
# 不是客戶說的話：背景代理通知（I-035）、跨 session 訊息與系統提醒（確認後裁定 L-3），都不開回合
NOT_A_PROMPT = ("<task-notification", "<cross-session-message", "<system-reminder")


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        from kb import guard
        from kb._lib import turn

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return
        source = payload.get("source")
        if source is not None and source not in USER_SOURCES:
            return
        prompt = payload.get("prompt")
        if prompt is None:
            prompt = payload.get("user_prompt") or ""
        if str(prompt).lstrip().startswith(NOT_A_PROMPT):
            return
        from kb._lib import journal

        sid = payload.get("session_id")
        previous = None
        if sid:
            try:
                previous = turn._read_turn(turn._session_file(vault, sid))  # start 會換掉它，先讀
            except BaseException as e:  # noqa: BLE001  讀不到上一句只少補記，回合照開
                _log(e, payload)
        data = turn.start(vault, sid, prompt)
        try:
            journal.begin_turn(vault, previous, data)  # 補記上一句的中斷與這一句開口，同月一把鎖（L1）
        except BaseException as e:  # noqa: BLE001  日誌失敗不影響開回合
            _log(e, payload)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="turn")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
