"""kb profile check／apply 黑箱（客戶設定檔與型別第 2 點；驗收 3～7，實作疑義裁定 1、2：用 fixture 沒有的「工地」）。
<V> 是 tests/fixtures/inventory 的複本、全掃過一次；fixture 的設定檔型別表只有「案子」。"""
import json
import shutil
import unittest
from datetime import date

from tests import _cards
from tests.test_lib_inventory_scan import make_inv_vault
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import inventory, journal, profile, turn  # noqa: E402

ROW = "| 工地 | 事 | 一個客戶一個裝修案 |"
ANCHOR = "| 案子 | 事 |  | 推測 | 2026-10-19 |\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()
        _cards.scan_apply(self.V)
        self.today = date.today().isoformat()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb("profile", *args, cwd=self.V)

    @property
    def md(self):
        return self.V / "系統" / "客戶設定檔.md"

    def md_text(self):
        return self.md.read_text(encoding="utf-8")

    def add_types(self, *rows):
        """md 第一節表格最後加幾列（接在目前第一節最後一列之後）。"""
        text = self.md_text()
        head = text.index("## 二、")
        at = text.rindex("|\n", 0, head) + 2
        self.md.write_text(text[:at] + "".join(r + "\n" for r in rows) + text[at:], encoding="utf-8", newline="\n")

    def drop_line(self, startswith):
        lines = self.md_text().split("\n")
        self.md.write_text("\n".join(ln for ln in lines if not ln.startswith(startswith)), encoding="utf-8",
                           newline="\n")

    def data(self):
        return json.loads((self.V / ".system" / "profile.json").read_text(encoding="utf-8"))

    def programs(self):
        return [r for m in journal.months(self.V) for r in journal.read_lines(self.V, m) if r.get("kind") == "program"]

    def stamps(self):
        return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns for p in (self.V / ".system").rglob("*")
                if p.is_file()}

    def apply(self, code=0):
        r = self.kb("apply")
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout.splitlines()


class Check(Base):
    def test_unchanged_writes_nothing(self):
        before = self.stamps()
        r = self.kb("check")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, ["客戶設定檔沒有改"]))
        self.assertEqual(self.stamps(), before)

    def test_added_row_and_unlisted_folder(self):
        self.add_types(ROW)
        r = self.kb("check")
        self.assertEqual(r.stdout.splitlines(), ["客戶設定檔有改：型別 +1 -0 ~0", "- 型別 多了 工地（事）"])
        (self.V / "廠商").mkdir()
        before = self.stamps()
        lines = self.kb("check").stdout.splitlines()
        self.assertEqual(lines[-1], "第一層夾不在型別表：廠商（請在設定檔第一節補一列）")
        self.assertEqual(self.stamps(), before)

    def test_broken_md_exit_3(self):
        self.add_types("| 工地 | 事 |")
        r = self.kb("check")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.splitlines()[0], "客戶設定檔讀不懂：第 1 節第 2 列欄數不對（要 3 欄或 5 欄）")

    def test_whitespace_only_change(self):
        self.md.write_text(self.md_text() + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(self.kb("check").stdout.splitlines(), ["客戶設定檔有改，但沒有內容上的差異"])

    def test_detail_lines_for_each_section(self):
        self.drop_line("| 案子 | 事 |")  # 只刪第一節那列：第四節案子的列跟著丟掉（實作回報裁定 1）
        self.add_types(ROW)
        text = self.md_text().replace("| 報價 | 日期與數字 |  |", "| 報價 | 日期與數字 | ✓ |")
        text = text.replace("| 地址 | 只記種類 | 推測 | 2026-10-19 |\n", "| 地址 | 只記種類 | 推測 | 2026-10-19 |\n| 請款 | 不整理 |\n")
        self.md.write_text(text, encoding="utf-8", newline="\n")
        lines = self.kb("check").stdout.splitlines()
        self.assertEqual(lines, ["客戶設定檔有改：型別 +1 -1 ~0、卡型 +0 -0 ~1、不整理與敏感 +1 -0 ~0、每類段落 +0 -2 ~0",
                                 "- 型別 多了 工地（事）", "- 型別 少了 案子（夾裡還有 2 項，套用會被擋）",
                                 "- 卡型 報價 的段落 日期與數字 改了", "- 不整理與敏感 多了 請款（不整理）",
                                 "- 每類段落 案子 的 時程 少了", "- 每類段落 案子 的 金額 少了",
                                 "第一層夾不在型別表：案子（請在設定檔第一節補一列）"])

    def test_json(self):
        self.add_types(ROW)
        (self.V / "廠商").mkdir()
        r = self.kb("check", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["state"], "changed")
        self.assertEqual(data["diff"]["types"], {"added": ["工地"], "removed": [], "changed": []})
        self.assertEqual((data["unlisted"], data["to_create"], data["removed_empty"], data["removed_nonempty"]),
                         (["廠商"], ["工地"], [], []))
        self.assertIsNone(data["error"])

    def test_missing_profile(self):
        (self.V / ".system" / "profile.json").unlink()
        r = self.kb("check")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "還沒有客戶設定檔，跑 kb init 補"))
        self.assertEqual(self.kb("apply").returncode, 3)

    def test_not_in_vault(self):
        r = run_kb("profile", "check", cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "這裡不是知識庫；到知識庫裡執行，或加 --vault"))
        r = run_kb("profile", "check", "--vault", str(self.V), cwd=self.tmp)
        self.assertEqual(r.stdout.strip(), "客戶設定檔沒有改")


class Apply(Base):
    def test_first_apply_creates_folder_and_marks_interview(self):
        self.add_types(ROW)
        t = turn.current(self.V)["turn"]
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 +1；建了資料夾 工地"])
        self.assertTrue((self.V / "工地").is_dir())
        data = self.data()
        row = data["types"][-1]
        self.assertEqual((row["name"], row["source"], row["date"]), ("工地", "interview", self.today))
        self.assertEqual(data["types"][0]["source"], "inferred", "沒變的列保留舊來源")
        self.assertIsNotNone(data["applied"])
        self.assertIn(f"| 工地 | 事 | 一個客戶一個裝修案 | 訪談 | {self.today} |", self.md_text())
        self.assertEqual(data["md_sha256"], profile.md_sha256(self.V))
        manifest = {e["path"] for e in turn.entries(self.V, t)}
        self.assertTrue({profile.PROFILE_POSIX, profile.MD_POSIX} <= manifest)
        prog = self.programs()[-1]
        self.assertEqual((prog["command"], prog["note"]), ("profile apply", "套用客戶設定檔：型別 +1；建了資料夾 工地"))
        self.assertEqual(prog["changed"], [{"path": profile.PROFILE_POSIX, "action": "modify"},
                                           {"path": profile.MD_POSIX, "action": "modify"}])
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用"])

    def test_later_rows_confirmed_corrected_and_bad_source(self):
        self.add_types(ROW)
        self.apply()
        self.add_types("| 工班 | 對象 | 施工班底 |", "| 業主 | 對象 | 屋主 | 糾正 |  |")
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 +2；建了資料夾 工班、業主"])
        got = {t["name"]: t["source"] for t in self.data()["types"]}
        self.assertEqual((got["工地"], got["工班"], got["業主"]), ("interview", "confirmed", "corrected"))
        self.add_types("| 水電 | 對象 | x | 亂填 |  |")
        lines = self.apply(3)
        self.assertEqual(lines[0], "客戶設定檔讀不懂：第 1 節第 5 列的來源要是 訪談、推測、確認、糾正 之一，或留空")

    def test_remove_empty_row_and_refuse_nonempty(self):
        self.add_types(ROW, "| 工班 | 對象 | 施工班底 |")
        self.apply()
        self.drop_line("| 工班 | 對象 |")
        self.assertIn("- 型別 少了 工班（空夾會一起拿掉）", self.kb("check").stdout.splitlines())
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 -1（拿掉了空夾 工班）、每類段落 -2"])
        self.assertFalse((self.V / "工班").exists(), "空夾一起拿掉（確認後修正裁定 21）")
        self.assertEqual(self.programs()[-1]["note"], "套用客戶設定檔：型別 -1（拿掉了空夾 工班）、每類段落 -2")
        self.assertEqual([t["name"] for t in self.data()["types"]], ["案子", "工地"])
        (self.V / "工地" / "a.txt").write_text("x", encoding="utf-8")
        self.drop_line("| 工地 | 事 |")
        before_json, before_md = self.data(), self.md_text()
        self.assertEqual(self.apply(3), ["型別「工地」底下還有 1 項，先搬走再拿掉這列"])
        self.assertEqual((self.data(), self.md_text()), (before_json, before_md))

    def test_rename_is_one_less_one_more(self):
        self.add_types("| 客戶 | 對象 | 業主 |")
        self.apply()
        text = self.md_text().replace("| 客戶 | 對象 |", "| 業主 | 對象 |")  # 只改第一節：舊名的第四節列丟掉、新名補預設
        self.md.write_text(text, encoding="utf-8", newline="\n")
        lines = self.kb("check").stdout.splitlines()
        self.assertIn("- 型別 多了 業主（對象）", lines)
        self.assertIn("- 型別 少了 客戶（空夾會一起拿掉）", lines)
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 +1 -1（拿掉了空夾 客戶）、每類段落 -2；建了資料夾 業主"])
        self.assertTrue(not (self.V / "客戶").exists() and (self.V / "業主").is_dir())
        rows = [(x["type"], y["name"], x["source"]) for x in self.data()["synthesis_sections"] for y in x["sections"]]
        self.assertIn(("業主", "聯絡與往來", "inferred"), rows)
        self.assertNotIn("客戶", {r[0] for r in rows})

    def test_rmdir_failure_keeps_folder_and_says_so(self):
        import contextlib
        import io
        from unittest import mock
        from kb import profile_cli
        self.add_types("| 客戶 | 對象 | 業主 |")
        self.apply()
        self.drop_line("| 客戶 | 對象 |")
        out = io.StringIO()
        with mock.patch.object(profile_cli.os, "rmdir", side_effect=PermissionError(32, "被佔用")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(profile_cli._apply(self.V, False, True), 0)
        self.assertEqual(out.getvalue().splitlines()[0], "已套用客戶設定檔：型別 -1（客戶 夾是空的，會留著）、每類段落 -2")
        self.assertTrue((self.V / "客戶").is_dir())
        self.assertEqual([t["name"] for t in self.data()["types"]], ["案子"])

    def test_whitespace_only_change_rewrites(self):
        self.md.write_text(self.md_text() + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(self.apply(), ["已套用客戶設定檔：沒有內容上的差異，重寫了設定檔"])
        self.assertEqual(self.kb("check").stdout.strip(), "客戶設定檔沒有改")

    def test_json(self):
        self.add_types(ROW)
        data = json.loads(self.kb("apply", "--json").stdout)
        self.assertEqual((data["applied"], data["created_dirs"], data["unskipped"]), (True, ["工地"], 0))
        data = json.loads(self.kb("apply", "--json").stdout)
        self.assertEqual((data["applied"], data["state"]), (False, "ok"))

    def test_in_conversation_no_program_line(self):
        turns = self.V / ".system" / "turns"
        turns.mkdir(exist_ok=True)
        (turns / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261019-100000-test", "session_id": "test-sid",
            "started": "2026-10-19T10:00:00", "prompt_head": "套用", "approved": False, "note": "", "done": False}),
            encoding="utf-8")
        n = len(self.programs())
        self.add_types(ROW)
        self.apply()
        self.assertEqual(len(self.programs()), n)
        self.assertTrue({profile.PROFILE_POSIX, profile.MD_POSIX}
                        <= {e["path"] for e in turn.entries(self.V, "20261019-100000-test")})

    def test_revert_restores_json_and_md_and_removes_made_folder(self):
        """D-142：apply 建的夾記 make，退回時空的就拿掉（取代「建的資料夾還在」那一行）。"""
        t = turn.start(self.V, None, "")["turn"]
        before_json, before_md = self.data(), self.md_text()
        self.add_types(ROW, "| 工班 | 對象 | 施工班底 |")
        self.apply()
        self.assertEqual([(r["op"], r["from"]) for r in turn.folders(self.V, t)], [("make", "工地"), ("make", "工班")])
        (self.V / "工班" / "a.txt").write_text("x", encoding="utf-8")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.data(), before_json)
        # fixture 是 schema 2 的 md：手改過的 md 備進回合的是舊 JSON 的渲染（schema 3 多一個空的第六節）
        self.assertEqual(self.md_text(), profile.render_text(profile.load(self.V)))
        self.assertEqual(self.md_text()[:len(before_md)], before_md)
        self.assertFalse((self.V / "工地").exists())
        self.assertTrue((self.V / "工班" / "a.txt").is_file())
        self.assertIn("建的資料夾「工班」裡有東西，留著", r.stdout.splitlines())
        self.assertNotIn("套用設定檔時建的資料夾還在，要拿掉請自己刪空夾", r.stdout.splitlines())

    def test_apply_removing_empty_folder_records_remove(self):
        self.add_types(ROW)
        self.apply()
        t = turn.start(self.V, None, "")["turn"]
        self.drop_line("| 工地 | 事 |")
        self.apply()
        self.assertEqual([(r["op"], r["from"]) for r in turn.folders(self.V, t)], [("remove", "工地")])
        self.assertEqual(run_kb("versions", "revert", cwd=self.V).returncode, 0)
        self.assertTrue((self.V / "工地").is_dir())


class AfterTest(Base):
    """測試後修正裁定 11、14：先建夾再寫檔，建不了就整份不套用；check 讀不懂不印 unlisted。"""

    def test_mkdir_failure_writes_nothing_and_keeps_made_folders(self):
        import contextlib
        import io
        import pathlib
        from unittest import mock
        from kb import profile_cli
        self.add_types(ROW, "| 工班 | 對象 | 施工班底 |")
        before_json, before_md = self.data(), self.md_text()
        real = pathlib.Path.mkdir

        def fake(path, *a, **kw):
            if path.name == "工班":
                raise PermissionError(13, "存取被拒")
            return real(path, *a, **kw)
        out = io.StringIO()
        with mock.patch.object(pathlib.Path, "mkdir", fake), contextlib.redirect_stdout(out):
            code = profile_cli._apply(self.V, False, True)
        self.assertEqual(code, 3)
        self.assertEqual(out.getvalue().splitlines(),
                         ["建不了資料夾「工班」：存取被拒；設定檔沒有套用", "已建的資料夾留著：工地"])
        self.assertEqual((self.data(), self.md_text()), (before_json, before_md))
        self.assertTrue((self.V / "工地").is_dir())
        self.assertFalse((self.V / "工班").exists())
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 +2；建了資料夾 工班"])

    def test_too_long_and_windows_name_refused_before_anything(self):
        self.add_types("| " + "長" * 300 + " | 事 | x |")
        before = self.stamps()
        self.assertEqual(self.apply(3), ["客戶設定檔讀不懂：第 1 節第 2 列的型別名「長長長長長長長長長長…」太長（最多 40 字）"])
        self.drop_line("| 長")
        self.add_types("| NUL | 事 | x |")
        self.assertEqual(self.apply(3), ["客戶設定檔讀不懂：第 1 節第 2 列的型別名「NUL」是 Windows 保留名，不能當資料夾"])
        self.assertEqual(self.stamps(), before)

    def test_check_unreadable_has_no_unlisted_line(self):
        (self.V / "廠商").mkdir()
        self.add_types("| 工地 | 事 |")
        r = self.kb("check")
        self.assertEqual((r.returncode, r.stdout.splitlines()),
                         (3, ["客戶設定檔讀不懂：第 1 節第 2 列欄數不對（要 3 欄或 5 欄）"]))


class SchemaTwoApply(Base):
    """實體與綜合卡第 10 點、驗收 14、15：第五節登記客戶自己、第四節補大類預設、刪光不補。"""

    def add_about(self, row):
        text = self.md_text()
        at = text.index("\n## 六、") if "\n## 六、" in text else len(text.rstrip("\n"))  # 第五節的表最後
        self.md.write_text(text[:at].rstrip("\n") + "\n" + row + "\n" + text[at:], encoding="utf-8", newline="\n")

    def test_about_registers_self_entity(self):
        from kb._lib import entities
        self.add_about("| 林小姐室內設計 | 公司 | 林小姐 |")
        self.assertIn("- 關於你 多了 林小姐室內設計（公司）", self.kb("check").stdout.splitlines())
        lines = self.apply()
        self.assertEqual(lines, ["已套用客戶設定檔：關於你 +1", "關於你：登記了 林小姐室內設計"])
        (e,) = entities.load(self.V)["entities"]
        self.assertEqual((e["name"], e["self"], e["type"], e["aliases"]), ("林小姐室內設計", "company", None, ["林小姐"]))
        self.assertIn(entities.ENTITIES_POSIX, {c["path"] for c in self.programs()[-1]["changed"]})
        r = run_kb("entity", "add", "--type", "案子", "--name", "林小姐", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "「林小姐」是你自己（關於你），掛「林小姐室內設計」"))
        todo = json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)
        self.assertEqual(todo["profile"]["about"], [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"]}])

    def test_new_type_gets_category_defaults_and_deleted_rows_not_refilled(self):
        self.add_types("| 工地 | 事 | 裝修現場 |")
        self.apply()
        rows = [(s["type"], x["name"], s["source"]) for s in self.data()["synthesis_sections"] for x in s["sections"]]
        self.assertIn(("工地", "時程", "inferred"), rows)
        self.assertIn(("工地", "金額", "inferred"), rows)
        self.assertIn("| 工地 | 時程 |  | 開始、重要日期、預計結束 | 推測 |", self.md_text())
        self.drop_line("| 案子 | 時程 |")
        self.drop_line("| 案子 | 金額 |")
        self.apply()
        self.assertNotIn("案子", {s["type"] for s in self.data()["synthesis_sections"]})
        self.assertEqual(self.data()["schema_version"], 3, "寫的時候寫 3")

    def test_drop_type_row_only_applies_and_drops_its_sections(self):
        """實作回報裁定 1：只刪第一節的列，apply 成功，第四節那個型別的列一起拿掉。"""
        self.add_types("| 廠商 | 對象 | 供應商 |")
        self.apply()
        self.assertIn("| 廠商 | 聯絡與往來 |", self.md_text())
        self.drop_line("| 廠商 | 對象 |")
        self.assertEqual(self.kb("check").stdout.splitlines()[0], "客戶設定檔有改：型別 +0 -1 ~0、每類段落 +0 -2 ~0")
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 -1（拿掉了空夾 廠商）、每類段落 -2"])
        self.assertNotIn("廠商", {x["type"] for x in self.data()["synthesis_sections"]})
        self.assertNotIn("| 廠商 |", self.md_text())

    def test_section_four_error_sentence(self):
        """第四節的型別從來不在第一節（打錯字）照樣報錯。"""
        self.md.write_text(self.md_text().replace("| 案子 | 時程 |", "| 工地 | 時程 |"), encoding="utf-8", newline="\n")
        self.assertEqual(self.apply(3), ["客戶設定檔讀不懂：第 4 節第 1 列的型別「工地」不在第 1 節"])



class AboutResync(Base):
    """測試後修正裁定 12：上次 apply 寫完設定檔、關於你沒登記成（實體清單壞了），修好後再 apply（沒有內容差異）會補上。"""

    def test_apply_without_diff_still_syncs_about(self):
        from kb._lib import entities
        ent = self.V / ".system" / "entities.json"
        ent.write_text("{bad", encoding="utf-8")
        self.md.write_text(self.md_text().rstrip("\n") + "\n| 林小姐室內設計 | 公司 | 林小姐 |\n", encoding="utf-8",
                           newline="\n")
        line = self.apply(3)[0]  # 設定檔已寫入、關於你那步沒做完（測試後修正裁定 19）
        self.assertTrue(line.startswith("設定檔已改好，但後面沒做完（.system/entities.json（"), line)
        self.assertTrue(line.endswith("）；跑 kb profile apply 會補上"), line)
        ent.unlink()
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用", "關於你：登記了 林小姐室內設計"])
        self.assertEqual([e["name"] for e in entities.load(self.V)["entities"]], ["林小姐室內設計"])
        self.assertEqual(self.programs()[-1]["note"], "關於你：登記了 林小姐室內設計")
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用"], "冪等：第二次沒有動到就不印")


class Edit(Base):
    """note 改規則第 2 點、驗收 2～6（D-144、D-110 補、D-137）：show、add、remove 每節成功各一、拒絕句一字不差。"""

    def run_ok(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def refused(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        return r.stdout.strip()

    def hashes(self):
        return {p.relative_to(self.V).as_posix(): p.read_bytes() for p in self.V.rglob("*")
                if p.is_file() and ".system/versions/" not in p.as_posix() and ".lock" not in p.name}

    def test_show(self):
        lines = self.run_ok("show")
        self.assertIn("## 一、型別", lines)
        self.assertIn("## 六、偏好", lines)
        self.assertIn("| 案子 | 事 |  | 推測 | 2026-10-19 |", lines)
        data = json.loads(self.kb("show", "--json").stdout)
        self.assertEqual([s["name"] for s in data["sections"]], ["型別", "卡型", "不整理與敏感", "每類段落", "關於你", "偏好"])
        self.assertIsNone(data["pending_edit"])
        self.assertEqual(data["sections"][5]["limits"], {"count": 10, "chars": 400, "used_count": 0, "used_chars": 0})
        self.assertEqual(data["sections"][0]["add"], "kb profile add 型別 名稱={名稱} 大類={大類} 一句說明={一句說明}")

    def test_preference_add_limits_remove(self):
        t = turn.current(self.V)["turn"]
        self.assertEqual(self.run_ok("add", "偏好", "偏好=回答先講結論"), ["已加偏好：回答先講結論"])
        data = self.data()
        self.assertEqual((data["schema_version"], data["preferences"]),
                         (3, [{"text": "回答先講結論", "source": "corrected", "date": self.today}]))
        self.assertIn(f"## 六、偏好", self.md_text())
        self.assertIn(f"| 回答先講結論 | 糾正 | {self.today} |", self.md_text())
        self.assertEqual(data["md_sha256"], profile.md_sha256(self.V))
        manifest = {e["path"] for e in turn.entries(self.V, t)}
        self.assertTrue({profile.PROFILE_POSIX, profile.MD_POSIX} <= manifest)
        self.assertEqual((self.programs()[-1]["command"], self.programs()[-1]["note"]), ("profile add", "已加偏好：回答先講結論"))
        self.assertEqual(self.run_ok("add", "偏好", "偏好=回答先講結論"), ["偏好已經有了：回答先講結論"])
        for i in range(9):
            self.run_ok("add", "偏好", f"偏好=第{i}條")
        before = self.hashes()
        self.assertEqual(self.refused("add", "偏好", "偏好=再一條"), "偏好最多 10 條（現在 10 條），先合併或拿掉一條")
        self.assertEqual(self.hashes(), before)
        for i in range(9):
            self.run_ok("remove", "偏好", "序號=2")
        self.assertEqual(self.run_ok("add", "偏好", "偏好=" + "長" * 384), ["已加偏好：" + "長" * 384])
        before = self.hashes()
        self.assertEqual(self.refused("add", "偏好", "偏好=" + "短" * 33), "偏好合計最多 400 字（加了會是 423 字），先合併或縮短")
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.run_ok("remove", "偏好", "序號=1"), ["已拿掉偏好 1：回答先講結論"])
        self.assertEqual(self.refused("remove", "偏好", "序號=5"), "沒有這一列：序號=5")

    def test_add_type_makes_folder_and_section_four(self):
        t = turn.current(self.V)["turn"]
        self.assertEqual(self.run_ok("add", "型別", "名稱=設計師", "大類=對象", "一句說明=外包設計"),
                         ["已加型別「設計師」（對象）；建了資料夾 設計師"])
        self.assertTrue((self.V / "設計師").is_dir())
        self.assertEqual([(r["op"], r["from"]) for r in turn.folders(self.V, t)], [("make", "設計師")])
        rows = [(s["type"], x["name"]) for s in self.data()["synthesis_sections"] for x in s["sections"]]
        self.assertIn(("設計師", "聯絡與往來"), rows)
        self.assertEqual(self.refused("add", "型別", "名稱=設計師", "大類=事"), "型別「設計師」已經有了，大類不能這樣改")
        self.assertEqual(self.refused("remove", "型別", "名稱=設計師"), "拿掉型別用 kb profile type remove")
        self.assertEqual(self.refused("add", "型別", "名稱=系統", "大類=事"), "第 1 節第 3 列的型別名「系統」不能用（跟保留字一樣）")
        self.assertEqual(self.run_ok("add", "型別", "名稱=設計師", "一句說明=室內設計外包"), ["已改型別「設計師」：一句說明"])
        self.assertEqual(self.data()["types"][-1]["note"], "室內設計外包")

    def test_sections_two_to_five(self):
        item = next(i for i in json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"])
        card = _cards.write_card(self.V, item, "報價卡", kind="報價")
        card_bytes = card.read_bytes()
        self.assertEqual(self.run_ok("add", "卡型", "卡型=會議紀錄", "段落=待辦", "必出現=✓"), ["已加卡型「會議紀錄」的段落「待辦」"])
        self.assertIn("| 會議紀錄 | 待辦 | ✓ |", self.md_text())
        self.assertEqual(self.run_ok("remove", "卡型", "卡型=報價"), ["已拿掉卡型「報價」（4 段）；已寫好的卡照留"])
        self.assertNotIn("報價", [c["name"] for c in self.data()["card_types"]])
        self.assertEqual(card.read_bytes(), card_bytes, "舊卡不動（D-138）")
        self.assertEqual(self.run_ok("add", "不整理與敏感", "種類=請款", "處理=不整理"), ["已加不整理：請款（不整理）"])
        ids = [i["id"] for i in json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"]][:2]
        inventory.set_skip(self.V, ids, "請款")
        self.assertEqual(self.run_ok("remove", "不整理與敏感", "種類=請款"), ["已拿掉不整理：請款；2 份回到待製卡"])
        new = {i["id"] for i in json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"]}
        self.assertTrue(set(ids) <= new)
        self.assertEqual(self.run_ok("add", "每類段落", "型別=案子", "段落=預算與實際花費"), ["已加每類段落「案子」的「預算與實際花費」"])
        self.assertEqual(self.run_ok("remove", "每類段落", "型別=案子", "段落=預算與實際花費"), ["已拿掉每類段落「案子」的「預算與實際花費」"])
        self.assertEqual(self.run_ok("add", "關於你", "名稱=林美華", "是什麼=本人", "別名=Amy"),
                         ["已加關於你：林美華（本人）", "關於你：登記了 林美華"])
        from kb._lib import entities
        (me,) = [e for e in entities.load(self.V)["entities"] if e.get("self")]
        self.assertEqual((me["name"], me["self"], me["aliases"]), ("林美華", "person", ["Amy"]))
        self.assertEqual(self.run_ok("remove", "關於你", "名稱=林美華"), ["已拿掉關於你：林美華", "關於你：林美華 不再記成你自己"])

    def test_bad_section_column_and_argument(self):
        before = self.hashes()
        self.assertEqual(self.refused("add", "偏好啦", "偏好=x"), "沒有「偏好啦」這一節；節是 型別、卡型、不整理與敏感、每類段落、關於你、偏好")
        self.assertEqual(self.refused("add", "偏好", "名稱=x"), "「偏好」這一節沒有「名稱」欄；欄是 偏好")
        self.assertEqual(self.refused("add", "偏好", "回答先講結論"), "「回答先講結論」要寫成 欄=值")
        self.assertEqual(self.refused("remove", "卡型", "卡型=估價單"), "沒有這一列：卡型=估價單")
        self.assertEqual(self.hashes(), before)

    def test_pending_hand_edit_refused(self):
        self.add_types(ROW)
        before = self.hashes()
        r = self.kb("add", "偏好", "偏好=先講結論", "--json")
        self.assertEqual(r.returncode, 3)
        got = json.loads(r.stdout)
        self.assertEqual((got["applied"], got["message"]), (False, "設定檔有你改過還沒套用的地方（型別 +1），先套用再改"))
        self.assertEqual(got["question"], "設定檔有你改過還沒套用的地方（型別 +1），要先套用嗎？")
        self.assertEqual([(x["n"], x["label"], x["do"], x["strict"]) for x in got["results"]],
                         [(1, "先套用我改的，再記這句", ["kb profile apply", "kb profile add 偏好 偏好=先講結論"], False),
                          (2, "先不記", [], False)])
        self.assertEqual(self.refused("add", "偏好", "偏好=先講結論"), "設定檔有你改過還沒套用的地方（型別 +1），先套用再改")
        self.assertEqual(self.hashes(), before)
        show = json.loads(self.kb("show", "--json").stdout)
        self.assertEqual(show["pending_edit"], {"summary": "型別 +1", "error": None})
        self.add_types("| 壞的 | 事 |")
        got = json.loads(self.kb("add", "偏好", "偏好=先講結論", "--json").stdout)
        self.assertEqual(got["question"], "設定檔有你改過但讀不懂：第 1 節第 3 列欄數不對（要 3 欄或 5 欄），改好之前我記不了")
        self.assertEqual([x["label"] for x in got["results"]], ["先不記"])
        self.assertEqual(json.loads(self.kb("show", "--json").stdout)["pending_edit"],
                         {"summary": None, "error": "第 1 節第 3 列欄數不對（要 3 欄或 5 欄）"})

    def test_in_conversation_no_program_line(self):
        turns = self.V / ".system" / "turns"
        turns.mkdir(exist_ok=True)
        (turns / "current.json").write_text(json.dumps({
            "schema_version": 1, "turn": "20261020-100000-test", "session_id": "test-sid",
            "started": "2026-10-20T10:00:00", "prompt_head": "", "approved": False, "note": "", "done": False}),
            encoding="utf-8")
        n = len(self.programs())
        self.run_ok("add", "偏好", "偏好=回答先講結論")
        self.assertEqual(len(self.programs()), n)
        self.assertIn(profile.PROFILE_POSIX, {e["path"] for e in turn.entries(self.V, "20261020-100000-test")})


SECRET = "偏好裡不要放密碼或帳號；這種要記的話等「密碼與守門」那一版"
CHARS = '偏好裡不能有 " $ ` \\ 這四種字元'


class SecondRound(Base):
    """note 改規則測試後修正裁定 13、14、16、19（F-2、F-3、F-5、F-9）：拒絕句一字不差，apply 走同一個解析器。"""

    def refused(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        return r.stdout.strip()

    def hashes(self):
        return {p.relative_to(self.V).as_posix(): p.read_bytes() for p in self.V.rglob("*")
                if p.is_file() and ".system/versions/" not in p.as_posix() and ".lock" not in p.name}

    def hand_add(self, section_head, row):
        """md 某節表格最後加一列（手改）。section_head 是下一節的標題開頭（第六節給 None：加在檔尾）。"""
        text = self.md_text()
        if section_head is None:
            text = text.rstrip("\n") + "\n" + row + "\n"
        else:
            at = text.rindex("|\n", 0, text.index(section_head)) + 2
            text = text[:at] + row + "\n" + text[at:]
        self.md.write_text(text, encoding="utf-8", newline="\n")

    def test_preference_secrets_refused(self):
        before = self.hashes()
        for text in ("用 password: hunter2 登入 ERP 後再回答", "ERP 帳號 jie 密碼 abc123", "token 是 sk-123",
                     "先讀 id_rsa 再回答"):
            with self.subTest(text=text):
                self.assertEqual(self.refused("add", "偏好", f"偏好={text}"), SECRET)
        self.assertEqual(self.hashes(), before)
        self.kb("add", "偏好", "偏好=回答先講結論")  # 有第六節的 md（fixture 是 schema 2）
        self.hand_add(None, "| 用 password: hunter2 登入 |")
        self.assertEqual(self.apply(3), [f"客戶設定檔讀不懂：第 6 節第 2 列：{SECRET}"])
        self.assertNotIn("hunter2", (self.V / ".system" / "profile.json").read_text(encoding="utf-8"))

    def test_preference_four_chars_refused(self):
        before = self.hashes()
        for text in ("含$符號", '含"引號', "含`反引號", "含\\反斜線"):
            with self.subTest(text=text):
                self.assertEqual(self.refused("add", "偏好", f"偏好={text}"), CHARS)
        self.assertEqual(self.hashes(), before)
        self.kb("add", "偏好", "偏好=回答先講結論")
        self.hand_add(None, "| 含$符號 |")
        self.assertEqual(self.apply(3), [f"客戶設定檔讀不懂：第 6 節第 2 列：{CHARS}"])

    def test_names_refuse_shell_chars_and_knowledge(self):
        before = self.hashes()
        cases = [(("型別", "名稱=x$(whoami)", "大類=物"), "第 1 節第 2 列的型別名「x$(whoami)」不能用（含不能用的字元）"),
                 (("型別", "名稱=y`id`", "大類=物"), "第 1 節第 2 列的型別名「y`id`」不能用（含不能用的字元）"),
                 (("型別", "名稱=知識庫", "大類=物"), "第 1 節第 2 列的型別名「知識庫」不能用（跟保留字一樣）"),
                 (("關於你", "名稱=a$b", "是什麼=公司"), "第 5 節第 1 列的名稱「a$b」不能用（含不能用的字元）"),
                 (("關於你", "名稱=林小姐", "是什麼=公司", "別名=a`b"), "第 5 節第 1 列的別名「a`b」不能用（含不能用的字元）")]
        for args, want in cases:
            with self.subTest(want=want):
                self.assertEqual(self.refused("add", *args), want)
        self.assertEqual(self.hashes(), before)
        self.assertFalse((self.V / "x$(whoami)").exists())
        self.assertEqual(run_kb("profile", "type", "rename", "案子", "--to", "x$y", cwd=self.V).stdout.strip(),
                         "型別名「x$y」不能用（含不能用的字元）")
        self.add_types("| 知識庫 | 物 |  |")
        self.assertEqual(self.apply(3), ["客戶設定檔讀不懂：第 1 節第 2 列的型別名「知識庫」不能用（跟保留字一樣）"])

    def test_add_type_entity_card_then_revert_leaves_no_folder(self):
        """F-2 的重現：同回合加型別、登記實體、寫綜合卡，退回後不留空夾、不印「裡有東西」。"""
        from kb._lib import entities
        self.assertEqual(self.kb("add", "型別", "名稱=設計師", "大類=對象").returncode, 0)
        r = run_kb("entity", "add", "--type", "設計師", "--name", "王設計師", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rel = "設計師/王設計師/王設計師.md"
        t = turn.current(self.V)["turn"]
        turn.backup(self.V, rel, t)
        (self.V / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.V / rel).write_text("王設計師\n", encoding="utf-8")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("裡有東西", r.stdout)
        self.assertFalse((self.V / "設計師").exists())
        self.assertNotIn("設計師", [t["name"] for t in profile.load(self.V)["types"]])
        self.assertEqual([e["name"] for e in entities.load(self.V)["entities"] if e["name"] == "王設計師"], [])
        self.assertNotIn("第一層夾不在型別表", self.kb("check").stdout)

    def run_in_process(self, *argv, patches=()):
        import contextlib
        import io
        import os
        from kb import cli
        from kb._lib import io as kio
        out, cwd, timeout = io.StringIO(), os.getcwd(), kio.LOCK_TIMEOUT
        os.chdir(self.V)
        try:
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                stack.enter_context(contextlib.redirect_stdout(out))
                code = cli.main(list(argv))
        finally:
            os.chdir(cwd)
            kio.LOCK_TIMEOUT = timeout
        return code, out.getvalue().strip().splitlines()

    def test_follow_up_failure_says_written_and_apply_fills_in(self):
        from unittest import mock
        from kb._lib import entities
        code, lines = self.run_in_process(
            "profile", "add", "關於你", "名稱=林美華", "是什麼=本人",
            patches=[mock.patch.object(entities, "sync_self", side_effect=TimeoutError("等待檔案鎖逾時"))])
        self.assertEqual((code, lines), (3, ["設定檔已改好，但後面沒做完（等不到鎖）；跑 kb profile apply 會補上"]))
        self.assertEqual([a["name"] for a in self.data()["about"]], ["林美華"])
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用", "關於你：登記了 林美華"])
        self.assertEqual([e["name"] for e in entities.load(self.V)["entities"] if e.get("self")], ["林美華"])

    def test_follow_up_unskip_failure_then_apply_fills_in(self):
        from unittest import mock
        self.assertEqual(self.kb("add", "不整理與敏感", "種類=請款", "處理=不整理").returncode, 0)
        ids = [i["id"] for i in json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"]][:2]
        inventory.set_skip(self.V, ids, "請款")
        code, lines = self.run_in_process(
            "profile", "remove", "不整理與敏感", "種類=請款",
            patches=[mock.patch.object(inventory, "unskip_kinds", side_effect=OSError(13, "被別的程式開著"))])
        self.assertEqual((code, lines),
                         (3, ["設定檔已改好，但後面沒做完（[Errno 13] 被別的程式開著）；跑 kb profile apply 會補上"]))
        self.assertNotIn("請款", [e["kind"] for e in self.data()["exclusions"]])
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用", "2 份回到待製卡"])
        new = {i["id"] for i in json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)["new"]}
        self.assertTrue(set(ids) <= new)
        self.assertEqual(self.apply(), ["客戶設定檔沒有改，沒有套用"], "冪等：第二次沒有動到就不印")


if __name__ == "__main__":
    unittest.main()
