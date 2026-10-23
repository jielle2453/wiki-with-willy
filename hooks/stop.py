"""Stop 工作日誌入口（D-083）：AI 答完一句話，把這回合那一行補完：結果、做了、改了、done。

結果取 payload 的 last_assistant_message，壓成一行、遮值再截（D-061、D-086）；沒有這個鍵（舊版 Claude Code）寫固定句，不解析 transcript。
回合記了 secret_shown（這句回答給了密碼，密碼與守門）就只記固定句，不碰回答。
回答裡出現這個知識庫存在憑證管理員的值（4 字以上，一模一樣）也只記固定句；讀不到憑證管理員跳過、寫 log。
收尾一律刪 .system/temp/*.secret*（「可以讀」的密碼檔的暫存），刪不掉寫 log。
日誌寫成後才 set_done；md 渲染失敗只寫 log，不擋 set_done。
做了：模型叫過 kb turn note 就用它（note_by ai），否則程式地板句（note_by program，D-084）；改了：這回合的 manifest。
只收第一次 Stop：回合檔 done 已是 true 就不做；turns/<sid>.json 不存在也不做，不退到 current.json（會記到別的對話）。
整批製卡（card-maker，D-195）：這個 session open 或剛關的 job 回合補「改了」（journal.refresh_changed，背景後來寫的才算得到）；
job 關了之後的第一次 Stop 把派工那句的結果換成這次的最後回答，只換一次（那個回合記 job_result）；那個回合或這一句記了 secret_shown
或這次的回答有存著的密碼值就不寫結果（同上），只補改了。這段不管這回合 done 了沒都做，各自 try。
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
        _clean_secrets(vault)
        sid = payload.get("session_id")
        if not sid:
            return
        from kb._lib import turn

        message = payload.get("last_assistant_message")
        data = turn._read_turn(turn._session_file(vault, sid))
        try:
            if data and not data.get("done"):
                _finish(vault, sid, data, message, payload)
            elif data:  # 已經答完的那句（背景整理的通知回合）：主對話後來跑的 kb 改了的照補（card-maker 第一輪修）
                from kb._lib import journal
                journal.refresh_changed(vault, data["turn"])
        finally:
            _jobs(vault, sid, message, payload, data)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _finish(vault, sid, data, message, payload):
    """這一句答完：補完日誌那一行、回合記 done（只收第一次）。"""
    from kb._lib import config, journal, turn

    if data.get("secret_shown"):
        result, truncated = SECRET_RESULT, False
    elif isinstance(message, str) and _has_saved_value(vault, message, payload):
        result, truncated = SECRET_RESULT, False  # 回答裡有存著的密碼值本身（沒有關鍵字也算）：整欄不記
    elif isinstance(message, str):  # cut_result 先抽段、壓成一行、遮值、再截（M2）
        result, truncated = journal.cut_result(message, config.get(vault, "journal_result_max"), vault)
    else:
        result, truncated = NO_MESSAGE, False
    note, note_by, changed = journal.finish_fields(vault, data)
    journal.complete_turn(vault, data, result, note, note_by, changed, interrupted=False, result_truncated=truncated)
    turn.set_done(vault, sid)


def _jobs(vault, sid, message, payload, here=None):
    """card-maker 的 job 回合：補改了；關了的換一次結果。這一句（here）記了 secret_shown、派工那句記了、或回答有存著的密碼值，
    都不換結果、只補改了（第一輪修 S1：值單獨出現時遮值擋不到）。出錯寫 log，不擋。"""
    try:
        from kb._lib import batches, config, journal, turn

        for job, closed in batches.job_turns(vault, sid):
            tid = job["turn"]
            journal.refresh_changed(vault, tid)
            if not closed:
                continue
            data = turn.turn_data(vault, tid)
            if data is None:
                continue
            secret = (data.get("secret_shown") or (here or {}).get("secret_shown")
                      or (isinstance(message, str) and _has_saved_value(vault, message, payload)))
            if not secret:
                if isinstance(message, str):
                    result, truncated = journal.cut_result(message, config.get(vault, "journal_result_max"), vault)
                else:
                    result, truncated = NO_MESSAGE, False
                journal.update_turn(vault, turn.journal_key(data), result=result, result_truncated=truncated)
            turn.change_turn(vault, tid, lambda d: d.update(job_result=True) or True)
    except BaseException as e:  # noqa: BLE001
        _log(e, payload)


def _has_saved_value(vault, message, payload):
    """縱深防禦（桌面版確認後）：這個知識庫存在憑證管理員的值（4 個字以上），回答裡出現一模一樣的字串就回 True。
    AI 只寫值、沒寫「密碼」這類字時遮值擋不到，靠這個。讀不到憑證管理員（不是 Windows、壞了）跳過、寫 log，不擋。"""
    try:
        from kb._lib import unlock

        return any(value in message for value in unlock.saved_values(vault))
    except BaseException as e:  # noqa: BLE001  比不到只少一層，照常寫日誌
        _log(e, payload)
        return False


def _clean_secrets(vault):
    """收尾一律刪 .system/temp/*.secret*（「可以讀」的密碼檔的暫存，密碼與守門）；刪不掉的寫 hook-errors.log，不擋。"""
    try:
        from kb import convert
        from kb._lib import ledger

        failed = convert.clean_secrets(vault)
        if failed:
            ledger.log_error(vault, f"密碼檔暫存刪不掉：{len(failed)} 個", source="stop")
    except BaseException as e:  # noqa: BLE001  刪不掉不能讓 Stop 掛掉
        _log(e, {"cwd": str(vault)})


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
