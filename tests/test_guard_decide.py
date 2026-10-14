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

    def test_read_source_is_pass(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"ls {p}/客戶案件", "pass")
        self.expect("Bash", f"cat {p}/客戶案件/a.txt", "pass")

    def test_shell_write_to_backed_up_zones_is_deny_shell_write(self):
        """事務層：指令寫綜合卡、AI 產出、檔案索引都 deny shell_write（指令寫不經備份也不經登記，I-027、D-046 補）。"""
        v = str(self.V).replace("\\", "/")
        d = self.expect("Bash", f"echo hi > {v}/案子/信義王/信義王.md", "deny", "Z1", "shell_write")
        self.assertIn("用指令改的退不回來", d.reason)
        self.expect("Bash", f'echo hi > "{v}/AI 產出/x.md"', "deny", "Z3", "shell_write")  # 有空白的路徑要加引號，shell 也是這樣
        self.expect("Bash", f'sed -i s/a/b/ "{v}/檔案索引/x.md"', "deny", "Z5", "shell_write")
        self.expect("PowerShell", f'Set-Content "{v}/檔案索引/x.md" x', "deny", "Z5", "shell_write")

    def test_shell_read_of_backed_up_zones_still_pass(self):
        v = str(self.V).replace("\\", "/")
        self.expect("Bash", f"cat {v}/案子/信義王/信義王.md", "pass")
        self.expect("Bash", f'cat "{v}/AI 產出/x.md"', "pass")
        self.expect("Bash", f'cat "{v}/檔案索引/x.md"', "pass")

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


class ReviewGaps(DecideBase):
    """審閱發現的漏洞。"""

    def test_trailing_dot_spelling_still_denied(self):
        self.expect("Read", str(self.raw / "行政" / "薪資.") + "/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Write", str(self.raw / "客戶案件.") + "/a.txt", "deny", "Z9", "readonly_source")
        self.expect("Bash", f"cat {self.raw}/行政/薪資./s.xlsx", "deny", "Z10", "excluded")

    def test_quoted_wrapper_write_denied(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f'bash -c "rm {p}/客戶案件/a.txt"', "deny", "Z9", "readonly_source")
        self.expect("PowerShell", f'powershell -c "Remove-Item {p}/客戶案件/a.txt"', "deny", "Z9", "readonly_source")
        self.expect("Bash", f'bash -c "echo hi > {self.V}/收件匣/x.txt"', "deny", "Z4", "inbox")

    def test_cd_then_relative_read_of_excluded_denied(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"cd {p}/行政 && cat 薪資/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", "cd ../raw/行政 && cat 薪資/s.xlsx", "deny", "Z10", "excluded")

    def test_glob_over_source_with_excludes_denied(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"cat {p}/行政/*/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", f"cat {p}/行政/薪?/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", f"ls {p}/客戶案件/*", "pass")

    def test_powershell_alias_write_denied(self):
        p = str(self.raw).replace("/", "\\")
        self.expect("PowerShell", f"ri {p}\\客戶案件\\a.txt", "deny", "Z9", "readonly_source")
        self.expect("PowerShell", f"sc {p}\\客戶案件\\a.txt x", "deny", "Z9", "readonly_source")

    def test_ancestor_write_denied(self):
        self.expect("Bash", f"rm -rf {self.raw}", "deny", "Z10b", "excluded_inside")  # 上層含排除夾，S3 的 Z10b 先命中
        self.expect("Bash", f"rm -rf {self.tmp}", "deny")
        self.expect("Bash", f"ls {self.tmp}", "deny", "Z10b", "excluded_inside")  # 範圍底下有排除夾（S3）

    def test_python_c_write_reports_source_zone(self):
        p = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"python -c \"open('{p}/客戶案件/a.txt','w').write('x')\"", "deny", "Z9", "readonly_source")

    def test_glob_tool_pattern_is_inspected(self):
        d = guard.decide({"tool_name": "Glob", "tool_input": {"pattern": f"{self.raw}/行政/薪資/*", "path": str(self.V)}, "cwd": str(self.V)})
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"))
        d = guard.decide({"tool_name": "Glob", "tool_input": {"pattern": "../raw/行政/薪資/**", "path": str(self.V)}, "cwd": str(self.V)})
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"))
        d = guard.decide({"tool_name": "Glob", "tool_input": {"pattern": "**/*.md", "path": str(self.V)}, "cwd": str(self.V)})
        self.assertEqual((d.decision, d.zone), ("allow", "Z8"))

    def test_git_bash_cwd_finds_vault(self):
        v = str(self.V).replace("\\", "/")
        cwd = "/" + v[0].lower() + v[2:]
        self.expect("Read", self.raw / "行政" / "薪資" / "s.xlsx", "deny", "Z10", "excluded", cwd=cwd)



class Round1(DecideBase):
    """第一輪測試後修正：驗收 23–28、30。"""

    def kb(self):
        return f'"{ROOT}/scripts/kb.py" version'

    def test_23_s1_interpreter_written_into_vault_is_not_allowed(self):
        fake = self.V / "檔案索引" / "python"
        self.expect("Write", fake, "allow", "Z5")
        fake.write_text("evil", encoding="utf-8")
        d = self.decide("Bash", f'{fake} {self.kb()}')
        self.assertNotEqual(d.decision, "allow", d)
        d = self.decide("Bash", f'檔案索引/python {self.kb()}')
        self.assertNotEqual(d.decision, "allow", d)
        d = self.decide("Bash", f'{self.raw}/客戶案件/python.exe {self.kb()}')
        self.assertNotEqual(d.decision, "allow", d)
        self.expect("Bash", "python " + self.kb(), "allow")

    def test_s1_plugin_root_env_var_is_ignored(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(self.tmp)
        try:
            (self.tmp / "scripts").mkdir(exist_ok=True)
            (self.tmp / "scripts" / "kb.py").write_text("print(1)", encoding="utf-8")
            d = self.decide("Bash", f'python "{self.tmp}/scripts/kb.py" version')
            self.assertNotEqual(d.decision, "allow", d)
            self.expect("Bash", "python " + self.kb(), "allow")
        finally:
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)

    def test_24_s2_powershell_subexpression_not_allowed(self):
        for tail in (" @(Remove-Item x)", "; rm x", " (rm x)", " $(rm x)"):
            d = self.decide("PowerShell", "python " + self.kb() + tail)
            with self.subTest(tail=tail):
                self.assertNotEqual(d.decision, "allow", d)

    def test_25_s3_file_tool_over_folder_containing_exclude(self):
        self.expect("Grep", self.raw / "行政", "deny", "Z10b", "excluded_inside")
        self.expect("Glob", self.raw, "deny", "Z10b", "excluded_inside")
        self.expect("Read", self.raw / "行政", "deny", "Z10b", "excluded_inside")
        self.expect("Grep", self.raw / "行政" / "請款", "allow", "Z9")
        self.expect("Grep", self.raw / "客戶案件", "allow", "Z9")
        d = guard.decide({"tool_name": "Grep", "tool_input": {"path": str(self.raw / "行政" / "請款"), "glob": "../薪資/*"}, "cwd": str(self.V)})
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"))

    def test_26_s3_shell_over_folder_containing_exclude(self):
        r = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"grep -r x {r}/行政", "deny", "Z10b", "excluded_inside")
        self.expect("Bash", f"cat {r}/*/薪資/s.xlsx", "deny")
        self.expect("Bash", f"cd {r}/行政 && cat */s.xlsx", "deny")
        self.expect("Bash", f"find {r} -name '*.xlsx'", "deny", "Z10b", "excluded_inside")
        self.expect("PowerShell", f"Set-Location -Path {r}/行政; Get-Content 薪資/s.xlsx", "deny", "Z10", "excluded")
        self.expect("Bash", f"ls {r}/客戶案件", "pass")
        self.expect("Bash", f"ls {r}/客戶案件/*", "pass")

    def test_27_b1_reading_md_is_not_write(self):
        v = str(self.V).replace("\\", "/")
        self.expect("Bash", f"cat {v}/CLAUDE.md", "pass")
        self.expect("Bash", f"head {v}/系統/待處理.md", "pass")
        self.expect("Bash", f"grep x {v}/收件匣/a.md", "pass")
        self.expect("Bash", f"type {self.raw}\\行政\\請款\\notes.md", "pass")
        self.expect("Bash", "cat CLAUDE.md", "pass")

    def test_28_b3_non_path_tokens(self):
        v = str(self.V).replace("\\", "/")
        self.expect("Bash", f'sed -i s/a/b/ "{v}/AI 產出/x.md"', "deny", "Z3", "shell_write")  # 事務層：由 pass 改 deny
        self.expect("Bash", f'echo 2026/10/12 > "{v}/AI 產出/x.md"', "deny", "Z3", "shell_write")
        self.expect("Bash", f"sed -i s/a/b/ {self.raw}/客戶案件/a.txt", "deny", "Z9", "readonly_source")

    def test_30_r1_z9_reason(self):
        d = self.decide("Write", self.raw / "客戶案件" / "a.txt")
        self.assertEqual(d.reason, "客戶原檔只讀。要改內容我用檔案工具另存一份")



class Round2(DecideBase):
    """第二輪：驗收 32–35。"""

    def kb(self):
        return f'"{ROOT}/scripts/kb.py" version'

    def test_32_na_escaped_quotes_not_allowed(self):
        evil = 'python ' + self.kb() + f' \\"; rm -rf {self.raw}/客戶案件; echo \\"'
        d = self.decide("Bash", evil)
        self.assertNotEqual(d.decision, "allow", d)
        self.assertEqual(d.decision, "deny", d)

    def test_33_s1_only_bare_interpreter(self):
        d = self.decide("Bash", 'C:/Temp/evil/python.exe ' + self.kb())
        self.assertNotEqual(d.decision, "allow", d)
        self.expect("Bash", "python.exe " + self.kb(), "allow")
        self.expect("Bash", "py " + self.kb(), "allow")

    def test_34_nb_prefixed_verbs_still_write(self):
        r = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"\\rm -rf {r}/客戶案件", "deny", "Z9", "readonly_source")
        self.expect("Bash", f"/bin/rm {r}/客戶案件/a.txt", "deny", "Z9", "readonly_source")
        self.expect("PowerShell", "Microsoft.PowerShell.Management\\Remove-Item " + str(self.raw) + "\\客戶案件\\a.txt", "deny", "Z9", "readonly_source")
        self.expect("Bash", "cat x.md", "pass")
        self.expect("Bash", "ls --rm", "pass")

    def test_35_nc_escaped_segment_still_denied(self):
        r = str(self.raw).replace("\\", "/")
        self.expect("Bash", f"cat {r}/行政/薪\\資/s.xlsx", "deny", "Z10", "excluded")


class Credential(DecideBase):
    """骨架與來源登記（D-079、I-032）：憑證檔名樣式在區之前擋，讀寫都擋、不分大小寫、任一段命中就算。"""

    def credential(self, tool, value, cwd=None):
        d = self.decide(tool, value, cwd)
        with self.subTest(tool=tool, value=str(value)):
            self.assertEqual(d.decision, "deny", d)
            self.assertEqual(d.reason_code, "credential", d)
            self.assertIn("密碼或金鑰", d.reason)
            self.assertEqual(len(d.ledger_rows), 1, d)
            self.assertEqual(d.ledger_rows[0]["reason_code"], "credential")
        return d

    def test_read_env_in_source_denied_with_zone(self):
        d = self.credential("Read", self.raw / "客戶案件" / ".env")
        self.assertEqual(d.zone, "Z9")
        self.assertIn("（.env）", d.reason)

    def test_chinese_password_name_denied(self):
        d = self.credential("Read", self.raw / "客戶案件" / "網站密碼.txt")
        self.assertIn("網站密碼.txt", d.reason)

    def test_case_insensitive(self):
        d = self.credential("Read", self.raw / "客戶案件" / "PASSWORD.TXT")
        self.assertIn("PASSWORD.TXT", d.reason)

    def test_glob_and_write_in_vault_denied(self):
        d = self.credential("Glob", self.V / "AI 產出" / "secret" / "*.md")
        self.assertEqual(d.zone, "Z3")
        d = self.credential("Write", self.V / "AI 產出" / "password.docx")
        self.assertEqual(d.zone, "Z3")
        self.credential("Edit", self.V / "AI 產出" / "password.docx")

    def test_bash_cat_env_denied(self):
        p = str(self.raw).replace("\\", "/")
        d = self.credential("Bash", f'cat "{p}/客戶案件/.env"')
        self.assertEqual(d.ledger_rows[0]["head"], f'cat "{p}/客戶案件/.env"')
        self.credential("PowerShell", f'Get-Content "{p}/客戶案件/id_rsa"')

    def test_accounting_voucher_is_not_credential(self):
        d = self.decide("Read", self.raw / "客戶案件" / "會計憑證.xlsx")
        self.assertEqual((d.decision, d.zone, d.reason_code), ("allow", "Z9", ""))

    def test_kb_call_with_password_folder_still_allowed(self):
        self.expect("Bash", f'python "{ROOT}/scripts/kb.py" folders exclude "{self.raw}/密碼"', "allow")

    def test_outside_vault_still_pass(self):
        self.expect("Read", self.raw / "客戶案件" / ".env", "pass", cwd=self.tmp)


class CredentialRound1(unittest.TestCase):
    """第一輪修正（D-079 補、驗收 34～37）：三道憑證：段（只比來源根／總資料夾以下）、裸 token、資料夾範圍走訪。每個測試自己一份 vault。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        self.client = self.raw / "客戶案件"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def decide(self, tool, value, cwd=None):
        return guard.decide(payload(tool, value, cwd or self.V))

    def grep(self, path, cwd=None):
        return guard.decide({"tool_name": "Grep", "tool_input": {"path": str(path), "pattern": "x"}, "cwd": str(cwd or self.V)})

    def assert_deny(self, d, code, zone=None, contains=None):
        self.assertEqual((d.decision, d.reason_code), ("deny", code), d)
        if zone is not None:
            self.assertEqual(d.zone, zone, d)
        if contains:
            self.assertIn(contains, d.reason)
        self.assertEqual(len(d.ledger_rows), 1, d)
        self.assertEqual(d.ledger_rows[0]["reason_code"], code)

    # 34：Grep 整夾
    def test_34_grep_folder_containing_env_denied_inside(self):
        (self.client / ".env").write_text("K=1", encoding="utf-8")
        (self.client / "子夾").mkdir()
        (self.client / "子夾" / "b.txt").write_text("x", encoding="utf-8")
        d = self.grep(self.client)
        self.assert_deny(d, "credential_inside", "Z9", "這個範圍裡有")
        self.assertIn(".env", d.reason)
        d = self.grep(self.client / "子夾")
        self.assertEqual((d.decision, d.zone), ("allow", "Z9"), d)
        d = guard.decide({"tool_name": "Glob", "tool_input": {"pattern": "*.md", "path": str(self.client)}, "cwd": str(self.V)})
        self.assertEqual((d.decision, d.zone), ("allow", "Z9"), d)  # Glob 只列檔名不走訪
        d = self.grep(self.V)
        self.assertEqual(d.decision, "allow", d)  # 總資料夾裡沒有憑證檔

    def test_34_grep_folder_with_credential_named_subfolder(self):
        (self.client / "密碼").mkdir()
        (self.client / "密碼" / "x.txt").write_text("x", encoding="utf-8")
        d = self.grep(self.client)
        self.assert_deny(d, "credential_inside", "Z9", "密碼")

    def test_grep_z11_folder_is_not_scanned(self):
        other = self.tmp / "別處"
        other.mkdir()
        (other / ".env").write_text("x", encoding="utf-8")
        d = self.grep(other)
        self.assertEqual((d.decision, d.zone), ("pass", "Z11"), d)

    # 35：指令
    def test_35_shell_commands(self):
        (self.client / ".env").write_text("K=1", encoding="utf-8")
        r = str(self.client).replace("\\", "/")
        self.assert_deny(self.decide("Bash", f'grep -r 報價 "{r}"'), "credential_inside", "Z9", ".env")
        self.assert_deny(self.decide("PowerShell", f'Select-String -Path "{r}" -Pattern 報價'), "credential_inside", "Z9")
        self.assert_deny(self.decide("Bash", f'cd "{r}" && cat *'), "credential_inside", "Z9")
        self.assert_deny(self.decide("Bash", f'cd "{r}" && cat id_rsa'), "credential", contains="id_rsa")
        self.assert_deny(self.decide("Bash", "cat id_rsa"), "credential", contains="id_rsa")
        self.assert_deny(self.decide("Bash", "cat id_rsa", cwd=self.V / "AI 產出"), "credential")
        self.assert_deny(self.decide("Bash", "type .env"), "credential", contains=".env")
        self.assert_deny(self.decide("PowerShell", "Get-Content 網站密碼.txt"), "credential", contains="網站密碼.txt")
        self.assert_deny(self.decide("Bash", "cat sub\\dir\\PASSWORD.TXT"), "credential", contains="PASSWORD.TXT")

    def test_45_bare_token_only_when_it_looks_like_a_filename(self):
        """第一輪修正裁定 1 收窄：搜尋詞、變數、旗標、沒副檔名的字不因第二道擋；有 . 的檔名與 id_rsa*、id_ed25519* 照擋。"""
        for cmd in ("grep -i password notes.txt", "echo $PASSWORD", "curl --password=x https://a.b/", "cat passwords",
                    "Get-ChildItem -Filter secret", "echo 密碼", "python -c \"print('secret')\"", "ls -la --secret-flag"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertNotEqual(d.reason_code, "credential", d)
                self.assertNotEqual(d.reason_code, "credential_inside", d)
        for cmd in ("cat passwords.txt", "cat id_rsa", "type .env", "Get-Content 網站密碼.txt",
                    "cat id_ed25519", "cat .env.local", "cat sub/id_rsa.pub", "cat 公司/密碼.txt"):
            with self.subTest(cmd=cmd):
                d = self.decide("PowerShell" if cmd.startswith("Get-") else "Bash", cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential"), d)
        self.assertEqual(guard.token_hit("cat passwords"), None)
        self.assertEqual(guard.token_hit("cat passwords.txt"), "passwords.txt")
        self.assertEqual(guard.token_hit("echo $PASSWORD.txt"), None)
        self.assertEqual(guard.token_hit("x --secret.txt"), None)

    # 第二輪 N1：cwd 本身、裸夾名、不給路徑的範圍搜尋
    def test_46_cwd_itself_bare_folder_and_search_without_path(self):
        (self.V / "收件匣" / "網站密碼.txt").write_text("x", encoding="utf-8")
        for tool, cmd in (("Bash", "grep -r 帳號 ."), ("Bash", "grep -rn 帳號"), ("Bash", "rg -i 帳號"), ("Bash", "cat *"),
                          ("PowerShell", "Get-ChildItem -Recurse | Select-String 帳號"), ("Bash", 'grep -r "" 收件匣'),
                          ("Bash", "find . -name '*.txt'"), ("Bash", "ls -R"), ("PowerShell", "gci -r"), ("Bash", "tree"),
                          ("Bash", "cat ../vault/*"), ("Bash", "cd 收件匣 && cat *"), ("Bash", "cd .. && grep -r x vault")):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
                self.assertIn("網站密碼.txt", d.reason)
        for cmd in ("ls", "git status", "ls -la", "cat 系統/待處理.md", "cat CLAUDE.md", "python --version"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertNotIn(d.reason_code, ("credential", "credential_inside", "credential_scan_limit"), d)
        d = self.decide("Bash", "cat 系統/待處理.md")
        self.assertEqual(d.decision, "pass", d)
        d = self.decide("Bash", "grep -r 帳號 案子")  # 裸夾名、裡面沒憑證
        self.assertEqual(d.decision, "pass", d)

    # 第二輪 N2：含來源根的上層夾也走訪
    def test_47_folder_above_source_root_is_scanned(self):
        r3 = self.tmp / "R3"
        (r3 / "甲").mkdir(parents=True)
        (r3 / "甲" / ".env").write_text("x", encoding="utf-8")
        (r3 / "乙").mkdir()
        (r3 / "乙" / "b.txt").write_text("x", encoding="utf-8")
        import json
        src = self.V / ".system" / "sources.json"
        data = json.loads(src.read_text(encoding="utf-8"))
        data["sources"].append({"id": "src-c3c3", "alias": "甲", "spec": (r3 / "甲").as_posix(), "excludes": []})
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        p = r3.as_posix()
        self.assert_deny(self.grep(r3), "credential_inside", contains=".env")
        self.assert_deny(self.decide("Bash", f'grep -r K "{p}"'), "credential_inside", contains="甲/.env")
        self.assert_deny(self.decide("Bash", f'cd "{p}" && cat 甲/*'), "credential_inside")
        self.assert_deny(self.decide("Bash", f'cd "{p}" && grep -r K 甲'), "credential_inside")
        other = self.tmp / "別處"
        other.mkdir()
        (other / ".env").write_text("x", encoding="utf-8")
        self.assertEqual((self.grep(other).decision, self.grep(other).zone), ("pass", "Z11"))
        self.assertEqual(self.decide("Bash", f'grep -r K "{other.as_posix()}"').decision, "pass")
        self.assertEqual(self.decide("Bash", f'grep -r K "{(r3 / "乙").as_posix()}"').decision, "pass")

    # 第二輪 N3：含點的句子與正規式不是檔名
    def test_48_regex_and_sentences_are_not_filenames(self):
        for tool, cmd in (("Bash", 'grep -rn "password.*=" 收件匣'), ("Bash", 'grep "密碼.*" notes.txt'),
                          ("Bash", 'echo "My password is safe."'), ("Bash", 'grep -E "(secret|token)\\.txt" a.md'),
                          ("PowerShell", 'Select-String -Pattern "密碼[0-9]+\\.log" x.md'), ("Bash", 'echo "密碼.txt 在哪"')):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertNotEqual(d.reason_code, "credential", d)
        for s in ("password.*=", "My password is safe.", "a b.env", "secret[1].txt", "x^密碼.txt", "(密碼).txt", "$x.env"):
            with self.subTest(token=s):
                self.assertIsNone(guard.token_hit("cat " + ('"' + s + '"' if " " in s else s)), s)

    # 第二輪 N4：走訪跳過排除夾，交 Z10b
    def test_49_scan_skips_excluded_folders(self):
        admin = self.raw / "行政"
        (admin / "薪資" / "網站密碼.txt").write_text("x", encoding="utf-8")
        (admin / "舊檔" / ".env").write_text("x", encoding="utf-8")
        for d in (self.grep(admin), self.decide("Bash", f'grep -r x "{admin.as_posix()}"'),
                  self.decide("Bash", f'cd "{admin.as_posix()}" && cat *')):
            with self.subTest(d=d):
                self.assertEqual((d.decision, d.reason_code), ("deny", "excluded_inside"), d)
                self.assertNotIn("網站密碼", d.reason)
                self.assertNotIn(".env", d.reason)
        (admin / "請款" / "id_rsa").write_text("x", encoding="utf-8")
        d = self.grep(admin)
        self.assert_deny(d, "credential_inside", "Z10b", "請款/id_rsa")
        self.assertNotIn("網站密碼", d.reason)

    def test_35_write_command_over_folder_is_not_scanned(self):
        (self.client / ".env").write_text("K=1", encoding="utf-8")
        r = str(self.client).replace("\\", "/")
        d = self.decide("Bash", f'rm -rf "{r}"')
        self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)
        self.assertEqual(self.decide("Bash", f'ls "{r}/子夾不存在"').decision, "pass")

    def test_35_folder_read_without_credentials_still_pass(self):
        r = str(self.client).replace("\\", "/")
        self.assertEqual(self.decide("Bash", f'ls "{r}"').decision, "pass")
        self.assertEqual(self.decide("Bash", f'grep -r x "{r}"').decision, "pass")

    # 36：走訪上限
    def test_36_scan_limit_from_config_fail_closed(self):
        (self.client / "b.txt").write_text("x", encoding="utf-8")
        (self.client / "c.txt").write_text("x", encoding="utf-8")
        cfg = self.V / ".system" / "config.json"
        cfg.write_text('{"credential_scan_max_entries": 1}', encoding="utf-8")
        d = self.grep(self.client)
        self.assert_deny(d, "credential_scan_limit", "Z9", "範圍太大")
        d = self.decide("Bash", f'ls "{self.client}"')
        self.assert_deny(d, "credential_scan_limit", "Z9", "範圍太大")
        cfg.unlink()
        self.assertEqual((self.grep(self.client).decision, self.grep(self.client).zone), ("allow", "Z9"))

    # 37：上層夾名不算
    def test_37_segments_above_source_root_are_ignored(self):
        root = self.tmp / "secretary" / "來源"
        root.mkdir(parents=True)
        (root / "a.txt").write_text("x", encoding="utf-8")
        import json
        src = self.V / ".system" / "sources.json"
        data = json.loads(src.read_text(encoding="utf-8"))
        data["sources"].append({"id": "src-ab12", "alias": "祕書", "spec": str(root).replace("\\", "/"), "excludes": []})
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        d = self.decide("Read", root / "a.txt")
        self.assertEqual((d.decision, d.zone, d.reason_code), ("allow", "Z9", ""), d)
        self.assert_deny(self.decide("Read", root / "password.txt"), "credential", "Z9", "password.txt")
        self.assert_deny(self.decide("Write", root / "password.txt"), "credential", "Z9")
        d = self.decide("Read", self.tmp / "secretary" / "docs" / "a.txt")  # 沒登記：只比最後一段
        self.assertEqual((d.decision, d.zone), ("pass", "Z11"), d)
        self.assert_deny(self.decide("Read", "~/.ssh/id_rsa"), "credential", "Z11", "id_rsa")
        self.assert_deny(self.decide("Read", self.tmp / "secretary" / "docs" / ".env"), "credential", "Z11")
        self.assertEqual(self.decide("Bash", f'ls "{root.as_posix()}"').decision, "pass")  # 反斜線在 bash 是跳脫，照其他測試用正斜線

    def test_37_vault_segments_only_below_vault(self):
        hidden = self.tmp / "secrets"
        hidden.mkdir()
        shutil.copytree(self.V, hidden / "vault")
        v2 = hidden / "vault"
        d = guard.decide(payload("Read", v2 / "案子" / "信義王" / "信義王.md", v2))
        self.assertEqual((d.decision, d.zone), ("allow", "Z1"), d)
        d = guard.decide(payload("Write", v2 / "AI 產出" / "password.docx", v2))
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential"), d)


if __name__ == "__main__":
    unittest.main()
