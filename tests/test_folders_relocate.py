"""kb folders relocate（manage 第二版第 5 點，D-177）：全對上、部分對上（比例與例子）、全對不上（照樣印、--check 不動）、
新位置不在、跟別的來源重疊、保留別名；換過去後別名、排除、清冊 uri 不變，「開原檔」指新位置；退回回到舊位置。"""
import contextlib
import io
import json
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests import _cards, _entities
from kb import cli, folders, versions
from kb._lib import cards, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402

ID_LIKE = re.compile(r"\b[aefsrcp]-[0-9a-f]{8}\b")


def kb(*args):
    saved = kio.LOCK_TIMEOUT
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
        code = cli.main(list(args))
    kio.LOCK_TIMEOUT = saved  # CLI 把鎖等待調成 30 秒，不留給後面的測試
    return code, out.getvalue()


class Relocate(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _entities.make_entity_vault()
        _entities.put_entities(self.V, [_entities.entity("e-2b2b2b2b", "內湖李")])
        self.card_rel = _entities.write_card(self.V, _entities.ESTIMATE, "內湖李估價", ["內湖李"])
        p = Path(self.V) / ".system/sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "大安陳", "kind": "unused", "reason": "", "added": "2026-10-22"})
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _cards.scan_apply(self.V)
        self.new = self.tmp / "E槽" / "客戶案件"
        turn.start(self.V, None, "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sources(self):
        return json.loads((Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"))

    def inventory_text(self):
        return (Path(self.V) / ".system/inventory.json").read_text(encoding="utf-8")

    def relocate(self, *extra):
        return kb("folders", "relocate", "客戶案件", str(self.new), *extra, "--vault", str(self.V))

    def test_full_match_check_then_do_then_revert(self):
        shutil.copytree(self.raw / "客戶案件", self.new)
        before = (json.dumps(self.sources()), self.inventory_text())
        code, out = self.relocate("--check")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("新位置：") and lines[0].endswith("E槽/客戶案件"), lines[0])
        self.assertRegex(lines[1], r"^抽樣 (\d+) 份：\1 份對得上$")
        self.assertIn("不讀的夾：大安陳 在新位置找到了", lines)
        self.assertEqual((json.dumps(self.sources()), self.inventory_text()), before)
        uris = re.findall(r'"uri": "([^"]+)"', self.inventory_text())
        code, out = self.relocate()
        self.assertEqual(code, 0, out)
        self.assertTrue(out.splitlines()[0].startswith("已把「客戶案件」換到新位置："), out)
        self.assertTrue(out.splitlines()[0].endswith("；說「退回上一版」換得回來"))
        self.assertIsNone(ID_LIKE.search(out), out)
        src = self.sources()["sources"][0]
        self.assertEqual(src["alias"], "客戶案件")
        self.assertEqual(src["spec"], "../E槽/客戶案件")
        self.assertEqual(src["excludes"][0]["spec"], "大安陳")
        self.assertEqual(sorted(re.findall(r'"uri": "([^"]+)"', self.inventory_text())), sorted(uris))
        front = cards.parse_front((Path(self.V) / self.card_rel).read_text(encoding="utf-8"))
        self.assertEqual(front["開原檔"], cards.open_link(self.new / "內湖李" / "估價單.pdf"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(versions._revert(self.V, None, False, False), 0)
        self.assertEqual(self.sources()["sources"][0]["spec"], "../raw/客戶案件")
        front = cards.parse_front((Path(self.V) / self.card_rel).read_text(encoding="utf-8"))
        self.assertEqual(front["開原檔"], cards.open_link(self.raw / "客戶案件" / "內湖李" / "估價單.pdf"))

    def test_partial(self):
        shutil.copytree(self.raw / "客戶案件", self.new)
        (self.new / "內湖李").rename(self.new / "內湖李_整理過")
        code, out = self.relocate("--check")
        self.assertEqual(code, 0, out)
        m = re.match(r"^抽樣 (\d+) 份：(\d+) 份對得上，1 份不在原本的位置（例：內湖李/估價單.pdf）$", out.splitlines()[1])
        self.assertIsNotNone(m, out)
        self.assertEqual(int(m.group(1)) - 1, int(m.group(2)))

    def test_none_match_still_reports_and_check_keeps(self):
        self.new.mkdir(parents=True)
        (self.new / "別的.txt").write_text("x", encoding="utf-8")
        before = json.dumps(self.sources())
        code, out = self.relocate("--check")
        self.assertEqual(code, 0, out)
        self.assertRegex(out.splitlines()[1], r"^抽樣 (\d+) 份：0 份對得上，\1 份不在原本的位置（例：")
        self.assertIn("不讀的夾：大安陳 在新位置找不到", out)
        self.assertEqual(json.dumps(self.sources()), before)

    def test_refusals(self):
        code, out = self.relocate()
        self.assertEqual((code, out.strip()), (3, f"找不到 {self.new}"))
        code, out = kb("folders", "relocate", "客戶案件", str(self.raw / "報價與合約" / "信義王"), "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (3, "和既有來源「報價與合約」重疊，同一份檔會有兩張卡；要細分請在來源裡建子夾"))
        self.new.mkdir(parents=True)
        code, out = kb("folders", "relocate", "知識庫", str(self.new), "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (3, "「知識庫」是知識庫自己的，不用換位置"))
        code, out = kb("folders", "relocate", "沒有這個", str(self.new), "--vault", str(self.V))
        self.assertEqual((code, out.strip()), (3, "沒有叫「沒有這個」的來源"))

    def test_same_folder_is_not_overlap_with_itself(self):
        code, out = kb("folders", "relocate", "客戶案件", str(self.raw / "客戶案件"), "--vault", str(self.V), "--check")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
