# manage：確認報告

日期：2026-10-16
plugin：`--plugin-dir C:\Users\Jie\Desktop\llm-wiki-j-manage`（worktree，HEAD 759a353）
地點：`C:\Users\Jie\Desktop\llm-wiki-test\manage-check\`，來源 `manage-check\來源\`（從 來源.zip 還原，44 份）
`<W>` = `C:\Users\Jie\Desktop\llm-wiki-j-manage`，`<K>` = `manage-check\知識庫`

前置：Claude Code 2.1.301；Python 3.14.7。
**舊 llm-wiki plugin 沒有停用**：`claude plugin list` 顯示 `llm-wiki@llm-wiki` Scope user、✔ enabled。照 llm-wiki-j-18 指示不自己改、已回報；確認照常進行。逐字紀錄裡舊 plugin 沒有 hook 跑（SessionStart 只有 superpowers 的 `run-hook.cmd` 與本 plugin 的 `session.py`），它的 skill 有列在清單裡但全程沒被叫，路由沒受影響。
環境另外啟用 superpowers（SessionStart 注入一大段英文），不是乾淨的客戶環境。
知識庫 `.claude\settings.json` 只有 `permissions.deny: []`；使用者層 settings、`~/.claude/CLAUDE.md`、`.claude.json` 都沒有語言設定。

## 準備（確認 session 做，絕對路徑、沒 cd 進知識庫）

1. `manage-check` 原本是空夾。`來源.zip` 的檔名帶 UTF-8 旗標（0x800），用 Python `zipfile.extractall` 解到 `manage-check\來源`，44 份；四個夾：公司行政（舊檔、薪資、請款）、報價與合約、客戶案件、廠商資料。
2. `python "<W>\scripts\kb.py" init "<K>"` → `已建立知識庫 …\知識庫：建了 11 項`，exit 0。jsonl 一段 `program` `init`。
3. 沒有登記來源：`folders list --json --vault "<K>"` → `{"sources": []}`。

## 客戶視角（對話開在 `知識庫\`，使用者親手打）

對話：cwd `<K>`，`claude --plugin-dir "<W>"`，session `66978cf4-d776-465f-b8f3-05926e136f96`。除了使用者貼回的畫面，也讀了逐字紀錄 `~\.claude\projects\C--Users-Jie-Desktop-llm-wiki-test-manage-check----\66978cf4-….jsonl` 來確認每個回合有沒有叫工具。

**跨步驟落差（R-10）：AI 全程用英文回答**（客戶打的全是中文）。jsonl 的 `result` 也是英文，不是貼的時候被轉掉的。逐字紀錄裡 SessionStart `session.py` hook_success，`additionalContext` 有開場段與 core.md 全文（含 R-10「給客戶看的全是繁體中文」），也進了 system 訊息。規則有送到，模型沒照做。程式印的句子（中文）在回答裡只是引用。原因沒查到；可能有關的是 Claude Code 系統提示與 superpowers 注入都是英文、SKILL.md 沒寫回答語言。下面各步判「過」都只看行為，語言另計。

### 第 1 步：人眼 `/skills`
- 使用者回報 `/skills` 裡看得到 `wiki-with-willy:manage`。
- 判：**過（人眼）**（B-013）。

### 第 2 步：「你現在讀哪些資料夾」
- `Skill(wiki-with-willy:manage)` → `Bash(python "C:/Users/Jie/Desktop/llm-wiki-j-manage/scripts/kb.py" folders list)` → `還沒有登記任何來源`。回答：沒登記任何來源，現在不讀任何夾；要加的話從檔案總管位址列複製路徑貼過來，會先檢查份數和像密碼金鑰的檔，再跟你確認。
- `${CLAUDE_PLUGIN_ROOT}` 有代換成 worktree 絕對路徑，沒找路徑。
- jsonl：`20261016-193010-6697`，沒有改動、`note_by` program。
- 判：**過**。

### 第 3 步：「把客戶案件那個資料夾加進來」（沒給路徑）
- 逐字紀錄裡這一回合 **0 個工具呼叫**（3 秒）。回答：沒有完整路徑加不了，不想猜是哪個夾；請從檔案總管位址列複製路徑（舉例 `D:\資料\客戶案件`）。
- jsonl：`20261016-193053-6697`，沒有改動。
- 判：**過**：沒猜路徑、沒自己找。

### 第 4 步：貼 `…\manage-check\來源\客戶案件`，再回「好」
- 貼路徑：`Read(<W>\skills\manage\references\sources.md)` → `folders add "…\來源\客戶案件" --check` → `會登記「客戶案件」（../來源/客戶案件，15 份）`。回答：還沒加；15 份、沒有像密碼金鑰的檔名；別名「客戶案件」可以嗎。
- 回「好」：`folders add "…\來源\客戶案件"` → `已登記「客戶案件」（../來源/客戶案件，15 份）` → `turn note "新增讀取資料夾「客戶案件」（15 份檔）"` → `記下了`。回答：從現在起讀得到，還不會整理成卡、之後補；已記進工作日誌。
- `list --json`：多 `src-6670` 客戶案件，`exists: true`，`excludes: []`。`.system\versions\20261016-193448-6697\` 出現。
- jsonl：`--check` 那回合沒有改動；「好」那回合 `note` 是那句、`note_by` **ai**、`changed` `.system/sources.json`。沒有 `folders add` 的程式段（D-085）。
- 判：**過**。
- 旁記：`--check` 那回合 jsonl 的 `result` 是「…none of their names look like password███」。`mask_secrets` 從 password 遮到子句尾是設計如此，但英文回答裡沒有中文句讀，後半段整段被吃掉。是因為 R-10 落差才看到的。

### 第 5 步：「不要讀公司行政裡的薪資」
腳本順序因 AI 的第一句回答調整過：先回「好」，再回「1」，再貼路徑，最後才加公司行政。
- **第一句（落差）**：逐字紀錄 0 個工具呼叫，AI 直接問①不想讓 AI 看到（「之前讀過的已送過雲端」有講）②只是用不到。**沒發現公司行政沒登記**，沒說「本來就沒在讀」，也沒問要不要先加。原因：SKILL.md「不讀某夾」那列的順序是先問①②，再 `exclude`，程式拒絕了才講沒在讀；「路徑」段雖寫了「客戶只講夾名時跑 `list --json`」，但沒要求在問①②之前先查登記。spec 確認情境 5 期望先講沒登記，SKILL.md 沒把這個寫進去。
- 回「好」：「"好" doesn't tell me which one, so I haven't done anything yet」，再列 1／2。0 個工具呼叫，jsonl 沒有改動。**過**（特別記的那點：再問一次、沒做）。
- 回「1」（**落差：猜路徑**）：`folders list --json`（只有客戶案件）→ AI 自己把路徑接成 `…\來源\客戶案件\公司行政\薪資`，跑 `exclude … --kind hidden`。SKILL 路徑段寫「接不出來或有兩個可能就請客戶貼完整路徑」，公司行政不在清單裡，這是硬接。程式 exit 3：`找不到 …\客戶案件\公司行政\薪資；排除的對象要真的存在`。AI 沒換方法、沒繞路（R-12 守住），說是自己假設錯了，請客戶貼薪資完整路徑；這一句還是沒講公司行政沒在讀。無變動。
  - 程式面：`_exclude` 先查路徑存在、才查在不在登記來源，所以猜錯的路徑拿到的是「找不到」，不是「不在任何登記的來源裡」。不算錯。
- 貼 `…\來源\公司行政\薪資`：複述一句 → `exclude … --kind hidden` → exit 3 `…\薪資 不在任何登記的來源裡；先用 folders add 登記`。回答：薪資現在本來就沒在讀，因為公司行政沒加；要不要先加公司行政、再把薪資設成不想讓 AI 看到。符合 `references/sources.md` 常見拒絕那列。**過**。
- 回「加」：AI 用客戶貼的薪資路徑的上一層當公司行政路徑（SKILL「`add` 的路徑一律請客戶貼」；這是從客戶貼的路徑推出來的，不是亂猜，記輕微），`add "…\來源\公司行政" --check` → `會登記「公司行政」（../來源/公司行政，16 份）`；報 16 份、沒有憑證樣式檔、別名可不可以；說加完接著排除薪資。
- 回「好」：`add` → `已登記「公司行政」（../來源/公司行政，16 份）`；`exclude "…\公司行政\薪資" --kind hidden` → `已排除「公司行政/薪資」（不想讓 AI 看到）。之前讀過的已送過雲端；這版還沒有卡和備份要清。`；`turn note "新增讀取資料夾「公司行政」，並把其中「薪資」設為不想讓 AI 看到"`（一次）。
  - `list --json`：`src-9bea` 公司行政，`excludes` 有 `{"spec": "薪資", "kind": "hidden", "reason": ""}`。
  - jsonl：`20261016-194400-6697` `note_by` ai、`changed` `.system/sources.json`。
  - 說法（輕）：沒轉述「之前讀過的已送過雲端」，改說公司行政之前沒加，所以沒送過雲端（這個情況下講的是對的）；說「16 份從現在起讀得到，薪資除外」，但 16 份裡有 5 份在薪資；加完沒講「整理成卡的功能之後補」。
- 判：**部分過**。重點項目（「好」再問不做、`--kind hidden`、程式那句、list 有 hidden）都過；「先講沒登記、問要不要加」晚了兩輪才出現，中間還猜了一次路徑。

### 第 6 步：「薪資那夾還是讀好了」
- 回答先複述一句（講出原本是「不想讓 AI 看到」），`include "…\公司行政\薪資"` → `已取消排除「公司行政/薪資」，從現在起讀得到。` → `turn note`。另外提醒讀到的內容會送雲端。
- `list --json`：公司行政 `excludes: []`。jsonl `20261016-194504-6697` `note_by` ai。
- 判：**過**。

### 第 7 步：「客戶案件不要了」→「以後不讀」→「確定」
- 第一句：0 個工具呼叫，問是以後不讀（拿掉登記、檔案不動），還是要刪檔（做不到，請自己在檔案總管刪）。
- 「以後不讀」：「Are you sure you want to remove 「客戶案件」? The files won't be touched.」請回「對，拿掉」或「確定」；**沒做**（jsonl 沒有改動）。
- 使用者回的是「確定」（不是腳本的「對，拿掉」；SKILL 把「確定」列為明確確認）：`remove "客戶案件"` → `已拿掉來源「客戶案件」；你的檔案沒動，從現在起不讀它；說「退回上一版」救得回。` → `turn note`。回答講了檔案沒動、可以說「退回上一版」。
- `list --json`：只剩公司行政；`來源\客戶案件` 三個子夾都在；`versions\20261016-194701-6697\` 有備份。jsonl `note_by` ai。
- 判：**過**（特別記的那點：「以後不讀」之後有再確認才 remove）。

### 第 8 步：「幫我把信義王的報價搬到王宅那夾」
- 回答：這版不會搬檔或改名，請在檔案總管拖曳或剪下貼上到王宅；如果是整個夾搬家或改名，做完說一聲，會拿掉舊的、加新的。
- 逐字紀錄這一回合 **0 個工具呼叫**；`<K>\.system\` 沒有 `ledger.tsv`（全程守門沒有 deny）；jsonl 沒有改動；來源仍 44 份。
- 判：**過**（特別記的那點：沒跑任何寫入指令、沒有 deny）。

### 第 9 步：「公司行政那夾我搬到 E 槽了」
- 回答：做法是拿掉舊的「公司行政」、加 E 槽的新位置，別名照舊、檔案不動，原本排除的會在新位置補回（現在沒有）；要不要現在做，要的話請貼 E 槽新路徑。0 個工具呼叫。
- 判：**過**：沒猜新路徑。

### 第 10 步：「退回上一版」→「確定退回」
- 第一句：`versions --help` → `versions list`（4 個回合，最新 `20261016-194701-6697` 問：「確定」 modify sources.json）。回答：還沒退；最近一次是 19:47 拿掉客戶案件，退了會再讀客戶案件；E 槽搬家沒動過、不受影響；請回「確定退回」。（R-05 先列出再確認。）
- 「確定退回」：`versions revert` → `已退回 20261016-194701-6697（問：「確定」）：還原 1、移除 0、成品 0 份` → `folders list`（客戶案件、公司行政都在）→ `turn note "退回上一版：恢復讀取「客戶案件」"`。另外指出公司行政還登記在舊位置、舊夾也還在，可能是複製不是搬，請貼 E 槽路徑。
- `list --json`：`src-6670` 客戶案件回來了（id 相同）、公司行政不變。
- 判：**過**（D-074 備份對 remove 有效）。

### 第 11 步：jsonl 佐證
`<K>\.system\journal\2026-10.jsonl` 共 20 行：1 段 `program`（init）、19 段 turn。改了登記的回合：

| 回合 | 問 | note | note_by | changed |
|---|---|---|---|---|
| 20261016-193448-6697 | 好（第 4 步） | 新增讀取資料夾「客戶案件」（15 份檔） | ai | .system/sources.json |
| 20261016-194400-6697 | 好（第 5 步） | 新增讀取資料夾「公司行政」，並把其中「薪資」設為不想讓 AI 看到 | ai | .system/sources.json |
| 20261016-194504-6697 | 薪資那夾還是讀好了 | 取消排除「公司行政/薪資」，恢復讀取 | ai | .system/sources.json |
| 20261016-194701-6697 | 確定（第 7 步） | 拿掉來源「客戶案件」，以後不讀（檔案沒動） | ai | .system/sources.json |
| 20261016-195023-6697 | 確定退回（第 10 步） | 退回上一版：恢復讀取「客戶案件」 | ai | .system/sources.json |

其餘 14 段都是「沒有改動」、`note_by` program。對話裡叫的 `folders add／exclude／include／remove`、`versions revert` 都沒有程式段。`hook-errors.log` 不存在。
- 判：**過**（第 4～7 步各一段 ai、沒有程式段，D-085）。

## 結論

**判：大致過，有兩個落差要交回。** 11 步裡 10 步過（第 1 步人眼），第 5 步部分過；另外全程違反 R-10。

特別記的四點都過：第 3 步沒猜、沒找；第 5 步回「好」有再問、沒做；第 7 步「以後不讀」之後有再確認才 remove；第 8 步 0 個工具呼叫、沒有 deny 紀錄。

落差：
1. **R-10：全程用英文回答。**core.md 有帶進 system 訊息，使用者環境也沒有語言設定。這是模型行為，程式沒錯。客戶看到的是英文，影響很大。建議設計 session 決定要不要在 SKILL.md（或知識庫 CLAUDE.md）加一句「用繁體中文回答客戶」，再找一個乾淨環境（不裝 superpowers）重看一次，判斷是不是環境造成的。
2. **第 5 步：「不讀某夾」沒先查登記，還猜了一次路徑。**SKILL.md 那列的順序是先問①②，再 exclude，程式拒絕才講沒在讀；確認情境 5 期望先講「本來就沒在讀、要先加嗎」。另外 AI 把未登記的「公司行政」硬接到「客戶案件」底下跑 exclude，違反 SKILL 路徑段「接不出來就請客戶貼」。結果無害（程式擋下、AI 沒繞路），但多問了兩輪、還猜了路徑。建議 SKILL.md「不讀某夾」那列開頭加一步：先 `list --json`，夾不在任何來源裡就直接說本來就沒在讀、問要不要先加，不先問①②、不硬接路徑。

觀察（不判不過）：
- 第 5 步 AI 用客戶貼的薪資路徑的上一層當公司行政路徑，沒請客戶另貼（推導，不是猜）。
- 第 5 步轉述：沒照轉「之前讀過的已送過雲端」（改講的反而對）；「16 份讀得到、薪資除外」數字不對（實際 11 份）；加完沒講「整理成卡之後補」（第 4 步有講）。
- 第 4 步 jsonl `result` 被 `mask_secrets` 從 password 遮到尾，英文回答才會這樣（R-10 的副作用）。
- 第 7 步使用者回的是「確定」不是「對，拿掉」，SKILL 兩個都認，不影響判定。
- 第 10 步 AI 主動指出公司行政舊夾還在、可能是複製不是搬；這是額外觀察，合理。

確認方說明：全程沒 cd 進知識庫，佐證都用絕對路徑跑 `folders list --json --vault`、讀 `.system\journal\2026-10.jsonl`、看 `.system\` 有沒有 `ledger.tsv` 與 `hook-errors.log`，另外讀對話逐字紀錄。確認 session 自己沒有對 `<K>` 跑任何寫入指令（除了準備的 `init`）。

收尾待辦：舊 `llm-wiki@llm-wiki` 本來就是 enabled，這次沒動，不用 enable 回來。`manage-check\` 留著。
