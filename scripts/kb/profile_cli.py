"""kb profile check／apply（客戶設定檔與型別第 2 點，D-110～D-113）。

check：系統/客戶設定檔.md 跟 .system/profile.json 比，印差異與型別夾對帳，不寫任何檔；讀不懂 exit 3，其餘 0。
apply：check 的判斷之後，刪了有東西的型別夾那列擋（D-058）；沒有改不寫；否則 merge → 兩檔備進回合後寫（D-074）→
建缺的型別夾（只建不刪、建夾不備份）→ 第三節拿掉的「不整理」種類把清冊的 skip_kind 拿掉（清冊先備份）。
鎖 profile.json 30 秒。終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json

from kb import journal_cli
from kb._lib import inventory, profile, turn
from kb._lib import io as kio
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
MISSING = "還沒有客戶設定檔，跑 kb init 補"
LOCK_BUSY = "等不到鎖，沒有寫入"
NO_CHANGE = "客戶設定檔沒有改"
NOT_APPLIED = "客戶設定檔沒有改，沒有套用"
NO_CONTENT_DIFF = "客戶設定檔有改，但沒有內容上的差異"
NONEMPTY = "型別「{name}」底下還有 {n} 項，先搬走再拿掉這列"
UNLISTED = "第一層夾不在型別表：{names}（請在設定檔第一節補一列）"
CHANGED = [{"path": profile.PROFILE_POSIX, "action": "modify"}, {"path": profile.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="profile_command", metavar="<子子命令>", required=True)
    ck = subs.add_parser("check", help="看客戶設定檔改了什麼", description="設定檔 md 跟 JSON 比，印差異與型別夾對帳；不寫檔。")
    ap = subs.add_parser("apply", help="套用客戶設定檔", description="把改過的設定檔套進 JSON、建缺的型別夾；寫前先備份。")
    for p in (ck, ap):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _detail_lines(st):
    d, cats = st["diff"], {v: k for k, v in profile.CATEGORIES.items()}
    acts = {v: k for k, v in profile.ACTIONS.items()}
    new = st["new"]
    old = st["data"]
    new_types = {t["name"]: t for t in new["types"]}
    nonempty = dict(st["removed_nonempty"])
    lines = [f"- 型別 多了 {n}（{cats.get(new_types[n]['category'], '')}）" for n in d["types"]["added"]]
    for n in d["types"]["removed"]:
        why = f"夾裡還有 {nonempty[n]} 項，套用會被擋" if n in nonempty else "夾是空的，會留著"
        lines.append(f"- 型別 少了 {n}（{why}）")
    lines += [f"- 型別 {n} 改了" for n in d["types"]["changed"]]
    for word, key in (("多了", "added"), ("少了", "removed"), ("改了", "changed")):
        lines += [f"- 卡型 {c} 的段落 {s} {word}" for c, s in d["card_types"][key]]
    new_ex = {e["kind"]: e for e in new["exclusions"]}
    old_ex = {e["kind"]: e for e in old["exclusions"]}
    lines += [f"- 不整理與敏感 多了 {k}（{acts.get(new_ex[k]['action'], '')}）" for k in d["exclusions"]["added"]]
    lines += [f"- 不整理與敏感 少了 {k}（{acts.get(old_ex[k].get('action'), '')}）" for k in d["exclusions"]["removed"]]
    lines += [f"- 不整理與敏感 {k} 改成 {acts.get(new_ex[k]['action'], '')}" for k in d["exclusions"]["changed"]]
    return lines


def _unlisted_line(st):
    return [UNLISTED.format(names="、".join(st["unlisted"]))] if st["unlisted"] else []


def _public(st):
    return {"state": st["state"], "diff": st["diff"], "error": st["error"], "unlisted": st["unlisted"],
            "removed_empty": st["removed_empty"], "removed_nonempty": [list(x) for x in st["removed_nonempty"]],
            "to_create": st["to_create"]}


def check_lines(st):
    """回 (行, exit code)。"""
    if st["state"] == "missing":
        return [MISSING], 0
    if st["state"] == "error":
        return [f"客戶設定檔讀不懂：{st['error']}"] + _unlisted_line(st), 3
    if st["state"] == "ok":
        return [NO_CHANGE] + _unlisted_line(st), 0
    if not profile.has_diff(st["diff"]):
        return [NO_CONTENT_DIFF] + _unlisted_line(st), 0
    return [f"客戶設定檔有改：{profile.summary_full(st['diff'])}"] + _detail_lines(st) + _unlisted_line(st), 0


def _check(vault, as_json):
    st = profile.status(vault)
    lines, code = check_lines(st)
    if as_json:
        print(json.dumps(_public(st), ensure_ascii=False))
    else:
        print("\n".join(lines))
    return code


def _refuse(as_json, st, line):
    if as_json:
        print(json.dumps(dict(_public(st), applied=False, created_dirs=[], unskipped=0, message=line), ensure_ascii=False))
    else:
        print(line)


def _apply_locked(vault, as_json, conversation):
    st = profile.status(vault)
    if st["state"] == "missing":
        _refuse(as_json, st, MISSING)
        return 3
    if st["state"] == "error":
        _refuse(as_json, st, f"客戶設定檔讀不懂：{st['error']}")
        return 3
    if st["state"] == "ok":
        _refuse(as_json, st, NOT_APPLIED)
        return 0
    if st["removed_nonempty"]:
        name, n = st["removed_nonempty"][0]
        _refuse(as_json, st, NONEMPTY.format(name=name, n=n))
        return 3
    blocked = [n for n in st["to_create"] if (vault / n).exists()]
    if blocked:
        _refuse(as_json, st, f"「{blocked[0]}」已經有同名的檔，不能建成型別夾；先改名或搬走")
        return 3
    old, new = st["data"], st["new"]
    turn_id = turn.current(vault)["turn"]
    merged = profile.merge(old, new, first=old.get("applied") is None)
    data = dict(old, **merged, applied=profile.now())
    profile.write_locked(vault, data, turn_id)
    created = []
    for name in st["to_create"]:
        (vault / name).mkdir(exist_ok=True)
        created.append(name)
    unskipped = inventory.unskip_kinds(vault, profile.unskipped_kinds(old, merged), turn_id)
    summary = profile.summary_short(st["diff"])
    parts = [summary] if summary else []
    if created:
        parts.append("建了資料夾 " + "、".join(created))
    if unskipped:
        parts.append(f"{len(unskipped)} 份回到待製卡")
    body = "；".join(parts) if summary else "沒有內容上的差異，重寫了設定檔" + "".join("；" + p for p in parts)
    sentence = f"已套用客戶設定檔：{body}"
    changed = CHANGED + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    journal_cli.record_program_or_warn(vault, sentence[1:], changed, "profile apply", conversation)
    if as_json:
        print(json.dumps(dict(_public(st), applied=True, created_dirs=created, unskipped=len(unskipped)),
                         ensure_ascii=False))
        return 0
    print(sentence)
    for line in _unlisted_line(st):
        print(line)
    return 0


def _apply(vault, as_json, conversation):
    with profile.lock(vault):
        return _apply_locked(vault, as_json, conversation)


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        if args.profile_command == "check":
            return _check(vault, as_json)
        return _apply(vault, as_json, journal_cli.in_conversation(vault))
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except ValueError as e:  # profile.json 或清冊壞了
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
