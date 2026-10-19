# 整理 ingest 測試報告

日期：2026-10-20
給誰看：設計 session（llm-wiki-j-ea），決定這題能不能進確認與發行。
驗證對象：分支 spec/整理ingest 的 988960b（worktree `C:\Users\Jie\Desktop\llm-wiki-j-整理ingest`），對照 main 的 faca9f5（v0.11.0）。spec 讀主 checkout 那份（含裁定 1～4）。
工作方式：只驗證，不改程式、不 commit。

## 開工確認
- `skill-creator:skill-creator`、`plugin-dev:skill-development`、`code-review`、agent `plugin-dev:skill-reviewer` 都載得到，沒有走後備路徑。superpowers 沒用。
- 驗收 1～6 的資料全部用複本：fixture 知識庫（`tests/_entities.make_entity_vault`）與 `ingest-check` 複本，v0.11.0 的程式用 `git archive faca9f5` 解到暫存夾跑。原本的 `ingest-check` 沒動。
- 工作區：`C:\Users\Jie\Desktop\llm-wiki-test\整理ingest-對照\`（`prep.py` 造情境、`mktasks.py` 出題、`collect.py` 收結果、`snap.py` 比檔案變動、`skill-snapshot/` 舊版快照）。

## 結果總表

| # | 驗收 | 結果 |
|---|---|---|
| 1 | `todo --json` 多三鍵、其餘逐字相同、人讀輸出相同 | 過 |
| 2 | `plan` 步驟與順序 | 過 |
| 3 | `asks` 順序、連號、strict、fill、引號 | 過 |
| 4 | 多量先問 | 過 |
| 5 | `tell` | 過 |
| 6 | `do` 逐條實跑 | 過 |
| 7 | SKILL 本文、frontmatter、禁詞、行數、補丁表 | 過 |
| 8 | 新舊版對照五情境 | 過，新版沒有退步（見各情境的小差異） |
| 9 | skill-reviewer 無 major | 過（0 major、9 minor） |
| 10 | 全部測試綠 | 過（1212 測試、exit 0） |

十條都過，沒有「不過」。code-review high 另外提出 9 條，沒有一條擋驗收，但有兩條建議在發行前處理（見「code-review」）。

## 驗收 1～7、10 的證據

### 1 三鍵與既有輸出不變
`cmp.py`：同一個知識庫先跑 v0.11.0 再跑 worktree，各跑 `cards todo --json` 與 `cards todo`。四個不同狀態的知識庫（fixture 有 12 份新檔；`ingest-check` 複本；同一份 sync 後；再加新檔與改綜合卡後），輸出一樣：

```
exit 0 0 0 0
extra keys ['asks', 'plan', 'tell'] missing []
json equal after dropping 3 keys: True
key order same prefix: True
human output byte-identical: True
```

### 2 plan
- 只有 `moved`：`ingest-check` 複本（moved 36）→ `plan` 是 `[{"step":"sync","items":36,"do":"kb cards sync","read":null}]`，沒有別的步驟。
- `new` 兩份加 `synthesis_stale` 一份（複本加兩個新檔、拿掉 `王宅.md` 的 `整理到` 一行；因為這個複本掃描後 moved 又出現，所以前面多一步 sync）：`sync(36)` → `cards(2, read="references/carding.md 一～六節")` → `synthesis_stale(1, read="${CLAUDE_PLUGIN_ROOT}/rules/synthesis.md")`，順序與 `read` 都對。
- 全空：sync 之後 `plan` 是 `[]`（`asks` 仍有兩題，與 plan 無關）。

### 3 asks
複本改名 `案子/王宅` → `王公館`、刪掉來源 `大安陳/報價_v1.docx` 並 scan，加上本來就在的「大安陳資料夾不見」與沒確認的「大成磁磚」：

```
[('drift:e-a8c8b8e7','drift_missing'), ('drift:e-e4ee5d11','drift_folder_renamed'),
 ('missing:p-3b75e7bd','missing'), ('name:e-4350ee1b','name')]
n = [1,2,3,4,5,6,7,8,9,10,11,12]
```
- 順序是 drift `missing` → drift 其他 → 原檔不見 → 名字確認，符合 D-135。
- `strict: true` 只有 n=1（`kb entity remove 大安陳`）與 n=7（`kb cards drop f-ed547a95`）。
- `fill`：n=6 `["新名字"]`、n=8 `["客戶的話"]`、n=11 `["同一個"]`、n=12 `["新名字"]`，其餘空。`do` 字串與 spec 範例一字相同（例：`kb pending resolve p-3b75e7bd --how "{客戶的話}"`、n=12 兩條 `kb entity rename 大成磁磚 --to {新名字}`／`kb entity confirm {新名字}`）。
- 空白名稱：`plan.cmd("entity","rename","陳 先生","--to","王 公館")` → `kb entity rename "陳 先生" --to "王 公館"`；`tests/test_lib_plan.py:237-245` 另有 folder_renamed／missing／name 的單元測試。這個知識庫裡 `陳 先生` 沒有綜合卡夾，造不出真的 drift，所以真資料上是直接呼叫加單元測試。

### 4 多量先問
`ingest_auto_max=1`、兩份新檔（另外 moved 36）：
```
plan [{"step":"sync","items":36,...}]
asks[0] batch  "有 2 份要整理，現在做嗎？大約一兩分鐘"   （結果 1 現在做：do 空，then=完整 plan；結果 2 之後再說：note「這個對話不再問」）
```
fixture 知識庫（12 份新檔、預設 5）：`asks[0]` 是 batch、`plan` 是空的。

### 5 tell
- 讀不到 1、依設定不整理 2（`請款` 加 `不整理` 並 `profile apply`、`cards skip` 兩份）、`missing_types` 1 種：
  - `舊合約.doc 讀不到：舊格式（.doc）；記在 系統/待處理.md`
  - `請款類 2 份依你的設定不整理`
  - `有 5 份看起來跟 員工 有關，但設定檔裡沒有『員工』這一類，要加的話在客戶設定檔第一節加一列，下次開對話我會問你要不要套用`
  - 三句與 spec 一字相同；為 0 的（不製卡、PDF 元件）沒列。
- 讀不到多份：我造了 7 份（不是 6）→ 前 5 份各一句，再接 `另有 2 份讀不到；記在 系統/待處理.md`。6 份的情形由 `test_unreadable_five_listed_six_merged`（`另有 1 份讀不到；記在 系統/待處理.md`）覆蓋。

### 6 do 逐條實跑
`rundo.py`：對上面 12 個結果，每條各用一份新複本，換成完整前綴、填空格（`新名字`→`王公館新`、`同一個`→`森活木業`、`客戶的話`→一句話）實跑，全部 exit 0：
```
[1] kb entity remove 大安陳                 → 已移除：大安陳（知識不刪，檔案卡上的掛載留著）
[3] kb entity rebuild 大安陳                → 好，「大安陳」會照檔案卡重寫一張
[4] kb entity rename 王宅 --to 王公館        → 已改名：王宅 → 王公館（舊名記成別名）
[5] kb entity rename 王宅 --to 王宅          → 已照「王宅」把資料夾與綜合卡改回來
[7] kb cards drop f-ed547a95                → 已拿掉「大安陳宅報價單v1」的卡；說「退回上一版」救得回來
[8] kb pending resolve p-3b75e7bd --how "…"  → 已記下
[9] kb pending dismiss p-3b75e7bd           → 已記下
[10] kb entity confirm 大成磁磚              → 已確認：大成磁磚（廠商）
[11] kb entity merge 大成磁磚 --into 森活木業 → 已合併：…
[12] rename 大成磁磚 --to 王公館新 + confirm 王公館新 → 兩條都 exit 0
[2] do 空（刪錯了，我從資源回收筒還原）
```

### 7 SKILL 本文與補丁表
- 本文 2775 字（去掉 frontmatter；舊版 5001）；frontmatter 與 faca9f5 逐字相同（程式比對 True）。
- 禁詞：`grep -nwE "new|stale|unlinked|synthesis_new|entity_drift|synthesis_stale|moved|duplicates" SKILL.md` 無結果；`entity rename|pending resolve|cards drop|entity remove|entity confirm|entity merge` 無結果；`tests/test_skill_ingest.py:72` 另守 `cards drop`、`pending resolve`、`entity rename`。
- 原則七條，每條有理由與編號：B-058、D-104＋D-132、D-073＋D-075、I-082＋D-134、D-135、D-104、D-032。
- `carding.md` 92 行、`commands.md` 150 行（`wc -l`，不超過 150）。
- 補丁表「刪」的各列去處：

| 刪的內容 | 現在在哪 |
|---|---|
| `changes` 最後一行才跑 `scan` | `scripts/kb/changes.py:18`（`要套用請跑 kb inventory scan。`）；SKILL 骨架第 1 步一句 |
| 第 6 步每份卡怎麼做 | `carding.md` 一～六節（讀法第 7～14 行、檔名與被打回第 38 行、stale 第四節、broken 第六節） |
| 標題寫之前先對一次（B-051） | `carding.md` 第 38 行；程式收錯名檔 `cards.py:149,168`（裁定 20） |
| convert 讀不到第二行 | `commands.md:24`、`carding.md:11`；`tell` 由 `plan.py:183` 給句子 |
| 第 8～11 步對照 | `plan.py` `_drift`／`_missing`／`_name`（asks 結果清單，驗收 3、6 實跑過） |
| 回答末尾各種告知 | `plan.py` `_tell`（驗收 5） |

### 10 測試
`python -m unittest discover tests`（worktree，分支已 commit、`git status` 乾淨，所以沒有要 `git add` 的東西）：
```
Ran 1212 tests in 203.551s
OK (skipped=1)
EXIT=0
```
repo `CLAUDE.md` 在 diff 內（`git diff --stat main...HEAD` 9 個檔含 CLAUDE.md）。

## 驗收 8：新舊版對照

### 做法
照 skill-creator 的 with-skill／old_skill 兩組，同一輪一起派 10 個子代理（general-purpose）。
- 舊版：`skill-snapshot/` 裡 faca9f5 的 SKILL.md 與兩份 references；新版：worktree 的 `skills/ingest/`。程式兩邊都用 worktree 的 `kb.py`。
- 每次跑前把 `ingest-check` 整個複製到 `iteration-1/<情境>/<old_skill|new_skill>/work/`，用同一支 `prep.py` 造狀態，所以兩版起點一樣。
- 共同底：複本現況有兩個舊尾巴（36 張卡位置待 sync、「大安陳」資料夾不見），不收掉的話每個情境都多一題。`prep.py` 先 `cards sync` 並 `entity remove 大安陳`，再造情境。這是我的選擇，不是 spec 寫的；兩版一樣。複本的實體名照現況（信義王已改名王宅）。
- 子代理不在 plugin 裡跑：**PostToolUse 驗卡 hook 不會作用**（寫壞的卡不會被擋；我用事後 `cards todo` 的 `plan` 空、無 `broken` 補查）、也沒有開場注入狀態行（它們自己跑 `changes`），`turn note` 沒有因缺回合失敗。
- (a) 的兩個子代理第一次撞到 session 限額（429）中斷，我把兩份複本重造後重派，下面是重派的結果。
- 工具：`iteration-1/<情境>/{eval_metadata.json, old_skill|new_skill/{task.md, final_answer.md, commands.log, timing.json, outputs/}}`；給人看的 HTML：**`C:\Users\Jie\Desktop\llm-wiki-test\整理ingest-對照\review.html`**（`generate_review.py --static`，沒有跑評分／benchmark，沒有寫 assertions，只做質性對照）。

### 逐情境

**(a) 新檔兩份加一份原檔不見**
| | 舊版 | 新版 |
|---|---|---|
| 指令 | changes、inventory scan、cards todo、convert×2、todo、(讀 synthesis.md)、todo、turn note | 同（另多一次重跑 todo） |
| 寫了什麼 | 2 張新卡、改 `王宅.md`；清冊、待處理跟著動 | 一樣（檔案變動清單兩邊一致，只差卡的 id） |
| 問了什麼 | 2 題，沒編號：報價_v1 不見、大成磁磚名字 | 2 題，**編號 1～3、4～6**，文字照 `asks`，沒超過 3 題 |
| 末尾 | 兩份 .doc 讀不到＋「用 Word 另存成新格式」；5 份員工類 | `tell` 三句照講；沒有「另存」那句；兩題編號選項 |
- 結論：**新版沒有退步**。小差異（minor）：舊版會多講「用 Word 另存成 .docx 我就讀得到」，新版的 `tell` 只給原因，那句「怎麼辦」在 `commands.md` convert 表，SKILL 本文沒指過去（skill-reviewer 第 6 條同一件事）。
- 兩版都沒處理 `orphans`（內湖李 2 張卡，原檔所在的資料夾已被排除），也都沒向客戶講；v0.11.0 就是這樣。

**(b) 實體夾被改名加一個沒確認的名字**
- 兩版都沒跑寫入指令；問了同樣兩題。舊版沒編號；新版 **1～3（王宅改名）、4～6（大成磁磚）** 接著編，另外在 3、5、6 後加「請告訴我新名字／是哪一個」。
- 結論：**新版沒有退步**，編號接著編符合 D-134。

**(c) 接 (b)，客戶回「2」**（題目寫明上一則是 1～6 的編號清單；舊版也給同一份編號清單當上一則）
- 兩版都實跑 `kb entity rename 王宅 --to 王宅`（輸出「已照「王宅」把資料夾與綜合卡改回原名」）、`案子/王公館/王宅.md` 刪、`案子/王宅/王宅.md` 新增，檔案變動兩版一樣；再跑 todo 後只剩大成磁磚一題，**重新從 1 編（1～3）**，`turn note` 成功。
- 測試設計的限制：每個子代理是新的 context，手上沒有「上一輪的 todo 輸出」，所以新版子代理沒辦法照「不要重跑 todo 再對編號」做，重跑了一次，結果對到的仍是 n=2 的 `do`。這一項只證明「編號 2 → 改回原名」的 `do` 執行對；「靠上一則記憶對編號、不重跑」的路徑，真實 session 才驗得到（確認第 2 步）。
- 結論：**新版沒有退步**。

**(d) 原檔不見那題，客戶回「好」**
- 舊版：沒跑任何寫入指令，重問兩題（自己編 1～6，因為 json 已有 `asks`）。
- 新版：**沒有 `cards drop`**，沒有任何 kb 指令；回答說明「刪除是刪除的動作，我需要你明確選一個」，重問 1～3、4～6，沿用上一則的編號。
- `files_changed.txt` 兩版都是空的。
- 結論：**新版沒有退步**（I-082 守住）。

**(e) 10 張卡檔頭改回範本第一版（待掛實體），`ingest_auto_max` 預設 5**
- 兩版都只跑 `changes`、`cards todo --json`，沒寫任何檔，**先問**：「有 10 份要整理，現在做嗎？大約 2 分鐘」，1. 現在做 2. 之後再說（舊版也套了 `asks` 的編號）。
- 新版回答多講「加上 1 張王宅綜合卡要更新」，是 `plan` 在 `then` 裡的內容。
- 結論：**新版沒有退步**。「現在做」之後 `then` 的實跑沒有測到（情境到問就結束），見 code-review 第 4 條。

### 效率（單次，僅供參考）
| 情境 | 舊版 tokens／秒 | 新版 tokens／秒 |
|---|---|---|
| a | 100,811／160 | 91,324／121 |
| b | 69,418／48 | 59,124／40 |
| c | 76,814／51 | 73,790／71 |
| d | 69,747／41 | 54,117／46 |
| e | 70,018／52 | 59,810／54 |
子代理執行時間受限額與排隊影響，tokens 比較可信一點：新版五個情境都較少，a 約少 9%、d 約少 22%。

### 子代理自己提出的不確定處（新版）
- 「先 `cd` 還是不 `cd`」：SKILL 說不 cd，子代理環境得用 `cd`／`Set-Location`（測試環境因素）。
- `plan` 空、只有 batch 時，`orphans` 與 `unlinked` 的細節要不要講，本文沒寫（e）。
- 新卡正文比 carding.md 的 300～800 字短很多（原檔只有 54、82 字），兩版一樣。

## 驗收 9：plugin-dev 審查
讀 `plugin-dev:skill-development` 當依據，跑 agent `plugin-dev:skill-reviewer`（唯讀）審 `skills/ingest/`。結論：**沒有 major**，9 條 minor。
1. SKILL.md:44／plan.py:16：`plan.read` 裡的 `${CLAUDE_PLUGIN_ROOT}/rules/synthesis.md` 是 runtime 字串、不會被代換，Read 不展開；本文只講了 `read` 的相對路徑。→ 加一句「`read` 裡的 `${CLAUDE_PLUGIN_ROOT}` 換成 kb 前綴去掉 scripts/kb.py 的部分」，或讓 plan.py 給相對路徑。（實測：(a) 新版子代理自己讀了 `rules\synthesis.md` 的正確路徑，因為任務把 `${CLAUDE_PLUGIN_ROOT}` 事先說明了；真實 session 靠開場那行「kb 指令」的前綴反推，沒測。）
2. :45 沒說用「最後一次重跑」的 `asks`、`tell`（重跑後編號會變）。
3. :47 `fill` 值的引號只說「有空白或特殊字元加雙引號」，沒說 `"`、`\`、`$`、反引號要跳脫；`--how "{客戶的話}"` 那格程式已預先加了引號。
4. :45 `plan`、`asks`、`tell` 全空時沒說講什麼（人讀輸出是「卡都是最新的」）。
5. :44 迴圈中沒說讀過的 `read` 不必再讀。
6. :45 `tell` 的讀不到只有原因，「怎麼辦」在 commands.md convert 表，本文沒指過去（對照 (a) 實際少了那句建議）。
7. :65 參考節只指 `cards todo`，entity／pending／profile／turn 各節沒指。
8. carding.md:29、:87 說 `entity add` 照第二節，但第二節沒寫語法（在 commands.md 的 entity 表）；`unlinked` 的 `read` 只指第七節，略過一、二節。
9. 重複與肥胖（低影響）：「答案在前」出現三次、「被打回改一次」兩處加 carding.md、commands.md 51–56 行人讀輸出細節。

這些與 spec 附錄 B-067 的 minor 有重疊，沒有一條造成行為錯誤。

## code-review（high，只看 main...HEAD）
9 條，我對可驗證的幾條做了確認，嚴重度是我的判斷：

| # | 位置 | 內容 | 嚴重度 |
|---|---|---|---|
| 1 | `plan.py:143`、SKILL.md:47 | `pending resolve ... --how "{客戶的話}"` 的引號是寫死的，客戶原話含 `"`、`$`、反引號時 AI 照貼會壞引號或展開；`quote()` 對其他參數有跳脫，這格沒有，本文也沒說要跳脫 | 中（建議發行前處理：讓程式給不加引號的 slot，或本文加跳脫那一句；同 skill-reviewer 第 3 條） |
| 4 | `plan.py:110`、SKILL.md:44／49 | batch 時 `plan` 只剩 sync，本文又寫「重跑到 `plan` 空為止」；「現在做」帶 `then` 完整 plan，靠本文第 5 步的 `then` 規則救。每次 `todo` 仍會重發 batch，「不再問」只靠對話記憶。我的測試沒走到「現在做」之後 | 中（建議補一個「現在做」之後的實跑；本文把「`plan` 空」和「`then` 做完」講成同一件事） |
| 5 | `test_scripts_cards_cli.py:57` | `plan == [] iff 第一題是 batch` 這條斷言不符合 `build`（batch 時 sync 仍保留），目前只因 fixture 兩邊都是 False 才過，也沒走到 batch 路徑；docstring「plan 空」不對 | 低（我讀過程式與測試確認；`test_lib_plan.py` 有 batch 的正確測試） |
| 2 | `plan.py:38` | `quote()` 讓以 `-` 開頭、只含安全字元的名稱不加引號；實測 `entity rename -1樓` 仍被 argparse 當位置參數（有回「實體清單沒有「-1樓」」），`--x` 這類才會被當選項 | 低（邊緣） |
| 3 | `plan.py:213` | `_by_setting` 重讀清冊並重寫 `cards.todo` 的 skip_kind 計數；兩邊不一致時 `tell` 退成只講份數 | 低（維護風險） |
| 6 | `plan.py:190` | `tell` 用 uri 最後一段當檔名，若 uri 經過小寫正規化可能與磁碟大小寫不同 | 低（沒驗證；測試資料的檔名顯示正常） |
| 7 | `cards.py:530` | `plan.build` 在每個呼叫 `todo` 的地方都跑（含 `cards sync`、人讀 `todo`） | 低（效能；無行為差） |
| 8 | SKILL.md:44 | 「到 plan 空為止」遇到某份卡一直被打回時 `plan` 不會空；本文有「只停那一份、本輪不再重試」，但沒講整體何時停 | 低 |
| 9 | `plan.py:101` | `_drift` 對 `found` 太短的 drift 會 IndexError，整個 `todo --json` 跟著壞 | 低（現有 drift 的 `found` 都是三段；加防衛即可） |

## 其他觀察（非驗收項目）
- **`moved` 在掃描後又出現（v0.11.0 就這樣，與本題無關）**：`cards sync` 之後 `todo` 的 `moved` 是 0；但同一個知識庫只要來源多了新檔再 `inventory scan`，`todo` 又列 `moved 36`（每張卡的 `to` 只是目錄），再 sync 一次才清掉。v0.11.0 的程式輸出逐字相同（驗收 1），所以不是這題造成。複本 `ingest-check` 本來就帶著 moved 36 與「大安陳資料夾不見」的狀態，確認情境照 spec 做時會遇到；只掃描、不新增檔時沒有再出現。
- **`orphans`**：內湖李（`客戶案件` 的排除項目）有 2 張卡，所有子代理都看到、都沒處理、都沒跟客戶說，v0.11.0 就是不處理。
- 所有子代理都注意到「skill 說不 cd，但 kb 要在知識庫裡跑」，是子代理環境的差異，真實對話開在知識庫裡。

## 結論
- **驗收十條全過**（1～7、9、10 逐條有證據，8 對照五情境過），沒有不過的：1212 測試 exit 0；todo 既有鍵與人讀輸出逐字不變；`plan`、`asks`、`tell` 的內容、順序、連號、strict、fill 與 `do` 實跑都對；SKILL 本文 2775 字、frontmatter 不變、禁詞 grep 為空；skill-reviewer 沒有 major。
- **新舊對照五個情境新版都沒有退步**：該先問的先問（e）、「好」不刪（d）、編號接著編（b）、回 2 改回原名（c）、製卡與綜合卡更新一樣（a）。新版 tokens 較少。唯一的小退步是 (a) 少了「讀不到的檔用 Word 另存」那句建議（minor）。
- 發行前建議處理：①`--how` 的引號與客戶原話跳脫（code-review 1 ＝ reviewer 3，中）；②補測「現在做」之後 `then` 的實跑，並把「plan 空」與「`then` 做完」的停止條件寫清楚（code-review 4，中）；③reviewer 第 1 條（`read` 的 `${CLAUDE_PLUGIN_ROOT}`）與第 6 條（讀不到的「怎麼辦」）各一句，確認時順手看。其餘是 minor，可記 backlog。
- 這份測試**沒有**的：真實 plugin 環境（hook、開場注入）、(c) 的「不重跑 todo、靠上一則對編號」、「現在做」之後 `then` 實跑、確認情境三步，都留給確認 session。
