"""hooks/turn.py 黑箱：subprocess 餵 UserPromptSubmit payload。開回合、task-notification 不開、非使用者來源不開、
不在知識庫不開、壞 JSON exit 0 無輸出。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import ROOT, make_vault

HOOK = ROOT / "hooks" / "turn.py"
SID = "test-sid"


def run_hook(data, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def prompt_payload(cwd, prompt, **extra):
    data = {"session_id": SID, "cwd": str(cwd), "prompt": prompt, "hook_event_name": "UserPromptSubmit"}
    data.update(extra)
    return data


class TurnHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.turns = self.V / ".system" / "turns"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_opens_turn_silently(self):
        r = run_hook(prompt_payload(self.V, "改一下"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        cur = json.loads((self.turns / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["turn"], cur["turn"])
        self.assertEqual(sess["prompt_head"], "改一下")
        self.assertFalse(sess["approved"])

    def test_61_63_prompt_head_is_masked_everywhere(self):
        """D-081、B-027：帳密不進回合檔；versions list 印的問也是遮過的。"""
        r = run_hook(prompt_payload(self.V, "幫我登入 ERP，帳號 jie 密碼 abc123"))
        self.assertEqual(r.returncode, 0, r.stderr)
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["prompt_head"], "幫我登入 ERP，帳號███")  # 第六輪 W3：遮到子句尾
        self.assertNotIn("jie", sess["prompt_head"])
        from kb._lib import turn
        turn.backup(self.V, "案子/信義王/信義王.md", sess["turn"])
        kb = ROOT / "scripts" / "kb.py"
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        out = subprocess.run([sys.executable, str(kb), "versions", "list"], capture_output=True, text=True, encoding="utf-8",
                             cwd=str(self.V), env=env).stdout
        self.assertIn("███", out)
        self.assertNotIn("abc123", out)
        for p in (self.V / ".system").rglob("*"):
            if p.is_file():
                self.assertNotIn("abc123", p.read_text(encoding="utf-8", errors="ignore"), p)

    def test_task_notification_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "<task-notification>done</task-notification>"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_cross_session_message_does_not_open(self):
        """確認後裁定 L-3：跨 session 訊息不是客戶的話，不開回合（標頭不能變成 prompt_head 印給客戶）。"""
        r = run_hook(prompt_payload(self.V, '<cross-session-message from="uds:x" from-name="llm-wiki-j-51">改一下</cross-session-message>'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_system_reminder_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "  <system-reminder>\n內部提醒\n</system-reminder>"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_non_user_source_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "改一下", source="scheduled"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.turns.exists())
        for ok in ("user", "sdk"):
            r = run_hook(prompt_payload(self.V, "改一下", source=ok))
            self.assertTrue((self.turns / f"{SID}.json").exists(), ok)

    def test_outside_vault_does_nothing(self):
        r = run_hook(prompt_payload(self.tmp, "改一下"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())
        self.assertFalse((self.tmp / ".system").exists())

    def test_bad_json_is_silent_exit_0(self):
        r = run_hook("not json", cwd=self.V)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "turn")  # 第一輪 L7：第二欄是哪支 hook

    def test_bad_json_outside_vault_is_silent_exit_0(self):
        r = run_hook("[1, 2]")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_registered_in_hooks_json_as_user_prompt_submit(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["UserPromptSubmit"]
        cmds = [h["command"] for g in groups for h in g["hooks"]]
        self.assertEqual(len(cmds), 1, cmds)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/turn.py", cmds[0])
        self.assertEqual(groups[0]["hooks"][0]["timeout"], 5)
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)  # PreToolUse 不動

    def test_second_prompt_opens_new_turn(self):
        run_hook(prompt_payload(self.V, "第一句"))
        first = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))["turn"]
        run_hook(prompt_payload(self.V, "第二句"))
        second = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first, second["turn"])
        self.assertEqual(second["prompt_head"], "第二句")


if __name__ == "__main__":
    unittest.main()
