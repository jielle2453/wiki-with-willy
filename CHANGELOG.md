# CHANGELOG

一版一段、最新在上。每段四件事：改了什麼／為什麼／commit／spec。
bump `plugin.json` 版號的那個 commit 要同時寫這裡，不事後補；事後補就會變成第二份會漂的清單。
為什麼只寫 spec 或決策裡有的，查不到寫「待補」，不推測。

## 0.3.0 — 2026-10-13
- **改了什麼**：事務層。UserPromptSubmit 每句話開一個回合（按 session 存 `.system/turns/`）；PreToolUse 寫綜合卡、AI 產出、檔案索引前先備份到 `.system/versions/<回合>/`，指令寫這三區改擋；PostToolUse 把 AI 產出登記進最小版 `.system/inventory.json`；`kb versions list|revert|delete-outputs`、`kb turn current|approve`；同回合修改既有綜合卡達 10 張就停（`config/defaults.json`）；`_lib/io` 加檔案鎖與 os.replace 重試；298 測試。
- **為什麼**：守門已放行寫入卻沒備份，等於能寫不能退（D-074）；成品要有登記才查得到、退得掉（D-076、D-011）；一句話改太多張要先問（D-075、I-028）。測試三輪補了退回與刪成品的語意洞（H1、R2-1～R2-4）。
- **commit**：fb7041a、b82aae5、472dc7a、f94f37d、2d64c9c（實作）
- **spec**：design/specs/2026-10-13-事務層.md

## 0.2.0 — 2026-10-13
- **改了什麼**：PreToolUse 守門 `hooks/guard.py`：客戶原檔讀放行寫擋、排除夾讀寫都擋、總資料夾五區照路徑表、`kb.py` 呼叫免詢問；`kb guard decide` 子命令；`.system/ledger.tsv` 事件帳、`guard-status/<主機>.json`、`hook-errors.log`；sources.json schema；路徑正規化與指令字串解析；142 測試。
- **為什麼**：讀客戶 raw 不能靠 permissions 規則（B-006～B-008 實測），守門 hook 回 allow／deny 是唯一在 auto mode 也有效的機制（D-073）；三輪測試補了七個高中嚴重的繞法。
- **commit**：7f816c5、2436d2d、583ae05、a34d57a（實作）
- **spec**：design/specs/2026-10-12-守門.md

## 0.1.0 — 2026-10-12
- **改了什麼**：repo 與 plugin 最小殼：manifest、LICENSE、README、CLAUDE.md、`kb.py` 入口與 `version`、`_lib/io`、五個測試檔、git 初始化。
- **為什麼**：後面每一題都在這個殼上長；預算與版號對齊從第一天就由測試守。
- **commit**：773954d（實作）
- **spec**：design/specs/2026-10-12-建殼.md
