"""card-maker 桌面版確認 B 段：落差 3～7、10 的回歸。"""
import json
import unittest

from tests import _cards
from tests import test_hooks_stop as _stop
from tests import test_scripts_cards_cli as _cards_cli
from tests import test_hooks_output_log as _out
from tests import test_scripts_cards_dispatch as _dispatch
from tests.test_scripts_inventory_cli import run_kb
from tests.test_scripts_output_cli import SID, Base
from kb._lib import batches, cards, turn  # noqa: E402
from kb._lib import sources as _sources  # noqa: E402


class RevertOfRevertIsSymmetric(Base):
    """落差 10：同一句收起草稿又改了別的檔；退回 → 再退回（退掉退回本身）→ 再退回，三步每一步兩件事一起來回，
    versions list 的「已退回」跟實際對得上。"""

    def setUp(self):
        super().setUp()
        self.rel = self.draft()
        self.other = self.V / "系統" / "測試.md"
        self.other.parent.mkdir(parents=True, exist_ok=True)
        self.other.write_text("舊\n", encoding="utf-8")
        self.t1 = turn.start(self.V, SID, "1.完成了 2.不要 3.對")["turn"]
        turn.backup(self.V, "系統/測試.md", self.t1)
        self.other.write_text("新\n", encoding="utf-8")
        self.ok("drop-draft", self.rel)

    def revert(self, said):
        turn.start(self.V, SID, said)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def state(self):
        return (self.other.read_text(encoding="utf-8"), (self.V / self.rel).is_file(), self.entry(self.rel)["status"])

    def marks(self):
        rows = json.loads(run_kb("versions", "list", "--n", "10", "--json", cwd=self.V).stdout)
        return {r["turn"]: r["reverted"] for r in rows}

    def test_three_reverts(self):
        self.assertEqual(self.state(), ("新\n", False, "deleted"))
        self.revert("退回")
        self.assertEqual(self.state(), ("舊\n", True, "active"), "第一次：兩件都退掉，草稿回來")
        self.assertTrue(self.marks()[self.t1])
        self.revert("再退回")
        self.assertEqual(self.state(), ("新\n", False, "deleted"), "退掉退回本身：兩件都回來，草稿又收起來")
        self.assertFalse(self.marks()[self.t1], "那一句的改動回來了，不算已退回")
        self.revert("退回")
        self.assertEqual(self.state(), ("舊\n", True, "active"))
        marks = self.marks()
        self.assertTrue(marks[self.t1], "又退掉了：已退回照原文標回去")


class JobResultAfterLastJob(_stop.JobResult):
    """落差 7：同一句開了兩個 job（做卡做完再派綜合卡）；第一個關了、第二個還開著時不換結果，第二個關了才換成那次的最後回答。"""

    def jobs(self, tid, *statuses):
        data = self.batches.empty()
        for i, status in enumerate(statuses):
            data["jobs"].append({"job": f"j-{i}", "session_id": _stop.SID, "turn": tid, "created": "2026-10-22T20:11:50",
                                 "status": status})
        turn.turn_dir(self.V, tid)
        (self.V / self.batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")

    def test_waits_for_every_job_of_the_turn(self):
        job = self.prompt("繼續整理")
        self.jobs(job, "open")
        _stop.run(_stop.HOOK, _stop.stop_payload(self.V, "交給製卡助手在背景整理"))
        self.jobs(job, "closed", "open")
        _stop.run(_stop.HOOK, _stop.stop_payload(self.V, "還在做 3 批"))
        self.assertEqual(self.row(job)["result"], "交給製卡助手在背景整理", "還有 job 開著：不換成中間那句")
        self.assertFalse(turn.turn_data(self.V, job).get("job_result"), "還有 job 開著：不算換過")
        self.jobs(job, "closed", "closed")
        _stop.run(_stop.HOOK, _stop.stop_payload(self.V, "整理完了：做好 49 份"))
        self.assertEqual(self.row(job)["result"], "整理完了：做好 49 份")
        self.assertEqual(turn.turn_data(self.V, job)["job_result"], "j-1")
        _stop.run(_stop.HOOK, _stop.stop_payload(self.V, "再一次"))
        self.assertEqual(self.row(job)["result"], "整理完了：做好 49 份", "只換一次")

    test_result_replaced_once_after_job_closes = None
    test_secret_shown_job_turn_keeps_result = None


class Counts(_dispatch.Base):
    """落差 3、5、4、6：整理中先講還在做幾份；停完 stop、status 跟 waiting 同一個數、拆開講；停的時候還在寫的卡之後算進去；
    job 開著不問批次題；停過的 plan 最前面是 resume。"""

    def write(self, n, batch=0):
        """模擬製卡助手寫好第 batch 批的前 n 張卡。"""
        data = batches.load(self.V)
        ids = data["batches"][batch]["items"][:n]
        items = {i["id"]: i for i in self.todo()["new"]}
        for n, fid in enumerate(ids):
            _cards.write_card(self.V, items[fid], f"估價單第 {batch}-{n} 張")
        return ids

    def test_in_progress_then_stop_then_late_card(self):
        self.ok("cards", "dispatch")
        self.write(3)
        self.assertEqual(self.ok("cards", "status"), "正在整理：還在做 42 份（3 批），做好 3 份；還沒輪到的 3 份")
        got = self.js("cards", "stop")
        self.assertEqual((got["done"], got["left"], got["waiting_parts"]),
                         (3, 45, {"cards": 42, "main": 0, "agenda": 3, "total": 45}))
        self.write(1, batch=1)  # 停的時候還在寫的那張，TaskStop 之前寫完
        self.assertEqual(self.ok("cards", "status"),
                         "現在沒有在整理；停下做好 4 份；還有 45 份沒整理完（41 份還沒做卡、4 份卡做好了還沒抽日程）")
        self.assertEqual(self.js("cards", "status")["waiting"], 45)

    def test_no_batch_question_while_job_open(self):
        self.assertEqual(self.todo()["asks"][0]["id"], "batch", "45 份超過門檻：先問")
        self.ok("cards", "dispatch")
        todo = self.todo()
        self.assertNotIn("batch", [a["id"] for a in todo["asks"]], "答應了、job 開著：不再問")
        self.assertIn("dispatch", [s["step"] for s in todo["plan"]])

    def test_resume_after_stop(self):
        self.ok("cards", "dispatch")
        self.ok("cards", "stop")
        turn.start(self.V, _dispatch.SID, "你好")
        todo = self.todo()
        self.assertNotIn("batch", [a["id"] for a in todo["asks"]])
        first = todo["plan"][0]
        self.assertEqual((first["step"], first["do"], first["note"]),
                         ("resume", "kb cards dispatch --json",
                          "這個對話說過停：客戶說「整理」「繼續整理」才跑 do 接著做；沒說就不做，照常回答"))
        self.assertNotIn("dispatch", [s["step"] for s in todo["plan"]])
        turn.start(self.V, _dispatch.SID, "繼續整理")
        self.assertEqual(len(self.js("cards", "dispatch")["batches"]), 3)
        self.assertNotIn("resume", [s["step"] for s in self.todo()["plan"]], "接著做了：job 開著")


class RedoJob(_dispatch.Base):
    """整批重做（D-228 改）：status --json 帶這個對話的整理紀錄與各自寫好幾張卡；redo --job 先列再標，可以給好幾個；拒絕句一字不差。"""

    def test_status_lists_jobs_and_redo_marks_them(self):
        self.ok("cards", "dispatch")
        first = Counts.write(self, 2)
        self.ok("cards", "stop")
        turn.start(self.V, _dispatch.SID, "繼續整理")
        self.ok("cards", "dispatch")
        second = Counts.write(self, 1, batch=3)
        jobs = self.js("cards", "status")["jobs"]
        self.assertEqual([j["cards"] for j in jobs], [2, 1])
        ids = [j["job"] for j in jobs]
        args = ("cards", "redo", "--job", ids[0], "--job", ids[1])
        check = self.ok(*args, "--check").splitlines()
        self.assertEqual(check[0], "要標成重做的卡 3 張：")
        self.assertEqual(len(check), 4)
        self.assertNotRegex("\n".join(check), r"[fj]-[0-9a-f]{8}", "人讀不印編號")
        self.assertEqual([c["id"] for c in self.js(*args, "--check")["cards"]], first + second)
        self.assertEqual([i for i in self.todo()["stale"] if i["id"] in first + second], [], "--check 不寫")
        self.assertEqual(self.ok(*args), "已標成要重做：3 張卡，下次整理時照原檔重寫")
        stale = {i["id"]: i["reason"] for i in self.todo()["stale"]}
        self.assertEqual({k: stale.get(k) for k in first + second}, {k: "客戶說卡讀錯了" for k in first + second})

    def test_refusals(self):
        self.ok("cards", "dispatch")
        job = self.js("cards", "status")["job"]
        for args, line in [
            (("--job", "j-00000000"), "沒有這次整理：j-00000000"),
            (("--job", job), "這次整理沒有寫好的卡"),
            ((), "給一張卡的編號，或用 --job 給整理的編號，擇一"),
            (("f-00000000", "--job", job), "給一張卡的編號，或用 --job 給整理的編號，擇一"),
        ]:
            with self.subTest(args=args):
                r = self.kb("cards", "redo", *args)
                self.assertEqual((r.returncode, r.stdout.strip()), (3, line))

    def test_jobs_kept_after_cleanup(self):
        """關了很久的 job 拿掉前留一筆整理紀錄：之後照樣標得到。"""
        self.ok("cards", "dispatch")
        ids = Counts.write(self, 2)
        self.ok("cards", "stop")
        job = self.js("cards", "status")["jobs"][0]["job"]
        with batches.lock(self.V):
            data = batches.load(self.V)
            data["jobs"][0]["closed"] = (batches._now() - batches.timedelta(hours=2)).strftime(batches._TIME)
            data["jobs"].append({"job": "j-later", "session_id": _dispatch.SID, "turn": self.t,
                                 "created": "2026-01-02T00:00:00", "status": "closed", "closed": "2099-01-01T00:00:00"})
            batches.save(self.V, data, self.t)
        data = batches.load(self.V)
        self.assertNotIn(job, [j["job"] for j in data["jobs"]])
        self.assertEqual([c["id"] for c in self.js("cards", "redo", "--job", job, "--check")["cards"]], ids)


class RedoJobSynthesis(_cards_cli.RedoMount):
    """整批標的卡，掛它們的綜合卡「整理到」那行改 redo（照 D-147 一張一張標的做法）。"""

    def test_marks_synthesis_line(self):
        self.synthesis(self.E.DAAN, "大安陳")
        card = self.V / "案子" / "大安陳" / "大安陳.md"
        t = turn.start(self.V, "sid-x", "整理")["turn"]
        with batches.lock(self.V):
            data = batches.empty()
            data["jobs"].append({"job": "j-1", "session_id": "sid-x", "turn": t, "created": "2026-10-24T03:00:00",
                                 "status": "closed", "closed": "2099-01-01T00:00:00"})
            data["batches"].append({"batch": "b-1", "job": "j-1", "kind": "cards", "items": [self.fid],
                                    "created": "2026-10-24T03:00:00", "status": "closed"})
            batches.save(self.V, data, t)
        self.assertEqual(self.kb("cards", "redo", "--job", "j-1"),
                         ["已標成要重做：1 張卡，下次整理時照原檔重寫", "連帶要更新的綜合卡：大安陳"])
        self.assertIn(f"{self.fid} redo", cards.parse_front(card.read_text(encoding="utf-8"))["整理到"])

    for _name in [n for n in dir(_cards_cli.RedoMount) if n.startswith("test_")]:
        locals()[_name] = None
    del _name


class ClosedWindow(_dispatch.Base):
    """第 6 步（22 的決定）：SessionEnd 把那個對話的 job 與批次放掉；收不到的靠 last_seen：兩邊都活著不重切，
    舊對話沒動靜超過 card_batch_idle_minutes 新對話就切得到；開場份數與 waiting 照算。"""
    SID2 = "sid-other"

    def setUp(self):
        super().setUp()
        self.first = self.js("cards", "dispatch")

    def age(self, minutes, seen=True):
        """把三批的建立時間（與 last_seen）往前推 minutes 分鐘，各記一個子代理。"""
        old = (batches._now() - batches.timedelta(minutes=minutes)).strftime(batches._TIME)
        with batches.lock(self.V):
            data = batches.load(self.V)
            for i, b in enumerate(data["batches"]):
                b["created"], b["agent_id"] = old, f"a-{i}"
                if seen:
                    b["last_seen"] = old
                data["agents"].append({"agent_id": f"a-{i}", "session_id": _dispatch.SID, "turn": self.t, "started": old})
            batches.save(self.V, data, self.t)

    def other(self):
        turn.start(self.V, self.SID2, "整理")
        r = run_kb("--session", self.SID2, "cards", "dispatch", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def end(self, reason):
        return _out.run_hook({"session_id": _dispatch.SID, "cwd": str(self.V), "hook_event_name": "SessionEnd",
                              "reason": reason}, hook=_out.ROOT / "hooks" / "session_end.py")

    def test_clear_and_resume_keep_claims(self):
        """/clear、/resume 還是同一個行程，製卡助手還活著：不放，交給 last_seen。"""
        for reason in ("clear", "resume"):
            with self.subTest(reason=reason):
                self.assertEqual(self.end(reason).returncode, 0)
                self.assertEqual({b["status"] for b in batches.load(self.V)["batches"]}, {"open"})
        cfg = json.loads((_out.ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["hooks"]["SessionEnd"][0]["matcher"], "logout|prompt_input_exit|other")

    def released_by(self, reason):
        self.assertEqual(self.end(reason).returncode, 0)
        self.assertEqual({b["status"] for b in batches.load(self.V)["batches"]}, {"released"})

    def test_logout_releases(self):
        self.released_by("logout")

    def test_prompt_input_exit_releases(self):
        self.released_by("prompt_input_exit")

    def test_session_end_releases(self):
        r = self.end("other")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        data = batches.load(self.V)
        self.assertEqual({b["status"] for b in data["batches"]}, {"released"})
        self.assertEqual([(j["status"], j.get("released_by_session_end"), j.get("stopped_turn")) for j in data["jobs"]],
                         [("closed", True, None)])
        self.assertNotIn(_dispatch.SID, data.get("revoked") or {}, "不是接手：之後回來照樣能整理")
        self.assertEqual(len(self.other()["batches"]), 3)

    def test_alive_sessions_keep_their_claims(self):
        self.age(15)
        for i, b in enumerate(self.first["batches"]):  # 子代理還在跑 kb：last_seen 一直更新
            r = run_kb("--turn", self.t, "--agent", f"a-{i}", "cards", "batch", b["batch"], "--json", cwd=self.V)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = self.other()
        self.assertEqual(got["batches"], [])
        self.assertEqual(got["say"], "另一個對話正在整理其中 45 份，這邊先不派，等那邊做完再說「整理」")

    def test_card_write_counts_as_alive(self):
        self.age(15)
        card = self.V / "檔案索引" / "x.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("x\n", encoding="utf-8")
        payload = dict(_out.post_payload(self.V, "Write", card), session_id=_dispatch.SID, agent_id="a-0")
        _out.run_hook(payload)
        seen = {b["agent_id"]: b.get("last_seen") for b in batches.load(self.V)["batches"]}
        self.assertGreater(seen["a-0"], seen["a-1"], "寫卡的那個子代理 last_seen 更新")

    def test_idle_other_session_can_be_recut(self):
        self.age(11)
        self.assertEqual(len(self.other()["batches"]), 3, "舊對話沒動靜超過 10 分鐘：新對話切得到")

    def test_own_session_keeps_old_rule_and_hold_needs_fresh_seen(self):
        self.age(11)
        again = self.js("cards", "dispatch")
        self.assertEqual((again["batches"], again["running"]), ([], 3), "自己的批次照舊：30 分鐘內算還在跑")
        now = batches._now()
        b = {"status": "open", "created": (now - batches.timedelta(minutes=40)).strftime(batches._TIME), "agent_id": "a-0"}
        agents = {"a-0": {"agent_id": "a-0"}}
        self.assertFalse(batches.live(dict(b, last_seen=b["created"]), now, 30, agents, 10), "過期又沒動靜：不延長")
        self.assertTrue(batches.live(dict(b, last_seen=now.strftime(batches._TIME)), now, 30, agents, 10))

    def test_counts_ignore_claims(self):
        before = batches.opening_counts(self.V, _sources.load(self.V))[0]
        self.age(11)
        self.assertEqual((before, batches.opening_counts(self.V, _sources.load(self.V))[0]), (45, 45))
        self.assertEqual(self.js("cards", "status")["waiting"], 45)


if __name__ == "__main__":
    unittest.main()
