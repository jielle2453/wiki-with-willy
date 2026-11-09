"""逐項改回的「項目」（放寬丙 要做的事 2-1、1-5）：一個回合夾裡每一個能單獨改回的動作算一項，編號 <回合代號>#<序>。

項目來源：manifest.jsonl（create／modify／delete 一檔一項）、folders.jsonl、moves.jsonl、detected.jsonl 一行一項；
.system/ 裡有條目編號的 JSON（日程、實體、陳述、裁示、待處理、發現）在 modify 時再拆成條目：比對那個回合的舊版複本與「改完的樣子」
（下一個備份過同一檔的回合的舊版複本，沒有就是現在的檔），一個條目編號一項（通用比對，不改各寫入程式）。
序號的排法：先排不拆的項目（照記下來的時間，同時間 manifest、folders、moves、detected 的順序），再排拆出來的條目（照檔、條目編號）；
所以之後多記幾行，前面的號碼不變。工作日誌的 actions 欄（journal）也用這裡的編號與格式。

只讀（不寫任何東西）；不 import kb.versions（它 import 這支）。
"""
import json
import re
from pathlib import Path

from kb._lib import inventory, turn
from kb._lib.io import read_json

RECORD_FILES = {  # .system/ 的 JSON → 條目清單的鍵（每筆有 id）
    ".system/agenda.json": "items",
    ".system/entities.json": "entities",
    ".system/statements.json": "statements",
    ".system/rulings.json": "rulings",
    ".system/pending.json": "items",
    ".system/findings.json": "findings",
    ".system/money.json": "items",  # 帳（記帳 2）
}
DERIVED = {  # 由別的東西重算的檔：不單獨改回（改完它的來源會重算），列出來也標 system
    ".system/inventory.json": None,
    ".system/local/inventory.json": None,  # 流水檔的本機那一半（多人共用 1）
    ".system/local/pending.json": None,
    ".system/local/profile.json": None,  # 設定檔 md 這台的指紋（#42），跟著設定檔走
    "系統/日程.md": ".system/agenda.json",
    "系統/待處理.md": ".system/pending.json",
    "系統/裁示.md": ".system/rulings.json",
    "系統/帳.md": ".system/money.json",  # 帳的年頁 系統/帳/<YYYY>.md 照 derived_from 的「系統/帳/ 底下」認（記帳 3）
}
DERIVED_PREFIXES = (("系統/帳/", ".system/money.json"),)


def derived_from(path):
    """這個檔是不是由別的東西重算的（DERIVED，加上「系統/帳/ 底下」的年頁）：是回來源（可能是 None），不是回 False。"""
    if path in DERIVED:
        return DERIVED[path]
    for prefix, src in DERIVED_PREFIXES:
        if str(path or "").startswith(prefix) and str(path).endswith(".md"):
            return src
    return False
LOCATION_OPS = ("move", "rename", "make", "remove", "trash")  # 工作日誌 actions 只抄這些加 set
_ORDER = {"manifest": 0, "folders": 1, "moves": 2, "detected": 3}


def fkey(text):
    return str(text).replace("\\", "/").casefold()


def uri_rel(uri):
    """搬動帳的 uri 換成能跟檔案路徑比的寫法：知識庫裡的是相對路徑，暫存區接區名；登記的來源照留 uri。"""
    text = str(uri or "")
    if not text.startswith("file://"):
        return text
    alias, _sep, rel = text[len("file://"):].partition("/")
    if alias.casefold() == inventory.KNOWLEDGE.casefold():
        return rel
    if alias.casefold() == inventory.HOLD.casefold():
        return f"{alias}/{rel}"
    return text


def overlaps(a, b):
    """兩個路徑鍵一樣，或一個是另一個裡面的（夾與夾裡的檔）。"""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class Ledger:
    """一個知識庫的回合夾帳。items(turn) 快取；all() 由舊到新。"""

    def __init__(self, vault):
        self.vault = Path(vault)
        root = self.vault / turn.VERSIONS_REL
        self.tids = sorted(d.name for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
        self._items = {}
        self._backups = None
        self._sessions = {}

    def session_of(self, tid):
        if tid not in self._sessions:
            info = read_json(self.vault / turn.VERSIONS_REL / tid / "turn.json", {}) or {}
            self._sessions[tid] = str(info.get("session_id") or "")
        return self._sessions[tid]

    # ---------- 備份與「改完的樣子」 ----------

    def backup_file(self, tid, path):
        return self.vault / turn.VERSIONS_REL / tid / "files" / path

    def _backup_index(self):
        if self._backups is None:
            self._backups = {}
            for tid in self.tids:
                for e in turn.entries(self.vault, tid):
                    if e.get("path") and self.backup_file(tid, e["path"]).is_file():
                        self._backups.setdefault(e["path"], []).append(tid)
        return self._backups

    def after_state(self, tid, path):
        """tid 這個回合改完 path 的樣子：下一個備份過同一檔的回合的舊版複本，沒有就是現在的檔；都沒有回 None。"""
        for t in self._backup_index().get(path, []):
            if t > tid:
                return self.backup_file(t, path)
        now = self.vault / path
        return now if now.is_file() else None

    # ---------- 項目 ----------

    def items(self, tid):
        if tid not in self._items:
            self._items[tid] = self._build(tid)
        return self._items[tid]

    def all(self, tids=None):
        out = []
        for tid in (self.tids if tids is None else tids):
            out.extend(self.items(tid))
        return out

    def find(self, code):
        """<回合代號>#<序> → 項目或 None。"""
        tid, _sep, seq = str(code).partition("#")
        if not seq.isdigit():
            return None
        return next((it for it in self.items(tid) if it["seq"] == int(seq)), None)

    def undone(self, tid):
        """這個回合的哪些項目已經改回過：{項目編號: 改回它的回合}。"""
        out = {}
        for row in _read_lines(self.vault / turn.VERSIONS_REL / tid / "undone.jsonl"):
            if row.get("item"):
                out[row["item"]] = row.get("by_turn")
        return out

    def _build(self, tid):
        base, expanded = [], []
        for e in turn.entries(self.vault, tid):
            if e.get("stream"):
                expanded.extend(self._expand_stream(tid, e))
                continue
            rec = self._expand(tid, e) if e.get("action") == "modify" and e.get("path") in RECORD_FILES else None
            if rec:
                expanded.extend(rec)
            else:
                base.append(((e.get("time", ""), _ORDER["manifest"]), self._manifest_item(tid, e)))
        for i, f in enumerate(turn.folders(self.vault, tid)):
            base.append(((f.get("time", ""), _ORDER["folders"], i), {
                "src": "folders", "op": f["op"], "kind": "folder", "from": f.get("from"), "to": f.get("to"), "by": "kb",
                "copy": False, "keys": [fkey(p) for p in (f.get("from"), f.get("to")) if p], "row": f}))
        for i, m in enumerate(turn.moves(self.vault, tid)):
            base.append(((m.get("time", ""), _ORDER["moves"], i), {
                "src": "moves", "op": m["op"], "kind": m["kind"], "from": m.get("from"), "to": m.get("to"), "by": "kb",
                "copy": False, "keys": [fkey(uri_rel(u)) for u in (m.get("from"), m.get("to")) if u], "row": m}))
        for i, d in enumerate(turn.detected(self.vault, tid)):
            it = {"src": "detected", "op": d["op"], "kind": d.get("kind", "file"), "from": d.get("from"), "to": d.get("to"),
                  "by": d.get("by", "outside"), "copy": False,
                  "keys": [fkey(uri_rel(u)) for u in (d.get("from"), d.get("to")) if u], "row": d}
            if d["op"] != "move":
                it["undoable"], it["why"] = False, "gone"  # willy 沒留一份：missing／added／modified 都改不回
            base.append(((d.get("time", ""), _ORDER["detected"], i), it))
        base.sort(key=lambda x: x[0])
        out = _fold_versions([it for _k, it in base]) + sorted(expanded, key=lambda it: (it["file"], str(it["key"])))
        writes = {w["path"]: w.get("time", "") for w in turn.writes(self.vault, tid)}  # 同路徑第二次以後的寫入（writes.jsonl）
        for n, it in enumerate(out, 1):
            it["seq"], it["turn"], it["item"] = n, tid, f"{tid}#{n}"
            it.setdefault("undoable", True)
            it["time"] = (it.get("row") or {}).get("time", "")
            it["last"] = max(it["time"], writes.get((it.get("row") or {}).get("path") or "", ""))
            it["session"] = self.session_of(tid)
            it["what"] = _what(it)
        return out

    def _manifest_item(self, tid, e):
        path, uri, action = e.get("path"), e.get("uri"), e.get("action")
        keys = [fkey(uri_rel(uri) if uri else path)] if (uri or path) else []
        has_copy = bool(path and self.backup_file(tid, path).is_file())
        it = {"src": "manifest", "op": action, "kind": "file", "from": uri or path, "to": None, "by": "kb",
              "copy": has_copy if action != "create" else False, "keys": keys, "row": e,
              "system": bool(path) and (path.startswith((".system/", "系統/")))}
        if action == "purged":
            it["undoable"], it["why"] = False, "purged"
        elif action in ("modify", "delete") and not has_copy:
            it["undoable"], it["why"] = False, "expired"
        elif derived_from(path) is not False:
            it["undoable"], it["why"] = False, "derived"
        return it

    def _expand_stream(self, tid, e):
        """流水檔的項目（多人共用 1）：回合的 streams.jsonl 一行 {kind, id, seq} 一項，帶這一行設了什麼；
        跟舊的條目一樣算 record 項目（rec 比同一筆），另帶 stream 資料讓 undo 只改這幾欄、衝突只看同一欄。"""
        from kb._lib import streams
        kind = streams.virtual_kind(self.vault / e["path"])[1]
        by = streams.own_file(self.vault, kind).stem
        out = []
        for r in streams.turn_items(self.vault, tid):
            if r.get("kind") != kind:
                continue
            eff = streams.line_effects(self.vault, kind, r.get("id"), r.get("seq"), by)
            if eff is None:
                continue
            key = str(r["id"])
            if key == streams.META_ID:
                continue
            derived = kind == "inventory"  # 掃描出來的帳：不單獨列、不單獨改回，整個回合退回時才一起改回
            out.append({**({"undoable": False, "why": "derived"} if derived else {}),
                        "src": "record", "op": "add" if eff["op"] == "create" else "set", "kind": "record", "file": e["path"],
                        "key": key, "from": e["path"], "to": None, "by": "kb", "copy": False, "keys": [fkey(e["path"])],
                        "rec": (fkey(e["path"]), key), "old": eff["old"], "new": eff["new"], "old_index": None, "row": e,
                        "system": True, "stream": {"kind": kind, "seq": r.get("seq"), "by": by, "fields": eff["fields"],
                                                   "set": (eff["row"].get("set") or {})},
                        "what_text": _record_name(eff["new"] or eff["old"], key)})
        return out

    def _expand(self, tid, e):
        """modify 的 JSON 檔拆成條目；讀不懂或沒有條目編號回 None（改成一檔一項）。"""
        path = e["path"]
        old_p = self.backup_file(tid, path)
        if not old_p.is_file():
            return None
        new_p = self.after_state(tid, path)
        try:
            old = _records(read_json(old_p), RECORD_FILES[path])
            new = _records(read_json(new_p), RECORD_FILES[path]) if new_p else {}
        except (ValueError, OSError):
            return None
        if old is None or (new_p and new is None):
            return None
        new = new or {}
        out = []
        for key in sorted(set(old) | set(new)):
            if key in old and key in new:
                if old[key][1] == new[key][1]:
                    continue
                op = "set"
            else:
                op = "add" if key in new else "remove"
            out.append({"src": "record", "op": op, "kind": "record", "file": path, "key": key, "from": path, "to": None,
                        "by": "kb", "copy": True, "keys": [fkey(path)], "rec": (fkey(path), key),
                        "old": old[key][1] if key in old else None, "new": new[key][1] if key in new else None,
                        "old_index": old[key][0] if key in old else None, "row": e, "system": True})
        return out


def _record_name(rec, key):
    for f in ("text", "name", "question", "alias"):
        if isinstance(rec, dict) and isinstance(rec.get(f), str) and rec[f]:
            return rec[f][:30]
    return key


def _records(data, list_key):
    """JSON → {條目編號: (原來的位置, 條目)}；結構不對或有條目沒編號回 None。"""
    if not isinstance(data, dict) or not isinstance(data.get(list_key), list):
        return None
    out = {}
    for i, rec in enumerate(data[list_key]):
        if not isinstance(rec, dict) or not rec.get("id"):
            return None
        out[str(rec["id"])] = (i, rec)
    return out


def _fold_versions(its):
    """改 Office 檔存的版本檔（D-529）：同一句 create 的「<主檔名>（版本 N）<副檔名>」併進同夾原檔那項（versions），
    不單獨列：只退版本檔那項會把唯一的備份刪掉（jacky 2026-11-05 定）。"""
    mods = [it for it in its if it["src"] == "manifest" and it["op"] == "modify" and (it.get("row") or {}).get("path")]
    if not mods:
        return its
    out = []
    for it in its:
        path = (it.get("row") or {}).get("path") or ""
        host = next((m for m in mods if it["src"] == "manifest" and it["op"] == "create" and _version_of(path, m["row"]["path"])), None)
        if host is None:
            out.append(it)
        else:
            host.setdefault("versions", []).append(it["row"])
            host["keys"] = host["keys"] + it["keys"]  # 之後有人動了版本檔，退這項要算衝突
    return out


def _version_of(path, original):
    folder, _sep, name = original.rpartition("/")
    vfolder, _sep, vname = path.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if vfolder != folder or not dot:
        return False
    return re.fullmatch(re.escape(stem) + "（版本[一二三四五六七八九十0-9]+）" + re.escape("." + ext), vname) is not None


def version_label(row):
    """版本檔那行 → 「版本二」。"""
    name = str(row.get("path") or "").rpartition("/")[2]
    return name[name.rfind("（版本") + 1:name.rfind("）")]


def _what(it):
    """項目的名字：檔或夾的名字，條目是「<檔名>:<條目編號>」。"""
    if it["kind"] == "record":
        if it.get("what_text"):
            return it["what_text"]
        return f"{Path(it['file']).stem}:{it['key']}"
    for field in ("from", "to"):
        v = it.get(field)
        if v:
            return str(v).rstrip("/").rpartition("/")[2] or str(v)
    return ""


def _read_lines(path):
    rows = []
    if not Path(path).is_file():
        return rows
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def later_than(a, b):
    """a 是不是比 b 晚（D-508 的「後來」換成以項目為單位）：同一回合序號比它大；回合代號比它晚（不管哪個對話）；
    或回合代號比它早、但最後一次寫的時間比 b 那一列晚（先開口、後寫的別的對話；時間只到秒，兩邊是不同對話時同一秒也算）。"""
    if a["turn"] == b["turn"]:
        return a["seq"] > b["seq"]
    if a["turn"] > b["turn"]:
        return True
    if not (a["last"] and b["time"]):
        return False
    return a["last"] > b["time"] or (a["last"] == b["time"] and bool(a["session"] and b["session"] and a["session"] != b["session"]))


def conflicts_of(ledger, item, candidates=None):
    """比 item 晚、碰到同一路徑或同一條目編號的項目（已改回過的不算）。條目只跟同檔同編號的條目、或同一個檔的整檔動作撞。"""
    out = []
    for other in (ledger.all() if candidates is None else candidates):
        if other["item"] == item["item"] or not later_than(other, item):
            continue
        if other["item"] in ledger.undone(other["turn"]):
            continue
        if item.get("rec") and other.get("rec"):
            hit = item["rec"] == other["rec"]
            if hit and item.get("stream") and other.get("stream"):  # 只往後加的欄（歷程）兩邊都加不算改了同一欄（D-337）
                from kb._lib import streams
                same = set(item["stream"]["fields"]) & set(other["stream"]["fields"])
                hit = bool(same - set(streams.append_only(item["stream"]["kind"])))
        else:
            hit = any(overlaps(a, b) for a in item["keys"] for b in other["keys"])
        if hit:
            out.append(other)
    return out


def actions_of_turn(vault, tid, value_max=200, ledger=None):
    """工作日誌 actions 欄（資料與介面格式）：這一回合的位置類動作（moves、folders、detected）加 JSON 條目的單欄改動（set），
    每筆帶項目編號與 by（kb｜turn｜outside）。檔內容不抄；舊值、新值超過 value_max 字的記 None。沒有回空清單。"""
    out = []
    for it in (ledger or Ledger(vault)).items(tid):
        base = {"item": it["item"], "op": it["op"], "kind": it["kind"], "from": it["from"], "to": it["to"], "by": it["by"]}
        if it["src"] == "folders":
            base["from"] = inventory.make_uri(inventory.KNOWLEDGE, it["from"])
            base["to"] = inventory.make_uri(inventory.KNOWLEDGE, it["to"]) if it["to"] else None
            out.append(base)
        elif it["src"] in ("moves", "detected"):
            if (it.get("row") or {}).get("colleague"):
                base["colleague"] = True  # 多人時共用夾裡程式比出來的變動，日誌寫「可能是同事動的」
            out.append(base)
        elif it["src"] == "record" and it["op"] == "set":
            for field in sorted(set(it["old"]) | set(it["new"])):
                if it["old"].get(field) == it["new"].get(field):
                    continue
                out.append({"item": it["item"], "op": "set", "kind": "record", "file": it["file"], "key": it["key"],
                            "field": field, "old": _short(it["old"].get(field), value_max),
                            "new": _short(it["new"].get(field), value_max), "by": "kb"})
    return out


def _short(value, limit):
    if len(json.dumps(value, ensure_ascii=False)) > limit:
        return None
    return value
