"""UserPromptSubmit 回合入口：客戶每說一句話開一個新回合（D-074），之後這句話造成的改動都備份到同一個回合夾。

不開回合的情況：不在知識庫裡；prompt 開頭是 <task-notification（I-035）、<cross-session-message、<system-reminder（L-3）、
<agent-message（桌面版背景子代理交回報告，card-maker 桌面版 D2）、[SYSTEM NOTIFICATION（VS Code 擴充套件在通知前多的那行）；
payload 有 source 且不是 user／sdk。
工作日誌（D-083）：開新回合時替這個 session 上一句沒等到 Stop 的回合補記「中斷」，並寫這一句的第一次；
同月一把鎖（hook 整支要在 5 秒內），失敗只寫 log，回合照開。
停了就不再自動整理（D-156、D-200）：背景整理（card-maker）客戶說停，AI 跑 kb cards stop，那支在目前回合記 ingest_stopped；
主對話自己整理時按了停止鈕：上一句是中斷補記的、且那句的回合檔記了 ingest（kb cards todo 跑過）→ 這一句的回合檔記
ingest_stopped，印一次 additionalContext。之後每句把 ingest_stopped 帶下去，同一個 session 不再印。
其餘不輸出。任何例外寫 .system/hook-errors.log，永遠 exit 0，不能擋住客戶提問。
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
NOT_A_PROMPT = ("<task-notification", "<cross-session-message", "<system-reminder", "<agent-message", "[SYSTEM NOTIFICATION")
STOPPED = "上一句的整理被你停了：這個對話不再自動整理，你說整理才做；回答用現有的資料，末尾一句還有幾份沒整理。"


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
        _clean_secrets(vault, payload)  # 上一句的 Stop 沒跑到（中斷）時留下的密碼檔暫存，先刪（密碼與守門）
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
        if sid and previous:
            try:
                _ingest_stopped(vault, previous)
            except BaseException as e:  # noqa: BLE001  沒提醒到只是多整理一次，回合照開
                _log(e, payload)
            try:
                _secret_carried(vault, previous)
            except BaseException as e:  # noqa: BLE001  沒帶到還有 Stop 的遮值與存值比對，回合照開
                _log(e, payload)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _ingest_stopped(vault, previous):
    """上一句已記過 ingest_stopped：帶到這一句、不印。上一句沒等到 Stop 且跑過 cards todo：記下並印一次。"""
    from kb._lib import turn
    if previous.get("ingest_stopped"):
        turn.mark(vault, "ingest_stopped")
        return
    if previous.get("done", True) or not previous.get("ingest"):
        return
    turn.mark(vault, "ingest_stopped")
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": STOPPED}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _secret_carried(vault, previous):
    """上一句沒答完（客戶在回答中途插話、沒等到 Stop）又記了 secret_shown：帶到這一句，答出密碼的回答落在這一句也不記
    （桌面版確認落差 D，跟 ingest 旗標一樣）。上一句答完了就不帶。"""
    from kb._lib import turn
    if previous.get("secret_shown") and not previous.get("done", True):
        turn.mark(vault, "secret_shown")


def _clean_secrets(vault, payload):
    """刪 .system/temp/*.secret*；刪不掉的寫 hook-errors.log，不擋這一句。"""
    try:
        from kb import convert
        from kb._lib import ledger

        failed = convert.clean_secrets(vault)
        if failed:
            ledger.log_error(vault, f"密碼檔暫存刪不掉：{len(failed)} 個", source="turn")
    except BaseException as e:  # noqa: BLE001
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
