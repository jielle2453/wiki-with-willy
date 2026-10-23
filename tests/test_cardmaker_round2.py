"""card-maker 第二輪修（22 派工，回測報告 design/reports/card-maker-測試.md「回測第一輪」）：S4 縮寫、R3 子代理 shell 收緊、
R1 壞綜合卡的材料、R2 每次 dispatch 都給 say。每項一組重現。"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from tests import _cards
from tests._guard_fixture import ROOT, make_vault, payload
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import batches, turn  # noqa: E402

SID = "sid-r2"
AGENT = "a0204e72"
KB = f'python "{ROOT}/scripts/kb.py"'
DENY = "製卡助手不能做這件事"
SELF_FLAGS = "kb 指令不能自己帶 --turn、--agent 或 --session"


def guard_out(data):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard.py")], input=json.dumps(data, ensure_ascii=False),
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}


class Guarded(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.job = turn.start(self.V, SID, "好，現在做")["turn"]
        batches.agent_start(self.V, SID, AGENT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, command, tool="Bash", agent=AGENT):
        data = payload(tool, command, self.V)
        data["session_id"] = SID
        if agent:
            data.update(agent_id=agent, agent_type=batches.AGENT)
        return guard_out(data)


class S4Abbreviations(Guarded):
    def test_agent_abbreviated_flags_denied(self):
        """回測 S4：--tu=、--ses=、--ag= 是 argparse 的縮寫，一樣擋；Bash 與 PowerShell。"""
        for flag in ("--tu=20990101-000000-xxxx", "--ses=sid-b", "--ag=x", "--tur 20990101-000000-xxxx", "--sess=x"):
            for where in ("{f} entity add --type 案子 --name 甲", "entity add --type 案子 --name 甲 {f}"):
                for tool in ("Bash", "PowerShell"):
                    cmd = f"{KB} " + where.format(f=flag)
                    with self.subTest(cmd=cmd, tool=tool):
                        out = self.call(cmd, tool)
                        self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", SELF_FLAGS))

    def test_main_abbreviated_global_flags_denied(self):
        for cmd in (f"{KB} --ses=x cards status", f"{KB} --ag=x entity list", f"{KB} --se x cards status"):
            for tool in ("Bash", "PowerShell"):
                with self.subTest(cmd=cmd, tool=tool):
                    out = self.call(cmd, tool, agent=None)
                    self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", SELF_FLAGS))

    def test_legit_flags_with_same_first_letter_pass(self):
        """--type、--text、--to、--source、--saved（主對話）不是那三個的前綴，照常。"""
        for cmd in ("entity add --type 案子 --name 甲", "agenda add --card f-1 --kind 到期 --date 2026-11-30 --text x"):
            out = self.call(f"{KB} {cmd}")
            self.assertEqual(out["permissionDecision"], "allow", cmd)
        out = self.call(f"{KB} cards dispatch --source 報價 --json", agent=None)
        self.assertEqual(out["permissionDecision"], "allow")


class S4CliNoAbbrev(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.other = turn.start(self.V, "sid-b", "另一個視窗")["turn"]
        turn.start(self.V, SID, "好")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_abbreviations_not_accepted_anywhere(self):
        """回測 S4 的實測：--tu=<別的回合> entity add 寫進了別的回合；主 parser 與子 parser 都不收縮寫。"""
        for args in (("--tu=" + self.other, "entity", "add", "--type", "案子", "--name", "甲"),
                     ("--ses=sid-b", "cards", "status"), ("--ag=x", "version"), ("cards", "todo", "--lim", "3"),
                     ("entity", "add", "--ty", "案子", "--name", "甲")):
            with self.subTest(args=args):
                r = run_kb(*args, cwd=self.V)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn(".system/entities.json", [e["path"] for e in turn.entries(self.V, self.other)])


class R3AgentShellOnlyKb(Guarded):
    def test_anything_but_a_single_whitelisted_kb_call_denied(self):
        """回測 R3：&&、管線、環境變數前綴、cd 再接、python -c、一般指令，子代理一律 deny（客戶按過一律允許 python 也一樣）。"""
        for cmd in (f"{KB} cards batch b-1 --json && echo done", f"{KB} cards todo --json | head -5",
                    f"PYTHONIOENCODING=utf-8 {KB} cards batch b-1 --json", f'cd "{self.V}" && {KB} cards batch b-1',
                    'python -c "print(1)"', "ls", "echo hi", f"{KB} cards batch b-1 --json; {KB} turn approve",
                    f"$env:X=1; {KB} cards batch b-1"):
            for tool in ("Bash", "PowerShell"):
                with self.subTest(cmd=cmd, tool=tool):
                    out = self.call(cmd, tool)
                    self.assertEqual(out.get("permissionDecision"), "deny")
                    if not cmd.startswith("$env:"):  # 這種守門本來就擋（理由是它自己的），其餘是這次補的
                        self.assertEqual(out.get("permissionDecisionReason"), DENY)

    def test_main_conversation_unaffected(self):
        out = self.call("echo hi", agent=None)
        self.assertNotEqual(out.get("permissionDecisionReason"), DENY)


class R1BrokenSynthesisMaterial(unittest.TestCase):
    """回測 R1：知識庫自帶的 案子/信義王/信義王.md 沒有檔頭；登記了信義王之後它是 synthesis_broken，
    批次要帶跟 synthesis_stale 一樣齊的材料，製卡助手才修得了。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        p = self.V / ".system" / "inventory.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for e in data["entries"]:
            e["skip_kind"] = "請款"  # 檔都處理完了：只剩綜合卡
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        turn.start(self.V, SID, "好")
        self.assertEqual(run_kb("entity", "add", "--type", "案子", "--name", "信義王", cwd=self.V).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_item_has_header_target_and_basis(self):
        self.assertEqual(run_kb("entity", "confirm", "信義王", cwd=self.V).returncode, 0)  # 名字確認過才派（R5）
        todo = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        (broken,) = todo["synthesis_broken"]
        self.assertEqual(broken["path"], "案子/信義王/信義王.md")
        self.assertEqual([s["step"] for s in todo["plan"]], ["dispatch"])
        got = json.loads(run_kb("--session", SID, "cards", "dispatch", "--json", cwd=self.V).stdout)
        (b,) = got["batches"]
        self.assertEqual((b["kind"], b["items"]), ("synthesis", 1))
        view = json.loads(run_kb("cards", "batch", b["batch"], "--json", cwd=self.V).stdout)
        (item,) = view["items"]
        self.assertEqual((item["bucket"], item["path"], item["target"], item["name"], item["type"]),
                         ("synthesis_broken", "案子/信義王/信義王.md", "案子/信義王/信義王.md", "信義王", "案子"))
        self.assertTrue(item["header"].startswith("---\n") and "編號: e-" in item["header"], item["header"][:80])
        for key in ("entity_id", "category", "sections", "cards", "statements", "rulings", "findings", "reason"):
            self.assertIn(key, item)

    def test_owner_unknown_goes_to_main(self):
        """找不到是哪個實體的壞綜合卡（編號對不到）：不派，留在主對話的 cards 步。"""
        bad = self.V / "案子" / "大安陳" / "大安陳.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("---\n編號: e-deadbeef\n---\n# 大安陳\n", encoding="utf-8")
        out = {"synthesis_broken": [{"path": "案子/大安陳/大安陳.md", "reason": "檔頭有錯"}]}
        parts = batches.split(self.V, out)
        self.assertEqual(([k for _b, k, _i in parts["synthesis"]], [k for _b, k, _i in parts["main"]]),
                         ([], ["案子/大安陳/大安陳.md"]))


class R2SayEveryDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        turn.start(self.V, SID, "好，現在做")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dispatch(self):
        return json.loads(run_kb("--session", SID, "cards", "dispatch", "--json", cwd=self.V).stdout)

    def test_say_first_progress_and_done(self):
        """回測 R2：收到通知後再派，那次的 say 是繁中進度；做完是收尾那句。"""
        first = self.dispatch()
        self.assertEqual(first["say"], "交給製卡助手在背景整理，好了跟你說；這期間照常問我")
        b = first["batches"][0]["batch"]
        items = batches.load(self.V)["batches"][0]["items"]
        todo = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        for n, item in enumerate(i for i in todo["new"] if i["id"] in items[:3]):  # 製卡助手做好 3 份
            _cards.write_card(self.V, item, f"卡{n}")
        batches.agent_start(self.V, SID, AGENT)
        batches.batch_view(self.V, b, AGENT)
        batches.agent_stop(self.V, SID, AGENT, "批次：做好 3、沒做成 0\n被擋或打回：無")
        nxt = self.dispatch()
        self.assertTrue(nxt["say"].startswith("做好 3 份了"), nxt["say"])
        self.assertRegex(nxt["say"], r"^做好 3 份了(，\d+ 份沒做成)?(；還在做 \d+ 批)?$")
        self.assertIsNone(re.search(r"[A-Za-z]", nxt["say"]))

    def test_done_says_closing_line(self):
        first = self.dispatch()
        data = batches.load(self.V)
        for b in data["batches"]:
            b["status"] = "done"  # 都回報做完（項目還在待做 → 記成沒做成）
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        last = self.dispatch()
        self.assertTrue(last["done"])
        self.assertEqual(last["say"], f"整理完了：{len(last['failed'])} 份沒做成")  # 一份都沒做好不說「做好 0 份」（R4）
        self.assertGreater(len(first["batches"]), 0)

    def test_skeleton_says_only_say_after_notifications(self):
        body = (ROOT / "skills" / "ingest" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("收到通知之後對客戶只說 `say`", body)



class AbbrevSecretActionsRejectedByKb(unittest.TestCase):
    """第二輪修連帶：密碼與守門原本把 --exp、--kin 這類縮寫當全名判成要按允許；現在 kb 自己不收縮寫，指令直接 exit 2，
    什麼都不做（守門照「拆不過的照常放行」那條）。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kb_refuses_abbreviated_secret_flags(self):
        for args in (("convert", "f-00000000", "--saved", "保險單", "--exp"),
                     ("ruling", "add", "--kin", "可以讀", "--file", "f-00000000", "--text", "x")):
            r = run_kb(*args, cwd=self.V)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("error:", r.stderr)


class R5UnconfirmedNameAskedFirst(unittest.TestCase):
    """回測第二輪 R5：信義王這一輪才登記（inferred），它的壞綜合卡製卡助手寫對了還是列在 broken（「還沒跟客戶確認名稱，先問再寫」）。
    名字沒確認的不派、asks 出名字題；客戶確認之後下一次 dispatch 才派。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        p = self.V / ".system" / "inventory.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for e in data["entries"]:
            e["skip_kind"] = "請款"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        turn.start(self.V, SID, "好")
        self.assertEqual(run_kb("entity", "add", "--type", "案子", "--name", "信義王", cwd=self.V).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def todo(self):
        return json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)

    def dispatch(self):
        return json.loads(run_kb("--session", SID, "cards", "dispatch", "--json", cwd=self.V).stdout)

    def test_inferred_not_sent_name_asked_then_sent_after_confirm(self):
        todo = self.todo()
        (broken,) = todo["synthesis_broken"]
        self.assertEqual(broken["path"], "案子/信義王/信義王.md")
        self.assertNotIn("dispatch", [s["step"] for s in todo["plan"]])
        names = [a for a in todo["asks"] if a["kind"] == "name"]
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0]["question"].startswith("我要幫「信義王」（案子，"), names[0]["question"])
        self.assertEqual(names[0]["results"][0]["do"], ["kb entity confirm 信義王"])
        got = self.dispatch()
        self.assertEqual((got["batches"], got["done"]), ([], True))
        self.assertEqual(run_kb("entity", "confirm", "信義王", cwd=self.V).returncode, 0)
        turn.start(self.V, SID, "名字對")
        todo = self.todo()
        self.assertEqual([a for a in todo["asks"] if a["kind"] == "name"], [])
        got = self.dispatch()
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("synthesis", 1)])

    def test_one_question_per_entity(self):
        from kb._lib import entities
        eid = entities.find(entities.load(self.V), "信義王")[0]["id"]
        out = {"synthesis_broken": [{"path": "案子/信義王/信義王.md", "reason": "x"}],
               "synthesis_stale": [{"entity_id": eid, "name": "信義王", "path": "案子/信義王/信義王.md"}],
               "synthesis_new": []}
        self.assertEqual([n["entity_id"] for n in batches.unconfirmed_names(self.V, out)], [eid])
        out["synthesis_new"] = [{"entity_id": eid, "confirmed": False}]
        self.assertEqual(batches.unconfirmed_names(self.V, out), [], "synthesis_new 已經在問就不重複")

    def test_stale_with_unconfirmed_entity_not_sent(self):
        from kb._lib import entities
        eid = entities.find(entities.load(self.V), "信義王")[0]["id"]
        out = {"synthesis_stale": [{"entity_id": eid, "name": "信義王", "path": "案子/信義王/信義王.md"}]}
        parts = batches.split(self.V, out)
        self.assertEqual((parts["synthesis"], parts["main"]), ([], []))
        self.assertEqual([n["name"] for n in batches.unconfirmed_names(self.V, out)], ["信義王"])
        run_kb("entity", "confirm", "信義王", cwd=self.V)
        self.assertEqual([k for _b, k, _i in batches.split(self.V, out)["synthesis"]], [eid])

    def test_ai_judged_counts_as_confirmed(self):
        """build（D-202）：AI 判斷登記的名字算確認：壞的、要更新的綜合卡照派，名字題不問它。"""
        from kb._lib import entities
        eid = entities.find(entities.load(self.V), "信義王")[0]["id"]
        out = {"synthesis_stale": [{"entity_id": eid, "name": "信義王", "path": "案子/信義王/信義王.md"}],
               "synthesis_broken": [{"path": "案子/信義王/信義王.md", "reason": "x"}]}
        self.assertEqual(run_kb("entity", "accept", "信義王", cwd=self.V).stdout.strip(), "已登記：信義王（AI 判斷）")
        self.assertEqual(batches.unconfirmed_names(self.V, out), [])
        self.assertEqual(sorted(b for b, _k, _i in batches.split(self.V, out)["synthesis"]),
                         ["synthesis_broken", "synthesis_stale"])
        self.assertEqual([a for a in self.todo()["asks"] if a["kind"] == "name"], [])


if __name__ == "__main__":
    unittest.main()
