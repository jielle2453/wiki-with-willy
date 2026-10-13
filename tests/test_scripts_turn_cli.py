"""kb turn current／approve 黑箱：subprocess 跑 kb.py，cwd 在 vault。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT, make_vault
from kb._lib import turn  # noqa: E402

SID = "test-sid"


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Current(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prints_turn_prompt_count_and_limit(self):
        t = turn.start(self.V, SID, "改一下")
        turn.backup(self.V, "案子/信義王/信義王.md", t["turn"])
        r = run_kb("turn", "current", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"{t['turn']}｜問：「改一下」｜已備份 1 檔｜未解除上限")

    def test_json_has_four_keys(self):
        t = turn.start(self.V, SID, "改一下")
        r = run_kb("turn", "current", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(set(data), {"turn", "prompt_head", "backed_up", "approved"})
        self.assertEqual(data["turn"], t["turn"])
        self.assertEqual(data["backed_up"], 0)
        self.assertFalse(data["approved"])

    def test_no_turn_yet_starts_one(self):
        r = run_kb("turn", "current", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / ".system" / "turns" / "current.json").exists())
        self.assertIn("已備份 0 檔", r.stdout)

    def test_outside_vault_exits_2(self):
        r = run_kb("turn", "current", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不在知識庫", r.stdout + r.stderr)


class Approve(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approve_sets_flag_and_prints(self):
        turn.start(self.V, SID, "全部加一行")
        r = run_kb("turn", "approve", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "這一題解除綜合卡張數上限")
        self.assertTrue(turn.current(self.V, SID)["approved"])
        self.assertTrue(turn.current(self.V)["approved"])
        r = run_kb("turn", "current", cwd=self.V)
        self.assertIn("已解除上限", r.stdout)

    def test_approve_json(self):
        t = turn.start(self.V, SID, "全部加一行")
        r = run_kb("turn", "approve", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(data, {"turn": t["turn"], "approved": True})


if __name__ == "__main__":
    unittest.main()
