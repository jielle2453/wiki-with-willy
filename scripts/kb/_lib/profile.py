"""客戶設定檔 .system/profile.json（schema 2，D-109～D-113、D-119、D-120）與渲染視圖 系統/客戶設定檔.md（D-052 一份三用、D-067）。

JSON 為真；md 由 render 從 JSON 寫出，提示句住這裡（D-067）。人改了 md 用 parse_md 讀回同形的 dict（同一支解析器，D-111），
diff 對 JSON 比、merge 補來源與日期、write 備進回合後寫 JSON 與 md。「md 有改」看內容指紋（md_sha256），不看 mtime。
status 給開場與 kb profile check 共用，只讀不寫。型別夾的對帳（folder_check）照 D-112：只建不刪、刪列只准空夾。
schema 2 多第四節每類段落（synthesis_sections，D-119）與第五節關於你（about，D-120）；schema 1 由 load 在記憶體遷移，
大類預設段落（CATEGORY_SECTIONS）只在遷移與套用新型別時寫進 JSON，製卡者只讀 JSON。
"""
import copy
import hashlib
import os
import re
import stat as stat_lib
import unicodedata
from datetime import date, datetime
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

PROFILE_POSIX = ".system/profile.json"
MD_POSIX = "系統/客戶設定檔.md"
SCHEMA_VERSION = 2
CATEGORIES = {"對象": "party", "事": "matter", "物": "asset", "主題": "topic"}
SOURCES = {"訪談": "interview", "推測": "inferred", "確認": "confirmed", "糾正": "corrected"}
ACTIONS = {"不整理": "no_card", "只記種類": "kind_only"}
KINDS = {"公司": "company", "本人": "person"}
RESERVED_TYPE_NAMES = ("AI 產出", "收件匣", "檔案索引", "系統", "CLAUDE.md")
REQUIRED_MARKS = ("✓", "是", "v", "V", "Y")
LISTS = ("types", "card_types", "exclusions", "synthesis_sections", "about")
LISTS_V1 = LISTS[:3]
NEW_LISTS = LISTS[3:]  # schema 2 多的兩節；legacy 解析缺節時取 JSON 遷移補的值
SECTION_NAMES = ("型別", "卡型", "不整理與敏感", "每類段落", "關於你")  # diff 摘要用的節名，順序同 LISTS
NUMERALS = ("一", "二", "三", "四", "五")
# 每節的基本欄數；後面可再加來源、日期兩欄
BASE_COLUMNS = (3, 4, 2, 4, 3)
HIDDEN_OR_SYSTEM = 0x2 | 0x4
# 四大類的預設段落（D-119）：load 遷移 schema 1、merge 遇到第一次加入的型別時寫進 JSON
CATEGORY_SECTIONS = {
    "party": (("聯絡與往來", "怎麼聯絡、平常跟誰接洽"), ("偏好與禁忌", "喜歡什麼、不能踩的雷")),
    "matter": (("時程", "開始、重要日期、預計結束"), ("金額", "報價、合約總價、付款期別與已付")),
    "asset": (("規格", "型號、尺寸、數量"), ("期限", "保固、到期、續約")),
    "topic": (),
}
_ALIAS_SPLIT = re.compile(r"[、,，]")

CARD_TYPE_NAMES = ("報價", "合約", "會議紀錄", "型錄", "進度表", "範本", "說明", "其他")
DEFAULT_SECTIONS = (("重點", "這份檔要記住的事，一點一行"),
                    ("日期與數字", "期限、金額、數量、面積；照原檔寫，不換算"),
                    ("提到的人與單位", "人名加角色、公司、廠商"),
                    ("沒讀到的部分", "讀取是「部分」時寫哪些沒讀、為什麼"))
DEFAULT_EXCLUSIONS = ("身分證字號", "護照號碼", "銀行帳號", "信用卡號", "密碼", "個人薪資金額", "統一編號", "電話", "地址")

HEADER = ("# 客戶設定檔\n"
          "這是什麼：你管哪幾類東西、每種文件要記什麼、哪些不整理，程式從 .system/profile.json 整理出來。\n"
          "怎麼用：可以直接改這個檔，下次開對話我會問你要不要套用；也可以跟我說。\n"
          "由程式產生，套用後會重寫；來源與日期兩欄可以留空，我會填。\n")
PROMPTS = (
    "你平常管哪幾類東西？一類一列。大類四選一：對象（人或公司）、事（有開始有結束的工作）、物（被管理的東西）、"
    "主題（規則與知識）。型別名會變成知識庫第一層的資料夾名，不能跟 AI 產出、收件匣、檔案索引、系統 一樣。"
    "不想讓我讀的資料夾不寫這裡，跟我說「不要讀 X」。",
    "常查的文件種類，每種想記住哪些段？一段一列；「必出現」打 ✓ 的段，原檔沒寫時卡上會寫「未知」。",
    "哪些種類的檔整份不整理成卡（我讀到會跳過，但程式認不出種類，只有我讀了才知道；要保證不讀用「不要讀 X」）、"
    "哪些資料只記種類不記值？",
    "每類的綜合卡除了現況、紀事、推算，還要固定寫哪些段？一段一列；「必出現」打 ✓ 的段沒內容時寫「未知」。"
    "新加的型別我會先填一組預設，你再改。",
    "你的公司與你本人叫什麼？檔案裡出現這些名字時我知道是你，不會替你自己寫一張。別名用頓號分開。",
)
TITLES = ("型別", "卡型與段落", "不整理與敏感", "每類最想知道什麼", "關於你")
TABLE_HEADS = (("名稱", "大類", "一句說明", "來源", "日期"),
               ("卡型", "段落", "必出現", "提示", "來源", "日期"),
               ("種類", "處理", "來源", "日期"),
               ("型別", "段落", "必出現", "提示", "來源", "日期"),
               ("名稱", "是什麼", "別名", "來源", "日期"))

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Windows 保留裝置名（測試後修正裁定 12）：帶副檔名也算，不分大小寫
_WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$", re.IGNORECASE)
TYPE_NAME_MAX = 40  # card_title_max_chars 的預設；status 從 config 讀（測試後修正裁定 11）
NAME_SHOWN = 10
_SEPARATOR = re.compile(r"^[|\-: ]+$")
_REVERSED = {v: k for k, v in CATEGORIES.items()}, {v: k for k, v in SOURCES.items()}, {v: k for k, v in ACTIONS.items()}
_KINDS_REVERSED = {v: k for k, v in KINDS.items()}


class ProfileError(ValueError):
    """解析錯誤：section 是 1～5、row 是那節表格的第幾列資料（表頭與分隔列不算）；缺節時 row 是 None。"""

    def __init__(self, section, row, message):
        super().__init__(message)
        self.section, self.row, self.message = section, row, message


def _today():
    return date.today().isoformat()


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _key(name):
    """型別名比對用：全半形 NFKC 後不分大小寫。"""
    return unicodedata.normalize("NFKC", str(name)).casefold()


RESERVED_KEYS = {_key(n) for n in RESERVED_TYPE_NAMES}


# ---------- JSON ----------

def defaults(today=None):
    today = today or _today()
    return {
        "schema_version": SCHEMA_VERSION, "applied": None, "md_sha256": None,
        "types": [],
        "card_types": [{"name": name, "sections": [{"name": s, "required": False, "hint": h} for s, h in DEFAULT_SECTIONS],
                        "source": "inferred", "date": today} for name in CARD_TYPE_NAMES],
        "exclusions": [{"kind": k, "action": "kind_only", "source": "inferred", "date": today} for k in DEFAULT_EXCLUSIONS],
        "synthesis_sections": [],
        "about": [],
    }


def default_sections(types, today=None):
    """型別清單 → 第四節的大類預設列（來源推測、日期 today）；主題類沒有預設，不出列。"""
    today = today or _today()
    return [{"type": t["name"], "sections": [{"name": n, "required": False, "hint": h}
                                             for n, h in CATEGORY_SECTIONS.get(t.get("category"), ())],
             "source": "inferred", "date": today}
            for t in types if CATEGORY_SECTIONS.get(t.get("category"))]


def _path(vault):
    return Path(vault) / PROFILE_POSIX


def stored_schema(vault):
    """磁碟上 profile.json 記的 schema_version（沒檔、壞檔、沒寫回 None）：判斷要不要 legacy 解析、kb init 要不要升級。"""
    try:
        data = read_json(_path(vault))
    except ValueError:
        return None
    version = data.get("schema_version") if isinstance(data, dict) else None
    return version if isinstance(version, int) else None


def load(vault):
    """沒檔回 None；壞 JSON、不是物件、清單缺一、清單裡有非物件、版本比這版新丟 ValueError。
    schema 1 在記憶體遷移成 2（不寫檔）：第四節照每個型別的大類補預設（來源推測、日期今天），第五節空。"""
    data = read_json(_path(vault))
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("設定檔不是物件")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"設定檔版本 {version!r} 比這版 plugin 新")
    for key in (LISTS if version >= 2 else LISTS_V1):
        if not isinstance(data.get(key), list) or any(not isinstance(x, dict) for x in data[key]):
            raise ValueError(f"設定檔沒有 {key} 清單")
    if version < 2:
        data = dict(data, schema_version=SCHEMA_VERSION, synthesis_sections=default_sections(data["types"]), about=[])
    return data


# ---------- 解析 ----------

def _sections(lines):
    """回 {1: [行], …, 5: [...]}：各節標題之後到下一個 ## 標題之前；同一節出現兩次只認第一個。"""
    found, current = {}, None
    for line in lines:
        if line.startswith("## "):
            current = None
            for i, num in enumerate(NUMERALS, 1):
                if line.startswith(f"## {num}、") and i not in found:
                    found[i] = []
                    current = i
            continue
        if current is not None:
            found[current].append(line)
    return found


def _table_rows(section, lines):
    """這節第一張表（第一個 | 開頭的行起，到第一個空行為止）去掉表頭列與分隔列，回每列的格子清單。
    表裡夾了非空、非 | 開頭的行就報錯，不靜默切斷（確認後修正裁定 22）；空行之後的字照舊忽略。"""
    table, started = [], False
    for line in lines:
        if not started:
            if line.lstrip().startswith("|"):
                table.append(line.strip())
                started = True
            continue
        if not line.strip():
            break
        if not line.lstrip().startswith("|"):
            n = sum(1 for t in table[1:] if not _SEPARATOR.match(t)) + 1
            _err(section, n, "不是表格列（少了開頭的 |，或多了一行字）")
        table.append(line.strip())
    rows = []
    for line in table[1:]:
        if _SEPARATOR.match(line):
            continue
        body = line[1:]
        if body.endswith("|"):
            body = body[:-1]
        rows.append([c.strip() for c in body.split("|")])
    return rows


def _err(section, row, text):
    raise ProfileError(section, row, f"第 {section} 節第 {row} 列{text}")


def _columns(section, row, cells):
    base = BASE_COLUMNS[section - 1]
    if len(cells) not in (base, base + 2):
        _err(section, row, f"欄數不對（要 {base} 欄或 {base + 2} 欄）")
    extra = cells[base:] if len(cells) == base + 2 else ["", ""]
    return cells[:base], extra


def _source_date(section, row, extra):
    raw_source, raw_date = extra
    if raw_source and raw_source not in SOURCES:
        _err(section, row, "的來源要是 訪談、推測、確認、糾正 之一，或留空")
    if raw_date:
        ok = bool(_DATE.match(raw_date))
        if ok:
            try:
                datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                ok = False
        if not ok:
            _err(section, row, "的日期要寫 YYYY-MM-DD，或留空")
    return (SOURCES[raw_source] if raw_source else None), (raw_date or None)


def type_name_problem(name, taken=()):
    """型別名不能用的原因（括號裡那句），能用回 None。taken 是已出現過的 _key。保留名與長度另由 _parse_types 擋。"""
    if not name:
        return "空的"
    if _key(name) in RESERVED_KEYS:
        return "跟保留字一樣"
    if _BAD_CHARS.search(name):
        return "含不能用的字元"
    if name.startswith(".") or name.endswith(".") or name != name.rstrip():
        return "不能以點開頭，也不能以點或空白結尾"
    if _key(name) in taken:
        return "重複"
    return None


def _parse_types(rows, max_chars):
    out, taken = [], set()
    for m, cells in enumerate(rows, 1):
        (name, category, note), extra = _columns(1, m, cells)
        why = type_name_problem(name, taken)
        if why:
            _err(1, m, f"的型別名「{name}」不能用（{why}）")
        if _WINDOWS_RESERVED.match(name):
            _err(1, m, f"的型別名「{name}」是 Windows 保留名，不能當資料夾")
        if len(name) > max_chars:
            _err(1, m, f"的型別名「{name[:NAME_SHOWN]}…」太長（最多 {max_chars} 字）")
        if category not in CATEGORIES:
            _err(1, m, "的大類要是 對象、事、物、主題 之一")
        source, when = _source_date(1, m, extra)
        taken.add(_key(name))
        out.append({"name": name, "category": CATEGORIES[category], "note": note, "source": source, "date": when})
    return out


def _parse_card_types(rows):
    out, by_name, seen = [], {}, {}
    for m, cells in enumerate(rows, 1):
        (name, section, required, hint), extra = _columns(2, m, cells)
        if not name:
            _err(2, m, "的卡型是空的")
        if not section:
            _err(2, m, "的段落是空的")
        if required and required not in REQUIRED_MARKS:
            _err(2, m, "的必出現只能打 ✓ 或留空")
        source, when = _source_date(2, m, extra)
        if (name, section) in seen:
            _err(2, m, f"跟第 {seen[(name, section)]} 列重複（卡型 {name} 的段落 {section}）")
        seen[(name, section)] = m
        if name not in by_name:  # 卡型的來源日期取它的第一列
            by_name[name] = {"name": name, "sections": [], "source": source, "date": when}
            out.append(by_name[name])
        by_name[name]["sections"].append({"name": section, "required": bool(required), "hint": hint})
    return out


def _parse_exclusions(rows):
    out, seen = [], {}
    for m, cells in enumerate(rows, 1):
        (kind, action), extra = _columns(3, m, cells)
        if not kind:
            _err(3, m, "的種類是空的")
        if action not in ACTIONS:
            _err(3, m, "的處理要是 不整理 或 只記種類")
        source, when = _source_date(3, m, extra)
        if kind in seen:
            _err(3, m, f"跟第 {seen[kind]} 列重複（種類 {kind}）")
        seen[kind] = m
        out.append({"kind": kind, "action": ACTIONS[action], "source": source, "date": when})
    return out


def _parse_synthesis(rows, types):
    """第四節：型別要在第一節（比對不分全半形大小寫，存第一節的寫法）；同型別的段落併一筆，來源日期取第一列。"""
    names = {_key(t["name"]): t["name"] for t in types}
    out, by_type, seen = [], {}, {}
    for m, cells in enumerate(rows, 1):
        (tname, section, required, hint), extra = _columns(4, m, cells)
        if _key(tname) not in names:
            _err(4, m, f"的型別「{tname}」不在第 1 節")
        if not section:
            _err(4, m, "的段落是空的")
        if required and required not in REQUIRED_MARKS:
            _err(4, m, "的必出現只能打 ✓ 或留空")
        source, when = _source_date(4, m, extra)
        key = (_key(tname), section)
        if key in seen:
            _err(4, m, f"跟第 {seen[key]} 列重複（型別 {tname} 的段落 {section}）")
        seen[key] = m
        name = names[_key(tname)]
        if name not in by_type:
            by_type[name] = {"type": name, "sections": [], "source": source, "date": when}
            out.append(by_type[name])
        by_type[name]["sections"].append({"name": section, "required": bool(required), "hint": hint})
    return out


def split_aliases(raw):
    return [a.strip() for a in _ALIAS_SPLIT.split(str(raw or "")) if a.strip()]


def _parse_about(rows, types, max_chars):
    """第五節：名稱照型別名的規則、也不能跟第一節的型別名一樣；是什麼 公司｜本人；名稱與別名在各列之間不能重複。"""
    type_keys = {_key(t["name"]) for t in types}
    out, seen = [], {}
    for m, cells in enumerate(rows, 1):
        (name, kind, aliases), extra = _columns(5, m, cells)
        why = type_name_problem(name)
        if not why and _WINDOWS_RESERVED.match(name):
            why = "Windows 保留名"
        if not why and len(name) > max_chars:
            why = f"太長，最多 {max_chars} 字"
        if not why and _key(name) in type_keys:
            why = "跟第 1 節的型別名一樣"
        if why:
            _err(5, m, f"的名稱「{name}」不能用（{why}）")
        if kind not in KINDS:
            _err(5, m, "的是什麼要是 公司 或 本人")
        source, when = _source_date(5, m, extra)
        alias_list = [a for a in split_aliases(aliases) if _key(a) != _key(name)]
        for word in [name] + alias_list:
            if _key(word) in seen:
                _err(5, m, f"跟第 {seen[_key(word)]} 列重複（{word}）")
        for word in [name] + alias_list:
            seen[_key(word)] = m
        out.append({"name": name, "kind": KINDS[kind], "aliases": alias_list, "source": source, "date": when})
    return out


def parse_md(text, max_type_chars=TYPE_NAME_MAX, legacy=False):
    """md → {"types", "card_types", "exclusions", "synthesis_sections", "about"}（跟 JSON 同形，source／date 沒填是 None）；
    讀不懂丟 ProfileError。各節都在但沒有任何列算成功；少任何一節就是錯（I-097：命中 0 節不回空）。型別名長度上限是 max_type_chars。
    legacy（JSON 還是 schema 1，舊 md 只有三節）：缺第四、五節不算錯，那兩節回 None，呼叫端取 JSON 遷移補的值。"""
    lines = str(text).lstrip("﻿").splitlines()
    found = _sections(lines)
    for i, num in enumerate(NUMERALS, 1):
        if i not in found and not (legacy and i > len(LISTS_V1)):
            raise ProfileError(i, None, f"少了第 {i} 節（## {num}、…）")
    types = _parse_types(_table_rows(1, found[1]), max_type_chars)
    return {"types": types,
            "card_types": _parse_card_types(_table_rows(2, found[2])),
            "exclusions": _parse_exclusions(_table_rows(3, found[3])),
            "synthesis_sections": _parse_synthesis(_table_rows(4, found[4]), types) if 4 in found else None,
            "about": _parse_about(_table_rows(5, found[5]), types, max_type_chars) if 5 in found else None}


# ---------- 比對與合併 ----------

def _type_rows(data):
    return {t["name"]: t for t in data.get("types") or []}


def _section_rows(data):
    return {(c["name"], s["name"]): s for c in data.get("card_types") or [] for s in c.get("sections") or []}


def _exclusion_rows(data):
    return {e["kind"]: e for e in data.get("exclusions") or []}


def _synthesis_rows(data):
    return {(s["type"], x["name"]): x for s in data.get("synthesis_sections") or [] for x in s.get("sections") or []}


def _about_rows(data):
    return {a["name"]: a for a in data.get("about") or []}


def _compare(old, new, fields):
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and any(new[k].get(f) != old[k].get(f) for f in fields)]
    return {"added": added, "removed": removed, "changed": changed}


def diff(old, new):
    """以鍵比：型別＝名稱、卡型＝[卡型名, 段落名]、排除＝種類、每類段落＝[型別, 段落名]、關於你＝名稱；
    changed 是同鍵其他欄不同（來源與日期不算）。"""
    cards = _compare(_section_rows(old), _section_rows(new), ("required", "hint"))
    synthesis = _compare(_synthesis_rows(old), _synthesis_rows(new), ("required", "hint"))
    return {"types": _compare(_type_rows(old), _type_rows(new), ("category", "note")),
            "card_types": {k: [list(x) for x in v] for k, v in cards.items()},
            "exclusions": _compare(_exclusion_rows(old), _exclusion_rows(new), ("action",)),
            "synthesis_sections": {k: [list(x) for x in v] for k, v in synthesis.items()},
            "about": _compare(_about_rows(old), _about_rows(new), ("kind", "aliases"))}


def has_diff(d):
    return any(v for part in d.values() for v in part.values())


def counts(d):
    """[(節名, +, -, ~)] 照節的順序。"""
    return [(name, len(d[key]["added"]), len(d[key]["removed"]), len(d[key]["changed"]))
            for key, name in zip(LISTS, SECTION_NAMES)]


def summary_full(d):
    """check 第一行用：「型別 +1 -0 ~0、不整理與敏感 +1 -0 ~0」；三個數都 0 的節不印。"""
    return "、".join(f"{name} +{a} -{r} ~{c}" for name, a, r, c in counts(d) if a or r or c)


def summary_short(d):
    """apply 與開場用：「型別 +1 -1、卡型 ~2」；0 的項不印。"""
    parts = []
    for name, a, r, c in counts(d):
        bits = [f"{sign}{n}" for sign, n in (("+", a), ("-", r), ("~", c)) if n]
        if bits:
            parts.append(f"{name} {' '.join(bits)}")
    return "、".join(parts)


def _stamp(new_row, old_row, changed, today, first):
    """回 (source, date)：沒變的保留舊值；新列與變了的列人填的優先（等於舊值的當沒填，實作疑義裁定 4），
    沒填來源：第一次套用 interview、之後 confirmed；沒填日期：today。"""
    if old_row is not None and not changed:
        return old_row.get("source"), old_row.get("date")
    source, when = new_row.get("source"), new_row.get("date")
    if old_row is not None:
        if source == old_row.get("source"):
            source = None
        if when == old_row.get("date"):
            when = None
    return source or ("interview" if first else "confirmed"), when or today


def merge(old, new, today=None, first=False):
    """回合併後的五節，順序照 md。第一節新加入的型別（舊 JSON 沒有）、第四節又沒有它的列 → 補該大類的預設列（推測、today），
    排在第四節最後；舊 JSON 已有、第四節被刪光的型別不補（D-119）。"""
    today = today or _today()
    d = diff(old, new)
    old_types, old_cards, old_ex = _type_rows(old), {c["name"]: c for c in old.get("card_types") or []}, _exclusion_rows(old)
    types = []
    for t in new["types"]:
        source, when = _stamp(t, old_types.get(t["name"]), t["name"] in d["types"]["changed"], today, first)
        types.append({"name": t["name"], "category": t["category"], "note": t["note"], "source": source, "date": when})
    touched = {k[0] for part in d["card_types"].values() for k in part}
    card_types = []
    for c in new["card_types"]:
        source, when = _stamp(c, old_cards.get(c["name"]), c["name"] in touched, today, first)
        card_types.append({"name": c["name"], "sections": [dict(s) for s in c["sections"]], "source": source, "date": when})
    exclusions = []
    for e in new["exclusions"]:
        source, when = _stamp(e, old_ex.get(e["kind"]), e["kind"] in d["exclusions"]["changed"], today, first)
        exclusions.append({"kind": e["kind"], "action": e["action"], "source": source, "date": when})
    old_synth = {s["type"]: s for s in old.get("synthesis_sections") or []}
    touched_synth = {k[0] for part in d["synthesis_sections"].values() for k in part}
    synthesis = []
    for s in new.get("synthesis_sections") or []:
        source, when = _stamp(s, old_synth.get(s["type"]), s["type"] in touched_synth, today, first)
        synthesis.append({"type": s["type"], "sections": [dict(x) for x in s["sections"]], "source": source, "date": when})
    had = {_key(t["name"]) for t in old.get("types") or []}
    listed = {_key(s["type"]) for s in synthesis}
    synthesis += default_sections([t for t in types if _key(t["name"]) not in had and _key(t["name"]) not in listed], today)
    old_about = _about_rows(old)
    about = []
    for a in new.get("about") or []:
        source, when = _stamp(a, old_about.get(a["name"]), a["name"] in d["about"]["changed"], today, first)
        about.append({"name": a["name"], "kind": a["kind"], "aliases": list(a["aliases"]), "source": source, "date": when})
    return {"types": types, "card_types": card_types, "exclusions": exclusions, "synthesis_sections": synthesis,
            "about": about}


def unskipped_kinds(old, new):
    """第三節拿掉、或從 no_card 改成別的的種類：清冊裡 skip_kind 是它們的條目要回到待製卡。"""
    after = {e["kind"]: e["action"] for e in new.get("exclusions") or []}
    return [e["kind"] for e in old.get("exclusions") or []
            if e.get("action") == "no_card" and after.get(e["kind"]) != "no_card"]


# ---------- 型別夾 ----------

def _first_level_dirs(vault):
    """知識庫第一層的夾名：不含保留夾、點開頭、隱藏或系統屬性、檔。"""
    reserved = {_key(n) for n in RESERVED_TYPE_NAMES}
    out = []
    try:
        items = sorted(os.scandir(vault), key=lambda i: i.name)
    except OSError:
        return out
    for item in items:
        if item.name.startswith(".") or _key(item.name) in reserved:
            continue
        try:
            st = item.stat(follow_symlinks=False)
        except OSError:
            continue
        if getattr(st, "st_file_attributes", 0) & HIDDEN_OR_SYSTEM:
            continue
        if stat_lib.S_ISDIR(st.st_mode) or item.is_symlink():
            out.append(item.name)
    return out


def _item_count(path):
    try:
        with os.scandir(path) as it:
            return sum(1 for _ in it)
    except OSError:
        return 0


def folder_check(vault, types, old_types=None):
    """型別表對第一層夾（D-112）：to_create 表裡有、夾還沒有；unlisted 夾有、表裡沒有；
    removed_* 是舊 JSON 有、這份表沒有的型別，夾空（或沒有夾）的進 empty，有東西的進 nonempty [(名稱, 項數)]。
    old_types 沒給讀 profile.json。"""
    if old_types is None:
        old_types = (load(vault) or {}).get("types") or []
    names = [t["name"] for t in types]
    listed = {_key(n) for n in names}
    to_create = [n for n in names if not (Path(vault) / n).is_dir()]
    unlisted = [n for n in _first_level_dirs(vault) if _key(n) not in listed]
    removed_empty, removed_nonempty = [], []
    for t in old_types:
        if _key(t["name"]) in listed:
            continue
        n = _item_count(Path(vault) / t["name"]) if (Path(vault) / t["name"]).is_dir() else 0
        if n:
            removed_nonempty.append((t["name"], n))
        else:
            removed_empty.append(t["name"])
    return {"to_create": to_create, "unlisted": unlisted, "removed_empty": removed_empty,
            "removed_nonempty": removed_nonempty}


# ---------- 渲染 ----------

def _cell(value):
    return " ".join(str(value or "").replace("|", "｜").split())


def _row(cells):
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def render_text(data):
    """JSON → md 全文；表格不對齊，分隔列固定短橫線。"""
    cats, srcs, acts = _REVERSED
    tables = (
        [[t["name"], cats.get(t.get("category"), ""), t.get("note"), srcs.get(t.get("source"), ""), t.get("date")]
         for t in data.get("types") or []],
        [[c["name"], s["name"], "✓" if s.get("required") else "", s.get("hint"), srcs.get(c.get("source"), ""),
          c.get("date")] for c in data.get("card_types") or [] for s in c.get("sections") or []],
        [[e["kind"], acts.get(e.get("action"), ""), srcs.get(e.get("source"), ""), e.get("date")]
         for e in data.get("exclusions") or []],
        [[s["type"], x["name"], "✓" if x.get("required") else "", x.get("hint"), srcs.get(s.get("source"), ""),
          s.get("date")] for s in data.get("synthesis_sections") or [] for x in s.get("sections") or []],
        [[a["name"], _KINDS_REVERSED.get(a.get("kind"), ""), "、".join(a.get("aliases") or []),
          srcs.get(a.get("source"), ""), a.get("date")] for a in data.get("about") or []],
    )
    parts = [HEADER]
    for num, title, prompt, head, rows in zip(NUMERALS, TITLES, PROMPTS, TABLE_HEADS, tables):
        lines = [f"## {num}、{title}", prompt, "", _row(head), "|" + "---|" * len(head)]
        lines += [_row(r) for r in rows]
        parts.append("\n" + "\n".join(lines) + "\n")
    return "".join(parts)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def md_sha256(vault):
    p = Path(vault) / MD_POSIX
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def render(vault, data):
    """寫 系統/客戶設定檔.md，回寫出的文字；備份與 md_sha256 由呼叫端管。"""
    text = render_text(data)
    target = Path(vault) / MD_POSIX
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    return text


def write_locked(vault, data, turn_id):
    """呼叫端持有 profile.json 的鎖：兩檔備進回合（D-074）、先寫 md 再把它的 sha 寫進 JSON。回寫進去的 JSON。
    md 是人改過的（sha 不等於舊 JSON 記的）時，備進回合的是舊 JSON 渲染出的那份：退回後 JSON 與 md 一致、開場不再問（驗收 7）；
    舊 JSON 沒記 sha（半份）就照磁碟上的備，人寫的不丟。"""
    from kb._lib import turn
    old = load(vault)
    md = Path(vault) / MD_POSIX
    if (old is not None and old.get("md_sha256") and md.is_file() and md_sha256(vault) != old["md_sha256"]
            and not any(e.get("path") == MD_POSIX for e in turn.entries(vault, turn_id))):
        write_text_atomic(md, render_text(old))
    turn.backup(vault, PROFILE_POSIX, turn_id)
    turn.backup(vault, MD_POSIX, turn_id)
    data = copy.deepcopy(data)
    data["md_sha256"] = sha256_text(render(vault, data))
    write_json_atomic(_path(vault), data)
    return data


def write(vault, data, turn_id):
    _path(vault).parent.mkdir(parents=True, exist_ok=True)
    with FileLock(_path(vault)):
        return write_locked(vault, data, turn_id)


def lock(vault):
    _path(vault).parent.mkdir(parents=True, exist_ok=True)
    return FileLock(_path(vault))


# ---------- 狀態 ----------

def status(vault):
    """開場與 check 共用，只讀。回 {"state": ok|changed|error|missing, "diff", "error", "unlisted", "to_create",
    "removed_empty", "removed_nonempty", "data"（JSON）, "new"（解析出的 md，changed 才有）}。
    profile.json 壞了丟 ValueError（呼叫端各自處理，I-046）。"""
    data = load(vault)
    out = {"state": "missing", "diff": None, "error": None, "unlisted": [], "to_create": [], "removed_empty": [],
           "removed_nonempty": [], "data": data, "new": None}
    md = Path(vault) / MD_POSIX
    if data is None or not md.is_file():
        return out
    types = data["types"]
    if md_sha256(vault) == data.get("md_sha256"):
        out["state"] = "ok"
    else:
        try:
            from kb._lib import config
            legacy = stored_schema(vault) == 1
            new = parse_md(md.read_text(encoding="utf-8"), config.get(vault, "card_title_max_chars"), legacy=legacy)
            for key in NEW_LISTS:  # 舊 md 沒有第四、五節：取 JSON 遷移補的值
                if new[key] is None:
                    new[key] = copy.deepcopy(data[key])
        except ProfileError as e:
            out["state"], out["error"] = "error", e.message
        except UnicodeDecodeError:
            out["state"], out["error"] = "error", "檔案不是 UTF-8"
        else:
            out["state"], out["new"], out["diff"] = "changed", new, diff(data, new)
            types = new["types"]
    out.update(folder_check(vault, types, data["types"]))
    if out["state"] != "changed":  # 沒有要套用的表：刪列的對帳不算
        out["removed_empty"], out["removed_nonempty"] = [], []
    if out["state"] == "error":  # 表讀不了，第一層夾的對帳沒意義（測試後修正裁定 14）
        out["unlisted"], out["to_create"] = [], []
    return out


def _plain_sections(sections):
    return [{"name": s["name"], "required": bool(s.get("required")), "hint": s.get("hint", "")} for s in sections or []]


def card_types_for_todo(data):
    """給 cards todo --json 的 profile 塊：去掉 source、date；多型別、每類段落、關於你（實體與綜合卡第 10 點）。"""
    return {"card_types": [{"name": c["name"], "sections": _plain_sections(c.get("sections"))}
                           for c in data.get("card_types") or []],
            "exclusions": [{"kind": e["kind"], "action": e["action"]} for e in data.get("exclusions") or []],
            "types": [{"name": t["name"], "category": t.get("category")} for t in data.get("types") or []],
            "synthesis_sections": [{"type": s["type"], "sections": _plain_sections(s.get("sections"))}
                                   for s in data.get("synthesis_sections") or []],
            "about": [{"name": a["name"], "kind": a.get("kind"), "aliases": list(a.get("aliases") or [])}
                      for a in data.get("about") or []]}


def no_card_kinds(data):
    return [e["kind"] for e in (data or {}).get("exclusions") or [] if e.get("action") == "no_card"]


def now():
    return _now()
