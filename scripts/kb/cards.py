"""kb cards todo／sync／drop／index（ingest 製卡第 4 點，D-103、D-104、D-105）。

todo：讀清冊、卡與待處理現算該做的事，不寫任何檔、不開回合；--json 全列帶 header，人讀照 --limit（預設 ingest_auto_max）。
sync：moved 的卡搬到現在該在的夾（檔名不變；同夾只改檔頭），原檔清單與開原檔重算、其他行原樣；舊路徑備成 delete、新路徑備成 create。
drop <編號>：原檔不見（status missing）才做；先備清冊，卡備成 delete 後刪、條目改 deleted、對應 missing_file 待處理 done。
index：手動重渲染總資料夾根的 總目錄.md。sync、drop、index 都把 總目錄.md 備進回合（實作疑義裁定 4）。
skip <編號> --kind <種類>：客戶設定檔第三節說不整理的種類，清冊記 skip_kind、有卡的拿掉（客戶設定檔與型別第 6 點，D-113）。
redo <編號>：客戶說卡讀錯了（D-147 第一種），清冊記 redo，下次 todo 把它列進 stale、帶 reason；
mount <編號> --entity <名>…：卡掛錯實體，重設檔頭的 實體 欄，重寫總目錄與兩邊綜合卡的快取（note 改知識第 5 點）。
list：列卡（answer 第 2 點，D-153、D-157），按卡型、年、實體、來源篩，窮舉不挑；另回庫裡實際有的卡型與份數，只讀、不開回合。
todo 在對話裡跑時在目前回合記 ingest（turn.mark，回合狀態不是知識）：主對話自己整理時被按停止，下一句的 UserPromptSubmit 認得出來（D-156）。
整批製卡（card-maker，D-195～D-200）：dispatch 開或沿用 job、切批、回每批給 Agent 工具的 prompt（終端機跑拒絕）；
batch <批次> 給 card-maker 自己那批的材料（只讀，帶 --agent 記上 agent_id）；status 看進度（只讀）；
stop 放掉這個 session 的批次、關 job、目前回合記 ingest_stopped（背景整理要停：說一聲，D-200）。
unreadable <編號> --reason：線上文件（link 條目）讀不到記進待處理（B-415；檔案讀不到由 convert 記）。
todo 的第一行接 待掛實體、綜合卡（待建、待更新、有問題）、實體夾有變、日程待重對、日程待抽；drop 拿掉卡也把從那張卡抽的推測日程改成取消（D-130）；sync、drop、skip 動到卡之後重渲染相關綜合卡的快取（實體與綜合卡第 4、5 點）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
from datetime import date
from pathlib import Path

from kb import journal_cli
from kb._lib import agenda, batches, cards, config, inventory, pending, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault, name_of

CLI_LOCK_TIMEOUT = 30
SKIPPED_TEXT = (("credential", "檔名像密碼或金鑰"), ("placeholder", "只在雲端"), ("too_large", "太大"),
                ("error", "讀不到"), ("skipped", "依設定不整理"))
PROFILE_MISSING = "客戶設定檔讀不到，卡型用預設"


def _limit(text):
    try:
        n = int(text)
    except ValueError:
        n = -1
    if n < 0:
        raise argparse.ArgumentTypeError(f"要是 0 以上的整數：{text}")
    return n


def _year(text):
    if not (len(text) == 4 and text.isdigit()):
        raise argparse.ArgumentTypeError(f"年份要寫成 YYYY：{text}")
    return text


def add_arguments(sub):
    subs = sub.add_subparsers(dest="cards_command", metavar="<子子命令>", required=True)
    td = subs.add_parser("todo", help="列出該做的卡", description="列出還沒有卡、該重做、該搬的卡；不寫檔。")
    td.add_argument("--limit", type=_limit, help="人讀輸出最多列幾份，預設 ingest_auto_max")
    sy = subs.add_parser("sync", help="原檔搬了，卡跟著搬", description="把 todo 裡 moved 的卡搬到原檔現在的位置。")
    dr = subs.add_parser("drop", help="拿掉原檔已刪的卡", description="原檔不見、客戶說刪了，拿掉它的卡。")
    dr.add_argument("id", help="清冊編號（f-…）")
    ix = subs.add_parser("index", help="重寫總目錄", description="重寫總資料夾根的 總目錄.md。")
    sk = subs.add_parser("skip", help="記成依設定不整理", description="客戶設定檔說不整理的種類：這份不做卡，有卡的拿掉。")
    sk.add_argument("id", help="清冊編號（f-…）")
    sk.add_argument("--kind", required=True, help="客戶設定檔第三節「不整理」的種類")
    rd = subs.add_parser("redo", help="卡讀錯了，標成要重做", description="客戶說卡讀錯原檔：標成要重做，下次整理時照原檔重寫。")
    rd.add_argument("id", help="清冊編號（f-…）")
    mt = subs.add_parser("mount", help="卡掛錯實體，改掛", description="重設這張卡掛的實體；原本與新掛的實體的綜合卡下次整理會更新。")
    mt.add_argument("id", help="清冊編號（f-…）")
    mt.add_argument("--entity", action="append", required=True, help="改掛的實體名稱，可以重複")
    ls = subs.add_parser("list", help="列卡（只讀）", description="按卡型、年、實體、來源列卡，窮舉不挑；另列庫裡實際有的卡型與份數。")
    ls.add_argument("--type", action="append", default=[], help="卡型（照字面），可以重複")
    ls.add_argument("--year", type=_year, help="文件日期的年 YYYY；沒寫日期的另外講份數")
    ls.add_argument("--entity", action="append", default=[], help="掛的實體名稱，可以重複")
    ls.add_argument("--source", help="原檔的來源別名")
    dp = subs.add_parser("dispatch", help="派背景製卡（對話裡用）", description="切批交給 card-maker，回每批的 prompt。")
    dp.add_argument("--source", help="只整理這個來源（別名）")
    bt = subs.add_parser("batch", help="card-maker 拿自己那批的材料（只讀）", description="列這一批要做的項目與要讀的規則。")
    bt.add_argument("batch", help="批次編號（b-…）")
    st = subs.add_parser("status", help="背景整理到哪了（只讀）", description="還在跑幾批、這次做好幾份、還剩幾份。")
    sp = subs.add_parser("stop", help="停背景整理", description="放掉這個對話的批次；回要停的子代理。")
    ur = subs.add_parser("unreadable", help="線上文件讀不到，記進待處理", description="只收線上文件（link 條目）。")
    ur.add_argument("id", help="清冊編號（out-… 或 f-…）")
    ur.add_argument("--reason", required=True, help="讀不到的原因，例：線上文件打不開")
    for p in (td, sy, dr, ix, sk, rd, mt, ls, dp, bt, st, sp, ur):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


# ---------- todo ----------

def _todo_lines(vault, data, limit):
    new, stale = len(data["new"]), len(data["stale"])
    moved, unreadable, missing, broken, dups = (len(data[k]) for k in ("moved", "unreadable", "missing", "broken",
                                                                      "duplicates"))
    lines = []
    if data.get("pdf_restored"):
        lines.append(f"PDF 元件裝好了，{data['pdf_restored']} 份 PDF 回到待製卡")
    extra = _entity_parts(data)
    if new or stale or moved or unreadable or missing or broken or dups or extra:
        first = (f"待製卡 {new + stale} 份（新 {new}、重做 {stale}）；搬動 {moved}；"
                 f"讀不到 {unreadable}、原檔不見 {missing}（見 系統/待處理.md）")
        if broken:
            first += f"；檔頭有問題 {broken} 張"
        if dups:
            first += f"；同編號多張 {dups} 組（跑 kb cards sync）"
        lines.append(first + "".join(f"；{x}" for x in extra))
    else:
        lines.append("卡都是最新的")
    if data.get("missing_types"):
        lines.append("型別表沒有：" + "、".join(f"{t} {n} 張" for t, n in data["missing_types"].items()))
    skipped = [f"{text} {data['skipped'][k]}" for k, text in SKIPPED_TEXT if data["skipped"][k]]
    if skipped:
        lines.append("不製卡：" + "、".join(skipped))
    work = [("新", i) for i in data["new"]] + [("重做", i) for i in data["stale"]]
    for label, item in work[:limit]:
        target = item["existing_card"] or f"{item['dir']}/{item['filename_pattern']}"
        lines.append(f"- {label} {item['id']} {name_of(item['uri'])} → {target}")
    if len(work) > limit:
        lines.append(f"……還有 {len(work) - limit} 份")
    if data.get("profile_missing"):
        lines.append(PROFILE_MISSING)
    return lines


def _entity_parts(data):
    """第一行後面接的實體與綜合卡幾段（實體與綜合卡第 5 點）；0 的項不印。"""
    parts = []
    if data.get("unlinked"):
        parts.append(f"待掛實體 {len(data['unlinked'])}")
    new = data.get("synthesis_new") or []
    unconfirmed = sum(1 for i in new if not i.get("confirmed"))
    bits = []
    if new:
        bits.append(f"待建 {len(new)}" + (f"（待確認 {unconfirmed}）" if unconfirmed else ""))
    if data.get("synthesis_stale"):
        bits.append(f"待更新 {len(data['synthesis_stale'])}")
    if data.get("synthesis_broken"):
        bits.append(f"有問題 {len(data['synthesis_broken'])}")
    if bits:
        parts.append("綜合卡 " + "、".join(bits))
    if data.get("entity_drift"):
        parts.append(f"實體夾有變 {len(data['entity_drift'])}")
    if data.get("agenda_recheck"):
        parts.append(f"日程待重對 {len(data['agenda_recheck'])}")
    if data.get("agenda_unscanned"):
        parts.append(f"日程待抽 {len(data['agenda_unscanned'])}")
    return parts


def _todo(vault, args, as_json):
    data = cards.todo(vault, sources_lib.load(vault))
    try:  # 回合狀態：記了才認得出「整理中被按停止」（D-156）；記不到不擋 todo
        turn.mark(vault, "ingest")
    except OSError:
        pass
    if as_json:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    limit = args.limit if args.limit is not None else config.get(vault, "ingest_auto_max")
    print("\n".join(_todo_lines(vault, data, limit)))
    return 0


# ---------- list ----------

def _list(vault, args, as_json):
    got = cards.listing(vault, types=[t.strip() for t in args.type], year=args.year,
                        names=[n.strip() for n in args.entity], source=args.source.strip() if args.source else None)
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
        return 0
    print("\n".join(_list_lines(got, args.year)))
    return 0


def _list_lines(got, year):
    rows = got["cards"]
    kinds = "、".join(f"{t['card_type'] or '（沒寫卡型）'} {t['count']}" + ("" if t["in_profile"] else "（設定檔沒有）")
                     for t in got["types"])
    lines = [f"共 {len(rows)} 張卡" + (f"；庫裡的卡型：{kinds}" if kinds else "")]
    if got["undated"]:
        lines.append(f"沒寫日期的 {got['undated']} 張" + ("，沒算進這一年" if year else ""))
    for c in rows:
        detail = "，".join(x for x in [c["card_type"]] + c["entities"] if x)
        mark = ("〔要重做〕" if c["redo"] else "〔原檔改過〕" if c["stale"] else "") + ("〔你說別信〕" if c["ruled_out"] else "")
        lines.append(f"- {c['date']} {c['title']}" + (f"（{detail}）" if detail else "") + mark)
    return lines


# ---------- sync ----------

def _remove_card(vault, rel, turn_id, changed):
    cards.remove_card(vault, rel, turn_id)
    changed.append({"path": rel, "action": "delete"})


def _sync(vault, as_json, conversation):
    turn_id = turn.current(vault)["turn"]
    got = cards.sync(vault, sources_lib.load(vault), turn_id)  # 本體在 _lib（型別改名與合併也叫它）
    moved, updated, skipped, dropped = got["moved"], got["updated"], got["skipped"], got["dropped"]
    changed, touched = got["changed"], got["touched"]
    if moved or updated or dropped:
        cards.render_index(vault, turn_id)
        changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
        cards.refresh_synthesis(vault, touched, turn_id)
        note = f"搬了 {len(moved)} 張卡" + (f"、刪掉重複卡 {len(dropped)} 張" if dropped else "")
        journal_cli.record_program_or_warn(vault, note, changed, "cards sync", conversation)
    if as_json:
        print(json.dumps({"moved": moved, "updated": updated, "dropped": dropped, "skipped": skipped},
                         ensure_ascii=False))
        return 0
    print(f"搬了 {len(moved)} 張卡、更新 {len(updated)} 張卡的原檔位置"
          + (f"；刪掉同編號的重複卡 {len(dropped)} 張" if dropped else ""))
    for line in skipped:
        print(line)
    return 0


# ---------- drop ----------

def _drop(vault, fid, as_json, conversation):
    sources = sources_lib.load(vault)
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid), None)
    if entry is None:
        print(f"清冊沒有 {fid}")
        return 3
    status = entry.get("status")
    if status == "active":
        path = inventory.path_of(vault, sources, entry["locations"][0]["uri"])
        print(f"原檔還在：{path}，不能拿掉卡")
        return 3
    if status != "missing":
        print(f"只能拿掉原檔不見的卡（{fid} 現在是 {status}）")
        return 3
    turn_id = turn.current(vault)["turn"]
    groups, _broken = cards._scan_groups(vault)
    group = groups.get(fid) or []
    card = group[0] if group else None
    changed = []
    title = None
    touched = cards.entity_names_of(group)
    # 先拿清冊的鎖再刪卡（測試後修正裁定 30）：等不到鎖就什麼都還沒動
    with kio.FileLock(Path(vault) / inventory.INVENTORY_POSIX):
        turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
        if card:
            title = card["front"].get("標題") or card["path"].rpartition("/")[2][:-3]
        for c in group:  # 同編號多張一起拿掉
            _remove_card(vault, c["path"], turn_id, changed)
        data = inventory.load(vault)
        for e in data["entries"]:
            if e.get("id") == fid:
                e["status"] = "deleted"
        inventory._write(vault, data["entries"])
    changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    how = f"客戶 {date.today().isoformat()} 說刪了"
    try:
        if pending.auto_done(vault, fid, "missing_file", how, turn_id):
            changed += [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
    except ValueError as e:  # pending.json 壞了：卡與清冊已改好，待處理下次 scan 補
        print(f"待處理沒更新：{e}")
    cancelled = []
    try:  # 從這張卡抽的推測日程跟著取消（D-130）；日程檔壞了或等不到鎖，卡與清冊已經改好，只說一聲
        cancelled = agenda.cancel_from_card(vault, fid, turn_id)
    except (ValueError, OSError) as e:
        print(f"日程沒更新：{e}")
    if cancelled:
        changed += [{"path": agenda.AGENDA_POSIX, "action": "modify"}, {"path": agenda.MD_POSIX, "action": "modify"}]
    cards.render_index(vault, turn_id)
    changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
    cards.refresh_synthesis(vault, touched, turn_id)
    name = title or name_of(entry["locations"][0]["uri"]).rpartition("/")[2]
    note = f"拿掉「{name}」的卡（原檔已刪）" if card else f"記下「{name}」已刪（沒有卡）"
    journal_cli.record_program_or_warn(vault, note, changed, "cards drop", conversation)
    if as_json:
        print(json.dumps({"id": fid, "card": card["path"] if card else None, "title": title,
                          "agenda_cancelled": [i["id"] for i in cancelled]}, ensure_ascii=False))
        return 0
    if card:
        print(f"已拿掉「{title}」的卡；說「退回上一版」救得回來")
    else:
        print(f"已記下「{name}」刪了（這份沒有卡）；說「退回上一版」救得回來")
    if cancelled:
        print(f"從這張卡抽的日程 {len(cancelled)} 條改成取消")
    return 0


# ---------- index ----------

def _index(vault, as_json, conversation):
    n = cards.render_index(vault, turn.current(vault)["turn"])
    journal_cli.record_program_or_warn(vault, f"總目錄：{n} 張卡", [{"path": cards.INDEX_POSIX, "action": "modify"}],
                                       "cards index", conversation)
    if as_json:
        print(json.dumps({"cards": n}, ensure_ascii=False))
    else:
        print(f"總目錄：{n} 張卡")
    return 0


# ---------- skip ----------

def _skip(vault, fid, kind, as_json, conversation):
    try:
        got = cards.skip(vault, sources_lib.load(vault), fid, kind, turn.current(vault)["turn"])
    except cards.SkipRefused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print("等不到鎖，沒有寫入")
        return 3
    changed = ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}]
               + [{"path": rel, "action": "delete"} for rel in got["removed"]]
               + [{"path": cards.INDEX_POSIX, "action": "modify"}])
    name = name_of(got["uri"]).rpartition("/")[2]
    journal_cli.record_program_or_warn(vault, f"依設定不整理：{kind}（{name}）", changed, "cards skip", conversation)
    if as_json:
        print(json.dumps({"id": fid, "kind": kind, "card": got["card"]}, ensure_ascii=False))
    else:
        print(f"已記成不整理（{kind}）：{got['uri']}；有卡的已拿掉，說「退回上一版」救得回來")
    return 0


# ---------- redo、mount ----------

def _redo(vault, fid, as_json, conversation):
    got = cards.mark_redo(vault, fid, turn.current(vault)["turn"])
    if got["changed"] or got["synthesis_paths"]:
        changed = ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if got["changed"] else []) + [
            {"path": rel, "action": "modify"} for rel in got["synthesis_paths"]]
        journal_cli.record_program_or_warn(vault, f"標成要重做：{got['title']}", changed, "cards redo", conversation)
    if as_json:
        print(json.dumps({"id": fid, "title": got["title"], "redo": got["redo"], "synthesis": got["synthesis"]},
                         ensure_ascii=False))
    else:
        print(f"已標成要重做：「{got['title']}」，下次整理時重寫")
        if got["synthesis"]:
            print("連帶要更新的綜合卡：" + "、".join(got["synthesis"]))
    return 0


def _mount(vault, fid, names, as_json, conversation):
    turn_id = turn.current(vault)["turn"]
    got = cards.mount(vault, fid, names, turn_id)
    now, was = "、".join(got["entities"]), "、".join(got["was"])
    if got["changed"]:
        changed = [{"path": got["path"], "action": "modify"}]
        cards.render_index(vault, turn_id)
        changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
        changed += [{"path": rel, "action": "modify"}
                    for rel in cards.refresh_synthesis(vault, list(got["was"]) + list(got["entities"]), turn_id)]
        journal_cli.record_program_or_warn(vault, f"改掛實體：{got['title']} → {now}", changed, "cards mount", conversation)
    if as_json:
        print(json.dumps({"id": fid, "title": got["title"], "entities": got["entities"], "was": got["was"]},
                         ensure_ascii=False))
    elif not got["changed"]:
        print(f"「{got['title']}」本來就掛在 {now}")
    else:
        print(f"已改：「{got['title']}」掛在 {now}（{'原本是 ' + was if was else '原本沒有掛實體'}）")
    return 0


# ---------- 背景製卡（card-maker） ----------

NOT_IN_CONVERSATION = "整理要在對話裡派"
NOT_RUNNING = "現在沒有在整理"
LINK_ONLY = "這個指令只收線上文件；檔案讀不到由 convert 記"
STOPPED_HERE = "這一句說了停，不再派；客戶說整理再派"


def _session(vault):
    """主對話的 session：current.json 的 session_id；終端機跑的沒有。"""
    data = turn._read_turn(turn._current_file(vault))
    return data.get("session_id") if data else None


def _left_text(left):
    parts = [f"{batches.KIND_TEXT[k]} {n}" for k, n in left.items() if n]
    return "還剩 " + "、".join(parts) if parts else "還剩 0"


def _dispatch(vault, source, as_json):
    sid = _session(vault)
    if not sid:
        print(NOT_IN_CONVERSATION)
        return 3
    got = batches.dispatch(vault, sid, turn.current(vault, sid)["turn"], source)
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
        return 0
    if got["stopped"]:
        print(STOPPED_HERE)
        return 0
    if got["done"]:
        print("整理完了")
        return 0
    made = got["batches"]
    head = (f"派了 {len(made)} 批（{batches.KIND_TEXT[made[0]['kind']]} " + "、".join(str(b["items"]) for b in made) + "）"
            if made else "沒有新派的")
    print(f"{head}；還在跑 {got['running']} 批；{_left_text(got['left'])}")
    if got["approval"]:
        print(got["approval"]["question"])
    return 0


def _batch(vault, batch_id, agent, as_json):
    try:
        got = batches.batch_view(vault, batch_id, agent)
    except batches.Refused as e:
        print(str(e))
        return 3
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
    else:
        print(f"批次 {batch_id}：{batches.KIND_TEXT[got['kind']]} {len(got['items'])} 項；先讀 " + "、".join(got["read"]))
    return 0


def _status(vault, as_json):
    got = batches.status(vault, _session(vault))
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
    elif got["job"] is None:
        print(f"{NOT_RUNNING}；還有 {got['waiting']} 份沒整理")
    else:
        run = "、".join(f"{batches.KIND_TEXT[b['kind']]} {b['items']}" for b in got["running"])
        print(f"正在整理：還在跑 {len(got['running'])} 批" + (f"（{run}）" if run else "")
              + f"；這次做好 {got['done']} 份；{_left_text(got['left'])}")
    return 0


def _stop(vault, as_json):
    sid = _session(vault)
    got = batches.stop(vault, sid, turn.current(vault, sid)["turn"] if sid else None)
    try:  # 這個對話不再自動整理（D-200）：照 D-156 的記號
        turn.mark(vault, "ingest_stopped")
    except OSError:
        pass
    if got is None:
        print(json.dumps({"agents": [], "done": 0, "left": None}, ensure_ascii=False) if as_json else NOT_RUNNING)
        return 0
    if as_json:
        print(json.dumps({"agents": got["agents"], "done": got["done"], "left": got["left"]}, ensure_ascii=False))
    else:
        print(f"已停：這次做好 {got['done']} 份，還剩 {got['left']} 份；這個對話不再自動整理，說「整理」再接著做")
    return 0


def _unreadable(vault, fid, reason, as_json, conversation):
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid), None)
    if entry is None:
        print(f"清冊沒有 {fid}")
        return 3
    if not inventory.is_link(entry) or entry.get("status") != "active":
        print(LINK_ONLY)
        return 3
    reason = " ".join(str(reason).split())
    label = f"「{entry.get('title') or entry['locations'][0]['uri']}」"
    turn_id = turn.current(vault)["turn"]
    pid = pending.add(vault, "unreadable", [fid], f"{label}讀不到", reason, turn_id)
    journal_cli.record_program_or_warn(vault, f"記下線上文件讀不到：{label}",
                                       [{"path": pending.PENDING_POSIX, "action": "modify"},
                                        {"path": pending.MD_POSIX, "action": "modify"}], "cards unreadable", conversation)
    if as_json:
        print(json.dumps({"id": fid, "pending_id": pid}, ensure_ascii=False))
    else:
        print(f"已記進待處理（{pid}）")
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.cards_command
    try:
        if command == "todo":
            return _todo(vault, args, as_json)
        if command == "list":
            return _list(vault, args, as_json)
        if command == "dispatch":
            return _dispatch(vault, args.source.strip() if args.source else None, as_json)
        if command == "batch":
            return _batch(vault, args.batch, getattr(args, "agent", None), as_json)
        if command == "status":
            return _status(vault, as_json)
        if command == "stop":
            return _stop(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        if command == "sync":
            return _sync(vault, as_json, conversation)
        if command == "drop":
            return _drop(vault, args.id, as_json, conversation)
        if command == "skip":
            return _skip(vault, args.id, args.kind.strip(), as_json, conversation)
        if command == "redo":
            return _redo(vault, args.id, as_json, conversation)
        if command == "mount":
            return _mount(vault, args.id, [n.strip() for n in args.entity], as_json, conversation)
        if command == "unreadable":
            return _unreadable(vault, args.id, args.reason, as_json, conversation)
        return _index(vault, as_json, conversation)
    except cards.CardRefused as e:
        print(str(e))
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except TimeoutError:
        print("等不到鎖（別的指令正在改），沒有寫入；請稍後再試")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
