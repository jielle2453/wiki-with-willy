"""Stop 工作日誌入口（D-083）：AI 答完一句話，把這回合那一行補完：結果、做了、改了、done。

結果取 payload 的 last_assistant_message，壓成一行、遮值再截（D-061、D-086）；沒有這個鍵（舊版 Claude Code）寫固定句，不解析 transcript。
回合記了 secret_shown（這句回答給了密碼，密碼與守門）就只記固定句，不碰回答。
日誌寫成後才 set_done；md 渲染失敗只寫 log，不擋 set_done。
做了：模型叫過 kb turn note 就用它（note_by ai），否則程式地板句（note_by program，D-084）；改了：這回合的 manifest。
只收第一次 Stop：回合檔 done 已是 true 就不做；turns/<sid>.json 不存在也不做，不退到 current.json（會記到別的對話）。
stop_hook_active 不理：從不 block。任何例外寫 .system/hook-errors.log（source stop），永遠 exit 0、不輸出。
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

NO_MESSAGE = "（這版 Claude Code 沒給最後回答）"
SECRET_RESULT = "（這句回答有密碼，沒記）"  # 密碼與守門：回合記了 secret_shown（kb unlock get、讀「可以讀」的密碼檔）


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        from kb import guard

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        if vault is None:
            return
        sid = payload.get("session_id")
        if not sid:
            return
        from kb._lib import config, journal, turn

        data = turn._read_turn(turn._session_file(vault, sid))
        if not data or data.get("done"):
            return
        message = payload.get("last_assistant_message")
        if data.get("secret_shown"):
            result, truncated = SECRET_RESULT, False
        elif isinstance(message, str):  # cut_result 先抽段、壓成一行、遮值、再截（M2）
            result, truncated = journal.cut_result(message, config.get(vault, "journal_result_max"), vault)
        else:
            result, truncated = NO_MESSAGE, False
        note, note_by, changed = journal.finish_fields(vault, data)
        journal.complete_turn(vault, data, result, note, note_by, changed, interrupted=False, result_truncated=truncated)
        turn.set_done(vault, sid)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="stop")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
