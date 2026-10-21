"""查一個實體（answer 第 1 點，D-153）：kb lookup 的本體，只讀。

照 entities.find 認名稱、別名與合併轉址，一次給齊回答要的材料：實體、綜合卡在哪、掛著的檔案卡（原檔改過或標了重做的
stale、不採用的 ruled_out）、有效口述、有效裁示（口述為準排前面，D-149）、有效推算（各帶 stale）、這個實體 open 的日程、
全庫還沒整理的份數（cards.unsorted，跟 kb cards todo 第一行同一個數）。
名稱沒登記（或已移除）：entity、synthesis 是 null，mentions 列標題或正文出現這個詞的檔案卡（最多 lookup_mentions_max 張，
mentions_total 是全部份數；比對用 entities.key，第一版的卡也找得到）。
狀態檔不在當空的；壞了丟 ValueError（帶檔名）。不寫任何檔。
"""
from pathlib import Path

from kb._lib import config, entities

NOTHING = "沒有登記這個名稱，也沒有卡提到它"


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
    for entry, card in cards.active_cards(vault):
        title = cards._title(card)
        row = {"id": entry["id"], "title": title, "path": card["path"]}
        if k in entities.key(title):
            by_title.append(dict(row, where="標題"))
            continue
        try:
            text = (Path(vault) / card["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if k in entities.key(_body(text)):
            by_body.append(dict(row, where="正文"))
    return by_title + by_body


def lookup(vault, query):
    """回 {"query","entity","synthesis","cards","statements","rulings","findings","agenda","unsorted","mentions"}
    （沒登記多 mentions_total）。"""
    from kb._lib import agenda, cards, findings, inventory, rulings, statements, synthesis
    ent = entities.load(vault)
    unsorted = cards.unsorted(vault)
    e, _hit = entities.find(ent, query)
    out = {"query": query, "entity": None, "synthesis": None, "cards": [], "statements": [], "rulings": [], "findings": [],
           "agenda": [], "unsorted": unsorted, "mentions": []}
    if e is None or e.get("status") != "active":
        found = mentions(vault, query)
        limit = config.get(vault, "lookup_mentions_max")
        out.update(mentions=found[:limit], mentions_total=len(found))
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
    if e.get("type") and not entities.is_self(e):
        out["synthesis"] = entities.locate(vault, e, ent)
    out["cards"] = cards.newest_first([cards.card_brief(vault, by_id[m["id"]], groups[m["id"]][0], ruled_out)
                                       for m in ms if m["id"] in by_id and m["id"] in groups])
    out["statements"] = [synthesis.statement_brief(s) for s in statements.active_for(statements.load(vault), name)]
    titles = agenda.card_titles(vault)
    ruls = sorted(rulings.for_entity(rul, name, mounts), key=lambda r: r.get("kind") != "spoken_wins")
    out["rulings"] = [{"id": r["id"], "kind": r.get("kind"), "text": r.get("text"),
                       "cards": [{"id": cid, "title": titles.get(cid)} for cid in rulings.card_ids(r)]} for r in ruls]
    finds = findings.active_for(findings.load(vault), name, ent)
    if finds:
        ctx = findings.context(vault, ent, mounts, groups)
        out["findings"] = [findings.brief(vault, c, titles, ctx) for c in finds]
    out["agenda"] = _agenda(vault, name)
    return out
