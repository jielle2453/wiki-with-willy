"""SessionStart 開場注入入口（D-087、D-060）：對話一開始、resume、clear、摺疊後，把狀態行與 rules/core.md 全文帶進 additionalContext。

狀態行固定順序、回 None 的不印；第一行固定以「【wiki-with-willy】」開頭，知識庫 CLAUDE.md 的哨兵句認這個字串（D-088）。
輸出 ensure_ascii=True：純 ASCII 不碰 cp950（I-001）。不碰 guard-status、ledger、回合、日誌。
放寬丙 3b（D-327）：開場只留 kb 行、核心規則、偏好、摺疊行，加「這台今天第一次」（env.json 的 daily_done 不是今天）才做的
版本、搬家、日程三行與背景健檢；這批做完記今天，之後同一天每句開頭（hooks/turn.py）不再做。變動比對、卡與標籤手改、設定檔手改、
找不到的來源、整理提示都改在每句開頭做，共用的函式在 hooks/_checks.py。不再報「還有 N 份沒整理」與收件匣份數（放寬丙 3）。
升級打包（D-215、D-217）：line_version 只用上一次查的結果（env.json 的 update_check），不連網；日期不是今天且清單網址有填就背景開
kb upgrade --background 的子行程、不等它。背景健檢（D-221、D-227）：上次健檢超過 lint_background_days 天就背景開 kb lint --background。
升級把 AI 產出併進暫存區、同名不同內容留下的（line_hold_left，放寬甲）每次開場都印到搬完為止。
core.md 之後接設定檔第六節的「客戶偏好（…）：」一條一行（D-137，沒有不印；讀不到這段不印、寫 log；框住，測試後修正裁定 15）。
任何例外寫 .system/hook-errors.log（source session），永遠 exit 0。找到知識庫之後的例外另印一行出錯的 additionalContext
（失敗明講，開場注入測試後修正 D2）；找不到知識庫、stdin 壞掉不輸出。write／flush 也包住：pipe 斷掉照樣 exit 0。
hook 不是從 scripts/ 跑的，所以這裡動 sys.path（hooks/*.py 是唯一例外，CLAUDE.md 三）。
"""
import json
import os
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)  # 共用的 _checks.py 在同一夾

import _checks  # noqa: E402

COMPACT_LINE = "對話剛摺疊，規則已重新帶入；做到一半的事以工作日誌為準。"
ERROR_LINE = "【wiki-with-willy】開場有一段出錯（{}），已記在 .system/hook-errors.log；其他照常做，跑 kb selftest 看原因。"  # D-322 第 7 件：非致命，其他照常
ERROR_LINE_FATAL = "【wiki-with-willy】開場程式出錯（{}），詳見 .system/hook-errors.log；跑 kb selftest 看原因，修好之前不改任何檔。"  # 只有 kb 前綴行或讀 core.md 失敗


def line_kb(vault):
    """永遠有值：kb 指令的完整呼叫前綴（D-013、D-019 不寫進檔，只在這裡印）。"""
    from kb._lib import paths

    return f'【wiki-with-willy】kb 指令：python "{paths.plugin_root().as_posix()}/scripts/kb.py" <子命令>'


# 共用的檢查在 _checks.py（放寬丙 3b）；這裡 re-export 讓舊名字還在
line_version = _checks.line_version
line_moved = _checks.line_moved
line_agenda = _checks.line_agenda
start_background_lint = _checks.start_background_lint
_UPGRADE = _checks._UPGRADE


def _log_vault(vault, e):
    _checks.log_vault(vault, e)


HOLD_LEFT_LINE = ("升級後「AI 產出」還有 {n} 份跟暫存區同名、內容不同：照內容判斷取代或另取名，"
                  "用 kb source-edit move 搬進暫存區，做完說一聲。")  # 句子歸乙定稿（放寬甲第二輪 13.2）


def line_hold_left(vault):
    """升級把 AI 產出併進暫存區時，同名而內容不同的檔留在 AI 產出（程式不判斷，D-324）：還有就印一行叫 AI 照 D-309 判斷；搬完就沒有了。"""
    from kb._lib import hold_migrate

    n = hold_migrate.left_count(vault)
    return HOLD_LEFT_LINE.format(n=n) if n else None


def line_compact(vault, source):
    """本題：只在摺疊後印。"""
    return COMPACT_LINE if source == "compact" else None


def status_lines(vault, source, session_id=None):
    """固定順序；每一行各自 try：出錯那行回 None、寫 hook-errors.log，其他行照印（I-046）。kb 行不包，它出錯走整段出錯。
    kb 行之後：這台今天第一次（env.json 的 daily_done 不是今天）才做版本、搬家、日程三行與背景健檢（放寬丙 3b）；
    變動、卡、標籤、設定檔、找不到的來源與整理提示都挪到每句開頭（hooks/turn.py）。"""
    out = [line_kb(vault)]
    if _checks.daily_pending(vault):
        out += _checks.daily_lines(vault)
    try:
        who = _checks.line_who(vault)  # 多人而這台沒記是誰（D-286）；每句開頭也會叫，同一個對話只講一次
    except Exception as e:  # noqa: BLE001
        _log_vault(vault, e)
        who = None
    if who:
        out.append(who)
    for fn in (line_hold_left, lambda v: line_compact(v, source)):
        try:
            line = fn(vault)
        except Exception as e:  # noqa: BLE001
            _log_vault(vault, e)
            line = None
        if line is not None:
            out.append(line)
    return out


def read_core():
    from kb._lib import paths

    return (paths.plugin_root() / "rules" / "core.md").read_text(encoding="utf-8")


# D-322 第 1 件（放寬乙）：偏好跟核心規則衝突時照客戶的，只有刪掉、蓋掉之前先留一份不讓
PREFERENCES_HEAD = "客戶偏好（跟上面的核心規則衝突時照客戶的，只有刪掉、蓋掉之前先留一份不讓）："


def _team_preferences(vault, prefs):
    """多人（多人共用 2 第 6 點）：只帶全隊的＋這台的人的；開場那組（整理成卡以外）兩邊合算超過 preference_max／preference_max_chars
    時先砍全隊最舊的（自己的優先留），被砍的寫 log。單人原樣回。"""
    from kb._lib import config, people

    if not people.multi(vault):
        return prefs
    mine = people.me(vault)
    kept = [p for p in prefs if not p.get("whose") or p.get("whose") == "team" or p.get("whose") == mine]
    top = [p for p in kept if (p.get("use") or "全部") != "整理成卡"]
    count, chars = config.get(vault, "preference_max"), config.get(vault, "preference_max_chars")
    teams = sorted((p for p in top if not p.get("whose") or p.get("whose") == "team"), key=lambda p: p.get("date") or "")
    while teams and (len(top) > count or sum(len(p.get("text", "")) for p in top) > chars):
        gone = teams.pop(0)
        top.remove(gone)
        kept.remove(gone)
        _log_vault(vault, ValueError(f"偏好太多，開場先不帶全隊的這條：{gone.get('text', '')[:40]}"))
    return kept


def preferences_block(vault):
    """note 改規則第 4 點（D-137、D-504）：設定檔第六節整理成卡以外的條目回「客戶偏好：」加一條一行，接在 core.md 之後；
    做文件、客製 skill 各自一組加小標（做文件時：、<skill 名>：）；沒有回 None。
    profile.json 讀不到這段不印、寫 log，其他照印（I-046）。只讀。"""
    try:
        from kb._lib import profile

        prefs = (profile.load(vault) or {}).get("preferences") or []
        prefs = _team_preferences(vault, prefs)
    except Exception as e:  # noqa: BLE001
        _log_vault(vault, e)
        return None
    groups = {}
    for p in prefs:  # 用在哪（D-504）：全部一條一行；整理成卡不進開場；其他照第一次出現的順序分組加小標
        use = p.get("use") or "全部"
        if use != "整理成卡":
            groups.setdefault(use, []).append(f"- {p.get('text', '')}")
    if not groups:
        return None
    lines = [PREFERENCES_HEAD] + groups.pop("全部", [])
    for use, items in groups.items():
        lines += [f"{use}時：" if use == "做文件" else f"{use}："] + items
    return "\n".join(lines)


def emit(context):
    """印一行 SessionStart JSON；write／flush 出錯（pipe 斷掉）吞掉。
    沒送出的字還在緩衝，結束時 Python 會再 flush 一次、失敗就 exit 120，所以把 fd 1 指到 devnull。"""
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    try:
        sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    except BaseException:  # noqa: BLE001
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except BaseException:  # noqa: BLE001
            pass


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        from kb import guard

        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
    except BaseException as e:  # noqa: BLE001  stdin 壞掉、找知識庫出錯：只記 log，不輸出
        _log(e, payload)
        return
    if vault is None:
        return
    try:  # 致命：kb 前綴行、讀 core.md 失敗才印「不改任何檔」那句（D-322 第 7 件）
        kb_line = line_kb(vault)
        core = read_core()
    except BaseException as e:  # noqa: BLE001
        _log(e, payload)
        emit(ERROR_LINE_FATAL.format(type(e).__name__))
        return
    try:  # 其他各行已各自 try；這裡再出錯只記 log、印非致命那句，其他照常
        context = "\n".join(status_lines(vault, payload.get("source"), payload.get("session_id"))) + "\n\n" + core
        block = preferences_block(vault)
        if block:
            context += "\n" + block
    except BaseException as e:  # noqa: BLE001
        _log(e, payload)
        context = kb_line + "\n" + ERROR_LINE.format(type(e).__name__) + "\n\n" + core
    emit(context)


def _log_vault(vault, e):
    try:
        from kb._lib import ledger

        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="session")
    except Exception:  # noqa: BLE001
        pass


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="session")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
