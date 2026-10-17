"""OOXML 內容指紋（D-096）：Office 另存不改內容時位元組會變（zip 重壓、docProps 時間、共用字串換成 inlineStr），
抽出文字算 sha256，清冊用它認「假變動」。只用標準函式庫（zipfile、ElementTree），不讀 markitdown。

只認 .docx .docm .xlsx .xlsm .pptx .pptm（不分大小寫），其他回 None；壞 zip、缺主成員、XML 壞、任何 OSError 都回 None，
呼叫端退回位元組指紋。命名空間用完整 URI 比，不靠前綴。
- docx：word/document.xml 每個 w:p 底下所有 w:t 接成一行，段落之間 \n；頁首頁尾不抽。
- xlsx：sharedStrings 每個 si 底下所有 t 接成字串表；xl/worksheets/sheet<N>.xml 按 N 排；每格一行 sheet<N>!<r>=<值>|<公式>，
  沒有值也沒有公式的格不輸出；數字 float 後 format(x, ".10g")，轉不了取原文。
- pptx：ppt/slides/slide<N>.xml 按 N 排，每個 a:p 底下所有 a:t 接成一行。
"""
import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from kb._lib.paths import long_path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WORD_EXTS = (".docx", ".docm")
EXCEL_EXTS = (".xlsx", ".xlsm")
SLIDE_EXTS = (".pptx", ".pptm")
_SHEET = re.compile(r"^xl/worksheets/sheet(\d+)\.xml$")
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def text_fingerprint(path):
    """OOXML 抽出的文字 UTF-8 算 sha256；不是 OOXML 或抽不出來回 None。"""
    ext = Path(str(path)).suffix.lower()
    if ext not in WORD_EXTS + EXCEL_EXTS + SLIDE_EXTS:
        return None
    try:
        with zipfile.ZipFile(long_path(path)) as z:
            if ext in WORD_EXTS:
                text = _docx(z)
            elif ext in EXCEL_EXTS:
                text = _xlsx(z)
            else:
                text = _pptx(z)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, ValueError, EOFError):
        return None
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse(z, name):
    with z.open(name) as f:
        return ET.parse(f).getroot()


def _joined(node, tag):
    return "".join(t.text or "" for t in node.iter(tag))


def _paragraphs(root, p_tag, t_tag):
    return "\n".join(_joined(p, t_tag) for p in root.iter(p_tag))


def _numbered(z, pattern):
    """zip 成員裡符合 pattern 的，按編號（數字）排。"""
    found = []
    for name in z.namelist():
        m = pattern.match(name)
        if m:
            found.append((int(m.group(1)), name))
    return sorted(found)


def _docx(z):
    return _paragraphs(_parse(z, "word/document.xml"), W + "p", W + "t")


def _pptx(z):
    slides = _numbered(z, _SLIDE)
    if not slides:
        return None
    return "\n".join(f"slide{n}\n" + _paragraphs(_parse(z, name), A + "p", A + "t") for n, name in slides)


def _number(raw):
    try:
        return format(float(raw), ".10g")
    except ValueError:
        return raw


def _cell_value(c, strings):
    kind = c.get("t")
    v = c.find(S + "v")
    raw = v.text if v is not None and v.text is not None else None
    if kind == "inlineStr":
        node = c.find(S + "is")
        return _joined(node, S + "t") if node is not None else None
    if raw is None:
        return None
    if kind == "s":
        try:
            return strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if kind in ("str", "b", "e", "d"):
        return raw
    return _number(raw)


def _xlsx(z):
    sheets = _numbered(z, _SHEET)
    if not sheets:
        return None
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        strings = [_joined(si, S + "t") for si in _parse(z, "xl/sharedStrings.xml").iter(S + "si")]
    lines = []
    for n, name in sheets:
        for c in _parse(z, name).iter(S + "c"):
            value = _cell_value(c, strings)
            f = c.find(S + "f")
            formula = f.text if f is not None and f.text is not None else None
            if value is None and formula is None:
                continue
            lines.append(f"sheet{n}!{c.get('r', '')}={value or ''}|{formula or ''}")
    return "\n".join(lines)
