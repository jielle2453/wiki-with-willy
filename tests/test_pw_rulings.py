"""「可以讀」裁示（密碼與守門第 6 點；D-183、D-184；驗收 2、3、4、5 的程式那半）：
rulings 只剩三種、掛清冊條目（files）、label、decline；清冊有效裁示的那份不記 credential、跟著檔走（搬、改名、改內容）；
守門只放讀、排除最優先、資料夾範圍全有裁示才放行、收回照擋；kb ruling 的成功與拒絕句、人讀不印編號。"""
import json
import os
import re
import shutil
import unittest
from pathlib import Path

from tests import _cards, _pw
from tests._guard_fixture import ROOT
from tests.test_scripts_inventory_cli import run_kb
from kb import guard  # noqa: E402
from kb._lib import inventory, rulings, turn  # noqa: E402

ID = r"[aefsrcp]-[0-9a-f]{8}"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fid(self, rel):
        return _cards.entry_of(self.V, rel)["id"]

    def entry(self, fid):
        return next(e for e in inventory.load(self.V)["entries"] if e["id"] == fid)

    def add(self, rel, label=None, text="那份網站密碼檔可以讀"):
        return rulings.add(self.V, "可以讀", [], [], text, self.t, files=[self.fid(rel)], label=label)

    def refused(self, want, kind, files=(), entities=(), cards=(), text="可以讀"):
        with self.assertRaises(rulings.RulingRefused) as cm:
            rulings.add(self.V, kind, list(entities), list(cards), text, self.t, files=list(files))
        self.assertEqual(str(cm.exception), want)

    def decide(self, tool, value, cwd=None):
        key = {"Read": "file_path", "Grep": "path", "Bash": "command", "PowerShell": "command"}[tool]
        return guard.decide({"tool_name": tool, "tool_input": {key: str(value)}, "cwd": str(cwd or self.V)}).decision


class Kinds(Base):
    def test_three_kinds(self):
        self.assertEqual(rulings.KINDS, {"口述為準": "spoken_wins", "不採用": "ruled_out", "可以讀": "credential_read"})
        self.assertEqual(rulings.ACTIVE_KINDS, ("spoken_wins", "ruled_out", "credential_read"))
        self.assertFalse(hasattr(rulings, "CREDENTIAL_KINDS"))

    def test_credential_read_records_files_and_label(self):
        item = self.add(_pw.WEB, label="網站密碼表")
        self.assertEqual((item["kind"], item["files"], item["label"], item["entities"], item["cards"]),
                         ("credential_read", [self.fid(_pw.WEB)], "網站密碼表", [], []))
        self.assertEqual(rulings.readable_ids(self.V), {self.fid(_pw.WEB)})
        md = (self.V / "系統" / "裁示.md").read_text(encoding="utf-8")
        self.assertIn(f"## 可以讀\n- 「網站密碼.xlsx」（網站密碼表）：那份網站密碼檔可以讀（{item['date']}）", md)
        self.assertNotRegex(md, ID)

    def test_file_by_uri_or_path(self):
        a = rulings.add(self.V, "可以讀", [], [], "可以讀", self.t, files=["file://" + _pw.WEB])
        b = rulings.add(self.V, "可以讀", [], [], "可以讀", self.t, files=[str(self.raw / _pw.BANK)])
        self.assertEqual((a["files"], b["files"]), ([self.fid(_pw.WEB)], [self.fid(_pw.BANK)]))

    def test_refusals_exact(self):
        self.refused("裁示種類要是 口述為準、不採用、可以讀 之一", "可以記", entities=["信義王"])
        self.refused("可以讀要指定是哪一份檔（--file）", "可以讀")
        self.refused("這份本來就讀得到", "可以讀", files=[self.fid("客戶案件/信義王/報價_v2.pdf")])
        self.refused("清冊沒有這份檔：f-00000000", "可以讀", files=["f-00000000"])
        self.refused("可以讀只掛檔（拿掉 --entity、--card）", "可以讀", files=[self.fid(_pw.WEB)], entities=["信義王"])
        self.refused("只有可以讀才用 --file", "口述為準", files=[self.fid(_pw.WEB)], entities=["信義王"])
        self.assertFalse((self.V / ".system" / "rulings.json").exists())


class InventoryFollows(Base):
    def test_scan_marks_ruled_file_readable_and_fingerprints_it(self):
        fid = self.fid(_pw.WEB)
        self.add(_pw.WEB)
        _cards.scan_apply(self.V)
        e = self.entry(fid)
        self.assertEqual(e["read"], "ok")
        self.assertTrue(e["fingerprints"])
        self.assertEqual(self.entry(self.fid(_pw.BANK))["read"], "credential")

    def test_follows_rename_move_and_content_change(self):
        fid = self.fid(_pw.WEB)
        self.add(_pw.WEB)
        _cards.scan_apply(self.V)
        new = self.raw / "客戶案件" / "大安陳" / "網站密碼_2026.xlsx"
        os.replace(self.raw / _pw.WEB, new)
        _cards.scan_apply(self.V)
        e = self.entry(fid)
        self.assertEqual((e["status"], e["read"], e["locations"][0]["uri"]),
                         ("active", "ok", "file://客戶案件/大安陳/網站密碼_2026.xlsx"))
        self.assertEqual(self.decide("Read", new), "allow")
        from tests.test_lib_inventory_scan import xlsx
        xlsx(new, (_pw.WEB_PW + "-v2", 2))
        _cards.scan_apply(self.V)
        self.assertEqual((self.entry(fid)["status"], self.entry(fid)["read"]), ("active", "ok"))
        self.assertEqual(self.decide("Read", new), "allow")

    def test_decline_recorded_and_kept_by_scan(self):
        fid = self.fid(_pw.BANK)
        rulings.decline(self.V, fid, self.t)
        _cards.scan_apply(self.V)
        self.assertRegex(self.entry(fid)["credential_declined"], r"^\d{4}-\d{2}-\d{2}$")
        with self.assertRaises(rulings.RulingRefused) as cm:
            rulings.decline(self.V, self.fid("客戶案件/信義王/報價_v2.pdf"), self.t)
        self.assertEqual(str(cm.exception), "這份本來就讀得到")


class Guard(Base):
    """驗收 2：只放讀那一份；同一夾 grep -r 兩份都有裁示才放；排除最優先；收回照擋；寫照擋。"""

    def test_read_only_that_file(self):
        web, bank = self.raw / _pw.WEB, self.raw / _pw.BANK
        folder = (self.raw / "客戶案件" / "信義王").as_posix()
        self.assertEqual(self.decide("Read", web), "deny")
        item = self.add(_pw.WEB)
        self.assertEqual(self.decide("Read", web), "allow")
        self.assertEqual(self.decide("Bash", f'cat "{web.as_posix()}"'), "pass")
        self.assertEqual(self.decide("PowerShell", f"Get-Content '{web}'"), "pass")  # 測試 28 第 3 件：反斜線路徑不產跳脫變體
        self.assertEqual(self.decide("Read", bank), "deny")
        self.assertEqual(self.decide("Bash", f'grep -r x "{folder}"'), "deny")
        self.assertEqual(self.decide("Grep", folder), "deny")
        self.assertEqual(self.decide("Bash", f'echo x > "{web.as_posix()}"'), "deny")
        self.assertEqual(self.decide("Bash", f'cp "{web.as_posix()}" "{self.tmp.as_posix()}/x.xlsx"'), "deny")
        both = self.add(_pw.BANK)
        self.assertEqual(self.decide("Bash", f'grep -r x "{folder}"'), "pass")
        self.assertEqual(self.decide("Grep", folder), "allow")
        rulings.remove(self.V, item["id"], self.t)
        rulings.remove(self.V, both["id"], self.t)
        self.assertEqual(self.decide("Read", web), "deny")
        self.assertEqual(self.decide("Bash", f'cat "{web.as_posix()}"'), "deny")

    def test_excluded_wins(self):
        self.add(_pw.WEB)
        _cards.scan_apply(self.V)
        src = json.loads((self.V / ".system" / "sources.json").read_text(encoding="utf-8"))
        src["sources"][0]["excludes"].append({"spec": "大安陳", "kind": "unused", "reason": "x", "added": "2026-10-23"})
        (self.V / ".system" / "sources.json").write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
        moved = self.raw / "客戶案件" / "大安陳" / "網站密碼.xlsx"
        os.replace(self.raw / _pw.WEB, moved)
        _cards.scan_apply(self.V)
        self.assertEqual(self.decide("Read", moved), "deny")

    def test_unreadable_rulings_file_means_no_ruling(self):
        self.add(_pw.WEB)
        (self.V / ".system" / "rulings.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(self.decide("Read", self.raw / _pw.WEB), "deny")


class Cli(Base):
    def kb(self, *args, code=0):
        r = run_kb("ruling", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def test_add_list_decline_and_no_ids(self):
        fid = self.fid(_pw.WEB)
        out = self.kb("add", "--kind", "可以讀", "--file", fid, "--label", "網站密碼表", "--text", "那份網站密碼檔可以讀")
        self.assertEqual(out, ["已記下裁示（可以讀）：「網站密碼.xlsx」"])
        listed = self.kb("list")
        self.assertIn("- 可以讀：「網站密碼.xlsx」—那份網站密碼檔可以讀", listed[0])
        self.assertEqual(listed[-1], "有效裁示 1 條")
        r = run_kb("ruling", "add", "--kind", "可以讀", "--file", self.fid(_pw.BANK), "--text", "x 可以讀", "--json", cwd=self.V)
        self.assertRegex(json.loads(r.stdout)["ruling"]["id"], r"^r-[0-9a-f]{8}$")
        self.assertEqual(self.kb("decline", "--file", self.fid("公司行政/薪資/網銀密碼.txt")),
                         ["記下了：「網銀密碼.txt」不讀，之後不再問"])
        for line in out + listed + self.kb("decline", "--file", self.fid("公司行政/薪資/網銀密碼.txt"), "--quiet"):
            self.assertNotRegex(line, ID)

    def test_refusals(self):
        self.assertEqual(self.kb("add", "--kind", "可以記", "--entity", "信義王", "--text", "x", code=3),
                         ["裁示種類要是 口述為準、不採用、可以讀 之一"])
        self.assertEqual(self.kb("add", "--kind", "可以讀", "--file", self.fid("客戶案件/信義王/報價_v2.pdf"), "--text", "x",
                                 code=3), ["這份本來就讀得到"])


if __name__ == "__main__":
    unittest.main()
