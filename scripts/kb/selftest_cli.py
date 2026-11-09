"""kb selftest [--json]（換版前先驗新版，D-230）：檢查這份 plugin 在這台電腦跑不跑得起來；本體在 _lib/selftest。
不收 --vault、不碰 cwd 的知識庫（只在系統暫存夾建一個小知識庫，跑完刪掉）。過了 exit 0，沒過 exit 3。
換版之前舊版跑新版的這支（_lib/upgrade.verify），發行程式打包時也跑（tools/release.py）。
"""
import json

from kb._lib import selftest

PASSED = "自我檢查過了（{version}）"
FAILED = "自我檢查沒過：{step}：{why}"


def run(args):
    got = selftest.run()
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    elif got["ok"]:
        print(PASSED.format(version=got["version"]))
    else:
        where = f"{got['file']} 第 {got['line']} 行：" if got["file"] and got["line"] else ""
        print(FAILED.format(step=got["step"], why=where + (got["why"] or "")))
    return 0 if got["ok"] else 3
