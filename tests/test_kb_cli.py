"""kb.py 的黑箱測試：用 subprocess 跑唯一入口，對照 spec 的介面表。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "scripts" / "kb.py"
MANIFEST = ROOT / ".claude-plugin" / "plugin.json"


def run_kb(*args):
    return subprocess.run(
        [sys.executable, str(KB), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )


def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


class Help(unittest.TestCase):
    def test_help_exits_0_and_lists_version(self):
        r = run_kb("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("version", r.stdout)
        self.assertIn("--json", r.stdout)


class Version(unittest.TestCase):
    def test_version_prints_name_and_version(self):
        r = run_kb("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = manifest()
        self.assertEqual(r.stdout.strip(), f"{m['name']} {m['version']}")

    def test_version_json_matches_manifest(self):
        r = run_kb("version", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        m = manifest()
        self.assertEqual(data, {"name": m["name"], "version": m["version"]})


class Unknown(unittest.TestCase):
    def test_unknown_subcommand_exits_2(self):
        r = run_kb("nosuch")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertNotEqual(r.stderr, "")


if __name__ == "__main__":
    unittest.main()
