"""_lib/inventory：成品登記（D-076，schema 2 格式見清冊題）。新建、同內容不重登、改內容追加指紋、outputs_of_turn、set_status、並行登記（I-080）。
第一輪 M2／M4：只配 status active 的條目；沒有 active 條目時，這回合 manifest 記 create 才新建、
曾登記過（turn_reverted／deleted）又再改也新建、從沒登記過的既有檔（modify）不登記。"""
import json
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import SCRIPTS, make_vault
from kb._lib import inventory, turn  # noqa: E402

SID = "test-sid"


def create_in_turn(vault, rel, turn_id, text):
    """模擬檔案工具在這回合新建一份：先備份（不存在 → create）再寫。"""
    turn.backup(vault, rel, turn_id)
    (vault / rel).write_text(text, encoding="utf-8")


def modify_in_turn(vault, rel, turn_id, text):
    turn.backup(vault, rel, turn_id)
    (vault / rel).write_text(text, encoding="utf-8")


class Register(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.rel = "AI 產出/x.md"
        self.t = turn.start(self.V, SID, "寫草稿")
        create_in_turn(self.V, self.rel, self.t["turn"], "第一版\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty_schema(self):
        self.assertEqual(inventory.load(self.V), {"schema_version": 2, "entries": []})

    def test_new_entry_shape(self):
        e = inventory.register_output(self.V, self.rel, self.t["turn"])
        data = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(len(data["entries"]), 1)
        got = data["entries"][0]
        self.assertEqual(got, e)
        self.assertRegex(got["id"], r"^f-[0-9a-f]{8}$")
        self.assertEqual(got["origin"], "ai_output")
        st = (self.V / self.rel).stat()
        self.assertEqual(got["locations"], [{"uri": "file://AI 產出/x.md", "size": st.st_size,
                                             "mtime": inventory.mtime_str(st.st_mtime)}])
        self.assertEqual((got["read"], got["missing_since"]), ("ok", None))
        self.assertEqual(got["first_seen"], got["fingerprints"][0]["time"])
        self.assertNotIn("created", got)
        self.assertEqual(got["created_turn"], self.t["turn"])
        self.assertEqual(got["status"], "active")
        self.assertEqual(len(got["fingerprints"]), 1)
        fp = got["fingerprints"][0]
        self.assertEqual(set(fp), {"sha256", "time", "turn"})
        self.assertEqual(fp["turn"], self.t["turn"])
        import hashlib
        self.assertEqual(fp["sha256"], hashlib.sha256((self.V / self.rel).read_bytes()).hexdigest())

    def test_same_content_does_not_add(self):
        inventory.register_output(self.V, self.rel, self.t["turn"])
        inventory.register_output(self.V, self.rel, self.t["turn"])
        data = inventory.load(self.V)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(len(data["entries"][0]["fingerprints"]), 1)

    def test_changed_content_appends_fingerprint_to_active_entry(self):
        inventory.register_output(self.V, self.rel, self.t["turn"])
        t2 = turn.start(self.V, SID, "再改")
        modify_in_turn(self.V, self.rel, t2["turn"], "第二版\n")
        inventory.register_output(self.V, self.rel, t2["turn"])
        data = inventory.load(self.V)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(len(data["entries"][0]["fingerprints"]), 2)
        self.assertEqual(data["entries"][0]["fingerprints"][1]["turn"], t2["turn"])
        self.assertEqual(data["entries"][0]["created_turn"], self.t["turn"])
        self.assertEqual(data["entries"][0]["locations"][0]["size"], (self.V / self.rel).stat().st_size)

    def test_outputs_of_turn_only_active_of_that_turn(self):
        t2 = turn.start(self.V, "other", "別的")
        create_in_turn(self.V, "AI 產出/y.md", t2["turn"], "y")
        a = inventory.register_output(self.V, self.rel, self.t["turn"])
        b = inventory.register_output(self.V, "AI 產出/y.md", t2["turn"])
        self.assertEqual([e["id"] for e in inventory.outputs_of_turn(self.V, self.t["turn"])], [a["id"]])
        inventory.set_status(self.V, [a["id"]], "turn_reverted")
        self.assertEqual(inventory.outputs_of_turn(self.V, self.t["turn"]), [])
        self.assertEqual(inventory.load(self.V)["entries"][0]["status"], "turn_reverted")
        self.assertEqual(inventory.load(self.V)["entries"][1]["status"], "active")
        self.assertEqual(b["status"], "active")

    def test_reverted_entry_then_modified_again_gets_new_entry(self):
        """第一輪 M2（驗收 22）：舊筆 turn_reverted，新回合再改這份檔 → 新 id、active、created_turn 是新回合。"""
        old = inventory.register_output(self.V, self.rel, self.t["turn"])
        inventory.set_status(self.V, [old["id"]], "turn_reverted")
        t2 = turn.start(self.V, SID, "再寫")
        modify_in_turn(self.V, self.rel, t2["turn"], "新內容\n")
        new = inventory.register_output(self.V, self.rel, t2["turn"])
        self.assertIsNotNone(new)
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(new["status"], "active")
        self.assertEqual(new["created_turn"], t2["turn"])
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["status"], "superseded")  # 第二輪 R2-3：舊筆被新成品取代

    def test_new_entry_marks_old_non_active_entries_superseded(self):
        """第二輪 R2-3(1)：新建條目時，同 location 其他非 active 的舊條目改 superseded。"""
        old = inventory.register_output(self.V, self.rel, self.t["turn"])
        inventory.set_status(self.V, [old["id"]], "turn_reverted")
        t2 = turn.start(self.V, SID, "再寫")
        modify_in_turn(self.V, self.rel, t2["turn"], "新內容\n")
        new = inventory.register_output(self.V, self.rel, t2["turn"])
        entries = {e["id"]: e for e in inventory.load(self.V)["entries"]}
        self.assertEqual(entries[old["id"]]["status"], "superseded")
        self.assertEqual(entries[new["id"]]["status"], "active")
        self.assertEqual(inventory.outputs_of_turn(self.V, self.t["turn"]), [])

    def test_superseded_is_not_touched_by_set_status_of_others(self):
        old = inventory.register_output(self.V, self.rel, self.t["turn"])
        inventory.set_status(self.V, [old["id"]], "deleted")
        (self.V / self.rel).unlink()
        t2 = turn.start(self.V, SID, "重寫")
        create_in_turn(self.V, self.rel, t2["turn"], "重寫\n")
        inventory.register_output(self.V, self.rel, t2["turn"])
        entries = {e["id"]: e for e in inventory.load(self.V)["entries"]}
        self.assertEqual(entries[old["id"]]["status"], "superseded")

    def test_deleted_entry_then_recreated_gets_new_entry(self):
        old = inventory.register_output(self.V, self.rel, self.t["turn"])
        inventory.set_status(self.V, [old["id"]], "deleted")
        (self.V / self.rel).unlink()
        t2 = turn.start(self.V, SID, "重寫")
        create_in_turn(self.V, self.rel, t2["turn"], "重寫\n")
        new = inventory.register_output(self.V, self.rel, t2["turn"])
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(len(inventory.load(self.V)["entries"]), 2)

    def test_preexisting_unregistered_file_is_not_registered(self):
        """第一輪 M4（驗收 23）：客戶自己放的檔，AI 第一次編輯是 modify，不登記、回 None。"""
        q = "AI 產出/q.md"
        (self.V / q).write_text("客戶自己放的\n", encoding="utf-8")
        t2 = turn.start(self.V, SID, "改 q")
        modify_in_turn(self.V, q, t2["turn"], "AI 改過\n")
        self.assertIsNone(inventory.register_output(self.V, q, t2["turn"]))
        self.assertEqual(inventory.load(self.V)["entries"], [])

    def test_no_manifest_line_is_not_registered(self):
        (self.V / "AI 產出" / "z.md").write_text("z", encoding="utf-8")
        self.assertIsNone(inventory.register_output(self.V, "AI 產出/z.md", self.t["turn"]))
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_two_processes_register_concurrently_both_kept(self):
        create_in_turn(self.V, "AI 產出/y.md", self.t["turn"], "y")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from kb._lib import inventory; "
                "inventory.register_output(sys.argv[2], sys.argv[3], sys.argv[4])")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.V), rel, self.t["turn"]],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for rel in (self.rel, "AI 產出/y.md")]
        for p in procs:
            _out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err.decode("utf-8", "replace"))
        data = inventory.load(self.V)
        self.assertEqual(sorted(e["locations"][0]["uri"] for e in data["entries"]), ["file://AI 產出/x.md", "file://AI 產出/y.md"])


class SkipKind(unittest.TestCase):
    """客戶設定檔與型別第 4 點：選填欄 skip_kind；scan／apply／reconcile 保留它；set_skip、unskip_kinds。"""

    def setUp(self):
        from tests import _cards
        from tests.test_lib_inventory_scan import make_inv_vault
        self.tmp, self.V, self.raw = make_inv_vault()
        _cards.scan_apply(self.V)
        self.entry = _cards.entry_of(self.V, "報價與合約/合約範本.txt")
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def by_id(self):
        return {e["id"]: e for e in inventory.load(self.V)["entries"]}

    def test_set_and_clear(self):
        inventory.set_skip(self.V, [self.entry["id"]], "請款")
        self.assertEqual(self.by_id()[self.entry["id"]]["skip_kind"], "請款")
        inventory.set_skip(self.V, [self.entry["id"]], None)
        self.assertNotIn("skip_kind", self.by_id()[self.entry["id"]])

    def test_kept_after_scan_apply_and_reconcile(self):
        from tests import _cards
        from kb._lib import sources as sources_lib
        inventory.set_skip(self.V, [self.entry["id"]], "請款")
        (self.raw / "報價與合約" / "合約範本.txt").write_text("改過了，內容不一樣\n", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.assertEqual(self.by_id()[self.entry["id"]]["skip_kind"], "請款")
        inventory.reconcile(self.V, sources_lib.load(self.V), self.t)
        self.assertEqual(self.by_id()[self.entry["id"]]["skip_kind"], "請款")

    def test_unskip_kinds_returns_ids_and_backs_up(self):
        others = [e["id"] for e in inventory.load(self.V)["entries"] if e["id"] != self.entry["id"]][:1]
        inventory.set_skip(self.V, [self.entry["id"]], "請款")
        inventory.set_skip(self.V, others, "薪資")
        self.assertEqual(inventory.unskip_kinds(self.V, ["請款"], self.t), [self.entry["id"]])
        got = self.by_id()
        self.assertNotIn("skip_kind", got[self.entry["id"]])
        self.assertEqual(got[others[0]]["skip_kind"], "薪資")
        self.assertIn(inventory.INVENTORY_POSIX, {e["path"] for e in turn.entries(self.V, self.t)})

    def test_unskip_nothing_writes_nothing(self):
        p = self.V / ".system" / "inventory.json"
        before = p.stat().st_mtime_ns
        t2 = turn.start(self.V, None, "")["turn"]
        self.assertEqual(inventory.unskip_kinds(self.V, ["請款"], t2), [])
        self.assertEqual(p.stat().st_mtime_ns, before)
        self.assertEqual(turn.entries(self.V, t2), [])


if __name__ == "__main__":
    unittest.main()
