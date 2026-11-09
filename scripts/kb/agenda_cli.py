"""kb agenda add／edit／done／cancel／reopen／recheck／scanned／list／render／export（日程第 2 點，D-062、D-125～D-130、D-140）。

日程只准這裡寫（經 _lib/agenda）：每次在 agenda.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，沒有寫入」exit 3），
寫前備進回合，寫完重渲染 系統/日程.md。拒絕的第一行就是原因、exit 3；成功句與拒絕句一字不差（驗收 1～3）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合；歷程的 by 在對話裡是 ai、終端機是 program。
人讀輸出不印內部編號（a-…、f-…，確認後裁定 6）：客戶聽不懂，AI 還會唸出來；要編號的取 --json。
list 可篩（answer 第 8 點）：--from／--to 照 parse_date（YYYY-MM-DD），期間條目跟那段有重疊就列；--entity 可重複，別名與合併過的
照登記的名稱認。
export（D-500）：不帶參數立刻匯出一次；--set 先試寫一個暫存檔、過了才寫進 .system/config.json（寫前備進回合）再匯出；--off 只清設定、
不刪已經匯出的檔；--status 印設定與 .system/agenda_export.json。匯出失敗與拒絕 exit 1（spec 定的），白話原因。
"""
import argparse
import copy
import json
import os
import tempfile
from pathlib import Path

from kb import journal_cli
from kb._lib import agenda, config, inventory, paths, streams, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": agenda.AGENDA_POSIX, "action": "modify"}, {"path": agenda.MD_POSIX, "action": "modify"}]
FIELD_LABEL = (("start", "開始"), ("text", "說明"), ("time", "時間"), ("entities", "掛的實體"), ("lead_days", "提前天數"), ("repeat", "重複"),
               ("whose", "誰的"))


def add_arguments(sub):
    subs = sub.add_subparsers(dest="agenda_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="加一條日程", description="加一條日程：到期、會議、承諾、提醒、期間。")
    ad.add_argument("--kind", required=True, help="種類：到期、會議、承諾、提醒、期間")
    ad.add_argument("--date", required=True, help="日期 YYYY-MM-DD（期間是預計結束）")
    ad.add_argument("--text", required=True, help="一句話")
    ad.add_argument("--start", help="開始日期 YYYY-MM-DD（期間一定要有）")
    ad.add_argument("--time", help="時間 HH:MM")
    ad.add_argument("--entity", action="append", help="掛的實體名稱，可以重複")
    ad.add_argument("--card", help="出處的檔案卡編號（f-…），製卡抽出的才有，會標成推測")
    ad.add_argument("--lead-days", type=int, help="提前幾天提醒；沒給用 agenda_window_days")
    ad.add_argument("--repeat", help="每年、每月、每週（D-501）")
    ad.add_argument("--whose", help="全隊或名字（可用逗號分開幾個人）；沒給是全隊；製卡抽的一律全隊")
    ed = subs.add_parser("edit", help="改一條日程", description="改日期、開始、時間、說明、掛的實體、提前天數。")
    ed.add_argument("id", help="日程編號（a-…）")
    ed.add_argument("--date", help="新日期 YYYY-MM-DD")
    ed.add_argument("--start", help="新的開始日期")
    ed.add_argument("--time", help="新時間 HH:MM")
    ed.add_argument("--text", help="新的一句話")
    ed.add_argument("--entity", action="append", help="換成這些實體，可以重複")
    ed.add_argument("--lead-days", type=int, help="提前幾天提醒")
    ed.add_argument("--repeat", help="每年、每月、每週；不重複 是拿掉")
    ed.add_argument("--whose", help="改成全隊或名字（逗號分開幾個人）；製卡抽的一律全隊")
    ed.add_argument("--card", help="照這張卡的新版重對（只能是出處那張）：來源維持推測、記下新版的指紋")
    helps = {"done": "記成完成", "cancel": "取消", "reopen": "重開"}
    status_parsers = [subs.add_parser(name, help=text, description=f"把一條日程{text}。") for name, text in helps.items()]
    for p in status_parsers:
        p.add_argument("id", help="日程編號（a-…）")
    status_parsers[1].add_argument("--check", action="store_true", help="只算取消了會怎樣（--json 回 effects 與 refuse），什麼都不寫")
    rc = subs.add_parser("recheck", help="原檔新版看過了", description="製卡者看過出處卡的新版：不用改，或記下新版的寫法等客戶選。")
    rc.add_argument("id", help="日程編號（a-…）")
    rc.add_argument("--propose-date", help="新版寫的日期 YYYY-MM-DD（只收客戶說過或改過的條目）")
    rc.add_argument("--propose-text", help="新版寫的一句話")
    sc = subs.add_parser("scanned", help="這張卡的日期抽過了",
                         description="製卡者對這張卡抽完日期（沒抽到任何日期也要跑）：清冊記這張卡現在的指紋，卡重做前不再列待抽。")
    sc.add_argument("id", help="檔案卡編號（f-…）")
    ls = subs.add_parser("list", help="列出日程", description="預設列 open 的，照日期排；--all 含過去、完成、取消。")
    ls.add_argument("--all", action="store_true", help="全部列出（含過去、完成、取消，多人時也含別人的）")
    ls.add_argument("--from", dest="date_from", help="只列這天（YYYY-MM-DD）以後的")
    ls.add_argument("--to", dest="date_to", help="只列這天（YYYY-MM-DD）以前的")
    ls.add_argument("--entity", action="append", default=[], help="只列掛著這個實體的，可以重複")
    rd = subs.add_parser("render", help="重寫日程.md", description="手動重寫 系統/日程.md。")
    ex = subs.add_parser("export", help="把日程放到指定資料夾",
                         description="不帶參數立刻匯出一次 willy-agenda.json；--set 設定資料夾並匯出、--off 停止匯出、--status 看設定與上次結果。")
    mode = ex.add_mutually_exclusive_group()
    mode.add_argument("--set", dest="set_dir", metavar="<資料夾>", help="之後每次日程有變就放一份到這個資料夾（要已經存在、寫得進去）")
    mode.add_argument("--off", action="store_true", help="不再匯出（已經匯出的檔不刪）")
    mode.add_argument("--status", action="store_true", help="印設定的資料夾與上次匯出的結果")
    for p in [ad, ed, *status_parsers, rc, sc, ls, rd, ex]:
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _when(it):
    """「2026-11-30（週一）」：日期後接程式算的星期；期間寫成 開始～結束，有時間接在星期後面。"""
    when = agenda.with_weekday(it["date"])
    if it.get("kind") == "period" and it.get("start"):
        when = f"{agenda.with_weekday(it['start'])}～{when}"
    if it.get("time"):
        when += f"{it['time']} "
    return when


def _describe(it):
    """「2026-11-30（週一）交屋（到期，信義王）」。"""
    inside = [agenda.KIND_TEXT.get(it["kind"], it["kind"])] + list(it.get("entities") or [])
    return f"{_when(it)}{it['text']}（{'，'.join(inside)}）{agenda.repeat_suffix(it)}"


def _origin(vault, it):
    """推測的標「（推測，出處：<卡標題>）」；卡標題讀不到就只標「（推測）」。"""
    if it.get("source") != "inferred" or not it.get("origin"):
        return ""
    title = agenda.card_titles(vault).get(it["origin"].get("card"))
    return f"（推測，出處：{title}）" if title else "（推測）"


def _add(vault, args, as_json, conversation, turn_id):
    it, created = agenda.add(vault, args.kind.strip(), args.date, args.text, args.entity or [], turn_id, start=args.start,
                             time=args.time, card=args.card, lead_days=args.lead_days, repeat=args.repeat,
                             whose=args.whose)
    line = f"{_describe(it)}{_origin(vault, it)}"
    if not created:
        _out(as_json, [f"日程裡已經有這條：{line}"], {"item": it, "created": False})
        return 0
    journal_cli.record_program_or_warn(vault, f"加進日程：{it['date']} {it['text']}", CHANGED, "agenda add", conversation)
    _out(as_json, [f"已加進日程：{line}"], {"item": it, "created": True})
    return 0


def _changes(before, after):
    """(句子片段, 日誌用片段)：日期變了寫「說明 舊 → 新」，其他欄位寫「欄名 舊 → 新」。"""
    parts, labels = [], []
    if before.get("date") != after.get("date"):
        parts.append(f"{before['text']} {agenda.with_weekday(before['date'])}→ {agenda.with_weekday(after['date'])}")
    for key, label in FIELD_LABEL:
        old, new = agenda.show(before.get(key), key), agenda.show(after.get(key), key)
        if old != new:
            parts.append(f"{label}{' ' + old if old else ''} → {new}")
            labels.append(label)
    return parts, labels


def _edit(vault, args, as_json, conversation, turn_id):
    fields = {"date": args.date, "start": args.start, "time": args.time, "text": args.text, "entities": args.entity,
              "lead_days": args.lead_days, "repeat": args.repeat, "whose": args.whose}
    before = copy.deepcopy(agenda.get(vault, args.id))
    if all(v is None for v in fields.values()) and not args.card and before.get("source") != "inferred":
        print("沒有給要改的欄位")  # 推測的那條不帶欄位是「記成確認」（lint 桌面版確認後）
        return 3
    after = agenda.edit(vault, args.id, turn_id, by="ai" if conversation else "program", card=args.card, **fields)
    parts, labels = _changes(before, after)
    if not parts and before.get("source") == "inferred" and after.get("source") == "confirmed":
        journal_cli.record_program_or_warn(vault, f"日程記成確認：{after['text']}", CHANGED, "agenda edit", conversation)
        _out(as_json, [f"記成確認了：{after['text']}"], {"item": after, "changed": True})
        return 0
    if not parts:
        _out(as_json, [f"沒有要改的：{after['text']}"], {"item": after, "changed": False})
        return 0
    if before.get("date") != after.get("date"):
        note = f"改日程：{after['text']} {agenda.md_of(before['date'])} → {agenda.md_of(after['date'])}"
    else:
        note = f"改日程：{after['text']}（{'、'.join(labels)}）"
    journal_cli.record_program_or_warn(vault, note, CHANGED, "agenda edit", conversation)
    _out(as_json, ["已改：" + "；".join(parts)], {"item": after, "changed": True})
    return 0


def _status(vault, args, as_json, conversation, turn_id):
    command = args.agenda_command
    status = {"done": "done", "cancel": "cancelled", "reopen": "open"}[command]
    was = agenda.get(vault, args.id).get("date")
    it = agenda.set_status(vault, args.id, status, turn_id, by="ai" if conversation else "program")
    when = agenda.md_of(it["date"])
    if command == "done" and it.get("status") == "open":  # 重複的完成這一次（D-501）
        journal_cli.record_program_or_warn(vault, f"日程完成：{it['text']}（{agenda.md_of(was)}，下次 {when}）", CHANGED,
                                           "agenda done", conversation)
        _out(as_json, [f"已記成完成這一次：{it['text']}，下次 {agenda.with_weekday(it['date'])}"], {"item": it})
        return 0
    line, note = {"done": (f"已記成完成：{it['text']}", f"日程完成：{it['text']}"),
                  "cancel": (f"已取消：{when} {it['text']}", f"日程取消：{when} {it['text']}"),
                  "reopen": (f"已重開：{it['text']}", f"日程重開：{it['text']}")}[command]
    journal_cli.record_program_or_warn(vault, note, CHANGED, f"agenda {command}", conversation)
    _out(as_json, [line], {"item": it})
    return 0


def _recheck(vault, args, as_json, conversation, turn_id):
    it = agenda.recheck(vault, args.id, turn_id, propose_date=args.propose_date, propose_text=args.propose_text,
                        by="ai" if conversation else "program")
    prop = it.get("proposal")
    if prop:
        if prop["date"] != it["date"]:
            old, new = agenda.md_of(it["date"]), agenda.md_of(prop["date"])
            pair = f"{it['text']} {old}（客戶說的）↔ {new}（新版）"
        else:
            pair = f"「{it['text']}」（客戶說的）↔「{prop['text']}」（新版）"
        line = f"已記下新版的寫法，會問客戶要用哪個：{pair}"
    else:
        line = f"已對過新版，不用改：{it['text']}"
    journal_cli.record_program_or_warn(vault, f"日程對過新版：{it['text']}", CHANGED, "agenda recheck", conversation)
    _out(as_json, [line], {"item": it})
    return 0


def _scanned(vault, args, as_json, conversation, turn_id):
    out = agenda.mark_scanned(vault, args.id, turn_id)
    journal_cli.record_program_or_warn(vault, f"日程抽過：{out['title']}", [{"path": inventory.INVENTORY_POSIX, "action": "modify"}],
                                       "agenda scanned", conversation)
    _out(as_json, [f"已記下：「{out['title']}」的日期抽過了"], out)
    return 0


def _suffix(it):
    label = agenda.STATUS_TEXT.get(it.get("status"), "")
    return (agenda.INFERRED_MARK if it.get("source") == "inferred" else "") + label


def _keys(vault, names):
    """--entity 的名稱 → 登記名稱的 key 集合；沒登記的丟 AgendaRefused（同其他指令的說法）。"""
    from kb._lib import entities
    data = entities.load(vault)
    keys = set()
    for name in names:
        e, _hit = entities.find(data, name.strip())
        if e is None or e.get("status") != "active":
            raise agenda.AgendaRefused(f"實體清單沒有「{name.strip()}」，先 kb entity add")
        keys.add(entities.key(e["name"]))
    return keys


def _in_range(it, start, end):
    first, last = it.get("start") or it.get("date", ""), it.get("date", "")
    return (start is None or last >= start) and (end is None or first <= end)


def _list(vault, args, as_json):
    from kb._lib import entities
    start = agenda.parse_date(args.date_from).isoformat() if args.date_from else None
    end = agenda.parse_date(args.date_to).isoformat() if args.date_to else None
    keys = _keys(vault, args.entity) if args.entity else None
    items = agenda.load(vault)["items"]
    if not args.all:
        items = agenda.visible(vault, [i for i in items if i.get("status") == "open"])
    items = [i for i in items if _in_range(i, start, end)]
    if keys is not None:
        items = [i for i in items if any(entities.key(n) in keys for n in i.get("entities") or [])]
    items = sorted(items, key=lambda i: (i.get("start") or i.get("date", ""), i.get("date", ""), i.get("time") or "",
                                         i.get("id", "")))
    if as_json:
        print(json.dumps({"items": items, "conflicts": streams.conflicts_of(vault, "agenda")}, ensure_ascii=False))
        return 0
    if not items:
        print("日程是空的")
        return 0
    for it in items:
        names = it.get("entities") or []
        print(f"- {_when(it)}{agenda.KIND_TEXT.get(it['kind'], it['kind'])} {it['text']}"
              + (f"（{'、'.join(names)}）" if names else "") + agenda.repeat_suffix(it) + _suffix(it))
    return 0


def _render(vault, as_json, conversation, turn_id):
    data = agenda.load(vault)
    wrote = agenda.render(vault, turn_id, data)
    n = len(data["items"])
    if wrote:
        journal_cli.record_program_or_warn(vault, f"重寫日程.md：{n} 條", [{"path": agenda.MD_POSIX, "action": "modify"}],
                                           "agenda render", conversation)
    _out(as_json, [f"日程：{n} 條"], {"items": n, "written": wrote})
    return 0


EXPORT_NOT_SET = "還沒設定日程要放哪個資料夾；用 kb agenda export --set <資料夾> 設定"
CONFIG_POSIX = config.CLIENT_REL.as_posix()


def _folder_of(text):
    """她給的路徑 → 絕對路徑（正斜線）；Git Bash 的 /g/… 也認。"""
    return Path(os.path.abspath(paths._text_forms(text)[0])).as_posix()


def _writable(folder):
    """寫一個暫存檔再刪：寫得進去回 True。"""
    try:
        fd, name = tempfile.mkstemp(dir=folder, prefix=".willy-")
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def _save_dir(vault, folder, turn_id):
    """把 agenda_export_dir 寫進 .system/config.json（空字串＝拿掉這個鍵）；鎖裡讀改寫、寫前備進回合。"""
    from kb._lib import env
    env.set_agenda_export_dir(vault, folder)  # 每台自己的路徑，存這台的 env.json（多人共用 1）
    p = Path(vault) / config.CLIENT_REL
    with kio.FileLock(p):
        try:
            cur = kio.read_json(p, default={})
        except ValueError as e:
            raise ValueError(f"{CONFIG_POSIX}（JSON 壞了：{e}）") from e
        cur = {} if cur is None else cur
        if not isinstance(cur, dict):
            raise ValueError(f"{CONFIG_POSIX}（不是物件）")
        cur.setdefault("schema_version", 1)
        if folder:
            cur["agenda_export_dir"] = folder
        else:
            cur.pop("agenda_export_dir", None)
        turn.backup(vault, CONFIG_POSIX, turn_id)
        kio.write_json_atomic(p, cur)


def _export_now(vault):
    return agenda.export(vault)  # export 自己拿匯出的鎖；不握 agenda.json 的鎖，匯出夾再慢也不卡日程（T-1、T-3）


def _export_out(as_json, got):
    """印匯出結果；成功 exit 0，失敗 exit 1。"""
    if not got["ok"]:
        _out(as_json, [f"日程放不到 {got['dir']}：{got['reason']}"], {"ok": False, "reason": got["reason"]})
        return 1
    where = f"{got['dir']}/{agenda.EXPORT_NAME}"
    line = f"已放好日程：{where}（{got['items']} 條）" if got["written"] else f"日程沒變，{where} 不用更新（{got['items']} 條）"
    _out(as_json, [line], {"dir": got["dir"], "written": got["written"], "items": got["items"], "ok": True})
    return 0


def _export(vault, args, as_json):
    current = config.get(vault, "agenda_export_dir").strip()
    if args.status:
        state = agenda.export_state(vault)
        out = {"dir": current, "last_ok_at": state.get("last_ok_at"), "last_error_at": state.get("last_error_at"),
               "last_error": state.get("last_error")}
        lines = [f"日程放到：{current}" if current else "沒有設定匯出日程"]
        if out["last_ok_at"]:
            lines.append(f"上次放好：{out['last_ok_at']}")
        if out["last_error_at"]:
            lines.append(f"上次沒放成：{out['last_error_at']}（{out['last_error']}）")
        _out(as_json, lines, out)
        return 0
    if args.off:
        if not current:
            _out(as_json, ["本來就沒有匯出日程"], {"ok": True, "dir": "", "changed": False})
            return 0
        conversation = journal_cli.in_conversation(vault)
        _save_dir(vault, "", turn.current(vault)["turn"])
        journal_cli.record_program_or_warn(vault, "停止匯出日程", [{"path": CONFIG_POSIX, "action": "modify"}],
                                           "agenda export", conversation)
        _out(as_json, [f"已停止匯出日程；{current} 裡那份 {agenda.EXPORT_NAME} 沒刪"], {"ok": True, "dir": "", "changed": True})
        return 0
    if args.set_dir is not None:
        folder = _folder_of(args.set_dir) if args.set_dir.strip() else ""
        reason = None
        if not folder or not Path(folder).is_dir():
            reason = agenda.EXPORT_NO_DIR
        elif not _writable(folder):
            reason = agenda.EXPORT_NO_WRITE
        if reason:
            _out(as_json, [f"沒有設定：{args.set_dir} {reason}"], {"ok": False, "reason": reason})
            return 1
        conversation = journal_cli.in_conversation(vault)
        _save_dir(vault, folder, turn.current(vault)["turn"])
        journal_cli.record_program_or_warn(vault, f"日程匯出到：{folder}", [{"path": CONFIG_POSIX, "action": "modify"}],
                                           "agenda export", conversation)
        return _export_out(as_json, _export_now(vault))
    if not current:
        _out(as_json, [EXPORT_NOT_SET], {"ok": False, "reason": EXPORT_NOT_SET})
        return 1
    return _export_out(as_json, _export_now(vault))


WRITERS = {"add": _add, "edit": _edit, "done": _status, "cancel": _status, "reopen": _status, "recheck": _recheck,
           "scanned": _scanned}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.agenda_command
    try:
        if command == "list":
            return _list(vault, args, as_json)
        if command == "export":
            return _export(vault, args, as_json)
        if getattr(args, "check", False):  # 先算後果、什麼都不寫（也不開回合），Obsidian 的確認框用（#22，D-256）
            from kb._lib import check
            return check.emit(check.agenda_cancel(vault, args.id), as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        if command == "render":
            return _render(vault, as_json, conversation, turn_id)
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except agenda.AgendaRefused as e:
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
