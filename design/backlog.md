# 待辦與延後

日期：2026-10-11
給誰看：你與所有 session。
什麼時候更新：任何 session 都能新增一行；改狀態只在設計 session。

格式：編號｜日期｜狀態 open／deferred／done｜一句話｜來自。

## 第一批要做的（順序）

| # | 日期 | 狀態 | 事 | 來自 |
|---|---|---|---|---|
| B-001 | 2026-10-11 | open | repo 殼（spec approved 2026-10-12：specs/2026-10-12-建殼.md）：plugin.json、LICENSE、CHANGELOG 格式、CLAUDE.md 地圖、測試 runner、.gitattributes、.gitignore、settings 放行 | 討論 |
| B-002 | 2026-10-11 | done | 測試資料生成與 `來源.zip`：2026-10-12 完成 44 份，腳本 llm-wiki-test\生成\generate.py。.doc 是手寫 Word 97 檔未經真 Word 驗證，密碼 PDF 密碼 lin2026 | conventions 六 |
| B-003 | 2026-10-11 | done | 事故清單 `incidents.md`：從兩邊 CHANGELOG、註解、skill 事故段採集 | 討論 |
| B-004 | 2026-10-11 | done | 量兩邊常駐成本與規則行數，含開場 hook 注入（版本、變動清單、健檢提醒、日程窗口、規則路徑），定 U-6 的預算 | decisions 未決 |
| B-005 | 2026-10-11 | open | 架構文件初稿審閱（本批結束前你看一次） | 討論 |
| B-006 | 2026-10-11 | done | spike：測試 session 中途改 `.claude/settings.json` 的 additionalDirectories 或 Read 放行規則，看對工作目錄外的 Read 會不會立刻生效、要不要重開。結果決 U-8。**等你說跑才跑**。同時驗 `.claude/settings.json` 的放行規則能不能寫 `${CLAUDE_PLUGIN_ROOT}`，不能的話 `kb upgrade` 要重寫規則（I-073） | decisions U-8 |
| B-008 | 2026-10-12 | open | 桌面版安裝與升級，方向在 D-072。先 spike：桌面版 `/plugin marketplace add <本機路徑>` 可不可以、cache 路徑長相、換資料夾後要不要 `/plugin update`、hook 能不能聯網 | 討論 |
| B-009 | 2026-10-12 | open | B-007 完成後，在桌面版重跑兩步：additionalDirectories 重開後生效嗎、auto mode 放不放行外部讀取 | B-006 |
| B-007 | 2026-10-12 | done | B-006 後續 spike：純 ASCII 路徑（`llm-wiki-test\spike-src`）重測 additionalDirectories、`Read(C:/...)`、`Read(${USERPROFILE}/...)` 三種，每種「關掉重開接回同一對話」與「全新 session」各一次；要排除中文路徑與 resume 兩個干擾。結果：ASCII 路徑下三種設定三種載入全部跳詢問，排除中文路徑與 resume；報告 reports/B-007-spike.md | B-006 |
| B-010 | 2026-10-12 | done | CHANGELOG 的 commit 欄無法寫入自己那個 commit 的 hash（建殼第一個 commit）；定：發行時由設計 session 填實作 commit 的 hash（process 六） | B-001 建殼實作 |
| B-011 | 2026-10-12 | deferred | 建殼測試觀察：tests/test_lib_io.py 用 sys.path.insert（驗收 15 只查 scripts/，下題統一用 subprocess 或 conftest 式匯入）；Windows 上 stdout 行尾 CRLF；Git Bash 下 unittest 的 skip 原因到 stderr 顯示亂碼（stderr 也要 reconfigure） | 建殼測試 |

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
