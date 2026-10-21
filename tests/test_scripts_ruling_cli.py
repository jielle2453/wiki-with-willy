"""kb ruling 黑箱（note 改知識第 3 點，驗收 9、10、12、13）：成功與拒絕句一字不差、裁示.md、人讀不印編號、--json 有、
終端機記程式段、revert 回去、掛的實體列進待更新綜合卡。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, journal, rulings, turn  # noqa: E402

ID = r"[afsr]-[0-9a-f]{8}"
MD = "系統/裁示.md"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.rel = E.write_card(self.V, E.CONTRACT, "合約", ["信義王"])
        self.cid = cards.parse_front((self.V / self.rel).read_text(encoding="utf-8"))["編號"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("ruling", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def add_json(self, *args):
        r = run_kb("ruling", "add", *args, "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["ruling"]["id"]

    def items(self):
        return {r["id"]: r for r in rulings.load(self.V)["rulings"]}

    def md(self):
        return (self.V / MD).read_text(encoding="utf-8")

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def stale(self, name="信義王"):
        data = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        return next((i for i in data["synthesis_stale"] if i["name"] == name), None)


class Add(Base):
    def test_ruled_out(self):
        t = turn.start(self.V, None, "")["turn"]
        self.assertEqual(self.kb("add", "--kind", "不採用", "--card", self.cid, "--text", "合約.docx 是舊的，別信"),
                         ["已記下裁示（不採用）：「合約」—合約.docx 是舊的，別信"])
        (item,) = self.items().values()
        self.assertEqual((item["kind"], [c["id"] for c in item["cards"]]), ("ruled_out", [self.cid]))
        self.assertRegex(item["cards"][0]["fp"], r"^[0-9a-f]{12}$")
        self.assertIn(f"## 不採用\n- 「合約」：合約.docx 是舊的，別信（{item['date']}）", self.md())
        self.assertTrue({rulings.RULINGS_POSIX, MD} <= {r["path"] for r in turn.entries(self.V, t)})
        self.assertEqual([(p["note"], p["command"]) for p in self.programs()][-1],
                         ("記下裁示（不採用）：「合約」", "ruling add"))
        stale = self.stale()
        self.assertEqual([(x["id"], x["source"]) for x in stale["added"]][-1], (item["id"], "ruling"))
        self.assertEqual([r["id"] for r in stale["rulings"]], [item["id"]])

    def test_spoken_wins_then_remove(self):
        self.assertEqual(self.kb("add", "--kind", "口述為準", "--entity", "信義王", "--text", "信義王的報價以口述為準"),
                         ["已記下裁示（口述為準）：信義王"])
        rid = next(iter(self.items()))
        self.assertIn("- 信義王：信義王的報價以口述為準", self.md())
        self.assertIsNotNone(self.stale())
        self.assertEqual(self.kb("remove", rid), ["已收回裁示：信義王—信義王的報價以口述為準"])
        self.assertEqual(self.items()[rid]["status"], "revoked")
        self.assertNotIn("信義王的報價以口述為準", self.md())
        self.assertEqual(self.kb("remove", "r-00000000", code=3), ["沒有這條裁示：r-00000000"])

    def test_refusals_exact(self):
        cases = [
            (["--kind", "可以讀", "--card", self.cid, "--text", "密碼檔可以讀"], "「可以讀」「可以記」這兩種要等密碼與守門那一版"),
            (["--kind", "不採用", "--text", "別信"], "不採用要指定是哪幾張卡（--card）"),
            (["--kind", "口述為準", "--text", "以口述為準"], "口述為準要掛實體或卡"),
            (["--kind", "以後都這樣", "--card", self.cid, "--text", "x"], "裁示種類要是 口述為準、不採用 之一"),
            (["--kind", "不採用", "--card", "f-00000000", "--text", "別信"], "清冊沒有 f-00000000"),
            (["--kind", "口述為準", "--entity", "內湖李", "--text", "以口述為準"], "實體清單沒有「內湖李」，先 kb entity add"),
            (["--kind", "口述為準", "--entity", "信義王", "--text", "字" * 201], "裁示要一句話，最多 200 字"),
            (["--kind", "口述為準", "--entity", "信義王", "--text", "報價以 $(echo hi) 為準"], "裁示裡不能有 \" $ ` \\ 這四種字元"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("add", *args, code=3), [want])
        self.assertFalse((self.V / ".system" / "rulings.json").exists())


class Secrets(Base):
    def test_password_refused_and_left_nowhere(self):
        """測試第一輪後裁定 3（F-1）：裁示原話也不收密碼；rulings.json、裁示.md、工作日誌、ledger 都沒有那串。"""
        from tests.test_scripts_statement_cli import leaks
        self.assertEqual(self.kb("add", "--kind", "口述為準", "--entity", "信義王", "--text", "Wi-Fi 密碼 password: Abc12345678",
                                 code=3), ["裁示裡不要放密碼或帳號；這種要記的話等「密碼與守門」那一版"])
        self.assertEqual(leaks(self.V, "Abc12345678"), [])


class RecheckList(Base):
    def test_recheck_and_list(self):
        rid = self.add_json("--kind", "不採用", "--card", self.cid, "--text", "合約.docx 是舊的，別信")
        p = self.V / self.rel
        fp = self.items()[rid]["cards"][0]["fp"]
        p.write_text(p.read_text(encoding="utf-8").replace(fp, "c" * 12, 1), encoding="utf-8")
        self.assertEqual(self.kb("recheck", rid), ["已對過新版，還是不採用：「合約」"])
        self.assertEqual(self.items()[rid]["cards"][0]["fp"], "c" * 12)
        self.add_json("--kind", "口述為準", "--entity", "大安陳", "--text", "大安陳以口述為準")
        lines = self.kb("list")
        self.assertEqual(lines[-1], "有效裁示 2 條")
        self.assertIn("- 不採用：「合約」—合約.docx 是舊的，別信", lines[0])
        out = json.loads(run_kb("ruling", "list", "--json", cwd=self.V).stdout)
        self.assertEqual(len(out["rulings"]), 2)

    def test_empty_list(self):
        self.assertEqual(self.kb("list"), ["有效裁示 0 條"])


class NoIds(Base):
    def test_human_output_has_no_ids(self):
        out = self.kb("add", "--kind", "不採用", "--card", self.cid, "--text", "合約.docx 是舊的，別信")
        rid = next(iter(self.items()))
        out += self.kb("recheck", rid) + self.kb("list") + self.kb("remove", rid)
        for line in out:
            self.assertIsNone(re.search(ID, line), line)
        self.assertIsNone(re.search(ID, self.md()))
        self.assertRegex(self.add_json("--kind", "口述為準", "--entity", "信義王", "--text", "x"), ID)


class Revert(Base):
    def test_revert_add_restores_json_and_md(self):
        turn.start(self.V, None, "")
        self.add_json("--kind", "不採用", "--card", self.cid, "--text", "別信")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.V / ".system" / "rulings.json").exists())
        self.assertFalse((self.V / MD).exists())

    def test_revert_remove_brings_it_back(self):
        rid = self.add_json("--kind", "不採用", "--card", self.cid, "--text", "別信")
        before = self.md()
        turn.start(self.V, None, "")
        self.kb("remove", rid)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.items()[rid]["status"], "active")
        self.assertEqual(self.md(), before)


if __name__ == "__main__":
    unittest.main()
