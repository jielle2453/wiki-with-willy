"""hooks/output_log.py 黑箱：PostToolUse 寫入 Z3 後登記成品；Z1 不登記；檔不存在不登記；壞 JSON exit 0。
檔案索引（Z5）的卡驗檔頭（D-104，ingest 製卡第 6 點、驗收 10）：不過印 decision block、exit 0；過了不印、重渲染總目錄。"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests import _cards
from tests._guard_fixture import ROOT, make_vault
from kb._lib import cards, inventory, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

HOOK = ROOT / "hooks" / "output_log.py"
GUARD = ROOT / "hooks" / "guard.py"
SID = "test-sid"


def run_hook(data, cwd=None, hook=HOOK):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(hook)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def write_via_tools(cwd, path, text, tool="Write"):
    """像 Claude Code 一樣：PreToolUse（守門備份）→ 真的寫 → PostToolUse（登記）。回 PostToolUse 的結果。"""
    pre = {"session_id": SID, "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": tool,
           "tool_input": {"file_path": str(path), "content": text}}
    r = run_hook(pre, hook=GUARD)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow", r.stdout
    Path(path).write_text(text, encoding="utf-8")
    return run_hook(post_payload(cwd, tool, path))


def post_payload(cwd, tool, path, key="file_path"):
    return {"session_id": SID, "cwd": str(cwd), "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": {key: str(path), "content": "x"}, "tool_response": {"filePath": str(path), "success": True}}


class OutputLog(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.t = turn.start(self.V, SID, "寫一份草稿")
        self.out = self.V / "AI 產出" / "x.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_to_z3_registers(self):
        r = write_via_tools(self.V, self.out, "草稿\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["origin"], "ai_output")
        self.assertEqual(entries[0]["created_turn"], self.t["turn"])
        self.assertEqual(entries[0]["locations"][0]["uri"], "file://AI 產出/x.md")
        self.assertEqual(entries[0]["locations"][0]["size"], self.out.stat().st_size)
        self.assertRegex(entries[0]["id"], r"^f-[0-9a-f]{8}$")
        self.assertEqual(entries[0]["read"], "ok")

    def test_same_content_again_does_not_add(self):
        write_via_tools(self.V, self.out, "草稿\n")
        run_hook(post_payload(self.V, "Edit", self.out))
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["fingerprints"]), 1)
        write_via_tools(self.V, self.out, "草稿 改\n", tool="Edit")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["fingerprints"]), 2)

    def test_relative_path_and_notebook_key(self):
        nb = self.V / "AI 產出" / "n.ipynb"
        turn.backup(self.V, "AI 產出/n.ipynb", self.t["turn"])
        nb.write_text("{}", encoding="utf-8")
        r = run_hook(post_payload(self.V, "NotebookEdit", "AI 產出/n.ipynb", key="notebook_path"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([l["uri"] for l in inventory.load(self.V)["entries"][0]["locations"]], ["file://AI 產出/n.ipynb"])

    def test_preexisting_unregistered_file_edit_is_not_registered(self):
        """第一輪 M4（驗收 23）：客戶自己放進 AI 產出的檔，AI 編輯不登記。"""
        q = self.V / "AI 產出" / "q.md"
        q.write_text("客戶自己放的\n", encoding="utf-8")
        r = write_via_tools(self.V, q, "AI 改過\n", tool="Edit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_reverted_output_modified_in_new_turn_gets_new_entry(self):
        """第一輪 M2（驗收 22）。"""
        write_via_tools(self.V, self.out, "草稿\n")
        old = inventory.load(self.V)["entries"][0]
        inventory.set_status(self.V, [old["id"]], "turn_reverted")
        t2 = turn.start(self.V, SID, "再寫")
        write_via_tools(self.V, self.out, "第二次\n", tool="Edit")
        entries = inventory.load(self.V)["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["status"], "superseded")  # 第二輪 R2-3：舊筆被新成品取代
        self.assertEqual(entries[1]["status"], "active")
        self.assertEqual(entries[1]["created_turn"], t2["turn"])
        self.assertNotEqual(entries[1]["id"], old["id"])

    def test_write_to_z1_not_registered(self):
        card = self.V / "案子" / "信義王" / "信義王.md"
        r = run_hook(post_payload(self.V, "Write", card))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_missing_file_not_registered(self):
        r = run_hook(post_payload(self.V, "Write", self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_non_write_tool_ignored(self):
        self.out.write_text("草稿\n", encoding="utf-8")
        r = run_hook(post_payload(self.V, "Read", self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.V / ".system" / "inventory.json").exists())

    def test_outside_vault_ignored(self):
        other = self.tmp / "AI 產出"
        other.mkdir()
        (other / "x.md").write_text("x", encoding="utf-8")
        r = run_hook(post_payload(self.tmp, "Write", other / "x.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / ".system").exists())

    def test_registered_in_hooks_json_as_post_tool_use(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["PostToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Write|Edit|MultiEdit|NotebookEdit")
        hook = groups[0]["hooks"][0]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/output_log.py", hook["command"])
        self.assertEqual(hook["timeout"], 10)

    def test_bad_json_silent_exit_0(self):
        r = run_hook("not json", cwd=self.V)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "output_log")  # 第一輪 L7：第二欄是哪支 hook
        r = run_hook("not json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class CardCheck(unittest.TestCase):
    """Z5：照 header 寫卡，各種錯各一次。"""

    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.t = turn.start(self.V, SID, "整理")
        fid = _cards.entry_of(self.V, _cards.MEETING)["id"]
        self.item = next(i for i in cards.todo(self.V, sources_lib.load(self.V))["new"] if i["id"] == fid)
        self.path = _cards.card_path(self.V, self.item, "大安陳會議一")
        self.text = _cards.card_text(self.item, "大安陳會議一")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        return write_via_tools(self.V, path, text)

    def blocked(self, r, want):
        self.assertEqual(r.returncode, 0, r.stderr)
        (line,) = r.stdout.splitlines()
        out = json.loads(line)
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("檔頭有錯："), out["reason"])
        self.assertTrue(out["reason"].endswith("。改好再寫一次。"), out["reason"])
        self.assertIn(want, out["reason"])

    def test_good_card_silent_and_index_rendered(self):
        r = self.write(self.text)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        index = (self.V / "總目錄.md").read_text(encoding="utf-8")
        self.assertIn("[大安陳會議一](檔案索引/客戶案件/大安陳/大安陳會議一-", index)
        self.assertIn("總目錄.md", {e["path"] for e in turn.entries(self.V, self.t["turn"])})

    def test_errors(self):
        front = cards.parse_front(self.text)
        fp = front["指紋"]
        bad_fp = self.text.replace(fp, ("0" if fp[0] != "0" else "1") + fp[1:]).replace(front["內容指紋"], "0" * 64)
        cases = [
            (bad_fp, None, "指紋不是現行的"),
            (self.text, self.path.with_name("大安陳會議一-00000000.md"), "檔名該是"),
            (self.text, self.V / "檔案索引" / "客戶案件" / "信義王" / self.path.name, "卡該放在"),
            (self.text.replace(front["開原檔"], front["開原檔"][:-1] + "y"), None, "開原檔那行"),
            (self.text.replace("標題: 大安陳會議一", "標題: （填）"), None, "標題"),
            (self.text.replace("---\n", "", 1), None, "檔頭解析不了"),
        ]
        for text, path, want in cases:
            with self.subTest(want=want):
                self.blocked(self.write(text, path), want)

    def test_list_value_blocked_not_silently_accepted(self):
        """測試後修正裁定 21（S4）：編號寫成清單時以前丟 TypeError 被吞掉、不擋；現在要 block。"""
        cid = self.item["id"]
        r = self.write(self.text.replace(f"編號: {cid}", f"編號: [{cid}]"))
        self.blocked(r, "檔頭「編號」的格式不對")
        self.assertFalse((self.V / ".system" / "hook-errors.log").exists())

    def test_edit_tool_checked_too(self):
        self.write(self.text)
        self.path.write_text(self.text.replace("出處: 客戶原檔", "出處: 對話收件"), encoding="utf-8")
        r = run_hook(post_payload(self.V, "Edit", self.path))
        self.blocked(r, "出處該是 客戶原檔")

    def test_non_md_in_index_ignored(self):
        p = self.V / "檔案索引" / "x.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        r = self.write("x", p)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_output_zone_still_registers(self):
        out = self.V / "AI 產出" / "草稿.md"
        r = write_via_tools(self.V, out, "草稿\n")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        uris = [l["uri"] for e in inventory.load(self.V)["entries"] for l in e["locations"]]
        self.assertIn("file://AI 產出/草稿.md", uris)


class SynthesisCheck(unittest.TestCase):
    """實體與綜合卡第 6 點、驗收 2、5、7：Z1 綜合卡驗檔頭，不過 block、過了 card true 與重渲染快取；
    Z5 檔案卡過了重渲染新舊 實體 的綜合卡快取；檔案卡的實體名稱錯了照樣 block。"""

    def setUp(self):
        from tests import _entities
        self.E = _entities
        self.tmp, self.V, self.raw = _entities.make_entity_vault(with_entities=True)
        self.t = turn.start(self.V, SID, "整理")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def update(self, eid, **kw):
        from kb._lib import entities
        rows = entities.load(self.V)["entities"]
        for e in rows:
            if e["id"] == eid:
                e.update(kw)
        self.E.put_entities(self.V, rows)

    def new_item(self):
        from kb._lib import entities, synthesis
        data = entities.load(self.V)
        out = synthesis.buckets(self.V, data, entities.mounts(self.V, data), entities._profile(self.V))["synthesis_new"]
        return next(i for i in out if i["entity_id"] == self.E.WANG)

    def write(self, rel, text):
        p = self.V / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return write_via_tools(self.V, p, text)

    def blocked(self, r, want):
        self.assertEqual(r.returncode, 0, r.stderr)
        (line,) = r.stdout.splitlines()
        self.assertEqual(json.loads(line), {"decision": "block", "reason": f"綜合卡有錯：{want}。改好再寫一次。"})

    def test_new_card_passes_sets_card_and_rewrites_cache(self):
        from kb._lib import entities
        self.update(self.E.WANG, source="confirmed")
        self.E.write_card(self.V, self.E.QUOTE, "王宅報價", ["王宅"])
        self.E.write_card(self.V, self.E.ESTIMATE, "王宅估價", ["王宅", "信義王"])
        item = self.new_item()
        lines = item["header"].splitlines()
        start = lines.index("檔案卡:")
        empty = "\n".join(lines[:start] + ["檔案卡:", "相關實體: []", "---"]) + "\n"
        r = self.write(item["path"], self.E.synthesis_text(empty, "王宅"))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertTrue(next(e for e in entities.load(self.V)["entities"] if e["id"] == self.E.WANG)["card"])
        front = cards.parse_front((self.V / item["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(front["檔案卡"]), 2)
        self.assertEqual(front["相關實體"], ["信義王"])

    def test_block_sentences(self):
        self.E.write_card(self.V, self.E.QUOTE, "王宅報價", ["王宅"])
        self.E.write_card(self.V, self.E.ESTIMATE, "王宅估價", ["王宅"])
        item = self.new_item()
        good = self.E.synthesis_text(item["header"], "王宅")
        rel = item["path"]
        self.blocked(self.write(rel, good), "還沒跟客戶確認名稱，先問再寫")
        self.update(self.E.WANG, source="confirmed")
        cases = [
            (rel, good.replace("名稱: 王宅", "名稱: 王"), "「名稱」照 kb cards todo 給的檔頭抄"),
            (rel, good.replace("範本版本: 1", "範本版本: 3"), "範本版本該是 1"),
            (rel, good.replace("更新日: ", "更新日: x"), "更新日要寫 YYYY-MM-DD"),
            (rel, good.replace("## 現況", "## 近況"), "正文要有 # 王宅 與 ## 現況"),
            (rel, good.replace(self.E.WANG, "e-99999999"), "編號 e-99999999 不在實體清單"),
            (rel, good.replace(self.E.WANG, self.E.SELF), "這是你自己（關於你），不寫綜合卡"),
            (rel, good.replace("---\n", "", 1), "檔頭解析不了：第一行不是 ---"),
            ("案子/大安陳/大安陳.md", good, "綜合卡該放在 案子/王宅/王宅.md"),
        ]
        for path, text, want in cases:
            with self.subTest(want=want):
                self.blocked(self.write(path, text), want)
        self.E.write_card(self.V, self.E.DRAFT, "王宅會議", ["王宅"])
        self.blocked(self.write(rel, good), "整理到不是現行的，重跑 kb cards todo 拿新檔頭")
        self.update(self.E.WANG, status="removed")
        self.blocked(self.write(rel, good), "這個實體已移除")

    def test_file_card_rerenders_old_and_new_entities(self):
        xinyi = self.V / "案子" / "信義王" / "信義王.md"
        item = self.E.new_item(self.V, self.E.QUOTE)
        path = _cards.card_path(self.V, item, "信義王報價")
        text = self.E.with_entities(_cards.card_text(item, "信義王報價"), ["信義王"])
        path.parent.mkdir(parents=True, exist_ok=True)
        r = write_via_tools(self.V, path, text)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(len(cards.parse_front(xinyi.read_text(encoding="utf-8"))["檔案卡"]), 1)
        rows = {e["path"]: e for e in turn.entries(self.V, self.t["turn"])}
        self.assertEqual(rows["案子/信義王/信義王.md"]["by"], "program")
        self.assertEqual(turn.modified_synthesis_count(self.V, self.t["turn"]), 0)
        turn.start(self.V, SID, "改掛")
        r = write_via_tools(self.V, path, self.E.with_entities(text, ["大安陳"]))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(cards.parse_front(xinyi.read_text(encoding="utf-8"))["檔案卡"], [], "舊的實體從回合備份讀")
        daan = cards.parse_front((self.V / "案子" / "大安陳" / "大安陳.md").read_text(encoding="utf-8"))
        self.assertEqual(len(daan["檔案卡"]), 1)

    def test_file_card_entity_errors_blocked(self):
        item = self.E.new_item(self.V, self.E.QUOTE)
        path = _cards.card_path(self.V, item, "信義王報價")
        path.parent.mkdir(parents=True, exist_ok=True)
        base = _cards.card_text(item, "信義王報價")
        cases = [
            ((["信義王宅"], ()), "實體「信義王宅」該寫登記的名稱「信義王」"),
            ((["順發"], ()), "實體「順發」還沒登記，先跑 kb entity add"),
            ((["信義王"], ["案子"]), "型別「案子」已在型別表，先 kb entity add 再掛"),
        ]
        for (names, pending), want in cases:
            with self.subTest(want=want):
                r = write_via_tools(self.V, path, self.E.with_entities(base, names, pending))
                (line,) = r.stdout.splitlines()
                self.assertEqual(json.loads(line)["reason"], f"檔頭有錯：{want}。改好再寫一次。")
        r = write_via_tools(self.V, path, self.E.with_entities(base, ["信義王"]))
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
