# card-maker 與整批製卡
日期：2026-10-22
狀態：done（2026-10-24 發行 v0.20.0）
版本：0.20.0

## 目的
現在整理成卡只有主對話自己做：20 份以內直接做，超過先問，客戶說「現在做」之後還是主對話一份一份做完（D-100、D-156、`plan.py` 的 `then`）。幾百份時原檔內容全進主對話、對話會被摺疊、一次做不完；建置時更是整批幾百上千份（D-022 安靜模式的前提是子代理）。這題讓做卡、抽日程、寫綜合卡一律交給子代理 card-maker 在背景分批做，主對話只管派工、問客戶、回答，並由程式保證模型自己保證不了的事：子代理拿得到核心規則（B-032）、寫卡照樣過守門、備份、驗卡、算回派它的那一句（退得回來）、中斷了接著做不重做不漏做、同一份不會被兩個子代理做、事先講得出大約多久（B-108）。build 的全量整理（第 (5) 題）用同一套。

## 決策
D-195（背景跑，子代理的寫入由程式算回派它的那一句）、D-196（製卡一律交給 card-maker，超過 20 份先問照留）、D-197（分批由程式切，每批清單程式直接給）、D-198（card-maker 做卡也抽日程；綜合卡也交子代理，材料程式給；ingest 管派工、問客戶、回答）、D-199（整理前只講時間，用量一句話提醒，不另設計分天）、D-200（背景整理要停：說一聲，AI 停子代理並照指令記號）。相關的既有決策：D-022、D-053、D-073、D-074、D-075、D-084、D-100、D-104、D-114、D-115、D-132、D-134、D-135、D-147、D-156、D-161、D-133（10-22 補：實作測試與 build 分開、確認併在一起）。

**開工條件**（2026-10-23 使用者同意改）：實作現在就開工，底用 `spec/密碼與守門` 的 c3fdf63（測了六輪的最終版，只差桌面版確認）：`git worktree add ..\llm-wiki-j-cardmaker -b spec/card-maker c3fdf63`；密碼與守門確認後若再修，a2 給 commit、合進本分支。實作 session 只跑單元測試、不另開 Claude；測試 session（會開 Claude、跑 run_eval 與 `claude -p`）與桌面版確認等密碼與守門發行之後。發行順序不變：先密碼與守門，再本題與 build 一起。spike 報告已收：`design/reports/card-maker-spike.md`（CLI 九題加桌面版一節）。

### 例句（設計 session 2026-10-22 列，使用者 10-22 定範圍；林小姐：接案室內設計師；服務人員：建置那天在場的人）
card-maker 是子代理，客戶不會叫它：她的話進 ingest（建置那天服務人員打 build 指令），ingest 照程式的 `plan` 派 card-maker。下表是客戶看得到的部分。

| # | 誰 | 說什麼 | 去哪 | 收不收 |
|---|---|---|---|---|
| 1 | 林小姐 | （開場「有 236 份要整理…現在做嗎」）「好，現在做」 | ingest | 收：`cards dispatch` 分批派（D-196、D-197） |
| 2 | 林小姐 | 「要多久？會不會用掉很多額度？」 | ingest | 收：講時間，用量一句話（D-199） |
| 3 | 林小姐 | 「先整理報價那夾就好，其他的之後再說」 | ingest | 收：`cards dispatch --source <別名>`（見釐清） |
| 4 | 林小姐 | 「整理到哪了？」 | ingest | 收：`cards status` |
| 5 | 林小姐 | （整理中）「王宅的合約放在哪？」 | answer | 收：背景做的時候照常回答（spike 第 5 題、桌面版 D1），回答末尾一句還在整理 |
| 6 | 林小姐 | 「今天先到這裡，明天再繼續」 | ingest | 收：照 D-200 停 |
| 7 | 林小姐 | （隔天開新對話）開場「還有 180 份沒整理完」 | ingest | 收：照 D-161 接著做，不重做不漏做 |
| 8 | 林小姐 | （做到一半額度用完、當機、關掉視窗） | — | 收：做好的卡留著，沒做完的那批下次重派（spike 第 8 題、桌面版 D5） |
| 9 | 林小姐 | 「整理好了跟我說一聲」 | ingest | 收：做完主對話自己開口報（spike 第 5 題、桌面版 D1） |
| 10 | 林小姐 | 「剛剛那一大批整理得不對，全部退回」 | 整理 | 收：先問要重做還是不讀；重做就整批標記重做，不讀走排除（D-228，2026-10-24 改，原本是整批退一次） |
| 11 | 林小姐 | 「為什麼有 14 份沒整理成？」 | ingest | 收：子代理回報沒做成的與原因，`tell` 講讀不到的 |
| 12 | 林小姐 | （整批做完）「新冒出來那些名字要我確認嗎？」 | ingest | 收：照 `asks`，一次最多 3 題（D-134、D-135） |
| 13 | 林小姐 | 「綜合卡也一起寫好」 | ingest | 收：名字確認後派子代理寫（D-198） |
| 14 | 林小姐 | 「信義王那 30 份的卡都重做」 | ingest | 收：逐份 `cards redo` 後照常 dispatch（D-147） |
| 15 | 林小姐 | 「我加了 3 份新的」 | ingest | 收：也派 card-maker，開始前講一句（D-196） |
| 16 | 林小姐 | （整理中）「私人那夾不要整理，我不想讓你看到」 | manage | 不在這題（`folders exclude`）；正在跑的子代理做到那夾的檔時 `convert` 拒絕，照「沒做成」回報 |
| 17 | 林小姐 | 「你整理的時候會把我的檔案傳出去嗎？」 | answer | 不在這題（第 (5) 題建置報告的說法） |
| 18 | 服務人員 | 「先抽 10 份做給她看」 | build | 不在這題（第 (5) 題） |
| 19 | 服務人員 | 「全部做卡」（建置那天） | build | 指令歸第 (5) 題；派工、續做、估算用這題的 `cards dispatch` |
| 20 | 林小姐 | 「我另一台電腦也開著，在那邊也整理」 | — | 不收：兩台同時整理不支援；同一份不會做兩次靠 `batches.json` 的鎖與認領 |

### 使用者時刻的起點狀態
| 起點 | 長什麼樣 | 程式怎麼指 |
|---|---|---|
| 全新 | 剛加資料夾、還沒有卡 | `plan` 只有 `dispatch` 一步；一律派 card-maker |
| 升級前就有資料（確認情境從這裡開始，I-113） | 既有知識庫：卡、綜合卡、日程都有；舊版整理留下 `agenda_unscanned` 一大批、待建綜合卡沒確認名字 | `dispatch` 照順序先做卡、再日程、最後綜合卡；名字沒確認的綜合卡不派，等 `asks` |
| 做到一半：上次按停或說停了 | `batches.json` 有這個 session 的 job，批次 `released` | 同一個對話不再自動整理（D-200 記號）；客戶說整理才 `dispatch`，沒做完的照常列 |
| 做到一半：關視窗、當機、額度用完 | `batches.json` 有別的 session 的 `open` 批次 | 新對話不認舊 session 的認領；`cards todo` 照卡檔算剩幾份；開場照 D-161 |
| 做到一半：子代理被停在一份中間 | 卡是一次 Write 寫完，不會有半張；最多是那份沒卡 | 下次照常列在 `new` |

### 本題釐清、不改 D 的點
- **一個子代理，三種工作**：`agents/card-maker.md` 一份，工作種類 `cards`（做卡：new、stale、有檔頭的 broken、unlinked；每寫完一張新卡或重做的卡，同一份內容接著照 carding.md 第九節抽日程、跑 `agenda scanned`）、`agenda`（只抽或重對日程：`agenda_unscanned`、`agenda_recheck` 裡沒落在 cards 批次的）、`synthesis`（`synthesis_broken`、`synthesis_stale`、確認過的 `synthesis_new`）。種類與要讀的規則由 `cards batch` 的輸出告訴它（D-198）。
- **規則給子代理（B-032）**：`hooks/hooks.json` 加 `SubagentStart`，matcher `^wiki-with-willy:card-maker$`，`hooks/subagent.py start` 回 `additionalContext`：開場段的「kb 指令」那行加 `rules/core.md` 全文（跟 `session.py` 同一個函式組，不另抄一份）。不帶 matcher 會連內建子代理也注入，不要（spike 第 1 題）。
- **權限**：背景子代理遇到要人按允許的指令會被自動拒絕（spike 第 7 點）。card-maker 的 `tools` 只給 Read、Write、Edit、Bash；Bash 只跑 kb 指令（單一指令，守門照 D-073 放行），清單由 `cards batch` 直接給（D-197），不寫臨時 python、不加 `PYTHONIOENCODING=` 這類前綴；kb 自己把輸出設成 UTF-8（已是）。a2 轉來的 `PYTHONIOENCODING` 問題照此解。
- **回合歸屬（D-195）**：
  1. `kb cards dispatch` 開一個 job（`batches.json`）：這個 session 有 open 的 job 就沿用，否則新開一個，`turn` 記目前回合；同一個 job 的批次都算這個回合，不管中間客戶說了什麼。job 在 dispatch 發現什麼都不剩、或 `cards stop` 時關。
  2. `SubagentStart`（card-maker）把 `agent_id` 記進 `batches.json` 的 `agents`：`turn` 取這個 session open 的 job 的回合，沒有 job 取目前回合。
  3b. PostToolUse（`hooks/output_log.py`）同理：驗卡後重寫總目錄、重渲染綜合卡快取、`register_output` 的備份與找舊卡備份，都用 `agent_turn` 找到的回合（同一支函式）。
  3. 守門（`hooks/guard.py`）的輸入帶 `agent_id` 且在 `agents` 裡：檔案工具寫入的備份與大改動計數用那個回合（`turn.before_write` 收回合代號）；Bash 放行的單一 kb 呼叫用 PreToolUse 的 `updatedInput` 在 `kb.py` 路徑後面插 `--turn <回合> --agent <agent_id>`（整個 input 照抄、只換 `command`，`permissionDecision` 仍是 allow）。主對話與其他子代理的呼叫不改。
  4. `kb.py` 收全域 `--turn`、`--agent`（放在子命令前、`--help` 不列）：有 `--turn` 時這次呼叫的備份進那個回合、算「對話裡」（不寫程式段，D-085）；回合不存在 exit 3「回合不存在：<代號>」。
  - 實作先驗 `updatedInput` 在子代理的 Bash 呼叫生效（官方文件沒寫子代理會不會不同）；不生效就停下來回報，不自己換做法。
- **認領，同一份不做兩次**：`dispatch` 只從「沒被認領」的項目切批；認領 = 批次 `open`、同一個 session、建立不到 `card_batch_expire_minutes` 分鐘。批次在 `SubagentStop`（照 `agent_id`，子代理第一個 `cards batch` 呼叫時記上）改 `done`、`cards stop` 改 `released`；被殺的子代理沒有 SubagentStop（spike 第 5 點），靠過期與換 session 放掉。做完的項目自然不在 todo。
- **日誌（桌面版 D2、D3）**：`hooks/turn.py` 的 `NOT_A_PROMPT` 加 `<agent-message`；開頭是 `[SYSTEM NOTIFICATION` 的也不開回合（VS Code 擴充套件 10-22 看到通知前多這一行）。子代理寫完東西後「改了」要更新：`SubagentStop` 與主對話的 `Stop` 都對這個 session open 或剛關的 job 回合跑 `journal.refresh_changed`（重算改了、地板句，用 `update_turn`）。派工那句的「結果」是「交給…正在整理」；job 關掉後主對話下一次 Stop 把那句的結果換成這次的最後回答，只換一次（回合檔記 `job_result`）。`dispatch` 在 job 做完時給 `note_cmd`：`kb turn note --turn <回合> "{做了什麼}"`（`turn note` 收 `--turn`）。
- **停（D-200）**：`kb cards stop` 把這個 session 的 open 批次改 `released`、job 關掉、目前回合記 `ingest_stopped`，輸出要停的 `agents`（agent_id，主對話逐個 TaskStop）與做好幾份、還剩幾份。按停止鈕中斷主對話那一句的舊偵測（`hooks/turn.py` 的 `_ingest_stopped`）照留。
- **大改動（D-075）**：子代理不能問客戶，所以 `dispatch` 派 synthesis 批次前先算：這個 job 回合已改的既有綜合卡（`ai_synthesis`）加這次要派的 `synthesis_stale` 張數超過 `big_change_synthesis_max`、回合沒 approve → 不派 synthesis，回 `approval`（問句與 `kb turn approve --turn <回合>`；`turn approve` 收 `--turn`），主對話問客戶、說好才 approve 再 dispatch。
- **只整理一部分（例句 3）**：`dispatch --source <別名>` 只從那個來源的項目切批；synthesis 不受 `--source` 限制但照樣等 cards 都做完。
- **估時（D-199）**：`plan.py` 的 `BATCH_SECONDS` 拿掉，改讀 `card_seconds_per_file` 與 `card_batch_overhead_seconds`：秒數 = 份數 × 每份秒數 + 準備秒數；不到 60 秒說「一兩分鐘」。
- **B-412 的其他資料與 B-415（a2 轉來 manage 第二版確認第 0～0c 節，10-22）**：三次開場兩次照做，沒照做的那次第一句是一大段接手指示（任務書），AI 先處理那段才整理——B-412 重現時也要試「第一句很長」的情況。另外兩件併進這題：(1) 開場的數字要對：`cards.unsorted` 只算 `dispatch` 真的派得出去的（讀不到的、包括 produce 連結型條目打不開的，記進待處理後不再算，B-415），另外把要跟著原檔搬的卡算出來，開場行改成「還有 N 份沒整理完、M 張卡要跟著搬」（0 的那半不印）；(2) 連結型條目（produce 登記的 claude.ai 線上文件）不切進批次、留給主對話（card-maker 沒有讀線上文件的工具，`convert` 也不處理 link 條目）：`plan` 在 `dispatch` 之外留一步 `cards`，只列 link 項、`read` 指 carding.md 一～六節；主對話讀不到時跑新指令 `kb cards unreadable <編號> --reason "線上文件打不開"`（只收 link 條目，其他回 exit 3「這個指令只收線上文件；檔案讀不到由 convert 記」），記進待處理 unreadable、之後不再算進開場數字；`plan.py` 的 `UNREADABLE_ADVICE` 加一句怎麼辦（到 claude.ai 確認那份還在、有分享給這個帳號，或跟我說先跳過）。確認時另記：主對話有沒有英文自言自語（a2 看到「Carding says to run `convert`.」，R-10）。
- **B-412**：開場有「還有 N 份沒整理完」（門檻內）時 AI 沒照 D-161 直接整理。開場指示照 D-196 改寫（見要做的事 8）；實作先用 `claude -p` 在測試知識庫重現（19 份沒整理、說「你好」跑 5 次），找出原因再改，改完同法再跑 5 次（驗收 14）。
- **跟密碼與守門、manage 第二版的交界**（a2 10-22 看過 spec 後提，兩題發行後都在 main）：
  1. **加密檔**：背景子代理不能問客戶，`convert` 一律不帶 `--password`、`--saved`；`dispatch` 切批時排除清冊或待處理已記成讀不到（含有密碼）的條目，留給主對話照 `plan`／`asks`（`unlock_same_kind` 等）問客戶。第一次遇到、還不知道有密碼的，子代理 `convert` 失敗照記讀不到、回報沒做成，下次 dispatch 就排除。檔名是憑證樣式的檔（含裁示「可以讀」的密碼檔，D-183、D-184）也不切進批次、留給主對話：它們的暫存是 `.system/temp/*.secret.md`，密碼與守門在每句話開頭與 Stop 時刪掉，子代理跑到一半可能被主對話那句的 Stop 刪掉（a2 10-23）。
  2. **密碼顯示過的回合（`secret_shown`）**：`refresh_changed` 與 `job_result` 照 `agent_turn` 找到的回合，回合檔記了 `secret_shown` 就不寫結果（跟 `stop.py` 同一條規矩），只更新改了。
  3. **守門的順序**：`updatedInput` 只加在 `scripts/kb/guard.py` 的判斷已經放行、而且是單一 kb 呼叫的指令上；排在密碼與守門加的 `onFailure: block` 與「shell 工具內部出錯也擋」之後；插進去的值照 `plan.py` 那套加引號（`shell_arg`）；PowerShell 裡有彎引號的 kb 指令照 manage 第二版擋下，不改寫。`is_single_plugin_call` 照它發行後的介面（多 `tool` 參數）。
  4. **plan 的順序**：manage 第二版排除時的兩步（`purge: "pending"` 先重寫綜合卡、再 `folders purge`）排最前面、不算門檻；`dispatch` 排在它們後面，而且不把 `purge: "pending"` 那夾的條目切進批次。
  5. **不讀的夾**：子代理用 Glob、Read 走訪來源會被守門擋（manage 第二版因此加了 `kb folders find`）；card-maker 的 `tools` 本來就沒有 Glob、Grep，清單一律由 `cards batch` 給，不自己找檔。
- **舊程式對照**：舊 `plugin/agents/card-maker.md` 的回報只給數量與檔名、不回報內容（照用：D-022），一次一個指令寫完整路徑（照用）；它自己改設定檔、試讀寫推測（不要：設定檔只經 kb，抽樣歸第 (5) 題）。

## 要做的事
1. **`agents/card-maker.md`（新）**：先讀 `plugin-dev:agent-development`。frontmatter：`name: card-maker`、`description`（≤ 600 字：只在 ingest 或 build 照 `kb cards dispatch` 給的 prompt 派；不回答客戶、不在知識庫外用）、`model: inherit`、`color: green`、`tools: ["Read","Write","Edit","Bash"]`、`background: true`。本文（≤ 3000 字，第二人稱）：When to invoke 三種情境；開始：照 prompt 跑 `cards batch <批次> --json`，先讀它 `read` 列的規則；三種工作各一段；不做的事（不跟客戶說話、不跑 `turn note`、不讀寫 `.system/`、`系統/`、`總目錄.md`、不碰原檔、被擋不繞路、打回改一次還不行就停那份）；回報格式（見介面）。
2. **`hooks/subagent.py`（新）**、`hooks/hooks.json`：先讀 `plugin-dev:hook-development`。`SubagentStart` 與 `SubagentStop`，matcher 都是 `^wiki-with-willy:card-maker$`，timeout 10；`start` 記 agents、回 additionalContext；`stop` 把批次改 done、`refresh_changed`。stdin 先 `reconfigure` UTF-8（B-093）、`sys.path` 照其他 hook；任何例外寫 hook-errors.log、exit 0。改完跑 `claude plugin validate` 與 `plugin-dev:plugin-validator`。
3. **`hooks/guard.py`**：釐清的回合歸屬第 3 點；`scripts/kb/guard.py` 的判斷不改。
4. **`hooks/turn.py`**：`NOT_A_PROMPT` 補兩種；說明改成 D-200 的停法。**`hooks/stop.py`**：`refresh_changed`、`job_result` 換一次。
5. **`scripts/kb/cli.py`**：全域 `--turn`、`--agent`。**`scripts/kb/_lib/turn.py`**：`current` 收覆寫的回合代號、`before_write` 收回合代號、`agent_turn(vault, session_id, agent_id)`；`turn_cli.py` 的 `note`、`approve` 收 `--turn`。**`scripts/kb/journal_cli.py`**：`in_conversation` 認 `--turn`。**`scripts/kb/_lib/journal.py`**：`refresh_changed(vault, turn_id)`。
6. **`scripts/kb/_lib/batches.py`（新）**：`batches.json` 的讀寫（鎖、寫前備份進回合、壞檔丟 `Unreadable` 帶檔名）、`claims`、`make_batches`、`materials`、`release`、`done_by_agent`、`expire`。**`scripts/kb/cards.py`**：子子命令 `dispatch`、`batch`、`status`、`stop`（介面見下）。`scripts/kb/_lib/cards.py` 只在 `materials` 要重用 todo 的項目組法時動（不改 todo 既有輸出）。
7. **`scripts/kb/_lib/plan.py`**：`cards`、`agenda_recheck`、`agenda_unscanned`、`unlinked`、`synthesis_broken`、`synthesis_stale`、`synthesis_new` 這幾步收成一步 `dispatch`（`do` 是 `kb cards dispatch --json`，`read` 是 null），`sync` 照留在前；多量先問那題的 `then` 是 `[sync, dispatch]`；`ANNOUNCE` 與先問題的句子照 D-199、D-200（見介面）；估時照釐清；模組說明的「按停止」改掉。
8. **`hooks/session.py`**：`INGEST_HINT` 照舊（見介面）；B-412 照釐清查、改，改句子不能超過開場預算。
9. **`config/defaults.json`**：`card_batch_size` 20、`card_parallel` 3、`synthesis_batch_size` 5、`card_batch_expire_minutes` 30、`card_seconds_per_file` 6、`card_batch_overhead_seconds` 30，各帶 `_why_`（引 D-197、D-199 與 spike 數字）；`ingest_auto_max` 的 `_why_` 改掉「按停止」、改成「派 card-maker」。
10. **`skills/ingest/`**：先讀 `plugin-dev:skill-development` 與 `skill-creator:skill-creator`，照 D-132 五條自查。`SKILL.md` 骨架改成：changes → todo → 有 `announce` 先講 → `sync` 照做 → `dispatch`：每個批次用 Agent 工具派（`subagent_type` 用輸出的 `agent`、`prompt` 原樣），派完一句告訴客戶、照常回答她這期間問的（用現有資料、末尾一句還在整理）→ 每收到一個子代理做完或停了的通知就再 `dispatch`（有新批次就派、有 `approval` 就問客戶）→ `done` 才 `todo --json` 拿 `asks`、`tell` 完整回答、跑 `note_cmd`；客戶說停 → `cards stop` → 逐個停 `agents`；問進度 → `cards status`。原則加三條（理由加編號）：製卡交給 card-maker、自己不讀原檔（D-022、D-196）；派工照程式給的 prompt、不自己拆批（D-197）；子代理的回報不轉述檔案內容（D-022）。描述不改。`references/commands.md`：加 `dispatch`、`batch`、`status`、`stop` 四節；todo 的 `new`、`stale`、`synthesis_*` 等欄位說明搬到 `carding.md`（主對話不再用、製卡者用），commands.md 只留一句指向；≤ 150 行。`references/carding.md`：開頭改成寫給 card-maker（仍寫「製卡者」）；加一節「批次」：`cards batch` 的輸出欄位、三種工作各讀哪幾節、回報格式；第九節「每寫完一張卡接著抽」；≤ 150 行。
11. **`README.md`**：「怎麼用」整理那段改成「交給背景的製卡助手、要停跟我說」；終端機指令清單加 `kb cards dispatch|batch|status|stop`。repo **`CLAUDE.md`** 地圖：加 `agents/card-maker.md`、`hooks/subagent.py`、`_lib/batches.py`，照改 `hooks/guard.py`、`turn.py`、`stop.py`、`session.py`、`cards.py`、`plan.py`、`_lib/turn.py`、`journal.py`、`cli.py`、`skills/ingest/` 那幾列；`agents/` 那列不再是「尚未」。
12. **測試**：`tests/test_lib_batches.py`（切批大小與並行、認領排除、過期、換 session 不認、`--source`、synthesis 等 cards 做完、大改動門檻回 approval、stop 放掉）、`tests/test_scripts_cards_dispatch.py`（四個子子命令黑箱、成功與拒絕句一字不差）、`tests/test_hooks_subagent.py`（start 的 additionalContext 含 core.md 全文與 kb 指令行、記 agents；stop 改 done 與 refresh；非 card-maker 的 agent_type 不動作；壞 stdin exit 0 寫 log）、`tests/test_hooks_guard.py` 加子代理案例（備份進 job 回合、`updatedInput` 插對位置、主對話不改、沒在 agents 的不改）、`tests/test_hooks_turn.py` 加 `<agent-message`、`[SYSTEM NOTIFICATION` 不開回合、`tests/test_hooks_stop.py` 加 refresh 與 `job_result` 換一次、`tests/test_lib_plan.py` 照新 plan 與句子改、`tests/test_budget.py` 量 card-maker 本文與 SubagentStart 注入長度（注入同開場預算）。`tests/test_skill_ingest.py` 照新骨架改。

## 來源程式對照
| 舊檔 | 怎麼用 |
|---|---|
| 舊 `plugin/agents/card-maker.md` | 回報只給數量與檔名、不回報內容（照用）；一次一個指令（照用）；自己改設定檔、試讀（不要） |
| spike 的 `card-maker-spike-plugin/agents/card-maker.md`、`hooks/spike_subagent_start.py`（在 llm-wiki-test，不在 repo） | frontmatter 與 SubagentStart 注入照這個寫法；回報第一行的暗號是 spike 用的，不要 |
| 本 repo `hooks/session.py` 的 core.md 注入 | SubagentStart 共用同一個組字函式 |
| 本 repo `_lib/turn.py` 的 `mark`、`_set` | job 回合欄位與 `job_result` 照這套寫 |

## 資料與介面格式

### `.system/batches.json`（schema 1）
```json
{"schema_version": 1,
 "jobs": [{"job": "j-1a2b3c4d", "session_id": "…", "turn": "20261022-201148-21e3", "created": "2026-10-22T20:11:50", "status": "open"}],
 "batches": [{"batch": "b-5e6f7a8b", "job": "j-1a2b3c4d", "kind": "cards", "items": ["f-…"], "source": null,
              "created": "2026-10-22T20:12:30", "agent_id": "a0204e72…", "status": "open"}],
 "agents": [{"agent_id": "a0204e72…", "session_id": "…", "turn": "20261022-201148-21e3", "started": "2026-10-22T20:12:32"}]}
```
`kind`：cards｜agenda｜synthesis；synthesis 的 `items` 是實體編號（`e-…`）或綜合卡路徑（broken）。`status`：job open｜closed；batch open｜done｜released。超過 `card_batch_expire_minutes` 的 open 批次讀到時當放掉（不改檔）。

### `kb cards dispatch [--source <別名>] [--json]`（主對話；終端機跑拒絕：「整理要在對話裡派」）
```json
{"job": "j-1a2b3c4d", "turn": "20261022-201148-21e3",
 "batches": [{"batch": "b-5e6f7a8b", "kind": "cards", "items": 20, "agent": "wiki-with-willy:card-maker",
              "prompt": "批次 b-5e6f7a8b：做卡 20 份。第一步跑 python \"<plugin 根>/scripts/kb.py\" cards batch b-5e6f7a8b --json，照它做，做完照你的說明回報。"}],
 "running": 2, "left": {"cards": 176, "agenda": 0, "synthesis": 6},
 "approval": null, "done": false, "note_cmd": null}
```
- 一次最多建到 `card_parallel` 個 open 批次；順序 cards → agenda → synthesis，前一種有剩或還在跑就不建後一種。
- `approval`（大改動）：`{"question":"這次要更新 14 張綜合卡，比平常多；要繼續嗎？","do":["kb turn approve --turn 20261022-201148-21e3"]}`。
- 什麼都不剩、也沒有在跑：`done` true、job 關掉、`note_cmd` 是 `kb turn note --turn <回合> "{做了什麼}"`。
- 人讀：`派了 3 批（做卡 20、20、20）；還在跑 0 批；還剩 做卡 176、綜合卡 6`；全做完 `整理完了`。

### `kb cards batch <批次> --json`（card-maker；只讀）
```json
{"batch": "b-5e6f7a8b", "kind": "cards", "prefix": "python \"<plugin 根>/scripts/kb.py\"",
 "read": ["<plugin 根>/skills/ingest/references/carding.md 一～七、九節"],
 "items": [{"id": "f-…", "type": "new", "…": "跟 todo 的 new／stale／broken／unlinked 同一組欄位"}],
 "profile": {"…": "todo 的 profile 塊"}, "entities": ["…todo 的 entities 塊"]}
```
synthesis 批次的 `items` 是 todo 的 `synthesis_new`／`synthesis_stale`／`synthesis_broken` 項，`read` 指 `rules/synthesis.md` 與 carding.md 第八節。批次不在、不是 open、或已過期：exit 3「這批已經停了或做完了」。帶 `--agent` 時把 agent_id 記上這個批次。

### `kb cards status [--json]`（只讀）
人讀：`正在整理：還在跑 2 批（做卡 20、做卡 16）；這次做好 58 份；還剩 做卡 120、綜合卡 6`；沒有 job：`現在沒有在整理；還有 N 份沒整理`。

### `kb cards stop [--json]`（主對話）
`{"agents": ["a0204e72…"], "done": 58, "left": 158}`；人讀：`已停：這次做好 58 份，還剩 158 份；這個對話不再自動整理，說「整理」再接著做`。沒有 job：`現在沒有在整理`（exit 0，照樣記號）。

### card-maker 的回報（最後一則，給主對話）
```
批次 b-5e6f7a8b：做好 18、沒做成 2
- f-1a2b3c4d：讀不到（有密碼）
- f-9e8d7c6b：檔頭打回兩次（範本版本該是 2）
被擋或打回：<原話一句，沒有寫「無」>
```
不寫檔案內容、摘要、人名、金額、日期（D-022）。

### 句子
- `ANNOUNCE`：`有 {n} 份要整理，大約{how_long}；要停的話跟我說一聲`
- 先問題：`有 {n} 份要整理，大約{how_long}；份數多，會用掉比平常多的額度。現在做嗎？`
- `INGEST_HINT`：照舊句不改（開場句只叫它走 ingest skill，怎麼派 card-maker 是 skill 本文的事；加字會超過 D-070 開場 400 字，實作疑義 10-23）。B-412 查出原因要改句子時，改完照樣過 `test_budget`。

## 可以動 / 不可以動
實作在 worktree：`git worktree add ..\llm-wiki-j-cardmaker -b spec/card-maker c3fdf63`（底是密碼與守門的最終版，見開工條件），之後全部在 `C:\Users\Jie\Desktop\llm-wiki-j-cardmaker` 做；commit 一律 `git -c user.name=…`，不要用 `git config`。

可以動：`agents/card-maker.md`（新）、`hooks/subagent.py`（新）、`hooks/hooks.json`、`hooks/guard.py`、`skills/note/references/preference.md`（只第 46 行「要停可以按停止」改成「要停跟我說一聲」，D-200）、`tests/test_skill_note.py`（跟著那一句）、`hooks/output_log.py`（只改取回合：payload 有 `agent_id` 且 `turn.agent_turn` 查得到就用它，否則照舊）、`hooks/turn.py`、`hooks/stop.py`、`hooks/session.py`（只 `INGEST_HINT` 與 B-412 那幾行）、`scripts/kb/cli.py`、`scripts/kb/cards.py`、`scripts/kb/turn_cli.py`、`scripts/kb/journal_cli.py`、`scripts/kb/_lib/batches.py`（新）、`scripts/kb/_lib/plan.py`、`scripts/kb/_lib/cards.py`（只為 `materials` 重用）、`scripts/kb/_lib/turn.py`、`scripts/kb/_lib/journal.py`、`config/defaults.json`、`skills/ingest/`、`README.md`、`CLAUDE.md`、`tests/`。

不可以動：`scripts/kb/guard.py`、`scripts/kb/_lib/cmdparse.py`、`scripts/kb/_lib/credentials.py`（守門判斷）、`rules/core.md`、其他 skill、`tests/evals/` 其他四份、`skeleton/`、`.claude-plugin/plugin.json`（版號發行時才動）。

## 驗收準則
`<ROOT>` 是 worktree；`<V>` 是從 `tests/fixtures/inventory/vault` 複製到 tmp 的知識庫，登記來源、放 45 份待製卡的檔（可用替身原檔）。指令前綴 `python "<ROOT>/scripts/kb.py"`。hook 用 stdin 餵 JSON 跑。

1. [ ] `cards todo --json` 的 `plan`：有新檔時是 `[sync?, dispatch]`；45 份時 `asks` 第一題是先問題（句子一字不差）、`then` 是 `[sync, dispatch]`；10 份時 `announce` 一字不差、秒數照 config 算。
2. [ ] `cards dispatch --json`（回合在對話裡）：45 份 → 3 批（20、20、5）、`prompt` 含 `cards batch <批次>` 與完整前綴；再跑一次 → 不建新批（都被認領）、`running` 3；終端機（current.json 沒 session_id）跑 → 拒絕一字不差。
2b. [ ] 交界：清冊記成讀不到（有密碼）的條目與憑證樣式檔名（含裁示可以讀的）不進批次；`purge: "pending"` 那夾的條目不進批次、`plan` 裡 purge 兩步排在 `dispatch` 前面；job 回合記了 `secret_shown` 時 `refresh_changed` 與 `job_result` 不寫結果。
3. [ ] 認領：把一批改 `done` 再 dispatch → 只從沒卡、沒認領的切；把批次 `created` 改成 31 分鐘前 → 當放掉、項目回來；換一個 session 的 current 跑 → 舊 session 的 open 批次不算。
4. [ ] 順序：還有 cards 時不建 synthesis 批次；cards 全做完（替身寫卡）後才建，大小照 `synthesis_batch_size`；`--source` 只切那個來源。
5. [ ] 大改動：job 回合的 `ai_synthesis` 已 9 張、這次 synthesis_stale 3 張 → 不派、回 `approval`（`--turn` 是 job 回合）；`turn approve --turn <回合>` 後再 dispatch → 派。
6. [ ] `cards batch <批次> --json`：欄位跟 todo 同一組、`read` 指對節、`--agent X` 記上 agent_id；released 或過期的批次 → exit 3 那句。
7. [ ] `hooks/subagent.py start`（stdin 帶 `agent_type: wiki-with-willy:card-maker`、`agent_id`、`session_id`）：輸出 `hookSpecificOutput.additionalContext` 含 core.md 全文與 kb 指令行；`batches.json` 的 `agents` 記上、`turn` 是 open job 的回合；`agent_type` 是別的 → 不輸出、不記。
8. [ ] 守門：帶那個 `agent_id` 的 Write 到 `檔案索引/` → 備份進 job 回合（客戶中途開了新回合也一樣）；帶它的單一 kb 呼叫 → 回 allow 加 `updatedInput.command`，`--turn <回合> --agent <id>` 插在 `kb.py"` 之後、其餘不變；主對話（沒有 agent_id）的同一指令 → 不改。
9. [ ] `kb.py --turn <回合> entity add …`：備份進那個回合、工作日誌不多程式段；`--turn` 不存在 → exit 3 那句。
10. [ ] `hooks/turn.py`：prompt 以 `<agent-message` 或 `[SYSTEM NOTIFICATION` 開頭 → 不開回合、日誌不多一段；`<task-notification` 照舊。
11. [ ] 日誌：派工那句 Stop 時「改了」是無；子代理寫 3 張卡後跑 `subagent.py stop` → 那句的「改了」有 3 張卡；job 關掉後主對話下一次 Stop → 那句的結果換成最後回答，再一次 Stop 不換。
12. [ ] `cards stop --json`：open 批次改 released、job 關、目前回合記 `ingest_stopped`、輸出 agents 與份數；之後同一 session 的 `cards todo` 開場指示不印（D-156 那套）。
13. [ ] 退回：一個 job 寫了 20 張卡、中途客戶開了新回合（只讀）→ `versions revert` 一次退掉 20 張（卡、總目錄、實體清單回來）。
13c. [ ] 連結型：todo 有一份 link 條目 → `dispatch` 不切它、`plan` 有一步 `cards` 只列它；`cards unreadable <它> --reason "線上文件打不開"` → 待處理多一筆、`tell` 是新那句、開場數字不再算它；對一般檔跑 → exit 3 那句。
13b. [ ] 開場數字：清冊有 2 份讀不到（一份有密碼、一份連結型 404）、3 份新、5 張卡要搬 → 開場行「還有 3 份沒整理完、5 張卡要跟著搬」；連結型那份的 `tell` 是新加的那句。
14. [ ] B-412：測試知識庫 19 份沒整理，`claude -p --plugin-dir <ROOT>` 說「你好」跑 5 次、第一句是一段 300 字的指示再跑 5 次，各至少 4 次開始整理（跑了 `cards dispatch`）；報告寫 5 次各自的開頭。
15. [ ] card-maker 本文與 SubagentStart 注入過 `test_budget`；`claude plugin validate` 通過、`plugin-dev:plugin-validator` 沒有錯。
16. [ ] 全部測試 `python -m unittest discover tests` exit 0（commit 之後跑，I-112）。

## 確認情境
**跟 build 的確認併在一起做**（D-133 10-22 補），在桌面版、plugin 只裝在測試知識庫（process.md 六的裝法：`--scope local`、做完 uninstall）；開始前使用者自己停用舊 `llm-wiki`。從「升級前就有資料」的知識庫開始（I-113）：先放一個已有卡、綜合卡、日程的知識庫，再加一個 60 份的新來源。build 那份 spec 的確認情境會引用下面這幾步。
1. 開新對話說「你好」→ 開場說有 60 份、先問；說「好」→ AI 講大約幾分鐘、交給製卡助手；工作清單看得到子代理（人眼）。
2. 整理中問「信義王的報價在哪？」→ 照常回答、末尾說還在整理。問「整理到哪了」→ 講做好幾份、還剩幾份。
3. 說「先停」→ 停下、講做好幾份剩幾份；再說「你好」→ 不自動整理；說「繼續整理」→ 接著做。
4. 做完 → AI 自己開口報結果、問新名字（最多 3 題）；確認名字後綜合卡寫好；工作日誌派工那句的「改了」有卡、「結果」是最後的回答，沒有多出 `<agent-message` 的段。
5. 說「剛剛那批整理得不對，全部退回」→ AI 問要重做還是不讀；答「重做」→ 列出這批的卡、確認、整批標記重做並交製卡助手重寫，這中間回答過的名字照舊（D-228，2026-10-24 改）。
6. 關掉視窗時子代理還在跑 → 開新對話，開場說還有幾份、門檻內直接接著做（B-412）。
7. 全程記：給客戶看的文字有沒有英文自言自語或內部編號；`hook-errors.log` 不存在；有沒有跳出「要允許嗎」。

報告存 `design/reports/card-maker-確認.md`（或併進 build 的確認報告，兩題各一節）。

## 不做
- build 的訪談、盤點、抽樣、交付報告（第 (5) 題）。
- 兩台電腦同時整理（例句 20）。
- 用量講得更準（B-108 剩下的）。
- 守門判斷本身的改動（密碼與守門已做）。
- 停止鈕在背景跑時的偵測（確認時測到了再說，D-200 重談條件）。

## 紀錄（設計 session 結案時填）
- 實作 commit：5aca4f2、e6b1fcd、5efaa41、811581c、445d113、60b909b、f6d75a8、f3ad435、87fc8d6、f15086e、f908066、ddf4b47、bcab389（分支 spec/card-maker），之後的修正在 spec/build
- 測試報告：design/reports/card-maker-測試.md（驗收 19 條加回測三輪；最後 2240 個綠，B-412 開口十次都先整理）
- 確認報告：design/reports/card-maker-確認.md（B 段 1、7 過，2～4 部分過，5 不過改 D-228，6 跳過；補驗 C 段 6 項都過）
- 發行版號：0.20.0（tag v0.20.0，card-maker 與 build 一起）
- 類似問題查核：I-116 查過 ingest 與 note 的名字確認、rules/synthesis.md S-02。教訓：子代理只拿到相對路徑會自己猜根，路徑一律給絕對的、守門擋寫到知識庫外；認領要不分對話，同時要處理關掉的視窗（SessionEnd、last_seen）；確認時「關掉視窗」要確認真的關了，第一個對話沒關造成兩邊同時派工。

## 討論進度
- 2026-10-22：使用者選先談 card-maker；列例句 20 句，待定範圍。
- 2026-10-22：使用者指出 card-maker 不是 skill、客戶的話不會叫到它；例句「去哪」改寫 ingest 或 build，另開「card-maker 怎麼被派」一節，邊界句標不在這題（使用者說好）。
- 2026-10-22：例句範圍定（使用者說好）：5、9 等 spike 結果再定收不收，13 收、誰寫放下一節，20 不收。
- 2026-10-22：查官方文件（子代理與 hook），寫 spike 任務檔九題加桌面版三步；文件沒寫死要實測的：SubagentStart 塞的規則子代理看不看得到、子代理裡 PostToolUse 打回回不回得去、背景子代理回報會不會觸發 UserPromptSubmit（會的話 turn hook 多開回合）、按停止與關視窗時子代理停不停。等 a2 排時間。
- 2026-10-22：spike CLI 部分跑完，報告收進 `design/reports/card-maker-spike.md`。要點：SubagentStart 加 matcher 能把 core.md 給 card-maker（B-032 有解）；守門、驗卡在子代理照跑；回合歸屬看寫的那一刻（客戶中途說話，之後的卡算進那句）；主對話派完回合就結束，子代理的 kb 呼叫被記成終端機、D-156 的停止偵測碰不到；I-035 在 CLI 成立；中斷靠卡檔判斷接得上；每份 12～18 秒（並行 3 個牆鐘 5～8 秒）；子代理每份拿清單要跑整份 todo、常被權限與守門誤擋；子代理不抽日程。桌面版三步待使用者做（要先加新來源夾）。
- 2026-10-22：a2 確認：守門誤擋三則收進密碼與守門（d4153e6）；`PYTHONIOENCODING=… python` 不符合 `Bash(python *)` 是權限規則，歸這題（子代理怎麼叫 kb、或 skeleton 的 allow 規則）；`hooks/turn.py` 可進這題的可動範圍（密碼與守門改 `_lib/turn.py`、`hooks/stop.py`，這題實作在它發行後，照順序不同時跑）。
- 2026-10-22：兩題實作測試分開、確認與 build 併在一起（D-133 補）；寫本 spec 前先做 spike 的桌面版三步。
- 2026-10-22：背景還是前景——使用者選背景，寫 D-195；按停止的細節等桌面版三步。舊 plugin（third party 的 `plugin`）有 `agents/card-maker.md` 可當來源對照；本 repo 還沒有，spike 那份只在測試夾的 plugin 副本。
- 2026-10-22：桌面版三步做完（報告桌面版一節）；B-412（開場還有 N 份沒整理完時沒照 D-161 直接整理）併進這題，跟 a2 談定：開場「先整理再回答」本來就要因 card-maker 重談，`hooks/session.py` 那幾行一起看。
- 2026-10-22：人眼補：桌面版工作清單有列出子代理；停止是打字說的（停止鈕在背景時的行為沒測）。製卡一律給 card-maker、先問門檻照留（使用者說好），寫 D-196，改 D-053、D-100 與架構原則 7。
- 2026-10-22：使用者問交給 card-maker 是不是就不用分批——說明分批的三個理由（空間、同時做、中斷損失小），客戶不用管、程式切；使用者說好，寫 D-197。
- 2026-10-22：日程與綜合卡誰做——使用者追問 ingest 做什麼（更正：分辨哪句歸誰是各 skill 描述的事，ingest 只在被叫錯時交回）、ingest 對綜合卡多知道什麼（查證後幾乎沒有，B 的好處是我講重了）；選 A，寫 D-198。（note 糾正後單張綜合卡：查 note 的 correction.md，note 只列待更新、下次整理時重寫，已在 D-198 範圍內，D-198 那句改掉。）
- 2026-10-22：例句 2 估算——選 A（只講時間、用量一句話），寫 D-199，B-108 改寫。
- 2026-10-22：停止——選 A，寫 D-200，改 D-156。I-116 grep：「按停止」在 `scripts/kb/_lib/plan.py` 的 `ANNOUNCE` 與模組說明、`hooks/turn.py` 說明、`scripts/kb/cards.py` 註解、`skills/ingest/references/commands.md` 的 announce 說明，寫 spec 時一起列進可以動。
- 2026-10-22：整理「本題釐清」六點（規則注入、權限、回合與日誌、一個或兩個子代理、舊程式對照、B-412）。
- 2026-10-22：寫完整 spec 草稿（使用者說寫 spec）。查官方文件補兩件：Bash 子行程沒有 session／agent 的環境變數，子代理身分只能從 hook 拿；PreToolUse 的 `updatedInput` 可改 Bash 指令並同時 allow。據此定回合歸屬的做法（job、agents 對照、守門插 `--turn`）、認領防重做、日誌補記、停、大改動、估時；一個子代理三種工作；例句 5、9 照 spike 改收，15 照 D-196 改收。I-116 grep：core.md、其他 skill 沒有相反的整理做法（answer 的 `unsorted`、note 的 `agenda scanned` 照用）。等使用者看過。
- 2026-10-22：a2 看過 spec，補「跟密碼與守門、manage 第二版的交界」五點（加密檔不進批次、secret_shown、守門順序與引號、plan 排在 purge 兩步後、不讀的夾不走訪）與驗收 2b。
- 2026-10-22：使用者說可以，狀態改 approved。
- 2026-10-22（approved 後）：a2 轉來 manage 第二版確認的開場三次（兩次照做、第一句是長指示那次沒照做）；B-415（連結型讀不到每次都算進還有 N 份）與開場沒算要搬的卡，併進 B-412 那條；驗收加 13b、14 多試長指示。範圍跟開場整理同一處，不另開題。
- 2026-10-23（approved 後）：a2 提憑證樣式檔（可以讀的密碼檔）的暫存會被 Stop 刪，補進交界第 1 點與驗收 2b：不切進批次、留給主對話；`hooks/turn.py` 以密碼與守門發行版為底（它加了刪 `.secret.md` 那幾行）。
- 2026-10-23：開工條件改（a2 轉、使用者向 22 確認「可以」）：實作先開工、底用 c3fdf63；測試與確認等密碼與守門發行。
- 2026-10-23（實作中疑義，22 定）：連結型條目不進批次、主對話照舊做；讀不到用新指令 `kb cards unreadable` 記（在 `cards.py`，不動 `convert.py`）；驗收加 13c。
- 2026-10-23（實作中疑義，22 定）：新 INGEST_HINT 讓開場最壞 416 字超過 D-070 的 400；選 (c) 開場句照舊、不提 card-maker，派工只寫在 ingest 本文。
- 2026-10-23（實作中疑義，22 定）：`hooks/output_log.py` 也照子代理的回合（總目錄、綜合卡快取、AI 產出登記的備份），加進可以動、只改取回合那一行；否則驗收 13 退不乾淨。
- 2026-10-23：實作回報（5aca4f2、e6b1fcd，commit 後全部測試 exit 0 由實作自報，22 另跑核對中）。疑義 13 條，22 定：1～9 照它的解讀（plan 的 cards 步也列憑證樣式檔與記過有密碼的、開場份數在記成讀不到前照算、approval 算全部未派的 stale、--source 時綜合卡只等範圍內、batches.json 備份但不算「改了」、`turn.start` 把舊回合欄位併進 versions 的 turn.json、dispatch 多 stopped、tell 講標題、只有 output_log 有取回合問題）；10 note 的 preference.md 第 46 行「按停止」加進可以動、照 D-200 改（I-116）；11～13 照記。`_lib/turn.py` 的改動跟密碼與守門確認後的修正（secret_shown 跨回合）同一支，合併時注意。
- 2026-10-24（22，桌面版確認 B 段）：第 5 步「剛剛那批全部退回」只退得到最近一句（名字那句），整批退不回。使用者先選「退到指定那句為止」，看過後改成「先問要重做還是不讀」，D-228 照改（`--to` 不做）；整批標記重做在 build 分支一起做，確認補驗第 5、6 步。
- 2026-10-24（22，B 段修後）：認領改成不分對話（兩個視窗不切同一批）；為了不讓關掉的視窗卡住新對話（B-412、確認第 6 步），加 SessionEnd 放掉那個對話的批次，另以批次的 last_seen 超過 `card_batch_idle_minutes`（10）當沒人在做、可重切；一般整理不加「接手整理」的說法。
