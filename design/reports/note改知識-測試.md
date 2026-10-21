# note 改知識：測試報告（第一輪）
日期：2026-10-21
給誰看：設計 session（決定要不要修、要不要發行）。
什麼時候更新：每輪測試後。

被測：worktree `llm-wiki-j-note改知識`，分支 `spec/note改知識`，HEAD `27bfa83`（merge-base `610f2bd`）。spec 以 main 的版本為準（含「實作回報裁定」1、2）。
方法：我自己對驗收 18 條逐條跑 CLI 與 hook（黑箱，環境見下）；code-review（high）與 security-review 派唯讀子代理列疑點，疑點裡有重現價值的我自己重跑；skill 審查用 `plugin-dev:skill-reviewer`；第 16 條用最終版 skill 另跑一輪。沒改任何程式。

## 結論

**驗收 18 條：18 過、0 不過。** 全部測試 `python -m unittest discover tests` exit 0（Ran 1631 tests，OK，skipped=1），在 commit 之後、工作樹乾淨時跑。`claude plugin validate` 通過（只有 icon 與 README 安裝行兩則既有警告）。

不過有 **2 條我自己重現的「外洩／注入」等級疑點**（下面 F-1、F-2），建議修完再發行；其餘是中低或 skill 文字品質，列進 backlog 建議。

## 驗證環境

- `<V>`：`tests/fixtures/inventory/{vault,raw}` 複本，掃描後設定檔第一節加 業主（對象）、案子本來就有；登記信義王、內湖李（案子）、王先生（業主）並確認。
- spec 說「三張檔案卡」，但綜合卡門檻 `synthesis_min_cards` 是 2，三張卡撐不起信義王與內湖李各一張綜合卡，所以我做了**四張**卡：內湖李報價單、大安陳報價單（先掛內湖李，給第 6 條用）、信義王合約（掛信義王、王先生）、信義王報價單（卡上 5800、原檔 8500）。王先生只掛一張卡（第 3 條）。（spec 小缺口，F-9。）
- fixture 的 `案子/信義王/信義王.md` 是壞檔頭（「綜合卡，不入冊。」），先刪掉才能起綜合卡。
- 回合：終端機直跑時全部落在同一個回合；要測 revert 的步驟，我用 `hooks/turn.py` 送 UserPromptSubmit 開新回合。PostToolUse 用 `hooks/output_log.py` 餵 stdin。

## 逐條

| # | 結果 | 證據（摘要） |
|---|---|---|
| 1 | 過 | `statement add --entity 內湖李 --text 內湖李改用系統櫃，不做木工` → `已記下：內湖李改用系統櫃，不做木工（內湖李）`；`statements.json` 一條 active；manifest 有 `.system/statements.json create`；`cards todo --json` 的 `synthesis_stale` 有內湖李，`added` 有 `source:"statement"`，項目帶 `statements`。拒絕三條一字不差：`實體清單沒有「沒這個」，先 kb entity add`、`口述要一句話，最多 200 字`（201 字）、`口述裡不能有 " $ ` \ 這四種字元`（含 `$`）；exit 3。 |
| 2 | 過 | 先把綜合卡寫成現行（整理到含舊 `s-`）再 `--replaces`：`已改：舊 → 新`；舊 superseded、`replaced_by` 對；todo `removed` 有舊、`added` 有新。`withdraw` → `已收回：…`、status withdrawn、`removed` 有它。改已被取代的 → `這條已經被取代了，改最新的那條`；`沒有這條口述：s-00000000`；再收回 → `這條已經收回了`；改已收回的 → 同句（exit 3）。 |
| 3 | 過 | 王先生只掛 1 張卡：加口述前 `synthesis_new` 空，加後出現王先生（`cards` 1 張、帶 `statements`）。`關於你` 加客戶自己後 `statement add --entity 林小姐設計` → `已記下：公司下個月搬家（林小姐設計）`，`synthesis_new` 不出現；`statement list --entity` 查得到。 |
| 4 | 過 | 照 todo 的 header（含 `s-` 行）寫綜合卡 → `output_log.py` 無輸出；舊 header 與少了 `s-` 行都被擋：`綜合卡有錯：整理到不是現行的，重跑 kb cards todo 拿新檔頭。改好再寫一次。`；`hook-errors.log` 不存在。 |
| 5 | 過 | `cards redo f-2cf7b0a1` → `已標成要重做：「信義王報價單」，下次整理時重寫` 加 `連帶要更新的綜合卡：信義王`；`--json` 有 `synthesis:["信義王"]`；清冊 `redo` 有值；信義王綜合卡 `整理到` 那行是 `f-2cf7b0a1 redo`；todo 的 `stale` 列它帶 `reason:"客戶說卡讀錯了"`、`synthesis_stale` 不列。照 header 重寫卡後：`stale` 空、`synthesis_stale` 有信義王且 `changed` 帶 `reason`；照 todo header 重寫綜合卡、PostToolUse 無輸出後不再列；下一次 `inventory scan` 寫清冊後 `redo` 拿掉。revert 退掉 redo：清冊與信義王綜合卡檔頭回去（全樹比對無殘差）。 |
| 6 | 過 | `cards mount f-39d7d1eb --entity 信義王` → `已改：「大安陳報價單」掛在 信義王（原本是 內湖李）`；卡檔頭 `實體: [內湖李]`→`[信義王]`；兩邊綜合卡檔案卡欄重渲染；todo 內湖李 `removed` 有它、信義王 `added` 有它。拒絕：`實體清單沒有「沒登記」，先 kb entity add`、`清冊沒有 f-00000000`；已掛 → `「…」本來就掛在 信義王`（exit 0）。總目錄在 manifest 裡（內容因分目錄列法沒變）。 |
| 7 | 過 | `entity alias 王先生 --add 王董` → `已加別名：王先生 也叫 王董`；`entities.json` 有；王先生綜合卡檔頭 `別名: [王董]`。`--add 信義王`（已登記實體）→ `「信義王」已經登記成一個實體了，是同一個就合併`；`--add 王董`（內湖李，撞王先生別名）→ `「王董」跟「王先生」的別名撞了`。先 `entity add 王董` 再 `--add 王董 --json`：exit 3、`entities.json` 不變、`refused` + `results`（`是同一個，合併` → `do: ["kb entity merge 王董 --into 王先生"]`；`不是，先不加` → `[]`）。`--remove 王董` → `已拿掉別名：王先生 不再叫 王董；有 1 張卡內文提到王董，要看是不是掛錯了`，`suspect_cards` 是信義王合約（掛王先生、正文出現王董）；沒有的別名 → `「王先生」沒有「王董」這個別名`。 |
| 8 | 過 | `entity rename 信義王 --to 王宅` 後 `statements.json`、`rulings.json` 的實體名都換成王宅、`裁示.md` 改寫；revert 乾淨。`entity remove` 後口述與裁示照留（`statement list`、`ruling list` 仍有）。 |
| 9 | 過 | `ruling add --kind 不採用 --card f-a82c8cc6 --text 合約.docx 是舊的，別信` → `已記下裁示（不採用）：「信義王合約」—合約.docx 是舊的，別信`；`rulings.json` 記 `fp` 前 12 碼；`系統/裁示.md` 照格式（三行頂端說明 + `## 不採用`）；信義王 stale、`added` 項目 `source:"ruling"`、帶 `rulings`；header 有 `r-` 行。`--kind 可以讀`／`可以記` → `「可以讀」「可以記」這兩種要等密碼與守門那一版`；不採用沒給 `--card`（或給 `--entity`）→ `不採用要指定是哪幾張卡（--card）`；另測 `口述為準要掛實體或卡`、`裁示種類要是 口述為準、不採用 之一`、`清冊沒有 f-00000000`、`這份還沒有檔案卡`，皆 exit 3。 |
| 10 | 過 | `ruling add --kind 口述為準 --entity 信義王 …` → `已記下裁示（口述為準）：信義王`，信義王 stale；照 header 寫回後不再列。`ruling remove` → `已收回裁示：信義王—…`；`裁示.md` 變 `目前沒有裁示`；stale 的 `removed` 有 `r-` 項目；再收回 → `這條裁示已經收回了`。 |
| 11 | 過 | 改 `客戶案件/信義王/合約.txt`、`inventory scan`、重寫卡後 `kb asks --json` 有 `ruling_recheck`，問句 `「合約.txt」改過了，你之前說別信，現在可以信了嗎？`。同時放「原檔不見」與「逾期」：順序 `missing` → `ruling_recheck` → `overdue`。`還是別信` 的 do（`ruling recheck`）→ `已對過新版，還是不採用：「信義王合約」`，`fp` 由 `a5674f911320` 變 `4d67f5c626cb`、不再問；`可以了` 的 do（`ruling remove`）→ 收回、`裁示.md` 空。 |
| 12 | 過 | 上面所有人讀成功輸出（statement／ruling／cards redo|mount／entity alias／remove）掃 `[afsr]-[0-9a-f]{8}` 為 0 筆；`--json` 都有編號。（實作 review 提到卡標題沒填時退用檔名會帶短編號，我沒重現，列 F-10。） |
| 13 | 過 | 每步用 `versions revert` 退，退完整棵樹（排除 versions／turns／journal）比對無殘差：第 1 步 statement add（移除 1）、第 6 步 mount（還原 4）、第 7 步 alias add（含有綜合卡時，還原 2）、第 9 步 ruling add（移除 2：`rulings.json`、`裁示.md`），另測口述為準、redo、entity rename、entity remove 皆乾淨。 |
| 14 | 過 | `rules/synthesis.md` S-11、S-12、S-14、S-26 改了，S-30～S-32 在（見 diff）；`test_rules_synthesis`、`test_rules_core`、`test_budget` 全過（17 tests OK）。 |
| 15 | 過 | `skills/note/SKILL.md` 本文 2660 字（≤ 3000）；分辨有「改知識的話」；「記不起來」只剩憑證例外與「把剛才的答案記下來」；原則含 D-147（糾正先查、客戶說的不寫進檔案卡、講清楚哪種修法）、D-146（改既有的先找）、D-041（規則 vs 裁示），各帶理由；本文沒有指令參數對照；`correction.md` 78 行、`ruling.md` 47 行（≤ 150）；frontmatter 前四行與 `610f2bd` 逐字相同。 |
| 16 | 過 | 見下節。 |
| 17 | 過 | `claude plugin validate` exit 0（警告：缺 icon、README 無 marketplace 安裝行，都是既有，與本題無關）；`plugin-dev:skill-reviewer` 看過 `skills/note/`（見「plugin-dev 審查」，結論「通過但要修」）；`plugin-dev:plugin-validator` 見「plugin-dev 審查」。 |
| 18 | 過 | `python -m unittest discover tests` exit 0（Ran 1631，OK，skipped=1）；README 加「改知識」一段與指令清單、repo `CLAUDE.md` 地圖加 `statements.py`、`rulings.py`、兩個 cli 並改相關列。「不可以動」清單的檔（guard、credentials、cmdparse、turn、versions、hooks、core.md、manage、ingest/SKILL.md、tests/evals、agenda、profile、design、plugin.json、CHANGELOG）`git diff --stat 610f2bd..HEAD` 皆無變動。 |

## 第 16 條：新舊版對照

**實作 session 已跑的證據**（`compare/new-report.md`、`old-report.md`）：新舊各跑 (a)～(h)，每個情境有讀了什麼、指令序列、exit code、回答。舊版（v0.14.0）(a)～(e) 都「沒寫任何檔」，(f) 不寫檔，(g)(h) 走日程與偏好；新版 (a) 記口述、(b) 對原檔後 `cards redo`、(c) `cards mount`、(d) `entity alias`、(e) `ruling add`、(f) 不寫檔並提醒、(g)(h) 結果與舊版相同（最終狀態的 agenda、偏好一樣）。**證據夠看方向，但不夠當最終版的證據**：那輪跑在 `8d211c0` 之後、`7d2dc86`（SKILL.md 與 references 修字）與 `27bfa83`（`cards redo` 連帶標綜合卡，行為變了）之前；且由實作自己跑自己寫的 skill。

**所以我用最終版（HEAD `27bfa83`）自己再跑新版一輪**：開一個全新子代理，只給它 skill 與 core.md、一份新 `<V>` 複本，照 skill 做 (a)～(h)（舊版我引用實作 session 的紀錄，舊版文字沒變）。結果：

| 情境 | 指令序列（最終版） | 回答要點 |
|---|---|---|
| (a) 內湖李改用系統櫃 | `entity list`、`statement list --entity 內湖李 --json`（空）、`statement add …`、`asks --json`、`turn note` | 「記下了：…掛在內湖李。不對說一聲可以退回。」 |
| (b) 單價 8500 不是 5800 | 讀卡、`convert f-2cf7b0a1`（原檔 8500）、`cards redo`（多印 `連帶要更新的綜合卡：信義王`）、`asks`、`turn note` | 「是我抄錯了，原檔寫的是每坪 8500，已標成要重做…信義王的綜合卡也會跟著改」（連帶行是 27bfa83 的新行為，此輪第一次被 skill 走到） |
| (c) 這份報價是大安陳的 | `cards mount`（拒絕：沒登記大安陳）、`entity add --help`、`entity add --type 案子 --name 大安陳`、再 `cards mount` | 回答有說順手登記了；**子代理沒有問就登記了新實體**（F-7） |
| (d) 王董 | `entity alias --help`、`entity alias 王先生 --add 王董 --json`、`asks`、`turn note` | 「以後看到『王董』就當成王先生」 |
| (e) 合約.docx 別信 | `ruling add --kind 不採用 --card f-a82c8cc6 …`（卡是 `合約.txt` 來的，客戶說 .docx，子代理自己指出可能對錯檔） | 「之後整理不再引用『信義王合約』」 |
| (f) 密碼檔可以讀 | 無指令、無 `turn note` | 「這個我這版還記不起來，沒有寫任何檔」＋不要改檔名的提醒 |
| (g) 交屋改 12/15 | `agenda list`、`agenda edit … --date 2026-12-15`、`asks`、`turn note` | 與舊版同；新版沒有舊版多出來的「超過合約工期」建議 |
| (h) 回答短一點 | `profile show --json`、`profile add 偏好 偏好=回答短一點`、`asks`、`turn note` | 與舊版同 |

結論：新版比舊版多做到 (a)～(e)、(f) 多一句提醒、(g)(h) 結果同，符合 spec。**過。** 唯一要設計 session 看的是 (c)：note skill 沒有「沒登記的實體要不要登記」的規則，子代理自己決定登記（F-7）。

## 疑點與建議（標嚴重度）

### 我自己重現的

- **F-1【中高，外洩】口述與裁示不擋密碼。** `statement add --entity 信義王 --text "門禁密碼 password: Abc12345678"` → exit 0；密碼明文寫進 `.system/statements.json`；終端機跑時再寫進工作日誌 `program` 行（`記下口述：…`）與渲染出的 `系統/工作日誌/2026-10.md`。`ruling add --text "Wi-Fi 密碼 password: …"` 同樣寫進 `rulings.json` 與 `系統/裁示.md`。spec 例句 14、15 與 D-145 說這類話「不收」，但目前只靠 skill 文字（`ruling.md` 第三節），程式不擋。專案已有現成做法：`profile.preference_problem` 用 `credentials.mask_secrets(text) != text` 擋偏好。建議 `statements.check_text` 與 `rulings.add` 也走同一套。
- **F-2【中，注入／脹大】`ruling add --text` 沒有字元與長度檢查。** 同一條 `--text` 在 `statement add` 會被擋 `" $ ` \`、200 字，在 `ruling add` 全收：`報價以 $(echo hi) 為準 `x` "q"` 與 500 字都寫進 `rulings.json`、`裁示.md`、`cards todo --json`。skill 叫模型把「客戶原話」放進 `--text "…"`，含 `$(…)` 的原話會在 Bash 層展開（程式端擋不住，但至少該拒絕收下已展開的怪字，並限長度）。建議跟口述同一套 `check_text`。
- **F-3【中低，對客戶說謊】`cards mount` 對沒有 `實體:` 行的卡（範本第一版）回「本來就掛在 X」。** 重現：刪掉卡檔頭 `實體:` 行後 `cards mount f-39d7d1eb --entity 信義王` → exit 0、`「大安陳報價單」本來就掛在 信義王`，檔頭沒有任何變化。`rewrite_entities` 只改既有的 `實體:` 行。
- **F-4【中低】`redo` 記號靠「卡檔 sha256」，卡因別的原因被改寫就悄悄失效。** `cards redo f-2cf7b0a1` 後 `cards mount f-2cf7b0a1 --entity 內湖李` → `todo` 的 `stale` 變空、卡從沒重做，信義王綜合卡 `整理到` 那行仍是 `redo`、綜合卡被當成要更新（`changed` 空）。`entity rename／merge`、`cards sync` 改寫實體行時同樣會踩。客戶要重說一次。
- **F-5【低】壞的 `statements.json`／`rulings.json`／清冊 會擋住 `cards todo`、`kb asks`**（乾淨訊息 `狀態檔讀不到：…`、exit 3，不是崩潰），但日程壞掉明明不擋主流程（`cards.todo` 有 try）。同一個 `validate` 在這情況下回「檔頭解析不了：ValueError」，PostToolUse 會擋每一次綜合卡寫入並給誤導的理由（來自 review，我只重現了 todo／asks 那半）。
- **F-6【低，spec 沒講】收回「取代別人的那條口述」不會讓被取代的那條復活。** s1 被 s2 取代、再 `withdraw s2` → 該實體這件事沒有任何有效口述，s1 也不能再改（`這條已經被取代了`）。客戶說「沒這回事，還是原來的」時事實就掉了。
- **F-7【低，skill】note skill 沒有「沒登記的實體」規則。** `correction.md` 寫「照平常整理的規則判斷要不要登記」，`agenda.md` 寫「不要為了掛日程登記新實體」，`ruling.md` 寫「問是哪一個」，三處不一致；最終版 skill 跑 (c) 時子代理沒問就 `entity add` 了。
- **F-8【低，外觀】** `cards mount` 後舊實體 `removed` 項目 `title` 是 `null`；`synthesis_new` 問句對只靠口述起卡的實體寫「掛了 0 份檔」（review，未重現）；`ruling recheck` 沒有任何變動也回成功句並記日誌（review）。
- **F-9【spec 小缺口】** 「寫三張檔案卡、信義王與內湖李各一張綜合卡」在 `synthesis_min_cards` = 2 時做不到，需要四張卡。另外 fixture 帶的 `案子/信義王/信義王.md` 是壞檔頭，會讓信義王永遠進不了 `synthesis_new`，驗收前要先刪。
- **F-10【低，未重現】** review 指出：卡標題沒填時 `_title` 退用檔名（含 `-xxxxxxxx` 短編號），若標題以 a/f/s/r 結尾會讓人讀輸出匹配到 `[afsr]-[0-9a-f]{8}`；`entity.sync_self` 不呼叫口述／裁示改名跟隨；重複編號的卡（`duplicates`）`redo`／`mount` 只動 `group[0]`；`redo`／`mount` 多步寫入沒有 `_guarded` 倒回；兩檔寫入（`rulings.json` 先、`裁示.md` 後）失敗時不一致。皆列 backlog 建議，未驗。

### skill 審查（`plugin-dev:skill-reviewer`）帶回的，全是 skill 文字品質，沒有程式錯

- **高（文字）**：(1) 例句 7「不對，業主是陳先生不是王先生」`correction.md` 沒有走完的例子，`preference.md` 例 20 還指到它；(2) `ruling.md` 第 20 行把「以後報價都以口述為準」導去改規則，但改規則沒有任何設定能表達它，模型可能寫出一條無效的偏好；(3) `correction.md` 「綜合卡寫錯：直接改綜合卡那一句」跟 S-06／S-24（檔頭要照 todo 給的）與 PostToolUse 的「整理到要現行」會打架，卡正好處於 stale 時會被打回。
- **中**：「先找」原則在 SKILL.md、correction.md 說法不同（找不到要不要問）；`entity merge／rename` 指引太薄（`--to` 沒寫、方向沒寫、搬夾要先讓客戶看影響）；`ruling.md`「要有對應的口述」意思不清；缺「要現在整理嗎」的銜接（客戶說完期待立刻看到）；卡錯與原檔不同時只並列、沒問要不要「以你說的為準」；糾正段沒有「口述記錯了」的路、沒有「沒指名哪張卡怎麼找」的步驟；掛客戶自己的口述不進綜合卡，`correction.md` 卻說會列進待更新綜合卡。
- **低**：「不對說一聲可以退回」跟糾正的「不對」撞字，建議用「退回上一版」；`ruling.md` 表格有不該觸發的拒絕列；`correction.md` 日期寫法與 S-14／S-15 有出入；其他小處見子代理報告。
- 結構面（漸進揭露、本文 ≤ 3000、原則帶理由與編號、成功句與拒絕句對得上程式輸出、`ruling_recheck` 順序）都通過。

## plugin-dev 審查

- `claude plugin validate "<ROOT>"`：通過，exit 0；2 則既有警告（缺 icon、README 沒有 marketplace 安裝行），與本題無關。
- `plugin-dev:skill-reviewer` 對 `skills/note/`：結論「通過但要修」，細節見上節。
- `plugin-dev:plugin-validator` 對整個 repo：通過。manifest 合法、版號仍 0.14.0（沒 bump，符合規定）；`hooks/hooks.json` 五個事件與腳本都在、用 `${CLAUDE_PLUGIN_ROOT}`；`hooks/`、`.claude-plugin/` 對 `610f2bd` 無變動；三個 skill 的 frontmatter 與 `references/` 全部對得上；文件裡提到的 `kb statement|ruling|cards redo|mount|entity alias` 子命令都註冊；沒有憑證或絕對路徑。它說「CLAUDE.md 不在 diff 裡、可能要更新」是它看漏了：`CLAUDE.md` 在 diff 裡且已加新模組與 skill 列（第 18 條已驗）。
- 依據：先讀 `plugin-dev:skill-development` 與 `skill-creator:skill-creator`（描述不改、本文精簡、references 漸進揭露、imperative 寫法）當標準審 `skills/note/`；本題沒動 `hooks/`、`.claude-plugin/`。

## code-review（high）與 security-review

兩個唯讀子代理看 `git diff 610f2bd..HEAD`，都沒讀測試。沒有找到：路徑穿越（`--card`、`--entity`、`--replaces` 只當查表鍵）、寫到總資料夾外、動到守門／憑證／hooks。兩個新狀態檔寫入都在 `FileLock` 裡、寫前 `turn.backup`、原子寫入。我重現了其中 F-1～F-5；其餘（鎖序、undo、`_guarded` 缺口、`group[0]`、sync_self）未驗，列 F-10。

## 停止規則備註

這是第一輪，全驗。建議設計 session 決定：F-1、F-2（外洩／注入）要不要在發行前修；其餘中低與 skill 文字缺口進 backlog。第二輪若有修，我只驗修過的條目加無退步，review 只看新 diff。

## 給設計 session 的 backlog 建議（一行一條）

1. 口述與裁示的文字走 `mask_secrets` 與 `preference_problem` 同一套檢查（F-1、F-2）。
2. `cards mount` 對沒有 `實體:` 行的卡要真的補上或明說做不到（F-3）。
3. `redo` 記號改成不被「只改實體行」的改寫弄丟（F-4）。
4. `synthesis.knowledge` 壞檔不擋 `cards todo`（F-5）。
5. 收回取代者時問要不要讓被取代的復活（F-6）。
6. note skill 補：沒登記實體的規則、「不對，業主是…」走完整、`以後都…` 的拒絕說法、綜合卡手改的限制、「要現在整理嗎」的銜接。
7. 重複編號卡的 `redo`／`mount`、`redo`／`mount` 多步寫入的 `_guarded`、`sync_self` 的口述跟隨。
