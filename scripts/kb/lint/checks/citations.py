"""清單 1：引用對不上（原 5、14、15）。綜合卡內文的〔f-…〕〔s-…〕〔r-…〕〔c-…〕指到不存在的、已移除的、不採用的，
或檔案卡「開原檔」連結打不開的卡 → 那張綜合卡標成要更新（出處點不開），整理時改寫。
「紀事」段只增不改（S-…、synthesis.BASE_HINTS），那裡引用已收回、不採用、取代掉的照舊合理，只有完全不存在的才算；
封存檔裡的口述、推算算存在。另數開原檔點不開的卡幾張（數字）。"""
import os
import re
from urllib.parse import unquote

from kb._lib import cards as cards_lib
from kb.lint import runner
from kb.lint.checks import _synthesis

NAME = "出處"
REASON = "出處點不開"
HISTORY = "紀事"
_BRACKET = re.compile(r"〔([^〕]*)〕")
_ID = re.compile(r"\b[fsrc]-[0-9a-f]{8}\b")


def _open_broken(front):
    """開原檔是 file:/// 而那個檔不在：回真（網址、沒有這欄的不算）。"""
    link = front.get("開原檔")
    if not isinstance(link, str) or not link.startswith("file:///"):
        return False
    path = unquote(link[len("file:///"):])
    if path.replace("\\", "/").startswith("//"):
        return False  # 網路上的位置（含混用斜線的寫法）不去查：查了會對外連線，當沒查（T-30）
    return not os.path.exists(path)


def _known(ctx):
    cards = ctx.cards()
    ruled_out = cards_lib.ruled_out_ids(ctx.rulings())
    stmts = {s["id"]: s for s in ctx.statements()["statements"] if s.get("id")}
    stmts.update({s["id"]: s for s in ctx.statements_archive()["statements"] if s.get("id") and s["id"] not in stmts})
    finds = {c["id"]: c for c in ctx.findings()["findings"] if c.get("id")}
    finds.update({c["id"]: c for c in ctx.findings_archive()["findings"] if c.get("id") and c["id"] not in finds})
    ruls = {r["id"]: r for r in ctx.rulings()["rulings"] if r.get("id")}
    broken = {cid for cid, c in cards.items() if _open_broken(c["front"])}
    return cards, ruled_out, stmts, finds, ruls, broken


def _problem(cid, history, known):
    cards, ruled_out, stmts, finds, ruls, broken = known
    if cid.startswith("f-"):
        return cid not in cards or cid in broken or (not history and cid in ruled_out)
    if cid.startswith("s-"):
        s = stmts.get(cid)
        return s is None or (not history and s.get("status") == "withdrawn")
    if cid.startswith("c-"):
        c = finds.get(cid)
        return c is None or (not history and c.get("status") == "withdrawn")
    r = ruls.get(cid)
    return r is None or (not history and r.get("status") != "active")


def run(ctx):
    known = _known(ctx)
    hits = []
    for e, rel, text in ctx.synthesis():
        bad = set()
        for section, lines in _synthesis.body_sections(text):
            for line in lines:
                for inner in _BRACKET.findall(line):
                    for cid in _ID.findall(inner):
                        if _problem(cid, section == HISTORY, known):
                            bad.add(cid)
        if bad:
            hits.append({"path": rel, "name": e["name"], "ids": sorted(bad)})
    out = [_synthesis.fix_finding("citations", "citations", REASON, hits)] if hits else []
    if known[5]:
        out.append(runner.finding("citations", "number", "citations", "", data={"broken_links": len(known[5])}))
    return out
