"""共用流水檔（多人共用 1，D-288）：每台只寫自己的 .system/streams/<種類>/<這台代號>.jsonl，一行一筆改動，讀的時候合起來。

行：{"v","op":"create|set|mask","id","seq","base","by","who","at","turn","set":{欄:值},"unset":[欄],"reverts":[…],
"drop":{欄:[元素]},"undrop":{欄:[元素]},"sub":{欄:{元素id:{改了的那幾欄}}}}。
合併：同一筆照 seq、同 seq 照 by 排；每一欄取最後一個設這一欄的行；兩行設同一欄、值不同、彼此都沒看過對方
（A.seq > B.base 而且 B.seq > A.base）才算撞到，進 conflicts。
只往後加的欄（APPEND_ONLY，D-337）：不取最後一行，把每一行帶來的清單合起來（同內容只留一次、照 date 再照版號排），不算撞到；
退回的行帶 drop（被退回那次加的元素，之後別台的行再帶著也不回來）、undrop（退回一次退回：放回來）。
逐項合併的欄（NESTED，D-552 子項）：行不放整份清單，sub 只記改了的元素的那幾欄；每個元素的每一欄各自取最後一行，
撞到的欄名是「欄:元素id:那一欄」；拿掉的元素標 del 留著（restore、退回用）；set 裡整份清單的舊寫法當成基準照讀。
每行帶 seen（{台代號: 這筆在那台的最大 seq}，測試裁定 11）判「看過沒」，沒有 seen 的舊行照舊用 base；
合併前每行先驗型別與字數，不合的整行略過、記 log（測試裁定 1、3）。
標籤（KEYED，D-553）：sub 記「鍵: {on, text}」，加的贏、不算撞到；註記（RESOLVE）是一般欄另套「解掉」。
各模組照舊「整份讀進、整份交回」：io.read_json／write_json_atomic 認得 LEGACY 的檔名，轉到這裡（doc_to_records／records_to_doc）。
刪掉一筆寫 set {"_del": true}（讀回來不列）；undo 一筆新增寫 RETRACT 的收回欄（kb versions undo）。
"""
import hashlib
import json
import marshal
import math
import os
import unicodedata
import secrets
import time
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

FORMAT_VERSION = 4  # 跟大升級走（D-335）：遷移表有新列、或行多了舊版看不懂的寫法（4：子項的 sub，D-552 (12)）的那一版 +1，
# tools/release.py 守；舊版讀到比它新的行照讀、寫之前停（D-298）
STREAMS_REL = ".system/streams"
LOCAL_REL = ".system/local"
LEGACY_REL = ".system/legacy"
MASKED = "（已遮掉）"
META_ID = "__meta__"
PROFILE_ID = "profile"

# 種類 → (舊檔相對路徑, 形狀, 清單或對照的鍵, 收回欄)
KINDS = {
    "statements": (".system/statements.json", "list", "statements", {"status": "withdrawn"}),
    "agenda": (".system/agenda.json", "list", "items", {"status": "cancelled"}),
    "rulings": (".system/rulings.json", "list", "rulings", {"status": "revoked"}),
    "findings": (".system/findings.json", "list", "findings", {"status": "withdrawn"}),
    "entities": (".system/entities.json", "list", "entities", {"status": "removed"}),
    "sources": (".system/sources.json", "list", "sources", {"deleted": True}),
    "set_aside": (".system/set_aside.json", "map", "keys", {"deleted": True}),
    "card_base": (".system/card_base.json", "map", "cards", {"deleted": True}),
    "profile": (".system/profile.json", "profile", None, {}),
    "inventory": (".system/inventory.json", "list", "entries", {"status": "deleted"}),
    "pending": (".system/pending.json", "list", "items", {"status": "done"}),
}
# 種類 → 只往後加的欄（D-337）：合起來時各行的清單聯集、不算撞到；退回只拿掉那次加的元素
APPEND_ONLY = {"agenda": ("history",)}
# 種類 → 逐項合併的欄（D-552）：清單的每個元素帶 id，text、done、pos、del 各自合併，留言（comments）照 D-337 合起來
NESTED = {"agenda": ("checklist", "tags")}
KEYED = ("tags",)  # 逐項合併的欄裡是字串清單的（D-553 標籤）：元素是比對用的鍵（casefold），只有 on、text；加的贏、不算撞到
RESOLVE = {"agenda": ("note",)}  # 一般欄、另套「解掉」：之後有一行看過兩邊又設了這一欄就不再列撞到（D-553）
SUB_CLASH = ("text", "done", "del")  # 會撞到的子項欄；pos 取最後一行、不算撞到
# 種類 → 取大的欄（裁定 7）：照一般欄取最後一行，但跟最後一行彼此沒看過的行設了更大的值就取大的，不算撞到
BIGGER = {"agenda": ("round",)}
FOLLOW = {"agenda": {"date": "round"}}  # 自動移（同一行把 round 加大）的 date 跟 round 大的那行走、不算撞到（測試第 3 輪裁定 16）
# 掃描看到的、只在這台算的那一半放 .system/local/<種類>.json（新定 3）；共用那一半走流水檔
SPLIT = ("inventory", "pending", "profile")
# kb versions overwritten 預設不列、--kind 指名才列的種類（#78 第二輪裁定 2）：每台自己算、自己寫的，兩台來回寫各自的值不是誰蓋掉誰講的話
OVERWRITTEN_ASK = ("inventory", "card_base", "pending")
LOCAL_PROFILE = ("md_sha256",)  # 設定檔 md 這台渲染出的指紋：md 不進 git、每台自己畫（#42），比手改要用這台的
LOCAL_PENDING_KINDS = ("missing_file", "unreadable")  # 原檔不見、讀不到：看這台的掃描，只開在本機
RETRACT = {k: v[3] for k, v in KINDS.items()}
# 封存檔不再是獨立的檔：同一筆多一欄 archived（新定 4）。這兩個是同一份流水檔的「封存那一邊」的整份文件視角
VIEWS = {
    "statements_archive": ("statements", ".system/statements-archive.json"),
    "findings_archive": ("findings", ".system/findings-archive.json"),
}
HIDE_ARCHIVED = ("statements", "findings")  # 這兩種的主文件不列 archived 有值的筆
LEGACY = {Path(v[0]).name: k for k, v in KINDS.items()}
LEGACY.update({Path(v[1]).name: k for k, v in VIEWS.items()})


def base(kind):
    return VIEWS[kind][0] if kind in VIEWS else kind


def legacy_rel(kind):
    return VIEWS[kind][1] if kind in VIEWS else KINDS[kind][0]


def _is_archived(rec):
    return bool(rec.get("archived"))


def append_only(kind):
    """這種類只往後加的欄（D-337）；沒有回空的。"""
    return APPEND_ONLY.get(base(kind), ())


def nested(kind):
    """這種類逐項合併的欄（D-552）；沒有回空的。"""
    return NESTED.get(base(kind), ()) if kind else ()


COMPACT_BYTES = 1_000_000  # 自己的檔超過這個大小，寫完順手併掉被同一台後面的行蓋過的舊行
hard_delete = False  # purge 期間：刪掉的筆連這台檔裡的舊行一起拿掉（原文不留在自己的檔裡）
stats = {"merges": 0}  # 快取沒命中、真的合併的次數（測試計次用）
_TURN = {}  # (vault, kind) → 最近一次 turn.backup 記的回合代號
_MEMO = {}  # (vault 絕對路徑, kind) → (靠的檔的樣子, 整份文件的 marshal 快照)：同一個 process 內 read_doc 的結果，不寫檔、不跨指令（D-548 (3)）
GEN = [0]  # 清全部或清清冊時 +1：inventory.load 記住的跟著失效


# ---------- 本機代號 ----------

def machine_id(vault):
    from kb._lib import env
    return env.machine_id(vault)


def stream_dir(vault, kind):
    return Path(vault) / STREAMS_REL / base(kind)


def own_file(vault, kind):
    return stream_dir(vault, kind) / f"{machine_id(vault)}.jsonl"


def virtual_kind(path):
    """這個路徑是不是搬去流水檔的狀態檔；是回 (vault, kind)。"""
    p = Path(path)
    kind = LEGACY.get(p.name)
    if kind is None or p.parent.name != ".system":
        return None
    return p.parent.parent, kind


def note_turn(vault, kind, turn_id):
    _TURN[(str(vault), base(kind))] = turn_id


def _vkey(vault):
    return os.path.normcase(os.path.abspath(vault))


def _stat3(path):
    try:
        st = os.stat(path)
        return (True, st.st_size, st.st_mtime_ns)
    except OSError:
        return (False, 0, 0)


def memo_sig(vault, kind):
    """read_doc 的結果靠哪些檔：這種類的流水檔、舊檔副本、副本記號（有本機那一半的再加那一半），各 (存在, 大小, mtime_ns)。"""
    sig = [(f.name,) + _stat3(f) for f in _files(vault, base(kind))]
    if kind in SPLIT:
        sig.append(_stat3(local_path(vault, kind)))
    sig += [_stat3(Path(vault) / legacy_rel(kind)), _stat3(_mirror_mark(vault, kind))]
    return tuple(sig)


def forget(vault=None, kind=None):
    """清掉 read_doc（與 inventory.load）記住的結果：不給 vault 清全部（測試用）；給了清這種類，連同一份流水檔的封存視角。"""
    if vault is None or base(kind) == "inventory":
        GEN[0] += 1
    if vault is None:
        _MEMO.clear()
        return
    v, b = _vkey(vault), base(kind)
    for k in [k for k in _MEMO if k[0] == v and base(k[1]) == b]:
        del _MEMO[k]


# ---------- 本機那一半（清冊的掃描所見、待處理的原檔不見／讀不到） ----------

def local_path(vault, kind):
    return Path(vault) / LOCAL_REL / f"{kind}.json"


def _read_local(vault, kind):
    try:
        data = json.loads(local_path(vault, kind).read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_local(vault, kind, items):
    from kb._lib.io import write_json_atomic as _w
    p = local_path(vault, kind)
    if _read_local(vault, kind) == items and (p.is_file() or not items):
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(p):
            _w(p, {"schema_version": 1, "items": items})
    finally:
        forget(vault, kind)


def _split(kind, doc):
    """整份文件 → (共用那份, 本機那一半 {id: {…}})。"""
    if kind == "profile":  # 共用那份第一次建時帶著（舊版的別台還讀它），之後 write_doc 不跟著改；讀的時候換成這台的
        return doc, {k: doc.get(k) for k in LOCAL_PROFILE if k in doc}
    key = KINDS[kind][2]
    shared = {k: v for k, v in doc.items() if k != key}
    local, out = {}, []
    for e in doc.get(key) or []:
        if not isinstance(e, dict):
            raise TypeError("條目不是物件")
        e = dict(e)
        if kind == "pending":
            if e.get("kind") in LOCAL_PENDING_KINDS:
                local[str(e.get("id"))] = e
            else:
                out.append(e)
            continue
        loc = {}
        if e.get("read") not in (None, "ok"):
            loc["read"] = e["read"]
        if e.get("error"):
            loc["error"] = e["error"]
        if e.get("missing_since"):
            loc["missing_since"] = e["missing_since"]
        for k in ("read", "error", "missing_since"):
            e.pop(k, None)
        if e.get("status") == "missing":
            loc["status_seen"] = "missing"
            e["status"] = "active"
        locs, plain = {}, []
        for L in e.get("locations") or []:
            if isinstance(L, dict):
                if "size" in L or "mtime" in L:
                    locs[L.get("uri")] = {k: L[k] for k in ("size", "mtime") if k in L}
                plain.append({k: v for k, v in L.items() if k not in ("size", "mtime")})
            else:
                plain.append(L)
        if "locations" in e:
            e["locations"] = plain
        if locs:
            loc["locs"] = locs
        if loc:
            local[str(e.get("id"))] = loc
        out.append(e)
    shared[key] = out
    return shared, local


def _join(vault, kind, doc):
    """共用那份 + 本機那一半 → 呼叫端看到的整份文件。"""
    local = _read_local(vault, kind)
    if kind == "profile":  # 這台沒記過（升級前、新 clone）就先用共用的
        return dict(doc, **{k: local[k] for k in LOCAL_PROFILE if local.get(k)})
    key = KINDS[kind][2]
    doc = dict(doc)
    rows = [dict(e) for e in doc.get(key) or []]
    if kind == "pending":
        ids = {e.get("id") for e in rows}
        rows += [dict(v) for k, v in local.items() if k not in ids]
        doc[key] = rows
        return doc
    for e in rows:
        loc = local.get(str(e.get("id")), {})
        e["read"] = loc.get("read", "ok")
        e["missing_since"] = loc.get("missing_since")
        if loc.get("error"):
            e["error"] = loc["error"]
        if loc.get("status_seen") == "missing":
            e["status"] = "missing"
        if "locations" in e:
            locs = loc.get("locs") or {}
            e["locations"] = [{"uri": L.get("uri"), **locs.get(L.get("uri"), {}), **{k: v for k, v in L.items() if k != "uri"}}
                              if isinstance(L, dict) else L for L in e["locations"]]
    doc[key] = rows
    return doc


def joined_doc(vault, kind, recs):
    """流水檔合起來的紀錄 → 呼叫端看到的整份文件（有本機那一半的種類會接上）。"""
    doc = records_to_doc(kind, recs)
    return _join(vault, kind, doc) if kind in SPLIT else doc


# ---------- 讀 ----------

class State:
    def __init__(self):
        self.records = {}
        self.meta = {}
        self.conflicts = []
        self.seen = {}  # id → {台代號: 這筆在那台的最大 seq}（測試裁定 11）


def _log(vault, text):
    try:
        from kb._lib import ledger
        ledger.log_error(vault, text, source="streams")
    except Exception:  # noqa: BLE001
        pass


CONFLICT_MARKS = ("<<<<<<<", "=======", ">>>>>>>")


def _read_file(vault, path):
    """[(idx, 行)]；最後一行不完整略過並記 log，其他壞行丟 ValueError。"""
    try:
        raw = Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path}（不是 UTF-8：{e}）") from e
    lines = raw.split("\n")
    out = []
    for i, text in enumerate(lines):
        if not text.strip():
            continue
        if text.startswith(CONFLICT_MARKS):  # git 合併沒收拾完留下的：那幾行略過，其他行照讀（#78 第二段第 5 點）
            _log(vault, f"{Path(path).name} 第 {i + 1} 行略過：合併衝突標記")
            continue
        try:
            row = json.loads(text)
        except ValueError as e:
            if i == len(lines) - 1 or all(not t.strip() for t in lines[i + 1:]):
                _log(vault, f"{Path(path).name} 最後一行不完整，略過")
                continue
            raise ValueError(f"{path}（第 {i + 1} 行讀不懂：{e}）") from e
        if not isinstance(row, dict):  # 整行不是物件：略過（測試裁定 1）
            _log(vault, f"{Path(path).name} 第 {i + 1} 行略過：不是物件")
            continue
        out.append((i, row))  # 比這版新的行照收（認得的欄照算、不認得的略過）；寫之前才擋（check_writable，多人共用 2 第 4 點）
    return out


TEXT_MAX = {"agenda": {"checklist": 200, "comment": 500, "tag": 20, "note": 1000}}  # 合併時的字數上限照 CLI
CAPS = {"agenda": {"checklist": 200, "comments": 1000, "tags": 50}}  # 防亂寫的硬上限：多的不列
BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_text(value, keep_newline=False):
    """拿掉 Unicode 類別 Cc 與雙向控制字元（keep_newline：留 \n、\t，註記用）；不是字串照回。"""
    if not isinstance(value, str):
        return value
    return "".join(c for c in value if c not in BIDI and (unicodedata.category(c) != "Cc" or (keep_newline and c in "\n\t")))


class _Bad(Exception):
    pass


def _need(ok, why):
    if not ok:
        raise _Bad(why)


def _nat(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _ostr(v):
    return v is None or isinstance(v, str)


def _fin(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _strs(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def bad_row(row, kind=None):
    """一行合併前先驗（測試裁定 1）：不合回原因，合回 None。"""
    try:
        _check_row(row, kind)
    except _Bad as e:
        return str(e)
    return None


def _check_row(row, kind):
    _need(isinstance(row, dict), "不是物件")
    for f in ("seq", "base"):
        if f in row:
            _need(_nat(row[f]), f"{f} 不是 0 以上的整數")
    for f in ("id", "by"):
        if f in row:
            _need(isinstance(row[f], str), f"{f} 不是字串")
    for f in ("who", "at", "op"):
        _need(_ostr(row.get(f)), f"{f} 不是字串")
    if row.get("op") == "mask":
        _need(_strs(row.get("ids") or []) and _strs(row.get("fields") or []), "遮字那行的 ids、fields 不對")
        return
    got = row.get("set")
    _need(got is None or isinstance(got, dict), "set 不是物件")
    _need(_strs(row.get("unset") or []), "unset 不是字串清單")
    _need(not set(got or {}) & set(row.get("unset") or []), "同一欄又 set 又 unset")  # 測試第 2 輪裁定 12
    for name in ("drop", "undrop"):
        d = row.get(name)
        _need(d is None or (isinstance(d, dict) and all(isinstance(v, list) for v in d.values())), f"{name} 不對")
    sub = row.get("sub")
    _need(sub is None or (isinstance(sub, dict) and all(isinstance(v, dict) for v in sub.values())), "sub 不是物件")
    if kind and base(kind) == "agenda":
        _check_agenda(got or {}, sub or {})


def _check_comment(c):
    lim = TEXT_MAX["agenda"]["comment"]
    _need(isinstance(c, dict) and isinstance(c.get("id"), str), "留言的 id 不是字串")
    _need(isinstance(c.get("text"), str), "留言的字不是字串")
    _need(len(c["text"]) <= lim, f"留言超過 {lim} 字")
    _need(_ostr(c.get("by")) and _ostr(c.get("at")), "留言的 by、at 不是字串")
    _need(isinstance(c.get("del", False), bool), "留言的 del 不是真假值")


def _check_text(v, whole):
    lim = TEXT_MAX["agenda"]["checklist"]
    _need(isinstance(v, str) or (v is None and not whole), "子項的字不是字串")
    _need(v is None or len(v) <= lim, f"子項超過 {lim} 字")


def _check_agenda(got, sub):
    lim = TEXT_MAX["agenda"]
    if "round" in got:
        _need(_nat(got["round"]), "round 不是 0 以上的整數")
    if "note" in got:
        _need(_ostr(got["note"]), "note 不是字串")
        _need(got["note"] is None or len(got["note"]) <= lim["note"], f"註記超過 {lim['note']} 字")
    if "tags" in got:
        _need(_strs(got["tags"]), "tags 不是字串清單")
        _need(all(len(t) <= lim["tag"] for t in got["tags"]), f"標籤超過 {lim['tag']} 字")
    if "checklist" in got:
        _need(isinstance(got["checklist"], list), "checklist 不是清單")
        for e in got["checklist"]:
            _need(isinstance(e, dict) and isinstance(e.get("id"), str), "子項不是物件或 id 不是字串")
            _check_text(e.get("text"), True)
            _need(e.get("pos") is None or _fin(e["pos"]), "pos 不是有限的數字")
            _need(isinstance(e.get("done", False), bool) and isinstance(e.get("del", False), bool), "done、del 不是真假值")
            _need(_nat(e.get("done_round", 0)), "done_round 不是 0 以上的整數")
            _need(all(_ostr(e.get(f)) for f in ("done_by", "done_at", "removed_by", "removed_at", "restored_by", "restored_at")),
                  "子項的人、時間不是字串")
            cs = e.get("comments", [])
            _need(isinstance(cs, list), "comments 不是清單")
            for c in cs:
                _check_comment(c)
    for cid, ch in (sub.get("checklist") or {}).items():
        _need(isinstance(ch, dict), "子項的改動不是物件")
        if "text" in ch:
            _check_text(ch["text"], False)
        _need(ch.get("pos") is None or _fin(ch["pos"]), "pos 不是有限的數字")
        if "done" in ch:
            g = ch["done"]
            _need(isinstance(g, dict) and isinstance(g.get("done"), bool), "done 那組不對")
            _need(_ostr(g.get("by")) and _ostr(g.get("at")) and _nat(g.get("round", 0)), "done 那組的人、時間、回合不對")
        if "del" in ch:
            g = ch["del"]
            _need(isinstance(g, bool) or (isinstance(g, dict) and isinstance(g.get("del"), bool) and _ostr(g.get("by"))
                                          and _ostr(g.get("at"))), "del 那組不對")
        if "comments_add" in ch:
            _need(isinstance(ch["comments_add"], list), "comments_add 不是清單")
            for c in ch["comments_add"]:
                _check_comment(c)
        for f in ("comments_drop", "comments_undrop"):
            if f in ch:
                _need(_strs(ch[f]), f"{f} 不是字串清單")
    for key, ch in (sub.get("tags") or {}).items():
        _need(isinstance(ch, dict) and isinstance(ch.get("on"), bool), "標籤的改動不對")
        _need(_ostr(ch.get("text")) and len(ch.get("text") or "") <= lim["tag"], f"標籤不是字串或超過 {lim['tag']} 字")


def _good_rows(vault, path, kind):
    """讀一個檔、驗過的行 [(idx, 行)]；不合的整行略過、記 log。"""
    out = []
    for i, row in _read_file(vault, path):
        why = bad_row(row, kind)
        if why:
            _log(vault, f"{Path(path).name} 第 {i + 1} 行略過：{why}")
            continue
        out.append((i, row))
    return out


def _seen(row):
    """這行的 seen（測試裁定 11）：型別不對當沒有（None）。"""
    v = row.get("seen")
    if isinstance(v, dict) and all(isinstance(k, str) and _nat(x) for k, x in v.items()):
        return v
    return None


def _saw(x, y):
    """x 看過 y：x 有 seen 看 seen，沒有（格式 3 以前）照舊用 base。"""
    got = _seen(x)
    if got is not None:
        return got.get(str(y.get("by", "")), 0) >= _seq(y)
    return _base(x) >= _seq(y)


class NewerFormat(Exception):  # noqa: F811  不是 ValueError：各 CLI 的 except ValueError 不要吞掉，由 kb/cli.py 統一回那一句；  帶寫那一行的 willy 版本，給「同事已經用 X 記過東西了」那句用
    def __init__(self, message, app=None, version=None):
        super().__init__(message)
        self.app, self.version = app, version


def check_writable(vault, kind=None):
    """寫之前檢查：有任何一個流水檔（kind 沒給就全部種類）有比這版新的格式的行 → 丟 NewerFormat（app 是那行記的 willy 版本）。"""
    kinds = [base(kind)] if kind else list(KINDS)
    for k in kinds:
        for f in _files(vault, k):
            try:
                raw = Path(f).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for text in raw.split("\n"):
                if '"v"' not in text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                v = row.get("v", 1) if isinstance(row, dict) else 1
                if isinstance(v, int) and v > FORMAT_VERSION:
                    raise NewerFormat(f"{Path(f).name} 有比這版 plugin 新的格式（v{v}）", row.get("app"), v)


def _files(vault, kind):
    d = stream_dir(vault, kind)
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def exists(vault, kind):
    return bool(_files(vault, kind))


def _sig(files):
    out = []
    for f in files:
        st = f.stat()
        out.append([f.name, st.st_size, st.st_mtime_ns])
    return out


def _cache_path(vault, kind):
    return Path(vault) / LOCAL_REL / "cache" / f"{kind}.json"


def load(vault, kind):
    kind = base(kind)
    files = _files(vault, kind)
    sig = _sig(files)
    cp = _cache_path(vault, kind)
    try:
        c = json.loads(cp.read_text(encoding="utf-8"))
        if c.get("sig") == sig and c.get("v") == FORMAT_VERSION:  # 舊版算的快取（合併規則可能不同，D-337）不用
            st = State()
            st.records = {k: v for k, v in c["records"]}
            st.meta, st.conflicts, st.seen = c["meta"], c["conflicts"], c["seen"]
            return st
    except (OSError, ValueError, KeyError, TypeError):
        pass
    st = _merge(vault, files, kind)
    if files:
        _mirror(vault, kind, st)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_name(cp.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"sig": sig, "v": FORMAT_VERSION, "records": list(st.records.items()), "meta": st.meta,
                                   "conflicts": st.conflicts, "seen": st.seen}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cp)
    except OSError:
        pass
    return st


def _mirror(vault, kind, st):
    """合併結果照舊的整份 JSON 寫回舊路徑，當這台的衍生副本（不進 git）：還直接開舊路徑讀、查有沒有檔的程式照舊能用。
    正本是流水檔；副本被改、被刪都不影響（讀的一律走 streams）。
    重寫前比磁碟上的副本跟這台上次寫出的指紋（記號）：不同、內容又跟這次要寫的不一樣，就是被外面換過，
    先存 handedits 再重寫，重寫後那一種的渲染頁跟著重畫（確認第 3 輪 #14、#15：不靠 load() 的快取判斷）。"""
    swapped = False
    try:
        from kb._lib.io import write_text_atomic
        for k in [kind] + [v for v, (b, _r) in VIEWS.items() if b == kind]:
            legacy = Path(vault) / legacy_rel(k)
            text = json.dumps(joined_doc(vault, k, st.records), ensure_ascii=False, indent=2) + "\n"
            if _swapped_copy(vault, k, legacy, text):
                _keep_swapped(vault, legacy)
                swapped = True
            write_text_atomic(legacy, text)
            mp = _mirror_mark(vault, k)
            mp.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(mp, _sha(legacy.read_bytes()))
    except OSError:
        pass
    if swapped:
        _redraw(vault, kind)


def _swapped_copy(vault, kind, legacy, text):
    """副本在、有記號、指紋跟記號不同、內容跟要寫的也不同 → 被外面換過。沒記號（沒有依據）、副本不在、
    壞了或不是物件（驗收 13：直接重寫、不存）、內容一樣只是排法不同（別台寫的、退回換回的）都不算。"""
    try:
        raw = legacy.read_bytes()
        mark = _mirror_mark(vault, kind).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if _sha(raw) == mark:
        return False
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return False
    return isinstance(doc, dict) and doc != json.loads(text)


REDRAW = ("agenda", "pending", "rulings")  # 副本換過重寫後跟著重畫頁的種類（#15）；設定檔頁要拿 profile 鎖，這裡可能已在鎖裡，不畫
_REDRAWING = set()


def _redraw(vault, kind):
    """照 refresh 的重畫入口只畫那一頁；畫不出來只記 log，不擋讀寫。"""
    key = (_vkey(vault), kind)
    if kind not in REDRAW or key in _REDRAWING:
        return
    _REDRAWING.add(key)
    try:
        from kb._lib import agenda, pending, refresh, rulings
        page = {"agenda": agenda.MD_POSIX, "pending": pending.MD_POSIX, "rulings": rulings.MD_POSIX}[kind]
        refresh._render_pages(vault, None, also=(page,), only=True)
    except Exception as e:  # noqa: BLE001
        _log(vault, f"{kind} 副本重寫後重畫頁面失敗：{type(e).__name__}: {str(e)[:100]}")
    finally:
        _REDRAWING.discard(key)


def remirror_local(vault, path):
    """path 是 .system/local/<種類>.json（有本機那一半的種類）：從流水檔加這一半重寫副本（#49，退回換回本機那一半之後）。
    不是就什麼都不做；讀不了照 _mirror 的規矩不中斷。"""
    try:
        rel = Path(path).resolve().relative_to((Path(vault) / LOCAL_REL).resolve()).as_posix()
    except (ValueError, OSError):
        return
    kind = rel[:-len(".json")] if rel.endswith(".json") and "/" not in rel else None
    if kind not in SPLIT or not exists(vault, kind):
        return
    try:
        _mirror(vault, kind, load(vault, kind))
    except (OSError, ValueError):
        pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _mirror_mark(vault, kind):
    return Path(vault) / LOCAL_REL / "cache" / f"{kind}.mirror"


def _mirror_edited(vault, kind, derived=None):
    """衍生副本被外部改過（手改、別的工具整份寫回）：回新內容的 dict；沒改過回 None；改壞了丟 ValueError。"""
    legacy = Path(vault) / legacy_rel(kind)
    try:
        raw = legacy.read_bytes()
    except OSError:
        return None
    try:
        mark = _mirror_mark(vault, kind).read_text(encoding="utf-8").strip()
    except OSError:  # 還沒有記號（例如封存檔副本後來才有人寫）：內容跟合併結果不同才算外部改過
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        return doc if isinstance(doc, dict) and derived is not None and doc != derived else None
    if _sha(raw) == mark:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None  # 副本壞了不是正本壞了：呼叫端從流水檔重寫
    return doc if isinstance(doc, dict) else None


def _ekey(e):
    return json.dumps(e, ensure_ascii=False, sort_keys=True)


def _seq(row):
    s = row.get("seq", 0)
    return s if isinstance(s, int) else 0


def _old_format(row):
    v = row.get("v", 1)
    return not isinstance(v, int) or v < 2


_NONE = object()  # 暫存原本沒有那一格


def _tag_copy(state):
    """標籤暫存的複本：只複製狀態的架子，行（row）照指同一個、不複製（測試第 3 輪裁定 15）。"""
    return _NONE if state is _NONE else {k: dict(v, rows=dict(v["rows"])) for k, v in state.items()}


def _sub_copy(st):
    """一個子項合併狀態的複本：done、del 只會被整個換掉，留言只會加；行裡的值不複製（測試第 3 輪裁定 15）。"""
    return _NONE if st is _NONE else dict(st, notes=dict(st["notes"]), gone=set(st["gone"]))


class _Fold:
    """一行一行套到一筆上：一般欄取最後一行；只往後加的欄（D-337）把各行帶來的清單合起來，
    同內容只留一次、照 date 再照版號（seq、by、行的先後、清單裡的位置）排；drop 的拿掉、undrop 的放回。
    格式 1（0.34.0 以前）的行在這種欄照舊整份取代。"""

    def __init__(self, kind):
        self.lists = append_only(kind) if kind else ()
        self.nested = nested(kind)
        self.acc = {}  # (id, 欄) → ({內容: (排序, 元素)}, {拿掉的內容})
        self.subs = {}  # (id, 欄) → {元素id: 合併狀態}（逐項合併的欄，D-552）
        self.tagst = {}  # (id, 欄) → {鍵: {text, rows（每台最後一行）, last}}（KEYED 的欄，D-553）
        self.round_cap = None  # id → 這筆各行設過的最大 round；勾的回合比它大當壞值（測試裁定 6）；None 不擋
        self.notes = []  # 合併時要記 log 的

    def _cap_done(self, rid, cid, ch):
        g = ch.get("done")
        if self.round_cap is None or not isinstance(g, dict):
            return ch
        cap = self.round_cap.get(rid, 0)
        if _round(g) > cap:
            self.notes.append(f"{rid} 子項 {cid} 勾的回合 {_round(g)} 比這筆的回合 {cap} 大，當壞值略過")
            return {k: v for k, v in ch.items() if k != "done"}
        return ch

    def keep(self, rid, row):
        """套一行前把那行會動到的暫存存一份（測試第 2 輪裁定 12）：丟例外時 restore 整份還原。"""
        ks = set(row.get("set") or {}) | set(row.get("unset") or [])
        for name in ("sub", "drop", "undrop"):
            if isinstance(row.get(name), dict):
                ks |= set(row[name])
        saved = []
        for k in ks:
            key = (rid, k)
            a = self.acc.get(key, _NONE)
            saved.append((self.acc, key, _NONE if a is _NONE else (dict(a[0]), set(a[1])), None))
            saved.append((self.tagst, key, _tag_copy(self.tagst.get(key, _NONE)), None))
            items = self.subs.get(key, _NONE)  # 子項只存這行動到的那幾項（整份換掉的是新的 dict，舊的不會被改）
            cids = (row.get("sub") or {}).get(k) if isinstance(row.get("sub"), dict) else None
            one = {} if items is _NONE else {str(c): _sub_copy(items.get(str(c), _NONE)) for c in (cids if isinstance(cids, dict) else ())}
            saved.append((self.subs, key, items, one))
        return saved, len(self.notes)

    def restore(self, kept):
        saved, n = kept
        for d, key, v, one in saved:
            if v is _NONE:
                d.pop(key, None)
                continue
            d[key] = v
            for c, st in (one or {}).items():
                if st is _NONE:
                    v.pop(c, None)
                else:
                    v[c] = st
        del self.notes[n:]

    def apply(self, rec, rid, row, n=0):
        touched, grown = set(), set()
        for k, v in (row.get("set") or {}).items():
            if k in self.nested and k in KEYED:  # 整份標籤（舊寫法、新增、purge 重寫）：當成基準
                if isinstance(v, list):
                    self.tagst[(rid, k)] = _tag_reset(v, row)
                    grown.add(k)
                else:
                    rec[k] = v
                    self.tagst.pop((rid, k), None)
            elif k in self.nested:  # 整份清單（舊寫法、新增、遷移、purge 重寫）：當成基準，之後的 sub 疊上去
                if isinstance(v, list):
                    self.subs[(rid, k)] = {str(e["id"]): _sub_state(e, i) for i, e in enumerate(v)
                                           if isinstance(e, dict) and e.get("id") is not None}
                    grown.add(k)
                else:
                    rec[k] = v
                    self.subs.pop((rid, k), None)
            elif k in self.lists and isinstance(v, list):
                if _old_format(row):  # 格式 1 的行照舊讀（整份取代，含舊版退回寫的較短清單）；從格式 2 的行起才合起來
                    self.acc[(rid, k)] = ({}, set())
                seen = self.acc.setdefault((rid, k), ({}, set()))[0]
                for i, e in enumerate(v):
                    key = _ekey(e)
                    if key not in seen:
                        date = e.get("date") if isinstance(e, dict) else None
                        seen[key] = ((str(date or ""), _seq(row), str(row.get("by", "")), n, i), e)
                touched.add(k)
            else:
                rec[k] = v
                self.acc.pop((rid, k), None)
        for k in row.get("unset") or []:
            rec.pop(k, None)
            self.acc.pop((rid, k), None)
            self.subs.pop((rid, k), None)
            self.tagst.pop((rid, k), None)
        got = row.get("sub")
        for k, items in (got.items() if isinstance(got, dict) else ()):
            if k not in self.nested or not isinstance(items, dict):
                continue
            if k in KEYED:
                _tag_apply(self.tagst.setdefault((rid, k), {}), items, row)
                grown.add(k)
                continue
            subs = self.subs.setdefault((rid, k), {})
            for cid, ch in items.items():
                if isinstance(ch, dict):
                    ch = self._cap_done(rid, cid, ch)
                    _sub_apply(subs.setdefault(str(cid), _sub_state({}, 0)), ch)
            grown.add(k)
        for name in ("drop", "undrop"):
            got = row.get(name)
            for k, es in (got.items() if isinstance(got, dict) else ()):
                if k not in self.lists or not isinstance(es, list):
                    continue
                gone = self.acc.setdefault((rid, k), ({}, set()))[1]
                for e in es:
                    (gone.add if name == "drop" else gone.discard)(_ekey(e))
                if isinstance(rec.get(k), list):
                    touched.add(k)
        for k in touched:
            seen, gone = self.acc[(rid, k)]
            rec[k] = [e for key, (_s, e) in sorted(seen.items(), key=lambda kv: kv[1][0]) if key not in gone]
        for k in grown:
            if k in KEYED:
                tags = _tag_list(self.tagst[(rid, k)])
                if tags:
                    rec[k] = tags
                else:
                    rec.pop(k, None)
            else:
                rec[k] = _sub_list(self.subs[(rid, k)])
        return rec


# ---------- 逐項合併的欄（D-552 子項） ----------

def _round(group):
    r = group.get("round", 0) if isinstance(group, dict) else -1
    return r if isinstance(r, int) and not isinstance(r, bool) else 0


def _group(e):
    """子項的勾：勾沒勾、誰、何時、哪一回合，四個一組合併。"""
    return {"done": bool(e.get("done")), "by": e.get("done_by"), "at": e.get("done_at"), "round": e.get("done_round", 0)}


DEL_KEYS = ("del", "removed_by", "removed_at", "restored_by", "restored_at")


def _dgroup(e):
    """子項的拿掉：拿掉沒、誰、何時，一組合併（確認裁定 6）；單人沒有 by。放回的 by、at 是放回的人與時間。"""
    on = bool(e.get("del"))
    by, at = (e.get("removed_by"), e.get("removed_at")) if on else (e.get("restored_by"), e.get("restored_at"))
    return {"del": on, **({"by": by} if by else {}), **({"at": at} if at else {})}


def _dv(v):
    """行裡 del 的真假：一組的看 del、第一版的純真假照讀；沒有回 None。"""
    if isinstance(v, dict):
        return bool(v.get("del"))
    return None if v is None else bool(v)


def _sub_state(e, i):
    """整份清單的一項 → 合併狀態（沒有 pos 的舊寫法照清單順序）。"""
    st = {"text": e.get("text"), "pos": e.get("pos", i + 1), "done": _group(e) if e.get("done") or "done_round" in e else None,
          "del": _dgroup(e), "notes": {}, "gone": set()}
    for c in e.get("comments") or []:
        if isinstance(c, dict) and c.get("id") is not None:
            st["notes"].setdefault(c["id"], {k: v for k, v in c.items() if k != "del"})
            if c.get("del"):
                st["gone"].add(c["id"])
    return st


def _sub_apply(st, ch):
    """一行 sub 裡一個子項的改動疊上去：各欄取最後一行；勾的回合大的贏；留言用 id 合起來、drop／undrop 拿掉放回。"""
    for f in ("text", "pos"):
        if f in ch:
            st[f] = ch[f]
    if "del" in ch:
        v = ch["del"]
        st["del"] = dict(v, **{"del": bool(v.get("del"))}) if isinstance(v, dict) else {"del": bool(v)}
    g = ch.get("done")
    if isinstance(g, dict) and _round(g) >= _round(st["done"]):
        st["done"] = g
    for c in ch.get("comments_add") or []:
        if isinstance(c, dict) and c.get("id") is not None:
            st["notes"].setdefault(c["id"], c)
    for m in ch.get("comments_drop") or []:
        st["gone"].add(m)
    for m in ch.get("comments_undrop") or []:
        st["gone"].discard(m)


def _pos(e):
    v = e.get("pos")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _sub_list(subs):
    """合併狀態 → 清單（照 pos、再照 id）；拿掉的帶 del 留著；只有勾、沒有字的（沒看過新增那行）不列。"""
    out = []
    for cid, st in subs.items():
        if st["text"] is None:
            continue
        e = {"id": cid, "text": st["text"], "pos": st["pos"], "done": False}
        g = st["done"]
        if g:
            e["done"] = bool(g.get("done"))
            if g.get("by"):
                e["done_by"] = g["by"]
            if g.get("at"):
                e["done_at"] = g["at"]
            e["done_round"] = max(_round(g), 0)
        notes = sorted(st["notes"].values(), key=lambda c: (str(c.get("at") or ""), str(c.get("id"))))
        if notes:
            e["comments"] = [dict(c, **({"del": True} if c["id"] in st["gone"] else {})) for c in notes]
        g = st["del"]
        if g.get("del"):
            e["del"] = True
        for key, val in zip(("removed_by", "removed_at") if g.get("del") else ("restored_by", "restored_at"), (g.get("by"), g.get("at"))):
            if val:
                e[key] = val
        out.append(e)
    return sorted(out, key=lambda e: (_pos(e), e["id"]))


def _without_gone(kind, rec):
    """purge 整筆重寫用（裁定 6）：逐項合併的欄拿掉帶 del 的元素與留言，原文不留。"""
    rec = dict(rec)
    for k in nested(kind):
        if k not in KEYED and isinstance(rec.get(k), list):  # 標籤的清單本來就只有 on 的
            rec[k] = [dict(e, **({"comments": [c for c in e["comments"] if not (isinstance(c, dict) and c.get("del"))]}
                                 if isinstance(e.get("comments"), list) else {}))
                      for e in rec[k] if isinstance(e, dict) and not e.get("del")]
            for e in rec[k]:
                if e.get("comments") == []:
                    del e["comments"]
    return rec


def _tkey(t):
    return str(t).casefold()


def _tag_info(row, on):
    return (bool(on), str(row.get("by", "")), row)


def _tag_reset(v, row):
    info = _tag_info(row, True)
    return {_tkey(t): {"text": t, "rows": {info[1]: info}, "last": info} for t in v if isinstance(t, str)}


def _tag_apply(state, items, row):
    """一行 sub 裡的標籤：每個鍵記每台最後一行與整體最後一行；text 取最後一行。"""
    for key, ch in items.items():
        if not isinstance(ch, dict) or "on" not in ch:
            continue
        st = state.setdefault(str(key), {"text": None, "rows": {}, "last": None})
        info = _tag_info(row, ch["on"])
        st["rows"][info[1]] = info
        st["last"] = info
        if isinstance(ch.get("text"), str):
            st["text"] = ch["text"]


def _tag_on(st):
    """on 取最後一行；跟最後一行彼此沒看過的行（每台只看它最後那行）有 on 的取 on（加的贏，D-553）。"""
    last = st["last"]
    if last is None:
        return False
    return last[0] or any(i[0] and i is not last and _apart(i[2], last[2]) for i in st["rows"].values())


def _tag_list(state):
    return [st["text"] for key, st in sorted(state.items()) if st["text"] and _tag_on(st)]


def _tag_diff(old, new):
    """兩份標籤 → {鍵: {"on": True, "text"} | {"on": False}}；有一邊不是清單回 None。"""
    old, new = [] if old is None else old, [] if new is None else new
    if not isinstance(old, list) or not isinstance(new, list):
        return None
    was = {_tkey(t): t for t in old if isinstance(t, str)}
    now = {_tkey(t): t for t in new if isinstance(t, str)}
    out = {k: {"on": True, "text": t} for k, t in now.items() if was.get(k) != t}
    out.update({k: {"on": False} for k in was if k not in now})
    return out


def _undo_tags(old, cur, names):
    """退回一行的標籤鍵：那行之前有就放回（原本的寫法）、沒有就拿掉；別的鍵照現在。"""
    keys = {n.partition(":")[2].rpartition(":")[0] for n in names}
    was = {_tkey(t): t for t in (old if isinstance(old, list) else []) if isinstance(t, str)}
    now = {_tkey(t): t for t in (cur if isinstance(cur, list) else []) if isinstance(t, str)}
    for k in keys:
        if k in was:
            now[k] = was[k]
        else:
            now.pop(k, None)
    return [now[k] for k in sorted(now)]


def _by_id(items):
    return {str(e["id"]): e for e in (items if isinstance(items, list) else []) if isinstance(e, dict) and e.get("id") is not None}


def _sub_diff(old, new):
    """兩份子項清單 → {子項id: {改了的欄}}（行的 sub）；有一邊不是清單回 None（照一般欄）。
    新的那份元素可以帶 _force（欄名清單）：撞到時值一樣也寫，那處撞到就解掉；_force 不寫進行。"""
    old, new = [] if old is None else old, [] if new is None else new
    if not isinstance(old, list) or not isinstance(new, list):
        return None
    was, now, out = _by_id(old), _by_id(new), {}
    for cid, e in now.items():
        prev, force, ch = was.get(cid), set(e.get("_force") or ()), {}
        if prev is None:
            ch.update(text=e.get("text"), pos=e.get("pos"))
            if e.get("done") or "done_round" in e:
                ch["done"] = _group(e)
            if e.get("del"):
                ch["del"] = _dgroup(e)
        else:
            if e.get("text") != prev.get("text") or "text" in force:
                ch["text"] = e.get("text")
            if e.get("pos") != prev.get("pos"):
                ch["pos"] = e.get("pos")
            if _group(e) != _group(prev) or "done" in force:
                ch["done"] = _group(e)
            if _dgroup(e) != _dgroup(prev) or "del" in force:
                ch["del"] = _dgroup(e)
        olds, nows = _by_id((prev or {}).get("comments")), _by_id(e.get("comments"))
        add, drop, undrop = [], [], []
        for mid, c in nows.items():
            o = olds.get(mid)
            if o is None:
                add.append({k: v for k, v in c.items() if k != "del"})
                if c.get("del"):
                    drop.append(c["id"])
            elif bool(c.get("del")) != bool(o.get("del")):
                (drop if c.get("del") else undrop).append(c["id"])
        drop += [o["id"] for mid, o in olds.items() if mid not in nows and not o.get("del")]
        for name, got in (("comments_add", add), ("comments_drop", drop), ("comments_undrop", undrop)):
            if got:
                ch[name] = got
        if ch:
            out[cid] = ch
    for cid, prev in was.items():
        if cid not in now and not prev.get("del"):
            out[cid] = {"del": {"del": True}}
    return out


def _ch(row, k, cid):
    got = ((row.get("sub") or {}).get(k) or {}) if isinstance(row.get("sub"), dict) else {}
    ch = got.get(cid) if isinstance(got, dict) else None
    return ch if isinstance(ch, dict) else {}


def _base(row):
    b = row.get("base", 0)
    return b if isinstance(b, int) else 0


def _apart(a, b):
    """彼此都沒看過對方（測試裁定 11：有 seen 看 seen）。"""
    return not _saw(a, b) and not _saw(b, a)


def _sub_clash(lines, k, cid, f):
    """同一子項同一欄（text、done、del）兩邊改得不一樣的行；之後有一行看過兩邊又設了這一欄就算解掉（回空的）。
    done 只比勾沒勾、回合不同不算；del：拿掉的那行跟沒看過它、改了字、勾或加了留言的行也算（刪除贏，D-552 (10)）。"""
    pairs = []

    def hit(a, b):
        pairs.append((a, b))
    setters = [r for r in lines if f in _ch(r, k, cid)]
    for i, a in enumerate(setters):
        for b in setters[i + 1:]:
            va, vb = _ch(a, k, cid)[f], _ch(b, k, cid)[f]
            if f == "done":
                if not isinstance(va, dict) or not isinstance(vb, dict) or _round(va) != _round(vb) or \
                        bool(va.get("done")) == bool(vb.get("done")):
                    continue
            elif f == "del":
                if _dv(va) == _dv(vb):  # 只比真假，誰、何時不同不算
                    continue
            elif va == vb:
                continue
            if _apart(a, b):
                hit(a, b)
    if f == "del":
        for a in setters:
            if _dv(_ch(a, k, cid).get("del")) is not True:
                continue
            for b in lines:
                c = _ch(b, k, cid)
                if b is not a and "del" not in c and ("text" in c or "done" in c or c.get("comments_add")) and _apart(a, b):
                    hit(a, b)
    found = []
    for a, b in pairs:  # 每一組各自看：之後有一行看過這兩行又設了這一欄，這組就解掉
        if any(_saw(r, a) and _saw(r, b) for r in setters if r is not a and r is not b):
            continue
        for x in (a, b):
            if x not in found:
                found.append(x)
    return found


def _sub_fields(row):
    """一行 sub 動到的欄名（「欄:子項id:那一欄」；留言的三種都算 comments）。"""
    out = []
    got = row.get("sub")
    for k, items in (got.items() if isinstance(got, dict) else ()):
        for cid, ch in (items.items() if isinstance(items, dict) else ()):
            for f in (ch if isinstance(ch, dict) else ()):
                name = f"{k}:{cid}:{'comments' if f.startswith('comments_') else f}"
                if name not in out:
                    out.append(name)
    return out


def _field_val(row, field):
    """conflicts 的值：子項的欄取那行 sub 裡的那一欄（del 給整個改動，看得出拿掉還是改了什麼）。"""
    head, _, rest = field.partition(":")
    if rest and any(head in v for v in NESTED.values()):
        cid, _, f = rest.rpartition(":")
        ch = _ch(row, head, cid)
        return ch if f == "del" else ch.get(f)
    return _val(row, field)


def _undo_subs(old, new, cur, names):
    """退回一行的子項欄（D-552）：那行新加的拿掉；字、位置、勾、拿掉設回那行之前；那行加的留言拿掉、拿掉的放回。
    別的子項與別的欄照現在。勾的回合比現在舊（中間移到下一次過）時設成這一回合沒勾。"""
    import copy
    was, then = _by_id(old), _by_id(new)
    cur = copy.deepcopy(cur) if isinstance(cur, list) else []
    now = _by_id(cur)
    for name in names:
        _k, _, rest = name.partition(":")
        cid, _, f = rest.rpartition(":")
        e = now.get(cid)
        if e is None:
            continue
        prev = was.get(cid)
        if prev is None:
            e["del"] = True
        elif f in ("text", "pos"):
            e[f] = prev.get(f)
        elif f == "done":
            r = e.get("done_round", 0)
            if prev.get("done_round", 0) >= r:
                for key in ("done", "done_by", "done_at", "done_round"):
                    if key in prev:
                        e[key] = prev[key]
                    else:
                        e.pop(key, None)
            else:
                e.update(done=False, done_round=r)
                e.pop("done_by", None)
                e.pop("done_at", None)
        elif f == "del":  # 整組設回那行之前（確認裁定 6）
            for key in DEL_KEYS:
                if prev.get(key):
                    e[key] = prev[key]
                else:
                    e.pop(key, None)
        elif f == "comments":
            pc, nc = _by_id(prev.get("comments")), _by_id((then.get(cid) or {}).get("comments"))
            cc = _by_id(e.get("comments"))
            for mid, c in nc.items():
                if mid in cc and not c.get("del") and (mid not in pc or pc[mid].get("del")):
                    cc[mid]["del"] = True
                elif mid in cc and c.get("del") and mid in pc and not pc[mid].get("del"):
                    cc[mid].pop("del", None)
    return cur


def _merge(vault, files, kind=None):
    stats["merges"] += 1
    rows = []
    for f in files:
        for i, row in _good_rows(vault, f, kind):
            rows.append((_seq(row), str(row.get("by", "")), len(rows), row, Path(f).name, i))
    rows.sort(key=lambda r: r[:3])
    st = State()
    fold = _Fold(kind)
    fold.round_cap = {}
    for r in rows:
        got = r[3].get("set") or {}
        if isinstance(r[3].get("id"), str) and _nat(got.get("round")):
            fold.round_cap[r[3]["id"]] = max(got["round"], fold.round_cap.get(r[3]["id"], 0))
    setters = {}  # (id, field) → [行]
    subs = {}  # (id, 欄, 子項id) → [行]（逐項合併的欄，D-552）
    masks = []
    for _s, _by, n, row, fname, i in rows:
        op = row.get("op")
        if op == "mask":
            masks.append(row)
            continue
        rid = row.get("id")
        if not isinstance(rid, str):
            continue
        rec = st.records.setdefault(rid, {})
        snap, kept = dict(rec), None  # rec 的值只會被換掉、不會被就地改，淺的就夠
        try:
            kept = fold.keep(rid, row)  # 複本做不出來也算壞行（測試第 3 輪裁定 15）
            fold.apply(rec, rid, row, n)
        except Exception as e:  # 驗過還是套不上：整行不套，那筆與 fold 的暫存整份還原（測試裁定 1、測試第 2 輪裁定 12）
            if kept is not None:
                fold.restore(kept)
            rec.clear()
            rec.update(snap)
            if not rec:
                st.records.pop(rid, None)
            _log(vault, f"{fname} 第 {i + 1} 行略過：{type(e).__name__}")
            continue
        mine = st.seen.setdefault(rid, {})
        mine[str(row.get("by", ""))] = max(_seq(row), mine.get(str(row.get("by", "")), 0))
        for k in list(row.get("set") or {}) + list(row.get("unset") or []):
            if k not in fold.lists and k not in KEYED:  # 只往後加的欄、標籤不算撞到（D-337、D-553）
                setters.setdefault((rid, k), []).append(row)
        got = row.get("sub")
        for k, items in (got.items() if isinstance(got, dict) else ()):
            if k in fold.nested and k not in KEYED and isinstance(items, dict):
                for cid in items:
                    subs.setdefault((rid, k, str(cid)), []).append(row)
        st.meta[rid] = {"seq": max(_seq(row), st.meta.get(rid, {}).get("seq", 0)), "by": row.get("by"),
                        "who": row.get("who"), "at": row.get("at")}
    bigger = BIGGER.get(base(kind), ()) if kind else ()
    resolve = RESOLVE.get(base(kind), ()) if kind else ()
    for (rid, field), lines in setters.items():
        if field in bigger:  # 取大的欄（裁定 7）：跟最後一行彼此沒看過、值更大的行贏，不算撞到
            last, newest = lines[-1], {}
            for r in lines:  # 每台只看它自己最後設這一欄的那行（同一台後來蓋掉的值不算，例如自己退回）
                newest[r.get("by")] = r
            got = [_val(r, field) for r in newest.values() if r is not last and _apart(r, last)] + [_val(last, field)]
            nums = [v for v in got if isinstance(v, int) and not isinstance(v, bool)]
            if nums and rid in st.records and field in st.records[rid]:
                st.records[rid][field] = max(nums)
            continue
        if len(lines) < 2:
            continue
        lead = FOLLOW.get(base(kind), {}).get(field) if kind else None
        if lead:
            _follow(st.records.get(rid), field, lines, lead)
        found, last = [], {}
        for r in lines:  # 每台最後寫的值；兩台最後一樣就不算撞到（測試第 2 輪裁定 13）
            last[r.get("by")] = _val(r, field)
        for i, a in enumerate(lines):
            for b in lines[i + 1:]:
                if _val(a, field) != _val(b, field) and last[a.get("by")] != last[b.get("by")] and _apart(a, b) \
                        and not (lead and _moved_apart(a, b, lead, field)) \
                        and not (field in resolve and any(_saw(r, a) and _saw(r, b) for r in lines if r is not a and r is not b)):
                    for x in (a, b):
                        if x not in found:
                            found.append(x)
        if found:
            st.conflicts.append({"id": rid, "field": field, "lines": found})
    for (rid, k, cid), lines in subs.items():
        for f in SUB_CLASH:
            found = _sub_clash(lines, k, cid, f)
            if found:
                st.conflicts.append({"id": rid, "field": f"{k}:{cid}:{f}", "lines": found})
    for note in fold.notes:
        _log(vault, note)
    if kind and base(kind) == "agenda":
        _finish_agenda(vault, st)
    for m in masks:
        for rid in m.get("ids") or []:
            if rid in st.records:
                for f in m.get("fields") or []:
                    if f in st.records[rid]:
                        st.records[rid][f] = MASKED
    return st


def _lead(row, lead, field):
    """這行同時設的 field 與 round（自動移的那條路）的 round；沒設 field（含 unset）、round 沒設或不是 0 以上的整數回 None。"""
    got = row.get("set") or {}
    v = got.get(lead)
    return v if field in got and _nat(v) else None


def _moved_apart(a, b, lead, field):
    """兩行都是自動移、round 又不一樣：field 跟 round 大的那行，不算撞到（測試第 3 輪裁定 16）。"""
    ra, rb = _lead(a, lead, field), _lead(b, lead, field)
    return ra is not None and rb is not None and ra != rb


def _follow(rec, field, lines, lead):
    """最後一行是自動移的，而別台彼此沒看過的自動移行 round 更大：這一欄取那行的值（測試第 3 輪裁定 16）。"""
    last = lines[-1]
    if rec is None or field not in rec or _lead(last, lead, field) is None:
        return
    newest = {}
    for r in lines:
        newest[r.get("by")] = r
    won = [r for r in newest.values() if r is not last and _apart(r, last)
           and _lead(r, lead, field) is not None and _lead(r, lead, field) > _lead(last, lead, field)]
    if won:
        rec[field] = max(won, key=lambda r: _lead(r, lead, field))["set"][field]


def _cap_live(vault, rows, limit, what):
    """硬上限只算沒拿掉的：超過的（照原本順序的後面那些）不列；拿掉的照留。"""
    live = [r for r in rows if not (isinstance(r, dict) and r.get("del"))]
    if len(live) <= limit:
        return rows
    _log(vault, f"{what} {len(live)} 個超過 {limit}，多的不列")
    over = {id(r) for r in live[limit:]}
    return [r for r in rows if id(r) not in over]


def _finish_agenda(vault, st):
    """合起來之後：防亂寫的硬上限（多的不列）、讀出時拿掉控制字元（測試裁定 3、7）。"""
    cap = CAPS["agenda"]
    for rid, rec in st.records.items():
        subs = rec.get("checklist")
        if isinstance(subs, list):
            subs = rec["checklist"] = _cap_live(vault, subs, cap["checklist"], f"{rid} 子項")
            for e in subs:
                if not isinstance(e, dict):
                    continue
                e["text"] = clean_text(e.get("text"))
                cs = e.get("comments")
                if isinstance(cs, list):
                    cs = e["comments"] = _cap_live(vault, cs, cap["comments"], f"{rid} 子項 {e.get('id')} 留言")
                    for c in cs:
                        if isinstance(c, dict):
                            c["text"] = clean_text(c.get("text"))
        tags = rec.get("tags")
        if isinstance(tags, list):
            seen, out = set(), []
            for t in tags:
                t = clean_text(t)
                if isinstance(t, str) and t and t.casefold() not in seen:
                    seen.add(t.casefold())
                    out.append(t)
            if len(out) > cap["tags"]:
                _log(vault, f"{rid} 標籤 {len(out)} 個超過 {cap['tags']}，多的不列")
                out = out[:cap["tags"]]
            if out:
                rec["tags"] = out
            else:
                rec.pop("tags", None)
        if isinstance(rec.get("note"), str):
            rec["note"] = clean_text(rec["note"], keep_newline=True)


def _val(row, field):
    return (row.get("set") or {}).get(field) if field in (row.get("set") or {}) else ("__unset__",)


def conflicts_json(state):
    """kb <種類> show --json 的 conflicts。"""
    out = []
    for c in state.conflicts:
        out.append({"id": c["id"], "field": c["field"],
                    "values": [{"value": _field_val(x, c["field"]), "by": x.get("by"), "who": x.get("who"), "seq": x.get("seq")}
                               for x in c["lines"]]})
    return out


# ---------- 寫 ----------

def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _app():
    try:
        from kb._lib.paths import read_manifest
        return read_manifest()["version"]
    except Exception:  # noqa: BLE001
        return None


def diff(before, after, state=None, kind=None):
    """before／after：{id: {欄: 值}}；回要寫的行（還沒填 seq、base 以外的欄）[(op, id, set, unset, sub)]。
    逐項合併的欄（NESTED）不放進 set，比出改了的子項放 sub（D-552）。"""
    nest = nested(kind)
    lists = append_only(kind) if kind else ()

    def split(old, rec):
        force = set(rec.get("_force") or ())  # 撞到時值一樣也寫（D-553 註記）；_force 本身不寫
        s = {k: v for k, v in rec.items() if k != "_force" and k not in nest and (k not in old or old[k] != v or k in force)}
        un = [k for k in old if k not in rec and k not in nest]
        un += [k for k in force if k not in rec and k not in un and k not in nest]  # 撞到時定成「沒有」也寫一行
        for k in lists:  # 只往後加的欄：只寫這次新加的（合併本來就是聯集）；只少了的（退回 drop）寫空的
            if k in s and isinstance(s[k], list) and isinstance(old.get(k), list):
                was = {_ekey(e) for e in old[k]}
                s[k] = [e for e in s[k] if _ekey(e) not in was]
        sub = {}
        for k in nest:
            if k not in rec and k not in old:
                continue
            got = (_tag_diff if k in KEYED else _sub_diff)(old.get(k), rec.get(k))
            if got is None:  # 不是清單（例如遮掉的字）：照一般欄
                if k not in rec:
                    un.append(k)
                elif old.get(k) != rec[k]:
                    s[k] = rec[k]
            elif got:
                sub[k] = got
        return s, un, sub

    out = []
    for rid, rec in after.items():
        if rid not in before:
            cur = state.records.get(rid) if state is not None else None
            if cur and not cur.get("_del"):  # 這筆其實已經有了（例如封存那一邊的筆搬到主文件）：只寫差的欄
                s, un, sub = split({k: v for k, v in cur.items() if k != "_del"}, rec)
                if s or un or sub:
                    out.append(("set", rid, s, un, sub))
                continue
            s = {k: v for k, v in rec.items() if k != "_force"}
            if cur and cur.get("_del"):
                s["_del"] = False
            out.append(("create", rid, s, [], {}))
            continue
        s, un, sub = split(before[rid], rec)
        if s or un or sub:
            out.append(("set", rid, s, un, sub))
    for rid in before:
        if rid not in after:
            out.append(("set", rid, {"_del": True}, [], {}))
    return out


def write(vault, kind, before, after, turn_id=None, who=None, reverts=None, row_extra=None, via=None):
    """比出新增、刪掉、改了哪幾欄，每筆一行 append 進這台的檔。回寫了幾行。row_extra 是每行另外帶的欄（退回的 drop／undrop）。
    via：明白吸收副本寫的行（adopt）記從哪裡來（undo、restore），選填鍵，舊版照讀（#78 第二段第 2 點）。"""
    try:
        return _write(Path(vault), kind, before, after, turn_id, who, reverts, dict(row_extra or {}, **({"via": via} if via else {})))
    finally:
        forget(vault, kind)


def _write(vault, kind, before, after, turn_id, who, reverts, row_extra):
    path = own_file(vault, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    turn_id = turn_id or _TURN.get((str(vault), kind))
    check_writable(vault, kind)
    if who is None:  # 多人時填這台的人（多人共用 2）；單人、還沒記名字是 None
        try:
            from kb._lib import people
            who = people.me(vault)
        except Exception:  # noqa: BLE001  拿不到人名不能讓寫入失敗
            who = None
    with FileLock(path):
        _scrub_own(vault, kind, path)
        state = load(vault, kind)
        changes = diff(before, after, state, kind)
        if not changes and not hard_delete:
            return 0
        by = path.stem
        extra = []
        if hard_delete:  # purge：動到的筆，這台檔裡的舊行拿掉，現在的樣子整筆換成一行（留在原來的位置，順序不變）
            replace = {}
            for _op, rid, *_rest in changes:
                if rid not in after:
                    continue
                prev = state.meta.get(rid, {}).get("seq", 0)
                alone = all(r.get("by") == by for r in _history(vault, kind, rid))
                seq = 1 if alone else prev + 1
                replace[rid] = {"v": FORMAT_VERSION, "op": "create" if seq == 1 else "set", "id": rid, "seq": seq,
                                "base": 0 if seq == 1 else prev, "by": by, "who": who, "at": _now(), "turn": turn_id,
                                "set": _without_gone(kind, after[rid]), "seen": dict(state.seen.get(rid, {})),
                                **({"app": _app()} if _app() else {})}
            drop = {c[1] for c in changes if c[1] not in after} | {r for r, v in state.records.items() if v.get("_del")}
            extra = _rewrite_own(vault, path, replace, drop)
            changes = [c for c in changes if c[1] not in replace]
            noted_extra = [{"kind": kind, "id": r["id"], "seq": r["seq"]} for r in replace.values()]
        else:
            noted_extra = []
        lines, noted = [], []
        for op, rid, s, un, sub in changes:
            prev = state.meta.get(rid, {}).get("seq", 0)
            row = {"v": FORMAT_VERSION, "op": op, "id": rid, "seq": prev + 1, "base": prev, "by": by, "who": who,
                   "at": _now(), "turn": turn_id, "set": s, "seen": dict(state.seen.get(rid, {}))}
            if un:
                row["unset"] = un
            if sub:
                row["sub"] = sub
            if reverts:
                row["reverts"] = reverts
            if row_extra:
                row.update(row_extra)
            app = _app()
            if app:
                row["app"] = app
            lines.append(json.dumps(row, ensure_ascii=False) + "\n")
            noted.append({"kind": kind, "id": rid, "seq": prev + 1})
        noted += noted_extra
        _append(path, lines)
        big = path.stat().st_size > COMPACT_BYTES
    if big:
        compact(vault, kind)
    load(vault, kind)  # 順便更新衍生副本
    if turn_id:
        _note_turn_lines(vault, turn_id, noted)
    return len(lines)


def _rewrite_own(vault, path, replace, drop):
    """這台的檔：drop 的筆整筆拿掉；replace 的筆舊行拿掉、第一行的位置換成新的一行（檔裡沒有就回傳要附加的）。"""
    from kb._lib.io import write_text_atomic
    rows = [r for _i, r in _read_file(vault, path)] if path.is_file() else []
    out, placed = [], set()
    for r in rows:
        rid = r.get("id")
        if r.get("op") == "mask" or (rid not in drop and rid not in replace):
            out.append(r)
        elif rid in replace and rid not in placed:
            out.append(replace[rid])
            placed.add(rid)
    for rid, r in replace.items():
        if rid not in placed:
            out.append(r)
    write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    return []


def _append(path, lines):
    lead = ""
    if path.is_file() and path.stat().st_size > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lead = "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if lead:
            f.write(lead)
        for ln in lines:
            f.write(ln)
        f.flush()
        os.fsync(f.fileno())


def _note_turn_lines(vault, turn_id, items):
    try:
        from kb._lib import turn
        vdir = turn.turn_dir(vault, turn_id)
        with FileLock(vdir / "streams.jsonl"):
            with open(vdir / "streams.jsonl", "a", encoding="utf-8", newline="\n") as f:
                for it in items:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
    except OSError:
        pass


def turn_items(vault, turn_id):
    """回合夾的 streams.jsonl：[{kind, id, seq}]。"""
    p = Path(vault) / ".system" / "versions" / turn_id / "streams.jsonl"
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


def write_mask(vault, kind, ids, fields, turn_id=None):
    """purge：寫一行「這幾筆遮掉」；自己檔裡的原文下次寫入前換掉（也可以馬上叫 scrub）。"""
    path = own_file(vault, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path):
        row = {"v": FORMAT_VERSION, "op": "mask", "ids": list(ids), "fields": list(fields), "seq": 0, "by": path.stem,
               "at": _now(), "turn": turn_id}
        try:
            _append(path, [json.dumps(row, ensure_ascii=False) + "\n"])
            _scrub_own(vault, kind, path)
        finally:
            forget(vault, kind)


def scrub(vault, kind):
    path = own_file(vault, kind)
    with FileLock(path):
        try:
            _scrub_own(vault, kind, path)
        finally:
            forget(vault, kind)


def _scrub_own(vault, kind, path):
    """有標記蓋到自己檔裡的行：重寫自己的檔，原文換成遮掉的字。"""
    masks = []
    for f in _files(vault, kind):
        for _i, row in _read_file(vault, f):
            if row.get("op") == "mask":
                masks.append(row)
    if not masks or not path.is_file():
        return
    want = {}
    for m in masks:
        for rid in m.get("ids") or []:
            want.setdefault(rid, set()).update(m.get("fields") or [])
    rows, dirty = [], False
    for _i, row in _read_file(vault, path):
        if row.get("op") != "mask" and row.get("id") in want:
            s = row.get("set") or {}
            for f in want[row["id"]]:
                if f in s and s[f] != MASKED:
                    s[f] = MASKED
                    dirty = True
                got = (row.get("sub") or {}).get(f) if f in nested(kind) and isinstance(row.get("sub"), dict) else None
                for ch in (got.values() if isinstance(got, dict) else ()):  # 子項的字、留言的字也遮（D-552）
                    if not isinstance(ch, dict):
                        continue
                    for c in [ch] + [c for c in ch.get("comments_add") or [] if isinstance(c, dict)]:
                        if "text" in c and c["text"] != MASKED:
                            c["text"] = MASKED
                            dirty = True
        rows.append(row)
    if dirty:
        write_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        from kb._lib.io import write_text_atomic
        write_text_atomic(path, write_text)


def compact(vault, kind):
    """只重寫這台自己的檔：被同一台後面的行完全蓋過的舊 set 行併掉（create 與 mask 留著）。回併掉幾行。"""
    path = own_file(vault, kind)
    if not path.is_file():
        return 0
    with FileLock(path):
        rows = [r for _i, r in _read_file(vault, path)]
        seen, keep, lists = set(), [], append_only(kind)
        for row in reversed(rows):
            if row.get("op") != "set":
                keep.append(row)
                continue
            got = row.get("set") if isinstance(row.get("set"), dict) else {}
            keys = {(row.get("id"), k) for k in list(got) + list(row.get("unset") or []) if k not in lists}
            carries = any(got.get(k) for k in lists) or row.get("drop") or row.get("undrop")  # 只往後加的欄不算被蓋過（測試裁定 5）
            if keys and keys <= seen and not row.get("reverts") and not row.get("sub") and not carries:  # 帶子項改動的行不併（D-552）
                continue
            seen |= keys
            keep.append(row)
        keep.reverse()
        dropped = len(rows) - len(keep)
        if dropped:
            from kb._lib.io import write_text_atomic
            write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
            forget(vault, kind)
        return dropped


# ---------- 整份文件 ↔ 筆 ----------

def pref_id(text, taken):
    base = "pf-" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]
    rid, n = base, 1
    while rid in taken:
        n += 1
        rid = f"{base}-{n}"
    return rid


def doc_to_records(kind, doc):
    view = kind in VIEWS
    _rel, shape, key, _r = KINDS[base(kind)]
    recs = {}
    if shape == "profile":
        recs[PROFILE_ID] = {k: v for k, v in doc.items() if k != "preferences"}
        if "preferences" in doc:
            recs[PROFILE_ID]["_has_prefs"] = True  # 原本有這一欄（空的也算），讀回來才一樣
        for p in doc.get("preferences") or []:
            recs[pref_id(p.get("text"), recs)] = dict(p)
        return recs
    meta = {k: v for k, v in doc.items() if k != key}
    if meta and not view:
        recs[META_ID] = meta
    body = doc.get(key)
    if shape == "list":
        for i, it in enumerate(body or []):
            rid = str(it.get("id")) if it.get("id") not in (None, "") else f"_n{i}"
            recs[rid] = dict(it)
    else:
        for k, v in (body or {}).items():
            recs[str(k)] = v if isinstance(v, dict) else {"_value": v}
    return recs


def records_to_doc(kind, recs):
    view = kind in VIEWS
    _rel, shape, key, _r = KINDS[base(kind)]
    live = {k: {f: x for f, x in v.items() if f != "_del"} for k, v in recs.items() if not v.get("_del")}
    if view:
        live = {k: v for k, v in live.items() if _is_archived(v)}
        return {"schema_version": 1, key: [{**v, **({"id": k} if "id" not in v else {})} for k, v in live.items()]}
    if kind in HIDE_ARCHIVED:
        live = {k: v for k, v in live.items() if not _is_archived(v)}
    if shape == "profile":
        doc = dict(live.get(PROFILE_ID) or {})
        prefs = [v for k, v in live.items() if k != PROFILE_ID]
        if prefs or doc.pop("_has_prefs", False):
            doc["preferences"] = prefs
        doc.pop("_has_prefs", None)
        return doc
    doc = dict(live.get(META_ID) or {})
    rest = [(k, v) for k, v in live.items() if k != META_ID]
    if shape == "list":
        doc[key] = [{**v, **({"id": k} if "id" not in v and not k.startswith("_n") else {})} for k, v in rest]
    else:
        doc[key] = {k: (v["_value"] if set(v) == {"_value"} else v) for k, v in rest}
    return doc


def read_doc(vault, kind):
    """流水檔合起來的整份文件；沒有流水檔回 None（呼叫端改讀舊檔）。
    同一個 process 內記住：靠的檔（memo_sig）沒變就回記住那份的複本，不重讀（D-548 (3)）。
    複本用 marshal 快照還原（同 deepcopy 的效果，1 萬筆清冊 0.13 秒，deepcopy 要 0.76 秒）。"""
    key = (_vkey(vault), kind)
    sig = memo_sig(vault, kind)
    hit = _MEMO.get(key)
    if hit is not None and hit[0] == sig:
        return marshal.loads(hit[1])
    _MEMO.pop(key, None)
    doc = _read_doc(vault, kind)
    if doc is not None:
        after = memo_sig(vault, kind)
        if after[:-2] == sig[:-2]:  # 讀的時候只重寫了副本與記號才記；流水檔、本機那一半中途變了（吸收外部改動、別的行程寫）下次重讀
            _MEMO[key] = (after, marshal.dumps(doc))
    return doc


def _read_doc(vault, kind):
    if not exists(vault, kind):
        return None
    st = load(vault, kind)
    joined = joined_doc(vault, kind, st.records)
    doc = _mirror_edited(vault, kind, joined)
    legacy = Path(vault) / legacy_rel(kind)
    if doc is None:
        try:
            if not legacy.is_file():
                _mirror(vault, kind, st)  # 副本被刪：重寫
            elif _sha(legacy.read_bytes()) != _mirror_mark(vault, kind).read_text(encoding="utf-8").strip():
                _mirror(vault, kind, st)  # 副本壞了或不是物件：從流水檔重寫
        except OSError:
            pass
    elif doc == joined:  # 內容一樣、只是排法不同（退回換回的舊檔）：副本留原樣的位元組，只換記號
        _mark_as_is(vault, kind, legacy)
    else:  # 副本被外面換過（git 同步、還原、手改）：不吸收（D-554 (1)），換掉的那份存一份、照流水檔重寫
        st = load(vault, kind)  # 別的行程剛寫完（流水檔、副本都是新的，這裡拿到的是舊的）不算換過：重讀一次再比（review 3）
        joined = joined_doc(vault, kind, st.records)
        if doc == joined:
            _mark_as_is(vault, kind, legacy)
        else:
            _keep_swapped(vault, legacy)
            _mirror(vault, kind, st)
    return joined


HANDEDITS_REL = ".system/handedits"
KEEP_SWAPPED = 20  # 同名只留最近幾份
SWAPPED_LINE = "{name} 被外面換過（git 同步、還原或手改），已照流水檔重寫；換掉的那份存在 {saved}"


def _mark_as_is(vault, kind, legacy):
    try:
        from kb._lib.io import write_text_atomic
        write_text_atomic(_mirror_mark(vault, kind), _sha(legacy.read_bytes()))
    except OSError:
        pass


def _keep_swapped(vault, legacy):
    """換掉的副本存到 .system/handedits/<時間>-<檔名>（照 renders.keep：先遮值；同名加 -2…），同名只留最近 KEEP_SWAPPED 份，記 log。
    存不了只記 log：副本是衍生的，照樣從流水檔重寫（正本是流水檔，沒有東西會丟）。"""
    from datetime import datetime
    name = legacy.name
    try:
        from kb._lib import renders
        from kb._lib.io import write_text_atomic
        text = renders.masked(vault, legacy.read_bytes().decode("utf-8", errors="replace"))
        folder = Path(vault) / HANDEDITS_REL
        folder.mkdir(parents=True, exist_ok=True)
        had = sorted((p for p in folder.glob(f"*-{name}") if p.is_file()), key=lambda p: (p.stat().st_mtime_ns, p.name))
        if had and had[-1].read_text(encoding="utf-8") == text:  # 副本重寫不成（被占用）時同一份不重複存、不洗 log（review 2）
            return f"{HANDEDITS_REL}/{had[-1].name}"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target, n = folder / f"{stamp}-{name}", 1
        while target.exists():
            n += 1
            target = folder / f"{stamp}-{n}-{name}"
        write_text_atomic(target, text)
        same = sorted((p for p in folder.glob(f"*-{name}") if p.is_file()), key=lambda p: (p.stat().st_mtime_ns, p.name))
        for old in same[:-KEEP_SWAPPED]:
            try:
                old.unlink()
            except OSError:
                pass
        _log(vault, SWAPPED_LINE.format(name=name, saved=f"{HANDEDITS_REL}/{target.name}"))
        return f"{HANDEDITS_REL}/{target.name}"
    except Exception as e:  # noqa: BLE001
        _log(vault, f"{name} 被外面換過，已照流水檔重寫；換掉的那份沒存成：{type(e).__name__}: {str(e)[:100]}")
        return None


def keep_copy(vault, kind):
    """吸收不成的時候（#78 第二輪裁定 6）：現在這份副本存到 handedits、副本照流水檔重寫；回存到哪（相對知識庫，沒存成回 None）。"""
    vault, kind = Path(vault), base(kind)
    legacy = vault / legacy_rel(kind)
    saved = _keep_swapped(vault, legacy) if legacy.is_file() else None
    try:
        forget(vault, kind)
        _mirror(vault, kind, load(vault, kind))
    except Exception as e:  # noqa: BLE001  重寫不成：拿掉「看過」的記號，下次讀照「外面換過」的路再試（review）
        try:
            _mirror_mark(vault, kind).unlink()
        except OSError:
            pass
        _log(vault, f"{legacy.name} 沒照流水檔重寫成：{type(e).__name__}: {str(e)[:100]}")
    _redraw(vault, kind)  # 退回可能把頁面也換回舊的：照流水檔重畫（#15；記號已被 adopt 標成看過，_mirror 不會自己畫）
    return saved


def adopt(vault, kind, via, absent_empty=False):
    """明白吸收現在的副本（willy 自己把舊的整份換回原位之後：退回 via="undo"、還原 via="restore"）：
    就是舊的吸收（write_doc），寫的每行帶 via（D-554 (2)）。副本沒有、壞了、不是物件回 0；回寫了幾行。
    吸收後內容跟副本一樣：副本留原樣的位元組（換回的舊檔不被重排）。
    absent_empty（倒回，第二輪裁定 7）：副本不在、流水檔在 → 那種原本是空的，吸收成空的（活著的筆全部照「不在整份裡」拿掉），副本照吸收後的流水檔畫出來。
    只給「記的時候沒副本也沒流水檔」的倒回用（entities._Undo.was_empty）：副本是衍生的，光看它不在不能當成空的。"""
    vault = Path(vault)
    legacy = vault / legacy_rel(kind)
    if absent_empty and not legacy.is_file():
        if not exists(vault, kind):
            return 0
        try:
            return _write_doc(vault, kind, {}, via=via)
        finally:
            forget(vault, kind)
    try:
        raw = legacy.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(doc, dict):
        return 0
    if exists(vault, kind):  # 先把這份記成看過的：吸收途中有人讀這種類（寫行要查這台是誰 → 讀 profile）不會把它當成外面換過、存一份又重寫
        _mark_as_is(vault, kind, legacy)
    try:
        n = _write_doc(vault, kind, doc, via=via)
    finally:
        forget(vault, kind)
    st = load(vault, kind)
    if joined_doc(vault, kind, st.records) == doc:
        try:
            legacy.write_bytes(raw)
            _mark_as_is(vault, kind, legacy)
        except OSError:
            pass
    else:  # 吸收後跟副本不完全一樣（只記在這台的那一半、合併規則）：副本照流水檔重寫，免得下次讀被當成外面換過
        _mirror(vault, kind, st)
    return n


def adopt_path(vault, path, via, absent_empty=False):
    """path 是流水檔種類的副本（.system/<種類>.json）就 adopt，不是就什麼都不做；回寫了幾行。
    willy 自己把舊的整份寫回原位的地方（退回、做到一半失敗倒回）寫完叫這個。"""
    v = virtual_kind(path)
    if v is None or Path(v[0]).resolve() != Path(vault).resolve():
        return 0
    return adopt(vault, v[1], via, absent_empty=absent_empty)


def write_doc(vault, kind, data):
    """整份交回：只有舊檔沒有流水檔時先把舊檔搬成這台的流水檔，再比出改動寫進去。"""
    try:
        _write_doc(Path(vault), kind, data)
    finally:
        forget(vault, kind)


def _write_doc(vault, kind, data, via=None):
    if not exists(vault, kind):
        migrate_kind(vault, kind)
    if kind in SPLIT:
        data, local = _split(kind, data)
        _write_local(vault, kind, local)
    st = load(vault, kind)
    live = {k: dict(v) for k, v in st.records.items() if not v.get("_del")}
    if kind in VIEWS:
        before = {k: v for k, v in live.items() if _is_archived(v)}
    elif kind in HIDE_ARCHIVED:
        before = {k: v for k, v in live.items() if not _is_archived(v)}
    else:
        before = live
    if kind == "profile" and "profile" in before and data.get("md_sha256"):  # md 指紋只記在這台那一半（#42）：共用那份照舊，單純重畫不寫共用流水檔；清成 None（沒有 md）照常寫
        data = dict(data)
        if "md_sha256" in before["profile"]:
            data["md_sha256"] = before["profile"]["md_sha256"]
        else:
            data.pop("md_sha256", None)
    after = doc_to_records(kind, data)
    changed = write(vault, base(kind), before, after, via=via)
    if kind in SPLIT and not changed:
        st = load(vault, kind)
        _mirror(vault, kind, st)  # 只有本機那一半變了：副本也要跟著換
    return changed


def migrate_kind(vault, kind, keep_legacy_dir=None):
    """舊的整份 JSON → 這台的流水檔（每筆一行 create、seq 1、base 0）；舊檔搬到 .system/legacy/。回轉了幾筆（沒舊檔回 0）。"""
    vault = Path(vault)
    kind = base(kind)
    legacy = vault / KINDS[kind][0]
    arch = [(v, vault / legacy_rel(v)) for v, (b, _r) in VIEWS.items() if b == kind]
    if exists(vault, kind) or not (legacy.is_file() or any(p.is_file() for _v, p in arch)):
        return 0
    recs, moved = {}, []
    for k, path in [(kind, legacy)] + arch:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("不是物件")
        except ValueError:  # 舊檔壞了：整份寫回的人（模組）本來就是要覆蓋它；壞的那份留在 legacy/，從空的開始
            dest = vault / LEGACY_REL / (path.name + ".broken")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, dest)
            continue
        try:
            if k in SPLIT:
                doc, local = _split(k, doc)
                _write_local(vault, k, local)
            for rid, rec in doc_to_records(k, doc).items():
                recs.setdefault(rid, rec)
        except (AttributeError, TypeError):  # 形狀不對的舊檔不動，留給各模組照舊報錯
            return 0
        moved.append(path)
    if not moved:
        return 0
    path = own_file(vault, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    by = path.stem
    app = _app()
    lines = []
    for rid, rec in recs.items():
        row = {"v": FORMAT_VERSION, "op": "create", "id": rid, "seq": 1, "base": 0, "by": by, "who": None,
               "at": _now(), "turn": None, "set": rec}
        if app:
            row["app"] = app
        lines.append(json.dumps(row, ensure_ascii=False) + "\n")
    with FileLock(path):
        _append(path, lines)
    for path in moved:
        dest = vault / (keep_legacy_dir or LEGACY_REL) / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, dest)
        except OSError:
            path.unlink()
    forget(vault, kind)
    load(vault, kind)  # 順便把衍生副本寫回舊路徑
    return len(recs)


def legacy_version_dir(version):
    return f"{LEGACY_REL}/{version}"


# ---------- 退回用：一行的效果、之後的行 ----------

def _history(vault, kind, rid):
    rows = []
    for f in _files(vault, kind):
        for _i, row in _good_rows(vault, f, kind):
            if row.get("op") != "mask" and row.get("id") == rid:
                rows.append((_seq(row), str(row.get("by", "")), len(rows), row))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


def line_effects(vault, kind, rid, seq, by):
    """一行改了什麼：{"op", "old"（改之前這筆的欄，沒有是 None）, "new", "fields", "row"}；找不到回 None。
    old／new 照合併的規則算（只往後加的欄是到那一行為止的聯集，D-337）。"""
    rec = None
    fold = _Fold(base(kind))
    for n, row in enumerate(_history(vault, kind, rid)):
        if row.get("seq") == seq and row.get("by") == by:
            old = None if rec is None else dict(rec)
            new = fold.apply(dict(rec or {}), rid, row, n)
            fields = list((row.get("set") or {}).keys()) + list(row.get("unset") or []) + _sub_fields(row)
            return {"op": row.get("op"), "old": old, "new": new, "fields": fields, "row": row}
        rec = fold.apply({} if rec is None else rec, rid, row, n)
    return None


def later_lines(vault, kind, rid, seq, fields, skip_turns=()):
    """比 seq 大、設了 fields 其中一欄的行（skip_turns 的回合寫的不算）。只往後加的欄不算（D-337：別人加了歷程不是改了同一欄）。
    子項的欄（「欄:子項id:那一欄」，D-552）看行的 sub；留言照 D-337 不算；整份清單的行算改了那欄全部子項。"""
    fields = {f for f in set(fields) - set(append_only(kind)) if not f.endswith(":comments")}
    out = []
    for row in _history(vault, kind, rid):
        if _seq(row) <= seq or row.get("turn") in skip_turns:
            continue
        whole = set(row.get("set") or {}) | set(row.get("unset") or [])
        if fields & (whole | set(_sub_fields(row))) or any(":" in f and f.partition(":")[0] in whole for f in fields):
            out.append(row)
    return out


def _undo_list(old, new, cur):
    """退回一行時只往後加的欄（D-337）：那行加的元素拿掉（drop）、那行拿掉的放回（undrop，退回一次退回）；別人加的照留。
    old／new 是那行之前、之後的清單，cur 是現在的。回 (新清單, drop, undrop)。"""
    old = old if isinstance(old, list) else []
    new = new if isinstance(new, list) else []
    cur = cur if isinstance(cur, list) else []
    was, now = {_ekey(e) for e in old}, {_ekey(e) for e in new}
    drop = [e for e in new if _ekey(e) not in was]
    undrop = [e for e in old if _ekey(e) not in now]
    gone = {_ekey(e) for e in drop}
    out = [e for e in cur if _ekey(e) not in gone]
    have = {_ekey(e) for e in out}
    return out + [e for e in undrop if _ekey(e) not in have], drop, undrop


def current(vault, kind, rid):
    return load(vault, kind).records.get(rid)


def revert_line(vault, kind, rid, effects, turn_id, who=None):
    """改回一行：寫反向的一行，reverts 記被改回的 {kind, id, seq}。這行是新增就寫收回欄（沒有收回欄的種類寫 _del）。"""
    cur = current(vault, kind, rid)
    if cur is None:
        return False
    row = effects["row"]
    ref = [{"kind": kind, "id": rid, "seq": row.get("seq"), "by": row.get("by")}]
    new = dict(cur)
    lists, subs = {}, {}
    if effects["op"] == "create" or effects["old"] is None:
        new.update(RETRACT.get(kind) or {"_del": True})
    else:
        for f in effects["fields"]:
            head = f.partition(":")[0]
            if ":" in f and head in nested(kind):  # 子項的欄：只改那行動到的子項（D-552）
                subs.setdefault(head, []).append(f)
            elif f in append_only(kind):  # 只拿掉那次加的那幾行，別人加的留著（D-337）
                new[f], drop, undrop = _undo_list(effects["old"].get(f), (effects["new"] or {}).get(f), cur.get(f))
                if drop:
                    lists.setdefault("drop", {})[f] = drop
                if undrop:
                    lists.setdefault("undrop", {})[f] = undrop
            elif f in effects["old"]:
                new[f] = effects["old"][f]
            else:
                new.pop(f, None)
        for k, names in subs.items():
            got = _undo_tags(effects["old"].get(k), cur.get(k), names) if k in KEYED else \
                _undo_subs(effects["old"].get(k), (effects["new"] or {}).get(k), cur.get(k), names)
            if got or k not in KEYED:
                new[k] = got
            else:
                new.pop(k, None)
    write(vault, kind, {rid: cur}, {rid: new}, turn_id=turn_id, who=who, reverts=ref, row_extra=lists)
    return True


# ---------- 被改回舊值的改動（#78 第三段，D-554 (4)） ----------

ABSENT = None  # 那一欄沒有值（JSON 給 null）
_SUB_WATCH = ("text", "done", "del", "on")  # 子項、標籤看的欄；位置、留言不算


def _line_values(row, skip):
    """一行設了哪些欄 [(欄名, 值)]：一般欄、unset（值是 ABSENT）、子項與標籤的欄（「欄:id:那一欄」）。只往後加的欄不算。"""
    out = [(k, v) for k, v in (row.get("set") or {}).items() if k not in skip]
    out += [(k, ABSENT) for k in (row.get("unset") or []) if k not in skip]
    got = row.get("sub")
    for k, items in (got.items() if isinstance(got, dict) else ()):
        for cid, ch in (items.items() if isinstance(items, dict) else ()):
            for f, v in (ch.items() if isinstance(ch, dict) else ()):
                if f in _SUB_WATCH and not (k in KEYED and f != "on"):  # 標籤只看有沒有
                    if f in ("done", "del") and isinstance(v, dict):  # 勾、拿掉是一組（誰、什麼時候）：只比真假
                        v = v.get(f)
                    out.append((f"{k}:{_tkey(cid) if k in KEYED else cid}:{f}", bool(v) if f != "text" else v))
    return out


def _reverts_ok(v):
    """reverts 要是清單、每項是 {seq: 整數, by: 字串, …}；None 當沒有。"""
    if v is None:
        return True
    return isinstance(v, list) and all(isinstance(x, dict) and isinstance(x.get("seq"), int) and not isinstance(x.get("seq"), bool)
                                       and isinstance(x.get("by"), str) for x in v)


def _done_round(row, field):
    """這行對子項勾那一欄（「欄:id:done」）寫的 round；不是勾的欄回 None；勾不是一組（合併不認）回 -1。"""
    head, _, rest = field.partition(":")
    cid, _, f = rest.rpartition(":")
    if f != "done" or head in KEYED:
        return None
    got = row.get("sub")
    ch = ((got.get(head) or {}).get(cid) if isinstance(got, dict) and isinstance(got.get(head), dict) else None) or {}
    g = ch.get("done") if isinstance(ch, dict) else None
    return _round(g) if isinstance(g, dict) else -1


def _done_round_in(rec, field):
    """這筆現在那個子項勾的 round（沒有就 0）。"""
    head, _, rest = field.partition(":")
    cid = rest.rpartition(":")[0]
    items = (rec or {}).get(head)
    it = next((i for i in items if isinstance(i, dict) and str(i.get("id")) == cid), None) if isinstance(items, list) else None
    r = (it or {}).get("done_round", 0)
    return r if isinstance(r, int) and not isinstance(r, bool) else 0


def _value_in(rec, field):
    """這筆現在那一欄的值（子項、標籤的欄從整份清單找）；沒有回 ABSENT。"""
    head, _, rest = field.partition(":")
    if not rest:
        return (rec or {}).get(field, ABSENT)
    cid, _, f = rest.rpartition(":")
    items = (rec or {}).get(head)
    items = items if isinstance(items, list) else []
    if head in KEYED:
        return any(isinstance(t, str) and _tkey(t) == _tkey(cid) for t in items) if f == "on" else ABSENT
    it = next((i for i in items if isinstance(i, dict) and str(i.get("id")) == cid), None)
    if it is None:
        return ABSENT
    if f == "del":
        return bool(it.get("del"))
    return bool(it.get(f)) if f == "done" else it.get(f, ABSENT)


def _sub_title(rec, field):
    head, _, rest = field.partition(":")
    cid = rest.rpartition(":")[0]
    for i in ((rec or {}).get(head) or []) if rest and head not in KEYED else ():
        if isinstance(i, dict) and str(i.get("id")) == cid:
            return i.get("text")
    return None


def _who_of(row):
    return {"by": row.get("by"), "who": row.get("who"), "seq": row.get("seq"), "at": row.get("at")}


def overwritten(vault, kind):
    """把某一欄設回以前的值、蓋掉別台寫的值的行（D-554 (4)）：照合併的順序走每一筆、每一欄。列出的條件：
    (a) 這行把那欄設回這筆那欄以前出現過的值；(b) 被蓋掉的那個值是別台寫的、這行看過它（沒看過是撞到，另外列）；
    (c) 這行不是退回（沒有 reverts）、不是 willy 明白吸收的行（帶 via：退回、還原、倒回，第二輪裁定 3）、不是解掉撞到的行。
    只往後加的欄不算；已經被退回的行不列。
    不靠 seen 判斷是不是吸收（吸收行也看過別台的行）。回 [{kind, id, field, line, overwritten, back_to, code, title, sub}]，照行的時間排。"""
    kind = base(kind)
    skip = set(append_only(kind))
    groups = {}
    for f in _files(vault, kind):
        for _i, row in _good_rows(vault, f, kind):
            rid = row.get("id")
            if "reverts" in row and not _reverts_ok(row["reverts"]):  # 型別怪的退回行整行略過（測試第 2 輪 #17，照壞行的規矩）
                _log(vault, f"{f.name} 一行的 reverts 格式不對，列被改回的改動時略過（{rid}，seq {row.get('seq')}）")
                continue
            if row.get("op") != "mask" and isinstance(rid, str) and rid != META_ID:
                rows = groups.setdefault(rid, [])
                rows.append((_seq(row), str(row.get("by", "")), len(rows), row))
    out = []
    for rid, rows in groups.items():
        rows = [r[3] for r in sorted(rows, key=lambda r: r[:3])]
        undone = {(r.get("seq"), r.get("by")) for row in rows for r in (row.get("reverts") or [])
                  if isinstance(r, dict) and r.get("id") == rid}
        fold, rec, hist = _Fold(kind), None, {}
        cap = max([g["round"] for g in (row.get("set") or {} for row in rows) if _nat(g.get("round"))] or [0])
        fold.round_cap = {rid: cap}  # 跟合併一樣：勾的回合比這筆設過的都大當壞值（rec 才跟實際合併一致）
        for n, row in enumerate(rows):
            for k in (row.get("set") or {}):  # 整份清單重寫（建立、遷移）：子項、標籤各欄的歷史從這裡重算（review 4）
                if k in nested(kind):
                    for name in [x for x in hist if x.startswith(k + ":")]:
                        del hist[name]
            for field, value in _line_values(row, skip):
                h = hist.get(field)
                if h is None:
                    h = hist[field] = [] if rec is None else [(_value_in(rec, field), rows[0])]
                r = _done_round(row, field)
                if r is not None:  # 子項的勾照 #77 算 round（測試第 2 輪 #16）：跟合併一樣，每行從套到前一行的 rec 讀現在的回合
                    cur = _done_round_in(rec, field)
                    if r < 0 or r < cur or r > cap:  # 勾不是一組、round 輸、round 超過這筆設過的：合併沒套上，不列、不算進歷史
                        continue
                    if r > cur:  # 換輪：新一輪從沒勾起算；重置勾本身不算蓋掉
                        hist[field] = [(False, {"by": "", "seq": 0, "seen": {}}), (value, row)]  # 起點是誰都看過的空佔位
                        continue
                if h:
                    prev, prow = h[-1]
                    setters = [r for _v, r in h]
                    back = value != prev and any(v == value for v, _r in h[:-1])
                    # 解掉撞到的行：第一個看過撞到的兩邊又設這一欄的那行；之後的行不算（review 1）
                    solved = any(_apart(a, b) and va != vb and _saw(row, a) and _saw(row, b)
                                 and not any(r is not a and r is not b and _saw(r, a) and _saw(r, b) for _v, r in h)
                                 for i, (va, a) in enumerate(h) for vb, b in h[i + 1:])
                    hit = (back and prow.get("by") != row.get("by") and not row.get("reverts") and not row.get("via")
                           and _saw(row, prow) and not solved)
                    if hit and (row.get("seq"), row.get("by")) not in undone and setters:
                        out.append({"kind": kind, "id": rid, "field": field,
                                    "line": dict(_who_of(row), turn=row.get("turn"), via=row.get("via")),
                                    "overwritten": dict(_who_of(prow), value=prev), "back_to": value,
                                    "code": f"{kind}:{rid}:{row.get('by')}:{row.get('seq')}",
                                    "title": None, "sub": _sub_title(rec, field)})
                h.append((value, row))
            try:
                rec = fold.apply({} if rec is None else rec, rid, row, n)
            except Exception:  # noqa: BLE001  套不上的行照合併的做法略過
                pass
        for it in out:
            if it["id"] == rid and it["title"] is None:
                it["title"] = next((str(rec[k]) for k in ("text", "name", "alias", "title") if rec and isinstance(rec.get(k), str) and rec[k]), rid)
    out.sort(key=lambda it: (str(it["line"]["at"] or ""), it["id"], it["field"]))
    return out


def conflicts_of(vault, kind):
    """kb <種類> list／show --json 的 conflicts：沒有流水檔或讀不了就是空清單。"""
    try:
        if not exists(vault, kind):
            return []
        return conflicts_json(load(vault, kind))
    except (OSError, ValueError):
        return []


def all_lines(vault, kind):
    """這種類全部的行（照檔、照檔內順序）；purge 找「曾經在那夾」的筆用。"""
    out = []
    for f in _files(vault, base(kind)):
        out += [r for _i, r in _read_file(vault, f)]
    return out


def purge_tombstones(vault, kind):
    """purge：這台自己的檔收成每筆一行現在的樣子（舊行裡的原文不留，位置與順序不變）；已經刪掉的筆整筆拿掉。
    別台的檔不動（只有同步改得動）。回收掉幾筆。"""
    kind = base(kind)
    path = own_file(vault, kind)
    if not path.is_file():
        return 0
    with FileLock(path):
        st = load(vault, kind)
        by = path.stem
        own_ids = {r.get("id") for _i, r in _read_file(vault, path) if r.get("op") != "mask"}
        dead = {rid for rid in own_ids if st.records.get(rid, {}).get("_del")}
        replace = {}
        for rid in own_ids - dead:
            rec = st.records.get(rid)
            if rec is None:
                continue
            prev = st.meta.get(rid, {}).get("seq", 0)
            alone = all(r.get("by") == by for r in _history(vault, kind, rid))
            seq = 1 if alone else prev
            replace[rid] = {"v": FORMAT_VERSION, "op": "create" if alone else "set", "id": rid, "seq": seq,
                            "base": 0 if alone else max(prev - 1, 0), "by": by, "who": None, "at": _now(), "turn": None,
                            "set": _without_gone(kind, rec), "seen": {} if alone else dict(st.seen.get(rid, {})),
                            **({"app": _app()} if _app() else {})}
        try:
            _rewrite_own(vault, path, replace, dead)
        finally:
            forget(vault, kind)
        return len(own_ids)
