"""hooks/session.py 黑箱：subprocess 餵 SessionStart payload（D-087、D-060）。第一行 kb 指令前綴、core.md 全文接在空行後、
compact 才有摺疊那行、不在知識庫或壞 JSON 無輸出 exit 0、stdout 純 ASCII、2 秒內、hooks.json 註冊。
清冊題：變動行三種（無、有數字、incomplete）、找不到來源第一次印第二次不印、清冊壞掉其他行照印且 log 一行。"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from tests._guard_fixture import ROOT, make_vault

HOOK = ROOT / "hooks" / "session.py"
CORE = ROOT / "rules" / "core.md"
KB_LINE = f'【wiki-with-willy】kb 指令：python "{ROOT.as_posix()}/scripts/kb.py" <子命令>'
COMPACT_LINE = "對話剛摺疊，規則已重新帶入；做到一半的事以工作日誌為準。"
ERROR_LINE = "【wiki-with-willy】開場程式出錯（{}），詳見 .system/hook-errors.log；這個對話不改任何檔。"


def run(data, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(cwd or ROOT), env=env)


def payload(cwd, source="startup"):
    data = {"session_id": "abc", "transcript_path": "x.jsonl", "cwd": str(cwd), "hook_event_name": "SessionStart"}
    if source is not None:
        data["source"] = source
    return data


class SessionHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def context(self, source="startup"):
        r = run(payload(self.V, source))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return out["hookSpecificOutput"]["additionalContext"]

    def test_startup_first_line_is_kb_prefix_and_path_exists(self):
        ctx = self.context("startup")
        self.assertEqual(ctx.splitlines()[0], KB_LINE)
        self.assertTrue((ROOT / "scripts" / "kb.py").is_file())

    def test_core_full_text_after_blank_line(self):
        core = CORE.read_text(encoding="utf-8")
        ctx = self.context("startup")
        self.assertTrue(ctx.endswith("\n\n" + core), ctx[:200])
        status = ctx[:-len("\n\n" + core)].split("\n")  # 清冊審閱裁定 10：kb 行與 core 之間允許多狀態行
        self.assertEqual(status[0], KB_LINE)
        self.assertTrue(all(line for line in status), status)

    def test_compact_adds_second_line(self):
        status = self.context("compact").split("\n\n", 1)[0].split("\n")
        self.assertEqual((status[0], status[-1]), (KB_LINE, COMPACT_LINE))

    def test_other_sources_have_no_compact_line(self):
        for source in ("startup", "resume", "clear", None):
            with self.subTest(source=source):
                self.assertNotIn(COMPACT_LINE, self.context(source))

    def test_outside_vault_silent_exit_0(self):
        r = run(payload(ROOT))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def test_bad_json_silent_exit_0_and_logged(self):
        r = run("not json", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[-1].split("\t")[1], "session")

    def test_error_after_vault_found_prints_error_line_and_logs(self):
        """第一輪 D2：找到知識庫之後出錯，除了寫 log，還要印一行出錯的 additionalContext（失敗明講）。
        複製 hooks/session.py、scripts/ 與 manifest 到沒有 rules/ 的暫存夾，讀 core.md 會 FileNotFoundError。"""
        plugin = self.tmp / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        shutil.copy2(HOOK, plugin / "hooks" / "session.py")
        shutil.copytree(ROOT / "scripts", plugin / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / ".claude-plugin", plugin / ".claude-plugin")
        shutil.copytree(ROOT / "config", plugin / "config")  # 清冊題：變動行要讀設定，缺它會多一行 log
        raw = json.dumps(payload(self.V), ensure_ascii=False)
        r = subprocess.run([sys.executable, str(plugin / "hooks" / "session.py")], input=raw, capture_output=True,
                           text=True, encoding="utf-8", cwd=str(self.V), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(plugin)))
        self.assertEqual(r.returncode, 0, r.stderr)
        r.stdout.encode("ascii")
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertEqual(out["additionalContext"], ERROR_LINE.format("FileNotFoundError"))
        lines = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "session")

    def test_broken_stdout_pipe_still_exit_0(self):
        """第一輪低項：stdout 讀端先關掉（pipe 斷），write／flush 失敗也要 exit 0、stderr 乾淨（結束時的補 flush 不能再炸）。"""
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        p = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=str(self.V), env=env)
        p.stdout.close()
        p.stdin.write(json.dumps(payload(self.V)).encode("utf-8"))
        p.stdin.close()
        err = p.stderr.read()
        p.stderr.close()
        self.assertEqual(p.wait(timeout=10), 0, err)
        self.assertEqual(err, b"")

    def test_stdout_is_pure_ascii(self):
        r = run(payload(self.V, "compact"))
        self.assertTrue(r.stdout)
        r.stdout.encode("ascii")

    def test_single_run_within_2_seconds(self):
        start = time.monotonic()
        r = run(payload(self.V))
        self.assertEqual(r.returncode, 0)
        self.assertLess(time.monotonic() - start, 2.0)

    def test_registered_in_hooks_json_as_session_start(self):
        cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        groups = cfg["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "startup|resume|clear|compact")
        hooks = groups[0]["hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["command"], 'python "${CLAUDE_PLUGIN_ROOT}/hooks/session.py"')
        self.assertEqual(hooks[0]["timeout"], 10)
        self.assertTrue(cfg["description"].startswith("開場帶入核心規則與 kb 指令前綴；"))
        self.assertEqual(len(cfg["hooks"]), 5)


KB = ROOT / "scripts" / "kb.py"
CHANGES_INCOMPLETE = "變動：來源很大還沒比完，說「有什麼變動」我再比"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def big_xlsx(path, rows, first=1):
    """一張工作表、5 欄數字；first 改 A1 的值（「改一格」）。900k 列約 27 MB，不設時間上限抽字要 14 秒（測試報告發現一的量級）。"""
    import zipfile
    parts = [f'<?xml version="1.0"?><worksheet xmlns="{SHEET_NS}"><sheetData>']
    for r in range(1, rows + 1):
        a = first if r == 1 else r
        parts.append(f'<row r="{r}"><c r="A{r}"><v>{a}</v></c><c r="B{r}"><v>{r * 2}</v></c><c r="C{r}"><v>{r * 3}</v></c>'
                     f'<c r="D{r}"><v>{r * 4}</v></c><c r="E{r}"><v>{r * 5}</v></c></row>')
    parts.append("</sheetData></worksheet>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "".join(parts))


class OpeningBigOoxml(unittest.TestCase):
    """審閱裁定 34、測試報告發現一：11 MB 級的 xlsx 改一格後，開場 hook 總時間在 10 秒（SessionStart 逾時）內。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_big_xlsx_one_cell_changed_opening_within_10_seconds(self):
        from kb._lib import inventory
        from kb._lib import sources as sources_lib

        p = self.raw / "客戶案件" / "大表.xlsx"
        big_xlsx(p, 900_000)
        self.assertGreater(p.stat().st_size, 10_000_000)
        real = inventory.content_fp.text_fingerprint
        inventory.content_fp.text_fingerprint = lambda path, **kw: None  # 入冊這步不是要量的，跳過抽字省時間
        try:
            report = inventory.scan(self.V, sources_lib.load(self.V))
        finally:
            inventory.content_fp.text_fingerprint = real
        from kb._lib import turn
        inventory.apply(self.V, report, turn.start(self.V, None, "")["turn"])
        big_xlsx(p, 900_000, first=999)
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + 100))
        start = time.monotonic()
        r = run(payload(self.V))
        elapsed = time.monotonic() - start
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(elapsed, 10.0, f"開場 hook 花了 {elapsed:.1f} 秒")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        status = ctx.split("\n\n", 1)[0].split("\n")
        self.assertEqual(status[0], KB_LINE)
        self.assertTrue(set(status) & {CHANGES_INCOMPLETE, "變動：新增 0、修改 1、不見 0"}, status)
        self.assertTrue(ctx.endswith(CORE.read_text(encoding="utf-8")))


class OpeningInventory(unittest.TestCase):
    """清冊題第 7 點：line_changes、line_missing_sources 共用一次快速 scan；每行各自 try（I-046）。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        # fixture 的 r.docx 與 合約.docx 都是 0 位元組，同一份內容會併成一筆；給它自己的內容才測得到「不見」
        (self.raw / "行政" / "請款" / "r.docx").write_bytes(b"PK fake request")
        r = subprocess.run([sys.executable, str(KB), "inventory", "scan"], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(self.V), env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, source="startup"):
        r = run(payload(self.V, source))
        self.assertEqual(r.returncode, 0, r.stderr)
        r.stdout.encode("ascii")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        return ctx.split("\n\n", 1)[0].split("\n")

    def change_lines(self, lines):
        return [ln for ln in lines if ln.startswith("變動：")]

    def test_no_change_no_line(self):
        self.assertEqual(self.change_lines(self.status()), [])

    def test_counts_line(self):
        (self.raw / "客戶案件" / "新.txt").write_text("新\n", encoding="utf-8")
        (self.raw / "客戶案件" / "a.txt").write_text("改過了，長度不一樣\n", encoding="utf-8")
        (self.raw / "行政" / "請款" / "r.docx").unlink()
        self.assertEqual(self.change_lines(self.status()), ["變動：新增 1、修改 1、不見 1"])

    def test_moves_and_duplicates_are_not_counted(self):
        shutil.copy2(self.raw / "客戶案件" / "a.txt", self.raw / "客戶案件" / "a 副本.txt")
        (self.raw / "行政" / "請款" / "r.docx").rename(self.raw / "行政" / "r.docx")
        self.assertEqual(self.change_lines(self.status()), [])

    def test_never_scanned_vault_counts_everything_as_new(self):
        """審閱裁定 10：沒有清冊等於全部新增。"""
        (self.V / ".system" / "inventory.json").unlink()
        lines = self.change_lines(self.status())
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^變動：新增 [1-9]\d*、修改 0、不見 0$")

    def test_incomplete_line(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"changes_quick_seconds": 0}), encoding="utf-8")
        self.assertEqual(self.change_lines(self.status()), [CHANGES_INCOMPLETE])

    def test_missing_source_reported_once_then_cleared(self):
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        line = "這台電腦找不到來源「客戶案件」，其他照常；搬過的話跟我說。"
        self.assertIn(line, self.status())
        env_json = json.loads((self.V / ".system" / "env.json").read_text(encoding="utf-8"))
        self.assertEqual([list(m["missing_sources_reported"]) for m in env_json["machines"].values()], [["src-3f9a"]])
        lines = self.status()
        self.assertNotIn(line, lines)
        self.assertEqual(self.change_lines(lines), [], "根找不到的來源底下不算不見")
        (self.raw / "客戶案件_舊").rename(self.raw / "客戶案件")
        self.assertNotIn(line, self.status())
        env_json = json.loads((self.V / ".system" / "env.json").read_text(encoding="utf-8"))
        self.assertEqual([m["missing_sources_reported"] for m in env_json["machines"].values()], [{}])
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        self.assertIn(line, self.status(), "又失蹤再報一次")

    def test_two_missing_sources_joined(self):
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        (self.raw / "行政").rename(self.raw / "行政_舊")
        self.assertIn("這台電腦找不到來源「客戶案件」、「行政」，其他照常；搬過的話跟我說。", self.status())

    def test_many_missing_sources_list_three_and_count(self):
        """審閱裁定 32：最多列 3 個別名，再多寫「等 N 個」；整段仍在 400 字內、全部都記成報過。"""
        p = self.V / ".system" / "sources.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for i in range(5):
            data["sources"].append({"id": f"src-a{i:03d}", "alias": f"很長很長的外接硬碟資料夾第{i}號", "spec": f"../沒有/{i}",
                                    "excludes": []})
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        lines = self.status("compact")
        line = [ln for ln in lines if "找不到來源" in ln]
        self.assertEqual(line, ["這台電腦找不到來源「很長很長的外接硬碟資料夾第0號」、「很長很長的外接硬碟資料夾第1號」、"
                                "「很長很長的外接硬碟資料夾第2號」等 5 個，其他照常；搬過的話跟我說。"])
        self.assertLessEqual(len("\n".join(lines)), 400)
        self.assertLessEqual(len(lines), 8)
        env_json = json.loads((self.V / ".system" / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(len(next(iter(env_json["machines"].values()))["missing_sources_reported"]), 5)

    def test_broken_inventory_other_lines_still_printed_and_one_log_line(self):
        (self.V / ".system" / "inventory.json").write_text("{bad", encoding="utf-8")
        lines = self.status("compact")
        self.assertEqual(lines[0], KB_LINE)
        self.assertEqual(lines[-1], COMPACT_LINE)
        self.assertEqual(self.change_lines(lines), [])
        self.assertFalse(any("找不到來源" in ln for ln in lines))
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].split("\t")[1], "session")

    def test_opening_writes_only_env_json(self):
        (self.raw / "客戶案件").rename(self.raw / "客戶案件_舊")
        (self.raw / "行政" / "請款" / "r.docx").unlink()

        def stamps():
            return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                    for p in (self.V / ".system").rglob("*") if p.is_file()}
        before = stamps()
        self.status()
        after = stamps()
        self.assertEqual({k for k in after if before.get(k) != after[k]}, {".system/env.json"})


INGEST_HINT = "有變動：先把客戶這句的答案完整說出來（不能只說「我先查」），同一則回答接著不用問、直接照 ingest skill 整理，末尾一句告知；只有超過 {n} 份才問。"  # 裁定 38、42


class OpeningIngestHint(unittest.TestCase):
    """ingest 製卡第 7 點、驗收 15：變動行印出來時緊接著一行指示；沒變動不印；n 讀 ingest_auto_max；整段守 8 行 400 字。"""

    setUp = OpeningInventory.setUp
    tearDown = OpeningInventory.tearDown
    status = OpeningInventory.status

    def hint_lines(self, lines):
        return [ln for ln in lines if ln.startswith("有變動：")]

    def test_no_change_no_hint(self):
        self.assertEqual(self.hint_lines(self.status()), [])

    def test_hint_right_after_change_line(self):
        (self.raw / "客戶案件" / "新.txt").write_text("新\n", encoding="utf-8")
        lines = self.status()
        i = lines.index("變動：新增 1、修改 0、不見 0")
        self.assertEqual(lines[i + 1], INGEST_HINT.format(n=5))
        self.assertEqual(len(self.hint_lines(lines)), 1)
        self.assertLessEqual(len(lines), 8)
        self.assertLessEqual(len("\n".join(lines)), 400)

    def test_hint_after_incomplete_line(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"changes_quick_seconds": 0}), encoding="utf-8")
        lines = self.status()
        self.assertEqual(lines[lines.index(CHANGES_INCOMPLETE) + 1], INGEST_HINT.format(n=5))

    def test_hint_reads_ingest_auto_max(self):
        (self.V / ".system" / "config.json").write_text(json.dumps({"ingest_auto_max": 3}), encoding="utf-8")
        (self.raw / "客戶案件" / "新.txt").write_text("新\n", encoding="utf-8")
        self.assertEqual(self.hint_lines(self.status()), [INGEST_HINT.format(n=3)])


class OpeningEntityDrift(unittest.TestCase):
    """實體與綜合卡第 7 點、驗收 13：實體夾有變接在變動行後、或自成一行，下一行是有變動指示；實體清單壞了變動行照印、log 一行；
    只寫 env.json。fixture 的 案子/信義王/信義王.md 換成 tests/fixtures/entities 的綜合卡，信義王 card true。"""

    setUp_inventory = OpeningInventory.setUp
    tearDown = OpeningInventory.tearDown
    status = OpeningInventory.status
    change_lines = OpeningInventory.change_lines

    def setUp(self):
        self.setUp_inventory()
        fix = ROOT / "tests" / "fixtures" / "entities"
        shutil.copy2(fix / "entities.json", self.V / ".system" / "entities.json")
        shutil.copy2(fix / "信義王.md", self.V / "案子" / "信義王" / "信義王.md")
        (self.V / "案子" / "大安陳").mkdir()
        shutil.copy2(fix / "大安陳.md", self.V / "案子" / "大安陳" / "大安陳.md")

    def stamps(self):
        return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                for p in (self.V / ".system").rglob("*") if p.is_file()}

    def test_no_drift_no_line(self):
        self.assertEqual(self.change_lines(self.status()), [])

    def test_drift_alone_then_hint(self):
        (self.V / "案子" / "信義王").rename(self.V / "案子" / "王宅")
        before = self.stamps()
        lines = self.status()
        i = lines.index("變動：實體夾有變 1")
        self.assertEqual(lines[i + 1], INGEST_HINT.format(n=5))
        after = self.stamps()
        self.assertLessEqual({k for k in after if before.get(k) != after[k]}, {".system/env.json"})

    def test_drift_appended_to_counts(self):
        (self.raw / "客戶案件" / "新.txt").write_text("新\n", encoding="utf-8")
        shutil.rmtree(self.V / "案子" / "大安陳")
        lines = self.status()
        i = lines.index("變動：新增 1、修改 0、不見 0、實體夾有變 1")
        self.assertEqual(lines[i + 1], INGEST_HINT.format(n=5))

    def test_broken_entities_change_line_still_printed(self):
        (self.raw / "客戶案件" / "新.txt").write_text("新\n", encoding="utf-8")
        (self.V / ".system" / "entities.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(self.change_lines(self.status()), ["變動：新增 1、修改 0、不見 0"])
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].split("\t")[1], "session")


ASK = "先 kb profile check 複述，客戶說好才 apply"
TYPE_ROW = "| 案子 | 事 |  | 推測 | 2026-10-19 |\n"


class OpeningProfile(unittest.TestCase):
    """客戶設定檔與型別第 7 點、驗收 10：沒改不印；有改一行；讀不懂一行；unlisted 接在同一行或自成一行；只讀不寫；
    profile.json 壞了其他行照印、log 一行。fixture 的型別表只有「案子」。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()
        self.md = self.V / "系統" / "客戶設定檔.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    status = OpeningInventory.status

    def profile_lines(self, lines=None):
        return [ln for ln in (lines if lines is not None else self.status()) if ln.startswith("設定檔")]

    def edit(self, old, new):
        text = self.md.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.md.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

    def test_unchanged_no_line(self):
        self.assertEqual(self.profile_lines(), [])

    def test_changed_line(self):
        self.edit(TYPE_ROW, TYPE_ROW + "| 工地 | 事 | x |\n")
        self.assertEqual(self.profile_lines(), [f"設定檔有改（型別 +1）：{ASK}"])

    def test_whitespace_only_change_no_line(self):
        self.edit(TYPE_ROW, TYPE_ROW + "\n")
        self.assertEqual(self.profile_lines(), [])

    def test_unreadable_line(self):
        self.edit(TYPE_ROW, TYPE_ROW + "| 工地 | 事 |\n")
        self.assertEqual(self.profile_lines(),
                         ["設定檔有改但讀不懂：第 1 節第 2 列欄數不對（要 3 欄或 5 欄）；改好下次再問"])

    def test_unreadable_line_has_no_unlisted_part(self):
        (self.V / "廠商").mkdir()
        self.edit(TYPE_ROW, TYPE_ROW + "| 工地 | 事 |\n")
        self.assertEqual(self.profile_lines(),
                         ["設定檔有改但讀不懂：第 1 節第 2 列欄數不對（要 3 欄或 5 欄）；改好下次再問"])

    def test_unlisted_alone_and_joined(self):
        (self.V / "廠商").mkdir()
        self.assertEqual(self.profile_lines(), ["設定檔：第一層夾「廠商」不在型別表（請客戶補進設定檔）"])
        self.edit(TYPE_ROW, TYPE_ROW + "| 工地 | 事 | x |\n")
        self.assertEqual(self.profile_lines(),
                         [f"設定檔有改（型別 +1）：{ASK}；第一層夾「廠商」不在型別表（請客戶補進設定檔）"])

    def test_long_unlisted_lists_two(self):
        for i in range(5):
            (self.V / f"很長很長很長很長的第一層資料夾名稱第{i}號").mkdir()
        self.edit(TYPE_ROW, TYPE_ROW + "| 工地 | 事 | x |\n")
        line = self.profile_lines()[0]
        self.assertLessEqual(len(line), 160)
        self.assertIn("「很長很長很長很長的第一層資料夾名稱第0號」、「很長很長很長很長的第一層資料夾名稱第1號」等 5 個", line)

    def test_reads_only(self):
        (self.V / "廠商").mkdir()

        def stamps():
            return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                    for p in (self.V / ".system").rglob("*") if p.is_file()}
        for change in ((TYPE_ROW, TYPE_ROW + "| 工地 | 事 | x |\n"), ("| 工地 | 事 | x |", "| 工地 | 事 |")):
            before = stamps()
            self.edit(*change)
            self.assertTrue(self.profile_lines())
            after = stamps()
            self.assertEqual({k for k in after if before.get(k) != after[k]} - {".system/env.json"}, set())
        self.assertEqual(self.md.read_text(encoding="utf-8").count("| 工地 | 事 |"), 1)

    def test_broken_profile_json_other_lines_printed(self):
        (self.V / ".system" / "profile.json").write_text("{bad", encoding="utf-8")
        lines = self.status("compact")
        self.assertEqual((lines[0], lines[-1]), (KB_LINE, COMPACT_LINE))
        self.assertEqual(self.profile_lines(lines), [])
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].split("\t")[1], "session")


if __name__ == "__main__":
    unittest.main()


class OpeningAgenda(unittest.TestCase):
    """日程題第 6 點、驗收 5：日程行三種（沒有、有逾期帶提示、只有接下來）、0 的項不印、agenda.json 壞了其他行照印、開場不寫檔。"""

    def setUp(self):
        self.tmp, self.V, self.raw = make_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, *specs):
        """specs：(kind, 距今天數, status)。"""
        from datetime import date, timedelta
        items = [{"id": f"a-{i:08x}", "kind": kind, "start": None, "date": (date.today() + timedelta(days=days)).isoformat(),
                  "ended": None, "time": None, "text": f"事{i}", "entities": [], "status": status, "source": "confirmed",
                  "origin": None, "lead_days": None, "created_turn": "t", "history": []}
                 for i, (kind, days, status) in enumerate(specs)]
        (self.V / ".system" / "agenda.json").write_text(
            json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")

    def status(self, source="startup"):
        r = run(payload(self.V, source))
        self.assertEqual(r.returncode, 0, r.stderr)
        r.stdout.encode("ascii")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        return ctx.split("\n\n", 1)[0].split("\n")

    def agenda_lines(self, lines):
        return [ln for ln in lines if ln.startswith("日程：")]

    def test_no_agenda_no_line(self):
        self.assertEqual(self.agenda_lines(self.status()), [])

    def test_overdue_line_has_the_hint(self):
        self.put(("deadline", -3, "open"))
        self.assertEqual(self.agenda_lines(self.status()), ["日程：逾期 1（回答末尾跑 kb asks 照著問）"])

    def test_all_three_numbers(self):
        self.put(("deadline", -3, "open"), ("promise", -1, "open"), ("meeting", 0, "open"), ("reminder", 2, "open"),
                 ("deadline", 5, "open"))
        self.assertEqual(self.agenda_lines(self.status()), ["日程：逾期 2（回答末尾跑 kb asks 照著問）、今天 1、7 天內 2"])

    def test_only_upcoming(self):
        self.put(("meeting", 2, "open"), ("reminder", 6, "open"), ("deadline", 30, "open"))
        self.assertEqual(self.agenda_lines(self.status()), ["日程：7 天內 2"])

    def test_zero_everywhere_prints_nothing(self):
        self.put(("deadline", -3, "done"), ("meeting", -3, "open"), ("deadline", 3, "cancelled"), ("period", 3, "open"))
        self.assertEqual(self.agenda_lines(self.status()), [])

    def test_window_reads_config(self):
        self.put(("meeting", 2, "open"), ("meeting", 5, "open"))
        (self.V / ".system" / "config.json").write_text(json.dumps({"agenda_window_days": 3}), encoding="utf-8")
        self.assertEqual(self.agenda_lines(self.status()), ["日程：3 天內 1"])

    def test_line_sits_before_compact_line(self):
        self.put(("meeting", 2, "open"))
        lines = self.status("compact")
        self.assertEqual((lines[-2], lines[-1]), ("日程：7 天內 1", COMPACT_LINE))

    def test_broken_agenda_other_lines_still_printed_and_one_log_line(self):
        (self.V / ".system" / "agenda.json").write_text("{bad", encoding="utf-8")
        lines = self.status("compact")
        self.assertEqual((lines[0], lines[-1]), (KB_LINE, COMPACT_LINE))
        self.assertEqual(self.agenda_lines(lines), [])
        log = (self.V / ".system" / "hook-errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].split("\t")[1], "session")
        self.assertIn(".system/agenda.json", log[0])

    def test_opening_writes_nothing_but_env_json(self):
        self.put(("deadline", -3, "open"), ("meeting", 2, "open"))

        def stamps():
            return {p.relative_to(self.V).as_posix(): p.stat().st_mtime_ns
                    for p in (self.V / ".system").rglob("*") if p.is_file()}
        before = stamps()
        self.status()
        after = stamps()
        self.assertEqual({k for k in after if before.get(k) != after[k]} - {".system/env.json"}, set())
        self.assertFalse((self.V / "系統" / "日程.md").exists())
