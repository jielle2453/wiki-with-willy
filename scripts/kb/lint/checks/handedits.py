"""清單 4（D-223）：程式產生的 md 被手改。偵測在重新產生的那一刻（_lib/renders）；這裡只數還沒處理完的（待處理 handedit）
與這個月總目錄、工作日誌被手改後留下她那份幾次（報告寫一句）。"""
from kb._lib import cards, pending, renders
from kb.lint import runner

NAME = "手改的檔"


def run(ctx):
    open_ = sum(1 for i in pending.load(ctx.vault)["items"] if i.get("kind") == "handedit" and i.get("status") == "open")
    month = ctx.today[:7]
    saved = [s for s in renders.saved(ctx.vault) if str(s.get("at") or "")[:7] == month]
    index = sum(1 for s in saved if s.get("file") == cards.INDEX_POSIX)
    journal = sum(1 for s in saved if str(s.get("file") or "").startswith("系統/工作日誌/"))
    return [runner.finding("handedits", "number", "handedits", "",
                           data={"open": open_, "index_saved": index, "journal_saved": journal})]
