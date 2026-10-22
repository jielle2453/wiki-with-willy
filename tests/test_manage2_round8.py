"""manage 第二版第八輪修（測試報告「第八輪」，設計 2026-10-23 定的三條）：都出在排除與 purge 不在同一個回合。"""
import json
from pathlib import Path

from tests import _entities
from tests.test_manage2_round7 import Private, kb
from kb._lib import entities, journal, purge, synthesis, turn  # noqa: E402


def grep(V, needle):
    hits = []
    for f in Path(V).rglob("*"):
        if f.is_file() and not f.name.endswith(".lock") and needle in f.read_text(encoding="utf-8", errors="ignore"):
            hits.append(f.relative_to(V).as_posix())
    return hits


class TwoTurns(Private):
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

    def open_turn(self, sid, prompt):
        t = turn.start(self.V, sid, prompt)
        journal.begin_turn(self.V, None, t)
        return t["turn"]

    def blocked_exclude(self, prompt="不要讀 客戶案件/私人 那夾"):
        self.write_synthesis()
        a = self.open_turn("sA", prompt)
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        self.assertIn("接著要重寫的綜合卡：信義王", out)
        self.write_synthesis()  # AI 照 todo 重寫：卡拿掉了
        return a

    def prompts(self):
        rows = journal.read_lines(self.V, journal.month_of(turn.current(self.V)["turn"]))
        return {r["turn"]: r.get("prompt") for r in rows if r.get("kind") == "turn"}


# ---------- 第 1 條：收尾的備份不留標題與編號 ----------

class NoLeakAfterFinish(TwoTurns):
    def test_grep_zero(self):
        self.blocked_exclude("不要讀私人那夾")
        self.open_turn("sB", "清掉")
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertEqual(grep(self.V, self.TITLE), [])
        self.assertEqual(grep(self.V, self.pid), [])


# ---------- 第 2 條：排除那一句永遠不換 ----------

class ExcludeTurnKept(TwoTurns):
    def test_blocked_then_purge_both_kept(self):
        other = self.open_turn("s0", "看一下 客戶案件/私人 底下")
        a = self.blocked_exclude()
        pp = json.loads((Path(self.V) / purge.PENDING_POSIX).read_text(encoding="utf-8"))
        self.assertIn(a, pp["items"]["客戶案件/私人"]["turns"])
        b = self.open_turn("sB", "清掉 客戶案件/私人 。")
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        got = self.prompts()
        self.assertEqual(got[other], "（已排除）")  # 路徑比得到：不是假過
        self.assertEqual(got[a], "不要讀 客戶案件/私人 那夾")
        self.assertEqual(got[b], "清掉 客戶案件/私人 。")

    def test_blocked_purge_records_own_turn(self):
        a = self.open_turn("sA", "不要讀")
        self.write_synthesis()
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        b = self.open_turn("sB", "清掉 客戶案件/私人 。")  # 還沒重寫綜合卡：被擋
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 3, out)
        self.assertEqual(purge.recorded(self.V, "客戶案件", "私人", "turns") >= {a, b}, True)


# ---------- 第 3 條：終端機跑的不記 ----------

class TerminalPurge(TwoTurns):
    def test_done_turn_not_kept(self):
        self.blocked_exclude()
        c = self.open_turn("sC", "看一下 客戶案件/私人 底下")
        turn.set_done(self.V, "sC")  # 答完了，接著在終端機跑
        self.assertEqual(purge.own_turns(self.V), set())
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.prompts()[c], "（已排除）")

    def test_in_conversation(self):
        t = self.open_turn("sD", "x")
        self.assertEqual(purge.own_turns(self.V), {t})
