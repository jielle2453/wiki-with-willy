# 製卡規則（寫給製卡者）

製卡者是背景的 card-maker：主對話跑 `kb cards dispatch` 派它，一批一批做（D-196、D-197）；線上文件、檔名像密碼的、有密碼的那幾份由主對話照 `plan` 的 `cards` 那步自己做，規則一樣。

一張檔案卡讓之後的人不用打開原檔就知道「這是什麼、重點在哪、要查細節去哪一頁」。卡是索引不是副本：抄多少由你判斷，數值要用時回原檔讀（D-032）。範本在 plugin 的 `templates/card.md`，只看段落樣式；它第一行的註解不抄（卡的第一行一定是 `---`），檔頭照材料的 `header`。

## 批次（card-maker）

- 第一步跑 `kb cards batch <批次> --json`：`prefix` 是 kb 指令前綴（下面寫 `kb …`、`convert …` 的都接在它後面）；`kind` 是 cards｜agenda｜synthesis｜adopt（第十二節）；`read` 是先讀的規則；`items` 是這批要做的，每項跟下一節的欄位同一組、多 `bucket`（來自哪一桶，不是 `type`：`synthesis_new` 的 `type` 是實體型別；new、stale、broken、unlinked、agenda_recheck、agenda_unscanned、synthesis_new、synthesis_stale、synthesis_broken）；`profile`、`entities` 同下一節。已經做好的項目不再列。
- `cards`：一～六節寫卡、第七節補掛實體；每寫完一張新卡或重做的卡，同一份內容接著照第九節抽日期、跑 `kb agenda scanned <卡編號>`。`agenda`：第九節。`synthesis`：`rules/synthesis.md`，`synthesis_broken` 照第八節。
- 清單只用 `items`，不跑 `cards todo`、不自己找檔；不跑 `turn note`、`cards dispatch`。
- 做完照 card-maker 本文的回報格式回一則給主對話：只有份數與沒做成的原因，不寫檔案內容、摘要、人名、金額、日期（D-022）；「被擋或打回：」那行的寫法也在那裡。

## 材料的欄位（`cards batch` 的 `items` 與 `cards todo --json`）

- `new`、`stale`：每項 `id`、`uri`、`path`（原檔絕對路徑）、`origin`、`kind`、`dir`（卡放的夾）、`filename_pattern`（`<標題>-八碼.md`）、`existing_card`（stale 才有，舊卡路徑）、`created`（條目產出那天，AI 產出的卡寫起草日用）、`header`（照抄的檔頭）。stale 可帶 `reason`：「客戶說卡讀錯了」是客戶糾正過（`kb cards redo`），整張照原檔重讀重寫，不沿用舊卡的寫法。
- `moved`：`{"id","from","to"}`，用 `cards sync` 處理；只有開原檔連結對不上（來源搬家）也在這裡，`to` 跟原夾相同。`duplicates`：`{"id","keep","drop":[…]}`，同編號多張卡；`cards sync` 留 `keep`、刪 `drop`（備進回合，退得回來）。
- `missing`：`{"id","uri","missing_since","pending_id","question"}`；客戶答過（先留著、不用管）的不再列，怎麼問怎麼答由 `asks` 給。`unreadable`：`{"id","uri","reason","pending_id"}`，已在待處理，講法在 `tell`；客戶說先跳過（`pending dismiss <pending_id>`）的不再列進 `new`，檔案換了才重讀。
- `orphans`：卡的編號不在清冊或原檔已不在；這版不處理，不用動它。
- `broken`：`{"path","reason"}`，檔頭有問題的卡；編號對得到清冊的多帶 `id`、`header`（carding.md 第六節）。
- `skipped`：`{"credential","placeholder","too_large","error","skipped"}` 份數；`skipped` 是跑過 `cards skip` 的（依設定不整理）。說過「可以讀」的密碼檔不算在 `credential`，列進 `new`（carding.md 第十節）。
- `profile`：客戶設定檔的 `card_types`（`[{"name","sections":[{"name","required","hint"}]}]`，卡型與段落照它）與 `exclusions`（`[{"kind","action"}]`，`action` 是 `kind_only` 或 `no_card`）。`profile_missing` 是 true 時設定檔讀不到、這塊是預設值，人讀最後多一行 `客戶設定檔讀不到，卡型用預設`；照常做卡，不用跟客戶說。另有 `types`（`[{"name","category"}]`）、`synthesis_sections`（`[{"type","sections"}]`，設定檔第四節）、`about`（`[{"name","kind","aliases"}]`，客戶自己）。
- `entities`：`[{"name","type","aliases","self"}]`，登記的實體（`self` 是 false、company 或 person）；檔案卡 `實體` 欄照 `name` 寫。案子這類「事」的另有 `progress`（ongoing｜closed｜paused）與 `asked_evidence`（問過結案的依據，同一份不再問）。
- 日程（carding.md 第九節）：`agenda_recheck`：`{"id"（日程條目）,"card","card_path","kind","date","text","source"}`，出處卡重做後待重對的條目。`agenda_unscanned`：`{"id"（卡編號）,"card_path","title","existing":[{"id","kind","date","text"}]}`，還沒抽過日期的卡，`existing` 是這張卡已加進日程、還 open 的條目。`agenda_counts`：`{"overdue","today","upcoming"}`，開場日程行的三個數字，做卡時不用管。
- `unlinked`：`{"id","path","reason","types","prefill"}`，要補掛實體的卡（carding.md 第七節）。`missing_types`：`{型別名: 張數}`，`待補型別` 裡型別表沒有的。
- `synthesis_new`：`{"entity_id","name","type","category","confirmed","path","cards":[{"id","path","title"}],"statements","rulings","sections","header","question"}`，`path` 相對總資料夾，`sections` 是 `[{"name","required","hint"}]`、已含現況、紀事、推算，`question` 只有沒確認的有。`synthesis_stale`：`{"entity_id","name","path","header","statements","rulings","added","changed","removed"}`；`statements` 是有效口述 `[{"id","text","date"}]`、`rulings` 是裁示 `[{"id","kind","text","cards":[{"id","title"}]}]`（`kind` 是 spoken_wins｜ruled_out），`added`／`changed`／`removed` 每項帶 `source`（card｜statement｜ruling；口述、裁示的內容在項目的 `text`），`changed` 可帶 `reason`「客戶說卡讀錯了」（卡照原檔重寫過，綜合卡裡照舊卡寫的句子要改）。`synthesis_broken`：`{"path","reason"}`。`entity_drift`：`{"entity_id","name","type","kind","found","question"}`（`found` 是找到的卡，相對總資料夾，`missing` 是 null），`kind` 是 folder_renamed｜card_renamed｜both_renamed｜moved_type｜missing，怎麼問怎麼答在 `asks`。寫法看 `rules/synthesis.md`。

## 一、讀原檔

- 先跑 `convert <編號>`，照它印的做：
  - 「暫存檔：…」→ 用 Read 讀那個暫存檔；長的分段讀（每次 offset、limit 一段），讀完不用刪。
  - PDF 也印「暫存檔：…」，第二行「格式：pdf；N 頁；掃描頁 N（已轉圖）」→ Read 暫存檔，每頁前面有 `<!-- 第 N 頁 -->`，〔位置〕照抄這個頁碼；接著每行「圖：<路徑>（第 N 頁）」是沒有文字層的掃描頁，用 Read 看那張 PNG，照看到的寫。印了「掃描頁超過 N 頁」就是後面的掃描頁沒轉圖：`讀取` 填「部分」，「沒讀到的部分」寫哪幾頁沒看。
  - 「圖片：用 Read 看：…」→ Read 看圖（png、jpg 這類圖片檔直接看原檔），照看到的寫。整份都是掃描頁的 PDF 暫存檔只有頁碼註解，內容全在那幾張圖裡，不要寫成空卡。
  - 「讀不到：…」→ 這份不做卡；第二行是「已記進待處理（p-…）」才記進了待處理；回答末尾怎麼講由 `tell` 給。
- xlsx 的暫存檔每行開頭是「第 N 列：」，N 是工作表裡的真實列號，〔位置〕照抄這個 N。暫存檔最後一行是「（只抽到工作表「X」第 N 列）」時，後面沒抽到：`讀取` 填「部分」，「沒讀到的部分」寫從哪個工作表哪一列以後沒抽。
- 「大檔：照 carding.md 摘要加定位」→ 不逐段抄，寫整份的結構與每部分在哪（第幾頁、哪個工作表、第幾列到第幾列），卡仍守第四節的長度。xlsx 抽到上限停下時也印這行：`讀取` 標「部分」，「沒讀到的部分」寫哪些工作表、從哪一列以後沒抽。
- 「轉換結果是空的」→ 見第六節。

## 二、檔頭

檔頭是卡的第一段，`---` 包起來。機器欄位照材料的 `header` 一字不改照抄（含 `開原檔` 那行、`內容指紋` 那行有就留著），只填寫著（填）的欄；`敏感資料種類` header 已帶 `[]`，有個資就換掉。鍵名、順序固定：

| 鍵 | 誰填 | 值 |
|---|---|---|
| `編號` | 照抄 | 清冊編號，f- 開頭 |
| `出處` | 照抄 | 客戶原檔、對話收件、AI 產出 三者之一 |
| `原檔` | 照抄 | 清單，每行 `  - file://別名/相對路徑`，同一份內容在幾處就幾行 |
| `指紋` | 照抄 | 64 碼 |
| `內容指紋` | 照抄 | 64 碼；只有 docx、xlsx、pptx 有，header 沒這行就不寫 |
| `標題` | 你填 | `card_title_max_chars`（預設 40）字內，講這份是什麼（「信義王廚房估價第三版」），不抄原檔名、不加副檔名 |
| `卡型` | 你填 | 從材料的 `profile.card_types` 挑一個 `name`；都不像就用 `其他`（表裡沒有 `其他` 就挑最接近的） |
| `實體` | 你填 | 這份講到的案子、業主、廠商這類，名稱照材料的 `entities`（`[信義王, 王先生]`）；不在清單、型別表（`profile.types`）有它的型別 → 先 `entity add` 再寫；客戶自己照 `profile.about` 的名稱掛；講到很多只掛主要的，最多 5 個；header 預填的（原檔在實體夾裡）留著 |
| `待補型別` | 你填 | 講到的東西型別表沒有對應型別時，寫你覺得該有的型別名（`[廠商]`）；這種不登記、不掛；沒有就 `[]` |
| `文件日期` | 你填 | YYYY-MM-DD；文件上寫民國就換西元（115 年 10 月 1 日 → 2026-10-01）；找不到寫「未載」 |
| `讀取` | 你填 | 完整 或 部分（只讀了一部分頁、工作表抽到一半都算部分） |
| `製卡日期` | 照抄 | 今天 |
| `範本版本` | 照抄 | 2 |
| `敏感資料種類` | 你填 | 清單，只記種類不記值，種類名照 `profile.exclusions` 的 `kind` 原字（表外的見第五節）：`[種類一, 種類二]`；沒有就 `[]` |
| `開原檔` | 照抄 | `file:///` 開頭的連結，客戶點它開原檔 |

檔名：`filename_pattern` 的 `<標題>` 換成你填的標題原字，放在 `dir`（路徑是總資料夾接 `dir`）。所以標題本身就不要用 `\ / : * ? " < > |`、不以點或空白結尾、`card_title_max_chars`（預設 40）字內，寫之前先對一次。被打回「檔頭有錯：…」就照原因整張重寫一次：原因是「檔名該是…」「卡該放在…」就寫到原因指的新路徑，不寫回原路徑（錯名的舊檔留著，下一次 `plan` 的 sync 會收）；其他原因寫回同一個路徑。

## 三、正文

檔頭之後寫 `# <標題>`，接著：

1. 第一段一到兩句：這是什麼文件、給誰、做什麼用。AI 產出的卡（出處「AI 產出」）第一段寫明「AI 於 YYYY-MM-DD 起草」，日期用 todo 那一項的 `created`（是 null 就用製卡日期）。
2. 段落照那個卡型在 `profile.card_types` 的 `sections`，順序照表，每段一個 `## <name>`；`required` 是 true 的段沒內容寫一行 `未知`，其他沒內容整段不寫；那段寫什麼以 `hint` 為準。卡型沒有「沒讀到的部分」而讀取是「部分」時，照樣在最後加這段。預設值是這四段，以 todo 給的為準：
   - `## 重點`：這份檔要記住的事，一點一行。
   - `## 日期與數字`：期限、金額、數量、面積；照原檔寫，不換算。
   - `## 提到的人與單位`：人名加角色、公司、廠商。
   - `## 沒讀到的部分`：讀取是「部分」時寫哪些沒讀、為什麼。
3. 每一點結尾一個〔位置〕，讓人回原檔找得到：
   - PDF：〔第 3 頁〕
   - xlsx：〔工作表「總表」第 12 列〕
   - pptx：〔投影片 4〕
   - docx、純文字：〔「二、付款方式」段〕或〔第 5 段〕
   - 圖片：〔圖左上〕這類看得懂的描述

## 四、長度與忠實

- 正文 300～800 字。大檔摘要加定位，不超過 800 字。
- 只寫原檔有的。換算、加總、推論要註明是你算的（「三期合計 1,280,000 元（依第 2 頁三筆相加）」）。
- 同一件事原檔前後寫法不同（兩個金額、兩個日期），兩個都記、各帶〔位置〕，不替客戶選。
- 重做（stale）：原檔改過，先 Read `existing_card` 拿舊標題，標題不改，整張寫回 `existing_card` 那個路徑，不在舊卡上補；卡型與段落照現在的 `profile` 重挑。不要換標題：換了會變成兩張同編號的卡，要等 `cards sync` 留檔頭全過的那張、刪掉另一張。

## 五、個資不抄

- 種類照材料的 `profile.exclusions`（客戶設定檔第三節）：
  - `kind_only`：只在 `敏感資料種類` 記種類，正文不寫值；要提就寫「有廠商的這類資料〔工作表「聯絡」〕」，不寫號碼。
  - `no_card`：整份不做卡，跑 `cards skip <編號> --kind <種類>`（種類照 `kind` 原字，像兩種就挑一種）；回答末尾怎麼講由 `tell` 給（「請款類 4 份依你的設定不整理」）。程式認不出種類，要你讀了才判斷。
- 表外但明顯是證號、帳號、密碼、個人收入這類的，也照 `kind_only` 處理，種類自己取一個短名詞。客戶從表裡刪一列不等於可以抄；密碼、帳號這類值一律不進卡（「可以讀」的密碼檔也一樣，第十節），只記種類。
- 人名、公司名、案名可以寫。

## 六、特殊情況

- 轉換結果是空的（空檔）：照樣寫一張，正文只有第一段「這份檔案沒有文字內容」，`讀取` 填「完整」。
- 檔名像密碼或金鑰、只在雲端、太大、讀取出錯的檔：todo 不會列，不做卡；怎麼講由 `tell` 給。客戶說過「可以讀」的那份會列進 `new`，由主對話照第十節寫。
- 線上文件（`kind` 是 link，主對話做）：沒有檔可 `convert`，照 `url` 用讀 claude.ai 線上文件的工具讀；打不開（換帳號、沒分享）就跑 `kb cards unreadable <編號> --reason "線上文件打不開"`，這份不做卡，怎麼講由 `tell` 給。
- 讀不到（有密碼、舊格式、不支援、檔案壞了）：不做卡；原因與客戶怎麼辦由 `tell` 給。有密碼的 PDF 她給了密碼、或 plan 的 `unlock_same_kind` 判斷開得了，開了就照一般寫（第十節）。
- 圖片型或掃描型 PDF：Read 看 `convert` 轉出的頁面圖照看到的寫；看不清的段寫進「沒讀到的部分」。
- 「讀不到：還沒裝 PDF 元件」：不做卡，裝好元件後程式會自動把它放回待製卡。
- 同一份內容在多處：只做一張卡（todo 只列一次），`原檔` 清單會列出全部位置。
- 壞卡（`broken`，檔頭有問題）：有 `header` 的照它與 `reason` 整張重寫（`reason` 是「檔名該是…」就寫到新檔名，其他寫回 `path`）；沒有 `header` 的這版不修，不用跟客戶說。

## 七、補掛實體

`unlinked` 是要補掛實體的舊卡（`reason` 是 `v1`：範本第一版；`types_added`：`待補型別` 裡的 `types` 後來進了型別表）。每張：

- 讀卡本身（正文與 `原檔`）判斷掛誰；卡上的線索不夠才 `convert <編號>` 讀原檔。`prefill` 有值就至少掛它。
- 用 Edit 只改檔頭：`v1` 的在 `卡型` 那行之後加 `實體: […]`、`待補型別: […]` 兩行，`範本版本: 1` 改成 `範本版本: 2`；`types_added` 的把那幾個型別從 `待補型別` 拿掉，從卡的正文找出屬於那個型別的名稱（例如廠商名），照第二節 `entity add` 後掛進 `實體`。正文不動。
- 被打回照原因改（多半是名稱沒登記或寫了別名）。

## 八、壞掉或放錯位置的綜合卡

`synthesis_broken` 每項 `{path, reason}`；製卡助手拿到的批次另帶跟 `synthesis_stale` 一樣齊的材料：`target`（該放的路徑）、`header`（該有的檔頭，照抄）、`entity_id`、`name`、`type`、`sections`、`cards`（掛的檔案卡）、`statements`、`rulings`、`findings`。寫法看 `${CLAUDE_PLUGIN_ROOT}/rules/synthesis.md`，寫之前先讀它。

- `reason` 是「綜合卡該放在 …」（寫到了錯的路徑）：Read `path` 那張拿正文，檔頭換成 `header`，整張寫到 `target`，再跑 `kb entity clean <path>`（相對總資料夾）清掉錯的那張。
- 其他 `reason`（檔頭有錯、檔頭解析不了）：檔頭換成 `header` 整張寫回 `target`；正文讀得到就沿用、照 `reason` 改，讀不到或空的就照 `cards`、`statements`、`rulings` 從頭寫，不憑空補內容。
- 沒帶 `header` 的（找不到是哪個實體的）不會派給製卡助手，由主對話照 `plan` 的 `cards` 那步處理，處理不了就跟客戶說。

## 九、日程與狀態

每寫完一張新卡或重做的卡，接著照這節抽；日程批次的 `agenda_recheck` 先做（見最後兩點），再做 `agenda_unscanned`（既有知識庫的舊卡、重做過的卡、客戶說「把日期也整理進日程」）。每張卡讀它的正文（`日期與數字`、各段；不夠才 `convert`）抽日期、加進日程，抽完跑 `kb agenda scanned <卡編號>`，沒抽到任何日期也要跑，程式才知道這張卡看過了，不然下次還會列。`existing` 是這張卡已加進日程、還 open 的條目，已經在的不再加。日程的加改取消由程式管（`kb agenda`，客戶口述的走 note skill）。

- **直接加（D-128）**：抽到的日期直接加，不逐條先問客戶；加了哪些由回答末尾的 `tell` 告知，錯了客戶說一聲就改。
- **抽取規則（D-062）**：未來的日期**加一個動作**才進日程（「10/20 前付第二期款」「11/30 交屋」「下次 10/5 開會」）；過去的日期是紀事，只寫進卡正文與綜合卡紀事，不進日程。會議紀錄的開會日是過去，裡面提到的下次開會與期限才是未來。沒有動作的日期（「合約簽於 9/1」）不加。
- **先查重**：`existing` 已有的不加；別張卡或客戶口述加過的，加之前跑一次 `kb agenda list --json`，已有一條 open 的、日期相同而且說的是同一件事的，就不再加。重做過的卡，舊版抽的條目由程式排在前面的待重對處理（`agenda_recheck`），這裡不要重加一條。
- **年份**：文件沒寫年份時，取這份文件的 `文件日期` 之後最近的那天；文件日期是「未載」就照 plugin 根的 `skills/note/references/agenda.md` 第二節的換算。
- **怎麼加**：每個日期跑一次 `kb agenda add --card <這張卡的編號> --kind <到期｜會議｜承諾｜提醒｜期間> --date <YYYY-MM-DD> --text "<一句>"`，掛它講到的實體用 `--entity <名稱>`（要是登記過的名稱）。種類照文意：對方規定的期限是到期，我方答應的是承諾，開會是會議。`--card` 讓程式記出處與那張卡現在的指紋，來源標推測；同一張卡同種類、同日期、同文字再加一次不會重複。
- **工期**：合約或進度表寫了開始到結束，加成「期間」，`--start` 填開始日、`--date` 填預計結束，只掛那個案子一個實體；案子還沒登記（程式說實體清單沒有）就先不加期間，照第二節登記後再加。
- **告知**：加進日程的幾條由 `tell` 帶出來，不用自己列（D-128）。
- **待重對（`agenda_recheck`，D-130）**：每項是出處卡重做過、還沒結束的日程條目。重讀新版卡後，照 `source` 處理：
  - `inferred`（推測）：新版日期或內容變了就 `kb agenda edit <編號> --date <新日期> --card <那張卡>`（`--card` 讓來源維持推測、記下新版指紋）；新版已經沒有這件事就 `kb agenda cancel <編號>`；沒變就 `kb agenda recheck <編號>`。
  - `confirmed`、`corrected`（客戶說過或改過的）：不自己改，新版沒變就 `recheck <編號>`；新版寫法不同就 `recheck <編號> --propose-date <新日期>`（要改文字再加 `--propose-text "<一句>"`），程式會在 `asks` 問客戶用哪個，你自己不問。
- **結束證據（D-127）**：讀到驗收紀錄、尾款收清、文件寫明結案這類，對掛著的事類實體跑 `kb entity suggest-close <名稱> --evidence <這張卡的編號>`；程式會問客戶、問過的依據不再問，你不直接改狀態。「驗收第一期」這類中間事件不算。

## 十、密碼檔與加密的 PDF

- **「可以讀」的密碼檔**（客戶說「那份網站密碼檔可以讀」，D-183）：照一般寫卡，但正文只寫這份是什麼、列了哪些項目的名字（「建材商訂貨網、公司網銀、雲端相簿三組帳密〔工作表「帳密」〕」），**任何帳號、密碼、代碼的值都不抄**；`敏感資料種類` 記「密碼」。回答與 `kb turn note` 都不寫任何值（只說整理了這份、列了哪幾組的名字）。她之後問其中一組時，answer 會回原檔讀，卡上不需要值。
- **同一種的加密 PDF**（plan 的 `unlock_same_kind`，D-181 第 2 點）：對 `files` 每份，看檔名、夾、大小、日期，跟 `kinds` 每種的例子比，判斷是不是同一種（保險單就是保險單：同一家保險公司、同樣的檔名樣子）。是就跑 `convert <id> --saved <那一種的 credential>`，開了照一般寫卡；回答末尾照 `tell` 講「我判斷跟之前的…是同一種，用那組密碼打開了」。判斷不是任何一種、或拿不準的不動，照 `tell` 的「讀不到：有密碼」講；不要拿存好的密碼去試別的檔（D-181，不自動試）。
- 開的時候印「密碼不對」：那份不是同一種，照讀不到講，不再試別組。

## 十一、名字有沒有把握（主對話，plan 的 names）

`names` 每項是到門檻、還沒確認的新名字（名稱、型別、掛的卡標題）。照掛的卡判斷（D-202）：有把握的跑 `kb entity accept <名稱>`（記成 AI 判斷，算確認、不問、綜合卡照寫）；有疑慮的不動，留給 `asks` 的名字題。有疑慮的三種：可能跟已登記的是同一個（名字像、同一個業主換了叫法，先 `kb entity list` 比一下）、分不清是人還是公司或案子（型別可能放錯）、只出現一次、像打錯或縮寫。其他從卡上看得清楚的就是有把握。平常整理照 `asks` 一次最多問三題；建置時有疑慮的一次列完。AI 判斷錯了，客戶一句話照 note 改名或合併。

## 十二、讀舊 wiki（adopt 批次，建置收舊 llm-wiki）

`items` 每項 `{name, chunk}`：用 Read 讀 `chunk`（第一行是來源檔與第幾段），撈四種候選：她說過、原檔可能沒有的事（口述：掛哪個實體、哪天、一句）、規則（以後怎麼做、哪份別信）、還沒到的日期加一個動作（日程）、還在等她確認的。舊 AI 自己的操作紀錄（哪天整理了什麼）不撈，密碼、帳號這類值不抄。不寫任何檔、不跑 `cards batch` 以外的指令；回報照 `cards batch` 給的 `report` 格式，一行一條，讀不成的段寫 `- <段名>：<原因>`。
