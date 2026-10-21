"""skills/note 的形狀（日程第 7 點，驗收 12）：frontmatter 只有 name、description；描述 150 字內、不帶英文且涵蓋 note 的全部（D-131）；
本文 120 行內、目標 3000 字內，沒有「第 N 步」或 Step、沒有欄位名與 agenda edit 這類指令對照，原則帶理由與編號（D-132），
含 D-127、D-131、D-135；用 ${CLAUDE_PLUGIN_ROOT} 叫 kb.py；references/agenda.md ≤150 行；tests/evals/note.json 20 句、各半，
該觸發的涵蓋偏好與糾正不只日程，不該觸發的有 ingest、manage、answer 的句子。字數上限另在 test_budget。"""
import json
import re
import unittest

from tests._guard_fixture import ROOT

SKILL_DIR = ROOT / "skills" / "note"
SKILL = SKILL_DIR / "SKILL.md"
REF = SKILL_DIR / "references" / "agenda.md"
EVALS = ROOT / "tests" / "evals" / "note.json"
DESC_MAX = 150
BODY_TARGET_CHARS = 3000
BODY_MAX_LINES = 120
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
FIELD_NAMES = ("kind", "date", "start", "entities", "status", "source", "origin", "lead_days", "proposal", "created_turn",
               "history", "ended")
COMMAND_MAP = re.compile(r"agenda\s+(add|edit|done|cancel|reopen|recheck|render)\b|--(kind|date|text|entity|start|time|card)\b")
DESCRIBES = ("偏好", "糾正", "決定", "記下來", "型別", "日程", "結案")
NOT_TRIGGER = ("整理一下", "加進來", "這週要做什麼", "信義王現在怎樣", "上次那份報價在哪", "做成 Word")


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
        self.assertEqual(self.fields["name"].strip(), "note")

    def test_description_short_chinese_and_complete(self):
        desc = self.fields["description"].strip()
        self.assertLessEqual(len(desc), DESC_MAX)
        self.assertIsNone(re.search(r"[A-Za-z]{2,}", desc), desc)
        for word in DESCRIBES:
            with self.subTest(word=word):
                self.assertIn(word, desc)


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def test_size(self):
        self.assertLessEqual(len(self.body.splitlines()), BODY_MAX_LINES)
        self.assertLessEqual(len(self.body), BODY_TARGET_CHARS)

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_no_step_numbering(self):
        """B-064：過場不唸步驟編號，本文自己也不編。"""
        self.assertIsNone(re.search(r"第\s*[0-9一二三四五六七八九十]+\s*步|\bStep\b", self.body, re.IGNORECASE))
        self.assertIsNone(re.search(r"^\d+\. ", self.body, re.MULTILINE))

    def test_no_field_names_or_command_map(self):
        for name in FIELD_NAMES:
            with self.subTest(field=name):
                self.assertNotRegex(self.body, rf"(?<![A-Za-z_]){name}(?![A-Za-z_])")
        self.assertIsNone(COMMAND_MAP.search(self.body), COMMAND_MAP.search(self.body))

    def test_principles_have_reasons_and_codes(self):
        section = self.body[self.body.index("## 原則"):self.body.index("## 骨架")]
        bullets = [ln for ln in section.splitlines() if ln.startswith("- ")]
        self.assertGreaterEqual(len(bullets), 5)
        for ln in bullets:
            with self.subTest(principle=ln[:12]):
                self.assertRegex(ln, r"[DI]-\d{3}")
                self.assertGreater(len(ln), 40, "要帶一句理由")
        for code in ("D-127", "D-131", "D-135"):
            self.assertIn(code, section)

    def test_discriminates_unsupported_kinds(self):
        for want in ("還記不起來", "沒有寫任何檔", "不要說「記下了」"):
            with self.subTest(want=want):
                self.assertIn(want, self.body)

    def test_skeleton_mentions_the_three_programs(self):
        skeleton = self.body[self.body.index("## 骨架"):self.body.index("## 共通")]
        for want in ("list --json", "kb entity progress", "kb asks --json", "turn note", "references/agenda.md"):
            with self.subTest(want=want):
                self.assertIn(want, skeleton)

    def test_no_absolute_paths(self):
        for p in (SKILL, REF):
            with self.subTest(file=p.name):
                self.assertIsNone(ABS_PATH.search(p.read_text(encoding="utf-8")), p.name)

    def test_reference_exists_short_and_linked(self):
        self.assertIn("references/agenda.md", self.body)
        self.assertLessEqual(len(REF.read_text(encoding="utf-8").splitlines()), REFERENCE_MAX_LINES)


class RuleChange(unittest.TestCase):
    """note 改規則第 7 點、驗收 15：分辨有改規則、「記不起來」只剩四種、原則含 D-137 與 D-139、本文沒有 欄=值 參數對照；
    references/preference.md 在、≤150 行、有 20 句例句表。"""

    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def section(self, head, nxt):
        return self.body[self.body.index(head):self.body.index(nxt)]

    def test_discriminates_rule_changes(self):
        part = self.section("## 分辨", "## 原則")
        self.assertIn("**改規則的話**", part)
        self.assertIn("偏好", part)
        self.assertIn("references/preference.md", self.body)
        self.assertIn("kb profile show --json", self.body)
        self.assertIn("--check --json", self.section("## 骨架", "## 共通"))

    def test_credential_line_warns_about_renaming(self):
        """確認後裁定 11（確認落差 3）：憑證例外那行本文就帶「別改檔名」的提醒，不只靠 ruling.md。"""
        line = next(ln for ln in self.body.splitlines() if ln.startswith("- **這版記不起來的話**"))
        self.assertIn("明說沒寫任何檔，提醒不要把檔名改掉來讓程式讀（守門只看檔名，D-145）", line)

    def test_unsupported_left_with_credentials_only(self):
        """note 改知識驗收 15、answer 第 11 點：「記不起來」只剩憑證例外（存答案這版記得起來了，D-151）。"""
        line = next(ln for ln in self.body.splitlines() if ln.startswith("- **這版記不起來的話**"))
        for want in ("密碼檔可以讀", "密碼記下來"):
            with self.subTest(want=want):
                self.assertIn(want, line)
        for gone in ("加型別", "改型別名", "以後報價一律附日期", "糾正", "裁示", "別名", "決定", "剛才的答案"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, line)

    def test_merge_asks_why_before_check(self):
        """測試第二輪後裁定 27（N-3b）：骨架的型別合併先問一句為什麼要併，再 --check（跟 preference.md 一致）。"""
        line = next(ln for ln in self.section("## 骨架", "## 共通").splitlines() if "合併" in ln and "--check" in ln)
        self.assertIn("為什麼", line)
        self.assertLess(line.index("為什麼"), line.index("--check"))

    def test_principles_have_new_codes(self):
        part = self.section("## 原則", "## 骨架")
        for code in ("D-137", "D-139"):
            self.assertIn(code, part)

    def test_no_field_value_map(self):
        self.assertIsNone(re.search(r"[^\s`=]+=[^\s`=]", self.body), re.search(r"[^\s`=]+=[^\s`=]", self.body))

    def test_preference_reference(self):
        ref = SKILL_DIR / "references" / "preference.md"
        text = ref.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), REFERENCE_MAX_LINES)
        rows = [ln for ln in text.splitlines() if re.match(r"^\| \d+ \|", ln)]
        self.assertEqual([int(ln.split("|")[1]) for ln in rows], list(range(1, 21)))
        for want in ("萬元", "兩種意思", "20 份以內", "按停止"):
            with self.subTest(want=want):
                self.assertIn(want, text)
        self.assertIsNone(ABS_PATH.search(text))


class Knowledge(unittest.TestCase):
    """note 改知識第 9 點、驗收 15：分辨有改知識、原則含 D-147、D-146、D-041 與理由、骨架有四條路；
    references/correction.md、references/ruling.md 在、各 ≤150 行、沒有絕對路徑；憑證那句只住 ruling.md。"""

    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def section(self, head, nxt):
        return self.body[self.body.index(head):self.body.index(nxt)]

    def test_discriminates_knowledge(self):
        part = self.section("## 分辨", "## 原則")
        line = next(ln for ln in part.splitlines() if ln.startswith("- **改知識的話**"))
        for want in ("事實", "決定", "糾正", "別名", "裁示"):
            with self.subTest(want=want):
                self.assertIn(want, line)

    def test_principles_have_knowledge_codes(self):
        part = self.section("## 原則", "## 骨架")
        for code in ("D-147", "D-146", "D-041"):
            self.assertIn(code, part)
        self.assertIn("糾正先查錯在哪", part)
        self.assertIn("不寫進檔案卡", part)

    def test_skeleton_has_the_knowledge_paths(self):
        part = self.section("## 骨架", "## 共通")
        for want in ("kb statement add", "kb convert", "references/correction.md", "kb entity alias", "kb ruling add",
                     "references/ruling.md"):
            with self.subTest(want=want):
                self.assertIn(want, part)

    def test_references(self):
        for name in ("correction.md", "ruling.md"):
            with self.subTest(name=name):
                text = (SKILL_DIR / "references" / name).read_text(encoding="utf-8")
                self.assertLessEqual(len(text.splitlines()), REFERENCE_MAX_LINES)
                self.assertIsNone(ABS_PATH.search(text))
        correction = (SKILL_DIR / "references" / "correction.md").read_text(encoding="utf-8")
        for want in ("--replaces", "withdraw", "kb cards redo", "kb cards mount", "suspect_cards", "是同一個，合併"):
            with self.subTest(want=want):
                self.assertIn(want, correction)
        ruling = (SKILL_DIR / "references" / "ruling.md").read_text(encoding="utf-8")
        for want in ("口述為準", "不採用", "D-041", "D-149", "D-145", "改了名整份連密碼都會被讀進去"):
            with self.subTest(want=want):
                self.assertIn(want, ruling)
        pref = (SKILL_DIR / "references" / "preference.md").read_text(encoding="utf-8")
        self.assertNotIn("改了名整份連密碼都會被讀進去", pref)

    def test_round_one_text_rulings(self):
        """測試第一輪後裁定 7～10：例句 7 走完、綜合卡寫錯改記口述、沒登記的實體先問（三份同一句）、「以後都以口述為準」不導去改規則。"""
        refs = {n: (SKILL_DIR / "references" / n).read_text(encoding="utf-8")
                for n in ("correction.md", "ruling.md", "agenda.md")}
        part = refs["correction.md"][refs["correction.md"].index("業主是陳先生"):]
        for want in ("kb cards mount", "kb cards redo", "記一條口述"):
            with self.subTest(want=want):
                self.assertIn(want, part)
        row = next(ln for ln in refs["correction.md"].splitlines() if ln.startswith("| 綜合卡寫錯 |"))
        self.assertIn("記一條口述", row)
        self.assertIn("記下了，下次整理時綜合卡會改", row)
        ask = "客戶說的名稱實體清單沒有，先問「<名稱> 是新的案子／對象嗎？」，客戶說是才 `kb entity add`；說是別的名字就照那個名字"
        for name, text in refs.items():
            with self.subTest(name=name):
                self.assertIn(ask, text)
        self.assertNotIn("不要為了掛日程替客戶登記新實體", refs["agenda.md"])
        self.assertIn("這版只能一件一件記", refs["ruling.md"])
        self.assertNotIn("走改規則", refs["ruling.md"])


class RecordAnswer(unittest.TestCase):
    """answer 第 11 點、驗收 16：分辨多一條「記的是剛才的回答」指到 record-answer.md；record-answer.md 在、≤60 行；描述沒改（D-151）。"""

    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.body = split(self.text)[1]

    def test_discriminates_recorded_answer(self):
        part = self.body[self.body.index("## 分辨"):self.body.index("## 原則")]
        line = next(ln for ln in part.splitlines() if ln.startswith("- **記的是剛才的回答**"))
        for want in ("references/record-answer.md", "D-151"):
            with self.subTest(want=want):
                self.assertIn(want, line)

    def test_reference(self):
        text = (SKILL_DIR / "references" / "record-answer.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 60)
        for want in ("kb finding add", "--basis", "--how", "--replaces", "已記下推算", "依據至少要一張卡（--basis）", "D-154"):
            with self.subTest(want=want):
                self.assertIn(want, text)
        self.assertIsNone(ABS_PATH.search(text))

    def test_description_unchanged(self):
        desc = split(self.text)[0]
        self.assertIn("description: 客戶口述要記的日程與事：「9/25 下午有個會」「答應王先生 10/10 前給報價」「交屋改 12/10」"
                      "「提醒我寄發票」「第二期款付了」「信義王結案了」，偏好、糾正舊資料、決定、「把這個記下來」、加型別或改型別名、"
                      "開場有「日程：逾期」時都用這個；問現況、整理檔案、加資料夾不用。", desc)


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

    def test_positives_cover_more_than_the_agenda(self):
        positives = " ".join(i["query"] for i in self.items if i["should_trigger"])
        for want in ("以後", "更正", "決定", "型別", "結案", "暫停", "付了", "改成"):
            with self.subTest(want=want):
                self.assertIn(want, positives)

    def test_negatives_cover_neighbours(self):
        negatives = [i["query"] for i in self.items if not i["should_trigger"]]
        for want in ("整理一下", "加進來", "這週要做什麼", "現在怎樣", "上次那份報價在哪", "Word"):
            with self.subTest(want=want):
                self.assertTrue(any(want in q for q in negatives), want)


if __name__ == "__main__":
    unittest.main()
