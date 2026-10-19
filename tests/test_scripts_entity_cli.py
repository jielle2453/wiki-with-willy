"""kb entity 黑箱（實體與綜合卡第 3 點，驗收 1、8～11、16）：每個子子命令成功與拒絕各一；rename 搬夾與只搬卡兩種、
搬到原檔時清冊跟著入冊；merge 改檔案卡實體欄；終端機程式段、對話裡不記；revert 退回 rename 後實體清單與檔案卡回去。"""
import json
import shutil
import unittest
from pathlib import Path

from tests import _entities as E
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, entities, inventory, journal, turn  # noqa: E402

XINYI_DIR = Path("案子") / "信義王"


class Base(unittest.TestCase):
    with_entities = True

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(self.with_entities)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, code=0):
        r = run_kb("entity", *args, cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def data(self):
        return entities.load(self.V)

    def get(self, eid):
        return next(e for e in self.data()["entities"] if e["id"] == eid)

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def front(self, rel):
        return cards.parse_front((self.V / rel).read_text(encoding="utf-8"))

    def new_turn(self):
        return turn.start(self.V, None, "")["turn"]

    def todo(self):
        r = run_kb("cards", "todo", "--json", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)


class Add(Base):
    with_entities = False

    def test_add_idempotent_and_refusals(self):
        md = self.V / "系統" / "客戶設定檔.md"
        text = md.read_text(encoding="utf-8")
        md.write_text(text.rstrip("\n") + "\n| 林小姐室內設計 | 公司 | 林小姐 |\n", encoding="utf-8", newline="\n")
        self.assertEqual(run_kb("profile", "apply", cwd=self.V).returncode, 0)
        t = self.new_turn()
        self.assertEqual(self.kb("add", "--type", "案子", "--name", "信義王"), ["已登記：信義王（案子，待確認）"])
        (e,) = [x for x in self.data()["entities"] if x["name"] == "信義王"]
        self.assertEqual((e["source"], e["card"]), ("inferred", False))
        self.assertIn(entities.ENTITIES_POSIX, {r["path"] for r in turn.entries(self.V, t)})
        p = self.V / ".system" / "entities.json"
        before = p.read_bytes()
        self.assertEqual(self.kb("add", "--type", "案子", "--name", "信義王"), ["已登記過：信義王（案子）"])
        self.assertEqual(p.read_bytes(), before)
        cases = [
            (("--type", "廠商", "--name", "順發"), "型別「廠商」不在型別表；在設定檔第一節加一列再套用"),
            (("--type", "案子", "--name", "a/b"), "名稱「a/b」不能用（含不能用的字元）"),
            (("--type", "業主", "--name", "信義王"), "已有「信義王」（案子）；是同一件就掛它，不是就加區分詞（例：信義王-大安）"),
            (("--type", "業主", "--name", "林小姐"), "「林小姐」是你自己（關於你），掛「林小姐室內設計」"),
        ]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.kb("add", *args, code=3), [want])
        self.assertEqual(p.read_bytes(), before)

    def test_program_line_in_terminal_not_in_conversation(self):
        self.kb("add", "--type", "案子", "--name", "信義王")
        self.assertEqual(self.programs()[-1]["note"], "登記實體：信義王（案子）")
        self.assertEqual(self.programs()[-1]["command"], "entity add")
        n = len(self.programs())
        (self.V / ".system" / "turns" / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid", "started": "2026-10-19T10:00:00",
            "prompt_head": "", "approved": False, "note": "", "done": False}), encoding="utf-8")
        self.kb("add", "--type", "案子", "--name", "王宅")
        self.assertEqual(len(self.programs()), n)

    def test_not_found(self):
        self.assertEqual(self.kb("confirm", "沒有", code=3), ["實體清單沒有「沒有」"])


class ConfirmListRender(Base):
    def test_confirm(self):
        self.assertEqual(self.kb("confirm", "王宅"), ["已確認：王宅（案子）"])
        self.assertEqual(self.get(E.WANG)["source"], "confirmed")

    def test_list_and_json(self):
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "林小姐室內設計"])
        lines = self.kb("list")
        self.assertIn("- 信義王（案子，確認）掛 1 張，有綜合卡", lines)
        self.assertIn("- 王宅（案子，待確認）掛 0 張", lines)
        self.assertIn("- 林小姐室內設計（你自己）掛 1 張", lines)
        rows = json.loads(run_kb("entity", "list", "--json", cwd=self.V).stdout)["entities"]
        xinyi = next(r for r in rows if r["id"] == E.XINYI)
        self.assertEqual((xinyi["mounted"], xinyi["has_card"]), (1, True))

    def test_render(self):
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        self.assertEqual(self.kb("render"), ["重寫了 1 張綜合卡的檔頭"])
        self.assertEqual(self.kb("render"), ["重寫了 0 張綜合卡的檔頭"])


class Rename(Base):
    def test_moves_folder_card_and_client_file(self):
        rel = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "王宅"])
        t = self.new_turn()
        lines = self.kb("rename", "信義王", "--to", "信義王宅邸")
        self.assertEqual(lines, ["已改名：信義王 → 信義王宅邸（舊名記成別名）",
                                 "資料夾裡的 1 個檔跟著搬了，檔案卡下次整理會跟著改位置"])
        new_dir = self.V / "案子" / "信義王宅邸"
        self.assertFalse((self.V / XINYI_DIR).exists())
        self.assertTrue((new_dir / "合約.docx").is_file())
        card = "案子/信義王宅邸/信義王宅邸.md"
        front = self.front(card)
        self.assertEqual((front["名稱"], front["別名"]), ("信義王宅邸", ["信義王宅", "信義王"]))
        self.assertIn("\n# 信義王宅邸\n", (self.V / card).read_text(encoding="utf-8"))
        self.assertEqual(self.front(rel)["實體"], ["信義王宅邸", "王宅"])
        self.assertIn("信義王報價", (self.V / "總目錄.md").read_text(encoding="utf-8"))
        uris = {loc["uri"] for e in inventory.load(self.V)["entries"] if e["status"] == "active" for loc in e["locations"]}
        self.assertIn("file://知識庫/案子/信義王宅邸/合約.docx", uris)
        self.assertNotIn("file://知識庫/案子/信義王/合約.docx", uris)
        e = self.get(E.XINYI)
        self.assertEqual((e["name"], e["aliases"]), ("信義王宅邸", ["信義王宅", "信義王"]))
        actions = {r["path"]: r["action"] for r in turn.entries(self.V, t)}
        self.assertEqual((actions["案子/信義王/信義王.md"], actions[card]), ("delete", "create"))
        self.assertEqual(self.programs()[-1]["note"], "實體改名：信義王 → 信義王宅邸")
        self.assertEqual(synthesis_ok(self.V, card), (True, ""))

    def test_folder_renamed_by_client_then_follow(self):
        (self.V / XINYI_DIR).rename(self.V / "案子" / "王宅邸")
        self.assertEqual([(d["kind"], d["found"]) for d in entities.drift(self.V)],
                         [("folder_renamed", "案子/王宅邸/信義王.md")])
        self.assertEqual(self.kb("rename", "信義王", "--to", "王宅邸"), ["已改名：信義王 → 王宅邸（舊名記成別名）"])
        self.assertTrue((self.V / "案子" / "王宅邸" / "王宅邸.md").is_file())
        self.assertFalse((self.V / "案子" / "王宅邸" / "信義王.md").exists())
        self.assertEqual(entities.drift(self.V), [])

    def test_rename_back_restores_folder(self):
        (self.V / XINYI_DIR).rename(self.V / "案子" / "王宅邸")
        self.assertEqual(self.kb("rename", "信義王", "--to", "信義王"), ["已照「信義王」把資料夾與綜合卡改回來",
                                                                       "資料夾裡的 1 個檔跟著搬了，檔案卡下次整理會跟著改位置"])
        self.assertTrue((self.V / XINYI_DIR / "信義王.md").is_file())
        self.assertTrue((self.V / XINYI_DIR / "合約.docx").is_file())
        self.assertFalse((self.V / "案子" / "王宅邸").exists())
        self.assertEqual(self.get(E.XINYI)["aliases"], ["信義王宅"])

    def test_change_type_moves_to_other_type_folder(self):
        self.kb("rename", "大安陳", "--to", "陳先生", "--type", "業主")
        self.assertTrue((self.V / "業主" / "陳先生" / "陳先生.md").is_file())
        front = self.front("業主/陳先生/陳先生.md")
        self.assertEqual((front["型別"], front["大類"]), ("業主", "對象"))

    def test_via_alias_says_which(self):
        self.assertEqual(self.kb("rename", "信義王宅", "--to", "信義王邸")[0], "「信義王宅」是「信義王」的別名，改的是「信義王」")

    def test_refusals(self):
        self.assertEqual(self.kb("rename", "信義王", "--to", "大安陳", code=3),
                         ["已有「大安陳」（案子）；是同一件就掛它，不是就加區分詞（例：大安陳-大安）"])
        self.assertEqual(self.kb("rename", "信義王", "--to", "王", "--type", "廠商", code=3),
                         ["型別「廠商」不在型別表；在設定檔第一節加一列再套用"])

    def test_revert_rename(self):
        rel = E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        before_entities = (self.V / ".system" / "entities.json").read_bytes()
        before_card = (self.V / XINYI_DIR / "信義王.md").read_bytes()
        self.new_turn()
        self.kb("rename", "信義王", "--to", "信義王宅邸")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.V / ".system" / "entities.json").read_bytes(), before_entities)
        self.assertEqual(self.front(rel)["實體"], ["信義王"])
        self.assertEqual((self.V / XINYI_DIR / "信義王.md").read_bytes(), before_card)
        self.assertFalse((self.V / "案子" / "信義王宅邸" / "信義王宅邸.md").exists())
        # 夾名由程式改了會留著（versions.py 不改）：客戶原檔還在新夾
        self.assertTrue((self.V / "案子" / "信義王宅邸" / "合約.docx").is_file())


def synthesis_ok(V, rel):
    from kb._lib import synthesis
    p = Path(V) / rel
    text = p.read_text(encoding="utf-8")
    data = entities.load(V)
    e = next(x for x in data["entities"] if x["id"] == cards.parse_front(text)["編號"])
    header = synthesis.header_for(V, e, entities.mounts(V, data).get(e["name"], []), "2026-10-20")
    body = text[text.index("\n---\n", 4) + 5:]
    p.write_text(header + body, encoding="utf-8")
    return synthesis.validate(V, rel)


class Merge(Base):
    def test_merge_moves_mounts_and_aliases(self):
        rel = E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        both = E.write_card(self.V, E.ESTIMATE, "兩邊都有", ["信義王", "王宅"])
        lines = self.kb("merge", "王宅", "--into", "信義王")
        self.assertEqual(lines, ["已合併：王宅 併進 信義王（王宅記成別名）", "信義王的綜合卡下次整理會補上這 1 張"])
        wang = self.get(E.WANG)
        self.assertEqual((wang["status"], wang["merged_into"]), ("merged", E.XINYI))
        self.assertIn("王宅", self.get(E.XINYI)["aliases"])
        self.assertEqual(self.front(rel)["實體"], ["信義王"])
        self.assertEqual(self.front(both)["實體"], ["信義王"])
        self.assertEqual(entities.find(self.data(), "王宅")[0]["id"], E.XINYI)
        self.assertEqual(self.programs()[-1]["note"], "合併實體：王宅 併進 信義王")

    def test_refusals(self):
        data = self.data()["entities"]
        data.append(E.entity("e-33333333", "王先生", type_="業主"))
        E.put_entities(self.V, data)
        self.assertEqual(self.kb("merge", "王先生", "--into", "信義王", code=3),
                         ["型別不同：先 kb entity rename 王先生 --to 王先生 --type 案子"])
        (self.V / "案子" / "大安陳" / "附件.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.kb("merge", "大安陳", "--into", "信義王", code=3),
                         ["「大安陳」的夾裡還有 1 個檔，先把檔搬到「信義王」再合併"])


class RemoveRebuild(Base):
    def test_remove(self):
        rel = E.write_card(self.V, E.QUOTE, "大安陳報價", ["大安陳"])
        t = self.new_turn()
        self.assertEqual(self.kb("remove", "大安陳"), ["已移除：大安陳（知識不刪，檔案卡上的掛載留著）"])
        self.assertEqual(self.get(E.DAAN)["status"], "removed")
        self.assertFalse((self.V / "案子" / "大安陳").exists())
        self.assertEqual({r["path"]: r["action"] for r in turn.entries(self.V, t)}["案子/大安陳/大安陳.md"], "delete")
        self.assertEqual(self.front(rel)["實體"], ["大安陳"])
        data = self.todo()
        for key in ("synthesis_new", "synthesis_stale", "synthesis_broken", "entity_drift"):
            self.assertEqual(data[key], [], key)
        self.assertEqual(self.programs()[-1]["note"], "移除實體：大安陳")

    def test_rebuild_only_when_missing(self):
        self.assertEqual(self.kb("rebuild", "信義王", code=3), ["「信義王」的綜合卡還在，不用重寫"])
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        E.write_card(self.V, E.ESTIMATE, "信義王估價", ["信義王"])
        shutil.rmtree(self.V / XINYI_DIR)
        self.assertEqual([d["kind"] for d in entities.drift(self.V)], ["missing"])
        self.assertEqual(self.kb("rebuild", "信義王"), ["好，「信義王」會照檔案卡重寫一張"])
        self.assertFalse(self.get(E.XINYI)["card"])
        (item,) = [i for i in self.todo()["synthesis_new"] if i["entity_id"] == E.XINYI]
        self.assertTrue(item["confirmed"])


class Clean(Base):
    def test_clean_misplaced_and_refusals(self):
        stray = self.V / "案子" / "王宅" / "王宅.md"
        stray.parent.mkdir()
        shutil.copy2(E.FIX / "信義王.md", stray)
        self.assertIn({"path": "案子/王宅/王宅.md", "reason": "綜合卡該放在 案子/信義王/信義王.md"},
                      self.todo()["synthesis_broken"])
        t = self.new_turn()
        self.assertEqual(self.kb("clean", "案子/王宅/王宅.md"), ["已清掉放錯的綜合卡：案子/王宅/王宅.md"])
        self.assertFalse(stray.parent.exists())
        self.assertEqual({r["path"]: r["action"] for r in turn.entries(self.V, t)}["案子/王宅/王宅.md"], "delete")
        self.assertEqual(self.programs()[-1]["note"], "清掉放錯的綜合卡：案子/王宅/王宅.md")
        self.assertEqual(self.kb("clean", "案子/信義王/信義王.md", code=3), ["這張綜合卡位置是對的，不用清"])
        self.assertEqual(self.kb("clean", "案子/信義王/合約.docx", code=3), ["這不是綜合卡，不能用 clean"])

    def test_folder_with_other_files_kept(self):
        stray = self.V / "案子" / "王宅" / "王宅.md"
        stray.parent.mkdir()
        shutil.copy2(E.FIX / "信義王.md", stray)
        (stray.parent / "照片.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.kb("clean", "案子/王宅/王宅.md"),
                         ["已清掉放錯的綜合卡：案子/王宅/王宅.md", "夾裡還有 1 個檔，夾留著"])
        self.assertTrue(stray.parent.is_dir())


class AfterTest(Base):
    """測試後修正裁定 8～14：先驗後做與中途還原、merge 後目標卡是 stale、名稱擋 , [ ]、實體清單壞了講檔名、
    clean 正規化 ..。"""

    def snapshot(self):
        out = {}
        for p in sorted(self.V.rglob("*")):
            rel = p.relative_to(self.V).as_posix()
            if p.is_file() and not rel.startswith((".system/versions/", ".system/turns/", ".system/journal/")) \
                    and not rel.startswith("系統/工作日誌/") and not rel.endswith(".lock"):
                out[rel] = p.read_bytes()
        return out

    def test_occupied_target_refused_before_touching_cards(self):
        """F2 確定性版本：目標路徑已有檔 → 擋下，檔案卡、實體清單、夾都沒動。"""
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王"])
        d = self.V / "案子" / "王宅邸"
        d.mkdir()
        (d / "王宅邸.md").write_text("客戶自己的檔\n", encoding="utf-8")
        before = self.snapshot()
        self.assertEqual(self.kb("rename", "信義王", "--to", "王宅邸", code=3),
                         ["「案子/王宅邸/王宅邸.md」已經有檔，沒有改名；先把它搬走"])
        self.assertEqual(self.snapshot(), before)

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

    def fail_on_entities_write(self):
        from unittest import mock
        real = entities.write_json_atomic

        def boom(path, data):
            if Path(path).name == "entities.json":
                raise PermissionError(13, "被別的程式開著")
            return real(path, data)
        return mock.patch.object(entities, "write_json_atomic", side_effect=boom)

    def test_rename_failure_rolls_back_everything(self):
        """F2：搬夾、改檔案卡之後才失敗 → 全部還原，印「沒有寫入：…」exit 3。"""
        E.write_card(self.V, E.QUOTE, "信義王報價", ["信義王", "王宅"])
        before = self.snapshot()
        with self.fail_on_entities_write():
            code, lines = self.run_in_process("entity", "rename", "信義王", "--to", "信義王宅邸")
        self.assertEqual(code, 3)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("沒有寫入：PermissionError"), lines)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.V / "案子" / "信義王宅邸").exists())

    def test_merge_and_remove_failure_roll_back(self):
        E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        before = self.snapshot()
        with self.fail_on_entities_write():
            code, _lines = self.run_in_process("entity", "merge", "大安陳", "--into", "信義王")
            self.assertEqual(code, 3)
            code, _lines = self.run_in_process("entity", "remove", "大安陳")
            self.assertEqual(code, 3)
        self.assertEqual(self.snapshot(), before)

    def test_partial_write_when_rollback_fails(self):
        from unittest import mock
        boom = mock.patch.object(entities._Undo, "rollback", side_effect=PermissionError(13, "還原不了"))
        with self.fail_on_entities_write(), boom:
            code, lines = self.run_in_process("entity", "remove", "大安陳")
        self.assertEqual(code, 3)
        self.assertTrue(lines[0].startswith("寫到一半失敗、沒能全部還原（"), lines)
        self.assertTrue(lines[0].endswith("說「退回上一版」救得回來"), lines)

    def test_merge_into_carded_target_is_stale_not_broken(self):
        """F3：merge 後目標綜合卡的 別名 跟著改，todo 把它列進 synthesis_stale（added 是來源的卡）。"""
        rel = E.write_card(self.V, E.QUOTE, "王宅報價", ["王宅"])
        fid = self.front(rel)["編號"]
        t = self.new_turn()
        self.kb("merge", "王宅", "--into", "信義王")
        self.assertEqual(self.front("案子/信義王/信義王.md")["別名"], ["信義王宅", "王宅"])
        self.assertEqual({r["path"]: r.get("by") for r in turn.entries(self.V, t)}["案子/信義王/信義王.md"], "program")
        data = self.todo()
        self.assertEqual(data["synthesis_broken"], [])
        (item,) = [i for i in data["synthesis_stale"] if i["entity_id"] == E.XINYI]
        self.assertEqual([x["id"] for x in item["added"]], [fid])

    def test_list_chars_refused(self):
        """F4：名稱擋 , [ ]，句子併進「含不能用的字元」。"""
        for name in ("Smith, John", "x]", "[甲"):
            with self.subTest(name=name):
                self.assertEqual(self.kb("add", "--type", "業主", "--name", name, code=3),
                                 [f"名稱「{name}」不能用（含不能用的字元）"])
        self.assertEqual(self.kb("rename", "信義王", "--to", "王,宅", code=3), ["名稱「王,宅」不能用（含不能用的字元）"])

    def test_broken_entities_says_file(self):
        """F5：entities.json 壞了，cards todo 與 kb entity 都講檔名。"""
        (self.V / ".system" / "entities.json").write_text("{bad", encoding="utf-8")
        for args in (("cards", "todo"), ("entity", "list"), ("entity", "add", "--type", "案子", "--name", "王")):
            with self.subTest(args=args):
                r = run_kb(*args, cwd=self.V)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertTrue(r.stdout.startswith("狀態檔讀不到：.system/entities.json（"), r.stdout)

    def test_clean_normalizes_dotdot(self):
        """F8：案子/../z.md 會碰到知識庫根 → 擋下，檔還在。"""
        z = self.V / "z.md"
        shutil.copy2(E.FIX / "大安陳.md", z)
        for path in ("案子/../z.md", "../z.md", "案子/./信義王/../王宅.md"):
            with self.subTest(path=path):
                self.assertEqual(self.kb("clean", path, code=3), ["這不是綜合卡，不能用 clean"])
        self.assertTrue(z.is_file())
        stray = self.V / "案子" / "王宅" / "王宅.md"
        stray.parent.mkdir()
        shutil.copy2(E.FIX / "信義王.md", stray)
        self.assertEqual(self.kb("clean", "案子/王宅/./王宅.md"), ["已清掉放錯的綜合卡：案子/王宅/王宅.md"])


if __name__ == "__main__":
    unittest.main()
