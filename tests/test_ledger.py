"""留痕：deny 一次 ledger 多一行且欄位對；allow 不寫；guard-status 同一天跑兩次只有一個日期；log_error 落點。"""
import json
import os
import platform
import shutil
import unittest
from datetime import date

from tests._guard_fixture import make_vault, payload
from tests.test_hooks_guard import run_hook
from kb._lib import ledger  # noqa: E402

HEADER = "time\ttool\tdecision\tzone\treason_code\tpath_or_command_head"


class ViaHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.ledger = self.V / ".system" / "ledger.tsv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        return self.ledger.read_text(encoding="utf-8").splitlines() if self.ledger.exists() else []

    def test_deny_appends_one_row_with_six_fields(self):
        run_hook(payload("Read", self.raw / "行政" / "薪資" / "s.xlsx", self.V))
        lines = self.lines()
        self.assertEqual(lines[0], HEADER)
        self.assertEqual(len(lines), 2)
        cols = lines[1].split("\t")
        self.assertEqual(len(cols), 6)
        self.assertEqual(cols[1:5], ["Read", "deny", "Z10", "excluded"])
        self.assertIn("s.xlsx", cols[5])
        self.assertTrue(cols[0].startswith(date.today().isoformat()))

    def test_allow_and_pass_do_not_write(self):
        run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        run_hook(payload("Read", "C:/Windows/notepad.exe", self.V))
        self.assertEqual(self.lines(), [])

    def test_unparsed_row(self):
        run_hook(payload("Bash", "rm $X", self.V))
        cols = self.lines()[1].split("\t")
        self.assertEqual(cols[1:5], ["Bash", "deny", "", "unparsed"])
        self.assertEqual(cols[5], "rm $X")

    def test_command_head_is_capped_at_120(self):
        run_hook(payload("Bash", "rm " + "$X" * 200, self.V))
        self.assertLessEqual(len(self.lines()[1].split("\t")[5]), 120)

    def test_guard_error_row(self):
        (self.V / ".system" / "sources.json").write_text("{bad", encoding="utf-8")
        run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        cols = self.lines()[1].split("\t")
        self.assertEqual(cols[1:5], ["Read", "deny", "", "guard_error"])

    def test_guard_status_written_once_per_day(self):
        run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        d = self.V / ".system" / "guard-status"
        files = list(d.glob("*.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"date", "python", "host", "plugin_version"})
        self.assertEqual(data["date"], date.today().isoformat())
        self.assertEqual(data["python"], platform.python_version())
        before = files[0].read_bytes()
        mtime = files[0].stat().st_mtime_ns
        run_hook(payload("Read", self.raw / "行政" / "薪資" / "s.xlsx", self.V))
        self.assertEqual(files[0].read_bytes(), before)
        self.assertEqual(files[0].stat().st_mtime_ns, mtime)
        self.assertEqual(len(list(d.glob("*.json"))), 1)


class Functions(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_creates_header_then_rows(self):
        row = {"tool": "Write", "decision": "deny", "zone": "Z9", "reason_code": "readonly_source", "head": "a\tb\nc"}
        ledger.append(self.V, row)
        ledger.append(self.V, row)
        lines = (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], HEADER)
        self.assertEqual(lines[2].split("\t")[5], "a b c")

    def test_log_error_without_vault_goes_to_temp(self):
        ledger.log_error(None, "ValueError: x")
        p = ledger.fallback_error_log()
        self.assertTrue(p.exists())
        self.assertTrue(p.read_text(encoding="utf-8").rstrip().endswith("\tguard\tValueError: x"))

    def test_log_error_with_vault(self):
        ledger.log_error(self.V, "KeyError: 'sources'")
        text = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\tguard\tKeyError: 'sources'\n$")

    def test_touch_status_same_day_noop(self):
        ledger.touch_status(self.V)
        files = list((self.V / ".system" / "guard-status").glob("*.json"))
        self.assertEqual(len(files), 1)
        before = files[0].read_bytes()
        ledger.touch_status(self.V)
        self.assertEqual(files[0].read_bytes(), before)
        files[0].write_text(json.dumps({"date": "2000-01-01"}), encoding="utf-8")
        ledger.touch_status(self.V)
        self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["date"], date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
