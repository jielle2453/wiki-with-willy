# CHANGELOG

一版一段、最新在上。每段四件事：改了什麼／為什麼／commit／spec。
bump `plugin.json` 版號的那個 commit 要同時寫這裡，不事後補；事後補就會變成第二份會漂的清單。
為什麼只寫 spec 或決策裡有的，查不到寫「待補」，不推測。

## 0.6.0 — 2026-10-16
- **改了什麼**：開場注入與核心規則。新 `rules/core.md` 十四條（R-01～R-14：三種真相分開、出處優先序、每句帶出處、不改原檔、退回走 `kb versions`、不猜、全庫操作不代跑、綜合卡寫法、外部動作先確認且帳密不落檔、對人中文對程式英文、建議下一步、被擋不繞路且同一回答做完、工作日誌的 `kb turn note`／`redact`／`journal recent`、kb 程式用 Bash 一次一個指令）。新 SessionStart hook `hooks/session.py`（startup｜resume｜clear｜compact）把 core.md 全文與狀態行塞進 additionalContext，狀態行第一版只有 `kb.py` 完整呼叫前綴與摺疊提示；hook 找到知識庫後出錯會印一行「開場程式出錯」。`skeleton/CLAUDE.md` 加哨兵句：沒看到開場段就是程式沒跑起來，AI 可查 python、客戶同意才裝、期間不改檔。回合的問剝掉 `<pasted_content>` 標籤。592 測試。
- **為什麼**：常駐層一直缺規則與程式位置，AI 要被點名才會跑 `kb.py`、沒拒絕代登入（B-031）；規則在對話裡才是機器保證，給路徑靠模型記得讀（I-036、D-087）；python 不在時偵測它的 hook 自己跑不起來，能活在外面的只有 CLAUDE.md（I-103、D-088）；D-059 補 R-13、R-14。測試兩輪，第一輪 review 抓到剝標籤黏字讓短關鍵詞遮不到、子代理會誤判哨兵句、hook 自己出錯被當成 python 壞掉。
- **commit**：e29c949、6b5e83c（實作，分支 spec/開場注入與核心規則，merge efb4422）
- **spec**：design/specs/2026-10-16-開場注入與核心規則.md

## 0.5.0 — 2026-10-16
- **改了什麼**：工作日誌。每句話一段記進 `系統/工作日誌/YYYY-MM.md`（真相是 `.system/journal/YYYY-MM.jsonl`，md 整月重渲染、最新在上）：問在 UserPromptSubmit 寫、結果由新的 Stop hook 從 payload 取（300 字、結論或摘要段優先、「…全文見對話」）、做了是程式地板句或 `kb turn note` 的 AI 一句、改了從回合帳抓；按 Esc 中斷的由下一句補成「（回答被中斷）」。程式自己做的事（`kb init`、`folders add|exclude`、`versions revert|delete-outputs`）在終端機跑時各記一段，對話裡叫的併進那句。新子命令 `kb turn note|redact`、`kb journal recent|render`。退回動作一律開新回合並成為目前回合，對話裡退回自己這句時新回合繼承問、做了、同意並帶 `journal_turn` 指回那句的日誌行。遮值一律先壓空白再遮，全文有遮到就不抽結論段；`prompt_head` 長度進 config（200）。573 測試。
- **為什麼**：六個面的「剛做了什麼」要有客戶看得懂、永久留的地方（D-065、D-082～D-086）；Stop 的 payload 直接帶最後回答，不解析 transcript（D-083）；規則不是保證，做了要有程式地板（D-084）。測試四輪，三輪在修退回動作回合與目前回合的糾纏（I-107）。
- **commit**：e36bbc4、e3834fb、270459e、161578e、464bd5a、0defa7d、0c793f7、1d633af（實作，分支 spec/工作日誌）
- **spec**：design/specs/2026-10-15-工作日誌.md

## 0.4.0 — 2026-10-15
- **改了什麼**：骨架與來源登記。`skeleton/`（CLAUDE.md 只寫身分與五區、settings.json 空殼）；`kb init <路徑>` 建五區、`.system/sources.json`、隱藏屬性，既有檔不覆寫；`kb folders add|exclude|list` 登記來源與排除（拒絕磁碟根、系統夾、互相包含、另一個知識庫、保留別名；spec 壓成相對→`~/`→絕對；排除對不到就拒絕；寫前備份進回合可退）。守門加憑證檔名樣式三道：目標路徑的段、指令裸 token、讀取範圍是資料夾時走訪（上限 `credential_scan_max_entries`，超過擋）；總資料夾內部豁免；理由句「客戶沒說可以讀」。回合 `prompt_head` 程式遮值（密碼、帳號、token 等關鍵詞後整個子句）。守門舊洞順手補：不帶斜線的 `.`／`..` 經區判斷、`cmd /c` 的 `/c` 不當路徑、大括號與字元類的前綴夾保底、寫入動詞清單補。439 測試。
- **為什麼**：ingest、第一個 skill、桌面版確認都要一個 plugin 自己建出來的總資料夾（D-077、D-078、D-080）；一登記來源就立刻可讀，raw 裡的密碼表會跟著開放（I-032、D-079）；密碼是客戶一件一件決定的事，不是開關（D-081）。測試十二輪，八輪是上一輪 shell 字串解析的修正帶出的退步，最後把區判斷的寫入判定退回守門題原版（I-106）。
- **commit**：ff46b8e、30241f4、fd50114、0e6a2c9、79d7c5e、76e61d1、fdff607、1b973ac、2b1ad4f、0ef6ac4、ccb8979、bb98026、0eff88b、18b6b8c、783f82b、d8892f6（實作，merge 9b7af0b）
- **spec**：design/specs/2026-10-14-骨架與來源登記.md

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
