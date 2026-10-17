"""_lib/inventory 的 schema 2（D-093、D-094、D-096，specs/2026-10-16-清冊.md 第 3 點）：遷移 1→2、掃描範圍、read、
六種比對、來源根找不到、快速模式、reconcile、excluded 經 include 回 active、apply 備份、錯誤夾不中斷。
fixture 是 tests/fixtures/inventory（vault 與 raw 整包複製到 tmp）。每個走訪類測試都斷言命中筆數（I-097）。"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from tests._guard_fixture import FIXTURES
from kb._lib import inventory, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

FIX = FIXTURES / "inventory"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FIRST_URIS = {
    "file://客戶案件/信義王/報價_v2.pdf",
    "file://客戶案件/內湖李/估價單.pdf",
    "file://客戶案件/大安陳/[草稿] 會議紀錄、第一次（修）.txt",
    "file://客戶案件/大安陳/會議紀錄_1.docx",
    "file://客戶案件/空檔.bin",
    "file://報價與合約/信義王/報價_v2.pdf",
    "file://報價與合約/合約範本.txt",
    "file://公司行政/薪資/薪轉帳號.csv",
    "file://公司行政/薪資/網銀密碼.txt",
    "file://公司行政/舊檔/Big5備忘.txt",
    "file://知識庫/案子/信義王/合約.docx",
    "file://知識庫/案子/說明.txt",
    "file://收件匣/貼文.txt",
    "file://AI 產出/x.md",
}


def make_inv_vault():
    tmp = Path(tempfile.mkdtemp(prefix="kbinv-")).resolve()
    shutil.copytree(FIX / "vault", tmp / "vault")
    shutil.copytree(FIX / "raw", tmp / "raw")
    return tmp, tmp / "vault", tmp / "raw"


def xlsx(path, cell, inline=False, order=None, stamp="2026-10-01"):
    """最小 xlsx：A1 一個字串（共用字串或 inlineStr）、B1 一個數字。"""
    if inline:
        a1 = f'<c r="A1" t="inlineStr"><is><t>{cell[0]}</t></is></c>'
    else:
        a1 = '<c r="A1" t="s"><v>0</v></c>'
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row r="1">{a1}'
             f'<c r="B1"><v>{cell[1]}</v></c></row></sheetData></worksheet>')
    members = [("xl/worksheets/sheet1.xml", sheet), ("docProps/core.xml", f"<core>{stamp}</core>")]
    if not inline:
        members.append(("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="{S}"><si><t>{cell[0]}</t></si></sst>'))
    if order:
        members = [members[i] for i in order]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members:
            z.writestr(name, text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()
        self.t = turn.start(self.V, None, "")

    def tearDown(self):
        subprocess.run(["icacls", str(self.tmp), "/reset", "/T", "/Q"], capture_output=True) if os.name == "nt" else None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sources(self):
        return sources_lib.load(self.V)

    def scan(self, **kw):
        return inventory.scan(self.V, self.sources(), **kw)

    def full(self, **kw):
        """全掃並套用，回 report。"""
        report = self.scan(**kw)
        inventory.apply(self.V, report, self.t["turn"])
        return report

    def entries(self):
        return inventory.load(self.V)["entries"]

    def entry_at(self, uri):
        key = inventory.uri_key(uri)
        found = [e for e in self.entries() if any(inventory.uri_key(l["uri"]) == key for l in e["locations"])]
        self.assertEqual(len(found), 1, f"{uri} 對到 {len(found)} 筆")
        return found[0]

    def config(self, **values):
        (self.V / ".system" / "config.json").write_text(json.dumps(values), encoding="utf-8")

    def bump_mtime(self, path, seconds=100):
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + seconds))


class Migration(Base):
    def put_v1(self):
        shutil.copy2(FIX / "inventory_v1.json", self.V / ".system" / "inventory.json")

    def test_v1_is_migrated_in_memory(self):
        self.put_v1()
        data = inventory.load(self.V)
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(len(data["entries"]), 2)
        e = data["entries"][0]
        self.assertEqual(e["id"], "out-1a2b3c4d")
        st = os.stat(self.V / "AI 產出" / "x.md")
        self.assertEqual(e["locations"], [{"uri": "file://AI 產出/x.md", "size": st.st_size,
                                           "mtime": inventory.mtime_str(st.st_mtime)}])
        self.assertEqual(e["first_seen"], "2026-10-13T11:00:00")
        self.assertNotIn("created", e)
        self.assertEqual(e["read"], "ok")
        self.assertIsNone(e["missing_since"])
        self.assertEqual(e["created_turn"], "20261013-110000-6697")
        gone = data["entries"][1]
        self.assertEqual(gone["locations"], [{"uri": "file://AI 產出/舊草稿.md", "size": None, "mtime": None}])
        self.assertEqual(gone["status"], "deleted")

    def test_scan_then_apply_writes_schema_2_and_keeps_out_id(self):
        self.put_v1()
        self.full()
        data = json.loads((self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 2)
        out = [e for e in data["entries"] if e["id"] == "out-1a2b3c4d"]
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0]["locations"][0], dict)
        self.assertEqual(out[0]["status"], "active")
        self.assertEqual(len([e for e in data["entries"] if inventory.uri_key(e["locations"][0]["uri"]) ==
                              inventory.uri_key("file://AI 產出/x.md")]), 1)

    def test_bad_shapes_raise_value_error(self):
        p = self.V / ".system" / "inventory.json"
        for text in ('{"schema_version": 3, "entries": []}', "[]", '{"schema_version": 2}',
                     '{"schema_version": 2, "entries": {}}', '{"schema_version": 2, "entries": [1]}', "{bad"):
            with self.subTest(text=text):
                p.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    inventory.load(self.V)

    def test_missing_file_is_empty_schema_2(self):
        self.assertEqual(inventory.load(self.V), {"schema_version": 2, "entries": []})


class Scope(Base):
    def test_first_scan_uris(self):
        r = self.full()
        uris = {l["uri"] for e in self.entries() for l in e["locations"]}
        self.assertEqual(uris, FIRST_URIS)
        self.assertEqual(r["total"], 14)
        self.assertEqual(len(r["added"]), 13)
        self.assertEqual(len(self.entries()), 13)

    def test_card_hidden_and_temp_files_are_not_in(self):
        self.full()
        uris = {l["uri"] for e in self.entries() for l in e["locations"]}
        for bad in ("file://知識庫/案子/信義王/信義王.md", "file://客戶案件/~$暫存.docx", "file://客戶案件/.隱藏/x.txt",
                    "file://知識庫/系統/待處理.md", "file://知識庫/CLAUDE.md"):
            self.assertNotIn(bad, uris)
        self.assertFalse(any("/.system/" in u or u.startswith("file://知識庫/.") for u in uris))

    def test_origins(self):
        self.full()
        self.assertEqual(self.entry_at("file://知識庫/案子/信義王/合約.docx")["origin"], "client_file")
        self.assertEqual(self.entry_at("file://知識庫/案子/說明.txt")["origin"], "client_file")
        self.assertEqual(self.entry_at("file://客戶案件/空檔.bin")["origin"], "client_file")
        self.assertEqual(self.entry_at("file://收件匣/貼文.txt")["origin"], "intake")
        self.assertEqual(self.entry_at("file://AI 產出/x.md")["origin"], "ai_output")

    def test_entry_shape(self):
        self.full()
        e = self.entry_at("file://報價與合約/合約範本.txt")
        self.assertRegex(e["id"], r"^f-[0-9a-f]{8}$")
        self.assertEqual(set(e), {"id", "origin", "locations", "fingerprints", "read", "status", "first_seen",
                                  "created_turn", "missing_since"})
        self.assertEqual(e["status"], "active")
        self.assertEqual(e["read"], "ok")
        self.assertIsNone(e["missing_since"])
        self.assertEqual(e["created_turn"], self.t["turn"])
        self.assertEqual(len(e["fingerprints"]), 1)
        self.assertEqual(set(e["fingerprints"][0]), {"sha256", "time", "turn"})
        self.assertEqual(e["fingerprints"][0]["turn"], self.t["turn"])
        loc = e["locations"][0]
        st = os.stat(self.raw / "報價與合約" / "合約範本.txt")
        self.assertEqual(loc, {"uri": "file://報價與合約/合約範本.txt", "size": st.st_size,
                               "mtime": inventory.mtime_str(st.st_mtime)})

    def test_credential_file_is_in_without_fingerprint(self):
        r = self.full()
        e = self.entry_at("file://公司行政/薪資/網銀密碼.txt")
        self.assertEqual(e["read"], "credential")
        self.assertEqual(e["fingerprints"], [])
        self.assertEqual(r["read_counts"]["credential"], 1)
        self.assertEqual(r["read_examples"]["credential"], ["file://公司行政/薪資/網銀密碼.txt"])
        self.assertEqual(self.entry_at("file://公司行政/薪資/薪轉帳號.csv")["read"], "ok")

    def test_vault_interior_is_exempt_from_credential(self):
        """審閱裁定 5：總資料夾內部照 D-081 豁免。"""
        (self.V / "AI 產出" / "password.md").write_text("x", encoding="utf-8")
        (self.V / "收件匣" / "密碼表.txt").write_text("x", encoding="utf-8")
        self.full()
        self.assertEqual(self.entry_at("file://AI 產出/password.md")["read"], "ok")
        self.assertEqual(self.entry_at("file://收件匣/密碼表.txt")["read"], "ok")

    def test_duplicate_is_one_entry_two_locations(self):
        r = self.full()
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual([l["uri"] for l in e["locations"]],
                         ["file://客戶案件/信義王/報價_v2.pdf", "file://報價與合約/信義王/報價_v2.pdf"])
        self.assertEqual(r["duplicates"], [["file://報價與合約/信義王/報價_v2.pdf", e["id"]]])

    def test_by_source_and_report_json_shape(self):
        r = self.scan()
        self.assertEqual(r["by_source"]["客戶案件"]["files"], 5)
        self.assertEqual(r["by_source"]["報價與合約"]["files"], 2)
        self.assertEqual(r["by_source"]["公司行政"]["files"], 3)
        self.assertEqual(r["by_source"]["知識庫"]["files"], 2)
        self.assertEqual(r["by_source"]["收件匣"]["files"], 1)
        self.assertEqual(r["by_source"]["AI 產出"]["files"], 1)
        pdf = os.stat(self.raw / "客戶案件" / "信義王" / "報價_v2.pdf").st_size
        self.assertGreaterEqual(r["by_source"]["報價與合約"]["bytes"], pdf)
        self.assertEqual(set(r), {"added", "modified", "missing", "still_missing", "moved", "duplicates", "restored",
                                  "unchanged", "total", "by_source", "read_counts", "read_examples", "errors",
                                  "missing_sources", "incomplete", "entries"})
        json.dumps({k: v for k, v in r.items() if k != "entries"}, ensure_ascii=False)

    def test_scan_does_not_write_or_open_turn(self):
        """審閱裁定 6：scan 不碰回合、不寫檔；新指紋的 turn 先是 None。"""
        before = sorted(p.relative_to(self.V).as_posix() for p in (self.V / ".system").rglob("*"))
        r = self.scan()
        after = sorted(p.relative_to(self.V).as_posix() for p in (self.V / ".system").rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())
        fps = [fp for e in r["entries"] for fp in e["fingerprints"]]
        self.assertGreater(len(fps), 5)
        self.assertTrue(all(fp["turn"] is None for fp in fps))

    def test_only_roots_limits_everything(self):
        """審閱裁定 8：only_roots 時 total、by_source、added 都只算掃到的根。"""
        r = self.scan(only_roots=["報價與合約"])
        self.assertEqual(set(r["by_source"]), {"報價與合約"})
        self.assertEqual(r["total"], 2)
        self.assertEqual(sorted(r["added"]), ["file://報價與合約/信義王/報價_v2.pdf", "file://報價與合約/合約範本.txt"])

    @unittest.skipUnless(os.name == "nt", "attrib +h 只在 Windows")
    def test_hidden_attribute_is_skipped(self):
        target = self.raw / "客戶案件" / "藏起來.txt"
        target.write_text("x", encoding="utf-8")
        hidden_dir = self.raw / "客戶案件" / "藏起來的夾"
        hidden_dir.mkdir()
        (hidden_dir / "y.txt").write_text("y", encoding="utf-8")
        for p in (target, hidden_dir):
            subprocess.run(["attrib", "+h", str(p)], check=True, capture_output=True)
        r = self.scan()
        self.assertEqual(r["total"], 14)
        self.assertFalse(any("藏起來" in u for u in r["added"]))

    @unittest.skipUnless(os.name == "nt", "mklink /J 只在 Windows")
    def test_junction_is_not_followed(self):
        outside = self.tmp / "外面"
        outside.mkdir()
        (outside / "z.txt").write_text("z", encoding="utf-8")
        link = self.raw / "客戶案件" / "連結"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if r.returncode != 0 or not link.exists():
            self.skipTest("建不起 junction")
        report = self.scan()
        self.assertEqual(report["total"], 14)
        self.assertFalse(any("連結" in u for u in report["added"]))

    def junction(self, link, target):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode != 0 or not link.exists():
            self.skipTest("建不起 junction")

    @unittest.skipUnless(os.name == "nt", "mklink /J 只在 Windows")
    def test_area_root_that_is_a_junction_is_not_scanned(self):
        """審閱裁定 38、測試報告發現五：收件匣／AI 產出的根是連結就不掃，errors 記「<區> 是連結，不掃」。"""
        outside = self.tmp / "外面"
        outside.mkdir()
        (outside / "password.txt").write_text("x", encoding="utf-8")
        for area in ("收件匣", "AI 產出"):
            with self.subTest(area=area):
                shutil.rmtree(self.V / area)
                self.junction(self.V / area, outside)
                r = self.scan()
                self.assertFalse(any(u.startswith(f"file://{area}/") for u in r["added"]), r["added"])
                self.assertIn({"path": f"file://{area}", "error": f"{area} 是連結，不掃"}, r["errors"])
                os.rmdir(self.V / area)  # 只拆連結，目標的檔還在
                (self.V / area).mkdir()
        self.assertTrue((outside / "password.txt").is_file())

    @unittest.skipUnless(os.name == "nt", "mklink /J 只在 Windows")
    def test_type_folder_that_is_a_junction_is_not_scanned(self):
        outside = self.tmp / "外面"
        outside.mkdir()
        (outside / "y.txt").write_text("y", encoding="utf-8")
        self.junction(self.V / "外接型別", outside)
        r = self.scan()
        self.assertFalse(any("外接型別" in u for u in r["added"]))
        self.assertIn({"path": "file://知識庫/外接型別", "error": "知識庫/外接型別 是連結，不掃"}, r["errors"])
        self.assertEqual(r["total"], 14)

    def test_type_folder_root_file_is_client_file_and_reserved_folders_skipped(self):
        (self.V / "檔案索引" / "客戶案件").mkdir()
        (self.V / "檔案索引" / "客戶案件" / "索引.md").write_text("x", encoding="utf-8")
        r = self.scan()
        self.assertFalse(any("檔案索引" in u for u in r["added"]))
        self.assertIn("file://知識庫/案子/說明.txt", r["added"])


class Walk(Base):
    def test_skip_names_case_insensitive_and_skip_roots(self):
        base = self.tmp / "w"
        (base / "a" / "b").mkdir(parents=True)
        for name in ("x.txt", "THUMBS.DB", "Desktop.ini", "y.TMP", "~$z.xlsx", ".DS_Store"):
            (base / "a" / name).write_text("1", encoding="utf-8")
        (base / "a" / "b" / "c.txt").write_text("1", encoding="utf-8")
        (base / "skip").mkdir()
        (base / "skip" / "d.txt").write_text("1", encoding="utf-8")
        (base / "one.txt").write_text("1", encoding="utf-8")
        got = [rel for rel, st, err in inventory.walk(base)]
        self.assertEqual(sorted(got), ["a/b/c.txt", "a/x.txt", "one.txt", "skip/d.txt"])
        skip = [str(base / "skip"), str(base / "one.txt")]
        got = [rel for rel, st, err in inventory.walk(base, skip)]
        self.assertEqual(sorted(got), ["a/b/c.txt", "a/x.txt"])

    def test_unopenable_root_yields_error(self):
        got = list(inventory.walk(self.tmp / "沒有這個夾"))
        self.assertEqual(len(got), 1)
        rel, st, err = got[0]
        self.assertEqual((rel, st), ("", None))
        self.assertTrue(err.startswith("無法開啟資料夾："), err)


class ReadKind(unittest.TestCase):
    def st(self, size=10, attrs=0):
        return SimpleNamespace(st_size=size, st_file_attributes=attrs)

    def test_kinds(self):
        rk = inventory.read_kind
        self.assertEqual(rk(["a", "b.txt"], self.st(), 100), ("ok", None))
        for attrs in (0x40000, 0x400000, 0x1000):
            self.assertEqual(rk(["a", "b.txt"], self.st(attrs=attrs), 100), ("placeholder", None))
        self.assertEqual(rk(["薪資", "網銀密碼.txt"], self.st(), 100), ("credential", None))
        self.assertEqual(rk(["secret", "b.txt"], self.st(), 100), ("credential", None))
        self.assertEqual(rk(["a", "b.txt"], self.st(size=101), 100), ("too_large", None))
        self.assertEqual(rk(["a", "b.txt"], self.st(size=100), 100), ("ok", None))
        self.assertEqual(rk(["secret", "b.txt"], self.st(), 100, credential=False), ("ok", None))

    def test_placeholder_wins_over_credential(self):
        self.assertEqual(inventory.read_kind([".env"], self.st(attrs=0x400000), 100), ("placeholder", None))


class TooLarge(Base):
    def test_over_cap_has_no_fingerprint_and_size_change_is_modified(self):
        self.config(fingerprint_max_bytes=100)
        big = self.raw / "報價與合約" / "大檔.txt"
        big.write_text("x" * 300, encoding="utf-8")
        self.full()
        e = self.entry_at("file://報價與合約/大檔.txt")
        self.assertEqual((e["read"], e["fingerprints"]), ("too_large", []))
        big.write_text("y" * 400, encoding="utf-8")
        r = self.full()
        self.assertEqual(r["modified"], ["file://報價與合約/大檔.txt"])
        e = self.entry_at("file://報價與合約/大檔.txt")
        self.assertEqual(e["fingerprints"], [])
        self.assertEqual(e["locations"][0]["size"], 400)
        self.assertEqual(r["read_counts"]["too_large"], 1)


class Compare(Base):
    def setUp(self):
        super().setUp()
        self.full()

    def test_nothing_changed(self):
        r = self.scan()
        for key in ("added", "modified", "missing", "moved", "duplicates", "restored", "still_missing"):
            self.assertEqual(r[key], [], key)
        self.assertEqual(r["unchanged"], 14)
        self.assertEqual(r["total"], 14)

    def test_added(self):
        (self.raw / "客戶案件" / "新案.txt").write_text("新的案子\n", encoding="utf-8")
        r = self.scan()
        self.assertEqual(r["added"], ["file://客戶案件/新案.txt"])
        self.full()
        self.assertEqual(self.entry_at("file://客戶案件/新案.txt")["status"], "active")

    def test_modified_appends_fingerprint(self):
        p = self.raw / "報價與合約" / "合約範本.txt"
        before = self.entry_at("file://報價與合約/合約範本.txt")
        p.write_text("改過的合約範本內容，長度也不同。\n", encoding="utf-8")
        r = self.scan()
        self.assertEqual(r["modified"], ["file://報價與合約/合約範本.txt"])
        t2 = turn.start(self.V, None, "")
        inventory.apply(self.V, r, t2["turn"])
        e = self.entry_at("file://報價與合約/合約範本.txt")
        self.assertEqual(e["id"], before["id"])
        self.assertEqual(len(e["fingerprints"]), 2)
        self.assertEqual(e["fingerprints"][0], before["fingerprints"][0])
        self.assertEqual(e["fingerprints"][1]["turn"], t2["turn"])
        self.assertEqual(e["locations"][0]["size"], p.stat().st_size)

    def test_same_size_content_change_is_modified(self):
        """非 OOXML 位元組變就是 modified（D-096 只救 OOXML）。"""
        p = self.raw / "公司行政" / "舊檔" / "Big5備忘.txt"
        data = p.read_bytes()
        p.write_bytes(data[:-2] + b"!\n")
        self.bump_mtime(p)
        self.assertEqual(self.scan()["modified"], ["file://公司行政/舊檔/Big5備忘.txt"])

    def test_mtime_only_is_fake_change(self):
        p = self.raw / "報價與合約" / "合約範本.txt"
        self.bump_mtime(p)
        r = self.scan()
        self.assertEqual((r["modified"], r["added"]), ([], []))
        self.assertEqual(r["unchanged"], 14)
        inventory.apply(self.V, r, self.t["turn"])
        e = self.entry_at("file://報價與合約/合約範本.txt")
        self.assertEqual(e["locations"][0]["mtime"], inventory.mtime_str(p.stat().st_mtime))
        self.assertEqual(len(e["fingerprints"]), 1)

    def test_moved_keeps_id(self):
        old = self.raw / "客戶案件" / "內湖李" / "估價單.pdf"
        before = self.entry_at("file://客戶案件/內湖李/估價單.pdf")
        shutil.move(old, self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        r = self.scan()
        self.assertEqual(r["moved"], [["file://客戶案件/內湖李/估價單.pdf", "file://客戶案件/信義王/估價單.pdf"]])
        self.assertEqual((r["added"], r["missing"]), ([], []))
        inventory.apply(self.V, r, self.t["turn"])
        e = self.entry_at("file://客戶案件/信義王/估價單.pdf")
        self.assertEqual(e["id"], before["id"])
        self.assertEqual([l["uri"] for l in e["locations"]], ["file://客戶案件/信義王/估價單.pdf"])

    def test_duplicate_copy(self):
        src = self.raw / "客戶案件" / "信義王" / "報價_v2.pdf"
        shutil.copy2(src, self.raw / "客戶案件" / "大安陳" / "報價_v2.pdf")
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        r = self.scan()
        self.assertEqual(r["duplicates"], [["file://客戶案件/大安陳/報價_v2.pdf", e["id"]]])
        self.assertEqual(r["added"], [])
        self.assertEqual(r["total"], 15)

    def test_missing_then_not_reset_then_reappears(self):
        p = self.raw / "客戶案件" / "大安陳" / "會議紀錄_1.docx"
        data = p.read_bytes()
        uri = "file://客戶案件/大安陳/會議紀錄_1.docx"
        p.unlink()
        r = self.scan(now="2026-10-16T21:00:00")
        self.assertEqual(r["missing"], [uri])
        inventory.apply(self.V, r, self.t["turn"])
        e = self.entry_at(uri)
        self.assertEqual((e["status"], e["missing_since"]), ("missing", "2026-10-16T21:00:00"))
        r = self.scan(now="2026-10-17T09:00:00")
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["still_missing"], [uri])
        inventory.apply(self.V, r, self.t["turn"])
        self.assertEqual(self.entry_at(uri)["missing_since"], "2026-10-16T21:00:00")
        p.write_bytes(data)
        r = self.full()
        self.assertEqual(r["moved"], [[uri, uri]])
        e = self.entry_at(uri)
        self.assertEqual((e["status"], e["missing_since"]), ("active", None))

    def test_editing_one_of_two_copies_splits_entry(self):
        """審閱裁定 27：改了的位置拆成新條目（同 origin、指紋只有新的一筆）報 modified；沒改的留原條目、原指紋不動。"""
        before = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        p = self.raw / "報價與合約" / "信義王" / "報價_v2.pdf"
        p.write_bytes(p.read_bytes() + b"edited")
        r = self.scan(now="2026-10-17T09:00:00")
        self.assertEqual(r["modified"], ["file://報價與合約/信義王/報價_v2.pdf"])
        t2 = turn.start(self.V, None, "")
        inventory.apply(self.V, r, t2["turn"])
        kept = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual(kept["id"], before["id"])
        self.assertEqual([l["uri"] for l in kept["locations"]], ["file://客戶案件/信義王/報價_v2.pdf"])
        self.assertEqual(kept["fingerprints"], before["fingerprints"])
        split = self.entry_at("file://報價與合約/信義王/報價_v2.pdf")
        self.assertNotEqual(split["id"], before["id"])
        self.assertEqual(split["origin"], "client_file")
        self.assertEqual(split["first_seen"], "2026-10-17T09:00:00")
        self.assertEqual(split["created_turn"], t2["turn"])
        self.assertEqual(len(split["fingerprints"]), 1)
        self.assertEqual(split["fingerprints"][0]["sha256"], inventory.sha256_of(p))
        self.assertEqual(split["status"], "active")
        self.assertEqual(len(self.entries()), 14)

    def test_editing_both_copies_the_same_way_keeps_one_entry(self):
        for p in (self.raw / "客戶案件" / "信義王" / "報價_v2.pdf", self.raw / "報價與合約" / "信義王" / "報價_v2.pdf"):
            p.write_bytes(p.read_bytes() + b"edited")
        r = self.full()
        self.assertEqual(sorted(r["modified"]), ["file://報價與合約/信義王/報價_v2.pdf", "file://客戶案件/信義王/報價_v2.pdf"])
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual(len(e["locations"]), 2)
        self.assertEqual(len(e["fingerprints"]), 2)
        self.assertEqual(len(self.entries()), 13)

    def test_editing_both_copies_differently_supersedes_original(self):
        """審閱裁定 39、測試報告發現七：拆條目把原條目位置拿光時原條目標 superseded，不留沒有位置的 active。"""
        before = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        (self.raw / "客戶案件" / "信義王" / "報價_v2.pdf").write_bytes(b"AAA")
        (self.raw / "報價與合約" / "信義王" / "報價_v2.pdf").write_bytes(b"BBBB")
        r = self.full()
        self.assertEqual(len(r["modified"]), 2)
        old = [e for e in self.entries() if e["id"] == before["id"]][0]
        self.assertEqual(old["status"], "superseded")
        self.assertFalse([e for e in self.entries() if e["status"] == "active" and not e["locations"]])
        self.assertNotEqual(self.entry_at("file://客戶案件/信義王/報價_v2.pdf")["id"],
                            self.entry_at("file://報價與合約/信義王/報價_v2.pdf")["id"])

    def test_one_of_two_locations_gone_is_not_missing(self):
        (self.raw / "報價與合約" / "信義王" / "報價_v2.pdf").unlink()
        r = self.full()
        self.assertEqual(r["missing"], [])
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual([l["uri"] for l in e["locations"]], ["file://客戶案件/信義王/報價_v2.pdf"])
        self.assertEqual(e["status"], "active")

    def test_case_only_rename_is_unchanged_and_uri_updated(self):
        if os.name != "nt":
            self.skipTest("大小寫不分的檔案系統才有意義")
        p = self.raw / "公司行政" / "舊檔" / "Big5備忘.txt"
        p.rename(p.with_name("BIG5備忘.txt"))
        r = self.full()
        self.assertEqual((r["added"], r["missing"], r["modified"]), ([], [], []))
        e = self.entry_at("file://公司行政/舊檔/BIG5備忘.txt")
        self.assertEqual(e["locations"][0]["uri"], "file://公司行政/舊檔/BIG5備忘.txt")

    def test_turn_reverted_output_is_left_alone(self):
        """turn_reverted 的成品還在 AI 產出：不當新檔、檔不見也不改 status。"""
        e = self.entry_at("file://AI 產出/x.md")
        inventory.set_status(self.V, [e["id"]], "turn_reverted")
        r = self.scan()
        self.assertEqual(r["added"], [])
        (self.V / "AI 產出" / "x.md").unlink()
        r = self.full()
        self.assertEqual(r["missing"], [])
        self.assertEqual(self.entry_at("file://AI 產出/x.md")["status"], "turn_reverted")


class Ooxml(Base):
    def setUp(self):
        super().setUp()
        self.p = self.raw / "客戶案件" / "大安陳" / "進度.xlsx"
        xlsx(self.p, ("信義王", "1.1"))
        self.full()
        self.uri = "file://客戶案件/大安陳/進度.xlsx"
        self.before = self.entry_at(self.uri)

    def test_new_ooxml_entry_has_text_fingerprint(self):
        fp = self.before["fingerprints"][0]
        self.assertEqual(set(fp), {"sha256", "text_sha256", "time", "turn"})
        self.assertRegex(fp["text_sha256"], r"^[0-9a-f]{64}$")

    def test_bytes_change_same_content_is_fake(self):
        xlsx(self.p, ("信義王", "1.1000000000000001"), inline=True, order=[1, 0], stamp="2026-10-16")
        self.bump_mtime(self.p)
        r = self.scan()
        self.assertEqual(r["modified"], [])
        inventory.apply(self.V, r, self.t["turn"])
        e = self.entry_at(self.uri)
        self.assertEqual(len(e["fingerprints"]), 1)
        self.assertNotEqual(e["fingerprints"][0]["sha256"], self.before["fingerprints"][0]["sha256"])
        self.assertEqual(e["fingerprints"][0]["sha256"], inventory.sha256_of(self.p))
        self.assertEqual(e["fingerprints"][0]["text_sha256"], self.before["fingerprints"][0]["text_sha256"])
        self.assertEqual(e["locations"][0]["size"], self.p.stat().st_size)

    def test_content_change_is_modified_with_new_text_fingerprint(self):
        xlsx(self.p, ("信義王", "2.5"))
        self.bump_mtime(self.p)
        r = self.full()
        self.assertEqual(r["modified"], [self.uri])
        e = self.entry_at(self.uri)
        self.assertEqual(len(e["fingerprints"]), 2)
        self.assertNotEqual(e["fingerprints"][1]["text_sha256"], e["fingerprints"][0]["text_sha256"])

    def test_doc_bytes_change_is_modified(self):
        doc = self.raw / "客戶案件" / "舊.doc"
        doc.write_bytes(b"\xd0\xcf\x11\xe0 old doc")
        self.full()
        doc.write_bytes(b"\xd0\xcf\x11\xe0 old doc saved")
        self.assertEqual(self.scan()["modified"], ["file://客戶案件/舊.doc"])


class MissingRoot(Base):
    def test_renamed_source_root_keeps_entries(self):
        self.full()
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        r = self.scan()
        self.assertEqual(r["missing_sources"], [{"alias": "客戶案件", "spec": "../raw/客戶案件", "id": "src-3f9a"}])
        self.assertEqual(r["missing"], [])
        self.assertNotIn("客戶案件", r["by_source"])
        inventory.apply(self.V, r, self.t["turn"])
        e = self.entry_at("file://客戶案件/內湖李/估價單.pdf")
        self.assertEqual(e["status"], "active")
        (self.raw / "客戶案件_舊").rename(self.raw / "客戶案件")
        r = self.scan()
        self.assertEqual((r["missing_sources"], r["missing"], r["added"]), ([], [], []))

    def test_unresolved_spec_is_missing_source_but_exclude_is_not(self):
        os.environ.pop("KB_TEST_NOPE", None)
        p = self.V / ".system" / "sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sources"].append({"id": "src-9999", "alias": "外接", "spec": "${KB_TEST_NOPE}/x", "excludes": []})
        data["sources"][0]["excludes"].append({"spec": "${KB_TEST_NOPE}/y", "kind": "hidden"})
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r = self.scan()
        self.assertEqual(r["missing_sources"], [{"alias": "外接", "spec": "${KB_TEST_NOPE}/x", "id": "src-9999"}])


class Quick(Base):
    def setUp(self):
        super().setUp()
        self.full()
        self.hashed = []
        real = inventory.sha256_of

        def spy(path, **kw):
            self.hashed.append(Path(path).name)
            return real(path, **kw)
        inventory.sha256_of = spy
        self.addCleanup(setattr, inventory, "sha256_of", real)

    def test_new_file_with_unknown_size_is_not_hashed(self):
        (self.raw / "客戶案件" / "新案.txt").write_text("一個大小獨特的新檔案內容。" * 7, encoding="utf-8")
        r = self.scan(quick=True)
        self.assertEqual(r["added"], ["file://客戶案件/新案.txt"])
        self.assertEqual(self.hashed, [])

    def test_move_is_recognized_by_size(self):
        shutil.move(self.raw / "客戶案件" / "內湖李" / "估價單.pdf", self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        r = self.scan(quick=True)
        self.assertEqual(r["moved"], [["file://客戶案件/內湖李/估價單.pdf", "file://客戶案件/信義王/估價單.pdf"]])
        self.assertEqual(self.hashed, ["估價單.pdf"])

    def test_copy_of_known_file_is_duplicate_not_added(self):
        """疑義 3 提案：快速模式對大小等於已知位置的新檔也算指紋，複製的重複檔不算新增。"""
        shutil.copy2(self.raw / "客戶案件" / "信義王" / "報價_v2.pdf", self.raw / "客戶案件" / "大安陳" / "報價_v2.pdf")
        r = self.scan(quick=True)
        self.assertEqual(r["added"], [])
        self.assertEqual(len(r["duplicates"]), 1)

    def test_hash_budget_used_up_counts_as_modified_and_added(self):
        """審閱裁定 39：讀取預算用完也 incomplete、不判不見（搬動的新位置沒指紋，舊位置不能報不見）。"""
        self.config(changes_quick_hash_budget_bytes=1)
        p = self.raw / "報價與合約" / "合約範本.txt"
        p.write_text("改過，大小也變了的合約範本。\n", encoding="utf-8")
        shutil.move(self.raw / "客戶案件" / "內湖李" / "估價單.pdf", self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        r = self.scan(quick=True)
        self.assertEqual(r["modified"], ["file://報價與合約/合約範本.txt"])
        self.assertEqual(r["added"], ["file://客戶案件/信義王/估價單.pdf"])
        self.assertEqual(r["missing"], [])
        self.assertTrue(r["incomplete"])
        self.assertEqual(self.hashed, [])

    def test_full_mode_ignores_hash_budget(self):
        self.config(changes_quick_hash_budget_bytes=1)
        shutil.move(self.raw / "客戶案件" / "內湖李" / "估價單.pdf", self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        self.assertEqual(len(self.scan()["moved"]), 1)

    def test_time_budget_zero_is_incomplete_and_unvisited_not_missing(self):
        self.config(changes_quick_seconds=0)
        r = self.scan(quick=True)
        self.assertTrue(r["incomplete"])
        self.assertEqual(r["missing"], [])
        self.assertFalse(self.scan()["incomplete"])

    def test_time_runs_out_in_the_middle_of_one_file(self):
        """審閱裁定 34：分塊讀、每塊查時間；算到一半時間到 → 放棄這個檔（不追加指紋）、incomplete。"""
        real_chunk = inventory.HASH_CHUNK
        inventory.HASH_CHUNK = 4
        self.addCleanup(setattr, inventory, "HASH_CHUNK", real_chunk)
        now = [0]
        real_clock = inventory._clock
        inventory._clock = lambda: now[0]
        self.addCleanup(setattr, inventory, "_clock", real_clock)
        spy = inventory.sha256_of

        def slow(path, **kw):
            now[0] = 100  # 開始讀之後時間才到（開始前那次檢查還沒到）
            return spy(path, **kw)
        inventory.sha256_of = slow
        p = self.raw / "報價與合約" / "合約範本.txt"
        p.write_text("改過的合約範本，長度也不同，比四個位元組長很多。\n", encoding="utf-8")
        before = self.entry_at("file://報價與合約/合約範本.txt")
        r = self.scan(quick=True)
        self.assertTrue(r["incomplete"])
        self.assertEqual(self.hashed, ["合約範本.txt"])
        after = [e for e in r["entries"] if e["id"] == before["id"]][0]
        self.assertEqual(after["fingerprints"], before["fingerprints"])

    def test_time_budget_also_stops_hashing(self):
        """審閱裁定 31：走訪內沒到、算指紋時到 → incomplete、後面的檔不再算指紋、不報不見（搬動認不出來）。"""
        now = [0]
        real_clock = inventory._clock
        inventory._clock = lambda: now[0]
        self.addCleanup(setattr, inventory, "_clock", real_clock)
        spy = inventory.sha256_of

        def slow(path, **kw):
            now[0] = 100  # 第一個指紋算完時間就到了
            return spy(path, **kw)
        inventory.sha256_of = slow
        shutil.move(self.raw / "客戶案件" / "內湖李" / "估價單.pdf", self.raw / "客戶案件" / "信義王" / "估價單.pdf")
        shutil.move(self.raw / "公司行政" / "舊檔" / "Big5備忘.txt", self.raw / "公司行政" / "Big5備忘.txt")
        r = self.scan(quick=True)
        self.assertTrue(r["incomplete"])
        self.assertEqual(len(self.hashed), 1)
        self.assertEqual(len(r["moved"]), 1)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["total"], 14, "走訪本身走完了")

    def test_time_budget_also_stops_ooxml_text(self):
        p = self.raw / "客戶案件" / "大安陳" / "進度.xlsx"
        xlsx(p, ("信義王", "1.1"))
        self.full()
        xlsx(p, ("信義王", "1.1"), inline=True, stamp="2026-10-17")
        self.bump_mtime(p)
        now = [0]
        real_clock = inventory._clock
        inventory._clock = lambda: now[0]
        self.addCleanup(setattr, inventory, "_clock", real_clock)
        spy = inventory.sha256_of

        def slow(path, **kw):
            now[0] = 100
            return spy(path, **kw)
        inventory.sha256_of = slow
        texts = []
        real_text = inventory.content_fp.text_fingerprint
        inventory.content_fp.text_fingerprint = lambda path, **kw: texts.append(path) or real_text(path, **kw)
        self.addCleanup(setattr, inventory.content_fp, "text_fingerprint", real_text)
        r = self.scan(quick=True)
        self.assertTrue(r["incomplete"])
        self.assertEqual(texts, [])

    def test_time_budget_with_patched_clock(self):
        ticks = iter(range(0, 10_000, 3))
        real = inventory._clock
        inventory._clock = lambda: next(ticks)
        self.addCleanup(setattr, inventory, "_clock", real)
        r = self.scan(quick=True)
        self.assertTrue(r["incomplete"])
        self.assertLess(r["total"], 14)
        self.assertEqual(r["missing"], [])


class Reconcile(Base):
    def setUp(self):
        super().setUp()
        self.full()

    def edit_sources(self, fn):
        p = self.V / ".system" / "sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        fn(data)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_unused_exclude_marks_entries_excluded(self):
        """unused 照裁定 21：位置全沒了標 excluded、保留最後的位置清單。"""
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "unused"}))
        t2 = turn.start(self.V, None, "")
        got = inventory.reconcile(self.V, self.sources(), t2["turn"])
        self.assertEqual(got, {"excluded": 2, "removed": 0})
        for uri in ("file://公司行政/薪資/薪轉帳號.csv", "file://公司行政/薪資/網銀密碼.txt"):
            self.assertEqual(self.entry_at(uri)["status"], "excluded")
        self.assertEqual(self.entry_at("file://公司行政/舊檔/Big5備忘.txt")["status"], "active")
        self.assertTrue((self.V / ".system" / "versions" / t2["turn"] / "files" / ".system" / "inventory.json").is_file())

    def test_removed_source_drops_only_its_locations(self):
        self.edit_sources(lambda d: d["sources"].pop(1))
        got = inventory.reconcile(self.V, self.sources(), self.t["turn"])
        self.assertEqual(got, {"excluded": 1, "removed": 0})
        self.assertEqual(self.entry_at("file://報價與合約/合約範本.txt")["status"], "excluded")
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual(e["status"], "active")
        self.assertEqual([l["uri"] for l in e["locations"]], ["file://客戶案件/信義王/報價_v2.pdf"])

    def test_reserved_aliases_are_not_touched(self):
        """審閱裁定 11。"""
        self.edit_sources(lambda d: d["sources"].clear())
        inventory.reconcile(self.V, self.sources(), self.t["turn"])
        for uri in ("file://知識庫/案子/說明.txt", "file://收件匣/貼文.txt", "file://AI 產出/x.md"):
            self.assertEqual(self.entry_at(uri)["status"], "active")

    def test_unresolved_source_is_not_treated_as_removed(self):
        os.environ.pop("KB_TEST_NOPE", None)
        self.edit_sources(lambda d: d["sources"][1].update(spec="${KB_TEST_NOPE}/x"))
        self.assertEqual(inventory.reconcile(self.V, self.sources(), self.t["turn"]), {"excluded": 0, "removed": 0})

    def test_nothing_to_do_writes_nothing(self):
        before = (self.V / ".system" / "inventory.json").read_bytes()
        t2 = turn.start(self.V, None, "")
        self.assertEqual(inventory.reconcile(self.V, self.sources(), t2["turn"]), {"excluded": 0, "removed": 0})
        self.assertEqual((self.V / ".system" / "inventory.json").read_bytes(), before)
        self.assertEqual(turn.entries(self.V, t2["turn"]), [])

    def test_include_brings_excluded_back_active(self):
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "unused"}))
        inventory.reconcile(self.V, self.sources(), self.t["turn"])
        ids = {self.entry_at(u)["id"] for u in ("file://公司行政/薪資/薪轉帳號.csv", "file://公司行政/薪資/網銀密碼.txt")}
        self.edit_sources(lambda d: d["sources"][2]["excludes"].clear())
        r = self.scan(only_roots=["公司行政"])
        self.assertEqual(sorted(r["restored"]), ["file://公司行政/薪資/網銀密碼.txt", "file://公司行政/薪資/薪轉帳號.csv"])
        self.assertEqual(r["added"], [])
        inventory.apply(self.V, r, self.t["turn"])
        for uri in ("file://公司行政/薪資/薪轉帳號.csv", "file://公司行政/薪資/網銀密碼.txt"):
            e = self.entry_at(uri)
            self.assertEqual(e["status"], "active")
            self.assertIn(e["id"], ids)

    def test_changed_credential_file_is_not_restored_by_location(self):
        """審閱裁定 21：位置鍵是 uri 加大小時間；沒指紋的檔換了內容就不是原來那筆。"""
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "unused"}))
        inventory.reconcile(self.V, self.sources(), self.t["turn"])
        old = self.entry_at("file://公司行政/薪資/網銀密碼.txt")
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").write_text("換過的內容，長度不同\n", encoding="utf-8")
        self.edit_sources(lambda d: d["sources"][2]["excludes"].clear())
        r = self.scan(only_roots=["公司行政"])
        self.assertEqual(r["restored"], ["file://公司行政/薪資/薪轉帳號.csv"])
        self.assertEqual(r["added"], ["file://公司行政/薪資/網銀密碼.txt"])
        inventory.apply(self.V, r, self.t["turn"])
        self.assertEqual([e["status"] for e in self.entries() if e["id"] == old["id"]], ["excluded"])

    def test_hidden_exclude_leaves_no_trace(self):
        """審閱裁定 40、測試報告觀察 E：hidden 排除的位置直接拿掉，條目沒位置剩下就整筆刪，清冊裡找不到那些檔名。"""
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "hidden"}))
        t2 = turn.start(self.V, None, "")
        got = inventory.reconcile(self.V, self.sources(), t2["turn"])
        self.assertEqual(got, {"excluded": 0, "removed": 2})
        text = (self.V / ".system" / "inventory.json").read_text(encoding="utf-8")
        self.assertNotIn("薪資", text)
        self.assertEqual(len(self.entries()), 11)
        self.assertEqual(self.entry_at("file://公司行政/舊檔/Big5備忘.txt")["status"], "active")

    def test_hidden_exclude_drops_only_that_location_of_a_duplicate(self):
        self.edit_sources(lambda d: d["sources"][1]["excludes"].append({"spec": "信義王", "kind": "hidden"}))
        got = inventory.reconcile(self.V, self.sources(), self.t["turn"])
        self.assertEqual(got, {"excluded": 0, "removed": 0})
        e = self.entry_at("file://客戶案件/信義王/報價_v2.pdf")
        self.assertEqual((e["status"], [l["uri"] for l in e["locations"]]), ("active", ["file://客戶案件/信義王/報價_v2.pdf"]))
        self.assertNotIn("報價與合約/信義王", (self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))

    def test_hidden_exclude_also_drops_missing_entries(self):
        (self.raw / "公司行政" / "薪資" / "薪轉帳號.csv").unlink()
        self.full()
        self.assertEqual(self.entry_at("file://公司行政/薪資/薪轉帳號.csv")["status"], "missing")
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "hidden"}))
        self.assertEqual(inventory.reconcile(self.V, self.sources(), self.t["turn"]), {"excluded": 0, "removed": 2})
        self.assertNotIn("薪資", (self.V / ".system" / "inventory.json").read_text(encoding="utf-8"))

    def test_include_of_hidden_exclude_rescans_as_new(self):
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "hidden"}))
        inventory.reconcile(self.V, self.sources(), self.t["turn"])
        self.edit_sources(lambda d: d["sources"][2]["excludes"].clear())
        r = self.scan(only_roots=["公司行政"])
        self.assertEqual(r["restored"], [])
        self.assertEqual(sorted(r["added"]), ["file://公司行政/薪資/網銀密碼.txt", "file://公司行政/薪資/薪轉帳號.csv"])

    def test_excluded_folder_is_not_walked(self):
        self.edit_sources(lambda d: d["sources"][2]["excludes"].append({"spec": "薪資", "kind": "unused"}))
        inventory.reconcile(self.V, self.sources(), self.t["turn"])
        r = self.scan()
        self.assertEqual((r["added"], r["restored"], r["missing"]), ([], [], []))
        self.assertEqual(r["total"], 12)


class Apply(Base):
    def test_first_apply_records_create_then_backup_copy(self):
        self.full()
        rows = turn.entries(self.V, self.t["turn"])
        self.assertEqual([(r["path"], r["action"]) for r in rows], [(".system/inventory.json", "create")])
        t2 = turn.start(self.V, None, "")
        before = (self.V / ".system" / "inventory.json").read_bytes()
        inventory.apply(self.V, self.scan(), t2["turn"])
        copy = self.V / ".system" / "versions" / t2["turn"] / "files" / ".system" / "inventory.json"
        self.assertEqual(copy.read_bytes(), before)

    def test_apply_keeps_entries_registered_after_scan(self):
        r = self.scan()
        rel = "AI 產出/新成品.md"
        turn.backup(self.V, rel, self.t["turn"])
        (self.V / rel).write_text("新成品\n", encoding="utf-8")
        registered = inventory.register_output(self.V, rel, self.t["turn"])
        inventory.apply(self.V, r, self.t["turn"])
        self.assertIn(registered["id"], {e["id"] for e in self.entries()})

    def test_sha256_of_reads_in_chunks_and_can_stop(self):
        """審閱裁定 34：第一塊之後每塊前問 should_stop；喊停回 None；不喊停跟一次讀完一樣。"""
        import hashlib
        p = self.tmp / "big.bin"
        p.write_bytes(os.urandom(inventory.HASH_CHUNK * 3 + 10))
        want = hashlib.sha256(p.read_bytes()).hexdigest()
        asked = []
        self.assertEqual(inventory.sha256_of(p, should_stop=lambda: asked.append(1) and False), want)
        self.assertEqual(len(asked), 3)
        self.assertEqual(inventory.sha256_of(p), want)
        self.assertIsNone(inventory.sha256_of(p, should_stop=lambda: True))
        small = self.tmp / "small.bin"
        small.write_bytes(b"x")
        self.assertEqual(inventory.sha256_of(small, should_stop=lambda: True), hashlib.sha256(b"x").hexdigest())

    def test_path_of(self):
        s = self.sources()
        self.assertEqual(inventory.path_of(self.V, s, "file://報價與合約/合約範本.txt"),
                         Path(s.entries[1]["root"]) / "合約範本.txt")
        self.assertEqual(inventory.path_of(self.V, s, "file://AI 產出/x.md"), self.V / "AI 產出" / "x.md")
        self.assertEqual(inventory.path_of(self.V, s, "file://知識庫/案子/說明.txt"), self.V / "案子" / "說明.txt")
        self.assertEqual(inventory.path_of(self.V, s, "file://收件匣/貼文.txt"), self.V / "收件匣" / "貼文.txt")
        self.assertIsNone(inventory.path_of(self.V, s, "file://沒有/x.txt"))
        self.assertTrue(inventory.path_of(self.V, s, "file://報價與合約/合約範本.txt").is_file())


class ErrorFolder(Base):
    @unittest.skipUnless(os.name == "nt", "icacls 只在 Windows")
    def test_unopenable_folder_is_reported_and_others_go_on(self):
        self.full()
        locked = self.raw / "客戶案件" / "大安陳"
        r = subprocess.run(["icacls", str(locked), "/deny", f"{os.environ.get('USERNAME', '')}:(OI)(CI)(RD)"],
                           capture_output=True)
        if r.returncode != 0:
            self.skipTest("icacls 設不了拒絕")
        self.addCleanup(subprocess.run, ["icacls", str(locked), "/reset", "/T", "/Q"], capture_output=True)
        try:
            os.listdir(locked)
            self.skipTest("拒絕沒有生效（可能是系統管理員）")
        except PermissionError:
            pass
        (self.raw / "報價與合約" / "新的.txt").write_text("新\n", encoding="utf-8")
        report = self.scan()
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["path"], "file://客戶案件/大安陳")
        self.assertTrue(report["errors"][0]["error"].startswith("無法開啟資料夾："))
        self.assertEqual(report["added"], ["file://報價與合約/新的.txt"])
        self.assertEqual(report["missing"], [], "開不了的夾底下的位置不算不見（疑義 6 提案）")


if __name__ == "__main__":
    unittest.main()
