"""card-maker 第一輪修（22 派工，測試報告 design/reports/card-maker-測試.md 的 S1～S6 與讀碼疑點）：每項一組重現。"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests import _cards
from tests._guard_fixture import HOOK, ROOT, make_vault, payload
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import batches, inventory, journal, pending, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402

SID = "sid-r1"
AGENT = "a0204e72"
KB = f'python "{ROOT}/scripts/kb.py"'


def hook(name, data, *args):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / name), *args], input=json.dumps(data, ensure_ascii=False),
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def guard_out(data):
    out = hook("guard.py", data)
    return json.loads(out)["hookSpecificOutput"] if out.strip() else {}


def rows(V):
    return [r for m in journal.months(V) for r in journal.read_lines(V, m)]


# ---------- S1 ----------

class S1JobResultWithSecretShownHere(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prompt(self, text):
        hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": text, "hook_event_name": "UserPromptSubmit"})
        return turn._read_turn(turn._session_file(self.V, SID))["turn"]

    def stop(self, message):
        hook("stop.py", {"session_id": SID, "cwd": str(self.V), "hook_event_name": "Stop", "last_assistant_message": message})

    def test_value_alone_in_secret_turn_does_not_reach_job_line(self):
        """報告的重現：job 關了；客戶下一句回合記 secret_shown；回答只有值、沒有「密碼」二字。"""
        job = self.prompt("好，現在做")
        turn.turn_dir(self.V, job)
        data = batches.empty()
        data["jobs"].append({"job": "j-1", "session_id": SID, "turn": job, "created": "2026-10-22T20:11:50", "status": "open"})
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        self.stop("交給製卡助手在背景整理")
        self.prompt("那組給我看")
        turn.mark(self.V, "secret_shown")
        data["jobs"][0]["status"] = "closed"
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        self.stop("好的，Hunter2xyz 這組我看到了")
        text = json.dumps(rows(self.V), ensure_ascii=False)
        self.assertNotIn("Hunter2xyz", text)
        for p in (self.V / "系統" / "工作日誌").glob("*.md"):
            self.assertNotIn("Hunter2xyz", p.read_text(encoding="utf-8"))
        job_row = next(r for r in rows(self.V) if r["turn"] == job)
        self.assertEqual(job_row["result"], "交給製卡助手在背景整理")
        self.assertTrue(turn.turn_data(self.V, job)["job_result"])


# ---------- S2(c)、S2(d) ----------

class S2TodoMatchesConvert(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_type_folder_root_file_not_listed_and_every_new_passes_convert_gate(self):
        """S2(c)：知識庫/案子/說明.txt 落在型別夾根（convert 拒「這不是要製卡的檔」），todo 一開始就不列；
        todo 列的每一份，convert 都不會用這個理由拒絕（兩邊同一個判斷）。"""
        data = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        uris = [i["uri"] for i in data["new"]]
        self.assertNotIn("file://知識庫/案子/說明.txt", uris)
        self.assertTrue(any(e.get("status") == "active" and e["locations"][0]["uri"] == "file://知識庫/案子/說明.txt"
                            for e in inventory.load(self.V)["entries"]), "清冊照記，只是不製卡")
        for item in data["new"]:
            out = json.loads(run_kb("convert", item["id"], "--json", cwd=self.V).stdout)
            self.assertNotEqual(out.get("unreadable"), "這不是要製卡的檔", item["uri"])

    def test_skill_says_failed_is_not_resent(self):
        body = (ROOT / "skills" / "ingest" / "SKILL.md").read_text(encoding="utf-8")
        steps = body[body.index("## 骨架"):body.index("## 共通")]
        self.assertIn("程式不會再派它們，你也不要再派", steps)


# ---------- S3、S4 ----------

class GuardScope(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.job = turn.start(self.V, SID, "好，現在做")["turn"]
        batches.agent_start(self.V, SID, AGENT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, command, tool="Bash", agent=AGENT, session=SID):
        data = payload(tool, command, self.V)
        if session:
            data["session_id"] = session
        if agent:
            data.update(agent_id=agent, agent_type=batches.AGENT)
        return guard_out(data)

    def test_agent_whitelist(self):
        """S3：card-maker 只准白名單；其餘 deny「製卡助手不能做這件事」。"""
        for allowed in ("cards batch b-1 --json", "convert f-1 --json", "agenda add --card f-1 --kind 到期 --date 2026-11-30 --text x",
                        "agenda scanned f-1", "agenda edit a-1 --date 2026-12-01", "agenda recheck a-1", "agenda cancel a-1",
                        "agenda list --json", "entity add --type 案子 --name 甲", "entity suggest-close 甲 --evidence f-1",
                        "entity clean 案子/甲/x.md", "cards skip f-1 --kind 請款", "cards list --json", "--json version"):
            with self.subTest(allowed=allowed):
                out = self.call(f"{KB} {allowed}")
                self.assertEqual(out["permissionDecision"], "allow", out)
                self.assertIn(f"--turn {self.job} --agent {AGENT}", out["updatedInput"]["command"])
        for denied in ("turn approve", "cards stop", "cards dispatch --json", "versions revert",
                       "folders exclude 報價/私人", "source-edit trash f-1", "entity remove 甲", "entity rename 甲 --to 乙",
                       "turn note 做完了", "pending dismiss p-1", "unlock get 甲", "convert f-1 --password abc",
                       "convert f-1 --saved 保險單", "convert f-1 --saved=保險單", "ruling add --kind 可以讀 --file f-1"):
            with self.subTest(denied=denied):
                out = self.call(f"{KB} {denied}")
                self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", "製卡助手不能做這件事"))
                self.assertNotIn("updatedInput", out)

    def test_main_conversation_not_restricted(self):
        for cmd in ("turn approve", "cards stop", "versions revert"):
            out = self.call(f"{KB} {cmd}", agent=None)
            self.assertEqual(out["permissionDecision"], "allow")

    def test_agent_secret_reads_denied(self):
        for tool, target in (("Read", self.V / ".system" / "unlock.json"), ("Read", self.V / ".system" / "temp" / "f-1.secret.md"),
                             ("Glob", self.V / ".system"), ("Grep", self.V / ".system" / "temp")):
            with self.subTest(tool=tool, target=target):
                out = self.call(str(target), tool=tool)
                self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", "製卡助手不能做這件事"))
        out = self.call(str(self.V / ".system" / "temp" / "f-1.md"), tool="Read")
        self.assertEqual(out["permissionDecision"], "allow", "convert 的一般暫存照讀")

    def test_agent_own_turn_or_agent_flag_denied(self):
        """S4：原指令自己帶 --turn／--agent／--session 的 deny，不改寫（argparse 取最後一個會蓋掉插的）。"""
        for cmd in (f"{KB} --turn 20990101-000000-xxxx entity add --type 案子 --name 甲",
                    f"{KB} --agent a-x cards batch b-1", f"{KB} cards batch b-1 --turn=x", f"{KB} --session s cards batch b-1"):
            for tool in ("Bash", "PowerShell"):
                with self.subTest(cmd=cmd, tool=tool):
                    out = self.call(cmd, tool=tool)
                    self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]),
                                     ("deny", "kb 指令不能自己帶 --turn、--agent 或 --session"))

    def test_main_global_agent_or_session_denied_turn_subflag_ok(self):
        for cmd in (f"{KB} --agent a-x entity list", f"{KB} --session other cards status"):
            out = self.call(cmd, agent=None)
            self.assertEqual(out["permissionDecision"], "deny", cmd)
        out = self.call(f'{KB} turn note --turn {self.job} "做完了"', agent=None)
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertNotIn("updatedInput", out)

    def test_main_dispatch_status_stop_get_session(self):
        for sub in ("dispatch --json", "status", "stop --json"):
            for tool in ("Bash", "PowerShell"):
                with self.subTest(sub=sub, tool=tool):
                    out = self.call(f"{KB} cards {sub}", tool=tool, agent=None)
                    self.assertEqual(out["updatedInput"]["command"], f"{KB} --session {SID} cards {sub}")
        out = self.call(f"{KB} cards todo --json", agent=None)
        self.assertNotIn("updatedInput", out)

    def test_powershell_with_turn(self):
        out = self.call(f"{KB} cards batch b-1 --json", tool="PowerShell")
        self.assertEqual(out["updatedInput"]["command"], f"{KB} --turn {self.job} --agent {AGENT} cards batch b-1 --json")


class TurnFlagBelongs(unittest.TestCase):
    """S4：kb.py --turn 只收這個對話的回合（目前回合或 job 回合），或 --agent 記的回合。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.other = turn.start(self.V, "sid-other", "別的視窗")["turn"]
        self.mine = turn.start(self.V, SID, "好")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_foreign_turn_refused(self):
        r = run_kb("--turn", self.other, "entity", "list", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, f"這個回合不是這個對話的：{self.other}"))
        self.assertEqual(run_kb("--turn", self.mine, "entity", "list", cwd=self.V).returncode, 0)
        self.assertEqual(run_kb("--session", "sid-other", "--turn", self.other, "entity", "list", cwd=self.V).returncode, 0)

    def test_agent_turn_accepted(self):
        batches.agent_start(self.V, "sid-other", "a-o")
        turn.start(self.V, SID, "下一句")
        r = run_kb("--turn", self.other, "--agent", "a-o", "entity", "list", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout)


# ---------- S5 ----------

class S5UnreadableReason(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.t = turn.start(self.V, SID, "好")["turn"]
        self.link = inventory.register_link(self.V, "https://claude.ai/artifact/abc", self.t, "報價說明")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refusals_exact(self):
        for reason, why in (("密碼是 Hunter2xyz", "原因裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員"),
                            ("打不開 $(x)", "原因裡不能有 \" $ ` \\ 這四種字元"),
                            ("打不開 `y`", "原因裡不能有 \" $ ` \\ 這四種字元"),
                            ("長" * 201, "原因要一句話，最多 200 字"),
                            ("第一行\n第二行", "原因要一句話，最多 200 字")):
            with self.subTest(reason=reason[:20]):
                r = run_kb("cards", "unreadable", self.link["id"], "--reason", reason, cwd=self.V)
                self.assertEqual((r.returncode, r.stdout.strip()), (3, why))
        self.assertEqual([i for i in pending.load(self.V)["items"] if i.get("kind") == "unreadable"], [])
        for p in (self.V / ".system" / "pending.json", self.V / "系統" / "待處理.md"):
            if p.exists():
                self.assertNotIn("Hunter2xyz", p.read_text(encoding="utf-8"))


# ---------- 讀碼疑點 ----------

class CollectInsideLock(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        turn.start(self.V, SID, "好")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_collects_while_holding_the_lock(self):
        seen = []
        orig = batches._collect

        def spy(vault):
            seen.append(Path(str(vault / batches.BATCHES_POSIX) + ".lock").exists())
            return orig(vault)
        batches._collect = spy
        try:
            batches.dispatch(self.V, SID, turn.current(self.V, SID)["turn"])
        finally:
            batches._collect = orig
        self.assertEqual(seen, [True])


class SaveTurnLocked(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.t = turn.start(self.V, SID, "好")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_turn_waits_for_the_mark_lock(self):
        """回合檔讀改寫跟 mark 同一把鎖：鎖被拿著時寫不進去（等不到丟 TimeoutError），不會蓋掉別人剛寫的旗標。"""
        old = kio.LOCK_TIMEOUT
        kio.LOCK_TIMEOUT = 0.3
        try:
            with kio.FileLock(turn._session_file(self.V, SID)):
                with self.assertRaises(TimeoutError):
                    turn.save_turn(self.V, dict(turn.current(self.V, SID), approved=True))
        finally:
            kio.LOCK_TIMEOUT = old
        turn.mark(self.V, "ingest")
        turn.save_turn(self.V, {"turn": self.t, "approved": True})
        data = turn.current(self.V, SID)
        self.assertTrue(data["ingest"] and data["approved"], "併進去，不是整份蓋掉")


class TwoWindows(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_goes_to_the_session_that_asked(self):
        a = turn.start(self.V, "sid-a", "好，現在做")["turn"]
        turn.start(self.V, "sid-b", "另一個視窗")  # current.json 現在是 b
        got = json.loads(run_kb("--session", "sid-a", "cards", "dispatch", "--json", cwd=self.V).stdout)
        self.assertEqual(got["turn"], a)
        self.assertEqual(batches.load(self.V)["jobs"][0]["session_id"], "sid-a")
        st = json.loads(run_kb("--session", "sid-b", "cards", "status", "--json", cwd=self.V).stdout)
        self.assertIsNone(st["job"])

    def test_unknown_source_cli(self):
        turn.start(self.V, SID, "好")
        r = run_kb("--session", SID, "cards", "dispatch", "--source", "報價單", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "沒有登記這個來源：報價單"))


class InConversationAfterStop(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        turn.start(self.V, SID, "好，現在做")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def programs(self):
        return [r for r in rows(self.V) if r.get("kind") == "program"]

    def test_kb_after_done_during_background_job_is_not_terminal(self):
        run_kb("--session", SID, "cards", "dispatch", "--json", cwd=self.V)
        turn.set_done(self.V, SID)
        before = len(self.programs())
        self.assertEqual(run_kb("entity", "add", "--type", "案子", "--name", "新店吳宅", cwd=self.V).returncode, 0)
        self.assertEqual(len(self.programs()), before)

    def test_without_job_it_is_terminal(self):
        turn.set_done(self.V, SID)
        before = len(self.programs())
        run_kb("entity", "add", "--type", "案子", "--name", "新店吳宅", cwd=self.V)
        self.assertEqual(len(self.programs()), before + 1)


class TypingIsNotStop(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_typing_during_background_job_not_marked_stopped(self):
        """D-200：背景整理中客戶打字（上一句沒等到 Stop、記了 ingest）不算按停止。"""
        hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "好，現在做", "hook_event_name": "UserPromptSubmit"})
        turn.mark(self.V, "ingest")
        t = turn._read_turn(turn._session_file(self.V, SID))["turn"]
        data = batches.empty()
        data["jobs"].append({"job": "j-1", "session_id": SID, "turn": t, "created": "2026-10-22T20:11:50", "status": "open"})
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        out = hook("turn.py", {"session_id": SID, "cwd": str(self.V), "prompt": "信義王的報價在哪？",
                               "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(out, "")
        self.assertFalse(turn._read_turn(turn._session_file(self.V, SID)).get("ingest_stopped"))


class SameExclusionsEverywhere(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.t = turn.start(self.V, SID, "好")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_opening_count_and_waiting_skip_link_and_credential(self):
        from kb._lib import cards, config, plan, rulings
        from kb._lib import sources as sources_lib
        before, _m = batches.opening_counts(self.V, sources_lib.load(self.V))
        inventory.register_link(self.V, "https://claude.ai/artifact/abc", self.t, "報價說明")
        cred = self.raw / "客戶案件" / "信義王" / "網站密碼.txt"
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text("帳號 abc\n", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.t = turn.start(self.V, SID, "好")["turn"]
        cid = _cards.entry_of(self.V, "客戶案件/信義王/網站密碼.txt")["id"]
        rulings.add(self.V, "可以讀", [], [], "網站密碼.txt 可以讀", self.t, files=[cid])
        after, _m = batches.opening_counts(self.V, sources_lib.load(self.V))
        self.assertEqual(after, before)
        out = cards.todo(self.V, sources_lib.load(self.V))
        self.assertEqual(len(out["new"]), before + 2, "todo 照列（主對話做）")
        self.assertEqual(plan._waiting(out, self.V), before)
        steps = {st["step"]: st for st in plan.build(out, self.V, config)["plan"]}
        self.assertEqual(len(steps["cards"]["files"]), 2, "兩份留給主對話")


if __name__ == "__main__":
    unittest.main()
