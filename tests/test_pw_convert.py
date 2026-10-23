"""kb convert 帶密碼（密碼與守門第 3 點；D-181、D-182、D-183；驗收 3、7、9、16）：
--password（她剛給的、一次性）、--saved（憑證管理員那一筆；要授權過的種類或 --explicit）、只有擁有者密碼的先不帶密碼試、
加密的 Word 照舊讀不到、gate 讀「可以讀」裁示、讀「可以讀」的密碼檔記 secret_shown；全程知識庫找不到密碼。
用到憑證管理員的只在 Windows 跑（名稱在這次測試的隨機知識庫編號底下，測完刪掉）；沒裝 pypdfium2 的機器 skip PDF 的。"""
import json
import shutil
import sys
import unittest
from pathlib import Path

from tests import _cards, _pw
from tests.test_lib_unlock import wipe
from tests.test_scripts_inventory_cli import run_kb
from kb._lib import pending, rulings, turn, unlock  # noqa: E402

ID = r"[aefsrcp]-[0-9a-f]{8}"
try:
    import pypdfium2  # noqa: F401
    HAVE_PDF = True
except Exception:  # noqa: BLE001  沒裝或 DLL 載不起來都算沒有
    HAVE_PDF = False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _pw.make_pw_vault()
        unlock.BACKEND = None

    def tearDown(self):
        try:
            wipe(self.V)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def fid(self, rel):
        return _cards.entry_of(self.V, rel)["id"]

    def convert(self, rel, *args, code=0, as_json=False):
        r = run_kb("convert", self.fid(rel), *args, *(["--json"] if as_json else []), cwd=self.V)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return json.loads(r.stdout) if as_json else r.stdout.splitlines()

    def open_pending(self, rel):
        fid = self.fid(rel)
        return [i for i in pending.load(self.V)["items"] if i.get("status") == "open" and fid in (i.get("file_ids") or [])]


@unittest.skipUnless(HAVE_PDF, "沒裝 pypdfium2")
class Password(Base):
    def test_once_password_opens_and_closes_pending(self):
        self.assertEqual(self.convert(_pw.WANG, code=3)[0], "讀不到：有密碼")
        self.assertEqual(len(self.open_pending(_pw.WANG)), 1)
        self.assertEqual(self.convert(_pw.WANG, "--password", "wrong-zq7x", code=3), ["密碼不對"])
        self.assertEqual(len(self.open_pending(_pw.WANG)), 1)
        out = self.convert(_pw.WANG, "--password", _pw.PDF_PW, as_json=True)
        self.assertTrue(out["ask_save"])
        self.assertIn("Insurance policy Wang", open(out["temp"], encoding="utf-8").read())
        self.assertEqual(self.open_pending(_pw.WANG), [])
        self.assertEqual(_pw.leaks(self.V), [])

    def test_owner_only_pdf_reads_without_password(self):
        out = self.convert(_pw.OWNER_ONLY, as_json=True)
        self.assertEqual(out["kind"], "pdf")
        self.assertIn("Quotation owner only", open(out["temp"], encoding="utf-8").read())

    def test_encrypted_docx_still_unreadable(self):
        self.assertEqual(self.convert(_pw.ENC_DOCX, code=3)[0], "讀不到：有密碼")
        self.assertEqual(self.convert(_pw.ENC_DOCX, "--password", _pw.PDF_PW, code=3)[0], "讀不到：有密碼")


@unittest.skipUnless(HAVE_PDF and sys.platform == "win32", "沒裝 pypdfium2 或不是 Windows")
class Saved(Base):
    def test_saved_needs_kind_or_explicit_then_records_opened(self):
        self.assertEqual(run_kb("unlock", "save", "--kind", "PDF 密碼", "--name", "保險單", "--value", _pw.PDF_PW,
                                cwd=self.V).returncode, 0)
        self.assertEqual(self.convert(_pw.CHEN, "--saved", "保險單", code=3), ["這組密碼沒說過可以用在別的檔"])
        self.assertEqual(self.convert(_pw.CHEN, "--saved", "沒有的", code=3), ["這台電腦沒有記「沒有的」"])
        out = self.convert(_pw.CHEN, "--saved", "保險單", "--explicit", as_json=True)
        self.assertFalse(out["ask_save"])
        self.assertEqual(run_kb("unlock", "authorize", "--name", "保險單", "--label", "保險單", "--example",
                                self.fid(_pw.WANG), cwd=self.V).returncode, 0)
        lines = self.convert(_pw.CHEN, "--saved", "保險單")
        for line in lines:
            if line.startswith(("暫存檔：", "圖：")):
                continue  # 暫存檔名照裁定 16 用清冊編號（路徑給 Read 用，不是給客戶的句子；回報列疑義）
            self.assertNotRegex(line, ID)
        opened = unlock.load(self.V)["opened"]
        self.assertIn((self.fid(_pw.CHEN), "保險單"), [(o["id"], o["label"]) for o in opened])
        self.assertEqual(_pw.leaks(self.V), [])

    def test_note_kind_is_not_for_pdf(self):
        run_kb("unlock", "save", "--kind", "記下的密碼", "--name", "門", "--value", _pw.GATE_PW, cwd=self.V)
        self.assertEqual(self.convert(_pw.CHEN, "--saved", "門", "--explicit", code=3), ["「門」不是 PDF 密碼"])


@unittest.skipUnless(HAVE_PDF, "沒裝 pypdfium2")
class NotEncrypted(Base):
    """測試 28 F9：沒加密（或只有擁有者密碼）的 PDF 給了 --password／--saved：不問要不要記、不記 opened、tell 不說用密碼打開。"""

    def test_password_not_needed(self):
        from tests._pdfcrypt import make_pdf
        plain = self.raw / "客戶案件" / "信義王" / "平面.pdf"
        make_pdf(plain, "Plain floor plan text for testing")
        _cards.scan_apply(self.V)
        out = self.convert("客戶案件/信義王/平面.pdf", "--password", _pw.PDF_PW, as_json=True)
        self.assertEqual((out["ask_save"], out["unlocked"]), (False, None))
        out = self.convert(_pw.OWNER_ONLY, "--password", _pw.PDF_PW, as_json=True)
        self.assertEqual((out["ask_save"], out["unlocked"]), (False, None))
        self.assertEqual(unlock.load(self.V)["opened"], [])


class Readable(Base):
    """驗收 2、3 的 convert 那半：「可以讀」的那份讀得到；讀的回合記 secret_shown；暫存是原檔內容，卡怎麼寫交 carding.md。"""

    def test_ruled_credential_file_readable_and_marks_secret_shown(self):
        self.assertEqual(self.convert(_pw.WEB, code=3)[0], "讀不到：檔名像密碼或金鑰，預設不讀")
        t = turn.start(self.V, "cv-sid", "網站密碼.xlsx 可以讀")["turn"]
        rulings.add(self.V, "可以讀", [], [], "網站密碼.xlsx 可以讀", t, files=[self.fid(_pw.WEB)])
        out = self.convert(_pw.WEB, as_json=True)
        self.assertEqual(out["kind"], "xlsx")
        sess = json.loads((self.V / ".system" / "turns" / "cv-sid.json").read_text(encoding="utf-8"))
        self.assertTrue(sess.get("secret_shown"))
        self.assertEqual(self.convert(_pw.BANK, code=3)[0], "讀不到：檔名像密碼或金鑰，預設不讀")

    def test_secret_temp_removed_at_stop_and_next_prompt(self):
        """設計 session 2026-10-23：暫存叫 <編號>.secret.md；Stop 收尾一律刪；Stop 沒跑到（中斷）的下一句開頭刪；一般暫存不受影響。
        驗收 3 的 grep 在那一句的 Stop 之後做。"""
        from tests.test_hooks_stop import HOOK as STOP, run as run_hook, stop_payload
        from tests.test_hooks_turn import prompt_payload, run_hook as run_turn
        sid = "sec-sid"
        run_turn(prompt_payload(self.V, "網站密碼.xlsx 可以讀", session_id=sid))
        t = turn.current(self.V)["turn"]
        rulings.add(self.V, "可以讀", [], [], "網站密碼.xlsx 可以讀", t, files=[self.fid(_pw.WEB)])
        out = self.convert(_pw.WEB, as_json=True)
        self.assertTrue(out["temp"].endswith(".secret.md"), out["temp"])
        normal = self.convert("客戶案件/大安陳/會議紀錄_1.docx", as_json=True)["temp"]
        self.assertTrue(normal.endswith(".md") and not normal.endswith(".secret.md"))
        self.assertIn(".system/temp/" + out["temp"].replace("\\", "/").rsplit("/", 1)[-1], _pw.leaks(self.V))
        run_hook(STOP, stop_payload(self.V, "網站密碼表是建材商訂貨網等三組帳密", sid=sid))
        self.assertEqual(_pw.leaks(self.V), [])
        self.assertTrue(Path(normal).exists())
        # 中斷：沒跑 Stop，下一句開頭刪
        self.convert(_pw.WEB)
        self.assertNotEqual(_pw.leaks(self.V), [])
        run_turn(prompt_payload(self.V, "下一句", session_id=sid))
        self.assertEqual(_pw.leaks(self.V), [])



class ConfirmTurnAndStop(Base):
    """桌面版確認落差 D：回答中途客戶插話，secret_shown 帶到下一個回合（上一句沒答完才帶）；
    縱深防禦：Stop 寫結果前比對這個知識庫存在憑證管理員的值，回答裡出現一模一樣的（4 字以上）整欄換掉。"""

    def journal_results(self):
        rows = []
        for p in (self.V / ".system" / "journal").glob("*.jsonl"):
            rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r.get("result") for r in rows]

    def test_flag_follows_interrupted_turn(self):
        from tests.test_hooks_stop import HOOK as STOP, run as run_hook, stop_payload
        from tests.test_hooks_turn import prompt_payload, run_hook as run_turn
        sid = "cut-sid"
        run_turn(prompt_payload(self.V, "工地大門密碼多少", session_id=sid))
        turn.mark(self.V, "secret_shown")
        run_turn(prompt_payload(self.V, "等一下，順便看日程", session_id=sid))  # 上一句沒等到 Stop
        sess = json.loads((self.V / ".system" / "turns" / f"{sid}.json").read_text(encoding="utf-8"))
        self.assertTrue(sess.get("secret_shown"))
        run_hook(STOP, stop_payload(self.V, "工地大門是 Gate-zq7x-8812，日程沒有新的", sid=sid))
        self.assertNotIn("Gate-zq7x-8812", "".join(str(r) for r in self.journal_results()))
        # 上一句答完了（Stop 跑過）：不帶
        run_turn(prompt_payload(self.V, "謝謝", session_id=sid))
        sess = json.loads((self.V / ".system" / "turns" / f"{sid}.json").read_text(encoding="utf-8"))
        self.assertFalse(sess.get("secret_shown"))

    @unittest.skipUnless(sys.platform == "win32", "Windows 憑證管理員")
    def test_stop_drops_result_with_saved_value(self):
        from tests.test_hooks_stop import HOOK as STOP, run as run_hook, stop_payload
        from tests.test_hooks_turn import prompt_payload, run_hook as run_turn
        for name, value in (("測試-工地大門", _pw.GATE_PW), ("測試-短", "zq7")):
            self.assertEqual(run_kb("unlock", "save", "--kind", "記下的密碼", "--name", name, "--value", value,
                                    cwd=self.V).returncode, 0)
        sid = "deep-sid"
        run_turn(prompt_payload(self.V, "幫我查日誌裡有沒有那串", session_id=sid))
        run_hook(STOP, stop_payload(self.V, f"搜 {_pw.GATE_PW} 命中兩個回合編號", sid=sid))
        self.assertIn("（這句回答有密碼，沒記）", self.journal_results())
        self.assertEqual(_pw.leaks(self.V), [])
        run_turn(prompt_payload(self.V, "短的呢", session_id=sid))
        run_hook(STOP, stop_payload(self.V, "zq7 這種三個字的不比", sid=sid))
        self.assertIn("zq7 這種三個字的不比", self.journal_results())


if __name__ == "__main__":
    unittest.main()
