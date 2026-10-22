"""守門（密碼與守門第 1、4 點；驗收 10、11、12、12b）：只列檔名（D-185）、照 shell 語法拆（D-186）、
B-022（不帶磁碟代號的路徑）、B-088（工具夾與 plugin 夾不算密碼檔）、AI 產出那條只擋寫（D-186 補）。
拆開之後以前的繞過要照擋：管線後面才寫（| xargs rm、| Remove-Item）、包裝指令、變數。"""
import os
import shutil
import unittest

from tests._guard_fixture import KB, ROOT, make_vault, payload
from kb import guard  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.V, cls.raw = make_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        cls.out = cls.tmp / "外面"
        cls.out.mkdir()
        (cls.raw / "客戶案件" / "網站密碼.xlsx").write_bytes(b"x")
        (cls.raw / "客戶案件" / "報價.pdf").write_bytes(b"x")
        (cls.V / "AI 產出" / "a.md").write_text("x", encoding="utf-8")
        (cls.V / "AI 產出" / "a.docx").write_bytes(b"x")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decide(self, tool, cmd, cwd=None):
        return guard.decide(payload(tool, cmd, cwd or self.V))

    def assertDecision(self, tool, cmd, want, cwd=None, zone=None):
        d = self.decide(tool, cmd, cwd)
        with self.subTest(tool=tool, cmd=cmd):
            self.assertEqual(d.decision, want, d)
            if zone is not None:
                self.assertEqual(d.zone, zone, d)
        return d


class ListOnly(Base):
    """驗收 10（D-185）：只列檔名放行；後面接讀內容的照擋；排除的夾連列都擋。"""

    def test_listing_folder_with_credential_file_passes(self):
        src = (self.raw / "客戶案件").as_posix()
        for tool, cmd in (("Bash", f'ls "{src}"'), ("Bash", f'ls -la "{src}"'), ("Bash", f'dir "{src}"'),
                          ("Bash", f'find "{src}" -name "*.pdf"'), ("Bash", f'find "{src}" -maxdepth 1 -type f'),
                          ("PowerShell", f'Get-ChildItem "{src}"'), ("PowerShell", f'gci "{src}" -Recurse'),
                          ("Bash", f'tree "{src}"'), ("Bash", f'cd "{src}"'), ("Bash", f'cd "{src}" && ls'),
                          ("Bash", f'cd "{src}" && pwd')):
            self.assertDecision(tool, cmd, "pass")

    def test_reading_after_listing_still_denied(self):
        src = (self.raw / "客戶案件").as_posix()
        for tool, cmd in (("Bash", f'ls "{src}" | xargs cat'), ("Bash", f'find "{src}" -exec cat {{}} \\;'),
                          ("Bash", f'ls "{src}"; cat "{src}/網站密碼.xlsx"'), ("PowerShell", f'Get-ChildItem "{src}" | Get-Content'),
                          ("Bash", f'cd "{src}" && cat *'), ("Bash", f'cd "{src}" && grep -r x .'),
                          ("Bash", f'grep -r x "{src}"'), ("Bash", f'ls "{src}" > "{self.out.as_posix()}/l.txt"'),
                          ("Bash", f'python -I -c "import os,sys;print(len(os.listdir(sys.argv[1])))" "{src}"')):
            self.assertDecision(tool, cmd, "deny")

    def test_excluded_folder_listing_denied(self):
        for tool, cmd in (("Bash", f'ls "{(self.raw / "行政" / "薪資").as_posix()}"'),
                          ("PowerShell", f'Get-ChildItem "{(self.raw / "行政" / "薪資").as_posix()}"'),
                          ("Bash", f'cd "{(self.raw / "行政" / "薪資").as_posix()}"')):
            self.assertDecision(tool, cmd, "deny", zone="Z10")

    def test_cd_into_parent_of_excluded_then_reading_it_denied(self):
        adm = (self.raw / "行政").as_posix()
        self.assertDecision("Bash", f'cd "{adm}"', "pass")
        for cmd in (f'cd "{adm}" && grep -r x', f'cd "{adm}" && cat 薪資/s.xlsx', f'cd "{adm}" && cat */s.xlsx',
                    f'cd "{adm}" && tar czf "{self.out.as_posix()}/x.tgz" .'):
            self.assertDecision("Bash", cmd, "deny")


class ShellSyntax(Base):
    """驗收 11（D-186 第 1 點）。"""

    def test_heredoc_and_quotes_are_data(self):
        o = self.out.as_posix()
        for tool, cmd in (("Bash", "python - <<'EOF'\nprint(1 > 0)\nEOF"),
                          ("Bash", f"cat > \"{o}/r.md\" <<'EOF'\n# 報告\n案子\\信義王 > 系統\\x 都看過\nEOF"),
                          ("Bash", f'python "{KB}" agenda list | awk \'$0 >= "2026"\''),
                          ("PowerShell", f"@'\n案子\\信義王 > 系統\n'@ | Set-Content \"{o}/r.md\""),
                          ("Bash", f"cat >> \"{o}/r.md\" <<EOF\n登記 {self.raw.as_posix()}/客戶案件\nEOF"),
                          ("Bash", 'ls | grep md'), ("Bash", "grep -n install README.md"),
                          ("Bash", f'python -c "import json;d=json.load(open(\'.system/sources.json\'));print(len(d)>1)"'),
                          ("Bash", f'python -I -c "import os,sys;print(len(os.listdir(sys.argv[1])))" "{self.raw.as_posix()}/行政/請款"'),
                          ("Bash", f'python "{KB}" cards todo --json > "{o}/todo.json"'),
                          ("Bash", f'python -I -X utf8 -c "import json,sys;o=open(sys.argv[2],\'w\',encoding=\'utf-8\');o.write(\'x\')" "{o}/in.json" "{o}/out.txt"')):
            self.assertDecision(tool, cmd, "pass")

    def test_real_writes_into_vault_denied(self):
        v = self.V.as_posix()
        for tool, cmd in (("Bash", f'python -c "open(\'{v}/系統/x\',\'w\')"'),
                          ("Bash", f'echo x > "{v}/系統/x"'),
                          ("Bash", f'python "{KB}" cards todo --json > "{v}/系統/x.txt"'),
                          ("Bash", f'python "{KB}" cards todo --json > "{v}/todo.json"'),
                          ("Bash", f"python - <<'EOF'\nopen(r'{v}/系統/x','w').write('1')\nEOF"),
                          ("Bash", f"cat <<'EOF' | python -\nopen(r'{v}/系統/x','w').write('1')\nEOF"),
                          ("Bash", f'echo "rm -rf {v}/系統" | bash'),
                          ("Bash", f'bash -c "echo x > {v}/系統/x"'),
                          ("Bash", f'ls "{self.raw.as_posix()}/客戶案件" | xargs rm'),
                          ("PowerShell", f'Get-ChildItem "{self.raw.as_posix()}/客戶案件" | Remove-Item'),
                          ("PowerShell", f'Get-ChildItem "{self.raw.as_posix()}/客戶案件" | ForEach-Object {{ Remove-Item $_ }}'),
                          ("Bash", f'find "{self.raw.as_posix()}/客戶案件" -name a.txt -delete'),
                          ("Bash", f'cd "{self.raw.as_posix()}/客戶案件" && rm a.txt'),
                          ("Bash", f'cp "{self.raw.as_posix()}/客戶案件/a.txt" "{v}/系統/"'),
                          ("Bash", f'f="{v}/系統/x"; echo hi > $f')):
            self.assertDecision(tool, cmd, "deny")

    def test_writes_after_cd_and_bash_globs_still_denied(self):
        """語料對照舊守門時抓到的：cd 的 Z10b 豁免只給不寫的；Bash 沒分隔符的 ?、[ 在寫入段照展開；dd 的 of=。"""
        o = self.out.as_posix()
        for cmd in (f"cd .. && rm -rf raw? {o}/x", f"mv .sys?em {o}/", f"dd if={o}/x of=../raw/客戶案件/a.txt",
                    f"cd ../raw/行政 && rm -rf 請款 {o}/x"):
            self.assertDecision("Bash", cmd, "deny")

    def test_heredoc_body_naming_excluded_folder_is_read(self):
        o = self.out.as_posix()
        cmd = f"cat > \"{o}/r.md\" <<'EOF'\n{(self.raw / '行政' / '薪資').as_posix()}/s.xlsx\nEOF"
        self.assertDecision("Bash", cmd, "deny", zone="Z10")


class Drive(Base):
    """驗收 12：B-022 不帶磁碟代號的路徑補目前的磁碟；B-088 工具夾的 cacert.pem 不是密碼檔。"""

    @unittest.skipUnless(os.name == "nt", "磁碟代號只在 Windows")
    def test_rootless_path_gets_current_drive(self):
        rootless = self.V.as_posix()[2:]
        self.assertDecision("Bash", f'echo x > "{rootless}/系統/x"', "deny", zone="Z6")
        self.assertDecision("Write", f"{rootless}/.system/sources.json", "deny", zone="Z7")
        back = str(self.V)[2:]
        self.assertDecision("Bash", f'cat "{back}\\系統\\x"', "pass")
        d = guard.decide(payload("Read", f"{back}\\系統\\客戶設定檔.md", self.V))
        self.assertEqual((d.decision, d.zone), ("allow", "Z6"))

    def test_tools_folder_and_plugin_folder_not_credentials(self):
        tools = self.tmp / "localapp" / "wiki-with-willy" / "tools" / "pylib" / "certifi"
        tools.mkdir(parents=True)
        (tools / "cacert.pem").write_text("x", encoding="utf-8")
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.tmp / "localapp")
        try:
            self.assertDecision("Read", tools / "cacert.pem", "pass")
            self.assertDecision("Bash", f'ls "{tools.as_posix()}"', "pass")
            self.assertDecision("Grep", tools.parent, "pass")
            self.assertDecision("Read", ROOT / "scripts" / "kb" / "_lib" / "credentials.py", "pass")
            self.assertDecision("Bash", f'cat "{(ROOT / "scripts" / "kb" / "_lib" / "credentials.py").as_posix()}"', "pass")
            other = self.tmp / "other" / "cacert.pem"
            other.parent.mkdir()
            other.write_text("x", encoding="utf-8")
            self.assertDecision("Read", other, "deny")
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old


class OutputShell(Base):
    """驗收 12b（D-186 補）：AI 產出照語法拆之後只擋寫；非 kb 執行檔的參數、餵給直譯器的 heredoc 照擋。"""

    def test_reads_and_reports_pass(self):
        v, o = self.V.as_posix(), self.out.as_posix()
        for tool, cmd in (("Bash", f"cat > \"{o}/r.md\" <<EOF\n成品在 AI 產出\\報價\\x.docx\nEOF"),
                          ("Bash", f'grep x "{v}/AI 產出/a.md"'), ("Bash", f'cat "{v}/AI 產出/a.md"'),
                          ("PowerShell", f'Get-Content "{v}/AI 產出/a.md"'), ("Bash", 'ls "AI 產出"'),
                          ("Bash", f'echo "放在 {v}/AI 產出/a.docx"'), ("PowerShell", f'Test-Path "{v}/AI 產出/a.docx"')):
            self.assertDecision(tool, cmd, "pass")

    def test_executables_and_interpreter_heredoc_denied(self):
        v = self.V.as_posix()
        for tool, cmd in (("Bash", f'node build.js "{v}/AI 產出/x.docx"'), ("Bash", f'python x.py "{v}/AI 產出/a.xlsx"'),
                          ("Bash", f'soffice --convert-to pdf "{v}/AI 產出/a.docx"'),
                          ("Bash", f"python - <<EOF\nprint(r'{v}/AI 產出/a.docx')\nEOF"),
                          ("Bash", f"python - <<EOF\nopen(r\"{v}\\AI 產出\\x.docx\",\"w\")\nEOF"),
                          ("Bash", f'echo x > "{v}/AI 產出/x.txt"'), ("Bash", f'cp "{v}/AI 產出/a.md" /tmp/b.md'),
                          ("Bash", 'cd "AI 產出" && node build.js out.docx'), ("Bash", "node build.js AI\\ 產出/新夾/x.docx")):
            d = self.assertDecision(tool, cmd, "deny")
            self.assertEqual(d.reason_code, "output_shell", cmd)


class SecurityReviewRound(Base):
    """收尾自己做的 security-review（子代理被安全分類器停下）：拆開之後的三個洞要擋。"""

    def test_heredoc_word_and_comments_cannot_hide_commands(self):
        src = (self.raw / "客戶案件").as_posix()
        for tool, cmd in (("Bash", f"echo hi # <<EOF\nrm -rf \"{src}\"\nEOF"),
                          ("Bash", f"cat <<E@F\nE@F\nrm -rf \"{src}\"\n"),
                          ("Bash", f"cat <<'E'\"O\"F\nEOF\nrm -rf \"{src}\"\n"),
                          ("PowerShell", f"# @'\nRemove-Item \"{src}/a.txt\"\n'@"),
                          ("PowerShell", f"Write-Host '@'\nRemove-Item \"{src}/a.txt\"")):
            self.assertDecision(tool, cmd, "deny")
        o = self.out.as_posix()
        self.assertDecision("Bash", f"cat > \"{o}/r.md\" <<'EOF'\n# 標題 <<X\nEOF", "pass")

    def test_pipeline_writes_that_take_paths_from_stdin(self):
        src, o = (self.raw / "行政" / "請款").as_posix(), self.out.as_posix()  # 沒有密碼檔的夾，才看得出是寫入欄擋的
        for tool, cmd in (("Bash", f'find "{src}" -type f | xargs rm -f "{o}/x"'),
                          ("Bash", f'ls "{src}"/* | xargs -I{{}} cp {{}} "{o}/"'),
                          ("PowerShell", f'Get-ChildItem "{src}" | Copy-Item -Destination "{o}"'),
                          ("PowerShell", f'Get-ChildItem "{src}" | Remove-Item -Force -ErrorAction SilentlyContinue "{o}/x"')):
            self.assertDecision(tool, cmd, "deny")
        self.assertDecision("Bash", f'cat "{src}/a.txt" | tee "{o}/b.txt"', "pass")

    def test_cd_into_parent_of_excluded_only_for_listing(self):
        adm = (self.raw / "行政").as_posix()
        self.assertDecision("Bash", f'cd "{adm}" && ls -d */', "deny")
        for cmd in (f'cd "{adm}" && python -c "print(open(\'薪資/s.xlsx\').read())"',
                    f'cd "{adm}" && python "{self.out.as_posix()}/read.py"', f'cd "{adm}" && wc -c 薪*/*'):
            self.assertDecision("Bash", cmd, "deny")
        self.assertDecision("Bash", f'cd "{adm}"', "pass")
        self.assertDecision("Bash", f'cd "{adm}" && pwd', "pass")


if __name__ == "__main__":
    unittest.main()
