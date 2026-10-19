"""SessionStart 開場注入入口（D-087、D-060）：對話一開始、resume、clear、摺疊後，把狀態行與 rules/core.md 全文帶進 additionalContext。

狀態行固定順序、回 None 的不印；第一行固定以「【wiki-with-willy】」開頭，知識庫 CLAUDE.md 的哨兵句認這個字串（D-088）。
輸出 ensure_ascii=True：純 ASCII 不碰 cp950（I-001）。不碰 guard-status、ledger、回合、日誌；
唯一寫的是 .system/env.json 的「找不到的來源已報過」記號（清冊題，D-094 補），清冊只讀（快速 scan，不 apply）；
客戶設定檔那行也只讀（profile.status，不寫 profile.json 與 md）。
任何例外寫 .system/hook-errors.log（source session），永遠 exit 0。找到知識庫之後的例外另印一行出錯的 additionalContext
（失敗明講，開場注入測試後修正 D2）；找不到知識庫、stdin 壞掉不輸出。write／flush 也包住：pipe 斷掉照樣 exit 0。
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

COMPACT_LINE = "對話剛摺疊，規則已重新帶入；做到一半的事以工作日誌為準。"
ERROR_LINE = "【wiki-with-willy】開場程式出錯（{}），詳見 .system/hook-errors.log；這個對話不改任何檔。"


def line_kb(vault):
    """永遠有值：kb 指令的完整呼叫前綴（D-013、D-019 不寫進檔，只在這裡印）。"""
    from kb._lib import paths

    return f'【wiki-with-willy】kb 指令：python "{paths.plugin_root().as_posix()}/scripts/kb.py" <子命令>'


def line_version(vault):
    """等 upgrade 題。"""
    return None


CHANGES_LINE = "變動：新增 {added}、修改 {modified}、不見 {missing}"
CHANGES_INCOMPLETE = "變動：來源很大還沒比完，說「有什麼變動」我再比"
MISSING_SOURCES_LINE = "這台電腦找不到來源{names}，其他照常；搬過的話跟我說。"
MISSING_SOURCES_SHOWN = 3
INGEST_HINT = "有變動：先把客戶這句的答案完整說出來（不能只說「我先查」），同一則回答接著不用問、直接照 ingest skill 整理，末尾一句告知；只有超過 {n} 份才問。"  # 裁定 38、42
_SCAN = {}  # vault → (sources, report) 或 None（scan 出錯）；兩行共用一次快速 scan（審閱裁定 9）


def _quick_scan(vault):
    """快速比對一次；出錯只在第一次記一行 log，之後兩行都回 None。"""
    key = str(vault)
    if key not in _SCAN:
        try:
            from kb._lib import inventory
            from kb._lib import sources as sources_lib

            sources = sources_lib.load(vault)
            _SCAN[key] = (sources, inventory.scan(vault, sources, quick=True))
        except Exception as e:  # noqa: BLE001
            _SCAN[key] = None
            _log_vault(vault, e)
    return _SCAN[key]


def line_changes(vault):
    """清冊題：新增、修改、不見三個數都 0 且比完 → None；走訪時間到 → 還沒比完。搬動、重複、之前就不見的不印。"""
    got = _quick_scan(vault)
    if got is None:
        return None
    report = got[1]
    if report["incomplete"]:
        return CHANGES_INCOMPLETE
    counts = {k: len(report[k]) for k in ("added", "modified", "missing")}
    return CHANGES_LINE.format(**counts) if any(counts.values()) else None


def line_ingest_hint(vault):
    """ingest 製卡第 7 點：變動行（含還沒比完）印出來時緊接著一行，叫 AI 先答再照 ingest skill 整理；n 讀 ingest_auto_max。"""
    if line_changes(vault) is None:
        return None
    from kb._lib import config

    return INGEST_HINT.format(n=config.get(vault, "ingest_auto_max"))


def line_missing_sources(vault):
    """清冊題：這台電腦找不到的來源，每台電腦只報一次（env.json 記號）；又找得到就清掉記號，之後再失蹤再報。"""
    got = _quick_scan(vault)
    if got is None:
        return None
    from kb._lib import env

    sources, report = got
    lost = {(m["id"] or m["alias"]): m["alias"] for m in report["missing_sources"]}
    env.clear_missing(vault, [s["id"] or s["alias"] for s in sources.entries if (s["id"] or s["alias"]) not in lost])
    new = {k: alias for k, alias in lost.items() if not env.missing_reported(vault, k)}
    if not new:
        return None
    env.mark_missing(vault, list(new))
    aliases = list(new.values())
    names = "、".join(f"「{alias}」" for alias in aliases[:MISSING_SOURCES_SHOWN])
    if len(aliases) > MISSING_SOURCES_SHOWN:
        names += f"等 {len(aliases)} 個"  # 最多列 3 個，整行守在開場預算裡（審閱裁定 32）
    return MISSING_SOURCES_LINE.format(names=names)


PROFILE_CHANGED = "設定檔有改（{summary}）：跑 kb profile check 複述後問客戶要不要套用，說好才 apply"
PROFILE_ERROR = "設定檔有改但讀不懂（{error}），改好下次再問"
PROFILE_UNLISTED = "第一層夾{names}不在型別表，請客戶在設定檔補一列"
PROFILE_LINE_MAX = 120
PROFILE_UNLISTED_SHOWN = 2


def line_profile(vault):
    """客戶設定檔與型別第 7 點：profile.status 只讀、不寫 JSON 也不寫 md。有改（有內容差異）或讀不懂各一句；
    第一層有不在型別表的夾接在同一行後面，沒有前半時自己成一行（開頭「設定檔：」）；整行超過 120 字夾名只列 2 個。"""
    from kb._lib import profile

    st = profile.status(vault)
    head = None
    if st["state"] == "changed" and profile.has_diff(st["diff"]):
        head = PROFILE_CHANGED.format(summary=profile.summary_short(st["diff"]))
    elif st["state"] == "error":
        head = PROFILE_ERROR.format(error=st["error"])
    if not st["unlisted"]:
        return head

    def joined(names, more):
        text = "、".join(f"「{n}」" for n in names) + (f"等 {more} 個" if more else "")
        tail = PROFILE_UNLISTED.format(names=text)
        return f"{head}；{tail}" if head else f"設定檔：{tail}"
    line = joined(st["unlisted"], 0)
    if len(line) > PROFILE_LINE_MAX:
        line = joined(st["unlisted"][:PROFILE_UNLISTED_SHOWN], len(st["unlisted"]))
    return line


def line_agenda(vault):
    """等日程題。"""
    return None


def line_backup(vault):
    """等備份題。"""
    return None


def line_health(vault):
    """等 lint 題。"""
    return None


def line_compact(vault, source):
    """本題：只在摺疊後印。"""
    return COMPACT_LINE if source == "compact" else None


def status_lines(vault, source):
    """固定順序；每一行各自 try：出錯那行回 None、寫 hook-errors.log，其他行照印（I-046）。kb 行不包，它出錯走整段出錯。"""
    out = [line_kb(vault)]
    for fn in (line_version, line_changes, line_ingest_hint, line_missing_sources, line_profile, line_agenda, line_backup,
               line_health,
               lambda v: line_compact(v, source)):
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
    try:
        context = "\n".join(status_lines(vault, payload.get("source"))) + "\n\n" + read_core()
    except BaseException as e:  # noqa: BLE001  找到知識庫之後出錯：記 log，再明講一行
        _log(e, payload)
        emit(ERROR_LINE.format(type(e).__name__))
        return
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
