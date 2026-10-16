"""skills/manage 的形狀（specs/2026-10-16-manage.md 第 5 節）：frontmatter 只有 name、description；
描述 150 字內、不帶英文觸發詞；本文用 ${CLAUDE_PLUGIN_ROOT} 叫 kb.py、不寫絕對路徑；提到的 reference 都在且 ≤150 行；
tests/evals/manage.json 20 句、該觸發與不該各 10、不重複、含 spec 給的句子（含疑義裁定 2 的兩句）；
憑證那句與 repo CLAUDE.md commands/ 列照疑義裁定 3、5。字數門檻在 test_budget，這裡只守形狀。
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
    "把信義王報價 v2 的付款條件改成 30 天",
)
CREDENTIAL_LINE = "檔名像密碼或金鑰的預設不讀，這版還不能開放，之後會有"
COMMANDS_ROW = "| `commands/` | 尚未。build 五步與 upgrade、backup、restore、relocate、migrate、account（D-028），沒有 manage 指令 |"


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
        self.assertIn("幫我搬", desc)  # 疑義裁定 2：搬單一檔案也進 manage，回做不到
        self.assertIn("改名", desc)


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_credential_line_follows_ruling_3(self):
        """驗收 17：憑證檔那句照疑義裁定 3；不再承諾一份一份放行。"""
        self.assertIn(CREDENTIAL_LINE, self.body)
        for p in (SKILL, *(SKILL_DIR / "references").glob("*.md")):
            with self.subTest(file=p.name):
                self.assertNotIn("一份一份", p.read_text(encoding="utf-8"))

    def test_move_row_checks_new_path_first(self):
        """驗收 19：搬家那列先對新路徑 add --check，過了才 remove；被拒什麼都不動。"""
        row = next(line for line in self.body.splitlines() if line.startswith("| 已經搬好"))
        self.assertLess(row.index("--check"), row.index("remove"))
        self.assertIn("什麼都不動", row)

    def test_move_row_step1_no_alias_step5_uses_old_spec(self):
        """驗收 21：第①步不問別名、不念暫用名；第⑤步用第②步記下的舊 spec 接新路徑，不再 list。"""
        row = next(line for line in self.body.splitlines() if line.startswith("| 已經搬好"))
        step1 = row[row.index("①"):row.index("②")]
        step5 = row[row.index("⑤"):row.index("⑥")]
        self.assertNotIn("別名", step1)
        self.assertIn("spec", step5)
        self.assertIn("新路徑", step5)
        self.assertNotIn("list", step5)

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
        self.assertTrue(by_query[SPEC_QUERIES[6]])  # 驗收 15：搬單一檔案要觸發
        self.assertFalse(by_query[SPEC_QUERIES[9]])  # 驗收 15：改內容是 produce
        self.assertFalse(by_query[SPEC_QUERIES[8]])
        self.assertTrue(by_query[SPEC_QUERIES[5]])


class RepoClaudeMd(unittest.TestCase):
    def test_commands_row_follows_ruling_5(self):
        """驗收 17：commands/ 列不寫 manage 指令，免得跟 skill 撞名。"""
        lines = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        self.assertIn(COMMANDS_ROW, lines)


if __name__ == "__main__":
    unittest.main()
