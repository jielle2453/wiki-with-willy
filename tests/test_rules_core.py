"""rules/core.md 內容（D-059、D-087）：R-01～R-14 各恰一次、順序遞增、每條以「- R-nn 」開頭；不含絕對路徑與 plugin 變數。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "rules" / "core.md"


class RulesCore(unittest.TestCase):
    def setUp(self):
        self.text = CORE.read_text(encoding="utf-8")

    def test_r01_to_r14_each_once_in_order(self):
        """條目各恰一次、順序遞增；條文裡引用別條（R-14「照 R-12」）不算條目，但引用的編號要存在。"""
        found = re.findall(r"^- (R-\d{2}) ", self.text, re.M)
        ids = [f"R-{n:02d}" for n in range(1, 15)]
        self.assertEqual(found, ids)
        for ref in re.findall(r"R-\d{2}", self.text):
            self.assertIn(ref, ids)

    def test_each_rule_line_starts_with_dash_and_id(self):
        rules = [line for line in self.text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(rules), 14)
        for n, line in enumerate(rules, 1):
            self.assertTrue(line.startswith(f"- R-{n:02d} "), line)

    def test_no_absolute_path_or_plugin_variable(self):
        for bad in ("C:", "${CLAUDE_PLUGIN_ROOT}", "\\", "http"):
            self.assertNotIn(bad, self.text)

    def test_utf8_lf_single_trailing_newline(self):
        raw = CORE.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))


if __name__ == "__main__":
    unittest.main()
