# answer：測試報告（第一輪）
日期：2026-10-21
給誰看：設計 session（決定要不要修、要不要發行）。
什麼時候更新：每輪測試後。

被測：worktree `llm-wiki-j-answer`，分支 `spec/answer`，HEAD `54580ef`（merge-base `dd5019d`）。spec 以 main 的版本為準（含「note 改知識實作補的介面」與「實作回報裁定」1～11，main HEAD `20b719a`）。
方法：驗收 20 條逐條自己跑 CLI 與 hook（黑箱；環境見下）；code-review（high）與 security-review 派唯讀子代理列疑點，只讀碼、沒讀測試，有重現價值的我自己重跑；skill 審查用 `plugin-dev:skill-reviewer`、plugin 審查用 `plugin-dev:plugin-validator`；第 17 條用 skill-creator 的 `run_eval`；第 18 條有無 skill 對照用 headless `claude -p`。沒改任何程式（只有為了裁定 10 暫時把程式改壞再 `git checkout` 還原，最後 `git status` 乾淨）。

## 結論

**驗收 20 條：19 過、1 不過（18 的 (d)）。**（逐條證據在表裡；第 17 條路由四份都過。）全部測試 `python -m unittest discover tests` exit 0（Ran 1728 tests，OK，skipped=1，357 秒），在 commit 之後、工作樹乾淨時跑。`claude plugin validate` 通過（只有 icon 與 README 安裝行兩則既有警告）。

**沒有擋發行的資料損毀或外洩。** 但有 **3 個我自己重現的中等問題**（下面 F-1～F-3）建議修完再發行；加上裁定 6（`verify_max`）本來就要第二輪做。

- **F-1【中，違反 D-155】** 裝上 answer 之後，AI 在回答「王董的電話」時發現王先生綜合卡引用的編號對不上，就**直接 `Edit` 了綜合卡**（無 skill 時是「先沒改」）。skill 說「只讀」，但沒有告訴 AI「綜合卡引用有問題時不要改、要怎麼辦」。
- **F-2【中，違反 D-156】** 按停止後，**同一個 session 之後的 compact／resume** 會再印「還有 N 份沒整理完」加整理指示，AI 會照做，等於按停止沒用。
- **F-3【中低】** `finding add` 可以對**已被「不採用」的卡**記推算；先記推算、之後才對依據卡記「不採用」，推算仍 `stale: false`，AI 會繼續引用含那張卡的數字（與「不採用的卡不引用、不算進統計」矛盾）。

另有：`tally` 邊界（F-4）、SessionStart 6 秒時限擋不住最重的一步（F-5，讀碼）、answer skill 文字與程式輸出對不上的幾處（F-6，skill-reviewer）、英文過場（F-7）。詳見「疑點」。

## 驗證環境

- `<V>`：`tests/fixtures/inventory/{vault,raw}` 複本，掃描後設定檔第一節加 業主（對象），登記信義王、內湖李（案子）、王先生（業主，別名王董）並確認。fixture 的 `案子/信義王/信義王.md` 是壞檔頭，先刪掉。
- spec 說「四張檔案卡」，我做了**五張**：信義王報價第一版（掛信義王、王先生，540,000）、內湖李報價（1,280,000）、信義王合約（尾款 320,000、單價 6000）、內湖李木作估價單（卡型「估價單」，設定檔沒有）、大安陳初步報價（第一版卡，沒有實體欄、文件日期未載）。原檔我自己寫成 `.txt`（fixture 的 pdf／docx 讀不出字）。另有信義王綜合卡、兩條口述、不採用（合約）與口述為準（信義王）兩條裁示、四條日程。
- 回合：終端機直跑時全部落在同一個回合；要測 revert、按停止的步驟，我用 `hooks/turn.py`／`stop.py` 送 UserPromptSubmit／Stop 開新回合。PostToolUse 用 `hooks/output_log.py` 餵 stdin。
- 驗收 18 用另一份**乾淨 `<V>`**（0 份沒整理、1 件逾期×2、1 件矛盾）；第一次用有 12 份沒整理的庫跑，裝上 skill 的 AI 照 D-156 先整理了 66 輪才回答，對照被污染，所以重建（見第 18 條）。

## 逐條

| # | 結果 | 證據（摘要） |
|---|---|---|
| 1 | 過 | `lookup 王董 --json`：實體王先生（別名王董）、卡 1 張、口述 1 條、`unsorted` 12（與 `cards todo` 第一行同數）；`lookup 信義王` 有綜合卡路徑、卡 2、口述 1、裁示 2、日程 3、推算。人讀第一行 `信義王（案子，進行中）：綜合卡有、檔案卡 2 張（1 張你說別信）、口述 1 條、裁示 2 條、日程 3 條；還沒整理 12 份`，不印編號。前後全樹雜湊相同（`ff04150d130cc89c`）。 |
| 2 | 過 | `lookup 大安陳`：`entity` null、`mentions` 有第一版卡（`where: 標題`）、`mentions_total` 1；`lookup 沒這個`：全空、人讀 `沒有登記這個名稱，也沒有卡提到它；還沒整理 12 份`。另測 `../../x`、空字串、`$(echo hi)`、5000 字：都是同一句、exit 0、不崩潰。 |
| 3 | 過 | `lookup 信義王`／`cards list`：合約 `ruled_out: true`，口述為準那條 `rulings` 排第一（`spoken_wins` 在 `ruled_out` 前）；改內湖李報價原檔＋`inventory scan` → 該卡 `stale: true`（人讀〔原檔改過〕）；`cards redo` 另一張 → `stale: true`、`redo: true`（〔要重做〕）。 |
| 4 | 過 | `cards list --type 報價 --year 2026` 只列信義王報價第一版、內湖李報價（估價單、合約、未載的不在）；`types` 有 `估價單`、`in_profile: false`；`undated` 1，人讀「沒寫日期的 1 張，沒算進這一年」；`--entity`、`--source`、`--year abc`（exit 2：`年份要寫成 YYYY：abc`）也正常；`--type 估價單` 字面篩有效。 |
| 5 | 過 | `finding add --entity 信義王 --text … --how … --basis f-… f-…`（也可重複 `--basis`）→ `已記下推算：信義王報價加合約尾款共 860,000 元（信義王；依據 2 份）`；`findings.json` 一筆；`cards todo --json` 的 `synthesis_stale` 有信義王，`added` 有 `source: "finding"`，項目帶 `findings`；header 多 `c-… -`（排在 `f-` 前）。照新 header 寫綜合卡 → PostToolUse 無輸出；少了 `c-` 行 → `綜合卡有錯：整理到不是現行的，重跑 kb cards todo 拿新檔頭。改好再寫一次。`。拒絕 exit 3、一字不差：`實體清單沒有「沒這個」，先 kb entity add`、`依據至少要一張卡（--basis）`、`清冊沒有 f-00000000`、`這份還沒有檔案卡`、`推算要一句話，最多 200 字`（text 與 how 各測，換行、空字串同）、`推算裡不能有 " $ ` \ 這四種字元`（四種各測）、`沒有這筆推算：c-00000000`（withdraw 與 --replaces）、`狀態檔讀不到：.system/findings.json（JSON 壞了：…）`。另測密碼：`推算裡不要放密碼或帳號；這種要記的話等「密碼與守門」那一版`。「等不到鎖」那句我沒重現：新建一個空鎖檔，CLI 等 30.1 秒後把它當殘留（`stale`＝`timeout`＝30）清掉、照寫成功。 |
| 6 | 過 | 依據卡原檔改、`scan` 後卡還沒重做：`stale: false`（照 spec 順序）；重做卡後 `stale: true`。給信義王多掛一張新卡 → `stale: true`。客戶自己（`關於你` 登記「林小姐設計」）的推算：庫裡多一張任何卡（掛內湖李）→ `stale: true`。 |
| 7 | 過 | `--replaces c-…`：`已改：舊 → 新`；舊 `superseded`、`replaced_by` 對；todo `removed` 有舊、`added` 有新。`withdraw` → `已收回：…`、withdrawn、`removed` 有它。改被取代的 → `這筆已經被取代了，改最新的那筆`；改已收回的／再收回 → `這筆已經收回了`（exit 3）。 |
| 8 | 過 | `tally --row "f-… 1,280,000 第 3 頁" --row "f-… 54萬 總表"` → 表格照格式、`共 2 筆，合計 1,820,000，平均 910,000`、不印編號；`--json` 有 `rows`、`total`、`average` 與 `_text`。`約一百萬` → `看不懂這個數字：約一百萬`；`54 萬`（分開寫）→ `看不懂這個數字：54 萬`；小數、負號、`1.5萬` 都對（`-1,000.5`＋`0.1`＋`0.2` → 合計 -1,000.2、平均 -333.4，無浮點誤差）；`1,2,3`、`1e5`、全形 `１２３` 拒絕；`清冊沒有 f-00000000`、`這份還沒有檔案卡`、`至少要一筆`。邊界問題見 F-4。 |
| 9 | 過 | `pending add --kind conflict --card A --card B --entity 信義王 --text …` → `已記進待處理：…`；`待處理.md` 有 `## 兩份寫法不同的（1）` 一條 `（「信義王報價第一版」「內湖李報價」，2026-10-21）`；反過來 `--card B --card A` → `已經記過了：…`；同一張兩次、只給一張 → `兩張要是不同的卡`；`這份還沒有檔案卡`、`清冊沒有 f-00000000`、密碼 → `說明裡不要放密碼或帳號；…`（exit 3）。`kb asks --json` 的 `asks` 空、`tell` 是 `['還有 1 件兩份寫法不同的，在待處理']`。 |
| 10 | 過 | 見下節「journal」。`--changed --since` 疊加、`--find 王董` 找得到、「改了」照分類（`綜合卡 信義王；檔案卡 1 張`、`日程`、`口述`、`裁示`、`實體清單`）、程式段寫 `你在終端機跑的：kb agenda add`。 |
| 11 | 過 | `agenda list --from 2026-10-21 --to 2026-10-28 --entity 信義王` → 只有 10/23 會議、10/25 水電；`--entity` 可重複；done 的不列；期間條目重疊也列；壞日期 → `日期要寫成 YYYY-MM-DD，而且是真的日期`（`今天` 這種字不收，spec 裁定 4）；未登記實體 → `實體清單沒有「沒這個」，先 kb entity add`。小點：`--from` 大於 `--to` 不拒絕，篩完空時印「日程是空的」（略誤導）。 |
| 12 | 過 | 見下節「hook」。 |
| 13 | 過 | 開新回合後 `versions revert`：退第 9 步（conflict）→ `已退回 … 還原 1、移除 1`，全樹（排除 versions／turns／journal）與基準位元組相同；退第 5 步（finding add＋寫綜合卡，PreToolUse 備份）→ `還原 1、移除 1`，`findings.json` 沒了、綜合卡回去，與基準相比只多一個 `.system/guard-status`（guard hook 每日一次的紀錄，不是狀態）。 |
| 14 | 過 | `lookup`（四種結果）、`cards list`、`finding add|list|withdraw|--replaces`、`tally`、`pending add`（兩種）、`journal recent`、`agenda list` 的人讀輸出共 99 行，掃 `[aefsrcp]-[0-9a-f]{8}`：除了我自己 echo 的指令標頭外 0 筆；`--json` 都有編號（`lookup`、`cards list`、`finding list`、`tally`）。（`pending list` 人讀印 `p-…` 是既有行為，不在清單內。） |
| 15 | 過 | `rules/synthesis.md`：開頭句「note、lint 也讀；answer 不寫綜合卡」、S-11 多〔c-…〕、S-17 寫 `findings` 每筆照寫帶〔c-…〕、`stale` 寫「依據有變，待重算」；rules 測試全過。 |
| 16 | 過 | answer 描述 133 字（≤150）、本文 2074 字（≤3000）；分辨與原則含 D-153、D-155、D-159、D-160 與理由；本文沒有參數對照表；`find.md` 72 行、`verify.md` 78 行（≤150）；`tests/evals/answer.json` 20 句 10／10，不該觸發含例句 12、15～18、20 與「幫我檢查一下」「整理一下」。note：多了「記的是剛才的回答」那一條、`record-answer.md` 36 行（≤60）、description 與 `dd5019d` 逐字相同。 |
| 17 | 過 | `answer.json` 20/20：該觸發 10 句各 3/3（30/30＝100%）、不該 10 句各 0/3（0/30＝0%）；`note.json` 20/20（30/30、0/30）、`ingest.json` 20/20（29/30、0/30）、`manage.json` 20/20（28/30、0/30）；四份 `query failed`／WinError 都是 0。細節見最後一節。 |
| 18 | **不過**（(d)） | 見「第 18 條」：(a)(b)(c)(e) 過，(d) 裝上 skill 的 AI 沒帶 `--changed`（因為它根本沒讀 `find.md`）。 |
| 19 | 過 | `claude plugin validate` exit 0；`plugin-dev:skill-reviewer` 看過 `skills/answer/` 與 `skills/note/` 改動（結論「通過但要修」，見 F-6）；`plugin-dev:plugin-validator` PASS（見「plugin-dev 審查」）。 |
| 20 | 過 | `python -m unittest discover tests` exit 0（Ran 1728，OK，skipped=1），樹乾淨；README 加「問資料」一段、指令清單加 `kb lookup`、`kb cards list`、`kb finding`、`kb tally`、`kb pending add`、`journal recent`／`agenda list` 新參數；repo `CLAUDE.md` 地圖加 `lookup.py`、`findings.py`、三個 cli、`skills/answer/`。小缺：`CLAUDE.md` 第 79 列（ingest）還寫「其餘三個 skill 尚未」，answer 有了該是「其餘兩個」（F-8）。「不可以動」清單的檔（guard、credentials、cmdparse、versions、其餘三支 hook、hooks.json、core.md、ingest／manage、既有三份 evals、agenda、profile、statements、rulings、entities、plugin.json、CHANGELOG、design）`git diff --stat dd5019d..HEAD` 皆無變動。 |

### journal（第 10 條細節）
預設全列；人讀每段 `<時間>｜你說：…`／`　結果：…｜做了：…`／`　改了：…`；`--changed` 只列有改動的；`--find 王董` 比到 prompt 含王董的段與 `kb entity alias` 的程式段；`--since 2099-01-01` → `沒有符合的段`；`--since 2026-10-22 --changed` 同；`--find 王董 --changed` 疊加只剩有改動的那段；壞日期 exit 2。路徑分類實測：`案子/信義王/信義王.md` → `綜合卡 信義王`、`檔案索引/…` → `檔案卡 1 張`。

### hook（第 12 條細節）
- 同一個 session：一句跑 `cards todo` 後沒 Stop、下一句送 UserPromptSubmit → 輸出 `additionalContext`（`上一句的整理被你停了：這個對話不再自動整理，你說整理才做；回答用現有的資料，末尾一句還有幾份沒整理。`）一次，session 檔記 `ingest_stopped: true`；Stop 後第三句不再印；上一句中斷但沒跑 todo → 不印；有跑 todo 但有 Stop → 不印。
- 壞檔：session 檔 `{ garbage`、`current.json` 內容 `xx`、`s5.json` 是目錄、空 stdin、壞 JSON：全部 exit 0，仍開新回合（目錄那個寫 `hook-errors.log`）。
- SessionStart：清冊 12 份沒做卡、沒有新變動 → 狀態行 `還有 12 份沒整理完` 後接 `有變動：先照 ingest skill 整理（開始前照 announce 說一句），整理完再完整回答客戶；超過 20 份不整理，先用現有資料答、末尾問要不要整理。`；空庫 0 份 → 不印；22 份 → 印 `還有 22 份…`（指示句含「超過 20 份不整理…末尾問」）；有新變動 → 印 `變動：新增 10…` 不印「還有」。「算份數超過 SessionStart 時限就不印」只有單元測試覆蓋（`_START` 往前撥），我沒有造出真實超時的庫。F-2 的重現也在這節：`ingest_stopped` 之後同 session 的 `compact` 照印 `還有 22 份沒整理完`。

## 裁定 8、10 的檢查

- **裁定 8（既有測試改動沒有放寬）：過。** `git diff dd5019d..HEAD -- tests` 只有三個既有檔有刪行：`test_hooks_session.py`（`test_no_change_no_hint` 先 `skip_all()` 讓清冊沒有未整理、再斷言沒有 `有變動：` 也沒有 `還有 ` 行，等於原本的斷言換到新語意下仍在；另加兩個新測試覆蓋 9b）、`test_scripts_journal_cli.py`（舊的「程式｜做了」格式斷言改成新格式的**精確整行字串**斷言，行數 3→6 也是新格式兩行一段）、`test_skill_note.py`（拿掉「把剛才的答案記下來」必含、改成必須不含，是裁定 2 的意思）。沒有 `skip`、沒有放寬成 `assertIn`／`assertTrue`。
- **裁定 10（先寫程式後補測試的五份，改壞程式看測試紅不紅）：過，11 處突變全紅。** 每處改壞一行、跑對應測試、`git checkout` 還原：`findings.stale` 不比集合雜湊（紅 5 個）、不比指紋（紅 1）、`--replaces` 把 `superseded` 改 `withdrawn`（紅 3）；`lookup` 口述為準不排前（紅 1）、`ruled_out` 不標（紅 2）；`cards list` `--year` 反向（紅 2）、`in_profile` 永遠 true（紅 3）；`tally` 萬乘 1000（紅 4）、合計少算最後一筆（紅 3）；`finding` CLI 成功句改字（紅 2）、收回句改字（紅 1）。最後 `git status` 空。**但測試沒覆蓋下面的 F-1～F-4。**

## 第 18 條：有無 skill 對照

做法：`git archive HEAD` 展開兩份 plugin（原樣、拿掉 `skills/answer/`），同一份乾淨 `<V>` 的複本（每次一份新的）、headless `claude -p`（`--plugin-dir`、`--settings` 把舊 `llm-wiki` 關掉、`--allowedTools "Bash Read Grep Glob"`），同五句各跑一次。`kb` 是 `python "<plugin>/scripts/kb.py"` 的縮寫。每個情境的指令序列（Read／Grep 簡寫）與回答要點：

| 情境 | 裝上 answer | 沒有 answer |
|---|---|---|
| (a) 王董的電話幾號 | `Skill answer` → `kb lookup "王董"` → Read 綜合卡 → Grep → `kb cards list --entity 王先生` → Grep → `kb convert <卡>` → Read 原檔 → **`Edit` 王先生綜合卡（把引用的編號改成對的）**（F-1）→ `kb asks --json` → `kb turn note`。14 輪、33 秒。回答：王董＝王先生、0912-345-678、出處短標「信義王報價第一版 2026-08-12」、**不印編號**、說設定只記種類所以回原檔；末尾照 asks 問兩件逾期、「還有 1 件兩份寫法不同」。 | Grep → Glob → Read → Grep → `kb asks` → Read×4（**沒用 lookup**，直接翻檔）。11 輪、23 秒。回答：同一個號碼；**內文印出〔f-b0b15ec0〕與 f-7603d260 內部編號**；發現綜合卡編號錯但「先沒改」並建議下一步；asks 兩件加 conflict 一句。 |
| (b) 今年報價總共多少錢 | `Skill answer` → Read×2（skill 文件）→ `kb cards list --year 2026 --json` → `kb cards list --type 報價 --type 估價單 --json` → **英文過場「Three dated quote cards for this year, plus one undated. I'll read the cards.」**（F-7）→ Read 卡×3 → 中文粗估一句「今年有 3 張報價類的卡（報價 2 份、估價單 1 份），卡上的金額還沒核對，正在逐份對原檔」→ `kb convert`×3 → Read 原檔 → `kb tally --row`×3 → `kb asks --json`。18 輪、38 秒。回答：合計 2,030,000、逐筆表、平均 676,667〔我算的，依據上面三份〕、範圍（報價 2 ＋估價單 1，估價單是舊名）、沒算大安陳（未載日期）、沒算信義王合約（你說過別信，也不是報價）、提醒木作 310,000 與 210,000 可能重複、conflict、asks 兩件。 | Glob → `kb --help` → `kb tally --help` → Read×… → `ls`／`cat` 原檔 → `kb statement list` → `kb finding list` → `kb cards list` → 中文過場 → `kb tally`（2 筆）→ `kb tally`（3 筆）→ `kb asks`。21 輪、49 秒。回答：1,820,000（兩份有日期的）／含大安陳 2,720,000；**沒有「先粗估」那句**；沒算估價單（說會重複）；沒講卡型舊名與範圍；問了 4 題。它自己從 `kb --help` 找到 `tally`，所以加總也是程式算的。 |
| (c) 合約怎麼寫付款條件 | `Skill answer` → Read×2 → `kb cards list --json` → `kb asks --json`（沒開原檔）。7 輪、26 秒。回答：先說「查了你的檔案卡，庫裡只有一份合約卡＝信義王合約，你說過別信，所以沒引用，也沒拿它當範本」；另起一段「下面是一般的說法，不是出自你的資料」；末尾 asks、conflict 一句、問要不要代進某案另存新檔。 | 中文過場 → `kb --help` → Grep → Read 卡 → `kb asks` → Glob → `kb pending list` → **Read 合約.txt 原檔**。9 輪、30 秒。回答：「你自己的案子裡有一個現成寫法」**＝引用了客戶說過別信的信義王合約**（簽約付 30%、尾款 320,000、單價 6000），**印出 f-1f3093a1、p-3d0a62b3 內部編號**；一般寫法有標「不是出自你的檔案」；建議把「簽約付 30%」補進卡。 |
| (d) 你最近幫我改了什麼 | `Skill answer` → **`kb journal recent`（沒帶 `--changed`）** → `kb asks --json`。5 輪、14 秒。**沒有讀 `find.md`。** 回答：4 筆（待處理新增一筆、清冊三筆「日程已抽過」），說明是你在終端機跑 kb 留下的、不是對話裡改的、原檔沒動；接 asks。 | `kb journal recent` → `kb asks`。3 輪、12 秒。回答等價。 |
| (e) 有沒有要我確認的 | `Skill answer` → `kb asks --json`。4 輪、10 秒。回答：兩件逾期、編號選項、「還有 1 件兩份寫法不同的，在待處理」。 | `kb asks`。2 輪、9 秒。回答等價。 |

判讀：
- **(a)(b)(c) 裝上 skill 明顯比較好**：(a) 認別名、短標、不印編號（沒 skill 的印了內部編號）；(b) 先粗估一句、講範圍與卡型、不採用的不算；(c) 不引用客戶說別信的那份（沒 skill 的直接引用，還印編號）。
- **(a) 的代價是 F-1**：裝上 skill 反而多寫了一次綜合卡。誘因是我建檔時王先生綜合卡引用了錯的舊編號（我的失誤，卻剛好成了壓力測試）；真實世界裡綜合卡引用對不上的情況也會出現。
- **(d) 不過**：spec 要求「用 `--changed`」，裝上的 AI 沒帶；原因是「`--changed`」這個細節只寫在 `references/find.md`，AI 對這種簡單問句只讀 `SKILL.md` 就動手了。這個乾淨庫裡每一段日誌都有改動，所以結果與有沒有帶 `--changed` 無差；要看出 `--changed` 的價值需要一個有「沒改動的段」的庫（我沒補）。
- (e) 這個庫只有 2 件逾期，「最多 3 件」沒被測到；(e) 兩邊等價。
- 裝上 skill 的情境比較貴的只有 (a)(b)(c)：多讀 skill 與 references，$0.15～0.21 對 $0.14～0.21，差不多。

## 疑點與建議（標嚴重度；除註明外都是我重現的）

- **F-1【中，已重現】** 見結論。`skills/answer/` 的「不寫知識」只列了兩個允許的寫入（`cards redo`、`pending add`）；遇到「綜合卡引用的編號查不到」這種第三種資料問題沒有指引，AI 自己 `Edit` 了綜合卡。建議本文或 `verify.md` 加一句：發現綜合卡引用有誤時不改檔，回答照卡片與原檔、`turn note` 不寫、或交 lint／note 記口述。
- **F-2【中，已重現】** `hooks/session.py` 不看 `ingest_stopped`，同 session 的 `compact`／`resume` 會再叫 AI 整理。code-review 子代理也從讀碼指出同一點。另外 `INGEST_HINT` 開頭「有變動：」套在「還有 N 份沒整理完」上措辭不準。
- **F-3【中低，已重現】** `findings.stale()` 不看 `ruled_out`，`finding add` 也不擋依據含不採用的卡。
- **F-4【中低，已重現】** `tally`：(1) 29 位整數（`99999999999999999999999999999`）→ `decimal.InvalidOperation` traceback、exit 1，且 traceback 印出 plugin 絕對路徑（子代理從讀碼指出，我重現）；超過 4300 位會被誤印成「狀態檔讀不到」、極大指數 `--json` 輸出非法 JSON（讀碼，未重現）；(2) 前導零 `0,001` 被收、算成 1；(3) 同一張卡放兩次 `--row` 兩筆都算；(4) `--row` 的位置欄沒有字元／長度檢查（`${VAR}`、換行會弄壞表，讀碼）。
- **F-5【中，讀碼未重現】** `hooks/session.py` 的 `UNSORTED_BUDGET = 6` 秒只在「每張卡之間」檢查，`_scan_groups` 本身與每張卡對來源原檔的 `os.path.exists`／`realpath` 沒有檢查；快速比對最多已吃 5 秒；來源在離線網路磁碟或卡很多時，整個 hook 可能逼近或超過 10 秒被殺，`additionalContext`（core 規則與偏好）整段消失。相關：B-038。
- **F-6【中到低，skill 文字；skill-reviewer，我重現了 H2、H4】** 結論「通過但要修」：
  - 高：`verify.md:67`「那份在哪」才給路徑與連結，與 SKILL.md 與例句 3（找檔只給檔名日期）衝突；`find.md:3`「五個都一律帶 `--json`」對 `agenda list`（`--json` 沒有星期，人讀版才有「（週五）」）與 `journal recent`（`--json` 是原始路徑清單，「改了」分類只在人讀版）是錯的，會逼 AI 自己算星期；`verify.md:7` 範例「粗估約 182 萬」與 `verify.md:10`「粗估不加總」自相矛盾；`--find` 會比到「這一句自己」（turn hook 已寫日誌開口：`journal recent --find 王董` 第一筆是剛開的這句，結果「（還沒結束）」——**已重現**），「第二次問」的判斷幾乎一定成立。
  - 中：`record-answer.md:9` 的 `finding add` 帶 `--json`，但成功句只在人讀輸出才有（`--json` 還露 `c-` 編號）；`verify.md:28` 範例帶 `--json` 但 `:33` 說不帶；`verify.md:20` 還寫 `answer_verify_max`（已知，裁定 6）；短標沒涵蓋裁示〔r-…〕、`lookup` 的 `rulings` 沒帶日期；份數題（例句 4、10）沒講「讀程式印的張數、扣掉 ruled_out」；分辨沒收例句 13、produce 還沒有卻指過去；conflict 的閉環（客戶回答哪個對 → note → `pending resolve`）note 沒接；`verify.md:77`「不在設定檔第四節」AI 查不到；有效推算怎麼用沒講。
  - 低：`find.md:32`「多 entities」不準；拒絕句清單不全（`清冊沒有`、`這張卡的原檔已不在`、密碼句）；`record-answer.md` 缺 `withdraw` 路徑、`lookup … self` 循環；描述「最近改了什麼」沒主詞、「信義王結案了嗎」與 note 只差一個「嗎」（要看路由結果）。
- **F-7【低中，已重現】** 裝上 answer 的 (b) 在工具之間出現英文過場（「Three dated quote cards … I'll read the cards.」），skill 明寫過場也一律繁中。沒 skill 的那次是中文。屬模型行為，但是確認第 10 步要查的項目；SKILL.md 的「共通」段已寫，可能要放更前面。
- **F-8【低】** `CLAUDE.md` 第 79 列「其餘三個 skill 尚未」該是「其餘兩個」；`hooks/hooks.json` description 沒提新行為（B-041 同類）。
- **F-9【低，讀碼】**（子代理，未重現）`turn.mark` 讀改寫沒有鎖，可能與 Stop hook 的 `set_done` 互蓋；多視窗共用 `current.json` 時 A 的 `cards todo` 會把 `ingest` 標到 B 的回合；`turn.start(inherit=…)` 不帶 `ingest`／`ingest_stopped` 旗標（STOPPED 可能多印一次）；`cards todo` 在對話內現在會寫 `.system/turns/*.json`（無備份、只 catch OSError）。
- **F-10【低，讀碼】**（子代理，未重現）`finding_cli.py:120-122`、`pending_cli.py:141` 的 `except OSError` 印 `type(e).__name__: e`（含絕對路徑，違反「人讀輸出不印絕對路徑」）；`lookup_cli`／`tally_cli` 只 catch `ValueError`，其他例外 traceback 帶 plugin 與知識庫絕對路徑；`finding list --entity 沒登記` 靜默回空（`cards list`、`agenda list` 會拒）；`pending add` conflict 的 `titles` 沒過 `check_text`、壞狀態檔 `TypeError`；`lookup` 的 `mentions` 讀卡正文沒有大小上限、不帶 `ruled_out`／`stale`；`findings.stale`／`context` 掃全部卡不看清冊狀態（依據卡不掛該實體且原檔被排除時不判過期）；`journal.changed_text` 日誌行形狀壞掉時 `AttributeError`；`check_text` 不擋控制字元與 markdown（與口述同）。
- 觀察（不是缺陷）：`finding add` 的「等不到鎖」句沒法用「新建空鎖檔」重現（`timeout`＝`stale`＝30 秒，鎖檔會被當殘留清掉）。裁定 6（`verify_max` 進 `--json`）是已知第二輪做，這輪不算不過。

## plugin-dev 審查

- 先用 Skill 工具載了 `plugin-dev:skill-development` 與 `plugin-dev:hook-development` 當標準：skill 要 imperative、漸進揭露、描述含觸發句；hook 要例外 exit 0、不擋、用 `${CLAUDE_PLUGIN_ROOT}`、輸出 `hookSpecificOutput`。（本專案描述是繁中客戶口語、不是第三人稱 "This skill should be used"，這是專案規則 D-070／D-091，不當缺陷。）
- `claude plugin validate "<ROOT>"`：通過，exit 0；警告兩則既有（缺 icon、README 沒有 marketplace 安裝行）。
- `plugin-dev:skill-reviewer` 對 `skills/answer/` 與 `skills/note/` 的改動：「通過但要修」，細節見 F-6。結構面（描述 133 字、本文 2074 字、references 行數、原則帶理由與編號、沒有參數對照表、指令與旗標都存在、成功句與拒絕句對得上程式）都通過。
- `plugin-dev:plugin-validator` 對整個 worktree：**通過**。manifest 合法、版號 0.15.0 沒 bump；`hooks/hooks.json` 五個事件與腳本都在、用 `${CLAUDE_PLUGIN_ROOT}`，與 `plugin.json`、`CHANGELOG.md` 對 `dd5019d` 無變動；動的 hook 只有 `turn.py`、`session.py`，其呼叫的 `turn.mark`、`cards.unsorted` 都存在；四個 skill 的 frontmatter 與 references 對得上；沒有寫死的 plugin 絕對路徑或憑證。它另說「CLAUDE.md 地圖要核」：我核過，新模組與 skill 列都在（只有 F-8 那句舊字）。
- hook 審查（`plugin-dev:hook-development` 標準）我自己重現的：例外一律 exit 0 且仍開回合（見 hook 節）、`additionalContext` 是常數沒有注入面、同 session 只印一次；問題在 F-2、F-5。

## code-review（high）與 security-review

兩個唯讀子代理看 `git diff dd5019d..HEAD`（排除 tests），都沒讀測試。**沒有找到**：路徑穿越（`--card`、`--basis`、`--replaces`、`--entity`、`--source` 只當查表鍵）、寫到總資料夾外、動到守門／憑證／hooks 其餘三支、密碼外洩（journal 的 `--find` 比的是寫入時已遮過的欄位）；只讀指令（`lookup`、`cards list`、`tally`、`finding list`、`agenda list`、`journal recent`）不寫檔——我另用全樹雜湊驗了 `lookup`；寫入指令都在 `FileLock` 裡、寫前備份、原子寫入。我重現了其中 F-1（我自己跑 headless）、F-2、F-3、F-4(1)(2)(3)、F-6 的 H2／H4；其餘（F-5、F-9、F-10）未驗，列 backlog 建議。

## 給設計 session 的 backlog 建議（一行一條）

1. F-1：answer skill 補「綜合卡引用有誤時不改檔」的處理（交 lint／記口述／只照原檔答）。
2. F-2：`hooks/session.py` 的 9b 行看 session 的 `ingest_stopped`（或 compact／resume 不印整理指示）。
3. F-3：`findings.stale` 把 `ruled_out` 的依據算過期；`finding add` 對已不採用的卡拒絕或警告。
4. F-4：`tally` 限制數字位數（或拉高 Decimal 精度）並接 `ArithmeticError`、前導零、同卡重複、位置欄字元檢查。
5. F-5：SessionStart 算 unsorted 的時限放進 `_scan_groups`／逐卡 `open_link`，或與 B-038 一起做。
6. F-6：第二輪連同裁定 6（`verify_max`）一起改 `find.md`／`verify.md`：`agenda list`／`journal recent` 不帶 `--json`、`--find` 跳過這一句自己、那份在哪不給連結、粗估範例、份數題路徑、〔r-…〕短標、例句 13、conflict 閉環、把 `--changed` 提到 SKILL.md 本文。
7. F-7：把「過場一律繁體中文」提到 SKILL.md 開頭。
8. F-8～F-10：`CLAUDE.md` 那句、`hooks.json` description、`OSError` 不印絕對路徑與 `run` 的例外處理、`turn.mark` 鎖、`finding list --entity` 一致性。

## 第 17 條：路由（run_eval）

跑法（B-036）：`PYTHONUTF8=1 PYTHONPATH="<skill-creator>" python <shim>/run_eval_win.py --eval-set <ROOT>/tests/evals/<skill>.json --skill-path <ROOT>/skills/<skill> --runs-per-query 3 --num-workers 1 --verbose`；shim 只把 `select.select` 換成永遠就緒，skill-creator 原檔沒動。**為了省時間，四份各用一份 `git archive HEAD` 的獨立複本（各自的 cwd 與 `.claude/commands/`）平行跑**，避免暫存指令檔互撞；結果與循序跑等價。機器上舊 `llm-wiki@llm-wiki` 是 enabled（使用者同意開跑時確認；非我啟用）。

| 檔 | 結果 | 該觸發（句×3 次） | 不該觸發 |
|---|---|---|---|
| `answer.json` | 20/20 passed | 10 句全 3/3，30/30（100%，門檻 ≥ 90%） | 10 句全 0/3，0/30（0%，門檻 ≤ 10%） |
| `note.json` | 20/20 passed | 10 句全 3/3，30/30 | 0/30 |
| `ingest.json` | 20/20 passed | 9 句 3/3、1 句 2/3（「資料夾裡的東西都整理好…」），29/30（97%） | 0/30 |
| `manage.json` | 20/20 passed | 8 句 3/3、2 句 2/3（「公司行政 的資料夾我改名」「幫我把信義王的報價 v2 搬」），28/30（93%） | 0/30 |

不退步：note、ingest 與第一輪前相同；manage 比 `manage-測試.md` 記的 30/30 少兩次觸發（93%），仍在 ≥ 90%。這次 run_eval 沒有載 wiki-with-willy（只放該 skill 的暫存指令檔），answer 的描述不會與它們競爭，兩次 2/3 看起來是取樣雜訊，不是 answer 造成；要確認「同時裝著四個 skill」的路由要到確認情境用真實對話看。

**提醒：這個 plugin（舊 `llm-wiki@llm-wiki`）目前仍是 enabled，確認情境前要再停用。**

---

# 第二輪（測試後修正裁定 12～23）
日期：2026-10-22
被測：HEAD `d827e70`（前一個 `e841494` 是 merge main，不審）。spec 以 main `03279d5` 的「測試後修正裁定」12～28 為準。
範圍（裁定 24）：只驗 12～23 與無退步；第 18 條只重跑 (d)，另在有「沒改動的段」的庫再跑；描述沒改，路由不重跑。
方法：每條裁定自己跑 CLI／hook／模擬（黑箱）；code-review（high）只看 `e841494..d827e70`（不含 tests）、security-review 看 hook 與鎖，兩個唯讀子代理只讀碼；文字類（13、18、19）對照 `skills/answer/` 的實際字句；(d) 用 headless `claude -p`。沒改任何程式。

## 結論

**裁定 12～23：12 條都照做、0 不過；無退步（全部測試 exit 0、`claude plugin validate` 通過）。第 18 條 (d) 重跑：仍不過**（`--changed` 還是沒被用到，原因不在 skill 文字，在常駐的 `rules/core.md` R-13，見 G-1）。
- `python -m unittest discover tests` exit 0（Ran 1752，OK，skipped=1，349 秒；第一輪 1728，多 24 個），樹乾淨。
- `claude plugin validate` exit 0（同樣只有 icon 與 README 安裝行兩則既有警告）。
- code-review（high）：12～23 全部讀碼確認做到，沒有會造成錯誤輸出或崩潰的 bug；security-review：沒有 CONFIRMED，只有低度 PLAUSIBLE。
- 不可以動清單（guard、credentials、cmdparse、versions、其餘三支 hook、core.md、ingest／manage、agenda／profile／statements／rulings／entities、plugin.json、CHANGELOG、design）`e841494..d827e70` 皆無變動；`hooks.json` 只改了 `description`（結構與 timeout 沒變）。

新發現（依嚴重度）：G-1【中】(d) 根因在 core.md R-13；G-2【低中】F-7 英文過場沒有改善；G-3【低】回答「綜合卡引用對不上」時印了內部編號；其餘低度見「疑點」。

## 逐條

| 裁定 | 結果 | 證據 |
|---|---|---|
| 12 `journal --find` 跳過這一句＋骨架寫明指令 | 過（程式與文字）；行為見第 18 條 | 開 session 的一句「上次我問王董你怎麼說」（還沒結束）後 `journal recent --find 王董`：只列「王董的電話幾號」與 `kb entity alias` 的程式段，不含這一句自己；不帶 `--find` 時照列（「結果：（還沒結束）」）；`--json` 也跳過；終端機（無 session）跑 `--find` 不誤跳別的段。`SKILL.md` 骨架一句寫了「最近改了什麼用 `kb journal recent --changed`、以前聊過什麼用 `--find`，這週要做什麼用 `kb agenda list --from --to`」，沒做成表。 |
| 13 綜合卡、檔案卡、設定檔一律不改 | 過（文字）；行為重測過 | `SKILL.md` 原則逐字有「綜合卡、檔案卡、設定檔一律不改；看到引用對不上，照原檔答、在回答裡說，卡抄錯才 `kb cards redo`，其他不寫檔、不叫 `turn note`」，帶理由與 D-155。F-1 情境（王先生綜合卡引用錯編號）用新 skill 重跑 2 次：**0 次 `Edit`**，AI 說「我沒有改卡」，指令只有 `lookup`／`cards list`／`convert`／`asks`。（但見 G-3、G-4。） |
| 14 session.py 看 `ingest_stopped` | 過 | 12 份沒整理、沒按過停止：`還有 12 份沒整理完` 後面接 `先照 ingest skill 整理（…）`（**開頭沒有「有變動：」**）；同 session 按停止後（UserPromptSubmit 寫入 `ingest_stopped`）`compact`／`resume`／`startup` 都只剩 `還有 12 份沒整理完`、不印整理指示；別的 session 不受影響；有新變動＋按過停止 → 印 `變動：新增 1…`、不印整理指示。session 檔壞（`{ bad`）→ 當沒按過、照印；session 檔是目錄 → exit 0、這一行不印、寫 log；空 session_id、含 `../` 的 session_id → exit 0、照印。 |
| 15 findings 與不採用 | 過 | `finding add --basis <不採用的卡>`（兩種：只有它、與別張並列）→ `「信義王合約」你說過別信，不能當依據`、exit 3、`findings.json` 沒建出來；先記推算再對依據卡記「不採用」→ `lookup` 那筆 `stale: true`，人讀 `〔依據有變，待重算〕`；收回那條裁示 → `stale: false`。 |
| 16 tally 限制 | 過 | 15 位整數收、16 位 → `數字太大：9999999999999999`（29 位、20 位同）；小數 4 位收、5 位 → `數字太大：1.23456`；`0,001`、`1,23`、`1,234,56` → `看不懂這個數字：…`；`ArithmeticError` 不再 traceback，29 位那個案例 exit 3；位置欄 30 字收、31 字／含 `|`／換行 → `位置要一行、最多 30 字，不能有 |`（exit 3，裁定 27 追認）；同一張卡兩筆都算（合計 300、平均 150）；`萬` 乘出來 20 位照算。（我第一次測 30 字被拒是 Windows 的 `python print` 帶了 `\r`，用 `printf` 重測後 29／30 收、31 拒。） |
| 17 時限進 `_scan_groups` | 過 | 6000 張卡＋12 份沒整理的庫：把 hook 的起算時間撥到「只剩 0.3 秒」，**新碼 0.35 秒就中止**並寫 log `TimeoutError: 讀卡讀太久`、那行不印；舊碼（54580ef）要 0.87 秒掃完才報「算太久」；剩很多時間時兩者都印、約 0.9～1.0 秒；`kb cards todo` 同庫 3.3 秒完整跑、不被截斷（deadline 只由 `session.py` 傳）。 |
| 18 find／verify／record-answer 文字 | 過（文字） | 逐項對照 `skills/answer/`：`find.md` 的 `agenda list`、`journal recent` 改人讀（寫明「日期帶星期、『改了』分好類，`--json` 沒有這些」）、其餘照 `--json`；`verify.md` 「那份在哪」只給檔名與日期、「打開給我看」才給連結；粗估範例改成「15 張卡，正在逐份對」、不加總；`record-answer.md` 的 `finding add` 不帶 `--json`；短標補裁示〔你 10/21 說的〕且 `lookup --json` 的 `rulings` 有 `date`（實測 `2026-10-21`）；`find.md` 份數題「照程式印的張數、扣掉不採用的」；`SKILL.md` 分辨收例句 13（開場有新檔先整理再答）、做 Word／Excel「這版還做不到，照實說」；conflict 閉環（客戶說哪個對 → 歸 note → `pending list --json` 找編號 → `pending resolve --how`）寫進 `verify.md` 第四節（`pending resolve --how` 實際存在）；「不在第四節」改成看 `lookup` 的 `synthesis`；有效推算怎麼用在原則與 `find.md` 第一節。描述逐字沒改；`SKILL.md` 本文 2340 字（≤ 3000）、`find.md` 74 行、`verify.md` 79 行、`record-answer.md` 36 行。殘留（不在裁定清單）：G-5。 |
| 19 過場繁中移到最前面 | 過（文字）；**行為沒改善** | `SKILL.md` 第一段（標題下）：「給客戶看的字一律繁體中文，工具之間的過場也是；不唸步驟、不寫自言自語」，「共通」裡的重複句已刪。但新 skill 重跑 (b) 仍出現兩句英文過場（G-2）。 |
| 20 CLAUDE.md、hooks.json | 過 | `CLAUDE.md` 第 79 列「其餘兩個 skill 尚未」；`hooks.json` description 補了「沒有新變動但還有沒整理完的照樣接著整理；整理中按了停止，這個對話不再自動整理」，hooks 結構不變；`claude plugin validate` exit 0。小缺：G-6。 |
| 21 `turn.mark` 鎖、`inherit` 帶旗標 | 過 | 手動持有 `<sid>.json.lock` 時在對話回合內跑 `kb cards todo`：等 30 秒（鎖逾時＝殘留 30 秒，之後清掉）後照樣跑完、不擋 todo，旗標在鎖清掉後寫入；`turn.start(inherit=)`：原回合 `ingest` → 新回合 `ingest: True`、`ingest_stopped` 有則帶、沒 inherit 則無。鎖只擋 mark 對 mark，不擋 Stop 的 `set_done`（裁定 25 已併 B-092；我的並行壓測因 `set_done` 先到時 `mark` 依設計不記，不具鑑別力，不當證據）。 |
| 22 OSError 與例外 | 過 | `findings.json` 或 `pending.json` 變成目錄：`finding list`／`finding add`／`pending add`／`pending list` → `狀態檔讀不到：.system/findings.json`（相對路徑、exit 3，沒有絕對路徑）；`lookup`（讀 findings 時 `PermissionError`）→ `查不出來（PermissionError），沒有結果` exit 3；清冊 `[]`、`entries: 5`、實體清單 `entities: 7` → `lookup`／`tally`／`cards list` 都是一句 `狀態檔讀不到：…`、exit 3，沒有 traceback；`finding list --entity 沒登記` → `實體清單沒有「沒這個」，先 kb entity add`（跟 `cards list` 一致，exit 3），別名查得到；`findings.json` 唯讀、`os.replace` 被拒 → `狀態檔讀不到：.system/findings.json.tmp`（措辭見 G-7）。 |
| 23 `verify_max` | 過 | `lookup --json` 與 `cards list --json` 都有 `verify_max`（預設 20）；客戶端 `.system/config.json` 設 `answer_verify_max: 3` 後兩者都是 3（沒登記的名稱也有）；設成 `"abc"` 退回 20；`verify.md` 照它。 |

## 第 18 條 (d)：重跑

做法同第一輪：`git archive HEAD`（d827e70）展開 plugin、headless `claude -p`、`--settings` 關掉舊 `llm-wiki`、每次一份新的庫複本。

1. **跟第一輪同一個乾淨庫（VC）**：新 skill 一次。指令序列：`kb journal recent` → `kb asks`。**沒有呼叫 `Skill` 工具、沒帶 `--changed`。**
2. **有「沒改動的段」的庫（VC2）**：先「王董的電話幾號」「信義王的尾款是多少」（都不改東西）、再「水電驗收改到 11/2」（改日程，`turn note`）、最後「信義王現在怎樣」（不改），所以日誌最新一段是沒改動的。新 skill 4 次、第一輪版 skill 1 次、沒有 answer skill 1 次：

| 版本 | 指令序列 | `Skill`？ | 有帶 `--changed`？ | 回答 |
|---|---|---|---|---|
| 新 skill（d827e70）×4 | `kb journal recent` → `kb asks`（其中一次只有 `journal recent`） | 4／4 沒呼叫 | 0／4 | 正確：只有一筆實際改動（水電驗收 10/25 → 11/2，日程）；4 次都先講有改的那筆，之後又用一兩句帶過沒改動的三題查詢（1 次逐句列出「只是查詢、沒有改任何東西：…」，其餘 3 次一句帶過），所以不是「只講有改的」 |
| 第一輪版 skill（54580ef）×1 | `Skill answer` → `kb journal recent` → `kb asks --json` | 有 | 沒有 | 同上，正確 |
| 沒有 answer skill ×1 | `kb journal recent` → `kb asks` | — | 沒有 | 同上，正確（也列了沒改動的查詢） |

判讀：**(d) 仍不過**（spec 要求用 `--changed`）。這次 5／5 的新 skill 連 `Skill` 都沒呼叫，直接答：常駐的 `rules/core.md` R-13 寫「問最近改了什麼用 kb journal recent 答」，模型照它做了，**skill 本文骨架那句 `--changed`（裁定 12 的修法）根本沒機會被讀到**。回答內容始終正確、有改的那筆放最前面（VC2 裡 `--changed` 與否的差別只在「是否也提沒改動的查詢」，4／4 都有提）；沒有資料風險，是指令不符 spec。

## 疑點與建議（第二輪新的；嚴重度）

- **G-1【中，已重現 5／5】** (d) 的根因：`rules/core.md` R-13 讓「問最近改了什麼」在 skill 載入前就被直接回答，且沒有 `--changed`。裁定 12 只改了 skill 文字，碰不到。建議（`core.md` 是設計 session 才能動的）：R-13 那句改成「問最近改了什麼 → 走 answer」或補成 `kb journal recent --changed`。
- **G-2【低中，已重現】** F-7 沒改善：新 skill 重跑 (b)，工具之間仍有兩句英文過場（「Start by checking which card types exist, without a type filter.」「Three 2026 cards (報價 2, 估價單 1), plus one undated. I will read the cards.」，原文略），跟第一輪同一種、同一個情境。裁定 19 的文字照做了但沒效。若要壓住，可能得放進 `core.md`（常駐）或換成更具體的說法；也可接受為模型行為上限。（(a)×2、(d)×5 的過場都是中文。）
- **G-3【低，已重現 1／2】** 裁定 13 要 AI「在回答裡說引用對不上」，但沒說怎麼講：新 skill (a) 第一次回答寫「綜合卡寫電話在原檔〔f-b0b15ec0〕…實際的檔案卡是 f-7603d260」，印了內部編號，與 D-160「不印編號」衝突。建議原則補「用卡標題講，不貼編號」。
- **G-4【低，子代理讀碼】** `SKILL.md` 原則 13「卡抄錯才 `cards redo`，其他不寫檔、不叫 `turn note`」與 `verify.md` 第四節末「標重做、記待處理之後…跑 `kb turn note`」字面互相打架，建議原則補一句「redo／pending add 之後照 `verify.md` 叫 `turn note`」。
- **G-5【低，已確認】** `verify.md` 第三節 `kb tally` 範例的指令結尾帶 `--json`，下一條卻寫「不帶 `--json` 跑，回的表…原樣貼進回答」（第一輪 M7 沒被裁定列到，仍在）。
- **G-6【低，子代理讀碼，我核過】** `CLAUDE.md` 的 `tally_cli.py` 那一列文末是「…不收 |，其他例外 exit 3 |」，cell 裡有沒跳脫的 `|`，Markdown 表格會多切出欄位；改成「不收豎線」並拿掉多的結尾 `|`。
- **G-7【低】** 讀碼／子代理（多數未重現）：寫入失敗與讀取失敗都印「狀態檔讀不到」；`hooks/session.py` 的 `_stopped` 沒接 `OSError`（docstring 說「讀不到當沒按過」，實測 session 檔是目錄時是「整行指示不印＋寫 log」）；`ingest_stopped` 要等下一句 UserPromptSubmit 才寫入（按停止後沒再送新句就直接 compact，指示照印）；`journal --find` 在終端機看到被中斷的上一場 `current.json` 時可能跳過那一段；`turn.mark` 的鎖不涵蓋 Stop 的 `set_done`／`set_note`（裁定 25，B-092），UserPromptSubmit 的 5 秒 timeout 與鎖 3 秒疊加最壞會超時（hook 本身不擋）；`tally` 前導零 16 位被誤判太大、很小的平均（`0.0001＋0.0001`）印成 `0`、`--json` 的 `total`／`average` 有小數時轉 float 失準（`total_text` 準）、位置欄與拒絕句回顯沒濾 ESC／U+2028 等控制字元與 markdown；`finding add` 拒絕句帶的卡標題沒過濾（卡標題是可控文字）；`finding_cli.rel_of` 沒接 `RuntimeError`／`TypeError`；`lookup_cli` 的 `except Exception` 會把程式 bug 也吞成一句白話且不留 log；`cards._walk_cards` 走訪與單張巨檔沒時限。皆不擋發行，進 backlog 建議。

## 無退步檢查

全部測試 exit 0；`plugin validate` 通過；第一輪的重現全部不再重現：F-1（新 skill (a)×2 不 `Edit`）、F-2（`compact`／`resume` 不印整理指示）、F-3（拒絕、`stale`）、F-4（29 位數 exit 3、`0,001` 拒絕）、F-5（`_scan_groups` 內中止）、F-6 的 H2／H4（`--find` 跳過這一句；`agenda list`／`journal recent` 改人讀）。(b) 重跑一次：粗估一句（中文）→ `kb convert`×3 → `kb tally`（合計 2,030,000、平均 676,667）→ 範圍、估價單舊名、沒算大安陳（未載）、不採用合約說明都在；另自己記了一筆木作 310,000 vs 210,000 的 conflict 待處理並叫 `turn note`（合 D-155）。

## 給設計 session 的 backlog 建議（第二輪，一行一條）

1. G-1：`rules/core.md` R-13 改成走 answer 或補 `--changed`（設計 session 決定）。
2. G-2：過場英文要不要放進常駐規則，或接受為上限。
3. G-3、G-4、G-5：answer 原則補「用卡標題講、不貼編號」與 `turn note` 例外一句，`verify.md` 第三節範例拿掉 `--json`。
4. G-6：`CLAUDE.md` tally 列拿掉未跳脫的 `|`。
5. G-7：`_stopped` 接 `OSError`、讀寫失敗訊息分開、`ingest_stopped` 寫入時機、`tally` 邊角與控制字元、其餘低度項併 B-092。

---

# 第三輪（測試第二輪後裁定 29、31～34）
日期：2026-10-22
被測：HEAD `20633b4`（前一個 `a841d4d` 是 merge main，不審）。spec 以 main `f37cfbf` 的「測試第二輪後裁定」29～36 為準。
範圍（裁定 35）：只驗 29、31～34（加疑義 36）是否照做、有無退步；18(d) 在有「沒改動的段」的庫重跑至少 3 次；路由不重跑。
方法：每條自己實跑（hook 模擬、grep 全文、headless `claude -p`）；code-review（high）派唯讀子代理只看 `a841d4d..20633b4`（不含 tests）。沒改任何程式。

## 結論

**裁定 29、31、32、33、34（含疑義 36）全部照做、0 不過；無退步。18(d) 這次過：4／4 用了 `kb journal recent --changed`、回答只講有改的。**
- `python -m unittest discover tests` exit 0（Ran 1757，OK，skipped=1，339 秒；第二輪 1752，多 5 個），樹乾淨。
- `claude plugin validate` exit 0（同樣只有 icon 與 README 安裝行兩則既有警告）。
- code-review（high）：六條都做到，沒有 CONFIRMED 缺陷；只有兩處文字層級的 PLAUSIBLE（見疑點）。
- 常駐預算沒超：`rules/core.md` 約 924 字（上限 2500），R-13 多的只有 `--changed` 幾個字（位元組 2226 → 2236）；四個 skill 描述合計 558 字，加 `skeleton/CLAUDE.md` 484 字，三項合計約 1966 字，遠低於 5000。

## 逐條

| 裁定 | 結果 | 證據 |
|---|---|---|
| 29 R-13 帶 `--changed` | 過 | `rules/core.md` 只有 R-13 最後一句改成「問最近改了什麼用 kb journal recent --changed 答」，其他條未動；SessionStart 實際注入的 `additionalContext` 裡那行就是新句子；與 `SKILL.md` 骨架一致。18(d) 行為見下節。（歷史 spec `design/specs/2026-10-16-開場注入與核心規則.md` 還留著舊 R-13 原文，不算矛盾，同步與否設計 session 決定。） |
| 31 不貼編號 | 過 | `SKILL.md` 原則 13 補「用卡標題與短標講，不貼〔f-…〕這類編號（D-160、B-089）」；`verify.md` 第四節開頭多同一句；grep `skills/answer`、`skills/note` 全文，〔f-…〕只出現在「不要貼」的語境。行為：F-1 情境（王先生綜合卡引用錯編號）新 skill 重跑 2 次，**兩次回答都沒有印內部編號**（第二輪是 1／2 有印），也都沒有 `Edit`，AI 說「指向的檔案編號對不上現有的任何卡…沒有改卡」。 |
| 32 原則 13 與 verify.md 第四節一致 | 過 | `SKILL.md` 原則 13：「…卡抄錯才 `kb cards redo`，其他問題不寫檔；只有 `kb cards redo` 或 `kb pending add` 的回合才叫 `turn note`」；`verify.md` 第四節末「只有標重做（`kb cards redo`）或記待處理（`kb pending add`）的回合，送出最終回答前跑 `kb turn note`…其他問題不寫檔、不叫」；`SKILL.md` 骨架那句也一致。（G-4 的字面打架已消除；剩一個措辭瑕疵見 H-1。） |
| 33 tally 範例、CLAUDE.md 不破表 | 過 | `verify.md` 第三節範例結尾已無 `--json`，與下一條「不帶 `--json` 跑」一致；`skills/answer`、`skills/note` 沒有其他地方要求 `tally` 帶 `--json`（`record-answer.md` 的 `--json` 是取編號用，相容）。`CLAUDE.md` 的 `tally_cli.py` 那列改成「不收直線，其他例外 exit 3 |」，用管線數檢查（正常列 3 條）已不再被標出（第二輪是 4 條）。其他三列（`entity.py`、`profile_cli.py`、`_lib/profile.py`）本來就含 `rename\|merge` 這類未跳脫的 `\|`，改前版本也一樣，非本輪引入，見 H-3。 |
| 34 `_stopped` 接 `OSError` | 過 | session 檔是目錄（讀它丟 `PermissionError`）：exit 0、`還有 12 份沒整理完` **照印、整理指示照印**（第二輪是整行不印）、`hook-errors.log` 多一行 `session PermissionError…`；session 檔壞 JSON → 當沒按過、照印；真的按過停止的 session（`ingest_stopped`）→ `compact` 仍只印 `還有 12 份沒整理完`、不印整理指示；`_log_vault` 存在、簽名對，其他例外由 `status_lines` 每行各自 try 接住、hook 永遠 exit 0。 |
| 36（疑義） | 過 | `verify.md` 第二節只剩「讀不到照實說…位置欄寫『沒核對』」，「convert 記了讀不到也算寫入」那句與第四節末的「或 convert 記了讀不到」都已刪；`skills/answer`、`skills/note` 沒有別處還說 convert 讀不到要叫 `turn note`。 |

## 第 18 條 (d)：重跑（裁定 35）

做法同第二輪：`git archive HEAD`（20633b4）展開 plugin、headless `claude -p`、關掉舊 `llm-wiki`、**VC2**（日誌最新一段是沒改動的：先問「王董的電話幾號」「信義王的尾款是多少」、再「水電驗收改到 11/2」、最後「信義王現在怎樣」）、每次一份新複本、4 次同時跑。

| 次 | 指令序列 | `Skill`？ | `--changed`？ | 回答 |
|---|---|---|---|---|
| 1 | `kb journal recent --changed` → `kb asks` | 沒呼叫（R-13 直接答） | **有** | 只講有改的：日程（水電驗收 10/25 → 11/2）、待處理新增一筆（終端機）、清冊三筆「日程抽過」（終端機）；沒提那三題只是查詢；「日誌裡只有這幾筆」；末尾逾期兩件加「還有 1 件兩份寫法不同的」 |
| 2 | `kb journal recent --changed` → `kb asks` | 沒呼叫 | 有 | 同上（三筆改動，沒提查詢），末尾 asks |
| 3 | `kb journal recent --changed` → `kb asks` | 沒呼叫 | 有 | 同上 |
| 4 | `kb journal recent --changed` | 沒呼叫 | 有 | 同上；沒跑 `asks`，改成末尾一句建議「我可以跑 `kb asks` 逐一跟你確認」（符合 R-11） |

**4／4 用了 `--changed`、4／4 回答只講有改的，沒有一次提「只是查詢、沒有改東西」**（第二輪 4／4 都有提）。仍然是 R-13 在 skill 載入前就答（0／4 呼叫 `Skill`），這次 R-13 本身帶了 `--changed`，所以結果對了。**裁定 35 的 (d)：過。**

(a)（G-3 順便看）：新 skill 在乾淨庫 VC（王先生綜合卡仍引用錯編號）跑 2 次：兩次都是 `Skill answer` → `kb lookup "王董"` → … → `kb convert`／Read 原檔 → `kb asks --json`，回答王董＝王先生、0912-345-678、出處短標「信義王報價第一版原檔（2026-08-12）」；**都沒有印 `f-…` 編號、都沒有 `Edit`**。

## 疑點（第三輪新的；都不擋發行）

- **H-1【低，子代理，文字】** `SKILL.md` 原則 13 的順序「卡抄錯才 `redo`，其他問題不寫檔；只有 `redo` 或 `pending add` 才叫 `turn note`」：「其他問題」讀起來包含「兩份原檔不同」，那種其實要 `pending add`（也是寫入）；後半句救回來了、不會誤判，只是前後半句字面上有點打架，可改成「除了 `kb pending add` 其他情況不寫檔」。
- **H-2【低，子代理，行為層，裁定 36 是刻意的】** `scripts/kb/_lib/convert.py` 的 `_record_unreadable` 在條目已在清冊時仍會寫一條 `unreadable` 待處理，所以「其他問題不寫檔」嚴格說不完全真，那個回合 AI 也不叫 `turn note`；工作日誌「做了」由程式地板句補（我沒去驗那一句寫得對不對，列為未驗）。
- **H-3【低，既有】** `CLAUDE.md` 第 27、35、59 列（`entity.py`、`profile_cli.py`、`_lib/profile.py`）含未跳脫的 `|`（如 `rename|merge|remove`），在 GFM 表格會多切欄；改前一樣，非本題。
- G-2（英文過場，併 B-064）這輪沒有專門重測；(a)×2、(d)×4 的過場都是中文。

## 無退步檢查

全部測試 exit 0；`plugin validate` 通過；不可動清單與第二輪相同（本輪只動 `CLAUDE.md`、`hooks/session.py`、`rules/core.md`〔准動，只 R-13 一句〕、`skills/answer/SKILL.md`、`references/verify.md` 與測試）；第二輪重現過的問題維持不重現（F-1 不 `Edit`、compact 不整理、`verify_max` 等沒被動到）。

## 給設計 session 的 backlog 建議（第三輪）

1. H-1：原則 13 那句改成「除了 `kb pending add` 其他情況不寫檔」（可選）。
2. H-3：`CLAUDE.md` 第 27、35、59 列的 `|` 跳脫或改寫（既有，順手）。
3. 確認前請使用者停用舊 `llm-wiki@llm-wiki`（仍 enabled）。
