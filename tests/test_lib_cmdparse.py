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
        self.assertTrue(cmdparse.is_single_plugin_call(f"python {PLUGIN}/scripts/kb.py guard decide --tool Read --path x", PLUGIN))
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
        self.assertTrue(cmdparse.is_single_plugin_call("C:/Python312/python.exe " + self.KB, PLUGIN))
        self.assertFalse(cmdparse.is_single_plugin_call("C:/x/vault/檔案索引/python " + self.KB, PLUGIN,
                                                        forbidden_dirs=[paths.normalize("C:/x/vault", None)]))
        self.assertFalse(cmdparse.is_single_plugin_call("C:/x/raw/客戶案件/python.exe " + self.KB, PLUGIN,
                                                        forbidden_dirs=[paths.normalize("C:/x/raw/客戶案件", None)]))

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


if __name__ == "__main__":
    unittest.main()
