"""_lib/journal：工作日誌（D-082～D-086、D-061、D-065、D-067）。jsonl 一行分兩次寫（upsert）、程式段追加、壞行跳過、
地板句、結果截斷、跨月、最近幾段、渲染等於 fixture、遮值、兩個程序同時寫。"""
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date

from tests._guard_fixture import FIXTURES, SCRIPTS, make_vault
from kb._lib import journal, turn  # noqa: E402

SID = "test-sid"


def this_month():
    return date.today().strftime("%Y-%m")


def prev_month(month):
    y, m = int(month[:4]), int(month[5:])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def turn_data(turn_id, prompt="改一下", sid=SID):
    t = turn_id[:8]
    hms = turn_id[9:15]
    return {"schema_version": 1, "turn": turn_id, "session_id": sid,
            "started": f"{t[:4]}-{t[4:6]}-{t[6:]}T{hms[:2]}:{hms[2:4]}:{hms[4:]}",
            "prompt_head": prompt, "approved": False, "note": "", "done": False}


def month_turn_id(month, day="15", hms="120000", tag="test"):
    return f"{month.replace('-', '')}{day}-{hms}-{tag}"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, month):
        p = journal.jsonl_path(self.V, month)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def errors(self):
        p = self.V / ".system" / "hook-errors.log"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


class Paths(Base):
    def test_month_of_turn_id(self):
        self.assertEqual(journal.month_of("20261015-180536-ea64"), "2026-10")
        self.assertEqual(journal.month_of("亂寫"), this_month())

    def test_paths(self):
        self.assertEqual(journal.jsonl_path(self.V, "2026-10"), self.V / ".system" / "journal" / "2026-10.jsonl")
        self.assertEqual(journal.md_path(self.V, "2026-10"), self.V / "系統" / "工作日誌" / "2026-10.md")


class OpenComplete(Base):
    def test_open_then_complete_is_one_line(self):
        td = turn_data("20261015-180536-test", "改一下")
        journal.begin_turn(self.V, None, td)
        rows = self.rows("2026-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"schema_version": 1, "kind": "turn", "turn": "20261015-180536-test", "session_id": SID,
                                   "time": "2026-10-15T18:05:36", "prompt": "改一下", "done": False})
        journal.complete_turn(self.V, td, "好了", "沒有改動", "program", [], interrupted=False)
        rows = self.rows("2026-10")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["done"])
        self.assertEqual(r["result"], "好了")
        self.assertFalse(r["result_truncated"])
        self.assertFalse(r["interrupted"])
        self.assertEqual(r["note"], "沒有改動")
        self.assertEqual(r["note_by"], "program")
        self.assertEqual(r["changed"], [])
        self.assertRegex(r["ended"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(r["prompt"], "改一下")

    def test_open_twice_does_not_duplicate(self):
        td = turn_data("20261015-180536-test")
        journal.begin_turn(self.V, None, td)
        journal.begin_turn(self.V, None, td)
        journal.begin_turn(self.V, None, turn_data("20261015-180600-test", "第二句"))
        self.assertEqual([r["turn"] for r in self.rows("2026-10")], ["20261015-180536-test", "20261015-180600-test"])

    def test_complete_without_open_writes_line(self):
        td = turn_data("20261015-180536-test")
        journal.complete_turn(self.V, td, "好了", "沒有改動", "program", [], interrupted=False)
        rows = self.rows("2026-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "改一下")

    def test_changed_keeps_only_path_and_action(self):
        td = turn_data("20261015-180536-test")
        journal.complete_turn(self.V, td, "好了", "改了 1 張綜合卡", "program",
                              [{"path": "案子/信義王/信義王.md", "action": "modify", "time": "2026-10-15T18:05:40"}], False)
        self.assertEqual(self.rows("2026-10")[0]["changed"], [{"path": "案子/信義王/信義王.md", "action": "modify"}])

    def test_open_renders_md(self):
        journal.begin_turn(self.V, None, turn_data("20261015-180536-test"))
        md = journal.md_path(self.V, "2026-10").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# 工作日誌 2026-10\n\n這是什麼："), md)
        self.assertIn("- 結果：（還沒結束）", md)

    def test_cross_month_turn_stays_in_opening_month(self):
        td = turn_data("20260930-235959-test")
        journal.begin_turn(self.V, None, td)
        journal.complete_turn(self.V, td, "好了", "沒有改動", "program", [], False)
        self.assertEqual(len(self.rows("2026-09")), 1)
        self.assertTrue(self.rows("2026-09")[0]["done"])
        self.assertFalse(journal.jsonl_path(self.V, "2026-10").exists())


class Masking(Base):
    def test_result_prompt_and_ai_note_are_masked(self):
        td = turn_data("20261015-180536-test", "幫我登入，密碼 abc")
        journal.begin_turn(self.V, None, td)
        journal.complete_turn(self.V, td, "已用密碼 abc 登入", "記下密碼 abc", "ai", [], False)
        r = self.rows("2026-10")[0]
        self.assertEqual(r["prompt"], "幫我登入，密碼 ███")
        self.assertEqual(r["result"], "已用密碼 ███ 登入")
        self.assertEqual(r["note"], "記下密碼 ███")
        for p in (self.V / ".system", self.V / "系統"):
            for f in p.rglob("*"):
                if f.is_file():
                    self.assertNotIn("abc", f.read_text(encoding="utf-8", errors="ignore"), f)

    def test_truncated_tail_survives_masking(self):
        td = turn_data("20261015-180536-test")
        journal.complete_turn(self.V, td, "已用密碼 abc" + journal.TRUNCATED_TAIL, "沒有改動", "program", [], False,
                              result_truncated=True)
        r = self.rows("2026-10")[0]
        self.assertEqual(r["result"], "已用密碼 ███" + journal.TRUNCATED_TAIL)
        self.assertTrue(r["result_truncated"])

    def test_program_note_is_not_masked(self):
        journal.record_program(self.V, "已登記「密碼本」（../raw/密碼本，1 份）", [], "folders add")
        self.assertEqual(self.rows(this_month())[0]["note"], "已登記「密碼本」（../raw/密碼本，1 份）")


class UpdateTurn(Base):
    def test_update_changes_only_given_fields(self):
        td = turn_data("20261015-180536-test")
        journal.begin_turn(self.V, None, td)
        self.assertTrue(journal.update_turn(self.V, td["turn"], note="改口", note_by="ai"))
        r = self.rows("2026-10")[0]
        self.assertEqual((r["note"], r["note_by"], r["prompt"], r["done"]), ("改口", "ai", "改一下", False))

    def test_update_missing_line_returns_false(self):
        self.assertFalse(journal.update_turn(self.V, "20261015-180536-test", note="x"))
        self.assertFalse(journal.jsonl_path(self.V, "2026-10").exists())

    def test_update_does_not_touch_program_line_with_same_turn(self):
        journal.record_program(self.V, "已登記", [], "folders add")
        tid = self.rows(this_month())[0]["turn"]
        self.assertFalse(journal.update_turn(self.V, tid, note="x"))


class Program(Base):
    def test_record_program_appends_and_never_upserts(self):
        journal.record_program(self.V, "一", [], "folders add")
        journal.record_program(self.V, "二", [{"path": ".system/sources.json", "action": "modify"}], "folders exclude")
        rows = self.rows(this_month())
        self.assertEqual([r["note"] for r in rows], ["一", "二"])
        self.assertEqual(rows[1], {"schema_version": 1, "kind": "program", "turn": rows[1]["turn"],
                                   "time": rows[1]["time"], "command": "folders exclude", "note": "二",
                                   "changed": [{"path": ".system/sources.json", "action": "modify"}]})

    def test_record_program_has_no_turn_id_parameter(self):
        """測試後修正 L2：程式段一律用現在時間組新代號，月份就是現在。"""
        with self.assertRaises(TypeError):
            journal.record_program(self.V, "x", [], "folders add", turn_id="20260901-120000-0000")

    def test_record_program_without_turn_uses_now_and_does_not_touch_turns(self):
        journal.record_program(self.V, "建立知識庫：建了 3 項", [], "init")
        r = self.rows(this_month())[0]
        self.assertRegex(r["turn"], r"^\d{8}-\d{6}-0000$")
        self.assertEqual(r["time"][:10].replace("-", ""), r["turn"][:8])
        self.assertFalse((self.V / ".system" / "turns").exists())


class BadLines(Base):
    def test_bad_line_skipped_logged_and_kept_on_rewrite(self):
        p = journal.jsonl_path(self.V, "2026-10")
        p.parent.mkdir(parents=True)
        good = json.dumps({"schema_version": 1, "kind": "program", "turn": "20261015-100000-0000",
                           "time": "2026-10-15T10:00:00", "command": "init", "note": "建立", "changed": []},
                          ensure_ascii=False)
        p.write_text(good + "\n{bad\n", encoding="utf-8")
        rows = journal.read_lines(self.V, "2026-10")
        self.assertEqual([r["note"] for r in rows], ["建立"])
        self.assertEqual(len(self.errors()), 0)  # 測試後修正 L3：只有寫入路徑記
        journal.read_lines(self.V, "2026-10", log=True)
        self.assertEqual(len(self.errors()), 1)
        journal.begin_turn(self.V, None, turn_data("20261015-180536-test"))
        lines = p.read_text(encoding="utf-8").splitlines()
        self.assertIn("{bad", lines)
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(self.errors()), 2)  # begin_turn 記一次；渲染不再記
        md = journal.md_path(self.V, "2026-10").read_text(encoding="utf-8")
        self.assertIn("程式 init", md)

    def test_recent_does_not_log_bad_lines(self):
        journal.begin_turn(self.V, None, turn_data(month_turn_id(this_month())))
        p = journal.jsonl_path(self.V, this_month())
        p.write_text(p.read_text(encoding="utf-8") + "{bad\n", encoding="utf-8")
        self.assertEqual(len(journal.recent(self.V, 5)), 1)
        self.assertEqual(self.errors(), [])


class RenderFailure(Base):
    def test_render_error_is_logged_not_raised(self):
        """測試後修正 M3：md 寫不了只寫 log，jsonl 照寫、呼叫端不知道（md 可用 journal render 重建）。"""
        md = journal.md_path(self.V, "2026-10")
        md.mkdir(parents=True)
        td = turn_data("20261015-180536-test")
        journal.begin_turn(self.V, None, td)
        journal.complete_turn(self.V, td, "好了", "沒有改動", "program", [], False)
        self.assertTrue(self.rows("2026-10")[0]["done"])
        self.assertEqual(len(self.errors()), 2)
        self.assertEqual(self.errors()[0].split("\t")[1], "journal")


class BeginTurn(Base):
    """測試後修正 L1：上一句中斷補記與這一句開口同月就一次鎖。"""

    def test_flush_previous_and_open_new_in_one_lock(self):
        prev = turn_data("20261015-180536-test", "第一句")
        journal.begin_turn(self.V, None, prev)
        new = turn_data("20261015-180600-test", "第二句")
        calls = []
        real = journal._lock

        def counting(p):
            calls.append(p)
            return real(p)
        from unittest import mock
        with mock.patch.object(journal, "_lock", counting):
            journal.begin_turn(self.V, prev, new)
        self.assertEqual(len(calls), 1)
        rows = {r["prompt"]: r for r in self.rows("2026-10")}
        self.assertEqual((rows["第一句"]["interrupted"], rows["第一句"]["result"]), (True, journal.INTERRUPTED_RESULT))
        self.assertFalse(rows["第二句"]["done"])

    def test_previous_done_or_missing_only_opens(self):
        prev = dict(turn_data("20261015-180536-test", "第一句"), done=True)
        journal.begin_turn(self.V, prev, turn_data("20261015-180600-test", "第二句"))
        journal.begin_turn(self.V, None, turn_data("20261015-180700-test", "第三句"))
        self.assertEqual([r["prompt"] for r in self.rows("2026-10")], ["第二句", "第三句"])

    def test_previous_ai_note_is_kept(self):
        prev = dict(turn_data("20261015-180536-test", "第一句"), note="中斷前記的")
        journal.begin_turn(self.V, prev, turn_data("20261015-180600-test", "第二句"))
        first = self.rows("2026-10")[0]
        self.assertEqual((first["note"], first["note_by"]), ("中斷前記的", "ai"))

    def test_cross_month_previous_goes_to_its_month(self):
        prev = turn_data("20260930-235959-test", "九月")
        journal.begin_turn(self.V, prev, turn_data("20261001-000010-test", "十月"))
        self.assertTrue(self.rows("2026-09")[0]["interrupted"])
        self.assertEqual(self.rows("2026-10")[0]["prompt"], "十月")


class JournalTurn(Base):
    """第二輪 R2-M2：對話裡退回自己這句後，回合檔是新代號 N、journal_turn 是原回合 T；日誌那行的 key 是 T。"""

    def test_complete_with_journal_turn_updates_original_line_and_keeps_time(self):
        t = turn_data("20261015-180536-test", "改一下卡")
        journal.begin_turn(self.V, None, t)
        n = dict(turn_data("20261015-180540-test", "改一下卡"), journal_turn=t["turn"])
        journal.complete_turn(self.V, n, "改了又退回了", "沒有改動", "program", [], False)
        rows = self.rows("2026-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["turn"], rows[0]["time"], rows[0]["result"]),
                         (t["turn"], "2026-10-15T18:05:36", "改了又退回了"))

    def test_flush_with_journal_turn_marks_original_line(self):
        t = turn_data("20261015-180536-test", "改一下卡")
        journal.begin_turn(self.V, None, t)
        n = dict(turn_data("20261015-180540-test", "改一下卡"), journal_turn=t["turn"], note="退了")
        journal.begin_turn(self.V, n, turn_data("20261015-180600-test", "下一句"))
        rows = {r["turn"]: r for r in self.rows("2026-10")}
        self.assertEqual(set(rows), {t["turn"], "20261015-180600-test"})
        self.assertEqual((rows[t["turn"]]["interrupted"], rows[t["turn"]]["note"]), (True, "退了"))

    def test_finish_fields_unions_two_manifests_later_action_wins(self):
        tid, nid = "20261015-180536-test", "20261015-180540-test"
        turn.backup(self.V, "案子/信義王/信義王.md", tid)            # modify
        turn.backup(self.V, "AI 產出/x.md", tid)                     # create
        turn.backup(self.V, "AI 產出/x.md", nid, "delete")           # 後者：delete
        turn.backup(self.V, ".system/sources.json", nid)             # modify
        note, by, changed = journal.finish_fields(self.V, dict(turn_data(nid), journal_turn=tid))
        self.assertEqual(changed, [{"path": "案子/信義王/信義王.md", "action": "modify"},
                                   {"path": "AI 產出/x.md", "action": "delete"},
                                   {"path": ".system/sources.json", "action": "modify"}])
        self.assertEqual((note, by), ("改了 1 張綜合卡、刪了 1 份 AI 產出、改了設定與狀態檔", "program"))

    def test_update_turn_by_journal_key(self):
        t = turn_data("20261015-180536-test")
        journal.begin_turn(self.V, None, t)
        n = dict(turn_data("20261015-180540-test"), journal_turn=t["turn"])
        self.assertTrue(journal.update_turn(self.V, turn.journal_key(n), note="退了", note_by="ai"))
        self.assertEqual(self.rows("2026-10")[0]["note"], "退了")


class LockPermission(Base):
    def test_permission_error_until_deadline_is_timeout(self):
        """測試後修正 L1：_lock 到期還是 PermissionError 也算逾時（TimeoutError）。"""
        from unittest import mock
        from kb._lib import io as kio
        with mock.patch.object(kio, "LOCK_TIMEOUT", 0.3), \
                mock.patch.object(journal.FileLock, "__enter__", side_effect=PermissionError("拒絕")):
            with self.assertRaises(TimeoutError):
                journal.begin_turn(self.V, None, turn_data("20261015-180536-test"))


class FloorNote(Base):
    def backup(self, tid, rel, action=None):
        turn.backup(self.V, rel, tid, action)

    def test_empty_turn_has_no_changes(self):
        self.assertEqual(journal.floor_note(self.V, "20261015-180536-test"), "沒有改動")

    def test_each_zone_and_action(self):
        tid = "20261015-180536-test"
        self.backup(tid, "案子/信義王/信義王.md")              # Z1 modify
        self.backup(tid, "案子/新案/新案.md")                  # Z1 create
        self.backup(tid, "案子/舊案/舊案.md", "delete")        # Z1 delete
        self.backup(tid, "檔案索引/客戶案件/a.txt.md")          # Z5 create
        self.backup(tid, "AI 產出/x.md")                       # Z3 create
        self.backup(tid, "AI 產出/y.md")                       # Z3 create
        self.backup(tid, ".system/sources.json")               # Z7
        self.backup(tid, "CLAUDE.md")                          # Z7
        self.backup(tid, "系統/待處理.md")                      # 其他區
        self.assertEqual(journal.floor_note(self.V, tid),
                         "改了 1 張綜合卡、新增 1 張綜合卡、刪了 1 張綜合卡、新增 1 張檔案卡、新增 2 份 AI 產出、"
                         "改了設定與狀態檔、改了 1 個檔")

    def test_z5_and_z3_modify_delete(self):
        tid = "20261015-180536-test"
        (self.V / "檔案索引" / "b.md").write_text("x", encoding="utf-8")
        (self.V / "AI 產出" / "z.md").write_text("x", encoding="utf-8")
        self.backup(tid, "檔案索引/b.md")
        self.backup(tid, "檔案索引/c.md", "delete")
        self.backup(tid, "AI 產出/z.md")
        self.backup(tid, "AI 產出/w.md", "delete")
        self.assertEqual(journal.floor_note(self.V, tid),
                         "改了 1 張檔案卡、刪了 1 張檔案卡、改了 1 份 AI 產出、刪了 1 份 AI 產出")


class CutResult(unittest.TestCase):
    def test_whole_text_is_one_line(self):
        """測試後修正 M2：抽段後一律壓成一行再遮再截。"""
        self.assertEqual(journal.cut_result("  好了。\n\n下一步看合約。 \n", 300), ("好了。 下一步看合約。", False))

    def test_conclusion_section(self):
        text = "改好了。\n\n## 結論\n信義王改成已完工。\n\n## 細節\n很多字"
        self.assertEqual(journal.cut_result(text, 300), ("信義王改成已完工。", False))

    def test_summary_section_runs_to_next_same_or_higher_heading(self):
        text = "# 報告\n前言\n## 摘要\n第一段\n### 小節\n小節內容\n## 其他\n不要"
        self.assertEqual(journal.cut_result(text, 300), ("第一段 ### 小節 小節內容", False))

    def test_value_after_blank_line_is_masked(self):
        """M2：值跟關鍵詞隔空行，先壓空白再遮才遮得到。"""
        self.assertEqual(journal.cut_result("密碼如下：\n\nabc123", 300), ("密碼如下： ███", False))

    def test_acceptance_38_keyword_outside_conclusion_section(self):
        """第二輪 R2-M1 裁定：全文壓一行遮過有東西被遮，就不抽段、用整段遮過的結果；三句都不能漏值。"""
        for text in ("## 結論：新的管理員密碼\n\nabc123\n\n其他", "密碼是\n## 結論\nabc123", "新的密碼如下\n\n## 結論\nabc123"):
            with self.subTest(text=text):
                out, _cut = journal.cut_result(text, 300)
                self.assertNotIn("abc123", out)
                self.assertIn("███", out)

    def test_no_secret_still_takes_conclusion(self):
        self.assertEqual(journal.cut_result("前言很多\n\n## 結論\n改好了", 300), ("改好了", False))

    def test_mask_before_cut(self):
        """先遮再截：遮掉的值不佔字數（先截會截在 x 中間、判成被截斷）。"""
        self.assertEqual(journal.cut_result("字" * 290 + "，密碼 " + "x" * 50, 300), ("字" * 290 + "，密碼 ███", False))

    def test_truncated_adds_tail(self):
        out, cut = journal.cut_result("字" * 400, 300)
        self.assertTrue(cut)
        self.assertEqual(out, "字" * 300 + "…全文見對話")

    def test_empty(self):
        self.assertEqual(journal.cut_result("", 300), ("（沒有文字回答）", False))
        self.assertEqual(journal.cut_result("  \n ", 300), ("（沒有文字回答）", False))


class Recent(Base):
    def test_recent_across_months_newest_first(self):
        cur = this_month()
        prev = prev_month(cur)
        journal.begin_turn(self.V, None, turn_data(month_turn_id(prev, "10"), "上月一"))
        journal.begin_turn(self.V, None, turn_data(month_turn_id(prev, "11"), "上月二"))
        journal.begin_turn(self.V, None, turn_data(month_turn_id(cur, "01")))
        self.assertEqual([r["prompt"] for r in journal.recent(self.V, 2)], ["改一下", "上月二"])
        self.assertEqual(len(journal.recent(self.V, 10)), 3)

    def test_recent_empty(self):
        self.assertEqual(journal.recent(self.V, 5), [])


class Render(Base):
    def test_render_equals_fixture(self):
        src = FIXTURES / "journal" / "2026-10.jsonl"
        dst = journal.jsonl_path(self.V, "2026-10")
        dst.parent.mkdir(parents=True)
        shutil.copyfile(src, dst)
        journal.render(self.V, "2026-10")
        self.assertEqual(journal.md_path(self.V, "2026-10").read_bytes(),
                         (FIXTURES / "journal" / "2026-10.md").read_bytes())


CONCURRENT = r"""
import sys
sys.path.insert(0, sys.argv[1])
from kb._lib import journal
vault, tag = sys.argv[2], sys.argv[3]
for i in range(6):
    journal.begin_turn(vault, None, {"turn": f"20261015-1200{i:02d}-{tag}", "session_id": tag,
                              "started": f"2026-10-15T12:00:{i:02d}", "prompt_head": tag + str(i)})
"""


class Concurrent(Base):
    def test_two_processes_open_different_turns_all_lines_land(self):
        procs = [subprocess.Popen([sys.executable, "-c", CONCURRENT, str(SCRIPTS), str(self.V), tag])
                 for tag in ("aaaa", "bbbb")]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(len(self.rows("2026-10")), 12)


if __name__ == "__main__":
    unittest.main()
