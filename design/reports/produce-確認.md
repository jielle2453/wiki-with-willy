# produce 確認報告

日期：2026-10-22（週四）
對象：分支 spec/produce，最新 0599a02（plugin 以 `--plugin-dir` 載入）
地點：`C:\Users\Jie\Desktop\llm-wiki-test\ingest-check\知識庫`
做法：每一句客戶的話由使用者親手在視窗打；確認 session 依 plugin 的 produce、answer、ingest 本文照做，並記每步輸出與落差。
標記：「人眼」是使用者在畫面上看到的、「紀錄」是從程式輸出與對話紀錄抽的。本報告全部是「紀錄」；使用者沒有回報畫面上的觀察（含開場訊息、Word 開啟後看修訂）。

## 起點核對

- 舊 llm-wiki：`~/.claude/settings.json` 裡 `llm-wiki@llm-wiki` 是 false（紀錄）。
- `%LOCALAPPDATA%\wiki-with-willy\` 不存在（紀錄）。
- 開場段（從對話開頭的 hook 內容抽，紀錄）：有「【wiki-with-willy】kb 指令」，也有一行「第一層夾「客戶」不在型別表（請客戶補進設定檔）」與「日程：7 天內 3」。沒有工具與草稿相關的開場句，這台起點本來就沒有。
- 既有資料（answer 確認留下）：46 張卡、`AI 產出/` 是空的、登記表裡「內湖李」夾被排除（10/19 unused）。

## 結論表

| 步 | 結果 | 一句話 |
|---|---|---|
| 1 | 部分過 | 流程走通、修訂作者寫對；但「退回」不會拿掉成品，要再說一次才刪 |
| 2 | 部分過 | 先草稿、先講三件事都對；Docs 建立被自動權限擋掉，改 Artifact 網頁版；整理前 answer 查得到登記但看不到標題 |
| 3 | 部分過 | 因為做的是網頁版，沒有「小檔存回」，改重新做一份 Word 並 `--link`；之後該卡出現 stale、moved，下次整理時被重做（兩個位置都在卡上） |
| 4 | 部分過 | 不再給草稿、直接做 Excel、`recalc` 對上；但前一句答案漏了客戶口述更正（142 萬 vs 124 萬） |
| 5 | 過 | 先草稿、推薦線上簡報並講三件事、客戶要 PowerPoint 就本機做、轉圖看、`output save --draft` |
| 6 | 部分過 | 庫裡沒有客戶原檔簡報，改用 AI 剛做的那份；字數增加但這台轉圖沒超出，「提醒打開確認」有說 |
| 7 | 過 | 新對話 A 寫草稿、說「先這樣」；新對話 B 問「今天還有甚麼要交的」，回答末尾問「大安陳 進度報告 的草稿還在等你看」，排在名字確認之前 |
| 8 | 未確認 | Office 路徑未確認（使用者說沒有裝 Microsoft Office 的電腦，跳過） |
| 9 | 見下 | 我這個對話：無英文過場、無編號外露；整個過程（含 A）有 2 句英文過場；守門前置擋 1 次、後置檢查擋 5 次（都擋得對）；`hook-errors.log` 不存在；成品都在清冊 |

## 第 1 步　合約付款條件改 30 天

指令序列（紀錄）：`office check`（Office 沒有、LibreOffice 有、node 有、工具夾沒裝）→ 問客戶要不要裝（講了裝在哪、約 1 分鐘、原檔不動）→ 客戶說「好」→ `office setup`（5.2 秒，裝好 python 4 個、node 2 個）→ `cards list --type 合約 --json` → `convert`（讀原檔）→ 問客戶 → `office copy` → `office unpack` → Edit `word/document.xml` 加 `w:ins` → `office pack --like` → `office unpack` 驗 → Grep `w:ins` → `office pdf` → `output save --from` → `asks` → `turn note`。

- 客戶說「信義王合約的付款條件改成 30 天」。庫裡有兩份信義王合約（合約、合約_用印版），付款寫法不同（兩期 50/50 vs 三期 30/40/30），原檔未說哪份有效，且兩份都是分期比例、沒有「幾天內付款」的條文。AI 先問「改哪一份」「30 天指什麼（A 每期、B 只最後一期、C 其他）」，客戶答「用印版，A」。多問兩題是對的（R-06），但比 spec 想的多一個來回。
- 工具提示：先 `office check` 再問，符合。`setup` 只花 5.2 秒（spec 寫約 1 分鐘，可能是這台已有快取；非錯誤）。
- 修訂：在副本加一個 `w:ins`，作者 `AI 助理`（裁定 24 這次第一次就寫對，沒用 `python -c`，用 Grep 驗）。轉 PDF 成功（檔打得開）。**Word／LibreOffice 看得到修訂**：沒有人眼確認，標「未確認」。
- 原檔沒動：`office copy` 句「原檔沒動」；沒有重算指紋比對，未獨立驗證。
- 成功句：`已放進 AI 產出\合約\… ，改自「…」（說「退回」會拿掉）`。
- 退回（客戶說「退回上一版」）：`versions list` 最新一筆是這份 → `versions revert` →「已退回 …：還原 0、移除 0、成品 1 份留著」「這 1 份成品先留著；要刪跟我說，我用 delete-outputs 刪」。**成品沒有不見**，AI 先前跟客戶說「退回會拿掉」就說錯了。客戶說「好」後 `versions delete-outputs`（先備份）才不見。之後 `AI 產出/` 為空、`cards todo` 沒有殘條。
- 落差（交給設計決定）：
  1. `output save` 成功句「說退回會拿掉」與 revert 實際行為不符，客戶要說兩次。要改句子，還是改 revert 行為（spec 第 1 步寫「AI 產出那份不見」）。
  2. 兩份信義王合約並存、已有一筆「寫法不同」待處理（`tell` 一直在報）。

## 第 2 步　內湖李進度報告（給業主）

指令序列（紀錄）：`lookup 內湖李` → Read 綜合卡 → `cards list --entity 內湖李 --json` → `convert`（報價第四版、需求訪談，逐份核對 138 萬、250 萬）→ Write 草稿 `AI 產出/報告/2026-10-22 內湖李 進度報告（草稿）.md` → 問客戶 → 客戶「可以」→ Claude Docs `batch`（建立）→ **被擋** → 客戶選 B（Artifact）→ Artifact 發布 HTML 頁 → `output link --title … --type 報告 --entity 內湖李 --basis f-… --basis f-…` → `output drop-draft` → `asks` → `turn note`。

- 草稿帶句尾短標，數字回原檔核對，「要請你看的地方」三條（擱置要不要提、報價原檔標題寫信義王卻掛內湖李、預算與報價落差）放在草稿尾、不進成品。裁定 17：在叫 Docs 之前，已講三件事（存在你的 claude.ai 帳號、預設只有你看得到、要檔怎麼拿）並問；裁定 23：先草稿再問，符合。
- **Docs 被擋**：客戶說「可以」之後，Claude Docs `batch` 被 Claude Code 的 auto mode 分類器拒絕，理由「在連接的應用裡做了沒要求的新增」。不是客戶按的，也不是守門。AI 沒有繞過，停下來給三個選項，客戶選 B。這會影響 spec「Docs 建好」的驗收：**Docs 路徑未驗過**，驗過的是 Artifact HTML 頁。
- Artifact 版是網頁，沒有匯出 Word 的按鈕（回答有講）。
- `output link` 成功：`已登記線上文件「內湖李宅 進度報告」（下次整理會做卡）`；`drop-draft` 成功、說退回會回來。
- 「剛才那份進度報告在哪」：整理前 `lookup 內湖李` 只顯示「還沒整理 1 份」，沒有標題；要靠 `cards todo --json` 才看得到是哪一份。`cards list --type 報告` 是 0（卡型不是「報告」）。客戶說「整理一下」後做了卡，之後 `lookup` 列得出「2026-10-22 內湖李宅 進度報告（其他）」。部分過：答得出「在哪、存在哪、誰看得到」，但整理前沒有標題。
- 登記表排除「內湖李」夾不影響 `lookup`；影響的是工具：Glob 到 `來源\客戶案件` 會被守門擋（理由句繁體，擋得對）。

## 第 3 步　也給我一份 Word

指令序列：`office check` → Write `build.js`（工作區）→ `office run` → `office render` → Read 圖 → `output save … --link <網址>`。

- spec 預期「小檔存回本機」。這次因為第 2 步是自做網頁，AI 重新做一份 Word（內容與網頁版對齊），`--link` 掛在同一筆。
- 轉圖看過一頁，版面正常。
- 新問題：之後 `cards todo --json` 出現該 link 卡 `stale: true`（`cards list`），再後來 `moved: [{id: f-d9363660, from: 檔案索引/AI 產出/報告/內湖李宅 進度報告-d9363660.md, to: 檔案索引/AI 產出/報告}]`，且 `plan` 有 `sync` 一步。`to` 是夾不是檔路徑，疑似 `--link` 加了第二個位置後卡被當成要搬。我沒有跑 `cards sync`。後來新對話 A 整理時重寫了這張卡：`原檔` 兩個位置（網頁網址與 `file://AI 產出/報告/…進度報告.docx`）都在、指紋換新，所以 stale、moved 是「加了第二個位置、卡要重做」的正常後果，沒有搞丟。`moved` 的 `to` 只有夾沒有檔名，只是顯示怪，不影響結果。

## 第 4 步　今年報價總共多少、再做成 Excel

指令序列：`cards list --year 2026 --json` → `convert` × 6 → `tally` × 2 → `asks`；接著 `office check` → Write `build_xlsx.py` → `office run` → `office recalc` → `office unpack`＋Grep 讀回 → `output save`。

- 答案：7 份有日期的報價，合計 7,255,500（約 726 萬）；各案最新版 342 萬；說明版本重複、稅別不一、2 份沒日期沒算進去。逐份回原檔核對（第四版標題寫信義王卻掛內湖李，有講）。
- 「這個做成 Excel」：沒有再給草稿，直接做；公式 2 個、錯誤 0；讀回 D12=7,255,500、D13=1,036,500 與 `tally` 相同；放進 `AI 產出/報價/`。符合。
- **漏洞**：第 7 步查 `lookup 王宅` 才看到客戶 10/21 口述「第五版含稅 124 萬，原檔寫的 142 萬不對」。「今年報價」答案走 `cards list`（find.md 第二節），看不到口述，所以合計用了 142 萬。客戶說「好」後 AI 重 `tally`（合計 7,075,500、約 708 萬）、另做「（更正版）」Excel（備註欄寫原檔 142 萬）。舊 Excel 沒刪。→ 這是 answer 的缺口（統計題不查口述與裁示），不是 produce；請設計判斷要不要補到 find.md／verify.md。

## 第 5 步　內湖李提案簡報

指令序列：Write 草稿 `AI 產出/簡報/… 提案簡報（草稿）.md` → `asks` → `turn note` → 客戶「我要 PowerPoint 檔」→ `office check` → Write `build_pptx.js` → `office run` → `office render` → Read 2 張圖 → `output save --to … --draft …` → `asks` → `turn note`。

- 草稿 5 頁，只放庫裡有的（需求、預算、報價、系統櫃、下一步），沒編平面、風格；「要請你看」兩條放尾端。回答推薦線上簡報（Slides），講三件事。符合。
- 客戶沒有明說「可以」，只說「我要 PowerPoint 檔」，AI 當成接受草稿。
- 成功句：`已放進 … .pptx，草稿收起來了（說「退回」會拿掉）`。草稿備進回合、被拿掉。轉圖看了封面與預算頁，沒有超出。
- 簡報字型：Microsoft JhengHei、`lang=zh-TW`。

## 第 6 步　改簡報第 2 頁（長字）

- 來源資料夾裡沒有 pptx（Glob 在 報價與合約、公司行政、客戶案件\信義王、客戶案件\大安陳 都找不到；`客戶案件` 根目錄被守門擋）。AI 向客戶說明，改用 AI 剛做的那份當「既有簡報」。`office copy "AI 產出/簡報/….pptx"` 收進（AI 產出可複製，符合 spec 的「清冊 active 的客戶原檔或 AI 產出」）。
- Edit `ppt/slides/slide2.xml`（把三個重點換成一個長段落）→ `office pack --like` → `office render --pages 2` → 5 行、沒超出 → `output save --to "…（AI 修改）.pptx" --from "<AI 產出的路徑>"`，成功句有「改自」。
- 回答有提醒「請你用 PowerPoint 打開第 2 頁確認」，並說「這台畫法跟 PowerPoint 不一定一樣」。
- 未測到：客戶原檔簡報這條路；字真的溢出的那個分支（這台轉圖 22 點仍放得下）。

## 第 7 步　草稿跨對話

過（紀錄）。先前那封信走 draft.md「信不寫草稿檔」，沒有留下草稿，所以改用另一份新做的文件來驗。這個確認對話不用關，另開兩個新對話：

- 對話 A（紀錄 jsonl 98a9c7b9…，14:27）：客戶打「幫我做一份大安陳的進度報告給業主」。因為庫裡有 6 份剛放進來的新檔（上面第 3～6 步的成品），AI 先照 ingest 整理（先講「有 6 份要整理，大約 1 分鐘；要停的話按停止」），寫好 6 張卡、日程加王宅 12/15 交屋、更新王宅與內湖李綜合卡，再進 produce：`lookup 大安陳`、`convert` 5 份原檔、寫草稿 `AI 產出/報告/2026-10-22 大安陳 進度報告（草稿）.md`，回答給重點、列四點要客戶確認、推薦線上文件並講三件事（存在你的 claude.ai 帳號、預設只有你看得到、要 Word 或 PDF 從那頁下載）。客戶說「先這樣」，AI 回「草稿先留在…，沒有做成檔，也沒有寄出去」。符合裁定 23（先草稿）與 D-163。
- 對話 B（紀錄 jsonl 381ef81d…，14:33）：客戶問「今天還有甚麼要交的」。`kb asks --json` 有 `draft`（「大安陳 進度報告 的草稿還在等你看」，三個結果定稿／再改／不要了，「不要了」的 do 是 `kb output drop-draft`，strict），回答末尾：「另外有兩件要請你確認：1. 「大安陳 進度報告」的草稿還在等你看。要定稿、再改，還是不要了？ 2. 「王先生」（業主，掛了 2 份檔）：名字這樣叫對嗎？……」順序是 draft 在名字確認之前，符合。
- 我在確認對話另查：`kb asks --json` 目前仍列這份草稿（`AI 產出/報告/` 的草稿檔在）。
- 沒做的：沒有實際選「定稿」「再改」「不要了」，只驗到問句出現。
- 小落差：B 的回答先講日程、再講確認，確認項前沒有分「草稿」與「名字」兩段標示，但清楚；B 的「建議下一步」沒提草稿，提的是明天回覆王董，因為日程更急，可接受。

## 第 8 步　有 Microsoft Office 的電腦

**Office 路徑未確認。** 這台沒有 Office（`office check`：Office 沒有、LibreOffice 有），使用者也說沒有另一台裝了 Microsoft Office 的電腦，所以第 1 步的轉 PDF 只在 LibreOffice 驗過（`office pdf` 成功，輸出 PDF 在工作區）；用 Office（PowerShell COM）轉 PDF、重算、轉圖的路徑沒驗。

## 第 9 步　全程記錄

- **給客戶看的文字有沒有英文自言自語、內部編號**：
  - 確認對話（第 1～6 步）：沒有英文過場、沒有貼內部編號（紀錄）。程式輸出（`cards todo`、`lookup`）帶 f-、s-、p- 編號，AI 沒貼進回答；網址（artifact）有貼，因為客戶問「在哪」。
  - 對話 A（第 7 步，整理加草稿）：有 2 句英文自言自語出現在助理文字裡：「Convert accepts a path; use the docx path.」（出現兩次記錄）與「Now the two synthesis cards to update. Reading the old ones.」。其餘過場是繁體；最終回答沒有編號。對話 B：無英文、無編號。→ ingest／synthesis 那一段的「過場一律繁體中文」仍會漏。
- **守門擋了幾次**：
  - 前置守門：1 次（確認對話 Glob 到 `來源\客戶案件`，底下有被排除的內湖李夾；理由句繁體，擋得對）。工作區用 Write／Edit 寫 `build*.js`、`build*.py`、`slide2.xml` 全部放行（裁定 1、12 通過，新庫第一次也通）。
  - 後置檢查：對話 A 一次改王宅、內湖李綜合卡時，PostToolUse 的 `output_log.py` 擋 5 次，原因「綜合卡有錯：整理到不是現行的，重跑 kb cards todo 拿新檔頭」（先寫了舊檔頭再 Edit，檔頭過期）；AI 重跑 `cards todo` 拿新檔頭後改好，擋得對。
  - 不是守門：Claude Docs `batch` 被 auto mode 分類器拒絕 1 次（見第 2 步）。
- **`hook-errors.log`**：Glob 整個知識庫沒有（不存在）。
- **每份成品都在清冊**：`cards todo` 顯示 5 份 AI 產出的新檔（彙整 xlsx、更正版 xlsx、提案簡報 pptx、簡報修改 pptx、交屋延期信 docx）都在清冊待製卡，加上 link 那筆（Word 掛在它底下）。
- **退得回去**：只實測第 1 步（revert 成功、成品留著要再 `delete-outputs`）。其餘回合的 `versions list` 顯示每回合有 `create` 項，但**修改欄是 0**（清冊檔沒有列在回合 manifest），沒有實際再退一次。同一回合做兩份成品時（交屋信＋更正版 Excel，回合 20261022-141733）退回會兩份一起拿，客戶要注意。
- **修訂作者**：`AI 助理`（裁定 24 這次對；注意 B-405 未重現）。

## 給使用者決定的問題

1. 「說退回會拿掉」與實際「退回後成品留著、要再說一次才刪」不一致。要改 `output save` 成功句，還是改 revert 的行為？
2. Docs 建立被 auto mode 擋掉（不是客戶按的）。要不要在確認環境放行 `mcp__claude_ai_Claude_Docs__batch`，重跑第 2 步的 Docs 路徑？
3. `output save --link` 掛第二個位置後，link 卡會 stale、moved（`to` 只有夾），下次整理後正常。這是設計如此（卡要重做），還是要讓顯示更清楚？
4. answer 的統計題（「今年報價總共多少」）看不到客戶口述的更正，答案與 Excel 用了被客戶說過不對的 142 萬。要不要補進 find.md／verify.md？
5. 缺口：庫裡沒有客戶原檔簡報（第 6 步）、沒有 Office 電腦（第 8 步），要補資料重跑，還是照現況記「未驗」？第 2 步 Docs 路徑也未驗。
6. 第 1 步前兩份信義王合約並存，AI 問了兩題才動手。是不是接受（我認為對）？
7. 對話 A 的 ingest／綜合卡段落仍有 2 句英文過場（「Convert accepts a path…」「Now the two synthesis cards…」）。要不要在 ingest SKILL 把「過場一律繁體中文」再強調一次，或視為 ingest 的既有問題另記？
8. 第 4 步我在 answer 漏了口述更正、第 7 步前一句又走信不寫草稿檔，兩處都不是 produce 設計錯，但請你判斷是否寫進 backlog。

## 第 2 步重跑（Docs）

日期：2026-10-22（週四）下午；依設計 00 的指示（spec 裁定 28）重跑第 2 步的 Docs 路徑。標記：「紀錄」（程式輸出與對話）、「人眼」（使用者在畫面上回報的）。

- **環境**：使用者在這個視窗按 Shift+Tab 把 auto mode 切到一般模式，回報「切好了」（人眼；我看不到模式本身）。
- **實體**：換成「王宅」（沒有進度報告草稿、也沒有 link；內湖李、大安陳都不用）。使用者親手打：「幫我做一份王宅的進度報告給業主」（紀錄）。
- **先草稿**：`lookup 王宅 --json` → Read 綜合卡 → `convert` 6 份原檔（工程進度表、四份追加項目、書牆追加）逐份核對數字 → `tally` 追加項目 5 筆（合計 260,000，草稿寫 26 萬）→ Write 草稿 `AI 產出/報告/2026-10-22 王宅 進度報告（草稿）.md`（句尾短標、「要請你看的地方」5 條不進成品）→ `asks` → `turn note`。草稿裡 5 條要請客戶看的：合約兩份付款寫法不同、第五版報價 142 萬對 124 萬（口述）、拆除預計 9/10 完成卻仍是「進行中」、書牆「10/27 口頭同意」日期在今天之後、聯絡人改王太太。
- **推薦線上文件並講三件事（裁定 17）**：在草稿回答裡，建之前，同一段講了「存在你自己的 claude.ai 帳號、預設只有你看得到、要 Word 或 PDF 從那頁匯出」，再問「內容這樣可以嗎？可以我就做成線上文件」。符合。使用者回「可以」。
- **建 Docs**：`Artifact` 以 `type_url`（Docs 類型）建立（`https://claude.ai/artifact/5Xk678B9LnqQbomYxM89V1`），再用 Claude Docs `batch` 對那個文件的第一個分頁插入全文（一次寫完，五段加一個表格；沒有逐段填，因為文件很短）。`batch` 回 `verdict: allow`，`rev 2`。**使用者回報「有看到」權限提示、由使用者按允許（人眼）**。這次沒有被擋；先前被擋那次（auto mode 分類器）是 auto mode 造成的。
- **登記**：`output link <網址> --title "信義王宅 進度報告" --type 報告 --entity 王宅 --basis …（9 份）` →「已登記線上文件「信義王宅 進度報告」（下次整理會做卡）」；`output drop-draft` →「草稿收起來了…（說「退回」會回來）」；`asks`、`turn note`。
- **回答**：給網址、說存在哪、誰看得到、要檔怎麼拿；說明沒放第五版報價與書牆口頭同意；末尾提醒大安陳草稿與王先生名字兩題。
- **「剛才那份進度報告在哪」**：`lookup 王宅` 顯示「還沒整理 1 份」、沒有標題（同第 2 步第一次的落差）；`journal recent --changed` 有「做了：王宅進度報告做成線上文件」與網址片段；`cards todo` 列出 `f-05e370af https://claude.ai/artifact/…` → 檔案索引/AI 產出/報告。AI 回答網址、存在帳號、預設只有使用者看得到、登記狀態與「還沒做卡」，並說明庫裡另外還有內湖李（線上版加 Word 版）與大安陳（草稿）兩份進度報告，「剛才那份」當成最近這份。**答得出來**。
- **判定**：第 2 步 Docs 路徑**過**（先草稿帶短標、三件事在建之前、Docs 建好、`output link` 登記、answer 找得到）。保留的落差：整理前 `lookup` 只有份數沒有標題；`cards todo --json --brief` 在這版不認（對話 A 也叫過同一個旗標，被拒絕），SKILL 或參考文件裡有沒有寫到這個旗標請設計看。
- **分支狀態**：commit 前 `git status` 乾淨，`git log` HEAD 是 1d2998a（實作 fd 的「確認後裁定 27，output save 成功句改成退回時成品先留著」，在我的 3f54536 之上）。這一節只加報告，沒有動程式；第 1 步看到的成功句落差，fd 已在 1d2998a 處理，**我沒有用新版重跑第 1 步**。
