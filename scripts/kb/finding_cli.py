"""kb finding add／withdraw／list（answer 第 3 點，D-154、D-155）。

推算清單只准這裡寫（經 _lib/findings）：每次在 findings.json 的鎖裡讀改寫（CLI 等 30 秒，等不到「等不到鎖，沒有寫入」exit 3），
寫前備進回合。拒絕的第一行就是原因、exit 3；成功句與拒絕句一字不差。客戶說「把剛才的答案記下來」時 note 叫它（D-151）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。人讀輸出不印 c-…、f-… 編號（日程確認後裁定 6），要編號取 --json。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import entities, findings, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
CHANGED = [{"path": findings.FINDINGS_POSIX, "action": "modify"}]
STATUS_SUFFIX = {"superseded": "（已被取代）", "withdrawn": "（已收回）"}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="finding_command", metavar="<子子命令>", required=True)
    ad = subs.add_parser("add", help="記一筆推算", description="記一筆從多張卡算出來的結果，掛在登記過的實體上。")
    ad.add_argument("--entity", action="extend", nargs="+", required=True, help="掛的實體名稱，可以多個（客戶自己也可以）")
    ad.add_argument("--text", required=True, help="結果一句，最多 200 字")
    ad.add_argument("--how", required=True, help="算法一句，最多 200 字")
    ad.add_argument("--basis", action="extend", nargs="+", default=[], help="依據的檔案卡編號（f-…），可以多個")
    ad.add_argument("--replaces", help="取代哪一筆舊推算（c-…）：舊的記成被這筆取代")
    wd = subs.add_parser("withdraw", help="收回一筆推算", description="這筆推算不用了：記成已收回（紀錄留著）。")
    wd.add_argument("id", help="推算編號（c-…）")
    ls = subs.add_parser("list", help="列出推算", description="預設列有效的；--all 含被取代與收回的。")
    ls.add_argument("--entity", help="只列掛在這個實體上的")
    ls.add_argument("--all", action="store_true", help="全部列出")
    for p in (ad, wd, ls):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _add(vault, args, as_json, conversation, turn_id):
    it, old = findings.add(vault, args.text, args.how, [n.strip() for n in args.entity],
                           [b.strip() for b in args.basis], turn_id,
                           replaces=args.replaces.strip() if args.replaces else None)
    if old is None:
        line = f"已記下推算：{it['text']}（{'、'.join(it['entities'])}；依據 {len(it['basis'])} 份）"
        note = f"記下推算：{it['text']}"
    else:
        line, note = f"已改：{old['text']} → {it['text']}", f"改推算：{old['text']} → {it['text']}"
    journal_cli.record_program_or_warn(vault, note, CHANGED, "finding add", conversation)
    _out(as_json, [line], {"finding": it, "replaced": old})
    return 0


def _withdraw(vault, args, as_json, conversation, turn_id):
    it = findings.withdraw(vault, args.id.strip(), turn_id)
    journal_cli.record_program_or_warn(vault, f"收回推算：{it['text']}", CHANGED, "finding withdraw", conversation)
    _out(as_json, [f"已收回：{it['text']}"], {"finding": it})
    return 0


def _list(vault, args, as_json):
    data = findings.load(vault)
    ent = entities.load(vault)
    items = data["findings"] if args.all else [c for c in data["findings"] if c.get("status") == "active"]
    if args.entity:
        found, _hit = entities.find(ent, args.entity.strip())
        name = found["name"] if found is not None else args.entity.strip()
        k = entities.key(name)
        items = [c for c in items
                 if any(entities.key(findings._current_name(ent, n)) == k for n in c.get("entities") or [])]
    order = {id(c): i for i, c in enumerate(data["findings"])}  # 同一天照記的先後，新的在上
    items = sorted(items, key=lambda c: (c.get("date") or "", order[id(c)]), reverse=True)
    ctx = findings.context(vault, ent) if items else None
    rows = [dict(c, stale=findings.stale(vault, c, ctx)) for c in items]
    if as_json:
        print(json.dumps({"findings": rows}, ensure_ascii=False))
        return 0
    if not rows:
        print("推算清單是空的")
        return 0
    for c in rows:
        mark = f"〔{findings.STALE_TEXT}〕" if c["stale"] and c.get("status") == "active" else ""
        print(f"- {c.get('date')} {c.get('text')}：{c.get('how')}（{'、'.join(c.get('entities') or [])}；"
              f"依據 {len(c.get('basis') or [])} 份）{mark}{STATUS_SUFFIX.get(c.get('status'), '')}")
    return 0


WRITERS = {"add": _add, "withdraw": _withdraw}


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.finding_command
    try:
        if command == "list":
            return _list(vault, args, as_json)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return WRITERS[command](vault, args, as_json, conversation, turn_id)
    except findings.FindingRefused as e:
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
