"""argparse 主程式。子命令註冊表在 COMMANDS；新子命令加一行，--help 自動列出。
模組有 add_arguments(sub) 就讓它自己加參數（子子命令、--path 之類），沒有就只給 --json。
每一層 parser 都 allow_abbrev=False（旗標要寫全名）。
全域 --turn <回合> --agent <agent_id>（放在子命令前、--help 不列）：守門替 card-maker 的 kb 呼叫插的（D-195）；
有 --turn 時這次呼叫的備份都進那個回合、算對話裡（不寫程式段）；回合不存在、或不是這個對話（--agent 記的回合、
這個 session 的目前回合或 job 回合）的 exit 3（第一輪修 S4）。--session <sid>：守門替主對話插的，每個單一 kb 呼叫都插（帶 --turn 與 selftest 除外）。
--via obsidian（#22，D-255）：只有 Obsidian 外掛帶，這次呼叫永遠開自己的回合（不讀 current.json、不併進對話）；值只收 obsidian，其他 exit 3。
"""
import argparse
import importlib
import os
import sys

NO_SUCH_TURN = "回合不存在：{}"
NOT_THIS_TURN = "這個回合不是這個對話的：{}"
BAD_VIA = "--via 只收 obsidian"

# 名稱: (模組, 一句說明)
COMMANDS = {
    "version": ("kb.version", "印 plugin 名稱與版號"),
    "guard": ("kb.guard_cli", "守門：decide 模擬一次判斷，查「為什麼擋」"),
    "versions": ("kb.versions", "回合：list 列最近的改動，revert 退回最近一次，delete-outputs 刪已退回回合留著的成品，clean 清放太久的回合備份與快照"),
    "turn": ("kb.turn_cli", "目前回合：current 看狀態，approve 解除綜合卡張數上限，note 給這一句的「做了」，redact 換掉這一句的「問」"),
    "init": ("kb.init", "建總資料夾的五區骨架；--check 只看會建什麼"),
    "folders": ("kb.folders", "來源：add 登記一個資料夾，exclude 排除裡面的夾或檔，remove 拿掉一個來源，include 取消排除，list 列出登記表，"
                           "find 找夾，relocate 資料夾搬家，purge 清不想讓 AI 看到的夾的殘留"),
    "source-edit": ("kb.source_edit", "原檔：move 搬一份檔或一個夾，rename 改名，trash 丟資源回收筒，where 這個實體的檔放在哪，open 開檔案總管"),
    "config": ("kb.config_cli", "門檻與頻率：show 列可以調的、set 改一個、reset 改回預設"),
    "journal": ("kb.journal_cli", "工作日誌：recent 印最近幾段"),
    "inventory": ("kb.inventory_cli", "清冊：scan 掃描來源與總資料夾，把變動套進清冊"),
    "changes": ("kb.changes", "變動：跟清冊上次掃描比，列新增、修改、搬動、不見；只看不改"),
    "convert": ("kb.convert", "抽字：把一份原檔轉成純文字暫存檔給製卡者讀；PDF、圖片指給 Read"),
    "cards": ("kb.cards", "檔案卡：todo 列該做的卡，sync 跟著原檔搬卡，drop 拿掉原檔已刪的卡，index 重寫總目錄，"
                          "skip 記成不整理，redo 卡讀錯了標成要重做，mount 卡掛錯實體改掛，list 列卡（只讀），"
                          "mode 會一直改的檔記成指路卡或完整卡，seen 指路卡的改動看過了"),
    "archive": ("kb.archive_cli", "封存：把過時的檔或實體收起來（還找得到，不再整理、不再問）；--undo 拿掉封存"),
    "pending": ("kb.pending_cli", "待處理：list 列等你決定的事，add 記一件兩份寫法不同的，resolve 記下怎麼處理，dismiss 記成不用管，"
                                  "set-aside 先放著，back 拿回來"),
    "profile": ("kb.profile_cli", "客戶設定檔：check 看改了什麼，apply 套用並建型別夾，set people 記幾個人用"),
    "who": ("kb.who", "這台是誰：多人共用時看與記這台電腦的人名、幾個人用、團隊成員；set <名字> 記下"),
    "entity": ("kb.entity", "實體：add 登記，confirm 確認名稱，accept AI 判斷有把握直接登記，rename 改名，merge 合併，remove 移除，rebuild 重寫綜合卡，"
                            "clean 清放錯的綜合卡，list 列出，render 重寫綜合卡快取，progress 事類的進行中、結案、擱置，"
                            "suggest-close 記一條結案待問，alias 加或拿掉別名"),
    "statement": ("kb.statement_cli", "客戶口述：add 記一條（--replaces 改舊的），withdraw 收回，restore 恢復被取代的那條，list 列出"),
    "ruling": ("kb.ruling_cli", "個案裁示：add 記一條（口述為準、不採用、可以讀），remove 收回，recheck 原檔新版看過了照舊，"
                               "decline 檔名像密碼的這份不要讀，list 列出"),
    "unlock": ("kb.unlock", "密碼：save 存進這台電腦的憑證管理員，get 取出，forget 忘掉，list 列名字，"
                            "authorize 這一種的 PDF 用這組開，revoke 不再自動開"),
    "money": ("kb.money_cli", "帳：add 記一筆，settle 收付了，edit 改，cancel／reopen，attach 補出處，merge 兩筆是同一筆，count 盤點，"
              "unsure 看不出是不是她的錢，scanned 卡的錢抽過了，list、candidates、sum、stock、backfill 只讀"),
    "agenda": ("kb.agenda_cli", "日程：add 加一條，edit 改，done／cancel／reopen 改狀態，recheck 原檔新版看過了，list 列出，render 重寫日程.md，export 把日程放到指定資料夾"),
    "asks": ("kb.asks_cli", "要問客戶的事：列出跟這一句有關的與有時間性的（--about 名稱、--all 全部），其餘報件數；set-aside 先放著"),
    "lookup": ("kb.lookup_cli", "查一個實體：只讀，一次給齊綜合卡、檔案卡、口述、裁示、推算、日程與還沒整理的份數"),
    "finding": ("kb.finding_cli", "推算：add 記一筆（--replaces 改舊的），list 列出"),
    "tally": ("kb.tally_cli", "加總：只讀，收每一筆（卡、數字、位置），回逐筆表、筆數、合計、平均"),
    "office": ("kb.office_cli", "做文件的工具：check 看這台有什麼，setup 裝工具，run 跑工作區的腳本，copy 拿原檔副本，"
                                "unpack／pack 解開與打包，pdf 轉 PDF，recalc 重算 Excel，render 轉每頁的圖"),
    "build": ("kb.build", "建置：status 做到哪，start 開始，env 檢查這台電腦，set 記幾個人用與舊 wiki，facts 盤點，"
                          "done 這一步做完，redo 回到做過的一步，adopt 收舊 llm-wiki，report 交付報告"),
    "output": ("kb.output_cli", "成品：save 把做好的檔放進知識庫（沒給 --to 落暫存區；登記、退回拿得掉），link 登記線上文件，drop-draft 收掉草稿，"
                               "links 列出存成連結的線上文件"),
    "intake": ("kb.intake_cli", "收件：把拖進對話的檔、程式交來的檔存進暫存區或指定的夾（原檔不動）；--pasted 存這一則（或上一則）貼的圖或文字；"
                                "--from 記來源，加 --name 定期同步同一份"),
    "backup": ("kb.backup_cli", "備份：把整個知識庫備份成一個 zip；--to 指定放哪（第一次要給），--check 只看不做"),
    "restore": ("kb.restore_cli", "還原：list 列備份與快照，check 演練會回到什麼樣子，apply 還原（先拍快照）"),
    "upgrade": ("kb.upgrade_cli", "升級：--check 查有沒有新版，直接跑是升級，--finish 換版後的新對話轉格式，--downgrade 退回舊版，"
                                  "skeleton 骨架檔保留或換新"),
    "lint": ("kb.lint_cli", "健檢：查整個知識庫、能修的修掉、要你決定的放進待處理、寫這個月的報告；--background 開場背景跑，"
                            "handedit 列你手改程式產生的檔的差異"),
    "moved": ("kb.moved_cli", "換了位置或換了電腦：補來源位置與卡的開原檔連結，列要補的；--check 只看不改"),
    "refresh": ("kb.refresh", "git 同步之後對帳：重畫日程與待處理等頁、比 pull 進來的原檔、收拾合併衝突、記太大的檔；不是 git 知識庫什麼都不做"),
    "home": ("kb.home_cli", "Obsidian 首頁：--json 一次給齊首頁要的（時間軸、待辦、要你確認的、最近），只讀，給外掛用"),
    "renders": ("kb.renders_cli", "程式產生的頁（日程、待處理、裁示）：apply 照你在頁面上直接改的做，drop 把「你加的」裡的一筆拿掉"),
    "obsidian": ("kb.obsidian_cli", "Obsidian：status 裝了沒、登記了沒、外掛放了沒，open 替她把知識庫加進 Obsidian 並打開"),
    "find": ("kb.find_cli", "找檔：把一串字拆成實體、卡型、標籤、時間詞和字，找卡與綜合卡；只讀，不讀原檔"),
    "selftest": ("kb.selftest_cli", "自我檢查：這份外掛在這台電腦跑不跑得起來（模組、版本、每個 hook、背景升級），用暫存的小知識庫，不碰你的"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="wiki-with-willy 的唯一指令入口。",
        allow_abbrev=False,
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    parser.add_argument("--turn", help=argparse.SUPPRESS)
    parser.add_argument("--agent", help=argparse.SUPPRESS)
    parser.add_argument("--session", help=argparse.SUPPRESS)
    parser.add_argument("--via", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", metavar="<子命令>", required=True)
    for name, (module_name, doc) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=doc, description=doc)
        module = importlib.import_module(module_name)
        if hasattr(module, "add_arguments"):
            module.add_arguments(sub)
        else:
            sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    _no_abbrev(parser)
    return parser


def _no_abbrev(parser):
    """主 parser 與每一層子 parser 都不收縮寫（--tu= 不會被當成 --turn，card-maker 第二輪修）。"""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _no_abbrev(sub)


def _touch(vault, agent_id):
    """子代理跑 kb：它認領中的批次記 last_seen（B 段第 6 步，別的對話靠這個分辨還有沒有人在做）；寫不了不擋這次呼叫。"""
    from kb._lib import batches, ledger
    try:
        batches.touch(vault, agent_id)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"批次 last_seen 沒記到：{type(e).__name__}: {e}", source="kb")


def main(argv=None):
    raw = sys.argv[1:] if argv is None else list(argv)
    if raw[:1] == ["selftest"] and all(a == "--json" for a in raw[1:]):
        # 不建整個 parser（那會先 import 每個子命令模組）：某個模組 import 壞了，自我檢查也要回 step import（換版前先驗新版 裁定 1）
        from kb import selftest_cli
        return selftest_cli.run(argparse.Namespace(json=bool(raw[1:])))
    args = build_parser().parse_args(argv)
    if args.session is not None:
        from kb._lib import turn
        turn.SESSION = args.session
    if args.via is not None:
        from kb._lib import turn
        if args.via != "obsidian":
            print(BAD_VIA)
            return 3
        turn.VIA = args.via
    if args.turn is not None:
        from kb import guard
        from kb._lib import turn
        vault = guard.find_vault(os.getcwd())
        if vault is None or not turn.exists(vault, args.turn):
            print(NO_SUCH_TURN.format(args.turn))
            return 3
        if not turn.owned(vault, args.turn, args.agent):
            print(NOT_THIS_TURN.format(args.turn))
            return 3
        turn.OVERRIDE = args.turn
        if args.agent:
            _touch(vault, args.agent)
    module_name, _doc = COMMANDS[args.command]
    module = importlib.import_module(module_name)
    try:
        return module.run(args)
    except Exception as e:  # noqa: BLE001
        line = newer_format_line(e)
        if line is None:
            raise
        print(line)
        return 3


NEWER_FORMAT = "同事已經用 {app} 記過東西了，這台是 {mine}，要升級才能繼續記。要現在升嗎？升完要開一個新對話。"  # 多人共用 2 新定 5


def newer_format_line(e):
    """流水檔有比這版新的格式的行、寫入被擋（streams.NewerFormat，可能被包在 __cause__／__context__ 裡）：回給客戶的那句；不是回 None。"""
    from kb._lib import paths, streams
    seen = 0
    while e is not None and seen < 5:
        if isinstance(e, streams.NewerFormat):
            mine = (paths.read_manifest() or {}).get("version") or "?"
            return NEWER_FORMAT.format(app=e.app or "新版", mine=mine)
        e, seen = e.__cause__ or e.__context__, seen + 1
    return None
