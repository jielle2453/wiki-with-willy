# card-maker 與整批製卡：測試報告

日期：2026-10-23
測的版本：`spec/card-maker` 的 60b909b（worktree `C:\Users\Jie\Desktop\llm-wiki-j-cardmaker`，跑前 `git status --short` 空）
spec：`design/specs/2026-10-22-card-maker.md`（驗收準則 1～16）
測試者：card-maker-測試（只驗證，沒改任何程式；唯一新增的檔是這份報告）

## 結論

- **驗收準則 19 條（1、2、2b、3～12、13、13b、13c、14～16）：19 條都過。** 其中 2b 是字面過，但同一條規矩有一條沒被守住的洩漏路徑（重大發現 S1）。
- 全部測試 `python -m unittest discover tests`：exit 0，Ran 2191，OK（skipped=1），487 秒。
- B-412：十次（「你好」5、300 字指示 5）**十次都先整理**，`cards dispatch` 都跑了、card-maker 都被派出去並做出 17 張卡；沒有權限拒絕、沒有 hook-errors.log。
- 但實跑與讀碼抓到 **5 個要修的問題**（見「重大發現」）：S1 密碼值可進工作日誌、S2 做不成的卡被無限重切（真跑已遇到）、S3 子代理能跑任何 kb 子命令、S4 守門插的 `--turn` 可被覆蓋、S5 `cards unreadable --reason` 不遮值。
- 另：十次裡主對話有 3 次對客戶可見的文字出現英文／步驟編號（B-064、R-10）。

## 環境與紀錄

| 項目 | 內容 |
|---|---|
| 方法 | 驗收逐條自己跑（自寫 driver 建 tmp 知識庫，用 stdin 餵 hook、真跑 CLI）；code-review（high）與 security-review 各派一個唯讀子代理只讀碼列疑點，**有重現價值的疑點我自己用程式跑**，沒有叫子代理組攻擊輸入 |
| 開 Claude | 只用 `claude -p --plugin-dir <worktree>`（B-412）與 skill-creator 的 `run_eval`；沒裝 plugin、沒動使用者範圍；22 轉述 b2 沒有在跑 spike，所以沒先問它 |
| `~\.claude\settings.json` sha256 | 跑前 `ac9c560c49fa969de683a36afbc0be98440c5adcbe224a1332350f1662657a1c`；**跑後（run_eval 結束後再量一次）`8ae82f7a69eea7b8321459b4a81b9d734c934bcbe188794b63816af6e2cf4039`（有變）**。檔案修改時間 16:07:48，落在 B-412 長指示第 2 次執行期間。內容差異：`enabledPlugins` 完全沒動（`llm-wiki@llm-wiki` 仍是 false）；現在的檔有 `model: opus`、`modelSettings`（兩個模型 effort high）、`tui`、`theme`、`agentPushNotifEnabled` 等，我沒有跑前的內容可以逐行比，所以**無法指出哪一行是新的**。對照實驗：空資料夾跑一次 `claude -p "只回 ok" --plugin-dir <worktree>`，前後 sha256 與 mtime 完全不變，所以不是 `claude -p` 或這個 plugin 寫的；推測是同一台電腦上其他視窗（使用者或別的 session）改了設定。這個變動跟本題無關，但依規定照實寫出。 |
| 舊 `llm-wiki` | 使用者範圍 `llm-wiki@llm-wiki`：settings.json `enabledPlugins` 為 false（`claude plugin list` 仍列出，是已安裝但停用）；跑前與跑後都是 false |
| `claude --version` | 2.1.308 |
| 備註 | B-412 第一次啟動時我自己多開了一組 driver（兩組同時跑同一批），發現後砍掉、整批作廢重跑；**下列 B-412 數字只算重跑（run2）**。第一組已完成的幾次也都是先整理（作廢的資料只當旁證） |

## 驗收準則逐條

| # | 結果 | 證據 |
|---|---|---|
| 1 | 過 | 45 份新檔：`asks[0]` 是 `kind: batch`，句子 `有 45 份要整理，大約 5 分鐘；份數多，會用掉比平常多的額度。現在做嗎？`（45×6+30=300 秒）一字不差；「現在做」的 `then` 是 `[dispatch]`，加 5 張要搬的卡後是 `[sync, dispatch]`；`ingest_auto_max` 調高後 `plan` 是單一 `dispatch`、`read` 是 null。12 份時 `announce` = `有 12 份要整理，大約 2 分鐘；要停的話跟我說一聲`（12×6+30=102 秒→2 分鐘），11 份同樣；秒數照 config（`card_seconds_per_file` 6、`card_batch_overhead_seconds` 30）。我沒測剛好 10 份，公式與單元測試涵蓋 |
| 2 | 過 | 45 份 → `batches` 三批 `[20, 20, 5]`；`prompt` = `批次 b-fb653532：做卡 20 份。第一步跑 python "C:/Users/Jie/Desktop/llm-wiki-j-cardmaker/scripts/kb.py" cards batch b-fb653532 --json，照它做，做完照你的說明回報。` 完整前綴在；再跑一次 `batches: []`、`running: 3`；沒有回合時（終端機）跑 → exit 3，`整理要在對話裡派` |
| 2b | 過（字面）；S1 另列 | 檔名像密碼的三份（`網銀密碼.txt`、`secret_notes.txt`、`帳號密碼表.txt`）都不在 `todo.new`、不進批次；待處理記「有密碼」的條目不進批次；裁示「可以讀」的 `帳號密碼表.txt` 在 `new` 但**不進批次**，`plan` 的 `cards` 步只列它（`files: ["f-6d7c3386"]`）；`purge: "pending"` 的「大安陳」夾 2 份不進批次，`plan` 第一步是 `purge`、`dispatch` 在後；job 回合記了 `secret_shown` 時，job 回合的結果不被換（journal 仍是「交給製卡助手整理了」）。**但**回合檔的旗標是記在「給密碼的那一句」，不在派工那句：見 S1 |
| 3 | 過 | 把一批改 `done`（沒寫卡）再 dispatch → 同樣那批項目重切（只從沒卡、沒認領的切，所以回來；S2 說的重切迴圈就是這條的副作用）；`created` 改成 31 分鐘前 → 三批都當放掉、重切 `[20, 20, 5]`、`running 0`；換 `sid-B` 的 current 跑 → 另開 job、`[20, 20, 5]`，舊 session 的 open 批次不算 |
| 4 | 過 | 還有 cards 時 synthesis 不切（`left.synthesis` 7、本次只有 cards 三批）；cards 做完後切 `[5, 2]`（`synthesis_batch_size` 5）；`--source 客戶案件` 只切那個來源的項目（5 份，uri 全在 `file://客戶案件/`），job 記住 `source`，之後不帶 `--source` 仍不放寬。synthesis 的材料我用 monkeypatch 換掉 `_collect` 餵假的 `synthesis_stale`（跑真的綜合卡要先有實體與卡，單元測試涵蓋） |
| 5 | 過 | job 回合 `ai_synthesis` 9 張 + 3 張 `synthesis_stale` → 不派、`approval` = `這次要更新 12 張綜合卡，比平常多；要繼續嗎？`，`do` 是 `kb turn approve --turn <job 回合>`；客戶中途開新回合後跑 `turn approve --turn <job 回合>` → `這一題解除綜合卡張數上限`，再 dispatch → 派 `synthesis 3`；只有 job 回合 `approved` 變 true，新回合沒有 |
| 6 | 過 | `cards batch --json` 的鍵恰是 `batch、kind、prefix、read、items、profile、entities`；`items` 跟 `todo.new` 同一組欄位加 `type`；`read` 指 `skills/ingest/references/carding.md 批次、材料的欄位、一～七、九節`；`--agent X` 記上 `agent_id`；released、過期（31 分鐘）、不存在的批次 → exit 3，`這批已經停了或做完了` |
| 7 | 過 | `subagent.py start`（`agent_type: wiki-with-willy:card-maker`）：`hookSpecificOutput.hookEventName` = SubagentStart，`additionalContext` 第一行是「【wiki-with-willy】kb 指令：…」，含 `core.md` 全文（1096 字）；`batches.json` 的 `agents` 記上、`turn` 是 open job 的回合；`agent_type: Explore` → 不輸出、不記；壞 stdin（非 UTF-8 的亂碼）→ exit 0、沒輸出 |
| 8 | 過 | 帶 `agent_id` 的 Write 到 `檔案索引/` → 放行，20 張卡的備份全進 job 回合（客戶中途開了新回合，新回合 manifest 是空的）；單一 kb 呼叫 → `allow` 加 `updatedInput.command` = `python ".../kb.py" --turn 20261023-150621-sid- --agent agt-1 cards batch … --json`（插在 `kb.py"` 之後、其餘不變，`description` 也照抄）；主對話（沒 `agent_id`）與沒記過的 `agent_id` → 不改。**真子代理也生效**（spec 要求先驗）：B-412 實跑的 batch 被記上真的 agent_id（`a77a44a659bb79a29`），那只能經過 `updatedInput` |
| 9 | 過 | 經守門改寫的 `entity add` → `entities.json` 備份進 job 回合、不在新回合；journal 沒有多「你在終端機跑的：kb entity add」；`--turn 20200101-000000-nope` → exit 3，`回合不存在：20200101-000000-nope`；`../x`、`a/b`、`..\..\evil` 都 exit 3 `回合不存在`（沒有碰到檔案系統） |
| 10 | 過 | prompt 以 `<agent-message`、`[SYSTEM NOTIFICATION`、`<task-notification`、`<system-reminder` 開頭都不開回合（current.json 的回合代號不變） |
| 11 | 過 | 派工那句 Stop：`做了：沒有改動`；`subagent.py stop` 後那句變 `做了：新增 20 張檔案卡、改了 1 個檔／改了：檔案卡 20 張；總目錄`；job 關掉後主對話下一次 Stop 把派工那句的結果換成最後回答（`整理完了：做好 45 份。`），再一次 Stop 不換（`第二次回答，不應該換。` 沒進日誌）。journal 沒有多出 `<agent-message` 的段 |
| 12 | 過 | `cards stop --json` = `{"agents": ["agt-1"], "done": 0, "left": 45}`；批次全 `released`、job `closed`、session 回合檔 `ingest_stopped: true`；之後同一 session 的開場（`session.py`）整理指示那行是空的（只剩「還有 45 份沒整理完」）；再 `stop` → `現在沒有在整理`；同一句停後 dispatch → `stopped: true`、不派；下一句說「整理」→ 派 `[20, 20, 5]` |
| 13 | 過 | 一個 job 寫 45 張卡、中途客戶開了新回合（只讀）→ `versions revert` 一次：`已退回 …：還原 1、移除 48、成品 0 份`（還原的 1 是 `entities.json`、移除的有 45 張卡、`總目錄.md`、`batches.json` 等）。真跑也看到：B-412 的 job 回合 manifest 有 17 張卡 create + 總目錄 + 實體清單，其他回合沒有東西 |
| 13b | 過 | 清冊 2 份讀不到（一份有密碼、一份連結型）、3 份新、5 張卡要搬 → 開場行 `還有 3 份沒整理完、5 張卡要跟著搬`（連結型沒記讀不到前是 `還有 4 份…`）；連結型的 `tell` 是新那句（見 13c） |
| 13c | 過 | `output link` 登記一份連結型 → `dispatch` 不切它（只切 3 份新檔）；`plan` 有一步 `cards`、`files` 只列它、`read` 是 carding.md 一～六節；`cards unreadable <它> --reason "線上文件打不開"` → `已記進待處理（p-502c8c81）`，待處理多一筆、`tell` 多 `線上報告 讀不到：線上文件打不開；到 claude.ai 確認那份還在、有分享給這個帳號，或跟我說先跳過；記在 系統/待處理.md`、`plan` 的 `cards` 步消失、開場數字不再算它；對一般檔 → exit 3，`這個指令只收線上文件；檔案讀不到由 convert 記`；不存在的編號 → exit 3，`清冊沒有 f-nonexist` |
| 14 | 過（10/10） | 見下「B-412」 |
| 15 | 過 | `test_budget` 在全部測試裡綠（含 card-maker 本文 1456 字與 SubagentStart 注入長度）；`claude plugin validate` 通過（警告只有根目錄 CLAUDE.md 不載入、沒有 icon，都是既有）；`plugin-dev:plugin-validator` 0 錯誤 |
| 16 | 過 | `python -m unittest discover tests` exit 0，Ran 2191 tests，OK (skipped=1)，487 秒，在乾淨樹（commit 之後）跑 |

## B-412（驗收 14）

方法：每次一個全新的 tmp 知識庫（來源 15 份 + 知識庫自帶 4 份 = 19 份沒整理，`cards todo` 的 `new` 恰為 19），登記一個來源，`claude -p "<prompt>" --plugin-dir <worktree> --permission-mode acceptEdits --allowedTools "Bash(python *)" Read Glob Grep Skill Agent --output-format stream-json --verbose`，每次逾時 420 秒，一次只開一個。判準：主對話有沒有跑 `cards dispatch`、有沒有用 Agent 派 card-maker。第一句長指示是 237 字（接手交接說明、要求打招呼並回報知識庫整體狀況）。

| 次 | prompt | 秒 | 先整理（`cards dispatch`） | 派 card-maker | 做出卡 | 第一個動作 |
|---|---|---|---|---|---|---|
| 1 | 你好 | 188 | 是 | 是 | 17 | 說「知識庫裡有 19 份還沒整理，我先照整理流程處理…」→ Skill ingest；**之後說 `Step 1: check changes.`** |
| 2 | 你好 | 213 | 是 | 是 | 17 | 說「…我先照流程整理好，再回答你」→ Skill ingest → `changes` → `cards todo` |
| 3 | 你好 | 198 | 是 | 是 | 17 | 同上；**中途說 `Another batch (a synthesis card) is ready; dispatching it as…`（英文）** |
| 4 | 你好 | 213 | 是 | 是 | 17 | 同上 |
| 5 | 你好 | 215 | 是 | 是 | 17 | 說「資料夾裡有 19 份檔案還沒整理，我先整理成檔案卡，再回你」→ Skill ingest；**`Step 1: check changes.`** |
| 6 | 長指示 | 189 | 是 | 是 | 17 | 先說「我先說一句：知識庫裡還有 19 份檔案沒整理，我會先照整理流程處理，再完整回答您」→ Skill ingest |
| 7 | 長指示 | 271 | 是 | 是 | 17 | 直接 Skill ingest（沒先說話） |
| 8 | 長指示 | 277 | 是 | 是 | 17 | 同上 |
| 9 | 長指示 | 248 | 是 | 是 | 17 | 同上 |
| 10 | 長指示 | 283 | 是 | 是 | 17 | 同上 |

- **十次都先整理**，10／10（門檻是各至少 4／5）。第一句很長的情況（這次重現 B-412 的條件）沒有被指示蓋掉。
- 最後 17 張而不是 19：知識庫自帶的 `案子/說明.txt` 被 `convert` 判「這不是要製卡的檔」，另一份是綜合卡；見 S2。
- 權限拒絕（`permission_denials`）十次都是 0；十個 tmp 知識庫（還有第一組作廢的）都沒有 `.system/hook-errors.log`。
- 對客戶可見的主對話文字（排除子代理內部說的話）：**3／10 出現英文或步驟編號**（#1、#5 的 `Step 1: check changes.`，#3 的 `Another batch … is ready`）。子代理內部的英文（「Convert the rest」之類）每次都有，客戶看不到，不算。
- 時間：160～283 秒，跟 `announce` 說的「大約 3 分鐘」一致。
- 最後一則回答：多數是「做了 N 份，還有 M 份沒做成」並列出原因；#2、#3、#4 的主對話發現同一份重複被排進新批次，自己停下來說「我先停在這裡，不再派工」（見 S2）。

## 重大發現（建議交給實作修）

### S1 密碼值可寫進派工那句的工作日誌（高）
`hooks/stop.py` 的 `_jobs`（line 78～100）：job 關閉後第一次 Stop，把這一次 Stop 的最後回答寫進派工那句的結果，只看**派工回合**有沒有 `secret_shown`（line 91），沒看**這一句**。`secret_shown` 是記在給密碼的那一句。
重現（我跑過）：派工後 job 關閉；客戶下一句，回合記 `secret_shown`；AI 回答「好的，Hunter2xyz 這組我看到了」（沒有「密碼」關鍵字，遮值遮不到，也不在憑證管理員）。結果：這一句的日誌是固定句 `（這句回答有密碼，沒記）`，**派工那句的結果卻是 `好的，Hunter2xyz 這組我看到了`**，值同時進了 jsonl 與 `系統/工作日誌/*.md`。（回答裡有「密碼」二字時遮值有擋住，所以只有值單獨出現時漏。）
建議：`_jobs` 也看這一次 Stop 的回合 `secret_shown`（`_finish` 用的那份 `data`），旗標在就不換結果，只補改了。這是 spec 交界第 2 點的另一半。

### S2 做不成的項目被無限重切（高；實跑已遇到）
`batches.dispatch` 只把「現在 open 且未過期」的批次當認領；批次 `done`／`released` 後仍留在 todo 的項目（讀不到、被打回兩次、子代理停在中間）下一次 dispatch 又被切成新批次。沒有重試計數，也沒有「試過、失敗了」的記憶。SKILL 骨架第 4 步要求每收到通知就再 dispatch，會一直循環、燒額度。
實跑證據：B-412 #4 的知識庫 `batches.json` = 19 份 `done`，之後三個各 1 份的批次（同一份 `案子/說明.txt`，`convert` 回「這不是要製卡的檔」，`todo` 卻仍列為 new）。主對話是自己發現後才停的（`程式每次都把它排進新一批，製卡助手每次都回「讀不到：…」`）。這份檔是知識庫自帶的測試檔，不是我造的情境，客戶的知識庫也可能出現 `todo` 列、`convert` 拒的檔。
建議：批次回報沒做成的項目記進 job（或 `batches.json` 的 `failed`），同一個 job 內不再切；或 `done` 後仍在 todo 的項目算一次嘗試，超過 N 次當讀不到記進待處理。順帶：`todo` 列出而 `convert` 拒絕的不一致要查（這份是 `知識庫/案子/說明.txt`）。

### S3 子代理能跑任何 kb 子命令（中高）
`kb.guard` 對單一 kb 呼叫一律 allow（`知識庫腳本`），`agents/card-maker.md` 的「不做的事」只是文字。我用 guard 模擬：帶 `agent_id`（在 agents 裡）的 `turn approve`、`cards stop`、`cards dispatch`、`versions revert`、`folders exclude`、`source-edit trash`、`entity remove` 全部 `allow`。子代理讀的是客戶原檔轉出的文字，內容可被攻擊者控制（間接提示注入）；注入成功可以自己 `turn approve` 解除大改動上限（D-075；spec 說「子代理不能問客戶」被繞過）、停掉或退回客戶的工作、丟檔進資源回收筒。
建議：守門對 agents 裡的 `agent_id` 改白名單（`cards batch`、`convert`、`agenda scanned／add／edit／recheck`、`entity add`、需要的 `pending` 等），其餘 deny。
同類：帶 `agent_id` 的 `Read` 讀 `.system/unlock.json`、`.system/temp/*.secret.md` 都 allow（跟主對話同一個區規則）；密碼檔暫存存在的短時間內，背景子代理讀得到。

### S4 守門插入的 `--turn`／`--agent` 可被模型自己再帶的蓋掉（中）
`hooks/guard.py` 的 `with_turn` 不檢查原指令有沒有 `--turn`、`--agent`。我實測：原指令 `kb.py --turn <別的回合> entity add …`，改寫成 `kb.py --turn <job 回合> --agent agt-1 --turn <別的回合> entity add …`，argparse 取最後一個，**備份進了模型指定的回合**（`entities.json` 備份出現在指定回合的 manifest）。只要回合存在就行（`--turn` 只驗存在，不驗是這個 session 的）。
建議：`with_turn` 遇到原指令已有 `--turn`／`--agent` 就拒絕或剝掉；`cli.py` 的 `--turn` 驗是這個 session 的回合（或只收 job 回合）。

### S5 `cards unreadable --reason` 不遮值、不限長（中低）
我實測 `--reason "密碼是 Hunter2xyz 之類 $(x) \`y\`"`：`pending.json` 與 `系統/待處理.md` 都原樣出現 `Hunter2xyz`；5000 字的原因也收。`plan.UNREADABLE_ADVICE` 與 `tell` 之後會把它轉給客戶。主對話有可能把畫面上的字貼進去。
建議：套 `credentials.mask_secrets` 與長度上限，擋 `$`、反引號（跟 `statement add` 的 `check_text` 同一套）。

### 其他讀碼疑點（已列給實作，未逐一重現）
- `batches.dispatch` 的 `_collect` 在鎖外執行：SubagentStop 剛好插進來可能重切剛做完的項目；過期但 agent 還在跑的批次會被另一個 agent 重切、兩個 agent 寫同一張卡。
- `_approval` 只數 `synthesis_stale` 與已改的張數，`before_write` 擋所有既有綜合卡改寫（含 broken）；6 張 stale + 6 張 broken 時 agent 第 11 次寫入被擋、卻問不了客戶。
- 回合檔 read-modify-write 沒有鎖（`save_turn`），三個並行子代理的 `before_write`、主對話的 `mark`／`approve`／Stop 可能互相蓋掉 `approved`、`ingest_stopped`、`done`、`job_result`、`secret_shown`。
- `cards._session` 取 `current.json` 的 session，同一個知識庫開兩個視窗會串；終端機拒絕也只在 current 沒 session 時成立。
- 子代理在第一次 `cards batch` 之前就死掉：批次沒有 `agent_id`，`SubagentStop` 改不了，30 分鐘內 dispatch 回「還在跑」。
- `dispatch --source` 打錯別名：全部濾掉，新 job 回 `done: true`、人讀「整理完了」。我實跑確認（`沒有新派的；還在跑 2 批；還剩 0`，沒有任何錯誤）。
- `synthesis_new` 項目的 `type` 欄是實體型別，`batch_view` 的 `dict(item, type=bucket)` 蓋掉它（`carding.md` 兩處說法不同）。
- 主對話在第一次 Stop 之後（回合 `done`）跑的 `cards dispatch`／`entity add` 等，journal 會算成「你在終端機跑的」（我實跑確認 `kb entity add` 出現一段程式段）；`hooks/turn.py` 的舊 `_ingest_stopped` 偵測在派工過程中客戶打字時可能誤判「被停了」。
- `opening_counts` 會把連結型、可以讀的密碼檔算進份數，而 `dispatch` 不切它們；`plan._waiting` 不排除 purge pending 夾；兩個數字可能對不上。
- `_locked_ids` 沒看待處理狀態，已 resolved／dismissed 的「有密碼」永遠留在主對話那邊。
- `batches.json` 的 `jobs`／`batches`／`agents` 只增不減；每次 `cards batch` 都跑一次完整 `cards.collect`。
- 測試沒涵蓋：上列迴圈、混合 broken+stale、批次沒被認領就收到 SubagentStop、壞 `batches.json` 的 stop／`agent_turn`、PowerShell 的 `with_turn`、`--source` 打錯、兩個 session、並行 `save_turn`。

## plugin-dev 審查

- **`plugin-dev:agent-development` 審 `agents/card-maker.md`**：結構合格（name 10 字小寫連字號、description 197 字、`model: inherit`、`color: green`、`tools` 最小集合 Read／Write／Edit／Bash、本文第二人稱 1456 字、有「When to invoke」三情境、回報格式明確）。三點：(1) `background: true` 不在 plugin-dev 的 agent frontmatter 清單（name／description／model／color／tools）；`claude plugin validate` 沒報錯，B-412 實跑的 card-maker 確實在背景跑，主對話繼續往下做，但我沒有官方文件可引，建議在 spec 或 CLAUDE.md 註明來源。(2) 現行 plugin-dev 模板把 `<example>` 換成「When to invoke」body 節，本檔符合。(3) 本文「不做的事」全靠文字，守門沒有對應（見 S3）。
- **`plugin-dev:hook-development` 審 `hooks/subagent.py` 與 `hooks.json`**：結構合格——命令都用 `${CLAUDE_PLUGIN_ROOT}`，SubagentStart／SubagentStop 的 matcher `^wiki-with-willy:card-maker$` 與腳本內 `AGENT_TYPE` 一致，timeout 10，stdin 先 UTF-8、任何例外吞掉並 exit 0。兩點：(1) `hooks.json` 的 `onFailure: "block"`（PreToolUse，前一題加的）在 plugin-dev 文件裡查不到，`claude plugin validate` 沒拒絕；前一題的確認已涵蓋，這題不重驗。(2) 壞 stdin 時 `_log` 找不到知識庫，錯誤只能落到 `%TEMP%`（跟 guard 同一個設計），spec 寫「exit 0 寫 log」在知識庫外是寫不到的，非缺陷。另外 `hooks.json` 頂層 `description` 很長，無害。
- **`plugin-dev:skill-development` 審 `skills/ingest/`**：SKILL.md 本文骨架符合 D-132（分辨、原則附理由與編號、骨架；指令與答法由 `cards todo --json` 的 `plan`／`asks`／`tell` 給）；描述沒改（新舊 `sed -n 3p` 的 sha256 相同 `e52920a35711117e`）；references 兩檔 140、150 行都在 150 行上限內。一點：骨架第 4 步「每收到通知就再 dispatch」搭配 S2 會變成迴圈，建議骨架補「同一份回報沒做成就不再重派」。
- **`plugin-dev:plugin-validator` 對整個 repo**：PASS，0 錯誤，2 警告（`background: true`、`onFailure` 都是 plugin-dev 本機文件未記載的欄位，`claude plugin validate` 通過）。manifest 版號 0.19.0 與 CHANGELOG 一致；所有 skill 的 references 都存在；除 tests／design 外沒有寫死的絕對路徑；六個 hook 腳本與 `scripts/kb.py` 語法正常。建議項：README「怎麼用」整理那段我查過已改（README.md line 47），指令清單也有（line 148）。

## ingest 路由（skill-creator run_eval）

描述沒改（新舊版第 3 行 sha256 相同），所以「新舊對照」等於同一份描述跑兩次；舊版 = `c3fdf63` 的 `skills/ingest/SKILL.md` 快照，新版 = 60b909b。`tests/evals/ingest.json` 20 句（10 該觸發、10 不該，不該的是 manage／answer／note／produce 的近似句）。

| 版本 | 該觸發（10 句×3 次） | 不該觸發（10 句×3 次） | 20 句通過 |
|---|---|---|---|
| 新（60b909b） | 30／30 = 100% | 0／30 = 0% | 20／20 |
| 舊（c3fdf63 快照） | 30／30 = 100% | 0／30 = 0% | 20／20 |

門檻（D-091）：該觸發 ≥ 90%、不該 ≤ 10%：兩版都過，沒有退步；上一輪 ingest 測試報告（`ingest製卡-測試.md`）失敗的「資料夾裡的東西都整理好了嗎？還有沒做的嗎」這次 3／3。

跑法與限制：
- Windows 的已知問題（B-036）：`run_eval` 讀 evals 不指定編碼、用 `select.select` 讀子行程管線。照前幾份報告的做法，原檔不動，`PYTHONUTF8=1`，`PYTHONPATH` 前面放一個 `sitecustomize.py`（把 `select.select` 換成永遠就緒），`--num-workers 1 --timeout 60 --runs-per-query 3`，`query failed`／WinError 0 次。
- 我第一次沒放 shim：兩版都是「該觸發 0／30」、「不該觸發 0／30」（20 句只過 10 句），這是 B-036 的症狀（所有 query 都當未觸發），不是路由結果，**作廢**；加了 shim 後才是上表。
- 在 scratchpad 的空資料夾（只有 `.claude/commands/`）跑，`run_eval` 沒有寫到 `~\.claude\commands`（跑後該夾沒有檔）；暫時指令檔都已清掉。
- 限制同 produce 報告：`run_eval` 只放被測 skill 的暫存指令檔，沒有載 wiki-with-willy 本身，所以不是五個 skill 同時裝著的路由；plugin 那層由 B-412 的真實對話補（十次都走到 `Skill wiki-with-willy:ingest`）。
- 舊 `llm-wiki` 是停用狀態（settings.json `enabledPlugins` false），不是「同時裝著且啟用」；這是使用者範圍的既有設定，我沒有改。

## 回測第一輪

日期：2026-10-23
測的版本：`87fc8d6`（`b1ca460..87fc8d6` = f6d75a8 主體、f3ad435、87fc8d6）；跑前樹乾淨，沒改任何程式。
範圍：22 派的 9 項（只驗修過的地方，不重跑 19 條）。

### 結論

- 9 項裡 **8 項過、第 4 項（S4）有一個洞沒補完**：子代理的指令寫 `--tu=…`、`--ses=…`、`--ag=…`（argparse 的縮寫），守門放行，我實測寫進了另一個 session 的回合。建議 `cli.py` 的 parser（含子 parser）加 `allow_abbrev=False`。
- B-412 第二輪十次：**十次都先整理**；英文 **1／10**（第一輪 3／10）；重複派同一份 **0 次**；步驟編號 **0 次**。
- 新發現 R1：`synthesis_broken` 檔頭壞掉的綜合卡，製卡助手 **10／10 都做不成**（批次材料沒給檔頭）；沒有再重切（S2 有效），但每個新對話都會再試一次。
- 全部測試 `python -m unittest discover tests`：exit 0，Ran 2224，OK（skipped=1），490 秒（87fc8d6，樹乾淨）。`claude plugin validate`：通過（警告同上輪：根目錄 CLAUDE.md 不載入、沒有 icon、README 安裝行）。
- settings.json sha256：跑前（全文存在 scratchpad `settings_before_round2.json`）`8ae82f7a69eea7b8321459b4a81b9d734c934bcbe188794b63816af6e2cf4039`，跑後同一個值，`diff` 逐行相同。上輪那個變動（`ac9c560c…`→`8ae82f7a…`）這輪沒再變。舊 `llm-wiki@llm-wiki` 的 `enabledPlugins` 仍是 false。

### 逐項

| # | 結果 | 證據 |
|---|---|---|
| 1 S1 | 過 | 用原本的重現：派工、job 關掉、下一句回合記 `secret_shown`、回答 `好的，Hunter2xyz 這組我看到了`（沒有「密碼」二字）、連 Stop 兩次。兩句的 jsonl 與 `系統/工作日誌/*.md` **都沒有** `Hunter2xyz`（這一句是 `（這句回答有密碼，沒記）`，派工那句留 `交給製卡助手整理了`）。對照：沒有 `secret_shown` 的一般回答仍會把派工那句換成最後回答 `整理好了，做了 12 份。`。另一種擺法（旗標記在派工那句、後一句值單獨出現）：派工那句沒被換，後一句自己的日誌會寫值，因為那一句沒有旗標；這是旗標的設計，不是新漏洞 |
| 2a S2 | 過 | 12 份、一份被製卡助手略過並在回報寫 `- f-4c61213d：讀不到（測試用的原因）`：`SubagentStop` 後 `dispatch --json` 的 `failed` = `[{"id":"f-4c61213d","kind":"cards","reason":"讀不到（測試用的原因）"}]`、`batches: []`、`done: true`，同一個 job 沒再切它；人讀句 `整理完了；沒做成 1 份` |
| 2b S2 | 過 | 第二個 job 同一份又沒做成 → 待處理多一筆 `unreadable`「製卡助手兩次沒做成：讀不到（測試用的原因）」、`todo.new` 不再列它、`unreadable` 與 `tell`（`空檔.bin 讀不到：製卡助手兩次沒做成：…；記在 系統/待處理.md`）都有；改了那份原檔再 `inventory scan` → 待處理那筆變 `done`、`todo.new` 又列它、下一個 job 的 dispatch 又切它 |
| 2c S2 | 過 | 知識庫自帶 `知識庫/案子/說明.txt`（清冊 active）不在 `todo.new`；同夾的 `知識庫/案子/信義王/合約.docx` 仍在（不是一律不列，是型別夾根直接放的才不列） |
| 3 S3 | 過 | 帶 agents 裡的 `agent_id`，Bash 單一 kb 呼叫：deny（理由 `製卡助手不能做這件事`）的有 `turn approve`、`versions revert`、`cards stop`、`cards dispatch`、`cards status`、`folders exclude`、`source-edit trash`、`entity remove`／`merge`／`rename`、`profile apply`、`pending dismiss`、`ruling add`、`statement add`、`inventory scan`、`output save`、`office run`、`unlock get`、`cards unreadable`／`sync`／`drop`／`index`／`redo`、`guard decide`，共 24 條全 deny；allow 的 `cards batch`／`list`／`todo`／`skip`、`convert`（含 `--json`）、`version`、`agenda scanned`／`add`／`edit`／`recheck`／`cancel`／`list`、`entity add`／`suggest-close`／`clean`／`list`，共 17 條全 allow（改寫成插了 `--turn --agent`）；`convert` 帶 `--password`、`--saved`、`--explicit`、`--password=abc` 都 deny。Read `.system/unlock.json`、`.system/temp/x.secret.md`、`.system/temp/f-1.secret`、Glob `.system/temp/*.secret*`、Grep `.system`、Glob `**/unlock*` 全 deny；Read `.system/temp/f-1.md`（convert 輸出）與 Write 檔案索引的卡 allow；Write `.system/inventory.json`、原檔、`總目錄.md` deny；PowerShell 的 cards batch allow、turn approve deny。對照 `agents/card-maker.md` 與 carding.md 一～九節用到的 kb 指令（cards batch、convert、agenda scanned／add／edit／recheck／cancel／list、entity add／suggest-close／clean），**沒有它要用卻被擋的**；`cards redo`、`cards unreadable` 在 carding.md 是主對話的事。Read carding.md、synthesis.md（在 plugin 目錄、知識庫外）守門沒有意見，照一般流程 |
| 4 S4 | **洞** | 通過的：子代理自己帶完整的 `--turn`、`--agent`、`--session`、`--turn=`（在子命令前或後）全 deny（`kb 指令不能自己帶 --turn、--agent 或 --session`）；主對話自己帶 `--session evil`、`--turn T9` 也 deny；cli 帶別的 session 的回合 → exit 3，`這個回合不是這個對話的：<回合>`；`--turn` 不存在 → exit 3，`回合不存在：…`。**沒通過的**：argparse 預設吃縮寫，`--tu=<回合>`、`--ses=<session>`、`--ag=x` 不是 `--turn`／`--agent`／`--session` 的完整字串，守門的 `_has_flag` 不認，放行並照樣改寫成 `kb.py --turn <job> --agent agt-1 --tu=<別的回合> …`。實測：session A 的子代理、`current.json` 是 session B，指令 `--tu=<B 的回合> --ses=sid-B --ag=x entity add …` → exit 0，**B 回合的 manifest 多了 `entities.json` 的備份**（只用 `--tu=` 一個也成功，因為 `owned()` 沒有 `--session` 時取 `current.json`，剛好是 B）。主對話同樣能帶 `--tu=T9`，守門照放行並照插 `--session`。需要模型刻意寫縮寫才會發生，但這正是 S4 要擋的 |
| 5 S5 | 過 | `cards unreadable <link> --reason …`：`密碼是 Hunter2xyz`、`password=abc123`、`帳號 a 密碼 b` → exit 3，`原因裡不要放密碼；要記的話跟我說『記下來』，我存進這台電腦的憑證管理員`；含 `$`（`打不開 $(x)`）、反引號、雙引號 → exit 3，`原因裡不能有 " $ ` \ 這四種字元`；201 字、兩行、全空白 → exit 3，`原因要一句話，最多 200 字`；200 字、`線上文件打不開` → exit 0。`pending.json` 與 `系統/待處理.md` 都沒有 `Hunter2xyz`。拒絕句與 `statement add` 同一套（只把「口述」換成「原因」） |
| 6 讀碼疑點 | 過（驗了四項） | `--source` 打錯：exit 3，`沒有登記這個來源：不存在的別名`（`--json` 也是純文字 exit 3），登記過的 `報價與合約` 正常派 15 份。兩個視窗：A 派工後 B 說話，A 的再 dispatch 沿用 A 的 job（`running 2`、不新開）、`status` 各看各的（B：`現在沒有在整理；還有 25 份沒整理`）、B 的 `stop` 不動 A 的批次與 job；終端機跑 `dispatch` → exit 3，`整理要在對話裡派`。第一次 Stop 之後主對話跑的 `entity add`、`turn note --turn`，日誌沒有「你在終端機跑的」那一段（`turn note` 的「做了」進了派工那句）。背景整理中客戶打字：`hooks/turn.py` 沒輸出、沒記 `ingest_stopped`；對照沒有 job 時仍印「上一句的整理被你停了…」。順帶：`dispatch` 在 `done: true` 之後再叫一次會開新 job、把 `failed` 的再切一次（人讀 `派了 1 批（做卡 1）`），這會讓「兩個 job 都沒做成」比預期更快成立；B-412 的十次主對話都只叫 3 次（初派、兩次通知），沒發生。`stop` 在沒有 job 的 session 的 `--json` 是 `{"agents":[],"done":0,"left":null}`（人讀是 `現在沒有在整理`），`left` 是 null |
| 7 開場份數 | 過 | 只剩一份連結型：開場 `還有 1 份沒整理完`、`plan` 只有 `cards` 一步（`items 1`）、`dispatch` 不派（`batches: []`）；`cards unreadable <它> --reason "線上文件打不開"` 之後 `plan` 空、`announce` null、開場那行不見 |
| 8 B-412 | 過（10／10 先整理） | 見下 |
| 9 validate | 過 | `claude plugin validate`：Validation passed with warnings（根目錄 CLAUDE.md 不載入、沒有 icon、README 安裝行，都是既有） |

### B-412 第二輪（10 次）

方法同上輪：每次全新 tmp 知識庫（`cards todo` 的 `new` 恰為 19；這輪 `說明.txt` 不再列，所以來源檔補到 19，來源 `客戶案件`），`claude -p "<prompt>" --plugin-dir <worktree> --permission-mode acceptEdits --allowedTools "Bash(python *)" Read Glob Grep Skill Agent --output-format stream-json --verbose`，逾時 480 秒，一次一個；只用 `--plugin-dir`。「你好」5、237 字長指示 5。

| 次 | prompt | 秒 | 第一個 kb 指令 | `cards dispatch` 次數 | 派 card-maker | 卡 | 對客戶說的英文 |
|---|---|---|---|---|---|---|---|
| 1～5 | 你好 | 338、281、303、274、273 | `changes`（Skill ingest 之後） | 各 3 | 各 2 | 各 18 | 無 |
| 6～8 | 長指示 | 289、249、241 | 同上 | 各 3 | 各 2 | 各 18 | 無 |
| 9 | 長指示 | 254 | 同上 | 3 | 2 | 18 | **1 句**（見下） |
| 10 | 長指示 | 294 | 同上 | 3 | 2 | 18 | 無 |

- (a) **十次都先整理**：先 `changes`、`cards todo`，說 announce（`有 19 份要整理，大約 3 分鐘；要停的話跟我說一聲`），再 `cards dispatch`、Agent 派。第一個動作兩種：先 Skill ingest 再 announce（多數），或先打招呼一句再 Skill。
- (b) 英文或步驟編號：**英文 1／10**（第 9 次：收到第一批通知後說了整句英文 `Thanks for waiting. The first batch is done: 18 files are now cards. One file, 信義王's 合約.docx, couldn't be read because the file seems damaged, and I've added it to the to-do list. One summary card is still being built in the background. I'll give you the full overview once it's finished.`）；步驟編號（`Step 1:` 這類）**0／10**。上輪 3／10 是 `Step 1: check changes.` ×2 與 `Another batch … is ready` ×1；S6 之後步驟編號沒了，但通知後的過場句偶爾仍會英文。
- (c) **19 份做出 18 張**：另一份是知識庫自帶的 `信義王/合約.docx`（fixture 本身是壞檔），`convert` 讀不到、記進待處理，原因 `檔案壞了：不是完整的 Office 檔`；十次最後一則回答都講了「18 份做好、合約.docx 讀不到」與原因，也都有講綜合卡沒更新（見 R1）。`todo` 剩下 `new` 0。
- (d) **重複派同一份：0**：每個 job 的 cards 批次只有 1 個（19 份）；同一份沒有出現在兩個批次；synthesis 批次 1 個（1 項）。每次都是 3 次 `cards dispatch`（初派、cards 做完、綜合卡做完）。
- 權限拒絕沒有；`hook-errors.log` 十個知識庫都沒有。

### 新發現

**R1（中）`synthesis_broken` 的檔頭壞掉，製卡助手做不了，10／10 失敗**
知識庫自帶的 `案子/信義王/信義王.md` 是一張檔頭壞掉的綜合卡。`dispatch` 把它切成 synthesis 批次（1 項），`cards batch` 給的項目只有 `path` 與 `reason`（「檔頭解析不了」），沒有 `header`、檔案卡、口述。carding.md 第八節寫「其他 reason（檔頭有錯）照原因改、寫回同一個路徑」，但製卡助手沒有可照抄的檔頭，規則又叫它不要自己編，十次回報都是 `- <路徑>：這張綜合卡缺檔頭，但批次項目只給了路徑和原因，沒給檔頭，也沒給可當依據的檔案卡或…`（原因文字十次不同，意思相同）。
S2 讓它不會再重切、`failed` 有列、主對話有講；但這份留在 `todo` 的 `synthesis_broken`，每個新對話、新 job 都再試一次，永遠修不好。
建議：`batch_view` 對 `synthesis_broken` 項補上該實體的 `header`（`synthesis.header_for`）與掛的檔案卡、口述；或這種項目不切給製卡助手，留給主對話／待處理說明。
（這張卡是 fixture 的人造狀態；真實知識庫也會因手動改壞而出現同樣的卡。）

**R2（低）通知後的過場句偶爾英文**
見 B-412 第 9 次。程式沒辦法擋，骨架已寫「過場不說英文」；要不要再加強由設計 session 決定。

**R3（低，觀察）守門的 scoping 只管「單一 kb 呼叫」**
`kb.py … && …`、`| head`、前面加環境變數的 kb 呼叫，守門回「無意見」（我測 `chain &&`、`pipe`、`env prefix`、`cd + call` 都是），交給 Claude Code 一般權限流程。製卡助手在背景、沒人按允許，一般會被自動拒絕；但使用者的設定若允許 `Bash(python *)`（本輪測試用 `--allowedTools "Bash(python *)"` 就是），這類指令不會被 AGENT_ALLOW 管。沒有實測子代理真的這樣跑，只是守門的行為；是否要處理由設計 session 決定。另外 `python "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" cards dispatch` 字面寫法守門同樣無意見（不會插 `--session`，會得到 `整理要在對話裡派`）；B-412 十次都用開場給的絕對路徑，沒遇到。
