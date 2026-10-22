# 做成成品（D-162、D-165、D-166、D-170）

內容定了以後，照用途挑成品；做檔交文件技能（`anthropic-skills:docx`、`xlsx`、`pptx`、`pdf`），這裡只講這台電腦上要注意的事與我們的程式。

## 用途決定成品

- 「把王宅的報價整理成 Excel」「剛才那個表存成檔」：表格就是 Excel，本機做。
- 「照合約範本幫大安陳寫一份，要寄給他簽」：要寄、要印、要套她的範本，本機 Word（或再轉 PDF）。
- 「這週的日程印出來」：本機 Word 或 PDF；行事曆檔這版不做專用指令。
- 「幫我做一份內湖李的進度報告給業主」：看與分享為主，先推薦線上文件（Claude Docs）；她要檔再本機做或存回。
- 「做一份設計提案簡報」：先推薦線上簡報（Slides）；她說要 PowerPoint 檔就本機做。
- 「幫我寫一封信給王先生」：草稿就是成品，信件內容放回答裡給她複製；不寄。
- 她指定格式就照她說的，不用再推薦。

## 推薦線上文件時講清楚三件事

1. 存在她自己的 claude.ai 帳號，不在這台電腦的資料夾裡。
2. 預設只有她看得到；要給別人看由她自己按分享，我不替她分享。
3. 要檔案的話：Docs 可以匯出 Word／PDF；Slides 請她在頁面上按下載。
- 她說好：建好後 `kb output link <網址> --title "<標題>" --type <型別> --entity <名> --basis <卡編號>…`，登記進知識庫，下次整理會做卡（卡的開原檔就是網址，內容以網頁為準）。
- Docs 匯出存回本機：純文字、小檔（約 20 KB 以下）可以由我把匯出的內容寫成檔再 `kb output save … --link <網址>`；有圖或比較大請她自己按匯出、存到下載夾，再 `kb output save "<下載夾的檔>" --to … --link <網址>`（下載夾的檔用複製的，不動她的）。
- Slides、網頁 artifact 下載回來的檔一樣用 `--link` 掛在同一筆。

## 本機做檔

- 先 `kb office check`：沒有的工具照它說的問她要不要裝（`kb office setup`，大約一分鐘）。沒有 Node.js 時新做 Word、簡報做不了，改推薦線上文件，或請她裝 Node.js。
- 文件技能說的「已預先裝好」在這台不成立：套件裝在工具夾，只有經 `kb office run` 跑才找得到（程式會設 NODE_PATH、PYTHONPATH）。
- 腳本用檔案工具寫在工作區（`kb office` 輸出印的那個夾，`.system/temp/office/<回合>/`），輸出檔也寫在工作區（環境變數 OUT_DIR）；`kb office run "<腳本>"` 跑。腳本裡不要寫死 AI 產出的路徑。
- 文件技能附的 soffice.py、recalc.py、thumbnail.py、accept_changes.py 在 Windows 跑不起來，unzip、zip、pdftoppm 也沒有：轉 PDF 用 `kb office pdf`、重算用 `kb office recalc`、看版面用 `kb office render`、解開打包用 `kb office unpack`／`pack`。
- 中文字型：東亞字型用「微軟正黑體」、語言 zh-TW；不寫的話 Word 會用新細明體，還可能標成簡體。
- 做好後驗一次：Word、簡報 `kb office render` 轉圖看版面；Excel 有公式的 `kb office recalc`，看錯誤數是 0，再把重算後的數字跟草稿比。
- 放進知識庫：`kb output save "<工作區的檔>" --to "AI 產出/<型別>/<檔名>"`（有草稿加 `--draft`，改自原檔加 `--from`）。

## 改她原本的檔

- 一律 `kb office copy <卡編號或路徑>` 拿副本，在副本上改；原檔不動。另存的檔名用「原檔主檔名（AI 修改）」。
- **Word**：用修訂標記（`w:ins`／`w:del`），作者固定「AI 助理」，只包住改的那幾個字；`kb office unpack` 解開 → 檔案工具改 `word/document.xml` → `kb office pack <夾> --to <檔名> --like <副本>`（照原檔順序，沒改的部件逐位元相同）。改完數一下修訂幾處、表格與頁尾還在。
- **Excel**：只改輸入格，公式格不動；改完 `kb office recalc`，把合計讀回來跟預期比。重算後檔案會整份重寫（圖表還在，但不是逐位元相同），這是正常的。
- **PowerPoint** 分三級：
  - 改幾個字、字數差不多：直接改 `ppt/slides/slideN.xml` 的字，打包。
  - 字變多、改版面：改完 `kb office render --pages <那幾頁>` 轉圖看；這台畫法跟 PowerPoint 不一定一樣，看得出字太多、看不準會超出多少，回答提醒她打開確認那幾頁。
  - 大改（換結構、很多頁）：推薦照內容重做一份新的，比修補穩。
- **轉格式**（「這份轉成 PDF」）：`kb office copy` → `kb office pdf "<副本>"` → `kb output save "<PDF>" --to … --from <原檔>`。

## 沒有工具時怎麼說

- 程式回的固定句照原意講，不加技術名詞：
  - 沒有能轉 PDF 或重算的程式：說可以免費裝 LibreOffice，或用 Word／Excel 打開另存；檔照樣做好、放進 AI 產出。
  - 重算做不到：公式格要她打開檔案才會更新，這次回答不報公式算出來的數。
  - 沒有 Node.js：新做 Word 或簡報可以改做成線上文件，或請她裝 Node.js。
- 不替她裝 Office、LibreOffice、Node.js，只說要裝什麼。

## 程式的話轉成白話

- `kb output save` 成功句「已放進 AI 產出\…（說「退回」會拿掉）」：照著講放在哪、退回會拿掉；改自原檔的講改自哪一份。
- 拒絕句：
  - 「只能放進 AI 產出底下」：目的地寫錯，改成 AI 產出底下的夾再放。
  - 「md 檔用檔案工具寫就好」：md 不用這個指令。
  - 「只收工作區或知識庫外的檔，這份在知識庫裡」：先 `kb office copy` 拿副本。
  - 「這個檔名像密碼或金鑰，不放」：換一個檔名。
  - 「草稿不在 AI 產出：…」「網址要是 claude.ai 的 artifact」「這個網址還沒登記；先跑 kb output link」：照句子補。
  - 「等不到鎖，沒有寫入」：等一下再放一次。
- `kb office copy` 的「拿不到副本：…」：排除的、像密碼的照句子跟她說是她設定的不讀；「清冊沒有這份檔」先跑 `kb inventory scan`。
- 守門擋「AI 產出的檔用 kb output save 放進來…」：表示指令碰到 AI 產出了，改成工作區做、output save 放；不要換寫法繞過去。
- 回答裡不印清冊編號與工作區的長路徑；講 AI 產出裡的夾與檔名就好。
