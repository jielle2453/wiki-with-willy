"""_lib/lookup 與 kb lookup（answer 第 1 點，驗收 1～3、14）：名稱、別名、合併轉址；沒登記走 mentions（第一版的卡也找得到）；
狀態檔不在當空的；不採用標 ruled_out、口述為準排前面；原檔改過與 redo 的卡 stale；推算帶 stale；unsorted 跟 todo 第一行一致；
全程不寫檔（全樹雜湊）；人讀第一行照格式、不印編號，--json 有。"""
import hashlib
import json
import re
import shutil
import unittest

from tests import _cards
from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import agenda, cards, findings, lookup, rulings, statements, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

ID = re.compile(r"[aefsrcp]-[0-9a-f]{8}")
OWNER = "e-0a0b0c0d"


def tree_hash(V):
    h = hashlib.sha256()
    for p in sorted(V.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(V).as_posix().encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def write_card(V, rel, title, names=(), kind="報價", date="2026-10-01", body="內容。〔第 1 頁〕\n"):
    item = E.new_item(V, rel)
    p = _cards.card_path(V, item, title)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = E.with_entities(_cards.card_text(item, title, body=body, kind=kind, date=date), names)
    p.write_text(text, encoding="utf-8", newline="\n")
    return item["id"], p.relative_to(V).as_posix()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        data = json.loads((self.V / ".system" / "entities.json").read_text(encoding="utf-8"))["entities"]
        E.put_entities(self.V, data + [E.entity(OWNER, "王先生", "業主", aliases=["王董"])])
        self.t = turn.start(self.V, None, "")["turn"]
        self.quote, _ = write_card(self.V, E.QUOTE, "信義王報價第二版", ["信義王", "王先生"], date="2026-10-01")
        self.contract, _ = write_card(self.V, E.CONTRACT, "信義王合約", ["信義王"], kind="合約", date="2026-09-20")
        self.est, est_rel = write_card(self.V, E.ESTIMATE, "內湖李估價單", [], kind="估價單", date="2026-03-02",
                                       body="業主李先生，木作 18 萬。〔第 1 頁〕\n")
        E.to_v1(self.V, est_rel)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def change_contract(self):
        """知識庫裡的合約.docx 換內容再掃：卡上的指紋不再是清冊現行的。"""
        from tests import _ooxml
        _ooxml.docx(self.V / "案子" / "信義王" / "合約.docx", [("p", "改過的合約")])
        _cards.scan_apply(self.V)

    def todo_unsorted(self):
        data = cards.todo(self.V, sources_lib.load(self.V))
        return len(data["new"]) + len(data["stale"])

    def kb(self, *args, code=0):
        r = run_kb("lookup", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout


class Find(Base):
    def test_alias_gives_entity_and_material(self):
        got = lookup.lookup(self.V, "王董")
        self.assertEqual(got["entity"], {"name": "王先生", "type": "業主", "category": "對象", "self": False,
                                         "aliases": ["王董"], "progress": None})
        self.assertIsNone(got["synthesis"])
        self.assertEqual([c["id"] for c in got["cards"]], [self.quote])
        self.assertEqual(set(got["cards"][0]), {"id", "title", "card_type", "date", "entities", "read", "path", "stale",
                                                "redo", "ruled_out"})
        self.assertEqual((got["statements"], got["rulings"], got["findings"], got["agenda"], got["mentions"]),
                         ([], [], [], [], []))
        self.assertEqual(got["unsorted"], self.todo_unsorted())

    def test_matter_has_progress_synthesis_and_cards_newest_first(self):
        got = lookup.lookup(self.V, "信義王宅")
        self.assertEqual((got["entity"]["name"], got["entity"]["progress"]), ("信義王", "ongoing"))
        self.assertEqual(got["synthesis"], "案子/信義王/信義王.md")
        self.assertEqual([c["id"] for c in got["cards"]], [self.quote, self.contract])
        self.assertEqual([c["card_type"] for c in got["cards"]], ["報價", "合約"])

    def test_merged_redirects(self):
        data = json.loads((self.V / ".system" / "entities.json").read_text(encoding="utf-8"))["entities"]
        E.put_entities(self.V, data + [E.entity("e-99999999", "王家", status="merged", merged_into=E.XINYI)])
        self.assertEqual(lookup.lookup(self.V, "王家")["entity"]["name"], "信義王")

    def test_self(self):
        got = lookup.lookup(self.V, "林小姐")
        self.assertEqual((got["entity"]["name"], got["entity"]["self"], got["synthesis"]), ("林小姐室內設計", True, None))


class NotRegistered(Base):
    def test_v1_card_found_by_title_and_body(self):
        got = lookup.lookup(self.V, "內湖李")
        self.assertIsNone(got["entity"])
        self.assertIsNone(got["synthesis"])
        self.assertEqual([(m["id"], m["where"]) for m in got["mentions"]], [(self.est, "標題")])
        self.assertEqual(got["mentions_total"], 1)
        body = lookup.lookup(self.V, "李先生")
        self.assertEqual([(m["id"], m["where"]) for m in body["mentions"]], [(self.est, "正文")])

    def test_mentions_capped_by_config(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"lookup_mentions_max": 1}), encoding="utf-8")
        got = lookup.lookup(self.V, "信義王報價")
        self.assertIsNone(got["entity"])
        got = lookup.lookup(self.V, "第 1 頁")
        self.assertEqual((len(got["mentions"]), got["mentions_total"]), (1, 3))

    def test_nothing_human_line(self):
        out = self.kb("沒這個").splitlines()
        want = lookup.NOTHING + (f"；還沒整理 {self.todo_unsorted()} 份" if self.todo_unsorted() else "")
        self.assertEqual(out, [want])
        got = json.loads(self.kb("沒這個", "--json"))
        self.assertEqual((got["entity"], got["cards"], got["mentions"], got["mentions_total"]), (None, [], [], 0))


class Knowledge(Base):
    def test_rulings_ruled_out_and_spoken_first(self):
        banned = rulings.add(self.V, "不採用", [], [self.contract], "合約是舊的，別信", self.t)
        spoken = rulings.add(self.V, "口述為準", ["信義王"], [], "信義王的報價以口述為準", self.t)
        got = lookup.lookup(self.V, "信義王")
        self.assertEqual([r["id"] for r in got["rulings"]], [spoken["id"], banned["id"]])
        self.assertEqual(got["rulings"][1]["cards"], [{"id": self.contract, "title": "信義王合約"}])
        self.assertEqual({c["id"]: c["ruled_out"] for c in got["cards"]}, {self.quote: False, self.contract: True})
        listed = cards.listing(self.V)
        self.assertEqual({c["id"]: c["ruled_out"] for c in listed["cards"]}[self.contract], True)

    def test_statements_findings_agenda(self):
        s, _ = statements.add(self.V, "信義王改用系統櫃", ["信義王"], self.t)
        c, _ = findings.add(self.V, "信義王報價合計 182 萬", "兩份相加", ["信義王"], [self.quote], self.t)
        a, _ = agenda.add(self.V, "到期", "2030-01-05", "交平面圖", ["信義王"], self.t)
        done, _ = agenda.add(self.V, "到期", "2030-01-06", "交立面圖", ["信義王"], self.t)
        agenda.set_status(self.V, done["id"], "done", self.t)
        got = lookup.lookup(self.V, "信義王")
        self.assertEqual(got["statements"], [{"id": s["id"], "text": "信義王改用系統櫃", "date": s["date"]}])
        self.assertEqual(got["findings"], [{"id": c["id"], "text": "信義王報價合計 182 萬", "how": "兩份相加",
                                            "basis": [{"id": self.quote, "title": "信義王報價第二版"}],
                                            "date": c["date"], "stale": False}])
        self.assertEqual([(i["id"], i["kind"], i["date"], i["status"]) for i in got["agenda"]],
                         [(a["id"], "到期", "2030-01-05", "open")])

    def test_finding_stale_after_new_card(self):
        c, _ = findings.add(self.V, "王先生有一份報價", "數卡", ["王先生"], [self.quote], self.t)
        self.assertFalse(lookup.lookup(self.V, "王董")["findings"][0]["stale"])
        write_card(self.V, E.DRAFT, "王先生會議", ["王先生"], kind="會議紀錄")
        self.assertTrue(lookup.lookup(self.V, "王董")["findings"][0]["stale"])
        self.assertEqual(lookup.lookup(self.V, "王董")["findings"][0]["id"], c["id"])


class Stale(Base):
    def test_changed_original_and_redo(self):
        self.change_contract()
        got = {c["id"]: (c["stale"], c["redo"]) for c in lookup.lookup(self.V, "信義王")["cards"]}
        self.assertEqual(got, {self.quote: (False, False), self.contract: (True, False)})
        cards.mark_redo(self.V, self.quote, self.t)
        got = {c["id"]: (c["stale"], c["redo"]) for c in lookup.lookup(self.V, "信義王")["cards"]}
        self.assertEqual(got[self.quote], (True, True))
        self.assertEqual(lookup.lookup(self.V, "信義王")["unsorted"], self.todo_unsorted())


class ReadOnly(Base):
    def test_whole_tree_unchanged(self):
        statements.add(self.V, "信義王改用系統櫃", ["信義王"], self.t)
        before = tree_hash(self.V)
        for q in ("王董", "信義王", "內湖李", "沒這個"):
            lookup.lookup(self.V, q)
            self.kb(q)
            self.kb(q, "--json")
        self.assertEqual(tree_hash(self.V), before)

    def test_missing_state_files_are_empty(self):
        for name in ("statements.json", "rulings.json", "findings.json", "agenda.json", "pending.json"):
            self.assertFalse((self.V / ".system" / name).exists())
        got = lookup.lookup(self.V, "信義王")
        self.assertEqual((got["statements"], got["rulings"], got["findings"], got["agenda"]), ([], [], [], []))

    def test_broken_state_file_exit_3(self):
        (self.V / ".system" / "findings.json").write_text("{", encoding="utf-8")
        out = self.kb("信義王", code=3)
        self.assertTrue(out.startswith("狀態檔讀不到："), out)


class Human(Base):
    def test_first_line_format_and_no_ids(self):
        statements.add(self.V, "信義王改用系統櫃", ["信義王"], self.t)
        findings.add(self.V, "信義王報價合計 182 萬", "兩份相加", ["信義王"], [self.quote, self.contract], self.t)
        rulings.add(self.V, "不採用", [], [self.contract], "合約是舊的，別信", self.t)  # 先記推算、後說別信（裁定 15 之後依據不能是別信的卡）
        agenda.add(self.V, "到期", "2030-01-05", "交平面圖", ["信義王"], self.t)
        self.change_contract()
        n = self.todo_unsorted()
        out = self.kb("信義王宅")
        lines = out.splitlines()
        self.assertEqual(lines[0], "信義王（案子，進行中）：綜合卡有、檔案卡 2 張（1 張原檔改過、1 張你說別信）、口述 1 條、"
                                   f"裁示 1 條、推算 1 筆、日程 1 條；還沒整理 {n} 份")
        self.assertIn("- 2026-10-01 信義王報價第二版（報價，完整）", lines)
        self.assertIn("- 2026-09-20 信義王合約（合約，完整）〔原檔改過〕〔你說別信〕", lines)
        self.assertIn("- 不採用：合約是舊的，別信（「信義王合約」）", lines)
        self.assertIsNone(ID.search(out), ID.search(out))
        self.assertTrue(ID.search(self.kb("信義王宅", "--json")))

    def test_not_registered_lists_mentions_without_ids(self):
        out = self.kb("內湖李")
        self.assertTrue(out.splitlines()[0].startswith("沒有登記「內湖李」；有 1 張卡提到它"), out)
        self.assertIn("- 內湖李估價單（標題）", out.splitlines())
        self.assertIsNone(ID.search(out))



class Round2(Base):
    """answer 測試後修正裁定 18、22、23：rulings 帶日期（短標〔你 10/21 說的〕）；--json 帶 verify_max；
    其他例外 exit 3 一句白話、不印 traceback 與絕對路徑。"""

    def test_ruling_date_and_verify_max(self):
        r = rulings.add(self.V, "口述為準", ["信義王"], [], "信義王的報價以口述為準", self.t)
        got = lookup.lookup(self.V, "信義王")
        self.assertEqual(got["rulings"][0]["date"], r["date"])
        self.assertEqual(got["verify_max"], 20)
        self.assertEqual(json.loads(self.kb("沒這個", "--json"))["verify_max"], 20)

    def test_other_errors_exit_3_without_paths(self):
        p = self.V / ".system" / "inventory.json"
        p.unlink()
        p.mkdir()
        out = self.kb("信義王", code=3)
        self.assertNotIn("Traceback", out)
        self.assertNotIn(str(self.V), out)
        self.assertNotIn(str(self.V).replace("\\", "/"), out)
        self.assertEqual(len(out.strip().splitlines()), 1, out)


if __name__ == "__main__":
    unittest.main()
