"""kb folders add／exclude／remove／include／list：登記來源資料夾、排除裡面的夾或檔、拿掉來源、取消排除、列登記表
（D-077、D-021 補、D-042、D-073、specs/2026-10-16-manage.md）。

add 先過拒絕條件（不存在、整顆磁碟、系統夾、跟總資料夾或既有來源互相包含、另一個知識庫，I-050、I-054），
再數份數並列出憑證樣式的檔（credentials.scan_all，只數、不讀內容），spec 用 paths.compress_spec 壓成可攜；
讀、檢查重疊與別名、備份、寫四步在同一把 FileLock 裡（I-080），改 sources.json 前先備進目前回合（D-074）。
exclude 的對象要真的存在（I-033），spec 相對該來源根；寫回時有 id 用 id、沒有用 alias，找不到就不存檔；兩種 kind 守門都不讀（D-024）。
remove 照別名拿掉整筆（含它的排除；別名去頭尾空白與結尾的點，跟 add 擋重複一樣不分大小寫），客戶的檔不動；
include 只比字串（排除的夾可能已被刪），刪 excludes 裡 root 相等的那列；拒絕分三種：不在登記來源、在某條排除裡面、沒被排除。
--check 只印報告不寫；拒絕一律第一行原因、exit 3；sources.json 壞掉也是一行原因 exit 3；argparse 參數錯 exit 2。
印給人看的路徑用原始大小寫。往上找知識庫一律 guard.find_vault。不碰 settings.json（D-079）。
add、exclude、remove、include 寫成功後記一段工作日誌程式段（D-085），note 是印給人的第一行；--check、list 不記。
清冊（D-095）：四個子命令在 sources.json 的鎖裡寫完 sources 之後、鎖還沒放之前接著改清冊（清冊自己的鎖在裡面），
兩份都備進同一回合；add、include 用 inventory.scan 限定那個來源再 apply，exclude、remove 用 inventory.reconcile。
add 第一行的份數與憑證清單從同一個 scan 取（清冊審閱裁定 7，scan_all 只剩找另一個知識庫）；--check 只 scan 不 apply。
第二行印清冊的數字；--json 多 "inventory"；改到清冊時程式段 changed 多 .system/inventory.json。清冊讀不到先擋，exit 3。
manage 第二版（D-174、D-177）：exclude（兩種）與 remove 在清冊對帳之後把那些條目的檔案卡拿掉（備進回合）、重寫總目錄與綜合卡快取，
成功句接「卡拿掉 N 張」；hidden 那條排除記 purge: pending，列出接著要重寫的綜合卡（「整理到」引用那些卡的），
沒有受影響的綜合卡就在同一個指令裡接著清殘留（kb folders purge，本體在 _lib/purge）。
relocate <別名> <新路徑>：資料夾搬家只換登記的位置（別名、排除、清冊 uri 不動），新路徑照 add 的拒絕條件查（跟自己不算重疊），
從清冊抽樣到新位置比對、照實回報；--check 只印，不帶就改 spec、掃描、搬卡、重寫總目錄，先備進回合，退回回到舊位置。
"""
import argparse
import contextlib
import json
import os
from datetime import date
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import cards, config, credentials, env, inventory, purge, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, write_json_atomic
from kb._lib.paths import compress_spec, inside, is_abs_spec, normalize, resolve_spec

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行，或加 --vault"
HOME_TOP_FOLDERS = ("Desktop", "Documents", "Downloads", "桌面", "文件", "下載")
CREDENTIAL_LIST_MAX = 10
KIND_TEXT = {"hidden": "不想讓 AI 看到", "unused": "用不到"}
PURGE_TAIL = "之前讀過的已送過雲端，Claude 自己的對話紀錄也還有，這兩處清不掉"
NOTHING_TO_PURGE = "「{name}」沒有要清的"
PURGE_BLOCKED = "還有綜合卡寫著那夾的內容：{names}（先重寫再清）"
RESERVED_RELOCATE = "「{alias}」是知識庫自己的，不用換位置"
ALIAS_BAD_CHARS = '<>:"/\\|?*'
# Windows 保留裝置名（N7）：當夾名會建不出來或指到裝置
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="folders_command", metavar="<子子命令>", required=True)
    a = subs.add_parser("add", help="登記一個來源資料夾（只讀）", description="登記一個來源資料夾（只讀）。")
    a.add_argument("path", help="來源資料夾")
    a.add_argument("--alias", help="別名，預設用資料夾名")
    a.add_argument("--check", action="store_true", help="只印報告，不寫")
    e = subs.add_parser("exclude", help="排除來源裡的一個夾或檔", description="排除來源裡的一個夾或檔；對象要真的存在。")
    e.add_argument("path", help="來源裡的夾或檔")
    e.add_argument("--kind", required=True, choices=("hidden", "unused"), help="hidden 不想讓 AI 看到；unused 用不到")
    e.add_argument("--reason", default="", help="一句原因")
    rm = subs.add_parser("remove", help="拿掉一個來源（含它的排除）", description="拿掉一個來源（含它的排除）；客戶的檔不動。")
    rm.add_argument("alias", help="來源的別名")
    inc = subs.add_parser("include", help="取消排除一個夾或檔", description="取消排除一個夾或檔；路徑不存在也可以。")
    inc.add_argument("path", help="排除過的夾或檔")
    ls = subs.add_parser("list", help="列出登記的來源與排除", description="列出登記的來源與排除。")
    rl = subs.add_parser("relocate", help="來源資料夾搬家或改名了，換登記的位置",
                         description="來源資料夾搬家或改名了：抽樣比對新位置，換登記的位置；別名、排除、清冊都不動。")
    rl.add_argument("alias", help="來源的別名")
    rl.add_argument("path", help="新位置")
    rl.add_argument("--check", action="store_true", help="只比對、不換")
    pg = subs.add_parser("purge", help="清「不想讓 AI 看到」那夾的殘留",
                         description="排除成「不想讓 AI 看到」之後，清卡的舊版、備份、暫存與工作日誌裡那夾的東西。")
    pg.add_argument("path", help="那條排除（「別名/相對路徑」或路徑）")
    for p in (a, e, rm, inc, ls, rl, pg):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def system_dirs():
    """照搬舊 kbcommon.system_dirs：Windows 系統夾與使用者的 AppData。"""
    env = os.environ
    cands = [env.get("SystemRoot"), env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("ProgramData"),
             str(Path.home() / "AppData")]
    if os.name == "nt" and not env.get("SystemRoot"):
        cands.append("C:\\Windows")
    return [c for c in cands if c]


def _overlap(a, b):
    """兩個已 normalize 的路徑互相包含（含相等）。"""
    return inside(a, b) or inside(b, a)


def _realcase(p):
    """給人看的路徑（L1）：存在的部分用磁碟上的大小寫（realpath），不存在的尾段原樣接回；一律正斜線。"""
    cur, tail = str(p), []
    try:
        while not os.path.exists(cur):
            head, name = os.path.split(cur)
            if not name or head == cur:
                return str(p).replace("\\", "/")
            tail.insert(0, name)
            cur = head
        real = os.path.realpath(cur)
    except (OSError, ValueError):
        return str(p).replace("\\", "/")
    return (os.path.join(real, *tail) if tail else real).replace("\\", "/")


def _display(spec, base):
    """spec 展開（paths.resolve_spec，N8）後給人看：回 (路徑字串或 None, 存不存在)。"""
    root = resolve_spec(spec, base) if spec else None
    if root is None:
        return None, False
    return _realcase(root), os.path.exists(root)


def _reject(reason):
    print(reason)
    return 3


def _clean_alias(alias):
    return str(alias).rstrip(" .")


def _alias_problem(alias, raw_sources):
    """M2：空、.、..、Windows 不允許的字元或控制字元、保留字、跟既有別名重複（不分大小寫）。alias 已去掉結尾空白與點。"""
    if alias in ("", ".", ".."):
        return "別名不能是空的、「.」或「..」"
    if any(ch in ALIAS_BAD_CHARS or ord(ch) < 32 for ch in alias):
        return f"別名「{alias}」不能含 {' '.join(ALIAS_BAD_CHARS)} 或控制字元"
    if alias.split(".")[0].upper() in DEVICE_NAMES:
        return f"別名「{alias}」是 Windows 保留的裝置名，建不出這個夾；請用 --alias 換一個"
    low = alias.lower()
    if low in {r.lower() for r in sources_lib.RESERVED_ALIASES}:
        return f"別名「{alias}」是保留字，檔案索引裡留給知識庫自己用；請用 --alias 換一個"
    if any(str(s.get("alias", "")).lower() == low for s in raw_sources):
        return f"別名「{alias}」已經有了（不分大小寫）；請用 --alias 換一個"
    return None


def _home_n():
    return normalize(str(Path.home()), None)


def _warnings(t, spec, files):
    out = []
    if files == 0:
        out.append("份數 0，這個資料夾裡沒有檔案")
    if is_abs_spec(spec):
        out.append("這個位置存的是絕對路徑，換電腦會找不到")
    if t.name in HOME_TOP_FOLDERS and normalize(str(t.parent), None) == _home_n():
        out.append(f"這是整個「{t.name}」資料夾，範圍很大，可以只加裡面的某個資料夾")
    return out


def _sources_path(vault):
    return Path(vault) / sources_lib.SOURCES_REL


def _journal(vault, note, command, inventory_changed=False, more=()):
    """程式段（D-085）：note 是印給人的第一行；對話裡叫的（有 sid 且還沒答完）不寫，改動已併進那一句。
    folders 不會開帶 sid 的回合，所以寫的時候判斷跟指令開始時一樣。改到清冊時 changed 多一筆（清冊題）；more 是卡與總目錄。"""
    changed = [{"path": sources_lib.SOURCES_REL, "action": "modify"}]
    if inventory_changed:
        changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    changed += list(more)
    journal_cli.record_program_or_warn(vault, note, changed, command, journal_cli.in_conversation(vault))


def _backup_then_write(vault, data):
    """呼叫端持有 sources.json 的鎖：先備進目前回合（D-074），再原子寫回。
    回 (回合代號, 寫之前的原文)：清冊備進同一回合；清冊階段失敗時用原文寫回（審閱裁定 36）。"""
    turn_id = turn.current(vault)["turn"]
    turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
    original = kio.read_text(_sources_path(vault))
    write_json_atomic(_sources_path(vault), data)
    return turn_id, original


class _InventoryFailed(Exception):
    """清冊階段失敗、sources.json 已寫回原文：照「沒有寫入」說。"""

    def __init__(self, cause):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


class _InventoryLagged(Exception):
    """清冊階段失敗、sources.json 也寫不回去：登記表已改、清冊沒跟上。"""


def _restore_sources(vault, original):
    kio.write_text_atomic(_sources_path(vault), original)


def _inventory_step(vault, original, step, *args):
    """D-095 一筆交易：清冊這一步丟例外就把 sources.json 寫回原文（呼叫端還持有它的鎖），再往外丟。"""
    try:
        return step(*args)
    except Exception as e:  # noqa: BLE001  清冊階段任何失敗都要收回登記表
        try:
            _restore_sources(vault, original)
        except Exception:  # noqa: BLE001
            raise _InventoryLagged(f"{type(e).__name__}: {e}") from e
        raise _InventoryFailed(e) from e


def _inventory_bytes(vault):
    try:
        return (Path(vault) / inventory.INVENTORY_REL).read_bytes()
    except FileNotFoundError:
        return None


def _reconcile(vault, raw, turn_id):
    """exclude、remove 寫完 sources 後（呼叫端還持有 sources.json 的鎖）：清冊對新登記表。
    回 ({"excluded", "removed"}, 有沒有改到清冊)；hidden 排除的條目整筆拿掉（審閱裁定 40）。"""
    before = _inventory_bytes(vault)
    result = inventory.reconcile(vault, sources_lib.Sources(vault, raw), turn_id)
    return result, _inventory_bytes(vault) != before


def _scan_source(vault, raw, alias, turn_id):
    """add、include：只掃那個來源；turn_id 是 None（--check）只比不套。回 (report, 有沒有改到清冊)。"""
    report = inventory.scan(vault, sources_lib.Sources(vault, raw), only_roots=[alias])
    if turn_id is None or not inventory.has_changes(vault, report):
        return report, False
    inventory.apply(vault, report, turn_id)
    return report, True


def _credential_rels(report, alias):
    """這個來源底下 read 是 credential 的檔（相對來源根），照發現順序。"""
    key = alias.casefold()
    return [rel for e in report["entries"] if e.get("read") == "credential" and e.get("status") == "active"
            for a, rel in (inventory.split_uri(loc["uri"]) for loc in e["locations"]) if a.casefold() == key]


def _snapshot_status(vault):
    try:
        return {e.get("id"): e.get("status") for e in inventory.load(vault)["entries"]}
    except ValueError:
        return {}


def _gone(before, after):
    """對帳後不讀了的條目：之前 active／missing，現在整筆拿掉（hidden）或改成 excluded。"""
    return sorted(i for i, st in before.items()
                  if st in ("active", "missing") and (i not in after or after[i] == "excluded"))


def _drop_cards(vault, ids, turn_id):
    """D-174：那些條目的檔案卡拿掉（備進回合）、重寫總目錄與綜合卡快取。回 (拿掉的卡路徑, 標題, changed)。"""
    groups = cards._scan_groups(vault)[0]
    group = [c for cid in ids for c in groups.get(cid, [])]
    removed, titles, changed = [], [], []
    for c in group:
        cards.remove_card(vault, c["path"], turn_id)
        removed.append(c["path"])
        titles.append(cards._title(c))
        changed.append({"path": c["path"], "action": "delete"})
    if removed:
        cards.render_index(vault, turn_id)
        changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
        changed += [{"path": rel, "action": "modify"}
                    for rel in cards.refresh_synthesis(vault, cards.entity_names_of(group), turn_id)]
    return removed, titles, changed


def _purge_sentence(got):
    found = f"、推算 {got['findings']} 條" if got["findings"] else ""
    return (f"清好了：卡 {got['cards']} 張、備份 {got['backups']} 份、暫存 {got['temp']} 份{found}、"
            f"工作日誌 {got['journal']} 段；{PURGE_TAIL}")


def _new_locations(report):
    return len(report["added"]) + len(report["duplicates"]) + len(report["moved"])


def _add(vault, args, as_json):
    t = Path(args.path).resolve()
    if not t.exists():
        return _reject(f"找不到 {t}")
    if not t.is_dir():
        return _reject(f"{t} 是檔案，不能當來源；請指定資料夾")
    if os.path.splitdrive(str(t))[1].strip("\\/") == "":
        return _reject("整顆磁碟不能當來源，請指定裡面的資料夾")
    tn = normalize(str(t), None)
    for s in system_dirs():
        if _overlap(tn, normalize(s, None)):
            return _reject(f"系統資料夾（{s}）不能當來源，也不能包住它")
    if _overlap(tn, normalize(str(vault), None)):
        return _reject("總資料夾和來源不能互相包含")
    other = guard.find_vault(str(t))
    if other is not None:
        return _reject(f"{_realcase(other)} 是另一個知識庫，不能當來源")
    alias = _clean_alias(args.alias if args.alias is not None else t.name)
    _creds, _files, _truncated, inner = credentials.scan_all(t, stop_marker=sources_lib.SOURCES_REL)
    if inner is not None:
        return _reject(f"裡面有另一個知識庫 {_realcase(inner)}，不能當來源")
    spec = compress_spec(t, vault, config.get(vault, "source_spec_max_up"))

    entry_id = None
    lock = contextlib.nullcontext() if args.check else FileLock(_sources_path(vault))
    with lock:  # L5：讀、檢查重疊與別名、備份、寫在同一把鎖裡；清冊也在這把鎖裡（D-095）
        raw = sources_lib.load_raw(vault)
        for e in sources_lib.Sources(vault, raw).entries:
            if _overlap(tn, e["root"]):
                return _reject(f"和既有來源「{e['alias']}」重疊，同一份檔會有兩張卡；要細分請在來源裡建子夾")
        problem = _alias_problem(alias, raw["sources"])
        if problem:
            return _reject(problem)
        if args.check:
            trial = dict(raw, sources=raw["sources"] + [{"id": "", "alias": alias, "spec": spec, "excludes": []}])
            report, inv_changed = _scan_source(vault, trial, alias, None)
        else:
            entry_id = sources_lib.new_id(s.get("id") for s in raw["sources"])
            raw["sources"].append({"id": entry_id, "alias": alias, "spec": spec, "excludes": []})
            turn_id, original = _backup_then_write(vault, raw)
            report, inv_changed = _inventory_step(vault, original, _scan_source, vault, raw, alias, turn_id)

    files = report["total"]  # 第一行的份數與清冊入冊同一個數（審閱裁定 7、26）
    creds = _credential_rels(report, alias)
    warnings = _warnings(t, spec, files)
    first = f"{'會登記' if args.check else '已登記'}「{alias}」（{spec}，{files} 份）"
    cred_tail = f"（檔名像密碼或金鑰 {len(creds)} 份不讀）" if creds else ""
    second = f"清冊：{'會入冊' if args.check else '入冊'} {files} 份{cred_tail}"
    if not args.check:
        _journal(vault, first, "folders add", inv_changed)
    if as_json:
        print(json.dumps({"id": entry_id, "alias": alias, "spec": spec, "path": str(t), "files": files,
                          "credential_files": creds, "warnings": warnings, "check": bool(args.check),
                          "inventory": {"added": files, "restored": len(report["restored"]), "credential": len(creds)}},
                         ensure_ascii=False))
        return 0
    print(first)
    print(second)
    if creds:
        shown = "、".join(f"{alias}/{c}" for c in creds[:CREDENTIAL_LIST_MAX])
        more = f"…還有 {len(creds) - CREDENTIAL_LIST_MAX} 份" if len(creds) > CREDENTIAL_LIST_MAX else ""
        print(f"- 其中 {len(creds)} 份檔名像密碼或金鑰，不會讀：{shown}{more}")
    for w in warnings:
        print(f"- 提醒：{w}")
    return 0


def _raw_entry(raw, best):
    """M3：寫回時找原始條目：有 id 用 id，沒有用 alias；都找不到回 None。"""
    if best["id"]:
        for s in raw["sources"]:
            if s.get("id") == best["id"]:
                return s
        return None
    for s in raw["sources"]:
        if not s.get("id") and s.get("alias") == best["alias"]:
            return s
    return None


def _exclude(vault, args, as_json):
    t = Path(args.path).resolve()
    if not t.exists():
        return _reject(f"找不到 {t}；排除的對象要真的存在")
    tn = normalize(str(t), None)
    with FileLock(_sources_path(vault)):
        raw = sources_lib.load_raw(vault)
        resolved = sources_lib.Sources(vault, raw)
        best = None
        for e in resolved.entries:
            if inside(tn, e["root"]) and (best is None or len(e["root"]) > len(best["root"])):
                best = e
        if best is None:
            return _reject(f"{t} 不在任何登記的來源裡；先用 folders add 登記")
        if tn == best["root"]:
            return _reject(f"整個來源用 folders remove {best['alias']}")
        for ex in best["excludes"]:
            if inside(tn, ex["root"]):
                return _reject(f"已經在排除裡：{_realcase(ex['root'])}")
        entry = _raw_entry(raw, best)
        if entry is None:
            shown = best["alias"] if best["alias"] != "?" else f"沒有別名、spec {best['root']}"  # Sources 對沒別名的填 ?，不印給人
            return _reject(f"sources.json 裡找不到「{shown}」這筆，沒有寫入")
        rel = os.path.relpath(str(t), os.path.realpath(best["root"])).replace("\\", "/")
        added = date.today().isoformat()
        row = {"spec": rel, "kind": args.kind, "reason": args.reason or "", "added": added}
        if args.kind == "hidden":
            row["purge"] = sources_lib.PURGE_PENDING  # D-174：殘留清完才改成日期
        entry.setdefault("excludes", []).append(row)
        before = _snapshot_status(vault)
        turn_id, original = _backup_then_write(vault, raw)
        result, inv_changed = _inventory_step(vault, original, _reconcile, vault, raw, turn_id)
        ids = _gone(before, _snapshot_status(vault))
        removed, titles, card_changed = _drop_cards(vault, ids, turn_id)
        name = f"{best['alias']}/{rel}"
        first = f"已排除「{name}」（{KIND_TEXT[args.kind]}）。卡拿掉 {len(removed)} 張。"
        _journal(vault, first, "folders exclude", inv_changed, card_changed)  # 先記：接著清的時候這段也一起遮
        rewrite, purged = [], None
        if args.kind == "hidden":
            rewrite = purge.blocking(vault, set(purge.target(vault, best["alias"], rel)["ids"]) | set(ids))
            if not rewrite:  # 沒有受影響的綜合卡：同一個指令接著清（D-174）
                purged = purge.purge(vault, best["alias"], rel, str(t), turn_id)
                purge.mark_done(vault, best["alias"], rel, turn_id)
    if as_json:
        print(json.dumps({"source_id": best["id"], "alias": best["alias"], **row, "inventory": result,
                          "cards_removed": len(removed), "rewrite": rewrite, "removed_titles": titles,
                          "purge": {k: v for k, v in purged.items() if k != "ids"} if purged else None,
                          "purge_command": f'kb folders purge "{name}"' if rewrite else None},
                         ensure_ascii=False))
        return 0
    print(first)
    if args.kind == "hidden":
        print(f"清冊：{result['removed']} 份已拿掉")
    else:
        print(f"清冊：{result['excluded']} 份標為不讀")
    if rewrite:
        what = f"拿掉「{titles[0]}」等 {len(titles)} 張卡的內容" if titles else "拿掉那夾的內容"
        print(f"接著要重寫的綜合卡：{'、'.join(rewrite)}（{what}），寫完跑 kb folders purge \"{name}\"")
    elif purged is not None:
        print(_purge_sentence(purged))
    return 0


def _remove(vault, args, as_json):
    wanted = _clean_alias(args.alias.strip())
    if not wanted:
        return _reject("別名不能是空的；先用 folders list 看有哪些來源")
    with FileLock(_sources_path(vault)):
        raw = sources_lib.load_raw(vault)
        low = wanted.lower()
        entry = next((s for s in raw["sources"] if str(s.get("alias", "")).lower() == low), None)
        if entry is None:
            return _reject(f"沒有叫「{wanted}」的來源")
        raw["sources"].remove(entry)
        before = _snapshot_status(vault)
        turn_id, original = _backup_then_write(vault, raw)
        result, inv_changed = _inventory_step(vault, original, _reconcile, vault, raw, turn_id)
        removed, _titles, changed = _drop_cards(vault, _gone(before, _snapshot_status(vault)), turn_id)
    alias = entry.get("alias", "")
    first = f"已拿掉來源「{alias}」；你的檔案沒動，從現在起不讀它；卡拿掉 {len(removed)} 張；說「退回上一版」救得回。"
    _journal(vault, first, "folders remove", inv_changed, changed)
    if as_json:
        print(json.dumps({"id": entry.get("id", ""), "alias": alias, "spec": entry.get("spec", ""),
                          "inventory": result, "cards_removed": len(removed)}, ensure_ascii=False))
        return 0
    print(first)
    print(f"清冊：{result['excluded']} 份標為不讀")
    return 0


def _include(vault, args, as_json):
    t = Path(args.path).resolve()
    tn = normalize(str(t), None)
    with FileLock(_sources_path(vault)):
        raw = sources_lib.load_raw(vault)
        rows, in_source = [], False  # rows：(來源, 排除列, 排除展開後的 root)
        for src in raw["sources"]:
            root = resolve_spec(src["spec"], str(vault)) if src.get("spec") else None
            if root is None:
                continue
            in_source = in_source or inside(tn, root)
            rows += [(src, ex, resolve_spec(ex["spec"], root)) for ex in src.get("excludes") or [] if ex.get("spec")]
        found = next(((src, ex) for src, ex, ex_root in rows if ex_root == tn), None)
        if found is None:
            shown = _realcase(t)
            outer = next(((src, ex) for src, ex, ex_root in rows if ex_root and inside(tn, ex_root)), None)
            if outer is not None:
                return _reject(f"{shown} 在排除「{outer[0].get('alias', '')}/{outer[1]['spec']}」裡面；要恢復就取消那條")
            if not in_source:
                return _reject(f"{shown} 不在任何登記的來源裡；先用 folders add 登記")
            return _reject(f"{shown} 不在排除清單裡，本來就讀得到")
        s, ex = found
        s["excludes"].remove(ex)
        turn_id, original = _backup_then_write(vault, raw)
        report, inv_changed = _inventory_step(vault, original, _scan_source, vault, raw, s.get("alias", ""), turn_id)
    alias = s.get("alias", "")
    first = f"已取消排除「{alias}/{ex['spec']}」，從現在起讀得到。"
    _journal(vault, first, "folders include", inv_changed)
    restored, added = len(report["restored"]), _new_locations(report)
    if as_json:
        print(json.dumps({"source_id": s.get("id", ""), "alias": alias, "spec": ex["spec"], "kind": ex.get("kind", ""),
                          "inventory": {"added": added, "restored": restored,
                                        "credential": report["read_counts"]["credential"]}}, ensure_ascii=False))
        return 0
    print(first)
    print(f"清冊：恢復 {restored} 份、新增 {added} 份")
    return 0


def _list(vault, as_json):
    raw = sources_lib.load_raw(vault)
    out = []
    for s in raw["sources"]:
        spec = s.get("spec", "")
        path, exists = _display(spec, str(vault))
        excludes = []
        for ex in s.get("excludes", []) or []:
            ex_path, ex_exists = _display(ex.get("spec", ""), path) if path else (None, False)
            excludes.append({"spec": ex.get("spec", ""), "kind": ex.get("kind", ""), "reason": ex.get("reason", ""),
                             "added": ex.get("added", ""), "path": ex_path, "exists": ex_exists})
        out.append({"id": s.get("id", ""), "alias": s.get("alias", ""), "spec": spec, "path": path, "exists": exists,
                    "excludes": excludes})
    if as_json:
        print(json.dumps({"sources": out}, ensure_ascii=False))
        return 0
    if not out:
        print("還沒有登記任何來源")
        return 0
    for s in out:
        shown = s["path"] if s["path"] is not None else "展不開"
        print(f"「{s['alias']}」 {s['spec']} → {shown}" + ("" if s["exists"] else "（這台電腦找不到）"))
        for ex in s["excludes"]:
            parts = "，".join(x for x in (ex["kind"], ex["reason"], ex["added"]) if x)
            print(f"  - 排除 {ex['spec']}（{parts}）")
    return 0


def _purge(vault, args, as_json):
    with FileLock(_sources_path(vault)):
        sources = sources_lib.Sources(vault, sources_lib.load_raw(vault))
        ex = purge.find(vault, sources, args.path, os.getcwd())
        shown = args.path.strip()
        if ex is None or ex.get("kind") != "hidden" or ex.get("purge") != sources_lib.PURGE_PENDING:
            name = f"{ex['alias']}/{ex['spec']}" if ex else shown
            print(json.dumps({"purged": False, "name": name}, ensure_ascii=False) if as_json
                  else NOTHING_TO_PURGE.format(name=name))
            return 0
        name = f"{ex['alias']}/{ex['spec']}"
        turn_id = turn.current(vault)["turn"]
        try:
            got = purge.purge(vault, ex["alias"], ex["spec"], ex["root"], turn_id)
        except purge.Blocked as b:
            print(PURGE_BLOCKED.format(names="、".join(b.args[0])))
            return 3
        purge.mark_done(vault, ex["alias"], ex["spec"], turn_id)
    sentence = _purge_sentence(got)
    journal_cli.record_program_or_warn(vault, sentence, [{"path": sources_lib.SOURCES_REL, "action": "modify"}],
                                       "folders purge", journal_cli.in_conversation(vault))
    if as_json:
        print(json.dumps(dict({k: v for k, v in got.items() if k != "ids"}, purged=True, name=name), ensure_ascii=False))
    else:
        print(sentence)
    return 0


def _sample(vault, alias, new_root):
    """D-177：清冊這個別名 active 的條目抽最多 relocate_sample_max 份（照 uri 排、平均挑），看新位置同一個相對路徑：
    大小相同再算指紋（沒指紋的、只在雲端的只比大小）。回 (抽了幾份, 對得上幾份, 對不上的相對路徑)。"""
    key = alias.casefold()
    pool = sorted((e for e in inventory.load(vault)["entries"] if e.get("status") == "active" and e.get("locations")
                   and inventory.split_uri(e["locations"][0]["uri"])[0].casefold() == key),
                  key=lambda e: inventory.uri_key(e["locations"][0]["uri"]))
    n = config.get(vault, "relocate_sample_max")
    if len(pool) > n:
        step = len(pool) / n
        pool = [pool[int(i * step)] for i in range(n)]
    ok, misses = 0, []
    for e in pool:
        loc = e["locations"][0]
        rel = inventory.split_uri(loc["uri"])[1]
        p = Path(new_root).joinpath(*rel.split("/"))
        try:
            st = os.stat(p)
        except OSError:
            misses.append(rel)
            continue
        same = st.st_size == loc.get("size")
        fps = e.get("fingerprints") or []
        cloud = getattr(st, "st_file_attributes", 0) & inventory.PLACEHOLDER_ATTRS
        if same and fps and e.get("read") == "ok" and not cloud:
            try:
                same = inventory.sha256_of(p) == fps[-1].get("sha256")
            except OSError:
                same = False
        if same:
            ok += 1
        else:
            misses.append(rel)
    return len(pool), ok, misses


def _relocate_lines(t, spec, total, ok, misses, excludes):
    lines = [f"新位置：{_realcase(t)}" + ("（換電腦會找不到，建議放在知識庫旁邊）" if is_abs_spec(spec) else "")]
    if total:
        bad = total - ok
        lines.append(f"抽樣 {total} 份：{ok} 份對得上" + (f"，{bad} 份不在原本的位置（例：{'、'.join(misses[:3])}）" if bad else ""))
    else:
        lines.append("清冊裡還沒有這個來源的檔，沒有抽樣")
    for ex_spec, found in excludes:
        lines.append(f"不讀的夾：{ex_spec} 在新位置{'找到了' if found else '找不到'}")
    return lines


def _relocate(vault, args, as_json):
    wanted = _clean_alias(args.alias.strip())
    if wanted.casefold() in {a.casefold() for a in sources_lib.RESERVED_ALIASES}:
        return _reject(RESERVED_RELOCATE.format(alias=wanted))
    t = Path(args.path).resolve()
    if not t.exists():
        return _reject(f"找不到 {t}")
    if not t.is_dir():
        return _reject(f"{t} 是檔案，不能當來源；請指定資料夾")
    if os.path.splitdrive(str(t))[1].strip("\\/") == "":
        return _reject("整顆磁碟不能當來源，請指定裡面的資料夾")
    tn = normalize(str(t), None)
    for s in system_dirs():
        if _overlap(tn, normalize(s, None)):
            return _reject(f"系統資料夾（{s}）不能當來源，也不能包住它")
    if _overlap(tn, normalize(str(vault), None)):
        return _reject("總資料夾和來源不能互相包含")
    other = guard.find_vault(str(t))
    if other is not None:
        return _reject(f"{_realcase(other)} 是另一個知識庫，不能當來源")
    _creds, _files, _truncated, inner = credentials.scan_all(t, stop_marker=sources_lib.SOURCES_REL)
    if inner is not None:
        return _reject(f"裡面有另一個知識庫 {_realcase(inner)}，不能當來源")
    spec = compress_spec(t, vault, config.get(vault, "source_spec_max_up"))
    lock = contextlib.nullcontext() if args.check else FileLock(_sources_path(vault))
    with lock:
        raw = sources_lib.load_raw(vault)
        entry = next((s for s in raw["sources"] if str(s.get("alias", "")).casefold() == wanted.casefold()), None)
        if entry is None:
            return _reject(f"沒有叫「{wanted}」的來源")
        alias = entry.get("alias", "")
        for e in sources_lib.Sources(vault, raw).entries:
            if e["alias"].casefold() != alias.casefold() and _overlap(tn, e["root"]):
                return _reject(f"和既有來源「{e['alias']}」重疊，同一份檔會有兩張卡；要細分請在來源裡建子夾")
        total, ok, misses = _sample(vault, alias, t)
        excludes = [(ex.get("spec", ""), resolve_spec(ex["spec"], str(t)) is not None
                     and os.path.exists(resolve_spec(ex["spec"], str(t))))
                    for ex in entry.get("excludes") or [] if ex.get("spec")]
        lines = _relocate_lines(t, spec, total, ok, misses, excludes)
        if args.check:
            if as_json:
                print(json.dumps({"alias": alias, "spec": spec, "path": str(t), "sampled": total, "matched": ok,
                                  "misses": misses, "excludes": [{"spec": s_, "found": f} for s_, f in excludes],
                                  "check": True, "lines": lines}, ensure_ascii=False))
            else:
                print("\n".join(lines))
            return 0
        old_spec = entry.get("spec", "")
        entry["spec"] = spec
        turn_id, original = _backup_then_write(vault, raw)
        report, inv_changed = _inventory_step(vault, original, _scan_source, vault, raw, alias, turn_id)
        got = cards.sync(vault, sources_lib.Sources(vault, raw), turn_id)
        changed = list(got["changed"])
        if got["moved"] or got["updated"] or got["dropped"]:
            cards.render_index(vault, turn_id)
            changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
            changed += [{"path": rel, "action": "modify"} for rel in cards.refresh_synthesis(vault, got["touched"], turn_id)]
    env.clear_missing(vault, [entry.get("id") or alias])
    first = f"已把「{alias}」換到新位置：{_realcase(t)}；說「退回上一版」換得回來"
    _journal(vault, first, "folders relocate", inv_changed, changed)
    if as_json:
        print(json.dumps({"alias": alias, "spec": spec, "old_spec": old_spec, "path": str(t), "sampled": total,
                          "matched": ok, "misses": misses, "excludes": [{"spec": s_, "found": f} for s_, f in excludes],
                          "check": False, "cards_updated": len(got["moved"]) + len(got["updated"])}, ensure_ascii=False))
        return 0
    print(first)
    print("\n".join(lines))
    return 0


def _vault(args):
    found = guard.find_vault(args.vault or os.getcwd())
    return Path(os.path.realpath(str(found))) if found is not None else None


def run(args):
    vault = _vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    if args.folders_command != "list":
        try:  # 清冊讀不到先擋，sources.json 一行都不改（I-046、清冊審閱裁定 4）
            inventory.load(vault)
        except ValueError as e:
            print(f"清冊讀不到：{e}")
            return 3
    try:
        if args.folders_command == "add":
            return _add(vault, args, as_json)
        if args.folders_command == "exclude":
            return _exclude(vault, args, as_json)
        if args.folders_command == "remove":
            return _remove(vault, args, as_json)
        if args.folders_command == "include":
            return _include(vault, args, as_json)
        if args.folders_command == "relocate":
            return _relocate(vault, args, as_json)
        if args.folders_command == "purge":
            return _purge(vault, args, as_json)
        return _list(vault, as_json)
    except _InventoryLagged as e:  # 清冊階段失敗、登記表也寫不回去（審閱裁定 36）
        print(f"來源已改、清冊沒跟上：{e}，請跑 kb inventory scan")
        return 3
    except _InventoryFailed as e:  # 清冊階段失敗、登記表已寫回原文
        if isinstance(e.cause, TimeoutError):
            print(f"等不到 {inventory.INVENTORY_POSIX} 的鎖（別的指令正在改它），沒有寫入；請稍後再試")
        else:
            print(f"沒有寫入：{e}")
        return 3
    except ValueError as e:  # L7、N6：sources.json 壞掉或格式不對
        print(f"{sources_lib.SOURCES_REL} 讀不了，請先修好它：{type(e).__name__}: {e}")
        return 3
    except TimeoutError:  # N6：FileLock 等不到
        print(f"等不到 {sources_lib.SOURCES_REL} 的鎖（別的指令正在改它），沒有寫入；請稍後再試")
        return 3
    except OSError as e:  # N6：備份或寫檔失敗
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
