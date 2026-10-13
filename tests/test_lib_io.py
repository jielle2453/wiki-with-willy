"""_lib/io 的測試：原子寫入、UTF-8 無 BOM、LF、失敗不留暫存檔。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kb._lib import io as kio  # noqa: E402

BOM = b"\xef\xbb\xbf"


class JsonRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbio-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_then_read_is_identical_with_chinese(self):
        data = {"名稱": "林小姐", "項目": ["報價 v1", "合約"], "數": 3, "巢": {"a": None}}
        p = self.tmp / "sub" / "x.json"
        p.parent.mkdir()
        kio.write_json_atomic(p, data)
        self.assertEqual(kio.read_json(p), data)

    def test_file_is_utf8_without_bom_and_lf(self):
        p = self.tmp / "x.json"
        kio.write_json_atomic(p, {"中": "文", "list": [1, 2]})
        raw = p.read_bytes()
        self.assertFalse(raw.startswith(BOM))
        self.assertNotIn(b"\r", raw)
        self.assertIn("中".encode("utf-8"), raw)
        self.assertTrue(raw.endswith(b"\n"))

    def test_read_json_missing_returns_default(self):
        self.assertIsNone(kio.read_json(self.tmp / "nope.json"))
        self.assertEqual(kio.read_json(self.tmp / "nope.json", default={}), {})

    def test_failed_write_leaves_no_tmp(self):
        missing_dir = self.tmp / "does-not-exist"
        with self.assertRaises(OSError):
            kio.write_json_atomic(missing_dir / "x.json", {"a": 1})
        self.assertFalse(missing_dir.exists())
        self.assertEqual(list(self.tmp.glob("**/*.tmp")), [])


class TextAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbio-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_text_then_read_text(self):
        p = self.tmp / "t.md"
        kio.write_text_atomic(p, "第一行\n第二行\n")
        self.assertEqual(kio.read_text(p, "utf-8"), "第一行\n第二行\n")
        raw = p.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertFalse(raw.startswith(BOM))
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])


class Lock(unittest.TestCase):
    """io.FileLock：O_CREAT|O_EXCL 鎖檔；殘留超過 600 秒清掉；逾時丟 TimeoutError（不 sys.exit）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbio-"))
        self.target = self.tmp / "inventory.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lock_file_exists_while_held_and_removed_after(self):
        lock_path = self.tmp / "inventory.json.lock"
        with kio.FileLock(self.target):
            self.assertTrue(lock_path.exists())
        self.assertFalse(lock_path.exists())

    def test_second_holder_times_out_with_timeout_error(self):
        with kio.FileLock(self.target):
            with self.assertRaises(TimeoutError):
                with kio.FileLock(self.target, timeout=0.3):
                    pass

    def test_stale_lock_older_than_600s_is_cleared(self):
        import os
        import time
        lock_path = self.tmp / "inventory.json.lock"
        lock_path.write_text("", encoding="utf-8")
        old = time.time() - 700
        os.utime(lock_path, (old, old))
        with kio.FileLock(self.target, timeout=0.5):
            self.assertTrue(lock_path.exists())
        self.assertFalse(lock_path.exists())


class Retry(unittest.TestCase):
    """write_text_atomic：os.replace 被拒（PermissionError，I-018）重試最多 20 次、每次 0.1 秒。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbio-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retries_then_succeeds(self):
        from unittest import mock
        real = kio.os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("busy")
            return real(src, dst)

        with mock.patch.object(kio.os, "replace", flaky), mock.patch.object(kio.time, "sleep", lambda s: None):
            kio.write_text_atomic(self.tmp / "t.md", "x\n")
        self.assertEqual(calls["n"], 3)
        self.assertEqual((self.tmp / "t.md").read_text(encoding="utf-8"), "x\n")
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])

    def test_gives_up_after_20_and_leaves_no_tmp(self):
        from unittest import mock
        calls = {"n": 0}

        def always(src, dst):
            calls["n"] += 1
            raise PermissionError("busy")

        with mock.patch.object(kio.os, "replace", always), mock.patch.object(kio.time, "sleep", lambda s: None):
            with self.assertRaises(PermissionError):
                kio.write_text_atomic(self.tmp / "t.md", "x\n")
        self.assertEqual(calls["n"], 20)
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
