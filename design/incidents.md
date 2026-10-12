# 事故

日期：2026-10-12
給誰看：設計 session 判斷架構與寫 spec；實作與測試 session 從這裡取測試案例（architecture 六、維運層）。
什麼時候更新：只在設計 session 改。新事故加一段、判斷改了就改該段；其他 session 發現的先寫 backlog。

**來源縮寫：** `llm-wiki` 指 `third party\llm-wiki-plugin-main\llm-wiki-plugin-main\`，現況 1.8.0；`plugin` 指 `third party\plugin\plugin\`，現況 0.13.1，沒有 CHANGELOG，修正版號取自程式註解。行號是採集當天（2026-10-12）的行號。

**收錄範圍：** 原文寫明真的發生過的（實測、驗收、ISS、e8、版號修正），加上合併版有對應物、值得變成測試的防範型（標「防範型」）。沒收：像素辦公室的外觀修改、純屬 llm-wiki 舊結構且合併版沒有對應物的（Obsidian 連結、`tidy_check` T1～T9 的格式細節、`coordination.md` 欄位格式）。

**判斷依據：** 「合併版會不會踩」依 `architecture.md` 與 `decisions.md`（到 D-070）判斷；拿不準的寫判斷並標「待設計 session 確認」。

---

## 一、編碼與 Windows 環境

### I-001 Windows 主控台 cp950，Python 印中文就崩潰
- 來源：llm-wiki 1.8.0 `CLAUDE.md:116-117`（2026-10-08）、`templates/vault/.claude/settings.json:13-15`；plugin 0.13.1 `scripts/kbcommon.py:62-68`、`scripts/office_tools.py:175-181`。
- 發生過什麼：stdout 預設 cp950，腳本印中文就丟 `UnicodeEncodeError`。`python -I` 會忽略 `PYTHONIOENCODING`，所以設環境變數救不了，只能在腳本開頭 reconfigure。另一個方向是子程序（PowerShell、Office COM）的錯誤訊息是 cp950，用 UTF-8 解會失敗，plugin 改成失敗時退回 mbcs。
- 合併版會不會踩：conventions 四已規定腳本開頭 reconfigure。容易漏的是 `hooks/*.py`，它們不經過 `kb.py`，每支都要自己 reconfigure；子程序輸出也要另外處理解碼。測試：每支 hook 與 `kb.py` 在 cp950 主控台都能印中文。
- 客戶會不會遇到：會。繁中 Windows 預設就是 cp950。漏掉的話 hook 崩潰、開場什麼都不印、守門失效。

### I-002 設了 PYTHONUTF8=1，Big5 舊檔反而讀不了
- 來源：llm-wiki 1.8.0 `templates/vault/.claude/settings.json:17-28`（2026-09-26 實測）。
- 發生過什麼：只設 `PYTHONIOENCODING` 時，`open()` 仍用 cp950，Big5 舊檔讀得到。加上 `PYTHONUTF8=1` 後 `open()` 變成 UTF-8，同一個檔就 `UnicodeDecodeError`。範本因此預設不設。
- 合併版會不會踩：只要 `skeleton/.claude/settings.json` 不設 `PYTHONUTF8`，而且讀檔一律帶 `encoding=`（conventions 四），就不會踩。測試資料有 Big5 備忘.txt，可以直接驗。
- 客戶會不會遇到：會，老客戶常有 Big5 檔。後果是那份檔讀不到，或被標成損壞。

### I-003 只讀前幾個 byte 測編碼，誤判成 UTF-8
- 來源：llm-wiki 1.8.0 `skills/wiki-ingest/SKILL.md:41-60`（實測儀器匯出的 `Specification.csv`）。
- 發生過什麼：檔案 267 bytes，第一個非 ASCII 字元在 offset 49，讀前 48 bytes 測編碼會通過、判成 UTF-8，讀整份時才失敗。造成問題的是度量單位符號（° √ ±），不是中文。同一個來源 4 個檔 100% 不是 UTF-8。
- 合併版會不會踩：會，而且規則本身有矛盾。conventions 四寫「讀外部檔一律帶 `encoding=`，不猜」，但客戶原檔的編碼事先不知道，`kb convert` 一定要偵測。偵測要讀整份或讀夠多、失敗要明講，寫在哪裡沒定。設計 session 2026-10-12 定：conventions 四改寫，自己的檔不猜、客戶原檔整份偵測、失敗明講。
- 客戶會不會遇到：會。舊 csv、txt 讀出亂碼，或建置報告把它列成「讀不到」。

### I-004 用 Read 讀 Big5 再整份重寫，變亂碼又漏段
- 來源：plugin 0.13.1 `skills/query/SKILL.md:111`、`scripts/source_edit.py:414-415`、`:494-544`、`scripts/convert.py:96-103`。防範型。
- 發生過什麼：plugin 規定改 txt、md、csv 要先轉暫存、用 Edit 只改要改的地方，再由程式覆蓋。程式沿用原檔的編碼、換行與 BOM，看不出編碼就不覆蓋，新內容有 Big5 寫不進的字就拒絕。新建的 csv 要加 BOM，否則 Excel 打開是亂碼。
- 合併版會不會踩：原地覆蓋這條路不存在（D-010，produce 一律另存）。但另存的新檔要用什麼編碼（沿用原檔的 Big5，還是改 UTF-8 加 BOM）沒定。設計 session 2026-10-12 定：另存一律 UTF-8，CSV 帶 BOM（conventions 四）。
- 客戶會不會遇到：會。另存的 csv 用 Excel 打開是亂碼。

### I-005 中文路徑用 PowerShell 會一直跳核准，加 cd 或串指令也會
- 來源：plugin 0.13.1 `rules/core-rules.md:146-147`、`scripts/envcheck.py:250-251`、`agents/card-maker.md:23`、`rules/office.md:34`。
- 發生過什麼：Windows 使用者名稱是中文時，plugin 路徑含中文，用 PowerShell 執行 plugin 程式會被要求核准，用 Bash 不會。加 `cd` 前綴、用 `&&` 串指令、用 `python -c`，都會落到放行規則外，客戶一直被問。
- 合併版會不會踩：會。conventions 規定唯一入口是 `kb.py`，但沒規定用哪個殼、一次只能一個指令。這條要寫進 core.md 還是各 SKILL.md 沒定。設計 session 2026-10-12 定：呼叫方式寫進 conventions 四腳本入口那列（Bash 工具、一次一個、引號、不 cd），SKILL.md 不重複寫。
- 客戶會不會遇到：會。核准視窗一直跳，客戶會以為系統壞了。

### I-006 PATH 上的 python 是 Windows 商店的空殼
- 來源：plugin 0.13.1 `scripts/envcheck.py:49`、`:241-242`。防範型。
- 發生過什麼：hook 用 `python` 指令執行。若 PATH 上的 python 不存在，或是 Windows 商店的空殼，hook 整個跑不起來，原檔保護與備份都失效。envcheck 因此實際跑一次 python，並教客戶關掉應用程式執行別名。
- 合併版會不會踩：會，hook 架構相同。envcheck 要有這一項（architecture 六）。
- 客戶會不會遇到：會，新電腦很常見。後果是守門與回合備份沒有作用，客戶看不出來。

### I-007 onnxruntime 在舊版 VC++ 上讓 Python 直接崩潰
- 來源：plugin 0.13.1 `scripts/envcheck.py:6-7`、`:29`、`scripts/_magika_stub/magika.py:3-4`、`scripts/convert.py:7`。
- 發生過什麼：markitdown 依賴 magika，magika 依賴 onnxruntime。在 Visual C++ 執行階段較舊的 Windows 上，載入時 Python 整個崩潰，`try` 接不住。plugin 改成在子程序裡試載入，失敗就換上 magika 替身，讓 markitdown 退回用副檔名判斷格式。
- 合併版會不會踩：會。markitdown 是選配（conventions 四），但「缺了降級」只處理沒裝的情況，沒處理裝了但一載入就崩潰。測試要模擬子程序崩潰。
- 客戶會不會遇到：會，舊電腦上建置會在轉檔時直接中斷。

### I-008 poppler 沒有數位簽章，被 Windows 擋下
- 來源：plugin 0.13.1 `scripts/convert.py:4-5`、`:74-75`（0.13.0 修正）、`rules/office.md:20`、`agents/card-maker.md:39`。
- 發生過什麼：Read 讀 PDF 要靠 poppler（pdftoppm），poppler 沒有數位簽章，被 Windows 智慧型應用程式控制擋下。0.13.0 改用 pdfplumber 附的 pypdfium2 轉圖，並禁止製卡時用 Read 直接讀原檔 PDF。
- 合併版會不會踩：會，只要 `carding.md` 或 card-maker 讓製卡者用 Read 讀 PDF 就會踩。製卡讀 PDF 一律走 `kb convert`。
- 客戶會不會遇到：會，Win11 預設開這個功能。所有 PDF 都讀不到。

### I-009 外部 Office 技能在客戶的 Windows 上用不了
- 來源：plugin 0.13.1 `rules/office.md:18`、`:20-21`、`:35`、`scripts/office_tools.py:9`。
- 發生過什麼：docx、xlsx 技能附的 `soffice.py`、`recalc.py` 用了 Linux 才有的 AF_UNIX，Windows 一定失敗。Git Bash 沒有 `zip`。`require('docx')` 失敗時 AI 自己 `npm install`，會裝到別的地方。有 Claude Docs 的環境，docx 技能預設改出 Docs，不出 .docx。
- 合併版會不會踩：會，produce 的 Office 能力（architecture 七）一樣要靠這些技能或自寫工具。`produce/references/office.md` 要把這四點寫進去。
- 客戶會不會遇到：會。結果是做不出 Word、轉不出 PDF，或拿到的是 Docs 連結而不是檔案。

### I-010 AI 自己試 python-docx 失敗，就說「這台電腦不能產生 Word」
- 來源：plugin 0.13.1 `rules/office.md:17`（原文：之前的實測就這樣誤判過）、`skills/query/SKILL.md:57`、`skeleton/CLAUDE.md:7`。
- 發生過什麼：AI 沒呼叫 docx 技能，自己 import python-docx，失敗後下結論說這個環境不能產生 Word。客戶電腦不一定有這個程式庫，而且它做不出修訂標記。
- 合併版會不會踩：會，produce 一樣有這個風險。核心是「先確認環境能力，再說做不到」，跟 envcheck 的 Office 偵測（D-051）接起來。
- 客戶會不會遇到：會，客戶會被錯誤告知「做不了」。

### I-011 用 openpyxl 存既有 Excel，圖表與連結被毀
- 來源：plugin 0.13.1 `rules/office.md:33`（e8 實測）、`scripts/kbcommon.py:510-511`、`scripts/convert.py:113`；llm-wiki 1.8.0 `skills/wiki-collabteam/07-技術提醒.md:16-17`。
- 發生過什麼：openpyxl 存檔會重寫圖表、清掉外部連結的快取值，走勢圖、交叉分析篩選器、表單控制項都會不見，帶列印設定的檔還會丟 printerSettings。e8 實測直接改工作表 XML，這些都保住了。openpyxl 寫出的公式格沒有快取值；xlrd 是 0 起算、COM 是 1 起算，混用同一格會差一列。
- 合併版會不會踩：會。原檔不會被改（D-010），但 produce「改一份文件」是從既有 Excel 另存，副本照樣被毀。
- 客戶會不會遇到：會。另存的報價單少了圖表，或公式沒有數字。

### I-012 Excel 公式沒有快取值，AI 自己手算，算錯
- 來源：plugin 0.13.1 `rules/office.md:85`（原文：實測 AI 手算算錯過）、`scripts/office_tools.py:368`。
- 發生過什麼：沒辦法重算、只能把 Excel 設成打開時重算的情況下，AI 自己手算公式結果報給客戶，數字錯了。之後禁止報公式的數字，清冊記「公式待重算」。
- 合併版會不會踩：會。R-03 只規定數值回正本讀，沒講正本裡的公式格沒有值時怎麼辦。answer 與 produce 都要有這一句。
- 客戶會不會遇到：會，拿到錯的金額。

### I-013 Office 背景程式跳出視窗卡住
- 來源：plugin 0.13.1 `scripts/office_tools.py:10`、`:45`、`:97-98`、`:142-148`、`commands/build-setup.md:57-59`。原文說 COM 還沒在有 Office 的電腦上測過。
- 發生過什麼：Office 第一次啟動要登入或按同意、跳出受保護的檢視時，背景重算或轉 PDF 會沒有回應。plugin 加了逾時，只結束自己開的那個 Office，並停用巨集與外部連結更新。建置當場用自檢小檔實際跑一次。
- 合併版會不會踩：會，`kb office` 在架構裡。第一版要不要用 COM 做重算與轉 PDF（B-102 只延後了舊格式自動轉）沒定。設計 session 2026-10-12 定：第一版 `kb office` 只偵測與讀，不開 COM；重算與轉 PDF 併進 B-102。
- 客戶會不會遇到：會，對話卡住不動。

### I-014 加密的 Office 檔和舊版 .doc、.xls 檔頭一樣
- 來源：plugin 0.13.1 `scripts/office_check.py:111-114`（0.13.1）。
- 發生過什麼：兩種檔的檔頭都是 OLE magic，分不出是加了密碼，還是舊版檔被改了副檔名。0.13.1 起兩種都拒絕修改，請客戶確認。
- 合併版會不會踩：會。inventory 要標讀不到的原因（D-050，分加密、舊格式等），只看檔頭會把原因標錯。測試資料裡同時有密碼 PDF 與 `.doc`。
- 客戶會不會遇到：會，建置報告說「加密」，客戶卻說沒設密碼。

### I-015 打開 Office 檔再存就算改了檔，指紋沒有資訊量
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/04-查核.md:42-43`、`07-技術提醒.md:8-11`、`skills/wiki-core/SKILL.md:79`；plugin 0.13.1 `scripts/inventory.py:4-6`、`skills/ingest/SKILL.md:47`。
- 發生過什麼：用桌面 App 開檔再存，格式會被正規化，size 與 md5 都變，副本立刻被判「過期」。程式化 COM 明確 `.Save()` 實測 byte 零變動。xlsx、docx 是 zip，每次存檔都重寫 metadata。plugin 處理的是另一半：mtime 變了但指紋相同（假變動）不重做卡。
- 合併版會不會踩：會。客戶只是打開報價單看一下再存，開場變動偵測就報「修改」，接著重做卡、花額度。Office 檔的指紋要不要改用抽出文字算（plugin 的 ai_output 已經用文字指紋）沒定。設計 session 2026-10-12 定：Office 檔指紋用抽出文字算，文字不變視為未改（D-014 補）。
- 客戶會不會遇到：會，開場一直報「改了 N 份」，其實沒改。

### I-016 Excel 逐格寫入，合併儲存格的表塌掉
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/07-技術提醒.md:12-14`。
- 發生過什麼：逐格寫入讓有垂直合併的表塌陷，一整列消失；修的時候又蓋掉另一列 13 欄。連過幾個 commit，雙方驗收都沒發現。之後規定改完要比對列數，並做全表逐格 diff。
- 合併版會不會踩：會，produce 改表格時就會。原檔不受影響（D-010），壞的是成品。produce 定稿前要有比對步驟。
- 客戶會不會遇到：會，成品少一列，客戶不一定看得出來。

### I-017 路徑超過 260 字元
- 來源：plugin 0.13.1 `scripts/kbcommon.py:75-82`、`scripts/convert.py:109`、`scripts/envcheck.py:259`。防範型。
- 發生過什麼：讀寫一律加 `\\?\` 前綴，交給 markitdown 前也先用長路徑開檔。
- 合併版會不會踩：conventions 四已寫「長路徑要認」。容易漏的是交給第三方程式庫的那一步，以及檔案卡鏡射路徑（`檔案索引/<別名>/<鏡射路徑>/`）比原檔路徑更長。
- 客戶會不會遇到：會，資料夾很深的客戶會有部分檔讀不到或卡寫不出來。

### I-018 防毒或索引正在讀檔，取代被暫時拒絕；殘留的鎖
- 來源：plugin 0.13.1 `scripts/kbcommon.py:119-128`、`:149-153`。防範型。
- 發生過什麼：防毒、Windows 索引或另一支程式剛好在讀時，`os.replace` 會暫時被拒，plugin 重試 20 次（約 2 秒）。程式中途被中斷會留下鎖檔，超過十分鐘的鎖視為殘留並清掉。
- 合併版會不會踩：會，`_lib/io` 與鎖檔防重入（architecture 四）要有這兩項。
- 客戶會不會遇到：會，偶爾出現。沒處理的話，客戶看到「寫入失敗」或「另一個在跑」，而且一直不消失。

### I-019 被 Word、Excel 開著的檔改名或搬動，留下沒登記的副本
- 來源：plugin 0.13.1 `scripts/source_edit.py:218-244`（0.10.1 修正）、`:253-266`、`scripts/office_check.py:148-150`、`scripts/folders.py:751-762`（0.10.1）。
- 發生過什麼：檔案開著時 rename 會被拒。`shutil.move` 失敗時會先複製再刪原檔，刪不掉就留下一份沒登記的副本。0.10.1 改成同一顆磁碟只用 `os.rename`，被拒直接報錯；跨磁碟才複製，刪原檔失敗就把複製出來的收回。分區改名也改成先改可能被鎖住的那一邊，失敗就整個不改。
- 合併版會不會踩：會。`kb source-edit move` 是收件匣與 AI 產出歸位的唯一路（D-045），客戶開著檔是常態。
- 客戶會不會遇到：會。後果是同一份檔出現兩份（變成重複檔），或搬到一半。

### I-020 同步夾的占位檔一讀就觸發下載
- 來源：plugin 0.13.1 `scripts/inventory.py:124-131`、`scripts/kbcommon.py:56-59`、`scripts/folders.py:367`、`scripts/source_edit.py:528-529`、`commands/build-inventory.md:19`。防範型。
- 發生過什麼：plugin 用 RECALL_ON_OPEN、RECALL_ON_DATA_ACCESS、OFFLINE 屬性判斷占位檔。占位檔不算指紋，抽樣比對只比大小，也不覆蓋。客戶按了「釋放空間」的檔沿用原紀錄。建置時請客戶設「一律保留在此裝置上」。
- 合併版會不會踩：會。architecture 七已列占位檔，但開場變動偵測（D-060）與 `kb folders relocate` 的抽樣比指紋都會碰到。
- 客戶會不會遇到：會（同步夾客戶）。開場時一口氣下載幾 GB，或把被釋放的檔報成「刪除」。

### I-021 走目錄時撞到 junction、WSL 連結或沒權限的路徑，整個中斷
- 來源：llm-wiki 1.8.0 `scripts/repo.py:99-101`（實測 WinError 1920）。
- 發生過什麼：`rglob` 撞到 WSL 符號連結、junction 或權限不足的路徑，整個拋 OSError。改成自寫的走訪，逐項容錯跳過。
- 合併版會不會踩：會，inventory 與 `kb folders discover` 都要走客戶的目錄。客戶的「文件」夾裡常有系統留下的舊版 junction（例如「我的音樂」），一碰就拒絕存取。
- 客戶會不會遇到：會，加資料夾或建置在掃描時整個失敗。

### I-022 檔名裡的符號被誤解析
- 來源：plugin 0.13.1 `scripts/update.py:41`、`scripts/kbcommon.py:412-414`、`scripts/folders.py:588`、`scripts/health.py:338`。防範型。
- 發生過什麼：用 glob 找檔時，檔名裡的 `[ ]` 被當成萬用字元。依據欄用頓號切，遇到檔名本身含「、」就切錯。全形「｜」被照抄。
- 合併版會不會踩：主鍵改成 id（D-014），依據欄的格式也固定（D-038），切錯的機會比較小。但任何用 glob 找客戶檔名的地方仍會踩。測試資料的檔名要放 `[ ]`、「、」與全形符號。
- 客戶會不會遇到：會，「報價[最終].xlsx」這種檔名很常見。

## 二、守門與寫入路徑

### I-023 守門用字串比對，擋不住把路徑拆開寫的程式碼
- 來源：plugin 0.13.1 `hooks/guard.py:4-5`、`:11`、`:169`（實測）。
- 發生過什麼：指令列寫入只能靠「提到來源路徑而且含寫入動作」的字串比對。實測把路徑拆開寫的程式碼攔不住，所以 hook 只是規則層之外的第二道防線。
- 合併版會不會踩：會，這是先天限制。D-034 的處理是解析不到就寫一行 ledger、會改外部狀態的工具預設擋。測試：拆開路徑的寫入指令，要在 ledger 留一行。
- 客戶會不會遇到：一般不會察覺。風險是原檔被改了卻沒有任何紀錄。

### I-024 引號裡的「>」被當成輸出導向，誤擋
- 來源：plugin 0.13.1 `hooks/guard.py:8`、`:113`、`:261`（0.10.1 修正）。
- 發生過什麼：`python -c` 裡的正規表示式 `<[^>]+>` 被當成導向寫檔而擋下。0.10.1 起導向只看引號外的部分。
- 合併版會不會踩：守門程式從 plugin 搬過來就不會；重寫就會。這條要當回歸測試。
- 客戶會不會遇到：會，算誤擋（B-110），客戶看到莫名其妙的拒絕。

### I-025 AI 直接改來源清單或 settings.json，擴大讀取權限
- 來源：plugin 0.13.1 `hooks/guard.py:9`、`:150-153`（0.10.1）、`rules/core-rules.md:109`。防範型。
- 發生過什麼：這兩個檔改由 hook 擋下，檔案工具與指令都不能直接改，只能經由 `init_kb.py` 或 `folders.py`。
- 合併版會不會踩：可能。D-046 的路徑表列了五區與客戶原檔區，沒列 `.system/`、`.claude/settings.json`、總資料夾 `CLAUDE.md`。conventions 說 `.system/` 是程式獨占，但守門擋不擋 AI 直接寫沒寫明。設計 session 2026-10-12 定：路徑表補三行，三處都擋（D-046 補、architecture 九）。
- 客戶會不會遇到：一般不會察覺。後果是「不讀某夾」被悄悄打開，客戶的隱私承諾破掉。

### I-026 路徑寫「..」借 kb.py 的放行規則去跑別的程式
- 來源：plugin 0.13.1 `hooks/guard.py:249-251`。防範型。
- 發生過什麼：總資料夾放行了「python 執行 plugin scripts/ 裡的程式」，所以路徑含 `..` 一律擋，免得借這條規則跳到別的程式。
- 合併版會不會踩：會，放行規則同型（conventions 四：客戶端放行 `kb.py`）。
- 客戶會不會遇到：不會察覺。這是安全問題。

### I-027 用 sed 或 echo> 改頁面，繞過回合備份
- 來源：plugin 0.13.1 `hooks/guard.py:12`、`:272-274`、`rules/core-rules.md:88`、`:92`、`rules/maintenance.md:79`。防範型。
- 發生過什麼：hook 只在檔案工具寫入前備份。用指令改的頁面退不回來，所以指令寫 wiki/、admin/、CLAUDE.md 一律擋。備份路徑另外要用原始大小寫，`normcase` 會把它轉成小寫（`guard.py:239`）。
- 合併版會不會踩：會。綜合卡與檔案索引走回合備份（D-046），指令寫這兩處也要擋或先備份。
- 客戶會不會遇到：會，「退回上一版」退不回來。

### I-028 把一次要求拆成好幾次呼叫，繞過「超過 N 份要先問」
- 來源：plugin 0.13.1 `scripts/source_edit.py:19`（0.10.1）、`:772-776`、`skills/query/SKILL.md:118`。防範型。
- 發生過什麼：原文舉的例子是「_01 到 _06 搬到照片A、_07 到 _11 搬到照片B」，每次都不超過門檻。0.10.1 加了合計試算，門檻也把同一回合前面已改的份數算進去。
- 合併版會不會踩：不會，前提是大改動計數掛在回合代號上（architecture 六，事務層）。測試：同一回合分兩次各搬 6 份，第二次要先問。
- 客戶會不會遇到：防住了就不會。沒防住，一次搬走二十份沒被問。

### I-029 讀大檔的守門只是減速丘；Read 讀 PDF 不理 limit
- 來源：llm-wiki 1.8.0 `scripts/big_read_guard.py:13-19`、`:80-81`（2026-09-21 驗收實測）、`skills/wiki-ingest/SKILL.md:104-117`、`skills/wiki-query/SKILL.md:31-33`、`hooks/hooks.json:34-46`。
- 發生過什麼：Read 讀 PDF 不理 offset、limit，`limit=50` 照樣回整份文字加每頁渲染圖，只有 `pages=` 有效。matcher 只認 Read，`cat`、`sed`、Grep 都能繞過，被擋之後改用 Bash `cat` 就把整份灌進 context。腳本自己出錯時放行。
- 合併版會不會踩：會。architecture 寫「併入讀檔守門」，要涵蓋 PDF 的 pages 參數，不能把它當保證。
- 客戶會不會遇到：會，額度一次用掉很多，對話很快被摺疊。

### I-030 settings.json 多一個未知鍵，可能連 hook 一起靜默失效
- 來源：llm-wiki 1.8.0 `templates/vault/.claude/settings.json:8-10`。防範型。
- 發生過什麼：註解放進 env 會變成真的環境變數；permissions 裡多一個未知鍵，可能讓整份設定驗證失敗、連 hook 一起失效。註解只能放頂層的 `_說明_*` 鍵。
- 合併版會不會踩：會，`skeleton/.claude/settings.json` 由 build 與 upgrade 寫入。測試：骨架 settings 只含已知鍵。
- 客戶會不會遇到：不會察覺。守門整個沒了。

### I-031 被擋了就換別的方法繞過
- 來源：plugin 0.13.1 `skills/query/SKILL.md:101`、`:131`、`agents/card-maker.md:77`、`rules/maintenance.md:84`。防範型。
- 發生過什麼：plugin 在多處規定：寫來源被擋不要換方法；轉換失敗不要換方法重試；程式不能跑時不要自己處理，也不要說已經做好。理由是那樣算不出指紋、無法安全備份，逐頁讀又花額度。
- 合併版會不會踩：會。D-059 的十一條沒有「被擋不繞路」，現在只能散在各 skill。要不要升成 core 一條。設計 session 2026-10-12 定：core.md 加 R-12「被擋就停下來說原因，不換方法」（D-059）。
- 客戶會不會遇到：會，原檔被另一條路改掉，或 AI 說做好了其實沒有。

### I-032 憑證檔名沒擋；保護如果是靜默的，會被下一個人拆掉
- 來源：llm-wiki 1.8.0 `scripts/repo.py:46-56`、`:125`（2026-09-26 實測）、`:82-83`、`:137-138`、`:47-48`（#26 教訓）、`skills/wiki-repo/SKILL.md:8-11`。
- 發生過什麼：實測發現目錄走訪只比對資料夾，不比對檔名，`secrets.txt`、`credentials.md`、`密碼.xlsx` 直接進 ingest 候選。修法是把憑證樣式寫死在程式碼，不放設定檔，因為放設定只保護之後才掛的來源。被擋掉的路徑一定要印出來，否則沒人知道保護存在，擋錯時第一個反應就是把它拆掉。
- 合併版會不會踩：寫死與不可關已經定了（architecture 六，資料層）。還沒寫明的是「被擋要印出來」，應該進建置報告的「讀不到的」一段。
- 客戶會不會遇到：會。沒擋的話，密碼表的內容會被送上雲端。

### I-033 隱私排除規則對到 0 份，卻當成完成
- 來源：plugin 0.13.1 `scripts/folders.py:629-632`（0.11.1，e8 K1）、`:432-438`、`skills/query/SKILL.md:160`、`:180`。
- 發生過什麼：規則寫成「行政/私人」，少了大括號（應該是「{行政}/私人」），對不到任何檔。客戶選了「不想讓 AI 看到」，結果什麼都沒刪，還當成完成。0.11.1 起對到 0 份就拒絕，不寫入規則，並提示可能少了大括號。
- 合併版會不會踩：會，`kb folders exclude` 是同一個動作。測試：排除對到 0 份時拒絕，並明講。
- 客戶會不會遇到：會。客戶以為私人資料已排除，其實沒有。

### I-034 排除之後，舊筆記、待處理、日誌裡還留著被排除的內容
- 來源：plugin 0.13.1 `scripts/folders.py:624-628`（0.10.1）、`:509-516`、`skills/ingest/SKILL.md:56`、`agents/card-maker.md:46`、`:54`、`commands/build-cards.md:30`。
- 發生過什麼：服務人員手動加的排除規則，不會清掉已經做過的卡，0.10.1 讓 `folders.py` 可以只清這些殘留。待處理裡的檔名本身就可能透露內容，所以那幾列也一起刪。依據欄空白的推測如果被補上依據，排除時就會漏掉，所以空白的舊列不補。
- 合併版會不會踩：會。D-024 把清理範圍定到 `versions/`、`temp/`、草稿，但 D-065 規定工作日誌永久保留，日誌裡的問句與「改了」行會留著被排除的檔名。兩條要怎麼並存。設計 session 2026-10-12 定：日誌遮檔名不刪行，待處理刪條目（D-024 補）。
- 客戶會不會遇到：會。排除之後，在日誌或待處理裡還看得到那些檔名。

### I-101 settings.json 不熱重載；auto mode 放行工作目錄外的讀取
- 來源：本 repo B-006 spike，2026-10-12，`design/reports/B-006-spike.md`。實測。
- 發生過什麼：session 開著時改 `.claude/settings.json`（additionalDirectories、Read allow、deny 都試），不重開完全不生效，deny canary 也要重開才被擋。另一個發現：session 在 auto mode 時，工作目錄外的 Read 沒有任何放行規則也直接讀到。重開後 additionalDirectories 與 Read 規則仍跳詢問；B-007 用純 ASCII 路徑、接回與全新 session 重測，十次全跳詢問，排除中文路徑與 resume。`Read(C:/...)` 沒有 `//` 前綴可能被當相對路徑、`${USERPROFILE}` 可能不展開、additionalDirectories 為何無效不明；`//c/` 與 `~/` 寫法未在 ASCII 下測，B-008 續。以上是 VS Code extension。B-008 在桌面版測：additionalDirectories 寫入後同一 session 立刻熱載入、重開也生效，兩個介面行為相反；PreToolUse 回 allow 可免詢問讀工作目錄外的檔，回 deny 在 auto 模式也擋（reports/B-008-spike.md）。
- 合併版會不會踩：會。(1) `kb folders add` 登記後這個對話一定讀不到新夾，U-8 只能是「登記、告知下次開場」。(2) 「不讀某夾」的隱私承諾不能靠 permissions 擋，客戶用 auto mode 時 permissions 形同沒有；只有 PreToolUse 守門 hook 擋讀取才可靠，D-034 的「併入讀檔守門」是必要不是選項。(3) 放行規則裡能不能寫 `${CLAUDE_PLUGIN_ROOT}`（I-073）仍未定，B-007 前 plugin 不假設可以。
- 客戶會不會遇到：會。加資料夾後要重開對話；用 auto mode 的客戶若守門 hook 掛了，排除的夾會被讀到。

### I-102 桌面版升級要在 GUI 點四步，且會跳假警報
- 來源：本 repo B-008 spike，2026-10-12，`design/reports/B-008-spike.md`。實測，Claude Code 桌面版。
- 發生過什麼：本機 directory 型 marketplace 的來源改了版號，重開桌面版不會更新，`/plugin update` 只會打開 GUI 管理頁。實際要：進 plugin 詳情頁、按「更新」、確認窗按「仍要更新」、重開。只要有別的 session 開著，確認窗會說「更新將覆寫本機變更」，是把 `.in_use/<pid>` 當成被改的檔，假警報，舊版資料夾不會被覆寫。更新完也不提示要重開。另外桌面版 GUI 加 marketplace 只收 https 或 owner/repo，本機路徑要用 CLI `claude plugin marketplace add "<路徑>"`。
- 合併版會不會踩：會。D-072 說客戶只要說「升級」，但目前升級最後一段是 GUI 四步加一個嚇人的假警報，`kb upgrade` 碰不到 GUI。待設計 session 確認：測 CLI `claude plugin update` 能不能由 `kb upgrade` 代跑、桌面版重開後吃不吃。
- 客戶會不會遇到：會，每次升級。

### I-103 hook 逾時或 python 不在 PATH 時，Claude Code 放行（fail open）
- 來源：本 repo 守門實作 session 的審閱，2026-10-12，`design/specs/2026-10-12-守門.md` 疑義 4。防範型。
- 發生過什麼：PreToolUse hook 若逾時（timeout 10）或命令本身起不來（python 不在 PATH），Claude Code 不會把它當 deny，工具照常執行。守門只在它跑得動的時候有效。
- 合併版會不會踩：會。D-073 要 fail closed，但這一層在 Claude Code 手上。對策三個：守門保持快（單次呼叫遠低於 timeout）；開場 hook 偵測 python 可用並印一行；B-016 查 hooks 設定有沒有「失敗即擋」的欄位與版本。
- 客戶會不會遇到：python 壞掉的那台電腦會。

### I-104 Claude Code 自己先擋的呼叫，守門看不到
- 來源：本 repo 守門確認，2026-10-13，`design/reports/守門-確認.md`。實測，CLI 2.1.296。
- 發生過什麼：Read 排除夾裡的 xlsx，Claude Code 的「不讀二進位檔」先擋，PreToolUse hook 沒跑，ledger 沒有那一筆、理由句也不是守門的。沒有外洩。
- 合併版會不會踩：會，只影響留痕完整性：ledger 記的是「守門擋的」，不是「所有被擋的」。lint 的守門趨勢以此為限。確認情境的 fixture 要用文字檔。
- 客戶會不會遇到：不會察覺。

### I-105 發行指令鏈沒以測試 exit code 把關，紅測試照樣 tag 與 push
- 來源：本 repo v0.2.0 發行，2026-10-13。實際發生。
- 發生過什麼：bump 0.2.0 後 `test_kb_cli` 寫死的「0.1.0」紅了，發行腳本用 `python -m unittest … | grep` 看結果，exit code 是 grep 的，`&&` 沒擋住，commit、tag、push 都出去了。補一個修正 commit 讓測試讀 plugin.json，tag 強制移到修正 commit。
- 合併版會不會踩：客戶端不會。repo 流程會：測試不准寫死版號（讀 plugin.json）；發行步驟測試結果以 exit code 為準，不經 grep（process 六）。
- 客戶會不會遇到：不會。

## 三、回合、備份、退回、登記

### I-035 背景代理做完的通知被當成客戶的新問題
- 來源：plugin 0.13.1 `hooks/turn.py:23-29`（0.10.1）、`skills/ingest/SKILL.md:68`、`rules/core-rules.md:148`。
- 發生過什麼：背景代理（例如製卡）做完時送進來的 `<task-notification` 也會觸發 UserPromptSubmit，於是開了新回合、記進工作日誌。0.10.1 起略過它，ingest 也規定代理一律在前景派出。
- 合併版會不會踩：會，而且影響更大。D-031 的工作日誌「問」由 UserPromptSubmit 記，D-053 多量製卡派 card-maker。測試：送一則 task-notification，不開回合、不記日誌。
- 客戶會不會遇到：會。工作日誌出現客戶沒問過的「問」，回合代號錯亂，退回也會退錯。

### I-036 對話被摺疊後，核心規則不會自己回來
- 來源：plugin 0.13.1 `hooks/session.py:7`、`:41-43`。
- 發生過什麼：compact 之後 core-rules 已經不在對話裡。plugin 在 SessionStart（compact）時提醒 AI 重讀規則，並帶入〈做到一半的事〉。
- 合併版會不會踩：可能。architecture 五說規則路徑由開場 hook 注入，只給路徑的話，摺疊後模型不一定重讀。compact 時要注入什麼。設計 session 2026-10-12 定：SessionStart 加 matcher compact，摺疊後重跑開場注入（architecture 五）。
- 客戶會不會遇到：會。長對話後半段，AI 開始改原檔或不附出處。

### I-037 同一種維護在一個對話裡重複開跑
- 來源：plugin 0.13.1 `rules/maintenance.md:31`、`hooks/session.py:46`。防範型。
- 發生過什麼：自動檢查的訊息一直留在對話裡，AI 會再看到、再跑一次。plugin 規定同一種維護一個對話只做一次，成功或失敗都算。
- 合併版會不會踩：可能。開場注入的健檢到期、變動數（D-060）會一直留在 context 裡。
- 客戶會不會遇到：會，同一件事被問兩次、額度多花一次。

### I-038 退回資料夾管理時，整份快照蓋掉後來的進度
- 來源：plugin 0.13.1 `scripts/versions.py:94-96`。防範型。
- 發生過什麼：資料夾管理的退回是整份還原來源清單與清冊，所以必須從最近的一次開始退。先退較早的那次，就會把之後的進度蓋掉。
- 合併版會不會踩：會。`kb versions` 退 `sources.json`、`inventory.json` 這類整份狀態檔時，是同一個問題。
- 客戶會不會遇到：會。退回一件事，連帶把後來加的資料夾也退掉了。

### I-039 成品不在退回範圍，換了對話就說不清檔案還在不在
- 來源：plugin 0.13.1 `scripts/ai_output.py:363-365`（0.13.1）。
- 發生過什麼：退回定稿時，成品區的檔案還在。換了對話，AI 沒辦法告訴客戶這件事，所以 0.13.1 起由程式在退回時印出來。
- 合併版會不會踩：可能。`AI 產出/` 走登記、不走備份（D-046）。退回一個產出成品的回合時，那份檔留不留、登記表怎麼改沒定。設計 session 2026-10-12 定：退回時列成品、告知不可逆、確認才刪，否則留著標記（D-011 補）。
- 客戶會不會遇到：會，退回之後不知道成品還在不在。

### I-040 客戶在 Word 按「全部接受」，檔案仍被當成 AI 產出
- 來源：plugin 0.13.1 `scripts/ai_output.py:179-181`（議題 B，0.13.1）。
- 發生過什麼：接受修訂後文字指紋不變，但修訂標記沒了，代表客戶已經採用。0.13.1 起改看檔內還有沒有作者「AI 助理」的修訂標記，沒有就判成原檔。
- 合併版會不會踩：會，而且設計方向不同。D-048 定了「類別跟著檔走，搬動後不變」，D-043 定了 AI 產出搬進實體夾仍標 AI 產出，所以客戶採用之後也永遠是 AI 產出。要不要允許從 AI 產出變成原檔。設計 session 2026-10-12 定：類別不變、清冊記改自檔頭渲染、客戶明說才轉、檔案卡不設紀錄段（D-048 補）。
- 客戶會不會遇到：會。answer 把客戶自己定稿的合約說成「AI 寫的草稿」。

### I-041 登記表的身分與位置錯置
- 來源：plugin 0.13.1 `scripts/ai_output.py:117-127`（0.13.0）、`:150-152`（0.12.1）、`:85`（0.12.1）、`:340`。
- 發生過什麼：同一份內容另存兩處時，參考與來歷借到別處那一筆（0.13.0 修）。兩份參考用逗號接成一個字串，被記成一個元素（0.12.1 會這樣）。登記用相對路徑，跟 hook 記的絕對路徑對不上（0.12.1 修）。同一回合定稿兩份同名草稿會互蓋。
- 合併版會不會踩：大部分不會。清冊改成 id 加位置清單、重複檔一張卡列多個位置（D-014）。同名互蓋與路徑正規化仍要測。
- 客戶會不會遇到：會，出處講錯，或少一份成品。

### I-042 檔頭前面有空白，程式讀不到，AI 起草的被當成客戶原檔
- 來源：plugin 0.13.1 `agents/card-maker.md:41`、`rules/page-formats.md:21`、`scripts/kbcommon.py:617`、`scripts/convert.py:182`。
- 發生過什麼：`parse_front_matter` 會跳過以空白開頭的行，出處類別讀不到就預設成「原檔」，AI 起草的文件就被當成客戶的資料。另一條：用 Edit 修舊卡時只把範本版本改了，沒補新欄位。
- 合併版會不會踩：會。檔頭是機器欄位（D-037），由製卡者（模型）寫出來。解析器要嚴格，讀不到時要報錯，不能給預設值；升級遷移檔頭要由程式做（D-054）。
- 客戶會不會遇到：會，出處類別講錯（D-048）。

### I-043 只標〔AI 起草〕、沒附連結，被健檢當成沒出處刪掉
- 來源：plugin 0.13.1 `rules/page-formats.md:58`、`skills/lint/SKILL.md:52`、`:60`、`:119`、`scripts/health.py:222`。防範型。
- 發生過什麼：健檢會刪掉沒有出處的句子。只寫〔AI 起草〕、或在矛盾段加一句「等客戶判斷」，都會被當成沒出處而刪掉。客戶的說明要寫成固定格式，下次健檢才不會被刪。
- 合併版會不會踩：會。D-038 規定綜合卡每句帶出處，D-064 有「出處 id 指得到」一項。lint 自動修是刪還是標，要先定好；連「你 YYYY-MM-DD 說的」這種出處也要認得。
- 客戶會不會遇到：會，客戶交代過的事被健檢刪掉了。

## 四、狀態檔與對帳

### I-044 絕對路徑寫進狀態檔與卡片，換電腦就全部失效
- 來源：llm-wiki 1.8.0 `scripts/_lib/vaultpaths.py:82-91`（初版 6d3db33；#27；2026-09-21 使用者裁示）、`scripts/repo.py:6-8`、`skills/wiki-repo/SKILL.md:145-147`、`:157`、`skills/wiki-doctor/SKILL.md:32`（0.4.15 只修了 manifest）。
- 發生過什麼：初版把 `C:/Users/<某人>` 直接存進 manifest，換電腦、換使用者名稱或搬位置，所有來源一起失效。0.4.14 遷移成可攜格式，但 0.4.15 只修了 manifest，卡片裡的路徑還是絕對路徑，又另外補了 `fixpaths`。
- 合併版會不會踩：不會。D-021 補述存可攜 spec，D-064 有絕對路徑檢查，卡、綜合卡、CLAUDE.md、settings 都不准有絕對路徑。測試：搬家之後 lint 為 0。
- 客戶會不會遇到：防住了就不會。

### I-045 同步到另一台電腦，每次開場都報「路徑失效」
- 來源：llm-wiki 1.8.0 `scripts/vault_state.py:289-290`、`scripts/test_vault_state.py:20-25`（ISS-004，1.8.0）。
- 發生過什麼：manifest 跟著 git 走，掛載路徑只在註冊者的電腦上存在。其他人每次開場都看到「路徑失效」。原文說代價不是那一行字，而是會訓練所有人忽略整個老化訊號區。修法是比對擁有者機器，認得是別人的機器就標明。
- 合併版會不會踩：可能。單人第一版不用 git（D-023），但客戶有兩台電腦、總資料夾放在同步夾裡時，`sources.json` 會同步過去，另一台的外部來源路徑可能不存在。D-050 補述已經處理憑證綁單機，來源路徑沒處理。設計 session 2026-10-12 定：找不到的來源按機器記、報一次、不當刪除（D-021 補）。
- 客戶會不會遇到：會（兩台電腦的客戶）。開場天天報錯，客戶學會不看。

### I-046 狀態檔混進備註或不合格式的值，開場 hook 靜默無輸出
- 來源：llm-wiki 1.8.0 `scripts/_lib/vaultpaths.py:46-55`（2026-09-22 成熟 vault 實測，#33）、`scripts/repo.py:336`、`:601`、`:670`、`:749`、`:957`、`scripts/vault_state.py:26`、`:233`、`:522`。
- 發生過什麼：manifest 的 sources 混進 `_note` 這類人寫的備註 key 和純文字值，直接迭代就 AttributeError。對帳整支掛掉，SessionStart hook 什麼都不印，來源數多算，備註 key 被當成「來源檔消失」。之後各段檢查各自 try/except，不讓開場整個失效。
- 合併版會不會踩：可能。`.system/*.json` 只准程式寫（conventions），但客戶或服務人員手改、或同步衝突留下半份檔時，一樣會壞。測試：任一狀態檔壞掉時，開場仍要印其他項，並把錯誤寫進 `hook-errors.log`。
- 客戶會不會遇到：會。開場什麼都不印，客戶以為沒事。

### I-047 卡片寫了卻沒登記，永遠不會被報過期
- 來源：llm-wiki 1.8.0 `scripts/vault_state.py:297-300`、`scripts/repo.py:771-772`（2026-09-21 AS9100 指標卡，#28／#32）、`skills/wiki-ingest/SKILL.md:216`。
- 發生過什麼：指標卡手寫進 catalog/，漏了 ingest 的登記步驟。卡看起來在、內容也對，但掃描永遠不會報它過期，變成會無聲腐爛的死卡。#28 加偵測，#32 加 `repo.py link` 預防。
- 合併版會不會踩：可能。檔案卡由製卡者（模型或 card-maker）寫，登記清冊由 `kb` 做，兩步之間可能斷開。D-064 有「登記表對實際檔」一項。測試：有卡沒登記、有登記沒卡，兩種都要報。
- 客戶會不會遇到：會，答案引用一張已經過期的卡。

### I-048 綜合頁沒有過期偵測，大多已經死掉
- 來源：llm-wiki 1.8.0 `scripts/_lib/derived.py:3-10`（2026-09-26 實測）。
- 發生過什麼：某成熟 vault 的 8 頁綜合頁都宣告了 sources，7 頁超過 3 個月沒更新，1 頁已停用。有 hash 偵測的指標卡都還活著。原文：綜合層會死不是因為綜合沒價值，是因為沒有過期偵測。
- 合併版會不會踩：不會。D-038 的依據卡 id 可以反查，重做時自動標待更新，D-064 也有這一項。這條是複利層存活的前提，一定要有測試。
- 客戶會不會遇到：防住了就不會；沒防住，「王宅現在怎樣」會答出舊狀態。

### I-049 新欄位只套用在之後新增的條目，舊的完全沒保護
- 來源：llm-wiki 1.8.0 `scripts/repo.py:226-232`（#26，2026-09-21）、`:47-48`。
- 發生過什麼：「種類」欄只在新增來源當下判定，0.4.12 之前掛的條目都沒有這一欄，防護對已經掛錯的完全無效。實例：某來源指向整個 vault 根（raw 1396 份），卻標成「文件」。改成每次重建前回填。
- 合併版會不會踩：可能。D-014 規定狀態檔帶 schema_version、升級跑遷移。每加一個欄位，就要有遷移把舊條目補上。測試：用舊版 fixture 升級後，新欄位全部有值。
- 客戶會不會遇到：會，舊客戶拿不到新版的保護。

### I-050 把另一個已編譯的知識庫當來源，同一份知識存兩處
- 來源：llm-wiki 1.8.0 `scripts/repo.py:197-200`、`:356-357`、`:445-446`、`:479-482`、`:574-575`（2026-09-21，#23）。
- 發生過什麼：掛上別人已編譯的 vault，工具照樣建議「批次 ingest 整個 repo」。對方的 wiki/ 已經是編譯過的知識，再 ingest 一次就存兩處，對方更新時這邊不知道，兩邊無聲漂掉。
- 合併版會不會踩：可能。既有 wiki 接入（B-211）與 `kb://`（B-207）都延後了，但客戶仍可能把舊的 Obsidian 庫或另一個總資料夾加成來源，結果是替卡片再做卡片。inventory 要不要偵測並擋下。設計 session 2026-10-12 定：`kb folders add` 偵測到另一個知識庫的標記就拒絕，指 B-207（architecture 四）。
- 客戶會不會遇到：會，同一件事有兩張卡、兩種說法。

### I-051 候選資料夾取最深的那層，專案根消失
- 來源：llm-wiki 1.8.0 `scripts/repo.py:541-544`（2026-09-21 驗收）、`skills/wiki-repo/SKILL.md:68`（0.4.3）。
- 發生過什麼：計數是遞迴的，父層份數一定大於等於子層。取最深的命中，等於保證丟掉「一個專案一層」那一列。實例：專案根有 1040 份 Office 檔，結果消失，被切成 7 個子來源。改成父層優先。
- 合併版會不會踩：會，`kb folders discover` 要掃上層並列候選夾與 Office 份數（architecture 四）。
- 客戶會不會遇到：會，加資料夾時看到切得很碎的清單。

### I-052 分批門檻設太高，一次建議製卡上百份
- 來源：llm-wiki 1.8.0 `scripts/repo.py:38-42`（#25）、`scripts/repo.py:106-107`（`--depth` 差一，2026-09-21 驗收）。
- 發生過什麼：批次門檻原本是 200。某份二階程序書有 139 份，剛好在門檻下，於是被建議一口氣全部 ingest。門檻改成 30。原文：批次的上限是「你願意一次看完的量」，不是「檔案系統撐得住的量」。
- 合併版會不會踩：可能。架構說 ingest 要判斷太多時要不要分批，數字住 config。初始值要照這個量級定。
- 客戶會不會遇到：會，一句「我加了新檔」就用掉一大筆額度。

### I-053 改名或重掛來源時，只搬了一半的參照
- 來源：llm-wiki 1.8.0 `scripts/repo.py:768-770`、`:952-954`（#31）、`:1010-1013`（2026-09-21 實測）、`scripts/vault_state.py:317-320`（#30）、`scripts/repo.py:726-727`（驗收 #14）。
- 發生過什麼：沒有改名工具，只能手改 JSON，而 skill 第一行就寫「不要手改 manifest」。原文：規則叫人別做的事，工具卻不提供替代方案，那條規則就一定會被違反。手動把來源 foo 拆成 foo-raw 之後，文件索引仍寫 `foo::…`，目錄頁連到死路徑；刪掉最後一個來源後，其他頁的連結都變死連結，而且不報錯。
- 合併版會不會踩：大部分不會。`kb folders relocate` 與 `kb source-edit rename` 有工具（architecture 四），清冊主鍵是 id（D-014），總目錄由程式渲染（D-067）。測試：relocate 之後 lint 的「引用不存在的檔」與「出處 id 指得到」都要是 0。
- 客戶會不會遇到：防住了就不會。

### I-054 「我在別人電腦上」被判成「路徑失效」，或往上找到外層的 repo
- 來源：llm-wiki 1.8.0 `scripts/vault_state.py:126-128`、`:82`、`scripts/repo.py:245-246`、`:250`。防範型。
- 發生過什麼：用 `git rev-parse` 判斷有沒有版控，會往上找到外層 repo（例如家目錄本身就是 repo）。用絕對路徑判斷隱藏檔，父目錄名以 `.` 開頭時，整個 vault 都被排除。抓不到擁有者時，不可以猜成「別人的」。
- 合併版會不會踩：會。plugin 用標記檔往上找總資料夾（D-003），同一類的錯是找到外層的另一個總資料夾。D-003 已經因此把測試 vault 移到 repo 外。測試：巢狀總資料夾要認最近的那一個。
- 客戶會不會遇到：少見。客戶把總資料夾建在另一個總資料夾裡面時會遇到。

## 五、守門留痕與假訊號

### I-055 守門腳本壞了 10 天，沒人發現
- 來源：llm-wiki 1.8.0 `scripts/_guard_status.py:3-4`、`skills/wiki-doctor/SKILL.md:75-80`。
- 發生過什麼：原 vault 的 tidy-check 在 Python 3.11 掛了 10 天沒人發現。守門跑完輸出就消失，「沒跑」「跑掛」「跑出 CLEAN」事後分不出來。因此加了「我跑過了」的留痕。
- 合併版會不會踩：不會。guard-status 與失聯判讀是 D-064 的一項。測試：守門腳本拋例外時不更新 guard-status，lint 就報失聯。
- 客戶會不會遇到：防住了就不會。

### I-056 多台電腦共寫一筆留痕，蓋掉別台的失聯
- 來源：llm-wiki 1.8.0 `scripts/_guard_status.py:8-14`、`skills/wiki-doctor/SKILL.md:82-85`。防範型。
- 發生過什麼：所有機器共寫同一筆時，B 機今天跑過，就把 A 機「60 天沒跑」蓋成「今天跑過」。改成一台一個檔，判讀分「全體失聯」與「單機停跑」。從沒跑過的機器不算逾期，否則第一次執行就一排假陽性。
- 合併版會不會踩：不會。`guard-status/<機器>.json` 已經定了（D-064）。「從沒跑過不算逾期」要寫進測試。
- 客戶會不會遇到：防住了就不會。

### I-057 帶過濾的查詢寫進留痕，趨勢看起來變好或變壞
- 來源：llm-wiki 1.8.0 `scripts/agenda.py:355-356`、CHANGELOG 1.7.2、`CLAUDE.md:66-84`（2026-10-10 第一版判準被實證駁回）、`scripts/stale_check.py:151`。
- 發生過什麼：`--days`、`--tag` 是文件明文教的查詢，但會蓋掉完整掃描的結果，趨勢誤顯示成「5 項降到 1 項」。第一版的修法是「加了過濾參數就不落盤」，被 vault 側用四支腳本的實證駁回：只縮顯示範圍的旗標也會被擋，一次完整掃描反而不算「跑過」，結果觸發假的「守門失聯」。判準要看「結果是不是全量」，不是看「有沒有帶參數」。
- 合併版會不會踩：會，`kb lint` 有分項、`--json` 等參數，`ledger.tsv` 只在結果變化時寫（D-064）。測試：帶範圍的執行不寫 ledger，只改顯示的旗標照寫。
- 客戶會不會遇到：不會直接看到。服務人員會看到錯的趨勢。

### I-058 本機 plugin 比骨架舊，對帳卻報「已是最新」
- 來源：llm-wiki 1.8.0 `scripts/skeleton_check.py:200-201`、`:262-264`、`scripts/test_skeleton_check.py:251-256`、`:274`（ISS-002，1.8.0）。
- 發生過什麼：`classify` 設了 downgrade 旗標，但沒有任何地方讀它，輸出照樣是「🟢 骨架已是最新」。骨架紀錄跟著 git 走，誰先升級，沒升級的人都被告知「已是最新」。測試註明 bug 在輸出層，只測旗標測不到。
- 合併版會不會踩：可能。單人單機第一版少見，但換電腦或還原備份後裝到較舊的 plugin 時會出現（architecture 四）。D-064 的版本對齊要分「比較新」和「比較舊」兩個方向，測試要跑真正的 CLI 輸出。
- 客戶會不會遇到：少見。遇到時客戶被告知不用升級，其實要升。

### I-059 穩定的假陽性，訓練大家無視整個健檢
- 來源：llm-wiki 1.8.0 `scripts/rules_check.py:116-117`（ISS-003，1.8.0）、`scripts/_lib/derived.py:37-43`、`:76-78`（#100 教訓）、`scripts/tidy_check.py:204-206`（#29）、`skills/wiki-collabteam/04-查核.md:80-89`、CHANGELOG 1.8.0。
- 發生過什麼：正規表示式對不上帶佔位符的 `raw/members/<人>/`，多人 vault 每次開場都誤報。綜合頁檢查上線當天就對主題樁報假陽性。網址、LINE 對話這種合法來源被報成「不存在」。某檢查報 66 筆、大多是合法副本，數字永遠停在 66，大家學會「verdict 可以無視」，之後的新訊號也一起被無視。之後定下規矩：新偵測器上線第一天必須是 🟢。
- 合併版會不會踩：會，lint 有十七項（D-064）。測試：剛建置完的測試環境跑 lint，每一項都要是 0；非檔案出處（「你 YYYY-MM-DD 說的」、對話收件）不能被報成找不到。
- 客戶會不會遇到：會，客戶學會跳過「有 N 件要你看」。

### I-060 開場與健檢把現況講錯
- 來源：llm-wiki 1.8.0 `scripts/vault_state.py:167-168`（2026-09-21 驗收）、`:58`、`scripts/coach.py:343-345`（2026-09-22 驗收）、`scripts/_lib/derived.py:15-17`（2026-09-26 實測）。
- 發生過什麼：素材早就編完了，開場還一直叫人 `/wiki-ingest`。剛建好的 vault 被算成 8 頁，其實應該是 0。已經有卡了，coach 還只教 ingest、不教查詢。用時間判斷的查詢會隨執行時刻漂移，而且不報錯。
- 合併版會不會踩：大部分不會。D-060 規定開場只印數字、沒事的項不印。測試：剛建置完的開場注入只印該印的項。
- 客戶會不會遇到：會，開場叫客戶做已經做完的事。

### I-061 規則層沒人驗證，慢慢漂掉
- 來源：llm-wiki 1.8.0 `scripts/rules_check.py:5-7`（成熟 vault 實測，2026-09-20）、`:92-93`。
- 發生過什麼：健檢只守知識層，不守 CLAUDE.md 與規則頁。某成熟 vault 的產品清單漏了 2 個近半年的產品，停用裁示只傳到 2 個檔，規則層有 14 行逐字重複，腳本登記率 58%。遷移進來的 SKILL.md 還引用著來源 vault 的腳本。
- 合併版會不會踩：大部分不會。規則與個案分兩個 JSON（D-041），`drift.py` 查客戶總資料夾引用不存在的東西（architecture 六）。還沒有的是「設定檔裡同一條規則寫兩次」的檢查。
- 客戶會不會遇到：會，規則互相矛盾，AI 時好時壞。

## 六、骨架升級與遷移

### I-062 骨架建好後永遠停在那天，plugin 升級了也沒人發現
- 來源：llm-wiki 1.8.0 `scripts/skeleton_check.py:4-7`、CHANGELOG 1.5.0、`README.md:177-179`、`skills/wiki-init/SKILL.md:138-141`、`docs/specs/2026-10-09-vault-skeleton-upgrade.md:7-19`。
- 發生過什麼：1.1.1 新增 `start.md`、`end.md`，開工收工的 skill 在舊 vault 能叫，但它要讀的檔不存在，默默失效。skill 新增「狀態欄寫登記日」的格式要求，舊 vault 的檔沒有，T9 檢查永遠只報個數。被 `.skeleton-policy` 排除的檔，連格式變更都偵測不到。
- 合併版會不會踩：不會。`kb upgrade` 加 `lint/checks/skeleton.py`（architecture 四、六）。被排除的檔偵測不到格式變更這個破口，要在骨架設計時避開。
- 客戶會不會遇到：防住了就不會。

### I-063 升級把一開始就客製的檔判成「可安全換」，整份覆蓋
- 來源：llm-wiki 1.8.0 `scripts/skeleton_check.py:243-246`、`scripts/test_skeleton_check.py:165-170`、spec `§11.6`（llm-wiki-aegiverse-e4 2026-10-09 review 實跑驗出）。
- 發生過什麼：「上次對帳後沒再動」被當成「等於樣板」。CLAUDE.md 與規則頁建 vault 當天就是真規則層，常常幾個月沒人動，樣板一改就落進「可安全換」，被整份覆蓋，連 `{{name}}` 都沒代換。
- 合併版會不會踩：會，`kb upgrade` 的骨架三組是同一套邏輯。測試：建置當天就跟樣板不同、之後沒再改的檔，升級時一定要問。
- 客戶會不會遇到：會。客戶設定或總資料夾 CLAUDE.md 被升級蓋掉。

### I-064 升級把還沒處理的檔一起封存，報告變綠
- 來源：llm-wiki 1.8.0 `scripts/skeleton_check.py:279-281`、`:340`、`scripts/test_skeleton_check.py:148-149`、spec `§11.3`（2026-10-09 對 wiki-test 複本實跑）。
- 發生過什麼：`--apply-safe` 跑完報告變 🟢，但 5 個待融合的檔根本沒融合，它們被記成新基準，等於宣告已對帳，下次不再報。原本的測試只驗「缺檔補完了」。
- 合併版會不會踩：會，`kb upgrade --apply` 是同一個動作。測試：只封存真的處理過的檔。
- 客戶會不會遇到：會。升級後該處理的事永遠不會再被提起。

### I-065 補缺檔沒代換佔位符；比對時把雜訊當差異
- 來源：llm-wiki 1.8.0 `scripts/skeleton_check.py:114-115`、`:130-135`、`:146-147`、`:189`、`:327`、`scripts/test_skeleton_check.py:183`、`:192-193`、`:228-229`、spec `§11.1`、`§11.6`。
- 發生過什麼：補缺檔用 `copyfile`，`updated: {{today}}` 原樣寫進檔（2～3 個檔中招）。第一次實跑 13 項裡有 4 項是雜訊：標題後補了註解就被報成一加一減，佔位符行一行造成兩筆差異。整行只有佔位符時，比對式變成 `^.*$`，整個檔的差異被抹平。樣板刪掉一條規則不會被報，理由字串還寫錯。政策檔的 glob 在樣板改名後變成死規則。
- 合併版會不會踩：會，骨架對帳是從 llm-wiki 搬的（D-001）。這幾條都要成為 `test_skeleton_check.py` 的案例。
- 客戶會不會遇到：不會直接看到。服務人員升級時被雜訊淹沒，或漏掉真的變更。

### I-066 重跑建置把既有的日誌與裁示蓋掉
- 來源：llm-wiki 1.8.0 `scripts/init_vault.py:60-61`（#33）、`skills/wiki-init/SKILL.md:67-69`。
- 發生過什麼：dry-run 把 19 個既有檔全標成「會建立」，`--force` 真的會蓋掉 log.md。CLAUDE.md、log.md、rulings.md 存的是累積的裁示與日誌，被覆寫就是無聲清空。改成既有檔永遠不碰。
- 合併版會不會踩：可能。build-setup 遇到已經有 `.system/` 的總資料夾時要怎麼做，architecture 沒寫。測試：對既有總資料夾跑 build-setup，`--check` 不能列出覆寫既有檔。
- 客戶會不會遇到：會，客戶自己重跑建置就把歷史清空了。

### I-067 功能改了、文件或樣板沒跟上，留下死指令
- 來源：llm-wiki 1.8.0 CHANGELOG 1.7.3、1.7.1、1.7.0、1.5.2、`scripts/test_template_coverage.py:9-10`、`:83-85`、`:137-141`、`:158-161`、`scripts/repo.py:323-325`、`CLAUDE.md:101-106`。
- 發生過什麼：1.6.0 刪了三個 command，自己的文件還有 9 處在教，其中一處在樣板裡，已經透過骨架升級散播出去。roles/ 只加了範本沒加資料夾；init-history.md 在 spec 裡、實作卻漏了。文件寫 repos.md 會自動產生，0.4.2 以前其實沒有程式會產生它。原文：作者比使用者更容易漏，因為不會去讀自己剛寫的檔。
- 合併版會不會踩：會，尤其是合併期間要把十幾個舊名改成六個 skill。`test_coverage.py` 要檢查：文件教的指令都叫得到；文件說會產生的檔，程式都產生得出來。
- 客戶會不會遇到：會，照說明打指令卻失敗。

### I-068 改完 plugin，已經開著的對話還在用舊版
- 來源：llm-wiki 1.8.0 CHANGELOG pixel-office 未發版段、`CLAUDE.md:118-121`（2026-10-08）。
- 發生過什麼：plugin 在 session 啟動時載入，已經開著的視窗不會重讀，使用者看到的是一兩天前開的 session 載入的舊版。`claude plugin prune` 不清舊版目錄，舊版一直堆積，grep 結果被舊版路徑塞滿、截斷，結果讀到舊版規則。cache 底下的 `temp_git_*` 殘留也會被誤認成版本目錄。
- 合併版會不會踩：會。D-060 規定開場 hook 版本不符時印一行，但已經開著的對話不會再跑開場。升級說明要告訴客戶關掉重開。
- 客戶會不會遇到：會，升級後說「沒變啊」。

### I-069 拷貝一定會漂
- 來源：llm-wiki 1.8.0 `scripts/init_vault.py:4-5`、`docs/design.md:181-182`、`skills/wiki-init/SKILL.md:19-22`、CHANGELOG 首段（ISS-005）。
- 發生過什麼：舊做法是每個範本各帶一整套 skills 拷貝，實測數量分岔成 18、18、30、31，而且沒人發現。vault 端自己抄的版本紀錄，開張當天就落後兩版。
- 合併版會不會踩：不會。系統由 plugin 提供，CHANGELOG 是版本唯一真相（architecture 六），conventions 規定一件事只住一個地方。
- 客戶會不會遇到：不會。

### I-070 同名 skill 到底載入哪一份，誰都沒實測
- 來源：llm-wiki 1.8.0 `skills/wiki-adopt/SKILL.md:24-28`（2026-09-21 實測）、`:53-70`（驗收 #22）、`scripts/adopt.py:83-87`、`:155-158`、`:200-203`。
- 發生過什麼：實測 vault 根目錄的 `skills/` 不會被載入，以前以為會。使用者層的 skill 是 symlink，指到第三個 vault，使用者以為在用本地那份。同名 skill 哪份生效，兩個 session 都只有推論、沒有實測，照推論刪檔就會刪錯。同名的 vault 版 wiki-ingest 跟 plugin 版差 558 行、章節完全不重疊，其實是另一套實作。
- 合併版會不會踩：可能。plugin 改名 `wiki-with-willy` 避開了跟已安裝 `llm-wiki` 撞名（D-069），但開發機上兩套同時裝著，skill 描述裡的觸發詞仍可能互搶。路由 eval 要在兩套都裝的環境跑一次。設計 session 2026-10-12 定：路由 eval 在兩套都裝的機器上跑（conventions 五）。
- 客戶會不會遇到：客戶端只裝一套時不會。服務人員的電腦會。

### I-071 搬遷舊內容時，真正有價值的東西被漏掉
- 來源：llm-wiki 1.8.0 `scripts/adopt.py:29-34`（驗收 #21）、`:111-114`（驗收 #16）、`:276-279`、`:302-304`（2026-09-22）。
- 發生過什麼：搶救清單只認中文關鍵字，英文 skill 738 行的獨有內容只撈到 5 行，結果回報「需搶救 0」。掃整份 skill 時，8 行 plugin 原文把真正該搬的 3 行埋掉。退役舊 vault 時，根目錄的 *.md 整層被漏掉，最後是人工逐檔撈出四樣有價值的東西。
- 合併版會不會踩：第一版不會，既有 wiki 接入延後了（B-211）。到時要用這幾條當驗收。
- 客戶會不會遇到：第一版不會。

### I-072 舊版建的總資料夾缺節缺欄，靠重跑 init 補
- 來源：plugin 0.13.1 `README.md:24`（0.4.0）、`scripts/kbcommon.py:199`（0.6.0）、`:481`（0.9.0）、`:457`（0.11.0）、`:493`（0.13.0）、`scripts/init_kb.py:125`、`:222`。
- 發生過什麼：0.4.0 以前建的沒有健檢紀錄，開對話時不會提醒健檢；0.6.0 以前沒有〈做到一半的事〉；0.9.0 以前來源清單沒有「共用」；0.11.0 以前推測表沒有「依據」欄；0.13.0 以前沒有〈辦公軟體〉。全靠重跑 init 補，或程式碼裡照預設值處理。
- 合併版會不會踩：不會。D-014 已經以此為由，改成 schema_version 加遷移。
- 客戶會不會遇到：不會。

### I-073 plugin 升級後路徑變了，放行規則失效
- 來源：plugin 0.13.1 `README.md:35`、`scripts/init_kb.py:19`、`:65-66`、`:75-91`（0.10.1）。
- 發生過什麼：放行規則寫的是 plugin 的絕對路徑，升級後路徑變了，舊規則無效，客戶每次執行都被問。要重跑 `init_kb.py` 拿掉舊規則、寫新的。另外 additionalDirectories 會連寫入一起開放，所以 plugin 的唯讀放行改用 `Read(//c/...)`；Windows 上 Claude Code 把 `C:\x` 寫成 `//c/x`。
- 合併版會不會踩：可能。D-019 不把 plugin 路徑寫進狀態檔，但 `skeleton/.claude/settings.json` 要放行 `kb.py`，permission 規則裡能不能用 `${CLAUDE_PLUGIN_ROOT}` 沒驗證過。跟 B-006 的 spike 一起驗。設計 session 2026-10-12 定：B-006～B-008 驗完，kb.py 放行走守門 hook allow，settings 不含 plugin 路徑（D-073）。
- 客戶會不會遇到：會，升級後核准詢問一直跳。

### I-074 沒按信任，放行規則全部不生效；加了資料夾，這個對話讀不到
- 來源：plugin 0.13.1 `commands/build-finish.md:14-16`、`commands/build-setup.md:70`、`scripts/envcheck.py:73-75`（0.11.0）、`:198-200`、`scripts/folders.py:24`、`:320`、`commands/build-inventory.md:28`、`skills/query/SKILL.md:153`、`:170`。
- 發生過什麼：總資料夾沒按信任時，所有放行規則都不生效，讀來源、執行程式都會一直被問，自動模式也會被擋。程式讀 `~/.claude.json` 的 `hasTrustDialogAccepted` 判斷，這不是公開文件寫的格式，讀不到就回報看不出來。加入資料夾後，讀取權限要開新對話才生效。
- 合併版會不會踩：會。信任檢查在 `--check` 裡（architecture 四）；加資料夾要不要等下次開對話就是 U-8、U-15。
- 客戶會不會遇到：會，兩個都會。

## 七、製卡、回答、對話判斷

### I-075 卡型選錯：會改版的文件做成完整摘要卡
- 來源：llm-wiki 1.8.0 `skills/wiki-ingest/SKILL.md:245-246`（驗收 #20，2026-09-21）。
- 發生過什麼：完整摘要卡的前提是文件已經凍結。它如果還在改版，就代表當初卡型選錯，要問使用者是否改建指標卡，不能默默沿用。
- 合併版會不會踩：可能。D-032 說抄多少看檔案大小，沒提「這份會不會一直改」。`carding.md` 要加一句。
- 客戶會不會遇到：會。進度表這類常改的檔，卡片很快就過期。

### I-076 全域搜尋把 A 的規格答成 B 的
- 來源：llm-wiki 1.8.0 `skills/wiki-query/SKILL.md:37-63`、`docs/design.md:24`、`skills/wiki-doctor/SKILL.md:33`。防範型，原文未述具體實例。
- 發生過什麼：多來源共存、術語重疊時，全域 grep 把「關鍵字一起出現」誤判成「同一件事」。只靠紀律一定會被違反，所以做了三層隔離：實體、scope 標記、紀律。
- 合併版會不會踩：可能。實體層與出處標實體（D-033、D-036）是對應物，`kb search` 第一版直接讀檔案索引（D-053）。測試資料裡的信義王與大安陳都有報價，可以直接驗。
- 客戶會不會遇到：會，答出別的案子的金額。

### I-077 一處查無，就說「沒有」
- 來源：llm-wiki 1.8.0 `skills/wiki-query/SKILL.md:92-96`、`skills/wiki-core/SKILL.md:216`、`templates/vault/CLAUDE.md:41`。
- 發生過什麼：採購單腳本掃不到制度建立前的庫存，受控表單腳本掃不到公司自用的表單。規定要每一處都查過才能說查無，並講出查了哪幾處。
- 合併版會不會踩：會，`answer/references/listing.md` 要有這條。architecture 原則 5 的「查無條件」要寫明。
- 客戶會不會遇到：會，「今年報價幾次」少算。

### I-078 PDF 抽出來的數字錯得很一致
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/07-技術提醒.md:46`。防範型。
- 發生過什麼：非對稱公差這類堆疊符號抽出來是錯的，而且前後一致，不會自己露出破綻。涉及數值要回去看原圖。
- 合併版會不會踩：會。R-03 數值回正本讀，但正本是 PDF 時讀到的還是抽出來的文字。圖片型 PDF 已延後（B-101）。
- 客戶會不會遇到：會（工程類客戶）。室內設計的尺寸標註也可能有這個問題。

### I-079 太常見的人或主題，把所有綜合頁併成同一組
- 來源：plugin 0.13.1 `scripts/health.py:117-120`、`:138-139`、`:157`、`rules/maintenance.md:50`。防範型。
- 發生過什麼：老闆、公司名這種出現在四成以上卡片的實體不拿來分組，否則全部頁面併成一組比矛盾。待更新、已停用的頁不參與比對，否則比出假矛盾。依據卡還在等重做時，先不重寫。
- 合併版會不會踩：會，lint 的矛盾檢查與實體掛載（D-036）都會碰到。客戶本人的公司通常會被每張卡掛上。
- 客戶會不會遇到：會，待處理被假矛盾塞滿。

### I-080 平行的製卡代理同時改客戶設定檔，互相蓋掉
- 來源：plugin 0.13.1 `agents/card-maker.md:46`、`:57`、`commands/build-sample.md:21`。防範型。
- 發生過什麼：平行的代理可能同時改設定檔，整份覆寫就互相蓋掉。plugin 的規定是寫入前重讀，用 Edit 加條目。
- 合併版會不會踩：可能。`profile.json` 與 `entities.json` 只准程式寫，鎖檔防重入（architecture 四）可以擋住，前提是 card-maker 一律經過 `kb` 寫入（D-053）。測試：兩個程序同時寫，結果兩筆都在。
- 客戶會不會遇到：不會察覺。建置時推測的型別或實體少了幾筆。

### I-081 開對話的快速比對被大檔卡住
- 來源：plugin 0.13.1 `scripts/changes.py:17`。防範型。
- 發生過什麼：開對話時的快速比對最多只讀 200 MB 算指紋，避免開場卡住。
- 合併版會不會踩：會，開場變動偵測是 hook（D-060），hook 有逾時。測試資料要放一個大檔。
- 客戶會不會遇到：會，開對話要等很久，或 hook 逾時什麼都不印。

### I-082 刪錯「預計刪除」的那份，筆記跟著被拿掉
- 來源：plugin 0.13.1 `skills/query/SKILL.md:103`、`skills/ingest/SKILL.md:111`、`skills/query/SKILL.md:158`、`:164`、`rules/core-rules.md:131`。防範型。
- 發生過什麼：客戶沒講清楚是哪一份時，要列候選讓他挑，不要猜，記錯的話客戶一刪，筆記就被拿掉。客戶只回「好」「嗯」時，不執行刪除或保留，執行「保留」會讓之後不再問。
- 合併版會不會踩：會，ingest 要判斷「刪的要不要問」（architecture 三），R-06 是對應的規則。
- 客戶會不會遇到：會，該留的卡被刪了。

### I-083 一個「好」分不清是在答哪一題
- 來源：plugin 0.13.1 `rules/office.md:51`、`skills/query/SKILL.md:128`、`:165`、`:168`、`rules/maintenance.md:20`。防範型。
- 發生過什麼：同時有兩個待確認的問題時，客戶回「好」，AI 分不出是答哪一題。plugin 規定有其他待確認題時，那則回答不加健檢提醒。
- 合併版會不會踩：會。R-11（回答末尾建議下一步）會讓每則回答多一個問題，跟待確認題疊在一起。
- 客戶會不會遇到：會，AI 照錯的那一題執行。

### I-084 說「接下來我會…」就結束，其實沒做
- 來源：plugin 0.13.1 `skills/query/SKILL.md:35`、`skills/ingest/SKILL.md:34`、`skeleton/CLAUDE.md:6`。
- 發生過什麼：plugin 在三處規定不要只說「接下來我會…」就結束回答。
- 合併版會不會踩：可能。D-059 的十一條沒有這條；D-065 的「做了」行由腳本寫進回合帳，可以拿來對：回答說做了，回合帳卻沒有。要不要加成規則或 lint 項。設計 session 2026-10-12 定：core.md 加 R-12「要做的事在同一回答做完」（D-059）。
- 客戶會不會遇到：會，以為事情做完了。

### I-085 已經定案的事變成新待辦，被反覆重提
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/03-回報與節奏.md:55-73`、`skills/wiki-day/end-manager.md:29-31`、`skills/wiki-doctor/SKILL.md:89-94`。
- 發生過什麼：使用者裁示之後，同一筆 commit 又衍生出「雖然定案了，但還有 X 風險」的新待辦，日後被當成未辦事項重報。使用者原話：「我之前明明提過定案，你今天卻還跟我說要做這兩件事。」另一件：收尾清快照時，8 條已結案裁示裡有 6 條在 log 找不到，直接刪就永遠消失。
- 合併版會不會踩：可能。待處理有 dismissed 狀態、JSON 會留著（D-062），裁示升格後刪除（D-041）。lint 不能把 dismissed 的事換個說法再提；裁示升格刪除前，工作日誌要記一段（D-065）。
- 客戶會不會遇到：會，同一件事被問好幾次。

### I-086 數字沒綁日期、做完的待辦留著、清單沒標權威來源
- 來源：llm-wiki 1.8.0 `skills/wiki-core/SKILL.md:86-104`、`skills/wiki-new/SKILL.md:31`、`templates/vault/Templates/_README模板.md:110`（原文：兩個成熟 vault 各踩過一次）。
- 發生過什麼：三條寫檔規則。數字要綁日期，「現價 83」三個月後就是錯的。待辦做完就刪，留著的 ✅ 下個 session 會被當未完成再報、再查一輪。手寫清單要標權威來源。原文沒寫各自的具體經過。
- 合併版會不會踩：大部分不會。D-038 規定數值附出處與日期，推算段帶「算的日期」；待處理用狀態欄（D-062）。
- 客戶會不會遇到：防住了就不會。

### I-087 導入從「收什麼知識」問起，或一次全面導入
- 來源：llm-wiki 1.8.0 `skills/wiki-init/SKILL.md:36-37`、`skills/wiki-init/references/setup-interview.md:7-9`、`:32-33`、`:76`、`:99`、`skills/wiki-core/SKILL.md:24-26`。經驗通則，未述單一實例。
- 發生過什麼：真實部署裡知識層只佔四成，從「收什麼知識」問起，會把使用者導向書摘型結構。2026-09-22 搶救來的舊訪談原本是給讀書用的，當天就改寫。原文另述：一開始就全面導入的通常會死，沒有維護者的區塊三個月內腐爛。
- 合併版會不會踩：不會。訪談從管理對象與事問起（D-039、D-051），只問五句。
- 客戶會不會遇到：不會。

### I-088 日期散在文字裡、外部日曆讀一次上萬 token、不存在的日期過了驗證
- 來源：llm-wiki 1.8.0 `templates/vault/wiki/meta/agenda-system.md:11-12`、`:55`、`skills/wiki-agenda/SKILL.md:56`、`scripts/agenda.py:65`。
- 發生過什麼：日期原本散在 log、hot、README 的文字裡，「哪天要做什麼」沒地方能回答。外部日曆讀一次要上萬 token，完成狀態也沒人維護。日期格式只用正規表示式驗，擋不住 2026-13-99、25:00，另外用 strptime 確認。
- 合併版會不會踩：大部分不會，`agenda.json` 是唯一來源（D-006、D-062）。日期驗證要用真的日期解析，不能只用正規表示式。
- 客戶會不會遇到：防住了就不會。

### I-089 把內部筆記或英文自言自語寫給客戶看
- 來源：plugin 0.13.1 `rules/core-rules.md:74`。防範型。
- 發生過什麼：plugin 規定不要把英文自言自語、內部筆記寫給客戶，因為都會顯示在客戶的畫面上。
- 合併版會不會踩：R-10 規定對人用中文。D-061 的工作日誌會截取回答前 N 字，內部語會被記進日誌。
- 客戶會不會遇到：會，看到一段英文或程式用語。

## 八、多 session 協作（B-206，第一版旗標關）

### I-090 煞車被拆、授權被轉讓、範圍被擴張
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/01-邊界與權責.md:23-55`、`collab.md:9-21`。
- 發生過什麼：peer 寫「等使用者點頭再開工」，主管回「不要等，反正不會白做」，peer 就開工了。peer A 叫 B「直接做」，B 在主管的「等」到達前動手。主管一天內自行核可三件技術上有道理的擴張，使用者當天只派了三件，其餘全是雜訊。另有連續三次靠臨場判斷擋下錯誤指示，沒有規則支撐。
- 合併版會不會踩：客戶端第一版不會（collab 旗標關）。但這個 repo 自己是多 session 開發（process.md），conventions 的「誰能寫什麼」就是對應的防線。
- 客戶會不會遇到：第一版不會。

### I-091 派工語意模糊、撞單、同一份檔兩人各改
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/02-派工.md:18-25`、`:46-48`、`:60-87`、`collab.md:34-36`。
- 發生過什麼：一天出了四次派工錯誤（份數、格數、日期欄位、升版還是就地改），都在 peer 收到後才發現。主管派 A 改的同時，使用者在 B 那邊下了同一個裁示，B 已經 push。兩個 peer 同一天各自改同一份檔。接手的人只能自己跑 `git status` 猜前一位收工了沒。
- 合併版會不會踩：客戶端第一版不會。repo 開發會：設計 session 派工給實作與測試 session，spec 就是派工單，驗收準則先於實作（D-002）。
- 客戶會不會遇到：第一版不會。

### I-092 回報太晚、太長，或回報本身是錯的
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/03-回報與節奏.md:6-30`、`:77-125`、`04-查核.md:6-21`、`:45-55`、CHANGELOG 1.7.2。
- 發生過什麼：主管處理多輪才回報一次，使用者抓不到進度。權限分類器擋下 peer 時，使用者不知道是哪個視窗在等，主管轉達的放行無效。2026-10-09 一天 12 則跨 session 訊息，數則超過 1000 token。主管把 peer 沒驗證的「原廠沒印過這個版本」當成事實轉給使用者，使用者據此改了 22 份檔名，後來被推翻。一輪內三次差錯，全是回報本身有問題。
- 合併版會不會踩：客戶端第一版不會。repo 開發會：跨 session 的回報要附可驗證的路徑（測試、確認報告），不能只傳結論。
- 客戶會不會遇到：第一版不會。

### I-093 共用的 git index 與自動備份，把別人的檔掃進自己的 commit
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/04-查核.md:35-40`、`07-技術提醒.md:23-33`（2026-09-26 實際踩到）、`02-派工.md:18-19`、`templates/vault/raw/.manifest.json:6`、CHANGELOG 1.7.0。
- 發生過什麼：逐檔 add 了九個路徑，commit 仍帶進別人 staged 的第十個。`git add -A` 掃進凍結紀錄與另一個 session 的 450 份在製檔，同一個 vault 發生兩次。`--only` 撤銷了 `git rm --cached`，commit 看起來成功，diff 裡卻沒有那個檔。obsidian-git 的自動備份突破凍結、吃掉歸屬，一個檔三天 76 筆 commit 全是備份帳號。`cp -p` 保留 mtime，`git status` 誤報乾淨。
- 合併版會不會踩：客戶端不會，第一版不用 git（D-023）。repo 開發可能：conventions 三規定單一工作目錄、切分支、實作 session 才 commit。設計與實作 session 同時開著時，index 是共用的。實作 session 要不要一律 `git commit --only <路徑>`。設計 session 2026-10-12 定：實作 session 用 `git commit -- <檔>`；同日守門題實際踩到（設計 commit 落到分支），改為實作與測試用 git worktree（conventions 三）。
- 客戶會不會遇到：不會。

### I-094 檔案系統沒有鎖，後存的無聲覆蓋先存的
- 來源：llm-wiki 1.8.0 `scripts/occupancy_check.py:4-6`、`skills/wiki-core/SKILL.md:185`、`skills/wiki-collabteam/meet.md:18`、`07-技術提醒.md:15`、`skills/wiki-day/end-manager.md:77-78`。防範型。
- 發生過什麼：多個 session 改同一個檔，後存的會無聲覆蓋先存的，xlsx、docx 也不會提示衝突。llm-wiki 用佔用表機械比對；佔用表裡寫猜測（「原 -aa（疑為 -bb）」），兩個名字都會被當成合法持有者，該紅的判成綠。
- 合併版會不會踩：第一版單人不會。客戶在同步夾裡跟同事共用（architecture 七「共用夾」）時，客戶原檔會被同事改，但 AI 不寫原檔（D-034），只會在開場變動偵測看到。
- 客戶會不會遇到：第一版少見。

### I-095 低風險動作套用全套流程，花掉大量 token
- 來源：llm-wiki 1.8.0 `skills/wiki-collabteam/03-回報與節奏.md:77-89`、`skills/wiki-collabteam/06-coordination格式.md:6-8`、`skills/wiki-new/SKILL.md:34`、CHANGELOG 1.4.0、1.6.0。
- 發生過什麼：搬檔也走了正式紀錄的完整流程，使用者說：「我們剛剛花了很多 tokens 在這上面。」coordination.md 長到 24 KB，每位接任主管都要整份讀，精簡後剩 6.6 KB、資訊一筆沒丟。表格的對齊空白實測佔單檔 50～60% 的字元。skill 描述瘦身後，常駐成本從 3,977 降到 2,524 tok。
- 合併版會不會踩：不會。常駐預算有數字、有測試守（D-070）。manage 的低風險操作要配得上它的風險，不要每件都複述確認。
- 客戶會不會遇到：防住了就不會；沒防住，額度很快用完。

## 九、開發流程（repo 自己）

### I-096 正規表示式的 \b 被工具層變成 backspace，修了等於沒修
- 來源：llm-wiki 1.8.0 `scripts/_lib/derived.py:45-47`。
- 發生過什麼：`^status:[ \t]*initializing\b` 經過 heredoc 或工具層後，`\b` 變成字面的 0x08，正規表示式永遠不匹配，而且不報錯。複驗時才抓到，最後改成純字串比對。
- 合併版會不會踩：會。實作 session 用 Bash heredoc 寫 Python 就可能踩。CLAUDE.md 要寫：程式檔用 Write 或 Edit 寫，不用 heredoc。
- 客戶會不會遇到：不會直接遇到。客戶碰到的是某項檢查永遠不觸發。

### I-097 解析器命中 0 筆不報錯，好幾個月沒跑起來
- 來源：llm-wiki 1.8.0 `scripts/_lib/logparse.py:3-4`、`skills/wiki-fold/SKILL.md:11-12`、`scripts/wiki_fold_parse.py:4-5`、`:58-66`（驗收 #17）、`scripts/test_template_coverage.py:83-85`、`:158-161`。
- 發生過什麼：舊 fold skill 的正規表示式跟 log 實際格式不合，命中 0 筆，4.5 個月沒跑起來，也沒人發現。fold 切在同一天中間，宣稱的範圍跟殘留的條目重疊。CI 的正規表示式被改壞時，測試會永遠通過，所以加了防呆：命中數要大於門檻。
- 合併版會不會踩：會。工作日誌、綜合卡出處 id、檔頭、客戶設定檔 md（D-052）都由解析器讀。測試：每個解析器對 fixture 至少命中 N 筆；解析失敗要報錯，不能回空。
- 客戶會不會遇到：不會直接看到。後果是某個功能默默停擺。

### I-098 測試只測內部旗標，bug 在輸出層
- 來源：llm-wiki 1.8.0 `scripts/test_skeleton_check.py:251-256`（ISS-002）、spec `§11.7`、`§11.3`。
- 發生過什麼：downgrade 旗標的測試會過，輸出照樣印「已是最新」。`--record` 設計了卻沒實作，驗收走到第 6 步才發現。`--apply-safe` 的測試只驗「缺檔補完了」，沒驗「沒處理的不封存」。
- 合併版會不會踩：會。process.md 的驗收準則要寫成「客戶或服務人員看到的輸出」，`test_guard_cli.py` 這類要跑真的 CLI。
- 客戶會不會遇到：不會直接遇到。

### I-099 推論代替實測，結論被實跑推翻
- 來源：llm-wiki 1.8.0 spec `§9`（2026-10-09，F6 實跑 0 筆）、`skills/wiki-adopt/SKILL.md:24-28`（2026-09-21）、`scripts/adopt.py:155-158`；plugin 0.13.1 `scripts/office_tools.py:10`（COM 未在有 Office 的電腦測過）。
- 發生過什麼：以為要把 skeleton_check 登記進各 vault 的 tools.md，實跑 F6 顯示 0 筆，證明不用。以為 vault 根目錄的 skills/ 會被載入，實測不會。plugin 的 COM 重算與轉 PDF 寫完了，卻還沒在有 Office 的電腦上跑過。
- 合併版會不會踩：會。U-8 已經用 spike（B-006）處理，用的就是這個做法。`kb office` 的 COM 路徑要在確認環境（有 Office 的電腦）實跑過才能 verified。
- 客戶會不會遇到：會。沒實測的功能在客戶電腦上第一次跑就失敗。

### I-100 找一個定義繞了 5 次 grep
- 來源：llm-wiki 1.8.0 `CLAUDE.md:4`、`:31`（2026-10-08）。
- 發生過什麼：要找 `Coworker` 型別，它在 `types/index.d.ts`，不在 `hooks/` 裡，繞了 5 次 grep 才找到。這是 plugin repo 的 CLAUDE.md 寫成原始碼地圖的原因。
- 合併版會不會踩：不會。CLAUDE.md 是原始碼地圖（conventions 二，B-001）。地圖要跟目錄一起改，不然會漂。
- 客戶會不會遇到：不會。
