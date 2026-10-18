"""kb convert <目標>（D-099，ingest 製卡第 2 點）：把一份原檔抽成純文字暫存檔給製卡者 Read；PDF、圖片只指路給 Read。

目標三種寫法：清冊編號（f-…、out-…）、uri（file://別名/相對路徑）、磁碟路徑（絕對或相對 cwd）；路徑經 inventory.uri_of
反查清冊條目，對不到也照轉但不寫待處理。每次先清 .system/temp/ 裡超過 temp_keep_hours 的暫存檔。
守門對 kb.py 一律放行，所以抽字前先做守門同一套判斷（實作疑義裁定 8）：guard.classify 是 Z10／Z10b 或條目 excluded、
Z11、Z1／Z6／Z7／Z8 都拒；來源根以下的段像密碼或金鑰也拒（總資料夾內部豁免，D-081）；這四種不寫待處理。
找不到原檔（含 status missing）只印一行、不寫待處理（實作疑義裁定 2）。其他讀不到的 exit 3，條目在清冊就寫一條 unreadable。
任何情況不印檔案內容到終端機（D-022）。
"""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

from kb import guard
from kb._lib import config, credentials, extract, inventory, pending, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.paths import long_path, normalize
from kb.inventory_cli import NOT_IN_VAULT, find_vault

TEMP_POSIX = ".system/temp"
CLI_LOCK_TIMEOUT = 30
_ID = re.compile(r"^(f|out)-[0-9a-f]+$")
NOT_CARD_ZONES = ("Z1", "Z6", "Z7", "Z8")
EXCLUDED = "這個位置你說過不讀"
CREDENTIAL = "檔名像密碼或金鑰，預設不讀"
OUTSIDE = "不在登記的資料夾裡"
NOT_CARD = "這不是要製卡的檔"
NOT_FOUND = "找不到原檔"
PLACEHOLDER = "只在雲端、還沒下載到電腦"
# 讀不到的第一行（「讀不到：」後面）與待處理記的 reason
KIND_REASONS = {
    "legacy_office": ("舊格式（{ext}），請另存成新格式", "舊格式（{ext}）"),
    "encrypted": ("有密碼", "有密碼"),
    "unsupported": ("不支援的格式（{ext}）", "不支援的格式（{ext}）"),
}
PARTS_WORD = {"xlsx": "工作表", "pptx": "投影片"}


class Refused(Exception):
    """讀不到：args = (第一行原因, 待處理 reason 或 None)。reason 是 None 就不寫待處理。"""


def add_arguments(sub):
    sub.add_argument("target", help="清冊編號（f-…）、uri（file://別名/相對路徑）或磁碟路徑")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def clean_temp(vault):
    """.system/temp/ 裡修改時間超過 temp_keep_hours 的檔刪掉；刪不掉的留著。"""
    d = Path(vault) / TEMP_POSIX
    if not d.is_dir():
        return
    limit = config.get(vault, "temp_keep_hours") * 3600
    now = time.time()
    for p in d.iterdir():
        try:
            if p.is_file() and now - p.stat().st_mtime >= limit:
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
    """守門同一套判斷（實作疑義裁定 8），加清冊的 read 與 status。不過丟 Refused。"""
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
            raise Refused(CREDENTIAL, None)
    if entry is not None and entry.get("read") == "credential":
        raise Refused(CREDENTIAL, None)
    if entry is not None and entry.get("read") == "placeholder":
        raise Refused(PLACEHOLDER, PLACEHOLDER)
    if not os.path.isfile(long_path(path)):
        raise Refused(NOT_FOUND, None)


def _temp_name(entry, path):
    if entry is not None:
        return f"{entry['id']}.md"
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12] + ".md"


def _extract(vault, kind, path):
    """回 (text, parts, encoding)。"""
    if kind == "docx":
        return extract.docx_text(path), None, None
    if kind == "xlsx":
        text = extract.xlsx_text(path, config.get(vault, "card_source_big_chars"))
        return text, sum(1 for ln in text.splitlines() if ln.startswith("## 工作表「")), None
    if kind == "pptx":
        text = extract.pptx_text(path)
        return text, sum(1 for ln in text.splitlines() if ln.startswith("## 投影片 ")), None
    text, encoding = extract.plain_text(path)
    return text.replace("\r\n", "\n").replace("\r", "\n"), None, encoding


def convert(vault, sources, entry, path):
    """回 (result dict, 印給人的行)。讀不到丟 Refused。"""
    real = os.path.realpath(str(path))
    result = {"kind": None, "temp": None, "chars": None, "parts": None, "encoding": None, "pages": None,
              "big": False, "path": real}
    kind = extract.probe(path)
    result["kind"] = kind
    ext = Path(str(path)).suffix.lower() or "沒有副檔名"
    if kind in KIND_REASONS:
        first, reason = KIND_REASONS[kind]
        raise Refused(first.format(ext=ext), reason.format(ext=ext))
    if kind == "pdf":
        pages = extract.pdf_pages(path)
        result["pages"] = pages
        lines = [f"PDF：{f'{pages} 頁' if pages else '頁數不明'}；用 Read 讀原檔：{real}"]
        limit = config.get(vault, "pdf_read_pages_max")
        if pages and pages > limit:
            lines.append(f"超過 {limit} 頁：先讀前 {limit} 頁，卡的讀取標「部分」")
        return result, lines
    if kind == "image":
        return result, [f"圖片：用 Read 看：{real}"]
    try:
        text, parts, encoding = _extract(vault, kind, path)
    except extract.Unreadable as e:
        raise Refused(str(e), str(e)) from None
    d = Path(vault) / TEMP_POSIX
    d.mkdir(parents=True, exist_ok=True)
    temp = d / _temp_name(entry, path)
    kio.write_text_atomic(temp, text + ("\n" if text and not text.endswith("\n") else ""))
    chars = len(text)
    big = chars > config.get(vault, "card_source_big_chars")
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


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    out = {k: None for k in ("id", "uri", "path", "kind", "temp", "chars", "parts", "encoding", "pages", "big",
                             "unreadable", "pending_id")}
    try:
        clean_temp(vault)
        sources = sources_lib.load(vault)
        entry = path = uri = None
        entry, path, uri = resolve(vault, sources, args.target, os.getcwd())
        out.update(id=entry["id"] if entry else None, uri=uri, path=str(path) if path else None)
        gate(vault, sources, entry, path)
        result, lines = convert(vault, sources, entry, path)
        out.update(result)
    except Refused as e:
        first, reason = e.args
        out["unreadable"] = first
        pid = _record_unreadable(vault, entry, uri, reason)
        out["pending_id"] = pid
        if as_json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(f"讀不到：{first}")
            if pid:
                print(f"已記進待處理（{pid}）")
        return 3
    except ValueError as e:  # 清冊或 sources.json 壞了
        print(f"讀不到：狀態檔壞了（{e}）")
        return 3
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0
