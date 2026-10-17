"""OOXML 內容指紋（D-096）：Office 另存不改內容時位元組會變（zip 重壓、docProps 時間、共用字串換成 inlineStr），
抽出文字算 sha256，清冊用它認「假變動」。只用標準函式庫（zipfile、ElementTree），不讀 markitdown。

只認 .docx .docm .xlsx .xlsm .pptx .pptm（不分大小寫），其他回 None；抽不出來的任何例外（壞 zip、缺主成員、XML 壞、
zlib.error、加密成員的 RuntimeError、不支援壓縮的 NotImplementedError、OSError…）都回 None，呼叫端退回位元組指紋（審閱裁定 35）。
用 iterparse 邊讀邊丟，每 CHECK_EVERY 個元素問一次 should_stop，回真就放棄、回 None（審閱裁定 34，開場的時間預算）。
命名空間用完整 URI 比，不靠前綴。
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
CHECK_EVERY = 1000
_SHEET = re.compile(r"^xl/worksheets/sheet(\d+)\.xml$")
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


class _Stopped(Exception):
    """should_stop 回真：放棄這個檔。"""


def text_fingerprint(path, should_stop=None):
    """OOXML 抽出的文字 UTF-8 算 sha256；不是 OOXML、抽不出來、或 should_stop 喊停都回 None。"""
    ext = Path(str(path)).suffix.lower()
    if ext not in WORD_EXTS + EXCEL_EXTS + SLIDE_EXTS:
        return None
    try:
        with zipfile.ZipFile(long_path(path)) as z:
            if ext in WORD_EXTS:
                text = _paragraphs(z, "word/document.xml", W + "p", W + "t", should_stop)
            elif ext in EXCEL_EXTS:
                text = _xlsx(z, should_stop)
            else:
                text = _pptx(z, should_stop)
    except Exception:  # noqa: BLE001  任何抽不出來的情況都退回位元組指紋
        return None
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _events(z, name, should_stop):
    """iterparse 一個成員，給 (event, elem)；每 CHECK_EVERY 個事件問一次 should_stop。"""
    with z.open(name) as f:
        for i, (event, elem) in enumerate(ET.iterparse(f, events=("start", "end")), 1):
            if should_stop is not None and i % CHECK_EVERY == 0 and should_stop():
                raise _Stopped
            yield event, elem


def _paragraphs(z, name, p_tag, t_tag, should_stop):
    """每個段落底下所有文字接成一行（巢狀段落的文字也算進外層），段落之間 \\n。"""
    lines, stack = [], []
    for event, elem in _events(z, name, should_stop):
        if elem.tag == p_tag:
            if event == "start":
                stack.append([])
            else:
                lines.append("".join(stack.pop()))
                elem.clear()
        elif event == "end" and elem.tag == t_tag:
            text = elem.text or ""
            for buf in stack:
                buf.append(text)
    return "\n".join(lines)


def _numbered(z, pattern):
    """zip 成員裡符合 pattern 的，按編號（數字）排。"""
    found = []
    for name in z.namelist():
        m = pattern.match(name)
        if m:
            found.append((int(m.group(1)), name))
    return sorted(found)


def _pptx(z, should_stop):
    slides = _numbered(z, _SLIDE)
    if not slides:
        return None
    return "\n".join(f"slide{n}\n" + _paragraphs(z, name, A + "p", A + "t", should_stop) for n, name in slides)


def _number(raw):
    try:
        return format(float(raw), ".10g")
    except ValueError:
        return raw


def _joined(node, tag):
    return "".join(t.text or "" for t in node.iter(tag))


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


def _shared_strings(z, should_stop):
    strings, current = [], None
    for event, elem in _events(z, "xl/sharedStrings.xml", should_stop):
        if elem.tag == S + "si":
            if event == "start":
                current = []
            else:
                strings.append("".join(current))
                current = None
                elem.clear()
        elif event == "end" and elem.tag == S + "t" and current is not None:
            current.append(elem.text or "")
    return strings


def _xlsx(z, should_stop):
    sheets = _numbered(z, _SHEET)
    if not sheets:
        return None
    strings = _shared_strings(z, should_stop) if "xl/sharedStrings.xml" in z.namelist() else []
    lines = []
    for n, name in sheets:
        for event, elem in _events(z, name, should_stop):
            if event != "end":
                continue
            if elem.tag == S + "c":
                value = _cell_value(elem, strings)
                f = elem.find(S + "f")
                formula = f.text if f is not None and f.text is not None else None
                if value is not None or formula is not None:
                    lines.append(f"sheet{n}!{elem.get('r', '')}={value or ''}|{formula or ''}")
                elem.clear()
            elif elem.tag == S + "row":
                elem.clear()
    return "\n".join(lines)
