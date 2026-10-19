"""_lib/profile（D-109～D-113，客戶設定檔與型別第 1 點）：預設值、渲染等於 fixture（D-067）、解析每種錯各一句、
三節都空算成功但缺節是錯（I-097）、3 欄與 5 欄混用、diff 與 merge（來源日期人填優先、等於舊值當沒填，實作疑義裁定 4）、
folder_check 四種、sha 判有改、status 只讀。"""
import json
import shutil
import unittest

from tests._guard_fixture import FIXTURES
from tests.test_lib_inventory_scan import make_inv_vault
from kb._lib import profile, turn  # noqa: E402

PROFILE = FIXTURES / "profile"
DAY = "2026-10-19"


def fixture_md(name="客戶設定檔.md"):
    return (PROFILE / name).read_text(encoding="utf-8")


def md_with(types=(), cards=None, exclusions=None):
    """空白版 md 的三張表換成給的列（字串）；cards／exclusions 是 None 時保留預設列。"""
    data = profile.defaults(DAY)
    if cards is not None:
        data["card_types"] = []
    if exclusions is not None:
        data["exclusions"] = []
    text = profile.render_text(data)
    heads = ("|---|---|---|---|---|\n", "|---|---|---|---|---|---|\n", "|---|---|---|---|\n")
    for num, head, rows in zip("一二三", heads, (list(types), cards or [], exclusions or [])):
        if rows:
            at = text.index("\n" + head, text.index(f"## {num}、")) + 1 + len(head)
            text = text[:at] + "".join(r + "\n" for r in rows) + text[at:]
    return text


class self_raises:
    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError("沒有丟 ProfileError")
        if not issubclass(kind, profile.ProfileError):
            return False
        self.error = value
        return True


def message(text, **kw):
    with self_raises() as cm:
        profile.parse_md(text, **kw)
    return cm.error.message


class Defaults(unittest.TestCase):
    def test_shape(self):
        d = profile.defaults(DAY)
        self.assertEqual((d["schema_version"], d["applied"], d["md_sha256"], d["types"]), (2, None, None, []))
        self.assertEqual((d["synthesis_sections"], d["about"]), ([], []))
        self.assertEqual([c["name"] for c in d["card_types"]],
                         ["報價", "合約", "會議紀錄", "型錄", "進度表", "範本", "說明", "其他"])
        for c in d["card_types"]:
            self.assertEqual([s["name"] for s in c["sections"]], ["重點", "日期與數字", "提到的人與單位", "沒讀到的部分"])
            self.assertFalse(any(s["required"] for s in c["sections"]))
            self.assertEqual((c["source"], c["date"]), ("inferred", DAY))
        self.assertEqual(len(d["exclusions"]), 9)
        self.assertTrue(all(e["action"] == "kind_only" and e["source"] == "inferred" for e in d["exclusions"]))

    def test_fixture_json_is_defaults_with_md_sha(self):
        data = json.loads((PROFILE / "profile.json").read_text(encoding="utf-8"))
        want = profile.defaults(DAY)
        want["md_sha256"] = profile.sha256_text(fixture_md())
        self.assertEqual(data, want)


class Render(unittest.TestCase):
    def test_render_equals_fixture(self):
        self.assertEqual(profile.render_text(profile.defaults(DAY)), fixture_md())

    def test_round_trip(self):
        d = profile.defaults(DAY)
        d["types"] = [{"name": "工地", "category": "matter", "note": "一個客戶一個裝修案", "source": "interview", "date": DAY}]
        d["card_types"][0]["sections"][1]["required"] = True
        got = profile.parse_md(profile.render_text(d))
        self.assertEqual(got, {k: d[k] for k in profile.LISTS})

    def test_pipe_in_note_does_not_break_table(self):
        d = profile.defaults(DAY)
        d["types"] = [{"name": "工地", "category": "matter", "note": "甲|乙", "source": "interview", "date": DAY}]
        self.assertEqual(profile.parse_md(profile.render_text(d))["types"][0]["note"], "甲｜乙")


class Parse(unittest.TestCase):
    def test_changed_fixture(self):
        got = profile.parse_md(fixture_md("改過.md"))
        self.assertEqual(got["types"], [{"name": "工班", "category": "matter", "note": "施工班底", "source": None,
                                         "date": None}])
        d = profile.diff(profile.defaults(DAY), got)
        self.assertEqual(d["types"], {"added": ["工班"], "removed": [], "changed": []})
        self.assertEqual(d["card_types"], {"added": [], "removed": [], "changed": [["報價", "日期與數字"]]})
        self.assertEqual(d["exclusions"], {"added": ["請款"], "removed": [], "changed": []})
        self.assertEqual(got["exclusions"][-1], {"kind": "請款", "action": "no_card", "source": None, "date": None})

    def test_broken_row_column_count(self):
        text = fixture_md("改過.md").replace("| 報價 | 提到的人與單位 |  |", "| 報價 | 提到的人與單位  |", 1)
        self.assertEqual(message(text), "第 2 節第 3 列欄數不對（要 4 欄或 6 欄）")

    def test_column_count_sentence_per_section(self):
        self.assertEqual(message(md_with(["| 工地 | 事 |"])), "第 1 節第 1 列欄數不對（要 3 欄或 5 欄）")
        self.assertEqual(message(md_with(exclusions=["| 請款 |"])), "第 3 節第 1 列欄數不對（要 2 欄或 4 欄）")

    def test_three_and_five_columns_mixed(self):
        got = profile.parse_md(md_with(["| 工地 | 事 | 一個客戶一個裝修案 |", "| 客戶 | 對象 | 業主 | 訪談 | 2026-10-01 |",
                                        "| 廠商 | 對象 | 工班與供應商 |  |  |"]))
        self.assertEqual([(t["name"], t["source"], t["date"]) for t in got["types"]],
                         [("工地", None, None), ("客戶", "interview", "2026-10-01"), ("廠商", None, None)])

    def test_three_sections_empty_is_ok(self):
        self.assertEqual(profile.parse_md(md_with(cards=[], exclusions=[])),
                         {"types": [], "card_types": [], "exclusions": [], "synthesis_sections": [], "about": []})

    def test_missing_section(self):
        text = md_with().replace("## 二、卡型與段落", "## 卡型與段落")
        self.assertEqual(message(text), "少了第 2 節（## 二、…）")

    def test_all_sections_missing_is_error(self):
        self.assertEqual(message("# 客戶設定檔\n隨便寫的\n"), "少了第 1 節（## 一、…）")

    def test_heading_suffix_not_compared(self):
        text = md_with(["| 工地 | 事 | x |"]).replace("## 一、型別", "## 一、我管的東西")
        self.assertEqual(profile.parse_md(text)["types"][0]["name"], "工地")

    def test_only_first_table_counts(self):
        text = md_with(["| 工地 | 事 | x |"]).replace("| 工地 | 事 | x |\n", "| 工地 | 事 | x |\n\n| 亂 | 寫 |\n", 1)
        self.assertEqual([t["name"] for t in profile.parse_md(text)["types"]], ["工地"])

    def test_category_error(self):
        self.assertEqual(message(md_with(["| 工地 | 事 | x |", "| 客戶 | 人 | y |"])),
                         "第 1 節第 2 列的大類要是 對象、事、物、主題 之一")

    def test_type_name_errors(self):
        cases = [("AI 產出", "跟保留字一樣"), ("ai 產出", "跟保留字一樣"), ("ＡＩ 產出", "跟保留字一樣"),
                 ("系統", "跟保留字一樣"), ("claude.md", "跟保留字一樣"), (".hidden", "不能以點開頭，也不能以點或空白結尾"),
                 ("a/b", "含不能用的字元"), ("x.", "不能以點開頭，也不能以點或空白結尾"), ("", "空的")]
        for name, why in cases:
            with self.subTest(name=name):
                self.assertEqual(message(md_with(["| 工地 | 事 | x |", f"| {name} | 事 | x |"])),
                                 f"第 1 節第 2 列的型別名「{name}」不能用（{why}）")

    def test_type_name_duplicate(self):
        self.assertEqual(message(md_with(["| 工地 | 事 | x |", "| 客戶 | 對象 | y |", "| 工地 | 物 | z |"])),
                         "第 1 節第 3 列的型別名「工地」不能用（重複）")

    def test_required_mark_error_and_accepted_marks(self):
        self.assertEqual(message(md_with(cards=["| 報價 | 重點 | 要 | x |"])), "第 2 節第 1 列的必出現只能打 ✓ 或留空")
        for mark in ("✓", "是", "v", "V", "Y"):
            got = profile.parse_md(md_with(cards=[f"| 報價 | 重點 | {mark} | x |"]))
            self.assertTrue(got["card_types"][0]["sections"][0]["required"], mark)

    def test_action_error(self):
        self.assertEqual(message(md_with(exclusions=["| 請款 | 跳過 |"])), "第 3 節第 1 列的處理要是 不整理 或 只記種類")

    def test_source_error(self):
        self.assertEqual(message(md_with(["| 工地 | 事 | x | 亂填 |  |"])),
                         "第 1 節第 1 列的來源要是 訪談、推測、確認、糾正 之一，或留空")

    def test_date_error(self):
        self.assertEqual(message(md_with(exclusions=["| 請款 | 不整理 | 訪談 | 10/19 |"])),
                         "第 3 節第 1 列的日期要寫 YYYY-MM-DD，或留空")
        self.assertEqual(message(md_with(exclusions=["| 請款 | 不整理 | 訪談 | 2026-02-30 |"])),
                         "第 3 節第 1 列的日期要寫 YYYY-MM-DD，或留空")

    def test_card_duplicate(self):
        self.assertEqual(message(md_with(cards=["| 報價 | 重點 |  | x |", "| 報價 | 日期 |  | y |", "| 報價 | 重點 |  | z |"])),
                         "第 2 節第 3 列跟第 1 列重複（卡型 報價 的段落 重點）")

    def test_card_and_kind_empty_and_kind_duplicate(self):
        self.assertEqual(message(md_with(cards=["|  | 重點 |  | x |"])), "第 2 節第 1 列的卡型是空的")
        self.assertEqual(message(md_with(exclusions=["|  | 不整理 |"])), "第 3 節第 1 列的種類是空的")
        self.assertEqual(message(md_with(exclusions=["| 請款 | 不整理 |", "| 請款 | 只記種類 |"])),
                         "第 3 節第 2 列跟第 1 列重複（種類 請款）")

    def test_card_source_from_first_row(self):
        got = profile.parse_md(md_with(cards=["| 請款 | 重點 |  | x | 訪談 | 2026-10-01 |", "| 請款 | 金額 | ✓ | y | 糾正 |  |"]))
        c = got["card_types"][0]
        self.assertEqual((c["name"], c["source"], c["date"], len(c["sections"])), ("請款", "interview", "2026-10-01", 2))


class Merge(unittest.TestCase):
    def test_first_apply_marks_new_rows_interview(self):
        old = profile.defaults("2026-10-01")
        new = profile.parse_md(fixture_md("改過.md").replace("2026-10-19", "2026-10-01"))
        got = profile.merge(old, new, today=DAY, first=True)
        self.assertEqual((got["types"][0]["source"], got["types"][0]["date"]), ("interview", DAY))
        self.assertEqual((got["exclusions"][-1]["source"], got["exclusions"][-1]["date"]), ("interview", DAY))
        quote = got["card_types"][0]
        self.assertEqual((quote["source"], quote["date"]), ("interview", DAY), "變了的卡型：渲染的舊值當沒填")
        self.assertTrue(quote["sections"][1]["required"])
        self.assertEqual((got["card_types"][1]["source"], got["card_types"][1]["date"]), ("inferred", "2026-10-01"))

    def test_later_apply_marks_confirmed_and_human_value_wins(self):
        old = profile.defaults("2026-10-01")
        old["types"] = [{"name": "工地", "category": "matter", "note": "x", "source": "interview", "date": "2026-10-01"}]
        new = profile.parse_md(md_with(["| 工地 | 事 | x | 訪談 | 2026-10-01 |", "| 工班 | 對象 | y |",
                                        "| 業主 | 對象 | z | 糾正 |  |", "| 客戶 | 對象 | w | 推測 | 2026-09-30 |"]))
        got = {t["name"]: (t["source"], t["date"]) for t in profile.merge(old, new, today=DAY)["types"]}
        self.assertEqual(got, {"工地": ("interview", "2026-10-01"), "工班": ("confirmed", DAY),
                               "業主": ("corrected", DAY), "客戶": ("inferred", "2026-09-30")})

    def test_changed_row_with_old_values_left_is_confirmed_today(self):
        old = profile.defaults("2026-10-01")
        old["types"] = [{"name": "工地", "category": "matter", "note": "x", "source": "interview", "date": "2026-10-01"}]
        new = profile.parse_md(md_with(["| 工地 | 事 | 改了說明 | 訪談 | 2026-10-01 |"]))
        t = profile.merge(old, new, today=DAY)["types"][0]
        self.assertEqual((t["note"], t["source"], t["date"]), ("改了說明", "confirmed", DAY))

    def test_order_follows_md(self):
        old = profile.defaults(DAY)
        new = profile.parse_md(md_with(["| 乙 | 事 | x |", "| 甲 | 事 | y |"]))
        self.assertEqual([t["name"] for t in profile.merge(old, new, today=DAY)["types"]], ["乙", "甲"])

    def test_unskipped_kinds(self):
        old = profile.defaults(DAY)
        old["exclusions"] += [{"kind": "請款", "action": "no_card"}, {"kind": "薪資", "action": "no_card"},
                              {"kind": "契約", "action": "no_card"}]
        new = {"exclusions": [{"kind": "薪資", "action": "kind_only"}, {"kind": "契約", "action": "no_card"}]}
        self.assertEqual(profile.unskipped_kinds(old, new), ["請款", "薪資"])

    def test_summaries(self):
        d = profile.diff(profile.defaults(DAY), profile.parse_md(fixture_md("改過.md")))
        self.assertEqual(profile.summary_full(d), "型別 +1 -0 ~0、卡型 +0 -0 ~1、不整理與敏感 +1 -0 ~0")
        self.assertEqual(profile.summary_short(d), "型別 +1、卡型 ~1、不整理與敏感 +1")


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = make_inv_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def md(self):
        return self.V / "系統" / "客戶設定檔.md"

    def test_fixture_vault_profile_is_consistent(self):
        data = profile.load(self.V)
        self.assertEqual([t["name"] for t in data["types"]], ["案子"])
        self.assertEqual(profile.md_sha256(self.V), data["md_sha256"])
        self.assertEqual(profile.status(self.V)["state"], "ok")

    def test_load_shapes(self):
        p = self.V / ".system" / "profile.json"
        for text in ("[]", '{"types": [], "card_types": []}', '{"schema_version": 2, "types": [], "card_types": [], '
                     '"exclusions": []}', "{bad", '{"types": [1], "card_types": [], "exclusions": []}'):
            p.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                profile.load(self.V)
        p.unlink()
        self.assertIsNone(profile.load(self.V))

    def test_folder_check_four_kinds(self):
        (self.V / "廠商").mkdir()
        (self.V / "空的").mkdir()
        (self.V / ".隱藏").mkdir()
        (self.V / "說明.txt").write_text("x", encoding="utf-8")
        old = [{"name": "案子"}, {"name": "空的"}, {"name": "沒有夾"}]
        got = profile.folder_check(self.V, [{"name": "工地"}, {"name": "廠商"}], old)
        self.assertEqual(got["to_create"], ["工地"])
        self.assertEqual(got["unlisted"], ["案子", "空的"])
        self.assertEqual(got["removed_empty"], ["空的", "沒有夾"])
        self.assertEqual(got["removed_nonempty"], [("案子", 2)])

    def test_sha_detects_change_and_status_states(self):
        self.assertEqual(profile.status(self.V)["state"], "ok")
        text = self.md().read_text(encoding="utf-8")
        self.md().write_text(text.replace("| 案子 | 事 |  | 推測 | 2026-10-19 |\n",
                                          "| 案子 | 事 |  | 推測 | 2026-10-19 |\n| 工地 | 事 | x |\n"), encoding="utf-8")
        st = profile.status(self.V)
        self.assertEqual((st["state"], st["diff"]["types"]["added"], st["to_create"]), ("changed", ["工地"], ["工地"]))
        self.md().write_text(text + "\n", encoding="utf-8")
        st = profile.status(self.V)
        self.assertEqual(st["state"], "changed")
        self.assertFalse(profile.has_diff(st["diff"]))
        self.md().write_text(text.replace("| 案子 | 事 |", "| 案子 | 事"), encoding="utf-8")
        st = profile.status(self.V)
        self.assertEqual((st["state"], st["error"]), ("error", "第 1 節第 1 列欄數不對（要 3 欄或 5 欄）"))
        self.md().unlink()
        self.assertEqual(profile.status(self.V)["state"], "missing")

    def test_status_reads_only(self):
        (self.V / "廠商").mkdir()
        self.md().write_text(self.md().read_text(encoding="utf-8") + "\n", encoding="utf-8")

        def stamps():
            return {p: p.stat().st_mtime_ns for p in (self.V / ".system").rglob("*") if p.is_file()}
        before = stamps()
        st = profile.status(self.V)
        self.assertEqual(st["unlisted"], ["廠商"])
        self.assertEqual(stamps(), before)

    def test_write_backs_up_and_sha_matches(self):
        t = turn.start(self.V, None, "")["turn"]
        data = profile.load(self.V)
        data["types"].append({"name": "工地", "category": "matter", "note": "", "source": "confirmed", "date": DAY})
        written = profile.write(self.V, data, t)
        self.assertEqual(written["md_sha256"], profile.md_sha256(self.V))
        self.assertEqual(profile.load(self.V)["md_sha256"], profile.md_sha256(self.V))
        self.assertEqual({e["path"]: e["action"] for e in turn.entries(self.V, t)},
                         {profile.PROFILE_POSIX: "modify", profile.MD_POSIX: "modify"})
        self.assertIn("| 工地 | 事 |  | 確認 | 2026-10-19 |", self.md().read_text(encoding="utf-8"))

    def test_card_types_for_todo(self):
        d = profile.defaults(DAY)
        got = profile.card_types_for_todo(d)
        self.assertEqual(got["card_types"][0], {"name": "報價", "sections": [
            {"name": s, "required": False, "hint": h} for s, h in profile.DEFAULT_SECTIONS]})
        self.assertEqual(got["exclusions"][0], {"kind": "身分證字號", "action": "kind_only"})


class AfterTestParse(unittest.TestCase):
    """測試後修正裁定 11、12：型別名太長、Windows 保留名。"""

    def test_windows_reserved_names(self):
        for name in ("CON", "con", "Prn", "AUX", "aux.txt", "NUL", "COM1", "com9", "LPT1", "lpt9.log"):
            with self.subTest(name=name):
                self.assertEqual(message(md_with(["| 工地 | 事 | x |", f"| {name} | 事 | x |"])),
                                 f"第 1 節第 2 列的型別名「{name}」是 Windows 保留名，不能當資料夾")
        got = profile.parse_md(md_with(["| COM10 | 事 | x |", "| CONSOLE | 事 | x |", "| 工地.aux | 事 | x |"]))
        self.assertEqual([t["name"] for t in got["types"]], ["COM10", "CONSOLE", "工地.aux"])

    def test_type_name_too_long(self):
        name = "長" * 41
        self.assertEqual(message(md_with([f"| {name} | 事 | x |"])),
                         "第 1 節第 1 列的型別名「長長長長長長長長長長…」太長（最多 40 字）")
        self.assertEqual(profile.parse_md(md_with(["| " + "長" * 40 + " | 事 | x |"]))["types"][0]["name"], "長" * 40)
        self.assertEqual(message(md_with(["| 工地很長 | 事 | x |"]), max_type_chars=3),
                         "第 1 節第 1 列的型別名「工地很長…」太長（最多 3 字）")

    def test_status_uses_config_max(self):
        tmp, V, _raw = make_inv_vault()
        try:
            (V / ".system" / "config.json").write_text('{"card_title_max_chars": 3}', encoding="utf-8")
            md = V / "系統" / "客戶設定檔.md"
            text = md.read_text(encoding="utf-8")
            md.write_text(text.replace("| 案子 | 事 |  | 推測 | 2026-10-19 |\n",
                                       "| 案子 | 事 |  | 推測 | 2026-10-19 |\n| 工地很長 | 事 | x |\n"), encoding="utf-8")
            st = profile.status(V)
            self.assertEqual((st["state"], st["error"]), ("error", "第 1 節第 2 列的型別名「工地很長…」太長（最多 3 字）"))
            (V / "廠商").mkdir()
            self.assertEqual(profile.status(V)["unlisted"], [], "讀不懂時不對帳（裁定 14）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AfterConfirmParse(unittest.TestCase):
    """確認後修正裁定 22：表格列之間夾了非表格行要報錯；表格以第一個空行結束，表後的字照舊忽略。"""

    def test_line_without_leading_pipe_inside_table(self):
        text = md_with(["| 工地 | 事 | x |", "| 客戶 | 對象 | y |"]).replace("| 客戶 | 對象 | y |", "客戶 | 對象 | y |")
        self.assertEqual(message(text), "第 1 節第 2 列不是表格列（少了開頭的 |，或多了一行字）")

    def test_extra_text_line_right_after_rows(self):
        text = md_with(cards=["| 報價 | 重點 |  | x |"]).replace("| 報價 | 重點 |  | x |\n", "| 報價 | 重點 |  | x |\n備註：還要加\n")
        self.assertEqual(message(text), "第 2 節第 2 列不是表格列（少了開頭的 |，或多了一行字）")

    def test_text_after_blank_line_ignored(self):
        text = md_with(["| 工地 | 事 | x |"]).replace("| 工地 | 事 | x |\n", "| 工地 | 事 | x |\n\n備註：這行不算\n| 亂 |\n")
        self.assertEqual([t["name"] for t in profile.parse_md(text)["types"]], ["工地"])


def with_rows(text, num, rows):
    """在第 num 節（四、五）的表格分隔列後面插入列。"""
    start = text.index(f"## {num}、")
    sep = text.index("\n|---", start) + 1
    at = text.index("\n", sep) + 1
    return text[:at] + "".join(r + "\n" for r in rows) + text[at:]


SECTION4_PROMPT = ("每類的綜合卡除了現況、紀事、推算，還要固定寫哪些段？一段一列；「必出現」打 ✓ 的段沒內容時寫「未知」。"
                   "新加的型別我會先填一組預設，你再改。")
SECTION5_PROMPT = "你的公司與你本人叫什麼？檔案裡出現這些名字時我知道是你，不會替你自己寫一張。別名用頓號分開。"


class SchemaTwo(unittest.TestCase):
    """實體與綜合卡第 10 點：第四節每類段落、第五節關於你；schema 1 在記憶體遷移；merge 補大類預設、刪光不補。"""

    def base(self, types=("| 案子 | 事 | 裝修案 |",)):
        return md_with(list(types))

    def test_render_has_five_sections_with_prompts_and_heads(self):
        text = profile.render_text(profile.defaults(DAY))
        self.assertIn("\n## 四、每類最想知道什麼\n" + SECTION4_PROMPT + "\n\n| 型別 | 段落 | 必出現 | 提示 | 來源 | 日期 |\n"
                      "|---|---|---|---|---|---|\n", text)
        self.assertIn("\n## 五、關於你\n" + SECTION5_PROMPT + "\n\n| 名稱 | 是什麼 | 別名 | 來源 | 日期 |\n|---|---|---|---|---|\n",
                      text)

    def test_parse_sections_four_and_five(self):
        text = with_rows(self.base(), "四", ["| 案子 | 時程 | ✓ | 開始與結束 |", "| 案子 | 金額 |  | 總價 | 訪談 | 2026-10-01 |"])
        text = with_rows(text, "五", ["| 林小姐室內設計 | 公司 | 林小姐、林設計 |", "| 林美華 | 本人 | 美華,Mia |"])
        got = profile.parse_md(text)
        self.assertEqual(got["synthesis_sections"], [
            {"type": "案子", "sections": [{"name": "時程", "required": True, "hint": "開始與結束"},
                                         {"name": "金額", "required": False, "hint": "總價"}],
             "source": None, "date": None}])
        self.assertEqual(got["about"], [
            {"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐", "林設計"], "source": None, "date": None},
            {"name": "林美華", "kind": "person", "aliases": ["美華", "Mia"], "source": None, "date": None}])

    def test_round_trip_with_new_sections(self):
        d = profile.defaults(DAY)
        d["types"] = [{"name": "案子", "category": "matter", "note": "", "source": "interview", "date": DAY}]
        d["synthesis_sections"] = profile.default_sections(d["types"], DAY)
        d["about"] = [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"], "source": "interview", "date": DAY}]
        text = profile.render_text(d)
        self.assertIn("| 案子 | 時程 |  | 開始、重要日期、預計結束 | 推測 | 2026-10-19 |", text)
        self.assertIn("| 林小姐室內設計 | 公司 | 林小姐 | 訪談 | 2026-10-19 |", text)
        self.assertEqual(profile.parse_md(text), {k: d[k] for k in profile.LISTS})

    def test_new_error_sentences(self):
        cases = [
            (with_rows(self.base(), "四", ["| 工地 | 時程 |  | x |"]), "第 4 節第 1 列的型別「工地」不在第 1 節"),
            (with_rows(self.base(), "五", ["| 林小姐室內設計 | 公司行號 |  |"]), "第 5 節第 1 列的是什麼要是 公司 或 本人"),
            (with_rows(self.base(), "五", ["| a/b | 公司 |  |"]), "第 5 節第 1 列的名稱「a/b」不能用（含不能用的字元）"),
            (with_rows(self.base(), "五", ["| 案子 | 公司 |  |"]), "第 5 節第 1 列的名稱「案子」不能用（跟第 1 節的型別名一樣）"),
            (with_rows(self.base(), "五", ["| 林小姐室內設計 | 公司 | 林小姐 |", "| 林美華 | 本人 | 林小姐 |"]),
             "第 5 節第 2 列跟第 1 列重複（林小姐）"),
        ]
        for text, want in cases:
            with self.subTest(want=want):
                self.assertEqual(message(text), want)

    def test_removed_type_rows_dropped_only_when_known(self):
        """實作回報裁定 1：舊 JSON 第一節有、這次拿掉的型別，第四節的列丟掉；從來不在第一節的照樣報錯。"""
        text = with_rows(self.base(), "四", ["| 工地 | 時程 |  | x |", "| 案子 | 金額 |  | y |"])
        self.assertEqual(message(text), "第 4 節第 1 列的型別「工地」不在第 1 節")
        got = profile.parse_md(text, known_types=["案子", "工地"])
        self.assertEqual([(s["type"], [x["name"] for x in s["sections"]]) for s in got["synthesis_sections"]],
                         [("案子", ["金額"])])
        self.assertEqual(message(text, known_types=["案子"]), "第 4 節第 1 列的型別「工地」不在第 1 節")

    def test_missing_new_sections_error_unless_legacy(self):
        text = self.base()
        old = text[:text.index("\n## 四、")] + "\n"
        self.assertEqual(message(old), "少了第 4 節（## 四、…）")
        got = profile.parse_md(old, legacy=True)
        self.assertEqual((got["synthesis_sections"], got["about"]), (None, None))
        self.assertEqual([t["name"] for t in got["types"]], ["案子"])

    def test_merge_adds_category_defaults_for_new_type_only(self):
        old = profile.defaults(DAY)
        old["types"] = [{"name": "案子", "category": "matter", "note": "", "source": "inferred", "date": DAY}]
        old["synthesis_sections"] = profile.default_sections(old["types"], DAY)
        new = profile.parse_md(self.base(["| 案子 | 事 |  |", "| 工地 | 事 | x |", "| 業主 | 對象 | y |", "| 規定 | 主題 | z |"]))
        merged = profile.merge(old, new, today="2026-10-20")
        rows = [(s["type"], x["name"], s["source"], s["date"]) for s in merged["synthesis_sections"] for x in s["sections"]]
        self.assertEqual(rows, [("工地", "時程", "inferred", "2026-10-20"), ("工地", "金額", "inferred", "2026-10-20"),
                                ("業主", "聯絡與往來", "inferred", "2026-10-20"), ("業主", "偏好與禁忌", "inferred", "2026-10-20")])
        self.assertNotIn("案子", {s["type"] for s in merged["synthesis_sections"]}, "第四節刪光的型別不補回")

    def test_merge_keeps_given_rows_for_new_type(self):
        old = profile.defaults(DAY)
        new = profile.parse_md(with_rows(self.base(["| 工地 | 事 | x |"]), "四", ["| 工地 | 預算 |  | 錢 |"]))
        merged = profile.merge(old, new, today="2026-10-20", first=True)
        self.assertEqual([x["name"] for s in merged["synthesis_sections"] for x in s["sections"]], ["預算"])

    def test_merge_about_stamped(self):
        old = profile.defaults(DAY)
        new = profile.parse_md(with_rows(self.base(), "五", ["| 林小姐室內設計 | 公司 | 林小姐 |"]))
        merged = profile.merge(old, new, today="2026-10-20", first=True)
        self.assertEqual(merged["about"], [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"],
                                            "source": "interview", "date": "2026-10-20"}])

    def test_diff_and_summary(self):
        old = profile.defaults(DAY)
        old["types"] = [{"name": "案子", "category": "matter", "note": "裝修案", "source": "inferred", "date": DAY}]
        text = with_rows(self.base(), "四", ["| 案子 | 時程 |  | x |", "| 案子 | 金額 |  | y |"])
        new = profile.parse_md(with_rows(text, "五", ["| 林小姐室內設計 | 公司 | 林小姐 |"]))
        d = profile.diff(old, new)
        self.assertEqual(d["synthesis_sections"]["added"], [["案子", "時程"], ["案子", "金額"]])
        self.assertEqual(d["about"]["added"], ["林小姐室內設計"])
        self.assertEqual(profile.summary_full(d), "每類段落 +2 -0 ~0、關於你 +1 -0 ~0")
        self.assertEqual(profile.summary_short(d), "每類段落 +2、關於你 +1")

    def test_card_types_for_todo_has_types_sections_about(self):
        d = profile.defaults(DAY)
        d["types"] = [{"name": "案子", "category": "matter", "note": "", "source": "inferred", "date": DAY}]
        d["synthesis_sections"] = profile.default_sections(d["types"], DAY)
        d["about"] = [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"], "source": "interview", "date": DAY}]
        got = profile.card_types_for_todo(d)
        self.assertEqual(got["types"], [{"name": "案子", "category": "matter"}])
        self.assertEqual(got["synthesis_sections"], [{"type": "案子", "sections": [
            {"name": "時程", "required": False, "hint": "開始、重要日期、預計結束"},
            {"name": "金額", "required": False, "hint": "報價、合約總價、付款期別與已付"}]}])
        self.assertEqual(got["about"], [{"name": "林小姐室內設計", "kind": "company", "aliases": ["林小姐"]}])

    def test_category_sections(self):
        self.assertEqual({k: [n for n, _h in v] for k, v in profile.CATEGORY_SECTIONS.items()},
                         {"party": ["聯絡與往來", "偏好與禁忌"], "matter": ["時程", "金額"], "asset": ["規格", "期限"],
                          "topic": []})


class SchemaOneMigration(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, _raw = make_inv_vault()
        shutil.copy2(PROFILE / "schema1.json", self.V / ".system" / "profile.json")
        shutil.copy2(PROFILE / "schema1.md", self.V / "系統" / "客戶設定檔.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_migrates_in_memory_without_writing(self):
        p = self.V / ".system" / "profile.json"
        before = p.read_bytes()
        data = profile.load(self.V)
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual([(s["type"], [x["name"] for x in s["sections"]], s["source"]) for s in data["synthesis_sections"]],
                         [("案子", ["時程", "金額"], "inferred")])
        self.assertEqual(data["about"], [])
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(profile.stored_schema(self.V), 1)

    def test_unchanged_old_md_is_ok(self):
        self.assertEqual(profile.status(self.V)["state"], "ok")

    def test_changed_old_md_parses_as_legacy(self):
        md = self.V / "系統" / "客戶設定檔.md"
        md.write_text(md.read_text(encoding="utf-8").replace("| 案子 | 事 |  | 推測 | 2026-10-19 |\n",
                                                             "| 案子 | 事 |  | 推測 | 2026-10-19 |\n| 工地 | 事 | x |\n"),
                      encoding="utf-8", newline="\n")
        st = profile.status(self.V)
        self.assertEqual(st["state"], "changed", st["error"])
        self.assertEqual(st["diff"]["types"]["added"], ["工地"])
        self.assertEqual(st["diff"]["synthesis_sections"], {"added": [], "removed": [], "changed": []})
        self.assertEqual(st["new"]["about"], [])



class AboutListChars(unittest.TestCase):
    """測試後修正裁定 10：第五節的名稱與別名也擋 , [ ]（併進「含不能用的字元」）。"""

    def test_name_and_alias(self):
        base = md_with(["| 案子 | 事 | x |"])
        self.assertEqual(message(with_rows(base, "五", ["| Lee[Inc] | 公司 |  |"])),
                         "第 5 節第 1 列的名稱「Lee[Inc]」不能用（含不能用的字元）")
        self.assertEqual(message(with_rows(base, "五", ["| 林小姐室內設計 | 公司 | 林[小姐] |"])),
                         "第 5 節第 1 列的別名「林[小姐]」不能用（含不能用的字元）")


if __name__ == "__main__":
    unittest.main()
