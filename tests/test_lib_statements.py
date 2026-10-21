"""_lib/statements（note 改知識第 1 點，D-146）：add 的驗證與拒絕句一字不差、replaces、withdraw、active_for、
rename_entity、壞檔帶檔名、寫前備進回合。"""
import json
import shutil
import unittest

from tests import _entities as E
from tests._guard_fixture import FIXTURES
from kb._lib import statements, turn  # noqa: E402

FIX = FIXTURES / "knowledge"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self):
        return statements.load(self.V)["statements"]

    def refused(self, want, *args, **kw):
        with self.assertRaises(statements.StatementRefused) as cm:
            statements.add(self.V, *args, turn_id=self.t, **kw)
        self.assertEqual(str(cm.exception), want)


class Load(Base):
    def test_no_file_is_empty(self):
        self.assertEqual(statements.load(self.V), {"schema_version": 1, "statements": []})

    def test_fixture_loads(self):
        shutil.copy2(FIX / "statements.json", self.V / ".system" / "statements.json")
        self.assertEqual([s["id"] for s in self.items()], ["s-1a2b3c4d", "s-5e6f7a8b", "s-9c0d1e2f", "s-0a0b0c0d"])

    def test_broken_file_names_the_file(self):
        for text in ("{bad", "[]", '{"statements": {}}', '{"statements": [1]}', '{"schema_version": 9, "statements": []}'):
            with self.subTest(text=text):
                (self.V / ".system" / "statements.json").write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError) as cm:
                    statements.load(self.V)
                self.assertIn(".system/statements.json", str(cm.exception))


class Add(Base):
    def test_success_shape_and_backup(self):
        item, replaced = statements.add(self.V, "信義王改用系統櫃，不做木工", ["信義王"], self.t)
        self.assertIsNone(replaced)
        self.assertRegex(item["id"], r"^s-[0-9a-f]{8}$")
        self.assertEqual((item["text"], item["entities"], item["turn"], item["status"], item["replaced_by"]),
                         ("信義王改用系統櫃，不做木工", ["信義王"], self.t, "active", None))
        self.assertRegex(item["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self.items(), [item])
        self.assertIn(statements.STATEMENTS_POSIX, {r["path"] for r in turn.entries(self.V, self.t)})

    def test_alias_becomes_registered_name_and_self_is_allowed(self):
        item, _r = statements.add(self.V, "公司下個月搬家", ["林小姐", "信義王宅"], self.t)
        self.assertEqual(item["entities"], ["林小姐室內設計", "信義王"])

    def test_refusals_exact(self):
        self.refused("實體清單沒有「內湖李」，先 kb entity add", "內湖李改用系統櫃", ["內湖李"])
        self.refused("口述要一句話，最多 200 字", "字" * 201, ["信義王"])
        self.refused("口述要一句話，最多 200 字", "第一句\n第二句", ["信義王"])
        self.refused("口述要一句話，最多 200 字", "   ", ["信義王"])
        for ch in ('"', "$", "`", "\\"):
            with self.subTest(ch=ch):
                self.refused("口述裡不能有 \" $ ` \\ 這四種字元", f"單價{ch}8500", ["信義王"])
        self.refused("沒有這條口述：s-00000000", "王宅的聯絡人改成王太太", ["王宅"], replaces="s-00000000")
        self.assertFalse((self.V / ".system" / "statements.json").exists())

    def test_two_hundred_is_fine(self):
        item, _r = statements.add(self.V, "字" * 200, ["信義王"], self.t)
        self.assertEqual(len(item["text"]), 200)

    def test_removed_entity_is_refused(self):
        data = json.loads((self.V / ".system" / "entities.json").read_text(encoding="utf-8"))["entities"]
        for e in data:
            if e["name"] == "大安陳":
                e["status"] = "removed"
        E.put_entities(self.V, data)
        self.refused("實體清單沒有「大安陳」，先 kb entity add", "大安陳要改廚房", ["大安陳"])

    def test_replaces(self):
        old, _r = statements.add(self.V, "王宅的聯絡人是王先生", ["王宅"], self.t)
        new, replaced = statements.add(self.V, "王宅的聯絡人改成王太太", ["王宅"], self.t, replaces=old["id"])
        self.assertEqual(replaced["id"], old["id"])
        got = {s["id"]: s for s in self.items()}
        self.assertEqual((got[old["id"]]["status"], got[old["id"]]["replaced_by"]), ("superseded", new["id"]))
        self.assertEqual(got[new["id"]]["status"], "active")
        self.refused("這條已經被取代了，改最新的那條", "王宅的聯絡人改成王小姐", ["王宅"], replaces=old["id"])


class Withdraw(Base):
    def test_withdraw_and_refusals(self):
        item, _r = statements.add(self.V, "信義王改用系統櫃", ["信義王"], self.t)
        got = statements.withdraw(self.V, item["id"], self.t)
        self.assertEqual(got["status"], "withdrawn")
        self.assertEqual(self.items()[0]["status"], "withdrawn")
        with self.assertRaises(statements.StatementRefused) as cm:
            statements.withdraw(self.V, "s-00000000", self.t)
        self.assertEqual(str(cm.exception), "沒有這條口述：s-00000000")
        old, _r = statements.add(self.V, "王宅的聯絡人是王先生", ["王宅"], self.t)
        statements.add(self.V, "王宅的聯絡人改成王太太", ["王宅"], self.t, replaces=old["id"])
        with self.assertRaises(statements.StatementRefused) as cm:
            statements.withdraw(self.V, old["id"], self.t)
        self.assertEqual(str(cm.exception), "這條已經被取代了，改最新的那條")


class Queries(Base):
    def setUp(self):
        super().setUp()
        shutil.copy2(FIX / "statements.json", self.V / ".system" / "statements.json")

    def test_active_for_and_find(self):
        data = statements.load(self.V)
        self.assertEqual([s["id"] for s in statements.active_for(data, "王宅")], ["s-9c0d1e2f"])
        self.assertEqual([s["id"] for s in statements.active_for(data, "林小姐室內設計")], [])
        self.assertEqual(statements.find(data, "s-5e6f7a8b")["status"], "superseded")
        self.assertIsNone(statements.find(data, "s-ffffffff"))

    def test_rename_entity(self):
        hits = statements.rename_entity(self.V, {"王宅": "王公館"}, self.t)
        self.assertEqual(sorted(h["id"] for h in hits), ["s-5e6f7a8b", "s-9c0d1e2f"])
        self.assertEqual({s["id"]: s["entities"] for s in self.items()}["s-9c0d1e2f"], ["王公館"])
        self.assertIn(statements.STATEMENTS_POSIX, {r["path"] for r in turn.entries(self.V, self.t)})

    def test_rename_nothing_writes_nothing(self):
        p = self.V / ".system" / "statements.json"
        before = p.read_bytes()
        t2 = turn.start(self.V, None, "")["turn"]
        self.assertEqual(statements.rename_entity(self.V, {"不在": "誰"}, t2), [])
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(turn.entries(self.V, t2), [])

    def test_rename_without_file_does_nothing(self):
        (self.V / ".system" / "statements.json").unlink()
        self.assertEqual(statements.rename_entity(self.V, {"王宅": "王公館"}, self.t), [])
        self.assertFalse((self.V / ".system" / "statements.json").exists())


if __name__ == "__main__":
    unittest.main()
