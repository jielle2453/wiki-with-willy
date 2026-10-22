"""守門（produce 第 3 點、驗收 10；實作回報裁定 1、3）：
- Bash／PowerShell 指令抽出的路徑落在 AI 產出（Z3），整句不是單一 kb 呼叫 → 讀寫都擋，新理由句；
- 原檔區 Z2、Z9 擋寫的理由句指到 kb office copy；Z10／Z10b 照舊「你說過不讀」；
- 檔案工具（Write／Edit）寫 .system/temp/office/ 底下放行（不備份）；指令寫照擋，.system 其他地方照擋，.. 跳出去照 Z7。"""
import os
import shutil
import unittest

from tests._guard_fixture import KB, ROOT, make_vault, payload
from kb import guard  # noqa: E402

OUTPUT_REASON = "AI 產出的檔用 kb output save 放進來；要轉 PDF 或看版面用 kb office"
SOURCE_REASON = "客戶原檔只讀；要改先用 kb office copy 拿一份副本"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.V, cls.raw = make_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        cls.ws = cls.V / ".system" / "temp" / "office" / "20261022-101010-test"
        cls.ws.mkdir(parents=True)
        (cls.V / "AI 產出" / "a.docx").write_bytes(b"x")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decide(self, tool, value, cwd=None):
        return guard.decide(payload(tool, value, cwd or self.V))


class ShellOutput(Base):
    def test_scripts_touching_output_denied(self):
        v = self.V.as_posix()
        for tool, cmd in (("Bash", f'node x.js "{v}/AI 產出/a.docx"'),
                          ("Bash", f'python x.py "{v}/AI 產出/a.xlsx"'),
                          ("Bash", 'node build.js "AI 產出/報告/進度報告.docx"'),
                          ("Bash", f'soffice --headless --convert-to pdf "{v}/AI 產出/a.docx"'),
                          ("Bash", f'cat "{v}/AI 產出/a.docx"'),
                          ("Bash", 'ls "AI 產出"'),
                          ("Bash", 'cat "AI 產出"/*.docx'),
                          ("Bash", 'cd "AI 產出" && node build.js out.docx'),
                          ("Bash", 'node build.js --out="AI 產出/新夾/x.docx"'),
                          ("Bash", "node build.js AI\\ 產出/新夾/x.docx"),
                          ("Bash", 'OUT="AI 產出/x.docx" node build.js'),
                          ("PowerShell", 'node build.js "AI 產出\\新夾\\x.docx"'),
                          ("PowerShell", f'Get-Content "{v}/AI 產出/a.docx"'),
                          ("Bash", f'python x.py "{v}/AI 產出/a.xlsx" && echo done'),
                          ("Bash", f'python "{KB}" output save "{v}/AI 產出/a.docx" --to "AI 產出/b.docx"; echo x')):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.zone, d.reason_code, d.reason), ("deny", "Z3", "output_shell", OUTPUT_REASON))
                self.assertEqual(len(d.ledger_rows), 1)

    def test_single_kb_call_still_allowed(self):
        v = self.V.as_posix()
        d = self.decide("Bash", f'python "{KB}" output save "{self.ws.as_posix()}/a.xlsx" --to "AI 產出/報價/a.xlsx"')
        self.assertEqual(d.decision, "allow")
        d = self.decide("Bash", f'python "{KB}" office pdf "{v}/AI 產出/a.docx"')
        self.assertEqual(d.decision, "allow")

    def test_read_tool_on_output_allowed(self):
        self.assertEqual(self.decide("Read", self.V / "AI 產出" / "a.docx").decision, "allow")
        d = self.decide("Write", self.V / "AI 產出" / "草稿（草稿）.md")
        self.assertEqual((d.decision, d.zone), ("allow", "Z3"))

    def test_other_shell_unaffected(self):
        self.assertEqual(self.decide("Bash", "node --version").decision, "pass")
        self.assertEqual(self.decide("Bash", f'python "{self.ws.as_posix()}/make.py"').decision, "pass")


class SourceReason(Base):
    def test_copy_original_into_vault_denied_with_office_copy(self):
        cmd = f'cp "{self.raw.as_posix()}/客戶案件/a.txt" "{self.V.as_posix()}/.system/temp/x.txt"'
        d = self.decide("Bash", cmd)
        self.assertEqual((d.decision, d.zone, d.reason_code, d.reason), ("deny", "Z9", "readonly_source", SOURCE_REASON))
        d = self.decide("Write", self.raw / "客戶案件" / "a.txt")
        self.assertEqual((d.decision, d.reason), ("deny", SOURCE_REASON))

    def test_entity_file_mentions_office_copy(self):
        d = self.decide("Write", self.V / "案子" / "信義王" / "合約.docx")
        self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z2", "entity_file"))
        self.assertIn("kb office copy", d.reason)

    def test_excluded_keeps_its_reason(self):
        d = self.decide("Write", self.raw / "行政" / "舊檔" / "o.doc")
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"))
        self.assertEqual(d.reason, guard.ZONES["Z10"]["write"][2])
        self.assertNotIn("office", d.reason)


class Workspace(Base):
    """實作回報裁定 1。"""

    def test_file_tools_write_workspace_allowed(self):
        for tool in ("Write", "Edit", "MultiEdit"):
            for target in (self.ws / "build.js", self.ws / "合約_unpacked" / "word" / "document.xml",
                           self.V / ".system" / "temp" / "office" / "x.py"):
                with self.subTest(tool=tool, target=target):
                    d = self.decide(tool, target)
                    self.assertEqual((d.decision, d.zone, d.ledger_rows), ("allow", "Z7", []))
                    self.assertNotIn(d.zone, ("Z1", "Z3", "Z5"), "不經回合備份")

    def test_relative_and_dotdot(self):
        d = self.decide("Write", ".system/temp/office/20261022-101010-test/a.py")
        self.assertEqual(d.decision, "allow")
        for target in (self.ws / ".." / ".." / ".." / "profile.json", self.ws / ".." / ".." / "x.py",
                       self.V / ".system" / "temp" / "officex" / "a.py", self.V / ".system" / "temp" / "office",
                       self.V / ".system" / "temp" / "a.py", self.V / ".system" / "inventory.json"):
            with self.subTest(target=target):
                d = self.decide("Write", target)
                self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z7", "protected"))

    def test_notebook_and_shell_still_denied(self):
        d = self.decide("NotebookEdit", self.ws / "a.ipynb")
        self.assertEqual((d.decision, d.zone), ("deny", "Z7"))
        for cmd in (f'echo x > "{self.ws.as_posix()}/a.py"', f'cp /tmp/a.py "{self.ws.as_posix()}/a.py"'):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decide("Bash", cmd).decision, "deny")

    def test_read_workspace_allowed(self):
        self.assertEqual(self.decide("Read", self.ws / "a.png").decision, "allow")

    def test_junction_out_of_workspace_denied(self):
        if os.name != "nt":
            self.skipTest("junction 只在 Windows")
        import subprocess
        link = self.ws / "跳出去"
        target = self.tmp / "外面"
        target.mkdir(exist_ok=True)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode != 0:
            self.skipTest("建不了 junction")
        try:
            d = self.decide("Write", link / "a.py")
            self.assertNotEqual(d.reason, "工作區", "連結跳出工作區的不算工作區（寫到知識庫外走一般流程）")
            self.assertFalse(guard.in_workspace(guard.normalize(str(link / "a.py"), None),
                                                guard.normalize(str(self.V), None)))
        finally:
            os.rmdir(link)



class SecurityReview(Base):
    """收尾 security-review 的修正：_lo_profile 不算工作區；路徑中間的段結尾有空白或點不算；PowerShell 的 -Param:值 與 /out:值；
    _touches_output 出錯一律擋。"""

    def test_lo_profile_not_writable(self):
        for target in (self.V / ".system/temp/office/_lo_profile/user/registrymodifications.xcu",
                       self.V / ".system/temp/office/_lo_profile/user/basic/Standard/Module1.xba"):
            with self.subTest(target=target):
                d = self.decide("Write", target)
                self.assertEqual((d.decision, d.zone), ("deny", "Z7"))

    def test_trailing_space_or_dot_segment_not_workspace(self):
        v = self.V.as_posix()
        for target in (f"{v}/.system /temp/office/t/x.js", f"{v}/.system/temp. /office/t/x.js",
                       f"{v}/.system/temp/office./t/x.js"):
            with self.subTest(target=target):
                self.assertNotEqual(self.decide("Write", target).reason, "工作區")

    def test_param_colon_forms(self):
        v = self.V.as_posix()
        for tool, cmd in (("PowerShell", f'Get-Content -Path:"{v}/AI 產出/a.docx"'),
                          ("PowerShell", 'node b.js -Out:"AI 產出/x.docx"'),
                          ("Bash", 'tool.exe /out:"AI 產出/x.docx"')):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "output_shell"))

    def test_exception_in_output_scan_denies(self):
        from unittest import mock
        with mock.patch.object(guard, "_touches_output", side_effect=RuntimeError("壞了")):
            d = self.decide("Bash", "node b.js")
        self.assertEqual((d.decision, d.reason_code), ("deny", "output_shell"))


if __name__ == "__main__":
    unittest.main()
