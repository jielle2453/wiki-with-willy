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
| 3 | 部分過 | 因為做的是網頁版，沒有「小檔存回」，改重新做一份 Word 並 `--link`；之後該卡出現 stale、moved |
| 4 | 部分過 | 不再給草稿、直接做 Excel、`recalc` 對上；但前一句答案漏了客戶口述更正（142 萬 vs 124 萬） |
| 5 | 過 | 先草稿、推薦線上簡報並講三件事、客戶要 PowerPoint 就本機做、轉圖看、`output save --draft` |
| 6 | 部分過 | 庫裡沒有客戶原檔簡報，改用 AI 剛做的那份；字數增加但這台轉圖沒超出，「提醒打開確認」有說 |
| 7 | 未做 | 待使用者做（信那句我走了「信寫在回答裡、不寫草稿檔」，所以沒有留下草稿，要另想辦法讓第 7 步有草稿） |
| 8 | 未確認 | Office 路徑未確認（待使用者回答有沒有 Microsoft Office 的電腦） |
| 9 | 見下 | 無英文過場、無編號外露；守門擋 1 次、擋得對；`hook-errors.log` 不存在；成品都在清冊 |

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
- 新問題：之後 `cards todo --json` 出現該 link 卡 `stale: true`（`cards list`），再後來 `moved: [{id: f-d9363660, from: 檔案索引/AI 產出/報告/內湖李宅 進度報告-d9363660.md, to: 檔案索引/AI 產出/報告}]`，且 `plan` 有 `sync` 一步。`to` 是夾不是檔路徑，疑似 `--link` 加了第二個位置後卡被當成要搬。我沒有跑 `cards sync`，**結果未驗證**。

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

未做。AI 在第 7 步前一句被要求「幫我寫一封信給王先生，說交屋延到 12/15，我要 Word 檔」：照 draft.md「信不寫草稿檔」，信直接寫在回答裡給客戶看，客戶說「好」後做了 Word；所以沒有 `（草稿）.md` 留下，新對話的「草稿還在等你看」（`asks` 的 draft）還沒被驗。要驗這條需要另外一份留在 `AI 產出/` 的草稿，建議用新做文件（例如另一個案子的報告）寫完草稿就說「先這樣」。

## 第 8 步　有 Microsoft Office 的電腦

未確認。這台沒有 Office（`office check`：Office 沒有）。待使用者回答是否有另一台；沒有就照 spec 寫「Office 路徑未確認」。

## 第 9 步　全程記錄

- **給客戶看的文字有沒有英文自言自語、內部編號**：沒有（紀錄，從 AI 自己寫的每一段回答與過場檢查）。程式輸出（`cards todo`、`lookup`）帶 f-、s-、p- 編號，AI 沒貼進回答；網址（artifact）有貼，因為客戶問「在哪」。
- **守門擋了幾次**：1 次（Glob 到 `來源\客戶案件`，底下有被排除的內湖李夾；理由句繁體，擋得對）。工作區用 Write／Edit 寫 `build*.js`、`build*.py`、`slide2.xml` 全部放行（裁定 1、12 通過，新庫第一次也通）。另有 1 次不是守門：Claude Docs `batch` 被 auto mode 分類器拒絕（見第 2 步）。
- **`hook-errors.log`**：Glob 整個知識庫沒有（不存在）。
- **每份成品都在清冊**：`cards todo` 顯示 5 份 AI 產出的新檔（彙整 xlsx、更正版 xlsx、提案簡報 pptx、簡報修改 pptx、交屋延期信 docx）都在清冊待製卡，加上 link 那筆（Word 掛在它底下）。
- **退得回去**：只實測第 1 步（revert 成功、成品留著要再 `delete-outputs`）。其餘回合的 `versions list` 顯示每回合有 `create` 項，但**修改欄是 0**（清冊檔沒有列在回合 manifest），沒有實際再退一次。同一回合做兩份成品時（交屋信＋更正版 Excel，回合 20261022-141733）退回會兩份一起拿，客戶要注意。
- **修訂作者**：`AI 助理`（裁定 24 這次對；注意 B-405 未重現）。

## 給使用者決定的問題

1. 「說退回會拿掉」與實際「退回後成品留著、要再說一次才刪」不一致。要改 `output save` 成功句，還是改 revert 的行為？
2. Docs 建立被 auto mode 擋掉（不是客戶按的）。要不要在確認環境放行 `mcp__claude_ai_Claude_Docs__batch`，重跑第 2 步的 Docs 路徑？
3. `output save --link` 掛第二個位置後，link 卡出現 stale、moved（`to` 是夾）。要不要記 backlog，由實作查？（我沒跑 `cards sync`。）
4. answer 的統計題（「今年報價總共多少」）看不到客戶口述的更正，答案與 Excel 用了被客戶說過不對的 142 萬。要不要補進 find.md／verify.md？
5. 第 6、7、8 步的缺口：庫裡沒有客戶原檔簡報、信沒有草稿檔、沒有 Office 電腦。要補資料重跑，還是照現況記「未驗」？
6. 第 1 步前兩份信義王合約並存，AI 問了兩題才動手。是不是接受（我認為對）？

（第 7、8 步補完後會更新本檔並回報。）
