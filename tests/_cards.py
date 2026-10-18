"""測試共用：檔案卡用的知識庫（tests/fixtures/inventory 複本，raw 的會議紀錄_1.docx 換成真 docx 再入冊）、照 header 寫卡。"""
import json
from pathlib import Path

from tests import _ooxml
from tests.test_lib_inventory_scan import make_inv_vault
from kb._lib import inventory, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

MEETING = "客戶案件/大安陳/會議紀錄_1.docx"
MEETING_BLOCKS = [("h", "大安陳第一次會議"), ("p", "討論廚房動線"), ("tbl", [["項目", "結論"], ["廚具", "L 型"]])]


def make_card_vault():
    """回 (tmp, V, raw)。真 docx 寫進 raw 複本（覆蓋假 zip），再全掃入冊。"""
    tmp, V, raw = make_inv_vault()
    _ooxml.docx(raw / MEETING, MEETING_BLOCKS)
    scan_apply(V)
    return tmp, V, raw


def scan_apply(V):
    report = inventory.scan(V, sources_lib.load(V))
    inventory.apply(V, report, turn.start(V, None, "")["turn"])
    return report


def entry_of(V, rel):
    inv = json.loads((Path(V) / ".system" / "inventory.json").read_text(encoding="utf-8"))
    return next(e for e in inv["entries"]
                if e["status"] == "active" and any(l["uri"] == "file://" + rel for l in e["locations"]))


def filled(header, title, kind="會議紀錄", date="2026-10-01", read="完整"):
    return (header.replace("標題: （填）", f"標題: {title}").replace("卡型: （填）", f"卡型: {kind}")
            .replace("文件日期: （填，YYYY-MM-DD 或 未載）", f"文件日期: {date}")
            .replace("讀取: （填，完整 或 部分）", f"讀取: {read}"))


def card_text(item, title, body="這是大安陳第一次會議的紀錄。〔第 1 段〕\n", **kw):
    return filled(item["header"], title, **kw) + f"\n# {title}\n\n{body}"


def card_path(V, item, title):
    return Path(V) / item["dir"] / item["filename_pattern"].replace("<標題>", title)


def write_card(V, item, title, **kw):
    p = card_path(V, item, title)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(card_text(item, title, **kw), encoding="utf-8", newline="\n")
    return p
