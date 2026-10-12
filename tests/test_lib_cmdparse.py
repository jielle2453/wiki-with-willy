"""_lib/cmdparse：抽路徑、判寫入、判單一 kb.py 呼叫。"""
import unittest

from tests._guard_fixture import ROOT, SCRIPTS  # noqa: F401
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
        got = cmdparse.extract_paths("ls ../raw/客戶案件", CWD)
        self.assertIn(n("C:/x/raw/客戶案件"), got)

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
        got = cmdparse.extract_paths("cd C:/x/raw/行政 && cat 薪資/s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)
        got = cmdparse.extract_paths("cd ../raw/行政; cat 薪資/s.xlsx", CWD)
        self.assertIn(n("C:/x/raw/行政/薪資/s.xlsx"), got)

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


if __name__ == "__main__":
    unittest.main()
