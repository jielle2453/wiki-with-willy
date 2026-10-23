# 收舊的 llm-wiki

舊 llm-wiki（GitHub 的 llm-wiki-plugin）的資料夾：`wiki/`（舊 AI 寫的專案頁、主題頁）、`raw/`（原檔）、`raw/.manifest.json`（外掛的夾）、`wiki/hot.md`（常駐規則、最近做了什麼、等確認的事）、`wiki/log.md`（每件事的過程與結論）、`CLAUDE.md`（規則）、`wiki/agenda.md`（日程）。做法（D-193）：舊 AI 寫的頁不搬進來；原檔登記成資料夾、照我們的規則從頭做卡；舊 wiki 只讀一次，把只有她說過、原檔裡沒有的事撈出來給她看，說好的才記。**舊資料夾一個字都不改**，留著當她的備份。

## 一、認出來（訪談那步）

- 她說以前有一個 wiki（「朋友幫我裝過一個 wiki，在桌面」）→ 問在哪，跑 `kb build adopt "<路徑>" --check --json`。印「這不像舊的 llm-wiki 資料夾」→ 照講，問她要不要當一般資料夾登記。
- 照結果講：`raw` 有沒有、`linked` 外掛了哪些夾（`exists` 是 false 的講這台找不到）、`wiki_pages` 幾頁、`hot_chars`、`log_chars` 多長、`rules` 幾條規則、`agenda` 幾條日程。
- 講做法（上面那段），說好才：`kb build set adopt "<路徑>"`；`raw` 與每個找得到的 `linked` 各跑一次 `kb folders add "<路徑>"`（只讀、從頭做卡）。
- 舊的 llm-wiki 外掛要停用：環境那步會認出來，沒停用先請服務人員停。

## 二、撈出來（收舊 wiki 那步，綜合卡寫完之後）

- 先講：要請製卡助手在背景把舊 wiki 讀一遍，撈出她說過的事、規則、有日期的事、還在等確認的，讀完列給她挑；舊資料夾不會動。說好才做。
- `kb build adopt "<路徑>" --extract --json`：程式把要讀的文字切成段（`chunks`），你不用讀那些段。
- `kb cards dispatch --adopt --json`：照 ingest 的「讀派工結果」派（`batches` 用 Agent 工具背景派、`say` 照講、收到通知再跑同一個指令），直到 `done`。
- 每個製卡助手回報的是候選清單，一行一條：`口述｜實體｜日期｜一句`、`規則｜一句`、`日程｜日期｜實體｜一句`、`待確認｜一句`；`- <段>：<原因>` 是那段沒讀成。把每一批的回報收齊。

## 三、列給她看、說好的才記

- 合併各批：同一件事幾段都提到算一條；舊 AI 自己的操作紀錄（哪天整理了什麼）不算，工作日誌從建置那天起算。
- 先對已經有的：`kb statement list --json`、`kb agenda list --json`、`kb entity list --json`；已經記過的、原檔做成的卡裡已經有的不列。實體名稱照 `entity list` 的寫，清單沒有的那條先問她掛誰。
- 照四類分組列給她，一條一行、帶日期，問哪幾條要記；她說不要的不寫。條數多就一類一類問。
- 說好的經 kb 寫（指令與成功句看 `${CLAUDE_PLUGIN_ROOT}/skills/note/references/`）：
  - 口述：`kb statement add --entity <名> --text "<一句>"`（日期寫進那一句裡）。
  - 規則：管怎麼回答的記成偏好 `kb profile add 偏好 偏好=<一句>`；管某個案子某份檔的記成裁示 `kb ruling add --kind 口述為準|不採用 --text "<她的話>"`（照 note 的 ruling.md 分）。
  - 有日期的事：還沒到的才進日程 `kb agenda add --kind <到期｜會議｜承諾｜提醒> --date <YYYY-MM-DD> --text "<一句>" --entity <名>`；過去的不加。
  - 還在等確認的：當場問她，她答了照 note 記。
- 讀不成的段照實講一句（第幾段、原因），不重派。
- 做完講一句記了幾條、哪幾類，再 `kb build done adopt`。
