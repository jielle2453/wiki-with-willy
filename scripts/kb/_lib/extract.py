"""抽字（D-099，ingest 製卡第 1 點）：OOXML 與純文字抽成人讀的文字，給 kb convert 寫暫存檔；PDF、圖片交給 Read，這裡只認格式。
只用標準函式庫（zipfile、ElementTree iterparse 邊讀邊丟）。content_fp 的內容指紋共用這裡的 _events、_paragraphs、
_shared_strings、_cell_value、_number（搬過來，content_fp import 這裡，它的指紋不變）。
抽不出來（壞 zip、缺主成員、XML 壞、加密成員…）一律丟 Unreadable("檔案壞了：<一句>")；純文字解不了丟 Unreadable("編碼")，
不 errors="replace"（I-003、I-004）。
- docx：word/document.xml 文件順序，段落一段一行、空段跳過；w:tbl 每列一行、格用 | 接；標題樣式（Heading／標題 開頭）前面加 #。
- xlsx：每個工作表一段 ## 工作表「名」（workbook 順序，名稱經 rels 對檔），每列一行「第 N 列：」開頭（實作疑義裁定 1）、格用 | 接、
  空列跳過；公式格沒快取值印 =<公式>（無快取值）（I-012）；超過 max_chars 停、最後一行標只抽到哪。
- pptx：每張投影片一段 ## 投影片 N（presentation 順序），備忘稿只取 body 佔位框，以（備忘稿）開頭接在後面。
"""
import posixpath
import re
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

from kb._lib.paths import long_path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CHECK_EVERY = 1000
CFB_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
PDF_SCAN_BYTES = 2 * 1024 * 1024

WORD_EXTS = (".docx", ".docm")
EXCEL_EXTS = (".xlsx", ".xlsm")
SLIDE_EXTS = (".pptx", ".pptm")
LEGACY_EXTS = (".doc", ".xls", ".ppt")
TEXT_EXTS = (".txt", ".md", ".csv", ".tsv", ".log", ".json")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
ENCODINGS = ("utf-8-sig", "utf-8", "cp950", "big5hkscs")
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
HEADING_PREFIXES = ("heading", "標題")
_PAGE = re.compile(rb"/Type\s*/Page(?![s])")
_SHEET = re.compile(r"^xl/worksheets/sheet(\d+)\.xml$")
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_ROW_REF = re.compile(r"\d+")


class Unreadable(Exception):
    """抽不出來；str 是給客戶看的原因（kb convert 印「讀不到：<原因>」）。"""


class _Stopped(Exception):
    """should_stop 回真：放棄這個檔。"""


# ---------- 共用（content_fp 也用） ----------

def _events(z, name, should_stop=None):
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


# ---------- 給 kb convert ----------

def _open(path, main, work):
    """開 zip、確認主成員在，交給 work(z)；所有抽不出來的情況轉成 Unreadable。"""
    try:
        with zipfile.ZipFile(long_path(path)) as z:
            if main is not None and main not in z.namelist():
                raise Unreadable(f"檔案壞了：缺少 {main}")
            return work(z)
    except Unreadable:
        raise
    except zipfile.BadZipFile:
        raise Unreadable("檔案壞了：不是完整的 Office 檔") from None
    except ET.ParseError:
        raise Unreadable("檔案壞了：內容格式錯誤") from None
    except (KeyError, zlib.error, RuntimeError, NotImplementedError, EOFError, ValueError) as e:
        raise Unreadable(f"檔案壞了：讀到一半出錯（{type(e).__name__}）") from None


def _style_of(p):
    node = p.find(f"{W}pPr/{W}pStyle")
    return (node.get(W + "val") or "") if node is not None else ""


def docx_text(path):
    def work(z):
        lines = []
        paras = []  # 段落文字堆疊（巢狀段落的字算進外層）
        cells = []  # 表格堆疊：每層 [目前這列的格, 目前這格的段落]
        for event, elem in _events(z, "word/document.xml"):
            tag = elem.tag
            if tag == W + "p":
                if event == "start":
                    paras.append([])
                    continue
                text = "".join(paras.pop()).strip()
                if paras:
                    paras[-1].append(text)
                elif cells:
                    if text:
                        cells[-1][1].append(text)
                elif text:
                    style = _style_of(elem).casefold()
                    lines.append(("# " + text) if style.startswith(HEADING_PREFIXES) else text)
                elem.clear()
            elif tag == W + "t" and event == "end":
                for buf in paras:
                    buf.append(elem.text or "")
            elif tag == W + "tab" and event == "end" and paras:
                paras[-1].append(" ")
            elif tag == W + "tbl":
                if event == "start":
                    cells.append([[], []])
                else:
                    cells.pop()
                    elem.clear()
            elif tag == W + "tc" and event == "end" and cells:
                cells[-1][0].append(" ".join(cells[-1][1]))
                cells[-1][1] = []
            elif tag == W + "tr" and event == "end" and cells:
                row = " | ".join(cells[-1][0])
                cells[-1][0] = []
                if len(cells) > 1:
                    cells[-2][1].append(row)  # 巢狀表格：整列併進外層那格
                elif row.strip(" |"):
                    lines.append(row)
                elem.clear()
        return "\n".join(lines)
    return _open(path, "word/document.xml", work)


def _rels(z, name):
    """rels 成員 → {Id: 成員完整路徑}；沒有回空 dict。"""
    if name not in z.namelist():
        return {}
    base = posixpath.dirname(posixpath.dirname(name))  # xl/_rels/workbook.xml.rels → xl
    out = {}
    for _event, elem in _events(z, name):
        if elem.tag == PR + "Relationship" and _event == "end":
            target = elem.get("Target") or ""
            full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
            out[elem.get("Id")] = full
    return out


def _sheets(z):
    """[(名稱, 成員)]，照 workbook 順序；workbook 或 rels 缺了就照 sheetN 編號、名稱用 sheetN。"""
    names = set(z.namelist())
    rels = _rels(z, "xl/_rels/workbook.xml.rels")
    found = []
    if "xl/workbook.xml" in names and rels:
        for event, elem in _events(z, "xl/workbook.xml"):
            if event == "end" and elem.tag == S + "sheet":
                member = rels.get(elem.get(R + "id"))
                if member in names:
                    found.append((elem.get("name") or posixpath.basename(member), member))
    if not found:
        found = [(f"sheet{n}", name) for n, name in _numbered(z, _SHEET)]
    return found


def _row_number(elem, last):
    r = elem.get("r")
    return int(r) if r and r.isdigit() else last + 1


def xlsx_text(path, max_chars=None):
    def work(z):
        sheets = _sheets(z)
        if not sheets:
            raise Unreadable("檔案壞了：沒有工作表")
        strings = _shared_strings(z, None) if "xl/sharedStrings.xml" in z.namelist() else []
        out, size = [], 0
        for i, (name, member) in enumerate(sheets):
            if i:
                out.append("")
            out.append(f"## 工作表「{name}」")
            row_no, last, cells = 0, 0, []
            for event, elem in _events(z, member):
                if event != "end":
                    continue
                if elem.tag == S + "c":
                    value = _cell_value(elem, strings)
                    if value is None:
                        f = elem.find(S + "f")
                        if f is not None and f.text:
                            value = f"={f.text}（無快取值）"
                    cells.append(value if value is not None else "")
                    elem.clear()
                elif elem.tag == S + "row":
                    row_no = _row_number(elem, row_no)
                    if any(c.strip() for c in cells):
                        line = f"第 {row_no} 列：" + " | ".join(cells)
                        if max_chars is not None and size + len(line) > max_chars:
                            out.append(f"（只抽到工作表「{name}」第 {last} 列）")
                            return "\n".join(out)
                        out.append(line)
                        size += len(line) + 1
                        last = row_no
                    cells = []
                    elem.clear()
        return "\n".join(out)
    return _open(path, None, work)


def _slides(z):
    names = set(z.namelist())
    rels = _rels(z, "ppt/_rels/presentation.xml.rels")
    found = []
    if "ppt/presentation.xml" in names and rels:
        for event, elem in _events(z, "ppt/presentation.xml"):
            if event == "end" and elem.tag == P + "sldId":
                member = rels.get(elem.get(R + "id"))
                if member in names:
                    found.append(member)
    if not found:
        found = [name for _n, name in _numbered(z, _SLIDE)]
    return found


def _notes_of(z, slide):
    """投影片對到的備忘稿成員（經 slide 的 rels），沒有回 None。"""
    rel = posixpath.join(posixpath.dirname(slide), "_rels", posixpath.basename(slide) + ".rels")
    for member in _rels(z, rel).values():
        if member.startswith("ppt/notesSlides/") and member in z.namelist():
            return member
    return None


def _notes_text(z, member):
    """備忘稿只取 body 佔位框（略過投影片縮圖、頁碼）。"""
    lines = []
    for event, elem in _events(z, member):
        if event == "end" and elem.tag == P + "sp":
            ph = elem.find(f".//{P}ph")
            if ph is not None and ph.get("type") == "body":
                for p in elem.iter(A + "p"):
                    text = _joined(p, A + "t").strip()
                    if text:
                        lines.append(text)
            elem.clear()
    return lines


def pptx_text(path):
    def work(z):
        slides = _slides(z)
        if not slides:
            raise Unreadable("檔案壞了：沒有投影片")
        out = []
        for n, member in enumerate(slides, 1):
            if n > 1:
                out.append("")
            out.append(f"## 投影片 {n}")
            out.extend(ln for ln in _paragraphs(z, member, A + "p", A + "t", None).split("\n") if ln.strip())
            notes = _notes_of(z, member)
            if notes:
                out.extend("（備忘稿）" + ln for ln in _notes_text(z, notes))
        return "\n".join(out)
    return _open(path, None, work)


def plain_text(path):
    """整份讀位元組，依序試 utf-8-sig、utf-8、cp950、big5hkscs；utf-16 只在有 BOM 時試。回 (text, encoding)。"""
    with open(long_path(path), "rb") as f:
        data = f.read()
    if data.startswith(UTF16_BOMS):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-8-sig" and not data.startswith(b"\xef\xbb\xbf"):
            continue  # 沒有 BOM 的 UTF-8 記成 utf-8
        return text, enc
    raise Unreadable("編碼")


def _head(path, n):
    with open(long_path(path), "rb") as f:
        return f.read(n)


def probe(path):
    """回 docx｜xlsx｜pptx｜text｜pdf｜image｜legacy_office｜encrypted｜unsupported。檔不在丟 OSError。"""
    ext = Path(str(path)).suffix.lower()
    if ext in WORD_EXTS + EXCEL_EXTS + SLIDE_EXTS:
        if _head(path, 8) == CFB_MAGIC:
            return "encrypted"  # OOXML 加密後是 CFB 容器（I-014）
        return "docx" if ext in WORD_EXTS else "xlsx" if ext in EXCEL_EXTS else "pptx"
    if ext in LEGACY_EXTS:
        return "legacy_office" if _head(path, 8) == CFB_MAGIC else "unsupported"
    if ext == ".pdf":
        return "encrypted" if b"/Encrypt" in _head(path, PDF_SCAN_BYTES) else "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    return "unsupported"


def pdf_pages(path):
    """數 /Type /Page（不含 /Pages）出現幾次；數不出來回 None。"""
    try:
        with open(long_path(path), "rb") as f:
            n = len(_PAGE.findall(f.read()))
    except OSError:
        return None
    return n or None
