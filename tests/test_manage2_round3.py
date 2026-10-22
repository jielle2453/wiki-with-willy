"""manage 第二版第二輪修（測試報告「第二輪」一節，設計 2026-10-22 定的五條）：每條一組測試。"""
import contextlib
import io
import json
import os
import threading
import time
from pathlib import Path
from unittest import mock

from tests import _cards
from tests.test_lib_source_edit import Base
from kb import cli, folders
from kb._lib import cards, entities, ledger, plan, purge, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import source_edit as se  # noqa: E402

CURLY = ("\u2018", "\u2019", "\u201a", "\u201b", "\u201c", "\u201d", "\u201e", "\u201f")


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


# ---------- 第 1 條：彎引號 ----------

class CurlyQuotes(Base):
    def test_both_shell_args_refuse_curly_quotes(self):
        for ch in CURLY:
            with self.subTest(ch=hex(ord(ch))):
                text = f"x{ch}; Write-Output PWNED; {ch}y"
                self.assertIsNone(plan.shell_arg(text))
                self.assertIsNone(se.shell_arg(text))
        self.assertEqual(plan.shell_arg("一般"), "一般")
        self.assertEqual(se.shell_arg("C:/a b"), '"C:/a b"')

    def test_purge_step_note_for_curly(self):
        steps = plan._purge_steps({"purge_pending": [{"name": "客戶案件/x\u201d; y", "synthesis": []}]})
        self.assertIsNone(steps[0]["do"])
        self.assertIn("改夾名", steps[0]["note"])


# ---------- 第 2 條：遮罩不看左邊界 ----------

class MaskLeft(Base):
    def test_paths_after_any_prefix(self):
        root = str(self.raw / "客戶案件" / "私人")
        mask = purge.masker(self.V, "客戶案件", "私人", root)
        for before in ("已搬好：", "note:", "path=", "[", "→", "絕對：", "x"):
            with self.subTest(before=before):
                self.assertEqual(mask(f"{before}客戶案件/私人/日記本.txt"), f"{before}（已排除）")
        self.assertEqual(mask(f"絕對：{root}\\日記本.txt"), "絕對：（已排除）")
        self.assertEqual(mask("檔案索引/客戶案件/私人/a-1.md"), "（已排除）")
        self.assertEqual(mask("客戶案件/私人2/x"), "客戶案件/私人2/x")  # 右邊界照舊
        self.assertEqual(mask("提到 日記本.txt 而已"), "提到 日記本.txt 而已")  # 單段的檔名不換


# ---------- 第 3 條：refresh_synthesis 收進倒回；rollback 與 restore_sources 各自 try ----------

class AfterRename3(Base):
    def test_refresh_synthesis_failure_rolls_back(self):
        before = self.snapshot()
        p = self.plan(self.fid, dest=str(self.raw / "客戶案件" / "大安陳"))
        with mock.patch.object(cards, "refresh_synthesis", side_effect=OSError(13, "locked")):
            msg = self.refuse(se.do_move, self.V, p, self.t, "move")
        self.assertTrue(msg.startswith("沒有動："), msg)
        self.assertEqual(self.snapshot(), before)

    def test_restore_sources_even_if_rollback_fails(self):
        lee = self.raw / "客戶案件" / "內湖李"
        (lee / "私人").mkdir()
        sp = Path(self.V) / ".system/sources.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "內湖李/私人", "kind": "hidden", "reason": "", "added": "2026-10-22"})
        sp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        original = sp.read_text(encoding="utf-8")
        p = self.plan(str(lee), dest=str(self.raw / "報價與合約"))
        with mock.patch.object(cards, "sync", side_effect=PermissionError(13, "拒絕存取")), \
                mock.patch.object(entities._Undo, "rollback", side_effect=OSError(13, "rollback broke")):
            self.refuse(se.do_move, self.V, p, self.t, "move")
        self.assertTrue((lee / "私人").is_dir())
        self.assertEqual(json.loads(sp.read_text(encoding="utf-8")), json.loads(original))


# ---------- 第 4 條：kb folders find ----------

class Find(Base):
    def test_finds_under_roots_skips_excluded_and_credential(self):
        (self.raw / "客戶案件" / "私人").mkdir()
        (self.raw / "報價與合約" / "信義王" / "私人").mkdir()
        (self.raw / "公司行政" / "密碼" / "私人").mkdir(parents=True)  # 憑證樣式的夾不進去
        (Path(self.V) / "案子" / "信義王" / "私人").mkdir(parents=True, exist_ok=True)
        sp = Path(self.V) / ".system/sources.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
        data["sources"][2]["excludes"].append({"spec": "舊檔", "kind": "unused", "reason": "", "added": "2026-10-22"})
        sp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (self.raw / "公司行政" / "舊檔" / "私人").mkdir()
        code, out = kb("folders", "find", "私人", "--json", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        got = json.loads(out)
        self.assertEqual(sorted(got["found"]), ["報價與合約/信義王/私人", "客戶案件/私人", "知識庫/案子/信義王/私人"])
        code, out = kb("folders", "find", "私人", "--vault", str(self.V))
        self.assertEqual(out.splitlines()[0], "找到 3 個叫「私人」的夾：")
        self.assertIn("- 客戶案件/私人", out)
        code, out = kb("folders", "find", "沒有這個夾", "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (0, "登記的資料夾裡沒有叫「沒有這個夾」的夾"))

    def test_walk_limit(self):
        (self.raw / "客戶案件" / "私人").mkdir()
        from kb._lib import config
        real = config.get
        with mock.patch.object(config, "get", side_effect=lambda v, k: 2 if k == "credential_scan_max_entries" else real(v, k)):
            code, out = kb("folders", "find", "私人", "--json", "--vault", str(self.V))
        self.assertTrue(json.loads(out)["truncated"])


# ---------- 第 5 條：其他 ----------

class Others(Base):
    def test_purge_manifest_written_even_if_row_fails(self):
        """F4：中途 OSError 也記 remaining；manifest 已刪的卡那行照樣標 purged，重跑不會說清好了卻留著路徑。"""
        private = self.raw / "客戶案件" / "私人"
        private.mkdir()
        (private / "日記.txt").write_text("秘密", encoding="utf-8")
        _cards.scan_apply(self.V)
        from tests import _entities
        rel = _entities.write_card(self.V, "客戶案件/私人/日記.txt", "王家心情")
        cards.render_index(self.V)
        t = turn.start(self.V, None, "")["turn"]
        turn.backup(self.V, rel, t)
        turn.backup(self.V, cards.INDEX_POSIX, t)
        real = purge.write_text_atomic

        def flaky(path, text):
            if Path(path).name == cards.INDEX_POSIX:
                raise OSError(13, "locked")
            return real(path, text)
        with mock.patch.object(purge, "write_text_atomic", side_effect=flaky):
            code, out = kb("folders", "exclude", str(private), "--kind", "hidden", "--vault", str(self.V))
        self.assertIn("還沒清完", out)
        rows = [json.loads(ln) for ln in (Path(self.V) / ".system/versions" / t / "manifest.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["action"] == "purged" for r in rows), rows)
        self.assertFalse(any("私人" in r["path"] for r in rows), rows)

    def test_ledger_append_waits_for_scrub_lock(self):
        lp = Path(self.V) / ledger.LEDGER_REL
        ledger.append(self.V, {"tool": "Read", "decision": "deny", "zone": "Z9", "reason_code": "x", "head": "a"})
        mask = purge.masker(self.V, "客戶案件", "私人", "/nowhere")
        real_read = Path.read_text
        appended = threading.Event()

        def slow_read(p, *a, **k):
            text = real_read(p, *a, **k)
            if p.name == "ledger.tsv":
                th = threading.Thread(target=lambda: (ledger.append(self.V, {"tool": "Read", "decision": "deny",
                                                                             "zone": "Z9", "reason_code": "y",
                                                                             "head": "後來追加的"}), appended.set()))
                th.start()
                time.sleep(0.3)  # 追加的那邊要等鎖，還沒寫進去
                self.assertFalse(appended.is_set())
            return text
        with mock.patch.object(Path, "read_text", slow_read):
            purge._scrub_text(self.V, ledger.LEDGER_REL, mask)
        appended.wait(5)
        self.assertIn("後來追加的", lp.read_text(encoding="utf-8"))

    def test_version_junction_skipped(self):
        outside = self.tmp / "外面的回合"
        (outside / "files").mkdir(parents=True)
        link = Path(self.V) / ".system/versions" / "zz-junction"
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import subprocess
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if r.returncode != 0:
                self.skipTest("建不了 junction")
        else:
            os.symlink(outside, link)
        self.assertNotIn(link.name, [d.name for d in purge._version_dirs(self.V)])

    def test_plan_cmd_unsafe_draft_does_not_crash(self):
        drafts = Path(self.V) / "AI 產出"
        (drafts / "Joe's $500 quote（草稿）.md").write_text("草稿", encoding="utf-8")
        asks = plan._draft_asks(self.V)
        bad = next(a for a in asks if "Joe" in a["id"])
        drop = next(r for r in bad["results"] if r["label"] == "不要了")
        self.assertEqual(drop["do"], [])
        self.assertIn("改名", drop["note"])
        self.assertIsNone(plan.cmd("output", "drop-draft", "a'$b"))

    def test_device_names(self):
        text = "這種名字 Windows 不收，換個名字"
        for name in ("NUL", "con", "COM1", "aux.pdf", "LPT9.txt", "prn"):
            with self.subTest(name=name):
                self.assertEqual(self.refuse(self.plan, self.fid, new_name=name), text)
        self.assertEqual(self.plan(self.fid, new_name="console")["new_name"], "console.pdf")
