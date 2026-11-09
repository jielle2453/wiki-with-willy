"""kb versions list／revert／delete-outputs／clean：列最近的回合、退回最近一次沒退過的回合、刪已退回回合留著的成品、清放太久的回合備份與快照（D-074、D-011、D-207）。

退回只從最近一次開始，不跳；退回前先把現況備進「退回」這句話自己的回合，退錯可再退。
成品（清冊 created_turn 是那回合且 manifest 記 create 的）預設留著、status 改 turn_reverted，revert 只列不問；
kb intake 收進收件匣的檔（origin intake）不是成品：照備份拿掉、清冊改 turn_reverted，退回「退回」本身時照成品那套改回 active（收件匣第 4 點）；
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
B-074（備份還原與換電腦）：人讀輸出不唸回合代號，每個回合用「MM-DD HH:MM 那句「…」」描述（list、revert 成功句、只能從最近一次退、
delete-outputs 的句子）；--json 照留代號。clean：本體在 _lib/clean，回合備份超過天數或個數就從最舊的清、至少留最近幾個。
多個對話同開（#20，D-506～D-508）：有 --session（守門替對話插的）時，list、revert、delete-outputs 只看這個對話的回合加上沒有對話的
（終端機、背景健檢、舊回合），別的對話的跳過；turn.json 沒記 session 的 job 回合照 batches.json 的 job 認。終端機跑的照舊看全部。
要退的回合碰過的路徑，後來又被任何回合碰過（回合代號比它晚，或那一列的時間比它晚；大小寫不分、夾與夾裡的檔也算）就整個不退、
什麼都不動，列出是哪幾份、被哪一句改過（D-508）。
"""
import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import card_base, credentials, inventory, items, ledger, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.areas import HOLD_DIR
from kb._lib.io import read_json, write_json_atomic, write_text_atomic

ENTRY_LINES_MAX = 15
CLI_LOCK_TIMEOUT = 30
REVERT_LOCK_TRY = 0.2  # 只試一下（殘留鎖照 FileLock 清掉）；搶不到就不排隊（測試第二輪後裁定 24）
NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"
TOUCHED_THIS_TURN = "{rel} 這一句話已經改過，下一句再刪"
CHANGED_SINCE = "{rel} 之後被改過，留著"
ALREADY_GONE = "{rel} 已經不在"
BACKUP_CHANGED = "沒換回 {n} 檔：改之前那份（版本檔）之後被直接改過，跟當時不一樣，現在的檔與那份版本檔照留：{names}"  # D-529 硬連結的備份
MADE_KEPT = "建的資料夾「{rel}」裡有東西，留著"
FOLDER_OPEN = "退不回去：「{rel}」裡有檔案正開著（檔案總管或 Office），什麼都沒動；關掉再說一次退回"
FOLDER_FAILED = "退不回去：{why}，什麼都沒動"
BAD_LEDGER = "回合紀錄的資料夾路徑不對，沒有退回"
BAD_MANIFEST = "回合紀錄的檔案路徑不對，沒有退回"
NOT_AN_OUTPUT = "這份不在暫存區裡，沒有刪"
REVERT_BUSY = "另一個退回正在跑，等它做完再說一次"
PURGED_LINE = "那一版因為不讀的夾清掉了，退不回"
IN_BIN = "{name} 在資源回收筒，要從那裡拿回來"
# 被擋的句子說怎麼解開（第一輪修）；跳過被擋的回合這版不做（B-410）
MOVE_BAD = "退不回去：回合紀錄的搬動路徑不對（{uri}），什麼都沒動；請服務人員看"
MOVE_PURGED = "退不回去：那一回合搬過的檔在清掉的不讀夾裡，紀錄已經清掉，什麼都沒動"
MOVE_UNREGISTERED = "退不回去：「{alias}」現在沒有登記；要退回得先把它加回來，什麼都沒動"
MOVE_EXCLUDED = "退不回去：「{name}」在設成不讀的夾裡；要退回得先取消不讀，什麼都沒動"
MOVE_GONE = "退不回去：「{name}」不見了，搬不回「{folder}」；把它找回來放回去再說退回，什麼都沒動"
MOVE_TAKEN = "退不回去：「{folder}」裡已經有「{name}」了；先把那份改名或搬走再說退回，什麼都沒動"
MOVE_NO_PARENT = "退不回去：原本的夾「{folder}」不見了；把夾建回來再說退回，什麼都沒動"
MOVE_OPEN = "退不回去：「{name}」裡有檔案正開著，什麼都沒動；關掉再說一次退回"
MOVE_FAILED = "退不回去：{why}，什麼都沒動"
MOVE_CREDENTIAL = "退不回去：「{name}」裡有檔名像密碼或金鑰的檔，不搬進知識庫；先把那些檔移開再說退回，什麼都沒動"
PURGED_MARK = "（已排除）"
FILE_OPEN = "退不回去：「{rel}」正開著（檔案總管、Office 或記事本），什麼都沒動；關掉再說一次退回"
UNEXPECTED = "退不回去：{why}；可能只退了一部分，先別再說退回，請服務人員看一下"
AGAIN = "再說一次「退回」會把這次的退回取消，不會再往前退。"  # 成功時最後一行（B-441：D-074 連退兩次是取消，不是再往前）
OVERLAP_HEAD = "退不回去：這一句之後，有別的地方也改過同一份，什麼都沒動"
OVERLAP_TAIL = "要退得先到那個對話退回它，或直接跟我說要改成什麼。"
OVERLAP_TAIL_OBSIDIAN = "那份後來你在 Obsidian 改過，退回會把那邊改的一起蓋掉，所以沒動；跟我說要改成什麼，我直接改。"
OBSIDIAN_SAID = "你在 Obsidian 按的"
SIDE_WORDS = {"other": "另一個對話", "none": "對話外", "self": "這個對話"}
OTHER_LINE = "另一個對話還有 {n} 次改動，不在這裡退"
_DRIVE = re.compile(r"^[A-Za-z]:")
# 帳裡的路徑不能是這些保留區的根本身（測試第二輪後裁定 26）：資料夾帳只動型別夾、實體夾與 AI 產出底下的子夾
RESERVED_ROOTS = {s.casefold() for s in (".system", "系統", HOLD_DIR, "檔案索引")}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="versions_command", metavar="<子子命令>", required=True)
    ls = subs.add_parser("list", help="由新到舊列最近的回合", description="由新到舊列最近的回合。")
    ls.add_argument("--n", type=int, default=5, help="列幾個回合，預設 5")
    ls.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    rv = subs.add_parser("revert", help="退回最近一次沒退過的回合", description="退回最近一次沒退過的回合。")
    rv.add_argument("code", nargs="?", help="回合代號；只能是最近一次")
    rv.add_argument("--delete-outputs", action="store_true", help="連這回合的成品一起刪（客戶明說才加）")
    rv.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    it = subs.add_parser("items", help="列可以改回的項目（一項一個動作）", description="列可以改回的項目，編號 <回合代號>#<序>；沒指定回合列這個對話最近幾句。")
    it.add_argument("--turn", dest="turn_code", help="只列這個回合")
    it.add_argument("--n", type=int, default=5, help="列最近幾個回合，預設 5")
    it.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    un = subs.add_parser("undo", help="改回指定的項目或整個回合", description="照項目反著做；給回合代號等於那一句的全部項目。之後被動過的那一項不做、列 conflict。")
    un.add_argument("targets", nargs="+", help="項目編號（<回合代號>#<序>）或回合代號")
    un.add_argument("--yes", action="store_true", help="別台的人後來改過同一欄、她同意之後：照退（被別人改過的那幾欄不動）；只對那種項目有作用")
    un.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    do = subs.add_parser("delete-outputs", help="刪已退回回合裡留著的成品（客戶明說才做）",
                         description="刪「已退回的回合」裡 status 還是 turn_reverted 的成品；先備份再刪，再退一次救得回。")
    do.add_argument("code", nargs="?", help="回合代號；沒給取最近一個有可刪成品的已退回回合")
    do.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    cl = subs.add_parser("clean", help="清放太久的回合備份與快照（D-207）",
                         description="回合備份超過 30 天或 200 個就從最舊的清（至少留最近 20 個），快照留最近 2 份；--check 只算。")
    cl.add_argument("--check", action="store_true", help="只算會清什麼，不動任何東西")
    cl.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def turns(vault):
    """由新到舊：{turn, dir, started, prompt_head, session_id, reverted, reverted_turn, entries}。
    session_id 讀 turn.json；空的照 batches.json 的 job 回合補（card-maker 的 job 回合算派工的對話，#20）。"""
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
            "session_id": str(info.get("session_id") or ""),
            "source": str(info.get("source") or ""),
            "reverted": (d / "reverted.json").is_file(),
            "reverted_turn": info.get("reverted_turn"),
            "remarks": info.get("remarks") or {},
            "entries": turn.entries(vault, d.name),
            "folders": turn.folders(vault, d.name),
            "moves": turn.moves(vault, d.name),
            "writes": turn.writes(vault, d.name),
        })
    if any(not t["session_id"] for t in out):
        jobs = _job_sessions(vault)
        for t in out:
            t["session_id"] = t["session_id"] or jobs.get(t["turn"], "")
    return out


def _job_sessions(vault):
    """job 回合 → 派工的 session（batches.json 還在的 job 與留下的紀錄）；讀不到回空的。"""
    from kb._lib import batches
    try:
        return {j.get("turn"): j.get("session_id") for j in batches.jobs_of(batches.load(vault))
                if j.get("turn") and j.get("session_id")}
    except (OSError, ValueError):
        return {}


def _side(t):
    """從這次呼叫的對話（--session）看這個回合：self、other（別的對話的）、none（沒有對話：終端機、背景健檢、舊回合）。"""
    if not t.get("session_id"):
        return "none"
    return "self" if turn.SESSION and t["session_id"] == turn.SESSION else "other"


def _obsidian(t):
    return t.get("source") == "obsidian"


def _in_range(t):
    """退回看得到的回合（D-507、D-255）：Obsidian 外掛叫的（--via obsidian）只看 Obsidian 的；對話裡（--session）跳過別的對話的
    與 Obsidian 的；終端機（兩個都沒有）全部。"""
    if turn.VIA:
        return _obsidian(t)
    if turn.SESSION:
        return _side(t) != "other" and not _obsidian(t)
    return True


def _changed(t):
    return not t["reverted"] and bool(t["entries"] or t["reverted_turn"] or t["folders"] or t["moves"])


def _uri_rel(uri):
    """搬動帳的 uri 換成能跟檔案路徑比的寫法：知識庫裡的是相對路徑，收件匣、AI 產出接區名；登記的來源照留 uri。"""
    text = str(uri or "")
    if not text.startswith("file://"):
        return text
    alias, _sep, rel = text[len("file://"):].partition("/")
    if alias.casefold() == inventory.KNOWLEDGE.casefold():
        return rel
    if alias.casefold() == inventory.HOLD.casefold():
        return f"{alias}/{rel}"
    return text


def _touches(t):
    """回合碰過的路徑：[(路徑, 時間)]，manifest、writes.jsonl（同路徑第二次以後的寫入）、folders.jsonl、moves.jsonl 都算。"""
    from kb._lib import streams
    got = [(e.get("path"), e.get("time")) for e in t["entries"] + t.get("writes", [])
           if not (e.get("path") and streams.virtual_kind(Path(".") / e["path"]))]  # 流水檔的碰過看逐項（多人共用 1）
    got += [(f.get(k), f.get("time")) for f in t["folders"] for k in ("from", "to")]
    got += [(_uri_rel(m.get(k)), m.get("time")) for m in t["moves"] for k in ("from", "to")]
    return [(str(p).replace("\\", "/"), str(w or "")) for p, w in got if p]


def _overlap(all_turns, target):
    """D-508：要退的回合碰過的路徑，後來被別的回合碰過的 [{path, turn(回合 dict)}]。「後來」＝回合代號比它晚（不管哪個對話、
    退過沒有），或回合代號比它早、但碰同一個路徑的那一列時間比它那一列晚（先開口、後寫的別的對話；時間只到秒，別的對話同一秒也算）。
    同一個路徑大小寫不分，一個是另一個裡面的（夾與夾裡的檔）也算。寧可多擋。"""
    mine = [(p, w, _fkey(p).rstrip("/")) for p, w in _touches(target)]
    if not mine:
        return []
    first = min((w for _p, w, _k in mine if w), default="")
    got, seen = [], set()
    for t in all_turns:
        if t["turn"] == target["turn"]:
            continue
        later = t["turn"] > target["turn"]
        # 同一秒只在兩邊都是對話、又是不同對話時算（兩個對話真的會同時寫）；終端機與對話、同一個對話照時間先後，不算
        other = bool(t["session_id"] and target["session_id"] and t["session_id"] != target["session_id"])
        theirs = [(w, _fkey(p).rstrip("/")) for p, w in _touches(t)]
        if not later and not any(w and first and (w > first or (other and w == first)) for w, _k in theirs):
            continue  # 比它早、每一列也都在它之前：不用逐一比

        def after(w, when):
            return later or bool(w and when and (w > when or (other and w == when)))
        for path, when, key in mine:
            if (path, t["turn"]) not in seen and any(
                    after(w, when) and (k == key or k.startswith(key + "/") or key.startswith(k + "/")) for w, k in theirs):
                seen.add((path, t["turn"]))
                got.append({"path": path, "turn": t})
    return got


def _refuse_overlap(hits, as_json):
    if as_json:
        print(json.dumps({"refused": "overlap", "files": [
            {"path": h["path"], "turn": h["turn"]["turn"], "describe": _describe(h["turn"]), "session": _side(h["turn"])}
            for h in hits]}, ensure_ascii=False))
        return 3
    print(OVERLAP_HEAD)
    for h in hits[:ENTRY_LINES_MAX]:
        side = _side(h["turn"])
        if _obsidian(h["turn"]):  # Obsidian 的回合不是「對話外」，是她在 Obsidian 按的（裁定 13）
            when = _short_time(h["turn"].get("started"))
            print(f"- {h['path']}：{when + ' ' if when else ''}{OBSIDIAN_SAID}")
            continue
        word = SIDE_WORDS[side] if turn.SESSION or side == "none" else "對話裡"  # 終端機沒有「這個對話」，不說「另一個」
        print(f"- {h['path']}：{_describe(h['turn'])}（{word}）")
    if len(hits) > ENTRY_LINES_MAX:
        print(f"……共 {len(hits)} 處")
    if any(not _obsidian(h["turn"]) for h in hits):  # 別的對話那句照舊；有 Obsidian 的再接一句（裁定 13、23）
        print(OVERLAP_TAIL)
    if any(_obsidian(h["turn"]) for h in hits):
        print(OVERLAP_TAIL_OBSIDIAN)
    return 3


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
    return len(parts) > 1 and parts[0].casefold() == HOLD_DIR.casefold()


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


FILE_ATTRIBUTE_HIDDEN = 0x2


def _set_hidden(path, on):
    """Windows 的隱藏屬性設上或拿掉；設不成丟 OSError（照退回的其他例外講）。"""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    attrs = kernel32.GetFileAttributesW(str(path))
    if attrs == 0xFFFFFFFF:
        raise ctypes.WinError()
    want = (attrs | FILE_ATTRIBUTE_HIDDEN) if on else (attrs & ~FILE_ATTRIBUTE_HIDDEN)
    if want != attrs and not kernel32.SetFileAttributesW(str(path), want):
        raise ctypes.WinError()


def _copy_back(vault, src, dst):
    """把備份寫回原位（型別夾編號裁定 10）：Windows 上隱藏的檔（kb init 把 CLAUDE.md 設成隱藏）shutil.copy2 寫不進去
    （來源不是隱藏的時候被拒），先拿掉隱藏、寫完設回去。能不能寫的檢查（_writable）照舊用 r+b 開：隱藏檔開得了，
    唯讀、被 Office 開著的照舊擋；隱藏這件事由這裡保證寫得了。寫完設回隱藏失敗不中斷退回：記一行 hook-errors.log（裁定 13）。"""
    hidden = os.name == "nt" and dst.is_file() and bool(getattr(os.stat(dst), "st_file_attributes", 0) & FILE_ATTRIBUTE_HIDDEN)
    if hidden:
        _set_hidden(dst, False)
    try:
        shutil.copy2(src, dst)
    finally:
        if hidden:
            try:
                _set_hidden(dst, True)
            except OSError as e:
                from kb._lib import ledger
                rel = Path(dst).relative_to(Path(vault)).as_posix() if Path(dst).is_relative_to(Path(vault)) else Path(dst).name
                ledger.log_error(vault, f"退回後「{rel}」設回隱藏沒成：{e.strerror or type(e).__name__}", source="versions")
    _remirror(vault, dst)


def _remirror(vault, path):
    """#49：本機那一半（.system/local/<種類>.json）換回或拿掉後，流水檔的副本要重寫（流水檔那一半先退時副本接的還是新的）。
    副本是衍生的，重寫不成只記一行 log，不中斷退回。"""
    try:
        from kb._lib import streams
        streams.remirror_local(vault, path)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"退回後副本沒重寫：{type(e).__name__}: {str(e)[:100]}", source="versions")


def _output_keys(vault):
    """暫存區裡哪些位置是 AI 產出（清冊 origin ai_output 的位置鍵）；清冊讀不到回 None（當暫存區全是成品，寧可多留）。"""
    try:
        entries = inventory.load(vault)["entries"]
    except ValueError:
        return None
    return {inventory.uri_key(loc.get("uri", "")) for e in entries if e.get("origin") == "ai_output"
            for loc in e.get("locations") or []}


def _is_output(vault, rel, sources, ai_keys):
    """暫存區（Z4）裡的成品：D-324 把 AI 產出併進暫存區後，退回時留著不拿掉的只有 AI 產出登記的那幾份，使用者放進來的照舊照備份拿掉。"""
    if turn.zone_of(vault, rel, sources) != "Z4":
        return False
    return ai_keys is None or inventory.uri_key("file://" + rel) in ai_keys


def _locked_file(vault, target, delete_outputs, sources):
    """測試第二輪後裁定 25（N-7）：改夾之前先試每個要還原或移除的檔能不能寫；回第一個不行的（現在的位置），都行回 None。"""
    back, ahead = _translator(target["folders"]), _forward(target["folders"])
    ai_keys = _output_keys(vault)
    for e in target["entries"]:
        rel = back(e.get("path"))
        now = ahead(rel)
        p = Path(vault) / now
        if not p.is_file():
            continue
        if e.get("action") == "create":
            if _is_output(vault, rel, sources, ai_keys) and not delete_outputs:
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
    # 掃描入冊的客戶檔、掃描補登記的 AI 產出（unbacked，B-400）都不是那一句做的成品
    n["outputs"] = sum(1 for e in entries if e.get("created_turn") == t["turn"] and e.get("origin") == "ai_output"
                       and not e.get("unbacked"))
    return n


def _fmt_time(started):
    try:
        return datetime.strptime(started, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return started


def _short_time(started):
    """B-074：人讀輸出不唸回合代號，用「MM-DD HH:MM」。讀不懂的時間回「那一次」。"""
    try:
        return datetime.strptime(started, "%Y-%m-%dT%H:%M:%S").strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _describe(t):
    """B-074：一個回合的白話描述「MM-DD HH:MM 那句「…」」；沒有那句話是「MM-DD HH:MM 那一次」；t 是 None（回合已被清掉）是「那一次」。"""
    if t is None:
        return "那一次"
    when = _short_time(t.get("started")) or "那一次"
    head = credentials.mask_secrets(" ".join(str(t.get("prompt_head") or "").split()))
    if when == "那一次":
        return when
    return f"{when} 那句「{head}」" if head else f"{when} 那一次"


def _turn_of(all_turns, code):
    return next((x for x in all_turns if x["turn"] == code), None)


def _list(vault, n, as_json):
    all_turns = turns(vault)
    current = (turn.now(vault) or {}).get("turn")
    if as_json:  # --json 照列全部，每筆帶 session（#20）
        print(json.dumps([{
            "turn": t["turn"], "started": t["started"], "prompt_head": t["prompt_head"], "session": _side(t),
            "source": t["source"], "reverted": t["reverted"], "reverted_turn": t["reverted_turn"], "current": t["turn"] == current,
            "counts": _counts(vault, t), "entries": t["entries"],
            "folders": [{k: f.get(k) for k in ("op", "from", "to")} for f in t["folders"]],
            "moves": [{k: m.get(k) for k in ("op", "kind", "from", "to")} for m in t["moves"]],
        } for t in all_turns[:n]], ensure_ascii=False))
        return 0
    if not all_turns:
        print("還沒有任何改動紀錄")
        return 0
    others = sum(1 for t in all_turns if not _in_range(t) and not _obsidian(t) and _changed(t))  # Obsidian 的自成一組，不算「另一個對話」
    for t in [t for t in all_turns if _in_range(t)][:n]:
        c = _counts(vault, t)
        flags = (("（Obsidian）" if _obsidian(t) else "") + ("（已退回）" if t["reverted"] else "")
                 + ("（這一次）" if t["turn"] == current else ""))
        outputs = "" if c["outputs"] is None else f"、成品 {c['outputs']}"
        print(f"{_describe(t)}{flags}｜修改 {c['modify']}、新增 {c['create']}、刪除 {c['delete']}{outputs}")
        for line in _folder_lines(t["folders"]):
            print(line)
        for line in _move_lines(t["moves"]):
            print(line)
        for e in t["entries"][:ENTRY_LINES_MAX]:
            print(f"    {e.get('action')}：{e.get('path')}")
        if len(t["entries"]) > ENTRY_LINES_MAX:
            print(f"    ……共 {len(t['entries'])} 檔")
    if others:
        print(OTHER_LINE.format(n=others))
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
    if a == inventory.HOME_ALIAS:  # 搬到知識庫外沒登記的地方（outside）：file://~/…，從家目錄起算（放寬甲）
        return Path(os.path.realpath(os.path.expanduser("~")))
    if a == inventory.KNOWLEDGE.casefold():
        return Path(os.path.realpath(str(vault)))
    for src in sources.entries:  # 暫存區登記在團隊雲端（D-324）也先照登記的位置（#41）
        if src["alias"].casefold() == a:
            return Path(os.path.realpath(src["root"]))
    if a == inventory.HOLD.casefold():
        return Path(os.path.realpath(str(vault))) / inventory.HOLD
    return None


def _abs_move_path(vault, sources, text, rel):
    """搬動帳裡的絕對路徑 uri（file:///D:/a/b）→ (路徑, None) 或 (None, 拒絕句)。不信紀錄：不准 ..／空段／段裡的冒號（磁碟代號只准開頭）／
    結尾的點或空白；解出來不能在知識庫裡（那要用 知識庫 別名）、不能在不讀的夾裡。"""
    from kb._lib.paths import inside, normalize
    drive = _DRIVE.match(rel)
    body = rel[2:] if drive else rel
    segs = [s for s in re.split(r"[\\/]", body)]
    if drive and segs and segs[0] == "":
        segs = segs[1:]
    if (not segs or any(seg in ("", ".", "..") or ":" in seg or seg.endswith((".", " ")) for seg in segs)):
        return None, MOVE_BAD.format(uri=text)
    full = inventory.abs_of_uri(text)
    if full is None:
        return None, MOVE_BAD.format(uri=text)
    real = os.path.realpath(os.path.abspath(str(full)))
    rn, vn = normalize(real, None), normalize(os.path.realpath(str(vault)), None)
    if rn == vn or inside(rn, vn):
        return None, MOVE_BAD.format(uri=text)
    status, _src = sources.lookup(rn)
    if status == "excluded":
        return None, MOVE_EXCLUDED.format(name=segs[-1])
    return full, None


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
    if alias == "" and _sep:  # file:///D:/…：搬到知識庫外沒登記的地方（家目錄外的絕對路徑，放寬甲）
        return _abs_move_path(vault, sources, text, rel)
    segs = re.split(r"[\\/]", rel)
    # 段裡的冒號（磁碟代號、資料流）與結尾的點或空白（Windows 會吃掉，「系統.」就是「系統」）一律不收（收尾 security-review）
    if (not rel or rel.startswith(("/", "\\")) or _DRIVE.match(rel) or Path(rel).is_absolute()
            or any(seg in ("", ".", "..") or ":" in seg or seg.endswith((".", " ")) for seg in segs)):
        return None, MOVE_BAD.format(uri=text)
    root = _move_root(vault, sources, alias)
    if root is None:
        return None, MOVE_UNREGISTERED.format(alias=alias)
    full = root.joinpath(*segs)
    # 照作業系統真的會用的路徑驗：同一顆磁碟、realpath 後在根底下（短檔名、連結都展開）
    real_root = os.path.realpath(str(root))
    real = os.path.realpath(os.path.abspath(str(full)))
    if full.drive.casefold() != root.drive.casefold() or os.path.normcase(real) == os.path.normcase(real_root) \
            or not os.path.normcase(real).startswith(os.path.normcase(real_root).rstrip("\\/") + os.sep):
        return None, MOVE_BAD.format(uri=text)
    parts = os.path.relpath(real, real_root).split(os.sep)
    if alias.casefold() == inventory.KNOWLEDGE.casefold() and (
            parts[0].startswith(".") or parts[0].casefold() in inventory.VAULT_RESERVED_DIRS):
        return None, MOVE_BAD.format(uri=text)
    rn, fn = normalize(real_root, None), normalize(real, None)
    if fn == rn or not inside(fn, rn):
        return None, MOVE_BAD.format(uri=text)
    status, _src = sources.lookup(fn)
    if status == "excluded":
        return None, MOVE_EXCLUDED.format(name=segs[-1])
    return full, None


def credential_inside(vault, path):
    from kb._lib.source_edit import credential_inside as inside_
    return inside_(vault, path)


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
        if (m["kind"] == "folder" and str(m["from"]).casefold().startswith("file://" + inventory.KNOWLEDGE.casefold() + "/")
                and os.path.isdir(dst) and credential_inside(vault, dst)):
            return None, MOVE_CREDENTIAL.format(name=name)  # 搬回知識庫裡守門就不查憑證樣式了
        state[os.path.normcase(str(dst))], state[os.path.normcase(str(src))] = False, True
        steps.append({"op": m["op"], "kind": m["kind"], "src": src, "dst": dst, "from_uri": m["from"], "to_uri": m["to"]})
    return steps, None


def _move_any(src, dst):
    """搬回去：同一顆硬碟 os.rename；不同顆（跨磁碟搬過的）shutil.move 複製再拿掉（放寬甲）。"""
    try:
        os.rename(src, dst)
    except OSError as e:
        if getattr(e, "errno", None) == 18 or getattr(e, "winerror", None) == 17:
            shutil.move(str(src), str(dst))
        else:
            raise


DRIVE_GONE = "原本放「{name}」的「{place}」現在找不到：先請使用者把隨身碟或磁碟接上再改回。"


def _drive_gone(vault, sources, entries):
    """要放回登記來源（知識庫外）的備份，來源的根夾現在找不到：回 (檔名, 根夾名) 第一個；都在回 None。"""
    for e in entries:
        uri = e.get("uri")
        if not uri or e.get("action") not in ("modify", "delete"):
            continue
        alias, _rel = inventory.split_uri(uri)
        src = next((x for x in sources.entries if x["alias"] == alias), None)
        if src is not None and not os.path.isdir(src["root"]):
            return Path(str(uri)).name, Path(src["root"]).name or alias
    return None


def _undo_source_moves(vault, steps):
    """照檢查過的步驟把客戶的檔或夾搬回去；一步失敗就把已搬的倒回去、丟 _FolderFailed。回搬了的。"""
    done = []
    for st in steps:
        try:
            _move_any(st["dst"], st["src"])
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
            _move_any(st["src"], st["dst"])
        except OSError as e:
            ledger.log_error(vault, f"退回倒不回去 {st['from_uri']}: {type(e).__name__}: {e}", source="versions")


def _rescan(vault, rows, turn_id):
    """搬回去之後對清冊掃一次那幾個根（大小、時間、不見照實）；有變才套用。"""
    aliases = sorted({inventory.split_uri(u)[0] for m in rows for u in (m.get("from"), m.get("to")) if u
                      and inventory.split_uri(u)[0] not in (inventory.HOME_ALIAS, "")})
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
    _remirror(vault, Path(vault) / rel)  # #49 審查：那一回合才建的本機那一半被拿掉，副本也要跟著換


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
    if turn.VIA:
        return rt  # Obsidian 外掛叫的：這次呼叫自己的回合就是動作回合，不動 current.json（D-255）
    if not rt.get("session_id") or rt.get("done"):
        return turn.start(vault, None, f"退回 {target_turn}")
    if rt["turn"] == target_turn:
        rt = turn.start(vault, rt["session_id"], f"退回 {target_turn}", inherit=rt)
    return rt


def _seal(vault, conversation):
    """終端機跑的（不在對話裡）做完後再 start(None) 一次，把動作回合封起來（第二輪裁定 (ii)）：
    之後的指令進新回合，下一次退回只退那些，不會連這次退回一起撤掉。對話裡的不封，同一句的寫入要記在同一個回合。"""
    if not conversation and not turn.VIA:
        turn.start(vault, None, "")


def _touched_paths(vault, turn_id):
    return {e.get("path") for e in turn.entries(vault, turn_id)}


def _unchanged_since_registered(vault, rel, entry):
    fps = entry.get("fingerprints") or []
    if not fps:
        return True
    return inventory.sha256_of(Path(vault) / rel) == fps[-1].get("sha256")


def _reconcile(vault, rels, turn_id=None):
    """第一輪 M3：退回動完後對每個碰到的 Z3 路徑對帳清冊：檔在就該 location 最新一筆改 active，不在就改 deleted。
    更早回合建的線上文件（kind link，還是 active）的下載檔不在：只拿掉那個 file:// 位置，條目照舊 active、網址留著（B-402；清冊先備進
    動作回合）。被退的那一回合自己建的 link 條目是那一回合的成品，前面已照成品改過 status（deleted 或 turn_reverted），這裡不動。"""
    if not rels:
        return
    data = inventory.load(vault)
    to_active, to_deleted = [], []
    for rel in rels:
        key = inventory.uri_key("file://" + rel)
        same = [e for e in data["entries"] if any(inventory.uri_key(loc.get("uri", "")) == key for loc in e.get("locations", []))]
        if not same:
            continue
        present = (Path(vault) / rel).is_file()
        if not present and inventory.is_link(same[-1]) and same[-1].get("status") == "active":
            if turn_id:
                turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
            inventory.detach_location(vault, same[-1]["id"], rel)
            continue
        ((to_active if present else to_deleted)).append(same[-1]["id"])
    if to_active:
        inventory.set_status(vault, to_active, "active")
    if to_deleted:
        inventory.set_status(vault, to_deleted, "deleted")


def _mark_acting_turn(vault, acting_turn, target_turn, remarks=None):
    """動作回合的 turn.json 記 reverted_turn（第二輪 R2-4）；退的是「退回」本身時另記 remarks（當時撤掉的標記原文，
    B 段落差 10）：再退掉這個動作回合時照原文標回去，versions list 的「已退回」才跟實際對得上。"""
    vdir = turn.turn_dir(vault, acting_turn)
    meta = read_json(vdir / "turn.json", {}) or {}
    meta["reverted_turn"] = target_turn
    if remarks:
        meta["remarks"] = remarks
    write_json_atomic(vdir / "turn.json", meta)


def _undo_revert_marks(vault, target):
    """退掉「退回」本身：原回合的 reverted.json 撤掉，它留著的成品（檔還在）改回 active（第二輪 R2-4）；
    那次退回當時撤掉的標記（remarks）照原文標回去、成品改回 turn_reverted（B 段落差 10）。回這一次撤掉的 {回合: 標記原文}。"""
    original_turn = target["reverted_turn"]
    unmarked = {}
    marker = Path(vault) / turn.VERSIONS_REL / original_turn / "reverted.json"
    if marker.is_file():
        unmarked[original_turn] = read_json(marker, {}) or {}
        marker.unlink()
    for again, content in (target.get("remarks") or {}).items():
        if (Path(vault) / turn.VERSIONS_REL / again).is_dir():
            write_json_atomic(Path(vault) / turn.VERSIONS_REL / again / "reverted.json", content or {})
    entries_ = inventory.load(vault)["entries"]
    back = [e["id"] for e in entries_
            if e.get("created_turn") == original_turn and e.get("status") == "turn_reverted"
            and (rel := _rel_of(e)) and (Path(vault) / rel).is_file()]
    if back:
        inventory.set_status(vault, back, "active")
    again_ids = [e["id"] for e in entries_ if e.get("created_turn") in (target.get("remarks") or {})
                 and e.get("status") == "active" and e.get("origin") == "ai_output"]
    if again_ids:
        inventory.set_status(vault, again_ids, "turn_reverted")
    return unmarked


def _backup_changed(row, src):
    """硬連結的備份（manifest 記了 sha256，D-529）：版本檔被原地改過，備份跟著變了。"""
    return bool(row.get("sha256")) and inventory.sha256_of(src) != row["sha256"]


def _linked_to(path, srcs):
    try:
        return any(os.path.samefile(path, s) for s in srcs)
    except OSError:
        return False


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
    cands = [t for t in all_turns if _in_range(t) and _changed(t)]  # D-507：別的對話的跳過
    if not cands:
        print("沒有可以退回的改動")
        return 3
    target = cands[0]
    if code and code != target["turn"]:
        print(f"只能從最近一次開始退：{_describe(target)}")
        return 3
    hits = _overlap(all_turns, target)
    stream_items = [it for it in items.Ledger(vault).items(target["turn"]) if it.get("stream")]         if any(e.get("stream") for e in target["entries"]) else []
    if stream_items and _stream_conflicted(vault, stream_items):  # 流水檔同一欄之後被動過（多人共用 1）：逐項，被動過的列 conflict
        return _undo_locked(vault, [target["turn"]], as_json, conversation, all_turns)
    if hits:  # 放寬丙（D-321(4)）：後來別處也改過同一份，改成逐項：沒被動過的照退，被動過的那一項列 conflict 交 AI 判斷
        return _undo_locked(vault, [target["turn"]], as_json, conversation, all_turns)
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
    if refused:  # D-175：搬動帳有一項不過整個不退，回合、檔、夾都不動；紀錄壞了才記 log，預期中的拒絕不記
        if refused.startswith(MOVE_BAD.split("{")[0]):
            ledger.log_error(vault, f"{target['turn']} moves 路徑不對：{refused}", source="versions")
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
    gone = _drive_gone(vault, sources_now, target["entries"])
    if gone:  # 放寬甲：原位置在隨身碟、網路磁碟上而現在不在，先請使用者接上，什麼都不動
        print(DRIVE_GONE.format(name=gone[0], place=gone[1]))
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
        of_turn = inventory.outputs_of_turn(vault, target["turn"])
        outputs = [o for o in of_turn if o.get("origin") == "ai_output" and _rel_of(o) in created]
        # 收件匣：kb intake 這回合收進來的檔照備份拿掉（不像成品留著），清冊改 turn_reverted（收件匣第 4 點）
        intakes = [o for o in of_turn if o.get("origin") == "intake" and o.get("intake") and _rel_of(o) in created]
        inv_error = None
    except ValueError as e:
        outputs, intakes, inv_error = [], [], str(e)
    out_rels = _rels_of(outputs)
    ai_keys = _output_keys(vault)

    restored, removed, lost, touched_z3, kept_msgs, kept_ids = [], [], [], [], [], []
    changed_backup, changed_srcs = [], []
    purged = False
    sctx = {"vault": vault, "rt": rt["turn"], "restored": [], "render": set()}
    for it in sorted(stream_items, key=lambda i: i["seq"], reverse=True):  # 流水檔：每一項寫一行反向的（多人共用 1）
        status, _why, _msg = _undo_stream_item(sctx, it)
        if status == "done" and it["file"] not in restored:
            restored.append(it["file"])
    for path in sorted(sctx["render"]):
        _render_record_md(vault, path, rt["turn"])
    for e in reversed(target["entries"]):
        recorded, action = e.get("path"), e.get("action")
        if e.get("stream"):
            continue
        if action == "purged":  # 不讀的夾清掉了：那一版沒有了（D-174）
            purged = True
            continue
        uri = e.get("uri")
        if uri:  # 登記的來源（知識庫外）的備份：照 uri 找回原位（放寬甲）
            ext = inventory.path_of(vault, sources, uri)
            if ext is None:
                lost.append(uri)
                continue
            src = target["dir"] / "files" / recorded
            if action in ("modify", "delete"):
                if not src.is_file():
                    lost.append(uri)
                    continue
                if _backup_changed(e, src):
                    changed_backup.append(uri)
                    changed_srcs.append(src)
                    continue
                turn.backup_source(vault, ext, None, rt["turn"], sources)
                Path(ext).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, ext)
                restored.append(uri)
            elif action == "create" and Path(ext).is_file():
                if _linked_to(ext, changed_srcs):  # 被原地改過的版本檔：照留
                    continue
                turn.backup_source(vault, ext, None, rt["turn"], sources)
                os.remove(ext)
                removed.append(uri)
            continue
        rel = back(recorded)  # 記在搬過的夾裡的檔，夾改回去之後在舊位置
        dst = Path(vault) / rel
        is_z3 = _is_output(vault, rel, sources, ai_keys)
        if action in ("modify", "delete"):
            src = target["dir"] / "files" / recorded
            if not src.is_file():
                lost.append(rel)
                continue
            if _backup_changed(e, src):
                changed_backup.append(rel)
                changed_srcs.append(src)
                continue
            turn.backup(vault, rel, rt["turn"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            _copy_back(vault, src, dst)
            restored.append(rel)
            if is_z3:
                touched_z3.append(rel)
        elif action == "create" and dst.is_file() and not _linked_to(dst, changed_srcs):
            # 成品照 D-011：沒明說就留著，之後用 delete-outputs 刪。「退回」本身那回合的 create 是退回時放回去的舊檔
            # （例如收起來的草稿），不是那回合的成品：清冊沒把它記成那回合的成品就照常拿掉（B 段落差 10）
            restored_back = target["reverted_turn"] and inv_error is None and rel not in out_rels
            if is_z3 and not delete_outputs and not restored_back:
                continue
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
    gone_intakes = [o["id"] for o in intakes if not (Path(vault) / back(_rel_of(o))).is_file()]
    if gone_intakes:
        inventory.set_status(vault, gone_intakes, "turn_reverted")
    card_base.after_revert(vault, [r for r in restored if not r.startswith("file://")],
                           [r for r in removed if not r.startswith("file://")])  # 退回的卡基準換成那一版，不算她改的（D-270）
    if inv_error is None:
        _reconcile(vault, touched_z3, rt["turn"])
        _rescan(vault, target["moves"], rt["turn"])
    unmarked = {}
    if target["reverted_turn"]:
        try:
            unmarked = _undo_revert_marks(vault, target)
        except ValueError:
            pass  # 標記已撤；清冊讀不到，成品 status 不對帳
    _mark_acting_turn(vault, rt["turn"], target["turn"], unmarked)
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
    # R2-L3：升級前沒遮好的問會被搬進來，_describe 先壓空白再遮再嵌進句子，數字留著（D-086「程式段不遮」的例外）
    converted = _converted_line(vault, target["turn"], all_turns)  # 格式轉換不可退（#45-07）
    sentence = f"已退回 {_describe(target)}：還原 {len(restored)}、移除 {len(removed)}、{tail}"  # B-074：不唸回合代號
    journal_cli.record_program_or_warn(
        vault, sentence,
        [{"path": rel, "action": "modify"} for rel in restored] + [{"path": rel, "action": "delete"} for rel in removed],
        "versions revert", conversation)
    if as_json:
        print(json.dumps({
            "turn": target["turn"], "prompt_head": target["prompt_head"], "by_turn": rt["turn"],
            "restored": restored, "removed": removed, "lost": lost, "backup_changed": changed_backup,
            "outputs": out_rels, "outputs_deleted": bool(delete_outputs), "kept": kept_msgs,
            "undone_revert_of": target["reverted_turn"], "inventory_error": inv_error,
            "folders": folders_done, "folders_kept": folders_kept,
            "moves": [{k: m.get(k) for k in ("op", "kind", "from", "to")} for m in target["moves"]],
            "in_bin": [_uri_name(m["from"]) for m in target["moves"] if m["op"] == "trash"], "purged": purged,
            "again": AGAIN, "converted": bool(converted),
        }, ensure_ascii=False))
        return 0
    if lost:
        print(f"無法還原 {len(lost)} 檔（備份已不在）：" + "、".join(lost))
    if changed_backup:
        print(BACKUP_CHANGED.format(n=len(changed_backup), names="、".join(changed_backup)))
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
        was = _describe(_turn_of(all_turns, target["reverted_turn"]))
        print(f"這一次退的是那次「退回」本身，{was}不再算已退回。")
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
    if converted:
        print(converted)
    print(AGAIN)
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
        if not t["reverted"] or not _in_range(t):
            continue
        outs = [e for e in all_entries if e.get("created_turn") == t["turn"]
                and (e.get("status") == "turn_reverted"
                     or (e.get("status") == "superseded" and _rel_of(e) in touched))]
        if outs:
            cands.append((t, outs))
    if code:
        cands = [(t, o) for t, o in cands if t["turn"] == code]
        if not cands:
            print(f"{_describe(_turn_of(turns(vault), code))}沒有可刪的成品")
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
    sentence = f"已刪 {len(deleted)} 份成品（{_describe(target)}）"  # B-074：不唸回合代號
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


def _clean(vault, check, as_json, conversation):
    """kb versions clean（備份還原與換電腦 要做的事 3，D-207）：本體在 _lib/clean；清掉的回合不進回合備份（它本身就是備份）。"""
    from kb._lib import backup, clean, moved
    try:
        got = clean.run(vault, check=check)
    except backup.Refused as e:
        print(json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False) if as_json else str(e))
        return 3
    if not check:
        try:
            moved.note_place(vault)  # 這題的指令第一次跑成功時記位置（測試 T-2）；寫不了不擋、拒絕時不寫
        except (OSError, ValueError):
            pass
    sentence = clean.sentence(got)
    if not check and (got["turns_removed"] or got["snapshots_removed"]):
        journal_cli.record_program_or_warn(vault, sentence.split("；")[0], [], "versions clean", conversation)
    print(json.dumps(got, ensure_ascii=False) if as_json else sentence)
    return 0


# ---------- 逐項改回（放寬丙 要做的事 2，D-321） ----------

WHY_WORDS = {"expired": "那一版的備份已經清掉了", "gone": "willy 沒留一份，改不回來", "purged": "那一版因為不讀的夾清掉了，退不回",
             "undone": "已經改回過了", "failed": "沒做成", "refused": "紀錄的路徑不對", "kept": "先留著", "taken": "原位已經有東西了",
             "missing": "找不到要改回的東西"}
OUTPUT_KEPT = "{rel} 是成品，先留著；要刪跟我說，我用 delete-outputs 刪。"
VERSION_KEPT = "{name} 照留（舊的那份還在）"  # D-529 版本檔沒拿掉
NO_SUCH_TARGET = "找不到這個項目或回合：{token}"
CONVERTED = "{what}升級時把資料換成新的存法，這一步退不回去"  # 格式轉換不可退（jacky 2026-11-05 定，#45-07）；B-074 不唸代號


def _converted_line(vault, token, all_turns=None):
    """token 是做了格式轉換的那一句 → 白話一句；不是 → None。"""
    from kb._lib import migrate
    if not migrate.converted_turn(vault, token):
        return None
    t = _turn_of(all_turns if all_turns is not None else turns(vault), str(token).split("#", 1)[0])
    return CONVERTED.format(what=f"{_describe(t)}：" if t else "那一句")


def _not_found(vault, token):
    return _converted_line(vault, token) or NO_SUCH_TARGET.format(token=token)
NO_ITEMS = "沒有可以改回的項目"
UNDO_HEAD = "已改回 {n} 項"
_PLAIN_OP = {"missing": "不見了", "added": "多了一份", "modified": "被改過"}
_RECORD_NOW = {"set": "改了內容", "add": "新增", "remove": "拿掉"}
_RECORD_DONE = {"set": "改回舊值", "add": "新增的拿掉", "remove": "拿掉的加回"}


def _item_text(it, done=False):
    """一項的白話（items 與 undo 的人讀輸出共用）。done：已改回的口氣。"""
    from kb._lib.source_edit import folder_word
    name = it["what"]
    op, src = it["op"], it["src"]
    if src == "record":
        return f"{name}：{(_RECORD_DONE if done else _RECORD_NOW)[op]}"
    if src == "manifest":
        word = {"modify": "換回舊版" if done else "改了內容", "delete": "拿回來" if done else "刪了",
                "create": "拿掉" if done else "新增", "purged": "清掉了"}.get(op, op)
        if it.get("versions"):  # D-529：版本檔併在原檔這項
            labels = "、".join(items.version_label(v) for v in it["versions"])
            word += f"，{labels}拿掉" if done else f"（舊的存成{labels}）"
        return f"{name}：{word}"
    if src == "folders":
        if op == "move":
            return f"資料夾 {name}：" + (f"改回 {_uri_name(it['from'])}" if done else f"改名／搬成 {it['to']}")
        word = {"make": "拿掉" if done else "建了", "remove": "建回來" if done else "拿掉了空夾"}[op]
        return f"資料夾 {name}：{word}"
    if op == "trash":
        return f"{name}：丟進資源回收筒"
    if op == "rename":
        return f"{_uri_name(it['to'] or '')}：" + (f"名字改回 {name}" if done else f"改名（原本叫 {name}）")
    if op == "move":
        return f"{name}：" + (f"搬回 {folder_word(it['from'])}" if done else f"從 {folder_word(it['from'])} 搬到 {folder_word(it['to'] or '')}")
    return f"{name}：{_PLAIN_OP.get(op, op)}"


def _public_item(it, t, undone_by):
    out = {"item": it["item"], "turn": it["turn"], "said": _describe(t), "op": it["op"], "kind": it["kind"], "what": it["what"],
           "from": it["from"], "to": it["to"], "by": it["by"], "copy": bool(it["copy"]), "undoable": it["undoable"]}
    if it.get("versions"):
        out["versions"] = [v.get("uri") or v.get("path") for v in it["versions"]]
    if it["src"] == "record":
        out.update(file=it["file"], key=it["key"])
        if it.get("stream"):
            st = it["stream"]
            out.update(kind=st["kind"], id=it["key"], seq=st["seq"], set=st["set"])
    if it["item"] in undone_by:
        out["undoable"], out["why"] = False, "undone"
    elif not it["undoable"]:
        out["why"] = it["why"]
    return out


def _items(vault, turn_code, n, as_json):
    """kb versions items：列可以改回的項目。沒指定回合：這個對話（--session）最近 n 個有項目的回合；終端機看全部。"""
    led = items.Ledger(vault)
    all_turns = turns(vault)
    if turn_code:
        picked = [t for t in all_turns if t["turn"] == turn_code]
        if not picked:
            print(_not_found(vault, turn_code))
            return 3
    else:
        picked = [t for t in all_turns if _in_range(t) and led.items(t["turn"])][:n]
    rows = []
    for t in picked:
        undone = led.undone(t["turn"])
        rows += [_public_item(it, t, undone) for it in led.items(t["turn"]) if it.get("why") != "derived"]
    converted = {t["turn"]: ln for t in picked for ln in [_converted_line(vault, t["turn"], all_turns)] if ln}
    if as_json:
        print(json.dumps({"items": rows, "converted": list(converted.values())}, ensure_ascii=False))
        return 0
    if not rows:
        print("\n".join([NO_ITEMS, *converted.values()]))
        return 0
    by_turn = {}
    for r in rows:
        by_turn.setdefault(r["turn"], []).append(r)
    lookup = {it["item"]: it for t in picked for it in led.items(t["turn"])}
    for t in picked:
        if t["turn"] not in by_turn:
            continue
        print(f"{_describe(t)}{'（已退回）' if t['reverted'] else ''}")
        if t["turn"] in converted:
            print(f"  {converted[t['turn']]}")
        for r in by_turn[t["turn"]]:
            if r["undoable"] is False and r.get("why") == "undone":
                tail = "（已改回過）"
            else:
                tail = "" if r["undoable"] else f"（改不回：{WHY_WORDS.get(r.get('why'), r.get('why'))}）"
            print(f"  {r['item']}　{_item_text(lookup[r['item']])}{tail}")
    return 0


def _conflict_entry(it, hits, by_turn, vault=None):
    """conflict 一項：then（那一句做了什麼）、later（之後誰在哪一句改成什麼）、now（現在的值或位置）。"""
    then = {"op": it["op"], "from": it["what"], "to": _uri_name(it["to"]) if it["to"] else None}
    if it["src"] == "manifest" and it["copy"]:
        then = {"op": it["op"], "backup": f"{turn.VERSIONS_REL.as_posix()}/{it['turn']}/files/{it['row']['path']}"}
    elif it["src"] == "record":
        then = {"op": it["op"], "key": it["key"], "old": it["old"], "new": it["new"]}
    later = []
    if it.get("stream") and not hits:
        lines = _other_machine_lines(vault, it) if vault is not None else []
        out = _stream_conflict(it, lines)
        if vault is not None:
            out["now"] = _stream_now(vault, it)
            touched = set()
            for r in lines:
                touched |= set(r.get("set") or {}) | set(r.get("unset") or [])
            if touched:  # now 只列別人動過的那幾欄（例：新日期），不是整筆
                out["now"] = {f: v for f, v in out["now"].items() if f in touched}
        return out
    for h in hits:
        t = by_turn.get(h["turn"])
        obs = bool(t and _obsidian(t))  # Obsidian 的回合不是「對話外」，是她在 Obsidian 按的（裁定 13）
        said = f"{_short_time(t.get('started')) or ''} {OBSIDIAN_SAID}".strip() if obs else _describe(t)
        later.append({"item": h["item"], "side": "obsidian" if obs else (_side(t) if t else "none"), "said": said,
                      "op": h["op"], "to": h["to"] or h["from"]})
    last = hits[-1]
    now = last["to"] or last["from"]
    if it["src"] == "manifest":
        now = it["row"].get("path") or now
    elif it["src"] == "record":
        now = it["file"]
    out = {"item": it["item"], "why": "later", "then": then, "later": later, "now": now}
    if it.get("stream"):
        out["fields"] = it["stream"]["fields"]
        if vault is not None:
            out["now"] = _stream_now(vault, it)
    return out


def _stream_now(vault, it):
    from kb._lib import streams
    st = it["stream"]
    cur = streams.current(vault, st["kind"], it["key"]) or {}
    return {f: cur.get(f) for f in st["fields"]}


def _stream_conflict(it, lines=()):
    st = it["stream"]
    later = [{"item": None, "side": "other", "who": r.get("who") or "同事", "said": r.get("who") or "別台電腦", "op": "set",
              "to": r.get("set")} for r in lines] or [{"item": None, "side": "other", "who": "同事", "said": "別台電腦",
                                                       "op": "set", "to": None}]
    return {"item": it["item"], "why": "later", "then": {"op": it["op"], "key": it["key"], "old": it["old"], "new": it["new"]},
            "later": later, "now": None, "fields": st["fields"]}


def _other_changed(vault, it):
    """檔的項目：回合收尾後被別人改過（after.jsonl 的 sha 對不上）→ 替她做好一半、回 conflict 一項（多人共用 3 第 6 點）。"""
    from kb._lib import refresh
    rel = (it.get("row") or {}).get("path") if it.get("src") == "manifest" else None
    if not rel or not isinstance(rel, str):
        return None
    try:
        backup = Path(vault) / turn.VERSIONS_REL / it["turn"] / "files" / rel if it.get("copy") else None
        got = refresh.other_changed(vault, it["turn"], rel, backup)
    except Exception as e:  # noqa: BLE001  比不出來就照原本的退
        ledger.log_error(vault, f"退回前比別人改過失敗：{type(e).__name__}: {str(e)[:100]}", source="versions")
        return None
    if got is None:
        return None
    then = {"op": it["op"], "backup": f"{turn.VERSIONS_REL.as_posix()}/{it['turn']}/files/{rel}"}
    return {"item": it["item"], "why": "later", "then": then,
            "later": [{"side": "other", "who": got["who"], "via": got["via"], "said": f"{got['who']}改過"}], "now": rel,
            "done": got["done"], "copy": got["copy"]}


def _undo(vault, targets, as_json, conversation):
    lock = kio.FileLock(revert_lock_path(vault), timeout=REVERT_LOCK_TRY)
    try:
        lock.__enter__()
    except TimeoutError:
        print(REVERT_BUSY)
        return 3
    try:
        return _undo_locked(vault, targets, as_json, conversation)
    except Exception as e:  # noqa: BLE001  白話一句、不丟 traceback
        ledger.log_error(vault, f"undo 失敗：{type(e).__name__}: {e}", source="versions")
        print(UNEXPECTED.format(why=_plain(vault, e)))
        return 3
    finally:
        lock.__exit__(None, None, None)


def _resolve(led, targets):
    """項目編號或回合代號 → (項目清單去重、新的在前；找不到的)。"""
    got, unknown = {}, []
    for token in targets:
        if "#" in token:
            it = led.find(token)
            found = [it] if it else []
        else:
            found = led.items(token) if token in led.tids else []
        if not found:
            unknown.append(token)
        for it in found:
            got[it["item"]] = it
    return sorted(got.values(), key=lambda it: (it["turn"], it["seq"]), reverse=True), unknown


_YES = False  # kb versions undo --yes：別台改過同一欄、她說要退的項目照退（多人共用 2 第 11 點）


def _undo_locked(vault, targets, as_json, conversation, all_turns=None):
    led = items.Ledger(vault)
    all_turns = all_turns or turns(vault)
    by_turn = {t["turn"]: t for t in all_turns}
    todo, unknown = _resolve(led, targets)
    if unknown and not todo:
        print("\n".join(_not_found(vault, u) for u in unknown))
        return 3
    if not todo:
        print(NO_ITEMS)
        return 3
    converted = [ln for ln in (_converted_line(vault, c, all_turns) for c in sorted({it["turn"] for it in todo})) if ln]
    rt = _acting_turn(vault, todo[0]["turn"])
    ctx = {"vault": vault, "sources": sources_lib.load(vault), "rt": rt["turn"], "restored": [], "removed": [],
           "touched_z3": [], "moves": [], "render": set(), "kept": []}
    done, conflict, skipped = [], [], []
    done_ids = set()
    for it in todo:
        if it.get("why") == "derived":
            continue
        said = _describe(by_turn.get(it["turn"]))
        if it["item"] in led.undone(it["turn"]):
            skipped.append({"item": it["item"], "why": "undone", "said": said})
            continue
        if not it["undoable"]:
            if it["src"] == "detected" and it["op"] in ("added", "modified") and it["item"] not in targets:
                continue  # 整個回合改回時，使用者自己新增、改過的檔不是 willy 動的，不逐項喊「改不回」
            skipped.append({"item": it["item"], "why": it["why"], "said": said, "message": _unfixable_message(it)})
            continue
        hits = [h for h in items.conflicts_of(led, it) if h["item"] not in done_ids and h["turn"] != ctx["rt"]]
        if hits:
            conflict.append(_conflict_entry(it, hits, by_turn, vault))
            continue
        if it.get("stream") and _other_machine_lines(vault, it) and not (_YES and it["item"] in targets):  # 別台改過同一欄
            conflict.append(_conflict_entry(it, [], by_turn, vault))
            continue
        other = _other_changed(vault, it)  # 多人共用 3：這句之後被 pull 進來或雲端同步改過（沒有這台的回合紀錄，上面抓不到）
        if other:
            conflict.append(other)
            continue
        try:
            status, why, message = _do_item(ctx, led, it)
        except _FolderFailed as e:
            status, why, message = "skipped", "failed", e.line
        except Exception as e:  # noqa: BLE001  一項出事不拖累別項
            ledger.log_error(vault, f"undo {it['item']} 失敗：{type(e).__name__}: {e}", source="versions")
            status, why, message = "skipped", "failed", UNEXPECTED.format(why=_plain(vault, e))
        if status == "done":
            done.append(it)
            done_ids.add(it["item"])
        else:
            skipped.append({"item": it["item"], "why": why, "said": said, "message": message})
    _undo_finish(ctx, led, done)
    _seal(vault, conversation)
    kept = ctx["kept"]
    if as_json:
        print(json.dumps({"done": [it["item"] for it in done], "conflict": conflict, "skipped": skipped, "kept": kept,
                          "turn": ctx["rt"], "again": AGAIN if done else None, "converted": converted}, ensure_ascii=False))
    else:
        _undo_print(vault, done, conflict, skipped, kept, led, unknown, converted)
    if done:
        journal_cli.record_program_or_warn(
            vault, UNDO_HEAD.format(n=len(done)),
            [{"path": r, "action": "modify"} for r in ctx["restored"]] + [{"path": r, "action": "delete"} for r in ctx["removed"]],
            "versions undo", conversation, actions=items.actions_of_turn(vault, ctx["rt"]))
    return 0 if (done or conflict or kept) else 3


def _unfixable_message(it):
    why = it.get("why")
    if why == "gone" and it["op"] == "trash":
        return IN_BIN.format(name=it["what"])
    if why == "gone":
        hint = "它在資源回收筒的話去那裡拿" if it["op"] == "missing" else "要改回得自己動手"
        return f"「{it['what']}」willy 沒留一份；{hint}"
    return WHY_WORDS.get(why, "")


def _undo_finish(ctx, led, done):
    """做完的收尾：重算（跟 revert 的收尾一樣）、日程等的 md 重畫、標已改回。"""
    vault, rt = ctx["vault"], ctx["rt"]
    card_base.after_revert(vault, [r for r in ctx["restored"] if not r.startswith("file://")],
                           [r for r in ctx["removed"] if not r.startswith("file://")])
    try:
        _reconcile(vault, ctx["touched_z3"], rt)
        _rescan(vault, ctx["moves"], rt)
    except ValueError:
        pass  # 清冊讀不到：不對帳，下次整理時再比
    for path in sorted(ctx["render"]):
        _render_record_md(vault, path, rt)
    if not done:
        return
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for it in done:
        vdir = Path(vault) / turn.VERSIONS_REL / it["turn"]
        with kio.FileLock(vdir / "manifest.jsonl"):
            turn._append_line(vdir / "undone.jsonl", {"item": it["item"], "by_turn": rt, "time": stamp})
    undo_of = turn.turn_dir(vault, rt) / "undo_of.jsonl"
    for it in done:
        turn._append_line(undo_of, {"item": it["item"]})
    # 改回的是以前的「改回」本身（那個回合有 undo_of.jsonl）：它改回過的項目撤掉「已改回」記號，可以再改回
    for tid in {it["turn"] for it in done if (Path(vault) / turn.VERSIONS_REL / it["turn"] / "undo_of.jsonl").is_file()}:
        _unmark(vault, led, tid)


def _unmark(vault, led, acting_tid):
    for tid in led.tids:
        path = Path(vault) / turn.VERSIONS_REL / tid / "undone.jsonl"
        rows = items._read_lines(path)
        keep = [r for r in rows if r.get("by_turn") != acting_tid]
        if len(keep) != len(rows):
            write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))


def _render_record_md(vault, path, rt):
    """條目改回後，由它重算的 md 重畫（日程、待處理、裁示）；重畫失敗只記 log。"""
    try:
        if path == ".system/agenda.json":
            from kb._lib import agenda
            agenda.render(vault, turn_id=rt)
        elif path == ".system/pending.json":
            from kb._lib import pending
            pending.render(vault, turn_id=rt)
        elif path == ".system/rulings.json":
            from kb._lib import rulings
            rulings.render(vault, turn_id=rt)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"undo 重畫 {path} 失敗：{type(e).__name__}: {e}", source="versions")


def _undo_print(vault, done, conflict, skipped, kept, led, unknown, converted=()):
    lookup = {it["item"]: it for it in led.all()}
    for it in sorted(done, key=lambda i: (i["turn"], i["seq"])):
        print(_item_text(it, done=True))
    for c in conflict:
        it = lookup[c["item"]]
        last = c["later"][-1]
        if it.get("stream"):  # 別人改過同一欄：講是誰、現在是什麼，要退得她同意再 --yes（D-296）
            now = "、".join(f"{v}" for k, v in (c["now"] or {}).items() if isinstance(v, (str, int)) and k != "history")
            who = last.get("who") or last["said"]
            side = "之後又改過" if last.get("side") == "self" else "後來改過"
            print(f"{it['what']}：{who}{side}（現在是「{now}」），退掉這一項會蓋過去，這項沒改回；她說要退就加 --yes")
            continue
        now = _uri_name(str(c["now"])) if c["now"] else ""
        print(f"{it['what']}：之後又被動過（{last['said']}），現在是「{now}」，這項沒改回")
    for s in skipped:
        it = lookup.get(s["item"])
        print(f"{it['what'] if it else s['item']}：{s.get('message') or WHY_WORDS.get(s['why'], s['why'])}")
    for u in unknown:
        print(_not_found(vault, u))
    for ln in converted:  # 同一句的其他項目照退，格式轉換留著（#45-07）
        print(ln)
    if done:
        print(AGAIN)


def _do_item(ctx, led, it):
    """做一項。回 (done｜skipped, why, 句子)。"""
    src = it["src"]
    if src in ("moves", "detected"):
        return _undo_move_item(ctx, it)
    if src == "folders":
        return _undo_folder_item(ctx, it)
    if src == "record":
        return _undo_record_item(ctx, it)
    return _undo_file_item(ctx, led, it)


def _undo_move_item(ctx, it):
    vault, sources, rt = ctx["vault"], ctx["sources"], ctx["rt"]
    m = it["row"]
    if m["op"] == "trash":
        return "skipped", "gone", IN_BIN.format(name=_uri_name(m["from"]))
    src, why = _move_path(vault, sources, m.get("from"))
    dst, why2 = _move_path(vault, sources, m.get("to"))
    if why or why2:
        return "skipped", "refused", why or why2
    name, folder = _uri_name(m["to"]), _uri_name(str(m["from"]).rpartition("/")[0])
    if not os.path.lexists(dst):
        return "skipped", "gone", MOVE_GONE.format(name=name, folder=folder)
    if os.path.normcase(str(src)) != os.path.normcase(str(dst)) and os.path.lexists(src):
        return "skipped", "taken", MOVE_TAKEN.format(folder=folder, name=_uri_name(m["from"]))
    if not os.path.lexists(src.parent):
        return "skipped", "gone", MOVE_NO_PARENT.format(folder=folder)
    try:
        _move_any(dst, src)
    except PermissionError:
        return "skipped", "failed", MOVE_OPEN.format(name=name)
    turn.record_move(vault, rt, "move" if m["op"] == "move" else m["op"], it["kind"], m["to"], m["from"])
    ctx["moves"].append({"from": m["from"], "to": m["to"]})
    return "done", None, None


def _undo_folder_item(ctx, it):
    vault, rt = ctx["vault"], ctx["rt"]
    root = Path(vault).resolve()
    op, src, dst = it["op"], it["from"], it["to"]
    if not all(_path_ok(root, p) for p in (src, dst) if p is not None):
        return "skipped", "refused", BAD_LEDGER
    v = Path(vault)
    if op == "move":
        if not dst or not (v / dst).exists():
            return "skipped", "gone", f"退不回去：「{dst}」不見了，改不回「{src}」"
        if _fkey(src) != _fkey(dst) and (v / src).exists():
            return "skipped", "taken", f"退不回去：「{dst}」改回「{src}」時「{src}」已經有東西了"
        (v / src).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(v / dst, v / src)
        except PermissionError:
            return "skipped", "failed", FOLDER_OPEN.format(rel=dst)
        turn.record_folder(vault, rt, "move", dst, src)
    elif op == "make":
        if not (v / src).is_dir():
            return "done", None, None
        if any((v / src).iterdir()):
            return "skipped", "kept", MADE_KEPT.format(rel=src)
        (v / src).rmdir()
        turn.record_folder(vault, rt, "remove", src)
    else:  # remove：空夾建回來
        if (v / src).is_file():
            return "skipped", "taken", f"退不回去：「{src}」現在是檔案"
        if not (v / src).is_dir():
            (v / src).mkdir(parents=True)
            turn.record_folder(vault, rt, "make", src)
    return "done", None, None


def _undo_file_item(ctx, led, it):
    """manifest 的一檔：modify／delete 放回舊版、create 拿掉（成品留著）。登記的來源（有 uri）照 uri 找回原位。
    併進來的版本檔（D-529）：原檔換回之後再一份一份拿掉；硬連結的備份被原地改過就整項不動。"""
    if it.get("versions"):
        backup = led.backup_file(it["turn"], it["row"].get("path"))
        if backup.is_file() and _backup_changed(it["row"], backup):
            name = it["row"].get("uri") or it["row"].get("path")
            return "skipped", "failed", BACKUP_CHANGED.format(n=1, names=name)
        for v in it["versions"]:  # 版本檔開著：先擋，免得原檔換回了、版本檔拿不掉
            path = inventory.path_of(ctx["vault"], ctx["sources"], v["uri"]) if v.get("uri") else Path(ctx["vault"]) / v["path"]
            if path and Path(path).is_file() and not _writable(Path(path)):
                return "skipped", "failed", FILE_OPEN.format(rel=Path(path).name)
        got = _undo_file_item(ctx, led, dict(it, versions=None))
        if got[0] == "done":
            for v in reversed(it["versions"]):
                status, why, _msg = _undo_file_item(ctx, led, dict(it, row=v, versions=None))
                if status != "done" and why != "kept":  # 成品那條已經說「先留著」
                    ctx["kept"].append(VERSION_KEPT.format(name=str(v.get("uri") or v["path"]).rpartition("/")[2]))
        return got
    vault, sources, rt = ctx["vault"], ctx["sources"], ctx["rt"]
    e = it["row"]
    action, recorded, uri = e.get("action"), e.get("path"), e.get("uri")
    backup = led.backup_file(it["turn"], recorded) if recorded else None
    if uri:
        ext = inventory.path_of(vault, sources, uri)
        if ext is None:
            return "skipped", "gone", MOVE_UNREGISTERED.format(alias=inventory.split_uri(uri)[0])
        if action in ("modify", "delete"):
            if not backup.is_file():
                return "skipped", "expired", WHY_WORDS["expired"]
            gone = _drive_gone(vault, sources, [e])
            if gone:
                return "skipped", "gone", DRIVE_GONE.format(name=gone[0], place=gone[1])
            turn.backup_source(vault, ext, None, rt, sources)
            Path(ext).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, ext)
            ctx["restored"].append(uri)
        elif action == "create" and Path(ext).is_file():
            turn.backup_source(vault, ext, None, rt, sources)
            os.remove(ext)
            ctx["removed"].append(uri)
        return "done", None, None
    root = Path(vault).resolve()
    if not _path_ok(root, recorded):
        return "skipped", "refused", BAD_MANIFEST
    dst = Path(vault) / recorded
    is_z3 = _is_output(vault, recorded, sources, _output_keys(vault))
    if action in ("modify", "delete"):
        if not backup.is_file():
            return "skipped", "expired", WHY_WORDS["expired"]
        if dst.is_file() and not _writable(dst):
            return "skipped", "failed", FILE_OPEN.format(rel=recorded)
        turn.backup(vault, recorded, rt)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_back(vault, backup, dst)
        ctx["restored"].append(recorded)
        if is_z3:
            ctx["touched_z3"].append(recorded)
        return "done", None, None
    if action == "create":
        if not dst.is_file():
            return "skipped", "gone", ALREADY_GONE.format(rel=recorded)
        if is_z3:
            ctx["kept"].append(OUTPUT_KEPT.format(rel=recorded))
            return "skipped", "kept", OUTPUT_KEPT.format(rel=recorded)
        if not _writable(dst):
            return "skipped", "failed", FILE_OPEN.format(rel=recorded)
        _remove(vault, recorded, rt)
        ctx["removed"].append(recorded)
        return "done", None, None
    return "skipped", "gone", ""


def _stream_conflicted(vault, stream_items):
    led = items.Ledger(vault)
    return any(items.conflicts_of(led, it) or _other_machine_lines(vault, it) for it in stream_items)


def _other_machine_lines(vault, it):
    from kb._lib import streams
    st = it["stream"]
    return [r for r in streams.later_lines(vault, st["kind"], it["key"], st["seq"], st["fields"]) if r.get("by") != st["by"]]


def _undo_stream_item(ctx, it):
    """流水檔的項目改回：只改這一行改的那幾欄，寫一行反向的（原本那行留著）；新增的寫收回欄。"""
    from kb._lib import streams
    vault, rt = ctx["vault"], ctx["rt"]
    st = it["stream"]
    eff = streams.line_effects(vault, st["kind"], it["key"], st["seq"], st["by"])
    if eff is None or streams.current(vault, st["kind"], it["key"]) is None:
        return "skipped", "gone", f"{it['what']} 現在已經不在"
    if _YES and eff["op"] != "create":  # --yes：別人後來改過的那幾欄不動，其他欄照改回
        others = set()
        for r in _other_machine_lines(vault, it):
            others |= set(r.get("set") or {}) | set(r.get("unset") or [])
        eff = dict(eff, fields=[f for f in eff["fields"] if f not in others])
        if not eff["fields"]:
            return "skipped", "gone", f"{it['what']} 的欄位都被別人改過了，沒有可改回的"
    turn.backup(vault, it["file"], rt)
    streams.revert_line(vault, st["kind"], it["key"], eff, rt)
    done_meta = ctx.setdefault("meta_done", set())
    if (it["turn"], st["kind"]) not in done_meta:  # 同回合同種類的「版本、其他頂層欄」那一筆跟著改回（不列成項目）
        done_meta.add((it["turn"], st["kind"]))
        own = streams.own_file(vault, st["kind"]).stem
        for r in streams.turn_items(vault, it["turn"]):
            if r.get("kind") == st["kind"] and r.get("id") == streams.META_ID:
                meff = streams.line_effects(vault, st["kind"], streams.META_ID, r.get("seq"), own)
                if meff is not None:
                    streams.revert_line(vault, st["kind"], streams.META_ID, meff, rt)
    ctx["restored"].append(it["file"])
    ctx["render"].add(it["file"])
    return "done", None, None


def _undo_record_item(ctx, it):
    """JSON 條目換回舊值：新增的拿掉、拿掉的加回、改的換回。整檔在 FileLock 裡讀改寫，寫前備進動作回合。"""
    import copy
    if it.get("stream"):
        return _undo_stream_item(ctx, it)
    vault, rt = ctx["vault"], ctx["rt"]
    path, key = it["file"], it["key"]
    target = Path(vault) / path
    list_key = items.RECORD_FILES[path]
    with kio.FileLock(target):
        data = read_json(target, {}) or {}
        lst = data.get(list_key)
        if not isinstance(lst, list):
            return "skipped", "failed", f"{path} 的內容讀不懂"
        idx = next((i for i, r in enumerate(lst) if isinstance(r, dict) and str(r.get("id")) == key), None)
        if it["op"] == "set":
            if idx is None:
                return "skipped", "gone", f"{it['what']} 現在已經不在"
            lst[idx] = copy.deepcopy(it["old"])
        elif it["op"] == "add":
            if idx is None:
                return "skipped", "gone", f"{it['what']} 現在已經不在"
            del lst[idx]
        else:
            if idx is not None:
                return "done", None, None
            lst.insert(min(it["old_index"], len(lst)), copy.deepcopy(it["old"]))
        turn.backup(vault, path, rt)
        write_json_atomic(target, data)
    ctx["restored"].append(path)
    ctx["render"].add(path)
    return "done", None, None


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT  # CLI 不受 hook 的 10 秒限制，等久一點才不會留下半套（R2-L3）
    as_json = getattr(args, "json", False)
    if args.versions_command == "list":
        return _list(vault, args.n, as_json)
    if args.versions_command == "items":
        return _items(vault, args.turn_code, args.n, as_json)
    # 對話裡叫的（有 sid、還沒答完）在動作前判斷一次（D-085 補）：退回自己這句時會開一個帶 sid 的新回合接著那句，
    # 判斷要跟動作前一致；這個結果也決定做完要不要封住動作回合（_seal）
    conversation = journal_cli.in_conversation(vault)
    if args.versions_command == "clean":
        return _clean(vault, args.check, as_json, conversation)
    if args.versions_command == "undo":
        global _YES
        _YES = bool(getattr(args, "yes", False))
        return _undo(vault, args.targets, as_json, conversation)
    if args.versions_command == "delete-outputs":
        return _delete_outputs(vault, args.code, as_json, conversation)
    return _revert(vault, args.code, args.delete_outputs, as_json, conversation)
