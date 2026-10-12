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


if __name__ == "__main__":
    unittest.main()
