"""她手改綜合卡的標籤行（D-505 決策 6、六-4）：開對話時開場段已叫 AI 跑 kb entity tag --from-card（裁定 6），這裡每週補一次漏的。
檔頭第八節那個型別的欄跟實體清單的標籤正本不同、卡的修改時間晚於正本最後寫入（tags_at，裁定 5；沒寫過正本也算），算她在 Obsidian 改的
（判斷同 entities.card_diffs）。詞在字典裡、單選一個的照她改的抄進正本、算她說的（--said；D-223 認出她改的、
照她的意思做、做完在報告列出）；字典外或單選寫兩個的記一條待處理 tag_edit 問她（結果清單照 D-134）。空著的那一行是還沒標過，不算。
卡上那一行跟正本又一樣了，她那件 tag_edit 自動 done。"""
from kb._lib import entities, pending, plan, profile
from kb.lint import runner

NAME = "卡上改的標籤"
DONE_TEXT = "你在 {name} 的卡上把{what}，照你改的記下了"
ASK_TEXT = "{name}的卡上{what}，不在字典裡：要把詞加進字典，還是改回去？"
ASK_SINGLE = "{name}的卡上「{field}」寫了 {n} 個詞，這一欄只能選一個：要留哪一個？"
GONE_HOW = "卡上的標籤跟實體清單一樣了"


def _words_text(v):
    words = entities.tag_words(v)
    return "、".join(words) if words else "沒有"


def run(ctx):
    prof = ctx.profile()
    asking = {(i.get("entity_ids") or [None])[0] for i in pending.load(ctx.vault)["items"]
              if i.get("kind") == "tag_edit" and i.get("status") == "open"}
    out, settled = [], []
    for e, rel, text in ctx.synthesis():
        if not profile.tags_for(prof, e.get("type")):
            continue
        good, bad = entities.card_diffs(ctx.vault, e, prof, text=text)
        if not good and not bad:
            if e["id"] in asking:
                settled.append(e["id"])
            continue
        if not entities.card_newer(ctx.vault, rel, e):
            continue
        if good:
            out.append(runner.finding("tag_edits", "fix", f"tag_edit:{e['id']}", "", entity_ids=[e["id"]],
                                      apply=_copy(e, rel, good)))
        if bad:
            out.append(runner.finding("tag_edits", "fix", f"tag_edit_ask:{e['id']}", "", entity_ids=[e["id"]],
                                      apply=_ask(e, bad)))
    for eid in settled:
        pending.auto_done_entity(ctx.vault, eid, "tag_edit", GONE_HOW, ctx.turn_id)
    return out


def _copy(e, rel, good):
    def apply(ctx):
        got = entities.set_tags(ctx.vault, e["id"], good, ctx.turn_id, said=True, by="program")
        if not got["changed"]:
            return None
        entities._rewrite_dropped(ctx.vault, rel, got["entity"], ctx.turn_id)  # 整行拿掉的寫回空的（裁定 12）
        tags = got["entity"].get("tags") or {}
        what = "、".join(f"{f}改成{_words_text(entities.tag_value(tags, f))}" for f in good)
        return DONE_TEXT.format(name=e["name"], what=what)
    return apply


def _ask(e, bad):
    def apply(ctx):
        ask(ctx.vault, e, bad, ctx.turn_id)
        return None
    return apply


def ask(vault, e, bad, turn_id):
    """卡上不合字典的那幾欄記一條待處理 tag_edit（結果清單照 D-134）；bad 是 [(第八節那列, 卡上的值)]。
    健檢與 cards dispatch／batch（重寫綜合卡前先收她的手改，裁定 11）共用。"""
    fields, results, texts = [], [], []
    for row, v in bad:
        canon = entities.tag_value(e.get("tags"), row["field"])
        words = entities.tag_words(v)
        fields.append({"field": row["field"], "card": v, "tags": canon})
        back = f"{row['field']}={','.join(entities.tag_words(canon))}" if canon is not None else f"{row['field']}="
        if not row.get("multi") and len(words) > 1:
            texts.append(ASK_SINGLE.format(name=e["name"], field=row["field"], n=len(words)))
            results.append({"label": f"「{row['field']}」留她說的那一個",
                            "note": f"問她留哪一個，照她說的跑 kb entity tag {e['name']} {row['field']}=<那個詞> --said"})
        else:
            known = {entities.key(w) for w in row.get("words") or []}
            extra = [w for w in words if entities.key(w) not in known]
            texts.append(ASK_TEXT.format(name=e["name"], what=f"「{row['field']}」寫了{'、'.join(extra)}"))
            adds = [plan.cmd("profile", "add", "標籤", f"型別={row['type']}", f"欄名={row['field']}", f"詞={w}")
                    for w in extra]
            results.append({"label": f"把{'、'.join(extra)}加進「{row['field']}」的字典",
                            "do": adds + [plan.cmd("entity", "tag", e["name"],
                                                   f"{row['field']}={','.join(words)}", "--said")]})
        results.append({"label": f"「{row['field']}」改回去", "do": [plan.cmd("entity", "tag", e["name"], back)]})
    text = texts[0] if len(texts) == 1 else f"{e['name']}的卡上有 {len(texts)} 欄標籤不合字典：要加進字典還是改回去？"
    pending.add_tag_edit(vault, e["id"], text, {"fields": fields, "results": results}, turn_id)
