"""kb entity progress／suggest-close 黑箱（日程第 4 點，驗收 8、9、10b、16）：句子一字不差、結案連帶結束期間、
事類才有狀態、擱置畫進甘特圖、rename／merge 連日程、revert 退回、終端機程式段、list 多一欄。"""
import json
import shutil
import unittest

from tests import _cards
from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, entities, journal, pending, turn  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.f1 = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.f2 = _cards.entry_of(self.V, E.ESTIMATE)["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0, group="entity"):
        r = run_kb(group, *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def get(self, name):
        return next(e for e in entities.load(self.V)["entities"] if e["name"] == name)

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def period(self, entity="信義王"):
        self.kb("add", "--kind", "期間", "--start", "2026-09-01", "--date", "2026-11-30", "--text", "施工",
                "--entity", entity, group="agenda")

    def close_items(self):
        return [i for i in pending.load(self.V)["items"] if i["kind"] == "close"]


class Progress(Base):
    def test_closed_sentence_with_periods(self):
        self.period()
        self.assertEqual(self.kb("progress", "信義王", "結案", "--evidence", self.f1),
                         ["已記成結案：信義王（期間 1 條一起結束）"])
        e = self.get("信義王")
        self.assertEqual((e["progress"], e["progress_source"]), ("closed", "confirmed"))
        self.assertEqual(e["progress_history"][0]["evidence"], [self.f1])
        (p,) = agenda.load(self.V)["items"]
        self.assertEqual((p["status"], p["ended"]), ("done", e["progress_history"][0]["date"]))

    def test_closed_without_periods(self):
        self.assertEqual(self.kb("progress", "信義王", "結案"), ["已記成結案：信義王"])

    def test_still_ongoing_with_note_only_adds_history(self):
        self.assertEqual(self.kb("progress", "信義王", "進行中", "--evidence", self.f1, "--note", "客戶說還沒結案"),
                         ["已記下：信義王還在進行中（客戶說還沒結案）"])
        e = self.get("信義王")
        self.assertEqual((e["progress"], len(e["progress_history"])), ("ongoing", 1))
        self.assertEqual(entities.asked_evidence(self.V, e), [self.f1])

    def test_paused_sentence_and_chart(self):
        self.period()
        self.assertEqual(self.kb("progress", "大安陳", "擱置"), ["已記成擱置：大安陳"])
        self.assertEqual(self.get("大安陳")["progress"], "paused")
        self.kb("progress", "信義王", "擱置")
        self.assertIn("  擱置 :done, ", (self.V / "系統" / "日程.md").read_text(encoding="utf-8"))

    def test_resume_from_paused_and_reopen_note(self):
        self.kb("progress", "大安陳", "擱置")
        self.assertEqual(self.kb("progress", "大安陳", "進行中"), ["已記成進行中：大安陳"])
        self.period()
        self.kb("progress", "信義王", "結案")
        self.assertEqual(self.kb("progress", "信義王", "進行中"),
                         ["已記成進行中：信義王", "期間條目沒有重開，要的話說改期"])

    def test_refusals(self):
        self.kb("add", "--type", "業主", "--name", "王先生")
        self.assertEqual(self.kb("progress", "王先生", "結案", code=3), ["「王先生」不是「事」類，沒有狀態"])
        self.assertEqual(self.kb("progress", "沒有這個", "結案", code=3), ["實體清單沒有「沒有這個」"])
        self.assertEqual(self.kb("progress", "信義王", "做完", code=3), ["狀態要是 進行中、結案、擱置 之一"])

    def test_closes_the_open_close_item(self):
        self.kb("suggest-close", "信義王", "--evidence", self.f1)
        self.kb("progress", "信義王", "進行中", "--evidence", self.f1, "--note", "客戶說還沒結案")
        self.assertEqual([i["status"] for i in self.close_items()], ["done"])

    def test_program_line_and_conversation(self):
        self.kb("progress", "信義王", "結案")
        self.assertEqual((self.programs()[-1]["note"], self.programs()[-1]["command"]), ("信義王記成結案", "entity progress"))
        n = len(self.programs())
        (self.V / ".system" / "turns" / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid", "started": "2026-10-19T10:00:00",
            "prompt_head": "", "approved": False, "note": "", "done": False}), encoding="utf-8")
        self.kb("progress", "大安陳", "擱置")
        self.assertEqual(len(self.programs()), n)

    def test_revert_restores_entities_and_agenda(self):
        self.period()
        before_e = (self.V / ".system" / "entities.json").read_bytes()
        before_a = (self.V / ".system" / "agenda.json").read_bytes()
        before_md = (self.V / "系統" / "日程.md").read_bytes()
        turn.start(self.V, None, "")
        self.kb("progress", "信義王", "結案")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / ".system" / "entities.json").read_bytes(), before_e)
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before_a)
        self.assertEqual((self.V / "系統" / "日程.md").read_bytes(), before_md)


class SuggestClose(Base):
    def test_recorded_then_asked_before(self):
        self.assertEqual(self.kb("suggest-close", "信義王", "--evidence", self.f1), ["已記下，會問客戶「信義王」要不要記成結案"])
        (item,) = self.close_items()
        self.assertEqual((item["kind"], item["entity_ids"], item["file_ids"]), ("close", [E.XINYI], [self.f1]))
        self.assertIn("要確認結案的", (self.V / "系統" / "待處理.md").read_text(encoding="utf-8"))
        self.assertEqual(self.kb("suggest-close", "信義王", "--evidence", self.f1), ["這份依據問過了，不再問"])
        self.assertEqual(len(self.close_items()), 1)

    def test_progress_closes_it(self):
        self.kb("suggest-close", "信義王", "--evidence", self.f1)
        self.kb("progress", "信義王", "結案")
        self.assertEqual(self.close_items()[0]["status"], "done")

    def test_refusals(self):
        self.kb("add", "--type", "業主", "--name", "王先生")
        self.assertEqual(self.kb("suggest-close", "王先生", "--evidence", self.f1, code=3), ["「王先生」不是「事」類，沒有狀態"])
        self.assertEqual(self.kb("suggest-close", "信義王", "--evidence", "f-00000000", code=3), ["清冊沒有 f-00000000"])

    def test_not_ongoing_says_so_exit_0(self):
        self.kb("progress", "信義王", "擱置")
        self.assertEqual(self.kb("suggest-close", "信義王", "--evidence", self.f1), ["「信義王」目前是擱置，不用問結案"])

    def test_program_line(self):
        self.kb("suggest-close", "信義王", "--evidence", self.f1)
        self.assertEqual(self.programs()[-1]["note"], "記下結案待問：信義王")


class ListAndRename(Base):
    def test_list_shows_progress_for_matter_types_only(self):
        self.kb("progress", "大安陳", "擱置")
        lines = self.kb("list")
        self.assertIn("- 信義王（案子，確認，進行中）掛 0 張，有綜合卡", lines)
        self.assertIn("- 大安陳（案子，確認，擱置）掛 0 張，有綜合卡", lines)
        self.assertIn("- 王宅（案子，待確認，進行中）掛 0 張", lines)
        self.assertIn("- 林小姐室內設計（你自己）掛 0 張", lines)

    def test_rename_and_merge_carry_the_agenda(self):
        self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "王宅", group="agenda")
        self.kb("rename", "王宅", "--to", "王宅邸")
        self.assertEqual(agenda.load(self.V)["items"][0]["entities"], ["王宅邸"])
        self.kb("merge", "王宅邸", "--into", "大安陳")
        self.assertEqual(agenda.load(self.V)["items"][0]["entities"], ["大安陳"])

    def test_revert_rename_restores_agenda(self):
        self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "王宅", group="agenda")
        before = (self.V / ".system" / "agenda.json").read_bytes()
        before_md = (self.V / "系統" / "日程.md").read_bytes()
        turn.start(self.V, None, "")
        self.kb("rename", "王宅", "--to", "王宅邸")
        self.assertNotEqual((self.V / ".system" / "agenda.json").read_bytes(), before)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before)
        self.assertEqual((self.V / "系統" / "日程.md").read_bytes(), before_md)


if __name__ == "__main__":
    unittest.main()
