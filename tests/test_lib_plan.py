"""plan.build（整理 ingest 第 1 點，D-132、D-134、D-135）：plan 的步驟與順序、asks 的順序與結果清單（do 字串一字不差、strict、
fill、連號跨題、引號）、多量先問、tell 的句子；最後一組拿真知識庫逐條跑 do（驗收 6）。"""
import json
import re
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import _cards
from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, config, inventory, plan, turn  # noqa: E402
from kb._lib.paths import plugin_root  # noqa: E402

TYPES = [{"name": "案子", "category": "matter"}, {"name": "業主", "category": "party"}]
SYNTHESIS_RULES = str(plugin_root() / "rules" / "synthesis.md")
FREE_TEXT = {"name": "客戶的話", "rule": "一句白話，不含 \" $ ` \\ 這四種字元"}
NAME_RULE = "一個名字，不含 \" $ ` \\ 這四種字元；有空白就加雙引號"
NEW_NAME = {"name": "新名字", "rule": NAME_RULE}
SAME_ONE = {"name": "同一個", "rule": NAME_RULE}
NEW_DATE = {"name": "新日期", "rule": "YYYY-MM-DD"}


def base(**kw):
    out = {"new": [], "stale": [], "moved": [], "duplicates": [], "orphans": [], "unreadable": [], "missing": [],
           "skipped": {"credential": 0, "placeholder": 0, "too_large": 0, "error": 0, "skipped": 0}, "broken": [],
           "pdf_restored": 0, "unlinked": [], "missing_types": {}, "profile_missing": False,
           "profile": {"types": TYPES}, "synthesis_new": [], "synthesis_stale": [], "synthesis_broken": [],
           "entity_drift": [], "entities": [], "agenda_recheck": [], "agenda_counts": {"overdue": 0, "today": 0, "upcoming": 0}}
    out.update(kw)
    return out


def work(n):
    return [{"id": f"f-0000000{i}", "uri": f"file://a/{i}.txt"} for i in range(n)]


def drift(kind, name="信義王", found=None, eid="e-5c1d0a7e", type_="案子"):
    return {"entity_id": eid, "name": name, "type": type_, "kind": kind, "found": found, "question": f"Q-{kind}"}


class Base(unittest.TestCase):
    def setUp(self):
        self.V = Path(tempfile.mkdtemp())
        (self.V / ".system").mkdir()

    def tearDown(self):
        shutil.rmtree(self.V, ignore_errors=True)

    def build(self, **kw):
        return plan.build(base(**kw), self.V, config)

    def set_max(self, n):
        (self.V / ".system" / "config.json").write_text(json.dumps({"ingest_auto_max": n}), encoding="utf-8")


class Plan(Base):
    def steps(self, **kw):
        return [(s["step"], s["items"]) for s in self.build(**kw)["plan"]]

    def test_empty(self):
        self.assertEqual(self.build()["plan"], [])

    def test_only_moved_is_sync(self):
        got = self.build(moved=[{"id": "f-1", "from": "a", "to": "b"}])["plan"]
        self.assertEqual(got, [{"step": "sync", "items": 1, "do": "kb cards sync", "read": None}])

    def test_duplicates_also_sync(self):
        self.assertEqual(self.steps(duplicates=[{"id": "f-1", "keep": "a", "drop": ["b"]}]), [("sync", 1)])

    def test_cards_and_synthesis_stale(self):
        """card-maker（D-196、D-198）：做卡、日程、綜合卡收成一步 dispatch，清單由程式切。"""
        got = self.build(new=work(2), synthesis_stale=[{"entity_id": "e-1", "name": "信義王"}])["plan"]
        self.assertEqual(got, [{"step": "dispatch", "items": 3, "do": "kb cards dispatch --json", "read": None}])

    def test_cards_count_new_stale_and_broken_with_header(self):
        broken = [{"path": "a", "reason": "x", "id": "f-1", "header": "h"}, {"path": "b", "reason": "y"}]
        stale = [{"id": "f-s1"}, {"id": "f-s2"}]
        self.assertEqual(self.steps(new=work(1), stale=stale, broken=broken), [("dispatch", 4)])
        self.assertEqual(self.steps(new=work(1), stale=work(1)), [("dispatch", 1)], "同一份只算一次")

    def test_broken_without_header_is_not_a_step(self):
        self.assertEqual(self.steps(broken=[{"path": "b", "reason": "y"}]), [])

    def test_agenda_and_unlinked_go_into_dispatch(self):
        rows = [{"id": "a-1", "card": "f-1", "card_path": "p", "kind": "到期", "date": "2026-11-30", "text": "交屋",
                 "source": "inferred"}]
        scanned = [{"id": "f-9", "card_path": "p", "title": "報價", "existing": []}]
        self.assertEqual(self.steps(new=work(1), unlinked=[{"id": "f-2"}], agenda_recheck=rows, agenda_unscanned=scanned),
                         [("dispatch", 4)])
        self.assertEqual(self.steps(agenda_recheck=rows), [("dispatch", 1)])

    def test_synthesis_broken_goes_into_dispatch(self):
        got = self.build(unlinked=[{"id": "f-1"}], synthesis_broken=[{"path": "p", "reason": "r"}])["plan"]
        self.assertEqual([(s["step"], s["items"], s["read"]) for s in got], [("dispatch", 2, None)])

    def test_synthesis_new_counts_confirmed_only(self):
        new = [{"entity_id": "e-1", "name": "甲", "confirmed": True}, {"entity_id": "e-2", "name": "乙", "confirmed": False, "question": "Q"}]
        self.assertEqual(self.steps(synthesis_new=new), [("dispatch", 1)])
        self.assertEqual(self.steps(synthesis_new=new[1:]), [])

    def test_full_order(self):
        got = self.steps(moved=[{"id": "f-1"}], new=work(1), unlinked=[{"id": "f-2"}], synthesis_broken=[{"path": "p"}],
                         synthesis_stale=[{"entity_id": "e"}], synthesis_new=[{"entity_id": "e2", "confirmed": True}])
        self.assertEqual(got, [("sync", 1), ("dispatch", 5)])

    def test_link_entry_stays_with_the_main_conversation(self):
        """驗收 13c：線上文件（link 條目）不切進批次，plan 另留一步 cards 只列它，read 一～六節。"""
        t = turn.start(self.V, None, "")["turn"]
        link = inventory.register_link(self.V, "https://claude.ai/artifact/abc", t, "報價說明")
        got = self.build(new=[{"id": link["id"], "uri": "https://claude.ai/artifact/abc", "kind": "link"}])["plan"]
        self.assertEqual(got, [{"step": "cards", "items": 1, "do": plan.MAIN_CARDS, "read": "references/carding.md 一～六節",
                                "files": [link["id"]]}])
        got = self.build(new=[{"id": link["id"]}] + work(2))["plan"]
        self.assertEqual([(s["step"], s["items"]) for s in got], [("dispatch", 2), ("cards", 1)])

    def test_credential_and_locked_entries_stay_with_the_main_conversation(self):
        """交界第 1 點：檔名像密碼的（含可以讀的）、待處理記過有密碼的不切進批次。"""
        from kb._lib import pending
        t = turn.start(self.V, None, "")["turn"]
        entries = [{"id": "f-c", "status": "active", "read": "credential", "locations": [{"uri": "file://a/密碼.txt"}]},
                   {"id": "f-p", "status": "active", "read": "ok", "locations": [{"uri": "file://a/保單.pdf"}]}]
        (self.V / ".system" / "inventory.json").write_text(json.dumps({"schema_version": 2, "entries": entries}),
                                                            encoding="utf-8")
        pending.add(self.V, "unreadable", ["f-p"], "「保單.pdf」讀不到", "有密碼", t)
        pending.auto_done(self.V, "f-p", "unreadable", "用密碼打開了", t)
        got = self.build(new=[{"id": "f-c"}, {"id": "f-p"}] + work(1))["plan"]
        self.assertEqual([(s["step"], s["items"], s.get("files")) for s in got],
                         [("dispatch", 1, None), ("cards", 2, ["f-c", "f-p"])])

    def test_pure(self):
        out = base(new=work(2))
        before = json.dumps(out, sort_keys=True)
        plan.build(out, self.V, config)
        self.assertEqual(json.dumps(out, sort_keys=True), before)


class Batch(Base):
    def question(self, n, how_long):
        return f"有 {n} 份要整理，大約{how_long}；份數多，會用掉比平常多的額度。現在做嗎？"

    def test_over_max_asks_first_and_keeps_only_sync(self):
        self.set_max(1)
        got = self.build(new=work(2), moved=[{"id": "f-1"}], unlinked=[], synthesis_stale=[{"entity_id": "e"}])
        self.assertEqual([s["step"] for s in got["plan"]], ["sync"])
        ask = got["asks"][0]
        self.assertEqual((ask["id"], ask["kind"]), ("batch", "batch"))
        self.assertEqual(ask["question"], self.question(2, "一兩分鐘"))
        self.assertEqual([(r["n"], r["label"], r["do"], r["strict"], r["fill"]) for r in ask["results"]],
                         [(1, "現在做", [], False, []), (2, "之後再說", [], False, [])])
        self.assertEqual(ask["results"][1]["note"], "這個對話不再問")

    def test_now_carries_the_full_plan(self):
        self.set_max(1)
        got = self.build(new=work(2), moved=[{"id": "f-1"}], synthesis_stale=[{"entity_id": "e"}])
        now = got["asks"][0]["results"][0]
        self.assertEqual([s["step"] for s in now["then"]], ["sync", "dispatch"])

    def test_then_equals_the_unclipped_plan(self):
        """裁定 6：batch 的「現在做」帶的 then，就是沒超過上限時算出來的那份 plan。"""
        kw = dict(new=work(2), moved=[{"id": "f-1"}], unlinked=[{"id": "f-2"}], synthesis_stale=[{"entity_id": "e"}],
                  synthesis_new=[{"entity_id": "e", "name": "甲", "confirmed": True}])
        self.set_max(1)
        clipped = self.build(**kw)
        self.set_max(100)
        unclipped = self.build(**kw)
        self.assertEqual(unclipped["asks"], [])
        self.assertEqual([s["step"] for s in unclipped["plan"]], ["sync", "dispatch"])
        self.assertEqual(clipped["asks"][0]["results"][0]["then"], unclipped["plan"])
        self.assertEqual(clipped["plan"], unclipped["plan"][:1])

    def test_agenda_unscanned_counts_toward_the_threshold(self):
        """確認後裁定 4：份數算進 ingest_auto_max 的門檻（new＋stale＋unlinked＋agenda_unscanned）。"""
        rows = [{"id": f"f-{i}", "card_path": "p", "title": "t", "existing": []} for i in range(3)]
        self.set_max(3)
        self.assertEqual(self.build(agenda_unscanned=rows)["asks"], [])
        got = self.build(agenda_unscanned=rows, new=work(1))
        self.assertEqual((got["asks"][0]["id"], got["asks"][0]["question"]), ("batch", self.question(4, "一兩分鐘")))
        self.assertEqual(got["plan"], [])
        self.assertEqual([s["step"] for s in got["asks"][0]["results"][0]["then"]], ["dispatch"])

    def test_counts_new_stale_and_unlinked(self):
        self.set_max(2)
        self.assertEqual(self.build(new=work(1), stale=work(1))["asks"], [])
        got = self.build(new=work(1), stale=work(1), unlinked=[{"id": "f-9"}])
        self.assertEqual(got["asks"][0]["id"], "batch")
        self.assertEqual(got["plan"], [])

    def test_equal_to_max_does_not_ask(self):
        self.set_max(2)
        got = self.build(new=work(2))
        self.assertEqual(got["asks"], [])
        self.assertEqual([s["step"] for s in got["plan"]], ["dispatch"])

    def test_minutes_from_config(self):
        """D-199：秒數 = 份數 × card_seconds_per_file + card_batch_overhead_seconds，不到 60 秒說一兩分鐘。"""
        self.set_max(1)
        self.assertEqual(self.build(new=work(5))["asks"][0]["question"], self.question(5, " 1 分鐘"))  # 5×6+30 = 60
        self.assertEqual(self.build(new=work(4))["asks"][0]["question"], self.question(4, "一兩分鐘"))  # 54
        self.assertEqual(self.build(new=work(6))["asks"][0]["question"], self.question(6, " 2 分鐘"))  # 66 進位
        (self.V / ".system" / "config.json").write_text(json.dumps(
            {"ingest_auto_max": 1, "card_seconds_per_file": 20, "card_batch_overhead_seconds": 0}), encoding="utf-8")
        self.assertEqual(self.build(new=work(3))["asks"][0]["question"], self.question(3, " 1 分鐘"))
        self.assertEqual(self.build(new=work(2))["asks"][0]["question"], self.question(2, "一兩分鐘"))

    def test_default_max_is_twenty(self):
        """D-100 2026-10-20 修訂：門檻 5 → 20。"""
        self.assertEqual(self.build(new=work(20))["asks"], [])
        self.assertEqual(self.build(new=work(21))["asks"][0]["id"], "batch")


class Announce(Base):
    """note 改規則第 5 點（D-100 修訂、D-199、D-200）：門檻內有要整理的份數時，開始前先說的一句；0 份、超過門檻是 null；kb asks 不帶。"""

    def test_zero_is_null(self):
        self.assertIsNone(self.build()["announce"])

    def test_within_threshold_has_sentence(self):
        self.assertEqual(self.build(new=work(3))["announce"], "有 3 份要整理，大約一兩分鐘；要停的話跟我說一聲")
        rows = [{"id": f"f-{i}", "card_path": "p", "title": "t", "existing": []} for i in range(4)]
        got = self.build(new=work(3), stale=work(2), unlinked=[{"id": "f-9"}] * 3, agenda_unscanned=rows)
        self.assertEqual(got["announce"], "有 12 份要整理，大約 2 分鐘；要停的話跟我說一聲")  # 12×6+30 = 102
        self.assertIsNone(self.build(moved=[{"id": "f-1"}])["announce"], "只有搬卡不算要整理的份數")

    def test_over_threshold_is_null_and_batch(self):
        got = self.build(new=work(21))
        self.assertIsNone(got["announce"])
        self.assertEqual(got["asks"][0]["id"], "batch")

    def test_asks_only_has_no_announce(self):
        self.assertNotIn("announce", plan.build(base(new=work(3)), self.V, config, tell_all=False))

    def test_cards_todo_json_has_it_and_kb_asks_not(self):
        tmp, V, _raw = _cards.make_card_vault()
        try:
            todo = json.loads(run_kb("cards", "todo", "--json", cwd=V).stdout)
            n = len(todo["new"]) + len(todo["stale"]) + len(todo["unlinked"]) + len(todo["agenda_unscanned"])
            seconds = n * 6 + 30
            how = "一兩分鐘" if seconds < 60 else f" {-(-seconds // 60)} 分鐘"
            self.assertEqual(todo["announce"], f"有 {n} 份要整理，大約{how}；要停的話跟我說一聲")
            self.assertNotIn("announce", json.loads(run_kb("asks", "--json", cwd=V).stdout))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Asks(Base):
    def results(self, ask):
        return [(r["n"], r["label"], r["do"], r["strict"], r["fill"]) for r in ask["results"]]

    def one(self, **kw):
        (ask,) = self.build(**kw)["asks"]
        return ask

    def test_folder_renamed(self):
        ask = self.one(entity_drift=[drift("folder_renamed", found="案子/王宅/信義王.md")])
        self.assertEqual((ask["id"], ask["kind"], ask["question"]), ("drift:e-5c1d0a7e", "drift_folder_renamed", "Q-folder_renamed"))
        self.assertEqual(self.results(ask), [
            (1, "跟著改名叫王宅", ["kb entity rename 信義王 --to 王宅"], False, []),
            (2, "改回信義王", ["kb entity rename 信義王 --to 信義王"], False, []),
            (3, "改成別的名字", ["kb entity rename 信義王 --to {新名字}"], False, [NEW_NAME])])

    def test_both_renamed_same_results(self):
        ask = self.one(entity_drift=[drift("both_renamed", found="案子/王宅/王宅.md")])
        self.assertEqual(ask["kind"], "drift_both_renamed")
        self.assertEqual(ask["results"][0]["do"], ["kb entity rename 信義王 --to 王宅"])

    def test_card_renamed(self):
        ask = self.one(entity_drift=[drift("card_renamed", found="案子/信義王/王宅.md")])
        self.assertEqual(ask["kind"], "drift_card_renamed")
        self.assertEqual(self.results(ask), [
            (1, "用新檔名王宅", ["kb entity rename 信義王 --to 王宅"], False, []),
            (2, "改回信義王", ["kb entity rename 信義王 --to 信義王"], False, []),
            (3, "改成別的名字", ["kb entity rename 信義王 --to {新名字}"], False, [NEW_NAME])])

    def test_moved_type(self):
        ask = self.one(entity_drift=[drift("moved_type", found="業主/信義王/信義王.md")])
        self.assertEqual(ask["kind"], "drift_moved_type")
        self.assertEqual(self.results(ask), [
            (1, "跟著改到業主", ["kb entity rename 信義王 --to 信義王 --type 業主"], False, []),
            (2, "搬回案子", ["kb entity rename 信義王 --to 信義王"], False, [])])

    def test_moved_type_not_in_table(self):
        ask = self.one(entity_drift=[drift("moved_type", found="廠商/信義王/信義王.md")])
        first = ask["results"][0]
        self.assertEqual((first["n"], first["label"], first["do"], first["strict"]), (1, "先把廠商加進設定檔", [], False))
        self.assertTrue(first["note"])
        self.assertEqual(self.results(ask)[1], (2, "搬回案子", ["kb entity rename 信義王 --to 信義王"], False, []))

    def test_drift_missing(self):
        ask = self.one(entity_drift=[drift("missing")])
        self.assertEqual(ask["kind"], "drift_missing")
        self.assertEqual(self.results(ask), [
            (1, "不要了", ["kb entity remove 信義王"], True, []),
            (2, "刪錯了，我從資源回收筒還原", [], False, []),
            (3, "刪錯了，照檔案卡重寫一張", ["kb entity rebuild 信義王"], False, [])])

    def test_file_missing(self):
        item = {"id": "f-98c66aa7", "uri": "file://客戶案件/大安陳/會議紀錄_1.docx", "pending_id": "p-1a2b3c4d",
                "question": "Q-missing"}
        ask = self.one(missing=[item])
        self.assertEqual((ask["id"], ask["kind"], ask["question"]), ("missing:p-1a2b3c4d", "missing", "Q-missing"))
        self.assertEqual(self.results(ask), [
            (1, "刪了，卡也拿掉", ["kb cards drop f-98c66aa7"], True, []),
            (2, "沒刪，先留著", ['kb pending resolve p-1a2b3c4d --how "{客戶的話}"'], False, [FREE_TEXT]),
            (3, "不用管", ["kb pending dismiss p-1a2b3c4d"], False, [])])

    def test_name_confirm(self):
        new = [{"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q-name"}]
        ask = self.one(synthesis_new=new)
        self.assertEqual((ask["id"], ask["kind"], ask["question"]), ("name:e-0f3e9a21", "name", "Q-name"))
        self.assertEqual(self.results(ask), [
            (1, "名字對", ["kb entity confirm 王宅"], False, []),
            (2, "跟已有的另一個是同一個", ["kb entity merge 王宅 --into {同一個}"], False, [SAME_ONE]),
            (3, "名字改成別的", ["kb entity rename 王宅 --to {新名字}", "kb entity confirm {新名字}"], False, [NEW_NAME])])

    def test_confirmed_new_has_no_ask(self):
        self.assertEqual(self.build(synthesis_new=[{"entity_id": "e", "name": "甲", "confirmed": True}])["asks"], [])

    def test_order_and_numbering(self):
        missing = {"id": "f-98c66aa7", "uri": "file://a/b.docx", "pending_id": "p-1a2b3c4d", "question": "Q-file"}
        new = {"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q-name"}
        got = self.build(
            synthesis_new=[new], missing=[missing],
            entity_drift=[drift("folder_renamed", found="案子/王宅邸/信義王.md"),
                          drift("missing", name="大安陳", eid="e-1a2b3c4d")])["asks"]
        self.assertEqual([a["id"] for a in got], ["drift:e-1a2b3c4d", "drift:e-5c1d0a7e", "missing:p-1a2b3c4d", "name:e-0f3e9a21"])
        ns = [r["n"] for a in got for r in a["results"]]
        self.assertEqual(ns, list(range(1, 13)))
        self.assertEqual([r["label"] for a in got for r in a["results"] if r["strict"]], ["不要了", "刪了，卡也拿掉"])

    def test_batch_comes_before_everything(self):
        self.set_max(1)
        got = self.build(new=work(2), entity_drift=[drift("missing")])["asks"]
        self.assertEqual([a["id"] for a in got], ["batch", "drift:e-5c1d0a7e"])
        self.assertEqual([r["n"] for a in got for r in a["results"]], [1, 2, 3, 4, 5])

    def test_every_fill_is_an_object_with_a_rule(self):
        """日程釐清（整理 ingest 裁定 12 留下的）：fill 一律 {"name","rule"}；do 裡每個 {空格} 都在 fill 裡；
        名字類的 rule 也擋 " $ ` \\ 四種字元。"""
        asks = self.build(
            missing=[{"id": "f-98c66aa7", "uri": "file://a/b.docx", "pending_id": "p-1a2b3c4d", "question": "Q"}],
            entity_drift=[drift("folder_renamed", found="案子/王宅/信義王.md")],
            synthesis_new=[{"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q"}])["asks"]
        fills = []
        for ask in asks:
            for r in ask["results"]:
                self.assertTrue(all(isinstance(f, dict) and set(f) == {"name", "rule"} for f in r["fill"]), r["label"])
                names = [f["name"] for f in r["fill"]]
                slots = sorted(set(re.findall(r"\{([^{}]+)\}", " ".join(r["do"]))))
                self.assertEqual(sorted(names), slots, r["label"])
                fills += r["fill"]
        self.assertEqual(fills, [NEW_NAME, FREE_TEXT, SAME_ONE, NEW_NAME])
        for rule in (FREE_TEXT["rule"], NAME_RULE):
            for ch in ('"', "$", "`", "\\"):
                self.assertIn(ch, rule)

    def test_names_with_spaces_are_quoted(self):
        ask = self.one(entity_drift=[drift("missing", name="陳 先生")])
        self.assertEqual(ask["results"][0]["do"], ['kb entity remove "陳 先生"'])
        ask = self.one(entity_drift=[drift("folder_renamed", name="陳 先生", found="案子/王 宅/陳 先生.md")])
        self.assertEqual(ask["results"][0]["do"], ['kb entity rename "陳 先生" --to "王 宅"'])
        self.assertEqual(ask["results"][1]["do"], ['kb entity rename "陳 先生" --to "陳 先生"'])
        self.assertEqual(ask["results"][2]["do"], ['kb entity rename "陳 先生" --to {新名字}'])
        new = [{"entity_id": "e-1", "name": "陳 先生", "confirmed": False, "question": "Q"}]
        self.assertEqual(self.one(synthesis_new=new)["results"][2]["do"],
                         ['kb entity rename "陳 先生" --to {新名字}', "kb entity confirm {新名字}"])

    def test_special_characters_are_quoted(self):
        ask = self.one(entity_drift=[drift("missing", name='王"宅')])
        self.assertEqual(ask["results"][0]["do"], [r'kb entity remove "王\"宅"'])
        ask = self.one(entity_drift=[drift("missing", name="王&宅")])
        self.assertEqual(ask["results"][0]["do"], ['kb entity remove "王&宅"'])
        self.assertEqual(shlex.split(ask["results"][0]["do"][0]), ["kb", "entity", "remove", "王&宅"])


class Tell(Base):
    def tell(self, **kw):
        return self.build(**kw)["tell"]

    def test_empty(self):
        self.assertEqual(self.tell(), [])

    def test_unreadable_each_with_trailer(self):
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://客戶案件/信義王/合約_加密.pdf", "reason": "有密碼"}])
        self.assertEqual(got, ["合約_加密.pdf 讀不到：有密碼；給我密碼就能打開，要我記住也可以；記在 系統/待處理.md"])

    def test_unreadable_two(self):
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.pdf", "reason": "有密碼"},
                                    {"id": "f-2", "uri": "file://a/乙.doc", "reason": "舊格式"}])
        self.assertEqual(got, ["甲.pdf 讀不到：有密碼；給我密碼就能打開，要我記住也可以",
                               "乙.doc 讀不到：舊格式；用 Word／Excel 另存成新格式就讀得到；記在 系統/待處理.md"])

    def test_unreadable_five_listed_six_merged(self):
        items = [{"id": f"f-{i}", "uri": f"file://a/{i}.pdf", "reason": "有密碼"} for i in range(6)]
        self.assertEqual(len(self.tell(unreadable=items[:5])), 5)
        got = self.tell(unreadable=items)
        self.assertEqual(got, [f"{i}.pdf 讀不到：有密碼；給我密碼就能打開，要我記住也可以" for i in range(5)]
                         + ["另有 1 份讀不到；記在 系統/待處理.md"])

    ADVICE = {
        "有密碼": "這種檔這版還打不開，用 Office 打開另存一份沒密碼的再放進來",  # 密碼與守門：不是 PDF 的照 Word、Excel 講（D-182）
        "舊格式（.doc）": "用 Word／Excel 另存成新格式就讀得到",
        "不支援的格式（.zip）": "這種格式還讀不了，跟我說先跳過，或換一份檔",
        "只在雲端、還沒下載到電腦": "請在檔案上按右鍵選保留在這台電腦上，我再整理",
        "還沒裝 PDF 元件（pip install pypdfium2）": "請服務人員跑 pip install pypdfium2，裝好後我會自動整理",
        "檔案壞了：PDF 打不開": "換一份，或跟我說先跳過",
        "檔案太大": "這版讀不了，跟我說先跳過",
        "編碼": "換一份檔，或跟我說先跳過",
    }

    def test_unreadable_advice_for_every_recorded_reason(self):
        """裁定 7：convert 會記進待處理的每個原因，tell 都帶「怎麼辦」。"""
        for reason, advice in self.ADVICE.items():
            with self.subTest(reason=reason):
                got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.x", "reason": reason}])
                self.assertEqual(got, [f"甲.x 讀不到：{reason}；{advice}；記在 系統/待處理.md"])

    def test_unreadable_unknown_reason_has_no_advice(self):
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.x", "reason": "怪原因"}])
        self.assertEqual(got, ["甲.x 讀不到：怪原因；記在 系統/待處理.md"])

    def test_not_carded_counts(self):
        got = self.tell(skipped={"credential": 2, "placeholder": 0, "too_large": 1, "error": 0, "skipped": 0})
        self.assertEqual(got, ["不製卡：檔名像密碼或金鑰 2、太大 1；要讓我讀哪一份跟我說「X 可以讀」"])  # 密碼與守門

    def test_cloud_only_adds_sentence(self):
        got = self.tell(skipped={"credential": 0, "placeholder": 3, "too_large": 0, "error": 1, "skipped": 0})
        self.assertEqual(got, ["不製卡：只在雲端 3、讀不到 1", "請在檔案上按右鍵選保留在這台電腦上，我再整理"])

    def test_skipped_by_setting_without_kinds(self):
        got = self.tell(skipped={"credential": 0, "placeholder": 0, "too_large": 0, "error": 0, "skipped": 4})
        self.assertEqual(got, ["4 份依你的設定不整理"])

    def test_missing_types(self):
        got = self.tell(missing_types={"廠商": 4, "工班": 1})
        self.assertEqual(got, [
            "有 4 份看起來跟 廠商 有關，但設定檔裡沒有『廠商』這一類，要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用",
            "有 1 份看起來跟 工班 有關，但設定檔裡沒有『工班』這一類，要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用"])

    def test_no_markdown_markers(self):
        """裁定 13：tell 的句子不帶 Markdown 記號（星號）。"""
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.doc", "reason": "舊格式（.doc）"}],
                        skipped={"credential": 1, "placeholder": 1, "too_large": 0, "error": 0, "skipped": 3},
                        missing_types={"員工": 2}, pdf_restored=1)
        self.assertTrue(got)
        for line in got:
            self.assertNotIn("*", line)

    def test_pdf_restored(self):
        self.assertEqual(self.tell(pdf_restored=2), ["PDF 元件裝好了，2 份 PDF 回到待製卡"])

    def test_order(self):
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.pdf", "reason": "有密碼"}],
                        skipped={"credential": 1, "placeholder": 0, "too_large": 0, "error": 0, "skipped": 3},
                        missing_types={"廠商": 1}, pdf_restored=1)
        self.assertEqual([s.split(" ")[0] for s in got], ["甲.pdf", "不製卡：檔名像密碼或金鑰", "3", "有", "PDF"])


DAY0 = "2020-01-01"  # 一定已經過了的日期


def agenda_item(**kw):
    base = {"id": "a-5e6f7a8b", "kind": "deadline", "start": None, "date": DAY0, "ended": None, "time": None,
            "text": "付第二期款", "entities": ["王宅"], "status": "open", "source": "inferred", "origin": None,
            "lead_days": None, "created_turn": "20200101-000000-test", "history": []}
    base.update(kw)
    return base


def put_agenda(V, *items):
    (Path(V) / ".system" / "agenda.json").write_text(
        json.dumps({"schema_version": 1, "items": list(items)}, ensure_ascii=False), encoding="utf-8")


def close_item(**kw):
    base = {"id": "p-9a8b7c6d", "kind": "close", "file_ids": ["f-3a553095"], "entity_ids": ["e-0f3e9a21"],
            "text": "「王宅」看起來已經結束了，要記成結案嗎？", "reason": "依據〔f-3a553095〕", "created": "2026-10-20T10:00:00",
            "created_turn": "t", "status": "open", "resolved": None, "how": None}
    base.update(kw)
    return base


def put_pending(V, *items):
    (Path(V) / ".system" / "pending.json").write_text(
        json.dumps({"schema_version": 1, "items": list(items)}, ensure_ascii=False), encoding="utf-8")


def put_entities(V):
    E.put_entities(V, [E.entity("e-0f3e9a21", "王宅", source="inferred")])


class AgendaAsks(Base):
    """日程第 5 點：overdue、agenda_conflict、close 三種題的結果清單與 do 字串、順序、編號。"""

    def one(self):
        (ask,) = self.build()["asks"]
        return ask

    def results(self, ask):
        return [(r["n"], r["label"], r["do"], r["strict"], r["fill"]) for r in ask["results"]]

    def test_overdue(self):
        put_agenda(self.V, agenda_item())
        ask = self.one()
        self.assertEqual((ask["id"], ask["kind"], ask["question"]),
                         ("overdue:a-5e6f7a8b", "overdue", "1/1 付第二期款（王宅）過了，完成了嗎？"))
        self.assertEqual(self.results(ask), [
            (1, "完成了", ["kb agenda done a-5e6f7a8b"], False, []),
            (2, "不用了", ["kb agenda cancel a-5e6f7a8b"], True, []),
            (3, "還沒，改到某天", ["kb agenda edit a-5e6f7a8b --date {新日期}"], False, [NEW_DATE]),
            (4, "還沒，下次再問", [], False, [])])
        self.assertEqual(ask["results"][3]["note"], "這個對話不再問")

    def test_overdue_question_without_entities_and_with_two(self):
        put_agenda(self.V, agenda_item(entities=[]), agenda_item(id="a-00000002", entities=["王宅", "大安陳"], date="2020-02-03",
                                                              kind="promise", text="給報價"))
        asks = self.build()["asks"]
        self.assertEqual([a["question"] for a in asks], ["1/1 付第二期款過了，完成了嗎？", "2/3 給報價（王宅、大安陳）過了，完成了嗎？"])

    def test_overdue_only_tracked_open_past(self):
        put_agenda(self.V, agenda_item(kind="meeting"), agenda_item(id="a-2", kind="reminder"),
                   agenda_item(id="a-3", status="done"), agenda_item(id="a-4", kind="period", start="2019-01-01"),
                   agenda_item(id="a-5", date="2999-01-01"), agenda_item(id="a-6", kind="promise"))
        self.assertEqual([a["id"] for a in self.build()["asks"]], ["overdue:a-6"])

    def test_conflict_without_card_title(self):
        put_agenda(self.V, agenda_item(id="a-1b2c3d4e", text="交屋", date="2999-12-15", source="confirmed",
                                       proposal={"date": "2999-12-20", "text": "交屋", "card": "f-1", "fp": "a"}))
        ask = self.one()
        self.assertEqual((ask["id"], ask["kind"], ask["question"]),
                         ("conflict:a-1b2c3d4e", "agenda_conflict", "交屋你之前說 12/15，新版寫 12/20，要用哪個？"))
        self.assertEqual(self.results(ask), [
            (1, "用新版 12/20", ["kb agenda edit a-1b2c3d4e --date 2999-12-20"], False, []),
            (2, "維持 12/15", ["kb agenda recheck a-1b2c3d4e"], False, [])])

    def test_conflict_with_text_change(self):
        put_agenda(self.V, agenda_item(id="a-1b2c3d4e", text="交屋", date="2999-12-15", source="corrected",
                                       proposal={"date": "2999-12-20", "text": "交屋驗收", "card": "f-1", "fp": "a"}))
        ask = self.one()
        self.assertEqual(ask["results"][0]["do"], ["kb agenda edit a-1b2c3d4e --date 2999-12-20 --text 交屋驗收"])

    def test_conflict_text_only(self):
        put_agenda(self.V, agenda_item(id="a-1b2c3d4e", text="交屋", date="2999-12-15", source="confirmed",
                                       proposal={"date": "2999-12-15", "text": "交屋驗收", "card": "f-1", "fp": "a"}))
        ask = self.one()
        self.assertEqual(ask["question"], "交屋你之前說「交屋」，新版寫「交屋驗收」，要用哪個？")
        self.assertEqual(self.results(ask)[0][1:3], ("用新版「交屋驗收」", ["kb agenda edit a-1b2c3d4e --text 交屋驗收"]))
        self.assertEqual(ask["results"][1]["label"], "維持「交屋」")

    def test_conflict_text_with_space_is_quoted(self):
        put_agenda(self.V, agenda_item(id="a-1b2c3d4e", text="交屋", date="2999-12-15", source="confirmed",
                                       proposal={"date": "2999-12-20", "text": "交屋 驗收", "card": "f-1", "fp": "a"}))
        self.assertEqual(self.one()["results"][0]["do"], ['kb agenda edit a-1b2c3d4e --date 2999-12-20 --text "交屋 驗收"'])

    def test_conflict_ignores_closed_items(self):
        put_agenda(self.V, agenda_item(kind="meeting", status="done", proposal={"date": "2999-12-20", "text": "x", "card": "f-1", "fp": "a"}))
        self.assertEqual(self.build()["asks"], [])

    def test_close(self):
        put_entities(self.V)
        put_pending(self.V, close_item())
        ask = self.one()
        self.assertEqual((ask["id"], ask["kind"], ask["question"]),
                         ("close:p-9a8b7c6d", "close", "「王宅」看起來已經結束了，要記成結案嗎？（依據：f-3a553095）"))
        self.assertEqual(self.results(ask), [
            (1, "對，結案了", ["kb entity progress 王宅 結案 --evidence f-3a553095"], False, []),
            (2, "還沒", ["kb entity progress 王宅 進行中 --evidence f-3a553095 --note 客戶說還沒結案"], False, []),
            (3, "先暫停了", ["kb entity progress 王宅 擱置 --evidence f-3a553095"], False, [])])

    def test_close_two_evidence_and_name_with_space(self):
        E.put_entities(self.V, [E.entity("e-0f3e9a21", "陳 先生宅")])
        put_pending(self.V, close_item(file_ids=["f-1", "f-2"]))
        ask = self.one()
        self.assertEqual(ask["results"][0]["do"], ['kb entity progress "陳 先生宅" 結案 --evidence f-1 --evidence f-2'])

    def test_close_skips_answered_and_unknown_entity(self):
        put_entities(self.V)
        put_pending(self.V, close_item(status="done"), close_item(id="p-2", entity_ids=["e-ffffffff"]))
        self.assertEqual(self.build()["asks"], [])

    def test_order_and_numbering(self):
        put_entities(self.V)
        put_agenda(self.V, agenda_item(),
                   agenda_item(id="a-1b2c3d4e", kind="meeting", status="open", source="confirmed", date="2999-01-01",
                               proposal={"date": "2999-02-01", "text": "x", "card": "f-1", "fp": "a"}))
        put_pending(self.V, close_item())
        new = {"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q-name"}
        missing = {"id": "f-98c66aa7", "uri": "file://a/b.docx", "pending_id": "p-1a2b3c4d", "question": "Q-file"}
        got = self.build(synthesis_new=[new], missing=[missing], entity_drift=[drift("missing")])["asks"]
        self.assertEqual([a["id"] for a in got], ["drift:e-5c1d0a7e", "missing:p-1a2b3c4d", "overdue:a-5e6f7a8b",
                                                   "conflict:a-1b2c3d4e", "close:p-9a8b7c6d", "name:e-0f3e9a21"])
        self.assertEqual([r["n"] for a in got for r in a["results"]], list(range(1, 1 + sum(len(a["results"]) for a in got))))

    def test_broken_state_files_give_no_agenda_asks(self):
        (self.V / ".system" / "agenda.json").write_text("{bad", encoding="utf-8")
        (self.V / ".system" / "pending.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(self.build()["asks"], [])


class TellScope(Base):
    """D-141：tell 只帶這一回合做了什麼（kb asks 用）時，整理狀態那些句子不帶；預設照舊全帶。"""

    def setUp(self):
        super().setUp()
        self.t = turn.start(self.V, None, "")["turn"]
        self.kw = dict(unreadable=[{"id": "f-1", "uri": "file://a/甲.pdf", "reason": "有密碼"}],
                       skipped={"credential": 2, "placeholder": 1, "too_large": 0, "error": 0, "skipped": 3},
                       missing_types={"廠商": 4}, pdf_restored=2)
        put_agenda(self.V, agenda_item(text="交屋", date="2026-11-30", created_turn=self.t, origin={"card": "f", "fp": "a"}))

    def test_default_keeps_everything(self):
        got = self.build(**self.kw)["tell"]
        self.assertEqual(len(got), 7)
        self.assertEqual(got[-1], "加進日程：11/30 交屋")

    def test_actions_only_has_just_this_turns_sentences(self):
        out = plan.build(base(**self.kw), self.V, config, tell_all=False)
        self.assertEqual(out["tell"], ["加進日程：11/30 交屋"])

    def test_actions_only_keeps_plan_and_asks_the_same(self):
        full = plan.build(base(**self.kw, agenda_unscanned=[{"id": "f-1"}]), self.V, config)
        part = plan.build(base(**self.kw, agenda_unscanned=[{"id": "f-1"}]), self.V, config, tell_all=False)
        self.assertEqual((part["plan"], part["asks"]), (full["plan"], full["asks"]))


class AgendaTell(Base):
    def setUp(self):
        super().setUp()
        self.t = turn.start(self.V, None, "")["turn"]

    def tell(self):
        return self.build()["tell"]

    def added(self, n, **kw):
        return [agenda_item(id=f"a-{i:08x}", date=f"2026-10-{5 + i:02d}", text=f"事{i}", created_turn=self.t,
                            origin={"card": "f-1", "fp": "a"}, entities=[], **kw) for i in range(n)]

    def test_three_added_this_turn_in_one_sentence_sorted_by_date(self):
        items = [agenda_item(id="a-1", kind="meeting", text="開會", date="2026-10-05", created_turn=self.t, origin={"card": "f", "fp": "a"}),
                 agenda_item(id="a-3", text="交屋", date="2026-11-30", created_turn=self.t, origin={"card": "f", "fp": "a"}),
                 agenda_item(id="a-2", text="付第二期款", date="2026-10-20", created_turn=self.t, origin={"card": "f", "fp": "a"})]
        put_agenda(self.V, *items)
        self.assertEqual(self.tell(), ["加進日程：10/5 開會、10/20 付第二期款、11/30 交屋"])

    def test_period_shows_span(self):
        put_agenda(self.V, agenda_item(kind="period", start="2026-09-01", date="2026-11-30", text="施工", created_turn=self.t,
                                       origin={"card": "f", "fp": "a"}))
        self.assertEqual(self.tell(), ["加進日程：9/1～11/30 施工"])

    def test_five_listed_six_counted(self):
        put_agenda(self.V, *self.added(5))
        self.assertEqual(len(self.tell()[0].split("、")), 5)
        put_agenda(self.V, *self.added(6))
        self.assertEqual(self.tell(), ["加進日程 6 條，看 系統/日程.md"])

    def test_other_turns_and_client_said_and_cancelled_are_not_listed(self):
        put_agenda(self.V, agenda_item(created_turn="20200101-000000-old", origin={"card": "f", "fp": "a"}),
                   agenda_item(id="a-2", created_turn=self.t, origin=None, source="confirmed"),
                   agenda_item(id="a-3", created_turn=self.t, origin={"card": "f", "fp": "a"}, status="cancelled"))
        self.assertEqual(self.tell(), [])

    def test_reworked_inferred_item_this_turn(self):
        hist = [{"date": "2026-10-21", "change": "date 2026-11-30 → 2999-12-15", "by": "ai", "turn": self.t}]
        put_agenda(self.V, agenda_item(text="交屋", date="2999-12-15", history=hist, origin={"card": "f", "fp": "a"}))
        self.assertEqual(self.tell(), ["交屋從 11/30 改成 12/15 了"])

    def test_reworked_in_another_turn_or_corrected_is_not_told(self):
        old = [{"date": "2026-10-21", "change": "date 2026-11-30 → 2999-12-15", "by": "ai", "turn": "20200101-000000-old"}]
        mine = [{"date": "2026-10-21", "change": "date 2026-11-30 → 2999-12-15", "by": "ai", "turn": self.t}]
        put_agenda(self.V, agenda_item(history=old), agenda_item(id="a-2", history=mine, source="corrected"))
        self.assertEqual(self.tell(), [])

    def test_no_current_turn_is_quiet(self):
        (self.V / ".system" / "turns" / "current.json").unlink()
        put_agenda(self.V, *self.added(1))
        self.assertEqual(self.tell(), [])

    def test_agenda_lines_come_after_the_others(self):
        put_agenda(self.V, *self.added(1))
        got = self.build(pdf_restored=1)["tell"]
        self.assertEqual([x.split(" ")[0].split("：")[0] for x in got], ["PDF", "加進日程"])


class WithRealVault(unittest.TestCase):
    """真知識庫：依設定不整理帶種類；todo --json 帶三個鍵、既有鍵不變；asks 的每條 do 實際跑（驗收 6）。"""

    OLD_KEYS = {"new", "stale", "moved", "duplicates", "orphans", "unreadable", "missing", "skipped", "broken", "pdf_restored",
                "unlinked", "missing_types", "profile_missing", "profile", "synthesis_new", "synthesis_stale",
                "synthesis_broken", "entity_drift", "entities", "agenda_recheck", "agenda_unscanned", "agenda_counts",
                "unbacked", "purge_pending", "unlock_same_kind", "unlock_kinds"}  # 密碼與守門

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def todo(self):
        r = run_kb("cards", "todo", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def run_do(self, line, fills=None):
        for key, value in (fills or {}).items():
            line = line.replace("{" + key + "}", value)
        args = shlex.split(line)
        self.assertEqual(args[0], "kb", line)
        r = run_kb(*args[1:], cwd=self.V)
        self.assertEqual(r.returncode, 0, f"{line}\n{r.stdout}{r.stderr}")

    def result_of(self, ask_id, n_in_ask):
        ask = next(a for a in self.todo()["asks"] if a["id"].startswith(ask_id))
        return ask["results"][n_in_ask]

    def run_result(self, ask_id, n_in_ask, fills=None):
        result = self.result_of(ask_id, n_in_ask)
        names = [f["name"] for f in result["fill"]]
        self.assertEqual(sorted(fills or {}), sorted(names))
        for line in result["do"]:
            self.run_do(line, fills)

    def test_three_keys_and_old_keys_unchanged(self):
        data = self.todo()
        self.assertEqual(set(data), self.OLD_KEYS | {"plan", "asks", "tell", "announce"})
        self.assertEqual(set(data) - {"plan", "asks", "tell", "announce"}, self.OLD_KEYS)

    def test_skipped_by_setting_names_the_kind(self):
        ids = [i["id"] for i in self.todo()["new"]][:4]
        inventory.set_skip(self.V, ids, "請款")
        self.assertIn("請款類 4 份依你的設定不整理", self.todo()["tell"])

    # --- drift
    def test_folder_renamed_results_run(self):
        for n, fills in ((0, None), (1, None), (2, {"新名字": "王宅二"})):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                (self.V / "案子" / "信義王").rename(self.V / "案子" / "王宅邸")
                self.run_result("drift:", n, fills)

    def test_card_renamed_results_run(self):
        for n, fills in ((0, None), (1, None), (2, {"新名字": "王宅二"})):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                (self.V / "案子" / "信義王" / "信義王.md").rename(self.V / "案子" / "信義王" / "王宅邸.md")
                self.run_result("drift:", n, fills)

    def test_moved_type_results_run(self):
        for n in (0, 1):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                shutil.move(str(self.V / "案子" / "信義王"), str(self.V / "業主" / "信義王"))
                self.run_result("drift:", n)

    def test_drift_missing_results_run(self):
        for n in (0, 1, 2):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                shutil.rmtree(self.V / "案子" / "信義王")
                self.run_result("drift:", n)

    # --- 原檔不見
    def make_missing(self):
        rel = E.write_card(self.V, _cards.MEETING, "大安陳會議一", [])
        (self.raw / _cards.MEETING).unlink()
        _cards.scan_apply(self.V)
        return rel

    def test_file_missing_results_run(self):
        for n, fills in ((0, None), (1, {"客戶的話": "我放別的地方了"}), (2, None)):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                rel = self.make_missing()
                self.run_result("missing:", n, fills)
                if n == 0:
                    self.assertFalse((self.V / rel).exists())
                else:
                    self.assertTrue((self.V / rel).exists())
                    self.assertEqual([a for a in self.todo()["asks"] if a["id"].startswith("missing:")], [])

    # --- 名字確認
    def make_unconfirmed(self):
        E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        E.write_card(self.V, E.ESTIMATE, "王宅估價", ["王宅"])

    def test_name_results_run(self):
        for n, fills in ((0, None), (1, {"同一個": "信義王"}), (2, {"新名字": "王公館"})):
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                self.make_unconfirmed()
                self.run_result("name:", n, fills)
                self.assertEqual([a for a in self.todo()["asks"] if a["id"].startswith("name:")], [])


def put_card(V, cid="f-07df81a8", fp="b" * 64, title="合約", uri="file://報價與合約/合約.docx"):
    d = Path(V) / "檔案索引" / "報價與合約"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{title}-{cid[2:]}.md").write_text(
        f"---\n編號: {cid}\n原檔:\n  - {uri}\n指紋: {fp}\n標題: {title}\n---\n# {title}\n", encoding="utf-8")


def ruling(**kw):
    base = {"id": "r-1a2b3c4d", "kind": "ruled_out", "entities": [], "cards": [{"id": "f-07df81a8", "fp": "a" * 12}],
            "text": "合約.docx 是舊的，別信", "date": "2026-10-21", "turn": "t", "status": "active", "revoked": None}
    base.update(kw)
    return base


def put_rulings(V, *items):
    (Path(V) / ".system" / "rulings.json").write_text(
        json.dumps({"schema_version": 1, "rulings": list(items)}, ensure_ascii=False), encoding="utf-8")


class RulingAsks(Base):
    """note 改知識第 7 點、驗收 11：掛卡的裁示遇到卡指紋變了問「現在可以信了嗎」；排在原檔不見之後、逾期之前。"""

    def results(self, ask):
        return [(r["n"], r["label"], r["do"], r["strict"], r["fill"]) for r in ask["results"]]

    def test_ruled_out(self):
        put_card(self.V)
        put_rulings(self.V, ruling())
        (ask,) = self.build()["asks"]
        self.assertEqual((ask["id"], ask["kind"], ask["question"]),
                         ("ruling:r-1a2b3c4d", "ruling_recheck", "「合約.docx」改過了，你之前說別信，現在可以信了嗎？"))
        self.assertEqual(self.results(ask), [(1, "可以了", ["kb ruling remove r-1a2b3c4d"], False, []),
                                             (2, "還是別信", ["kb ruling recheck r-1a2b3c4d"], False, [])])

    def test_spoken_wins_on_a_card(self):
        put_card(self.V, title="報價", uri="file://客戶案件/信義王/報價.pdf")
        put_rulings(self.V, ruling(kind="spoken_wins", text="這份報價以口述為準"))
        (ask,) = self.build()["asks"]
        self.assertEqual(ask["question"], "「報價.pdf」改過了，你之前說以你說的為準，現在還是嗎？")
        self.assertEqual([(r["label"], r["do"]) for r in ask["results"]],
                         [("改用新版", ["kb ruling remove r-1a2b3c4d"]), ("還是以我說的為準", ["kb ruling recheck r-1a2b3c4d"])])

    def test_no_ask_when_unchanged_revoked_or_card_gone(self):
        put_card(self.V)
        put_rulings(self.V, ruling(cards=[{"id": "f-07df81a8", "fp": "b" * 12}]),
                    ruling(id="r-2", status="revoked"), ruling(id="r-3", cards=[{"id": "f-99999999", "fp": "a" * 12}]))
        self.assertEqual(self.build()["asks"], [])

    def test_broken_rulings_file_gives_no_ask(self):
        (self.V / ".system" / "rulings.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(self.build()["asks"], [])

    def test_order_after_missing_before_overdue(self):
        put_card(self.V)
        put_rulings(self.V, ruling())
        put_agenda(self.V, agenda_item())
        missing = {"id": "f-98c66aa7", "uri": "file://a/b.docx", "pending_id": "p-1a2b3c4d", "question": "Q-file"}
        got = self.build(missing=[missing])["asks"]
        self.assertEqual([a["id"] for a in got], ["missing:p-1a2b3c4d", "ruling:r-1a2b3c4d", "overdue:a-5e6f7a8b"])
        self.assertEqual([r["n"] for a in got for r in a["results"]], list(range(1, 1 + sum(len(a["results"]) for a in got))))


class ProduceAsks(Base):
    """produce 第 5 點：asks 多 draft（AI 產出裡的（草稿）.md，這一回合才寫的不問），排在 close 之後、名字確認之前；
    tell 多 unbacked 那句。"""

    def draft(self, name="2026-10-21 內湖李 進度報告（草稿）.md", sub="報告"):
        d = self.V / "AI 產出" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("草稿\n", encoding="utf-8")
        return f"AI 產出/{sub}/{name}"

    def test_draft_ask(self):
        rel = self.draft()
        (ask,) = self.build()["asks"]
        self.assertEqual((ask["id"], ask["kind"], ask["question"]),
                         (f"draft:{rel}", "draft", "內湖李 進度報告 的草稿還在等你看"))
        self.assertEqual([(r["n"], r["label"], r["do"], r["strict"]) for r in ask["results"]],
                         [(1, "定稿", [], False), (2, "再改", [], False),
                          (3, "不要了", [f'kb output drop-draft "{rel}"'], True)])

    def test_draft_written_this_turn_not_asked(self):
        t = turn.start(self.V, "sid", "寫草稿")["turn"]
        rel = "AI 產出/報告/x（草稿）.md"
        turn.backup(self.V, rel, t)
        self.draft("x（草稿）.md")
        self.assertEqual(self.build()["asks"], [])

    def test_order_after_close_before_name(self):
        put_entities(self.V)
        put_pending(self.V, close_item())
        self.draft()
        new = {"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q-name"}
        got = self.build(synthesis_new=[new])["asks"]
        self.assertEqual([a["kind"] for a in got], ["close", "draft", "name"])

    def test_unbacked_tell(self):
        self.assertIn("AI 產出有 2 份沒經程式放進來，這幾份退回拿不掉", self.build(unbacked=2)["tell"])
        self.assertFalse(any("沒經程式" in x for x in self.build(unbacked=0)["tell"]))


if __name__ == "__main__":
    unittest.main()
