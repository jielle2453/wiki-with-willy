"""_lib/entities（實體與綜合卡第 1 點，D-114～D-124）：add 的冪等與四種拒絕、find 的 id／名稱／別名／merged 轉址、
drift 五種、sync_self 三種（新增、改記成自己、拿掉）、mounts 只算 active 的檔案卡與實體、load 的形狀。"""
import json
import shutil
import unittest
from pathlib import Path

from tests import _entities as E
from kb._lib import entities, turn  # noqa: E402


class Base(unittest.TestCase):
    with_entities = False

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(self.with_entities)
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def data(self):
        return entities.load(self.V)

    def by_name(self, name):
        return next(e for e in self.data()["entities"] if e["name"] == name)


class Load(Base):
    def test_missing_is_empty_and_bad_shapes_raise(self):
        self.assertEqual(entities.load(self.V), {"schema_version": 2, "entities": []})
        p = self.V / ".system" / "entities.json"
        for text in ("[]", "{}", '{"entities": [1]}', '{"schema_version": 3, "entities": []}', "{bad"):
            p.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                entities.load(self.V)

    def test_key_is_nfkc_casefold(self):
        self.assertEqual(entities.key("ＡＢＣ"), entities.key("abc"))


class Add(Base):
    def test_add_then_idempotent(self):
        e, created = entities.add(self.V, "案子", "信義王", self.t)
        self.assertTrue(created)
        self.assertRegex(e["id"], r"^e-[0-9a-f]{8}$")
        self.assertEqual({k: e[k] for k in ("type", "name", "aliases", "self", "status", "source", "card", "created_turn")},
                         {"type": "案子", "name": "信義王", "aliases": [], "self": False, "status": "active",
                          "source": "inferred", "card": False, "created_turn": self.t})
        self.assertIn(entities.ENTITIES_POSIX, {r["path"] for r in turn.entries(self.V, self.t)})
        p = self.V / ".system" / "entities.json"
        before = p.read_bytes()
        again, created = entities.add(self.V, "案子", "信義王", self.t)
        self.assertFalse(created)
        self.assertEqual(again["id"], e["id"])
        self.assertEqual(p.read_bytes(), before)

    def test_four_refusals(self):
        E.set_profile(self.V, about=(("林小姐室內設計", "company", ["林小姐"]),))
        entities.sync_self(self.V, [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"], "source": "interview",
                                     "date": E.DAY}], self.t)
        entities.add(self.V, "案子", "信義王", self.t)
        cases = [
            (("廠商", "順發"), "型別「廠商」不在型別表；在設定檔第一節加一列再套用"),
            (("案子", "a/b"), "名稱「a/b」不能用（含不能用的字元）"),
            (("業主", "信義王"), "已有「信義王」（案子）；是同一件就掛它，不是就加區分詞（例：信義王-大安）"),
            (("案子", "林小姐"), "「林小姐」是你自己（關於你），掛「林小姐室內設計」"),
        ]
        for (type_, name), want in cases:
            with self.subTest(want=want), self.assertRaises(entities.EntityRefused) as cm:
                entities.add(self.V, type_, name, self.t)
            self.assertEqual(str(cm.exception), want)

    def test_name_problem_sentences(self):
        data = self.data()
        self.assertIsNone(entities.name_problem(self.V, data, "王宅"))
        self.assertEqual(entities.name_problem(self.V, data, "x."), "名稱「x.」不能用（不能以點開頭，也不能以點或空白結尾）")
        self.assertEqual(entities.name_problem(self.V, data, "CON"), "名稱「CON」不能用（Windows 保留名）")
        self.assertEqual(entities.name_problem(self.V, data, "長" * 41), f"名稱「{'長' * 41}」不能用（太長，最多 40 字）")


class Find(Base):
    with_entities = True

    def test_id_name_alias_and_merged(self):
        data = self.data()
        self.assertEqual(entities.find(data, E.XINYI)[1], "id")
        e, hit = entities.find(data, "信義王")
        self.assertEqual((e["id"], hit), (E.XINYI, "name"))
        e, hit = entities.find(data, "信義王宅")
        self.assertEqual((e["id"], hit), (E.XINYI, "alias"))
        self.assertEqual(entities.find(data, "沒有這個"), (None, None))
        wang = next(x for x in data["entities"] if x["id"] == E.WANG)
        wang.update(status="merged", merged_into=E.XINYI)
        e, _hit = entities.find(data, "王宅")
        self.assertEqual(e["id"], E.XINYI)


class Mounts(Base):
    with_entities = True

    def test_mounts_resolve_names_and_skip_inactive(self):
        a = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "林小姐室內設計"])
        E.write_card(self.V, E.ESTIMATE, "內湖李估價", ["信義王"], date="未載")
        got = entities.mounts(self.V)
        self.assertEqual(sorted(m["title"] for m in got["信義王"]), ["信義王報價", "內湖李估價"])
        self.assertEqual(len(got["林小姐室內設計"]), 1)
        m = next(x for x in got["信義王"] if x["path"] == a)
        self.assertEqual(len(m["fp"]), 12)
        self.assertEqual(m["date"], "2026-10-01")
        from kb._lib import inventory
        from tests import _cards
        inventory.set_status(self.V, [_cards.entry_of(self.V, E.QUOTE)["id"]], "missing")
        self.assertEqual([x["title"] for x in entities.mounts(self.V)["信義王"]], ["內湖李估價"])


class Drift(Base):
    with_entities = True

    def kinds(self):
        return {d["name"]: (d["kind"], d["found"]) for d in entities.drift(self.V)}

    def test_no_drift_when_card_in_place(self):
        self.assertEqual(entities.drift(self.V), [])

    def test_five_kinds(self):
        v = self.V / "案子"
        (v / "信義王").rename(v / "王宅邸")
        self.assertEqual(self.kinds()["信義王"], ("folder_renamed", "案子/王宅邸/信義王.md"))
        (v / "王宅邸" / "信義王.md").rename(v / "王宅邸" / "王宅邸.md")
        self.assertEqual(self.kinds()["信義王"], ("both_renamed", "案子/王宅邸/王宅邸.md"))
        (v / "王宅邸").rename(v / "信義王")
        self.assertEqual(self.kinds()["信義王"], ("card_renamed", "案子/信義王/王宅邸.md"))
        (v / "信義王" / "王宅邸.md").rename(v / "信義王" / "信義王.md")
        shutil.move(str(v / "信義王"), str(self.V / "業主" / "信義王"))
        self.assertEqual(self.kinds()["信義王"], ("moved_type", "業主/信義王/信義王.md"))
        shutil.rmtree(self.V / "業主" / "信義王")
        self.assertEqual(self.kinds()["信義王"], ("missing", None))

    def test_only_card_true_active_plain(self):
        shutil.rmtree(self.V / "案子" / "大安陳")
        data = self.data()
        for e in data["entities"]:
            if e["id"] == E.DAAN:
                e["status"] = "removed"
        E.put_entities(self.V, data["entities"])
        self.assertEqual(entities.drift(self.V), [])

    def test_reads_only(self):
        (self.V / "案子" / "信義王").rename(self.V / "案子" / "王宅邸")
        before = {p: p.stat().st_mtime_ns for p in (self.V / ".system").rglob("*") if p.is_file()}
        entities.drift(self.V)
        self.assertEqual({p: p.stat().st_mtime_ns for p in (self.V / ".system").rglob("*") if p.is_file()}, before)


class SyncSelf(Base):
    def row(self, name="林小姐室內設計", kind="company", aliases=("林小姐",)):
        return {"name": name, "kind": kind, "aliases": list(aliases), "source": "interview", "date": E.DAY}

    def test_adds_self_entry(self):
        got = entities.sync_self(self.V, [self.row()], self.t)
        self.assertEqual(got["added"], ["林小姐室內設計"])
        e = self.by_name("林小姐室內設計")
        self.assertEqual((e["type"], e["self"], e["aliases"], e["source"], e["status"]),
                         (None, "company", ["林小姐"], "interview", "active"))
        p = self.V / ".system" / "entities.json"
        before = p.read_bytes()
        self.assertEqual(entities.sync_self(self.V, [self.row()], self.t),
                         {"added": [], "converted": [], "released": [], "notes": []})
        self.assertEqual(p.read_bytes(), before)

    def test_existing_plain_entry_converted(self):
        entities.add(self.V, "業主", "林小姐", self.t)
        got = entities.sync_self(self.V, [self.row()], self.t)
        self.assertEqual(got["converted"], ["林小姐室內設計"])
        self.assertEqual(got["notes"], ["「林小姐室內設計」改記成你自己；它原有的綜合卡之後不再更新"])
        data = self.data()
        self.assertEqual(len(data["entities"]), 1)
        e = data["entities"][0]
        self.assertEqual((e["name"], e["type"], e["self"], e["aliases"]), ("林小姐室內設計", None, "company", ["林小姐"]))

    def test_removed_row_released(self):
        entities.sync_self(self.V, [self.row()], self.t)
        got = entities.sync_self(self.V, [], self.t)
        self.assertEqual(got["released"], ["林小姐室內設計"])
        e = self.by_name("林小姐室內設計")
        self.assertEqual((e["self"], e["status"]), (False, "removed"))


if __name__ == "__main__":
    unittest.main()
