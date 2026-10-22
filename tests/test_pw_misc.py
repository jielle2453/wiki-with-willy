"""密碼與守門的零星幾處：kb init 寫 .system/vault.json（設計 session 2026-10-23）、kb folders add 的密碼檔那行（在 test_scripts_folders_cli）、
manage SKILL.md 加資料夾那句（I-116 再查，a2 定）。"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._guard_fixture import ROOT
from tests.test_scripts_init_cli import run_kb as run_init

TAIL = "；要讓我讀哪一份跟我說「X 可以讀」"


class InitVaultId(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbinitpw-")).resolve()
        self.V = self.tmp / "知識庫"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_writes_vault_id_once(self):
        r = run_init("init", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads((self.V / ".system" / "vault.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertRegex(data["vault_id"], r"^kb-[0-9a-f]{8}$")
        run_init("init", str(self.V), cwd=self.tmp)
        self.assertEqual(json.loads((self.V / ".system" / "vault.json").read_text(encoding="utf-8")), data)


class ManageSkillLine(unittest.TestCase):
    def test_add_folder_line(self):
        body = (ROOT / "skills" / "manage" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("檔名像密碼或金鑰的預設不讀；要讓我讀哪一份跟我說「X 可以讀」", body)
        self.assertNotIn("這版還不能開放", body)


class A2Followups(unittest.TestCase):
    """a2 回疑義（spec 最後一條）：answer 骨架指到問密碼那節；README 講 Python 壞了連別的專案都動不了（onFailure: block）。"""

    def test_answer_skeleton_points_to_password_section(self):
        body = (ROOT / "skills" / "answer" / "SKILL.md").read_text(encoding="utf-8")
        skeleton = body[body.index("## 骨架"):body.index("## 共通")]
        self.assertIn("問密碼：`references/find.md` 第八節", skeleton)

    def test_readme_python_broken_blocks_everything(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 壞了時，連別的專案的 Claude 也動不了；先修 Python，或在外掛畫面暫時停用 wiki-with-willy", readme)
        self.assertNotIn("Claude Code 會當作沒有 hook，照常放行", readme)


class NoOldPhrases(unittest.TestCase):
    """驗收 15：skills 與程式裡「這版還不能開放」「這版記不起來」「等密碼與守門那一版」為 0（只在 design/）。"""

    def test_grep_zero(self):
        old = re.compile("這版還不能開放|這版記不起來|等密碼與守門那一版|等「密碼與守門」那一版|要等密碼與守門")
        hits = []
        for base in ("skills", "scripts", "hooks", "rules", "skeleton"):
            for p in (ROOT / base).rglob("*"):
                if p.is_file() and p.suffix in (".md", ".py", ".json"):
                    if old.search(p.read_text(encoding="utf-8")):
                        hits.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
