"""kb versions list／revert／delete-outputs 黑箱：subprocess 跑 kb.py，cwd 在 vault。
list 空、list 一回合、revert 還原修改、revert 移除新增、成品留著／刪掉、指定舊代號被拒 exit 3、退回後再退回、--json；
第一輪 H1 delete-outputs、M3 退回後對帳 status、M4 成品只算 manifest create 的。"""
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

    def make_output(self, prompt="寫草稿", rel="AI 產出/x.md", text="草稿\n"):
        """模擬檔案工具在新回合新建一份 AI 產出並登記（PreToolUse 備份 create、寫檔、PostToolUse 登記）。"""
        t = turn.start(self.V, SID, prompt)
        turn.backup(self.V, rel, t["turn"])
        (self.V / rel).write_text(text, encoding="utf-8")
        entry = inventory.register_output(self.V, rel, t["turn"])
        return t, rel, entry

    def status_of(self, entry_id):
        return [e for e in inventory.load(self.V)["entries"] if e["id"] == entry_id][0]["status"]


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

    def test_bad_manifest_line_does_not_break_list(self):
        """第一輪 L4（驗收 25）：manifest 半截行，list 照常印。"""
        t = self.modify_card_in_turn()
        with open(self.versions_dir(t["turn"]) / "manifest.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write("{bad\n")
        r = run_kb("versions", "list", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("修改 1、新增 0", r.stdout)
        self.assertNotIn("Traceback", r.stderr)


class Revert(Base):
    def test_restores_modified_file(self):
        t = self.modify_card_in_turn()
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        self.assertEqual(r.stdout.strip(), f"已退回 {t['turn']}（問：「改一下」）：還原 1、移除 0、成品 0 份")
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

    def test_outputs_kept_without_flag_and_status_turn_reverted(self):
        """第一輪 H1：revert 不問「確定嗎」，只列清單加一句怎麼刪。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"已退回 {t['turn']}（問：「寫草稿」）：還原 0、移除 0、成品 1 份留著")
        self.assertEqual(lines[1], f"    {rel}")
        self.assertEqual(lines[2], "這 1 份成品先留著；要刪跟我說，我用 delete-outputs 刪。")
        self.assertEqual(len(lines), 3)
        self.assertNotIn("確定嗎", r.stdout)
        self.assertTrue((self.V / rel).is_file())
        self.assertEqual(self.status_of(entry["id"]), "turn_reverted")
        self.assertTrue((self.versions_dir(t["turn"]) / "reverted.json").is_file())

    def test_delete_outputs_flag_removes_file_with_backup(self):
        t, rel, entry = self.make_output()
        rt = self.new_turn()
        r = run_kb("versions", "revert", "--delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / rel).exists())
        self.assertIn("成品 1 份刪了", r.stdout)
        self.assertIn(f"    {rel}", r.stdout)
        self.assertEqual(self.status_of(entry["id"]), "deleted")
        rows = turn.entries(self.V, rt["turn"])
        self.assertEqual([(e["path"], e["action"]) for e in rows], [(rel, "delete")])
        self.assertEqual((self.versions_dir(rt["turn"]) / "files" / "AI 產出" / "x.md").read_text(encoding="utf-8"), "草稿\n")

    def test_reverting_a_deletion_restores_file_and_status_active(self):
        """第一輪 M3：退掉「刪成品」那一回合，檔回來、status 回 active。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", "--delete-outputs", cwd=self.V)
        self.assertEqual(self.status_of(entry["id"]), "deleted")
        self.new_turn("救回來")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.V / rel).read_text(encoding="utf-8"), "草稿\n")
        self.assertEqual(self.status_of(entry["id"]), "active")

    def test_preexisting_file_in_outputs_is_restored_not_deleted(self):
        """第一輪 M4：這回合之前就存在、沒登記的 AI 產出檔，--delete-outputs 不刪，還原成原內容。"""
        q = self.V / "AI 產出" / "q.md"
        q.write_text("客戶自己放的\n", encoding="utf-8")
        t = turn.start(self.V, SID, "改 q")
        turn.backup(self.V, "AI 產出/q.md", t["turn"])
        q.write_text("AI 改過\n", encoding="utf-8")
        self.assertIsNone(inventory.register_output(self.V, "AI 產出/q.md", t["turn"]))
        rt = self.new_turn()
        r = run_kb("versions", "revert", "--delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(q.read_text(encoding="utf-8"), "客戶自己放的\n")
        self.assertIn("還原 1、移除 0、成品 0 份", r.stdout)
        rows = turn.entries(self.V, rt["turn"])
        self.assertEqual([(e["path"], e["action"]) for e in rows], [("AI 產出/q.md", "modify")])
        self.assertEqual((self.versions_dir(rt["turn"]) / "files" / "AI 產出" / "q.md").read_text(encoding="utf-8"), "AI 改過\n")

    def test_registered_entry_whose_manifest_says_modify_is_not_counted_as_output(self):
        """第一輪 M4：成品只算 manifest create 的。舊筆 turn_reverted 後新回合再改（新筆 active、manifest modify）：
        退那回合是還原、不是刪成品；對帳後新筆仍 active（檔在）。"""
        t, rel, old = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(self.status_of(old["id"]), "turn_reverted")
        t3 = turn.start(self.V, SID, "再寫")
        turn.backup(self.V, rel, t3["turn"])
        (self.V / rel).write_text("第二次\n", encoding="utf-8")
        new = inventory.register_output(self.V, rel, t3["turn"])
        self.assertNotEqual(new["id"], old["id"])
        self.new_turn()
        r = run_kb("versions", "revert", "--delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("還原 1、移除 0、成品 0 份", r.stdout)
        self.assertEqual((self.V / rel).read_text(encoding="utf-8"), "草稿\n")
        self.assertEqual(self.status_of(new["id"]), "active")
        self.assertEqual(self.status_of(old["id"]), "superseded")  # 第二輪 R2-3

    def test_reverted_turn_is_never_chosen_again(self):
        """已退回的回合不再是候選；第二輪 R2-4 後「退回」那一回合本身可以退（它帶 reverted_turn），所以用指定代號驗。"""
        a, rel, entry = self.make_output()
        rt = self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn()
        r = run_kb("versions", "revert", a["turn"], cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), f"只能從最近一次開始退：{rt['turn']}")
        self.assertEqual(self.status_of(entry["id"]), "turn_reverted")

    def test_nothing_left_after_undoing_a_revert_chain_is_reported(self):
        """沒有任何回合有改動也沒有退回紀錄時才印「沒有可以退回的改動」。"""
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

    def test_three_turns_in_one_second_revert_hits_the_last(self):
        """驗收 29：同一秒開三個回合、各改一張不同的 Z1 卡，revert 退的是代號最大的第三個。"""
        from datetime import datetime
        from unittest import mock
        cards = []
        for i in range(3):
            d = self.V / "案子" / f"案{i}"
            d.mkdir()
            (d / f"案{i}.md").write_text(f"原 {i}\n", encoding="utf-8")
            cards.append(f"案子/案{i}/案{i}.md")
        ids = []
        with mock.patch.object(turn, "_now", lambda: datetime(2026, 10, 13, 15, 30, 12)):
            for i in range(3):
                t = turn.start(self.V, SID, f"第{i}句")
                turn.backup(self.V, cards[i], t["turn"])
                (self.V / cards[i]).write_text(f"改 {i}\n", encoding="utf-8")
                ids.append(t["turn"])
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 3)
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"已退回 {ids[2]}（問：「第2句」）", r.stdout)
        self.assertEqual((self.V / cards[2]).read_text(encoding="utf-8"), "原 2\n")
        self.assertEqual((self.V / cards[0]).read_text(encoding="utf-8"), "改 0\n")
        self.assertEqual((self.V / cards[1]).read_text(encoding="utf-8"), "改 1\n")

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


class DeleteOutputs(Base):
    """第一輪 H1：退回 → 客戶說刪 → kb versions delete-outputs。"""

    def test_deletes_turn_reverted_outputs_of_latest_reverted_turn(self):
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(self.status_of(entry["id"]), "turn_reverted")
        dt = self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"已刪 1 份成品（回合 {t['turn']}）：")
        self.assertEqual(lines[1], f"    {rel}")
        self.assertFalse((self.V / rel).exists())
        self.assertEqual(self.status_of(entry["id"]), "deleted")
        rows = turn.entries(self.V, dt["turn"])
        self.assertEqual([(e["path"], e["action"]) for e in rows], [(rel, "delete")])
        self.assertEqual((self.versions_dir(dt["turn"]) / "files" / "AI 產出" / "x.md").read_text(encoding="utf-8"), "草稿\n")

    def test_then_revert_brings_output_back_active(self):
        """驗收 14 的尾段：delete-outputs 之後 revert 退掉刪除，檔回來、status 回 active。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn("刪掉")
        run_kb("versions", "delete-outputs", cwd=self.V)
        self.new_turn("救回來")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.V / rel).read_text(encoding="utf-8"), "草稿\n")
        self.assertEqual(self.status_of(entry["id"]), "active")

    def test_explicit_code(self):
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", t["turn"], cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status_of(entry["id"]), "deleted")

    def test_not_reverted_turn_is_refused(self):
        t, rel, entry = self.make_output()
        self.new_turn()
        r = run_kb("versions", "delete-outputs", t["turn"], cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), f"{t['turn']} 沒有可刪的成品")
        self.assertTrue((self.V / rel).is_file())
        self.assertEqual(self.status_of(entry["id"]), "active")

    def test_nothing_to_delete(self):
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可刪的成品")

    def test_already_deleted_is_refused(self):
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn("刪掉")
        run_kb("versions", "delete-outputs", cwd=self.V)
        self.new_turn("再刪")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可刪的成品")

    def test_json(self):
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(data["turn"], t["turn"])
        self.assertEqual(data["deleted"], [rel])

    def test_same_turn_rewrite_then_delete_outputs_is_refused(self):
        """第二輪 R2-2（驗收 31）：同回合先重寫留著的成品再 delete-outputs → 不刪、印「這一句話已經改過」、status 不變。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        at = self.new_turn("重寫再刪")
        turn.backup(self.V, rel, at["turn"])
        (self.V / rel).write_text("這一句話重寫的\n", encoding="utf-8")
        new = inventory.register_output(self.V, rel, at["turn"])
        statuses_before = {e["id"]: e["status"] for e in inventory.load(self.V)["entries"]}
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{rel} 這一句話已經改過，下一句再刪", r.stdout)
        self.assertNotIn("已刪", r.stdout)
        self.assertEqual((self.V / rel).read_text(encoding="utf-8"), "這一句話重寫的\n")
        self.assertEqual({e["id"]: e["status"] for e in inventory.load(self.V)["entries"]}, statuses_before)
        self.assertEqual([(e["path"], e["action"]) for e in turn.entries(self.V, at["turn"])], [(rel, "modify")])
        self.assertIsNotNone(new)

    def test_superseded_output_is_not_deletable(self):
        """第二輪 R2-3(1)（驗收 32 前半）：留著的成品被新回合重寫 → 舊筆 superseded，delete-outputs 沒東西可刪，新檔還在。"""
        t, rel, old = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        t3 = turn.start(self.V, SID, "再寫")
        turn.backup(self.V, rel, t3["turn"])
        (self.V / rel).write_text("新回合的\n", encoding="utf-8")
        inventory.register_output(self.V, rel, t3["turn"])
        self.assertEqual(self.status_of(old["id"]), "superseded")
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可刪的成品")
        self.assertEqual((self.V / rel).read_text(encoding="utf-8"), "新回合的\n")

    def test_manually_changed_output_is_kept(self):
        """第二輪 R2-3(2)（驗收 32 後半）：留著的成品手動改過內容 → 指紋不同、不刪、印「之後被改過，留著」。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        (self.V / rel).write_text("手動改的\n", encoding="utf-8")
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{rel} 之後被改過，留著", r.stdout)
        self.assertTrue((self.V / rel).is_file())
        self.assertEqual(self.status_of(entry["id"]), "turn_reverted")

    def test_missing_output_file_is_reported_and_marked_deleted(self):
        """第二輪 R2-L1（驗收 36）：檔已不在 → 印「已經不在」、status deleted、manifest 不多行。"""
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        (self.V / rel).unlink()
        dt = self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{rel} 已經不在", r.stdout)
        self.assertNotIn("已刪 1 份", r.stdout)
        self.assertEqual(self.status_of(entry["id"]), "deleted")
        self.assertEqual(turn.entries(self.V, dt["turn"]), [])

    def test_reverting_the_revert_restores_reverted_marker_and_status(self):
        """第二輪 R2-4（驗收 33）：A 退回（成品留著）→ 再退掉那次退回：A 的 reverted.json 不在、list 不標、成品 active；delete-outputs 沒東西。"""
        a, rel, entry = self.make_output()
        rt = self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertTrue((self.versions_dir(a["turn"]) / "reverted.json").is_file())
        meta = json.loads((self.versions_dir(rt["turn"]) / "turn.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["reverted_turn"], a["turn"])
        self.new_turn("退掉退回")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"已退回 {rt['turn']}", r.stdout)
        self.assertFalse((self.versions_dir(a["turn"]) / "reverted.json").exists())
        self.assertEqual(self.status_of(entry["id"]), "active")
        r = run_kb("versions", "list", cwd=self.V)
        self.assertNotIn(f"{a['turn']}（已退回）", r.stdout)
        self.assertIn(f"{rt['turn']}（已退回）", r.stdout)
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), "沒有可刪的成品")

    def test_reverting_the_revert_with_card_change(self):
        """R2-4 的另一面：退掉退回後，被退回那一回合的綜合卡改動也回來、它不再算「已退回」；
        再退一次退的是「退掉退回」那一回合（只從最近一次開始），卡又回原版。"""
        t = self.modify_card_in_turn()
        rt = self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(self.card.read_bytes(), self.original)
        rt2 = self.new_turn("退掉退回")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertIn(f"已退回 {rt['turn']}", r.stdout)
        self.assertIn(f"{t['turn']} 不再算已退回", r.stdout)
        self.assertEqual(self.card.read_text(encoding="utf-8"), "改過了\n")
        self.assertFalse((self.versions_dir(t["turn"]) / "reverted.json").exists())
        self.new_turn("再退")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertIn(f"已退回 {rt2['turn']}", r.stdout)
        self.assertEqual(self.card.read_bytes(), self.original)

    def test_second_revert_after_revert_does_not_touch_earlier_turn(self):
        """H1 的根因：退回後「刪」不再靠第二次 revert，前一回合客戶接受的綜合卡修改不會被退掉。"""
        self.modify_card_in_turn("改卡", "客戶接受的版本\n")
        t, rel, entry = self.make_output()
        self.new_turn()
        run_kb("versions", "revert", cwd=self.V)
        self.new_turn("刪掉")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_text(encoding="utf-8"), "客戶接受的版本\n")
        self.assertFalse((self.V / rel).exists())


class VersionsJournal(Base):
    """工作日誌程式段（D-085）：CLI 自己的回合（session_id 空）裡 revert、delete-outputs 成功多一行 kind program；
    對話開的回合裡叫的不寫；delete-outputs 刪 0 份不寫（疑義裁定 4）。"""

    def program_rows(self):
        from kb._lib import journal
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r["kind"] == "program"]

    def cli_turn_with(self, rel, text, register=False):
        """像守門自己開的回合（payload 沒 session_id）：備份、真的寫。"""
        t = turn.start(self.V, None, "")
        turn.backup(self.V, rel, t["turn"])
        (self.V / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.V / rel).write_text(text, encoding="utf-8")
        if register:
            inventory.register_output(self.V, rel, t["turn"])
        return t

    def test_revert_writes_program_line_with_printed_sentence(self):
        self.cli_turn_with(self.card_rel, "改過了\n")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["command"], "versions revert")
        self.assertEqual(rows[0]["note"], r.stdout.splitlines()[0])
        self.assertTrue(rows[0]["note"].startswith("已退回 "))
        self.assertEqual(rows[0]["changed"], [{"path": self.card_rel, "action": "modify"}])

    def test_revert_removed_file_is_delete(self):
        self.cli_turn_with("案子/新案/新案.md", "新卡\n")
        run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(self.program_rows()[0]["changed"], [{"path": "案子/新案/新案.md", "action": "delete"}])

    def test_delete_outputs_writes_program_line(self):
        t = self.cli_turn_with("AI 產出/y.md", "草稿\n", register=True)
        run_kb("versions", "revert", cwd=self.V)
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["command"], "versions delete-outputs")
        self.assertEqual(rows[1]["note"], f"已刪 1 份成品（回合 {t['turn']}）")
        self.assertEqual(rows[1]["changed"], [{"path": "AI 產出/y.md", "action": "delete"}])

    def test_delete_outputs_of_zero_writes_nothing(self):
        t = self.cli_turn_with("AI 產出/y.md", "草稿\n", register=True)
        run_kb("versions", "revert", cwd=self.V)
        os.remove(self.V / "AI 產出" / "y.md")
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["command"] for x in self.program_rows()], ["versions revert"])
        self.assertTrue(t)

    def test_json_mode_also_writes(self):
        self.cli_turn_with(self.card_rel, "改過了\n")
        r = run_kb("versions", "revert", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["note"].startswith("已退回 "))

    def test_inside_conversation_turn_writes_nothing(self):
        self.modify_card_in_turn()
        self.new_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.program_rows(), [])


if __name__ == "__main__":
    unittest.main()
