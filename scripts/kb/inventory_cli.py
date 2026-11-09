"""kb inventory scan：全掃來源與總資料夾，把變動套進 .system/inventory.json（D-094、specs/2026-10-16-清冊.md 第 5 點）。

流程：sources.load → inventory.scan（全掃；--source 只掃那個來源）→ apply（先備進目前回合，D-074）→
env.clear_missing（這次找得到的來源）→ 印。印給人最多四行（沒有的項不印）；--json 印 Report 去掉 entries。
清冊讀不到（壞 JSON、形狀不對、版本比這版新，I-046）第一行「清冊讀不到：<原因>」exit 3；--source 對不到別名 exit 3；
找不到知識庫 exit 3（審閱裁定 14）。終端機跑的記一段工作日誌程式段（D-085），對話裡叫的不記。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import env, inventory, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行，或加 --vault"
CHANGED = [{"path": inventory.INVENTORY_POSIX, "action": "modify"}]
PENDING_CHANGED = [{"path": ".system/pending.json", "action": "modify"}, {"path": "系統/待處理.md", "action": "modify"}]


def changed_of(touched_pending):
    """程式段的 changed：清冊一定有；這次有建或關待處理條目才加 pending.json 與 待處理.md（ingest 製卡第 5 點）。"""
    return CHANGED + (PENDING_CHANGED if touched_pending else [])


def add_arguments(sub):
    subs = sub.add_subparsers(dest="inventory_command", metavar="<子子命令>", required=True)
    sc = subs.add_parser("scan", help="掃描來源與總資料夾，把變動套進清冊",
                         description="全掃來源與總資料夾，把新增、修改、搬動、不見套進清冊；寫前先備份。")
    sc.add_argument("--source", help="只掃這個來源（別名）")
    lr = subs.add_parser("learn", help="新同事第一次：認這台的檔案（算指紋填本機那半）",
                         description="共用清冊有東西、這台的本機清冊是空的（新 clone）：對每個位置算指紋對共用的，記大小與時間；雲端占位檔只記大小與時間、不讀內容。")
    lr.add_argument("--background", action="store_true", help="背景跑（開場叫的）：不印東西")
    lr.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    lr.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    sc.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sc.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def find_vault(args):
    found = guard.find_vault(getattr(args, "vault", None) or os.getcwd())
    return Path(os.path.realpath(str(found))) if found is not None else None


def name_of(uri):
    """給人看的名字：去掉 file://。"""
    return str(uri)[len("file://"):] if str(uri).startswith("file://") else str(uri)


def missing_text(missing_sources):
    return "、".join(f"{m['alias']}（{m['spec']}）" for m in missing_sources)


def found_source_ids(sources, report, only=None):
    """這次找得到的來源（env 的鍵：id，沒有 id 用別名）。only 是 --source 的別名清單：沒掃到的來源不算找得到（審閱裁定 39）。"""
    lost = {m["id"] or m["alias"] for m in report["missing_sources"]}
    wanted = None if only is None else {a.casefold() for a in only}
    return [s["id"] or s["alias"] for s in sources.entries
            if (s["id"] or s["alias"]) not in lost and (wanted is None or s["alias"].casefold() in wanted)]


def _lines(report):
    by = report["by_source"]
    parts = "、".join(f"{alias} {v['files']}" for alias, v in by.items() if v["files"])
    out = [f"清冊已更新：總計 {report['total']} 份" + (f"（{parts}）" if parts else "")]
    dup = len(report["duplicates"])
    out.append(f"這次：新增 {len(report['added'])}、修改 {len(report['modified'])}、搬動 {len(report['moved'])}、"
               f"不見 {len(report['missing'])}" + (f"；同一份內容在多處 {dup} 份" if dup else ""))
    rc = report["read_counts"]
    cannot = rc["error"] + len(report["errors"])
    if rc["credential"] or rc["placeholder"] or rc["too_large"] or cannot:
        examples = "、".join(name_of(u) for u in report["read_examples"].get("credential", []))
        cred = f"檔名像密碼或金鑰 {rc['credential']}" + (f"（{examples}）" if examples else "")
        out.append(f"不算指紋：{cred}、占位檔 {rc['placeholder']}、超過大小 {rc['too_large']}；開不了 {cannot}")
    if report["missing_sources"]:
        out.append(f"這台電腦找不到：{missing_text(report['missing_sources'])}")
    return out


def scan_and_apply(vault, sources, only=None, via="inventory scan"):
    """掃（only：別名清單）、寫進清冊、記工作日誌程式段；回 report。office copy 補記清冊也走這裡（簡化一 A）。"""
    conversation = journal_cli.in_conversation(vault)
    report = inventory.scan(vault, sources, only_roots=only)
    touched = inventory.apply(vault, report, turn.current(vault)["turn"])
    env.clear_missing(vault, found_source_ids(sources, report, only))
    note = (f"清冊掃描：新增 {len(report['added'])}、修改 {len(report['modified'])}、不見 {len(report['missing'])}"
            f"（總計 {report['total']} 份）")
    journal_cli.record_program_or_warn(vault, note, changed_of(touched), via, conversation)
    return report


def _scan(vault, args, as_json):
    sources = sources_lib.load(vault)
    only = None
    if args.source is not None:
        wanted = args.source.strip()
        aliases = [s["alias"] for s in sources.entries] + [u["alias"] for u in sources.unresolved]
        aliases += [inventory.KNOWLEDGE, inventory.HOLD]
        if wanted.casefold() not in {a.casefold() for a in aliases}:
            print(f"沒有叫「{wanted}」的來源")
            return 3
        only = [wanted]
    report = scan_and_apply(vault, sources, only)
    if as_json:
        print(json.dumps(inventory.public(report), ensure_ascii=False))
        return 0
    lines = _lines(report)
    dup = _dup_line(vault, report)
    if dup:
        lines.insert(2, dup)
    print("\n".join(lines))
    return 0


DUP_SHOWN = 3


def _dup_line(vault, report):
    """「同一份內容在多處：報價.pdf（2 處）、合約.docx（3 處）」（B-046 (2)）：這次掃到重複的那幾份，處數照清冊現在的位置數；
    最多列 DUP_SHOWN 份，多的接「等 K 份」；不印編號。沒有重複回 None。"""
    ids = list(dict.fromkeys(target for _uri, target in report["duplicates"]))
    if not ids:
        return None
    by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    parts = []
    for eid in ids:
        locs = (by_id.get(eid) or {}).get("locations") or []
        if locs:
            parts.append(f"{name_of(locs[0]['uri']).rsplit('/', 1)[-1]}（{len(locs)} 處）")
    if not parts:
        return None
    more = f"等 {len(parts)} 份" if len(parts) > DUP_SHOWN else ""
    return "同一份內容在多處：" + "、".join(parts[:DUP_SHOWN]) + more


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        inventory.load(vault)
    except ValueError as e:
        print(f"清冊讀不到：{e}")
        return 3
    if args.inventory_command == "learn":
        got = inventory.learn(vault)
        if getattr(args, "background", False):
            return 0
        print(json.dumps(got, ensure_ascii=False) if as_json else
              f"認好了：{got['learned']} 個位置（雲端占位 {got['placeholder']}、這台找不到或跟共用的不一樣 {got['skipped']}）")
        return 0
    if inventory.learning(vault):  # 還在認檔案：本機沒有的不當新增或修改（D-300）
        msg = "這台還在認檔案（kb inventory learn），認好之前不掃描、不報變動"
        print(json.dumps({"learning": True, "message": msg}, ensure_ascii=False) if as_json else msg)
        return 0
    try:
        return _scan(vault, args, as_json)
    except ValueError as e:  # sources.json 壞掉
        print(f"{sources_lib.SOURCES_REL} 讀不了，請先修好它：{type(e).__name__}: {e}")
        return 3
    except TimeoutError:
        print("等不到清冊的鎖（別的指令正在改它），沒有寫入；請稍後再試")
        return 3
    except OSError as e:  # 備份或寫檔失敗
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
