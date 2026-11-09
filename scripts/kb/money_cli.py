"""kb money add／settle／edit／cancel／reopen／attach／merge／count／unsure／scanned／list／candidates／sum／stock／backfill
（記帳 2，issue #27；D-271～D-277、D-282、D-284、D-338、D-339、D-342、D-343）。

帳只准這裡寫（經 _lib/money）：每次在 money.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，沒有寫入」exit 3），寫前備進回合。
拒絕的第一行就是原因、exit 3（--json 時印 {"refused", …}）；帳關著、只記我講的擋帶卡的也是 exit 3，句子給 AI 不給她（D-343）。
寫入成功的輸出帶 tell（成功句），有低於安全庫存的帶 low_stock（D-282），edit 改了到期日帶 related_agenda（D-273）；
add、edit 品項有換不了的單位照記、帶 unit_ask 教 AI 問一次（D-344 再補，不因單位擋）。
--advanced-by 我（或不給值）＝這台的人（確認報告 #2）；寫入的子命令帶 --resolves <p-…> 寫完關那件待處理（#6，編號錯了不寫）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合；歷程的 by 在對話裡是 ai、終端機是 program。
人讀輸出不印內部編號（m-…、f-…）：要編號的取 --json。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import agenda, inventory, money, numbers, pending, streams, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": money.MONEY_POSIX, "action": "modify"}]
PENDING_CHANGED = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]


ADV_HELP = "墊錢的成員（報帳，多人才有）；我或不給值＝這台的人"
NO_PENDING = "沒有這件待處理：{}（看 kb asks --json 的 do），沒有寫入"


def _common(p):
    p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def add_arguments(sub):
    subs = sub.add_subparsers(dest="money_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="記一筆帳", description="記一筆收、付或只動數量的帳；有可能同一筆的先回 candidates 不寫。")
    ad.add_argument("--dir", required=True, help="收、付、無（只動數量：調貨、送人、壞掉）")
    ad.add_argument("--amount", help="金額（可省＝未知，只准還沒收付的）")
    ad.add_argument("--date", help="收付那天 YYYY-MM-DD")
    ad.add_argument("--due", help="到期日 YYYY-MM-DD（還沒收付）；--date 與 --due 都沒給＝今天已收付（帶 --card 的＝還沒收付、沒到期日）")
    ad.add_argument("--entity", action="append", default=[], help="掛的實體，可以重複；沒掛的歸「其他」")
    ad.add_argument("--item", action="append", default=[], help="品項「<商品> <±數量>[單位][@單價][在<地點>]」，可以重複")
    ad.add_argument("--advanced-by", nargs="?", const="我", help=ADV_HELP)
    ad.add_argument("--text", default="", help="一句說明")
    ad.add_argument("--note", default="", help="備註")
    ad.add_argument("--repeat", help="每年、每月、每週")
    ad.add_argument("--new", action="store_true", help="看過 candidates，確定是另一筆")
    ad.add_argument("--unsure", help="跟這筆（m-…）看不出是不是同一筆：寫進去並開一條待處理問她（要跟 --new）")
    ad.add_argument("--from-statement", help="補自這句口述（s-…，帳關著時記的）：照那天記")
    ad.add_argument("--card", help="從這張檔案卡讀到的（f-…），標推測")
    se = subs.add_parser("settle", help="收付了", description="這筆收付了；金額少於原額就拆成已收付與還沒收付的兩筆。")
    se.add_argument("id", help="帳的編號（m-…）")
    se.add_argument("--amount", help="這次收付的金額；沒給＝整筆")
    se.add_argument("--date", help="收付那天；沒給＝今天")
    se.add_argument("--due", help="拆出來那筆的到期日")
    se.add_argument("--card", help="從這張檔案卡讀到收付了（f-…，匯款單、收據）：出處記在收付的那筆")
    ed = subs.add_parser("edit", help="改一筆帳", description="改金額、日期、到期、實體、品項、墊的人、說明、備註。")
    ed.add_argument("id", help="帳的編號（m-…）")
    ed.add_argument("--amount")
    ed.add_argument("--date")
    ed.add_argument("--due")
    ed.add_argument("--entity", action="append", default=None)
    ed.add_argument("--item", action="append", default=None)
    ed.add_argument("--advanced-by", nargs="?", const="我", help=ADV_HELP)
    ed.add_argument("--text")
    ed.add_argument("--note")
    status = [subs.add_parser(n, help=h, description=f"把一筆帳{h}。") for n, h in (("cancel", "取消"), ("reopen", "重開"))]
    for p in status:
        p.add_argument("id", help="帳的編號（m-…）")
    at = subs.add_parser("attach", help="補出處", description="這筆帳的出處多一張檔案卡（讀到收據、匯款單）。")
    at.add_argument("id", help="帳的編號（m-…）")
    at.add_argument("--card", required=True, help="檔案卡編號（f-…）")
    mg = subs.add_parser("merge", help="兩筆是同一筆", description="併掉的那筆取消，出處併進留的那筆。")
    mg.add_argument("keep", help="留的（m-…）")
    mg.add_argument("drop", help="併掉的（m-…）")
    ct = subs.add_parser("count", help="盤點", description="照實際數量記一筆調整（金額 0），不直接改庫存。")
    ct.add_argument("product", help="商品")
    ct.add_argument("--actual", required=True, help="實際數量（可帶單位）")
    ct.add_argument("--place", help="在哪（地點型別的實體）")
    un = subs.add_parser("unsure", help="看不出是不是她的錢", description="製卡讀到錢、看不出流向：開一條待處理問她。")
    un.add_argument("--card", required=True, help="檔案卡編號（f-…）")
    un.add_argument("--text", required=True, help="一句：讀到什麼、哪裡看不出")
    sc = subs.add_parser("scanned", help="這張卡的錢抽過了", description="製卡者對這張卡抽完錢與數字欄（沒抽到也要跑）：清冊記指紋。")
    sc.add_argument("id", help="檔案卡編號（f-…）")
    ls = subs.add_parser("list", help="列出帳", description="預設全部沒取消的，照日期排。")
    ls.add_argument("--open", action="store_true", help="只列還沒收付的")
    ls.add_argument("--entity", help="只列跟這個實體有關的")
    ls.add_argument("--from", dest="date_from")
    ls.add_argument("--to", dest="date_to")
    ls.add_argument("--advanced-by", nargs="?", const="我", help="只列這個成員墊的（我＝這台的人）")
    ca = subs.add_parser("candidates", help="列可能同一筆的", description="記之前先看（D-276）：只列不寫。")
    ca.add_argument("--dir", required=True)
    ca.add_argument("--amount")
    ca.add_argument("--date")
    ca.add_argument("--due")
    ca.add_argument("--entity", action="append", default=[])
    sm = subs.add_parser("sum", help="區間加總", description="照收付日算收、付、淨；未收付是現在還沒收付的全部（不限區間）；帳關著時照標過錢的口述。")
    sm.add_argument("--from", dest="date_from", required=True)
    sm.add_argument("--to", dest="date_to", required=True)
    sm.add_argument("--entity")
    sm.add_argument("--advanced-by", nargs="?", const="我", help="只算這個成員墊的（我＝這台的人）")
    sm.add_argument("--vs-from")
    sm.add_argument("--vs-to")
    st = subs.add_parser("stock", help="庫存", description="商品 × 地點與合計。")
    st.add_argument("product", nargs="?", help="商品；沒給全部")
    st.add_argument("--place", help="只看這個地點")
    bf = subs.add_parser("backfill", help="還沒補進帳的口述", description="帳關著時標過錢、還沒進帳的口述（改開時問她要不要補）。")
    for p in [ad, se, ed, *status, at, mg]:
        p.add_argument("--resolves", help="這筆是照她對這件待處理（p-…）的回答做的：寫完程式關那件")
    for p in [ad, se, ed, *status, at, mg, ct, un, sc, ls, ca, sm, st, bf]:
        _common(p)


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _by(conversation):
    return "ai" if conversation else "program"


def _unit_ask(vault, it, payload, lines):
    """記帳不因單位擋（D-344 再補）：換不了的單位照記、分開列；成功句帶「（不知道一箱幾瓶）」（有基本單位的品項字已帶），
    unit_ask（有才有）教 AI 問她一次，她不知道就放著。"""
    asks = money.unit_asks(vault, it)
    extra = "".join(f"；{a['entity']}分開算{a['mark']}" for a in asks if not a["shown"])
    if extra:
        payload["tell"] += extra
        if lines:
            lines[0] += extra
    if asks:
        payload["unit_ask"] = list(dict.fromkeys(a["ask"] for a in asks))
        lines += payload["unit_ask"]
    return payload, lines


def _with_extras(vault, it, payload, lines):
    """寫入成功：帶 tell 與 low_stock（有才有）；品項換算過的成功句會講出來（「精華液 +2 箱（48 瓶）」，D-344 補）。"""
    payload["tell"] = "記下：" + money.describe(vault, it)
    names = [ln["entity"] for ln in it.get("lines") or [] if (ln.get("qty") or 0) < 0]
    low = money.low_stock(vault, names) if names else []
    if low:
        payload["low_stock"] = low
        lines += [f"{x['entity']}剩 {x['text']}，低於安全庫存 {numbers.show(x['min'])}" for x in low]
    return payload, lines


def _add(vault, args, as_json, conversation, turn_id):
    it, created = money.add(vault, turn_id, args.dir, amount=args.amount, date=args.date, due=args.due, entities=args.entity,
                            items=args.item, advanced_by=args.advanced_by, text=args.text, note=args.note, repeat=args.repeat,
                            new=args.new, unsure=args.unsure, card=args.card, statement=args.from_statement, by=_by(conversation))
    if not created:
        _out(as_json, [f"帳裡已經有這筆：{money.describe(vault, it)}"], {"item": it, "created": False})
        return 0
    payload, lines = _with_extras(vault, it, {"item": it, "created": True}, [])
    lines.insert(0, payload["tell"])
    _unit_ask(vault, it, payload, lines)
    changed = list(CHANGED)
    if args.unsure:
        got, _new = pending.add_money(vault, "money_dupe", f"這兩筆是不是同一筆：{money.describe(vault, money.get(vault, args.unsure))}"
                                      f"／{money.describe(vault, it)}", turn_id, data={"keep": args.unsure, "other": it["id"]})
        payload["pending"] = got["id"]
        changed += PENDING_CHANGED
    journal_cli.record_program_or_warn(vault, payload["tell"], changed, "money add", conversation)
    _out(as_json, lines, payload)
    return 0


def _settle(vault, args, as_json, conversation, turn_id):
    it, rest, rep = money.settle(vault, args.id, turn_id, amount=args.amount, date=args.date, due=args.due, by=_by(conversation),
                                 card=args.card)
    shown = rep or it
    line = "收付了：" + money.describe(vault, shown)
    if rest:
        line += f"；還沒收付的另記一筆：{money.describe(vault, rest)}"
    if rep:
        line += f"；下次 {agenda.md_of(it['due'])}"
    journal_cli.record_program_or_warn(vault, line, CHANGED, "money settle", conversation)
    _out(as_json, [line], {"item": it if not rep else rep, "split": rest, "repeat": rep, "next": it if rep else None, "tell": line})
    return 0


def _edit(vault, args, as_json, conversation, turn_id):
    after, before = money.edit(vault, args.id, turn_id, by=_by(conversation), amount=args.amount, date=args.date, due=args.due,
                               entities=args.entity, items=args.item, advanced_by=args.advanced_by, text=args.text, note=args.note)
    payload = {"item": after, "changed": before != after, "tell": "已改：" + money.describe(vault, after)}
    lines = [payload["tell"] if payload["changed"] else "沒有要改的：" + money.describe(vault, after)]
    if payload["changed"] and args.item:
        _unit_ask(vault, after, payload, lines)
    if args.due and before.get("due") and before.get("due") != after.get("due"):
        rel = money.related_agenda(vault, after.get("entities"), before["due"])
        payload["related_agenda"] = rel
        lines += [f"{'、'.join(a.get('entities') or [])}還有一條 {agenda.md_of(a['date'])} 的日程：{a['text']}" for a in rel]
    if payload["changed"]:
        journal_cli.record_program_or_warn(vault, payload["tell"], CHANGED, "money edit", conversation)
    _out(as_json, lines, payload)
    return 0


def _status(vault, args, as_json, conversation, turn_id):
    status = {"cancel": "cancelled", "reopen": "open"}[args.money_command]
    it = money.set_status(vault, args.id, status, turn_id, by=_by(conversation))
    line = ("已取消：" if status == "cancelled" else "已重開：") + money.describe(vault, it)
    journal_cli.record_program_or_warn(vault, line, CHANGED, f"money {args.money_command}", conversation)
    _out(as_json, [line], {"item": it, "tell": line})
    return 0


def _attach(vault, args, as_json, conversation, turn_id):
    it = money.attach(vault, args.id, args.card, turn_id, by=_by(conversation))
    title = agenda.card_titles(vault).get(args.card) or ""
    line = "補了出處：" + money.describe(vault, it) + (f"（{title}）" if title else "")
    journal_cli.record_program_or_warn(vault, line, CHANGED, "money attach", conversation)
    _out(as_json, [line], {"item": it, "tell": line})
    return 0


def _merge(vault, args, as_json, conversation, turn_id):
    keep, drop = money.merge(vault, args.keep, args.drop, turn_id, by=_by(conversation))
    line = "併成一筆：" + money.describe(vault, keep)
    changed = list(CHANGED)
    if pending.resolve_money_dupe(vault, {args.keep, args.drop}, turn_id):
        changed += PENDING_CHANGED
    journal_cli.record_program_or_warn(vault, line, changed, "money merge", conversation)
    _out(as_json, [line], {"item": keep, "dropped": drop, "tell": line})
    return 0


def _count(vault, args, as_json, conversation, turn_id):
    it, diff = money.count(vault, args.product, args.actual, turn_id, place=args.place, by=_by(conversation))
    if it is None:
        _out(as_json, ["庫存對得上，不用調整"], {"item": None, "diff": 0})
        return 0
    payload, lines = _with_extras(vault, it, {"item": it, "diff": diff}, [])
    lines.insert(0, payload["tell"])
    journal_cli.record_program_or_warn(vault, payload["tell"], CHANGED, "money count", conversation)
    _out(as_json, lines, payload)
    return 0


def _unsure(vault, args, as_json, conversation, turn_id):
    agenda._card(vault, args.card, no_card="這份還沒有檔案卡，先寫卡")
    text = " ".join(args.text.split())
    got, created = pending.add_money(vault, "money_unsure", text, turn_id, data={"card": args.card}, file_ids=[args.card])
    line = "記下了，會問她：" + text
    if created:
        journal_cli.record_program_or_warn(vault, line, PENDING_CHANGED, "money unsure", conversation)
    _out(as_json, [line], {"pending": got, "created": created})
    return 0


def _scanned(vault, args, as_json, conversation, turn_id):
    out = money.mark_scanned(vault, args.id, turn_id)
    journal_cli.record_program_or_warn(vault, f"錢抽過：{out['title']}", [{"path": inventory.INVENTORY_POSIX, "action": "modify"}],
                                       "money scanned", conversation)
    _out(as_json, [f"已記下：「{out['title']}」的錢抽過了"], out)
    return 0


def _pending_item(vault, pid):
    try:
        return next((i for i in pending.load(vault)["items"] if i.get("id") == pid), None)
    except ValueError:
        return None


def _resolve(vault, pid, command, conversation, turn_id):
    """--resolves（確認報告 #6）：照她的回答記完帳，那件待處理還開著就關掉；已經關了不動。"""
    if (_pending_item(vault, pid) or {}).get("status") != "open":
        return
    how = f"照她的回答記帳（money {command}）"
    pending.resolve(vault, pid, how, turn_id)
    journal_cli.record_program_or_warn(vault, f"待處理 {pid}：{how}", PENDING_CHANGED, "pending resolve", conversation)


WRITERS = {"add": _add, "settle": _settle, "edit": _edit, "cancel": _status, "reopen": _status, "attach": _attach,
           "merge": _merge, "count": _count, "unsure": _unsure, "scanned": _scanned}


def _day(text):
    return money._date(text) if text else None


def _candidate_lines(rows):
    """候選的人讀行（不印編號）：日期、方向、金額、實體、說明，還沒收付的標出來。"""
    out = []
    for c in rows:
        amt = numbers.show(c["amount"]) if c.get("amount") is not None else "金額未知"
        day = c.get("date") or c.get("due")
        out.append(f"- {agenda.md_of(day) if day else ''} {money.DIR_TEXT.get(c.get('dir'), '')} {amt}"
                   f"（{'、'.join(c.get('entities') or []) or money.OTHER}）"
                   + (f" {c['text']}" if c.get("text") else "") + ("（還沒收付）" if c.get("open") else ""))
    return out


def _list(vault, args, as_json):
    data = money.load(vault)
    items = [i for i in data["items"] if i.get("status") != "cancelled"]
    if args.open:
        items = [i for i in items if i.get("status") == "open"]
    if args.entity:
        k = money._key(money._entity(money._ents(vault), args.entity)["name"])
        items = [i for i in items if money._touches(i, k)]
    frm, to = _day(args.date_from), _day(args.date_to)
    if frm or to:
        items = [i for i in items if money._in_range(money._ref_day(i), frm, to)]
    if args.advanced_by is not None:
        who = money.member(vault, args.advanced_by)
        items = [i for i in items if money._key(i.get("advanced_by") or "") == money._key(who)]
    items = sorted(items, key=lambda i: (money._ref_day(i) or "", i.get("id", "")))
    if as_json:
        print(json.dumps({"items": items, "conflicts": streams.conflicts_of(vault, "money")}, ensure_ascii=False))
        return 0
    prof, ents = money._profile(vault), money._ents(vault)
    print("\n".join(f"- {money.describe(vault, i, prof, ents)}" for i in items) if items else "帳是空的")
    return 0


def _candidates(vault, args, as_json):
    got = money.candidates(vault, args.dir, amount=args.amount, date=args.date, due=args.due, entities=args.entity)
    _out(as_json, _candidate_lines(got) or ["沒有可能同一筆的"], {"candidates": got})
    return 0


def _sum(vault, args, as_json):
    vs = (money._date(args.vs_from), money._date(args.vs_to)) if args.vs_from and args.vs_to else None
    who = money.member(vault, args.advanced_by) if args.advanced_by is not None else None
    got = money.summary(vault, money._date(args.date_from), money._date(args.date_to), entity=args.entity, vs=vs,
                        advanced_by=who)
    lines = [f"{args.date_from}～{args.date_to}：收 {numbers.show(got['in'])}、付 {numbers.show(got['out'])}、淨 {numbers.show(got['net'])}"
             + (f"；現在未收 {numbers.show(got['open_in'])}、未付 {numbers.show(got['open_out'])}（全部還沒收付的，不限區間）"
                if got["source"] == "money" else "")
             + ("（照你說過的加）" if got["source"] == "statements" else "")]
    if got["diff"]:
        d = got["diff"]["in"]
        lines.append(f"比 {args.vs_from}～{args.vs_to}：收 {numbers.show(got['vs']['in'])}，差 {numbers.show(d['diff'])}"
                     + (f"（{d['pct']}%）" if d["pct"] is not None else ""))
    for p in got["products"]:
        amt = "" if p["amount"] is None else f"、{numbers.show(p['amount'])}{'（估）' if p['estimated'] else ''}{money.missing_text(p)}"
        lines.append(f"- {p['name']} 賣出 {p['text']}{amt}")
    _out(as_json, lines, got)
    return 0


def _stock(vault, args, as_json):
    got = money.stock(vault, args.product, place=args.place)
    lines = []
    for p in got["products"]:
        where = money.places_line(p)
        lines.append(f"- {p['name']} {p['text']}" + (f"（{where}）" if where else ""))
    _out(as_json, lines or ["還沒有庫存的紀錄"], got)
    return 0


def _backfill(vault, as_json):
    rows = money.backfill(vault)
    _out(as_json, [f"- {s.get('date')} {s.get('text')}" for s in rows] or ["沒有要補的"], {"statements": rows})
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.money_command
    try:
        if command == "list":
            return _list(vault, args, as_json)
        if command == "candidates":
            return _candidates(vault, args, as_json)
        if command == "sum":
            return _sum(vault, args, as_json)
        if command == "stock":
            return _stock(vault, args, as_json)
        if command == "backfill":
            return _backfill(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        resolves = getattr(args, "resolves", None)
        if resolves and _pending_item(vault, resolves) is None:
            raise money.MoneyRefused(NO_PENDING.format(resolves))
        code = WRITERS[command](vault, args, as_json, conversation, turn_id)
        if code == 0 and resolves:
            _resolve(vault, resolves, command, conversation, turn_id)
        return code
    except money.MoneyRefused as e:
        if as_json and e.payload:
            print(json.dumps(dict(e.payload, message=str(e)), ensure_ascii=False))
        else:
            print(str(e))
            for line in _candidate_lines(e.payload.get("candidates") or []):
                print(line)
        return 3
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
