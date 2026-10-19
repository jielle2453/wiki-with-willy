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
from kb._lib import config, inventory, plan  # noqa: E402
from kb._lib.paths import plugin_root  # noqa: E402

TYPES = [{"name": "案子", "category": "matter"}, {"name": "業主", "category": "party"}]
SYNTHESIS_RULES = str(plugin_root() / "rules" / "synthesis.md")
FREE_TEXT = {"name": "客戶的話", "rule": "一句白話，不含 \" $ ` \\ 這四種字元"}


def base(**kw):
    out = {"new": [], "stale": [], "moved": [], "duplicates": [], "orphans": [], "unreadable": [], "missing": [],
           "skipped": {"credential": 0, "placeholder": 0, "too_large": 0, "error": 0, "skipped": 0}, "broken": [],
           "pdf_restored": 0, "unlinked": [], "missing_types": {}, "profile_missing": False,
           "profile": {"types": TYPES}, "synthesis_new": [], "synthesis_stale": [], "synthesis_broken": [],
           "entity_drift": [], "entities": []}
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
        got = self.build(new=work(2), synthesis_stale=[{"entity_id": "e-1", "name": "信義王"}])["plan"]
        self.assertEqual(got, [
            {"step": "cards", "items": 2, "do": "照清單寫卡（new、stale、broken）", "read": "references/carding.md 一～六節"},
            {"step": "synthesis_stale", "items": 1, "do": "照清單改綜合卡（synthesis_stale）",
             "read": SYNTHESIS_RULES}])

    def test_cards_count_new_stale_and_broken_with_header(self):
        broken = [{"path": "a", "reason": "x", "id": "f-1", "header": "h"}, {"path": "b", "reason": "y"}]
        self.assertEqual(self.steps(new=work(1), stale=work(2), broken=broken), [("cards", 4)])

    def test_broken_without_header_is_not_a_step(self):
        self.assertEqual(self.steps(broken=[{"path": "b", "reason": "y"}]), [])

    def test_unlinked_and_synthesis_broken_read_sections(self):
        got = self.build(unlinked=[{"id": "f-1"}], synthesis_broken=[{"path": "p", "reason": "r"}])["plan"]
        self.assertEqual([(s["step"], s["read"]) for s in got], [
            ("unlinked", "references/carding.md 第七節"), ("synthesis_broken", "references/carding.md 第八節")])

    def test_synthesis_new_counts_confirmed_only(self):
        new = [{"entity_id": "e-1", "name": "甲", "confirmed": True}, {"entity_id": "e-2", "name": "乙", "confirmed": False, "question": "Q"}]
        got = self.build(synthesis_new=new)["plan"]
        self.assertEqual([(s["step"], s["items"], s["read"]) for s in got],
                         [("synthesis_new", 1, SYNTHESIS_RULES)])
        self.assertEqual(self.steps(synthesis_new=new[1:]), [])

    def test_full_order(self):
        got = self.steps(moved=[{"id": "f-1"}], new=work(1), unlinked=[{"id": "f-2"}], synthesis_broken=[{"path": "p"}],
                         synthesis_stale=[{"entity_id": "e"}], synthesis_new=[{"entity_id": "e", "confirmed": True}])
        self.assertEqual([s for s, _n in got],
                         ["sync", "cards", "unlinked", "synthesis_broken", "synthesis_stale", "synthesis_new"])

    def test_pure(self):
        out = base(new=work(2))
        before = json.dumps(out, sort_keys=True)
        plan.build(out, self.V, config)
        self.assertEqual(json.dumps(out, sort_keys=True), before)


class Batch(Base):
    def test_over_max_asks_first_and_keeps_only_sync(self):
        self.set_max(1)
        got = self.build(new=work(2), moved=[{"id": "f-1"}], unlinked=[], synthesis_stale=[{"entity_id": "e"}])
        self.assertEqual([s["step"] for s in got["plan"]], ["sync"])
        ask = got["asks"][0]
        self.assertEqual((ask["id"], ask["kind"]), ("batch", "batch"))
        self.assertEqual(ask["question"], "有 2 份要整理，現在做嗎？大約一兩分鐘")
        self.assertEqual([(r["n"], r["label"], r["do"], r["strict"], r["fill"]) for r in ask["results"]],
                         [(1, "現在做", [], False, []), (2, "之後再說", [], False, [])])
        self.assertEqual(ask["results"][1]["note"], "這個對話不再問")

    def test_now_carries_the_full_plan(self):
        self.set_max(1)
        got = self.build(new=work(2), moved=[{"id": "f-1"}], synthesis_stale=[{"entity_id": "e"}])
        now = got["asks"][0]["results"][0]
        self.assertEqual([s["step"] for s in now["then"]], ["sync", "cards", "synthesis_stale"])

    def test_then_equals_the_unclipped_plan(self):
        """裁定 6：batch 的「現在做」帶的 then，就是沒超過上限時算出來的那份 plan。"""
        kw = dict(new=work(2), moved=[{"id": "f-1"}], unlinked=[{"id": "f-2"}], synthesis_stale=[{"entity_id": "e"}],
                  synthesis_new=[{"entity_id": "e", "name": "甲", "confirmed": True}])
        self.set_max(1)
        clipped = self.build(**kw)
        self.set_max(100)
        unclipped = self.build(**kw)
        self.assertEqual(unclipped["asks"], [])
        self.assertEqual([s["step"] for s in unclipped["plan"]],
                         ["sync", "cards", "unlinked", "synthesis_stale", "synthesis_new"])
        self.assertEqual(clipped["asks"][0]["results"][0]["then"], unclipped["plan"])
        self.assertEqual(clipped["plan"], unclipped["plan"][:1])

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
        self.assertEqual([s["step"] for s in got["plan"]], ["cards"])

    def test_minutes_round_up(self):
        self.set_max(1)
        self.assertEqual(self.build(new=work(7))["asks"][0]["question"], "有 7 份要整理，現在做嗎？大約 2 分鐘")
        self.assertEqual(self.build(new=work(6))["asks"][0]["question"], "有 6 份要整理，現在做嗎？大約 1 分鐘")

    def test_default_max_is_five(self):
        self.assertEqual(self.build(new=work(5))["asks"], [])
        self.assertEqual(self.build(new=work(6))["asks"][0]["id"], "batch")


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
            (3, "改成別的名字", ["kb entity rename 信義王 --to {新名字}"], False, ["新名字"])])

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
            (3, "改成別的名字", ["kb entity rename 信義王 --to {新名字}"], False, ["新名字"])])

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
            (2, "跟已有的另一個是同一個", ["kb entity merge 王宅 --into {同一個}"], False, ["同一個"]),
            (3, "名字改成別的", ["kb entity rename 王宅 --to {新名字}", "kb entity confirm {新名字}"], False, ["新名字"])])

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

    def test_free_text_fill_carries_a_rule(self):
        """裁定 5：自由文字的空格是 {"name","rule"}，其他空格是名稱字串；do 裡每個 {空格} 都在 fill 裡。"""
        asks = self.build(
            missing=[{"id": "f-98c66aa7", "uri": "file://a/b.docx", "pending_id": "p-1a2b3c4d", "question": "Q"}],
            entity_drift=[drift("folder_renamed", found="案子/王宅/信義王.md")],
            synthesis_new=[{"entity_id": "e-0f3e9a21", "name": "王宅", "confirmed": False, "question": "Q"}])["asks"]
        free = []
        for ask in asks:
            for r in ask["results"]:
                names = [f["name"] if isinstance(f, dict) else f for f in r["fill"]]
                slots = sorted(set(re.findall(r"\{([^{}]+)\}", " ".join(r["do"]))))
                self.assertEqual(sorted(names), slots, r["label"])
                free += [f for f in r["fill"] if isinstance(f, dict)]
        self.assertEqual(free, [FREE_TEXT])
        for ch in ('"', "$", "`", "\\"):
            self.assertIn(ch, FREE_TEXT["rule"])

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
        self.assertEqual(got, ["合約_加密.pdf 讀不到：有密碼；另存一份沒密碼的，或跟我說先跳過；記在 系統/待處理.md"])

    def test_unreadable_two(self):
        got = self.tell(unreadable=[{"id": "f-1", "uri": "file://a/甲.pdf", "reason": "有密碼"},
                                    {"id": "f-2", "uri": "file://a/乙.doc", "reason": "舊格式"}])
        self.assertEqual(got, ["甲.pdf 讀不到：有密碼；另存一份沒密碼的，或跟我說先跳過",
                               "乙.doc 讀不到：舊格式；用 Word／Excel 另存成新格式就讀得到；記在 系統/待處理.md"])

    def test_unreadable_five_listed_six_merged(self):
        items = [{"id": f"f-{i}", "uri": f"file://a/{i}.pdf", "reason": "有密碼"} for i in range(6)]
        self.assertEqual(len(self.tell(unreadable=items[:5])), 5)
        got = self.tell(unreadable=items)
        self.assertEqual(got, [f"{i}.pdf 讀不到：有密碼；另存一份沒密碼的，或跟我說先跳過" for i in range(5)]
                         + ["另有 1 份讀不到；記在 系統/待處理.md"])

    ADVICE = {
        "有密碼": "另存一份沒密碼的，或跟我說先跳過",
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
        self.assertEqual(got, ["不製卡：檔名像密碼或金鑰 2、太大 1"])

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


class WithRealVault(unittest.TestCase):
    """真知識庫：依設定不整理帶種類；todo --json 帶三個鍵、既有鍵不變；asks 的每條 do 實際跑（驗收 6）。"""

    OLD_KEYS = {"new", "stale", "moved", "duplicates", "orphans", "unreadable", "missing", "skipped", "broken", "pdf_restored",
                "unlinked", "missing_types", "profile_missing", "profile", "synthesis_new", "synthesis_stale",
                "synthesis_broken", "entity_drift", "entities"}

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
        names = [f["name"] if isinstance(f, dict) else f for f in result["fill"]]
        self.assertEqual(sorted(fills or {}), sorted(names))
        for line in result["do"]:
            self.run_do(line, fills)

    def test_three_keys_and_old_keys_unchanged(self):
        data = self.todo()
        self.assertEqual(set(data), self.OLD_KEYS | {"plan", "asks", "tell"})
        self.assertEqual(set(data) - {"plan", "asks", "tell"}, self.OLD_KEYS)

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


if __name__ == "__main__":
    unittest.main()
