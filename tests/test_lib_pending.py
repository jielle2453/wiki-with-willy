"""_lib/pending（D-102、D-062，ingest 製卡第 5 點）：.system/pending.json schema 1、去重、resolve／dismiss／auto_done、
sync_missing 照清冊狀態補開與自動 done、每次寫先備進回合（json 與 md 兩個）、渲染 系統/待處理.md（fixture 比對，D-067）。"""
import json
import shutil
import unittest

from tests._guard_fixture import FIXTURES
from tests.test_lib_inventory_scan import make_inv_vault
from kb._lib import pending, turn  # noqa: E402

CARDS = FIXTURES / "cards"
MISSING_Q = "是你刪的嗎？是的話我把它的卡一起拿掉；不是的話可以從資源回收筒或雲端找回來。"


def entry(fid, uri, status="active"):
    return {"id": fid, "origin": "client_file", "locations": [{"uri": uri, "size": 1, "mtime": "2026-10-01T00:00:00"}],
            "fingerprints": [], "read": "ok", "status": status, "first_seen": "x", "created_turn": "t",
            "missing_since": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self):
        return pending.load(self.V)["items"]

    def md(self):
        return (self.V / "系統" / "待處理.md").read_text(encoding="utf-8")

    def manifest(self, turn_id=None):
        return {e["path"]: e["action"] for e in turn.entries(self.V, turn_id or self.t)}


class LoadShape(Base):
    def test_missing_file_is_empty(self):
        self.assertEqual(pending.load(self.V), {"schema_version": 1, "items": []})

    def test_bad_shapes_raise(self):
        p = self.V / ".system" / "pending.json"
        for text in ("{", "[]", '{"items": 3}', '{"items": [1]}', '{"schema_version": 9, "items": []}'):
            p.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                pending.load(self.V)


class Render(Base):
    def test_matches_fixture(self):
        shutil.copy2(CARDS / "pending.json", self.V / ".system" / "pending.json")
        pending.render(self.V)
        self.assertEqual(self.md(), (CARDS / "待處理.md").read_text(encoding="utf-8"))

    def test_nothing_open(self):
        pending.render(self.V)
        lines = self.md().splitlines()
        self.assertEqual(lines[0], "# 待處理")
        self.assertEqual(lines[3], "由程式產生，改了會被覆蓋。")
        self.assertEqual(lines[-1], "目前沒有等你決定的事。")

    def test_reason_questions(self):
        for reason, ask in (("舊格式（.doc）", "用 Word／Excel 另存成新格式。"),
                            ("只在雲端、還沒下載到電腦", "在檔案上按右鍵選保留在這台電腦上。"),
                            ("不支援的格式（.zip）", "跟我說先跳過，或換一份檔。")):
            pid = pending.add(self.V, "unreadable", ["f-" + str(abs(hash(reason)))[:8]], "「x」（a）讀不到", reason, self.t)
            with self.subTest(reason=reason):
                self.assertIn(f"「x」（a）讀不到：{reason}。{ask}", self.md())
            pending.dismiss(self.V, pid, self.t)

    def test_newest_first(self):
        pending._now = lambda: "2026-10-17T09:00:00"
        try:
            pending.add(self.V, "missing_file", ["f-1"], "「舊」（a）不見了", None, self.t)
            pending._now = lambda: "2026-10-18T09:00:00"
            pending.add(self.V, "missing_file", ["f-2"], "「新」（a）不見了", None, self.t)
        finally:
            pending._now = pending._now_default
        body = [ln for ln in self.md().splitlines() if ln.startswith("- ")]
        self.assertEqual(body[0], f"- 2026-10-18 「新」（a）不見了。{MISSING_Q}")
        self.assertEqual(body[1], f"- 2026-10-17 「舊」（a）不見了。{MISSING_Q}")


class AddResolve(Base):
    def test_add_shape_and_dedupe(self):
        pid = pending.add(self.V, "unreadable", ["f-11112222"], "「x.doc」（客戶案件）讀不到", "舊格式（.doc）", self.t)
        self.assertRegex(pid, r"^p-[0-9a-f]{8}$")
        again = pending.add(self.V, "unreadable", ["f-11112222"], "「x.doc」（客戶案件）讀不到", "舊格式（.doc）", self.t)
        self.assertEqual(again, pid)
        (item,) = self.items()
        self.assertEqual(item["kind"], "unreadable")
        self.assertEqual(item["file_ids"], ["f-11112222"])
        self.assertEqual(item["entity_ids"], [])
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["created_turn"], self.t)
        self.assertIsNone(item["resolved"])
        self.assertIn("## 讀不到（1）", self.md())

    def test_writes_backup_both_files(self):
        pending.add(self.V, "missing_file", ["f-1"], "「a」（b）不見了", None, self.t)
        m = self.manifest()
        self.assertEqual(m.get(".system/pending.json"), "create")
        self.assertEqual(m.get("系統/待處理.md"), "modify")  # fixture 本來就有這個檔

    def test_resolve_and_dismiss(self):
        a = pending.add(self.V, "missing_file", ["f-1"], "「a」（b）不見了", None, self.t)
        b = pending.add(self.V, "missing_file", ["f-2"], "「c」（d）不見了", None, self.t)
        pending.resolve(self.V, a, "先留著", self.t)
        pending.dismiss(self.V, b, self.t)
        by = {i["id"]: i for i in self.items()}
        self.assertEqual((by[a]["status"], by[a]["how"]), ("done", "先留著"))
        self.assertTrue(by[a]["resolved"])
        self.assertEqual((by[b]["status"], by[b]["how"]), ("dismissed", "客戶說不用管"))  # 測試後修正裁定 31
        self.assertIn("目前沒有等你決定的事。", self.md())

    def test_resolve_unknown_raises(self):
        with self.assertRaises(KeyError):
            pending.resolve(self.V, "p-00000000", "x", self.t)
        with self.assertRaises(KeyError):
            pending.dismiss(self.V, "p-00000000", self.t)

    def test_closed_item_does_not_block_new_one(self):
        a = pending.add(self.V, "missing_file", ["f-1"], "「a」（b）不見了", None, self.t)
        pending.resolve(self.V, a, "找回來了", self.t)
        b = pending.add(self.V, "missing_file", ["f-1"], "「a」（b）不見了", None, self.t)
        self.assertNotEqual(a, b)

    def test_auto_done(self):
        pending.add(self.V, "unreadable", ["f-1"], "「a」（b）讀不到", "有密碼", self.t)
        self.assertTrue(pending.auto_done(self.V, "f-1", "unreadable", "檔案換了，重新讀", self.t))
        self.assertFalse(pending.auto_done(self.V, "f-1", "unreadable", "檔案換了，重新讀", self.t))
        (item,) = self.items()
        self.assertEqual((item["status"], item["how"]), ("done", "檔案換了，重新讀"))

    def test_open_for(self):
        pid = pending.add(self.V, "unreadable", ["f-1"], "「a」（b）讀不到", "有密碼", self.t)
        self.assertEqual(pending.open_for(self.V, "f-1", "unreadable")["id"], pid)
        self.assertIsNone(pending.open_for(self.V, "f-1", "missing_file"))


class SyncMissing(Base):
    def test_opens_for_missing_and_closes_when_back(self):
        gone = entry("f-aaaa0001", "file://客戶案件/大安陳/會議紀錄_1.docx", "missing")
        here = entry("f-aaaa0002", "file://客戶案件/報價.pdf")
        self.assertTrue(pending.sync_missing(self.V, [gone, here], self.t))
        (item,) = self.items()
        self.assertEqual(item["kind"], "missing_file")
        self.assertEqual(item["file_ids"], ["f-aaaa0001"])
        self.assertEqual(item["text"], "「會議紀錄_1.docx」（客戶案件/大安陳）不見了")
        self.assertFalse(pending.sync_missing(self.V, [gone, here], self.t))  # 不重開
        self.assertEqual(len(self.items()), 1)
        gone["status"] = "active"
        self.assertTrue(pending.sync_missing(self.V, [gone, here], self.t))
        (item,) = self.items()
        self.assertEqual((item["status"], item["how"]), ("done", "找回來了"))
        self.assertNotIn("會議紀錄_1.docx", self.md())

    def test_root_level_file_label(self):
        pending.sync_missing(self.V, [entry("f-1", "file://客戶案件/空檔.bin", "missing")], self.t)
        self.assertEqual(self.items()[0]["text"], "「空檔.bin」（客戶案件）不見了")

    def test_no_change_writes_nothing(self):
        self.assertFalse(pending.sync_missing(self.V, [entry("f-1", "file://a/b.txt")], self.t))
        self.assertFalse((self.V / ".system" / "pending.json").exists())
        self.assertEqual(self.manifest(), {})


class Answered(Base):
    """測試後修正裁定 31：客戶答過的（done、dismissed，created ≥ missing_since）不重開；dismissed 的 unreadable 也能自動 done。"""

    def gone(self, since):
        e = entry("f-aaaa0001", "file://客戶案件/會議紀錄_1.docx", "missing")
        e["missing_since"] = since
        return e

    def test_resolved_or_dismissed_not_reopened(self):
        pending._now = lambda: "2026-10-18T10:00:00"
        try:
            e = self.gone("2026-10-18T09:00:00")
            pending.sync_missing(self.V, [e], self.t)
            pid = self.items()[0]["id"]
            pending.resolve(self.V, pid, "先留著", self.t)
            self.assertFalse(pending.sync_missing(self.V, [e], self.t))
            self.assertEqual(len(self.items()), 1)
        finally:
            pending._now = pending._now_default

    def test_dismissed_not_reopened(self):
        pending._now = lambda: "2026-10-18T10:00:00"
        try:
            e = self.gone("2026-10-18T09:00:00")
            pending.sync_missing(self.V, [e], self.t)
            pending.dismiss(self.V, self.items()[0]["id"], self.t)
            self.assertFalse(pending.sync_missing(self.V, [e], self.t))
            self.assertEqual(len(self.items()), 1)
        finally:
            pending._now = pending._now_default

    def test_missing_again_later_reopens(self):
        pending._now = lambda: "2026-10-18T10:00:00"
        try:
            pending.sync_missing(self.V, [self.gone("2026-10-18T09:00:00")], self.t)
            pending.resolve(self.V, self.items()[0]["id"], "找回來了", self.t)
            pending._now = lambda: "2026-10-19T10:00:00"
            self.assertTrue(pending.sync_missing(self.V, [self.gone("2026-10-19T09:00:00")], self.t))
            self.assertEqual([i["status"] for i in self.items()], ["done", "open"])
        finally:
            pending._now = pending._now_default

    def test_auto_done_includes_dismissed(self):
        pid = pending.add(self.V, "unreadable", ["f-1"], "「a」（b）讀不到", "有密碼", self.t)
        pending.dismiss(self.V, pid, self.t)
        self.assertTrue(pending.auto_done(self.V, "f-1", "unreadable", "檔案換了，重新讀", self.t,
                                          statuses=("open", "dismissed")))
        self.assertEqual((self.items()[0]["status"], self.items()[0]["how"]), ("done", "檔案換了，重新讀"))


if __name__ == "__main__":
    unittest.main()
