"""skills/manage 的形狀（manage 第二版第 8 點、驗收 17，D-132 五條）：frontmatter 只有 name、description，描述照 spec、150 字內、
不帶英文觸發詞；本文 3000 字內，有分辨、原則、骨架三段，原則每條帶理由與決策編號，沒有參數對照表與六步搬家；
用 ${CLAUDE_PLUGIN_ROOT} 叫 kb.py、不寫絕對路徑；兩份 references 在且 ≤150 行；tests/evals/manage.json 20 句各半、含 spec 給的句子。
字數總門檻在 test_budget，這裡只守形狀。
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
BODY_MAX = 3000
REFERENCE_MAX_LINES = 150
ABS_PATH = re.compile(r"(?<![A-Za-z$])[A-Za-z]:[\\/]|/Users/|/home/")
DESCRIPTION = ("管資料夾與原檔的位置都用這個：加資料夾、不讀某夾、拿掉、恢復讀、現在讀哪些、「報價夾搬到 E 槽了」「這份搬到 2026」"
               "「合約改個名字」「舊備忘幫我刪掉」「定稿放到王宅那夾」「這份要放哪」；我自己搬／刪好了、改檔案內容、整理成卡、實體改名、重建不用這個。")
DESCRIPTION = DESCRIPTION.replace("現在讀哪些、", "現在讀哪些、「不要讀私人那夾，我不想讓你看到」")  # 第二輪修：c7 試的候選
SHOULD = ("報價那個資料夾我搬到 E 槽了", "這份估價單幫我搬到 2026 那夾", "合約改名叫『信義王合約_簽約版』", "舊備忘幫我刪掉，那份早就沒用了",
          "把信義王整個資料夾搬到 2025 封存", "剛做好的定稿要放哪？你決定就好")
SHOULD_NOT = ("我自己把報價搬到信義王那夾了", "信義王改叫王宅", "把這份合約付款條件改 30 天", "信義王現在怎樣")
CREDENTIAL_LINE = "檔名像密碼或金鑰的預設不讀；要讓我讀哪一份跟我說「X 可以讀」"  # 密碼與守門（I-116 再查，a2 定）
COMMANDS_ROW = "| `commands/` | 尚未。build（一個指令走五步，D-190）與 upgrade、backup、restore、relocate、migrate、account（D-028），沒有 manage 指令 |"


def split(text):
    """回 (frontmatter 行, 本文)。"""
    lines = text.splitlines()
    assert lines and lines[0] == "---", "SKILL.md 開頭不是 ---"
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1:])


def section(body, title):
    start = body.index(f"## {title}")
    nxt = body.find("\n## ", start + 1)
    return body[start:nxt if nxt != -1 else len(body)]


class Frontmatter(unittest.TestCase):
    def setUp(self):
        self.front, self.body = split(SKILL.read_text(encoding="utf-8"))
        self.fields = dict(line.split(":", 1) for line in self.front)

    def test_only_name_and_description(self):
        self.assertEqual(set(self.fields), {"name", "description"})
        self.assertEqual(self.fields["name"].strip(), "manage")

    def test_description_is_spec_text(self):
        desc = self.fields["description"].strip()
        self.assertEqual(desc, DESCRIPTION)
        self.assertLessEqual(len(desc), DESC_MAX)
        self.assertIsNone(re.search(r"[A-Za-z]{2,}", desc), desc)  # E 槽這種單字母可以


class Body(unittest.TestCase):
    def setUp(self):
        self.body = split(SKILL.read_text(encoding="utf-8"))[1]

    def test_size_and_three_parts(self):
        self.assertLessEqual(len(self.body), BODY_MAX)
        for title in ("分辨", "原則", "骨架"):
            self.assertIn(f"## {title}", self.body)

    def test_no_parameter_table_or_six_steps(self):
        """驗收 17：本文沒有參數對照表與六步搬家（細節住 references，介面由程式輸出給）。"""
        self.assertNotIn("|---", self.body)
        self.assertNotIn("⑥", self.body)
        self.assertNotIn("做不到", section(self.body, "分辨"))

    def test_principles_have_reason_and_decision(self):
        rules = [ln for ln in section(self.body, "原則").splitlines() if re.match(r"^\d+\. \*\*", ln)]
        self.assertGreaterEqual(len(rules), 8)
        for ln in rules:
            with self.subTest(rule=ln[:20]):
                bold_end = ln.index("**", ln.index("**") + 2) + 2
                self.assertTrue(ln[bold_end:].strip(), "原則後面要一句理由")
                self.assertRegex(ln, r"[DI]-\d{3}")

    def test_principles_cover_spec_points(self):
        rules = section(self.body, "原則")
        for word in ("kb source-edit", "--check", "kb folders purge", "open", "where", "對不太上", "救不回"):
            with self.subTest(word=word):
                self.assertIn(word, rules)
        self.assertIn("清完的回答不提那夾裡的檔名與內容", rules)  # 疑義第 4 條

    def test_exclude_finds_folder_under_roots(self):
        """第一輪修第 1 條：不讀某夾不再「先 folders list 查、不在就說沒在讀」；在登記的根底下找夾名，多個問、找不到才說，exclude 自己判。"""
        line = next(ln for ln in section(self.body, "骨架").splitlines() if ln.startswith("- **不讀某夾**"))
        self.assertIn('folders find "<夾名>"', line)  # 第二輪修：Glob 會被守門擋，改用 kb 自己找
        self.assertIn("找到多個就問", line)
        self.assertIn("都找不到才說本來就沒在讀", line)
        self.assertIn("--json", line)  # 第三輪修：照 find --json 給的現成 exclude 指令跑，不自己拼引號
        self.assertIn("不自己拼引號", line)
        self.assertNotIn("查它在不在來源裡", line)

    def test_principles_3_and_8_merged(self):
        rules = section(self.body, "原則")
        self.assertEqual(rules.count("救不回"), 1)
        self.assertIn("D-174", rules[rules.index("3. **"):rules.index("4. **")])

    def test_distinguishes_and_hands_off(self):
        part = section(self.body, "分辨")
        for word in ("ingest", "note", "produce", "重建"):
            with self.subTest(word=word):
                self.assertIn(word, part)

    def test_calls_kb_through_plugin_root(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/kb.py", self.body)

    def test_credential_line(self):
        self.assertIn(CREDENTIAL_LINE, self.body)

    def test_common_section_says_traditional_chinese(self):
        self.assertIn("回答客戶一律繁體中文", section(self.body, "共通"))

    def test_no_absolute_paths(self):
        for text, name in ((self.body, "SKILL.md"),
                           *((p.read_text(encoding="utf-8"), p.name) for p in (SKILL_DIR / "references").glob("*.md"))):
            with self.subTest(file=name):
                self.assertIsNone(ABS_PATH.search(text), name)

    def test_references_exist_and_short(self):
        refs = set(re.findall(r"references/[^\s`)）、，。]+\.md", self.body))
        self.assertEqual(refs, {"references/sources.md", "references/files.md"})
        for ref in refs:
            p = SKILL_DIR / ref
            with self.subTest(reference=ref):
                self.assertTrue(p.is_file())
                self.assertLessEqual(len(p.read_text(encoding="utf-8").splitlines()), REFERENCE_MAX_LINES)

    def test_references_cover_new_subcommands(self):
        sources = (SKILL_DIR / "references" / "sources.md").read_text(encoding="utf-8")
        files = (SKILL_DIR / "references" / "files.md").read_text(encoding="utf-8")
        for word in ("## relocate", "## purge", "還有綜合卡寫著那夾的內容"):
            self.assertIn(word, sources)
        for word in ("## move", "## rename", "## trash", "## where", "## open", "OneDrive", "不同顆硬碟不代搬"):
            self.assertIn(word, files)
        self.assertNotIn("這版還沒有卡和備份要清", sources)


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

    def test_contains_spec_queries(self):
        by_query = {i["query"]: i["should_trigger"] for i in self.items}
        for q in SHOULD:
            with self.subTest(query=q):
                self.assertIs(by_query.get(q), True)
        for q in SHOULD_NOT:
            with self.subTest(query=q):
                self.assertIs(by_query.get(q), False)


class IngestHandOff(unittest.TestCase):
    def test_type_folder_line(self):
        """第一輪修第 10 條：型別底下還有東西時，實體換型別走 kb entity，散檔才問要不要走 manage 搬（不繞圈）。"""
        text = (ROOT / "skills" / "ingest" / "references" / "commands.md").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "先搬走再拿掉這列" in ln)
        self.assertIn("實體換型別用 kb entity，夾裡的散檔問她要不要幫她搬（走 manage）", line)


class RepoClaudeMd(unittest.TestCase):
    def test_commands_row_has_no_manage_command(self):
        """commands/ 列不寫 manage 指令，免得跟 skill 撞名。"""
        lines = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        self.assertIn(COMMANDS_ROW, lines)


if __name__ == "__main__":
    unittest.main()
