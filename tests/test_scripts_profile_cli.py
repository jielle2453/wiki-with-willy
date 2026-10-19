"""kb profile check／apply 黑箱（客戶設定檔與型別第 2 點；驗收 3～7，實作疑義裁定 1、2：用 fixture 沒有的「工地」）。
<V> 是 tests/fixtures/inventory 的複本、全掃過一次；fixture 的設定檔型別表只有「案子」。"""
import json
import shutil
import unittest
from datetime import date

from tests import _cards
from tests.test_lib_inventory_scan import make_inv_vault
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import journal, profile, turn  # noqa: E402

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
        self.drop_line("| 案子 |")
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
        self.drop_line("| 工班 |")
        self.assertIn("- 型別 少了 工班（空夾會一起拿掉）", self.kb("check").stdout.splitlines())
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 -1（拿掉了空夾 工班）、每類段落 -2"])
        self.assertFalse((self.V / "工班").exists(), "空夾一起拿掉（確認後修正裁定 21）")
        self.assertEqual(self.programs()[-1]["note"], "套用客戶設定檔：型別 -1（拿掉了空夾 工班）、每類段落 -2")
        self.assertEqual([t["name"] for t in self.data()["types"]], ["案子", "工地"])
        (self.V / "工地" / "a.txt").write_text("x", encoding="utf-8")
        self.drop_line("| 工地 |")
        before_json, before_md = self.data(), self.md_text()
        self.assertEqual(self.apply(3), ["型別「工地」底下還有 1 項，先搬走再拿掉這列"])
        self.assertEqual((self.data(), self.md_text()), (before_json, before_md))

    def test_rename_is_one_less_one_more(self):
        self.add_types("| 客戶 | 對象 | 業主 |")
        self.apply()
        text = self.md_text().replace("| 客戶 |", "| 業主 |")  # 第四節的列跟著改，不然讀不懂（型別不在第 1 節）
        self.md.write_text(text, encoding="utf-8", newline="\n")
        lines = self.kb("check").stdout.splitlines()
        self.assertIn("- 型別 多了 業主（對象）", lines)
        self.assertIn("- 型別 少了 客戶（空夾會一起拿掉）", lines)
        self.assertEqual(self.apply(), ["已套用客戶設定檔：型別 +1 -1（拿掉了空夾 客戶）、每類段落 +2 -2；建了資料夾 業主"])
        self.assertTrue(not (self.V / "客戶").exists() and (self.V / "業主").is_dir())

    def test_rmdir_failure_keeps_folder_and_says_so(self):
        import contextlib
        import io
        from unittest import mock
        from kb import profile_cli
        self.add_types("| 客戶 | 對象 | 業主 |")
        self.apply()
        self.drop_line("| 客戶 |")
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

    def test_revert_restores_json_and_md_and_keeps_folder(self):
        turn.start(self.V, None, "")
        before_json, before_md = self.data(), self.md_text()
        self.add_types(ROW)
        self.apply()
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.data(), self.md_text()), (before_json, before_md))
        self.assertTrue((self.V / "工地").is_dir())
        self.assertIn("套用設定檔時建的資料夾還在，要拿掉請自己刪空夾", r.stdout.splitlines())


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


if __name__ == "__main__":
    unittest.main()
