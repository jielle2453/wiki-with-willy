"""被同步（git）追蹤的 willy 本機與衍生檔（#78，D-554 (3)）：有就一件待處理 sync_untrack，問她要不要停止追蹤。
問句與計數跟 refresh 每天的偵測同一套（_lib/refresh.untrack_summary）；每台各自查（停止追蹤之前每台都可能有）。"""
from kb._lib import refresh
from kb.lint import runner

SCOPE = "local"
NAME = "同步追蹤"


def run(ctx):
    got = refresh.untrack_summary(ctx.vault)
    if not got["n"]:
        return []
    return [runner.finding("sync_untrack", "decide", refresh.UNTRACK_KEY, refresh.ask_text(got), data=refresh.ask_data(got))]
