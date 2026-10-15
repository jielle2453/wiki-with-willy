"""工作日誌（D-082～D-086、D-061、D-065、D-067）：每句話一段，程式自己做的事也一段。
真相是 .system/journal/YYYY-MM.jsonl（一行一個 JSON，每行帶 schema_version），給人看的是 系統/工作日誌/YYYY-MM.md，
每次寫完 jsonl 整月重渲染（最新在上）。一行的月由回合代號的日期決定，跨月的回合留在開口那個月。
回合行分兩次寫：UserPromptSubmit 開口（begin_turn，順便補記上一句的中斷，同月一把鎖），Stop 補完（complete_turn），
同一行 upsert；程式行只追加，代號一律用現在時間組。
讀改寫 jsonl 整段包在 FileLock 裡（hook 3 秒、CLI 30 秒，拿不到丟 TimeoutError 給呼叫端）；渲染不在鎖裡，重讀檔案，
最後一個寫的人渲染的就是最新；渲染失敗只寫 log 不往上丟（md 可用 journal render 重建）。
壞行跳過，只有寫入路徑寫一行 hook-errors.log，重寫時原樣留著（I-046）。
從人或模型來的欄位（prompt、result、AI 的 note）寫入前一律過 mask_secrets（D-086，多遮不改）；程式的 note 不遮。
結果先壓成一行再遮再截（值跟關鍵詞隔空行也遮得到，測試後修正 M2）。
"""
import contextlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

from kb._lib import credentials, ledger, turn
from kb._lib import io as kio
from kb._lib.io import FileLock, write_text_atomic

JOURNAL_REL = Path(".system") / "journal"
RENDER_REL = Path("系統") / "工作日誌"
SCHEMA_VERSION = 1
INTERRUPTED_RESULT = "（回答被中斷，沒有結果）"
NO_TEXT_RESULT = "（沒有文字回答）"
TRUNCATED_TAIL = "…全文見對話"
IN_PROGRESS = "（還沒結束）"
NO_CHANGES = "沒有改動"
NO_CHANGED_FILES = "（無）"
ACTION_TEXT = {"create": "新增", "modify": "修改", "delete": "刪除"}
HEADER_LINES = (
    "這是什麼：這個月你每說一句話，AI 答了什麼、做了什麼、改了哪些檔；程式自己做的事也有一段。最新的在最上面。",
    "怎麼用：直接問我「最近改了什麼」，不用翻這個檔，也不用改它。",
    "由程式產生，改了會被覆蓋。",
)
RECENT_MONTHS = 12

# 地板句（D-084）：區 → [(action, 動詞)], 單位；Z7 不數，其他區數總檔數
_FLOOR_COUNTED = (
    ("Z1", (("modify", "改了"), ("create", "新增"), ("delete", "刪了")), "張綜合卡"),
    ("Z5", (("modify", "改了"), ("create", "新增"), ("delete", "刪了")), "張檔案卡"),
    ("Z3", (("create", "新增"), ("modify", "改了"), ("delete", "刪了")), "份 AI 產出"),
)
_FLOOR_SETTINGS = "改了設定與狀態檔"

_MONTH_OF = re.compile(r"^(\d{4})(\d{2})(\d{2})")
_CONCLUSION = re.compile(r"^(#{1,6})\s*(結論|摘要)")
_HEADING = re.compile(r"^(#{1,6})(\s|$)")


def _now():
    return datetime.now().replace(microsecond=0)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def month_of(turn_id):
    """回合代號前 8 碼的年月；解析不了用今天。"""
    m = _MONTH_OF.match(str(turn_id or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m")
        except ValueError:
            pass
    return date.today().strftime("%Y-%m")


def jsonl_path(vault, month):
    return Path(vault) / JOURNAL_REL / f"{month}.jsonl"


def md_path(vault, month):
    return Path(vault) / RENDER_REL / f"{month}.md"


def _load(vault, month, log=True):
    """回 [(原始行, dict 或 None)]；壞行 dict 是 None，寫回時原樣留著。"""
    p = jsonl_path(vault, month)
    if not p.is_file():
        return []
    items = []
    with open(p, encoding="utf-8") as f:
        for lineno, ln in enumerate(f, 1):
            raw = ln.rstrip("\r\n")
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError as e:
                obj, why = None, f"{type(e).__name__}: {e}"
            else:
                why = None if isinstance(obj, dict) else "不是物件"
                if why:
                    obj = None
            if obj is None and log:
                ledger.log_error(vault, f"工作日誌壞行跳過 {month}:{lineno}: {why}", source="journal")
            items.append((raw, obj))
    return items


def _save(vault, month, items):
    p = jsonl_path(vault, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "".join((raw if obj is None else json.dumps(obj, ensure_ascii=False)) + "\n" for raw, obj in items)
    write_text_atomic(p, text)


def read_lines(vault, month, log=False):
    """這個月的每一行（dict），追加順序；壞行跳過。log 只給寫入路徑用（L3）：recent、render 不記。"""
    return [obj for _raw, obj in _load(vault, month, log=log) if obj is not None]


@contextlib.contextmanager
def _lock(p):
    """FileLock 加一層重試（B-029）：Windows 上鎖檔剛被別人刪掉時 O_EXCL 會丟 PermissionError，
    FileLock 再看已不存在就當成建不起來直接丟；這裡在同一個逾時內重來，到期還是 PermissionError 也算逾時（L1）。"""
    deadline = time.time() + kio.LOCK_TIMEOUT
    while True:
        lock = FileLock(p, timeout=max(0.0, deadline - time.time()))
        try:
            lock.__enter__()
            break
        except PermissionError as e:
            if time.time() >= deadline:
                raise TimeoutError(f"等待檔案鎖逾時：{p}.lock（{type(e).__name__}: {e}）") from e
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _locked_edit(vault, month, edit):
    """鎖住讀改寫：edit(items) 回 True 才寫回。之後在鎖外重渲染，渲染失敗只寫 log（M3）。回 edit 的結果。"""
    p = jsonl_path(vault, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock(p):
        items = _load(vault, month)
        changed = edit(items)
        if changed:
            _save(vault, month, items)
    if changed:
        try:
            render(vault, month)
        except Exception as e:  # noqa: BLE001  md 寫不了不能讓 Stop 跳過 set_done；journal render 可重建
            ledger.log_error(vault, f"工作日誌渲染失敗 {month}: {type(e).__name__}: {e}", source="journal")
    return changed


def _find_turn(items, turn_id):
    for i, (_raw, obj) in enumerate(items):
        if obj is not None and obj.get("kind") == "turn" and obj.get("turn") == turn_id:
            return i
    return None


def _mask(text):
    return credentials.mask_secrets(str(text or ""))


def _mask_result(result, truncated):
    """截斷尾句不能被遮值吃掉：有尾句時只遮前面那段。"""
    result = str(result or "")
    if truncated and result.endswith(TRUNCATED_TAIL):
        return _mask(result[:-len(TRUNCATED_TAIL)]) + TRUNCATED_TAIL
    return _mask(result)


def _open_row(turn_data):
    return {
        "schema_version": SCHEMA_VERSION, "kind": "turn", "turn": turn_data["turn"],
        "session_id": turn_data.get("session_id") or "",
        "time": turn_data.get("started") or _iso(_now()),
        "prompt": _mask(turn_data.get("prompt_head", "")),
        "done": False,
    }


def _upsert_rows(rows):
    """edit 函式：每一行照 turn 代號換掉同回合那行，沒有就追加。"""
    def edit(items):
        for row in rows:
            i = _find_turn(items, row["turn"])
            if i is None:
                items.append((None, row))
            else:
                items[i] = (None, row)
        return True
    return edit


def open_turn(vault, turn_data):
    """回合開口：寫入或換掉這回合那一行，done false。"""
    _locked_edit(vault, month_of(turn_data["turn"]), _upsert_rows([_open_row(turn_data)]))


def _clean_changed(changed):
    return [{"path": c.get("path"), "action": c.get("action")} for c in (changed or [])]


def _complete_row(turn_data, result, note, note_by, changed, interrupted, result_truncated=None):
    if result_truncated is None:
        result_truncated = str(result or "").endswith(TRUNCATED_TAIL)
    row = _open_row(turn_data)
    row.update({
        "done": True,
        "ended": _iso(_now()),
        "result": _mask_result(result, result_truncated),
        "result_truncated": bool(result_truncated),
        "interrupted": bool(interrupted),
        "note": str(note or "") if note_by == "program" else _mask(note),
        "note_by": note_by,
        "changed": _clean_changed(changed),
    })
    return row


def complete_turn(vault, turn_data, result, note, note_by, changed, interrupted, result_truncated=None):
    """回合補完（Stop 或中斷補記）：同一行換成完整的一行，done true。"""
    row = _complete_row(turn_data, result, note, note_by, changed, interrupted, result_truncated)
    _locked_edit(vault, month_of(turn_data["turn"]), _upsert_rows([row]))


def finish_fields(vault, turn_data):
    """Stop 與中斷補記共用（D-084）：(做了, note_by, 改了)。模型叫過 turn note 就用它（ai），沒有才地板句（program）。"""
    tid = turn_data["turn"]
    note = turn_data.get("note") or ""
    changed = _clean_changed(turn.entries(vault, tid))
    if note:
        return note, "ai", changed
    return floor_note(vault, tid), "program", changed


def update_turn(vault, turn_id, **fields):
    """只改給的欄位（kb turn note／redact 用）；那行不存在就不做、回 False。"""
    month = month_of(turn_id)
    if not jsonl_path(vault, month).is_file():
        return False

    def edit(items):
        i = _find_turn(items, turn_id)
        if i is None:
            return False
        row = dict(items[i][1])
        for key, value in fields.items():
            if key == "prompt":
                value = _mask(value)
            elif key == "result":
                value = _mask_result(value, fields.get("result_truncated", row.get("result_truncated")))
            elif key == "note" and fields.get("note_by", row.get("note_by")) != "program":
                value = _mask(value)
            row[key] = value
        items[i] = (None, row)
        return True
    return _locked_edit(vault, month, edit)


def begin_turn(vault, previous, turn_data):
    """UserPromptSubmit 用：previous 是開新回合前這個 session 的回合檔（沒有給 None）。
    previous 還沒答完（done false，被中斷）就用中斷句、finish_fields 補記那行；再寫這一句的開口。
    同月一把鎖（L1）；跨月才兩次，先寫這一句（逾時往上丟），補記那月失敗只寫 log。
    previous 的回合檔會被新回合換掉，不用另標 done；補記沒寫成的舊行留 done false（接受，lint 題清）。"""
    rows = []
    if previous and not previous.get("done", True):
        note, note_by, changed = finish_fields(vault, previous)
        rows.append(_complete_row(previous, INTERRUPTED_RESULT, note, note_by, changed, True, False))
    month = month_of(turn_data["turn"])
    opened = _open_row(turn_data)
    if not rows or month_of(rows[0]["turn"]) == month:
        _locked_edit(vault, month, _upsert_rows(rows + [opened]))
        return
    _locked_edit(vault, month, _upsert_rows([opened]))
    try:
        _locked_edit(vault, month_of(rows[0]["turn"]), _upsert_rows(rows))
    except Exception as e:  # noqa: BLE001  第二把鎖不再往上丟（hook 整支要在 5 秒內）
        ledger.log_error(vault, f"工作日誌補記上個月中斷沒寫成：{type(e).__name__}: {e}", source="journal")


def record_program(vault, note, changed, command):
    """程式段（D-085）：追加一行 kind program。代號一律用現在時間組（尾 0000，L2），月份就是現在；不碰 turns/。"""
    now = _now()
    tid = f"{now:%Y%m%d-%H%M%S}-0000"
    row = {
        "schema_version": SCHEMA_VERSION, "kind": "program", "turn": tid, "time": _iso(now),
        "command": command, "note": str(note or ""), "changed": _clean_changed(changed),
    }

    def edit(items):
        items.append((None, row))
        return True
    _locked_edit(vault, month_of(tid), edit)


def floor_note(vault, turn_id):
    """「做了」的程式地板（D-084）：manifest 按區、按 action 數。"""
    from kb._lib import sources as sources_lib
    rows = turn.entries(vault, turn_id)
    if not rows:
        return NO_CHANGES
    sources = sources_lib.load(vault)
    counts, settings, other = {}, False, 0
    for e in rows:
        zone = turn.zone_of(vault, e["path"], sources)
        if zone == "Z7":
            settings = True
        elif zone in ("Z1", "Z3", "Z5"):
            key = (zone, e.get("action"))
            counts[key] = counts.get(key, 0) + 1
        else:
            other += 1
    parts = []
    for zone, verbs, unit in _FLOOR_COUNTED:
        for action, verb in verbs:
            n = counts.get((zone, action), 0)
            if n:
                parts.append(f"{verb} {n} {unit}")
    if settings:
        parts.append(_FLOOR_SETTINGS)
    if other:
        parts.append(f"改了 {other} 個檔")
    return "、".join(parts) if parts else NO_CHANGES


def cut_result(text, max_chars):
    """結果截斷（D-061）：有「結論」或「摘要」標題就取那一段（要換行才抽得到），沒有取整段；
    再把空白壓成單一空格、再 mask_secrets、再截，超過 max_chars 截掉加尾句（測試後修正 M2，結果一律一行）。"""
    text = str(text or "")
    lines = text.split("\n")
    body = None
    for i, ln in enumerate(lines):
        m = _CONCLUSION.match(ln.rstrip("\r"))
        if not m:
            continue
        level, end = len(m.group(1)), len(lines)
        for j in range(i + 1, len(lines)):
            h = _HEADING.match(lines[j].rstrip("\r"))
            if h and len(h.group(1)) <= level:
                end = j
                break
        body = "\n".join(lines[i + 1:end]).strip()
        break
    if not body:
        body = text
    body = _mask(" ".join(body.split()))
    if not body:
        return NO_TEXT_RESULT, False
    if len(body) > max_chars:
        return body[:max_chars] + TRUNCATED_TAIL, True
    return body, False


def _newest_first(rows):
    """時間新的在前；同一秒的，後寫的在前。"""
    return [r for _i, r in sorted(enumerate(rows), key=lambda x: (str(x[1].get("time", "")), x[0]), reverse=True)]


def _months_back(n):
    y, m = date.today().year, date.today().month
    for _ in range(n):
        yield f"{y:04d}-{m:02d}"
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)


def recent(vault, n):
    """從本月往前翻最多 12 個月，收到 n 行為止，新在前；兩種 kind 都收。"""
    out = []
    for month in _months_back(RECENT_MONTHS):
        out.extend(_newest_first(read_lines(vault, month)))
        if len(out) >= n:
            break
    return out[:n]


def months(vault):
    """有 jsonl 的月，由舊到新。"""
    d = Path(vault) / JOURNAL_REL
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl") if re.fullmatch(r"\d{4}-\d{2}", p.stem))


def _flat(text):
    return " ".join(str(text or "").split())


def _changed_lines(row):
    changed = row.get("changed") or []
    if not changed:
        return [f"- 改了：{NO_CHANGED_FILES}"]
    return ["- 改了："] + [f"  - {ACTION_TEXT.get(c.get('action'), c.get('action'))} {c.get('path')}" for c in changed]


def _block(row):
    when = str(row.get("time", "")).replace("T", " ")
    if row.get("kind") == "program":
        return [f"## {when}　程式 {row.get('command', '')}", f"- 做了：{_flat(row.get('note'))}"] + _changed_lines(row)
    lines = [f"## {when}　回合 {row.get('turn', '')}", f"- 問：{_flat(row.get('prompt'))}"]
    if not row.get("done"):
        return lines + [f"- 結果：{IN_PROGRESS}"]
    return lines + [f"- 結果：{_flat(row.get('result'))}", f"- 做了：{_flat(row.get('note'))}"] + _changed_lines(row)


def render(vault, month):
    """從 jsonl 整檔寫 系統/工作日誌/YYYY-MM.md（D-067）。壞行在寫的人那裡記過 log，這裡不再記。"""
    rows = [obj for _raw, obj in _load(vault, month, log=False) if obj is not None]
    parts = [f"# 工作日誌 {month}", "\n".join(HEADER_LINES)] + ["\n".join(_block(r)) for r in _newest_first(rows)]
    p = md_path(vault, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, "\n\n".join(parts) + "\n")
