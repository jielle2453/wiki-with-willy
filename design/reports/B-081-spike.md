日期：2026-10-21
給誰看：設計 session（llm-wiki-j-cb），用來寫 wiki-with-willy 的 produce spec
何時更新：第 9 題（CLI 守門）另記在 B-081-守門.md；本檔只在重跑桌面版實驗時更新

# B-081 報告：produce 做檔的工具在桌面版能不能用

環境：Claude 桌面版 3.1623.0.0（Claude Code 2.1.306），Windows 11 Home 10.0.26200。Python 3.14.7、Node v24.20.0、npm 11.19.0。LibreOffice 26.8.1.1 在 `C:\Program Files\LibreOffice\program\soffice.exe`。沒有 Microsoft Office（只有 Microsoft 365 啟動器 App「MicrosoftOfficeHub」，不是 Word/Excel）。pypdfium2 5.14.0 可 import。沒有 pandoc、pdftoppm、unzip/zip、gcc。Windows「智慧型應用程式控制」狀態為 1（開啟）。

## 總表

| 題 | 做法（實際指令／工具） | 花多久 | 結果 |
|---|---|---|---|
| 0 環境 | PowerShell 查版本、登錄檔、`Get-AppxPackage`；看 skill 清單與工具清單 | 數秒 | 見上；docx/xlsx/pptx/pdf 技能都看得到；Artifact、Claude Docs 工具都在 |
| 1 新 Word | docx 技能：`node 工作\腳本\make_report_docx.js`（docx-js），`NODE_PATH=produce-spike\node\node_modules` | 安裝 2.2 秒、產生 0.1 秒 | 成功。微軟正黑體、zh-TW、頁尾「第 N 頁，共 M 頁」都進檔；轉 PDF 看版面正常 |
| 2 技能附的工具 | 每個工具直接跑一次，失敗再試替代 | 每個 < 11 秒 | 照技能原樣：凡是叫 LibreOffice 的腳本全部失敗；加一個 Windows 包裝後 soffice.py、recalc.py 可用 |
| 3 轉 PDF／重算 | `soffice.exe --headless --norestore -env:UserInstallation=… --convert-to pdf`；重算用 recalc.py（經包裝） | PDF：冷啟動 5.6～10.3 秒、熱 1.8～2.8 秒；重算 5.2 秒 | 成功，不需 Office |
| 4 改 Word（修訂） | 複製→python zipfile 解開→merge_runs.py→python 改 XML 加 `w:del`/`w:ins`→python 打包→validate.py `--author` | < 2 秒 | 成功。2 處修訂進檔，表格、頁尾保住，檢查腳本通過 |
| 5 改 Excel | xlsx 技能做法：openpyxl 只改 D5 → recalc.py | 5 秒 | 成功。合計 376,400、稅 18,820、總計 395,220；公式 9 個都在；長條圖在 |
| 6 改 PPT＋轉圖 | 解開→改 slide2/slide5 的 `<a:t>`→打包→validate.py→soffice 轉 PDF→pypdfium2 轉 PNG→Read 看圖 | PDF 2.8 秒 | 改字成功、第 3 頁圖保住；第 5 頁看得出異常，但 LibreOffice 畫法和 PowerPoint 不同（見結論 6） |
| 7 Docs | Claude Docs `batch` 建檔→`update` 填內容→`export`(docx)→base64 寫成文字檔→python 解碼 | 匯出 1 次呼叫 | 成功。4,596 bytes，zip CRC 通過，python-docx 讀得出全部內容 |
| 7 Slides | Artifact Slides 類型，5 頁＋上傳圖 | 4 次發佈 | 建成功；我無法下載，使用者按下載後 pptx、PDF 都落在 `C:\Users\Jie\Downloads` |
| 7 網頁 | 一般 HTML artifact＋`downloads` capability；xlsx 以 base64 內嵌 | 1 次發佈 | 使用者按「下載 Excel」成功，檔與原檔逐位元相同，公式與圖表都在 |
| 8 新 PPT | pptx 技能：pptxgenjs + apply_theme.js（structured deck），validate.py，轉 PDF 看圖修一次 | 安裝與 docx 同一次；產生 0.2 秒 | 成功，Windows 上可跑 |
| 9 守門 | — | — | 第 9 題見守門.md |

## Artifact 網址（都設為私人，未分享；要不要刪請你決定）

| 名稱 | 類型 | 網址 |
|---|---|---|
| B-081 測試 內湖李進度報告 | Claude Docs | https://claude.ai/artifact/PFWe7FqJWiKJjYDa4spXhd |
| B-081 測試 內湖李提案 | Slides | https://claude.ai/artifact/VLPKSD1rvbwuBd6C7qZ598 |
| B-081 測試 大安陳報價 | 網頁（downloads capability） | https://claude.ai/artifact/L1aqBAuwM7quqPzpfDJdtY |

Docs 與 Slides 由我用 Artifact `open` 開給使用者；開在哪裡（桌面版側邊面板或瀏覽器）使用者沒有回報，**未確認**。

## 逐題結論

### 1. 桌面版看得到哪些文件技能、Artifact 與 Docs 工具

- 技能：`anthropic-skills:docx`、`xlsx`、`pptx`、`pdf` 都在，另有 `docs`、`google-workspace`。技能檔放在 `%LOCALAPPDATA%\Claude-Data\…\skills\<名稱>\`。
- Artifact 工具：有，含 publish、read、list、open、quickstart（Slides、Docs、Dashboard、Design、Design System 五種類型）。**沒有**把 Slides 匯出成 pptx/PDF 的動作。
- Claude Docs 工具：`batch`、`guide`、`update` 一直在；`create`、`read`、`query`、`delete`、`export` 要先用 ToolSearch 載入。`export` 支援 pdf/docx/html/text/markdown/notion，回傳 base64。
- 另外連著 Google Drive、Gmail connector（這次沒用）。

### 2. node docx 套件裝在哪、裝不裝得起來

- 技能說 `docx`、`pptxgenjs`「已預先裝好」，在這台電腦上**都沒有**（`require('docx')` → `Cannot find module 'docx'`）。markitdown、defusedxml 也沒有。
- 經使用者同意，`npm install --prefix produce-spike\node docx pptxgenjs`：25 個套件、2.2 秒，順利。
- **要注意**：裝在 `produce-spike\node\` 後，放在別處的腳本 `require` 找不到（第一次跑就報 `Cannot find module`），必須設 `NODE_PATH=produce-spike\node\node_modules`。pptx 技能的 `apply_theme.js` 也靠 `require.resolve("pptxgenjs")`，同樣要 NODE_PATH。
- pip：`pip install --target produce-spike\pylib defusedxml "markitdown[pptx,xlsx]"`，20.7 秒，連帶裝了 numpy、pandas、onnxruntime、Pillow 等約 30 個套件。**裝完馬上用時 .pyd 載入失敗**（`cannot import name '_imaging' from 'PIL'`、`No module named 'lxml.etree'`、`No module named 'numpy._core._multiarray_umath'`），約一分鐘後再試就正常。推測是 Windows 智慧型應用程式控制／Defender 在掃剛寫入、未簽章的 .pyd，**未證實**。之後要用 `PYTHONPATH=produce-spike\pylib`。

### 3. 技能附的工具哪些在 Windows 能用

| 工具 | 照技能原樣 | 替代做法後 | 錯誤原文／備註 |
|---|---|---|---|
| `office/soffice.py` | ✗ | ✓（包裝） | `AttributeError: module 'socket' has no attribute 'AF_UNIX'`（Windows 的 Python 沒有 AF_UNIX）。試 1：原樣；試 2：把 LibreOffice 加進 PATH，仍同錯；試 3：`工作\腳本\win_skill_run.py` 把 `_needs_shim` 換成回傳 False＋加 PATH，成功 |
| `xlsx/scripts/recalc.py` | ✗ | ✓（包裝） | `"Could not prepare the LibreOffice environment: module 'socket' has no attribute 'AF_UNIX'"`；包裝後 5.2 秒、`status: success` |
| `docx/scripts/accept_changes.py` | ✗ | ✗ | 原樣：同 AF_UNIX 錯。包裝後：`Error: LibreOffice failed: libpng error: Write Error`。它把設定檔路徑寫死成 `/tmp/libreoffice_docx_profile`，在 Windows 變成 **`C:\tmp\libreoffice_docx_profile\`（在 produce-spike 外面建了資料夾）**，我想刪但系統擋下（「C:\tmp 受保護」），**請使用者自行刪除 `C:\tmp`**（裡面只有這次建的一個 678 bytes 巨集檔） |
| `pptx/scripts/thumbnail.py` | ✗ | ✗ | 第一次：PIL 載入失敗（見結論 2）；包裝後：`UnicodeDecodeError: 'cp950' codec can't decode byte 0xbd`（讀 LibreOffice 輸出用系統編碼）＋`[WinError 2] 系統找不到指定的檔案`（叫 pdftoppm） |
| `office/validate.py`（docx、pptx） | ✓ | — | 需 defusedxml、lxml；`--original`、`--author` 都能用 |
| `docx/scripts/merge_runs.py` | ✓ | — | |
| `pdf/scripts/convert_pdf_to_images.py` | ✗ | 改用 pypdfium2 | `No module named 'pdf2image'`（且 pdf2image 也要 poppler） |
| `pandoc` | ✗ | 改用 python-docx／markitdown 讀 | 指令不存在 |
| `pdftoppm` | ✗ | 改用 pypdfium2（`工作\腳本\pdf_to_png.py`） | 指令不存在 |
| `unzip`／`zip` | ✗ | `python -m zipfile -e`；打包寫了 `工作\腳本\repack.py` | PowerShell `Expand-Archive` 拒收 .docx：「.docx 不是支援的保存檔案格式」 |
| `markitdown` | ✓（裝後） | — | 讀原報價表時公式格顯示 NaN，因為原檔沒有快取值 |

### 4. 轉 PDF、Excel 重算走 LibreOffice 做不做得成、要多久；都沒有時怎麼說

- 做得成，不需要 Office。直接叫 `soffice.exe` 最穩：
  `"C:\Program Files\LibreOffice\program\soffice.exe" --headless --norestore "-env:UserInstallation=file:///…/工作/lo_profile" --convert-to pdf --outdir <資料夾> <檔>`
- 時間：第一次（設定檔冷）5.6 秒（pptx）、經 soffice.py 包裝每次用新暫存設定檔約 10 秒；同一個設定檔第二次起 1.8～2.8 秒。重算 recalc.py 約 5 秒。每次都會印一行無害的 `Could not find platform independent libraries <prefix>`。
- 會不會跳視窗：用了 `--headless`，我這邊看不到畫面；問了使用者，**沒有得到回答，未確認**。
- 原報價表本身沒有任何公式快取值（所有公式格讀回 None），任何只讀快取值的工具（pandas、預覽）都會看到空白；重算後才有數字。
- 客戶電腦沒有 Office 也沒有 LibreOffice 時，我會說：「檔案我已經做好了，但這台電腦沒有能打開或轉成 PDF 的程式；你可以免費裝 LibreOffice（或用 Google 文件／Office 網頁版開），裝好我就能幫你轉 PDF、檢查版面。」

### 5. 改既有 Word（修訂）、Excel（只改輸入格）、PowerPoint（改字）

- **Word 修訂**：成功。第三條有兩處「15 日內」（表格第三期「完工驗收後 15 日內」、表格下方「甲方應於各期時點屆至後 15 日內以匯款方式支付」），兩處都改，只把「15」包進 `w:del`、插入「30」的 `w:ins`，作者「AI 助理」。檢查：`w:ins` 2、`w:del` 2；表格 1 個保住；頁尾「信義王宅工程合約｜第 1 頁」保住（原檔頁尾是純文字，不是頁碼欄位）；`validate.py --original … --author "AI 助理"` 通過（段落數 25 → 25）；轉 PDF 看到刪除線與底線標記。輸出：`工作\信義王室內裝修工程合約（AI 修改）.docx`。「接受所有修訂」的 accept_changes.py 跑不起來（見結論 3）。
- **Excel**：成功。照 xlsx 技能用 openpyxl 改 D5（5800 → 6000）存成 `工作\大安陳報價表（改單價）.xlsx`，再 recalc.py。核算：原合計 45,000＋120,000＋18×5,800＋6×9,500＋32×1,200＋8,000＝372,800；改後多 18×200＝3,600 → 376,400 ✓；稅 ROUND(376,400×5%)＝18,820；總計 395,220。9 個公式都在、0 錯誤；長條圖在（參照 `報價!$E$3:$E$8`）。注意：LibreOffice 重算會整份重寫檔案，圖表 XML 從 1,235 bytes 變 4,623 bytes、多了 style/colors 檔，圖還在但不是「原封不動」。
- **PowerPoint**：成功改字，第 3 頁圖片（image1.png）保住，validate.py 通過。輸出：`工作\內湖李設計提案（AI 修改）.pptx`。

### 6. 轉圖片後看不看得出 PowerPoint 版面問題

- 第 5 頁看得出「不對勁」：原本一行字的框，在圖上變成一條窄長、一路長到接近頁底的直欄（框本身沒有邊線，所以看不到框的邊界，只看得到字的範圍）。
- **但 LibreOffice 畫的不是 PowerPoint 會畫的樣子**：原文字框設定 `wrap="none"`（不換行）＋自動長大。PowerPoint 會照我放的 4 個換行顯示 4 行長字、往右超出 4:3 投影片的右邊；LibreOffice 卻在 4 吋框寬處自動折行、往下長。依 OOXML 定義推測如此，**沒有 PowerPoint 可以驗證**。所以結論是：轉圖能抓到「字太多」，但抓到的樣子可能和客戶在 PowerPoint 看到的不同；要可靠，得另外用程式檢查文字長度對框大小。
- 第 3 頁圖片保住，轉圖看得到。

### 7. Docs 匯出、Slides 下載、網頁下載 Excel

- **Docs 匯出存回本機：做得到，只適合小檔。** `export` 回傳 base64（4,596 bytes → 6,128 字元）。我得把整段 base64 再寫一次進 `工作\從Docs匯出.b64.txt`，再用 python 解碼成 `工作\從Docs匯出.docx`。zip CRC 檢查通過、python-docx 讀出全部段落與表格、LibreOffice 能轉 PDF。內容在 context 裡出現兩次（匯出結果一次、我寫檔一次）；這一步整體約用掉 1.4 萬 token。等比例推算：50 KB 的檔約 15 萬 token，含照片的 1 MB 檔完全不可行。我抄寫 base64 也可能抄錯（這次沒錯，靠 CRC 才知道）。建議門檻：純文字 docx（約 20 KB 以下）可以；有圖就請使用者在 Docs 上按「Export」。匯出的 docx 有一行「Oct 21, 2026 · @leu」（日期與作者 chip 變成英文日期與帳號名稱）。
- **Slides：我不能下載。** Artifact 工具沒有匯出動作。使用者在網頁上按下載，pptx（27,858 bytes，5 頁、16:9、圖在 `ppt/media/image-3-1.png`）與 PDF（463,028 bytes，5 頁）都落在 `C:\Users\Jie\Downloads`，我再複製到 `工作\下載回來\` 檢查，內容與版面正確。要幾步：使用者沒有回報，**未確認**。
- **網頁「下載 Excel」按鈕：可行。** 頁面一般的 `<a download>` 在 artifact 裡被擋，要宣告 `downloads` capability 並呼叫 `downloads.save()`（會跳確認，使用者可拒絕；xlsx 在允許清單內）。我把重算過的報價表以 base64 內嵌在頁面裡（11,734 bytes），使用者按一次就下載到 `C:\Users\Jie\Downloads\大安陳報價表.xlsx`，和我放進去的檔逐位元相同：9 個公式、快取值、長條圖都在。重點：這樣下載的是「事先做好的真 xlsx」；如果改成在瀏覽器裡用 JS 現做 xlsx，圖表會做不出來。

### 8. 本機新做 PowerPoint 在 Windows 跑不跑得起來

- 跑得起來。pptxgenjs（裝在 `produce-spike\node\`，要 NODE_PATH）＋技能的 `apply_theme.js`，5 頁含圖，產生 0.2 秒，validate.py 通過，`工作\內湖李提案（新）.pptx`。
- 技能要求的轉圖檢查只能靠我自己的 soffice＋pypdfium2 流程（技能的 soffice.py、pdftoppm、thumbnail.py 都不能照原樣用）。第一次轉圖看出兩個問題（標題置中、第 5 頁最後一個字被擠到第二行），改一次後正常。

### 9. 哪些交給模型、哪些需要 kb 程式幫忙

交給模型就好：
- 寫 docx-js／pptxgenjs／openpyxl 腳本、改 XML 做修訂、改單一儲存格、改投影片文字。這次都一次成功或改一次就好。
- 看轉出來的圖判斷版面、核算金額。

需要 kb 程式幫忙（模型每次自己摸索很浪費，也容易踩到同樣的坑）：
- **偵測環境**：有沒有 Office、LibreOffice 在哪、node 套件與 python 套件在哪（技能說「已預先裝好」在 Windows 桌面版不成立）。
- **轉 PDF／重算／轉圖**：給一個 Windows 可用的指令包住 `soffice.exe --headless`（固定設定檔路徑、處理 cp950 輸出）＋pypdfium2 轉 PNG。技能附的 soffice.py、recalc.py、thumbnail.py、accept_changes.py 在 Windows 原樣都跑不起來，其中 accept_changes.py 還會寫到 `C:\tmp`。
- **解壓／打包 Office 檔**：Windows 沒有 zip/unzip，PowerShell 拒收 .docx；需要固定的 python 解包／打包工具，保留原檔的檔內順序。
- **安裝套件的位置與路徑**：統一裝到某個資料夾並自動設 NODE_PATH／PYTHONPATH；剛裝完的 .pyd 可能暫時載入失敗，要能重試。
- **文字超框檢查**：LibreOffice 的畫法和 PowerPoint 不一定一樣，靠看圖不夠，最好有程式估算文字寬度對框大小。
- **Docs 匯出存檔**：小檔可由模型抄 base64，大檔不行；spec 應該寫「有圖或超過約 20 KB 時請使用者按匯出」，或由程式直接取檔。

第 9 題見守門.md。

## 檔案位置

- 產出：`工作\內湖李進度報告.docx`、`工作\信義王室內裝修工程合約（AI 修改）.docx`、`工作\大安陳報價表（改單價）.xlsx`、`工作\內湖李設計提案（AI 修改）.pptx`、`工作\內湖李提案（新）.pptx`、`工作\從Docs匯出.docx`、`工作\pdf\*.pdf`、`工作\img\*.png`、`工作\下載回來\`（使用者下載的三個檔的複本）
- 腳本：`工作\腳本\`（make_report_docx.js、make_deck.js、win_skill_run.py、pdf_to_png.py、repack.py、redline_contract.py、edit_deck.py、edit_xlsx_openpyxl.py、inspect_*.py）
- 套件：`node\`（docx、pptxgenjs）、`pylib\`（defusedxml、markitdown 等）
- `材料\` 沒有改動。
- produce-spike 以外留下的東西：`C:\tmp\libreoffice_docx_profile\`（accept_changes.py 建的，刪除被擋，請自行刪）；`C:\Users\Jie\Downloads\` 裡使用者下載的三個檔。
