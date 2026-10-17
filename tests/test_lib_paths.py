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


class CompressSpec(unittest.TestCase):
    """骨架與來源登記：compress_spec 四種結果（相對、~/、絕對、不同磁碟）與 is_abs_spec。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="KbSpec-")).resolve()
        self.vault = self.tmp / "總" / "知識庫"
        self.vault.mkdir(parents=True)
        self.env = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_home(self, path):
        os.environ["USERPROFILE"] = str(path)
        os.environ["HOME"] = str(path)

    def test_same_drive_within_max_up_is_relative_with_forward_slash(self):
        target = self.tmp / "總" / "來源" / "公司行政"
        target.mkdir(parents=True)
        self.assertEqual(paths.compress_spec(target, self.vault, 4), "../來源/公司行政")
        self.assertEqual(paths.compress_spec(self.tmp / "別處", self.vault, 4), "../../別處")

    def test_over_max_up_under_home_gives_tilde(self):
        self.set_home(self.tmp)
        target = self.tmp / "Documents" / "x"
        self.assertEqual(paths.compress_spec(target, self.vault, 1), "~/Documents/x")

    def test_over_max_up_outside_home_gives_absolute(self):
        other = Path(tempfile.mkdtemp(prefix="KbHome-")).resolve()
        try:
            self.set_home(other)
            target = self.tmp / "Documents" / "x"
            got = paths.compress_spec(target, self.vault, 1)
            self.assertEqual(got, target.as_posix())
            self.assertNotIn("\\", got)
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_different_drive_gives_absolute(self):
        if os.name != "nt":
            self.skipTest("不同磁碟只在 Windows 模擬")
        drive = str(self.vault)[0]
        other = "D" if drive.upper() != "D" else "E"
        target = f"{other}:\\share\\客戶"
        try:
            os.path.relpath(target, str(self.vault))
        except ValueError:
            pass
        else:
            self.skipTest("relpath 沒有丟 ValueError，無法模擬不同磁碟")
        self.assertEqual(paths.compress_spec(target, self.vault, 4), f"{other}:/share/客戶")

    def test_is_abs_spec(self):
        for spec in ("D:/x", "//nas/x", "C:\\x"):
            with self.subTest(spec=spec):
                self.assertTrue(paths.is_abs_spec(spec))
        for spec in ("../x", "~/x", "${X}/x", "x"):
            with self.subTest(spec=spec):
                self.assertFalse(paths.is_abs_spec(spec))


class LongPath(unittest.TestCase):
    r"""清冊題：Windows 加 \\?\ 前綴（UNC 是 \\?\UNC\），非 Windows 原樣（I-017）。"""

    @unittest.skipUnless(os.name == "nt", "Windows 才加前綴")
    def test_drive_path_gets_prefix(self):
        self.assertEqual(paths.long_path("C:/x/raw/a.txt"), r"\\?\C:\x\raw\a.txt")

    @unittest.skipUnless(os.name == "nt", "Windows 才加前綴")
    def test_unc_gets_unc_prefix(self):
        self.assertEqual(paths.long_path(r"\\server\share\a.txt"), r"\\?\UNC\server\share\a.txt")

    @unittest.skipUnless(os.name == "nt", "Windows 才加前綴")
    def test_already_prefixed_kept(self):
        self.assertEqual(paths.long_path(r"\\?\C:\x"), r"\\?\C:\x")

    @unittest.skipUnless(os.name == "nt", "Windows 才加前綴")
    def test_over_260_chars_can_be_written_and_read(self):
        tmp = Path(tempfile.mkdtemp(prefix="kblong-"))
        try:
            deep = tmp
            for i in range(30):
                deep = deep / f"資料夾{i:02d}_很長的名字"
            os.makedirs(paths.long_path(deep))
            target = deep / "a.txt"
            self.assertGreater(len(str(target)), 260)
            with open(paths.long_path(target), "w", encoding="utf-8") as f:
                f.write("x")
            with open(paths.long_path(target), encoding="utf-8") as f:
                self.assertEqual(f.read(), "x")
        finally:
            shutil.rmtree(paths.long_path(tmp), ignore_errors=True)

    @unittest.skipIf(os.name == "nt", "非 Windows 原樣")
    def test_posix_unchanged(self):
        self.assertEqual(paths.long_path("/x/raw/a.txt"), "/x/raw/a.txt")


class SourcesUnresolvedShape(unittest.TestCase):
    """審閱裁定 2：unresolved 每項是 {alias, spec, id, kind}，kind 是 source｜exclude。"""

    def test_unresolved_items_are_dicts_with_kind(self):
        from kb._lib import sources as sources_lib
        os.environ.pop("KB_TEST_NOPE", None)
        tmp = Path(tempfile.mkdtemp(prefix="kbsrc-"))
        try:
            (tmp / "raw").mkdir()
            data = {"schema_version": 1, "sources": [
                {"id": "src-0001", "alias": "外接", "spec": "${KB_TEST_NOPE}/x", "excludes": []},
                {"id": "src-0002", "alias": "原檔", "spec": str(tmp / "raw"),
                 "excludes": [{"spec": "${KB_TEST_NOPE}/y", "kind": "hidden"}]},
                {"alias": "舊的", "spec": "${KB_TEST_NOPE}/z"},
            ]}
            s = sources_lib.Sources(str(tmp), data)
            self.assertEqual(s.unresolved, [
                {"alias": "外接", "spec": "${KB_TEST_NOPE}/x", "id": "src-0001", "kind": "source"},
                {"alias": "原檔", "spec": "${KB_TEST_NOPE}/y", "id": "src-0002", "kind": "exclude"},
                {"alias": "舊的", "spec": "${KB_TEST_NOPE}/z", "id": "", "kind": "source"},
            ])
            self.assertEqual([e["alias"] for e in s.entries], ["原檔"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
