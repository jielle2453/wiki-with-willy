"""kb statement 黑箱（note 改知識第 3 點，驗收 1、2、12、13）：成功與拒絕句一字不差、人讀不印編號、--json 有、
終端機記程式段、對話裡不記、revert 回去、cards todo 把掛的實體列進待更新綜合卡。"""
import json
import re
import shutil
import unittest

from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import journal, statements, turn  # noqa: E402

ID = r"[afsr]-[0-9a-f]{8}"


def leaks(V, secret):
    """總資料夾裡（含 .system/、系統/）內容出現 secret 的檔，相對路徑清單。"""
    out = []
    for p in sorted(V.rglob("*")):
        if p.is_file():
            try:
                if secret in p.read_text(encoding="utf-8", errors="ignore"):
                    out.append(p.relative_to(V).as_posix())
            except OSError:
                pass
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("statement", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def add(self, text="信義王改用系統櫃，不做木工", entity="信義王", *extra):
        r = run_kb("statement", "add", "--entity", entity, "--text", text, *extra, "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["statement"]["id"]

    def items(self):
        return {s["id"]: s for s in statements.load(self.V)["statements"]}

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def todo(self):
        return json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)


class Add(Base):
    def test_success_state_backup_program_line_and_todo(self):
        t = turn.start(self.V, None, "")["turn"]
        self.assertEqual(self.kb("add", "--entity", "信義王", "--text", "信義王改用系統櫃，不做木工"),
                         ["已記下：信義王改用系統櫃，不做木工（信義王）"])
        (item,) = self.items().values()
        self.assertEqual((item["status"], item["entities"]), ("active", ["信義王"]))
        self.assertIn(statements.STATEMENTS_POSIX, {r["path"] for r in turn.entries(self.V, t)})
        self.assertEqual([(p["note"], p["command"]) for p in self.programs()][-1],
                         ("記下口述：信義王改用系統櫃，不做木工", "statement add"))
        (stale,) = [i for i in self.todo()["synthesis_stale"] if i["name"] == "信義王"]
        self.assertEqual([(x["id"], x["source"]) for x in stale["added"]], [(item["id"], "statement")])
        self.assertEqual([x["id"] for x in stale["statements"]], [item["id"]])

    def test_refusals_exact(self):
        cases = [
            (["--entity", "內湖李", "--text", "內湖李改用系統櫃"], "實體清單沒有「內湖李」，先 kb entity add"),
            (["--entity", "信義王", "--text", "字" * 201], "口述要一句話，最多 200 字"),
            (["--entity", "信義王", "--text", "單價 $8500"], "口述裡不能有 \" $ ` \\ 這四種字元"),
            (["--entity", "信義王", "--text", "x", "--replaces", "s-00000000"], "沒有這條口述：s-00000000"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("add", *args, code=3), [want])
        self.assertFalse((self.V / ".system" / "statements.json").exists())

    def test_password_refused_and_left_nowhere(self):
        """測試第一輪後裁定 3（F-1）：被拒的那次，狀態檔、工作日誌 jsonl 與月檔、ledger.tsv 都沒有那串密碼。"""
        self.assertEqual(self.kb("add", "--entity", "信義王", "--text", "門禁密碼 password: Abc12345678", code=3),
                         ["口述裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員"])
        self.assertEqual(leaks(self.V, "Abc12345678"), [])

    def test_two_entities(self):
        self.assertEqual(self.kb("add", "--entity", "信義王", "--entity", "大安陳", "--text", "兩案共用同一個木工"),
                         ["已記下：兩案共用同一個木工（信義王、大安陳）"])


class ReplaceWithdraw(Base):
    def test_replace_withdraw_and_superseded_refusal(self):
        old = self.add("王宅的聯絡人是王先生", "王宅")
        self.assertEqual(self.kb("add", "--entity", "王宅", "--text", "王宅的聯絡人改成王太太", "--replaces", old),
                         ["已改：王宅的聯絡人是王先生 → 王宅的聯絡人改成王太太"])
        got = self.items()
        new = got[old]["replaced_by"]
        self.assertEqual((got[old]["status"], got[new]["status"]), ("superseded", "active"))
        self.assertEqual(self.kb("add", "--entity", "王宅", "--text", "王小姐", "--replaces", old, code=3),
                         ["這條已經被取代了，改最新的那條"])
        self.assertEqual(self.kb("withdraw", new), ["已收回：王宅的聯絡人改成王太太"])
        self.assertEqual(self.items()[new]["status"], "withdrawn")
        self.assertEqual([p["command"] for p in self.programs()][-2:], ["statement add", "statement withdraw"])
        self.assertEqual(self.kb("withdraw", "s-00000000", code=3), ["沒有這條口述：s-00000000"])


class List(Base):
    def test_list_default_all_entity_and_json(self):
        self.assertEqual(self.kb("list"), ["口述清單是空的"])
        old = self.add("王宅的聯絡人是王先生", "王宅")
        self.add("王宅的聯絡人改成王太太", "王宅", "--replaces", old)
        self.add()
        lines = self.kb("list")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(re.match(r"^- \d{4}-\d{2}-\d{2} ", ln) for ln in lines), lines)
        self.assertIn("信義王改用系統櫃，不做木工（信義王）", lines[0], "同一天照記的先後，新的在上")
        self.assertIn("王宅的聯絡人改成王太太（王宅）", lines[1])
        self.assertEqual(len(self.kb("list", "--entity", "王宅")), 1)
        everything = self.kb("list", "--all")
        self.assertTrue(any(ln.endswith("（王宅）（已被取代）") for ln in everything), everything)
        out = json.loads(run_kb("statement", "list", "--all", "--json", cwd=self.V).stdout)
        self.assertEqual(len(out["statements"]), 3)


class NoIdsAndConversation(Base):
    def test_human_output_has_no_ids(self):
        out = self.kb("add", "--entity", "信義王", "--text", "信義王改用系統櫃")
        sid = next(iter(self.items()))
        out += self.kb("add", "--entity", "信義王", "--text", "信義王改用木工", "--replaces", sid)
        new = self.items()[sid]["replaced_by"]
        out += self.kb("list", "--all") + self.kb("withdraw", new)
        for line in out:
            self.assertIsNone(re.search(ID, line), line)
        self.assertRegex(json.loads(run_kb("statement", "list", "--json", "--all", cwd=self.V).stdout)["statements"][0]["id"], ID)

    def test_nothing_recorded_in_a_conversation(self):
        (self.V / ".system" / "turns" / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid", "started": "2026-10-19T10:00:00",
            "prompt_head": "", "approved": False, "note": "", "done": False}), encoding="utf-8")
        self.add()
        self.assertEqual(self.programs(), [])


class Revert(Base):
    def test_revert_of_the_first_add(self):
        turn.start(self.V, None, "")
        self.add()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(statements.load(self.V)["statements"], [])

    def test_revert_of_a_replace(self):
        old = self.add("王宅的聯絡人是王先生", "王宅")
        before = (self.V / ".system" / "statements.json").read_bytes()
        turn.start(self.V, None, "")
        self.add("王宅的聯絡人改成王太太", "王宅", "--replaces", old)
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / ".system" / "statements.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
