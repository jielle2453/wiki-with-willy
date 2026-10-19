"""綜合卡（D-036、D-038、D-114～D-121；實體與綜合卡第 2 點）：檔頭、驗證、快取渲染、待辦清單。

綜合卡住 <型別>/<名稱>/<名稱>.md（D-046 的 Z1），一個實體一張。檔頭由 header_for 印給製卡者照抄；
「整理到」是寫卡時讀過的檔案卡與各自指紋前 12 碼，PostToolUse 用 validate 驗它等於目前掛著的那組；
「檔案卡」「相關實體」兩欄是快取（D-118），render_cache 只重寫這兩欄、備份帶 by program（D-075 不數）。
buckets 給 cards todo：synthesis_new（D-114 門檻）、synthesis_stale（D-121）、synthesis_broken、entity_drift（D-057）。
檔頭用 cards.parse_front 解析（同一套 YAML 子集）。
"""
import re
from datetime import date, datetime
from pathlib import Path

from kb._lib import config, entities, profile
from kb._lib.io import write_text_atomic

TEMPLATE_VERSION = "1"
KEYS = ("編號", "型別", "大類", "名稱", "別名", "範本版本", "整理到", "更新日", "檔案卡", "相關實體")
CACHE_KEYS = ("檔案卡", "相關實體")
BASE_SECTIONS = ("現況", "紀事", "推算")
BLOCK_KEYS = ("整理到", "檔案卡")  # 一行一項（"  - …"）；其他清單寫一行 [a, b]
LIST_KEYS = ("別名", "相關實體") + BLOCK_KEYS
BASE_HINTS = {"現況": "這件事現在怎樣：一點一行、事實變了就改寫，每點結尾〔f-…〕",
              "紀事": "日期倒序、只增不改：決定、改版、關鍵事件",
              "推算": "從多張卡算出來的：結果、算法、依據、算的日期；沒有就整段不寫"}
STALE_CODE = "synced"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# validate 的問題代號：這些一出就不再往下驗（後面的比對沒有意義）
GATING = ("parse", "missing_key", "unknown", "self", "removed", "inferred")


def card_rel(entity):
    return entities.card_rel(entity)


def category_text(prof, type_name):
    """型別的大類，中文（對象、事、物、主題）；型別不在表回空字串。"""
    row = next((t for t in (prof or {}).get("types") or [] if entities.key(t["name"]) == entities.key(type_name)), None)
    return profile._REVERSED[0].get(row.get("category"), "") if row else ""


def sections_for(prof, type_):
    """綜合卡要寫的段：固定三段（現況、紀事、推算）加設定檔第四節那個型別的列，[{"name","required","hint"}]。"""
    out = [{"name": n, "required": False, "hint": BASE_HINTS[n]} for n in BASE_SECTIONS]
    for s in (prof or {}).get("synthesis_sections") or []:
        if entities.key(s.get("type")) == entities.key(type_):
            out += [{"name": x["name"], "required": bool(x.get("required")), "hint": x.get("hint", "")}
                    for x in s.get("sections") or []]
    return out


# ---------- 快取欄 ----------

def _date_key(m):
    d = m.get("date") or ""
    return (1, d) if _DATE.match(d) else (0, "")


def card_lines(mounts_of_it):
    """檔案卡 欄：每行「<編號> <文件日期> <標題>」照文件日期倒序（未載的排最後），同日照編號。"""
    ordered = sorted(sorted(mounts_of_it, key=lambda m: m["id"]), key=_date_key, reverse=True)
    return [f"{m['id']} {m.get('date') or '未載'} {m.get('title') or ''}".rstrip() for m in ordered]


def synced_lines(mounts_of_it):
    """整理到 欄：每行「<檔案卡編號> <fp>」照編號排。"""
    return [f"{m['id']} {m.get('fp') or ''}".rstrip() for m in sorted(mounts_of_it, key=lambda m: m["id"])]


def related(data, mounts, entity):
    """跟它共用至少一張檔案卡、不是客戶自己、status 不是 merged 的實體名稱，照共用張數多到少（同數照名稱）。"""
    mine = {m["id"] for m in mounts.get(entity["name"], [])}
    by_name = {e["name"]: e for e in data["entities"] if e.get("status") != "merged"}
    counts = {}
    for name, ms in mounts.items():
        other = by_name.get(name)
        if other is None or other.get("id") == entity.get("id") or entities.is_self(other):
            continue
        n = sum(1 for m in ms if m["id"] in mine)
        if n:
            counts[name] = n
    return sorted(counts, key=lambda n: (-counts[n], n))


# ---------- 檔頭 ----------

def _front_text(front):
    out = ["---"]
    for k in KEYS:
        if k not in front:
            continue
        v = front[k]
        if k in BLOCK_KEYS:
            out.append(f"{k}:")
            out.extend(f"  - {x}" for x in v)
        elif isinstance(v, list):
            out.append(f"{k}: [{', '.join(v)}]")
        else:
            out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n"


def header_for(vault, entity, mounts_of_it, today=None, data=None, mounts=None, prof=None):
    """印給製卡者照抄的綜合卡檔頭（資料與介面格式那個樣子）。data、mounts、prof 沒給自己讀。"""
    data = data if data is not None else entities.load(vault)
    mounts = mounts if mounts is not None else entities.mounts(vault, data)
    prof = prof if prof is not None else entities._profile(vault)
    return _front_text({
        "編號": entity["id"], "型別": entity.get("type"), "大類": category_text(prof, entity.get("type")),
        "名稱": entity["name"], "別名": list(entity.get("aliases") or []), "範本版本": TEMPLATE_VERSION,
        "整理到": synced_lines(mounts_of_it), "更新日": today or date.today().isoformat(),
        "檔案卡": card_lines(mounts_of_it), "相關實體": related(data, mounts, entity)})


def replace_front(text, updates):
    """只換檔頭裡 updates 給的鍵（一行的值或「鍵:」下接「  - 項」的整塊），其他行原樣；鍵不在檔頭就不加。"""
    lines = text.splitlines(keepends=True)
    out, i, in_front = [], 0, False
    while i < len(lines):
        line = lines[i]
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("﻿") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
        elif in_front and bare.partition(":")[0].strip() in updates and not bare.startswith(" "):
            k = bare.partition(":")[0].strip()
            v = updates[k]
            i += 1
            while i < len(lines) and lines[i].startswith(("  - ", "- ")):
                i += 1
            if k in BLOCK_KEYS:
                out.append(f"{k}:\n")
                out.extend(f"  - {x}\n" for x in v)
            elif isinstance(v, list):
                out.append(f"{k}: [{', '.join(v)}]\n")
            else:
                out.append(f"{k}: {v}\n")
            continue
        out.append(line)
        i += 1
    return "".join(out)


def replace_title(text, old, new):
    """正文第一個「# <old>」換成「# <new>」（改名後卡才驗得過）；其他行原樣。"""
    lines = text.splitlines(keepends=True)
    seen = 0
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "---":
            seen += 1
            continue
        if seen >= 2 and line.rstrip("\r\n").rstrip() == f"# {old}":
            lines[i] = f"# {new}" + line[len(line.rstrip("\r\n")):]
            break
    return "".join(lines)


def _body_lines(text):
    lines = str(text).lstrip("﻿").splitlines()
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return [x.rstrip() for x in lines[i + 1:]]
    return []


# ---------- 驗證 ----------

def problems(vault, rel, text, data, mounts, prof):
    """綜合卡的問題 [(代號, 原因)]，照 validate 的順序；GATING 的一出就只回它一個。"""
    from kb._lib import cards
    try:
        front = cards.parse_front(text)
    except ValueError as e:
        return [("parse", f"檔頭解析不了：{e}")]
    for k in KEYS:
        if k not in front:
            return [("missing_key", f"檔頭少了「{k}」")]
    eid = front["編號"]
    entry = next((e for e in data["entities"] if e.get("id") == eid), None) if isinstance(eid, str) else None
    if entry is None:
        return [("unknown", f"編號 {eid} 不在實體清單")]
    if entities.is_self(entry):
        return [("self", "這是你自己（關於你），不寫綜合卡")]
    if entry.get("status") != "active":
        return [("removed", "這個實體已移除")]
    if entry.get("source") == "inferred":
        return [("inferred", "還沒跟客戶確認名稱，先問再寫")]
    out = []
    want = card_rel(entry)
    if rel.casefold() != want.casefold():
        out.append(("path", f"綜合卡該放在 {want}"))
    expect = {"型別": entry.get("type"), "大類": category_text(prof, entry.get("type")), "名稱": entry["name"],
              "別名": list(entry.get("aliases") or [])}
    for k, v in expect.items():
        if front[k] != v:
            out.append(("field", f"「{k}」照 kb cards todo 給的檔頭抄"))
            break
    if front["範本版本"] != TEMPLATE_VERSION:
        out.append(("version", f"範本版本該是 {TEMPLATE_VERSION}"))
    have = front["整理到"]
    if not isinstance(have, list) or sorted(have) != sorted(synced_lines(mounts.get(entry["name"], []))):
        out.append((STALE_CODE, "整理到不是現行的，重跑 kb cards todo 拿新檔頭"))
    day = front["更新日"]
    ok = isinstance(day, str) and bool(_DATE.match(day))
    if ok:
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            ok = False
    if not ok:
        out.append(("date", "更新日要寫 YYYY-MM-DD"))
    body = _body_lines(text)
    if f"# {entry['name']}" not in body or "## 現況" not in body:
        out.append(("body", f"正文要有 # {entry['name']} 與 ## 現況"))
    return out


def safe_problems(vault, rel, text, data, mounts, prof):
    """problems 整段包住：任何例外都當成解析不了（同 cards.safe_problems）。"""
    try:
        return problems(vault, rel, text, data, mounts, prof)
    except Exception as e:  # noqa: BLE001
        return [("parse", f"檔頭解析不了：{type(e).__name__}")]


def validate(vault, rel, data=None, mounts=None, prof=None):
    """PostToolUse 與測試用：回 (ok, reason)，只報第一個錯。"""
    try:
        text = (Path(vault) / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"檔頭解析不了：{type(e).__name__}"
    try:
        data = data if data is not None else entities.load(vault)
        mounts = mounts if mounts is not None else entities.mounts(vault, data)
        prof = prof if prof is not None else entities._profile(vault)
    except Exception as e:  # noqa: BLE001
        return False, f"檔頭解析不了：{type(e).__name__}"
    found = safe_problems(vault, rel, text, data, mounts, prof)
    return (False, found[0][1]) if found else (True, "")


# ---------- 快取渲染 ----------

def _cache_ok(found):
    """合法綜合卡（或只差整理到）才重渲染快取。"""
    return all(code == STALE_CODE for code, _r in found)


def render_cache(vault, entity_names, turn_id):
    """對這些實體裡已有合法綜合卡的（只差整理到也算），只重寫檔頭的 檔案卡 與 相關實體 兩欄；內容沒變不寫；
    寫前 turn.backup(by="program")。entity_names 是 None 就全部；名稱可以是別名或合併過的。回重寫了的相對路徑清單。"""
    from kb._lib import turn
    data = entities.load(vault)
    mounts = entities.mounts(vault, data)
    prof = entities._profile(vault)
    if entity_names is None:
        targets = [e for e in data["entities"] if e.get("status") == "active"]
    else:
        targets, seen = [], set()
        for name in entity_names:
            e, _hit = entities.find(data, name)
            if e is not None and e.get("id") not in seen:
                seen.add(e.get("id"))
                targets.append(e)
    written = []
    for e in targets:
        if entities.is_self(e) or not e.get("type") or e.get("status") != "active":
            continue
        rel = card_rel(e)
        p = Path(vault) / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _cache_ok(safe_problems(vault, rel, text, data, mounts, prof)):
            continue
        new = replace_front(text, {"檔案卡": card_lines(mounts.get(e["name"], [])),
                                   "相關實體": related(data, mounts, e)})
        if new == text:
            continue
        turn.backup(vault, rel, turn_id, by="program")
        write_text_atomic(p, new)
        written.append(rel)
    return written


def related_names(data, mounts, names):
    """names 加上跟它們共用檔案卡的實體（改名、合併後要重渲染快取的那一圈）。"""
    out = set()
    for name in names:
        e, _hit = entities.find(data, name)
        if e is None:
            continue
        out.add(e["name"])
        out.update(related(data, mounts, e))
    return sorted(out)


# ---------- 待辦 ----------

def _question(d):
    name = d["name"]
    found = d.get("found") or ""
    folder, stem = (found.split("/") + ["", "", ""])[1], (found.rpartition("/")[2])[:-3]
    if d["kind"] in ("folder_renamed", "both_renamed"):
        return f"「{name}」的資料夾改成「{folder}」了？要跟著改名，還是改回來？"
    if d["kind"] == "card_renamed":
        return f"「{name}」的綜合卡檔名改成「{stem}」了？要採新名，還是改回來？"
    if d["kind"] == "moved_type":
        return f"「{name}」的資料夾搬到「{found.split('/')[0]}」底下了？要跟著改，還是搬回「{d['type']}」？"
    return f"「{name}」的資料夾（或綜合卡）不見了：是不要了，還是刪錯了？"


def _brief(m):
    return {"id": m["id"], "path": m.get("path"), "title": m.get("title")}


def buckets(vault, data, mounts, prof):
    """給 cards todo：回 synthesis_new、synthesis_stale、synthesis_broken、entity_drift 四桶（資料與介面格式）。只讀。"""
    min_cards = config.get(vault, "synthesis_min_cards")
    types = {entities.key(t["name"]) for t in prof.get("types") or []}
    drift_list = entities.drift(vault, data)
    drift_ids = {d["entity_id"] for d in drift_list}
    drift_found = {d["found"].casefold() for d in drift_list if d["found"]}
    today = date.today().isoformat()
    new, stale, broken = [], [], []
    listed = set()
    for e in data["entities"]:
        if e.get("status") == "merged" or entities.is_self(e) or not e.get("type"):
            continue
        rel = card_rel(e)
        ms = mounts.get(e["name"], [])
        p = Path(vault) / rel
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as err:
                broken.append({"path": rel, "reason": f"檔頭解析不了：{type(err).__name__}"})
                listed.add(rel.casefold())
                continue
            found = safe_problems(vault, rel, text, data, mounts, prof)
            if not found:
                continue
            if [c for c, _r in found] == [STALE_CODE]:
                stale.append(_stale_item(vault, e, rel, text, ms, data, mounts, prof, today))
            else:
                broken.append({"path": rel, "reason": found[0][1]})
                listed.add(rel.casefold())
            continue
        if e.get("status") != "active" or e.get("id") in drift_ids or entities.key(e["type"]) not in types:
            continue
        if len(ms) < min_cards:
            continue
        item = {"entity_id": e["id"], "name": e["name"], "type": e["type"], "category": category_text(prof, e["type"]),
                "confirmed": e.get("source") != "inferred", "path": rel, "cards": [_brief(m) for m in ms],
                "sections": sections_for(prof, e["type"]),
                "header": header_for(vault, e, ms, today, data, mounts, prof)}
        if not item["confirmed"]:
            item["question"] = (f"我要幫「{e['name']}」（{e['type']}，掛了 {len(ms)} 份檔）整理一頁現況："
                                f"名字這樣叫對嗎？它跟其他{e['type']}是同一件嗎？")
        new.append(item)
    by_id = {e.get("id"): e for e in data["entities"]}
    for eid, rels in sorted(entities.synthesis_files(vault).items()):
        for rel in rels:
            if rel.casefold() in drift_found or rel.casefold() in listed:
                continue
            owner = by_id.get(eid)
            if owner is not None and owner.get("type") and rel.casefold() == card_rel(owner).casefold():
                continue
            ok, reason = validate(vault, rel, data, mounts, prof)
            broken.append({"path": rel, "reason": reason if not ok else f"綜合卡該放在 {card_rel(owner)}"})
            listed.add(rel.casefold())
    drift_items = [dict(d, question=_question(d)) for d in drift_list]
    return {"synthesis_new": new, "synthesis_stale": stale, "synthesis_broken": broken, "entity_drift": drift_items}


def _stale_item(vault, e, rel, text, ms, data, mounts, prof, today):
    from kb._lib import cards
    front = cards.parse_front(text)
    have = {}
    for line in front.get("整理到") or []:
        cid, _sp, fp = str(line).partition(" ")
        have[cid] = fp.strip()
    titles = {}
    for line in front.get("檔案卡") or []:
        parts = str(line).split(" ", 2)
        if len(parts) == 3:
            titles[parts[0]] = parts[2]
    now = {m["id"]: m for m in ms}
    added = [_brief(now[c]) for c in sorted(now) if c not in have]
    changed = [_brief(now[c]) for c in sorted(now) if c in have and have[c] != (now[c].get("fp") or "")]
    gone = [c for c in sorted(have) if c not in now]
    groups = cards._scan_groups(vault)[0] if gone else {}
    removed = [{"id": c, "path": groups[c][0]["path"] if c in groups else None, "title": titles.get(c)} for c in gone]
    return {"entity_id": e["id"], "name": e["name"], "path": rel, "header": header_for(vault, e, ms, today, data, mounts, prof),
            "added": added, "changed": changed, "removed": removed}
