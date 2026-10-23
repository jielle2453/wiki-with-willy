"""抽字（D-099，ingest 製卡第 1 點）：OOXML 與純文字抽成人讀的文字，給 kb convert 寫暫存檔；PDF、圖片交給 Read，這裡只認格式。
只用標準函式庫（zipfile、ElementTree iterparse 邊讀邊丟）。content_fp 的內容指紋共用這裡的 _events、_paragraphs、
_shared_strings、_cell_value、_number（搬過來，content_fp import 這裡，它的指紋不變）。
抽不出來（壞 zip、缺主成員、XML 壞、加密成員…）一律丟 Unreadable("檔案壞了：<一句>")；純文字解不了丟 Unreadable("編碼")，
不 errors="replace"（I-003、I-004）。
- docx：word/document.xml 文件順序，段落一段一行、空段跳過；w:tbl 每列一行、格用 | 接；標題樣式（Heading／標題 開頭）前面加 #。
- xlsx：每個工作表一段 ## 工作表「名」（workbook 順序，名稱經 rels 對檔），每列一行「第 N 列：」開頭（實作疑義裁定 1）、格用 | 接、
  空列跳過；公式格沒快取值印 =<公式>（無快取值）（I-012）；超過 max_chars 停、最後一行標只抽到哪。
- pptx：每張投影片一段 ## 投影片 N（presentation 順序），備忘稿只取 body 佔位框，以（備忘稿）開頭接在後面。
- pdf（測試後修正裁定 36，D-099 修訂）：選配套件 pypdfium2 逐頁抽文字層，每頁前面 <!-- 第 N 頁 -->；去空白後少於 min_chars 的頁
  算掃描頁，render_pages 轉成 PNG（PNG 用標準函式庫 zlib 自己寫，不裝 Pillow）。沒裝丟 Unreadable("還沒裝 PDF 元件（…）")。
  本行程 import 不到、kb office setup 的工具夾（pylib）有 pypdfium2 時，三支 PDF 函式改在子行程跑（PYTHONPATH 帶 scripts 與工具夾，
  跟 office.render 同一招；本行程不動模組搜尋路徑，build 第 3 點、D-189 補）。密碼只經 stdin 給子行程（不放命令列、不放環境變數）；
  子行程出錯時 stderr 先換掉密碼、再 mask_secrets，才寫 hook-errors.log；只有擁有者密碼的判法跟本行程同一支（子行程裡跑的就是它）。
  工具夾已經在 PYTHONPATH 上（自己就是子行程、或 office.render 的子行程）還 import 不到，就不再開子行程。
"""
import json
import os
import posixpath
import re
import struct
import subprocess
import sys
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
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
TOO_LARGE = "檔案太大"
PDF_MISSING = "還沒裝 PDF 元件（pip install pypdfium2）"
PDF_ENCRYPTED = "有密碼"
PDF_WRONG_PASSWORD = "密碼不對"  # 密碼與守門：帶了密碼還開不了
PDF_BROKEN = "檔案壞了：PDF 打不開"
PDF_CHILD_FAILED = "PDF 元件出錯"  # 工具夾的 pypdfium2 在子行程裡起不來（多半是被智慧型應用程式控制擋了）
CHILD_SECONDS = 600
CHILD_MASK = "＊＊＊"
_CHILD_CODE = ("import json,sys\n"
               "from kb._lib import extract\n"
               "req=json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
               "try:\n"
               "    out={'ok':getattr(extract,req['op'])(*req['args'],**req['kw'])}\n"
               "except extract.Unreadable as e:\n"
               "    out={'unreadable':str(e)}\n"
               "sys.stdout.buffer.write(json.dumps(out,ensure_ascii=False).encode('utf-8'))\n")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
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

def _open(path, main, work, max_bytes=None):
    """開 zip、確認主成員在，交給 work(z)；所有抽不出來的情況轉成 Unreadable。
    max_bytes：任一個 XML 成員（.xml、.rels）解壓後超過就丟 Unreadable("檔案太大")（測試後修正裁定 24）。"""
    try:
        with zipfile.ZipFile(long_path(path)) as z:
            if main is not None and main not in z.namelist():
                raise Unreadable(f"檔案壞了：缺少 {main}")
            if max_bytes is not None and any(i.file_size > max_bytes for i in z.infolist()
                                             if i.filename.lower().endswith((".xml", ".rels"))):
                raise Unreadable(TOO_LARGE)
            return work(z)
    except Unreadable:
        raise
    except zipfile.BadZipFile:
        raise Unreadable("檔案壞了：不是完整的 Office 檔") from None
    except ET.ParseError:
        raise Unreadable("檔案壞了：內容格式錯誤") from None
    except (KeyError, zlib.error, RuntimeError, NotImplementedError, EOFError, ValueError) as e:
        raise Unreadable(f"檔案壞了：讀到一半出錯（{type(e).__name__}）") from None


def _check_size(z, name, max_bytes):
    """成員解壓後超過 max_bytes 就丟 Unreadable("檔案太大")（測試後修正裁定 24，zip bomb 不讀）。"""
    if max_bytes is not None and z.getinfo(name).file_size > max_bytes:
        raise Unreadable(TOO_LARGE)


def _member(z, name, max_bytes):
    _check_size(z, name, max_bytes)
    return _events(z, name)


def _style_of(p):
    node = p.find(f"{W}pPr/{W}pStyle")
    return (node.get(W + "val") or "") if node is not None else ""


def docx_text(path, max_bytes=None):
    """文字方塊（w:txbxContent）只抽一次：mc:Fallback 裡那份跳過，巢狀段落接在外層段落後面；w:br 換行
    （表格格子裡換成空白）。"""
    def work(z):
        lines = []
        paras = []  # 段落文字堆疊：字只進最內層，內層段落結束再接到外層後面
        cells = []  # 表格堆疊：每層 [目前這列的格, 目前這格的段落]
        fallback = 0  # 在 mc:Fallback 裡的深度
        for event, elem in _member(z, "word/document.xml", max_bytes):
            tag = elem.tag
            if tag == MC + "Fallback":
                fallback += 1 if event == "start" else -1
                if event == "end":
                    elem.clear()
                continue
            if fallback:
                continue
            if tag == W + "p":
                if event == "start":
                    paras.append([])
                    continue
                text = "".join(paras.pop()).strip()
                if paras:
                    if text:
                        paras[-1].append(" " + text)
                elif cells:
                    if text:
                        cells[-1][1].append(" ".join(text.split("\n")))
                elif text:
                    style = _style_of(elem).casefold()
                    text = "\n".join(seg.strip() for seg in text.split("\n") if seg.strip())
                    lines.append(("# " + text) if style.startswith(HEADING_PREFIXES) else text)
                elem.clear()
            elif tag == W + "t" and event == "end" and paras:
                paras[-1].append(elem.text or "")
            elif tag == W + "br" and event == "end" and paras:
                paras[-1].append("\n")
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
    return _open(path, "word/document.xml", work, max_bytes)


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


def xlsx_text(path, max_chars=None, max_bytes=None):
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
                            where = f"第 {last} 列" if last else f"第 {row_no} 列之前"  # 第一列就停不印第 0 列（裁定 30）
                            out.append(f"（只抽到工作表「{name}」{where}）")
                            return "\n".join(out)
                        out.append(line)
                        size += len(line) + 1
                        last = row_no
                    cells = []
                    elem.clear()
        return "\n".join(out)
    return _open(path, None, work, max_bytes)


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


def pptx_text(path, max_bytes=None):
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
    return _open(path, None, work, max_bytes)


def plain_text(path, max_bytes=None):
    """整份讀位元組，依序試 utf-8-sig、utf-8、cp950、big5hkscs；utf-16 只在有 BOM 時試。回 (text, encoding)。
    max_bytes：檔超過就丟 Unreadable("檔案太大")，不整份讀進記憶體（測試後修正裁定 24）。"""
    with open(long_path(path), "rb") as f:
        if max_bytes is not None and os.fstat(f.fileno()).st_size > max_bytes:
            raise Unreadable(TOO_LARGE)
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


def kind_by_name(path):
    """只看副檔名、不開檔（測試後修正裁定 28，cards todo 用）：docx｜xlsx｜pptx｜legacy_office｜pdf｜text｜image｜unsupported。
    加密與 CFB 只在 kb convert 的 probe 查。"""
    ext = Path(str(path)).suffix.lower()
    if ext in WORD_EXTS:
        return "docx"
    if ext in EXCEL_EXTS:
        return "xlsx"
    if ext in SLIDE_EXTS:
        return "pptx"
    if ext in LEGACY_EXTS:
        return "legacy_office"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    return "unsupported"


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


def _pdfium():
    """選配套件；沒裝（或 sys.modules 裡是 None、載入時被擋）丟 Unreadable(PDF_MISSING)。"""
    try:
        import pypdfium2
    except (ImportError, OSError):
        raise Unreadable(PDF_MISSING) from None
    return pypdfium2


def _tool_lib():
    """kb office setup 裝的工具夾（pylib）；測試換掉。"""
    from kb._lib import office
    return office.pylib()


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _via_child():
    """本行程 import 不到、工具夾有 pypdfium2、工具夾還沒在 PYTHONPATH 上：回工具夾（改在子行程跑），否則 None。"""
    try:
        _pdfium()
        return None
    except Unreadable:
        pass
    lib = _tool_lib()
    if not (Path(lib) / "pypdfium2").is_dir():
        return None
    if _norm(lib) in {_norm(x) for x in os.environ.get("PYTHONPATH", "").split(os.pathsep) if x}:
        return None
    return lib


def pdf_available():
    """這台讀得了 PDF 嗎（本行程或工具夾）：cards 判「PDF 元件裝好了」用。"""
    try:
        _pdfium()
        return True
    except Unreadable:
        return _via_child() is not None


def _run_child(argv, data, env, timeout):
    """跑子行程，回 (結束碼, stdout bytes, stderr 文字)；測試換掉記下參數。stdin 只送 data（含密碼的 JSON）。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    import tempfile
    p = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout,
                       cwd=tempfile.gettempdir(), creationflags=flags)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", errors="replace")


def _scrub(text, password):
    """子行程的錯誤訊息：先換掉密碼原字，再照 mask_secrets 遮值（ledger 寫入前同一套）。"""
    from kb._lib import credentials
    text = str(text or "")
    if password:
        text = text.replace(str(password), CHILD_MASK)
    return credentials.mask_secrets(text)


def _log_child(text):
    from kb import guard
    from kb._lib import ledger
    ledger.log_error(guard.find_vault(os.getcwd()), text, source="extract")


def _child(lib, op, args, kw):
    """在子行程跑 extract.<op>：PYTHONPATH 是 scripts 與工具夾，請求（含密碼）走 stdin。回 op 的結果；
    子行程回 Unreadable 照丟；起不來、逾時、回的東西讀不懂丟 Unreadable(PDF_CHILD_FAILED)，錯誤遮過才記 hook-errors.log。"""
    from kb._lib.paths import plugin_root
    password = kw.get("password")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    env["PYTHONPATH"] = os.pathsep.join([str(plugin_root() / "scripts"), str(lib)])
    env["PYTHONIOENCODING"] = "utf-8"
    data = json.dumps({"op": op, "args": args, "kw": kw}, ensure_ascii=False).encode("utf-8")
    try:
        code, out, err = _run_child([sys.executable, "-c", _CHILD_CODE], data, env, CHILD_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        _log_child(_scrub(f"PDF 子行程起不來：{type(e).__name__}: {e}", password))
        raise Unreadable(PDF_CHILD_FAILED) from None
    try:
        got = json.loads(out.decode("utf-8")) if code == 0 else None
    except (ValueError, UnicodeDecodeError):
        got = None
    if not isinstance(got, dict) or not ({"ok", "unreadable"} & set(got)):
        tail = " ".join(str(err or "").strip().splitlines()[-3:])
        _log_child(_scrub(f"PDF 子行程出錯（結束碼 {code}）：{tail}", password))
        raise Unreadable(PDF_CHILD_FAILED)
    if got.get("unreadable") == PDF_MISSING:  # 工具夾在子行程的路徑上還 import 不到：載入被擋了
        _log_child(_scrub(f"PDF 子行程載不了 pypdfium2：{' '.join(str(err or '').strip().splitlines()[-3:])}", password))
        raise Unreadable(PDF_CHILD_FAILED)
    if "unreadable" in got:
        raise Unreadable(str(got["unreadable"]))
    return got["ok"]


def _open_pdf(pdfium, path, password=None):
    """password（密碼與守門 D-182）：加密的 PDF 帶密碼開；錯的密碼丟 Unreadable(PDF_WRONG_PASSWORD)，沒帶丟 PDF_ENCRYPTED。"""
    try:
        return pdfium.PdfDocument(long_path(path), password=password)
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise Unreadable(PDF_WRONG_PASSWORD if password else PDF_ENCRYPTED) from None
        raise Unreadable(PDF_BROKEN) from None


def pdf_opens_without_password(path):
    """只有擁有者密碼的 PDF（不用密碼就能看）：/Encrypt 在，但不帶密碼開得了。沒裝元件丟 Unreadable(PDF_MISSING)。"""
    lib = _via_child()
    if lib is not None:
        return bool(_child(lib, "pdf_opens_without_password", [str(path)], {}))
    pdfium = _pdfium()
    try:
        doc = _open_pdf(pdfium, path)
    except Unreadable:
        return False
    doc.close()
    return True


def pdf_pages_text(path, min_chars, should_stop=None, password=None):
    """回 (text, 頁數, 掃描頁清單)。每頁前面一行 <!-- 第 N 頁 -->；去空白後少於 min_chars 字的頁算掃描頁，只留頁碼註解。
    should_stop 每頁問一次，回真就停在那頁之前（子行程跑時不問）。password：加密的 PDF 帶密碼開。"""
    lib = _via_child()
    if lib is not None:
        text, total, scanned = _child(lib, "pdf_pages_text", [str(path), min_chars], {"password": password})
        return text, total, list(scanned)
    pdfium = _pdfium()
    doc = _open_pdf(pdfium, path, password)
    try:
        parts, scanned = [], []
        total = len(doc)
        for i in range(total):
            if should_stop is not None and should_stop():
                break
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            parts.append(f"<!-- 第 {i + 1} 頁 -->")
            if len("".join(text.split())) < min_chars:
                scanned.append(i + 1)
            else:
                parts.append(text)
        return "\n".join(parts), total, scanned
    except pdfium.PdfiumError:
        raise Unreadable(PDF_BROKEN) from None
    finally:
        doc.close()


# 位元組在 bitmap buffer 裡的順序 → 取 R、G、B 的位置（pypdfium2 的 mode）
_CHANNELS = {"RGB": (0, 1, 2), "BGR": (2, 1, 0), "RGBA": (0, 1, 2), "RGBX": (0, 1, 2), "BGRA": (2, 1, 0),
             "BGRX": (2, 1, 0), "L": (0, 0, 0)}


def _png(width, height, rows):
    """rows 是每列 RGB 位元組；寫成 8 位元 RGB、濾波 0、zlib 壓縮的 PNG。"""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in rows)
    return PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _rgb_rows(bitmap):
    order = _CHANNELS.get(bitmap.mode)
    if order is None:
        raise Unreadable(f"檔案壞了：PDF 頁面格式不認得（{bitmap.mode}）")
    n, width, stride = bitmap.n_channels, bitmap.width, bitmap.stride
    buf = bytes(bitmap.buffer)
    for y in range(bitmap.height):
        row = buf[y * stride:y * stride + width * n]
        out = bytearray(width * 3)
        out[0::3], out[1::3], out[2::3] = row[order[0]::n], row[order[1]::n], row[order[2]::n]
        yield bytes(out)


def render_pages(path, pages, out_dir, stem, scale, limit, password=None):
    """掃描頁轉 PNG：<out_dir>/<stem>_p<N>.png，最多 limit 頁。回 [(頁碼, 絕對路徑)]。stem 由呼叫端驗過（裁定 16）。"""
    pages = list(pages)[:max(limit, 0)]
    if not pages:
        return []
    lib = _via_child()
    if lib is not None:
        got = _child(lib, "render_pages", [str(path), pages, str(out_dir), stem, scale, limit], {"password": password})
        return [(n, p) for n, p in got]
    pdfium = _pdfium()
    doc = _open_pdf(pdfium, path, password)
    out = []
    try:
        for n in pages:
            page = doc[n - 1]
            try:
                bitmap = page.render(scale=scale)
                try:
                    data = _png(bitmap.width, bitmap.height, _rgb_rows(bitmap))
                finally:
                    bitmap.close()
            finally:
                page.close()
            target = Path(out_dir) / f"{stem}_p{n}.png"
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            out.append((n, str(target.resolve())))
    except pdfium.PdfiumError:
        raise Unreadable(PDF_BROKEN) from None
    finally:
        doc.close()
    return out


def pdf_pages(path):
    """數 /Type /Page（不含 /Pages）出現幾次；數不出來回 None。"""
    try:
        with open(long_path(path), "rb") as f:
            n = len(_PAGE.findall(f.read()))
    except OSError:
        return None
    return n or None
