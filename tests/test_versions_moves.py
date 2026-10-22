"""versions revert 讀 moves.jsonl（manage 第二版第 3、4 點，D-175）：退回搬回、卡與清冊跟回來；原位有同名整個不退；
moves.jsonl 被改壞（絕對路徑、..、沒登記的別名、排除裡的路徑）各拒絕、什麼都沒動；trash 那句；purged 那句；versions list 的白話。"""
import contextlib
import io
import json
import os
import re
from pathlib import Path
from unittest import mock

from tests import _cards
from tests.test_lib_source_edit import ESTIMATE, Base, entry, inv
from kb import versions
from kb._lib import source_edit as se
from kb._lib import turn  # noqa: E402

ID_LIKE = re.compile(r"\b[aefsrcp]-[0-9a-f]{8}\b")


def revert(V):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = versions._revert(V, None, False, False)
    return code, out.getvalue()


def listing(V):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        versions._list(V, 5, False)
    return out.getvalue()


class RecordMove(Base):
    def test_record_and_read_back(self):
        turn.record_move(self.V, self.t, "move", "file", "file://a/x.txt", "file://b/x.txt")
        turn.record_move(self.V, self.t, "trash", "file", "file://a/y.txt")
        with open(Path(self.V) / ".system/versions" / self.t / "moves.jsonl", "a", encoding="utf-8") as f:
            f.write("壞行\n")
        rows = turn.moves(self.V, self.t)
        self.assertEqual([(r["op"], r["kind"], r["from"], r["to"]) for r in rows],
                         [("move", "file", "file://a/x.txt", "file://b/x.txt"), ("trash", "file", "file://a/y.txt", None)])
        with self.assertRaises(ValueError):
            turn.record_move(self.V, self.t, "copy", "file", "file://a/x.txt")


class RevertMoves(Base):
    def test_file_move_back_and_cards_follow(self):
        before_card = (Path(self.V) / self.card_rel).read_bytes()
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))
        code, out = revert(self.V)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.raw / ESTIMATE).is_file())
        self.assertFalse((self.raw / "客戶案件" / "大安陳" / "估價單.pdf").exists())
        self.assertEqual(entry(self.V, self.fid)["locations"][0]["uri"], "file://" + ESTIMATE)
        self.assertEqual((Path(self.V) / self.card_rel).read_bytes(), before_card)
        self.assertFalse(list((Path(self.V) / "檔案索引/客戶案件/大安陳").glob("*.md")))
        self.assertIn("估價單.pdf：從 大安陳 搬到 內湖李", out)

    def test_folder_move_back_restores_exclude(self):
        lee = self.raw / "客戶案件" / "內湖李"
        (lee / "私人").mkdir()
        (lee / "私人" / "日記.txt").write_text("秘密", encoding="utf-8")
        p = Path(self.V) / ".system/sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sources"][0]["excludes"].append({"spec": "內湖李/私人", "kind": "hidden", "reason": "", "added": "2026-10-22"})
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        self.move(str(lee), str(self.raw / "報價與合約"))
        code, out = revert(self.V)
        self.assertEqual(code, 0, out)
        self.assertTrue((lee / "私人" / "日記.txt").is_file())
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), json.loads(before))
        self.assertEqual(entry(self.V, self.fid)["locations"][0]["uri"], "file://" + ESTIMATE)

    def test_ai_output_move_back(self):
        t0 = turn.start(self.V, None, "")["turn"]
        turn.backup(self.V, "AI 產出/報告.docx", t0)
        (Path(self.V) / "AI 產出" / "報告.docx").write_bytes(b"r")
        from kb._lib import inventory
        e = inventory.register_output(self.V, "AI 產出/報告.docx", t0)
        dest = Path(self.V) / "案子" / "內湖李"
        dest.mkdir(parents=True, exist_ok=True)
        self.t = turn.start(self.V, None, "")["turn"]
        self.move(str(Path(self.V) / "AI 產出" / "報告.docx"), str(dest))
        code, out = revert(self.V)
        self.assertEqual(code, 0, out)
        self.assertTrue((Path(self.V) / "AI 產出" / "報告.docx").is_file())
        self.assertEqual(entry(self.V, e["id"])["locations"][0]["uri"], "file://AI 產出/報告.docx")

    def test_same_name_at_origin_refuses_whole(self):
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))
        (self.raw / ESTIMATE).write_bytes(b"new one")
        before = self.snapshot()
        code, out = revert(self.V)
        self.assertEqual(code, 3)
        self.assertEqual(out.strip(), "退不回去：「內湖李」裡已經有「估價單.pdf」了，什麼都沒動")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((Path(self.V) / ".system/versions" / self.t / "reverted.json").exists())

    def tamper(self, uri):
        path = Path(self.V) / ".system/versions" / self.t / "moves.jsonl"
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        row["from"] = uri
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_tampered_records_refused(self):
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))
        excluded = self.raw / "公司行政" / "舊檔"
        p = Path(self.V) / ".system/sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sources"][2]["excludes"].append({"spec": "舊檔", "kind": "unused", "reason": "", "added": "2026-10-22"})
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cases = {
            "file://客戶案件/C:/Windows/x.pdf": "退不回去：回合紀錄的搬動路徑不對（file://客戶案件/C:/Windows/x.pdf），什麼都沒動",
            "file://客戶案件/../../x.pdf": "退不回去：回合紀錄的搬動路徑不對（file://客戶案件/../../x.pdf），什麼都沒動",
            "file://沒有這個/x.pdf": "退不回去：「沒有這個」現在沒有登記，什麼都沒動",
            "file://公司行政/舊檔/x.pdf": "退不回去：「x.pdf」在設成不讀的夾裡，什麼都沒動",
            "file://知識庫/.system/x.pdf": "退不回去：回合紀錄的搬動路徑不對（file://知識庫/.system/x.pdf），什麼都沒動",
            "/abs/x.pdf": "退不回去：回合紀錄的搬動路徑不對（/abs/x.pdf），什麼都沒動",
            "file://客戶案件/（已排除）": "退不回去：那一回合搬過的檔在清掉的不讀夾裡，什麼都沒動",
            # security-review：段中間的磁碟代號會變成另一顆磁碟的相對路徑（D:Secret/x），不能被當成在根底下
            "file://知識庫/a/D:/Secret/x.pdf": "退不回去：回合紀錄的搬動路徑不對（file://知識庫/a/D:/Secret/x.pdf），什麼都沒動",
            "file://客戶案件/a/D:/Secret/x.pdf": "退不回去：回合紀錄的搬動路徑不對（file://客戶案件/a/D:/Secret/x.pdf），什麼都沒動",
            "file://知識庫/系統./x/y.pdf": "退不回去：回合紀錄的搬動路徑不對（file://知識庫/系統./x/y.pdf），什麼都沒動",
        }
        self.assertTrue(excluded.is_dir())
        here = os.getcwd()
        os.chdir(self.V)  # 真的 kb 從知識庫裡跑：磁碟相對路徑會接到 cwd（security-review 那條要在這個條件下才重現）
        self.addCleanup(os.chdir, here)
        for uri, text in cases.items():
            with self.subTest(uri=uri):
                self.tamper(uri)
                before = self.snapshot()
                code, out = revert(self.V)
                self.assertEqual(code, 3)
                self.assertEqual(out.strip(), text)
                self.assertEqual(self.snapshot(), before)

    def test_trash_revert_brings_card_and_inventory_back(self):
        with mock.patch.object(se, "recycle_room", return_value=(0, 10 ** 9)):
            p = se.plan_trash(self.V, self.sources, self.fid, self.V)
        with mock.patch.object(se, "shell_trash", side_effect=os.remove):
            se.do_trash(self.V, p, self.t)
        code, out = revert(self.V)
        self.assertEqual(code, 0, out)
        self.assertIn("估價單.pdf 在資源回收筒，要從那裡拿回來", out)
        self.assertTrue((Path(self.V) / self.card_rel).is_file())
        self.assertNotEqual(entry(self.V, self.fid)["status"], "deleted")

    def test_purged_row_skipped_with_line(self):
        t = turn.start(self.V, None, "")["turn"]
        turn.backup(self.V, self.card_rel, t, action="delete")
        os.remove(Path(self.V) / self.card_rel)
        m = Path(self.V) / ".system/versions" / t / "manifest.jsonl"
        row = json.loads(m.read_text(encoding="utf-8").splitlines()[0])
        row.update(action="purged", path="（已排除）")
        m.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        code, out = revert(self.V)
        self.assertEqual(code, 0, out)
        self.assertIn("那一版因為不讀的夾清掉了，退不回", out)
        self.assertFalse((Path(self.V) / self.card_rel).exists())


class ListMoves(Base):
    def test_list_shows_plain_lines_without_ids(self):
        self.move(self.fid, str(self.raw / "客戶案件" / "大安陳"))
        out = listing(self.V)
        self.assertIn("    估價單.pdf：從 內湖李 搬到 大安陳", out)
        self.assertIsNone(ID_LIKE.search(out), out)
        data = json.loads(self._json())
        self.assertEqual(data[0]["moves"][0]["from"], "file://" + ESTIMATE)

    def _json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            versions._list(self.V, 5, True)
        return out.getvalue()
