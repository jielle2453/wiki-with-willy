"""測試共用：用 zipfile 組真的（最小但結構完整的）docx、xlsx、pptx，與 cp950、CFB、/Encrypt 假檔。
fixture 不放真 Office 檔（tests/fixtures/inventory 的 .docx 是假 zip），要用的測試自己寫進 tmp 複本。
"""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CFB_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
CORE = '<?xml version="1.0"?><cp:coreProperties xmlns:cp="x"><modified>{}</modified></cp:coreProperties>'


def _zip(path, members):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members:
            z.writestr(name, text)
    return path


def _rels(rows):
    body = "".join(f'<Relationship Id="{rid}" Type="x" Target="{target}"/>' for rid, target in rows)
    return f'<?xml version="1.0"?><Relationships xmlns="{PR}">{body}</Relationships>'


def _wp(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def docx(path, blocks, stamp="2026-10-01"):
    """blocks：("p", 文字)｜("h", 文字, 樣式名)｜("tbl", [[格, …], …])。"""
    body = []
    for b in blocks:
        if b[0] == "p":
            body.append(_wp(b[1]))
        elif b[0] == "h":
            body.append(_wp(b[1], b[2] if len(b) > 2 else "Heading1"))
        else:
            rows = "".join("<w:tr>" + "".join(f"<w:tc>{_wp(c)}</w:tc>" for c in row) + "</w:tr>" for row in b[1])
            body.append(f"<w:tbl>{rows}</w:tbl>")
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{"".join(body)}</w:body></w:document>'
    return _zip(path, [("word/document.xml", xml), ("docProps/core.xml", CORE.format(stamp))])


def _cell(ref, spec, strings):
    """spec：None｜("s", 文字)｜("inline", 文字)｜("n", 數字字串)｜("f", 公式, 快取值或 None)｜("b", "1")。"""
    kind = spec[0]
    if kind == "s":
        strings.append(spec[1])
        return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
    if kind == "inline":
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(spec[1])}</t></is></c>'
    if kind == "n":
        return f'<c r="{ref}"><v>{spec[1]}</v></c>'
    if kind == "f":
        v = f"<v>{spec[2]}</v>" if spec[2] is not None else ""
        return f'<c r="{ref}"><f>{escape(spec[1])}</f>{v}</c>'
    if kind == "b":
        return f'<c r="{ref}" t="b"><v>{spec[1]}</v></c>'
    raise ValueError(kind)


def xlsx(path, sheets, stamp="2026-10-01"):
    """sheets：[(名稱, rows)]；rows 是 [[spec, …], …]，第 i 列的 r 是 i+1；整列 None 代表空列（不寫 row）。"""
    strings, members, wb_sheets, rels = [], [], [], []
    for n, (name, rows) in enumerate(sheets, 1):
        xml_rows = []
        for i, row in enumerate(rows, 1):
            if row is None:
                continue
            cells = "".join(_cell(f"{chr(65 + j)}{i}", spec, strings) for j, spec in enumerate(row) if spec is not None)
            xml_rows.append(f'<row r="{i}">{cells}</row>')
        members.append((f"xl/worksheets/sheet{n}.xml",
                        f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'))
        wb_sheets.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
        rels.append((f"rId{n}", f"worksheets/sheet{n}.xml"))
    members.append(("xl/workbook.xml", f'<?xml version="1.0"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                                       f'{"".join(wb_sheets)}</sheets></workbook>'))
    members.append(("xl/_rels/workbook.xml.rels", _rels(rels)))
    if strings:
        si = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
        members.append(("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="{S}">{si}</sst>'))
    members.append(("docProps/core.xml", CORE.format(stamp)))
    return _zip(path, members)


def _ap(paragraphs):
    return "".join(f"<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>" for t in paragraphs)


def pptx(path, slides, notes=None):
    """slides：[[段落, …], …]；notes：{投影片序號(1 起): [段落, …]}。"""
    notes = notes or {}
    members, ids, rels = [], [], []
    for n, paras in enumerate(slides, 1):
        members.append((f"ppt/slides/slide{n}.xml",
                        f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>'
                        f"{_ap(paras)}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"))
        ids.append(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>')
        rels.append((f"rId{n}", f"slides/slide{n}.xml"))
        if n in notes:
            members.append((f"ppt/notesSlides/notesSlide{n}.xml",
                            f'<?xml version="1.0"?><p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
                            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr><p:txBody>{_ap([str(n)])}</p:txBody></p:sp>'
                            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody>{_ap(notes[n])}</p:txBody></p:sp>'
                            f"</p:spTree></p:cSld></p:notes>"))
            members.append((f"ppt/slides/_rels/slide{n}.xml.rels", _rels([("rId9", f"../notesSlides/notesSlide{n}.xml")])))
    members.append(("ppt/presentation.xml", f'<?xml version="1.0"?><p:presentation xmlns:p="{P}" xmlns:r="{R}">'
                                            f'<p:sldIdLst>{"".join(ids)}</p:sldIdLst></p:presentation>'))
    members.append(("ppt/_rels/presentation.xml.rels", _rels(rels)))
    return _zip(path, members)


def cp950_text(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(text.encode("cp950"))
    return path


def cfb(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(CFB_MAGIC + b"\x00" * 504)
    return path


def encrypted_pdf(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\ntrailer << /Encrypt 5 0 R >>\n%%EOF\n")
    return path


def pdf(path, pages=2):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    objs = "".join(f"{i + 3} 0 obj << /Type /Page /Parent 2 0 R >> endobj\n" for i in range(pages))
    Path(path).write_bytes(f"%PDF-1.4\n2 0 obj << /Type /Pages /Count {pages} >> endobj\n{objs}%%EOF\n".encode())
    return path
