# 約定

日期：2026-10-11
給誰看：你與所有 session。
什麼時候更新：只在設計 session 改。其他 session 覺得約定不夠用，寫進 backlog。

## 一、三個資料夾

| 資料夾 | 放什麼 | git |
|---|---|---|
| `C:\Users\Jie\Desktop\llm-wiki-j\` | repo：plugin 根目錄、程式、測試、設計文件 | 是 |
| `C:\Users\Jie\Desktop\third party\` | 舊的 `llm-wiki-plugin-main\`、`plugin\`。唯讀參考，**路徑固定不搬**，spec 的來源對照靠它 | 否 |
| `C:\Users\Jie\Desktop\llm-wiki-test\` | 確認環境：假來源、總資料夾 | 否 |

plugin 名稱 `wiki-with-willy`（D-069），指令前綴 `/wiki-with-willy:`；repo 資料夾仍叫 `llm-wiki-j`。不能叫 `llm-wiki`，會跟已安裝的撞。

## 二、文件

```
llm-wiki-j/
├─ README.md            給服務人員與使用者：裝、用、升級
├─ CLAUDE.md            給改 repo 的 Claude：原始碼地圖、改完要做的事
├─ CHANGELOG.md         一版一段：改了什麼／為什麼／commit／spec
├─ LICENSE
└─ design/
   ├─ process.md        流程
   ├─ conventions.md    本檔
   ├─ architecture.md   架構
   ├─ decisions.md      決策 D-nnn，含未決區
   ├─ incidents.md      事故 I-nnn
   ├─ backlog.md        待辦與延後 B-nnn
   ├─ specs/            YYYY-MM-DD-題目.md
   ├─ reports/          題目-測試.md、題目-確認.md
   └─ research/         產品依據：reports/、marketing/、reference/。唯讀，只在設計 session 引用
```

遠端是私有 repo。`design/research/` 含合約草稿與定價，repo 不公開。

**一件事只住一個地方：**

| 內容 | 只放這裡 |
|---|---|
| 門檻與預設數字 | `config/defaults.json`，每個鍵旁一個 `_why_<鍵>` |
| 事故 | `incidents.md`；CHANGELOG 與 skill 只引用編號 |
| 決策與理由 | `decisions.md`；spec 與規則只引用編號 |
| 某版改了什麼 | `CHANGELOG.md` |
| 延後的想法、別的 session 發現的問題 | `backlog.md` |
| 怎麼改 repo | `CLAUDE.md` |
| 怎麼用 plugin | `README.md` |
| 訪談要問什麼 | `skeleton/系統/客戶設定檔.md` 空白版的提示句（D-052） |
| 給客戶看的說明（速查卡、資料去向） | `skeleton/` 或由 build 生成到 `系統/建置報告.md` 前半，不在 design |
| 執行規則 | `rules/`，每條有編號；理由不寫在規則裡 |

**每份文件：** 開頭三行寫日期、給誰看、何時更新。引用不抄。能從程式推導的清單不手寫；手寫的標「本清單會漂，以 X 為準」。日期 `YYYY-MM-DD`。不新增上表以外的文件，真的需要先改上表。

**狀態詞固定：** spec `draft｜approved｜implemented｜verified｜confirmed｜released｜done`；backlog `open｜deferred｜done`。

**誰能寫什麼：**

| session | 能寫 | 不能寫 |
|---|---|---|
| 設計 | 全部 | — |
| 實作 | 程式、測試、`backlog.md` 只能新增 | spec、decisions、architecture、README |
| 測試 | `reports/<題目>-測試.md` | 程式、spec |
| 確認 | `reports/<題目>-確認.md` | 程式、spec |

**格式：** `decisions.md` 一條一段，編號 `D-nnn`，五行：日期、選了什麼、為什麼、放棄了什麼、重談條件。`incidents.md` 一條一段 `I-nnn`，四行：來源、發生過什麼、合併版會不會踩、客戶會不會遇到。`backlog.md` 一行一條 `B-nnn`：日期、狀態、一句話、來自哪題。

**要查什麼看哪裡（客戶端）：**

| 要查 | 看 |
|---|---|
| 某回合 AI 改了哪些檔、怎麼退 | `.system/versions/<回合>/` 回合帳 |
| 客戶問過什麼 | `系統/工作日誌/` |
| AI 對知識庫改了什麼（白話） | `系統/工作日誌/` 每段的「做了」行，或問「最近改了什麼」 |
| 守門腳本還活著嗎 | `.system/guard-status/<機器>.json` |
| 守門結果變好或變壞 | `.system/ledger.tsv` |
| hook 自己出錯 | `.system/hook-errors.log` |
| 升級過什麼 | `.system/upgrade-history.json` |

## 三、版本控制

| 項目 | 約定 |
|---|---|
| 初始化 | 第一個 commit 只有殼 |
| 分支 | `main` 只在發行時動。一題一個分支 `spec/<題目>`，實作與測試都在分支上。單一工作目錄，切分支，不用 worktree |
| 發行 | 設計 session 併回 main、bump、CHANGELOG、tag `vX.Y.Z`、push。只在你說「發行」之後 |
| 版號 | semver。首位客戶試跑前停在 `0.x`；功能加 minor，修 bug 加 patch |
| commit | 第一行 `<題目>: <一句話>`；結尾 `Spec: design/specs/<檔名>` 加各 session 自己的 Co-Authored-By；用 `git commit -- <檔>` 只提交自己改的檔，不 `-a`（I-093） |
| author | 實作 session `git -c user.name="實作:<題目>" commit`，只改 user.name。測試與確認 session 不 commit |
| 行尾 | `.gitattributes`：`* text=auto eol=lf` |
| 不進 repo | `__pycache__/`、`*.pyc`、`.claude/settings.local.json`、`*.tmp`、測試環境、任何客戶資料 |
| 版號對齊 | 測試檢查 `plugin.json` 與 CHANGELOG 最新一段一致 |

## 四、技術基線

| 項目 | 約定 |
|---|---|
| 目標環境 | 客戶用 Claude Code 桌面版，Windows；plugin 由本機 marketplace 載入（做法在 B-008）。開發、實作、測試用 CLI 或 VS Code；確認 session 一律用桌面版 |
| Python | 3.10 以上 |
| 相依 | 核心只用標準函式庫。`markitdown`、`pdfplumber` 選配，缺了降級並明講 |
| 編碼 | 我們自己的檔（JSON、md、設定、腳本輸出）一律 UTF-8，讀寫帶 `encoding=`；腳本開頭 `sys.stdout.reconfigure(encoding="utf-8")`。客戶原檔由 `kb convert` 偵測：整份試 UTF-8，再試 cp950，都不行標「讀不到：編碼」明講，不靜默取代字元（I-003）。另存的純文字一律 UTF-8，CSV 帶 BOM 給 Excel |
| 平台 | Windows 優先；`pathlib`；長路徑與 Git Bash 路徑都要認 |
| 腳本入口 | 唯一入口 `scripts/kb.py <子命令>`，呼叫寫 `python "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" <子命令>`。統一 `--json`、`--help`。不用 `python -m`。模型用 Bash 工具呼叫、一次一個指令、路徑加引號、不 cd |
| 門檻 | 全在 `config/defaults.json`；客戶端 `.system/config.json` 覆寫 |
| 狀態檔 | `.system/*.json` 每個帶 `schema_version`；升級含遷移 |
| 真相模式 | JSON 為真，`系統/*.md` 是渲染視圖，頂端三行：這是什麼、怎麼用、由程式產生改了會被覆蓋（D-067）；格式住程式，範例在 `tests/fixtures/` |
| 測試 | `unittest`，`tests/test_<模組>.py`，`python -m unittest discover tests`，全綠才算改完 |
| 預算 | 常駐字數、`rules/core.md` 行數、每個 `SKILL.md` 行數各有上限，`tests/test_budget.py` 守。初始值見 D-070 |
| 權限 | repo `.claude/settings.json` 放行測試與 `kb.py`；客戶端高風險子命令放 `permissions.ask` |

## 五、哪個步驟用哪個 skill

| 步驟 | 用 |
|---|---|
| 討論與決策 | `superpowers:brainstorming`；定目錄時 `plugin-dev:plugin-structure` |
| 寫 spec | 本流程的範本。不用 `superpowers:writing-plans`，兩份會漂 |
| 實作 | `superpowers:test-driven-development`、`superpowers:verification-before-completion`；碰到什麼用對應的 `plugin-dev:hook-development`／`skill-development`／`command-development`／`agent-development`；測試紅了 `superpowers:systematic-debugging` |
| 實作收尾 | 動過 manifest、目錄、hooks.json 跑 agent `plugin-dev:plugin-validator`；動過 SKILL.md 跑 agent `plugin-dev:skill-reviewer` |
| 測試 | `code-review` 等級 high；碰守門、權限、路徑時加 `security-review`；改了 skill 描述時用 `anthropic-skills:skill-creator` 的 eval 跑 `tests/evals/routing.yaml`，在同時裝著舊 `llm-wiki` 的機器上跑 |
| 確認、發行 | 無，照流程手動 |

不用：`cowork-plugin-management:*`、`plugin-authoring`、`plugin-dev:mcp-integration`、`superpowers:subagent-driven-development`、`executing-plans`、`finishing-a-development-branch`。

## 六、測試資料

位置 `llm-wiki-test\來源\`，全假，程式生成，腳本放 `llm-wiki-test\生成\`，生完打包 `來源.zip`。假客戶沿用接案流程演練的「林小姐，接案室內設計師」。

```
來源\
├─ 客戶案件\  信義王（平面圖說明、會議紀錄×3、進度.xlsx、圖片型 pdf）、大安陳（報價 v1、v2）、內湖李（兩份）
├─ 報價與合約\  信義王（報價 v1.pdf、v2.pdf、合約.docx）、大安陳（報價.xlsx、合約.docx）、範本\
├─ 廠商資料\  型錄×3.pdf（一份掃描型）、廠商聯絡表.xlsx（統編、電話）
└─ 公司行政\  薪資\（假身分證與帳號，要被排除）、請款\×4.docx、舊檔\（舊合約.doc、Big5 備忘.txt）
```

刻意放的情況：同一份報價兩版、完全相同的檔在兩處、掃描型 PDF、圖片型 PDF、`.doc`、Big5、薪資夾含假個資、統編電話、兩份合約付款條件矛盾、一份生成後搬到別夾、一份密碼 PDF。約 40 到 50 份。
