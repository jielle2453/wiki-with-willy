"""kb journal recent／render 黑箱：subprocess 跑 kb.py，cwd 在 vault。recent 空、n、程式段說法、還沒結束、--json；
answer 第 7 點（D-158，驗收 10、14）：每段帶你說、結果、做了、改了（照路徑分類），三種篩法（--changed、--find、--since）可疊加；
render 指定月與全部、手改被蓋回、--json。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests._guard_fixture import FIXTURES, KB, ROOT, make_vault
from kb._lib import journal  # noqa: E402


def run_kb(*args, cwd):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(KB), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd), env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy_fixture(self, month="2026-10"):
        dst = journal.jsonl_path(self.V, month)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "journal" / "2026-10.jsonl", dst)
        return dst


class Recent(Base):
    def test_empty(self):
        r = run_kb("journal", "recent", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "還沒有工作日誌"), r.stderr)

    def test_lines_newest_first_with_program_prefix_and_in_progress(self):
        journal.record_program(self.V, "建立知識庫：建了 9 項", [], "init")
        prog = journal.recent(self.V, 1)[0]
        journal.begin_turn(self.V, None, {"turn": prog["turn"][:15] + "-test", "session_id": "s",
                                   "started": prog["time"], "prompt_head": "改一下"})
        r = run_kb("journal", "recent", cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = prog["time"].replace("T", " ")[:16]
        self.assertEqual(r.stdout.splitlines(), [f"{t}｜你說：改一下", "　結果：（還沒結束）",
                                                 f"{t}｜你在終端機跑的：kb init", "　做了：建立知識庫：建了 9 項"])

    def test_n_limits_and_json_length(self):
        for i in range(5):
            journal.record_program(self.V, f"第 {i} 段", [], "folders add")
        r = run_kb("journal", "recent", "--n", "3", cwd=self.V)
        self.assertEqual(len(r.stdout.splitlines()), 6)
        self.assertEqual(r.stdout.splitlines()[1], "　做了：第 4 段")
        data = json.loads(run_kb("journal", "recent", "--n", "3", "--json", cwd=self.V).stdout)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["note"], "第 4 段")

    def test_outside_vault_exits_2(self):
        self.assertEqual(run_kb("journal", "recent", cwd=self.tmp).returncode, 2)

    def test_acceptance_34_n_must_be_positive(self):
        """測試後修正 L4：--n 0、-1 是 argparse 錯誤，exit 2。"""
        for n in ("0", "-1"):
            r = run_kb("journal", "recent", "--n", n, cwd=self.V)
            self.assertEqual(r.returncode, 2, n)
            self.assertIn("--n", r.stderr)


def turn_row(turn_id, time, prompt, result="好", note="做了一件事", changed=(), done=True):
    return {"schema_version": 1, "kind": "turn", "turn": turn_id, "session_id": "s", "time": time, "prompt": prompt,
            "done": done, "ended": time, "result": result, "result_truncated": False, "interrupted": False, "note": note,
            "note_by": "ai", "changed": [{"path": p, "action": "modify"} for p in changed]}


class RecentFilters(Base):
    """answer 測試後修正裁定 12：對話裡跑 --find，turn hook 剛開的這一句自己的段不算。
    D-158：預設全列（含沒改動的）；--changed、--find（問、結果、做了，NFKC casefold）、--since 可疊加；改了照路徑分類。"""

    def setUp(self):
        super().setUp()
        rows = [
            turn_row("20261018-090000-aaaa", "2026-10-18T09:00:00", "王董的電話幾號？", result="王先生的電話在合約第 2 頁", note="沒有改動"),
            turn_row("20261019-100000-aaaa", "2026-10-19T10:00:00", "水電改到 11/2", result="好，水電預計完成改成 11/2",
                     note="水電預計完成從 10/25 改到 11/2", changed=[".system/agenda.json", "系統/日程.md"]),
            turn_row("20261020-220100-aaaa", "2026-10-20T22:01:00", "退回上一版",
                     result="已經退回上一次整理，信義王進度表的卡與王宅的綜合卡都回到之前的樣子了，你可以再看看",
                     note="退回上一次整理（信義王進度表卡、王宅綜合卡）",
                     changed=["案子/王宅/王宅.md", "檔案索引/客戶案件/信義王/進度表-1a2b3c4d.md", ".system/agenda.json",
                              "系統/日程.md", ".system/inventory.json"]),
            turn_row("20261021-080000-aaaa", "2026-10-21T08:00:00", "ＡＢＣ公司的報價", result="沒有找到", note="沒有改動"),
        ]
        p = journal.jsonl_path(self.V, "2026-10")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def recent(self, *args):
        r = run_kb("journal", "recent", "--n", "10", *args, cwd=self.V)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def heads(self, lines):
        return [ln for ln in lines if not ln.startswith("　")]

    def test_default_lists_everything_with_format(self):
        lines = self.recent()
        self.assertEqual(len(self.heads(lines)), 4)
        i = lines.index("2026-10-20 22:01｜你說：退回上一版")
        result = "已經退回上一次整理，信義王進度表的卡與王宅的綜合卡都回到之前的樣子了，你可以再看看"
        self.assertEqual(lines[i + 1], f"　結果：{result[:30]}…｜做了：退回上一次整理（信義王進度表卡、王宅綜合卡）")
        self.assertEqual(lines[i + 2], "　改了：綜合卡 王宅；檔案卡 1 張；日程；清冊")
        j = lines.index("2026-10-18 09:00｜你說：王董的電話幾號？")
        self.assertEqual(lines[j + 1], "　結果：王先生的電話在合約第 2 頁｜做了：沒有改動")
        self.assertTrue(j + 2 == len(lines) or not lines[j + 2].startswith("　改了"))

    def test_changed_since_find_and_stacking(self):
        self.assertEqual(self.heads(self.recent("--changed")),
                         ["2026-10-20 22:01｜你說：退回上一版", "2026-10-19 10:00｜你說：水電改到 11/2"])
        self.assertEqual(self.heads(self.recent("--changed", "--since", "2026-10-20")), ["2026-10-20 22:01｜你說：退回上一版"])
        self.assertEqual(self.heads(self.recent("--find", "王董")), ["2026-10-18 09:00｜你說：王董的電話幾號？"])
        self.assertEqual(self.heads(self.recent("--find", "王宅綜合卡")), ["2026-10-20 22:01｜你說：退回上一版"])
        self.assertEqual(self.heads(self.recent("--find", "abc")), ["2026-10-21 08:00｜你說：ＡＢＣ公司的報價"])
        self.assertEqual(self.recent("--find", "11/2", "--since", "2026-10-20"), ["沒有符合的段"])
        self.assertEqual(self.recent("--find", "沒這個詞"), ["沒有符合的段"])
        data = json.loads(run_kb("journal", "recent", "--changed", "--json", cwd=self.V).stdout)
        self.assertEqual([d["turn"] for d in data], ["20261020-220100-aaaa", "20261019-100000-aaaa"])

    def test_own_open_turn_skipped(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from kb._lib import turn
        t = turn.start(self.V, "s", "上次我問王董你怎麼說？")
        journal.begin_turn(self.V, None, t)
        heads = self.heads(self.recent("--find", "王董"))
        self.assertEqual(heads, ["2026-10-18 09:00｜你說：王董的電話幾號？"])
        self.assertEqual(len(self.heads(self.recent())), 5, "不帶 --find 時照列")

    def test_bad_since_exit_2(self):
        r = run_kb("journal", "recent", "--since", "2026-02-30", cwd=self.V)
        self.assertEqual(r.returncode, 2)

    def test_program_row_changed_line(self):
        journal.record_program(self.V, "記下推算：合計 182 萬", [{"path": ".system/findings.json", "action": "modify"}],
                               "finding add")
        lines = self.recent("--n", "1")
        self.assertTrue(lines[0].endswith("｜你在終端機跑的：kb finding add"), lines)
        self.assertEqual(lines[1:], ["　做了：記下推算：合計 182 萬", "　改了：推算"])


class ChangedText(unittest.TestCase):
    def test_classification(self):
        rows = [{"path": p, "action": "modify"} for p in (
            "案子/信義王/信義王.md", "業主/王先生/王先生.md", "案子/大安陳/大安陳.md", "案子/內湖李/內湖李.md",
            "檔案索引/a/b-1.md", "檔案索引/a/c-2.md", "AI 產出/報價.docx", ".system/statements.json",
            ".system/rulings.json", "系統/裁示.md", ".system/x.json", "系統/y.md", ".system/z.json", ".system/w.json",
            "收件匣/貼文.txt", "案子/信義王/合約.docx")]
        self.assertEqual(journal.changed_text(rows),
                         "綜合卡 信義王、王先生、大安陳等 4 張；檔案卡 2 張；AI 產出 1 份；口述；裁示；x、y、z等 4 個；其他 2 個檔")
        self.assertEqual(journal.changed_text([]), "")


class Render(Base):
    def test_render_month_and_all(self):
        self.copy_fixture("2026-10")
        self.copy_fixture("2026-09")
        r = run_kb("journal", "render", "2026-10", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已重寫 1 個月"), r.stderr)
        self.assertEqual(journal.md_path(self.V, "2026-10").read_bytes(), (FIXTURES / "journal" / "2026-10.md").read_bytes())
        self.assertFalse(journal.md_path(self.V, "2026-09").exists())
        r = run_kb("journal", "render", cwd=self.V)
        self.assertEqual(r.stdout.strip(), "已重寫 2 個月")
        self.assertTrue(journal.md_path(self.V, "2026-09").exists())

    def test_hand_edit_is_overwritten_and_rerender_is_stable(self):
        self.copy_fixture()
        run_kb("journal", "render", cwd=self.V)
        md = journal.md_path(self.V, "2026-10")
        before = md.read_bytes()
        run_kb("journal", "render", cwd=self.V)
        self.assertEqual(md.read_bytes(), before)
        md.write_text(md.read_text(encoding="utf-8").replace("沒有改動", "手改"), encoding="utf-8")
        run_kb("journal", "render", cwd=self.V)
        self.assertEqual(md.read_bytes(), before)

    def test_json(self):
        self.copy_fixture()
        r = run_kb("journal", "render", "--json", cwd=self.V)
        self.assertEqual(json.loads(r.stdout), {"months": ["2026-10"]})

    def test_month_without_jsonl(self):
        r = run_kb("journal", "render", "2026-01", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已重寫 0 個月"))
        self.assertFalse(journal.md_path(self.V, "2026-01").exists())

    def test_bad_month_exit_2(self):
        r = run_kb("journal", "render", "十月", cwd=self.V)
        self.assertEqual(r.returncode, 2)



if __name__ == "__main__":
    unittest.main()
