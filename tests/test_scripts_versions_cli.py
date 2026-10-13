"""kb versions list／revert 黑箱：subprocess 跑 kb.py，cwd 在 vault。
list 空、list 一回合、revert 還原修改、revert 移除新增、成品留著／刪掉、指定舊代號被拒 exit 3、退回後再退回、--json。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT, make_vault
from kb._lib import inventory, turn  # noqa: E402

SID = "test-sid"


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.card = self.V / "案子" / "信義王" / "信義王.md"
        self.card_rel = "案子/信義王/信義王.md"
        self.original = self.card.read_bytes()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def modify_card_in_turn(self, prompt="改一下", text="改過了\n"):
        """模擬一句話改了綜合卡：開回合、備份、真的改。回回合 dict。"""
        t = turn.start(self.V, SID, prompt)
        turn.backup(self.V, self.card_rel, t["turn"])
        self.card.write_text(text, encoding="utf-8")
        return t

    def new_turn(self, prompt="退回上一版"):
        return turn.start(self.V, SID, prompt)

    def versions_dir(self, turn_id):
        return self.V / ".system" / "versions" / turn_id


class List(Base):
    def test_empty(self):
        r = run_kb("versions", "list", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "還沒有任何改動紀錄")
        r = run_kb("versions", "list", "--json", cwd=self.V)
        self.assertEqual(json.loads(r.stdout), [])

    def test_one_turn(self):
        t = self.modify_card_in_turn()
        r = run_kb("versions", "list", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith(f"{t['turn']}（這一次）｜"), lines[0])
        self.assertIn("問：「改一下」", lines[0])
        self.assertIn("修改 1、新增 0、刪除 0、成品 0", lines[0])
        self.assertEqual(lines[1], f"    modify：{self.card_rel}")

    def test_json(self):
        t = self.modify_card_in_turn()
        r = run_kb("versions", "list", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["turn"], t["turn"])
        self.assertTrue(data[0]["current"])
        self.assertFalse(data[0]["reverted"])
        self.assertEqual(data[0]["counts"], {"modify": 1, "create": 0, "delete": 0, "outputs": 0})
        self.assertEqual(data[0]["entries"][0]["path"], self.card_rel)

    def test_n_limits_turns(self):
        self.modify_card_in_turn("一")
        self.modify_card_in_turn("二")
        self.modify_card_in_turn("三")
        r = run_kb("versions", "list", "--n", "2", cwd=self.V)
        heads = [ln for ln in r.stdout.splitlines() if not ln.startswith("    ")]
        self.assertEqual(len(heads), 2)
        self.assertIn("問：「三」", heads[0])


class Revert(Base):
    def test_restores_modified_file(self):
        t = self.modify_card_in_turn()
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        self.assertIn(f"已退回 {t['turn']}（問：「改一下」）：還原 1、移除 0、成品 0 份", r.stdout)
        self.assertTrue((self.versions_dir(t["turn"]) / "reverted.json").is_file())
        rv = json.loads((self.versions_dir(t["turn"]) / "reverted.json").read_text(encoding="utf-8"))
        self.assertEqual(set(rv), {"time", "by_turn"})
        r = run_kb("versions", "list", cwd=self.V)
        self.assertIn(f"{t['turn']}（已退回）", r.stdout)

    def test_revert_then_revert_again_brings_change_back(self):
        """D-074：退回前先把現況備進「退回」這句話的回合，退錯可再退。"""
        self.modify_card_in_turn()
        rt = self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(self.card.read_bytes(), self.original)
        rows = turn.entries(self.V, rt["turn"])
        self.assertEqual([(e["path"], e["action"]) for e in rows], [(self.card_rel, "modify")])
        self.new_turn("再退一次")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"已退回 {rt['turn']}", r.stdout)
        self.assertEqual(self.card.read_text(encoding="utf-8"), "改過了\n")

    def test_removes_created_file_and_empty_entity_folder(self):
        t = turn.start(self.V, SID, "開新卡")
        rel = "案子/新卡/新卡.md"
        turn.backup(self.V, rel, t["turn"])
        (self.V / "案子" / "新卡").mkdir()
        (self.V / rel).write_text("# 新卡\n", encoding="utf-8")
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / rel).exists())
        self.assertFalse((self.V / "案子" / "新卡").exists())
        self.assertTrue((self.V / "案子").is_dir())
        self.assertIn("還原 0、移除 1", r.stdout)

    def test_created_folder_with_other_files_is_kept(self):
        t = turn.start(self.V, SID, "開新卡")
        rel = "案子/新卡/新卡.md"
        turn.backup(self.V, rel, t["turn"])
        (self.V / "案子" / "新卡").mkdir()
        (self.V / rel).write_text("# 新卡\n", encoding="utf-8")
        (self.V / "案子" / "新卡" / "原檔.docx").write_bytes(b"x")
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertFalse((self.V / rel).exists())
        self.assertTrue((self.V / "案子" / "新卡" / "原檔.docx").exists())

    def _make_output(self, prompt="寫草稿"):
        t = turn.start(self.V, SID, prompt)
        rel = "AI 產出/x.md"
        turn.backup(self.V, rel, t["turn"])
        (self.V / rel).write_text("草稿\n", encoding="utf-8")
        entry = inventory.register_output(self.V, rel, t["turn"])
        return t, rel, entry

    def test_outputs_kept_without_flag_and_status_turn_reverted(self):
        t, rel, entry = self._make_output()
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "這一回合有 1 份成品：")
        self.assertEqual(lines[1], f"    {rel}")
        self.assertIn("這 1 份會刪掉，確定嗎", lines[2])
        self.assertIn("成品 1 份留著", lines[-1])
        self.assertTrue((self.V / rel).is_file())
        got = [e for e in inventory.load(self.V)["entries"] if e["id"] == entry["id"]][0]
        self.assertEqual(got["status"], "turn_reverted")
        self.assertTrue((self.versions_dir(t["turn"]) / "reverted.json").is_file())

    def test_delete_outputs_removes_file_with_backup(self):
        t, rel, entry = self._make_output()
        rt = self.new_turn()
        r = run_kb("versions", "revert", "--delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / rel).exists())
        self.assertIn("成品 1 份刪了", r.stdout)
        got = [e for e in inventory.load(self.V)["entries"] if e["id"] == entry["id"]][0]
        self.assertEqual(got["status"], "deleted")
        rows = turn.entries(self.V, rt["turn"])
        self.assertEqual([(e["path"], e["action"]) for e in rows], [(rel, "delete")])
        self.assertEqual((self.versions_dir(rt["turn"]) / "files" / "AI 產出" / "x.md").read_text(encoding="utf-8"), "草稿\n")

    def test_reverted_turn_cannot_be_reverted_again(self):
        self._make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可以退回的改動")

    def test_older_code_is_rejected_exit_3_and_nothing_changes(self):
        old = self.modify_card_in_turn("一", "一\n")
        new = self.modify_card_in_turn("二", "二\n")
        self.new_turn()
        r = run_kb("versions", "revert", old["turn"], cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), f"只能從最近一次開始退：{new['turn']}")
        self.assertEqual(self.card.read_text(encoding="utf-8"), "二\n")
        self.assertFalse((self.versions_dir(old["turn"]) / "reverted.json").exists())
        self.assertFalse((self.versions_dir(new["turn"]) / "reverted.json").exists())

    def test_explicit_latest_code_accepted(self):
        new = self.modify_card_in_turn("二", "二\n")
        self.new_turn()
        r = run_kb("versions", "revert", new["turn"], cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_nothing_to_revert_exit_3(self):
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可以退回的改動")

    def test_json_output(self):
        t = self.modify_card_in_turn()
        self.new_turn()
        r = run_kb("versions", "revert", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(data["turn"], t["turn"])
        self.assertEqual(data["restored"], [self.card_rel])
        self.assertEqual(data["removed"], [])
        self.assertEqual(data["outputs"], [])
        self.assertFalse(data["outputs_deleted"])

    def test_revert_in_same_turn_as_target_opens_new_turn(self):
        """退回動作的回合不能等於要退的回合（不然現況沒處備）。"""
        t = self.modify_card_in_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        rt = turn.current(self.V)
        self.assertNotEqual(rt["turn"], t["turn"])
        self.assertEqual([e["action"] for e in turn.entries(self.V, rt["turn"])], ["modify"])

    def test_lost_backup_is_reported(self):
        t = self.modify_card_in_turn()
        shutil.rmtree(self.versions_dir(t["turn"]) / "files")
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("無法還原 1 檔", r.stdout)
        self.assertEqual(self.card.read_text(encoding="utf-8"), "改過了\n")

    def test_outside_vault_exits_2(self):
        r = run_kb("versions", "list", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
