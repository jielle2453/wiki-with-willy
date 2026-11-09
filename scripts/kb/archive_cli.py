"""kb archive <目標>… [--reason "<使用者的話>"]、kb archive --undo <目標>…（放寬丙 12，D-332）。

使用者說某些檔或某個實體過時了、收起來、不往來：清冊條目或實體清單記 archived（{"date","reason","turn"}），記進回合（退得回）；
檔案卡檔頭的「封存: YYYY-MM-DD」照清冊補（--undo 拿掉）。封存之後 kb cards todo 不列它重做、不算份數、不從它抽日期，kb asks、待處理、健檢不出跟它有關的題；
kb find、kb lookup、kb cards list 照樣找得到，結果帶 archived: true、排在沒封存的後面；綜合卡「相關檔」把它放進「其他」框；
總目錄「摘要」把封存的實體收進最後的摺疊框「已封存」。實體封存不改事類的結案狀態。
目標可以是清冊編號、file:// uri、實體名稱（別名也認）或檔案路徑；一個目標不行整句不動（先全部認出來才寫）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

from kb import journal_cli
from kb._lib import archive, cards, entities, inventory, pending, statements, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault, name_of

CLI_LOCK_TIMEOUT = 30
REASON_LABEL = "原因"


def add_arguments(sub):
    sub.add_argument("targets", nargs="+", help="清冊編號（f-…）、實體名稱、檔案路徑或 file:// 位置，可以多個")
    sub.add_argument("--reason", help="使用者的話（為什麼封存，例：舊價目表）")
    sub.add_argument("--undo", action="store_true", help="拿掉封存（使用者說還要用）")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


class _Refused(Exception):
    pass


def _label(kind, thing):
    return thing["name"] if kind == "entity" else name_of(thing["locations"][0]["uri"]).rpartition("/")[2]


def _reason(text):
    if not text:
        return ""
    try:
        return statements.check_text(text, REASON_LABEL, _Refused)
    except _Refused as e:
        raise archive.ArchiveRefused(str(e)) from None


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        sources = sources_lib.load(vault)
        found, seen = [], set()
        for target in args.targets:
            kind, thing = archive.resolve(vault, sources, target, os.getcwd())
            key = (kind, thing.get("id"))
            if key not in seen:
                seen.add(key)
                found.append((kind, thing))
        reason = _reason(args.reason)
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        info = None if args.undo else {"date": date.today().isoformat(), "reason": reason, "turn": turn_id}
        done, same, changed, touched = [], [], [], []
        file_ids = [t["id"] for k, t in found if k == "file"]
        if file_ids:
            groups = cards._scan_groups(vault)[0]
            hit = set(inventory.set_archived(vault, file_ids, info, turn_id))
            if hit:
                changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
                changed += cards.sync_fronts(vault, turn_id, hit)
                touched += cards.entity_names_of([c for fid in hit for c in (groups.get(fid) or [])[:1]])
        else:
            hit = set()
        for kind, thing in found:
            if kind == "file":
                (done if thing["id"] in hit else same).append((kind, thing))
            else:
                _e, wrote = entities.set_archived(vault, thing["id"], info, turn_id)
                if wrote:
                    changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
                    touched.append(thing["name"])
                (done if wrote else same).append((kind, thing))
        if done:
            changed += [{"path": rel, "action": "modify"} for rel in cards.refresh_synthesis(vault, touched, turn_id)]
            cards.render_index(vault, turn_id)
            changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
            if (Path(vault) / pending.MD_POSIX).exists():  # 待處理頁換成現在不列封存的樣子
                pending.refresh(vault, turn_id)
                changed.append({"path": pending.MD_POSIX, "action": "modify"})
            names = "、".join(_label(k, t) for k, t in done)
            journal_cli.record_program_or_warn(vault, f"{'拿掉封存' if args.undo else '封存'}：{names}", changed, "archive",
                                               conversation)
    except archive.ArchiveRefused as e:
        print(str(e))
        return 3
    except entities.EntityRefused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print("等不到鎖（別的指令正在改），沒有寫入；請稍後再試")
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
    if as_json:
        row = lambda k, t: {"kind": k, "id": t["id"], "name": _label(k, t)}  # noqa: E731
        print(json.dumps({"undo": bool(args.undo), "done": [row(k, t) for k, t in done],
                          "unchanged": [row(k, t) for k, t in same]}, ensure_ascii=False))
        return 0
    verb = "拿掉封存" if args.undo else "封存"
    if done:
        print(f"已{verb}：" + "、".join(_label(k, t) for k, t in done) + "；說「退回上一版」救得回來")
    if same:
        print(("本來就沒封存：" if args.undo else "本來就封存了：") + "、".join(_label(k, t) for k, t in same))
    return 0
