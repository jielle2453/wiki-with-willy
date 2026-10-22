# manage 第二版 測試報告

日期：2026-10-22
測試 session：llm-wiki-j-c7（a2 派工）
對象：worktree `C:\Users\Jie\Desktop\llm-wiki-j-manage2`，分支 `spec/manage第二版`，最後 commit `460df19`（樹乾淨）
spec：main 的 `design/specs/2026-10-22-manage第二版.md`（含「討論進度」最後幾條的答覆）
沒改任何程式；本報告是唯一新增的檔（未 commit）。

## 結論（先看這段）

- 單元測試 `python -m unittest discover tests`：**exit 0**（Ran 1945，OK，skipped=1）。
- 驗收 23 條：**22 條過、1 條過但帶一個要你決定的缺陷（18）**；19 是對照，沒有過不過，但看出一個真實問題（見下）。
- 要你決定的事，依重要性：
  1. **「不要讀私人那夾，我不想讓你看到」在真實對話裡走不下去，而且會說錯話**（驗收 19 第 4 句，新舊版一樣）。`SKILL.md:43` 叫模型先 `folders list` 查它在不在來源裡、「不在就說本來就沒在讀」；但 `folders list` 只列來源的根，「私人」是 `客戶案件` 底下的子夾（有檔、有卡、被讀著），模型就回「裡面沒有叫私人的夾，它現在不在讀取範圍內」。客戶要的是隱私，被告知「本來就沒讀」是錯的。D-174 那條流程（hidden → 重寫綜合卡 → purge）在 CLI 上完全正常（驗收 12 過），斷在模型找不到夾。
  2. **例句 19「我自己把報價搬到信義王那夾了」被 manage 接走，兩輪都 3/3**（驗收 18）。數字上 manage 該觸發 29/30、30/30，不該觸發 3/30（剛好 10%）都過，但那 3 次全是這一句。我在暫存複本試了候選描述（加「我自己搬／刪好了、」，128 字）：該觸發 28/30、**不該觸發 0/30**。與 b4 記的「那句錯路由才加」條件吻合，建議加。
  3. **`do_move` 在 `os.rename` 之後任何一步失敗，會說「沒有動：…」，其實檔已經搬了**（我重現）。spec 說被拒時什麼都沒動、全有或全無；這個是搬完後半段的失敗。`versions revert` 救得回，但訊息是錯的。另外 security 子代理重現：`sources.json` 寫失敗時 hidden 排除還指著舊路徑，搬走的夾會變成可讀。
  4. security：`folders purge` 的清理範圍是從 purge 自己要清的備份裡算出來的，第一次中途失敗（例如暫存檔被鎖）重跑會算出空集合、印「清好了」、把 `purge` 記成完成，留下暫存裡含機密字串的檔（子代理重現，我沒重跑）。
  5. security：`kb folders purge "<名>"`、`OTHER_DISK` 的指令字串不跳脫 `$`、反引號；夾名像 `Q3$(cmd)` 會被 PowerShell 展開（子代理重現 plan.quote 那條；我重現了夾名可建、可排除，但沒在需要重寫綜合卡的路徑上跑出那行字）。

其餘（低）在最後「審查發現」一節逐條列。

## 環境

- 模型：`~/.claude/settings.json` 的 `"model": "sonnet"`（沒改）；`claude -p` 實際跑的是 `claude-sonnet-5-5`（stream-json 的 model 欄）。Claude Code 2.1.308。
- 舊 `llm-wiki@llm-wiki` disabled（`claude plugin list` 確認，使用者層 settings 沒動）。
- 所有測試知識庫都是 `%TEMP%` 或 scratchpad 的假庫（用 `tests/` 的 fixture 建，讀它們當建構器，受測的一律經 `python scripts/kb.py` subprocess）；**沒碰 `llm-wiki-test`**，沒動使用者層 plugin 與 settings。
- 替身（驗收 4、8 放不下、21）用 in-process 呼叫 `kb.cli.main` 並 mock `kb._lib.source_edit` 的 `_rename`／`_dev`／`_attrs`／`onedrive_roots`／`shell_trash`／`recycle_room` 與 `kb.convert._attrs`，跟單元測試同一組縫。
- `folders relocate` 的新位置在 `%LOCALAPPDATA%` 底下會被「系統資料夾不能當來源」擋（跟 `folders add` 同一條規則），所以驗收 10 把 `kb.folders.system_dirs` 換成空清單（跟 `tests/test_folders_relocate.py` 同做法）。
- 受測回合都先清掉 fixture 建立時留下的回合紀錄，避免 fixture 的 `create` 備份混進受測回合（一開始看到清冊備份被記成 `create`，查明是我的 fixture 與受測指令落在同一回合，不是缺陷）。

## 驗收逐條

| # | 結果 | 證據 |
|---|---|---|
| 1 | 過 | `move <卡編號> --to 大安陳`：`已搬好：估價單.pdf 從 內湖李 搬到 大安陳；卡跟著改了（說「退回」會搬回去）`；舊位置檔沒了、新位置有；清冊位置換成 `file://客戶案件/大安陳/估價單.pdf`、id 不變；卡搬到 `檔案索引/客戶案件/大安陳/`，`開原檔:` 指新位置（URL 編碼），`原檔:` 行同步；總目錄含新位置；回合夾有 `moves.jsonl` 一行（`op=move kind=file from/to` 都是 uri）、備份 `.system/inventory.json`（modify）、舊卡、總目錄。 |
| 2 | 過 | 19 種拒絕全部 exit 3、句子與 spec 與 b4 補的答覆一字相符：沒登記的夾、`.system`、`檔案索引`、`案子`（型別夾根）、知識庫根、`系統`（都是「「X」是知識庫自己用的地方，不搬進去也不搬出來」）、AI 產出／收件匣當目的地（「不搬進「X」；要放哪個夾跟我說」）、整個實體夾 move／rename（「整個實體夾的改名或換型別用 kb entity」）、型別夾根當對象（「整個型別夾的改名用 kb profile type」）、來源根（「整個登記的資料夾換位置用 kb folders relocate」）、憑證檔名 move／rename／新名字、不存在的檔、副檔名、夾已在那裡、空名。每一項前後快照比對（排除回合簿記）無差異。`unused`／`hidden` 排除的夾當目的地、當來源、改名、刪都拒絕「設成不讀，不搬進去也不搬出來」，無差異。 |
| 3 | 過 | 同名：`目標已經有「估價單.pdf」`、無差異；`--replace`（替身記到回收筒呼叫，路徑是目標那份）→ 第二行 `原本的「估價單.pdf」丟進資源回收筒了`，目標現在是搬來的內容、舊位置沒了；`rename` 成 `.docx` → `副檔名不改：.pdf → .docx`；rename 不寫副檔名 → 接原副檔名。 |
| 4 | 過 | `_rename` 丟 `PermissionError` → `有檔開著（估價單.pdf），關掉再試`；`_dev` 兩邊不同 → `不同顆硬碟不代搬；可以幫你打開兩個夾自己拖：kb source-edit open "<來源夾>" "<目標夾>"`；`os.rename` 真丟 `EXDEV` 也走同一句。三者資料無差異（只多一個 `turns/current.json` 簿記檔）。 |
| 5 | 過 | 同一來源與跨來源各一次（夾 `內湖李`，內含不讀的 `私人`，另有 `公開.txt`）：`--check` 印 `要搬：內湖李（3 份，卡 1 張）從 客戶案件 搬到 …`／`裡面不讀的夾：私人`，前後無差異；實搬 `已搬好：內湖李（3 份）從 客戶案件 搬到 大安陳；不讀的「私人」跟著搬`；`sources.json` 的排除 spec 同來源改成 `大安陳/內湖李/私人`、跨來源則從 `客戶案件` 那條搬到 `報價與合約` 那條（`內湖李/私人`）；`kb guard decide --tool Read` 新位置 `私人/日記.txt` → `deny Z10 excluded`、`公開.txt` → `allow Z9`；清冊裡沒有「私人」的條目、估價單位置跟上、卡搬新夾。小註：「3 份」是實際檔數，含被排除的 `私人/日記.txt`（清冊 active 只有 2）。 |
| 6 | 過 | `output save` 一份 docx 到 `AI 產出/報價/` → `move` 到 `案子/信義王`：位置 `file://知識庫/案子/信義王/定稿.docx`、`origin` 仍 `ai_output`、檔在新位置；收件匣 `貼文.txt` → `客戶案件/大安陳`：位置換、`origin` 仍 `intake`（清冊本來的值）、檔在新位置、收件匣舊的沒了。 |
| 7 | 過 | 單檔、夾（含不讀子夾，`sources.json` 排除回原位）、AI 產出三種各自 `versions revert` 搬回，卡與清冊跟回來（`估價單.pdf：從 大安陳 搬到 內湖李`）；原位先放同名檔再退 → `退不回去：「內湖李」裡已經有「估價單.pdf」了，什麼都沒動`、資料無差異；`moves.jsonl` 改成：from／to 絕對路徑、from／to 含 `..`、沒登記的別名（`「不存在的別名」現在沒有登記`）、磁碟代號段、指進排除夾（from／to 各一次，`在設成不讀的夾裡`）→ 每一種 exit 3、資料無差異。小註：被拒的退回會寫一行 `hook-errors.log`（`versions … moves 退不回`），跟 D-142 folders 路徑被拒同做法；但這是使用者正常的拒絕，不是錯誤，確認情境第 10 步「`hook-errors.log` 不存在」遇到這種拒絕就會破功。 |
| 8 | 過（待處理關閉沒實測） | `trash --check` 三行（`要丟進資源回收筒：估價單.pdf（客戶案件\內湖李）`／`那張卡會拿掉`／`說「退回上一版」救不回來，刪錯要從資源回收筒拿`），前後無差異；放不下（替身上限 10 B）→ `這份 0.0 MB，資源回收筒放不下，刪了救不回來；要刪請在檔案總管自己刪`、檔還在；回收筒用不了（替身）→ `資源回收筒用不了（測試替身），沒有刪`；**真的丟一次**：`已丟進資源回收筒：估價單.pdf；卡拿掉了`，回收筒 `$Recycle.Bin` 裡找到記錄、條目 `deleted`、卡沒了、從這張卡抽的推測日程取消（`agenda list` 空）；退回 → `估價單.pdf 在資源回收筒，要從那裡拿回來`，卡與清冊回來（條目 `missing`）；把檔放回去後 `inventory scan` → 條目 `active`。測試用的回收筒記錄我都清掉了。「待處理關掉」沒實測（fixture 沒有那條 open 的待處理）；程式與單元測試有，這裡不算證據。小註：放不下句寫死單位用 `size_text`，小檔會印 `0.0 MB`（spec 範例是 `X GB`）。 |
| 9 | 過 | 手算（信義王掛 3 張卡：`客戶案件/信義王` 報價 1、`知識庫/案子/信義王` 合約 1、`報價與合約` 合約 1）與輸出一致；把三份的 `first_seen` 改成 08-03、10-15、10-20 後最近日期各自對、依最近排序；`--card-type 合約` 只剩兩夾；`--json` 同；沒登記的實體 `實體清單沒有「沒有這人」，先 kb entity add`；前後無差異（只讀）。小註：同一份內容在兩處有位置（fixture 的 `報價_v2.pdf`）只算在第一個位置的夾。 |
| 10 | 過 | 全對上（`抽樣 5 份：5 份對得上`、`不讀的夾：私人 在新位置找到了`）：`--check` 無差異，實換 `已把「客戶案件」換到新位置：…；說「退回上一版」換得回來`，別名、排除、清冊 uri 不變，卡 `開原檔` 指新位置，`versions revert` 後 spec 與 `開原檔` 回舊；部分對上 `3 份對得上，2 份不在原本的位置（例：信義王/報價_v2.pdf、內湖李/估價單.pdf）`；全對不上照樣印、`--check` 無差異、實換也成功；新位置不在 `找不到 …`；跟別的來源重疊（指到、包住）`和既有來源「報價與合約」重疊，…`；保留別名三個 `「X」是知識庫自己的，不用換位置`；不存在的別名 `沒有叫「X」的來源`。（system_dirs 替身見環境。） |
| 11 | 過 | `exclude --kind unused` 與 `remove` 來源：卡 2→1、2→0，`cards todo --json` 的 `synthesis_stale` 列信義王；退回 → 卡、排除、來源數全回來（`remove` 句：`…卡拿掉 2 張；說「退回上一版」救得回。`）。 |
| 12 | 過 | 用 `tests/test_lib_purge.py` 的 fixture（七項都有東西）：`exclude --kind hidden` → `已排除「客戶案件/私人」（不想讓 AI 看到）。卡拿掉 1 張。`＋`接著要重寫的綜合卡：信義王（拿掉「王家心情」等 1 張卡的內容），寫完跑 kb folders purge "客戶案件/私人"`，`sources.json` 帶 `"purge":"pending"`；綜合卡還引用時 `purge` → exit 3 `還有綜合卡寫著那夾的內容：信義王（先重寫再清）`、無差異；重寫後 `purge` → `清好了：卡 1 張、備份 12 份、暫存 3 份、推算 1 條、工作日誌 3 段；之前讀過的已送過雲端，…`，`purge` 欄改日期；第二次 `「客戶案件/私人」沒有要清的`（exit 0）；全庫（含 `.system/`）grep 機密字串 0 個檔；grep 檔名「日記本」剩 5 處，**全部只是客戶自己那句話「幫我看 日記本.txt」**：`.system/journal/2026-10.jsonl` 的 `prompt`、`系統/工作日誌/2026-10.md` 的「問：」、`.system/turns/current.json`、`.system/turns/<sid>.json`、`.system/versions/<回合>/turn.json` 的 `prompt_head`（逐行看過）。spec 與 b4 補的例外只列了前兩者與 `turns/<sid>.json`，**建議把 `turns/current.json`、`versions/*/turn.json`、工作日誌 md 也寫進例外**。`versions revert` 退到 purged 的那回合印 `那一版因為不讀的夾清掉了，退不回`；繼續退（退「退回」）後機密字串仍 0 個檔。 |
| 13 | 過 | `exclude` 後 `cards todo --json` 的 `plan` 前兩步是 `purge_synthesis`（`read` 指 `rules/synthesis.md`）與 `purge`（`do: kb folders purge 客戶案件/私人`），之後才是 `cards`；重寫綜合卡後只剩 `purge`；`orphans` 帶 `reason`（本機 fixture 沒有 orphan，欄位由 `test_lib_plan` 與單元測試守，這裡只看到空清單）。 |
| 14 | 過 | 原檔刪掉＋`inventory scan` 產生 open 的「原檔不見」待處理後，`synthesis_stale` 的 removed 是 `[]`；`cards drop` 之後 removed 出現該卡。 |
| 15 | 過（未能在原案重現） | 不碰 `llm-wiki-test`，用 fixture 黑箱照 B-068 描述跑：12 張卡 → 搬一份 → `scan` → `cards sync` → 來源多一份新檔 → `scan` → `cards todo --json` 的 `moved` 在搬動後 1、sync 後 `[]`、新檔 scan 後 `[]`（不列 moved）。跟 b4 一樣重現不出來；ingest-check 複本那一步留給確認 session。 |
| 16 | 過 | `source-edit move/rename/trash --check/where`、`versions list`（含搬動那行 `估價單.pdf：從 內湖李 搬到 大安陳`）、`folders relocate`、`exclude`、`purge` 的人讀輸出掃 `[aefsrcp]-[0-9a-f]{8}` 均 0 筆；`source-edit rename --json` 有 `entry_id`，`versions list --json` 有 `moves` 欄；`where --json` 本來就沒有編號可放。 |
| 17 | 過 | description 119 字（≤150）、本文 2775 字（≤3000，`test_budget` 同）、frontmatter 只有 name 與 description、標題 分辨／原則／骨架／一段／參考、沒有表格與「六步」、沒有絕對路徑；`sources.md` 118 行、`files.md` 100 行；`manage.json` 20 句各 10；spec 指定的 19、20 與兩句 produce／answer 近似句都在。 |
| 18 | 過（臨界；一個缺陷要決定） | 見下「路由」一節。 |
| 19 | 對照（見下） | 見下「有無 skill 對照」一節。 |
| 20 | 過（附發現） | `plugin-dev:skill-reviewer`、`claude plugin validate`（Validation passed；警告只有沒有 icon 與 README 沒有 marketplace 安裝句）、code-review、security-review 都做了，見下。 |
| 21 | 過 | 屬性與 `%OneDrive%` 用替身。只在雲端的檔（清冊記 placeholder、沒指紋）`move` 到同一個 OneDrive 的另一夾：id 不變、位置換、條目數 13→13、沒有「原檔不見」待處理、之後 `inventory scan` 搬動 0／不見 0、條目 active；同 OneDrive 的 `--check` 沒有下載那行；搬出 OneDrive 的 `--check` 多 `1 份只在雲端，搬出 OneDrive 會先全部下載（約 0.0 MB）`；`trash --check` 第三行三種各對（只在雲端 `這份只在雲端：會從你所有裝置一起刪，要救得到 OneDrive 網頁的回收筒`、OneDrive 本機 `刪錯要從資源回收筒拿；別台電腦與 OneDrive 上的那份也會一起刪`、一般 `說「退回上一版」救不回來，…`）；成功句兩種對（`已刪：…這份只在雲端，從你所有裝置一起刪了，…`、`已丟進資源回收筒：…別台電腦與 OneDrive 上的那份也一起刪了，…`）；`kb convert` 屬性是只在雲端、清冊記 ok → `讀不到：只在雲端、還沒下載到電腦`，替身把 `extract.probe` 換成一碰就炸的也沒被呼叫，本機屬性對照則照常讀。 |
| 22 | 過 | R-04 現為「…搬動、改名走 kb 程式；刪除經 kb 丟資源回收筒，先講後果、她明確說刪才做。」`test_budget`、`test_rules_core` 綠（整包測試也綠）；grep `rules hooks skeleton skills README` 沒有別處叫客戶自己刪／自己搬的舊句（`rules/synthesis.md:74` S-29 是刪綜合卡、`manage` 拒絕表是拒絕時的話，都不衝突）；produce 兩句與 ingest `commands.md` 兩處已改。 |
| 23 | 過 | `python -m unittest discover tests` exit 0，Ran 1945 OK（skipped=1），樹乾淨；README 與 repo `CLAUDE.md` 在 diff 裡。 |

## 驗收 18：路由（run_eval）

跑法：B-036 的做法。shim 只把 `select.select` 換成永遠就緒（放 scratchpad，skill-creator 原檔沒動；要加 `if __name__ == "__main__"` 保護，不然 Windows spawn 會遞迴），`PYTHONUTF8=1 --runs-per-query 3 --num-workers 1 --timeout 60`，每份用一份 `git archive` 的獨立複本（各自的 `.claude/commands/`）。模型：`sonnet`（`claude-sonnet-5-5`）。限制同前幾份報告：run_eval 只放被測 skill 的暫存指令檔，沒有載 wiki-with-willy，所以不是五個 skill 同時裝著的路由。

| 檔 | 該觸發（10 句×3） | 不該觸發（10 句×3） |
|---|---|---|
| `manage.json` 第 1 輪 | 29／30（96.7%；「不要讀 公司行政 裡面那個薪資的資料夾…」2／3） | **3／30**（「我自己把報價搬到信義王那夾了」3／3） |
| `manage.json` 第 2 輪 | 30／30 | **3／30**（同一句 3／3） |
| `manage.json` 候選描述（試跑，不在 repo） | 28／30（兩句 2／3） | **0／30** |
| `ingest.json` | 30／30 | 0／30 |
| `produce.json` 第 1 輪 | 30／30 | 3／30（「那份報價寫錯了，總價是 128 萬」3／3） |
| `produce.json` 第 2 輪 | 30／30 | 3／30（同一句 3／3） |
| `produce.json`（改動前 `1f009a4`，同樣跑法） | 見下 | 見下 |

`query failed`／WinError 都是 0。

- **manage**：門檻（≥90%、≤10%）數字上過，但不該觸發的 3 次全是例句 19「我自己把報價搬到信義王那夾了」，兩輪都 3/3，是穩定的錯路由，不是取樣雜訊。spec 要我特別記的就是這句：manage 會接走，模型要 `source-edit move` 一份已經搬好的檔，只會得到「找不到這個檔」。候選描述在 `；改檔案內容、…` 前加一句「我自己搬／刪好了、」（128 字，仍 ≤150）：不該觸發 0/30、該觸發 28/30（掉的兩句是「不要讀 公司行政 裡面那個薪資的資料夾，那是私人的」與「剛做好的定稿要放哪？你決定就好」各 2/3），仍 ≥90%。skill-reviewer 也提同一條（Major 1）。建議加；這是 b4 說「那句錯路由才加」的條件。
- **ingest**：30/30、0/30，沒退步（上次 29/30、0/30）。
- **produce**：該觸發 100%。不該觸發多一句「那份報價寫錯了，總價是 128 萬」3/3（兩輪都是）；上一份 produce 報告同一組是 0/30。這個 branch 對 produce 只改了本文兩行（`SKILL.md:27、41`），**run_eval 只讀描述**，描述沒動；改動前 `1f009a4` 我另跑一輪是 1/30（同一句 1/3），見最後「produce 改動前對照」。結論：不是這個 branch 造成，但數字上比上一份報告差，請 a2 決定要不要算「退步」。
- `note.json`、`answer.json` 這個 branch 沒動描述，也沒重跑（run_eval 彼此獨立）。

## 驗收 19：有無 skill 對照（舊版 `1f009a4` vs 新版 `460df19`）

做法：同一份基底知識庫（`tests/test_lib_purge.py` 的 fixture：客戶案件有 `私人/日記本.txt`、一張掛信義王的卡、信義王綜合卡引用它；另補 `客戶案件/2026/`；全部整理成卡、`cards sync`），每句各一份新複本；`claude -p "<句子>" --plugin-dir <plugin 快照> --output-format stream-json --permission-mode acceptEdits --allowedTools "Bash Read Grep Glob Write Edit Skill"`，一句一個回合（需要確認的話停在確認那一步）。「報價夾搬到 E 槽」先在複本裡把 `報價與合約` 夾搬到 `E槽\`。模型 `claude-sonnet-5-5`。

| 句子 | 舊版 | 新版 |
|---|---|---|
| 「這份估價單搬到 2026」 | 載了 **produce**（不是 manage），`lookup 估價單`、讀了三張卡，最後回「不確定是哪一份、『搬到 2026』是改成 2026 年新版還是搬進資料夾」，反問。沒動。 | 載 **manage**，讀 `files.md`、找到唯一一份估價單、`folders list`、`ls` 看 2026 夾，`source-edit move` 一次做完，記一句 `turn note`，回「已經把「估價單.pdf」從「客戶案件/內湖李」搬到「客戶案件/2026」，檔案卡也跟著改了；搬錯了說「退回」」。磁碟上確實在 `客戶案件/2026/`。小缺點：回答尾巴多提「有一次被擋下來：公司行政底下有個檔名像密碼的檔案」，跟這件事無關。 |
| 「舊備忘幫我刪掉」 | 載 **note**，找到 `Big5備忘.txt` 與兩條日程，回「按規則我不刪客戶原檔，要請你自己在資料夾裡刪」，另問是不是日程那兩條。沒動。 | 載 **manage**，只找一份名字有「備忘」的 `Big5備忘.txt`，`source-edit trash --check`，照三行講後果（進資源回收筒、卡拿掉、退回救不回來、要從回收筒還原），請她明確回「刪」或「丟掉」才做。磁碟上沒動。 |
| 「報價夾搬到 E 槽了」 | 載 manage（舊版的六步／「拿掉舊的再加新位置」做法），說要請她貼路徑、比對、再確認才拿掉舊的。沒動。 | 載 manage，`folders list`，說「報價與合約」現在找不到，請她貼完整路徑；拿到後會先比對再把來源改過去（`relocate`）。沒動。兩版都停在要路徑，新版說法對得上 relocate。 |
| 「不要讀私人那夾，我不想讓你看到」 | 載 manage，`folders list`，說「裡面沒有叫私人的夾，本來就沒在讀」。沒動。 | 載 manage，`folders list`，說「來源只有三個，沒有叫『私人』的來源，所以它現在不在讀取範圍內」，要她貼完整路徑、選用不到或不想讓 AI 看到。沒動。 |

觀察：
1. 前三句新版明顯比較好：舊版沒有 skill 能搬、刪，搬被 produce 接走、刪被 note 接走並叫客戶自己刪；新版搬一次做完、刪先講後果。
2. **第 4 句兩版都錯，新版沒有修好。** 磁碟上 `客戶案件/私人/` 存在、有卡、被讀著，但 `folders list` 只列來源根；`SKILL.md:43` 還叫模型「不在就說本來就沒在讀、不從別的路徑推」。結果客戶要的是「不要讓你看到」，模型回「它現在不在讀取範圍內」。這是例句 3 的主流程，應該在 `SKILL.md:43` 補：名字不是來源別名時，先在來源底下找有沒有同名的夾（`Glob`、`cards list`），找到再往 `exclude` 走，找不到才說沒有。
3. 沒有英文自言自語、沒有內部編號出現在給客戶的文字裡（前三句的 `kb` 指令序列在過程，不在回答）。

## 審查發現

我自己重現的標「已重現（我）」；子代理說重現但我沒重跑的標「子代理重現」；只讀碼的標「讀碼」。

### code-review（high，子代理）

1. **後半段失敗說謊**（已重現（我））：`do_move` 的 `os.rename` 成功後，`record_move`／`_rewrite_excludes`／清冊／`cards.sync` 任何一步丟例外，CLI 印「沒有動：…」或「等不到鎖，沒有動」，但檔已經在新位置、清冊已指新位置、`moves.jsonl` 有那行。我把 `cards.sync` 換成丟 `PermissionError`：輸出 `3 沒有動：拒絕存取`，檔已搬走；`versions revert` 搬得回來。建議搬完之後的失敗改說「已搬好，但後面沒做完，說「退回」可以退回」，或失敗就把 rename 倒回。
2. `--replace` 先丟目標再 rename，rename 失敗時舊目標已進回收筒、卻說「什麼都沒動」（讀碼）。
3. 唯讀檔 `trash` 被當成「有檔開著」（已重現（我））：`_writable` 用 `open(path, "r+b")`；把 `估價單.pdf` 設成唯讀後 `trash` → `有檔開著（估價單.pdf），關掉再試`（`--check` 正常）。同一檢查在 `--replace` 也會誤擋。
4. 搬進（或改名成）`.` 開頭、`~$` 開頭或隱藏屬性的名字（已重現（我））：`move` 到 `客戶案件/.隱藏` 成功，但 `inventory scan` 把該條目標 `missing` 並開「原檔不見」待處理（`條目 status: missing`、`p-… 原檔不見 「估價單.pdf」（客戶案件/.隱藏）不見了`）；`rename` 成 `.隱藏的`、`~$暫存` 的 `--check` 都放行。建議這兩類拒絕。
5. 新名字含點被當成換副檔名（已重現（我））：`2026.10 報價` → `副檔名不改：.pdf → .10 報價`；`v1.2`、`Dr. Wang` 同。
6. 退回只動最新一回合，最新那回合退不了就卡住整串（子代理與 code-review 都提；我重現了其中「原位先放同名檔 → 整個不退，exit 3」；沒有測「更舊的回合因此退不了」那個後果，只讀碼）。要有跳過或標記的辦法。
7. `folders purge --json` 被拒時印人話句不是 JSON（讀碼）。

### security-review（`1f009a4..460df19` 的 `scripts/`，子代理）

乾淨的部分（它們重現或讀過）：source-edit 目標的 `..`、`\\?\`、UNC、磁碟代號、junction、ADS、尾點尾空白、大小寫別名；憑證檔名（含父夾、新名字、整夾搬進知識庫）；Z10／Z1／系統區／型別夾根；`--replace` 重跑 plan_trash 的檢查；`trash` 的 `pFrom` 雙 NUL、旗標、失敗不退成直接刪；`open` 只開驗過的夾；退回的 `_move_path` 擋 `..`、磁碟代號、UNC、尾點、`:` 段、排除裡的路徑與保留根（子代理的結果跟我自己做的驗收 2、7 一致）。

發現：
1. **中**（子代理重現）purge 的範圍從 `versions/` 的清冊舊版算出來，卻先把那些舊版清掉；暫存檔被鎖時第一次失敗、重跑算出空集合並把 `purge` 記成完成，留下 `.system/temp/f-….png` 與 `.system/temp/office/<回合>/日記本.txt`（含機密字串）。修法：把 ids 與檔名先記在排除列，全部成功才標完成。
2. **中**（子代理重現）同 code-review 1：`sources.json` 寫失敗時 rename 已發生、hidden 排除還指舊路徑，搬走的夾分類成 Z9 可讀、下一次掃描登記；CLI 還說「沒有動」。
3. **中**（子代理重現 `plan.quote`）`folders.py:419` 的 `kb folders purge "{name}"` 與 `purge_command`、`source_edit.py:36` 的 `OTHER_DISK` 不跳脫；夾名可含 `$`、反引號（`ALIAS_BAD_CHARS` 與 `name_problem` 沒擋，實體名稱有擋），`Q3$(cmd)` 被 PowerShell 展開。我確認該夾名可建、可排除（排除成功，沒有綜合卡要重寫時 purge 內嵌做完，不印那行指令）。
4. 中低：退回被擋時整串卡住（同 code-review 6）。
5. 低：`_version_dirs` 跟 junction、`purge.py:398-419` 重渲染前沒 `_under_files` 檢查（要先寫得進 `.system`，守門擋，屬縱深防禦）。
6. 低：`names` 遮罩是不分大小寫的子字串（隱藏檔叫 `data` 會改寫整個 ledger 的 `data`）；`ledger.tsv`、`hook-errors.log` 讀改寫沒鎖，與 hook 附加並行會掉一行。
7. 低（未驗）：沒有 `GetDriveType == DRIVE_FIXED` 檢查，可移除磁碟上 `FOF_ALLOWUNDO` 會直接永久刪、事後的「檔還在原位」檢查看不出來；這台只有 C:，沒法測。**請在隨身碟上確認或補檢查**（刪檔是不可逆的）。
8. 低：Z1 綜合卡、憑證檔名、實體夾規則在退回時沒重驗（偽造 `moves.jsonl` 要寫 `.system/versions`，守門擋）。
9. 資訊：`relocate` 時 hidden／unused 排除在新根不存在，只印「找不到」，排除默默失效。

### skill 審查（`plugin-dev:skill-reviewer`，只讀；沒有 Bash 所以沒跑 diff）

Pass，3 個 Major：
1. 描述沒擋「我自己搬好了」（就是驗收 18 那句）；ingest 的「我刪了」也只靠時態區分。建議加「我自己搬／刪好了」。
2. `files.md:73`：`where` 印的是「報價與合約/信義王」「知識庫/案子/…」（別名/相對路徑），`move --to` 對沒有 `file://` 的文字當總資料夾相對路徑，直接餵會得到「找不到這個夾」；要補一句轉 `file://<那一行>`。（讀碼；我沒用 CLI 驗這個轉法。）
3. `skills/ingest/references/commands.md:114`「型別底下還有 N 項，先搬走 → 走 manage」，但底下的項目多半是整個實體夾，manage 會拒絕（`整個實體夾的改名或換型別用 kb entity`），兩邊繞圈；只有「夾裡的檔」才走 manage。

Minor：`sources.md:32` 說 hidden 的卡拿掉「退回救得回」不準（purge 後退不回，跟原則 8 矛盾）；`files.md:58` 的「程式都處理好了」沒講綜合卡誰、何時更新；`SKILL.md:43` 先 `folders list` 才 `exclude` 這步多餘且是上面第 2 點的元兇；原則 3 與 8 重複；`files.md:6` 只寫 `f-…`，程式也收 `out-…`；`--replace` 成功句與雲端刪除成功句的「；卡拿掉了」沒寫；`files.md:96` 寫死 `GB`；`sources.md` 的 `--json` 欄位表漏幾項；「定稿放到王宅那夾」沒說怎麼從「王宅那夾」找候選夾。produce 兩句與 manage 一致。

### plugin 驗證

- `plugin-dev:plugin-validator` 對整個 worktree：PASS，無 critical、無警告（manifest、hooks.json 五個事件與 `${CLAUDE_PLUGIN_ROOT}`、五個 SKILL.md frontmatter 與描述字數 119～142、13 個 references 都有連結、`sys.path` 只在 hooks、沒有追蹤到的機密）。
- `claude plugin validate "<ROOT>"`：Validation passed with warnings（無 icon；README 沒有 marketplace 安裝句）。

## 其他觀察（不影響過不過）

- `versions list` 對一個搬動回合顯示「成品 1」：是我的 fixture 條目的 `created_turn` 和受測回合撞了同一秒的 id，不是這個 branch 的行為；列在這裡只是提醒回合代號只到秒。
- `kb convert` 的人讀輸出現在印 `已記進待處理（p-…）`（舊行為，非本題）。
- 驗收 8 的「待處理關掉」、驗收 13 的 orphans `reason` 在黑箱層沒有 open 的資料可看，這兩項靠單元測試。
- 驗收 15 與確認情境 B-068 那步（桌面版 ingest-check 複本、開新對話看開場）沒做，照 spec 留給確認 session。

## produce 改動前對照

`1f009a4`（這個 branch 之前）的 `produce.json`，同樣跑法、單獨跑：該觸發 30/30、不該觸發 1/30（還是「那份報價寫錯了，總價是 128 萬」，1/3）。新版兩輪是 3/3、3/3。produce 描述兩個 commit 一字不差（diff 只動本文兩行），run_eval 不讀本文，所以這句是邊界句、取樣在 1/3 到 3/3 之間擺，不是這個 branch 造成的退步；但它每次都會被 produce 接走一部分，之後調 produce 描述時順便看。

---

# 第二輪（重測 d7b3f23）

日期：2026-10-22
對象：worktree 同一分支，commit `d7b3f23`（樹乾淨；修的 10 條見 spec「討論進度」最後三條）
做法：第一輪的 16 支驗收腳本原樣重跑（全部沒有退步）、修的每一條照第一輪的重現做法再跑一次、新增的東西另外測、`1f009a4..d7b3f23` 的 security-review 與 `460df19..d7b3f23` 的 code-review（子代理，唯讀；我自己重現的標「已重現（我）」）、run_eval、驗收 19 第 4 句。沒改任何程式。模型同第一輪：`sonnet`＝`claude-sonnet-5-5`。

## 結論（第二輪）

- 單元測試 `python -m unittest discover tests`：**exit 0**（Ran 1969，OK，skipped=1）。
- 第一輪 5 件事（外加隨身碟）：**修好 2 件（描述、purge 範圍）、大致修好 2 件（搬完失敗倒回、隨身碟）、部分修好 2 件（私人那夾、指令跳脫）**。
- 還要處理的，依重要性：
  1. **PowerShell 彎引號注入**（已重現（我））：夾名含 `”`（U+201D）等彎引號，`plan.shell_arg` 與 `source_edit.shell_arg` 都不處理。夾名 `x”; Write-Output PWNED; “y` 產出的指令字串 `"x”; Write-Output PWNED; “y"` 在真的 PowerShell 裡被拆成 `x`、`PWNED`、`y`，中間那段當成第二個陳述式執行。`$`、反引號已修好，這是漏網的另一類。修法：夾名含 U+2018～U+201F 也比照「同時有 `$` 和 `'`」給不出指令。
  2. **遮罩漏（隱私）**（已重現（我））：第 9 條把遮罩改成「只換完整路徑」，但路徑前面只認空白、引號、括號。`已搬好：客戶案件/私人/日記本.txt`（全形冒號）、`note:…`、`path=…`、`[…]`、`→…` 後面的路徑，purge 之後原封不動留在 `hook-errors.log`（絕對路徑前面是「絕對：」也漏）；專案自己的訊息就常用「：」接路徑（例：`cards.sync` 的「沒搬：」）。第一輪那條「名稱子字串遮罩」過寬，現在過窄，兩邊都沒到位。
  3. **搬完成功卻說「沒有動」**（已重現（我））：`do_move` 最後一行 `cards.refresh_synthesis(...)` 在 try 外面；它丟 `OSError`（例如鎖逾時）時，檔已搬好、清冊已換，CLI 印 `沒有動：locked` exit 3。第 3 條要的是「搬完之後任何一步失敗倒回或說實話」，這一步漏了。
  4. 其餘中低：F1 `undo.rollback()` 與 `restore_sources()` 同一個 try，rollback 丟錯就不還原 `sources.json`（子代理重現：hidden 的舊 spec 被留成只剩新 spec，倒回原位的夾變可讀）；F4 purge 中途 `OSError` 時 manifest 沒重寫，重跑對這些列 `continue` 掉，回報「清好了」但 manifest 還留著卡路徑與總目錄舊版裡的夾名（子代理重現）；ledger／hook-errors 的鎖只鎖 purge 這一邊（已重現（我），見下）；`_version_dirs` 跟 junction（已重現（我），見下）。
  5. **「不要讀私人那夾」還不穩**（驗收 19 第 4 句，見下）：說錯話的毛病修好了（沒有一次再說「本來就沒在讀」），但載了 manage 的 9 次執行裡只有 1 次真的設成不讀；其餘不是找不到夾就是被守門擋住，另外 4／13 次完全沒載 manage、只口頭答應「我不會讀」（沒有登記任何東西）。
- 路由數字：manage 三輪 27／30、30／30、26／30（中位數 90.0%），不該觸發 0／90；例句 19 前兩輪各 3／3 錯路由，現在 0／9。

## 修的每一條逐條驗

| 項目 | 結果 | 證據 |
|---|---|---|
| 5 件事 (1) 私人那夾 SKILL | 部分 | `SKILL.md:43` 已改成「在登記的來源底下找那個夾名」。d7b3f23 載了 manage 的對話沒有一次再說「本來就沒在讀」（舊版 5／5、`460df19` 3／3 說了）。但只有 1／13 次完整做完（`folders list` → `exclude "<完整路徑>"` → `cards todo` → 重寫綜合卡 → `folders purge` → `turn note`，回答正確）。其餘：模型用 Glob（vault 為 cwd，看不到 `../raw`）或 PowerShell 遞迴掃 `raw` 根，掃到 `公司行政` 裡有檔名像密碼的檔就被守門擋下，模型守規矩停手，請客戶貼完整路徑；或根本沒載 manage。沒有說錯話，但客戶得自己貼路徑。建議給模型一條不被擋的路：`kb folders find "<名>"`（在登記的根底下找夾名，kb 自己走，守門單一 kb 呼叫放行）。 |
| (2) 描述加「我自己搬／刪好了」 | 修好 | 描述 128 字（≤150）。manage.json 不該觸發 **0／30、0／30、0／30**；例句 19 前兩輪各 3／3，現在 0／9。代價見 run_eval。 |
| (3) 改名之後失敗倒回 | 大致修好 | 已重現（我）：`cards.sync` 丟 `PermissionError` → 檔回原位、清冊與卡回原樣、`沒有動：拒絕存取`；`record_move` 丟錯同；含 hidden 子夾的夾搬到一半第二次寫 `sources.json` 失敗 → 夾回原位、`sources.json` 與原本逐位元相同、原位私人檔仍 `deny Z10`；改名那一刻 `sources.json` 裡新舊兩條排除都在（`['內湖李/私人', '大安陳/內湖李/私人']`），搬完剩新的；倒不回去（第二次 rename 也失敗）→ `已搬好，但後面沒做完；說「退回」可以退回`，`versions revert` 搬得回來；倒回後留下一個空的「修改 清冊」回合，退回它沒有副作用。**漏的**：結論 3（`refresh_synthesis` 在 try 外）與 F1（rollback 失敗就不還原 sources）。 |
| (3) `--replace` | 修好（一般失敗） | 已重現（我）：暫名改名失敗（`PermissionError`）→ `有檔開著`，舊目標沒丟、來源在原位、沒有暫名殘留；丟回收筒失敗 → `資源回收筒用不了（…），沒有刪`，兩邊都沒動；丟成功後 `暫名→目標名` 失敗、搬回原位成功 → `原本的「估價單.pdf」已經丟進資源回收筒，要從那裡拿回來；這份沒搬過去（…），還在原位`；雙失敗 → `…這份搬到一半，請服務人員看`，暫名檔 `.kb-move-xxxx.pdf` 留在目標夾（掃描略過 `.` 開頭，清冊顯示該筆「不見」＋待處理）。一般成功回歸正常。小註：雙失敗那句沒講暫名檔叫什麼；子代理另重現「進程被殺在改暫名之後」檔留成 `.kb-move-xxxx`、`versions revert` 搬不回，建議改暫名前先寫一行記錄。 |
| (4) purge 範圍先記、重跑照記 | 修好 | 已重現（我）：`exclude --kind hidden` 當下排除列帶 `purge_ids`（卡編號）與 `purge_names`（檔名雜湊），`sources.json` 沒有明碼檔名也沒有機密字串；暫存檔被鎖（`Path.unlink` 與 `os.remove` 換成對 `*_p1.png` 丟 `PermissionError`）→ 第一次 exit 3 `還沒清完：暫存 1 份；關掉開著的檔再跑一次 kb folders purge 客戶案件/私人`，排除列仍 `pending` 且範圍還在、殘留 temp 一個；重跑成功、temp 清空、`purge` 改日期、`purge_ids`／`purge_names` 拿掉。**同一回合先掃描再排除**（新檔→scan→寫卡→`exclude hidden`）：purge `清好了：卡 1 張、備份 1 份…`，grep 機密字串與檔名 0 個檔。**漏的**：F4（manifest 列與總目錄舊版在中途 `OSError` 時沒重寫，子代理重現）。 |
| (5) 指令跳脫 | 部分 | 已重現（我）：`$(echo PWNED)`、反引號夾名 → 指令改單引號 `kb folders purge '客戶案件/Q3$(echo PWNED)'`，`cards todo --json` 的 plan 同；夾名同時有 `'` 和 `$` → 不給指令，`接著要重寫的綜合卡：…夾名同時有 $ 或反引號和單引號，清殘留的指令給不出來；請她先在檔案總管改夾名…`，plan 的 `purge.do` 是 `null`；`OTHER_DISK` 夾名有 `$` 時是單引號、特殊到給不出時是另一句。**漏的**：彎引號（結論 1，已重現（我））；草稿檔名同時有 `'` 和 `$`（例 `AI 產出/Joe's $500 quote（草稿）.md`）時 `plan.cmd` 丟 `ValueError`（已重現（我）），子代理重現 `kb asks`／`cards todo` 因此整個 exit 3。 |
| (5) 只收固定磁碟 | 修好（替身） | `_drive_type` 換成 2／4／0：`trash --check` 與 `trash` 都 `資源回收筒用不了（隨身碟或網路磁碟），沒有刪`，檔還在；`--replace` 目標在可移除磁碟 → 同句、兩邊都沒動；真實 `GetDriveTypeW` 回 3，真的丟一次回收筒仍成功（回收筒記錄已清）。**沒有隨身碟可試**，請找一支確認。 |
| (6) 唯讀、隱藏名字、副檔名 | 修好 | 唯讀檔 `trash` → `這份是唯讀的，先取消唯讀再刪`（`--check` 照常 exit 0）；rename 成 `.隱藏的`、`~$暫存`、`  .x`、move 進 `.隱藏`、進隱藏屬性夾 → `這種名字知識庫不會讀，換個名字`；副檔名：`2026.10 報價`、`v1.2`、`Dr. Wang`、`估價單.pdf`、`估價單.PDF`、`報告.zzz`（補成 `報告.zzz.pdf`）、`報價.v2.final` 都放行；`.docx`、`.xlsx`、`.jpg`、`.doc` 拒絕。新發現（已重現（我））：Windows 裝置名 `NUL`、`con`、`COM1`、`aux.pdf` 放行（`folders.py` 的別名有擋、rename 沒有），之後檔案總管打不開、刪不掉，低。 |
| (7) 退回被擋的說法 | 修好；B-410 沒做 | 原位有同名：`退不回去：「內湖李」裡已經有「估價單.pdf」了；先把那份改名或搬走再說退回，什麼都沒動`；`moves.jsonl` 指到沒登記的別名／排除裡：`…要退回得先把它加回來`／`…要退回得先取消不讀`；預期中的拒絕不再寫 `hook-errors.log`（上面三種都沒有；路徑被改壞的仍寫，並說請服務人員看）。最新回合被擋時仍整串擋住（已重現（我）：最新回合的檔被客戶自己搬走，退不回，更舊的改名那回合也退不了）；這是 spec 寫的「B-410 不做」，訊息有說怎麼解開。子代理提醒：日後做 B-410 時，退回更舊的回合會把舊備份蓋在新狀態上，要先設計。 |
| (8) relocate 擋 hidden 對不上 | 修好 | 新位置缺 hidden 夾：`--check` 印 `不讀的夾：私人 在新位置找不到`（exit 0），不帶 `--check` → exit 3 `不想讓 AI 看到的「私人」在新位置找不到；先說它現在叫什麼，或重新設不讀，再換過去`，`sources.json` 沒動；新位置有 → 照換。`unused` 的找不到仍只是提示。子代理指出死路：該夾已被 purge 刪掉、新舊位置都沒有時，relocate 要先有排除、`exclude` 要先在登記的根底下，只能先 `include` 舊列（`sources.md` 沒寫），我沒驗，低。 |
| (9) 遮罩、鎖、purge --json | 部分 | `purge --json` 被擋也印 JSON（已重現（我））：`{"purged": false, "name": "客戶案件/Q3$(echo PWNED)", "blocked": ["信義王"], "error": "還有綜合卡寫著那夾的內容：信義王（先重寫再清）"}`。遮罩：完整路徑被換成「（已排除）」，自由文字裡單獨提到的檔名（`無關：提到 日記本 的筆記 metadata`）留著、`metadata` 沒被誤改；**但**前面是全形冒號、`:`、`=`、`[`、`→` 的路徑沒被遮（結論 2，已重現（我））。鎖：`_scrub_text` 對 `ledger.tsv`、`hook-errors.log` 拿 `FileLock`，但 `ledger.append`／`log_error`（hook 呼叫的）沒有用同一把鎖。已重現（我）：在 purge 讀改寫的中間讓 `ledger.append` 追加一行，寫回後那行不見。第 9 條的目的（hook 追加不掉行）沒達到。 |
| (10) skill 與文件 | 大致修好 | `move --to` 收 `where` 印的「別名/相對路徑」：`客戶案件/大安陳`、`知識庫/案子/信義王`、反斜線、尾端 `..` 都能 `--check`／實搬；`客戶案件/../.system`、`知識庫/.system`、`知識庫/系統`、`AI 產出`、`收件匣`、`不存在別名/x`、`客戶案件/../../x`、排除夾的 alias 形式全部拒絕（零逃逸）。`ingest/references/commands.md:114` 改成「實體換型別用 kb entity，夾裡的散檔問她要不要幫她搬（走 manage）」；sources.md hidden 的卡 purge 後退不回；原則 3、8 合併；`out-…`、兩個成功句、不寫死 GB、`--json` 欄位補齊都有。描述 128 字、本文 2853 字、references 128／109 行。還沒修的 Minor 兩條：`files.md:62`「程式都處理好了」沒講綜合卡誰更新；「定稿放到王宅那夾」沒說怎麼找候選夾。 |

## 第一輪 code-review 7 條、security 9 條

| 來源 | 條 | 結果 |
|---|---|---|
| code-review | 1 後半段失敗說謊 | 大致修好（見 (3)；漏 `refresh_synthesis`，已重現（我）） |
| | 2 `--replace` 先丟目標 | 修好（一般失敗）；子代理另重現丟成功後 `do_trash` 內部失敗被說成「沒有動」，我用 `inventory._write` 丟錯沒重現（我這邊印的是對的句子），不確定 |
| | 3 唯讀檔 | 修好 |
| | 4 `.`／`~$`／隱藏名字 | 修好 |
| | 5 名字含點 | 修好 |
| | 6 退回卡死 | 沒修（B-410 不做），訊息改好 |
| | 7 purge --json | 修好 |
| security | 1 purge 範圍 | 修好（F4 殘留） |
| | 2 改名後失敗 | 大致修好（F1、`refresh_synthesis`） |
| | 3 `$`／反引號跳脫 | 部分（彎引號漏，已重現（我）） |
| | 4 退回卡死 | 沒修（B-410） |
| | 5 junction | **沒修**（已重現（我）：`.system/versions/zz-junction` 指到庫外的資料夾，裡面有一份編號相符的卡舊版與 manifest，purge 跑完那份庫外的檔被刪了、manifest 被改成 `purged`）。要先能寫 `.system`（守門擋），縱深防禦，低 |
| | 6 遮罩與鎖 | 沒修（遮罩過窄、鎖不起作用，見上） |
| | 7 磁碟類型 | 修好（替身；隨身碟沒測） |
| | 8 退回沒重驗 Z1／憑證／實體夾 | 沒修（已重現（我）：`moves.jsonl` 的 `from` 改成 `file://客戶案件/內湖李/密碼.pdf`，`versions revert` exit 0，檔被搬進憑證檔名；`.system` 仍被擋）。要先能寫 `.system`，縱深防禦，低 |
| | 9 relocate 吃掉排除 | 修好（hidden）；`unused` 仍只提示 |

## 新增的東西

- `purge_ids`／`purge_names`：`sources.json` 裡沒有明碼檔名、夾內內容或機密字串（已驗）。`purge_ids` 是卡編號；`purge_names` 是 `sha256(casefold(檔名))[:16]`，**沒有加鹽**（我算過：`日記本.txt` 的雜湊就是 `a2b04eda23bf5531`）。檔名猜得到的（例：`薪資表.xlsx`）可以字典比對；清完會拿掉，低。
- 同一回合先掃描再排除：清乾淨（見 (4)）。
- `--replace` 暫名 `.kb-move-xxxx`：一般失敗說實話（見 (3)）；被殺掉的視窗留下檔但無記錄，低。
- 子代理另列、我沒驗：purge 遇到不能解碼的 `ledger.tsv` 會永遠 exit 3（F7）；`_current_state` 的壞 `agenda.json` 讓 purge 永遠 pending（F8，提示「關掉開著的檔」不適用）；`sources.md` 說 `exclude --json` 帶 `purge_ids`／`purge_names`，子代理說實際 JSON 是舊的 `row` 物件沒有這兩個鍵（F9，讀碼）；`plan.shell_arg` 雙寫反斜線是 bash 對、PowerShell 不對（讀碼）。

## run_eval（d7b3f23）

跑法同第一輪（shim、`--runs-per-query 3 --num-workers 1 --timeout 60`、各自的 `git archive` 複本）。模型 `sonnet`＝`claude-sonnet-5-5`。

| 檔 | 該觸發（10 句×3） | 不該觸發（10 句×3） |
|---|---|---|
| `manage.json` 第 1 輪 | 27／30（90.0%；「不要讀 公司行政 裡面那個薪資的資料夾…」1／3、「剛做好的定稿要放哪？…」2／3） | **0／30** |
| `manage.json` 第 2 輪 | 30／30 | **0／30** |
| `manage.json` 第 3 輪 | 26／30（86.7%；薪資那句 2／3、「定稿要放哪」0／3） | **0／30** |
| manage 三輪 | 83／90（92.2%），中位數 27／30（90.0%，剛好在門檻上） | **0／90** |
| `produce.json` | 30／30 | 2／30（「那份報價寫錯了，總價是 128 萬」2／3）；改動前 1／30，第一輪 3／30，描述沒動，取樣擺盪 |
| `ingest.json` | 30／30 | 0／30 |

- 例句 19 的錯路由修掉了（第一輪 3／3、3／3，現在三輪都 0／3）。
- 代價：manage 該觸發從第一輪（29／30、30／30）掉到 27／30、30／30、26／30，掉的兩句是「定稿要放哪」（0～3／3 擺盪）與「不要讀…薪資的資料夾，那是私人的」（1～3／3 擺盪）。中位數仍壓在門檻上，沒過線也沒有餘裕。第二句就是 D-174 的主流程，擺盪不得不重視。
- 另外在暫存複本試了一個描述（不在 repo）：在引號例句最前面加「不要讀私人那夾，我不想讓你看到」，145 字（≤150）。用驗收 19 第 4 句各跑 8 次：現描述載 manage 5／8、真的 `exclude` 0／8（3 次只口頭答應「我不會讀」）；候選載 manage **8／8**、真的 `exclude` 2／8（其餘是找不到夾或被守門擋）。**沒有用候選描述重跑 manage.json**，若採用請 a2 排一輪。

## 驗收 19 第 4 句（「不要讀私人那夾，我不想讓你看到」）

做法同第一輪：基底知識庫有 `客戶案件/私人/日記本.txt`、一張卡、信義王綜合卡引用它；每次一份新複本；`claude -p` 一句一個回合；舊版 `1f009a4`、第一輪新版 `460df19`、本輪 `d7b3f23` 各 5 次，`d7b3f23` 另 8 次（候選對照用同一批 8 次）。

| 版本 | 載 manage | 說「本來就沒在讀」 | 真的 exclude 並重寫綜合卡、purge |
|---|---|---|---|
| `1f009a4` | 5／5 | 5／5 | 0／5 |
| `460df19` | 3／5（2 次沒載、只口頭答應） | 3／3 | 0／5 |
| `d7b3f23` | 9／13 | **0／9** | 1／13（第一批 5 次中 1 次；另 8 次 0 次） |
| 候選描述（8 次） | 8／8 | 0／8 | 2／8 |

`d7b3f23` 載了 manage 的對話：沒有再說錯話。成功的那次：`folders list --json` → `exclude "<完整路徑>"` → `cards todo --json` → 重寫綜合卡 → `folders purge "客戶案件/私人"` → `turn note`，回答「已設成不想讓 AI 看到…卡拿掉 1 張、信義王綜合卡已重寫、殘留已清（備份 14 份…）」，並提醒送雲端的清不掉。找不到夾的：被守門擋住（`公司行政` 有檔名像密碼的檔），模型照規則停手、請客戶貼完整路徑，沒有繞路。沒載 manage 的 4 次（5 次裡的 1 次、8 次裡的 3 次）：只回「好，我不會讀私人那夾」，沒登記任何東西，客戶以為受保護。

同一輪另外跑了前三句（「這份估價單搬到 2026」、「舊備忘幫我刪掉」、「報價夾搬到 E 槽了」），行為與第一輪新版一致：搬一次做完並複述、刪先 `--check` 講後果等明確確認、搬家請她貼路徑；沒有退步。

## 建議（請 a2 定）

1. 彎引號：`shell_arg` 兩份都擋 U+2018～U+201F（含 `„‚‛`），比照 `$`＋`'` 給不出指令。
2. 遮罩左邊界改成「不是路徑字元」（`[\w/\\.\-]` 之外）而不是白名單；補全形冒號、`=`、`[`、`→` 的測試。
3. `refresh_synthesis` 放進 try 裡，或失敗時說「已搬好，但後面沒做完」；`restore_sources()` 放在 `rollback()` 前、各自 try。
4. purge：manifest 行在 finally 裡重寫（F4）。
5. 私人那夾：加 `kb folders find "<名>"`（在登記的根底下找夾名），並考慮把候選描述那句放進描述；採用要重跑 manage.json。
6. `ledger.append`／`log_error` 也拿同一把鎖（短等待）；不然把第 9 條的說法改成實際做到的。
7. `plan.build` 不要被草稿檔名拖垮（catch `ValueError` 或給無指令句）。
8. 低：`_version_dirs` 不跟 junction；rename 擋 Windows 裝置名；暫名檔先記一行；`purge_names` 加鹽。
9. 隨身碟請實測一次 `trash`。

---

# 第三輪（重測 2276aca）

日期：2026-10-22
對象：worktree 同一分支，commit `2276aca`（樹乾淨）；修的 5 條見 spec「討論進度」最後兩條
做法：第二輪列的每一條照原本的重現做法再跑一次；新增的 `kb folders find` 另外測，並驗 a2 指定的「`find` 印的別名/相對路徑直接餵 `exclude`」（cwd 在知識庫、在別處加 `--vault` 各一次）；第二輪之前的驗收腳本 13 支原樣重跑（輸出和第二輪逐行比對）；`d7b3f23..2276aca` 的 security-review（子代理，唯讀）；run_eval；驗收 19 第 4 句。沒改任何程式。模型同前：`sonnet`＝`claude-sonnet-5-5`。

## 結論（第三輪）

- 單元測試 `python -m unittest discover tests`：**exit 0**（Ran 1981，OK，skipped=1）。
- 第二輪列的 5 件事：**彎引號、遮罩、`refresh_synthesis`／rollback 順序、purge manifest、ledger 鎖、versions 底下的 junction、草稿檔名、裝置名都修好了**（逐條證據見下）。第二輪之前的 13 支驗收腳本輸出與第二輪逐行相同，沒有退步。
- run_eval 全部過線：manage 三輪 30／30、30／30、29／30（89／90＝98.9%），不該觸發 0／90；produce 30／30 與 1／30；ingest 30／30 與 0／30。
- 驗收 19 第 4 句（「不要讀私人那夾，我不想讓你看到」）10 次：10／10 載 manage、10／10 用 `folders find`、**0／10 說「本來就沒在讀」**、**7／10 做完整個流程（exclude → 重寫綜合卡 → purge）**；其餘 3／10 停在 exclude 失敗，照實說「這次沒排除成功，什麼都沒改到」。
- **但有 3 件事我認為要在進確認之前處理**（1、2 是新發現且已重現；3 是這輪才發現、一直都在的洞）：
  1. **`folders find` 印的「別名/相對路徑」不能直接餵 `folders exclude`**（已重現（我））。`find 私人` 印 `客戶案件/私人`，把它給 `exclude`（cwd 在知識庫，或在別處加 `--vault`）都 exit 3 `找不到 <cwd>\客戶案件\私人；排除的對象要真的存在`；只有絕對路徑能排除。`exclude` 只認檔案系統路徑，`move --to` 才認別名/相對路徑。`SKILL.md:42` 與 `sources.md:29、80` 卻寫 exclude 收。後果：驗收 19 的 10 次裡全部先試別名形式、全部失敗一次，其中 7 次自己改用 `folders list` 拿根路徑後接成絕對路徑完成，3 次停手。另外 `find` 會列 `知識庫/案子/信義王/私人`，但 `exclude` 連絕對路徑都不收知識庫裡的夾（`不在任何登記的來源裡`），這種夾 find 找得到、exclude 做不了。
  2. **守門對彎引號指令放行**（已重現（我），子代理評 HIGH）：`kb guard decide --tool PowerShell --command 'python "<kb.py>" folders exclude "客戶案件/a“; calc; “b" --kind unused'` 回 `allow 知識庫腳本`（Bash 同）。PowerShell 把 U+2018～U+201E 當引號，這串被拆成三個陳述式、中間的 `calc` 會被執行。這條路是 `find` 開出來的：`SKILL.md:42` 叫模型把 `find` 印的名字手工包成 `exclude "<…>"`，夾名含彎引號時模型照包。第二輪我報的是「程式產生的指令字串」，已修好；現在漏的是「模型自己拼的指令」＋ `cmdparse.is_single_plugin_call` 只認 ASCII 引號。修法：指令含 U+2018～U+201F 就不放行；`find`／`where` 遇到含彎引號、`$`、反引號的名字旗標或略過。相關：含 `$(` 的名字守門只回 `pass`（使用者會被問），但 `SKILL.md` 仍叫用雙引號，PowerShell 的雙引號會展開 `$()`。
  3. **purge 之後 manifest 的 create 列與總目錄舊版還留著夾名與卡標題**（已重現（我），第一輪起就存在，是我第一輪沒 grep 夾名與卡標題）：一次正常、沒有任何失敗的 purge 之後，`.system/versions/<建立那張卡的回合>/manifest.jsonl` 仍有 `{"path": "檔案索引/客戶案件/私人/王家心情-9dd72424.md", "action": "create", …}`（夾名「私人」＋由內容取的卡標題「王家心情」），`.system/versions/<回合>/files/總目錄.md` 的舊版仍有 ` - 私人` 這一行標題。只有 `sources.json` 該有夾名。同類的另一個洞（子代理重現、我也重現）：只有 `moves.jsonl`、沒有 `files/` 的回合夾（例：先搬或刪、清冊還沒有該檔），`_version_dirs` 把它當成連結跳過，`moves.jsonl` 裡的 `file://客戶案件/私人/秘密檔.pdf` 留著，purge 仍印「清好了」。
- 其餘（低）見最後一節。

**能不能進確認**：我的建議是**先修上面 1、2、3 再進**。1 不修，「不要讀私人那夾」到確認情境時有三成機率停在「沒排除成功」（客戶得自己貼路徑），而且文件寫的跟程式不一致；2 是可被「放進來的壓縮檔夾名」觸發的指令注入，且守門自動放行；3 是 D-174 要清乾淨的東西沒清乾淨（夾名與卡標題）。除此之外沒有擋路的了。若 a2 決定 2、3 記 backlog 先進，請在確認報告裡寫明，並且確認情境的測試知識庫不要有含彎引號、`$()` 的夾名，也不要在檢查項目裡寫「全庫 grep 夾名只剩 sources.json」。

## 第二輪列的每一條重測

| 項目 | 結果 | 證據 |
|---|---|---|
| 彎引號（兩份 `shell_arg`） | 修好 | 已重現（我）：`x”; Write-Output PWNED; “y`、`x$’; calc; ‘y`、`a‘b`、`a„b`、`a‟b` 全部回 `None`（不給指令）；`正常 名字` → `"正常 名字"`、`Q3$x` → `'Q3$x'`、`a'b` → `"a'b"`；`plan.cmd("folders","purge",<彎引號名>)` → `None`；`other_disk` 給 `不同顆硬碟不代搬，請在檔案總管自己拖；夾名有特殊符號，開夾的指令給不出來，要我幫你開請先改夾名`；真的 exclude 一個含彎引號的夾成功、不印指令。（但見結論 2：模型自己拼的指令守門放行。） |
| 遮罩邊界 | 修好 | 已重現（我）：purge 後 `hook-errors.log` 裡 `已搬好：客戶案件/私人/日記本.txt`、`note:…`、`path=…`、`[…]`、`→…`、`改了客戶案件/私人/日記本.txt 的卡`、`檔案索引/客戶案件/私人/王家心情-ab12.md`、`file://客戶案件/私人/日記本.txt`、`絕對：C:\…\私人\日記本.txt`、`--path=C:\…` 全部換成「（已排除）」；全庫 grep `私人/日記本` 與 `私人\日記本` 0 個檔；單獨提到的檔名（`單獨 日記本.txt 在句子裡`）與 `metadata` 沒動（單段檔名照設計不換）。副作用（子代理重現、低）：沒有左邊界之後，別名／規格很短時會誤改無關文字（`see data/x` → `see dat（已排除）`），只傷日誌文字、不洩漏。 |
| `refresh_synthesis`／rollback 順序 | 修好 | 已重現（我）：`refresh_synthesis` 丟 `OSError(locked)` → `沒有動：locked`，檔回原位、清冊位置回舊、除簿記外資料差異 `[]`（第二輪是檔留在新位置）；含 hidden 子夾的夾搬到一半 `cards.sync` 丟 `PermissionError` 且 `_Undo.rollback` 也丟錯 → 夾回原位、`sources.json` 與原本逐位元相同、原位私人檔仍 `deny Z10`（第二輪子代理重現的 F1，現在還原 sources 不再被 rollback 拖累）；倒不回去 → `已搬好，但後面沒做完；說「退回」可以退回`。子代理另指（讀碼、未重現、低）：倒回順序是先 `_rename(dst, src)` 再 `restore_sources()`，兩者之間若 `restore_sources` 失敗，舊路徑會短暫只剩新 spec；較安全的順序是先還原 sources。 |
| purge manifest（F4） | 修好 | 已重現（我）：把 versions 裡的 `待處理.md` 寫入換成第一次丟 `OSError` → 第一次 exit 3 `還沒清完：備份 1 份；關掉開著的檔再跑一次 kb folders purge 客戶案件/私人`，排除列 `purge: pending` 且 `purge_ids` 還在；重跑 `清好了…`；結果與「沒有失敗的對照組」逐項相同（機密字串 0 個檔、檔名只剩 prompt 的 5 處）。注意：對照組本身就有結論 3 的殘留，所以 F4 修好但結論 3 是另一個洞。 |
| ledger 鎖 | 修好 | 已重現（我）：在 purge 讀改寫的中間讓 `ledger.append` 追加一行 → 那行還在，追加那邊只等 0.1 秒；事後 `.system` 沒有殘留 `.lock`；目標路徑壞掉時 `append` 不丟例外。子代理（低）：鎖被占著時 `append` 等滿 2 秒才照寫，殘留的舊鎖會讓每次 hook 追加多 2 秒、最長 30 秒；`guard` 一次呼叫追加幾次還在 10 秒時限內，`session.py` 有多個 `log_error` 的話可能累積；沒有死結，hook 仍不會丟例外。 |
| junction | 部分 | 已重現（我）：`.system/versions/zz-junction` 指到庫外有編號相符卡舊版與 manifest 的資料夾 → purge 後庫外的檔還在、manifest 沒被改（第二輪是被刪被改）。子代理重現：junction 在 `versions/<turn>` 或 `files/` 層被正確跳過；但**巢在 `files/` 底下**的 junction（例 `files/系統` 指到庫外有 `待處理.md` 的夾）仍被 `_scrub_md` 跟進去、庫外的檔被刪（沒有 `_under_files` 檢查）。要先能寫 `.system`，低。 |
| 草稿檔名同時有 `'` 和 `$` | 修好 | 已重現（我）：`plan.cmd("output","drop-draft","AI 產出/Joe's $500 quote（草稿）.md")` → `None`；放這樣的檔進 `AI 產出/` 後 `kb cards todo --json` exit 0、`kb asks` exit 0，出 `Joe's $500 quote 的草稿還在等你看  1. 定稿 2. 再改 3. 不要了`，`do` 為空（第二輪是整個 exit 3）。子代理（低、讀碼）：`profile_cli.py` 拿 `plan.cmd` 的結果不處理 `None`（型別名含彎引號時 `"do":[null]`）。 |
| 裝置名 | 大致修好 | 已重現（我）：`NUL`、`con`、`COM1`、`aux.pdf`、`LPT9`、`CON.txt`、`nul .pdf`、`COM1.`、`PRN`、rename 夾成 `NUL` → `這種名字 Windows 不收，換個名字`；`Console`、`com10`、`報價` 正常放行。子代理：`COM¹ ² ³`、`LPT¹ ² ³`、`CONIN$`、`CONOUT$` 仍放行（`os.path.isreserved` 認為保留名；我確認 `con²`、`COM¹` 放行）；全形 `ＣＯＮ` 不是保留名，放行是對的。低。 |
| 第二輪之前的驗收腳本 | 無退步 | `a02b、a03_06、a05、a06、a07g、a08、a09、a10、a11、a12、a14、a16、a21` 原樣重跑，輸出與第二輪逐行相同（去掉暫存路徑、編號、時間；唯一差在 a11 的雜湊）。 |

## 新增：`kb folders find`

| 檢查 | 結果 | 證據 |
|---|---|---|
| 找得到 | 過 | `find 私人` → `找到 3 個叫「私人」的夾：` ＋ `- 公司行政/私人`、`- 客戶案件/私人`、`- 知識庫/案子/信義王/私人`；`信義王` 3 個、`大安陳` 2 個、`薪資`、`舊檔`、`案子`；`--json` 有 `name`、`found`、`truncated`；找不到 `登記的資料夾裡沒有叫「不存在的夾」的夾`；`信義王/私人`（上層/夾名，上層是夾名不是別名）找得到；`客戶案件/私人`（別名/夾名）找不到。 |
| 只讀 | 過 | 前後快照無差異。 |
| 不洩漏 | 過 | 排除成 hidden 的 `客戶案件/大安陳` 不列（只列 `知識庫/案子/大安陳`）；`密碼備份` 夾不列；`.隱藏`、`隱藏`、`.system`、`檔案索引`、`系統`、`AI 產出`、`收件匣` 不列；`find` 只印夾路徑、不印檔名。子代理（低）：沒做 NFKC／NFD 折疊，全形夾名會得到「沒有叫…」而 skill 會跟客戶說本來就沒在讀；全形 `ＰＡＳＳＷＯＲＤ` 這種憑證夾名會列出（與守門一致）；走訪上限只數夾，單一夾底下上百萬檔仍整個 `scandir`。 |
| 逃逸型輸入 | 過 | `..`、`../..`、`客戶案件/..`、`C:\`、`C:/Windows`、`\\server\x`、`客戶案件\私人`、`*`、`?`、`私人*`、`.` 都回「沒有叫…的夾」（只做字串比對）。`exclude` 吃別名/相對路徑形式的逃逸（`客戶案件/../.system`、`知識庫/.system`、`不存在別名/x`、`客戶案件/../../x`）全部 exit 3，什麼都沒動。 |
| **`find` 的輸出餵 `exclude`** | **不過** | 見結論 1。cwd 在知識庫與在別處加 `--vault` 各一次：`exclude "客戶案件/私人" --kind unused` 都 exit 3 `找不到 <cwd>\客戶案件\私人；排除的對象要真的存在`；絕對路徑（`C:\…\raw\客戶案件\私人`）兩種 cwd 都成功；`purge "客戶案件/私人"` 收別名/相對路徑；`include "客戶案件/私人"` 不收（回「不在任何登記的來源裡」）。 |

## run_eval（2276aca）

跑法同前。模型 `sonnet`＝`claude-sonnet-5-5`。描述 145 字（≤150）：加了「不要讀私人那夾，我不想讓你看到」。

| 檔 | 該觸發（10 句×3） | 不該觸發（10 句×3） |
|---|---|---|
| `manage.json` 第 1 輪 | 30／30 | 0／30 |
| `manage.json` 第 2 輪 | 30／30 | 0／30 |
| `manage.json` 第 3 輪 | 29／30（「剛做好的定稿要放哪？你決定就好」2／3） | 0／30 |
| manage 三輪 | **89／90（98.9%）** | **0／90** |
| `produce.json` | 30／30 | 1／30（「那份報價寫錯了，總價是 128 萬」1／3） |
| `ingest.json` | 30／30 | 0／30 |

manage 該觸發從第二輪的 83／90（92.2%）升到 89／90，兩個擺盪的句子（薪資資料夾、定稿要放哪）穩下來了；例句 19 維持 0 次。

## 驗收 19 第 4 句（2276aca，10 次）

做法同前（基底知識庫有 `客戶案件/私人/日記本.txt`、一張卡、信義王綜合卡引用它；一次一份新複本）。

| 版本 | 載 manage | 說「本來就沒在讀」 | 完整做完 exclude＋重寫綜合卡＋purge |
|---|---|---|---|
| `1f009a4`（5 次） | 5／5 | 5／5 | 0／5 |
| `460df19`（5 次） | 3／5 | 3／3 | 0／5 |
| `d7b3f23`（13 次） | 9／13 | 0／9 | 1／13 |
| **`2276aca`（10 次）** | **10／10** | **0／10** | **7／10** |

- 10 次全部先 `folders find "私人"`，找到 `客戶案件/私人`；全部先把別名形式給 `exclude` 試一次（`SKILL.md:42` 叫的），全部 exit 3 `找不到…`；其中 7 次改用 `folders list` 拿來源根、接成絕對路徑再排除，接著 `cards todo`、重寫綜合卡、`folders purge "客戶案件/私人"`、記 `turn note`，回答正確（拿掉 1 張卡、綜合卡已重寫、殘留已清、送過雲端的清不掉）。3 次停手，沒有繞路亂試，回答大意是「這次沒排除成功，什麼都沒改到；程式找不到『客戶案件/私人』，我的搜尋明明找到了這個夾，兩邊對不上」。沒有一次說錯話，也沒有一次口頭答應就算。
- 同一輪前三句：「這份估價單搬到 2026」這次模型回「我對到兩份（內湖李估價、報價_v2），不確定是哪一份」並問（`cards list` 對到兩張卡；前幾輪都只挑 `估價單.pdf`）；「舊備忘幫我刪掉」先 `trash --check` 講後果、等明確「刪」；「報價夾搬到 E 槽了」請她貼路徑、說會先預覽。三句都沒有動檔，也沒有錯。
- 修掉結論 1（讓 `exclude` 收別名/相對路徑）後，這句的成功率預期可以從 7／10 往 10／10 走，但需要再跑一次。

## 子代理的其餘發現（`d7b3f23..2276aca`，除上面已列的）

- 低：`purge` 把「有 `files/` 的回合夾」以外的回合夾（只有 `moves.jsonl`／`turn.json`）整個跳過（結論 3 的後半，已重現（我））；巢在 `files/` 底下的 junction 仍被跟進（上表）；`profile_cli` 沒處理 `plan.cmd` 回 `None`；`find` 沒做 Unicode 折疊且只限制夾數；ledger 鎖等 2 秒。
- 資訊：`hook` 只經 `ledger.py` 寫檔，沒動 `hooks/`；追加等不到鎖照樣寫（hook 有時限），這是設計認可的取捨。

## 建議（請 a2 定）

1. **`exclude`（也建議 `include`）收「別名/相對路徑」**，與 `move --to`、`purge` 一致，並把 `find` 找到的知識庫裡的夾（`知識庫/案子/…`）從輸出略過或標明不能排除。
2. **`cmdparse.is_single_plugin_call`：指令含 U+2018～U+201F 不放行**；`SKILL.md`／`sources.md` 叫模型改用單引號包含 `$()` 的名字，或叫它遇到含 `$`、反引號、彎引號的名字停手請客戶改名。
3. **purge 補洞**：manifest 裡 `create` 列的 path（夾名、卡標題）換成「（已排除）」；`總目錄.md` 舊版拿掉該夾的標題行（現在只拿掉卡那幾行）；`_version_dirs` 只在路徑存在且是 reparse point 時才跳過（沒有 `files/` 的回合夾也要掃 `moves.jsonl`）；`_scrub_md` 加 `_under_files`。
4. 低：`COM¹ ² ³`、`LPT¹ ² ³`、`CONIN$`、`CONOUT$`；先還原 sources 再倒回檔；`find` 做 NFKC 比對；`profile_cli` 處理 `plan.cmd` 的 `None`；遮罩對短別名過度遮罩；暫名檔先記一行；`purge_names` 加鹽。
5. 隨身碟的 `trash` 還是要找一支實測。

---

# 第四輪（重測 d7eb8e2）

日期：2026-10-22
對象：worktree 同一分支，commit `d7eb8e2`（樹乾淨；修的內容見 spec「討論進度」最後兩條）
做法：第三輪列的 3 件與順手修的 3 項照原本的重現做法再跑；a2 補充的格式（`find --json` 每筆 `{path, in_vault, exclude}`、守門 `reason_code: curly_quote`）照著驗；第三輪以前的驗收腳本再跑一遍逐行比對；run_eval 跑 manage 一輪；驗收 19 第 4 句 10 次。沒改任何程式。這輪沒再派 security-review 子代理（範圍只有三件），守門與 purge 的新行為我自己測。模型同前：`sonnet`＝`claude-sonnet-5-5`。

## 結論（第四輪）

- 單元測試 `python -m unittest discover tests`：**exit 0**（Ran 1991，OK，skipped=1）。
- 第三輪要修的 3 件 **都修好了**；順手修的 3 項也修好了；第三輪以前的驗收腳本輸出與上一輪逐行相同，沒有退步。
- run_eval：manage 29／30（96.7%；「剛做好的定稿要放哪？你決定就好」2／3），不該觸發 0／30。
- 驗收 19 第 4 句 10 次：**10／10 載 manage、10／10 用 `folders find`、10／10 完整做完（exclude → 重寫綜合卡 → purge）、0／10 說「本來就沒在讀」**（上一輪是 7／10）。其他三句行為正常，沒有動檔。
- **新發現 1 件（中）**：守門的彎引號規則擋得太寬，見下。其他都是低。
- **能不能進確認：可以進。** 上一輪我說要修的 3 件已經修好，驗收 19 第 4 句從 7／10 到 10／10。新發現的守門過寬建議同時修（見建議 1），但它不擋 D-174 的主流程（驗收 19 的 10 次全程沒有碰到），確認情境若要照做，注意：**所有送進 `kb` 的文字參數（`turn note`、`statement add`、`agenda add`、`ruling add`…）別帶 `“ ” ‘ ’`**，不然會被擋；用 `「 」` 沒事。隨身碟的 `trash` 仍只用替身驗過（a2 已記錄使用者沒有隨身碟）。

## 上一輪 3 件、順手修 3 項

| 項目 | 結果 | 證據 |
|---|---|---|
| 1. `exclude`（兩種）與 `include` 收「別名/相對路徑」 | 修好 | 已重現（我）：`exclude 客戶案件/私人 --kind unused`，cwd 在知識庫、cwd 在別處加 `--vault` 兩種都 exit 0 `已排除「客戶案件/私人」（用不到）。卡拿掉 0 張。`，`include 客戶案件/私人`（兩種 cwd）都 `已取消排除…`；`hidden` 同；`客戶案件/私人/../大安陳` 解成 `客戶案件/大安陳`；逃逸型輸入 `客戶案件/../.system`、`客戶案件/../../x`、`不存在別名/x`、`收件匣/x`、`客戶案件`（整個來源，回 `整個來源用 folders remove 客戶案件`）全部 exit 3、沒動。 |
| 1b. `find --json` 每筆帶現成指令 | 修好 | 已重現（我）：`{"path": "客戶案件/私人", "in_vault": false, "exclude": {"hidden": "kb folders exclude 客戶案件/私人 --kind hidden", "unused": "…--kind unused"}}`，照著跑（`kb` 換成 `python kb.py`）成功；夾名 `Q3$x` → 指令用單引號；夾名含彎引號或同時有 `'` 與 `$` → `"exclude": null` ＋ `note: 夾名有引號一類的特殊符號，指令給不出來；請她先在檔案總管改名`；人讀輸出同樣標「（知識庫裡的夾，不能設成不讀）」。`find` 仍只讀（前後快照無差異）。 |
| 1c. 三區裡的夾這版拒絕（B-413） | 修好 | 已重現（我）：`find` 對 `知識庫/案子/信義王/私人` 標 `in_vault: true`、`exclude: null`、`note: 知識庫裡的夾不能設成不讀；要不讓 AI 看到，先搬到登記的來源夾再設`；`exclude`（unused、hidden）該夾、`知識庫/.system`、`AI 產出` 都 exit 3 `知識庫裡的夾不能設成不讀；要不讓 AI 看到，先搬到登記的來源夾再設`。（`include 知識庫形式` 回「不在任何登記的來源裡」，可接受。） |
| 2. 守門擋彎引號 | 修好；但範圍過寬（見下新發現） | 已重現（我）：PowerShell 與 Bash 對含 U+201C／201D、U+2019／2018、U+201E 的 kb 指令（含上輪的注入案例 `exclude "客戶案件/a“; calc; “b" --kind unused`、`x$’; calc; ‘y`）都 `deny curly_quote  指令裡有彎引號，PowerShell 會拆開；夾名有彎引號請她先改名`；與 kb 無關的 `echo “hi”` 仍 `pass`（不干涉）；無彎引號的分號案例 `"客戶案件/a; calc"` 仍 `allow`（雙引號內的分號安全）；含 `$(` 仍 `pass`（使用者會被問）；`Read` 檔案工具帶彎引號路徑不受影響。 |
| 3. purge 清 create 列、樹節點、只有 `moves.jsonl` 的回合 | 修好 | 已重現（我）：一次正常 purge（外加一個只有 `moves.jsonl`、`turn.json`、空 `manifest.jsonl` 的回合夾、和一個巢在 `files/系統` 的 junction 指向庫外）之後，全庫 grep：機密字串 0、`秘密檔` 0、`王家心情`（卡標題）0、卡檔名 0、**`私人` 只剩 `.system/sources.json`**（上一輪另有 manifest 的 create 列與總目錄舊版的 ` - 私人`）；`moves.jsonl` 的 `from` 變 `（已排除）`；庫外巢狀 junction 裡的檔還在、內容沒被改；純 `OSError` 中斷再重跑的情境，`私人` 同樣 0 個殘留（`manifest`／總目錄舊版都處理了）；`versions revert` 退到 purged 的回合仍印 `那一版因為不讀的夾清掉了，退不回`。 |
| 順手修：裝置名 | 修好 | 已重現（我）：`COM¹ ² `、`LPT³`、`COM².pdf`、`LPT¹.txt`、`CONIN$`、`CONOUT$`、`conin$.txt` 與 `NUL`、`con`、`COM1`、`LPT9` 全部 `這種名字 Windows 不收，換個名字`；`ＣＯＮ`、`Console`、`com10`、`COM0`、`CLOCK$`、`con².pdf`（`os.path.isreserved` 也說不是保留名）、`報價` 正常放行。 |
| 順手修：巢狀 junction | 修好 | 見上，`files/系統` 指向庫外的 `待處理.md`，purge 之後庫外檔還在、內容未改。 |
| 順手修：`profile_cli` 接 `plan.cmd` 的 `None` | 修好（讀 diff＋`plan.cmd` 驗證） | `plan.cmd("profile","apply","x“y")` → `None`；`profile_cli._result` 現在遇到 `None` 回 `{"do": [], "note": plan.NO_CMD_NOTE, …}`。我沒用真的型別名含彎引號端對端跑（型別名另有名稱檢查擋彎引號以外的字元，沒試出能放進去的名字）。 |
| 第三輪以前的驗收腳本 | 無退步 | `a02b、a03_06、a05、a06、a07g、a08、a09、a10、a12、a14、a16、a21` 輸出與上一輪逐行相同；`b03`（倒回）、`b04`（purge 範圍與同回合先掃描）、`c04`（中途失敗重跑）句子與結果一致。 |

## 新發現：守門的彎引號規則擋太寬（中）

已重現（我）。`guard.decide` 對 PowerShell／Bash 的 kb 指令，只要指令字串裡任何地方有 U+2018～U+201F 就 `deny curly_quote`，不分是不是夾名、不分在不在引號裡：

| 指令（`python kb.py …`） | 結果 |
|---|---|
| `turn note "把“舊備忘”搬到 2026"` | `deny curly_quote` |
| `statement add --entity 信義王 --text "客戶說「付款」改“30 天”"` | `deny curly_quote` |
| `agenda add --kind 提醒 --date 2026-12-01 --text "王先生’s 簽約"` | `deny curly_quote` |
| `turn note "把舊備忘搬到 2026"`（無彎引號） | `allow` |

中文裡 `“ ”` 很常見（模型寫備註、口述、日程文字時常用），這些指令是 note／manage 每個回合都會跑的（`SKILL.md` 骨架每件事結束叫 `turn note`）。被擋時給的理由「夾名有彎引號請她先改名」對這些情況是錯的，模型不一定知道只要拿掉引號重打。驗收 19 的 40 個以上對話裡這條沒觸發過（模型的 `turn note` 都用直引號或不用引號），所以不擋主流程，但真實客戶的口述、日程可能撞到。

建議：規則只套在「會被當成路徑／夾名用」的子命令（`folders`、`source-edit`、`convert`、`office copy`…）的引數，或只擋出現在引號內且前後緊接 `;`、換行、`|`、`&` 的彎引號；`turn note`、`statement`、`agenda`、`ruling`、`finding`、`pending add` 的 `--text` 之類純文字參數放行（那些參數進 `kb` 的 argv 之後不會再被 shell 解讀，真正的風險只在 PowerShell 拆分指令那一步，而那一步只有在彎引號「前面有一個直引號開著」的時候才拆）。至少把理由句改成「指令裡有彎引號（“ ” ‘ ’），請拿掉再試」。

## run_eval（d7eb8e2）

| 檔 | 該觸發（10 句×3） | 不該觸發（10 句×3） |
|---|---|---|
| `manage.json` | 29／30（96.7%；「剛做好的定稿要放哪？你決定就好」2／3） | 0／30 |

這輪 a2 只要求 manage 一輪，produce、ingest 沒重跑（描述沒動）。模型 `sonnet`＝`claude-sonnet-5-5`。

## 驗收 19 第 4 句（d7eb8e2，10 次）

| 版本 | 載 manage | 說「本來就沒在讀」 | 完整做完 exclude＋重寫綜合卡＋purge |
|---|---|---|---|
| `1f009a4`（5 次） | 5／5 | 5／5 | 0／5 |
| `460df19`（5 次） | 3／5 | 3／3 | 0／5 |
| `d7b3f23`（13 次） | 9／13 | 0／9 | 1／13 |
| `2276aca`（10 次） | 10／10 | 0／10 | 7／10 |
| **`d7eb8e2`（10 次）** | **10／10** | **0／10** | **10／10** |

10 次都是 `folders find "私人"` → 照 `--json` 的現成指令 `folders exclude 客戶案件/私人 --kind hidden`（一次成功，沒有再先試錯一次）→ `cards todo --json` → 重寫信義王綜合卡 → `folders purge "客戶案件/私人"` → `turn note`；回答都講：卡拿掉 1 張、綜合卡已重寫、備份 14 份、暫存 3 份、推算 1 條、工作日誌已處理，並提醒送過雲端的清不掉。

同一輪另外三句，沒有動檔、沒有錯：
- 「這份估價單搬到 2026」：載 manage，`source-edit move "f-…" --to "客戶案件/2026"` 一次做完，記 `turn note`，回答說明依檔名只對到內湖李那份，搬錯說「退回」。
- 「舊備忘幫我刪掉」：`trash --check`，講後果（回收筒、卡拿掉、退回救不回來），等明確「刪」或「丟掉」才做。
- 「報價夾搬到 E 槽了」：`folders list`，請她貼完整路徑，說會先預覽再問。

## 建議（請 a2 定）

1. 守門的彎引號規則縮窄（見上），至少改理由句。
2. 低、仍未修（B-414 範圍）：遮罩對很短的別名／規格會過度遮罩；`find` 沒做 NFKC、走訪上限只數夾；ledger 鎖等滿 2 秒；rollback 先 rename 回去再還原 sources 的順序；`purge_names` 沒加鹽；被殺掉時留下的 `.kb-move-xxxx`；B-410。
3. 隨身碟的 `trash` 只用替身驗過，請在發行說明或確認報告註明。
