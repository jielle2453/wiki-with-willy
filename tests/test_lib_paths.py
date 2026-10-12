"""_lib/paths 的守門部分：normalize 六種寫法同一結果、inside 的邊界、resolve_spec 四種 spec 與展不開的變數。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._guard_fixture import SCRIPTS  # noqa: F401  (把 scripts/ 放進 sys.path)
from kb._lib import paths  # noqa: E402

CWD = "C:/x/vault"


class Normalize(unittest.TestCase):
    def test_six_spellings_give_one_result(self):
        want = paths.normalize("C:/x/raw/行政/薪資/s.xlsx", CWD)
        forms = [
            "C:/x/raw/行政/薪資/s.xlsx",
            "c:/x/raw/行政/薪資/s.xlsx",
            "C:\\x\\raw\\行政\\薪資\\s.xlsx",
            "/c/x/raw/行政/薪資/s.xlsx",
            "../raw/行政/薪資/s.xlsx",
            "\\\\?\\C:\\x\\raw\\行政\\薪資\\s.xlsx",
        ]
        for f in forms:
            with self.subTest(form=f):
                self.assertEqual(paths.normalize(f, CWD), want)

    def test_result_uses_forward_slash_no_trailing_slash(self):
        got = paths.normalize("C:\\x\\vault\\AI 產出\\", CWD)
        self.assertNotIn("\\", got)
        self.assertFalse(got.endswith("/"))
        self.assertTrue(got.endswith("/ai 產出") if os.name == "nt" else got.endswith("/AI 產出"))

    def test_relative_joins_cwd_and_collapses_dots(self):
        self.assertEqual(paths.normalize("./案子/../案子/a.md", CWD), paths.normalize("C:/x/vault/案子/a.md", CWD))

    def test_quotes_are_stripped(self):
        self.assertEqual(paths.normalize('"C:/x/a b.txt"', CWD), paths.normalize("C:/x/a b.txt", CWD))

    def test_lowercase_on_windows(self):
        if os.name != "nt":
            self.skipTest("只在 Windows 比小寫")
        self.assertEqual(paths.normalize("C:/X/Y.TXT", CWD), "c:/x/y.txt")


class Inside(unittest.TestCase):
    def test_segment_boundary(self):
        d = paths.normalize("C:/a/b", CWD)
        self.assertTrue(paths.inside(paths.normalize("C:/a/b/c.txt", CWD), d))
        self.assertTrue(paths.inside(d, d))
        self.assertFalse(paths.inside(paths.normalize("C:/a/bc", CWD), d))
        self.assertFalse(paths.inside(paths.normalize("C:/a", CWD), d))


class ResolveSpec(unittest.TestCase):
    VAULT = "C:/x/vault"

    def test_relative_to_vault(self):
        self.assertEqual(paths.resolve_spec("../raw/客戶案件", self.VAULT), paths.normalize("C:/x/raw/客戶案件", CWD))
        self.assertEqual(paths.resolve_spec("案子", self.VAULT), paths.normalize("C:/x/vault/案子", CWD))

    def test_absolute(self):
        self.assertEqual(paths.resolve_spec("D:/share/客戶", self.VAULT), paths.normalize("D:/share/客戶", CWD))

    def test_home(self):
        home = os.path.expanduser("~")
        self.assertEqual(paths.resolve_spec("~/docs", self.VAULT), paths.normalize(os.path.join(home, "docs"), CWD))

    def test_env_var(self):
        os.environ["KB_TEST_SHARE"] = "E:/share"
        try:
            self.assertEqual(paths.resolve_spec("${KB_TEST_SHARE}/客戶", self.VAULT), paths.normalize("E:/share/客戶", CWD))
        finally:
            del os.environ["KB_TEST_SHARE"]

    def test_unresolvable_env_var_returns_none(self):
        os.environ.pop("KB_TEST_NOPE", None)
        self.assertIsNone(paths.resolve_spec("${KB_TEST_NOPE}/客戶", self.VAULT))


class NormalizeEquivalentSpellings(unittest.TestCase):
    """Win32 會接受的同一個檔的其他寫法（審閱發現）：尾點、\\.\ 前綴、本機 UNC 管理共享、磁碟相對、8.3 短名。"""

    def test_trailing_dot_on_segments_is_stripped(self):
        want = paths.normalize("C:/x/raw/行政/薪資/s.xlsx", CWD)
        for f in ("C:/x/raw/行政/薪資./s.xlsx", "C:/x/raw/行政./薪資/s.xlsx", "C:/x/raw/行政/薪資/s.xlsx.", "C:/x/raw/行政/薪資 /s.xlsx"):
            with self.subTest(form=f):
                self.assertEqual(paths.normalize(f, CWD), want)

    def test_device_prefix_and_local_admin_share(self):
        want = paths.normalize("C:/x/a.txt", CWD)
        for f in ("\\\\.\\C:\\x\\a.txt", "//?/C:/x/a.txt", "\\\\localhost\\c$\\x\\a.txt", "//127.0.0.1/c$/x/a.txt"):
            with self.subTest(form=f):
                self.assertEqual(paths.normalize(f, CWD), want)

    def test_drive_relative_joins_cwd(self):
        self.assertEqual(paths.normalize("C:../raw/a.txt", CWD), paths.normalize("C:/x/raw/a.txt", CWD))

    def test_existing_path_is_canonicalized(self):
        """存在的路徑經 realpath：8.3 短名、junction、大小寫都回到真名；不存在的尾段照樣接上。"""
        d = Path(tempfile.mkdtemp(prefix="KbPaths-"))
        try:
            (d / "Sub").mkdir()
            (d / "Sub" / "F.txt").write_text("x", encoding="utf-8")
            want = paths.normalize(str(d / "Sub" / "F.txt"), CWD)
            self.assertEqual(paths.normalize(str(d / "SUB" / "f.TXT"), CWD), want)
            self.assertEqual(paths.normalize(str(d / "Sub" / "新檔.txt"), CWD), want.rsplit("/", 1)[0] + "/新檔.txt")
            if os.name == "nt":
                import ctypes
                buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.kernel32.GetShortPathNameW(str(d / "Sub" / "F.txt"), buf, 512)
                short = buf.value
                if short and short.lower() != str(d / "Sub" / "F.txt").lower():
                    self.assertEqual(paths.normalize(short, CWD), want)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
