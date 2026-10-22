"""produce 測試共用：用標準函式庫組 Office 能開的最小檔（有 [Content_Types].xml 與關聯，LibreOffice 載得進去）。
一份小 docx（段落與表格）、一份有公式的 xlsx（公式沒有快取值，跟 openpyxl 寫出來的一樣）、一份 pptx（給 pack 測試）。"""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _zip(path, members):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members:
            z.writestr(name, text)
    return path


def _types(overrides):
    body = "".join(f'<Override PartName="/{name}" ContentType="{ctype}"/>' for name, ctype in overrides)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{body}</Types>')


def _rels(rows):
    body = "".join(f'<Relationship Id="{rid}" Type="{OD}/{kind}" Target="{target}"/>' for rid, kind, target in rows)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">{body}</Relationships>'


def docx(path, paragraphs, table=None):
    """paragraphs：段落文字；table：[[格, …], …] 放在段落後面。"""
    def para(text):
        return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    body = "".join(para(t) for t in paragraphs)
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in table)
        body += f"<w:tbl>{rows}</w:tbl><w:p/>"
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>{body}'
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
           '</w:sectPr></w:body></w:document>')
    return _zip(path, [
        ("[Content_Types].xml", _types([("word/document.xml",
                                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")])),
        ("_rels/.rels", _rels([("rId1", "officeDocument", "word/document.xml")])),
        ("word/document.xml", doc)])


def xlsx(path, rows):
    """rows：[[值, …], …]；值是數字或「=公式」（不寫快取值）或文字。"""
    xml_rows = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, v in enumerate(row):
            ref = f"{chr(65 + j)}{i}"
            if isinstance(v, str) and v.startswith("="):
                cells.append(f'<c r="{ref}"><f>{escape(v[1:])}</f></c>')
            elif isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{S}">'
             f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    book = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{S}" xmlns:r="{OD}">'
            '<sheets><sheet name="報價" sheetId="1" r:id="rId1"/></sheets></workbook>')
    return _zip(path, [
        ("[Content_Types].xml", _types([
            ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
            ("xl/worksheets/sheet1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")])),
        ("_rels/.rels", _rels([("rId1", "officeDocument", "xl/workbook.xml")])),
        ("xl/workbook.xml", book),
        ("xl/_rels/workbook.xml.rels", _rels([("rId1", "worksheet", "worksheets/sheet1.xml")])),
        ("xl/worksheets/sheet1.xml", sheet)])


def pptx(path, slides):
    """slides：[[段落, …], …]。只給 pack／unpack 用（沒有母片，PowerPoint 不一定開得了）。"""
    members, ids, rels, overrides = [], [], [], [("ppt/presentation.xml",
                                                  "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")]
    for n, paras in enumerate(slides, 1):
        text = "".join(f"<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>" for t in paras)
        members.append((f"ppt/slides/slide{n}.xml",
                        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:p="{P}" xmlns:a="{A}">'
                        f"<p:cSld><p:spTree><p:sp><p:txBody>{text}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"))
        ids.append(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>')
        rels.append((f"rId{n}", "slide", f"slides/slide{n}.xml"))
        overrides.append((f"ppt/slides/slide{n}.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"))
    return _zip(path, [("[Content_Types].xml", _types(overrides)),
                       ("_rels/.rels", _rels([("rId1", "officeDocument", "ppt/presentation.xml")])),
                       ("ppt/presentation.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                                f'<p:presentation xmlns:p="{P}" xmlns:r="{OD}"><p:sldIdLst>{"".join(ids)}'
                                                "</p:sldIdLst></p:presentation>"),
                       ("ppt/_rels/presentation.xml.rels", _rels(rels))] + members)
