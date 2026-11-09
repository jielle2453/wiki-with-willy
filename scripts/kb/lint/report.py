"""健檢報告（lint 題，D-227、D-066 修訂）：程式照 templates/health-report.md 的固定格式寫 系統/健檢報告/YYYY-MM.md。

段：一句話（照規則跟上個月比）、這個月替你做了什麼、要你決定的（摺疊、標「已放進待處理」，寫的是當時的狀況）、
先放著超過一個月的、建議下一步、數字（給服務人員）。沒內容的段整段不寫；不印編號（句子都是給她看的白話）。
跟工作日誌一樣永久留（D-065），不做手改比對（每月一份、不重寫舊月）。寫前備進那個健檢回合（B-454），退回回得去。
"""
from pathlib import Path

from kb._lib import paths
from kb._lib.io import write_text_atomic

REPORT_DIR = "系統/健檢報告"
SECTION_KEYS = {"這個月替你做了什麼": "done", "要你決定的": "decide", "先放著超過一個月的": "aside", "建議下一步": "suggest",
                "數字（給服務人員）": "numbers"}
FEW = 3  # 要她決定的在幾件以內算「大致正常」


def template():
    return (paths.plugin_root() / "templates" / "health-report.md").read_text(encoding="utf-8")


def summary(decide, done, last, failed=0):
    """一句話：先講整體（沒有要決定的「一切正常」、FEW 件以內「大致正常」、更多講件數；有項目沒查成就不說一切正常，測試 T-16），
    再跟上一份定稿報告比要她決定的件數與替她做了幾件（一樣的不講）；沒有上一份就講這個月做了幾件。"""
    head = "一切正常" if decide == 0 else ("大致正常" if decide <= FEW else f"有 {decide} 件要你決定")
    if failed:
        head = f"有 {failed} 項這次沒查成" if decide == 0 else f"{head}，有 {failed} 項這次沒查成"
    parts = []
    if last and isinstance(last.get("decide"), int):
        diff = decide - last["decide"]
        parts.append("要你決定的事跟上個月一樣多" if diff == 0 else
                     f"要你決定的事比上個月{'多' if diff > 0 else '少'} {abs(diff)} 件")
        if isinstance(last.get("done"), int) and done != last["done"]:
            d = done - last["done"]
            parts.append(f"替你做的事比上個月{'多' if d > 0 else '少'} {abs(d)} 件")
    elif done:
        parts.append(f"這個月替你做了 {done} 件事")
    return head + ("；" + "，".join(parts) if parts else "") + "。"


def text(month, summary, done, decide, aside, suggest, numbers):
    """照範本填；沒內容的段（一句話以外）整段拿掉。"""
    values = {"done": "\n".join(f"- {x}" for x in done), "decide": "\n".join(f"> - {x}" for x in decide),
              "aside": "\n".join(f"- {x}" for x in aside), "suggest": "\n".join(f"- {x}" for x in suggest),
              "numbers": "\n".join(f"- {x}" for x in numbers)}
    raw = template().replace("{month}", month).replace("{summary}", summary).replace("{decide_count}", str(len(decide)))
    head, *sections = raw.split("\n## ")
    keep = [head.rstrip("\n")]
    for sec in sections:
        title = sec.split("\n", 1)[0].strip()
        key = SECTION_KEYS.get(title)
        if key is not None:
            if not values[key]:
                continue
            sec = sec.replace("{" + key + "}", values[key])
        keep.append("## " + sec.rstrip("\n"))
    return "\n\n".join(keep) + "\n"


def _numbers(ctx, got, numbers, ingest, notes, failed):
    """數字段：一行總數，其他各一行；做不了的修、沒查成的項目也寫在這裡。"""
    n_cards = n_syn = 0
    try:
        n_cards, n_syn = len(ctx.cards()), len(ctx.synthesis())
    except Exception:  # noqa: BLE001
        pass
    g = numbers.get("guard") or {}
    v = numbers.get("versions") or {}
    s = numbers.get("sizes") or {}
    head = (f"卡 {n_cards}、綜合卡 {n_syn}、待處理 {got['decide']}（先放著 {got['set_aside']}）、"
            f"守門這週擋 {g.get('denied_week', 0)} 次（上週 {g.get('denied_prev', 0)}）、"
            f"知識庫 {_mb(s.get('vault_bytes', 0))}、舊範本的卡 {v.get('old_template_cards', 0)}、存的 schema 比現在舊 {v.get('old_schema', 0)}")
    lines = [head]
    if g.get("last"):
        lines.append(f"守門最後一次跑是 {g['last']}")
    if s.get("inbox"):
        lines.append(f"暫存區 {s['inbox']} 份")
    links = (numbers.get("citations") or {}).get("broken_links")
    if links:
        lines.append(f"開原檔點不開的卡 {links} 張")
    h = numbers.get("handedits") or {}
    if h.get("index_saved") or h.get("journal_saved"):
        lines.append(f"這個月你改過總目錄 {h.get('index_saved', 0)} 次、工作日誌 {h.get('journal_saved', 0)} 次，你那份都留著")
    left = _ingest_text(ingest)
    if left:
        lines.append("整理還有：" + left)
    asked = _decide_text(ingest)
    if asked:
        lines.append("要你決定的另有：" + asked)
    lines += notes
    lines += [f"{name}這項這次沒查成" for name in failed]
    return lines


def _mb(n):
    return f"{max(1, round(n / (1024 * 1024))) if n else 0} MB"


# 整理做得了的（交 ingest）與要她決定的（已經在待處理）分開講（lint 桌面版確認後）
INGEST_TEXT = (("unsorted", "待整理 {n} 份"), ("unlinked", "沒掛實體的卡 {n} 張"), ("synthesis_stale", "綜合卡待更新 {n} 張"),
               ("duplicates", "同一份檔在兩處 {n} 份"))
DECIDE_TEXT = (("missing", "原檔不見 {n} 份"), ("unreadable", "讀不到 {n} 份"), ("overdue", "日期過了沒銷 {n} 條"),
               ("conflicts", "兩份寫法不同 {n} 件"))


def _ingest_text(ingest):
    return "、".join(t.format(n=ingest[k]) for k, t in INGEST_TEXT if ingest.get(k))


def _decide_text(ingest):
    return "、".join(t.format(n=ingest[k]) for k, t in DECIDE_TEXT if ingest.get(k))


def _aside_lines(ctx):
    """先放著、而且放了超過一個月的（從建立那天算）。"""
    from kb._lib import pending
    out = []
    for i in pending.load(ctx.vault)["items"]:
        if i.get("status") != "open" or not i.get("set_aside"):
            continue
        days = ctx.days_since(i.get("created") or "")
        if days is not None and days > 30:
            out.append(f"{_plain(pending.line_of(i))}（放了 {days} 天）")
    return out


def _plain(line):
    return line.rstrip("。")


def _suggest(numbers, ingest):
    out = []
    b = numbers.get("backup") or {}
    if b.get("suggest"):
        out.append(b["suggest"])
    if ingest and _ingest_text(ingest):
        out.append("整理還有沒做完的，說「整理」就好")
    return out


def write(ctx, month, done, got, numbers, ingest, notes, failed, last_counts):
    """寫 系統/健檢報告/<month>.md（同月重寫）；回相對路徑。"""
    decide = [a["question"] for a in got.get("asks") or []]
    body = text(month, summary(got["decide"], len(done), last_counts, failed=len(failed)), done, decide, _aside_lines(ctx),
                _suggest(numbers, ingest), _numbers(ctx, got, numbers, ingest, notes, failed))
    rel = f"{REPORT_DIR}/{month}.md"
    p = Path(ctx.vault) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if ctx.turn_id:  # 備進健檢回合：退回那個回合時報告跟著拿掉或還原（B-454）
        from kb._lib import turn
        turn.backup(ctx.vault, rel, ctx.turn_id)
    write_text_atomic(p, body)
    return rel
