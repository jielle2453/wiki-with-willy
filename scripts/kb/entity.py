"""kb entity add／confirm／rename／merge／remove／rebuild／clean／list／render／progress／suggest-close
（實體與綜合卡第 3 點，D-115～D-117、D-057；日程第 4 點，D-126、D-127）。

實體清單只准這裡寫（I-080）：寫入的子子命令在 entities.json 的鎖裡讀改寫（等 30 秒，等不到「等不到鎖，沒有寫入」exit 3），
寫前備進回合；改到檔案卡就重寫 總目錄.md，最後重渲染受影響實體的綜合卡快取（synthesis.render_cache，備份帶 by program）。
progress 改事類實體的進行中／結案／擱置（結案連帶結束它的期間條目），suggest-close 記一條待處理等客戶答。
rename 搬了夾、夾裡有客戶原檔時，鎖外接著 inventory.scan（只掃知識庫）加 apply，讓原檔的新位置入冊（同 folders add）。
終端機跑的記一段工作日誌程式段（D-085），command 是 entity <子子命令>；對話裡叫的併進回合。
"""
import argparse
import json
from pathlib import Path

from kb import journal_cli
from kb._lib import agenda, cards, entities, inventory, pending, synthesis, turn
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
    cl.add_argument("path", help="放錯的綜合卡（相對總資料夾，例：案子/王宅/王宅.md）")
    ls = subs.add_parser("list", help="列出實體", description="列出實體、掛了幾張檔案卡、有沒有綜合卡。")
    rd = subs.add_parser("render", help="重寫綜合卡的快取欄", description="重寫全部綜合卡檔頭的 檔案卡、相關實體 兩欄。")
    pg = subs.add_parser("progress", help="事類實體的狀態", description="事類實體記成進行中、結案或擱置；結案連帶結束它的期間條目。")
    pg.add_argument("ref", help="名稱或編號（e-…）")
    pg.add_argument("state", help="進行中、結案、擱置")
    pg.add_argument("--evidence", action="append", help="依據的檔案卡編號（f-…），可以重複")
    pg.add_argument("--note", help="一句白話（例：客戶說還沒結案）")
    sc = subs.add_parser("suggest-close", help="看起來結案了，記一條待問", description="製卡者讀到結束證據：記一條待處理，由程式問客戶。")
    sc.add_argument("ref", help="名稱或編號（e-…）")
    sc.add_argument("--evidence", action="append", required=True, help="依據的檔案卡編號（f-…），可以重複")
    for p in (ad, cf, rn, mg, rm, rb, cl, ls, rd, pg, sc):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _state(entry):
    return "待確認" if entry.get("source") == "inferred" else "確認"


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


def _rename(vault, args, as_json, conversation, turn_id):
    got = entities.rename(vault, args.ref, args.to.strip(), args.type.strip() if args.type else None, turn_id)
    old, new = got["old_name"], got["entity"]["name"]
    changed = list(got["changed"])
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
    journal_cli.record_program_or_warn(vault, f"實體改名：{old} → {new}", changed, "entity rename", conversation)
    _out(as_json, lines, {"entity": got["entity"], "old_name": old, "cards": got["cards"], "card": got["card"],
                          "moved_files": got["moved_files"]})
    return 0


def _merge(vault, args, as_json, conversation, turn_id):
    got = entities.merge(vault, args.ref, args.into, turn_id)
    src, dst = got["source"]["name"], got["target"]["name"]
    changed = list(got["changed"])
    if any(c["path"].startswith(cards.CARDS_DIR + "/") for c in changed):
        _index(vault, turn_id, changed)
    changed += [{"path": rel, "action": "modify"} for rel in _refresh(vault, [dst], turn_id)]
    lines = [f"已合併：{src} 併進 {dst}（{src}記成別名）"]
    if got["target_card"] and got["added"]:
        lines.append(f"{dst}的綜合卡下次整理會補上這 {len(got['added'])} 張")
    journal_cli.record_program_or_warn(vault, f"合併實體：{src} 併進 {dst}", changed, "entity merge", conversation)
    _out(as_json, lines, {"source": got["source"], "target": got["target"], "added": got["added"]})
    return 0


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


def _list(vault, as_json):
    data = entities.load(vault)
    mounts = entities.mounts(vault, data)
    rows = []
    for e in data["entities"]:
        has_card = bool(e.get("card")) and entities.locate(vault, e) is not None if e.get("type") else False
        rows.append(dict(e, mounted=len(mounts.get(e["name"], [])) if e.get("status") == "active" else 0,
                         has_card=has_card))
    if as_json:
        print(json.dumps({"entities": rows}, ensure_ascii=False))
        return 0
    lines = []
    for e in rows:
        if e.get("status") != "active":
            continue
        if entities.is_self(e):
            lines.append(f"- {e['name']}（你自己）掛 {e['mounted']} 張")
        else:
            kind = f"{e['type']}，{_state(e)}" + (f"，{entities.PROGRESS_TEXT.get(e['progress'], e['progress'])}" if e.get("progress") else "")
            lines.append(f"- {e['name']}（{kind}）掛 {e['mounted']} 張" + ("，有綜合卡" if e["has_card"] else ""))
    print("\n".join(lines) if lines else "實體清單是空的")
    return 0


def _render(vault, as_json, conversation, turn_id):
    written = synthesis.render_cache(vault, None, turn_id)
    line = f"重寫了 {len(written)} 張綜合卡的檔頭"
    if written:
        journal_cli.record_program_or_warn(vault, line, [{"path": rel, "action": "modify"} for rel in written],
                                           "entity render", conversation)
    _out(as_json, [line], {"written": written})
    return 0


WRITERS = {"add": _add, "confirm": _confirm, "rename": _rename, "merge": _merge, "remove": _remove,
           "rebuild": _rebuild, "clean": _clean, "progress": _progress, "suggest-close": _suggest_close}


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
            return _list(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        if command == "render":
            return _render(vault, as_json, conversation, turn_id)
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
