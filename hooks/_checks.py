"""開場（hooks/session.py）與每句開頭（hooks/turn.py）共用的檢查與提示行（放寬丙 3b，D-327）。

從 session.py 搬來，句子照放寬乙定的版本（design/specs/2026-11-01-放寬乙-說法與規則.md 要做的事 3、4），這裡不改字。
開場只留 kb 行、核心規則、偏好、摺疊行與「這台今天第一次」那批（daily）；其他每句開頭做（turn_lines）：
  daily（env.json 的 daily_done 不是今天才做）→ 檔案比對（hook 點，下面 file_compare）→ line_card_edits → line_tags
  → line_profile → line_missing_sources → 搬動、變動（change_lines）、實體夾有變（line_drift）。
新放、改過的檔先算製作中，不叫 AI 整理；md 照舊整理那幾份（D-525）。
時間到（TURN_BUDGET 秒，從 hook 開始算）就停，沒做完的下一句接著做。各檢查先比修改時間（記在 session 的回合檔 checks，
上次檢查的時間），沒變不讀；同一件同一個對話講過就不再印（checks 的 told）。全部只讀（卡的基準與 env.json 的記號例外，照舊）。
任何例外只寫 .system/hook-errors.log（source 由呼叫端的 SOURCE 決定），其他檢查照做。
hook 不是從 scripts/ 跑的，sys.path 由 session.py、turn.py 設好（hooks/*.py 是唯一例外，CLAUDE.md 三）。
"""
import os
import sys
import time

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

START = time.monotonic()  # hook 的時限從 import 這支算（呼叫端是 hook 的第一件事）
TURN_BUDGET = 1.5  # 秒，每句開頭所有檢查共用；turn_lines 開頭換成設定 turn_scan_seconds
DETECTED_SEEN_KEEP = 300  # session 檔 checks.detected 最多留幾條（已記過的偵測鍵）
SOURCE = "session"  # log 的 source；turn.py 改成 "turn"

WIP_LINE = "有 {n} 份新放或改過（製作中）：不整理、不跟客戶提；先跑 kb inventory scan 記進清冊；問到這幾份讀原檔答。"  # D-525
MD_LINE = ("有 {n} 份 md 新放或改過（{names}）：她這句沒說整理，就先跑 kb inventory scan，再照 ingest 只整理這幾份"
           "（kb cards dispatch --ids <編號>，編號看 kb cards todo --json）；她說了整理就照 ingest 整理全部、不帶 --ids。")  # D-525 第 8 點、裁定 3、15
COLLEAGUE = "（可能是同事動的，不是這台使用者的意思，別從中推測他的偏好）"  # 多人共用 2 第 10 點（D-334(3)）
MISSING_LINE = "有 {n} 份不見：跑 kb inventory scan 再 kb cards sync 拿掉卡；不派整理、不做卡。"
NAMES_SHOWN = 3
WIP_DAILY = "製作中 {n} 份：{names}；跟客戶說一句還有這幾份沒整理、要整理說一聲，不問、不追。"  # D-525 第 5 點
CHANGES_INCOMPLETE = "變動還沒比完：下一句接著比，不用跟客戶提。"
MOVED_HINT = ("有 {n} 件搬動：跑 kb changes --moved --json，照內容判斷要不要改掛（搬到結案夾、桌面、客戶自己的夾就不用動也不用提）；"
              "先看夾名、檔名一批一起判斷，看不出來的才讀內容，卡跟著走不重做；看完 changes --moved 之後要讀搬過的檔，再跑 kb inventory scan。")  # 放寬丙 5-3，字句照乙；刪綜合卡說一聲 裁定 6
DRIFT_LINE = ("實體夾有變 {n}：跑 kb cards todo --json，照 ingest「實體夾有變」那段處理"
              "（刪了綜合卡照 entity_drift 判斷、做完說一聲），不做卡。")  # D-525 裁定 1；刪綜合卡說一聲 第 2 點
MOVED_LINE_TEXT = "知識庫換了位置：先跑 kb moved 補好再做別的。"
NEW_MACHINE_LINE = "這台電腦第一次開這個知識庫：先跑 kb moved 看要補什麼。"
MISSING_SOURCES_LINE = "這台電腦找不到來源{names}：跑 kb folders locate <別名> 自己找，找到就 kb folders relocate 接上、說一聲；找不到才問使用者。"
MISSING_SOURCES_SHOWN = 3
MISSING_SOURCE_TEAM = "這台找不到來源「{alias}」（預設在 {spec}），請問她這台的「{alias}」在哪，答了跑 kb folders locate {alias} <路徑>。"  # 多人共用 2 第 3 點
LEARNING_LINE = "這台第一次用，還在認檔案，好了再報變動；先不整理、不跟客戶提變動。"  # D-300
WHO_LINE = "這台第一次用，請先問她這台電腦是誰在用（預設 {name}），答了跑 kb who set <名字>。"  # D-286
CARD_EDITS_LINE = "客戶在 Obsidian 改了 {n} 張卡（kb cards edits）：先照 note skill 處理完再回答；那是客戶的意思，照做、不跟客戶提指令名。"  # D-270
CARD_EDITS_MAYBE = "可能有客戶改過的卡（kb cards edits）：先跑一次看看，有的話先照 note skill 處理完再回答；那是客戶的意思，照做、不跟客戶提指令名。"  # 時限內一張都沒比到
CARD_EDITS_MORE = "客戶在 Obsidian 改了 {n} 張卡（可能還有，kb cards edits）：先照 note skill 處理完再回答；那是客戶的意思，照做、不跟客戶提指令名。"
PROFILE_CHANGED = "設定檔被手改了（{summary}）：跑 kb profile apply 套用；做完照核心規則講發現什麼、推測使用者的意思、做了什麼。"
PROFILE_ERROR = "設定檔有改但讀不懂：{error}；改好下次再問"
PROFILE_LINE_MAX = 120
PROFILE_ORDER = "型別順序"  # 只調了型別表的順序（型別夾會改名）：diff 沒有內容差異時的摘要
TAGS_EDITED = ("客戶在 Obsidian 卡的屬性欄改了 {n} 張綜合卡的標籤或數字（{names}）：跑 kb entity tag --from-card 記進去"
               "（數字一起記，同 kb entity field --from-card），跟她說「你在卡的屬性欄改了標籤或數字：…（記了什麼）」；"
               "不在字典的詞直接加進字典，不是數字的問她要記成多少。")
TAGS_EDITED_SHOWN = 3
AGENDA_OVERDUE_HINT = "（逾期的在回答最後問一件，客戶說先放著就 kb asks set-aside）"
BACKGROUND_LINT_BUDGET = 7  # 秒，從 hook 開始算；前面用掉太多時間這次就不開
TOLD_KEEP = 30  # checks.told 最多留幾條
FILES_KEEP = 1000  # checks.told_files（變動行講過的檔，「wip|uri」等）最多留幾條：一次放很多份也不會每句重講


def log_vault(vault, e):
    try:
        from kb._lib import ledger

        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source=SOURCE)
    except Exception:  # noqa: BLE001
        pass


def over_budget(seconds=None):
    return time.monotonic() - START > (TURN_BUDGET if seconds is None else seconds)


# ---------- 升級、搬家（讓位判斷共用） ----------

_UPGRADE = {}  # vault → upgrade.opening 的結果或 None（出錯）；log 只記一行


def upgrade_state(vault):
    """upgrade.opening 不連網、不等背景（日期不是今天才開 kb upgrade --background 的子行程）；出任何錯回 None、記一行 log。"""
    key = str(vault)
    if key not in _UPGRADE:
        try:
            from kb._lib import upgrade

            _UPGRADE[key] = upgrade.opening(vault)
        except Exception as e:  # noqa: BLE001
            _UPGRADE[key] = None
            log_vault(vault, e)
    return _UPGRADE[key]


def line_version(vault):
    """升級打包（D-060、D-215、D-217）：換了版要 --finish、上次查到可以拿的大升級、清單被指回舊版；其他不印。"""
    state = upgrade_state(vault)
    return state["line"] if state else None


_MOVED = {}  # vault → state（moved｜new_machine｜same｜first）或 None（出錯）


def moved_state(vault):
    key = str(vault)
    if key not in _MOVED:
        try:
            from kb._lib import moved

            _MOVED[key] = moved.note_place(vault)  # first 時默默記位置、補 vault.json（測試 T-2），其他狀態只讀
        except Exception as e:  # noqa: BLE001
            _MOVED[key] = None
            log_vault(vault, e)
    return _MOVED[key]


def line_moved(vault):
    """env.json 這台電腦記的位置跟現在不同 → 換了位置；這台沒有紀錄、但有別台的 → 第一次開。只讀。"""
    return {"moved": MOVED_LINE_TEXT, "new_machine": NEW_MACHINE_LINE}.get(moved_state(vault))


def yield_to_upgrade(vault):
    """先補位置、先 kb upgrade --finish 轉格式：整理提示、卡的比對這時都先不做。"""
    state = upgrade_state(vault)
    return bool(line_moved(vault) or (state and state["finish"]))


# ---------- 清冊比對（hook 點）與變動行 ----------

_SCAN = {}  # vault → (sources, report) 或 None（scan 出錯）


def file_compare(vault, seconds=None):
    """每句開頭與答完的檔案比對（放寬丙 要做的事 1）：inventory.scan(quick) 只讀、不 apply。時間上限預設是每句開頭預算剩下的，
    讀取上限 turn_scan_hash_budget_bytes。回 (sources, report) 或 None（scan 出錯，記一行 log）。同一個行程只比一次。"""
    key = str(vault)
    if key not in _SCAN:
        try:
            from kb._lib import config, inventory
            from kb._lib import sources as sources_lib

            sources = sources_lib.load(vault)
            left = max(0.0, TURN_BUDGET - (time.monotonic() - START)) if seconds is None else seconds
            _SCAN[key] = (sources, inventory.scan(vault, sources, quick=True, seconds=left,
                                                  hash_budget=config.get(vault, "turn_scan_hash_budget_bytes")))
        except Exception as e:  # noqa: BLE001
            _SCAN[key] = None
            log_vault(vault, e)
    return _SCAN[key]


def _session_turn(vault, session_id):
    """這個對話現在的回合代號；讀不到回 None。"""
    from kb._lib import turn

    data = turn._read_turn(turn._session_file(vault, session_id)) if session_id else None
    return (data or {}).get("turn")


def _kb_keys(vault, turn_id):
    """這一句 kb 或守門已記的路徑（moves.jsonl、folders.jsonl、manifest.jsonl），換成清冊 uri 的比對鍵。"""
    from kb._lib import inventory, turn

    keys = set()
    for m in turn.moves(vault, turn_id):
        keys.update(inventory.uri_key(m[k]) for k in ("from", "to") if m.get(k))
    for f in turn.folders(vault, turn_id):
        keys.update(inventory.uri_key(inventory.make_uri(inventory.KNOWLEDGE, f[k])) for k in ("from", "to") if f.get(k))
    for e in turn.entries(vault, turn_id):
        if e.get("uri"):
            keys.add(inventory.uri_key(e["uri"]))
        if e.get("path"):
            keys.add(inventory.uri_key(inventory.make_uri(inventory.KNOWLEDGE, e["path"])))
    return keys


def _colleague_uri(vault, sources, uri):
    """多人時這個位置在共用的夾（來源登記 shared，多人時雲端來源都是）→ True；單人一律 False。"""
    try:
        from kb._lib import people

        if not uri or not people.multi(vault):
            return False
        alias = str(uri)[len("file://"):].partition("/")[0].casefold() if str(uri).startswith("file://") else ""
        return any(s.get("shared") and s["alias"].casefold() == alias for s in sources.entries)
    except Exception as e:  # noqa: BLE001
        log_vault(vault, e)
        return False


def record_detected(vault, session_id, got, by, seen):
    """把這次比對的結果寫進這一句的 detected.jsonl（只寫這個檔）。by：outside（兩句之間）｜turn（這一句 AI 用自己的方法）。
    沒比完不記 added（inventory.detected_rows）；seen（已記過的 op|from|to）有的不重記；by turn 時 kb 或守門已記的同路徑跳過。
    回更新後的 seen（清單，呼叫端存回 session 檔的 checks.detected）。"""
    seen = list(seen or [])
    if got is None or yield_to_upgrade(vault):  # 要先補位置、先 --finish 轉格式時，整個知識庫看起來都不見，不記
        return seen
    tid = _session_turn(vault, session_id)
    if not tid:
        return seen
    from kb._lib import inventory, turn

    rows = inventory.detected_rows(got[0], got[1], by=by)
    for r in rows:  # 多人時落在共用夾的變動不掛這台的人（可能是同事動的）
        if by == "outside" and _colleague_uri(vault, got[0], r.get("from") or r.get("to")):
            r["colleague"] = True
    skip = _kb_keys(vault, tid) if by == "turn" else set()
    fresh = []
    for r in rows:
        key = f"{r['op']}|{r['from']}|{r['to']}"
        if key in seen:
            continue
        if any(inventory.uri_key(r[k]) in skip for k in ("from", "to") if r.get(k)):
            continue
        fresh.append(r)
        seen.append(key)
    turn.record_detected(vault, tid, fresh)
    return seen[-DETECTED_SEEN_KEEP:]


def after_answer(vault, session_id):
    """Stop 用：答完再比一次（stop_scan_seconds），這一句期間出現、又不是 kb 或守門記過的變動寫進同一個 detected.jsonl（by turn）。
    只讀清冊、只寫 detected.jsonl 與 session 檔的已記鍵；任何例外只寫 log。"""
    try:
        from kb._lib import config, turn

        if not session_id or _session_turn(vault, session_id) is None:
            return
        got = file_compare(vault, seconds=config.get(vault, "stop_scan_seconds"))
        checks = turn.get_checks(vault, session_id)
        seen = record_detected(vault, session_id, got, "turn", checks.get("detected"))
        if seen != list(checks.get("detected") or []):
            checks["detected"] = seen
            turn.set_checks(vault, session_id, checks)
    except Exception as e:  # noqa: BLE001
        log_vault(vault, e)


_DRIFT = {}  # vault → 實體夾有變的筆數或 None（出錯）


def drift_count(vault):
    """實體夾有變（D-057）：entities.drift 只讀；實體清單壞了回 None、記一行 log。"""
    key = str(vault)
    if key not in _DRIFT:
        try:
            from kb._lib import entities

            _DRIFT[key] = len(entities.drift(vault))
        except Exception as e:  # noqa: BLE001
            _DRIFT[key] = None
            log_vault(vault, e)
    return _DRIFT[key]


def _is_md(uri):
    return uri.lower().endswith(".md")


def _names(uris):
    names = [u.rsplit("/", 1)[-1] for u in uris]
    return "、".join(names[:NAMES_SHOWN]) + ("等" if len(names) > NAMES_SHOWN else "")


def _stamp(vault, sources, uri):
    """檔現在的「大小:修改時間」（裁定 18：同一份又改了再講）；找不到檔回空字串。"""
    from kb._lib import inventory

    try:
        st = os.stat(inventory.path_of(vault, sources, uri))
    except (OSError, TypeError, ValueError):
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def change_lines(vault, told_files=(), session_id=None):
    """每句開頭的變動（D-525）：回 (行清單, 更新後的 told_files)。md 以外的新增、修改 → WIP_LINE（製作中，不叫整理）；md → MD_LINE
    （這個 session 按過停止不印，answer 裁定 14）；不見 → MISSING_LINE。同一份同一個內容一個對話只講一次，又改了再講（裁定 18：
    鍵「wip|uri|大小:修改時間」「md|uri|…」，記新鍵時同一份的舊鍵拿掉；不見的鍵「missing|uri」在檔回來後拿掉（missing、
    still_missing 都不算回來），又不見再講）。清冊裡記成指路卡、封存、照設定不整理、讀不了的不算（cards.not_wip，裁定 8）。沒比完 → [CHANGES_INCOMPLETE]；搬動、重複、之前就不見的不算；要先補位置或
    --finish 轉格式時都不印。不報「還有幾份沒整理」（放寬丙 3，D-310(2)）。"""
    told = list(told_files)
    got = file_compare(vault)
    if got is None or yield_to_upgrade(vault):
        return [], told
    sources, report = got
    if report["incomplete"]:
        return [CHANGES_INCOMPLETE], told
    gone = set(report["missing"]) | set(report.get("still_missing") or [])
    told = [k for k in told if not (k.startswith("missing|") and k[len("missing|"):] not in gone)]
    seen = set(told)
    from kb._lib import cards, inventory

    skip = set()
    if report["added"] or report["modified"]:
        try:  # 指路卡、封存、照設定不整理的不算（裁定 8，同 kb cards wip）；清冊讀不到就不篩、記一行 log
            skip = {inventory.uri_key(loc["uri"]) for e in inventory.load(vault)["entries"] if cards.not_wip(e)
                    for loc in e.get("locations") or [] if loc.get("uri")}
        except (ValueError, OSError) as e:
            log_vault(vault, e)

    stamps = {}  # 每份只 stat 一次：過濾與記鍵用同一個（review 第 3 條）

    def key(kind, uri):
        if kind == "missing":
            return f"missing|{uri}"
        if uri not in stamps:
            stamps[uri] = _stamp(vault, sources, uri)
        return f"{kind}|{uri}|{stamps[uri]}"

    def fresh(kind, uris):
        return [u for u in dict.fromkeys(uris) if key(kind, u) not in seen]
    changed = [u for u in report["added"] + report["modified"] if inventory.uri_key(u) not in skip]
    groups = (("wip", fresh("wip", [u for u in changed if not _is_md(u)])),
              ("md", [] if _stopped(vault, session_id) else fresh("md", [u for u in changed if _is_md(u)])),
              ("missing", fresh("missing", report["missing"])))
    lines, keys = [], []
    for kind, uris in groups:
        if not uris:
            continue
        line = (WIP_LINE.format(n=len(uris)) if kind == "wip" else MISSING_LINE.format(n=len(uris)) if kind == "missing"
                else MD_LINE.format(n=len(uris), names=_names(uris)))
        if any(_colleague_uri(vault, sources, u) for u in uris):  # 多人時共用夾裡的變動：不說「你」、不掛這台的人
            line += COLLEAGUE
        lines.append(line)
        keys += [key(kind, u) for u in uris]
    old = {k.rsplit("|", 1)[0] for k in keys if not k.startswith("missing|")}  # 同一份的舊鍵（含沒帶指紋的舊格式）
    told = [k for k in told if k not in old and k.rsplit("|", 1)[0] not in old]
    return lines, told + keys


def line_drift(vault):
    """實體夾有變 N（D-057）自成一行（D-525 裁定 1）；沒有或實體清單壞了回 None。"""
    drift = drift_count(vault) or 0
    return DRIFT_LINE.format(n=drift) if drift else None


def line_moved_files(vault, told):
    """認出搬動時印給 AI 的一行（放寬丙 5-3）：整夾搬算一件（cards.group_moves）；同一件同一個對話只講一次（鍵記進 told，
    「moved|舊|新」）。沒有新的件回 None；回 (句子, 新鍵清單)。"""
    got = file_compare(vault)
    if got is None or not got[1]["moved"]:
        return None
    from kb._lib import cards

    fresh = [k for k in (f"moved|{g['from']}|{g['to']}" for g in cards.group_moves(vault, got[1]["moved"])) if k not in told]
    return (MOVED_HINT.format(n=len(fresh)), fresh) if fresh else None


def _stopped(vault, session_id):
    """這個 session 按過停止嗎（session 檔記著 ingest_stopped，answer 第 9 點）。只讀；讀不到當沒按過。"""
    if not session_id:
        return False
    from kb._lib import turn

    try:
        data = turn._read_turn(turn._session_file(vault, session_id))
    except OSError as e:
        log_vault(vault, e)
        return False
    return bool(data and data.get("ingest_stopped"))


# ---------- 卡、標籤、設定檔、找不到的來源 ----------

def line_card_edits(vault, deadline=None):
    """客戶手改了卡，系統跟著更新（D-270）：比卡內文跟基準（card_base.edits），有改過的才印；時限內比不完就說可能還有。
    沒有基準的卡這裡順手補上（第一次開不報）。先 kb upgrade --finish 轉格式、卡要跟著搬的庫先不比。回 (行, 比完了嗎)。"""
    if yield_to_upgrade(vault):
        return None, True
    from kb._lib import card_base

    got = card_base.edits(vault, deadline=deadline if deadline is not None else START + TURN_BUDGET)
    n = len(got["edits"])
    if not n:
        return (CARD_EDITS_MAYBE if got["more"] else None), not got["more"]
    return (CARD_EDITS_MORE if got["more"] else CARD_EDITS_LINE).format(n=n), not got["more"]


def line_learning(vault):
    """新同事第一次（D-300）：共用清冊有東西、這台本機清冊是空的 → 背景開 kb inventory learn --background（十分鐘內不重開），回那一句；
    不是這種情況回 None。測試環境變數 WIKI_WITH_WILLY_NO_BACKGROUND 有值時不開背景。"""
    from kb._lib import inventory

    if not inventory.learning(vault):
        return None
    marker = os.path.join(str(vault), ".system", "local", "learning-started")
    fresh = False
    try:
        fresh = time.time() - os.path.getmtime(marker) < 600
    except OSError:
        pass
    if not fresh and not os.environ.get("WIKI_WITH_WILLY_NO_BACKGROUND"):
        try:
            import subprocess

            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
            from kb._lib import paths

            kw = {"cwd": str(vault), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "close_fds": True}
            if os.name == "nt":
                kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            else:
                kw["start_new_session"] = True
            subprocess.Popen([sys.executable, str(paths.plugin_root() / "scripts" / "kb.py"), "inventory", "learn",
                              "--background"], **kw)
        except Exception as e:  # noqa: BLE001  起不來不影響這一句
            log_vault(vault, e)
    return LEARNING_LINE


def line_who(vault):
    """多人共用 2（D-286、設計自定 2）：多人而這台還沒記是誰就問一次；單人、已經記了不印。"""
    from kb._lib import people

    if not people.multi(vault) or people.me(vault):
        return None
    return WHO_LINE.format(name=people.default_name(vault) or "（沒有，請她說）")


def _team_missing(vault, got_sources, report):
    """多人：找不到的來源一個一句講預設在哪、問這台在哪（同一個對話只報一次由 env 記號管）。"""
    from kb._lib import env, sources as sources_lib

    specs = {s.get("id") or s.get("alias"): s.get("spec", "") for s in sources_lib.load_raw(vault)["sources"]}
    lost = {(m["id"] or m["alias"]): m["alias"] for m in report["missing_sources"]}
    env.clear_missing(vault, [s["id"] or s["alias"] for s in got_sources.entries if (s["id"] or s["alias"]) not in lost])
    new = {k: a for k, a in lost.items() if not env.missing_reported(vault, k)}
    if not new:
        return None
    env.mark_missing(vault, list(new))
    first = next(iter(new))
    line = MISSING_SOURCE_TEAM.format(alias=new[first], spec=specs.get(first, "?"))
    if len(new) > 1:
        line += f"（還有 {len(new) - 1} 個也找不到：{'、'.join(list(new.values())[1:MISSING_SOURCES_SHOWN])}）"
    return line


def line_missing_sources(vault):
    """清冊題：這台電腦找不到的來源，每台電腦只報一次（env.json 記號）；又找得到就清掉記號，之後再失蹤再報。"""
    if line_moved(vault):  # kb moved 會列這台找不到的來源；這裡報了就把「已報過」記號寫掉
        return None
    got = file_compare(vault)
    if got is None:
        return None
    from kb._lib import env, people

    sources, report = got
    if people.multi(vault):
        return _team_missing(vault, sources, report)
    lost = {(m["id"] or m["alias"]): m["alias"] for m in report["missing_sources"]}
    env.clear_missing(vault, [s["id"] or s["alias"] for s in sources.entries if (s["id"] or s["alias"]) not in lost])
    new = {k: alias for k, alias in lost.items() if not env.missing_reported(vault, k)}
    if not new:
        return None
    env.mark_missing(vault, list(new))
    aliases = list(new.values())
    names = "、".join(f"「{alias}」" for alias in aliases[:MISSING_SOURCES_SHOWN])
    if len(aliases) > MISSING_SOURCES_SHOWN:
        names += f"等 {len(aliases)} 個"  # 最多列 3 個，整行守在預算裡（審閱裁定 32）
    return MISSING_SOURCES_LINE.format(names=names)


def line_profile(vault):
    """客戶設定檔與型別第 7 點：profile.status 只讀、不寫 JSON 也不寫 md。有改（有內容差異，或調了型別順序）或讀不懂各一句；
    第一層有不在型別表的夾接在同一行後面，沒有前半時自己成一行；型別夾改名（D-247）認得出來就在同一行講。"""
    from kb._lib import profile

    st = profile.status(vault)
    head = None
    if st["state"] == "changed" and profile.has_change(st):
        head = PROFILE_CHANGED.format(summary=profile.summary_short(st["diff"]) or PROFILE_ORDER)
    elif st["state"] == "error":
        head = PROFILE_ERROR.format(error=st["error"])
    if st.get("renamed"):  # 句子跟 kb profile check 同一支（profile.renamed_line，裁定 8）
        text = profile.renamed_line(st["renamed"], with_change=head is not None)
        head = f"{head}；{text}" if head else text
    return head


def line_tags(vault):
    """標籤記在實體上（D-505、裁定 6）：客戶在 Obsidian 手改了綜合卡的標籤行或數字行（記帳 1b 第 6 點；entities.tag_edits，只讀）
    就印一行，名稱最多列 3 個。"""
    from kb._lib import entities

    try:
        edits = entities.tag_edits(vault)
    except ValueError:
        return None  # entities.json 壞了由變動行那邊記 log，這裡不重記
    if not edits:
        return None
    names = "、".join(x["name"] for x in edits[:TAGS_EDITED_SHOWN]) + ("…" if len(edits) > TAGS_EDITED_SHOWN else "")
    return TAGS_EDITED.format(n=len(edits), names=names)


# ---------- 每天第一句 ----------

def line_agenda(vault):
    """日程題（D-060、D-129）：逾期、今天、N 天內三個數字，0 的項不印、全 0 整行不印；有逾期時數字後面接一句。只讀。"""
    from kb._lib import agenda, config

    counts = agenda.counts(vault, agenda.load(vault))
    parts = []
    if counts["overdue"]:
        parts.append(f"逾期 {counts['overdue']}{AGENDA_OVERDUE_HINT}")
    if counts["today"]:
        parts.append(f"今天 {counts['today']}")
    if counts["upcoming"]:
        parts.append(f"{config.get(vault, 'agenda_window_days')} 天內 {counts['upcoming']}")
    return "日程：" + "、".join(parts) if parts else None


OWED_LINE = "公司還欠你 {n} 筆共 {total}（{names}）"
OWED_NAMES_SHOWN = 3


def line_owed(vault):
    """報帳（D-338，記帳 3）：這台的人是墊的人、公司還沒還的；照 D-289「我的」只給他看。單人、沒有就不印。只讀。"""
    from kb._lib import money, numbers, people

    me = people.me(vault)
    if not me or not money.exists(vault):
        return None
    got = money.owed_to(vault, me)
    if not got["items"]:
        return None
    names = [i.get("text") or "沒寫是什麼" for i in got["items"]]
    shown = "、".join(names[:OWED_NAMES_SHOWN]) + ("…" if len(names) > OWED_NAMES_SHOWN else "")
    return OWED_LINE.format(n=len(names), total=numbers.show(got["total"]), names=shown)


def start_background_lint(vault):
    """背景健檢的觸發（D-221、D-227）：kb.lint.runner.opening 判斷要不要開、開了不等。出錯只記 log。
    要先補位置或先 kb upgrade --finish 時不開。"""
    if yield_to_upgrade(vault) or over_budget(BACKGROUND_LINT_BUDGET):
        return
    try:
        from kb.lint import runner

        runner.opening(vault)
    except Exception as e:  # noqa: BLE001  起不來不影響開場
        log_vault(vault, e)


def _today():
    from datetime import date

    return date.today().isoformat()


def daily_pending(vault):
    """這台今天的那批還沒做嗎。讀不到 env.json 當還沒做。"""
    try:
        from kb._lib import env

        return env.daily_done(vault) != _today()
    except Exception as e:  # noqa: BLE001
        log_vault(vault, e)
        return True


def line_wip(vault):
    """製作中（D-525 第 5 點）：清冊裡新放、改過、還沒說整理的（cards.wip，不含 md）；沒有回 None。
    算太久（過了這句的時限）或狀態檔讀不到（變動比對那邊已記 log）就不印，不另記 log。"""
    from kb._lib import cards

    try:
        rows = cards.wip(vault, deadline=START + TURN_BUDGET)
    except (TimeoutError, ValueError):
        return None
    if not rows:
        return None
    names = [r["name"] for r in rows]
    return WIP_DAILY.format(n=len(rows), names="、".join(names[:NAMES_SHOWN]) + ("…" if len(names) > NAMES_SHOWN else ""))


def daily_lines(vault):
    """這台每天第一句（開場或第一句，先到先做）：版本、搬家、日程、公司還欠你（記帳 3）、製作中，順便背景開健檢；做完記今天。
    每行各自 try，出錯那行不印、其他照做。"""
    out = []
    try:
        from kb._lib import env

        env.set_daily_done(vault, _today())  # 先記：中途出錯也不重做一整天
    except Exception as e:  # noqa: BLE001
        log_vault(vault, e)
    for fn in (line_version, line_moved, line_agenda, line_owed, line_wip):
        try:
            line = fn(vault)
        except Exception as e:  # noqa: BLE001
            log_vault(vault, e)
            line = None
        if line is not None:
            out.append(line)
    start_background_lint(vault)
    return out


# ---------- 每句開頭 ----------

def _newest_mtime(paths):
    best = 0.0
    for p in paths:
        try:
            best = max(best, os.stat(p).st_mtime)
        except OSError:
            pass
    return best


_NOT_CARD_DIRS = {".system", ".git", ".obsidian", ".claude", ".trash", "系統"}


def cards_mtime(vault):
    """卡可能被改的最近修改時間：第一層（不含系統夾、暫存區）底下所有 .md，加 entities.json。"""
    from kb._lib.areas import HOLD_DIR

    skip = {s.casefold() for s in _NOT_CARD_DIRS | {HOLD_DIR}}
    best = _newest_mtime([os.path.join(str(vault), ".system", "entities.json")])
    stack = [str(vault)]
    top = True
    while stack:
        root = stack.pop()
        try:
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not (top and e.name.casefold() in skip):
                                stack.append(e.path)
                        elif e.name.endswith(".md") and not top:
                            best = max(best, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
        top = False
    return best


def profile_mtime(vault):
    from kb._lib import profile

    return _newest_mtime([os.path.join(str(vault), *profile.PROFILE_POSIX.split("/")),
                          os.path.join(str(vault), *profile.MD_POSIX.split("/"))])


REBASE_LINE = "同步卡住了（rebase 到一半），請聯絡服務人員：跟客戶說一句，不要自己動 git。"
PULLED_LINE = ("git 同步進來 {n} 個檔（{names}）：那是同事動的，跟客戶說「可能是同事動的」、不要說「你」，也不要從中推測客戶的意思。")
CONFLICT_CARD_LINE = ("你跟 {other} 同時改了「{name}」的卡：我留了 {other} 那版、標成要重做，客戶改的那段在 kb cards edits，"
                      "照手改流程補回去；跟客戶說一句收拾了什麼。")
CONFLICT_FILE_LINE = "你跟 {other} 同時改了「{name}」：兩版都留著（另存「{copy}」），問客戶要用哪一版、不要自己挑。"
BIG_LINE = ("「{name}」太大（{mb} MB）推不上 git，這台先不同步：{where}")
BIG_CLOUD = "問客戶要不要搬到團隊雲端的「{cloud}」夾（說好跑 kb source-edit move <檔> --to <夾>）。"
BIG_LOCAL = "建議放到團隊共用的雲端夾。"
REFRESH_NAMES_SHOWN = 3


CARRIED_LINE = ("她在{page}寫了：『{text}』（id {cid}）。照意思記成資料（日程照 D-289 判斷誰的），回答裡說一句記成什麼；看不懂才問。"
                "處理完跑 kb renders drop {cid}")


def carried_lines(vault):
    """renders.json 的 carried 還沒交給 AI 的，每筆一行、記 handed（只交一次）。"""
    from kb._lib import hand, renders

    rows = renders.unhanded(vault)
    if not rows:
        return []
    out = [CARRIED_LINE.format(page=hand.PAGE_NAMES.get(rel, rel), text=renders.carried_line(c), cid=c["id"]) for rel, c in rows]
    renders.mark_handed(vault, [c["id"] for _rel, c in rows])
    return out


def refresh_lines(vault):
    """git HEAD 變了（或 rebase 做到一半）才叫 kb refresh 的本體；回 [(行, once)]。不是 git 或沒變什麼都不做。"""
    from kb._lib import git, refresh

    if not git.is_repo(vault):
        return []
    rebase = git.rebase_in_progress(vault)
    if not rebase and not refresh.head_changed(vault):
        return []
    got = refresh.run(vault)
    out = []
    if got.get("rebase"):
        out.append((REBASE_LINE, True))
    for c in got.get("conflicts") or []:
        name = os.path.basename(c["path"])
        if c["kind"] == "card":
            out.append((CONFLICT_CARD_LINE.format(other=c["other"], name=os.path.splitext(name)[0]), False))
        else:
            out.append((CONFLICT_FILE_LINE.format(other=c["other"], name=name, copy=os.path.basename(c["copy"] or "")), False))
    for b in got.get("big") or []:
        where = BIG_CLOUD.format(cloud=b["cloud"]) if b.get("cloud") else BIG_LOCAL
        out.append((BIG_LINE.format(name=os.path.basename(b["path"]), mb=b["mb"], where=where), False))
    from kb._lib.skeleton import UNTRACKED
    program = {".gitignore", *UNTRACKED}  # 升級換的 .gitignore、不再追蹤的設定檔 md 是程式動的，不說成同事改的（#42 回測）
    pulled = [p for p in got.get("pulled") or [] if not str(p).startswith(".system/") and str(p) not in program]  # 流水檔不列給 AI（#45）
    if pulled and not got.get("skipped"):
        names = "、".join(os.path.basename(p) for p in pulled[:REFRESH_NAMES_SHOWN]) + ("…" if len(pulled) > REFRESH_NAMES_SHOWN else "")
        out.append((PULLED_LINE.format(n=len(pulled), names=names), False))
    return out


def turn_lines(vault, session_id):
    """每句開頭要給 AI 的行（additionalContext），照順序、時間到就停。沒有 session_id（終端機）不做。"""
    if not session_id:
        return []
    from kb._lib import turn

    try:
        global TURN_BUDGET
        from kb._lib import config

        TURN_BUDGET = config.get(vault, "turn_scan_seconds")
    except Exception as e:  # noqa: BLE001  讀不到設定就用預設
        log_vault(vault, e)
    checks = turn.get_checks(vault, session_id)
    stamps = dict(checks.get("stamps") or {})
    told = list(checks.get("told") or [])
    told_files = list(checks.get("told_files") or [])
    out = []

    def tell(line, once=False):
        """once：同一個對話講過同一句就不再印。"""
        if line is None or (once and line in told):
            return
        if once:
            told.append(line)
        out.append(line)

    def guarded(fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            log_vault(vault, e)
            return None

    if daily_pending(vault):
        for line in guarded(daily_lines, vault) or []:
            tell(line)
    tell(guarded(line_who, vault), once=True)  # 多人而這台沒記是誰：同一個對話只講一次（D-286）
    # 0. git 對帳（多人共用 3，D-293）：HEAD 變了才叫，排在檔案比對之前；跑完才給比對（對帳不能停一半）
    for line, once in guarded(refresh_lines, vault) or []:
        tell(line, once=once)
    # 1. 檔案比對：搬動、不見、新增寫進這一句的 detected.jsonl（by outside）；變動行與整理提示在最後一步用它的結果
    got = None
    detected = list(checks.get("detected") or [])
    learning = guarded(line_learning, vault)  # 新同事第一次：認完檔案之前不比對、不報變動（D-300）
    tell(learning, once=True)
    if learning:
        checks_done = {"stamps": stamps, "told": told[-TOLD_KEEP:], "told_files": told_files[-FILES_KEEP:],
                       "detected": detected}
        try:
            turn.set_checks(vault, session_id, checks_done)
        except Exception as e:  # noqa: BLE001
            log_vault(vault, e)
        return out
    if not over_budget():
        got = guarded(file_compare, vault)
        detected = guarded(record_detected, vault, session_id, got, "outside", detected) or detected
    # 2. 卡、標籤：比修改時間，沒變不讀
    stamp_now = time.time()
    if not over_budget():
        newest = guarded(cards_mtime, vault) or 0.0
        if newest > stamps.get("cards", 0.0):
            edits, done = guarded(line_card_edits, vault) or (None, True)
            tell(edits, once=not done)
            if not over_budget():
                tell(guarded(line_tags, vault))
                if done:
                    stamps["cards"] = stamp_now
    if not over_budget():
        newest = guarded(profile_mtime, vault) or 0.0
        if newest > stamps.get("profile", 0.0):
            tell(guarded(line_profile, vault))
            stamps["profile"] = stamp_now
    if got is not None or not over_budget():  # 比對本身有自己的時限；比完後組句子很快，比過了就不再看這裡的預算
        tell(guarded(line_missing_sources, vault))
        moved = guarded(line_moved_files, vault, told)
        if moved and not yield_to_upgrade(vault):
            tell(moved[0])
            told.extend(moved[1])
        lines, told_files = guarded(change_lines, vault, told_files, session_id) or ([], told_files)
        for line in lines:
            tell(line, once=line == CHANGES_INCOMPLETE)
        tell(guarded(line_drift, vault), once=True)
    for line in guarded(carried_lines, vault) or []:  # 程式頁上沒照格式的字（D-299）：每行只交一次
        tell(line)
    checks = {"stamps": stamps, "told": told[-TOLD_KEEP:], "told_files": told_files[-FILES_KEEP:], "detected": detected}
    try:
        turn.set_checks(vault, session_id, checks)
    except Exception as e:  # noqa: BLE001
        log_vault(vault, e)
    return out
