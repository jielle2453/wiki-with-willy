"""manage 第二版第七輪修（補確認報告第 7 步，設計 2026-10-23 定的四條）：每條一組測試；第 4 條（header 的字面 \\n）
查過是 --json 的跳脫被讀錯，程式印的是真的換行，這裡守著。"""
import contextlib
import io
import json
import re
from pathlib import Path
from unittest import mock

from tests import _cards, _entities
from tests.test_lib_source_edit import Base
from kb import cli, folders
from kb._lib import cards, entities, findings, journal, synthesis, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

PURGED = "（已排除）"


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


class Private(Base):
    TITLE = "王家心情紀錄"

    def setUp(self):
        super().setUp()
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / "日記.txt").write_text("秘密", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.pid = _cards.entry_of(self.V, "客戶案件/私人/日記.txt")["id"]
        self.prel = _entities.write_card(self.V, "客戶案件/私人/日記.txt", self.TITLE, ["信義王"])

    def exclude(self):
        return kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))

    def said(self, sid, prompt, result="好"):
        """一句話一個回合：turns/<sid>.json、工作日誌一行、回合夾的 turn.json（備份一份別的檔）。回回合代號。"""
        jt = turn.start(self.V, sid, prompt)
        journal.begin_turn(self.V, None, jt)
        turn.backup(self.V, self.card_rel, jt["turn"])
        journal.complete_turn(self.V, jt, result, "做了", "ai", [], False)
        return jt["turn"]

    def prompts(self):
        rows = journal.read_lines(self.V, journal.month_of(turn.current(self.V)["turn"]))
        return {r["turn"]: r.get("prompt") for r in rows if r.get("kind") == "turn"}

    def turn_file(self, sid):
        return json.loads((Path(self.V) / ".system/turns" / f"{sid}.json").read_text(encoding="utf-8"))["prompt_head"]

    def version_meta(self, t):
        return json.loads((Path(self.V) / ".system/versions" / t / "turn.json").read_text(encoding="utf-8"))["prompt_head"]


# ---------- 第 1 條：字詞遮罩也套「問」 ----------

class PromptMasked(Private):
    def test_mentions_masked_exclude_turn_kept(self):
        by_title = self.said("s1", f"幫我看 {self.TITLE} 那張，金額 120 萬")
        by_id = self.said("s2", f"〔{self.pid}〕寫什麼")
        by_path = self.said("s3", "檔案索引/客戶案件/私人/ 底下有什麼")
        plain = self.said("s4", "今天天氣如何")
        ex = turn.start(self.V, "s5", f"不要讀客戶案件/私人那夾，{self.TITLE}不想讓你看")
        journal.begin_turn(self.V, None, ex)
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        got = self.prompts()
        for t in (by_title, by_id, by_path):
            self.assertEqual(got[t], PURGED)
            self.assertEqual(self.version_meta(t), PURGED)
        for sid in ("s1", "s2", "s3"):
            self.assertEqual(self.turn_file(sid), PURGED)
        self.assertEqual(got[plain], "今天天氣如何")
        self.assertEqual(self.turn_file("s4"), "今天天氣如何")
        self.assertIn(self.TITLE, got[ex["turn"]])  # 排除那一句自己不換
        self.assertIn(self.TITLE, self.turn_file("s5"))
        month = (Path(self.V) / "系統/工作日誌" / f"{journal.month_of(plain)}.md").read_text(encoding="utf-8")
        self.assertNotIn("金額 120 萬", month)


class PromptKeptForPurgeTurn(Private):
    def test_exclude_and_purge_turns_kept(self):
        self.said("s1", f"{self.TITLE} 是什麼")
        ex = turn.start(self.V, "s2", "不要讀客戶案件/私人")
        journal.begin_turn(self.V, None, ex)
        with mock.patch.object(journal, "scrub", side_effect=OSError(13, "locked")):
            code, out = self.exclude()
        self.assertIn("還沒清完", out)
        pp = json.loads((Path(self.V) / ".system/purge-pending.json").read_text(encoding="utf-8"))
        self.assertIn(ex["turn"], pp["items"]["客戶案件/私人"]["turns"])
        pu = turn.start(self.V, "s3", "再清一次客戶案件/私人")
        journal.begin_turn(self.V, None, pu)
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        got = self.prompts()
        self.assertEqual(got[ex["turn"]], "不要讀客戶案件/私人")
        self.assertEqual(got[pu["turn"]], "再清一次客戶案件/私人")
        self.assertEqual(self.turn_file("s3"), "再清一次客戶案件/私人")
        self.assertNotIn(f"{self.TITLE} 是什麼", got.values())


# ---------- 第 2 條：綜合卡檔頭「整理到」拿掉清掉的推算 ----------

class SyncedLinesDropped(Private):
    def write_synthesis(self):
        data = entities.load(self.V)
        e = entities.find(data, "信義王")[0]
        ms = entities.mounts(self.V, data).get("信義王", [])
        rel = synthesis.card_rel(e)
        p = Path(self.V) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_entities.synthesis_text(synthesis.header_for(self.V, e, ms), "信義王"), encoding="utf-8",
                     newline="\n")
        return rel

    def synced(self, rel):
        return cards.parse_front((Path(self.V) / rel).read_text(encoding="utf-8")).get("整理到") or []

    def test_finding_line_removed_with_backup(self):
        _entities.write_card(self.V, _entities.QUOTE, "信義王報價", ["信義王"])
        qid = _cards.entry_of(self.V, _entities.QUOTE)["id"]
        t = turn.start(self.V, None, "")["turn"]
        cid = findings.add(self.V, "日記推出來的", "看日記", ["信義王"], [self.pid], t)[0]["id"]
        self.write_synthesis()
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertIn("接著要重寫的綜合卡：信義王", out)
        rel = self.write_synthesis()  # AI 照 todo 的 header 重寫：卡拿掉了，推算還在
        self.assertTrue(any(x.startswith(cid) for x in self.synced(rel)))
        pt = turn.start(self.V, None, "")["turn"]
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        lines = self.synced(rel)
        self.assertFalse(any(x.startswith((cid, self.pid)) for x in lines), lines)
        self.assertTrue(any(x.startswith(qid) for x in lines), lines)
        manifest = (Path(self.V) / ".system/versions" / pt / "manifest.jsonl").read_text(encoding="utf-8")
        self.assertIn(json.dumps(rel, ensure_ascii=False), manifest)
        self.assertEqual(cards.todo(self.V, sources_lib.load(self.V))["synthesis_stale"], [])


# ---------- 第 3 條：S-16、S-23 的例外 ----------

class Rules(Base):
    def test_hidden_exception(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "rules/synthesis.md").read_text(encoding="utf-8")
        rules = dict(re.findall(r"^- (S-\d{2}) (.+)$", text, re.MULTILINE))
        for key in ("S-16", "S-23"):
            self.assertIn("不想讓 AI 看到", rules[key])
            self.assertIn("紀事要刪", rules[key])


# ---------- 第 4 條：header 是真的換行 ----------

class HeaderNewlines(Base):
    def test_no_literal_backslash_n(self):
        data = entities.load(self.V)
        e = entities.find(data, "內湖李")[0]
        ms = entities.mounts(self.V, data).get("內湖李", [])
        h = synthesis.header_for(self.V, e, ms)
        self.assertNotIn("\\", h)
        self.assertIn("\n名稱: 內湖李\n", h)
