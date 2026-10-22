"""skills/produce 的形狀（produce 第 7 點，驗收 15）：frontmatter 只有 name、description；描述是 spec 介面那一句、150 字內；
本文 3000 字內，沒有步驟編號、沒有表格；分辨列出新做、改原檔、轉格式、存成檔、草稿與定稿，不收的各交給誰；
原則各帶理由與編號（D-163、D-165、D-166、D-010、D-170、D-159、D-169、D-164、R-09），明寫工作區用檔案工具寫（實作回報裁定 1）；
沒有怎麼寫內容與排版的規定（使用者 10-21：不限制模型做得好的事）；骨架指到程式與兩份 references；references 在、各 ≤150 行、
沒有絕對路徑；tests/evals/produce.json 20 句各半，不該觸發的含例句 15～20 與「今年報價總共多少」。字數上限另在 test_budget。"""
import json
import re
import unittest

from tests._guard_fixture import ROOT

SKILL_DIR = ROOT / "skills" / "produce"
SKILL = SKILL_DIR / "SKILL.md"
REFS = {name: SKILL_DIR / "references" / name for name in ("draft.md", "make.md")}
EVALS = ROOT / "tests" / "evals" / "produce.json"
DESC = ("要 AI 做出一份東西都用這個：做成 Word、Excel、PowerPoint、PDF、線上文件或簡報，「幫我做一份報價」「照去年的合約寫新的」"
        "「合約付款條件改成 30 天」「剛才那個表存成檔」「這週日程印出來」「草稿可以了，定稿」；問資料、記下來、整理檔案、搬檔、寄出去不用這個。")
DESC_MAX = 150
BODY_MAX_CHARS = 3000
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
NEGATIVES = ("以後報價單一律附報價日期", "大安陳報價多少", "幫我寄給王先生", "那份估價單幫我搬到", "總價是 128 萬",
             "我剛存了一份新報價進去", "今年報價總共多少")


def split(text):
    lines = text.splitlines()
    assert lines and lines[0] == "---", "SKILL.md 開頭不是 ---"
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1:])


class Frontmatter(unittest.TestCase):
    def setUp(self):
        front, _body = split(SKILL.read_text(encoding="utf-8"))
        self.fields = dict(line.split(":", 1) for line in front)

    def test_only_name_and_description(self):
        self.assertEqual(set(self.fields), {"name", "description"})
        self.assertEqual(self.fields["name"].strip(), "produce")

    def test_description_is_spec_sentence(self):
        desc = self.fields["description"].strip()
        self.assertEqual(desc, DESC)
        self.assertLessEqual(len(desc), DESC_MAX)


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def section(self, head, nxt):
        return self.body[self.body.index(head):self.body.index(nxt)]

    def test_size(self):
        self.assertLessEqual(len(self.body), BODY_MAX_CHARS)

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_no_step_numbering_and_no_table(self):
        self.assertIsNone(re.search(r"第\s*[0-9一二三四五六七八九十]+\s*步|\bStep\b", self.body, re.IGNORECASE))
        self.assertIsNone(re.search(r"^\d+\. ", self.body, re.MULTILINE))
        self.assertIsNone(re.search(r"^\|", self.body, re.MULTILINE), "本文不放表格（D-132）")

    def test_no_content_or_layout_rules(self):
        """怎麼寫內容與排版交給模型：本文不規定字型、配色、字級、段落結構。"""
        for word in ("字型", "配色", "字級", "微軟正黑體", "版型"):
            with self.subTest(word=word):
                self.assertNotIn(word, self.body)
        self.assertIsNone(re.search(r"\d+\s*pt", self.body), "不規定字級")

    def test_discriminates(self):
        part = self.section("## 分辨", "## 原則")
        for want in ("新做", "改她原本的檔", "轉格式", "存成檔", "草稿", "定稿", "範本", "第七節"):
            with self.subTest(want=want):
                self.assertIn(want, part)
        for want in ("answer", "note", "ingest", "manage", "做不到"):
            with self.subTest(hand_off=want):
                self.assertIn(want, part)

    def test_principles_have_reasons_and_codes(self):
        part = self.section("## 原則", "## 骨架")
        bullets = [ln for ln in part.splitlines() if ln.startswith("- ")]
        self.assertGreaterEqual(len(bullets), 8)
        for ln in bullets:
            with self.subTest(principle=ln[:12]):
                self.assertRegex(ln, r"[DIBR]-\d{2,3}|實作回報裁定")
                self.assertGreater(len(ln), 40, "要帶一句理由")
        for code in ("D-163", "D-165", "D-166", "D-010", "D-170", "D-159", "D-169", "D-164", "R-09"):
            with self.subTest(code=code):
                self.assertIn(code, part)
        self.assertIn("工作區用檔案工具寫", part, "實作回報裁定 1：明寫，免得模型照 R-01 不敢寫")
        for want in ("只讀副本", "只寫 OUT_DIR", "不碰原檔與其他位置", "B-401"):
            with self.subTest(want=want):
                self.assertIn(want, part, "實作回報後裁定 7：office run 免問，腳本的範圍要寫明")

    def test_skeleton_points_to_programs_and_references(self):
        part = self.section("## 骨架", "## 共通")
        for want in ("kb office check", "kb office setup", "kb office copy", "kb office run", "kb office recalc",
                     "kb office render", "kb output save", "kb output link", "kb lookup", "kb cards list",
                     "kb agenda list", "kb asks", "turn note", "references/draft.md", "references/make.md"):
            with self.subTest(want=want):
                self.assertIn(want, self.body if want.startswith("kb office run") else part)

    def test_references_exist_short_no_abs_paths(self):
        for p in [SKILL, *REFS.values()]:
            with self.subTest(file=p.name):
                self.assertTrue(p.is_file())
                text = p.read_text(encoding="utf-8")
                self.assertIsNone(ABS_PATH.search(text), p.name)
                if p != SKILL:
                    self.assertLessEqual(len(text.splitlines()), REFERENCE_MAX_LINES)

    def test_make_covers_spec_points(self):
        make = REFS["make.md"].read_text(encoding="utf-8")
        for want in ("claude.ai 帳號", "只有她看得到", "按下載", "微軟正黑體", "zh-TW", "AI 助理", "kb office run",
                     "kb office recalc", "kb office render", "（AI 修改）", "--link", "LibreOffice", "Node.js"):
            with self.subTest(want=want):
                self.assertIn(want, make)
        draft = REFS["draft.md"].read_text(encoding="utf-8")
        for want in ("（草稿）.md", "短標", "kb tally", "照片", "剛回答過的", "kb output drop-draft", "kb asks"):
            with self.subTest(want=want):
                self.assertIn(want, draft)


class Round2(unittest.TestCase):
    """測試後修正裁定 17（預設推薦線上文件、三件事在建之前講）、18（修訂作者範例、Grep 驗）、19（描述、tally 格式、
    Docs 改請她下載、範本從 profile show --json 讀、草稿用 Write、拒絕句補一句）。"""

    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]
        self.draft = REFS["draft.md"].read_text(encoding="utf-8")
        self.make = REFS["make.md"].read_text(encoding="utf-8")

    def test_online_by_default_and_three_things_first(self):
        part = self.body[self.body.index("## 原則"):self.body.index("## 骨架")]
        for want in ("一律推薦線上文件", "給業主", "claude.ai 帳號", "只有她看得到", "要檔怎麼拿", "建之前"):
            with self.subTest(want=want):
                self.assertIn(want, part)
        for text in (self.draft, self.make):
            self.assertIn("一律推薦線上文件" if text is self.draft else "預設推薦線上文件", text)
            self.assertIn("建之前", text)

    def test_draft_written_with_write(self):
        self.assertIn("檔案工具（Write）", self.body)

    def test_redline_author_example_and_grep(self):
        self.assertIn('w:author="AI 助理"', self.make)
        self.assertIn("<w:ins", self.make)
        self.assertIn("Grep", self.make)

    def test_tally_row_format(self):
        self.assertIn('--row "<卡編號> <數字> [位置]"', self.draft)
        self.assertRegex(self.draft, r'kb tally --row "f-[0-9a-f]{8} ')

    def test_docs_download_not_written_by_me(self):
        self.assertNotIn("由我把匯出的 docx", self.make)
        self.assertIn("請她從線上文件下載", self.make)

    def test_template_from_profile_show(self):
        self.assertIn("kb profile show --json", self.make)
        self.assertIn("只收工作區或 AI 產出裡的檔", self.make)

    def test_answer_says_excel_file(self):
        answer = (ROOT / "skills" / "answer" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("做成文件、Excel 檔、簡報", answer)


class Evals(unittest.TestCase):
    def test_twenty_half_half_with_negatives(self):
        items = json.loads(EVALS.read_text(encoding="utf-8"))
        self.assertEqual(len(items), 20)
        self.assertEqual(sum(1 for x in items if x["should_trigger"]), 10)
        negatives = " ".join(x["query"] for x in items if not x["should_trigger"])
        for want in NEGATIVES:
            with self.subTest(want=want):
                self.assertIn(want, negatives)


if __name__ == "__main__":
    unittest.main()
