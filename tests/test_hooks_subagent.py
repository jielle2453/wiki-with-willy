"""hooks/subagent.py 黑箱（card-maker，驗收 7、11）：start 的 additionalContext 是 kb 指令行加 core.md 全文、agents 記上 job 回合；
stop 把那批改 done、補工作日誌的「改了」；不是 card-maker 不動作；壞 stdin exit 0 寫 log；hooks.json 兩個事件都帶 matcher。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import ROOT, make_vault
from kb._lib import batches, journal, turn  # noqa: E402
from kb._lib.paths import plugin_root  # noqa: E402

HOOK = ROOT / "hooks" / "subagent.py"
TURN_HOOK = ROOT / "hooks" / "turn.py"
STOP_HOOK = ROOT / "hooks" / "stop.py"
SID = "test-sid"
AGENT = "wiki-with-willy:card-maker"


def run(hook, data, *args):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(hook), *args], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, event, agent_id="a0204e72", agent_type=AGENT, **extra):
        data = {"session_id": SID, "cwd": str(self.V), "hook_event_name": event, "agent_id": agent_id,
                "agent_type": agent_type}
        data.update(extra)
        return data

    def prompt(self, text):
        r = run(TURN_HOOK, {"session_id": SID, "cwd": str(self.V), "prompt": text, "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return turn._read_turn(turn._session_file(self.V, SID))

    def errors(self):
        p = self.V / ".system" / "hook-errors.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""


class Start(Base):
    def test_context_is_kb_line_and_core_and_agent_recorded(self):
        t = self.prompt("好，現在做")
        r = run(HOOK, self.payload("SubagentStart"), "start")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SubagentStart")
        core = (ROOT / "rules" / "core.md").read_text(encoding="utf-8")
        kb_line = f'【wiki-with-willy】kb 指令：python "{plugin_root().as_posix()}/scripts/kb.py" <子命令>'
        self.assertEqual(out["additionalContext"], kb_line + "\n\n" + core)
        (agent,) = batches.load(self.V)["agents"]
        self.assertEqual((agent["agent_id"], agent["session_id"], agent["turn"]), ("a0204e72", SID, t["turn"]))
        self.assertEqual(self.errors(), "")

    def test_turn_is_the_open_job_turn(self):
        t = self.prompt("好，現在做")
        batches.dispatch(self.V, SID, t["turn"])  # 沒東西可派：不開 job
        data = batches.empty()
        data["jobs"].append({"job": "j-1", "session_id": SID, "turn": t["turn"], "created": "2026-10-22T20:11:50",
                             "status": "open"})
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        self.prompt("信義王的報價在哪？")
        run(HOOK, self.payload("SubagentStart"), "start")
        self.assertEqual(batches.load(self.V)["agents"][0]["turn"], t["turn"])

    def test_other_agent_type_does_nothing(self):
        self.prompt("好")
        r = run(HOOK, self.payload("SubagentStart", agent_type="general-purpose"), "start")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.V / batches.BATCHES_POSIX).exists())

    def test_bad_stdin_exit_0_and_logs(self):
        r = run(HOOK, "{not json", "start")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, str(HOOK), "start"], input="[1]", capture_output=True, text=True,
                           encoding="utf-8", cwd=str(self.V))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("\tsubagent\t", self.errors())

    def test_broken_batches_file_still_gives_rules_and_logs(self):
        self.prompt("好")
        (self.V / batches.BATCHES_POSIX).write_text("{", encoding="utf-8")
        r = run(HOOK, self.payload("SubagentStart"), "start")
        self.assertEqual(r.returncode, 0)
        core = (ROOT / "rules" / "core.md").read_text(encoding="utf-8")
        self.assertTrue(json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].endswith(core))
        self.assertIn("batches.json", self.errors())


class Stop(Base):
    def test_stop_marks_done_and_refreshes_changed(self):
        """驗收 11：派工那句 Stop 時改了是無；子代理寫 3 張卡後 subagent stop → 那句的改了有 3 張卡。"""
        t = self.prompt("好，現在做")
        tid = t["turn"]
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(dict(batches.empty(), jobs=[
            {"job": "j-1", "session_id": SID, "turn": tid, "created": "2026-10-22T20:11:50", "status": "open"}],
            batches=[{"batch": "b-1", "job": "j-1", "kind": "cards", "items": ["f-1"], "source": None,
                      "created": "2026-10-22T20:12:30", "agent_id": "a0204e72", "status": "open"}])), encoding="utf-8")
        run(HOOK, self.payload("SubagentStart"), "start")
        r = run(STOP_HOOK, {"session_id": SID, "cwd": str(self.V), "hook_event_name": "Stop",
                            "last_assistant_message": "交給製卡助手，正在整理"})
        self.assertEqual(r.returncode, 0, r.stderr)
        row = journal.read_lines(self.V, journal.month_of(tid))[-1]
        self.assertEqual((row["changed"], row["note"]), ([], journal.NO_CHANGES))
        for i in range(3):
            turn.backup(self.V, f"檔案索引/客戶案件/卡{i}-0000000{i}.md", tid)
        r = run(HOOK, self.payload("SubagentStop", last_assistant_message="批次 b-1：做好 3、沒做成 0"), "stop")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(batches.load(self.V)["batches"][0]["status"], "done")
        self.assertTrue(batches.load(self.V)["agents"][0]["stopped"])
        row = journal.read_lines(self.V, journal.month_of(tid))[-1]
        self.assertEqual(len(row["changed"]), 3)
        self.assertEqual(row["note"], "新增 3 張檔案卡")
        self.assertEqual(row["result"], "交給製卡助手，正在整理")
        self.assertEqual(self.errors(), "")

    def test_unknown_agent_stop_is_silent(self):
        self.prompt("好")
        r = run(HOOK, self.payload("SubagentStop", agent_id="a-none"), "stop")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class Registration(unittest.TestCase):
    def test_hooks_json_has_both_events_with_matcher(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        for event, arg in (("SubagentStart", "start"), ("SubagentStop", "stop")):
            (group,) = cfg[event]
            self.assertEqual(group["matcher"], "^wiki-with-willy:card-maker$")
            (hook,) = group["hooks"]
            self.assertEqual(hook["command"], f'python "${{CLAUDE_PLUGIN_ROOT}}/hooks/subagent.py" {arg}')
            self.assertEqual(hook["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
