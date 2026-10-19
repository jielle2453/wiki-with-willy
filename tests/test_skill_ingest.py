"""skills/ingest 的形狀（ingest 製卡第 9 點，驗收 16、17、21）：frontmatter 只有 name、description；描述 150 字內、不帶英文；
本文 120 行內（字數上限在 test_budget，目標 3000 字內）、用 ${CLAUDE_PLUGIN_ROOT} 叫 kb.py、不寫絕對路徑；兩份 reference 在且 ≤150 行；
tests/evals/ingest.json 20 句、各半、不重複，不該觸發的有 manage、answer、note、produce 句；
templates/card.md 的檔頭鍵與 carding.md 的欄位表跟 cards.KEYS 一致；manage 只改了「加資料夾」那列的收尾句。
字數門檻在 test_budget，這裡只守形狀。"""
import json
import re
import unittest

from tests._guard_fixture import ROOT
from kb._lib import cards  # noqa: E402

SKILL_DIR = ROOT / "skills" / "ingest"
SKILL = SKILL_DIR / "SKILL.md"
REFS = ("references/carding.md", "references/commands.md")
EVALS = ROOT / "tests" / "evals" / "ingest.json"
TEMPLATE = ROOT / "templates" / "card.md"
MANAGE = ROOT / "skills" / "manage" / "SKILL.md"
DESC_MAX = 150
BODY_MAX_LINES = 120
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
NOT_TRIGGER = ("上次那份報價在哪", "以後報價一律附日期", "做成 Word")
MANAGE_CLOSE = "加完說「從現在起讀得到；要我現在整理成卡嗎？」客戶說要就照 ingest skill 做。"


def split(text):
    lines = text.splitlines()
    assert lines and lines[0] == "---", "SKILL.md 開頭不是 ---"
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1:])


class Frontmatter(unittest.TestCase):
    def setUp(self):
        front, self.body = split(SKILL.read_text(encoding="utf-8"))
        self.fields = dict(line.split(":", 1) for line in front)

    def test_only_name_and_description(self):
        self.assertEqual(set(self.fields), {"name", "description"})
        self.assertEqual(self.fields["name"].strip(), "ingest")

    def test_description_short_chinese(self):
        desc = self.fields["description"].strip()
        self.assertTrue(desc)
        self.assertLessEqual(len(desc), DESC_MAX)
        self.assertIsNone(re.search(r"[A-Za-z]{2,}", desc), desc)


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def test_size(self):
        self.assertLessEqual(len(self.body.splitlines()), BODY_MAX_LINES)

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_order_of_steps(self):
        """骨架一節依序出現：changes → cards todo --json → plan → asks → turn note（整理 ingest 實作疑義裁定 1）。"""
        marks = ["changes", "cards todo --json", "plan", "asks", "turn note"]
        steps = self.body[self.body.index("## 骨架"):self.body.index("## 共通")]
        pos = [steps.index(m) for m in marks]
        self.assertEqual(pos, sorted(pos), marks)

    def test_mentions_judgment_words_and_principles(self):
        """仍屬判斷的字眼留在本文；答法對照（指令字串）不能回來（驗收 7）；兩條原則帶編號（裁定 1、2）。"""
        for want in ("manage", "收件", "還沒存檔"):
            with self.subTest(want=want):
                self.assertIn(want, self.body)
        for banned in ("cards drop", "pending resolve", "entity rename"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.body)
        for lead, code in (("刪除只認明確確認", "I-082"), ("一次最多 3 個問題", "D-135")):
            with self.subTest(principle=lead):
                line = next((ln for ln in self.body.splitlines() if lead in ln), "")
                self.assertIn(code, line)

    def test_no_card_field_names(self):
        """驗收 7：欄位名住程式輸出，本文不寫。"""
        for field in ("new", "stale", "unlinked", "synthesis_new", "entity_drift"):
            with self.subTest(field=field):
                self.assertNotRegex(self.body, rf"(?<![A-Za-z_]){field}(?![A-Za-z_])")

    def test_no_absolute_paths(self):
        for p in (SKILL, *(SKILL_DIR / r for r in REFS)):
            with self.subTest(file=p.name):
                self.assertIsNone(ABS_PATH.search(p.read_text(encoding="utf-8")), p.name)

    def test_references_exist_and_short(self):
        for ref in REFS:
            self.assertIn(ref, self.body)
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
        self.assertEqual(len({i["query"] for i in self.items}), 20)

    def test_negatives_cover_neighbours(self):
        negatives = [i["query"] for i in self.items if not i["should_trigger"]]
        for want in NOT_TRIGGER:
            with self.subTest(want=want):
                self.assertTrue(any(want in q for q in negatives), want)
        self.assertTrue(any("加進來" in q for q in negatives), "要有一句 manage")


def template_keys(text):
    lines = text.splitlines()
    start = lines.index("---")
    end = lines.index("---", start + 1)
    return [ln.split(":", 1)[0] for ln in lines[start + 1:end] if ln and not ln.startswith(" ")]


class FieldTables(unittest.TestCase):
    def test_template_keys_match_cards_keys(self):
        self.assertEqual(template_keys(TEMPLATE.read_text(encoding="utf-8")), list(cards.KEYS))

    def test_carding_table_matches(self):
        text = (SKILL_DIR / "references" / "carding.md").read_text(encoding="utf-8")
        rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
        keys = [re.match(r"\| `([^`]+)`", ln).group(1) for ln in rows]
        self.assertEqual(keys, list(cards.KEYS))

    def test_template_header_comment(self):
        first = TEMPLATE.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(first.startswith("<!--") and "kb cards todo" in first and "沒內容的段不寫" in first, first)


class ManageRow(unittest.TestCase):
    def test_add_row_closing_sentence(self):
        row = next(ln for ln in MANAGE.read_text(encoding="utf-8").splitlines() if ln.startswith("| 加資料夾"))
        self.assertTrue(row.rstrip(" |").endswith(MANAGE_CLOSE), row[-80:])
        self.assertNotIn("整理成卡的功能之後補", row)


if __name__ == "__main__":
    unittest.main()
