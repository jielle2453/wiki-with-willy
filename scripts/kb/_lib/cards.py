"""檔案卡（D-101、D-104、D-105，ingest 製卡第 3 點）：檔頭、位置、開原檔、對帳與總目錄。

卡住 檔案索引/<別名>/<主位置相對夾>/<標題>-<編號去前綴>.md（D-029、D-036），檔頭中文鍵名（D-101），只認這題寫得出來的 YAML 子集。
檔頭機器欄位（編號、出處、原檔、指紋、內容指紋、製卡日期、範本版本、開原檔）由 header_for 印給製卡者照抄；
validate 給 PostToolUse 與測試用，只報第一個錯；todo 讀清冊與卡現算該做的事（D-104），不寫任何檔。
render_index 只讀 檔案索引/ 的卡檔頭寫根的 總目錄.md（D-105），不讀清冊，孤兒卡也列。
todo 的結果併進 plan.build 的 plan、asks、tell、announce（門檻內開始整理前先說的一句，D-100 修訂）；
sync 是 kb cards sync 的本體，型別改名與合併（kb profile type）也叫它讓卡當場跟著原檔搬。
note 改知識第 5 點（D-147）：mark_redo 是 kb cards redo 的本體（清冊記 redo，todo 把它列進 stale、帶 reason），
mount 是 kb cards mount 的本體（重設一張卡掛的實體）。
"""
import os
import re
import urllib.request
from datetime import date
from pathlib import Path

from kb._lib import config, profile
from kb._lib.areas import HOLD_DIR
from kb._lib.io import FileLock, write_text_atomic

CARDS_DIR = "檔案索引"
INDEX_POSIX = "總目錄.md"
KEYS = ("編號", "出處", "收件", "原檔", "指紋", "內容指紋", "標題", "卡型", "實體", "提到", "待補型別", "文件日期", "讀取", "製卡日期",
        "範本版本", "敏感資料種類", "開原檔")
OPTIONAL = ("內容指紋", "收件", "提到")  # 收件：只有 kb intake 收的檔有（收件匣第 2 點）；提到：範本第三版加的選填欄，舊卡沒有全算主要（放寬丙 7）
V2_KEYS = ("實體", "待補型別")  # 範本第二版加的兩欄（實體與綜合卡第 4 點）；第一版的卡沒有，不算少鍵
LIST_KEYS = ("原檔", "敏感資料種類", "實體", "提到", "待補型別")
# 程式照清冊補的選填欄（不在 KEYS：範本、header、對帳都不管它們，只驗型別）：卡式只有指路卡寫「指路」（kb cards mode，放寬丙 11，D-331）；
# 封存寫 YYYY-MM-DD（kb archive，放寬丙 12，D-332）；兩欄都由 cards sync_fronts 照清冊補或拿掉。指路卡不要求「日期與數字」段（段落本來就不驗）
PROGRAM_KEYS = ("卡式", "封存")
# 檔頭值的型別（測試後修正裁定 21）：這些要是字串，LIST_KEYS 要是清單
STR_KEYS = ("編號", "指紋", "內容指紋", "出處", "收件", "標題", "卡型", "文件日期", "讀取", "開原檔", *PROGRAM_KEYS)
ORIGIN_TEXT = {"client_file": "客戶原檔", "intake": "對話收件", "ai_output": "AI 產出"}
FILL = "（填）"
DATE_FILL = "（填，YYYY-MM-DD 或 未載）"
READ_FILL = "（填，完整 或 部分）"
READ_VALUES = ("完整", "部分")
TEMPLATE_VERSION = "3"
OK_VERSIONS = ("2", "3")  # 第二版的卡照舊有效：只少「提到」一欄，全部算主要，不重做（放寬丙 7，設計自定）
OLD_TEMPLATE_VERSION = "1"  # 第一版的卡只補掛載（D-116），不算檔頭有問題
SKIPPED_READS = ("credential", "placeholder", "too_large", "error")  # error 也列份數（裁定 29）
REDO_REASON = "客戶說卡讀錯了"
# orphans 的原因（B-059）：其他狀態（missing、turn_reverted、superseded）照填狀態名（疑義第 7 條）
ORPHAN_REASONS = {None: "not_in_inventory", "excluded": "excluded", "deleted": "deleted"}
INDEX_HEAD = ("# 總目錄\n"
              "這是什麼：你的檔案都在這裡，點進去是那份檔的卡，卡裡有開原檔的連結。\n"
              "怎麼用：可以摺疊；找不到的就問我。\n"
              "由程式產生，改了會被覆蓋。\n")
INDEX_EMPTY = "還沒有卡；加資料夾後說「整理」就會有。"
INDEX_NO_SUMMARY = "還沒有綜合卡。"
# 總目錄「檔案」那節葉子前面的種類圖示（放寬丙 8，D-313）：副檔名 → 圖示，認不得的用 DEFAULT_ICON；設定值 index_icons 設 false 就不印
INDEX_ICONS = (((".pdf",), "📕"), ((".doc", ".docx"), "📘"), ((".xls", ".xlsx", ".csv"), "📗"), ((".ppt", ".pptx"), "📙"),
               ((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff"), "🖼️"))
DEFAULT_ICON = "📄"
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


LINK_DIR = "線上"  # link 條目沒給型別時卡放的夾（produce 第 5 點）
DRAFT_SUFFIX = "（草稿）.md"


def is_draft(uri):
    """暫存區裡的草稿（D-163）：檔名以（草稿）.md 結尾。todo 不列它製卡。"""
    alias, rel = _split(uri)
    return alias == HOLD_DIR and rel.endswith(DRAFT_SUFFIX)


def card_dir(entry):
    """檔案索引/<別名>/<主位置相對夾>（保留別名照字面）；link 條目放 檔案索引/暫存區/<型別>（沒給型別放 線上）。"""
    if entry.get("kind") == "link":
        return f"{CARDS_DIR}/{HOLD_DIR}/{entry.get('type') or LINK_DIR}"
    alias, rel = _split(entry["locations"][0].get("uri", ""))
    parent = rel.rpartition("/")[0]
    return f"{CARDS_DIR}/{alias}" + (f"/{parent}" if parent else "")


def open_link(path):
    """主位置絕對路徑 → file: 加 pathname2url。存在的先 realpath 取磁碟大小寫（實作疑義裁定 5），不存在照原樣。"""
    p = str(path)
    if os.path.exists(p):
        p = os.path.realpath(p)
    return "file:" + urllib.request.pathname2url(p)


OPEN_PROTOCOL = "obsidian://wiki-with-willy-open?id="


def old_link_same_place(vault, sources, entry, link):
    """開原檔是舊格式（file:）、指著原檔現在的位置：先不換（刪綜合卡說一聲 裁定 5：1 萬張卡升級後第一句要等約 9 分鐘），
    卡重做或真的搬了才順便換。只解連結、比字串，不碰磁碟。"""
    from kb._lib import inventory

    if entry.get("kind") == "link" or not str(link).startswith("file:") or not entry.get("locations"):
        return False
    try:
        old = urllib.request.url2pathname(str(link)[len("file:"):])
        now = inventory.path_of(vault, sources, entry["locations"][0].get("uri", ""))
    except (ValueError, OSError):
        return False
    return now is not None and os.path.normcase(os.path.normpath(old)) == os.path.normcase(os.path.normpath(str(now)))


def entry_link(vault, sources, entry):
    """開原檔（放寬丙 9，D-328）：Obsidian 外掛連結 obsidian://wiki-with-willy-open?id=<清冊編號>，點了由外掛叫 kb 查這台的位置再開，
    所以來源搬家、換電腦卡不用重寫；link 條目是網址本身。連結不帶 vault=（開的是目前這個知識庫；多個知識庫同時開的情形待 Obsidian 實跑確認）。"""
    if entry.get("kind") == "link":
        return entry["locations"][0].get("uri", "")
    return OPEN_PROTOCOL + str(entry.get("id", ""))


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


def prefill_entities(entry, ent_data, vault=None):
    """原檔主位置在知識庫實體夾裡（file://知識庫/<型別夾>/<名稱>/…）且 <名稱> 是登記的 active 實體（型別也對）→ [<名稱>]，
    否則 []（D-043：位置決定掛哪個）。link 條目取 output link 給的 --entity 裡還登記著的（produce 第 5 點）。
    型別夾名照設定檔換成型別名（profile.type_of_folder，型別夾編號）；vault 沒給或不是型別夾照夾名比。
    只在製卡時預填（header_for、todo 的 prefill）：之後任何地方都不得照位置重算「實體」（放寬丙 5-1，D-308）——
    原檔被搬了，掛哪個實體交 AI 判斷（kb changes --moved、kb cards mount），sync 只搬卡、不動「實體」。"""
    from kb._lib import entities
    from kb._lib.inventory import KNOWLEDGE
    if entry.get("kind") == "link":
        if ent_data is None:
            return []
        out = []
        for name in entry.get("entities") or []:
            found, _hit = entities.find(ent_data, name)
            if found is not None and found.get("status") == "active" and found["name"] not in out:
                out.append(found["name"])
        return out
    alias, rel = _split((entry.get("locations") or [{}])[0].get("uri", ""))
    parts = rel.split("/")
    if alias != KNOWLEDGE or len(parts) < 3 or ent_data is None:
        return []
    found, _hit = entities.find(ent_data, parts[1])
    type_ = (profile.type_of_folder(vault, parts[0]) if vault is not None else None) or parts[0]
    if (found is None or found.get("status") != "active" or entities.is_self(found)
            or entities.key(found.get("type")) != entities.key(type_) or entities.key(found["name"]) != entities.key(parts[1])):
        return []
    return [found["name"]]


def _entities_or_none(vault):
    from kb._lib import entities
    try:
        return entities.load(vault)
    except ValueError:
        return None


NO_FP = "-"  # link 條目還沒有下載的檔、沒有指紋時卡上寫的（空值會被讀成清單）


def current_fp(entry):
    """卡上「指紋」該是的值：現行指紋的 sha256；link 條目還沒有指紋回 -。"""
    fp = (entry.get("fingerprints") or [{}])[-1]
    if not fp.get("sha256") and entry.get("kind") == "link":
        return NO_FP
    return fp.get("sha256", "")


INTAKE_WRONG = "收件那行照 kb cards todo 印的抄"


def intake_text(entry):
    """卡上「收件」那行該是的值（收件匣第 2 點，D-242、D-243）：<收件日期>，有 from 接「，從<from>」，從對話紀錄取的圖接「，對話裡的版本」。
    條目沒有 intake 欄（客戶自己丟進收件匣的、舊的）回 None：卡上不要求這行。"""
    info = entry.get("intake")
    if not isinstance(info, dict):
        return None
    out = str(info.get("received") or "")[:10]
    if info.get("from"):
        out += f"，從{info['from']}"
    if info.get("copy"):
        out += "，對話裡的版本"
    return out


def header_for(vault, sources, entry, today=None, ent_data=False):
    """印給製卡者照抄的檔頭：機器欄位填好，製卡者欄位放（填）；實體 預填實體夾的那個、待補型別 空。
    today 是 YYYY-MM-DD，沒給用今天；ent_data 是 entities.load 的結果（沒給自己讀）。"""
    if ent_data is False:
        ent_data = _entities_or_none(vault)
    fp = (entry.get("fingerprints") or [{}])[-1]
    front = {
        "編號": entry["id"],
        "出處": ORIGIN_TEXT.get(entry.get("origin"), entry.get("origin", "")),
        "原檔": [loc.get("uri", "") for loc in entry.get("locations") or []],
        "指紋": current_fp(entry),
    }
    if intake_text(entry) is not None:  # 印的順序照 KEYS：出處的下一行
        front["收件"] = intake_text(entry)
    if fp.get("text_sha256"):
        front["內容指紋"] = fp["text_sha256"]
    title = safe_title(entry.get("title"), config.get(vault, "card_title_max_chars")) if entry.get("kind") == "link" else None
    front.update({"標題": title or FILL, "卡型": FILL, "實體": prefill_entities(entry, ent_data, vault), "提到": [], "待補型別": [],
                  "文件日期": DATE_FILL, "讀取": READ_FILL,
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


def _scan_groups(vault, deadline=None):
    """回 (groups, broken)：groups {編號: [{"path", "front"}, …]}（照路徑排）；解析不了、沒有編號的進 broken。
    deadline（time.monotonic 的時刻）給開場 hook：讀每張卡之前看一次，過了丟 TimeoutError（answer 裁定 17）。"""
    import time
    groups, broken = {}, []
    for rel in _walk_cards(vault):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("讀卡讀太久")
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


def _is_v1(front):
    return front.get("範本版本") == OLD_TEMPLATE_VERSION


def problems(vault, rel, front, entries_by_id, sources=None, ent_data=False, prof=None):
    """檔頭解析得了之後的全部問題 [(代號, 原因)]，照 validate 的順序。代號給 todo 分桶。
    範本第一版（沒有 實體、待補型別 兩欄）只錯在版本 → 代號 v1（todo 收進 unlinked，D-116）；
    第二版多驗 實體 每個名稱要是登記的名稱（entity）、待補型別 不能是型別表已有的（missing_type，todo 收進 unlinked）。
    ent_data 是 entities.load 的結果、prof 是設定檔（都沒給自己讀）。"""
    from kb._lib import sources as sources_lib
    for key in KEYS:
        if key not in OPTIONAL and key not in front and not (key in V2_KEYS and _is_v1(front)):
            return [("missing_key", f"檔頭少了「{key}」")]
    for key in KEYS + PROGRAM_KEYS:  # 型別先驗（裁定 21）：不對的值不拿去查清冊、比字串
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
    if front["指紋"] != current_fp(entry) and not (text_fp and text_fp == fp.get("text_sha256")):
        out.append(("fingerprint", "指紋不是現行的，重跑 kb cards todo 拿新檔頭"))
    want = ORIGIN_TEXT.get(entry.get("origin"), entry.get("origin"))
    if front["出處"] != want:
        out.append(("origin", f"出處該是 {want}"))
    want_intake = intake_text(entry)
    if want_intake is not None and front.get("收件") != want_intake:
        out.append(("intake", INTAKE_WRONG))
    if front["開原檔"] != entry_link(vault, sources, entry) and not old_link_same_place(vault, sources, entry, front["開原檔"]):
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
    if _is_v1(front):
        out.append(("v1", f"範本版本該是 {TEMPLATE_VERSION}"))
    elif front["範本版本"] not in OK_VERSIONS:
        out.append(("version", f"範本版本該是 {TEMPLATE_VERSION}"))
    else:
        out.extend(_entity_problems(vault, front, ent_data, prof))
    return out


def _entity_problems(vault, front, ent_data, prof):
    """第二版的 實體 與 待補型別：名稱要登記過（merged 的不算登記的名稱），寫別名要改成登記的名稱；待補型別不能是型別表已有的。"""
    from kb._lib import entities
    if ent_data is False:
        ent_data = entities.load(vault)
    out = []
    for name in list(front["實體"]) + list(front.get("提到") or []):
        found, _hit = entities.find(ent_data, name)
        if found is None:
            out.append(("entity", f"實體「{name}」還沒登記，先跑 kb entity add"))
            break
        if entities.key(found["name"]) != entities.key(name):
            out.append(("entity", f"實體「{name}」該寫登記的名稱「{found['name']}」"))
            break
    if prof is None:
        prof, _missing = _profile_or_default(vault)
    have = {entities.key(t["name"]) for t in prof.get("types") or []}
    known = [t for t in front["待補型別"] if entities.key(t) in have]
    if known:
        out.append(("missing_type", f"型別「{known[0]}」已在型別表，先 kb entity add 再掛"))
    return out


def safe_problems(vault, rel, front, entries_by_id, sources=None, ent_data=False, prof=None):
    """problems 整段包住：任何例外都當成解析不了（裁定 21，不靜默放行）。"""
    try:
        return problems(vault, rel, front, entries_by_id, sources, ent_data, prof)
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
    """裁定 36：pypdfium2 裝好了（本行程或 kb office setup 的工具夾，build 第 3 點），就把 reason 是「還沒裝 PDF 元件」的
    open／dismissed 待處理 done（備進回合），回關掉幾份。todo 只有這一處會寫檔；沒有這種條目就什麼都不碰。"""
    from kb._lib import extract, pending, turn
    if not extract.pdf_available():
        return 0
    try:
        items = pending.load(vault)["items"]
    except ValueError:
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


def not_for_card(vault, sources, entry):
    """這份不是要製卡的檔嗎：跟 kb convert 同一個判斷（guard.classify 落在 convert.NOT_CARD_ZONES，例如型別夾根直接放的檔）。
    只看總資料夾自己的條目（知識庫、收件匣、AI 產出）；登記來源的檔不會落在這幾區。card-maker 第一輪修 S2(c)：todo 列了、
    convert 卻拒絕，製卡助手一直重做同一份。"""
    from kb import convert, guard
    from kb._lib import inventory
    from kb._lib.paths import normalize
    uri = (entry.get("locations") or [{}])[0].get("uri") or ""
    if inventory.split_uri(uri)[0] not in (inventory.KNOWLEDGE, inventory.HOLD):
        return False
    path = inventory.path_of(vault, sources, uri)
    if path is None:
        return False
    zone, _alias = guard.classify(normalize(str(path), None), normalize(str(vault), None), sources)
    return zone in convert.NOT_CARD_ZONES


def _profile_or_default(vault):
    """回 (設定檔, 讀不到嗎)：沒檔或壞了用 profile.defaults，製卡者拿到的 profile 塊永遠有東西（客戶設定檔與型別第 5 點）。"""
    try:
        data = profile.load(vault)
    except ValueError:
        data = None
    return (data, False) if data is not None else (profile.defaults(), True)


def is_open(vault, sources, entry):
    """同夾有鎖檔（Word、Excel 開著時的 ~$<完整檔名> 或 ~$<檔名去掉前兩個字>）嗎；只給 change_info 當依據（D-331）。
    D-525 拿掉 D-329：開著、剛改過都不再延後整理。link 條目、讀不到檔回 False。"""
    from kb._lib import inventory
    if entry.get("kind") == "link" or not entry.get("locations"):
        return False
    path = inventory.path_of(vault, sources, entry["locations"][0]["uri"])
    if path is None:
        return False
    path = Path(path)
    try:
        return any((path.parent / ("~$" + name)).exists() for name in (path.name, path.name[2:]))
    except OSError:
        return False


# ---------- 會一直改的檔（放寬丙 11，D-331）：程式只記與給依據，判斷交 AI ----------

TEXTCACHE_DIR = ".system/local/textcache"  # 本機：每台自己存上次看過的文字（多人時不進 git）
POINTER = "pointer"
FULL = "full"
POINTER_TEXT = "指路"  # 檔案卡檔頭「卡式」的值


def _parse_time(text):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def entity_done(ent_data, front):
    """卡主要掛的實體都結案或封存了嗎（沒掛實體算 False）。"""
    from kb._lib import entities
    names = front.get("實體")
    names = [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
    if not names or ent_data is None:
        return False
    for name in names:
        e, _hit = entities.find(ent_data, name)
        if e is None or not (e.get("archived") or e.get("progress") == "closed"):
            return False
    return True


def change_info(vault, sources, entry, card, ent_data):
    """這份檔改得多勤，給 AI 判斷完整卡或指路卡（D-331）：changes_7d、changes_30d（清冊 fingerprints 的 time，第一筆是登記不算改）、
    days_since（最近一次改到現在幾天）、open（有鎖檔）、entity_done（掛的實體結案或封存）、mode、track。"""
    from datetime import datetime
    now = datetime.now()
    times = [t for t in (_parse_time(fp.get("time")) for fp in (entry.get("fingerprints") or [])[1:]) if t is not None]
    days = [(now - t).total_seconds() / 86400 for t in times]
    return {"changes_7d": sum(1 for d in days if d <= 7), "changes_30d": sum(1 for d in days if d <= 30),
            "days_since": int(min(days)) if days else None, "open": is_open(vault, sources, entry),
            "entity_done": entity_done(ent_data, card["front"]), "mode": entry.get("card_mode") or FULL,
            "track": entry.get("track") is not False}


def current_text(vault, sources, entry):
    """現在抽出的文字（跟 kb convert 同一套抽字，不寫暫存檔、不做守門）；線上文件、圖片、讀不到回 None。"""
    from kb import convert
    from kb._lib import extract, inventory
    if entry.get("kind") == "link" or not entry.get("locations"):
        return None
    path = inventory.path_of(vault, sources, entry["locations"][0]["uri"])
    try:
        if path is None or not Path(path).is_file():
            return None
        kind = extract.probe(path)
        if kind in ("docx", "xlsx", "pptx", "text"):
            return convert._extract(vault, kind, path)[0]
        if kind == "pdf":
            return extract.pdf_pages_text(path, config.get(vault, "pdf_page_min_chars"))[0]
    except (extract.Unreadable, OSError, ValueError):
        return None
    return None


def _textcache(vault, fid):
    return Path(vault) / TEXTCACHE_DIR / f"{fid}.txt"


def read_textcache(vault, fid):
    try:
        return _textcache(vault, fid).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def write_textcache(vault, fid, text):
    p = _textcache(vault, fid)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, text)


def drop_textcache(vault, fid):
    try:
        _textcache(vault, fid).unlink()
    except OSError:
        pass


def pointer_diff(vault, sources, entry):
    """指路卡的檔改了：textcache 跟現在抽出文字的差（difflib 統一格式，最多 pointer_diff_max_chars，超過截斷並標 diff_truncated）。
    track 是 false 沒有 diff；沒有 textcache 或現在讀不到文字回空的 diff 與 diff_missing。"""
    import difflib
    if entry.get("track") is False:
        return {}
    old, new = read_textcache(vault, entry["id"]), current_text(vault, sources, entry)
    if old is None or new is None:
        return {"diff": "", "diff_missing": True}
    text = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "上次看過", "現在", lineterm="", n=1))
    cap = config.get(vault, "pointer_diff_max_chars")
    return {"diff": text[:cap], "diff_truncated": True} if len(text) > cap else {"diff": text}


def set_front_value(text, key, value):
    """只換檔頭一個單行欄位（value 是 None 就拿掉那一行；沒有這欄就接在檔頭最後），其他行原樣。"""
    lines = text.splitlines(keepends=True)
    nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    out, in_front, done = [], False, False
    for i, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("\ufeff") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
            if not done and value is not None:
                out.append(f"{key}: {value}{nl}")
            done = True
        elif in_front and bare.startswith(f"{key}:"):
            if value is not None and not done:
                out.append(f"{key}: {value}{nl}")
            done = True
            continue
        out.append(line)
    return "".join(out)


def sync_fronts(vault, turn_id, ids=None, undo=None):
    """檔案卡檔頭的「卡式」「封存」照清冊補或拿掉（kb cards sync、kb cards mode、kb archive 用）；ids 給了只動那幾張。
    卡重寫過，redo 記號跟著換。回 changed（給日誌的 [{"path","action"}]）。"""
    from kb._lib import inventory, turn
    by_id = {e["id"]: e for e in inventory.load(vault)["entries"] if e.get("id")}
    changed, shas = [], {}
    for cid, group in _scan_groups(vault)[0].items():
        entry = by_id.get(cid)
        if entry is None or entry.get("status") != "active" or (ids is not None and cid not in ids):
            continue
        want = {"卡式": POINTER_TEXT if entry.get("card_mode") == POINTER else None,
                "封存": (entry.get("archived") or {}).get("date") or None}
        card = group[0]
        text = _read(vault, card["path"])
        new = text
        for key_, value in want.items():
            if card["front"].get(key_) != value:
                new = set_front_value(new, key_, value)
        if new == text:
            continue
        before = inventory.card_sha(vault, card["path"])
        if undo is not None:
            undo.file(card["path"])
        turn.backup(vault, card["path"], turn_id, by="program")
        write_text_atomic(Path(vault) / card["path"], new)
        shas[cid] = (before, inventory.card_sha(vault, card["path"]))
        changed.append({"path": card["path"], "action": "modify"})
    if inventory.follow_redo(vault, shas, turn_id, undo):
        changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    return changed


def mode(vault, sources, fid, card_mode, track, turn_id):
    """kb cards mode：清冊記 card_mode 與 track（記進回合，退得回）；指路卡且記過程時，現在抽出的文字存 textcache，
    換回完整卡或不記過程就刪。卡檔頭的「卡式」同時照清冊補或拿掉。回 {"id","mode","track","textcache","changed"}。"""
    from kb._lib import inventory
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid), None)
    if entry is None:
        raise CardRefused(f"清冊沒有 {fid}")
    if entry.get("status") != "active":
        raise CardRefused("這份的原檔已不在")
    wrote = inventory.set_card_mode(vault, fid, card_mode, track, turn_id)
    entry = next(e for e in inventory.load(vault)["entries"] if e.get("id") == fid)
    tracked = entry.get("card_mode") == POINTER and entry.get("track") is not False
    cached = False
    if tracked:
        text = current_text(vault, sources, entry)
        if text is not None:
            write_textcache(vault, fid, text)
            cached = True
    else:
        drop_textcache(vault, fid)
    changed = ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if wrote else []) + sync_fronts(vault, turn_id, {fid})
    return {"id": fid, "mode": entry.get("card_mode") or FULL, "track": entry.get("track") is not False, "textcache": cached,
            "changed": changed}


def seen(vault, sources, fid, turn_id):
    """kb cards seen：AI 看完指路卡的檔改了什麼：卡檔頭的指紋換成現在的（不再列進 pointer_changed）、textcache 換成現在的文字。
    不是指路卡丟 CardRefused。回 {"id","title","changed"}。"""
    from kb._lib import inventory, turn
    card = card_of(vault, fid)
    entry = next(e for e in inventory.load(vault)["entries"] if e.get("id") == fid)
    if entry.get("card_mode") != POINTER:
        raise CardRefused("這份不是指路卡，沒有要看的差別")
    fp = (entry.get("fingerprints") or [{}])[-1]
    text = _read(vault, card["path"])
    new = set_front_value(text, "指紋", current_fp(entry))
    new = set_front_value(new, "內容指紋", fp.get("text_sha256") or None)
    changed = []
    if new != text:
        before = inventory.card_sha(vault, card["path"])
        turn.backup(vault, card["path"], turn_id, by="program")
        write_text_atomic(Path(vault) / card["path"], new)
        changed.append({"path": card["path"], "action": "modify"})
        if inventory.follow_redo(vault, {fid: (before, inventory.card_sha(vault, card["path"]))}, turn_id):
            changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    if entry.get("track") is not False:
        now = current_text(vault, sources, entry)
        if now is not None:
            write_textcache(vault, fid, now)
    return {"id": fid, "title": _title(card), "changed": changed}


def _sort_files(vault, sources, ent_data, entries, restored=0, light=False, deadline=None):
    """todo 的檔案分桶（new、stale、moved、duplicates、broken、orphans、unreadable、missing、skipped、unlinked、missing_types）：
    回 (out, groups, 設定檔)。light 給 unsorted：new、stale 只放 {"id"}、不印 header（份數照算，跟 todo 第一行同一個數）；
    deadline（time.monotonic 的時刻）過了丟 TimeoutError（開場 hook 有時限）。不寫檔。
    改過內容的照常進 stale，開著、剛改過都不延後（D-525 拿掉 D-329）。
    stale 每項多 change（改得多勤，放寬丙 11）；指路卡（清冊 card_mode pointer）的檔改了不進 stale，改放 pointer_changed: [{id, title, change, diff}]；
    清冊封存（archived）的檔不進 new、stale，也不算份數（放寬丙 12）。
    produce 第 5 點：AI 產出的（草稿）.md 不列；link 條目的 new 項 kind 是 link、path 是 None、多 url；
    unbacked 是 new 裡掃描補登記的 AI 產出份數（給 tell）。"""
    import time
    from kb._lib import entities, extract, inventory, pending

    def check_time():
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("還沒整理的份數算太久")
    check_time()
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
    groups, broken = _scan_groups(vault, deadline)
    out = {"new": [], "stale": [], "moved": [], "duplicates": [], "orphans": [], "unreadable": [], "missing": [],
           "skipped": dict({k: 0 for k in SKIPPED_READS}, skipped=0), "broken": list(broken), "pdf_restored": restored,
           "unlinked": [], "missing_types": {}, "unbacked": 0, "pointer_changed": []}
    data, out["profile_missing"] = _profile_or_default(vault)
    out["profile"] = profile.card_types_for_todo(data)
    have_types = {entities.key(t["name"]) for t in data.get("types") or []}
    today = date.today().isoformat()

    def work_item(entry, card):
        if light:
            return {"id": entry["id"]}
        link = entry.get("kind") == "link"
        path = None if link else inventory.path_of(vault, sources, entry["locations"][0]["uri"])
        extra = {"url": entry["locations"][0]["uri"]} if link else {}  # link 條目沒有檔可轉，內容照網址讀
        return dict({"id": entry["id"], "uri": entry["locations"][0]["uri"], "path": str(path) if path else None,
                     "origin": entry.get("origin"),
                     "kind": "link" if link else extract.kind_by_name(entry["locations"][0]["uri"]),
                     "dir": card_dir(entry), "filename_pattern": f"<標題>-{short_id(entry['id'])}.md",
                     "existing_card": card["path"] if card else None,
                     "created": _created_date(entry),
                     "header": header_for(vault, sources, entry, today, ent_data)}, **extra)

    def broken_item(entry, card, reason):
        """編號對得到 active 條目的壞卡：多帶 id 與 header，製卡者照它整張寫回 path（審閱後補）。"""
        if light:
            return {"path": card["path"], "reason": reason, "id": entry["id"]}
        return {"path": card["path"], "reason": reason, "id": entry["id"],
                "header": header_for(vault, sources, entry, today, ent_data)}

    def unlinked_item(entry, card, reason):
        types = [t for t in card["front"].get("待補型別") or [] if entities.key(t) in have_types] if reason != "v1" else []
        return {"id": entry["id"], "path": card["path"], "reason": reason, "types": types,
                "prefill": prefill_entities(entry, ent_data, vault)}

    try:  # 密碼與守門：有「可以讀」裁示的那份照一般檔整理（D-183），下次掃描前清冊可能還記著 credential
        from kb._lib import rulings
        readable = rulings.readable_ids(vault)
    except ValueError:
        readable = set()
    carded = set()
    for cid, group in groups.items():
        check_time()
        entry = by_id.get(cid)
        if entry is None or entry.get("status") != "active":  # B-059：帶原因，排除夾的卡不會被說成原檔不見
            reason = ORPHAN_REASONS.get(None if entry is None else entry.get("status"), (entry or {}).get("status"))
            out["orphans"].extend({"path": c["path"], "reason": reason} for c in group)
            continue
        carded.add(cid)
        codes_of = {c["path"]: safe_problems(vault, c["path"], c["front"], by_id, sources, ent_data, data) for c in group}
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
        if keep["front"].get("範本版本") in OK_VERSIONS and isinstance(keep["front"].get("待補型別"), list):
            for t in dict.fromkeys(keep["front"]["待補型別"]):
                if isinstance(t, str) and t and entities.key(t) not in have_types:
                    out["missing_types"][t] = out["missing_types"].get(t, 0) + 1
        redo = bool(entry.get("redo")) and inventory.card_sha(vault, keep["path"]) == entry["redo"]
        if names.keys() & {"missing_key", "format", "crash"}:
            out["broken"].append(broken_item(entry, keep, codes[0][1]))
        elif "dir" in names or keep["front"].get("原檔") != _uris(entry):
            out["moved"].append({"id": cid, "from": keep["path"], "to": card_dir(entry)})
        elif entry.get("archived") and ("fingerprint" in names or redo):
            continue  # 封存的：不重做、不算份數（放寬丙 12，D-332）
        elif "fingerprint" in names and not redo and entry.get("card_mode") == "pointer":  # 指路卡：不重做，交 AI 看差別（放寬丙 11）
            out["pointer_changed"].append({"id": cid} if light else dict(
                {"id": cid, "title": _title(keep), "change": change_info(vault, sources, entry, keep, ent_data)},
                **pointer_diff(vault, sources, entry)))
        elif "fingerprint" in names or redo:  # 客戶說卡讀錯了（kb cards redo）：重寫前一直列著
            item = work_item(entry, keep)
            if not light:
                item["change"] = change_info(vault, sources, entry, keep, ent_data)
            out["stale"].append(dict(item, **({"reason": REDO_REASON} if redo else {})))
        elif {c[0] for c in others} == {"v1"}:  # 第一版、其他都過：只補掛載（D-116）
            out["unlinked"].append(unlinked_item(entry, keep, "v1"))
        elif {c[0] for c in others} == {"missing_type"}:  # 待補型別後來進了型別表（D-124）
            out["unlinked"].append(unlinked_item(entry, keep, "types_added"))
        elif others:  # broken 只收開原檔以外的錯（裁定 19）
            out["broken"].append(broken_item(entry, keep, others[0][1]))
        elif "link" in names:  # 只有開原檔對不上（來源搬家、連結形式改了）：sync 重寫（裁定 19）
            out["moved"].append({"id": cid, "from": keep["path"], "to": card_dir(entry)})
    check_time()
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
        ok = entry.get("read") == "ok" or (entry.get("read") == "credential" and eid in readable)
        if entry.get("read") in SKIPPED_READS and not ok:
            out["skipped"][entry["read"]] += 1
            continue
        if entry.get("skip_kind"):  # 客戶設定檔說不整理的種類（D-113）：只數份數
            out["skipped"]["skipped"] += 1
            continue
        if eid in unreadable_open:
            item = unreadable_open[eid]
            out["unreadable"].append({"id": eid, "uri": entry["locations"][0]["uri"], "reason": item.get("reason"),
                                      "pending_id": item["id"]})
            continue
        if is_draft(entry["locations"][0].get("uri", "")):
            continue  # 草稿還在改，定稿才製卡（produce 第 5 點）
        if (ok and eid not in carded and eid not in unreadable_skip and not entry.get("archived")
                and not not_for_card(vault, sources, entry)):
            out["new"].append(work_item(entry, None))
            if entry.get("unbacked"):
                out["unbacked"] += 1
    return out, groups, data


def todo(vault, sources, tell_all=True):
    """讀清冊 active 條目、卡與待處理，分 new、stale、moved、duplicates、broken、orphans、unreadable、missing、skipped（D-104）；
    skipped 多一鍵 skipped（設定檔說不整理、清冊記了 skip_kind 的份數，D-113），另帶 profile 塊（卡型、段落、個資種類、型別、
    每類段落、關於你）。實體與綜合卡第 4 點：多 unlinked（第一版的卡、待補型別後來進了型別表的卡）、missing_types、
    synthesis_new／stale／broken、entity_drift（後四桶由 synthesis.buckets 給）與 entities（active、removed 的名稱），
    日程第 5 點：多 agenda_recheck（出處卡重做後待重對的日程條目）與 agenda_counts（逾期、今天、接下來）；
    entities 塊的事類條目多 progress 與 asked_evidence；agenda_unscanned（D-140）列還沒抽過日期的卡。
    最後併進 plan、asks、tell 三鍵（plan.build，整理 ingest；tell_all 是 False 時 tell 只帶這一回合做了什麼，D-141，kb asks 用）。
    不寫檔、不開原檔（kind 只看副檔名，裁定 28）。"""
    from kb._lib import plan
    out = collect(vault, sources, _restore_pdf(vault))
    out.update(plan.build(out, vault, config, tell_all=tell_all))
    return out


def collect(vault, sources, restored=0):
    """todo 的材料（plan、asks、tell 之外的全部鍵），不寫檔：kb cards dispatch／batch 用同一套項目組法（card-maker，D-197）。"""
    from kb._lib import agenda, entities, inventory, ledger, plan, synthesis
    ent_data = entities.load(vault)
    entries = inventory.load(vault)["entries"]
    out, groups, data = _sort_files(vault, sources, ent_data, entries, restored)
    out.update(synthesis.buckets(vault, ent_data, entities.mounts(vault, ent_data), data))
    out["purge_pending"] = _purge_pending(vault, sources)
    matters = {entities.key(t["name"]) for t in data.get("types") or [] if t.get("category") == "matter"}
    # 狀態只在事類顯示：事類併進別的大類後 progress 留在實體清單、不再顯示（D-143）
    out["entities"] = [dict({"name": e["name"], "type": e.get("type"), "aliases": list(e.get("aliases") or []),
                             "self": e.get("self") or False},
                            **({"progress": e["progress"], "asked_evidence": entities.asked_evidence(vault, e)}
                               if e.get("progress") and entities.key(e.get("type")) in matters else {}))
                       for e in ent_data["entities"] if e.get("status") in ("active", "removed")]
    try:  # 日程壞了不能擋整理的主流程：兩塊給空的，開場那行與 kb agenda 自己會報
        ag = agenda.load(vault)
        out.update(agenda.buckets(vault, ag))
        out.update(agenda.unscanned(vault, ag, groups, entries, skip={b["id"] for b in out["broken"] if b.get("id")}))
        out["agenda_counts"] = agenda.counts(vault, ag)
    except ValueError as e:
        ledger.log_error(vault, f"日程讀不到，agenda_recheck 與 agenda_unscanned 先給空的：{e}", source="cards")
        out["agenda_recheck"], out["agenda_unscanned"] = [], []
        out["agenda_counts"] = {"overdue": 0, "today": 0, "upcoming": 0}
    out[plan.UNLOCK_STEP], out["unlock_kinds"] = plan.locked_same_kind(vault, out)  # 密碼與守門 D-181 第 2 點
    return out


def _purge_pending(vault, sources):
    """還沒清的「不想讓 AI 看到」排除（manage 第二版，D-174）：每條帶要重寫的綜合卡；沒有就是空清單。"""
    from kb._lib import ledger, purge
    from kb._lib import sources as sources_lib
    out = []
    for p in sources_lib.pending_purges(sources):
        try:
            out.append(purge.pending_detail(vault, p["alias"], p["spec"]))
        except Exception as e:  # noqa: BLE001  算不出來不擋整理；照樣列 purge 那步，purge 自己會擋
            ledger.log_error(vault, f"purge_pending 算不出來：{type(e).__name__}: {e}", source="cards")
            out.append({"name": f"{p['alias']}/{p['spec']}", "alias": p["alias"], "spec": p["spec"], "synthesis": []})
    return out


# ---------- 查詢（answer 第 1、2 點） ----------

def light_sort(vault, sources=None, deadline=None):
    """unsorted、wip 共用的分桶（light，不寫檔、不跑 _restore_pdf）：回 {"out","sources","entries","entities"}。"""
    from kb._lib import entities, inventory
    from kb._lib import sources as sources_lib
    sources = sources if sources is not None else sources_lib.load(vault)
    ent, entries = entities.load(vault), inventory.load(vault)["entries"]
    out, _groups, _data = _sort_files(vault, sources, ent, entries, light=True, deadline=deadline)
    return {"out": out, "sources": sources, "entries": entries, "entities": ent}


def unsorted(vault, sources=None, deadline=None, sorted_=None):
    """還沒整理的份數：todo 第一行「待製卡」的同一個數（new＋stale），同一套分桶、不寫檔（不跑 _restore_pdf）。只數檔：
    健檢標的要改寫綜合卡另外講（開場那行，lint 桌面版確認後）。deadline 給開場 hook：過了丟 TimeoutError。"""
    out = (sorted_ or light_sort(vault, sources, deadline))["out"]
    return len(out["new"]) + len(out["stale"])


def not_wip(entry):
    """這份改了也不算製作中（D-525 裁定 8，同 _sort_files 不進 new、stale 的那幾種）：指路卡、清冊封存、照設定不整理的。
    不看「讀取」：有卡的改了不管讀取都進 stale，沒卡的密碼檔有「可以讀」裁示也進 new（review 第 1 條）。
    每句開頭的變動行用它篩清冊裡已有的檔；新放的（清冊還沒有）照算。"""
    return entry.get("card_mode") == POINTER or bool(entry.get("archived")) or bool(entry.get("skip_kind"))


def _wip_entities(entry, name, ent_data, vault):
    """這份算在哪幾個實體下（D-525）：實體夾照 prefill_entities，加上檔名（不含副檔名）含 active、沒封存的實體的名稱或別名的
    （比法同 lookup.mentions：entities.key 包含；封存的不掛，裁定 9）；自己不算。"""
    from kb._lib import entities
    out = list(prefill_entities(entry, ent_data, vault))
    stem = entities.key(Path(name).stem)
    for e in (ent_data or {}).get("entities") or []:
        if e.get("status") != "active" or e.get("archived") or entities.is_self(e) or e["name"] in out:
            continue
        if any(k and k in stem for k in (entities.key(n) for n in [e["name"], *(e.get("aliases") or [])])):
            out.append(e["name"])
    return out


def wip(vault, sources=None, deadline=None, sorted_=None):
    """製作中（D-525）：todo 的 new＋stale，不列 md、線上文件、標了重做的（裁定 2）與 not_wip 的（裁定 13），不另存。
    回 [{"id","path","name","mtime","entities"}]，新改的排前面；path 是原檔絕對路徑，mtime 是本機時間 ISO 字串（讀不到 None）。"""
    from datetime import datetime
    from kb._lib import inventory
    s = sorted_ or light_sort(vault, sources, deadline)
    by_id = {e.get("id"): e for e in s["entries"]}
    rows = []
    for item in s["out"]["new"] + [i for i in s["out"]["stale"] if i.get("reason") != REDO_REASON]:
        entry = by_id.get(item["id"])
        if entry is None or entry.get("kind") == "link" or not entry.get("locations") or not_wip(entry):  # 跟每句開頭同一套（裁定 13）
            continue
        uri = entry["locations"][0]["uri"]
        name = uri.rsplit("/", 1)[-1]
        if name.lower().endswith(".md"):
            continue
        path = inventory.path_of(vault, s["sources"], uri)
        try:
            mtime = datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat(timespec="seconds")
        except (OSError, TypeError):
            mtime = None
        rows.append({"id": entry["id"], "path": str(path) if path else None, "name": name, "mtime": mtime,
                     "entities": _wip_entities(entry, name, s["entities"], vault)})
    rows.sort(key=lambda r: r["mtime"] or "", reverse=True)
    return rows


def wip_label(row):
    """一份製作中的檔給人讀：「<檔名>（M/D 改）」，沒有修改時間只給檔名。"""
    m = row.get("mtime") or ""
    return f"{row['name']}（{int(m[5:7])}/{int(m[8:10])} 改）" if len(m) >= 10 else row["name"]


def card_state(vault, entry, card):
    """這張卡該重做嗎：客戶說讀錯了（redo 記號還有效）回 "redo"，原檔改過（卡上的指紋不是清冊現行的，內容指紋對得上的不算）
    回 "changed"，都不是回 None。"""
    from kb._lib import inventory
    if entry.get("redo") and inventory.card_sha(vault, card["path"]) == entry["redo"]:
        return "redo"
    front = card["front"]
    fp = (entry.get("fingerprints") or [{}])[-1]
    text_fp = front.get("內容指紋")
    if front.get("指紋") != current_fp(entry) and not (text_fp and text_fp == fp.get("text_sha256")):
        return "changed"
    return None


def ruled_out_ids(rul_data):
    """有效的「不採用」裁示掛著的卡編號（D-149）：answer 不引用、不算進統計。"""
    from kb._lib import rulings
    return {cid for r in rul_data["rulings"] if rulings.is_active(r) and r.get("kind") == "ruled_out"
            for cid in rulings.card_ids(r)}


def _text_or(front, key, default=""):
    value = front.get(key)
    return value if isinstance(value, str) else default


def card_brief(vault, entry, card, ruled_out):
    """lookup 與 cards list 的一張卡：編號、標題、卡型、文件日期、實體、讀取、卡的路徑、stale（原檔改過或標了重做）、
    redo（是標了重做的那種）、ruled_out、archived（清冊封存的，放寬丙 12）。"""
    front = card["front"]
    state = card_state(vault, entry, card)
    names = front.get("實體")
    return {"id": entry["id"], "title": _title(card), "card_type": _text_or(front, "卡型"),
            "date": _text_or(front, "文件日期", "未載"), "entities": list(names) if isinstance(names, list) else [],
            "read": _text_or(front, "讀取"), "path": card["path"], "stale": state is not None, "redo": state == "redo",
            "ruled_out": entry["id"] in ruled_out, "archived": bool(entry.get("archived"))}


def newest_first(items):
    """文件日期新的在前、未載的排最後，同日照標題；封存的（archived）排在沒封存的後面。"""
    ordered = sorted(items, key=lambda c: (c["title"], c["id"]))
    dated = sorted(ordered, key=lambda c: (1, c["date"]) if _DATE.match(c["date"]) else (0, ""), reverse=True)
    return sorted(dated, key=lambda c: bool(c.get("archived")))


def active_cards(vault):
    """清冊 active 的條目有檔案卡的（同編號多張取第一張）：[(條目, 卡)]，照編號排。"""
    from kb._lib import inventory
    by_id = {e.get("id"): e for e in inventory.load(vault)["entries"] if e.get("status") == "active"}
    groups, _broken = _scan_groups(vault)
    return [(by_id[cid], group[0]) for cid, group in sorted(groups.items()) if cid in by_id]


def listing(vault, types=(), year=None, names=(), source=None):
    """kb cards list 的本體（D-153、D-157）：窮舉不挑。篩法可疊加：卡型照字面（任一相符）、文件日期的年、掛的實體（任一，
    別名與合併過的照登記的名稱認）、原檔來源別名。回 {"cards", "types", "undated", "verify_max"}：verify_max 是要逐份核對的
    份數上限（answer_verify_max，客戶端可覆寫，answer 裁定 6）；types 是卡型以外的篩法篩完後
    實際出現的卡型與份數（in_profile 是設定檔第二節有沒有這個卡型）；undated 是文件日期未載的份數（給了年就不列進 cards）。
    實體沒登記丟 CardRefused。只讀。"""
    from kb._lib import entities, rulings
    ent = entities.load(vault)
    want_ids = None
    if names:
        mounts = entities.mounts(vault, ent)
        want_ids = set()
        for name in names:
            e, _hit = entities.find(ent, name)
            if e is None or e.get("status") != "active":
                raise CardRefused(f"實體清單沒有「{name}」，先 kb entity add")
            want_ids |= {m["id"] for m in mounts.get(e["name"], [])}
    ruled_out = ruled_out_ids(rulings.load(vault))
    pool = []
    for entry, card in active_cards(vault):
        if want_ids is not None and entry["id"] not in want_ids:
            continue
        if source and all(_split(u)[0].casefold() != source.casefold() for u in _uris(entry)):
            continue
        brief = card_brief(vault, entry, card, ruled_out)
        dated = bool(_DATE.match(brief["date"]))
        if year and dated and brief["date"][:4] != str(year):
            continue
        pool.append((brief, dated))
    prof, _missing = _profile_or_default(vault)
    known = {entities.key(c["name"]) for c in prof.get("card_types") or []}
    counts = {}
    for brief, _dated in pool:
        counts[brief["card_type"]] = counts.get(brief["card_type"], 0) + 1
    type_rows = [{"card_type": t, "count": n, "in_profile": entities.key(t) in known}
                 for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if types:
        pool = [(b, d) for b, d in pool if b["card_type"] in types]
    undated = sum(1 for _b, dated in pool if not dated)
    return {"cards": newest_first([b for b, dated in pool if dated or not year]), "types": type_rows, "undated": undated,
            "verify_max": config.get(vault, "answer_verify_max")}


# ---------- 重做與改掛（note 改知識第 5 點） ----------

class CardRefused(ValueError):
    """kb cards redo／mount 的拒絕：訊息就是給人看的那句。"""


def card_of(vault, fid):
    """回 {"path","front"}：要在清冊、原檔 active、檔案卡寫好了（同 agenda._card），不行丟 CardRefused。"""
    from kb._lib import inventory
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid), None)
    if entry is None:
        raise CardRefused(f"清冊沒有 {fid}")
    if entry.get("status") != "active":
        raise CardRefused("這張卡的原檔已不在")
    group = _scan_groups(vault)[0].get(fid)
    if not group:
        raise CardRefused("這份還沒有檔案卡")
    return group[0]


def _title(card):
    t = card["front"].get("標題")
    return t if _filled(t) else card["path"].rpartition("/")[2][:-3]


def mark_redo(vault, fid, turn_id):
    """kb cards redo：清冊條目記 redo（卡檔現在的 sha256 前 12 碼）；掛著這張卡的綜合卡「整理到」裡那一行指紋欄改成 redo
    （replace_front、備份 by program，實作回報裁定 1）：卡重寫後綜合卡才會列待更新。
    回 {"id","title","path","redo","changed","synthesis"（改到的綜合卡名稱）,"synthesis_paths"}。"""
    from kb._lib import entities, inventory, synthesis, turn
    card = card_of(vault, fid)
    sha = inventory.card_sha(vault, card["path"])
    changed = inventory.set_redo(vault, fid, sha, turn_id)
    names, paths = [], []
    with entities.lock(vault):
        data = entities.load(vault)
        for name in entity_names_of([card]):
            e, _hit = entities.find(data, name)
            if e is None or e.get("status") != "active" or entities.is_self(e) or not e.get("type"):
                continue
            loc = entities.locate(vault, e)
            if not loc or e["name"] in names:
                continue
            text = _read(vault, loc)
            try:
                lines = parse_front(text).get("整理到")
            except ValueError:
                continue
            if not isinstance(lines, list):
                continue
            new = [f"{fid} {synthesis.REDO}" if str(x).split(" ")[0] == fid else x for x in lines]
            if new == lines:
                continue
            turn.backup(vault, loc, turn_id, by="program")
            write_text_atomic(Path(vault) / loc, synthesis.replace_front(text, {"整理到": new}))
            names.append(e["name"])
            paths.append(loc)
    _wake(vault, [fid], turn_id)
    return {"id": fid, "title": _title(card), "path": card["path"], "redo": sha, "changed": changed,
            "synthesis": names, "synthesis_paths": paths}


def _wake(vault, fids, turn_id):
    """卡標了重做：跟它有關的先放著回到前面（D-226）；出錯只記 log。"""
    from kb._lib import ledger, pending
    try:
        pending.wake(vault, file_ids=fids, turn_id=turn_id)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"先放著沒拿回來：{type(e).__name__}: {e}", source="cards")


def mark_redo_many(vault, fids, turn_id, check=False):
    """kb cards redo --job（B 段整批重做）：這幾張（清冊 active、卡寫好了的；其餘跳過）照 mark_redo 標要重做，讀一次卡、清冊寫一次，
    每張綜合卡只改一次。check 只回要標的卡、不寫。回 {"cards":[{"id","title","source"}],"changed","synthesis","synthesis_paths"}。"""
    from kb._lib import entities, inventory, synthesis, turn
    groups = _scan_groups(vault)[0]
    by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    picked = [(fid, groups[fid][0]) for fid in dict.fromkeys(fids)
              if fid in groups and (by_id.get(fid) or {}).get("status") == "active"]
    shown = [{"id": fid, "title": _title(card), "source": str((by_id[fid].get("locations") or [{}])[0].get("uri", "")).rpartition("/")[2]}
             for fid, card in picked]
    if check or not picked:
        return {"cards": shown, "changed": False, "synthesis": [], "synthesis_paths": []}
    changed = bool(inventory.set_redo_many(vault, {fid: inventory.card_sha(vault, card["path"]) for fid, card in picked},
                                           turn_id))
    ids = {fid for fid, _c in picked}
    names, paths = [], []
    with entities.lock(vault):
        data = entities.load(vault)
        for name in entity_names_of([card for _f, card in picked]):
            e, _hit = entities.find(data, name)
            if e is None or e.get("status") != "active" or entities.is_self(e) or not e.get("type"):
                continue
            loc = entities.locate(vault, e)
            if not loc or e["name"] in names:
                continue
            text = _read(vault, loc)
            try:
                lines = parse_front(text).get("整理到")
            except ValueError:
                continue
            if not isinstance(lines, list):
                continue
            new = [f"{str(x).split(' ')[0]} {synthesis.REDO}" if str(x).split(" ")[0] in ids else x for x in lines]
            if new == lines:
                continue
            turn.backup(vault, loc, turn_id, by="program")
            write_text_atomic(Path(vault) / loc, synthesis.replace_front(text, {"整理到": new}))
            names.append(e["name"])
            paths.append(loc)
    _wake(vault, [c["id"] for c in shown], turn_id)
    return {"cards": shown, "changed": changed, "synthesis": names, "synthesis_paths": paths}


def mount(vault, fid, names, turn_id):
    """kb cards mount：這張卡掛的實體換成 names（登記且 active 的名稱，客戶自己可以；別名轉成登記的名稱、去重）。
    在實體清單的鎖裡改檔頭（rewrite_entities，備份 by program），內容沒變不寫。總目錄、綜合卡快取、日誌由呼叫端做。
    回 {"id","title","path","entities","was","changed"}。"""
    from kb._lib import entities, inventory, turn
    with entities.lock(vault):
        data = entities.load(vault)
        new = []
        for name in names:
            e, _hit = entities.find(data, name)
            if e is None or e.get("status") != "active":
                raise CardRefused(f"實體清單沒有「{name}」，先 kb entity add")
            if e["name"] not in new:
                new.append(e["name"])
        card = card_of(vault, fid)
        was = card["front"].get("實體") if isinstance(card["front"].get("實體"), list) else []
        text = _read(vault, card["path"])
        updated = rewrite_entities(text, new)
        if updated != text:
            before = inventory.card_sha(vault, card["path"])
            turn.backup(vault, card["path"], turn_id, by="program")
            write_text_atomic(Path(vault) / card["path"], updated)
            inventory.follow_redo(vault, {fid: (before, inventory.card_sha(vault, card["path"]))}, turn_id)
    return {"id": fid, "title": _title(card), "path": card["path"], "entities": new, "was": was,
            "changed": updated != text}


# ---------- 不整理 ----------

class SkipRefused(ValueError):
    """kb cards skip 的拒絕：訊息就是給人看的那句。"""

def prune_dirs(vault, rel):
    """刪卡後父夾空就移除，最多到 檔案索引/ 底下第一層（檔案索引本身不刪）。"""
    parts = rel.split("/")
    for depth in range(len(parts) - 1, 1, -1):
        d = Path(vault).joinpath(*parts[:depth])
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
            else:
                break
        except OSError:
            break


def remove_card(vault, rel, turn_id):
    """卡備成 delete 後刪、收空夾。"""
    from kb._lib import turn
    turn.backup(vault, rel, turn_id, action="delete")
    os.remove(Path(vault) / rel)
    from kb._lib import card_base
    card_base.forget(vault, rel)
    prune_dirs(vault, rel)


SKIP_AI = "AI 判斷"  # skip_kind 的特殊值：AI 看過、判斷不用卡（D-310(1)），不在設定檔第三節


def skip(vault, sources, fid, kind, turn_id):
    """kb cards skip（客戶設定檔與型別第 5 點，D-113）：條目要 active、read ok，kind 要是設定檔第三節 no_card 的種類，
    錯了丟 SkipRefused 帶給人看的句子。有卡（同編號多張全部）備成 delete 後刪；清冊備份後記 skip_kind；重寫總目錄。
    整段在清冊的鎖裡（測試後修正裁定 13，同 drop）：等不到鎖丟 TimeoutError 時什麼都還沒動。
    回 {"id", "card"（第一張刪掉的卡或 None）, "kind", "removed"（刪掉的全部卡）}。"""
    from kb._lib import inventory, turn
    with FileLock(Path(vault) / inventory.INVENTORY_POSIX):
        entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == fid), None)
        if entry is None:
            raise SkipRefused(f"清冊沒有 {fid}")
        if entry.get("status") != "active" or entry.get("read") != "ok":
            raise SkipRefused(f"這份不是待製卡的檔（{entry.get('status')}／{entry.get('read')}）")
        data, _missing = _profile_or_default(vault)
        kind = kind or SKIP_AI
        if kind != SKIP_AI and kind not in profile.no_card_kinds(data):
            raise SkipRefused(f"「{kind}」不在客戶設定檔的不整理種類裡，先在設定檔第三節加一列再套用")
        groups, _broken = _scan_groups(vault)
        removed = [c["path"] for c in groups.get(fid) or []]
        mounted = entity_names_of(groups.get(fid) or [])
        for rel in removed:
            remove_card(vault, rel, turn_id)
        turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
        inventory.set_skip(vault, [fid], kind, locked=True)
        render_index(vault, turn_id)
    refresh_synthesis(vault, mounted, turn_id)
    return {"id": fid, "card": removed[0] if removed else None, "kind": kind, "removed": removed,
            "uri": entry["locations"][0]["uri"]}


def entity_names_of(group):
    """這幾張卡檔頭 實體 與 提到 欄的名稱（去重）：這些實體的綜合卡「相關檔」要跟著重算。"""
    out = []
    for c in group:
        for key_ in ("實體", "提到"):
            names = c["front"].get(key_)
            for n in names if isinstance(names, list) else []:
                if n not in out:
                    out.append(n)
    return out


def refresh_synthesis(vault, names, turn_id):
    """卡刪了、搬了之後重渲染那些實體的綜合卡快取（實體與綜合卡第 4 點）；實體清單壞了只記 log，不擋卡的動作（I-046）。"""
    if not names:
        return []
    from kb._lib import ledger, synthesis
    try:
        return synthesis.render_cache(vault, names, turn_id)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"綜合卡快取沒更新：{type(e).__name__}: {e}", source="cards")
        return []


# ---------- 搬卡 ----------

def sync(vault, sources, turn_id, undo=None):
    """kb cards sync 的本體（note 改規則第 2 點開放給型別操作呼叫）：同編號多張留正本、其餘備進回合後刪；moved 的卡搬到原檔
    現在該在的夾（檔名不變；同夾只改檔頭），原檔清單與開原檔重算；舊路徑備成 delete、新路徑備成 create。
    undo（entities._Undo）給了就在每次寫、刪之前記舊內容，呼叫端出錯時還原。總目錄、綜合卡快取、日誌由呼叫端做。
    回 {"moved","updated","dropped","skipped","changed","touched"（動到的卡掛的實體名稱）}。"""
    from kb._lib import inventory, turn
    data = todo(vault, sources)
    by_id = {e["id"]: e for e in inventory.load(vault)["entries"]}
    moved, updated, skipped, changed, dropped = [], [], [], [], []
    shas = {}  # 卡編號 → (改寫前 sha, 改寫後 sha)：redo 記號跟著換（測試第一輪後裁定 6）
    groups, _broken = _scan_groups(vault)
    touched = entity_names_of([c for g in groups.values() for c in g
                               if c["path"] in {i["from"] for i in data["moved"]}
                               | {r for d in data["duplicates"] for r in d["drop"]}])

    def drop(rel):
        if undo is not None:
            undo.file(rel)
        remove_card(vault, rel, turn_id)
        changed.append({"path": rel, "action": "delete"})
        dropped.append(rel)

    def write(rel, text):
        if undo is not None:
            undo.file(rel)
        (Path(vault) / rel).parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(Path(vault) / rel, text)
    for dup in data["duplicates"]:  # 同編號多張：留正本，其餘備進回合後刪（測試後修正裁定 20）
        for rel in dup["drop"]:
            drop(rel)
    for item in data["moved"]:
        entry = by_id[item["id"]]
        old = item["from"]
        new = f"{item['to']}/{old.rpartition('/')[2]}"
        text = rewrite_locations(_read(vault, old), [loc["uri"] for loc in entry["locations"]],
                                 entry_link(vault, sources, entry))
        before = inventory.card_sha(vault, old)
        if new.casefold() == old.casefold():
            turn.backup(vault, old, turn_id)
            write(old, text)
            shas[item["id"]] = (before, inventory.card_sha(vault, old))
            updated.append(old)
            changed.append({"path": old, "action": "modify"})
            continue
        if (Path(vault) / new).exists():
            try:
                same = parse_front(_read(vault, new)).get("編號") == item["id"]
            except (ValueError, OSError, UnicodeDecodeError):
                same = False
            if same:  # 目的地是同編號的卡：收斂成那張（裁定 20）
                drop(old)
            else:
                skipped.append(f"「{new}」已經有同名的卡，沒搬：{old}")
            continue
        turn.backup(vault, old, turn_id, action="delete")
        turn.backup(vault, new, turn_id)
        write(new, text)
        shas[item["id"]] = (before, inventory.card_sha(vault, new))
        if undo is not None:
            undo.file(old)
        os.remove(Path(vault) / old)
        prune_dirs(vault, old)
        from kb._lib import card_base
        card_base.rekey(vault, old, new, synced=False)  # 搬的只換檔頭，內文位元組不動：紀錄原樣換鍵
        moved.append({"from": old, "to": new})
        changed += [{"path": old, "action": "delete"}, {"path": new, "action": "create"}]
    if inventory.follow_redo(vault, shas, turn_id, undo):
        changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    changed += sync_fronts(vault, turn_id, undo=undo)  # 卡式、封存照清冊補或拿掉（放寬丙 11、12）
    return {"moved": moved, "updated": updated, "dropped": dropped, "skipped": skipped, "changed": changed,
            "touched": touched}


def follow_outside(vault, sources, ids, turn_id, undo=None):
    """原檔搬到知識庫外沒登記的地方（outside）：卡留在原處，只把檔頭的 原檔 清單與 開原檔 換成新位置。回改到的卡路徑。"""
    from kb._lib import inventory, turn
    by_id = {e["id"]: e for e in inventory.load(vault)["entries"]}
    groups, _broken = _scan_groups(vault)
    changed = []
    for fid in ids:
        entry = by_id.get(fid)
        if entry is None or not entry.get("locations"):
            continue
        for card in groups.get(fid) or []:
            rel = card["path"]
            text = rewrite_locations(_read(vault, rel), [loc["uri"] for loc in entry["locations"]],
                                     entry_link(vault, sources, entry))
            if text == _read(vault, rel):
                continue
            if undo is not None:
                undo.file(rel)
            turn.backup(vault, rel, turn_id)
            write_text_atomic(Path(vault) / rel, text)
            changed.append(rel)
    return changed


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


def pending_type_cards(vault, type_name):
    """檔頭 待補型別 有 type_name（照實體名稱的比法）的檔案卡相對路徑，照路徑排（build 桌面版確認 A 段：型別只在待補型別裡）。"""
    from kb._lib import entities
    want = entities.key(type_name)
    got = []
    for _cid, group in _scan_groups(vault)[0].items():
        for card in group:
            pend = card["front"].get("待補型別")
            if isinstance(pend, list) and any(isinstance(t, str) and entities.key(t) == want for t in pend):
                got.append(card["path"])
    return sorted(got)


def rewrite_pending_types(text, old, new):
    """只換檔頭 待補型別 欄裡的 old 成 new（重複的去掉），其他行原樣；寫成一行的清單。"""
    from kb._lib import entities
    front = parse_front(text)
    pend = [t for t in front.get("待補型別") or [] if isinstance(t, str)]
    out, seen = [], set()
    for t in pend:
        t = new if entities.key(t) == entities.key(old) else t
        if entities.key(t) not in seen:
            seen.add(entities.key(t))
            out.append(t)
    lines = text.splitlines(keepends=True)
    got, i, in_front = [], 0, False
    while i < len(lines):
        line = lines[i]
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("\ufeff") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
        elif in_front and bare.startswith("待補型別:"):
            got.append(f"待補型別: [{', '.join(out)}]" + (line[len(bare):] or "\n"))
            i += 1
            while i < len(lines) and lines[i].startswith(("  - ", "- ")):
                i += 1
            continue
        got.append(line)
        i += 1
    return "".join(got)


def rewrite_entities(text, names):
    """只換檔頭的 實體 欄（kb entity rename／merge 改掛載），其他行原樣。一行清單或「實體:」下接「  - 項」的區塊都換，
    換完一律寫成一行的清單（測試後修正裁定 13，收法同 rewrite_locations）。"""
    lines = text.splitlines(keepends=True)
    out, i, in_front, found = [], 0, False, False
    while i < len(lines):
        line = lines[i]
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("﻿") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
            if not found:  # 舊版卡沒有實體欄：補一欄再改掛（放寬甲，D-322 第 24 件）
                out.append(f"實體: [{', '.join(names)}]" + (line[len(bare):] or "\n"))
        elif in_front and bare.startswith("實體:"):
            found = True
            out.append(f"實體: [{', '.join(names)}]" + (line[len(bare):] or "\n"))
            i += 1
            while i < len(lines) and lines[i].startswith(("  - ", "- ")):
                i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


def rewrite_mentions(text, names):
    """只換檔頭的 提到 欄（改名、合併跟著換），其他行原樣；沒有這欄的卡不補（放寬丙 7）。"""
    lines = text.splitlines(keepends=True)
    out, i, in_front = [], 0, False
    while i < len(lines):
        line = lines[i]
        bare = line.rstrip("\r\n")
        if i == 0 and bare.lstrip("﻿") == "---":
            in_front = True
        elif in_front and bare == "---":
            in_front = False
        elif in_front and bare.startswith("提到:"):
            out.append(f"提到: [{', '.join(names)}]" + (line[len(bare):] or "\n"))
            i += 1
            while i < len(lines) and lines[i].startswith(("  - ", "- ")):
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


def index_broken(vault):
    """總目錄.md 不見、讀不了（不是 UTF-8）或開頭不是總目錄的標題（放寬丙 6-3，D-311(2)）：該由 render_index 重寫。"""
    try:
        text = (Path(vault) / INDEX_POSIX).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    return not text.startswith(INDEX_HEAD.split("\n", 1)[0])


def render_index(vault, turn_id=None):
    """寫總資料夾根的 總目錄.md：巢狀清單（第一層來源別名、往下鏡射夾、葉子是卡），同層夾在前卡在後、各自按名稱排；
    解析不了的卡用檔名當標題。有 turn_id 就先備進回合（實作疑義裁定 4）。整段在 總目錄.md 的鎖裡（裁定 26）。回卡數。"""
    with FileLock(Path(vault) / INDEX_POSIX):
        return _render_index(vault, turn_id)


def icon_of(name):
    """原檔名的種類圖示（放寬丙 8，D-313）；沒有副檔名、認不得的用 📄。"""
    ext = os.path.splitext(str(name).lower())[1]
    for exts, mark in INDEX_ICONS:
        if ext in exts:
            return mark
    return DEFAULT_ICON


def _summary_lines(vault):
    """## 摘要：照型別表順序，每個型別一個子清單，列 active 實體連到它的綜合卡（有綜合卡的才列）。實體清單讀不到就空的。"""
    from kb._lib import entities
    try:
        data = entities.load(vault)
    except ValueError:
        return []
    prof, _missing = _profile_or_default(vault)
    by_type = {}
    for e in data["entities"]:
        if e.get("status") != "active" or entities.is_self(e) or not e.get("type"):
            continue
        by_type.setdefault(entities.key(e["type"]), []).append(e)
    out, old = [], []
    for t in prof.get("types") or []:
        rows = []
        for e in sorted(by_type.get(entities.key(t["name"]), []), key=lambda x: x["name"]):
            loc = entities.locate(vault, e)
            if not loc:
                continue
            if e.get("archived"):  # 封存的實體收進最後的摺疊框「已封存」（放寬丙 12，D-332）
                old.append(f"> - [{_link_text(e['name'])}]({_link_target(loc)})")
            else:
                rows.append(f"  - [{_link_text(e['name'])}]({_link_target(loc)})")
        if rows:
            out += [f"- {_link_text(t['name'])}"] + rows
    if old:
        out += ["", "> [!note]- 已封存"] + old
    return out


def _render_index(vault, turn_id):
    tree = {}
    count = 0
    icons = config.get(vault, "index_icons")
    for rel in _walk_cards(vault):
        parts = rel.split("/")[1:]
        node = tree
        for folder in parts[:-1]:
            node = node.setdefault(("d", folder), {})
        mark = ""
        try:
            front = parse_front(_read(vault, rel))
            title = front.get("標題")
            first = (front.get("原檔") or [""])[0] if isinstance(front.get("原檔"), list) else ""
            if icons:
                mark = icon_of(first) + " "
        except (ValueError, OSError, UnicodeDecodeError):
            title = None
            mark = (DEFAULT_ICON + " ") if icons else ""
        if not _filled(title):
            title = parts[-1][:-3]
        node[("c", parts[-1])] = (title, rel, mark)
        count += 1
    lines = []

    def emit(node, depth):
        for key in sorted(node, key=lambda k: (k[0] != "d", k[1])):
            pad = "  " * depth
            if key[0] == "d":
                lines.append(f"{pad}- {_link_text(key[1])}")
                emit(node[key], depth + 1)
            else:
                title, rel, mark = node[key]
                lines.append(f"{pad}- {mark}[{_link_text(title)}]({_link_target(rel)})")
    emit(tree, 0)
    summary = "\n".join(_summary_lines(vault)) or INDEX_NO_SUMMARY
    files = "\n".join(lines) if lines else INDEX_EMPTY
    sections = [f"## 摘要\n\n{summary}", f"## 檔案\n\n{files}"]
    if config.get(vault, "index_order") == "files_first":
        sections.reverse()
    text = f"{INDEX_HEAD}\n" + "\n\n".join(sections) + f"\n\n共 {count} 張卡\n"
    from kb._lib import renders
    if renders.keep_only(vault, INDEX_POSIX) == renders.KEEP_FAILED:  # 她手改過：留她那份（D-223，總目錄只存不問），照樣重寫
        return count  # 她那份沒存成就不蓋掉（測試 T-10）
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, INDEX_POSIX, turn_id)
    write_text_atomic(Path(vault) / INDEX_POSIX, text)
    renders.record(vault, INDEX_POSIX, text)
    return count


# ---------- 搬動（放寬丙 5-2，D-308）：給 kb changes --moved 與每句開頭的搬動提示 ----------

def group_moves(vault, moved):
    """scan 報告的 moved（[(舊 uri, 新 uri)]）→ 整夾搬算一件：舊夾底下清冊上全部的檔都搬到同一個新夾（相對位置不變）→
    {"kind":"folder","from","to","count","pairs"}，其他 {"kind":"file","from","to"}。程式不判斷算不算換分類。
    從最淺的夾開始試（根夾不算）；只有一個檔的夾當一個檔。只讀清冊。"""
    from kb._lib import inventory
    pairs = {inventory.uri_key(a): (a, b) for a, b in moved}
    under = [loc["uri"] for e in inventory.load(vault)["entries"] if e.get("status") == "active"
             for loc in e.get("locations") or []]
    done, out = set(), []
    for old, new in moved:
        k = inventory.uri_key(old)
        if k in done:
            continue
        o_parts, n_parts = old[len("file://"):].split("/"), new[len("file://"):].split("/")
        hit = None
        for depth in range(2, len(o_parts)):  # o_parts[0] 是別名；夾至少一層
            rest = o_parts[depth:]
            if len(n_parts) <= len(rest) + 1 or n_parts[-len(rest):] != rest:
                continue
            f_old, f_new = "file://" + "/".join(o_parts[:depth]), "file://" + "/".join(n_parts[:-len(rest)])
            if inventory.uri_key(f_old) == inventory.uri_key(f_new):
                continue
            kids = [u for u in under if inventory.uri_key(u).startswith(inventory.uri_key(f_old) + "/")]
            if len(kids) < 2:
                continue
            ok = True
            for u in kids:
                got = pairs.get(inventory.uri_key(u))
                if got is None or inventory.uri_key(got[1]) != inventory.uri_key(f_new + u[len(f_old):]):
                    ok = False
                    break
            if ok:
                hit = (f_old, f_new, kids)
                break
        if hit:
            done.update(inventory.uri_key(u) for u in hit[2])
            out.append({"kind": "folder", "from": hit[0], "to": hit[1], "count": len(hit[2]),
                        "pairs": [pairs[inventory.uri_key(u)] for u in hit[2]]})
        else:
            done.add(k)
            out.append({"kind": "file", "from": old, "to": new, "pairs": [(old, new)]})
    return out


def _entity_folder_of(vault, ent_data, uri, is_dir):
    """新位置的夾若是知識庫某個型別夾底下的 active 實體夾 → 實體名稱，否則 None。"""
    from kb._lib import entities
    from kb._lib.inventory import KNOWLEDGE
    alias, rel = _split(uri)
    parts = rel.split("/") if is_dir else rel.split("/")[:-1]
    if alias != KNOWLEDGE or len(parts) < 2 or ent_data is None:
        return None
    found, _hit = entities.find(ent_data, parts[1])
    type_ = profile.type_of_folder(vault, parts[0]) or parts[0]
    if (found is None or found.get("status") != "active" or entities.is_self(found)
            or entities.key(found.get("type")) != entities.key(type_)):
        return None
    return found["name"]


def moved_changes(vault, moved):
    """kb changes --moved --json 的 moved 清單：group_moves 之後每筆帶 name、id（檔）、entities（卡上現在掛的 main／mention，
    夾是裡面全部卡的聯集）、folder_entity（新位置在某個實體夾底下就帶那個實體）、user_folder（新位置的夾不是實體夾、清冊上本來也沒有任何檔：
    使用者自己開的）。只讀。"""
    from kb._lib import inventory
    groups = group_moves(vault, moved)
    if not groups:
        return []
    ent_data = _entities_or_none(vault)
    by_uri = {inventory.uri_key(loc["uri"]): e["id"] for e in inventory.load(vault)["entries"]
              for loc in e.get("locations") or []}
    known = [inventory.uri_key(u) for u in by_uri]
    cards_by_id = _scan_groups(vault)[0]
    out = []
    for g in groups:
        main, mention, ids = [], [], []
        for old, _new in g["pairs"]:
            fid = by_uri.get(inventory.uri_key(old))
            ids.append(fid)
            group = cards_by_id.get(fid) if fid else None
            front = group[0]["front"] if group else {}
            for key_, bucket in (("實體", main), ("提到", mention)):
                for n in front.get(key_) if isinstance(front.get(key_), list) else []:
                    if n not in bucket:
                        bucket.append(n)
        is_dir = g["kind"] == "folder"
        parent = g["to"].rsplit("/", 1)[0]
        pk = inventory.uri_key(parent) + "/"
        name = g["to"].rsplit("/", 1)[-1]
        item = {"kind": g["kind"], "name": name, "from": g["from"], "to": g["to"]}
        if is_dir:
            item["count"] = g["count"]
        else:
            item["id"] = ids[0]
        fe = _entity_folder_of(vault, ent_data, g["to"], is_dir)
        item.update(entities={"main": main, "mention": mention}, folder_entity=fe,
                    user_folder=fe is None and not any(k.startswith(pk) for k in known))
        out.append(item)
    return out
