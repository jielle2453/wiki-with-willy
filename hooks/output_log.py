"""PostToolUse 入口：檔案工具寫進 AI 產出（Z3）之後把那份檔登進 .system/inventory.json（D-076）；
寫進 檔案索引（Z5）的 .md 卡驗檔頭（D-104，ingest 製卡第 6 點）；寫進 <型別>/<名稱>/<名稱>.md（Z1）的綜合卡驗檔頭（實體與綜合卡第 6 點）。

只做四個寫入工具；目標檔案真的存在才做；回合代號取 turn.current(vault, session_id)；payload 帶 agent_id 而且 batches.json 記過它
（card-maker）就取它記的回合。
Z5：cards.validate 不過就印一行 {"decision": "block", "reason": "檔頭有錯：<原因>。改好再寫一次。"}；
過了不印，接著重渲染 總目錄.md（備進目前回合，實作疑義裁定 4）與這張卡新舊 實體 聯集的綜合卡快取（舊的從回合備份讀）。
實體清單讀不到時兩種卡都 block「實體清單讀不到（.system/entities.json），先停下來說「檢查」」（測試後修正裁定 11）。
Z1：synthesis.validate 不過印 {"decision": "block", "reason": "綜合卡有錯：<原因>。改好再寫一次。"}；
過了不印，接著 entities.set_card(True) 與重渲染它的快取（檔案卡、相關實體兩欄）。
build 桌面版確認 A 段：綜合卡在這一回合備份過（寫之前已經有舊卡）時，比對舊卡「## 紀事」的每一條：新卡的紀事裡找不到它的出處
（〔…〕裡的編號）、沒有出處的找不到原句，就 block JISHI_BLOCK（紀事只增，R-08、S-16）；還有沒清的「不想讓 AI 看到」排除時不比
（purge_synthesis 本來就要刪那夾的紀事）。
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
ENTITIES_UNREADABLE = "實體清單讀不到（.system/entities.json），先停下來說「檢查」"  # 測試後修正裁定 11
JISHI_BLOCK = "綜合卡的紀事少了 {n} 條（紀事只增）：{lines}。先 Read 舊卡的紀事，把這幾條照原樣補回去再寫一次。"
JISHI_SHOWN = 3


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
        # card-maker（D-195 釐清 3b）：子代理寫的照它記的回合（跟 guard.py 同一支），總目錄與綜合卡快取的備份才在同一回合
        agent_turn = turn.agent_turn(vault, payload.get("session_id"), payload.get("agent_id"))
        t = (turn.turn_data(vault, agent_turn) if agent_turn else None) or turn.current(vault, payload.get("session_id"))
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


def _entities_ok(vault):
    """實體清單讀得到嗎；讀不到就 block 那一句（不把狀態檔的錯說成卡檔頭的錯）。"""
    from kb._lib import entities
    try:
        entities.load(vault)
    except ValueError:
        _block(ENTITIES_UNREADABLE)
        return False
    return True


def _check_card(vault, rel, sources, turn_id, payload):
    from kb._lib import cards, inventory

    if not _entities_ok(vault):
        return
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

    if not _entities_ok(vault):
        return
    ok, reason = synthesis.validate(vault, rel)
    if not ok:
        _block(SYNTHESIS_BLOCK.format(reason))
        return
    lost = _lost_events(vault, rel, turn_id)
    if lost:
        shown = "；".join(l[:60] for l in lost[:JISHI_SHOWN]) + ("…" if len(lost) > JISHI_SHOWN else "")
        _block(JISHI_BLOCK.format(n=len(lost), lines=shown))
        return
    try:
        eid = cards.parse_front((vault / rel).read_text(encoding="utf-8"))["編號"]
        entities.set_card(vault, eid, True, turn_id)
        entry, _hit = entities.find(entities.load(vault), eid)
        if entry is not None:
            synthesis.render_cache(vault, [entry["name"]], turn_id)
    except BaseException as e:  # noqa: BLE001  快取與 card 旗標沒更新不影響卡
        _log(e, payload)


def _events(text):
    """「## 紀事」那段的條目（- 開頭的行，去頭尾空白）。"""
    got, inside = [], False
    for line in str(text or "").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == "紀事"
            continue
        if inside and line.strip().startswith("- "):
            got.append(line.strip())
    return got


def _cites(line):
    import re
    return set(re.findall(r"[fsrc]-[0-9a-f]{8}", line))


def _lost_events(vault, rel, turn_id):
    """這一回合寫之前的舊卡（回合備份）有、新卡沒有的紀事：有出處的看出處編號在不在新卡的紀事裡，沒出處的看原句。
    沒有備份（新卡）、還有沒清的「不想讓 AI 看到」排除時回空。"""
    from kb._lib import sources as sources_lib
    backup = _backup_of(vault, rel, turn_id)
    if not backup.is_file() or sources_lib.pending_purges(sources_lib.load(vault)):
        return []
    try:
        old = _events(backup.read_text(encoding="utf-8"))
        new = _events((vault / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []
    new_cites = set().union(*(_cites(l) for l in new)) if new else set()
    new_lines = set(new)
    return [l for l in old if (_cites(l) and not _cites(l) <= new_cites) or (not _cites(l) and l not in new_lines)]


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
