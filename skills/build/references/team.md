# 多人共用：給服務人員的設定

只有客戶說「好幾個人一起用」才讀這份（D-302）。AI 不照這份替客戶操作 git；這份是給服務人員（或懂的人）照著做的。代做的範圍與收費另外談，這裡只寫內容。

## 資料放哪

- 原檔與暫存區：團隊雲端硬碟（OneDrive、Google Drive 或 NAS 夾），由雲端自己同步、自己處理兩人同時改（雲端會留「檔名-電腦名」這類衝突副本，AI 看到會問用哪一版）。
- git 主機：只放 willy 產的文字（卡、口述、日程、設定、紀錄）。

## git 放哪

- 沒有現成的：用 GitHub private repo。
- 已有自架的（Gitea、GitLab、NAS 上的 git 服務）：照用。
- 不建議把網路磁碟上的裸 repo 當遠端：多台同時寫的時候鎖不可靠，容易壞。

## 步驟

1. 建 repo（private），邀請所有成員。
2. 每台：clone 到知識庫的位置；裝 Claude 桌面版與 willy；登入 git（讓 Obsidian git 外掛能背景 push／pull，不跳帳密視窗）。
3. 每台裝 Obsidian git 外掛，建議設定：
   - 開啟時先 pull。
   - **停止改動幾分鐘後**才自動 commit 並同步（不用固定每 5 分鐘，免得把 AI 做到一半的卡推給同事；設定名字以外掛當時的介面為準）。
   - 合併方式選 **merge**，不要用 rebase：willy 不收拾 rebase 做到一半的狀態，遇到會在對話裡說「同步卡住了，請聯絡服務人員」。
4. 每台把團隊雲端夾對上：`kb folders locate <別名> <這台的路徑>`，暫存區也一樣（`kb folders locate 暫存區 <路徑>`）。
5. 每台第一次開對話，Claude 會問「這台電腦是誰在用」，答了存在這台。

## 單人轉多人

把知識庫推上 git、`kb profile set people <人數>`、其他人各自 clone，再照上面第 3～5 步。

## 新同事

新 clone 第一次開，Claude 會問是誰、說還在認檔案（背景算指紋），不會重做卡，也不會整份下載只在雲端的檔。

## 出狀況時

- 「同步卡住了（rebase 到一半）」：到那台把 rebase 做完或中止（`git rebase --abort`），再開對話。
- 太大的檔（超過 95 MB）：willy 會把它排除在 git 外（`.git/info/exclude`），並問要不要搬到團隊雲端夾。
- `willy/claim/*`、`willy/lint/*`、`willy/upgrade/*` 這些標籤是 willy 用來「只讓一台做」的記號，不要手動刪；超過 7 天的認領標籤健檢會自己清。
