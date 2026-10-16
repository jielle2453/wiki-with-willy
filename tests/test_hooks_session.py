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
