# manage 測試報告

日期：2026-10-16
給誰看：設計 session（llm-wiki-j-18）與實作 session。
什麼時候更新：驗收 11 補跑後補一節；之後不改。

對象：worktree `C:\Users\Jie\Desktop\llm-wiki-j-manage`，分支 `spec/manage`，實作 bf34ed9、修正 c245597（中間 25351dc 是 merge main，只動 spec）。spec `design/specs/2026-10-16-manage.md`，含疑義裁定與驗收 14～17。測試 session 沒改任何程式，worktree 除本檔外 `git status` 乾淨。

## 結論

- 驗收 1～17：**16 條過、0 條不過、1 條沒跑**（11 路由：`llm-wiki@llm-wiki` 是 disabled，照指示沒有自己 enable，已回報）。
- `python -m unittest discover tests`：exit 0，617 個測試 OK。
- code-review（high）：1 個中、3 個低，沒有高。security-review：1 個中（已知、範圍外的 B-025 類），沒有高。
- plugin-dev：`claude plugin validate` exit 0（1 個 warning：根目錄 CLAUDE.md 不當 context，預期內）；plugin-validator 0 critical；skill-reviewer 0 critical、3 major、6 minor，沒有「描述沒有觸發句」與「第二人稱」的指摘。

## 驗收準則逐條

`<V>` 每次都從 `tests/fixtures/vault` 與 `raw` 一起複製到 scratchpad 下的新 tmp 夾（`<T>/vault`、`<T>/raw`），從 `<T>` 跑 `python "<ROOT>/scripts/kb.py" …`。

| # | 結果 | 證據 |
|---|---|---|
| 1 | 過 | `folders remove 客戶案件 --vault <V>` exit 0，印「已拿掉來源「客戶案件」；你的檔案沒動，從現在起不讀它；說「退回上一版」救得回。」；`sources.json` 只剩 `src-7c21 行政`；`<V>/.system/versions/20261016-153059-0000/files/.system/sources.json` 是改前全文（兩筆），manifest 一行 `{"path": ".system/sources.json", "action": "modify"}`。再跑一次 exit 3「沒有叫「客戶案件」的來源」。 |
| 2 | 過 | include 前 `guard decide --tool Read --path <raw>\行政\薪資\s.xlsx` → `deny Z10 excluded`；`folders include "<V>\..\raw\行政\薪資"` exit 0，印「已取消排除「行政/薪資」，從現在起讀得到。」；`excludes` 只剩舊檔；之後同一判斷 → `allow Z9 登記的來源（行政）`。憑證例外：在薪資放 `password.txt`，include 後讀它仍 `deny Z9 credential 檔名像密碼或金鑰（password.txt）…`。 |
| 3 | 過 | `include "<V>\..\raw\行政\請款"` exit 3，第一行「C:/…/raw/行政/請款 不在排除清單裡」。 |
| 4 | 過 | 先 `rm -rf raw/行政/舊檔`，再 `include` 它：exit 0「已取消排除「行政/舊檔」，從現在起讀得到。」，`excludes` 變空。 |
| 5 | 過 | `exclude "<V>\..\raw\客戶案件" --kind unused` exit 3，第一行「整個來源用 folders remove 客戶案件」。 |
| 6 | 過 | include `--json`：`{"source_id": "src-7c21", "alias": "行政", "spec": "舊檔", "kind": "unused"}`；remove `--json`：`{"id": "src-3f9a", "alias": "客戶案件", "spec": "../raw/客戶案件"}`；拒絕時 `--json` 仍印一行原因、exit 3。 |
| 7 | 過 | 終端機跑完 1、2、4 後 `<V>/.system/journal/2026-10.jsonl` 三行，都是 `"kind": "program"`，`command` 分別 `folders remove`、`folders include`、`folders include`，`note` 是第一行，`changed` 是 `[{"path": ".system/sources.json", "action": "modify"}]`。 |
| 8 | 過 | `python -m unittest -v tests.test_skill_manage`：9 個 OK，exit 0（含裁定 3、5 的兩個）。 |
| 9 | 過 | `python -m unittest -v tests.test_budget`：8 個 OK、沒有 skip，exit 0；`SkillBudget` 三個（description 與本文、reference 行數、描述合計）都真的量了。描述 145 字，本文 48 行。 |
| 10 | 過 | `claude plugin validate "<ROOT>"` exit 0，「Validation passed with warnings」，唯一 warning 是根目錄 `CLAUDE.md` 不會當 plugin context（給改 repo 的人看，預期內）。plugin-validator：Critical 0。skill-reviewer：觸發句「沒有缺」、第二人稱「沒有」（只有 sources.md:61 程式印的句子裡有「你」）。 |
| 11 | **沒跑** | `claude plugin list`：`llm-wiki@llm-wiki` Status ✘ disabled（scope user）。I-070 要兩套都在才算數，照指示沒自己 enable，已用 SendMessage 回報 llm-wiki-j-18，等回覆。觸發率表待補。 |
| 12 | 過 | `python -m unittest discover tests`：Ran 617 tests，OK，exit 0。 |
| 13 | 過 | repo `CLAUDE.md` 第 45 行 `tests/evals/`、第 49 行 `skills/manage/`；README 第 21 行「## 怎麼用」四句對話例子加一句做不到的事。 |
| 14 | 過 | `kb folders --help` 描述「來源：add …，exclude …，remove 拿掉一個來源，include 取消排除，list 列出登記表」，子子命令列有 remove、include；`kb --help` 的 folders 列同一句。 |
| 15 | 過 | evals 20 筆、`should_trigger` true 10 筆、query 不重複 20；「幫我把信義王的報價 v2 搬到王宅那夾」= true、「把信義王報價 v2 的付款條件改成 30 天」= false；描述含「幫我搬」。 |
| 16 | 過 | fixture 的 `sources.json` 加一筆 `{"alias": "Docs", "spec": "../raw/Docs"}` 後 `remove docs` exit 0，印「已拿掉來源「Docs」…」（印存的大小寫）；`remove 客戶案件` exit 0。註：本想用 `folders add` 加 Docs，但 scratchpad 在 `AppData` 底下，被「系統資料夾不能當來源」正確擋下，所以改手加 fixture。 |
| 17 | 過 | SKILL.md 第 28 行「檔名像密碼或金鑰的預設不讀，這版還不能開放，之後會有」，references/sources.md 第 34 行同句；repo CLAUDE.md 第 50 行 `commands/` 列照裁定 5。 |

## 路由測試（驗收 11）

沒跑。前置條件不符：`llm-wiki@llm-wiki` disabled。等設計 session 決定（enable 後重跑，或指示用目前狀態跑）。

## code-review（等級 high，只看 bf34ed9 與 c245597 的 diff）

沒有高。

**中 R-1：include 只有一種拒絕「不在排除清單裡」，對應的話術會講錯。** `folders.py` 的 `_include` 在三種情況印同一句：①真的沒排除（讀得到）、②路徑在某條排除的夾**裡面**（仍不讀）、③路徑不在任何來源裡（根本不讀，例如來源剛被 remove）。`references/sources.md:108` 把這句對到「那個夾本來就讀得到，不用取消。」，②③都講錯。實測：`include "<raw>\行政\薪資\s.xlsx"` → exit 3「不在排除清單裡」，同時 guard 讀那份仍 `deny Z10`；remove 客戶案件後 `include "<raw>\客戶案件"` → 同一句。會發生的情境：拿掉「客戶案件」後客戶說「客戶案件還是讀好了」——SKILL.md:31 把「…還是讀好了」對到 include → exit 3 → AI 照表說「本來就讀得到」，實際什麼都不讀。SKILL.md:37 叫 include 用 `list --json` 的排除 path，能擋掉大部分②，擋不掉③。建議：程式分出②③兩種原因句（例如「在排除的「X」裡面，要整個取消請用 X」「不在任何登記的來源裡」），reference 各給一句；或至少 reference 那列改成「先跑 list 再說」。

**低 R-2：巢狀排除時 include 的句子說太滿。** 先排除 `請款/2025`（hidden）再排除 `請款`（unused）——既有 `_exclude` 只擋「新的在舊的裡面」，所以兩列都會在。`include 請款` 印「從現在起讀得到」，但 `請款/2025/x.docx` 仍 `deny Z10`。偏安全（hidden 的還藏著），只是話不準。

**低 R-3：`remove ""` 會拿掉沒有別名的那筆。** 比對是 `str(s.get("alias","")).lower() == ""`；沒別名的條目程式是認的（`_raw_entry` 有處理）。實測手加一筆沒 alias 的條目，`remove "" --json` exit 0、拿掉它。只有手改過或舊格式的 sources.json 會有，有備份可退。建議空別名直接 exit 3。

**低 R-4：remove 沒有照 add 去掉別名結尾的空白與點。** `remove "行政 "` exit 3「沒有叫「行政 」的來源」。偏安全，客戶照 list 念的別名不會碰到。

觀察（不在 diff 內，既有行為）：終端機連續跑幾次 folders，`_backup_then_write` 用 `current.json` 的同一回合，同回合同檔只備第一次，所以 remove 後再 include 兩次只留一份備份（remove 前的）；「退回上一版」會把三次一起退。對話裡每句一回合，不受影響。

## security-review（`folders.py` 的 remove／include）

沒有高。

**中 S-1：include 放寬讀取範圍，而且不經人按允許。** 守門對 `kb.py` 一律放行（D-073）：`guard decide --tool Bash --command 'python "<kb.py>" folders include "<raw>\行政\薪資"'` 在薪資還是 hidden 時回 `allow 知識庫腳本`。所以把 hidden 夾放回來，只靠 skill 的文字守；文件裡的提示注入叫模型跑 include，沒有程式層的關卡。同樣的性質 `add` 本來就有，不是新的一類；高風險子命令改 ask 在 spec「不做」裡（B-025）。SKILL.md:31 照 spec「複述一句就 include」，hidden 也不另外確認——skill-reviewer 的 Major 1 也指到這裡。建議設計 session 判斷：hidden 的 include 要不要跟 remove 一樣只認明確確認，或併進 B-025。

確認沒問題的：
- include 後憑證檔名樣式的檔仍 deny（驗收 2）。
- remove、include 只改 `.system/sources.json`，在同一把 `FileLock` 裡讀、備份、原子寫；路徑參數只拿來比字串，不寫、不刪任何客戶檔。
- remove 只會縮小讀取範圍。
- 拒絕時什麼都沒寫（驗收 1 再跑、驗收 3）。
- 沒有新的 `sys.path`、絕對路徑或設定檔改動。

## plugin-dev 審查

依據 `plugin-dev:skill-development`，審 `skills/manage/SKILL.md` 與 `references/sources.md`：

| 項目 | 結果 |
|---|---|
| frontmatter | 只有 `name: manage`、`description`，YAML 合法。skill-development 範例另有 `version`，本專案刻意不加（spec）。 |
| 描述觸發句 | 有，十句客戶原話。skill-development 要的英文第三人稱「This skill should be used when…」不適用（D-027、D-070 規定繁中、無英文觸發詞）；改成「客戶說「…」時用」，作用相同。145 字。 |
| 祈使句 | 本文全祈使句，沒有「你應該」。 |
| progressive disclosure | 本文 48 行，只放分辨、做法表、共通；參數、輸出、拒絕話術放 reference（109 行）。本文末有「參考」節指到 reference，兩個被引用的小節都存在。 |
| 指令寫法 | `python "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" folders …`，有代換不了的退路。沒有絕對路徑。 |

agent `plugin-dev:plugin-validator`（整個 worktree）：PASS，Critical 0。warning：根目錄 CLAUDE.md（預期內）；hooks 一律叫 `python`，只有 `python3`／`py` 的機器會失敗（可攜性，不是這題動的）。

agent `plugin-dev:skill-reviewer`（`skills/manage`，修正後第一次跑）：Critical 0。
- Major 1：SKILL.md:31 include 複述完就跑，hidden 的放回沒等確認（同 S-1；這是 spec 寫的做法）。
- Major 2：SKILL.md:33 搬家流程先 `remove` 再 `add`，新路徑被拒（找不到、重疊、系統夾）時客戶變成一個來源都沒有、舊排除只剩對話記憶。建議先對新路徑跑 `add --check`，過了才 remove。我認同這條，值得改。
- Major 3：描述裡「幫我搬」「改名」太泛，之後的卡片類 skill（例如卡改名）可能搶路由。這兩詞是裁定 2 明文放回去的，留給設計 session 判斷；驗收 11 的誤觸率可以當依據。
- Minor 6 條：SKILL.md:20 的「幫我刪」導到 :34，但那列沒寫刪檔怎麼回；`--check` 有提醒（份數 0、整個 Desktop）時直接問別名，沒先問還要不要加；哪些指令「好」算同意、哪些不算，散在各列；`整個來源用 folders remove` 的拒絕先問一次，remove 列又問一次，客戶被問兩次；remove 列沒說別名不確定先跑 list；remove 列叫 AI 再講「退回上一版救得回」，跟程式印的第一行重複。

## 沒做的

- 驗收 11（見上）。
- 「確認情境」11 步不是測試 session 的事，沒做。

## 第二輪（2026-10-16，實作修正 7d74891）

對象：同 worktree，`3193bd0`（merge main）之後的 `7d74891`。spec 以主 checkout 版為準（含「測試後修正（第一輪）」、驗收 18～20、「疑義裁定（第一輪修正後）」）。沒改程式；worktree 除本檔外乾淨。

### 結論

- 新增 18～20：**3 條全過**。
- 無退步：1～10、12～17 **全過**。
- 11 路由：仍沒跑（`llm-wiki@llm-wiki` 還是 disabled，使用者會親自 enable，等通知）。
- `python -m unittest discover tests`：exit 0，620 OK。
- code-review（只看 7d74891）：沒有高、沒有中，1 個低。skill-reviewer：Critical 0、Major 2（1 條我認同，1 條是 spec 要的做法）。

### 新增驗收

| # | 結果 | 證據 |
|---|---|---|
| 18 | 過 | remove 客戶案件後 `include "<V>\..\raw\客戶案件"` → exit 3「C:/…/raw/客戶案件 不在任何登記的來源裡；先用 folders add 登記」；`include "<V>\..\raw\行政\薪資\2025"`（夾不存在也一樣）→ exit 3「…/行政/薪資/2025 在排除「行政/薪資」裡面；要恢復就取消那條」；`include "<V>\..\raw\行政\請款"` → exit 3「…/行政/請款 不在排除清單裡，本來就讀得到」。另：薪資裡的檔 `s.xlsx` 走第二句，`--json` 時拒絕仍印一行原因。 |
| 19 | 過 | SKILL.md:33 第①步是 `add "<新路徑>" --check --alias "<舊別名>-新位置"`，被拒就什麼都不動，過了才 list、remove、add、exclude。`remove " 客戶案件 "` exit 0；`remove ""`、`remove "  "` exit 3「別名不能是空的；先用 folders list 看有哪些來源」；`remove "行政." --json` exit 0、alias「行政」。 |
| 20 | 過 | `python -m unittest discover tests`：Ran 620，OK，exit 0。 |

### 無退步（只記過不過）

| # | 結果 | # | 結果 |
|---|---|---|---|
| 1 | 過 | 9 | 過（`test_budget` 8 個不 skip，與 `test_skill_manage` 合跑 18 OK） |
| 2 | 過（含憑證檔仍 deny） | 10 | 過（validate exit 0、validator Critical 0、skill-reviewer 沒有觸發句與第二人稱指摘） |
| 3 | 過（句子改成「…不在排除清單裡，本來就讀得到」，仍含驗收要的字） | 12 | 過（620 OK） |
| 4 | 過 | 13 | 過 |
| 5 | 過 | 14 | 過 |
| 6 | 過 | 15 | 過（20／10／20 不重複，兩句的 true／false 對，描述含「幫我搬」，145 字） |
| 7 | 過 | 16 | 過（`remove docs`、`remove 客戶案件`） |
| 8 | 過 | 17 | 過 |

### code-review（等級 high，只看 7d74891）

**include 三種判斷的順序**：先找完全相等的排除列（跨所有來源），沒有才找「在某條排除裡面」，再看在不在任何來源，最後才說「本來就讀得到」。順序對：在排除裡面的不會被說成讀得到；來源根本身（`include "<raw>\行政"`）走第三句，對（根的直接檔案讀得到）。`rows` 只收有 `spec` 的排除，`resolve_spec` 回 None（環境變數展不開）的來源整個跳過，跟 `_list` 一致。references/sources.md 拒絕表由上往下第一個對到：三句各自落到第 104、108、109 列，前面沒有會先吃掉的列（`已經在排除裡` 不是「在排除「X」裡面」的子字串；include 不會印「找不到」）。

**remove 別名清理**：`_clean_alias(args.alias.strip())`，`str.strip()` 也去全形空白；「.」清成空字串一樣拒絕。空字串在鎖外就拒絕，沒寫檔。沒別名的條目現在用 CLI 拿不掉（只會出現在手改的 sources.json），可接受。

**SKILL.md 搬家那列**：見下面 skill-reviewer Major 1。

**低 R2-1：巢狀排除反方向也說太滿。** 先排除 `請款/2025`（hidden）再排除 `請款`（unused），然後 `include 請款/2025`：完全相等那條優先，exit 0「已取消排除「行政/請款/2025」，從現在起讀得到」，但讀 `請款/2025/x.docx` 仍 `deny Z10`（外層還在）。B-035 只寫了「include 外層後內層仍擋」，這是另一個方向，建議 B-035 一起涵蓋。偏安全。

### plugin-dev 審查（第二輪）

plugin-validator：`claude plugin validate` exit 0，warning 只有根目錄 CLAUDE.md（預期內），Critical 0。

skill-reviewer（`skills/manage`，第二次跑）：觸發句不缺、沒有第二人稱。三句 include 拒絕的落點它也獨立核對過，結論相同。
- **Major 1（認同）**：SKILL.md:33 第①步說「照「加資料夾」那列報給客戶」，那列最後要問別名，AI 會把暫用別名「<舊別名>-新位置」念給客戶、請客戶同意。建議第①步只報份數、憑證檔與提醒，不提暫用別名，別名留到第④步。
- **Major 2（不採，spec 要的）**：第 104 列「不在任何登記的來源裡」exclude 與 include 共用，回「那個夾本來就沒在讀。要先把它所在的資料夾加進來嗎？」。reviewer 認為對 exclude 不合；但 spec 確認情境 5 正是要 exclude 這樣回（「AI 說那個夾本來就沒在讀，問要不要先加公司行政」）。對 include 也合。
- Minor 4：第⑤步說用「新來源的 `path`」，但唯一的 `list --json` 在第②步、新來源還沒加，要再跑一次 list 或用 add `--json` 的 `path`；第④步 add 被拒時客戶兩邊都沒有，可補「說退回上一版救得回舊的」；暫用別名本身被拒（例如撞名）時第 103 列會請客戶換一個他沒取過的名字，應該自己換一個暫用名；第 108 列提議恢復的 X 若是 hidden，沒有像 SKILL.md:31 那樣叫 AI 講出「不想讓 AI 看到」。

## 第三輪（2026-10-16，實作修正 759a353）

對象：`698ff72`（merge main）之後的 `759a353`，只改 SKILL.md 搬家那列的字與 `test_skill_manage.py` 一個測試。只驗 21，加跑全部測試。

| # | 結果 | 證據 |
|---|---|---|
| 21 | 過 | SKILL.md:33 第①步（①到②之間）沒有「別名」二字、也沒有「問」：暫用名寫成 `--alias "<舊名>-新位置"`，註明「只給程式，不念給客戶」，只報份數、憑證樣式的檔、提醒。第⑤步是「第②步記下的排除一條一條 `exclude "<新路徑>/<spec>"`（`spec` 相對來源根），`--kind`、`--reason` 照舊，不用再查」，沒有 list。`python -m unittest -v tests.test_skill_manage`：11 OK，含新的 `test_move_row_step1_no_alias_step5_uses_old_spec` 與既有的 `test_move_row_checks_new_path_first`。 |
| 全部測試 | 過 | `python -m unittest discover tests`：Ran 621，OK，exit 0。`test_budget` 仍綠。 |

看了一下 diff，沒有新的問題。一個字面上的小地方，不影響做法：第①步用「<舊名>」，舊別名要到第②步 `list --json` 才記下；暫用名只要不撞名就行，AI 從對話裡也知道舊名，所以不算問題。

11 路由仍沒跑（`llm-wiki@llm-wiki` disabled，等通知）。

## 驗收 11 路由（2026-10-16，對 759a353 的描述）

**過。** 該觸發 30/30 = **100%**（門檻 ≥ 90%），不該觸發 0/30 = **0%**（門檻 ≤ 10%）。

前置：使用者在測試 session 親手下 `claude plugin enable llm-wiki@llm-wiki`，`claude plugin list` 確認 `✔ enabled`（I-070 要兩套都在）。描述是 SKILL.md 現版（145 字）。

跑法：cwd 在 worktree，
```
PYTHONUTF8=1 PYTHONPATH="<skill-creator>" python "<scratchpad>/evalshim/run_eval_win.py" \
  --eval-set "<ROOT>/tests/evals/manage.json" --skill-path "<ROOT>/skills/manage" \
  --runs-per-query 3 --num-workers 1 --verbose
```
skill-creator 原檔沒動。`run_eval_win.py` 是放在 scratchpad 的一支 shim（全文如下），在模組層把 `select.select` 換成永遠回「就緒」，所以 `run_single_query` 原本的 `os.read` 變成阻塞讀。觸發判定（`content_block_start` 的 Skill／Read、`input_json_delta` 裡有沒有暫存名）一行沒改。另加兩個跟 spec 指令不同的地方：`PYTHONUTF8=1`（見觀察 1）與 `--num-workers 1`（見觀察 2）。
```python
import select
import sys

select.select = lambda r, w, x, t=None: (list(r), [], [])

if __name__ == "__main__":
    from scripts import run_eval
    sys.argv = ["run_eval"] + sys.argv[1:]
    run_eval.main()
```
結果 exit 0，`query failed` 0 次，Results 20/20 passed，花了 7 分鐘。

| 句子 | 預期 | 觸發 |
|---|---|---|
| 把 D:\報價 也加進來，裡面是去年到現在的報價單 | 觸發 | 3/3 |
| 不要讀 公司行政 裡面那個薪資的資料夾，那是私人的 | 觸發 | 3/3 |
| 廠商資料那個資料夾不要了 | 觸發 | 3/3 |
| 薪資那夾還是讀好了 | 觸發 | 3/3 |
| 你現在讀哪些資料夾 | 觸發 | 3/3 |
| 報價那個資料夾我搬到 E 槽了 | 觸發 | 3/3 |
| D:\工地照片6 這個也加近來，剛從隨身碟拷進去的 | 觸發 | 3/3 |
| 公司行政 的資料夾我改名叫 行政總務 了，你那邊要跟著改嗎 | 觸發 | 3/3 |
| 感覺卡片都亂掉了，幫我重建知識庫好了 | 觸發 | 3/3 |
| 幫我把信義王的報價 v2 搬到王宅那夾 | 觸發 | 3/3 |
| D 槽報價單裡信義王那份寫多少錢 | 不觸發 | 0/3 |
| 以後不要把私人的事記進卡 | 不觸發 | 0/3 |
| 最近改了什麼？我記得昨天好像有加一個資料夾進來 | 不觸發 | 0/3 |
| 退回上一版，剛剛那個不對 | 不觸發 | 0/3 |
| 薪資那夾裡面去年的檔有幾份？順便抓一下總金額 | 不觸發 | 0/3 |
| 幫我把王宅跟信義王兩案的報價整理成一張比較表，存到 AI 產出 | 不觸發 | 0/3 |
| 收件匣裡那份廠商新合約幫我看一下重點，有沒有要注意的 | 不觸發 | 0/3 |
| 卡片裡王宅的地址寫錯了，是信義路五段不是四段，幫我改一下 | 不觸發 | 0/3 |
| 幫我寫封信給廠商資料裡那家磁磚行，問下個月的報價 | 不觸發 | 0/3 |
| 把信義王報價 v2 的付款條件改成 30 天 | 不觸發 | 0/3 |

清理：跑完 `.claude/commands/` 裡沒有任何檔（每次的暫存指令檔都刪了）；只留下 run_eval 建的空夾，原本沒有這個夾，已 `rmdir`。worktree 現在只有本報告未追蹤，沒有 commit 任何東西。

### 觀察：skill-creator 的 run_eval 在這台 Windows 上不能照 spec 的指令直接跑

1. **讀檔編碼**：`json.loads(Path(args.eval_set).read_text())` 沒指定 encoding，Windows 預設 cp950，讀 UTF-8 的中文 evals 一開始就丟 `UnicodeDecodeError`。用 `PYTHONUTF8=1` 繞過。
2. **select 不支援管線**：`run_single_query` 用 `select.select([process.stdout], …)` 讀 `claude -p` 的輸出，Windows 的 select 只收 socket，每次都丟 `WinError 10038 嘗試操作的對象不是通訊端`。例外被當成「沒觸發」，所以第一次跑 60 次全是 0/3，看起來像描述完全沒用，其實一次都沒問到模型。用上面的 shim 繞過。
3. **平行 worker 互撞**（不限 Windows）：預設 `--num-workers 10`，每個 worker 都把同一段描述寫成 `.claude/commands/manage-skill-<各自 id>.md`，放在同一個資料夾。模型同時看到最多 10 個一模一樣的指令，挑到別的 worker 那個就算沒觸發，期望值大約 1/10。第二次跑（有 shim、10 workers）實測該觸發 3/30 = 10%、不該 0/30，跟這個推算吻合。佐證：另寫一支只看第一個 tool_use 的診斷，一次一句跑「你現在讀哪些資料夾」「廠商資料那個資料夾不要了」「把 D:\報價 也加進來…」，三句都是 `Skill` → 自己那個暫存名。改 `--num-workers 1` 後 30/30。

以後跑 D-091 路由驗收要 `--num-workers 1`，或改用每個 worker 各自一個 cwd 的跑法；1、2 在 Windows 上一定要繞。設計 session 說會記 backlog。

## 第四輪（2026-10-16，確認後修正 f38382f）

對象：`c16abee`（merge main）之後的 `f38382f`：`skeleton/CLAUDE.md` 一句、SKILL.md 兩處，加兩支測試檔的新測試。只驗 22、23，加跑全部測試與 `claude plugin validate`。

| # | 結果 | 證據 |
|---|---|---|
| 22 | 過 | `skeleton/CLAUDE.md` 第 3 行（身分段）尾加「客戶講繁體中文，回答客戶一律繁體中文。」；全檔 12 行、455 字（上限 25 行、1200 字）。`kb init "<scratchpad>\init4\知識庫"` exit 0「建了 11 項」，建出的 `CLAUDE.md` 跟 skeleton 逐位元組相同（`cmp`）。SKILL.md「## 共通」段第一條「回答客戶一律繁體中文。」 |
| 23 | 過 | SKILL.md「不讀某夾」列開頭「先跑 `list --json` 查登記」，在①之前；含「那個夾本來就沒在讀」與「不從別的路徑推上一層或接子夾，這句話裡不跑 `exclude`」；在來源裡才問①②。`tests.test_skill_manage`、`tests.test_budget`、`tests.test_scripts_init_cli` 合跑 41 OK。 |
| 全部測試 | 過 | `python -m unittest discover tests`：Ran 624，OK，exit 0。 |
| plugin validate | 過 | `claude plugin validate "<ROOT>"` exit 0，「Validation passed with warnings」，warning 跟前幾輪一樣只有根目錄 CLAUDE.md 一條，另有 README 沒有 marketplace 安裝行的建議。 |

看了 diff，沒有新問題。「不讀某夾」列的「不從別的路徑推」只管夾不在登記來源裡的情況，跟 SKILL.md:37「`exclude` 用來源的 `path` 接上夾名」（夾在來源裡時）不衝突。
