"""kb journal recent／render 黑箱：subprocess 跑 kb.py，cwd 在 vault。recent 空、n、程式段前綴、還沒結束、--json；
render 指定月與全部、手改被蓋回、--json。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import FIXTURES, KB, ROOT, make_vault
from kb._lib import journal  # noqa: E402


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy_fixture(self, month="2026-10"):
        dst = journal.jsonl_path(self.V, month)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "journal" / "2026-10.jsonl", dst)
        return dst


class Recent(Base):
    def test_empty(self):
        r = run_kb("journal", "recent", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "還沒有工作日誌"), r.stderr)

    def test_lines_newest_first_with_program_prefix_and_in_progress(self):
        journal.record_program(self.V, "建立知識庫：建了 9 項", [], "init")
        prog = journal.recent(self.V, 1)[0]
        journal.open_turn(self.V, {"turn": prog["turn"][:15] + "-test", "session_id": "s",
                                   "started": prog["time"], "prompt_head": "改一下"})
        r = run_kb("journal", "recent", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = prog["time"].replace("T", " ")
        self.assertEqual(r.stdout.splitlines(), [f"{t}｜做了：（還沒結束）", f"{t}｜程式｜做了：建立知識庫：建了 9 項"])

    def test_n_limits_and_json_length(self):
        for i in range(5):
            journal.record_program(self.V, f"第 {i} 段", [], "folders add")
        r = run_kb("journal", "recent", "--n", "3", cwd=self.V)
        self.assertEqual(len(r.stdout.splitlines()), 3)
        self.assertTrue(r.stdout.splitlines()[0].endswith("｜程式｜做了：第 4 段"))
        data = json.loads(run_kb("journal", "recent", "--n", "3", "--json", cwd=self.V).stdout)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["note"], "第 4 段")

    def test_outside_vault_exits_2(self):
        self.assertEqual(run_kb("journal", "recent", cwd=self.tmp).returncode, 2)

    def test_acceptance_34_n_must_be_positive(self):
        """測試後修正 L4：--n 0、-1 是 argparse 錯誤，exit 2。"""
        for n in ("0", "-1"):
            r = run_kb("journal", "recent", "--n", n, cwd=self.V)
            self.assertEqual(r.returncode, 2, n)
            self.assertIn("--n", r.stderr)


class Render(Base):
    def test_render_month_and_all(self):
        self.copy_fixture("2026-10")
        self.copy_fixture("2026-09")
        r = run_kb("journal", "render", "2026-10", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已重寫 1 個月"), r.stderr)
        self.assertEqual(journal.md_path(self.V, "2026-10").read_bytes(), (FIXTURES / "journal" / "2026-10.md").read_bytes())
        self.assertFalse(journal.md_path(self.V, "2026-09").exists())
        r = run_kb("journal", "render", cwd=self.V)
        self.assertEqual(r.stdout.strip(), "已重寫 2 個月")
        self.assertTrue(journal.md_path(self.V, "2026-09").exists())

    def test_hand_edit_is_overwritten_and_rerender_is_stable(self):
        self.copy_fixture()
        run_kb("journal", "render", cwd=self.V)
        md = journal.md_path(self.V, "2026-10")
        before = md.read_bytes()
        run_kb("journal", "render", cwd=self.V)
        self.assertEqual(md.read_bytes(), before)
        md.write_text(md.read_text(encoding="utf-8").replace("沒有改動", "手改"), encoding="utf-8")
        run_kb("journal", "render", cwd=self.V)
        self.assertEqual(md.read_bytes(), before)

    def test_json(self):
        self.copy_fixture()
        r = run_kb("journal", "render", "--json", cwd=self.V)
        self.assertEqual(json.loads(r.stdout), {"months": ["2026-10"]})

    def test_month_without_jsonl(self):
        r = run_kb("journal", "render", "2026-01", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已重寫 0 個月"))
        self.assertFalse(journal.md_path(self.V, "2026-01").exists())

    def test_bad_month_exit_2(self):
        r = run_kb("journal", "render", "十月", cwd=self.V)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
