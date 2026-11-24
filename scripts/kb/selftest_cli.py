"""kb selftest [--json]（換版前先驗新版，D-230）：檢查這份 plugin 在這台電腦跑不跑得起來；本體在 _lib/selftest。
不收 --vault、不改 cwd 的知識庫（只在系統暫存夾建一個小知識庫，跑完刪掉）。過了 exit 0，沒過 exit 3。
在知識庫裡跑時多印一行被同步追蹤的 willy 檔（D-554 (3)，只讀、不算進過不過；--json 是 sync_untrack 欄）。
換版之前舊版跑新版的這支（_lib/upgrade.verify），發行程式打包時也跑（tools/release.py）。
"""
import json

from kb._lib import selftest

PASSED = "自我檢查過了（{version}）"
FAILED = "自我檢查沒過：{step}：{why}"


def _sync_untrack():
    """在知識庫裡跑時多一項（D-554 (3)）：被同步追蹤的 willy 檔，同 kb sync untrack --check 那句；只讀、不算進過不過。"""
    try:
        import os
        from kb import guard
        from kb._lib import refresh
        found = guard.find_vault(os.getcwd())
        got = refresh.untrack_summary(found) if found else None
        return refresh.check_line(got) if got and got["n"] else None
    except Exception:  # noqa: BLE001  這一項出錯不影響自我檢查
        return None


def run(args):
    got = selftest.run()
    got["sync_untrack"] = _sync_untrack()
    if getattr(args, "json", False):
        print(json.dumps(got, ensure_ascii=False))
    elif got["ok"]:
        print(PASSED.format(version=got["version"]))
    else:
        where = f"{got['file']} 第 {got['line']} 行：" if got["file"] and got["line"] else ""
        print(FAILED.format(step=got["step"], why=where + (got["why"] or "")))
    if got.get("sync_untrack") and not getattr(args, "json", False):  # 沒過也印（測試 #7）；JSON 已有這個鍵
        print(got["sync_untrack"])
    return 0 if got["ok"] else 3
