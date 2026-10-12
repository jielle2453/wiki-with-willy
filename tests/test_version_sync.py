"""plugin.json 的 version 必須等於 CHANGELOG 最新一段的版號（conventions 三）。"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — ", re.MULTILINE)


class VersionSync(unittest.TestCase):
    def test_manifest_version_equals_latest_changelog_section(self):
        with open(ROOT / ".claude-plugin" / "plugin.json", encoding="utf-8") as f:
            manifest_version = json.load(f)["version"]
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        m = HEADING.search(text)
        self.assertIsNotNone(m, "CHANGELOG.md 找不到 `## X.Y.Z — ` 段落")
        self.assertEqual(manifest_version, m.group(1))


if __name__ == "__main__":
    unittest.main()
