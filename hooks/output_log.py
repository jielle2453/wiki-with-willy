"""PostToolUse 成品登記入口：檔案工具寫進 AI 產出（Z3）之後，把那份檔登進 .system/inventory.json（D-076）。

只做四個寫入工具；目標在 Z3 且檔案真的存在才登記；回合代號取 turn.current(vault, session_id)。
任何例外寫 .system/hook-errors.log，永遠 exit 0、不輸出。
hook 不是從 scripts/ 跑的，所以這裡動 sys.path（hooks/*.py 是唯一例外，CLAUDE.md 三）。
"""
import json
import os
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
OUTPUT_ZONE = "Z3"


def main():
    payload = {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"payload 不是物件：{type(payload).__name__}")
        tool = payload.get("tool_name")
        if tool not in WRITE_TOOLS:
            return
        ti = payload.get("tool_input") or {}
        target = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
        if not target:
            return
        from kb import guard
        from kb._lib import inventory, turn
        from kb._lib import sources as sources_lib
        from kb._lib.paths import normalize

        cwd = payload.get("cwd") or os.getcwd()
        vault = guard.find_vault(cwd)
        if vault is None:
            return
        zone, _alias = guard.classify(normalize(str(target), cwd), normalize(str(vault), None), sources_lib.load(vault))
        if zone != OUTPUT_ZONE:
            return
        rel = turn.rel_path(vault, str(target), cwd)
        if not (vault / rel).is_file():
            return
        t = turn.current(vault, payload.get("session_id"))
        inventory.register_output(vault, rel, t["turn"])
    except BaseException as e:  # noqa: BLE001  任何例外都要吞掉
        _log(e, payload)


def _log(e, payload):
    try:
        from kb import guard
        from kb._lib import ledger

        payload = payload if isinstance(payload, dict) else {}
        vault = guard.find_vault(payload.get("cwd") or os.getcwd())
        ledger.log_error(vault, f"{type(e).__name__}: {str(e)[:200]}", source="output_log")
    except BaseException:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
