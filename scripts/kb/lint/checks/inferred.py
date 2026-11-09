"""清單 5（原 9）：推測放太久沒確認（lint_inferred_days 天）：AI 從檔案猜的日程（source inferred）、猜的實體名稱（inferred）、
設定檔來源是推測的列。各合成一件待處理（同一類一件）。日程那件帶各條的日期，快到期時先放著也會回到前面。"""
from kb._lib import agenda, plan, profile
from kb.lint import runner

NAME = "推測放太久"
PROFILE_SECTIONS = (("types", "name"), ("card_types", "name"), ("exclusions", "kind"), ("synthesis_sections", "type"),
                    ("about", "name"))
SHOWN = 5


def _since(it):
    hist = it.get("history") or []
    if hist and isinstance(hist[0], dict) and hist[0].get("date"):
        return hist[0]["date"]
    return str(it.get("created_turn") or "")[:8]


def _iso(day):
    day = str(day)
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else day


def _old(ctx, day, limit):
    n = ctx.days_since(_iso(day))
    return n is not None and n > limit


def _names(items):
    shown = "、".join(items[:SHOWN])
    return shown + ("…" if len(items) > SHOWN else "")


def _default_row(prof, sec, row):
    """程式建的預設列（kb init 的卡型與不整理種類、型別的大類預設段）不算我推測的，只看後來加或改過的（T-22）。"""
    if sec == "card_types":
        want = [{"name": s, "required": False, "hint": h} for s, h in profile.DEFAULT_SECTIONS]
        return row.get("name") in profile.CARD_TYPE_NAMES and row.get("sections") == want
    if sec == "exclusions":
        return row.get("kind") in profile.DEFAULT_EXCLUSIONS and row.get("action") == "kind_only"
    if sec == "synthesis_sections":
        defaults = {d["type"]: d["sections"] for d in profile.default_sections(prof.get("types") or [])}
        return row.get("type") in defaults and row.get("sections") == defaults[row.get("type")]
    return False


def run(ctx):
    limit = ctx.config("lint_inferred_days")
    out = []
    items = agenda.load(ctx.vault)["items"]  # 日程讀不到就丟出去：這項算沒查成，不把它的件當成已不成立（T-28）
    old = [it for it in items if it.get("status") == "open" and it.get("source") == "inferred" and _old(ctx, _since(it), limit)]
    if old:
        texts = [f"{it.get('date')} {it.get('text')}" for it in old]
        out.append(runner.finding(
            "inferred", "decide", "inferred:agenda",
            f"日程有 {len(old)} 條是我從檔案推測的，放了超過 {limit} 天還沒確認：{_names(texts)}",
            items=texts, data={"dates": sorted({it["date"] for it in old if it.get("date")}),
                               "results": [{"label": "一條一條看", "note": "kb agenda list 唸出推測的那幾條，對的跑 kb agenda edit <編號>（不帶欄位，"
                                                                         "記成確認），不對的照她說的改"}]}))
    ents = [e for e in ctx.entities()["entities"] if e.get("status") == "active" and e.get("source") == "inferred"
            and _old(ctx, e.get("date") or "", limit)]
    if ents:
        names = [e["name"] for e in ents]
        out.append(runner.finding(
            "inferred", "decide", "inferred:entity",
            f"有 {len(ents)} 個名字是我猜的，放了超過 {limit} 天還沒確認：{_names(names)}", items=names,
            data={"names": names, "results": [{"label": "名字都對",  # 名字照 shell_arg 加引號，給不出來的不給（T-9）
                                                "do": [c for c in (plan.cmd("entity", "confirm", n) for n in names) if c]}]},
            entity_ids=[e["id"] for e in ents]))
    prof = ctx.profile_strict()
    rows = [f"{sec}:{row.get(key)}" for sec, key in PROFILE_SECTIONS for row in prof.get(sec) or []
            if isinstance(row, dict) and row.get("source") == "inferred" and _old(ctx, row.get("date") or "", limit)
            and not _default_row(prof, sec, row)]
    if rows:
        out.append(runner.finding(
            "inferred", "decide", "inferred:profile",
            f"設定檔有 {len(rows)} 列是我推測的，放了超過 {limit} 天還沒確認",
            items=rows, data={"rows": rows, "results": [{"label": "一列一列看", "note": "kb profile show 唸出來源是推測的列，"
                                                                                     "使用者說對的，用 Edit 改設定檔（rows 裡「節:名稱」那一列的來源欄改成確認），再跑 kb profile apply；或照 note skill 改規則。"}]}))
    return out
