"""skills/manage 的形狀（specs/2026-10-16-manage.md 第 5 節）：frontmatter 只有 name、description；
描述 150 字內、不帶英文觸發詞；本文用 ${CLAUDE_PLUGIN_ROOT} 叫 kb.py、不寫絕對路徑；提到的 reference 都在且 ≤150 行；
tests/evals/manage.json 20 句、該觸發與不該各 10、不重複、含 spec 給的句子。字數門檻在 test_budget，這裡只守形狀。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "manage"
SKILL = SKILL_DIR / "SKILL.md"
EVALS = ROOT / "tests" / "evals" / "manage.json"
DESC_MAX = 150
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
SPEC_QUERIES = (
    "把 D:\\報價 也加進來，裡面是去年到現在的報價單",
    "不要讀 公司行政 裡面那個薪資的資料夾，那是私人的",
    "廠商資料那個資料夾不要了",
    "薪資那夾還是讀好了",
    "你現在讀哪些資料夾",
    "報價那個資料夾我搬到 E 槽了",
    "幫我把信義王的報價 v2 搬到王宅那夾",
    "D 槽報價單裡信義王那份寫多少錢",
    "以後不要把私人的事記進卡",
)


def split(text):
    """回 (frontmatter 行, 本文)。"""
    lines = text.splitlines()
    assert lines and lines[0] == "---", "SKILL.md 開頭不是 ---"
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1:])


class Frontmatter(unittest.TestCase):
    def setUp(self):
        self.front, self.body = split(SKILL.read_text(encoding="utf-8"))
        self.fields = dict(line.split(":", 1) for line in self.front)

    def test_only_name_and_description(self):
        self.assertEqual(set(self.fields), {"name", "description"})
        self.assertEqual(self.fields["name"].strip(), "manage")

    def test_description_short_chinese_no_english_triggers(self):
        desc = self.fields["description"].strip()
        self.assertTrue(desc)
        self.assertLessEqual(len(desc), DESC_MAX)
        self.assertIsNone(re.search(r"[A-Za-z]{2,}", desc), desc)  # D 槽、E 槽這種單字母可以


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_no_absolute_paths(self):
        for text, name in ((self.body, "SKILL.md"),
                           *((p.read_text(encoding="utf-8"), p.name) for p in (SKILL_DIR / "references").glob("*.md"))):
            with self.subTest(file=name):
                self.assertIsNone(ABS_PATH.search(text), name)

    def test_mentioned_references_exist_and_short(self):
        refs = set(re.findall(r"references/[^\s`)）、，。]+\.md", self.body))
        self.assertIn("references/sources.md", refs)
        for ref in refs:
            p = SKILL_DIR / ref
            with self.subTest(reference=ref):
                self.assertTrue(p.is_file())
                self.assertLessEqual(len(p.read_text(encoding="utf-8").splitlines()), REFERENCE_MAX_LINES)


class Evals(unittest.TestCase):
    def setUp(self):
        self.items = json.loads(EVALS.read_text(encoding="utf-8"))

    def test_twenty_half_and_half_unique(self):
        self.assertEqual(len(self.items), 20)
        for item in self.items:
            self.assertEqual(set(item), {"query", "should_trigger"})
            self.assertIsInstance(item["should_trigger"], bool)
        self.assertEqual(sum(i["should_trigger"] for i in self.items), 10)
        queries = [i["query"] for i in self.items]
        self.assertEqual(len(set(queries)), 20)

    def test_contains_spec_queries(self):
        by_query = {i["query"]: i["should_trigger"] for i in self.items}
        for q in SPEC_QUERIES:
            with self.subTest(query=q):
                self.assertIn(q, by_query)
        self.assertFalse(by_query[SPEC_QUERIES[6]])
        self.assertFalse(by_query[SPEC_QUERIES[8]])
        self.assertTrue(by_query[SPEC_QUERIES[5]])


if __name__ == "__main__":
    unittest.main()
