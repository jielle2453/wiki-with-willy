"""hooks/stop.py 黑箱：subprocess 餵 Stop payload（D-083）。補完那一行（結果、做了、改了、done）、沒有 last_assistant_message
用固定句、第二次 Stop 不改、沒有回合檔不寫、不在知識庫不寫、壞 JSON exit 0 無輸出、log 第二欄 stop。"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import date

from tests._guard_fixture import ROOT, make_vault
from kb._lib import journal, turn  # noqa: E402

HOOK = ROOT / "hooks" / "stop.py"
TURN_HOOK = ROOT / "hooks" / "turn.py"
SID = "test-sid"


def run(hook, data, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(hook)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def stop_payload(cwd, message=None, sid=SID):
    data = {"session_id": sid, "cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False,
            "transcript_path": "x.jsonl"}
    if message is not None:
        data["last_assistant_message"] = message
    return data


class StopHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.month = date.today().strftime("%Y-%m")
        self.J = journal.jsonl_path(self.V, self.month)
        self.M = journal.md_path(self.V, self.month)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prompt(self, text="改一下"):
        r = run(TURN_HOOK, {"session_id": SID, "cwd": str(self.V), "prompt": text, "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))

    def rows(self):
        return [json.loads(x) for x in self.J.read_text(encoding="utf-8").splitlines() if x.strip()] if self.J.exists() else []

    def test_completes_line_note_by_program_and_marks_done(self):
        t = self.prompt("改信義王")
        turn.backup(self.V, "案子/信義王/信義王.md", t["turn"])
        r = run(HOOK, stop_payload(self.V, "改好了。\n\n## 結論\n信義王改成已完工。"))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["done"])
        self.assertEqual(row["result"], "信義王改成已完工。")
        self.assertFalse(row["result_truncated"])
        self.assertFalse(row["interrupted"])
        self.assertEqual((row["note"], row["note_by"]), ("改了 1 張綜合卡", "program"))
        self.assertEqual(row["changed"], [{"path": "案子/信義王/信義王.md", "action": "modify"}])
        sess = json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertTrue(sess["done"])
        md = self.M.read_text(encoding="utf-8")
        self.assertIn("- 結果：信義王改成已完工。\n- 做了：改了 1 張綜合卡\n- 改了：\n  - 修改 案子/信義王/信義王.md", md)
        self.assertNotIn("還沒結束", md)

    def test_long_result_is_truncated(self):
        self.prompt()
        run(HOOK, stop_payload(self.V, "字" * 400))
        row = self.rows()[0]
        self.assertEqual(row["result"], "字" * 300 + "…全文見對話")
        self.assertTrue(row["result_truncated"])

    def test_result_is_masked(self):
        self.prompt()
        run(HOOK, stop_payload(self.V, "已用密碼 abc123 登入"))
        self.assertEqual(self.rows()[0]["result"], "已用密碼███")

    def test_ai_note_wins_over_floor(self):
        self.prompt()
        turn.set_note(self.V, SID, "把現況改成已完工")
        run(HOOK, stop_payload(self.V, "好了"))
        row = self.rows()[0]
        self.assertEqual((row["note"], row["note_by"]), ("把現況改成已完工", "ai"))

    def test_missing_last_assistant_message_uses_fixed_sentence(self):
        self.prompt()
        run(HOOK, stop_payload(self.V))
        self.assertEqual(self.rows()[0]["result"], "（這版 Claude Code 沒給最後回答）")

    def test_second_stop_does_not_change_result(self):
        self.prompt()
        run(HOOK, stop_payload(self.V, "第一次"))
        r = run(HOOK, stop_payload(self.V, "第二次"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.rows()[0]["result"], "第一次")

    def test_no_turn_file_writes_nothing(self):
        self.prompt()
        before = self.J.read_bytes()
        r = run(HOOK, stop_payload(self.V, "好了", sid="nobody"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.J.read_bytes(), before)
        self.assertFalse((self.V / ".system" / "hook-errors.log").exists())

    def test_outside_vault_writes_nothing(self):
        r = run(HOOK, stop_payload(self.tmp, "好了"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.tmp / ".system").exists())

    def test_bad_json_is_silent_and_logged_as_stop(self):
        r = run(HOOK, "not json", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "stop")

    def test_bad_json_outside_vault_is_silent(self):
        r = run(HOOK, "[1, 2]")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_registered_in_hooks_json_as_stop(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["Stop"]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("matcher", groups[0])
        hooks = groups[0]["hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["command"], 'python "${CLAUDE_PLUGIN_ROOT}/hooks/stop.py"')
        self.assertEqual(hooks[0]["timeout"], 10)
        self.assertIn("每句話記進工作日誌", cfg["description"])


if __name__ == "__main__":
    unittest.main()
