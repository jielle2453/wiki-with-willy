"""_lib/rulings（note 改知識第 2 點，D-149、D-150）：兩種的掛法驗證、憑證兩種拒絕、remove、recheck、drifted、for_entity、
rename_entity、render 等於 fixture；拒絕句一字不差。"""
import json
import shutil
import unittest

from tests import _cards
from tests import _entities as E
from tests._guard_fixture import FIXTURES
from kb._lib import cards, entities, rulings, turn  # noqa: E402

FIX = FIXTURES / "knowledge"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, rel=E.CONTRACT, title="合約", names=("信義王",)):
        path = E.write_card(self.V, rel, title, list(names))
        front = cards.parse_front((self.V / path).read_text(encoding="utf-8"))
        return front["編號"], (front.get("內容指紋") or front["指紋"])[:12], path

    def items(self):
        return rulings.load(self.V)["rulings"]

    def refused(self, want, *args):
        with self.assertRaises(rulings.RulingRefused) as cm:
            rulings.add(self.V, *args, turn_id=self.t)
        self.assertEqual(str(cm.exception), want)


class Load(Base):
    def test_no_file_is_empty_and_broken_names_file(self):
        self.assertEqual(rulings.load(self.V), {"schema_version": 1, "rulings": []})
        (self.V / ".system" / "rulings.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            rulings.load(self.V)
        self.assertIn(".system/rulings.json", str(cm.exception))

    def test_kinds(self):
        # 密碼與守門：「可以讀」開放、「可以記」拿掉（D-183）；可以讀的細節在 test_pw_rulings
        self.assertEqual(rulings.KINDS, {"口述為準": "spoken_wins", "不採用": "ruled_out", "可以讀": "credential_read"})
        self.assertEqual(rulings.ACTIVE_KINDS, ("spoken_wins", "ruled_out", "credential_read"))


class Add(Base):
    def test_ruled_out_records_card_fp_and_renders(self):
        cid, fp, _p = self.card()
        item = rulings.add(self.V, "不採用", [], [cid], "合約.docx 是舊的，別信", turn_id=self.t)
        self.assertRegex(item["id"], r"^r-[0-9a-f]{8}$")
        self.assertEqual((item["kind"], item["entities"], item["cards"], item["status"], item["revoked"], item["turn"]),
                         ("ruled_out", [], [{"id": cid, "fp": fp}], "active", None, self.t))
        md = (self.V / "系統" / "裁示.md").read_text(encoding="utf-8")
        self.assertIn(f"## 不採用\n- 「合約」：合約.docx 是舊的，別信（{item['date']}）\n", md)
        self.assertNotIn(cid, md)
        paths = {r["path"] for r in turn.entries(self.V, self.t)}
        self.assertTrue({rulings.RULINGS_POSIX, rulings.MD_POSIX} <= paths)

    def test_spoken_wins_on_entity(self):
        item = rulings.add(self.V, "口述為準", ["信義王宅"], [], "信義王的報價以口述為準", turn_id=self.t)
        self.assertEqual((item["kind"], item["entities"], item["cards"]), ("spoken_wins", ["信義王"], []))

    def test_refusals_exact(self):
        cid, _fp, _p = self.card()
        self.refused("裁示種類要是 口述為準、不採用、可以讀 之一", "以後都這樣", [], [cid], "x")
        self.refused("可以讀要指定是哪一份檔（--file）", "可以讀", [], [cid], "密碼檔可以讀")
        self.refused("裁示種類要是 口述為準、不採用、可以讀 之一", "可以記", ["信義王"], [], "這組密碼記下來")
        self.refused("不採用要指定是哪幾張卡（--card）", "不採用", ["信義王"], [], "信義王的報價別信")
        self.refused("口述為準要掛實體或卡", "口述為準", [], [], "以口述為準")
        self.refused("實體清單沒有「內湖李」，先 kb entity add", "口述為準", ["內湖李"], [], "以口述為準")
        self.refused("清冊沒有 f-00000000", "不採用", [], ["f-00000000"], "別信")
        self.refused("裁示裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員", "口述為準", ["信義王"], [],
                     "Wi-Fi 密碼 password: Abc12345678")
        self.refused("裁示要一句話，最多 200 字", "口述為準", ["信義王"], [], "字" * 201)
        self.refused("裁示要一句話，最多 200 字", "口述為準", ["信義王"], [], "   ")
        self.refused("裁示要一句話，最多 200 字", "口述為準", ["信義王"], [], "第一句\n第二句")
        for ch in ('"', "$", "`", "\\"):
            with self.subTest(ch=ch):
                self.refused("裁示裡不能有 \" $ ` \\ 這四種字元", "口述為準", ["信義王"], [], f"以口述為準{ch}")
        nocard = _cards.entry_of(self.V, E.QUOTE)["id"]
        self.refused("這份還沒有檔案卡", "不採用", [], [nocard], "別信")
        self.assertFalse((self.V / ".system" / "rulings.json").exists())


class Change(Base):
    def setUp(self):
        super().setUp()
        self.cid, self.fp, self.path = self.card()
        self.item = rulings.add(self.V, "不採用", [], [self.cid], "合約.docx 是舊的，別信", turn_id=self.t)

    def test_remove_revokes_and_rerenders(self):
        got = rulings.remove(self.V, self.item["id"], self.t)
        self.assertEqual(got["status"], "revoked")
        self.assertRegex(got["revoked"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual((self.V / "系統" / "裁示.md").read_text(encoding="utf-8").splitlines()[-1], "目前沒有裁示")
        with self.assertRaises(rulings.RulingRefused) as cm:
            rulings.remove(self.V, "r-00000000", self.t)
        self.assertEqual(str(cm.exception), "沒有這條裁示：r-00000000")

    def touch_card(self):
        p = self.V / self.path
        p.write_text(p.read_text(encoding="utf-8").replace(self.fp, "b" * 12, 1), encoding="utf-8")

    def test_drifted_and_recheck(self):
        self.assertEqual(rulings.drifted(self.V, rulings.load(self.V)), [])
        self.touch_card()
        (hit,) = rulings.drifted(self.V, rulings.load(self.V))
        self.assertEqual((hit["ruling"]["id"], [c["id"] for c in hit["cards"]]), (self.item["id"], [self.cid]))
        self.assertEqual(hit["cards"][0]["title"], "合約")
        self.assertEqual(hit["cards"][0]["file"], "合約.docx")
        got = rulings.recheck(self.V, self.item["id"], self.t)
        self.assertEqual(got["cards"][0]["fp"], ("b" * 12 + self.fp[12:])[:12])
        self.assertEqual(rulings.drifted(self.V, rulings.load(self.V)), [])

    def test_revoked_is_not_drifted(self):
        self.touch_card()
        rulings.remove(self.V, self.item["id"], self.t)
        self.assertEqual(rulings.drifted(self.V, rulings.load(self.V)), [])

    def test_for_entity_by_card_and_by_name(self):
        spoken = rulings.add(self.V, "口述為準", ["大安陳"], [], "大安陳的報價以口述為準", turn_id=self.t)
        data = entities.load(self.V)
        mounts = entities.mounts(self.V, data)
        rd = rulings.load(self.V)
        self.assertEqual([r["id"] for r in rulings.for_entity(rd, "信義王", mounts)], [self.item["id"]])
        self.assertEqual([r["id"] for r in rulings.for_entity(rd, "大安陳", mounts)], [spoken["id"]])
        rulings.remove(self.V, spoken["id"], self.t)
        self.assertEqual(rulings.for_entity(rulings.load(self.V), "大安陳", mounts), [])

    def test_rename_entity(self):
        spoken = rulings.add(self.V, "口述為準", ["信義王"], [], "信義王的報價以口述為準", turn_id=self.t)
        hits = rulings.rename_entity(self.V, {entities.key("信義王"): "王宅邸"}, self.t)
        self.assertEqual([h["id"] for h in hits], [spoken["id"]])
        self.assertEqual({r["id"]: r["entities"] for r in self.items()}[spoken["id"]], ["王宅邸"])
        self.assertIn("- 王宅邸：", (self.V / "系統" / "裁示.md").read_text(encoding="utf-8"))


class Render(Base):
    def test_fixture(self):
        data = json.loads((FIX / "rulings.json").read_text(encoding="utf-8"))
        got = rulings.render_text(data, {"f-07df81a8": "合約"})
        self.assertEqual(got, (FIX / "裁示.md").read_text(encoding="utf-8"))

    def test_empty(self):
        got = rulings.render_text({"schema_version": 1, "rulings": []}, {})
        self.assertTrue(got.endswith("\n\n目前沒有裁示\n"))
        self.assertTrue(got.startswith("# 裁示\n"))


if __name__ == "__main__":
    unittest.main()
