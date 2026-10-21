"""回合：每句話一個回合（D-074）。目前回合按 session 存 .system/turns/<sid>.json，另有 current.json 給 CLI。
回合備份住 .system/versions/<turn>/：turn.json、manifest.jsonl（一行一檔）、files/<rel>（modify 與 delete 的舊版）、
folders.jsonl（kb 程式改的資料夾，一行一個動作，D-142）。
大改動攔截（D-075）：同回合用檔案工具修改既有綜合卡達 big_change_synthesis_max 張就擋，approve 後解除。
相對路徑保留原始大小寫（I-027：paths.normalize 會小寫，備份與還原不能用它）。
"""
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from kb._lib import config, credentials, ledger
from kb._lib.io import FileLock, read_json, write_json_atomic

TURNS_REL = Path(".system") / "turns"
VERSIONS_REL = Path(".system") / "versions"
CURRENT_NAME = "current.json"
BIG_CHANGE_REASON = ("這一題已經改了 {n} 張綜合卡，先停下來告訴客戶還有幾張、為什麼；"
                     "客戶同意後用知識庫腳本 turn approve 解除再繼續。")
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
    return credentials.mask_secrets(" ".join(text.split()))[:config.get(vault, "prompt_head_max")]


def journal_key(data):
    """這個回合在工作日誌裡那一行的 key：退回自己這句時開的新回合記著原回合（journal_turn），沒有就是自己。"""
    return data.get("journal_turn") or data["turn"]


def start(vault, session_id, prompt, inherit=None):
    """開新回合：寫 turns/<sid>.json（有 sid 才寫）與 current.json。回回合 dict。
    inherit（工作日誌第二輪 R2-M2）：對話裡退回自己這句時，新回合接著原回合，繼承它的問、note、approved，
    並記 journal_turn（原回合的 journal_turn，沒有就原回合），Stop 與補記才收原回合那一行。"""
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
        "approved": bool(inherit.get("approved")) if inherit else False,  # 同一句話的同意不因退回失效（D-075）
        "note": inherit.get("note", "") if inherit else "",  # 模型叫 kb turn note 給的「做了」（D-084）
        "done": False,  # Stop 或中斷補記後為 true（D-083）
    }
    if inherit:
        data["journal_turn"] = journal_key(inherit)
    (Path(vault) / TURNS_REL).mkdir(parents=True, exist_ok=True)
    if session_id:
        write_json_atomic(_session_file(vault, session_id), data)
    write_json_atomic(_current_file(vault), data)
    return data


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


def current(vault, session_id=None):
    """有 sid 讀 turns/<sid>.json；沒有或檔不存在讀 current.json；都沒有就開一個（hook 沒跑到也要有回合）。"""
    if session_id:
        data = _read_turn(_session_file(vault, session_id))
        if data:
            return data
    data = _read_turn(_current_file(vault))
    if data:
        return data
    return start(vault, session_id, "")


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


def approve(vault, session_id=None):
    data = current(vault, session_id)
    data["approved"] = True
    _write_turn(vault, data)
    return data


def _set(vault, session_id, **fields):
    """改 turns/<sid>.json（沒有 sid 改 current.json）；current.json 是同回合時一起改。回合不存在回 None，不開新的。"""
    path = _session_file(vault, session_id) if session_id else _current_file(vault)
    data = _read_turn(path)
    if data is None:
        return None
    data.update(fields)
    _write_turn(vault, data)
    return data


def set_note(vault, session_id, text):
    return _set(vault, session_id, note=text)


def set_done(vault, session_id):
    return _set(vault, session_id, done=True)


def set_prompt_head(vault, session_id, text):
    return _set(vault, session_id, prompt_head=text)


def mark(vault, key):
    """目前回合檔設一個旗標（回合狀態，不是知識；answer 第 9 點）：只在對話裡的回合（current.json 有 session_id 且還沒答完），
    終端機跑的不記。已經是 true 不寫。回這個回合現在有沒有這個旗標。"""
    data = _read_turn(_current_file(vault))
    if not data or not data.get("session_id") or data.get("done"):
        return False
    if data.get(key) is True:
        return True
    sess = _read_turn(_session_file(vault, data["session_id"]))
    if not sess or sess.get("turn") != data["turn"]:  # session 檔已經是別的回合：不記到錯的那句
        return False
    return _set(vault, data["session_id"], **{key: True}) is not None


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


def backup(vault, rel, turn_id, action=None, by=None):
    """寫入前把舊版複製到 versions/<turn_id>/files/<rel>，manifest 追加一行。同回合同路徑只做第一次。
    action 預設存在 modify、不存在 create；呼叫端刪檔前傳 delete。
    by 只收 "program"：程式自己改的（綜合卡檔頭快取重渲染、改名、合併），寫進那行，大改動門檻不數（實體與綜合卡第 9 點）。
    查重、複製、記帳在同一把鎖裡（第一輪 M1／L3）；複製失敗或鎖逾時丟 OSError（TimeoutError 也是）。
    """
    if by not in (None, "program"):
        raise ValueError(f"backup 的 by 只收 program：{by!r}")
    target = Path(vault) / rel
    vdir = turn_dir(vault, turn_id)
    manifest = vdir / "manifest.jsonl"
    with FileLock(manifest):
        if any(e.get("path") == rel for e in entries(vault, turn_id)):
            return
        exists = target.is_file()
        action = action or ("modify" if exists else "create")
        if exists:
            dst = vdir / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dst)
        row = {"path": rel, "action": action, "time": _now().strftime("%Y-%m-%dT%H:%M:%S")}
        if by:
            row["by"] = by
        lead = ""
        if manifest.is_file() and manifest.stat().st_size > 0:
            with open(manifest, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    lead = "\n"  # 檔尾是半截行：先補換行，新的一行不被吃掉（第二輪 R2-L5）
        with open(manifest, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + json.dumps(row, ensure_ascii=False) + "\n")


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
    程式先備份（by program）、AI 後改同一張照樣算。"""
    data = _find_turn(vault, turn_id) or {}
    return len(data.get("ai_synthesis") or [])


def before_write(vault, target, cwd, zone, session_id):
    """守門放行後、真的寫之前：大改動門檻（數 ai_synthesis）、回合備份。回 (ok, code, reason)。
    Z1 既有檔放行時把路徑記進回合檔的 ai_synthesis（去重）；同一張再改不重複算。"""
    t = current(vault, session_id)
    rel = rel_path(vault, target, cwd)
    if zone == "Z1" and (Path(vault) / rel).is_file():
        done = list(t.get("ai_synthesis") or [])
        if rel not in done:
            n = len(done)
            if not t.get("approved") and n >= config.get(vault, "big_change_synthesis_max"):
                return False, "big_change", BIG_CHANGE_REASON.format(n=n)
            t["ai_synthesis"] = done + [rel]
            _write_turn(vault, t)
    try:
        backup(vault, rel, t["turn"], None)
    except OSError as e:
        ledger.log_error(vault, f"backup_failed {rel}: {type(e).__name__}: {e}")
        return False, "backup_failed", BACKUP_FAILED_REASON
    return True, "", ""
