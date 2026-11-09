"""kb unlock save／get／forget／list／authorize／revoke（密碼與守門第 2 點；D-180、D-181、D-050）：本體在 _lib/unlock。

密碼存這台電腦的 Windows 憑證管理員，知識庫裡不留任何密碼；值只在 get 的那一行（與 get --json）出現，不進任何檔、不進錯誤訊息。
總資料夾只從 cwd 往上找，不收 --vault（同 kb convert 裁定 15：會把密碼帶給 AI 的子命令不准由參數指定總資料夾）。
成功句與拒絕句一字不差，拒絕 exit 3，人讀輸出不印編號。對話裡跑 get 時回合記 secret_shown（Stop 不記那句回答）。
終端機跑的 save、forget、authorize、revoke 記一段工作日誌程式段（不帶值），對話裡叫的併進回合；get、list 只讀不記。
"""
import argparse
import json
import os

from kb import guard, journal_cli
from kb._lib import io as kio
from kb._lib import turn, unlock

CLI_LOCK_TIMEOUT = 30
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行"
LOCK_BUSY = "等不到鎖，沒有寫入"
SAVED = "記好了：{name}（{kind}），存在這台電腦的憑證管理員"
REPLACED = "換成新的了：{name}"
FORGOT = "忘掉了：{name}"
FORGOT_KINDS = "；{labels}那一種也不會再自動開"
AUTHORIZED = "以後{label}這一種的 PDF，會用「{name}」那組密碼開，開了會跟你說"
REVOKED = "不再自動開：{label}這一種"
EMPTY = "這台電腦沒有記任何密碼"
UNLOCK_FILE = [{"path": unlock.UNLOCK_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="unlock_command", metavar="<子子命令>", required=True)
    sv = subs.add_parser("save", help="存一組密碼", description="存進這台電腦的憑證管理員；同名就換掉。")
    sv.add_argument("--kind", required=True, help="種類：記下的密碼、PDF 密碼")
    sv.add_argument("--name", required=True, help="名稱（客戶怎麼叫它）")
    sv.add_argument("--value", required=True, help="密碼（用引號包起來）")
    gt = subs.add_parser("get", help="取出一組密碼", description="印「名稱：密碼」；對話裡跑時這句回答不記進工作日誌。")
    gt.add_argument("--name", required=True, help="名稱")
    fg = subs.add_parser("forget", help="忘掉一組密碼", description="從憑證管理員刪掉，連用它自動開的種類一起拿掉。")
    fg.add_argument("--name", required=True, help="名稱")
    ls = subs.add_parser("list", help="列出記了哪些", description="只列名稱與種類，不列密碼；接著列會自動開的種類。")
    au = subs.add_parser("authorize", help="這一種的 PDF 用這組密碼開",
                         description="客戶說「保險單都用這組密碼開」：記一種，之後同一種的新 PDF 由 AI 判斷後直接開。")
    au.add_argument("--name", required=True, help="PDF 密碼的名稱")
    au.add_argument("--label", required=True, help="客戶怎麼稱呼這一種（例：保險單）")
    au.add_argument("--example", action="append", help="這一種的例子：清冊編號、uri 或路徑，可以重複")
    rv = subs.add_parser("revoke", help="這一種不再自動開", description="拿掉一個授權過的種類。")
    rv.add_argument("--label", required=True, help="種類名稱")
    for p in (sv, gt, fg, ls, au, rv):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else "\n".join(lines))


def _save(vault, args, as_json, conversation, turn_id):
    name = unlock.check_name(args.name)
    replaced = unlock.save(vault, args.kind, name, args.value)
    kind = unlock._kind(args.kind)
    journal_cli.record_program_or_warn(vault, f"存密碼：{name}（{kind}）", [], "unlock save", conversation)
    line = REPLACED.format(name=name) if replaced else SAVED.format(name=name, kind=kind)
    _out(as_json, [line], {"name": name, "kind": kind, "replaced": replaced})
    return 0


def _get(vault, args, as_json, conversation, turn_id):
    name = unlock.check_name(args.name)
    kind, value = unlock.get(vault, name)
    if conversation:
        try:
            turn.mark(vault, "secret_shown")  # Stop 寫結果時只記固定句（釐清「答密碼時工作日誌不記答案」）
        except TimeoutError:
            pass
    _out(as_json, [f"{name}：{value}"], {"name": name, "kind": kind, "value": value})
    return 0


def _forget(vault, args, as_json, conversation, turn_id):
    name = unlock.check_name(args.name)
    labels = unlock.forget(vault, name, turn_id)
    journal_cli.record_program_or_warn(vault, f"忘掉密碼：{name}", UNLOCK_FILE if labels else [], "unlock forget", conversation)
    line = FORGOT.format(name=name) + (FORGOT_KINDS.format(labels="、".join(labels)) if labels else "")
    _out(as_json, [line], {"name": name, "kinds_removed": labels})
    return 0


def _list(vault, args, as_json, conversation, turn_id):
    items = unlock.entries(vault)
    try:
        kinds = unlock.load(vault)["kinds"]
    except ValueError:
        kinds = []
    if as_json:
        _out(True, [], {"saved": [{"kind": k, "name": n} for k, n in items],
                        "kinds": [{"label": k.get("label"), "credential": k.get("credential")} for k in kinds]})
        return 0
    lines = [f"- {n}（{k}）" for k, n in items] or [EMPTY]
    lines += [f"- 會自動開：{k.get('label')}這一種（用「{str(k.get('credential', '')).partition('/')[2]}」那組）" for k in kinds]
    _out(False, lines, {})
    return 0


def _authorize(vault, args, as_json, conversation, turn_id):
    name = unlock.check_name(args.name)
    item = unlock.authorize(vault, name, args.label, [e.strip() for e in args.example or []], turn_id)
    journal_cli.record_program_or_warn(vault, f"授權自動開：{item['label']}這一種（{name}）", UNLOCK_FILE, "unlock authorize",
                                       conversation)
    _out(as_json, [AUTHORIZED.format(label=item["label"], name=name)], {"kind": item})
    return 0


def _revoke(vault, args, as_json, conversation, turn_id):
    label = unlock.revoke(vault, str(args.label or "").strip(), turn_id)
    journal_cli.record_program_or_warn(vault, f"不再自動開：{label}這一種", UNLOCK_FILE, "unlock revoke", conversation)
    _out(as_json, [REVOKED.format(label=label)], {"label": label})
    return 0


HANDLERS = {"save": _save, "get": _get, "forget": _forget, "list": _list, "authorize": _authorize, "revoke": _revoke}


def run(args):
    as_json = getattr(args, "json", False)
    found = guard.find_vault(os.getcwd())  # 只信 cwd 所在的知識庫
    if found is None:
        print(NOT_IN_VAULT)
        return 3
    vault = found
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    try:
        conversation = journal_cli.in_conversation(vault)
        turn_id = turn.current(vault)["turn"]
        return HANDLERS[args.unlock_command](vault, args, as_json, conversation, turn_id)
    except unlock.Refused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}")
        return 3
