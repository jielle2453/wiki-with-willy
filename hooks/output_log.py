"""PostToolUse 入口：檔案工具寫進 AI 產出（Z3）之後把那份檔登進 .system/inventory.json（D-076）；
寫進 檔案索引（Z5）的 .md 卡驗檔頭（D-104，ingest 製卡第 6 點）；寫進 <型別>/<名稱>/<名稱>.md（Z1）的綜合卡驗檔頭（實體與綜合卡第 6 點）。

只做四個寫入工具；目標檔案真的存在才做；回合代號取 turn.current(vault, session_id)。
Z5：cards.validate 不過就印一行 {"decision": "block", "reason": "檔頭有錯：<原因>。改好再寫一次。"}；
過了不印，接著重渲染 總目錄.md（備進目前回合，實作疑義裁定 4）與這張卡新舊 實體 聯集的綜合卡快取（舊的從回合備份讀）。
Z1：synthesis.validate 不過印 {"decision": "block", "reason": "綜合卡有錯：<原因>。改好再寫一次。"}；
過了不印，接著 entities.set_card(True) 與重渲染它的快取（檔案卡、相關實體兩欄）。
驗過之後的事失敗只記 log（I-046）。任何例外寫 .system/hook-errors.log，永遠 exit 0；除了 block，不輸出。
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

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
OUTPUT_ZONE = "Z3"
CARDS_ZONE = "Z5"
SYNTHESIS_ZONE = "Z1"
BLOCK_REASON = "檔頭有錯：{}。改好再寫一次。"
SYNTHESIS_BLOCK = "綜合卡有錯：{}。改好再寫一次。"


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        tool = payload.get("tool_name")
        if tool not in WRITE_TOOLS:
            return
        ti = payload.get("tool_input") or {}
        target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
        if not target:
            return
        from kb import guard
        from kb._lib import inventory, turn
        from kb._lib import sources as sources_lib
        from kb._lib.paths import normalize

        cwd = payload.get("cwd") or os.getcwd()
        vault = guard.find_vault(cwd)
        if vault is None:
            return
        sources = sources_lib.load(vault)
        zone, _alias = guard.classify(normalize(str(target), cwd), normalize(str(vault), None), sources)
        if zone not in (OUTPUT_ZONE, CARDS_ZONE, SYNTHESIS_ZONE):
            return
        rel = turn.rel_path(vault, str(target), cwd)
        if not (vault / rel).is_file():
            return
        t = turn.current(vault, payload.get("session_id"))
        if zone == OUTPUT_ZONE:
            inventory.register_output(vault, rel, t["turn"])
        elif zone == SYNTHESIS_ZONE:
            _check_synthesis(vault, rel, t["turn"], payload)
        elif rel.lower().endswith(".md"):
            _check_card(vault, rel, sources, t["turn"], payload)
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _block(reason):
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _check_card(vault, rel, sources, turn_id, payload):
    from kb._lib import cards, inventory

    by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    ok, reason = cards.validate(vault, rel, by_id, sources)
    if not ok:
        _block(BLOCK_REASON.format(reason))
        return
    try:
        cards.render_index(vault, turn_id)
    except BaseException as e:  # noqa: BLE001  總目錄沒更新不影響卡
        _log(e, payload)
    try:
        from kb._lib import synthesis
        names = _entity_names(vault / rel) + _entity_names(_backup_of(vault, rel, turn_id))
        if names:
            synthesis.render_cache(vault, list(dict.fromkeys(names)), turn_id)
    except BaseException as e:  # noqa: BLE001  綜合卡快取沒更新不影響卡
        _log(e, payload)


def _backup_of(vault, rel, turn_id):
    from kb._lib import turn
    return vault / turn.VERSIONS_REL / turn_id / "files" / rel


def _entity_names(path):
    """卡檔頭的 實體 欄；讀不到（沒有備份、檔頭壞了）回空。"""
    from kb._lib import cards
    try:
        names = cards.parse_front(path.read_text(encoding="utf-8")).get("實體")
    except (ValueError, OSError, UnicodeDecodeError):
        return []
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _check_synthesis(vault, rel, turn_id, payload):
    from kb._lib import cards, entities, synthesis

    ok, reason = synthesis.validate(vault, rel)
    if not ok:
        _block(SYNTHESIS_BLOCK.format(reason))
        return
    try:
        eid = cards.parse_front((vault / rel).read_text(encoding="utf-8"))["編號"]
        entities.set_card(vault, eid, True, turn_id)
        entry, _hit = entities.find(entities.load(vault), eid)
        if entry is not None:
            synthesis.render_cache(vault, [entry["name"]], turn_id)
    except BaseException as e:  # noqa: BLE001  快取與 card 旗標沒更新不影響卡
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="output_log")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
