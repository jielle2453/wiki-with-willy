"""kb guard decide：組一份跟 Claude Code 一樣的 payload 丟給 guard.decide，印結果。dry-run：不寫 ledger、不寫 status。
給測試 session 與服務人員查「為什麼擋」用。
"""
import argparse
import json
import os

from kb import guard

TOOL_KEY = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path", "Glob": "path", "Grep": "path",
    "Bash": "command", "PowerShell": "command",
}


def add_arguments(sub):
    subs = sub.add_subparsers(dest="guard_command", metavar="<子子命令>", required=True)
    d = subs.add_parser("decide", help="模擬一次守門判斷（不留痕）", description="模擬一次守門判斷（不留痕）。")
    d.add_argument("--tool", required=True, choices=sorted(TOOL_KEY), help="工具名")
    group = d.add_mutually_exclusive_group(required=True)
    group.add_argument("--path", help="檔案工具的目標路徑")
    group.add_argument("--command", dest="shell_command", help="Bash／PowerShell 的指令字串")
    d.add_argument("--cwd", help="對話所在目錄，預設是目前目錄")
    d.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def run(args):
    value = args.shell_command if args.tool in ("Bash", "PowerShell") else args.path
    if value is None:
        print(f"--tool {args.tool} 要配 {'--command' if args.tool in ('Bash', 'PowerShell') else '--path'}")
        return 2
    payload = {
        "tool_name": args.tool,
        "tool_input": {TOOL_KEY[args.tool]: value},
        "cwd": args.cwd or os.getcwd(),
        "permission_mode": "default",
    }
    d = guard.decide(payload)
    if getattr(args, "json", False):
        print(json.dumps({"decision": d.decision, "zone": d.zone, "reason_code": d.reason_code, "reason": d.reason},
                         ensure_ascii=False))
    elif d.decision == "pass":
        print("pass")
    else:
        print(" ".join(part for part in (d.decision, d.zone, d.reason_code, d.reason) if part))
    return 0
