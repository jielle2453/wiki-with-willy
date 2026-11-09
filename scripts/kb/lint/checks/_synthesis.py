"""綜合卡那幾項共用：拆正文的段、把要更新的綜合卡寫進 lint.json（自己修，D-221：整理時改寫）。"""
import re

from kb.lint import runner, state

HEADING = re.compile(r"^##\s+(.+?)\s*$")


def body_sections(text):
    """正文（檔頭之後）的 [(段名或 None, [行])]；None 是第一個 ## 之前的那塊。"""
    lines = str(text).lstrip("﻿").splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                start = i + 1
                break
    out = [(None, [])]
    for line in lines[start:]:
        m = HEADING.match(line)
        if m:
            out.append((m.group(1), []))
        else:
            out[-1][1].append(line)
    return out


def fix_finding(check, key, reason, cards):
    """cards：[{"path","name","ids"?,"sections"?}]。apply 把每張寫進 lint.json 的 synthesis_fix，回「標了 N 張綜合卡要更新（原因）」。"""
    def apply(ctx):
        n = sum(1 for c in cards if state.add_fix(ctx.vault, c["path"], reason, c.get("ids") or [], ctx.turn_id))
        return f"標了 {n} 張綜合卡要更新（{reason}），整理時會改寫" if n else None  # 早就標過的不再記（T-7）
    names = "、".join(f"「{c['name']}」" for c in cards)
    return runner.finding(check, "fix", key, f"{names}的綜合卡要更新（{reason}）", items=[c["name"] for c in cards],
                          data={"cards": cards, "reason": reason}, apply=apply)
