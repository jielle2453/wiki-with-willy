"""kb versions list／revert／delete-outputs：列最近的回合、退回最近一次沒退過的回合、刪已退回回合留著的成品（D-074、D-011）。

退回只從最近一次開始，不跳；退回前先把現況備進「退回」這句話自己的回合，退錯可再退。
成品（清冊 created_turn 是那回合且 manifest 記 create 的）預設留著、status 改 turn_reverted，revert 只列不問；
客戶明說刪才 delete-outputs（或一開始就 revert --delete-outputs）。退回動完後對碰到的 Z3 路徑對帳清冊 status。
退回標記是回合夾裡的 reverted.json，不改夾名；退回動作的回合在 turn.json 記 reverted_turn，退掉它時撤原回合的標記（第二輪 R2-4）。
刪成品前三道關：這一句話已經改過的不刪（R2-2）、指紋跟清冊不同的不刪（R2-3）、檔已不在只改 status（R2-L1）。
CLI 的鎖等 30 秒（hook 維持 3，第二輪 R2-L3）。
revert、delete-outputs 成功後記一段工作日誌程式段（D-085），note 是「已退回 …」「已刪 N 份成品（回合 …）」那句；刪 0 份不記。
"""
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import credentials, inventory, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.io import read_json, write_json_atomic

ENTRY_LINES_MAX = 15
CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"
TOUCHED_THIS_TURN = "{rel} 這一句話已經改過，下一句再刪"
CHANGED_SINCE = "{rel} 之後被改過，留著"
ALREADY_GONE = "{rel} 已經不在"


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
        })
    return out


def _counts(vault, t):
    n = {k: sum(1 for e in t["entries"] if e.get("action") == k) for k in ("modify", "create", "delete")}
    n["outputs"] = sum(1 for e in inventory.load(vault)["entries"] if e.get("created_turn") == t["turn"])
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
        } for t in shown], ensure_ascii=False))
        return 0
    if not all_turns:
        print("還沒有任何改動紀錄")
        return 0
    for t in shown:
        c = _counts(vault, t)
        flags = ("（已退回）" if t["reverted"] else "") + ("（這一次）" if t["turn"] == current else "")
        print(f"{t['turn']}{flags}｜{_fmt_time(t['started'])}｜問：「{t['prompt_head']}」｜"
              f"修改 {c['modify']}、新增 {c['create']}、刪除 {c['delete']}、成品 {c['outputs']}")
        for e in t["entries"][:ENTRY_LINES_MAX]:
            print(f"    {e.get('action')}：{e.get('path')}")
        if len(t["entries"]) > ENTRY_LINES_MAX:
            print(f"    ……共 {len(t['entries'])} 檔")
    return 0


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
    return [loc[len("file://"):] for o in entries_ for loc in o.get("locations", []) if loc.startswith("file://")]


def _rel_of(entry):
    rels = _rels_of([entry])
    return rels[0] if rels else None


def _acting_turn(vault, target_turn):
    """退回動作本身的回合；若等於要退的回合，開新回合並成為目前回合（工作日誌第二輪 R2-M2、M3）：
    - 目前回合 sid 空或已答完（done true）：turn.start(None)，只換 current.json，之後的寫入不會記進已退回的回合。
    - 對話裡、還沒答完（sid 有、done false）：turn.start(sid, inherit=目前回合)，新回合繼承問與 note、記 journal_turn，
      換掉 turns/<sid>.json 與 current.json；Stop 收原回合那一行。連續退回每次再開一個，journal_turn 一路繼承。"""
    rt = turn.current(vault)
    if rt["turn"] == target_turn:
        if rt.get("session_id") and not rt.get("done"):
            rt = turn.start(vault, rt["session_id"], f"退回 {target_turn}", inherit=rt)
        else:
            rt = turn.start(vault, None, f"退回 {target_turn}")
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
        location = "file://" + rel
        same = [e for e in data["entries"] if location in e.get("locations", [])]
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


def _revert(vault, code, delete_outputs, as_json, conversation=False):
    all_turns = turns(vault)
    cands = [t for t in all_turns if not t["reverted"] and (t["entries"] or t["reverted_turn"])]
    if not cands:
        print("沒有可以退回的改動")
        return 3
    target = cands[0]
    if code and code != target["turn"]:
        print(f"只能從最近一次開始退：{target['turn']}")
        return 3
    rt = _acting_turn(vault, target["turn"])
    touched_before = _touched_paths(vault, rt["turn"])  # 這一句話在退回前就動過的路徑（R2-2）
    sources = sources_lib.load(vault)
    # 成品只算「清冊 created_turn 是這回合 且 這回合 manifest 記 create」的（第一輪 M4）
    created = {e.get("path") for e in target["entries"] if e.get("action") == "create"}
    outputs = [o for o in inventory.outputs_of_turn(vault, target["turn"]) if _rel_of(o) in created]
    out_rels = _rels_of(outputs)

    restored, removed, lost, touched_z3, kept_msgs, kept_ids = [], [], [], [], [], []
    for e in reversed(target["entries"]):
        rel, action = e.get("path"), e.get("action")
        dst = Path(vault) / rel
        is_z3 = turn.zone_of(vault, rel, sources) == "Z3"
        if action in ("modify", "delete"):
            src = target["dir"] / "files" / rel
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

    if outputs:
        if delete_outputs:
            gone = [o["id"] for o in outputs if o["id"] not in kept_ids]
            if gone:
                inventory.set_status(vault, gone, "deleted")
            if kept_ids:
                inventory.set_status(vault, kept_ids, "turn_reverted")
        else:
            inventory.set_status(vault, [o["id"] for o in outputs], "turn_reverted")
    _reconcile(vault, touched_z3)
    if target["reverted_turn"]:
        _undo_revert_marks(vault, target["reverted_turn"])
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
            "undone_revert_of": target["reverted_turn"],
        }, ensure_ascii=False))
        return 0
    if lost:
        print(f"無法還原 {len(lost)} 檔（備份已不在）：" + "、".join(lost))
    print(sentence)
    for rel in out_rels:
        print(f"    {rel}")
    for msg in kept_msgs:
        print(msg)
    if outputs and not delete_outputs:
        print(f"這 {len(outputs)} 份成品先留著；要刪跟我說，我用 delete-outputs 刪。")
    if target["reverted_turn"]:
        print(f"這一次退的是「退回 {target['reverted_turn']}」本身，{target['reverted_turn']} 不再算已退回。")
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
    for o in outs:
        rel = _rel_of(o)
        if rel is None:
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
