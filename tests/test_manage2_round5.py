"""manage 第二版第五輪修（確認報告 reports/manage第二版-確認.md 第 7、9 步，設計 2026-10-22 定的五條）：每條一組測試。"""
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
from unittest import mock

from tests import _cards, _entities
from tests.test_lib_source_edit import ESTIMATE, Base
from kb import cli, folders
from kb._lib import cards, inventory, journal, purge, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import source_edit as se  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

TAIL = ("之前讀過的已送過雲端，Claude 自己的對話紀錄也還有，這兩處清不掉；"
        "以前的回答若沒提到檔名或卡名、只是轉述，認不出來")


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


def grep(V, needle):
    hits = []
    for f in Path(V).rglob("*"):
        if f.is_file() and not f.name.endswith(".lock") and needle in f.read_text(encoding="utf-8", errors="ignore"):
            hits.append(f.relative_to(V).as_posix())
    return hits


# ---------- 第 1 條：工作日誌與別的檔的舊版 ----------

class PurgeJournal(Base):
    def setUp(self):
        super().setUp()
        V = self.V
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / "合約.txt").write_text("逾期每日千分之一", encoding="utf-8")
        shutil.copy2(self.raw / ESTIMATE, self.private / "估價副本.pdf")  # 同一份內容在兩處：估價那張卡的原檔行帶著這夾
        _cards.scan_apply(V)
        self.pid = _cards.entry_of(V, "客戶案件/私人/合約.txt")["id"]
        self.rel = _entities.write_card(V, "客戶案件/私人/合約.txt", "信義王合約第一版")
        cards.sync(V, sources_lib.load(V), turn.start(V, None, "")["turn"])  # 估價那張卡的原檔行多了這夾裡的副本
        t = turn.start(V, None, "")["turn"]
        turn.backup(V, self.card_rel, t)  # 別張卡（估價）的舊版：原檔行有這夾的路徑
        assert "客戶案件/私人/估價副本.pdf" in (Path(V) / ".system/versions" / t / "files" / self.card_rel).read_text(encoding="utf-8")
        # 回答裡列了卡標題、卡編號的回合（changed 沒指到那夾）
        for result in (f"報價比較：信義王合約第一版 120 萬", f"依據〔{self.pid}〕", "跟那夾無關的回答"):
            jt = turn.start(V, "sid-" + str(abs(hash(result)) % 1000), "問一下")
            journal.begin_turn(V, None, jt)
            journal.complete_turn(V, jt, result, "做了", "ai", [], False)
        journal.record_program(V, "整理了「信義王合約第一版」", [], "cards")
        turn.start(V, None, "")

    def test_mentions_scrubbed_and_tail(self):
        code, out = kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.strip().endswith(TAIL), out)
        rows = journal.read_lines(self.V, journal.month_of(turn.current(self.V)["turn"]))
        results = [r.get("result") for r in rows if r.get("kind") == "turn"]
        self.assertIn("跟那夾無關的回答", results)
        self.assertEqual(sum(1 for r in results if r == "（已排除）"), 2)
        self.assertEqual(grep(self.V, "信義王合約第一版"), [])
        self.assertEqual(grep(self.V, self.pid), [])
        self.assertEqual([h for h in grep(self.V, "客戶案件/私人") if not h.endswith(".system/sources.json")], [])


# ---------- 第 2 條：exclude 當下改有其他位置的卡的原檔 ----------

class ExcludeOtherLocation(Base):
    def test_card_follows_remaining_location_now(self):
        dup = self.raw / "報價與合約" / "估價單.pdf"
        shutil.copy2(self.raw / ESTIMATE, dup)
        _cards.scan_apply(self.V)
        code, out = kb("folders", "exclude", str(self.raw / "客戶案件" / "內湖李"), "--kind", "unused",
                       "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        groups = cards._scan_groups(self.V)[0]
        front = groups[self.fid][0]["front"]
        self.assertEqual(front["原檔"], ["file://報價與合約/估價單.pdf"])
        self.assertEqual(cards.todo(self.V, sources_lib.load(self.V))["moved"], [])


# ---------- 第 3 條：只在雲端的檔不讀內容 ----------

class CloudNoRead(Base):
    def test_scan_does_not_hash_when_attrs_turn_cloud(self):
        """walk 時看起來是一般檔、要算指紋前一刻是只在雲端：不讀（指紋、內容指紋都不算）。"""
        new = self.raw / "客戶案件" / "新的.docx"
        new.write_bytes(b"x" * 100)
        with mock.patch.object(inventory, "_attrs_now", return_value=0x400000), \
                mock.patch.object(inventory, "sha256_of", side_effect=AssertionError("讀了內容")), \
                mock.patch.object(inventory.content_fp, "text_fingerprint", side_effect=AssertionError("讀了內容")):
            report = inventory.scan(self.V, sources_lib.load(self.V))
        e = next(e for e in report["entries"] if e["locations"][0]["uri"] == "file://客戶案件/新的.docx")
        self.assertEqual(e["read"], "placeholder")
        self.assertEqual(e["fingerprints"], [])

    def test_single_file_check_cloud_line(self):
        with mock.patch.object(se, "cloud_only", return_value=True):
            p = self.plan(self.fid, dest=str(self.raw / "報價與合約"))
        self.assertIn("這份只在雲端；同一個 OneDrive 裡搬不會下載", se.check_lines(p, "move"))


# ---------- 第 4 條：成功句 ----------

class Sentences(Base):
    def test_same_last_segment_prints_alias_paths(self):
        a = self.raw / "報價與合約" / "內湖李"
        a.mkdir()
        p, got = self.move(self.fid, str(a))
        self.assertEqual(se.move_sentence(p, got),
                         "已搬好：估價單.pdf 從 客戶案件/內湖李 搬到 報價與合約/內湖李；卡跟著改了（說「退回」會搬回去）")

    def test_folder_sentence_has_revert(self):
        archive = self.raw / "客戶案件" / "封存"
        archive.mkdir()
        p, got = self.move(str(self.raw / "客戶案件" / "內湖李"), str(archive))
        self.assertTrue(se.move_sentence(p, got).endswith("（說「退回」會搬回去）"), se.move_sentence(p, got))

    def test_relocate_counts_cards(self):
        new = self.tmp / "E槽" / "客戶案件"
        shutil.copytree(self.raw / "客戶案件", new)
        code, out = kb("folders", "relocate", "客戶案件", str(new), "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertIn("；卡的原檔連結更新了 1 張；", out.splitlines()[0])
