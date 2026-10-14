"""kb folders add／exclude／list：登記來源資料夾、排除裡面的夾或檔、列登記表（D-077、D-021 補、D-042、D-073）。

add 先過拒絕條件（不存在、整顆磁碟、系統夾、跟總資料夾或既有來源互相包含、另一個知識庫，I-050、I-054），
再數份數並列出憑證樣式的檔（只數、不讀內容），spec 用 paths.compress_spec 壓成可攜；改 sources.json 前先備進目前回合（D-074）。
exclude 的對象要真的存在（I-033），spec 相對該來源根；兩種 kind 守門都不讀（D-024），清殘留延後。
--check 只印報告不寫；拒絕一律第一行原因、exit 3；argparse 參數錯 exit 2。不碰 settings.json（D-079）。
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

from kb import guard
from kb._lib import config, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.paths import compress_spec, inside, is_abs_spec, normalize, resolve_spec

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行，或加 --vault"
HOME_TOP_FOLDERS = ("Desktop", "Documents", "Downloads", "桌面", "文件", "下載")
CREDENTIAL_LIST_MAX = 10
KIND_TEXT = {"hidden": "不想讓 AI 看到", "unused": "用不到"}
HIDDEN_TAIL = "之前讀過的已送過雲端；這版還沒有卡和備份要清。"


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
    ls = subs.add_parser("list", help="列出登記的來源與排除", description="列出登記的來源與排除。")
    for p in (a, e, ls):
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


def _vault_at_or_above(p):
    for d in [p, *p.parents]:
        if (d / sources_lib.SOURCES_REL).is_file():
            return d
    return None


def _reject(reason):
    print(reason)
    return 3


def _alias_problem(alias, raw_sources):
    if not alias.strip():
        return "別名不能是空的"
    if "/" in alias or "\\" in alias:
        return f"別名「{alias}」不能含斜線"
    if alias in sources_lib.RESERVED_ALIASES:
        return f"「{alias}」是保留字，檔案索引裡留給知識庫自己用；請用 --alias 換一個"
    if any(s.get("alias") == alias for s in raw_sources):
        return f"別名「{alias}」已經有了；請用 --alias 換一個"
    return None


def _survey(t):
    """os.walk 容錯版（I-021）：只數檔案份數、收集憑證樣式的相對路徑；走到另一個知識庫就回它。不讀內容、不跟隨符號連結。"""
    files, creds = 0, []
    marker = sources_lib.SOURCES_REL.split("/")

    def onerror(_e):
        pass

    for dirpath, _dirnames, filenames in os.walk(str(t), onerror=onerror):
        if dirpath != str(t) and os.path.isfile(os.path.join(dirpath, *marker)):
            return files, creds, dirpath
        rel_dir = os.path.relpath(dirpath, str(t)).replace("\\", "/")
        for name in filenames:
            files += 1
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if guard.is_credential(rel):
                creds.append(rel)
    return files, creds, None


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


def _backup_then_save(vault, data):
    turn_id = turn.current(vault)["turn"]
    turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
    sources_lib.save(vault, data)


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
    raw = sources_lib.load_raw(vault)
    resolved = sources_lib.Sources(vault, raw)
    for e in resolved.entries:
        if _overlap(tn, e["root"]):
            return _reject(f"和既有來源「{e['alias']}」重疊，同一份檔會有兩張卡；要細分請在來源裡建子夾")
    other = _vault_at_or_above(t)
    if other is not None:
        return _reject(f"{other} 是另一個知識庫，不能當來源")
    alias = args.alias if args.alias is not None else t.name
    problem = _alias_problem(alias, raw["sources"])
    if problem:
        return _reject(problem)
    files, creds, inner = _survey(t)
    if inner is not None:
        return _reject(f"裡面有另一個知識庫 {inner}，不能當來源")
    spec = compress_spec(t, vault, config.get(vault, "source_spec_max_up"))
    warnings = _warnings(t, spec, files)

    entry_id = None
    if not args.check:
        entry_id = sources_lib.new_id(s.get("id") for s in raw["sources"])
        raw["sources"].append({"id": entry_id, "alias": alias, "spec": spec, "excludes": []})
        _backup_then_save(vault, raw)

    if as_json:
        print(json.dumps({"id": entry_id, "alias": alias, "spec": spec, "path": str(t), "files": files,
                          "credential_files": creds, "warnings": warnings, "check": bool(args.check)}, ensure_ascii=False))
        return 0
    print(f"{'會登記' if args.check else '已登記'}「{alias}」（{spec}，{files} 份）")
    if creds:
        shown = "、".join(f"{alias}/{c}" for c in creds[:CREDENTIAL_LIST_MAX])
        more = f"…還有 {len(creds) - CREDENTIAL_LIST_MAX} 份" if len(creds) > CREDENTIAL_LIST_MAX else ""
        print(f"- 其中 {len(creds)} 份檔名像密碼或金鑰，不會讀：{shown}{more}")
    for w in warnings:
        print(f"- 提醒：{w}")
    return 0


def _exclude(vault, args, as_json):
    t = Path(args.path).resolve()
    if not t.exists():
        return _reject(f"找不到 {t}；排除的對象要真的存在")
    tn = normalize(str(t), None)
    raw = sources_lib.load_raw(vault)
    resolved = sources_lib.Sources(vault, raw)
    best = None
    for e in resolved.entries:
        if inside(tn, e["root"]) and (best is None or len(e["root"]) > len(best["root"])):
            best = e
    if best is None:
        return _reject(f"{t} 不在任何登記的來源裡；先用 folders add 登記")
    if tn == best["root"]:
        return _reject("整個來源的拿掉這版還沒有")
    for ex in best["excludes"]:
        if inside(tn, ex["root"]):
            return _reject(f"已經在排除裡：{ex['root']}")
    rel = os.path.relpath(str(t), os.path.realpath(best["root"])).replace("\\", "/")
    added = date.today().isoformat()
    row = {"spec": rel, "kind": args.kind, "reason": args.reason or "", "added": added}
    for s in raw["sources"]:
        if s.get("id") == best["id"] and s.get("alias") == best["alias"]:
            s.setdefault("excludes", []).append(row)
            break
    _backup_then_save(vault, raw)
    if as_json:
        print(json.dumps({"source_id": best["id"], "alias": best["alias"], **row}, ensure_ascii=False))
        return 0
    name = f"{best['alias']}/{rel}"
    tail = HIDDEN_TAIL if args.kind == "hidden" else ""
    print(f"已排除「{name}」（{KIND_TEXT[args.kind]}）。{tail}")
    return 0


def _display(root):
    """展開後的路徑給人看：存在就用磁碟上的大小寫、正斜線；回 (字串或 None, 存不存在)。"""
    if root is None:
        return None, False
    if os.path.exists(root):
        return os.path.realpath(root).replace("\\", "/"), True
    return root, False


def _list(vault, as_json):
    raw = sources_lib.load_raw(vault)
    vn = normalize(str(vault), None)
    out = []
    for s in raw["sources"]:
        spec = s.get("spec", "")
        root = resolve_spec(spec, vn) if spec else None
        path, exists = _display(root)
        excludes = []
        for ex in s.get("excludes", []) or []:
            ex_root = resolve_spec(ex.get("spec", ""), root) if root and ex.get("spec") else None
            ex_path, ex_exists = _display(ex_root)
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
    if args.folders_command == "add":
        return _add(vault, args, as_json)
    if args.folders_command == "exclude":
        return _exclude(vault, args, as_json)
    return _list(vault, as_json)
