"""build 第一輪修（22 派工，測試報告 design/reports/build-測試.md 的 S1～S10）：每項一組重現。"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tests._guard_fixture import FIXTURES, ROOT, make_vault, payload
from tests.test_cardmaker_round1 import guard_out
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import batches, build, credentials, extract, turn  # noqa: E402

FAKE_ENV = [{"key": k, "level": "ok", "text": k} for k in build.ENV_KEYS]
KB = f'python "{ROOT}/scripts/kb.py"'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuildr1-")).resolve()
        self.D = self.tmp / "新知識庫"
        self.D.mkdir()
        self._env, self._collect = build.env_items, batches._collect
        build.env_items = lambda cwd: [dict(i) for i in FAKE_ENV]
        self.out = {}
        batches._collect = lambda vault: self.out
        build.start(self.D)
        self.t = turn.start(self.D, "sid-a", "/wiki-with-willy:build")["turn"]
        turn.SESSION = "sid-a"

    def tearDown(self):
        build.env_items, batches._collect = self._env, self._collect
        turn.SESSION = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def data(self):
        return json.loads((self.D / build.BUILD_POSIX).read_text(encoding="utf-8"))

    def put(self, **kw):
        data = self.data()
        data.update(kw)
        (self.D / build.BUILD_POSIX).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def refused(self, fn, *a, **kw):
        with self.assertRaises(build.Refused) as cm:
            fn(*a, **kw)
        return str(cm.exception)

    def edit_batches(self, change):
        data = batches.load(self.D)
        change(data)
        (self.D / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")


def files(n):
    return [{"id": f"f-{i:08x}", "uri": f"file://案子/{i}.txt"} for i in range(n)]


class S1TakeoverReleasesOldBatches(Base):
    """接手之後，前一個對話派出去、子代理沒停的批次不再擋 done cards；放掉的項目這個對話切得出來。"""

    def test_takeover_then_dispatch_and_done(self):
        self.put(step="cards")
        build.status(self.D)
        self.out = {"new": files(3)}
        got = batches.dispatch(self.D, "sid-a", self.t)
        self.assertEqual([b["items"] for b in got["batches"]], [3])
        batches.agent_start(self.D, "sid-a", "agent-old")
        self.edit_batches(lambda d: d["batches"][0].update(agent_id="agent-old"))  # 子代理認領了、沒停（視窗被關掉）
        turn.SESSION = "sid-b"
        self.assertTrue(self.refused(build.status, self.D).startswith("另一個對話正在建置"))
        build.status(self.D, takeover=True)
        data = batches.load(self.D)
        self.assertEqual([b["status"] for b in data["batches"]], ["released"])
        self.assertEqual(data["jobs"][0]["status"], "closed")
        self.assertNotIn("stopped_turn", data["jobs"][0], "不是那一句說了停")
        self.assertEqual(build._running(self.D, ("cards", "agenda")), [])
        self.assertEqual(batches.status(self.D, "sid-b")["running"], [], "build 與 cards status 同一個判斷")
        t2 = turn.start(self.D, "sid-b", "接手建置")["turn"]
        again = batches.dispatch(self.D, "sid-b", t2)
        self.assertEqual([b["items"] for b in again["batches"]], [3], "放掉的項目回到可派")
        self.assertEqual(self.refused(build.done, self.D, "cards"),
                         "還有 1 批在背景整理，做完再往下")
        self.edit_batches(lambda d: d["batches"][-1].update(status="done"))
        self.out = {}
        self.assertEqual(build.done(self.D, "cards")[2], "建置：全量整理 做完了，下一步 確認名字")

    def test_running_counts_only_this_session(self):
        """build._running 跟 batches.running 同一支：別的對話的批次不算（那邊被鎖擋著，接手時放掉）。"""
        now = datetime.now().strftime(build.TIME)
        (self.D / batches.BATCHES_POSIX).write_text(json.dumps({"schema_version": 1, "agents": [], "jobs": [
            {"job": "j-1", "session_id": "sid-x", "status": "open"}], "batches": [
            {"batch": "b-1", "job": "j-1", "kind": "cards", "items": ["f-1"], "created": now, "status": "open"}]}),
            encoding="utf-8")
        self.assertEqual(build._running(self.D, ("cards",)), [])
        turn.SESSION = "sid-x"
        self.assertEqual(len(build._running(self.D, ("cards",))), 1)


class S3SampleJobClosed(Base):
    """抽樣的 ids job：done sample 收掉；抽樣批次在跑時擋；job 開著時 --ids 帶不存在的也拒絕。"""

    def test_done_sample_closes_ids_job_then_full_dispatch(self):
        self.put(step="sample")
        build.status(self.D)
        self.out = {"new": files(42)}
        ids = [f["id"] for f in files(4)]
        got = batches.dispatch(self.D, "sid-a", self.t, ids=ids)
        self.assertEqual([b["items"] for b in got["batches"]], [4])
        self.assertEqual(self.refused(build.done, self.D, "sample"), "抽樣還在背景做，做完再往下")
        with self.assertRaises(batches.Refused) as cm:
            batches.dispatch(self.D, "sid-a", self.t, ids=[ids[0], "f-deadbeef"])
        self.assertEqual(str(cm.exception), "這幾份不在要整理的清單裡：f-deadbeef")
        self.edit_batches(lambda d: d["batches"][0].update(status="done"))
        self.out = {"new": files(42)[4:]}
        self.assertEqual(build.done(self.D, "sample")[2], "建置：抽樣 做完了，下一步 全量整理")
        self.assertEqual([j["status"] for j in batches.load(self.D)["jobs"]], ["closed"])
        full = batches.dispatch(self.D, "sid-a", turn.start(self.D, "sid-a", "好，全部整理")["turn"])
        self.assertEqual([b["items"] for b in full["batches"]], [20, 18])
        self.assertFalse(full["done"])


class S2AdoptCredentials(Base):
    """舊 wiki：檔名像密碼的頁不切、記進 skipped 與報告；pwd、Bearer、私鑰整段、解鎖碼遮掉；連結的 hot.md 不讀。"""

    SECRETS = ("Zq8kd91mm", "Zq8!kd91xx", "eyJhbGciOiJ", "MIIEowIBAAKCAQEA", "Zq8kd91pp")

    def setUp(self):
        super().setUp()
        self.O = self.tmp / "old_vault"
        shutil.copytree(FIXTURES / "old_vault", self.O)
        (self.O / "wiki" / "projects" / "密碼清單.md").write_text("網銀: Zq8kd91mm\n", encoding="utf-8")
        (self.O / "wiki" / "topics" / "雜項.md").write_text(
            "# 雜項\n\npwd: Zq8!kd91xx\nAuthorization: Bearer eyJhbGciOiJ.x.y\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabcdef\n-----END RSA PRIVATE KEY-----\n"
            "解鎖碼 Zq8kd91pp\n廚具用 L 型。\n", encoding="utf-8")

    def test_check_and_extract_skip_and_mask(self):
        check = build.adopt_check(self.O)
        self.assertEqual(check["skipped"], ["wiki/projects/密碼清單.md"])
        self.assertEqual(check["wiki_pages"], 3)
        got = build.adopt_extract(self.D, self.O)
        self.assertEqual(got["skipped"], ["wiki/projects/密碼清單.md"])
        self.assertNotIn("wiki/projects/密碼清單.md", [f["path"] for f in got["files"]])
        whole = "".join(Path(c).read_text(encoding="utf-8") for c in got["chunks"])
        self.assertIn("廚具用 L 型", whole)
        for s in self.SECRETS:
            self.assertNotIn(s, whole, s)

    def test_report_lists_skipped(self):
        build.set_value(self.D, "adopt", str(self.O))
        facts = build.report_facts(self.D)
        self.assertEqual(facts["adopt_skipped"], ["wiki/projects/密碼清單.md"])
        self.assertIn("- 舊 wiki 裡檔名像密碼、沒讀的：wiki/projects/密碼清單.md", build.numbers_lines(facts))

    def test_linked_special_file_not_read(self):
        hot = self.O / "wiki" / "hot.md"
        target = self.tmp / "外面的.md"
        target.write_text("外面的秘密內容\n", encoding="utf-8")
        hot.unlink()
        try:
            os.symlink(target, hot)
        except OSError:
            self.skipTest("這台建不了連結")
        got = build.adopt_extract(self.D, self.O)
        self.assertNotIn("wiki/hot.md", [f["path"] for f in got["files"]])
        self.assertNotIn("外面的秘密內容", "".join(Path(c).read_text(encoding="utf-8") for c in got["chunks"]))
        self.assertEqual(build.adopt_check(self.O)["hot_chars"], 0)


class S2MaskWords(unittest.TestCase):
    def test_new_words_and_pem(self):
        text = ("pwd: Zq8!kd91xx\nAuthorization: Bearer eyJhbGciOiJ.x\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1r\n"
                "-----END OPENSSH PRIVATE KEY-----\n解鎖碼 Zq8kd91pp\n")
        got = credentials.mask_secrets(text)
        for s in ("Zq8!kd91xx", "eyJhbGciOiJ", "b3BlbnNzaC1r", "Zq8kd91pp", "BEGIN OPENSSH"):
            self.assertNotIn(s, got, s)
        self.assertEqual(credentials.mask_secrets("-----BEGIN PRIVATE KEY-----\nabc"), credentials.MASK, "沒有結尾就到最後")
        self.assertEqual(credentials.mask_secrets("typing pwdx 照常"), "typing pwdx 照常")


class S6ScrubForms(unittest.TestCase):
    def test_repr_and_json_forms(self):
        pw = "Pw#9x'q\\z"
        text = f"bad {pw!r} / {json.dumps(pw)} / {pw}"
        got = extract._scrub(text, pw)
        self.assertNotIn("Pw#9x", got)


class S7GivenUp(Base):
    def test_report_facts_has_given_up(self):
        (self.D / batches.BATCHES_POSIX).write_text(json.dumps({"schema_version": 1, "agents": [], "batches": [], "jobs": [
            {"job": "j-1", "session_id": "sid-a", "status": "closed",
             "failed": {"案子/王宅/王宅.md": {"kind": "synthesis", "reason": "檔頭兩次被打回"}}}]}), encoding="utf-8")
        facts = build.report_facts(self.D)
        self.assertEqual(facts["given_up"], [{"id": "案子/王宅/王宅.md", "kind": "synthesis", "reason": "檔頭兩次被打回",
                                              "name": "王宅.md"}])
        self.assertIn("- 製卡助手沒做成 1 份：王宅.md", build.numbers_lines(facts))
        self.assertIn("given_up", (ROOT / "templates" / "build-report.md").read_text(encoding="utf-8"))


class S9FinishedVault(unittest.TestCase):
    """建置做完的知識庫：facts、report、adopt 照 status 那句擋。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuildr1-")).resolve()
        self.D = self.tmp / "新知識庫"
        self.D.mkdir()
        self.assertEqual(run_kb("build", "start", cwd=self.D).returncode, 0)
        p = self.D / build.BUILD_POSIX
        data = json.loads(p.read_text(encoding="utf-8"))
        data["step"] = "done"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_refused(self):
        old = str(FIXTURES / "old_vault")
        for args in (("facts", "--json"), ("report", "--facts"), ("report", "--from", "x.md"), ("adopt", old, "--check"),
                     ("adopt", old, "--extract")):
            with self.subTest(args=args):
                r = run_kb("build", *args, cwd=self.D)
                self.assertEqual((r.returncode, r.stdout.strip()), (3, build.ALREADY))
        self.assertFalse((self.D / ".system" / "temp" / "adopt").exists())


class S8AdoptAgentCannotWrite(unittest.TestCase):
    """記過的子代理認領的是讀舊 wiki 的批次時，Write／Edit 一律 deny；做卡批次照舊放行寫卡。"""

    def setUp(self):
        self.tmp, self.V, _raw = make_vault()
        turn.start(self.V, "sid-g", "好")
        batches.agent_start(self.V, "sid-g", "agent-1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def batch(self, kind):
        data = batches.load(self.V)
        data["batches"] = [{"batch": "b-1", "job": "j-1", "kind": kind, "items": ["001.txt"], "agent_id": "agent-1",
                            "created": datetime.now().strftime(build.TIME), "status": "open"}]
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")

    def write(self, tool="Write"):
        data = payload(tool, self.V / "檔案索引" / "x.md", self.V)
        data.update(session_id="sid-g", agent_id="agent-1", agent_type=batches.AGENT)
        return guard_out(data)

    def test_adopt_batch_denied_cards_batch_allowed(self):
        self.batch("cards")
        self.assertEqual(self.write().get("permissionDecision"), "allow")
        self.batch("adopt")
        for tool in ("Write", "Edit"):
            out = self.write(tool)
            self.assertEqual((out["permissionDecision"], out["permissionDecisionReason"]), ("deny", "製卡助手不能做這件事"))


if __name__ == "__main__":
    unittest.main()
