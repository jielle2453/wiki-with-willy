# wiki-with-willy — 給改 repo 的 Claude

日期：2026-10-13
給誰看：改這個 repo 的 Claude 與人。怎麼用 plugin 看 README.md，設計與決策看 design/。
什麼時候更新：目錄或流程變了的那個 commit 一起改。

## 一、原始碼地圖

本清單會漂，以 `git ls-files` 為準。「尚未」是 design/architecture.md 第九節計畫中、這版還沒有的目錄。

| 路徑 | 用途 |
|---|---|
| `.claude-plugin/plugin.json` | plugin manifest：名稱、版號、描述 |
| `.claude/settings.json` | repo 內的權限：只放行測試與 `kb.py` |
| `README.md` `CHANGELOG.md` `LICENSE` | 給使用者的說明、一版一段的變更、MIT |
| `.gitattributes` `.gitignore` | 行尾一律 LF；不進 repo 的東西 |
| `config/defaults.json` | 門檻與預設數字，每個鍵旁一個 `_why_<鍵>`；`schema_version`、`big_change_synthesis_max`、`source_spec_max_up`、`credential_scan_max_entries`、`prompt_head_max`（問的長度）、`journal_result_max`（日誌結果字數）、`fingerprint_max_bytes`（單檔算指紋上限）、`changes_quick_hash_budget_bytes`、`changes_quick_seconds`（開場快速比對的讀取與時間預算） |
| `scripts/kb.py` | 唯一 CLI 入口（D-013）。不動 `sys.path` |
| `scripts/kb/cli.py` | argparse 主程式；子命令註冊表 `COMMANDS` |
| `scripts/kb/version.py` | 子命令 `version` |
| `scripts/kb/guard.py` | 守門判斷：`find_vault`、`classify`、`decide`，決策表 `ZONES`（Z1／Z3／Z5 多一欄 `shell_write`）；憑證三道在區之前、關不掉（D-079 補）：`credential_hit`（段）、`_token_filename_hit`（裸 token）、`folder_scan`（資料夾範圍走訪，材料由 `_analyze` 按子指令與管線段算）；生效範圍照 D-081（總資料夾內部豁免）。純函式，hook 與 CLI 共用 |
| `scripts/kb/guard_cli.py` | 子命令 `guard decide`：模擬一次判斷，查「為什麼擋」，不留痕、不備份 |
| `scripts/kb/versions.py` | 子命令 `versions list`／`revert`／`delete-outputs`：列回合、退回最近一次、刪已退回回合留著的成品（D-074、D-011）；成品只算清冊 `origin` 是 `ai_output` 的 |
| `scripts/kb/inventory_cli.py` | 子命令 `inventory scan`：全掃來源與總資料夾，把變動套進清冊（寫前備進回合、終端機記程式段）；`--source` 只掃一個來源 |
| `scripts/kb/changes.py` | 子命令 `changes`：跟清冊上次掃描比，列新增、修改、搬動、不見、之前就不見；只讀不寫、不開回合；`--quick` 走開場那條路 |
| `scripts/kb/turn_cli.py` | 子命令 `turn current`／`approve`／`note`／`redact`：看目前回合、解除綜合卡張數上限（D-075）、給這一句的「做了」（D-084）、換掉這一句的「問」（D-086） |
| `scripts/kb/journal_cli.py` | 子命令 `journal recent`／`render`：印最近幾段工作日誌、從 jsonl 重寫月檔；`record_program_or_warn` 給 init、folders、versions 寫程式段（D-085，對話裡叫的不寫） |
| `scripts/kb/init.py` | 子命令 `init <路徑>`：從 `skeleton/` 建五區骨架與 `.system/sources.json`，缺的才建、既有檔不碰；`--check` 只印計畫 |
| `scripts/kb/folders.py` | 子命令 `folders add`／`exclude`／`remove`／`include`／`list`：登記來源（拒絕條件、數份數、憑證清單、spec 壓縮）、排除夾或檔、拿掉來源（含它的排除）、取消排除（只比字串）、列登記表；改 `sources.json` 前先備進回合；同一把鎖裡接著改清冊（D-095：add／include 用 `inventory.scan` 加 `apply`，exclude／remove 用 `inventory.reconcile`），第一行份數與憑證清單從同一個 scan 取 |
| `scripts/kb/_lib/paths.py` | `plugin_root()`、`read_manifest()`：從 `__file__` 往上找，不寫死路徑；`normalize`、`inside`、`resolve_spec`、`compress_spec`（D-021 補）、`is_abs_spec`、`long_path`（Windows `\\?\` 前綴，I-017） |
| `scripts/kb/_lib/io.py` | UTF-8、LF、原子寫入的讀寫函式；`os.replace` 被拒重試（I-018）；`FileLock` 鎖檔（hook 等 3 秒、CLI 30 秒、殘留 30 秒；鎖檔建不起來直接丟錯） |
| `scripts/kb/_lib/config.py` | `get(vault, key)`：`defaults.json` 為底，客戶端 `.system/config.json` 同鍵覆寫 |
| `scripts/kb/_lib/turn.py` | 回合：`start`（`prompt_head` 先剝 `<pasted_content>` 標籤、再壓空白、再 `mask_secrets`、再截 `prompt_head_max`；回合檔帶 `note`、`done`；`inherit=` 給對話裡退回自己這句時接著原回合，記 `journal_turn`）、`journal_key`（日誌那行的 key：`journal_turn` 或自己）、`set_note`／`set_done`／`set_prompt_head`、`current`、`approve`、`rel_path`（原始大小寫）、`backup`、`before_write`（大改動門檻加備份） |
| `scripts/kb/_lib/journal.py` | 工作日誌（D-082～D-086）：真相 `.system/journal/YYYY-MM.jsonl`，`begin_turn`（中斷補記加開口，同月一把鎖）、`complete_turn`（同一行 upsert）、`finish_fields`（做了：AI 優先、地板句；改了取 `journal_turn` 與目前回合兩份 manifest 的聯集）、`update_turn`、`record_program`（代號一律現在時間）、`floor_note`、`cut_result`（全文一行有遮到就不抽段；否則抽結論段、壓一行、截）、`recent`、`render`（整月寫 `系統/工作日誌/YYYY-MM.md`，最新在上）；鎖住讀改寫，渲染失敗只記 log，壞行跳過留著 |
| `scripts/kb/_lib/inventory.py` | 清冊（schema 2，D-093～D-096）：`.system/inventory.json` 一筆一份內容（位置清單、指紋、read、status）；`load` 遷移 1→2、形狀不對丟 `ValueError`；`scan`（純函式：走訪、比出新增、修改、搬動、重複、不見；`quick` 給開場）、`apply`（鎖裡備份再寫）、`reconcile`（排除、拿掉來源）、`path_of`；`register_output`、`outputs_of_turn`、`set_status` |
| `scripts/kb/_lib/content_fp.py` | OOXML 內容指紋（D-096）：docx、xlsx、pptx 抽文字算 sha256，認 Office 另存的假變動；標準函式庫 |
| `scripts/kb/_lib/env.py` | `.system/env.json` 按機器記「找不到的來源已報過」：`missing_reported`、`mark_missing`、`clear_missing`；壞檔當空的重建 |
| `scripts/kb/_lib/cmdparse.py` | 指令字串解析：`extract_paths`（`.`、`..` 也算路徑）、`is_write`、`is_single_plugin_call` |
| `scripts/kb/_lib/sources.py` | 讀 `.system/sources.json`，`lookup` 查登記／排除；`load_raw` 給 `kb folders` 讀原始 dict（格式不對丟 `ValueError`），`new_id`、`RESERVED_ALIASES`；`unresolved` 每項 `{alias, spec, id, kind}`（kind 是 source｜exclude） |
| `scripts/kb/_lib/credentials.py` | 憑證檔名樣式 `CREDENTIAL_PATTERNS`（寫死、不讀 config）、`match_segment`、`looks_like_filename`、`scan_dir`（守門走訪、有上限、跳過排除項目與總資料夾）、`scan_all`（`kb folders add` 數檔與收憑證清單）；`SECRET_WORDS`、`mask_secrets`（回合 `prompt_head` 遮值，D-081、B-027） |
| `scripts/kb/_lib/ledger.py` | 留痕：`ledger.tsv` 事件帳、`guard-status` 每日一次、`hook-errors.log` |
| `hooks/hooks.json` | 五個 hook 的註冊：SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop |
| `hooks/session.py` | SessionStart（startup／resume／clear／compact）：`additionalContext` 先印狀態行（`status_lines`，第一行「【wiki-with-willy】kb 指令」前綴、變動行與找不到來源行共用一次快速 `inventory.scan`、找不到的來源每台電腦報一次（`env.json`），摺疊後加一行，其餘四項回 `None` 等各自的題，D-060；每行各自 try，出錯那行不印、寫 log），空一行再接 `rules/core.md` 全文（D-087）；`ensure_ascii=True` |
| `hooks/guard.py` | PreToolUse 守門入口：讀 stdin、呼叫 `kb.guard.decide`，寫 Z1／Z3／Z5 放行前先 `turn.before_write` 備份，印 allow／deny 或不印，永遠 exit 0 |
| `hooks/turn.py` | UserPromptSubmit：每句話開一個回合，寫 `.system/turns/<sid>.json` 與 `current.json`；先補記上一句的中斷，再寫這一句的日誌開口 |
| `hooks/output_log.py` | PostToolUse：檔案工具寫進 AI 產出後登記到 `inventory.json` |
| `hooks/stop.py` | Stop：AI 答完把這回合的日誌那行補完（結果取 `last_assistant_message` 壓一行遮值再截、做了、改了），只收第一次 |
| `tests/` | `unittest`；`test_budget.py` 守常駐預算（D-070）、`test_version_sync.py` 守版號對齊、`test_repo_hygiene.py` 守編碼、行尾與 `sys.path` |
| `tests/evals/` | skill 路由的觸發句（D-091）：`<skill>.json` 20 句、該觸發與不該各半，測試 session 用 skill-creator 的 `run_eval` 跑；`test_skill_manage.py` 守形狀 |
| `tests/fixtures/` | 測試用範例檔；`vault/` 與 `raw/` 是守門的假總資料夾與來源，測試時整包複製到 tmp；`journal/` 是渲染的範例輸入與期望輸出（D-067）；`inventory/` 是清冊的假總資料夾、三個來源與一份 schema 1 清冊 |
| `design/` | 流程、約定、架構、決策、spec。實作 session 只能在 `backlog.md` 新增一行 |
| `rules/core.md` | 常駐核心規則 R-01～R-14（D-059），開場注入整份帶入；`test_budget` 量、`test_rules_core` 守條目 |
| `skills/manage/` | 第一個 skill（D-089、D-090）：`SKILL.md` 只做分派，加資料夾、不讀某夾、拿掉來源、取消排除、列出，一律經 `kb folders`；`references/sources.md` 是子子命令的參數、輸出與拒絕原因怎麼說。其餘五個 skill 尚未 |
| `commands/` | 尚未。build 五步與 upgrade、backup、restore、relocate、migrate、account（D-028），沒有 manage 指令 |
| `agents/` | 尚未 |
| `templates/` | 尚未。卡與報告範本 |
| `skeleton/` | 客戶端總資料夾骨架：`CLAUDE.md`（身分與五區、沒有開場段就不改檔的哨兵句，D-080、D-088，`test_budget` 量）、`.claude/settings.json`（空殼 deny 清單）、四區的 `.gitkeep`（只為 git 留空夾，`kb init` 不複製） |
| `scripts/kb/sources/` `sinks/` `lint/` | 尚未。其餘子命令與 `_lib` 模組 |

## 二、改完要做的事

1. 跑 `python -m unittest discover tests`，全綠才算改完。
2. 動過 `.claude-plugin/plugin.json` 或 `hooks/` 要跑 plugin 驗證（agent `plugin-dev:plugin-validator`）。
3. bump 版號的那個 commit 要同時寫 `CHANGELOG.md`，不事後補。

## 三、不准

- 不寫 plugin 的絕對路徑進任何狀態檔或設定（D-019）；路徑一律從 `__file__` 或 `${CLAUDE_PLUGIN_ROOT}` 推。
- 核心只用標準函式庫。
- `hooks/*.py` 以外不動 `sys.path`（hook 不是從 `scripts/` 跑的，那是唯一例外；`test_repo_hygiene.py` 守著）。
- 不 push、不 bump，除非設計 session 說發行。
