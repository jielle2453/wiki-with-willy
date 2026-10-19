"""待處理 .system/pending.json（schema 1，D-102、D-062）與渲染視圖 系統/待處理.md（D-067：JSON 為真）。

三種 kind：missing_file（原檔不見，照清冊狀態由 sync_missing 開與自動 done）、unreadable（kb convert 讀不到）、
close（事類實體看起來結案了，等客戶答；entity_ids 帶實體，file_ids 是依據，日程第 3 點、D-127；客戶答了 kb entity progress 自動 done）。
每次寫都在 pending.json 的 FileLock 裡：先 turn.backup pending.json 與 待處理.md 兩個（退回後客戶看到的要一致），
再原子寫，寫完重渲染 md。沒有改動就什麼都不寫（不建檔、不備份）。形狀不對的 pending.json 丟 ValueError。
"""
import copy
import secrets
from datetime import datetime
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

PENDING_POSIX = ".system/pending.json"
MD_POSIX = "系統/待處理.md"
SCHEMA_VERSION = 1
EMPTY = {"schema_version": SCHEMA_VERSION, "items": []}
KINDS = ("missing_file", "unreadable", "close")
SECTIONS = (("missing_file", "原檔不見"), ("unreadable", "讀不到"), ("close", "要確認結案的"))
HEADER = ("# 待處理\n"
          "這是什麼：等你回答或處理的事，程式從 .system/pending.json 整理出來。\n"
          "怎麼用：在對話裡直接回答我，處理完會從這裡消失。\n"
          "由程式產生，改了會被覆蓋。\n")
NOTHING = "目前沒有等你決定的事。"
MISSING_ASK = "是你刪的嗎？是的話我把它的卡一起拿掉；不是的話可以從資源回收筒或雲端找回來。"
# 讀不到的問法，依 reason 開頭挑；都不是就用最後一句
UNREADABLE_ASKS = (("還沒裝 PDF 元件", "請服務人員在這台電腦跑 pip install pypdfium2。"),  # 裁定 36
                   ("有密碼", "另存一份沒密碼的，或跟我說先跳過。"),
                   ("舊格式", "用 Word／Excel 另存成新格式。"),
                   ("只在雲端", "在檔案上按右鍵選保留在這台電腦上。"))
UNREADABLE_OTHER = "跟我說先跳過，或換一份檔。"
FOUND_AGAIN = "找回來了"
DISMISSED_HOW = "客戶說不用管"


def _now_default():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


_now = _now_default  # 測試換掉它排新舊


def _path(vault):
    return Path(vault) / PENDING_POSIX


def load(vault):
    data = read_json(_path(vault))
    if data is None:
        return copy.deepcopy(EMPTY)
    if not isinstance(data, dict):
        raise ValueError("待處理不是物件")
    items = data.get("items")
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ValueError("待處理沒有 items 清單")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"待處理版本 {version!r} 比這版 plugin 新")
    return {"schema_version": SCHEMA_VERSION, "items": items}


def file_label(uri):
    """file://客戶案件/大安陳/會議紀錄_1.docx → 「會議紀錄_1.docx」（客戶案件/大安陳）。"""
    body = str(uri)[len("file://"):] if str(uri).startswith("file://") else str(uri)
    folder, _, name = body.rpartition("/")
    return f"「{name}」（{folder}）" if folder else f"「{name}」"


def _find_open(items, file_id, kind, entity_ids=None):
    """同一份檔同一種 kind 的 open 條目；close 還要同一個實體（同一份依據可以問兩個案子）。"""
    return next((i for i in items if i.get("status") == "open" and i.get("kind") == kind
                 and (i.get("file_ids") or [None])[0] == file_id
                 and (kind != "close" or (i.get("entity_ids") or []) == list(entity_ids or []))), None)


def open_for(vault, file_id, kind):
    return _find_open(load(vault)["items"], file_id, kind)


def _edit(vault, turn_id, change):
    """鎖住讀改寫：change(items) 回真才備份、寫、渲染。回 change 的結果。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        result = change(data["items"])
        if result:
            from kb._lib import turn
            turn.backup(vault, PENDING_POSIX, turn_id)
            turn.backup(vault, MD_POSIX, turn_id)
            write_json_atomic(p, data)
            render(vault, data)
    return result


def _new_item(items, kind, file_ids, text, reason, turn_id, entity_ids=None):
    taken = {i.get("id") for i in items}
    while True:
        pid = "p-" + secrets.token_hex(4)
        if pid not in taken:
            break
    return {"id": pid, "kind": kind, "file_ids": list(file_ids), "entity_ids": list(entity_ids or []), "text": text, "reason": reason,
            "created": _now(), "created_turn": turn_id, "status": "open", "resolved": None, "how": None}


def add(vault, kind, file_ids, text, reason, turn_id, entity_ids=None):
    """同 file_ids[0]＋kind 已有 open 的不重複，回既有 id（close 另比實體）。"""
    found = {}

    def change(items):
        old = _find_open(items, file_ids[0], kind, entity_ids)
        if old is not None:
            found["id"] = old["id"]
            return False
        item = _new_item(items, kind, file_ids, text, reason, turn_id, entity_ids)
        items.append(item)
        found["id"] = item["id"]
        return True
    _edit(vault, turn_id, change)
    return found["id"]


def _close(item, status, how):
    item["status"], item["resolved"], item["how"] = status, _now(), how


def _close_by_id(vault, pid, status, how, turn_id):
    """找不到這個 id、或它已經不是 open，丟 KeyError。"""
    def change(items):
        item = next((i for i in items if i.get("id") == pid), None)
        if item is None:
            raise KeyError(f"沒有這條待處理：{pid}")
        if item.get("status") != "open":
            raise KeyError(f"這條已經處理過了：{pid}")
        _close(item, status, how)
        return item
    return _edit(vault, turn_id, change)


def resolve(vault, pid, how, turn_id):
    return _close_by_id(vault, pid, "done", how, turn_id)


def dismiss(vault, pid, turn_id):
    return _close_by_id(vault, pid, "dismissed", DISMISSED_HOW, turn_id)


def auto_done(vault, file_id, kind, how, turn_id, statuses=("open",)):
    """這份檔有 statuses 狀態的 kind 條目就 done；回有沒有關到。
    檔案換了時 open 與 dismissed 都關（測試後修正裁定 31），呼叫端傳 statuses=("open", "dismissed")。"""
    if not _path(vault).exists():
        return False

    def change(items):
        hits = [i for i in items if i.get("status") in statuses and i.get("kind") == kind
                and (i.get("file_ids") or [None])[0] == file_id]
        for item in hits:
            _close(item, "done", how)
        return bool(hits)
    return _edit(vault, turn_id, change)


def auto_done_entity(vault, entity_id, kind, how, turn_id):
    """這個實體有 open 的 kind 條目就 done；回有沒有關到（客戶答了結案問句，kb entity progress 呼叫）。"""
    if not _path(vault).exists():
        return False

    def change(items):
        hits = [i for i in items if i.get("status") == "open" and i.get("kind") == kind
                and entity_id in (i.get("entity_ids") or [])]
        for item in hits:
            _close(item, "done", how)
        return bool(hits)
    return _edit(vault, turn_id, change)


def answered(items, entry):
    """這次不見之後客戶答過了嗎：有任何 missing_file 條目（open、done、dismissed 都算）是這份檔、
    且建立時間不早於 missing_since（測試後修正裁定 31）。"""
    since = str(entry.get("missing_since") or "")
    return any(i.get("kind") == "missing_file" and (i.get("file_ids") or [None])[0] == entry.get("id")
               and str(i.get("created") or "") >= since for i in items)


def sync_missing(vault, entries, turn_id):
    """照清冊狀態（實作「釐清」那條）：status missing 且這次不見後還沒有任何 missing_file 條目的開一條
    （客戶答過「先留著」「不用管」的不重開，裁定 31）；有 open missing_file 但條目已回 active 的自動 done「找回來了」。
    回有沒有改到。"""
    missing = [e for e in entries if e.get("status") == "missing" and e.get("locations")]
    active = {e.get("id") for e in entries if e.get("status") == "active"}
    if not missing and not _path(vault).exists():
        return False

    def change(items):
        changed = False
        for e in missing:
            if not answered(items, e):
                text = f"{file_label(e['locations'][0].get('uri', ''))}不見了"
                items.append(_new_item(items, "missing_file", [e["id"]], text, None, turn_id))
                changed = True
        for item in items:
            if (item.get("status") == "open" and item.get("kind") == "missing_file"
                    and (item.get("file_ids") or [None])[0] in active):
                _close(item, "done", FOUND_AGAIN)
                changed = True
        return changed
    return _edit(vault, turn_id, change)


def ask_of(item):
    if item.get("kind") == "missing_file":
        return MISSING_ASK
    if item.get("kind") == "close":
        return ""
    reason = item.get("reason") or ""
    return next((ask for head, ask in UNREADABLE_ASKS if reason.startswith(head)), UNREADABLE_OTHER)


def line_of(item):
    """「<text>。<問法>」；讀不到有 reason 時 text 後面接「：<reason>」。"""
    text = item.get("text", "")
    if item.get("kind") == "close":
        return text + (f"（{item['reason']}）" if item.get("reason") else "")
    if item.get("kind") == "unreadable" and item.get("reason"):
        text += f"：{item['reason']}"
    return f"{text}。{ask_of(item)}"


def render(vault, data=None):
    """寫 系統/待處理.md：只列 open，依 kind 分段、新的在上；沒有 open 時一行 NOTHING。"""
    data = data if data is not None else load(vault)
    opened = [i for i in data["items"] if i.get("status") == "open"]
    parts = [HEADER]
    for kind, title in SECTIONS:
        rows = sorted((i for i in opened if i.get("kind") == kind), key=lambda i: str(i.get("created", "")),
                      reverse=True)
        if rows:
            parts.append(f"\n## {title}（{len(rows)}）\n")
            parts.extend(f"- {str(i.get('created', ''))[:10]} {line_of(i)}\n" for i in rows)
    if not opened:
        parts.append(f"\n{NOTHING}\n")
    target = Path(vault) / MD_POSIX
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, "".join(parts))
