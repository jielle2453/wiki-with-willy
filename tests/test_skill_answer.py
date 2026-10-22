"""skills/answer 的形狀（answer 第 10 點，驗收 16）：frontmatter 只有 name、description；描述 150 字內、不帶英文、涵蓋例句的問法；
本文 120 行、3000 字內，沒有步驟編號、沒有參數對照表（表格列與參數旗標），原則帶理由與編號、含 D-153、D-155、D-159、D-160；
分辨列出不收的話各交給誰；骨架指到程式與兩份 references；references/find.md、verify.md 在、各 ≤150 行、沒有絕對路徑；
tests/evals/answer.json 20 句、各半，不該觸發的含例句 12、15～18、20 與「幫我檢查一下」「整理一下」。字數上限另在 test_budget。"""
import json
import re
import unittest

from tests._guard_fixture import ROOT

SKILL_DIR = ROOT / "skills" / "answer"
SKILL = SKILL_DIR / "SKILL.md"
REFS = {name: SKILL_DIR / "references" / name for name in ("find.md", "verify.md")}
EVALS = ROOT / "tests" / "evals" / "answer.json"
DESC_MAX = 150
BODY_MAX_CHARS = 3000
BODY_MAX_LINES = 120
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
# 裁定 12：骨架那一句准帶 --changed、--find、--from --to（AI 不讀 find.md 也挑得對），其餘參數仍住 references
FLAGS = re.compile(r"--(type|year|entity|source|row|since|kind|card|basis|text|how|replaces|n)\b")
DESCRIBES = ("現況", "找檔", "數字", "統計", "比較", "這週", "最近改了什麼", "聊過", "確認")
NEGATIVES = ("把剛才的答案記下來", "改用系統櫃", "8500", "Excel", "放進去了", "搬到", "幫我檢查一下", "整理一下")


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
        self.assertEqual(self.fields["name"].strip(), "answer")

    def test_description_short_chinese_and_complete(self):
        desc = self.fields["description"].strip()
        self.assertLessEqual(len(desc), DESC_MAX)
        self.assertIsNone(re.search(r"[A-Za-z]{2,}", desc), desc)
        for word in DESCRIBES:
            with self.subTest(word=word):
                self.assertIn(word, desc)
        for word in ("記下來", "整理檔案", "做成文件", "管資料夾"):
            with self.subTest(word=word):
                self.assertIn(word, desc[desc.index("；"):], "不收的放在分號之後")


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def section(self, head, nxt):
        return self.body[self.body.index(head):self.body.index(nxt)]

    def test_size(self):
        self.assertLessEqual(len(self.body.splitlines()), BODY_MAX_LINES)
        self.assertLessEqual(len(self.body), BODY_MAX_CHARS)

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_no_step_numbering_and_no_parameter_table(self):
        self.assertIsNone(re.search(r"第\s*[0-9一二三四五六七八九十]+\s*步|\bStep\b", self.body, re.IGNORECASE))
        self.assertIsNone(re.search(r"^\d+\. ", self.body, re.MULTILINE))
        self.assertIsNone(re.search(r"^\|", self.body, re.MULTILINE), "本文不放表格（D-132）")
        self.assertIsNone(FLAGS.search(self.body), FLAGS.search(self.body))

    def test_discriminates(self):
        part = self.section("## 分辨", "## 原則")
        for want in ("現在怎樣", "在哪", "數字", "比", "這週", "改了什麼", "聊過", "要我確認", "一般知識"):
            with self.subTest(want=want):
                self.assertIn(want, part)
        self.assertIn("草稿還在等的歸 produce", part, "produce 實作回報後裁定 10：asks 的 draft 歸 produce")
        for want in ("note", "ingest", "manage", "produce"):
            with self.subTest(hand_off=want):
                self.assertIn(want, part)
        self.assertIn("開場有新檔", part, "裁定 18：收例句 13")

    def test_principles_have_reasons_and_codes(self):
        part = self.section("## 原則", "## 骨架")
        bullets = [ln for ln in part.splitlines() if ln.startswith("- ")]
        self.assertGreaterEqual(len(bullets), 8)
        for ln in bullets:
            with self.subTest(principle=ln[:12]):
                self.assertRegex(ln, r"[DIBR]-\d{2,3}")
                self.assertGreater(len(ln), 40, "要帶一句理由")
        for code in ("D-153", "D-155", "D-159", "D-160", "D-149", "R-02", "B-048", "B-089"):
            with self.subTest(code=code):
                self.assertIn(code, part)
        self.assertIn("〔", part, "編號換成短標那條要講〔…〕")

    def test_skeleton_points_to_programs_and_references(self):
        part = self.section("## 骨架", "## 共通")
        for want in ("kb lookup", "kb cards list", "kb agenda list", "kb journal recent", "kb asks", "kb convert",
                     "kb tally", "kb cards redo", "kb pending add", "turn note", "references/find.md",
                     "references/verify.md"):
            with self.subTest(want=want):
                self.assertIn(want, part)

    def test_no_absolute_paths(self):
        for p in [SKILL, *REFS.values()]:
            with self.subTest(file=p.name):
                self.assertIsNone(ABS_PATH.search(p.read_text(encoding="utf-8")), p.name)


class Round2(unittest.TestCase):
    """answer 測試後修正裁定 12、13、18、19：骨架寫明每種問法叫哪個指令與篩法；原則不改卡；過場一律繁中放最前面；
    find.md 日程與工作日誌用人讀輸出；verify.md 照 verify_max、粗估不加總、那份在哪只給檔名日期、裁示短標、矛盾閉環。"""

    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]
        self.find = REFS["find.md"].read_text(encoding="utf-8")
        self.verify = REFS["verify.md"].read_text(encoding="utf-8")

    def section(self, head, nxt):
        return self.body[self.body.index(head):self.body.index(nxt)]

    def test_12_skeleton_names_the_filters(self):
        part = self.section("## 骨架", "## 共通")
        for want in ("journal recent --changed", "--find", "agenda list --from --to"):
            with self.subTest(want=want):
                self.assertIn(want, part)

    def test_13_never_edit_cards(self):
        part = self.section("## 原則", "## 骨架")
        line = next(ln for ln in part.splitlines() if "一律不改" in ln)
        for want in ("綜合卡、檔案卡、設定檔一律不改", "照原檔答", "cards redo", "D-155",
                     "用卡標題與短標講，不貼〔f-…〕這類編號",  # 裁定 31
                     "其他問題不寫檔；只有 `kb cards redo` 或 `kb pending add` 的回合才叫 `turn note`"):  # 裁定 32
            with self.subTest(want=want):
                self.assertIn(want, line)

    def test_18_findings_and_counts(self):
        part = self.section("## 原則", "## 骨架")
        self.assertIn("依據沒變", part)
        self.assertIn("扣掉不採用", self.find)

    def test_19_chinese_transitions_first(self):
        first = self.body.index("繁體中文")
        self.assertLess(first, self.body.index("## 分辨"))
        self.assertIn("不唸步驟", self.body[:self.body.index("## 分辨")])
        self.assertNotIn("過場", self.body[self.body.index("## 共通"):], "移到最前面，不留兩份")

    def test_18_find_uses_human_output_for_agenda_and_journal(self):
        for head in ("## 三、", "## 四、"):
            with self.subTest(head=head):
                line = next(ln for ln in self.find.splitlines() if ln.startswith(head))
                self.assertNotIn("--json", line)
        self.assertIn("人讀", self.find)

    def test_18_verify(self):
        self.assertIn("verify_max", self.verify)
        self.assertNotIn("answer_verify_max", self.verify)
        self.assertNotIn("粗估約", self.verify)
        self.assertIn("15 張卡，正在逐份對", self.verify)
        self.assertIn("〔你 10/21 說的〕", self.verify)
        self.assertIn("kb pending resolve", self.verify)
        self.assertNotIn("那份在哪」才給", self.verify)
        self.assertNotIn("設定檔第四節時", self.verify)
        record = (ROOT / "skills" / "note" / "references" / "record-answer.md").read_text(encoding="utf-8")
        line = next(ln for ln in record.splitlines() if ln.startswith("kb finding add"))
        self.assertNotIn("--json", line)


class Round3(unittest.TestCase):
    """answer 測試第二輪後裁定 31～33：verify.md 跟原則 13 一致（短標不貼編號、只有 redo 與 pending add 才 turn note）；
    tally 範例不帶 --json；CLAUDE.md 的 tally_cli 那列不被 | 破表。"""

    def setUp(self):
        self.verify = REFS["verify.md"].read_text(encoding="utf-8")

    def test_31_32_verify_matches_principle(self):
        part = self.verify[self.verify.index("## 四、"):self.verify.index("## 五、")]
        self.assertIn("用卡標題與短標講，不貼〔f-…〕這類編號（D-160、B-089）", part)
        line = next(ln for ln in part.splitlines() if "turn note" in ln)
        self.assertIn("只有標重做（`kb cards redo`）或記待處理（`kb pending add`）的回合", line)
        self.assertNotIn("convert 記了讀不到", self.verify)

    def test_33_tally_example_without_json(self):
        part = self.verify[self.verify.index("## 三、"):self.verify.index("## 四、")]
        line = next(ln for ln in part.splitlines() if ln.startswith("kb tally --row"))
        self.assertNotIn("--json", line)

    def test_33_claude_md_tally_row_keeps_three_columns(self):
        row = next(ln for ln in (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
                   if ln.startswith("| `scripts/kb/tally_cli.py` |"))
        cells = re.split(r"(?<!\\)\|", row)
        self.assertEqual(len(cells), 4, cells)


class References(unittest.TestCase):
    def test_exist_and_short(self):
        for name, p in REFS.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(p.read_text(encoding="utf-8").splitlines()), REFERENCE_MAX_LINES)

    def test_find_covers(self):
        text = REFS["find.md"].read_text(encoding="utf-8")
        for want in ("kb lookup", "kb cards list", "kb agenda list", "kb journal recent", "kb asks", "mentions",
                     "--find", "--changed", "--since", "types", "in_profile", "ruled_out", "stale", "unsorted",
                     "全新", "升級前", "做到一半"):
            with self.subTest(want=want):
                self.assertIn(want, text)

    def test_verify_covers(self):
        text = REFS["verify.md"].read_text(encoding="utf-8")
        for want in ("粗估", "kb convert", "kb tally", "--row", "kb cards redo", "kb pending add", "--kind conflict",
                     "verify_max", "〔你", "〔我算的", "一般知識", "第二次問", "原檔改過"):
            with self.subTest(want=want):
                self.assertIn(want, text)


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
        for want in NEGATIVES:
            with self.subTest(want=want):
                self.assertTrue(any(want in q for q in negatives), want)

    def test_positives_cover_the_kinds_of_question(self):
        positives = " ".join(i["query"] for i in self.items if i["should_trigger"])
        for want in ("現在", "多少", "在哪", "幾次", "這週", "改了什麼", "要我確認", "結案了嗎"):
            with self.subTest(want=want):
                self.assertIn(want, positives)


if __name__ == "__main__":
    unittest.main()
