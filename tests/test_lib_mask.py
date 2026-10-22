"""遮值（D-187，密碼與守門第 4 點、驗收 14）：只遮值本身，關鍵字、分隔、前後的話照留。

- 比對關鍵字前先 NFKC、去空白（ｐａｓｓｗｏｒｄ、密 碼 認得出來），位置對回原字串；
- 關鍵字後接分隔（：、:、=、是、為、空白）的那一段遮到下一個空白或標點；值在前的「<值> 是他的密碼」遮 <值>；
- 不遮：清冊裡有的檔名（known_names）、程式固定句（SAFE_PHRASES）。
"""
import unittest

from tests._guard_fixture import ROOT  # noqa: F401  （把 scripts 放進 sys.path）
from kb._lib import credentials

M = credentials.MASK


def m(text, **kw):
    return credentials.mask_secrets(text, **kw)


class OnlyTheValue(unittest.TestCase):
    def test_acceptance_14(self):
        self.assertEqual(m("密碼：abc"), f"密碼：{M}")
        self.assertEqual(m("密碼是 abc"), f"密碼是 {M}")
        self.assertEqual(m("abc 是他的密碼"), f"{M} 是他的密碼")
        self.assertEqual(m("ｐａｓｓｗｏｒｄ：abc"), f"ｐａｓｓｗｏｒｄ：{M}")
        self.assertEqual(m("密 碼：abc"), f"密 碼：{M}")

    def test_value_first_variants(self):
        self.assertEqual(m("hunter2 是他的密碼"), f"{M} 是他的密碼")
        self.assertEqual(m("hunter2是我的密碼"), f"{M}是我的密碼")
        self.assertEqual(m("hunter2 is my password"), f"{M} is my password")

    def test_rest_of_sentence_stays(self):
        self.assertEqual(m("密碼 abc，之後再改"), f"密碼 {M}，之後再改")
        self.assertEqual(m("密碼 abc。謝謝"), f"密碼 {M}。謝謝")
        self.assertEqual(m("my password is hunter2 ok"), f"my password is {M} ok")
        self.assertEqual(m("Password: Abc_9!"), f"Password: {M}")
        self.assertEqual(m("幫我登入 ERP，帳號 jie 密碼 abc123"), f"幫我登入 ERP，帳號 {M} 密碼 {M}")
        self.assertEqual(m("我的密碼是 Secret!23"), f"我的密碼是 {M}")

    def test_separators(self):
        for text, want in (("TOKEN=abcd", f"TOKEN={M}"), ("PGPASSWORD=xyz 請存", f"PGPASSWORD={M} 請存"),
                           ("密碼為abc", f"密碼為{M}"), ("password:\nhunter2", f"password:\n{M}"),
                           ("密碼\nabc123", f"密碼\n{M}"), ("PIN 碼 4821", f"PIN 碼 {M}"),
                           ("Token: Bearer eyJabc", f"Token: {M}"), ("pw abc", f"pw {M}"),
                           ("金鑰：xyz。", f"金鑰：{M}。"), ("密碼我設成 abc123", f"密碼我設成 {M}"),
                           ("以後密碼都用 1234", f"以後密碼都用 {M}")):
            with self.subTest(text=text):
                self.assertEqual(m(text), want)

    def test_two_values_after_account_and_password(self):
        self.assertEqual(m("帳號密碼分別是 jie、abc123"), f"帳號密碼分別是 {M}、{M}")
        self.assertEqual(m("帳號密碼分別是 jie，abc123"), f"帳號密碼分別是 {M}，{M}")
        self.assertEqual(m("帳密：jie abc123"), f"帳密：{M} {M}")
        self.assertEqual(m("密碼 abc、之後再改"), f"密碼 {M}、之後再改")

    def test_quoted_value_whole(self):
        self.assertEqual(m('密碼是 "abc def"'), f"密碼是 {M}")
        self.assertEqual(m("密碼是「abc def」"), f"密碼是{M}")

    def test_kb_value_flag(self):
        """指令被守門擋下時 ledger 記指令開頭（驗收 2c 情境）：--value、--password 的值也遮。"""
        cmd = 'python kb.py unlock save --kind 記下的密碼 --name 工地大門 --value "1234"'
        got = m(cmd)
        self.assertNotIn("1234", got)
        self.assertIn("工地大門", got)
        self.assertNotIn("lin2026", m('python kb.py convert f-1 --password lin2026'))
        self.assertNotIn("x9", m("kb convert f-1 --password=x9"))

    def test_other_keywords_still_mask_value(self):
        for text in ("api-key: k1", "api_key k2", "密码是k3", "passphrase k4", "pin 1234", "PIN碼 5678", "驗證碼 999",
                     "otp=12", "userPassword=k5", "accessToken=k6", "mySecret k7", "api key: k8"):
            with self.subTest(text=text):
                got = m(text)
                self.assertIn(M, got)
                self.assertNotIn(text.split()[-1].split("=")[-1].split("是")[-1].split(":")[-1], got)


class NotMasked(unittest.TestCase):
    def test_bare_keyword_or_no_value(self):
        for text in ("secret", "今天天氣好", "", "典型的 typing fast", "spinach 1", "stopwatch 2", "ottoman 3",
                     "工地大門密碼是多少？", "門禁密碼多少", "密碼是什麼", "當成密碼檔"):
            with self.subTest(text=text):
                self.assertEqual(m(text), text)

    def test_file_names_and_fixed_phrases(self):
        """B-093 2-4e：「當成密碼檔」不遮；清冊裡有的檔名與程式固定句不遮（驗收 14）。"""
        self.assertEqual(m("整理了 網銀密碼.txt 以外的 5 份", known_names=["網銀密碼.txt"]),
                         "整理了 網銀密碼.txt 以外的 5 份")
        self.assertEqual(m("讀不到：有密碼"), "讀不到：有密碼")
        for phrase in credentials.SAFE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertEqual(m(phrase), phrase)

    def test_known_name_that_looks_like_value(self):
        """檔名本身帶分隔（「密碼 王.txt」），不給 known_names 會遮，給了不遮。"""
        self.assertNotEqual(m("看了 密碼 王.txt"), "看了 密碼 王.txt")
        self.assertEqual(m("看了 密碼 王.txt", known_names=["密碼 王.txt"]), "看了 密碼 王.txt")

    def test_none_and_non_str(self):
        self.assertIsNone(m(None))
        self.assertEqual(m(""), "")


class KnownNamesFromInventory(unittest.TestCase):
    """日誌與回合從清冊取檔名（D-187 (c)）：清冊有「密碼 王.txt」，問與結論提到它不遮；清冊讀不到照舊遮。"""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp(prefix="kbmask-"))
        (self.tmp / ".system").mkdir()
        inv = {"schema_version": 2, "entries": [
            {"id": "f-1a2b3c4d", "locations": [{"uri": "file://客戶案件/密碼 王.txt"}], "status": "active"},
            {"id": "f-2a2b3c4d", "locations": [{"uri": "file://客戶案件/a.txt"}], "status": "active"}]}
        (self.tmp / ".system" / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_known_names_only_those_with_keywords(self):
        self.assertEqual(credentials.known_names(self.tmp), ["密碼 王.txt"])
        self.assertEqual(credentials.known_names(self.tmp / "nope"), [])
        self.assertEqual(credentials.known_names(None), [])

    def test_prompt_head_and_journal_use_inventory_names(self):
        from kb._lib import journal, turn
        text = "看了 密碼 王.txt 之後再說"
        self.assertEqual(turn.prompt_head(self.tmp, text), text)
        self.assertEqual(journal.cut_result(text, 300, self.tmp), (text, False))
        self.assertNotEqual(journal.cut_result(text, 300), (text, False))  # 沒給知識庫：照舊遮


if __name__ == "__main__":
    unittest.main()
