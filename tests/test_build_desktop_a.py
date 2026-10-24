"""build 桌面版確認 A 段修（22 派工，cmb-check\\A-build確認報告.md 落差 2、4、5、7、9、10、12、14 與 adopt 段檔）：每項一組重現。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import _cards
from tests import _entities as E
from tests._guard_fixture import FIXTURES, ROOT
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import batches, build, cards, entities, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

FAKE_ENV = [{"key": k, "level": "ok", "text": k} for k in build.ENV_KEYS]


def files(n):
    return [{"id": f"f-{i:08x}", "uri": f"file://案子/{i}.txt"} for i in range(n)]


class TakenOverSessionStopsDispatching(unittest.TestCase):
    """落差 7、9、10、12 的根因：接手之後，前一個對話（視窗關了、背景還在跑）收到子代理的通知照樣 dispatch，開新 job
    跟接手的對話搶同一批日程與綜合卡。接手之後那個對話一律不派，原對話接手回來才解開。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuilda-")).resolve()
        self.D = self.tmp / "知識庫"
        self.D.mkdir()
        self._env, self._collect = build.env_items, batches._collect
        build.env_items = lambda cwd: [dict(i) for i in FAKE_ENV]
        self.out = {"new": files(5)}
        batches._collect = lambda vault: self.out
        build.start(self.D)
        data = json.loads((self.D / build.BUILD_POSIX).read_text(encoding="utf-8"))
        data["step"] = "cards"
        (self.D / build.BUILD_POSIX).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.ta = turn.start(self.D, "sid-a", "全部整理")["turn"]
        turn.SESSION = "sid-a"
        build.status(self.D)

    def tearDown(self):
        build.env_items, batches._collect = self._env, self._collect
        turn.SESSION = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_session_cannot_dispatch_after_takeover(self):
        self.assertEqual(len(batches.dispatch(self.D, "sid-a", self.ta)["batches"]), 1)
        turn.SESSION = "sid-b"
        build.status(self.D, takeover=True)
        old = batches.dispatch(self.D, "sid-a", self.ta)  # 子代理的通知把舊對話叫醒
        self.assertEqual((old["batches"], old["stopped"], old["taken_over"], old["say"]),
                         ([], True, True, "整理已經交給另一個對話接手，這個對話不再派"))
        self.assertEqual(batches.dispatch_adopt.__name__, "dispatch_adopt")
        jobs_before = len(batches.load(self.D)["jobs"])
        self.assertEqual(len(batches.load(self.D)["jobs"]), jobs_before, "舊對話沒有開新 job")
        tb = turn.start(self.D, "sid-b", "接手建置")["turn"]
        self.assertEqual([b["items"] for b in batches.dispatch(self.D, "sid-b", tb)["batches"]], [5])

    def test_takeover_back_unlocks(self):
        turn.SESSION = "sid-b"
        build.status(self.D, takeover=True)
        turn.SESSION = "sid-a"
        build.status(self.D, takeover=True)
        self.assertFalse(batches.taken_over(batches.load(self.D), "sid-a"))
        self.assertTrue(batches.taken_over(batches.load(self.D), "sid-b"))
        self.assertEqual(len(batches.dispatch(self.D, "sid-a", self.ta)["batches"]), 1)


class OneCountEverywhere(unittest.TestCase):
    """落差 7、12：開場的份數、plan 的門檻與 announce、cards status 出自同一支（batches.waiting）：日程待抽也算進開場。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        item = next(i for i in cards.todo(self.V, sources_lib.load(self.V))["new"] if i["uri"].endswith(".docx"))
        _cards.write_card(self.V, item, "大安陳第一次會議")  # 卡做好、日程還沒抽：todo 的 agenda_unscanned 一份

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_number(self):
        out = cards.todo(self.V, sources_lib.load(self.V))
        self.assertTrue(out["agenda_unscanned"])
        n = batches.waiting(self.V, out)
        self.assertEqual(batches.opening_counts(self.V, sources_lib.load(self.V))[0], n)
        status = json.loads(run_kb("cards", "status", "--json", cwd=self.V).stdout)
        self.assertEqual(status["waiting"], n)
        todo = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        if todo["announce"]:
            self.assertTrue(todo["announce"].startswith(f"有 {n} 份要整理"))


class FactsCountsFiles(unittest.TestCase):
    """落差 2：facts 的份數照檔數（跟 folders add 同一個數），內容一樣的另外記 duplicates。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        src = self.raw / "報價與合約" / "合約範本.txt"
        (self.raw / "報價與合約" / "範本").mkdir(exist_ok=True)
        shutil.copy2(src, self.raw / "報價與合約" / "範本" / "合約範本.txt")  # 同一個來源裡兩份一樣的
        _cards.scan_apply(self.V)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_files_contents_duplicates(self):
        got = build.facts(self.V)
        files_on_disk = {s["alias"]: 0 for s in got["sources"]}
        inv = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))
        for e in inv["entries"]:
            if e.get("status") == "active":
                for loc in e["locations"]:
                    alias = loc["uri"][len("file://"):].split("/", 1)[0]
                    if alias in files_on_disk:
                        files_on_disk[alias] += 1
        for s in got["sources"]:
            self.assertEqual(s["files"], files_on_disk[s["alias"]])
            self.assertEqual(s["files"], s["contents"] + s["duplicates"])
        self.assertTrue(any(s["duplicates"] for s in got["sources"]), "同一個來源裡兩份一樣的合約範本")


class SynthesisExistingCard(unittest.TestCase):
    """落差 10：綜合卡的目標已經有檔（別的批次剛寫過）時，batch 給 existing_card；PostToolUse 比對紀事，少了就打回。"""

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        E.write_card(self.V, E.QUOTE, "王宅報價", names=["王宅"])
        E.write_card(self.V, E.ESTIMATE, "王宅估價", names=["王宅"])
        self.assertEqual(run_kb("entity", "accept", "王宅", cwd=self.V).returncode, 0)
        (self.V / "案子" / "王宅").mkdir(parents=True, exist_ok=True)
        (self.V / "案子" / "王宅" / "王宅.md").write_text("先寫好的一張\n", encoding="utf-8")
        turn.start(self.V, "sid-s", "好")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_has_existing_card(self):
        from datetime import datetime
        eid = entities.find(entities.load(self.V), "王宅")[0]["id"]
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps({"schema_version": 1, "agents": [], "jobs": [
            {"job": "j-1", "session_id": "sid-s", "turn": "t", "status": "open"}], "batches": [
            {"batch": "b-1", "job": "j-1", "kind": "synthesis", "items": [eid], "created": now, "status": "open"}]}),
            encoding="utf-8")
        orig = batches._collect  # 派的時候還是待建、拿材料時已經有檔（另一批剛寫）：用 todo 的結果模擬那一刻
        batches._collect = lambda vault: {"synthesis_new": [{"entity_id": eid, "name": "王宅", "confirmed": True,
                                                            "path": "案子/王宅/王宅.md"}]}
        try:
            view = batches.batch_view(self.V, "b-1")
        finally:
            batches._collect = orig
        (wang,) = view["items"]
        self.assertEqual((wang["bucket"], wang["existing_card"]), ("synthesis_new", "案子/王宅/王宅.md"))
        self.assertTrue(wang["existing_card_abs"].endswith("/案子/王宅/王宅.md"))


class JishiOnlyGrows(unittest.TestCase):
    """落差 10 防線：寫綜合卡把舊卡的紀事少寫了（出處編號不見），PostToolUse 打回；照留再加的放行。"""

    def setUp(self):
        from tests.test_hooks_output_log import post_payload, run_hook
        self.post, self.run_hook = post_payload, run_hook
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.card = self.V / "案子" / "信義王" / "信義王.md"
        text = self.card.read_text(encoding="utf-8")
        self.old = text.replace("- 2026-10-20 登記這個案子", "- 2026-10-20 登記這個案子\n- 2026-10-21 報價第二版〔f-1234abcd〕")
        self.card.write_text(self.old, encoding="utf-8")
        self.t = turn.start(self.V, "test-sid", "整理")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        from tests.test_hooks_output_log import write_via_tools
        return write_via_tools(self.V, self.card, text)

    def test_dropped_event_blocked(self):
        r = self.write(self.old.replace("- 2026-10-21 報價第二版〔f-1234abcd〕\n", ""))
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("綜合卡的紀事少了 1 條（紀事只增）：- 2026-10-21 報價第二版"), out["reason"])

    def test_reworded_with_same_cite_and_added_ok(self):
        new = self.old.replace("- 2026-10-21 報價第二版〔f-1234abcd〕",
                               "- 2026-10-21 開出報價第二版〔f-1234abcd〕\n- 2026-10-22 送出合約〔f-5678abcd〕")
        r = self.write(new)
        self.assertEqual(r.stdout, "")


class PendingTypeRename(unittest.TestCase):
    """落差 4：型別名只在檔案卡的待補型別裡（「案子改叫工程」）：新名不在型別表先問大類，帶 --category 加進型別表、
    卡的待補型別換成新名；退回改得回來。"""

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault()
        self.rels = [E.write_card(self.V, E.QUOTE, "報價", pending=["工地"]),
                     E.write_card(self.V, E.ESTIMATE, "估價", pending=["工地", "廠商"])]
        turn.start(self.V, None, "工地改叫工程")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pending(self, rel):
        return cards.parse_front((self.V / rel).read_text(encoding="utf-8"))["待補型別"]

    def test_needs_category_then_renames(self):
        r = run_kb("profile", "type", "rename", "工地", "--to", "工程", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 3)
        got = json.loads(r.stdout)
        self.assertTrue(got["message"].startswith("「工地」只在 2 張檔案卡的待補型別裡"))
        self.assertEqual(got["results"][1]["do"], ["kb profile type rename 工地 --to 工程 --category 事"])
        r = run_kb("profile", "type", "rename", "工地", "--to", "工程", "--category", "事", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.strip(), "已把 2 張檔案卡的待補型別「工地」換成「工程」，「工程」加進型別表（事）；"
                                           "下次整理會把這些卡掛上「工程」的實體，不對可以說退回")
        self.assertEqual([self.pending(r) for r in self.rels], [["工程"], ["工程", "廠商"]])
        prof = json.loads((self.V / ".system" / "profile.json").read_text(encoding="utf-8"))
        self.assertIn(("工程", "matter"), [(t["name"], t["category"]) for t in prof["types"]])
        self.assertTrue((self.V / "工程").is_dir())
        todo = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        self.assertEqual(sorted(u["reason"] for u in todo["unlinked"]), ["types_added", "types_added"])

    def test_revert(self):
        run_kb("profile", "type", "rename", "工地", "--to", "工程", "--category", "事", cwd=self.V)
        listing = run_kb("versions", "list", "--json", cwd=self.V)
        self.assertEqual(listing.returncode, 0)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([self.pending(r) for r in self.rels], [["工地"], ["工地", "廠商"]])
        self.assertFalse((self.V / "工程").exists())

    def test_existing_type_just_rewrites(self):
        run_kb("profile", "add", "型別", "名稱=工程", "大類=事", cwd=self.V)
        r = run_kb("profile", "type", "rename", "工地", "--to", "工程", cwd=self.V)
        self.assertEqual(r.stdout.strip(), "已把 2 張檔案卡的待補型別「工地」換成「工程」；下次整理會把這些卡掛上「工程」的實體，不對可以說退回")


class NoStopDuringBuild(unittest.TestCase):
    """落差 5：建置中上一句跑過 cards todo、沒等到 Stop 就來了下一句（桌面版把回答途中打的話排進來），不說「你停了」。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuilda-")).resolve()
        self.D = self.tmp / "知識庫"
        self.D.mkdir()
        self._env = build.env_items
        build.env_items = lambda cwd: [dict(i) for i in FAKE_ENV]
        build.start(self.D)

    def tearDown(self):
        build.env_items = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def say(self, prompt):
        from tests.test_hooks_turn import prompt_payload, run_hook
        r = run_hook(prompt_payload(self.D, prompt))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_no_stop_injection(self):
        self.say("案子改叫工程")
        self.assertEqual(run_kb("cards", "todo", "--json", cwd=self.D).returncode, 0)
        self.assertEqual(self.say("工程最想知道進度跟付款"), "")
        sess = json.loads((self.D / ".system" / "turns" / "test-sid.json").read_text(encoding="utf-8"))
        self.assertNotIn("ingest_stopped", sess)


class AdoptStepFinish(unittest.TestCase):
    """落差 14 與段檔：done adopt 要等剛記的口述、裁示寫進綜合卡；做完清掉 .system/temp/adopt/。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuilda-")).resolve()
        self.D = self.tmp / "知識庫"
        self.D.mkdir()
        self._env, self._collect = build.env_items, batches._collect
        build.env_items = lambda cwd: [dict(i) for i in FAKE_ENV]
        self.out = {}
        batches._collect = lambda vault: self.out
        build.start(self.D)
        turn.start(self.D, "sid-a", "收舊 wiki")
        turn.SESSION = "sid-a"
        build.set_value(self.D, "adopt", str(FIXTURES / "old_vault"))
        p = self.D / build.BUILD_POSIX
        data = json.loads(p.read_text(encoding="utf-8"))
        data["step"] = "adopt"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        build.adopt_extract(self.D, FIXTURES / "old_vault")

    def tearDown(self):
        build.env_items, batches._collect = self._env, self._collect
        turn.SESSION = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_waits_for_synthesis_then_cleans(self):
        self.out = {"synthesis_stale": [{"entity_id": "e-1", "name": "信義王", "path": "案子/信義王/信義王.md"}]}
        with self.assertRaises(build.Refused) as cm:
            build.done(self.D, "adopt")
        self.assertEqual(str(cm.exception), "剛記的口述、裁示要寫進 1 張綜合卡，先跑 kb cards dispatch 更新完再往下")
        self.assertTrue((self.D / ".system" / "temp" / "adopt").is_dir())
        self.out = {}
        self.assertEqual(build.done(self.D, "adopt")[2], "建置：收舊 wiki 做完了，下一步 交付報告")
        self.assertFalse((self.D / ".system" / "temp" / "adopt").exists())


if __name__ == "__main__":
    unittest.main()
