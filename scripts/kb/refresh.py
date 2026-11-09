"""kb refresh [--json]（多人共用 3，D-293）：git pull 之後讓這台對上。重畫頁、比 pull 進來的原檔、收拾合併衝突、記大檔。

每句話開頭 hook 在 git HEAD 變了才叫；Obsidian 外掛看到 .git 變了也叫。不是 git repo 什麼都不做。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard
from kb._lib import io as kio
from kb._lib import refresh

NOT_A_VAULT = "這裡不是知識庫"
CLI_LOCK_TIMEOUT = 30


def add_arguments(sub):
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def lines(got):
    out = []
    if got.get("rebase"):
        out.append("同步卡住了（rebase 到一半），請聯絡服務人員")
    for c in got["conflicts"]:
        out.append(f"衝突：{c['path']}（{c['kind']}，{c['other']}）" + (f"，另存 {c['copy']}" if c.get("copy") else ""))
    for b in got["big"]:
        out.append(f"太大推不上 git：{b['path']}（{b['mb']} MB）")
    out.append(f"重畫 {len(got['rendered'])} 頁，pull 進來 {len(got['pulled'])} 個檔")
    return out


def run(args):
    as_json = getattr(args, "json", False)
    found = guard.find_vault(os.getcwd())
    if found is None:
        print(json.dumps({"ok": False, "message": NOT_A_VAULT}, ensure_ascii=False) if as_json else NOT_A_VAULT)
        return 3
    vault = Path(os.path.realpath(str(found)))
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    try:
        got = refresh.run(vault)
    except (TimeoutError, OSError, ValueError) as e:
        msg = f"refresh 沒做完：{e}"
        print(json.dumps({"ok": False, "message": msg}, ensure_ascii=False) if as_json else msg)
        return 3
    print(json.dumps(dict(got, ok=True), ensure_ascii=False) if as_json else "\n".join(lines(got)))
    return 0
