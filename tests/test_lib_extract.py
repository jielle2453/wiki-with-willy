"""_lib/extract（D-099，specs/2026-10-17-ingest製卡.md 第 1 點）：OOXML 與純文字抽成人讀的文字、probe、pdf_pages。
測試檔一律由 tests/_ooxml.py 組進 tmp；content_fp 共用抽字後指紋要跟改前一樣（下面寫死的雜湊是改前算的）。
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import _ooxml
from tests._guard_fixture import SCRIPTS  # noqa: F401  加 sys.path
from kb._lib import content_fp, extract  # noqa: E402

DOCX_BLOCKS = [("h", "第一章 概要"), ("p", "第一段"), ("tbl", [["品項", "金額"], ["木作", "120000"]]), ("p", "第二段")]
XLSX_SHEETS = [
    ("總表", [[("s", "品項"), ("inline", "金額")], None, [("s", "木作"), ("n", "360000.0")],
              [("s", "合計"), ("f", "SUM(B1:B3)", "360000")], [("s", "未算"), ("f", "SUM(A1:A3)", None)]]),
    ("明細", [[("inline", "日期"), ("n", "46000")], [("b", "1"), ("n", "1.5")]]),
]
PPTX_SLIDES = [["封面", "信義王提案"], ["第二張"]]
PPTX_NOTES = {2: ["講者提醒"]}
# 改前（content_fp 還沒共用 extract 時）對同一份檔算出的內容指紋
FP_BEFORE = {
    "docx": "bee2a33c3888cefde30e7e3efbcfc57c0a8780727b81e84431c1140f70c91b71",
    "xlsx": "f54eadd6f1bea067b466b8bdbbcf9987a5d53aee92ceac14c6d923e4282952d9",
    "pptx": "745c8e91f99687ace91861b51157a4db2677fdd5c12308720bdc2a529225adf0",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbext-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Docx(Base):
    def test_order_table_heading(self):
        text = extract.docx_text(_ooxml.docx(self.tmp / "a.docx", DOCX_BLOCKS))
        self.assertEqual(text.splitlines(), ["# 第一章 概要", "第一段", "品項 | 金額", "木作 | 120000", "第二段"])

    def test_chinese_heading_style(self):
        text = extract.docx_text(_ooxml.docx(self.tmp / "a.docx", [("h", "標題甲", "標題 1"), ("p", "內文")]))
        self.assertEqual(text.splitlines(), ["# 標題甲", "內文"])

    def test_broken_zip_unreadable(self):
        p = self.tmp / "bad.docx"
        p.write_bytes(b"PK\x03\x04fake")
        with self.assertRaises(extract.Unreadable) as cm:
            extract.docx_text(p)
        self.assertTrue(str(cm.exception).startswith("檔案壞了："), str(cm.exception))

    def test_missing_main_member_unreadable(self):
        p = _ooxml._zip(self.tmp / "x.docx", [("docProps/core.xml", "<a/>")])
        with self.assertRaises(extract.Unreadable):
            extract.docx_text(p)


class Xlsx(Base):
    def setUp(self):
        super().setUp()
        self.text = extract.xlsx_text(_ooxml.xlsx(self.tmp / "a.xlsx", XLSX_SHEETS))
        self.lines = self.text.splitlines()

    def test_sheet_headers_in_workbook_order(self):
        heads = [ln for ln in self.lines if ln.startswith("## ")]
        self.assertEqual(heads, ["## 工作表「總表」", "## 工作表「明細」"])

    def test_rows_carry_real_row_number(self):
        """實作疑義裁定 1：每行開頭「第 N 列：」，N 是真實列號，空列跳過不影響。"""
        self.assertEqual(self.lines, [
            "## 工作表「總表」",
            "第 1 列：品項 | 金額",
            "第 3 列：木作 | 360000",
            "第 4 列：合計 | 360000",
            "第 5 列：未算 | =SUM(A1:A3)（無快取值）",
            "",
            "## 工作表「明細」",
            "第 1 列：日期 | 46000",
            "第 2 列：1 | 1.5",
        ])

    def test_stops_at_max_chars_and_marks_where(self):
        rows = [[("inline", f"第{i}列內容很長很長很長")] for i in range(1, 200)]
        p = _ooxml.xlsx(self.tmp / "big.xlsx", [("大表", rows)])
        text = extract.xlsx_text(p, max_chars=300)
        self.assertLess(len(text), 400)
        self.assertRegex(text.splitlines()[-1], r"^（只抽到工作表「大表」第 \d+ 列）$")


class Pptx(Base):
    def test_slides_and_notes(self):
        text = extract.pptx_text(_ooxml.pptx(self.tmp / "a.pptx", PPTX_SLIDES, PPTX_NOTES))
        self.assertEqual(text.splitlines(), ["## 投影片 1", "封面", "信義王提案", "", "## 投影片 2", "第二張",
                                             "（備忘稿）講者提醒"])


class Plain(Base):
    def test_utf8_sig_strips_bom(self):
        p = self.tmp / "a.txt"
        p.write_bytes("﻿備忘一".encode("utf-8"))
        self.assertEqual(extract.plain_text(p), ("備忘一", "utf-8-sig"))

    def test_utf8(self):
        p = self.tmp / "a.txt"
        p.write_bytes("備忘一".encode("utf-8"))
        self.assertEqual(extract.plain_text(p), ("備忘一", "utf-8"))

    def test_cp950(self):
        p = _ooxml.cp950_text(self.tmp / "Big5備忘.txt", "舊合約備忘：付款三期。")
        self.assertEqual(extract.plain_text(p), ("舊合約備忘：付款三期。", "cp950"))

    def test_utf16_only_with_bom(self):
        p = self.tmp / "a.txt"
        p.write_bytes("﻿報價".encode("utf-16-le"))
        text, enc = extract.plain_text(p)
        self.assertEqual((text, enc), ("報價", "utf-16"))

    def test_undecodable_raises_no_replacement_char(self):
        p = self.tmp / "a.txt"
        p.write_bytes("溫度 25".encode("utf-8") + b"\xb0C \xb1")  # cp1252 的 ° ±，結尾落單的前導位元組
        with self.assertRaises(extract.Unreadable) as cm:
            extract.plain_text(p)
        self.assertEqual(str(cm.exception), "編碼")
        self.assertNotIn("�", str(cm.exception))


class Probe(Base):
    def kind(self, name, data=None, maker=None):
        p = self.tmp / name
        if maker:
            maker(p)
        else:
            p.write_bytes(data if data is not None else b"x")
        return extract.probe(p)

    def test_ooxml(self):
        self.assertEqual(self.kind("a.docx", maker=lambda p: _ooxml.docx(p, [("p", "x")])), "docx")
        self.assertEqual(self.kind("a.XLSM", maker=lambda p: _ooxml.xlsx(p, [("s", [[("n", "1")]])])), "xlsx")
        self.assertEqual(self.kind("a.pptx", maker=lambda p: _ooxml.pptx(p, [["x"]])), "pptx")

    def test_legacy_and_encrypted(self):
        self.assertEqual(self.kind("x.doc", maker=_ooxml.cfb), "legacy_office")
        self.assertEqual(self.kind("x.xls", maker=_ooxml.cfb), "legacy_office")
        self.assertEqual(self.kind("x.docx", maker=_ooxml.cfb), "encrypted")
        self.assertEqual(self.kind("x.pdf", maker=_ooxml.encrypted_pdf), "encrypted")

    def test_doc_without_cfb_is_unsupported(self):
        self.assertEqual(self.kind("x.doc", b"{\\rtf1 hi}"), "unsupported")

    def test_pdf_text_image_unsupported(self):
        self.assertEqual(self.kind("x.pdf", maker=_ooxml.pdf), "pdf")
        for name in ("a.txt", "a.md", "a.CSV", "a.tsv", "a.log", "a.json"):
            self.assertEqual(self.kind(name), "text", name)
        for name in ("a.PNG", "a.jpg", "a.jpeg", "a.gif", "a.webp"):
            self.assertEqual(self.kind(name), "image", name)
        self.assertEqual(self.kind("a.zip", b"PK\x03\x04"), "unsupported")
        self.assertEqual(self.kind("空檔.bin", b""), "unsupported")


class PdfPages(Base):
    def test_counts_page_not_pages(self):
        self.assertEqual(extract.pdf_pages(_ooxml.pdf(self.tmp / "a.pdf", pages=3)), 3)

    def test_unknown_is_none(self):
        p = self.tmp / "a.pdf"
        p.write_bytes(b"%PDF-1.4\n fake quote v2\n%%EOF\n")
        self.assertIsNone(extract.pdf_pages(p))


class SharedWithContentFp(Base):
    def test_fingerprints_unchanged(self):
        files = {"docx": _ooxml.docx(self.tmp / "a.docx", DOCX_BLOCKS),
                 "xlsx": _ooxml.xlsx(self.tmp / "a.xlsx", XLSX_SHEETS),
                 "pptx": _ooxml.pptx(self.tmp / "a.pptx", PPTX_SLIDES, PPTX_NOTES)}
        for kind, p in files.items():
            with self.subTest(kind=kind):
                self.assertEqual(content_fp.text_fingerprint(p), FP_BEFORE[kind])

    def test_content_fp_uses_extract_helpers(self):
        for name in ("_shared_strings", "_cell_value", "_number", "_paragraphs"):
            self.assertIs(getattr(content_fp, name), getattr(extract, name), name)


MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


class AfterTest(Base):
    """測試後修正裁定 24、27、28、30。"""

    def test_27_textbox_once_and_fallback_skipped_and_br(self):
        tb = '<w:txbxContent><w:p><w:r><w:t>Inner</w:t></w:r></w:p></w:txbxContent>'
        body = (f'<w:p><w:r><w:t>Outer</w:t></w:r><w:r><mc:AlternateContent><mc:Choice Requires="wps">{tb}'
                f'</mc:Choice><mc:Fallback>{tb}</mc:Fallback></mc:AlternateContent></w:r></w:p>'
                '<w:p><w:r><w:t>A</w:t><w:br/><w:t>B</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>C</w:t><w:br/><w:t>D</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        xml = (f'<?xml version="1.0"?><w:document xmlns:w="{_ooxml.W}" xmlns:mc="{MC}"><w:body>{body}'
               '</w:body></w:document>')
        p = _ooxml._zip(self.tmp / "tb.docx", [("word/document.xml", xml)])
        lines = extract.docx_text(p).splitlines()
        self.assertEqual(lines, ["Outer Inner", "A", "B", "C D"])

    def test_30_first_row_truncation_no_row_zero(self):
        p = _ooxml.xlsx(self.tmp / "big.xlsx", [("大表", [[("inline", "很長" * 100)], [("inline", "x")]])])
        text = extract.xlsx_text(p, max_chars=50)
        self.assertNotIn("第 0 列", text)
        self.assertEqual(text.splitlines()[-1], "（只抽到工作表「大表」第 1 列之前）")

    def test_24_size_limits(self):
        t = self.tmp / "a.txt"
        t.write_text("一二三四五六七八九十", encoding="utf-8")
        with self.assertRaises(extract.Unreadable) as cm:
            extract.plain_text(t, max_bytes=5)
        self.assertEqual(str(cm.exception), "檔案太大")
        self.assertEqual(extract.plain_text(t, max_bytes=1000)[0], "一二三四五六七八九十")
        d = _ooxml.docx(self.tmp / "a.docx", DOCX_BLOCKS)
        x = _ooxml.xlsx(self.tmp / "a.xlsx", XLSX_SHEETS)
        s = _ooxml.pptx(self.tmp / "a.pptx", PPTX_SLIDES)
        for fn, path in ((extract.docx_text, d), (extract.xlsx_text, x), (extract.pptx_text, s)):
            with self.subTest(fn=fn.__name__), self.assertRaises(extract.Unreadable) as cm:
                fn(path, max_bytes=10)
            self.assertEqual(str(cm.exception), "檔案太大")
            fn(path, max_bytes=10 ** 6)

    def test_28_kind_by_name_does_not_open(self):
        self.assertEqual(extract.kind_by_name("x.DOCX"), "docx")
        self.assertEqual(extract.kind_by_name("x.xlsm"), "xlsx")
        self.assertEqual(extract.kind_by_name("x.pptx"), "pptx")
        self.assertEqual(extract.kind_by_name("x.doc"), "legacy_office")
        self.assertEqual(extract.kind_by_name("x.pdf"), "pdf")
        self.assertEqual(extract.kind_by_name("x.csv"), "text")
        self.assertEqual(extract.kind_by_name("x.jpeg"), "image")
        self.assertEqual(extract.kind_by_name("x.zip"), "unsupported")
        self.assertEqual(extract.kind_by_name(self.tmp / "不存在.pdf"), "pdf")


if __name__ == "__main__":
    unittest.main()
