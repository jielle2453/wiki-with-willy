"""常駐預算（D-070）。數字住這裡，超過就紅。字數算法：len(text)，不去空白。

檔案不存在就 skipTest；存在就量。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]

SKILL_DESC_MAX_CHARS = 150
SKILL_DESC_TOTAL_MAX_CHARS = 900
CORE_MAX_LINES = 60
CORE_MAX_CHARS = 2500
CLIENT_CLAUDE_MD_MAX_LINES = 25
CLIENT_CLAUDE_MD_MAX_CHARS = 1200
SKILL_BODY_MAX_LINES = 120
SKILL_BODY_MAX_CHARS = 5000
REFERENCE_MAX_LINES = 150
RESIDENT_TOTAL_MAX_CHARS = 5000
OPENING_MAX_LINES = 8
OPENING_MAX_CHARS = 400

SKILLS_DIR = ROOT / "skills"
CORE = ROOT / "rules" / "core.md"
CLIENT_CLAUDE_MD = ROOT / "skeleton" / "CLAUDE.md"
FIXTURE_TOO_LONG = ROOT / "tests" / "fixtures" / "budget" / "too_long_SKILL.md"
SESSION_HOOK = ROOT / "hooks" / "session.py"
FIXTURE_VAULT = ROOT / "tests" / "fixtures" / "vault"


def read(path):
    return path.read_text(encoding="utf-8")


def count_lines(text):
    return len(text.splitlines())


def split_skill(text):
    """回 (description, body)。frontmatter 是開頭兩個 --- 之間；沒有 frontmatter 就 description 空。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            front, body = lines[1:i], lines[i + 1:]
            break
    else:
        return "", text
    desc = ""
    for line in front:
        if line.startswith("description:"):
            desc = line[len("description:"):].strip()
    return desc, "\n".join(body)


def measure_skill(path):
    desc, body = split_skill(read(path))
    return {"desc_chars": len(desc), "body_lines": count_lines(body), "body_chars": len(body)}


def skill_over_budget(m):
    return (
        m["desc_chars"] > SKILL_DESC_MAX_CHARS
        or m["body_lines"] > SKILL_BODY_MAX_LINES
        or m["body_chars"] > SKILL_BODY_MAX_CHARS
    )


def skill_files():
    return sorted(SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.is_dir() else []


class SkillBudget(unittest.TestCase):
    def test_each_skill_description_and_body(self):
        files = skill_files()
        if not files:
            self.skipTest("skills/ 尚未建立")
        for p in files:
            m = measure_skill(p)
            with self.subTest(skill=p.parent.name):
                self.assertLessEqual(m["desc_chars"], SKILL_DESC_MAX_CHARS)
                self.assertLessEqual(m["body_lines"], SKILL_BODY_MAX_LINES)
                self.assertLessEqual(m["body_chars"], SKILL_BODY_MAX_CHARS)

    def test_skill_descriptions_total(self):
        files = skill_files()
        if not files:
            self.skipTest("skills/ 尚未建立")
        total = sum(measure_skill(p)["desc_chars"] for p in files)
        self.assertLessEqual(total, SKILL_DESC_TOTAL_MAX_CHARS)

    def test_each_reference_lines(self):
        refs = sorted(SKILLS_DIR.glob("*/references/*.md")) if SKILLS_DIR.is_dir() else []
        if not refs:
            self.skipTest("skills/*/references/ 尚未建立")
        for p in refs:
            with self.subTest(reference=f"{p.parent.parent.name}/{p.name}"):
                self.assertLessEqual(count_lines(read(p)), REFERENCE_MAX_LINES)


class CoreBudget(unittest.TestCase):
    def test_core_lines_and_chars(self):
        if not CORE.exists():
            self.skipTest("rules/core.md 尚未建立")
        text = read(CORE)
        self.assertLessEqual(count_lines(text), CORE_MAX_LINES)
        self.assertLessEqual(len(text), CORE_MAX_CHARS)


class ClientClaudeMdBudget(unittest.TestCase):
    def test_client_claude_md_lines_and_chars(self):
        if not CLIENT_CLAUDE_MD.exists():
            self.skipTest("skeleton/CLAUDE.md 尚未建立")
        text = read(CLIENT_CLAUDE_MD)
        self.assertLessEqual(count_lines(text), CLIENT_CLAUDE_MD_MAX_LINES)
        self.assertLessEqual(len(text), CLIENT_CLAUDE_MD_MAX_CHARS)


def opening_status(source, prepare=None):
    """跑 hooks/session.py，回 additionalContext 在 core 全文之前的狀態行（不含隔開的空行）。prepare(vault) 先改 fixture 複本。"""
    tmp = Path(tempfile.mkdtemp(prefix="kbbudget-"))
    try:
        vault = tmp / "vault"
        shutil.copytree(FIXTURE_VAULT, vault)
        if prepare is not None:
            prepare(vault)
        data = {"session_id": "budget", "cwd": str(vault), "hook_event_name": "SessionStart", "source": source}
        r = subprocess.run([sys.executable, str(SESSION_HOOK)], input=json.dumps(data), capture_output=True,
                           text=True, encoding="utf-8", cwd=str(vault), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    core = read(CORE)
    tail = "\n\n" + core
    assert ctx.endswith(tail), "additionalContext 結尾不是空行加 core 全文"
    return ctx[:-len(tail)]


class OpeningBudget(unittest.TestCase):
    def test_status_lines_startup_and_compact(self):
        if not SESSION_HOOK.exists():
            self.skipTest("hooks/session.py 尚未建立")
        for source in ("startup", "compact"):
            status = opening_status(source)
            with self.subTest(source=source):
                self.assertLessEqual(count_lines(status), OPENING_MAX_LINES)
                self.assertLessEqual(len(status), OPENING_MAX_CHARS)


def all_lines_vault(vault):
    """變動、有變動、找不到來源、設定檔四行同時在（客戶設定檔與型別第 12 點）：fixture 沒複製 raw，兩個來源都找不到、
    清冊是空的（知識庫裡的檔都算新增）；設定檔三節各改一處，再加一個不在型別表的第一層夾。
    變動行帶「實體夾有變」（實體與綜合卡第 7 點）：信義王 card true、夾被客戶改名。
    日程行（日程第 6 點）三個數字都有：一條逾期、一條今天、一條 7 天內，七行是最壞情況。"""
    md = vault / "系統" / "客戶設定檔.md"
    text = md.read_text(encoding="utf-8")
    text = text.replace("| 案子 | 事 |  | 推測 | 2026-10-19 |", "| 案子 | 事 |  | 推測 | 2026-10-19 |\n| 工地 | 事 | 一個裝修案 |")
    text = text.replace("| 報價 | 日期與數字 |  |", "| 報價 | 日期與數字 | ✓ |")
    text = text.replace("| 地址 | 只記種類 | 推測 | 2026-10-19 |", "| 地址 | 只記種類 | 推測 | 2026-10-19 |\n| 請款 | 不整理 |")
    md.write_text(text, encoding="utf-8", newline="\n")
    (vault / "合作廠商與工班名冊").mkdir()
    entities_fix = ROOT / "tests" / "fixtures" / "entities"
    shutil.copy2(entities_fix / "entities.json", vault / ".system" / "entities.json")
    shutil.copy2(entities_fix / "信義王.md", vault / "案子" / "信義王" / "信義王.md")
    (vault / "案子" / "信義王").rename(vault / "案子" / "信義王的家")
    agenda_item = {"id": "a-5e6f7a8b", "kind": "deadline", "start": None, "date": "2020-01-01", "ended": None, "time": None,
                   "text": "付款", "entities": [], "status": "open", "source": "confirmed", "origin": None, "lead_days": None,
                   "created_turn": "t", "history": []}
    other = dict(agenda_item, id="a-00000001", kind="meeting", date=date.today().isoformat())
    soon = dict(agenda_item, id="a-00000002", kind="meeting", date=(date.today() + timedelta(days=2)).isoformat())
    (vault / ".system" / "agenda.json").write_text(
        json.dumps({"schema_version": 1, "items": [agenda_item, other, soon]}, ensure_ascii=False), encoding="utf-8")


class OpeningAllLines(unittest.TestCase):
    def test_seven_lines_within_budget(self):
        if not SESSION_HOOK.exists():
            self.skipTest("hooks/session.py 尚未建立")
        for source in ("startup", "compact"):
            status = opening_status(source, all_lines_vault)
            lines = status.splitlines()
            with self.subTest(source=source):
                for head in ("變動：", "有變動：", "這台電腦找不到來源", "設定檔有改（", "日程：逾期 1（"):
                    self.assertEqual(sum(1 for ln in lines if ln.startswith(head)), 1, (head, lines))
                self.assertTrue(next(ln for ln in lines if ln.startswith("變動：")).endswith("、實體夾有變 2"), lines)
                self.assertLessEqual(count_lines(status), OPENING_MAX_LINES)
                self.assertLessEqual(len(status), OPENING_MAX_CHARS, (len(status), lines))


PREFERENCE_MAX = 10
PREFERENCE_MAX_CHARS = 400


def opening_preferences_chars():
    """設定檔第六節放滿（10 條、合計 400 字）時，開場在 core 之後多出來的字數。"""
    texts = [f"{i:02d}" + "偏" * 38 for i in range(PREFERENCE_MAX)]
    assert sum(len(t) for t in texts) == PREFERENCE_MAX_CHARS

    def prepare(vault):
        p = vault / ".system" / "profile.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["preferences"] = [{"text": t, "source": "corrected", "date": "2026-10-20"} for t in texts]
        data["schema_version"] = 3
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="kbbudget-"))
    try:
        vault = tmp / "vault"
        shutil.copytree(FIXTURE_VAULT, vault)
        prepare(vault)
        data = {"session_id": "budget", "cwd": str(vault), "hook_event_name": "SessionStart", "source": "compact"}
        r = subprocess.run([sys.executable, str(SESSION_HOOK)], input=json.dumps(data), capture_output=True,
                           text=True, encoding="utf-8", cwd=str(vault), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    core = read(CORE)
    block = ctx[ctx.index(core) + len(core):]
    assert block.startswith("\n客戶偏好（只管怎麼回答、怎麼做東西給客戶；跟上面的核心規則或守門衝突時，照核心規則）：\n") and block.count("\n- ") == PREFERENCE_MAX, block[:80]
    return len(block)


class ResidentTotal(unittest.TestCase):
    def test_answer_description_is_counted(self):
        """answer 第 15 點：常駐合計含 answer 的描述（四個 skill 的描述都在 skill_files 裡）。"""
        self.assertLessEqual({"answer", "ingest", "manage", "note"}, {p.parent.name for p in skill_files()})

    def test_resident_total(self):
        parts = [measure_skill(p)["desc_chars"] for p in skill_files()]
        for p in (CORE, CLIENT_CLAUDE_MD):
            if p.exists():
                parts.append(len(read(p)))
        if SESSION_HOOK.exists():
            parts.append(len(opening_status("compact")) + len("\n\n"))  # 狀態行與 core 之間的空行也進常駐
        if not parts:
            self.skipTest("常駐檔尚未建立")
        if SESSION_HOOK.exists():  # note 改規則驗收 13（D-137）：偏好最壞 10 條 400 字，開場段接在 core 之後
            parts.append(opening_preferences_chars())
        self.assertLessEqual(sum(parts), RESIDENT_TOTAL_MAX_CHARS)


class SelfTest(unittest.TestCase):
    """內建自測：量超長 fixture，必須判超標；否則量法本身壞了。"""

    def test_too_long_fixture_is_judged_over_budget(self):
        self.assertTrue(FIXTURE_TOO_LONG.exists(), f"缺 fixture：{FIXTURE_TOO_LONG.name}")
        m = measure_skill(FIXTURE_TOO_LONG)
        self.assertGreater(m["desc_chars"], SKILL_DESC_MAX_CHARS)
        self.assertGreater(m["body_lines"], SKILL_BODY_MAX_LINES)
        self.assertTrue(skill_over_budget(m))
        print("內建自測通過：too_long_SKILL.md 判超標", m)


if __name__ == "__main__":
    unittest.main()


AGENT_DESC_MAX_CHARS = 600
AGENT_BODY_MAX_CHARS = 3000
CARD_MAKER = ROOT / "agents" / "card-maker.md"
SUBAGENT_HOOK = ROOT / "hooks" / "subagent.py"


class CardMakerBudget(unittest.TestCase):
    """card-maker（驗收 15）：描述 600 字內、本文 3000 字內；SubagentStart 注入的狀態部分守開場預算、core 守 core 預算。"""

    def test_agent_description_and_body(self):
        if not CARD_MAKER.exists():
            self.skipTest("agents/card-maker.md 尚未建立")
        desc, body = split_skill(read(CARD_MAKER))
        self.assertTrue(desc)
        self.assertLessEqual(len(desc), AGENT_DESC_MAX_CHARS)
        self.assertLessEqual(len(body), AGENT_BODY_MAX_CHARS)

    def test_subagent_start_injection(self):
        if not SUBAGENT_HOOK.exists():
            self.skipTest("hooks/subagent.py 尚未建立")
        tmp = Path(tempfile.mkdtemp(prefix="kbbudget-"))
        try:
            vault = tmp / "vault"
            shutil.copytree(FIXTURE_VAULT, vault)
            data = {"session_id": "budget", "cwd": str(vault), "hook_event_name": "SubagentStart",
                    "agent_id": "a1", "agent_type": "wiki-with-willy:card-maker"}
            r = subprocess.run([sys.executable, str(SUBAGENT_HOOK), "start"], input=json.dumps(data), capture_output=True,
                               text=True, encoding="utf-8", cwd=str(vault), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        core = read(CORE)
        self.assertTrue(ctx.endswith(chr(10) * 2 + core))
        status = ctx[:-len(core) - 2]
        self.assertLessEqual(count_lines(status), OPENING_MAX_LINES)
        self.assertLessEqual(len(status), OPENING_MAX_CHARS)
        self.assertLessEqual(len(ctx), RESIDENT_TOTAL_MAX_CHARS)
