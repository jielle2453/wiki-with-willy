"""kb unlock（密碼與守門第 2 點；D-180、D-181；驗收 6、16）與 .system/vault.json：
- _lib/unlock 用記憶體替身驗邏輯（同名換掉、換種類、forget 連種類一起拿掉、authorize、revoke、opened）；
- Windows 上真的寫讀刪一筆（名稱放在這次測試的隨機知識庫編號底下，測完全部刪掉，不碰使用者自己的憑證）；不是 Windows skip；
- kb unlock 黑箱：成功句與拒絕句一字不差、人讀不印編號、對話裡 get 記 secret_shown、全程知識庫找不到密碼；
- vault.json：補編號的那一句退回之後編號照留、get 拿得到。"""
import json
import os
import re
import shutil
import sys
import unittest

from tests import _pw
from tests._guard_fixture import ROOT
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import turn, unlock  # noqa: E402
from kb._lib import vault as vault_lib  # noqa: E402

ID = r"[aefsrcp]-[0-9a-f]{8}"


class Mem(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        self.old = unlock.BACKEND
        unlock.BACKEND = unlock.MemoryBackend()
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        unlock.BACKEND = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_get_replace_forget(self):
        self.assertFalse(unlock.save(self.V, "記下的密碼", "工地大門", _pw.GATE_PW))
        self.assertEqual(unlock.get(self.V, "工地大門"), ("記下的密碼", _pw.GATE_PW))
        self.assertTrue(unlock.save(self.V, "記下的密碼", "工地大門", _pw.GATE_PW + "x"))
        self.assertEqual(unlock.get(self.V, "工地大門")[1], _pw.GATE_PW + "x")
        self.assertTrue(unlock.save(self.V, "PDF 密碼", "工地大門", _pw.GATE_PW))
        self.assertEqual(unlock.entries(self.V), [("PDF 密碼", "工地大門")])
        vid = vault_lib.vault_id(self.V)
        self.assertRegex(vid, r"^kb-[0-9a-f]{8}$")
        self.assertEqual(list(unlock.BACKEND.store), [f"wiki-with-willy/{vid}/PDF 密碼/工地大門"])
        self.assertEqual(unlock.forget(self.V, "工地大門", self.t), [])
        with self.assertRaises(unlock.Refused) as cm:
            unlock.get(self.V, "工地大門")
        self.assertEqual(str(cm.exception), "這台電腦沒有記「工地大門」")
        self.assertEqual(_pw.leaks(self.V), [])

    def test_refusals(self):
        for args, want in ((("記下的", "a", "1"), "種類要是 記下的密碼、PDF 密碼 之一"),
                           (("記下的密碼", "a/b", "1"), "名稱不能空白、最多 60 字，也不能有 / \\ \" $ ` 這幾個字元"),
                           (("記下的密碼", " ", "1"), "名稱不能空白、最多 60 字，也不能有 / \\ \" $ ` 這幾個字元"),
                           (("記下的密碼", "a", "  "), "密碼不能空白、最多 500 字")):
            with self.subTest(want=want), self.assertRaises(unlock.Refused) as cm:
                unlock.save(self.V, *args)
            self.assertEqual(str(cm.exception), want)

    def test_authorize_revoke_and_forget_takes_kinds(self):
        from tests import _cards
        wang = _cards.entry_of(self.V, _pw.WANG)["id"]
        unlock.save(self.V, "PDF 密碼", "保險單", _pw.PDF_PW)
        item = unlock.authorize(self.V, "保險單", "保險單", [wang], self.t)
        self.assertEqual((item["credential"], item["examples"]), ("PDF 密碼/保險單", [wang]))
        self.assertEqual(unlock.kind_for(self.V, "保險單")["label"], "保險單")
        data = json.loads((self.V / ".system" / "unlock.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(_pw.leaks(self.V), [])
        unlock.record_opened(self.V, wang, "保險單", self.t)
        unlock.record_opened(self.V, wang, "保險單", self.t)
        self.assertEqual(len(unlock.opened_in_turn(self.V, self.t)), 1)
        self.assertEqual(unlock.forget(self.V, "保險單", self.t), ["保險單"])
        self.assertIsNone(unlock.kind_for(self.V, "保險單"))
        unlock.save(self.V, "記下的密碼", "門", "x1")
        with self.assertRaises(unlock.Refused) as cm:
            unlock.authorize(self.V, "門", "門禁", [], self.t)
        self.assertEqual(str(cm.exception), "「門」不是 PDF 密碼")
        with self.assertRaises(unlock.Refused) as cm:
            unlock.revoke(self.V, "沒有的", self.t)
        self.assertEqual(str(cm.exception), "沒有「沒有的」這一種")

    def test_not_windows(self):
        unlock.BACKEND = None
        real = sys.platform
        try:
            sys.platform = "linux"
            with self.assertRaises(unlock.Refused) as cm:
                unlock.save(self.V, "記下的密碼", "a", "1")
            self.assertEqual(str(cm.exception), "這台電腦不是 Windows，存不了密碼")
        finally:
            sys.platform = real
            unlock.BACKEND = None


def wipe(vault):
    """刪掉這個測試知識庫編號底下所有憑證（真的憑證管理員）。"""
    vid = vault_lib.vault_id(vault)
    if not vid or sys.platform != "win32":
        return
    cred = unlock._WinCred()
    for target in cred.targets(f"wiki-with-willy/{vid}/"):
        cred.delete(target)


@unittest.skipUnless(sys.platform == "win32", "Windows 憑證管理員")
class RealCredentialManager(unittest.TestCase):
    """驗收 6：真的寫進憑證管理員（測試用的名字），讀得到、列得到、刪得掉；全程知識庫找不到密碼。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        unlock.BACKEND = None
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)

    def tearDown(self):
        try:
            wipe(self.V)
        finally:
            unlock.BACKEND = None
            shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("unlock", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def test_cli_round_trip(self):
        name = "測試門-zq7x"
        self.assertEqual(self.kb("save", "--kind", "記下的密碼", "--name", name, "--value", _pw.GATE_PW),
                         [f"記好了：{name}（記下的密碼），存在這台電腦的憑證管理員"])
        self.assertEqual(self.kb("save", "--kind", "記下的密碼", "--name", name, "--value", _pw.GATE_PW),
                         [f"換成新的了：{name}"])
        self.assertEqual(self.kb("get", "--name", name), [f"{name}：{_pw.GATE_PW}"])
        listed = self.kb("list")
        self.assertIn(f"- {name}（記下的密碼）", listed)
        self.assertNotIn(_pw.GATE_PW, "\n".join(listed))
        self.assertEqual(self.kb("forget", "--name", name), [f"忘掉了：{name}"])
        self.assertEqual(self.kb("get", "--name", name, code=3), [f"這台電腦沒有記「{name}」"])
        self.assertEqual(self.kb("forget", "--name", name, code=3), [f"這台電腦沒有記「{name}」"])
        cred = unlock._WinCred()
        self.assertEqual(cred.targets(f"wiki-with-willy/{vault_lib.vault_id(self.V)}/"), [])
        self.assertEqual(_pw.leaks(self.V), [])

    def test_conversation_get_marks_secret_shown_and_journal_has_no_value(self):
        sid = "pw-sid"
        turn.start(self.V, sid, "工地大門密碼多少")
        self.kb("save", "--kind", "記下的密碼", "--name", "工地大門", "--value", _pw.GATE_PW)
        self.kb("get", "--name", "工地大門")
        sess = json.loads((self.V / ".system" / "turns" / f"{sid}.json").read_text(encoding="utf-8"))
        self.assertTrue(sess.get("secret_shown"))
        self.assertEqual(_pw.leaks(self.V), [])

    def test_authorize_and_no_ids_in_output(self):
        from tests import _cards
        wang = _cards.entry_of(self.V, _pw.WANG)["id"]
        self.kb("save", "--kind", "PDF 密碼", "--name", "保險單", "--value", _pw.PDF_PW)
        out = self.kb("authorize", "--name", "保險單", "--label", "保險單", "--example", wang)
        self.assertEqual(out, ["以後保險單這一種的 PDF，會用「保險單」那組密碼開，開了會跟你說"])
        out += self.kb("list")
        self.assertEqual(self.kb("forget", "--name", "保險單"), ["忘掉了：保險單；保險單那一種也不會再自動開"])
        for line in out:
            self.assertNotRegex(line, ID)
        r = run_kb("unlock", "list", "--json", cwd=self.V)
        self.assertNotIn(_pw.PDF_PW, r.stdout)
        self.assertEqual(_pw.leaks(self.V), [])

    def test_vault_id_survives_revert(self):
        """設計 session 2026-10-23：補編號那一句退回之後，編號照留、get 拿得到。"""
        sid = "rv-sid"
        t = turn.start(self.V, sid, "記下工地大門")["turn"]
        self.kb("save", "--kind", "記下的密碼", "--name", "工地大門", "--value", _pw.GATE_PW)
        vid = vault_lib.vault_id(self.V)
        r = run_kb("versions", "revert", t, cwd=self.V)
        self.assertIn(r.returncode, (0, 3), r.stdout + r.stderr)
        self.assertEqual(vault_lib.vault_id(self.V), vid)
        self.assertEqual(self.kb("get", "--name", "工地大門"), [f"工地大門：{_pw.GATE_PW}"])


class VaultId(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_creates_once_and_broken_file_is_not_overwritten(self):
        self.assertIsNone(vault_lib.vault_id(self.V))
        vid = vault_lib.ensure_vault_id(self.V)
        self.assertEqual(vault_lib.ensure_vault_id(self.V), vid)
        self.assertEqual(json.loads((self.V / ".system" / "vault.json").read_text(encoding="utf-8")),
                         {"schema_version": 1, "vault_id": vid})
        (self.V / ".system" / "vault.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(ValueError):
            vault_lib.ensure_vault_id(self.V)
        self.assertEqual((self.V / ".system" / "vault.json").read_text(encoding="utf-8"), "{bad")


if __name__ == "__main__":
    unittest.main()
