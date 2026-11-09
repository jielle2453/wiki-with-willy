"""kb backup [--to <夾>] [--check] [--json]（備份還原與換電腦 要做的事 5，D-203、D-204、D-209）：整個知識庫備份成一個 zip。

AI 在對話裡直接跑；第一次沒記過放哪回 need_to（人讀「還沒說備份要放哪」exit 3），AI 問客戶，帶 --to 再跑、程式記住。
--check 只算：放哪、幾份、多大、是不是同步夾、會不會被擋（refuse），一律 exit 0，不寫任何東西。
成功句與拒絕句一字不差（exit 3）；成功後接著清理（_lib/clean）。總資料夾只從 cwd 往上找（不收 --vault）。
終端機跑的記一段工作日誌程式段（D-085），對話裡叫的不記（AI 用 kb turn note）。備份本身不改知識庫。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import backup, clean, config

LOCK_BUSY = backup.BUSY
SUCCESS = "備份好了：{path}（{files} 份、{mb}）；那裡留最近 {keep} 份。"
SYNC_NOTE = "放在雲端同步的夾裡：會上傳到雲端，第一次比較久"


def add_arguments(sub):
    sub.add_argument("--to", help="備份放哪個資料夾；第一次要給，程式記住，之後不用再給（換位置也是用它）")
    sub.add_argument("--check", action="store_true", help="只看會放哪、多大、會不會被擋，不動任何東西")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _out(as_json, line, payload):
    print(json.dumps(payload, ensure_ascii=False) if as_json else line)


def _check_lines(got):
    if got["need_to"]:
        return [backup.NEED_TO]
    lines = [f"會備份 {got['files']} 份（{clean.mb_text(got['bytes'])}）到 {got['to']}"]
    if got["refuse"]:
        lines.append(got["refuse"])
    if got["sync_folder"]:
        lines.append(SYNC_NOTE)
    return lines


def run(args):
    as_json = getattr(args, "json", False)
    found = guard.find_vault(os.getcwd())
    if found is None:
        _out(as_json, backup.NOT_A_VAULT, {"ok": False, "message": backup.NOT_A_VAULT})
        return 3
    vault = Path(os.path.realpath(str(found)))
    try:
        if args.check:
            got = backup.check(vault, args.to)
            _out(as_json, "\n".join(_check_lines(got)), got)
            return 0
        conversation = journal_cli.in_conversation(vault)
        got = backup.backup(vault, args.to)  # 位置在寫 zip 之前就記了（backup.backup 裡，拒絕時不寫，N-6）
    except backup.Refused as e:
        _out(as_json, str(e), {"ok": False, "message": str(e)})
        return 3
    except (ValueError, OSError) as e:
        line = backup.oserror_sentence(e) if isinstance(e, OSError) else None
        line = line or f"備份沒有做成：{type(e).__name__}"
        _out(as_json, line, {"ok": False, "message": line})
        return 3
    line = SUCCESS.format(path=got["path"], files=got["files"], mb=clean.mb_text(got["bytes"]),
                          keep=config.get(vault, "backup_keep"))
    extra = clean.sentence(got["clean"]) if got["clean"] else None
    journal_cli.record_program_or_warn(vault, line.split("；")[0], [], "backup", conversation)
    _out(as_json, line + (("\n" + extra) if extra else ""), dict(got, ok=True, message=line))
    return 0
