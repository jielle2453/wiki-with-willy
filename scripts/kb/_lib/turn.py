"""回合：每句話一個回合（D-074）。目前回合按 session 存 .system/turns/<sid>.json，另有 current.json 給 CLI。
回合備份住 .system/versions/<turn>/：turn.json、manifest.jsonl（一行一檔）、files/<rel>（modify 與 delete 的舊版）、
folders.jsonl（kb 程式改的資料夾，一行一個動作，D-142）。
綜合卡大改動攔截拿掉了（放寬丙 5，D-322 第 18 件）：改了幾張只記進 ai_synthesis（工作日誌程式段記張數），不擋；舊回合檔的 approved 欄略過。
相對路徑保留原始大小寫（I-027：paths.normalize 會小寫，備份與還原不能用它）。
整批製卡（card-maker，D-195）：kb.py 收全域 --turn 時 OVERRIDE 是那個回合，current() 回它、這次呼叫的備份都進它；
那個回合已經不是 session 檔的目前回合時，它的欄位（approved、ai_synthesis、note、job_result）存在 versions/<回合>/turn.json
（turn_data／save_turn）。agent_turn 從 batches.json 的 agents 查子代理算哪個回合。
回答末尾的問法（lint 題，D-225）：asks_told 是這個 session 說過「還有 N 件要你決定的事」了，start 從同 session 的上一句帶下去；
touched 是這一句查過、動過的實體與檔（kb lookup、convert、cards 在對話裡跑時記），kb asks 照它判斷哪些題跟這一句有關。
"""
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from kb._lib import config, credentials, ledger
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

TURNS_REL = Path(".system") / "turns"
VERSIONS_REL = Path(".system") / "versions"
CURRENT_NAME = "current.json"
BACKUP_FAILED_REASON = "備份失敗，這次不寫。說「檢查」我看原因"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _now():
    return datetime.now()


def _safe_sid(session_id):
    return _UNSAFE.sub("_", str(session_id))


def _session_file(vault, session_id):
    return Path(vault) / TURNS_REL / f"{_safe_sid(session_id)}.json"


def _current_file(vault):
    return Path(vault) / TURNS_REL / CURRENT_NAME


def prompt_head(vault, prompt):
    """先剝 <pasted_content> 標籤（B-031，標籤換一個空格、內文留著）、壓空白再遮帳密、截 prompt_head_max 字（D-081、B-027、B-028）：
    回合檔、versions/<回合>/turn.json、工作日誌的「問」都從這裡來。
    換空格不換空字串：abc</pasted_content>otp 998877 黏成 abcotp 就遮不到（開場注入測試後修正 M1）。
    先壓再遮：值跟關鍵詞隔一個空行時 mask_secrets 會停在空行，壓成一行才遮得到（工作日誌測試後修正 M2）。"""
    text = re.sub(r"</?pasted_content\b[^>]*>", " ", str(prompt or ""), flags=re.IGNORECASE)
    return credentials.mask_secrets(" ".join(text.split()), credentials.known_names(vault))[:config.get(vault, "prompt_head_max")]


def journal_key(data):
    """這個回合在工作日誌裡那一行的 key：退回自己這句時開的新回合記著原回合（journal_turn），沒有就是自己。"""
    return data.get("journal_turn") or data["turn"]


def start(vault, session_id, prompt, inherit=None, transcript=None):
    """開新回合：寫 turns/<sid>.json（有 sid 才寫）與 current.json。回回合 dict。
    inherit（工作日誌第二輪 R2-M2）：對話裡退回自己這句時，新回合接著原回合，繼承它的問、note，
    並記 journal_turn（原回合的 journal_turn，沒有就原回合），Stop 與補記才收原回合那一行。
    transcript（收件匣第 3 點）：hook 給的對話紀錄路徑，只存路徑不讀內容（kb intake --pasted 從那裡取貼的圖與文字）；
    inherit 開的不帶舊的。"""
    now = _now().replace(microsecond=0)  # 代號只到秒，比較也只到秒
    head = inherit.get("prompt_head", "") if inherit else prompt_head(vault, prompt)
    tag = _safe_sid(session_id)[:4] if session_id else "0000"
    # 代號單調遞增：取 max(現在, 既有最大代號 + 1 秒)。同一秒幾句話不撞，時鐘落後也不會排到舊回合前面
    latest = _latest_turn_time(vault, session_id, now)
    if latest is not None and now <= latest:
        now = latest + timedelta(seconds=1)
    data = {
        "schema_version": 1,
        "turn": f"{now:%Y%m%d-%H%M%S}-{tag}",
        "session_id": str(session_id) if session_id else "",
        "started": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt_head": head,
        "note": inherit.get("note", "") if inherit else "",  # 模型叫 kb turn note 給的「做了」（D-084）
        "done": False,  # Stop 或中斷補記後為 true（D-083）
    }
    if transcript:
        data["transcript"] = str(transcript)
    if inherit:
        data["journal_turn"] = journal_key(inherit)
        if inherit.get("program_notes"):  # 指令自帶的「做了」也接著（#46）
            data["program_notes"] = list(inherit["program_notes"])
        for flag in MARK_FLAGS:  # 同一句話跑過整理、按過停止，退回自己後照樣認得（answer 裁定 21）
            if inherit.get(flag):
                data[flag] = True
    (Path(vault) / TURNS_REL).mkdir(parents=True, exist_ok=True)
    if session_id:
        old = _read_turn(_session_file(vault, session_id))
        if old and old.get(SESSION_FLAG):
            data[SESSION_FLAG] = True  # 「還有 N 件…」一個對話只說一次（D-225）：跟著每一句帶下去
        if old and isinstance(old.get("checks"), dict):
            data["checks"] = old["checks"]  # 每句開頭的檢查記的上次時間與講過的事（hooks/_checks.py，放寬丙 3b）
        _keep(vault, old)
        write_json_atomic(_session_file(vault, session_id), data)
    write_json_atomic(_current_file(vault), data)
    return data


def _keep(vault, old):
    """回合檔要被下一句換掉前：那個回合有 versions/<回合>/turn.json（整批整理的 job 回合一定有）就把欄位併進去，
    之後 turn_data 才讀得到它的 approved、ai_synthesis、secret_shown（card-maker）。寫不了不擋開新回合。"""
    if not old:
        return
    meta = _meta_file(vault, old["turn"])
    try:
        if meta.is_file():
            kept = read_json(meta, {}) or {}
            kept.update(old)
            write_json_atomic(meta, kept)
    except (OSError, ValueError) as e:
        ledger.log_error(vault, f"回合 {old['turn']} 的欄位沒留到 versions：{type(e).__name__}: {e}")


_TURN_TIME = re.compile(r"^(\d{8}-\d{6})-")


def _turn_time(turn_id):
    m = _TURN_TIME.match(str(turn_id or ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


FUTURE_TOLERANCE = timedelta(days=1)


def _latest_turn_time(vault, session_id, now):
    """既有回合裡最晚的時間：這個 session 的回合、current.json、versions/ 底下每個夾名。
    比現在晚超過一天的代號不算（時鐘曾經錯過），忽略時寫一行 log（第二輪 R2-L4）。
    """
    ids = []
    for t in (_read_turn(_session_file(vault, session_id)) if session_id else None, _read_turn(_current_file(vault))):
        if t:
            ids.append(t.get("turn"))
    root = Path(vault) / VERSIONS_REL
    if root.is_dir():
        ids.extend(p.name for p in root.iterdir() if p.is_dir())
    times = []
    for i in ids:
        x = _turn_time(i)
        if x is None:
            continue
        if x - now > FUTURE_TOLERANCE:
            ledger.log_error(vault, f"回合代號 {i} 比現在晚超過一天，算新代號時不理它")
            continue
        times.append(x)
    return max(times) if times else None


OVERRIDE = None  # kb.py --turn 給的回合代號（cli 設，一次呼叫一個行程）
SESSION = None  # kb.py --session 給的 session（守門替主對話的每個單一 kb 呼叫插的，D-506；兩個視窗不會串）
VIA = None  # kb.py --via 給的來源（只有 "obsidian"，Obsidian 外掛帶的，#22 D-255）：這次呼叫自己開一個回合，不碰 current.json 與 turns/
_VIA_TURNS = {}  # 知識庫 → 這個行程裡 Obsidian 的回合（同一次呼叫內沿用同一個）


class NoSuchTurn(ValueError):
    pass


def in_conversation(vault):
    """對話裡叫的（D-085 補）：current.json 有 session_id 且 done 為 false，改動併進那一句、Stop 會補記，不另寫程式段。
    對話答完（done true）後在終端機跑的不算。只讀不開新回合；versions 在動作前判斷。
    kb.py 帶 --turn（card-maker 的呼叫，守門插的）或 --session（守門替主對話每個單一 kb 呼叫插的，D-506）一律算對話裡（D-195）。
    回合已經答完、但這個 session 正在背景整理（或剛整理完）時主對話跑的也算（收通知那一輪，card-maker 第一輪修）。
    日誌（journal_cli）與 current() 共用這一份判斷（D-502 裁定 2）。
    Obsidian 外掛叫的（--via）永遠不算：它有自己的回合，工作日誌要寫程式段（D-255）。"""
    if VIA:
        return False
    if OVERRIDE or SESSION:
        return True
    try:
        data = _read_turn(_current_file(vault))
    except OSError:
        return False
    if not (data and data.get("session_id")):
        return False
    if not data.get("done"):
        return True
    from kb._lib import batches
    return batches.active_job(vault, data["session_id"], config.get(vault, "card_batch_expire_minutes"))


def current(vault, session_id=None):
    """有 --turn（OVERRIDE）回那個回合；有 sid（沒給取 --session 的 SESSION）讀 turns/<sid>.json，檔不在就開這個 session 的
    新回合，不讀 current.json（別的對話可能剛開口，D-506）；沒有 sid 讀 current.json，都沒有就開一個（hook 沒跑到也要有回合）。
    current.json 是對話回合、但已不在對話裡（in_conversation 為假：答完了、也沒有背景整理）→ 對話外跑的，開終端機回合
    （尾 0000）不掛回那句（D-502）；還在回答中照舊沿用（D-085）。"""
    if OVERRIDE:
        data = turn_data(vault, OVERRIDE)
        if data is None:
            raise NoSuchTurn(OVERRIDE)
        return data
    if VIA:
        return _via_turn(vault)
    sid = session_id or SESSION
    if sid:
        return _read_turn(_session_file(vault, sid)) or start(vault, sid, "")
    data = _read_turn(_current_file(vault))
    if data and data.get("session_id") and not in_conversation(vault):
        return start(vault, None, "")
    if data:
        return data
    return start(vault, session_id, "")


def _via_turn(vault):
    """--via obsidian 的回合（D-255）：第一次要用時才開（只讀的指令不開），同一次呼叫沿用同一個。代號尾 obsd，
    只寫 versions/<回合>/turn.json（source: obsidian）：不寫 turns/ 也不寫 current.json，所以不併進正在回答的對話、
    對話裡的 current() 也看不到它；程式段由 journal 認 source。"""
    key = str(vault)
    got = _VIA_TURNS.get(key)
    if got is not None:
        return got
    now = _now().replace(microsecond=0)
    latest = _latest_turn_time(vault, None, now)
    if latest is not None and now <= latest:
        now = latest + timedelta(seconds=1)
    tid = f"{now:%Y%m%d-%H%M%S}-obsd"
    data = {"schema_version": 1, "turn": tid, "session_id": "", "started": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_head": "", "source": "obsidian", "done": True, "note": ""}
    meta = _meta_file(vault, tid)
    meta.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(meta, data)
    _VIA_TURNS[key] = data
    return data


def _now_file(vault):
    """這次呼叫的「目前回合檔」：有 --session 是那個對話的 turns/<sid>.json，沒有是 current.json（D-506）。
    --via obsidian 沒有對話：回一個永遠不存在的檔，讀目前回合的（mark、touch、last、_set）全落空，不會記到正在回答的對話上。"""
    if VIA:
        return Path(vault) / TURNS_REL / "via-obsidian.none"
    return _session_file(vault, SESSION) if SESSION else _current_file(vault)


def now(vault):
    """這次呼叫的回合（#20 裁定 6）：有 --turn（OVERRIDE）是那個回合，有 --session 讀那個對話的回合檔，都沒有讀 current.json；
    只讀，沒有回 None、不開新回合。讀「這一句」的旗標、touched、回合代號用這支，不要直接讀 current.json。"""
    if OVERRIDE:
        return turn_data(vault, OVERRIDE)
    if VIA:
        return _VIA_TURNS.get(str(vault))
    return _read_turn(_now_file(vault))


def last(vault):
    """這個對話的回合（--session 的回合檔，沒有讀 current.json；只讀，沒有回 None、不開新回合）：turn note／redact 補記剛答完那句用
    （D-502 裁定 3）。帶 --turn 的 note 走 turn_data，不經這裡。"""
    return _read_turn(_now_file(vault))


def _read_turn(path):
    """讀回合檔。舊檔少 note、done 兩鍵：note 視為空字串、done 視為 true（升級前的回合不補記，不遷移）。"""
    try:
        data = read_json(path)
    except ValueError:
        return None
    if not (isinstance(data, dict) and data.get("turn")):
        return None
    data.setdefault("note", "")
    data.setdefault("done", True)
    return data


def _write_turn(vault, data):
    sid = data.get("session_id")
    if sid:
        write_json_atomic(_session_file(vault, sid), data)
    cur = _read_turn(_current_file(vault))
    if not sid or (cur and cur.get("turn") == data["turn"]):
        write_json_atomic(_current_file(vault), data)


def _meta_file(vault, turn_id):
    return Path(vault) / VERSIONS_REL / str(turn_id) / "turn.json"


def exists(vault, turn_id):
    """回合在嗎：turns/ 底下哪個回合檔是它，或 versions/<回合>/turn.json 在。代號只收回合代號的字元。"""
    if not turn_id or _UNSAFE.search(str(turn_id)):
        return False
    return _find_turn(vault, turn_id) is not None or _meta_file(vault, turn_id).is_file()


def turn_data(vault, turn_id):
    """某個回合的資料：還是哪個 session 的目前回合就讀回合檔，否則讀 versions/<回合>/turn.json；都沒有回 None。"""
    if not turn_id or _UNSAFE.search(str(turn_id)):
        return None
    live = _find_turn(vault, turn_id)
    if live is not None:
        return live
    try:
        meta = read_json(_meta_file(vault, turn_id))
    except ValueError:
        return None
    if not (isinstance(meta, dict) and meta.get("turn") == turn_id):
        return None
    meta.setdefault("note", "")
    meta.setdefault("done", True)
    return meta


def _turn_lock(vault, turn_id):
    """回合檔讀改寫的鎖：那個回合還是哪個 session 的目前回合就鎖那個 session 檔（跟 mark 同一把），否則鎖 versions 那份。"""
    d = Path(vault) / TURNS_REL
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        if p.name == CURRENT_NAME:
            continue
        t = _read_turn(p)
        if t and t.get("turn") == turn_id:
            return FileLock(p)
    meta = _meta_file(vault, turn_id)
    meta.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(meta)


def change_turn(vault, turn_id, change):
    """在鎖裡讀那個回合、change(data) 回真就寫回（第一輪修：並行的子代理與主對話不互相蓋掉旗標）。回合不在回 None，
    否則回 change 的結果。等不到鎖丟 TimeoutError。"""
    with _turn_lock(vault, turn_id):
        data = turn_data(vault, turn_id)
        if data is None:
            return None
        got = change(data)
        if got:
            _save_turn(vault, data)
        return got


def save_turn(vault, data):
    """把 data 的欄位併進那個回合（在鎖裡重讀再寫）。"""
    return change_turn(vault, data["turn"], lambda d: d.update(data) or True)


def _save_turn(vault, data):
    """寫回某個回合：還是 session 的目前回合就寫回合檔（同 _write_turn）；versions/<回合>/turn.json 在或回合已經不是目前的，
    一起寫那份（換了句話之後 job 回合的 approved、ai_synthesis 才留得住）。呼叫端拿著 _turn_lock。"""
    live = _find_turn(vault, data["turn"])
    if live is not None:
        _write_turn(vault, data)
    meta = _meta_file(vault, data["turn"])
    if live is None or meta.is_file():
        turn_dir(vault, data["turn"], data)
        old = read_json(meta, {}) or {}
        old.update(data)
        write_json_atomic(meta, old)


def owned(vault, turn_id, agent_id=None):
    """kb.py --turn 收不收這個回合（第一輪修 S4）：帶 --agent 而 agents 記的就是這個回合；或是這個對話的回合
    （session 取 --session，沒有取 current.json）：它目前的回合，或它的 job 回合。"""
    if agent_id and agent_turn(vault, None, agent_id) == turn_id:
        return True
    sid = SESSION
    if not sid:
        cur = _read_turn(_current_file(vault))
        sid = cur.get("session_id") if cur else None
    if not sid:
        return False
    now = _read_turn(_session_file(vault, sid))
    if now and now.get("turn") == turn_id:
        return True
    from kb._lib import batches
    return turn_id in batches.job_turns_of_session(vault, sid)


def agent_turn(vault, session_id, agent_id):
    """子代理（card-maker）的寫入算哪個回合（D-195）：batches.json 的 agents 有這個 agent_id（session 也對）就回它記的回合，
    沒有回 None。batches.json 讀不到也回 None（守門照主對話的回合備份）。"""
    if not agent_id:
        return None
    from kb._lib import batches
    try:
        data = batches.load(vault)
    except ValueError:
        return None
    for a in data["agents"]:
        if a.get("agent_id") == agent_id and (not session_id or a.get("session_id") == session_id):
            return a.get("turn")
    return None


def _set(vault, session_id, **fields):
    """改 turns/<sid>.json（沒有 sid 改 --session 那個對話的，再沒有改 current.json）；current.json 是同回合時一起改。
    回合不存在回 None，不開新的。"""
    path = _session_file(vault, session_id) if session_id else _now_file(vault)
    data = _read_turn(path)
    if data is None:
        return None
    data.update(fields)
    _write_turn(vault, data)
    return data


def set_note(vault, session_id, text):
    return _set(vault, session_id, note=text)


def get_checks(vault, session_id):
    """每句開頭檢查的記號（hooks/_checks.py）：{"stamps": {檢查名: 上次檢查的時間}, "told": [這個對話講過的行]}；沒有回 {}。只讀。"""
    data = _read_turn(_session_file(vault, session_id)) if session_id else None
    checks = (data or {}).get("checks")
    return dict(checks) if isinstance(checks, dict) else {}


def set_checks(vault, session_id, checks):
    """寫回 get_checks 那份；在 session 檔的鎖裡讀改寫，回合不存在不開新的。"""
    if not session_id:
        return None
    with FileLock(_session_file(vault, session_id)):
        return _set(vault, session_id, checks=dict(checks))


def set_done(vault, session_id):
    return _set(vault, session_id, done=True)


def set_prompt_head(vault, session_id, text):
    return _set(vault, session_id, prompt_head=text)


# mark 記的回合旗標；退回自己這句時 start(inherit=) 帶過去。secret_shown（密碼與守門）：這句回答給了密碼，Stop 不記結果；
# asks_told（lint 題，D-225）：這個對話說過「還有 N 件要你決定的事」，start 從同 session 的上一句帶下去
MARK_FLAGS = ("ingest", "ingest_stopped", "secret_shown", "asks_told", "codex")  # codex：Codex 的對話（hooks/turn.py 每句標，#56）
SESSION_FLAG = "asks_told"


def mark(vault, key):
    """目前回合檔設一個旗標（回合狀態，不是知識；answer 第 9 點）：只在對話裡的回合（--session 那個對話的回合檔，沒有讀 current.json；
    有 session_id 且還沒答完），終端機跑的不記。已經是 true 不寫。讀改寫在 session 檔的鎖裡（answer 裁定 21），等不到鎖丟
    TimeoutError、什麼都不寫。回這個回合現在有沒有這個旗標。"""
    data = _read_turn(_now_file(vault))
    if not data or not data.get("session_id") or data.get("done"):
        return False
    if data.get(key) is True:
        return True
    with FileLock(_session_file(vault, data["session_id"])):
        sess = _read_turn(_session_file(vault, data["session_id"]))
        if not sess or sess.get("turn") != data["turn"]:  # session 檔已經是別的回合：不記到錯的那句
            return False
        return _set(vault, data["session_id"], **{key: True}) is not None


def mark_session(vault, session_id, key):
    """替 session_id 那個對話的目前回合設旗標（守門用，#20 裁定 12：帶密碼值的 kb 呼叫不改寫指令，守門直接替那個對話標
    secret_shown）。只讀 turns/<sid>.json、不讀 current.json、不開新回合；回合答完了也標（多標可以接受）。在 session 檔的鎖裡讀改寫。
    回有沒有標上。"""
    assert key in MARK_FLAGS, key
    if not session_id:
        return False
    with FileLock(_session_file(vault, session_id)):
        return _set(vault, session_id, **{key: True}) is not None


def touch(vault, entities=(), files=()):
    """這一句查過、動過的對象記進回合的 touched（{"entities": [...], "files": [...]}，lint 題 D-225）：有 --turn（子代理）記進那個回合；
    否則只在對話裡的回合（同 mark，--session 那個對話的），終端機跑的不記。在回合檔的鎖裡讀改寫；寫不了不擋呼叫的指令（回 False）。"""
    ents, fids = [str(x) for x in entities or [] if x], [str(x) for x in files or [] if x]
    if not ents and not fids:
        return False
    if OVERRIDE:
        tid = OVERRIDE
    else:
        data = _read_turn(_now_file(vault))
        if not data or not data.get("session_id") or data.get("done"):
            return False
        tid = data["turn"]

    def change(d):
        got = dict(d.get("touched") or {})
        new_e = list(dict.fromkeys(list(got.get("entities") or []) + ents))
        new_f = list(dict.fromkeys(list(got.get("files") or []) + fids))
        if new_e == list(got.get("entities") or []) and new_f == list(got.get("files") or []):
            return False
        d["touched"] = {"entities": new_e, "files": new_f}
        return True
    try:
        return bool(change_turn(vault, tid, change))
    except (OSError, ValueError) as e:
        ledger.log_error(vault, f"touched 沒記到：{type(e).__name__}: {e}")
        return False


def add_program_note(vault, text):
    """對話裡叫的 kb 寫入指令那句白話（record_program_or_warn 的 note，先遮好）加進回合的 program_notes（#46）：
    Stop 沒有 AI 的 turn note 時拿它當「做了」。回合的找法同 touch（--turn 是那個回合，否則對話裡、還沒答完的回合）；
    寫不了不擋指令（回 False）。"""
    text = str(text or "").strip()
    if not text:
        return False
    if OVERRIDE:
        tid = OVERRIDE
    else:
        data = _read_turn(_now_file(vault))
        if not data or not data.get("session_id") or data.get("done"):
            return False
        tid = data["turn"]
    try:
        return bool(change_turn(vault, tid, lambda d: d.update(program_notes=list(d.get("program_notes") or []) + [text]) or True))
    except (OSError, ValueError, TimeoutError) as e:
        ledger.log_error(vault, f"program_notes 沒記到：{type(e).__name__}: {e}")
        return False


def touched(data):
    """回合資料的 touched → {"entities": set, "files": set}。"""
    got = (data or {}).get("touched") or {}
    return {"entities": set(got.get("entities") or []), "files": set(got.get("files") or [])}


def _realcase(p):
    """存在的部分以 realpath 為準（磁碟上的大小寫、junction），不存在的尾段原樣接回。"""
    cur, tail = p, []
    while not os.path.exists(cur):
        head, name = os.path.split(cur)
        if not name or head == cur:
            return p
        tail.insert(0, name)
        cur = head
    return os.path.join(os.path.realpath(cur), *tail) if tail else os.path.realpath(cur)


def rel_path(vault, target, cwd):
    """目標相對總資料夾的路徑，正斜線、原始大小寫。不在總資料夾裡丟 ValueError。
    字串層的寫法（引號、\\\\?\\、file://、~、Git Bash 的 /c/、磁碟相對）跟守門一樣交給 paths._text_forms（第二輪 R2-L2／L7），
    之後才用 realpath 取磁碟上的大小寫；normalize 本身會小寫，所以這裡不用它（I-027）。
    """
    from kb._lib.paths import _text_forms
    target, drive_rel = _text_forms(target)
    if drive_rel or not os.path.isabs(target):
        target = os.path.join(str(cwd or os.getcwd()), target)
    real_t = _realcase(os.path.abspath(target))
    real_v = os.path.realpath(str(vault))
    nc_t, nc_v = os.path.normcase(real_t), os.path.normcase(real_v).rstrip("\\/")
    if not nc_t.startswith(nc_v + os.sep):
        raise ValueError(f"不在總資料夾裡：{target}")
    return real_t[len(nc_v) + 1:].replace("\\", "/")


def turn_dir(vault, turn_id, info=None):
    """.system/versions/<turn_id>/，第一次建時寫 turn.json。接著原回合的回合帶 journal_turn，redact 才找得到整條鏈（R3-L2）。"""
    vdir = Path(vault) / VERSIONS_REL / turn_id
    vdir.mkdir(parents=True, exist_ok=True)
    meta = vdir / "turn.json"
    if not meta.exists():
        info = info or _find_turn(vault, turn_id) or {}
        data = {
            "schema_version": 1, "turn": turn_id,
            "session_id": info.get("session_id", ""),
            "started": info.get("started", _now().strftime("%Y-%m-%dT%H:%M:%S")),
            "prompt_head": info.get("prompt_head", ""),
        }
        if info.get("journal_turn"):
            data["journal_turn"] = info["journal_turn"]
        write_json_atomic(meta, data)
    return vdir


def _find_turn(vault, turn_id):
    d = Path(vault) / TURNS_REL
    if not d.is_dir():
        return None
    for p in [d / CURRENT_NAME, *sorted(d.glob("*.json"))]:
        data = _read_turn(p)
        if data and data.get("turn") == turn_id:
            return data
    return None


def entries(vault, turn_id):
    """讀 manifest.jsonl。半截行跳過並寫一行 hook-errors.log，不讓之後全部 deny（第一輪 L4）。"""
    m = Path(vault) / VERSIONS_REL / turn_id / "manifest.jsonl"
    if not m.is_file():
        return []
    rows = []
    with open(m, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as e:
                ledger.log_error(vault, f"manifest 壞行跳過 {turn_id}:{lineno}: {type(e).__name__}: {e}")
                continue
            if isinstance(row, dict) and row.get("path"):
                rows.append(row)
            else:
                ledger.log_error(vault, f"manifest 壞行跳過 {turn_id}:{lineno}: 不是有 path 的物件")
    return rows


def backup(vault, rel, turn_id, action=None, by=None, link_from=None):
    """寫入前把舊版複製到 versions/<turn_id>/files/<rel>，manifest 追加一行。同回合同路徑只做第一次；第二次以後只在
    writes.jsonl 記這次寫的時間（#20 裁定 4，manifest 不動）。
    action 預設存在 modify、不存在 create；呼叫端刪檔前傳 delete。
    by 只收 "program"：程式自己改的（綜合卡檔頭快取重渲染、改名、合併），寫進那行，大改動門檻不數（實體與綜合卡第 9 點）。
    查重、複製、記帳在同一把鎖裡（第一輪 M1／L3）；複製失敗或鎖逾時丟 OSError（TimeoutError 也是）。
    link_from：見 _keep_old（D-529）。
    """
    if by not in (None, "program"):
        raise ValueError(f"backup 的 by 只收 program：{by!r}")
    target = Path(vault) / rel
    from kb._lib import streams
    virtual = streams.virtual_kind(target)
    if virtual is not None and target.is_file() and not streams.exists(*virtual):
        virtual = None  # 還是舊的整份 JSON（沒轉成流水檔）：照舊整份備份，退回時副本被換回舊內容會被當成一次整份改動吸收
    if virtual is not None:  # 流水檔：不整份備份；改動由 streams.write 記在回合的 streams.jsonl，manifest 只記「碰過」（stream: true，多人共用 1）
        streams.note_turn(virtual[0], virtual[1], turn_id)
        if virtual[1] in streams.SPLIT:  # 本機那一半不在流水檔裡，照舊整份備份（整個回合退回時一起換回）
            backup(vault, f".system/local/{virtual[1]}.json", turn_id)
    vdir = turn_dir(vault, turn_id)
    manifest = vdir / "manifest.jsonl"
    with FileLock(manifest):
        if any(e.get("path") == rel for e in entries(vault, turn_id)):
            _note_write(vault, vdir, turn_id, rel)
            return
        if virtual is not None:
            row = {"path": rel, "action": action or ("modify" if target.is_file() else "create"), "time": _now().strftime("%Y-%m-%dT%H:%M:%S"), "stream": True}
            if by:
                row["by"] = by
            _append_line(manifest, row)
            return
        exists = target.is_file()
        action = action or ("modify" if exists else "create")
        row = {"path": rel, "action": action, "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
        if exists:
            _keep_old(target, vdir / "files" / rel, link_from, row)
        if by:
            row["by"] = by
        _append_line(manifest, row)


CLOUD_ATTRS = 0x400 | 0x1000 | 0x40000 | 0x80000 | 0x100000 | 0x400000  # 重解析點、離線、雲端占位、釘選／取消釘選（OneDrive 這類）


def _cloud(path):
    return bool(getattr(os.stat(path), "st_file_attributes", 0) & CLOUD_ATTRS)


def _keep_old(src, dst, link_from, row):
    """舊版放進回合。link_from（D-529 改 Office 的版本檔，內容同 src）：硬連結過去不多存一份，row 記 sha256 讓退回時
    驗它沒被原地改過；同步夾（雲端屬性，OneDrive 可能把它換成只在雲端）、別的磁碟、連不了就照舊複製。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if link_from is not None:
        try:
            if not (_cloud(src) or _cloud(link_from)):  # 新建的版本檔未必已被標上，原檔也看
                os.link(link_from, dst)
                from kb._lib import inventory
                row["sha256"] = inventory.sha256_of(dst)
                return
        except OSError:
            row.pop("sha256", None)
            if os.path.lexists(dst):  # 連上了才失敗：先拆掉，免得下面的複製寫進版本檔本身
                os.unlink(dst)
    shutil.copy2(src, dst)


def backup_source(vault, target, cwd, turn_id, sources=None, link_from=None):
    """登記的來源（Z9，知識庫外）寫入前備份（放寬甲 D-306）：舊版複製到 versions/<回合>/files/_src/<別名>/<相對路徑>，
    manifest 那行多 uri（file://<別名>/<相對路徑>）；退回時 versions 照 uri 經 inventory.path_of 放回。同回合同路徑只做第一次。
    link_from：見 _keep_old（D-529）。
    target 不在任何登記的來源裡丟 ValueError；複製失敗丟 OSError。回 uri。"""
    from kb._lib import sources as sources_lib
    from kb._lib.paths import _text_forms, inside, normalize
    sources = sources or sources_lib.load(vault)
    text, drive_rel = _text_forms(target)
    if drive_rel or not os.path.isabs(text):
        text = os.path.join(str(cwd or os.getcwd()), text)
    real = _realcase(os.path.abspath(text))
    real_n = normalize(real, None)
    best = None
    for src in sources.entries:
        if inside(real_n, src["root"]) and real_n != src["root"] and (best is None or len(src["root"]) > len(best["root"])):
            best = src
    if best is None:
        raise ValueError(f"不在登記的來源裡：{target}")
    rel = real.replace("\\", "/")[len(best["root"]) + 1:]
    uri = f"file://{best['alias']}/{rel}"
    key = f"_src/{best['alias']}/{rel}"
    vdir = turn_dir(vault, turn_id)
    manifest = vdir / "manifest.jsonl"
    with FileLock(manifest):
        if any(e.get("path") == key for e in entries(vault, turn_id)):
            _note_write(vault, vdir, turn_id, key)
            return uri
        src_file = Path(real)
        exists = src_file.is_file()
        row = {"path": key, "uri": uri, "action": "modify" if exists else "create", "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
        if exists:
            _keep_old(src_file, vdir / "files" / key, link_from, row)
        _append_line(manifest, row)
    return uri


def _append_line(path, row):
    """jsonl 追加一行；檔尾是半截行先補換行，新的一行不被吃掉（第二輪 R2-L5）。呼叫端拿著 manifest 鎖。"""
    lead = ""
    if path.is_file() and path.stat().st_size > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lead = "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(lead + json.dumps(row, ensure_ascii=False) + "\n")


def _note_write(vault, vdir, turn_id, rel):
    """同路徑再寫一次：writes.jsonl 那個路徑只留一行、時間換成這次（重疊檢查只看最晚的，一個 job 改幾百次總目錄也只一行）。
    呼叫端拿著 manifest 鎖。"""
    when = _now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = [r for r in writes(vault, turn_id) if r.get("path") != rel] + [{"path": rel, "time": when}]
    write_text_atomic(vdir / "writes.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def writes(vault, turn_id):
    """讀 writes.jsonl（#20 裁定 4：同回合同路徑第二次以後的寫入，一個路徑一行 {path, time}，時間是最後一次）；壞行跳過並寫一行 hook-errors.log。
    manifest 只記第一次（備份），退回的重疊檢查要知道這回合最後一次寫是什麼時候。"""
    path = Path(vault) / VERSIONS_REL / turn_id / "writes.jsonl"
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as e:
                ledger.log_error(vault, f"writes 壞行跳過 {turn_id}:{lineno}: {type(e).__name__}: {e}")
                continue
            if isinstance(row, dict) and row.get("path"):
                rows.append(row)
            else:
                ledger.log_error(vault, f"writes 壞行跳過 {turn_id}:{lineno}: 不是有 path 的物件")
    return rows


FOLDER_OPS = ("move", "make", "remove")


def record_folder(vault, turn_id, op, src, dst=None):
    """kb 程式改了資料夾（D-142）：versions/<turn>/folders.jsonl 追加一行 {"op","from","to","time"}，路徑是總資料夾相對的 POSIX。
    op：move（整夾改名或搬，from → to）、make（建了 from）、remove（拿掉空夾 from）。跟 backup 同一把 manifest 鎖；
    manifest 的既有讀者（list 計數、日誌、大改動門檻）不受影響。"""
    if op not in FOLDER_OPS:
        raise ValueError(f"資料夾動作只收 {'、'.join(FOLDER_OPS)}：{op!r}")
    vdir = turn_dir(vault, turn_id)
    path = vdir / "folders.jsonl"
    row = {"op": op, "from": str(src).replace("\\", "/"), "to": str(dst).replace("\\", "/") if dst else None,
           "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
    with FileLock(vdir / "manifest.jsonl"):
        lead = ""
        if path.is_file() and path.stat().st_size > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    lead = "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + json.dumps(row, ensure_ascii=False) + "\n")


def folders(vault, turn_id):
    """讀 folders.jsonl（照記的順序）；壞行跳過並寫一行 hook-errors.log（同 entries）。"""
    path = Path(vault) / VERSIONS_REL / turn_id / "folders.jsonl"
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as e:
                ledger.log_error(vault, f"folders 壞行跳過 {turn_id}:{lineno}: {type(e).__name__}: {e}")
                continue
            if isinstance(row, dict) and row.get("op") in FOLDER_OPS and row.get("from"):
                rows.append(row)
            else:
                ledger.log_error(vault, f"folders 壞行跳過 {turn_id}:{lineno}: 不是資料夾動作")
    return rows


MOVE_OPS = ("move", "rename", "trash")
MOVE_KINDS = ("file", "folder")


def record_move(vault, turn_id, op, kind, from_uri, to_uri=None):
    """kb source-edit 動了客戶的檔或夾（D-175）：versions/<turn>/moves.jsonl 追加一行 {"op","kind","from","to","time"}，
    記清冊 uri（file://<別名>/<相對路徑>）不記絕對路徑；trash 的 to 是 None。跟 record_folder 同一把 manifest 鎖。"""
    if op not in MOVE_OPS:
        raise ValueError(f"搬動只收 {'、'.join(MOVE_OPS)}：{op!r}")
    if kind not in MOVE_KINDS:
        raise ValueError(f"搬動的對象只收 {'、'.join(MOVE_KINDS)}：{kind!r}")
    vdir = turn_dir(vault, turn_id)
    path = vdir / "moves.jsonl"
    row = {"op": op, "kind": kind, "from": str(from_uri), "to": str(to_uri) if to_uri else None,
           "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
    with FileLock(vdir / "manifest.jsonl"):
        lead = ""
        if path.is_file() and path.stat().st_size > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    lead = "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + json.dumps(row, ensure_ascii=False) + "\n")


def moves(vault, turn_id):
    """讀 moves.jsonl（照記的順序）；壞行跳過並寫一行 hook-errors.log（同 folders）。"""
    path = Path(vault) / VERSIONS_REL / turn_id / "moves.jsonl"
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as e:
                ledger.log_error(vault, f"moves 壞行跳過 {turn_id}:{lineno}: {type(e).__name__}: {e}")
                continue
            if isinstance(row, dict) and row.get("op") in MOVE_OPS and row.get("kind") in MOVE_KINDS and row.get("from"):
                rows.append(row)
            else:
                ledger.log_error(vault, f"moves 壞行跳過 {turn_id}:{lineno}: 不是搬動")
    return rows


DETECTED_OPS = ("move", "missing", "added", "modified")


def record_detected(vault, turn_id, rows):
    """hook 事後偵測（放寬丙 要做的事 1）：versions/<turn>/detected.jsonl 追加，一行一筆 {"op","kind","from","to","id","by","time"}
    （shared 的來源多 shared: true）。只在有 rows 時才建回合夾；跟 record_move 同一把 manifest 鎖。"""
    rows = [r for r in rows if r.get("op") in DETECTED_OPS]
    if not rows:
        return
    vdir = turn_dir(vault, turn_id)
    with FileLock(vdir / "manifest.jsonl"):
        for row in rows:
            _append_line(vdir / "detected.jsonl", row)


def detected(vault, turn_id):
    """讀 detected.jsonl（照記的順序）；壞行跳過並寫一行 hook-errors.log（同 moves）。"""
    path = Path(vault) / VERSIONS_REL / turn_id / "detected.jsonl"
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as e:
                ledger.log_error(vault, f"detected 壞行跳過 {turn_id}:{lineno}: {type(e).__name__}: {e}")
                continue
            if isinstance(row, dict) and row.get("op") in DETECTED_OPS and (row.get("from") or row.get("to")):
                rows.append(row)
            else:
                ledger.log_error(vault, f"detected 壞行跳過 {turn_id}:{lineno}: 不是偵測紀錄")
    return rows


def zone_of(vault, rel, sources=None):
    """相對路徑落在哪一區，判斷只有這一份（第一輪 L8）；sources 可先載好傳進來，省得每行重讀。"""
    from kb import guard
    from kb._lib import sources as sources_lib
    from kb._lib.paths import normalize
    if sources is None:
        sources = sources_lib.load(vault)
    zone, _alias = guard.classify(normalize(str(Path(vault) / rel), None), normalize(str(vault), None), sources)
    return zone


def modified_synthesis_count(vault, turn_id):
    """這回合 AI 用檔案工具改過的既有綜合卡張數：數回合檔的 ai_synthesis 清單（測試後修正裁定 7），不看 manifest——
    程式先備份（by program）、AI 後改同一張照樣算。回合已經不是目前的照 versions 那份數（card-maker）。"""
    data = turn_data(vault, turn_id) or {}
    return len(data.get("ai_synthesis") or [])


def before_write(vault, target, cwd, zone, session_id, turn_id=None):
    """守門放行後、真的寫之前：記 ai_synthesis（只記張數、不擋，放寬丙 5）、回合備份。回 (ok, code, reason)。
    Z1 既有檔放行時把路徑記進回合檔的 ai_synthesis（去重）；同一張再改不重複算。
    turn_id（子代理的寫入，D-195）給了就算那個回合；那個回合不在了照 session 的目前回合。"""
    t = (turn_data(vault, turn_id) if turn_id else None) or current(vault, session_id)
    if zone == "Z9":  # 登記的來源在知識庫外：備份記 uri（放寬甲）
        try:
            backup_source(vault, target, cwd, t["turn"])
        except (OSError, ValueError) as e:
            ledger.log_error(vault, f"backup_failed {target}: {type(e).__name__}: {e}")
            return False, "backup_failed", BACKUP_FAILED_REASON
        return True, "", ""
    rel = rel_path(vault, target, cwd)
    if zone == "Z1" and (Path(vault) / rel).is_file():

        def count(data):  # 記在鎖裡
            done = list(data.get("ai_synthesis") or [])
            if rel in done:
                return False
            data["ai_synthesis"] = done + [rel]
            return True
        change_turn(vault, t["turn"], count)
    try:
        backup(vault, rel, t["turn"], None)
    except OSError as e:
        ledger.log_error(vault, f"backup_failed {rel}: {type(e).__name__}: {e}")
        return False, "backup_failed", BACKUP_FAILED_REASON
    return True, "", ""
