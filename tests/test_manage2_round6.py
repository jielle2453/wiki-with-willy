"""manage 第二版第六輪修（測試報告「第六輪」一節，設計 2026-10-22 定的七條）：每條用 c7 的重現當測試。"""
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
from kb._lib import agenda, cards, findings, journal, ledger, purge, rulings, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import source_edit as se  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

UNDECODABLE = "有 1 份備份讀不懂編碼，沒動"


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


class Private(Base):
    """客戶案件/私人 底下一份檔、一張卡（標題可換）。"""
    TITLE = "王家心情"

    def setUp(self):
        super().setUp()
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / "日記.txt").write_text("秘密", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.pid = _cards.entry_of(self.V, "客戶案件/私人/日記.txt")["id"]
        self.prel = _entities.write_card(self.V, "客戶案件/私人/日記.txt", self.TITLE)

    def exclude(self):
        return kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))

    def journal_turn(self, result):
        jt = turn.start(self.V, f"sid{abs(hash(result)) % 10000}", "問")
        journal.begin_turn(self.V, None, jt)
        journal.complete_turn(self.V, jt, result, "做了", "ai", [], False)

    def results(self):
        rows = journal.read_lines(self.V, journal.month_of(turn.current(self.V)["turn"]))
        return [r.get("result") for r in rows if r.get("kind") == "turn"]


# ---------- 第 1 條：字詞遮罩只套工作日誌 ----------

class WordMaskJournalOnly(Private):
    TITLE = "會議紀錄報告"

    def test_other_backups_ledger_untouched_by_words(self):
        t = turn.start(self.V, None, "")["turn"]
        turn.backup(self.V, self.card_rel, t)  # 別張卡的舊版：卡型: 會議紀錄、標題…
        turn.backup(self.V, ".system/entities.json", t)
        ledger.append(self.V, {"tool": "Read", "decision": "deny", "zone": "Z9", "reason_code": "x", "head": "會議紀錄報告"})
        before_card = (Path(self.V) / ".system/versions" / t / "files" / self.card_rel).read_text(encoding="utf-8")
        before_ent = (Path(self.V) / ".system/versions" / t / "files/.system/entities.json").read_text(encoding="utf-8")
        before_ledger = (Path(self.V) / ledger.LEDGER_REL).read_text(encoding="utf-8")
        self.journal_turn("整理了會議紀錄報告那份")
        turn.start(self.V, None, "")
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertEqual((Path(self.V) / ".system/versions" / t / "files" / self.card_rel).read_text(encoding="utf-8"),
                         before_card)
        self.assertEqual((Path(self.V) / ".system/versions" / t / "files/.system/entities.json").read_text(
            encoding="utf-8"), before_ent)
        self.assertEqual((Path(self.V) / ledger.LEDGER_REL).read_text(encoding="utf-8"), before_ledger)
        self.assertIn("（已排除）", self.results())  # 日誌照樣認得出標題


class TitleIsCardTypeOrEntity(Private):
    TITLE = "會議紀錄"  # 也是設定檔的卡型名

    def test_card_type_title_not_matched_as_word(self):
        self.journal_turn("今天整理了會議紀錄以外的事")
        turn.start(self.V, None, "")
        self.exclude()
        self.assertIn("今天整理了會議紀錄以外的事", self.results())

    def test_entity_name_title_not_matched(self):
        mask = purge.word_masker(lambda s: s, {self.pid}, purge.word_titles(self.V, {"信義王", "王家心情"}))
        self.assertEqual(mask("信義王那案"), "信義王那案")
        self.assertEqual(mask("王家心情那張"), "（已排除）那張")


class LedgerTimestamps(Private):
    TITLE = "2026"

    def test_ledger_times_kept_and_json_valid(self):
        t = turn.start(self.V, None, "")["turn"]
        turn.backup(self.V, ".system/entities.json", t)
        ledger.append(self.V, {"tool": "Read", "decision": "deny", "zone": "Z9", "reason_code": "x", "head": "a"})
        turn.start(self.V, None, "")
        self.exclude()
        lines = (Path(self.V) / ledger.LEDGER_REL).read_text(encoding="utf-8").splitlines()
        self.assertTrue(all(ln.startswith(("time", "2026-")) for ln in lines), lines)
        json.loads((Path(self.V) / ".system/versions" / t / "files/.system/entities.json").read_text(encoding="utf-8"))


# ---------- 第 2 條：exclude 當場 sync 失敗就整段倒回 ----------

class ExcludeSyncRollback(Base):
    def test_sync_failure_restores_everything(self):
        index = Path(self.V) / cards.INDEX_POSIX
        sp = Path(self.V) / ".system/sources.json"
        before = (sp.read_text(encoding="utf-8"), (Path(self.V) / ".system/inventory.json").read_text(encoding="utf-8"),
                  index.read_text(encoding="utf-8"), (Path(self.V) / self.card_rel).read_text(encoding="utf-8"))
        with mock.patch.object(cards, "sync", side_effect=OSError(13, "locked")):
            code, out = kb("folders", "exclude", str(self.raw / "客戶案件" / "內湖李"), "--kind", "unused",
                           "--vault", str(self.V))
        self.assertEqual(code, 3)
        self.assertTrue(out.strip().startswith("沒有動："), out)
        after = (sp.read_text(encoding="utf-8"), (Path(self.V) / ".system/inventory.json").read_text(encoding="utf-8"),
                 index.read_text(encoding="utf-8"), (Path(self.V) / self.card_rel).read_text(encoding="utf-8"))
        self.assertEqual(after, before)


# ---------- 第 3 條：標題清單留到清完 ----------

class TitlesKept(Private):
    TITLE = "王家心情"

    def test_rerun_still_masks_titles(self):
        self.journal_turn("報價表：王家心情 很好")
        turn.start(self.V, None, "")
        with mock.patch.object(journal, "scrub", side_effect=OSError(13, "locked")):
            code, out = self.exclude()
        self.assertIn("還沒清完", out)
        pp = Path(self.V) / ".system" / "purge-pending.json"
        self.assertIn("王家心情", pp.read_text(encoding="utf-8"))
        self.assertNotIn("王家心情", (Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"))
        self.assertFalse((Path(self.V) / self.prel).exists())  # 卡已經刪了，重跑只能靠記下的標題
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertNotIn("報價表：王家心情 很好", self.results())
        self.assertFalse(pp.exists())


# ---------- 第 4 條：非 UTF-8 的文字備份 ----------

class Encodings(Private):
    def test_cp950_masked_and_undecodable_skipped(self):
        t = turn.start(self.V, None, "")["turn"]
        files = Path(self.V) / ".system/versions" / t / "files"
        for rel, data in (("AI 產出/清單.csv", "客戶案件/私人/日記.txt,1\n".encode("cp950")),
                          ("AI 產出/亂碼.txt", b"\xff\xfe\x81\x00\xff\x80\xfe")):
            (files / rel).parent.mkdir(parents=True, exist_ok=True)
            (files / rel).write_bytes(data)
            with open(Path(self.V) / ".system/versions" / t / "manifest.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": rel, "action": "modify", "time": "x"}, ensure_ascii=False) + "\n")
        turn.start(self.V, None, "")
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertIn(UNDECODABLE, out)
        self.assertNotIn("還沒清完", out)
        text = (files / "AI 產出/清單.csv").read_bytes().decode("cp950")  # 還是 Big5、解得開
        self.assertTrue(text.startswith("（已排除）"), text)  # 檔名可以有逗號：路徑後面整段一起遮
        self.assertNotIn("私人", text)


# ---------- 第 5 條：SKILL 與 sources.md ----------

class Docs(Base):
    def test_turn_note_before_purge(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "skills/manage/SKILL.md").read_text(encoding="utf-8")
        sources = (root / "skills/manage/references/sources.md").read_text(encoding="utf-8")
        for text in (skill, sources):
            self.assertIn("turn note", text)
            self.assertIn("purge 之前", text)
            self.assertIn("不寫檔名、卡名", text)
        self.assertIn("那夾以外的副本讀不到，就一起清", sources)


# ---------- 第 6 條：同一個 OneDrive 根才印 ----------

class SameOneDriveRoot(Base):
    def plan_with(self, roots):
        with mock.patch.object(se, "cloud_only", return_value=True), mock.patch.object(se, "onedrive_roots", return_value=roots):
            return se.check_lines(self.plan(self.fid, dest=str(self.raw / "報價與合約")), "move")

    def test_only_same_root(self):
        line = "這份只在雲端；同一個 OneDrive 裡搬不會下載"
        raw = str(self.raw).replace("\\", "/").lower()
        self.assertIn(line, self.plan_with([raw]))
        self.assertNotIn(line, self.plan_with([]))  # Dropbox 一類：不是 OneDrive
        two = [raw + "/客戶案件", raw + "/報價與合約"]  # 個人與公司兩個根
        self.assertNotIn(line, self.plan_with(two))


# ---------- 第 7 條：還有別的位置（照 B） ----------

class OtherLocation(Private):
    def hooked(self):
        t = turn.start(self.V, None, "")["turn"]
        findings.add(self.V, "日記推出來的", "看日記", ["信義王"], [self.pid], t)
        agenda.add(self.V, "提醒", "2026-12-01", "推測的事", ["信義王"], t, card=self.pid)
        rulings.add(self.V, "口述為準", ["信義王"], [self.pid], "以我說的為準", t)

    def setUp(self):
        super().setUp()
        _entities.put_entities(self.V, [_entities.entity(_entities.XINYI, "信義王")])
        self.copy = self.raw / "報價與合約" / "日記.txt"
        shutil.copy2(self.private / "日記.txt", self.copy)
        _cards.scan_apply(self.V)
        self.hooked()

    def test_a_active_elsewhere_kept(self):
        turn.start(self.V, None, "")
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertIn(self.pid, cards._scan_groups(self.V)[0])
        self.assertEqual([c["text"] for c in findings.load(self.V)["findings"]], ["日記推出來的"])
        self.assertEqual([i["text"] for i in agenda.load(self.V)["items"]], ["推測的事"])
        self.assertEqual([c["id"] for r in rulings.load(self.V)["rulings"] for c in r["cards"]], [self.pid])

    def test_b_missing_purged(self):
        os.remove(self.copy)
        os.remove(self.private / "日記.txt")
        _cards.scan_apply(self.V)
        turn.start(self.V, None, "")
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertNotIn(self.pid, cards._scan_groups(self.V)[0])
        self.assertEqual(findings.load(self.V)["findings"], [])
        self.assertEqual(agenda.load(self.V)["items"], [])
