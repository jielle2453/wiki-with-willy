"""kb versions list／revert／delete-outputs：列最近的回合、退回最近一次沒退過的回合、刪已退回回合留著的成品（D-074、D-011）。

退回只從最近一次開始，不跳；退回前先把現況備進「退回」這句話自己的回合，退錯可再退。
成品（清冊 created_turn 是那回合且 manifest 記 create 的）預設留著、status 改 turn_reverted，revert 只列不問；
客戶明說刪才 delete-outputs（或一開始就 revert --delete-outputs）。退回動完後對碰到的 Z3 路徑對帳清冊 status。
退回標記是回合夾裡的 reverted.json，不改夾名。
"""
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from kb import guard
from kb._lib import inventory, turn
from kb._lib import sources as sources_lib
from kb._lib.io import read_json, write_json_atomic

ENTRY_LINES_MAX = 15
NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"


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
    """由新到舊：{turn, dir, started, prompt_head, reverted, entries}。"""
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
            "reverted": t["reverted"], "current": t["turn"] == current,
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


def _remove(vault, rel, revert_turn):
    """刪檔前先備進退回動作的回合（action delete），再退一次救得回（D-074、D-011）。"""
    turn.backup(vault, rel, revert_turn, action="delete")
    os.remove(Path(vault) / rel)
    _prune_dirs(vault, rel)


def _rels_of(entries_):
    return [loc[len("file://"):] for o in entries_ for loc in o.get("locations", []) if loc.startswith("file://")]


def _acting_turn(vault, target_turn):
    """退回動作本身的回合；若等於要退的回合，開一個新的（現況要有別的回合可備）。"""
    rt = turn.current(vault)
    if rt["turn"] == target_turn:
        rt = turn.start(vault, rt.get("session_id") or None, f"退回 {target_turn}")
    return rt


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


def _revert(vault, code, delete_outputs, as_json):
    all_turns = turns(vault)
    cands = [t for t in all_turns if not t["reverted"] and t["entries"]]
    if not cands:
        print("沒有可以退回的改動")
        return 3
    target = cands[0]
    if code and code != target["turn"]:
        print(f"只能從最近一次開始退：{target['turn']}")
        return 3
    rt = _acting_turn(vault, target["turn"])
    sources = sources_lib.load(vault)
    # 成品只算「清冊 created_turn 是這回合 且 這回合 manifest 記 create」的（第一輪 M4）
    created = {e.get("path") for e in target["entries"] if e.get("action") == "create"}
    outputs = [o for o in inventory.outputs_of_turn(vault, target["turn"]) if any(r in created for r in _rels_of([o]))]
    out_rels = _rels_of(outputs)

    restored, removed, lost, touched_z3 = [], [], [], []
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
            _remove(vault, rel, rt["turn"])
            removed.append(rel)
            if is_z3:
                touched_z3.append(rel)

    if outputs:
        if delete_outputs:
            for rel in out_rels:
                if (Path(vault) / rel).is_file():
                    _remove(vault, rel, rt["turn"])
                    removed.append(rel)
            inventory.set_status(vault, [o["id"] for o in outputs], "deleted")
        else:
            inventory.set_status(vault, [o["id"] for o in outputs], "turn_reverted")
    _reconcile(vault, [r for r in touched_z3 if delete_outputs or r not in out_rels])

    write_json_atomic(target["dir"] / "reverted.json",
                      {"time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "by_turn": rt["turn"]})

    if as_json:
        print(json.dumps({
            "turn": target["turn"], "prompt_head": target["prompt_head"], "by_turn": rt["turn"],
            "restored": restored, "removed": removed, "lost": lost,
            "outputs": out_rels, "outputs_deleted": bool(delete_outputs),
        }, ensure_ascii=False))
        return 0
    if lost:
        print(f"無法還原 {len(lost)} 檔（備份已不在）：" + "、".join(lost))
    tail = ("刪了" if delete_outputs else "留著") if outputs else ""
    print(f"已退回 {target['turn']}（問：「{target['prompt_head']}」）："
          f"還原 {len(restored)}、移除 {len(removed)}、成品 {len(outputs)} 份{tail}")
    for rel in out_rels:
        print(f"    {rel}")
    if outputs and not delete_outputs:
        print(f"這 {len(outputs)} 份成品先留著；要刪跟我說，我用 delete-outputs 刪。")
    return 0


def _delete_outputs(vault, code, as_json):
    """第一輪 H1：「退回 → 客戶說刪 → 刪」的第二步。只刪已退回回合裡 status 還是 turn_reverted 的成品。"""
    entries_ = inventory.load(vault)["entries"]
    cands = []
    for t in turns(vault):
        if not t["reverted"]:
            continue
        outs = [e for e in entries_ if e.get("created_turn") == t["turn"] and e.get("status") == "turn_reverted"]
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
    deleted = []
    for rel in _rels_of(outs):
        if (Path(vault) / rel).is_file():
            _remove(vault, rel, rt["turn"])
        deleted.append(rel)
    inventory.set_status(vault, [o["id"] for o in outs], "deleted")
    if as_json:
        print(json.dumps({"turn": target["turn"], "by_turn": rt["turn"], "deleted": deleted}, ensure_ascii=False))
        return 0
    print(f"已刪 {len(outs)} 份成品（回合 {target['turn']}）：")
    for rel in deleted:
        print(f"    {rel}")
    return 0


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    as_json = getattr(args, "json", False)
    if args.versions_command == "list":
        return _list(vault, args.n, as_json)
    if args.versions_command == "delete-outputs":
        return _delete_outputs(vault, args.code, as_json)
    return _revert(vault, args.code, args.delete_outputs, as_json)
