# 壞掉或放錯位置的綜合卡（寫給製卡者）

綜合卡批次的項目有 `synthesis_broken` 時照這份；主對話照 `plan` 的 `cards` 那步自己修的也照這份。

## 材料的欄位

- `synthesis_broken`：`{"path","reason"}`。

## 怎麼修

`synthesis_broken` 每項 `{path, reason}`；製卡助手拿到的批次另帶跟 `synthesis_stale` 一樣齊的材料：`target`（該放的路徑）、`header`（該有的檔頭，照抄）、`entity_id`、`name`、`type`、`sections`、`cards`（掛的檔案卡）、`statements`、`rulings`、`findings`。寫法看 `${CLAUDE_PLUGIN_ROOT}/rules/synthesis.md`，寫之前先讀它。

- `reason` 是「綜合卡該放在 …」（寫到了錯的路徑）：Read `path` 那張拿正文，檔頭換成 `header`，整張寫到 `target`，再跑 `kb entity clean <path>`（相對總資料夾）清掉錯的那張。
- 其他 `reason`（檔頭有錯、檔頭解析不了）：檔頭換成 `header` 整張寫回 `target`；正文讀得到就沿用、照 `reason` 改，讀不到或空的就照 `cards`、`statements`、`rulings` 從頭寫，不憑空補內容。
- 正文各段照 現況、自訂段、推算、相關檔、紀事 的順序排。
- 沒帶 `header` 的（找不到是哪個實體的）不會派給製卡助手，由主對話照 `plan` 的 `cards` 那步處理，處理不了就跟客戶說。
