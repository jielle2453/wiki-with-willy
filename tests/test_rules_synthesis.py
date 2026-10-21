"""rules/synthesis.md（note 改知識第 8 點，驗收 14）：編號 S-01～S-32 連號不重複；S-11、S-12、S-14、S-26 寫到口述，
S-30～S-32 寫口述為準、不採用、兩個依據都標。"""
import re
import unittest

from tests._guard_fixture import ROOT

RULES = ROOT / "rules" / "synthesis.md"


class SynthesisRules(unittest.TestCase):
    def setUp(self):
        self.text = RULES.read_text(encoding="utf-8")
        self.rules = dict(re.findall(r"^- (S-\d{2}) (.+)$", self.text, re.MULTILINE))

    def test_numbering_is_contiguous(self):
        found = re.findall(r"^- (S-\d{2}) ", self.text, re.MULTILINE)
        self.assertEqual(found, [f"S-{i:02d}" for i in range(1, 33)])

    def test_changed_rules_mention_statements(self):
        self.assertIn("〔s-", self.rules["S-11"])
        self.assertIn("口述", self.rules["S-12"])
        self.assertIn("〔s-", self.rules["S-14"])
        self.assertIn("statements", self.rules["S-26"])
        self.assertIn("先記再寫", self.rules["S-26"])

    def test_new_rules(self):
        self.assertIn("口述為準", self.rules["S-30"])
        self.assertIn("原檔寫 X", self.rules["S-30"])
        self.assertIn("不採用", self.rules["S-31"])
        self.assertIn("兩個都標", self.rules["S-32"])

    def test_no_reasons_or_decision_codes(self):
        """conventions 二：rules/ 不寫理由，決策編號不出現。"""
        self.assertIsNone(re.search(r"\b[DI]-\d{3}\b", self.text))


if __name__ == "__main__":
    unittest.main()
