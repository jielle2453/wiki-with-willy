"""kb profile type rename／merge／remove 黑箱（note 改規則第 2 點、驗收 7～12；D-139、D-142、D-143、D-144）。
<V> 是 tests/fixtures/inventory 的複本、全掃過；型別案子（事）、業主、工班、廠商（對象）；實體信義王、王先生、陳師傅、大成磁磚
各一張綜合卡；陳師傅的夾裡放一份客戶原檔 報價.txt，入冊後寫一張掛陳師傅的檔案卡。--check 不動任何檔；做了之後夾、實體、
綜合卡檔頭、設定檔、清冊、檔案卡位置與開原檔、總目錄都跟著改；中途出錯照 _Undo 還原；退回夾回去。"""
import json
import shutil
import unittest
import urllib.parse
from pathlib import Path

from tests import _cards
from tests import _entities as E
from tests.test_lib_inventory_scan import make_inv_vault
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, cards, entities, inventory, profile, synthesis, turn  # noqa: E402

TYPES = (("案子", "matter"), ("業主", "party"), ("工班", "party"), ("廠商", "party"))
MEMBERS = (("信義王", "案子"), ("王先生", "業主"), ("陳師傅", "工班"), ("大成磁磚", "廠商"))
RAW = "知識庫/工班/陳師傅/報價.txt"
SKIP = (".system/versions/", ".system/journal/", ".system/turns/", "系統/工作日誌/")


def tree(V, skip=SKIP):
    files, dirs = {}, set()
    for p in sorted(Path(V).rglob("*")):
        rel = p.relative_to(V).as_posix()
        if any((rel + "/").startswith(s) for s in skip) or rel.endswith((".lock", "hook-errors.log", "ledger.tsv")):
            continue
        if p.is_dir():
            dirs.add(rel)
        else:
            files[rel] = p.read_bytes()
    return files, dirs


def write_synthesis(V, name, t):
    data = entities.load(V)
    e, _hit = entities.find(data, name)
    header = synthesis.header_for(V, e, entities.mounts(V, data).get(name, []), E.DAY, data=data)
    p = Path(V) / entities.card_rel(e)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(E.synthesis_text(header, name), encoding="utf-8", newline="\n")
    entities.set_card(V, e["id"], True, t)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()
        _cards.scan_apply(self.V)
        E.set_profile(self.V, TYPES)
        t = turn.start(self.V, None, "")["turn"]
        for name, type_ in MEMBERS:
            entities.add(self.V, type_, name, t)
        raw = self.V / "工班" / "陳師傅" / "報價.txt"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("陳師傅的木作報價 12 萬\n", encoding="utf-8")
        _cards.scan_apply(self.V)
        item = E.new_item(self.V, RAW)
        p = _cards.card_path(self.V, item, "陳師傅報價")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(E.with_entities(_cards.card_text(item, "陳師傅報價", kind="報價"), ["陳師傅"]), encoding="utf-8",
                     newline="\n")
        self.card_id = item["id"]
        t = turn.start(self.V, None, "")["turn"]
        for name, _type in MEMBERS:
            write_synthesis(self.V, name, t)
        cards.render_index(self.V)
        _cards.mark_dates_scanned(self.V)
        self.turn = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("profile", "type", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r

    def check(self, *args, code=0):
        return json.loads(self.kb(*args, "--check", "--json", code=code).stdout)

    def data(self):
        return profile.load(self.V)

    def entity(self, name):
        return entities.find(entities.load(self.V), name)[0]

    def front(self, rel):
        return cards.parse_front((self.V / rel).read_text(encoding="utf-8"))

    def card_rel(self):
        return next(c["path"] for c in cards._scan_groups(self.V)[0][self.card_id])


class Rename(Base):
    def test_check_writes_nothing_and_counts(self):
        before = tree(self.V, ())
        got = self.check("rename", "工班", "--to", "師傅")
        self.assertEqual(tree(self.V, ()), before, "--check 不動任何檔（全樹比對）")
        self.assertEqual((got["op"], got["type"], got["target"], got["refusal"]), ("rename", "工班", "師傅", None))
        self.assertEqual(got["impact"], {"entities": 1, "file_cards": 1, "synthesis": 1,
                                         "folders": [{"from": "工班", "to": "師傅"}], "category_change": None})
        self.assertEqual(got["question"], "要把「工班」改名叫「師傅」嗎？1 個實體、1 張檔案卡、1 張綜合卡會跟著搬，資料夾「工班」會改成「師傅」。")
        self.assertEqual([(r["n"], r["label"], r["do"], r["strict"]) for r in got["results"]],
                         [(1, "改名叫師傅", ["kb profile type rename 工班 --to 師傅"], False), (2, "先不要", [], False)])
        human = self.kb("rename", "工班", "--to", "師傅", "--check").stdout.splitlines()
        self.assertIn("1. 改名叫師傅", human)

    def test_rename_follows_everywhere(self):
        r = self.kb("rename", "工班", "--to", "師傅")
        self.assertEqual(r.stdout.strip(), "已把型別「工班」改名叫「師傅」：1 個實體、1 張檔案卡跟著搬；不對可以說退回")
        self.assertFalse((self.V / "工班").exists())
        self.assertTrue((self.V / "師傅" / "陳師傅" / "報價.txt").is_file())
        self.assertEqual(self.entity("陳師傅")["type"], "師傅")
        self.assertEqual(self.front("師傅/陳師傅/陳師傅.md")["型別"], "師傅")
        data = self.data()
        self.assertEqual([t["name"] for t in data["types"]], ["案子", "業主", "師傅", "廠商"])
        self.assertEqual(data["types"][2]["source"], "corrected")
        self.assertIn("師傅", {s["type"] for s in data["synthesis_sections"]})
        self.assertNotIn("工班", {s["type"] for s in data["synthesis_sections"]})
        uris = {loc["uri"] for e in inventory.load(self.V)["entries"] if e["status"] == "active" for loc in e["locations"]}
        self.assertIn("file://知識庫/師傅/陳師傅/報價.txt", uris)
        self.assertNotIn("file://" + RAW, uris)
        rel = self.card_rel()
        self.assertTrue(rel.startswith("檔案索引/知識庫/師傅/陳師傅/"), rel)
        front = self.front(rel)
        self.assertEqual(front["原檔"], ["file://知識庫/師傅/陳師傅/報價.txt"])
        self.assertIn("/師傅/陳師傅/報價.txt", urllib.parse.unquote(front["開原檔"]))
        self.assertIn("師傅/陳師傅", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        self.assertEqual(profile.status(self.V)["unlisted"], [], "開場沒有「不在型別表」")
        self.assertEqual([(x["op"], x["from"], x["to"]) for x in turn.folders(self.V, self.turn)], [("move", "工班", "師傅")])

    def test_refusals_write_nothing(self):
        (self.V / "木工").write_text("x", encoding="utf-8")
        run_kb("profile", "add", "關於你", "名稱=林小姐室內設計", "是什麼=公司", cwd=self.V)
        before = tree(self.V, ())
        cases = [
            (("工班", "--to", "廠商"), "型別「廠商」已經有了，不能改成這個名字；要併在一起用合併"),
            (("工班", "--to", "木工"), "「木工」已經有同名的資料夾或檔，沒有改名；先把它搬走"),
            (("工班", "--to", "林小姐室內設計"), "「林小姐室內設計」是關於你裡的名稱，不能當型別名"),
            (("工班", "--to", "系統"), "型別名「系統」不能用（跟保留字一樣）"),
            (("水電", "--to", "師傅"), "型別表沒有「水電」"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("rename", *args, code=3).stdout.strip(), want)
                got = self.check("rename", *args, code=3)
                self.assertEqual((got["refusal"], got["results"]), (want, []))
        self.assertEqual(tree(self.V, ()), before)

    def test_revert_puts_everything_back(self):
        before = tree(self.V)
        self.kb("rename", "工班", "--to", "師傅")
        (self.V / "工班").mkdir()
        mid = tree(self.V)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "退不回去：「師傅」改回「工班」時「工班」已經有東西了，什麼都沒動"))
        self.assertEqual(tree(self.V), mid)
        (self.V / "工班").rmdir()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(tree(self.V), before)


class Merge(Base):
    def test_check_strict_and_impact(self):
        before = tree(self.V, ())
        got = self.check("merge", "工班", "--into", "廠商")
        self.assertEqual(tree(self.V, ()), before)
        self.assertEqual(got["impact"], {"entities": 1, "file_cards": 1, "synthesis": 1,
                                         "folders": [{"from": "工班/陳師傅", "to": "廠商/陳師傅"}, {"from": "工班", "to": None}],
                                         "category_change": None})
        self.assertEqual(got["question"], "要把「工班」併進「廠商」嗎？1 個實體、1 張檔案卡、1 張綜合卡會跟著搬，「工班」這一類會拿掉。")
        self.assertEqual([(r["label"], r["do"], r["strict"]) for r in got["results"]],
                         [("併進廠商", ["kb profile type merge 工班 --into 廠商"], True), ("先不要", [], False)])

    def test_merge_moves_and_drops_type(self):
        r = self.kb("merge", "工班", "--into", "廠商")
        self.assertEqual(r.stdout.strip(), "已把「工班」併進「廠商」：1 個實體、1 張檔案卡跟著搬；「工班」這一類拿掉了")
        self.assertTrue((self.V / "廠商" / "陳師傅" / "報價.txt").is_file())
        self.assertTrue((self.V / "廠商" / "陳師傅" / "陳師傅.md").is_file())
        self.assertFalse((self.V / "工班").exists())
        self.assertEqual(self.entity("陳師傅")["type"], "廠商")
        data = self.data()
        self.assertNotIn("工班", [t["name"] for t in data["types"]])
        self.assertNotIn("工班", {s["type"] for s in data["synthesis_sections"]})
        self.assertTrue(self.card_rel().startswith("檔案索引/知識庫/廠商/陳師傅/"))
        ops = [(x["op"], x["from"], x["to"]) for x in turn.folders(self.V, self.turn)]
        self.assertEqual(ops, [("move", "工班/陳師傅", "廠商/陳師傅"), ("remove", "工班", None)])
        before_revert = tree(self.V)
        self.assertEqual(run_kb("versions", "revert", cwd=self.V).returncode, 0)
        self.assertTrue((self.V / "工班" / "陳師傅" / "報價.txt").is_file())
        self.assertNotEqual(tree(self.V), before_revert)

    def test_ai_output_parent_is_recorded_and_reverted(self):
        """測試後修正裁定 18（F-7）：AI 產出/<被併> 搬進新建的 AI 產出/<目標>/，上層夾記 make，退回後不留空夾。"""
        draft = self.V / "AI 產出" / "工班" / "草稿.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("草稿\n", encoding="utf-8")
        self.kb("merge", "工班", "--into", "廠商")
        self.assertTrue((self.V / "AI 產出" / "廠商" / "工班" / "草稿.md").is_file())
        ops = [(x["op"], x["from"], x["to"]) for x in turn.folders(self.V, self.turn)]
        self.assertIn(("make", "AI 產出/廠商", None), ops)
        self.assertLess(ops.index(("make", "AI 產出/廠商", None)), ops.index(("move", "AI 產出/工班", "AI 產出/廠商/工班")))
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(draft.is_file())
        self.assertFalse((self.V / "AI 產出" / "廠商").exists())
        self.assertNotIn("裡有東西", r.stdout)

    def test_cross_category_keeps_progress(self):
        t = turn.start(self.V, None, "")["turn"]
        agenda.add(self.V, "期間", "2026-12-31", "施工", ["信義王"], t, start="2026-10-01")
        got = self.check("merge", "案子", "--into", "業主")
        self.assertEqual(got["impact"]["category_change"],
                         {"from": "事", "to": "對象", "progress_hidden": 1, "progress_added": 0, "periods": 1})
        self.assertTrue(got["question"].endswith("案子是『事』、業主是『對象』，併過去後 1 個的狀態不再顯示（資料留著）、1 條期間日程照留。"),
                        got["question"])
        self.kb("merge", "案子", "--into", "業主")
        xinyi = self.entity("信義王")
        self.assertEqual((xinyi["type"], xinyi["progress"]), ("業主", "ongoing"))
        lines = run_kb("entity", "list", cwd=self.V).stdout.splitlines()
        self.assertIn("- 信義王（業主，待確認）掛 0 張，有綜合卡", lines)
        run_kb("profile", "add", "型別", "名稱=工程", "大類=事", cwd=self.V)
        back = self.check("merge", "業主", "--into", "工程")
        self.assertEqual(back["impact"]["category_change"],
                         {"from": "對象", "to": "事", "progress_hidden": 0, "progress_added": 1, "periods": 0})
        self.assertIn("業主是『對象』、工程是『事』，併過去後 1 個會多出狀態『進行中（推測）』。", back["question"])
        self.kb("merge", "業主", "--into", "工程")
        lines = run_kb("entity", "list", cwd=self.V).stdout.splitlines()
        self.assertIn("- 信義王（工程，待確認，進行中）掛 0 張，有綜合卡", lines)

    def test_refusals(self):
        (self.V / "廠商" / "陳師傅").mkdir()
        before = tree(self.V, ())
        cases = [(("工班", "--into", "工班"), "工班跟工班是同一個"),
                 (("工班", "--into", "水電"), "型別表沒有「水電」"),
                 (("工班", "--into", "廠商"), "「廠商」底下已經有「陳師傅」，沒有合併；先改名或搬走")]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("merge", *args, code=3).stdout.strip(), want)
        self.assertEqual(tree(self.V, ()), before)


class Remove(Base):
    def test_remove_empty_type(self):
        run_kb("profile", "add", "型別", "名稱=設計師", "大類=對象", cwd=self.V)
        t = turn.start(self.V, None, "")["turn"]
        got = self.check("remove", "設計師")
        self.assertEqual([(r["label"], r["do"], r["strict"]) for r in got["results"]],
                         [("刪掉設計師這一類", ["kb profile type remove 設計師"], True), ("先不刪", [], False)])
        self.assertEqual(self.kb("remove", "設計師").stdout.strip(), "已拿掉型別「設計師」")
        self.assertFalse((self.V / "設計師").exists())
        self.assertNotIn("設計師", [x["name"] for x in self.data()["types"]])
        self.assertEqual([(x["op"], x["from"]) for x in turn.folders(self.V, t)], [("remove", "設計師")])

    def test_remove_with_entities(self):
        before = tree(self.V, ())
        self.assertEqual(self.kb("remove", "業主", code=3).stdout.strip(), "「業主」底下還有 1 個實體，先併到別的型別")
        got = self.check("remove", "業主")
        self.assertEqual(got["question"], "「業主」底下還有 1 個實體，要併到哪一類？")
        self.assertEqual([(r["label"], r["do"], r["strict"]) for r in got["results"]],
                         [("併到案子", ["kb profile type merge 業主 --into 案子"], True),
                          ("併到工班", ["kb profile type merge 業主 --into 工班"], True),
                          ("併到廠商", ["kb profile type merge 業主 --into 廠商"], True), ("先不刪", [], False)])
        self.assertEqual(tree(self.V, ()), before)


class Rollback(Base):
    """驗收 12：清冊寫入丟 OSError → 夾、設定檔、實體清單、綜合卡檔頭照 _Undo 回到操作前，資料夾帳沒有記。"""

    def run_in_process(self, *argv):
        import contextlib
        import io
        import os
        from kb import cli
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.V)
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(list(argv))
        finally:
            os.chdir(cwd)
        return code, out.getvalue().splitlines()

    def test_inventory_failure_rolls_back(self):
        from unittest import mock
        before = tree(self.V)
        for args in (("rename", "工班", "--to", "師傅"), ("merge", "工班", "--into", "廠商")):
            with self.subTest(args=args), \
                    mock.patch.object(inventory, "apply", side_effect=PermissionError(13, "被別的程式開著")):
                code, lines = self.run_in_process("profile", "type", *args)
                self.assertEqual(code, 3)
                self.assertEqual(lines, ["沒有寫入：PermissionError: [Errno 13] 被別的程式開著"])
                self.assertEqual(tree(self.V), before)
                self.assertEqual(turn.folders(self.V, turn.current(self.V)["turn"]), [])


if __name__ == "__main__":
    unittest.main()
