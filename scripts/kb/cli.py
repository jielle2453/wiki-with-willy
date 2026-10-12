"""argparse 主程式。子命令註冊表在 COMMANDS；新子命令加一行，--help 自動列出。"""
import argparse
import importlib

# 名稱: (模組, 一句說明)
COMMANDS = {
    "version": ("kb.version", "印 plugin 名稱與版號"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="wiki-with-willy 的唯一指令入口。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    subparsers = parser.add_subparsers(dest="command", metavar="<子命令>", required=True)
    for name, (_module, doc) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=doc, description=doc)
        sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    module_name, _doc = COMMANDS[args.command]
    module = importlib.import_module(module_name)
    return module.run(args)
