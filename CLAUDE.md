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
| `config/defaults.json` | 門檻與預設數字，每個鍵旁一個 `_why_<鍵>`；`schema_version`、`big_change_synthesis_max`、`source_spec_max_up`、`credential_scan_max_entries`、`prompt_head_max`（問的長度）、`journal_result_max`（日誌結果字數）、`fingerprint_max_bytes`（單檔算指紋上限）、`changes_quick_hash_budget_bytes`、`changes_quick_seconds`（開場快速比對的讀取與時間預算）、`ingest_auto_max`（開場自動製卡上限）、`pdf_read_pages_max`、`pdf_page_min_chars`、`pdf_render_scale`、`card_source_big_chars`、`card_title_max_chars`、`temp_keep_hours`（製卡）、`synthesis_min_cards`（待建綜合卡門檻） |
| `scripts/kb.py` | 唯一 CLI 入口（D-013）。不動 `sys.path` |
| `scripts/kb/cli.py` | argparse 主程式；子命令註冊表 `COMMANDS` |
| `scripts/kb/version.py` | 子命令 `version` |
| `scripts/kb/guard.py` | 守門判斷：`find_vault`、`classify`、`decide`，決策表 `ZONES`（Z1／Z3／Z5 多一欄 `shell_write`）；憑證三道在區之前、關不掉（D-079 補）：`credential_hit`（段）、`_token_filename_hit`（裸 token）、`folder_scan`（資料夾範圍走訪，材料由 `_analyze` 按子指令與管線段算）；生效範圍照 D-081（總資料夾內部豁免）。純函式，hook 與 CLI 共用 |
| `scripts/kb/guard_cli.py` | 子命令 `guard decide`：模擬一次判斷，查「為什麼擋」，不留痕、不備份 |
| `scripts/kb/versions.py` | 子命令 `versions list`／`revert`／`delete-outputs`：列回合、退回最近一次、刪已退回回合留著的成品（D-074、D-011）；成品只算清冊 `origin` 是 `ai_output` 的；清冊讀不到時 list 不印成品數、revert 照 manifest 還原、跳過成品對帳；退的回合有 `profile.json` 時多印「建的資料夾還在」一行 |
| `scripts/kb/inventory_cli.py` | 子命令 `inventory scan`：全掃來源與總資料夾，把變動套進清冊（寫前備進回合、終端機記程式段，有動到待處理時 changed 多兩檔）；`--source` 只掃一個來源 |
| `scripts/kb/convert.py` | 子命令 `convert <目標>`：不收 `--vault`、總資料夾只從 cwd 找；編號、uri 或路徑 → 抽字寫 `.system/temp/`（寫前清超過 `temp_keep_hours` 的一般檔，temp 是連結不清；檔名只用驗過的編號），PDF 經 pypdfium2 抽文字層、掃描頁轉 PNG，圖片指給 Read；抽字前照守門同一套判斷（排除、憑證名、Z11、Z1／Z6／Z7／Z8 拒）；讀不到 exit 3，條目在清冊就記 unreadable 待處理；終端機不印內容 |
| `scripts/kb/cards.py` | 子命令 `cards todo`／`sync`／`drop`／`index`／`skip`：列該做的卡（不寫檔；第一行接待掛實體、綜合卡、實體夾有變）、卡跟著原檔搬與收斂同編號多張、原檔已刪拿掉卡、重寫 `總目錄.md`、`skip <編號> --kind` 記成依設定不整理（D-113）；動到卡後重渲染相關綜合卡快取；改檔前備進回合、終端機記程式段 |
| `scripts/kb/entity.py` | 子命令 `entity add`／`confirm`／`rename`／`merge`／`remove`／`rebuild`／`clean`／`list`／`render`（D-115～D-117、D-057）：實體只經這裡寫；鎖 `entities.json` 30 秒、寫前備份；改到檔案卡重寫 `總目錄.md`、最後重渲染受影響的綜合卡快取；rename 搬夾帶到客戶原檔時鎖外 `inventory.scan` 加 `apply`；終端機記程式段 |
| `scripts/kb/profile_cli.py` | 子命令 `profile check`／`apply`：設定檔 md 對 JSON 的差異與型別夾對帳（只讀）；套用：刪有東西的型別那列擋、兩檔備進回合後寫、先建缺的型別夾、表少了的型別夾是空的一起拿掉、拿掉的不整理種類把清冊 `skip_kind` 清掉、第五節關於你對實體清單（`entities.sync_self`）；鎖 `profile.json`、終端機記程式段 |
| `scripts/kb/pending_cli.py` | 子命令 `pending list`／`resolve`／`dismiss`：看與處理待處理 |
| `scripts/kb/changes.py` | 子命令 `changes`：跟清冊上次掃描比，列新增、修改、搬動、不見、之前就不見；只讀不寫、不開回合；`--quick` 走開場那條路 |
| `scripts/kb/turn_cli.py` | 子命令 `turn current`／`approve`／`note`／`redact`：看目前回合、解除綜合卡張數上限（D-075）、給這一句的「做了」（D-084）、換掉這一句的「問」（D-086） |
| `scripts/kb/journal_cli.py` | 子命令 `journal recent`／`render`：印最近幾段工作日誌、從 jsonl 重寫月檔；`record_program_or_warn` 給 init、folders、versions 寫程式段（D-085，對話裡叫的不寫） |
| `scripts/kb/init.py` | 子命令 `init <路徑>`：從 `skeleton/` 建五區骨架與 `.system/sources.json`，加 `.system/profile.json`（預設值）與 `系統/客戶設定檔.md`（渲染）；缺的才建、既有檔不碰（只缺 md 從 JSON 渲染回來、只缺 JSON 寫預設不動 md）；`profile.json` 是 schema 1 而 md 沒改過就升 2、重渲染 md，改過就請先 apply；`--check` 只印計畫 |
| `scripts/kb/folders.py` | 子命令 `folders add`／`exclude`／`remove`／`include`／`list`：登記來源（拒絕條件、數份數、憑證清單、spec 壓縮）、排除夾或檔、拿掉來源（含它的排除）、取消排除（只比字串）、列登記表；改 `sources.json` 前先備進回合；同一把鎖裡接著改清冊（D-095：add／include 用 `inventory.scan` 加 `apply`，exclude／remove 用 `inventory.reconcile`），第一行份數與憑證清單從同一個 scan 取；清冊這步失敗就把 `sources.json` 寫回原文 |
| `scripts/kb/_lib/paths.py` | `plugin_root()`、`read_manifest()`：從 `__file__` 往上找，不寫死路徑；`normalize`、`inside`、`resolve_spec`、`compress_spec`（D-021 補）、`is_abs_spec`、`long_path`（Windows `\\?\` 前綴，I-017） |
| `scripts/kb/_lib/io.py` | UTF-8、LF、原子寫入的讀寫函式；`os.replace` 被拒重試（I-018）；`FileLock` 鎖檔（hook 等 3 秒、CLI 30 秒、殘留 30 秒；鎖檔建不起來直接丟錯） |
| `scripts/kb/_lib/config.py` | `get(vault, key)`：`defaults.json` 為底，客戶端 `.system/config.json` 同鍵覆寫 |
| `scripts/kb/_lib/turn.py` | 回合：`start`（`prompt_head` 先剝 `<pasted_content>` 標籤、再壓空白、再 `mask_secrets`、再截 `prompt_head_max`；回合檔帶 `note`、`done`；`inherit=` 給對話裡退回自己這句時接著原回合，記 `journal_turn`）、`journal_key`（日誌那行的 key：`journal_turn` 或自己）、`set_note`／`set_done`／`set_prompt_head`、`current`、`approve`、`rel_path`（原始大小寫）、`backup`（`by="program"` 記程式自己改的）、`modified_synthesis_count`（數回合檔的 `ai_synthesis`）、`before_write`（Z1 既有檔記進 `ai_synthesis`、大改動門檻加備份） |
| `scripts/kb/_lib/journal.py` | 工作日誌（D-082～D-086）：真相 `.system/journal/YYYY-MM.jsonl`，`begin_turn`（中斷補記加開口，同月一把鎖）、`complete_turn`（同一行 upsert）、`finish_fields`（做了：AI 優先、地板句；改了取 `journal_turn` 與目前回合兩份 manifest 的聯集）、`update_turn`、`record_program`（代號一律現在時間）、`floor_note`、`cut_result`（全文一行有遮到就不抽段；否則抽結論段、壓一行、截）、`recent`、`render`（整月寫 `系統/工作日誌/YYYY-MM.md`，最新在上）；鎖住讀改寫，渲染失敗只記 log，壞行跳過留著 |
| `scripts/kb/_lib/inventory.py` | 清冊（schema 2，D-093～D-096）：`apply` 寫完接待處理（`sync_missing`、modified 關 unreadable，失敗只記 log）並回有沒有改到；`uri_of`（路徑→uri）、`entry_for`（uri→條目）；`.system/inventory.json` 一筆一份內容（位置清單、指紋、read、status）；`load` 遷移 1→2、形狀不對丟 `ValueError`；`scan`（純函式：走訪、比出新增、修改、搬動、重複、不見；`quick` 給開場）、`apply`（鎖裡備份再寫）、`reconcile`（unused 排除、拿掉來源標 excluded；hidden 排除直接拿掉、不留檔名）、`path_of`；快速模式每算一塊指紋前查時間；`register_output`、`outputs_of_turn`、`set_status`；條目選填欄 `skip_kind`（掃描保留），`set_skip`、`unskip_kinds`；`_is_card` 跳過型別夾第三層檔名等於夾名、或檔頭編號 `e-` 開頭的 md |
| `scripts/kb/_lib/content_fp.py` | OOXML 內容指紋（D-096）：docx、xlsx、pptx 抽文字算 sha256，認 Office 另存的假變動；`iterparse` 邊讀邊丟、可喊停，抽不出來一律回 None；共用抽字住 `extract`；標準函式庫 |
| `scripts/kb/_lib/extract.py` | 抽字（D-099）：`docx_text`、`xlsx_text`（每行「第 N 列：」）、`pptx_text`、`plain_text`（不猜編碼、解不了丟 `Unreadable`）、`probe`（格式、舊格式、加密）、`kind_by_name`（只看副檔名）、`pdf_pages`、`pdf_pages_text` 與 `render_pages`（選配 pypdfium2，PNG 用 zlib 自己寫，裁定 36）；抽字有 `max_bytes` 上限；`content_fp` 共用的 `_events`、`_paragraphs`、`_shared_strings`、`_cell_value`、`_number` |
| `scripts/kb/_lib/cards.py` | 檔案卡（D-101、D-104、D-105、D-116）：範本第二版（`實體`、`待補型別`）；`parse_front`、`KEYS`、`ORIGIN_TEXT`、`card_dir`、`open_link`、`safe_title`、`filename_ok`、`header_for`（實體夾預填 `實體`）、`scan_cards`、`validate`（型別先驗、任何例外都擋，只報第一個錯；第一版代號 `v1`、實體名稱要登記過、待補型別不能在型別表）、`todo`（分桶含 duplicates、`unlinked`、`missing_types`、綜合卡四桶、`entities`、`skipped.skipped`、帶設定檔的 `profile` 塊、最後併進 `plan`、`asks`、`tell` 三鍵、不寫檔、kind 只看副檔名）、`skip`（拒絕丟 `SkipRefused`）、`remove_card`、`rewrite_locations`、`rewrite_entities`、`refresh_synthesis`、`render_index`（總目錄，有 turn_id 先備份） |
| `scripts/kb/_lib/entities.py` | 實體清單（D-114～D-124）：`.system/entities.json` schema 1；`load`、`key`（NFKC casefold）、`find`（id、名稱、別名，merged 轉址）、`name_problem`、`refusal`、`add`（冪等，拒絕丟 `EntityRefused`）、`confirm`、`set_card`、`mounts`（檔案卡 `實體` 欄）、`drift`（只讀：夾改名、卡改名、搬型別、不見）、`locate`、`head_id`、`synthesis_files`、`retarget_cards`、`rename`、`merge`、`remove`（先驗後做，中途出錯照 `_Undo` 還原）、`rebuild`、`clean`（先正規化路徑）、`sync_self`（關於你）；壞檔丟 `Unreadable` 帶檔名；寫入都在鎖裡、寫前備份 |
| `scripts/kb/_lib/plan.py` | 整理 ingest 的介面（D-132、D-134、D-135）：`build(out, vault, config)` 吃 `cards.todo` 的結果算 `plan`（照順序要做的事，多量先問時只留 sync）、`asks`（排好序、從 1 連號的問題與結果清單，結果帶 `do` 指令、`strict`、`fill`，指令一律 `kb ` 開頭、有空白或特殊字元自己加引號）、`tell`（回答末尾的現成句子）；只讀（取 `ingest_auto_max`、依設定不整理的種類讀清冊），不寫檔 |
| `scripts/kb/_lib/synthesis.py` | 綜合卡（D-038、D-114、D-118、D-121）：`KEYS`、`card_rel`、`header_for`、`replace_front`、`replace_title`、`validate`（只報第一個錯，任何例外都擋）、`render_cache`（只重寫 `檔案卡`、`相關實體`，備份 by program）、`buckets`（`synthesis_new`／`stale`／`broken`、`entity_drift`）、`sections_for` |
| `scripts/kb/_lib/profile.py` | 客戶設定檔（D-109～D-113、D-119、D-120）：`.system/profile.json` schema 2 與渲染 `系統/客戶設定檔.md`（提示句住這裡，D-067）；`defaults`、`load`（schema 1 在記憶體遷移）、`stored_schema`、`parse_md`（五節表格、錯了丟 `ProfileError` 帶節與列，缺節是錯；`legacy` 時缺四、五節不算錯）、`diff`、`merge`（來源日期等於舊值當沒填；新型別補 `CATEGORY_SECTIONS` 預設）、`default_sections`、`folder_check`（型別表對第一層夾）、`render`、`write`（兩檔備進回合、`md_sha256` 判有改）、`status`（開場與 check 共用，只讀）、`card_types_for_todo` |
| `scripts/kb/_lib/pending.py` | 待處理（D-102）：`.system/pending.json` schema 1 與渲染 `系統/待處理.md`；`add`（去重）、`resolve`、`dismiss`、`auto_done`、`sync_missing`；寫前兩檔都備進回合 |
| `scripts/kb/_lib/env.py` | `.system/env.json` 按機器記「找不到的來源已報過」：`missing_reported`、`mark_missing`、`clear_missing`；壞檔當空的重建 |
| `scripts/kb/_lib/cmdparse.py` | 指令字串解析：`extract_paths`（`.`、`..` 也算路徑）、`is_write`、`is_single_plugin_call` |
| `scripts/kb/_lib/sources.py` | 讀 `.system/sources.json`，`lookup` 查登記／排除；`load_raw` 給 `kb folders` 讀原始 dict（格式不對丟 `ValueError`），`new_id`、`RESERVED_ALIASES`；`unresolved` 每項 `{alias, spec, id, kind}`（kind 是 source｜exclude） |
| `scripts/kb/_lib/credentials.py` | 憑證檔名樣式 `CREDENTIAL_PATTERNS`（寫死、不讀 config）、`match_segment`、`looks_like_filename`、`scan_dir`（守門走訪、有上限、跳過排除項目與總資料夾）、`scan_all`（`kb folders add` 數檔與收憑證清單）；`SECRET_WORDS`、`mask_secrets`（回合 `prompt_head` 遮值，D-081、B-027） |
| `scripts/kb/_lib/ledger.py` | 留痕：`ledger.tsv` 事件帳、`guard-status` 每日一次、`hook-errors.log` |
| `hooks/hooks.json` | 五個 hook 的註冊：SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop |
| `hooks/session.py` | SessionStart（startup／resume／clear／compact）：`additionalContext` 先印狀態行（`status_lines`，第一行「【wiki-with-willy】kb 指令」前綴、變動行與找不到來源行共用一次快速 `inventory.scan`、變動行接「實體夾有變 N」（`entities.drift`，只讀，沒變動時自成一行）、找不到的來源每台電腦報一次（`env.json`），變動行（含還沒比完）印出時緊接著一行整理指示（`ingest_auto_max`），找不到來源之後一行設定檔有改／讀不懂／第一層夾不在型別表（`profile.status`，只讀），摺疊後加一行，其餘四項回 `None` 等各自的題，D-060；每行各自 try，出錯那行不印、寫 log），空一行再接 `rules/core.md` 全文（D-087）；`ensure_ascii=True` |
| `hooks/guard.py` | PreToolUse 守門入口：讀 stdin、呼叫 `kb.guard.decide`，寫 Z1／Z3／Z5 放行前先 `turn.before_write` 備份，印 allow／deny 或不印，永遠 exit 0 |
| `hooks/turn.py` | UserPromptSubmit：每句話開一個回合，寫 `.system/turns/<sid>.json` 與 `current.json`；先補記上一句的中斷，再寫這一句的日誌開口 |
| `hooks/output_log.py` | PostToolUse：檔案工具寫進 AI 產出後登記到 `inventory.json`；寫進 `檔案索引/` 的 `.md` 卡 `cards.validate`，不過印 decision block（exit 仍 0），過了重寫 `總目錄.md` 與新舊 `實體` 的綜合卡快取；寫進 `<型別>/<名稱>/<名稱>.md` 的綜合卡 `synthesis.validate`，不過 block，過了 `card` 記 true、重渲染快取 |
| `hooks/stop.py` | Stop：AI 答完把這回合的日誌那行補完（結果取 `last_assistant_message` 壓一行遮值再截、做了、改了），只收第一次 |
| `tests/` | `unittest`；`test_budget.py` 守常駐預算（D-070）、`test_version_sync.py` 守版號對齊、`test_repo_hygiene.py` 守編碼、行尾與 `sys.path` |
| `tests/evals/` | skill 路由的觸發句（D-091）：`<skill>.json` 20 句、該觸發與不該各半，測試 session 用 skill-creator 的 `run_eval` 跑；`test_skill_manage.py`、`test_skill_ingest.py` 守形狀 |
| `tests/fixtures/` | 測試用範例檔；`cards/` 是兩張合法卡、一張檔頭壞的、`pending.json` 與期望的 `待處理.md`；`vault/` 與 `raw/` 是守門的假總資料夾與來源，測試時整包複製到 tmp；`journal/` 是渲染的範例輸入與期望輸出（D-067）；`inventory/` 是清冊的假總資料夾、三個來源與一份 schema 1 清冊；兩個假總資料夾都帶設定檔兩份（schema 2，型別表一列「案子」）；`profile/` 是 `kb init` 剛建完的設定檔 md 與 JSON（期望輸出）、一份改過的 `改過.md`、`schema1.json` 與它渲染的舊 md；`entities/` 是實體清單、兩張合法綜合卡、一張範本第一版的檔案卡 |
| `design/` | 流程、約定、架構、決策、spec。實作 session 只能在 `backlog.md` 新增一行 |
| `rules/core.md` | 常駐核心規則 R-01～R-14（D-059），開場注入整份帶入；`test_budget` 量、`test_rules_core` 守條目 |
| `rules/synthesis.md` | 綜合卡怎麼寫 S-01～S-29（寫給製卡者，D-053）；不常駐，ingest 寫綜合卡前讀 |
| `skills/manage/` | 第一個 skill（D-089、D-090）：`SKILL.md` 只做分派，加資料夾、不讀某夾、拿掉來源、取消排除、列出，一律經 `kb folders`；`references/sources.md` 是子子命令的參數、輸出與拒絕原因怎麼說；加完資料夾問要不要整理成卡，交給 ingest |
| `skills/ingest/` | 第二個 skill（D-098～D-104）：整理成卡，`SKILL.md` 本文只放分辨、原則（各帶理由與編號）與骨架（D-132，指令與答法對照由 `cards todo --json` 的 `plan`、`asks`、`tell` 給）；`references/carding.md` 是製卡規則（寫給製卡者，一～八節）、`references/commands.md` 是 convert、cards、pending、profile、entity 的參數、輸出、拒絕原因與 `plan`／`asks`／`tell` 的欄位。其餘四個 skill 尚未 |
| `commands/` | 尚未。build 五步與 upgrade、backup、restore、relocate、migrate、account（D-028），沒有 manage 指令 |
| `agents/` | 尚未 |
| `templates/` | `card.md` 檔案卡範本（檔頭中文鍵名，D-101，第二版）、`synthesis.md` 綜合卡範本；報告範本尚未 |
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
