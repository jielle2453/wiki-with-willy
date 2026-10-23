"""_lib/build（build 第 2 點，D-190、D-194、D-044；驗收 1、2、5）：步驟順序、每步 done 的前提、redo、沒登記資料夾的短路、
已是知識庫擋、不空提醒、兩個建置的鎖。環境檢查換成固定的八項（環境本身在 test_scripts_build_cli 測）；todo 的材料用假的。"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests._guard_fixture import FIXTURES
from kb._lib import batches, build, turn  # noqa: E402

FAKE_ENV = [{"key": k, "level": "ok", "text": k} for k in build.ENV_KEYS]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuild-")).resolve()
        self.D = self.tmp / "新知識庫"
        self.D.mkdir()
        self._env = build.env_items
        build.env_items = lambda cwd: [dict(i) for i in FAKE_ENV]
        self._collect = batches._collect
        self.out = {}

    def tearDown(self):
        build.env_items = self._env
        batches._collect = self._collect
        turn.SESSION = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refused(self, fn, *a):
        with self.assertRaises(build.Refused) as cm:
            fn(*a)
        return str(cm.exception)

    def data(self):
        return json.loads((self.D / build.BUILD_POSIX).read_text(encoding="utf-8"))

    def put(self, **kw):
        data = self.data()
        data.update(kw)
        (self.D / build.BUILD_POSIX).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def fake_todo(self, **out):
        self.out = out
        batches._collect = lambda vault: self.out

    def started(self, sid="sid-a"):
        build.start(self.D)
        turn.start(self.D, sid, "/wiki-with-willy:build")


class Where(Base):
    def test_empty_folder_starts_at_env(self):
        got = build.status(self.D)
        self.assertEqual((got["step"], got["not_empty"], got["do"], got["after"]),
                         ("env", False, ["kb build env --json"], "kb build start"))
        self.assertFalse((self.D / ".system").exists(), "status 不建任何東西")

    def test_not_empty_only_counts_real_things(self):
        (self.D / ".claude").mkdir()
        (self.D / "desktop.ini").write_text("x", encoding="utf-8")
        self.assertFalse(build.status(self.D)["not_empty"])
        (self.D / "舊檔.txt").write_text("x", encoding="utf-8")
        self.assertTrue(build.status(self.D)["not_empty"])

    def test_existing_vault_without_record_refused(self):
        V = self.tmp / "vault"
        shutil.copytree(FIXTURES / "inventory" / "vault", V)
        self.assertEqual(self.refused(build.status, V), "這裡已經是建好的知識庫，要加資料夾、整理檔案直接跟我說")
        self.assertEqual(self.refused(build.start, V), build.ALREADY)
        self.assertFalse((V / build.BUILD_POSIX).exists())

    def test_system_folder_alone_counts_as_vault(self):
        (self.D / ".system").mkdir()
        self.assertEqual(self.refused(build.status, self.D), build.ALREADY)

    def test_inside_a_vault_refused(self):
        V = self.tmp / "vault"
        shutil.copytree(FIXTURES / "inventory" / "vault", V)
        self.assertEqual(self.refused(build.status, V / "案子"), build.ALREADY)

    def test_finished_build_refused(self):
        self.started()
        self.put(step="done")
        for fn, a in ((build.status, ()), (build.done, ("finish",)), (build.redo, ("sample",)),
                      (build.set_value, ("people", "兩個人"))):
            self.assertEqual(self.refused(fn, self.D, *a), build.ALREADY)


class Start(Base):
    def test_skeleton_and_record(self):
        vault, data = build.start(self.D)
        self.assertEqual(vault, self.D)
        for rel in (".system/sources.json", ".system/profile.json", "系統/客戶設定檔.md", "CLAUDE.md"):
            self.assertTrue((self.D / rel).exists(), rel)
        got = self.data()
        self.assertEqual((got["step"], list(got["done"]), got["env"]["items"]), ("setup", ["env"], FAKE_ENV))
        self.assertEqual(self.refused(build.start, self.D), build.ALREADY)


class Steps(Base):
    def test_sequence(self):
        self.assertEqual(build.sequence({}), ["env", "setup", "inventory", "sample", "cards", "names", "synthesis",
                                              "finish", "done"])
        self.assertEqual(build.sequence({"adopt": "C:/舊"}), ["env", "setup", "inventory", "sample", "cards", "names",
                                                             "synthesis", "adopt", "finish", "done"])
        self.assertEqual(build.sequence({"no_sources": True}), ["env", "setup", "finish", "done"])
        self.assertEqual(build.sequence({"no_sources": True, "adopt": "x"}), ["env", "setup", "adopt", "finish", "done"])

    def test_wrong_step_refused(self):
        self.started()
        self.assertEqual(self.refused(build.done, self.D, "inventory"), "現在是「訪談」這一步，不是「盤點」")

    def test_no_sources_goes_to_finish(self):
        self.started()
        _v, data, line = build.done(self.D, "setup")
        self.assertEqual((line, data["step"], data["no_sources"]), ("建置：訪談 做完了，下一步 交付報告", "finish", True))
        self.assertEqual(build.status(self.D)["step"], "finish")

    def test_walk_all_steps_with_source(self):
        self.started()
        raw = self.tmp / "raw"
        raw.mkdir()
        (self.D / ".system" / "sources.json").write_text(json.dumps(
            {"schema_version": 1, "sources": [{"id": "src-1", "alias": "案子", "spec": str(raw), "excludes": []}]},
            ensure_ascii=False), encoding="utf-8")
        self.fake_todo()
        lines = [build.done(self.D, s)[2] for s in ("setup", "inventory", "sample", "cards", "names", "synthesis")]
        self.assertEqual(lines[0], "建置：訪談 做完了，下一步 盤點")
        self.assertEqual(lines[-1], "建置：綜合卡 做完了，下一步 交付報告")
        self.assertEqual(self.refused(build.done, self.D, "finish"), "建置報告還沒寫，先跑 kb build report --from <寫好的那份>")
        self.put(report="2026-10-23T10:00:00")
        self.assertEqual(build.done(self.D, "finish")[2], "建置：交付報告 做完了，建置完成")
        self.assertEqual(self.data()["step"], "done")
        self.assertEqual(self.refused(build.status, self.D), build.ALREADY)

    def test_setup_needs_readable_profile(self):
        self.started()
        md = self.D / "系統" / "客戶設定檔.md"
        sep = "|---|---|---|---|---|" + chr(10)
        md.write_text(md.read_text(encoding="utf-8").replace(sep, sep + "| 案子 | 不存在的大類 |  |  |  |" + chr(10), 1),
                      encoding="utf-8")
        self.assertTrue(self.refused(build.done, self.D, "setup").startswith("客戶設定檔讀不懂："))
        self.assertEqual(self.data()["step"], "setup")

    def at(self, step):
        self.started()
        self.put(step=step)

    def test_cards_needs_todo_empty(self):
        self.at("cards")
        self.fake_todo(new=[{"id": "f-00000001", "uri": "file://案子/a.txt"}, {"id": "f-00000002", "uri": "file://案子/b.txt"}])
        self.assertEqual(self.refused(build.done, self.D, "cards"), "還有 2 份沒整理完，整理完再往下")

    def test_cards_skips_what_card_maker_gave_up(self):
        self.at("cards")
        self.fake_todo(new=[{"id": "f-00000001", "uri": "file://案子/a.txt"}])
        (self.D / batches.BATCHES_POSIX).write_text(json.dumps({"schema_version": 1, "batches": [], "agents": [], "jobs": [
            {"job": "j-1", "session_id": "sid-a", "status": "closed", "failed": {"f-00000001": {"kind": "cards"}}}]}),
            encoding="utf-8")
        self.assertEqual(build.done(self.D, "cards")[2], "建置：全量整理 做完了，下一步 確認名字")

    def test_cards_waits_for_running_batches(self):
        self.at("cards")
        self.fake_todo()
        now = datetime.now().strftime(build.TIME)
        (self.D / batches.BATCHES_POSIX).write_text(json.dumps({"schema_version": 1, "agents": [], "jobs": [
            {"job": "j-1", "session_id": "sid-a", "status": "open"}], "batches": [
            {"batch": "b-1", "job": "j-1", "kind": "cards", "items": ["f-1"], "created": now, "status": "open"}]}),
            encoding="utf-8")
        self.assertEqual(self.refused(build.done, self.D, "cards"), "還有 1 批在背景整理，做完再往下")

    def test_names_needs_all_confirmed(self):
        self.at("names")
        self.fake_todo(synthesis_new=[{"entity_id": "e-1", "name": "王宅", "type": "案子", "confirmed": False, "cards": []},
                                      {"entity_id": "e-2", "name": "陳宅", "type": "案子", "confirmed": True, "cards": []}])
        self.assertEqual(self.refused(build.done, self.D, "names"), "還有 1 個名字沒確認，確認完再往下")

    def test_synthesis_needs_none_left(self):
        self.at("synthesis")
        self.fake_todo(synthesis_new=[{"entity_id": "e-2", "name": "陳宅", "confirmed": True}])
        self.assertEqual(self.refused(build.done, self.D, "synthesis"), "還有 1 張綜合卡沒寫好，寫完再往下")

    def test_redo(self):
        self.at("cards")
        self.put(done={"env": "a", "setup": "b", "inventory": "c", "sample": "d"})
        _v, data, line = build.redo(self.D, "sample")
        self.assertEqual((line, data["step"], sorted(data["done"])), ("建置：回到「抽樣」", "sample",
                                                                      ["env", "inventory", "setup"]))
        self.assertEqual(self.refused(build.redo, self.D, "names"), "還沒做到「確認名字」，不能重做")
        self.assertEqual(self.refused(build.redo, self.D, "adopt"), "這次建置沒有「收舊 wiki」這一步")


class Lock(Base):
    def test_second_session_refused_until_lock_expires(self):
        self.started("sid-a")
        self.assertEqual(build.status(self.D)["step"], "setup")
        self.assertEqual(self.data()["session_id"], "sid-a")
        turn.start(self.D, "sid-b", "/wiki-with-willy:build")
        last = datetime.strptime(self.data()["last_action"], build.TIME).strftime("%H:%M")
        self.assertEqual(self.refused(build.status, self.D),
                         f"另一個對話正在建置（訪談，最後動作 {last}）；那個對話還開著就回那邊繼續，已經關掉了就說『接手建置』")
        self.assertEqual(self.refused(build.done, self.D, "setup"), build.BUSY.format(step="訪談", time=last))
        self.put(lock_until=(datetime.now() - timedelta(minutes=1)).strftime(build.TIME))
        self.assertEqual(build.status(self.D)["step"], "setup")
        self.assertEqual(self.data()["session_id"], "sid-b")

    def test_same_session_refreshes(self):
        self.started("sid-a")
        build.status(self.D)
        first = self.data()["lock_until"]
        self.put(lock_until=(datetime.now() + timedelta(minutes=1)).strftime(build.TIME))
        build.status(self.D)
        self.assertGreaterEqual(self.data()["lock_until"], first)

    def test_injected_session_wins(self):
        self.started("sid-a")
        build.status(self.D)
        turn.SESSION = "sid-other"
        self.assertTrue(self.refused(build.status, self.D).startswith("另一個對話正在建置（訪談，最後動作 "))

    def test_takeover_then_old_session_refused(self):
        """build 驗後修：關掉視窗再開一個對話，說「接手建置」就換成這個對話；原本那個再打會被擋。"""
        turn.SESSION = "sid-a"
        self.started("sid-a")
        build.status(self.D)
        turn.SESSION = "sid-b"
        self.assertTrue(self.refused(build.status, self.D).startswith("另一個對話正在建置"))
        got = build.status(self.D, takeover=True)
        self.assertEqual(got["step"], "setup")
        data = self.data()
        self.assertEqual((data["session_id"], [(t["from"], t["to"]) for t in data["takeovers"]]), ("sid-b", [("sid-a", "sid-b")]))
        self.assertEqual((data["step"], data["done"]), ("setup", {"env": data["done"]["env"]}), "其他不動")
        self.assertEqual(build.done(self.D, "setup")[2], "建置：訪談 做完了，下一步 交付報告")
        turn.SESSION = "sid-a"
        self.assertTrue(self.refused(build.status, self.D).startswith("另一個對話正在建置（交付報告，最後動作 "))
        self.assertTrue(self.refused(build.done, self.D, "finish").startswith("另一個對話正在建置"))

    def test_takeover_by_owner_records_nothing(self):
        turn.SESSION = "sid-a"
        self.started("sid-a")
        build.status(self.D, takeover=True)
        self.assertNotIn("takeovers", self.data())


class SetValue(Base):
    def test_people_masked_and_recorded(self):
        self.started()
        _v, data, line = build.set_value(self.D, "people", "我跟助理兩個人")
        self.assertEqual((line, self.data()["people"]), ("記下了：幾個人用：我跟助理兩個人", "我跟助理兩個人"))
        build.set_value(self.D, "people", "兩個人，網銀密碼 abc12345")
        self.assertNotIn("abc12345", self.data()["people"])

    def test_adopt_needs_old_vault(self):
        self.started()
        self.assertEqual(self.refused(build.set_value, self.D, "adopt", str(FIXTURES / "inventory" / "raw")),
                         "這不像舊的 llm-wiki 資料夾")
        _v, data, line = build.set_value(self.D, "adopt", str(FIXTURES / "old_vault"))
        self.assertEqual(data["adopt"], (FIXTURES / "old_vault").resolve().as_posix())
        self.assertEqual(line, f"記下了：要收的舊 wiki 在 {data['adopt']}")
        self.assertIn("adopt", build.sequence(self.data()))

    def test_empty_value_refused(self):
        self.started()
        self.assertEqual(self.refused(build.set_value, self.D, "people", "  "), "要記的值是空的")


if __name__ == "__main__":
    unittest.main()
