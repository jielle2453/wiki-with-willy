"""kb convert 黑箱（D-099，ingest 製卡第 2 點；驗收 4、5，實作疑義裁定 2、8）：真的跑 CLI。
三種目標寫法、暫存檔 UTF-8 LF、終端機不印內容（D-022）、PDF／圖片指給 Read、讀不到的原因句與 exit 3、寫待處理與不重複、
找不到原檔不寫待處理、排除夾／不在清冊的憑證名／總資料夾外／.system 狀態檔一律拒且不寫暫存、暫存清理、--json 欄位。"""
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import _cards, _ooxml
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import pending  # noqa: E402

MEETING_TEXT = ("大安陳第一次會議", "討論廚房動線", "廚具 | L 型")
JSON_KEYS = {"id", "uri", "path", "kind", "temp", "chars", "parts", "encoding", "pages", "big", "unreadable", "pending_id"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.temp = self.V / ".system" / "temp"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, cwd=None):
        return run_kb(*args, cwd=cwd or self.V)

    def temps(self):
        return sorted(p.name for p in self.temp.iterdir()) if self.temp.is_dir() else []

    def items(self):
        return pending.load(self.V)["items"]

    def scan(self):
        self.assertEqual(self.kb("inventory", "scan").returncode, 0)


class Convert(Base):
    def check_meeting(self, r, entry_id):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("暫存檔："), lines)
        temp = Path(lines[0][len("暫存檔："):])
        self.assertEqual(temp, self.temp.resolve() / f"{entry_id}.md")
        self.assertRegex(lines[1], r"^格式：docx；字數 \d+$")
        data = temp.read_bytes()
        self.assertNotIn(b"\r\n", data)
        text = data.decode("utf-8")
        for want in MEETING_TEXT:
            self.assertIn(want, text)
            self.assertNotIn(want, r.stdout + r.stderr)

    def test_three_target_forms(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        self.check_meeting(self.kb("convert", e["id"]), e["id"])
        self.check_meeting(self.kb("convert", "file://" + _cards.MEETING), e["id"])
        self.check_meeting(self.kb("convert", str(self.raw / _cards.MEETING)), e["id"])
        self.check_meeting(self.kb("convert", os.path.join("..", "raw", *_cards.MEETING.split("/"))), e["id"])

    def test_pdf_points_to_read(self):
        r = self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = os.path.realpath(self.raw / "報價與合約" / "信義王" / "報價_v2.pdf")
        self.assertEqual(r.stdout.splitlines(), [f"PDF：頁數不明；用 Read 讀原檔：{path}"])
        self.assertEqual(self.temps(), [])

    def test_pdf_pages_and_over_limit(self):
        p = _ooxml.pdf(self.raw / "客戶案件" / "型錄.pdf", pages=25)
        self.scan()
        lines = self.kb("convert", str(p)).stdout.splitlines()
        self.assertEqual(lines[0], f"PDF：25 頁；用 Read 讀原檔：{os.path.realpath(p)}")
        self.assertEqual(lines[1], "超過 20 頁：先讀前 20 頁，卡的讀取標「部分」")

    def test_image_points_to_read(self):
        p = self.raw / "客戶案件" / "平面圖.PNG"
        p.write_bytes(b"\x89PNG\r\n")
        self.scan()
        r = self.kb("convert", str(p))
        self.assertEqual(r.stdout.splitlines(), [f"圖片：用 Read 看：{os.path.realpath(p)}"])

    def test_text_cp950_and_xlsx_parts(self):
        p = _ooxml.cp950_text(self.raw / "公司行政" / "舊檔" / "Big5備忘.txt", "舊合約備忘：付款三期。")
        x = _ooxml.xlsx(self.raw / "客戶案件" / "進度.xlsx", [("總表", [[("s", "a")]]), ("明細", [[("n", "1")]]),
                                                              ("備註", [[("inline", "b")]])])
        self.scan()
        lines = self.kb("convert", str(p)).stdout.splitlines()
        self.assertRegex(lines[1], r"^格式：text；字數 11$")
        self.assertIn("編碼：cp950", lines)
        lines = self.kb("convert", str(x)).stdout.splitlines()
        self.assertRegex(lines[1], r"^格式：xlsx；工作表 3；字數 \d+$")

    def test_empty_result(self):
        p = self.raw / "客戶案件" / "空白.txt"
        p.write_bytes(b"")
        self.scan()
        r = self.kb("convert", str(p))
        self.assertEqual(r.returncode, 0)
        self.assertIn("轉換結果是空的", r.stdout.splitlines())

    def test_big_hint(self):
        (self.V / ".system" / "config.json").write_text('{"card_source_big_chars": 10}', encoding="utf-8")
        r = self.kb("convert", "file://" + _cards.MEETING)
        self.assertIn("大檔：照 carding.md 摘要加定位", r.stdout.splitlines())

    def test_json(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        data = json.loads(self.kb("convert", e["id"], "--json").stdout)
        self.assertEqual(set(data), JSON_KEYS)
        self.assertEqual((data["id"], data["uri"], data["kind"]), (e["id"], "file://" + _cards.MEETING, "docx"))
        self.assertTrue(Path(data["temp"]).is_file())
        self.assertIsNone(data["unreadable"])
        pdf = json.loads(self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf", "--json").stdout)
        self.assertEqual((pdf["kind"], pdf["temp"], pdf["pages"]), ("pdf", None, None))

    def test_unregistered_vault_file_uses_fingerprint_name(self):
        """對不到清冊也照轉（總資料夾三區裡的新檔還沒 scan），暫存檔名用指紋前 12 碼，不寫待處理。"""
        p = self.V / "收件匣" / "新貼文.txt"
        p.write_text("新的貼文", encoding="utf-8")
        r = self.kb("convert", str(p))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(Path(r.stdout.splitlines()[0][len("暫存檔："):]).name, r"^[0-9a-f]{12}\.md$")


class Refuse(Base):
    def refused(self, target, first, pending_n=0):
        r = self.kb("convert", str(target))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], first)
        self.assertEqual(self.temps(), [])
        self.assertEqual(len(self.items()), pending_n)
        return r

    def test_credential_entry(self):
        self.refused(self.raw / "公司行政" / "薪資" / "網銀密碼.txt", "讀不到：檔名像密碼或金鑰，預設不讀")
        self.assertFalse((self.V / ".system" / "pending.json").exists())

    def test_legacy_writes_pending_once(self):
        x = _ooxml.cfb(self.raw / "客戶案件" / "x.doc")
        self.scan()
        r = self.refused(x, "讀不到：舊格式（.doc），請另存成新格式", pending_n=1)
        (item,) = self.items()
        self.assertEqual(r.stdout.splitlines()[1], f"已記進待處理（{item['id']}）")
        self.assertEqual((item["kind"], item["reason"]), ("unreadable", "舊格式（.doc）"))
        self.assertEqual(item["text"], "「x.doc」（客戶案件）讀不到")
        md = (self.V / "系統" / "待處理.md").read_text(encoding="utf-8")
        self.assertIn("## 讀不到（1）", md)
        self.refused(x, "讀不到：舊格式（.doc），請另存成新格式", pending_n=1)

    def test_encrypted_and_unsupported(self):
        _ooxml.cfb(self.raw / "客戶案件" / "加密.docx")
        _ooxml.encrypted_pdf(self.raw / "報價與合約" / "合約_加密.pdf")
        (self.raw / "客戶案件" / "a.zip").write_bytes(b"PK\x03\x04")
        self.scan()
        self.refused(self.raw / "客戶案件" / "加密.docx", "讀不到：有密碼", pending_n=1)
        self.refused(self.raw / "報價與合約" / "合約_加密.pdf", "讀不到：有密碼", pending_n=2)
        self.refused(self.raw / "客戶案件" / "a.zip", "讀不到：不支援的格式（.zip）", pending_n=3)

    def test_broken_office_file(self):
        p = self.raw / "客戶案件" / "壞.xlsx"
        p.write_bytes(b"PK\x03\x04broken")
        self.scan()
        r = self.refused(p, "讀不到：檔案壞了：不是完整的 Office 檔", pending_n=1)
        self.assertTrue(r.stdout.splitlines()[1].startswith("已記進待處理"))

    def test_missing_file_no_pending(self):
        """實作疑義裁定 2：找不到原檔只印一行，不寫待處理；status missing 的條目同樣。"""
        e = _cards.entry_of(self.V, _cards.MEETING)
        (self.raw / _cards.MEETING).unlink()
        r = self.refused(e["id"], "讀不到：找不到原檔")
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.scan()
        n = len(self.items())  # scan 開了一條 missing_file
        self.refused(e["id"], "讀不到：找不到原檔", pending_n=n)

    def test_placeholder_writes_pending(self):
        from kb._lib import inventory
        e = _cards.entry_of(self.V, _cards.MEETING)
        data = inventory.load(self.V)
        for x in data["entries"]:
            if x["id"] == e["id"]:
                x["read"] = "placeholder"
        inventory._write(self.V, data["entries"])
        self.refused(e["id"], "讀不到：只在雲端、還沒下載到電腦", pending_n=1)

    def test_excluded_folder(self):
        """實作疑義裁定 8：排除夾裡的檔（清冊對不到）、status excluded 的條目都拒。"""
        e = _cards.entry_of(self.V, _cards.MEETING)
        r = self.kb("folders", "exclude", str(self.raw / "客戶案件" / "大安陳"), "--kind", "unused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.refused(self.raw / _cards.MEETING, "讀不到：這個位置你說過不讀")
        self.refused(e["id"], "讀不到：這個位置你說過不讀")
        (self.raw / "客戶案件" / "大安陳" / "新.txt").write_text("x", encoding="utf-8")
        self.refused(self.raw / "客戶案件" / "大安陳" / "新.txt", "讀不到：這個位置你說過不讀")

    def test_unlisted_credential_name(self):
        p = self.raw / "客戶案件" / "信義王" / "id_rsa"
        p.write_text("x", encoding="utf-8")
        self.refused(p, "讀不到：檔名像密碼或金鑰，預設不讀")

    def test_outside_vault_and_sources(self):
        p = self.tmp / "別處.txt"
        p.write_text("x", encoding="utf-8")
        self.refused(p, "讀不到：不在登記的資料夾裡")

    def test_vault_state_and_cards(self):
        self.refused(self.V / ".system" / "inventory.json", "讀不到：這不是要製卡的檔")
        self.refused(self.V / "系統" / "待處理.md", "讀不到：這不是要製卡的檔")
        self.refused(self.V / "案子" / "信義王" / "信義王.md", "讀不到：這不是要製卡的檔")

    def test_unknown_id(self):
        self.refused("f-00000000", "讀不到：清冊沒有 f-00000000")


class TempCleanup(Base):
    def test_keep_hours_zero_clears_old(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        self.kb("convert", e["id"])
        self.assertEqual(self.temps(), [f"{e['id']}.md"])
        (self.V / ".system" / "config.json").write_text('{"temp_keep_hours": 0}', encoding="utf-8")
        self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf")
        self.assertEqual(self.temps(), [])

    def test_fresh_temp_kept(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        self.kb("convert", e["id"])
        self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf")
        self.assertEqual(self.temps(), [f"{e['id']}.md"])


LOCKED = "讀不到：檔案被別的程式開著或沒有權限，關掉 Word／Excel 再試"


class AfterTest(Base):
    """測試後修正裁定 15～18、24、25（design/reports/ingest製卡-測試.md 的 S1～S3、C1、C4、S7）。"""

    def set_entry(self, rel, **fields):
        from kb._lib import inventory
        e = _cards.entry_of(self.V, rel)
        data = inventory.load(self.V)
        for x in data["entries"]:
            if x["id"] == e["id"]:
                x.update(fields)
        inventory._write(self.V, data["entries"])
        return e

    def config(self, **kw):
        (self.V / ".system" / "config.json").write_text(json.dumps(kw), encoding="utf-8")

    def test_15_no_vault_option(self):
        """S1：不收 --vault，總資料夾只從 cwd 找。"""
        fake = self.tmp  # 報告的重現：在來源的上層放一份空的 sources.json 當假總資料夾
        (fake / ".system").mkdir(parents=True)
        (fake / ".system" / "sources.json").write_text('{"schema_version": 1, "sources": []}', encoding="utf-8")
        r = self.kb("convert", str(self.raw / "公司行政" / "薪資" / "網銀密碼.txt"), "--vault", str(fake))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.temps(), [])
        self.assertFalse((fake / ".system" / "temp").exists())

    def test_15_outside_vault_cwd(self):
        r = self.kb("convert", str(self.raw / _cards.MEETING), cwd=self.tmp)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.splitlines()[0], "這裡不是知識庫；到知識庫裡執行")
        r = self.kb("convert", str(self.raw / _cards.MEETING), "--json", cwd=self.tmp)
        self.assertEqual(r.returncode, 3)
        self.assertTrue(json.loads(r.stdout)["unreadable"])

    def test_16_entry_id_never_becomes_path(self):
        """S2：清冊 id 不合 f-／out- 格式就用指紋當檔名，不會寫到 temp 以外。"""
        md = (self.V / "系統" / "待處理.md").read_bytes()
        self.set_entry(_cards.MEETING, id="../../系統/待處理")
        r = self.kb("convert", str(self.raw / _cards.MEETING))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        temp = Path(r.stdout.splitlines()[0][len("暫存檔："):])
        self.assertRegex(temp.name, r"^[0-9a-f]{12}\.md$")
        self.assertEqual(temp.parent, self.temp.resolve())
        self.assertEqual((self.V / "系統" / "待處理.md").read_bytes(), md)

    @unittest.skipUnless(os.name == "nt", "junction 只在 Windows")
    def test_17_junction_temp_not_cleaned(self):
        """S3：.system/temp 是 junction 就不清，記一行 log。"""
        import subprocess
        target = self.tmp / "別處"
        target.mkdir()
        (target / "留著.txt").write_text("x", encoding="utf-8")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(self.temp), str(target)], check=True, capture_output=True)
        self.config(temp_keep_hours=0)
        self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf")
        self.assertTrue((target / "留著.txt").is_file())
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertIn("temp", log)

    def test_17_refused_call_does_not_clean(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        self.kb("convert", e["id"])
        self.config(temp_keep_hours=0)
        r = self.kb("convert", str(self.raw / "公司行政" / "薪資" / "網銀密碼.txt"))
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.temps(), [f"{e['id']}.md"])

    def test_17_only_plain_files_cleaned(self):
        self.temp.mkdir(parents=True)
        (self.temp / "子夾").mkdir()
        (self.temp / "子夾" / "a.md").write_text("x", encoding="utf-8")
        self.config(temp_keep_hours=0)
        self.kb("convert", "file://報價與合約/信義王/報價_v2.pdf")
        self.assertTrue((self.temp / "子夾" / "a.md").is_file())

    @unittest.skipUnless(os.name == "nt", "msvcrt 只在 Windows")
    def test_18_locked_file(self):
        """C1：原檔被 Word／Excel 鎖住 → exit 3 一句，不寫待處理；--json 也印 JSON。"""
        import msvcrt
        p = self.raw / _cards.MEETING
        with open(p, "r+b") as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, p.stat().st_size)
            try:
                r = self.kb("convert", str(p))
                rj = self.kb("convert", str(p), "--json")
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, p.stat().st_size)
        self.assertEqual((r.returncode, r.stdout.splitlines()), (3, [LOCKED]), r.stderr)
        self.assertEqual(rj.returncode, 3)
        self.assertEqual(json.loads(rj.stdout)["unreadable"], LOCKED[len("讀不到："):])
        self.assertEqual(self.items(), [])

    def test_18_json_on_broken_state(self):
        (self.V / ".system" / "inventory.json").write_text("{", encoding="utf-8")
        r = self.kb("convert", str(self.raw / _cards.MEETING), "--json")
        self.assertEqual(r.returncode, 3)
        self.assertIn("狀態檔壞了", json.loads(r.stdout)["unreadable"])

    def test_24_too_large(self):
        """S7：清冊標 too_large 的擋；抽字超過 fingerprint_max_bytes 的丟檔案太大。"""
        self.set_entry(_cards.MEETING, read="too_large")
        r = self.kb("convert", str(self.raw / _cards.MEETING))
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (3, "讀不到：檔案太大"))
        self.set_entry(_cards.MEETING, read="ok")
        self.config(fingerprint_max_bytes=20)
        r = self.kb("convert", str(self.raw / _cards.MEETING))
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (3, "讀不到：檔案太大"))
        r = self.kb("convert", "file://報價與合約/合約範本.txt")
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (3, "讀不到：檔案太大"))
        self.assertEqual(self.temps(), [])

    def test_25_xlsx_truncated_is_big(self):
        x = _ooxml.xlsx(self.raw / "客戶案件" / "大表.xlsx", [("總表", [[("inline", f"第{i}列" + "很長" * 25)] for i in range(1, 60)])])
        self.scan()
        self.config(card_source_big_chars=100)
        r = self.kb("convert", str(x))
        self.assertIn("大檔：照 carding.md 摘要加定位", r.stdout.splitlines())
        temp = Path(r.stdout.splitlines()[0][len("暫存檔："):])
        self.assertTrue(temp.read_text(encoding="utf-8").rstrip().splitlines()[-1].startswith("（只抽到工作表「總表」"))
        self.assertTrue(json.loads(self.kb("convert", str(x), "--json").stdout)["big"])


if __name__ == "__main__":
    unittest.main()
