"""kb build 九個子子命令黑箱（build 第 12 點；驗收 1～3、5～8、10～12）：真的跑 CLI，成功與拒絕句一字不差。
env 的每一項換替身（沒信任、智慧型應用程式控制三種值、PDF 子行程被擋、沒有 Office、磁碟 500 MB、舊 llm-wiki 開著），
在行程裡跑；其他都照終端機跑。另有 profile apply --from、entity accept、cards dispatch --ids／--adopt 的黑箱。"""
import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import _cards
from tests import _entities as E
from tests._guard_fixture import FIXTURES
from tests.test_scripts_inventory_cli import run_kb
from kb import build as build_cli  # noqa: E402
from kb._lib import build, entities, turn  # noqa: E402

SID = "sid-build"


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kbbuildcli-")).resolve()
        self.D = self.tmp / "新知識庫"
        self.D.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kb(self, *args, cwd=None):
        return run_kb(*args, cwd=cwd or self.D)

    def ok(self, *args, cwd=None):
        r = self.kb(*args, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def refused(self, *args, cwd=None):
        r = self.kb(*args, cwd=cwd)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        return r.stdout.strip()

    def data(self):
        return json.loads((self.D / build.BUILD_POSIX).read_text(encoding="utf-8"))

    def start(self, sid=SID):
        self.assertEqual(self.ok("build", "start"), "建置：環境 做完了，下一步 訪談")
        turn.start(self.D, sid, "/wiki-with-willy:build")


class StatusStart(Base):
    def test_empty_then_not_empty(self):
        got = json.loads(self.ok("build", "status", "--json"))
        self.assertEqual((got["step"], got["not_empty"], got["after"]), ("env", False, "kb build start"))
        (self.D / "放錯的.txt").write_text("x", encoding="utf-8")
        self.assertTrue(json.loads(self.ok("build", "status", "--json"))["not_empty"])

    def test_existing_vault_refused(self):
        V = self.tmp / "vault"
        shutil.copytree(FIXTURES / "inventory" / "vault", V)
        self.assertEqual(self.refused("build", "status", "--json", cwd=V),
                         "這裡已經是建好的知識庫，要加資料夾、整理檔案直接跟我說")
        self.assertEqual(self.refused("build", "start", cwd=V), build.ALREADY)

    def test_start_builds_skeleton_like_init(self):
        self.start()
        for rel in (".system/sources.json", ".system/profile.json", "系統/客戶設定檔.md", "系統/日程.md", "CLAUDE.md",
                    "收件匣", "AI 產出", "檔案索引"):
            self.assertTrue((self.D / rel).exists(), rel)
        got = self.data()
        self.assertEqual((got["step"], [i["key"] for i in got["env"]["items"]]), ("setup", list(build.ENV_KEYS)))
        got = json.loads(self.ok("build", "status", "--json"))
        self.assertEqual((got["step"], got["after"], got["read"]), ("setup", "kb build done setup", "references/steps.md 訪談"))

    def test_second_session_busy(self):
        self.start("sid-a")
        self.ok("build", "status", "--json")
        turn.start(self.D, "sid-b", "/wiki-with-willy:build")
        self.assertEqual(self.refused("build", "status", "--json"), "另一個建置正在跑（訪談），等它做完或關掉那個對話再打一次")

    def test_done_wrong_step_and_redo(self):
        self.start()
        self.assertEqual(self.refused("build", "done", "sample"), "現在是「訪談」這一步，不是「抽樣」")
        self.assertEqual(self.ok("build", "done", "setup"), "建置：訪談 做完了，下一步 交付報告")  # 沒登記資料夾（D-044）
        self.assertEqual(self.ok("build", "redo", "setup"), "建置：回到「訪談」")
        self.assertEqual(self.refused("build", "redo", "finish"), "還沒做到「交付報告」，不能重做")
        self.assertEqual(self.refused("build", "redo", "sample"), "這次建置沒有「抽樣」這一步")

    def test_not_started(self):
        self.assertEqual(self.refused("build", "done", "env"), "還沒開始建置，先跑 kb build start")

    def test_set_people(self):
        self.start()
        self.assertEqual(self.ok("build", "set", "people", "我跟助理兩個人"), "記下了：幾個人用：我跟助理兩個人")
        self.assertEqual(self.data()["people"], "我跟助理兩個人")


class Env(Base):
    """env 八項：替身在行程裡換掉外部的東西；只讀。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        self.saved = {k: getattr(build, k) for k in ("_claude_home", "_sac_state", "_pdfium_installed", "_pdf_probe",
                                                     "_office_env", "_disk_free")}
        build._claude_home = lambda: self.home
        build._sac_state = lambda: 0
        build._pdfium_installed = lambda: True
        build._pdf_probe = lambda: True
        build._office_env = lambda: {"office": {"word": "W", "excel": "E", "powerpoint": None}, "soffice": None,
                                     "node": None, "npm": None, "node_packages": False, "py_missing": [],
                                     "pdfium": True}
        build._disk_free = lambda p: 120 * 1024 ** 3
        self.trust(self.tmp)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(build, k, v)
        super().tearDown()

    def trust(self, path, accepted=True):
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {Path(path).as_posix(): {"hasTrustDialogAccepted": accepted}}}), encoding="utf-8")

    def plugins(self, enabled):
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"llm-wiki@llm-wiki": [{"scope": "user"}]}}), encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"enabledPlugins": {"llm-wiki@llm-wiki": enabled}}), encoding="utf-8")

    def env(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(build_cli._env(self.D, True), 0)
        return {i["key"]: (i["level"], i["text"]) for i in json.loads(buf.getvalue())["items"]}

    def test_all_ok_and_read_only(self):
        before = tree_hashes(self.tmp)
        got = self.env()
        self.assertEqual(list(got), list(build.ENV_KEYS))
        self.assertEqual(got["trust"], ("ok", "這個資料夾按過信任"))
        self.assertEqual(got["sac"], ("ok", "智慧型應用程式控制關著"))
        self.assertEqual(got["pdf"], ("ok", "PDF 元件可以用"))
        self.assertEqual(got["office"], ("ok", "有 Office（Word、Excel）"))
        self.assertEqual(got["disk"], ("ok", "剩 120 GB"))
        self.assertEqual(got["old_wiki"], ("ok", "沒有舊的 llm-wiki"))
        self.assertEqual(got["python"][0], "ok")
        self.assertEqual(tree_hashes(self.tmp), before)

    def test_trust(self):
        self.trust(self.tmp / "別的夾")
        self.assertEqual(self.env()["trust"], ("fail", "這個資料夾還沒按過信任：在這個資料夾開對話、跳出「是否信任」時按信任，"
                                                       "不然每一步都會被問"))
        (self.home / ".claude.json").unlink()
        self.assertEqual(self.env()["trust"], ("warn", "看不出這個資料夾有沒有按過信任"))

    def test_smart_app_control_three_values(self):
        for state, want in ((0, ("ok", "智慧型應用程式控制關著")),
                            (1, ("warn", "智慧型應用程式控制開著；目前沒擋到我們的程式")),
                            (2, ("warn", "智慧型應用程式控制在評估中（之後可能自己打開）；目前沒擋到我們的程式")),
                            (None, ("ok", "這台沒有智慧型應用程式控制"))):
            build._sac_state = lambda s=state: s
            self.assertEqual(self.env()["sac"], want, state)

    def test_pdf_blocked(self):
        build._sac_state = lambda: 1
        build._pdf_probe = lambda: False
        got = self.env()
        self.assertEqual(got["pdf"], ("fail", "PDF 元件被擋，PDF 會讀不到"))
        self.assertEqual(got["sac"], ("warn", "智慧型應用程式控制開著；它擋了 PDF 元件，PDF 會讀不到；不建議關，關了可能開不回來"))

    def test_pdf_missing_has_fix(self):
        build._pdfium_installed = lambda: False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            build_cli._env(self.D, True)
        pdf = next(i for i in json.loads(buf.getvalue())["items"] if i["key"] == "pdf")
        self.assertEqual(pdf, {"key": "pdf", "level": "fail", "text": "還沒裝 PDF 元件，PDF 會讀不到", "fix": "kb office setup"})

    def test_no_office(self):
        build._office_env = lambda: {"office": {"word": None, "excel": None, "powerpoint": None}, "soffice": None,
                                     "node": None, "npm": None, "node_packages": False, "py_missing": [], "pdfium": True}
        self.assertEqual(self.env()["office"], ("warn", "沒有 Office 也沒有 LibreOffice：轉 PDF、重算 Excel 做不到，讀檔不受影響"))

    def test_low_disk(self):
        build._disk_free = lambda p: 500 * 1024 ** 2
        self.assertEqual(self.env()["disk"], ("warn", "剩 500 MB，少於 1 GB，整理到一半可能寫不下"))

    def test_old_wiki(self):
        self.plugins(True)
        self.assertEqual(self.env()["old_wiki"], ("fail", "舊的 llm-wiki 開著，要先在外掛頁停用"))
        self.plugins(False)
        self.assertEqual(self.env()["old_wiki"], ("ok", "舊的 llm-wiki 停用了"))
        (self.D / ".claude").mkdir()
        (self.D / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"llm-wiki@llm-wiki": True}}),
                                                          encoding="utf-8")
        self.assertEqual(self.env()["old_wiki"][0], "fail", "這個資料夾的設定蓋過使用者的")

    def test_real_run_reads_only(self):
        before = tree_hashes(self.D)
        got = json.loads(self.ok("build", "env", "--json"))
        self.assertEqual([i["key"] for i in got["items"]], list(build.ENV_KEYS))
        self.assertTrue(all(i["level"] in ("ok", "warn", "fail") for i in got["items"]))
        self.assertEqual(tree_hashes(self.D), before)
        self.assertFalse((self.D / ".system").exists())

    def test_runs_in_built_vault(self):
        """換電腦（D-208）會在建好的知識庫叫它：不擋、不碰 build.json。"""
        V = self.tmp / "vault"
        shutil.copytree(FIXTURES / "inventory" / "vault", V)
        before = tree_hashes(V)
        self.assertEqual(len(json.loads(self.ok("build", "env", "--json", cwd=V))["items"]), 8)
        self.assertEqual(tree_hashes(V), before)


class WithSource(Base):
    """登記了來源的建置：facts、--ids、profile apply --from、report。"""

    def setUp(self):
        super().setUp()
        self.raw = self.tmp / "raw"
        shutil.copytree(FIXTURES / "inventory" / "raw", self.raw)
        self.start()
        (self.D / ".system" / "sources.json").write_text(json.dumps({"schema_version": 1, "sources": [  # 暫存夾在 AppData：
            {"id": "src-1", "alias": "案件", "spec": "../raw", "excludes": []}]}, ensure_ascii=False),  # folders add 會擋，直接寫
            encoding="utf-8")
        self.ok("inventory", "scan")

    def test_facts(self):
        got = json.loads(self.ok("build", "facts", "--json"))
        (src,) = got["sources"]
        self.assertEqual(src["alias"], "案件")
        self.assertEqual(sum(f["files"] for f in src["folders"]), src["files"])
        self.assertEqual(got["total"], src["files"])
        self.assertIn("客戶案件", [f["name"] for f in src["folders"]])
        self.assertIn("檔名像密碼", src["unreadable"]["reasons"])
        self.assertIn("不支援的格式", src["unreadable"]["reasons"])
        self.assertEqual(src["unreadable"]["count"], sum(src["unreadable"]["reasons"].values()))
        self.assertTrue(got["estimate"]["text"].startswith("大約"))
        self.assertIn(".pdf", src["formats"])

    def test_setup_then_inventory(self):
        self.assertEqual(self.ok("build", "done", "setup"), "建置：訪談 做完了，下一步 盤點")
        self.assertEqual(json.loads(self.ok("build", "status", "--json"))["do"], ["kb build facts --json"])

    def test_dispatch_ids_only_those(self):
        new = json.loads(self.ok("cards", "todo", "--json"))["new"]
        a, b = new[0]["id"], new[1]["id"]
        got = json.loads(self.ok("--session", SID, "cards", "dispatch", "--ids", f"{a},{b}", "--json"))
        self.assertEqual([x["items"] for x in got["batches"]], [2])
        batch = json.loads(self.ok("cards", "batch", got["batches"][0]["batch"], "--json"))
        self.assertEqual(sorted(i["id"] for i in batch["items"]), sorted([a, b]))
        self.assertEqual(self.refused("--session", "sid-other", "cards", "dispatch", "--ids", "f-deadbeef", "--json"),
                         "這幾份不在要整理的清單裡：f-deadbeef")


class ProfileFrom(Base):
    def setUp(self):
        super().setUp()
        self.start()
        self.md = self.D / "系統" / "客戶設定檔.md"
        self.filled = self.tmp / "填好的設定檔.md"

    def test_apply_from_filled(self):
        sep = "|---|---|---|---|---|" + chr(10)
        self.filled.write_text(self.md.read_text(encoding="utf-8").replace(
            sep, sep + "| 工程 | 事 | 室內設計的案子 |  |  |" + chr(10), 1), encoding="utf-8")
        out = self.ok("profile", "apply", "--from", str(self.filled))
        self.assertTrue(out.startswith("已套用客戶設定檔："), out)
        prof = json.loads((self.D / ".system" / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in prof["types"]], ["工程"])
        self.assertTrue((self.D / "工程").is_dir())
        self.assertIn("| 工程 | 事 |", self.md.read_text(encoding="utf-8"))

    def test_unreadable_writes_nothing(self):
        sep = "|---|---|---|---|---|" + chr(10)
        self.filled.write_text(self.md.read_text(encoding="utf-8").replace(
            sep, sep + "| 工程 | 不存在的大類 |  |  |  |" + chr(10), 1), encoding="utf-8")
        before = tree_hashes(self.D / "系統"), (self.D / ".system" / "profile.json").read_bytes()
        out = self.refused("profile", "apply", "--from", str(self.filled))
        self.assertTrue(out.startswith("客戶設定檔讀不懂：第 1 節"), out)
        self.assertEqual((tree_hashes(self.D / "系統"), (self.D / ".system" / "profile.json").read_bytes()), before)
        self.assertFalse((self.D / "工程").exists())


class Adopt(Base):
    def setUp(self):
        super().setUp()
        self.O = self.tmp / "old_vault"
        shutil.copytree(FIXTURES / "old_vault", self.O)
        (self.tmp / "外部案子").mkdir()

    def test_check_matches_fixture(self):
        before = tree_hashes(self.O)
        got = json.loads(self.ok("build", "adopt", str(self.O), "--check", "--json"))
        hot = (self.O / "wiki" / "hot.md").read_text(encoding="utf-8")
        log = (self.O / "wiki" / "log.md").read_text(encoding="utf-8")
        self.assertEqual(got, {"path": self.O.as_posix(), "raw": (self.O / "raw").as_posix(),
                               "linked": [{"alias": "案子", "path": (self.tmp / "外部案子").as_posix(), "exists": True}],
                               "wiki_pages": 2, "hot_chars": len(hot), "log_chars": len(log), "rules": 2, "agenda": 1})
        self.assertEqual(tree_hashes(self.O), before)

    def test_not_old_vault(self):
        self.assertEqual(self.refused("build", "adopt", str(FIXTURES / "inventory" / "raw"), "--check"),
                         "這不像舊的 llm-wiki 資料夾")

    def test_extract_chunks_masked_and_old_vault_untouched(self):
        self.start()
        (self.D / ".system" / "config.json").write_text(json.dumps({"adopt_chunk_chars": 700}), encoding="utf-8")
        before = tree_hashes(self.O)
        human = self.ok("build", "adopt", str(self.O), "--extract")
        self.assertRegex(human, r"^切好了：\d+ 段（\d+ 份檔）$")
        got = json.loads(self.ok("build", "adopt", str(self.O), "--extract", "--json"))
        self.assertGreater(len(got["chunks"]), 1)
        texts = [Path(c).read_text(encoding="utf-8") for c in got["chunks"]]
        self.assertTrue(all(len(t) <= 700 for t in texts), [len(t) for t in texts])
        whole = "".join(texts)
        self.assertIn("尾款延到年底", whole)
        self.assertIn("報價一律以最新一版為準", whole)
        self.assertNotIn("abc12345", whole)
        self.assertNotIn("尾款", human)
        self.assertEqual({f["path"] for f in got["files"]},
                         {"wiki/hot.md", "CLAUDE.md", "wiki/agenda.md", "wiki/projects/王宅.md", "wiki/topics/付款習慣.md",
                          "wiki/log.md"})
        self.assertEqual(tree_hashes(self.O), before)

    def test_dispatch_adopt_and_batch(self):
        self.start()
        self.assertEqual(self.refused("--session", SID, "cards", "dispatch", "--adopt", "--json"),
                         "還沒切段，先跑 kb build adopt <舊 wiki 的路徑> --extract")
        chunks = json.loads(self.ok("build", "adopt", str(self.O), "--extract", "--json"))["chunks"]
        got = json.loads(self.ok("--session", SID, "cards", "dispatch", "--adopt", "--json"))
        self.assertEqual(got["say"], "交給製卡助手在背景讀舊的 wiki，好了跟你說；這期間照常問我")
        self.assertEqual(len(chunks), 6)  # 六份檔各一段
        self.assertEqual([(b["kind"], b["items"], b["agent"]) for b in got["batches"]],
                         [("adopt", 4, "wiki-with-willy:card-maker"), ("adopt", 2, "wiki-with-willy:card-maker")])
        b = got["batches"][0]
        self.assertTrue(b["prompt"].startswith(f"批次 {b['batch']}：讀舊 wiki 4 段。"))
        self.assertEqual(b["description"], "整理第 1 批（讀舊 wiki 4 段）")
        view = json.loads(self.ok("cards", "batch", b["batch"], "--json"))
        self.assertEqual([i["chunk"] for i in view["items"]], chunks[:4])
        self.assertIn("口述｜<實體名稱或「無」>｜<YYYY-MM-DD 或「未載」>｜<一句>", view["report"])
        self.assertTrue(view["read"][0].endswith("carding.md 批次、第十二節"))
        self.assertEqual(self.refused("--session", SID, "cards", "dispatch", "--adopt", "--ids", "f-1"),
                         "--adopt 不跟 --source、--ids 一起用")


class Report(Base):
    def setUp(self):
        super().setUp()
        self.start()
        self.ok("build", "set", "people", "兩個人")
        self.tid = turn.current(self.D, SID)["turn"]
        self.ws = self.D / ".system" / "temp" / "office" / self.tid
        self.ws.mkdir(parents=True)
        self.src = self.ws / "建置報告.md"

    def write(self, drop=None):
        heads = [h for h in build.REPORT_HEADS if h != drop]
        body = {"你的資料去了哪": "檔案留在原本的夾。網銀密碼：abc12345", "怎麼開始問": "問「王宅到哪了」。",
                "我猜的請改": "", "讀不到的": "<!-- 提示 -->", "要你決定的": "沒有。", "數字": "AI 不寫這段"}
        self.src.write_text("# 建置報告\n\n" + "\n".join(f"## {h}\n\n{body[h]}\n" for h in heads), encoding="utf-8")

    def test_from_writes_report(self):
        facts = json.loads(self.ok("build", "report", "--facts", "--json"))
        self.assertEqual((facts["people"], facts["files"], facts["no_sources"]), ("兩個人", 0, False))
        self.write()
        self.assertEqual(self.ok("build", "report", "--from", str(self.src)), "建置報告寫好了：系統/建置報告.md")
        text = (self.D / "系統" / "建置報告.md").read_text(encoding="utf-8")
        self.assertNotIn("abc12345", text)
        self.assertIn("## 你的資料去了哪", text)
        self.assertNotIn("## 我猜的請改", text, "沒內容的段不寫")
        self.assertNotIn("## 讀不到的", text, "只有註解也算沒內容")
        self.assertNotIn("AI 不寫這段", text)
        self.assertIn("## 數字", text)
        self.assertIn("- 幾個人用：兩個人", text)
        self.assertIn({"path": "系統/建置報告.md", "action": "create"},
                      [{k: e[k] for k in ("path", "action")} for e in turn.entries(self.D, self.tid)])
        self.assertTrue(self.data()["report"])

    def test_missing_section_refused(self):
        self.write(drop="讀不到的")
        self.assertEqual(self.refused("build", "report", "--from", str(self.src)), "建置報告少了一段：讀不到的")
        self.assertFalse((self.D / "系統" / "建置報告.md").exists())

    def test_extra_section_refused(self):
        self.write()
        self.src.write_text(self.src.read_text(encoding="utf-8") + "\n## 附錄\n\nx\n", encoding="utf-8")
        self.assertEqual(self.refused("build", "report", "--from", str(self.src)), "建置報告多了一段：附錄")

    def test_outside_workspace_refused(self):
        other = self.tmp / "外面.md"
        other.write_text("x", encoding="utf-8")
        self.assertEqual(self.refused("build", "report", "--from", str(other)),
                         "建置報告要先寫在工作區（.system/temp/office/ 底下）再交給我")


class Accept(unittest.TestCase):
    """entity accept（D-202，驗收 8）：source ai_judged、todo 當確認、asks 沒有它的名字題、list 標 AI 判斷。"""

    def setUp(self):
        self.tmp, self.V, self.raw = E.make_entity_vault(with_entities=True)
        E.write_card(self.V, E.QUOTE, "王宅報價", names=["王宅"])
        E.write_card(self.V, E.ESTIMATE, "王宅估價", names=["王宅"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def todo(self):
        return json.loads(run_kb("cards", "todo", "--json", cwd=self.V).stdout)

    def test_accept(self):
        before = self.todo()
        (wang,) = [i for i in before["synthesis_new"] if i["name"] == "王宅"]
        self.assertFalse(wang["confirmed"])
        self.assertEqual(before["plan"][0]["step"], "names")
        self.assertIn("name:" + E.WANG, [a["id"] for a in before["asks"]])
        r = run_kb("entity", "accept", "王宅", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已登記：王宅（AI 判斷）"))
        entry = next(e for e in entities.load(self.V)["entities"] if e["name"] == "王宅")
        self.assertEqual(entry["source"], "ai_judged")
        after = self.todo()
        (wang,) = [i for i in after["synthesis_new"] if i["name"] == "王宅"]
        self.assertTrue(wang["confirmed"])
        self.assertNotIn("question", wang)
        self.assertNotIn("name:" + E.WANG, [a["id"] for a in after["asks"]])
        self.assertNotIn("names", [s["step"] for s in after["plan"]])
        self.assertIn("dispatch", [s["step"] for s in after["plan"]])
        lines = run_kb("entity", "list", cwd=self.V).stdout
        self.assertIn("- 王宅（案子，AI 判斷，進行中）掛 2 張", lines)

    def test_sentences(self):
        r = run_kb("entity", "accept", "信義王", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已經確認過了：信義王"))
        r = run_kb("entity", "accept", "沒有這個", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "實體清單沒有：沒有這個"))
        self.assertEqual(run_kb("entity", "accept", "王宅", cwd=self.V).returncode, 0)
        r = run_kb("entity", "accept", "王宅", cwd=self.V)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "已登記：王宅（AI 判斷）"), "再說一次不重寫")


if __name__ == "__main__":
    unittest.main()
