"""argparse 主程式。子命令註冊表在 COMMANDS；新子命令加一行，--help 自動列出。
模組有 add_arguments(sub) 就讓它自己加參數（子子命令、--path 之類），沒有就只給 --json。
全域 --turn <回合> --agent <agent_id>（放在子命令前、--help 不列）：守門替 card-maker 的 kb 呼叫插的（D-195）；
有 --turn 時這次呼叫的備份都進那個回合、算對話裡（不寫程式段）；回合不存在、或不是這個對話（--agent 記的回合、
這個 session 的目前回合或 job 回合）的 exit 3（第一輪修 S4）。--session <sid>：守門替主對話的 cards dispatch／status／stop 插的。
"""
import argparse
import importlib
import os

NO_SUCH_TURN = "回合不存在：{}"
NOT_THIS_TURN = "這個回合不是這個對話的：{}"

# 名稱: (模組, 一句說明)
COMMANDS = {
    "version": ("kb.version", "印 plugin 名稱與版號"),
    "guard": ("kb.guard_cli", "守門：decide 模擬一次判斷，查「為什麼擋」"),
    "versions": ("kb.versions", "回合：list 列最近的改動，revert 退回最近一次，delete-outputs 刪已退回回合留著的成品"),
    "turn": ("kb.turn_cli", "目前回合：current 看狀態，approve 解除綜合卡張數上限，note 給這一句的「做了」，redact 換掉這一句的「問」"),
    "init": ("kb.init", "建總資料夾的五區骨架；--check 只看會建什麼"),
    "folders": ("kb.folders", "來源：add 登記一個資料夾，exclude 排除裡面的夾或檔，remove 拿掉一個來源，include 取消排除，list 列出登記表，"
                           "find 找夾，relocate 資料夾搬家，purge 清不想讓 AI 看到的夾的殘留"),
    "source-edit": ("kb.source_edit", "原檔：move 搬一份檔或一個夾，rename 改名，trash 丟資源回收筒，where 這個實體的檔放在哪，open 開檔案總管"),
    "journal": ("kb.journal_cli", "工作日誌：recent 印最近幾段，render 重寫月檔"),
    "inventory": ("kb.inventory_cli", "清冊：scan 掃描來源與總資料夾，把變動套進清冊"),
    "changes": ("kb.changes", "變動：跟清冊上次掃描比，列新增、修改、搬動、不見；只看不改"),
    "convert": ("kb.convert", "抽字：把一份原檔轉成純文字暫存檔給製卡者讀；PDF、圖片指給 Read"),
    "cards": ("kb.cards", "檔案卡：todo 列該做的卡，sync 跟著原檔搬卡，drop 拿掉原檔已刪的卡，index 重寫總目錄，"
                          "skip 記成不整理，redo 卡讀錯了標成要重做，mount 卡掛錯實體改掛，list 列卡（只讀）"),
    "pending": ("kb.pending_cli", "待處理：list 列等你決定的事，add 記一件兩份寫法不同的，resolve 記下怎麼處理，dismiss 記成不用管"),
    "profile": ("kb.profile_cli", "客戶設定檔：check 看改了什麼，apply 套用並建型別夾"),
    "entity": ("kb.entity", "實體：add 登記，confirm 確認名稱，rename 改名，merge 合併，remove 移除，rebuild 重寫綜合卡，"
                            "clean 清放錯的綜合卡，list 列出，render 重寫綜合卡快取，progress 事類的進行中、結案、擱置，"
                            "suggest-close 記一條結案待問，alias 加或拿掉別名"),
    "statement": ("kb.statement_cli", "客戶口述：add 記一條（--replaces 改舊的），withdraw 收回，list 列出"),
    "ruling": ("kb.ruling_cli", "個案裁示：add 記一條（口述為準、不採用、可以讀），remove 收回，recheck 原檔新版看過了照舊，"
                               "decline 檔名像密碼的這份不要讀，list 列出"),
    "unlock": ("kb.unlock", "密碼：save 存進這台電腦的憑證管理員，get 取出，forget 忘掉，list 列名字，"
                            "authorize 這一種的 PDF 用這組開，revoke 不再自動開"),
    "agenda": ("kb.agenda_cli", "日程：add 加一條，edit 改，done／cancel／reopen 改狀態，recheck 原檔新版看過了，list 列出，render 重寫日程.md"),
    "asks": ("kb.asks_cli", "要問客戶的事：只讀，列出 asks 與 tell（逾期、日程有出入、結案、名字確認…）"),
    "lookup": ("kb.lookup_cli", "查一個實體：只讀，一次給齊綜合卡、檔案卡、口述、裁示、推算、日程與還沒整理的份數"),
    "finding": ("kb.finding_cli", "推算：add 記一筆（--replaces 改舊的），withdraw 收回，list 列出"),
    "tally": ("kb.tally_cli", "加總：只讀，收每一筆（卡、數字、位置），回逐筆表、筆數、合計、平均"),
    "office": ("kb.office_cli", "做文件的工具：check 看這台有什麼，setup 裝工具，run 跑工作區的腳本，copy 拿原檔副本，"
                                "unpack／pack 解開與打包，pdf 轉 PDF，recalc 重算 Excel，render 轉每頁的圖"),
    "output": ("kb.output_cli", "成品：save 把做好的檔放進 AI 產出（登記、退回拿得掉），link 登記線上文件，drop-draft 收掉草稿"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="wiki-with-willy 的唯一指令入口。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    parser.add_argument("--turn", help=argparse.SUPPRESS)
    parser.add_argument("--agent", help=argparse.SUPPRESS)
    parser.add_argument("--session", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", metavar="<子命令>", required=True)
    for name, (module_name, doc) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=doc, description=doc)
        module = importlib.import_module(module_name)
        if hasattr(module, "add_arguments"):
            module.add_arguments(sub)
        else:
            sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.session is not None:
        from kb._lib import turn
        turn.SESSION = args.session
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
    module_name, _doc = COMMANDS[args.command]
    module = importlib.import_module(module_name)
    return module.run(args)
