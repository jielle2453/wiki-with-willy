# CHANGELOG

一版一段、最新在上。每段四件事：改了什麼／為什麼／commit／spec。
bump `plugin.json` 版號的那個 commit 要同時寫這裡，不事後補；事後補就會變成第二份會漂的清單。
為什麼只寫 spec 或決策裡有的，查不到寫「待補」，不推測。

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
