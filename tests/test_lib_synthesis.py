"""_lib/synthesis（實體與綜合卡第 2 點，D-114～D-121）：validate 每條錯各一、句子一字不差；render_cache 只改兩欄、
內容沒變不寫、備份帶 by program；buckets 四桶各一、門檻邊界 1 與 2 張、客戶自己不列、型別表沒有的不列。"""
import shutil
import unittest
from pathlib import Path

from tests import _entities as E
from kb._lib import cards, config, entities, synthesis, turn  # noqa: E402

XINYI_REL = "案子/信義王/信義王.md"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entity(self, eid):
        return next(e for e in entities.load(self.V)["entities"] if e["id"] == eid)

    def update(self, eid, **kw):
        data = entities.load(self.V)["entities"]
        for e in data:
            if e["id"] == eid:
                e.update(kw)
        E.put_entities(self.V, data)

    def buckets(self):
        data = entities.load(self.V)
        return synthesis.buckets(self.V, data, entities.mounts(self.V, data), entities._profile(self.V))

    def header(self, eid):
        data = entities.load(self.V)
        mounts = entities.mounts(self.V, data)
        e = self.entity(eid)
        return synthesis.header_for(self.V, e, mounts.get(e["name"], []), "2026-10-20")


class Header(Base):
    def test_header_shape(self):
        E.write_card(self.V, E.QUOTE, "信義王報價第二版", ["信義王", "王宅"], date="2026-10-01")
        E.write_card(self.V, E.ESTIMATE, "信義王估價", ["信義王"], date="2026-09-12")
        front = cards.parse_front(self.header(E.XINYI))
        self.assertEqual(list(front), list(synthesis.KEYS))
        self.assertEqual((front["編號"], front["型別"], front["大類"], front["名稱"], front["別名"], front["範本版本"],
                          front["更新日"]), (E.XINYI, "案子", "事", "信義王", ["信義王宅"], "1", "2026-10-20"))
        self.assertEqual([x.split(" ", 2)[2] for x in front["檔案卡"]], ["信義王報價第二版", "信義王估價"])
        self.assertTrue(all(len(x.split(" ")[1]) == 12 for x in front["整理到"]))
        self.assertEqual(front["整理到"], sorted(front["整理到"]))
        self.assertEqual(front["相關實體"], ["王宅"])

    def test_sections_for(self):
        got = synthesis.sections_for(entities._profile(self.V), "案子")
        self.assertEqual([s["name"] for s in got], ["現況", "紀事", "推算", "時程", "金額"])
        self.assertEqual([s["name"] for s in synthesis.sections_for(entities._profile(self.V), "不在表")],
                         ["現況", "紀事", "推算"])


class Validate(Base):
    def write(self, text, rel=XINYI_REL):
        p = self.V / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return synthesis.validate(self.V, rel)

    def good(self):
        return E.synthesis_text(self.header(E.XINYI), "信義王")

    def test_good_ok(self):
        self.assertEqual(self.write(self.good()), (True, ""))

    def test_each_error_sentence(self):
        good = self.good()
        E.write_card(self.V, E.ESTIMATE, "信義王估價", ["信義王"])
        stale = good  # 整理到是空的，現在掛了一張
        cases = [
            ("---\n編號: e-5c1d0a7e\n", XINYI_REL, "檔頭解析不了：少了結尾的 ---"),
            (good.replace("更新日: 2026-10-20\n", ""), XINYI_REL, "檔頭少了「更新日」"),
            (good.replace(E.XINYI, "e-99999999"), XINYI_REL, "編號 e-99999999 不在實體清單"),
            (good.replace(E.XINYI, E.SELF), XINYI_REL, "這是你自己（關於你），不寫綜合卡"),
            (good.replace(E.XINYI, E.WANG), XINYI_REL, "還沒跟客戶確認名稱，先問再寫"),
            (good, "案子/王宅/王宅.md", "綜合卡該放在 案子/信義王/信義王.md"),
            (good.replace("名稱: 信義王", "名稱: 信義"), XINYI_REL, "「名稱」照 kb cards todo 給的檔頭抄"),
            (good.replace("範本版本: 1", "範本版本: 2"), XINYI_REL, "範本版本該是 1"),
            (stale, XINYI_REL, "整理到不是現行的，重跑 kb cards todo 拿新檔頭"),
        ]
        for text, rel, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.write(text, rel), (False, want))

    def test_more_error_sentences(self):
        good = self.good()
        cases = [
            (good.replace("更新日: 2026-10-20", "更新日: 10/20"), "更新日要寫 YYYY-MM-DD"),
            (good.replace("## 現況", "## 近況"), "正文要有 # 信義王 與 ## 現況"),
            (good.replace("# 信義王\n", "# 信義王宅\n"), "正文要有 # 信義王 與 ## 現況"),
        ]
        for text, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.write(text), (False, want))
        self.update(E.XINYI, status="removed")
        self.assertEqual(self.write(good), (False, "這個實體已移除"))

    def test_list_value_where_string_expected_is_not_crash_free_pass(self):
        ok, reason = self.write(self.good().replace("編號: e-5c1d0a7e", "編號: [e-5c1d0a7e]"))
        self.assertFalse(ok)
        self.assertIn("不在實體清單", reason)


class RenderCache(Base):
    def test_only_two_fields_change_and_program_backup(self):
        card = self.V / XINYI_REL
        before = card.read_text(encoding="utf-8").splitlines()
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "王宅"], date="2026-10-01")
        self.assertEqual(synthesis.render_cache(self.V, ["信義王"], self.t), [XINYI_REL])
        after = card.read_text(encoding="utf-8").splitlines()
        front = cards.parse_front("\n".join(after))
        self.assertEqual([x.split(" ", 2)[2] for x in front["檔案卡"]], ["信義王報價"])
        self.assertEqual(front["相關實體"], ["王宅"])
        unchanged = [ln for ln in before if not ln.startswith(("檔案卡:", "相關實體:"))]
        self.assertEqual([ln for ln in after if not ln.startswith(("檔案卡:", "相關實體:", "  - "))], unchanged)
        rows = {r["path"]: r for r in turn.entries(self.V, self.t)}
        self.assertEqual(rows[XINYI_REL]["by"], "program")
        self.assertEqual(turn.modified_synthesis_count(self.V, self.t), 0)

    def test_unchanged_not_written(self):
        card = self.V / XINYI_REL
        mtime = card.stat().st_mtime_ns
        self.assertEqual(synthesis.render_cache(self.V, None, self.t), [])
        self.assertEqual(card.stat().st_mtime_ns, mtime)
        self.assertEqual(turn.entries(self.V, self.t), [])

    def test_self_not_in_related(self):
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "林小姐室內設計"])
        synthesis.render_cache(self.V, ["信義王"], self.t)
        self.assertEqual(cards.parse_front((self.V / XINYI_REL).read_text(encoding="utf-8"))["相關實體"], [])

    def test_broken_card_left_alone(self):
        card = self.V / XINYI_REL
        card.write_text(card.read_text(encoding="utf-8").replace("範本版本: 1", "範本版本: 9"), encoding="utf-8")
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        self.assertEqual(synthesis.render_cache(self.V, ["信義王"], self.t), [])


class Buckets(Base):
    def test_threshold_one_and_two_and_confirm(self):
        self.update(E.WANG, source="inferred")
        E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        self.assertEqual(self.buckets()["synthesis_new"], [])
        E.write_card(self.V, E.ESTIMATE, "王宅估價", ["王宅"])
        (item,) = self.buckets()["synthesis_new"]
        self.assertEqual((item["entity_id"], item["name"], item["type"], item["category"], item["confirmed"], item["path"]),
                         (E.WANG, "王宅", "案子", "事", False, "案子/王宅/王宅.md"))
        self.assertEqual(item["question"], "我要幫「王宅」（案子，掛了 2 份檔）整理一頁現況：名字這樣叫對嗎？它跟其他案子是同一件嗎？")
        self.assertEqual(len(item["cards"]), 2)
        self.assertEqual([s["name"] for s in item["sections"]], ["現況", "紀事", "推算", "時程", "金額"])
        self.assertTrue(item["header"].startswith("---\n編號: e-0f3e9a21\n"))
        self.update(E.WANG, source="confirmed")
        (item,) = self.buckets()["synthesis_new"]
        self.assertTrue(item["confirmed"])
        self.assertNotIn("question", item)
        (self.V / ".system" / "config.json").write_text('{"synthesis_min_cards": 3}', encoding="utf-8")
        self.assertEqual(self.buckets()["synthesis_new"], [])

    def test_self_and_type_not_in_table_not_listed(self):
        data = entities.load(self.V)["entities"]
        data.append(E.entity("e-22222222", "順發水電", type_="廠商"))
        E.put_entities(self.V, data)
        for rel, title in ((E.QUOTE, "a"), (E.ESTIMATE, "b"), (E.DRAFT, "c")):
            E.write_card(self.V, rel, title, ["林小姐室內設計", "順發水電"])
        self.assertEqual(self.buckets()["synthesis_new"], [])

    def test_stale_added_changed_removed(self):
        a = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        E.write_card(self.V, E.ESTIMATE, "信義王估價", ["信義王"])
        card = self.V / XINYI_REL
        card.write_text(E.synthesis_text(self.header(E.XINYI), "信義王"), encoding="utf-8")
        self.assertEqual(self.buckets()["synthesis_stale"], [])
        E.write_card(self.V, E.DRAFT, "信義王會議", ["信義王"])
        (item,) = self.buckets()["synthesis_stale"]
        self.assertEqual([x["title"] for x in item["added"]], ["信義王會議"])
        self.assertEqual((item["changed"], item["removed"]), ([], []))
        p = self.V / a
        p.write_text(p.read_text(encoding="utf-8").replace("實體: [信義王]", "實體: []"), encoding="utf-8")
        (item,) = self.buckets()["synthesis_stale"]
        self.assertEqual([(x["id"], x["title"]) for x in item["removed"]],
                         [(cards.parse_front(p.read_text(encoding="utf-8"))["編號"], "信義王報價")])
        card.write_text(E.synthesis_text(item["header"], "信義王"), encoding="utf-8")
        self.assertEqual(self.buckets()["synthesis_stale"], [])

    def test_stale_changed_when_fingerprint_moves(self):
        a = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        (self.V / XINYI_REL).write_text(E.synthesis_text(self.header(E.XINYI), "信義王"), encoding="utf-8")
        p = self.V / a
        front = cards.parse_front(p.read_text(encoding="utf-8"))
        p.write_text(p.read_text(encoding="utf-8").replace(front["指紋"], "a" * 64), encoding="utf-8")
        (item,) = self.buckets()["synthesis_stale"]
        self.assertEqual([x["id"] for x in item["changed"]], [front["編號"]])

    def test_broken_and_misplaced(self):
        card = self.V / XINYI_REL
        card.write_text(card.read_text(encoding="utf-8").replace("範本版本: 1", "範本版本: 9"), encoding="utf-8")
        stray = self.V / "案子" / "王宅" / "王宅.md"
        stray.parent.mkdir()
        stray.write_text((E.FIX / "大安陳.md").read_text(encoding="utf-8"), encoding="utf-8")
        got = self.buckets()["synthesis_broken"]
        self.assertEqual(got, [{"path": XINYI_REL, "reason": "範本版本該是 1"},
                               {"path": "案子/王宅/王宅.md", "reason": "綜合卡該放在 案子/大安陳/大安陳.md"}])

    def test_drift_with_question(self):
        (self.V / "案子" / "信義王").rename(self.V / "案子" / "王宅邸")
        (item,) = self.buckets()["entity_drift"]
        self.assertEqual(item["question"], "「信義王」的資料夾改成「王宅邸」了？要跟著改名，還是改回來？")
        self.assertEqual(self.buckets()["synthesis_broken"], [], "drift 認出的不重複列")
        (self.V / "案子" / "王宅邸" / "信義王.md").rename(self.V / "案子" / "王宅邸" / "王宅邸.md")
        (self.V / "案子" / "王宅邸").rename(self.V / "案子" / "信義王")
        self.assertEqual(self.buckets()["entity_drift"][0]["question"],
                         "「信義王」的綜合卡檔名改成「王宅邸」了？要採新名，還是改回來？")
        shutil.move(str(self.V / "案子" / "信義王"), str(self.V / "業主" / "信義王"))
        self.assertEqual(self.buckets()["entity_drift"][0]["question"],
                         "「信義王」的資料夾搬到「業主」底下了？要跟著改，還是搬回「案子」？")
        shutil.rmtree(self.V / "業主" / "信義王")
        self.assertEqual(self.buckets()["entity_drift"][0]["question"],
                         "「信義王」的資料夾（或綜合卡）不見了：是不要了，還是刪錯了？")

    def test_min_cards_default(self):
        self.assertEqual(config.get(self.V, "synthesis_min_cards"), 2)


if __name__ == "__main__":
    unittest.main()
