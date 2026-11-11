"""kb renders apply <頁> [--json]／drop <c-…> [--json]（Obsidian 首頁，#22，D-263）。

apply：她在程式產生的頁（系統/日程.md、待處理.md、裁示.md）上直接改了（打勾、刪一行、改日期、新打一行），照意思做：認得出的在那份狀態檔的鎖裡
直接做（歷程 by hand），認不出的放進頁面最底下「你加的（還沒處理）」，做完重新產生那頁；她那份照舊存進 .system/handedits/。
人讀一行一件，沒改什麼印「這頁沒有要照做的」。外掛在她離開被改的那一行、或切到別頁時叫它。
drop：「你加的」裡的一筆不用了（renders.json 的 carried 拿掉，那頁重寫）。renders.json 不備進回合，不用了的沒有退回。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import agenda, hand, pending, renders, rulings, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NOTHING = "這頁沒有要照做的"
BAD_PAGE = "頁只收 系統/日程.md、系統/待處理.md、系統/裁示.md"
NO_SUCH = "沒有這一筆「你加的」"
KEEP_FAILED = "她那份沒存成，這次先不動；再說一次"
UNRECOGNIZED = "這頁你改的我沒認出來，記在待處理了"
CLI_LOCK_TIMEOUT = 30
PAGES = {
    "系統/日程.md": (agenda.apply_hand, [agenda.AGENDA_POSIX, agenda.MD_POSIX]),
    "系統/待處理.md": (pending.apply_hand, [pending.PENDING_POSIX, pending.MD_POSIX]),
    "系統/裁示.md": (rulings.apply_hand, [rulings.RULINGS_POSIX, rulings.MD_POSIX]),
}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="renders_command", metavar="<子子命令>", required=True)
    ap = subs.add_parser("apply", help="照她在程式產生的頁上直接改的做", description="照她在日程.md、待處理.md、裁示.md 上直接改的做。")
    ap.add_argument("page", help="頁：系統/日程.md、系統/待處理.md、系統/裁示.md")
    dr = subs.add_parser("drop", help="「你加的（還沒處理）」裡的一筆不用了", description="拿掉「你加的」裡的一筆。")
    dr.add_argument("id", help="c-… 編號（kb asks --json 每題的 key）")
    for p in (ap, dr):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _page(text):
    page = str(text or "").strip().replace("\\", "/")
    return page if page in PAGES else None


def _entry(rel):
    """(套用的函式, 寫到的檔)。"""
    return PAGES[rel]


def _refresh(vault, rel, turn_id):
    """拿掉一筆之後重寫那頁（不當成手改：renders.suppressed）。"""
    with renders.suppressed(rel):
        if rel == agenda.MD_POSIX:
            agenda.render(vault, turn_id, hand={})
        elif rel == pending.MD_POSIX:
            turn.backup(vault, pending.MD_POSIX, turn_id)
            pending.render(vault, turn_id=turn_id)
        else:
            rulings.render(vault, turn_id, hand={})


def _apply(vault, args, as_json, conversation):
    rel = _page(args.page)
    if rel is None:
        print(BAD_PAGE)
        return 3
    run, changed = _entry(rel)
    if renders.check(vault, rel) is None:  # 她沒改過這頁：不開回合（外掛每次離開一行、切頁都叫，大多沒事）
        _out(as_json, [NOTHING], {"page": rel, "applied": [], "carried": [], "dropped": 0})
        return 0
    turn_id = turn.current(vault)["turn"]
    report = run(vault, turn_id)
    if report == hand.KEEP_FAILED:
        print(KEEP_FAILED)
        return 3
    if report == hand.UNRECOGNIZED:  # 認手改時出錯：她那份存了、待處理開了一件，頁面已重寫（裁定 22）
        _out(as_json, [UNRECOGNIZED], {"page": rel, "applied": [], "carried": [], "dropped": 0, "unrecognized": True})
        return 0
    if report is None:
        _out(as_json, [NOTHING], {"page": rel, "applied": [], "carried": [], "dropped": 0})
        return 0
    lines = []
    if report["applied"]:
        lines.append("照你改的做了：" + "、".join(report["applied"]))
    if report["carried"]:
        lines.append("我看不懂、留在最下面「你加的」：" + "、".join(report["carried"]))
    if report["dropped"]:
        lines.append(f"不用了的 {report['dropped']} 筆拿掉了")
    journal_cli.record_program_or_warn(vault, hand.note(rel, report), [{"path": p, "action": "modify"} for p in changed], "renders apply",
                                       conversation)
    _out(as_json, lines or [NOTHING], {"page": rel, "applied": report["applied"], "carried": report["carried"],
                                       "dropped": report["dropped"]})
    return 0


def _drop(vault, args, as_json, conversation):
    cid = str(args.id or "").strip()
    rel = next((r for r, items in renders.carried_all(vault).items() if any(c["id"] == cid for c in items)), None)
    if rel is None:
        print(NO_SUCH)
        return 3
    turn_id = turn.current(vault)["turn"]
    if _entry(rel)[0](vault, turn_id) == hand.KEEP_FAILED:  # 先把她這頁上其他的改動照意思處理掉，免得重寫頁面時蓋掉；存不下就不動
        print(KEEP_FAILED)
        return 3
    page, item = renders.drop_carried(vault, cid)
    if page is None:
        print(NO_SUCH)
        return 3
    _refresh(vault, page, turn_id)
    text = renders.carried_line(item)
    journal_cli.record_program_or_warn(vault, f"{hand.page_name(page)}「你加的」不用了：{text}",
                                       [{"path": p, "action": "modify"} for p in _entry(page)[1]], "renders drop", conversation)
    _out(as_json, [f"已拿掉：{text}"], {"page": page, "id": cid, "text": text})
    return 0


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    conversation = journal_cli.in_conversation(vault)
    try:
        if args.renders_command == "apply":
            return _apply(vault, args, as_json, conversation)
        return _drop(vault, args, as_json, conversation)
    except agenda.AgendaRefused as e:
        print(e)
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except TimeoutError:
        print("等不到鎖（別的指令正在改），沒有寫入；請稍後再試")
        return 3
    except OSError as e:
        print(f"讀不到：{type(e).__name__}: {e}")
        return 3
