"""manage 第二版第三輪修（測試報告「第三輪」一節，設計 2026-10-22 定的四條）：每條一組測試。"""
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from tests import _cards, _entities
from tests.test_lib_source_edit import Base
from kb import cli, folders, guard, profile_cli
from kb._lib import cards, cmdparse, plan, purge, turn  # noqa: E402
from kb._lib import io as kio  # noqa: E402
from kb._lib.paths import plugin_root  # noqa: E402

CURLY = [chr(c) for c in range(0x2018, 0x2020)]
KB_IN_VAULT = "知識庫裡的夾不能設成不讀；要不讓 AI 看到，先搬到登記的來源夾再設"
CURLY_DENY = "指令裡有彎引號，PowerShell 會拆開；夾名有彎引號請她先改名"


def kb(*args, cwd=None):
    saved = kio.LOCK_TIMEOUT
    here = os.getcwd()
    if cwd:
        os.chdir(cwd)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), mock.patch.object(folders, "system_dirs", return_value=[]):
            code = cli.main(list(args))
    finally:
        os.chdir(here)
        kio.LOCK_TIMEOUT = saved
    return code, out.getvalue()


# ---------- 第 1 條：exclude、include 收 find 的輸出 ----------

class ExcludeFindForm(Base):
    def setUp(self):
        super().setUp()
        (self.raw / "客戶案件" / "私人").mkdir()
        (Path(self.V) / "案子" / "信義王" / "私人").mkdir(parents=True, exist_ok=True)

    def excludes(self):
        data = json.loads((Path(self.V) / ".system/sources.json").read_text(encoding="utf-8"))
        return [x["spec"] for x in data["sources"][0]["excludes"]]

    def test_exclude_and_include_alias_form_cwd_vault(self):
        code, out = kb("folders", "exclude", "客戶案件/私人", "--kind", "unused", cwd=self.V)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.excludes(), ["私人"])
        code, out = kb("folders", "include", "客戶案件/私人", cwd=self.V)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.excludes(), [])

    def test_exclude_alias_form_with_vault_flag(self):
        code, out = kb("folders", "exclude", "客戶案件/私人", "--kind", "unused", "--vault", str(self.V), cwd=self.tmp)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.excludes(), ["私人"])

    def test_vault_folders_refused(self):
        for target in ("知識庫/案子/信義王/私人", str(Path(self.V) / "案子" / "信義王" / "私人")):
            with self.subTest(target=target):
                code, out = kb("folders", "exclude", target, "--kind", "hidden", "--vault", str(self.V))
                self.assertEqual((code, out.strip()), (3, KB_IN_VAULT))

    def test_find_json_gives_ready_commands(self):
        (self.raw / "報價與合約" / "Q3$x").mkdir()
        (self.raw / "報價與合約" / "Q3$x" / "私人").mkdir()
        (self.raw / "報價與合約" / "a”; calc; “b").mkdir()
        (self.raw / "報價與合約" / "a”; calc; “b" / "私人").mkdir()
        code, out = kb("folders", "find", "私人", "--json", "--vault", str(self.V))
        got = {f["path"]: f for f in json.loads(out)["found"]}
        self.assertEqual(got["客戶案件/私人"]["exclude"],
                         {"hidden": "kb folders exclude 客戶案件/私人 --kind hidden",
                          "unused": "kb folders exclude 客戶案件/私人 --kind unused"})
        self.assertEqual(got["報價與合約/Q3$x/私人"]["exclude"]["hidden"],
                         "kb folders exclude '報價與合約/Q3$x/私人' --kind hidden")
        curly = got["報價與合約/a”; calc; “b/私人"]
        self.assertIsNone(curly["exclude"])
        self.assertIn("改名", curly["note"])
        vault_one = got["知識庫/案子/信義王/私人"]
        self.assertTrue(vault_one["in_vault"])
        self.assertIsNone(vault_one["exclude"])
        self.assertEqual(vault_one["note"], KB_IN_VAULT)
        code, out = kb("folders", "find", "私人", "--vault", str(self.V))
        self.assertIn("- 知識庫/案子/信義王/私人（知識庫裡的夾，不能設成不讀）", out)


# ---------- 第 2 條：守門的彎引號 ----------

class GuardCurly(Base):
    def test_kb_call_with_curly_quotes_denied(self):
        kbpy = os.path.join(str(plugin_root()), "scripts", "kb.py")
        for tool in ("Bash", "PowerShell"):
            for ch in CURLY:
                with self.subTest(tool=tool, ch=hex(ord(ch))):
                    command = f'python "{kbpy}" folders exclude "客戶案件/a{ch}; calc; {ch}b" --kind unused'
                    self.assertFalse(cmdparse.is_single_plugin_call(command, plugin_root(), str(self.V)))
                    d = guard.decide({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(self.V)})
                    self.assertEqual((d.decision, d.reason), ("deny", CURLY_DENY))

    def test_plain_kb_call_still_allowed(self):
        kbpy = os.path.join(str(plugin_root()), "scripts", "kb.py")
        d = guard.decide({"tool_name": "Bash", "tool_input": {"command": f'python "{kbpy}" folders list'},
                          "cwd": str(self.V)})
        self.assertEqual(d.decision, "allow")


# ---------- 第 3 條：purge 還留著夾名與卡標題 ----------

class PurgeLeftovers(Base):
    def setUp(self):
        super().setUp()
        self.private = self.raw / "客戶案件" / "私人"
        self.private.mkdir()
        (self.private / "日記.txt").write_text("秘密", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.t_create = turn.start(self.V, None, "")["turn"]
        item = _entities.new_item(self.V, "客戶案件/私人/日記.txt")
        p = _cards.card_path(self.V, item, "王家心情")
        p.parent.mkdir(parents=True, exist_ok=True)
        turn.backup(self.V, p.relative_to(self.V).as_posix(), self.t_create)  # create 列，沒有 files/
        p.write_text(_cards.card_text(item, "王家心情"), encoding="utf-8", newline="\n")
        t = turn.start(self.V, None, "")["turn"]
        cards.render_index(self.V, t)  # 備份的是之前的總目錄
        cards.render_index(self.V, turn.start(self.V, None, "")["turn"])  # 這一版的備份含「私人」節點與卡
        self.t_moves = turn.start(self.V, None, "")["turn"]
        turn.record_move(self.V, self.t_moves, "move", "file", "file://客戶案件/私人/秘密檔.pdf", "file://客戶案件/秘密檔.pdf")

    def grep(self, needle):
        hits = []
        for f in Path(self.V).rglob("*"):
            if f.is_file() and not f.name.endswith(".lock"):
                if needle in f.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(f.relative_to(self.V).as_posix())
        return hits

    def test_no_folder_name_or_title_left(self):
        turn.start(self.V, None, "")
        code, out = kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))
        self.assertIn("清好了", out)
        rows = [json.loads(ln) for ln in (Path(self.V) / ".system/versions" / self.t_create / "manifest.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["action"], r["path"]) for r in rows], [("purged", "（已排除）")])
        moves = (Path(self.V) / ".system/versions" / self.t_moves / "moves.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("私人", moves)
        allowed = lambda h: h.endswith(".system/sources.json")  # noqa: E731  只剩登記表那條排除的夾名
        self.assertEqual([h for h in self.grep("私人") if not allowed(h)], [])
        self.assertEqual(self.grep("王家心情"), [])

    def test_nested_junction_under_files_not_followed(self):
        outside = self.tmp / "庫外"
        outside.mkdir()
        (outside / "待處理.md").write_text("客戶案件/私人/日記.txt", encoding="utf-8")
        files = Path(self.V) / ".system/versions" / self.t_moves / "files"
        files.mkdir(parents=True, exist_ok=True)
        link = files / "系統"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True) \
            if os.name == "nt" else None
        if os.name != "nt":
            os.symlink(outside, link)
        elif r.returncode != 0:
            self.skipTest("建不了 junction")
        turn.start(self.V, None, "")
        kb("folders", "exclude", str(self.private), "--kind", "hidden", "--vault", str(self.V))
        self.assertTrue((outside / "待處理.md").is_file())


# ---------- 第 4 條：順手修 ----------

class Small4(Base):
    def test_more_device_names(self):
        for name in ("COM¹", "com²", "LPT³.txt", "CONIN$", "conout$.pdf"):
            with self.subTest(name=name):
                self.assertEqual(self.refuse(self.plan, self.fid, new_name=name), "這種名字 Windows 不收，換個名字")

    def test_profile_cli_result_with_none(self):
        r = profile_cli._result(1, "改名叫x", [plan.cmd("profile", "type", "rename", "a”b", "--to", "x")])
        self.assertEqual(r["do"], [])
        self.assertIn("note", r)
