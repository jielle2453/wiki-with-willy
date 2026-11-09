"""程式產生的 md 有沒有被手改（lint 第 4 點，D-223）：.system/renders.json（schema 1）記每份 md 最後一次程式寫進去的指紋。

{"schema_version": 1, "files": {"系統/待處理.md": "<sha256>"}, "history": {...}, "dates": {"系統/日程.md": "2026-10-20"}, "saved": [...],
 "asks": [...], "carried": {"系統/日程.md": [{"id","kind","text","orig","at"}]}}
history 是同一份 md 程式以前寫過的指紋（退回換回舊版時對得上，不算手改）；dates 是寫的那天（日程照當天分段，重算她看到的那份要用同一天）；saved 是留下她那份的紀錄（{file, saved, at}，最多 SAVED_MAX 筆），
健檢報告數這個月總目錄、工作日誌被手改了幾次；asks 是待處理.md「整理時要問你的」那段（健檢與 kb asks --all 算好記下）。
carried（Obsidian 首頁，#22，D-263；選填鍵）是她在程式產生的頁上寫、程式認不出的字：重新產生時放在那頁最底下「你加的（還沒處理）」，
kb asks 每筆出一題，她在那段刪掉一行或 kb renders drop 就是不用了。
重新產生的那支先 check：沒檔、沒紀錄（第一次跑這版的知識庫）、跟紀錄一樣都不算手改；不一樣就 keep 把她那份存到
.system/handedits/<時間>-<檔名>，寫完新的再 record。renders.json 讀不到當沒有紀錄、寫不了只記 log：手改偵測不能擋住重新產生。
diff 把她那份跟程式那份比：一行一個條目的清單（lines 是 [(那行, 編號)]），分打勾、刪掉、改日期、其他；
ignore_sections 的段與 ``` 圍起來的圖不比（整理時要問的問題每次都會變）。
"""
import contextlib
import hashlib
import re
import secrets
from datetime import date, datetime
from pathlib import Path

from kb._lib import ledger
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

RENDERS_POSIX = ".system/renders.json"
HANDEDITS_POSIX = ".system/handedits"
SCHEMA_VERSION = 1
SAVED_MAX = 200
HISTORY_MAX = 20
_CHECKED = re.compile(r"^-\s*\[[xX✓✔]\]\s*")
_BOX = re.compile(r"^-\s*\[\s?\]\s*")
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}|\d{1,2}-\d{1,2}")
_WEEKDAY = re.compile(r"（週[一二三四五六日]）")
_COUNT = re.compile(r"（\d+）$")  # 日程每行日期後面的星期，改日期時跟著變
_LINK = re.compile(r"\[\[([^\[\]|]*)\|([^\[\]]*)\]\]")
_BARE_LINK = re.compile(r"\[\[([^\[\]|]*)\]\]")


CARRIED_TITLE = "你加的（還沒處理）"
CARRIED_NOTE = "這段是你在這頁寫、我還沒看懂的；在首頁或對話裡回我，或刪掉這行就是不用了。"
CARRIED_MAX = 100
_SUPPRESS = set()  # 正在照她的手改套用的頁：重新產生時不再當成手改（kb renders apply 自己處理，避免套用到一半又被偵測一次）


@contextlib.contextmanager
def suppressed(rel):
    _SUPPRESS.add(rel)
    try:
        yield
    finally:
        _SUPPRESS.discard(rel)


def fingerprint(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _path(vault):
    return Path(vault) / RENDERS_POSIX


def _str_map(value):
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(value, dict) else {}


def load(vault):
    """壞檔、形狀不對當空的（不擋重新產生）；內層型別不對的那幾筆也丟掉（測試 T-14）。"""
    try:
        data = read_json(_path(vault))
    except (ValueError, OSError):
        data = None
    if not isinstance(data, dict):
        data = {}
    history = data.get("history") if isinstance(data.get("history"), dict) else {}
    return {"schema_version": SCHEMA_VERSION, "files": _str_map(data.get("files")),
            "history": {k: [h for h in v if isinstance(h, str)] for k, v in history.items()
                        if isinstance(k, str) and isinstance(v, list)},
            "dates": _str_map(data.get("dates")),
            "saved": [s for s in data.get("saved") or [] if isinstance(s, dict)] if isinstance(data.get("saved"), list) else [],
            "asks": [q for q in data.get("asks") if isinstance(q, str)] if isinstance(data.get("asks"), list) else [],
            "carried": {k: [c for c in v if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("text"), str)]
                        for k, v in data.get("carried").items() if isinstance(k, str) and isinstance(v, list)}
                       if isinstance(data.get("carried"), dict) else {},
            "bases": {k: {"text": v["text"], "lines": [x for x in v["lines"] if isinstance(x, list) and len(x) == 2]}
                      for k, v in data.get("bases").items() if isinstance(v, dict) and isinstance(v.get("text"), str)
                      and isinstance(v.get("lines"), list)} if isinstance(data.get("bases"), dict) else {}}


def check(vault, rel, raw=False):
    """她手改過就回她那份的文字，否則 None（沒檔、讀不了、沒紀錄、跟紀錄一樣）。正在套用的頁（suppressed）回 None，raw=True 照看。"""
    if rel in _SUPPRESS and not raw:
        return None
    p = Path(vault) / rel
    try:
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8").lstrip("\ufeff")  # 記事本另存會加 BOM，字沒改不算手改（測試 T-11）
    except (OSError, UnicodeDecodeError):
        return None
    data = load(vault)
    want = data["files"].get(rel)
    sha = fingerprint(text)
    if not want or sha == want or sha in (data["history"].get(rel) or []):
        return None
    return text


def recorded_day(vault, rel):
    return load(vault)["dates"].get(rel)


def _edit(vault, change):
    p = _path(vault)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(p):
            data = load(vault)
            change(data)
            write_json_atomic(p, data)
    except OSError as e:
        ledger.log_error(vault, f"renders.json 沒寫成：{type(e).__name__}: {e}", source="renders")


def base(vault, rel):
    """上一次真的寫進 md 的內容與條目 (text, [(行, 編號)])；沒記過回 None（手改比對的基準，lint 第二輪 T-29）。"""
    got = load(vault)["bases"].get(rel)
    if not got:
        return None
    return got["text"], [(str(x[0]), x[1]) for x in got["lines"] if isinstance(x, (list, tuple)) and len(x) == 2]


def record(vault, rel, text, day=None, turn_id=None, lines=None):
    """記下這次程式寫進去的內容指紋（與那天），舊的指紋留最近 HISTORY_MAX 個在 history：退回把 md 換回程式以前寫的那份時
    指紋對得上舊的，不會被當成手改（renders.json 不備進回合，免得每次寫待處理都多一行改了）。turn_id 只為了呼叫端寫法一致。"""
    def change(data):
        sha = fingerprint(text)
        old = [h for h in data["history"].get(rel, []) if h != sha]
        if data["files"].get(rel) and data["files"][rel] != sha:
            old = [h for h in old if h != data["files"][rel]] + [data["files"][rel]]
        data["history"][rel] = old[-HISTORY_MAX:]
        data["files"][rel] = sha
        data["dates"][rel] = day or date.today().isoformat()
        if lines is not None:  # 有 JSON 真相的清單：記下這次寫進去的那份，下次比她改了什麼用它（不用現在的 JSON 重算）
            data.setdefault("bases", {})[rel] = {"text": str(text), "lines": [[str(ln), i] for ln, i in lines]}
    _edit(vault, change)


# ---------- 你加的（還沒處理）----------

def carried(vault, rel):
    """這頁還沒處理的字 [{"id","kind":"added|changed","text","orig","at"}]。"""
    return list(load(vault)["carried"].get(rel) or [])


def carried_all(vault):
    """{頁: [條目]}，只列有東西的頁。"""
    return {k: list(v) for k, v in load(vault)["carried"].items() if v}


def carried_line(item):
    """那段裡的一行（不含開頭的「- 」）。"""
    if item.get("kind") == "changed" and item.get("orig"):
        return f"原本「{item['orig']}」→ 你改成「{item['text']}」"
    return item["text"]


def carried_section(vault, rel):
    """這頁最底下「你加的（還沒處理）」那段的文字（含前面的空行），沒有東西回空字串。"""
    items = carried(vault, rel)
    if not items:
        return ""
    body = "".join(f"- {carried_line(c)}\n" for c in items)
    return f"\n## {CARRIED_TITLE}\n{CARRIED_NOTE}\n" + body


def carried_lines_in(text):
    """她那份頁面裡「你加的」那段的每一行（去掉開頭的「- 」）；說明那句不算。"""
    out, inside = [], False
    for raw in str(text).splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            inside = line[3:].startswith(CARRIED_TITLE)
            continue
        if inside and line.startswith("- "):
            out.append(line[2:])
    return out


def add_carried(vault, rel, new, drop_ids=()):
    """加幾筆、拿掉幾筆（編號）；回 (實際加的, 實際拿掉的)。文字先遮值；同頁同文字不重複。"""
    done = {"added": [], "dropped": []}

    def change(data):
        items = data["carried"].setdefault(rel, [])
        keep = [c for c in items if c["id"] not in set(drop_ids)]
        done["dropped"] = [c for c in items if c["id"] in set(drop_ids)]
        have = {(c.get("kind"), c["text"], c.get("orig")) for c in keep}
        for n in new:
            text = masked(vault, n["text"]).strip()
            orig = masked(vault, n["orig"]).strip() if n.get("orig") else None
            if not text or (n.get("kind"), text, orig) in have or len(keep) >= CARRIED_MAX:
                continue
            item = {"id": "c-" + secrets.token_hex(4), "kind": n.get("kind") or "added", "text": text, "orig": orig,
                    "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            keep.append(item)
            done["added"].append(item)
            have.add((item["kind"], text, orig))
        data["carried"][rel] = keep
    _edit(vault, change)
    return done["added"], done["dropped"]


def unhanded(vault):
    """還沒交給 AI 的「你加的」（多人共用 3 第 11 點，D-299）：[(頁, 條目)]，handed 欄是空的。"""
    return [(rel, c) for rel, items in sorted(carried_all(vault).items()) for c in items if not c.get("handed")]


def mark_handed(vault, ids):
    """記這幾筆已經交給 AI（每筆只交一次）；回記了幾筆。"""
    ids, n = set(ids), [0]

    def change(data):
        for items in data["carried"].values():
            for c in items:
                if c["id"] in ids and not c.get("handed"):
                    c["handed"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    n[0] += 1
        return n[0] > 0
    _edit(vault, change)
    return n[0]


def drop_carried(vault, cid):
    """kb renders drop：回 (頁, 條目) 或 (None, None)。"""
    for rel, items in carried_all(vault).items():
        hit = next((c for c in items if c["id"] == cid), None)
        if hit is not None:
            add_carried(vault, rel, [], drop_ids=[cid])
            return rel, hit
    return None, None


def stored_asks(vault):
    """上一次健檢或 kb asks --all 算好的「整理時要問你的」問句（待處理.md 那段用）。"""
    got = load(vault).get("asks")
    return [str(q) for q in got] if isinstance(got, list) else []


def set_asks(vault, questions):
    def change(data):
        data["asks"] = [str(q) for q in questions]
    _edit(vault, change)


KEEP_FAILED = "keep_failed"  # 她那份沒存成：這次不重寫，免得蓋掉（測試 T-10）


def masked(vault, text):
    """存她那份與差異之前先遮值（她可能在檔裡寫了密碼）。"""
    from kb._lib import credentials
    return credentials.mask_secrets(str(text), credentials.known_names(vault))


def keep(vault, rel, text):
    """她那份（遮過值）存到 .system/handedits/<時間>-<檔名>（同名加 -2…），記進 saved；回總資料夾相對路徑。寫不了丟 OSError。"""
    text = masked(vault, text)
    folder = Path(vault) / HANDEDITS_POSIX
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = Path(rel).name
    target, n = folder / f"{stamp}-{name}", 1
    while target.exists():
        n += 1
        target = folder / f"{stamp}-{n}-{name}"
    write_text_atomic(target, text)
    saved_rel = f"{HANDEDITS_POSIX}/{target.name}"

    def change(data):
        data["saved"] = (data["saved"] + [{"file": rel, "saved": saved_rel, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}])[-SAVED_MAX:]
    _edit(vault, change)
    return saved_rel


def saved(vault):
    return load(vault)["saved"]


# ---------- 她改了什麼 ----------

def unlink(text):
    """程式頁上的連結還原成名字（記帳 3，D-280）：[[路徑|名字]] → 名字，[[路徑]] → 路徑最後一段。
    比對手改前先過一遍：她沒動連結的行、Obsidian 搬卡時改了路徑的行都不算改。"""
    text = _LINK.sub(lambda m: m.group(2), str(text))
    return _BARE_LINK.sub(lambda m: m.group(1).rsplit("/", 1)[-1].split("#", 1)[0].split("^", 1)[0], text)


def _lines(text, ignore_sections, skip_line=None):
    ignore_sections = tuple(ignore_sections) + (CARRIED_TITLE,)  # 「你加的」那段另外處理（kb renders apply），不當條目比
    out, skip, fence = [], False, False
    for raw in str(text).splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if line.startswith("## "):
            skip = any(line[3:].startswith(s) for s in ignore_sections)
            out.append(_COUNT.sub("", line))  # 段名後面的件數不比（件數變了不是她改的，T-29）
            continue
        if skip or not line.strip() or (skip_line is not None and skip_line(line)):
            continue
        out.append(line)
    return out


def _undated(line):
    return _DATE.sub("#", _WEEKDAY.sub("", line))


def diff(base, hers, lines, ignore_sections=(), skip=None):
    """base 是程式寫的那份、hers 是她改的；lines 是 [(條目那行, 編號)]。回 {"checked","removed","changed_date","other"}：
    前三種每項 {"id","line"}（改日期多 "to"：她寫的日期），other 是看不出對到哪一條的行（她加的、改了字的）。
    比之前三樣都先 unlink（連結還原成名字，回的行也是還原過的）；skip(行) 為真的行不比（帳頁的表與合計，記帳 3）。"""
    lines = [(unlink(line), pid) for line, pid in lines]
    ids = {line: pid for line, pid in lines}
    base_lines = _lines(unlink(base), ignore_sections, skip)
    hers_lines = _lines(unlink(hers), ignore_sections, skip)
    base_set = set(base_lines)
    got = {"checked": [], "removed": [], "changed_date": [], "other": []}
    seen = set()
    loose = []
    for line in hers_lines:
        checked = _CHECKED.match(line)
        body = "- " + line[checked.end():] if checked else (("- " + line[_BOX.match(line).end():]) if _BOX.match(line) else line)
        if body in ids and body not in seen:
            seen.add(body)
            if checked:
                got["checked"].append({"id": ids[body], "line": body})
            continue
        if body in base_set:
            continue
        loose.append(body)
    missing = [line for line, _pid in lines if line not in seen and line in base_set]
    for body in loose:
        hit = next((m for m in missing if _undated(m) == _undated(body) and m != body), None)
        if hit is not None:
            missing.remove(hit)
            new = _DATE.findall(body)
            old = _DATE.findall(hit)
            to = next((d for d in new if d not in old), new[0] if new else "")
            got["changed_date"].append({"id": ids[hit], "line": hit, "to": to})
        else:
            got["other"].append(body)
    got["removed"] = [{"id": ids[m], "line": m} for m in missing]
    return got


def keep_known(got, ids):
    """只留現在資料裡還有的編號（退回拿掉的件不算她打勾、刪掉、改日期的，第三輪 T-36）。"""
    ids = set(ids)
    for k in ("checked", "removed", "changed_date"):
        got[k] = [c for c in got.get(k) or [] if c.get("id") in ids]
    return got


def is_empty(d):
    return not any(d.get(k) for k in ("checked", "removed", "changed_date", "other"))


def mask_diff(vault, got):
    """差異裡的「其他」是她寫的字，先遮值再存進待處理。"""
    got["other"] = [masked(vault, x) for x in got.get("other") or []]
    return got


def handle_list(vault, rel, hers, base_text, lines, turn_id, ignore_sections=(), known_ids=None):
    """日程、裁示被手改（有 JSON 真相的清單）：看得出改了條目就存她那份、待處理開一條 handedit（D-223）。回存的路徑、None，
    或 KEEP_FAILED（她那份沒存成，呼叫端這次不重寫）。比對出錯只記 log、當成改了別的。"""
    try:
        got = diff(base_text, hers, lines, ignore_sections)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 手改比對失敗：{type(e).__name__}: {e}", source="renders")
        got = {"checked": [], "removed": [], "changed_date": [], "other": ["（比對不出改了哪裡）"]}
    if known_ids is not None:
        got = keep_known(got, known_ids)
    if is_empty(got):
        return None
    try:
        saved_rel = keep(vault, rel, hers)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 她那份沒存成，這次不重寫：{type(e).__name__}: {e}", source="renders")
        return KEEP_FAILED
    try:
        from kb._lib import pending, turn
        pending.add_handedit(vault, rel, saved_rel, mask_diff(vault, got), turn_id or turn.current(vault)["turn"])
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 手改沒記進待處理：{type(e).__name__}: {e}", source="renders")
    return saved_rel


def keep_only(vault, rel):
    """總目錄、工作日誌被手改：只存她那份（之後重新產生、報告說一句），不開待處理。回存的路徑、None 或 KEEP_FAILED。"""
    try:
        hers = check(vault, rel)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 手改比對失敗：{type(e).__name__}: {e}", source="renders")
        return None
    if hers is None:
        return None
    try:
        return keep(vault, rel, hers)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 她那份沒存成，這次不重寫：{type(e).__name__}: {e}", source="renders")
        return KEEP_FAILED
