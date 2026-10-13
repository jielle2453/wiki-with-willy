"""_lib/inventory：清冊最小版（D-076）。新建、同內容不重登、改內容追加指紋、outputs_of_turn、set_status、並行登記（I-080）。"""
import json
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import SCRIPTS, make_vault
from kb._lib import inventory  # noqa: E402

TURN = "20261013-153012-test"


class Register(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.rel = "AI 產出/x.md"
        (self.V / self.rel).write_text("第一版\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty_schema(self):
        self.assertEqual(inventory.load(self.V), {"schema_version": 1, "entries": []})

    def test_new_entry_shape(self):
        e = inventory.register_output(self.V, self.rel, TURN)
        data = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["entries"]), 1)
        got = data["entries"][0]
        self.assertEqual(got, e)
        self.assertRegex(got["id"], r"^out-[0-9a-f]{8}$")
        self.assertEqual(got["origin"], "ai_output")
        self.assertEqual(got["locations"], ["file://AI 產出/x.md"])
        self.assertEqual(got["created_turn"], TURN)
        self.assertEqual(got["status"], "active")
        self.assertEqual(len(got["fingerprints"]), 1)
        fp = got["fingerprints"][0]
        self.assertEqual(set(fp), {"sha256", "time", "turn"})
        self.assertEqual(fp["turn"], TURN)
        import hashlib
        self.assertEqual(fp["sha256"], hashlib.sha256((self.V / self.rel).read_bytes()).hexdigest())

    def test_same_content_does_not_add(self):
        inventory.register_output(self.V, self.rel, TURN)
        inventory.register_output(self.V, self.rel, TURN)
        data = inventory.load(self.V)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(len(data["entries"][0]["fingerprints"]), 1)

    def test_changed_content_appends_fingerprint(self):
        inventory.register_output(self.V, self.rel, TURN)
        (self.V / self.rel).write_text("第二版\n", encoding="utf-8")
        inventory.register_output(self.V, self.rel, "20261013-160000-test")
        data = inventory.load(self.V)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(len(data["entries"][0]["fingerprints"]), 2)
        self.assertEqual(data["entries"][0]["fingerprints"][1]["turn"], "20261013-160000-test")
        self.assertEqual(data["entries"][0]["created_turn"], TURN)

    def test_outputs_of_turn_only_active_of_that_turn(self):
        (self.V / "AI 產出" / "y.md").write_text("y", encoding="utf-8")
        a = inventory.register_output(self.V, self.rel, TURN)
        b = inventory.register_output(self.V, "AI 產出/y.md", "other")
        self.assertEqual([e["id"] for e in inventory.outputs_of_turn(self.V, TURN)], [a["id"]])
        inventory.set_status(self.V, [a["id"]], "turn_reverted")
        self.assertEqual(inventory.outputs_of_turn(self.V, TURN), [])
        self.assertEqual(inventory.load(self.V)["entries"][0]["status"], "turn_reverted")
        self.assertEqual(inventory.load(self.V)["entries"][1]["status"], "active")
        self.assertEqual(b["status"], "active")

    def test_two_processes_register_concurrently_both_kept(self):
        (self.V / "AI 產出" / "y.md").write_text("y", encoding="utf-8")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from kb._lib import inventory; "
                "inventory.register_output(sys.argv[2], sys.argv[3], 't')")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.V), rel],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for rel in (self.rel, "AI 產出/y.md")]
        for p in procs:
            _out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err.decode("utf-8", "replace"))
        data = inventory.load(self.V)
        self.assertEqual(sorted(e["locations"][0] for e in data["entries"]), ["file://AI 產出/x.md", "file://AI 產出/y.md"])


if __name__ == "__main__":
    unittest.main()
