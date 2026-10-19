"""kb profile check／apply（客戶設定檔與型別第 2 點，D-110～D-113）。

check：系統/客戶設定檔.md 跟 .system/profile.json 比，印差異與型別夾對帳，不寫任何檔；讀不懂 exit 3，其餘 0。
apply：check 的判斷之後，刪了有東西的型別夾那列擋（D-058）；沒有改不寫；否則先建缺的型別夾（只建不刪、建夾不備份；
建不成就整份不套用，測試後修正裁定 11）→ merge → 兩檔備進回合後寫（D-074）→ 表少了的型別、夾是空的就一起拿掉（確認後修正裁定 21）
→ 第三節拿掉的「不整理」種類把清冊的 skip_kind 拿掉（清冊先備份）→ 第五節關於你對實體清單（entities.sync_self，先備份）。
鎖 profile.json 30 秒。終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
import os

from kb import journal_cli
from kb._lib import entities, inventory, profile, turn
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
        why = f"夾裡還有 {nonempty[n]} 項，套用會被擋" if n in nonempty else "空夾會一起拿掉"
        lines.append(f"- 型別 少了 {n}（{why}）")
    lines += [f"- 型別 {n} 改了" for n in d["types"]["changed"]]
    for word, key in (("多了", "added"), ("少了", "removed"), ("改了", "changed")):
        lines += [f"- 卡型 {c} 的段落 {s} {word}" for c, s in d["card_types"][key]]
    new_ex = {e["kind"]: e for e in new["exclusions"]}
    old_ex = {e["kind"]: e for e in old["exclusions"]}
    lines += [f"- 不整理與敏感 多了 {k}（{acts.get(new_ex[k]['action'], '')}）" for k in d["exclusions"]["added"]]
    lines += [f"- 不整理與敏感 少了 {k}（{acts.get(old_ex[k].get('action'), '')}）" for k in d["exclusions"]["removed"]]
    lines += [f"- 不整理與敏感 {k} 改成 {acts.get(new_ex[k]['action'], '')}" for k in d["exclusions"]["changed"]]
    for word, key in (("多了", "added"), ("少了", "removed"), ("改了", "changed")):
        lines += [f"- 每類段落 {t} 的 {s} {word}" for t, s in d["synthesis_sections"][key]]
    kinds = {v: k for k, v in profile.KINDS.items()}
    new_about = {a["name"]: a for a in new["about"]}
    lines += [f"- 關於你 多了 {n}（{kinds.get(new_about[n]['kind'], '')}）" for n in d["about"]["added"]]
    lines += [f"- 關於你 少了 {n}" for n in d["about"]["removed"]]
    lines += [f"- 關於你 {n} 改了" for n in d["about"]["changed"]]
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
        return [f"客戶設定檔讀不懂：{st['error']}"], 3
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


def _remove_empty_dirs(vault, names):
    """表少了一列、夾是空的：寫完設定檔後一起拿掉（確認後修正裁定 21）；rmdir 失敗（剛好被佔用）就留著。回 (拿掉的, 留著的)。"""
    removed, kept = [], []
    for name in names:
        path = vault / name
        if not path.is_dir():
            continue
        try:
            os.rmdir(path)
        except OSError:
            kept.append(name)
            continue
        removed.append(name)
    return removed, kept


def _with_dirs(summary, removed, kept):
    """「型別 -1」後面接「（拿掉了空夾 X；Y 夾是空的，會留著）」。"""
    notes = (["拿掉了空夾 " + "、".join(removed)] if removed else []) + [f"{n} 夾是空的，會留著" for n in kept]
    if not notes or not summary.startswith("型別 "):
        return summary
    head, sep, rest = summary.partition("、")
    return f"{head}（{'；'.join(notes)}）{sep}{rest}"


def _apply_locked(vault, as_json, conversation):
    st = profile.status(vault)
    if st["state"] == "missing":
        _refuse(as_json, st, MISSING)
        return 3
    if st["state"] == "error":
        _refuse(as_json, st, f"客戶設定檔讀不懂：{st['error']}")
        return 3
    if st["state"] == "ok":  # 沒有內容差異也補跑一次關於你（冪等；上次寫完設定檔才失敗的會補上，測試後修正裁定 12）
        about = entities.sync_self(vault, st["data"].get("about") or [], turn.current(vault)["turn"])
        lines = _about_lines(about)
        if lines:
            journal_cli.record_program_or_warn(vault, lines[0], [{"path": entities.ENTITIES_POSIX, "action": "modify"}],
                                               "profile apply", conversation)
        if as_json:
            print(json.dumps(dict(_public(st), applied=False, created_dirs=[], unskipped=0, message=NOT_APPLIED,
                                  about=about), ensure_ascii=False))
        else:
            print("\n".join([NOT_APPLIED] + lines))
        return 0
    if st["removed_nonempty"]:
        name, n = st["removed_nonempty"][0]
        _refuse(as_json, st, NONEMPTY.format(name=name, n=n))
        return 3
    blocked = [n for n in st["to_create"] if (vault / n).exists()]
    if blocked:
        _refuse(as_json, st, f"「{blocked[0]}」已經有同名的檔，不能建成型別夾；先改名或搬走")
        return 3
    created = []
    for name in st["to_create"]:  # 先建夾再寫檔（測試後修正裁定 11）：建不成就整份不套用，已建的夾留著
        try:
            (vault / name).mkdir(exist_ok=True)
        except OSError as e:
            line = f"建不了資料夾「{name}」：{e.strerror or e}；設定檔沒有套用"
            _refuse(as_json, st, line)
            if created and not as_json:
                print("已建的資料夾留著：" + "、".join(created))
            return 3
        created.append(name)
    old, new = st["data"], st["new"]
    turn_id = turn.current(vault)["turn"]
    merged = profile.merge(old, new, first=old.get("applied") is None)
    data = dict(old, **merged, applied=profile.now())
    profile.write_locked(vault, data, turn_id)
    removed_dirs, kept_dirs = _remove_empty_dirs(vault, st["removed_empty"])
    unskipped = inventory.unskip_kinds(vault, profile.unskipped_kinds(old, merged), turn_id)
    about = entities.sync_self(vault, merged["about"], turn_id)
    about_lines = _about_lines(about)
    summary = _with_dirs(profile.summary_short(st["diff"]), removed_dirs, kept_dirs)
    st["unlisted"] = [n for n in st["unlisted"] if n not in removed_dirs]
    parts = [summary] if summary else []
    if created:
        parts.append("建了資料夾 " + "、".join(created))
    if unskipped:
        parts.append(f"{len(unskipped)} 份回到待製卡")
    body = "；".join(parts) if summary else "沒有內容上的差異，重寫了設定檔" + "".join("；" + p for p in parts)
    sentence = f"已套用客戶設定檔：{body}"
    changed = CHANGED + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    if about_lines:
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    journal_cli.record_program_or_warn(vault, sentence[1:], changed, "profile apply", conversation)
    if as_json:
        print(json.dumps(dict(_public(st), applied=True, created_dirs=created, removed_dirs=removed_dirs,
                              unskipped=len(unskipped), about=about), ensure_ascii=False))
        return 0
    print(sentence)
    for line in about_lines + _unlisted_line(st):
        print(line)
    return 0


def _about_lines(about):
    """第五節對實體清單動到的（實體與綜合卡第 10 點）：登記了誰、誰改記成你自己、誰不再記成你自己。"""
    lines = [f"關於你：登記了 {'、'.join(about['added'])}"] if about["added"] else []
    lines += [f"關於你：{note}" for note in about["notes"]]
    if about["released"]:
        lines.append(f"關於你：{'、'.join(about['released'])} 不再記成你自己")
    return lines


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
