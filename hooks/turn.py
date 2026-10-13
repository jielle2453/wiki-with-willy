"""UserPromptSubmit 回合入口：客戶每說一句話開一個新回合（D-074），之後這句話造成的改動都備份到同一個回合夾。

不開回合的情況：不在知識庫裡；prompt 是背景代理的 <task-notification（I-035）；payload 有 source 且不是 user／sdk。
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
        if str(prompt).lstrip().startswith("<task-notification"):
            return
        turn.start(vault, payload.get("session_id"), prompt)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"turn {type(e).__name__}: {str(e)[:200]}")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
