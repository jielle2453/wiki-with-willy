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


class Round28(Base):
    """測試 28（a2 轉）：指令替換與展開、here-string 經變數餵直譯器；語料同一批在 guard_corpus（來源標測試 28）。"""

    def test_substitutions_are_commands(self):
        v = self.V.as_posix()
        t = f'"{v}/系統/待處理.md"'
        for tool, cmd in (("Bash", f"echo $(rm {t})"), ("Bash", f"cat <(rm {t})"), ("Bash", f"cat `rm {t}`"),
                          ("Bash", f"ls $(rm {t})"), ("Bash", f"cat <<EOF\n$(rm {t})\nEOF"),
                          ("Bash", f'cd "{self.out.as_posix()}" && echo $(rm ../vault/系統/待處理.md)'),
                          ("Bash", f'echo $(cd "{self.out.as_posix()}"); rm 系統/待處理.md'),
                          ("PowerShell", f"Write-Output $(Remove-Item '{v}/系統/x')"),
                          ("PowerShell", f"Write-Host (Remove-Item '{v}/系統/x')"),
                          ("PowerShell", f"Get-ChildItem '{v}/系統' | Where-Object {{ Remove-Item '{v}/系統/x' }}")):
            self.assertDecision(tool, cmd, "deny")
        for tool, cmd in (("Bash", f"cat <<'EOF'\n$(rm {t})\nEOF"), ("Bash", f"echo '$(rm {t})'"),
                          ("Bash", f'echo "$(date)" > "{self.out.as_posix()}/d.txt"')):
            self.assertDecision(tool, cmd, "pass")

    def test_herestring_through_variable_into_interpreter(self):
        v = str(self.V)
        self.assertDecision("PowerShell", f"$c = @'\nopen(r\"{v}\\系統\\x\",\"w\").write(\"1\")\n'@\n$c | python -", "deny")


class Round28b(Base):
    """測試 28 第二輪（報告附錄 A）：引號尾端反斜線、導向變體、perl -e、只讀動詞的輸出旗標。語料同一批。"""

    def test_quote_backslash_redirect_variants_perl_and_output_flags(self):
        v, o = self.V.as_posix(), self.out.as_posix()
        t = f'"{v}/系統/待處理.md"'
        for tool, cmd in (("Bash", f"echo 'a\\' ; rm {t}"),
                          ("PowerShell", f"Get-ChildItem 'C:\\Windows\\'; Remove-Item '{v}/系統/待處理.md'"),
                          ("PowerShell", f'Write-Host "C:\\x\\" ; Set-Content \'{v}/系統/x\' -Value 1'),
                          ("Bash", f"echo hi >| {t}"), ("Bash", f"echo hi >& {t}"), ("Bash", f"echo hi >&{t}"),
                          ("Bash", f"perl -e 'unlink q({v}/系統/待處理.md)'"),
                          ("Bash", f'uniq "{o}/a.txt" "{v}/系統/x.txt"'), ("Bash", f'xxd -r "{o}/a.hex" "{v}/系統/x.bin"'),
                          ("Bash", f'less -o "{v}/系統/x.txt" "{o}/a.txt"'), ("Bash", f'tree -o "{v}/系統/x.txt" "{o}"')):
            self.assertDecision(tool, cmd, "deny")
        d = self.assertDecision("Bash", f'sort -o "{v}/AI 產出/x.txt" "{o}/a.txt"', "deny")
        self.assertEqual(d.reason_code, "output_shell")
        for cmd in (f'sort -o "{o}/s.txt" "{o}/a.txt"', 'ls 2>&1', 'ls >&2', f'uniq -c "{o}/a.txt"', f'xxd "{o}/a.bin"',
                    'python -c "import copy, json; print(1)"', 'git commit -m "rename x"'):
            self.assertDecision("Bash", cmd, "pass")


class Round28c(Base):
    """測試 28 第三輪（報告附錄 B）：<<< 餵殼或直譯器、包一層再跑的動詞（xargs、find -exec、setsid、parallel、watch、stdbuf）、
    管線前一段的輸出當寫入目標（kb … | xargs rm）。語料同一批。"""

    def test_herestring_wrappers_and_piped_targets(self):
        v, o = self.V.as_posix(), self.out.as_posix()
        t = f"{v}/系統/待處理.md"
        kb = f'python "{ROOT.as_posix()}/scripts/kb.py"'
        for tool, cmd in (("Bash", f"bash <<< 'rm \"{t}\"'"), ("Bash", f"python <<< \"open('{v}/系統/x','w')\""),
                          ("Bash", f"echo x | xargs sh -c 'rm {t}'"), ("Bash", f'find "{o}" -maxdepth 0 -exec sh -c "rm {t}" \\;'),
                          ("Bash", f"setsid sh -c 'rm {t}'"), ("Bash", f"parallel sh -c 'rm {t}' ::: a"),
                          ("Bash", f"watch -n1 sh -c 'rm {t}'"), ("Bash", f"stdbuf -o0 sh -c 'rm {t}'"),
                          ("Bash", f"{kb} folders find 信義王 | xargs rm -rf"),
                          ("PowerShell", f"{kb} folders find 信義王 | Remove-Item -Recurse")):
            self.assertDecision(tool, cmd, "deny")
        for tool, cmd in (("Bash", "cat <<< 'rm x'"), ("Bash", 'find . -name "*.md" | xargs grep -l 報價'),
                          ("PowerShell", f'Get-Content "{o}/a.txt" | Set-Content "{o}/b.txt"')):
            self.assertDecision(tool, cmd, "pass")


class Round28d(Base):
    """測試 28 第四輪（報告附錄 C）：\; 與行尾續行不是磁碟根（N4）、變數經 <<< 或管線餵殼（N5）、
    寫入目標來自指令替換或檔案清單（N6）。語料同一批。"""

    def test_backslash_tokens_variable_feed_and_substituted_targets(self):
        v, o = self.V.as_posix(), self.out.as_posix()
        t = f"{v}/系統/待處理.md"
        kb = f'python "{ROOT.as_posix()}/scripts/kb.py"'
        for cmd in ('find . -name "*.md" -exec grep -l x {} \\;', "grep a f \\; echo", "grep -rn 報價 \\\n  檔案索引",
                    f'echo "$(date)" > "{o}/d.txt"', f'xargs -a "{o}/list" grep -l x'):
            self.assertDecision("Bash", cmd, "pass")
        self.assertDecision("PowerShell", f'Set-Content "{o}/d.txt" -Value (Get-Date)', "pass")
        for tool, cmd in (("Bash", f"c='rm \"{t}\"'; bash <<< \"$c\""), ("Bash", f"c='rm \"{t}\"'; echo \"$c\" | bash"),
                          ("Bash", f"rm -rf $({kb} folders find 信義王)"), ("Bash", "rm $(cat list.txt)"),
                          ("Bash", f'xargs -a "{o}/list" rm'), ("PowerShell", "Remove-Item (Get-Content list.txt)")):
            self.assertDecision(tool, cmd, "deny")


class SecretActionAsk(Base):
    """D-212（測試 28 F7）：kb unlock get、kb ruling add --kind 可以讀、kb unlock authorize、kb convert --saved … --explicit
    回 ask（客戶按允許才跑），理由句說是哪個動作、不帶密碼值；其他 kb 呼叫照舊 allow。子命令與旗標照 kb 自己的 argparse 認
    （縮寫、--x=值、順序），引號照工具的規則；認不出來的照 ask。"""

    def kb(self, rest):
        return f'python "{ROOT.as_posix()}/scripts/kb.py" {rest}'

    def test_four_actions_ask_with_reason(self):
        cases = (
            ("unlock get --name 工地大門", "要把存好的「工地大門」密碼顯示出來"),
            ("unlock authorize --name 保險單 --label 保險單 --example f-1a2b3c4d", "要讓「保險單」這一種的檔都用「保險單」這組密碼自動開"),
            ("convert 保險單_陳.pdf --saved 保險單 --explicit", "要用存好的「保險單」密碼開「保險單_陳.pdf」這一份"),
            ("convert --explicit 保險單_陳.pdf --saved=保險單", "要用存好的「保險單」密碼開「保險單_陳.pdf」這一份"),
            ("convert 保險單_陳.pdf --saved 保險單 --exp", "要用存好的「保險單」密碼開「保險單_陳.pdf」這一份"),
            ("--json unlock get --name 工地大門", "要把存好的「工地大門」密碼顯示出來"),
        )
        for tool in ("Bash", "PowerShell"):
            for rest, reason in cases:
                d = self.assertDecision(tool, self.kb(rest), "ask")
                with self.subTest(tool=tool, rest=rest):
                    self.assertEqual((d.reason_code, d.reason), ("secret_action", "需要客戶按允許才會執行：" + reason))
        self.assertDecision("Bash", self.kb("'unlock' \"get\" --name '工地大門'"), "ask")
        self.assertDecision("PowerShell", self.kb("'unlock' 'get' --name '工地''大門'"), "ask")

    def test_unknown_or_uncertain_asks(self):
        for tool, rest in (("Bash", "unlock $x --name a"), ("Bash", "ruling add --kind \"$k\" --file a --text x"),
                           ("PowerShell", "unlock $x --name a"), ("Bash", "$c get --name a"),
                           ("Bash", "ruling add --kind 可以* --file a --text x")):
            self.assertDecision(tool, self.kb(rest), "ask")
        # argparse 拆不過的（打錯子命令、多了不認得的旗標）：守門用的就是 kb.py 那一份 argparse，kb 什麼都不做，照常放行
        for rest in ("convert a.pdf --saved n --explicit --bogus", "unlok get --name a"):
            self.assertDecision("Bash", self.kb(rest), "allow")
        # 不是這三個子命令的，其他參數有變數照常放行
        for tool, rest in (("Bash", "version {a,b}"), ("PowerShell", "version @args"), ("Bash", "cards list --entity \"$e\"")):
            self.assertDecision(tool, self.kb(rest), "allow")

    def test_other_kb_calls_still_allowed(self):
        for rest in ("convert 保險單_陳.pdf --saved 保險單", "convert 保險單_王.pdf --password \"Zq7x-only\"",
                     "unlock save --kind 記下的密碼 --name 工地大門 --value \"Zq7x-gate\"", "unlock list",
                     "unlock forget --name 工地大門", "unlock revoke --label 保險單", "ruling decline --file 網站密碼.xlsx",
                     "ruling add --kind 口述為準 --entity 信義王 --text x", "ruling add --kind 不採用 --card f-1a2b3c4d --text x",
                     "ruling list", "version", "cards todo --json", "unlock get --help", "--help"):
            for tool in ("Bash", "PowerShell"):
                d = self.assertDecision(tool, self.kb(rest), "allow")
                with self.subTest(tool=tool, rest=rest):
                    self.assertNotIn("Zq7x", d.reason)

    def test_reason_never_carries_a_value(self):
        d = self.assertDecision("Bash", self.kb("convert a.pdf --saved 保險單 --explicit --password \"Zq7x-secret\""), "ask")
        self.assertNotIn("Zq7x", d.reason)
        d = self.assertDecision("Bash", self.kb("unlock authorize --name 門 --label 門禁 --example \"Zq7x-secret\""), "ask")
        self.assertNotIn("Zq7x", d.reason)


class Round28e(Base):
    """測試 28 第四輪三件小的：unparsed 理由句不叫 AI 去問客戶；Bash 動詞本身是變數、eval 間接展開照 unparsed 擋；
    PowerShell Set-Content／Add-Content 位置參數與巢狀括號的值不算目標。語料同一批。"""

    def test_reason_variable_verb_and_positional_values(self):
        v, o = self.V.as_posix(), self.out.as_posix()
        d = self.assertDecision("Bash", f"cmd='rm'; $cmd \"{v}/系統/待處理.md\"", "deny")
        self.assertEqual((d.reason_code, d.reason), ("unparsed", "看不出這個指令會寫到哪；請用檔案工具，或把完整路徑直接寫在指令裡（不要用 $(…)、反引號或變數）"))
        self.assertDecision("Bash", "v=c; c='rm x'; eval \"${!v}\"", "deny")
        for cmd in (f'Set-Content "{o}/d.txt" (Get-Date)', f'Add-Content "{o}/d.txt" (Get-Date)',
                    f"Set-Content \"{o}/d.txt\" -Value ((Get-Date).ToString('o'))"):
            self.assertDecision("PowerShell", cmd, "pass")
        for cmd in (f'Set-Content "{v}/系統/x.txt" (Get-Date)', 'Set-Content (Get-Content list.txt) "x"',
                    "Remove-Item -Recurse (Get-Content list.txt)"):
            self.assertDecision("PowerShell", cmd, "deny")



class SecretReadLabel(unittest.TestCase):
    """第五輪 S2：「可以讀」的理由句帶「別名/上一層夾/檔名」分得出同名檔；給的檔對不到清冊直接 deny（找不到這份檔），不跳 ask。
    S1：ask 的理由句一律以「需要客戶按允許才會執行：」開頭。"""

    @classmethod
    def setUpClass(cls):
        from tests import _pw
        cls.pw = _pw
        cls.tmp, cls.V, cls.raw = _pw.make_pw_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decide(self, rest, tool="Bash"):
        return guard.decide(payload(tool, f'python "{ROOT.as_posix()}/scripts/kb.py" {rest}', self.V))

    def test_label_has_alias_and_parent(self):
        from tests import _cards
        entry = _cards.entry_of(self.V, self.pw.BANK)
        alias, rel = entry["locations"][0]["uri"][len("file://"):].split("/", 1)
        want = "需要客戶按允許才會執行：要讓 AI 讀「" + "/".join([alias] + rel.split("/")[-2:]) + "」"
        self.assertTrue(want.endswith("/信義王/網銀密碼.txt」"), want)
        path = (self.raw / self.pw.BANK).as_posix()
        for rest in (f'ruling add --kind 可以讀 --file "{path}" --text x', f"ruling add --kind=可以讀 --file {entry['id']} --text x",
                     f'ruling add --kin 可以讀 --file "{entry["locations"][0]["uri"]}" --text x'):
            for tool in ("Bash", "PowerShell"):
                d = self.decide(rest, tool)
                with self.subTest(rest=rest, tool=tool):
                    self.assertEqual((d.decision, d.reason_code, d.reason), ("ask", "secret_action", want))

    def test_unknown_file_denies(self):
        for rest in ('ruling add --kind 可以讀 --file 網站密碼.xlsx --text x', "ruling add --kind 可以讀 --file f-00000000 --text x",
                     f'ruling add --kind 可以讀 --file "{(self.raw / "客戶案件" / "沒有這份.txt").as_posix()}" --text x'):
            d = self.decide(rest)
            with self.subTest(rest=rest):
                self.assertEqual((d.decision, d.reason), ("deny", "找不到這份檔"))

    def test_every_ask_has_prefix(self):
        for rest in ("unlock get --name 門", "unlock authorize --name a --label b", "convert x.pdf --saved a --explicit",
                     "unlock $x --name a"):
            d = self.decide(rest)
            with self.subTest(rest=rest):
                self.assertEqual(d.decision, "ask")
                self.assertTrue(d.reason.startswith("需要客戶按允許才會執行："), d.reason)



class ConfirmListPipe(Base):
    """桌面版確認 #10、#11：有密碼檔的夾，列檔名接只處理文字的管線照 D-185 放行；接讀內容的照擋。語料同一批。"""

    def test_list_then_text_filters(self):
        r = (self.raw / "客戶案件").as_posix()
        for tool, cmd in (("Bash", f'ls "{r}" | head -5'), ("Bash", f'ls "{r}" | sort | wc -l'),
                          ("PowerShell", f'Get-ChildItem "{r}" | Select-Object -ExpandProperty Name'),
                          ("PowerShell", f"Get-ChildItem \"{r}\" | Where-Object Name -like '*報價*' | Sort-Object Name")):
            self.assertDecision(tool, cmd, "pass")
        for tool, cmd in (("Bash", f'ls "{r}" | xargs cat'), ("Bash", f'ls "{r}" | grep -r 密碼'),
                          ("PowerShell", f'Get-ChildItem "{r}" | Get-Content'),
                          ("PowerShell", f'Get-ChildItem "{r}" | ForEach-Object {{ Get-Content $_ }}'),
                          ("PowerShell", f"Get-ChildItem \"{r}\" | Where-Object {{ (Get-Content $_) -match 'x' }}")):
            self.assertDecision(tool, cmd, "deny")



class Round28g(Base):
    """28 第七輪中途回報：filter 段改白名單。Bash 只收旗標、旗標的數字值與 grep 的一個樣式，任何參數含 ? [ ] * { } ~ $ 或反斜線就不算；
    PowerShell 的 filter 段出現 { 就不算（不再用讀檔指令的黑名單）。語料同一批。"""

    def test_filter_whitelist(self):
        d = self.raw / "客戶案件" / "信義王"
        d.mkdir(parents=True, exist_ok=True)
        (d / "網銀密碼.txt").write_text("x", encoding="utf-8")
        cd = f'cd "{d.as_posix()}" && ls | '
        for cmd in ("grep -h 密碼 網銀密碼.tx?", "head -5 $'網銀密碼.txt'", "head -5 網銀密?.txt", "wc $\"網銀密碼.txt\"",
                    "sort 網銀密碼.txt", "grep '密碼.*'"):
            self.assertDecision("Bash", cd + cmd, "deny")
        for cmd in ("head -n 5", "grep 報價", "sort | wc -l", "sort -rn", "cut -d , -f 1", "grep -iv 報價", "grep -e 報價 -c",
                    "tail -n +3"):
            self.assertDecision("Bash", cd + cmd, "pass")
        # H1 補：旗標白名單、輸入導向、cd 後的相對路徑
        for cmd in ("sort -m", "wc --files0-from=網銀密碼.txt", "grep -f 網銀密碼.txt", "head -q", "head < 網銀密碼.txt",
                    "grep x 0< 網銀密碼.txt"):
            self.assertDecision("Bash", cd + cmd, "deny")
        self.assertDecision("Bash", f'cd "{d.parent.as_posix()}" && cd 信義王 && ls | head 網銀密碼.txt', "deny")
        gci = f'Get-ChildItem "{d.as_posix()}" | '
        for cmd in ("Where-Object { clc $_.FullName }", "Where-Object { findstr keep $_.FullName }",
                    "Sort-Object { certutil -dump $_.FullName }", "Measure-Object -Property { strings $_.FullName }",
                    "Where-Object { g`c $_ }"):
            self.assertDecision("PowerShell", gci + cmd, "deny")
        for cmd in ("Where-Object Length -gt 5", "Select-Object Name", "Sort-Object Length", "Measure-Object"):
            self.assertDecision("PowerShell", gci + cmd, "pass")


if __name__ == "__main__":
    unittest.main()
