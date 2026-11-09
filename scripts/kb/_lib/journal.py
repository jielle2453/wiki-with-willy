"""工作日誌（D-082～D-086、D-061、D-065、D-067）：每句話一段，程式自己做的事也一段。
真相是 .system/journal/YYYY-MM.jsonl（一行一個 JSON，每行帶 schema_version），給人看的是 系統/工作日誌/YYYY-MM.md，
每次寫完 jsonl 整月重渲染（最新在上）。一行的月由回合代號的日期決定，跨月的回合留在開口那個月。
回合行分兩次寫：UserPromptSubmit 開口（begin_turn，順便補記上一句的中斷，同月一把鎖），Stop 補完（complete_turn），
同一行 upsert；程式行只追加，代號一律用現在時間組。
讀改寫 jsonl 整段包在 FileLock 裡（hook 3 秒、CLI 30 秒，拿不到丟 TimeoutError 給呼叫端）；渲染不在鎖裡，重讀檔案，
最後一個寫的人渲染的就是最新；渲染失敗只寫 log 不往上丟（下一次寫入整月重畫）。
壞行跳過，只有寫入路徑寫一行 hook-errors.log，重寫時原樣留著（I-046）。
從人或模型來的欄位（prompt、result、AI 的 note）寫入前一律過 mask_secrets（D-086，多遮不改）；程式的 note 不遮。
結果先壓成一行再遮再截（值跟關鍵詞隔空行也遮得到，測試後修正 M2）。
recent 可篩（answer 第 7 點，D-158）：只要有改動的、問／結果／做了裡有某個詞的（NFKC casefold）、某天之後的，可疊加；
changed_text 把改了的路徑照區分類成白話（綜合卡、檔案卡、AI 產出、系統檔照檔名說），給 kb journal recent 印。
"""
import contextlib
import json
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path

from kb._lib import credentials, ledger, turn
from kb._lib import ledger as ledger_mod
from kb._lib.areas import HOLD_DIR
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
# 不是她在終端機跑的程式段：照這個說法寫（lint 桌面版確認後；versions list 的「（背景健檢）」同一個意思）
AUTO_LABELS = {"lint --background": "每週健檢（自動）"}


OBSIDIAN_LABEL = "你在 Obsidian 按的"  # --via obsidian 的程式段（#22，D-255）


def program_label(command, source=None):
    """程式段的抬頭：Obsidian 外掛按的（source obsidian）寫「你在 Obsidian 按的」，自動跑的照 AUTO_LABELS，其他是「程式 <指令>」。"""
    if source == "obsidian":
        return OBSIDIAN_LABEL
    return AUTO_LABELS.get(str(command or ""), f"程式 {command or ''}".rstrip())

# 地板句（D-084）：區 → [(action, 動詞)], 單位；Z7 不數，其他區數總檔數
_FLOOR_COUNTED = (
    ("Z1", (("modify", "改了"), ("create", "新增"), ("delete", "刪了")), "張綜合卡"),
    ("Z5", (("modify", "改了"), ("create", "新增"), ("delete", "刪了")), "張檔案卡"),
    ("Z4", (("create", "新增"), ("modify", "改了"), ("delete", "刪了")), "份 " + HOLD_DIR),
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
        except Exception as e:  # noqa: BLE001  md 寫不了不能讓 Stop 跳過 set_done；下一次寫入整月重畫
            ledger.log_error(vault, f"工作日誌渲染失敗 {month}: {type(e).__name__}: {e}", source="journal")
    return changed


def _find_turn(items, turn_id):
    for i, (_raw, obj) in enumerate(items):
        if obj is not None and obj.get("kind") == "turn" and obj.get("turn") == turn_id:
            return i
    return None


def _mask(text, vault=None):
    """遮值；給了 vault 就把清冊裡的檔名當不遮的字（D-187 (c)）。"""
    return credentials.mask_secrets(str(text or ""), credentials.known_names(vault))


def _mask_result(result, truncated, vault=None):
    """截斷尾句不能被遮值吃掉：有尾句時只遮前面那段。"""
    result = str(result or "")
    if truncated and result.endswith(TRUNCATED_TAIL):
        return _mask(result[:-len(TRUNCATED_TAIL)], vault) + TRUNCATED_TAIL
    return _mask(result, vault)


def _open_row(turn_data, vault=None):
    """這一行的 turn 是 journal_key：退回自己這句時開的新回合，寫回原回合那一行（第二輪 R2-M2）。"""
    return {
        "schema_version": SCHEMA_VERSION, "kind": "turn", "turn": turn.journal_key(turn_data),
        "session_id": turn_data.get("session_id") or "",
        "time": turn_data.get("started") or _iso(_now()),
        "prompt": _mask(turn_data.get("prompt_head", ""), vault),
        "done": False,
    }


def _upsert_rows(rows):
    """edit 函式：每一行照 turn 代號換掉同回合那行（time 留原來開口的），沒有就追加。"""
    def edit(items):
        for row in rows:
            i = _find_turn(items, row["turn"])
            if i is None:
                items.append((None, row))
            else:
                items[i] = (None, dict(row, time=items[i][1].get("time", row["time"])))
        return True
    return edit


def _clean_changed(changed):
    return [{"path": c.get("path"), "action": c.get("action")} for c in (changed or [])]


def _complete_row(turn_data, result, note, note_by, changed, interrupted, result_truncated=None, vault=None, actions=None):
    if result_truncated is None:
        result_truncated = str(result or "").endswith(TRUNCATED_TAIL)
    row = _open_row(turn_data, vault)
    row.update({
        "done": True,
        "ended": _iso(_now()),
        "result": _mask_result(result, result_truncated, vault),
        "result_truncated": bool(result_truncated),
        "interrupted": bool(interrupted),
        "note": str(note or "") if note_by == "program" else _mask(note, vault),
        "note_by": note_by,
        "changed": _clean_changed(changed),
    })
    if actions:  # 位置類動作與 JSON 條目的單欄改動（放寬丙）：永久留，回合備份清掉後搬動、改名、建夾還能照這裡改回；舊行沒有這欄照讀
        row["actions"] = actions
    return row


def actions_for(vault, turn_data):
    """這一句的 actions 欄（items.actions_of_turn）：journal_key 與目前回合兩個回合夾聯集（跟 finish_fields 一樣）。
    讀不出來回空清單，不擋日誌。"""
    from kb._lib import config, items
    try:
        limit = config.get(vault, "journal_value_max")
        ledger = items.Ledger(vault)
        out = []
        for tid in dict.fromkeys((turn.journal_key(turn_data), turn_data["turn"])):
            out.extend(items.actions_of_turn(vault, tid, limit, ledger))
        return out
    except Exception as e:  # noqa: BLE001
        ledger_mod.log_error(vault, f"工作日誌 actions 沒記到：{type(e).__name__}: {e}", source="journal")
        return []


def complete_turn(vault, turn_data, result, note, note_by, changed, interrupted, result_truncated=None):
    """回合補完（Stop 或中斷補記）：同一行換成完整的一行，done true。"""
    row = _complete_row(turn_data, result, note, note_by, changed, interrupted, result_truncated, vault,
                        actions_for(vault, turn_data))
    _locked_edit(vault, month_of(row["turn"]), _upsert_rows([row]))  # row["turn"] 是 journal_key：跨月的退回寫回原月（R3-L1）


def finish_fields(vault, turn_data):
    """Stop 與中斷補記共用（D-084）：(做了, note_by, 改了)。模型叫過 turn note 就用它（ai），其次是對話裡 kb 寫入指令自帶的那句
    （program_notes，#46），都沒有才地板句（後兩種都是 program）。
    改了取 journal_turn 與目前回合兩份 manifest 的聯集，同路徑取後者的 action，地板句照聯集算（第二輪 R2-M2）。
    整批製卡的派工帳（.system/batches.json）是程式的記帳，不算改了（card-maker）。"""
    merged = {}
    for tid in dict.fromkeys((turn.journal_key(turn_data), turn_data["turn"])):
        for e in turn.entries(vault, tid):
            if e["path"] != NOT_CHANGES:
                merged[e["path"]] = e.get("action")
    changed = [{"path": p, "action": a} for p, a in merged.items()]
    note = turn_data.get("note") or ""
    if note:
        return note, "ai", changed
    said = [str(x) for x in turn_data.get("program_notes") or [] if x]
    if said:  # 對話裡叫的 kb 寫入指令自帶的那句（#46），已遮過
        return "；".join(said[:COMMAND_NOTES_MAX]) + (f"…等 {len(said)} 件" if len(said) > COMMAND_NOTES_MAX else ""), "program", changed
    return _floor(vault, changed), "program", changed


COMMAND_NOTES_MAX = 3  # 「做了」最多接幾句指令自帶的白話，其餘寫「…等 N 件」


NOT_CHANGES = ".system/batches.json"  # batches.BATCHES_POSIX（不 import：batches 會 import turn）


def refresh_changed(vault, turn_id):
    """背景子代理寫完之後補「改了」（card-maker，桌面版 D3）：那個回合在日誌裡那行已經答完（done）才改，重算改了；
    「做了」是程式地板句的也照新的改了重算，AI 給過的不動。結果不碰。那行不在或還沒答完回 False。"""
    data = turn.turn_data(vault, turn_id)
    if data is None:
        return False
    key = turn.journal_key(data)
    note, note_by, changed = finish_fields(vault, data)
    month = month_of(key)
    if not jsonl_path(vault, month).is_file():
        return False

    def edit(items):
        i = _find_turn(items, key)
        if i is None or not items[i][1].get("done"):
            return False
        row = dict(items[i][1])
        row["changed"] = _clean_changed(changed)
        actions = actions_for(vault, data)
        if actions:
            row["actions"] = actions
        if row.get("note_by") != "ai":
            row["note"], row["note_by"] = note, note_by
        items[i] = (None, row)
        return True
    return _locked_edit(vault, month, edit)


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
                value = _mask(value, vault)
            elif key == "result":
                value = _mask_result(value, fields.get("result_truncated", row.get("result_truncated")), vault)
            elif key == "note" and fields.get("note_by", row.get("note_by")) != "program":
                value = _mask(value, vault)
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
        rows.append(_complete_row(previous, INTERRUPTED_RESULT, note, note_by, changed, True, False, vault,
                                  actions_for(vault, previous)))
    opened = _open_row(turn_data, vault)
    month = month_of(opened["turn"])
    if not rows or month_of(rows[0]["turn"]) == month:
        _locked_edit(vault, month, _upsert_rows(rows + [opened]))
        return
    _locked_edit(vault, month, _upsert_rows([opened]))
    try:
        _locked_edit(vault, month_of(rows[0]["turn"]), _upsert_rows(rows))
    except Exception as e:  # noqa: BLE001  第二把鎖不再往上丟（hook 整支要在 5 秒內）
        ledger.log_error(vault, f"工作日誌補記上個月中斷沒寫成：{type(e).__name__}: {e}", source="journal")


def record_program(vault, note, changed, command, actions=None):
    """程式段（D-085）：追加一行 kind program。代號一律用現在時間組（尾 0000，L2），月份就是現在；不碰 turns/。"""
    now = _now()
    tid = f"{now:%Y%m%d-%H%M%S}-0000"
    row = {
        "schema_version": SCHEMA_VERSION, "kind": "program", "turn": tid, "time": _iso(now),
        "command": command, "note": str(note or ""), "changed": _clean_changed(changed),
    }
    if actions:
        row["actions"] = actions
    if turn.VIA:
        row["source"] = turn.VIA  # Obsidian 外掛按的：抬頭寫「你在 Obsidian 按的」，不說是她在終端機跑的

    def edit(items):
        items.append((None, row))
        return True
    _locked_edit(vault, month_of(tid), edit)


def floor_note(vault, turn_id):
    """「做了」的程式地板（D-084）：manifest 按區、按 action 數。"""
    return _floor(vault, turn.entries(vault, turn_id))


def _floor(vault, rows):
    from kb._lib import sources as sources_lib
    if not rows:
        return NO_CHANGES
    sources = sources_lib.load(vault)
    counts, settings, other = {}, False, 0
    for e in rows:
        zone = turn.zone_of(vault, e["path"], sources)
        if zone == "Z7":
            settings = True
        elif zone in ("Z1", "Z4", "Z5"):
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


def cut_result(text, max_chars, vault=None):
    """結果截斷（D-061）：全文壓成一行再遮得 M；M 跟壓過的原文不同（全文有任何一處被遮）就不抽段、直接用 M 截，
    抽段會把關鍵詞和值切開（第二輪 R2-M1 裁定）。沒有被遮才取「結論」或「摘要」標題那一段（要換行才抽得到），
    沒有標題取整段，再壓空白；超過 max_chars 截掉加尾句。結果一律一行。"""
    text = str(text or "")
    flat = " ".join(text.split())
    masked = _mask(flat, vault)
    if masked != flat:
        return _cut(masked, max_chars)
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
    return _cut(" ".join(body.split()), max_chars)


def _cut(body, max_chars):
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


def _fold(text):
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def keep(row, changed=False, find=None, since=None):
    """篩法（D-158，可疊加）：changed 只要有改動的；find 在問、結果、做了裡找（NFKC casefold）；since（YYYY-MM-DD）那天以後的。"""
    if changed and not row.get("changed"):
        return False
    if since and str(row.get("time", ""))[:10] < since:
        return False
    if find:
        word = _fold(find)
        return any(word in _fold(row.get(k)) for k in ("prompt", "result", "note"))
    return True


def recent(vault, n, changed=False, find=None, since=None, skip=None):
    """從本月往前翻最多 12 個月（給了 since 就翻到那個月為止），收到 n 行為止，新在前；兩種 kind 都收，照 keep 篩；
    skip 是不要的那一行的 turn（--find 跳過這一句自己）。回的 result 已過 plain（B-473；篩照原文）。"""
    out = []
    for month in _months_back(RECENT_MONTHS):
        if since and month < since[:7]:
            break
        out.extend(r for r in _newest_first(read_lines(vault, month))
                   if keep(r, changed, find, since) and not (skip and r.get("turn") == skip))
        if len(out) >= n:
            break
    return [dict(r, result=plain(r["result"])) if r.get("result") else r for r in out[:n]]


# 系統檔的白話（D-158）：同一件事的 JSON 與 md 說成同一個詞；不在這裡的照檔名說
SYSTEM_WORDS = {
    ".system/agenda.json": "日程", "系統/日程.md": "日程",
    ".system/pending.json": "待處理", "系統/待處理.md": "待處理",
    ".system/statements.json": "口述",
    ".system/rulings.json": "裁示", "系統/裁示.md": "裁示",
    ".system/findings.json": "推算",
    ".system/entities.json": "實體清單",
    ".system/profile.json": "客戶設定檔", "系統/客戶設定檔.md": "客戶設定檔",
    ".system/inventory.json": "清冊",
    ".system/sources.json": "來源登記",
    "總目錄.md": "總目錄",
    # lint 題的新檔（發行前 T-39：背景健檢每週一段，「改了」不能出英文）
    ".system/lint.json": "健檢紀錄", ".system/set_aside.json": "先放著的清單", ".system/renders.json": "手改比對紀錄",
    ".system/statements-archive.json": "口述封存", ".system/findings-archive.json": "推算封存",
}
SYSTEM_PREFIXES = ((".system/handedits/", "你手改的那份"), ("系統/健檢報告/", "健檢報告"))
NAMES_SHOWN = 3
_TOP_RESERVED = ("檔案索引", "系統", ".system", HOLD_DIR)


def _named(names, unit):
    """「甲、乙、丙等 5 張」：最多列 NAMES_SHOWN 個，多的講份數。"""
    shown = "、".join(names[:NAMES_SHOWN])
    return shown + (f"等 {len(names)} {unit}" if len(names) > NAMES_SHOWN else "")


def changed_text(changed):
    """改了的路徑 → 一行白話：「綜合卡 王宅；檔案卡 1 張；暫存區 1 份；日程；其他 2 個檔」；沒有回空字串。"""
    synth, cards, outputs, words, files, other = [], 0, 0, [], [], 0
    for c in changed or []:
        rel = str(c.get("path") or "")
        parts = rel.split("/")
        word = SYSTEM_WORDS.get(rel) or next((w for head, w in SYSTEM_PREFIXES if rel.startswith(head)), None)
        if word:
            if word not in words:
                words.append(word)
        elif parts[0] == "檔案索引":
            cards += 1
        elif parts[0] == HOLD_DIR:
            outputs += 1
        elif parts[0] in ("系統", ".system"):
            stem = parts[-1].rsplit(".", 1)[0]
            if stem not in files:
                files.append(stem)
        elif (len(parts) == 3 and parts[0] not in _TOP_RESERVED and parts[2].endswith(".md")
              and parts[2][:-3] == parts[1]):
            if parts[1] not in synth:
                synth.append(parts[1])
        else:
            other += 1
    out = []
    if synth:
        out.append(f"綜合卡 {_named(synth, '張')}")
    if cards:
        out.append(f"檔案卡 {cards} 張")
    if outputs:
        out.append(f"{HOLD_DIR} {outputs} 份")
    out += words
    if files:
        out.append(_named(files, "個"))
    if other:
        out.append(f"其他 {other} 個檔")
    return "；".join(out)


def months(vault):
    """有 jsonl 的月，由舊到新。"""
    d = Path(vault) / JOURNAL_REL
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl") if re.fullmatch(r"\d{4}-\d{2}", p.stem))


def _flat(text):
    return " ".join(str(text or "").split())


_PLAIN_FENCE = re.compile(r"```[A-Za-z0-9]*")
_PLAIN_HEADING = re.compile(r"(?<!\S)#{1,6}(?=\s)")


def plain(text):
    """「結果」欄給人讀時去掉 Markdown 記號（B-473）：``` 連同緊接的英數語言名、**、開頭或空白後面接空白的 1～6 個 #；
    其他字元不動，連續空白壓成一個。jsonl 存的原文不改，只用在 render 與 recent。"""
    text = _PLAIN_FENCE.sub(" ", str(text or "")).replace("**", "")
    return _flat(_PLAIN_HEADING.sub(" ", text))


def _changed_lines(row):
    changed = row.get("changed") or []
    if not changed:
        return [f"- 改了：{NO_CHANGED_FILES}"]
    return ["- 改了："] + [f"  - {ACTION_TEXT.get(c.get('action'), c.get('action'))} {c.get('path')}" for c in changed]


ACTION_WORDS = {"move": "把{name}從 {a} 搬到 {b}", "rename": "把{name}改名叫 {b}", "trash": "把{name}丟進資源回收筒",
                "make": "建了資料夾 {name}", "remove": "拿掉了空夾 {name}", "missing": "{name}不見了（不是 willy 動的）",
                "added": "多了 {name}", "modified": "{name}被改過", "set": "把 {name} 的 {field} 從 {old} 改成 {new}"}
DETECTED_SUMMARY = "這一句有 {n} 份檔換了位置"


def _action_line(a):
    from kb._lib import inventory
    kind_uri = a.get("to") if a.get("op") == "added" else a.get("from")
    name = a.get("key") if a.get("op") == "set" else str(kind_uri or "").rstrip("/").rpartition("/")[2]
    where = lambda u: inventory.split_uri(u)[1].rpartition("/")[0].rpartition("/")[2] or inventory.split_uri(u)[0]  # noqa: E731
    text = ACTION_WORDS.get(a.get("op"), "{name}")
    show = lambda v: "（太長沒記）" if v is None else str(v)  # noqa: E731
    line = text.format(name=name, a=where(a["from"]) if a.get("from") else "", b=where(a["to"]) if a.get("to") else "",
                       field=a.get("field"), old=show(a.get("old")), new=show(a.get("new")))
    return line + "（可能是同事動的）" if a.get("colleague") else line


def _action_lines(row, detail_max):
    """actions 欄的白話：認得的逐筆一行；偵測到的（detected：op 是 move、missing、added、modified，不是 kb 記的）超過 detail_max 筆併一行。"""
    acts = row.get("actions") or []
    outside = [a for a in acts if a.get("by") in ("turn", "outside")]
    shown = [a for a in acts if a not in outside] + (outside if len(outside) <= detail_max else [])
    lines = [f"  - {_action_line(a)}" for a in shown]
    if len(outside) > detail_max:
        lines.append("  - " + DETECTED_SUMMARY.format(n=len(outside)))
    return ["- 動了："] + lines if lines else []


def _block(row, detail_max=10):
    when = str(row.get("time", "")).replace("T", " ")
    if row.get("kind") == "program":
        return [f"## {when}　{program_label(row.get('command'), row.get('source'))}", f"- 做了：{_flat(row.get('note'))}"] + _changed_lines(row) + _action_lines(row, detail_max)
    lines = [f"## {when}　回合 {row.get('turn', '')}", f"- 問：{_flat(row.get('prompt'))}"]
    if not row.get("done"):
        return lines + [f"- 結果：{IN_PROGRESS}"]
    return lines + [f"- 結果：{plain(row.get('result'))}", f"- 做了：{_flat(row.get('note'))}"] + _changed_lines(row) + _action_lines(row, detail_max)


def render(vault, month):
    """從 jsonl 整檔寫 系統/工作日誌/YYYY-MM.md（D-067）。壞行在寫的人那裡記過 log，這裡不再記。
    寫之前看她有沒有手改那份月檔（D-223）：改了就只把她那份存到 .system/handedits/（不備進回合：會變成這一句的改了），照樣重寫；
    complete_turn、refresh_changed、update_turn（job_result 換結果也是）都經 _locked_edit 走到這裡。"""
    from kb._lib import renders
    rows = [obj for _raw, obj in _load(vault, month, log=False) if obj is not None]
    parts = [f"# 工作日誌 {month}", "\n".join(HEADER_LINES)] + ["\n".join(_block(r)) for r in _newest_first(rows)]
    p = md_path(vault, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    rel = p.relative_to(vault).as_posix()
    if renders.keep_only(vault, rel) == renders.KEEP_FAILED:
        return  # 她那份沒存成就不蓋掉（測試 T-10），下次寫再試
    text = "\n\n".join(parts) + "\n"
    write_text_atomic(p, text)
    renders.record(vault, rel, text)


PURGED_TEXT = "（已排除）"
_MONTH_NAME = re.compile(r"^\d{4}-\d{2}$")


def scrub(vault, mask, mention=None, keep=()):
    """kb folders purge（manage 第二版第 7 點，D-174）：每個月的工作日誌，changed 裡 mask 改得動的路徑整個換成「（已排除）」、
    note 照 mask 換；changed 有換到的、或 result／note 讓 mention 說有提到的回合行，result 整欄換成「（已排除）」
    （第五輪：回答裡列了那些卡的標題、編號的回合也算）；prompt 讓 mention 說有提到的整欄換（第七輪：客戶會把 AI 的話貼回來），
    keep 裡的回合（排除那一句、清那一句）的 prompt 不動。
    鎖住讀改寫、壞行照舊留著，改到的月重渲染（_locked_edit）。mask(text) 回換過的字串。回改到幾段。"""
    root = Path(vault) / JOURNAL_REL
    if not root.is_dir():
        return 0
    total = 0
    for p in sorted(root.glob("*.jsonl")):
        if not _MONTH_NAME.match(p.stem):
            continue
        hits = []

        def edit(items):
            for i, (_raw, obj) in enumerate(items):
                if obj is None:
                    continue
                row, hit = dict(obj), False
                changed = []
                for c in row.get("changed") or []:
                    if isinstance(c, dict) and isinstance(c.get("path"), str) and mask(c["path"]) != c["path"]:
                        c, hit = dict(c, path=PURGED_TEXT), True
                    changed.append(c)
                if row.get("actions"):  # 放寬丙：actions 裡的路徑與值也照 mask 換
                    acts = [{k: (v if k in ("item", "op", "kind", "by") or not isinstance(v, str) else mask(v))
                             for k, v in a.items()} if isinstance(a, dict) else a for a in row["actions"]]
                    if acts != row["actions"]:
                        row["actions"], hit = acts, True
                if mention is not None and row.get("kind") == "turn" and (
                        mention(str(row.get("result") or "")) or mention(str(row.get("note") or ""))):
                    hit = True
                if hit:
                    row["changed"] = changed
                    if row.get("kind") == "turn" and "result" in row:
                        row["result"] = PURGED_TEXT
                if isinstance(row.get("note"), str):
                    row["note"] = mask(row["note"])
                if (mention is not None and row.get("kind") == "turn" and row.get("turn") not in keep
                        and isinstance(row.get("prompt"), str) and mention(row["prompt"])):
                    row["prompt"] = PURGED_TEXT
                if row != obj:
                    items[i] = (None, row)
                    hits.append(i)
            return bool(hits)
        _locked_edit(vault, p.stem, edit)
        total += len(hits)
    return total
