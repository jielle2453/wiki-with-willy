"""查一個實體（answer 第 1 點，D-153）：kb lookup 的本體，只讀。

照 entities.find 認名稱、別名與合併轉址，一次給齊回答要的材料：實體、綜合卡在哪、掛著的檔案卡（原檔改過或標了重做的
stale、不採用的 ruled_out）、有效口述、有效裁示（口述為準排前面，D-149）、有效推算（各帶 stale）、這個實體 open 的日程、
全庫還沒整理的份數（cards.unsorted，跟 kb cards todo 第一行同一個數）。
名稱沒登記（或已移除）：entity、synthesis 是 null，mentions 列標題或正文出現這個詞的檔案卡（最多 lookup_mentions_max 張，
mentions_total 是全部份數；比對用 entities.key，第一版的卡也找得到）。
狀態檔不在當空的；壞了丟 ValueError（帶檔名）。不寫任何檔。
放寬丙：cards 每張多 archived（清冊封存的排後面）、卡上有口述為準裁示的多 rulings；實體封存了頂層 archived 是 true。
tags（標籤字典第四節第 6 點）：實體清單的標籤正本 [{"field","words"}]，照第八節的順序（D-505：沒有綜合卡的也有）；沒標過是空的。
#65 拿掉記帳：不再回 fields、money、owed_to（數字欄與帳都拿掉了；她講到的錢在口述裡）。
推算 stale 的那筆 text 是 null、多 redo「依據變了，要用就重算：<how>」（B-100）；findings.brief 不動，綜合卡待辦要原文。
"""
from pathlib import Path

from kb._lib import card_index, config, entities, profile

NOTHING = "沒有登記這個名稱，也沒有卡提到它"
REDO = "依據變了，要用就重算："


def _brief_entity(vault, e, prof):
    from kb._lib import synthesis
    return {"name": e["name"], "type": e.get("type"), "category": synthesis.category_text(prof, e.get("type")),
            "self": entities.is_self(e), "aliases": list(e.get("aliases") or []),
            "progress": e.get("progress") if e.get("progress") and entities.is_matter(vault, e) else None}


def _agenda(vault, name):
    from kb._lib import agenda
    k = entities.key(name)
    items = [i for i in agenda.load(vault)["items"] if i.get("status") == "open"
             and any(entities.key(n) == k for n in i.get("entities") or [])]
    items.sort(key=lambda i: (i.get("start") or i.get("date", ""), i.get("date", ""), i.get("time") or "", i.get("id", "")))
    out = []
    for i in items:
        row = {"id": i.get("id"), "kind": agenda.KIND_TEXT.get(i.get("kind"), i.get("kind")), "date": i.get("date"),
               "text": i.get("text"), "status": i.get("status")}
        for extra in ("start", "time"):
            if i.get(extra):
                row[extra] = i[extra]
        out.append(row)
    return out


def _body(text):
    lines = str(text).lstrip("﻿").splitlines()
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return ""


def mentions(vault, word):
    """標題或正文出現 word 的檔案卡（清冊 active 的），標題先、照編號排：[{"id","title","path","where"}]。"""
    from kb._lib import cards
    k = entities.key(word)
    if not k:
        return []
    by_title, by_body = [], []
    active = cards.active_cards(vault)  # 先讀卡（卡片索引在這裡跟上，#57），再問內文
    hits = card_index.body_hits(vault, [k])
    for entry, card in active:
        title = cards._title(card)
        row = {"id": entry["id"], "title": title, "path": card["path"]}
        if k in entities.key(title):
            by_title.append(dict(row, where="標題"))
            continue
        if hits is not None:
            if card["path"] in hits[k]:
                by_body.append(dict(row, where="正文"))
            continue
        try:
            text = (Path(vault) / card["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if k in entities.key(_body(text)):
            by_body.append(dict(row, where="正文"))
    return by_title + by_body


def _tags(entry, prof):
    return [{"field": t["field"], "words": entities.tag_words(entities.tag_value(entry.get("tags"), t["field"]))}
            for t in profile.tags_for(prof, entry.get("type")) if entities.tag_value(entry.get("tags"), t["field"]) is not None]


def _read_ranges(vault, rel):
    """綜合卡要讀的行號範圍（D-522：檔頭、相關檔、紀事不讀，同 synthesis.read_ranges）；沒卡或讀不到回 None。"""
    from kb._lib import synthesis
    if not rel:
        return None
    try:
        text = (Path(vault) / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return synthesis.read_ranges(text) or None


def lookup(vault, query):
    """回 {"query","entity","synthesis","read_ranges","cards","statements","rulings","findings","agenda","unsorted","mentions","verify_max",
    "in_progress"（算在這個實體下的製作中檔，同 cards.wip 的欄位，D-525；沒登記也沒卡提到時是全部製作中的檔，檔名有問的詞的在前，#37）}
    （沒登記多 mentions_total；沒卡提到再多 in_progress_total）。verify_max 是要逐份核對的份數上限（answer_verify_max，answer 裁定 6）；裁示帶日期（短標用）。"""
    from kb._lib import agenda, cards, findings, inventory, rulings, statements, synthesis
    ent = entities.load(vault)
    sorted_ = cards.light_sort(vault)
    unsorted = cards.unsorted(vault, sorted_=sorted_)
    e, _hit = entities.find(ent, query)
    out = {"query": query, "entity": None, "synthesis": None, "read_ranges": None, "cards": [], "statements": [], "rulings": [], "findings": [],
           "agenda": [], "unsorted": unsorted, "mentions": [], "verify_max": config.get(vault, "answer_verify_max"),
           "tags": [], "in_progress": []}
    if e is None or e.get("status") != "active":
        found = mentions(vault, query)
        limit = config.get(vault, "lookup_mentions_max")
        out.update(mentions=found[:limit], mentions_total=len(found))
        if not found:  # 還沒整理的檔沒有卡，可能就在裡面（#37）
            out["in_progress"], out["in_progress_total"] = wip_hint(vault, [query], sorted_=sorted_)
        return out
    prof = entities._profile(vault)
    name = e["name"]
    groups = cards._scan_groups(vault)[0]
    mounts = entities.mounts(vault, ent)
    by_id = {x.get("id"): x for x in inventory.load(vault)["entries"]}
    rul = rulings.load(vault)
    ruled_out = cards.ruled_out_ids(rul)
    ms = mounts.get(name, [])
    out["entity"] = _brief_entity(vault, e, prof)
    out["archived"] = bool(e.get("archived"))  # 實體封存了：不拿來答「現在」的事（放寬丙 12）
    if e.get("type") and not entities.is_self(e):
        out["synthesis"] = entities.locate(vault, e, ent)
        out["read_ranges"] = _read_ranges(vault, out["synthesis"])
        out["tags"] = _tags(e, prof)
    out["cards"] = cards.newest_first([cards.card_brief(vault, by_id[m["id"]], groups[m["id"]][0], ruled_out)
                                       for m in ms if m["id"] in by_id and m["id"] in groups])
    out["statements"] = [synthesis.statement_brief(s, vault) for s in statements.active_for(statements.load(vault), name)]
    titles = agenda.card_titles(vault)
    ruls = sorted(rulings.for_entity(rul, name, mounts), key=lambda r: r.get("kind") != "spoken_wins")
    out["rulings"] = [ruling_row(r, titles) for r in ruls]
    spoken = spoken_by_card(vault, rul)
    for c in out["cards"]:  # 卡上有口述為準裁示的，卡自己也帶（沒有就不帶這欄）
        if c["id"] in spoken:
            c["rulings"] = spoken[c["id"]]
    finds = findings.active_for(findings.load(vault), name, ent)
    if finds:
        ctx = findings.context(vault, ent, mounts, groups)
        out["findings"] = [_finding(findings.brief(vault, c, titles, ctx)) for c in finds]
    out["agenda"] = _agenda(vault, name)
    out["in_progress"] = [r for r in cards.wip(vault, sorted_=sorted_) if name in r["entities"]]  # 製作中（D-525）
    return out


def wip_hint(vault, words, names=(), sorted_=None):
    """lookup、find 找不到時附的製作中檔（#37）：全部 cards.wip，檔名含 words 任一個（同 mentions 的比對）或算在 names 底下的排前面；
    回 (前 lookup_mentions_max 份, 全部份數)。"""
    from kb._lib import cards
    keys = [k for k in (entities.key(w.strip()) for w in words) if k]
    rows = sorted(cards.wip(vault, sorted_=sorted_),
                  key=lambda r: not (any(k in entities.key(r["name"]) for k in keys) or set(names) & set(r["entities"])))
    return rows[:config.get(vault, "lookup_mentions_max")], len(rows)


def ruling_row(r, titles):
    """一條裁示的輸出格式（lookup 的 rulings 與卡上的 rulings 共用）。"""
    from kb._lib import rulings
    return {"id": r["id"], "kind": r.get("kind"), "text": r.get("text"), "date": r.get("date"),
            "cards": [{"id": cid, "title": titles.get(cid)} for cid in rulings.card_ids(r)]}


def spoken_by_card(vault, rul=None):
    """{卡編號: [裁示列]}：掛在那張卡上的有效「口述為準」裁示（放寬丙 13，D-333；格式同 lookup 的 rulings）。沒有就空的。"""
    from kb._lib import agenda, rulings
    rul = rulings.load(vault) if rul is None else rul
    rows = [r for r in rul["rulings"] if rulings.is_active(r) and r.get("kind") == "spoken_wins" and rulings.card_ids(r)]
    if not rows:
        return {}
    titles = agenda.card_titles(vault)
    out = {}
    for r in rows:
        row = ruling_row(r, titles)
        for cid in rulings.card_ids(r):
            out.setdefault(cid, []).append(row)
    return out


def _finding(row):
    """依據變了的推算不給結果文字（B-100）：text 換成 null、多 redo（算法），AI 才不會照寫舊數字。"""
    if not row["stale"]:
        return row
    return dict(row, text=None, redo=REDO + str(row.get("how") or ""))
