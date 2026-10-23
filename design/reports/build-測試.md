# build 測試報告

日期：2026-10-23～24
測試：llm-wiki-j-2c [3216e8]（派工：llm-wiki-j-22 [23488e]）
測的版本：`spec/build` 的 `89d71c3`（`fe4c15c..89d71c3`：`b9d79e7` 主體、`89d71c3` 驗後修）；跑前樹乾淨，全程沒改任何程式。
spec：`design/specs/2026-10-22-build.md`（含 90b3cca 補充）

## 結論

- **驗收 1～14 全過**（14 是 2327 個測試 exit 0；驗收 3、4 的環境相關項用替身，另外用 `python -S` 與真機各跑了一次）。
- **要修的有 3 件，都不擋發行但建議這輪處理**：
  - **S1（中）接手之後，前一個對話留下的批次還會擋 `build done cards`／`synthesis`，最多 90 分鐘**，真跑重現。確認情境第 5 步的「中途關掉再開」正是這個情境。
  - **S2（中）`build adopt --extract` 沒擋憑證檔名，遮值也漏了幾種**：檔名像密碼的舊 wiki 頁照樣整份切進 `.system/temp/adopt/`，私鑰區塊、`pwd:`、Bearer 也沒遮。
  - **S3（低到中）抽樣用的 `--ids` job 沒收掉，進全量整理第一次 `dispatch` 會說「整理完了」其實還有 38 份。** 真跑時 AI 剛好多叫了一次 dispatch 收掉，沒踩到；腳本重現。
- 另有 7 件低等級（S4～S10），都不擋。
- **真跑（`claude -p`）從環境走到報告、試問、再打 build 被擋，一路走完**：AI 對客戶說的話沒有英文自言自語、沒有步驟編號、沒有內部編號；沒有自己跳步；有列「我猜的」；有把握的名字直接登記（3 個）、有疑慮的一次列出（6 題）；舊 vault 撈到 8 條候選、挑了 5 條記；舊 vault 與來源夾一個檔都沒變；報告六段齊。唯一的語言問題是 S4（最後一句變成簡體）。
- settings.json 跑前跑後 sha256 相同：`8ae82f7a69eea7b8…`（全文跑前存在 scratchpad `settings_before_build.json`）。舊 `llm-wiki` 本機是停用的（`old_wiki` 回「停用了」）。

## 環境與紀錄

- Windows 11、Python 3.14.7、pypdfium2 5.14.0（站台套件與工具夾都有）、LibreOffice、智慧型應用程式控制開著（沒擋到我們的程式）。
- 全部測試：`python -m unittest discover tests`，exit 0，**Ran 2327，OK（skipped=1）**，583 秒；跑完 `git status` 乾淨。
- `claude plugin validate`：通過（警告同前：沒有 icon、README 沒有安裝行）。
- 只用 `claude -p --plugin-dir C:\Users\Jie\Desktop\llm-wiki-j-build`，一次只開一個；沒裝 plugin、沒碰使用者層設定。
- 測試腳本在 scratchpad（`t12.py`、`t3.py`、`t4b.py`、`t5*.py`、`t6.py`、`t10*.py`、`t11.py`、`t13*.py`、`t14.py` 等），不進 repo。
- 兩處測試設定的說明：（1）真跑第 10 輪我在 75 秒時**砍掉 claude 行程**扮「中途關視窗」，所以 `hook-errors.log` 有一行 `23:22:17 guard OSError: [Errno 22]`，是 hook 的輸出管線被砍斷造成，不算 bug；（2）接手之後 `done cards` 被擋，我把前一個對話兩批的 `created` 手動改成 19:00（模擬「隔天」）才繼續往下，原因見 S1。

## 驗收逐條

| # | 結果 | 證據 |
|---|---|---|
| 1 | 過 | 空夾 `build status --json` → `step: env`、`not_empty: false`；放一個檔 → `true`（`desktop.ini`、`.` 開頭的不算）；`<V>`（有 `.system/`、沒有 build.json）與 `<V>/案子` 子夾 → exit 3 `這裡已經是建好的知識庫，要加資料夾、整理檔案直接跟我說`，一字不差（人讀與 `--json` 都印這一句） |
| 2 | 過 | `build start` 建骨架（跟 `kb init` 比，只多 `.system/build.json` 與我自己動作留下的回合／日誌）；session A `status` 認領後 session B → exit 3 `另一個對話正在建置（訪談，最後動作 22:59）；那個對話還開著就回那邊繼續，已經關掉了就說『接手建置』`（status／done 都擋）；`status --takeover` 換成 B、`takeovers` 記一行、A 再動就被擋；鎖空著時兩個 session 同時 `status` ×12 次，每次恰好一個贏；A 的 `lock_until` 過了 B 不用接手就能做；同一 session 同時兩個 `done` 只有一個成功 |
| 3 | 過 | 八項（python、kb、trust、sac、pdf、office、disk、old_wiki）用替身：沒有 `~/.claude.json` → warn「看不出」、信任 false → fail、true／上層夾信任 → ok、壞檔 → warn；sac：沒有值 → 「這台沒有」、0 → 關著、1 → 開著、2 → 評估中（都 warn）、讀錯 → warn；PDF 子行程被擋 → fail「PDF 元件被擋，PDF 會讀不到」且 sac 那項改說「它擋了 PDF 元件…不建議關，關了可能開不回來」；沒裝 → fail 帶 `fix: kb office setup`；沒有 Office 也沒有 LibreOffice → warn、有 LibreOffice → ok、工具沒裝帶 fix；磁碟 500 MB → warn、剛好 1 GB → ok；`llm-wiki@` 開著 → fail、設 false → ok、裝著沒設定 → warn、被資料夾的 `settings.local.json` 蓋成開著 → fail。跑前跑後整棵樹雜湊一樣（只讀）；建好的知識庫與子夾裡也能跑 |
| 4 | 過（一個低：S6） | 用 `python -S`（本行程 import 不到）跑真的 `convert`：一般 PDF、加密 PDF（帶／不帶／帶錯密碼）都走工具夾子行程，結果跟本行程一樣（讀得到／`有密碼`／`密碼不對`）；兩處都沒有 → 原本那句 `還沒裝 PDF 元件（pip install pypdfium2）`；記下 `_run_child` 的參數：argv 只有 `python -c <固定碼>`、環境變數沒有密碼、密碼只在 stdin 的 JSON；只有 owner 密碼的 PDF 子行程回 `True`、有 user 密碼的回 `False`；子行程故意出錯（假工具夾的 pypdfium2 開檔就丟例外、訊息帶密碼）→ 回「PDF 元件出錯」，`hook-errors.log` 裡密碼原字被換成 `███`；但密碼的 `repr` 形式漏遮，見 S6 |
| 5 | 過 | 每步照順序 `done`，非目前這步 → exit 3 `現在是「訪談」這一步，不是「全量整理」`；`cards` 還有可派／在跑 → `還有 N 份沒整理完`／`還有 2 批在背景整理`；`names` 還有 3 個 → `還有 3 個名字沒確認，確認完再往下`；`synthesis` 還有 3 張／在跑 → `還有 3 張綜合卡沒寫好`；`redo sample` 從全量整理回到抽樣、完成紀錄清掉；`redo` 後面的步 → `還沒做到…不能重做`；沒登記資料夾時 `done setup` 後下一步是「交付報告」，`done finish` 在沒報告時被擋 |
| 6 | 過 | `profile apply --from <填好的 md>` → `已套用客戶設定檔：型別 +1；建了資料夾 案子`，md、json 都備進回合；表格值不合法 → `客戶設定檔讀不懂：第 1 節第 1 列的大類要是…`；不是設定檔 → `少了第 1 節`；非 UTF-8、檔不存在都有一句；失敗的 5 次整棵樹沒變；apply 拒絕（刪有東西的型別）→ 原本的 md 寫回；未套用的手改讓 `done setup` 被擋 `客戶設定檔有改還沒套用，先跑 kb profile apply` |
| 7 | 過 | `cards dispatch --ids` 4 個 → 只派那 4 份；`set people` 寫進 build.json（先遮值：`密碼是 abc12345` → `███`）、報告數字段有「幾個人用」；太長／空值有句子 |
| 8 | 過 | `entity accept 信義王` → `已登記：信義王（AI 判斷）`，`source: ai_judged`；`cards todo` 的 `synthesis_new` 當 `confirmed: true`、`asks` 沒有它的名字題；`entity list` 人讀標「AI 判斷」；沒登記 → `實體清單沒有：沒這個`（exit 3）；已確認 → `已經確認過了：大安陳`；再 accept 一次 AI 判斷的回同一句（不寫） |
| 9 | 過 | 有新名字時 `plan` 第一步是 `names`（`names` 帶實體名與掛的卡標題），在名字題之前；accept 之後只剩沒 accept 的出題 |
| 10 | 過 | `<O>` `--check`：raw、linked（`../外部案子`、`exists`）、wiki 頁、hot／log 字數、規則 2、日程 1 都對得上 fixture；`--extract` 每段 ≤ `adopt_chunk_chars`（1.4 MB 的 log＋6 萬字單行也沒超，107 段）、終端機只印「切好了：N 段」；一般夾與不存在的路徑 → `這不像舊的 llm-wiki 資料夾`；`.system/temp/adopt` 是連結（junction）→ 拒絕不寫；`<O>` 跑完雜湊不變（真跑完也不變） |
| 11 | 過 | `cards dispatch --adopt` 派第四種工作（6 段 → 4＋2 兩批，`description`「讀舊 wiki N 段」）；`--adopt` 加 `--ids`／`--source` 被擋；沒切段 → 先跑 extract 的句子；`cards batch` 給 `items`（`name`、絕對 `chunk`）、`read`、`report` 格式；子代理回報有「- 004.txt：太亂讀不懂」→ 記成沒讀成、`say` 說 `讀好 3 段了，1 段沒讀成` |
| 12 | 過 | `report --from`：六段齊 → 寫 `系統/建置報告.md`、數字段由程式接上（AI 寫的那段被換掉）、`密碼是 hunter2xyz`／`帳號 A123456789`／`password: Abc12345` 都遮成 `███`、`系統/建置報告.md` 備進回合（manifest `create`）、build.json 記 `report`；缺一段 → `建置報告少了一段：讀不到的`、多一段／重複 → `建置報告多了一段：…`、缺數字 → 少了一段：數字，都沒寫；工作區外、經 junction 繞出去、不存在的路徑都被擋 |
| 13 | 過 | `skills/build/SKILL.md` 有 `disable-model-invocation: true` 與 `argument-hint`；本文 1788 字（≤ 3000）、references 73 與 30 行（≤ 150）；`test_budget` 在全部測試裡；`claude plugin validate` 通過；plugin-validator 沒有錯（有意見見下面）；ingest 描述行兩版相同（`fe4c15c` 與 `89d71c3` 取出的 description 逐字一樣），不用跑 run_eval |
| 14 | 過 | 2327 個測試 exit 0 |

## 攻擊面

| | 結果 | 證據 |
|---|---|---|
| (a) PDF 子行程密碼 | 過（S6 低） | 見驗收 4。命令列、環境變數、`hook-errors.log`、輸出都沒有原字密碼；只有「錯誤訊息帶密碼的 `repr` 形式」會漏 |
| (b) `adopt --extract`、`report --from` 遮值，舊 vault 不變 | **有漏（S2）**，舊 vault 沒變 | 遮到：`密碼：`、`帳號 `、`pw`、`passwd`、`secret`、`token`（`ghp_…`）、全形、空白分開的「密 碼」、cp950／BOM 檔；漏：`pwd:`、`Authorization: Bearer …`、`-----BEGIN RSA PRIVATE KEY-----` 區塊、`解鎖碼`、`身分證 A123456789`（沒有關鍵字）、信用卡號、`wiki/projects/密碼清單.md` 這種檔名像密碼的頁整份被切進去。`<O>` 與來源夾比對雜湊都一樣 |
| (c) adopt 子代理只能跑 AGENT_ALLOW | 過 | 用 guard.py 餵已登記子代理的 payload：Bash 的 `build adopt|status|done|set|report`、`entity accept`、`statement add`、`ruling add`、`profile add`、`cards dispatch --adopt`、`cat`、PowerShell `Get-Content` 全 deny `製卡助手不能做這件事`（沒新增白名單）；Read 段檔（`.system/temp/adopt/001.txt`，正斜線反斜線都行）allow；Read `unlock.json`、`*.secret*`（含 `adopt/..` 繞）deny；Grep／Glob 在 `.system` 或知識庫根都 deny；Write／Edit 段檔、`.system`、`系統/` deny。已知的缺口（不是這輪新增）：子代理工具 Write 在 `檔案索引/` 底下被放行，adopt 批次「不寫任何檔」只是文字規定，見 S8 |
| (d) 守門插的 `--session` | 過 | 主對話的 `build status|adopt|report` 都插 `--session sid`（Bash 與 PowerShell `python …` 形式）；指令前面帶自己的 `--session|--sess|--turn|--agent` → deny `kb 指令不能自己帶 --turn、--agent 或 --session`；子子命令後面帶（`--session=evil`、`--sess evil`）→ argparse 拒絕（exit 2），`build --session evil status` 也被當成子子命令錯誤。PowerShell `& python …` 形式守門不管（不插），這是既有行為 |
| (e) 兩個 session 搶鎖 | 過 | 見驗收 2：鎖空著 ×12 次恰好一個贏；同時兩邊 `--takeover` ×6 都成功、最後一個贏（`takeovers` 各記一行），失敗的那邊再動就被擋；接手後舊 session 的 `done` 被擋 |

## plugin-dev 審查

- **plugin-dev:skill-reviewer／skill-development（`skills/build/`）**：本文 1788 字、references 73／30 行、frontmatter 對；`argument-hint` 與 `$ARGUMENTS` 用法沒錯。審查員列的疑點（我只核對標 ✔ 的，其他是審查員讀碼的意見，未獨立重現）：
  - ✔ `steps.md` 全量整理說「看 `plan`…直接做 `then` 的步驟」：`then` 只在 batch 那題的結果裡，`plan` 本身沒有；真跑時 AI 沒被誤導（它直接 `dispatch`），但文字不準。
  - ✔ `SKILL.md` 沒有「代換不了時」的退路，也沒提 `--takeover`（只在 `steps.md` 第一節）；真跑時 AI 讀了 `steps.md` 才接手成功。
  - ✔ `adopt.md` 的 `kb ruling add --kind 口述為準|不採用 --text` 少了 `--entity／--card`；真跑時 AI 自己讀 note 的 ruling.md 補了 `--card`。口述候選的實體可能是「無」，但 `statement add --entity` 必填，沒有交代。
  - `steps.md` 寫死 `profile add` 的欄名（D-132 第 3 點：指令與參數表應由程式輸出給）；「你」「她」混用；`no_sources` 又記了舊 wiki 時會多走 `adopt`（steps.md 說直接到報告）；`done sample` 沒有前提檢查（S3）；同一件事兩處寫（智慧型應用程式控制、redo、報告路徑）。
- **plugin-dev:command-development**：`disable-model-invocation: true` 與 `argument-hint` 沒有誤用。`claude -p "/wiki-with-willy:build"` 能叫起來（真跑第 1 輪）。
- **plugin-dev:agent-development（`agents/card-maker.md` adopt 段）**、**plugin-validator**：沒有高等級。意見：
  - 工具清單四種工作共用（Read／Write／Edit／Bash），「adopt 不寫任何檔」只是文字（S8）；
  - `card-maker.md` 回報段與 carding.md 第 12 行寫「不寫…人名、金額、日期（D-022）」，跟第十二節要求回報實體／日期衝突；**真跑時製卡助手照第十二節的格式回了**（8 條候選帶實體與日期），沒受影響，建議把兩句限定在非 adopt 批次；
  - `card-maker.md` 「開始」第 1 步說 `items` 同 `cards todo`、有 `profile`／`entities`，adopt 批次沒有；
  - description 的觸發寫「build 指令」，實際入口是 `/wiki-with-willy:build`（無害）。
  - `hooks.json`、`plugin.json` 沒動。

## 真跑（`claude -p`）紀錄

fixture：`llm-wiki-test/build-e2e/`：`客戶夾`（空）、`來源`（46 份：含 11 個 PDF 其中 1 個有密碼、2 個舊 `.doc`、`私人/` 2 份帳單、`公司行政/薪資/`）、`舊wiki`（`tests/fixtures/old_vault` 複製）、`外部案子`（2 份）。第一個對話用 `claude -p "/wiki-with-willy:build"` 開始，之後每輪 `--resume` 扮服務人員；第 10 輪砍掉後用**新對話**再打 build 並「接手建置」，到完成後再開第三個對話打 build。

| 輪 | 我說的 | 秒 | AI 做了什麼／說了什麼 |
|---|---|---|---|
| 1 | `/wiki-with-willy:build` | 16 | 跑 `build status`、讀 steps.md；講八項檢查要看什麼、問要不要開始 |
| 2 | 好，開始檢查 | 13 | `build env`：逐項中文講，只有智慧型應用程式控制要提；問要不要建 |
| 3 | 智慧型應用程式控制是什麼？要關掉嗎？ | 9 | 白話講它擋什麼、**不建議關**（關了可能要重灌）、現在沒擋到我們、關不關她決定 |
| 4 | 好，建吧 | 12 | `build start`；講訪談要問的七件事 |
| 5 | 公司名、資料夾、私人那夾不要讀、兩個人用、舊 wiki 路徑 | 34 | `folders add --check`、`build adopt --check`，整理成 5 項確認；**自己注意到「薪資」夾**問要不要讀；講舊 wiki 的做法（舊頁不搬、原檔登記、舊的一個字不改） |
| 6 | 好，薪資也不要讀 | 56 | 登記 3 個資料夾、2 個 `exclude --kind hidden`、公司名、`set people`、`set adopt`、舊 wiki 的 raw 與外部案子 `folders add`、`done setup`；講結果與下一步 |
| 7 | 盤點吧 | 24 | `build facts`：42 份、各夾份數、讀不到 3 份（2 舊格式、1 有密碼）與怎麼辦、約 5 分鐘、一句「份數多會用掉比平常多的額度」；沒講 token |
| 8 | 先抽樣 | 241 | 挑 11 份（跨夾跨格式）`cards dispatch --ids`、背景派 1 批、做完再叫一次 dispatch 收尾；列「我猜的」：分類、狀態詞、卡型、哪幾個像同一個、公司名別名 |
| 9 | 案子改叫工程、王宅＝信義王宅、加請款單、狀態詞、最想知道進度與尾款 | 132 | `profile add 型別|卡型|每類段落|關於你`、對舊分類的 8 張卡 `cards redo`、`done sample`；講狀態詞程式只有三種（進行中／結案／擱置）、洽談中施工中寫在「進度」段 |
| 10 | 名字講「信義王」，整理全部（75 秒砍掉） | 75 | 記偏好、`cards dispatch` 派 3 批（20＋20＋1）；我砍掉 claude 行程 |
| 新對話 1 | `/wiki-with-willy:build` | 8 | `build status` → exit 3 `另一個對話正在建置（全量整理，最後動作 23:21）；那個對話還開著就回那邊繼續，已經關掉了就說『接手建置』`，AI 照講 |
| 新對話 2 | 那個對話關掉了，接手建置 | 349 | `status --takeover`、`cards todo`、`dispatch` 剩下 37 份（2 批）背景做完；`done cards` 被擋 → **S1**，AI 誠實停下並把卡住的指令與輸出整理給服務人員 |
| 新對話 3 | 好，繼續（再被擋一次）；我把前一個對話的兩批改成 19:00 | 20＋48 | 第一次仍被擋；改過之後 `done cards` 過，進「確認名字」：**直接 `entity accept` 3 家廠商**，其餘 6 個一次列出（3 個「宅」要不要去掉、王宅／陳宅是不是同一個、森活木業與森活木地板是不是同一家）並教怎麼回 |
| 新對話 4-5 | 1 到 3 去掉宅，4 同一個，5 另一個叫中山陳，6 同一家 | 38＋27 | `entity rename|merge|confirm`；第 6 題接著問用哪個名字，答 1 後 `done names` |
| 新對話 6 | 好，寫吧 | 136 | 綜合卡：派 2 批（5＋3 張）、背景寫完 8 頁、`done synthesis` |
| 新對話 7 | 好，開始讀舊 wiki | 56 | `adopt --extract`（6 段）、`cards dispatch --adopt`（4＋2 兩批）、收回報；跟 `statement list`、`agenda list`、`entity list` 比對，**主動指出「王宅 2026-05-01」跟信義王日期對不上**，把 8 條分四類列出，加三個疑問 |
| 新對話 8 | 1、2、4、5、7 要記；3、6 不要；王宅＝信義王；陳先生＝大安陳；第 8 條先放著 | 47 | 只記說好的：2 條口述、1 條偏好、1 條裁示（不採用報價第一版）、1 條日程；`done adopt`；第 8 條照說先放著 |
| 新對話 9 | 好，寫報告 | 36 | `report --facts`、Write 工作區、`report --from`、`done finish`；對話講三句重點、問要不要試問 |
| 新對話 10 | 信義王現在做到哪？ | 38 | 走 answer skill：`lookup`、讀卡、`convert` 對原檔、帶〔出處〕、指出進度表可能沒更新 |
| 第三個對話 | `/wiki-with-willy:build` | 11 | `build status` → exit 3 擋下；**AI 用簡體中文回覆**（S4） |

每項要記的：

- **給客戶看的字**：主對話共 43 段文字，沒有英文自言自語、沒有「步驟 N」、沒有 `f-…`／`e-…`／`b-…` 這類內部編號；唯一問題是最後一句簡體（S4）。子代理自己的英文過場（如「Now let me read the cards」）客戶看不到。
- **自己跳步**：沒有。每一步都先 `build status`（或用上一步 done 的輸出）再做；被擋就停（S1 時它停下、沒繞過去）。
- **抽樣有沒有列「我猜的」**：有（第 8 輪四項），也在報告第三段。
- **名字**：有把握的直接登記（3 家廠商）、有疑慮的一次列出 6 題；平常整理那條沒出題。
- **舊 vault**：撈到口述 3、規則 3（另 1 條「業主電話不寫」自己判斷設定檔已排除）、日程 1、待確認 1；`<O>` 雜湊不變。
- **報告**：`系統/建置報告.md` 六段齊（資料去了哪、怎麼開始問、我猜的請改、讀不到的、要你決定的、數字），數字段由程式接上（登記 3 個資料夾共 42 份、檔案卡 39、綜合卡 8、讀不到 3、AI 判斷登記 3 個、合併 2、要注意的、兩個人用、花約 48 分鐘〔含我等批次過期的時間〕）。
- **私人與薪資**：清冊、卡、暫存都沒有這兩夾的任何東西。
- **花了多久**：claude 實際跑的時間約 27 分鐘（含 S1 白等的兩輪）；牆鐘 11:01～00:01 一小時，其中約 20 分鐘是等批次過期與跑 B-412。
- **權限**：沒有「要允許嗎」的跳出（`-p` 下沒有互動）；沒有被擋的客戶動作。主對話裡 AI 偶爾用 `python -c` 讀 todo 結果檔，守門放行。
- 走不下去的地方：只有 S1。`-p` 做不到的：真正在桌面版「關視窗」（我用砍行程代替）、讓舊 `llm-wiki` 開著看 `fail`（本機是停用的，只用替身驗）、PDF 元件真的被智慧型應用程式控制擋住（只用替身，真機是 `ok`）。

### B-412 回歸（build worktree）

`claude -p "<prompt>" --plugin-dir <worktree> --permission-mode acceptEdits --allowedTools "Bash(python *)" Read Glob Grep Skill Agent`，每次全新 tmp 知識庫（19 份待整理），逾時 480 秒。

| 次 | prompt | 秒 | 第一個 kb 指令 | `cards dispatch` | 派 card-maker | 卡 | 重複派 | 名字 | 英文過場 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 你好 | 262 | `changes` | 4 | 2（做卡、綜合卡） | 18＋3 張綜合卡 | 0 | 3 個都 `ai_judged`、`asks` 空 | 0 |
| 2 | 237 字長指示 | 415 | `changes` | 4 | 2 | 18＋3 | 0 | 3 個都 `ai_judged`、`asks` 空 | **2 段**（`Three case names confirmed; checking the plan again for the case summa…`、`Verified. Recording the log entry.`） |

- 兩次都**先整理**（`changes` → `cards todo` → `dispatch`），names 那步加進來後沒有改變順序；三個名字（大安陳、內湖李、信義王）看卡都很清楚，AI 全部直接 `entity accept`，最後回答說「這三個名字我判斷沒問題，就直接登記了」並教怎麼改；信義王原本壞掉的綜合卡也在名字登記後被派去重寫、有檔頭。fixture 沒有「有疑慮」的名字，所以沒出名字題（有疑慮才問的那邊由真跑「新對話 3」（6 題一次列出）驗過）。
- 沒有權限拒絕、沒有 `hook-errors.log`。
- 長指示那次有兩段英文過場，是通知後的轉場句，跟 card-maker 前幾輪觀察到的同一個問題（S5）。

## 要修的

| # | 等級 | 問題 | 重現 | 建議 |
|---|---|---|---|---|
| S1 | **中** | 接手之後，前一個對話派出去、子代理已死的批次，會擋 `build done cards`／`synthesis`／`adopt` 最多 **90 分鐘**（`card_batch_expire_minutes` 30 分鐘 × `HOLD_FACTOR` 3，因為記著的子代理沒有 stopped）。`build._running` 數**所有 session** 的 live 批次，而 `cards status`／`cards dispatch`（`batches.running`）只數這個 session 的，所以接手後 AI 看到「沒有在整理、`done: true`」卻 `done cards` 一直回 `還有 2 批在背景整理`，只能停下來叫服務人員。接手的鎖本身不擋 30 分鐘（驗收 2 過），但等於換了個地方擋 | 真跑：第 10 輪 75 秒砍掉 claude → 新對話「接手建置」→ 37 份做完 → `build done cards` 回 `還有 2 批在背景整理，做完再往下`，`cards status` 回 `現在沒有在整理；還有 0 份沒整理`；等到 23:52（30 分鐘過了）仍被擋；手動把那兩批 `created` 改成 19:00 才過。腳本：任何有 open 批次、agent 沒 stopped、屬於另一個 session 的 job 都會 | 二擇一：（1）`status --takeover` 順便把 `takeovers[].from` 那個 session 的 job 與 open 批次放掉（同 `batches.stop` 的做法）；（2）`build._running` 改成只數這個 session 的（跟 `batches.running` 一致），並讓 `cards dispatch` 在 takeover 後重認領舊批次的項目 |
| S2 | **中** | `build adopt --extract` 有兩個縫：（a）**不看檔名**：`wiki/projects/密碼清單.md`、`wiki/…/帳密.md` 這類檔名像密碼的頁，`convert` 會拒絕（`檔名像密碼或金鑰`），這裡整份切進 `.system/temp/adopt/` 讓子代理讀；（b）`mask_secrets` 的關鍵字表沒有 `pwd`、`Bearer`、PEM 私鑰區塊、`解鎖碼`，也不認沒有關鍵字的身分證、信用卡號。私鑰會整段原文留在知識庫暫存，之後 `temp_keep_hours` 才清 | `<O>` 加 `wiki/projects/密碼清單.md`（內容 `網銀: Zq8kd91mm`）與一頁含 `pwd: Zq8!kd91xx`、`Authorization: Bearer eyJ…`、`-----BEGIN RSA PRIVATE KEY-----…`、`解鎖碼 Zq8kd91pp`、`信用卡 4111 1111 1111 1111`、`身分證 A123456789` 的頁，跑 `build adopt <O> --extract`，這些全留在 `001.txt…` 裡（有關鍵字的 `密碼：`、`pw`、`token`、`secret`、`passwd`、`帳號` 都遮到） | 切段前套 `credentials.looks_like_filename`（跳過並記一句「某某頁檔名像密碼，沒讀」）；遮值補 `pwd`、`bearer`、`-----BEGIN … PRIVATE KEY-----` 到 `-----END` 整段、`解鎖碼`；身分證、信用卡號看要不要靠「只記種類」那條（設定檔第三節）而不靠遮值，至少在報告「讀不到的」寫出被跳過的頁 |
| S3 | 低到中 | 抽樣用 `dispatch --ids` 開的 job 會記著 `ids`；抽樣做完、AI **沒有再叫一次 `dispatch`** 就 `done sample`、進全量整理時，第一次 `cards dispatch` 還受 `ids` 限制：派不出東西、回 `done: true`、`say: 整理完了：做好 4 份`，但還有 38 份沒做（再叫一次才切出 20＋18）。`done sample` 沒有前提檢查，也不放掉那個 job。`--ids` 帶不存在的編號在 job 還開著時不會被拒（只有新開 job 才驗） | 腳本：`start` → `folders add` → `done setup|inventory` → `cards dispatch --ids <4 個>` → 假製卡助手做完 → `done sample`（不叫 dispatch）→ 新一句 `cards dispatch --json` → `done: true`、`say 整理完了：做好 4 份`、`cards todo` 的 `new` 還有 38。真跑時 AI 每次收到通知都會再叫 dispatch，所以沒踩到；`done cards` 也會擋回「還有 N 份」，AI 能自己救，但客戶會先聽到一句「整理完了」 | `done sample` 時順便關掉這個 session 的 ids job（或 `dispatch` 在 ids 的項目全做完後把 job 收掉、這次當新的 job 切）；`done sample` 加前提「沒有在跑的抽樣批次」 |

## 低等級（不擋）

| # | 問題 | 重現／證據 | 建議 |
|---|---|---|---|
| S4 | 被「已經是建好的知識庫」擋下的那一句，AI **改用簡體中文**講：`这里的知识库已经建好了，不用再跑一次建置。…` 違反「一律繁體中文」 | 真跑第三個對話 `/wiki-with-willy:build`（`build status` 回 exit 3 那句後，AI 自己重寫）；只出現這一次（本輪 43 段主對話文字裡的 1 段） | SKILL.md 的「印的是一句話就照講」改成「原句照貼」，不要重寫 |
| S5 | 通知之後的轉場句偶爾英文：B-412 長指示那次 2 段（`Three case names confirmed; checking the plan again for the case summa…`、`Verified. Recording the log entry.`）；真跑 43 段裡 0 段 | `bbrun_long1.jsonl` | 跟 card-maker 測試的同一個老問題；build 的 SKILL 與 ingest 的「讀派工結果」都可以再加一句「通知之後只說 `say`，不說別的」 |
| S6 | PDF 子行程出錯時，`_scrub` 只換密碼原字再 `mask_secrets`；錯誤訊息裡若是密碼的 `repr`（含 `\` 或同時有單雙引號的密碼 `Pw#9x'q\z` → `"Pw#9x'q\\z"`）就漏遮，寫進 `hook-errors.log` | 假工具夾的 pypdfium2 丟 `RuntimeError('bad %r' % password)` → log 裡有 `bad "Pw#9x'q\\z"`。真的 pypdfium2 訊息不帶密碼，實際機率很低 | `_scrub` 另外換掉 `repr(password)[1:-1]` 與 `json.dumps(password)[1:-1]` 兩種形式 |
| S7 | `done cards`／`done synthesis` 讓「製卡助手回報沒做成」的項目通過（`_given_up`，D-196 的設計），但 `build report --facts` 的數字與「讀不到的」只算待處理裡的 unreadable，**沒做成的卡（例如綜合卡寫不出來）不出現在報告**，客戶不會知道 | 腳本：3 張綜合卡被打回後，`done synthesis` 過、報告數字「綜合卡」仍照磁碟上有幾個檔算 | `report --facts` 加 `given_up`（各 job 的 `failed`），範本「要你決定的」提示寫上 |
| S8 | adopt 批次「不寫任何檔」只是文字：card-maker 的工具共用 Write／Edit，守門對已登記子代理寫 `檔案索引/` 底下是放行的（寫卡要用）；adopt 的段檔（`.system/temp/adopt/`）本身寫不了。另外 `card-maker.md` 回報段寫「不寫…人名、金額、日期（D-022）」跟 adopt 要回報實體與日期衝突（真跑時製卡助手照 adopt 格式回了，沒出事） | guard.py 餵 payload：Write `檔案索引/x.md` allow | 守門看批次種類（adopt 的 agent 不放行 Write），或在測試鎖住這是接受的缺口；兩句 D-022 限定「非 adopt 批次」 |
| S9 | 在已經建好的知識庫裡，`build facts`、`build report --facts|--from`、`build adopt --check|--extract` 都不擋（只有 `status|done|redo|set` 擋）。`adopt --extract` 在平常的知識庫也會寫 `.system/temp/adopt/`。讀碼：舊 wiki 的 `hot.md`／`log.md`／`agenda.md`／`CLAUDE.md` 沒檢查是不是連結（wiki 頁有檢查）| `t15.py` | 看要不要統一擋；`_special` 回傳前加 `is_symlink` 檢查（讀碼，未實測連結） |
| S10 | 兩個視窗同時 `start` 同一個空夾，慢的那個印 `沒有寫入：PermissionError: [Errno 13] Permission denied: 'C:\\…\\.system\\sources.json.tmp'`（帶絕對路徑，一般人看不懂）；終端機（沒有 `--session`）跑 `build status` 時，會讀 `current.json` 的 session，別的視窗剛好是目前回合就被當成「另一個對話」擋下（`--takeover` 可解） | `t14.py` 第 9、6 項 | `start` 在 `init` 前先拿鎖；終端機沒 `--session` 時不認 `current.json` |

## 沒問題但值得記的

- 真跑時 AI 自己讀 `ingest/SKILL.md`「讀派工結果」那段來派背景批次，build 的 SKILL 只寫「照 ingest skill 的…」沒要它載入，它是用 Grep 找到再 Read 的，多兩個來回；結果沒錯。
- 名字題的「掛了 N 份檔」措辭、`accept`、`names` 步、`ai_judged` 當確認：真跑與 B-412 都對。
- `fixture` 說明：舊 wiki 的 `hot.md` 提到的「業主電話不寫進 wiki 頁」，AI 判斷設定檔已有，沒列給客戶；沒有出問題。
- `entity accept` 對已是 AI 判斷的再叫一次，回的是 `已登記：…（AI 判斷）` 而不是「已經確認過了」，spec 只規定已確認的那句，沒衝突。

## 回測第一輪

日期：2026-10-24
測的版本：`f7c65d3`（`6e8a502..f7c65d3`：`d75a14f` 主體、`f7c65d3` 補修）；跑前樹乾淨，沒改任何程式。
範圍：22 派的 7 項（S1、S2、S3、S4～S10、plugin-dev 文字、真跑加 B-412、validate）。

### 結論

- **S1、S3、S4、S6～S10 都過；S2 有 1 件沒過、1 件新發現；S5 沒完全好。** 沒有新的高等級問題。
- 沒過的兩件：
  - **R1（中）`帳密.md` 沒被跳過。** 密碼樣式只有 `*密碼*`、`*password*`…，沒有 `帳密`；真跑時它整份被切進 `.system/temp/adopt/004.txt`，內容（假值）原文在裡面，製卡助手讀到了。`密碼清單.md` 有跳過。
  - **S5（低）通知之後的英文轉場還有。** B-412 長指示那次又出現 1 段：`Back to getting the plan, since confirming names can bring up new steps.`（出現在 names 之後、不是 dispatch 的 `say` 那一段）。
- 新發現 2 件（低）：R2 連結點過去的夾（junction）會被跟進去讀；R3 終端機的 `build status|done|redo|set` 在鎖空著或過期時會認領成「終端機」，之後對話那邊反而被擋 30 分鐘。
- 全部測試：exit 0，**Ran 2339，OK（skipped=2）**，543 秒；`claude plugin validate` 通過（警告同前）。
- 真跑（`claude -p`，刻意走接手建置，舊 wiki 有密碼清單頁）一路走到報告，**接手之後 `done cards` 立刻過**，沒再被舊批次擋。
- settings.json 跑前跑後 sha256 相同：`8ae82f7a69eea7b8…`（跑前全文存在 scratchpad `settings_before_build_r1.json`）。

### 逐項

| # | 結果 | 證據 |
|---|---|---|
| S1 接手 | **過** | 腳本：A 派 3 批（20＋20＋2）、子代理認領後 B `status --takeover` → 那 3 批 `released`、A 的 job `closed`（`released_by_takeover`）；B 的 `cards status` 與 `build done cards` 說同一件事（沒在跑、還有 42 份）；B `dispatch` 切出同樣 3 批；做完 `done cards` 過。鎖過期、沒用 `--takeover`、舊批次 45 分鐘：B 照樣切得出來、`done cards` 只看自己的批次。真跑：第 9 輪 70 秒砍掉 claude（兩批 open、子代理已死）→ 新對話 `build` 被擋（`另一個對話正在建置（全量整理，最後動作 01:02）…`，AI 原句照貼）→ 說「接手建置」→ 剩下 26 份重派（兩批 20＋9 加 2 張綜合卡）→ 收完通知後 `build done cards` **第一次就過**。接手後原對話再動 → 被擋 |
| S2 密碼頁跳過 | **部分過（R1）** | `密碼清單.md`、`wiki/credentials.md`、`wiki/topics/credentials.md`、`id_rsa.md` 都跳過，列在 `adopt --check` 的 `skipped`、`--extract` 的 `skipped`（人讀多一行「檔名像密碼、沒讀：…」）、`report --facts` 的 `adopt_skipped`（數字段有「舊 wiki 裡檔名像密碼、沒讀的：…」）；真跑報告「讀不到的」也寫了。**`帳密.md` 沒跳過**（見 R1） |
| S2 遮值 | **過（有誤傷，已記）** | `pwd: …`、`PWD=…`、`Authorization: Bearer …`、`解鎖碼 …`、`解鎖碼：…` 都遮；私鑰 `-----BEGIN RSA PRIVATE KEY-----` 到 `-----END…` 整段遮、**沒有 END 的 `BEGIN OPENSSH PRIVATE KEY` 遮到檔尾**；CLAUDE.md 與 hot.md 裡的也遮；舊 vault 雜湊不變（比對 fixture 與整棵樹）；公鑰區塊、`cwd`、`load-bearing wall` 沒被誤遮 |
| S2 「寧可多遮」的誤傷 | 記下（低） | 正常句 `今天的 pwd 命令列出目前資料夾` 變成 `今天的 pwd ███。`（命令名後面那一串被吃掉）；`bearer of bad news` 變成 `bearer ███ bad news`。設計師的舊 wiki 出現這兩個字的機率很低，影響是少一個詞，不是漏 |
| S3 | **過** | 抽樣批次還在跑時 `done sample` → exit 3 `抽樣還在背景做，做完再往下`；job 開著時帶不存在的編號（單獨或混在好的裡）→ exit 3 `這幾份不在要整理的清單裡：…`；同樣的編號再給一次不報錯（`沒有新派的`）；`done sample` 後第一次 `cards dispatch` 切出 20＋18（不再說「整理完了」），`cards todo` 的 new 38 份；先叫一次收尾 dispatch 再 `done sample` 的老路也對 |
| S4 原句照貼 | **過** | 真跑：新對話被擋時 AI 只貼了那一句（沒有重寫、沒有簡體）；兩輪真跑共 73 段給客戶的文字，簡體 0、英文自言自語 0、步驟編號 0、內部編號 0 |
| S5 通知後只說 `say` | **部分（低）** | 真跑：收到通知後的主對話文字是 `做好 10 份了；還在做 1 批`、`做好 29 份了；還在做 1 批`、`交給製卡助手在背景整理，好了跟你說；這期間照常問我`，都是 `say`。B-412 長指示（328 秒，先整理、dispatch 4 次、派 2 次、18 張卡＋3 張綜合卡、三個名字都 `ai_judged`、`asks` 空）：6 段主對話文字裡 **1 段英文** `Back to getting the plan, since confirming names can bring up new steps.`（names 之後、派綜合卡之前）；其餘 5 段都是中文，沒有 `hook-errors.log`、沒有權限拒絕。這個轉場發生在「名字登記完要再拿一次計畫」的時候，SKILL／ingest 都只管 dispatch 的 `say` |
| S6 repr 密碼 | **過** | `Pw#9x'q\z` 的原字、`repr`、`json.dumps`（含 `ensure_ascii=False`）形式都被換成 `＊＊＊`；帶非 ASCII 的前綴也行 |
| S7 `given_up` | **過** | 12 份被打回後 `report --facts` 的 `given_up` 有 12 筆（`id`、`kind`、`reason`、`name`），人讀數字段多一行 `製卡助手沒做成 12 份：…`；沒有失敗時沒有這行 |
| S8 adopt 子代理 | **過** | 認領 adopt 批次的子代理：Write、Edit、MultiEdit、NotebookEdit 都 deny `製卡助手不能做這件事`；Read 段檔、`cards batch` 仍 allow。認領做卡批次的子代理 Write／Edit `檔案索引/…` 照常 allow |
| S9 建好的知識庫 | **過** | `done` 之後與沒有 build.json 的一般知識庫：`status|facts|report --facts|report --from|set|redo|done|adopt --check|adopt --extract` 全 exit 3 `這裡已經是建好的知識庫，要加資料夾、整理檔案直接跟我說`；`env` 仍可跑；空資料夾 `adopt --check` 仍可跑。`hot.md`／`log.md`／`agenda.md`／`CLAUDE.md` 改成不跟檔案連結（符號連結沒法在這台建，用讀碼加單元測試）；**目錄連結（junction）沒擋，見 R2** |
| S10 並行 start／終端機 | **過（R3 新發現）** | 並行 `start` ×4 輪：慢的那個回 `另一個對話正在建置（訪談，最後動作 00:47）…`、沒有路徑；終端機（沒 `--session`）在別的對話持鎖時 `build status` → 被擋（不再被當成同一個對話）、`env`／`facts` 不動鎖 |
| plugin-dev 文字 | **大致過** | 見下 |
| 真跑 | **過**（R1 除外） | 見下 |
| validate | **過** | `Validation passed with warnings`（沒有 icon、README 沒有安裝行） |

### 沒過與新發現

| # | 等級 | 問題 | 重現 | 建議 |
|---|---|---|---|---|
| R1 | **中** | `帳密.md`（舊 wiki 常見的檔名）不在憑證檔名樣式裡（`CREDENTIAL_PATTERNS` 只有 `*密碼*`、`*password*`、`*passwd*`、`*secret*`、`*credential*`、`.env`、`*.pem`…，沒有 `*帳密*`、`*金鑰*`），所以 `adopt` 不跳過、`convert` 也不擋。內容沒有關鍵字時（`雲端相簿 abc / <值>`）遮值也抓不到，整份進 `.system/temp/adopt/` 讓製卡助手讀 | 腳本：舊 wiki 加 `wiki/projects/帳密.md`（`user abc / Zq8kd91nn`）→ `--check` 的 `skipped` 沒有它、`--extract` 切進去、值留在段檔。真跑：`帳密.md` 內容（`Zq8kd91ooREAL`）在 `004.txt` 原文；製卡助手回報「舊 wiki 有一頁帳密（值沒有抄）」，AI 對客戶說了「帳密那頁…沒有抄」，沒有進卡或口述 | `CREDENTIAL_PATTERNS` 加 `*帳密*`、`*金鑰*`（這是守門共用的樣式表，spec 不准動 `guard.py`／守門，所以請設計 session 決定；或只在 adopt 另加一份舊 wiki 專用樣式） |
| R2 | 低 | 舊 wiki 的 `wiki/` 底下若有目錄連結（Windows junction），`rglob` 會跟進去，連結外面的檔被切進段檔（`is_symlink()` 對 junction 回 False） | 腳本：`mklink /J <舊wiki>/wiki/junc <外面夾>`，外面夾放 `外檔.md` → `--extract` 的 `files` 有 `wiki/junc/外檔.md`，內容在段檔 | `_wiki_pages` 對每一層用 `os.path.realpath` 與 `root` 比（跟 `_plain` 同一招），或跳過 junction |
| R3 | 低 | 終端機（沒有 `--session`）跑 `build status|done|redo|set`，在鎖空著或過期時會把鎖認領成 `終端機`，之後原本的對話再動就被擋 30 分鐘（要說「接手建置」才解開） | 腳本：`start` 後（鎖空著）終端機 `kb build set people x` → `owner: 終端機` → 對話 A `build status` → `另一個對話正在建置…` | 終端機只讀（`status` 不認領），或終端機的寫入不覆蓋對話的 `session_id` |

### plugin-dev 文字（`plugin-dev:skill-reviewer`，14 項逐項對照）

審查員只讀檔、無 shell；字數是估的，我另外量過：SKILL.md 本文現在約 2050 字（≤ 3000）、`steps.md` 70 行、`adopt.md` 29 行、`build-report.md` 20 行。

- **已修**：`then` 的出處（steps.md 說明它在「現在做」那個結果裡）、`--takeover`（SKILL 分辨多一條）、`ruling`／`statement` 的旗標與實體「無」的做法、`profile add` 欄名改指向 `kb profile show --json`（steps.md）、沒資料夾又記了舊 wiki 的走法、`done sample` 的前提、抽樣列進 SKILL 骨架且 id 來自 `cards todo`、「你」「她」稱呼、原句照貼與「只說 say」兩條原則、報告範本多了舊 wiki 被跳過、`given_up` 的提示。
- **還部分／沒修（都低）**：
  - references 裡的 `${CLAUDE_PLUGIN_ROOT}/…` 路徑（steps.md:23、:68、adopt.md:23）在 Read 進來的檔裡不會被代換；SKILL.md 的備援只講 kb.py。
  - adopt.md 還寫死 `kb profile add 偏好 偏好=<一句>` 與 statement／ruling／agenda 的旗標。
  - `八項` 沒講 python 太舊、kb 起不來、`kb build start` 被擋怎麼說；sac「評估中」沒提。
  - 還有重複寫的（三句重點、報告 Write 路徑、不講英文不唸編號）。
  - steps.md 的「ingest 的 carding.md 第十一節」「照 ingest 自己做」沒給路徑。
  - SKILL.md 的 description 沒列「收舊 wiki」；報告範本「讀不到的」沒提 sac 擋住功能。
- **審查員新提的（低到中）**：
  - 日程那行永遠帶 `--entity <名>`，但候選的實體可能是「無」（`--entity` 對日程是選填）；
  - 「份數多那題在盤點時已經說好」，但盤點那節沒要 AI 問；
  - `done` 的 `建置：X 做完了，下一步 Y` 要不要原句講沒寫；
  - env 有 `fail` 時可不可以 `build start` 沒寫；
  - 抽樣的 `new` 少於 10 或空（重新抽樣）沒寫。

  真跑中這幾點都沒造成問題：AI 自己加了 `--entity`、自己問了份數多那題或直接做。

### 真跑（`claude -p`，第二輪）

fixture：`llm-wiki-test/build-e2e2/`：`來源` 46 份（含 1 份有密碼 PDF、2 份舊 `.doc`、`私人/`、`公司行政/薪資/`）、舊 wiki（`tests/fixtures/old_vault` 加 `wiki/projects/密碼清單.md`、`帳密.md`）、`外部案子`。第一個對話跑到全量整理，第 9 輪 70 秒時砍掉 claude；新對話打 `/wiki-with-willy:build` → 被擋 → 「接手建置」→ 走完。

| 步 | 秒 | 結果 |
|---|---|---|
| 環境→建→訪談（4 輪） | 22＋17＋21＋39 | 逐項中文講，sac 只提醒、不建議關；訪談一次講完 5 點，**AI 自己注意到薪資夾與「密碼清單」那頁（檔名像密碼，不讀）**問客戶 |
| 登記、盤點 | 45＋22 | 3 個資料夾、2 個 `exclude --kind hidden`；盤點 41 份、讀不到 3 份、約 5 分鐘、一句用量；AI 主動更正「薪資夾 3 份」是只算檔名有「薪資」的，實際拿掉 5 份 |
| 抽樣 | 221＋75 | 挑 12 份（跨夾跨格式），列「我猜的」四項；客戶改完（工程、請款、同一家、別名）寫進去，`done sample` |
| 全量整理（被砍） | 70 | 派 2 批（20＋12）後 claude 被砍 |
| 新對話 | 9 | `build status` 被擋，原句照貼：`另一個對話正在建置（全量整理，最後動作 01:02）；那個對話還開著就回那邊繼續，已經關掉了就說『接手建置』` |
| 接手 | 39 | `status --takeover`、看 `cards todo`，講剩下 26 份與讀不到 3 份，問要不要開始 |
| **全量整理（接手後）** | 409 | 派 2 批（20＋9）＋綜合卡 2 張，收到兩次通知都只說 `say`（`做好 10 份了；還在做 1 批`、`做好 29 份了；還在做 1 批`），**`build done cards` 第一次就過** |
| 名字 | 36＋36 | 直接 `entity accept` 3 家廠商（`明亮燈飾`、`大成磁磚`、`順發水電工程行`），有疑慮的 1 題（大安陳宅跟「陳宅」是不是同一件）列出並教怎麼回；答完 `entity merge`、`rename`、`done names` |
| 綜合卡 | 105 | 派 1 批 4 張，`done synthesis` |
| 收舊 wiki | 74＋42 | `--extract` 切 7 段（跳過密碼清單）、派 2 批；收回報、跟 `statement list`／`agenda list`／`entity list` 比對後列 9 條分四類（包含「帳密那頁有，值沒抄」）；客戶「1 到 6 都記，尾款 1/15，第 8 條放著，帳密不存」→ 記 4 條口述、2 條偏好、1 條日程；其中第 5 條找不到第一版的卡就改記成口述（AI 自己說明） |
| 報告 | 42 | 六段齊；數字段有「舊 wiki 裡檔名像密碼、沒讀的：wiki/projects/密碼清單.md」；對話講三句重點、問要不要試問 |

- 給客戶看的文字（兩輪真跑共 30 段加上第一輪的 43 段）：簡體 0、英文自言自語 0、步驟編號 0、內部編號 0；自己跳步 0。
- 權限：沒有被擋的客戶動作。製卡助手有 2 次想 `grep carding.md` 找章節被守門擋，改用 Read，無影響；主對話有 1 次 `PYTHONIOENCODING=… python -c …` 被「requires approval」擋，改用 Read 同一個檔，在桌面版會跳一次要不要允許。
- `hook-errors.log` 不存在；舊 wiki 一個檔都沒變（只比 fixture 多我加的兩頁，雜湊與內容都沒變）；來源夾只多我放的私人帳單；`私人`、`薪資` 沒有任何東西進清冊、卡或暫存。
- 花了多久：claude 實際跑約 23 分鐘（牆鐘 00:55～01:18，接手之後 21 分鐘走完）。
- 沒做到：同第一次，不能在桌面版關視窗（用砍行程代替）、舊 llm-wiki 開著只用替身驗。
