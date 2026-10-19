"""kb cards todo／sync／drop／index（ingest 製卡第 4 點，D-103、D-104、D-105）。

todo：讀清冊、卡與待處理現算該做的事，不寫任何檔、不開回合；--json 全列帶 header，人讀照 --limit（預設 ingest_auto_max）。
sync：moved 的卡搬到現在該在的夾（檔名不變；同夾只改檔頭），原檔清單與開原檔重算、其他行原樣；舊路徑備成 delete、新路徑備成 create。
drop <編號>：原檔不見（status missing）才做；先備清冊，卡備成 delete 後刪、條目改 deleted、對應 missing_file 待處理 done。
index：手動重渲染總資料夾根的 總目錄.md。sync、drop、index 都把 總目錄.md 備進回合（實作疑義裁定 4）。
skip <編號> --kind <種類>：客戶設定檔第三節說不整理的種類，清冊記 skip_kind、有卡的拿掉（客戶設定檔與型別第 6 點，D-113）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

from kb import journal_cli
from kb._lib import cards, config, inventory, pending, turn
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
    for p in (td, sy, dr, ix, sk):
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
    if new or stale or moved or unreadable or missing or broken or dups:
        first = (f"待製卡 {new + stale} 份（新 {new}、重做 {stale}）；搬動 {moved}；"
                 f"讀不到 {unreadable}、原檔不見 {missing}（見 系統/待處理.md）")
        if broken:
            first += f"；檔頭有問題 {broken} 張"
        if dups:
            first += f"；同編號多張 {dups} 組（跑 kb cards sync）"
        lines.append(first)
    else:
        lines.append("卡都是最新的")
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


def _todo(vault, args, as_json):
    data = cards.todo(vault, sources_lib.load(vault))
    if as_json:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    limit = args.limit if args.limit is not None else config.get(vault, "ingest_auto_max")
    print("\n".join(_todo_lines(vault, data, limit)))
    return 0


# ---------- sync ----------

_prune = cards.prune_dirs


def _remove_card(vault, rel, turn_id, changed):
    cards.remove_card(vault, rel, turn_id)
    changed.append({"path": rel, "action": "delete"})


def _card_id(vault, rel):
    try:
        return cards.parse_front((Path(vault) / rel).read_text(encoding="utf-8")).get("編號")
    except (ValueError, OSError, UnicodeDecodeError):
        return None


def _sync(vault, as_json, conversation):
    sources = sources_lib.load(vault)
    data = cards.todo(vault, sources)
    by_id = {e["id"]: e for e in inventory.load(vault)["entries"]}
    turn_id = turn.current(vault)["turn"]
    moved, updated, skipped, changed, dropped = [], [], [], [], []
    for dup in data["duplicates"]:  # 同編號多張：留正本，其餘備進回合後刪（測試後修正裁定 20）
        for rel in dup["drop"]:
            _remove_card(vault, rel, turn_id, changed)
            dropped.append(rel)
    for item in data["moved"]:
        entry = by_id[item["id"]]
        old = item["from"]
        name = old.rpartition("/")[2]
        new = f"{item['to']}/{name}"
        text = (Path(vault) / old).read_text(encoding="utf-8")
        text = cards.rewrite_locations(text, [loc["uri"] for loc in entry["locations"]],
                                       cards.entry_link(vault, sources, entry))
        if new.casefold() == old.casefold():
            turn.backup(vault, old, turn_id)
            kio.write_text_atomic(Path(vault) / old, text)
            updated.append(old)
            changed.append({"path": old, "action": "modify"})
            continue
        if (Path(vault) / new).exists():
            if _card_id(vault, new) == item["id"]:  # 目的地是同編號的卡：收斂成那張（裁定 20）
                _remove_card(vault, old, turn_id, changed)
                dropped.append(old)
            else:
                skipped.append(f"「{new}」已經有同名的卡，沒搬：{old}")
            continue
        turn.backup(vault, old, turn_id, action="delete")
        turn.backup(vault, new, turn_id)
        (Path(vault) / new).parent.mkdir(parents=True, exist_ok=True)
        kio.write_text_atomic(Path(vault) / new, text)
        os.remove(Path(vault) / old)
        _prune(vault, old)
        moved.append({"from": old, "to": new})
        changed += [{"path": old, "action": "delete"}, {"path": new, "action": "create"}]
    if moved or updated or dropped:
        cards.render_index(vault, turn_id)
        changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
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
    cards.render_index(vault, turn_id)
    changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
    name = title or name_of(entry["locations"][0]["uri"]).rpartition("/")[2]
    note = f"拿掉「{name}」的卡（原檔已刪）" if card else f"記下「{name}」已刪（沒有卡）"
    journal_cli.record_program_or_warn(vault, note, changed, "cards drop", conversation)
    if as_json:
        print(json.dumps({"id": fid, "card": card["path"] if card else None, "title": title}, ensure_ascii=False))
    elif card:
        print(f"已拿掉「{title}」的卡；說「退回上一版」救得回來")
    else:
        print(f"已記下「{name}」刪了（這份沒有卡）；說「退回上一版」救得回來")
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
        conversation = journal_cli.in_conversation(vault)
        if command == "sync":
            return _sync(vault, as_json, conversation)
        if command == "drop":
            return _drop(vault, args.id, as_json, conversation)
        if command == "skip":
            return _skip(vault, args.id, args.kind.strip(), as_json, conversation)
        return _index(vault, as_json, conversation)
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except TimeoutError:
        print("等不到鎖（別的指令正在改），沒有寫入；請稍後再試")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
