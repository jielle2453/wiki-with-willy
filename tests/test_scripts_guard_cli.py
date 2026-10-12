"""kb guard decide：三種輸出（allow／deny／pass）、--json 可 loads、不寫 ledger 與 status。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT, make_vault


def run_kb(*args, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


class GuardDecide(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_allow_line(self):
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "客戶案件" / "a.txt"), "--cwd", str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("allow Z9"), r.stdout)

    def test_deny_line(self):
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "行政" / "薪資" / "s.xlsx"), "--cwd", str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("deny Z10 excluded "), r.stdout)
        self.assertIn("這個夾你說過不讀", r.stdout)

    def test_pass_line(self):
        r = run_kb("guard", "decide", "--tool", "Read", "--path", "C:/Windows/notepad.exe", "--cwd", str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "pass")

    def test_json_output(self):
        r = run_kb("guard", "decide", "--tool", "Bash", "--command", f"cat {self.raw}/行政/薪資/s.xlsx",
                   "--cwd", str(self.V), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["decision"], "deny")
        self.assertEqual(data["zone"], "Z10")
        self.assertEqual(data["reason_code"], "excluded")
        self.assertEqual(set(data), {"decision", "zone", "reason_code", "reason"})

    def test_cwd_defaults_to_process_cwd_and_outside_vault_is_pass(self):
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "行政" / "薪資" / "s.xlsx"), cwd=self.tmp)
        self.assertEqual(r.stdout.strip(), "pass")
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "行政" / "薪資" / "s.xlsx"), cwd=self.V)
        self.assertTrue(r.stdout.startswith("deny Z10"), r.stdout)

    def test_dry_run_writes_nothing(self):
        run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "行政" / "薪資" / "s.xlsx"), "--cwd", str(self.V))
        self.assertFalse((self.V / ".system" / "ledger.tsv").exists())
        self.assertFalse((self.V / ".system" / "guard-status").exists())

    def test_help_lists_guard(self):
        r = run_kb("--help")
        self.assertIn("guard", r.stdout)


if __name__ == "__main__":
    unittest.main()
