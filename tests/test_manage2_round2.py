"""manage 第二版第一輪修（測試報告 reports/manage第二版-測試.md，設計 2026-10-22 定的十條）：每條一組測試。"""
import contextlib
import io
import json
import os
import stat
import shutil
from pathlib import Path
from unittest import mock

from tests import _cards, _entities
from tests.test_lib_source_edit import ESTIMATE, Base, entry, inv
from kb import cli, folders, guard, versions
from kb._lib import cards, ledger, plan, purge, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import source_edit as se  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


def moves_rows(V, t):
    return turn.moves(V, t)


# ---------- 第 3 條：os.rename 之後任何一步失敗 ----------

class AfterRename(Base):
    def test_failure_after_rename_rolls_back(self):
        before = self.snapshot()
        dest = str(self.raw / "客戶案件" / "大安陳")
        p = self.plan(self.fid, dest=dest)
        with mock.patch.object(cards, "sync", side_effect=PermissionError(13, "拒絕存取")):
            msg = self.refuse(se.do_move, self.V, p, self.t, "move")
        self.assertTrue(msg.startswith("沒有動："), msg)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(moves_rows(self.V, self.t), [])

    def test_rollback_fails_tells_truth(self):
        dest = str(self.raw / "客戶案件" / "大安陳")
        p = self.plan(self.fid, dest=dest)
        real_rename = se._rename
        calls = []

        def rename(a, b):
            calls.append((a, b))
            if len(calls) > 1:  # 倒回去那一下也失敗
                raise PermissionError(13, "in use")
            real_rename(a, b)
        with mock.patch.object(cards, "sync", side_effect=PermissionError(13, "拒絕存取")), \
                mock.patch.object(se, "_rename", side_effect=rename):
            msg = self.refuse(se.do_move, self.V, p, self.t, "move")
        self.assertEqual(msg, "已搬好，但後面沒做完；說「退回」可以退回")
        self.assertTrue((Path(dest) / "估價單.pdf").is_file())
        self.assertEqual(len(moves_rows(self.V, self.t)), 1)  # 退回才搬得回去

    def hidden_folder(self):
        lee = self.raw / "客戶案件" / "內湖李"
        (lee / "私人").mkdir()
        (lee / "私人" / "日記.txt").write_text("秘密", encoding="utf-8")
        sp = Path(self.V) / ".system/sources.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "內湖李/私人", "kind": "hidden", "reason": "", "added": "2026-10-22"})
        sp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return lee

    def denied(self, path):
        d = guard.decide({"tool_name": "Read", "tool_input": {"file_path": str(path)}, "cwd": str(self.V)})
        return d.decision == "deny"

    def test_hidden_excludes_rewritten_before_rename(self):
        lee = self.hidden_folder()
        dest = self.raw / "報價與合約"
        seen = []
        real_rename = se._rename

        def rename(a, b):  # 改名那一刻兩邊都要擋得住
            seen.append((self.denied(Path(a) / "私人" / "日記.txt"), self.denied(Path(b) / "私人" / "日記.txt")))
            real_rename(a, b)
        with mock.patch.object(se, "_rename", side_effect=rename):
            self.move(str(lee), str(dest))
        self.assertEqual(seen, [(True, True)])
        self.assertTrue(self.denied(dest / "內湖李" / "私人" / "日記.txt"))

    def test_sources_write_fails_nothing_moves(self):
        lee = self.hidden_folder()
        before = self.snapshot()
        sp = (Path(self.V) / ".system/sources.json").read_text(encoding="utf-8")
        p = self.plan(str(lee), dest=str(self.raw / "報價與合約"))
        with mock.patch.object(se, "write_json_atomic", side_effect=PermissionError(13, "拒絕存取")):
            msg = self.refuse(se.do_move, self.V, p, self.t, "move")
        self.assertTrue(msg.startswith("沒有動："), msg)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"), sp)

    def existing_target(self):
        dest = self.raw / "客戶案件" / "大安陳"
        (dest / "估價單.pdf").write_bytes(b"old one")
        _cards.scan_apply(self.V)
        return dest

    def test_replace_checks_rename_before_trashing(self):
        dest = self.existing_target()
        trashed = []
        with mock.patch.object(se, "shell_trash", side_effect=lambda p: trashed.append(p)), \
                mock.patch.object(se, "recycle_room", return_value=(0, 10 ** 9)), \
                mock.patch.object(se, "_rename", side_effect=PermissionError(13, "in use")):
            msg = self.refuse(se.do_move, self.V, self.plan(self.fid, dest=str(dest)), self.t, "move", replace=True)
        self.assertEqual(msg, "有檔開著（估價單.pdf），關掉再試")
        self.assertEqual(trashed, [])
        self.assertEqual((dest / "估價單.pdf").read_bytes(), b"old one")

    def test_replace_final_rename_fails_says_old_in_bin(self):
        dest = self.existing_target()
        real_rename = se._rename
        calls = []

        def rename(a, b):
            calls.append(b)
            if Path(b).name == "估價單.pdf" and Path(b).parent == dest:  # 換到目標名字那一下失敗
                raise PermissionError(13, "in use")
            real_rename(a, b)
        with mock.patch.object(se, "shell_trash", side_effect=os.remove), \
                mock.patch.object(se, "recycle_room", return_value=(0, 10 ** 9)), \
                mock.patch.object(se, "_rename", side_effect=rename):
            msg = self.refuse(se.do_move, self.V, self.plan(self.fid, dest=str(dest)), self.t, "move", replace=True)
        self.assertIn("原本的「估價單.pdf」已經丟進資源回收筒", msg)
        self.assertTrue((self.raw / ESTIMATE).is_file())  # 這份放回原位了


# ---------- 第 4 條：purge 的範圍先記下來 ----------

class PurgeScope(Base):
    def setUp(self):
        super().setUp()
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / "日記.txt").write_text("秘密", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.pid = _cards.entry_of(self.V, "客戶案件/私人/日記.txt")["id"]
        temp = Path(self.V) / ".system/temp"
        temp.mkdir(parents=True, exist_ok=True)
        (temp / f"{self.pid}.md").write_text("秘密", encoding="utf-8")
        (temp / f"{self.pid}_p1.png").write_bytes(b"png")

    def row(self):
        data = json.loads((Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"))
        return data["sources"][0]["excludes"][-1]

    def test_incomplete_then_rerun_uses_recorded_scope(self):
        real_unlink = Path.unlink

        def flaky(p, *a, **k):
            if p.name == f"{self.pid}_p1.png":
                raise PermissionError(13, "in use")
            return real_unlink(p, *a, **k)
        with mock.patch.object(Path, "unlink", flaky):
            code, out = kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertIn("還沒清完：暫存 1 份", out)
        self.assertEqual(self.row()["purge"], "pending")
        self.assertIn(self.pid, json.dumps(self.row()))
        # 備份裡的清冊舊版已經改寫過了：重跑照記下的範圍清
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("清好了："), out)
        self.assertFalse((Path(self.V) / ".system/temp" / f"{self.pid}_p1.png").exists())
        self.assertNotIn("pending", json.dumps(self.row()))
        self.assertNotIn(self.pid, json.dumps(self.row()))

    def test_purge_incomplete_exit_3(self):
        code, out = kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))
        p = Path(self.V) / ".system/sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"][-1].update(purge="pending", purge_ids=[self.pid])
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (Path(self.V) / ".system/temp" / f"{self.pid}.md").write_text("又出現", encoding="utf-8")
        real_unlink = Path.unlink

        def flaky(p_, *a, **k):
            if p_.name == f"{self.pid}.md":
                raise PermissionError(13, "in use")
            return real_unlink(p_, *a, **k)
        with mock.patch.object(Path, "unlink", flaky):
            code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), "還沒清完：暫存 1 份；關掉開著的檔再跑一次 kb folders purge 客戶案件/私人")


# ---------- 第 5 條：引號與刪檔 ----------

class Quoting(Base):
    def test_plan_quote(self):
        self.assertEqual(plan.quote("客戶案件/Q3$(x)"), "'客戶案件/Q3$(x)'")
        self.assertEqual(plan.quote("a`b"), "'a`b'")
        self.assertEqual(plan.quote("一般 夾"), '"一般 夾"')
        self.assertIsNone(plan.shell_arg("a$b'c"))

    def test_purge_step_quoting(self):
        out = {"purge_pending": [{"name": "客戶案件/Q3$(x)", "synthesis": []}, {"name": "客戶案件/a$'b", "synthesis": []}]}
        steps = plan._purge_steps(out)
        self.assertEqual(steps[0]["do"], "kb folders purge '客戶案件/Q3$(x)'")
        self.assertIsNone(steps[1]["do"])
        self.assertIn("改夾名", steps[1]["note"])

    def test_other_disk_single_quotes(self):
        odd = self.raw / "報價與合約" / "Q3$x"
        odd.mkdir()
        with mock.patch.object(se, "_dev", side_effect=lambda p: 2 if "Q3$x" in str(p) else 1):
            msg = self.refuse(self.plan, self.fid, dest=str(odd))
        self.assertIn(f"'{odd}'", msg)  # 有 $ 的那個用單引號
        self.assertNotIn(f'"{odd}"', msg)

    def test_removable_drive_refused(self):
        with mock.patch.object(se, "_drive_type", return_value=2):
            msg = self.refuse(se.plan_trash, self.V, self.sources, self.fid, self.V)
        self.assertEqual(msg, "資源回收筒用不了（隨身碟或網路磁碟），沒有刪")


# ---------- 第 6 條：檔名 ----------

class Names(Base):
    def test_readonly(self):
        target = self.raw / ESTIMATE
        os.chmod(target, stat.S_IREAD)
        moved = self.raw / "客戶案件" / "大安陳" / "估價單.pdf"
        self.addCleanup(lambda: [os.chmod(p, stat.S_IWRITE | stat.S_IREAD) for p in (target, moved) if p.exists()])
        with mock.patch.object(se, "recycle_room", return_value=(0, 10 ** 9)):
            p = se.plan_trash(self.V, self.sources, self.fid, self.V)
            self.assertEqual(self.refuse(se.do_trash, self.V, p, self.t), "這份是唯讀的，先取消唯讀再刪")
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))  # 唯讀的照樣搬得動
        self.assertTrue((self.raw / "客戶案件" / "大安陳" / "估價單.pdf").is_file())

    def test_unreadable_names(self):
        text = "這種名字知識庫不會讀，換個名字"
        self.assertEqual(self.refuse(self.plan, self.fid, new_name=".隱藏的"), text)
        self.assertEqual(self.refuse(self.plan, self.fid, new_name="~$暫存"), text)
        hidden = self.raw / "客戶案件" / ".隱藏"  # fixture 本來就有這個夾
        hidden.mkdir(exist_ok=True)
        self.assertEqual(self.refuse(self.plan, self.fid, dest=str(hidden)), text)
        attr = self.raw / "客戶案件" / "屬性隱藏"
        attr.mkdir()
        with mock.patch.object(se, "_attrs", side_effect=lambda p: 2 if "屬性隱藏" in str(p) else 0):
            self.assertEqual(self.refuse(self.plan, self.fid, dest=str(attr)), text)

    def test_extensions(self):
        self.assertEqual(self.plan(self.fid, new_name="2026.10 報價")["new_name"], "2026.10 報價.pdf")
        self.assertEqual(self.plan(self.fid, new_name="Dr. Wang")["new_name"], "Dr. Wang.pdf")
        self.assertEqual(self.plan(self.fid, new_name="v1.2")["new_name"], "v1.2.pdf")
        self.assertEqual(self.plan(self.fid, new_name="簽約版.PDF")["new_name"], "簽約版.PDF")
        self.assertEqual(self.refuse(self.plan, self.fid, new_name="合約.docx"), "副檔名不改：.pdf → .docx")
        self.assertEqual(self.refuse(self.plan, self.fid, new_name="照片.jpg"), "副檔名不改：.pdf → .jpg")


# ---------- 第 7 條：退回被擋說怎麼解開、不寫 hook-errors.log ----------

class RevertBlocked(Base):
    def test_taken_sentence_and_no_error_log(self):
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))
        (self.raw / ESTIMATE).write_bytes(b"new one")
        log = Path(self.V) / ledger.ERROR_LOG_REL
        before = log.read_text(encoding="utf-8") if log.exists() else ""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = versions._revert(self.V, None, False, False)
        self.assertEqual(code, 3)
        self.assertEqual(out.getvalue().strip(),
                         "退不回去：「內湖李」裡已經有「估價單.pdf」了；先把那份改名或搬走再說退回，什麼都沒動")
        after = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertEqual(after, before)


# ---------- 第 8 條：relocate 時 hidden 排除在新位置找不到 ----------

class RelocateHidden(Base):
    def test_hidden_missing_refused_without_check(self):
        lee = self.raw / "客戶案件" / "內湖李"
        (lee / "私人").mkdir()
        sp = Path(self.V) / ".system/sources.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "內湖李/私人", "kind": "hidden", "reason": "", "added": "2026-10-22"})
        sp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        new = self.tmp / "E槽" / "客戶案件"
        shutil.copytree(self.raw / "客戶案件", new, ignore=shutil.ignore_patterns("私人"))
        code, out = kb("folders", "relocate", "客戶案件", str(new), "--check", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertIn("不讀的夾：內湖李/私人 在新位置找不到", out)
        before = sp.read_text(encoding="utf-8")
        code, out = kb("folders", "relocate", "客戶案件", str(new), "--vault", str(self.V))
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), "不想讓 AI 看到的「內湖李/私人」在新位置找不到；先說它現在叫什麼，或重新設不讀，再換過去")
        self.assertEqual(sp.read_text(encoding="utf-8"), before)


# ---------- 第 9 條：遮罩、鎖、--json ----------

class Small(Base):
    def test_mask_full_paths_only(self):
        mask = purge.masker(self.V, "客戶案件", "私人", str(self.raw / "客戶案件" / "私人"))
        self.assertEqual(mask("改了 客戶案件/私人/日記.txt。"), "改了 （已排除）。")
        self.assertEqual(mask("日記.txt 跟 data"), "日記.txt 跟 data")
        self.assertEqual(mask("舊客戶案件/私人2"), "舊客戶案件/私人2")
        self.assertEqual(mask("x客戶案件/私人"), "x（已排除）")  # 第二輪修：完整路徑不看左邊界

    def test_ledger_rewrite_takes_lock(self):
        taken = []
        real = purge.FileLock

        class Spy(real):
            def __enter__(self_):
                taken.append(self_.path)
                return super().__enter__()
        ledger.append(self.V, {"tool": "Read", "decision": "deny", "zone": "Z9", "reason_code": "x", "head": "客戶案件/私人/a"})
        with mock.patch.object(purge, "FileLock", Spy):
            purge._scrub_text(self.V, ledger.LEDGER_REL, purge.masker(self.V, "客戶案件", "私人", "/nowhere"))
        self.assertTrue(any(p.endswith("ledger.tsv.lock") for p in taken), taken)

    def test_purge_json_refusal(self):
        private = self.raw / "客戶案件" / "私人"
        private.mkdir()
        sp = Path(self.V) / ".system/sources.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "私人", "kind": "hidden", "reason": "", "added": "2026-10-22",
                                               "purge": "pending"})
        sp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(purge, "blocking", return_value=["信義王"]):
            code, out = kb("folders", "purge", "客戶案件/私人", "--json", "--vault", str(self.V))
        self.assertEqual(code, 3)
        got = json.loads(out)
        self.assertEqual((got["purged"], got["blocked"]), (False, ["信義王"]))


# ---------- 第 10 條：move --to 收 where 印的「別名/相對路徑」 ----------

class WhereForm(Base):
    def test_move_to_alias_relative(self):
        old = os.getcwd()
        os.chdir(self.V)
        self.addCleanup(os.chdir, old)
        p = se.plan_move(self.V, sources_lib.load(self.V), self.fid, self.V, dest="客戶案件/大安陳")
        self.assertEqual(Path(p["dest_dir"]).name, "大安陳")
        (Path(self.V) / "案子" / "內湖李").mkdir(parents=True, exist_ok=True)
        p = se.plan_move(self.V, sources_lib.load(self.V), self.fid, self.V, dest="知識庫/案子/內湖李")
        self.assertEqual(Path(p["dest_dir"]), (Path(self.V) / "案子" / "內湖李").resolve())
