# 做成成品（D-162、D-165、D-166、D-170）

內容定了以後，照用途挑成品；做檔交文件技能（`anthropic-skills:docx`、`xlsx`、`pptx`、`pdf`），這裡只講這台電腦上要注意的事與我們的程式。

## 用途決定成品

- 「把王宅的報價整理成 Excel」「剛才那個表存成檔」：表格就是 Excel，本機做。
- 「照合約範本幫大安陳寫一份，要寄給他簽」：要寄、要印、要套她的範本，本機 Word（或再轉 PDF）。
- 「這週的日程印出來」：本機 Word 或 PDF；行事曆檔這版不做專用指令。
- 「幫我做一份內湖李的進度報告給業主」：看與分享為主，先推薦線上文件（Claude Docs）；她要檔再本機做或存回。
- 「做一份設計提案簡報」：先推薦線上簡報（Slides）；她說要 PowerPoint 檔就本機做。
- 「幫我寫一封信給王先生」：信寫在回答裡給她複製，不寫草稿檔、不寄；她要 Word 才本機做。
- 她指定格式就照她說的，不用再推薦。

## 線上文件

- 預設推薦線上文件：她沒說寄出、印出、套範本、要 Excel 的（給業主看、給同事看都算）都推薦。
- 推薦時同一句講清楚三件事再問：存在她自己的 claude.ai 帳號、不在這台電腦的資料夾；預設只有她看得到，要給別人看由她自己按分享；要檔案的話從那一頁下載（Docs 匯出 Word／PDF，Slides 按下載）。
- 她自己說「做成線上文件」：不用再問，但三件事一樣先講一句，講完才建。三件事一定在叫 Docs／Artifact 建之前講。
- 她對內容與做成線上文件都說好了，才建（Docs 用 Claude Docs 工具、簡報用 Artifact 的 Slides）。建在她自己的帳號不算替她分享。這個對話沒有這些工具就照實說，改做本機檔。
- 建好後登記：`kb output link <網址> --title "<標題>" --type <型別>`；掛實體加 `--entity <登記的名稱>`（沒登記的不寫，不要為這個去登記），依據加 `--basis <卡編號>`（編號從 `kb lookup`、`kb cards list` 的 `--json` 取）。下次整理會做卡，卡的開原檔就是網址。
- 她要本機檔：請她從線上文件下載（Docs 匯出 Word／PDF、Slides 按下載）到下載夾，再 `kb output save "<下載的檔>" --to "AI 產出/<型別>/<檔名>" --link <網址>`（知識庫外的檔用複製的，不動她的下載夾）。docx 是二進位，不要自己用檔案工具寫。
- Slides、網頁 artifact 下載回來的檔一樣用 `--link` 掛在同一筆。

## 本機做檔

- `kb office check` 看工具，最後一行印這一回合的工作區（`.system/temp/office/<回合>/`）；缺工具照它說的問她要不要裝（`kb office setup`，大約一分鐘）。沒有 Node.js 時新做 Word、簡報做不了，改推薦線上文件，或請她裝 Node.js。
- 文件技能說的「已預先裝好」在這台不成立：套件裝在工具夾，只有經 `kb office run` 跑才找得到（程式會設 NODE_PATH、PYTHONPATH）。
- 腳本用檔案工具寫在工作區，輸出檔也寫在工作區（環境變數 OUT_DIR，`kb office run` 第一行也印工作區）；腳本裡不要寫死 AI 產出的路徑。
- 工作區每一句話換一個夾，舊的過一天會清掉：用程式印出來的完整路徑；跨了幾句話的副本找不到了就重新 `kb office copy`。
- 文件技能附的 soffice.py、recalc.py、thumbnail.py、accept_changes.py 在 Windows 跑不起來，unzip、zip、pdftoppm 也沒有：轉 PDF 用 `kb office pdf`、重算用 `kb office recalc`、看版面用 `kb office render`、解開打包用 `kb office unpack`／`pack`。
- 中文字型：東亞字型用「微軟正黑體」、語言 zh-TW；不寫的話 Word 會用新細明體，還可能標成簡體。
- 放進來之前先驗（`recalc` 只收工作區的檔，要在 output save 之前做）：Word、簡報 `kb office render` 轉圖看版面；Excel 有公式的 `kb office recalc`，錯誤數要是 0，再把重算後的數字跟草稿比。
- 放進知識庫：`kb output save "<工作區的檔>" --to "AI 產出/<型別>/<檔名>"`（有草稿加 `--draft`，改自原檔或範本加 `--from`）。

## 套範本、照舊檔寫新的

- 範本在設定檔第七節：`kb profile show --json` 的 `範本` 那節，每列的「底」是卡編號；同種文件只有一份直接用，多份才問。
- `kb office copy <範本或舊檔的卡編號>` 拿副本，在副本上把內容換成新的（Word、PowerPoint 解開改 XML；Excel 改輸入格），抬頭、表格、頁尾照留。
- 驗過後 `kb output save … --from <範本或舊檔>`；檔名用新內容的（`2026-10-22 大安陳 合約.docx`），不用「（AI 修改）」。

## 改她原本的檔

- 一律 `kb office copy <卡編號或路徑>` 拿副本，在副本上改；原檔不動。放進 AI 產出時 `--to` 的檔名用「原檔主檔名（AI 修改）」，`pack` 的工作檔名隨意。
- **Word**：用修訂標記，作者固定「AI 助理」（不是 Claude），只包住改的那幾個字：`kb office unpack` 解開 → 檔案工具改 `word/document.xml` → `kb office pack <夾> --to <檔名> --like <副本>`（照原檔順序，沒改的部件逐位元相同）。修訂長這樣：
  `<w:del w:id="901" w:author="AI 助理" w:date="2026-10-22T10:00:00Z"><w:r><w:delText>15</w:delText></w:r></w:del><w:ins w:id="902" w:author="AI 助理" w:date="2026-10-22T10:00:00Z"><w:r><w:t>30</w:t></w:r></w:ins>`
  改完驗：對解開的夾用 Grep 數 `w:ins`、`w:del`、`w:author="AI 助理"`（不用 `python -c`，會被守門擋），再確認表格與頁尾還在。
- **Excel**：只改輸入格，公式格不動；直接改 XML（unpack／pack）最保險，用 openpyxl 存檔可能弄掉圖表與圖片。改完 `kb office recalc`，把合計讀回來跟預期比；重算會整份重寫檔案（圖表還在、不是逐位元相同），這是正常的。
- **PowerPoint** 分三級：
  - 改幾個字、字數差不多：直接改 `ppt/slides/slideN.xml` 的字，打包。
  - 字變多、改版面：改完 `kb office render --pages <那幾頁>` 轉圖看；這台畫法跟 PowerPoint 不一定一樣，看得出字太多、看不準會超出多少，回答提醒她打開確認那幾頁。
  - 大改（換結構、很多頁）：推薦照內容重做一份新的，比修補穩。
- Excel 與 PowerPoint 沒有修訂標記，回答要列出改了哪幾格、哪幾頁。
- **轉格式**（「這份轉成 PDF」）：`kb office copy` → `kb office pdf "<副本>"` → `kb output save "<PDF>" --to … --from <原檔>`。

## 沒有工具時怎麼說

- 程式回的固定句照原意講，不加技術名詞：
  - 沒有能轉 PDF 或重算的程式：說可以免費裝 LibreOffice，或用 Word／Excel 打開另存；檔照樣做好、放進 AI 產出。
  - 重算做不到：公式格要她打開檔案才會更新，這次回答不報公式算出來的數。
  - 沒有 Node.js：新做 Word 或簡報可以改做成線上文件，或請她裝 Node.js。
- 不替她裝 Office、LibreOffice、Node.js，只說要裝什麼。

## 程式的話轉成白話

- `kb output save` 成功句「已放進 AI 產出\…（說「退回」會拿掉）」：照著講放在哪、退回會拿掉；改自原檔的講改自哪一份。
- `kb output` 的拒絕句：
  - 「只能放進 AI 產出底下」：目的地寫錯，改成 AI 產出底下的夾再放。
  - 「md 檔用檔案工具寫就好」：md 不用這個指令。
  - 「找不到這個檔：…」：工作區的路徑換了或檔沒做出來，看 `kb office run` 的輸出。
  - 「只收工作區或知識庫外的檔，這份在知識庫裡」「客戶原檔先用 kb office copy 拿副本」：先 `kb office copy` 拿副本。
  - 「這個位置你說過不讀」：她設定不讀的夾，不放；「檔名不能有冒號」：換檔名。
  - 「這個檔名像密碼或金鑰，不放」：換一個檔名。
  - 「找不到這份原檔：…」：`--from` 寫錯，用卡編號。
  - 「草稿不在 AI 產出：…」「網址要是 claude.ai 的 artifact」「這個網址還沒登記；先跑 kb output link」「要給標題（--title）」「型別要是一個資料夾名…」「實體清單沒有…」：照句子補或拿掉那個參數。
  - 「檔案被別的程式開著或沒有權限」：請她關掉 Word／Excel 再說一次。「等不到鎖，沒有寫入」：等一下再放一次。「狀態檔讀不到」：照句子講，請服務人員看。
- `kb office pdf`／`render` 的「只收工作區或 AI 產出裡的檔：…」：原檔先 `kb office copy`，用副本的路徑。
- `kb office copy` 的「拿不到副本：…」：排除的、像密碼的照句子跟她說是她設定的不讀；「不在登記的資料夾裡」要先把那個夾加進來（manage）；「清冊沒有這份檔」是剛放進來的，先跑 `kb inventory scan`。
- 守門擋「AI 產出的檔用 kb output save 放進來…」：表示指令碰到 AI 產出了，改成工作區做、output save 放；不要換寫法繞過去。
- 回答裡不印清冊編號與工作區的長路徑；講 AI 產出裡的夾與檔名就好。
