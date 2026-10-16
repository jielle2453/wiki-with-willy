"""_lib/turn：回合（start／current／approve）、相對路徑（原始大小寫，I-027）、回合備份、大改動門檻（D-075）。"""
import json
import os
import shutil
import unittest
from pathlib import Path

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

    def test_prompt_head_cut_at_config_prompt_head_max(self):
        """工作日誌（B-028 第 2 項）：上限從 80 改讀 config prompt_head_max，預設 200；客戶端可覆寫。"""
        t = turn.start(self.V, SID, "一" * 250)
        self.assertEqual(len(t["prompt_head"]), 200)
        self.assertEqual(len(turn.start(self.V, SID, "二" * 150)["prompt_head"]), 150)
        (self.V / ".system" / "config.json").write_text(json.dumps({"prompt_head_max": 10}), encoding="utf-8")
        self.assertEqual(len(turn.start(self.V, SID, "三" * 50)["prompt_head"]), 10)
        self.assertFalse(hasattr(turn, "PROMPT_HEAD_MAX"))

    def test_prompt_head_masks_secrets_before_cut(self):
        """第 5b 節（D-081、B-027）：先遮值再壓空白、截 prompt_head_max 字。"""
        t = turn.start(self.V, SID, "幫我登入 ERP，帳號 jie  密碼 abc123")
        self.assertEqual(t["prompt_head"], "幫我登入 ERP，帳號███")  # 第六輪 W3：遮到子句尾
        t2 = turn.start(self.V, SID, "密碼是" + "x" * 300 + "，謝謝")
        self.assertNotIn("xxx", t2["prompt_head"])
        self.assertLessEqual(len(t2["prompt_head"]), 200)
        self.assertEqual(turn.start(self.V, SID, "今天天氣好")["prompt_head"], "今天天氣好")

    def test_start_writes_note_and_done(self):
        t = turn.start(self.V, SID, "改一下")
        self.assertEqual((t["note"], t["done"]), ("", False))
        sess = json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual((sess["note"], sess["done"]), ("", False))

    def test_old_turn_file_without_note_and_done(self):
        """疑義裁定 3：舊回合檔缺 done 視為 true（升級前那一句不補記），缺 note 視為空字串，不遷移。"""
        d = self.V / ".system" / "turns"
        d.mkdir(parents=True)
        old = {"schema_version": 1, "turn": "20261014-100000-test", "session_id": SID,
               "started": "2026-10-14T10:00:00", "prompt_head": "舊的", "approved": False}
        (d / f"{SID}.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        t = turn.current(self.V, SID)
        self.assertEqual((t["note"], t["done"]), ("", True))
        self.assertNotIn("done", json.loads((d / f"{SID}.json").read_text(encoding="utf-8")))


class SetFields(unittest.TestCase):
    """set_note／set_done／set_prompt_head：改 turns/<sid>.json，current.json 是同回合時一起改。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.d = self.V / ".system" / "turns"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, name):
        return json.loads((self.d / name).read_text(encoding="utf-8"))

    def test_set_note_done_prompt_head_on_session_and_current(self):
        turn.start(self.V, SID, "改一下")
        self.assertEqual(turn.set_note(self.V, SID, "改了現況")["note"], "改了現況")
        turn.set_done(self.V, SID)
        turn.set_prompt_head(self.V, SID, "遮好了")
        for name in (f"{SID}.json", "current.json"):
            data = self.read(name)
            self.assertEqual((data["note"], data["done"], data["prompt_head"]), ("改了現況", True, "遮好了"), name)

    def test_current_of_other_turn_is_not_touched(self):
        turn.start(self.V, SID, "我的")
        other = turn.start(self.V, "other-sid", "別人的")
        turn.set_note(self.V, SID, "只改我的")
        self.assertEqual(self.read(f"{SID}.json")["note"], "只改我的")
        self.assertEqual(self.read("current.json")["turn"], other["turn"])
        self.assertEqual(self.read("current.json")["note"], "")

    def test_without_session_id_changes_current(self):
        turn.start(self.V, None, "CLI")
        turn.set_note(self.V, "", "程式做的")
        self.assertEqual(self.read("current.json")["note"], "程式做的")

    def test_missing_turn_returns_none(self):
        self.assertIsNone(turn.set_done(self.V, "nobody"))
        self.assertFalse((self.d / "nobody.json").exists())

    def test_no_session_id_uses_0000_and_only_current(self):
        t = turn.start(self.V, None, "x")
        self.assertTrue(t["turn"].endswith("-0000"))
        self.assertTrue((self.V / ".system" / "turns" / "current.json").exists())
        self.assertEqual(sorted(p.name for p in (self.V / ".system" / "turns").iterdir()), ["current.json"])

    def test_turn_ids_are_monotonic_even_when_clock_lags(self):
        """裁定：新代號取 max(現在, 既有最大代號 + 1 秒)。同一秒三句話，第三個代號最大；時鐘落後也不會排到舊的前面。"""
        from datetime import datetime
        from unittest import mock
        fixed = datetime(2026, 10, 13, 15, 30, 12)
        with mock.patch.object(turn, "_now", lambda: fixed):
            a = turn.start(self.V, SID, "一")
            turn.backup(self.V, "案子/信義王/信義王.md", a["turn"])
            b = turn.start(self.V, SID, "二")
            turn.backup(self.V, "案子/信義王/信義王.md", b["turn"])
            c = turn.start(self.V, SID, "三")
        self.assertEqual([a["turn"], b["turn"], c["turn"]],
                         ["20261013-153012-test", "20261013-153013-test", "20261013-153014-test"])
        self.assertLess(a["turn"], b["turn"])
        self.assertLess(b["turn"], c["turn"])
        self.assertEqual(c["started"], "2026-10-13T15:30:14")

    def test_new_turn_sorts_after_every_existing_versions_dir(self):
        from datetime import datetime
        from unittest import mock
        (self.V / ".system" / "versions" / "20261013-153020-test").mkdir(parents=True)
        with mock.patch.object(turn, "_now", lambda: datetime(2026, 10, 13, 15, 30, 12)):
            t = turn.start(self.V, SID, "晚來的時鐘")
        self.assertEqual(t["turn"], "20261013-153021-test")

    def test_far_future_turn_dir_is_ignored_and_logged(self):
        """第二輪 R2-L4（驗收 37）：比現在晚超過一天的代號不算，新代號是現在，寫一行 log。"""
        from datetime import datetime
        from unittest import mock
        (self.V / ".system" / "versions" / "20991231-235958-zzzz").mkdir(parents=True)
        with mock.patch.object(turn, "_now", lambda: datetime(2026, 10, 13, 15, 30, 12)):
            t = turn.start(self.V, SID, "現在")
        self.assertEqual(t["turn"], "20261013-153012-test")
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("20991231-235958-zzzz", log[0])

    def test_turn_dir_within_a_day_ahead_still_counts(self):
        from datetime import datetime
        from unittest import mock
        (self.V / ".system" / "versions" / "20261014-100000-test").mkdir(parents=True)
        with mock.patch.object(turn, "_now", lambda: datetime(2026, 10, 13, 15, 30, 12)):
            t = turn.start(self.V, SID, "時鐘差幾小時")
        self.assertEqual(t["turn"], "20261014-100001-test")

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

    def test_git_bash_drive_form_is_understood(self):
        """第一輪 L1：/c/... 先轉 C:/... 再 realpath，不會變成 C:/c/...。"""
        if os.name != "nt":
            self.skipTest("Git Bash 寫法只在 Windows 有意義")
        p = str(self.V / "案子" / "信義王" / "信義王.md").replace("\\", "/")
        gb = "/" + p[0].lower() + p[2:]
        self.assertEqual(turn.rel_path(self.V, gb, str(self.V)), "案子/信義王/信義王.md")
        self.assertEqual(turn.rel_path(self.V, "/" + p[0].upper() + p[2:], str(self.V)), "案子/信義王/信義王.md")

    def test_outside_vault_raises(self):
        with self.assertRaises(ValueError):
            turn.rel_path(self.V, str(self.tmp / "x.md"), str(self.V))

    def test_text_forms_tilde_long_prefix_file_uri_and_quotes(self):
        """第二輪 R2-L2／L7（驗收 35）：~、\\\\?\\、file://、外圍引號都走 paths._text_forms。"""
        if os.name != "nt":
            self.skipTest("這些寫法在 Windows 驗")
        card = self.V / "案子" / "信義王" / "信義王.md"
        want = "案子/信義王/信義王.md"
        home = Path(os.path.expanduser("~")).resolve()
        resolved = card.resolve()
        if str(resolved).lower().startswith(str(home).lower() + os.sep):
            tilde = "~/" + str(resolved)[len(str(home)) + 1:].replace("\\", "/")
            self.assertEqual(turn.rel_path(self.V, tilde, str(self.V)), want)
        self.assertEqual(turn.rel_path(self.V, "\\\\?\\" + str(resolved), str(self.V)), want)
        self.assertEqual(turn.rel_path(self.V, "file:///" + str(resolved).replace("\\", "/"), str(self.V)), want)
        self.assertEqual(turn.rel_path(self.V, f'"{resolved}"', str(self.V)), want)


class ZoneOf(unittest.TestCase):
    """第一輪 L8：Z1 判斷只有一份，sources 可一次載入傳下去。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zone_of_card_and_output(self):
        self.assertEqual(turn.zone_of(self.V, "案子/信義王/信義王.md"), "Z1")
        self.assertEqual(turn.zone_of(self.V, "AI 產出/x.md"), "Z3")
        self.assertEqual(turn.zone_of(self.V, "檔案索引/x.md"), "Z5")

    def test_zone_of_accepts_preloaded_sources(self):
        from kb._lib import sources as sources_lib
        src = sources_lib.load(self.V)
        self.assertEqual(turn.zone_of(self.V, "案子/信義王/信義王.md", src), "Z1")

    def test_versions_has_no_second_copy(self):
        from kb import versions
        self.assertFalse(hasattr(versions, "_zone_of"))
        self.assertFalse(hasattr(turn, "_is_synthesis"))


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

    def test_held_lock_times_out_without_copying(self):
        """第一輪 M1／L3：複製與記帳在同一把鎖裡；鎖拿不到就 TimeoutError，files/ 不留半截副本。"""
        vdir = turn.turn_dir(self.V, self.t["turn"])
        (vdir / "manifest.jsonl.lock").write_text("busy", encoding="utf-8")
        import time
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            turn.backup(self.V, self.card, self.t["turn"])
        self.assertLess(time.time() - t0, 6)
        self.assertFalse((vdir / "files").exists())
        self.assertEqual(turn.entries(self.V, self.t["turn"]), [])

    def test_stale_lock_is_cleared_and_backup_proceeds(self):
        vdir = turn.turn_dir(self.V, self.t["turn"])
        lock = vdir / "manifest.jsonl.lock"
        lock.write_text("stale", encoding="utf-8")
        import time
        old = time.time() - 60
        os.utime(lock, (old, old))
        turn.backup(self.V, self.card, self.t["turn"])
        self.assertFalse(lock.exists())
        self.assertEqual(len(manifest_lines(self.V, self.t["turn"])), 1)

    def test_append_after_half_line_starts_on_a_new_line(self):
        """第二輪 R2-L5：manifest 檔尾是半截行沒換行，追加前先補換行，新的一行不會被吃掉。"""
        turn.backup(self.V, self.card, self.t["turn"])
        m = self.V / ".system" / "versions" / self.t["turn"] / "manifest.jsonl"
        with open(m, "a", encoding="utf-8", newline="\n") as f:
            f.write('{"path": "半截')
        turn.backup(self.V, "案子/新卡/新卡.md", self.t["turn"])
        rows = turn.entries(self.V, self.t["turn"])
        self.assertEqual([e["path"] for e in rows], [self.card, "案子/新卡/新卡.md"])
        self.assertEqual(m.read_bytes().count(b"\n"), 3)

    def test_entries_skips_bad_line_and_logs(self):
        """第一輪 L4：manifest 半截行跳過並寫一行 hook-errors.log，不讓之後全部 deny。"""
        turn.backup(self.V, self.card, self.t["turn"])
        m = self.V / ".system" / "versions" / self.t["turn"] / "manifest.jsonl"
        with open(m, "a", encoding="utf-8", newline="\n") as f:
            f.write("{bad\n")
        rows = turn.entries(self.V, self.t["turn"])
        self.assertEqual([e["path"] for e in rows], [self.card])
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("manifest", log[0])


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

    def test_held_lock_returns_backup_failed_quickly(self):
        """第一輪 M1：鎖等 3 秒就放棄，回 backup_failed（TimeoutError 是 OSError）。"""
        t = turn.current(self.V, SID)
        vdir = turn.turn_dir(self.V, t["turn"])
        (vdir / "manifest.jsonl.lock").write_text("busy", encoding="utf-8")
        import time
        t0 = time.time()
        ok, code, _reason = self.write(self.cards[0])
        self.assertLess(time.time() - t0, 6)
        self.assertEqual((ok, code), (False, "backup_failed"))

    def test_bad_config_value_falls_back_to_default_and_logs(self):
        """第一輪 L5：big_change_synthesis_max 不是整數就用預設 10，寫一行 hook-errors.log，不 deny。"""
        (self.V / ".system" / "config.json").write_text('{"big_change_synthesis_max": "十"}', encoding="utf-8")
        for p in self.cards[:10]:
            self.assertEqual(self.write(p), (True, "", ""))
        ok, code, _ = self.write(self.cards[10])
        self.assertEqual((ok, code), (False, "big_change"))
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertIn("big_change_synthesis_max", log)

    def test_z3_write_backs_up(self):
        p = self.V / "AI 產出" / "x.md"
        p.write_text("舊", encoding="utf-8")
        self.assertEqual(self.write(p, zone="Z3"), (True, "", ""))
        t = turn.current(self.V, SID)
        self.assertEqual((self.V / ".system" / "versions" / t["turn"] / "files" / "AI 產出" / "x.md").read_text(encoding="utf-8"), "舊")


class Inherit(unittest.TestCase):
    """測試後修正第二輪 R2-M2：對話裡退回自己這句，新回合繼承原回合的問、note，記 journal_turn（一路繼承）。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inherit_keeps_prompt_note_and_records_journal_turn(self):
        t = turn.start(self.V, SID, "改一下卡")
        t = turn.set_note(self.V, SID, "改了卡")
        n1 = turn.start(self.V, SID, "退回 x", inherit=t)
        self.assertNotEqual(n1["turn"], t["turn"])
        self.assertTrue(n1["turn"].endswith("-test"))
        self.assertEqual((n1["prompt_head"], n1["note"], n1["journal_turn"], n1["done"]),
                         ("改一下卡", "改了卡", t["turn"], False))
        n2 = turn.start(self.V, SID, "退回 y", inherit=n1)
        self.assertEqual(n2["journal_turn"], t["turn"])
        sess = json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))
        cur = json.loads((self.V / ".system" / "turns" / "current.json").read_text(encoding="utf-8"))
        self.assertEqual((sess["turn"], cur["turn"], sess["journal_turn"]), (n2["turn"],) * 2 + (t["turn"],))

    def test_plain_start_has_no_journal_turn(self):
        self.assertNotIn("journal_turn", turn.start(self.V, SID, "一"))
        self.assertFalse(hasattr(turn, "mint"))

    def test_journal_key(self):
        t = turn.start(self.V, SID, "一")
        self.assertEqual(turn.journal_key(t), t["turn"])
        self.assertEqual(turn.journal_key(turn.start(self.V, SID, "退回", inherit=t)), t["turn"])


class PromptHeadOrder(unittest.TestCase):
    """測試後修正 M2：先壓空白再遮，值跟關鍵詞隔空行也遮得到。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blank_line_between_keyword_and_value(self):
        self.assertEqual(turn.start(self.V, SID, "幫我登入，密碼如下：\n\nabc123")["prompt_head"], "幫我登入，密碼███")


if __name__ == "__main__":
    unittest.main()
