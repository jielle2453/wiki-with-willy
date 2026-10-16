"""kb init 黑箱：subprocess 跑 kb.py init，在 tmp 建總資料夾（specs/2026-10-14-骨架與來源登記.md 第 6 節）。
骨架樹全在、.gitkeep 不複製、檔案內容等於 skeleton/ 的、sources.json 空清單、隱藏屬性、再跑不碰既有檔、補齊、--check、拒絕、--json。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._guard_fixture import KB, ROOT, make_vault

SKELETON = ROOT / "skeleton"
EXPECTED_DIRS = ["AI 產出", "收件匣", "檔案索引", "系統/工作日誌", "系統/健檢報告", ".claude", ".system"]
EXPECTED_FILES = ["CLAUDE.md", ".claude/settings.json", ".system/sources.json"]
SKELETON_TRACKED = {"CLAUDE.md", ".claude/settings.json", "AI 產出/.gitkeep", "收件匣/.gitkeep", "檔案索引/.gitkeep",
                    "系統/工作日誌/.gitkeep", "系統/健檢報告/.gitkeep"}


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Skeleton(unittest.TestCase):
    def test_skeleton_has_exactly_these_files(self):
        files = {p.relative_to(SKELETON).as_posix() for p in SKELETON.rglob("*") if p.is_file()}
        self.assertEqual(files, SKELETON_TRACKED)

    def test_settings_json_is_bare_deny_list(self):
        data = json.loads((SKELETON / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"permissions": {"deny": []}})

    def test_claude_md_mentions_five_zones_and_no_plugin_path(self):
        text = (SKELETON / "CLAUDE.md").read_text(encoding="utf-8")
        for zone in ("型別夾", "AI 產出", "收件匣", "檔案索引", "系統"):
            self.assertIn(zone, text)
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", text)
        self.assertNotIn("scripts/", text)

    def test_claude_md_has_sentinel(self):
        """D-088：沒有「【wiki-with-willy】」開場段就是程式沒跑起來，這個對話不改任何檔。"""
        text = (SKELETON / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("【wiki-with-willy】", text)
        self.assertIn("不改任何檔", text)


class Init(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbinit-")).resolve()
        self.V = self.tmp / "知識庫"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def init(self, *args, cwd=None):
        return run_kb("init", *args, cwd=cwd or self.tmp)

    def test_creates_skeleton_without_gitkeep(self):
        r = self.init(str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(f"已建立知識庫 {self.V}：建了 "), r.stdout)
        self.assertIn(" 項", r.stdout)
        for d in EXPECTED_DIRS:
            self.assertTrue((self.V / d).is_dir(), d)
        for f in EXPECTED_FILES:
            self.assertTrue((self.V / f).is_file(), f)
        self.assertEqual([p for p in self.V.rglob(".gitkeep")], [])
        self.assertEqual((self.V / "CLAUDE.md").read_bytes(), (SKELETON / "CLAUDE.md").read_bytes())
        self.assertEqual((self.V / ".claude" / "settings.json").read_bytes(), (SKELETON / ".claude" / "settings.json").read_bytes())
        self.assertEqual(json.loads((self.V / ".system" / "sources.json").read_text(encoding="utf-8")),
                         {"schema_version": 1, "sources": []})

    def test_hidden_attribute_on_three_items(self):
        if os.name != "nt":
            self.skipTest("隱藏屬性只在 Windows")
        self.init(str(self.V))
        for name in ("CLAUDE.md", ".claude", ".system"):
            with self.subTest(name=name):
                self.assertTrue((self.V / name).stat().st_file_attributes & 0x2, name)
        self.assertFalse((self.V / "AI 產出").stat().st_file_attributes & 0x2)

    def test_rerun_says_nothing_missing_and_keeps_files(self):
        self.init(str(self.V))
        claude = self.V / "CLAUDE.md"
        with open(claude, "r+", encoding="utf-8", newline="\n") as f:  # 隱藏檔用 "w" 開會被 Windows 拒絕
            f.truncate()
            f.write("客戶改過的\n")
        before = claude.stat().st_mtime_ns
        r = self.init(str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"{self.V} 已是知識庫，沒有缺的")
        self.assertEqual(claude.stat().st_mtime_ns, before)
        self.assertEqual(claude.read_text(encoding="utf-8"), "客戶改過的\n")

    def test_fills_one_missing_folder(self):
        self.init(str(self.V))
        shutil.rmtree(self.V / "收件匣")
        r = self.init(str(self.V))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"{self.V} 已是知識庫，補齊了 1 項")
        self.assertTrue((self.V / "收件匣").is_dir())

    def test_check_prints_plan_and_creates_nothing(self):
        r = self.init(str(self.V), "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(ln.startswith("會建 ") for ln in lines), lines)
        self.assertIn("會建 AI 產出/", lines)
        self.assertIn("會建 CLAUDE.md", lines)
        self.assertIn("會建 .system/sources.json", lines)
        self.assertFalse(self.V.exists())
        self.init(str(self.V))
        r = self.init(str(self.V), "--check")
        self.assertTrue(all(ln.startswith("已有 ") for ln in r.stdout.splitlines()), r.stdout)

    def test_target_is_file_exit_3(self):
        f = self.tmp / "a.txt"
        f.write_text("x", encoding="utf-8")
        r = self.init(str(f))
        self.assertEqual(r.returncode, 3)
        self.assertIn("是檔案", r.stdout.splitlines()[0])

    def test_inside_existing_vault_exit_3(self):
        tmp, V, _raw = make_vault()
        try:
            r = self.init(str(V / "AI 產出" / "x"))
            self.assertEqual(r.returncode, 3)
            first = r.stdout.splitlines()[0]
            self.assertIn("上層", first)
            self.assertIn("已經是知識庫", first)
            self.assertFalse((V / "AI 產出" / "x").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_output(self):
        r = run_kb("--json", "init", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["vault"], str(self.V))
        self.assertEqual(sorted(data["created"]), sorted(EXPECTED_DIRS + EXPECTED_FILES + ["系統"]))
        self.assertEqual(data["existing"], [])
        r = run_kb("init", str(self.V), "--json", cwd=self.tmp)
        data = json.loads(r.stdout)
        self.assertEqual(data["created"], [])
        self.assertEqual(len(data["existing"]), len(EXPECTED_DIRS) + len(EXPECTED_FILES) + 1)

    def test_41_check_with_json(self):
        r = run_kb("--json", "init", str(self.V), "--check", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["vault"], str(self.V))
        self.assertEqual(sorted(data["would_create"]), sorted(EXPECTED_DIRS + EXPECTED_FILES + ["系統"]))
        self.assertEqual(data["existing"], [])
        self.assertFalse(self.V.exists())

    def test_relative_path_resolves_against_cwd(self):
        r = self.init("知識庫")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / ".system" / "sources.json").is_file())


class InitJournal(unittest.TestCase):
    """工作日誌程式段（D-085）：init 成功寫一段 kind program；沒有缺的不寫；不開回合；對話裡叫的不寫。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbinit-")).resolve()
        self.V = self.tmp / "知識庫"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        from kb._lib import journal
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m)]

    def test_init_writes_program_line_and_md_without_turns(self):
        r = run_kb("init", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        n = int(r.stdout.split("建了 ")[1].split(" 項")[0])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["kind"], row["command"], row["note"]), ("program", "init", f"建立知識庫：建了 {n} 項"))
        self.assertEqual(len(row["changed"]), n)
        self.assertIn({"path": ".system/sources.json", "action": "create"}, row["changed"])
        self.assertTrue(all(c["action"] == "create" for c in row["changed"]))
        self.assertFalse([c for c in row["changed"]
                          if c["path"].startswith((".system/journal", "系統/工作日誌/"))])  # 日誌自己的檔不算
        from kb._lib import journal
        md = journal.md_path(self.V, journal.month_of(row["turn"])).read_text(encoding="utf-8")
        self.assertIn("　程式 init\n- 做了：建立知識庫：建了", md)
        self.assertFalse((self.V / ".system" / "turns").exists())
        self.assertFalse((self.V / ".system" / "versions").exists())

    def test_rerun_without_missing_writes_nothing_and_fill_writes_one(self):
        run_kb("init", str(self.V), cwd=self.tmp)
        run_kb("init", str(self.V), cwd=self.tmp)
        self.assertEqual(len(self.rows()), 1)
        shutil.rmtree(self.V / "收件匣")
        run_kb("init", str(self.V), cwd=self.tmp)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["note"], "補齊了 1 項")
        self.assertEqual(rows[1]["changed"], [{"path": "收件匣", "action": "create"}])

    def test_check_writes_no_journal(self):
        run_kb("init", str(self.V), "--check", cwd=self.tmp)
        self.assertFalse(self.V.exists())

    def test_json_mode_also_writes(self):
        run_kb("--json", "init", str(self.V), cwd=self.tmp)
        self.assertEqual(len(self.rows()), 1)

    def test_inside_conversation_turn_writes_nothing(self):
        run_kb("init", str(self.V), cwd=self.tmp)
        turns = self.V / ".system" / "turns"
        turns.mkdir()
        (turns / "current.json").write_text(json.dumps({"schema_version": 1, "turn": "20261016-100000-test",
                                                        "session_id": "test-sid", "started": "2026-10-16T10:00:00",
                                                        "prompt_head": "補齊", "approved": False, "note": "",
                                                        "done": False}), encoding="utf-8")
        shutil.rmtree(self.V / "收件匣")
        r = run_kb("init", str(self.V), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
