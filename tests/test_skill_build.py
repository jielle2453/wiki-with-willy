"""skills/build 的形狀（build 第 1 點）：只由人打、references 在、steps.md 的節名跟 kb build status 給的 read 對得上。"""
import re
import unittest
from pathlib import Path

from tests._guard_fixture import ROOT
from kb._lib import build  # noqa: E402

SKILL = ROOT / "skills" / "build"


class Shape(unittest.TestCase):
    def test_frontmatter(self):
        front = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1].splitlines()
        self.assertIn("name: build", front)
        self.assertIn("disable-model-invocation: true", front)

    def test_references_exist(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for rel in re.findall(r"`(references/[^`]+\.md)`", text):
            self.assertTrue((SKILL / rel).is_file(), rel)
        self.assertTrue((ROOT / "templates" / "build-report.md").is_file())

    def test_steps_headings_match_status_read(self):
        heads = re.findall(r"^## (.+)$", (SKILL / "references" / "steps.md").read_text(encoding="utf-8"), re.M)
        for step in build.STEPS[:-1]:
            if step != "adopt":
                self.assertIn(build.STEP_TEXT[step], heads, step)

    def test_report_template_has_six_heads(self):
        heads = re.findall(r"^## (.+)$", (ROOT / "templates" / "build-report.md").read_text(encoding="utf-8"), re.M)
        self.assertEqual(tuple(heads), build.REPORT_HEADS)


if __name__ == "__main__":
    unittest.main()
