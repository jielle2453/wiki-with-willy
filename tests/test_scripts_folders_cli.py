"""kb folders add／exclude／remove／include／list 黑箱：用 kb init 建的 tmp vault 加複製的 tests/fixtures/raw/（specs/2026-10-14-骨架與來源登記.md 第 7 節）。
add 成功與回合備份（D-074）、各種拒絕 exit 3、憑證樣式清單、--check、提醒；exclude 成功句與拒絕；
remove、include 成功句、備份、找不到 exit 3（specs/2026-10-16-manage.md 第 1 節）；list 人看與 --json。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests._guard_fixture import FIXTURES, KB, ROOT

SRC_ID = re.compile(r"^src-[0-9a-f]{4}$")


def run_kb(*args, cwd, env_extra=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbfolders-")).resolve()
        self.V = self.tmp / "vault"
        self.raw = self.tmp / "raw"
        shutil.copytree(FIXTURES / "raw", self.raw)
        r = run_kb("init", str(self.V), cwd=self.tmp)
        assert r.returncode == 0, r.stdout + r.stderr

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sources(self):
        return json.loads((self.V / ".system" / "sources.json").read_text(encoding="utf-8"))

    def folders(self, *args, cwd=None, env_extra=None):
        # 測試的 tmp 在 %TEMP%（~/AppData 底下，是系統夾）：家目錄指到 tmp/home，像客戶真的家目錄不在來源裡
        env = {"USERPROFILE": str(self.tmp / "home"), "HOME": str(self.tmp / "home")}
        env.update(env_extra or {})
        return run_kb("folders", *args, cwd=cwd or self.V, env_extra=env)

    def add(self, *args, **kw):
        return self.folders("add", *args, **kw)

    def versions_dirs(self):
        root = self.V / ".system" / "versions"
        return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


class Add(Base):
    def test_registers_relative_spec_and_backs_up(self):
        r = self.add("../raw/客戶案件")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "已登記「客戶案件」（../raw/客戶案件，1 份）")
        data = self.sources()
        self.assertEqual(len(data["sources"]), 1)
        entry = data["sources"][0]
        self.assertRegex(entry["id"], SRC_ID)
        self.assertEqual(entry["alias"], "客戶案件")
        self.assertEqual(entry["spec"], "../raw/客戶案件")
        self.assertEqual(entry["excludes"], [])
        dirs = self.versions_dirs()
        self.assertEqual(len(dirs), 1)
        copy = dirs[0] / "files" / ".system" / "sources.json"
        self.assertTrue(copy.is_file())
        self.assertEqual(json.loads(copy.read_text(encoding="utf-8")), {"schema_version": 1, "sources": []})

    def test_revert_restores_empty_list_then_add_again_works(self):
        self.add("../raw/客戶案件")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已退回", r.stdout)
        self.assertEqual(self.sources()["sources"], [])
        r = self.add("../raw/客戶案件")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.sources()["sources"]), 1)

    def test_guard_allows_new_source_right_away(self):
        self.add("../raw/客戶案件")
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "客戶案件" / "a.txt"), cwd=self.V)
        self.assertEqual(r.stdout.split()[0], "allow", r.stdout)

    def test_json(self):
        r = self.add("../raw/客戶案件", "--json")
        data = json.loads(r.stdout)
        self.assertRegex(data["id"], SRC_ID)
        self.assertEqual((data["alias"], data["spec"], data["files"], data["check"]), ("客戶案件", "../raw/客戶案件", 1, False))
        self.assertEqual(Path(data["path"]), self.raw / "客戶案件")
        self.assertEqual(data["credential_files"], [])
        self.assertIsInstance(data["warnings"], list)

    def test_alias_rules(self):
        """驗收 13、38（M2）：沒登記過的夾配各種別名，理由講別名不是重疊；結尾空白與點去掉；重複不分大小寫。"""
        self.add("../raw/客戶案件")
        r = self.add("../raw/客戶案件")  # 同一夾第二次：先碰到重疊，也是 exit 3
        self.assertEqual(r.returncode, 3)
        self.assertIn("重疊", r.stdout)
        (self.raw / "別名測試").mkdir()
        bad = ("客戶案件", "客戶案件 ", "客戶案件.", "收件匣", "收件匣 ", "收件匣.", "AI 產出", "ai 產出", "知識庫",
               "a/b", "a\\b", "", " ", ".", "..", "a:b", 'a"b', "a<b", "a>b", "a|b", "a?b", "a*b", "a\tb")
        for alias in bad:
            with self.subTest(alias=alias):
                r = self.add("../raw/別名測試", "--alias", alias)
                self.assertEqual(r.returncode, 3, r.stdout)
                first = r.stdout.splitlines()[0]
                self.assertIn("別名", first)
                self.assertNotIn("重疊", first)
        self.assertEqual(len(self.sources()["sources"]), 1)
        r = self.add("../raw/別名測試", "--alias", "另名 ")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.sources()["sources"][-1]["alias"], "另名")
        self.assertIn("「另名」", r.stdout)
        (self.raw / "再一個").mkdir()
        r = self.add("../raw/再一個", "--alias", "Abc")
        self.assertEqual(r.returncode, 0, r.stdout)
        (self.raw / "又一個").mkdir()
        r = self.add("../raw/又一個", "--alias", "abc")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("別名", r.stdout.splitlines()[0])

    def test_40_broken_sources_json_exit_3_without_traceback(self):
        src = self.V / ".system" / "sources.json"
        for text in ('{"sources": {}}', "{bad json", "[]"):
            src.write_text(text, encoding="utf-8")
            for args in (("add", "../raw/客戶案件"), ("exclude", "../raw/行政/薪資", "--kind", "hidden"), ("list",)):
                with self.subTest(text=text, cmd=args[0]):
                    r = self.folders(*args)
                    self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                    self.assertTrue(r.stdout.splitlines()[0].strip())
                    self.assertNotIn("Traceback", r.stdout + r.stderr)
            self.assertEqual(src.read_text(encoding="utf-8"), text)

    def test_50_entry_not_object_exit_3_without_traceback(self):
        src = self.V / ".system" / "sources.json"
        for text in ('{"schema_version":1,"sources":[1]}', '{"schema_version":1,"sources":[{"alias":"x","spec":"../raw/客戶案件","excludes":5}]}',
                     '{"schema_version":1,"sources":[null]}'):
            src.write_text(text, encoding="utf-8")
            for args in (("list",), ("add", "../raw/行政"), ("exclude", "../raw/行政/薪資", "--kind", "hidden")):
                with self.subTest(text=text, cmd=args[0]):
                    r = self.folders(*args)
                    self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                    self.assertTrue(r.stdout.splitlines()[0].strip())
                    self.assertNotIn("Traceback", r.stdout + r.stderr)
            self.assertEqual(src.read_text(encoding="utf-8"), text)

    def test_50_device_names_rejected_as_alias(self):
        (self.raw / "別名測試").mkdir()
        for alias in ("CON", "nul", "COM1", "lpt9", "Aux", "PRN", "con.txt"):
            with self.subTest(alias=alias):
                r = self.add("../raw/別名測試", "--alias", alias)
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertIn("別名", r.stdout.splitlines()[0])
        r = self.add("../raw/別名測試", "--alias", "console")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_50_lock_timeout_is_one_line_exit_3(self):
        lock = self.V / ".system" / "sources.json.lock"
        lock.write_text("busy", encoding="utf-8")
        (self.raw / "另一個").mkdir()
        # 鎖等 30 秒太久：同樣的 main，把鎖的等待時間調成 1 秒
        code = ("import sys; sys.stdout.reconfigure(encoding='utf-8'); sys.path.insert(0, %r); "
                "from kb._lib import io; io.LOCK_TIMEOUT = 1; from kb import folders; folders.CLI_LOCK_TIMEOUT = 1; "
                "from kb.cli import main; sys.exit(main(['folders', 'add', '../raw/另一個']))") % str(ROOT / "scripts")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", cwd=str(self.V),
                           env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), USERPROFILE=str(self.tmp / "home"), HOME=str(self.tmp / "home")))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertTrue(r.stdout.splitlines()[0].strip())
        self.assertEqual(self.sources()["sources"], [])

    def test_44_two_concurrent_adds_both_land(self):
        (self.raw / "甲").mkdir()
        (self.raw / "乙").mkdir()
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), USERPROFILE=str(self.tmp / "home"), HOME=str(self.tmp / "home"))
        procs = [subprocess.Popen([sys.executable, str(KB), "folders", "add", f"../raw/{name}"], cwd=str(self.V), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ("甲", "乙")]
        outs = [p.communicate() for p in procs]
        for p, (out, err) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, out.decode("utf-8", "replace") + err.decode("utf-8", "replace"))
        self.assertEqual(sorted(s["alias"] for s in self.sources()["sources"]), ["乙", "甲"])

    def test_rejections_exit_3_with_reason_on_first_line(self):
        (self.tmp / "檔.txt").write_text("x", encoding="utf-8")
        self.add("../raw/客戶案件")
        self.add("../raw/行政")
        hidden_vault = self.raw / "藏"
        (hidden_vault / "內" / ".system").mkdir(parents=True)
        (hidden_vault / "內" / ".system" / "sources.json").write_text('{"schema_version": 1, "sources": []}', encoding="utf-8")
        other_vault = self.tmp / "vault2"
        assert run_kb("init", str(other_vault), cwd=self.tmp).returncode == 0
        drive_root = os.path.splitdrive(str(self.tmp))[0] + "\\" if os.name == "nt" else "/"
        cases = {
            "不存在": str(self.tmp / "沒有這個"),
            "檔案": str(self.tmp / "檔.txt"),
            "磁碟根": drive_root,
            "系統夾": os.environ.get("SystemRoot") or ("C:\\Windows" if os.name == "nt" else "/usr"),
            "總資料夾": str(self.V),
            "總資料夾的子夾": str(self.V / "AI 產出"),
            "既有來源的父": str(self.raw),
            "既有來源的子": str(self.raw / "行政" / "請款"),
            "另一個知識庫": str(other_vault),
            "另一個知識庫的子夾": str(other_vault / "AI 產出"),
            "底下藏著知識庫": str(hidden_vault),
        }
        before = self.sources()
        for name, path in cases.items():
            with self.subTest(case=name):
                r = self.add(path)
                self.assertEqual(r.returncode, 3, f"{name}: {r.stdout}{r.stderr}")
                self.assertTrue(r.stdout.splitlines()[0].strip(), name)
        self.assertEqual(self.sources(), before)
        r = self.add(str(self.raw / "行政" / "請款"))
        self.assertIn("和既有來源「行政」重疊", r.stdout)
        r = self.add(str(other_vault))
        self.assertIn("另一個知識庫", r.stdout)
        r = self.add(str(hidden_vault))
        self.assertIn("另一個知識庫", r.stdout)

    def test_credential_files_listed(self):
        (self.raw / "行政" / "網站密碼.txt").write_text("x", encoding="utf-8")
        (self.raw / "行政" / ".env").write_text("x", encoding="utf-8")
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("已登記「行政」（../raw/行政，"), lines[0])
        cred = [ln for ln in lines if "其中 2 份檔名像密碼或金鑰，不會讀" in ln]
        self.assertEqual(len(cred), 1, lines)
        self.assertIn("行政/網站密碼.txt", cred[0])
        self.assertIn("行政/.env", cred[0])
        (self.V / ".system" / "sources.json").write_text('{"schema_version": 1, "sources": []}', encoding="utf-8")
        r = self.add("../raw/行政", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(sorted(data["credential_files"]), [".env", "網站密碼.txt"])

    def test_check_writes_nothing(self):
        empty = self.raw / "空夾"
        empty.mkdir()
        r = self.add("../raw/空夾", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("「空夾」", r.stdout)
        self.assertIn("0 份", r.stdout)
        self.assertIn("份數 0", r.stdout)
        self.assertEqual(self.sources()["sources"], [])
        self.assertFalse((self.V / ".system" / "versions").exists())
        r = self.add("../raw/空夾", "--check", "--json")
        self.assertTrue(json.loads(r.stdout)["check"])

    def test_empty_folder_warning_on_real_add(self):
        (self.raw / "空夾").mkdir()
        r = self.add("../raw/空夾")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("份數 0", r.stdout)

    def test_absolute_spec_warning_when_relative_and_home_both_fail(self):
        other_home = Path(tempfile.mkdtemp(prefix="kbhome-")).resolve()
        try:
            (self.V / ".system" / "config.json").write_text('{"source_spec_max_up": 0}', encoding="utf-8")
            env = {"USERPROFILE": str(other_home), "HOME": str(other_home)}
            r = self.add("../raw/客戶案件", env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            spec = self.sources()["sources"][0]["spec"]
            self.assertFalse(spec.startswith(".."), spec)
            self.assertFalse(spec.startswith("~"), spec)
            self.assertIn("換電腦會找不到", r.stdout)
        finally:
            shutil.rmtree(other_home, ignore_errors=True)

    def test_home_spec_when_over_max_up(self):
        (self.V / ".system" / "config.json").write_text('{"source_spec_max_up": 0}', encoding="utf-8")
        env = {"USERPROFILE": str(self.tmp), "HOME": str(self.tmp)}
        r = self.add("../raw/客戶案件", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sources()["sources"][0]["spec"], "~/raw/客戶案件")
        self.assertNotIn("換電腦會找不到", r.stdout)

    def test_whole_home_top_folder_warning(self):
        home = self.tmp / "home"
        (home / "Documents" / "x").mkdir(parents=True)
        env = {"USERPROFILE": str(home), "HOME": str(home)}
        r = self.add(str(home / "Documents"), env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("整個「Documents」資料夾", r.stdout)

    def test_vault_option_from_outside(self):
        r = self.add("raw/客戶案件", "--vault", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sources()["sources"][0]["spec"], "../raw/客戶案件")


class Exclude(Base):
    def setUp(self):
        super().setUp()
        assert self.add("../raw/行政").returncode == 0

    def exclude(self, *args, **kw):
        return self.folders("exclude", *args, **kw)

    def test_hidden_with_reason(self):
        r = self.exclude("../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已排除「行政/薪資」（不想讓 AI 看到）。之前讀過的已送過雲端；這版還沒有卡和備份要清。")
        ex = self.sources()["sources"][0]["excludes"]
        self.assertEqual(ex, [{"spec": "薪資", "kind": "hidden", "reason": "薪資個資", "added": date.today().isoformat()}])
        copies = [d / "files" / ".system" / "sources.json" for d in self.versions_dirs()]
        self.assertTrue(any(c.is_file() for c in copies))

    def test_unused_without_reason(self):
        r = self.exclude("../raw/行政/舊檔", "--kind", "unused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已排除「行政/舊檔」（用不到）。")
        self.assertEqual(self.sources()["sources"][0]["excludes"][0]["reason"], "")

    def test_json(self):
        r = self.exclude("../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資", "--json")
        data = json.loads(r.stdout)
        self.assertRegex(data["source_id"], SRC_ID)
        self.assertEqual((data["alias"], data["spec"], data["kind"], data["reason"]), ("行政", "薪資", "hidden", "薪資個資"))
        self.assertEqual(data["added"], date.today().isoformat())

    def test_file_can_be_excluded(self):
        r = self.exclude("../raw/行政/請款/r.docx", "--kind", "unused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sources()["sources"][0]["excludes"][0]["spec"], "請款/r.docx")

    def test_rejections(self):
        assert self.exclude("../raw/行政/薪資", "--kind", "hidden").returncode == 0
        cases = {
            "不存在": "../raw/行政/沒有",
            "不在來源裡": "../raw/客戶案件",
            "來源根": "../raw/行政",
            "重複": "../raw/行政/薪資",
            "在既有排除底下": "../raw/行政/薪資/s.xlsx",
        }
        for name, path in cases.items():
            with self.subTest(case=name):
                r = self.exclude(path, "--kind", "hidden")
                self.assertEqual(r.returncode, 3, f"{name}: {r.stdout}{r.stderr}")
                self.assertTrue(r.stdout.splitlines()[0].strip())
        self.assertEqual(len(self.sources()["sources"][0]["excludes"]), 1)
        r = self.exclude("../raw/行政/舊檔")
        self.assertEqual(r.returncode, 2)
        r = self.exclude("../raw/行政/舊檔", "--kind", "secret")
        self.assertEqual(r.returncode, 2)

    def test_39_entry_without_id_is_found_by_alias_and_missing_entry_exit_3(self):
        src = self.V / ".system" / "sources.json"
        data = self.sources()
        del data["sources"][0]["id"]
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r = self.exclude("../raw/行政/請款", "--kind", "unused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sources()["sources"][0]["excludes"][0]["spec"], "請款")
        data["sources"] = [{"spec": "../raw/行政", "excludes": []}]  # 沒 id 也沒 alias：找不到就不存檔
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = src.read_text(encoding="utf-8")
        r = self.exclude("../raw/行政/舊檔", "--kind", "unused")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("找不到", r.stdout.splitlines()[0])
        self.assertEqual(src.read_text(encoding="utf-8"), before)

    def test_42_duplicate_message_prints_original_case_path(self):
        self.exclude("../raw/行政/薪資", "--kind", "hidden")
        r = self.exclude("../raw/行政/薪資/s.xlsx", "--kind", "hidden")
        self.assertEqual(r.returncode, 3)
        first = r.stdout.splitlines()[0]
        self.assertIn("已經在排除裡", first)
        self.assertIn((self.raw / "行政" / "薪資").as_posix(), first)
        self.assertNotIn("\\", first)

    def test_guard_denies_after_exclude(self):
        self.exclude("../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資")
        r = run_kb("guard", "decide", "--tool", "Read", "--path", str(self.raw / "行政" / "薪資" / "s.xlsx"), cwd=self.V)
        self.assertTrue(r.stdout.startswith("deny"), r.stdout)
        self.assertIn("你說過不讀", r.stdout)
        self.assertNotIn("密碼或金鑰", r.stdout)

    def test_whole_source_points_to_folders_remove(self):
        r = self.exclude("../raw/行政", "--kind", "unused")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        first = r.stdout.splitlines()[0]
        self.assertIn("folders remove", first)
        self.assertIn("行政", first)
        self.assertNotIn("還沒有", first)


class Remove(Base):
    def setUp(self):
        super().setUp()
        assert self.add("../raw/客戶案件").returncode == 0
        assert self.add("../raw/行政").returncode == 0

    def remove(self, *args, **kw):
        return self.folders("remove", *args, **kw)

    def test_removes_entry_and_backs_up(self):
        before = len(self.versions_dirs())
        r = self.remove("客戶案件")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0],
                         "已拿掉來源「客戶案件」；你的檔案沒動，從現在起不讀它；說「退回上一版」救得回。")
        self.assertEqual([s["alias"] for s in self.sources()["sources"]], ["行政"])
        self.assertTrue((self.raw / "客戶案件" / "a.txt").is_file())
        copies = [d / "files" / ".system" / "sources.json" for d in self.versions_dirs()]
        self.assertTrue(any(c.is_file() for c in copies))
        self.assertGreaterEqual(len(self.versions_dirs()), max(before, 1))

    def test_removes_its_excludes_too(self):
        assert self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden").returncode == 0
        r = self.remove("行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([s["alias"] for s in self.sources()["sources"]], ["客戶案件"])

    def test_unknown_alias_exit_3(self):
        assert self.remove("客戶案件").returncode == 0
        before = (self.V / ".system" / "sources.json").read_text(encoding="utf-8")
        r = self.remove("客戶案件")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "沒有叫「客戶案件」的來源")
        self.assertEqual((self.V / ".system" / "sources.json").read_text(encoding="utf-8"), before)

    def test_json(self):
        r = self.remove("客戶案件", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(set(data), {"id", "alias", "spec"})
        self.assertRegex(data["id"], SRC_ID)
        self.assertEqual((data["alias"], data["spec"]), ("客戶案件", "../raw/客戶案件"))

    def test_revert_brings_it_back(self):
        """確認情境 10：對話裡一句話一個回合，remove 備進那回合，退回就回來。"""
        data = {"session_id": "test-sid", "cwd": str(self.V), "prompt": "客戶案件不要了", "hook_event_name": "UserPromptSubmit"}
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "turn.py")], input=json.dumps(data, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stderr)
        assert self.remove("客戶案件").returncode == 0
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(s["alias"] for s in self.sources()["sources"]), ["客戶案件", "行政"])

    def test_vault_option_from_outside(self):
        r = self.remove("客戶案件", "--vault", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_alias_is_case_insensitive_like_add(self):
        """驗收 16：別名比對跟 add 擋重複一樣不分大小寫；印的與 --json 用登記的原樣。"""
        (self.raw / "Docs").mkdir()
        (self.raw / "Docs" / "d.txt").write_text("d", encoding="utf-8")
        assert self.add("../raw/Docs").returncode == 0
        r = self.remove("docs", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["alias"], "Docs")
        self.assertEqual(sorted(s["alias"] for s in self.sources()["sources"]), ["客戶案件", "行政"])


class Help(unittest.TestCase):
    """驗收 14：kb --help 與 kb folders --help 的 folders 描述句含 remove、include。"""

    def test_folders_description_mentions_remove_and_include(self):
        from kb.cli import COMMANDS
        doc = COMMANDS["folders"][1]
        for word in ("remove", "include"):
            self.assertIn(word, doc)
        env = {"COLUMNS": "400"}  # 不讓 argparse 折行，才能整句比
        for args in (("--help",), ("folders", "--help")):
            with self.subTest(args=args):
                r = run_kb(*args, cwd=ROOT, env_extra=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(doc, r.stdout)


class Include(Base):
    def setUp(self):
        super().setUp()
        assert self.add("../raw/行政").returncode == 0
        assert self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資").returncode == 0
        assert self.folders("exclude", "../raw/行政/舊檔", "--kind", "unused").returncode == 0

    def include(self, *args, **kw):
        return self.folders("include", *args, **kw)

    def excludes(self):
        return [ex["spec"] for ex in self.sources()["sources"][0]["excludes"]]

    def test_removes_exclude_row(self):
        r = self.include("../raw/行政/薪資")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "已取消排除「行政/薪資」，從現在起讀得到。")
        self.assertEqual(self.excludes(), ["舊檔"])
        copies = [d / "files" / ".system" / "sources.json" for d in self.versions_dirs()]
        self.assertTrue(any(c.is_file() for c in copies))

    def test_guard_allows_after_include(self):
        target = str(self.raw / "行政" / "薪資" / "s.xlsx")
        r = run_kb("guard", "decide", "--tool", "Read", "--path", target, cwd=self.V)
        self.assertTrue(r.stdout.startswith("deny"), r.stdout)
        self.include("../raw/行政/薪資")
        r = run_kb("guard", "decide", "--tool", "Read", "--path", target, cwd=self.V)
        self.assertTrue(r.stdout.startswith("allow"), r.stdout)

    def test_not_excluded_exit_3(self):
        before = (self.V / ".system" / "sources.json").read_text(encoding="utf-8")
        r = self.include("../raw/行政/請款")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        first = r.stdout.splitlines()[0]
        self.assertTrue(first.endswith(" 不在排除清單裡"), first)
        self.assertIn((self.raw / "行政" / "請款").as_posix(), first)
        self.assertEqual((self.V / ".system" / "sources.json").read_text(encoding="utf-8"), before)

    def test_inside_an_exclude_is_not_the_exclude(self):
        r = self.include("../raw/行政/薪資/s.xlsx")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.excludes(), ["薪資", "舊檔"])

    def test_missing_folder_can_still_be_included(self):
        shutil.rmtree(self.raw / "行政" / "舊檔")
        r = self.include("../raw/行政/舊檔")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "已取消排除「行政/舊檔」，從現在起讀得到。")
        self.assertEqual(self.excludes(), ["薪資"])

    def test_json(self):
        r = self.include("../raw/行政/薪資", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(set(data), {"source_id", "alias", "spec", "kind"})
        self.assertRegex(data["source_id"], SRC_ID)
        self.assertEqual((data["alias"], data["spec"], data["kind"]), ("行政", "薪資", "hidden"))


class List(Base):
    def list(self, *args, **kw):
        return self.folders("list", *args, **kw)

    def test_empty(self):
        r = self.list()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "還沒有登記任何來源")
        self.assertEqual(json.loads(self.list("--json").stdout), {"sources": []})

    def test_human_format_with_excludes(self):
        self.add("../raw/客戶案件")
        self.add("../raw/行政")
        self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資")
        r = self.list()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("「客戶案件」 ../raw/客戶案件 → "), lines[0])
        shown = lines[0].split(" → ", 1)[1]
        self.assertEqual(Path(shown).resolve(), self.raw / "客戶案件")
        self.assertNotIn("\\", shown)
        self.assertNotIn("找不到", lines[0])
        self.assertTrue(lines[1].startswith("「行政」 ../raw/行政 → "), lines[1])
        self.assertEqual(lines[2], f"  - 排除 薪資（hidden，薪資個資，{date.today().isoformat()}）")

    def test_missing_folder_is_flagged(self):
        self.add("../raw/客戶案件")
        os.rename(self.raw / "客戶案件", self.raw / "改名")
        r = self.list()
        first = r.stdout.splitlines()[0]
        self.assertTrue(first.endswith("（這台電腦找不到）"), r.stdout)
        self.assertIn((self.raw / "客戶案件").as_posix(), first)  # 驗收 42：原始大小寫
        data = json.loads(self.list("--json").stdout)
        self.assertFalse(data["sources"][0]["exists"])
        os.rename(self.raw / "改名", self.raw / "客戶案件")
        self.assertTrue(json.loads(self.list("--json").stdout)["sources"][0]["exists"])

    def test_50_git_bash_spec_is_expanded(self):
        if os.name != "nt":
            self.skipTest("Git Bash 寫法只在 Windows 有意義")
        p = (self.raw / "客戶案件").as_posix()
        spec = "/" + p[0].lower() + p[2:]
        src = self.V / ".system" / "sources.json"
        src.write_text(json.dumps({"schema_version": 1, "sources": [{"id": "src-1234", "alias": "客戶案件", "spec": spec, "excludes": []}]},
                                  ensure_ascii=False), encoding="utf-8")
        r = self.list()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first = r.stdout.splitlines()[0]
        self.assertNotIn("找不到", first)
        self.assertNotIn("?", first)
        self.assertTrue(first.endswith(p), first)
        self.assertTrue(json.loads(self.list("--json").stdout)["sources"][0]["exists"])

    def test_json_shape(self):
        self.add("../raw/行政")
        self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden", "--reason", "薪資個資")
        data = json.loads(self.list("--json").stdout)
        src = data["sources"][0]
        self.assertEqual(set(src), {"id", "alias", "spec", "path", "exists", "excludes"})
        self.assertTrue(src["exists"])
        ex = src["excludes"][0]
        self.assertEqual(set(ex), {"spec", "kind", "reason", "added", "path", "exists"})
        self.assertEqual((ex["spec"], ex["kind"], ex["reason"]), ("薪資", "hidden", "薪資個資"))
        self.assertTrue(ex["exists"])

    def test_not_in_vault_exit_3_and_vault_option(self):
        r = self.list(cwd=self.tmp)
        self.assertEqual(r.returncode, 3)
        self.assertIn("不是知識庫", r.stdout.splitlines()[0])
        r = self.list("--vault", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "還沒有登記任何來源")


class FoldersJournal(Base):
    """工作日誌程式段（D-085）：add、exclude 成功後多一行 kind program，note 等於印出的第一行；
    list、--check 不寫；對話開的回合裡叫的不寫（改動併進那一句）。"""

    def program_rows(self):
        from kb._lib import journal
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m)
                if r["kind"] == "program" and r["command"] != "init"]

    def test_add_and_exclude_write_program_lines(self):
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["command"], rows[0]["note"]), ("folders add", r.stdout.splitlines()[0]))
        self.assertEqual(rows[0]["changed"], [{"path": ".system/sources.json", "action": "modify"}])
        self.assertRegex(rows[0]["turn"], r"^\d{8}-\d{6}-0000$")  # 測試後修正 L2：一律新代號
        r = self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[1]["command"], rows[1]["note"]), ("folders exclude", r.stdout.splitlines()[0]))
        self.assertEqual(rows[1]["changed"], [{"path": ".system/sources.json", "action": "modify"}])

    def test_remove_and_include_write_program_lines(self):
        assert self.add("../raw/客戶案件").returncode == 0
        assert self.add("../raw/行政").returncode == 0
        assert self.folders("exclude", "../raw/行政/薪資", "--kind", "hidden").returncode == 0
        for args, command in ((("remove", "客戶案件"), "folders remove"), (("include", "../raw/行政/薪資"), "folders include")):
            with self.subTest(command=command):
                r = self.folders(*args)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                row = self.program_rows()[-1]
                self.assertEqual((row["command"], row["note"]), (command, r.stdout.splitlines()[0]))
                self.assertEqual(row["changed"], [{"path": ".system/sources.json", "action": "modify"}])
        n = len(self.program_rows())
        self.folders("remove", "沒有這個")
        self.folders("include", "../raw/行政/請款")
        self.assertEqual(len(self.program_rows()), n)

    def test_list_check_and_rejection_write_nothing(self):
        self.folders("list")
        self.add("../raw/行政", "--check")
        self.add("../raw/不存在")
        self.assertEqual(self.program_rows(), [])

    def test_json_mode_also_writes(self):
        r = self.add("../raw/行政", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "已登記「行政」（../raw/行政，3 份）")

    def test_journal_failure_prints_stderr_and_keeps_exit_code(self):
        shutil.rmtree(self.V / ".system" / "journal")
        (self.V / ".system" / "journal").write_text("不是資料夾", encoding="utf-8")
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "已登記「行政」（../raw/行政，3 份）")
        self.assertTrue(r.stderr.startswith("工作日誌沒記到："), r.stderr)
        self.assertEqual(len(r.stderr.splitlines()), 1)

    def write_current(self, done):
        turns = self.V / ".system" / "turns"
        turns.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 1, "turn": "20261016-100000-test", "session_id": "test-sid",
                "started": "2026-10-16T10:00:00", "prompt_head": "加資料夾", "approved": False, "note": "", "done": done}
        (turns / "current.json").write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_inside_conversation_turn_writes_nothing(self):
        self.write_current(done=False)
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.program_rows(), [])

    def test_finished_conversation_turn_writes_program_line_with_new_turn(self):
        """D-085 補（驗收 25 的程式面）：current.json 有 sid 但 done true（對話已答完）→ 寫程式段，turn 是新代號。"""
        cur = self.write_current(done=True)
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["turn"], cur["turn"])
        self.assertRegex(rows[0]["turn"], r"^\d{8}-\d{6}-0000$")

    def test_acceptance_33_old_cli_turn_from_last_month_does_not_pull_line_back(self):
        """測試後修正 L2：current.json 是上個月的 CLI 回合，程式段照樣寫進本月、本月新代號。"""
        turns = self.V / ".system" / "turns"
        turns.mkdir(parents=True, exist_ok=True)
        (turns / "current.json").write_text(json.dumps({"schema_version": 1, "turn": "20260901-120000-0000",
                                                        "session_id": "", "started": "2026-09-01T12:00:00",
                                                        "prompt_head": "", "approved": False, "note": "",
                                                        "done": False}), encoding="utf-8")
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        from kb._lib import journal
        month = date.today().strftime("%Y-%m")
        rows = [x for x in journal.read_lines(self.V, month) if x["kind"] == "program" and x["command"] == "folders add"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["turn"].startswith(date.today().strftime("%Y%m%d")), rows[0]["turn"])
        self.assertNotEqual(rows[0]["turn"], "20260901-120000-0000")

    def test_acceptance_25_after_stop_folders_add_writes_program_line(self):
        """驗收 25：UserPromptSubmit、Stop（回合 done），再在終端機跑 folders add → 多一行 program，turn 不是那回合。"""
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        for hook, data in (("turn.py", {"session_id": "test-sid", "cwd": str(self.V), "prompt": "看一下",
                                        "hook_event_name": "UserPromptSubmit"}),
                           ("stop.py", {"session_id": "test-sid", "cwd": str(self.V), "hook_event_name": "Stop",
                                        "stop_hook_active": False, "last_assistant_message": "好了"})):
            r = subprocess.run([sys.executable, str(ROOT / "hooks" / hook)], input=json.dumps(data, ensure_ascii=False),
                               capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        tid = json.loads((self.V / ".system" / "turns" / "test-sid.json").read_text(encoding="utf-8"))["turn"]
        r = self.add("../raw/行政")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.program_rows()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["turn"], tid)


if __name__ == "__main__":
    unittest.main()
