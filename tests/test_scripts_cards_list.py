"""kb cards list（answer 第 2 點，驗收 3、4、14）：卡型只照字面篩、年照文件日期、實體（別名也認）、來源；types 列實際有的卡型與
in_profile（設定檔第二節沒有的 false）；undated 數對；ruled_out 標出來；人讀不印編號；只讀。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_lib_lookup import ID, tree_hash, write_card
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import rulings, turn  # noqa: E402


class CardsList(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]
        self.quote, _ = write_card(self.V, E.QUOTE, "信義王報價第二版", ["信義王"], kind="報價", date="2026-10-01")
        self.est, _ = write_card(self.V, E.ESTIMATE, "內湖李估價單", [], kind="估價單", date="2026-03-02")
        self.old, _ = write_card(self.V, E.TEMPLATE, "舊報價", ["大安陳"], kind="報價", date="2025-12-30")
        self.undated, _ = write_card(self.V, E.MEMO, "沒日期的報價", ["信義王"], kind="報價", date="未載")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def listing(self, *args, code=0):
        r = run_kb("cards", "list", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout

    def json(self, *args):
        return json.loads(self.listing(*args, "--json"))

    def ids(self, got):
        return [c["id"] for c in got["cards"]]

    def test_all_newest_first_with_types(self):
        got = self.json()
        self.assertEqual(self.ids(got), [self.quote, self.est, self.old, self.undated])
        self.assertEqual(got["types"], [{"card_type": "報價", "count": 3, "in_profile": True},
                                        {"card_type": "估價單", "count": 1, "in_profile": False}])
        self.assertEqual(got["undated"], 1)
        card = got["cards"][0]
        self.assertEqual({k: card[k] for k in ("title", "card_type", "date", "entities", "read", "stale", "ruled_out")},
                         {"title": "信義王報價第二版", "card_type": "報價", "date": "2026-10-01", "entities": ["信義王"],
                          "read": "完整", "stale": False, "ruled_out": False})
        self.assertTrue(card["path"].startswith("檔案索引/"))

    def test_type_is_literal_and_year_counts_undated(self):
        got = self.json("--type", "報價", "--year", "2026")
        self.assertEqual(self.ids(got), [self.quote])
        self.assertEqual(got["undated"], 1)
        self.assertEqual({t["card_type"]: t["in_profile"] for t in got["types"]}, {"報價": True, "估價單": False})
        both = self.json("--type", "報價", "--type", "估價單", "--year", "2026")
        self.assertEqual(self.ids(both), [self.quote, self.est])

    def test_entity_and_source(self):
        self.assertEqual(self.ids(self.json("--entity", "信義王宅")), [self.quote, self.undated])
        self.assertEqual(self.ids(self.json("--entity", "信義王", "--entity", "大安陳")), [self.quote, self.old, self.undated])
        # 信義王的報價在兩個來源各有一份（同一份內容兩個位置），哪個位置在這個來源都算
        self.assertEqual(self.ids(self.json("--source", "報價與合約")), [self.quote, self.old])
        self.assertEqual(self.ids(self.json("--source", "公司行政")), [self.undated])
        self.assertEqual(self.listing("--entity", "沒登記", code=3).strip(), "實體清單沒有「沒登記」，先 kb entity add")

    def test_ruled_out(self):
        rulings.add(self.V, "不採用", [], [self.old], "舊報價別信", self.t)
        got = {c["id"]: c["ruled_out"] for c in self.json()["cards"]}
        self.assertEqual(got, {self.quote: False, self.est: False, self.old: True, self.undated: False})

    def test_human_no_ids_and_read_only(self):
        rulings.add(self.V, "不採用", [], [self.old], "舊報價別信", self.t)
        before = tree_hash(self.V)
        out = self.listing("--year", "2026")
        self.assertEqual(tree_hash(self.V), before)
        lines = out.splitlines()
        self.assertEqual(lines[0], "共 2 張卡；庫裡的卡型：報價 2、估價單 1（設定檔沒有）")
        self.assertEqual(lines[1], "沒寫日期的 1 張，沒算進這一年")
        self.assertIn("- 2026-03-02 內湖李估價單（估價單）", lines)
        self.assertIsNone(ID.search(out))
        self.assertIsNone(re.search(r"檔案索引/", out))

    def test_bad_year(self):
        r = run_kb("cards", "list", "--year", "26", cwd=self.V)
        self.assertEqual(r.returncode, 2)


    def test_verify_max_in_json(self):
        self.assertEqual(self.json()["verify_max"], 20)
        (self.V / ".system" / "config.json").write_text(json.dumps({"answer_verify_max": 5}), encoding="utf-8")
        self.assertEqual(self.json()["verify_max"], 5)

    def test_scan_groups_checks_deadline(self):
        """裁定 17（F-5）：時限檢查放進 _scan_groups（讀每張卡之前）。"""
        import time
        from kb._lib import cards
        with self.assertRaises(TimeoutError):
            cards._scan_groups(self.V, deadline=time.monotonic() - 1)
        self.assertTrue(cards._scan_groups(self.V)[0])


if __name__ == "__main__":
    unittest.main()
