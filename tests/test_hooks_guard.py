"""hooks/guard.py 黑箱：subprocess 餵 stdin payload，看 stdout JSON、exit code、hook-errors.log。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import HOOK, ROOT, make_vault, payload


def run_hook(data, env_extra=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(data, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env,
    )


def decision_of(r):
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["hookEventName"], out["permissionDecision"], out["permissionDecisionReason"]


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_allow_prints_json(self):
        r = run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        event, decision, reason = decision_of(r)
        self.assertEqual((event, decision), ("PreToolUse", "allow"))
        self.assertTrue(reason)

    def test_deny_prints_json_with_chinese_reason(self):
        r = run_hook(payload("Read", self.raw / "行政" / "薪資" / "s.xlsx", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        event, decision, reason = decision_of(r)
        self.assertEqual(decision, "deny")
        self.assertIn("這個夾你說過不讀", reason)

    def test_pass_prints_nothing(self):
        r = run_hook(payload("Read", "C:/Windows/notepad.exe", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_kb_call_allow(self):
        r = run_hook(payload("Bash", f'python "{ROOT}/scripts/kb.py" version', self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision_of(r)[1], "allow")

    def test_bad_sources_json_denies_file_tool_and_logs(self):
        src = self.V / ".system" / "sources.json"
        src.write_text("{bad json", encoding="utf-8")
        log = self.V / ".system" / "hook-errors.log"
        r = run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision_of(r)[1], "deny")
        self.assertIn("守門出錯", decision_of(r)[2])
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "guard")
        r = run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)

    def test_bad_sources_json_bash_is_silent_but_logs(self):
        (self.V / ".system" / "sources.json").write_text("{bad json", encoding="utf-8")
        r = run_hook(payload("Bash", "git status", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertTrue((self.V / ".system" / "hook-errors.log").exists())

    def test_garbage_stdin_outside_vault_is_silent_and_logged_to_temp(self):
        """P2：找不到 vault 時任何錯誤一律不表態，只寫 %TEMP% 的錯誤檔（驗收 29）。"""
        from kb._lib import ledger
        log = ledger.fallback_error_log()
        before = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True,
                           encoding="utf-8", cwd=str(ROOT), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), before + 1)

    def test_garbage_stdin_inside_vault_cwd_still_denies(self):
        """stdin 壞掉但程序 cwd 在知識庫裡：還是 fail closed。"""
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True,
                           encoding="utf-8", cwd=str(self.V), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision_of(r)[1], "deny")
        self.assertTrue((self.V / ".system" / "hook-errors.log").exists())


class ReviewGaps(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_corrupt_status_file_does_not_change_decision(self):
        import platform
        d = self.V / ".system" / "guard-status"
        d.mkdir()
        (d / f"{platform.node()}.json").write_text("null", encoding="utf-8")
        r = run_hook(payload("Read", self.raw / "客戶案件" / "a.txt", self.V))
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        self.assertEqual(json.loads((d / f"{platform.node()}.json").read_text(encoding="utf-8")).get("python"), platform.python_version())

    def test_list_stdin_outside_vault_is_silent(self):
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(HOOK)], input="[1, 2]", capture_output=True, text=True,
                           encoding="utf-8", cwd=str(ROOT), env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")



class Round2(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_32_escaped_quote_payload_via_hook(self):
        evil = f'python "{ROOT}/scripts/kb.py" version \\"; rm -rf {self.raw}/客戶案件; echo \\"'
        r = run_hook(payload("Bash", evil, self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout == "" or decision_of(r)[1] == "deny", r.stdout)


if __name__ == "__main__":
    unittest.main()
