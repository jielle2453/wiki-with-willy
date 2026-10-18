"""檔案卡（D-101、D-104、D-105，ingest 製卡第 3 點）：檔頭、位置、開原檔、對帳與總目錄。

卡住 檔案索引/<別名>/<主位置相對夾>/<標題>-<編號去前綴>.md（D-029、D-036），檔頭中文鍵名（D-101），只認這題寫得出來的 YAML 子集。
檔頭機器欄位（編號、出處、原檔、指紋、內容指紋、製卡日期、範本版本、開原檔）由 header_for 印給製卡者照抄；
validate 給 PostToolUse 與測試用，只報第一個錯；todo 讀清冊與卡現算該做的事（D-104），不寫任何檔。
render_index 只讀 檔案索引/ 的卡檔頭寫根的 總目錄.md（D-105），不讀清冊，孤兒卡也列。
"""
import os
import re
import urllib.request
from datetime import date
from pathlib import Path

from kb._lib import config
from kb._lib.io import FileLock, write_text_atomic

CARDS_DIR = "檔案索引"
INDEX_POSIX = "總目錄.md"
KEYS = ("編號", "出處", "原檔", "指紋", "內容指紋", "標題", "卡型", "文件日期", "讀取", "製卡日期", "範本版本",
        "敏感資料種類", "開原檔")
OPTIONAL = ("內容指紋",)
LIST_KEYS = ("原檔", "敏感資料種類")
# 檔頭值的型別（測試後修正裁定 21）：這些要是字串，LIST_KEYS 要是清單
STR_KEYS = ("編號", "指紋", "內容指紋", "出處", "標題", "卡型", "文件日期", "讀取", "開原檔")
ORIGIN_TEXT = {"client_file": "客戶原檔", "intake": "對話收件", "ai_output": "AI 產出"}
FILL = "（填）"
DATE_FILL = "（填，YYYY-MM-DD 或 未載）"
READ_FILL = "（填，完整 或 部分）"
READ_VALUES = ("完整", "部分")
TEMPLATE_VERSION = "1"
SKIPPED_READS = ("credential", "placeholder", "too_large", "error")  # error 也列份數（裁定 29）
INDEX_HEAD = ("# 總目錄\n"
              "這是什麼：你的檔案都在這裡，點進去是那份檔的卡，卡裡有開原檔的連結。\n"
              "怎麼用：可以摺疊；找不到的就問我。\n"
              "由程式產生，改了會被覆蓋。\n")
INDEX_EMPTY = "還沒有卡；加資料夾後說「整理」就會有。"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 總目錄連結目標要編碼的字（實作疑義裁定 3）；% 先編，中文照原樣
_LINK_ESC = (("%", "%25"), (" ", "%20"), ("(", "%28"), (")", "%29"), ("[", "%5B"), ("]", "%5D"), ("#", "%23"))
# 總目錄連結文字與夾名（裁定 23）：反斜線與方括號跳脫，角括號換全形
_TEXT_ESC = (("\\", "\\\\"), ("[", "\\["), ("]", "\\]"), ("<", "＜"), (">", "＞"))


# ---------- 檔頭 ----------

def _scalar(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    return raw


def _value(raw):
    """鍵: 後面的值。清單只認一層 [a, b]，不遞迴；裡面再有方括號丟 ValueError（裁定 22）。"""
    raw = raw.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise ValueError("清單少了結尾的 ]")
        inner = raw[1:-1].strip()
        if "[" in inner or "]" in inner:
            raise ValueError("清單裡不能再有清單")
        return [_scalar(x) for x in inner.split(",")] if inner else []
    return _scalar(raw)


def parse_front(text):
    """解析卡檔頭：第一行 ---、到下一個 --- 為止；鍵: 值、鍵: [a, b]、鍵: 下接「  - 項」，值可帶雙引號。
    回 dict（照出現順序）；解析不了丟 ValueError（一句原因）。"""
    lines = str(text).lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("第一行不是 ---")
    front, current = {}, None
    for no, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            return front
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current is None or not isinstance(front.get(current), list):
                raise ValueError(f"第 {no} 行的清單項沒有接在鍵後面")
            front[current].append(_scalar(line.split("- ", 1)[1]))
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep or not key or line.startswith(" "):
            raise ValueError(f"第 {no} 行看不懂：{line.strip()[:20]}")
        if key in front:
            raise ValueError(f"「{key}」寫了兩次")
        front[key] = [] if not raw.strip() else _value(raw)
        current = key
    raise ValueError("少了結尾的 ---")


def _front_lines(front):
    out = ["---"]
    for key in KEYS:
        if key not in front:
            continue
        value = front[key]
        if key == "原檔":
            out.append("原檔:")
            out.extend(f"  - {v}" for v in value)
        elif isinstance(value, list):
            out.append(f"{key}: [{', '.join(value)}]")
        else:
            out.append(f"{key}: {value}")
    out.append("---")
    return out


# ---------- 位置與名稱 ----------

def _split(uri):
    body = str(uri)[len("file://"):] if str(uri).startswith("file://") else str(uri)
    alias, _, rel = body.partition("/")
    return alias, rel


def card_dir(entry):
    """檔案索引/<別名>/<主位置相對夾>（保留別名照字面）。"""
    alias, rel = _split(entry["locations"][0].get("uri", ""))
    parent = rel.rpartition("/")[0]
    return f"{CARDS_DIR}/{alias}" + (f"/{parent}" if parent else "")


def open_link(path):
    """主位置絕對路徑 → file: 加 pathname2url。存在的先 realpath 取磁碟大小寫（實作疑義裁定 5），不存在照原樣。"""
    p = str(path)
    if os.path.exists(p):
        p = os.path.realpath(p)
    return "file:" + urllib.request.pathname2url(p)


def entry_link(vault, sources, entry):
    from kb._lib import inventory
    path = inventory.path_of(vault, sources, entry["locations"][0].get("uri", ""))
    return open_link(path) if path is not None else ""


def safe_title(title, max_chars):
    """去掉 \\ / : * ? \" < > | 與控制字元、壓空白、去結尾空白與點（B-040）、截 max_chars；空的回 None。"""
    text = " ".join(_UNSAFE.sub(lambda m: " " if m.group() in "\t\n\r" else "", str(title or "")).split())
    text = text[:max_chars].rstrip(" .")
    return text or None


def short_id(entry_id):
    return str(entry_id).split("-", 1)[1] if "-" in str(entry_id) else str(entry_id)


def expected_name(title, entry_id, max_chars):
    return f"{safe_title(title, max_chars) or ''}-{short_id(entry_id)}.md"


def filename_ok(name, entry_id, title, max_chars):
    return name == expected_name(title, entry_id, max_chars)


def header_for(vault, sources, entry, today=None):
    """印給製卡者照抄的檔頭：機器欄位填好，製卡者欄位放（填）。today 是 YYYY-MM-DD，沒給用今天。"""
    fp = (entry.get("fingerprints") or [{}])[-1]
    front = {
        "編號": entry["id"],
        "出處": ORIGIN_TEXT.get(entry.get("origin"), entry.get("origin", "")),
        "原檔": [loc.get("uri", "") for loc in entry.get("locations") or []],
        "指紋": fp.get("sha256", ""),
    }
    if fp.get("text_sha256"):
        front["內容指紋"] = fp["text_sha256"]
    front.update({"標題": FILL, "卡型": FILL, "文件日期": DATE_FILL, "讀取": READ_FILL,
                  "製卡日期": today or date.today().isoformat(),
                  "範本版本": TEMPLATE_VERSION, "敏感資料種類": [], "開原檔": entry_link(vault, sources, entry)})
    return "\n".join(_front_lines(front)) + "\n"


# ---------- 對帳 ----------

def _walk_cards(vault):
    """檔案索引/ 底下全部 .md（跳過 . 開頭的夾與檔），回相對總資料夾的路徑（正斜線），照路徑排。"""
    root = Path(vault) / CARDS_DIR
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.lower().endswith(".md") and not name.startswith("."):
                out.append(Path(dirpath, name).relative_to(vault).as_posix())
    return sorted(out)


def _read(vault, rel):
    return (Path(vault) / rel).read_text(encoding="utf-8")


def _scan_groups(vault):
    """回 (groups, broken)：groups {編號: [{"path", "front"}, …]}（照路徑排）；解析不了、沒有編號的進 broken。"""
    groups, broken = {}, []
    for rel in _walk_cards(vault):
        try:
            front = parse_front(_read(vault, rel))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            broken.append({"path": rel, "reason": f"檔頭解析不了：{e}"})
            continue
        cid = front.get("編號")
        if not isinstance(cid, str) or not cid:
            broken.append({"path": rel, "reason": "檔頭「編號」的格式不對" if "編號" in front else "檔頭少了「編號」"})
        else:
            groups.setdefault(cid, []).append({"path": rel, "front": front})
    return groups, broken


def scan_cards(vault):
    """回 (cards, broken)：cards {編號: {"path", "front"}}（同編號多張取第一張，其餘進 broken）。"""
    groups, broken = _scan_groups(vault)
    found = {}
    for cid, group in groups.items():
        found[cid] = group[0]
        broken.extend({"path": c["path"], "reason": f"編號 {cid} 已經有卡：{group[0]['path']}"} for c in group[1:])
    return found, broken


def _filled(value):
    return isinstance(value, str) and value.strip() and "（填" not in value


def problems(vault, rel, front, entries_by_id, sources=None):
    """檔頭解析得了之後的全部問題 [(代號, 原因)]，照 validate 的順序。代號給 todo 分桶。"""
    from kb._lib import sources as sources_lib
    for key in KEYS:
        if key not in OPTIONAL and key not in front:
            return [("missing_key", f"檔頭少了「{key}」")]
    for key in KEYS:  # 型別先驗（裁定 21）：不對的值不拿去查清冊、比字串
        if key not in front:
            continue
        if (key in LIST_KEYS and not isinstance(front[key], list)) or (key in STR_KEYS and not isinstance(front[key], str)):
            return [("format", f"檔頭「{key}」的格式不對")]
    if not front["原檔"]:
        return [("missing_key", "檔頭少了「原檔」")]
    cid = front["編號"]
    entry = entries_by_id.get(cid)
    if entry is None:
        return [("unknown_id", f"編號 {cid} 不在清冊")]
    if entry.get("status") != "active":
        return [("inactive", f"編號 {cid} 的原檔已不在（{entry.get('status')}）")]
    if sources is None:
        sources = sources_lib.load(vault)
    max_chars = config.get(vault, "card_title_max_chars")
    out = []
    fp = (entry.get("fingerprints") or [{}])[-1]
    text_fp = front.get("內容指紋")
    if front["指紋"] != fp.get("sha256") and not (text_fp and text_fp == fp.get("text_sha256")):
        out.append(("fingerprint", "指紋不是現行的，重跑 kb cards todo 拿新檔頭"))
    want = ORIGIN_TEXT.get(entry.get("origin"), entry.get("origin"))
    if front["出處"] != want:
        out.append(("origin", f"出處該是 {want}"))
    if front["開原檔"] != entry_link(vault, sources, entry):
        out.append(("link", "開原檔那行照 kb cards todo 印的抄"))
    folder, _, name = rel.rpartition("/")
    if folder.casefold() != card_dir(entry).casefold():
        out.append(("dir", f"卡該放在 {card_dir(entry)}"))
    if not _filled(front["標題"]):
        out.append(("title", "標題還沒填"))
    elif not filename_ok(name, cid, front["標題"], max_chars):
        out.append(("filename", f"檔名該是 {expected_name(front['標題'], cid, max_chars)}"))
    if not _filled(front["卡型"]):
        out.append(("kind", "卡型還沒填"))
    if not (front["文件日期"] == "未載" or _DATE.match(front["文件日期"])):
        out.append(("date", "文件日期要寫 YYYY-MM-DD 或 未載"))
    if front["讀取"] not in READ_VALUES:
        out.append(("read", "讀取要寫 完整 或 部分"))
    if front["範本版本"] != TEMPLATE_VERSION:
        out.append(("version", f"範本版本該是 {TEMPLATE_VERSION}"))
    return out


def safe_problems(vault, rel, front, entries_by_id, sources=None):
    """problems 整段包住：任何例外都當成解析不了（裁定 21，不靜默放行）。"""
    try:
        return problems(vault, rel, front, entries_by_id, sources)
    except Exception as e:  # noqa: BLE001
        return [("crash", f"檔頭解析不了：{type(e).__name__}")]


def validate(vault, rel, entries_by_id, sources=None):
    """回 (ok, reason)；只報第一個錯。標題檢查在檔名之前（標題還是（填）時先講標題）。"""
    try:
        front = parse_front(_read(vault, rel))
    except (ValueError, UnicodeDecodeError) as e:
        return False, f"檔頭解析不了：{e}"
    found = safe_problems(vault, rel, front, entries_by_id, sources)
    return (False, found[0][1]) if found else (True, "")


def _uris(entry):
    return [loc.get("uri", "") for loc in entry.get("locations") or []]


def _created_date(entry):
    """條目產出那天 YYYY-MM-DD：created_turn 的代號（YYYYMMDD-…）優先，沒有用 first_seen；都沒有回 None。"""
    turn_id = str(entry.get("created_turn") or "")
    if re.match(r"^\d{8}-", turn_id):
        return f"{turn_id[:4]}-{turn_id[4:6]}-{turn_id[6:8]}"
    first = str(entry.get("first_seen") or "")
    return first[:10] if _DATE.match(first[:10]) else None


PDF_MISSING_HEAD = "還沒裝 PDF 元件"
PDF_BACK = "PDF 元件裝好了"


def _restore_pdf(vault):
    """裁定 36：pypdfium2 裝好了，就把 reason 是「還沒裝 PDF 元件」的 open／dismissed 待處理 done（備進回合），回關掉幾份。
    todo 只有這一處會寫檔；沒有這種條目就什麼都不碰。"""
    from kb._lib import extract, pending, turn
    try:
        extract._pdfium()
        items = pending.load(vault)["items"]
    except (extract.Unreadable, ValueError):
        return 0
    fids = {(i.get("file_ids") or [None])[0] for i in items
            if i.get("kind") == "unreadable" and i.get("status") in ("open", "dismissed")
            and str(i.get("reason") or "").startswith(PDF_MISSING_HEAD)}
    if not fids:
        return 0
    turn_id = turn.current(vault)["turn"]
    return sum(1 for fid in fids
               if pending.auto_done(vault, fid, "unreadable", PDF_BACK, turn_id, statuses=("open", "dismissed")))


def _mtime(vault, rel):
    try:
        return (Path(vault) / rel).stat().st_mtime
    except OSError:
        return 0


def todo(vault, sources):
    """讀清冊 active 條目、卡與待處理，分 new、stale、moved、duplicates、broken、orphans、unreadable、missing、skipped（D-104）。
    不寫檔、不開原檔（kind 只看副檔名，裁定 28）。"""
    from kb._lib import extract, inventory, pending
    restored = _restore_pdf(vault)
    entries = inventory.load(vault)["entries"]
    by_id = {e.get("id"): e for e in entries}
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []

    def first_file(i):
        return (i.get("file_ids") or [None])[0]
    opened = [i for i in items if i.get("status") == "open"]
    unreadable_open = {first_file(i): i for i in opened if i.get("kind") == "unreadable"}
    # 客戶說先跳過的（dismissed）也不列回 new，檔案換了才重讀（裁定 31）
    unreadable_skip = set(unreadable_open) | {first_file(i) for i in items
                                              if i.get("kind") == "unreadable" and i.get("status") == "dismissed"}
    missing_open = {first_file(i): i for i in opened if i.get("kind") == "missing_file"}
    groups, broken = _scan_groups(vault)
    out = {"new": [], "stale": [], "moved": [], "duplicates": [], "orphans": [], "unreadable": [], "missing": [],
           "skipped": {k: 0 for k in SKIPPED_READS}, "broken": list(broken), "pdf_restored": restored}
    today = date.today().isoformat()

    def work_item(entry, card):
        path = inventory.path_of(vault, sources, entry["locations"][0]["uri"])
        return {"id": entry["id"], "uri": entry["locations"][0]["uri"], "path": str(path) if path else None,
                "origin": entry.get("origin"), "kind": extract.kind_by_name(entry["locations"][0]["uri"]),
                "dir": card_dir(entry), "filename_pattern": f"<標題>-{short_id(entry['id'])}.md",
                "existing_card": card["path"] if card else None,
                "created": _created_date(entry),
                "header": header_for(vault, sources, entry, today)}

    def broken_item(entry, card, reason):
        """編號對得到 active 條目的壞卡：多帶 id 與 header，製卡者照它整張寫回 path（審閱後補）。"""
        return {"path": card["path"], "reason": reason, "id": entry["id"],
                "header": header_for(vault, sources, entry, today)}

    carded = set()
    for cid, group in groups.items():
        entry = by_id.get(cid)
        if entry is None or entry.get("status") != "active":
            out["orphans"].extend(c["path"] for c in group)
            continue
        carded.add(cid)
        codes_of = {c["path"]: safe_problems(vault, c["path"], c["front"], by_id, sources) for c in group}
        if len(group) > 1:  # 同編號多張（裁定 20）：全過的那張是正本；沒有就留最新的，其餘交 sync 刪
            good = [c for c in group if not codes_of[c["path"]]]
            keep = good[0] if good else max(group, key=lambda c: _mtime(vault, c["path"]))
            out["duplicates"].append({"id": cid, "keep": keep["path"],
                                      "drop": [c["path"] for c in group if c is not keep]})
        else:
            keep = group[0]
        codes = codes_of[keep["path"]]
        names = dict(codes)
        others = [c for c in codes if c[0] != "link"]
        if names.keys() & {"missing_key", "format", "crash"}:
            out["broken"].append(broken_item(entry, keep, codes[0][1]))
        elif "dir" in names or keep["front"].get("原檔") != _uris(entry):
            out["moved"].append({"id": cid, "from": keep["path"], "to": card_dir(entry)})
        elif "fingerprint" in names:
            out["stale"].append(work_item(entry, keep))
        elif others:  # broken 只收開原檔以外的錯（裁定 19）
            out["broken"].append(broken_item(entry, keep, others[0][1]))
        elif "link" in names:  # 只有開原檔對不上（來源搬家、連結形式改了）：sync 重寫（裁定 19）
            out["moved"].append({"id": cid, "from": keep["path"], "to": card_dir(entry)})
    for entry in entries:
        status, eid = entry.get("status"), entry.get("id")
        if status == "missing" and entry.get("locations"):
            item = missing_open.get(eid)
            if item is None:
                continue  # 只列還沒答的；答過「先留著」「不用管」的不再問（裁定 31）
            out["missing"].append({"id": eid, "uri": entry["locations"][0]["uri"],
                                   "missing_since": entry.get("missing_since"), "pending_id": item["id"],
                                   "question": pending.line_of(item)})
            continue
        if status != "active" or not entry.get("locations"):
            continue
        if entry.get("read") in SKIPPED_READS:
            out["skipped"][entry["read"]] += 1
            continue
        if eid in unreadable_open:
            item = unreadable_open[eid]
            out["unreadable"].append({"id": eid, "uri": entry["locations"][0]["uri"], "reason": item.get("reason"),
                                      "pending_id": item["id"]})
            continue
        if entry.get("read") == "ok" and eid not in carded and eid not in unreadable_skip:
            out["new"].append(work_item(entry, None))
    return out


# ---------- 搬卡 ----------

def rewrite_locations(text, uris, link):
    """只換檔頭的 原檔 清單與 開原檔 一行，其他行原樣（kb cards sync）。"""
    lines = text.splitlines(keepends=True)
    out, i, in_front = [], 0, False
    nl = "\n"
    while i < len(lines):
        line = lines[i]
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("﻿") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
        elif in_front and bare.startswith("原檔:"):
            out.append("原檔:" + nl)
            out.extend(f"  - {u}{nl}" for u in uris)
            i += 1
            while i < len(lines) and lines[i].startswith(("  - ", "- ")):
                i += 1
            continue
        elif in_front and bare.startswith("開原檔:"):
            out.append(f"開原檔: {link}{nl}")
            i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


# ---------- 總目錄 ----------

def _link_target(rel):
    for a, b in _LINK_ESC:
        rel = rel.replace(a, b)
    return rel


def _link_text(text):
    text = str(text)
    for a, b in _TEXT_ESC:
        text = text.replace(a, b)
    return text


def render_index(vault, turn_id=None):
    """寫總資料夾根的 總目錄.md：巢狀清單（第一層來源別名、往下鏡射夾、葉子是卡），同層夾在前卡在後、各自按名稱排；
    解析不了的卡用檔名當標題。有 turn_id 就先備進回合（實作疑義裁定 4）。整段在 總目錄.md 的鎖裡（裁定 26）。回卡數。"""
    with FileLock(Path(vault) / INDEX_POSIX):
        return _render_index(vault, turn_id)


def _render_index(vault, turn_id):
    tree = {}
    count = 0
    for rel in _walk_cards(vault):
        parts = rel.split("/")[1:]
        node = tree
        for folder in parts[:-1]:
            node = node.setdefault(("d", folder), {})
        try:
            title = parse_front(_read(vault, rel)).get("標題")
        except (ValueError, OSError, UnicodeDecodeError):
            title = None
        if not _filled(title):
            title = parts[-1][:-3]
        node[("c", parts[-1])] = (title, rel)
        count += 1
    lines = []

    def emit(node, depth):
        for key in sorted(node, key=lambda k: (k[0] != "d", k[1])):
            pad = "  " * depth
            if key[0] == "d":
                lines.append(f"{pad}- {_link_text(key[1])}")
                emit(node[key], depth + 1)
            else:
                title, rel = node[key]
                lines.append(f"{pad}- [{_link_text(title)}]({_link_target(rel)})")
    emit(tree, 0)
    body = "\n".join(lines) if lines else INDEX_EMPTY
    text = f"{INDEX_HEAD}\n{body}\n\n共 {count} 張卡\n"
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, INDEX_POSIX, turn_id)
    write_text_atomic(Path(vault) / INDEX_POSIX, text)
    return count
