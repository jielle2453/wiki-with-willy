"""第三類（D-146、D-154）：口述、推算封存。被取代或收回、而且沒有任何綜合卡引用（「整理到」或正文的〔…〕）的，
移到封存檔（statements-archive.json、findings-archive.json），自己做。lookup 與綜合卡依據本來就只用有效的。"""
import re

from kb._lib import findings, statements
from kb.lint import runner

NAME = "口述與推算封存"
_ID = re.compile(r"\b[sc]-[0-9a-f]{8}\b")


def _cited(ctx):
    out = set()
    for _e, _rel, text in ctx.synthesis():
        out.update(_ID.findall(text))
    return out


def run(ctx):
    cited = _cited(ctx)
    s_ids = [s["id"] for s in ctx.statements()["statements"]
             if s.get("status") in ("superseded", "withdrawn") and s.get("id") not in cited]
    c_ids = [c["id"] for c in ctx.findings()["findings"]
             if c.get("status") in ("superseded", "withdrawn") and c.get("id") not in cited]
    if not s_ids and not c_ids:
        return []

    def apply(ctx):
        parts = []
        n = statements.archive(ctx.vault, s_ids, ctx.turn_id) if s_ids else 0
        if n:
            parts.append(f"收起 {n} 條被取代或收回的口述")
        n = findings.archive(ctx.vault, c_ids, ctx.turn_id) if c_ids else 0
        if n:
            parts.append(f"收起 {n} 筆被取代或收回的推算")
        ctx.forget("statements", "statements_archive", "findings", "findings_archive")
        return "、".join(parts) or None
    return [runner.finding("archive", "fix", "archive", "", data={"statements": s_ids, "findings": c_ids}, apply=apply)]
