"""kb web on／off／ensure／status／serve／public／token／access [--vault] [--json]（網頁小伺服器，#74，D-350、D-356、D-357、D-359）：
本體在 _lib/web，手機那幾個在 _lib/web_access。

on：寫這台的 .system/local/web.json（{"on": true, "port": N}；沒給 --port 用原本的，再沒有用 47831）再跑 ensure，回網址。
off：寫 on false、刪排程；伺服器在下一輪檢查（60 秒內）自己退出。
ensure：開關 on → 排程不在或不對就重設；off → 有就刪。回 created／updated／ok／off／unsupported／failed（failed exit 3、記待處理）。
status：開關、連接埠、網址、排程（ok／missing／stale／unsupported）、伺服器有沒有在回（GET /api/status 2 秒，回 pid 與版本）、
對話用的 claude 找到的路徑（沒有是 null，網頁對話第 7 點）。
serve：前景跑小伺服器（排程跑的就是這個）；沒給 --port 用 web.json 的。--vault 不是知識庫（刪了、搬了）照 --task 刪自己的排程、exit 0；
人手動跑（沒有 --task）不在知識庫裡或開關關著說一句、exit 3。
public：--host <網址> --team <team 網域> --aud <AUD> [--account --app --policy]，寫進 web.json（建置時）；--host "" 拿掉對外網址（關對外）。
token set：從 stdin 讀 Cloudflare 權杖（不收參數，免得進指令紀錄；終端機上不顯示打的字）存進認證管理員；token forget 刪掉。守門兩個都問。
access list：列 policy 的 include 裡的信箱；access add|remove <信箱>：GET 整份、改信箱、PUT 整份寫回；守門問（D-212、R-09）。
status 多回對外網址、Access 設好沒、加減名單的編號齊不齊、權杖有沒有存（只說有沒有）。拒絕與 Cloudflare 錯誤 exit 3。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from kb._lib import ledger, schedule, web, web_access, web_chat
from kb.inventory_cli import NOT_IN_VAULT, find_vault

BAD_PORT = "連接埠要是 1～65535 的數字"
SWITCHED_OFF = "網頁是關著的：先跑 kb web on"
TASK_TEXT = {"created": "建好了", "updated": "重設好了", "ok": "好的", "off": "已刪掉", "unsupported": "這台不設",
             "failed": "設不起來（已記進待處理，請服務人員看一下）"}
NO_CLAUDE_LINE = "Claude：沒找到（照建置步驟「網頁」裝）"
STATE_TEXT = {"ok": "好的", "missing": "沒有", "stale": "要重設（不是這一版或設定不同，跑 kb web ensure）", "unsupported": "這台不設"}
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_AUD = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
_CF_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
BAD_HOST = "{} 要是網域（例如 willy.example.com），不帶 https:// 與斜線"
BAD_AUD = "--aud 要是 Access 應用的 AUD 標籤（英數字）"
BAD_CF_ID = "--{} 要是 Cloudflare 的編號（英數字與 -）"
NEED_ACCESS = "設對外網址要一起給 --team 與 --aud（Access 的 team 網域與 AUD），不然手機誰都進不來"
PUBLIC_OFF = "對外網址拿掉了，手機連不進來（電腦上照常用）"
PUBLIC_ON = "手機網址：https://{host}（Access：{team}）"
CF_IDS = (("cf_account", "帳號編號"), ("cf_app", "應用編號"), ("cf_policy", "policy 編號"))
TOKEN_PROMPT = "貼上 Cloudflare 權杖（不會顯示），按 Enter："
TOKEN_SAVED = "Cloudflare 權杖存好了（只存在這台電腦的認證管理員）"
TOKEN_GONE = "Cloudflare 權杖刪掉了"
TOKEN_NONE = "這台本來就沒存 Cloudflare 權杖"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="web_command", metavar="<子子命令>", required=True)
    on = subs.add_parser("on", help="打開網頁：記下開關與連接埠、設排程", description="記下開關與連接埠，接著設排程，回網址。")
    off = subs.add_parser("off", help="關掉網頁：刪排程，伺服器 60 秒內自己停", description="記成關、刪排程。")
    en = subs.add_parser("ensure", help="照開關把排程設對", description="開著：排程不在或不對就重設；關著：有就刪。")
    st = subs.add_parser("status", help="開關、連接埠、排程、伺服器有沒有在回", description="只讀。")
    sv = subs.add_parser("serve", help="前景跑小伺服器（排程跑的就是這個）", description="一直跑到關掉、知識庫不見或換版。")
    pb = subs.add_parser("public", help="設手機用的對外網址與 Access（建置時）",
                         description="寫進這台的 web.json；--host \"\" 拿掉對外網址。")
    pb.add_argument("--host", required=True, help="對外網址的網域（例如 willy.example.com）；空字串＝關對外")
    pb.add_argument("--team", help="Access 的 team 網域（例如 xxx.cloudflareaccess.com）")
    pb.add_argument("--aud", help="Access 應用的 AUD 標籤")
    pb.add_argument("--account", help="Cloudflare 帳號編號（加減名單用）")
    pb.add_argument("--app", help="Access 應用編號（加減名單用）")
    pb.add_argument("--policy", help="Access policy 編號（加減名單用）")
    tk = subs.add_parser("token", help="存或刪手機登入用的 Cloudflare 權杖", description="set 從 stdin 讀；forget 刪掉。")
    tks = tk.add_subparsers(dest="token_command", metavar="<動作>", required=True)
    tk_set = tks.add_parser("set", help="從 stdin 讀權杖存進認證管理員（不收參數）", description="從 stdin 讀一行權杖。")
    tk_forget = tks.add_parser("forget", help="刪掉存的權杖", description="刪掉認證管理員裡的 Cloudflare 權杖。")
    ac = subs.add_parser("access", help="誰能從手機登入：列出、加、減信箱", description="改的是 Cloudflare Access 的名單。")
    acs = ac.add_subparsers(dest="access_command", metavar="<動作>", required=True)
    ac_list = acs.add_parser("list", help="列出能從手機登入的信箱", description="只讀。")
    ac_add = acs.add_parser("add", help="讓一個信箱能從手機登入", description="先讀整份名單、加上、整份寫回。")
    ac_rm = acs.add_parser("remove", help="讓一個信箱不能再從手機登入", description="先讀整份名單、拿掉、整份寫回。")
    for p in (ac_add, ac_rm):
        p.add_argument("email", help="信箱")
    for p in (on, off, en, st, sv, pb, tk_set, tk_forget, ac_list, ac_add, ac_rm):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    for p in (on, sv):
        p.add_argument("--port", type=int, help="連接埠（預設 47831，給過一次就記住）")
    sv.add_argument("--task", help=argparse.SUPPRESS)


def _print(as_json, data, line):
    print(json.dumps(data, ensure_ascii=False) if as_json else line)


def _current(vault):
    return web.read_switch(vault) or {"on": False, "port": web.DEFAULT_PORT}


def _on(vault, args, as_json):
    port = _current(vault)["port"] if args.port is None else args.port
    if not web.valid_port(port):
        print(BAD_PORT)
        return 3
    web.write_switch(vault, True, port)
    task = schedule.ensure_web(vault)
    _print(as_json, {"on": True, "port": port, "url": web.url(port), "task": task},
           f"網頁開了：{web.url(port)}（一分鐘內打得開）\n排程：{TASK_TEXT[task]}")
    return 3 if task == "failed" else 0


def _off(vault, as_json):
    port = _current(vault)["port"]
    web.write_switch(vault, False, port)
    task = schedule.ensure_web(vault)
    _print(as_json, {"on": False, "port": port, "task": task}, f"網頁關了，一分鐘內會停。\n排程：{TASK_TEXT[task]}")
    return 3 if task == "failed" else 0


def _ensure(vault, as_json):
    got = schedule.ensure_web(vault)
    _print(as_json, {"result": got}, f"網頁的排程：{TASK_TEXT[got]}")
    return 3 if got == "failed" else 0


def _status(vault, as_json):
    from kb._lib import vault as vault_lib
    sw = _current(vault)
    task = schedule.web_task_state(vault)
    answer = web.probe(sw["port"])
    mine = bool(answer and answer.get("app") == web.APP and answer.get("vault_id") == vault_lib.vault_id(vault))
    server = {"pid": answer.get("pid"), "version": answer.get("version")} if mine else None
    claude = web_chat.find_claude()
    pub = web.read_public(vault)
    access = bool(pub["access_team"] and pub["access_aud"])
    missing = [name for key, name in CF_IDS if not pub[key]]
    token = web_access.has_token(vault)
    data = {"on": sw["on"], "port": sw["port"], "url": web.url(sw["port"]), "task": task, "running": mine,
            "server": server, "claude": claude, "public_host": pub["public_host"], "access": access,
            "cf_ids": not missing, "token": token}
    lines = [f"開關：{'開' if sw['on'] else '關'}", f"網址：{web.url(sw['port'])}", f"排程：{STATE_TEXT[task]}",
             f"伺服器：在回（pid {server['pid']}，版本 {server['version']}）" if mine else "伺服器：沒在回",
             f"Claude：{claude}" if claude else NO_CLAUDE_LINE]
    if pub["public_host"]:
        lines.append(f"手機網址：https://{pub['public_host']}（Access {'設好了' if access else '沒設好：少了 team 或 AUD'}）")
    else:
        lines.append("手機網址：沒設（只在這台電腦用）")
    lines.append("加減登入名單：" + ("編號齊了" if not missing else "還缺 " + "、".join(missing)))
    lines.append("Cloudflare 權杖：" + {True: "有存", False: "沒存", None: "這台存不了（不是 Windows）"}[token])
    _print(as_json, data, "\n".join(lines))
    return 0


def _check(value, rx, text):
    if not rx.match(value):
        print(text)
        return False
    return True


def _public(vault, args, as_json):
    host = args.host.strip().lower()
    if not host:
        web.write_public(vault, {"public_host": None})
        _print(as_json, {"public_host": None}, PUBLIC_OFF)
        return 0
    new = {"public_host": host, "access_team": (args.team or "").strip().lower() or None,
           "access_aud": (args.aud or "").strip() or None, "cf_account": (args.account or "").strip() or None,
           "cf_app": (args.app or "").strip() or None, "cf_policy": (args.policy or "").strip() or None}
    if not _check(host, _HOSTNAME, BAD_HOST.format("--host")):
        return 3
    if new["access_team"] and not _check(new["access_team"], _HOSTNAME, BAD_HOST.format("--team")):
        return 3
    if new["access_aud"] and not _check(new["access_aud"], _AUD, BAD_AUD):
        return 3
    for key, flag in (("cf_account", "account"), ("cf_app", "app"), ("cf_policy", "policy")):
        if new[key] and not _check(new[key], _CF_ID, BAD_CF_ID.format(flag)):
            return 3
    old = web.read_public(vault)
    merged = {k: new[k] or old[k] for k in web.PUBLIC_KEYS}
    if not merged["access_team"] or not merged["access_aud"]:
        print(NEED_ACCESS)
        return 3
    web.write_public(vault, merged)
    missing = [name for key, name in CF_IDS if not merged[key]]
    line = PUBLIC_ON.format(host=host, team=merged["access_team"])
    line += "\n加減登入名單：" + ("編號齊了" if not missing else "還缺 " + "、".join(missing) + "（--account --app --policy）")
    _print(as_json, dict(merged, cf_ids=not missing), line)
    return 0


def _read_token():
    """權杖從 stdin 讀（不收參數）；終端機上用 getpass 不顯示。"""
    stream = sys.stdin
    if stream is None:
        return ""
    if hasattr(stream, "isatty") and stream.isatty():
        import getpass
        return getpass.getpass(TOKEN_PROMPT)
    return (stream.readline() or "").strip()


def _token(vault, args, as_json):
    try:
        if args.token_command == "set":
            web_access.save_token(vault, _read_token())
            _print(as_json, {"saved": True}, TOKEN_SAVED)
            return 0
        gone = web_access.forget_token(vault)
    except web_access.Refused as e:
        _print(as_json, {"error": str(e)}, str(e))
        return 3
    _print(as_json, {"forgot": gone}, TOKEN_GONE if gone else TOKEN_NONE)
    return 0


def _access(vault, args, as_json):
    cfg = web.read_public(vault)
    try:
        if args.access_command == "list":
            got = web_access.access_list(vault, cfg)
            lines = (["能從手機登入的："] + [f"- {e}" for e in got["emails"]]) if got["emails"] else ["名單上沒有信箱"]
            if got["other_rules"]:
                lines.append(f"（另有 {got['other_rules']} 條不是單一信箱的規則，照原樣留著）")
            _print(as_json, got, "\n".join(lines))
            return 0
        changed, said = web_access.access_change(vault, cfg, args.email, args.access_command == "add")
    except web_access.Refused as e:
        _print(as_json, {"error": str(e)}, str(e))
        return 3
    _print(as_json, {"changed": changed, "message": said}, said)
    return 0


def _orphan(args):
    """排程跑的 serve 找不到庫（刪了、搬了、不是庫了）：照 --task 刪掉自己的排程（同背景比對 changes._orphan）。"""
    gone = schedule.forget(args.task)
    ledger.log_error(None, f"網頁找不到知識庫 {args.vault}，{'刪了排程 ' + args.task if gone else '排程沒刪'}", source="web")


def run(args):
    vault = find_vault(args)
    if args.web_command == "serve":
        # --vault 本身要是庫：往上找到外層的庫不算（同 changes --background）
        if vault is None or (args.vault and Path(os.path.realpath(args.vault)) != vault):
            if not args.task:  # 人手動跑、不在知識庫裡：照其他子命令說
                print(NOT_IN_VAULT)
                return 3
            _orphan(args)
            return 0
        if args.port is not None and not web.valid_port(args.port):
            print(BAD_PORT)
            return 3
        if not args.task and not _current(vault)["on"]:
            print(SWITCHED_OFF)
            return 3
        return web.serve(vault, args.port, args.task)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    as_json = getattr(args, "json", False)
    if args.web_command == "on":
        return _on(vault, args, as_json)
    if args.web_command == "off":
        return _off(vault, as_json)
    if args.web_command == "ensure":
        return _ensure(vault, as_json)
    if args.web_command == "public":
        return _public(vault, args, as_json)
    if args.web_command == "token":
        return _token(vault, args, as_json)
    if args.web_command == "access":
        return _access(vault, args, as_json)
    return _status(vault, as_json)
