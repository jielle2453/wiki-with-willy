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
