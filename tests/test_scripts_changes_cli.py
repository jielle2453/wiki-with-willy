"""kb changes 黑箱（I-098 跑真的 CLI）：沒有清冊當全部新增、各類一行與每類 10 個、--all、搬動行、之前就不見的一行（裁定 20）、
找不到來源、incomplete 註記、--json、--quick；只讀不寫：跑前後 .system/ 一個檔都不動、沒有新回合與程式段（驗收 8）。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT
from tests.test_lib_inventory_scan import make_inv_vault


def run_kb(*args, cwd):
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(cwd), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class Changes(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def changes(self, *args):
        return run_kb("changes", *args, cwd=self.V)

    def applied(self):
        r = run_kb("inventory", "scan", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_inventory_means_everything_is_new(self):
        r = self.changes("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)["added"]), 13)
        lines = self.changes().stdout.splitlines()
        self.assertEqual(lines[0], "跟上次掃描比（還沒套用）：")
        self.assertTrue(lines[1].startswith("- 新增 13 份："), lines[1])
        self.assertTrue(lines[1].endswith("……等 13 份"), lines[1])
        self.assertEqual(lines[1].count("、"), 9 + 1)  # 10 個名字 9 個頓號，加檔名裡本來就有的一個
        self.assertEqual(lines[-1], "要套用請跑 kb inventory scan。")
        full = self.changes("--all").stdout.splitlines()[1]
        self.assertNotIn("……等", full)
        self.assertIn("客戶案件/大安陳/[草稿] 會議紀錄、第一次（修）.txt", full)

    def test_nothing_changed(self):
        self.applied()
        r = self.changes()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "跟上次掃描時相同，沒有變動。")

    def test_each_kind(self):
        self.applied()
        (self.raw / "客戶案件" / "新案.txt").write_text("新案\n", encoding="utf-8")
        (self.raw / "報價與合約" / "合約範本.txt").write_text("改過的合約範本，長度不同。\n", encoding="utf-8")
        shutil.move(self.raw / "客戶案件" / "內湖李" / "估價單.pdf", self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        (self.raw / "客戶案件" / "大安陳" / "會議紀錄_1.docx").unlink()
        r = self.changes()
        self.assertEqual(r.stdout.splitlines(), [
            "跟上次掃描比（還沒套用）：",
            "- 新增 1 份：客戶案件/新案.txt",
            "- 修改 1 份：報價與合約/合約範本.txt",
            "- 搬動或改名 1 份：客戶案件/內湖李/估價單.pdf → 客戶案件/信義王/估價單.pdf",
            "- 不見 1 份：客戶案件/大安陳/會議紀錄_1.docx",
            "要套用請跑 kb inventory scan。",
        ])

    def test_still_missing_has_own_line_after_apply(self):
        self.applied()
        (self.raw / "客戶案件" / "大安陳" / "會議紀錄_1.docx").unlink()
        self.applied()
        r = self.changes()
        self.assertEqual(r.stdout.splitlines(), [  # 審閱裁定 39：套用了也不會變，不印「要套用請跑」
            "跟上次掃描比（還沒套用）：",
            "- 之前就不見、還沒處理 1 份：客戶案件/大安陳/會議紀錄_1.docx",
        ])
        data = json.loads(self.changes("--json").stdout)
        self.assertEqual((data["missing"], data["still_missing"]), ([], ["file://客戶案件/大安陳/會議紀錄_1.docx"]))

    def test_missing_source_line(self):
        self.applied()
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        r = self.changes()
        self.assertIn("- 這台電腦找不到來源：客戶案件（../raw/客戶案件）", r.stdout.splitlines())
        data = json.loads(self.changes("--json").stdout)
        self.assertEqual(data["missing_sources"], [{"alias": "客戶案件", "spec": "../raw/客戶案件", "id": "src-3f9a"}])
        self.assertEqual(data["missing"], [])

    def test_quick_incomplete_note(self):
        self.applied()
        (self.V / ".system" / "config.json").write_text(json.dumps({"changes_quick_seconds": 0}), encoding="utf-8")
        r = self.changes("--quick")
        lines = r.stdout.splitlines()
        self.assertEqual(lines[:2], ["跟上次掃描比（還沒套用）：", "（來源很大還沒比完，數字是比到的部分）"])
        self.assertTrue(json.loads(self.changes("--quick", "--json").stdout)["incomplete"])
        self.assertFalse(json.loads(self.changes("--json").stdout)["incomplete"])

    def test_writes_nothing(self):
        """驗收 8：.system/ 任何檔不動、沒有新回合、沒有程式段。"""
        self.applied()
        (self.raw / "客戶案件" / "新案.txt").write_text("新案\n", encoding="utf-8")
        before = snapshot(self.V / ".system")
        for args in ((), ("--json",), ("--quick",), ("--all",)):
            r = self.changes(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(snapshot(self.V / ".system"), before)

    def test_writes_nothing_without_inventory(self):
        before = snapshot(self.V / ".system")
        self.changes()
        self.assertEqual(snapshot(self.V / ".system"), before)

    def test_broken_inventory_exit_3(self):
        (self.V / ".system" / "inventory.json").write_text("{bad", encoding="utf-8")
        r = self.changes()
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stdout.startswith("清冊讀不到："), r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_outside_vault_exit_3(self):
        r = run_kb("changes", cwd=self.tmp)
        self.assertEqual(r.returncode, 3)
        r = run_kb("changes", "--vault", str(self.V), "--json", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
