"""repo 衛生：git ls-files 列出的 .py .md .json 檔要 UTF-8 無 BOM、LF、檔尾一個換行；
不含開發機絕對路徑。排除 design/（唯讀設計文件，不受程式檔規範）。
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTS = {".py", ".md", ".json"}
BOM = b"\xef\xbb\xbf"
# 刻意拆開拼，讓本檔自己不含那兩個字串
DEV_PATHS = ("C:" + "\\" + "Users", "C:" + "/Users")


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, cwd=str(ROOT), check=True
    ).stdout
    names = [n.decode("utf-8") for n in out.split(b"\0") if n]
    return [
        n for n in names
        if Path(n).suffix in EXTS and not n.startswith("design/")
    ]


class Encoding(unittest.TestCase):
    def test_tracked_files_exist(self):
        self.assertTrue(tracked_files(), "git ls-files 沒列出任何 .py/.md/.json（repo 未 init？）")

    def test_utf8_no_bom_lf_single_trailing_newline(self):
        for name in tracked_files():
            raw = (ROOT / name).read_bytes()
            if raw == b"":
                continue  # 空檔（如 __init__.py）不要求換行
            with self.subTest(file=name):
                self.assertFalse(raw.startswith(BOM), "有 BOM")
                raw.decode("utf-8")
                self.assertNotIn(b"\r", raw, "含 CR")
                self.assertTrue(raw.endswith(b"\n"), "檔尾沒有換行")
                self.assertFalse(raw.endswith(b"\n\n"), "檔尾多於一個換行")


class NoSysPath(unittest.TestCase):
    """scripts/ 與 hooks/ 裡只有 hooks/*.py 准動 sys.path（hook 不是從 scripts/ 跑的，見 CLAUDE.md 三）。"""

    @staticmethod
    def is_exception(name):
        return name.startswith("hooks/") and "/" not in name[len("hooks/"):] and name.endswith(".py")

    def test_only_hook_entries_touch_sys_path(self):
        for name in tracked_files():
            if not (name.startswith("scripts/") or name.startswith("hooks/")) or Path(name).suffix != ".py":
                continue
            if self.is_exception(name):
                continue
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("sys.path", text)


class HookHeader(unittest.TestCase):
    """每支 hooks/*.py 開頭都要 dont_write_bytecode 與 stdout／stderr reconfigure UTF-8（stop.py 也是）。"""

    def test_every_hook_entry_has_header(self):
        names = sorted(p.name for p in (ROOT / "hooks").glob("*.py"))
        self.assertIn("stop.py", names)
        for name in names:
            text = (ROOT / "hooks" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn("sys.dont_write_bytecode = True", text)
                self.assertIn('reconfigure(encoding="utf-8")', text)


class NoDevPaths(unittest.TestCase):
    def test_no_dev_machine_paths(self):
        for name in tracked_files():
            if name == "README.md":
                continue
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                for needle in DEV_PATHS:
                    self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
