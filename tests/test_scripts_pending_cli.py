"""待處理接清冊與 kb pending（ingest 製卡第 5 點；驗收 11、13、14）：真的跑 CLI。
inventory scan 後依清冊狀態開／關 missing_file、modified 自動關 unreadable、程式段 changed 動態加兩檔、pending 壞了清冊照寫；
kb pending list／resolve／dismiss、不存在的 id exit 3。"""
import json
import shutil
import unittest

from tests import _ooxml
from tests.test_lib_inventory_scan import make_inv_vault
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import journal, pending, turn  # noqa: E402

GONE = "客戶案件/大安陳/會議紀錄_1.docx"
PENDING_FILES = {".system/pending.json", "系統/待處理.md"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()
        self.assertEqual(self.kb("inventory", "scan").returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb(*args, cwd=self.V)

    def items(self):
        return pending.load(self.V)["items"]

    def md(self):
        return (self.V / "系統" / "待處理.md").read_text(encoding="utf-8")

    def last_program(self):
        rows = [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]
        return rows[-1]

    def entry_of(self, rel):
        inv = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))
        return next(e for e in inv["entries"] if any(l["uri"] == "file://" + rel for l in e["locations"]))


class ScanMissing(Base):
    def test_first_scan_without_items_has_plain_changed(self):
        self.assertEqual({c["path"] for c in self.last_program()["changed"]}, {".system/inventory.json"})
        self.assertFalse((self.V / ".system" / "pending.json").exists())

    def test_missing_opens_then_found_closes(self):
        data = (self.raw / GONE).read_bytes()
        (self.raw / GONE).unlink()
        r = self.kb("inventory", "scan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (item,) = self.items()
        self.assertEqual((item["kind"], item["status"]), ("missing_file", "open"))
        self.assertEqual(item["file_ids"], [self.entry_of(GONE)["id"]])
        self.assertIn("## 原檔不見（1）", self.md())
        self.assertIn("「會議紀錄_1.docx」（客戶案件/大安陳）不見了。是你刪的嗎？", self.md())
        self.assertTrue(PENDING_FILES <= {c["path"] for c in self.last_program()["changed"]})
        self.kb("inventory", "scan")
        self.assertEqual(len(self.items()), 1)
        self.assertEqual({c["path"] for c in self.last_program()["changed"]}, {".system/inventory.json"})
        (self.raw / GONE).write_bytes(data)
        self.kb("inventory", "scan")
        (item,) = self.items()
        self.assertEqual((item["status"], item["how"]), ("done", "找回來了"))
        self.assertNotIn("會議紀錄_1.docx", self.md())

    def test_broken_pending_does_not_block_inventory(self):
        (self.V / ".system" / "pending.json").write_text("{", encoding="utf-8")
        (self.raw / GONE).unlink()
        r = self.kb("inventory", "scan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.entry_of(GONE)["status"], "missing")
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)

    def test_modified_closes_unreadable(self):
        """驗收 14：讀不到的檔換了內容，scan 後那條 done「檔案換了，重新讀」。"""
        x = self.raw / "客戶案件" / "x.doc"
        _ooxml.cfb(x)
        self.kb("inventory", "scan")
        r = self.kb("convert", str(x))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertEqual(len(self.items()), 1)
        x.write_bytes(_ooxml.CFB_MAGIC + b"\x01" * 600)
        self.kb("inventory", "scan")
        (item,) = self.items()
        self.assertEqual((item["status"], item["how"]), ("done", "檔案換了，重新讀"))
        todo = json.loads(self.kb("cards", "todo", "--json").stdout)
        self.assertIn(item["file_ids"][0], [n["id"] for n in todo["new"]])


class PendingCli(Base):
    def setUp(self):
        super().setUp()
        (self.raw / GONE).unlink()
        self.kb("inventory", "scan")
        self.pid = self.items()[0]["id"]
        turn.start(self.V, None, "")

    def test_list(self):
        r = self.kb("pending", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"- {self.pid} 原檔不見 「會議紀錄_1.docx」（客戶案件/大安陳）不見了"])
        data = json.loads(self.kb("pending", "list", "--json").stdout)
        self.assertEqual([i["id"] for i in data], [self.pid])

    def test_resolve(self):
        r = self.kb("pending", "resolve", self.pid, "--how", "先留著")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已記下"), r.stderr)
        (item,) = self.items()
        self.assertEqual((item["status"], item["how"]), ("done", "先留著"))
        self.assertNotIn("會議紀錄_1.docx", self.md())
        prog = self.last_program()
        self.assertEqual(prog["command"], "pending resolve")
        self.assertEqual(prog["note"], f"待處理 {self.pid}：先留著")
        self.assertEqual(self.kb("pending", "list").stdout.strip(), "目前沒有等你決定的事")

    def test_dismiss(self):
        r = self.kb("pending", "dismiss", self.pid)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已記下"), r.stderr)
        self.assertEqual(self.items()[0]["status"], "dismissed")
        self.assertEqual(self.last_program()["note"], f"待處理 {self.pid}：不用管")

    def test_unknown_id_exit_3(self):
        for args in (("resolve", "p-00000000", "--how", "x"), ("dismiss", "p-00000000")):
            r = self.kb("pending", *args)
            with self.subTest(args=args):
                self.assertEqual(r.returncode, 3)
                self.assertEqual(r.stdout.splitlines()[0], "沒有這條待處理：p-00000000")


if __name__ == "__main__":
    unittest.main()
