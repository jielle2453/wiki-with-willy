"""wiki-with-willy 唯一 CLI 入口（D-013）。用法：python scripts/kb.py <子命令> [--json]"""
import sys

for _stream in (sys.stdout, sys.stderr):  # pythonw 跑的（電腦排程的背景比對）兩個都是 None
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from kb.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
