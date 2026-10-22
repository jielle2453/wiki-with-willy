"""kb office 黑箱（produce 第 1 點，驗收 1、3、4、6、13）：subprocess 跑 kb.py，cwd 在 vault。
check 不寫檔；copy 的副本在工作區、原檔指紋不變、排除／憑證名／Z11／不在清冊各拒絕一字不差；run 工作區外拒絕；
這台有 LibreOffice 時真的轉一份 docx 成 PDF、轉圖、重算一份有公式的 xlsx，沒有就 skip。"""
import hashlib
import json
import re
import shutil
import unittest
from pathlib import Path

from tests import _cards, _produce
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import office, turn  # noqa: E402

ID_PATTERN = re.compile(r"[aefsrcp]-[0-9a-f]{8}")
HAS_LO = office.find_soffice() is not None
HAS_PDFIUM = office.pdfium_ok()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.t = turn.start(self.V, "sid-office", "改合約")["turn"]
        self.ws = self.V / ".system" / "temp" / "office" / self.t

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb("office", *args, cwd=self.V)

    def ok(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(ID_PATTERN.findall(r.stdout), [], "人讀輸出不印編號")
        return r.stdout.strip().splitlines()

    def refused(self, *args):
        r = self.kb(*args)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        return r.stdout.strip()

    def files(self):
        return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns for p in self.V.rglob("*") if p.is_file()}


class Check(Base):
    def test_check_writes_nothing(self):
        before = self.files()
        lines = self.ok("check")
        self.assertTrue(lines[0].startswith("Office："))
        self.assertTrue(any(ln.startswith("可以：") for ln in lines))
        data = json.loads(self.kb("check", "--json").stdout)
        self.assertEqual(set(data["env"]), {"office", "soffice", "node", "node_version", "npm", "node_packages",
                                            "py_missing", "pdfium", "tools"})
        self.assertEqual(set(data["capabilities"]), {"can", "after_setup", "cannot", "setup"})
        self.assertEqual(self.files(), before)


class Copy(Base):
    def entry_id(self, rel):
        return _cards.entry_of(self.V, rel)["id"]

    def test_copy_by_id(self):
        src = self.raw / _cards.MEETING
        before = sha(src)
        fid = self.entry_id("客戶案件/" + _cards.MEETING.split("/", 1)[1])
        lines = self.ok("copy", fid)
        copy = Path(lines[0][len("副本："):])
        self.assertEqual(copy, self.ws / "會議紀錄_1.docx")
        self.assertEqual(sha(copy), before)
        self.assertEqual(sha(src), before, "原檔不動")
        data = json.loads(self.kb("copy", fid, "--json").stdout)
        self.assertEqual((data["id"], Path(data["copy"]).name), (fid, "會議紀錄_1 (2).docx"))

    def test_refusals_exact(self):
        run_kb("folders", "exclude", str(self.raw / "公司行政" / "舊檔"), "--kind", "unused", cwd=self.V)
        self.assertEqual(self.refused("copy", str(self.raw / "公司行政" / "舊檔" / "Big5備忘.txt")),
                         "拿不到副本：這個位置你說過不讀")
        self.assertEqual(self.refused("copy", str(self.raw / "公司行政" / "薪資" / "網銀密碼.txt")),
                         "拿不到副本：檔名像密碼或金鑰，預設不讀")
        other = self.tmp / "外面.docx"
        other.write_bytes(b"x")
        self.assertEqual(self.refused("copy", str(other)), "拿不到副本：不在登記的資料夾裡")
        fresh = self.raw / "客戶案件" / "大安陳" / "剛放的.docx"
        fresh.write_bytes(b"x")
        self.assertEqual(self.refused("copy", str(fresh)), "拿不到副本：清冊沒有這份檔；先跑 kb inventory scan")
        self.assertEqual(self.refused("copy", "f-00000000"), "拿不到副本：清冊沒有 f-00000000")
        self.assertFalse(self.ws.exists() and any(self.ws.iterdir()), "拒絕時不留副本")

    def test_copy_takes_no_vault_flag(self):
        r = self.kb("copy", "f-00000000", "--vault", str(self.V))
        self.assertEqual(r.returncode, 2, "跟 convert 一樣不收 --vault（實作回報裁定 5）")


class Run(Base):
    def test_run(self):
        outside = self.tmp / "x.py"
        outside.write_text("print(1)\n", encoding="utf-8")
        self.assertTrue(self.refused("run", str(outside)).startswith("腳本要放在工作區裡"))
        self.ws.mkdir(parents=True)
        script = self.ws / "make.py"
        script.write_text("import os\nprint('OUT=' + os.environ['OUT_DIR'])\nraise SystemExit(int(os.sys.argv[1]))\n",
                          encoding="utf-8")
        lines = self.ok("run", str(script), "0")
        self.assertEqual(lines, [f"OUT={self.ws}", "結束碼：0"])
        r = self.kb("run", str(script), "4")
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (3, "結束碼：4"))


@unittest.skipUnless(HAS_LO, "這台沒有 LibreOffice")
class RealLibreOffice(Base):
    """驗收 6：真的轉 PDF、轉圖；重算一份 openpyxl 風格（公式沒有快取值）的 xlsx。"""

    def setUp(self):
        super().setUp()
        self.ws.mkdir(parents=True)

    def test_pdf_and_render(self):
        _produce.docx(self.ws / "報告.docx", ["內湖李進度報告", "第一期完工"], [["期別", "金額"], ["一", "100"]])
        lines = self.ok("pdf", str(self.ws / "報告.docx"))
        self.assertEqual(lines, [f"PDF：{self.ws / '報告.pdf'}（用 LibreOffice 轉的）"])
        self.assertTrue((self.ws / "報告.pdf").read_bytes().startswith(b"%PDF"))
        if not HAS_PDFIUM:
            self.skipTest("這台沒有 pypdfium2")
        lines = self.ok("render", str(self.ws / "報告.docx"), "--pages", "1")
        self.assertEqual(len(lines), 1)
        png = Path(lines[0].split("：", 1)[1])
        self.assertTrue(lines[0].startswith("第 1 頁："))
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png.parent, self.ws)

    def test_recalc(self):
        xlsx = self.ws / "報價.xlsx"
        _produce.xlsx(xlsx, [["木作", 45000], ["油漆", 120000], ["合計", "=B1+B2"]])
        lines = self.ok("recalc", str(xlsx))
        self.assertEqual(lines[0], "重算好了（LibreOffice）：公式 1 個、錯誤 0 個")
        import zipfile
        with zipfile.ZipFile(xlsx) as z:
            sheet = next(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
            self.assertIn("165000", z.read(sheet).decode("utf-8"))


class Places(Base):
    def test_pdf_only_workspace_or_output(self):
        raw_file = self.raw / _cards.MEETING
        self.assertEqual(self.refused("pdf", str(raw_file)), f"只收工作區或 AI 產出裡的檔：{raw_file}")
        self.assertTrue(self.refused("recalc", str(raw_file)).startswith("只收工作區裡的檔"))


if __name__ == "__main__":
    unittest.main()
