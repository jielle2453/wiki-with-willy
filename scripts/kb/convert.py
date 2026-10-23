"""kb convert <目標>（D-099，ingest 製卡第 2 點）：把一份原檔抽成純文字暫存檔給製卡者 Read；圖片只指路給 Read。
PDF 走選配 pypdfium2（裁定 36）：文字層寫暫存檔，掃描頁轉 PNG 到 .system/temp/；沒裝 exit 3 進待處理。

目標三種寫法：清冊編號（f-…、out-…）、uri（file://別名/相對路徑）、磁碟路徑（絕對或相對 cwd）；路徑經 inventory.uri_of
反查清冊條目，對不到也照轉但不寫待處理。總資料夾只從 cwd 往上找，不收 --vault（測試後修正裁定 15：會把原檔內容帶給 AI 的
子命令不准由參數指定總資料夾）。判斷都過、要寫暫存（或指給 Read）之前才清 .system/temp/ 裡超過 temp_keep_hours 的一般檔；
temp 是重解析點就不清（裁定 17）。暫存檔名只用驗過格式的編號，否則用指紋前 12 碼（裁定 16）。
守門對 kb.py 一律放行，所以抽字前先做守門同一套判斷（實作疑義裁定 8）：guard.classify 是 Z10／Z10b 或條目 excluded、
Z11、Z1／Z6／Z7／Z8 都拒；來源根以下的段像密碼或金鑰也拒（總資料夾內部豁免，D-081）；這四種不寫待處理。
讀前看一次目前的屬性：只在雲端（OneDrive）就照 placeholder 回，不讀內容（manage 第二版，B-095）。
找不到原檔（含 status missing）只印一行、不寫待處理（實作疑義裁定 2）。其他讀不到的 exit 3，條目在清冊就寫一條 unreadable。
原檔被別的程式鎖住、沒權限（OSError）→ 一句話 exit 3、不寫待處理（裁定 18）；--json 時所有失敗都印 JSON。
任何情況不印檔案內容到終端機（D-022）。
密碼與守門（D-181、D-182、D-183）：--password「她剛給的」一次性、--saved 憑證管理員那一筆（要授權過的種類或這句明說要試 --explicit）；
只解加密的 PDF，開成功關掉那份的「讀不到：有密碼」待處理、--saved 開的記進 unlock.json 的 opened、--password 的輸出帶 ask_save；
密碼錯一行「密碼不對」exit 3、不記待處理。/Encrypt 在但不用密碼開得了（只有擁有者密碼）照一般 PDF 讀。
gate 的憑證判斷讀「可以讀」裁示（guard.ruled_readable，跟守門同一支）；讀那種檔的回合記 secret_shown。密碼不進任何輸出與檔。
"""
import argparse
import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from kb import guard
from kb._lib import config, credentials, extract, inventory, ledger, pending, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.paths import long_path, normalize

TEMP_POSIX = ".system/temp"
NOT_IN_VAULT = "這裡不是知識庫；到知識庫裡執行"  # 不收 --vault，不提示它（裁定 15）
CLI_LOCK_TIMEOUT = 30
_ID = re.compile(r"^(f|out)-[0-9a-f]+$")
NOT_CARD_ZONES = ("Z1", "Z6", "Z7", "Z8")
EXCLUDED = "這個位置你說過不讀"
CREDENTIAL = "檔名像密碼或金鑰，預設不讀"
OUTSIDE = "不在登記的資料夾裡"
NOT_CARD = "這不是要製卡的檔"
NOT_FOUND = "找不到原檔"
PLACEHOLDER = "只在雲端、還沒下載到電腦"
TOO_LARGE = extract.TOO_LARGE
LOCKED = "檔案被別的程式開著或沒有權限，關掉 Word／Excel 再試"
BROKEN = "檔案壞了"
BAD_TEMP = "暫存位置不對"
JSON_KEYS = ("id", "uri", "path", "kind", "temp", "chars", "parts", "encoding", "pages", "big", "unreadable",
             "pending_id", "scanned_pages", "images", "ask_save", "unlocked")
WRONG_PASSWORD = extract.PDF_WRONG_PASSWORD
NOT_ALLOWED = "這組密碼沒說過可以用在別的檔"
ONE_PASSWORD = "--password 跟 --saved 只能擇一"
ASK_SAVE_LINE = "這組密碼還沒記住：回答末尾問她要不要記住"
OPENED_HOW = "給了密碼，打開了"
# 讀不到的第一行（「讀不到：」後面）與待處理記的 reason
KIND_REASONS = {
    "legacy_office": ("舊格式（{ext}），請另存成新格式", "舊格式（{ext}）"),
    "encrypted": ("有密碼", "有密碼"),
    "unsupported": ("不支援的格式（{ext}）", "不支援的格式（{ext}）"),
}
PARTS_WORD = {"xlsx": "工作表", "pptx": "投影片"}


PASSWORD_USED = "_password_used"


class Refused(Exception):
    """讀不到：args = (第一行原因, 待處理 reason 或 None)。reason 是 None 就不寫待處理。"""


def add_arguments(sub):
    sub.add_argument("target", help="清冊編號（f-…）、uri（file://別名/相對路徑）或磁碟路徑；總資料夾從目前目錄往上找")
    sub.add_argument("--password", help="加密的 PDF：客戶剛給的密碼（一次性，用引號包起來）")
    sub.add_argument("--saved", help="加密的 PDF：用憑證管理員裡存的那一組（名稱）；要授權過這一種，或加 --explicit")
    sub.add_argument("--explicit", action="store_true", help="客戶這句明說要試這組密碼（不用授權過的種類）")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


RECALL_ON_DATA_ACCESS = 0x400000  # OneDrive 只在雲端：讀內容會觸發下載（manage 第二版，B-095）


def _attrs(path):
    """目前的檔案屬性（os.stat 只讀中繼資料，不下載）；讀不到回 0。"""
    try:
        return getattr(os.stat(long_path(path)), "st_file_attributes", 0)
    except OSError:
        return 0


def _is_reparse(path):
    try:
        st = os.lstat(long_path(path))
    except OSError:
        return False
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & inventory.REPARSE_POINT)


def clean_temp(vault):
    """.system/temp/ 裡修改時間超過 temp_keep_hours 的一般檔刪掉，不進子夾、不碰連結；刪不掉的留著。
    temp 本身是重解析點（junction、symlink）就整個不清，記一行 hook-errors.log（裁定 17）。"""
    d = Path(vault) / TEMP_POSIX
    if _is_reparse(d):
        ledger.log_error(vault, f"{TEMP_POSIX} 是連結，不清暫存", source="convert")
        return
    if not d.is_dir():
        return
    limit = config.get(vault, "temp_keep_hours") * 3600
    now = time.time()
    for p in d.iterdir():
        try:
            st = os.lstat(p)
            if (not _is_reparse(p) and os.path.isfile(p) and not getattr(st, "st_file_attributes", 0) & 0x10
                    and now - st.st_mtime >= limit):
                p.unlink()
        except OSError:
            pass


def resolve(vault, sources, target, cwd):
    """回 (entry 或 None, 磁碟路徑 或 None, uri 或 None)。編號對不到丟 Refused。"""
    target = str(target).strip()
    if _ID.match(target):
        entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == target), None)
        if entry is None or not entry.get("locations"):
            raise Refused(f"清冊沒有 {target}", None)
        uri = entry["locations"][0]["uri"]
        return entry, inventory.path_of(vault, sources, uri), uri
    if target.startswith("file://"):
        return inventory.entry_for(vault, target), inventory.path_of(vault, sources, target), target
    path = Path(target) if os.path.isabs(target) else Path(cwd) / target
    path = Path(os.path.abspath(path))
    uri = inventory.uri_of(vault, sources, path)
    entry = inventory.entry_for(vault, uri) if uri else None
    if entry is not None:  # uri_of 可能是 normalize 過的小寫：換成清冊裡那個位置的原始寫法
        key = inventory.uri_key(uri)
        uri = next((loc["uri"] for loc in entry["locations"] if inventory.uri_key(loc["uri"]) == key), uri)
    return entry, path, uri


def gate(vault, sources, entry, path):
    """守門同一套判斷（實作疑義裁定 8），加清冊的 read 與 status。不過丟 Refused。
    憑證樣式的那份有「可以讀」裁示就放（guard.ruled_readable，排除照擋，D-183、D-184）。回這份是不是靠裁示讀的密碼檔。"""
    readable = False
    if entry is not None and entry.get("status") == "excluded":
        raise Refused(EXCLUDED, None)
    if entry is not None and entry.get("status") == "missing":
        raise Refused(NOT_FOUND, None)
    if path is None:
        raise Refused(OUTSIDE, None)
    target_n = normalize(str(path), None)
    zone, _alias = guard.classify(target_n, normalize(str(vault), None), sources)
    if zone in ("Z10", "Z10b"):
        raise Refused(EXCLUDED, None)
    if zone == "Z11":
        raise Refused(OUTSIDE, None)
    if zone in NOT_CARD_ZONES:
        raise Refused(NOT_CARD, None)
    if zone == "Z9":
        _status, src = sources.lookup(target_n)
        segs = target_n[len(src["root"]):].strip("/").split("/") if src else []
        if credentials.match_segment(segs) is not None:
            if not guard.ruled_readable(vault, sources, target_n):
                raise Refused(CREDENTIAL, None)
            readable = True
    if entry is not None and entry.get("read") == "credential" and not readable:
        if entry.get("id") not in guard.readable_ids(vault):
            raise Refused(CREDENTIAL, None)
        readable = True
    if _attrs(path) & RECALL_ON_DATA_ACCESS:  # 讀前看一次目前的屬性，不靠清冊的 read（B-095：清冊過時會整份下載）
        raise Refused(PLACEHOLDER, PLACEHOLDER)
    if entry is not None and entry.get("read") == "placeholder":
        raise Refused(PLACEHOLDER, PLACEHOLDER)
    if entry is not None and entry.get("read") == "too_large":
        raise Refused(TOO_LARGE, None)
    if not os.path.isfile(long_path(path)):
        raise Refused(NOT_FOUND, None)
    return readable


SECRET_SUFFIX = ".secret"  # 密碼與守門（設計 session 2026-10-23）：「可以讀」的密碼檔的暫存，那一句答完就刪


def clean_secrets(vault):
    """刪 .system/temp/ 裡 *.secret*（密碼檔的暫存與掃描頁的圖）：Stop 收尾一律刪，Stop 沒跑到的下一句開頭刪。
    不進子夾、不跟連結；回刪不掉的檔名清單（呼叫端寫 hook-errors.log，不擋那一句）。"""
    d = Path(vault) / TEMP_POSIX
    failed = []
    if not d.is_dir() or _is_reparse(d):
        return failed
    for p in d.glob("*" + SECRET_SUFFIX + "*"):
        try:
            if p.is_file() and not _is_reparse(p):
                p.unlink()
        except OSError:
            failed.append(p.name)
    return failed


def _temp_name(entry, path, secret=False):
    """編號合 f-／out- 格式才拿來當檔名（清冊可能被改過，裁定 16），否則用指紋前 12 碼。
    secret（「可以讀」的密碼檔）：<編號>.secret.md，那一句答完就刪。"""
    tail = SECRET_SUFFIX + ".md" if secret else ".md"
    if entry is not None and isinstance(entry.get("id"), str) and _ID.fullmatch(entry["id"]):
        return f"{entry['id']}{tail}"
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12] + tail


def _extract(vault, kind, path):
    """回 (text, parts, encoding)。抽字上限 fingerprint_max_bytes（裁定 24）。"""
    cap = config.get(vault, "fingerprint_max_bytes")
    if kind == "docx":
        return extract.docx_text(path, cap), None, None
    if kind == "xlsx":
        text = extract.xlsx_text(path, config.get(vault, "card_source_big_chars"), cap)
        return text, sum(1 for ln in text.splitlines() if ln.startswith("## 工作表「")), None
    if kind == "pptx":
        text = extract.pptx_text(path, cap)
        return text, sum(1 for ln in text.splitlines() if ln.startswith("## 投影片 ")), None
    text, encoding = extract.plain_text(path, cap)
    return text.replace("\r\n", "\n").replace("\r", "\n"), None, encoding


def convert(vault, sources, entry, path, password=None, secret=False):
    """回 (result dict, 印給人的行)。讀不到丟 Refused。password：加密的 PDF 帶密碼開（只有 PDF，D-182）；
    真的用了密碼才開得了的，result[PASSWORD_USED] 是 True（呼叫端拿掉，不進 JSON）。
    secret：「可以讀」的密碼檔，暫存叫 <編號>.secret.md。"""
    real = os.path.realpath(str(path))
    result = {"kind": None, "temp": None, "chars": None, "parts": None, "encoding": None, "pages": None,
              "big": False, "path": real, "scanned_pages": None, "images": None}
    kind = extract.probe(path)
    ext = Path(str(path)).suffix.lower() or "沒有副檔名"
    used = False
    if kind == "encrypted" and ext == ".pdf":
        try:
            if extract.pdf_opens_without_password(path):
                kind, password = "pdf", None  # 只有擁有者密碼（不用密碼就能看）：給了密碼也不用（測試 28 F9）
            elif password:
                kind, used = "pdf", True
        except extract.Unreadable as e:
            raise Refused(str(e), str(e)) from None
    elif kind == "pdf":
        password = None  # 沒加密：給了密碼也不用、不問要不要記
    result["kind"] = kind
    result[PASSWORD_USED] = used
    if kind in KIND_REASONS:
        first, reason = KIND_REASONS[kind]
        raise Refused(first.format(ext=ext), reason.format(ext=ext))
    if kind == "pdf":
        return _convert_pdf(vault, entry, path, result, password, secret)
    if kind == "image":
        clean_temp(vault)
        return result, [f"圖片：用 Read 看：{real}"]
    try:
        text, parts, encoding = _extract(vault, kind, path)
    except extract.Unreadable as e:
        raise Refused(str(e), str(e)) from None
    _d, temp = _temp_target(vault, entry, path, secret)
    kio.write_text_atomic(temp, text + ("\n" if text and not text.endswith("\n") else ""))
    chars = len(text)
    truncated = kind == "xlsx" and bool(text) and text.splitlines()[-1].startswith("（只抽到工作表「")
    big = truncated or chars > config.get(vault, "card_source_big_chars")  # xlsx 抽到上限停下就是大檔（裁定 25）
    result.update(temp=str(temp.resolve()), chars=chars, parts=parts, encoding=encoding, big=big)
    detail = f"格式：{kind}" + (f"；{PARTS_WORD[kind]} {parts}" if kind in PARTS_WORD else "") + f"；字數 {chars}"
    lines = [f"暫存檔：{temp.resolve()}", detail]
    if encoding:
        lines.append(f"編碼：{encoding}")
    if big:
        lines.append("大檔：照 carding.md 摘要加定位")
    if chars == 0:
        lines.append("轉換結果是空的")
    return result, lines


def _temp_target(vault, entry, path, secret=False):
    """回 (temp 夾, 暫存檔路徑)；判斷都過了才清舊暫存（裁定 17），父夾不是 temp 就拒（裁定 16）。"""
    d = Path(vault) / TEMP_POSIX
    temp = d / _temp_name(entry, path, secret)
    clean_temp(vault)
    d.mkdir(parents=True, exist_ok=True)
    if os.path.realpath(str(temp.parent)) != os.path.realpath(str(d)):
        raise Refused(BAD_TEMP, None)
    return d, temp


def _convert_pdf(vault, entry, path, result, password=None, secret=False):
    """裁定 36：pypdfium2 抽文字層寫暫存檔（每頁頁碼註解），掃描頁轉 PNG（最多 pdf_read_pages_max 頁）。
    密碼錯不記待處理（密碼與守門：是給錯，不是讀不到）。"""
    try:
        text, pages, scanned = extract.pdf_pages_text(path, config.get(vault, "pdf_page_min_chars"), password=password)
    except extract.Unreadable as e:
        raise Refused(str(e), None if str(e) == WRONG_PASSWORD else str(e)) from None
    d, temp = _temp_target(vault, entry, path, secret)
    limit = config.get(vault, "pdf_read_pages_max")
    try:
        images = extract.render_pages(path, scanned, d, temp.stem, config.get(vault, "pdf_render_scale"), limit,
                                      password=password)
    except extract.Unreadable as e:
        raise Refused(str(e), str(e)) from None
    kio.write_text_atomic(temp, text + ("\n" if text and not text.endswith("\n") else ""))
    chars = len(text)
    big = chars > config.get(vault, "card_source_big_chars")
    result.update(temp=str(temp.resolve()), chars=chars, pages=pages, big=big, scanned_pages=scanned,
                  images=[p for _n, p in images])
    detail = f"格式：pdf；{pages} 頁" + (f"；掃描頁 {len(scanned)}（已轉圖）" if scanned else "")
    lines = [f"暫存檔：{temp.resolve()}", detail]
    lines += [f"圖：{p}（第 {n} 頁）" for n, p in images]
    if len(scanned) > limit:
        lines.append(f"掃描頁超過 {limit} 頁：只轉前 {limit} 頁，卡的讀取標「部分」")
    if big:
        lines.append("大檔：照 carding.md 摘要加定位")
    return result, lines


def _record_unreadable(vault, entry, uri, reason):
    """條目在清冊才寫；寫失敗只記 log，回待處理 id 或 None。"""
    if entry is None or reason is None:
        return None
    try:
        label = pending.file_label(uri or entry["locations"][0]["uri"])
        return pending.add(vault, "unreadable", [entry["id"]], f"{label}讀不到", reason, turn.current(vault)["turn"])
    except Exception as e:  # noqa: BLE001  待處理沒記到不改讀不到的結論
        from kb._lib import ledger
        ledger.log_error(vault, f"待處理沒記到：{type(e).__name__}: {e}", source="convert")
        return None


def _password(vault, args):
    """回 (密碼 或 None, 用的那組名稱 或 None, 授權過的種類 label 或 None)。--saved 照 D-181 驗：要 PDF 密碼、
    要授權過的種類或 --explicit。拒絕丟 Refused（不寫待處理），憑證管理員的拒絕照它的句子。"""
    from kb._lib import unlock
    if args.password and args.saved:
        raise Refused(ONE_PASSWORD, None)
    if args.password:
        return args.password, None, None
    if not args.saved:
        return None, None, None
    try:
        name = unlock.check_name(args.saved)
        kind, value = unlock.get(vault, name)
    except unlock.Refused as e:
        raise Refused(str(e), None) from None
    if kind != unlock.KIND_PDF:
        raise Refused(unlock.NOT_PDF.format(name), None)
    granted = unlock.kind_for(vault, name)
    if granted is None and not args.explicit:
        raise Refused(NOT_ALLOWED, None)
    return value, name, (granted or {}).get("label") or ""


def _after_unlock(vault, entry, saved, label, readable):
    """開成功之後：關掉「讀不到：有密碼」、--saved 的記進 opened、讀「可以讀」的密碼檔記 secret_shown。失敗只記 log。"""
    from kb._lib import unlock
    try:
        turn_id = turn.current(vault)["turn"]
        if entry is not None and saved is not None:
            pending.auto_done(vault, entry["id"], "unreadable", OPENED_HOW, turn_id)
            unlock.record_opened(vault, entry["id"], label, turn_id)
        if readable:
            turn.mark(vault, "secret_shown")
    except Exception as e:  # noqa: BLE001  後續記錄沒做到不改讀到的結論
        ledger.log_error(vault, f"開檔後的記錄沒做完：{type(e).__name__}", source="convert")


def _fail(out, as_json, line, pid=None):
    """失敗一律 exit 3；--json 時印 JSON（unreadable 是「讀不到：」後面那句），否則印句子。"""
    if line in ("讀不到：" + WRONG_PASSWORD, "讀不到：" + NOT_ALLOWED, "讀不到：" + ONE_PASSWORD) or (
            line.startswith("讀不到：這台電腦") or (line.startswith("讀不到：「") and line.endswith("不是 PDF 密碼"))):
        line = line[len("讀不到："):]  # 密碼的拒絕句照原樣一行（密碼與守門介面）
    out["unreadable"] = line[len("讀不到："):] if line.startswith("讀不到：") else line
    out["pending_id"] = pid
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(line)
        if pid:
            print(f"已記進待處理（{pid}）")
    return 3


def run(args):
    as_json = getattr(args, "json", False)
    out = {k: None for k in JSON_KEYS}
    found = guard.find_vault(os.getcwd())  # 只信 cwd 所在的知識庫（裁定 15）
    if found is None:
        return _fail(out, as_json, NOT_IN_VAULT)
    vault = Path(os.path.realpath(str(found)))
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    entry = path = uri = None
    try:
        try:
            sources = sources_lib.load(vault)
            entry, path, uri = resolve(vault, sources, args.target, os.getcwd())
            out.update(id=entry["id"] if entry else None, uri=uri, path=str(path) if path else None)
            readable = gate(vault, sources, entry, path)
            password, saved, label = _password(vault, args)
            result, lines = convert(vault, sources, entry, path, password, secret=readable)
        except MemoryError:
            raise Refused(TOO_LARGE, TOO_LARGE) from None
        except (zipfile.BadZipFile, ET.ParseError):
            raise Refused(BROKEN, BROKEN) from None
        except ValueError as e:  # 清冊或 sources.json 壞了
            return _fail(out, as_json, f"讀不到：狀態檔壞了（{e}）")
        except OSError:  # 原檔被 Word／Excel 開著、沒權限、暫存寫不進去：暫時狀態，不寫待處理
            raise Refused(LOCKED, None) from None
    except Refused as e:
        first, reason = e.args
        return _fail(out, as_json, f"讀不到：{first}", _record_unreadable(vault, entry, uri, reason))
    used = result.pop(PASSWORD_USED, False)
    out.update(result)
    if used and saved is None and entry is not None:
        try:
            pending.auto_done(vault, entry["id"], "unreadable", OPENED_HOW, turn.current(vault)["turn"])
        except Exception as e:  # noqa: BLE001
            ledger.log_error(vault, f"待處理沒關到：{type(e).__name__}", source="convert")
    _after_unlock(vault, entry, saved if used else None, label, readable)
    out.update(ask_save=bool(used and saved is None), unlocked=("saved" if saved else "password") if used else None)
    if out["ask_save"]:
        lines.append(ASK_SAVE_LINE)
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0
