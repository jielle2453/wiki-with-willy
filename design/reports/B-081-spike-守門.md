日期：2026-10-21
給誰看：設計 session（llm-wiki-j-cb），寫 wiki-with-willy 的 produce spec
何時更新：重跑第 9 題時

# B-081 第 9 題：有 plugin 時守門擋不擋

環境：Claude Code CLI（Opus 5.5），工作目錄 `produce-spike\知識庫\`，wiki-with-willy plugin 已載入（開場段「【wiki-with-willy】」有出現，核心規則 R-01～R-14 有帶入）。權限模式是 **auto mode**，所以沒有「跳出來問使用者允許」的對話框；該問的地方改由 auto mode 分類器直接拒絕（下表「分類器」那幾列）。使用者畫面上有沒有跳東西，我看不到，**未確認**。

## 結果

- (1) 進度報告 Word：**成功**。`知識庫\AI 產出\內湖李進度報告.docx`（11,547 bytes），轉 PDF 看版面正常（微軟正黑體、表格、頁尾「第 1 頁，共 1 頁」）。
- (2) 合約追蹤修訂：**成功**，但中途被擋一次、經使用者決定改走另一條路（見擋 2）。`知識庫\AI 產出\信義王室內裝修工程合約（AI 修改）.docx`（36,966 bytes）。自己寫的檢查：`w:ins` 2、`w:del` 2、作者「AI 助理」、表格 1→1、段落 25→25、接受修訂後文字＝原文把兩處「後 15 日內」換成「後 30 日內」、頁尾與其他 17 個檔內部件逐位元相同。技能的 `validate.py` 被擋，沒跑成。
- `材料\` 原檔沒動（36,799 bytes、20:56）。

## 被擋紀錄（照時間順序）

| # | 擋的是誰 | 指令原文 | 擋的理由（原文） | 後來怎麼做 |
|---|---|---|---|---|
| 1 | plugin 守門（PreToolUse:Bash） | `cat ".../produce-spike/B-081-任務.md"; echo ----; ls ".../produce-spike/" ".../produce-spike/材料" ".../produce-spike/工作/腳本"` | 「這個範圍裡有檔名像密碼或金鑰的檔（pylib/certifi/cacert.pem），客戶沒說可以讀；請縮到不含它的子資料夾或指定單一檔。」 | 照理由縮小：分開讀任務檔、只 ls `工作/腳本` 與 `材料`，都過。註：`ls` 不遞迴，但守門把 `produce-spike\` 整個範圍當成會被讀，碰到 `pylib\certifi\cacert.pem`（CA 憑證，不是密碼）就擋 |
| 2 | plugin 守門（Z9 readonly_source） | `mkdir -p ".../工作/kb9" ; cp ".../材料/信義王室內裝修工程合約.docx" ".../工作/kb9/合約複本.docx" ; python -m zipfile -e ".../工作/kb9/合約複本.docx" ".../工作/kb9/unpacked"` | 「客戶原檔只讀。要改內容我用檔案工具另存一份」 | 照 R-12 先停。用 `kb guard decide` 模擬：單獨 `cp 原檔 工作\…` → deny Z9；`python -m zipfile -e 原檔 工作\kb9\unpacked`（只讀原檔）→ `pass`；`Read` 原檔 → allow。問使用者，使用者選「改用 python 讀原檔解開」，之後順利。註：`cp` 的原檔只是來源、不是目的地，守門仍當成寫原檔；而理由句說的「用檔案工具另存」對二進位 .docx 做不到（Write 只能寫文字） |
| 3 | Claude Code auto mode 分類器 | `PYTHONPATH=".../pylib" python "C:/Users/Jie/.claude/skills/synced/…/docx/scripts/merge_runs.py" ".../工作/kb9/unpacked"` | 「Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Auto-Mode Bypass].」 | 不繞路。查 XML 發現兩處「15 日內」本來就在完整的 run 裡，這步可省，直接跑自己的 `工作\腳本\redline_contract.py`（過） |
| 4 | Claude Code auto mode 分類器 | `PYTHONPATH=".../pylib" python ".../docx/scripts/office/validate.py" ".../知識庫/AI 產出/信義王室內裝修工程合約（AI 修改）.docx" --original ".../材料/信義王室內裝修工程合約.docx" --author "AI 助理"` | 同上「[Auto-Mode Bypass]」 | 不繞路（沒把技能腳本複製出來跑）。改寫自己的唯讀檢查（見擋 5） |
| 5 | plugin 守門（PreToolUse:Bash） | `python - <<'EOF'` …（唯讀：用 zipfile 打開原檔與修訂檔，數 `w:ins`／`w:del`、比對頁尾）… `EOF` | 「總資料夾第一層與型別夾根不直接放檔；請寫進 AI 產出或對應的綜合卡。」 | 把同一段程式用 Write 存成 `工作\腳本\check_redline.py` 再跑，過。註：這段程式不寫任何檔；推測守門把 heredoc 的 `<<` 當成寫檔重導向、目的地落在目前目錄（知識庫第一層），**推測，沒看守門原始碼確認** |

分類器擋 3、4 的推測原因：發生在擋 2 之後不久，且是從技能資料夾跑腳本、對象是剛被守門擋過的檔，分類器可能判成「換方法繞過守門」。第 1 題（桌面版）同樣的腳本都跑得動。**推測**。

## 沒擋、也沒問的（值得注意）

| 動作 | 指令 | 結果 |
|---|---|---|
| node 直接寫 AI 產出 | `NODE_PATH=".../node/node_modules" node ".../工作/腳本/make_report_docx_kb.js" ".../知識庫/AI 產出/內湖李進度報告.docx"` | 過 |
| python 讀原檔＋寫 AI 產出 | `python ".../工作/腳本/repack.py" ".../工作/kb9/unpacked" ".../材料/信義王…合約.docx" ".../知識庫/AI 產出/信義王…（AI 修改）.docx"` | 過（原檔路徑當參數也沒擋） |
| python 讀原檔解開 | `python -m zipfile -e ".../材料/信義王…合約.docx" ".../工作/kb9/unpacked"` | 過 |
| sed 重導向寫腳本到工作夾 | `sed … make_report_docx.js > ".../工作/腳本/make_report_docx_kb.js"` | 過 |
| soffice 讀 AI 產出、PDF 寫到工作夾 | `soffice.exe --headless --norestore -env:UserInstallation=… --convert-to pdf --outdir ".../工作/pdf_kb" ".../AI 產出/內湖李進度報告.docx"` | 過 |
| pypdfium2 轉圖 | `python ".../工作/腳本/pdf_to_png.py" …` | 過 |
| Write 寫知識庫外 | `工作\腳本\check_redline.py` | 過 |
| kb 程式 | `kb guard decide`、`kb turn current`、`kb turn note` | 過，不用按允許 |

## 給 spec 的觀察

1. **AI 產出用 node／python 寫進去，程式沒有備份也沒有登記。** `kb turn current` 顯示「已備份 0 檔」；在 `.system\`、`系統\` 找這兩個檔名，只找得到我自己用 `kb turn note` 寫的那句話，清冊沒有。R-01 說 AI 產出「用檔案工具寫，程式會先備份、登記」，但 Word／Excel／PPT 是二進位檔，Write 寫不了，實際上一定是 Bash 跑腳本寫。produce 需要一條登記路（例如 kb 子命令登記某個 AI 產出檔，或守門／PostToolUse 看 Bash 寫進 AI 產出的檔）。
2. **「客戶原檔只讀」的理由句叫模型用檔案工具另存，對 Office 檔不可行。** 而且 `cp 原檔 別處` 擋、`python 讀原檔寫別處` 放，同一件事兩種寫法結果不同，模型很容易被 R-12 卡住或看起來像在繞路。建議 spec 明寫 produce 改既有檔的合法路（例如 kb 子命令「複製原檔到工作區」，或守門放行「原檔只當來源」的 cp）。
3. **技能腳本放哪**：這次全部放 `produce-spike\工作\腳本\`（知識庫外），輸出才寫進 `AI 產出\`。技能自己的腳本在 `C:\Users\Jie\.claude\skills\synced\…\docx\scripts\`，在 auto mode 下被分類器擋了兩次。正式版若要用技能的 `validate.py`、`merge_runs.py`，最好有固定、可允許的位置或由 kb 程式包一層。
4. **heredoc 被當成寫檔**（擋 5，推測）：`python - <<'EOF'` 是很常見的唯讀檢查寫法，在知識庫目錄下會被擋。
5. **範圍掃描碰到 `cacert.pem` 就擋**（擋 1）：非遞迴的 `ls` 也被擋；`.pem` 一律當密碼檔，python 套件夾裡的憑證包會常常誤中。

## 產生的檔

- `知識庫\AI 產出\內湖李進度報告.docx`、`知識庫\AI 產出\信義王室內裝修工程合約（AI 修改）.docx`
- `工作\腳本\make_report_docx_kb.js`（輸出路徑改成參數）、`工作\腳本\check_redline.py`
- `工作\kb9\unpacked\`（合約解開的工作資料夾）、`工作\pdf_kb\`（檢查用 PDF 與圖）
