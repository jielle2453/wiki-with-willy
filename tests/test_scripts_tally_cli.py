"""kb tally 黑箱（answer 第 5 點，驗收 8、14）：千分位、小數、負號、「萬」；拒絕一字不差（exit 3）；合計與平均；
表格照格式、人讀不印編號；只讀。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_lib_lookup import ID, tree_hash, write_card
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards  # noqa: E402
from kb import tally_cli  # noqa: E402
from decimal import Decimal


class Parse(unittest.TestCase):
    def test_formats(self):
        for text, want in (("1,280,000", "1280000"), ("54萬", "540000"), ("1.5萬", "15000"), ("-3.5", "-3.5"),
                           ("820", "820"), ("12,345.67", "12345.67")):
            with self.subTest(text=text):
                self.assertEqual(tally_cli.parse_number(text), Decimal(want))
        for text in ("約一百萬", "1,28,000", "12萬元", "", "1.2.3", "$100", "１００"):
            with self.subTest(text=text), self.assertRaises(tally_cli.TallyRefused) as cm:
                tally_cli.parse_number(text)
            self.assertEqual(str(cm.exception), f"看不懂這個數字：{text}")

    def test_show(self):
        self.assertEqual([tally_cli.show(Decimal(x)) for x in ("1820000", "910000.50", "-1234.5", "0.25")],
                         ["1,820,000", "910,000.5", "-1,234.5", "0.25"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.quote, _ = write_card(self.V, E.QUOTE, "大安陳報價表", ["大安陳"])
        self.contract, _ = write_card(self.V, E.CONTRACT, "信義王報價第一版", ["信義王"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tally(self, *rows, code=0, as_json=False):
        args = [x for r in rows for x in ("--row", r)] + (["--json"] if as_json else [])
        r = run_kb("tally", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return json.loads(r.stdout) if as_json else r.stdout

    def test_table_total_average_no_ids(self):
        before = tree_hash(self.V)
        out = self.tally(f"{self.quote} 1,280,000 第 3 頁", f"{self.contract} 54萬 工作表「總表」第 12 列")
        self.assertEqual(out.splitlines(), [
            "| 卡 | 數字 | 位置 |", "|---|---|---|",
            "| 大安陳報價表 | 1,280,000 | 第 3 頁 |",
            "| 信義王報價第一版 | 540,000 | 工作表「總表」第 12 列 |",
            "共 2 筆，合計 1,820,000，平均 910,000"])
        self.assertIsNone(re.search(ID, out))
        self.assertEqual(tree_hash(self.V), before)

    def test_json(self):
        got = self.tally(f"{self.quote} 1,280,000 第 3 頁", f"{self.contract} 54萬", as_json=True)
        self.assertEqual((got["count"], got["total"], got["average"]), (2, 1820000, 910000))
        self.assertEqual([(r["id"], r["number"], r["where"]) for r in got["rows"]],
                         [(self.quote, 1280000, "第 3 頁"), (self.contract, 540000, "")])

    def test_decimals_negative_and_odd_average(self):
        out = self.tally(f"{self.quote} 100.5", f"{self.contract} -1", f"{self.quote} 1")
        self.assertEqual(out.splitlines()[-1], "共 3 筆，合計 100.5，平均 33.5")
        out = self.tally(f"{self.quote} 1", f"{self.contract} 1", f"{self.quote} 2")
        self.assertEqual(out.splitlines()[-1], "共 3 筆，合計 4，平均 1.33")

    def test_refusals_exact(self):
        no_card = cards._scan_groups(self.V)[0]
        self.assertTrue(no_card)
        memo = E._cards.entry_of(self.V, E.MEMO)["id"]
        for rows, want in (((f"{self.quote} 約一百萬",), "看不懂這個數字：約一百萬"),
                           (("f-00000000 100",), "清冊沒有 f-00000000"),
                           ((f"{memo} 100",), "這份還沒有檔案卡"),
                           ((), "至少要一筆"),
                           ((f"{self.quote} 54 萬 總表",), "看不懂這個數字：54 萬")):
            with self.subTest(want=want):
                self.assertEqual(self.tally(*rows, code=3).strip(), want)


if __name__ == "__main__":
    unittest.main()
