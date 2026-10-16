"""hooks/session.py 黑箱：subprocess 餵 SessionStart payload（D-087、D-060）。第一行 kb 指令前綴、core.md 全文接在空行後、
compact 才有摺疊那行、不在知識庫或壞 JSON 無輸出 exit 0、stdout 純 ASCII、2 秒內、hooks.json 註冊。"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from tests._guard_fixture import ROOT, make_vault

HOOK = ROOT / "hooks" / "session.py"
CORE = ROOT / "rules" / "core.md"
KB_LINE = f'【wiki-with-willy】kb 指令：python "{ROOT.as_posix()}/scripts/kb.py" <子命令>'
COMPACT_LINE = "對話剛摺疊，規則已重新帶入；做到一半的事以工作日誌為準。"
ERROR_LINE = "【wiki-with-willy】開場程式出錯（{}），詳見 .system/hook-errors.log；這個對話不改任何檔。"


def run(data, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def payload(cwd, source="startup"):
    data = {"session_id": "abc", "transcript_path": "x.jsonl", "cwd": str(cwd), "hook_event_name": "SessionStart"}
    if source is not None:
        data["source"] = source
    return data


class SessionHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def context(self, source="startup"):
        r = run(payload(self.V, source))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return out["hookSpecificOutput"]["additionalContext"]

    def test_startup_first_line_is_kb_prefix_and_path_exists(self):
        ctx = self.context("startup")
        self.assertEqual(ctx.splitlines()[0], KB_LINE)
        self.assertTrue((ROOT / "scripts" / "kb.py").is_file())

    def test_core_full_text_after_blank_line(self):
        core = CORE.read_text(encoding="utf-8")
        ctx = self.context("startup")
        self.assertTrue(ctx.endswith("\n\n" + core), ctx[:200])
        self.assertEqual(ctx, KB_LINE + "\n\n" + core)

    def test_compact_adds_second_line(self):
        lines = self.context("compact").splitlines()
        self.assertEqual(lines[:3], [KB_LINE, COMPACT_LINE, ""])

    def test_other_sources_have_no_compact_line(self):
        for source in ("startup", "resume", "clear", None):
            with self.subTest(source=source):
                self.assertNotIn(COMPACT_LINE, self.context(source))

    def test_outside_vault_silent_exit_0(self):
        r = run(payload(ROOT))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def test_bad_json_silent_exit_0_and_logged(self):
        r = run("not json", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[-1].split("\t")[1], "session")

    def test_error_after_vault_found_prints_error_line_and_logs(self):
        """第一輪 D2：找到知識庫之後出錯，除了寫 log，還要印一行出錯的 additionalContext（失敗明講）。
        複製 hooks/session.py、scripts/ 與 manifest 到沒有 rules/ 的暫存夾，讀 core.md 會 FileNotFoundError。"""
        plugin = self.tmp / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        shutil.copy2(HOOK, plugin / "hooks" / "session.py")
        shutil.copytree(ROOT / "scripts", plugin / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / ".claude-plugin", plugin / ".claude-plugin")
        raw = json.dumps(payload(self.V), ensure_ascii=False)
        r = subprocess.run([sys.executable, str(plugin / "hooks" / "session.py")], input=raw, capture_output=True,
                           text=True, encoding="utf-8", cwd=str(self.V), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(plugin)))
        self.assertEqual(r.returncode, 0, r.stderr)
        r.stdout.encode("ascii")
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertEqual(out["additionalContext"], ERROR_LINE.format("FileNotFoundError"))
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "session")

    def test_broken_stdout_pipe_still_exit_0(self):
        """第一輪低項：stdout 讀端先關掉（pipe 斷），write／flush 失敗也要 exit 0、stderr 乾淨（結束時的補 flush 不能再炸）。"""
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        p = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=str(self.V), env=env)
        p.stdout.close()
        p.stdin.write(json.dumps(payload(self.V)).encode("utf-8"))
        p.stdin.close()
        err = p.stderr.read()
        p.stderr.close()
        self.assertEqual(p.wait(timeout=10), 0, err)
        self.assertEqual(err, b"")

    def test_stdout_is_pure_ascii(self):
        r = run(payload(self.V, "compact"))
        self.assertTrue(r.stdout)
        r.stdout.encode("ascii")

    def test_single_run_within_2_seconds(self):
        start = time.monotonic()
        r = run(payload(self.V))
        self.assertEqual(r.returncode, 0)
        self.assertLess(time.monotonic() - start, 2.0)

    def test_registered_in_hooks_json_as_session_start(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "startup|resume|clear|compact")
        hooks = groups[0]["hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["command"], 'python "${CLAUDE_PLUGIN_ROOT}/hooks/session.py"')
        self.assertEqual(hooks[0]["timeout"], 10)
        self.assertTrue(cfg["description"].startswith("開場帶入核心規則與 kb 指令前綴；"))
        self.assertEqual(len(cfg["hooks"]), 5)


if __name__ == "__main__":
    unittest.main()
