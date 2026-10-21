"""_lib/findings（answer 第 3、4 點，D-154、D-155）：推算清單的驗證、replaces、withdraw、兩種 stale（依據卡指紋、掛的卡變多；
掛客戶自己的看全庫）、實體改名後照找得到；綜合卡接推算：整理到收 c- 行、synthesis_stale 的 added／removed 帶 source finding、
項目帶 findings、推算不算進建卡門檻。"""
import json
import shutil
import unittest

from tests import _cards
from tests import _entities as E
from kb._lib import cards, entities, findings, synthesis, turn  # noqa: E402

XINYI_REL = "案子/信義王/信義王.md"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]
        self.quote = self.card(E.QUOTE, "信義王報價", ["信義王"])
        self.est = self.card(E.ESTIMATE, "信義王估價", ["信義王"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, rel, title, names, date="2026-10-01"):
        path = E.write_card(self.V, rel, title, names, date=date)
        return cards.parse_front((self.V / path).read_text(encoding="utf-8"))["編號"]

    def add(self, text="信義王報價合計 182 萬", how="兩份報價相加", names=("信義王",), basis=None, replaces=None):
        basis = [self.quote, self.est] if basis is None else basis
        return findings.add(self.V, text, how, list(names), list(basis), self.t,
                            replaces=replaces)

    def items(self):
        return {c["id"]: c for c in findings.load(self.V)["findings"]}

    def stale(self, cid):
        return findings.stale(self.V, findings.find(findings.load(self.V), cid))


class Add(Base):
    def test_shape_backup_and_return(self):
        it, old = self.add()
        self.assertIsNone(old)
        self.assertRegex(it["id"], r"^c-[0-9a-f]{8}$")
        self.assertEqual((it["text"], it["how"], it["entities"], it["status"], it["replaced_by"], it["turn"]),
                         ("信義王報價合計 182 萬", "兩份報價相加", ["信義王"], "active", None, self.t))
        self.assertEqual([b["id"] for b in it["basis"]], [self.quote, self.est])
        self.assertTrue(all(len(b["fp"]) == 12 for b in it["basis"]))
        self.assertEqual(len(it["mounts_fp"]), 12)
        data = json.loads((self.V / findings.FINDINGS_POSIX).read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertIn(findings.FINDINGS_POSIX, {r["path"] for r in turn.entries(self.V, self.t)})
        self.assertFalse(self.stale(it["id"]))

    def test_alias_resolves_and_self_allowed(self):
        it, _old = self.add(names=("信義王宅", "林小姐"))
        self.assertEqual(it["entities"], ["信義王", "林小姐室內設計"])

    def test_refusals(self):
        cases = [
            ({"names": ("內湖李",)}, "實體清單沒有「內湖李」，先 kb entity add"),
            ({"basis": ["x"], "names": ()}, "實體清單沒有「」，先 kb entity add"),
            ({"basis": []}, "依據至少要一張卡（--basis）"),
            ({"basis": ["f-00000000"]}, "清冊沒有 f-00000000"),
            ({"basis": [_cards.entry_of(self.V, E.MEMO)["id"]]}, "這份還沒有檔案卡"),
            ({"text": "字" * 201}, "推算要一句話，最多 200 字"),
            ({"text": "一\n二"}, "推算要一句話，最多 200 字"),
            ({"how": "加 $1"}, "推算裡不能有 \" $ ` \\ 這四種字元"),
            ({"replaces": "c-00000000"}, "沒有這筆推算：c-00000000"),
        ]
        for kw, want in cases:
            with self.subTest(want=want):
                with self.assertRaises(findings.FindingRefused) as cm:
                    self.add(**kw)
                self.assertEqual(str(cm.exception), want)
        self.assertFalse((self.V / findings.FINDINGS_POSIX).exists())

    def test_replaces_and_withdraw(self):
        old, _ = self.add()
        new, was = self.add(text="信義王報價合計 180 萬", replaces=old["id"])
        self.assertEqual(was["id"], old["id"])
        items = self.items()
        self.assertEqual((items[old["id"]]["status"], items[old["id"]]["replaced_by"]), ("superseded", new["id"]))
        with self.assertRaises(findings.FindingRefused) as cm:
            self.add(replaces=old["id"])
        self.assertEqual(str(cm.exception), "這筆已經被取代了，改最新的那筆")
        got = findings.withdraw(self.V, new["id"], self.t)
        self.assertEqual(got["status"], "withdrawn")
        with self.assertRaises(findings.FindingRefused) as cm:
            findings.withdraw(self.V, new["id"], self.t)
        self.assertEqual(str(cm.exception), "這筆已經收回了")
        self.assertEqual(findings.active_for(findings.load(self.V), "信義王"), [])


class Stale(Base):
    def test_basis_card_rewritten_with_new_fingerprint(self):
        it, _ = self.add()
        (self.raw / E.ESTIMATE).write_bytes(b"%PDF-1.4 changed estimate")
        _cards.scan_apply(self.V)
        self.assertFalse(self.stale(it["id"]), "卡還沒重做，依據卡的指紋沒變")
        item = E.new_item(self.V, E.ESTIMATE)
        p = self.V / item["existing_card"]
        p.write_text(E.with_entities(_cards.card_text(item, "信義王估價"), ["信義王"]), encoding="utf-8", newline="\n")
        self.assertTrue(self.stale(it["id"]))

    def test_new_card_on_entity(self):
        it, _ = self.add(basis=[self.quote])
        self.card(E.DRAFT, "信義王會議", ["信義王"])
        self.assertTrue(self.stale(it["id"]))

    def test_new_card_elsewhere_is_not_stale_for_entity(self):
        it, _ = self.add(basis=[self.quote])
        self.card(E.DRAFT, "大安陳會議", ["大安陳"])
        self.assertFalse(self.stale(it["id"]))

    def test_self_sees_any_new_card(self):
        it, _ = self.add(names=("林小姐室內設計",), basis=[self.quote])
        self.card(E.DRAFT, "大安陳會議", ["大安陳"])
        self.assertTrue(self.stale(it["id"]))

    def test_rename_entity_still_found(self):
        it, _ = self.add()
        entities.rename(self.V, "信義王", "信義王家", None, self.t)
        data = findings.load(self.V)
        ent = entities.load(self.V)
        self.assertEqual([c["id"] for c in findings.active_for(data, "信義王家", ent)], [it["id"]])
        self.assertFalse(findings.stale(self.V, findings.find(data, it["id"])))


class Synthesis(Base):
    """綜合卡接推算（第 4 點）：同 note 改知識接口述那一套。"""

    def setUp(self):
        super().setUp()
        self.write_synthesis()

    def buckets(self):
        data = entities.load(self.V)
        return synthesis.buckets(self.V, data, entities.mounts(self.V, data), entities._profile(self.V))

    def header(self, name="信義王"):
        data = entities.load(self.V)
        mounts = entities.mounts(self.V, data)
        e, _ = entities.find(data, name)
        return synthesis.header_for(self.V, e, mounts.get(e["name"], []), "2026-10-21")

    def write_synthesis(self, header=None):
        (self.V / XINYI_REL).write_text(E.synthesis_text(header or self.header(), "信義王"), encoding="utf-8")
        self.assertEqual(self.buckets()["synthesis_stale"], [])

    def stale_item(self):
        (item,) = self.buckets()["synthesis_stale"]
        return item

    def test_added_with_source_and_item_findings(self):
        it, _ = self.add()
        item = self.stale_item()
        self.assertEqual(item["added"], [{"id": it["id"], "text": it["text"], "date": it["date"], "source": "finding"}])
        self.assertEqual(item["findings"], [{"id": it["id"], "text": it["text"], "how": it["how"],
                                             "basis": [{"id": self.quote, "title": "信義王報價"},
                                                       {"id": self.est, "title": "信義王估價"}],
                                             "date": it["date"], "stale": False}])
        front = cards.parse_front(item["header"])
        self.assertEqual(front["整理到"][0], f"{it['id']} -")
        self.assertEqual(front["整理到"], sorted(front["整理到"]))
        self.write_synthesis(item["header"])
        self.assertEqual(synthesis.validate(self.V, XINYI_REL), (True, ""))

    def test_replaces_removed_and_added(self):
        old, _ = self.add()
        self.write_synthesis(self.stale_item()["header"])
        new, _ = self.add(text="信義王報價合計 180 萬", replaces=old["id"])
        item = self.stale_item()
        self.assertEqual([(x["id"], x["source"]) for x in item["added"]], [(new["id"], "finding")])
        self.assertEqual(item["removed"], [{"id": old["id"], "text": old["text"], "date": old["date"], "source": "finding"}])
        self.write_synthesis(item["header"])
        findings.withdraw(self.V, new["id"], self.t)
        self.assertEqual([(x["id"], x["source"]) for x in self.stale_item()["removed"]], [(new["id"], "finding")])

    def test_findings_do_not_count_for_new_synthesis(self):
        data = entities.load(self.V)["entities"]
        for e in data:
            if e["id"] == E.WANG:
                e["source"] = "confirmed"
        E.put_entities(self.V, data)
        self.add(names=("王宅",), basis=[self.quote])
        self.assertEqual([i["name"] for i in self.buckets()["synthesis_new"]], [])

    def test_stale_finding_flagged_in_item(self):
        it, _ = self.add(basis=[self.quote])
        self.card(E.DRAFT, "信義王會議", ["信義王"])
        (got,) = self.stale_item()["findings"]
        self.assertEqual((got["id"], got["stale"]), (it["id"], True))


class RuledOut(Base):
    """answer 測試後修正裁定 15（F-3）：依據卡被不採用算過期；依據含不採用的卡拒絕。"""

    def test_ruled_out_after_add_makes_it_stale(self):
        from kb._lib import rulings
        it, _ = self.add()
        rulings.add(self.V, "不採用", [], [self.est], "估價是舊的，別信", self.t)
        self.assertTrue(self.stale(it["id"]))

    def test_add_with_ruled_out_basis_refused(self):
        from kb._lib import rulings
        rulings.add(self.V, "不採用", [], [self.est], "估價是舊的，別信", self.t)
        with self.assertRaises(findings.FindingRefused) as cm:
            self.add()
        self.assertEqual(str(cm.exception), "「信義王估價」你說過別信，不能當依據")
        self.assertFalse((self.V / findings.FINDINGS_POSIX).exists())


if __name__ == "__main__":
    unittest.main()
