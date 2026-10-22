"""kb folders purge（manage 第二版第 5 點，D-174；疑義第 4、5、6 條）：「不想讓 AI 看到」排除後清殘留。
七項各一條（卡舊版、綜合卡舊版、狀態檔舊版與 md 重渲染、暫存、目前的狀態檔、工作日誌、ledger）加疑義第 5 條的
總目錄舊版、findings、office 工作區、moves.jsonl、hook-errors.log；綜合卡還引用時拒絕；清完 grep 整個知識庫找不到
那夾檔裡的獨特字串與檔名（工作日誌的 prompt 除外）；versions revert 退到 purged 那行印那句；cards todo 的 plan 最前面兩步。"""
import contextlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import _cards, _entities
from kb import cli, versions
from kb._lib import agenda, cards, entities, findings, journal, ledger, pending, rulings, synthesis, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

SECRET = "紫色大象UNIQ42"
DIARY = "日記本.txt"
SID = "test-purge-sid"


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved  # CLI 把鎖等待調成 30 秒，不留給後面的測試
    return code, out.getvalue()


def hits(V, needle):
    """整個知識庫（含 .system）裡含 needle 的檔（相對路徑）。"""
    out = []
    for p in Path(V).rglob("*"):
        if p.is_file() and not p.name.endswith(".lock"):
            try:
                if needle in p.read_text(encoding="utf-8", errors="ignore"):
                    out.append(p.relative_to(V).as_posix())
            except OSError:
                pass
    return sorted(out)


class Purge(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _entities.make_entity_vault()
        V = self.V
        _entities.put_entities(V, [_entities.entity(_entities.XINYI, "信義王")])
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / DIARY).write_text(f"今天{SECRET}來了", encoding="utf-8")
        _cards.scan_apply(V)
        self.fid = _cards.entry_of(V, "客戶案件/私人/" + DIARY)["id"]
        self.keep_fid = _cards.entry_of(V, _entities.ESTIMATE)["id"]
        t0 = turn.start(V, None, "")["turn"]
        item = _entities.new_item(V, "客戶案件/私人/" + DIARY)
        p = _cards.card_path(V, item, "王家心情")
        p.parent.mkdir(parents=True, exist_ok=True)
        text = _entities.with_entities(_cards.card_text(item, "王家心情", body=f"寫著{SECRET}。〔第 1 段〕\n"), ["信義王"])
        turn.backup(V, p.relative_to(V).as_posix(), t0)
        p.write_text(text, encoding="utf-8", newline="\n")
        turn.backup(V, p.relative_to(V).as_posix(), turn.start(V, None, "")["turn"])  # 卡的一個舊版在別的回合
        self.card_rel = p.relative_to(V).as_posix()
        _entities.write_card(V, _entities.ESTIMATE, "內湖李估價", ["信義王"])
        cards.render_index(V, turn.start(V, None, "")["turn"])
        self.syn_rel = self.write_synthesis(f"信義王最近{SECRET}。")
        t1 = turn.start(V, None, "")["turn"]
        pending.add(V, "unreadable", [self.fid], f"「{DIARY}」（客戶案件/私人）讀不到", "檔案壞了", t1)
        agenda.add(V, "提醒", "2026-12-01", "推測的事", ["信義王"], t1, card=self.fid)
        it, _new = agenda.add(V, "提醒", "2026-12-02", "確認過的事", ["信義王"], t1, card=self.fid)
        agenda.edit(V, it["id"], t1, text="確認過的事改過")
        findings.add(V, "日記推出來的", "看日記", ["信義王"], [self.fid], t1)
        findings.add(V, "估價推出來的", "看估價", ["信義王"], [self.keep_fid], t1)
        rulings.add(V, "不採用", [], [self.fid], "這張別信", t1)
        rulings.add(V, "口述為準", ["信義王"], [self.fid, self.keep_fid], "以我說的為準", t1)
        temp = Path(V) / ".system" / "temp"
        temp.mkdir(parents=True, exist_ok=True)
        (temp / f"{self.fid}.md").write_text(SECRET, encoding="utf-8")
        (temp / f"{self.fid}_p1.png").write_bytes(b"png")
        office = temp / "office" / t1
        office.mkdir(parents=True)
        (office / DIARY).write_text(SECRET, encoding="utf-8")
        turn.record_move(V, t1, "move", "file", f"file://客戶案件/{DIARY}", f"file://客戶案件/私人/{DIARY}")
        journal.record_program(V, f"拿到 客戶案件/私人/{DIARY}", [{"path": self.card_rel, "action": "create"}], "cards")
        jt = turn.start(V, SID, f"幫我看 {DIARY}")
        journal.begin_turn(V, None, jt)
        journal.complete_turn(V, jt, f"裡面寫{SECRET}", "看了日記", "ai", [{"path": self.card_rel, "action": "modify"}], False)
        ledger.append(V, {"tool": "Read", "decision": "deny", "zone": "Z9", "reason_code": "x",
                          "head": str(self.private / DIARY)})
        ledger.log_error(V, f"讀不到 {self.private / DIARY}", source="convert")
        turn.set_done(V, SID)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_synthesis(self, body_line):
        data = entities.load(self.V)
        e = entities.find(data, "信義王")[0]
        ms = entities.mounts(self.V, data)
        rel = synthesis.card_rel(e)
        p = Path(self.V) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        t = turn.current(self.V)["turn"]
        turn.backup(self.V, rel, t)
        p.write_text(_entities.synthesis_text(synthesis.header_for(self.V, e, ms.get("信義王", [])), "信義王",
                                              body=f"一句。\n\n## 現況\n- {body_line}〔f-00000000〕\n"),
                     encoding="utf-8", newline="\n")
        return rel

    def exclude(self):
        return kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))

    def sources_row(self):
        data = json.loads((Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"))
        return data["sources"][0]["excludes"][-1]

    def test_whole_flow(self):
        V = self.V
        turn.start(V, None, "不要讀私人那夾")
        code, out = self.exclude()
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "已排除「客戶案件/私人」（不想讓 AI 看到）。卡拿掉 1 張。")
        self.assertEqual(lines[2], "接著要重寫的綜合卡：信義王（拿掉「王家心情」等 1 張卡的內容），寫完跑 kb folders purge 客戶案件/私人")
        self.assertEqual(self.sources_row()["purge"], "pending")
        self.assertFalse((Path(V) / self.card_rel).exists())
        # 13：cards todo 的 plan 最前面兩步
        todo = cards.todo(V, sources_lib.load(V))
        self.assertEqual([s["step"] for s in todo["plan"][:2]], ["purge_synthesis", "purge"])
        self.assertEqual(todo["plan"][1]["do"], 'kb folders purge 客戶案件/私人')
        self.assertEqual(todo["purge_pending"][0]["synthesis"][0]["cards"], [{"id": self.fid, "title": "王家心情"}])
        # 綜合卡還引用 → 拒絕並列名
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(V))
        self.assertEqual((code, out.strip()), (3, "還有綜合卡寫著那夾的內容：信義王（先重寫再清）"))
        # AI 重寫綜合卡（PreToolUse 會先備份剛才那版）
        self.write_synthesis("信義王現在很好。")
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(V))
        self.assertEqual(code, 0, out)
        self.assertRegex(out.strip(), r"^清好了：卡 1 張、備份 \d+ 份、暫存 3 份、推算 1 條、工作日誌 \d+ 段；"
                                      r"之前讀過的已送過雲端，Claude 自己的對話紀錄也還有，這兩處清不掉；以前的回答若沒提到檔名或卡名、只是轉述，認不出來$")
        self.assertNotEqual(self.sources_row()["purge"], "pending")
        # 第 5 點：目前的狀態檔
        self.assertEqual([i for i in pending.load(V)["items"] if self.fid in i.get("file_ids", [])], [])
        items = agenda.load(V)["items"]
        self.assertEqual([i["text"] for i in items], ["確認過的事改過"])
        self.assertIsNone(items[0]["origin"])
        rs = rulings.load(V)["rulings"]
        self.assertEqual([(r["status"], [c["id"] for c in r["cards"]]) for r in rs],
                         [("revoked", []), ("active", [self.keep_fid])])
        self.assertEqual([c["text"] for c in findings.load(V)["findings"]], ["估價推出來的"])
        # 第 4 點：暫存與 office
        self.assertEqual(sorted(p.name for p in (Path(V) / ".system/temp").glob(f"{self.fid}*")), [])
        # 驗收 12：整個知識庫找不到獨特字串；檔名只剩工作日誌的 prompt（jsonl 與渲染的月檔）
        self.assertEqual(hits(V, SECRET), [])
        diary = hits(V, DIARY)
        # 那一句的「問」：工作日誌的 prompt（jsonl 與月檔）、回合檔（turns/<sid>.json、current.json）、versions/*/turn.json
        self.assertTrue(all(h.startswith((".system/journal/", "系統/工作日誌/", ".system/turns/"))
                            or (h.startswith(".system/versions/") and h.endswith("/turn.json")) for h in diary), diary)
        for row in journal.read_lines(V, journal.month_of(turn.current(V)["turn"])):
            for k in ("note", "result"):
                self.assertNotIn(DIARY, str(row.get(k, "")), row)
            for c in row.get("changed") or []:
                self.assertNotIn("私人", c["path"])
        # 第三輪修：夾名與卡標題也查；夾名只准留在登記表（含它的舊版）與那一句的「問」
        def prompt_place(h):
            return (h.startswith((".system/journal/", "系統/工作日誌/", ".system/turns/"))
                    or (h.startswith(".system/versions/") and h.endswith("/turn.json")))
        folder = [h for h in hits(V, "私人") if not h.endswith(".system/sources.json") and not prompt_place(h)]
        self.assertEqual(folder, [])
        self.assertEqual(hits(V, "王家心情"), [])
        prompts = [r.get("prompt") for r in journal.read_lines(V, journal.month_of(turn.current(V)["turn"]))]
        self.assertIn(f"幫我看 {DIARY}", prompts)
        # manifest 的行改 purged、path 換掉；退回印那句
        rows = [json.loads(ln) for d in (Path(V) / ".system/versions").iterdir()
                for ln in (d / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if (d / "manifest.jsonl").is_file()]
        self.assertTrue(any(r["action"] == "purged" and r["path"] == "（已排除）" for r in rows))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = versions._revert(V, None, False, False)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("那一版因為不讀的夾清掉了，退不回", out.getvalue())
        self.assertEqual(hits(V, SECRET), [])

    def test_moves_record_masked_and_revert_refuses(self):
        turn.start(self.V, None, "")
        self.exclude()
        self.write_synthesis("信義王現在很好。")
        kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        found = [ln for d in (Path(self.V) / ".system/versions").iterdir() if (d / "moves.jsonl").is_file()
                 for ln in (d / "moves.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(found), 1)
        self.assertEqual((json.loads(found[0])["from"], json.loads(found[0])["to"]), ("（已排除）", "（已排除）"))

    def test_index_backup_lines_dropped(self):
        turn.start(self.V, None, "")
        self.exclude()
        self.write_synthesis("信義王現在很好。")
        kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        short = cards.short_id(self.fid)
        for p in (Path(self.V) / ".system/versions").rglob(cards.INDEX_POSIX):
            self.assertNotIn(f"-{short}.md)", p.read_text(encoding="utf-8"))
            self.assertIn("內湖李估價", p.read_text(encoding="utf-8"))

    def test_nothing_to_purge(self):
        code, out = kb("folders", "purge", "客戶案件/沒有這條", "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (0, "「客戶案件/沒有這條」沒有要清的"))
        kb("folders", "exclude", str(self.private), "--kind", "unused", "--vault", str(self.V))
        code, out = kb("folders", "purge", "客戶案件/私人", "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (0, "「客戶案件/私人」沒有要清的"))

    def test_purge_by_path(self):
        turn.start(self.V, None, "")
        self.exclude()
        self.write_synthesis("信義王現在很好。")
        code, out = kb("folders", "purge", str(self.private), "--vault", str(self.V))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("清好了："))

    def test_unused_and_remove_drop_cards_and_revert(self):
        """驗收 11：unused 與 remove 那些卡拿掉、退回回來；綜合卡列待更新。"""
        turn.start(self.V, None, "")
        code, out = kb("folders", "exclude", str(self.private), "--kind", "unused", "--vault", str(self.V))
        self.assertEqual(out.splitlines()[0], "已排除「客戶案件/私人」（用不到）。卡拿掉 1 張。")
        self.assertFalse((Path(self.V) / self.card_rel).exists())
        stale = cards.todo(self.V, sources_lib.load(self.V))["synthesis_stale"]
        self.assertEqual([r["id"] for s in stale for r in s["removed"] if r["id"].startswith("f-")], [self.fid])
        with contextlib.redirect_stdout(io.StringIO()):
            versions._revert(self.V, None, False, False)
        self.assertTrue((Path(self.V) / self.card_rel).is_file())
        turn.start(self.V, None, "")
        code, out = kb("folders", "remove", "客戶案件", "--vault", str(self.V))
        self.assertIn("卡拿掉 2 張", out.splitlines()[0])  # 日記與估價單（會議紀錄沒有卡）
        self.assertFalse((Path(self.V) / self.card_rel).exists())


if __name__ == "__main__":
    unittest.main()
