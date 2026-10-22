"""kb turn current／approve／note／redact 黑箱：subprocess 跑 kb.py，cwd 在 vault。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import KB, ROOT, make_vault
from kb._lib import journal, turn  # noqa: E402

SID = "test-sid"


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Current(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prints_turn_prompt_count_and_limit(self):
        t = turn.start(self.V, SID, "改一下")
        turn.backup(self.V, "案子/信義王/信義王.md", t["turn"])
        r = run_kb("turn", "current", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"{t['turn']}｜問：「改一下」｜已備份 1 檔｜未解除上限")

    def test_json_has_four_keys(self):
        t = turn.start(self.V, SID, "改一下")
        r = run_kb("turn", "current", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(set(data), {"turn", "prompt_head", "backed_up", "approved"})
        self.assertEqual(data["turn"], t["turn"])
        self.assertEqual(data["backed_up"], 0)
        self.assertFalse(data["approved"])

    def test_no_turn_yet_starts_one(self):
        r = run_kb("turn", "current", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / ".system" / "turns" / "current.json").exists())
        self.assertIn("已備份 0 檔", r.stdout)

    def test_outside_vault_exits_2(self):
        r = run_kb("turn", "current", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不在知識庫", r.stdout + r.stderr)


class Approve(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approve_sets_flag_and_prints(self):
        turn.start(self.V, SID, "全部加一行")
        r = run_kb("turn", "approve", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "這一題解除綜合卡張數上限")
        self.assertTrue(turn.current(self.V, SID)["approved"])
        self.assertTrue(turn.current(self.V)["approved"])
        r = run_kb("turn", "current", cwd=self.V)
        self.assertIn("已解除上限", r.stdout)

    def test_approve_json(self):
        t = turn.start(self.V, SID, "全部加一行")
        r = run_kb("turn", "approve", "--json", cwd=self.V)
        data = json.loads(r.stdout)
        self.assertEqual(data, {"turn": t["turn"], "approved": True})


class JournalBase(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.t = turn.start(self.V, SID, "改一下")
        journal.begin_turn(self.V, None, self.t)
        self.month = journal.month_of(self.t["turn"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self):
        return [r for r in journal.read_lines(self.V, self.month) if r["turn"] == self.t["turn"]][0]

    def sess(self):
        return json.loads((self.V / ".system" / "turns" / f"{SID}.json").read_text(encoding="utf-8"))

    def md(self):
        return journal.md_path(self.V, self.month).read_text(encoding="utf-8")

    def stop(self):
        data = turn.current(self.V, SID)
        journal.complete_turn(self.V, data, "好了", data["note"] or "沒有改動", "ai" if data["note"] else "program", [], False)
        turn.set_done(self.V, SID)


class Note(JournalBase):
    def test_note_writes_turn_file_and_journal(self):
        r = run_kb("turn", "note", "把現況改成已完工", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "記下了"), r.stderr)
        self.assertEqual(self.sess()["note"], "把現況改成已完工")
        self.assertEqual((self.row()["note"], self.row()["note_by"]), ("把現況改成已完工", "ai"))
        self.stop()
        self.assertEqual((self.row()["note"], self.row()["note_by"]), ("把現況改成已完工", "ai"))

    def test_note_after_stop_still_rewrites_journal_and_md(self):
        self.stop()
        self.assertIn("- 做了：沒有改動", self.md())
        r = run_kb("turn", "note", "改口", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.row()["note"], self.row()["note_by"]), ("改口", "ai"))
        self.assertIn("- 做了：改口", self.md())

    def test_note_is_masked(self):
        run_kb("turn", "note", "用密碼 abc123 登入了", cwd=self.V)
        self.assertEqual(self.sess()["note"], "用密碼 ███ 登入了")
        self.assertEqual(self.row()["note"], "用密碼 ███ 登入了")

    def test_acceptance_37_note_value_after_blank_line_is_masked(self):
        """驗收 37（M2「一律」）：turn note 的句子也先壓空白再遮，回合檔與 jsonl 都是「密碼如下： ███」（D-187 只遮值）。"""
        r = run_kb("turn", "note", "密碼如下：\n\nabc123", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sess()["note"], "密碼如下： ███")
        self.assertEqual(self.row()["note"], "密碼如下： ███")

    def test_note_outside_vault_exits_2(self):
        r = run_kb("turn", "note", "x", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)


class Redact(JournalBase):
    def setUp(self):
        super().setUp()
        turn.backup(self.V, "案子/信義王/信義王.md", self.t["turn"])  # versions/<回合>/turn.json 存在
        self.meta = self.V / ".system" / "versions" / self.t["turn"] / "turn.json"

    def three(self):
        return (self.sess()["prompt_head"], json.loads(self.meta.read_text(encoding="utf-8"))["prompt_head"],
                self.row()["prompt"])

    def test_redact_rewrites_three_places_and_md(self):
        r = run_kb("turn", "redact", "幫我登入 ERP，已遮", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "這一句的問已改成：「幫我登入 ERP，已遮」"), r.stderr)
        self.assertEqual(self.three(), ("幫我登入 ERP，已遮",) * 3)
        self.assertIn("- 問：幫我登入 ERP，已遮", self.md())
        self.assertNotIn("- 問：改一下", self.md())

    def test_redact_value_is_masked_again(self):
        r = run_kb("turn", "redact", "登入 ERP 帳號 x", cwd=self.V)
        self.assertEqual(r.stdout.strip(), "這一句的問已改成：「登入 ERP 帳號 ███」")
        self.assertEqual(self.three(), ("登入 ERP 帳號 ███",) * 3)

    def test_redact_without_versions_dir_does_not_create_it(self):
        shutil.rmtree(self.V / ".system" / "versions")
        r = run_kb("turn", "redact", "已遮", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.meta.exists())
        self.assertEqual(self.row()["prompt"], "已遮")

    def test_redact_cut_at_prompt_head_max(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"prompt_head_max": 5}), encoding="utf-8")
        run_kb("turn", "redact", "一二三四五六七", cwd=self.V)
        self.assertEqual(self.three(), ("一二三四五",) * 3)


class LockTimeout(JournalBase):
    """測試後修正 M4（驗收 31 的程式面）：CLI 等鎖 CLI_LOCK_TIMEOUT 秒，逾時印中文一句 exit 3、什麼都沒寫。
    在程序內跑、把等待縮短，不用真的等 30 秒。"""

    def setUp(self):
        super().setUp()
        turn.backup(self.V, "案子/信義王/信義王.md", self.t["turn"])
        self.meta = self.V / ".system" / "versions" / self.t["turn"] / "turn.json"
        p = journal.jsonl_path(self.V, self.month)
        self.lock = p.with_name(p.name + ".lock")
        self.lock.write_text("", encoding="utf-8")

    def run_in_process(self, *argv):
        import contextlib
        import io
        from unittest import mock
        from kb import cli, turn_cli
        from kb._lib import io as kio
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.V)
        try:
            with mock.patch.object(turn_cli, "CLI_LOCK_TIMEOUT", 0.5), \
                    mock.patch.object(kio, "LOCK_TIMEOUT", kio.LOCK_TIMEOUT), contextlib.redirect_stdout(out):
                code = cli.main(list(argv))
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_cli_lock_timeout_is_30_seconds(self):
        from kb import turn_cli
        self.assertEqual(turn_cli.CLI_LOCK_TIMEOUT, 30)

    def test_note_lock_timeout_exit_3_with_chinese_line(self):
        code, out = self.run_in_process("turn", "note", "x")
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), "等不到工作日誌的鎖（別的指令正在寫），沒有寫入；請稍後再試")
        self.assertEqual(self.sess()["note"], "")

    def test_redact_lock_timeout_leaves_everything(self):
        before = (self.sess(), self.meta.read_bytes())
        cur = (self.V / ".system" / "turns" / "current.json").read_bytes()
        code, out = self.run_in_process("turn", "redact", "y")
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), "等不到工作日誌的鎖（別的指令正在寫），沒有寫入；請稍後再試")
        self.assertEqual((self.sess(), self.meta.read_bytes()), before)
        self.assertEqual((self.V / ".system" / "turns" / "current.json").read_bytes(), cur)
        self.assertEqual(self.row()["prompt"], "改一下")


if __name__ == "__main__":
    unittest.main()
