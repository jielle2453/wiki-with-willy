"""kb folders add／exclude／remove／include／list：登記來源資料夾、排除裡面的夾或檔、拿掉來源、取消排除、列登記表
（D-077、D-021 補、D-042、D-073、specs/2026-10-16-manage.md）。

add 先過拒絕條件（不存在、整顆磁碟、系統夾、跟總資料夾或既有來源互相包含、另一個知識庫，I-050、I-054），
再數份數並列出憑證樣式的檔（credentials.scan_all，只數、不讀內容），spec 用 paths.compress_spec 壓成可攜；
讀、檢查重疊與別名、備份、寫四步在同一把 FileLock 裡（I-080），改 sources.json 前先備進目前回合（D-074）。
exclude 的對象要真的存在（I-033），spec 相對該來源根；寫回時有 id 用 id、沒有用 alias，找不到就不存檔；兩種 kind 守門都不讀（D-024）。
remove 照別名拿掉整筆（含它的排除；別名跟 add 擋重複一樣不分大小寫），客戶的檔不動；include 只比字串（排除的夾可能已被刪），刪 excludes 裡 root 相等的那列。
--check 只印報告不寫；拒絕一律第一行原因、exit 3；sources.json 壞掉也是一行原因 exit 3；argparse 參數錯 exit 2。
印給人看的路徑用原始大小寫。往上找知識庫一律 guard.find_vault。不碰 settings.json（D-079）。
add、exclude、remove、include 寫成功後記一段工作日誌程式段（D-085），note 是印給人的第一行；--check、list 不記。
"""
import argparse
import contextlib
import json
import os
from datetime import date
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import config, credentials, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, write_json_atomic
from kb._lib.paths import compress_spec, inside, is_abs_spec, normalize, resolve_spec

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行，或加 --vault"
HOME_TOP_FOLDERS = ("Desktop", "Documents", "Downloads", "桌面", "文件", "下載")
CREDENTIAL_LIST_MAX = 10
KIND_TEXT = {"hidden": "不想讓 AI 看到", "unused": "用不到"}
HIDDEN_TAIL = "之前讀過的已送過雲端；這版還沒有卡和備份要清。"
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
    for p in (a, e, rm, inc, ls):
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


def _journal(vault, note, command):
    """程式段（D-085）：note 是印給人的第一行；對話裡叫的（有 sid 且還沒答完）不寫，改動已併進那一句。
    folders 不會開帶 sid 的回合，所以寫的時候判斷跟指令開始時一樣。"""
    changed = [{"path": sources_lib.SOURCES_REL, "action": "modify"}]
    journal_cli.record_program_or_warn(vault, note, changed, command, journal_cli.in_conversation(vault))


def _backup_then_write(vault, data):
    """呼叫端持有 sources.json 的鎖：先備進目前回合（D-074），再原子寫回。"""
    turn_id = turn.current(vault)["turn"]
    turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
    write_json_atomic(_sources_path(vault), data)


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
    creds, files, _truncated, inner = credentials.scan_all(t, stop_marker=sources_lib.SOURCES_REL)
    if inner is not None:
        return _reject(f"裡面有另一個知識庫 {_realcase(inner)}，不能當來源")
    spec = compress_spec(t, vault, config.get(vault, "source_spec_max_up"))
    warnings = _warnings(t, spec, files)

    entry_id = None
    lock = contextlib.nullcontext() if args.check else FileLock(_sources_path(vault))
    with lock:  # L5：讀、檢查重疊與別名、備份、寫在同一把鎖裡
        raw = sources_lib.load_raw(vault)
        for e in sources_lib.Sources(vault, raw).entries:
            if _overlap(tn, e["root"]):
                return _reject(f"和既有來源「{e['alias']}」重疊，同一份檔會有兩張卡；要細分請在來源裡建子夾")
        problem = _alias_problem(alias, raw["sources"])
        if problem:
            return _reject(problem)
        if not args.check:
            entry_id = sources_lib.new_id(s.get("id") for s in raw["sources"])
            raw["sources"].append({"id": entry_id, "alias": alias, "spec": spec, "excludes": []})
            _backup_then_write(vault, raw)

    first = f"{'會登記' if args.check else '已登記'}「{alias}」（{spec}，{files} 份）"
    if not args.check:
        _journal(vault, first, "folders add")
    if as_json:
        print(json.dumps({"id": entry_id, "alias": alias, "spec": spec, "path": str(t), "files": files,
                          "credential_files": creds, "warnings": warnings, "check": bool(args.check)}, ensure_ascii=False))
        return 0
    print(first)
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
        entry.setdefault("excludes", []).append(row)
        _backup_then_write(vault, raw)
    name = f"{best['alias']}/{rel}"
    tail = HIDDEN_TAIL if args.kind == "hidden" else ""
    first = f"已排除「{name}」（{KIND_TEXT[args.kind]}）。{tail}"
    _journal(vault, first, "folders exclude")
    if as_json:
        print(json.dumps({"source_id": best["id"], "alias": best["alias"], **row}, ensure_ascii=False))
        return 0
    print(first)
    return 0


def _remove(vault, args, as_json):
    with FileLock(_sources_path(vault)):
        raw = sources_lib.load_raw(vault)
        low = args.alias.lower()
        entry = next((s for s in raw["sources"] if str(s.get("alias", "")).lower() == low), None)
        if entry is None:
            return _reject(f"沒有叫「{args.alias}」的來源")
        raw["sources"].remove(entry)
        _backup_then_write(vault, raw)
    alias = entry.get("alias", "")
    first = f"已拿掉來源「{alias}」；你的檔案沒動，從現在起不讀它；說「退回上一版」救得回。"
    _journal(vault, first, "folders remove")
    if as_json:
        print(json.dumps({"id": entry.get("id", ""), "alias": alias, "spec": entry.get("spec", "")}, ensure_ascii=False))
        return 0
    print(first)
    return 0


def _include(vault, args, as_json):
    t = Path(args.path).resolve()
    tn = normalize(str(t), None)
    with FileLock(_sources_path(vault)):
        raw = sources_lib.load_raw(vault)
        found = None
        for s in raw["sources"]:
            root = resolve_spec(s["spec"], str(vault)) if s.get("spec") else None
            for ex in (s.get("excludes") or []) if root else []:
                if ex.get("spec") and resolve_spec(ex["spec"], root) == tn:
                    found = (s, ex)
                    break
            if found:
                break
        if found is None:
            return _reject(f"{_realcase(t)} 不在排除清單裡")
        s, ex = found
        s["excludes"].remove(ex)
        _backup_then_write(vault, raw)
    alias = s.get("alias", "")
    first = f"已取消排除「{alias}/{ex['spec']}」，從現在起讀得到。"
    _journal(vault, first, "folders include")
    if as_json:
        print(json.dumps({"source_id": s.get("id", ""), "alias": alias, "spec": ex["spec"], "kind": ex.get("kind", "")},
                         ensure_ascii=False))
        return 0
    print(first)
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
    try:
        if args.folders_command == "add":
            return _add(vault, args, as_json)
        if args.folders_command == "exclude":
            return _exclude(vault, args, as_json)
        if args.folders_command == "remove":
            return _remove(vault, args, as_json)
        if args.folders_command == "include":
            return _include(vault, args, as_json)
        return _list(vault, as_json)
    except ValueError as e:  # L7、N6：sources.json 壞掉或格式不對
        print(f"{sources_lib.SOURCES_REL} 讀不了，請先修好它：{type(e).__name__}: {e}")
        return 3
    except TimeoutError:  # N6：FileLock 等不到
        print(f"等不到 {sources_lib.SOURCES_REL} 的鎖（別的指令正在改它），沒有寫入；請稍後再試")
        return 3
    except OSError as e:  # N6：備份或寫檔失敗
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
