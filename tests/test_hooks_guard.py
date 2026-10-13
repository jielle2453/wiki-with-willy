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


class Transaction(unittest.TestCase):
    """事務層：檔案工具寫 Z1／Z3／Z5 放行前先回合備份；第 N+1 張既有綜合卡 deny big_change；備份失敗 deny backup_failed。"""

    SID = "test-sid"

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        from kb._lib import turn
        self.turn = turn
        self.t = turn.start(self.V, self.SID, "改一下")
        self.card = self.V / "案子" / "信義王" / "信義王.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, tool="Write"):
        data = payload(tool, path, self.V)
        data["session_id"] = self.SID
        return run_hook(data)

    def manifest(self, turn_id=None):
        m = self.V / ".system" / "versions" / (turn_id or self.t["turn"]) / "manifest.jsonl"
        return [json.loads(x) for x in m.read_text(encoding="utf-8").splitlines() if x.strip()] if m.exists() else []

    def ledger_rows(self):
        p = self.V / ".system" / "ledger.tsv"
        return [ln.split("\t") for ln in p.read_text(encoding="utf-8").splitlines()[1:]] if p.exists() else []

    def test_write_existing_card_backs_up_once(self):
        before = self.card.read_bytes()
        r = self.write(self.card)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        copy = self.V / ".system" / "versions" / self.t["turn"] / "files" / "案子" / "信義王" / "信義王.md"
        self.assertEqual(copy.read_bytes(), before)
        rows = self.manifest()
        self.assertEqual([(e["path"], e["action"]) for e in rows], [("案子/信義王/信義王.md", "modify")])
        self.write(self.card, "Edit")
        self.assertEqual(len(self.manifest()), 1)
        self.assertEqual(self.ledger_rows(), [])

    def test_write_new_card_records_create_without_copy(self):
        new = self.V / "案子" / "新卡" / "新卡.md"
        r = self.write(new)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        self.assertEqual(self.manifest()[-1]["action"], "create")
        self.assertFalse((self.V / ".system" / "versions" / self.t["turn"] / "files" / "案子" / "新卡").exists())

    def test_write_z3_and_z5_back_up(self):
        out = self.V / "AI 產出" / "x.md"
        out.write_text("舊", encoding="utf-8")
        idx = self.V / "檔案索引" / "i.md"
        idx.write_text("索引", encoding="utf-8")
        self.assertEqual(decision_of(self.write(out))[1], "allow")
        self.assertEqual(decision_of(self.write(idx))[1], "allow")
        files = self.V / ".system" / "versions" / self.t["turn"] / "files"
        self.assertEqual((files / "AI 產出" / "x.md").read_text(encoding="utf-8"), "舊")
        self.assertEqual((files / "檔案索引" / "i.md").read_text(encoding="utf-8"), "索引")
        self.assertEqual(len(self.manifest()), 2)

    def test_read_does_not_back_up(self):
        r = self.write(self.card, "Read")
        self.assertEqual(decision_of(r)[1], "allow")
        self.assertFalse((self.V / ".system" / "versions").exists())

    def test_eleventh_card_denied_then_approve_allows_then_new_turn_resets(self):
        cards = []
        for i in range(11):
            d = self.V / "案子" / f"案{i:02d}"
            d.mkdir()
            (d / f"案{i:02d}.md").write_text(f"# {i}\n", encoding="utf-8")
            cards.append(d / f"案{i:02d}.md")
        for p in cards[:10]:
            self.assertEqual(decision_of(self.write(p))[1], "allow", str(p))
        r = self.write(cards[10])
        _event, decision, reason = decision_of(r)
        self.assertEqual(decision, "deny")
        self.assertIn("10 張", reason)
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], "big_change")
        self.assertEqual(rows[0][3], "Z1")
        self.assertEqual(len(self.manifest()), 10)
        self.turn.approve(self.V, self.SID)
        self.assertEqual(decision_of(self.write(cards[10]))[1], "allow")
        self.assertEqual(len(self.manifest()), 11)
        t2 = self.turn.start(self.V, self.SID, "下一句")
        self.assertEqual(decision_of(self.write(cards[0]))[1], "allow")
        self.assertEqual(len(self.manifest(t2["turn"])), 1)

    def test_backup_failure_denies_and_logs(self):
        (self.V / ".system" / "versions").write_text("我是檔不是夾", encoding="utf-8")
        r = self.write(self.card)
        self.assertEqual(r.returncode, 0, r.stderr)
        _event, decision, reason = decision_of(r)
        self.assertEqual(decision, "deny")
        self.assertIn("備份失敗", reason)
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], "backup_failed")
        self.assertEqual(len((self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()), 1)

    def test_no_turn_yet_starts_one(self):
        shutil.rmtree(self.V / ".system" / "turns")
        r = self.write(self.card)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        t = self.turn.current(self.V, self.SID)
        self.assertTrue((self.V / ".system" / "turns" / f"{self.SID}.json").exists())
        self.assertEqual(len(self.manifest(t["turn"])), 1)

    def test_shell_write_to_card_denied_with_ledger(self):
        data = payload("Bash", f"echo hi > {self.card}", self.V)
        r = run_hook(data)
        self.assertEqual(decision_of(r)[1], "deny")
        self.assertIn("用指令改的退不回來", decision_of(r)[2])
        self.assertEqual(self.ledger_rows()[0][4], "shell_write")


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
