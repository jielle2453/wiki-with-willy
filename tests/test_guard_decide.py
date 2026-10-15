"""kb.guard.decide：決策表每一格至少一個案例，直接呼叫，不經 stdin。"""
import os
import re
import shutil
import unittest

from tests._guard_fixture import ROOT, make_vault, payload
from kb import guard  # noqa: E402
from kb._lib.paths import normalize as paths_normalize  # noqa: E402


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

    def test_28_vault_interior_is_exempt(self):
        """D-081 內部豁免：總資料夾內部（Z1～Z8）不比憑證樣式，照區判。"""
        for tool, p, zone in (("Glob", self.V / "AI 產出" / "secret" / "*.md", "Z3"), ("Write", self.V / "AI 產出" / "password.docx", "Z3"),
                              ("Edit", self.V / "AI 產出" / "password.docx", "Z3"), ("Read", self.V / "收件匣" / ".env", "Z4"),
                              ("Read", self.V / "案子" / "大樓" / "門禁密碼.md", "Z2"), ("Read", self.V / "案子" / "密碼" / "密碼.md", "Z1")):
            with self.subTest(tool=tool, p=str(p)):
                d = self.decide(tool, p)
                self.assertEqual((d.decision, d.zone, d.reason_code), ("allow", zone, ""), d)
        d = self.decide("Write", self.V / "收件匣" / "password.txt")
        self.assertEqual((d.decision, d.reason_code), ("deny", "inbox"), d)

    def test_60_reason_mentions_customer_not_unswitchable(self):
        d = self.credential("Read", self.raw / "客戶案件" / ".env")
        self.assertIn("客戶沒說", d.reason)
        self.assertNotIn("關不掉", d.reason)

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

    def in_client(self, cmd, tool="Bash"):
        """效用 cwd 在 raw/客戶案件：對話開在總資料夾，指令先 cd 進來源。"""
        p = self.client.as_posix()
        return f'Set-Location "{p}"; {cmd}' if tool == "PowerShell" else f'cd "{p}" && {cmd}'

    def analyze(self, cmd, cwd=None, tool="Bash"):
        return guard._analyze(cmd, str(cwd or self.client), paths_normalize(str(self.V), None), tool)

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
        self.assert_deny(self.decide("Bash", self.in_client("cat id_rsa")), "credential", contains="id_rsa")
        self.assert_deny(self.decide("Bash", f'cd "{(self.raw / "行政").as_posix()}" && type .env'), "credential", contains=".env")
        self.assert_deny(self.decide("PowerShell", self.in_client("Get-Content 網站密碼.txt", "PowerShell")), "credential", contains="網站密碼.txt")
        self.assert_deny(self.decide("Bash", self.in_client("cat sub\\dir\\PASSWORD.TXT")), "credential", contains="PASSWORD.TXT")
        for cmd in ("cat id_rsa", "type .env", "cat passwords.txt"):  # D-081：效用 cwd 在總資料夾裡，第二道不做
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd, cwd=self.V)
                self.assertNotEqual(d.reason_code, "credential", d)
                d = self.decide("Bash", cmd, cwd=self.V / "AI 產出")
                self.assertNotEqual(d.reason_code, "credential", d)
        d = self.decide("Bash", f'cd "{self.client.as_posix()}" && cd "{self.V.as_posix()}" && cat id_rsa')  # cd 回總資料夾後也豁免
        self.assertNotEqual(d.reason_code, "credential", d)

    def test_45_bare_token_only_when_it_looks_like_a_filename(self):
        """第一輪修正裁定 1 收窄：搜尋詞、變數、旗標、沒副檔名的字不因第二道擋；有 . 的檔名與 id_rsa*、id_ed25519* 照擋。"""
        # 效用 cwd 在 raw 裡：對話開在總資料夾、指令先 cd 進來源（payload cwd 在知識庫外時守門本來就不表態）
        for cmd in ("grep -i password notes.txt", "echo $PASSWORD", "curl --password=x https://a.b/", "cat passwords",
                    "Get-ChildItem -Filter secret", "echo 密碼", "python -c \"print('secret')\"", "ls -la --secret-flag"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", self.in_client(cmd))
                self.assertNotEqual(d.reason_code, "credential", d)
                self.assertNotEqual(d.reason_code, "credential_inside", d)
        for cmd in ("cat passwords.txt", "cat id_rsa", "type .env", "Get-Content 網站密碼.txt",
                    "cat id_ed25519", "cat .env.local", "cat sub/id_rsa.pub", "cat 公司/密碼.txt"):
            tool = "PowerShell" if cmd.startswith("Get-") else "Bash"
            with self.subTest(cmd=cmd):
                d = self.decide(tool, self.in_client(cmd, tool))
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential"), d)
                d = self.decide(tool, cmd, cwd=self.V)  # 總資料夾裡豁免
                self.assertNotEqual(d.reason_code, "credential", d)
        self.assertEqual(guard.token_hit("cat passwords"), None)
        self.assertEqual(guard.token_hit("cat passwords.txt"), "passwords.txt")
        self.assertEqual(guard.token_hit("echo $PASSWORD.txt"), None)
        self.assertEqual(guard.token_hit("x --secret.txt"), None)

    # 第二輪 N1、第四輪 D-081：cwd 在總資料夾裡永不走訪 cwd；cwd 在 raw 裡時動詞任一處、只認存在的路徑
    def test_46_cwd_scan_only_outside_vault(self):
        (self.V / "收件匣" / "網站密碼.txt").write_text("x", encoding="utf-8")
        for cmd in ("grep -r 帳號 .", "grep -rn 帳號", "rg -i 帳號", "cat *", 'grep -r "" 收件匣', "find . -name '*.txt'", "ls -R",
                    "tree", "cd 收件匣 && cat *", "ls", "git status", "cat 系統/待處理.md", "cat CLAUDE.md", "grep -r 帳號 案子"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertNotIn(d.reason_code, ("credential", "credential_inside", "credential_scan_limit"), d)
        self.assertEqual(self.decide("Bash", "cat 系統/待處理.md").decision, "pass")
        (self.client / ".env").write_text("x", encoding="utf-8")
        (self.client / "子夾").mkdir()
        (self.client / "子夾" / "b.txt").write_text("x", encoding="utf-8")
        (self.client / "a.txt").write_text("x", encoding="utf-8")
        client_n = paths_normalize(str(self.client), None)
        # 效用 cwd 在 raw 裡：「走訪 cwd」的規則用 _analyze 直接驗（payload cwd 在知識庫外時守門不表態；cd 進來源時 cd 目標本身也會走訪）
        hits = ("grep -r 帳號 .", "grep -rn 帳號", "rg -i 帳號", "cat *", "time rg 帳號", "LC_ALL=C grep -r 帳號", "sudo grep -r 帳號",
                'rg "2026/10"', "grep -rn TODO/FIXME", "dir -r | sls 帳號", "ls -R | sls 帳號", "Get-ChildItem -Recurse | Select-String 帳號",
                "$f = Get-ChildItem -Recurse -File; $f | Select-String 帳號", 'grep -r "" 子夾', "rg 帳號 | grep 案子",
                "findstr /s 帳號", "tree", "find . -name '*.md'")
        for cmd in hits:
            with self.subTest(cmd=cmd):
                dirs = self.analyze(cmd)["read_dirs"]
                self.assertTrue(any(d in (client_n, client_n + "/子夾") for d in dirs), (cmd, dirs))
                tool = "PowerShell" if cmd.startswith(("dir", "ls -R", "Get-", "$f")) else "Bash"
                d = self.decide(tool, self.in_client(cmd, tool))
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
                self.assertIn(".env", d.reason)
        for cmd in ("ls", "git status", 'echo "what?"', 'git commit -m "fix?"', "git log | grep fix", "cat a.txt | grep 帳號",
                    "cat a.txt", "grep 帳號 a.txt", "grep -r 帳號 a.txt", "python --version", "grep -rn 帳號 a.txt"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.analyze(cmd)["read_dirs"], [], cmd)
        self.assertEqual(self.analyze("grep -r 帳號 子夾")["read_dirs"], [client_n + "/子夾"])
        d = self.decide("Bash", self.in_client("grep -r 帳號 子夾"))  # 走訪 子夾（沒憑證）與 cd 目標 客戶案件（有 .env）
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        d = self.decide("Bash", f'cd "{(self.client / "子夾").as_posix()}" && grep -r 帳號')
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
        for d in (self.grep(admin), self.decide("Bash", f'grep -r x "{admin.as_posix()}"')):
            with self.subTest(d=d):
                self.assertEqual((d.decision, d.reason_code), ("deny", "excluded_inside"), d)
                self.assertNotIn("網站密碼", d.reason)
                self.assertNotIn(".env", d.reason)
        d = self.decide("Bash", f'cd "{admin.as_posix()}" && cat *')  # * 展開命中排除夾本身：Z10 自己那句（Q5）
        self.assertEqual(d.decision, "deny", d)
        self.assertIn(d.reason_code, ("excluded", "excluded_inside"), d)
        self.assertNotIn("網站密碼", d.reason)
        (admin / "請款" / "id_rsa").write_text("x", encoding="utf-8")
        d = self.grep(admin)
        self.assert_deny(d, "credential_inside", "Z10b", "請款/id_rsa")
        self.assertNotIn("網站密碼", d.reason)

    # 第三輪 S1：不帶斜線的 .. 也經區判斷
    def test_51_dotdot_goes_through_zones(self):
        for cmd in ("grep -r SALARY ..", "rg SALARY .."):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "excluded_inside"), d)
        self.assertEqual(self.decide("Bash", "cat ../raw/客戶案件/a.txt").decision, "pass")
        d = self.decide("Bash", "rm -rf ..")
        self.assertEqual(d.decision, "deny", d)

    # 第三輪 S2、第四輪 Q8：有寫入仍走訪讀取範圍，只有寫入目標不走訪；. 只在導向或寫入動詞那段才算寫入目標
    def test_52_read_range_scanned_even_when_command_writes(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        client_n = paths_normalize(str(self.client), None)
        for cmd in ("grep -r TOKEN . | tee /tmp/hits.txt", "grep -r TOKEN > /tmp/hits.txt", "cat * > /tmp/all.txt"):
            with self.subTest(cmd=cmd):
                info = self.analyze(cmd)
                self.assertIn(client_n, info["read_dirs"], (cmd, info))
                d = self.decide("Bash", self.in_client(cmd))
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        info = self.analyze("ls . | tee /tmp/a")
        self.assertNotIn(client_n, info["write_targets"], info)
        d = self.decide("Bash", self.in_client("ls . | tee /tmp/a"))
        self.assertNotEqual(d.reason_code, "protected", d)
        # 第七輪 Q8 重定：只保「. 在讀取段不是 protected」；一段有導向或寫入動詞就整段寫入，打包類在總資料夾裡擋就擋
        for cmd in ("ls . | tee /tmp/a", "grep -r X . | tee /tmp/h"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertNotEqual(d.reason_code, "protected", d)
                self.assertEqual(d.decision, "pass", d)
        for cmd in ("tar czf ../bak.tgz .", "echo x > CLAUDE.md", "rm -rf .", "cat CLAUDE.md > /tmp/c.md"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "protected"), d)

    # 第五輪 V1：PowerShell 的 cmdlet 會展開引號裡的 *；Bash 不會
    def test_64_powershell_quoted_wildcards_expand(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        (self.raw / "行政" / "薪資" / "s2.txt").write_text("x", encoding="utf-8")
        r = self.client.as_posix()
        d = self.decide("PowerShell", f'Get-Content "{r}/*"')
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential"), d)
        self.assertIn(".env", d.reason)
        admin = (self.raw / "行政").as_posix()
        for cmd in (f'Get-Content "{admin}/*/s2.txt"', f'Get-ChildItem "{admin}/*" -Recurse | Get-Content'):
            with self.subTest(cmd=cmd):
                d = self.decide("PowerShell", cmd)
                self.assertEqual(d.decision, "deny", d)
                self.assertIn(d.reason_code, ("excluded", "excluded_inside"), d)
        d = self.decide("Bash", 'echo "*.md"')
        self.assertEqual(d.decision, "pass", d)
        self.assertEqual(self.analyze('echo "*.md"', cwd=self.V)["globs"], [])
        self.assertEqual(len(guard._analyze('echo "*.md"', str(self.V), paths_normalize(str(self.V), None), "PowerShell")["globs"]), 1)

    # 第五輪 V2：cd 到萬用字元路徑時走訪前綴夾
    def test_65_cd_to_wildcard_scans_prefix_folder(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        d = self.decide("Bash", f"cd {self.raw.as_posix()}/客戶* && grep -r sk")
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        self.assertIn(".env", d.reason)
        d = self.decide("PowerShell", f'Set-Location "{self.raw.as_posix()}/客戶*"; Get-ChildItem -Recurse')
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)

    # 第五輪 V6：推不出效用 cwd 時第二道照跑、不豁免；Push-Location 也是 cd
    def test_66_unresolvable_cwd_keeps_token_gate(self):
        for tool, cmd in (("PowerShell", "Push-Location ~\\.ssh; Get-Content id_rsa"), ("Bash", 'cd "$HOME/.ssh" && cat id_rsa'),
                          ("Bash", "type %USERPROFILE%\\.ssh\\id_rsa"), ("Bash", 'cd "$HOME/.ssh" && cat $HOME/.ssh/id_rsa'),
                          ("PowerShell", 'Push-Location "$env:USERPROFILE\\.ssh"; Get-Content id_rsa')):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential"), d)
                self.assertIn("id_rsa", d.reason)
        d = self.decide("Bash", 'cd "$HOME/x" && echo $PASSWORD')
        self.assertNotEqual(d.reason_code, "credential", d)

    # 第六輪 W1：寫入欄以路徑為單位，同一路徑一段讀一段寫也套寫入欄
    def test_69_path_read_then_written_gets_write_column(self):
        a = (self.client / "a.txt").as_posix()
        d = self.decide("Bash", f'cat "{a}"; echo x > "{a}"')
        self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)
        d = self.decide("PowerShell", f'(Get-Content "{a}") -replace "a","b" | Set-Content "{a}"')
        self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)
        d = self.decide("Bash", "cat .system/sources.json && rm .system/sources.json")
        self.assertEqual((d.decision, d.reason_code), ("deny", "protected"), d)
        d = self.decide("PowerShell", '(Get-Content .system/sources.json) -replace "hidden","x" | Set-Content .system/sources.json')
        self.assertEqual((d.decision, d.reason_code), ("deny", "protected"), d)
        d = self.decide("Bash", "cat .system/sources.json")  # 只讀照舊
        self.assertEqual(d.decision, "pass", d)

    # 第六輪 W2：真正的磁碟根照走訪（上限住 config）
    def test_70_real_drive_root_is_scanned(self):
        drive = self.V.drive.upper() + "/" if self.V.drive else "/"
        for cmd in (f"grep -r X {drive}", f"rg X {drive}"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertEqual(d.decision, "deny", d)
                self.assertIn(d.reason_code, ("credential_inside", "credential_scan_limit"), d)
        d = self.decide("Bash", "grep -r X ..")  # .. 是 tmp，含 V 與 raw：走訪，fixture 乾淨 → Z10b
        self.assertEqual(d.decision, "deny", d)
        (self.client / ".env").write_text("x", encoding="utf-8")
        d = self.decide("Bash", "grep -r X ..")
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)

    # 第六輪 V5 另一半：cmd /c 的 /c 不是路徑，區判斷也不是 Z10b
    def test_71_cmd_slash_c_is_pass(self):
        for cmd in ("cmd /c ver", "cmd /c dir"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertEqual((d.decision, d.reason_code), ("pass", ""), d)

    # 第六輪 W4：cd 經萬用字元進乾淨夾，用展開後的夾走訪
    def test_72_cd_wildcard_uses_expanded_folder(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        d = self.decide("Bash", f'cd "{self.raw.as_posix()}"/行*/請款 && ls')
        self.assertNotIn(d.reason_code, ("credential_inside", "credential_scan_limit"), d)
        d = self.decide("Bash", f'cd "{self.raw.as_posix()}"/客戶* && ls')
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        d = self.decide("Bash", f'cd "{self.raw.as_posix()}"/行*/薪資 && ls')  # 展開後是排除夾：交 Z10
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"), d)

    # 第七輪：寫入判斷回到保守預設——一段有導向或寫入動詞，整段路徑都是寫入目標；打包類在總資料夾裡擋就擋
    def test_73_write_stage_makes_every_path_a_write_target(self):
        for tool, cmd in (("Bash", "tar xzf /tmp/a.tgz -C ."), ("Bash", "cp C:/tmp/zip/a.txt ."), ("PowerShell", "Copy-Item C:/tmp/a.txt ."),
                          ("Bash", "mv /tmp/x.md ."), ("Bash", "tar -xf /tmp/a.tar -C ."), ("Bash", "unzip /tmp/a.zip -d ."),
                          ("Bash", "tar -C . -xzf /tmp/a.tgz"), ("Bash", "tar czf ../bak.tgz ."), ("Bash", "tar -czf /tmp/b.tgz .")):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "protected"), d)
        for tool, cmd in (("Bash", "tar czf /tmp/b.tgz 收件匣"), ("Bash", "zip -r /tmp/b.zip 收件匣"), ("Bash", "7z a /tmp/b.7z 收件匣"),
                          ("PowerShell", "Compress-Archive -Path 收件匣 -DestinationPath /tmp/b.zip")):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z4", "inbox"), d)  # 收件匣在寫入段：Z4 寫入欄，接受誤擋
        info = self.analyze("cp C:/tmp/zip/a.txt .", cwd=self.V)
        self.assertIn(paths_normalize(str(self.V), None), info["write_targets"], info)
        self.assertFalse(hasattr(guard, "_special_roles"))

    # 第七輪 X1：mv 的來源也是寫入目標
    def test_75_move_source_is_a_write_target(self):
        a = (self.client / "a.txt").as_posix()
        for tool, cmd in (("Bash", f'mv "{a}" /tmp/a.txt'), ("PowerShell", f'Move-Item -Path "{a}" -Destination /tmp/'),
                          ("Bash", f'mv "{self.client.as_posix()}" /tmp/x')):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)
        d = self.decide("Bash", "mv .system/sources.json /tmp/s.json")
        self.assertEqual((d.decision, d.reason_code), ("deny", "protected"), d)

    # 第七輪 X2：-C／-t／-Destination 這些目標不再被當讀取
    def test_76_targets_of_extract_copy_sync_are_write_targets(self):
        r = self.client.as_posix()
        for tool, cmd in (("Bash", f'tar -C "{r}" -xzf /tmp/x.tgz'), ("Bash", f'tar xf /tmp/x.tar --directory "{r}"'),
                          ("Bash", f'cp -t "{r}" /tmp/t.txt'), ("PowerShell", f'Copy-Item -Destination "{r}" -Path /tmp/t.txt'),
                          ("Bash", f'rsync -a --remove-source-files "{r}/" /tmp/t/'), ("Bash", f'robocopy "{r}" /tmp/t /MIR'),
                          ("Bash", f'xcopy "{r}" /tmp/t /s'), ("PowerShell", f'Expand-Archive /tmp/x.zip -DestinationPath "{r}"'),
                          ("PowerShell", f'Set-Content "{r}/n.txt" x'), ("PowerShell", f'Out-File -FilePath "{r}/n.txt"'),
                          ("Bash", f'zip -r /tmp/b.zip "{r}"')):  # 旗標黏值（7z -o"…"）cmdparse 抽不到，B-026
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)

    # 第七輪 X3：單字母 /x 只在開關式指令或 PowerShell 當旗標；Bash 的 grep -r X /c 裡 /c 是 C 槽根
    def test_77_slash_letter_is_a_switch_only_for_switch_commands(self):
        for cmd in ("grep -r TODO /c", 'find /c -name "*.env"'):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertEqual(d.decision, "deny", d)
                self.assertIn(d.reason_code, ("excluded_inside", "credential_inside", "credential_scan_limit"), d)
        d = self.decide("Bash", "cmd /c ver")
        self.assertEqual(d.decision, "pass", d)
        d = self.decide("PowerShell", "robocopy a b /e")
        self.assertNotEqual(d.reason_code, "excluded_inside", d)
        d = self.decide("Bash", "findstr /s TODO *.txt")
        self.assertNotIn(d.reason_code, ("excluded_inside", "credential_scan_limit"), d)

    # 第五輪 V5：單字母 /x token 不當路徑、不走訪整顆磁碟
    def test_67_single_letter_slash_token_is_not_a_path(self):
        for cmd in ("cmd /c ver", "findstr /s x *.txt", "cmd /c dir", "robocopy /e a b"):
            with self.subTest(cmd=cmd):
                d = self.decide("Bash", cmd)
                self.assertNotIn(d.reason_code, ("credential_inside", "credential_scan_limit"), d)
                info = self.analyze(cmd, cwd=self.V)
                self.assertFalse(any(len(p) <= 3 for p in info["read_dirs"]), info)
        r = str(self.client).replace("\\", "/")
        d = self.decide("Bash", f'rm -rf "{r}"')  # 寫入目標本身不走訪（35 的案例照舊）
        self.assertEqual((d.decision, d.reason_code), ("deny", "readonly_source"), d)

    # 第三輪 P1、第四輪 D-081：總資料夾裡一張叫「門禁密碼」的卡不再擋整庫；含 V 的上層夾走訪時跳過 V
    def test_53_vault_card_named_password_does_not_block(self):
        (self.V / "案子" / "大樓").mkdir()
        (self.V / "案子" / "大樓" / "門禁密碼.md").write_text("x", encoding="utf-8")
        d = self.decide("Read", self.V / "案子" / "大樓" / "門禁密碼.md")
        self.assertEqual((d.decision, d.zone), ("allow", "Z2"), d)  # 跟夾不同名是實體夾原檔（Z2）；同名綜合卡是 Z1，都 allow
        for tool, cmd in (("Bash", "grep -r 帳號"), ("Bash", "rg 帳號"), ("Bash", 'echo "what?"'), ("Bash", 'git commit -m "fix?"'),
                          ("Bash", 'python -c "x=[0]"'), ("Bash", 'find 案子 -name "*.md"'), ("Bash", "git log | grep fix"),
                          ("PowerShell", "Get-ChildItem -Recurse | Select-String 帳號"), ("Bash", "grep -r 帳號 案子")):
            with self.subTest(cmd=cmd):
                d = self.decide(tool, cmd)
                self.assertNotIn(d.reason_code, ("credential", "credential_inside", "credential_scan_limit"), d)
        d = self.grep(self.V / "案子")  # 型別夾根是 Z8，內部不走訪
        self.assertEqual(d.decision, "allow", d)
        d = self.grep(self.V / "案子" / "大樓")
        self.assertEqual((d.decision, d.zone), ("allow", "Z2"), d)
        (self.client / ".env").write_text("x", encoding="utf-8")
        d = self.grep(self.tmp)  # P 底下有 V 與含 .env 的 raw
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        self.assertIn(".env", d.reason)
        self.assertNotIn("門禁密碼", d.reason)

    # 第三輪 P2：萬用字元展開命中照第一道比段
    def test_54_glob_expansion_hits_go_through_segment_gate(self):
        other = self.tmp / "other"
        (other / ".ssh").mkdir(parents=True)
        (other / ".ssh" / "id_rsa").write_text("x", encoding="utf-8")
        (other / "proj").mkdir()
        (other / "proj" / ".env").write_text("x", encoding="utf-8")
        s = other.as_posix()
        self.assert_deny(self.decide("Bash", f"cat {s}/.ssh/id_*"), "credential", contains="id_rsa")
        self.assert_deny(self.decide("Bash", f"cat {s}/proj/.env*"), "credential", contains=".env")
        d = self.decide("Bash", 'grep -rn "密碼[0-9]+\\.log" 收件匣')
        self.assertNotEqual(d.reason_code, "credential", d)

    # 第三輪 P3：走訪起點是排除夾或排除單檔就不走訪，交 Z10；排除單檔名不進理由句
    def test_55_excluded_start_point_is_not_scanned(self):
        salary = self.raw / "行政" / "薪資"
        (salary / "網站密碼.txt").write_text("x", encoding="utf-8")
        for d in (self.grep(salary), self.decide("Bash", f'grep -r x "{salary.as_posix()}"'),
                  self.decide("Bash", f'cd "{salary.as_posix()}" && cat *')):
            with self.subTest(d=d):
                self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z10", "excluded"), d)
                self.assertNotIn("網站密碼", d.reason)
        import json
        billing = self.raw / "行政" / "請款"
        (billing / "密碼單.txt").write_text("x", encoding="utf-8")
        src = self.V / ".system" / "sources.json"
        data = json.loads(src.read_text(encoding="utf-8"))
        data["sources"][1]["excludes"].append({"spec": "請款/密碼單.txt", "kind": "hidden", "reason": "", "added": "2026-10-15"})
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        d = self.grep(billing)  # 夾裡有排除單檔：範圍含排除（Z10b）用自己的理由句，排除單檔名不進理由句
        self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z10b", "excluded_inside"), d)
        self.assertNotIn("密碼單", d.reason)
        (billing / "x.txt").write_text("x", encoding="utf-8")
        d = self.decide("Bash", f'cat "{(billing / "x.txt").as_posix()}"')
        self.assertEqual(d.decision, "pass", d)
        d = self.decide("Read", billing / "密碼單.txt")
        self.assertEqual((d.decision, d.zone), ("deny", "Z10"), d)

    # 第三輪 P4：子指令各自判「沒給路徑」（cwd 在 raw 裡）
    def test_56_each_subcommand_judged_separately(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        (self.client / "a.txt").write_text("x", encoding="utf-8")
        client_n = paths_normalize(str(self.client), None)
        for cmd in ("cat a.txt && grep -r 帳號", "cat a.txt; rg 帳號", "cat a.txt || grep -r 帳號"):
            with self.subTest(cmd=cmd):
                self.assertIn(client_n, self.analyze(cmd)["read_dirs"], cmd)
                d = self.decide("Bash", self.in_client(cmd))
                self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        self.assertEqual(self.analyze("cat a.txt && grep 帳號")["read_dirs"], [])  # 第二個子指令沒遞迴，不走訪

    # 第三輪 O2、第四輪 Q4：走訪含總資料夾的上層夾時跳過總資料夾整棵；raw 裡的 .claude 照比
    def test_57_scan_skips_whole_vault_but_not_dot_claude_in_raw(self):
        big = self.V / ".system" / "versions" / "20261015-000000-0000" / "files"
        big.mkdir(parents=True)
        for i in range(6000):
            (big / f"f{i}.txt").write_bytes(b"")
        (self.V / ".claude" / "password.json").write_text("{}", encoding="utf-8")
        d = self.grep(self.tmp)  # P 底下只有 V 與乾淨的 raw（raw 的排除夾跳過）
        self.assertNotEqual(d.reason_code, "credential_scan_limit", d)
        self.assertNotIn(d.reason_code, ("credential", "credential_inside"), d)
        (self.raw / "proj" / ".claude").mkdir(parents=True)
        (self.raw / "proj" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        d = self.grep(self.tmp)
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)
        self.assertIn(".credentials.json", d.reason)

    # 第四輪 Q5：萬用字元展開命中排除檔交 Z10，不印檔名
    def test_58_glob_hit_on_excluded_file_goes_to_z10(self):
        import json
        billing = self.raw / "行政" / "請款"
        (billing / "密碼單.txt").write_text("x", encoding="utf-8")
        src = self.V / ".system" / "sources.json"
        data = json.loads(src.read_text(encoding="utf-8"))
        data["sources"][1]["excludes"].append({"spec": "請款/密碼單.txt", "kind": "hidden", "reason": "", "added": "2026-10-15"})
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        d = self.decide("Bash", f'cat "{billing.as_posix()}/"*.txt')
        self.assertEqual((d.decision, d.zone, d.reason_code), ("deny", "Z10", "excluded"), d)
        self.assertNotIn("密碼單", d.reason)

    # 第四輪 Q7：引號裡的字不當萬用字元
    def test_59_quoted_wildcards_are_not_globs(self):
        (self.client / ".env").write_text("x", encoding="utf-8")
        (self.client / "子夾").mkdir()
        (self.client / "子夾" / "b.md").write_text("x", encoding="utf-8")
        client_n = paths_normalize(str(self.client), None)
        for cmd in ('find 子夾 -name "*.md"', 'git commit -m "update *.md"', "echo '*'"):
            with self.subTest(cmd=cmd):
                info = self.analyze(cmd)
                self.assertEqual(info["globs"], [], (cmd, info))
                self.assertNotIn(client_n, info["read_dirs"], (cmd, info))
        self.assertEqual(self.analyze("cat 子夾/*.md")["read_dirs"], [])
        self.assertEqual(self.analyze("cat 子夾/*.md")["globs"], [client_n + "/子夾/*.md"])
        d = self.decide("Bash", f'cd "{(self.client / "子夾").as_posix()}" && cat *.md')
        self.assertEqual(d.decision, "pass", d)
        d = self.decide("Bash", self.in_client("cat *.md"))
        self.assertEqual((d.decision, d.reason_code), ("deny", "credential_inside"), d)

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
        self.assertEqual((d.decision, d.zone, d.reason_code), ("allow", "Z3", ""), d)  # D-081 內部豁免


if __name__ == "__main__":
    unittest.main()
