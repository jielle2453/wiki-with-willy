"""kb build status／start／env／set／facts／done／redo／adopt／report（build 第 2 點，D-189～D-194）。

本體在 _lib/build；這裡只拆參數、印人讀或 JSON。不收 --vault：建置在 cwd（桌面版開對話時選的資料夾）。
拒絕句一字不差、exit 3（--json 時也印那一句）。env、facts、adopt --check、report --facts 只讀；
start、set、done、redo、report --from 寫 build.json（或報告）並記一段工作日誌程式段（D-085），對話裡叫的併進回合。
終端機不印舊 wiki 的內容（adopt --extract 只印段數）。
"""
import argparse
import json
import os
from pathlib import Path

from kb import journal_cli
from kb._lib import batches, entities, profile
from kb._lib import build as lib
from kb._lib import io as kio

CLI_LOCK_TIMEOUT = 30
LOCK_BUSY = "等不到鎖，沒有寫入"
LEVEL_TEXT = {"ok": "可以", "warn": "注意", "fail": "不行"}
STEP_CHOICES = [s for s in lib.STEPS if s != "done"]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="build_command", metavar="<子子命令>", required=True)
    st = subs.add_parser("status", help="建置做到哪、這一步做什麼", description="印目前這一步與要跑的指令；還沒開始從環境那步起。")
    sa = subs.add_parser("start", help="開始建置：在這個資料夾建知識庫", description="在目前的資料夾跑 kb init、記下環境檢查。")
    ev = subs.add_parser("env", help="檢查這台電腦（只讀）", description="Python、kb、信任、智慧型應用程式控制、PDF 元件、Office、磁碟、舊 llm-wiki。")
    se = subs.add_parser("set", help="記下幾個人用、舊 wiki 在哪", description="people：幾個人用（她的原話）；adopt：舊 llm-wiki 的資料夾。")
    se.add_argument("key", choices=["people", "adopt"], help="people 或 adopt")
    se.add_argument("value", help="值（有空白加引號）")
    fa = subs.add_parser("facts", help="盤點（只讀）", description="每個來源幾份、什麼格式、哪些夾、讀不到幾份與原因、大約多久。")
    dn = subs.add_parser("done", help="這一步做完了", description="程式驗前提，過了往下一步。")
    dn.add_argument("step", choices=STEP_CHOICES, help="步驟名")
    rd = subs.add_parser("redo", help="回到做過的一步", description="例：重新抽樣 → kb build redo sample。")
    rd.add_argument("step", choices=STEP_CHOICES, help="步驟名")
    ad = subs.add_parser("adopt", help="收舊 llm-wiki", description="--check 只看舊 wiki 有什麼；--extract 切成段給製卡助手讀。")
    ad.add_argument("path", help="舊 llm-wiki 的資料夾")
    how = ad.add_mutually_exclusive_group(required=True)
    how.add_argument("--check", action="store_true", help="只看，不寫")
    how.add_argument("--extract", action="store_true", help="切段寫進 .system/temp/adopt/")
    rp = subs.add_parser("report", help="交付報告", description="--facts 給數字與清單；--from 把寫好的那份放進 系統/建置報告.md。")
    which = rp.add_mutually_exclusive_group(required=True)
    which.add_argument("--facts", action="store_true", help="數字與清單（只讀）")
    which.add_argument("--from", dest="from_md", help="在工作區寫好的報告")
    for p in (st, sa, ev, se, fa, dn, rd, ad, rp):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, lines, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else "\n".join(lines))


def _journal(vault, note, changed, command):
    journal_cli.record_program_or_warn(vault, note, changed, command, journal_cli.in_conversation(vault))


BUILD_CHANGED = [{"path": lib.BUILD_POSIX, "action": "modify"}]


def _status(cwd, as_json):
    got = lib.status(cwd)
    lines = [f"建置：現在是「{lib.STEP_TEXT[got['step']]}」"] + [f"- {d}" for d in got["do"]] + \
        [f"讀：{got['read']}", f"做完跑：{got['after']}"]
    if got["not_empty"]:
        lines.insert(1, "這個夾裡已經有東西")
    _out(as_json, lines, got)
    return 0


def _start(cwd, as_json):
    vault, data = lib.start(cwd)
    line = lib.DONE_LINE.format(step=lib.STEP_TEXT["env"], next=lib.STEP_TEXT["setup"])
    _out(as_json, [line], {"vault": str(vault), "step": data["step"], "env": data["env"]["items"], "message": line})
    return 0


def _env(cwd, as_json):
    items = lib.env_items(cwd)
    lines = [f"{LEVEL_TEXT[i['level']]}：{i['text']}" + (f"（要裝：{i['fix']}）" if i.get("fix") else "") for i in items]
    _out(as_json, lines, {"items": items})
    return 0


def _set(cwd, args, as_json):
    vault, _data, line = lib.set_value(cwd, args.key, args.value)
    _journal(vault, line, BUILD_CHANGED, "build set")
    _out(as_json, [line], {"key": args.key, "value": _data.get(args.key), "message": line})
    return 0


def _vault(cwd):
    from kb import guard
    found = guard.find_vault(str(cwd))
    if found is None:
        raise lib.Refused(lib.NOT_STARTED)
    return Path(os.path.realpath(str(found)))


def _facts(cwd, as_json):
    got = lib.facts(_vault(cwd))
    lines = []
    for s in got["sources"]:
        bad = s["unreadable"]
        why = "、".join(f"{k} {n}" for k, n in bad["reasons"].items())
        lines.append(f"{s['alias']}：{s['files']} 份" + (f"，讀不到 {bad['count']} 份（{why}）" if bad["count"] else ""))
        lines += [f"- {f['name']}：{f['files']} 份" for f in s["folders"]]
    est = got["estimate"]
    lines.append(f"共 {got['total']} 份；要整理 {est['files']} 份，{est['text']}" + (f"；{est['usage']}" if est["usage"] else ""))
    _out(as_json, lines, got)
    return 0


def _done(cwd, args, as_json):
    vault, data, line = lib.done(cwd, args.step)
    _journal(vault, line, BUILD_CHANGED, "build done")
    _out(as_json, [line], {"step": args.step, "next": data["step"], "message": line})
    return 0


def _redo(cwd, args, as_json):
    vault, data, line = lib.redo(cwd, args.step)
    _journal(vault, line, BUILD_CHANGED, "build redo")
    _out(as_json, [line], {"step": data["step"], "message": line})
    return 0


def _adopt(cwd, args, as_json):
    if args.check:
        got = lib.adopt_check(args.path)
        linked = "、".join(x["path"] + ("" if x["exists"] else "（找不到）") for x in got["linked"])
        lines = [f"舊 wiki：{got['path']}", f"raw：{got['raw'] or '沒有'}" + (f"；外掛的夾：{linked}" if linked else ""),
                 f"wiki 頁 {got['wiki_pages']} 頁；hot.md {got['hot_chars']} 字；log.md {got['log_chars']} 字；"
                 f"規則 {got['rules']} 條；日程 {got['agenda']} 條"]
        _out(as_json, lines, got)
        return 0
    vault = _vault(cwd)
    got = lib.adopt_extract(vault, args.path)
    _out(as_json, [f"切好了：{len(got['chunks'])} 段（{len(got['files'])} 份檔）"], got)
    return 0


def _report(cwd, args, as_json):
    if args.facts:
        got = lib.report_facts(_vault(cwd))
        _out(as_json, lib.numbers_lines(got), got)
        return 0
    vault = lib.report_from(cwd, args.from_md)
    changed = [{"path": lib.REPORT_POSIX, "action": "modify"}, {"path": lib.BUILD_POSIX, "action": "modify"}]
    _journal(vault, "寫了建置報告", changed, "build report")
    _out(as_json, [lib.REPORT_OK], {"path": lib.REPORT_POSIX, "message": lib.REPORT_OK})
    return 0


def run(args):
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    cwd = Path(os.getcwd())
    command = args.build_command
    try:
        if command == "status":
            return _status(cwd, as_json)
        if command == "start":
            return _start(cwd, as_json)
        if command == "env":
            return _env(cwd, as_json)
        if command == "set":
            return _set(cwd, args, as_json)
        if command == "facts":
            return _facts(cwd, as_json)
        if command == "done":
            return _done(cwd, args, as_json)
        if command == "redo":
            return _redo(cwd, args, as_json)
        if command == "adopt":
            return _adopt(cwd, args, as_json)
        return _report(cwd, args, as_json)
    except lib.Refused as e:
        print(str(e))
        return 3
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except (ValueError, batches.Unreadable, entities.Unreadable, profile.ProfileError) as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
