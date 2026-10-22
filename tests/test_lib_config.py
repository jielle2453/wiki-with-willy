"""_lib/config：讀 config/defaults.json，客戶端 .system/config.json 同鍵覆寫，未知鍵 KeyError。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kb._lib import config  # noqa: E402


class Get(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbcfg-"))
        self.vault = self.tmp / "vault"
        (self.vault / ".system").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_value_from_defaults_json(self):
        self.assertEqual(config.get(self.vault, "big_change_synthesis_max"), 10)

    def test_defaults_json_has_why_for_each_key(self):
        data = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))
        for key in data:
            if key.startswith("_why_"):
                continue
            with self.subTest(key=key):
                self.assertIn(f"_why_{key}", data)

    def test_journal_keys_have_defaults_and_why(self):
        """工作日誌：prompt_head_max 200（B-028）、journal_result_max 300（D-061），各有 _why。"""
        data = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get(self.vault, "prompt_head_max"), 200)
        self.assertEqual(config.get(self.vault, "journal_result_max"), 300)
        for key in ("prompt_head_max", "journal_result_max"):
            self.assertTrue(data.get(f"_why_{key}"), key)

    def test_inventory_keys_have_defaults_and_why(self):
        """清冊：單檔指紋上限 200 MB、快速比對讀取預算 200 MB、快速比對 5 秒（D-094、I-081）。"""
        data = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get(self.vault, "fingerprint_max_bytes"), 209715200)
        self.assertEqual(config.get(self.vault, "changes_quick_hash_budget_bytes"), 209715200)
        self.assertEqual(config.get(self.vault, "changes_quick_seconds"), 5)
        for key in ("fingerprint_max_bytes", "changes_quick_hash_budget_bytes", "changes_quick_seconds"):
            self.assertTrue(data.get(f"_why_{key}"), key)

    def test_relocate_sample_max(self):
        """manage 第二版第 9 點：folders relocate 抽樣 40 份，帶 _why。"""
        data = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))
        self.assertEqual(config.get(self.vault, "relocate_sample_max"), 40)
        self.assertTrue(data.get("_why_relocate_sample_max"))

    def test_client_override_wins(self):
        (self.vault / ".system" / "config.json").write_text(
            json.dumps({"schema_version": 1, "big_change_synthesis_max": 3}), encoding="utf-8")
        self.assertEqual(config.get(self.vault, "big_change_synthesis_max"), 3)

    def test_unknown_key_raises_key_error(self):
        with self.assertRaises(KeyError):
            config.get(self.vault, "no_such_key")

    def test_wrong_type_in_client_file_uses_default_and_logs(self):
        """第一輪 L5：型別跟預設不同就用預設，寫一行 hook-errors.log。"""
        (self.vault / ".system" / "config.json").write_text(
            json.dumps({"big_change_synthesis_max": "十"}), encoding="utf-8")
        self.assertEqual(config.get(self.vault, "big_change_synthesis_max"), 10)
        log = (self.vault / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("big_change_synthesis_max", log[0])

    def test_whole_file_not_an_object_uses_default_and_logs(self):
        """第二輪 L5 補（驗收 34）：config.json 整份是 5 → 用預設、寫一行 log。"""
        (self.vault / ".system" / "config.json").write_text("5", encoding="utf-8")
        self.assertEqual(config.get(self.vault, "big_change_synthesis_max"), 10)
        log = (self.vault / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("config.json", log[0])

    def test_bool_is_not_accepted_as_int(self):
        (self.vault / ".system" / "config.json").write_text(
            json.dumps({"big_change_synthesis_max": True}), encoding="utf-8")
        self.assertEqual(config.get(self.vault, "big_change_synthesis_max"), 10)

    def test_client_file_cannot_add_unknown_key(self):
        (self.vault / ".system" / "config.json").write_text(json.dumps({"mystery": 1}), encoding="utf-8")
        with self.assertRaises(KeyError):
            config.get(self.vault, "mystery")


if __name__ == "__main__":
    unittest.main()
