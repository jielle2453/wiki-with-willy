# 待辦與延後

日期：2026-10-11
給誰看：你與所有 session。
什麼時候更新：任何 session 都能新增一行；改狀態只在設計 session。

格式：編號｜日期｜狀態 open／deferred／done｜一句話｜來自。

## 第一批要做的（順序）

| # | 日期 | 狀態 | 事 | 來自 |
|---|---|---|---|---|
| B-001 | 2026-10-11 | done | repo 殼（v0.1.0，2026-10-12 發行；specs/2026-10-12-建殼.md）：plugin.json、LICENSE、CHANGELOG 格式、CLAUDE.md 地圖、測試 runner、.gitattributes、.gitignore、settings 放行 | 討論 |
| B-002 | 2026-10-11 | done | 測試資料生成與 `來源.zip`：2026-10-12 完成 44 份，腳本 llm-wiki-test\生成\generate.py。.doc 是手寫 Word 97 檔未經真 Word 驗證，密碼 PDF 密碼 lin2026 | conventions 六 |
| B-003 | 2026-10-11 | done | 事故清單 `incidents.md`：從兩邊 CHANGELOG、註解、skill 事故段採集 | 討論 |
| B-004 | 2026-10-11 | done | 量兩邊常駐成本與規則行數，含開場 hook 注入（版本、變動清單、健檢提醒、日程窗口、規則路徑），定 U-6 的預算 | decisions 未決 |
| B-005 | 2026-10-11 | open | 架構文件初稿審閱（本批結束前你看一次） | 討論 |
| B-006 | 2026-10-11 | done | spike：測試 session 中途改 `.claude/settings.json` 的 additionalDirectories 或 Read 放行規則，看對工作目錄外的 Read 會不會立刻生效、要不要重開。結果決 U-8。**等你說跑才跑**。同時驗 `.claude/settings.json` 的放行規則能不能寫 `${CLAUDE_PLUGIN_ROOT}`，不能的話 `kb upgrade` 要重寫規則（I-073） | decisions U-8 |
| B-008 | 2026-10-12 | done | 桌面版安裝與升級，方向在 D-072。spike 做完，報告 reports/B-008-spike.md，結論待設計 session 定；原題：桌面版 `/plugin marketplace add <本機路徑>` 可不可以、cache 路徑長相、換資料夾後要不要 `/plugin update`、hook 能不能聯網 | 討論 |
| B-009 | 2026-10-12 | done | 併入 B-008 測完：桌面版 additionalDirectories 寫入即熱載入、重開也生效（跟 VS Code extension 相反）；auto mode 下 hook deny 照擋 | B-006 |
| B-007 | 2026-10-12 | done | B-006 後續 spike：純 ASCII 路徑（`llm-wiki-test\spike-src`）重測 additionalDirectories、`Read(C:/...)`、`Read(${USERPROFILE}/...)` 三種，每種「關掉重開接回同一對話」與「全新 session」各一次；要排除中文路徑與 resume 兩個干擾。結果：ASCII 路徑下三種設定三種載入全部跳詢問，排除中文路徑與 resume；報告 reports/B-007-spike.md | B-006 |
| B-010 | 2026-10-12 | done | CHANGELOG 的 commit 欄無法寫入自己那個 commit 的 hash（建殼第一個 commit）；定：發行時由設計 session 填實作 commit 的 hash（process 六） | B-001 建殼實作 |
| B-011 | 2026-10-12 | deferred | 建殼測試觀察：tests/test_lib_io.py 用 sys.path.insert（驗收 15 只查 scripts/，下題統一用 subprocess 或 conftest 式匯入）；Windows 上 stdout 行尾 CRLF；Git Bash 下 unittest 的 skip 原因到 stderr 顯示亂碼（stderr 也要 reconfigure） | 建殼測試 |
| B-012 | 2026-10-12 | deferred | `claude plugin validate` 每次都警告「根目錄 CLAUDE.md 不會被當 plugin context 載入」，在 CLAUDE.md 註明可忽略；順手把 validate 加進 CLAUDE.md「改完要做的事」 | 建殼確認 |
| B-013 | 2026-10-12 | open | 下一題（第一個 skill 或 command）的確認情境必含「/help 或 /plugin 看得到 wiki-with-willy」，補建殼沒拿到的載入正面證據；順便弄清楚 --plugin-dir 載的 plugin 在 /plugin 裡列不列。守門確認第 1 步已給 hook 層證據（2026-10-13）；事務層確認人眼看到 /plugin Installed 列 `wiki-with-willy inline`（2026-10-13）；/help 可見留給第一個 skill | 建殼確認 |
| B-014 | 2026-10-12 | open | spike：CLI `claude plugin update wiki-with-willy@<mp>` 能不能由 `kb upgrade` 用 subprocess 代跑、桌面版重開後吃不吃；能就 D-072 維持一句「升級」，不能就修訂成「說升級後我帶你點四步」（I-102） | B-008 |
| B-015 | 2026-10-12 | done | 守門 spec 疑義待設計 session 拍板：(1) Bash 只提到登記來源或寫 Z1／Z3 時實作回 pass 不回 allow（spec 第 2 節「其餘不表態」vs 驗收 11「allow」vs D-073）；(2) Z8 寫擋的 reason_code 表上沒有，暫用 protected；(3) `kb guard decide` 例子 `cat a.txt > b.txt` 標 Z9，但無斜線 token 不抽路徑，實作會是 unparsed 或 Z8 | 守門實作 |
| B-016 | 2026-10-12 | open | 查 Claude Code hooks 設定有沒有「hook 失敗或逾時即 deny」的欄位（onFailure 之類）與最低版本；有就加進 hooks.json，沒有就靠開場偵測 python（I-103） | 守門實作 |
| B-017 | 2026-10-12 | deferred | 守門測試的低優先：guard-status 每次呼叫都讀改成每天一次快取；`.gitattributes` 對 tests/fixtures 的二進位檔標 binary；POSIX 上 CLAUDE.md 大小寫；型別夾根新檔理由句用 Z8；origin/HEAD 沒設讓 security-review skill 跑不起來（已設）；Z9 理由句結尾補句號；fixture a.txt 內容別用 hello；deny 在終端機顯示成「Error: PreToolUse:X hook error: <理由句>」，事務層確認已人眼看過（2026-10-13）：理由句完整但前綴嚇人，是 Claude Code 固定格式，只能靠理由句白話（併 B-110） | 守門測試、確認 |
| B-018 | 2026-10-13 | deferred | AI 產出的指紋先用位元組 sha256，`kb convert` 出來後 Office 檔改文字指紋（D-014 補、D-076） | 事務層設計 |
| B-020 | 2026-10-13 | done | v0.3.0 README 加「環境需求」一節（2026-10-13）。原題：發行 README 寫明只支援 Windows、Python 3.10 以上、PATH 上要有 `python`（hooks.json 是裸 python，沒有就 fail open，I-103）；plugin-dev 的 validate-hook-schema.sh 沒 jq 會誤報 Invalid JSON，不拿它當發行關卡 | 事務層 plugin-validator |
| B-021 | 2026-10-13 | deferred | ledger.tsv 的 path_or_command_head 只留前 120 字，深路徑看不到檔名；改成保留尾端或拉長；長 TEMP 下 test_ledger 會紅 | 事務層測試第二輪 |
| B-022 | 2026-10-13 | open | 守門：不帶磁碟代號的根路徑（`\案子\...`）normalize 後判 Z11 不表態；事務層 delete-outputs 刪前也不再查 zone。守門題補一條 | 事務層測試第三輪 |
| B-023 | 2026-10-13 | deferred | 事務層低項：T 建成品→退回→U 重寫→退掉 U 後 T 那筆仍 superseded 刪不到；未來時間回合夾在 list 排第一且每句記 log；delete-outputs 固定取第一個候選可能卡住；revert --delete-outputs 沒比 sha；CLI 鎖 30 秒等於殘留門檻；LOCK_TIMEOUT 改模組全域；`\\?\` 加結尾點檔名未驗。細節 reports/事務層-測試.md 第三輪 | 事務層測試第三輪 |
| B-024 | 2026-10-13 | deferred | 守門誤擋：Bash heredoc 內文提到總資料夾第一層路徑（寫知識庫外的報告）被當寫入目標 deny Z8；引號內與 heredoc 內的路徑要不要算寫入目標，併 B-110 看誤擋率 | 事務層確認 |
| B-019 | 2026-10-13 | deferred | `kb.py` 由模型呼叫時拿不到 session_id，CLI 用 `turns/current.json`，兩個對話同開同一知識庫時可能記到另一個 session 的回合；collab（B-206）時解 | 事務層設計 |
| B-025 | 2026-10-14 | deferred | 高風險子命令（restore、relocate、全部重做）的「問一句」由守門對那幾個 kb.py 呼叫回 ask，不寫 settings 的 permissions.ask：守門對 kb.py 回 allow 會蓋過 ask（D-079）；那些子命令出現的那題做 | 骨架與來源登記設計 |
| B-026 | 2026-10-15 | deferred | 骨架與來源登記測試低項：展不開的來源（`${NAS}/…`）不參與 add 的重疊比對；`_survey` 每份檔重比每層夾名；理由句 `_original_segment` 引號黏在段尾找不回原始大小寫；guard 重用 `cmdparse._tokens`，要公開改名；第二輪 N5：走訪遇到夾名命中憑證樣式就剪掉，`folders add` 漏掉藏在底下的另一個知識庫與份數；走訪跳過排除夾用字串比對，排除根經 junction 或 8.3 短名另寫時跳不過（只多擋）；第三輪低項：`cd -`／裸 `cd` 吞下一個字、`dir /s` 不走訪、junction 指向排除夾、`ls \| grep md` 被當 unparsed 寫入（守門題舊）；第四輪：`\"` 黏住子指令、行尾 `\` 續行、`rg.exe`；第五輪程式整理：guard 與 cmdparse 兩套切字器合一、`_tokens_q` 是複製、`token_hit` 測試對不到正式碼、`folder_scan` 每次重讀 config；`glob.glob(include_hidden)` 是 3.11 起，3.10 上 PowerShell 的 `*` 展不到點開頭檔、只靠第三道；第六輪：`$HOME/.aws/credentials` 屬 `$VAR` 界線、`tar czf … 案子/信義王` 被 entity_file 擋（守門題舊）；第七輪：旗標黏值 `7z x -o"<R>/…"` 抽不到、guard `WRITE_VERBS` 與 cmdparse `WRITE_WORDS` 兩份動詞清單 | 骨架與來源登記測試第一～七輪 |
| B-027 | 2026-10-15 | open | 工作日誌題：「問」那行用 `credentials.mask_secrets` 同一個函式遮值；加 AI 第二層 `kb turn redact`（客戶給帳密時 AI 把這回合的問改成它認出的遮法，補程式猜不到的寫法）；建置告知加一句「敏感帳密不要打在對話裡，Claude Code 自己的紀錄與雲端都有」（D-081） | 骨架與來源登記設計 |

## 補漏洞型延伸（落在既有位置，不加分類）

| # | 日期 | 狀態 | 事 | 落在哪 |
|---|---|---|---|---|
| B-101 | 2026-10-11 | deferred | 掃描 PDF 做 OCR、圖片與照片製卡 | ingest/formats、kb convert |
| B-102 | 2026-10-11 | deferred | `.doc` `.ppt` 用客戶的 Office 自動轉、Excel 重算與轉 PDF；第一版 `kb office` 只偵測與讀，不開 COM（I-013） | ingest/formats、kb office |
| B-103 | 2026-10-11 | deferred | Google 文件捷徑、NAS、隨身碟路徑 | ingest/local |
| B-104 | 2026-10-11 | deferred | 規模大了之後的本地搜尋索引，索引是可拋棄衍生物 | kb search、answer |
| B-105 | 2026-10-11 | deferred | 索引過大拆年份 | kb reindex |
| B-106 | 2026-10-11 | deferred | 客戶設定檔加了格式調整後，舊卡補欄位 | lint 檢查項、kb batch 標重做 |
| B-107 | 2026-10-11 | deferred | API 製卡執行器：到時才加 config 鍵與 kb/executors/，規則檔不動（D-053） | kb/executors/ |
| B-108 | 2026-10-11 | deferred | 額度與時間估算更準、建置分天 | kb inventory、build-cards |
| B-109 | 2026-10-11 | deferred | 回答末尾建議問題、交付速查卡 | rules/next-step.md、build-finish |
| B-110 | 2026-10-11 | deferred | 守門誤擋率、失敗訊息白話化 | guard.py、rules/core.md |
| B-111 | 2026-10-11 | deferred | Mac | _lib/paths、envcheck |

## 選取型延伸（依客戶特質開關）

| # | 日期 | 狀態 | 事 | 槽位 |
|---|---|---|---|---|
| B-201 | 2026-10-11 | deferred | Email（Gmail、Outlook）來源，Claude 取腳本記 | 來源型別 |
| B-202 | 2026-10-11 | deferred | 行事曆讀寫 | 來源加輸出 |
| B-203 | 2026-10-11 | deferred | Line，做法待定 | 來源加輸出 |
| B-204 | 2026-10-11 | deferred | 線上版雲端硬碟、Notion | 來源型別 |
| B-205 | 2026-10-11 | deferred | 會議錄音轉文字 | 來源型別 |
| B-206 | 2026-10-11 | deferred | collab：多 session 主管制、佔用偵測、角色卡 | 模式 |
| B-207 | 2026-10-11 | deferred | 團隊共享、個人與團隊空間分開：一個知識庫當另一個的唯讀來源 `kb://`；對方的綜合卡不重 ingest，只建指標卡回正本讀 | 模式加來源型別 |
| B-208 | 2026-10-11 | deferred | 主動推送：會前背景、逾期承諾，外部排程跑 `kb agenda brief` | 模式加輸出 |
| B-209 | 2026-10-11 | deferred | 排程健檢 | 模式 |
| B-210 | 2026-10-11 | deferred | 起草 PowerPoint、輸出 Google 文件 | 輸出去處 |
| B-211 | 2026-10-11 | deferred | 既有 wiki 接入 | build-setup 分支 |

## 從 llm-wiki 延後的想法

| # | 日期 | 狀態 | 事 | 歸哪 |
|---|---|---|---|---|
| B-301 | 2026-10-11 | deferred | 開工與收工儀式 | collab，單人不需要 |
| B-303 | 2026-10-11 | deferred | 取用副本夾一致性檢查 | lint 檢查項 |
| B-304 | 2026-10-11 | deferred | 像素辦公室 | 獨立 plugin，不進本架構 |
| B-305 | 2026-10-11 | deferred | 教練開關設定 | 不做，見 D-007 |

## 不做

自建桌面應用、本機模型版本、知識圖譜視覺化。來自競品分析的決定。行業預設包（原 B-212，D-040）。日誌摺疊（原 B-302，D-065）。
