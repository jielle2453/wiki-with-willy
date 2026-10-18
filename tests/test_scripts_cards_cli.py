"""kb cards todo／sync／drop／index 黑箱（ingest 製卡第 4 點；驗收 6、8、9、12、20，實作疑義裁定 6）：真的跑 CLI。"""
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import _cards
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, inventory, journal, pending, turn  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb(*args, cwd=self.V)

    def todo(self):
        r = self.kb("cards", "todo", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def item(self, rel=_cards.MEETING, bucket="new"):
        fid = _cards.entry_of(self.V, rel)["id"]
        return next(i for i in self.todo()[bucket] if i["id"] == fid)

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def scan(self):
        self.assertEqual(self.kb("inventory", "scan").returncode, 0)

    def manifest(self, turn_id):
        return {e["path"]: e["action"] for e in turn.entries(self.V, turn_id)}


def snapshot(V):
    """.system/ 底下（temp/ 除外）每個檔的 mtime，加回合夾清單。"""
    out = {}
    for p in sorted((V / ".system").rglob("*")):
        rel = p.relative_to(V).as_posix()
        if p.is_file() and not rel.startswith(".system/temp/"):
            out[rel] = p.stat().st_mtime_ns
    return out


class Todo(Base):
    def test_json_and_read_only(self):
        before = snapshot(self.V)
        data = self.todo()
        self.assertEqual(snapshot(self.V), before)
        n_ok = sum(1 for e in inventory.load(self.V)["entries"] if e["status"] == "active" and e["read"] == "ok")
        self.assertEqual(len(data["new"]), n_ok)
        for item in data["new"]:
            self.assertTrue(item["dir"] and item["filename_pattern"] and item["header"])
        self.assertEqual(data["skipped"]["credential"], 1)

    def test_human_lines_and_limit(self):
        r = self.kb("cards", "todo", "--limit", "2")
        lines = r.stdout.splitlines()
        n = len(self.todo()["new"])
        self.assertEqual(lines[0], f"待製卡 {n} 份（新 {n}、重做 0）；搬動 0；讀不到 0、原檔不見 0（見 系統/待處理.md）")
        self.assertEqual(lines[1], "不製卡：檔名像密碼或金鑰 1")
        items = [ln for ln in lines if ln.startswith("- ")]
        self.assertEqual(len(items), 2)
        self.assertRegex(items[0], r"^- 新 f-[0-9a-f]{8} \S.* → 檔案索引/.+/<標題>-[0-9a-f]{8}\.md$")
        self.assertEqual(lines[-1], f"……還有 {n - 2} 份")

    def test_default_limit_is_ingest_auto_max(self):
        items = [ln for ln in self.kb("cards", "todo").stdout.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(items), 5)

    def test_all_done_says_up_to_date(self):
        for item in self.todo()["new"]:
            _cards.write_card(self.V, item, "卡" + item["id"][2:6])
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").unlink()
        self.scan()
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertEqual(lines[0], "待製卡 0 份（新 0、重做 0）；搬動 0；讀不到 0、原檔不見 1（見 系統/待處理.md）")
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").write_text("x", encoding="utf-8")

    def test_up_to_date_line(self):
        for item in self.todo()["new"]:
            _cards.write_card(self.V, item, "卡" + item["id"][2:6])
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertEqual(lines, ["卡都是最新的", "不製卡：檔名像密碼或金鑰 1"])

    def test_broken_counted_on_first_line(self):
        p = _cards.write_card(self.V, self.item(), "大安陳會議一")
        p.write_text(p.read_text(encoding="utf-8").replace("出處: 客戶原檔", "出處: 別的"), encoding="utf-8")
        first = self.kb("cards", "todo").stdout.splitlines()[0]
        self.assertTrue(first.endswith("；檔頭有問題 1 張"), first)
        self.assertNotIn("新 " + str(len(self.todo()["new"]) + 1), first)


class Sync(Base):
    def setUp(self):
        super().setUp()
        self.it = self.item()
        self.card = _cards.write_card(self.V, self.it, "大安陳會議一")
        self.before = self.card.read_text(encoding="utf-8")

    def test_move_to_other_dir(self):
        dst = self.raw / "客戶案件" / "信義王" / "會議紀錄_1.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        self.assertEqual(len(self.todo()["moved"]), 1)
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "sync")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "搬了 1 張卡、更新 0 張卡的原檔位置"), r.stderr)
        new = self.V / "檔案索引" / "客戶案件" / "信義王" / self.card.name
        self.assertFalse(self.card.exists())
        self.assertTrue(new.is_file())
        front = cards.parse_front(new.read_text(encoding="utf-8"))
        self.assertEqual(front["原檔"], ["file://客戶案件/信義王/會議紀錄_1.docx"])
        self.assertEqual(front["開原檔"], cards.open_link(dst))
        old_lines = [ln for ln in self.before.splitlines() if not ln.startswith(("  - ", "開原檔:"))]
        new_lines = [ln for ln in new.read_text(encoding="utf-8").splitlines() if not ln.startswith(("  - ", "開原檔:"))]
        self.assertEqual(old_lines, new_lines)
        m = self.manifest(t)
        self.assertEqual(m[self.card.relative_to(self.V).as_posix()], "delete")
        self.assertEqual(m[new.relative_to(self.V).as_posix()], "create")
        self.assertIn("總目錄.md", m)
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards sync", "搬了 1 張卡"))
        self.assertEqual(self.todo()["moved"], [])
        self.assertIn(new.name, (self.V / "總目錄.md").read_text(encoding="utf-8").replace("%20", " "))

    def test_same_dir_rename(self):
        dst = self.raw / "客戶案件" / "大安陳" / "會議紀錄_一.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        (moved,) = self.todo()["moved"]
        self.assertEqual(moved["to"], "檔案索引/客戶案件/大安陳")
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "sync")
        self.assertEqual(r.stdout.strip(), "搬了 0 張卡、更新 1 張卡的原檔位置")
        self.assertTrue(self.card.is_file())
        self.assertEqual(cards.parse_front(self.card.read_text(encoding="utf-8"))["原檔"],
                         ["file://客戶案件/大安陳/會議紀錄_一.docx"])
        rel = self.card.relative_to(self.V).as_posix()
        self.assertEqual(self.manifest(t)[rel], "modify")
        self.assertEqual(self.todo()["moved"], [])

    def test_target_exists_skipped(self):
        dst = self.raw / "客戶案件" / "信義王" / "會議紀錄_1.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        clash = self.V / "檔案索引" / "客戶案件" / "信義王" / self.card.name
        clash.parent.mkdir(parents=True)
        clash.write_text("別人", encoding="utf-8")
        r = self.kb("cards", "sync")
        self.assertIn("已經有同名的卡", r.stdout)
        self.assertTrue(self.card.exists())
        self.assertEqual(clash.read_text(encoding="utf-8"), "別人")


class Drop(Base):
    def test_active_refused(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        r = self.kb("cards", "drop", e["id"])
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stdout.splitlines()[0].startswith("原檔還在："), r.stdout)

    def test_drop_then_revert(self):
        item = self.item()
        card = _cards.write_card(self.V, item, "大安陳會議一")
        (self.raw / _cards.MEETING).unlink()
        self.scan()
        (pend,) = [i for i in pending.load(self.V)["items"] if i["kind"] == "missing_file"]
        self.kb("cards", "index")
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "drop", item["id"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已拿掉「大安陳會議一」的卡；說「退回上一版」救得回來")
        self.assertFalse(card.exists())
        m = self.manifest(t)
        self.assertEqual(m[card.relative_to(self.V).as_posix()], "delete")
        for rel in (".system/inventory.json", ".system/pending.json", "系統/待處理.md", "總目錄.md"):
            self.assertIn(rel, m)
        e = next(x for x in inventory.load(self.V)["entries"] if x["id"] == item["id"])
        self.assertEqual(e["status"], "deleted")
        done = next(i for i in pending.load(self.V)["items"] if i["id"] == pend["id"])
        self.assertEqual(done["status"], "done")
        self.assertIn("說刪了", done["how"])
        self.assertIn("目前沒有等你決定的事。", (self.V / "系統" / "待處理.md").read_text(encoding="utf-8"))
        self.assertNotIn("大安陳會議一", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards drop", "拿掉「大安陳會議一」的卡（原檔已刪）"))
        r = self.kb("versions", "revert")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(card.is_file())
        e = next(x for x in inventory.load(self.V)["entries"] if x["id"] == item["id"])
        self.assertEqual(e["status"], "missing")
        back = next(i for i in pending.load(self.V)["items"] if i["id"] == pend["id"])
        self.assertEqual(back["status"], "open")
        self.assertIn("大安陳會議一", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        (m,) = self.todo()["missing"]
        self.assertEqual(m["id"], item["id"])

    def test_drop_without_card(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        (self.raw / _cards.MEETING).unlink()
        self.scan()
        r = self.kb("cards", "drop", e["id"])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(next(x for x in inventory.load(self.V)["entries"] if x["id"] == e["id"])["status"], "deleted")

    def test_unknown_id(self):
        r = self.kb("cards", "drop", "f-00000000")
        self.assertEqual(r.returncode, 3)


class Index(Base):
    def test_empty_then_three_then_drop(self):
        r = self.kb("cards", "index")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "總目錄：0 張卡"), r.stderr)
        text = (self.V / "總目錄.md").read_text(encoding="utf-8")
        self.assertIn("還沒有卡", text)
        self.assertTrue(text.rstrip().endswith("共 0 張卡"))
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards index", "總目錄：0 張卡"))
        self.assertEqual([c["path"] for c in prog["changed"]], ["總目錄.md"])
        pick = {i["uri"]: i for i in self.todo()["new"]}
        _cards.write_card(self.V, pick["file://" + _cards.MEETING], "大安陳 會議")
        _cards.write_card(self.V, pick["file://報價與合約/合約範本.txt"], "合約範本")
        _cards.write_card(self.V, pick["file://客戶案件/內湖李/估價單.pdf"], "內湖李估價單")
        self.assertEqual(self.kb("cards", "index").stdout.strip(), "總目錄：3 張卡")
        lines = (self.V / "總目錄.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("    - [大安陳 會議](檔案索引/客戶案件/大安陳/大安陳%20會議-"
                      f"{pick['file://' + _cards.MEETING]['id'][2:]}.md)", lines)
        self.assertEqual(lines[-1], "共 3 張卡")
        meeting = _cards.card_path(self.V, pick["file://" + _cards.MEETING], "大安陳 會議")
        meeting.write_text("壞了\n", encoding="utf-8")
        r = self.kb("cards", "index")
        self.assertEqual(r.returncode, 0)
        self.assertIn(f"[大安陳 會議-{pick['file://' + _cards.MEETING]['id'][2:]}]",
                      (self.V / "總目錄.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
