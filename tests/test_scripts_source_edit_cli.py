"""kb source-edit 黑箱（manage 第二版第 12 點）：subprocess 跑 kb.py；成功句與拒絕句、--check 不動、人讀不印編號而 --json 有；
trash 真的丟一份 tmp 檔進資源回收筒一次（回收筒不能用的機器 skip），檢查回收筒裡有、再把它從回收筒拿掉。"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests import _cards, _entities
from tests._guard_fixture import KB, ROOT
from kb._lib import source_edit as se  # noqa: E402

ID_LIKE = re.compile(r"\b[aefsrcp]-[0-9a-f]{8}\b")


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(cwd), env=env)


def bin_records(path):
    """資源回收筒裡記著這個原始路徑的 $I 檔（目前使用者那一夾）。"""
    drive = os.path.splitdrive(str(path))[0] + "\\"
    needle = os.path.abspath(str(path)).encode("utf-16-le")
    out = []
    root = Path(drive) / "$Recycle.Bin"
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for d in dirs:
        try:
            for f in d.glob("$I*"):
                try:
                    if needle in f.read_bytes():
                        out.append(f)
                except OSError:
                    pass
        except OSError:
            pass
    return out


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _entities.make_entity_vault()
        _entities.put_entities(self.V, [_entities.entity("e-2b2b2b2b", "內湖李")])
        self.card_rel = _entities.write_card(self.V, _entities.ESTIMATE, "內湖李估價", ["內湖李"])
        self.fid = _cards.entry_of(self.V, _entities.ESTIMATE)["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args):
        return run_kb("source-edit", *args, cwd=self.V)

    def test_move_check_then_move_then_json(self):
        dest = str(self.raw / "客戶案件" / "大安陳")
        r = self.kb("move", self.fid, "--to", dest, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["要搬：估價單.pdf 從 內湖李 搬到 大安陳", "卡會跟著改"])
        self.assertTrue((self.raw / _entities.ESTIMATE).is_file())
        r = self.kb("move", self.fid, "--to", dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已搬好：估價單.pdf 從 內湖李 搬到 大安陳；卡跟著改了（說「退回」會搬回去）")
        self.assertIsNone(ID_LIKE.search(r.stdout))
        r = self.kb("rename", "file://客戶案件/大安陳/估價單.pdf", "估價單_簽約版", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["entry_id"], self.fid)
        self.assertEqual(data["sentence"], "已改名：估價單.pdf → 估價單_簽約版.pdf（說「退回」會改回來）")

    def test_refusals_exit_3(self):
        r = self.kb("rename", self.fid, "合約.docx")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "副檔名不改：.pdf → .docx"))
        r = self.kb("move", self.fid, "--to", str(Path(self.V) / "AI 產出"))
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "不搬進「AI 產出」；要放哪個夾跟我說"))
        r = self.kb("trash", str(self.raw / "客戶案件"))
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "整個夾請在檔案總管刪"))
        r = self.kb("open", "a", "b", "c")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "一次最多開兩個夾"))

    def test_where_no_ids(self):
        r = self.kb("where", "--entity", "內湖李")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "內湖李的檔放在：")
        self.assertIsNone(ID_LIKE.search(r.stdout))

    def test_real_recycle_bin_once(self):
        target = self.raw / _entities.MEMO
        try:
            se.recycle_room(target)
        except se.BinUnavailable as e:
            self.skipTest(f"這台的資源回收筒用不了：{e}")
        r = self.kb("trash", str(target), "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "要丟進資源回收筒：Big5備忘.txt（公司行政\\舊檔）")
        self.assertTrue(target.is_file())
        r = self.kb("trash", str(target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "已丟進資源回收筒：Big5備忘.txt")
        self.assertFalse(target.exists())
        found = bin_records(target)
        try:
            self.assertTrue(found, "資源回收筒裡找不到這份")
        finally:
            for i_file in found:  # 測試的檔不留在客戶（開發機）的回收筒
                r_file = i_file.with_name("$R" + i_file.name[2:])
                for f in (i_file, r_file):
                    try:
                        f.unlink()
                    except OSError:
                        pass


if __name__ == "__main__":
    unittest.main()
