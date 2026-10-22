"""kb output save／link／drop-draft 黑箱（produce 第 2 點，驗收 7、8、9、13；實作回報裁定 2）：subprocess 跑 kb.py，cwd 在 vault。
<V> 是 tests/fixtures/inventory 的複本（tests/_cards 組的，有一張會議紀錄的卡）；回合用 turn.start 開，模擬對話裡的那一句。"""
import json
import re
import shutil
import unittest
from pathlib import Path

from tests import _cards
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, inventory, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

SID = "sid-output"
ID_PATTERN = re.compile(r"[aefsrcp]-[0-9a-f]{8}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        item = next(i for i in cards.todo(self.V, sources_lib.load(self.V))["new"] if i["uri"].endswith("會議紀錄_1.docx"))
        _cards.write_card(self.V, item, "王宅合約", kind="合約")
        self.fid = item["id"]
        self.t = turn.start(self.V, SID, "做報價")["turn"]
        self.ws = self.V / ".system" / "temp" / "office" / self.t
        self.ws.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb("output", *args, cwd=self.V)

    def ok(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(ID_PATTERN.findall(r.stdout), [], "人讀輸出不印編號")
        return r.stdout.strip()

    def refused(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        return r.stdout.strip()

    def work(self, name="a.xlsx", data=b"PK xlsx"):
        p = self.ws / name
        p.write_bytes(data)
        return p

    def entry(self, rel):
        return inventory.entry_for(self.V, "file://" + rel)

    def manifest(self, turn_id=None):
        return {e["path"]: e["action"] for e in turn.entries(self.V, turn_id or self.t)}

    def draft(self, name="2026-10-21 內湖李 報價（草稿）.md"):
        """模擬檔案工具在前一回合寫的草稿：備份（create）、寫、hook 登記。"""
        t0 = turn.start(self.V, SID, "先給草稿")["turn"]
        rel = f"AI 產出/報價/{name}"
        turn.backup(self.V, rel, t0)
        (self.V / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.V / rel).write_text("| 項目 | 金額 |\n", encoding="utf-8")
        inventory.register_output(self.V, rel, t0)
        self.t = turn.start(self.V, SID, "可以，定稿")["turn"]
        self.ws = self.V / ".system" / "temp" / "office" / self.t
        self.ws.mkdir(parents=True, exist_ok=True)
        return rel


class Save(Base):
    def test_save_from_workspace(self):
        src = self.work()
        out = self.ok("save", str(src), "--to", "AI 產出/報價/a.xlsx", "--from", self.fid)
        self.assertEqual(out, "已放進 AI 產出\\報價\\a.xlsx，改自「王宅合約」（說「退回」可以退回這一步；這份檔會先留著，要刪再跟我說）")
        self.assertFalse(src.exists(), "工作區的檔用搬的")
        self.assertEqual((self.V / "AI 產出/報價/a.xlsx").read_bytes(), b"PK xlsx")
        self.assertEqual(self.manifest()["AI 產出/報價/a.xlsx"], "create")
        e = self.entry("AI 產出/報價/a.xlsx")
        self.assertEqual((e["origin"], e["status"], e["derived_from"], e["created_turn"]),
                         ("ai_output", "active", self.fid, self.t))
        self.assertNotIn("unbacked", e)
        new = {i["id"] for i in cards.todo(self.V, sources_lib.load(self.V))["new"]}
        self.assertIn(e["id"], new)
        data = json.loads(self.kb("save", str(self.work("b.xlsx")), "--to", "AI 產出/報價/b.xlsx", "--json").stdout)
        self.assertEqual((data["path"], data["id"] is not None), ("AI 產出/報價/b.xlsx", True))

    def test_revert_marks_turn_reverted(self):
        self.ok("save", str(self.work()), "--to", "AI 產出/報價/a.xlsx")
        e = self.entry("AI 產出/報價/a.xlsx")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = next(x for x in inventory.load(self.V)["entries"] if x["id"] == e["id"])
        self.assertEqual(got["status"], "turn_reverted")

    def test_outside_vault_copied(self):
        outside = self.tmp / "下載" / "提案.pptx"
        outside.parent.mkdir()
        outside.write_bytes(b"pptx")
        self.ok("save", str(outside), "--to", "AI 產出/簡報/提案.pptx")
        self.assertTrue(outside.exists(), "知識庫外的檔用複製的")
        self.assertEqual((self.V / "AI 產出/簡報/提案.pptx").read_bytes(), b"pptx")

    def test_same_name_gets_2(self):
        self.ok("save", str(self.work()), "--to", "AI 產出/報價/a.xlsx")
        out = self.ok("save", str(self.work()), "--to", "AI 產出/報價/a.xlsx")
        self.assertEqual(out, "已放進 AI 產出\\報價\\a (2).xlsx（說「退回」可以退回這一步；這份檔會先留著，要刪再跟我說）")
        self.assertTrue((self.V / "AI 產出/報價/a (2).xlsx").is_file())

    def test_refusals_exact(self):
        src = self.work()
        before = sorted(p.relative_to(self.V).as_posix() for p in (self.V / "AI 產出").rglob("*"))
        self.assertEqual(self.refused("save", str(src), "--to", "案子/a.xlsx"), "只能放進 AI 產出底下")
        self.assertEqual(self.refused("save", str(src), "--to", "AI 產出/../系統/a.xlsx"), "只能放進 AI 產出底下")
        self.assertEqual(self.refused("save", str(src), "--to", "AI 產出/a.md"), "md 檔用檔案工具寫就好")
        self.assertEqual(self.refused("save", str(self.ws / "沒有.xlsx"), "--to", "AI 產出/a.xlsx"),
                         f"找不到這個檔：{self.ws / '沒有.xlsx'}")
        self.assertEqual(self.refused("save", str(src), "--to", "AI 產出/id_rsa.pem"), "這個檔名像密碼或金鑰，不放")
        inside = self.V / "案子" / "說明.txt"
        self.assertEqual(self.refused("save", str(inside), "--to", "AI 產出/說明.txt"),
                         "只收工作區或知識庫外的檔，這份在知識庫裡")
        self.assertEqual(self.refused("save", str(src), "--to", "AI 產出/a.xlsx", "--draft", "案子/說明.txt"),
                         "草稿不在 AI 產出：案子/說明.txt")
        self.assertEqual(self.refused("save", str(src), "--to", "AI 產出/a.xlsx", "--link", "https://example.com/x"),
                         "網址要是 claude.ai 的 artifact")
        self.assertTrue(src.exists())
        self.assertEqual(sorted(p.relative_to(self.V).as_posix() for p in (self.V / "AI 產出").rglob("*")), before)


class SaveSources(Base):
    """收尾 security-review：知識庫外的來源照 convert 同一套擋——排除的、檔名像密碼的拒絕，客戶原檔要先 office copy；
    --to 不收冒號（NTFS 資料流）；output 不收 --vault（同 convert，會把檔案內容帶給 AI）。"""

    def test_excluded_credential_and_originals_refused(self):
        run_kb("folders", "exclude", str(self.raw / "公司行政" / "舊檔"), "--kind", "unused", cwd=self.V)
        self.assertEqual(self.refused("save", str(self.raw / "公司行政" / "舊檔" / "Big5備忘.txt"), "--to", "AI 產出/a.txt"),
                         "這個位置你說過不讀")
        self.assertEqual(self.refused("save", str(self.raw / "公司行政" / "薪資" / "網銀密碼.txt"), "--to", "AI 產出/a.txt"),
                         "這個檔名像密碼或金鑰，不放")
        key = self.tmp / "ssh" / "id_rsa"
        key.parent.mkdir()
        key.write_text("x", encoding="utf-8")
        self.assertEqual(self.refused("save", str(key), "--to", "AI 產出/notes.txt"), "這個檔名像密碼或金鑰，不放")
        self.assertEqual(self.refused("save", str(self.raw / "報價與合約" / "合約範本.txt"), "--to", "AI 產出/a.txt"),
                         "客戶原檔先用 kb office copy 拿副本")
        self.assertFalse((self.V / "AI 產出" / "a.txt").exists())
        self.assertFalse((self.V / "AI 產出" / "notes.txt").exists())

    def test_colon_in_destination_refused(self):
        src = self.work()
        for to in ("AI 產出/r.md::$DATA", "AI 產出/a.docx:s"):
            with self.subTest(to=to):
                self.assertEqual(self.refused("save", str(src), "--to", to), "檔名不能有冒號")

    def test_no_vault_flag(self):
        r = self.kb("save", str(self.work()), "--to", "AI 產出/a.xlsx", "--vault", str(self.V))
        self.assertEqual(r.returncode, 2)


class Draft(Base):
    def test_draft_not_carded_and_asked_after_turn(self):
        rel = self.draft()
        e = self.entry(rel)
        new = {i["id"] for i in cards.todo(self.V, sources_lib.load(self.V))["new"]}
        self.assertNotIn(e["id"], new)
        asks = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)["asks"]
        (ask,) = [a for a in asks if a["kind"] == "draft"]
        self.assertEqual(ask["question"], "內湖李 報價 的草稿還在等你看")
        drop = ask["results"][2]
        self.assertEqual((drop["label"], drop["strict"], drop["do"]),
                         ("不要了", True, [f'kb output drop-draft "{rel}"']))

    def test_save_with_draft_then_revert(self):
        rel = self.draft()
        out = self.ok("save", str(self.work()), "--to", "AI 產出/報價/2026-10-21 內湖李 報價.xlsx", "--draft", rel)
        self.assertEqual(out, "已放進 AI 產出\\報價\\2026-10-21 內湖李 報價.xlsx，草稿收起來了（說「退回」可以退回這一步；這份檔會先留著，要刪再跟我說）")
        self.assertFalse((self.V / rel).exists())
        self.assertEqual(self.manifest()[rel], "delete")
        self.assertTrue((self.V / ".system" / "versions" / self.t / "files" / rel).is_file(), "草稿備進回合")
        self.assertEqual(self.entry(rel)["status"], "deleted")
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.V / rel).is_file(), "退回後草稿回來")
        self.assertEqual(self.entry(rel)["status"], "active")

    def test_drop_draft(self):
        rel = self.draft()
        self.assertEqual(self.ok("drop-draft", rel), "草稿收起來了：2026-10-21 內湖李 報價（草稿）.md（說「退回」會回來）")
        self.assertFalse((self.V / rel).exists())
        self.assertEqual(self.manifest()[rel], "delete")
        self.assertEqual(self.refused("drop-draft", rel), f"草稿不在 AI 產出：{rel}")
        self.assertEqual(self.refused("drop-draft", "案子/說明.txt"), "草稿不在 AI 產出：案子/說明.txt")


class Link(Base):
    URL = "https://claude.ai/artifact/PFWe7FqJWiKJ"

    def test_link_then_download_attached(self):
        out = self.ok("link", self.URL, "--title", "內湖李進度報告", "--type", "報告", "--basis", self.fid)
        self.assertEqual(out, "已登記線上文件「內湖李進度報告」（下次整理會做卡）")
        e = inventory.entry_for(self.V, self.URL)
        self.assertEqual((e["kind"], e["origin"], e["title"], e["type"], e["basis"]),
                         ("link", "ai_output", "內湖李進度報告", "報告", [self.fid]))
        item = next(i for i in cards.todo(self.V, sources_lib.load(self.V))["new"] if i["id"] == e["id"])
        self.assertIn(f"開原檔: {self.URL}", item["header"])
        self.ok("save", str(self.work("進度報告.docx")), "--to", "AI 產出/報告/進度報告.docx", "--link", self.URL)
        e = inventory.entry_for(self.V, self.URL)
        self.assertEqual([loc["uri"] for loc in e["locations"]], [self.URL, "file://AI 產出/報告/進度報告.docx"])
        self.assertEqual(len([x for x in inventory.load(self.V)["entries"] if x.get("kind") == "link"]), 1)

    def test_link_refusals(self):
        self.assertEqual(self.refused("link", "https://example.com/a", "--title", "x"), "網址要是 claude.ai 的 artifact")
        self.assertEqual(self.refused("link", "http://claude.ai/artifact/a", "--title", "x"), "網址要是 claude.ai 的 artifact")
        self.assertEqual(self.refused("link", self.URL, "--title", "x", "--entity", "沒登記"),
                         "實體清單沒有「沒登記」，先 kb entity add")
        self.assertEqual(self.refused("link", self.URL, "--title", "x", "--basis", "f-00000000"), "清冊沒有 f-00000000")
        self.assertEqual(self.refused("save", str(self.work()), "--to", "AI 產出/a.docx", "--link", self.URL),
                         "這個網址還沒登記；先跑 kb output link")
        self.assertEqual(inventory.entry_for(self.V, self.URL), None)



class Round2(Base):
    """測試後修正裁定 13（link 進回合帳、退回拿掉 link）、14（同網址拒絕）、20 L-3（工作區來源收窄到 office/）、L-4（標題擋 $ 與反引號）。"""

    URL = "https://claude.ai/artifact/bbb"

    def test_link_is_its_own_turn_and_revert_removes_it(self):
        self.ok("save", str(self.work()), "--to", "AI 產出/報價/a.xlsx")
        saved = self.entry("AI 產出/報價/a.xlsx")
        t_b = turn.start(self.V, SID, "做成線上文件")["turn"]
        self.ok("link", self.URL, "--title", "內湖李進度報告")
        self.assertIn(".system/inventory.json", {e["path"] for e in turn.entries(self.V, t_b)})
        r = run_kb("versions", "revert", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNone(inventory.entry_for(self.V, self.URL), "退回拿掉的是 B 的 link")
        again = next(x for x in inventory.load(self.V)["entries"] if x["id"] == saved["id"])
        self.assertEqual(again["status"], "active", "A 的成品留著")

    def test_same_url_refused(self):
        self.ok("link", self.URL, "--title", "內湖李進度報告", "--type", "報告")
        before = inventory.entry_for(self.V, self.URL)
        self.assertEqual(self.refused("link", self.URL, "--title", "v2"), "這個網址已經登記過了：內湖李進度報告")
        self.assertEqual(inventory.entry_for(self.V, self.URL), before)

    def test_title_shell_chars_refused(self):
        for title in ("報價$(whoami)", "報價`id`"):
            with self.subTest(title=title):
                self.assertEqual(self.refused("link", self.URL, "--title", title), "標題不能有 $ 或 ` 這兩種字元")

    def test_workspace_source_is_office_only(self):
        other = self.V / ".system" / "temp" / "converted.txt"
        other.write_text("x", encoding="utf-8")
        self.assertEqual(self.refused("save", str(other), "--to", "AI 產出/a.txt"),
                         "只收工作區或知識庫外的檔，這份在知識庫裡")
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
