"""守門語料（密碼與守門第 10 點、驗收 1；D-186：以前的繞過全部留著）：tests/fixtures/guard_corpus.json 每一筆照跑 guard.decide。

每組 needs 建一份知識庫（fixtures 的 vault 與 raw 複製到 tmp，再建 needs 列的檔、連結、壞檔），佔位換成實際路徑。
decide 丟例外時照 hooks/guard.py：在知識庫裡算 deny（D-186 第 4 點），外面算 pass。
直接跑這支（python -m tests.test_guard_corpus）會印每一筆對不上的，給整理語料用。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._guard_fixture import FIXTURES, ROOT
from kb import guard  # noqa: E402

CORPUS = FIXTURES / "guard_corpus.json"
BROKEN_SOURCES = "sources.json broken"


def _fwd(p):
    return str(p).replace("\\", "/")


class Env:
    """一組 needs 的知識庫。"""

    def __init__(self, needs):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbcorpus-"))
        self.out = Path(tempfile.mkdtemp(prefix="kbcorpus-out-"))
        shutil.copytree(FIXTURES / "vault", self.tmp / "vault")
        shutil.copytree(FIXTURES / "raw", self.tmp / "raw")
        self.V, self.RAW = self.tmp / "vault", self.tmp / "raw"
        self.ok = True
        for need in needs:
            self._make(need)

    def sub(self, text):
        v, raw, tmp, out = (_fwd(os.path.realpath(p)) for p in (self.V, self.RAW, self.tmp, self.out))
        home = _fwd(os.path.realpath(os.path.expanduser("~")))
        text = str(text)
        text = text.replace("<V去磁碟代號>", v[2:] if re.match(r"^[A-Za-z]:", v) else v)
        text = text.replace("<V相對家目錄>", os.path.relpath(v, home).replace("\\", "/") if v.lower().startswith(home.lower()) else v)
        for ph, val in (("<V>", v), ("<RAW>", raw), ("<TMP>", tmp), ("<OUT>", out), ("<PLUGIN>", _fwd(ROOT))):
            for prefix in ("/c", "//localhost/c$"):  # Git Bash 與本機管理共享：/c<RAW> → /c/Users/…
                text = text.replace(prefix + ph, prefix + (val[2:] if re.match(r"^[A-Za-z]:", val) else val))
            text = text.replace(ph, val)
        return text

    def _make(self, need):
        if need.startswith("junction:"):
            link, target = (self.sub(x.strip()) for x in need[len("junction:"):].split("->"))
            if os.name != "nt":
                self.ok = False
                return
            Path(link).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cmd", "/c", "mklink", "/J", link.replace("/", "\\"), target.replace("/", "\\")],
                           capture_output=True, check=True)
            return
        if "sources.json" in need and ("overwritten" in need or "broken" in need):
            (self.V / ".system" / "sources.json").write_text("{ 這不是 JSON", encoding="utf-8")
            return
        path = Path(self.sub(need))
        if need.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("x\n", encoding="utf-8")

    def close(self):
        if os.name == "nt":  # 先拆連結，rmtree 才不會跟進去刪到目標
            for p in sorted(self.tmp.rglob("*"), key=lambda q: -len(q.parts)):
                try:
                    if p.is_junction() if hasattr(p, "is_junction") else os.path.islink(p):
                        os.rmdir(p)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)


def decide(env, case):
    tool_input = {k: env.sub(v) if isinstance(v, str) else v for k, v in case["input"].items()}
    cwd = env.sub(case.get("cwd") or "<V>")
    payload = {"tool_name": case["tool"], "tool_input": tool_input, "cwd": cwd, "permission_mode": "default"}
    try:
        return guard.decide(payload).decision
    except Exception:  # noqa: BLE001  照 hooks/guard.py：知識庫裡出錯一律擋
        return "deny" if guard.find_vault(cwd) is not None else "pass"


def run_all():
    """回 [(case, got)] 對不上的。"""
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    groups = {}
    for case in cases:
        groups.setdefault(tuple(sorted(case.get("needs") or [])), []).append(case)
    bad = []
    for needs, items in groups.items():
        env = Env(needs)
        try:
            for case in items:
                if not env.ok:
                    continue
                got = decide(env, case)
                if got != case["expect"]:
                    bad.append((case, got))
        finally:
            env.close()
    return bad


class GuardCorpus(unittest.TestCase):
    def test_corpus_has_sources(self):
        cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
        self.assertGreater(len(cases), 400)
        for case in cases:
            with self.subTest(case=case):
                self.assertIn(case["expect"], ("deny", "allow", "pass"))
                self.assertTrue(case.get("來源"))

    def test_every_case(self):
        bad = run_all()
        lines = [f"{c['來源']}｜{c['tool']} {json.dumps(c['input'], ensure_ascii=False)[:160]}｜要 {c['expect']} 得 {got}"
                 for c, got in bad]
        self.assertEqual(lines, [], "\n".join(lines))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for c, got in run_all():
        print(json.dumps({"got": got, **c}, ensure_ascii=False))
