"""argparse 主程式。子命令註冊表在 COMMANDS；新子命令加一行，--help 自動列出。
模組有 add_arguments(sub) 就讓它自己加參數（子子命令、--path 之類），沒有就只給 --json。
"""
import argparse
import importlib

# 名稱: (模組, 一句說明)
COMMANDS = {
    "version": ("kb.version", "印 plugin 名稱與版號"),
    "guard": ("kb.guard_cli", "守門：decide 模擬一次判斷，查「為什麼擋」"),
    "versions": ("kb.versions", "回合：list 列最近的改動，revert 退回最近一次，delete-outputs 刪已退回回合留著的成品"),
    "turn": ("kb.turn_cli", "目前回合：current 看狀態，approve 解除綜合卡張數上限"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="wiki-with-willy 的唯一指令入口。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    subparsers = parser.add_subparsers(dest="command", metavar="<子命令>", required=True)
    for name, (module_name, doc) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=doc, description=doc)
        module = importlib.import_module(module_name)
        if hasattr(module, "add_arguments"):
            module.add_arguments(sub)
        else:
            sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    module_name, _doc = COMMANDS[args.command]
    module = importlib.import_module(module_name)
    return module.run(args)
