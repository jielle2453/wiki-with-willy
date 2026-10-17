"""_lib/content_fp：OOXML 內容指紋（D-096）。測試裡用 zipfile 自己組最小的 docx、xlsx、pptx（fixture 不放真 Office 檔）。
docx：run 怎麼切不影響、改一個字不同、docProps 不影響；xlsx：共用字串與 inlineStr 相同、數字表示法相同、改值不同、加公式不同、
成員順序打亂不影響；pptx 同理；.doc、壞 zip、缺主成員回 None。
"""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests._guard_fixture import SCRIPTS  # noqa: F401  加 sys.path
from kb._lib import content_fp  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
CORE = '<?xml version="1.0"?><cp:coreProperties xmlns:cp="x"><modified>{}</modified></cp:coreProperties>'


def write_zip(path, members):
    """members：[(名稱, 文字)]，照順序寫進 zip。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members:
            z.writestr(name, text)
    return path


def docx_xml(paragraphs):
    """paragraphs：每段一個 run 清單。"""
    body = "".join("<w:p>" + "".join(f"<w:r><w:t>{t}</w:t></w:r>" for t in runs) + "</w:p>" for runs in paragraphs)
    return f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'


def sheet_xml(cells):
    """cells：[(r, 屬性字串, 內層 xml)]。"""
    row = "".join(f'<c r="{r}"{attrs}>{inner}</c>' for r, attrs, inner in cells)
    return f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row r="1">{row}</row></sheetData></worksheet>'


def shared_xml(strings):
    si = "".join(f"<si><t>{s}</t></si>" for s in strings)
    return f'<?xml version="1.0"?><sst xmlns="{S}">{si}</sst>'


def slide_xml(paragraphs):
    body = "".join("<a:p>" + "".join(f"<a:r><a:t>{t}</a:t></a:r>" for t in runs) + "</a:p>" for runs in paragraphs)
    return (f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>'
            f"{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbfp-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fp(self, name, members):
        return content_fp.text_fingerprint(write_zip(self.tmp / name, members))


class Docx(Base):
    def test_run_split_does_not_matter(self):
        one = self.fp("a.docx", [("word/document.xml", docx_xml([["報價單第一版"], ["第二段"]]))])
        three = self.fp("b.docx", [("word/document.xml", docx_xml([["報價", "單第", "一版"], ["第二段"]]))])
        self.assertIsNotNone(one)
        self.assertRegex(one, r"^[0-9a-f]{64}$")
        self.assertEqual(one, three)

    def test_one_char_changed_differs(self):
        a = self.fp("a.docx", [("word/document.xml", docx_xml([["報價單第一版"]]))])
        b = self.fp("b.docx", [("word/document.xml", docx_xml([["報價單第二版"]]))])
        self.assertNotEqual(a, b)

    def test_paragraph_boundary_matters(self):
        a = self.fp("a.docx", [("word/document.xml", docx_xml([["ab"], ["c"]]))])
        b = self.fp("b.docx", [("word/document.xml", docx_xml([["a"], ["bc"]]))])
        self.assertNotEqual(a, b)

    def test_core_props_change_does_not_matter(self):
        doc = ("word/document.xml", docx_xml([["內容"]]))
        a = self.fp("a.docx", [doc, ("docProps/core.xml", CORE.format("2026-10-01"))])
        b = self.fp("b.docx", [("docProps/core.xml", CORE.format("2026-10-16")), doc])
        self.assertEqual(a, b)

    def test_uppercase_extension_and_docm(self):
        a = self.fp("a.DOCX", [("word/document.xml", docx_xml([["x"]]))])
        b = self.fp("b.docm", [("word/document.xml", docx_xml([["x"]]))])
        self.assertIsNotNone(a)
        self.assertEqual(a, b)


class Xlsx(Base):
    def book(self, name, cells, strings=None, order=None, sheets=None):
        members = [("xl/worksheets/sheet1.xml", sheet_xml(cells))]
        for n, more in (sheets or {}).items():
            members.append((f"xl/worksheets/sheet{n}.xml", sheet_xml(more)))
        if strings is not None:
            members.append(("xl/sharedStrings.xml", shared_xml(strings)))
        members.append(("docProps/core.xml", CORE.format(name)))
        if order:
            members = [members[i] for i in order]
        return self.fp(name, members)

    def test_shared_string_equals_inline_string(self):
        a = self.book("a.xlsx", [("A1", ' t="s"', "<v>0</v>")], strings=["信義王"])
        b = self.book("b.xlsx", [("A1", ' t="inlineStr"', "<is><t>信義王</t></is>")])
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_float_representation_normalized(self):
        a = self.book("a.xlsx", [("B2", "", "<v>1.1</v>")])
        b = self.book("b.xlsx", [("B2", ' t="n"', "<v>1.1000000000000001</v>")])
        self.assertEqual(a, b)

    def test_value_change_differs(self):
        a = self.book("a.xlsx", [("B2", "", "<v>1.1</v>")])
        b = self.book("b.xlsx", [("B2", "", "<v>1.2</v>")])
        self.assertNotEqual(a, b)

    def test_adding_formula_differs(self):
        a = self.book("a.xlsx", [("B2", "", "<v>3</v>")])
        b = self.book("b.xlsx", [("B2", "", "<f>1+2</f><v>3</v>")])
        self.assertNotEqual(a, b)

    def test_member_order_does_not_matter(self):
        cells = [("A1", ' t="s"', "<v>0</v>"), ("B1", "", "<v>5</v>")]
        a = self.book("a.xlsx", cells, strings=["x"], sheets={2: [("A1", ' t="str"', "<v>y</v>")]})
        b = self.book("b.xlsx", cells, strings=["x"], sheets={2: [("A1", ' t="str"', "<v>y</v>")]}, order=[3, 2, 1, 0])
        self.assertEqual(a, b)

    def test_sheet_number_sorted_numerically(self):
        """sheet10 排在 sheet2 後面：換一個工作表的內容會改指紋，兩張表互換也會。"""
        a = self.book("a.xlsx", [("A1", "", "<v>1</v>")], sheets={2: [("A1", "", "<v>2</v>")], 10: [("A1", "", "<v>3</v>")]})
        b = self.book("b.xlsx", [("A1", "", "<v>1</v>")], sheets={2: [("A1", "", "<v>3</v>")], 10: [("A1", "", "<v>2</v>")]})
        self.assertNotEqual(a, b)

    def test_empty_cell_is_skipped(self):
        a = self.book("a.xlsx", [("A1", "", "<v>1</v>")])
        b = self.book("b.xlsx", [("A1", "", "<v>1</v>"), ("C1", ' s="3"', "")])
        self.assertEqual(a, b)

    def test_unparsable_number_keeps_text(self):
        a = self.book("a.xlsx", [("A1", "", "<v>abc</v>")])
        b = self.book("b.xlsx", [("A1", "", "<v>abd</v>")])
        self.assertIsNotNone(a)
        self.assertNotEqual(a, b)


class Pptx(Base):
    def deck(self, name, slides, order=None):
        members = [(f"ppt/slides/slide{n}.xml", slide_xml(paras)) for n, paras in slides]
        members.append(("docProps/core.xml", CORE.format(name)))
        if order:
            members = [members[i] for i in order]
        return self.fp(name, members)

    def test_run_split_and_order(self):
        a = self.deck("a.pptx", [(1, [["簡報標題"]]), (2, [["第二頁"]])])
        b = self.deck("b.pptx", [(1, [["簡報", "標題"]]), (2, [["第二頁"]])], order=[2, 1, 0])
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_text_change_differs(self):
        a = self.deck("a.pptx", [(1, [["簡報標題"]])])
        b = self.deck("b.pptx", [(1, [["簡報標頭"]])])
        self.assertNotEqual(a, b)


class NotOoxml(Base):
    def test_doc_extension_is_none(self):
        self.assertIsNone(self.fp("a.doc", [("word/document.xml", docx_xml([["x"]]))]))

    def test_bad_zip_is_none(self):
        p = self.tmp / "bad.docx"
        p.write_bytes(b"not a zip at all")
        self.assertIsNone(content_fp.text_fingerprint(p))

    def test_missing_main_member_is_none(self):
        self.assertIsNone(self.fp("a.docx", [("docProps/core.xml", CORE.format("x"))]))

    def test_bad_xml_is_none(self):
        self.assertIsNone(self.fp("a.docx", [("word/document.xml", "<w:document><unclosed>")]))

    def test_missing_file_is_none(self):
        self.assertIsNone(content_fp.text_fingerprint(self.tmp / "nope.xlsx"))

    def test_xlsx_without_sheets_is_none(self):
        self.assertIsNone(self.fp("a.xlsx", [("docProps/core.xml", CORE.format("x"))]))


def patch_central(path, offset, value):
    """改中央目錄第一筆的某個 2 位元組欄位（+8 旗標、+10 壓縮方式）。"""
    data = bytearray(Path(path).read_bytes())
    at = data.index(b"PK\x01\x02")
    data[at + offset:at + offset + 2] = value.to_bytes(2, "little")
    Path(path).write_bytes(bytes(data))


class AnyFailureIsNone(Base):
    """審閱裁定 35：zlib.error、加密成員（RuntimeError）、不支援的壓縮方式（NotImplementedError）一律回 None。"""

    def docx(self, name="a.docx"):
        return write_zip(self.tmp / name, [("word/document.xml", docx_xml([["內容" * 200]] * 50))])

    def test_corrupt_deflate_stream(self):
        p = self.docx()
        with zipfile.ZipFile(p) as z:
            info = z.getinfo("word/document.xml")
        data = bytearray(p.read_bytes())
        start = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
        for i in range(start + 5, start + 45):
            data[i] ^= 0xFF
        p.write_bytes(bytes(data))
        self.assertIsNone(content_fp.text_fingerprint(p))

    def test_encrypted_member(self):
        p = self.docx()
        patch_central(p, 8, 0x1)
        self.assertIsNone(content_fp.text_fingerprint(p))

    def test_unsupported_compression(self):
        p = self.docx()
        patch_central(p, 10, 1)
        self.assertIsNone(content_fp.text_fingerprint(p))


class Interruptible(Base):
    """審閱裁定 34：iterparse，每隔一批元素問 should_stop，回真就放棄（None）；不問的時候結果不變。"""

    def big_xlsx(self):
        cells = [(f"A{i}", "", f"<v>{i}</v>") for i in range(1, 6000)]
        return write_zip(self.tmp / "big.xlsx", [("xl/worksheets/sheet1.xml", sheet_xml(cells))])

    def test_stop_gives_none_and_is_asked_more_than_once(self):
        p = self.big_xlsx()
        full = content_fp.text_fingerprint(p)
        self.assertIsNotNone(full)
        asked = []
        self.assertEqual(content_fp.text_fingerprint(p, should_stop=lambda: asked.append(1) and False), full)
        self.assertGreater(len(asked), 2)
        calls = []

        def stop_on_third():
            calls.append(1)
            return len(calls) >= 3
        self.assertIsNone(content_fp.text_fingerprint(p, should_stop=stop_on_third))
        self.assertEqual(len(calls), 3)

    def test_docx_and_pptx_also_ask(self):
        d = write_zip(self.tmp / "a.docx", [("word/document.xml", docx_xml([["x"]] * 5000))])
        s = write_zip(self.tmp / "a.pptx", [("ppt/slides/slide1.xml", slide_xml([["x"]] * 5000))])
        for p in (d, s):
            with self.subTest(p=p.name):
                self.assertIsNone(content_fp.text_fingerprint(p, should_stop=lambda: True))
                self.assertIsNotNone(content_fp.text_fingerprint(p))


if __name__ == "__main__":
    unittest.main()
