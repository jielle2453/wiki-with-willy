"""_lib/output（produce 第 2 點、實作回報裁定 2）：目的地寫法、同名加 (2)、來源搬或複製或拒絕、網址只收 claude.ai。
save、link、drop-draft 的整段流程在 test_scripts_output_cli。"""
import shutil
import unittest

from tests.test_lib_inventory_scan import make_inv_vault
from kb._lib import output  # noqa: E402


class Paths(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_output_rel_forms(self):
        for raw, cwd in (("AI 產出/報價/a.xlsx", self.tmp), ("AI 產出\\報價\\a.xlsx", self.tmp),
                         (str(self.V / "AI 產出" / "報價" / "a.xlsx"), self.tmp), ("報價/a.xlsx", self.V / "AI 產出")):
            with self.subTest(raw=raw):
                self.assertEqual(output.output_rel(self.V, raw, cwd), "AI 產出/報價/a.xlsx")
        for raw in ("案子/a.xlsx", "AI 產出", "AI 產出/../系統/a.xlsx", str(self.tmp / "a.xlsx"), ".system/x.xlsx"):
            with self.subTest(raw=raw), self.assertRaises(output.Refused) as c:
                output.output_rel(self.V, raw, self.V)
            self.assertEqual(str(c.exception), output.ONLY_OUTPUT)

    def test_free_rel(self):
        (self.V / "AI 產出" / "a.xlsx").write_bytes(b"1")
        self.assertEqual(output.free_rel(self.V, "AI 產出/a.xlsx"), "AI 產出/a (2).xlsx")
        (self.V / "AI 產出" / "a (2).xlsx").write_bytes(b"2")
        self.assertEqual(output.free_rel(self.V, "AI 產出/a.xlsx"), "AI 產出/a (3).xlsx")
        self.assertEqual(output.free_rel(self.V, "AI 產出/b.xlsx"), "AI 產出/b.xlsx")

    def test_source_mode(self):
        ws = self.V / ".system" / "temp" / "office" / "t1"
        ws.mkdir(parents=True)
        self.assertEqual(output.source_mode(self.V, ws / "a.xlsx"), "move")
        self.assertEqual(output.source_mode(self.V, self.tmp / "下載" / "a.xlsx"), "copy")
        for inside in (self.V / "案子" / "說明.txt", self.V / "AI 產出" / "x.md", self.V / ".system" / "profile.json"):
            with self.subTest(inside=inside), self.assertRaises(output.Refused) as c:
                output.source_mode(self.V, inside)
            self.assertEqual(str(c.exception), output.IN_VAULT)

    def test_url(self):
        output.check_url("https://claude.ai/artifact/abc")
        for bad in ("https://claude.ai.evil.com/x", "http://claude.ai/x", "https://example.com/claude.ai/x",
                    'https://claude.ai/x" && rm', "https://claude.ai/x y"):
            with self.subTest(bad=bad), self.assertRaises(output.Refused):
                output.check_url(bad)


if __name__ == "__main__":
    unittest.main()
