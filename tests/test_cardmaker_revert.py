"""card-maker 退回（D-195，驗收 13）：一個 job 寫了 20 張卡、中途客戶開了新回合（只讀）→ versions revert 一次退掉 20 張，
卡、總目錄、實體清單都回來。寫卡照真的走：PreToolUse（guard.py）→ 寫檔 → PostToolUse（output_log.py），都帶 agent_id。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests import _cards
from tests._guard_fixture import ROOT
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import batches, cards, turn  # noqa: E402

SID = "sid-revert"
AGENT = "a0204e72"


def hook(name, data):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / name), *(["start"] if name == "subagent.py" else [])],
                       input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8",
                       cwd=str(ROOT), env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


class Revert(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        have = len(json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"])
        for i in range(20 - have):
            p = self.raw / "報價與合約" / "批次" / f"估價單_{i:02d}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"估價單第 {i} 號。\n", encoding="utf-8")
        _cards.scan_apply(self.V)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prompt(self, text):
        hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": text, "hook_event_name": "UserPromptSubmit"})
        return turn._read_turn(turn._session_file(self.V, SID))["turn"]

    def tool(self, event, tool_name, path):
        return hook("guard.py" if event == "PreToolUse" else "output_log.py", {
            "session_id": SID, "cwd": str(self.V), "hook_event_name": event, "tool_name": tool_name,
            "tool_input": {"file_path": str(path)}, "agent_id": AGENT, "agent_type": batches.AGENT})

    def test_one_revert_takes_back_the_whole_job(self):
        index_before = (self.V / cards.INDEX_POSIX).read_text(encoding="utf-8") if (self.V / cards.INDEX_POSIX).exists() else None
        ent = self.V / ".system" / "entities.json"
        entities_before = ent.read_bytes() if ent.exists() else None
        job = self.prompt("好，現在做")
        got = json.loads(run_kb("--session", SID, "cards", "dispatch", "--json", cwd=self.V).stdout)
        self.assertEqual(got["turn"], job)
        hook("subagent.py", {"session_id": SID, "cwd": str(self.V), "hook_event_name": "SubagentStart",
                             "agent_id": AGENT, "agent_type": batches.AGENT})
        items = json.loads(run_kb("--turn", job, "--agent", AGENT, "cards", "batch", got["batches"][0]["batch"], "--json",
                                  cwd=self.V).stdout)["items"]
        self.assertEqual(len(items), 20)
        r = run_kb("--turn", job, "--agent", AGENT, "entity", "add", "--type", "案子", "--name", "新店吳宅", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout)
        later = self.prompt("信義王的報價在哪？")  # 客戶中途說話（只讀）
        self.assertNotEqual(later, job)
        for i, item in enumerate(items):
            path = _cards.card_path(self.V, item, f"卡{i:02d}")
            self.assertIn('"allow"', self.tool("PreToolUse", "Write", path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_cards.card_text(item, f"卡{i:02d}"), encoding="utf-8", newline="\n")
            self.assertEqual(self.tool("PostToolUse", "Write", path), "", "卡檔頭要過")
        self.assertEqual(len(cards._scan_groups(self.V)[0]), 20)
        self.assertFalse((self.V / ".system" / "versions" / later).exists(), "客戶那句沒有任何備份")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(job, r.stdout)
        self.assertEqual(len(cards._scan_groups(self.V)[0]), 0)
        self.assertEqual(ent.read_bytes() if ent.exists() else None, entities_before)
        now = (self.V / cards.INDEX_POSIX).read_text(encoding="utf-8") if (self.V / cards.INDEX_POSIX).exists() else None
        self.assertEqual(now, index_before)
        self.assertEqual(len(json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"]), 20)


if __name__ == "__main__":
    unittest.main()
