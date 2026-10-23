"""日程跟 kb cards、kb asks 的整條路（日程驗收 10、10a、10c、11）：原檔改版 → 重做卡 → agenda_recheck 列出與處理的四種、
asks 三種新題的順序與每個 do 實跑、kb asks 跟 cards todo --json 一樣、tell 兩句、cards drop 連帶取消。"""
import json
import shlex
import shutil
import unittest

from tests import _cards, _ooxml
from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, cards, entities, pending, turn  # noqa: E402

PAST = "2020-01-01"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.title = "大安陳會議一"
        (self.V / ".system" / "config.json").write_text(json.dumps({"ingest_auto_max": 100}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb(*args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def todo(self):
        return json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)

    def card(self, bucket="new"):
        """照 todo 的清單寫會議紀錄的卡（掛大安陳），回編號。"""
        fid = _cards.entry_of(self.V, _cards.MEETING)["id"]
        item = next(i for i in self.todo()[bucket] if i["id"] == fid)
        p = _cards.card_path(self.V, item, self.title)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(E.with_entities(_cards.card_text(item, self.title), ["大安陳"]), encoding="utf-8", newline="\n")
        return fid

    def change_original(self, word):
        _ooxml.docx(self.raw / _cards.MEETING, [("h", f"大安陳{word}會議"), ("p", f"{word}的內容")])
        self.kb("inventory", "scan")

    def add_json(self, *args):
        r = run_kb("agenda", "add", *args, "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["item"]["id"]

    def add(self, cid, text="交屋", date="2999-11-30", kind="到期"):
        return self.add_json("--kind", kind, "--date", date, "--text", text, "--entity", "大安陳", "--card", cid)

    def item(self, aid):
        return next(i for i in agenda.load(self.V)["items"] if i["id"] == aid)

    def set_source(self, aid, source):
        d = agenda.load(self.V)
        next(i for i in d["items"] if i["id"] == aid)["source"] = source
        (self.V / ".system" / "agenda.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def redo(self, word):
        """原檔改版、掃描、重寫卡（指紋變了）。"""
        self.change_original(word)
        self.card("stale")


class Recheck(Base):
    def test_listed_only_after_the_card_is_redone_then_gone_after_recheck(self):
        cid = self.card()
        aid = self.add(cid)
        self.assertEqual(self.todo()["agenda_recheck"], [])
        self.change_original("第二次")
        self.assertEqual(self.todo()["agenda_recheck"], [])
        self.card("stale")
        (row,) = self.todo()["agenda_recheck"]
        self.assertEqual((row["id"], row["card"], row["kind"], row["date"], row["text"], row["source"]),
                         (aid, cid, "到期", "2999-11-30", "交屋", "inferred"))
        self.assertTrue(row["card_path"].startswith("檔案索引/") and row["card_path"].endswith(".md"))
        first = self.kb("cards", "todo")[0]
        self.assertIn("；日程待重對 1", first)
        steps = [s["step"] for s in self.todo()["plan"]]
        self.assertEqual(steps, ["dispatch"], "card-maker：日程也收進 dispatch（D-198）")
        self.kb("agenda", "recheck", aid)
        self.assertEqual(self.todo()["agenda_recheck"], [])
        self.assertNotIn("日程待重對", self.kb("cards", "todo")[0])

    def test_inferred_edit_with_card_reworks_it_and_tells(self):
        cid = self.card()
        aid = self.add(cid)
        self.redo("第二次")
        turn.start(self.V, None, "")
        self.kb("agenda", "edit", aid, "--date", "2999-12-15", "--card", cid)
        self.assertEqual((self.item(aid)["source"], self.todo()["agenda_recheck"]), ("inferred", []))
        self.assertIn("交屋從 11/30 改成 12/15 了（大安陳會議一）", self.todo()["tell"])
        turn.start(self.V, None, "")
        self.assertEqual([t for t in self.todo()["tell"] if "改成" in t], [])

    def test_inferred_cancel_drops_it_from_the_list(self):
        cid = self.card()
        aid = self.add(cid)
        self.redo("第二次")
        self.kb("agenda", "cancel", aid)
        self.assertEqual(self.todo()["agenda_recheck"], [])

    def test_confirmed_item_is_listed_and_proposal_becomes_a_question(self):
        cid = self.card()
        aid = self.add(cid)
        self.set_source(aid, "confirmed")
        self.redo("第二次")
        self.assertEqual([r["source"] for r in self.todo()["agenda_recheck"]], ["confirmed"])
        self.kb("agenda", "recheck", aid, "--propose-date", "2999-12-20")
        data = self.todo()
        self.assertEqual(data["agenda_recheck"], [])
        (ask,) = [a for a in data["asks"] if a["kind"] == "agenda_conflict"]
        self.assertEqual(ask["question"], "交屋你之前說 11/30，新版大安陳會議一寫 12/20，要用哪個？")
        self.assertEqual([r["do"] for r in ask["results"]],
                         [[f"kb agenda edit {aid} --date 2999-12-20"], [f"kb agenda recheck {aid}"]])

    def test_both_conflict_results_run_and_clear_the_proposal(self):
        for n in (0, 1):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                cid = self.card()
                aid = self.add(cid)
                self.set_source(aid, "corrected")
                self.redo("第二次")
                self.kb("agenda", "recheck", aid, "--propose-date", "2999-12-20")
                (ask,) = [a for a in self.todo()["asks"] if a["kind"] == "agenda_conflict"]
                for line in ask["results"][n]["do"]:
                    args = shlex.split(line)
                    self.assertEqual(args[0], "kb")
                    self.kb(*args[1:])
                it = self.item(aid)
                self.assertNotIn("proposal", it)
                self.assertEqual(it["date"], "2999-12-20" if n == 0 else "2999-11-30")
                self.assertEqual([a for a in self.todo()["asks"] if a["kind"] == "agenda_conflict"], [])

    def test_done_item_is_not_listed(self):
        cid = self.card()
        aid = self.add(cid)
        self.kb("agenda", "done", aid)
        self.redo("第二次")
        self.assertEqual(self.todo()["agenda_recheck"], [])

    def test_entities_block_has_progress_and_asked_evidence(self):
        rows = {e["name"]: e for e in self.todo()["entities"]}
        self.assertEqual((rows["大安陳"]["progress"], rows["大安陳"]["asked_evidence"]), ("ongoing", []))
        self.assertNotIn("progress", rows["林小姐室內設計"])
        fid = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.kb("entity", "progress", "大安陳", "進行中", "--evidence", fid, "--note", "客戶說還沒結案")
        self.assertEqual({e["name"]: e for e in self.todo()["entities"]}["大安陳"]["asked_evidence"], [fid])

    def test_agenda_counts_in_json(self):
        self.kb("agenda", "add", "--kind", "到期", "--date", PAST, "--text", "付款")
        self.kb("agenda", "add", "--kind", "到期", "--date", "2999-01-01", "--text", "遠的")
        self.assertEqual(self.todo()["agenda_counts"], {"overdue": 1, "today": 0, "upcoming": 0})

    def test_broken_agenda_does_not_break_todo(self):
        (self.V / ".system" / "agenda.json").write_text("{bad", encoding="utf-8")
        data = self.todo()
        self.assertEqual((data["agenda_recheck"], data["agenda_counts"]), ([], {"overdue": 0, "today": 0, "upcoming": 0}))
        self.assertIn("agenda", (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8"))


class Unscanned(Base):
    """確認後裁定 4（D-140，驗收 19）：agenda_unscanned 列還沒抽日期的卡（新卡、舊卡沒有 agenda_fp、重做後指紋變了），
    kb agenda scanned 後不列；existing 帶這張卡已有的 open 條目；plan 的順序；份數算進門檻。"""

    def one(self):
        return self.todo()["agenda_unscanned"]

    def test_new_card_and_legacy_card_are_listed_scanned_is_not(self):
        self.assertEqual(self.one(), [])
        cid = self.card()
        (row,) = self.one()
        self.assertEqual((row["id"], row["title"], row["existing"]), (cid, self.title, []))
        self.assertTrue(row["card_path"].startswith("檔案索引/") and row["card_path"].endswith(".md"), row["card_path"])
        entry = _cards.entry_of(self.V, _cards.MEETING)
        self.assertNotIn("agenda_fp", entry)
        self.assertEqual(self.kb("agenda", "scanned", cid), [f"已記下：「{self.title}」的日期抽過了"])
        self.assertEqual(self.one(), [])

    def test_redone_card_is_listed_again(self):
        cid = self.card()
        self.kb("agenda", "scanned", cid)
        self.redo("第二次")
        self.assertEqual([r["id"] for r in self.one()], [cid])
        self.kb("agenda", "scanned", cid)
        self.assertEqual(self.one(), [])

    def test_scanned_with_no_dates_found_still_counts(self):
        """沒抽到任何日期也要跑 scanned；沒跑就一直列。"""
        cid = self.card()
        self.assertEqual([r["id"] for r in self.one()], [cid])
        self.assertEqual([r["id"] for r in self.one()], [cid])
        self.kb("agenda", "scanned", cid)
        self.assertEqual(self.one(), [])

    def test_existing_lists_the_open_items_of_that_card(self):
        cid = self.card()
        a = self.add(cid, "交屋")
        b = self.add(cid, "驗收", "2999-12-01", "承諾")
        done = self.add(cid, "做完了", "2999-10-01")
        self.kb("agenda", "done", done)
        (row,) = self.one()
        self.assertEqual(row["existing"], [{"id": a, "kind": "到期", "date": "2999-11-30", "text": "交屋"},
                                           {"id": b, "kind": "承諾", "date": "2999-12-01", "text": "驗收"}])

    def test_first_line_says_how_many_and_plan_orders_the_steps(self):
        cid = self.card()
        aid = self.add(cid)
        self.redo("第二次")
        first = self.kb("cards", "todo")[0]
        self.assertTrue(first.endswith("；日程待重對 1；日程待抽 1"), first)
        data = self.todo()
        self.assertEqual([s["step"] for s in data["plan"]], ["dispatch"])  # card-maker：日程收進 dispatch（D-198）
        from kb._lib import batches
        parts = batches.split(self.V, data)  # 日程批次裡先重對再抽（D-140）
        self.assertEqual([b for b, _k, _i in parts["agenda"]], ["agenda_recheck", "agenda_unscanned"])
        self.kb("agenda", "recheck", aid)
        self.kb("agenda", "scanned", cid)
        self.assertNotIn("日程待", self.kb("cards", "todo")[0])

    def test_first_line_is_not_all_clear_while_cards_wait_for_dates(self):
        cid = self.card()
        self.assertEqual(self.kb("cards", "todo")[0].split("；")[-1], "日程待抽 1")
        self.kb("agenda", "scanned", cid)
        self.assertNotIn("日程待抽", self.kb("cards", "todo")[0])

    def test_counts_toward_the_ingest_auto_max_threshold(self):
        for rel, title in ((E.QUOTE, "信義王報價"), (E.ESTIMATE, "內湖李估價")):
            E.write_card(self.V, rel, title, [])
        data = self.todo()
        waiting = len(data["new"]) + len(data["stale"]) + len(data["unlinked"])
        self.assertEqual(len(data["agenda_unscanned"]), 2)
        for limit, batch in ((waiting + 1, True), (waiting + 2, False)):
            (self.V / ".system" / "config.json").write_text(json.dumps({"ingest_auto_max": limit}), encoding="utf-8")
            asks = self.todo()["asks"]
            self.assertEqual(bool(asks) and asks[0]["kind"] == "batch", batch, limit)
            if batch:
                self.assertEqual(asks[0]["question"].split("，")[0], f"有 {waiting + 2} 份要整理")

    def test_broken_agenda_gives_an_empty_bucket(self):
        self.card()
        (self.V / ".system" / "agenda.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(self.one(), [])


class TellAdded(Base):
    def test_three_added_this_turn_then_not_next_turn(self):
        cid = self.card()
        turn.start(self.V, None, "")
        self.add(cid, "開會", "2999-10-05", "會議")
        self.add(cid, "付第二期款", "2999-10-20")
        self.add(cid, "交屋", "2999-11-30")
        self.assertIn("加進日程：10/5 開會、10/20 付第二期款、11/30 交屋", self.todo()["tell"])
        turn.start(self.V, None, "")
        self.assertEqual([t for t in self.todo()["tell"] if t.startswith("加進日程")], [])

    def test_more_than_five_says_the_count(self):
        cid = self.card()
        turn.start(self.V, None, "")
        for i in range(6):
            self.add(cid, f"事{i}", f"2999-10-{10 + i}")
        self.assertIn("加進日程 6 條，看 系統/日程.md", self.todo()["tell"])


class Drop(Base):
    def test_drop_cancels_inferred_open_items_only(self):
        cid = self.card()
        a = self.add(cid, "交屋")
        b = self.add(cid, "驗收")
        self.set_source(b, "confirmed")
        c = self.add_json("--kind", "提醒", "--date", "2999-01-01", "--text", "客戶說的")
        (self.raw / _cards.MEETING).unlink()
        self.kb("inventory", "scan")
        t = turn.start(self.V, None, "")["turn"]
        lines = self.kb("cards", "drop", cid)
        self.assertEqual(lines, [f"已拿掉「{self.title}」的卡；說「退回上一版」救得回來", "從這張卡抽的日程 1 條改成取消"])
        self.assertEqual({i["id"]: i["status"] for i in agenda.load(self.V)["items"]},
                         {a: "cancelled", b: "open", c: "open"})
        self.assertIn(agenda.AGENDA_POSIX, {e["path"] for e in turn.entries(self.V, t)})
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual({i["id"]: i["status"] for i in agenda.load(self.V)["items"]}, {a: "open", b: "open", c: "open"})

    def test_drop_without_items_prints_no_extra_line(self):
        cid = self.card()
        (self.raw / _cards.MEETING).unlink()
        self.kb("inventory", "scan")
        self.assertEqual(self.kb("cards", "drop", cid), [f"已拿掉「{self.title}」的卡；說「退回上一版」救得回來"])


class Asks(Base):
    def setUp(self):
        super().setUp()
        self.cid = self.card()
        self.fid = _cards.entry_of(self.V, E.QUOTE)["id"]

    def make_all(self):
        """一題原檔不見、一條逾期、一條有提議、一條 suggest-close，外加沒確認名字的綜合卡。"""
        self.kb("agenda", "add", "--kind", "到期", "--date", PAST, "--text", "付第二期款", "--entity", "信義王")
        aid = self.add(self.cid, "交屋")
        self.set_source(aid, "confirmed")
        self.redo("第二次")
        self.kb("agenda", "recheck", aid, "--propose-date", "2999-12-20")
        self.kb("entity", "suggest-close", "信義王", "--evidence", self.fid)
        E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        E.write_card(self.V, E.ESTIMATE, "王宅估價", ["王宅"])
        (self.raw / "報價與合約" / "合約範本.txt").unlink()
        self.kb("inventory", "scan")
        return aid

    def test_order_and_numbering(self):
        self.make_all()
        asks = self.todo()["asks"]
        self.assertEqual([a["kind"] for a in asks], ["missing", "overdue", "agenda_conflict", "close", "name"])
        self.assertEqual([r["n"] for a in asks for r in a["results"]], list(range(1, 1 + sum(len(a["results"]) for a in asks))))

    def test_kb_asks_json_equals_todo(self):
        self.make_all()
        todo = self.todo()
        got = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)
        self.assertEqual(set(got), {"asks", "tell"})
        self.assertEqual(got["asks"], todo["asks"])
        self.assertEqual(got["tell"], [t for t in todo["tell"] if t.startswith(("加進日程", "照新版"))
                                       or "改成" in t])

    def test_kb_asks_tell_leaves_out_the_tidying_state_todo_tells(self):
        """確認後裁定 5（D-141）：整理狀態（不製卡、讀不到…）只在 cards todo 的 tell，kb asks 不帶。"""
        todo = self.todo()
        self.assertTrue([t for t in todo["tell"] if t.startswith("不製卡")], todo["tell"])
        got = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)
        self.assertEqual(got["tell"], [])
        self.assertNotIn("不製卡", "\n".join(self.kb("asks")))

    def test_kb_asks_tell_keeps_this_turns_action_sentences(self):
        turn.start(self.V, None, "")
        self.add(self.cid, "交屋", "2999-11-30")
        got = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)
        self.assertEqual(got["tell"], ["加進日程：11/30 交屋"])

    def test_kb_asks_is_read_only(self):
        self.make_all()
        before = {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                  for p in (self.V / ".system").rglob("*") if p.is_file() and "temp" not in p.parts}
        self.kb("asks")
        self.kb("asks", "--json")
        after = {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                 for p in (self.V / ".system").rglob("*") if p.is_file() and "temp" not in p.parts}
        self.assertEqual(after, before)

    def test_human_output_numbers_questions_and_results(self):
        self.kb("agenda", "add", "--kind", "到期", "--date", PAST, "--text", "付第二期款", "--entity", "信義王")
        lines = self.kb("asks")
        self.assertEqual(lines[:5], ["1/1 付第二期款（信義王）過了，完成了嗎？", "  1. 完成了", "  2. 不用了", "  3. 還沒，改到某天",
                                     "  4. 還沒，下次再問"])

    def test_nothing_to_ask(self):
        self.assertEqual(self.kb("asks")[0], "沒有要問的")
        self.assertEqual(json.loads(run_kb("asks", "--json", cwd=self.V).stdout)["asks"], [])

    def run_result(self, kind, n, fills=None):
        (ask,) = [a for a in self.todo()["asks"] if a["kind"] == kind]
        result = ask["results"][n]
        self.assertEqual(sorted(f["name"] for f in result["fill"]), sorted(fills or {}))
        for line in result["do"]:
            for key, value in (fills or {}).items():
                line = line.replace("{" + key + "}", value)
            args = shlex.split(line)
            self.assertEqual(args[0], "kb", line)
            self.kb(*args[1:])

    def test_overdue_results_run(self):
        for n, fills, status in ((0, None, "done"), (1, None, "cancelled"), (2, {"新日期": "2999-05-05"}, "open"), (3, None, "open")):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                aid = self.add_json("--kind", "承諾", "--date", PAST, "--text", "給報價", "--entity", "信義王")
                self.run_result("overdue", n, fills)
                self.assertEqual(self.item(aid)["status"], status)
                if n == 2:
                    self.assertEqual(self.item(aid)["date"], "2999-05-05")
                if n == 3:
                    self.assertEqual(self.item(aid)["date"], PAST)
                else:
                    self.assertEqual([a for a in self.todo()["asks"] if a["kind"] == "overdue"], [])

    def test_close_results_run(self):
        for n, progress in ((0, "closed"), (1, "ongoing"), (2, "paused")):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                fid = _cards.entry_of(self.V, E.QUOTE)["id"]
                self.kb("entity", "suggest-close", "信義王", "--evidence", fid)
                self.run_result("close", n)
                e = next(x for x in entities.load(self.V)["entities"] if x["name"] == "信義王")
                self.assertEqual(e["progress"], progress)
                self.assertEqual([a for a in self.todo()["asks"] if a["kind"] == "close"], [])
                self.assertEqual([i["status"] for i in pending.load(self.V)["items"] if i["kind"] == "close"], ["done"])
                self.kb("entity", "suggest-close", "信義王", "--evidence", fid) if progress == "ongoing" else None
                if progress == "ongoing":
                    self.assertEqual(len([i for i in pending.load(self.V)["items"] if i["kind"] == "close"]), 1)

    def test_every_fill_everywhere_is_an_object(self):
        self.make_all()
        for ask in self.todo()["asks"]:
            for r in ask["results"]:
                for f in r["fill"]:
                    self.assertEqual(set(f), {"name", "rule"}, r["label"])


if __name__ == "__main__":
    unittest.main()
