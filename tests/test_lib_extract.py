"""_lib/extract（D-099，specs/2026-10-17-ingest製卡.md 第 1 點）：OOXML 與純文字抽成人讀的文字、probe、pdf_pages。
測試檔一律由 tests/_ooxml.py 組進 tmp；content_fp 共用抽字後指紋要跟改前一樣（下面寫死的雜湊是改前算的）。
"""
import os
import shutil
import sys
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


PDF_FIX = Path(__file__).resolve().parent / "fixtures" / "pdf"
TEXT_PDF = PDF_FIX / "文字兩頁.pdf"
IMAGE_PDF = PDF_FIX / "只有圖.pdf"


def has_pdfium():
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def png_size(path):
    """讀 PNG 的 IHDR 寬高，順便把 IDAT 解壓驗長度（RGB、每列一個濾波位元組）。"""
    import struct
    import zlib
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, kind
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
            assert body[8:13] == bytes([8, 2, 0, 0, 0])  # 8 位元、RGB、無交錯
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h = size
    assert len(zlib.decompress(idat)) == h * (1 + w * 3)
    return w, h


class Pdf(Base):
    """測試後修正裁定 36：PDF 走選配 pypdfium2。"""

    def test_missing_package(self):
        import sys
        from unittest import mock
        with mock.patch.dict(sys.modules, {"pypdfium2": None}), \
                mock.patch.object(extract, "_tool_lib", lambda: self.tmp):  # 工具夾也沒有（build：兩處都沒有才印這句）
            with self.assertRaises(extract.Unreadable) as cm:
                extract.pdf_pages_text(TEXT_PDF, 10)
        self.assertEqual(str(cm.exception), "還沒裝 PDF 元件（pip install pypdfium2）")

    @unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
    def test_text_pages_and_scanned(self):
        text, pages, scanned = extract.pdf_pages_text(TEXT_PDF, 10)
        self.assertEqual((pages, scanned), (2, [2]))
        lines = text.splitlines()
        self.assertEqual(lines[0], "<!-- 第 1 頁 -->")
        self.assertIn("Quote v2 total 1280000 NTD", lines)
        self.assertIn("<!-- 第 2 頁 -->", lines)
        self.assertNotIn("\r", text)
        self.assertNotIn("p2", text)  # 掃描頁只留頁碼註解

    @unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
    def test_image_only_pdf_is_all_scanned(self):
        text, pages, scanned = extract.pdf_pages_text(IMAGE_PDF, 10)
        self.assertEqual((pages, scanned), (1, [1]))
        self.assertEqual(text.strip(), "<!-- 第 1 頁 -->")

    @unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
    def test_render_pages_png(self):
        out = extract.render_pages(IMAGE_PDF, [1], self.tmp, "f-0a1b2c3d", 1, 20)
        self.assertEqual([n for n, _p in out], [1])
        path = out[0][1]
        self.assertEqual(Path(path).name, "f-0a1b2c3d_p1.png")
        self.assertEqual(png_size(path), (612, 792))
        out = extract.render_pages(IMAGE_PDF, [1], self.tmp, "x", 2, 20)
        self.assertEqual(png_size(out[0][1]), (1224, 1584))

    @unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
    def test_render_limit(self):
        self.assertEqual(extract.render_pages(TEXT_PDF, [1, 2], self.tmp, "x", 1, 1)[0][0], 1)
        self.assertEqual(len(extract.render_pages(TEXT_PDF, [1, 2], self.tmp, "x", 1, 1)), 1)
        self.assertEqual(extract.render_pages(TEXT_PDF, [1, 2], self.tmp, "x", 1, 0), [])

    @unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
    def test_broken_pdf(self):
        p = self.tmp / "壞.pdf"
        p.write_bytes(b"%PDF-1.4\n fake quote v2\n%%EOF\n")
        with self.assertRaises(extract.Unreadable) as cm:
            extract.pdf_pages_text(p, 10)
        self.assertTrue(str(cm.exception).startswith("檔案壞了"), str(cm.exception))

    def test_fixtures_small_and_clean(self):
        for p in (TEXT_PDF, IMAGE_PDF):
            data = p.read_bytes()
            self.assertLess(len(data), 20 * 1024)
            self.assertNotIn(b"Users", data)
            self.assertNotIn(b"\r\n", data)  # .gitattributes 是 text=auto eol=lf：被當文字時 CRLF 會被改掉、xref 位移就錯



@unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
class PdfChild(unittest.TestCase):
    """build 第 3 點（驗收 4）：本行程 import 不到、工具夾有 pypdfium2（替身：只有一個 pypdfium2 夾，子行程實際載入的是
    這台裝的那份）→ 三支 PDF 函式改在子行程跑；密碼只走 stdin；子行程出錯時記的訊息沒有密碼；只有擁有者密碼的照常讀。"""
    PW = "child-zq7x-9911"

    def setUp(self):
        from tests import _pdfcrypt
        self.tmp = Path(tempfile.mkdtemp(prefix="kbchild-")).resolve()
        self.lib = self.tmp / "pylib"
        (self.lib / "pypdfium2").mkdir(parents=True)
        self.plain = _pdfcrypt.make_pdf(self.tmp / "plain.pdf", "Hello child process")
        self.locked = _pdfcrypt.make_pdf(self.tmp / "locked.pdf", "Locked content here", user=self.PW)
        self.owner = _pdfcrypt.make_pdf(self.tmp / "owner.pdf", "Owner only content", user="")
        self.saved = (extract._tool_lib, extract._run_child, sys.modules.get("pypdfium2"))
        extract._tool_lib = lambda: self.lib
        sys.modules["pypdfium2"] = None
        self.calls = []
        real = self.saved[1]

        def spy(argv, data, env, timeout):
            self.calls.append((list(argv), data, dict(env)))
            return real(argv, data, env, timeout)
        extract._run_child = spy

    def tearDown(self):
        extract._tool_lib, extract._run_child, mod = self.saved
        sys.modules["pypdfium2"] = mod
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_text_via_child(self):
        self.assertTrue(extract.pdf_available())
        text, total, scanned = extract.pdf_pages_text(self.plain, 5)
        self.assertIn("Hello child process", text)
        self.assertEqual((total, scanned), (1, []))
        self.assertEqual(len(self.calls), 1)
        argv, _data, env = self.calls[0]
        self.assertEqual(argv[:2], [sys.executable, "-c"])
        self.assertIn(str(self.lib), env["PYTHONPATH"].split(os.pathsep))

    def test_password_only_on_stdin(self):
        text, _t, _s = extract.pdf_pages_text(self.locked, 5, password=self.PW)
        self.assertIn("Locked content here", text)
        argv, data, env = self.calls[-1]
        self.assertNotIn(self.PW, " ".join(argv))
        self.assertFalse([k for k, v in env.items() if self.PW in str(v) or self.PW in k])
        self.assertIn(self.PW.encode("utf-8"), data)
        with self.assertRaises(extract.Unreadable) as cm:
            extract.pdf_pages_text(self.locked, 5, password="wrong-zq7x")
        self.assertEqual(str(cm.exception), extract.PDF_WRONG_PASSWORD)
        with self.assertRaises(extract.Unreadable) as cm:
            extract.pdf_pages_text(self.locked, 5)
        self.assertEqual(str(cm.exception), extract.PDF_ENCRYPTED)

    def test_owner_only_same_as_in_process(self):
        self.assertTrue(extract.pdf_opens_without_password(self.owner))
        self.assertFalse(extract.pdf_opens_without_password(self.locked))
        self.assertIn("Owner only content", extract.pdf_pages_text(self.owner, 5)[0])

    def test_render_pages_via_child(self):
        got = extract.render_pages(IMAGE_PDF, [1], self.tmp, "img", 0.5, 5)
        self.assertEqual([n for n, _p in got], [1])
        self.assertTrue(Path(got[0][1]).is_file())

    def test_child_error_does_not_leak_password(self):
        V = self.tmp / "vault"
        (V / ".system").mkdir(parents=True)
        (V / ".system" / "sources.json").write_text('{"schema_version": 1, "sources": []}', encoding="utf-8")
        saved_code, cwd = extract._CHILD_CODE, os.getcwd()
        extract._CHILD_CODE = ("import json,sys\nr=json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
                               "sys.stderr.write('Traceback boom password='+r['kw']['password']+' 密碼：'+r['kw']['password'])\n"
                               "sys.exit(1)\n")
        os.chdir(V)
        try:
            with self.assertRaises(extract.Unreadable) as cm:
                extract.pdf_pages_text(self.locked, 5, password=self.PW)
        finally:
            os.chdir(cwd)
            extract._CHILD_CODE = saved_code
        self.assertEqual(str(cm.exception), extract.PDF_CHILD_FAILED)
        log = (V / ".system" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertIn("PDF 子行程出錯", log)
        self.assertIn("Traceback boom", log)
        self.assertNotIn(self.PW, log)

    def test_both_missing_keeps_old_sentence(self):
        shutil.rmtree(self.lib / "pypdfium2")
        self.assertFalse(extract.pdf_available())
        with self.assertRaises(extract.Unreadable) as cm:
            extract.pdf_pages_text(self.plain, 5)
        self.assertEqual(str(cm.exception), extract.PDF_MISSING)
        self.assertEqual(self.calls, [])

    def test_no_child_when_lib_already_on_path(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(self.lib)}):  # 自己就是子行程：不再開
            with self.assertRaises(extract.Unreadable) as cm:
                extract.pdf_pages_text(self.plain, 5)
        self.assertEqual((str(cm.exception), self.calls), (extract.PDF_MISSING, []))


@unittest.skipUnless(has_pdfium(), "沒裝 pypdfium2")
class ConvertViaChild(unittest.TestCase):
    """kb convert 在本行程 import 不到時照樣抽得出（替身：先把 pypdfium2 擋掉、工具夾換成假的再跑 kb.cli），
    帶密碼也照樣開、知識庫與錯誤記錄裡找不到密碼；工具夾也沒有時印原本那句。"""

    WRAP = ("import sys\nfrom pathlib import Path\nsys.path.insert(0, {scripts!r})\nsys.modules['pypdfium2'] = None\n"
            "from kb._lib import extract\nextract._tool_lib = lambda: Path({lib!r})\n"
            "from kb import cli\nsys.exit(cli.main(sys.argv[1:]))\n")

    def setUp(self):
        from tests import _pw
        self._pw = _pw
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        self.lib = self.tmp / "pylib"
        (self.lib / "pypdfium2").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def convert(self, rel, *args):
        import json
        import subprocess
        from tests import _cards
        from tests._guard_fixture import SCRIPTS
        code = self.WRAP.format(scripts=str(SCRIPTS), lib=str(self.lib))
        fid = _cards.entry_of(self.V, rel)["id"]
        r = subprocess.run([sys.executable, "-c", code, "convert", fid, *args, "--json"], capture_output=True,
                           text=True, encoding="utf-8", cwd=str(self.V), env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return r.returncode, json.loads(r.stdout)

    def test_convert_reads_and_opens_with_password(self):
        code, out = self.convert(self._pw.OWNER_ONLY)
        self.assertEqual(code, 0, out)
        self.assertIn("Quotation owner only", Path(out["temp"]).read_text(encoding="utf-8"))
        code, out = self.convert(self._pw.WANG, "--password", self._pw.PDF_PW)
        self.assertEqual(code, 0, out)
        self.assertIn("Insurance policy Wang", Path(out["temp"]).read_text(encoding="utf-8"))
        self.assertEqual(self._pw.leaks(self.V), [])

    def test_both_missing(self):
        shutil.rmtree(self.lib / "pypdfium2")
        code, out = self.convert(self._pw.OWNER_ONLY)
        self.assertEqual((code, out["unreadable"]), (3, extract.PDF_MISSING))


if __name__ == "__main__":
    unittest.main()
