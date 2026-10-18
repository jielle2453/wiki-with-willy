"""OOXML 內容指紋（D-096）：Office 另存不改內容時位元組會變（zip 重壓、docProps 時間、共用字串換成 inlineStr），
抽出文字算 sha256，清冊用它認「假變動」。只用標準函式庫（zipfile、ElementTree），不讀 markitdown。
抽字的共用部分（_events、_paragraphs、_shared_strings、_cell_value、_number）住 _lib/extract，這裡只組指紋用的字串。

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
from pathlib import Path

from kb._lib.extract import (  # noqa: F401  抽字的共用部分住 extract（ingest 製卡第 1 點），這裡的指紋不變
    A, CHECK_EVERY, EXCEL_EXTS, S, SLIDE_EXTS, W, WORD_EXTS, _cell_value, _events, _joined, _number, _numbered,
    _paragraphs, _shared_strings, _Stopped,
)
from kb._lib.paths import long_path

_SHEET = re.compile(r"^xl/worksheets/sheet(\d+)\.xml$")
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


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


def _pptx(z, should_stop):
    slides = _numbered(z, _SLIDE)
    if not slides:
        return None
    return "\n".join(f"slide{n}\n" + _paragraphs(z, name, A + "p", A + "t", should_stop) for n, name in slides)


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
