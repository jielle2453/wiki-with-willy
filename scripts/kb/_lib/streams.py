"""共用流水檔（多人共用 1，D-288）：每台只寫自己的 .system/streams/<種類>/<這台代號>.jsonl，一行一筆改動，讀的時候合起來。

行：{"v","op":"create|set|mask","id","seq","base","by","who","at","turn","set":{欄:值},"unset":[欄],"reverts":[…]}。
合併：同一筆照 seq、同 seq 照 by 排；每一欄取最後一個設這一欄的行；兩行設同一欄、值不同、彼此都沒看過對方
（A.seq > B.base 而且 B.seq > A.base）才算撞到，進 conflicts。
各模組照舊「整份讀進、整份交回」：io.read_json／write_json_atomic 認得 LEGACY 的檔名，轉到這裡（doc_to_records／records_to_doc）。
刪掉一筆寫 set {"_del": true}（讀回來不列）；undo 一筆新增寫 RETRACT 的收回欄（kb versions undo）。
"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic

FORMAT_VERSION = 1
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
# 掃描看到的、只在這台算的那一半放 .system/local/<種類>.json（新定 3）；共用那一半走流水檔
SPLIT = ("inventory", "pending", "profile")
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

COMPACT_BYTES = 1_000_000  # 自己的檔超過這個大小，寫完順手併掉被同一台後面的行蓋過的舊行
hard_delete = False  # purge 期間：刪掉的筆連這台檔裡的舊行一起拿掉（原文不留在自己的檔裡）
stats = {"merges": 0}  # 快取沒命中、真的合併的次數（測試計次用）
_TURN = {}  # (vault, kind) → 最近一次 turn.backup 記的回合代號


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
    with FileLock(p):
        _w(p, {"schema_version": 1, "items": items})


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


def _log(vault, text):
    try:
        from kb._lib import ledger
        ledger.log_error(vault, text, source="streams")
    except Exception:  # noqa: BLE001
        pass


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
        try:
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError("不是物件")
        except ValueError as e:
            if i == len(lines) - 1 or all(not t.strip() for t in lines[i + 1:]):
                _log(vault, f"{Path(path).name} 最後一行不完整，略過")
                continue
            raise ValueError(f"{path}（第 {i + 1} 行讀不懂：{e}）") from e
        out.append((i, row))  # 比這版新的行照收（認得的欄照算、不認得的略過）；寫之前才擋（check_writable，多人共用 2 第 4 點）
    return out


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
        if c.get("sig") == sig:
            st = State()
            st.records = {k: v for k, v in c["records"]}
            st.meta, st.conflicts = c["meta"], c["conflicts"]
            return st
    except (OSError, ValueError, KeyError, TypeError):
        pass
    st = _merge(vault, files)
    if files:
        _mirror(vault, kind, st)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_name(cp.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"sig": sig, "records": list(st.records.items()), "meta": st.meta,
                                   "conflicts": st.conflicts}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cp)
    except OSError:
        pass
    return st


def _mirror(vault, kind, st):
    """合併結果照舊的整份 JSON 寫回舊路徑，當這台的衍生副本（不進 git）：還直接開舊路徑讀、查有沒有檔的程式照舊能用。
    正本是流水檔；副本被改、被刪都不影響（讀的一律走 streams）。"""
    try:
        from kb._lib.io import write_text_atomic
        for k in [kind] + [v for v, (b, _r) in VIEWS.items() if b == kind]:
            legacy = Path(vault) / legacy_rel(k)
            text = json.dumps(joined_doc(vault, k, st.records), ensure_ascii=False, indent=2) + "\n"
            write_text_atomic(legacy, text)
            mp = _mirror_mark(vault, k)
            mp.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(mp, _sha(legacy.read_bytes()))
    except OSError:
        pass


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


def _merge(vault, files):
    stats["merges"] += 1
    rows = []
    for f in files:
        for i, row in _read_file(vault, f):
            rows.append((row.get("seq", 0) if isinstance(row.get("seq", 0), int) else 0, str(row.get("by", "")), len(rows), row))
    rows.sort(key=lambda r: r[:3])
    st = State()
    setters = {}  # (id, field) → [行]
    masks = []
    for _seq, _by, _n, row in rows:
        op = row.get("op")
        if op == "mask":
            masks.append(row)
            continue
        rid = row.get("id")
        if not isinstance(rid, str):
            continue
        rec = st.records.setdefault(rid, {})
        for k, v in (row.get("set") or {}).items():
            rec[k] = v
            setters.setdefault((rid, k), []).append(row)
        for k in row.get("unset") or []:
            rec.pop(k, None)
            setters.setdefault((rid, k), []).append(row)
        st.meta[rid] = {"seq": max(row.get("seq", 0), st.meta.get(rid, {}).get("seq", 0)), "by": row.get("by"),
                        "who": row.get("who"), "at": row.get("at")}
    for (rid, field), lines in setters.items():
        if len(lines) < 2:
            continue
        found = []
        for i, a in enumerate(lines):
            for b in lines[i + 1:]:
                if _val(a, field) != _val(b, field) and a.get("seq", 0) > b.get("base", 0) and b.get("seq", 0) > a.get("base", 0):
                    for x in (a, b):
                        if x not in found:
                            found.append(x)
        if found:
            st.conflicts.append({"id": rid, "field": field, "lines": found})
    for m in masks:
        for rid in m.get("ids") or []:
            if rid in st.records:
                for f in m.get("fields") or []:
                    if f in st.records[rid]:
                        st.records[rid][f] = MASKED
    return st


def _val(row, field):
    return (row.get("set") or {}).get(field) if field in (row.get("set") or {}) else ("__unset__",)


def conflicts_json(state):
    """kb <種類> show --json 的 conflicts。"""
    out = []
    for c in state.conflicts:
        out.append({"id": c["id"], "field": c["field"],
                    "values": [{"value": _val(x, c["field"]), "by": x.get("by"), "seq": x.get("seq")} for x in c["lines"]]})
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


def diff(before, after, state=None):
    """before／after：{id: {欄: 值}}；回要寫的行（還沒填 seq、base 以外的欄）[(op, id, set, unset)]。"""
    out = []
    for rid, rec in after.items():
        if rid not in before:
            cur = state.records.get(rid) if state is not None else None
            if cur and not cur.get("_del"):  # 這筆其實已經有了（例如封存那一邊的筆搬到主文件）：只寫差的欄
                s = {k: v for k, v in rec.items() if k not in cur or cur[k] != v}
                un = [k for k in cur if k not in rec and k != "_del"]
                if s or un:
                    out.append(("set", rid, s, un))
                continue
            s = dict(rec)
            if cur and cur.get("_del"):
                s["_del"] = False
            out.append(("create", rid, s, []))
            continue
        old = before[rid]
        s = {k: v for k, v in rec.items() if k not in old or old[k] != v}
        un = [k for k in old if k not in rec]
        if s or un:
            out.append(("set", rid, s, un))
    for rid in before:
        if rid not in after:
            out.append(("set", rid, {"_del": True}, []))
    return out


def write(vault, kind, before, after, turn_id=None, who=None, reverts=None):
    """比出新增、刪掉、改了哪幾欄，每筆一行 append 進這台的檔。回寫了幾行。"""
    vault = Path(vault)
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
        changes = diff(before, after, state)
        if not changes and not hard_delete:
            return 0
        by = path.stem
        extra = []
        if hard_delete:  # purge：動到的筆，這台檔裡的舊行拿掉，現在的樣子整筆換成一行（留在原來的位置，順序不變）
            replace = {}
            for op, rid, s, un in changes:
                if rid not in after:
                    continue
                prev = state.meta.get(rid, {}).get("seq", 0)
                alone = all(r.get("by") == by for r in _history(vault, kind, rid))
                seq = 1 if alone else prev + 1
                replace[rid] = {"v": FORMAT_VERSION, "op": "create" if seq == 1 else "set", "id": rid, "seq": seq,
                                "base": 0 if seq == 1 else prev, "by": by, "who": who, "at": _now(), "turn": turn_id,
                                "set": dict(after[rid]), **({"app": _app()} if _app() else {})}
            drop = {c[1] for c in changes if c[1] not in after} | {r for r, v in state.records.items() if v.get("_del")}
            extra = _rewrite_own(vault, path, replace, drop)
            changes = [c for c in changes if c[1] not in replace]
            noted_extra = [{"kind": kind, "id": r["id"], "seq": r["seq"]} for r in replace.values()]
        else:
            noted_extra = []
        lines, noted = [], []
        for op, rid, s, un in changes:
            prev = state.meta.get(rid, {}).get("seq", 0)
            row = {"v": FORMAT_VERSION, "op": op, "id": rid, "seq": prev + 1, "base": prev, "by": by, "who": who,
                   "at": _now(), "turn": turn_id, "set": s}
            if un:
                row["unset"] = un
            if reverts:
                row["reverts"] = reverts
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
        _append(path, [json.dumps(row, ensure_ascii=False) + "\n"])
        _scrub_own(vault, kind, path)


def scrub(vault, kind):
    path = own_file(vault, kind)
    with FileLock(path):
        _scrub_own(vault, kind, path)


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
        seen, keep = set(), []
        for row in reversed(rows):
            if row.get("op") != "set":
                keep.append(row)
                continue
            keys = {(row.get("id"), k) for k in list(row.get("set") or {}) + list(row.get("unset") or [])}
            if keys and keys <= seen and not row.get("reverts"):
                continue
            seen |= keys
            keep.append(row)
        keep.reverse()
        dropped = len(rows) - len(keep)
        if dropped:
            from kb._lib.io import write_text_atomic
            write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
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
    """流水檔合起來的整份文件；沒有流水檔回 None（呼叫端改讀舊檔）。"""
    if not exists(vault, kind):
        return None
    st = load(vault, kind)
    doc = _mirror_edited(vault, kind, joined_doc(vault, kind, st.records))
    if doc is None:
        legacy = Path(vault) / legacy_rel(kind)
        try:
            if legacy.is_file() and _sha(legacy.read_bytes()) != _mirror_mark(vault, kind).read_text(encoding="utf-8").strip():
                _mirror(vault, kind, st)  # 副本壞了或不是物件：從流水檔重寫
        except OSError:
            pass
    if doc is not None:  # 副本被外部整份改過：當成一次整份交回吸收進流水檔
        legacy = Path(vault) / legacy_rel(kind)
        raw = legacy.read_bytes()
        write_doc(vault, kind, doc)
        st = load(vault, kind)
        if joined_doc(vault, kind, st.records) == doc:  # 吸收後內容一樣：副本留原樣的位元組（退回換回舊版的檔不被重排）
            try:
                from kb._lib.io import write_text_atomic
                legacy.write_bytes(raw)
                write_text_atomic(_mirror_mark(vault, kind), _sha(raw))
            except OSError:
                pass
    return joined_doc(vault, kind, st.records)


def write_doc(vault, kind, data):
    """整份交回：只有舊檔沒有流水檔時先把舊檔搬成這台的流水檔，再比出改動寫進去。"""
    vault = Path(vault)
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
    changed = write(vault, base(kind), before, after)
    if kind in SPLIT and not changed:
        st = load(vault, kind)
        _mirror(vault, kind, st)  # 只有本機那一半變了：副本也要跟著換


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
    load(vault, kind)  # 順便把衍生副本寫回舊路徑
    return len(recs)


def legacy_version_dir(version):
    return f"{LEGACY_REL}/{version}"


# ---------- 退回用：一行的效果、之後的行 ----------

def _history(vault, kind, rid):
    rows = []
    for f in _files(vault, kind):
        for _i, row in _read_file(vault, f):
            if row.get("op") != "mask" and row.get("id") == rid:
                rows.append((row.get("seq", 0), str(row.get("by", "")), len(rows), row))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


def _apply(rec, row):
    rec = {} if rec is None else rec
    rec.update(row.get("set") or {})
    for k in row.get("unset") or []:
        rec.pop(k, None)
    return rec


def line_effects(vault, kind, rid, seq, by):
    """一行改了什麼：{"op", "old"（改之前這筆的欄，沒有是 None）, "new", "fields", "row"}；找不到回 None。"""
    rec = None
    for row in _history(vault, kind, rid):
        if row.get("seq") == seq and row.get("by") == by:
            old = None if rec is None else dict(rec)
            new = _apply(dict(rec) if rec is not None else None, row)
            fields = list((row.get("set") or {}).keys()) + list(row.get("unset") or [])
            return {"op": row.get("op"), "old": old, "new": new, "fields": fields, "row": row}
        rec = _apply(rec, row)
    return None


def later_lines(vault, kind, rid, seq, fields, skip_turns=()):
    """比 seq 大、設了 fields 其中一欄的行（skip_turns 的回合寫的不算）。"""
    out = []
    for row in _history(vault, kind, rid):
        if row.get("seq", 0) > seq and row.get("turn") not in skip_turns and                 set(fields) & (set(row.get("set") or {}) | set(row.get("unset") or [])):
            out.append(row)
    return out


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
    if effects["op"] == "create" or effects["old"] is None:
        new.update(RETRACT.get(kind) or {"_del": True})
    else:
        for f in effects["fields"]:
            if f in effects["old"]:
                new[f] = effects["old"][f]
            else:
                new.pop(f, None)
    write(vault, kind, {rid: cur}, {rid: new}, turn_id=turn_id, who=who, reverts=ref)
    return True


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
                            "set": dict(rec), **({"app": _app()} if _app() else {})}
        _rewrite_own(vault, path, replace, dead)
        return len(own_ids)
