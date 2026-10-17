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

    def test_unreadable_inventory_list_omits_outputs_and_says_why(self):
        """審閱裁定 37：清冊形狀不對時 list 不吐 traceback、不印成品數，多印一行「清冊讀不到：<原因>」。"""
        t = self.modify_card_in_turn()
        (self.V / ".system" / "inventory.json").write_text('{"schema_version": 3, "entries": []}', encoding="utf-8")
        r = run_kb("versions", "list", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith(t["turn"]), lines[0])
        self.assertTrue(lines[0].endswith("修改 1、新增 0、刪除 0"), lines[0])
        self.assertIn("清冊讀不到：清冊版本 3 比這版 plugin 新", lines)
        data = json.loads(run_kb("versions", "list", "--json", cwd=self.V).stdout)
        self.assertIsNone(data[0]["counts"]["outputs"])

    def test_outputs_count_only_ai_output(self):
        """清冊審閱裁定 1：kb inventory scan 那回合入冊的客戶檔不是成品，list 不印「成品 N」。"""
        r = run_kb("inventory", "scan", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = inventory.load(self.V)
        scan_turn = data["entries"][0]["created_turn"]
        self.assertGreater(sum(1 for e in data["entries"] if e["created_turn"] == scan_turn), 1)
        r = run_kb("versions", "list", "--json", cwd=self.V)
        row = [t for t in json.loads(r.stdout) if t["turn"] == scan_turn][0]
        self.assertEqual(row["counts"]["outputs"], 0)
        t, _rel, _entry = self.make_output()
        r = run_kb("versions", "list", "--json", cwd=self.V)
        row = [x for x in json.loads(r.stdout) if x["turn"] == t["turn"]][0]
        self.assertEqual(row["counts"]["outputs"], 1)


class Revert(Base):
    def test_unreadable_inventory_revert_still_restores_files(self):
        """審閱裁定 37：清冊讀不到時 revert 照 manifest 還原檔案、跳過成品對帳、印「清冊讀不到：<原因>」。"""
        self.modify_card_in_turn()
        (self.V / ".system" / "inventory.json").write_text('{"schema_version": 3, "entries": []}', encoding="utf-8")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        self.assertIn("清冊讀不到：清冊版本 3 比這版 plugin 新", r.stdout.splitlines())

    def test_unreadable_inventory_is_itself_restored_from_backup(self):
        r = run_kb("inventory", "scan", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        good = (self.V / ".system" / "inventory.json").read_bytes()
        t = turn.start(self.V, SID, "弄壞清冊")
        turn.backup(self.V, ".system/inventory.json", t["turn"])
        (self.V / ".system" / "inventory.json").write_text('{"schema_version": 3, "entries": []}', encoding="utf-8")
        r = run_kb("versions", "revert", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["restored"], [".system/inventory.json"])
        self.assertEqual(data["inventory_error"], "清冊版本 3 比這版 plugin 新")
        self.assertEqual((self.V / ".system" / "inventory.json").read_bytes(), good)

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
        """退回動作的回合不能等於要退的回合（不然現況沒處備）。第二輪 R2-M2：對話裡（帶 sid、還沒答完）退回自己這句，
        開新回合並成為目前回合，繼承原回合的問、記 journal_turn。"""
        t = self.modify_card_in_turn()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        by = json.loads((self.versions_dir(t["turn"]) / "reverted.json").read_text(encoding="utf-8"))["by_turn"]
        self.assertNotEqual(by, t["turn"])
        for cur in (turn.current(self.V), turn.current(self.V, SID)):
            self.assertEqual((cur["turn"], cur["journal_turn"], cur["prompt_head"]), (by, t["turn"], t["prompt_head"]))
        self.assertEqual([e["action"] for e in turn.entries(self.V, by)], ["modify"])

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

    def hook(self, name, data):
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / name)], input=json.dumps(data, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        if name == "guard.py":
            self.assertIn('"allow"', r.stdout)
        else:
            self.assertEqual(r.stdout, "")

    def prompt_and_write_card(self, prompt):
        base = {"session_id": SID, "cwd": str(self.V)}
        self.hook("turn.py", dict(base, prompt=prompt, hook_event_name="UserPromptSubmit"))
        self.hook("guard.py", dict(base, tool_name="Write", hook_event_name="PreToolUse",
                                   tool_input={"file_path": str(self.card), "content": "改過了\n"}))
        self.card.write_text("改過了\n", encoding="utf-8")

    def stop(self, message):
        self.hook("stop.py", {"session_id": SID, "cwd": str(self.V), "hook_event_name": "Stop",
                              "stop_hook_active": False, "last_assistant_message": message})

    def rows(self):
        from kb._lib import journal
        return [x for m in journal.months(self.V) for x in journal.read_lines(self.V, m)]

    def sess(self):
        return json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))

    def test_acceptance_27_revert_after_stop_leaves_session_file_and_no_fake_interrupt(self):
        """驗收 27（第二輪）：答完後終端機退回，turns/<sid>.json 仍是 done true 的原回合，current.json 換成 sid 空的回合，
        下一句話不會被補記出假的「退回 …」中斷段。"""
        self.prompt_and_write_card("改信義王")
        self.stop("改好了")
        sess_file = self.V / ".system" / "turns" / f"{SID}.json"
        before = sess_file.read_bytes()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        self.assertEqual(sess_file.read_bytes(), before)
        self.assertEqual(turn.current(self.V)["session_id"], "")
        self.hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "下一句", "hook_event_name": "UserPromptSubmit"})
        self.assertEqual([x for x in self.rows() if x.get("interrupted")], [])

    def test_acceptance_39_revert_inside_same_sentence_inherits_and_stop_closes_original(self):
        """驗收 39（取代 28）：同一句裡改卡又退回：回合檔換成新代號、journal_turn 是 T；Stop 收 T 那一行，changed 含卡；
        turns/ 沒有多檔。同一句再退兩次，卡片每次來回，journal_turn 一路是 T。"""
        self.prompt_and_write_card("改一下卡")
        turns_dir = self.V / ".system" / "turns"
        names = sorted(p.name for p in turns_dir.iterdir())
        t_turn = self.sess()["turn"]
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.card.read_bytes(), self.original)
        sess = self.sess()
        self.assertNotEqual(sess["turn"], t_turn)
        self.assertTrue(sess["turn"].endswith("-test"), sess["turn"])
        self.assertEqual((sess["journal_turn"], sess["prompt_head"]), (t_turn, "改一下卡"))
        self.stop("改了又退回了")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["turn"], rows[0]["done"], rows[0]["result"]), (t_turn, True, "改了又退回了"))
        self.assertIn(self.card_rel, [c["path"] for c in rows[0]["changed"]])
        self.assertEqual(sorted(p.name for p in turns_dir.iterdir()), names)

    def test_acceptance_39_consecutive_reverts_in_same_sentence(self):
        self.prompt_and_write_card("改一下卡")
        changed = self.card.read_bytes()
        t_turn = self.sess()["turn"]
        states = []
        for _ in range(3):
            r = run_kb("versions", "revert", cwd=self.V)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.startswith("已退回 "), r.stdout)
            states.append(self.card.read_bytes())
        self.assertEqual(states, [self.original, changed, self.original])
        self.assertEqual(self.sess()["journal_turn"], t_turn)

    def test_acceptance_40_note_after_inherit_lands_on_original_line(self):
        """驗收 40：繼承後 turn note 再 Stop，T 那行 note 是那句；改成不 Stop 直接下一句，T 那行 interrupted、note 照留。"""
        for finish in ("stop", "prompt"):
            with self.subTest(finish=finish):
                self.prompt_and_write_card(f"改一下卡 {finish}")
                t_turn = self.sess()["turn"]
                run_kb("versions", "revert", cwd=self.V)
                r = run_kb("turn", "note", "退了", cwd=self.V)
                self.assertEqual(r.returncode, 0, r.stderr)
                if finish == "stop":
                    self.stop("退好了")
                else:
                    self.hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "下一句",
                                          "hook_event_name": "UserPromptSubmit"})
                row = [x for x in self.rows() if x["turn"] == t_turn][0]
                self.assertEqual((row["note"], row["note_by"], row["interrupted"]), ("退了", "ai", finish == "prompt"))
                self.card.write_bytes(self.original)

    def test_acceptance_41_terminal_revert_seals_acting_turn(self):
        """驗收 41（裁定 (ii)）：答完後終端機 revert、folders exclude、再 revert：第二次只退 exclude（sources.json 回來、卡片不變）；
        current.json 的 session_id 空。"""
        self.prompt_and_write_card("改信義王")
        self.stop("改好了")
        sources = self.V / ".system" / "sources.json"
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        card_after_first = self.card.read_bytes()
        before_exclude = sources.read_bytes()
        r = run_kb("folders", "exclude", "../raw/客戶案件/a.txt", "--kind", "unused", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(sources.read_bytes(), before_exclude)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(sources.read_text(encoding="utf-8")), json.loads(before_exclude.decode("utf-8")))
        self.assertEqual(self.card.read_bytes(), card_after_first)
        self.assertEqual(turn.current(self.V)["session_id"], "")

    def test_acceptance_42_unmasked_old_prompt_head_is_masked_in_sentence(self):
        """驗收 42（R2-L3）：升級前沒遮好的問被退回句搬出來時，印給人與程式段 note 都整句再遮。"""
        self.hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "改卡", "hook_event_name": "UserPromptSubmit"})
        sess_file = self.V / ".system" / "turns" / f"{SID}.json"
        data = self.sess()
        data["prompt_head"] = "帳號 jie 密碼 abc123"
        for f in (sess_file, self.V / ".system" / "turns" / "current.json"):  # 像升級前留下的回合檔
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.hook("guard.py", {"session_id": SID, "cwd": str(self.V), "tool_name": "Write", "hook_event_name": "PreToolUse",
                               "tool_input": {"file_path": str(self.card), "content": "改過了\n"}})
        self.card.write_text("改過了\n", encoding="utf-8")
        self.stop("改好了")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("abc123", r.stdout)
        line = r.stdout.splitlines()[0]
        self.assertTrue(line.endswith("（問：「帳號███」）：還原 1、移除 0、成品 0 份"), line)  # 只遮問那段，數字留著
        notes = [x["note"] for x in self.program_rows()]
        self.assertEqual(notes, [line])

    def test_acceptance_44_terminal_delete_outputs_opens_own_turn(self):
        """驗收 44（R3-M1）：對話裡建 z.md、同一句退回、Stop；終端機 delete-outputs 再 revert：z.md 回來、status active，
        卡片與 T 的 reverted.json 都不變；versions/ 多兩個尾 0000 的回合夾。"""
        base = {"session_id": SID, "cwd": str(self.V)}
        z = self.V / "AI 產出" / "z.md"
        self.hook("turn.py", dict(base, prompt="寫 z", hook_event_name="UserPromptSubmit"))
        t_turn = self.sess()["turn"]
        self.hook("guard.py", dict(base, tool_name="Write", hook_event_name="PreToolUse",
                                   tool_input={"file_path": str(z), "content": "z\n"}))
        z.write_text("z\n", encoding="utf-8")
        self.hook("output_log.py", dict(base, tool_name="Write", hook_event_name="PostToolUse",
                                        tool_input={"file_path": str(z)}))
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.stop("退好了")
        marker = self.versions_dir(t_turn) / "reverted.json"
        before = (self.card.read_bytes(), marker.read_bytes())
        vroot = self.V / ".system" / "versions"
        dirs = {p.name for p in vroot.iterdir()}
        r = run_kb("versions", "delete-outputs", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(z.exists())
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(z.is_file())
        entry = [e for e in inventory.load(self.V)["entries"] if "file://AI 產出/z.md" in [l["uri"] for l in e.get("locations", [])]][-1]
        self.assertEqual(entry["status"], "active")
        self.assertEqual((self.card.read_bytes(), marker.read_bytes()), before)
        new = {p.name for p in vroot.iterdir()} - dirs
        self.assertEqual(len(new), 2, new)
        self.assertTrue(all(n.endswith("-0000") for n in new), new)

    def test_acceptance_45_revert_across_month_completes_original_month_line(self):
        """驗收 45（R3-L1）：原回合是上個月的，同一句退回後 Stop，補完的是上個月那行，本月不多行。"""
        from datetime import date
        from kb._lib import journal
        self.hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "改卡", "hook_event_name": "UserPromptSubmit"})
        old = "20260930-235900-test"
        this_month = date.today().strftime("%Y-%m")
        jsonl = journal.jsonl_path(self.V, this_month)
        jsonl.unlink()
        data = dict(self.sess(), turn=old, started="2026-09-30T23:59:00")
        for f in (self.V / ".system" / "turns" / f"{SID}.json", self.V / ".system" / "turns" / "current.json"):
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        journal.begin_turn(self.V, None, data)
        self.hook("guard.py", {"session_id": SID, "cwd": str(self.V), "tool_name": "Write", "hook_event_name": "PreToolUse",
                               "tool_input": {"file_path": str(self.card), "content": "改過了\n"}})
        self.card.write_text("改過了\n", encoding="utf-8")
        self.assertEqual(run_kb("versions", "revert", cwd=self.V).returncode, 0)
        self.stop("退好了")
        sept = journal.read_lines(self.V, "2026-09")
        self.assertEqual(len(sept), 1)
        self.assertEqual((sept[0]["turn"], sept[0]["done"], sept[0]["result"]), (old, True, "退好了"))
        self.assertEqual(journal.read_lines(self.V, this_month), [])

    def test_acceptance_46_redact_after_two_reverts_rewrites_whole_chain(self):
        """驗收 46（R3-L2）：同一句連退兩次後 redact，versions/ 底下 turn 或 journal_turn 是 T 的 turn.json 都改，versions list 印不出原句。"""
        self.prompt_and_write_card("原本的問")
        t_turn = self.sess()["turn"]
        for _ in range(2):
            self.assertEqual(run_kb("versions", "revert", cwd=self.V).returncode, 0)
        r = run_kb("turn", "redact", "已遮", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        metas = []
        for p in (self.V / ".system" / "versions").glob("*/turn.json"):
            meta = json.loads(p.read_text(encoding="utf-8"))
            if t_turn in (meta.get("turn"), meta.get("journal_turn")):
                metas.append(meta)
        self.assertGreaterEqual(len(metas), 3)
        self.assertEqual({m["prompt_head"] for m in metas}, {"已遮"})
        self.assertNotIn("原本的問", run_kb("versions", "list", cwd=self.V).stdout)

    def test_inherit_keeps_approval_in_same_sentence(self):
        """D-075：同一句話的同意不因退回失效；inherit 時 approved 一起繼承，退回後再改超過上限的卡仍放行。
        上限用 .system/config.json 調成 1，不用真的改 11 張。"""
        (self.V / ".system" / "config.json").write_text(json.dumps({"big_change_synthesis_max": 1}), encoding="utf-8")
        other = self.V / "案子" / "甲案" / "甲案.md"
        other.parent.mkdir(parents=True)
        other.write_text("# 甲案\n", encoding="utf-8")
        self.prompt_and_write_card("全部改")
        r = run_kb("turn", "approve", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sess()["approved"])
        ok, code, _reason = turn.before_write(self.V, str(other), str(self.V), "Z1", SID)
        self.assertEqual((ok, code), (True, ""))

    def test_cli_turn_revert_rotates_current_only(self):
        """M1 裁定 (a)：目前回合是 CLI 自己的（sid 空）就照舊 turn.start(None) 換 current.json，
        之後的寫入不會記進已退回的回合；turns/ 只有 current.json。"""
        t = self.cli_turn_with(self.card_rel, "改過了\n")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        cur = turn.current(self.V)
        self.assertNotEqual(cur["turn"], t["turn"])
        self.assertEqual((cur["session_id"], cur["turn"][-4:]), ("", "0000"))
        self.assertEqual(sorted(p.name for p in (self.V / ".system" / "turns").iterdir()), ["current.json"])
        by = json.loads((self.versions_dir(t["turn"]) / "reverted.json").read_text(encoding="utf-8"))["by_turn"]
        self.assertNotEqual(cur["turn"], by)  # 裁定 (ii)：動作回合封起來，目前回合是再開的那個

    def test_acceptance_35_empty_prompt_head_is_left_out_of_sentence(self):
        """測試後修正 L6：守門自己開的回合問是空的，退回句與程式段 note 不印「（問：「」）」。"""
        t = self.cli_turn_with(self.card_rel, "改過了\n")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.splitlines()[0]
        self.assertEqual(line, f"已退回 {t['turn']}：還原 1、移除 0、成品 0 份")
        self.assertEqual(self.program_rows()[0]["note"], line)

    def test_finished_conversation_turn_writes_program_line_with_new_turn(self):
        """D-085 補：對話已答完（done true）後在終端機退回，寫程式段，turn 是現在時間的新代號（L2）。"""
        t = self.modify_card_in_turn()
        turn.set_done(self.V, SID)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["turn"], t["turn"])
        self.assertRegex(rows[0]["turn"], r"^\d{8}-\d{6}-0000$")
        self.assertEqual(turn.current(self.V)["session_id"], "")  # R2-M3：答完後退回換成 sid 空的回合
        self.assertEqual(turn.current(self.V, SID)["turn"], t["turn"])  # turns/<sid>.json 不動


if __name__ == "__main__":
    unittest.main()
