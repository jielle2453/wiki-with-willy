"""kb agenda 黑箱（日程第 2 點，驗收 1～3、16）：每個子子命令成功與拒絕各一、句子一字不差、終端機程式段與對話裡不記、
list／render、revert 後 agenda.json 與 日程.md 回去、--json。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, cards, journal, turn  # noqa: E402

ID = r"[af]-[0-9a-f]{8}"  # 內部編號：給人看的輸出一個都不能出現（確認後裁定 6）


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0, cwd=None):
        r = run_kb("agenda", *args, cwd=cwd or self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def items(self):
        return agenda.load(self.V)["items"]

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def new_turn(self):
        return turn.start(self.V, None, "")["turn"]

    def add(self, *extra, kind="到期", date="2026-11-30", text="交屋", entity="信義王"):
        args = ["add", "--kind", kind, "--date", date, "--text", text]
        if entity:
            args += ["--entity", entity]
        r = run_kb("agenda", *args, *extra, "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["item"]["id"]

    def card(self):
        path = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        front = cards.parse_front((self.V / path).read_text(encoding="utf-8"))
        return front["編號"], (front.get("內容指紋") or front["指紋"])[:12]


class Add(Base):
    def test_success_sentence_state_backup_and_md(self):
        t = self.new_turn()
        (line,) = self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王")
        self.assertEqual(line, "已加進日程：2026-11-30（週一）交屋（到期，信義王）")
        (it,) = self.items()
        self.assertEqual((it["source"], it["origin"], it["status"], it["entities"]), ("confirmed", None, "open", ["信義王"]))
        self.assertEqual({r["path"] for r in turn.entries(self.V, t)}, {agenda.AGENDA_POSIX, agenda.MD_POSIX})
        self.assertIn("交屋", (self.V / "系統" / "日程.md").read_text(encoding="utf-8"))

    def test_json(self):
        r = run_kb("agenda", "add", "--kind", "會議", "--date", "2026-11-30", "--text", "開會", "--time", "14:00", "--json",
                   cwd=self.V)
        out = json.loads(r.stdout)
        self.assertEqual((out["created"], out["item"]["kind"], out["item"]["time"]), (True, "meeting", "14:00"))

    def test_no_entity_and_time_in_sentence(self):
        (line,) = self.kb("add", "--kind", "會議", "--date", "2026-09-25", "--time", "14:00", "--text", "跟王先生看磁磚")
        self.assertEqual(line, "已加進日程：2026-09-25（週五）14:00 跟王先生看磁磚（會議）")

    def test_period_sentence(self):
        (line,) = self.kb("add", "--kind", "期間", "--start", "2026-09-01", "--date", "2026-11-30", "--text", "施工",
                          "--entity", "信義王")
        self.assertEqual(line, "已加進日程：2026-09-01（週二）～2026-11-30（週一）施工（期間，信義王）")

    def test_refusals_exact(self):
        base = ["add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王"]

        def swap(**kw):
            args = list(base)
            for flag, value in kw.items():
                i = args.index("--" + flag) if "--" + flag in args else None
                if i is None:
                    args += ["--" + flag, value]
                else:
                    args[i + 1] = value
            return args
        cases = [
            (swap(date="2026-02-30"), "日期要寫 YYYY-MM-DD，而且是真的日期"),
            (swap(date="2026/11/30"), "日期要寫 YYYY-MM-DD，而且是真的日期"),
            (swap(time="25:00"), "時間要寫 HH:MM"),
            (swap(kind="期間"), "期間要有 --start，而且只掛一個實體"),
            (swap(start="2026-12-01"), "開始不能晚於日期"),
            (swap(entity="王宅小"), "實體清單沒有「王宅小」，先 kb entity add"),
            (swap(kind="生日"), "種類要是 到期、會議、承諾、提醒、期間 之一"),
            (swap(card="f-00000000"), "清冊沒有 f-00000000"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb(*args, code=3), [want])
        self.assertFalse((self.V / ".system" / "agenda.json").exists())

    def test_card_makes_it_inferred_and_repeat_returns_existing(self):
        cid, fp = self.card()
        (line,) = self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王", "--card", cid)
        self.assertEqual(line, "已加進日程：2026-11-30（週一）交屋（到期，信義王）（推測，出處：信義王報價）")
        (it,) = self.items()
        self.assertEqual((it["source"], it["origin"]), ("inferred", {"card": cid, "fp": fp}))
        (again,) = self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王", "--card", cid)
        self.assertEqual(again, "日程裡已經有這條：2026-11-30（週一）交屋（到期，信義王）（推測，出處：信義王報價）")
        self.assertEqual(len(self.items()), 1)

    def test_card_whose_file_is_gone(self):
        path = E.write_card(self.V, E.ESTIMATE, "內湖李估價", ["信義王"])
        cid = cards.parse_front((self.V / path).read_text(encoding="utf-8"))["編號"]
        (self.raw / E.ESTIMATE).unlink()
        from tests import _cards
        _cards.scan_apply(self.V)
        self.assertEqual(self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--card", cid, code=3),
                         ["這張卡的原檔已不在"])

    def test_lead_days_and_bad_value(self):
        self.add("--lead-days", "14")
        self.assertEqual(self.items()[0]["lead_days"], 14)
        self.assertEqual(self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "x", "--lead-days", "-1", code=3),
                         ["提前天數要是 0 以上的整數"])

    def test_not_in_vault(self):
        self.assertEqual(self.kb("list", code=3, cwd=self.tmp), ["這裡不是知識庫；到知識庫裡執行，或加 --vault"])

    def test_broken_state_file(self):
        (self.V / ".system" / "agenda.json").write_text("{bad", encoding="utf-8")
        (line,) = self.kb("list", code=3)
        self.assertTrue(line.startswith("狀態檔讀不到：.system/agenda.json（"), line)


class EditStatus(Base):
    def test_edit_sentence_and_corrected(self):
        cid, _fp = self.card()
        aid = self.add("--card", cid)
        self.assertEqual(self.kb("edit", aid, "--date", "2026-12-15"), ["已改：交屋 2026-11-30（週一）→ 2026-12-15（週二）"])
        (it,) = self.items()
        self.assertEqual((it["date"], it["source"], len(it["history"])), ("2026-12-15", "corrected", 1))

    def test_edit_other_fields(self):
        aid = self.add()
        self.assertEqual(self.kb("edit", aid, "--text", "交屋驗收", "--time", "10:00", "--entity", "王宅", "--lead-days", "3"),
                         ["已改：說明 交屋 → 交屋驗收；時間 → 10:00；掛的實體 信義王 → 王宅；提前天數 → 3"])

    def test_edit_with_card_keeps_inferred(self):
        cid, _fp = self.card()
        aid = self.add("--card", cid)
        self.kb("edit", aid, "--date", "2026-12-15", "--card", cid)
        self.assertEqual(self.items()[0]["source"], "inferred")

    def test_edit_refusals(self):
        aid = self.add()
        self.assertEqual(self.kb("edit", aid, code=3), ["沒有給要改的欄位"])
        self.assertEqual(self.kb("edit", "a-ffffffff", "--date", "2026-12-01", code=3), ["日程沒有 a-ffffffff"])
        self.assertEqual(self.kb("edit", aid, "--date", "2026-02-30", code=3), ["日期要寫 YYYY-MM-DD，而且是真的日期"])

    def test_done_cancel_reopen(self):
        aid = self.add(text="第二期款")
        self.assertEqual(self.kb("done", aid), ["已記成完成：第二期款"])
        self.assertEqual(self.items()[0]["status"], "done")
        self.assertEqual(self.kb("reopen", aid), ["已重開：第二期款"])
        self.assertEqual(self.kb("cancel", aid), ["已取消：11/30 第二期款"])
        self.assertEqual(self.items()[0]["status"], "cancelled")
        self.assertEqual([h["change"] for h in self.items()[0]["history"]],
                         ["status open → done", "status done → open", "status open → cancelled"])

    def test_status_unknown_id(self):
        for sub in ("done", "cancel", "reopen"):
            with self.subTest(sub=sub):
                self.assertEqual(self.kb(sub, "a-ffffffff", code=3), ["日程沒有 a-ffffffff"])


class Recheck(Base):
    def setUp(self):
        super().setUp()
        self.cid, self.fp = self.card()

    def make(self, source):
        aid = self.add("--card", self.cid)
        d = agenda.load(self.V)
        d["items"][0].update(source=source, date="2026-12-15")
        d["items"][0]["origin"]["fp"] = "000000000000"
        (self.V / ".system" / "agenda.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return aid

    def test_without_proposal(self):
        aid = self.make("confirmed")
        self.assertEqual(self.kb("recheck", aid), ["已對過新版，不用改：交屋"])
        self.assertEqual(self.items()[0]["origin"]["fp"], self.fp)

    def test_with_proposal_sentence_and_state(self):
        aid = self.make("confirmed")
        self.assertEqual(self.kb("recheck", aid, "--propose-date", "2026-12-20"),
                         ["已記下新版的寫法，會問客戶要用哪個：交屋 12/15（客戶說的）↔ 12/20（新版）"])
        self.assertEqual(self.items()[0]["proposal"], {"date": "2026-12-20", "text": "交屋", "card": self.cid, "fp": self.fp})

    def test_with_text_proposal(self):
        aid = self.make("corrected")
        self.assertEqual(self.kb("recheck", aid, "--propose-text", "交屋驗收"),
                         ["已記下新版的寫法，會問客戶要用哪個：「交屋」（客戶說的）↔「交屋驗收」（新版）"])

    def test_inferred_proposal_refused(self):
        aid = self.make("inferred")
        self.assertEqual(self.kb("recheck", aid, "--propose-date", "2026-12-20", code=3), ["推測的條目直接 edit，不用提議"])

    def test_no_origin_refused(self):
        aid = self.add()
        self.assertEqual(self.kb("recheck", aid, code=3), ["這條不是從檔案卡抽的，沒有可以對的版本"])


class ListRender(Base):
    def test_empty(self):
        self.assertEqual(self.kb("list"), ["日程是空的"])
        self.assertEqual(self.kb("list", "--all"), ["日程是空的"])

    def test_default_lists_open_sorted_by_date_all_lists_everything(self):
        cid, _fp = self.card()
        late = self.add(date="2026-12-31", text="後面")
        early = self.add("--card", cid, date="2026-10-31", text="前面")
        done = self.add(date="2026-11-15", text="做完了")
        self.kb("done", done)
        self.assertEqual(self.kb("list"), ["- 2026-10-31（週六）到期 前面（信義王）〔推測〕", "- 2026-12-31（週四）到期 後面（信義王）"])
        self.assertEqual(self.kb("list", "--all"), ["- 2026-10-31（週六）到期 前面（信義王）〔推測〕",
                                                     "- 2026-11-15（週日）到期 做完了（信義王）完成",
                                                     "- 2026-12-31（週四）到期 後面（信義王）"])

    def test_list_shows_time_and_period_span(self):
        self.add("--time", "09:30", kind="會議", date="2026-11-30", text="開會", entity="")
        self.add("--start", "2026-09-01", kind="期間", date="2026-12-01", text="施工")
        self.assertEqual(self.kb("list"), ["- 2026-09-01（週二）～2026-12-01（週二）期間 施工（信義王）",
                                           "- 2026-11-30（週一）09:30 會議 開會"])

    def test_list_json(self):
        aid = self.add()
        out = json.loads(run_kb("agenda", "list", "--json", cwd=self.V).stdout)
        self.assertEqual([(i["kind"], i["id"]) for i in out["items"]], [("deadline", aid)])

    def test_render_rewrites_the_md(self):
        self.add()
        (self.V / "系統" / "日程.md").write_text("手改的", encoding="utf-8")
        self.assertEqual(self.kb("render"), ["日程：1 條"])
        self.assertIn("交屋", (self.V / "系統" / "日程.md").read_text(encoding="utf-8"))
        self.assertEqual(self.kb("render"), ["日程：1 條"])


class ProgramLines(Base):
    def test_terminal_writes_have_a_program_line_each(self):
        aid = self.add()
        self.kb("edit", aid, "--date", "2026-12-15")
        self.kb("done", aid)
        self.kb("reopen", aid)
        self.kb("cancel", aid)
        notes = [(p["note"], p["command"]) for p in self.programs()]
        self.assertEqual(notes, [("加進日程：2026-11-30 交屋", "agenda add"), ("改日程：交屋 11/30 → 12/15", "agenda edit"),
                                 ("日程完成：交屋", "agenda done"), ("日程重開：交屋", "agenda reopen"),
                                 ("日程取消：12/15 交屋", "agenda cancel")])

    def test_recheck_has_a_line_and_reads_do_not(self):
        cid, _fp = self.card()
        aid = self.add("--card", cid)
        n = len(self.programs())
        self.kb("list")
        self.kb("render")
        self.assertEqual(len(self.programs()), n)
        self.kb("recheck", aid)
        self.assertEqual(self.programs()[-1]["note"], "日程對過新版：交屋")

    def test_nothing_recorded_in_a_conversation(self):
        (self.V / ".system" / "turns" / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid", "started": "2026-10-19T10:00:00",
            "prompt_head": "", "approved": False, "note": "", "done": False}), encoding="utf-8")
        aid = self.add()
        self.kb("done", aid)
        self.assertEqual(self.programs(), [])
        self.assertEqual(self.items()[0]["history"][0]["by"], "ai")

    def test_terminal_history_is_by_program(self):
        aid = self.add()
        self.kb("done", aid)
        self.assertEqual(self.items()[0]["history"][0]["by"], "program")


class Scanned(Base):
    """確認後裁定 4：kb agenda scanned <f-…> 記這張卡現在的指紋（日程抽過了）。"""

    def test_success_sentence_state_and_program_line(self):
        cid, fp = self.card()
        t = self.new_turn()
        self.assertEqual(self.kb("scanned", cid), ["已記下：「信義王報價」的日期抽過了"])
        entry = next(e for e in json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))["entries"]
                     if e["id"] == cid)
        self.assertEqual(entry["agenda_fp"], fp)
        self.assertIn(".system/inventory.json", {r["path"] for r in turn.entries(self.V, t)})
        self.assertEqual([(p["note"], p["command"]) for p in self.programs()][-1], ("日程抽過：信義王報價", "agenda scanned"))
        self.assertEqual(self.kb("scanned", cid), ["已記下：「信義王報價」的日期抽過了"])

    def test_json(self):
        cid, fp = self.card()
        out = json.loads(run_kb("agenda", "scanned", cid, "--json", cwd=self.V).stdout)
        self.assertEqual((out["id"], out["title"], out["fp"]), (cid, "信義王報價", fp))

    def test_refusals_exit_3(self):
        self.assertEqual(self.kb("scanned", "f-00000000", code=3), ["清冊沒有 f-00000000"])
        from tests import _cards
        cid = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.assertEqual(self.kb("scanned", cid, code=3), ["這份還沒有檔案卡，先寫卡再抽日期"])

    def test_nothing_recorded_in_a_conversation(self):
        cid, _fp = self.card()
        (self.V / ".system" / "turns" / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid", "started": "2026-10-19T10:00:00",
            "prompt_head": "", "approved": False, "note": "", "done": False}), encoding="utf-8")
        self.kb("scanned", cid)
        self.assertEqual(self.programs(), [])

    def test_revert_takes_the_mark_back(self):
        cid, _fp = self.card()
        self.new_turn()
        self.kb("scanned", cid)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        inv = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))["entries"]
        self.assertNotIn("agenda_fp", next(e for e in inv if e["id"] == cid))


class NoInternalIds(Base):
    """確認後裁定 6（驗收 21）：add、edit、done、cancel、reopen、recheck、list、scanned 的人讀輸出一個 a-…、f-… 都沒有；--json 有。"""

    def test_human_output_has_no_ids_but_json_has(self):
        cid, _fp = self.card()
        outputs = []
        outputs += self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王", "--card", cid)
        outputs += self.kb("add", "--kind", "到期", "--date", "2026-11-30", "--text", "交屋", "--entity", "信義王", "--card", cid)
        aid = self.items()[0]["id"]
        outputs += self.kb("edit", aid, "--date", "2026-12-15")
        outputs += self.kb("edit", aid, "--date", "2026-12-15")
        outputs += self.kb("recheck", aid)
        d = agenda.load(self.V)
        d["items"][0]["source"] = "confirmed"
        (self.V / ".system" / "agenda.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        outputs += self.kb("recheck", aid, "--propose-date", "2026-12-20")
        outputs += self.kb("done", aid)
        outputs += self.kb("reopen", aid)
        outputs += self.kb("cancel", aid)
        outputs += self.kb("list", "--all")
        outputs += self.kb("render")
        outputs += self.kb("scanned", cid)
        self.assertGreater(len(outputs), 10)
        for line in outputs:
            self.assertIsNone(re.search(ID, line), line)
        j = run_kb("agenda", "add", "--kind", "提醒", "--date", "2026-12-01", "--text", "x", "--json", cwd=self.V)
        self.assertRegex(json.loads(j.stdout)["item"]["id"], ID)
        self.assertRegex(json.loads(run_kb("agenda", "list", "--json", cwd=self.V).stdout)["items"][0]["id"], ID)


class Revert(Base):
    def test_revert_restores_agenda_and_md(self):
        aid = self.add()
        before_json = (self.V / ".system" / "agenda.json").read_bytes()
        before_md = (self.V / "系統" / "日程.md").read_bytes()
        self.new_turn()
        self.kb("edit", aid, "--date", "2026-12-15")
        self.assertNotEqual((self.V / ".system" / "agenda.json").read_bytes(), before_json)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / ".system" / "agenda.json").read_bytes(), before_json)
        self.assertEqual((self.V / "系統" / "日程.md").read_bytes(), before_md)

    def test_revert_of_the_first_add_removes_the_files_again(self):
        self.new_turn()
        self.add()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.items(), [])


if __name__ == "__main__":
    unittest.main()
