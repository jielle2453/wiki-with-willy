"""wiki-with-willy 唯一 CLI 入口（D-013）。用法：python scripts/kb.py <子命令> [--json]"""
import sys

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from kb.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
