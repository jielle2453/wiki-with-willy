"""kb cards todo／sync／drop／index 黑箱（ingest 製卡第 4 點；驗收 6、8、9、12、20，實作疑義裁定 6）：真的跑 CLI。"""
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import _cards
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, inventory, journal, pending, turn  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb(*args, cwd=self.V)

    def todo(self):
        r = self.kb("cards", "todo", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def item(self, rel=_cards.MEETING, bucket="new"):
        fid = _cards.entry_of(self.V, rel)["id"]
        return next(i for i in self.todo()[bucket] if i["id"] == fid)

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def scan(self):
        self.assertEqual(self.kb("inventory", "scan").returncode, 0)

    def manifest(self, turn_id):
        return {e["path"]: e["action"] for e in turn.entries(self.V, turn_id)}


def snapshot(V):
    """.system/ 底下（temp/ 除外）每個檔的 mtime，加回合夾清單。"""
    out = {}
    for p in sorted((V / ".system").rglob("*")):
        rel = p.relative_to(V).as_posix()
        if p.is_file() and not rel.startswith(".system/temp/"):
            out[rel] = p.stat().st_mtime_ns
    return out


class Todo(Base):
    def test_json_and_read_only(self):
        before = snapshot(self.V)
        data = self.todo()
        self.assertEqual(snapshot(self.V), before)
        n_ok = sum(1 for e in inventory.load(self.V)["entries"] if e["status"] == "active" and e["read"] == "ok")
        self.assertEqual(len(data["new"]), n_ok)
        for item in data["new"]:
            self.assertTrue(item["dir"] and item["filename_pattern"] and item["header"])
        self.assertEqual(data["skipped"]["credential"], 1)

    def test_human_lines_and_limit(self):
        r = self.kb("cards", "todo", "--limit", "2")
        lines = r.stdout.splitlines()
        n = len(self.todo()["new"])
        self.assertEqual(lines[0], f"待製卡 {n} 份（新 {n}、重做 0）；搬動 0；讀不到 0、原檔不見 0（見 系統/待處理.md）")
        self.assertEqual(lines[1], "不製卡：檔名像密碼或金鑰 1")
        items = [ln for ln in lines if ln.startswith("- ")]
        self.assertEqual(len(items), 2)
        self.assertRegex(items[0], r"^- 新 f-[0-9a-f]{8} \S.* → 檔案索引/.+/<標題>-[0-9a-f]{8}\.md$")
        self.assertEqual(lines[-1], f"……還有 {n - 2} 份")

    def test_default_limit_is_ingest_auto_max(self):
        items = [ln for ln in self.kb("cards", "todo").stdout.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(items), 5)

    def test_all_done_says_up_to_date(self):
        for item in self.todo()["new"]:
            _cards.write_card(self.V, item, "卡" + item["id"][2:6])
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").unlink()
        self.scan()
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertEqual(lines[0], "待製卡 0 份（新 0、重做 0）；搬動 0；讀不到 0、原檔不見 1（見 系統/待處理.md）")
        (self.raw / "公司行政" / "薪資" / "網銀密碼.txt").write_text("x", encoding="utf-8")

    def test_up_to_date_line(self):
        for item in self.todo()["new"]:
            _cards.write_card(self.V, item, "卡" + item["id"][2:6])
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertEqual(lines, ["卡都是最新的", "不製卡：檔名像密碼或金鑰 1"])

    def test_broken_counted_on_first_line(self):
        p = _cards.write_card(self.V, self.item(), "大安陳會議一")
        p.write_text(p.read_text(encoding="utf-8").replace("出處: 客戶原檔", "出處: 別的"), encoding="utf-8")
        first = self.kb("cards", "todo").stdout.splitlines()[0]
        self.assertTrue(first.endswith("；檔頭有問題 1 張"), first)
        self.assertNotIn("新 " + str(len(self.todo()["new"]) + 1), first)


class Sync(Base):
    def setUp(self):
        super().setUp()
        self.it = self.item()
        self.card = _cards.write_card(self.V, self.it, "大安陳會議一")
        self.before = self.card.read_text(encoding="utf-8")

    def test_move_to_other_dir(self):
        dst = self.raw / "客戶案件" / "信義王" / "會議紀錄_1.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        self.assertEqual(len(self.todo()["moved"]), 1)
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "sync")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "搬了 1 張卡、更新 0 張卡的原檔位置"), r.stderr)
        new = self.V / "檔案索引" / "客戶案件" / "信義王" / self.card.name
        self.assertFalse(self.card.exists())
        self.assertTrue(new.is_file())
        front = cards.parse_front(new.read_text(encoding="utf-8"))
        self.assertEqual(front["原檔"], ["file://客戶案件/信義王/會議紀錄_1.docx"])
        self.assertEqual(front["開原檔"], cards.open_link(dst))
        old_lines = [ln for ln in self.before.splitlines() if not ln.startswith(("  - ", "開原檔:"))]
        new_lines = [ln for ln in new.read_text(encoding="utf-8").splitlines() if not ln.startswith(("  - ", "開原檔:"))]
        self.assertEqual(old_lines, new_lines)
        m = self.manifest(t)
        self.assertEqual(m[self.card.relative_to(self.V).as_posix()], "delete")
        self.assertEqual(m[new.relative_to(self.V).as_posix()], "create")
        self.assertIn("總目錄.md", m)
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards sync", "搬了 1 張卡"))
        self.assertEqual(self.todo()["moved"], [])
        self.assertIn(new.name, (self.V / "總目錄.md").read_text(encoding="utf-8").replace("%20", " "))

    def test_same_dir_rename(self):
        dst = self.raw / "客戶案件" / "大安陳" / "會議紀錄_一.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        (moved,) = self.todo()["moved"]
        self.assertEqual(moved["to"], "檔案索引/客戶案件/大安陳")
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "sync")
        self.assertEqual(r.stdout.strip(), "搬了 0 張卡、更新 1 張卡的原檔位置")
        self.assertTrue(self.card.is_file())
        self.assertEqual(cards.parse_front(self.card.read_text(encoding="utf-8"))["原檔"],
                         ["file://客戶案件/大安陳/會議紀錄_一.docx"])
        rel = self.card.relative_to(self.V).as_posix()
        self.assertEqual(self.manifest(t)[rel], "modify")
        self.assertEqual(self.todo()["moved"], [])

    def test_target_exists_skipped(self):
        dst = self.raw / "客戶案件" / "信義王" / "會議紀錄_1.docx"
        (self.raw / _cards.MEETING).rename(dst)
        self.scan()
        clash = self.V / "檔案索引" / "客戶案件" / "信義王" / self.card.name
        clash.parent.mkdir(parents=True)
        clash.write_text("別人", encoding="utf-8")
        r = self.kb("cards", "sync")
        self.assertIn("已經有同名的卡", r.stdout)
        self.assertTrue(self.card.exists())
        self.assertEqual(clash.read_text(encoding="utf-8"), "別人")


class Drop(Base):
    def test_active_refused(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        r = self.kb("cards", "drop", e["id"])
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stdout.splitlines()[0].startswith("原檔還在："), r.stdout)

    def test_drop_then_revert(self):
        item = self.item()
        card = _cards.write_card(self.V, item, "大安陳會議一")
        (self.raw / _cards.MEETING).unlink()
        self.scan()
        (pend,) = [i for i in pending.load(self.V)["items"] if i["kind"] == "missing_file"]
        self.kb("cards", "index")
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "drop", item["id"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已拿掉「大安陳會議一」的卡；說「退回上一版」救得回來")
        self.assertFalse(card.exists())
        m = self.manifest(t)
        self.assertEqual(m[card.relative_to(self.V).as_posix()], "delete")
        for rel in (".system/inventory.json", ".system/pending.json", "系統/待處理.md", "總目錄.md"):
            self.assertIn(rel, m)
        e = next(x for x in inventory.load(self.V)["entries"] if x["id"] == item["id"])
        self.assertEqual(e["status"], "deleted")
        done = next(i for i in pending.load(self.V)["items"] if i["id"] == pend["id"])
        self.assertEqual(done["status"], "done")
        self.assertIn("說刪了", done["how"])
        self.assertIn("目前沒有等你決定的事。", (self.V / "系統" / "待處理.md").read_text(encoding="utf-8"))
        self.assertNotIn("大安陳會議一", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards drop", "拿掉「大安陳會議一」的卡（原檔已刪）"))
        r = self.kb("versions", "revert")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(card.is_file())
        e = next(x for x in inventory.load(self.V)["entries"] if x["id"] == item["id"])
        self.assertEqual(e["status"], "missing")
        back = next(i for i in pending.load(self.V)["items"] if i["id"] == pend["id"])
        self.assertEqual(back["status"], "open")
        self.assertIn("大安陳會議一", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        (m,) = self.todo()["missing"]
        self.assertEqual(m["id"], item["id"])

    def test_drop_without_card(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        (self.raw / _cards.MEETING).unlink()
        self.scan()
        r = self.kb("cards", "drop", e["id"])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(next(x for x in inventory.load(self.V)["entries"] if x["id"] == e["id"])["status"], "deleted")

    def test_unknown_id(self):
        r = self.kb("cards", "drop", "f-00000000")
        self.assertEqual(r.returncode, 3)


class Index(Base):
    def test_empty_then_three_then_drop(self):
        r = self.kb("cards", "index")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "總目錄：0 張卡"), r.stderr)
        text = (self.V / "總目錄.md").read_text(encoding="utf-8")
        self.assertIn("還沒有卡", text)
        self.assertTrue(text.rstrip().endswith("共 0 張卡"))
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards index", "總目錄：0 張卡"))
        self.assertEqual([c["path"] for c in prog["changed"]], ["總目錄.md"])
        pick = {i["uri"]: i for i in self.todo()["new"]}
        _cards.write_card(self.V, pick["file://" + _cards.MEETING], "大安陳 會議")
        _cards.write_card(self.V, pick["file://報價與合約/合約範本.txt"], "合約範本")
        _cards.write_card(self.V, pick["file://客戶案件/內湖李/估價單.pdf"], "內湖李估價單")
        self.assertEqual(self.kb("cards", "index").stdout.strip(), "總目錄：3 張卡")
        lines = (self.V / "總目錄.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("    - [大安陳 會議](檔案索引/客戶案件/大安陳/大安陳%20會議-"
                      f"{pick['file://' + _cards.MEETING]['id'][2:]}.md)", lines)
        self.assertEqual(lines[-1], "共 3 張卡")
        meeting = _cards.card_path(self.V, pick["file://" + _cards.MEETING], "大安陳 會議")
        meeting.write_text("壞了\n", encoding="utf-8")
        r = self.kb("cards", "index")
        self.assertEqual(r.returncode, 0)
        self.assertIn(f"[大安陳 會議-{pick['file://' + _cards.MEETING]['id'][2:]}]",
                      (self.V / "總目錄.md").read_text(encoding="utf-8"))


class AfterTest(Base):
    """測試後修正裁定 19、20、29、30（報告 C2、C3、C9、C10）。"""

    def test_19_source_relocated_sync_rewrites_link(self):
        item = self.item()
        card = _cards.write_card(self.V, item, "大安陳會議一")
        shutil.copytree(self.raw, self.tmp / "raw2")
        p = self.V / ".system" / "sources.json"
        p.write_text(p.read_text(encoding="utf-8").replace("../raw/客戶案件", "../raw2/客戶案件"), encoding="utf-8")
        self.scan()
        (moved,) = self.todo()["moved"]
        self.assertEqual(moved["to"], "檔案索引/客戶案件/大安陳")
        r = self.kb("cards", "sync")
        self.assertEqual(r.stdout.strip(), "搬了 0 張卡、更新 1 張卡的原檔位置")
        front = cards.parse_front(card.read_text(encoding="utf-8"))
        self.assertEqual(front["開原檔"], cards.open_link(self.tmp / "raw2" / _cards.MEETING))
        self.assertEqual(self.todo()["moved"], [])

    def test_20_sync_drops_duplicate(self):
        item = self.item()
        good = _cards.write_card(self.V, item, "大安陳會議一")
        wrong = self.V / "檔案索引" / "客戶案件" / "信義王" / good.name
        wrong.parent.mkdir(parents=True)
        wrong.write_text(good.read_text(encoding="utf-8"), encoding="utf-8")
        first = self.kb("cards", "todo").stdout.splitlines()[0]
        self.assertIn("同編號多張 1 組（跑 kb cards sync）", first)
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "sync")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("刪掉同編號的重複卡 1 張", r.stdout)
        self.assertFalse(wrong.exists())
        self.assertTrue(good.is_file())
        self.assertEqual(self.manifest(t)[wrong.relative_to(self.V).as_posix()], "delete")
        self.assertEqual(self.todo()["duplicates"], [])

    def test_20_sync_target_same_id_converges(self):
        """C3：卡放錯夾後在對的夾重寫，sync 不再每次跳過。"""
        item = self.item()
        good = _cards.write_card(self.V, item, "大安陳會議一")
        old = self.V / "檔案索引" / "客戶案件" / "信義王" / good.name
        old.parent.mkdir(parents=True)
        old.write_text(good.read_text(encoding="utf-8"), encoding="utf-8")
        self.kb("cards", "sync")
        data = self.todo()
        self.assertEqual((data["moved"], data["duplicates"]), ([], []))

    def test_29_read_error_second_line(self):
        e = _cards.entry_of(self.V, _cards.MEETING)
        data = inventory.load(self.V)
        for x in data["entries"]:
            if x["id"] == e["id"]:
                x["read"] = "error"
        inventory._write(self.V, data["entries"])
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertEqual(lines[1], "不製卡：檔名像密碼或金鑰 1、讀不到 1")

    def test_30_drop_lock_timeout_writes_nothing(self):
        from kb import cards as cards_cli
        from kb._lib import io as kio
        item = self.item()
        card = _cards.write_card(self.V, item, "大安陳會議一")
        (self.raw / _cards.MEETING).unlink()
        self.scan()
        inv_before = (self.V / ".system" / "inventory.json").read_bytes()
        lock = self.V / ".system" / "inventory.json.lock"
        lock.write_text("", encoding="utf-8")
        old = kio.LOCK_TIMEOUT
        kio.LOCK_TIMEOUT = 0.3
        try:
            with self.assertRaises(TimeoutError):
                cards_cli._drop(self.V, item["id"], False, True)
        finally:
            kio.LOCK_TIMEOUT = old
            lock.unlink()
        self.assertTrue(card.is_file())
        self.assertEqual((self.V / ".system" / "inventory.json").read_bytes(), inv_before)


PAY = "| 地址 | 只記種類 | 推測 | 2026-10-19 |\n"


class Skip(Base):
    """客戶設定檔與型別第 5、6 點、驗收 8：kb cards skip；todo 的 skipped.skipped；apply 拿掉種類後回到待製卡。"""

    def md(self):
        return self.V / "系統" / "客戶設定檔.md"

    def set_no_card(self, on=True):
        text = self.md().read_text(encoding="utf-8")
        row = "| 請款 | 不整理 |\n"
        text = text.replace(PAY, PAY + row) if on else "".join(
            ln for ln in text.splitlines(keepends=True) if not ln.startswith("| 請款 |"))
        self.md().write_text(text, encoding="utf-8", newline="\n")
        r = self.kb("profile", "apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def fid(self, rel=_cards.MEETING):
        return _cards.entry_of(self.V, rel)["id"]

    def entry(self, fid):
        return next(e for e in inventory.load(self.V)["entries"] if e["id"] == fid)

    def test_skip_then_todo_then_unskip(self):
        self.set_no_card()
        fid = self.fid()
        r = self.kb("cards", "skip", fid, "--kind", "請款")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(),
                         f"已記成不整理（請款）：file://{_cards.MEETING}；有卡的已拿掉，說「退回上一版」救得回來")
        self.assertEqual(self.entry(fid)["skip_kind"], "請款")
        data = self.todo()
        self.assertNotIn(fid, [i["id"] for i in data["new"]])
        self.assertEqual(data["skipped"]["skipped"], 1)
        self.assertEqual(self.kb("cards", "todo").stdout.splitlines()[1], "不製卡：檔名像密碼或金鑰 1、依設定不整理 1")
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("cards skip", "依設定不整理：請款（會議紀錄_1.docx）"))
        self.scan()
        self.assertEqual(self.entry(fid)["skip_kind"], "請款", "scan 後還在")
        self.assertEqual(self.set_no_card(False), ["已套用客戶設定檔：不整理與敏感 -1；1 份回到待製卡"])
        self.assertNotIn("skip_kind", self.entry(fid))
        self.assertIn(fid, [i["id"] for i in self.todo()["new"]])

    def test_skip_removes_existing_card(self):
        self.set_no_card()
        item = self.item()
        p = _cards.write_card(self.V, item, "大安陳第一次會議")
        self.kb("cards", "index")
        before = cards.render_index(self.V)
        t = turn.start(self.V, None, "")["turn"]
        r = self.kb("cards", "skip", item["id"], "--kind", "請款", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rel = p.relative_to(self.V).as_posix()
        self.assertEqual(json.loads(r.stdout), {"id": item["id"], "kind": "請款", "card": rel})
        self.assertFalse(p.exists())
        self.assertEqual(self.manifest(t)[rel], "delete")
        self.assertEqual(self.manifest(t)[inventory.INVENTORY_POSIX], "modify")
        self.assertEqual(cards.render_index(self.V), before - 1)
        self.assertEqual(self.programs()[-1]["changed"],
                         [{"path": inventory.INVENTORY_POSIX, "action": "modify"}, {"path": rel, "action": "delete"},
                          {"path": cards.INDEX_POSIX, "action": "modify"}])

    def test_refusals(self):
        self.set_no_card()
        fid = self.fid()
        r = self.kb("cards", "skip", fid, "--kind", "薪資")
        self.assertEqual((r.returncode, r.stdout.strip()),
                         (3, "「薪資」不在客戶設定檔的不整理種類裡，先在設定檔第三節加一列再套用"))
        cred = next(e for e in inventory.load(self.V)["entries"] if e["read"] == "credential")
        r = self.kb("cards", "skip", cred["id"], "--kind", "請款")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "這份不是待製卡的檔（active／credential）"))
        r = self.kb("cards", "skip", "f-00000000", "--kind", "請款")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "清冊沒有 f-00000000"))
        self.assertNotIn("skip_kind", self.entry(fid))


class TodoProfile(Base):
    """驗收 9：todo --json 的 profile 塊等於設定檔去掉來源日期；設定檔壞了用預設、profile_missing、人讀多一行、exit 0。"""

    def test_profile_block(self):
        from kb._lib import profile
        data = self.todo()
        self.assertEqual(data["profile"], profile.card_types_for_todo(profile.load(self.V)))
        self.assertFalse(data["profile_missing"])
        self.assertEqual(data["skipped"]["skipped"], 0)

    def test_broken_profile_uses_defaults(self):
        from kb._lib import profile
        (self.V / ".system" / "profile.json").write_text("{bad", encoding="utf-8")
        data = self.todo()
        self.assertEqual(data["profile"], profile.card_types_for_todo(profile.defaults()))
        self.assertTrue(data["profile_missing"])
        r = self.kb("cards", "todo")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines()[-1], "客戶設定檔讀不到，卡型用預設")


class SkipLock(Base):
    """測試後修正裁定 13：skip 先拿清冊的鎖，等不到時卡與清冊都沒動。"""

    md = Skip.md
    set_no_card = Skip.set_no_card

    def test_lock_timeout_touches_nothing(self):
        from kb import cards as cards_cli
        from kb._lib import io as kio
        self.set_no_card()
        item = self.item()
        card = _cards.write_card(self.V, item, "大安陳會議一")
        t = turn.start(self.V, None, "")["turn"]
        inv_before = (self.V / ".system" / "inventory.json").read_bytes()
        lock = self.V / ".system" / "inventory.json.lock"
        lock.write_text("", encoding="utf-8")
        old = kio.LOCK_TIMEOUT
        kio.LOCK_TIMEOUT = 0.3
        try:
            self.assertEqual(cards_cli._skip(self.V, item["id"], "請款", False, True), 3)
        finally:
            kio.LOCK_TIMEOUT = old
            lock.unlink()
        self.assertTrue(card.is_file())
        self.assertEqual((self.V / ".system" / "inventory.json").read_bytes(), inv_before)
        self.assertEqual(turn.entries(self.V, t), [])


class EntityParts(unittest.TestCase):
    """實體與綜合卡第 4、5 點、驗收 2、3、4、6：第二版 header、待掛實體、型別表沒有、綜合卡待建／待更新、實體夾有變；
    drop 一張依據卡後綜合卡的 removed 有它。"""

    def setUp(self):
        from tests import _entities
        self.E = _entities
        self.tmp, self.V, self.raw = _entities.make_entity_vault(with_entities=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb(*args, cwd=self.V)

    def todo(self):
        return json.loads(self.kb("cards", "todo", "--json").stdout)

    def first(self):
        return self.kb("cards", "todo").stdout.splitlines()[0]

    def test_v2_header_and_prefill(self):
        data = self.todo()
        fid = _cards.entry_of(self.V, self.E.CONTRACT)["id"]
        header = next(i for i in data["new"] if i["id"] == fid)["header"]
        self.assertIn("\n實體: [信義王]\n待補型別: []\n", header)
        self.assertIn("\n範本版本: 2\n", header)
        other = next(i for i in data["new"] if i["id"] != fid)["header"]
        self.assertIn("\n實體: []\n", other)

    def test_first_line_parts_and_missing_types(self):
        rel = self.E.write_card(self.V, self.E.QUOTE, "信義王報價", [])
        self.E.to_v1(self.V, rel)
        self.E.write_card(self.V, self.E.ESTIMATE, "順發請款", ["王宅"], pending=["廠商"])
        self.E.write_card(self.V, self.E.DRAFT, "王宅會議", ["王宅", "信義王"])
        (self.V / "案子" / "大安陳").rename(self.V / "案子" / "陳家")
        lines = self.kb("cards", "todo").stdout.splitlines()
        self.assertTrue(lines[0].endswith("；待掛實體 1；綜合卡 待建 1（待確認 1）、待更新 1；實體夾有變 1"), lines[0])
        self.assertEqual(lines[1], "型別表沒有：廠商 1 張")

    def test_threshold_confirm_and_config(self):
        self.E.write_card(self.V, self.E.QUOTE, "王宅報價", ["王宅"])
        self.assertEqual(self.todo()["synthesis_new"], [])
        self.E.write_card(self.V, self.E.ESTIMATE, "王宅估價", ["王宅"])
        (item,) = self.todo()["synthesis_new"]
        self.assertFalse(item["confirmed"])
        self.assertEqual(self.kb("entity", "confirm", "王宅").returncode, 0)
        (item,) = self.todo()["synthesis_new"]
        self.assertTrue(item["confirmed"])
        (self.V / ".system" / "config.json").write_text('{"synthesis_min_cards": 3}', encoding="utf-8")
        self.assertEqual(self.todo()["synthesis_new"], [])

    def test_drop_card_shows_in_removed_and_cache_rewritten(self):
        from kb._lib import entities, synthesis
        self.E.write_card(self.V, self.E.QUOTE, "信義王報價", ["信義王"])
        self.E.write_card(self.V, self.E.ESTIMATE, "信義王估價", ["信義王"])
        data = entities.load(self.V)
        mounts = entities.mounts(self.V, data)
        e = next(x for x in data["entities"] if x["id"] == self.E.XINYI)
        card = self.V / "案子" / "信義王" / "信義王.md"
        card.write_text(self.E.synthesis_text(synthesis.header_for(self.V, e, mounts["信義王"], "2026-10-20"), "信義王"),
                        encoding="utf-8")
        self.assertEqual(self.todo()["synthesis_stale"], [])
        fid = _cards.entry_of(self.V, self.E.ESTIMATE)["id"]
        (self.raw / self.E.ESTIMATE).unlink()
        self.assertEqual(self.kb("inventory", "scan").returncode, 0)
        r = self.kb("cards", "drop", fid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (item,) = self.todo()["synthesis_stale"]
        self.assertEqual([x["id"] for x in item["removed"]], [fid])
        self.assertEqual([x.split(" ", 2)[2] for x in cards.parse_front(card.read_text(encoding="utf-8"))["檔案卡"]],
                         ["信義王報價"])


if __name__ == "__main__":
    unittest.main()
