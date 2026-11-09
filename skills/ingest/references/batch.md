# 批次（寫給製卡者）

## 批次（card-maker）

- 第一步跑 `kb cards batch <批次> --json`：`prefix` 是 kb 指令前綴（下面的 `kb …`、`convert …` 接在它後面）；`vault` 是總資料夾的絕對路徑，項目裡相對總資料夾的路徑（`dir`、`existing_card`、`path`、`target`、`card_path`）都另給 `<鍵>_abs` 絕對路徑，讀寫一律用它；`kind` 是 cards｜agenda｜synthesis｜adopt（adopt.md）；`read` 是先讀的規則檔（絕對路徑），每份整份讀；`items` 是這批要做的，每項的欄位在那種批次的規則檔、多 `bucket`（來自哪一桶，不是 `type`：`synthesis_new` 的 `type` 是實體型別；new、stale、broken、unlinked、agenda_recheck、agenda_unscanned、synthesis_new、synthesis_stale、synthesis_broken）；`profile`、`entities` 同下一節。已經做好的項目不再列。
- 每種批次照它的規則檔做：`cards` 照 carding.md（每張接著照 agenda-scan.md 抽日程）、`agenda` 照 agenda-scan.md、`synthesis` 照 `rules/synthesis.md` 與 synthesis-batch.md（有 `synthesis_broken` 另讀 synthesis-broken.md）、`adopt` 照 adopt.md。
- 清單只用 `items`，不跑 `cards todo`、不自己找檔；不跑 `turn note`、`cards dispatch`。
- 做完照 card-maker 本文的回報格式回一則給主對話：讀舊 wiki 以外的批次只有份數與沒做成的原因，不寫檔案內容、摘要、人名、金額、日期（D-022），「被擋或打回：」那行的寫法也在那裡；讀舊 wiki 的批次照 adopt.md 的格式。

## 材料的共用欄位（`cards batch` 與 `cards todo --json`）

- `profile`：客戶設定檔（做卡用的 `card_types`、`exclusions`、`profile_missing` 在 carding.md）：`types`（`[{"name","category"}]`）、`synthesis_sections`（`[{"type","sections"}]`，設定檔第四節）、`about`（`[{"name","kind","aliases"}]`，客戶自己）、`tags`（設定檔第八節的標籤字典 `{型別: [{"field","words","multi"}]}`，寫綜合卡時照它標，`rules/synthesis.md` S-33）、`preferences`（設定檔第六節偏好的句子清單，寫卡照 carding.md 第三節第 4 點）。
- `entities`：`[{"name","type","aliases","self"}]`，登記的實體（`self` 是 false、company 或 person）；檔案卡 `實體` 欄照 `name` 寫。案子這類「事」的另有 `progress`（ongoing｜closed｜paused）與 `asked_evidence`（問過結案的依據，同一份不再問）。
