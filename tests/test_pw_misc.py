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


class Round28bDocs(unittest.TestCase):
    """測試 28 第二輪 E：skill-reviewer 的三個 Major。"""

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_majors(self):
        self.assertIn("回答裡完全不出現密碼值，唯一例外是 `kb unlock get` 印出的那一行", self.read("skills/note/references/password.md"))
        carding = self.read("skills/ingest/references/carding.md")
        self.assertIn("回答與 `kb turn note` 都不寫任何值", carding[carding.index("## 十、"):])
        find = self.read("skills/answer/references/find.md")
        part = find[find.index("## 八、"):]
        for want in ("`mentions`", "`kb cards list --entity", "不用 Grep"):
            self.assertIn(want, part)


class D212Docs(unittest.TestCase):
    """D-212：README 的「kb.py 不用按允許」補例外；note 的 password.md、ruling.md 說這幾個動作會跳出允許詢問。"""

    def test_docs(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`kb.py` 不用按允許（顯示存好的密碼、讓 AI 讀密碼檔、讓同一種檔自動開、用存好的密碼開沒授權過的檔，這四種要按允許）", readme)
        for rel in ("skills/note/references/password.md", "skills/note/references/ruling.md"):
            self.assertIn("這幾個動作會跳出允許詢問，請客戶按允許", (ROOT / rel).read_text(encoding="utf-8"), rel)



class Round28fDocs(unittest.TestCase):
    """第五輪 S1、S3：password.md 寫看到允許詢問被拒或沒按就停、不換寫法重試；R-09 後半改成客戶說要記才存、沒說就問一句。"""

    def test_s1_and_s3(self):
        pw = (ROOT / "skills" / "note" / "references" / "password.md").read_text(encoding="utf-8")
        self.assertIn("「需要客戶按允許才會執行：」", pw)
        self.assertIn("不換寫法重試", pw)
        self.assertIn("客戶說要記（「記下來」「記住」）就直接存", pw)
        core = (ROOT / "rules" / "core.md").read_text(encoding="utf-8")
        self.assertIn("客戶說要記才存（走 Windows 憑證管理員，kb unlock save），沒說就問一句", core)
        self.assertNotIn("要存就問客戶", core)


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
