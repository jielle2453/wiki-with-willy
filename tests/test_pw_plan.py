"""整理的介面（密碼與守門第 7 點；D-181、D-182、D-183；驗收 5、8、9 的 tell）：
plan 多 unlock_same_kind（有授權的種類、待處理有新的「有密碼」PDF；排在寫卡之前、files 與 kinds 欄位齊），沒種類時沒有這步；
asks 多 credential_same_kind（有帶 label 的「可以讀」裁示時每份新的密碼檔一題，排在原檔不見之後，decline 後不再列）；
tell：加密的 PDF、加密的 Word 分開講，不製卡的密碼檔接「要讓我讀哪一份」，同一種開了講一句；有裁示的密碼檔進待製卡。"""
import json
import shutil
import unittest

from tests import _cards, _pw
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import cards, plan, rulings, turn, unlock  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

try:
    import pypdfium2  # noqa: F401
    HAVE_PDF = True
except Exception:  # noqa: BLE001
    HAVE_PDF = False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        self.old = unlock.BACKEND
        unlock.BACKEND = unlock.MemoryBackend()
        self.t = turn.start(self.V, None, "")["turn"]

    def tearDown(self):
        unlock.BACKEND = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fid(self, rel):
        return _cards.entry_of(self.V, rel)["id"]

    def todo(self, tell_all=True):
        return cards.todo(self.V, sources_lib.load(self.V), tell_all=tell_all)

    def lock(self, *rels):
        for rel in rels:
            r = run_kb("convert", self.fid(rel), cwd=self.V)
            self.assertEqual(r.returncode, 3, r.stdout)


class Tell(Base):
    def test_locked_pdf_and_docx_and_credential_sentences(self):
        self.lock(_pw.WANG, _pw.ENC_DOCX) if HAVE_PDF else self.lock(_pw.ENC_DOCX)
        tell = self.todo()["tell"]
        if HAVE_PDF:
            self.assertIn("保險單_王.pdf 讀不到：有密碼；給我密碼就能打開，要我記住也可以", tell)
        self.assertTrue(any(t.startswith("合約_加密.docx 讀不到：有密碼；這種檔這版還打不開，用 Office 打開另存一份沒密碼的再放進來")
                            for t in tell), tell)
        line = next(t for t in tell if t.startswith("不製卡："))
        self.assertIn("檔名像密碼或金鑰 3", line)
        self.assertTrue(line.endswith("；要讓我讀哪一份跟我說「X 可以讀」"), line)
        self.assertFalse(any("另存一份沒密碼的，或跟我說先跳過" in t for t in tell), tell)

    def test_ruled_credential_file_goes_to_new(self):
        rulings.add(self.V, "可以讀", [], [], "網站密碼.xlsx 可以讀", self.t, files=[self.fid(_pw.WEB)])
        out = self.todo()
        self.assertIn(self.fid(_pw.WEB), [i["id"] for i in out["new"]])
        self.assertEqual(out["skipped"]["credential"], 2)


@unittest.skipUnless(HAVE_PDF, "沒裝 pypdfium2")
class UnlockSameKind(Base):
    """驗收 8。"""

    def test_no_kinds_no_step(self):
        self.lock(_pw.WANG, _pw.CHEN)
        self.assertNotIn("unlock_same_kind", [s["step"] for s in self.todo()["plan"]])

    def test_step_before_cards_with_files_and_kinds(self):
        self.lock(_pw.WANG, _pw.CHEN)
        unlock.save(self.V, "PDF 密碼", "保險單", _pw.PDF_PW)
        unlock.authorize(self.V, "保險單", "保險單", [self.fid(_pw.WANG)], self.t)
        out = self.todo()
        steps = [s["step"] for s in out["plan"]]
        self.assertIn("unlock_same_kind", steps)
        self.assertLess(steps.index("unlock_same_kind"), steps.index("cards"))
        step = next(s for s in out["plan"] if s["step"] == "unlock_same_kind")
        self.assertEqual(step["items"], 2)
        self.assertEqual({f["name"] for f in step["files"]}, {"保險單_王.pdf", "保險單_陳.pdf"})
        for f in step["files"]:
            self.assertEqual(set(f), {"id", "name", "folder", "size", "date"})
        self.assertEqual(step["kinds"], [{"label": "保險單", "credential": "保險單",
                                          "examples": [{"name": "保險單_王.pdf", "folder": "客戶案件/信義王"}]}])
        self.assertIn("carding.md", step["read"])
        self.assertEqual(_pw.leaks(self.V), [])

    def test_opened_tell(self):
        unlock.record_opened(self.V, self.fid(_pw.CHEN), "保險單", turn.current(self.V)["turn"])
        self.assertIn("「保險單_陳.pdf」我判斷跟之前的保險單是同一種，用那組密碼打開了", self.todo()["tell"])


class CredentialSameKind(Base):
    """驗收 5：帶 label 的裁示之後，新的密碼檔 → asks 有 credential_same_kind、排在 missing 之後；decline 後不再列。"""

    def test_asks_after_missing_then_decline(self):
        rulings.add(self.V, "可以讀", [], [], "網站密碼.xlsx 可以讀", self.t, files=[self.fid(_pw.WEB)], label="網站密碼表")
        (self.raw / "客戶案件" / "內湖李" / "新網站密碼.txt").write_text("新的網站帳密\n", encoding="utf-8")
        (self.raw / "客戶案件" / "內湖李" / "估價單.pdf").unlink()
        r = run_kb("inventory", "scan", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        asks = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)["asks"]
        kinds = [a["kind"] for a in asks]
        self.assertIn("credential_same_kind", kinds)
        self.assertIn("missing", kinds)
        self.assertLess(kinds.index("missing"), kinds.index("credential_same_kind"))
        new = next(a for a in asks if a["kind"] == "credential_same_kind" and a["file"]["name"] == "新網站密碼.txt")
        self.assertEqual(new["question"], "又有一份「新網站密碼.txt」，跟之前的網站密碼表一樣可以讀嗎？")
        self.assertEqual(new["labels"], [{"label": "網站密碼表", "examples": ["網站密碼.xlsx"]}])
        labels = [r["label"] for r in new["results"]]
        self.assertEqual(labels, ["可以讀", "不要", "不是同一種（不問）"])
        self.assertTrue(new["results"][1]["do"][0].startswith("kb ruling decline --file "))
        fid = new["file"]["id"]
        self.assertEqual(run_kb("ruling", "decline", "--file", fid, cwd=self.V).returncode, 0)
        asks = json.loads(run_kb("asks", "--json", cwd=self.V).stdout)["asks"]
        self.assertNotIn(fid, [a.get("file", {}).get("id") for a in asks])

    def test_no_label_no_question(self):
        rulings.add(self.V, "可以讀", [], [], "網站密碼.xlsx 可以讀", self.t, files=[self.fid(_pw.WEB)])
        self.assertNotIn("credential_same_kind", [a["kind"] for a in self.todo()["asks"]])


if __name__ == "__main__":
    unittest.main()
