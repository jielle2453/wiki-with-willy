"""kb versions list／revert：列最近的回合，或退回最近一次沒退過的回合（D-074、D-011）。

退回只從最近一次開始，不跳；退回前先把現況備進「退回」這句話自己的回合，退錯可再退。
成品（inventory 裡 created_turn 是那回合的）預設留著、status 改 turn_reverted，客戶明說刪才加 --delete-outputs。
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


def _zone_of(vault, rel):
    from kb._lib import sources as sources_lib
    from kb._lib.paths import normalize
    zone, _alias = guard.classify(normalize(str(Path(vault) / rel), None), normalize(str(vault), None),
                                  sources_lib.load(vault))
    return zone


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
    turn.backup(vault, rel, revert_turn, action="delete")
    os.remove(Path(vault) / rel)
    _prune_dirs(vault, rel)


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
    rt = turn.current(vault)
    if rt["turn"] == target["turn"]:
        rt = turn.start(vault, rt.get("session_id") or None, f"退回 {target['turn']}")  # 現況要有別的回合可備
    outputs = inventory.outputs_of_turn(vault, target["turn"])
    out_rels = [loc[len("file://"):] for o in outputs for loc in o.get("locations", []) if loc.startswith("file://")]

    restored, removed, lost = [], [], []
    for e in reversed(target["entries"]):
        rel, action = e.get("path"), e.get("action")
        dst = Path(vault) / rel
        if action in ("modify", "delete"):
            src = target["dir"] / "files" / rel
            if not src.is_file():
                lost.append(rel)
                continue
            turn.backup(vault, rel, rt["turn"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)
        elif action == "create" and dst.is_file():
            if _zone_of(vault, rel) == "Z3" and not delete_outputs:
                continue  # 成品照 D-011：沒明說就留著
            _remove(vault, rel, rt["turn"])
            removed.append(rel)

    if outputs and not as_json:
        print(f"這一回合有 {len(outputs)} 份成品：")
        for rel in out_rels:
            print(f"    {rel}")
    if outputs:
        if delete_outputs:
            for rel in out_rels:
                if (Path(vault) / rel).is_file():
                    _remove(vault, rel, rt["turn"])
            inventory.set_status(vault, [o["id"] for o in outputs], "deleted")
        else:
            inventory.set_status(vault, [o["id"] for o in outputs], "turn_reverted")
            if not as_json:
                print(f"這 {len(outputs)} 份會刪掉，確定嗎？沒有 --delete-outputs 就留著。")

    write_json_atomic(target["dir"] / "reverted.json",
                      {"time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "by_turn": rt["turn"]})

    tail = "刪了" if delete_outputs else "留著"
    summary = (f"已退回 {target['turn']}（問：「{target['prompt_head']}」）："
               f"還原 {len(restored)}、移除 {len(removed)}、成品 {len(outputs)} 份{tail if outputs else ''}")
    if as_json:
        print(json.dumps({
            "turn": target["turn"], "prompt_head": target["prompt_head"], "by_turn": rt["turn"],
            "restored": restored, "removed": removed, "lost": lost,
            "outputs": out_rels, "outputs_deleted": bool(delete_outputs),
        }, ensure_ascii=False))
        return 0
    if lost:
        print(f"無法還原 {len(lost)} 檔（備份已不在）：" + "、".join(lost))
    print(summary)
    return 0


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    as_json = getattr(args, "json", False)
    if args.versions_command == "list":
        return _list(vault, args.n, as_json)
    return _revert(vault, args.code, args.delete_outputs, as_json)
