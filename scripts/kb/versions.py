"""kb versions list／revert／delete-outputs：列最近的回合、退回最近一次沒退過的回合、刪已退回回合留著的成品（D-074、D-011）。

退回只從最近一次開始，不跳；退回前先把現況備進「退回」這句話自己的回合，退錯可再退。
成品（清冊 created_turn 是那回合且 manifest 記 create 的）預設留著、status 改 turn_reverted，revert 只列不問；
客戶明說刪才 delete-outputs（或一開始就 revert --delete-outputs）。退回動完後對碰到的 Z3 路徑對帳清冊 status。
退回標記是回合夾裡的 reverted.json，不改夾名；退回動作的回合在 turn.json 記 reverted_turn，退掉它時撤原回合的標記（第二輪 R2-4）。
刪成品前三道關：這一句話已經改過的不刪（R2-2）、指紋跟清冊不同的不刪（R2-3）、檔已不在只改 status（R2-L1）。
CLI 的鎖等 30 秒（hook 維持 3，第二輪 R2-L3）。
revert、delete-outputs 成功後記一段工作日誌程式段（D-085），note 是「已退回 …」「已刪 N 份成品（回合 …）」那句；刪 0 份不記。
資料夾帳（D-142）：回合夾的 folders.jsonl 記了 kb 改過的夾，讀回先驗路徑（絕對、..、不在知識庫裡就整個不退，I-111），
再照相反順序全部檢查、有一項不行整個不退；可以就先把夾改回去、拿掉的建回來（整段成功或整段不動：一步失敗就把已改的倒回去、
檔案不還原、不標已退回），每一步反著記進動作回合，再還原檔案；建的夾在檔案還原之後才判空，空就拿掉、不空留著並說一句
（測試後修正裁定 12、13、17）。記在搬過的夾裡的檔，路徑換回舊位置再還原。revert 整段持一把鎖，兩個退回不會同時跑。
搬動帳（manage 第二版，D-175）：kb source-edit 搬過、改過名的客戶檔記在回合夾的 moves.jsonl（清冊 uri）。讀回時別名要是目前登記且
沒設成不讀的來源或知識庫、AI 產出、收件匣，相對路徑不准有 ..、絕對路徑、磁碟代號，解出來要在那個根底下、不在任何排除裡；
再照相反順序全部檢查（搬過去的還在、原位沒有同名、原本的夾還在），有一項不過整個不退。過了先把 moves.jsonl 搬回去
（一步失敗就倒回已搬的），再做 folders.jsonl 與檔案還原，最後對清冊掃一次那幾個根。trash 那行不搬回，只多印一句在資源回收筒。
manifest 的 purged 行（不讀的夾清掉了，kb folders purge）跳過，多印一句退不回。
"""
import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import credentials, inventory, ledger, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.io import read_json, write_json_atomic

ENTRY_LINES_MAX = 15
CLI_LOCK_TIMEOUT = 30
REVERT_LOCK_TRY = 0.2  # 只試一下（殘留鎖照 FileLock 清掉）；搶不到就不排隊（測試第二輪後裁定 24）
NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"
TOUCHED_THIS_TURN = "{rel} 這一句話已經改過，下一句再刪"
CHANGED_SINCE = "{rel} 之後被改過，留著"
ALREADY_GONE = "{rel} 已經不在"
MADE_KEPT = "建的資料夾「{rel}」裡有東西，留著"
FOLDER_OPEN = "退不回去：「{rel}」裡有檔案正開著（檔案總管或 Office），什麼都沒動；關掉再說一次退回"
FOLDER_FAILED = "退不回去：{why}，什麼都沒動"
BAD_LEDGER = "回合紀錄的資料夾路徑不對，沒有退回"
BAD_MANIFEST = "回合紀錄的檔案路徑不對，沒有退回"
NOT_AN_OUTPUT = "這份不在 AI 產出裡，沒有刪"
REVERT_BUSY = "另一個退回正在跑，等它做完再說一次"
PURGED_LINE = "那一版因為不讀的夾清掉了，退不回"
IN_BIN = "{name} 在資源回收筒，要從那裡拿回來"
MOVE_BAD = "退不回去：回合紀錄的搬動路徑不對（{uri}），什麼都沒動"
MOVE_PURGED = "退不回去：那一回合搬過的檔在清掉的不讀夾裡，什麼都沒動"
MOVE_UNREGISTERED = "退不回去：「{alias}」現在沒有登記，什麼都沒動"
MOVE_EXCLUDED = "退不回去：「{name}」在設成不讀的夾裡，什麼都沒動"
MOVE_GONE = "退不回去：「{name}」不見了，搬不回「{folder}」，什麼都沒動"
MOVE_TAKEN = "退不回去：「{folder}」裡已經有「{name}」了，什麼都沒動"
MOVE_NO_PARENT = "退不回去：原本的夾「{folder}」不見了，什麼都沒動"
MOVE_OPEN = "退不回去：「{name}」裡有檔案正開著，什麼都沒動；關掉再說一次退回"
MOVE_FAILED = "退不回去：{why}，什麼都沒動"
PURGED_MARK = "（已排除）"
FILE_OPEN = "退不回去：「{rel}」正開著（檔案總管、Office 或記事本），什麼都沒動；關掉再說一次退回"
UNEXPECTED = "退不回去：{why}；可能只退了一部分，先別再說退回，請服務人員看一下"
_DRIVE = re.compile(r"^[A-Za-z]:")
# 帳裡的路徑不能是這些保留區的根本身（測試第二輪後裁定 26）：資料夾帳只動型別夾、實體夾與 AI 產出底下的子夾
RESERVED_ROOTS = {s.casefold() for s in (".system", "系統", "收件匣", "檔案索引", "AI 產出")}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="versions_command", metavar="<子子命令>", required=True)
    ls = subs.add_parser("list", help="由新到舊列最近的回合", description="由新到舊列最近的回合。")
    ls.add_argument("--n", type=int, default=5, help="列幾個回合，預設 5")
    ls.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    rv = subs.add_parser("revert", help="退回最近一次沒退過的回合", description="退回最近一次沒退過的回合。")
    rv.add_argument("code", nargs="?", help="回合代號；只能是最近一次")
    rv.add_argument("--delete-outputs", action="store_true", help="連這回合的成品一起刪（客戶明說才加）")
    rv.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    do = subs.add_parser("delete-outputs", help="刪已退回回合裡留著的成品（客戶明說才做）",
                         description="刪「已退回的回合」裡 status 還是 turn_reverted 的成品；先備份再刪，再退一次救得回。")
    do.add_argument("code", nargs="?", help="回合代號；沒給取最近一個有可刪成品的已退回回合")
    do.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def turns(vault):
    """由新到舊：{turn, dir, started, prompt_head, reverted, reverted_turn, entries}。"""
    root = Path(vault) / turn.VERSIONS_REL
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        info = read_json(d / "turn.json", {}) or {}
        out.append({
            "turn": d.name, "dir": d,
            "started": info.get("started", ""), "prompt_head": info.get("prompt_head", ""),
            "reverted": (d / "reverted.json").is_file(),
            "reverted_turn": info.get("reverted_turn"),
            "entries": turn.entries(vault, d.name),
            "folders": turn.folders(vault, d.name),
            "moves": turn.moves(vault, d.name),
        })
    return out


def _fkey(rel):
    return str(rel).casefold()


def _folder_steps(vault, ops):
    """D-142：照相反順序全部先檢查，回 (步驟, 拒絕句)；有一項不行步驟是 None、什麼都不動。
    move 要 to 在、from 不在（只差大小寫的不算占用）；make 要那個夾在或已經不在；remove 要那個路徑不在或是夾。
    前面的步驟做完後的樣子記在 state 裡，後面的檢查照它看（夾改名接著改名的鏈也算得對）。"""
    state = {}

    def exists(rel):
        return state[_fkey(rel)] if _fkey(rel) in state else (Path(vault) / rel).exists()

    def is_file(rel):
        return _fkey(rel) not in state and (Path(vault) / rel).is_file()
    for op in reversed(ops):
        src, dst = op["from"], op.get("to")
        if op["op"] == "move":
            if not dst or not exists(dst):
                return None, f"退不回去：「{dst}」不見了，改不回「{src}」，什麼都沒動"
            if _fkey(src) != _fkey(dst) and exists(src):
                return None, f"退不回去：「{dst}」改回「{src}」時「{src}」已經有東西了，什麼都沒動"
            state[_fkey(dst)], state[_fkey(src)] = False, True
        elif is_file(src):
            return None, f"退不回去：「{src}」現在是檔案，什麼都沒動"
        elif op["op"] == "remove":
            state[_fkey(src)] = True
    return list(reversed(ops)), None


def _path_ok(root, rel):
    """帳裡的一個路徑（F-6、N-6）：要是知識庫裡的相對路徑，不是絕對路徑、不含 ..、解析後在知識庫裡，
    也不是知識庫本身或保留區的根本身。"""
    s = rel if isinstance(rel, str) else ""
    if not s or s.startswith(("/", "\\")) or _DRIVE.match(s) or Path(s).is_absolute() or ".." in re.split(r"[\\/]", s):
        return False
    try:
        parts = (root / s).resolve().relative_to(root).parts
    except (ValueError, OSError):
        return False
    return bool(parts) and not (len(parts) == 1 and parts[0].casefold() in RESERVED_ROOTS)


def _output_path_ok(root, rel):
    """delete-outputs 要刪的路徑（測試第三輪後裁定 32）：過 _path_ok，而且解析後在 AI 產出/ 底下（成品只會在那裡）。"""
    if not _path_ok(root, rel):
        return False
    parts = (root / rel).resolve().relative_to(root).parts
    return len(parts) > 1 and parts[0].casefold() == "ai 產出"


def _bad_path(vault, ops):
    """資料夾帳每一列的 from／to 照 _path_ok 驗；不行的那一列回出來，都對回 None。"""
    root = Path(vault).resolve()
    for op in ops:
        for rel in [op.get("from")] + ([op["to"]] if op.get("to") is not None else []):
            if not _path_ok(root, rel):
                return op
    return None


def _bad_entry(vault, entries):
    """manifest 每一列的 path 跟資料夾帳同一套檢查（測試第二輪後裁定 26）。"""
    root = Path(vault).resolve()
    return next((e for e in entries if not _path_ok(root, e.get("path"))), None)


def _forward(ops):
    """夾改回去之前，還原目標（舊位置）現在在哪：照記的順序一層層換成新位置（_translator 的反方向）。"""
    moves = [(op["from"], op["to"]) for op in ops if op["op"] == "move" and op.get("to")]

    def ahead(rel):
        for src, dst in moves:
            k, s = _fkey(rel), _fkey(src)
            if k == s or k.startswith(s + "/"):
                rel = dst + rel[len(src):]
        return rel
    return ahead


def _writable(path):
    """能不能寫：開檔寫入試一次再關（Windows 上被 Office 開著、唯讀都會丟 PermissionError）。"""
    try:
        with open(path, "r+b"):
            return True
    except PermissionError:
        return False


def _locked_file(vault, target, delete_outputs, sources):
    """測試第二輪後裁定 25（N-7）：改夾之前先試每個要還原或移除的檔能不能寫；回第一個不行的（現在的位置），都行回 None。"""
    back, ahead = _translator(target["folders"]), _forward(target["folders"])
    for e in target["entries"]:
        rel = back(e.get("path"))
        now = ahead(rel)
        p = Path(vault) / now
        if not p.is_file():
            continue
        if e.get("action") == "create":
            if turn.zone_of(vault, rel, sources) == "Z3" and not delete_outputs:
                continue  # 成品留著，不動它
        elif not (target["dir"] / "files" / e.get("path")).is_file():
            continue  # 備份不在，不會寫它
        if not _writable(p):
            return now
    return None


def _plain(vault, e):
    """其他例外的白話：OSError 用 strerror，檔名換成知識庫裡的相對路徑（不在知識庫裡就不印）；不印絕對路徑。"""
    if not isinstance(e, OSError):
        return type(e).__name__
    why = e.strerror or type(e).__name__
    if e.filename:
        try:
            rel = Path(e.filename).resolve().relative_to(Path(vault).resolve()).as_posix()
            why += f"（「{rel}」）"
        except (ValueError, OSError):
            pass
    return why


class _FolderFailed(Exception):
    def __init__(self, line):
        super().__init__(line)
        self.line = line


def _undo_moves(vault, steps):
    """還原檔案之前：照檢查過的步驟把夾改回去、拿掉的建回來。整段成功或整段不動（F-1）：一步失敗就把已做的倒回去，
    丟 _FolderFailed 帶給客戶的那句。回做了的（{op, from, to}，是要反著記進動作回合的樣子）。"""
    done = []
    v = Path(vault)
    for op in steps:
        src, dst = op["from"], op.get("to")
        try:
            if op["op"] == "move":
                (v / src).parent.mkdir(parents=True, exist_ok=True)
                os.rename(v / dst, v / src)
                done.append({"op": "move", "from": dst, "to": src})
            elif op["op"] == "remove" and not (v / src).is_dir():
                (v / src).mkdir(parents=True)
                done.append({"op": "make", "from": src, "to": None})
        except Exception as e:  # noqa: BLE001  白話一句、不丟 traceback
            _roll_back(vault, done)
            if isinstance(e, PermissionError):
                raise _FolderFailed(FOLDER_OPEN.format(rel=dst if op["op"] == "move" else src)) from e
            raise _FolderFailed(FOLDER_FAILED.format(why=e)) from e
    return done


def _roll_back(vault, done):
    v = Path(vault)
    for d in reversed(done):
        try:
            if d["op"] == "move":
                os.rename(v / d["to"], v / d["from"])
            else:
                (v / d["from"]).rmdir()
        except OSError as e:
            ledger.log_error(vault, f"退回倒不回去 {d}: {type(e).__name__}: {e}", source="versions")


def _undo_makes(vault, steps):
    """還原檔案之後（F-2）：這回合建的夾空了就拿掉、不空留著並說一句。同一個夾後面（更早的動作）是 remove 的不拿掉：
    它原本就在。回 (做了的, 留著的句子)。"""
    done, kept = [], []
    v = Path(vault)
    for i, op in enumerate(steps):
        src = op["from"]
        if op["op"] != "make" or not (v / src).is_dir():
            continue
        if any(o["op"] == "remove" and _fkey(o["from"]) == _fkey(src) for o in steps[i + 1:]):
            continue
        try:
            if any((v / src).iterdir()):
                kept.append(MADE_KEPT.format(rel=src))
                continue
            (v / src).rmdir()
        except OSError:
            kept.append(MADE_KEPT.format(rel=src))
            continue
        done.append({"op": "remove", "from": src, "to": None})
    return done, kept


def _translator(ops):
    """夾改回去之後，回合裡記在新位置（move 的 to 底下）的檔案路徑換回舊位置（from 底下），照記的相反順序一層層換回。"""
    moves = [(op["from"], op["to"]) for op in ops if op["op"] == "move" and op.get("to")]

    def back(rel):
        for src, dst in reversed(moves):
            k, d = _fkey(rel), _fkey(dst)
            if k == d or k.startswith(d + "/"):
                rel = src + rel[len(dst):]
        return rel
    return back


def _counts(vault, t):
    """清冊讀不到（ValueError）時 outputs 是 None、不印成品數（清冊審閱裁定 37）。"""
    n = {k: sum(1 for e in t["entries"] if e.get("action") == k) for k in ("modify", "create", "delete")}
    try:
        entries = inventory.load(vault)["entries"]
    except ValueError:
        n["outputs"] = None
        return n
    n["outputs"] = sum(1 for e in entries
                       if e.get("created_turn") == t["turn"] and e.get("origin") == "ai_output")  # 掃描入冊的客戶檔不是成品
    return n


def _fmt_time(started):
    try:
        return datetime.strptime(started, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return started


def _list(vault, n, as_json):
    all_turns = turns(vault)
    current = (turn._read_turn(turn._current_file(vault)) or {}).get("turn")
    shown = all_turns[:n]
    if as_json:
        print(json.dumps([{
            "turn": t["turn"], "started": t["started"], "prompt_head": t["prompt_head"],
            "reverted": t["reverted"], "reverted_turn": t["reverted_turn"], "current": t["turn"] == current,
            "counts": _counts(vault, t), "entries": t["entries"],
            "folders": [{k: f.get(k) for k in ("op", "from", "to")} for f in t["folders"]],
            "moves": [{k: m.get(k) for k in ("op", "kind", "from", "to")} for m in t["moves"]],
        } for t in shown], ensure_ascii=False))
        return 0
    if not all_turns:
        print("還沒有任何改動紀錄")
        return 0
    for t in shown:
        c = _counts(vault, t)
        flags = ("（已退回）" if t["reverted"] else "") + ("（這一次）" if t["turn"] == current else "")
        outputs = "" if c["outputs"] is None else f"、成品 {c['outputs']}"
        print(f"{t['turn']}{flags}｜{_fmt_time(t['started'])}｜問：「{t['prompt_head']}」｜"
              f"修改 {c['modify']}、新增 {c['create']}、刪除 {c['delete']}{outputs}")
        for line in _folder_lines(t["folders"]):
            print(line)
        for line in _move_lines(t["moves"]):
            print(line)
        for e in t["entries"][:ENTRY_LINES_MAX]:
            print(f"    {e.get('action')}：{e.get('path')}")
        if len(t["entries"]) > ENTRY_LINES_MAX:
            print(f"    ……共 {len(t['entries'])} 檔")
    try:
        inventory.load(vault)
    except ValueError as e:
        print(f"清冊讀不到：{e}")
    return 0


def _folder_lines(ops):
    """資料夾動作的白話（確認後 G-5）：AI 退回前講得出「夾會改回工班」。"""
    words = {"move": "    資料夾：{from} → {to}", "make": "    建了資料夾：{from}", "remove": "    拿掉了空夾：{from}"}
    return [words[f["op"]].format(**{"from": f["from"], "to": f.get("to")}) for f in ops if f.get("op") in words]


# ---------- 搬動帳（manage 第二版，D-175） ----------

def _uri_name(uri):
    return str(uri).rstrip("/").rpartition("/")[2]


def _move_lines(rows):
    """versions list 與 revert 的搬動白話：一行一項（「信義王合約.docx：從 案子 搬到 2026」）。"""
    from kb._lib.source_edit import folder_word
    out = []
    for m in rows:
        name = _uri_name(m.get("from", ""))
        if m.get("op") == "trash":
            out.append(f"    {name}：丟進資源回收筒")
        elif m.get("op") == "rename":
            out.append(f"    {name}：改名叫 {_uri_name(m.get('to') or '')}")
        else:
            out.append(f"    {name}：從 {folder_word(m.get('from', ''))} 搬到 {folder_word(m.get('to') or '')}")
    return out


def _move_root(vault, sources, alias):
    """搬動帳的別名 → 根（磁碟上的大小寫）：知識庫、AI 產出、收件匣，或目前登記的來源；都不是回 None。"""
    a = str(alias).casefold()
    if a == inventory.KNOWLEDGE.casefold():
        return Path(os.path.realpath(str(vault)))
    for area in (inventory.INBOX, inventory.OUTPUT):
        if a == area.casefold():
            return Path(os.path.realpath(str(vault))) / area
    for src in sources.entries:
        if src["alias"].casefold() == a:
            return Path(os.path.realpath(src["root"]))
    return None


def _move_path(vault, sources, uri):
    """搬動帳的一個 uri → (絕對路徑, None) 或 (None, 拒絕句)。不信紀錄（B-080）：別名要登記、相對路徑不准 ..／絕對／磁碟代號、
    解出來在根底下、不在任何排除裡；知識庫裡的只收實體夾底下（型別/名稱/…）。"""
    from kb._lib.paths import inside, normalize
    text = uri if isinstance(uri, str) else ""
    if PURGED_MARK in text:
        return None, MOVE_PURGED
    if not text.startswith("file://"):
        return None, MOVE_BAD.format(uri=text)
    alias, _sep, rel = text[len("file://"):].partition("/")
    segs = re.split(r"[\\/]", rel)
    if (not rel or rel.startswith(("/", "\\")) or _DRIVE.match(rel) or Path(rel).is_absolute()
            or any(seg in ("", ".", "..") for seg in segs)):
        return None, MOVE_BAD.format(uri=text)
    root = _move_root(vault, sources, alias)
    if root is None:
        return None, MOVE_UNREGISTERED.format(alias=alias)
    if alias.casefold() == inventory.KNOWLEDGE.casefold() and (
            len(segs) < 3 or segs[0].startswith(".") or segs[0].casefold() in inventory.VAULT_RESERVED_DIRS):
        return None, MOVE_BAD.format(uri=text)
    full = root.joinpath(*segs)
    rn, fn = normalize(str(root), None), normalize(str(full), None)
    if fn == rn or not inside(fn, rn):
        return None, MOVE_BAD.format(uri=text)
    status, _src = sources.lookup(fn)
    if status == "excluded":
        return None, MOVE_EXCLUDED.format(name=segs[-1])
    return full, None


def _move_steps(vault, sources, rows):
    """D-175：搬動帳先全部驗、再照相反順序檢查（搬過去的還在、原位沒有同名、原本的夾還在）。回 (步驟, 拒絕句)。"""
    resolved = []
    for m in rows:
        src, why = _move_path(vault, sources, m.get("from"))
        if why:
            return None, why
        dst = None
        if m.get("op") != "trash":
            dst, why = _move_path(vault, sources, m.get("to"))
            if why:
                return None, why
        resolved.append((m, src, dst))
    state = {}

    def exists(p):
        k = os.path.normcase(str(p))
        return state[k] if k in state else os.path.lexists(p)
    steps = []
    for m, src, dst in reversed(resolved):
        if m["op"] == "trash":
            continue
        name, folder = _uri_name(m["to"]), _uri_name(str(m["from"]).rpartition("/")[0])
        if not exists(dst):
            return None, MOVE_GONE.format(name=name, folder=folder)
        if os.path.normcase(str(src)) != os.path.normcase(str(dst)) and exists(src):
            return None, MOVE_TAKEN.format(folder=folder, name=_uri_name(m["from"]))
        if not exists(src.parent):
            return None, MOVE_NO_PARENT.format(folder=folder)
        state[os.path.normcase(str(dst))], state[os.path.normcase(str(src))] = False, True
        steps.append({"op": m["op"], "kind": m["kind"], "src": src, "dst": dst, "from_uri": m["from"], "to_uri": m["to"]})
    return steps, None


def _undo_source_moves(vault, steps):
    """照檢查過的步驟把客戶的檔或夾搬回去；一步失敗就把已搬的倒回去、丟 _FolderFailed。回搬了的。"""
    done = []
    for st in steps:
        try:
            os.rename(st["dst"], st["src"])
        except Exception as e:  # noqa: BLE001  白話一句、不丟 traceback
            _roll_back_source_moves(vault, done)
            if isinstance(e, PermissionError):
                raise _FolderFailed(MOVE_OPEN.format(name=_uri_name(st["to_uri"]))) from e
            raise _FolderFailed(MOVE_FAILED.format(why=_plain(vault, e))) from e
        done.append(st)
    return done


def _roll_back_source_moves(vault, done):
    for st in reversed(done):
        try:
            os.rename(st["src"], st["dst"])
        except OSError as e:
            ledger.log_error(vault, f"退回倒不回去 {st['from_uri']}: {type(e).__name__}: {e}", source="versions")


def _rescan(vault, rows, turn_id):
    """搬回去之後對清冊掃一次那幾個根（大小、時間、不見照實）；有變才套用。"""
    aliases = sorted({inventory.split_uri(u)[0] for m in rows for u in (m.get("from"), m.get("to")) if u})
    if not aliases:
        return
    report = inventory.scan(vault, sources_lib.load(vault), only_roots=aliases)
    if inventory.has_changes(vault, report):
        inventory.apply(vault, report, turn_id)


def _prune_dirs(vault, rel):
    """刪檔後父夾空就移除，只移到型別夾那層（型別夾本身不刪）。"""
    parts = rel.split("/")
    for depth in range(len(parts) - 1, 1, -1):
        d = Path(vault).joinpath(*parts[:depth])
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
            else:
                break
        except OSError:
            break


def _remove(vault, rel, acting_turn):
    """刪檔前先備進動作回合（action delete），再退一次救得回（D-074、D-011）。"""
    turn.backup(vault, rel, acting_turn, action="delete")
    os.remove(Path(vault) / rel)
    _prune_dirs(vault, rel)


def _rels_of(entries_):
    return [loc["uri"][len("file://"):] for o in entries_ for loc in o.get("locations", [])
            if str(loc.get("uri", "")).startswith("file://")]


def _rel_of(entry):
    rels = _rels_of([entry])
    return rels[0] if rels else None


def _acting_turn(vault, target_turn):
    """退回／刪成品動作本身的回合（工作日誌第二輪 R2-M2、M3，第三輪 R3-M1）：
    - 目前回合 sid 空或已答完（done true，終端機在跑）：不論目標，一律 turn.start(None) 開新動作回合，只換 current.json；
      做完再由 _seal 封住，之後的指令進新回合，下一次退回不會連先前的退回一起撤掉。
    - 對話裡、還沒答完（sid 有、done false）：目前回合就是這句話，動作記在它裡面；只有它正好是要退的回合時，
      turn.start(sid, inherit=目前回合) 開新回合接著這句，繼承問、note、approved，記 journal_turn，
      換掉 turns/<sid>.json 與 current.json；Stop 收原回合那一行。連續退回每次再開一個，journal_turn 一路繼承。"""
    rt = turn.current(vault)
    if not rt.get("session_id") or rt.get("done"):
        return turn.start(vault, None, f"退回 {target_turn}")
    if rt["turn"] == target_turn:
        rt = turn.start(vault, rt["session_id"], f"退回 {target_turn}", inherit=rt)
    return rt


def _seal(vault, conversation):
    """終端機跑的（不在對話裡）做完後再 start(None) 一次，把動作回合封起來（第二輪裁定 (ii)）：
    之後的指令進新回合，下一次退回只退那些，不會連這次退回一起撤掉。對話裡的不封，同一句的寫入要記在同一個回合。"""
    if not conversation:
        turn.start(vault, None, "")


def _touched_paths(vault, turn_id):
    return {e.get("path") for e in turn.entries(vault, turn_id)}


def _unchanged_since_registered(vault, rel, entry):
    fps = entry.get("fingerprints") or []
    if not fps:
        return True
    return inventory.sha256_of(Path(vault) / rel) == fps[-1].get("sha256")


def _reconcile(vault, rels):
    """第一輪 M3：退回動完後對每個碰到的 Z3 路徑對帳清冊：檔在就該 location 最新一筆改 active，不在就改 deleted。"""
    if not rels:
        return
    data = inventory.load(vault)
    to_active, to_deleted = [], []
    for rel in rels:
        key = inventory.uri_key("file://" + rel)
        same = [e for e in data["entries"] if any(inventory.uri_key(loc.get("uri", "")) == key for loc in e.get("locations", []))]
        if not same:
            continue
        ((to_active if (Path(vault) / rel).is_file() else to_deleted)).append(same[-1]["id"])
    if to_active:
        inventory.set_status(vault, to_active, "active")
    if to_deleted:
        inventory.set_status(vault, to_deleted, "deleted")


def _mark_acting_turn(vault, acting_turn, target_turn):
    """動作回合的 turn.json 記 reverted_turn（第二輪 R2-4）。"""
    vdir = turn.turn_dir(vault, acting_turn)
    meta = read_json(vdir / "turn.json", {}) or {}
    meta["reverted_turn"] = target_turn
    write_json_atomic(vdir / "turn.json", meta)


def _undo_revert_marks(vault, original_turn):
    """退掉「退回」本身：原回合的 reverted.json 撤掉，它留著的成品（檔還在）改回 active（第二輪 R2-4）。"""
    marker = Path(vault) / turn.VERSIONS_REL / original_turn / "reverted.json"
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    back = [e["id"] for e in inventory.load(vault)["entries"]
            if e.get("created_turn") == original_turn and e.get("status") == "turn_reverted"
            and (rel := _rel_of(e)) and (Path(vault) / rel).is_file()]
    if back:
        inventory.set_status(vault, back, "active")


def revert_lock_path(vault):
    """revert 整段持的那把鎖（鎖檔是 .system/revert.lock）。"""
    return Path(vault) / ".system" / "revert"


def _revert(vault, code, delete_outputs, as_json, conversation=False):
    lock = kio.FileLock(revert_lock_path(vault), timeout=REVERT_LOCK_TRY)
    try:
        lock.__enter__()
    except TimeoutError:
        print(REVERT_BUSY)
        return 3
    try:
        return _revert_locked(vault, code, delete_outputs, as_json, conversation)
    except Exception as e:  # noqa: BLE001  白話一句、不丟 traceback、不印絕對路徑（測試第二輪後裁定 25）
        ledger.log_error(vault, f"revert 失敗：{type(e).__name__}: {e}", source="versions")
        print(UNEXPECTED.format(why=_plain(vault, e)))
        return 3
    finally:
        lock.__exit__(None, None, None)


def _revert_locked(vault, code, delete_outputs, as_json, conversation):
    all_turns = turns(vault)
    cands = [t for t in all_turns
             if not t["reverted"] and (t["entries"] or t["reverted_turn"] or t["folders"] or t["moves"])]
    if not cands:
        print("沒有可以退回的改動")
        return 3
    target = cands[0]
    if code and code != target["turn"]:
        print(f"只能從最近一次開始退：{target['turn']}")
        return 3
    bad = _bad_path(vault, target["folders"])
    if bad is not None:  # I-111：kb 信任的根只能是 cwd 推出的知識庫
        ledger.log_error(vault, f"{target['turn']} folders 路徑不對：{json.dumps(bad, ensure_ascii=False)}",
                         source="versions")
        print(BAD_LEDGER)
        return 3
    bad = _bad_entry(vault, target["entries"])
    if bad is not None:
        ledger.log_error(vault, f"{target['turn']} manifest 路徑不對：{json.dumps(bad, ensure_ascii=False)}",
                         source="versions")
        print(BAD_MANIFEST)
        return 3
    sources_now = sources_lib.load(vault)
    move_steps, refused = _move_steps(vault, sources_now, target["moves"])
    if refused:  # D-175：搬動帳有一項不過整個不退，回合、檔、夾都不動
        ledger.log_error(vault, f"{target['turn']} moves 退不回：{refused}", source="versions")
        print(refused)
        return 3
    steps, refused = _folder_steps(vault, target["folders"])
    if refused:  # D-142：夾改不回去就整個不退，回合、檔、夾都不動
        print(refused)
        return 3
    locked = _locked_file(vault, target, delete_outputs, sources_now)
    if locked:  # N-7：不退成一半延伸到檔案階段
        print(FILE_OPEN.format(rel=locked))
        return 3
    try:
        moved_back = _undo_source_moves(vault, move_steps)
    except _FolderFailed as e:
        print(e.line)
        return 3
    try:
        folders_done = _undo_moves(vault, steps)
    except _FolderFailed as e:
        _roll_back_source_moves(vault, moved_back)
        print(e.line)
        return 3
    rt = _acting_turn(vault, target["turn"])
    for m in moved_back:  # 反著記進動作回合：退掉這次退回時再搬過去
        turn.record_move(vault, rt["turn"], m["op"], m["kind"], m["to_uri"], m["from_uri"])
    for d in folders_done:  # 反著記進動作回合：退掉這次退回時夾再改過去
        turn.record_folder(vault, rt["turn"], d["op"], d["from"], d["to"])
    back = _translator(target["folders"])
    touched_before = _touched_paths(vault, rt["turn"])  # 這一句話在退回前就動過的路徑（R2-2）
    sources = sources_lib.load(vault)
    # 成品只算「清冊 created_turn 是這回合 且 這回合 manifest 記 create」的（第一輪 M4）
    created = {e.get("path") for e in target["entries"] if e.get("action") == "create"}
    try:  # 清冊讀不到：照 manifest 還原檔案（含 inventory.json），跳過成品對帳（清冊審閱裁定 37）
        outputs = [o for o in inventory.outputs_of_turn(vault, target["turn"])
                   if o.get("origin") == "ai_output" and _rel_of(o) in created]
        inv_error = None
    except ValueError as e:
        outputs, inv_error = [], str(e)
    out_rels = _rels_of(outputs)

    restored, removed, lost, touched_z3, kept_msgs, kept_ids = [], [], [], [], [], []
    purged = False
    for e in reversed(target["entries"]):
        recorded, action = e.get("path"), e.get("action")
        if action == "purged":  # 不讀的夾清掉了：那一版沒有了（D-174）
            purged = True
            continue
        rel = back(recorded)  # 記在搬過的夾裡的檔，夾改回去之後在舊位置
        dst = Path(vault) / rel
        is_z3 = turn.zone_of(vault, rel, sources) == "Z3"
        if action in ("modify", "delete"):
            src = target["dir"] / "files" / recorded
            if not src.is_file():
                lost.append(rel)
                continue
            turn.backup(vault, rel, rt["turn"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)
            if is_z3:
                touched_z3.append(rel)
        elif action == "create" and dst.is_file():
            if is_z3 and not delete_outputs:
                continue  # 成品照 D-011：沒明說就留著，之後用 delete-outputs 刪
            if is_z3 and rel in touched_before:
                kept_msgs.append(TOUCHED_THIS_TURN.format(rel=rel))
                kept_ids.extend(o["id"] for o in outputs if _rel_of(o) == rel)
                continue
            _remove(vault, rel, rt["turn"])
            removed.append(rel)
            if is_z3:
                touched_z3.append(rel)

    made_done, folders_kept = _undo_makes(vault, steps)  # F-2：檔案還原之後才判空
    for d in made_done:
        turn.record_folder(vault, rt["turn"], d["op"], d["from"], d["to"])
    folders_done += made_done
    if outputs:
        if delete_outputs:
            gone = [o["id"] for o in outputs if o["id"] not in kept_ids]
            if gone:
                inventory.set_status(vault, gone, "deleted")
            if kept_ids:
                inventory.set_status(vault, kept_ids, "turn_reverted")
        else:
            inventory.set_status(vault, [o["id"] for o in outputs], "turn_reverted")
    if inv_error is None:
        _reconcile(vault, touched_z3)
        _rescan(vault, target["moves"], rt["turn"])
    if target["reverted_turn"]:
        try:
            _undo_revert_marks(vault, target["reverted_turn"])
        except ValueError:
            pass  # 標記已撤；清冊讀不到，成品 status 不對帳
    _mark_acting_turn(vault, rt["turn"], target["turn"])
    write_json_atomic(target["dir"] / "reverted.json",
                      {"time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "by_turn": rt["turn"]})
    _seal(vault, conversation)

    deleted_n = len(outputs) - len(set(kept_ids))
    if outputs and delete_outputs:
        tail = f"成品 {deleted_n} 份刪了" + (f"、{len(set(kept_ids))} 份留著" if kept_ids else "")
    elif outputs:
        tail = f"成品 {len(outputs)} 份留著"
    else:
        tail = "成品 0 份"
    # R2-L3：升級前沒遮好的問會被搬進來，只把「問」那段先壓空白再遮再嵌進句子，數字留著（D-086「程式段不遮」的例外）
    head = credentials.mask_secrets(" ".join(str(target["prompt_head"] or "").split()))
    asked = f"（問：「{head}」）" if head else ""  # L6：守門自己開的回合問是空的
    sentence = f"已退回 {target['turn']}{asked}：還原 {len(restored)}、移除 {len(removed)}、{tail}"
    journal_cli.record_program_or_warn(
        vault, sentence,
        [{"path": rel, "action": "modify"} for rel in restored] + [{"path": rel, "action": "delete"} for rel in removed],
        "versions revert", conversation)
    if as_json:
        print(json.dumps({
            "turn": target["turn"], "prompt_head": target["prompt_head"], "by_turn": rt["turn"],
            "restored": restored, "removed": removed, "lost": lost,
            "outputs": out_rels, "outputs_deleted": bool(delete_outputs), "kept": kept_msgs,
            "undone_revert_of": target["reverted_turn"], "inventory_error": inv_error,
            "folders": folders_done, "folders_kept": folders_kept,
            "moves": [{k: m.get(k) for k in ("op", "kind", "from", "to")} for m in target["moves"]],
            "in_bin": [_uri_name(m["from"]) for m in target["moves"] if m["op"] == "trash"], "purged": purged,
        }, ensure_ascii=False))
        return 0
    if lost:
        print(f"無法還原 {len(lost)} 檔（備份已不在）：" + "、".join(lost))
    print(sentence)
    for rel in out_rels:
        print(f"    {rel}")
    for line in _folder_lines(folders_done):  # 夾改回去了哪些（確認後 G-5）
        print(line)
    for msg in kept_msgs:
        print(msg)
    if outputs and not delete_outputs:
        print(f"這 {len(outputs)} 份成品先留著；要刪跟我說，我用 delete-outputs 刪。")
    if target["reverted_turn"]:
        print(f"這一次退的是「退回 {target['reverted_turn']}」本身，{target['reverted_turn']} 不再算已退回。")
    for msg in folders_kept:  # 建的夾裡有東西（D-142，取代「套用設定檔時建的資料夾還在」）
        print(msg)
    for line in _move_lines([{"op": m["op"], "from": m["to_uri"], "to": m["from_uri"]} for m in moved_back]):
        print(line)
    for m in target["moves"]:
        if m["op"] == "trash":
            print(IN_BIN.format(name=_uri_name(m["from"])))
    if purged:
        print(PURGED_LINE)
    if inv_error is not None:
        print(f"清冊讀不到：{inv_error}")
    return 0


def _delete_outputs(vault, code, as_json, conversation=False):
    """第一輪 H1：「退回 → 客戶說刪 → 刪」的第二步。只刪已退回回合裡 status 還是 turn_reverted 的成品；
    三道關：這一句話已經改過的不刪（R2-2，連 superseded 的也先告知）、指紋不同的不刪（R2-3）、檔已不在只改 status（R2-L1）。
    """
    all_entries = inventory.load(vault)["entries"]
    acting = turn.current(vault)
    touched = _touched_paths(vault, acting["turn"])
    cands = []
    for t in turns(vault):
        if not t["reverted"]:
            continue
        outs = [e for e in all_entries if e.get("created_turn") == t["turn"]
                and (e.get("status") == "turn_reverted"
                     or (e.get("status") == "superseded" and _rel_of(e) in touched))]
        if outs:
            cands.append((t, outs))
    if code:
        cands = [(t, o) for t, o in cands if t["turn"] == code]
        if not cands:
            print(f"{code} 沒有可刪的成品")
            return 3
    elif not cands:
        print("沒有可刪的成品")
        return 3
    target, outs = cands[0]
    rt = _acting_turn(vault, target["turn"])
    deleted, missing, kept_msgs, gone_ids = [], [], [], []
    root = Path(vault).resolve()
    for o in outs:
        rel = _rel_of(o)
        if rel is None:
            continue
        if not _output_path_ok(root, rel):  # S-3：清冊的路徑不信，跟 revert 同一套檢查，而且只刪 AI 產出底下的
            ledger.log_error(vault, f"delete-outputs 路徑不對：{o.get('id')}", source="versions")
            kept_msgs.append(NOT_AN_OUTPUT)
            continue
        if rel in touched:
            kept_msgs.append(TOUCHED_THIS_TURN.format(rel=rel))
            continue
        if not (Path(vault) / rel).is_file():
            missing.append(rel)
            gone_ids.append(o["id"])
            continue
        if not _unchanged_since_registered(vault, rel, o):
            kept_msgs.append(CHANGED_SINCE.format(rel=rel))
            continue
        _remove(vault, rel, rt["turn"])
        deleted.append(rel)
        gone_ids.append(o["id"])
    if gone_ids:
        inventory.set_status(vault, gone_ids, "deleted")
    _seal(vault, conversation)
    sentence = f"已刪 {len(deleted)} 份成品（回合 {target['turn']}）"  # 沒有「問」，不用遮
    if deleted:  # 刪 0 份不寫段（疑義裁定 4）
        journal_cli.record_program_or_warn(vault, sentence, [{"path": rel, "action": "delete"} for rel in deleted],
                                           "versions delete-outputs", conversation)
    if as_json:
        print(json.dumps({"turn": target["turn"], "by_turn": rt["turn"], "deleted": deleted, "missing": missing,
                          "kept": kept_msgs}, ensure_ascii=False))
        return 0
    if deleted:
        print(f"{sentence}：")
        for rel in deleted:
            print(f"    {rel}")
    for rel in missing:
        print(ALREADY_GONE.format(rel=rel))
    for msg in kept_msgs:
        print(msg)
    return 0


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT  # CLI 不受 hook 的 10 秒限制，等久一點才不會留下半套（R2-L3）
    as_json = getattr(args, "json", False)
    if args.versions_command == "list":
        return _list(vault, args.n, as_json)
    # 對話裡叫的（有 sid、還沒答完）在動作前判斷一次（D-085 補）：退回自己這句時會開一個帶 sid 的新回合接著那句，
    # 判斷要跟動作前一致；這個結果也決定做完要不要封住動作回合（_seal）
    conversation = journal_cli.in_conversation(vault)
    if args.versions_command == "delete-outputs":
        return _delete_outputs(vault, args.code, as_json, conversation)
    return _revert(vault, args.code, args.delete_outputs, as_json, conversation)
