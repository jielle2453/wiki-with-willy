"""待處理 .system/pending.json（schema 2，D-102、D-062、D-226）與渲染視圖 系統/待處理.md（D-067：JSON 為真）。

kind：missing_file（原檔不見，照清冊狀態由 sync_missing 開與自動 done）、unreadable（kb convert 讀不到）、
close（事類實體看起來結案了，等客戶答；entity_ids 帶實體，file_ids 是依據，日程第 3 點、D-127；客戶答了 kb entity progress 自動 done）、
conflict（兩份原檔寫法不同，answer 核對時發現；file_ids 是那兩張卡、titles 是記的時候的卡標題，同一對不論順序只開一條，
answer 第 6 點、D-155；不進 asks，客戶說哪個對之後照 note 記完再 resolve）、
script_touched（kb office run 的腳本動到了登記的來源、實體夾、AI 產出或收件匣裡的檔，密碼與守門 D-188；file_ids 是清冊編號，
不在清冊的放 uri）、lint（健檢要她決定的，key 是合成的鍵、data 是項目，同 key 只開一條，lint 第 2 點）、
report（這個月的健檢報告出來了，data.month；新的開了舊的自動 done，最多一件，D-227）、
handedit（她手改了程式產生的 md，data 是 {file, saved, diff}，D-223）、
tag_edit（她在 Obsidian 手改綜合卡的標籤行、詞不在字典或單選寫了兩個，D-505 決策 6；entity_ids 是那個實體、data 是
{"fields": [{"field","card","tags"}], "results"}，一個實體只開一條；之後卡上那一行跟正本一樣了就由健檢自動 done）。
schema 2（lint 題，D-226）：條目多 set_aside（null 或 {"at","by": customer|lint,"fps"}）與 returned（最後回到前面的日期）；
先放著是 open 加 set_aside：不在回答末尾問、待處理.md 移到最底下「先放著的」一段；相關的原檔換版、卡重做、實體有新口述或裁示、
或日期快到了（data.dates 離到期剩 set_aside_wake_days 天）就回到前面（wake、due_back）。schema 1 在 load 時補欄位，
kb upgrade --finish 經 _lib/migrate.py 的遷移表寫回。
每次寫都在 pending.json 的 FileLock 裡：先 turn.backup pending.json 與 待處理.md 兩個（退回後客戶看到的要一致），
再原子寫，寫完重渲染 md。改之前先比對 待處理.md 有沒有被手改（_lib/renders，D-223）：有就把她那份存起來、開一條 handedit。
沒有改動就什麼都不寫（不建檔、不備份）。形狀不對的 pending.json 丟 ValueError。
"""
import copy
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from kb._lib import archive, hand, ledger, renders
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

PENDING_POSIX = ".system/pending.json"
MD_POSIX = "系統/待處理.md"
SCHEMA_VERSION = 2
EMPTY = {"schema_version": SCHEMA_VERSION, "items": []}
KINDS = ("missing_file", "unreadable", "close", "conflict", "script_touched", "lint", "report", "handedit", "tag_edit")
SECTIONS = (("missing_file", "原檔不見"), ("unreadable", "讀不到"), ("close", "要確認結案的"), ("conflict", "兩份寫法不同的"),
            ("script_touched", "程式動到的檔"), ("lint", "健檢查到的"), ("handedit", "你手改過的"), ("tag_edit", "卡上改了標籤的"),
            ("report", "健檢報告"))
ASKS_TITLE = "整理時要問你的"
ASIDE_TITLE = "先放著的"
NOT_FOUND = "沒有這件待處理"
REPORT_TEXT = "{month} 的健檢報告出來了，說「看健檢報告」我講重點"
REPORT_REPLACED = "新的健檢報告出來了，這件換新"
LINT_GONE_HOW = "健檢確認已不成立"  # 程式關的：條件又出現時照樣再開（她答過的才不重開，測試 T-1、T-2）
HANDEDIT_TEXT = "你改過「{name}」，我留下了你那份，照你改的處理"
SCRIPT_TOUCHED_ASK = "我沒有要動它；說「退回上一版」看救不救得回，救不回就從資源回收筒或雲端找。"
HEADER = ("# 待處理\n"
          "這是什麼：等你回答或處理的事，程式從 .system/pending.json 整理出來。\n"
          "怎麼用：在對話裡直接回答我，處理完會從這裡消失。\n"
          "由程式產生；在這裡打勾或刪掉一行，我下次會照著做。\n")  # D-223：照她打勾、刪行做
NOTHING = "目前沒有等你決定的事。"
MISSING_ASK = "是你刪的嗎？是的話我把它的卡一起拿掉；不是的話可以從資源回收筒或雲端找回來。"
MISSING_ASK_SHARED = "在共用的夾裡，可能是同事移走或刪的；確定不要了我把它的卡一起拿掉，不是的話可以從資源回收筒或雲端找回來。"  # 多人（D-334，#45）
# 讀不到的問法，依 reason 開頭挑；都不是就用最後一句
UNREADABLE_ASKS = (("還沒裝 PDF 元件", "請服務人員在這台電腦跑 pip install pypdfium2。"),  # 裁定 36
                   ("有密碼", "PDF 給我密碼就能打開（要我記住也可以）；Word、Excel 請另存一份沒密碼的，或跟我說先跳過。"),
                   ("舊格式", "用 Word／Excel 另存成新格式。"),
                   ("只在雲端", "在檔案上按右鍵選保留在這台電腦上。"))
UNREADABLE_OTHER = "跟我說先跳過，或換一份檔。"
FOUND_AGAIN = "找回來了"
DISMISSED_HOW = "客戶說不用管"
HAND_RESOLVE_HOW = "在待處理.md 打勾"  # 她直接在頁面上打勾（D-263）


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
    for item in items:  # schema 1 → 2 在記憶體補欄位（D-226）
        item.setdefault("set_aside", None)
        item.setdefault("returned", None)
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


def _open_pair(items, file_ids):
    """同一對卡（不論順序）的 open conflict 條目。"""
    pair = sorted(file_ids)
    return next((i for i in items if i.get("status") == "open" and i.get("kind") == "conflict"
                 and sorted(i.get("file_ids") or []) == pair), None)


def open_for(vault, file_id, kind):
    return _find_open(load(vault)["items"], file_id, kind)


def _edit(vault, turn_id, change, hand_out=None):
    """鎖住讀改寫：change(items) 回真才備份、寫、渲染。回 change 的結果。
    改之前先看 待處理.md 有沒有被她手改（D-223、D-263）：認得出的照做、認不出的留在頁面上，這次一定重寫；hand_out 是清單時把報告放進去。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        handed = _hand_edit(vault, data, turn_id)
        if hand_out is not None:
            hand_out.append(handed)
        result = change(data["items"])
        if result or handed:
            from kb._lib import turn
            turn.backup(vault, PENDING_POSIX, turn_id)
            turn.backup(vault, MD_POSIX, turn_id)
            write_json_atomic(p, data)
            if handed != renders.KEEP_FAILED:  # 她那份沒存成就不蓋掉（測試 T-10），下次寫再試
                render(vault, data, turn_id=turn_id)
    return result


def _apply_hand(data, got, turn_id):
    """她在待處理.md 打勾 = 處理好了、刪掉一行 = 不用管；改日期在這頁沒有意思，記成認不出的。回 (句子, 改記成認不出的)。"""
    by_id = {i.get("id"): i for i in data["items"]}
    applied, extra = [], []
    for c in got["checked"]:
        it = by_id.get(c["id"])
        if it is not None and it.get("status") == "open":
            _close(it, "done", HAND_RESOLVE_HOW)
            applied.append(f"處理好了『{line_of(it)[:30]}』")
    for c in got["removed"]:
        it = by_id.get(c["id"])
        if it is not None and it.get("status") == "open":
            _close(it, "dismissed", DISMISSED_HOW)
            applied.append(f"不用管『{line_of(it)[:30]}』")
    for c in got["changed_date"]:
        extra.append({"kind": "changed", "text": f"{c['line'][2:]}（你改的日期：{c['to']}）", "orig": c["line"][2:]})
    return applied, extra


def _hand_edit(vault, data, turn_id):
    """待處理.md 跟程式上次寫的不一樣：認得出的照做（打勾、刪行），認不出的（新打的字、改了字）留在頁面最底下「你加的」（D-263）。
    整理時要問你的那段不算（每次都會變）。她那份存不了回 KEEP_FAILED（這次不重寫）。回報告、None 或 KEEP_FAILED。"""
    try:
        hers = renders.check(vault, MD_POSIX)
        if hers is None:
            return None
        known = renders.base(vault, MD_POSIX)  # 上一次真的寫進去的那份（T-29）；沒記過才用現在的資料重算
        base_text, lines = known if known else (render_text(vault, data, asks=[]), item_lines(data, vault))
        return hand.guarded(
            vault, MD_POSIX, hers, data,
            lambda st: hand.consume(vault, MD_POSIX, hers, base_text, lines, {i.get("id") for i in data["items"]},
                                    lambda got: _apply_hand(data, got, turn_id), ignore_sections=(ASKS_TITLE,), state=st),
            lambda saved, diff: data["items"].append(_handedit_item(data["items"], MD_POSIX, saved, diff, turn_id)))  # 已在待處理的鎖裡，直接加
    except Exception as e:  # noqa: BLE001  手改偵測不能擋住寫待處理（guarded 以外的出錯，例如讀頁面）
        ledger.log_error(vault, f"待處理.md 手改比對失敗：{type(e).__name__}: {e}", source="renders")
        return None


def apply_hand(vault, turn_id):
    """kb renders apply 系統/待處理.md：照她手改的做。回報告 dict、None 或 hand.KEEP_FAILED。"""
    out = []
    _edit(vault, turn_id, lambda items: False, hand_out=out)
    return out[0] if out else None


def _new_item(items, kind, file_ids, text, reason, turn_id, entity_ids=None):
    taken = {i.get("id") for i in items}
    while True:
        pid = "p-" + secrets.token_hex(4)
        if pid not in taken:
            break
    return {"id": pid, "kind": kind, "file_ids": list(file_ids), "entity_ids": list(entity_ids or []), "text": text, "reason": reason,
            "created": _now(), "created_turn": turn_id, "status": "open", "resolved": None, "how": None,
            "set_aside": None, "returned": None}


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


def add_tag_edit(vault, entity_id, text, data, turn_id):
    """記一條「卡上改了標籤、不合字典」（D-505 決策 6）：同一個實體已有 open 的就換成這次的 text 與 data。回 (編號, 新建了嗎)。"""
    found = {}

    def change(items):
        old = next((i for i in items if i.get("status") == "open" and i.get("kind") == "tag_edit"
                    and (i.get("entity_ids") or []) == [entity_id]), None)
        if old is not None:
            found["id"], found["new"] = old["id"], False
            if old.get("text") == text and old.get("data") == data:
                return False
            old["text"], old["data"] = text, dict(data)
            return True
        item = _new_item(items, "tag_edit", [], text, None, turn_id, [entity_id])
        item["data"] = dict(data)
        items.append(item)
        found["id"], found["new"] = item["id"], True
        return True
    _edit(vault, turn_id, change)
    return found["id"], found["new"]


def add_conflict(vault, file_ids, text, turn_id, entity_ids=None, titles=None):
    """記一條「兩份寫法不同」：同一對卡已有 open 的不重複。回 (條目, 新建了嗎)。"""
    found = {}

    def change(items):
        old = _open_pair(items, file_ids)
        if old is not None:
            found["item"] = dict(old)
            return False
        item = _new_item(items, "conflict", file_ids, text, None, turn_id, entity_ids)
        item["titles"] = list(titles or [])
        items.append(item)
        found["item"] = dict(item)
        return True
    created = _edit(vault, turn_id, change)
    return found["item"], bool(created)


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


def dismiss_entity(vault, entity_id, kind, how, turn_id):
    """這個實體 open 的 kind 條目一律 dismissed（實體被移除或合併掉，問句沒有對象了）；回關掉幾條。"""
    if not _path(vault).exists():
        return 0

    def change(items):
        hits = [i for i in items if i.get("status") == "open" and i.get("kind") == kind
                and entity_id in (i.get("entity_ids") or [])]
        for item in hits:
            _close(item, "dismissed", how)
        return len(hits)
    return _edit(vault, turn_id, change)


def close_ids(vault, ids, how, turn_id):
    """這些 open 條目一律 done（健檢確認已不成立，lint 第 2 點）；回關掉幾條。"""
    ids = set(ids)
    if not ids or not _path(vault).exists():
        return 0

    def change(items):
        hits = [i for i in items if i.get("status") == "open" and i.get("id") in ids]
        for item in hits:
            _close(item, "done", how)
        return len(hits)
    return _edit(vault, turn_id, change) or 0


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

    shared = _shared_aliases(vault) if missing else set()

    def change(items):
        changed = False
        for e in missing:
            if not answered(items, e):
                uri = e["locations"][0].get("uri", "")
                text = f"{file_label(uri)}不見了"
                items.append(_new_item(items, "missing_file", [e["id"]], text, None, turn_id))
                if _alias_of(uri) in shared:  # 多人共用夾：可能是同事動的（D-334，#45）
                    items[-1]["colleague"] = True
                changed = True
        for item in items:
            if (item.get("status") == "open" and item.get("kind") == "missing_file"
                    and (item.get("file_ids") or [None])[0] in active):
                _close(item, "done", FOUND_AGAIN)
                changed = True
        return changed
    return _edit(vault, turn_id, change)


def _shared_aliases(vault):
    """多人時登記成共用（shared）的來源別名（casefold）；單人或讀不到回空集合（同 hooks/_checks._colleague_uri）。"""
    try:
        from kb._lib import people, sources as sources_lib
        if not people.multi(vault):
            return set()
        return {s["alias"].casefold() for s in sources_lib.load(vault).entries if s.get("shared")}
    except Exception:  # noqa: BLE001  問法而已，出錯照舊問
        return set()


def _alias_of(uri):
    return str(uri)[len("file://"):].partition("/")[0].casefold() if str(uri).startswith("file://") else ""


def ask_of(item):
    if item.get("kind") == "missing_file":
        return MISSING_ASK_SHARED if item.get("colleague") else MISSING_ASK
    if item.get("kind") in ("close", "conflict", "lint", "report", "handedit", "tag_edit"):
        return ""
    if item.get("kind") == "script_touched":
        return SCRIPT_TOUCHED_ASK
    reason = item.get("reason") or ""
    return next((ask for head, ask in UNREADABLE_ASKS if reason.startswith(head)), UNREADABLE_OTHER)


def line_of(item):
    """「<text>。<問法>」；讀不到有 reason 時 text 後面接「：<reason>」；健檢、報告、手改只有 text。"""
    text = item.get("text", "")
    if item.get("kind") == "close":
        return text + (f"（{item['reason']}）" if item.get("reason") else "")
    if item.get("kind") == "conflict":
        titles = "".join(f"「{t}」" for t in item.get("titles") or [])
        return f"{text}（{titles + '，' if titles else ''}{str(item.get('created', ''))[:10]}）"
    if item.get("kind") in ("lint", "report", "handedit", "tag_edit"):
        return text
    if item.get("kind") == "unreadable" and item.get("reason"):
        text += f"：{item['reason']}"
    return f"{text}。{ask_of(item)}"


def _row(item):
    return f"- {line_of(item)}" if item.get("kind") == "conflict" else f"- {str(item.get('created', ''))[:10]} {line_of(item)}"


def _newest_first(rows):
    return sorted(rows, key=lambda i: str(i.get("created", "")), reverse=True)


def item_lines(data, vault=None):
    """[(待處理.md 裡那一行, 編號)]：open 的每一條（先放著的也是），手改比對用；給了 vault 就跟 render_text 一樣不列跟封存有關的。"""
    items = archive.visible_items(vault, data["items"]) if vault is not None else data["items"]
    return [(_row(i), i["id"]) for i in items if i.get("status") == "open" and i.get("id")]


def render_text(vault, data, asks=None):
    """待處理.md 的內容：只列 open，依 kind 分段、新的在上；再來「整理時要問你的」（asks：不住在 pending.json 的問題，
    沒給就用上一次健檢或 kb asks --all 算好、記在 renders.json 的那份——每次寫待處理都重算整理問題太慢）；最底下「先放著的」。
    什麼都沒有時一行 NOTHING。兩份寫法不同的日期寫在句尾。"""
    opened = [i for i in archive.visible_items(vault, data["items"]) if i.get("status") == "open"]  # 跟封存有關的不列（放寬丙 12）
    front = [i for i in opened if not i.get("set_aside")]
    aside = [i for i in opened if i.get("set_aside")]
    asks = renders.stored_asks(vault) if asks is None else list(asks)
    parts = [HEADER]
    for kind, title in SECTIONS:
        rows = _newest_first(i for i in front if i.get("kind") == kind)
        if rows:
            parts.append(f"\n## {title}（{len(rows)}）\n")
            parts.extend(_row(i) + "\n" for i in rows)
    if asks:
        parts.append(f"\n## {ASKS_TITLE}（{len(asks)}）\n")
        parts.extend(f"- {q}\n" for q in asks)
    if aside:
        parts.append(f"\n## {ASIDE_TITLE}（{len(aside)}）\n")
        parts.extend(_row(i) + "\n" for i in _newest_first(aside))
    if not opened and not asks:
        parts.append(f"\n{NOTHING}\n")
    parts.append(renders.carried_section(vault, MD_POSIX))
    return "".join(parts)


def render(vault, data=None, asks=None, turn_id=None):
    """寫 系統/待處理.md 並記指紋（_lib/renders；有 turn_id 時 renders.json 先備進回合，退回後兩邊一致）。"""
    data = data if data is not None else load(vault)
    text = render_text(vault, data, asks)
    target = Path(vault) / MD_POSIX
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    renders.record(vault, MD_POSIX, text, turn_id=turn_id, lines=item_lines(data, vault))


def refresh(vault, turn_id, asks=None):
    """重寫一次（健檢、遷移、kb asks --all 用）：照樣先比對手改；asks 給了就換掉記著的「整理時要問你的」。"""
    if asks is not None:
        renders.set_asks(vault, asks)
    return _edit(vault, turn_id, lambda items: True)


# ---------- 健檢、報告、手改（lint 題） ----------

def _answered(item, once, fp):
    """她（或 AI 照她的話）處理過、不是程式自己關的：once 的一律算，其他要對象指紋跟那時一樣（測試 T-2）。"""
    return (item.get("status") in ("done", "dismissed", "closed") and item.get("how") != LINT_GONE_HOW and not item.get("cleared")
            and (once or item.get("fp") == fp))


def add_lint(vault, key, text, data, turn_id, file_ids=(), entity_ids=(), once=False, check=None, fp=None):
    """健檢要她決定的一件：同 key 已有 open 的不重複（文字、資料、指紋變了就更新）；她答過（done、dismissed）而對象指紋 fp
    跟那時一樣的不重開，once 的答過就不再開（骨架檔：鍵本身帶她那份的指紋）。check 是哪一項健檢開的（條件消失時照它關）。
    回 (編號, 新開了嗎)。"""
    found = {}

    def change(items):
        old = next((i for i in items if i.get("kind") == "lint" and i.get("key") == key and i.get("status") == "open"), None)
        if old is None:
            old = next((i for i in items if i.get("kind") == "lint" and i.get("key") == key and _answered(i, once, fp)), None)
        if old is not None:
            found.update(id=old["id"], created=False)
            if old.get("status") == "open" and (old.get("text"), old.get("data"), old.get("fp")) != (text, data, fp):
                old.update(text=text, data=data, fp=fp, check=check or old.get("check"))
                return True
            return False
        item = _new_item(items, "lint", file_ids, text, None, turn_id, entity_ids)
        item.update(key=key, data=data, check=check, fp=fp)
        items.append(item)
        found.update(id=item["id"], created=True)
        return True
    _edit(vault, turn_id, change)
    return found["id"], found["created"]


def close_lint_gone(vault, owner, ran, keep, turn_id):
    """這次跑成功的健檢（ran：check 名稱）沒再回的 lint 件關掉（how 是 LINT_GONE_HOW，測試 T-1）；owner(item) 回那件是哪一項開的。
    keep 是這次又回的 key。她答過的同 key 件記 cleared（條件消失過，之後復發算新的一件，第二輪 T-26）。回關掉幾件。"""
    if not _path(vault).exists():
        return 0
    got = {}

    def change(items):
        gone = [i for i in items if i.get("kind") == "lint" and owner(i) in ran and i.get("key") not in keep]
        hits = [i for i in gone if i.get("status") == "open"]
        for i in hits:
            _close(i, "done", LINT_GONE_HOW)
        forgot = [i for i in gone if i.get("status") in ("done", "dismissed") and not i.get("cleared")]
        for i in forgot:
            i["cleared"] = _today()
        got["n"] = len(hits)
        return bool(hits or forgot)
    _edit(vault, turn_id, change)
    return got.get("n", 0)


def add_report(vault, month, turn_id):
    """「這個月的健檢報告出來了」：其他月的 open report 自動 done（最多一件，D-227）；同月已有 open 的不重複。回 (編號, 新開了嗎)。"""
    found = {}

    def change(items):
        changed = False
        for i in items:
            if i.get("kind") == "report" and i.get("status") == "open" and (i.get("data") or {}).get("month") != month:
                _close(i, "done", REPORT_REPLACED)
                changed = True
        old = next((i for i in items if i.get("kind") == "report" and i.get("status") == "open"), None)
        if old is not None:
            found.update(id=old["id"], created=False)
            return changed
        item = _new_item(items, "report", [], REPORT_TEXT.format(month=month), None, turn_id)
        item["data"] = {"month": month}
        items.append(item)
        found.update(id=item["id"], created=True)
        return True
    _edit(vault, turn_id, change)
    return found["id"], found["created"]


def _handedit_item(items, rel, saved, diff, turn_id):
    item = _new_item(items, "handedit", [], HANDEDIT_TEXT.format(name=Path(rel).name), None, turn_id)
    item["data"] = {"file": rel, "saved": saved, "diff": diff}
    return item


def add_handedit(vault, rel, saved, diff, turn_id):
    """日程、裁示被手改（D-223）：開一條 handedit；回編號。"""
    found = {}

    def change(items):
        item = _handedit_item(items, rel, saved, diff, turn_id)
        items.append(item)
        found["id"] = item["id"]
        return True
    _edit(vault, turn_id, change)
    return found["id"]


def _today():
    return date.today().isoformat()


def _snapshot(vault, file_ids):
    """先放著那一刻相關原檔的指紋與重做記號（之後變了就回到前面）；清冊讀不到回空。"""
    try:
        from kb._lib import cards, inventory
        by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    except Exception:  # noqa: BLE001
        return {}
    return {f: {"fp": cards.current_fp(by_id[f]), "redo": by_id[f].get("redo")} for f in file_ids if f in by_id}


def _open_item(items, pid):
    item = next((i for i in items if i.get("id") == pid), None)
    if item is None or item.get("status") != "open":
        raise KeyError(NOT_FOUND)
    return item


def set_aside(vault, pid, by, turn_id):
    """收進先放著（by 是 customer 或 lint）；已經放著的照回。找不到或不是 open 丟 KeyError(NOT_FOUND)。"""
    got = {}

    def change(items):
        item = _open_item(items, pid)
        got["item"] = item
        if item.get("set_aside"):
            return False
        item["set_aside"] = {"at": _today(), "by": by, "fps": _snapshot(vault, item.get("file_ids") or [])}
        return True
    _edit(vault, turn_id, change)
    return dict(got["item"])


EXPIRED_REASON = "沒回，照推測"


def close_expired(vault, pid, turn_id, reason=EXPIRED_REASON):
    """放寬乙：客戶一直沒回、超過 ask_expire_days 的題關掉（status closed）；不是 open 或找不到的照回 False。
    reason 寫進 reason 與 how（unreadable 那種本來就有 reason 的不蓋掉）。"""
    got = {}

    def change(items):
        item = next((i for i in items if i.get("id") == pid), None)
        if item is None or item.get("status") != "open":
            return False
        _close(item, "closed", reason)
        item.setdefault("reason", reason)
        got["ok"] = True
        return True
    _edit(vault, turn_id, change)
    return bool(got.get("ok"))


def _back(item):
    item["set_aside"] = None
    item["returned"] = _today()


def back(vault, pid, turn_id):
    """從先放著拿回來（沒放著的照回）。找不到或不是 open 丟 KeyError(NOT_FOUND)。"""
    got = {}

    def change(items):
        item = _open_item(items, pid)
        got["item"] = item
        if not item.get("set_aside"):
            return False
        _back(item)
        return True
    _edit(vault, turn_id, change)
    return dict(got["item"])


def wake(vault, file_ids=(), entity_ids=(), turn_id=None):
    """跟這些檔或實體有關的先放著回到前面（卡重做、實體有新口述或裁示時，各寫入點叫）。沒有 pending.json 不建。回幾件。"""
    files, ents = set(file_ids or []), set(entity_ids or [])
    if not _path(vault).exists() or not (files or ents):
        return 0
    if turn_id is None:
        from kb._lib import turn
        turn_id = turn.current(vault)["turn"]

    def change(items):
        hits = [i for i in items if i.get("status") == "open" and i.get("set_aside")
                and (files & set(i.get("file_ids") or []) or ents & set(i.get("entity_ids") or []))]
        for i in hits:
            _back(i)
        return len(hits)
    return _edit(vault, turn_id, change) or 0


def due_back(vault, items, today=None, wake_days=None):
    """先放著、而現在該回到前面的編號（只讀，D-226）：相關原檔指紋或重做記號跟放著那時不同（原檔換版沒有寫入點可叫 wake，
    在這裡比）、掛的實體在放著之後有新的口述或裁示、data.dates 裡有離今天剩 wake_days 天以內的。讀不到的那一類略過。"""
    aside = [i for i in items if i.get("status") == "open" and i.get("set_aside")]
    if not aside:
        return set()
    from kb._lib import config
    today = today or _today()
    if wake_days is None:
        wake_days = config.get(vault, "set_aside_wake_days")
    out = set()
    try:
        from kb._lib import cards, inventory
        by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    except Exception:  # noqa: BLE001
        by_id = {}
    news = _knowledge_dates(vault)
    limit = (date.fromisoformat(today) + timedelta(days=wake_days)).isoformat()  # 今天到 limit 之間才算快到期（過了的不算，測試 T-3）
    for i in aside:
        snap = (i["set_aside"] or {}).get("fps") or {}
        at = (i["set_aside"] or {}).get("at") or ""
        for fid, was in snap.items():
            e = by_id.get(fid)
            if e is not None and (cards.current_fp(e) != was.get("fp") or e.get("redo") != was.get("redo")):
                out.add(i["id"])
        if any(d > at for eid in i.get("entity_ids") or [] for d in news.get(eid, ())):
            out.add(i["id"])
        if any(today <= str(d) <= limit for d in (i.get("data") or {}).get("dates") or []):
            out.add(i["id"])
    return out


def _knowledge_dates(vault):
    """{實體編號: [有效口述與裁示的日期]}；讀不到回空。"""
    try:
        from kb._lib import entities, rulings, statements
        ent = entities.load(vault)
        out = {}
        for s in statements.load(vault)["statements"] + rulings.load(vault)["rulings"]:
            if s.get("status") != "active":
                continue
            for name in s.get("entities") or []:
                e, _hit = entities.find(ent, name)
                if e is not None:
                    out.setdefault(e["id"], []).append(str(s.get("date") or ""))
        return out
    except Exception:  # noqa: BLE001
        return {}


def wake_due(vault, turn_id, today=None):
    """把 due_back 認出的寫回（健檢做）。回幾件。"""
    if not _path(vault).exists():
        return 0

    def change(items):
        ids = due_back(vault, items, today)
        hits = [i for i in items if i.get("id") in ids]
        for i in hits:
            _back(i)
        return len(hits)
    return _edit(vault, turn_id, change) or 0


def set_aside_old(vault, days, turn_id, today=None):
    """open 超過 days 天沒動（created 或最後回到前面的日期）的收進先放著（by lint，D-226）。回幾件。"""
    if not _path(vault).exists():
        return 0
    today = today or _today()
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()

    def change(items):
        hits = [i for i in items if i.get("status") == "open" and not i.get("set_aside")
                and max(str(i.get("created") or "")[:10], str(i.get("returned") or "")) < cutoff]
        for i in hits:
            i["set_aside"] = {"at": today, "by": "lint", "fps": _snapshot(vault, i.get("file_ids") or [])}
        return len(hits)
    return _edit(vault, turn_id, change) or 0
