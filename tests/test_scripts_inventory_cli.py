"""kb inventory scan 黑箱（I-098 跑真的 CLI）：印給人的四行、--json、--source、exit code、寫前備份、程式段、遷移、
清冊壞掉 exit 3、找不到知識庫 exit 3、env.json 清掉找回來的來源。fixture 是 tests/fixtures/inventory。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT
from tests.test_lib_inventory_scan import FIX, make_inv_vault
from kb._lib import env, journal, turn  # noqa: E402


def run_kb(*args, cwd):
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(cwd), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, *args, cwd=None):
        return run_kb("inventory", "scan", *args, cwd=cwd or self.V)

    def inventory(self):
        return json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))

    def program_rows(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]


class Scan(Base):
    def test_human_lines(self):
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "清冊已更新：總計 14 份（客戶案件 5、報價與合約 2、公司行政 3、知識庫 2、收件匣 1、AI 產出 1）")
        self.assertEqual(lines[1], "這次：新增 13、修改 0、搬動 0、不見 0；同一份內容在多處 1 份")
        self.assertEqual(lines[2], "不算指紋：檔名像密碼或金鑰 1（公司行政/薪資/網銀密碼.txt）、占位檔 0、超過大小 0；開不了 0")
        self.assertEqual(len(lines), 3)

    def test_second_run_prints_two_lines(self):
        self.scan()
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").unlink()
        r = self.scan()
        lines = r.stdout.splitlines()
        self.assertEqual(lines[1], "這次：新增 0、修改 0、搬動 0、不見 1")
        self.assertEqual(len(lines), 2)

    def test_json_is_report_without_entries(self):
        r = self.scan("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertNotIn("entries", data)
        self.assertEqual(data["total"], 14)
        self.assertEqual(len(data["added"]), 13)
        self.assertEqual(data["read_counts"], {"placeholder": 0, "credential": 1, "too_large": 0, "error": 0})
        self.assertEqual(data["missing_sources"], [])
        self.assertFalse(data["incomplete"])

    def test_backup_and_program_line(self):
        self.scan()
        t2 = turn.start(self.V, None, "")  # 終端機連跑兩次會落在同一個回合；開新回合才看得到第二次的備份
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / ".system" / "versions" / t2["turn"] / "files" / ".system" / "inventory.json").is_file())
        backups = list((self.V / ".system" / "versions").glob("*/files/.system/inventory.json"))
        self.assertEqual(len(backups), 1)
        rows = self.program_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["command"], "inventory scan")
        self.assertEqual(rows[0]["note"], "清冊掃描：新增 13、修改 0、不見 0（總計 14 份）")
        self.assertEqual(rows[0]["changed"], [{"path": ".system/inventory.json", "action": "modify"}])

    def test_source_option(self):
        r = self.scan("--source", "報價與合約", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["total"], 2)
        self.assertEqual({l["uri"].split("/")[2] for e in self.inventory()["entries"] for l in e["locations"]}, {"報價與合約"})
        r = self.scan("--source", "沒有這個")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.splitlines()[0], "沒有叫「沒有這個」的來源")

    def test_missing_source_line_and_env_cleared_when_back(self):
        (self.raw / "報價與合約").rename(self.raw / "報價與合約_舊")
        env.mark_missing(self.V, ["src-5b10"])
        r = self.scan()
        self.assertEqual(r.stdout.splitlines()[-1], "這台電腦找不到：報價與合約（../raw/報價與合約）")
        self.assertTrue(env.missing_reported(self.V, "src-5b10"))
        (self.raw / "報價與合約_舊").rename(self.raw / "報價與合約")
        self.scan()
        self.assertFalse(env.missing_reported(self.V, "src-5b10"))

    def test_v1_migration_and_schema_3_exit_3(self):
        shutil.copy2(FIX / "inventory_v1.json", self.V / ".system" / "inventory.json")
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = self.inventory()
        self.assertEqual(data["schema_version"], 2)
        out = [e for e in data["entries"] if e["id"] == "out-1a2b3c4d"][0]
        self.assertIsInstance(out["locations"][0], dict)
        for key in ("first_seen", "read", "missing_since"):
            self.assertIn(key, out)
        (self.V / ".system" / "inventory.json").write_text('{"schema_version": 3, "entries": []}', encoding="utf-8")
        r = self.scan()
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stdout.splitlines()[0].startswith("清冊讀不到："), r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_outside_vault_exit_3_and_vault_option(self):
        r = self.scan(cwd=self.tmp)
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("Traceback", r.stderr)
        r = self.scan("--vault", str(self.V), "--json", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["total"], 14)

    def test_help_lists_inventory_and_changes(self):
        r = run_kb("--help", cwd=self.V)
        self.assertIn("inventory", r.stdout)
        self.assertIn("changes", r.stdout)


if __name__ == "__main__":
    unittest.main()
