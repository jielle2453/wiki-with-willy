"""kb entity add／confirm／accept／rename／merge／remove／rebuild／clean／list／render／log／progress／suggest-close／alias／panel
（實體與綜合卡第 3 點，D-115～D-117、D-057；日程第 4 點，D-126、D-127；note 改知識第 4 點，D-148）。

實體清單只准這裡寫（I-080）：寫入的子子命令在 entities.json 的鎖裡讀改寫（等 30 秒，等不到「等不到鎖，沒有寫入」exit 3），
寫前備進回合；改到檔案卡就重寫 總目錄.md，最後重渲染受影響實體的綜合卡快取（synthesis.render_cache，備份帶 by program）。
progress 改事類實體的進行中／結案／擱置（結案連帶結束它的期間條目），suggest-close 記一條待處理等客戶答。
alias 加或拿掉別名：別名撞到另一個已登記的實體就不寫、exit 3，--json 帶問題與結果清單（同 asks 的形狀）改問合併；
拿掉時列出內文提到那個別名的檔案卡（suspect_cards）。
accept：AI 看掛的卡有把握的新名字直接登記成「AI 判斷」（source ai_judged，D-202；build 第 5 點），算確認、不出名字題。
list --tag 欄=詞[,詞]（標籤字典第四節，D-235）：照實體清單的標籤正本篩，照型別一段、第一句幾個符合與幾個還沒標過，只列符合的。
tag <名稱> 欄=詞[,詞] …（D-505）：寫標籤正本（欄= 後面空著是看過、沒有）；--said 是她親口說的，那一欄之後 AI 不重判；
有綜合卡就把那幾行照正本寫回卡上（備份 by program）。tag --from-card [<名稱>…]（裁定 6）：她在 Obsidian 手改了綜合卡的標籤行，
合字典的照她改的寫進正本、算 --said；不合字典的不寫、印問句（不給名稱就是 entities.tag_edits 全部）。
rename 搬了夾、夾裡有客戶原檔時，鎖外接著 inventory.scan（只掃知識庫）加 apply，讓原檔的新位置入冊（同 folders add）。
log <名稱> <行>…（D-521）：紀事由程式加進綜合卡（synthesis.add_events），--synced 加完標記整理到。
render [名稱…]：只重寫那幾張的快取欄；--synced 另換 整理到、更新日（D-518，AI 用 Edit 改完內文之後跑），有沒寫的 exit 1。
終端機跑的記一段工作日誌程式段（D-085），command 是 entity <子子命令>；對話裡叫的併進回合。
panel <名稱或編號> --json（D-340，唯讀、不開回合）：Obsidian 外掛在她打開綜合卡時叫，回這個實體的日程（還沒做完的、照 D-289
這台看得到的），每行帶要開的頁與那一行；外掛只畫（#65 起沒有帳與庫存）。
"""
import argparse
import json
from pathlib import Path

from kb import journal_cli
from kb._lib import agenda, cards, entities, inventory, pending, plan, streams, synthesis, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="entity_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="登記一個實體", description="登記一個實體（案子、業主…），先記成待確認。")
    ad.add_argument("--type", required=True, help="型別（客戶設定檔第一節的名稱）")
    ad.add_argument("--name", required=True, help="名稱")
    cf = subs.add_parser("confirm", help="客戶確認了名稱", description="記成客戶確認過的名稱。")
    cf.add_argument("ref", help="名稱或編號（e-…）")
    ac = subs.add_parser("accept", help="AI 判斷名字有把握，直接登記", description="AI 看掛的卡有把握：記成 AI 判斷，算確認、不問名字。")
    ac.add_argument("ref", help="名稱或編號（e-…）")
    rn = subs.add_parser("rename", help="改名或換型別", description="改名（舊名記成別名）、換型別；檔案卡與綜合卡一起改。")
    rn.add_argument("ref", help="名稱或編號（e-…）")
    rn.add_argument("--to", required=True, help="新名稱")
    rn.add_argument("--type", help="新型別")
    mg = subs.add_parser("merge", help="兩個是同一件，合併", description="來源併進目標：來源記成目標的別名。")
    mg.add_argument("ref", help="來源的名稱或編號")
    mg.add_argument("--into", required=True, help="目標的名稱或編號")
    rm = subs.add_parser("remove", help="不要了", description="記成已移除；知識不刪、檔案卡上的掛載留著，綜合卡拿掉。")
    rm.add_argument("ref", help="名稱或編號（e-…）")
    rb = subs.add_parser("rebuild", help="綜合卡不見了，重寫", description="綜合卡被刪了：回到待建綜合卡，照檔案卡重寫。")
    rb.add_argument("ref", help="名稱或編號（e-…）")
    cl = subs.add_parser("clean", help="清掉放錯的綜合卡", description="刪掉放錯位置的綜合卡（照對的位置寫好之後）。")
    cl.add_argument("path", help="放錯的綜合卡（相對總資料夾，例：01 案子/王宅/王宅.md）")
    ls = subs.add_parser("list", help="列出實體", description="列出實體、掛了幾張檔案卡、有沒有綜合卡。")
    ls.add_argument("--tag", action="append", help="照標籤篩：欄=詞（多個詞用逗號分開，是或）；可以重複（不同欄是且）")
    rd = subs.add_parser("render", help="重寫綜合卡的快取欄", description="重寫綜合卡檔頭的 檔案卡、相關實體 兩欄（沒給名稱就全部）；"
                         "--synced 另把 整理到、更新日 換成現行的（Edit 改完內文之後跑）。")
    rd.add_argument("names", nargs="*", metavar="名稱", help="實體名稱或別名；沒給就全部")
    rd.add_argument("--synced", action="store_true", help="整理到、更新日 也換成現行的（至少給一個名稱）")
    lg = subs.add_parser("log", help="綜合卡的紀事加幾行", description="照日期倒序把紀事加進綜合卡的「## 紀事」（D-521）；"
                         "一字不差已有的不加。--synced 加完再標記整理到。")
    lg.add_argument("ref", help="名稱、別名或編號（e-…）")
    lg.add_argument("lines", nargs="+", metavar="行", help="YYYY-MM-DD 事〔f-xxxxxxxx〕（出處可以多個〔〕）")
    lg.add_argument("--synced", action="store_true", help="加完把 整理到、更新日 換成現行的（同 render --synced）")
    lg.add_argument("--if-absent", action="store_true", help="這行的卡編號在紀事裡已經有任何一條就不加（md 簡單卡那條，D-526）")
    pg = subs.add_parser("progress", help="事類實體的狀態", description="事類實體記成進行中、結案或擱置；結案連帶結束它的期間條目。")
    pg.add_argument("ref", help="名稱或編號（e-…）")
    pg.add_argument("state", help="進行中、結案、擱置")
    pg.add_argument("--evidence", action="append", help="依據的檔案卡編號（f-…），可以重複")
    pg.add_argument("--note", help="一句白話（例：客戶說還沒結案）")
    for p in (pg, rn, mg):
        p.add_argument("--check", action="store_true", help="只算做了會怎樣（後果一句話，--json 回 effects 與 refuse），什麼都不寫")
    sc = subs.add_parser("suggest-close", help="看起來結案了，記一條待問", description="製卡者讀到結束證據：記一條待處理，由程式問客戶。")
    sc.add_argument("ref", help="名稱或編號（e-…）")
    sc.add_argument("--evidence", action="append", required=True, help="依據的檔案卡編號（f-…），可以重複")
    al = subs.add_parser("alias", help="加或拿掉別名", description="給實體加一個別名，或拿掉一個別名（綜合卡檔頭跟著改）。")
    al.add_argument("ref", help="名稱或編號（e-…）")
    which = al.add_mutually_exclusive_group(required=True)
    which.add_argument("--add", help="要加的別名")
    which.add_argument("--remove", help="要拿掉的別名")
    tg = subs.add_parser("tag", help="給實體標標籤", description="照設定檔第八節的字典給實體標標籤；有綜合卡的卡上那幾行跟著改。")
    tg.add_argument("ref", nargs="?", help="名稱或編號（e-…）")
    tg.add_argument("pairs", nargs="*", help="欄=詞（多個詞用逗號分開；欄= 後面空著是看過、沒有）；--from-card 時是更多名稱")
    tg.add_argument("--said", action="store_true", help="她親口說的：這一欄之後照她說的留著")
    tg.add_argument("--from-card", action="store_true", help="她在卡上手改了標籤行：合字典的照她改的記進正本")
    pn = subs.add_parser("panel", help="卡底下的日程（唯讀，外掛用）",
                         description="綜合卡底下「日程」那段的資料：日程（全隊的＋我的）；只讀、不開回合。")
    pn.add_argument("name", help="實體名稱、別名或編號（e-…）")
    for p in (ad, cf, ac, rn, mg, rm, rb, cl, ls, rd, lg, pg, sc, al, tg, pn):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


STATE_TEXT = {"inferred": "待確認", entities.AI_JUDGED: "AI 判斷"}


def _state(entry):
    return STATE_TEXT.get(entry.get("source"), "確認")


PROGRESS_BY_TEXT = {v: k for k, v in entities.PROGRESS_TEXT.items()}
PROGRESS_REFUSAL = "狀態要是 進行中、結案、擱置 之一"


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _refresh(vault, names, turn_id):
    """重渲染 names 與跟它們共用檔案卡的實體的綜合卡快取；回改到的路徑（日誌用）。"""
    data = entities.load(vault)
    return synthesis.render_cache(vault, synthesis.related_names(data, entities.mounts(vault, data), names), turn_id)


def _index(vault, turn_id, changed):
    cards.render_index(vault, turn_id)
    changed.append({"path": cards.INDEX_POSIX, "action": "modify"})


# ---------- 子子命令 ----------

def _add(vault, args, as_json, conversation, turn_id):
    entry, created = entities.add(vault, args.type.strip(), args.name.strip(), turn_id)
    if not created:
        _out(as_json, [f"已登記過：{entry['name']}（{entry['type']}）"], {"entity": entry, "created": False})
        return 0
    journal_cli.record_program_or_warn(vault, f"登記實體：{entry['name']}（{entry['type']}）",
                                       [{"path": entities.ENTITIES_POSIX, "action": "modify"}], "entity add", conversation)
    _out(as_json, [f"已登記：{entry['name']}（{entry['type']}，待確認）"], {"entity": entry, "created": True})
    return 0


def _confirm(vault, args, as_json, conversation, turn_id):
    entry = entities.confirm(vault, args.ref, turn_id)
    line = f"已確認：{entry['name']}（{entry.get('type') or '你自己'}）"
    journal_cli.record_program_or_warn(vault, line, [{"path": entities.ENTITIES_POSIX, "action": "modify"}],
                                       "entity confirm", conversation)
    _out(as_json, [line], {"entity": entry})
    return 0


def _accept(vault, args, as_json, conversation, turn_id):
    entry, how = entities.accept(vault, args.ref, turn_id)
    if how == "confirmed":
        _out(as_json, [f"已經確認過了：{entry['name']}"], {"entity": entry, "accepted": False})
        return 0
    line = f"已登記：{entry['name']}（AI 判斷）"
    if how == "accepted":
        journal_cli.record_program_or_warn(vault, line, [{"path": entities.ENTITIES_POSIX, "action": "modify"}],
                                           "entity accept", conversation)
    _out(as_json, [line], {"entity": entry, "accepted": how == "accepted"})
    return 0


def _rename(vault, args, as_json, conversation, turn_id):
    try:
        was = entities.find(entities.load(vault), args.ref)[0]
    except ValueError:
        was = None
    old_type = (was or {}).get("type")
    got = entities.rename(vault, args.ref, args.to.strip(), args.type.strip() if args.type else None, turn_id)
    old, new = got["old_name"], got["entity"]["name"]
    changed = list(got["changed"])
    settled = None
    if args.type and old_type and entities.key(old_type) != entities.key(got["entity"].get("type") or ""):
        from kb import profile_cli
        settled = profile_cli.settle_pending(vault, turn_id, old_type)
        if settled:
            changed += [{"path": profile_cli.profile.PROFILE_POSIX, "action": "modify"},
                        {"path": profile_cli.profile.MD_POSIX, "action": "modify"}]
    if got["moved_files"]:  # 夾裡的客戶原檔換了位置：只掃知識庫、套進清冊（鎖外，同 folders add）
        report = inventory.scan(vault, sources_lib.load(vault), only_roots=[inventory.KNOWLEDGE])
        if inventory.has_changes(vault, report):
            inventory.apply(vault, report, turn_id)
            changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    if got["cards"]:
        _index(vault, turn_id, changed)
    changed += [{"path": rel, "action": "modify"} for rel in _refresh(vault, [new], turn_id)]
    lines = []
    if got["via_alias"]:
        lines.append(f"「{args.ref}」是「{old}」的別名，改的是「{old}」")
    if entities.key(old) == entities.key(new):
        lines.append(f"已照「{new}」把資料夾與綜合卡改回來")
    else:
        lines.append(f"已改名：{old} → {new}（舊名記成別名）")
    if got["moved_files"]:
        lines.append(f"資料夾裡的 {got['moved_files']} 個檔跟著搬了，檔案卡下次整理會跟著改位置")
    if settled:
        lines.append(settled)
    journal_cli.record_program_or_warn(vault, f"實體改名：{old} → {new}", changed, "entity rename", conversation)
    _out(as_json, lines, {"entity": got["entity"], "old_name": old, "cards": got["cards"], "card": got["card"],
                          "moved_files": got["moved_files"]})
    return 0


def _move_leftovers(vault, src_folder, dst_folder, turn_id, conversation):
    """合併之後舊夾裡剩的檔：照 source-edit move 搬進目標的夾（搬、同名比內容、回合記 moves 與 folders），
    目標沒有夾就把舊夾改名過去，舊夾空了拿掉。回 (conflict 列, 搬了幾個, 別的拒絕句)。"""
    import os
    from kb._lib import source_edit as se
    from kb._lib.io import FileLock
    root = Path(vault)
    srcp, dstp = root / src_folder, root / dst_folder
    if not srcp.is_dir():
        return [], 0, []
    if not any(srcp.iterdir()):
        try:
            se.remove_empty_folder(vault, sources_lib.load(vault), srcp, turn_id)
        except se.Refused:
            pass
        return [], 0, []
    conflicts, refused, moved = [], [], 0
    with FileLock(root / sources_lib.SOURCES_REL):
        sources = sources_lib.load(vault)
        if not dstp.exists():  # 目標沒有夾：舊夾整個搬過去、改成目標的夾名
            plan = se.plan_move(vault, sources, str(srcp), str(root), dest=str(dstp.parent), to_name=dstp.name)
            got = se.do_move(vault, plan, turn_id, "move")
            journal_cli.record_program_or_warn(vault, se.move_sentence(plan, got, "move"), got["changed"],
                                               "entity merge", conversation)
            return [], plan["files"], []
        for item in sorted(srcp.iterdir(), key=lambda p: p.name.casefold()):
            try:
                plan = se.plan_move(vault, sources, str(item), str(root), dest=str(dstp))
                got = se.do_move(vault, plan, turn_id, "move")
            except se.Conflict as e:
                conflicts += list(e.rows)
                continue
            except se.Refused as e:
                refused.append(str(e))
                continue
            moved += 1
            journal_cli.record_program_or_warn(vault, se.move_sentence(plan, got, "move"), got["changed"],
                                               "entity merge", conversation)
        try:
            se.remove_empty_folder(vault, sources_lib.load(vault), srcp, turn_id)
        except se.Refused:
            pass
    return conflicts, moved, refused


def _merge(vault, args, as_json, conversation, turn_id):
    got = entities.merge(vault, args.ref, args.into, turn_id)
    src, dst = got["source"]["name"], got["target"]["name"]
    changed = list(got["changed"])
    if got["retyped"] and got["retyped"]["moved_files"]:  # 先換型別搬了夾：原檔的新位置入冊（同 rename）
        report = inventory.scan(vault, sources_lib.load(vault), only_roots=[inventory.KNOWLEDGE])
        if inventory.has_changes(vault, report):
            inventory.apply(vault, report, turn_id)
            changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    conflicts, moved, refused = _move_leftovers(vault, got["src_folder"], got["dst_folder"], turn_id, conversation)
    if any(c["path"].startswith(cards.CARDS_DIR + "/") for c in changed) or moved:
        _index(vault, turn_id, changed)
    changed += [{"path": rel, "action": "modify"} for rel in _refresh(vault, [dst], turn_id)]
    lines = [f"已合併：{src} 併進 {dst}（{src}記成別名）"]
    if got["retyped"]:
        lines.insert(0, f"「{src}」先從「{got['retyped']['from']}」換成「{got['retyped']['to']}」")
    if got["target_card"] and got["added"]:
        lines.append(f"{dst}的綜合卡下次整理會補上這 {len(got['added'])} 張")
    if moved:
        lines.append(f"舊夾裡的 {moved} 個檔搬進了「{dst}」的夾")
    payload = {"source": got["source"], "target": got["target"], "added": got["added"]}
    if conflicts or refused:
        names = "、".join(c["name"] for c in conflicts)
        lines.append(f"這些檔跟「{dst}」的夾裡同名、沒有搬（留在原夾）：{names}；照內容判斷取代或另取名，"
                     "用 kb source-edit move 搬（--replace 或 --name）。" if conflicts else "；".join(refused))
        lines.append("（實體已經合併了）")
        payload["refused"] = lines[-2]
        if conflicts:
            payload["conflict"] = conflicts
    journal_cli.record_program_or_warn(vault, f"合併實體：{src} 併進 {dst}", changed, "entity merge", conversation)
    _out(as_json, lines, payload)
    return 3 if (conflicts or refused) else 0


def _remove(vault, args, as_json, conversation, turn_id):
    got = entities.remove(vault, args.ref, turn_id)
    name = got["entity"]["name"]
    journal_cli.record_program_or_warn(vault, f"移除實體：{name}", got["changed"], "entity remove", conversation)
    _out(as_json, [f"已移除：{name}（知識不刪，檔案卡上的掛載留著）"], {"entity": got["entity"]})
    return 0


def _rebuild(vault, args, as_json, conversation, turn_id):
    got = entities.rebuild(vault, args.ref, turn_id)
    line = f"好，「{got['entity']['name']}」會照檔案卡重寫一張"
    journal_cli.record_program_or_warn(vault, line, got["changed"], "entity rebuild", conversation)
    _out(as_json, [line], {"entity": got["entity"]})
    return 0


def _rel(vault, text):
    p = Path(text)
    if p.is_absolute():
        return turn.rel_path(vault, str(p), str(vault))
    return str(text).replace("\\", "/").strip("/")


def _clean(vault, args, as_json, conversation, turn_id):
    got = entities.clean(vault, _rel(vault, args.path), turn_id)
    lines = [f"已清掉放錯的綜合卡：{got['path']}"]
    if got["left"]:
        lines.append(f"夾裡還有 {got['left']} 個檔，夾留著")
    journal_cli.record_program_or_warn(vault, f"清掉放錯的綜合卡：{got['path']}",
                                       [{"path": got["path"], "action": "delete"}], "entity clean", conversation)
    _out(as_json, lines, got)
    return 0


def _progress(vault, args, as_json, conversation, turn_id):
    state = PROGRESS_BY_TEXT.get(args.state.strip())
    if state is None:
        print(PROGRESS_REFUSAL)
        return 3
    note = args.note.strip() if args.note else None
    got = entities.set_progress(vault, args.ref, state, turn_id, evidence=args.evidence, note=note)
    name = got["entity"]["name"]
    if state == "closed":
        n = len(got["closed_periods"])
        lines = [f"已記成結案：{name}" + (f"（期間 {n} 條一起結束）" if n else "")]
    elif state == "paused":
        lines = [f"已記成擱置：{name}"]
    elif got["was"] == "ongoing":
        lines = [f"已記下：{name}還在進行中" + (f"（{note}）" if note else "")]
    else:
        lines = [f"已記成進行中：{name}"]
    if got["reopened"]:
        lines.append("期間條目沒有重開，要的話說改期")
    changed = [{"path": entities.ENTITIES_POSIX, "action": "modify"}]
    if got["closed_periods"]:
        changed += [{"path": agenda.AGENDA_POSIX, "action": "modify"}, {"path": agenda.MD_POSIX, "action": "modify"}]
    if got["pending_closed"]:
        changed += [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
    journal_cli.record_program_or_warn(vault, f"{name}記成{entities.PROGRESS_TEXT[state]}", changed, "entity progress",
                                       conversation)
    _out(as_json, lines, {"entity": got["entity"], "closed_periods": [p["id"] for p in got["closed_periods"]]})
    return 0


def _check(vault, args, as_json):
    from kb._lib import check
    if args.entity_command == "progress":
        state = PROGRESS_BY_TEXT.get(args.state.strip())
        if state is None:
            print(PROGRESS_REFUSAL)
            return 3
        got = check.progress(vault, args.ref, state)
    elif args.entity_command == "rename":
        got = check.rename(vault, args.ref, args.to.strip(), args.type.strip() if args.type else None)
    else:
        got = check.merge(vault, args.ref, args.into)
    return check.emit(got, as_json)


def _suggest_close(vault, args, as_json, conversation, turn_id):
    got = entities.suggest_close(vault, args.ref, args.evidence, turn_id)
    name = got["entity"]["name"]
    if got["recorded"]:
        line = f"已記下，會問客戶「{name}」要不要記成結案"
        journal_cli.record_program_or_warn(
            vault, f"記下結案待問：{name}",
            [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}],
            "entity suggest-close", conversation)
    elif got["skipped"] == "not_ongoing":
        line = f"「{name}」目前是{entities.PROGRESS_TEXT[got['entity'].get('progress') or 'ongoing']}，不用問結案"
    else:
        line = "這份依據問過了，不再問"
    _out(as_json, [line], {"entity": got["entity"], "recorded": got["recorded"], "skipped": got["skipped"],
                           "pending_id": got["pending_id"]})
    return 0


def _alias(vault, args, as_json, conversation, turn_id):
    word = (args.add if args.add is not None else args.remove).strip()
    got = entities.alias(vault, args.ref, add=word if args.add is not None else None,
                         remove=word if args.remove is not None else None, turn_id=turn_id)
    name = got["entity"]["name"]
    if got["merge_with"] is not None:
        other = got["merge_with"]["name"]
        refused = f"「{other}」已經登記成一個實體了，是同一個就合併"
        payload = {"refused": refused, "question": f"「{other}」已經登記成一個實體，跟{name}是同一個嗎？",
                   "results": plan._number([{"results": [
                       plan._result("是同一個，合併", [plan.cmd("entity", "merge", other, "--into", name)]),
                       plan._result("不是，先不加")]}])[0]["results"]}
        _out(as_json, [refused], payload)
        return 3
    if args.add is not None:
        line = f"已加別名：{name} 也叫 {word}" if got["added"] else f"{name} 本來就叫 {word}"
        note = f"加別名：{name} 也叫 {word}"
    else:
        n = len(got["suspect_cards"])
        line = f"已拿掉別名：{name} 不再叫 {word}" + (f"；有 {n} 張卡內文提到{word}，要看是不是掛錯了" if n else "")
        note = f"拿掉別名：{name} 不再叫 {word}"
    changed = list(got["changed"])
    if changed:
        changed += [{"path": rel, "action": "modify"} for rel in _refresh(vault, [name], turn_id)]
        journal_cli.record_program_or_warn(vault, note, changed, "entity alias", conversation)
    _out(as_json, [line], {"entity": got["entity"], "added": got["added"], "removed": got["removed"],
                           "suspect_cards": got["suspect_cards"]})
    return 0


TAG_KEPT = "（{fields}是你說過的，照你說的留著）"


def _tag_pairs(raw):
    """欄=詞[,詞] → {欄: [詞…]}；欄= 後面空著是 []（看過、沒有）。寫法不對丟 EntityRefused。"""
    out = {}
    for arg in raw:
        field, sep, rest = str(arg).partition("=")
        if not sep or not field.strip():
            raise entities.EntityRefused("要寫成 欄=詞（多個詞用逗號分開；欄= 後面空著是看過、沒有）")
        text = rest.strip()
        words = [] if text in ("", "[]", '""') else [w.strip() for w in entities._TAG_SPLIT.split(text) if w.strip()]
        out.setdefault(field.strip(), [])
        out[field.strip()] += [w for w in words if entities.key(w) not in {entities.key(x) for x in out[field.strip()]}]
    return out


def _tag_text(tags, fields):
    parts = []
    for f in fields:
        words = entities.tag_words(entities.tag_value(tags, f))
        parts.append(f"{f} {'、'.join(words) if words else '沒有'}")
    return "；".join(parts)


FROM_CARD_DONE = "照你在卡上改的記下了：{what}"
FROM_CARD_NONE = "卡上的標籤跟實體清單一樣，沒有要記的"


def _from_card(vault, args, as_json, conversation, turn_id):
    """tag --from-card：標籤行照她改的記回來。"""
    refs = [r for r in [args.ref, *args.pairs] if r] or None
    got = entities.copy_card_edits(vault, refs, turn_id)
    done = [g for g in got if g["written"]]
    lines = []
    if done:
        bits = [f"{g['entity']['name']} {_tag_text(g['entity'].get('tags') or {}, g['written'])}" for g in done]
        lines.append(FROM_CARD_DONE.format(what="；".join(bits)))
        journal_cli.record_program_or_warn(vault, lines[0], [{"path": entities.ENTITIES_POSIX, "action": "modify"}],
                                           f"entity {args.entity_command}", conversation)
    lines += [p for g in got for p in g["problems"]]
    _out(as_json, lines or [FROM_CARD_NONE],
         {"recorded": [{"name": g["entity"]["name"], "fields": g["written"], "tags": g["entity"].get("tags") or {}}
                       for g in done],
          "problems": [{"name": g["entity"]["name"], "field": row["field"], "card": v, "problem": text}
                       for g in got for (row, v), text in zip(g["bad"], g["problems"])]})
    return 0


def _tag(vault, args, as_json, conversation, turn_id):
    if args.from_card:
        return _from_card(vault, args, as_json, conversation, turn_id)
    if not args.ref or not args.pairs:
        raise entities.EntityRefused("要寫成 kb entity tag <名稱> 欄=詞 …（或 --from-card 把卡上手改的記進去）")
    got = entities.set_tags(vault, args.ref, _tag_pairs(args.pairs), turn_id, said=args.said)
    e = got["entity"]
    tags = e.get("tags") or {}
    fields = [f for f in _tag_pairs(args.pairs)]
    shown = [next((k for k in tags if entities.key(k) == entities.key(f)), f) for f in fields]
    changed = [{"path": entities.ENTITIES_POSIX, "action": "modify"}] if got["changed"] or args.said else []
    if got["card"]:
        rel = got["card"]
        text = (Path(vault) / rel).read_text(encoding="utf-8")
        new = synthesis.write_tag_lines(text, tags, shown)
        if new != text:
            turn.backup(vault, rel, turn_id, by="program")
            kio.write_text_atomic(Path(vault) / rel, new)
            changed.append({"path": rel, "action": "modify"})
    line = f"{e['name']}標了：{_tag_text(tags, shown)}"
    if got["kept"]:
        line += TAG_KEPT.format(fields="、".join(got["kept"]))
    if changed:
        journal_cli.record_program_or_warn(vault, line, changed, "entity tag", conversation)
    _out(as_json, [line], {"name": e["name"], "changed": got["changed"], "kept": got["kept"], "tags": tags,
                           "card": got["card"]})
    return 0


def _list(vault, as_json, tag_args=None):
    conds = entities.parse_tag_args(tag_args) if tag_args else None
    data = entities.load(vault)
    got = entities.tag_filter(vault, conds, data) if conds else None
    mounts = entities.mounts(vault, data)
    rows = []
    keep = None if got is None else set(got["matched"])
    for e in data["entities"]:
        if keep is not None and e.get("id") not in keep:
            continue
        where = entities.locate(vault, e) if e.get("card") and e.get("type") else None
        row = dict(e, mounted=len(mounts.get(e["name"], [])) if e.get("status") == "active" else 0,
                   has_card=where is not None)
        if got is not None:
            row["synthesis"] = where  # --tag：符合的綜合卡在哪（相對總資料夾），回答只讀這幾張
        rows.append(row)
    if as_json:
        payload = {"entities": rows} if got is None else {"entities": rows, "counts": got["counts"], "tag": got["tag"]}
        payload["conflicts"] = streams.conflicts_of(vault, "entities")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if got is not None:
        return _print_tagged(vault, rows, got)
    lines = [_row_line(vault, e) for e in rows if e.get("status") == "active"]
    print("\n".join(lines) if lines else "實體清單是空的")
    return 0


def _row_line(vault, e):
    if entities.is_self(e):
        return f"- {e['name']}（你自己）掛 {e['mounted']} 張"
    kind = f"{e['type']}，{_state(e)}" + (f"，{entities.PROGRESS_TEXT.get(e['progress'], e['progress'])}"
                                            if e.get("progress") and entities.is_matter(vault, e) else "")  # D-143
    return f"- {e['name']}（{kind}）掛 {e['mounted']} 張" + ("，有綜合卡" if e["has_card"] else "")


def _print_tagged(vault, rows, got):
    """照型別一段：第一句「客戶 200 個中 12 個符合；另有 15 個還沒照字典標過」（0 的那半不印），接著只列符合的。"""
    blocks = []
    for c in got["counts"]:
        head = f"{c['type']} {c['total']} 個中 {c['matched']} 個符合" + \
            (f"；另有 {c['untagged']} 個還沒照字典標過" if c["untagged"] else "")
        blocks.append("\n".join([head] + [_row_line(vault, e) for e in rows
                                          if entities.key(e.get("type")) == entities.key(c["type"])]))
    print("\n\n".join(blocks) if blocks else "第八節沒有哪個型別同時有這些欄")
    return 0


PANEL_NO_ENTITY = "實體清單沒有「{}」"


def _wd(day):
    """YYYY-MM-DD → 「11/25（三）」。"""
    d = agenda.dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}（{agenda.WEEKDAYS[d.weekday()]}）"


def panel_data(vault, name, today=None):
    """kb entity panel 的本體（D-340）：{"entity","type","agenda": [...]}。
    日程照 agenda.visible（這台這個人看得到的，D-289）、掛這個實體、還沒做完的（含逾期），每行帶 line（畫的字）、page、match
    （點了開日程頁的那一行）。名稱、別名、編號都認。"""
    data = entities.load(vault)
    text = str(name or "").strip()
    e = next((x for x in data["entities"] if x.get("id") == text), None) or entities.find(data, text)[0]
    if e is None or e.get("status") != "active":
        raise entities.EntityRefused(PANEL_NO_ENTITY.format(text))
    today = today or agenda._today()
    k = entities.key(e["name"])
    link = agenda.linker(vault, data)
    rows = []
    for it in sorted(agenda.visible(vault, agenda.load(vault)["items"]), key=agenda._order):
        if it.get("status") != "open" or not any(entities.key(n) == k for n in it.get("entities") or []):
            continue
        day = it.get("date")
        late = it.get("kind") in agenda.TRACKED and isinstance(day, str) and day < today
        span = agenda._span(it) if it.get("kind") == "period" else _wd(day)
        line = f"{span}{agenda.KIND_TEXT.get(it.get('kind'), it.get('kind'))}：{it.get('text')}"
        if late:
            line += f"（已過 {(agenda.dt.date.fromisoformat(today) - agenda.dt.date.fromisoformat(day)).days} 天）"
        rows.append({"id": it.get("id"), "date": day, "kind": agenda.KIND_TEXT.get(it.get("kind"), it.get("kind")),
                     "text": it.get("text"), "late": late, "line": line, "page": agenda.MD_POSIX,
                     "match": agenda._line(it, link) if it.get("kind") != "period" else None})
    return {"entity": e["name"], "type": e.get("type"), "agenda": rows}


def _panel(vault, args, as_json):
    got = panel_data(vault, args.name)
    lines = [f"{got['entity']}的日程"] + [f"- {a['line']}" for a in got["agenda"]]
    _out(as_json, lines, got)
    return 0


SYNCED_NEEDS_NAME = "--synced 要給至少一個實體名稱"


def _render(vault, args, as_json, conversation, turn_id):
    names = list(getattr(args, "names", None) or [])
    if getattr(args, "synced", False):
        written, skipped = synthesis.mark_synced(vault, names, turn_id)
    else:
        written, skipped = synthesis.render_cache(vault, names or None, turn_id), []
    line = f"重寫了 {len(written)} 張綜合卡的檔頭"
    changed = [{"path": rel, "action": "modify"} for rel in written]
    if written:
        journal_cli.record_program_or_warn(vault, line, changed, "entity render", conversation)
    _out(as_json, [line] + [f"沒寫 {s['name']}：{s['reason']}" for s in skipped], {"written": written, "skipped": skipped})
    return 1 if skipped else 0


def _log(vault, args, as_json, conversation, turn_id):
    got = synthesis.add_events(vault, args.ref, args.lines, turn_id, args.synced, args.if_absent)
    line = f"{got['name']}的紀事加了 {len(got['added'])} 行" + ("，已標記整理到" if got["synced"] else "")
    if got["written"]:
        journal_cli.record_program_or_warn(vault, line, [{"path": rel, "action": "modify"} for rel in got["written"]],
                                           "entity log", conversation)
    lines = [line] + [f"已經有了：{x}" for x in got["skipped"]]
    if args.synced and not got["synced"]:
        lines.append(f"沒標記整理到：{got['synced_reason']}")
    _out(as_json, lines, {k: got[k] for k in ("name", "added", "skipped", "synced")})
    return 1 if args.synced and not got["synced"] else 0


WRITERS = {"add": _add, "confirm": _confirm, "accept": _accept, "rename": _rename, "merge": _merge, "remove": _remove,
           "rebuild": _rebuild, "clean": _clean, "progress": _progress, "suggest-close": _suggest_close, "alias": _alias,
           "tag": _tag, "log": _log}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.entity_command
    try:
        if command == "list":
            return _list(vault, as_json, args.tag)
        if command == "panel":  # 唯讀，不開回合（外掛每開一張綜合卡就叫一次）
            return _panel(vault, args, as_json)
        if command == "render" and getattr(args, "synced", False) and not args.names:
            _out(as_json, [SYNCED_NEEDS_NAME], {"error": SYNCED_NEEDS_NAME})
            return 2
        if getattr(args, "check", False):  # 先算後果、什麼都不寫（也不開回合），Obsidian 的確認框用（#22，D-256）
            return _check(vault, args, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        if command == "render":
            return _render(vault, args, as_json, conversation, turn_id)
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except entities.EntityRefused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
