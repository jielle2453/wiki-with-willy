"""hooks/output_log.py 黑箱：PostToolUse 寫入 Z3 後登記成品；Z1 不登記；檔不存在不登記；壞 JSON exit 0。"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests._guard_fixture import ROOT, make_vault
from kb._lib import inventory, turn  # noqa: E402

HOOK = ROOT / "hooks" / "output_log.py"
GUARD = ROOT / "hooks" / "guard.py"
SID = "test-sid"


def run_hook(data, cwd=None, hook=HOOK):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(hook)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def write_via_tools(cwd, path, text, tool="Write"):
    """像 Claude Code 一樣：PreToolUse（守門備份）→ 真的寫 → PostToolUse（登記）。回 PostToolUse 的結果。"""
    pre = {"session_id": SID, "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": tool,
           "tool_input": {"file_path": str(path), "content": text}}
    r = run_hook(pre, hook=GUARD)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow", r.stdout
    Path(path).write_text(text, encoding="utf-8")
    return run_hook(post_payload(cwd, tool, path))


def post_payload(cwd, tool, path, key="file_path"):
    return {"session_id": SID, "cwd": str(cwd), "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": {key: str(path), "content": "x"}, "tool_response": {"filePath": str(path), "success": True}}


class OutputLog(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.t = turn.start(self.V, SID, "寫一份草稿")
        self.out = self.V / "AI 產出" / "x.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_to_z3_registers(self):
        r = write_via_tools(self.V, self.out, "草稿\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["origin"], "ai_output")
        self.assertEqual(entries[0]["created_turn"], self.t["turn"])
        self.assertEqual(entries[0]["locations"][0], "file://AI 產出/x.md")

    def test_same_content_again_does_not_add(self):
        write_via_tools(self.V, self.out, "草稿\n")
        run_hook(post_payload(self.V, "Edit", self.out))
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["fingerprints"]), 1)
        write_via_tools(self.V, self.out, "草稿 改\n", tool="Edit")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["fingerprints"]), 2)

    def test_relative_path_and_notebook_key(self):
        nb = self.V / "AI 產出" / "n.ipynb"
        turn.backup(self.V, "AI 產出/n.ipynb", self.t["turn"])
        nb.write_text("{}", encoding="utf-8")
        r = run_hook(post_payload(self.V, "NotebookEdit", "AI 產出/n.ipynb", key="notebook_path"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(inventory.load(self.V)["entries"][0]["locations"], ["file://AI 產出/n.ipynb"])

    def test_preexisting_unregistered_file_edit_is_not_registered(self):
        """第一輪 M4（驗收 23）：客戶自己放進 AI 產出的檔，AI 編輯不登記。"""
        q = self.V / "AI 產出" / "q.md"
        q.write_text("客戶自己放的\n", encoding="utf-8")
        r = write_via_tools(self.V, q, "AI 改過\n", tool="Edit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_reverted_output_modified_in_new_turn_gets_new_entry(self):
        """第一輪 M2（驗收 22）。"""
        write_via_tools(self.V, self.out, "草稿\n")
        old = inventory.load(self.V)["entries"][0]
        inventory.set_status(self.V, [old["id"]], "turn_reverted")
        t2 = turn.start(self.V, SID, "再寫")
        write_via_tools(self.V, self.out, "第二次\n", tool="Edit")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["status"], "turn_reverted")
        self.assertEqual(entries[1]["status"], "active")
        self.assertEqual(entries[1]["created_turn"], t2["turn"])
        self.assertNotEqual(entries[1]["id"], old["id"])

    def test_write_to_z1_not_registered(self):
        card = self.V / "案子" / "信義王" / "信義王.md"
        r = run_hook(post_payload(self.V, "Write", card))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_missing_file_not_registered(self):
        r = run_hook(post_payload(self.V, "Write", self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_non_write_tool_ignored(self):
        self.out.write_text("草稿\n", encoding="utf-8")
        r = run_hook(post_payload(self.V, "Read", self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_outside_vault_ignored(self):
        other = self.tmp / "AI 產出"
        other.mkdir()
        (other / "x.md").write_text("x", encoding="utf-8")
        r = run_hook(post_payload(self.tmp, "Write", other / "x.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / ".system").exists())

    def test_registered_in_hooks_json_as_post_tool_use(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["PostToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Write|Edit|MultiEdit|NotebookEdit")
        hook = groups[0]["hooks"][0]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/output_log.py", hook["command"])
        self.assertEqual(hook["timeout"], 10)

    def test_bad_json_silent_exit_0(self):
        r = run_hook("not json", cwd=self.V)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "output_log")  # 第一輪 L7：第二欄是哪支 hook
        r = run_hook("not json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
