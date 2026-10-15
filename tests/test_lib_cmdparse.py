"""_lib/cmdparse：抽路徑、判寫入、判單一 kb.py 呼叫。"""
import shutil
import unittest

from tests._guard_fixture import ROOT, SCRIPTS, make_vault  # noqa: F401
from kb._lib import cmdparse, paths  # noqa: E402

CWD = "C:/x/vault"
PLUGIN = str(ROOT)


def n(p):
    return paths.normalize(p, CWD)


class ExtractPaths(unittest.TestCase):
    def test_quoted_chinese_path_with_space(self):
        got = cmdparse.extract_paths('cat "C:/x/vault/AI 產出/報告 v1.md"', CWD)
        self.assertIn(n("C:/x/vault/AI 產出/報告 v1.md"), got)

    def test_git_bash_path(self):
        got = cmdparse.extract_paths("cat /c/x/raw/行政/薪資/s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)

    def test_backslash_path(self):
        got = cmdparse.extract_paths("type C:\\x\\raw\\行政\\薪資\\s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)

    def test_dot_and_dotdot_are_paths(self):
        """骨架與來源登記第三輪 S1：不帶斜線的 . 與 .. 也當路徑抽出來，經區判斷（grep -r X .. 要走 Z10b）。"""
        tmp, V, _raw = make_vault()
        try:
            self.assertIn(paths.normalize(str(tmp), None), cmdparse.extract_paths("grep -r X ..", str(V)))
            self.assertIn(paths.normalize(str(V), None), cmdparse.extract_paths("ls .", str(V)))
            self.assertIn(paths.normalize(str(tmp), None), cmdparse.extract_paths("cd 收件匣 && cat ../..", str(V)))
            self.assertEqual(cmdparse.extract_paths("echo a.b", str(V)), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_write_words_round8_additions(self):
        """骨架與來源登記第八輪：find -delete、shred、unlink、Tee-Object 等是寫入。"""
        for cmd in ("find C:/x -delete", "shred C:/x/a.txt", "unlink C:/x/a.txt", "Get-Content a | Tee-Object -FilePath C:/x/a.txt",
                    "Set-Content C:/x/a.txt x", "Out-File -FilePath C:/x/a.txt", "robocopy a b", "xcopy a b /s", "rsync -a a b",
                    "Remove-Item a", "Move-Item a b", "Copy-Item a b", "cp.exe a b", "sed -E -i s/a/b/ x.md"):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd), cmd)
        for cmd in ("cat C:/x/a.txt", "find C:/x -name a", "git status", "echo unlinked"):
            with self.subTest(cmd=cmd):
                self.assertFalse(cmdparse.is_write(cmd), cmd)

    def test_bare_filename_token_is_a_path_when_command_writes(self):
        """第八輪驗收 73：cp /tmp/a 新檔.md 的 新檔.md 要抽成路徑（寫入指令裡像檔名的裸 token）。"""
        tmp, V, _raw = make_vault()
        try:
            self.assertIn(paths.normalize(str(V / "新檔.md"), None), cmdparse.extract_paths("cp /tmp/a 新檔.md", str(V)))
            self.assertIn(paths.normalize(str(V / "新檔.md"), None), cmdparse.extract_paths("echo x > 新檔.md", str(V)))
            self.assertNotIn(paths.normalize(str(V / "notes.txt"), None), cmdparse.extract_paths("grep x notes.txt", str(V)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bare_token_existing_under_cwd_or_known_extension(self):
        """第九輪 Z2／Z4：寫入指令裡效用 cwd 下存在的裸夾名或檔名（沒副檔名）算路徑；不存在的裸 token 要已知副檔名才算新檔。"""
        tmp, V, _raw = make_vault()
        try:
            v = str(V)
            self.assertIn(paths.normalize(str(V / "案子"), None), cmdparse.extract_paths("rm -rf 案子 /tmp/x", v))
            self.assertIn(paths.normalize(str(V / "收件匣"), None), cmdparse.extract_paths("mv 收件匣 /tmp/", v))
            self.assertIn(paths.normalize(str(V / "收件匣"), None), cmdparse.extract_paths("tar czf /tmp/b.tgz 收件匣", v))
            self.assertNotIn(paths.normalize(str(V / "czf"), None), cmdparse.extract_paths("tar czf /tmp/b.tgz 收件匣", v))
            self.assertNotIn(paths.normalize(str(V / "v1.2"), None), cmdparse.extract_paths("echo v1.2 > /tmp/ver.txt", v))
            self.assertNotIn(paths.normalize(str(V / "example.com"), None), cmdparse.extract_paths("wget example.com -O /tmp/x", v))
            self.assertIn(paths.normalize(str(V / "新檔.xlsx"), None), cmdparse.extract_paths("cp /tmp/a 新檔.xlsx", v))
            self.assertIn(paths.normalize(str(V / "report.PDF"), None), cmdparse.extract_paths("cp /tmp/a report.PDF", v))
            self.assertNotIn(paths.normalize(str(V / "案子"), None), cmdparse.extract_paths("ls 案子", v))  # 讀取指令照舊
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unresolved_variable_paths(self):
        """第九輪 Z3（裁定 3）：帶 $變數／%變數% 展不開的 token 全部回報（含沒斜線的裸變數），呼叫端對寫入指令 deny unparsed。"""
        self.assertEqual(cmdparse.unresolved_paths('rm -rf "C:/raw/$X" /tmp/y'), ["C:/raw/$X"])
        self.assertEqual(cmdparse.unresolved_paths('rm -rf "%RAW%/a" '), ["%RAW%/a"])
        self.assertEqual(cmdparse.unresolved_paths("echo x > $OUT"), ["$OUT"])
        self.assertEqual(cmdparse.unresolved_paths('cd "$HOME/x" && rm a.txt'), ["$HOME/x"])
        self.assertEqual(cmdparse.unresolved_paths("Remove-Item $env:TEMP\\a.txt"), ["$env:TEMP\\a.txt"])
        self.assertEqual(cmdparse.unresolved_paths('rm -rf "$X" /tmp/y'), ["$X"])
        self.assertEqual(cmdparse.unresolved_paths("Remove-Item $f"), ["$f"])
        self.assertEqual(cmdparse.unresolved_paths("echo $PASSWORD > /tmp/x"), ["$PASSWORD"])
        self.assertEqual(cmdparse.unresolved_paths("rm -rf %X% /tmp/y"), ["%X%"])
        for cmd in ("rm -rf /tmp/y", "curl https://x/$id > /tmp/a", "ls 2>$null", "echo x > $null", "ls | Out-Null"):
            with self.subTest(cmd=cmd):
                self.assertEqual(cmdparse.unresolved_paths(cmd), [], cmd)

    def test_unresolved_paths_narrowed_round10(self):
        """第十輪 A5：裸變數只在整個 token 是 $X、${X}、$X/…、$X\\…（PowerShell 另加 %X%、%X%\\…）才算；
        單引號裡、格式字串（+%Y%m%d、--format=%H）、旗標黏值（-Confirm:$false）、PowerShell 自動變數都不算。"""
        u = cmdparse.unresolved_paths
        self.assertEqual(u("rm -rf ${X} /tmp/y"), ["${X}"])
        self.assertEqual(u("rm -rf $X\\a /tmp/y"), ["$X\\a"])
        self.assertEqual(u("Remove-Item %X%\\a", powershell=True), ["%X%\\a"])
        self.assertEqual(u('echo x > "out$Y"'), ["out$Y"])  # 導向目標裡有變數：還是看不出寫到哪
        for cmd in ("date +%Y%m%d > /tmp/d.txt", "awk '{print $1}' a.txt > /tmp/x", "printf '%s\\n' a > /tmp/p", "git log --format=%H > /tmp/g",
                    "echo '$HOME/x' > /tmp/q", "echo a$b > /tmp/r"):
            with self.subTest(cmd=cmd):
                self.assertEqual(u(cmd), [], cmd)
        for cmd in ("Remove-Item /tmp/x -Confirm:$false", "gci /tmp | ForEach-Object { $_ } | Out-File /tmp/o", "Remove-Item /tmp/x; $null",
                    "Remove-Item $true $false $PSItem $args $LASTEXITCODE $?"):
            with self.subTest(cmd=cmd):
                self.assertEqual(u(cmd, powershell=True), [], cmd)
        self.assertEqual(u("rm -rf $null /tmp/y"), ["$null"])  # Bash 沒有自動變數

    def test_brace_expand(self):
        """第十輪 A1：token 含 {a,b} 展開成各組合（上限 20 組）；沒逗號的大括號不展開。"""
        self.assertEqual(cmdparse.brace_expand("../raw/行政/{薪資,x}/s.xlsx"), ["../raw/行政/薪資/s.xlsx", "../raw/行政/x/s.xlsx"])
        self.assertEqual(cmdparse.brace_expand("../r{2,3}"), ["../r2", "../r3"])
        self.assertEqual(cmdparse.brace_expand("a{1,2}{x,y}"), ["a1x", "a1y", "a2x", "a2y"])
        self.assertEqual(cmdparse.brace_expand("{print $1}"), ["{print $1}"])
        self.assertEqual(cmdparse.brace_expand("plain"), ["plain"])
        self.assertEqual(len(cmdparse.brace_expand("{1,2,3,4,5}{1,2,3,4,5}")), 20)
        got = cmdparse.extract_paths("cat C:/x/raw/行政/{薪資,x}/s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)
        self.assertIn(n("C:/x/raw/行政/x/s.xlsx"), got)
        self.assertIn(n("C:/x/r3"), cmdparse.extract_paths("rm -rf C:/x/r{2,3}", CWD))

    def test_is_wildcard(self):
        """第十輪 A6：候選要含 *，或同時含 ?／[ 與路徑分隔符且不是網址。"""
        for text in ("*.md", "../客戶*", "a/?b", "../r[^x]", "C:\\x\\[a]", "薪?/"):
            with self.subTest(text=text):
                self.assertTrue(cmdparse.is_wildcard(text), text)
        for text in ("[", "]", "Is this ok?", "https://a.b/?x=1", "a?b", "{print $1}", "plain", ""):
            with self.subTest(text=text):
                self.assertFalse(cmdparse.is_wildcard(text), text)
        self.assertEqual(cmdparse.glob_prefixes("echo Is this ok? > /tmp/z", CWD), [])
        self.assertEqual(cmdparse.glob_tokens('curl https://a.b/?x=1 > /tmp/x', CWD), [])

    def test_single_letter_switch_is_not_a_path(self):
        """骨架與來源登記第六輪 V5：cmd /c、findstr /s 的單字母開關不是路徑（Git Bash 不會有人寫裸 /c）。"""
        self.assertEqual(cmdparse.extract_paths("cmd /c ver", CWD), [])
        self.assertEqual(cmdparse.extract_paths("findstr /s x", CWD), [])
        self.assertEqual(cmdparse.extract_paths("robocopy /e /s", CWD), [])
        self.assertEqual(cmdparse.extract_paths("ls; cmd /c dir", CWD), [])
        self.assertIn(n("C:/x/a.txt"), cmdparse.extract_paths("cat /c/x/a.txt", CWD))  # 真正的 Git Bash 路徑照抽
        # 第七輪 X3：只有開關式指令（cmd、findstr、robocopy、xcopy、dir、attrib、icacls）或 PowerShell 才把 /x 當旗標
        self.assertIn(n("C:/"), cmdparse.extract_paths("grep -r TODO /c", CWD))
        self.assertIn(n("C:/"), cmdparse.extract_paths('find /c -name "*.env"', CWD))
        self.assertEqual(cmdparse.extract_paths("foo a /e", CWD, powershell=True), [])
        self.assertIn(n("E:/"), cmdparse.extract_paths("foo a /e", CWD))

    def test_relative_path_joins_cwd(self):
        tmp, V, raw = make_vault()
        try:
            got = cmdparse.extract_paths("ls ../raw/客戶案件", str(V))
            self.assertIn(paths.normalize(str(raw / "客戶案件"), None), got)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_redirect_symbols_are_stripped(self):
        got = cmdparse.extract_paths("echo hi >C:/x/raw/客戶案件/a.txt", CWD)
        self.assertIn(n("C:/x/raw/客戶案件/a.txt"), got)
        got = cmdparse.extract_paths("cat C:/x/raw/客戶案件/a.txt|head", CWD)
        self.assertIn(n("C:/x/raw/客戶案件/a.txt"), got)

    def test_variables_and_discarded_output_are_not_paths(self):
        self.assertEqual(cmdparse.extract_paths("rm $X", CWD), [])
        self.assertEqual(cmdparse.extract_paths("ls 2>/dev/null", CWD), [])
        self.assertEqual(cmdparse.extract_paths("git status", CWD), [])


class IsWrite(unittest.TestCase):
    def test_read_commands_are_not_write(self):
        for cmd in ("cat a.txt", "type a.txt", "grep -rn foo .", "ls -la", "git status", "python x.py 2>/dev/null",
                    'git commit -m "copy of notes"', "cat a.txt 2>&1", "Get-Content a.txt | Out-Null"):
            with self.subTest(cmd=cmd):
                self.assertFalse(cmdparse.is_write(cmd))

    def test_write_commands(self):
        for cmd in ("echo hi > a.txt", "cat a >> b", "rm a.txt", "mv a b", "Set-Content a.txt 'x'",
                    "python -c \"open('x','w').write('1')\"", "sed -i s/a/b/ f.txt", "mkdir foo", "touch a",
                    "Out-File -FilePath a.txt", "New-Item a.txt"):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd))

    def test_redirect_inside_quotes_is_text(self):
        self.assertFalse(cmdparse.is_write('grep "<[^>]+>" a.html'))

    def test_unbalanced_quote_falls_back_to_whole_string(self):
        self.assertTrue(cmdparse.is_write('echo "x > a.txt'))


class SinglePluginCall(unittest.TestCase):
    KB = f'python "{PLUGIN}/scripts/kb.py" version'

    def test_single_call_true(self):
        self.assertTrue(cmdparse.is_single_plugin_call(self.KB, PLUGIN))
        fwd = PLUGIN.replace("\\", "/")
        self.assertTrue(cmdparse.is_single_plugin_call(f"python {fwd}/scripts/kb.py guard decide --tool Read --path x", PLUGIN))
        # N-a：引號外的反斜線一律不是單一呼叫，不加引號的 Windows 路徑要用 /
        self.assertFalse(cmdparse.is_single_plugin_call(f"python {fwd.replace('/', chr(92))}\\scripts\\kb.py version", PLUGIN))
        self.assertTrue(cmdparse.is_single_plugin_call(self.KB.replace("/", "\\"), PLUGIN))

    def test_chained_is_false(self):
        for cmd in (self.KB + " && rm -rf x", self.KB + "; echo 1", self.KB + " | tee out", self.KB + " > out",
                    "$(" + self.KB + ")", "`" + self.KB + "`", "echo 1\n" + self.KB):
            with self.subTest(cmd=cmd):
                self.assertFalse(cmdparse.is_single_plugin_call(cmd, PLUGIN))

    def test_other_script_is_false(self):
        self.assertFalse(cmdparse.is_single_plugin_call(f'python "{PLUGIN}/scripts/other.py"', PLUGIN))
        self.assertFalse(cmdparse.is_single_plugin_call(f'python "{PLUGIN}/scripts/../evil/scripts/kb.py"', PLUGIN))
        self.assertFalse(cmdparse.is_single_plugin_call('python "C:/elsewhere/scripts/kb.py"', PLUGIN))

    def test_outside_quotes(self):
        self.assertEqual(cmdparse.outside_quotes('a "b;c" d'), "a  d")
        self.assertIsNone(cmdparse.outside_quotes('a "b'))


class ReviewGaps(unittest.TestCase):
    """審閱發現的漏洞：引號裡的包裝指令、PowerShell 別名、cd 之後的相對路徑、token 裡夾的絕對路徑、萬用字元。"""

    def test_write_verb_inside_wrapper_quotes_counts(self):
        for cmd in ('bash -c "rm C:/x/a.txt"', "sh -c 'rm a'", 'cmd /c "del C:\\x\\a.txt"', 'powershell -c "Remove-Item C:\\x\\a.txt"',
                    'pwsh -Command "Set-Content C:\\x\\a.txt x"', 'eval "rm a"', 'bash -c "echo hi > C:/x/a.txt"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd))

    def test_commit_message_is_still_not_write(self):
        self.assertFalse(cmdparse.is_write('git commit -m "copy of notes, rm later"'))

    def test_powershell_aliases_and_other_tools(self):
        for cmd in ("ri C:\\x\\a.txt", "sc C:\\x\\a.txt x", "ac C:\\x\\a.txt x", "md C:\\x\\d", "rd C:\\x\\d", "ni C:\\x\\a.txt",
                    "sed --in-place s/a/b/ f", "perl -i -pe s/a/b/ f", "dd if=a of=C:/x/b", "find C:/x -delete", "curl -o C:/x/a http://h",
                    "tar -xf a.tar -C C:/x", "unzip a.zip -d C:/x", "ln -s a C:/x/b", "git clean -fd", "git checkout -- C:/x/a.txt",
                    "Export-Csv C:\\x\\a.csv", "Set-ItemProperty C:\\x\\a.txt"):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd))

    def test_cd_then_relative_resolves_against_cd_target(self):
        tmp, V, raw = make_vault()
        try:
            want = paths.normalize(str(raw / "行政" / "薪資" / "s.xlsx"), None)
            got = cmdparse.extract_paths(f"cd {raw}/行政 && cat 薪資/s.xlsx", str(V))
            self.assertIn(want, got)
            got = cmdparse.extract_paths("cd ../raw/行政; cat 薪資/s.xlsx", str(V))
            self.assertIn(want, got)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_absolute_path_embedded_in_token(self):
        got = cmdparse.extract_paths("python -c \"print(open('C:/x/raw/行政/薪資/s.xlsx','rb').read())\"", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)
        got = cmdparse.extract_paths("P=C:/x/raw/a.txt; rm $P", CWD)
        self.assertIn(n("C:/x/raw/a.txt"), got)
        got = cmdparse.extract_paths("cat file:///C:/x/raw/a.txt", CWD)
        self.assertIn(n("C:/x/raw/a.txt"), got)

    def test_glob_prefixes(self):
        globs = cmdparse.glob_prefixes("cat C:/x/raw/行政/*/s.xlsx; ls C:/x/raw/行政/薪?/", CWD)
        self.assertEqual(globs, [n("C:/x/raw/行政"), n("C:/x/raw/行政")])
        self.assertEqual(cmdparse.glob_prefixes("ls C:/x", CWD), [])



class Round1(unittest.TestCase):
    """第一輪測試後修正：S1 直譯器、S2 PowerShell 子運算式、B1 副檔名、B3 非路徑 token、萬用字元 token。"""

    KB = f'"{PLUGIN}/scripts/kb.py" version'

    def test_s1_interpreter_must_be_bare_or_safe_absolute(self):
        self.assertTrue(cmdparse.is_single_plugin_call("python " + self.KB, PLUGIN))
        self.assertTrue(cmdparse.is_single_plugin_call("python3 " + self.KB, PLUGIN))
        self.assertTrue(cmdparse.is_single_plugin_call("py " + self.KB, PLUGIN))
        self.assertTrue(cmdparse.is_single_plugin_call("python.exe " + self.KB, PLUGIN))
        for bad in ("檔案索引/python ", "./python ", "../python ", "AI\\ 產出/python ", "C:/x/vault/檔案索引/notpython.exe "):
            with self.subTest(interp=bad):
                self.assertFalse(cmdparse.is_single_plugin_call(bad + self.KB, PLUGIN))
        # 第二輪 S1 殘：任何含路徑分隔符的直譯器一律不算，連 vault 外的絕對路徑也不算
        for bad in ("C:/Python312/python.exe ", "C:/Temp/evil/python.exe ", "C:\\Temp\\evil\\python.exe ",
                    "C:/x/vault/檔案索引/python ", "C:/x/raw/客戶案件/python.exe ", "/usr/bin/python3 "):
            with self.subTest(interp=bad):
                self.assertFalse(cmdparse.is_single_plugin_call(bad + self.KB, PLUGIN))

    def test_s2_subexpressions_are_not_single_call(self):
        for tail in (" @(Remove-Item x)", " (rm x)", " $(rm x)", " `rm x`", "; rm x", " @('a')"):
            with self.subTest(tail=tail):
                self.assertFalse(cmdparse.is_single_plugin_call("python " + self.KB + tail, PLUGIN))
        self.assertTrue(cmdparse.is_single_plugin_call('python ' + self.KB + ' --path "a (b)"', PLUGIN))

    def test_b1_extension_is_not_a_write_verb(self):
        for cmd in ("cat CLAUDE.md", "head 系統/待處理.md", "grep TODO 收件匣/a.md", "type C:\\r\\行政\\請款\\notes.md",
                    "cat a.sc", "cat x.cp", "ls notes.ac", "cat README.md | head", "cat a.rm"):
            with self.subTest(cmd=cmd):
                self.assertFalse(cmdparse.is_write(cmd))
        for cmd in ("md x", "rd x", "rm x.md", "cp a.md b.md", "sc a.md x", "cat a.md > b.md"):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd))

    def test_b3_relative_token_must_resolve(self):
        tmp, V, raw = make_vault()
        try:
            cwd = str(V)
            self.assertEqual(cmdparse.extract_paths("sed -i s/a/b/ x", cwd), [])
            self.assertEqual(cmdparse.extract_paths("echo 2026/10/12", cwd), [])
            got = cmdparse.extract_paths('sed -i s/a/b/ "AI 產出/x.md"', cwd)
            self.assertEqual(got, [paths.normalize(str(V / "AI 產出" / "x.md"), None)])
            got = cmdparse.extract_paths("cat 案子/信義王/合約.docx", cwd)
            self.assertEqual(got, [paths.normalize(str(V / "案子" / "信義王" / "合約.docx"), None)])
            got = cmdparse.extract_paths("echo hi > 收件匣/x.txt", cwd)
            self.assertEqual(got, [paths.normalize(str(V / "收件匣" / "x.txt"), None)])
            got = cmdparse.extract_paths("mkdir 案子/新案", cwd)
            self.assertEqual(got, [paths.normalize(str(V / "案子" / "新案"), None)])
            self.assertEqual(cmdparse.extract_paths("echo 新案/新案/x.md", cwd), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_glob_tokens(self):
        tmp, V, raw = make_vault()
        try:
            r = str(raw).replace("\\", "/")
            got = cmdparse.glob_tokens(f"cat {r}/*/薪資/s.xlsx; ls {V}/AI\\ 產出", str(V))
            self.assertEqual(got, [paths.normalize(f"{r}/*/薪資/s.xlsx", None)])
            got = cmdparse.glob_tokens(f"cd {r}/行政 && cat */s.xlsx", str(V))
            self.assertEqual(got, [paths.normalize(f"{r}/行政/*/s.xlsx", None)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)



class Round2(unittest.TestCase):
    """第二輪：N-a 反斜線跳脫引號、N-b lookbehind 退步、N-c 跳脫形態。"""

    KB = f'"{PLUGIN}/scripts/kb.py" version'

    def test_na_escaped_quote_is_not_a_quote_boundary(self):
        bare = cmdparse.outside_quotes('a \\"b; c')
        self.assertIsNotNone(bare)
        self.assertIn(";", bare)
        self.assertEqual(cmdparse.outside_quotes('a "b;c" d'), "a  d")
        self.assertIsNone(cmdparse.outside_quotes('a "b'))

    def test_na_escaped_quote_payload_is_not_single_call(self):
        evil = 'python ' + self.KB + ' \\"; rm -rf C:/x/raw/客戶案件; echo \\"'
        self.assertFalse(cmdparse.is_single_plugin_call(evil, PLUGIN))
        self.assertFalse(cmdparse.is_single_plugin_call('python ' + self.KB + " \\'; rm x; echo \\'", PLUGIN))
        self.assertFalse(cmdparse.is_single_plugin_call('python ' + self.KB + ' --x a\\b', PLUGIN))  # 引號外任何 \ 都不是單一呼叫
        self.assertTrue(cmdparse.is_single_plugin_call('python ' + self.KB.replace("/", "\\"), PLUGIN))  # 引號內的 \ 是路徑
        self.assertTrue(cmdparse.is_write(evil))

    def test_nb_verb_after_slash_or_backslash_counts(self):
        for cmd in ("\\rm -rf C:/x/a", "/bin/rm C:/x/a.txt", "/usr/bin/cp a b", "Microsoft.PowerShell.Management\\Remove-Item C:\\x\\a.txt",
                    "  rm x", "rm x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(cmdparse.is_write(cmd))
        for cmd in ("cat x.md", "ls --rm", "cat x.cp", "cat README.md", "type notes.ac"):
            with self.subTest(cmd=cmd):
                self.assertFalse(cmdparse.is_write(cmd))

    def test_nc_escaped_non_ascii_variant_is_extracted(self):
        got = cmdparse.extract_paths("cat C:/x/raw/行政/薪\\資/s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)
        got = cmdparse.extract_paths("ls C:/x/vault/AI\\ 產出", CWD)
        self.assertIn(n("C:/x/vault/AI 產出"), got)


if __name__ == "__main__":
    unittest.main()
