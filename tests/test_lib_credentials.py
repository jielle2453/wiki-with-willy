"""_lib/credentials：憑證檔名樣式（D-079 補）。match_segment 不分大小寫、「憑證」不命中；scan_dir 命中回相對路徑、上限截斷、壞夾略過；
scan_all 數檔、收全部命中、碰到標記檔就停。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._guard_fixture import SCRIPTS  # noqa: F401
from kb._lib import credentials  # noqa: E402


class MatchSegment(unittest.TestCase):
    def test_hits_are_case_insensitive(self):
        hits = [".env", ".env.local", "x.pem", "k.ppk", "db.kdbx", "id_rsa", "id_rsa.pub", "id_ed25519",
                "網站密碼.txt", "passwd.bak", "my-secret.json", "credentials.json", "PASSWORD.TXT", "Secret", "密碼"]
        for name in hits:
            with self.subTest(name=name):
                self.assertEqual(credentials.match_segment([name]), name)

    def test_non_hits(self):
        for name in ("會計憑證.xlsx", "a.txt", "environment.md", "合約.docx", "report.pdf", "", "c:", "secretary"[:0] + "x"):
            with self.subTest(name=name):
                self.assertIsNone(credentials.match_segment([name]))

    def test_first_hit_among_segments_is_returned(self):
        self.assertEqual(credentials.match_segment(["raw", "密碼", "x.txt"]), "密碼")
        self.assertEqual(credentials.match_segment(["raw", "a", ".ENV"]), ".ENV")
        self.assertIsNone(credentials.match_segment(["raw", "客戶案件", "a.txt"]))
        self.assertIsNone(credentials.match_segment([]))

    def test_patterns_do_not_include_voucher(self):
        self.assertNotIn("*憑證*", credentials.CREDENTIAL_PATTERNS)
        self.assertIn("*密碼*", credentials.CREDENTIAL_PATTERNS)


class ScanDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbcred-")).resolve()
        (self.tmp / "a" / "b").mkdir(parents=True)
        (self.tmp / "a" / "b" / "r.docx").write_text("x", encoding="utf-8")
        (self.tmp / "a" / "c.txt").write_text("x", encoding="utf-8")
        (self.tmp / "z.md").write_text("x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_hit(self):
        self.assertEqual(credentials.scan_dir(self.tmp, 100), (None, False))

    def test_hit_returns_relative_path_with_forward_slash(self):
        (self.tmp / "a" / "b" / "網站密碼.txt").write_text("x", encoding="utf-8")
        hit, truncated = credentials.scan_dir(self.tmp, 100)
        self.assertEqual(hit, "a/b/網站密碼.txt")
        self.assertFalse(truncated)

    def test_folder_name_counts_as_hit(self):
        (self.tmp / "a" / "secret").mkdir()
        (self.tmp / "a" / "secret" / "k.txt").write_text("x", encoding="utf-8")
        hit, _t = credentials.scan_dir(self.tmp, 100)
        self.assertEqual(hit, "a/secret")

    def test_truncated_when_over_max_entries(self):
        (self.tmp / "a" / "b" / ".env").write_text("x", encoding="utf-8")
        hit, truncated = credentials.scan_dir(self.tmp, 1)
        self.assertIsNone(hit)
        self.assertTrue(truncated)
        hit, truncated = credentials.scan_dir(self.tmp, 1000)
        self.assertEqual((hit, truncated), ("a/b/.env", False))

    def test_missing_or_file_path_is_skipped_not_raised(self):
        self.assertEqual(credentials.scan_dir(self.tmp / "沒有", 100), (None, False))
        self.assertEqual(credentials.scan_dir(self.tmp / "z.md", 100), (None, False))

    def test_scan_all_counts_files_and_collects_every_hit(self):
        (self.tmp / "a" / "b" / ".env").write_text("x", encoding="utf-8")
        (self.tmp / "網站密碼.txt").write_text("x", encoding="utf-8")
        hits, files, truncated, marker = credentials.scan_all(self.tmp)
        self.assertEqual(sorted(hits), ["a/b/.env", "網站密碼.txt"])
        self.assertEqual(files, 5)
        self.assertFalse(truncated)
        self.assertIsNone(marker)

    def test_scan_all_stops_at_marker_dir(self):
        (self.tmp / "a" / "內" / ".system").mkdir(parents=True)
        (self.tmp / "a" / "內" / ".system" / "sources.json").write_text("{}", encoding="utf-8")
        _hits, _files, _t, marker = credentials.scan_all(self.tmp, stop_marker=".system/sources.json")
        self.assertEqual(Path(marker), self.tmp / "a" / "內")

    def test_scan_all_with_limit_truncates(self):
        _hits, _files, truncated, _m = credentials.scan_all(self.tmp, max_entries=2)
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
