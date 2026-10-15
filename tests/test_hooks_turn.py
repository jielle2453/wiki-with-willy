"""hooks/turn.py 黑箱：subprocess 餵 UserPromptSubmit payload。開回合、task-notification 不開、非使用者來源不開、
不在知識庫不開、壞 JSON exit 0 無輸出。"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from datetime import date

from tests._guard_fixture import ROOT, make_vault
from kb._lib import journal  # noqa: E402

HOOK = ROOT / "hooks" / "turn.py"
SID = "test-sid"


def run_hook(data, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def prompt_payload(cwd, prompt, **extra):
    data = {"session_id": SID, "cwd": str(cwd), "prompt": prompt, "hook_event_name": "UserPromptSubmit"}
    data.update(extra)
    return data


class TurnHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.turns = self.V / ".system" / "turns"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_opens_turn_silently(self):
        r = run_hook(prompt_payload(self.V, "改一下"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        cur = json.loads((self.turns / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["turn"], cur["turn"])
        self.assertEqual(sess["prompt_head"], "改一下")
        self.assertFalse(sess["approved"])

    def test_61_63_prompt_head_is_masked_everywhere(self):
        """D-081、B-027：帳密不進回合檔；versions list 印的問也是遮過的。"""
        r = run_hook(prompt_payload(self.V, "幫我登入 ERP，帳號 jie 密碼 abc123"))
        self.assertEqual(r.returncode, 0, r.stderr)
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["prompt_head"], "幫我登入 ERP，帳號███")  # 第六輪 W3：遮到子句尾
        self.assertNotIn("jie", sess["prompt_head"])
        from kb._lib import turn
        turn.backup(self.V, "案子/信義王/信義王.md", sess["turn"])
        kb = ROOT / "scripts" / "kb.py"
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        out = subprocess.run([sys.executable, str(kb), "versions", "list"], capture_output=True, text=True, encoding="utf-8",
                             cwd=str(self.V), env=env).stdout
        self.assertIn("███", out)
        self.assertNotIn("abc123", out)
        for p in (self.V / ".system").rglob("*"):
            if p.is_file():
                self.assertNotIn("abc123", p.read_text(encoding="utf-8", errors="ignore"), p)

    def test_task_notification_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "<task-notification>done</task-notification>"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_cross_session_message_does_not_open(self):
        """確認後裁定 L-3：跨 session 訊息不是客戶的話，不開回合（標頭不能變成 prompt_head 印給客戶）。"""
        r = run_hook(prompt_payload(self.V, '<cross-session-message from="uds:x" from-name="llm-wiki-j-51">改一下</cross-session-message>'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_system_reminder_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "  <system-reminder>\n內部提醒\n</system-reminder>"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())

    def test_non_user_source_does_not_open(self):
        r = run_hook(prompt_payload(self.V, "改一下", source="scheduled"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.turns.exists())
        for ok in ("user", "sdk"):
            r = run_hook(prompt_payload(self.V, "改一下", source=ok))
            self.assertTrue((self.turns / f"{SID}.json").exists(), ok)

    def test_outside_vault_does_nothing(self):
        r = run_hook(prompt_payload(self.tmp, "改一下"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.turns.exists())
        self.assertFalse((self.tmp / ".system").exists())

    def test_bad_json_is_silent_exit_0(self):
        r = run_hook("not json", cwd=self.V)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "turn")  # 第一輪 L7：第二欄是哪支 hook

    def test_bad_json_outside_vault_is_silent_exit_0(self):
        r = run_hook("[1, 2]")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_registered_in_hooks_json_as_user_prompt_submit(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["UserPromptSubmit"]
        cmds = [h["command"] for g in groups for h in g["hooks"]]
        self.assertEqual(len(cmds), 1, cmds)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/turn.py", cmds[0])
        self.assertEqual(groups[0]["hooks"][0]["timeout"], 5)
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)  # PreToolUse 不動

    def journal_rows(self):
        return journal.read_lines(self.V, date.today().strftime("%Y-%m"))

    def md(self):
        return journal.md_path(self.V, date.today().strftime("%Y-%m")).read_text(encoding="utf-8")

    def errors(self):
        p = self.V / ".system" / "hook-errors.log"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def test_journal_line_opened_with_in_progress_md(self):
        """工作日誌（D-083）：開口就寫一行 done false；md 頂端三行、那段結果「（還沒結束）」。"""
        r = run_hook(prompt_payload(self.V, "改一下"))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["prompt"], rows[0]["done"]), ("turn", "改一下", False))
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual((sess["note"], sess["done"]), ("", False))
        self.assertEqual(rows[0]["turn"], sess["turn"])
        md = self.md().splitlines()
        self.assertEqual(md[2:5], list(journal.HEADER_LINES))
        self.assertIn("- 結果：（還沒結束）", md)

    def test_journal_prompt_is_masked(self):
        run_hook(prompt_payload(self.V, "幫我登入 ERP，帳號 jie 密碼 abc123"))
        self.assertEqual(self.journal_rows()[0]["prompt"], "幫我登入 ERP，帳號███")

    def test_previous_turn_without_stop_is_flushed_as_interrupted(self):
        run_hook(prompt_payload(self.V, "第三句"))
        run_hook(prompt_payload(self.V, "第四句"))
        rows = {r["prompt"]: r for r in self.journal_rows()}
        self.assertEqual(len(rows), 2)
        third = rows["第三句"]
        self.assertTrue(third["done"])
        self.assertTrue(third["interrupted"])
        self.assertEqual(third["result"], "（回答被中斷，沒有結果）")
        self.assertEqual((third["note"], third["note_by"], third["changed"]), ("沒有改動", "program", []))
        self.assertFalse(rows["第四句"]["done"])
        md = self.md()
        self.assertLess(md.index("第四句"), md.index("第三句"))  # 最新在上

    def test_acceptance_26_flush_keeps_ai_note(self):
        """驗收 26（D-084）：中斷前模型叫過 turn note，補記的做了用它、note_by ai。"""
        run_hook(prompt_payload(self.V, "第一句"))
        kb = ROOT / "scripts" / "kb.py"
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(kb), "turn", "note", "中斷前記的"], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(self.V), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        run_hook(prompt_payload(self.V, "第二句"))
        first = {r["prompt"]: r for r in self.journal_rows()}["第一句"]
        self.assertEqual((first["note"], first["note_by"], first["interrupted"]), ("中斷前記的", "ai", True))

    def test_old_turn_file_without_done_is_not_flushed(self):
        """疑義裁定 3：升級前的回合檔沒有 done 鍵，不補記。"""
        self.turns.mkdir(parents=True)
        old = {"schema_version": 1, "turn": "20261014-100000-test", "session_id": SID,
               "started": "2026-10-14T10:00:00", "prompt_head": "舊的", "approved": False}
        (self.turns / f"{SID}.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        run_hook(prompt_payload(self.V, "新的"))
        self.assertNotIn(old["turn"], [r["turn"] for r in journal.read_lines(self.V, "2026-10")])
        self.assertEqual([r["prompt"] for r in self.journal_rows()], ["新的"])

    def test_prompt_head_length_reads_config(self):
        """B-028 第 2 項：150 字整句留著；客戶端 prompt_head_max 10 就截 10。"""
        run_hook(prompt_payload(self.V, "長" * 150))
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["prompt_head"], "長" * 150)
        self.assertEqual(self.journal_rows()[0]["prompt"], "長" * 150)
        (self.V / ".system" / "config.json").write_text(json.dumps({"prompt_head_max": 10}), encoding="utf-8")
        run_hook(prompt_payload(self.V, "短" * 50))
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["prompt_head"], "短" * 10)

    def test_bad_journal_line_is_kept_and_logged(self):
        run_hook(prompt_payload(self.V, "第一句"))
        p = journal.jsonl_path(self.V, date.today().strftime("%Y-%m"))
        p.write_text(p.read_text(encoding="utf-8") + "{bad\n", encoding="utf-8")
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        sess["done"] = True  # 前一句已結束，只看開口那一次讀檔
        (self.turns / f"{SID}.json").write_text(json.dumps(sess, ensure_ascii=False), encoding="utf-8")
        r = run_hook(prompt_payload(self.V, "第二句"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("{bad", p.read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(self.errors()), 1)
        self.assertEqual([r["prompt"] for r in self.journal_rows()], ["第一句", "第二句"])
        self.assertIn("第二句", self.md())

    def test_held_lock_still_opens_turn_and_logs(self):
        """驗收 18：鎖被占著（mtime 現在）回合照開、日誌沒新行、log 一行、4 秒內結束；鎖殘留 60 秒前就清掉照寫。"""
        p = journal.jsonl_path(self.V, date.today().strftime("%Y-%m"))
        p.parent.mkdir(parents=True)
        lock = p.with_name(p.name + ".lock")
        lock.write_text("", encoding="utf-8")
        t0 = time.time()
        r = run_hook(prompt_payload(self.V, "鎖住了"))
        elapsed = time.time() - t0
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertLess(elapsed, 4.5)
        self.assertTrue((self.turns / f"{SID}.json").exists())
        self.assertEqual(self.journal_rows(), [])
        self.assertEqual(len(self.errors()), 1)
        old = time.time() - 60
        os.utime(lock, (old, old))
        run_hook(prompt_payload(self.V, "鎖舊了"))
        self.assertIn("鎖舊了", [r["prompt"] for r in self.journal_rows()])

    def test_flush_lock_timeout_skips_open_turn(self):
        """疑義裁定 2：一支 hook 最多等一次鎖；flush 逾時就跳過 open_turn，回合照開。"""
        run_hook(prompt_payload(self.V, "第一句"))
        p = journal.jsonl_path(self.V, date.today().strftime("%Y-%m"))
        lock = p.with_name(p.name + ".lock")
        lock.write_text("", encoding="utf-8")
        t0 = time.time()
        r = run_hook(prompt_payload(self.V, "第二句"))
        self.assertLess(time.time() - t0, 4.5)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        sess = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertEqual(sess["prompt_head"], "第二句")
        self.assertEqual(len(self.errors()), 1)
        lock.unlink()

    def test_second_prompt_opens_new_turn(self):
        run_hook(prompt_payload(self.V, "第一句"))
        first = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))["turn"]
        run_hook(prompt_payload(self.V, "第二句"))
        second = json.loads((self.turns / f"{SID}.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first, second["turn"])
        self.assertEqual(second["prompt_head"], "第二句")


if __name__ == "__main__":
    unittest.main()
