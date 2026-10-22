"""kb office run 跑完檢查（密碼與守門第 1b 點、D-188、驗收 12c）與待處理新種類 script_touched：
替身腳本改了來源裡一份檔的修改時間 → 第一行「剛才的程式動到了…」、待處理多一條 script_touched（渲染進 待處理.md）；
只寫工作區的腳本沒有那句。另驗「有密碼」的待處理問法（PDF 與 Word、Excel 分開講）。"""
import shutil
import unittest

from tests import _cards
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import pending, turn  # noqa: E402

TOUCH = "剛才的程式動到了"


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.t = turn.start(self.V, "sid-office", "做報告")["turn"]
        self.ws = self.V / ".system" / "temp" / "office" / self.t
        self.ws.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, body):
        script = self.ws / "make.py"
        script.write_text(body, encoding="utf-8")
        r = run_kb("office", "run", str(script), cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip().splitlines()

    def test_touching_a_source_file_is_reported_and_recorded(self):
        target = self.raw / _cards.MEETING
        lines = self.run_script(f"import os\nos.utime(r'{target}', None)\nprint('done')\n")
        self.assertEqual(lines[0], f"{TOUCH} 「會議紀錄_1.docx」，我沒有要動它；說「退回上一版」看救不救得回")
        items = [i for i in pending.load(self.V)["items"] if i.get("kind") == "script_touched"]
        self.assertEqual(len(items), 1)
        md = (self.V / "系統" / "待處理.md").read_text(encoding="utf-8")
        self.assertIn("## 程式動到的檔（1）", md)
        self.assertIn("會議紀錄_1.docx", md)

    def test_workspace_only_script_is_quiet(self):
        lines = self.run_script("import os\nopen(os.path.join(os.environ['OUT_DIR'], 'r.docx'), 'w').write('x')\n")
        self.assertFalse(any(TOUCH in ln for ln in lines), lines)
        self.assertFalse([i for i in pending.load(self.V)["items"] if i.get("kind") == "script_touched"])


class LockedAsk(unittest.TestCase):
    def test_locked_ask_mentions_pdf_and_office(self):
        item = {"kind": "unreadable", "text": "「a.pdf」讀不到", "reason": "有密碼"}
        self.assertEqual(pending.ask_of(item), "PDF 給我密碼就能打開（要我記住也可以）；Word、Excel 請另存一份沒密碼的，或跟我說先跳過。")


if __name__ == "__main__":
    unittest.main()
