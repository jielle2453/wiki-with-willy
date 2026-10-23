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

    def test_bad_sources_json_bash_denies_and_logs(self):
        """密碼與守門 D-186 第 4 點、驗收 13：知識庫裡的 Bash／PowerShell 守門出錯也擋（以前不表態等於放行）。"""
        (self.V / ".system" / "sources.json").write_text("{bad json", encoding="utf-8")
        for tool in ("Bash", "PowerShell"):
            r = run_hook(payload(tool, "git status", self.V))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(decision_of(r)[1:], ("deny", "守門出錯，這次不放行。說「檢查」我看原因"))
        self.assertTrue((self.V / ".system" / "hook-errors.log").exists())
        rows = (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8").splitlines()[1:]
        self.assertTrue(all(r.split("\t")[2] == "deny" and r.split("\t")[4] == "guard_error" for r in rows), rows)

    def test_guard_error_outside_vault_stays_silent(self):
        r = run_hook(payload("Bash", "git status", self.tmp / "nowhere"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hooks_json_blocks_on_failure(self):
        """D-186 第 4 點：hook 本身掛掉、逾時、起不來時 Claude Code 擋下（onFailure: block，Claude Code 2.1.295 起）。"""
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        hooks = [h for group in cfg["hooks"]["PreToolUse"] for h in group["hooks"]]
        self.assertTrue(hooks)
        for h in hooks:
            self.assertEqual(h.get("onFailure"), "block", h)


    def test_secret_action_asks_and_ledger_records_ask(self):
        """D-212：四種密碼動作印 ask（Claude Code 跳允許詢問），ledger 記 ask；理由句與 ledger 都不帶密碼值。"""
        r = run_hook(payload("Bash", f'python "{ROOT}/scripts/kb.py" unlock get --name 工地大門', self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(decision_of(r)[1:], ("ask", "需要客戶按允許才會執行：要把存好的「工地大門」密碼顯示出來"))
        r = run_hook(payload("Bash", f'python "{ROOT}/scripts/kb.py" convert a.pdf --saved 門 --explicit --password "zq-7731-secret"', self.V))
        self.assertEqual(decision_of(r)[1], "ask")
        self.assertNotIn("zq-7731-secret", r.stdout)
        rows = [line.split("\t") for line in (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8").splitlines()[1:]]
        self.assertEqual([(row[2], row[4]) for row in rows], [("ask", "secret_action")] * 2, rows)
        self.assertNotIn("zq-7731-secret", (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8"))

    def test_ledger_head_is_masked(self):
        """密碼與守門 8：擋下時 ledger 記的指令開頭先遮值（--value、密碼：…）。"""
        cmd = f'python “{ROOT}/scripts/kb.py” unlock save --kind 記下的密碼 --name 門 --value "zq-7731-secret"'
        r = run_hook(payload("PowerShell", cmd, self.V))
        self.assertEqual(decision_of(r)[1], "deny")
        r = run_hook(payload("Bash", f'echo "密碼：zq-7731-secret" > "{self.V.as_posix()}/系統/x.txt"', self.V))
        self.assertEqual(decision_of(r)[1], "deny")
        text = (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8")
        self.assertEqual(len(text.splitlines()), 3, text)
        self.assertNotIn("zq-7731-secret", text)

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
        return self.turn.entries(self.V, turn_id or self.t["turn"])

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

    def test_held_lock_denies_backup_failed_within_hook_budget(self):
        """第一輪 M1（驗收 21）：殘留的 manifest.jsonl.lock（mtime 現在）3 到 4 秒內 deny backup_failed，不超過 10 秒。"""
        import time
        vdir = self.turn.turn_dir(self.V, self.t["turn"])
        lock = vdir / "manifest.jsonl.lock"
        lock.write_text("busy", encoding="utf-8")
        t0 = time.time()
        r = self.write(self.card)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 10)
        self.assertGreaterEqual(elapsed, 2.9)
        _event, decision, reason = decision_of(r)
        self.assertEqual(decision, "deny")
        self.assertIn("備份失敗", reason)
        self.assertEqual(self.ledger_rows()[-1][4], "backup_failed")
        self.assertFalse((vdir / "files").exists())
        old = time.time() - 60
        os.utime(lock, (old, old))
        r = self.write(self.card)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        self.assertFalse(lock.exists())
        self.assertEqual(len(self.manifest()), 1)

    def test_git_bash_path_form_is_backed_up_correctly(self):
        """第一輪 L1（驗收 24）：file_path 用 /c/Users/... 寫法。"""
        if os.name != "nt":
            self.skipTest("Git Bash 寫法只在 Windows 有意義")
        p = str(self.card).replace("\\", "/")
        gb = "/" + p[0].lower() + p[2:]
        r = self.write(gb)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        copy = self.V / ".system" / "versions" / self.t["turn"] / "files" / "案子" / "信義王" / "信義王.md"
        self.assertTrue(copy.is_file())
        self.assertEqual(self.manifest()[0]["path"], "案子/信義王/信義王.md")

    def test_long_prefix_and_tilde_forms_are_backed_up_correctly(self):
        """第二輪 R2-L2／L7（驗收 35）：\\\\?\\<絕對路徑> 與 ~/… 都 allow 且 manifest path 正確。"""
        if os.name != "nt":
            self.skipTest("這些寫法在 Windows 驗")
        from pathlib import Path
        resolved = self.card.resolve()
        r = self.write("\\\\?\\" + str(resolved))
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        self.assertEqual(self.manifest()[0]["path"], "案子/信義王/信義王.md")
        home = Path(os.path.expanduser("~")).resolve()
        if str(resolved).lower().startswith(str(home).lower() + os.sep):
            t2 = self.turn.start(self.V, self.SID, "再來")
            tilde = "~/" + str(resolved)[len(str(home)) + 1:].replace("\\", "/")
            r = self.write(tilde)
            self.assertEqual(decision_of(r)[1], "allow", r.stdout)
            self.assertEqual(self.manifest(t2["turn"])[0]["path"], "案子/信義王/信義王.md")

    def test_whole_config_not_object_still_allows_and_logs(self):
        """第二輪 L5 補（驗收 34）。"""
        (self.V / ".system" / "config.json").write_text("5", encoding="utf-8")
        r = self.write(self.card)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("config.json", log[0])

    def test_bad_manifest_line_still_allows_and_logs(self):
        """第一輪 L4（驗收 25）。"""
        self.write(self.card)
        m = self.V / ".system" / "versions" / self.t["turn"] / "manifest.jsonl"
        with open(m, "a", encoding="utf-8", newline="\n") as f:
            f.write("{bad\n")
        idx = self.V / "檔案索引" / "i.md"
        idx.write_text("索引", encoding="utf-8")
        r = self.write(idx)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(log), 1)
        self.assertIn("manifest", log[0])
        self.assertEqual(self.manifest()[-1]["path"], "檔案索引/i.md")

    def test_bad_config_type_still_allows_and_logs(self):
        """第一輪 L5（驗收 26）。"""
        (self.V / ".system" / "config.json").write_text('{"big_change_synthesis_max": "十"}', encoding="utf-8")
        r = self.write(self.card)
        self.assertEqual(decision_of(r)[1], "allow", r.stdout)
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("big_change_synthesis_max", log[0])

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

    def test_credential_path_denied_and_ledgered(self):
        """骨架與來源登記（D-079）：Read 憑證檔名 → stdout deny JSON，ledger 一行 reason_code credential。"""
        r = run_hook(payload("Read", self.raw / "客戶案件" / ".env", self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        _event, decision, reason = decision_of(r)
        self.assertEqual(decision, "deny")
        self.assertIn("密碼或金鑰", reason)
        rows = [ln.split("\t") for ln in (self.V / ".system" / "ledger.tsv").read_text(encoding="utf-8").splitlines()[1:]]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0][1], rows[0][2], rows[0][3], rows[0][4]), ("Read", "deny", "Z9", "credential"))

    def test_32_escaped_quote_payload_via_hook(self):
        evil = f'python "{ROOT}/scripts/kb.py" version \\"; rm -rf {self.raw}/客戶案件; echo \\"'
        r = run_hook(payload("Bash", evil, self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout == "" or decision_of(r)[1] == "deny", r.stdout)


if __name__ == "__main__":
    unittest.main()
