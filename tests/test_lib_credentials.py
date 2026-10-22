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

    def test_looks_like_filename(self):
        for s in ("a.txt", ".env", "id_rsa", "ID_RSA.pub", "id_ed25519", "網站密碼.txt"):
            with self.subTest(s=s):
                self.assertTrue(credentials.looks_like_filename(s))
        for s in ("passwords", "secret", "密碼", "PASSWORD", "", "id_dsa"):
            with self.subTest(s=s):
                self.assertFalse(credentials.looks_like_filename(s))

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

    def test_skip_folders_are_not_entered(self):
        (self.tmp / "a" / "b" / ".env").write_text("x", encoding="utf-8")
        skip = {str(self.tmp / "a" / "b").replace("\\", "/")}
        if os.name == "nt":
            skip = {s.lower() for s in skip}
        self.assertEqual(credentials.scan_dir(self.tmp, 100, skip=skip), (None, False))
        self.assertEqual(credentials.scan_dir(self.tmp, 100), ("a/b/.env", False))
        (self.tmp / "網站密碼.txt").write_text("x", encoding="utf-8")
        self.assertEqual(credentials.scan_dir(self.tmp, 100, skip=skip), ("網站密碼.txt", False))

    def test_skip_list_can_name_single_files(self):
        (self.tmp / "a" / "密碼單.txt").write_text("x", encoding="utf-8")
        skip = {str(self.tmp / "a" / "密碼單.txt").replace("\\", "/")}
        if os.name == "nt":
            skip = {s.lower() for s in skip}
        self.assertEqual(credentials.scan_dir(self.tmp, 100, skip=skip), (None, False))
        self.assertEqual(credentials.scan_dir(self.tmp, 100), ("a/密碼單.txt", False))

    def test_dot_claude_is_not_special_only_skip_list_counts(self):
        """第四輪 Q4：跳的是呼叫端給的夾（總資料夾根），不是 .claude／.system 這些夾名。"""
        vault = self.tmp / "vault"
        (vault / ".system" / "versions").mkdir(parents=True)
        for i in range(300):
            (vault / ".system" / "versions" / f"f{i}.json").write_text("{}", encoding="utf-8")
        (self.tmp / "raw" / "proj" / ".claude").mkdir(parents=True)
        (self.tmp / "raw" / "proj" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        skip = {str(vault).replace("\\", "/")}
        if os.name == "nt":
            skip = {s.lower() for s in skip}
        hit, truncated = credentials.scan_dir(self.tmp, 100, skip=skip)
        self.assertEqual((hit, truncated), ("raw/proj/.claude/.credentials.json", False))
        hit, truncated = credentials.scan_dir(self.tmp, 100)
        self.assertTrue(truncated or hit, (hit, truncated))

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


class MaskSecrets(unittest.TestCase):
    """第 5b 節（D-081、B-027）的舊案例，D-187 改成只遮值（關鍵字、分隔、後面的話照留）；完整的在 test_lib_mask。"""

    def test_cases_from_acceptance_62(self):
        m = credentials.mask_secrets
        M = credentials.MASK
        self.assertEqual(m("密碼是abc 123，謝謝"), f"密碼是{M} 123，謝謝")  # 值遮到空白為止（D-187）
        self.assertEqual(m("my password is hunter2"), f"my password is {M}")
        self.assertEqual(m("密碼 abc。謝謝"), f"密碼 {M}。謝謝")
        self.assertEqual(m("密碼是多少？abc"), "密碼是多少？abc")  # 疑問詞不是值
        self.assertEqual(m("Token: Bearer eyJabc"), f"Token: {M}")
        self.assertEqual(m("PIN 碼 4821"), f"PIN 碼 {M}")
        self.assertEqual(m("今天天氣好"), "今天天氣好")
        self.assertEqual(m(""), "")
        self.assertEqual(m("password:\nhunter2"), f"password:\n{M}")
        self.assertEqual(m("密碼：abc\n\n另外"), f"密碼：{M}\n\n另外")
        self.assertEqual(m("第一行密碼 a\n第二行沒有，好"), f"第一行密碼 {M}\n第二行沒有，好")

    def test_keywords_case_insensitive_and_bare_keyword_untouched(self):
        m = credentials.mask_secrets
        self.assertEqual(m("secret"), "secret")
        self.assertNotIn("abcd", m("TOKEN=abcd"))
        for text in ("typing fast", "spinach 1", "stopwatch 2", "ottoman 3"):  # 短字 pw、pin、otp 要整字
            with self.subTest(text=text):
                self.assertEqual(m(text), text)
        for w in ("密碼", "帳密", "帳號", "password", "passwd", "pw", "token", "金鑰", "secret", "api key", "apikey",
                  "api_key", "api-key", "密码", "passphrase", "pin", "pin碼", "驗證碼", "otp"):
            self.assertIn(w, credentials.SECRET_WORDS)


if __name__ == "__main__":
    unittest.main()
