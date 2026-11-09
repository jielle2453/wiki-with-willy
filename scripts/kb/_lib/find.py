"""kb find "<字>" 的本體（Obsidian 智慧搜尋，#22，D-257）：把她打的一串字拆成實體、卡型、標籤、時間詞，剩下的字比卡標題與內文。

只讀卡（檔案索引/）與綜合卡，不讀原檔；清冊 active 的才找得到（排除與 purge 清掉的本來就不在）。
拆字：先 NFKC 加 casefold（entities.key）；在整串裡由長到短找實體名稱與別名（合併轉址到目標、只認 active）、設定檔卡型、第八節標籤詞、
時間詞，長度一樣時實體 > 卡型 > 標籤 > 時間；剩下的照空白切成字。
比對：不同種類「且」、同種類「或」（兩個標籤詞中一個就算，裁定 11）。實體 → 掛它的卡；標籤 → 標了那詞的實體的卡；時間 → 卡的文件日期在範圍；字 → 標題（hit title）或內文（hit body）。
拆完一份都沒有時，整串去掉時間詞當字再找一次（裁定 12，JSON 多 fallback: true、parsed 換成第二次那份）。
排序：標題命中在前，再照日期新到舊；最多 find_results_max 份，more 說還有沒有。
"""
import datetime as dt
import re
import unicodedata
from pathlib import Path

from kb._lib import agenda, cards, config, entities, home, inventory, lookup, rulings
from kb._lib import sources as sources_lib

KIND_RANK = {"entity": 0, "type": 1, "tag": 2, "time": 3}
_YEAR_MONTH = re.compile(r"(?<![0-9])((?:19|20)\d\d)\s*年\s*(1[0-2]|0?[1-9])\s*月?")
_YEAR = re.compile(r"(?<![0-9])((?:19|20)\d\d)\s*年?(?![0-9])")
_MONTH = re.compile(r"(?<![0-9])(1[0-2]|0?[1-9])\s*月")
_PUNCT = " \t\r\n,，、。;；:：!！?？"
ENTITIES_MAX = 10  # 綜合卡內文有字的實體最多列幾個（名稱和別名認到的不限）


def _norm_chars(text):
    """逐字做 NFKC＋casefold，回 (正規化後的字串, 每個正規化字元對應原字串的位置)。"""
    out, where = [], []
    for i, ch in enumerate(text):
        piece = entities.key(ch)
        for c in piece:
            out.append(c)
            where.append(i)
    return "".join(out), where


def _week(day, back=0):
    start = day - dt.timedelta(days=day.weekday()) - dt.timedelta(weeks=back)
    return start, start + dt.timedelta(days=6)


def _month_range(year, month):
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def _time_fixed(today, recent_days):
    last_month = _month_range(today.year - (today.month == 1), (today.month - 2) % 12 + 1)
    return {
        "今天": (today, today), "昨天": (today - dt.timedelta(days=1),) * 2, "這週": _week(today), "這周": _week(today),
        "本週": _week(today), "上週": _week(today, 1), "上周": _week(today, 1),
        "這個月": _month_range(today.year, today.month), "本月": _month_range(today.year, today.month),
        "上個月": last_month, "上月": last_month,
        "今年": (dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)),
        "去年": (dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)),
        "最近": (today - dt.timedelta(days=recent_days), today),
    }


def _range_label(lo, hi, today):
    def one(d):
        return f"{d.month}/{d.day}" if d.year == today.year else f"{d.year}/{d.month}/{d.day}"
    return f"時間 {one(lo)}" if lo == hi else f"時間 {one(lo)}～{one(hi)}"


def _time_candidates(norm, today, recent_days):
    """時間詞候選 [(start, end, 範圍 (lo, hi))]：固定詞、年月、年、月。月份比今天晚就是去年。"""
    out = []
    for word, rng in _time_fixed(today, recent_days).items():
        w = entities.key(word)
        for m in re.finditer(re.escape(w), norm):
            out.append((m.start(), m.end(), rng))
    taken = []
    for m in _YEAR_MONTH.finditer(norm):
        out.append((m.start(), m.end(), _month_range(int(m.group(1)), int(m.group(2)))))
        taken.append((m.start(), m.end()))
    for m in _YEAR.finditer(norm):
        if not any(a <= m.start() < b for a, b in taken):
            y = int(m.group(1))
            out.append((m.start(), m.end(), (dt.date(y, 1, 1), dt.date(y, 12, 31))))
    for m in _MONTH.finditer(norm):
        if any(a <= m.start() < b for a, b in taken):
            continue
        month = int(m.group(1))
        out.append((m.start(), m.end(), _month_range(today.year - (month > today.month), month)))
    return out


def _vocabulary(vault, ent_data, prof):
    """(實體詞 {key: (目標實體, 命中的詞, 怎麼認到的)}, 卡型 {key: 名稱}, 標籤詞 {key: [(欄, 詞)]})。"""
    ents = {}
    by_id = {e.get("id"): e for e in ent_data["entities"]}
    for e in ent_data["entities"]:
        if e.get("type") is None and not e.get("self"):
            continue
        target = entities._resolve(ent_data, e)
        if target.get("status") != "active":
            continue
        merged = e is not target
        names = [(e.get("name"), "合併" if merged else "名稱")] + [(a, "合併" if merged else "別名") for a in e.get("aliases") or []]
        for word, how in names:
            if word:
                ents.setdefault(entities.key(word), (target, word, how))
    types = {entities.key(c["name"]): c["name"] for c in (prof or {}).get("card_types") or [] if c.get("name")}
    tags = {}
    for t in (prof or {}).get("tags") or []:
        for w in t.get("words") or []:
            tags.setdefault(entities.key(w), []).append((t["field"], w))
    return ents, types, tags, by_id


def parse(vault, q, today=None, ent_data=None, prof=None):
    """拆字：回 (parsed 清單, 內部用的條件)。parsed 每項 {"kind","text","label","value"}，照出現的順序。"""
    today = dt.date.fromisoformat(today or agenda._today())
    ent_data = ent_data if ent_data is not None else entities.load(vault)
    if prof is None:
        prof = cards._profile_or_default(vault)[0]
    norm, where = _norm_chars(q)
    ents, types, tags, _by_id = _vocabulary(vault, ent_data, prof)
    cands = []  # (長度, 種類排序, start, end, kind, payload)
    for k, (target, word, how) in ents.items():
        for m in re.finditer(re.escape(k), norm):
            cands.append((m.end() - m.start(), KIND_RANK["entity"], m.start(), m.end(), "entity", (target, word, how)))
    for k, name in types.items():
        for m in re.finditer(re.escape(k), norm):
            cands.append((m.end() - m.start(), KIND_RANK["type"], m.start(), m.end(), "type", name))
    for k, rows in tags.items():
        for m in re.finditer(re.escape(k), norm):
            cands.append((m.end() - m.start(), KIND_RANK["tag"], m.start(), m.end(), "tag", rows))
    for a, b, rng in _time_candidates(norm, today, config.get(vault, "find_recent_days")):
        cands.append((b - a, KIND_RANK["time"], a, b, "time", rng))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    used = [False] * len(norm)
    picked = []
    for length, _rank, a, b, kind, payload in cands:
        if length == 0 or any(used[a:b]):
            continue
        for i in range(a, b):
            used[i] = True
        picked.append((a, b, kind, payload))
    # 剩下的字：連續沒被認走的片段，照空白與標點切
    i = 0
    while i < len(norm):
        if used[i] or norm[i] in _PUNCT:
            i += 1
            continue
        j = i
        while j < len(norm) and not used[j] and norm[j] not in _PUNCT:
            j += 1
        picked.append((i, j, "word", None))
        i = j
    picked.sort(key=lambda p: p[0])
    parsed, cond = [], {"entity": [], "type": [], "tag": [], "time": [], "word": [], "time_spans": []}
    for a, b, kind, payload in picked:
        text = q[where[a]:where[b - 1] + 1].strip()
        if kind == "entity":
            target, word, how = payload
            parsed.append({"kind": "entity", "text": text, "label": f"實體 {text}", "value": target["name"]})
            cond["entity"].append((target, how, word))
        elif kind == "type":
            parsed.append({"kind": "type", "text": text, "label": f"卡型 {payload}", "value": payload})
            cond["type"].append(payload)
        elif kind == "tag":
            field, word = payload[0]
            parsed.append({"kind": "tag", "text": text, "label": f"標籤 {word}", "value": {"field": field, "word": word}})
            cond["tag"].append(payload)
        elif kind == "time":
            lo, hi = payload
            parsed.append({"kind": "time", "text": text, "label": _range_label(lo, hi, today),
                           "value": [lo.isoformat(), hi.isoformat()]})
            cond["time"].append((lo.isoformat(), hi.isoformat()))
            cond["time_spans"].append((where[a], where[b - 1] + 1))  # 在原字串 q 裡的位置，fallback 照這個拿掉（裁定 21）
        else:
            word = q[where[a]:where[b - 1] + 1]
            parsed.append({"kind": "word", "text": word, "label": f"字 {word}", "value": word})
            cond["word"].append(entities.key(word))
    return parsed, cond


def _card_entity_ids(ent_data, front):
    names = front.get("實體")
    ids = []
    for name in names if isinstance(names, list) else []:
        e, _hit = entities.find(ent_data, str(name))
        if e is not None and e.get("status") == "active":
            ids.append(e["id"])
    return ids


def _tag_entities(ent_data, tag_rows):
    """標了這個詞的實體編號：同一個詞在好幾欄時任一欄標了都算。"""
    out = set()
    for e in ent_data["entities"]:
        if e.get("status") != "active":
            continue
        have = e.get("tags") or {}
        for field, word in tag_rows:
            ws = have.get(field) if isinstance(have, dict) else None
            if isinstance(ws, str):
                ws = [ws]
            if any(entities.key(w) == entities.key(word) for w in ws or []):
                out.add(e["id"])
    return out


def _body(vault, path):
    try:
        text = (Path(vault) / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return entities.key(text[m.end():] if m else text)


def _fallback_words(q, parsed, cond):
    """拆完 0 筆時的第二次：整串去掉認出的時間詞，剩下的照空白與標點切成字。回 (parsed, cond)；沒有什麼好再試的回 None。"""
    main = [p for p in parsed if p["kind"] != "time"]
    if not any(p["kind"] in ("entity", "type", "tag") for p in main):
        return None
    if len(main) == 1 and main[0]["kind"] in ("entity", "type"):
        return None  # 整串就是一個認得的實體或卡型：沒有卡就是沒有，不拆錯
    rest = list(q)
    for a, b in cond["time_spans"]:  # 第一次拆字認到的時間詞那一段，不是字樣相同的第一處
        rest[a:b] = " " * (b - a)
    rest = "".join(rest)
    words = [w for w in re.split("[" + re.escape(_PUNCT) + "]+", rest) if w]
    if not words:
        return None
    again = [{"kind": "word", "text": w, "label": f"字 {w}", "value": w} for w in words]
    return again, {"entity": [], "type": [], "tag": [], "time": [], "word": [entities.key(w) for w in words], "time_spans": []}


def find(vault, q, today=None):
    """回 {"q","parsed","entities","files","total","more"}（格式見 spec 第 2 節）；第二次才找到的另加 "fallback": true；
    兩次都 0 筆另加 in_progress、in_progress_total（同 lookup.wip_hint，#37）。"""
    ent_data = entities.load(vault)
    prof = cards._profile_or_default(vault)[0]
    parsed, cond = parse(vault, q, today, ent_data, prof)
    out = _search(vault, q, parsed, cond, ent_data)
    if out["files"]:
        return out
    again = _fallback_words(q, parsed, cond)
    if again is not None:
        second = _search(vault, q, again[0], again[1], ent_data)
        second["fallback"] = True
        # 第一次認到的實體（沒有卡也要看得到）接在第二次的前面
        names = {e["name"] for e in second["entities"]}
        second["entities"] = [e for e in out["entities"] if e["name"] not in names] + second["entities"]
        out = second
    if not out["files"]:  # 還沒整理的檔沒有卡，可能就在裡面（#37）
        words = [p["text"] for p in parsed + (again[0] if again else []) if p["kind"] != "time"]
        out["in_progress"], out["in_progress_total"] = lookup.wip_hint(vault, words, [e["name"] for e in out["entities"]])
    return out


def _search(vault, q, parsed, cond, ent_data):
    cap = config.get(vault, "find_results_max")
    out = {"q": q, "parsed": parsed, "entities": [], "files": [], "total": 0, "more": False}
    if not parsed:
        return out
    # 實體：parse 認到的，加上綜合卡內文有字的
    seen = set()
    for target, how, _word in cond["entity"]:
        if target["id"] not in seen:
            seen.add(target["id"])
            out["entities"].append({"name": target["name"], "type": target.get("type"), "why": how})
    sources = sources_lib.load(vault)
    ent_ids = {t["id"] for t, _h, _w in cond["entity"]}
    tag_ids = set().union(*[_tag_entities(ent_data, rows) for rows in cond["tag"]]) if cond["tag"] else set()
    types = {entities.key(t) for t in cond["type"]}
    words = cond["word"]
    rul = rulings.load(vault)
    ruled = cards.ruled_out_ids(rul)
    rows = _scan(vault, cond, ent_data, ent_ids, tag_ids, types, words, ruled, all)
    if not rows and len(words) > 1:  # 每個字都中的沒有，才放寬成中一個就算（jacky 2026-11-09）
        rows = _scan(vault, cond, ent_data, ent_ids, tag_ids, types, words, ruled, any)
        if rows:
            out["some_words"] = True
    ordered = cards.newest_first([r[1] for r in rows])
    first = {r[1]["id"]: r for r in rows}
    titled = [b for b in ordered if first[b["id"]][0] == "title"]
    rest = [b for b in ordered if first[b["id"]][0] != "title"]
    final = sorted(titled + rest, key=lambda b: b["archived"])  # 封存的排在後面（放寬丙 12）
    spoken = lookup.spoken_by_card(vault, rul) if final else {}
    out["total"] = len(final)
    out["more"] = len(final) > cap
    for brief in final[:cap]:
        hit, _b, entry = first[brief["id"]]
        out["files"].append({"card": brief["id"], "title": brief["title"], "type": brief["card_type"], "date": brief["date"],
                             "entities": brief["entities"], "open": home._open_target(vault, sources, entry),
                             "card_path": brief["path"], "hit": hit, "archived": brief["archived"]})
        if brief["id"] in spoken:  # 卡上有口述為準裁示（放寬丙 13）：沒有就不帶這欄
            out["files"][-1]["rulings"] = spoken[brief["id"]]
    if words:
        out["entities"] += _synthesis_hits(vault, ent_data, words, seen)
    return out


def _scan(vault, cond, ent_data, ent_ids, tag_ids, types, words, ruled, need):
    """照條件篩卡；字用 need（all＝每個字都要在標題或內文、any＝中一個就算）。回 [(hit, brief, entry)]。"""
    rows = []
    for entry, card in cards.active_cards(vault):
        brief = cards.card_brief(vault, entry, card, ruled)
        front = card["front"]
        if cond["entity"] or cond["tag"]:
            mine = set(_card_entity_ids(ent_data, front))
            if cond["entity"] and not (mine & ent_ids):
                continue
            if cond["tag"] and not (mine & tag_ids):
                continue
        if types and entities.key(brief["card_type"]) not in types:
            continue
        if cond["time"]:
            d = brief["date"]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d) or not any(lo <= d <= hi for lo, hi in cond["time"]):
                continue
        hit = "filter"
        if words:
            title = entities.key(brief["title"])
            in_title = [w in title for w in words]
            if need(in_title):
                hit = "title"
            else:
                body = _body(vault, brief["path"])
                if not need(t or w in body for t, w in zip(in_title, words)):
                    continue
                hit = "title" if any(in_title) else "body"
        rows.append((hit, brief, entry))
    return rows


def _synthesis_hits(vault, ent_data, words, seen):
    """綜合卡內文有這些字的實體（還沒列進去的）。"""
    out = []
    for e in ent_data["entities"]:
        if e.get("status") != "active" or e.get("id") in seen or not e.get("type") or not e.get("card"):
            continue
        rel = entities.card_rel(e, vault)
        body = _body(vault, rel)
        if body and any(w in body for w in words):
            out.append({"name": e["name"], "type": e.get("type"), "why": "內文"})
            if len(out) >= ENTITIES_MAX:
                break
    return out
