"""_lib/entities 的事類狀態（日程第 3 點，D-126、D-127）：schema 1→2 遷移、set_progress（結案連帶結束期間、非事類拒絕、
同狀態只加歷程、擱置畫進甘特圖）、asked_evidence、suggest_close 的記與不重記、rename／merge 連日程、名稱擋四種 shell 字元。"""
import json
import shutil
import unittest

from tests import _cards
from tests import _entities as E
from kb._lib import agenda, entities, pending, turn  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]
        self.f1 = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.f2 = _cards.entry_of(self.V, E.ESTIMATE)["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def by_name(self, name):
        return next(e for e in entities.load(self.V)["entities"] if e["name"] == name)

    def close_items(self):
        return [i for i in pending.load(self.V)["items"] if i["kind"] == "close"]

    def period(self):
        agenda.add(self.V, "期間", "2026-11-30", "施工", ["信義王"], self.t, start="2026-09-01")


class Schema2(Base):
    def test_schema1_is_migrated_in_memory_for_matter_types_only(self):
        raw = json.loads((self.V / ".system" / "entities.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(entities.load(self.V)["schema_version"], 2)
        xinyi = self.by_name("信義王")
        self.assertEqual((xinyi["progress"], xinyi["progress_source"], xinyi["progress_history"]),
                         ("ongoing", "inferred", []))
        self.assertNotIn("progress", self.by_name("林小姐室內設計"))
        entities.add(self.V, "業主", "王先生", self.t)
        self.assertNotIn("progress", self.by_name("王先生"))

    def test_any_write_leaves_schema_2_with_the_fields(self):
        entities.confirm(self.V, "信義王", self.t)
        raw = json.loads((self.V / ".system" / "entities.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(next(e for e in raw["entities"] if e["name"] == "信義王")["progress"], "ongoing")

    def test_add_matter_entity_has_progress(self):
        e, _ = entities.add(self.V, "案子", "新案", self.t)
        self.assertEqual((e["progress"], e["progress_source"], e["progress_history"]), ("ongoing", "inferred", []))

    def test_existing_progress_is_kept(self):
        data = entities.load(self.V)
        data["entities"][0].update(progress="closed", progress_source="confirmed")
        E.put_entities(self.V, data["entities"])
        self.assertEqual(self.by_name("信義王")["progress"], "closed")


class NameChars(Base):
    def test_four_shell_characters_are_refused(self):
        for ch in ('"', "$", "`", "\\"):
            with self.subTest(ch=ch):
                self.assertEqual(entities.name_problem(self.V, entities.load(self.V), f"王{ch}宅"),
                                 f"名稱「王{ch}宅」不能用（含不能用的字元）")


class Progress(Base):
    def test_closed_records_history_and_ends_periods(self):
        self.period()
        got = entities.set_progress(self.V, "信義王", "closed", self.t, evidence=[self.f1], note="驗收完")
        self.assertEqual((got["entity"]["progress"], got["entity"]["progress_source"], got["was"]),
                         ("closed", "confirmed", "ongoing"))
        self.assertEqual(len(got["closed_periods"]), 1)
        hist = self.by_name("信義王")["progress_history"]
        self.assertEqual([{k: h[k] for k in ("progress", "source", "evidence", "note")} for h in hist],
                         [{"progress": "closed", "source": "confirmed", "evidence": [self.f1], "note": "驗收完"}])
        (period,) = agenda.load(self.V)["items"]
        self.assertEqual((period["status"], period["ended"]), ("done", hist[0]["date"]))
        self.assertIn(entities.ENTITIES_POSIX, {r["path"] for r in turn.entries(self.V, self.t)})

    def test_non_matter_is_refused(self):
        entities.add(self.V, "業主", "王先生", self.t)
        for name, want in (("王先生", "「王先生」不是「事」類，沒有狀態"),
                           ("林小姐室內設計", "「林小姐室內設計」不是「事」類，沒有狀態")):
            with self.subTest(name=name), self.assertRaises(entities.EntityRefused) as cm:
                entities.set_progress(self.V, name, "closed", self.t)
            self.assertEqual(str(cm.exception), want)
        with self.assertRaises(entities.EntityRefused):
            entities.set_progress(self.V, "沒有這個", "closed", self.t)

    def test_same_progress_again_only_adds_history(self):
        entities.set_progress(self.V, "信義王", "ongoing", self.t, evidence=[self.f1], note="客戶說還沒結案")
        after = self.by_name("信義王")
        self.assertEqual((after["progress"], len(after["progress_history"])), ("ongoing", 1))
        self.assertIn(self.f1, entities.asked_evidence(self.V, after))

    def test_closed_back_to_ongoing_does_not_reopen_periods(self):
        self.period()
        entities.set_progress(self.V, "信義王", "closed", self.t)
        got = entities.set_progress(self.V, "信義王", "ongoing", self.t)
        self.assertTrue(got["reopened"])
        self.assertEqual(agenda.load(self.V)["items"][0]["status"], "done")

    def test_paused_adds_a_pause_bar_to_the_chart(self):
        self.period()
        entities.set_progress(self.V, "信義王", "paused", self.t)
        self.assertIn("  擱置 :done, ", (self.V / "系統" / "日程.md").read_text(encoding="utf-8"))

    def test_bad_progress_value(self):
        with self.assertRaises(ValueError):
            entities.set_progress(self.V, "信義王", "weird", self.t)


class SuggestClose(Base):
    def test_records_one_close_item(self):
        got = entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        self.assertTrue(got["recorded"])
        (item,) = self.close_items()
        self.assertEqual((item["status"], item["file_ids"], item["entity_ids"], item["text"], item["reason"]),
                         ("open", [self.f1], [E.XINYI], "「信義王」看起來已經結束了，要記成結案嗎？", f"依據〔{self.f1}〕"))
        self.assertIn("要確認結案的", (self.V / "系統" / "待處理.md").read_text(encoding="utf-8"))

    def test_same_evidence_is_not_asked_twice(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        before = (self.V / ".system" / "pending.json").read_bytes()
        got = entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        self.assertEqual((got["recorded"], got["skipped"]), (False, "asked"))
        self.assertEqual((self.V / ".system" / "pending.json").read_bytes(), before)

    def test_answered_evidence_is_not_asked_again_but_new_evidence_is(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        entities.set_progress(self.V, "信義王", "ongoing", self.t, evidence=[self.f1], note="客戶說還沒結案")
        self.assertEqual(self.close_items()[0]["status"], "done")
        self.assertEqual(entities.suggest_close(self.V, "信義王", [self.f1], self.t)["skipped"], "asked")
        self.assertTrue(entities.suggest_close(self.V, "信義王", [self.f1, self.f2], self.t)["recorded"])
        self.assertEqual(self.close_items()[-1]["file_ids"], [self.f2])

    def test_asked_evidence_is_history_union_pending(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        entities.set_progress(self.V, "大安陳", "ongoing", self.t, evidence=[self.f2])
        self.assertEqual(entities.asked_evidence(self.V, self.by_name("信義王")), [self.f1])
        self.assertEqual(entities.asked_evidence(self.V, self.by_name("大安陳")), [self.f2])

    def test_two_entities_same_evidence_get_two_items(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        self.assertTrue(entities.suggest_close(self.V, "大安陳", [self.f1], self.t)["recorded"])
        self.assertEqual(len(self.close_items()), 2)

    def test_close_progress_closes_the_open_item(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        entities.set_progress(self.V, "信義王", "closed", self.t, evidence=[self.f1])
        (item,) = self.close_items()
        self.assertEqual(item["status"], "done")

    def test_not_matter_and_not_ongoing(self):
        entities.add(self.V, "業主", "王先生", self.t)
        with self.assertRaises(entities.EntityRefused) as cm:
            entities.suggest_close(self.V, "王先生", [self.f1], self.t)
        self.assertEqual(str(cm.exception), "「王先生」不是「事」類，沒有狀態")
        entities.set_progress(self.V, "信義王", "paused", self.t)
        got = entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        self.assertEqual((got["recorded"], got["skipped"]), (False, "not_ongoing"))
        self.assertEqual(self.close_items(), [])

    def test_unknown_evidence_is_refused(self):
        with self.assertRaises(entities.EntityRefused) as cm:
            entities.suggest_close(self.V, "信義王", ["f-00000000"], self.t)
        self.assertEqual(str(cm.exception), "清冊沒有 f-00000000")


class RenameCarriesAgenda(Base):
    def test_rename_and_merge_rewrite_agenda_entities(self):
        agenda.add(self.V, "到期", "2026-11-30", "交屋", ["信義王"], self.t)
        agenda.add(self.V, "提醒", "2026-11-30", "x", ["王宅", "信義王"], self.t)
        entities.rename(self.V, "信義王", "王宅邸", None, self.t)
        self.assertEqual([i["entities"] for i in agenda.load(self.V)["items"]], [["王宅邸"], ["王宅", "王宅邸"]])
        entities.merge(self.V, "王宅", "王宅邸", self.t)
        self.assertEqual([i["entities"] for i in agenda.load(self.V)["items"]], [["王宅邸"], ["王宅邸"]])

    def test_remove_leaves_agenda_alone(self):
        agenda.add(self.V, "到期", "2026-11-30", "交屋", ["王宅"], self.t)
        entities.remove(self.V, "王宅", self.t)
        self.assertEqual(agenda.load(self.V)["items"][0]["entities"], ["王宅"])

    def test_rename_rolls_agenda_back_when_a_later_step_fails(self):
        agenda.add(self.V, "到期", "2026-11-30", "交屋", ["信義王"], self.t)
        before = (self.V / ".system" / "agenda.json").read_bytes()
        real = entities._save
        entities._save = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            with self.assertRaises(OSError):
                entities.rename(self.V, "信義王", "王宅邸", None, self.t)
        finally:
            entities._save = real
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before)


class CloseItemsFollowRemoveAndMerge(Base):
    """確認後裁定 7(a)：remove、merge 時那個實體 open 的 close 待處理一起 dismiss（同一把鎖、同一套 _Undo），不然永遠留在待處理.md。"""

    def statuses(self):
        return [i["status"] for i in self.close_items()]

    def test_remove_dismisses_its_open_close_items_only(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        entities.suggest_close(self.V, "大安陳", [self.f2], self.t)
        entities.remove(self.V, "信義王", self.t)
        by_entity = {i["entity_ids"][0]: i["status"] for i in self.close_items()}
        self.assertEqual(by_entity, {E.XINYI: "dismissed", E.DAAN: "open"})
        md = (self.V / "系統" / "待處理.md").read_text(encoding="utf-8")
        self.assertIn("大安陳", md)
        self.assertNotIn("信義王", md)

    def test_merge_dismisses_the_source_items(self):
        entities.add(self.V, "案子", "王宅邸", self.t)
        entities.suggest_close(self.V, "王宅邸", [self.f1], self.t)
        entities.merge(self.V, "王宅邸", "信義王", self.t)
        self.assertEqual(self.statuses(), ["dismissed"])
        self.assertNotIn("要確認結案的", (self.V / "系統" / "待處理.md").read_text(encoding="utf-8"))

    def test_asks_no_longer_has_the_close_question(self):
        from kb._lib import plan
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        self.assertEqual([a["kind"] for a in plan._agenda_asks(self.V)], ["close"])
        entities.remove(self.V, "信義王", self.t)
        self.assertEqual(plan._agenda_asks(self.V), [])

    def test_no_pending_file_and_other_kinds_are_left_alone(self):
        entities.remove(self.V, "信義王", self.t)
        self.assertFalse((self.V / ".system" / "pending.json").exists())

    def test_backed_up_into_the_turn(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        t2 = turn.start(self.V, None, "")["turn"]
        entities.remove(self.V, "信義王", t2)
        self.assertTrue({pending.PENDING_POSIX, pending.MD_POSIX} <= {r["path"] for r in turn.entries(self.V, t2)})

    def test_pending_is_rolled_back_when_a_later_step_fails(self):
        entities.suggest_close(self.V, "信義王", [self.f1], self.t)
        before = {p: (self.V / p).read_bytes() for p in (pending.PENDING_POSIX, pending.MD_POSIX)}
        real = entities._save
        entities._save = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            with self.assertRaises(OSError):
                entities.remove(self.V, "信義王", self.t)
        finally:
            entities._save = real
        self.assertEqual({p: (self.V / p).read_bytes() for p in before}, before)
        self.assertEqual(self.statuses(), ["open"])


if __name__ == "__main__":
    unittest.main()
