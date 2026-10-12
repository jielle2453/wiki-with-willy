"""kb.guard.decide：決策表每一格至少一個案例，直接呼叫，不經 stdin。"""
import os
import re
import shutil
import unittest

from tests._guard_fixture import ROOT, make_vault, payload
from kb import guard  # noqa: E402


class DecideBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.V, cls.raw = make_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decide(self, tool, value, cwd=None):
        return guard.decide(payload(tool, value, cwd or self.V))

    def expect(self, tool, value, decision, zone=None, code=None, cwd=None):
        d = self.decide(tool, value, cwd)
        with self.subTest(tool=tool, value=str(value)):
            self.assertEqual(d.decision, decision, d)
            if zone is not None:
                self.assertEqual(d.zone, zone, d)
            if code is not None:
                self.assertEqual(d.reason_code, code, d)
            if decision == "deny":
                # 理由句全中文、一句、沒有英文代碼（「AI 產出」是資料夾名，准）
                self.assertTrue(d.reason, d)
                self.assertEqual(set(re.findall(r"[A-Za-z_]+", d.reason)) - {"AI"}, set(), d.reason)
                self.assertEqual(len(d.ledger_rows), 1, d)
            if decision == "allow":
                self.assertEqual(d.ledger_rows, [], d)
        return d


class FileToolsVault(DecideBase):
    def test_z1_card_read_write_allow(self):
        card = self.V / "案子" / "信義王" / "信義王.md"
        self.expect("Read", card, "allow", "Z1")
        self.expect("Write", card, "allow", "Z1")
        self.expect("Edit", card, "allow", "Z1")
        self.expect("MultiEdit", card, "allow", "Z1")

    def test_z1_new_entity_card_allow(self):
        self.expect("Write", self.V / "案子" / "新案" / "新案.md", "allow", "Z1")

    def test_z2_entity_file_read_allow_write_deny(self):
        doc = self.V / "案子" / "信義王" / "合約.docx"
        self.expect("Read", doc, "allow", "Z2")
        self.expect("Write", doc, "deny", "Z2", "entity_file")
        self.expect("Edit", self.V / "案子" / "信義王" / "sub" / "備註.md", "deny", "Z2", "entity_file")
        self.expect("NotebookEdit", self.V / "案子" / "信義王" / "分析.ipynb", "deny", "Z2", "entity_file")

    def test_z3_ai_output_allow_both(self):
        p = self.V / "AI 產出" / "x.md"
        self.expect("Read", p, "allow", "Z3")
        self.expect("Write", p, "allow", "Z3")
        self.expect("Glob", self.V / "AI 產出", "allow", "Z3")

    def test_z4_inbox_read_allow_write_deny(self):
        p = self.V / "收件匣" / "x.txt"
        self.expect("Read", p, "allow", "Z4")
        self.expect("Write", p, "deny", "Z4", "inbox")

    def test_z5_index_allow_both(self):
        p = self.V / "檔案索引" / "x.md"
        self.expect("Read", p, "allow", "Z5")
        self.expect("Write", p, "allow", "Z5")

    def test_z6_system_render_read_allow_write_deny(self):
        p = self.V / "系統" / "待處理.md"
        self.expect("Read", p, "allow", "Z6")
        self.expect("Write", p, "deny", "Z6", "system_render")

    def test_z7_protected_read_allow_write_deny(self):
        for p in (self.V / ".claude" / "settings.json", self.V / "CLAUDE.md", self.V / ".system" / "sources.json",
                  self.V / ".system" / "ledger.tsv"):
            self.expect("Read", p, "allow", "Z7")
            self.expect("Edit", p, "deny", "Z7", "protected")

    def test_z8_other_first_level_read_allow_write_deny(self):
        self.expect("Read", self.V / "readme.txt", "allow", "Z8")
        self.expect("Write", self.V / "readme.txt", "deny", "Z8")
        self.expect("Write", self.V / "案子" / "備忘.txt", "deny")
        self.expect("Grep", self.V, "allow", "Z8")

    def test_glob_without_path_uses_cwd(self):
        d = guard.decide({"tool_name": "Glob", "tool_input": {"pattern": "*.md"}, "cwd": str(self.V / "AI 產出")})
        self.assertEqual((d.decision, d.zone), ("allow", "Z3"))

    def test_relative_path_joins_cwd(self):
        self.expect("Write", "收件匣/x.txt", "deny", "Z4", "inbox")
        self.expect("Read", "../raw/行政/薪資/s.xlsx", "deny", "Z10", "excluded")


class FileToolsSources(DecideBase):
    def test_z9_registered_read_allow_write_deny(self):
        p = self.raw / "客戶案件" / "a.txt"
        self.expect("Read", p, "allow", "Z9")
        self.expect("Glob", self.raw / "客戶案件", "allow", "Z9")
        self.expect("Grep", self.raw / "客戶案件", "allow", "Z9")
        self.expect("Write", p, "deny", "Z9", "readonly_source")
        self.expect("Edit", self.raw / "行政" / "請款" / "r.docx", "deny", "Z9", "readonly_source")

    def test_z10_excluded_deny_both_kinds(self):
        for p in (self.raw / "行政" / "薪資" / "s.xlsx", self.raw / "行政" / "舊檔" / "o.doc", self.raw / "行政" / "薪資"):
            self.expect("Read", p, "deny", "Z10", "excluded")
            self.expect("Write", p, "deny", "Z10", "excluded")
            self.expect("Glob", p, "deny", "Z10", "excluded")
        self.assertIn("這個夾你說過不讀", self.decide("Read", self.raw / "行政" / "薪資" / "s.xlsx").reason)

    def test_z11_other_pass(self):
        self.expect("Read", "C:/Windows/notepad.exe", "pass", "Z11")
        self.expect("Write", "C:/Windows/notepad.exe", "pass", "Z11")
        self.expect("Read", self.tmp / "raw" / "別的" / "x.txt", "pass", "Z11")

    def test_five_spellings_of_excluded_file_all_deny(self):
        p = str(self.raw / "行政" / "薪資" / "s.xlsx").replace("\\", "/")
        drive, rest = p[0], p[2:]
        forms = [p, p[0].lower() + p[1:], p.replace("/", "\\"), f"/{drive.lower()}{rest}", "../raw/行政/薪資/s.xlsx"]
        for f in forms:
            self.expect("Read", f, "deny", "Z10", "excluded")

    def test_not_in_vault_is_pass(self):
        self.expect("Read", self.raw / "客戶案件" / "a.txt", "pass", cwd=self.tmp)
        self.expect("Read", self.raw / "行政" / "薪資" / "s.xlsx", "pass", cwd=self.tmp)

    def test_unresolved_source_pass_with_ledger_row(self):
        os.environ.pop("KB_TEST_NOPE", None)
        extra = {"id": "src-x", "alias": "外接", "spec": "${KB_TEST_NOPE}/x", "excludes": []}
        src = self.V / ".system" / "sources.json"
        original = src.read_text(encoding="utf-8")
        import json
        data = json.loads(original)
        data["sources"].append(extra)
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        try:
            d = self.decide("Read", self.tmp / "elsewhere.txt")
            self.assertEqual(d.decision, "pass")
            self.assertEqual([r["reason_code"] for r in d.ledger_rows], ["unresolved"])
            self.expect("Read", self.raw / "客戶案件" / "a.txt", "allow", "Z9")
        finally:
            src.write_text(original, encoding="utf-8")


class Shell(DecideBase):
    def test_single_kb_call_allow(self):
        cmd = f'python "{ROOT}/scripts/kb.py" version'
        self.expect("Bash", cmd, "allow")
        self.expect("PowerShell", cmd, "allow")
        self.expect("Bash", cmd, "allow", cwd=self.tmp)

    def test_chained_kb_call_not_allowed(self):
        d = self.decide("Bash", f'python "{ROOT}/scripts/kb.py" version && rm -rf {self.raw}/客戶案件')
        self.assertEqual(d.decision, "deny")

    def test_read_excluded_three_spellings_deny(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"cat {p}/行政/薪資/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", "type " + p.replace("/", "\\") + "\\行政\\薪資\\s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", f"cat /{p[0].lower()}{p[2:]}/行政/薪資/s.xlsx", "deny", "Z10", "excluded")
        self.expect("PowerShell", f'Get-Content "{p}/行政/舊檔/o.doc"', "deny", "Z10", "excluded")

    def test_write_to_source_deny(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"echo hi > {p}/客戶案件/a.txt", "deny", "Z9", "readonly_source")
        self.expect("PowerShell", "Set-Content " + p.replace("/", "\\") + "\\客戶案件\\a.txt 'x'", "deny", "Z9", "readonly_source")
        self.expect("Bash", f"rm {p}/客戶案件/a.txt", "deny", "Z9", "readonly_source")

    def test_write_to_vault_blocked_zones_deny(self):
        v = str(self.V).replace("\\", "/")
        self.expect("Bash", f"echo hi > {v}/收件匣/x.txt", "deny", "Z4", "inbox")
        self.expect("Bash", f"cp a {v}/案子/信義王/合約.docx", "deny", "Z2", "entity_file")
        self.expect("Bash", f"echo x > {v}/系統/待處理.md", "deny", "Z6", "system_render")
        self.expect("Bash", f"echo x > {v}/CLAUDE.md", "deny", "Z7", "protected")
        self.expect("Bash", f"echo x >> {v}/.claude/settings.json", "deny", "Z7", "protected")

    def test_write_to_allowed_zone_or_read_source_is_pass(self):
        v = str(self.V).replace("\\", "/")
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"echo hi > {v}/案子/信義王/信義王.md", "pass")
        self.expect("Bash", f'echo hi > "{v}/AI 產出/x.md"', "pass")  # 有空白的路徑要加引號，shell 也是這樣
        self.expect("Bash", f"ls {p}/客戶案件", "pass")
        self.expect("Bash", f"cat {p}/客戶案件/a.txt", "pass")

    def test_write_without_parsable_path_deny_unparsed(self):
        d = self.expect("Bash", "rm $X", "deny", code="unparsed")
        self.assertEqual(d.ledger_rows[0]["reason_code"], "unparsed")
        self.expect("Bash", "mkdir foo", "deny", code="unparsed")

    def test_harmless_commands_pass(self):
        self.expect("Bash", "git status", "pass")
        self.expect("Bash", "python --version", "pass")
        self.expect("Bash", "ls -la 2>/dev/null", "pass")
        self.expect("PowerShell", "Get-ChildItem", "pass")

    def test_outside_vault_only_kb_call_matters(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"cat {p}/行政/薪資/s.xlsx", "pass", cwd=self.tmp)
        self.expect("Bash", "rm $X", "pass", cwd=self.tmp)
        self.expect("Bash", f'python "{ROOT}/scripts/kb.py" version', "allow", cwd=self.tmp)


class Zones(unittest.TestCase):
    def test_every_table_row_has_both_columns(self):
        for z in ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8", "Z9", "Z10", "Z11"):
            with self.subTest(zone=z):
                self.assertIn("read", guard.ZONES[z])
                self.assertIn("write", guard.ZONES[z])
                self.assertIn(guard.ZONES[z]["read"][0], ("allow", "deny", "pass"))
                self.assertIn(guard.ZONES[z]["write"][0], ("allow", "deny", "pass"))


if __name__ == "__main__":
    unittest.main()
