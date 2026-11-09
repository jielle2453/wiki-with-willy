"""清單 2：綜合卡出現的段是空的（D-037：沒內容的段不寫）→ 那張標成要更新（有空段），整理時改寫。"""
from kb.lint.checks import _synthesis

NAME = "空段"
REASON = "有空段"


def run(ctx):
    hits = []
    for e, rel, text in ctx.synthesis():
        empty = [name for name, lines in _synthesis.body_sections(text) if name and not any(x.strip() for x in lines)]
        if empty:
            hits.append({"path": rel, "name": e["name"], "sections": empty})
    return [_synthesis.fix_finding("empty_sections", "empty_sections", REASON, hits)] if hits else []
