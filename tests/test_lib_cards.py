"""_lib/cards（D-101、D-104、D-105，ingest 製卡第 3 點）：檔頭解析、位置、開原檔、標題與檔名、header_for、validate 的原因句、
render_index（總目錄）、todo 的分桶。fixture 是 tests/fixtures/cards（兩張合法卡、一張檔頭壞的）與 tests/_cards 組的知識庫。"""
import os
import shutil
import unittest
import urllib.parse
from pathlib import Path

from tests import _cards, _ooxml
from tests._guard_fixture import FIXTURES
from kb._lib import cards, inventory, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

FIX = FIXTURES / "cards"


class ParseFront(unittest.TestCase):
    def test_list_block_and_inline_and_quotes(self):
        a = cards.parse_front((FIX / "信義王報價第二版-3f9a1b2c.md").read_text(encoding="utf-8"))
        self.assertEqual(a["編號"], "f-3f9a1b2c")
        self.assertEqual(a["原檔"], ["file://客戶案件/信義王/報價_v2.pdf", "file://報價與合約/信義王/報價_v2.pdf"])
        self.assertEqual(a["敏感資料種類"], [])
        self.assertEqual(a["範本版本"], "1")
        self.assertTrue(a["開原檔"].startswith("file:///D:/"))
        b = cards.parse_front((FIX / "大安陳進度表-5d0e2a77.md").read_text(encoding="utf-8"))
        self.assertEqual(b["出處"], "AI 產出")
        self.assertEqual(b["原檔"], ["file://AI 產出/大安陳進度表.xlsx"])
        self.assertEqual(b["敏感資料種類"], ["統編", "電話"])

    def test_broken_raises(self):
        for text in ((FIX / "檔頭壞了-00000000.md").read_text(encoding="utf-8"), "編號: x\n---\n", "---\n編號: x\n"):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                cards.parse_front(text)

    def test_keys_and_origin_text(self):
        self.assertEqual(cards.KEYS, ("編號", "出處", "原檔", "指紋", "內容指紋", "標題", "卡型", "實體", "待補型別",
                                      "文件日期", "讀取", "製卡日期", "範本版本", "敏感資料種類", "開原檔"))
        self.assertEqual(cards.ORIGIN_TEXT, {"client_file": "客戶原檔", "intake": "對話收件", "ai_output": "AI 產出"})


class Small(unittest.TestCase):
    def test_card_dir(self):
        e = {"locations": [{"uri": "file://客戶案件/信義王/報價_v3.pdf"}, {"uri": "file://報價與合約/x.pdf"}]}
        self.assertEqual(cards.card_dir(e), "檔案索引/客戶案件/信義王")
        self.assertEqual(cards.card_dir({"locations": [{"uri": "file://AI 產出/x.md"}]}), "檔案索引/AI 產出")
        self.assertEqual(cards.card_dir({"locations": [{"uri": "file://知識庫/案子/信義王/合約.docx"}]}),
                         "檔案索引/知識庫/案子/信義王")

    def test_safe_title(self):
        self.assertEqual(cards.safe_title(' 信義王:報價*第三版?"  <v3>|. ', 40), "信義王報價第三版 v3")
        self.assertEqual(cards.safe_title("a\tb\x07c", 40), "a bc")
        self.assertEqual(cards.safe_title("一二三四五六七八九十" * 5, 40), "一二三四五六七八九十" * 4)
        self.assertIsNone(cards.safe_title(" ./ ", 40))

    def test_filename_ok(self):
        self.assertTrue(cards.filename_ok("信義王報價第三版-3f9a1b2c.md", "f-3f9a1b2c", "信義王報價第三版", 40))
        self.assertTrue(cards.filename_ok("報價v3-3f9a1b2c.md", "f-3f9a1b2c", "報價:v3", 40))
        self.assertFalse(cards.filename_ok("信義王報價第三版-00000000.md", "f-3f9a1b2c", "信義王報價第三版", 40))
        self.assertFalse(cards.filename_ok("別的-3f9a1b2c.md", "f-3f9a1b2c", "信義王報價第三版", 40))
        self.assertTrue(cards.filename_ok("x-0a1b2c3d.md", "out-0a1b2c3d", "x", 40))

    def test_open_link_uses_disk_case(self):
        tmp = Path(os.path.realpath(self._tmp()))
        p = tmp / "來源 一" / "報價(v3).pdf"
        p.parent.mkdir()
        p.write_bytes(b"x")
        link = cards.open_link(str(p).lower() if os.name == "nt" else str(p))
        self.assertTrue(link.startswith("file:"), link)
        self.assertEqual(urllib.parse.unquote(link[len("file:"):]).lstrip("/").replace("\\", "/"),
                         str(p).replace("\\", "/").lstrip("/"))
        self.assertNotIn(" ", link)

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="kbcard-")
        self.addCleanup(shutil.rmtree, d, True)
        return d


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sources(self):
        return sources_lib.load(self.V)

    def todo(self):
        return cards.todo(self.V, self.sources())

    def item(self, rel=_cards.MEETING, bucket="new"):
        fid = _cards.entry_of(self.V, rel)["id"]
        return next(i for i in self.todo()[bucket] if i["id"] == fid)

    def by_id(self):
        return {e["id"]: e for e in inventory.load(self.V)["entries"]}


class Header(Vault):
    def test_header_machine_fields(self):
        item = self.item()
        front = cards.parse_front(item["header"])
        e = _cards.entry_of(self.V, _cards.MEETING)
        self.assertEqual(front["編號"], e["id"])
        self.assertEqual(front["出處"], "客戶原檔")
        self.assertEqual(front["原檔"], ["file://" + _cards.MEETING])
        self.assertEqual(front["指紋"], e["fingerprints"][-1]["sha256"])
        self.assertEqual(front["內容指紋"], e["fingerprints"][-1]["text_sha256"])
        self.assertEqual(front["標題"], "（填）")
        self.assertEqual(front["範本版本"], "2")
        self.assertEqual((front["實體"], front["待補型別"]), ([], []))
        self.assertEqual(front["敏感資料種類"], [])
        self.assertEqual(front["開原檔"], cards.open_link(self.raw / _cards.MEETING))
        self.assertEqual(list(front), [k for k in cards.KEYS])
        self.assertEqual(item["dir"], "檔案索引/客戶案件/大安陳")
        self.assertEqual(item["filename_pattern"], f"<標題>-{e['id'][2:]}.md")
        self.assertEqual(item["kind"], "docx")
        self.assertIsNone(item["existing_card"])
        self.assertRegex(item["created"], r"^\d{4}-\d{2}-\d{2}$")  # AI 產出的卡寫「AI 於 YYYY-MM-DD 起草」用
        self.assertEqual(Path(item["path"]), Path(os.path.realpath(self.raw / _cards.MEETING)))

    def test_no_content_fp_line_for_pdf(self):
        front = cards.parse_front(self.item("客戶案件/內湖李/估價單.pdf")["header"])
        self.assertNotIn("內容指紋", front)


class Validate(Vault):
    def setUp(self):
        super().setUp()
        self.it = self.item()
        self.path = _cards.write_card(self.V, self.it, "大安陳會議一")
        self.rel = self.path.relative_to(self.V).as_posix()

    def reason(self, text=None, rel=None):
        if text is not None:
            (self.V / (rel or self.rel)).parent.mkdir(parents=True, exist_ok=True)
            (self.V / (rel or self.rel)).write_text(text, encoding="utf-8")
        return cards.validate(self.V, rel or self.rel, self.by_id())

    def edited(self, old, new):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        return text.replace(old, new, 1)

    def test_good_card_ok(self):
        self.assertEqual(self.reason(), (True, ""))

    def test_reasons(self):
        front = cards.parse_front(self.path.read_text(encoding="utf-8"))
        fp = front["指紋"]
        cases = [
            (self.edited("---\n", ""), "檔頭解析不了"),
            (self.edited("卡型: 會議紀錄\n", ""), "檔頭少了「卡型」"),
            (self.edited(f"編號: {front['編號']}", "編號: f-99999999"), "編號 f-99999999 不在清冊"),
            (self.edited(fp, ("0" if fp[0] != "0" else "1") + fp[1:]).replace(front["內容指紋"], "0" * 64),
             "指紋不是現行的，重跑 kb cards todo 拿新檔頭"),
            (self.edited("出處: 客戶原檔", "出處: AI 產出"), "出處該是 客戶原檔"),
            (self.edited(front["開原檔"], front["開原檔"][:-1] + "y"), "開原檔那行照 kb cards todo 印的抄"),
            (self.edited("標題: 大安陳會議一", "標題: （填）"), "標題"),
            (self.edited("卡型: 會議紀錄", "卡型: （填）"), "卡型"),
            (self.edited("文件日期: 2026-10-01", "文件日期: 115年10月1日"), "文件日期"),
            (self.edited("讀取: 完整", "讀取: 全部"), "讀取"),
            (self.edited("範本版本: 2", "範本版本: 3"), "範本版本該是 2"),
            (self.edited("原檔:\n  - file://" + _cards.MEETING + "\n", "原檔: []\n"), "檔頭少了「原檔」"),
        ]
        for text, want in cases:
            with self.subTest(want=want):
                ok, reason = self.reason(text)
                self.assertFalse(ok)
                self.assertIn(want, reason)

    def test_content_fp_alone_is_enough(self):
        front = cards.parse_front(self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.reason(self.edited(front["指紋"], "0" * 64)), (True, ""))

    def test_wrong_dir_and_filename(self):
        text = self.path.read_text(encoding="utf-8")
        ok, reason = self.reason(text, rel="檔案索引/客戶案件/信義王/" + self.path.name)
        self.assertIn("卡該放在 檔案索引/客戶案件/大安陳", reason)
        ok, reason = self.reason(text, rel=f"檔案索引/客戶案件/大安陳/大安陳會議一-00000000.md")
        self.assertIn(f"檔名該是 大安陳會議一-{self.it['id'][2:]}.md", reason)

    def test_inactive_entry(self):
        inventory.set_status(self.V, [self.it["id"]], "missing")
        ok, reason = self.reason()
        self.assertIn(f"編號 {self.it['id']} 的原檔已不在（missing）", reason)


class RenderIndex(Vault):
    def index(self):
        return (self.V / "總目錄.md").read_text(encoding="utf-8").splitlines()

    def test_empty(self):
        self.assertEqual(cards.render_index(self.V), 0)
        lines = self.index()
        self.assertEqual(lines[:4], ["# 總目錄", "這是什麼：你的檔案都在這裡，點進去是那份檔的卡，卡裡有開原檔的連結。",
                                     "怎麼用：可以摺疊；找不到的就問我。", "由程式產生，改了會被覆蓋。"])
        self.assertIn("還沒有卡；加資料夾後說「整理」就會有。", lines)
        self.assertEqual(lines[-1], "共 0 張卡")

    def test_nested_dirs_first_and_links(self):
        todo = self.todo()["new"]
        pick = {i["uri"]: i for i in todo}
        _cards.write_card(self.V, pick["file://" + _cards.MEETING], "大安陳 會議(一)")
        _cards.write_card(self.V, pick["file://客戶案件/空檔.bin"], "空檔")
        _cards.write_card(self.V, pick["file://知識庫/案子/信義王/合約.docx"], "信義王合約[正本]")
        broken = self.V / "檔案索引" / "客戶案件" / "壞卡-00000000.md"
        shutil.copy2(FIX / "檔頭壞了-00000000.md", broken)
        self.assertEqual(cards.render_index(self.V), 4)
        body = self.index()[5:]
        self.assertEqual(body, [
            "- 客戶案件",
            "  - 大安陳",
            f"    - [大安陳 會議(一)](檔案索引/客戶案件/大安陳/大安陳%20會議%28一%29-{pick['file://' + _cards.MEETING]['id'][2:]}.md)",
            "  - [壞卡-00000000](檔案索引/客戶案件/壞卡-00000000.md)",
            f"  - [空檔](檔案索引/客戶案件/空檔-{pick['file://客戶案件/空檔.bin']['id'][2:]}.md)",
            "- 知識庫",
            "  - 案子",
            "    - 信義王",
            f"      - [信義王合約\\[正本\\]](檔案索引/知識庫/案子/信義王/信義王合約%5B正本%5D-"
            f"{pick['file://知識庫/案子/信義王/合約.docx']['id'][2:]}.md)",
            "",
            "共 4 張卡",
        ])
        for line in body:
            if "](" in line:
                target = urllib.parse.unquote(line[line.index("](") + 2:-1])
                self.assertTrue((self.V / target).is_file(), target)

    def test_fullwidth_parens_not_encoded(self):
        item = self.todo()["new"][0]
        _cards.write_card(self.V, item, "會議紀錄（修）")
        cards.render_index(self.V)
        self.assertIn("會議紀錄（修）-", "\n".join(self.index()))

    def test_backup_with_turn(self):
        t = turn.start(self.V, None, "")["turn"]
        cards.render_index(self.V, t)
        self.assertEqual({e["path"]: e["action"] for e in turn.entries(self.V, t)}, {"總目錄.md": "create"})


class Todo(Vault):
    def test_buckets_on_clean_vault(self):
        todo = self.todo()
        active_ok = [e for e in inventory.load(self.V)["entries"] if e["status"] == "active" and e["read"] == "ok"]
        self.assertEqual(len(todo["new"]), len(active_ok))
        self.assertEqual(todo["skipped"], {"credential": 1, "placeholder": 0, "too_large": 0, "error": 0, "skipped": 0})
        for k in ("stale", "moved", "orphans", "unreadable", "missing", "broken"):
            self.assertEqual(todo[k], [], k)

    def test_card_written_then_stale_then_fake_change(self):
        item = self.item()
        _cards.write_card(self.V, item, "大安陳會議一")
        todo = self.todo()
        self.assertNotIn(item["id"], [i["id"] for i in todo["new"]])
        self.assertEqual((todo["stale"], todo["moved"], todo["broken"]), ([], [], []))
        p = self.raw / _cards.MEETING
        _ooxml.docx(p, _cards.MEETING_BLOCKS, stamp="2027-01-01")  # 只改 docProps：內容指紋相同（D-096）
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 50))
        _cards.scan_apply(self.V)
        self.assertEqual(self.todo()["stale"], [])
        _ooxml.docx(p, _cards.MEETING_BLOCKS + [("p", "追加一段")])
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))
        _cards.scan_apply(self.V)
        stale = self.todo()["stale"]
        self.assertEqual([i["id"] for i in stale], [item["id"]])
        self.assertTrue(stale[0]["existing_card"].endswith(f"大安陳會議一-{item['id'][2:]}.md"))

    def test_orphan_and_broken(self):
        item = self.item()
        p = _cards.write_card(self.V, item, "大安陳會議一")
        orphan = self.V / "檔案索引" / "客戶案件" / "舊" / "x-00000000.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text(p.read_text(encoding="utf-8").replace(item["id"], "f-00000000"), encoding="utf-8")
        p.write_text(p.read_text(encoding="utf-8").replace("出處: 客戶原檔", "出處: 別的"), encoding="utf-8")
        todo = self.todo()
        self.assertEqual(todo["orphans"], ["檔案索引/客戶案件/舊/x-00000000.md"])
        self.assertEqual(len(todo["broken"]), 1)
        self.assertIn("出處該是", todo["broken"][0]["reason"])
        self.assertEqual(todo["broken"][0]["id"], item["id"])  # 製卡者照 header 整張寫回（審閱後補）
        self.assertEqual(cards.parse_front(todo["broken"][0]["header"])["編號"], item["id"])
        self.assertNotIn(item["id"], [i["id"] for i in todo["new"] + todo["stale"]])

    def test_moved_other_dir_and_same_dir_rename(self):
        item = self.item()
        _cards.write_card(self.V, item, "大安陳會議一")
        src = self.raw / _cards.MEETING
        dst = self.raw / "客戶案件" / "信義王" / "會議紀錄_1.docx"
        dst.parent.mkdir(exist_ok=True)
        src.rename(dst)
        _cards.scan_apply(self.V)
        (moved,) = self.todo()["moved"]
        self.assertEqual(moved["id"], item["id"])
        self.assertEqual(moved["to"], "檔案索引/客戶案件/信義王")
        self.assertTrue(moved["from"].startswith("檔案索引/客戶案件/大安陳/"))
        dst.rename(dst.with_name("會議紀錄_一.docx"))
        _cards.scan_apply(self.V)
        (moved,) = self.todo()["moved"]
        self.assertEqual(moved["to"], "檔案索引/客戶案件/信義王")

    def test_missing_bucket(self):
        (self.raw / _cards.MEETING).unlink()
        _cards.scan_apply(self.V)
        (m,) = self.todo()["missing"]
        self.assertEqual(m["uri"], "file://" + _cards.MEETING)
        self.assertRegex(m["pending_id"], r"^p-")
        self.assertTrue(m["question"].startswith("「會議紀錄_1.docx」（客戶案件/大安陳）不見了。是你刪的嗎？"))
        self.assertTrue(m["missing_since"])


class AfterTest(Vault):
    """測試後修正裁定 19～23、26、28、29、31。"""

    def card(self, title="大安陳會議一"):
        return _cards.write_card(self.V, self.item(), title)

    def test_19_link_only_mismatch_is_moved_same_dir(self):
        p = self.card()
        text = p.read_text(encoding="utf-8")
        front = cards.parse_front(text)
        p.write_text(text.replace(front["開原檔"], front["開原檔"][:-1] + "y"), encoding="utf-8")
        todo = self.todo()
        (moved,) = todo["moved"]
        self.assertEqual(moved["to"], "檔案索引/客戶案件/大安陳")
        self.assertEqual(todo["broken"], [])

    def test_20_duplicates_keep_valid_one(self):
        item = self.item()
        good = _cards.write_card(self.V, item, "大安陳會議一")
        wrong = self.V / "檔案索引" / "客戶案件" / "信義王" / good.name
        wrong.parent.mkdir(parents=True)
        wrong.write_text(good.read_text(encoding="utf-8"), encoding="utf-8")
        todo = self.todo()
        self.assertEqual(todo["duplicates"], [{"id": item["id"], "keep": good.relative_to(self.V).as_posix(),
                                               "drop": [wrong.relative_to(self.V).as_posix()]}])
        self.assertEqual((todo["moved"], todo["broken"], todo["stale"]), ([], [], []))

    def test_20_duplicates_none_valid_keep_newest(self):
        item = self.item()
        a = _cards.write_card(self.V, item, "大安陳會議一")
        b = a.with_name("別名-" + a.name)
        b.write_text(a.read_text(encoding="utf-8"), encoding="utf-8")
        a.write_text(a.read_text(encoding="utf-8").replace("出處: 客戶原檔", "出處: 錯"), encoding="utf-8")
        os.utime(a, (a.stat().st_atime, a.stat().st_mtime - 100))
        (dup,) = self.todo()["duplicates"]
        self.assertEqual(dup["keep"], b.relative_to(self.V).as_posix())
        self.assertEqual(dup["drop"], [a.relative_to(self.V).as_posix()])

    def test_21_value_types_checked(self):
        p = self.card()
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(self.V).as_posix()
        for old, new, key in (("出處: 客戶原檔", "出處: [客戶原檔]", "出處"),
                              ("讀取: 完整", "讀取: [完整]", "讀取"),
                              ("敏感資料種類: []", "敏感資料種類: 無", "敏感資料種類")):
            p.write_text(text.replace(old, new), encoding="utf-8")
            with self.subTest(key=key):
                self.assertEqual(cards.validate(self.V, rel, self.by_id()), (False, f"檔頭「{key}」的格式不對"))
        front = cards.parse_front(text)
        p.write_text(text.replace(f"編號: {front['編號']}", f"編號: [{front['編號']}]"), encoding="utf-8")
        self.assertEqual(cards.validate(self.V, rel, self.by_id()), (False, "檔頭「編號」的格式不對"))

    def test_21_any_exception_blocks(self):
        p = self.card()
        real = cards.entry_link
        cards.entry_link = lambda *a, **k: 1 / 0
        try:
            ok, reason = cards.validate(self.V, p.relative_to(self.V).as_posix(), self.by_id())
        finally:
            cards.entry_link = real
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("檔頭解析不了"), reason)

    def test_22_no_nested_list_no_recursion(self):
        for value in ("[[a]]", "[a, [b]]", "[" * 5000 + "]" * 5000):
            with self.subTest(value=value[:10]), self.assertRaises(ValueError):
                cards.parse_front(f"---\n編號: {value}\n---\n")

    def test_23_link_text_and_folder_escaped(self):
        item = self.item()
        p = _cards.write_card(self.V, item, "報價v3")
        p.write_text(p.read_text(encoding="utf-8").replace("標題: 報價v3", "標題: 報價<v3>"), encoding="utf-8")
        folder = self.V / "檔案索引" / "怪[夾]"
        folder.mkdir(parents=True)
        (folder / "x-00000000.md").write_text("---\n標題: a\\b\n---\n", encoding="utf-8")
        cards.render_index(self.V)
        text = (self.V / "總目錄.md").read_text(encoding="utf-8")
        self.assertIn("[報價＜v3＞](", text)
        self.assertIn("- 怪\\[夾\\]", text)
        self.assertIn("[a\\\\b](", text)

    def test_26_render_index_takes_lock(self):
        from kb._lib import io as kio
        lock = self.V / "總目錄.md.lock"
        lock.write_text("", encoding="utf-8")
        old = kio.LOCK_TIMEOUT
        kio.LOCK_TIMEOUT = 0.3
        try:
            with self.assertRaises(TimeoutError):
                cards.render_index(self.V)
        finally:
            kio.LOCK_TIMEOUT = old
            lock.unlink()
        self.assertFalse((self.V / "總目錄.md").exists())

    def test_28_kind_from_extension_only(self):
        _ooxml.encrypted_pdf(self.raw / "報價與合約" / "合約_加密.pdf")
        _cards.scan_apply(self.V)
        item = self.item("報價與合約/合約_加密.pdf")
        self.assertEqual(item["kind"], "pdf")

    def test_29_read_error_skipped(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        data = inventory.load(self.V)
        for x in data["entries"]:
            if x["id"] == e["id"]:
                x["read"] = "error"
        inventory._write(self.V, data["entries"])
        todo = self.todo()
        self.assertEqual(todo["skipped"]["error"], 1)
        self.assertNotIn(e["id"], [i["id"] for i in todo["new"]])

    def test_31_answered_missing_not_listed(self):
        from kb._lib import pending
        (self.raw / _cards.MEETING).unlink()
        _cards.scan_apply(self.V)
        pid = self.todo()["missing"][0]["pending_id"]
        pending.resolve(self.V, pid, "先留著", turn.start(self.V, None, "")["turn"])
        self.assertEqual(self.todo()["missing"], [])


class TemplateTwo(unittest.TestCase):
    """實體與綜合卡第 4 點：第二版檔頭、實體夾預填、實體名稱三種錯、第一版進 unlinked、types_added、missing_types。"""

    def setUp(self):
        from tests import _entities
        self.E = _entities
        self.tmp, self.V, self.raw = _entities.make_entity_vault(with_entities=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def todo(self):
        return cards.todo(self.V, sources_lib.load(self.V))

    def by_id(self):
        return {e["id"]: e for e in inventory.load(self.V)["entries"]}

    def reason(self, rel):
        return cards.validate(self.V, rel, self.by_id())

    def test_fixture_v1_card_has_no_v2_keys(self):
        front = cards.parse_front((self.E.FIX / "範本第一版-0f0f0f0f.md").read_text(encoding="utf-8"))
        self.assertEqual(front["範本版本"], cards.OLD_TEMPLATE_VERSION)
        self.assertFalse(set(cards.V2_KEYS) & set(front))
        self.assertEqual([k for k in cards.KEYS if k not in front], ["內容指紋", "實體", "待補型別"])

    def test_prefill_from_entity_folder(self):
        front = cards.parse_front(self.E.new_item(self.V, self.E.CONTRACT)["header"])
        self.assertEqual(front["實體"], ["信義王"])
        self.assertEqual(cards.parse_front(self.E.new_item(self.V, self.E.QUOTE)["header"])["實體"], [])

    def test_entity_name_errors(self):
        cases = [
            ((["信義王宅"], ()), "實體「信義王宅」該寫登記的名稱「信義王」"),
            ((["順發"], ()), "實體「順發」還沒登記，先跑 kb entity add"),
            ((["信義王"], ["案子"]), "型別「案子」已在型別表，先 kb entity add 再掛"),
            ((["林小姐"], ()), "實體「林小姐」該寫登記的名稱「林小姐室內設計」"),
        ]
        rel = self.E.write_card(self.V, self.E.QUOTE, "信義王報價", ["信義王"])
        self.assertEqual(self.reason(rel), (True, ""))
        p = self.V / rel
        good = p.read_text(encoding="utf-8")
        for (names, pending), want in cases:
            with self.subTest(want=want):
                p.write_text(self.E.with_entities(good, names, pending), encoding="utf-8")
                self.assertEqual(self.reason(rel), (False, want))

    def test_merged_name_points_to_target(self):
        from kb._lib import entities
        rows = entities.load(self.V)["entities"]
        for e in rows:
            if e["id"] == self.E.WANG:
                e.update(status="merged", merged_into=self.E.XINYI)
        self.E.put_entities(self.V, rows)
        rel = self.E.write_card(self.V, self.E.QUOTE, "信義王報價", ["王宅"])
        self.assertEqual(self.reason(rel), (False, "實體「王宅」該寫登記的名稱「信義王」"))

    def test_v1_goes_to_unlinked_then_upgrade(self):
        rel = self.E.write_card(self.V, self.E.QUOTE, "信義王報價", [])
        self.E.to_v1(self.V, rel)
        data = self.todo()
        fid = cards.parse_front((self.V / rel).read_text(encoding="utf-8"))["編號"]
        self.assertEqual(data["unlinked"], [{"id": fid, "path": rel, "reason": "v1", "types": [], "prefill": []}])
        self.assertEqual(data["broken"], [])
        self.assertEqual(self.reason(rel), (False, "範本版本該是 2"))
        p = self.V / rel
        text = p.read_text(encoding="utf-8").replace("卡型: 會議紀錄\n", "卡型: 會議紀錄\n實體: [信義王]\n待補型別: []\n")
        p.write_text(text.replace("範本版本: 1", "範本版本: 2"), encoding="utf-8")
        self.assertEqual(self.reason(rel), (True, ""))
        self.assertEqual(self.todo()["unlinked"], [])

    def test_v1_prefill_for_entity_folder_card(self):
        rel = self.E.write_card(self.V, self.E.CONTRACT, "信義王合約", ["信義王"])
        self.E.to_v1(self.V, rel)
        (item,) = self.todo()["unlinked"]
        self.assertEqual(item["prefill"], ["信義王"])

    def test_missing_types_then_types_added(self):
        rel = self.E.write_card(self.V, self.E.QUOTE, "順發請款", [], pending=["廠商"])
        self.assertEqual(self.reason(rel), (True, ""))
        data = self.todo()
        self.assertEqual(data["missing_types"], {"廠商": 1})
        self.assertEqual(data["unlinked"], [])
        self.E.set_profile(self.V, types=(("案子", "matter"), ("業主", "party"), ("廠商", "party")))
        data = self.todo()
        self.assertEqual(data["missing_types"], {})
        (item,) = data["unlinked"]
        self.assertEqual((item["path"], item["reason"], item["types"]), (rel, "types_added", ["廠商"]))

    def test_entities_and_profile_blocks(self):
        data = self.todo()
        self.assertEqual(data["entities"][0], {"name": "信義王", "type": "案子", "aliases": ["信義王宅"], "self": False})
        self.assertIn({"name": "林小姐室內設計", "type": None, "aliases": ["林小姐"], "self": "company"}, data["entities"])
        self.assertEqual(data["profile"]["types"], [{"name": "案子", "category": "matter"},
                                                    {"name": "業主", "category": "party"}])
        self.assertEqual([s["type"] for s in data["profile"]["synthesis_sections"]], ["案子", "業主"])
        for key in ("synthesis_new", "synthesis_stale", "synthesis_broken", "entity_drift"):
            self.assertEqual(data[key], [], key)

    def test_rewrite_entities_only_touches_that_line(self):
        rel = self.E.write_card(self.V, self.E.QUOTE, "信義王報價", ["信義王", "王宅"])
        text = (self.V / rel).read_text(encoding="utf-8")
        new = cards.rewrite_entities(text, ["王宅"])
        self.assertEqual([ln for ln in new.splitlines() if not ln.startswith("實體:")],
                         [ln for ln in text.splitlines() if not ln.startswith("實體:")])
        self.assertIn("\n實體: [王宅]\n", new)



class RewriteEntitiesBlock(unittest.TestCase):
    """測試後修正裁定 13：實體 寫成「實體:」下接「  - 項」的區塊也換，換完寫成一行的清單。"""

    def test_block_form_replaced_by_inline(self):
        text = "---\n編號: f-1\n卡型: 報價\n實體:\n  - 信義王\n  - 林小姐室內設計\n待補型別: []\n---\n# 卡\n"
        new = cards.rewrite_entities(text, ["王宅", "林小姐室內設計"])
        self.assertEqual(new, "---\n編號: f-1\n卡型: 報價\n實體: [王宅, 林小姐室內設計]\n待補型別: []\n---\n# 卡\n")
        self.assertEqual(cards.parse_front(new)["實體"], ["王宅", "林小姐室內設計"])


if __name__ == "__main__":
    unittest.main()
