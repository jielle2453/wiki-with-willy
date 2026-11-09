"""第三類（D-149、D-150）：裁示升格。同種類、掛的卡同卡型的有效裁示到 ruling_promote_min 條 → 待處理一件；
AI 照 lint skill 擬一條設定檔偏好，她說好就 kb profile add 偏好 … 並逐條 kb ruling remove（data.rulings）。只提議，不封頂。"""
from kb._lib import rulings
from kb.lint import runner

NAME = "裁示升格"
KIND_TEXT = {"spoken_wins": "以你說的為準", "ruled_out": "別信"}


def run(ctx):
    cards = ctx.cards()
    groups = {}
    for r in ctx.rulings()["rulings"]:
        if not rulings.is_active(r) or r.get("kind") not in KIND_TEXT:
            continue
        types = {str(cards[c]["front"].get("卡型") or "") for c in rulings.card_ids(r) if c in cards}
        if len(types) != 1 or not next(iter(types)):
            continue
        groups.setdefault((r["kind"], types.pop()), []).append(r)
    out = []
    least = ctx.config("ruling_promote_min")
    for (kind, card_type), rows in sorted(groups.items()):
        if len(rows) < least:
            continue
        out.append(runner.finding(
            "rulings_promote", "decide", f"promote:{kind}:{card_type}",
            f"你說過 {len(rows)} 次「{card_type}」{KIND_TEXT[kind]}，要不要改成一條規則？",
            data={"kind": kind, "card_type": card_type, "rulings": [r["id"] for r in rows], "texts": [r.get("text") for r in rows],
                  "results": [{"label": "好，改成規則",
                               "note": "照 lint skill 擬一句偏好給她看，她說好就 kb profile add 偏好 偏好=<那句>，"
                                       "再照 data.rulings 逐條 kb ruling remove"}]}))
    return out
