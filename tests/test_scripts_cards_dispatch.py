"""kb cards dispatch／batch／status／stop／unreadable 與全域 --turn 黑箱（card-maker，驗收 1、2、2b、6、9、12、13b、13c）：
真的跑 CLI，成功與拒絕句一字不差。知識庫是 tests/fixtures/inventory 複本，補到 45 份待製卡。"""
import json
import shutil
import unittest

from tests import _cards
from tests.test_scripts_inventory_cli import run_kb
from kb import convert  # noqa: E402
from kb._lib import batches, inventory, journal, pending, rulings, turn  # noqa: E402
from kb._lib.paths import plugin_root  # noqa: E402

SID = "sid-cardmaker"
PREFIX = f'python "{plugin_root().as_posix()}/scripts/kb.py"'


class Base(unittest.TestCase):
    TOTAL = 45

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        have = len(self.todo()["new"])
        for i in range(self.TOTAL - have):
            p = self.raw / "報價與合約" / "批次" / f"估價單_{i:02d}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"估價單第 {i} 號，金額 {1000 + i} 元。\n", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.t = turn.start(self.V, SID, "好，現在做")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        """對話裡的呼叫：守門會替主對話的 cards dispatch／status／stop 插 --session，這裡照插。"""
        if args[:1] == ("cards",) and args[1:2] in (("dispatch",), ("status",), ("stop",)):
            args = ("--session", SID) + args
        return run_kb(*args, cwd=self.V)

    def ok(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def js(self, *args):
        return json.loads(self.ok(*args, "--json"))

    def todo(self):
        r = run_kb("cards", "todo", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)


class Dispatch(Base):
    def test_45_files_three_batches_then_running(self):
        """驗收 2。"""
        got = self.js("cards", "dispatch")
        self.assertEqual([b["items"] for b in got["batches"]], [20, 20, 5])
        self.assertEqual(got["turn"], self.t)
        for b in got["batches"]:
            self.assertEqual(b["prompt"], f"批次 {b['batch']}：做卡 {b['items']} 份。第一步跑 {PREFIX} cards batch {b['batch']} "
                                          "--json，照它做，做完照你的說明回報。")
        again = self.js("cards", "dispatch")
        self.assertEqual((again["batches"], again["running"]), ([], 3))

    def test_human_lines(self):
        self.assertEqual(self.ok("cards", "dispatch"), "派了 3 批（做卡 20、20、5）；還在跑 0 批；還剩 0")
        self.assertEqual(self.ok("cards", "dispatch"), "沒有新派的；還在跑 3 批；還剩 0")
        self.assertEqual(self.ok("cards", "status"), "正在整理：還在跑 3 批（做卡 20、做卡 20、做卡 5）；這次做好 0 份；還剩 0")

    def test_terminal_refused(self):
        """終端機跑的沒有 --session（守門只替對話裡插）：拒絕；current.json 有 session 也一樣（第一輪修：兩個視窗不串）。"""
        for _ in range(2):
            r = run_kb("cards", "dispatch", "--json", cwd=self.V)
            self.assertEqual((r.returncode, r.stdout.strip()), (3, "整理要在對話裡派"))
            turn.start(self.V, None, "")

    def test_plan_is_one_dispatch_step_and_batch_question(self):
        """驗收 1：45 份 asks 第一題是先問題、then 是 [dispatch]；10 份時 announce 照 config 算。"""
        data = self.todo()
        ask = data["asks"][0]
        self.assertEqual(ask["question"], "有 45 份要整理，大約 5 分鐘；份數多，會用掉比平常多的額度。現在做嗎？")  # 45×6+30
        self.assertEqual([s["step"] for s in ask["results"][0]["then"]], ["dispatch"])
        (self.V / ".system" / "config.json").write_text(json.dumps({"ingest_auto_max": 100}), encoding="utf-8")
        data = self.todo()
        self.assertEqual(data["plan"], [{"step": "dispatch", "items": 45, "do": "kb cards dispatch --json", "read": None}])
        self.assertEqual(data["announce"], "有 45 份要整理，大約 5 分鐘；要停的話跟我說一聲")


class Batch(Base):
    def test_fields_read_and_agent(self):
        """驗收 6。"""
        b = self.js("cards", "dispatch")["batches"][2]["batch"]
        got = json.loads(self.ok("--turn", self.t, "--agent", "a0204e72", "cards", "batch", b, "--json"))
        self.assertEqual((got["batch"], got["kind"], got["prefix"]), (b, "cards", PREFIX))
        self.assertEqual(got["read"], [f"{plugin_root().as_posix()}/skills/ingest/references/carding.md 批次、材料的欄位、一～七、九節"])
        todo_new = {i["id"]: i for i in self.todo()["new"]}
        self.assertEqual(len(got["items"]), 5)
        for item in got["items"]:
            self.assertEqual(item["bucket"], "new")
            self.assertEqual({k: v for k, v in item.items() if k != "bucket"}, todo_new[item["id"]])
        self.assertEqual(set(got), {"batch", "kind", "prefix", "read", "items", "profile", "entities"})
        mine = next(x for x in batches.load(self.V)["batches"] if x["batch"] == b)
        self.assertEqual(mine["agent_id"], "a0204e72")

    def test_released_or_unknown_refused(self):
        b = self.js("cards", "dispatch")["batches"][0]["batch"]
        self.ok("cards", "stop")
        for target in (b, "b-00000000"):
            r = self.kb("cards", "batch", target, "--json")
            self.assertEqual((r.returncode, r.stdout.strip()), (3, "這批已經停了或做完了"))


class Stop(Base):
    def test_stop_json_and_marks_ingest_stopped(self):
        """驗收 12。"""
        b = self.js("cards", "dispatch")["batches"][0]["batch"]
        self.ok("--turn", self.t, "--agent", "a-1", "cards", "batch", b)
        got = self.js("cards", "stop")
        self.assertEqual(got, {"agents": ["a-1"], "done": 0, "left": 45})
        self.assertEqual({x["status"] for x in batches.load(self.V)["batches"]}, {"released"})
        self.assertTrue(turn._read_turn(turn._session_file(self.V, SID)).get("ingest_stopped"))
        self.assertEqual(self.ok("cards", "stop"), "現在沒有在整理")
        self.assertEqual(self.ok("cards", "status"), "現在沒有在整理；還有 45 份沒整理")

    def test_no_dispatch_after_stop_in_the_same_turn(self):
        """審閱修：停掉的子代理也送通知；同一句收通知再 dispatch 不重派，客戶下一句說整理才派。"""
        self.ok("cards", "dispatch")
        self.ok("cards", "stop")
        got = self.js("cards", "dispatch")
        self.assertEqual((got["stopped"], got["batches"]), (True, []))
        self.assertEqual(self.ok("cards", "dispatch"), "這一句說了停，不再派；客戶說整理再派")
        turn.start(self.V, SID, "繼續整理")
        self.assertEqual(len(self.js("cards", "dispatch")["batches"]), 3)

    def test_stop_human(self):
        self.ok("cards", "dispatch")
        self.assertEqual(self.ok("cards", "stop"), "已停：這次做好 0 份，還剩 45 份；這個對話不再自動整理，說「整理」再接著做")


class Boundary(Base):
    TOTAL = 3

    def test_credential_and_locked_files_stay_out(self):
        """驗收 2b：檔名像密碼的（裁示可以讀的）與待處理記過有密碼的不進批次，留在 plan 的 cards 那步。"""
        cred = self.raw / "客戶案件" / "信義王" / "網站密碼.txt"
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text("帳號 abc\n", encoding="utf-8")
        _cards.scan_apply(self.V)
        cid = _cards.entry_of(self.V, "客戶案件/信義王/網站密碼.txt")["id"]
        self.t = turn.start(self.V, SID, "好，現在做")["turn"]  # scan 開了終端機的回合，換回對話裡
        rulings.add(self.V, "可以讀", [], [], "網站密碼.txt 可以讀", self.t, files=[cid])
        lid = self.todo()["new"][0]["id"]
        pending.add(self.V, "unreadable", [lid], "x 讀不到", "有密碼", self.t)
        pending.auto_done(self.V, lid, "unreadable", convert.OPENED_HOW, self.t)
        data = self.todo()
        self.assertIn(cid, [i["id"] for i in data["new"]])
        (cards_step,) = [s for s in data["plan"] if s["step"] == "cards"]
        self.assertEqual(sorted(cards_step["files"]), sorted([cid, lid]))
        sent = [k for b in self.js("cards", "dispatch")["batches"] for k in
                next(x for x in batches.load(self.V)["batches"] if x["batch"] == b["batch"])["items"]]
        self.assertEqual(len(sent), len(data["new"]) - 2)
        self.assertNotIn(cid, sent)
        self.assertNotIn(lid, sent)


class Link(Base):
    TOTAL = 3

    def setUp(self):
        super().setUp()
        self.link = inventory.register_link(self.V, "https://claude.ai/artifact/abc", self.t, "報價說明")

    def test_link_not_dispatched_and_unreadable(self):
        """驗收 13c。"""
        data = self.todo()
        (cards_step,) = [s for s in data["plan"] if s["step"] == "cards"]
        self.assertEqual((cards_step["files"], cards_step["read"]), ([self.link["id"]], "references/carding.md 一～六節"))
        sent = [k for x in (self.js("cards", "dispatch"), ) for b in x["batches"]
                for k in next(y for y in batches.load(self.V)["batches"] if y["batch"] == b["batch"])["items"]]
        self.assertNotIn(self.link["id"], sent)
        out = self.ok("cards", "unreadable", self.link["id"], "--reason", "線上文件打不開")
        self.assertRegex(out, r"^已記進待處理（p-[0-9a-f]+）$")
        item = next(i for i in pending.load(self.V)["items"] if i.get("file_ids") == [self.link["id"]])
        self.assertEqual((item["kind"], item["status"], item["reason"]), ("unreadable", "open", "線上文件打不開"))
        data = self.todo()
        self.assertNotIn(self.link["id"], [i["id"] for i in data["new"]])
        self.assertIn("報價說明 讀不到：線上文件打不開；到 claude.ai 確認那份還在、有分享給這個帳號，或跟我說先跳過；"
                      "記在 系統/待處理.md", data["tell"])
        n, _moved = batches.opening_counts(self.V, __import__("kb._lib.sources", fromlist=["load"]).load(self.V))
        self.assertEqual(n, len(data["new"]) + len(data["stale"]))

    def test_plain_file_refused(self):
        fid = self.todo()["new"][0]["id"]
        r = self.kb("cards", "unreadable", fid, "--reason", "線上文件打不開")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "這個指令只收線上文件；檔案讀不到由 convert 記"))
        r = self.kb("cards", "unreadable", "f-ffffffff", "--reason", "x")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "清冊沒有 f-ffffffff"))


class GlobalTurn(Base):
    TOTAL = 1

    def test_turn_flag_backs_up_into_that_turn_without_program_segment(self):
        """驗收 9：--turn 的呼叫備份進那個回合、算對話裡（不寫程式段）；客戶中途開了新回合也一樣。"""
        self.ok("cards", "dispatch")  # 派工那一刻 job 回合就有 versions 夾（batches.json 備進去）
        turn.start(self.V, SID, "信義王的報價在哪？")
        turn.set_done(self.V, SID)
        before = [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]
        self.ok("--turn", self.t, "--agent", "a-1", "entity", "add", "--type", "案子", "--name", "新店吳宅")
        self.assertIn(".system/entities.json", [e["path"] for e in turn.entries(self.V, self.t)])
        after = [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]
        self.assertEqual(after, before)

    def test_unknown_turn_refused(self):
        r = self.kb("--turn", "20990101-000000-xxxx", "entity", "list")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "回合不存在：20990101-000000-xxxx"))

    def test_help_does_not_list_it(self):
        self.assertNotIn("--turn", self.ok("--help"))

    def test_turn_note_and_approve_take_turn(self):
        self.ok("cards", "dispatch")
        turn.start(self.V, SID, "下一句")
        self.assertEqual(self.ok("turn", "approve", "--turn", self.t), "這一題解除綜合卡張數上限")
        self.assertTrue(turn.turn_data(self.V, self.t)["approved"])
        self.assertFalse(turn.current(self.V, SID).get("approved"))
        r = self.kb("turn", "note", "--turn", "20990101-000000-xxxx", "x")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "回合不存在：20990101-000000-xxxx"))


if __name__ == "__main__":
    unittest.main()
