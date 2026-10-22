"""kb office check／setup／run／copy／unpack／pack／pdf／recalc／render（produce 第 1 點，D-170）：做文件的工具與副本，本體在 _lib/office。

只管工具與副本，不寫知識庫：輸出都在這一回合的工作區 .system/temp/office/<回合>/（照 temp_keep_hours 清）。
check 只讀；setup 裝到 %LOCALAPPDATA%\\wiki-with-willy\\tools\\（AI 叫之前照 SKILL 先問客戶）；run 只跑工作區（.system/temp/）裡的腳本，
cwd 與 OUT_DIR 是工作區；copy 照 kb convert 同一套判斷（排除、憑證名、Z11、只收清冊 active 的）；除了 check 都不收 --vault、只從 cwd 找知識庫
（實作回報裁定 5：會把原檔內容帶給 AI；收尾 security-review 擴到其他子指令：假的知識庫會讓真知識庫的檔變成「外面」）；pdf、recalc、render 沒有工具回 exit 3 與固定句。
拒絕與做不到一律 exit 3、第一行就是原因；人讀輸出不印清冊編號，要編號取 --json。
"""
import argparse
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

from kb import convert, guard
from kb._lib import inventory, turn
from kb._lib import io as kio
from kb._lib import office
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
NOT_LISTED = "清冊沒有這份檔；先跑 kb inventory scan"
COPY_HEAD = "拿不到副本："
ONLY_HERE = "只收工作區或 AI 產出裡的檔：{path}"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="office_command", metavar="<子子命令>", required=True)
    ck = subs.add_parser("check", help="看這台有什麼做文件的工具", description="列 Office、LibreOffice、Node.js、套件，最後說可以做什麼；不寫檔。")
    st = subs.add_parser("setup", help="裝做文件的工具", description="把 node 與 python 套件裝到這台電腦的工具夾；已裝好就說已裝好。")
    rn = subs.add_parser("run", help="跑工作區裡的做檔腳本", description="照副檔名用 node 或 python 跑；cwd 與 OUT_DIR 是工作區。")
    rn.add_argument("script", help="工作區裡的 .js 或 .py")
    rn.add_argument("args", nargs=argparse.REMAINDER, help="給腳本的參數")
    cp = subs.add_parser("copy", help="拿一份原檔的副本", description="把清冊裡的一份檔複製到工作區；原檔不動。總資料夾從目前目錄往上找。")
    cp.add_argument("target", help="清冊編號（f-…）、uri 或路徑")
    up = subs.add_parser("unpack", help="解開工作區的 Office 檔", description="解開成一個夾（預設 <主檔名>_unpacked），改 XML 用。")
    up.add_argument("file", help="工作區裡的 docx、xlsx、pptx")
    up.add_argument("--to", help="解開到哪個夾（工作區裡）")
    pk = subs.add_parser("pack", help="把夾打包成 Office 檔", description="--like 給原檔時照它的檔內順序，沒改的部件逐位元相同。")
    pk.add_argument("folder", help="工作區裡解開的夾")
    pk.add_argument("--to", required=True, help="輸出的檔名（放在工作區）")
    pk.add_argument("--like", help="原 Office 檔（工作區裡的副本）")
    pd = subs.add_parser("pdf", help="轉 PDF", description="工作區或 AI 產出的檔轉成 PDF，放在工作區。")
    pd.add_argument("file", help="工作區或 AI 產出裡的檔")
    rc = subs.add_parser("recalc", help="Excel 重算", description="工作區的 xlsx 原地重算公式，回公式與錯誤數。")
    rc.add_argument("file", help="工作區裡的 xlsx")
    rd = subs.add_parser("render", help="轉成每頁的圖", description="轉 PDF 再轉每頁 PNG，印路徑給 Read 看版面。")
    rd.add_argument("file", help="工作區或 AI 產出裡的檔")
    rd.add_argument("--pages", help="哪幾頁，例 1-5")
    # 只有 check 收 --vault（只用來印工作區）；其他跟 copy、convert 一樣只信 cwd 的知識庫（收尾 security-review）
    ck.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找（只用來印工作區）")
    for p in (ck, st, rn, cp, up, pk, pd, rc, rd):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


def _fail(as_json, line, **payload):
    _out(as_json, [line], dict({"ok": False, "message": line}, **payload))
    return 3


def _turn_id(vault):
    return turn.current(vault)["turn"]


# ---------- check、setup（不需要知識庫） ----------

def _current_workspace(args):
    """在知識庫裡跑 check 時，這一回合的工作區在哪（只讀：不開回合、不建夾）；不在知識庫或還沒有回合回 None。"""
    vault = find_vault(args)
    if vault is None:
        return None
    data = turn._read_turn(turn._current_file(vault))
    try:
        return office.workspace(vault, data["turn"], make=False) if data else None
    except office.Refused:
        return None


def _check(args, as_json):
    env = office.detect()
    cap = office.capabilities(env)
    ws = _current_workspace(args)
    lines = office.check_lines(env) + ([f"工作區：{ws}（腳本與做出來的檔放這裡）"] if ws else [])
    _out(as_json, lines, {"ok": True, "env": env, "capabilities": cap, "workspace": str(ws) if ws else None})
    return 0


def _setup(as_json):
    res = office.setup()
    if res["already"]:
        lines = ["工具已經裝好了，不用再裝"]
    else:
        parts = []
        if res["python"]:
            parts.append("python 套件 " + "、".join(res["python"]))
        if res["node"]:
            parts.append("node 套件 " + "、".join(res["node"]))
        lines = [f"已裝好：{'；'.join(parts)}（花了 {res['seconds']} 秒）"]
    if res["skipped_node"]:
        lines.append("這台沒有 npm（Node.js 裝得不完整），只裝了 python 那組；新做 Word 或簡報要重裝 Node.js"
                     if res.get("has_node") else "這台沒有 Node.js，只裝了 python 那組；" + office.NO_NODE)
    _out(as_json, lines, dict({"ok": True}, **res))
    return 0


# ---------- copy ----------

def _copy(args, as_json):
    found = guard.find_vault(os.getcwd())  # 只信 cwd 所在的知識庫（同 convert，實作回報裁定 5）
    if found is None:
        return _fail(as_json, convert.NOT_IN_VAULT)
    vault = Path(os.path.realpath(str(found)))
    try:
        sources = sources_lib.load(vault)
        entry, path, _uri = convert.resolve(vault, sources, args.target, os.getcwd())
        convert.gate(vault, sources, entry, path)
        if entry is None or entry.get("status") != "active":
            raise convert.Refused(NOT_LISTED, None)
        ws = office.workspace(vault, _turn_id(vault))
        dst = _free_name(ws / Path(str(path)).name)
        shutil.copy2(path, dst)
        os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)  # 唯讀原檔的副本也要能改、能清（測試後修正裁定 20 L-9）
    except convert.Refused as e:
        return _fail(as_json, COPY_HEAD + e.args[0])
    except office.Refused as e:
        return _fail(as_json, COPY_HEAD + str(e))
    except ValueError as e:
        return _fail(as_json, f"{COPY_HEAD}狀態檔壞了（{e}）")
    except OSError:
        return _fail(as_json, COPY_HEAD + convert.LOCKED)
    _out(as_json, [f"副本：{dst}", "原檔沒動；改好用 kb output save --from 放進 AI 產出"],
         {"ok": True, "copy": str(dst), "id": entry["id"], "workspace": str(ws)})
    return 0


def _free_name(path):
    """同名就加 (2)、(3)…"""
    path = Path(path)
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
        n += 1


# ---------- 其他（要知識庫） ----------

def _source_file(vault, raw):
    """pdf、render 的輸入：工作區（.system/temp/）或 AI 產出裡、存在的檔。"""
    path = office.resolve_arg(raw)
    if not (office.in_temp(vault, path) or office.inside(path, Path(vault) / inventory.OUTPUT)):
        raise office.Refused(ONLY_HERE.format(path=path))
    if not path.is_file():
        raise office.Refused(f"找不到這個檔：{path}")
    return path


TOUCHED_LINE = "剛才的程式動到了 {names}，我沒有要動它；說「退回上一版」看救不救得回"
TOUCHED_SHOWN = 5


def _touched(vault, uris, turn_id):
    """D-188：動到的檔第一行講、記一條 script_touched 待處理（清冊有的用編號，沒有的用 uri）。回那一行或 None。"""
    from kb._lib import pending
    if not uris:
        return None
    names = [f"「{u.rsplit('/', 1)[-1]}」" for u in uris[:TOUCHED_SHOWN]]
    more = f"等 {len(uris)} 份" if len(uris) > TOUCHED_SHOWN else ""
    line = TOUCHED_LINE.format(names="、".join(names) + more)
    try:
        entries = inventory.load(vault)["entries"]
        ids = [(inventory.entry_for(vault, u, entries) or {}).get("id") or u for u in uris]
        pending.add(vault, "script_touched", ids, f"剛才的程式動到了 {'、'.join(names)}{more}", None, turn_id)
    except Exception as e:  # noqa: BLE001  待處理沒記到不擋這一行
        from kb._lib import ledger
        ledger.log_error(vault, f"script_touched 沒記到：{type(e).__name__}", source="office")
    return line


def _run(vault, args, as_json):
    rest = list(args.args or [])
    if rest[:1] == ["--"]:
        rest = rest[1:]
    turn_id = _turn_id(vault)
    code, tail, ws, touched = office.run_script(vault, args.script, rest, turn_id)
    first = _touched(vault, touched, turn_id)
    _out(as_json, ([first] if first else []) + [f"工作區：{ws}"] + tail + [f"結束碼：{code}"],
         {"ok": code == 0, "exit_code": code, "tail": tail, "workspace": str(ws), "touched": touched})
    return 0 if code == 0 else 3


def _unpack(vault, args, as_json):
    dest = office.unpack(vault, args.file, args.to)
    _out(as_json, [f"解開到：{dest}"], {"ok": True, "folder": str(dest)})
    return 0


def _pack(vault, args, as_json):
    out = office.pack(vault, args.folder, args.to, args.like)
    _out(as_json, [f"打包好了：{out}"], {"ok": True, "file": str(out)})
    return 0


def _pdf(vault, args, as_json):
    src = _source_file(vault, args.file)
    pdf, used = office.to_pdf(vault, src, office.workspace(vault, _turn_id(vault)))
    how = "" if used == "pdf" else f"（用 {used} 轉的）"
    _out(as_json, [f"PDF：{pdf}{how}"], {"ok": True, "pdf": str(pdf), "used": used})
    return 0


def _recalc(vault, args, as_json):
    src = office.resolve_arg(args.file)
    office.need_in_temp(vault, src)
    if src.suffix.lower() not in (".xlsx", ".xlsm"):
        raise office.Refused("只重算 xlsx")
    res = office.recalc(vault, src)
    errors = res["errors"]
    line = f"重算好了（{res['used']}）：公式 {res['formulas']} 個、錯誤 {len(errors)} 個"
    lines = [line] + [f"  {sheet}!{cell} {err}" for sheet, cell, err in errors[:20]]
    _out(as_json, lines, {"ok": True, "used": res["used"], "formulas": res["formulas"],
                          "errors": [list(e) for e in errors]})
    return 0


def _render(vault, args, as_json):
    from kb._lib import config
    src = _source_file(vault, args.file)
    pages = office.parse_pages(args.pages, config.get(vault, "pdf_read_pages_max"))
    ws = office.workspace(vault, _turn_id(vault))
    pdf, _used = office.to_pdf(vault, src, ws)
    images = office.render(vault, pdf, ws, pages)
    _out(as_json, [f"第 {n} 頁：{p}" for n, p in images] or ["沒有轉出任何一頁"],
         {"ok": True, "pdf": str(pdf), "images": [{"page": n, "path": p} for n, p in images]})
    return 0


HANDLERS = {"run": _run, "unpack": _unpack, "pack": _pack, "pdf": _pdf, "recalc": _recalc, "render": _render}


def run(args):
    as_json = getattr(args, "json", False)
    command = args.office_command
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    try:
        if command == "check":
            return _check(args, as_json)
        if command == "setup":
            return _setup(as_json)
        if command == "copy":
            return _copy(args, as_json)
        vault = find_vault(args)
        if vault is None:
            return _fail(as_json, NOT_IN_VAULT)
        return HANDLERS[command](vault, args, as_json)
    except (office.Unavailable, office.Refused) as e:
        return _fail(as_json, str(e))
    except zipfile.BadZipFile:
        return _fail(as_json, "這不是 Office 檔（解不開）")
    except OSError as e:
        return _fail(as_json, f"檔案被別的程式開著或沒有權限：{getattr(e, 'filename', None) or type(e).__name__}")
