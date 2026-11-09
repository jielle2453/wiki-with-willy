"""程式產生的頁（系統/日程.md、待處理.md、裁示.md、帳.md 與帳的年頁）被她手改：認得出的照做，認不出的留在頁面上（Obsidian 首頁，#22，D-263 修訂 D-223）。

認得出的（renders.diff）：打勾、刪掉一行、改日期；每頁能做的不一樣（日程：完成、取消、改日期；待處理：處理好了、不用管；裁示：收回），
這頁做不了的（例如裁示打勾）算認不出。認不出的（新打的行、改了某行的字）記進 renders.json 的 carried，重新產生時放在那頁最底下
「你加的（還沒處理）」，kb asks 每筆出一題；她在那段刪掉一行 = 不用了。她那份照舊存進 .system/handedits/（存不下回 KEEP_FAILED，呼叫端
這次不重寫）；歷程 by 是 hand。

呼叫端（agenda._mutate、pending._edit、rulings._mutate）已經拿著那份狀態檔的鎖、手上有資料：consume 在資料上直接改，寫檔與重新產生由呼叫端接著做。
"""
import copy
import difflib

from kb._lib import ledger, renders

KEEP_FAILED = renders.KEEP_FAILED
UNRECOGNIZED = "unrecognized"  # 認手改時出錯：她那份存了、開了 handedit，頁面照常重寫（裁定 22）
SIMILAR = 0.6  # 刪掉的那行跟新打的那行像到這個程度，算「改了某行的字」而不是刪一行加一行
PAGE_NAMES = {"系統/日程.md": "日程.md", "系統/待處理.md": "待處理.md", "系統/裁示.md": "裁示.md", "系統/帳.md": "帳.md"}
MONEY_YEAR_PREFIX = "系統/帳/"  # 帳的每年一頁（記帳 3，D-284）：照「系統/帳/ 底下」認，不一頁一頁列


def page_name(rel):
    """工作日誌與每句開頭說的頁名：三頁加帳.md 照 PAGE_NAMES；帳的年頁說「帳/2026.md」。"""
    rel = str(rel)
    if rel.startswith(MONEY_YEAR_PREFIX):
        return rel[len("系統/"):]
    return PAGE_NAMES.get(rel, rel)


def _strip(line):
    return line[2:] if line.startswith("- ") else line


def plan(vault, rel, hers, base_text, lines, known_ids, ignore_sections=(), skip=None):
    """她改了什麼，分好：{"checked","removed","changed_date"（認得出，每項 {"id","line"}，改日期多 "to"）,
    "add": [{"kind","text","orig"}]（認不出的）, "drop": [carried 編號]（她在「你加的」那段刪掉的）}。
    比對出錯只記 log、當成改了別的。"""
    try:
        got = renders.diff(base_text, hers, lines, ignore_sections, skip)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 手改比對失敗：{type(e).__name__}: {e}", source="renders")
        got = {"checked": [], "removed": [], "changed_date": [], "other": ["（比對不出改了哪裡）"]}
    if known_ids is not None:
        got = renders.keep_known(got, known_ids)
    add = []
    removed = list(got["removed"])
    for body in got["other"]:
        match = max(removed, key=lambda r: _ratio(r["line"], body), default=None)
        if match is not None and _ratio(match["line"], body) >= SIMILAR:
            removed.remove(match)  # 改了某行的字：不是刪掉那條
            add.append({"kind": "changed", "text": _strip(body), "orig": _strip(match["line"])})
        else:
            add.append({"kind": "added", "text": _strip(body), "orig": None})
    got["removed"] = removed
    stored = renders.carried(vault, rel)
    shown = {renders.carried_line(c): c for c in stored}
    mine = renders.carried_lines_in(hers)
    got["drop"] = [c["id"] for c in stored if renders.carried_line(c) not in set(mine)]
    typed = [m for m in mine if m not in shown]  # 她直接打在「你加的」那段裡的
    got["add"] = add + [{"kind": "added", "text": m, "orig": None} for m in typed]
    return got


def _ratio(a, b):
    return difflib.SequenceMatcher(None, renders._undated(str(a)), renders._undated(str(b))).ratio()


def is_empty(got):
    return not any(got.get(k) for k in ("checked", "removed", "changed_date", "add", "drop"))


def consume(vault, rel, hers, base_text, lines, known_ids, apply_fn, ignore_sections=(), state=None, skip=None):
    """在鎖裡（呼叫端拿著）處理她那份：回 None（沒有要做的）、KEEP_FAILED（她那份沒存成，這次不重寫）或報告
    {"applied": [句子], "carried": [認不出的字], "dropped": 幾筆不用了, "saved": 她那份存到哪}。
    apply_fn(got) 在資料上做認得出的，回 (做了什麼的句子, 這頁做不了而改記成認不出的)。state 是 dict 時，她那份存好就把路徑放進
    state["saved"]（guarded 靠它知道不用再存一份）。"""
    got = plan(vault, rel, hers, base_text, lines, known_ids, ignore_sections, skip)
    if is_empty(got):
        return None
    try:
        saved = renders.keep(vault, rel, hers)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"{rel} 她那份沒存成，這次不重寫：{type(e).__name__}: {e}", source="renders")
        return KEEP_FAILED
    if state is not None:
        state["saved"] = saved
    applied, extra = apply_fn(got)
    added, dropped = renders.add_carried(vault, rel, got["add"] + list(extra), drop_ids=got["drop"])
    return {"applied": applied, "carried": [renders.carried_line(c) for c in added], "dropped": len(dropped), "saved": saved}


def guarded(vault, rel, hers, data, run, open_item):
    """consume 包一層（三頁的 _consume_hand 都用）：run(state) 認手改時丟例外，記 hook-errors.log，data 退回呼叫前的樣子，
    她那份只存一份（consume 已經存了就不再存），另開一件 handedit（open_item(存到哪, 差異)，D-223 原本那條路），回 UNRECOGNIZED；
    呼叫端照常做它自己的寫入、頁面照常重寫，不讓之後每次寫都失敗。她那份存不下回 KEEP_FAILED（這次不重寫）。"""
    snap = copy.deepcopy(data)
    state = {}
    try:
        return run(state)
    except Exception as e:  # noqa: BLE001
        data.clear()
        data.update(snap)
        ledger.log_error(vault, f"{rel} 認手改失敗，這次照常寫、不照做：{type(e).__name__}: {e}", source="renders")
        saved = state.get("saved")
        if saved is None:
            try:
                saved = renders.keep(vault, rel, hers)
            except Exception as e2:  # noqa: BLE001
                ledger.log_error(vault, f"{rel} 她那份沒存成：{type(e2).__name__}: {e2}", source="renders")
                return KEEP_FAILED
        try:
            open_item(saved, renders.mask_diff(vault, {"checked": [], "removed": [], "changed_date": [],
                                                       "other": ["（比對不出改了哪裡）"]}))
        except Exception as e3:  # noqa: BLE001
            ledger.log_error(vault, f"{rel} 手改沒記進待處理：{type(e3).__name__}: {e3}", source="renders")
        return UNRECOGNIZED


def note(rel, report):
    """工作日誌那句：「照你在日程.md 改的：記成完成『…』、…」。"""
    name = page_name(rel)
    parts = list(report.get("applied") or [])
    if report.get("carried"):
        parts.append(f"{len(report['carried'])} 段字放進「你加的」")
    if report.get("dropped"):
        parts.append(f"{report['dropped']} 筆不用了")
    return f"照你在{name}改的：" + "、".join(parts or ["沒有要做的"])
