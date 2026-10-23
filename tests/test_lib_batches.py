"""_lib/batches（card-maker，D-195～D-200；驗收 2～5、12）：切批大小與並行、認領排除、過期、換 session 不認、--source、
cards → agenda → synthesis 的順序、大改動門檻回 approval、stop 放掉。todo 的材料用假的（換掉 batches._collect）。"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests._guard_fixture import ROOT  # noqa: F401  （把 scripts/ 放進 sys.path）
from kb._lib import batches, turn  # noqa: E402

SID = "sid-1"


def files(n, alias="報價", start=0):
    return [{"id": f"f-{i:08x}", "uri": f"file://{alias}/{i}.txt"} for i in range(start, start + n)]


class Base(unittest.TestCase):
    def setUp(self):
        self.V = Path(tempfile.mkdtemp())
        (self.V / ".system").mkdir()
        for alias in ("報價", "合約"):  # dispatch --source 要登記過的別名
            (self.V.parent / f"{self.V.name}-{alias}").mkdir()
        (self.V / ".system" / "sources.json").write_text(json.dumps({"schema_version": 1, "sources": [
            {"id": f"src-{i}", "alias": a, "spec": f"../{self.V.name}-{a}", "excludes": []}
            for i, a in enumerate(("報價", "合約"))]}, ensure_ascii=False), encoding="utf-8")
        self.out = {}
        self._orig = batches._collect
        batches._collect = lambda vault: self.out
        self.t = turn.start(self.V, SID, "好，現在做")["turn"]

    def tearDown(self):
        batches._collect = self._orig
        shutil.rmtree(self.V, ignore_errors=True)
        for alias in ("報價", "合約"):
            shutil.rmtree(self.V.parent / f"{self.V.name}-{alias}", ignore_errors=True)

    def config(self, **kw):
        (self.V / ".system" / "config.json").write_text(json.dumps(kw), encoding="utf-8")

    def entries(self, rows):
        (self.V / ".system" / "inventory.json").write_text(json.dumps({"schema_version": 2, "entries": rows}),
                                                            encoding="utf-8")

    def dispatch(self, sid=SID, source=None):
        return batches.dispatch(self.V, sid, turn.current(self.V, sid)["turn"], source)

    def data(self):
        return batches.load(self.V)

    def edit(self, change):
        data = self.data()
        change(data)
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")


class Cut(Base):
    def test_45_files_three_batches_20_20_5(self):
        self.out = {"new": files(45)}
        got = self.dispatch()
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("cards", 20), ("cards", 20), ("cards", 5)])
        self.assertEqual((got["running"], got["left"], got["done"], got["approval"]), (0, {"cards": 0, "agenda": 0, "synthesis": 0},
                                                                                      False, None))
        self.assertEqual(got["turn"], self.t)
        for b in got["batches"]:
            self.assertEqual(b["agent"], "wiki-with-willy:card-maker")
            self.assertIn(f"{batches.prefix()} cards batch {b['batch']} --json", b["prompt"])
        items = [k for b in self.data()["batches"] for k in b["items"]]
        self.assertEqual(sorted(items), sorted(i["id"] for i in self.out["new"]))

    def test_parallel_caps_open_batches(self):
        self.out = {"new": files(100)}
        got = self.dispatch()
        self.assertEqual(len(got["batches"]), 3)
        self.assertEqual(got["left"]["cards"], 40)

    def test_second_dispatch_makes_nothing_new(self):
        self.out = {"new": files(45)}
        first = self.dispatch()
        got = self.dispatch()
        self.assertEqual((got["batches"], got["running"], got["job"]), ([], 3, first["job"]))

    def test_sizes_from_config(self):
        self.config(card_batch_size=4, card_parallel=2)
        self.out = {"new": files(10)}
        got = self.dispatch()
        self.assertEqual([b["items"] for b in got["batches"]], [4, 4])
        self.assertEqual(got["left"]["cards"], 2)


class Claims(Base):
    def test_done_batch_items_still_pending_are_failed_not_recut(self):
        """驗收 3 改（第一輪修 S2a）：批次 done 了、項目還在待做 → 記成這個 job 沒做成，不再切；released 的才放回來。"""
        self.out = {"new": files(45)}
        self.dispatch()
        first = self.data()["batches"][0]
        self.out = {"new": files(40, start=5)}  # 第一批做好前 5 份，其餘 15 份沒做成
        self.edit(lambda d: d["batches"][0].update(status="done"))
        got = self.dispatch()
        self.assertEqual(got["batches"], [])
        self.assertEqual(sorted(f["id"] for f in got["failed"]), sorted(first["items"][5:]))
        self.assertEqual({f["reason"] for f in got["failed"]}, {batches.DEFAULT_FAIL})
        self.edit(lambda d: d["batches"][1].update(status="released"))  # 第二批被停了：放回來
        (made,) = self.dispatch()["batches"]
        self.assertEqual(self.data()["batches"][-1]["items"], self.data()["batches"][1]["items"])

    def test_report_reasons_and_all_failed_is_done(self):
        self.out = {"new": files(2)}
        self.dispatch()
        b = self.data()["batches"][0]["batch"]
        batches.agent_start(self.V, SID, "a-1")
        batches.batch_view(self.V, b, "a-1")
        batches.agent_stop(self.V, SID, "a-1", "批次 x：做好 0、沒做成 2\n- f-00000000：讀不到（有密碼）\n"
                                                "- f-00000001：檔頭打回兩次\n被擋或打回：無")
        got = self.dispatch()
        self.assertEqual({f["id"]: f["reason"] for f in got["failed"]},
                         {"f-00000000": "讀不到（有密碼）", "f-00000001": "檔頭打回兩次"})
        self.assertTrue(got["done"], "只剩沒做成的：整理完了，不再派")

    def test_failed_in_two_jobs_goes_to_pending_until_file_changes(self):
        """第一輪修 S2b：同一份（指紋沒變）兩個 job 都沒做成 → 照讀不到記進待處理。"""
        from kb._lib import pending
        item = files(1)
        self.entries([{"id": item[0]["id"], "status": "active", "read": "ok", "fingerprints": [{"sha256": "fp1"}],
                       "locations": [{"uri": item[0]["uri"]}]}])
        for n in range(2):
            self.out = {"new": item}
            self.dispatch()
            self.edit(lambda d: d["batches"][-1].update(status="done"))
            got = self.dispatch()
            self.assertTrue(got["done"])
            turn.start(self.V, SID, f"再整理 {n}")
        (p,) = [i for i in pending.load(self.V)["items"] if i["kind"] == "unreadable"]
        self.assertEqual((p["file_ids"], p["status"]), ([item[0]["id"]], "open"))
        self.assertTrue(p["reason"].startswith("製卡助手兩次沒做成"))

    def test_same_turn_redispatch_is_not_a_second_try(self):
        """22 第二輪驗後：同一回合 done 之後再 dispatch → 不切 failed、failures 計數不變、不記待處理；
        換一個回合再 dispatch → 重試一次，再沒做成才記待處理。"""
        from kb._lib import pending
        item = files(1)
        self.entries([{"id": item[0]["id"], "status": "active", "read": "ok", "fingerprints": [{"sha256": "fp1"}],
                       "locations": [{"uri": item[0]["uri"]}]}])
        self.out = {"new": item}
        self.dispatch()
        self.edit(lambda d: d["batches"][-1].update(status="done"))
        self.assertTrue(self.dispatch()["done"])
        self.assertEqual(self.data()["failures"][item[0]["id"]]["jobs"].__len__(), 1)
        n_batches = len(self.data()["batches"])
        again = self.dispatch()  # 同一回合又叫一次
        self.assertEqual((again["batches"], again["done"]), ([], True))
        self.assertEqual([f["id"] for f in again["failed"]], [item[0]["id"]])
        self.assertIsNone(again["say"], "第三輪修 R4：沒切任何東西、只沿用上次的 failed，不說話（最後的回答照 tell）")
        self.assertEqual(len(self.data()["batches"]), n_batches)
        self.assertEqual(len(self.data()["failures"][item[0]["id"]]["jobs"]), 1)
        self.assertEqual([i for i in pending.load(self.V)["items"] if i["kind"] == "unreadable"], [])
        turn.start(self.V, SID, "再整理一次")  # 換一個回合：重試一次
        retry = self.dispatch()
        self.assertEqual(len(retry["batches"]), 1)
        self.edit(lambda d: d["batches"][-1].update(status="done"))
        self.assertTrue(self.dispatch()["done"])
        (p,) = [i for i in pending.load(self.V)["items"] if i["kind"] == "unreadable"]
        self.assertTrue(p["reason"].startswith("製卡助手兩次沒做成"))

    def test_failures_reset_when_fingerprint_changes(self):
        from kb._lib import pending
        item = files(1)
        for fp in ("fp1", "fp2"):
            self.entries([{"id": item[0]["id"], "status": "active", "read": "ok", "fingerprints": [{"sha256": fp}],
                           "locations": [{"uri": item[0]["uri"]}]}])
            self.out = {"new": item}
            self.dispatch()
            self.edit(lambda d: d["batches"][-1].update(status="done"))
            self.dispatch()
            turn.start(self.V, SID, f"再整理 {fp}")
        self.assertEqual([i for i in pending.load(self.V)["items"] if i["kind"] == "unreadable"], [])

    def test_expired_batch_counts_as_released(self):
        self.out = {"new": files(45)}
        self.dispatch()
        old = (datetime.now() - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%S")
        self.edit(lambda d: d["batches"][0].update(created=old))
        got = self.dispatch()
        self.assertEqual((len(got["batches"]), got["running"]), (1, 2))
        self.assertEqual(self.data()["batches"][0]["status"], "open", "過期只是讀到時當放掉，不改檔")

    def test_other_session_does_not_see_old_claims(self):
        self.out = {"new": files(45)}
        first = self.dispatch()
        turn.start(self.V, "sid-2", "繼續整理")
        got = self.dispatch("sid-2")
        self.assertNotEqual(got["job"], first["job"])
        self.assertEqual((len(got["batches"]), got["running"]), (3, 0))

    def test_unknown_source_refused(self):
        """第一輪修：--source 打錯別名不准回整理完了。"""
        self.out = {"new": files(3)}
        with self.assertRaises(batches.Refused) as cm:
            self.dispatch(source="報價單")
        self.assertEqual(str(cm.exception), "沒有登記這個來源：報價單")
        self.assertFalse((self.V / batches.BATCHES_POSIX).exists())

    def test_expired_but_agent_alive_stays_claimed(self):
        """第一輪修：過期了但子代理還沒停，不准被重切（三倍時間內）。"""
        self.out = {"new": files(45)}
        self.dispatch()
        b = self.data()["batches"][0]["batch"]
        batches.agent_start(self.V, SID, "a-1")
        batches.batch_view(self.V, b, "a-1")
        old = (datetime.now() - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%S")
        self.edit(lambda d: d["batches"][0].update(created=old))
        self.assertEqual((self.dispatch()["batches"], self.dispatch()["running"]), ([], 3))
        older = (datetime.now() - timedelta(minutes=91)).strftime("%Y-%m-%dT%H:%M:%S")
        self.edit(lambda d: d["batches"][0].update(created=older))
        self.assertEqual(len(self.dispatch()["batches"]), 1)

    def test_agent_dies_before_claiming(self):
        """第一輪修：子代理第一次 cards batch 之前就停了 → 多出來沒人認領的批次放掉，不用等過期。"""
        self.out = {"new": files(45)}
        self.dispatch()
        for a in ("a-1", "a-2", "a-3"):
            batches.agent_start(self.V, SID, a)
        b0 = self.data()["batches"][0]["batch"]
        batches.batch_view(self.V, b0, "a-1")
        batches.agent_stop(self.V, SID, "a-3")  # 沒認領就停：三批只剩兩個子代理會來拿
        statuses = [b["status"] for b in self.data()["batches"]]
        self.assertEqual(statuses.count("released"), 1)
        self.assertEqual(statuses[0], "open", "已經認領的不放")

    def test_source_only(self):
        self.out = {"new": files(5, "報價") + files(7, "合約", start=100)}
        self.entries([{"id": i["id"], "status": "active", "read": "ok", "locations": [{"uri": i["uri"]}]}
                      for i in self.out["new"]])
        got = self.dispatch(source="報價")
        self.assertEqual([b["items"] for b in got["batches"]], [5])
        self.assertEqual(self.data()["batches"][0]["source"], "報價")
        self.assertTrue(all(k.startswith("f-0000000") for k in self.data()["batches"][0]["items"]))

    def test_job_remembers_source(self):
        """審閱修：收通知再派不帶 --source，照 job 記的來源，範圍不放寬。"""
        self.config(card_batch_size=2, card_parallel=1)
        self.out = {"new": files(4, "報價") + files(3, "合約", start=100)}
        self.entries([{"id": i["id"], "status": "active", "read": "ok", "locations": [{"uri": i["uri"]}]}
                      for i in self.out["new"]])
        self.dispatch(source="報價")
        self.edit(lambda d: d["batches"][0].update(status="done"))
        self.out = {"new": files(2, "報價", start=2) + files(3, "合約", start=100)}
        got = self.dispatch()
        self.assertEqual(got["left"]["cards"], 0)
        self.assertEqual(self.data()["batches"][-1]["items"], [i["id"] for i in files(2, "報價", start=2)])


class Order(Base):
    def test_synthesis_waits_for_cards_and_agenda(self):
        stale = [{"entity_id": f"e-{i}", "name": f"甲{i}"} for i in range(7)]
        self.out = {"new": files(2), "synthesis_stale": stale}
        got = self.dispatch()
        self.assertEqual([b["kind"] for b in got["batches"]], ["cards"])
        self.assertEqual(got["left"], {"cards": 0, "agenda": 0, "synthesis": 7})
        self.out = {"synthesis_stale": stale, "agenda_unscanned": [{"id": "f-9", "card_path": "p", "existing": []}]}
        self.assertEqual(self.dispatch()["batches"], [], "做卡那批還在跑：不建後一種")
        self.edit(lambda d: d["batches"][0].update(status="done"))
        self.assertEqual([b["kind"] for b in self.dispatch()["batches"]], ["agenda"])
        self.edit(lambda d: d["batches"][-1].update(status="done"))
        self.out = {"synthesis_stale": stale}
        got = self.dispatch()
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("synthesis", 5), ("synthesis", 2)])
        self.assertIn("綜合卡 5 張", got["batches"][0]["prompt"])

    def test_unconfirmed_synthesis_new_is_not_sent(self):
        self.out = {"synthesis_new": [{"entity_id": "e-1", "confirmed": False}, {"entity_id": "e-2", "confirmed": True}]}
        got = self.dispatch()
        self.assertEqual([b["items"] for b in got["batches"]], [1])
        self.assertEqual(self.data()["batches"][0]["items"], ["e-2"])

    def test_nothing_left_closes_job_and_gives_note_cmd(self):
        self.out = {"new": files(2)}
        got = self.dispatch()
        self.edit(lambda d: d["batches"][0].update(status="done"))
        self.out = {}
        got = self.dispatch()
        self.assertTrue(got["done"])
        self.assertEqual(got["note_cmd"], f'kb turn note --turn {self.t} "{{做了什麼}}"')
        self.assertEqual(self.data()["jobs"][0]["status"], "closed")

    def test_nothing_at_all_opens_no_job(self):
        got = self.dispatch()
        self.assertEqual((got["done"], got["job"], got["note_cmd"]), (True, None, None))
        self.assertFalse((self.V / batches.BATCHES_POSIX).exists())


class Cleanup(Base):
    def test_old_finished_entries_removed(self):
        """第一輪修：jobs、batches、agents 結束超過 card_batch_expire_minutes 的移走；每個 session 最後一個關了的 job 留著。"""
        old = (datetime.now() - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%S")
        data = batches.empty()
        data["jobs"] = [{"job": "j-old", "session_id": SID, "turn": self.t, "created": old, "status": "closed", "closed": old},
                        {"job": "j-last", "session_id": SID, "turn": self.t, "created": old, "status": "closed", "closed": old}]
        data["batches"] = [{"batch": "b-old", "job": "j-old", "kind": "cards", "items": [], "created": old, "status": "done"}]
        data["agents"] = [{"agent_id": "a-old", "session_id": SID, "turn": self.t, "started": old, "stopped": old}]
        (self.V / batches.BATCHES_POSIX).write_text(json.dumps(data), encoding="utf-8")
        self.out = {"new": files(1)}
        self.dispatch()
        got = self.data()
        self.assertEqual([j["job"] for j in got["jobs"]][:1], ["j-last"])
        self.assertNotIn("b-old", [b["batch"] for b in got["batches"]])
        self.assertEqual(got["agents"], [])


class BigChange(Base):
    def test_over_the_limit_asks_then_approve_sends(self):
        """驗收 5：job 回合已改 9 張、這次 synthesis_stale 3 張 → 不派、回 approval；approve --turn 後再派。"""
        data = turn.current(self.V, SID)
        data["ai_synthesis"] = [f"案子/甲{i}/甲{i}.md" for i in range(9)]
        turn.save_turn(self.V, data)
        self.out = {"synthesis_stale": [{"entity_id": f"e-{i}", "name": f"乙{i}"} for i in range(3)]}
        got = self.dispatch()
        self.assertEqual(got["batches"], [])
        self.assertEqual(got["approval"], {"question": "這次要更新 12 張綜合卡，比平常多；要繼續嗎？",
                                           "do": [f"kb turn approve --turn {self.t}"]})
        self.assertFalse(got["done"])
        turn.start(self.V, SID, "好，繼續")  # 客戶答話開了新回合：同意記在 job 回合
        turn.approve(self.V, turn_id=self.t)
        got = self.dispatch()
        self.assertEqual([b["kind"] for b in got["batches"]], ["synthesis"])
        self.assertIsNone(got["approval"])

    def test_broken_counts_too(self):
        """第一輪修：綜合卡 broken 寫回原路徑也是改既有綜合卡，跟 stale 一起算進門檻。"""
        orig = batches._fixable
        batches._fixable = lambda vault, ctx, item: True  # 假的路徑沒有實體：當成修得了
        self.addCleanup(setattr, batches, "_fixable", orig)
        self.out = {"synthesis_stale": [{"entity_id": f"e-{i}"} for i in range(6)],
                    "synthesis_broken": [{"path": f"案子/甲{i}/甲{i}.md", "reason": "檔頭有錯"} for i in range(6)]}
        got = self.dispatch()
        self.assertEqual(got["approval"]["question"], "這次要更新 12 張綜合卡，比平常多；要繼續嗎？")

    def test_within_the_limit_sends(self):
        self.out = {"synthesis_stale": [{"entity_id": f"e-{i}"} for i in range(10)]}
        self.assertIsNone(self.dispatch()["approval"])


class Stop(Base):
    def test_stop_releases_and_lists_agents(self):
        self.out = {"new": files(45)}
        self.dispatch()
        b = self.data()["batches"][0]["batch"]
        batches.batch_view(self.V, b, "a-1")
        batches.agent_start(self.V, SID, "a-2")
        got = batches.stop(self.V, SID)
        self.assertEqual(got["agents"], ["a-1", "a-2"])
        self.assertEqual((got["done"], got["left"]), (0, 45))
        data = self.data()
        self.assertEqual({x["status"] for x in data["batches"]}, {"released"})
        self.assertEqual(data["jobs"][0]["status"], "closed")
        self.assertIsNone(batches.stop(self.V, SID))
        with self.assertRaises(batches.Refused):
            batches.batch_view(self.V, b)

    def test_status_counts_done(self):
        self.out = {"new": files(45)}
        self.dispatch()
        self.out = {"new": files(30, start=15)}  # 前 15 份的卡寫好了
        got = batches.status(self.V, SID)
        self.assertEqual((len(got["running"]), got["done"], got["left"]["cards"]), (3, 15, 0))


class Dispatch2(Base):
    def test_batch_has_chinese_description_and_say_once(self):
        """第一輪修 S6：給 Agent 工具的 description 是繁中；say 只在第一次派出去時有。"""
        self.config(card_batch_size=2)
        self.out = {"new": files(8)}
        got = self.dispatch()
        self.assertEqual([b["description"] for b in got["batches"]],
                         ["整理第 1 批（做卡 2 份）", "整理第 2 批（做卡 2 份）", "整理第 3 批（做卡 2 份）"])
        self.assertEqual(got["say"], batches.SAY_FIRST)
        self.edit(lambda d: d["batches"][0].update(status="released"))
        again = self.dispatch()
        self.assertEqual(again["batches"][0]["description"], "整理第 4 批（做卡 2 份）")
        self.assertEqual(again["say"], "還在做 3 批", "第二輪修 R2：之後每次都給一句進度")

    def test_bucket_key_keeps_entity_type(self):
        """第一輪修：batch_view 不蓋掉 synthesis_new 的 type（實體型別）。"""
        self.out = {"synthesis_new": [{"entity_id": "e-1", "type": "案子", "confirmed": True}]}
        b = self.dispatch()["batches"][0]["batch"]
        (item,) = batches.batch_view(self.V, b)["items"]
        self.assertEqual((item["type"], item["bucket"]), ("案子", "synthesis_new"))

    def test_locked_ids_skip_resolved_and_dismissed(self):
        from kb._lib import pending
        rows = [{"id": f"f-{c}", "status": "active", "read": "ok", "locations": [{"uri": f"file://報價/{c}.pdf"}]}
                for c in "abcd"]
        self.entries(rows)
        for c, how in (("a", None), ("b", "auto_done"), ("c", "resolve"), ("d", "dismiss")):
            pid = pending.add(self.V, "unreadable", [f"f-{c}"], "x", "有密碼", self.t)
            if how == "auto_done":
                from kb import convert
                pending.auto_done(self.V, f"f-{c}", "unreadable", convert.OPENED_HOW, self.t)
            elif how == "resolve":
                pending.resolve(self.V, pid, "換了沒密碼的檔", self.t)
            elif how == "dismiss":
                pending.dismiss(self.V, pid, self.t)
        self.assertEqual(batches._locked_ids(self.V), {"f-a", "f-b"})


class Agents(Base):
    def test_start_records_job_turn_even_after_a_new_turn(self):
        self.out = {"new": files(3)}
        self.dispatch()
        turn.start(self.V, SID, "信義王的報價在哪？")
        self.assertEqual(batches.agent_start(self.V, SID, "a-9"), self.t)
        self.assertEqual(turn.agent_turn(self.V, SID, "a-9"), self.t)
        self.assertIsNone(turn.agent_turn(self.V, SID, "a-other"))

    def test_stop_marks_its_batch_done(self):
        self.out = {"new": files(3)}
        self.dispatch()
        b = self.data()["batches"][0]["batch"]
        batches.agent_start(self.V, SID, "a-9")
        batches.batch_view(self.V, b, "a-9")
        self.assertEqual(self.data()["batches"][0]["agent_id"], "a-9")
        self.assertEqual(batches.agent_stop(self.V, SID, "a-9"), self.t)
        self.assertEqual(self.data()["batches"][0]["status"], "done")
        self.assertIsNone(batches.agent_stop(self.V, SID, "a-unknown"))

    def test_bad_file_raises_with_name(self):
        (self.V / batches.BATCHES_POSIX).write_text("{", encoding="utf-8")
        with self.assertRaises(batches.Unreadable) as cm:
            batches.load(self.V)
        self.assertIn(".system/batches.json", str(cm.exception))

    def test_writes_back_up_into_job_turn(self):
        self.out = {"new": files(3)}
        self.dispatch()
        self.assertIn(batches.BATCHES_POSIX, [e["path"] for e in turn.entries(self.V, self.t)])


if __name__ == "__main__":
    unittest.main()


class Purge(Base):
    def test_purge_pending_folder_not_sent_and_plan_order(self):
        """驗收 2b：purge pending 那夾的條目不進批次；plan 的 purge 兩步排在 dispatch 前面。"""
        from kb._lib import config, plan
        inside = [{"id": "f-in", "uri": "file://報價/私人/a.txt"}]
        outside = [{"id": "f-out", "uri": "file://報價/b.txt"}]
        self.entries([{"id": i["id"], "status": "active", "read": "ok", "locations": [{"uri": i["uri"]}]}
                      for i in inside + outside])
        self.out = {"new": inside + outside,
                    "purge_pending": [{"name": "報價/私人", "alias": "報價", "spec": "私人", "synthesis": ["案子/甲/甲.md"]}]}
        got = self.dispatch()
        self.assertEqual(self.data()["batches"][0]["items"], ["f-out"])
        self.assertEqual(got["left"]["cards"], 0)
        steps = [s["step"] for s in plan.build(dict(self.out, moved=[], duplicates=[]), self.V, config)["plan"]]
        self.assertEqual(steps, ["purge_synthesis", "purge", "dispatch"])



class Ids(Base):
    """build 第 6 點（驗收 7）：dispatch --ids 只派那幾份做卡；job 記著，之後再派照它；不在可派清單裡的拒絕。"""

    def test_only_those(self):
        self.out = {"new": files(30), "agenda_unscanned": [{"id": "f-00000000"}],
                    "synthesis_new": [{"entity_id": "e-1", "confirmed": True}]}
        got = batches.dispatch(self.V, SID, self.t, ids=["f-00000003", "f-00000007"])
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("cards", 2)])
        self.assertEqual(self.data()["batches"][0]["items"], ["f-00000003", "f-00000007"])
        self.assertEqual(got["left"], {"cards": 0, "agenda": 0, "synthesis": 0})
        self.assertEqual(self.data()["jobs"][0]["ids"], ["f-00000003", "f-00000007"])
        self.edit(lambda d: d["batches"][0].update(status="done"))
        self.out = {"new": [f for f in files(30) if f["id"] not in ("f-00000003", "f-00000007")],
                    "agenda_unscanned": [{"id": "f-00000000"}]}
        again = self.dispatch()  # 不帶 --ids：照 job 記的
        self.assertEqual((again["batches"], again["done"]), ([], True))

    def test_unknown_refused(self):
        self.out = {"new": files(3)}
        with self.assertRaises(batches.Refused) as cm:
            batches.dispatch(self.V, SID, self.t, ids=["f-00000001", "f-deadbeef"])
        self.assertEqual(str(cm.exception), "這幾份不在要整理的清單裡：f-deadbeef")
        self.assertEqual(self.data()["jobs"], [])


class Adopt(Base):
    """build 第 6 點（驗收 11）：第四種工作 adopt。段分批（一批 ADOPT_BATCH_CHUNKS 段）、同時 card_parallel 個；
    job 跟整理的分開；做完的段不再派、回報的「- 段：原因」記成沒讀成；stop 一起停。"""

    def chunks(self, n):
        d = self.V / batches.ADOPT_POSIX
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (d / f"{i:03d}.txt").write_text(f"<!-- 來源：wiki/{i}.md，第 1／1 段 -->\n內容 {i}", encoding="utf-8")

    def adopt(self, sid=SID):
        return batches.dispatch_adopt(self.V, sid, turn.current(self.V, sid)["turn"])

    def test_no_chunks_refused(self):
        with self.assertRaises(batches.Refused) as cm:
            self.adopt()
        self.assertEqual(str(cm.exception), "還沒切段，先跑 kb build adopt <舊 wiki 的路徑> --extract")

    def test_batches_and_done(self):
        self.chunks(14)
        got = self.adopt()
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("adopt", 4), ("adopt", 4), ("adopt", 4)])
        self.assertEqual((got["left"], got["say"], got["done"]), ({"adopt": 2}, batches.SAY_ADOPT_FIRST, False))
        self.assertEqual(got["batches"][0]["description"], "整理第 1 批（讀舊 wiki 4 段）")
        job = self.data()["jobs"][0]
        self.assertEqual(job["kind"], "adopt")
        self.assertIsNone(batches.open_job(self.data(), SID), "整理的 job 不認 adopt 的")
        view = batches.batch_view(self.V, got["batches"][0]["batch"])
        self.assertEqual([i["name"] for i in view["items"]], ["001.txt", "002.txt", "003.txt", "004.txt"])
        self.assertTrue(view["items"][0]["chunk"].endswith("/.system/temp/adopt/001.txt"))
        self.assertIn(f"批次 {got['batches'][0]['batch']}：讀了 N 段", view["report"])
        self.edit(lambda d: [b.update(status="done") for b in d["batches"][:2]] and
                  d["batches"][0].update(failed={"002.txt": "段落是空的"}))
        nxt = self.adopt()
        self.assertEqual([b["items"] for b in nxt["batches"]], [2])
        self.assertEqual(nxt["say"], "讀好 7 段了，1 段沒讀成；還在讀 2 批")
        self.edit(lambda d: [b.update(status="done") for b in d["batches"]])
        last = self.adopt()
        self.assertEqual((last["done"], last["say"], last["failed"]),
                         (True, "舊的 wiki 讀完了，1 段沒讀成", [{"id": "002.txt", "kind": "adopt", "reason": "段落是空的"}]))
        self.assertEqual(self.data()["jobs"][0]["status"], "closed")

    def test_agent_start_uses_adopt_job_and_stop_closes_it(self):
        self.chunks(2)
        self.adopt()
        self.assertEqual(batches.agent_start(self.V, SID, "a-1"), self.t)
        self.edit(lambda d: d["batches"][0].update(agent_id="a-1"))
        got = batches.stop(self.V, SID, self.t)
        self.assertEqual(got["agents"], ["a-1"])
        self.assertEqual(self.data()["jobs"][0]["status"], "closed")
        self.assertTrue(self.adopt()["stopped"], "這一句說了停")

    def test_cards_job_unaffected(self):
        self.chunks(2)
        self.adopt()
        self.out = {"new": files(3)}
        got = self.dispatch()
        self.assertEqual([(b["kind"], b["items"]) for b in got["batches"]], [("cards", 3)])
        self.assertEqual(len(self.data()["jobs"]), 2)


class NameQuestion(unittest.TestCase):
    """build 交界：名字題的「掛了 N 份檔」掛 0 份時改說「還沒做卡」。"""

    def test_wording(self):
        self.assertEqual(batches.NAME_QUESTION.format(name="王宅", type="案子", mounted=batches.NO_CARDS_YET),
                         "我要幫「王宅」（案子，還沒做卡）整理一頁現況：名字這樣叫對嗎？它跟其他案子是同一件嗎？")
        self.assertEqual(batches.NAME_QUESTION.format(name="王宅", type="案子", mounted=batches.MOUNTED.format(n=3)),
                         "我要幫「王宅」（案子，掛了 3 份檔）整理一頁現況：名字這樣叫對嗎？它跟其他案子是同一件嗎？")


if __name__ == "__main__":
    unittest.main()
