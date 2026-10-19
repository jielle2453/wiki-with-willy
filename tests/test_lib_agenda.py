"""_lib/agenda（日程第 1 點，D-125～D-130）：日期解析、add 的驗證與冪等、edit 的來源與歷程、set_status、recheck 的提議、
cancel_from_card、rename_entity、close_periods、buckets、counts 的種類分法、render 等於 fixture。"""
import copy
import json
import shutil
import unittest
from pathlib import Path

from tests import _cards
from tests import _entities as E
from tests._guard_fixture import FIXTURES
from kb._lib import agenda, cards, entities, inventory, turn  # noqa: E402

FIX = FIXTURES / "agenda"
TODAY = "2026-10-21"


def item(**kw):
    base = {"id": "a-00000001", "kind": "deadline", "start": None, "date": "2026-10-25", "ended": None, "time": None,
            "text": "付款", "entities": [], "status": "open", "source": "confirmed", "origin": None, "lead_days": None,
            "created_turn": "20261001-100000-ab12", "history": []}
    base.update(kw)
    return base


def put(V, items):
    p = Path(V) / ".system" / "agenda.json"
    p.write_text(json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(V):
    return json.loads((Path(V) / ".system" / "agenda.json").read_text(encoding="utf-8"))["items"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]
        self._today = agenda._today
        agenda._today = lambda: TODAY

    def tearDown(self):
        agenda._today = self._today
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, rel=E.QUOTE, title="信義王報價", names=("信義王",)):
        """寫一張卡，回 (編號, 卡的 fp 前 12 碼)。"""
        path = self.card_rel = E.write_card(self.V, rel, title, list(names))
        front = cards.parse_front((self.V / path).read_text(encoding="utf-8"))
        return front["編號"], (front.get("內容指紋") or front["指紋"])[:12]

    def add(self, **kw):
        args = dict(kind="deadline", date="2026-11-30", text="交屋", entities=["信義王"])
        args.update(kw)
        return agenda.add(self.V, args.pop("kind"), args.pop("date"), args.pop("text"), args.pop("entities"), self.t, **args)

    def refused(self, want, **kw):
        with self.assertRaises(agenda.AgendaRefused) as cm:
            self.add(**kw)
        self.assertEqual(str(cm.exception), want)


class Dates(unittest.TestCase):
    def test_parse_date_really_parses(self):
        self.assertEqual(agenda.parse_date("2026-11-30").isoformat(), "2026-11-30")
        for bad in ("2026-13-99", "2026-02-30", "2026/11/30", "2026-1-5", "", "明天", None, "2026-11-30 "):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                agenda.parse_date(bad)
            self.assertEqual(str(cm.exception), "日期要寫 YYYY-MM-DD，而且是真的日期")

    def test_parse_time(self):
        self.assertEqual(agenda.parse_time("00:00"), "00:00")
        self.assertEqual(agenda.parse_time("23:59"), "23:59")
        for bad in ("25:00", "9:00", "12:60", "ab", "", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                agenda.parse_time(bad)
            self.assertEqual(str(cm.exception), "時間要寫 HH:MM")

    def test_constants(self):
        self.assertEqual(agenda.KINDS, {"到期": "deadline", "會議": "meeting", "承諾": "promise", "提醒": "reminder",
                                       "期間": "period"})
        self.assertEqual((agenda.TRACKED, agenda.PASSING, agenda.STATUSES),
                         (("deadline", "promise"), ("meeting", "reminder"), ("open", "done", "cancelled")))


class Load(Base):
    def test_missing_is_empty(self):
        self.assertEqual(agenda.load(self.V), {"schema_version": 1, "items": []})

    def test_bad_shapes_raise_with_filename(self):
        p = self.V / ".system" / "agenda.json"
        for text in ("[]", '{"items": 1}', '{"items": [1]}', '{"schema_version": 2, "items": []}', "{bad"):
            p.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError) as cm:
                agenda.load(self.V)
            self.assertIn(".system/agenda.json", str(cm.exception))


class Add(Base):
    def test_confirmed_add(self):
        got, created = self.add()
        self.assertTrue(created)
        self.assertRegex(got["id"], r"^a-[0-9a-f]{8}$")
        self.assertEqual({k: got[k] for k in ("kind", "start", "date", "ended", "time", "text", "entities", "status", "source",
                                              "origin", "lead_days", "created_turn", "history")},
                         {"kind": "deadline", "start": None, "date": "2026-11-30", "ended": None, "time": None, "text": "交屋",
                          "entities": ["信義王"], "status": "open", "source": "confirmed", "origin": None,
                          "lead_days": None, "created_turn": self.t, "history": []})
        self.assertEqual(read(self.V), [got])
        data = json.loads((self.V / ".system" / "agenda.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual({r["path"] for r in turn.entries(self.V, self.t)}, {agenda.AGENDA_POSIX, agenda.MD_POSIX})
        self.assertIn("交屋", (self.V / "系統" / "日程.md").read_text(encoding="utf-8"))

    def test_kind_accepts_chinese_and_english(self):
        self.assertEqual(self.add(kind="到期")[0]["kind"], "deadline")
        self.assertEqual(self.add(kind="meeting", text="開會")[0]["kind"], "meeting")

    def test_alias_is_stored_as_registered_name(self):
        self.assertEqual(self.add(entities=["信義王宅"])[0]["entities"], ["信義王"])

    def test_time_start_lead_days(self):
        got, _ = self.add(kind="period", start="2026-09-01", entities=["信義王"], time="09:30", lead_days=14)
        self.assertEqual((got["start"], got["time"], got["lead_days"]), ("2026-09-01", "09:30", 14))

    def test_refusals(self):
        self.refused("日期要寫 YYYY-MM-DD，而且是真的日期", date="2026-02-30")
        self.refused("日期要寫 YYYY-MM-DD，而且是真的日期", date="2026/11/30")
        self.refused("日期要寫 YYYY-MM-DD，而且是真的日期", start="明天")
        self.refused("時間要寫 HH:MM", time="25:00")
        self.refused("期間要有 --start，而且只掛一個實體", kind="期間")
        self.refused("期間要有 --start，而且只掛一個實體", kind="期間", start="2026-09-01", entities=[])
        self.refused("期間要有 --start，而且只掛一個實體", kind="期間", start="2026-09-01", entities=["信義王", "王宅"])
        self.refused("開始不能晚於日期", start="2026-12-01")
        self.refused("實體清單沒有「王小明」，先 kb entity add", entities=["王小明"])
        self.refused("種類要是 到期、會議、承諾、提醒、期間 之一", kind="生日")
        self.refused("日程要有一句說明", text="  ")
        self.assertFalse((self.V / ".system" / "agenda.json").exists())

    def test_merged_name_resolves_to_target(self):
        data = entities.load(self.V)
        wang = next(x for x in data["entities"] if x["name"] == "王宅")
        wang.update(status="merged", merged_into=E.XINYI)
        E.put_entities(self.V, data["entities"])
        self.assertEqual(self.add(entities=["王宅"])[0]["entities"], ["信義王"])

    def test_with_card_is_inferred_with_origin(self):
        cid, fp = self.card()
        got, created = self.add(card=cid)
        self.assertTrue(created)
        self.assertEqual((got["source"], got["origin"]), ("inferred", {"card": cid, "fp": fp}))
        self.assertEqual(len(fp), 12)

    def test_with_card_is_idempotent(self):
        cid, _fp = self.card()
        first, _ = self.add(card=cid)
        before = (self.V / ".system" / "agenda.json").read_bytes()
        again, created = self.add(card=cid)
        self.assertFalse(created)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before)
        self.assertTrue(self.add(card=cid, text="交屋驗收")[1])
        self.assertTrue(self.add(card=cid, date="2026-12-01")[1])
        self.assertTrue(self.add(card=cid, kind="會議")[1])

    def test_card_refusals(self):
        self.refused("清冊沒有 f-00000000", card="f-00000000")
        cid, _fp = self.card()
        inventory.set_status(self.V, [cid], "missing")
        self.refused("這張卡的原檔已不在", card=cid)

    def test_card_without_card_file_is_refused(self):
        cid = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.refused("這份還沒有檔案卡，先寫卡再加日程", card=cid)

    def test_without_card_dedupes_nothing(self):
        self.assertTrue(self.add()[1])
        self.assertTrue(self.add()[1])


class Edit(Base):
    def test_inferred_becomes_corrected_with_history(self):
        cid, _fp = self.card()
        got, _ = self.add(card=cid)
        out = agenda.edit(self.V, got["id"], self.t, date="2026-12-15")
        self.assertEqual((out["date"], out["source"]), ("2026-12-15", "corrected"))
        self.assertEqual(out["history"], [{"date": TODAY, "change": "date 2026-11-30 → 2026-12-15", "by": "ai", "turn": self.t}])
        self.assertEqual(read(self.V)[0]["date"], "2026-12-15")

    def test_confirmed_stays_confirmed_and_program_by(self):
        got, _ = self.add()
        out = agenda.edit(self.V, got["id"], self.t, date="2026-12-15", by="program")
        self.assertEqual((out["source"], out["history"][0]["by"]), ("confirmed", "program"))

    def test_recheck_edit_with_card_stays_inferred_and_updates_fp(self):
        cid, fp = self.card()
        got, _ = self.add(card=cid)
        d = read(self.V)
        d[0]["origin"]["fp"] = "000000000000"
        put(self.V, d)
        out = agenda.edit(self.V, got["id"], self.t, date="2026-12-15", card=cid)
        self.assertEqual((out["source"], out["origin"]), ("inferred", {"card": cid, "fp": fp}))
        self.assertEqual(len(out["history"]), 1)

    def test_recheck_edit_with_other_card_is_refused(self):
        cid, _fp = self.card()
        got, _ = self.add(card=cid)
        other, _ = self.card(E.ESTIMATE, "內湖李估價", ("信義王",))
        with self.assertRaises(agenda.AgendaRefused) as cm:
            agenda.edit(self.V, got["id"], self.t, date="2026-12-15", card=other)
        self.assertEqual(str(cm.exception), "這條不是從這張卡抽的")

    def test_fields_text_time_entities_lead_start(self):
        got, _ = self.add(kind="meeting", text="開會")
        out = agenda.edit(self.V, got["id"], self.t, text="開大會", time="14:00", entities=["王宅"], lead_days=3,
                          start="2026-11-01")
        self.assertEqual((out["text"], out["time"], out["entities"], out["lead_days"], out["start"]),
                         ("開大會", "14:00", ["王宅"], 3, "2026-11-01"))
        self.assertEqual(len(out["history"]), 1)
        for part in ("text 開會 → 開大會", "time  → 14:00", "entities 信義王 → 王宅", "lead_days  → 3"):
            self.assertIn(part, out["history"][0]["change"])

    def test_edit_validates_like_add(self):
        got, _ = self.add()
        for kw, want in ((dict(date="2026-02-30"), "日期要寫 YYYY-MM-DD，而且是真的日期"),
                         (dict(time="25:00"), "時間要寫 HH:MM"),
                         (dict(start="2027-01-01"), "開始不能晚於日期"),
                         (dict(entities=["王小明"]), "實體清單沒有「王小明」，先 kb entity add")):
            with self.subTest(kw=kw), self.assertRaises(agenda.AgendaRefused) as cm:
                agenda.edit(self.V, got["id"], self.t, **kw)
            self.assertEqual(str(cm.exception), want)
        with self.assertRaises(agenda.AgendaRefused) as cm:
            agenda.edit(self.V, "a-ffffffff", self.t, date="2026-12-01")
        self.assertEqual(str(cm.exception), "日程沒有 a-ffffffff")

    def test_no_change_writes_nothing(self):
        got, _ = self.add()
        before = (self.V / ".system" / "agenda.json").read_bytes()
        out = agenda.edit(self.V, got["id"], self.t, date="2026-11-30")
        self.assertEqual(out["history"], [])
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before)

    def test_edit_clears_proposal(self):
        got, _ = self.add()
        d = read(self.V)
        d[0]["proposal"] = {"date": "2026-12-20", "text": "交屋", "card": "f-x", "fp": "a"}
        put(self.V, d)
        self.assertNotIn("proposal", agenda.edit(self.V, got["id"], self.t, date="2026-12-20"))


class Status(Base):
    def test_done_cancel_reopen_with_history(self):
        got, _ = self.add()
        out = agenda.set_status(self.V, got["id"], "done", self.t)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["history"], [{"date": TODAY, "change": "status open → done", "by": "ai", "turn": self.t}])
        self.assertEqual(agenda.set_status(self.V, got["id"], "cancelled", self.t)["status"], "cancelled")
        out = agenda.set_status(self.V, got["id"], "open", self.t)
        self.assertEqual((out["status"], len(out["history"])), ("open", 3))
        self.assertEqual(out["history"][-1]["change"], "status cancelled → open")

    def test_unknown_id_and_bad_status(self):
        with self.assertRaises(agenda.AgendaRefused):
            agenda.set_status(self.V, "a-ffffffff", "done", self.t)
        got, _ = self.add()
        with self.assertRaises(ValueError):
            agenda.set_status(self.V, got["id"], "weird", self.t)


class Recheck(Base):
    def setUp(self):
        super().setUp()
        self.cid, self.fp = self.card()

    def stale(self, **kw):
        got, _ = self.add(**kw)
        d = read(self.V)
        d[0]["origin"]["fp"] = "000000000000"
        put(self.V, d)
        return got["id"]

    def test_recheck_without_proposal_updates_fp(self):
        aid = self.stale(card=self.cid)
        out = agenda.recheck(self.V, aid, self.t)
        self.assertEqual(out["origin"]["fp"], self.fp)
        self.assertNotIn("proposal", out)

    def test_inferred_proposal_is_refused(self):
        aid = self.stale(card=self.cid)
        with self.assertRaises(agenda.AgendaRefused) as cm:
            agenda.recheck(self.V, aid, self.t, propose_date="2026-12-20")
        self.assertEqual(str(cm.exception), "推測的條目直接 edit，不用提議")

    def test_confirmed_or_corrected_proposal_is_recorded(self):
        for source in ("confirmed", "corrected"):
            with self.subTest(source=source):
                aid = self.stale(card=self.cid)
                d = read(self.V)
                d[-1]["source"] = source
                put(self.V, d)
                out = agenda.recheck(self.V, aid, self.t, propose_date="2026-12-20", propose_text="交屋驗收")
                self.assertEqual(out["proposal"], {"date": "2026-12-20", "text": "交屋驗收", "card": self.cid, "fp": self.fp})
                self.assertEqual(out["origin"]["fp"], self.fp)
                cleared = agenda.recheck(self.V, aid, self.t)
                self.assertNotIn("proposal", cleared)

    def test_proposal_defaults_missing_half_to_current(self):
        aid = self.stale(card=self.cid)
        d = read(self.V)
        d[-1]["source"] = "confirmed"
        put(self.V, d)
        out = agenda.recheck(self.V, aid, self.t, propose_date="2026-12-20")
        self.assertEqual(out["proposal"]["text"], "交屋")

    def test_proposal_bad_date_and_no_origin(self):
        aid = self.stale(card=self.cid)
        d = read(self.V)
        d[-1]["source"] = "confirmed"
        put(self.V, d)
        with self.assertRaises(agenda.AgendaRefused) as cm:
            agenda.recheck(self.V, aid, self.t, propose_date="2026-02-30")
        self.assertEqual(str(cm.exception), "日期要寫 YYYY-MM-DD，而且是真的日期")
        plain, _ = self.add(text="別的")
        with self.assertRaises(agenda.AgendaRefused) as cm:
            agenda.recheck(self.V, plain["id"], self.t)
        self.assertEqual(str(cm.exception), "這條不是從檔案卡抽的，沒有可以對的版本")


class Buckets(Base):
    def test_lists_open_items_whose_card_changed(self):
        cid, fp = self.card()
        got, _ = self.add(card=cid)
        self.assertEqual(agenda.buckets(self.V, agenda.load(self.V))["agenda_recheck"], [])
        d = read(self.V)
        d[0]["origin"]["fp"] = "000000000000"
        put(self.V, d)
        (row,) = agenda.buckets(self.V, agenda.load(self.V))["agenda_recheck"]
        self.assertEqual(row, {"id": got["id"], "card": cid, "card_path": self.card_rel, "kind": "到期",
                               "date": "2026-11-30", "text": "交屋", "source": "inferred"})
        self.assertTrue(row["card_path"].startswith("檔案索引/"))
        for status in ("done", "cancelled"):
            d[0]["status"] = status
            put(self.V, d)
            self.assertEqual(agenda.buckets(self.V, agenda.load(self.V))["agenda_recheck"], [], status)

    def test_confirmed_with_origin_is_listed_and_no_origin_is_not(self):
        cid, _fp = self.card()
        self.add(card=cid)
        self.add(text="沒出處")
        d = read(self.V)
        d[0]["origin"]["fp"] = "000000000000"
        d[0]["source"] = "confirmed"
        put(self.V, d)
        rows = agenda.buckets(self.V, agenda.load(self.V))["agenda_recheck"]
        self.assertEqual([(r["text"], r["source"]) for r in rows], [("交屋", "confirmed")])

    def test_missing_card_file_is_not_listed(self):
        d = [item(origin={"card": "f-12345678", "fp": "a"}, source="inferred")]
        put(self.V, d)
        self.assertEqual(agenda.buckets(self.V, agenda.load(self.V))["agenda_recheck"], [])


class CancelFromCard(Base):
    def test_inferred_open_cancelled_confirmed_untouched(self):
        cid, _fp = self.card()
        a, _ = self.add(card=cid)
        b, _ = self.add(card=cid, text="別的")
        c, _ = self.add(text="客戶說的")
        d = read(self.V)
        d[1]["source"] = "confirmed"
        d[0]["status"] = "done"
        put(self.V, d)
        e, _ = self.add(card=cid, text="第三條")
        done = agenda.cancel_from_card(self.V, cid, self.t)
        self.assertEqual([x["id"] for x in done], [e["id"]])
        status = {x["id"]: x["status"] for x in read(self.V)}
        self.assertEqual(status, {a["id"]: "done", b["id"]: "open", c["id"]: "open", e["id"]: "cancelled"})
        self.assertEqual(read(self.V)[3]["history"][0]["by"], "program")

    def test_no_file_no_write(self):
        self.assertEqual(agenda.cancel_from_card(self.V, "f-1", self.t), [])
        self.assertFalse((self.V / ".system" / "agenda.json").exists())


class RenameAndClose(Base):
    def test_rename_entity_replaces_and_dedupes(self):
        self.add(entities=["信義王"])
        self.add(kind="提醒", text="x", entities=["信義王", "王宅"])
        agenda.rename_entity(self.V, {entities.key("信義王"): "王宅"}, self.t)
        self.assertEqual([x["entities"] for x in read(self.V)], [["王宅"], ["王宅"]])

    def test_rename_without_file_writes_nothing(self):
        agenda.rename_entity(self.V, {"a": "b"}, self.t)
        self.assertFalse((self.V / ".system" / "agenda.json").exists())

    def test_close_periods(self):
        self.add(kind="期間", start="2026-09-01", text="施工")
        self.add(kind="到期", text="交屋")
        other, _ = self.add(kind="期間", start="2026-09-01", text="別案", entities=["王宅"])
        closed = agenda.close_periods(self.V, "信義王", TODAY, self.t)
        self.assertEqual(len(closed), 1)
        rows = {x["text"]: x for x in read(self.V)}
        self.assertEqual((rows["施工"]["ended"], rows["施工"]["status"]), (TODAY, "done"))
        self.assertEqual((rows["交屋"]["status"], rows["別案"]["status"]), ("open", "open"))
        self.assertEqual(agenda.close_periods(self.V, "信義王", TODAY, self.t), [])


class Counts(Base):
    def count(self, *items):
        return agenda.counts(self.V, {"schema_version": 1, "items": list(items)}, TODAY)

    def test_tracked_overdue_and_passing_kinds(self):
        got = self.count(item(id="a-1", kind="deadline", date="2026-10-20"),
                         item(id="a-2", kind="promise", date="2026-10-01"),
                         item(id="a-3", kind="meeting", date="2026-10-20"),
                         item(id="a-4", kind="reminder", date="2026-10-20"))
        self.assertEqual(got, {"overdue": 2, "today": 0, "upcoming": 0})

    def test_today_and_upcoming(self):
        got = self.count(item(id="a-1", date=TODAY), item(id="a-2", kind="meeting", date=TODAY),
                         item(id="a-3", date="2026-10-22"), item(id="a-4", kind="reminder", date="2026-10-28"),
                         item(id="a-5", date="2026-10-29"))
        self.assertEqual(got, {"overdue": 0, "today": 2, "upcoming": 2})

    def test_period_counts_nowhere(self):
        got = self.count(item(kind="period", start="2026-09-01", date="2026-10-01"),
                         item(id="a-2", kind="period", start="2026-09-01", date=TODAY),
                         item(id="a-3", kind="period", start="2026-09-01", date="2026-10-23"))
        self.assertEqual(got, {"overdue": 0, "today": 0, "upcoming": 0})

    def test_lead_days_widens_the_window(self):
        late = "2026-10-31"  # 今天後第 10 天
        self.assertEqual(self.count(item(date=late))["upcoming"], 0)
        self.assertEqual(self.count(item(date=late, lead_days=14))["upcoming"], 1)
        self.assertEqual(self.count(item(date="2026-10-24", lead_days=1))["upcoming"], 0)

    def test_done_and_cancelled_never_count(self):
        got = self.count(item(id="a-1", date="2026-10-01", status="done"), item(id="a-2", date=TODAY, status="cancelled"),
                         item(id="a-3", date="2026-10-23", status="done"))
        self.assertEqual(got, {"overdue": 0, "today": 0, "upcoming": 0})

    def test_window_reads_config(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"agenda_window_days": 2}), encoding="utf-8")
        self.assertEqual(self.count(item(date="2026-10-23"), item(id="a-2", date="2026-10-24"))["upcoming"], 1)


class Render(Base):
    def test_fixture_renders_exactly(self):
        shutil.copy2(FIX / "agenda.json", self.V / ".system" / "agenda.json")
        shutil.copy2(FIX / "entities.json", self.V / ".system" / "entities.json")
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertEqual(got, (FIX / "日程.md").read_text(encoding="utf-8"))

    def test_empty_items_is_header_and_one_sentence(self):
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertEqual(got, agenda.HEADER + "\n日程是空的；說「9/25 下午有個會」我就記下來。\n")
        self.assertEqual(len(agenda.HEADER.splitlines()), 4)

    def test_only_past_items_still_has_five_sections_and_no_chart_when_nothing_drawn(self):
        put(self.V, [item(kind="meeting", date="2026-01-01", status="done")])
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        for head in ("## 逾期", "## 今天", "## 接下來 7 天", "## 之後", "## 過去 30 天"):
            self.assertIn(head, got)
        self.assertNotIn("時程圖", got)
        self.assertEqual(got.count("（沒有）"), 5)
        self.assertIn("## 逾期\n（沒有）", got)

    def test_overdue_period_gets_extra_bar_and_pause_bar_when_paused(self):
        put(self.V, [item(kind="period", start="2026-09-01", date="2026-10-10", text="施工", entities=["信義王"])])
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertIn("  施工 :active, 2026-09-01, 2026-10-10\n  逾預計結束 :crit, 2026-10-10, 2026-10-21\n", got)

    def test_done_period_draws_until_ended(self):
        put(self.V, [item(kind="period", start="2026-09-01", date="2026-11-30", ended="2026-10-15", status="done",
                          text="施工", entities=["信義王"])])
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertIn("  施工 :done, 2026-09-01, 2026-10-15\n", got)
        self.assertIn("- 2026-10-15 期間 施工（信義王）完成", got)

    def test_current_pause_is_drawn_to_today_at_least_a_day(self):
        data = json.loads((FIX / "entities.json").read_text(encoding="utf-8"))
        data["entities"][0]["progress"] = "paused"
        data["entities"][0]["progress_history"] = [{"date": TODAY, "progress": "paused", "source": "confirmed",
                                                    "evidence": [], "note": ""}]
        (self.V / ".system" / "entities.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        put(self.V, [item(kind="period", start="2026-09-01", date="2026-11-30", text="施工", entities=["信義王"])])
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertIn(f"  擱置 :done, {TODAY}, 1d\n", got)

    def test_unchanged_content_is_not_rewritten_or_backed_up(self):
        shutil.copy2(FIX / "agenda.json", self.V / ".system" / "agenda.json")
        agenda.render(self.V, today=TODAY)
        t2 = turn.start(self.V, None, "")["turn"]
        stamp = (self.V / "系統" / "日程.md").stat().st_mtime_ns
        agenda.render(self.V, t2, today=TODAY)
        self.assertEqual((self.V / "系統" / "日程.md").stat().st_mtime_ns, stamp)
        self.assertEqual(turn.entries(self.V, t2), [])

    def test_render_with_turn_backs_up_md_when_it_changes(self):
        agenda.render(self.V, today=TODAY)
        t2 = turn.start(self.V, None, "")["turn"]
        put(self.V, [item()])
        agenda.render(self.V, t2, today=TODAY)
        self.assertEqual([r["path"] for r in turn.entries(self.V, t2)], [agenda.MD_POSIX])

    def test_text_newlines_are_flattened_in_chart(self):
        put(self.V, [item(text="一行\n二行", date="2026-10-25")])
        agenda.render(self.V, today=TODAY)
        got = (self.V / "系統" / "日程.md").read_text(encoding="utf-8")
        self.assertIn("  一行 二行 :milestone, 2026-10-25, 0d\n", got)


if __name__ == "__main__":
    unittest.main()
