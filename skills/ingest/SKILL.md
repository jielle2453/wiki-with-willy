---
name: ingest
description: 把客戶資料夾裡新增、改過、搬動的檔整理成檔案卡，原檔不見的問要不要拿掉卡。開場有「有變動」那行，或客戶說「我加了檔」「我改了報價單」「我刪了」「檔案我搬了」「有新東西嗎」「整理一下」「整理成卡」時用；加資料夾、不讀某夾、資料夾搬家改名、要我搬或改檔、問檔案內容、做成文件不用這個。
---

# 整理成卡

這個 skill 讓檔案變成檔案卡：程式算哪些檔要做卡、抽出文字，製卡者（這版就是主對話的你）讀完照規則寫一張卡到 `檔案索引/`。程式的參數、輸出與拒絕原因看 `references/commands.md`；卡怎麼寫看 `references/carding.md`，寫卡前一定先讀它。

用 Bash 工具這樣叫：

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" <子命令> …
```

`${CLAUDE_PLUGIN_ROOT}` 代換不了（指令說找不到 kb.py，或路徑開頭變成 `/scripts`）時，用開場段「kb 指令」那行的前綴。

## 分辨

- 開場有「變動：…」與「有變動：…」兩行，或客戶說「我加了」「我改了」「我刪了」「搬了」「有新東西嗎」「整理」→ 照下面的順序做。
- 開場有「設定檔有改」、客戶說「設定檔改好了」「套用」→ 照 commands.md 的 profile 節：先 `profile check` 複述差異、客戶說好才 `profile apply`；開場同時有「有變動」時先處理設定檔再整理；開場是「設定檔有改但讀不懂」時不跑 check，請客戶照那句改。
- 「把 D 槽加進來」「不要讀…」「那個資料夾不要了」→ 走 manage，不在這裡做。manage 加完資料夾問「要我現在整理成卡嗎？」，客戶說要才回到這裡。
- 搬動分兩種：**來源根**（登記的那個資料夾本身）搬了或改名 → manage；來源**裡面**的檔或子夾搬了或改名 → 這裡。
- 「這是合約內容，存起來」（貼一段文字或圖）→ 這版做不到，說「收件功能之後補；先把檔案放進登記的資料夾，我再整理」，不寫任何檔。
- 客戶說「我改了報價單」但 `changes` 查無變動 → 先跑 `cards todo --json`：那份在 `new` 或 `stale` 就照下面做（之前可能沒做完）；不在才說「檔案看起來跟上次一樣，是不是還沒存檔？要我記下來的事之後會有記事功能」，不做別的事。
- 客戶問卡裡的內容（「上次那份報價在哪」）是回答的事，不是整理；直接讀卡回答。

## 順序

1. 開場自動時：先把客戶這句的答案完整寫出來（該有的數字、出處都寫），這段寫完才開始整理；整理的過程不取代答案，最終回答裡答案在前、整理告知在後。客戶不是來等整理的。
2. 跑 `changes`；最後一行印了「要套用請跑 kb inventory scan。」才跑 `inventory scan` 把變動記進清冊。從 manage 加完資料夾交棒過來（加資料夾時已入冊）、或客戶明說「整理」但沒變動時，直接跑第 3 步。
3. 跑 `cards todo --json`，看 `new`、`stale`、`moved`、`duplicates`、`missing`、`unreadable`、`broken`，以及實體與綜合卡的 `unlinked`、`missing_types`、`synthesis_new`、`synthesis_stale`、`synthesis_broken`、`entity_drift`。
4. `moved` 或 `duplicates` 有項目就先跑 `cards sync`（卡跟著原檔搬、檔名不變；同編號多張留正本），跑完再跑一次 `cards todo --json`，照新的清單做下面幾步（搬了又改過的檔，搬完才會列進 `stale`）。
5. 待製卡份數直接看 `--json` 的 `new`、`stale`、`unlinked` 加起來幾項，≤ 上限就不問客戶要不要、不說「要不要接著整理」，直接做完在回答末尾一句告知（上限是開場那行寫的「超過 N 份」，沒有那行用 5）；超過先問「有 N 份要整理，現在做嗎？大約 N 分鐘」（一份抓 10 秒，不到 1 分鐘說「一兩分鐘」）。客戶說好就全部做、每 5 份講一句進度；說之後再說，這個對話不再問。
6. 每一份照 `references/carding.md` 做（讀完判斷是 `profile.exclusions` 裡 `no_card` 的種類就不做卡、跑 `cards skip`）：
   - `convert <編號>`；
   - 讀它指的東西：暫存檔用 Read 分段讀（PDF 也是暫存檔，掃描頁另外印「圖：…」，用 Read 看那些 PNG）；圖片檔用 Read 看原檔；
   - 用 Write 寫卡：檔頭照 todo 給的 `header` 一字不改（含 `開原檔` 那行），只填（填）的欄與 `實體`、`待補型別`（講到的實體不在 `entities` 就先 `entity add`，見 carding.md 第二節），有個資就把 `敏感資料種類` 的 `[]` 換成種類；路徑是總資料夾接 `dir`，檔名照 `filename_pattern` 把 `<標題>` 換成標題。標題寫之前先照 carding.md 第二節對一次：檔名寫錯被打回時，錯名的檔會留在 `檔案索引/`，要等 `cards sync` 收；
   - 寫完被打回（「檔頭有錯：…」）就照原因改、整張重寫一次：原因是「檔名該是…」或「卡該放在…」就寫到原因指的新路徑，不寫回原路徑（舊檔交 `cards sync` 收）；其他原因寫回同一個路徑。再被打回就停，回答末尾講這份沒做好與原因。總目錄程式會跟著更新，不用叫。
   - `convert` 回「讀不到」就不做這份，記住原因，回答末尾照 commands.md 的說法講；第二行是「已記進待處理（p-…）」才算記進待處理（檔案被 Word 開著這類就沒記，請客戶關掉再試）。
   - `stale` 是原檔改過：先 Read `existing_card` 拿舊標題，標題不改，用 Write 整張寫回 `existing_card` 那個路徑，不用 Edit 補；卡型與段落照現在的 `profile` 重挑。
   - `broken` 是檔頭有問題的卡：有 `header` 的照它與 `reason` 整張重寫（`reason` 是「檔名該是…」就寫到新檔名，其他寫回 `path`）；沒有 `header` 的這版不修，不用跟客戶說。
   - 這批有寫到新路徑的（被打回改名、改夾），全部做完再跑一次 `cards sync`，把留下的舊檔收掉。
7. `unlinked` 的卡照 carding.md 第七節補掛實體（份數算進第 5 步的上限）。
8. `entity_drift` 在回答末尾問它的 `question`（`found` 是找到的卡，相對總資料夾，`<型別>/<夾>/<檔>.md`）。客戶說跟著改：`folder_renamed`、`both_renamed` → `entity rename <name> --to <夾名>`；`card_renamed` → `--to <檔名去 .md>`；`moved_type` → `entity rename <name> --to <name> --type <found 的第一層>`。說改回來 → `entity rename <name> --to <name>`。`missing`（夾或綜合卡不見）：不要了 → `entity remove`；刪錯了 → 先請他從資源回收筒還原，沒有才 `entity rebuild`。含糊再問一次。
9. 第 6～8 步有寫卡、補掛、改實體，先再跑一次 `cards todo --json`，照新的 `synthesis_*` 做（舊的 `header`、`path` 已不對）。綜合卡照 `rules/synthesis.md` 寫（寫前一定先讀它）：`synthesis_stale` 直接改，不問；`synthesis_new` 裡 `confirmed` 的直接寫，沒確認的在回答末尾問 `question`（每次最多 3 個）。客戶說對 → `entity confirm`；說是同一個 X → `entity merge <它> --into X`；說名字叫 Y → `entity rename <它> --to Y` 再 `entity confirm`；改完實體重跑 `cards todo --json`，用新的那項寫卡；含糊再問一次，這句不寫。寫綜合卡被守門擋「大改動」就停下來，跟客戶說這次要改 N 張綜合卡，他說好才 `turn approve`，再重寫被擋的那張。
10. `synthesis_broken` 照 `reason` 與 `rules/synthesis.md` 第七節改；是「綜合卡該放在…」就寫到正確路徑，再 `entity clean <錯的路徑>`。
11. `missing` 的條目在回答末尾問，每次最多 3 件，問法照 todo 給的 `question`；客戶答過的程式不會再列。客戶怎麼答：
   - 明確說刪了 → `cards drop <編號>`，說已拿掉卡、說「退回上一版」救得回來；
   - 不是、先留著 → `pending resolve <待處理編號> --how "<客戶的話一句>"`；
   - 不用管 → `pending dismiss <待處理編號>`；
   - 含糊（「嗯」「好像吧」）→ 再問一次，這句話裡不做。刪除不能靠猜。
   - 讀不到的檔客戶說「先跳過」→ `pending dismiss <待處理編號>`（檔案換了程式會自動重讀）。
12. 這句話裡改了知識庫（寫卡、搬卡、拿掉卡、記待處理、記成不整理、登記或改實體、補掛實體、寫綜合卡），最後跑一次 `turn note "<白話一句>"`，一句講完全部做了什麼；再跑會蓋掉前一次（profile apply 程式自己記）。

## 回答末尾

- 一句講做了什麼：檔名最多列 5 個，多的講份數（「我把 報價_v3.txt 整理進去、進度.xlsx 更新了、估價單的卡跟著搬到信義王」）；有寫綜合卡加「綜合卡：新寫 X、更新 Y」（「信義王的現況改成第四版的總價了」）。
- 讀不到的講份數與原因（「合約_加密.pdf 讀不到：有密碼」），說記在 `系統/待處理.md`。
- 憑證檔、只在雲端的檔、太大的檔、讀取出錯的檔不做卡；todo 的 `skipped` 有份數時講一句（只在雲端的請客戶在檔案上按右鍵選「保留在這台電腦上」）。
- 這一批依設定不整理的，講一句種類與份數（「請款類 4 份依你的設定不整理」）。
- `missing_types` 有值：一句「有 N 份看起來跟廠商有關，但設定檔裡沒有『廠商』這一類，要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用」。
- 接著問 `entity_drift`、沒確認的 `synthesis_new`、`missing` 那幾件。

## 共通

- 回答客戶一律繁體中文。
- 一次一個指令；路徑加引號，不 cd，不用 && 串接。
- 程式拒絕（exit 3）就照 `references/commands.md` 的說法跟客戶講，不自己換方法、不繞路去讀原檔。
- 不自己讀寫 `.system/`、`系統/`、`總目錄.md`；都經 kb 指令。暫存檔可以 Read。
- 終端機不印檔案內容；卡裡只寫原檔有的，數字以原檔為準。

## 參考

- `references/carding.md`：製卡規則（讀法、檔頭欄位、段落與〔位置〕、長度、個資、特殊情況、補掛實體）。
- plugin 的 `rules/synthesis.md`：綜合卡怎麼寫。
- `references/commands.md`：`convert`、`cards todo|sync|drop|index|skip`、`profile check|apply`、`pending list|resolve|dismiss`、`entity …` 的參數、第一行長相、exit code、`--json` 欄位、拒絕原因怎麼跟客戶說。
