"""kb statement add／withdraw／restore／list（note 改知識第 3 點，D-146；restore 與 withdraw 帶被取代那條是 lint 題，D-224）。

口述清單只准這裡與 entities 寫（經 _lib/statements）：每次在 statements.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，
沒有寫入」exit 3），寫前備進回合。拒絕的第一行就是原因、exit 3；成功句與拒絕句一字不差。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。人讀輸出不印 s-… 編號（日程確認後裁定 6），要編號取 --json。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import credentials, statements, streams, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": statements.STATEMENTS_POSIX, "action": "modify"}]
STATUS_SUFFIX = {"superseded": "（已被取代）", "withdrawn": "（已收回）"}
REPLACED_LINE = "它取代過：{text}（{date}）；要恢復的話跟我說"
RESTORED_LINE = "恢復了：{}（說「退回」可以改回去）"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="statement_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="記一條客戶口述", description="記一句客戶說的事實或決定，掛在登記過的實體上。")
    ad.add_argument("--entity", action="append", default=[], help="掛的實體名稱，可以重複（客戶自己也可以）；帶 --money 的可以不掛")
    ad.add_argument("--text", required=True, help="一句話，最多 200 字")
    ad.add_argument("--replaces", help="改的是哪一條舊口述（s-…）：舊的記成被這條取代")
    ad.add_argument("--money", nargs=2, metavar=("收或付", "金額"),
                    help="錢的話加這個記號（例 --money 收 5000）：問加總時 kb statement sum 照它算（D-343）")
    wd = subs.add_parser("withdraw", help="收回一條口述", description="客戶說沒這回事：這條記成已收回（紀錄留著）。")
    wd.add_argument("id", help="口述編號（s-…）")
    rs = subs.add_parser("restore", help="恢復被取代的那條", description="取代它的那條已經收回：原本那條改回有效。")
    rs.add_argument("id", help="口述編號（s-…）")
    ls = subs.add_parser("list", help="列出口述", description="預設列有效的；--all 含被取代與收回的。")
    ls.add_argument("--entity", help="只列掛在這個實體上的")
    ls.add_argument("--all", action="store_true", help="全部列出")
    ls.add_argument("--money", action="store_true", help="只列標過錢的（D-343）")
    sm = subs.add_parser("sum", help="區間內口述的錢加總", description="照標過錢（--money）的口述算區間內的收、付、淨與逐筆。")
    sm.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD", help="起日")
    sm.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD", help="迄日")
    sm.add_argument("--entity", help="只算掛了這個實體的")
    sm.add_argument("--vs-from", metavar="YYYY-MM-DD", help="比較的起日（要跟 --vs-to 一起給）")
    sm.add_argument("--vs-to", metavar="YYYY-MM-DD", help="比較的迄日")
    for p in (ad, wd, rs, ls, sm):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _masked(vault, text):
    """程式段的 note 不遮值（D-086），口述原文先遮再組句：修正前記下的舊口述可能含密碼（B-476）。"""
    return credentials.mask_secrets(text, credentials.known_names(vault))


def _add(vault, args, as_json, conversation, turn_id):
    money = statements.money_mark(*args.money) if args.money else None
    it, old = statements.add(vault, args.text, [n.strip() for n in args.entity], turn_id,
                             replaces=args.replaces.strip() if args.replaces else None, money=money)
    if old is None:
        where = f"（{'、'.join(it['entities'])}）" if it["entities"] else ""
        line, note = f"已記下：{it['text']}{where}", f"記下口述：{_masked(vault, it['text'])}"
    else:
        line = f"已改：{old['text']} → {it['text']}"
        note = f"改口述：{_masked(vault, old['text'])} → {_masked(vault, it['text'])}"
    journal_cli.record_program_or_warn(vault, note, CHANGED, "statement add", conversation)
    _out(as_json, [line], {"statement": it, "replaced": old})
    return 0


def _withdraw(vault, args, as_json, conversation, turn_id):
    it = statements.withdraw(vault, args.id.strip(), turn_id)
    journal_cli.record_program_or_warn(vault, f"收回口述：{_masked(vault, it['text'])}", CHANGED, "statement withdraw",
                                       conversation)
    try:  # 收回已經寫好：封存檔讀不到只記 log，成功句照印（B-454）
        old = statements.replaced_of(vault, it["id"])
    except (ValueError, OSError) as e:  # 壞檔、鎖住或沒權限都一樣（TimeoutError 也是 OSError）
        from kb._lib import ledger
        ledger.log_error(vault, f"收回後讀不到封存檔：{type(e).__name__}: {e}", source="statement")
        old = None
    lines = [f"已收回：{it['text']}"] + ([REPLACED_LINE.format(text=old.get("text"), date=old.get("date"))] if old else [])
    _out(as_json, lines, {"statement": it, "replaced": old})
    return 0


def _restore(vault, args, as_json, conversation, turn_id):
    it = statements.restore(vault, args.id.strip(), turn_id)
    journal_cli.record_program_or_warn(vault, f"恢復口述：{_masked(vault, it['text'])}", CHANGED, "statement restore",
                                       conversation)
    _out(as_json, [RESTORED_LINE.format(it["text"])], {"statement": it})
    return 0


def _list(vault, args, as_json):
    from kb._lib import entities
    data = statements.load(vault)
    items = data["statements"] if args.all else [s for s in data["statements"] if s.get("status") == "active"]
    if args.entity:
        k = entities.key(args.entity.strip())
        found, _hit = entities.find(entities.load(vault), args.entity.strip())
        if found is not None:
            k = entities.key(found["name"])
        items = [s for s in items if any(entities.key(n) == k for n in s.get("entities") or [])]
    if args.money:
        items = [s for s in items if isinstance(s.get("money"), dict)]
    order = {id(s): i for i, s in enumerate(data["statements"])}  # 同一天照記的先後，新的在上
    items = sorted(items, key=lambda s: (s.get("date") or "", order[id(s)]), reverse=True)
    if as_json:
        print(json.dumps({"statements": items, "conflicts": streams.conflicts_of(vault, "statements")}, ensure_ascii=False))
        return 0
    if not items:
        print("口述清單是空的")
        return 0
    from kb._lib import people
    multi = people.multi(vault)  # 多人帶誰說的（#43）；單人照舊
    for s in items:
        who = f"，{statements.said_by(s, True)}" if multi else ""
        print(f"- {s.get('date')} {s.get('text')}（{'、'.join(s.get('entities') or [])}{who}）{STATUS_SUFFIX.get(s.get('status'), '')}")
    return 0


def _date(text):
    from kb._lib import agenda
    try:
        return agenda.parse_date(text).isoformat()
    except agenda.AgendaRefused as e:
        raise statements.StatementRefused(str(e)) from None


def _sum(vault, args, as_json):
    from kb._lib import numbers
    vs = (_date(args.vs_from), _date(args.vs_to)) if args.vs_from and args.vs_to else None
    got = statements.statements_summary(vault, _date(args.date_from), _date(args.date_to), entity=args.entity, vs=vs)
    lines = [f"{args.date_from}～{args.date_to}：收 {numbers.show(got['in'])}、付 {numbers.show(got['out'])}、淨 {numbers.show(got['net'])}（照你說過的加）"]
    if got["diff"]:
        d = got["diff"]["in"]
        lines.append(f"比 {args.vs_from}～{args.vs_to}：收 {numbers.show(got['vs']['in'])}，差 {numbers.show(d['diff'])}"
                     + (f"（{d['pct']}%）" if d["pct"] is not None else ""))
    _out(as_json, lines, got)
    return 0


WRITERS = {"add": _add, "withdraw": _withdraw, "restore": _restore}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.statement_command
    try:
        if command == "list":
            return _list(vault, args, as_json)
        if command == "sum":
            return _sum(vault, args, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except statements.StatementRefused as e:
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
