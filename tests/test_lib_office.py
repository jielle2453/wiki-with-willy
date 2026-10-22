"""_lib/office（produce 第 1 點，驗收 1、2、4、5、6）：check 的偵測在四種假環境（有 Office、只有 LibreOffice、都沒有、沒 npm）；
setup 用假的 npm／pip（_call 替身）、裝完驗 import 失敗會重試；run 拒收工作區外的腳本、環境變數設好；pack --like 順序；
pdf／recalc 的呼叫層用替身（這台沒有 Office），沒有工具回固定句。真的轉檔在 test_scripts_office_cli（有 LibreOffice 才跑）。"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests import _ooxml
from tests.test_lib_inventory_scan import make_inv_vault
from kb._lib import office  # noqa: E402

OFFICE_ALL = {"word": "C:/Office/WINWORD.EXE", "excel": "C:/Office/EXCEL.EXE", "powerpoint": "C:/Office/POWERPNT.EXE"}
NO_OFFICE = {"word": None, "excel": None, "powerpoint": None}


class FakeEnv(unittest.TestCase):
    """工具夾放在 tmp（LOCALAPPDATA 換掉），偵測函式換成假的。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kboffice-"))
        self.patches = [mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp / "local")})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake(self, office_apps=NO_OFFICE, soffice=None, node=None, npm=None):
        for p in (mock.patch.object(office, "find_office", return_value=dict(office_apps)),
                  mock.patch.object(office, "find_soffice", return_value=soffice),
                  mock.patch.object(office, "find_node", return_value=(node, "v24.20.0" if node else None)),
                  mock.patch.object(office, "find_npm", return_value=npm),
                  mock.patch.object(office, "pdfium_ok", return_value=True)):
            p.start()
            self.patches.append(p)

    def install(self, py=True, node=True):
        if py:
            for name in office.PY_PACKAGES:
                (office.pylib() / name).mkdir(parents=True, exist_ok=True)
        if node:
            for name in office.NODE_PACKAGES:
                (office.node_modules() / name).mkdir(parents=True, exist_ok=True)


class Check(FakeEnv):
    def test_office_everything(self):
        self.fake(OFFICE_ALL, None, "C:/node/node.exe", "C:/node/npm.cmd")
        lines = office.check_lines(office.detect())
        self.assertEqual(lines[:4], ["Office：有（Word、Excel、PowerPoint）", "LibreOffice：沒有",
                                     "Node.js：有（v24.20.0）；工具夾 node 套件：沒裝", "python 套件：沒裝"])
        self.assertEqual(lines[4], "可以：改 Word、改 Excel、改簡報、轉 PDF、看版面、重算 Excel；"
                                   "裝好工具後也可以新做 Word、新做簡報、新做 Excel")
        self.assertEqual(lines[5], "要先做：kb office setup（大約 1 分鐘）")
        self.install()
        lines = office.check_lines(office.detect())
        self.assertEqual(lines[-1], "可以：改 Word、改 Excel、改簡報、新做 Word、新做簡報、新做 Excel、轉 PDF、看版面、重算 Excel")

    def test_only_libreoffice(self):
        self.fake(NO_OFFICE, "C:/LO/soffice.exe", "C:/node/node.exe", "C:/node/npm.cmd")
        self.install()
        lines = office.check_lines(office.detect())
        self.assertEqual(lines[:2], ["Office：沒有", "LibreOffice：有（C:/LO/soffice.exe）"])
        self.assertEqual(lines[-1], "可以：改 Word、改 Excel、改簡報、新做 Word、新做簡報、新做 Excel、轉 PDF、看版面、重算 Excel")

    def test_nothing(self):
        self.fake()
        env = office.detect()
        lines = office.check_lines(env)
        self.assertEqual(lines[:4], ["Office：沒有", "LibreOffice：沒有", "Node.js：沒有", "python 套件：沒裝"])
        self.assertEqual(lines[4], "可以：改 Word、改 Excel、改簡報；裝好工具後也可以新做 Excel；"
                                   "不行：新做 Word、新做簡報（要裝 Node.js）、轉 PDF、看版面（要裝 LibreOffice）、"
                                   "重算 Excel（要裝 LibreOffice）")
        self.assertEqual(lines[5], "要先做：kb office setup（大約 1 分鐘）")
        cap = office.capabilities(env)
        self.assertEqual(set(cap), {"can", "after_setup", "cannot", "setup"})

    def test_node_without_npm(self):
        self.fake(NO_OFFICE, "C:/LO/soffice.exe", "C:/node/node.exe", None)
        self.install(node=False)
        lines = office.check_lines(office.detect())
        self.assertEqual(lines[2], "Node.js：有（v24.20.0）；沒有 npm；工具夾 node 套件：沒裝")
        self.assertIn("不行：新做 Word、新做簡報（要裝 Node.js）", lines[-1])
        self.assertNotIn("要先做", "\n".join(lines))


class Setup(FakeEnv):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.fail_verify = 0
        p = mock.patch.object(office, "_sleep", lambda s: self.calls.append(("sleep", s)))
        p.start()
        self.patches.append(p)
        p = mock.patch.object(office, "_call", self.call)
        p.start()
        self.patches.append(p)

    def call(self, argv, timeout, env=None, cwd=None):
        self.calls.append(tuple(argv))
        if "pip" in argv:
            target = Path(argv[argv.index("--target") + 1])
            for name in office.PY_PACKAGES:
                (target / name).mkdir(parents=True, exist_ok=True)
            return 0, "Successfully installed"
        if argv[1:2] == ["install"]:
            prefix = Path(argv[argv.index("--prefix") + 1])
            for name in office.NODE_PACKAGES:
                (prefix / "node_modules" / name).mkdir(parents=True, exist_ok=True)
            return 0, "added 25 packages"
        if argv[1] == "-c" and argv[2] == office.PY_IMPORTS:
            self.assertEqual(env["PYTHONPATH"], str(office.pylib()))
            if self.fail_verify:
                self.fail_verify -= 1
                return 1, "ImportError: cannot import name '_imaging'"
            return 0, ""
        if argv[1] == "-e":
            self.assertEqual(env["NODE_PATH"], str(office.node_modules()))
            return 0, ""
        raise AssertionError(argv)

    def test_installs_both_then_already(self):
        self.fake(NO_OFFICE, None, "C:/node/node.exe", "C:/node/npm.cmd")
        self.fail_verify = 2
        res = office.setup()
        self.assertEqual((res["already"], res["python"], res["node"], res["skipped_node"]),
                         (False, list(office.PY_PACKAGES), list(office.NODE_PACKAGES), False))
        self.assertEqual(res["tries"], {"python": 3, "node": 1})
        self.assertEqual([c for c in self.calls if c[0] == "sleep"], [("sleep", 5), ("sleep", 5)])
        self.assertTrue(str(office.tools_dir()).startswith(str(self.tmp)))
        self.assertTrue(all((office.pylib() / n).is_dir() for n in office.PY_PACKAGES))
        self.assertTrue(office.node_packages_ok())
        self.calls.clear()
        self.assertTrue(office.setup()["already"])
        self.assertEqual(self.calls, [])

    def test_no_npm_python_only(self):
        self.fake(NO_OFFICE, None, None, None)
        res = office.setup()
        self.assertEqual((res["python"], res["node"], res["skipped_node"]), (list(office.PY_PACKAGES), [], True))
        self.assertFalse(any(c[1:2] == ("install",) for c in self.calls))

    def test_verify_gives_up(self):
        self.fake(NO_OFFICE, None, None, None)
        self.fail_verify = 9
        with self.assertRaises(office.Unavailable) as c:
            office.setup()
        self.assertIn("python 套件裝好了但載不起來", str(c.exception))
        self.assertEqual(len([x for x in self.calls if x[0] == "sleep"]), office.VERIFY_TRIES - 1)


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, _raw = make_inv_vault()
        self.ws = office.workspace(self.V, "20261022-101010-test")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workspace_place_and_clean(self):
        self.assertEqual(self.ws, self.V / ".system" / "temp" / "office" / "20261022-101010-test")
        old = self.V / ".system" / "temp" / "office" / "20200101-000000-old"
        old.mkdir()
        (old / "x.docx").write_bytes(b"x")
        os.utime(old, (0, 0))
        office.workspace(self.V, "20261022-101011-test")
        self.assertFalse(old.exists(), "超過 temp_keep_hours 的工作區清掉")
        self.assertTrue(self.ws.exists(), "還新的留著")
        with self.assertRaises(office.Refused):
            office.workspace(self.V, "../x")

    def test_run_refuses_outside_and_sets_env(self):
        outside = self.tmp / "x.py"
        outside.write_text("print('hi')\n", encoding="utf-8")
        with self.assertRaises(office.Refused) as c:
            office.run_script(self.V, outside, [], "20261022-101010-test")
        self.assertTrue(str(c.exception).startswith("腳本要放在工作區裡"))
        script = self.ws / "make.py"
        script.write_text("import os\nprint('OUT', os.environ['OUT_DIR'])\nprint('PP', os.environ['PYTHONPATH'])\n"
                          "print('CWD', os.getcwd())\nimport sys\nprint('ARG', sys.argv[1])\n", encoding="utf-8")
        code, tail, ws = office.run_script(self.V, script, ["甲"], "20261022-101010-test")
        self.assertEqual(code, 0, tail)
        self.assertIn(f"OUT {ws}", tail)
        self.assertIn(f"PP {office.pylib()}", tail)
        self.assertIn(f"CWD {os.path.realpath(ws)}", [f"CWD {os.path.realpath(t[4:])}" if t.startswith("CWD ") else t for t in tail])
        self.assertIn("ARG 甲", tail)
        bad = self.ws / "a.sh"
        bad.write_text("x", encoding="utf-8")
        with self.assertRaises(office.Refused):
            office.run_script(self.V, bad, [], "20261022-101010-test")

    def test_unpack_pack_like_keeps_parts(self):
        src = self.ws / "合約.docx"
        _ooxml.docx(src, [("p", "付款條件：完工驗收後 15 日內"), ("tbl", [["期別", "金額"], ["一", "100"]])])
        with zipfile.ZipFile(src) as z:  # 打亂順序：沒 --like 的 pack 會改順序
            members = [(i.filename, z.read(i.filename)) for i in z.infolist()]
        shuffled = self.ws / "亂序.docx"
        with zipfile.ZipFile(shuffled, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in reversed(members):
                z.writestr(name, data)
        folder = office.unpack(self.V, shuffled)
        doc = folder / "word" / "document.xml"
        doc.write_text(doc.read_text(encoding="utf-8").replace("15 日內", "30 日內"), encoding="utf-8")
        out = office.pack(self.V, folder, "改好.docx", like=shuffled)
        with zipfile.ZipFile(shuffled) as a, zipfile.ZipFile(out) as b:
            self.assertEqual(a.namelist(), b.namelist(), "檔內順序照原檔")
            for name in a.namelist():
                if name == "word/document.xml":
                    self.assertIn("30 日內", b.read(name).decode("utf-8"))
                else:
                    self.assertEqual(a.read(name), b.read(name), name)
        (folder / "[Content_Types].xml").write_text("<Types/>", encoding="utf-8")
        plain = office.pack(self.V, folder, "沒照.docx")
        with zipfile.ZipFile(plain) as z:
            self.assertEqual(z.namelist()[0], "[Content_Types].xml")
        with self.assertRaises(office.Refused):
            office.pack(self.V, folder, str(self.tmp / "外面.docx"))
        with self.assertRaises(office.Refused):
            office.unpack(self.V, self.tmp / "x.docx")

    def test_unpack_refuses_unsafe_member(self):
        evil = self.ws / "evil.docx"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../../x.txt", "x")
        with self.assertRaises(office.Refused):
            office.unpack(self.V, evil)
        self.assertFalse((self.V / ".system" / "x.txt").exists())


class Convert(FakeEnv):
    """呼叫層替身：有 Office 用 PowerShell COM、只有 LibreOffice 用 soffice、都沒有回固定句。"""

    def setUp(self):
        super().setUp()
        self.vtmp, self.V, _raw = make_inv_vault()
        self.ws = office.workspace(self.V, "20261022-101010-test")
        self.src = self.ws / "a.docx"
        self.src.write_bytes(b"x")
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.vtmp, ignore_errors=True)
        super().tearDown()

    def call(self, argv, timeout, env=None, cwd=None):
        self.calls.append((list(argv), dict(env or {})))
        out = Path((env or {}).get("KB_OUT") or self.ws / "a.pdf")
        recalc = (argv[0] == "powershell.exe" and not (env or {}).get("KB_OUT")) or argv[-2:-1] and "Module1" in argv[-2]
        if not recalc:
            out.write_bytes(b"%PDF-1.4")
        else:  # 重算：改寫檔案
            target = Path((env or {}).get("KB_IN") or argv[-1])
            target.write_bytes(target.read_bytes() + b" ")
        return 0, ""

    def test_word_com_when_office(self):
        with mock.patch.object(office, "_call", self.call):
            pdf, used = office.to_pdf(self.V, self.src, self.ws, {"office": OFFICE_ALL, "soffice": "C:/LO/soffice.exe"})
        argv, env = self.calls[0]
        self.assertEqual((argv[0], used, pdf), ("powershell.exe", "Word", self.ws / "a.pdf"))
        self.assertIn("Word.Application", argv[-1])
        self.assertEqual((env["KB_IN"], env["KB_OUT"]), (str(self.src), str(self.ws / "a.pdf")))

    def test_libreoffice_args(self):
        with mock.patch.object(office, "_call", self.call):
            _pdf, used = office.to_pdf(self.V, self.src, self.ws, {"office": NO_OFFICE, "soffice": "C:/LO/soffice.exe"})
        argv, _env = self.calls[0]
        self.assertEqual(used, "LibreOffice")
        self.assertEqual(argv[:3], ["C:/LO/soffice.exe", "--headless", "--norestore"])
        self.assertTrue(argv[3].startswith("-env:UserInstallation=file:"))
        self.assertIn("_lo_profile", argv[3])
        self.assertEqual(argv[4:], ["--convert-to", "pdf", "--outdir", str(self.ws), str(self.src)])

    def test_excel_recalc_com(self):
        xlsx = self.ws / "a.xlsx"
        _ooxml.xlsx(xlsx, [("報價", [[("n", "1"), ("f", "A1*2", None)]])])
        with mock.patch.object(office, "_call", self.call):
            res = office.recalc(self.V, xlsx, {"office": OFFICE_ALL, "soffice": None})
        self.assertEqual((res["used"], res["formulas"], res["errors"]), ("Excel", 1, []))
        self.assertIn("CalculateFull", self.calls[0][0][-1])

    def test_no_tools_fixed_sentences(self):
        env = {"office": NO_OFFICE, "soffice": None}
        with self.assertRaises(office.Unavailable) as c:
            office.to_pdf(self.V, self.src, self.ws, env)
        self.assertEqual(str(c.exception), office.NO_CONVERTER)
        self.assertEqual(office.NO_CONVERTER, "這台沒有能轉 PDF 或重算的程式；要的話可以裝 LibreOffice（免費），或用 Word／Excel 打開另存")
        with self.assertRaises(office.Unavailable) as c:
            office.recalc(self.V, self.src, env)
        self.assertEqual(str(c.exception), office.NO_CONVERTER + "\n公式的結果要打開檔案才會更新")

    def test_formula_report_counts_errors(self):
        xlsx = self.ws / "e.xlsx"
        with zipfile.ZipFile(xlsx, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row r="1"><c r="A1"><f>1/0</f><v>1</v></c>'
                                                   '<c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c></row></sheetData></worksheet>')
        self.assertEqual(office.formula_report(xlsx), (2, [("sheet1", "B1", "#DIV/0!")]))

    def test_parse_pages(self):
        self.assertEqual(office.parse_pages("1-3,5", 20), [1, 2, 3, 5])
        self.assertIsNone(office.parse_pages(None, 20))
        self.assertEqual(office.parse_pages("1-30", 20), list(range(1, 21)))
        for bad in ("a", "3-1", "0"):
            with self.subTest(bad=bad), self.assertRaises(office.Refused):
                office.parse_pages(bad, 20)


class ComSecurity(unittest.TestCase):
    def test_macros_forced_off_before_open(self):
        for script in list(office._PS_PDF.values()) + [office._PS_RECALC]:
            with self.subTest(script=script[:40]):
                self.assertIn("AutomationSecurity=3", script)
                self.assertLess(script.index("AutomationSecurity=3"), script.index(".Open("))


class NoNode(unittest.TestCase):
    def test_js_without_node(self):
        tmp, V, _raw = make_inv_vault()
        try:
            ws = office.workspace(V, "20261022-101010-test")
            (ws / "a.js").write_text("1", encoding="utf-8")
            with mock.patch.object(office, "find_node", return_value=(None, None)), \
                    self.assertRaises(office.Unavailable) as c:
                office.run_script(V, ws / "a.js", [], "20261022-101010-test")
            self.assertEqual(str(c.exception), "新做 Word 或簡報要 Node.js；這台沒有，可以改做成線上文件，或請你裝 Node.js")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
