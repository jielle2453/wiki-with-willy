"""_lib/turn：回合（start／current／approve）、相對路徑（原始大小寫，I-027）、回合備份、大改動門檻（D-075）。"""
import json
import os
import shutil
import unittest

from tests._guard_fixture import make_vault
from kb._lib import turn  # noqa: E402

SID = "test-sid"


def manifest_lines(vault, turn_id):
    m = vault / ".system" / "versions" / turn_id / "manifest.jsonl"
    return [json.loads(x) for x in m.read_text(encoding="utf-8").splitlines() if x.strip()] if m.exists() else []


class Start(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_writes_session_file_and_current(self):
        t = turn.start(self.V, SID, "  幫我把  信義王 的現況\n改成已完工 ")
        sess = json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))
        cur = json.loads((self.V / ".system" / "turns" / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(sess, cur)
        self.assertEqual(sess["turn"], t["turn"])
        self.assertEqual(sess["prompt_head"], "幫我把 信義王 的現況 改成已完工")
        self.assertEqual(sess["session_id"], SID)
        self.assertFalse(sess["approved"])
        self.assertEqual(sess["schema_version"], 1)
        self.assertTrue(t["turn"].endswith("-test"), t["turn"])
        self.assertRegex(t["turn"], r"^\d{8}-\d{6}-test$")

    def test_prompt_head_cut_at_80(self):
        t = turn.start(self.V, SID, "一" * 100)
        self.assertEqual(len(t["prompt_head"]), 80)

    def test_no_session_id_uses_0000_and_only_current(self):
        t = turn.start(self.V, None, "x")
        self.assertTrue(t["turn"].endswith("-0000"))
        self.assertTrue((self.V / ".system" / "turns" / "current.json").exists())
        self.assertEqual(sorted(p.name for p in (self.V / ".system" / "turns").iterdir()), ["current.json"])

    def test_session_id_is_sanitised_for_filename(self):
        turn.start(self.V, "a/b:c d", "x")
        self.assertTrue((self.V / ".system" / "turns" / "a_b_c_d.json").exists())


class Current(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_with_sid_reads_session_file(self):
        turn.start(self.V, "other", "別人的")
        mine = turn.start(self.V, SID, "我的")
        turn.start(self.V, "other", "別人後來的")  # current.json 現在是別人的
        self.assertEqual(turn.current(self.V, SID)["turn"], mine["turn"])

    def test_without_sid_reads_current(self):
        turn.start(self.V, SID, "a")
        last = turn.start(self.V, "other", "b")
        self.assertEqual(turn.current(self.V)["turn"], last["turn"])

    def test_sid_without_file_falls_back_to_current(self):
        last = turn.start(self.V, "other", "b")
        self.assertEqual(turn.current(self.V, "nobody")["turn"], last["turn"])

    def test_nothing_starts_a_turn(self):
        t = turn.current(self.V, SID)
        self.assertEqual(t["prompt_head"], "")
        self.assertTrue((self.V / ".system" / "turns" / f"{SID}.json").exists())
        self.assertTrue((self.V / ".system" / "turns" / "current.json").exists())

    def test_nothing_and_no_sid_writes_only_current(self):
        turn.current(self.V)
        self.assertEqual(sorted(p.name for p in (self.V / ".system" / "turns").iterdir()), ["current.json"])


class RelPath(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keeps_original_case_and_forward_slashes(self):
        rel = turn.rel_path(self.V, str(self.V / "AI 產出" / "Draft.MD"), str(self.V))
        self.assertEqual(rel, "AI 產出/Draft.MD")

    def test_relative_to_cwd(self):
        rel = turn.rel_path(self.V, "案子/信義王/信義王.md", str(self.V))
        self.assertEqual(rel, "案子/信義王/信義王.md")

    def test_existing_part_uses_realpath_case(self):
        if os.name != "nt":
            self.skipTest("大小寫不敏感只在 Windows")
        rel = turn.rel_path(self.V, str(self.V / "案子" / "信義王" / "信義王.md").replace(".md", ".MD").lower(), str(self.V))
        self.assertEqual(rel, "案子/信義王/信義王.md")


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.t = turn.start(self.V, SID, "改")
        self.card = "案子/信義王/信義王.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_modify_copies_old_version_and_writes_manifest(self):
        before = (self.V / self.card).read_bytes()
        turn.backup(self.V, self.card, self.t["turn"])
        vdir = self.V / ".system" / "versions" / self.t["turn"]
        self.assertEqual((vdir / "files" / self.card).read_bytes(), before)
        rows = manifest_lines(self.V, self.t["turn"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], self.card)
        self.assertEqual(rows[0]["action"], "modify")
        self.assertIn("time", rows[0])
        info = json.loads((vdir / "turn.json").read_text(encoding="utf-8"))
        self.assertEqual(info["turn"], self.t["turn"])
        self.assertEqual(info["session_id"], SID)
        self.assertEqual(info["prompt_head"], "改")

    def test_same_path_same_turn_only_once(self):
        turn.backup(self.V, self.card, self.t["turn"])
        (self.V / self.card).write_text("改過了", encoding="utf-8")
        turn.backup(self.V, self.card, self.t["turn"])
        self.assertEqual(len(manifest_lines(self.V, self.t["turn"])), 1)
        self.assertNotEqual((self.V / ".system" / "versions" / self.t["turn"] / "files" / self.card).read_text(encoding="utf-8"), "改過了")

    def test_create_does_not_copy(self):
        rel = "案子/新卡/新卡.md"
        turn.backup(self.V, rel, self.t["turn"])
        rows = manifest_lines(self.V, self.t["turn"])
        self.assertEqual(rows[0]["action"], "create")
        self.assertFalse((self.V / ".system" / "versions" / self.t["turn"] / "files" / rel).exists())

    def test_delete_action_is_recorded_as_given(self):
        turn.backup(self.V, self.card, self.t["turn"], action="delete")
        rows = manifest_lines(self.V, self.t["turn"])
        self.assertEqual(rows[0]["action"], "delete")
        self.assertTrue((self.V / ".system" / "versions" / self.t["turn"] / "files" / self.card).exists())

    def test_entries_reads_manifest(self):
        self.assertEqual(turn.entries(self.V, self.t["turn"]), [])
        turn.backup(self.V, self.card, self.t["turn"])
        self.assertEqual([e["path"] for e in turn.entries(self.V, self.t["turn"])], [self.card])

    def test_copy_failure_raises_oserror(self):
        versions = self.V / ".system" / "versions"
        versions.write_text("我是檔不是夾", encoding="utf-8")
        with self.assertRaises(OSError):
            turn.backup(self.V, self.card, self.t["turn"])


class BeforeWrite(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        turn.start(self.V, SID, "全部加一行")
        self.cards = []
        for i in range(12):
            d = self.V / "案子" / f"案{i:02d}"
            d.mkdir()
            (d / f"案{i:02d}.md").write_text(f"# 案{i:02d}\n", encoding="utf-8")
            self.cards.append(d / f"案{i:02d}.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, zone="Z1"):
        return turn.before_write(self.V, str(path), str(self.V), zone, SID)

    def test_first_ten_allow_eleventh_denies_big_change(self):
        for p in self.cards[:10]:
            self.assertEqual(self.write(p), (True, "", ""))
        ok, code, reason = self.write(self.cards[10])
        self.assertFalse(ok)
        self.assertEqual(code, "big_change")
        self.assertIn("10 張", reason)
        self.assertIn("turn approve", reason)

    def test_same_card_again_does_not_count_twice(self):
        for p in self.cards[:10]:
            self.write(p)
        self.assertEqual(self.write(self.cards[0]), (True, "", ""))

    def test_new_card_does_not_count(self):
        for p in self.cards[:10]:
            self.write(p)
        ok, code, _ = self.write(self.V / "案子" / "新案" / "新案.md")
        self.assertTrue(ok, code)

    def test_index_zone_does_not_count(self):
        for p in self.cards[:10]:
            self.write(p)
        (self.V / "檔案索引" / "x.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.write(self.V / "檔案索引" / "x.md", zone="Z5"), (True, "", ""))

    def test_approve_lifts_limit(self):
        for p in self.cards[:10]:
            self.write(p)
        turn.approve(self.V, SID)
        self.assertTrue(turn.current(self.V, SID)["approved"])
        self.assertEqual(self.write(self.cards[10]), (True, "", ""))

    def test_new_turn_resets_count(self):
        for p in self.cards[:10]:
            self.write(p)
        turn.start(self.V, SID, "下一句")
        self.assertEqual(self.write(self.cards[10]), (True, "", ""))

    def test_backup_failure_returns_backup_failed(self):
        (self.V / ".system" / "versions").write_text("我是檔不是夾", encoding="utf-8")
        ok, code, reason = self.write(self.cards[0])
        self.assertFalse(ok)
        self.assertEqual(code, "backup_failed")
        self.assertIn("備份失敗", reason)

    def test_z3_write_backs_up(self):
        p = self.V / "AI 產出" / "x.md"
        p.write_text("舊", encoding="utf-8")
        self.assertEqual(self.write(p, zone="Z3"), (True, "", ""))
        t = turn.current(self.V, SID)
        self.assertEqual((self.V / ".system" / "versions" / t["turn"] / "files" / "AI 產出" / "x.md").read_text(encoding="utf-8"), "舊")


if __name__ == "__main__":
    unittest.main()
