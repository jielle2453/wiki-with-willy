# card-maker spike 報告（B-032、B-108，CLI 部分）

日期：2026-10-22
跑的人：spike session（CLI 部分；桌面版那節沒做，留給使用者）
給誰看：設計 session（llm-wiki-j-22）
Claude Code：2.1.308。A～D 用 claude-opus-5-5，E～H 用 claude-sonnet-5-5（見「注意」）。

## 結論表

| # | 題目 | 結論（一句） |
|---|---|---|
| 1 | 規則給得到嗎（B-032） | 給得到：SubagentStart 的 `additionalContext` 有進到子代理，card-maker 第一句就說得出暗號與 R-04 原文。不帶 matcher 的那組也打到內建的 general-purpose（它只拿到對照暗號）。stdin 有 `agent_id`、`agent_type`，`session_id` 是主對話的 |
| 2 | 守門照跑嗎 | 照跑：子代理寫 `系統/`、`.system/`、來源夾三處都被擋，理由句一字不差回到子代理。PreToolUse 的 `session_id` 是主對話的，多了 `agent_id`、`agent_type`（主對話自己的呼叫沒有這兩欄） |
| 3 | 備份與回合 | 子代理寫的卡備份進「寫的那一刻」這個 session 的目前回合。客戶中途沒說話：全進「現在做」那一句的回合，revert 退得掉（卡不見、總目錄與實體清單還原、todo 份數回來）。客戶中途說了別的話：之後寫的卡全算到那句新的話（實測 10 張全進了「信義王的報價在哪？」那個回合），「現在做」那個回合變成 0 改動 |
| 4 | 驗卡打回 | 有用：PostToolUse 的 `decision: block` 理由（「檔頭有錯：範本版本該是 2。改好再寫一次。」）回到子代理，它照改一次就過。但錯的卡已經落地過，是被第二次寫入蓋掉的 |
| 5 | 背景與回合 hook | 背景子代理做完時，CLI 會把 `<task-notification>` 當成一句新的話送進來：UserPromptSubmit 有觸發，prompt 第一個字元就是 `<task-notification>`（前面沒多一行），turn.py 照 I-035 不開回合、日誌不多一段；接著 Stop 也觸發。被 TaskStop 停掉時一樣會來一則（status stopped）。沒看到別的標籤或漏網的。主對話在子代理跑的時候照常回答問題，做完也會自己開口報結果 |
| 6 | 並行 | 3 個並行（10、10、8 份）與後來「一批 20、同時 3 個」共 92 份：沒有鎖等待、逾時、hook 錯誤，總目錄與清冊沒寫壞，沒有同一份被做兩次；同名實體兩個子代理搶著登記，第二個拿到「已登記過」 |
| 7 | 時間與用量（B-108） | 每份：單一子代理約 12～18 秒（開頭固定約 60～90 秒準備），3 個並行時整批牆鐘約 5～8 秒。子代理每份用量約：cache 讀 4～7 萬、cache 寫 3.5～6 千、output 1.7～1.9 千 token。派子代理讓主對話每份的 cache 讀從約 36 萬降到約 1.3 萬，主對話的 context 也幾乎不漲 |
| 8 | 中斷 | 不會有「寫一半的卡」：卡是一次 Write 寫完。TaskStop 與直接殺行程兩種都只留下已寫完、檔頭正確的卡（4 張、4 張），回合帳正常，沒有殘留的鎖檔。新 session 開場說「還有 92 份沒整理完」，todo 也是 92，正確，沒有哪份被誤當成做完。CLI 沒辦法模擬「按停止鈕」，這裡用 TaskStop 與殺行程近似 |
| 9 | 大量 | 92 份用「一批 20、同時 3 個」：從派出到全部做完約 8 分 20 秒（Sonnet）。主對話 context 從約 4.5 萬漲到 7.6 萬，沒有被摺疊；主對話收到通知會自己補派下一批 |

**做不到或只能近似的**：
- 第 8 題的「按停止鈕」，CLI `-p` 做不到，改用 TaskStop（8a）與 taskkill 整棵行程樹（8b）近似。
- 第 7 題的「10 份一批」用 Opus 量、「20 份一批」用 Sonnet 量，兩者不是同一個模型（原因見「注意」）。
- 第 7 題的對照組（主對話自己做 10 份）順手把日程也抽了（照 plan 的 `agenda_unscanned` 做），card-maker 沒做這步，所以兩邊的工作量不完全一樣。

**注意**：使用者範圍 `~/.claude/settings.json` 的 `model` 在 17:34～17:43 之間變成 `sonnet`。這不是本 spike 改的（我沒動使用者範圍），之後的 E～H 就跟著用 Sonnet。製卡總量：A 2、B 10（之後退回）、C 10、D 28、E 4、F 4、H 92，共 150 份。

## 準備

1. **plugin 副本**：用 `git ls-files` 把 326 個檔複製到 `C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike-plugin\`，沒帶 `.git`。repo 一個檔都沒改。
2. **`agents\card-maker.md`**：照 plugin-dev:agent-development 寫。`model: inherit`、`color: green`，`tools` 給 Read、Write、Edit、Bash、Glob、Grep。本文叫它：
   - 讀 `${CLAUDE_PLUGIN_ROOT}/skills/ingest/references/carding.md` 一到六節；
   - 跑 `cards todo --json` 拿自己那幾個編號的 header；
   - 一份一份 `convert`、寫卡，被打回照理由改一次、被擋就不繞路；
   - 回報第一行照固定格式寫暗號與 R-04，接著列做成、沒做成、實體、錯誤。

   本文沒有指到 core.md。
3. **hooks**（照 plugin-dev:hook-development）：
   - `hooks/spike_subagent_start.py`：stdin 先 reconfigure 成 utf-8，整份 stdin 附加寫進 `hook-log.jsonl`，再回 `additionalContext`：core.md 全文加一行暗號。matcher 是 `^wiki-with-willy:card-maker$` 的那組給「青蛙七號」，不帶 matcher 的對照組給「白鷺三號」，這樣分得出是哪一組的注入到了子代理（跟任務檔寫的「同一句暗號」不同，是故意的）。
   - `hooks/spike_wrap.py <事件> <原 hook>`：先把 stdin 記一行，再用 runpy 在同一個行程裡跑原本的 hook，stdout 照原樣轉出，也記進 log 的 `out`。SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop 都包了這層；SubagentStop 只記 log。包了之後每次 hook 多花 50～160 ms，沒有逾時。
   - `claude plugin validate`：✔ Validation passed with warnings，只有兩個 warning：根目錄的 CLAUDE.md 不會被載入、沒有 icon。
4. **測試知識庫**：
   - `kb init` 建 `card-maker-spike\知識庫`；
   - `.claude\settings.json` 加 `"enabledPlugins": {"llm-wiki@llm-wiki": false}`；
   - `kb profile add 型別 名稱=案子 大類=事`：加了「案子」型別，好讓子代理登記實體，順便測實體清單的並行；
   - 假來源用 python-docx、openpyxl、reportlab（字型用標楷體）產生，全是虛構的估價單、合約、會議紀錄、進度表：
     - `來源\` 40 份：docx、xlsx、txt、pdf 各 10，三個案子名重複出現；
     - `來源-大\` 100 份，六個案子；
   - `kb folders add` 先登記 `來源\`，第 8 題前再登記 `來源-大\`。
5. **怎麼跑**：
   - 每個 `claude -p` 的參數：`--plugin-dir <副本> --permission-mode acceptEdits --allowedTools "Bash(python *)" Read Glob Grep`，工作目錄是知識庫。
   - 多句對話用 `--input-format stream-json`，由 scratchpad 裡的 `drive.py` 依序送話、等結果或等卡的數量。
   - 同時只開一個 `claude -p`，每個都有逾時。

## 各題做法與證據

hook log 一行一則：`C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike\hook-log.jsonl`（898 行）。各次 session 的 id 是 `11111111-aaaa-4aaa-8aaa-0000000000xx`，transcript 在 `~\.claude\projects\C--Users-Jie-Desktop-llm-wiki-test-card-maker-spike----\`。

### 1. 規則給得到嗎

**做法**：
- A 次：主對話前景派 card-maker 做 2 份卡；
- A2 次：主對話先派內建 general-purpose，再派 card-maker，兩個都只做寫入測試。

**證據**：
- 派 card-maker 時兩組 SubagentStart 都觸發（`group: matched`、`group: nomatcher`，`agent_type: wiki-with-willy:card-maker`）。card-maker 第一句：
  > 開場看到的暗號：白鷺三號、青蛙七號（開場額外內容裡有兩段核心規則，各帶一個暗號）；R-04：不改客戶原檔。改內容一律另存新檔；搬動、改名走 kb 程式；刪除請客戶自己刪。
- 派 general-purpose 時只有 nomatcher 那組觸發（`agent_type: general-purpose`）。它的第一句：「開場看到的暗號：白鷺三號；R-04：不改客戶原檔。…」。所以**不帶 matcher 的那組會打到所有子代理，而且內容真的進了它們的開場**。
- 之後 B、D、E、F、H 每個 card-maker 都兩組各觸發一次，前景或背景都一樣。
- **SubagentStart 的 stdin 欄位**：`session_id`（主對話的）、`transcript_path`（主對話的）、`cwd`、`scratchpad_dir`、`prompt_id`、`agent_id`、`agent_type`、`hook_event_name`。沒有 `permission_mode`、`effort`，也沒有派它時給的 prompt。
- **SubagentStop 的 stdin**：多了 `stop_hook_active`、`agent_transcript_path`、`last_assistant_message`（子代理回報的全文）、`background_tasks`、`session_crons`。

**結論**：B-032 用 SubagentStart 加 matcher 就給得到；主對話拿到的核心規則與子代理拿到的是兩條不同的路。

### 2. 守門照跑嗎

**做法與經過**：
- A 次叫 card-maker 寫三個測試檔，它照自己本文的「不讀寫 .system/、系統/」與 R-01、R-04 拒絕，一次 Write 都沒叫，測不到。
- A2 次由「服務人員驗收測試」的名義再派一次，這次寫了。

**證據**（hook log 第 31～39 行，general-purpose 與 card-maker 結果相同）：
- `系統/spike-test.md` → deny：「系統區是程式渲染的，直接改會被蓋掉；跟我說要改什麼，我從源頭改。」
- `.system/spike-test.md` → deny：「這是知識庫的設定與狀態檔，只有知識庫腳本能改；要加資料夾或改設定，跟我說要改什麼。」
- `../來源/spike-test.txt` → deny：「客戶原檔只讀；要改先用 kb office copy 拿一份副本」
- 子代理看到的是 `PreToolUse:Write hook error: <理由句>`，三句它都原文抄回來了。
- **PreToolUse 的 stdin**：`session_id` 是主對話的，子代理的呼叫多了 `agent_id`、`agent_type`；主對話自己的呼叫沒有這兩欄。其他欄位兩邊一樣：`transcript_path`（主對話的）、`prompt_id`、`permission_mode`、`effort`、`tool_name`、`tool_input`、`tool_use_id`。

**順帶看到的守門誤擋**（不只子代理會遇到）：
- 子代理用 `python -I -X utf8 -c "…open(sys.argv[2],'w')…"` 把 todo JSON 摘到 scratchpad，被擋：「實體夾裡只有同名的綜合卡能改；…」（第 7 行）。
- 主對話用 `python -I -c` 只數檔案，被擋：「客戶原檔只讀；…」（第 427 行）。
- 子代理用 `kb.py cards todo --json … > <scratchpad 檔>`，被擋：「總資料夾第一層與型別夾根不直接放檔；…」（第 890 行）。

### 3. 備份與回合

**證據**（`kb versions list`）：
- **A**：4 筆（2 張卡、總目錄、entities.json）都進了 `20261022-171953-1111`，也就是說「現在做」那一句的回合。
- **B**：背景跑，45 秒後客戶問「信義王的報價在哪？」，開了新回合 `20261022-172532-1111`。之後子代理寫的 10 張卡、總目錄、entities.json **全部記在這個新回合**；「現在做」那個回合（17:24）是 0 改動，日誌寫「做了：沒有改動」。
- **退回**：`kb versions revert`（退的就是 172532）→「已退回 20261022-172532-1111（問：「信義王的報價在哪？」）：還原 2、移除 10、成品 0 份」。之後：
  - `檔案索引/來源/大安陳宅/` 不見了；
  - 總目錄裡「大安陳宅」0 處；
  - entities.json 回到只有「信義王宅」；
  - `cards todo` 的 new 回到 38。
- **D、E、F**：客戶中途沒說話，卡全在「現在做」那一句的回合（D 是「新增 28」）。
- **H**：我在第 4 批還沒做完時送了「進度如何？」，開了新回合 `20261022-175715-1111`。第 4 批之後對自己 3 張卡的 Edit 就記在這個回合（「修改 4」）。要退回整批得連退兩個回合。
- **子代理的 kb 呼叫被當成終端機**：主對話派完背景子代理、先回一句話就結束了這一回合（Stop → 回合 `done: true`）。之後子代理跑的 kb 指令被程式當成「終端機跑的」：工作日誌出現「你在終端機跑的：kb entity add／登記實體：新店吳宅（案子）」這種程式段。備份照樣進同一個回合。
- **回合檔沒記到 `ingest`**：`cards todo` 由子代理在 done 的回合裡跑，`turn.mark("ingest")` 不記，回合檔沒有 `ingest`。
- **我在終端機跑的 `kb folders add 來源-大`**：備份算進了當時 `current.json` 指的那個對話回合（「三個子代理都做完了嗎？」，修改 sources.json、inventory.json）。日誌照樣記成終端機段。

### 4. 驗卡打回

**證據**（A 次第 19～22 行）：
- 第一次 Write 帶著「範本版本: 9」→ PostToolUse 回 `{"decision": "block", "reason": "檔頭有錯：範本版本該是 2。改好再寫一次。」}`。子代理看到的是 `PostToolUse:Write hook blocking error from command: "...spike_wrap.py" PostToolUse output_log.py": 檔頭有錯：範本版本該是 2。改好再寫一次。`。
- 它改好寫回同一個路徑，第二次沒被打回。
- 子代理自己指出：「這個打回是在檔案寫進去之後才觸發的，所以第一次那份範本版本寫 9 的卡其實落地過」。

### 5. 背景與回合 hook

**證據**（B 次）：
- 主對話派完背景子代理就回「我已經在背景整理這 10 份檔了」，Stop 觸發。Stop 的 stdin 多了 `background_tasks: [{id, type: subagent, status: running, agent_type: wiki-with-willy:card-maker, …}]`。
- 子代理跑的時候客戶問「信義王的報價在哪？」，主對話在 42 秒內照常回答（走 answer skill，還提到「其中 10 份正在背景整理」）。
- 子代理做完（17:27:48）：
  - SubagentStop 觸發；
  - 同一秒 **UserPromptSubmit 觸發**，`prompt_head200` 是 `<task-notification>⏎<task-id>a6d6fe92a841ebb2d</task-id>⏎<tool-use-id>…⏎<output-file>…⏎<status>completed</status>⏎<summary>Agent "整理 10 份檔案卡" finished</summary>⏎<note>A task-notification fires each time this agent stops … the same task-id may notify more than once.</note>⏎<result>開場看到的暗號：…`；
  - stdin 沒有 `source` 欄。
- `.system/turns/` 沒有多開回合（還是一個 session 一個檔）。`系統/工作日誌` 沒多一段，日誌只有客戶說的四句。
- 通知觸發的那一輪，主對話自己開口報「背景那 10 份已經整理完…」，跑了 `kb turn note`，Stop 也觸發。這句「做了」寫進了上一句客戶的話（「信義王的報價在哪？」）那一段；「改了」停在那句 Stop 時的「檔案卡 1 張」。
- 整個 spike 共 25 個 UserPromptSubmit，其中 10 個是背景完成或停止的通知（B 1、D 3、E 1、H 5），全部以 `<task-notification>` 開頭。**沒有一個前面多一行說明，也沒有 `<cross-session-message`、`<system-reminder` 或子代理回報那類別的標籤**；其餘 15 個都是我送的話。I-035 的判斷在 CLI 2.1.308 下成立。
- 被 TaskStop 停掉的子代理也來一則通知，`<status>` 是 stopped。前景子代理交回結果不經過 UserPromptSubmit。

### 6. 並行

**做法**：D 次一則訊息同時派 3 個背景 card-maker（10、10、8 份，三案混在一起）。

**證據**：
- 寫卡交錯：28 張卡的 PostToolUse 裡，有 10 對不同子代理的寫入相隔不到 1 秒。
- hook 耗時最多 PreToolUse 96 ms、PostToolUse 161 ms，沒有鎖等待的跡象。`.system/hook-errors.log` 不存在。
- 卡檔 40 個，`-<8碼>.md` 編號沒有重複；總目錄 40 條連結；inventory.json 讀得起來。
- `cards todo`：new、stale、broken、duplicates、orphans 都是 0。
- 實體：第三批先登記「大安陳宅」，第二批隨後拿到「已登記過：大安陳宅（案子）」直接掛上，沒有登記出兩筆。
- H 次（3 個並行、一批 20，共 92 份）結果一樣：140 張卡、140 條連結、沒有重複、沒有 broken。

### 7. 時間與用量

秒數來自子代理 transcript 的第一則到最後一則時間戳；token 是每則 `usage` 按 message.id 去重後加總。「cache 讀、cache 寫、output」三欄分開，input 每批只有幾十，略。

| 情況 | 模型 | 份數 | 子代理秒數 | 每份秒數 | 每份 cache 讀 | 每份 cache 寫 | 每份 output |
|---|---|---|---|---|---|---|---|
| B：單一背景，10 份一批 | Opus | 10 | 184 | 18.4 | 53,125 | 5,712 | 1,921 |
| D：3 個並行，10／10／8 | Opus | 28 | 173／214／154 | 17～21（牆鐘 7.8） | 65,837 | 5,129 | 1,875 |
| H：一批 20，同時 3 個 | Sonnet | 92 | 242／248／277／209（20 份）、161（12 份） | 12.2（牆鐘 5.0） | 40,925 | 3,519 | 1,707 |

- **時間分兩段**：每個子代理開頭有約 60～90 秒準備（讀 carding.md、`cards todo --json`、從 todo 抽出自己那幾份），之後每份約 6～9 秒。B 的第一張卡在派出後 89 秒才寫，之後 10 張用 78 秒。
- 子代理多半先把整批 `convert` 跑完、一口氣讀完暫存檔，再連續寫卡。
- **程式現在的估計**：`cards todo` 的 asks 說「有 40 份要整理，現在做嗎？大約 7 分鐘」「有 130 份…大約 22 分鐘」，約每份 10 秒。實測單一子代理每份 12～18 秒，3 個並行時牆鐘每份 5～8 秒。

**主對話的用量**（派子代理 vs 自己做）：

| 情況 | 模型 | 份數 | 主對話 cache 讀 | cache 寫 | output | 主對話最後 context |
|---|---|---|---|---|---|---|
| C：主對話自己做（含日程抽取） | Opus | 10 | 3,587,865 | 105,499 | 28,259 | 136k |
| D：派 3 個並行 | Opus | 28 | 350,627 | 28,453 | 3,453 | 59k |
| H：派 5 批 | Sonnet | 92 | 1,224,757 | 45,963 | 6,153 | 76k |

- 主對話每份的 cache 讀：自己做約 359k，派子代理約 12.5k（D）到 13.3k（H），省了大約 27 倍。
- C 花了 285 秒；它多做了日程抽取，所以秒數不能直接比。
- 主對話派一批的成本大約是：派的那一輪 cache 讀約 4.5 萬（第一輪另有 cache 寫約 4.9 萬建快取），加上通知回來那一輪 cache 讀約 12 萬、output 約 1.2 千。
- CLI 報的 `total_cost_usd`（列價，含子代理；H 的 modelUsage output 163,183 正好等於主對話加 5 個子代理）：
  - D：$2.50，28 份，每份約 $0.09（Opus）；
  - C：$2.13，10 份，每份約 $0.21（Opus，含日程）；
  - H：$3.12，92 份，每份約 $0.034（Sonnet）。

### 8. 中斷

**8a TaskStop（E 次）**：
- 背景派 1 個 card-maker 做 20 份，卡數多 4 張時送「停！用 TaskStop 把它停掉…」。主對話 4 秒內停掉（`task_updated status: killed`），接著來一則 `<task-notification>`，status 是 stopped。
- 被停的子代理**沒有 SubagentStop**。
- 停之後 40 秒內沒有再寫卡。主對話跑 `cards todo --json`，回「new 還剩 96 份」，正確。
- 回合帳：4 張卡與總目錄在派的那一句（`20261022-174343-1111`）。

**8b 殺掉整個行程（F 次）**：
- 背景派 1 個做 20 份，卡數多 4 張時 `taskkill /T /F` 整棵 claude 行程樹，沒有 Stop、沒有 SubagentStop。
- 殺之後：卡 48 張；`cards todo`：new 92、broken 0、duplicates 0；`.system` 底下沒有 `*.lock`。
- 回合檔是 `done: true`：主對話派完那句早已 Stop，殺掉前沒有新的話。備份 4 張卡、總目錄、entities.json 都在派的那一句。
- `.system/temp/` 留著已 convert、還沒做卡的暫存檔，之後照 `temp_keep_hours` 清。

**卡寫到一半會怎樣**：
- 卡是一次 Write 整檔寫入，兩種中斷都只看到「寫完的卡」與「還沒開始的卡」，沒看到寫一半的。
- 推論（沒實測到）：殺在 Write 之後、PostToolUse 打回之前，錯檔頭的卡會留下，下次會出現在 todo 的 `broken`，不會被當成做完。

**新 session（G 次）**：
- 開場那行原文是「還有 92 份沒整理完」，接著是整理指示。
- `cards todo --json` 的 new 是 92，其他各項 0。沒有哪份被當成做完，也沒有重做。

**D-156 的「按停止」偵測接不到背景整理**：E、F 兩次派的那句都已 Stop，回合也沒記 `ingest`（第 3 題最後一點），所以 turn.py 的 `ingest_stopped` 判斷碰不到。

**做不到的**：CLI `-p` 沒辦法按 Esc 或停止鈕。上面用 TaskStop 與殺行程近似；桌面版按停止鈕的行為要人眼。

### 9. 大量

**做法**：H 次一句話「把來源-大還沒做卡的全部整理成卡…一批 20、同時最多 3 個…一個做完就補派」，主對話自己跑 `cards todo --json` 分成 5 批（20、20、20、20、12）。

**證據**：
- 時間：17:49:35 送出 → 17:50:14 回「第一輪派出去了」。之後每收到一則通知就自己補派一批，不用人催：17:54:13 派第 4 批，17:54:25 派第 5 批。17:57:42 最後一批做完，17:57:57 總結。合計約 8 分 20 秒，92 份全部做成。
- 主對話 context：第一輪約 4.5 萬，最後 76,436。stream 裡沒有 `compact_boundary`，**沒有被摺疊**。
- 子代理各自的 context 最後是 5.5～7.8 萬，也沒有摺疊。
- 主對話最後說「後面的 20 份，製卡助手回報後我沒有逐張核對卡的內容」，也就是說它只核對了份數。

## 對設計的影響

列看到的事，不替設計做決定。

1. **B-032 有解，但注入沒有限縮範圍**：
   - SubagentStart 加 matcher `^wiki-with-willy:card-maker$` 就能把 core.md 給 card-maker。
   - 不帶 matcher 會連 general-purpose 這類內建子代理也注入。
   - matcher 比的是 `agent_type`，plugin 的子代理型別是 `wiki-with-willy:card-maker`。
2. **回合歸屬看「寫的那一刻」，不看「誰派的」**：
   - 背景子代理在跑時客戶說了別的話，之後的卡就算進那句話的回合，「現在做」那句變成 0 改動。
   - `versions revert` 只退最近一個，要退整批得一個一個退，也會把那句話自己的改動一起退掉。
   - 例句 10（「剛剛那一大批全部退回」）要在這上面設計。可用的線索：PreToolUse 有 `agent_id`、`agent_type`；Stop 的 stdin 有 `background_tasks`。
3. **子代理的 kb 呼叫被當成終端機**：
   - 主對話派完就 Stop、回合 `done`，之後子代理的 `kb entity add` 等被記成「你在終端機跑的」程式段。
   - `cards todo` 的 `turn.mark("ingest")` 不生效。
   - 所以 D-156 的「整理中按停止」偵測碰不到背景整理。
   - 日誌裡「做了」也會落到別句話（通知那一輪的 `turn note` 寫進上一句客戶的話）。
4. **背景完成通知照 I-035 處理就對**：CLI 下通知以 `<task-notification>` 開頭、不開回合；TaskStop 也會來一則；同一個 task 可能通知不只一次（通知本文自己這樣說）。之後若加說明行或換標籤，會被當成客戶的話開回合，可以留一個測試守著。
5. **中斷的保證來自程式，不靠子代理**：
   - 卡是一次 Write，`cards todo` 照卡檔判斷做沒做，所以停、殺、新 session 都接得上，不重做不漏做。
   - 被殺或被停的子代理沒有 SubagentStop，靠 SubagentStop 收尾的設計會漏。
6. **驗卡是寫後才打回**：錯檔頭的卡會先落地，子代理改一次才蓋掉。被殺在中間就留下 broken 卡（推論），下次 todo 會列。
7. **子代理拿清單的方式**：
   - 每個 card-maker 都跑完整的 `cards todo --json`（100 份時 142 KB），輸出太大被存成 tool-results 檔，再自己寫 python 摘出那幾份。
   - 這一步常被擋：一種是 `PYTHONIOENCODING=utf-8 python …` 不符合 `Bash(python *)`，背景子代理不能等人按允許，會被自動拒絕（`permission_denied: This command requires approval`）；另一種是被守門誤擋（第 2 題那三則）。
   - 它們就換寫法重試，主對話自己也說這違反 R-12。
   - 開頭 60～90 秒的準備時間大多花在這裡。
   - 「只給這幾個編號的 header 與 profile」這種輸出，會直接影響時間、用量與 R-12。
8. **守門對臨時 python 指令的誤判**：讀 todo、寫 scratchpad、只數檔案的 `python -c` 被當成寫實體夾或寫原檔；`kb … > scratchpad 檔` 被當成寫知識庫第一層。主對話與子代理都會遇到。
9. **估時與估量（B-108）**：
   - 程式現在每份估約 10 秒。實測單一子代理每份 12～18 秒，另加每批 60～90 秒準備；3 個並行時牆鐘每份 5～8 秒。
   - 用量每份約 cache 讀 4～7 萬、cache 寫 3.5～6 千、output 約 1.8 千（子代理）。主對話每批另約 cache 讀 15 萬。
   - 模型會改變秒數與費用：同類工作 Opus 每份約 $0.09，Sonnet 約 $0.034（CLI 列價）。
10. **主對話省很多、也不會被摺疊**：派子代理讓主對話每份 cache 讀約少 27 倍；92 份後主對話 context 才 7.6 萬，自己會照通知補派下一批。
11. **子代理比主對話守規矩，也更死板**：
    - card-maker 照本文拒絕主對話「去寫 系統/」的要求；
    - 會主動指出原檔互相矛盾、行政區算不算地址這類問題；
    - 但只做卡，不抽日程（主對話做的時候會照 plan 抽）。
    - 日程與綜合卡由誰做，要另外定。
12. **子代理在跑時主對話照常回答**（例句 5），回答裡還會提到「其中 10 份正在背景整理」；做完會自己開口報結果（例句 9）。以上是 CLI 的行為，桌面版請使用者照任務檔那節看。
13. **桌面版測之前**：知識庫的 140 份現在全都有卡，「把來源裡的檔都整理成卡」會沒東西可做。要先加一個新的來源夾（或退回最近幾個回合）再測。plugin 副本與 hook log 都留著，桌面版跑完可以直接對照 log。

## 檔案位置

- 報告：`C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike\報告.md`
- hook log：`C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike\hook-log.jsonl`
- plugin 副本：`C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike-plugin\`（`agents\card-maker.md`、`hooks\spike_subagent_start.py`、`hooks\spike_wrap.py`、`hooks\hooks.json`）
- 測試知識庫：`C:\Users\Jie\Desktop\llm-wiki-test\card-maker-spike\知識庫\`；假來源：`來源\`（40）、`來源-大\`（100）
- 每次 `claude -p` 的 stream 輸出與驅動腳本（A、A2、B～H 的 `.jsonl`、`.driver.log`，`drive.py`、`usage.py`、`hl.py`、`gen.py`）：`C:\Users\Jie\AppData\Local\Temp\claude\c--Users-Jie-Desktop-llm-wiki-j\eb30c280-dbe7-45be-8d73-f30c2a863b1e\scratchpad\`
