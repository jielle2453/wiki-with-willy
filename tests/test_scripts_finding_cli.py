"""kb finding 黑箱（answer 第 3 點，驗收 5、7、13、14）：成功與拒絕句一字不差、人讀不印編號、--json 有；
cards todo 把信義王列進待更新綜合卡、added 帶 source finding、項目帶 findings；照新檔頭寫綜合卡 PostToolUse validate 過；
replaces 與 withdraw；終端機記程式段；revert 回去。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_hooks_output_log import write_via_tools
from tests.test_lib_lookup import ID, write_card
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import findings, journal, turn  # noqa: E402

XINYI_REL = "案子/信義王/信義王.md"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        self.quote, _ = write_card(self.V, E.QUOTE, "信義王報價第二版", ["信義王"])
        self.contract, _ = write_card(self.V, E.CONTRACT, "信義王合約", ["信義王"], kind="合約")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("finding", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def add(self, *extra, text="信義王報價合計 182 萬", how="兩份報價相加"):
        r = run_kb("finding", "add", "--entity", "信義王", "--text", text, "--how", how, "--basis", self.quote,
                   self.contract, *extra, "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["finding"]["id"]

    def items(self):
        return {c["id"]: c for c in findings.load(self.V)["findings"]}

    def todo(self):
        return json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)


class Add(Base):
    def test_success_program_line_todo_and_validate(self):
        self.assertEqual(self.kb("add", "--entity", "信義王", "--text", "信義王報價合計 182 萬", "--how", "兩份報價相加",
                                 "--basis", self.quote, self.contract),
                         ["已記下推算：信義王報價合計 182 萬（信義王；依據 2 份）"])
        (item,) = self.items().values()
        programs = [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]
        self.assertEqual((programs[-1]["note"], programs[-1]["command"]), ("記下推算：信義王報價合計 182 萬", "finding add"))
        (stale,) = [i for i in self.todo()["synthesis_stale"] if i["name"] == "信義王"]
        self.assertIn({"id": item["id"], "text": item["text"], "date": item["date"], "source": "finding"}, stale["added"])
        self.assertEqual([c["id"] for c in stale["findings"]], [item["id"]])
        self.assertIn(f"  - {item['id']} -\n", stale["header"])
        turn.start(self.V, "test-sid", "整理")
        r = write_via_tools(self.V, self.V / XINYI_REL, E.synthesis_text(stale["header"], "信義王"))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stdout)

    def test_repeated_flags_also_work(self):
        self.assertEqual(self.kb("add", "--entity", "信義王", "--entity", "林小姐", "--text", "合計 182 萬", "--how", "相加",
                                 "--basis", self.quote, "--basis", self.contract),
                         ["已記下推算：合計 182 萬（信義王、林小姐室內設計；依據 2 份）"])

    def test_refusals_exact(self):
        base = ["--text", "合計 182 萬", "--how", "相加"]
        cases = [
            (["--entity", "內湖李", *base, "--basis", self.quote], "實體清單沒有「內湖李」，先 kb entity add"),
            (["--entity", "信義王", *base], "依據至少要一張卡（--basis）"),
            (["--entity", "信義王", *base, "--basis", "f-00000000"], "清冊沒有 f-00000000"),
            (["--entity", "信義王", "--text", "字" * 201, "--how", "相加", "--basis", self.quote], "推算要一句話，最多 200 字"),
            (["--entity", "信義王", "--text", "合計 $182", "--how", "相加", "--basis", self.quote],
             "推算裡不能有 \" $ ` \\ 這四種字元"),
            (["--entity", "信義王", *base, "--basis", self.quote, "--replaces", "c-00000000"], "沒有這筆推算：c-00000000"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("add", *args, code=3), [want])
        self.assertFalse((self.V / findings.FINDINGS_POSIX).exists())
        (self.V / findings.FINDINGS_POSIX).write_text("{", encoding="utf-8")
        out = self.kb("add", "--entity", "信義王", *base, "--basis", self.quote, code=3)
        self.assertTrue(out[0].startswith("狀態檔讀不到："), out)


class ReplaceWithdraw(Base):
    def test_replace_then_withdraw(self):
        old = self.add()
        self.assertEqual(self.kb("add", "--entity", "信義王", "--text", "信義王報價合計 180 萬", "--how", "扣掉重複的一項",
                                 "--basis", self.quote, "--replaces", old),
                         ["已改：信義王報價合計 182 萬 → 信義王報價合計 180 萬"])
        got = self.items()
        new = got[old]["replaced_by"]
        self.assertEqual((got[old]["status"], got[new]["status"]), ("superseded", "active"))
        self.assertEqual(self.kb("add", "--entity", "信義王", "--text", "x", "--how", "y", "--basis", self.quote,
                                 "--replaces", old, code=3), ["這筆已經被取代了，改最新的那筆"])
        self.assertEqual(self.kb("withdraw", new), ["已收回：信義王報價合計 180 萬"])
        self.assertEqual(self.items()[new]["status"], "withdrawn")
        self.assertEqual(self.kb("withdraw", "c-00000000", code=3), ["沒有這筆推算：c-00000000"])

    def test_todo_removed_and_added_after_replace(self):
        old = self.add()
        (stale,) = [i for i in self.todo()["synthesis_stale"] if i["name"] == "信義王"]
        (self.V / XINYI_REL).write_text(E.synthesis_text(stale["header"], "信義王"), encoding="utf-8")
        self.assertEqual(self.todo()["synthesis_stale"], [])
        new = self.add("--replaces", old, text="信義王報價合計 180 萬")
        (stale,) = self.todo()["synthesis_stale"]
        self.assertEqual([(x["id"], x["source"]) for x in stale["added"]], [(new, "finding")])
        self.assertEqual([(x["id"], x["source"]) for x in stale["removed"]], [(old, "finding")])


class ListAndIds(Base):
    def test_list_and_no_ids(self):
        self.assertEqual(self.kb("list"), ["推算清單是空的"])
        out = self.kb("add", "--entity", "信義王", "--text", "合計 182 萬", "--how", "相加", "--basis", self.quote)
        cid = next(iter(self.items()))
        out += self.kb("add", "--entity", "信義王", "--text", "合計 180 萬", "--how", "相加", "--basis", self.quote,
                       "--replaces", cid)
        lines = self.kb("list", "--all")
        self.assertTrue(any(ln.endswith("（已被取代）") for ln in lines), lines)
        self.assertEqual(len(self.kb("list", "--entity", "信義王宅")), 1)
        write_card(self.V, E.DRAFT, "信義王會議", ["信義王"], kind="會議紀錄")
        (line,) = self.kb("list")
        self.assertTrue(line.endswith("〔依據有變，待重算〕"), line)
        for ln in out + lines + [line]:
            self.assertIsNone(re.search(ID, ln), ln)
        got = json.loads(run_kb("finding", "list", "--json", cwd=self.V).stdout)["findings"]
        self.assertRegex(got[0]["id"], ID)
        self.assertTrue(got[0]["stale"])


class Revert(Base):
    def test_revert_add_and_synthesis(self):
        turn.start(self.V, None, "")
        self.add()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(findings.load(self.V)["findings"], [])

    def test_revert_replace(self):
        old = self.add()
        before = (self.V / findings.FINDINGS_POSIX).read_bytes()
        turn.start(self.V, None, "")
        self.add("--replaces", old, text="信義王報價合計 180 萬")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / findings.FINDINGS_POSIX).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
