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


def opening_status(source):
    """跑 hooks/session.py，回 additionalContext 在 core 全文之前的狀態行（不含隔開的空行）。"""
    tmp = Path(tempfile.mkdtemp(prefix="kbbudget-"))
    try:
        vault = tmp / "vault"
        shutil.copytree(FIXTURE_VAULT, vault)
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


class ResidentTotal(unittest.TestCase):
    def test_resident_total(self):
        parts = [measure_skill(p)["desc_chars"] for p in skill_files()]
        for p in (CORE, CLIENT_CLAUDE_MD):
            if p.exists():
                parts.append(len(read(p)))
        if SESSION_HOOK.exists():
            parts.append(len(opening_status("compact")))
        if not parts:
            self.skipTest("常駐檔尚未建立")
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
