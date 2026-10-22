# produce：測試報告（第一輪）
日期：2026-10-22
給誰看：設計 session（決定要不要修、要不要發行）。
什麼時候更新：每輪測試後。

被測：worktree `llm-wiki-j-produce`，分支 `spec/produce`，HEAD `ecdb9d9`（實作 `5e12a76`、`18b9d7b`、`ecdb9d9`；base `b37888d`）。spec 以 main 的版本為準（含「開工前查核結果」、「實作回報裁定」1～6、「實作回報後裁定」7～11）。
方法：驗收 19 條逐條自己跑（黑箱；環境見下）；code-review（high）與 security-review 派唯讀子代理只讀碼列疑點，有重現價值的我自己用 `kb guard decide`、CLI 實跑；skill 審查用 `plugin-dev:skill-reviewer`、plugin 審查用 `plugin-dev:plugin-validator` 與 `claude plugin validate`；第 16 條用 skill-creator 的 `run_eval`；第 17 條有無 skill 對照用 headless `claude -p`。沒改任何程式（只新增這份報告）。

## 結論

**驗收 19 條：18 過、1 不過（第 17 條）。** `python -m unittest discover tests` exit 0（Ran 1854，OK，skipped=1，430 秒），樹乾淨；`claude plugin validate` 通過（只有 icon 與 README 安裝行兩則既有警告）；路由（willy-probe 干擾排除後重跑的結果）produce 平均 91.1%（三輪 90／90／93%，剛好過 90% 門檻；第四輪 93%）、不該觸發 0%，其他四個 skill 不退步（note 的不該觸發 1／30，原本「7／30」沒復發）。**環境干擾與作廢的結果見「環境干擾」一節。**

**但有一個擋住主功能的高嚴重度問題（H-1）**：全新知識庫（還沒有 `.system/temp/office/` 這個夾）第一次「新做」Word／Excel／PDF，AI 照 SKILL 用檔案工具把腳本寫進 `kb office check` 印的工作區，被守門擋掉（理由句還是 Z7 的「這是知識庫的設定與狀態檔…」，AI 只能照規矩停下來請客戶找服務人員）。因為 `office check`、`office setup` 都不建工作區，只有 `office copy`、`office run` 會建，而 `run` 要先有腳本。第 17 條對照裡，裝上 skill 的版本在 (b)(c)(d) 全部卡在這裡（3／3 次），沒裝 skill 的版本同樣先被擋 3 次，但它們接著把腳本改寫在庫外的 session 暫存夾，所以做得出來。**預先建好 `.system/temp/office/` 之後，(c)(d) 完整走通（`r2-c-skill`、`r2-d-skill`）**（草稿 → tally → 腳本 → `office run` → `recalc` 錯誤 0／`office pdf`＋`render` 看版面 → `output save --draft`），所以問題只在那一步。

找到的問題（詳見「疑點」）：高 1、中 6、低 9。

| 等級 | 編號 | 一句話 |
|---|---|---|
| 高 | H-1 | 新庫第一次寫工作區的腳本被守門擋（工作區夾還不存在），新做檔案整條路卡死 |
| 中 | M-1 | `output link` 不進回合帳：只有登記連結的回合，「退回」會退到前一個不相干的回合 |
| 中 | M-2 | 同網址再 `output link` 會把第一次的 title／type／entities／basis 清空 |
| 中 | M-3 | `recalc` 的錯誤檢查漏報：自閉合的有格式空格後面的 `#DIV/0!` 不算錯誤 |
| 中 | M-4 | (b) 草稿那句問的是「做成 Word」，沒有推薦線上文件三件事；客戶說做成線上文件時，AI 沒先講三件事就叫 Docs 建 |
| 中 | M-5 | 改 Word 的修訂作者實測 2／2 是「Claude」，不是 SKILL 規定的「AI 助理」 |
| 中 | M-6 | `office pdf` 轉壞檔報成功，回傳同主檔名的舊 PDF |
| 低 | L-1～L-9 | 見疑點 |

## 驗證環境

- `<V>`：`tests/fixtures/inventory/{vault,raw}` 複本，另在 raw 加兩份真的 docx／xlsx（用標準函式庫組，內含付款條件與公式），登記後手寫三張檔案卡（合約、報價、卡型「範本」）。因為我的 session 暫存路徑很長，LibreOffice 在那邊會壞（L-1），所以第 6 條以後都搬到短路徑 `C:\Users\Jie\Desktop\llm-wiki-test\produce-test\a\vault`（及 `b`、`c` 副本）。
- 回合：終端機直跑都在同一回合；要測 revert 與草稿跨回合，用 `hooks/turn.py` 送 UserPromptSubmit（每回合不同 session）。PostToolUse／PreToolUse 用 `hooks/output_log.py`／`hooks/guard.py` 餵 stdin。
- 第 17 條：基底是 `llm-wiki-test\ingest-check\` 複本（`produce-test\base\`，沒動 ingest-check 本身），做了 `cards sync`（複製後卡的原檔位置要更新）、加一份 6 頁的 `設計提案.pptx`（用 LibreOffice 由 FODP 轉）並手寫它的卡、日程標記已抽，開場時沒有「還有 N 份沒整理」的指示。plugin 用 `git archive HEAD` 的兩份複本：`pl-skill`（完整）與 `pl-noskill`（拿掉 `skills/produce/`）。每句一份獨立的 `<V>` 複本，`claude -p --plugin-dir … --output-format stream-json --permission-mode acceptEdits`，兩回合用 `--resume`。舊 `llm-wiki` 維持 disabled。
- **這次測試在這台電腦留下的東西**（確認前請使用者清掉）：
  - `%LOCALAPPDATA%\wiki-with-willy\tools\`（約 51 MB）：`node\node_modules\`（`docx`、`pptxgenjs` 與它們的相依）、`pylib\`（`defusedxml`、`lxml`、`openpyxl`、`pypdfium2` 與相依）。由第 17 條的 `claude -p` 跑 `kb office setup` 裝的（多個平行的 run 同時裝，結果完整）。
  - `C:\Users\Jie\Desktop\llm-wiki-test\produce-test\`（拋棄式：`base`、`a`、`b`、`c`、`r-*`、`r2-*`、`first-*`）。
  - 我的失誤一件：測第 2 條外面時我用 shell 環境變數把 `LOCALAPPDATA` 指到暫存夾再叫 `python`，Windows 的 Python 安裝管理員被觸發，輸出「Python install manager was successfully updated to 26.4」與「Installing Python 3.15.1」。3.15.1 裝在那個暫存 `LOCALAPPDATA` 底下，`py --list` 只有原本的 3.14.7；但安裝管理員本身可能被更新到 26.4。對 repo 無影響，記在這裡讓使用者知道。
  - Office 沒有（這台只有 LibreOffice）：**Office 路徑（PowerShell COM 轉 PDF、Excel 重算）未確認**，測的只有 LibreOffice 路徑與假環境的偵測。

## 逐條

| # | 結果 | 證據（摘要） |
|---|---|---|
| 1 | 過 | `office check` 在四種假環境（有 Office 三個、只有 LibreOffice、都沒有、沒 npm，另加沒 node、全裝好）各印對的「可以：…；不行：…（要裝 …）」與「要先做」（用 monkeypatch `find_*` 的 harness，登錄檔層沒測到：這台沒有 Office）；`--json` 有 office×3、soffice、node、npm、node_packages、py_missing、pdfium、tools、capabilities、workspace。跑前後對 `<V>` 全樹雜湊 `diff` 無差異（不建 `.system/temp`）。真環境：`Office：沒有`、`LibreOffice：有`、`Node.js：有（v24.20.0）`、工具夾沒裝。小：有 node 沒 npm 時「不行：新做 Word、新做簡報（要裝 Node.js）」但上一行寫 Node.js 有（L-5）。 |
| 2 | 過 | 用假的 `_call`（npm／pip 替身）與暫存 `LOCALAPPDATA`：驗證 import 前兩次失敗、第三次成功 → 等 5 秒×2、`已裝好：python 套件…；node 套件…`；再跑一次 → `工具已經裝好了，不用再裝`；驗證一直失敗 → 4 次、等 5 秒×3、exit 3 `python 套件裝好了但載不起來：…；過一兩分鐘再跑一次 kb office setup`；沒 node／npm → 只裝 python 那組並印 `這台沒有 Node.js，只裝了 python 那組；新做 Word 或簡報要 Node.js；這台沒有，可以改做成線上文件，或請你裝 Node.js`。真環境由第 17 條實裝成功。小：套件夾只要存在就算裝好、不驗 import（L-2）。 |
| 3 | 過 | `office copy f-9f7b0f71`（卡編號）、路徑、`../raw/…` 都複製到 `.system/temp/office/<回合>/`，原檔 sha1 不變；同名加 (2)(3)。拒絕：像密碼 `拿不到副本：檔名像密碼或金鑰，預設不讀`、不在登記夾 `不在登記的資料夾裡`、清冊沒有 `清冊沒有 f-00000000`、`.system/inventory.json` `這不是要製卡的檔`、排除 `這個位置你說過不讀`（`folders exclude` 之後用編號與路徑都擋）；與 `kb convert` 同一組理由（只差開頭「拿不到副本」／「讀不到」）。 |
| 4 | 過 | `office run` 工作區內 .py：`OUT_DIR`＝工作區、`PYTHONPATH`＝工具夾 `pylib`、cwd＝工作區、參數收到；工作區外腳本、vault 內非 temp 的腳本、`..` 跳出去、`.sh`、不存在，各拒絕（`腳本要放在工作區裡…，這支不在：…`、`只跑 .js 或 .py 的腳本`、`找不到這支腳本`）；腳本退出碼 7 → `結束碼：7` exit 3；`.system/config.json` 設 `office_run_seconds: 2` → 2.4 秒後 `跑超過 2 秒，停掉了`。 |
| 5 | 過 | 自組的 6 部件 docx（含 STORED 的二進位部件、不同壓縮方式、非字母順序）`unpack`→改 `document.xml`→`pack --like`：檔內順序相同、每個部件壓縮方式相同、除 `document.xml` 外位元組與 CRC 都相同。zip 檔頭時間戳不同（`date_time` 變成打包時間），部件內容逐位元相同。另：`--to` 跳出工作區、打包工作區外的夾、zip 內 `../../x` 路徑（zip-slip）都拒絕。 |
| 6 | 過（有 L-1） | **短路徑**：`office pdf` 用 LibreOffice 轉出 PDF（6.3 秒），`render` 印每頁 PNG 路徑；`recalc` 一份有公式的 xlsx：`重算好了（LibreOffice）：公式 3 個、錯誤 0 個`，快取值寫回。都沒工具的假環境：`pdf`、`recalc`、`render` 都 exit 3，固定句一字不差，`recalc` 多一行 `公式的結果要打開檔案才會更新`。**長路徑（profile 路徑 ≥ 約 150 字元）時 LibreOffice 直接閃退（exit 0xC0000409）**，程式報 `轉不出 PDF（LibreOffice）：沒有訊息`（L-1）。 |
| 7 | 過 | `output save <工作區>/大安陳報價.xlsx --to "AI 產出/報價/大安陳報價.xlsx" --from f-b468cc2e` → `已放進 AI 產出\報價\大安陳報價.xlsx，改自「大安陳報價」（說「退回」會拿掉）`；工作區的檔被搬走；回合 manifest 有 `create`；清冊 `origin: ai_output`、`derived_from: f-b468cc2e`、sha256；`cards todo` 列它；`versions revert` → `成品 1 份留著`，清冊 status `turn_reverted`。拒絕一字不差：`只能放進 AI 產出底下`（收件匣、`../`、`AI 產出/../案子/`、`AI 產出/`、`AI 產出`）、`md 檔用檔案工具寫就好`、`這個檔名像密碼或金鑰，不放`、`找不到這個檔：…`；同名加 (2)(3)。來源限制：知識庫內 `.system/*.json`、`案子/…` → `只收工作區或知識庫外的檔，這份在知識庫裡`；raw 原檔 → `客戶原檔先用 kb office copy 拿副本`；知識庫外的檔用複製、不動原處；來源檔名 `id_rsa`、`網銀密碼.txt` 一樣拒絕。 |
| 8 | 過（一小項沒驗） | 草稿 `AI 產出/報價/2026-10-22 內湖李 進度報告（草稿）.md` 經 PreToolUse allow、PostToolUse 登記：`cards todo` 不列；**同一回合** `asks` 不列（裁定 6），下一回合 `asks --json` 有 `draft`（問「內湖李 進度報告 的草稿還在等你看」，結果 定稿／再改／不要了，「不要了」的 do 是 `kb output drop-draft "…"`、strict）；有 `close` 時順序是 close → draft。`output save … --draft` → 成功句多 `，草稿收起來了`，草稿不在、manifest 有 `delete`；`versions revert` → `還原 1`、草稿回來；`drop-draft` 單獨用也可 revert 回來；拒絕 `草稿不在 AI 產出：…`（收件匣檔、不存在、非草稿的成品）。沒驗到：「排在名字確認之前」（沒造出待確認實體的問題）。 |
| 9 | 過 | 有卡後 `output link https://claude.ai/artifact/li001 --title … --type 報價 --entity 內湖李 --basis f-… --basis f-…` → `已登記線上文件「…」（下次整理會做卡）`；清冊 `kind: link`、`origin: ai_output`、位置是網址、`title/type/entities/basis`；`cards todo` 列它，`檔案索引/AI 產出/線上/<標題>-….md`，header `開原檔: https://claude.ai/artifact/…`（header 的 `實體: []` 沒預填，因為同網址再 link 被覆蓋，見 M-2）。拒絕 `網址要是 claude.ai 的 artifact`（http、`claude.ai.evil.com`、example.com、含空白）、`這份還沒有檔案卡`（依據卡沒卡時）、`清冊沒有 f-00000000`。`output save <庫外的.pptx> --to … --link <網址>` → 同一筆 link 有兩個位置（網址與 `file://AI 產出/簡報/…`）、複製不動原處；`--link` 沒登記過 → `這個網址還沒登記；先跑 kb output link`。**M-1／M-2 在這條旁邊找到。** |
| 10 | 過 | `kb guard decide`（cwd＝`<V>`）：`node x.js "<V>/AI 產出/a.docx"`、`python x.py "<V>/AI 產出/a.xlsx"`、`soffice --convert-to pdf "<V>/AI 產出/a.docx"`、`cat "<V>/AI 產出/x.md"`、`node x.js AI\ 產出/a.docx`、PowerShell `node …`、`Get-Content "AI 產出\x.md"` → `deny Z3 output_shell AI 產出的檔用 kb output save 放進來；要轉 PDF 或看版面用 kb office`；`cp "<raw>/合約.docx" "<V>/.system/temp/x.docx"` → `deny Z9 … 客戶原檔只讀；要改先用 kb office copy 拿一份副本`；Write 原檔 raw → 同句；Edit `案子/信義王/合約.docx` → `deny Z2 … 要改先用 kb office copy 拿一份副本`；`python kb.py output save …` → `allow 知識庫腳本`；Read／Write AI 產出 → `allow Z3`。工作區例外：Write `.system/temp/office/<回合>/build.js` allow（工作區夾存在時）；`..` 跳出、`.system/inventory.json`、`.system/temp/other/x.js`、`_lo_profile/…`、Bash `echo > …/office/t/x.js` 全 deny（Z7）。**既有守門測試全綠**（單元測試 exit 0）。security-review 見「疑點」。例外與缺口：**H-1**；包在引號裡的指令（`bash -c "cat 'AI 產出/x.md'"`）、遞迴讀（`grep -r 報價 .`、`find . -name '*.md'`）、`$HOME/…`、`AI?產出` 這幾種讀取回 `pass`（不表態，交預設權限去問客戶，不是 allow），與「一律擋」的承諾有落差（L-6）。 |
| 11 | 過 | 把 `寫死路徑.docx` 直接放進 `AI 產出/報價/` → `inventory scan` 登記成 `origin: ai_output`、`unbacked: true`；`cards todo` 列它；`tell` 有 `AI 產出有 2 份沒經程式放進來，這幾份退回拿不掉`（2＝這份加 fixture 原有的 `x.md`）；SessionStart hook 的快速掃描也看得到「還有 N 份沒整理完」。 |
| 12 | 過 | `profile add 範本 文件=報價單 底=f-ab1b83ba` → `已加範本：報價單`；`profile.json` `schema_version: 4`、`templates: [{doc, card, source: corrected, date}]`；md 第七節表格顯示卡標題（`報價單範本`）；`底=` 也收標題；卡不存在／標題不存在 → `找不到這張卡：…` exit 3；`remove 範本 文件=合約` 拿掉；`--json` 有欄位。`kb init` 對 schema 3（md 沒改過）→ 升 4、補第七節、`templates: []`；md 有手改 → `設定檔有沒套用的改動，先 kb profile apply 再跑 kb init 升級`。 |
| 13 | 過 | 所有 `office`、`output`、`profile add 範本` 人讀輸出掃 `[aefsrcp]-[0-9a-f]{8}`：0 筆（含 `office copy`、`output save`、`output link`、`drop-draft`）；`--json` 有編號。 |
| 14 | 過 | `rules/core.md` R-01 多「Word、Excel 這類檔用 kb output save 放進去」；單元測試（含 `test_budget`）exit 0。 |
| 15 | 過 | 描述 146 字（≤150）、本文 2915 字含空白／2659 不含（≤3000）；分辨（新做、套範本、改原檔、轉格式、存成檔、草稿定稿、不是這裡的話）與原則各帶理由與 D 編號；沒有參數對照表；`references/draft.md` 56 行、`make.md` 74 行（≤150）；`tests/evals/produce.json` 20 句，10／10。skill-reviewer 另有意見，見第 18 條。 |
| 16 | 過（邊緣） | 見「第 16 條：路由」。重跑後 produce 三輪 90%／90%／93%（平均 91.1%，82／90；另跑第四輪 93%），不該觸發 0／90；note、answer、ingest、manage 重跑各 30／30，不該觸發 0／30（note 1／30）。弱點句「剛才那個表幫我存成檔案」4 輪只觸發 2／12。 |
| 17 | **不過** | 見「第 17 條：有無 skill 對照」：(a)(e) 過；(b) 不完全照 spec（M-4）且在 skill 版卡 H-1；(c)(d) 新庫卡 H-1，預建工作區後走通。 |
| 18 | 過（有修正建議） | `plugin-dev:skill-reviewer` 看過 `skills/produce/` 與 answer／note 改的幾句（4 項必修、7 項建議，見「plugin-dev 審查」）；`claude plugin validate "C:\Users\Jie\Desktop\llm-wiki-j-produce"` 通過（警告只有 icon 與 README 安裝行，既有）。 |
| 19 | 過 | `python -m unittest discover tests` exit 0（Ran 1854，OK，skipped=1，430 秒），跑前樹乾淨（`git status --short` 空）；README 加「做成文件」一段與指令清單、repo `CLAUDE.md` 地圖加 `office.py`、`output.py`、兩個 cli、`skills/produce/` 等（`git diff --stat` 兩檔有改）。「不可以動」清單（credentials、cmdparse、turn、versions、hooks、plugin.json、CHANGELOG、既有四份 evals、其他 skill）`b37888d..HEAD` 皆無變動；`design/` 只多 backlog 一行（B-400；裁定 7 的 B-401、裁定 11 的 B-402、B-403 沒進這個分支的 backlog，應該是設計 session 在 main 補）。 |

## 第 16 條：路由（run_eval）

跑法（B-036）：skill-creator `scripts/run_eval.py` 原檔不動；因為 Windows 管線不能 `select` 又要用 `ProcessPoolExecutor`（spawn），**shim 用 `sitecustomize.py`（`PYTHONPATH` 前面放一個把 `select.select` 換成永遠就緒的模組）**，`PYTHONUTF8=1 … --runs-per-query 3 --num-workers 1 --timeout 60`。每份各用一份 `git archive HEAD` 的獨立複本（各自的 `.claude/commands/`），produce 三輪同時跑、其餘四份同時跑，沒有跟單元測試同時。舊 `llm-wiki` disabled（`claude plugin list` 確認）。

| 檔 | 該觸發（20 句中 10 句×3 次） | 不該觸發（10 句×3 次） | `query failed` |
|---|---|---|---|
| `produce.json` 重跑第 1 輪 | 27／30（90%；「剛才那個表幫我存成檔案」0／3） | 0／30 | 0 |
| `produce.json` 重跑第 2 輪 | 27／30（90%；同一句 0／3） | 0／30 | 0 |
| `produce.json` 重跑第 3 輪 | 28／30（93%；同一句 1／3） | 0／30 | 0 |
| `produce.json` 重跑第 4 輪（多跑） | 28／30（93%；同一句 1／3） | 0／30 | 0 |
| **produce 平均（前三輪）** | **82／90（91.1%）**（門檻 ≥ 90%，邊緣；加第四輪 110／120＝91.7%） | **0／90（0%）**（門檻 ≤ 10%） | |
| `note.json` 重跑 | 30／30 | 1／30（「我把信義王的合約換成新版了，檔名一樣」1／3） | 0 |
| `answer.json` 重跑 | 30／30 | 0／30 | 0 |
| `ingest.json` 重跑 | 30／30 | 0／30 | 0 |
| `manage.json` 重跑 | 30／30 | 0／30 | 0 |

note 上次的「不該觸發 7／30」不見了（1／30）；沒有退步。**第一次跑（11:22～11:33，被 willy-probe 干擾）的結果作廢**，當時 produce 100%／100%／93%、四個其他 skill 全部 30／30 與 0／30；重跑 produce 掉到 90%，差異主要在「剛才那個表幫我存成檔案」（0／3 或 1／3，與 `anthropic-skills:xlsx` 競爭，上輪 3／3 與 2／3），建議描述再補「存成檔案／存成 Excel」的觸發詞（skill-reviewer 必修 1 同向）。限制：run_eval 只放被測 skill 的暫存指令檔，沒有載 wiki-with-willy 本身，所以不是「五個 skill 同時裝著」的路由；那要看第 17 條的真實對話——那邊有一個觀察：沒裝 produce 時，「大安陳的合約，付款條件改成…」被 note 接走，記成口述（`statement add`），裝上後走 produce，路由是對的。

## 第 17 條：有無 skill 對照

指令序列只列 `kb`／工具重點，路徑縮寫。`有`＝裝上 `skills/produce/`，`無`＝拿掉。所有 run：`src_changed` 為空（客戶原檔的 sha1 一份都沒變）。

### (a)「大安陳的合約，付款條件改成完工後 30 天內付清」
（第一次我用「信義王合約…」：知識庫裡信義王有兩份寫法不同的合約，`有` 先問哪一份、哪個意思，這是資料本來就矛盾，不算缺陷；但回答裡貼了〔檔案卡 f-07df81a8〕這種內部編號，見 L-7。改用只有一份合約的大安陳。）
- **有**（過）：`Skill produce` → `lookup 大安陳` → `office check` → Read 卡 → `office copy f-d7e8411a` → `office unpack` → Read／Edit `word/document.xml`（插 `w:ins`，第二次連 `w:del`）→ `office pack --to … `（其中一次漏了 `--to`，exit 2 後自己改）→ `output save … --from` → `asks` → `turn note`。原檔不動；成品 `AI 產出\合約\大安陳合約-付款30天.docx`（有登記、有 `derived_from`）；回答說放在哪、改自哪份、「說『退回』就會拿掉」、改的是哪一句。**修訂作者 2／2 都是「Claude」**，SKILL 規定「AI 助理」（M-5）。它想用 `python -I -c` 讀 docx 數修訂處，被守門擋（Z7 理由句），回答老實說「那個檢查指令被系統擋下，沒做成，請你打開看」——SKILL 要它「改完數一下修訂幾處」，但沒告訴它在不能用 `python -c` 的情況下怎麼數（可以 `unpack` 後用 Grep），低。
- **無**：被 `note` skill 接走：`statement add --entity 大安陳 …` 記成口述，沒有做任何檔（一次因為「實體清單沒有大安陳」停下來問）。成品沒有，也就沒有登記。
- 判定：過。

### (b)「幫我做一份內湖李的進度報告給業主」→「內容可以，就這樣」
- **有**：`Skill produce` → `lookup` → Read `draft.md` → Read 綜合卡與卡 → `office check` → `convert` 兩份核對 → `agenda list` → Write `AI 產出/報告/2026-10-22 內湖李 進度報告（草稿）.md`（檔名格式對、句尾短標〔卡標題〕、數字標「原檔已核對」，照片辨識不適用）→ `asks` → `turn note`。草稿本身好：發現「報價第四版」原檔標題是信義王宅、案子是「擱置」卻有 10/21 口述，列成「要你先決定的事」四項。**但問句是「內容這樣可以嗎？可以的話我就做成 Word。做 Word 前要先裝一組文件工具」——沒有推薦線上文件，也沒講存在哪／誰看得到／要檔怎麼拿**（draft.md 與 make.md 都叫它要講）。第二回合說「就這樣」：它去做 Word（載 `anthropic-skills:docx`、`office check`），Write 腳本到工作區 → **被守門擋（H-1）**，回答停下來請客戶找服務人員，並給兩個選項（修好再做／貼可複製的版本）。
  - 補充（第一輪，第二回合我說「做成線上文件吧」）：它**先直接呼叫 Claude Docs 的 `batch`（沒有先講三件事）**，被權限擋；擋下後回答才講了存在哪、預設只有她看得到、要檔的拿法。
- **無**：`answer` skill 接走，沒給草稿，回一份「要請您確認」的分析；第二回合（Word）它先 Write 到工作區也被擋，改寫到 session 暫存夾 `python` 組出 docx，`output save` 放進 `AI 產出\案子\內湖李宅進度報告.docx`（成功，原檔不動）。第二回合（線上文件）也是直接呼叫 Docs 建。
- 判定：不完全照 spec：沒有先推薦線上文件並講清楚（M-4），且在新庫被 H-1 擋。草稿本身（位置、檔名、短標、核對）過。

### (c)「幫我把大安陳的報價整理成 Excel」→「內容可以，做成 Excel 檔吧」
- **有**（新庫）：`lookup` → Read 四張報價卡 → Read `draft.md` → `convert`×4 → Read → `tally --row`×3 版（每版拆除／水電／木作逐項、合計 59／61／62／62 萬）→ Write 草稿 `（草稿）.md` → `asks` → `turn note`；回答把四份並排、指出 v1 與改單價版同日期同標題但水電不同、合約總價 65.1 萬跟任何報價都不同，「沒替你選」，問要不要裝工具。第二回合說可以：`office setup`（成功）→ Read `make.md` → `office check` → Write 腳本到工作區 → **被守門擋（H-1）**，回答停下來。
- **有**（預先建好 `.system/temp/office/`）：同樣的草稿（tally）→ Write `make_quote.py`（allow）→ Edit → `office run` → `office recalc`（錯誤 0）→ Write `check_quote.py` → `office run`（讀回來比）→ `output save … --draft <草稿>` → `asks` → `turn note`。成品 `AI 產出/報價/2026-10-22 大安陳 報價.xlsx`（4 個公式、有快取值、第二張表「說明」）、草稿收起來（manifest：create xlsx、delete 草稿）、`cards todo` 列它；回答「成品上沒有附出處，也沒有內部檔名」、說了合約總價並列；沒有內部編號。
- **無**：不給草稿，直接做；`output save --help`、`office setup`；Write 腳本到工作區 → 被擋；改寫到 session 暫存夾 → `python` 組出 xlsx → `office recalc` 被拒（只收工作區的檔，它改用手算）→ `output save` 放進 `AI 產出\案子\大安陳報價整理.xlsx`；回答貼了 `p-e5e22942` 內部編號，說「我沒能用知識庫的重算指令檢查公式，公式是我手算核對的」。
- 判定：新庫不過（H-1），預建工作區後過（逐份核對、`tally`、`recalc`、`--draft` 全做到）。

### (d)「這週的日程印出來，我要貼在牆上」→「內容可以，做成 PDF」
- **有**（新庫）：`agenda list`（人讀、`--json`、`--to`）→ Read `draft.md` → `convert` 核對兩張卡 → Write 草稿 → `asks` → `turn note`；回答標「日期是推測」、說 10/30 超出 7 天問要不要放；第二回合 Write 腳本 → 被擋（H-1）。
- **有**（預建工作區）：Write 腳本 → `office run` → `office pdf` → `office render` → Read PNG 看 → Edit 腳本（調兩次）→ 重做 → `output save --draft`，回答「A4 直式、大字表格、我轉成圖看過字都沒折行」、「日期要自己確認（推測）」、「說『退回』就會把這份 PDF 拿掉，草稿也會回來」。
- **無**：`answer` skill 接走，只列清單；第二回合載 `anthropic-skills:pdf`，用 reportlab 在 session 暫存夾組 PDF（自己跑 `ls /c/Windows/Fonts`、`pypdfium2` 轉圖看），`output save` 放進去；回答貼了完整絕對路徑，沒有說「退回」。
- 判定：新庫不過（H-1），預建工作區後過。

### (e)「大安陳的設計提案簡報，第 5 頁的標題改成〔一段 60 多字的長句〕，內文不用動」
（第一次我要它「內文也補上規格與交期」，知識庫沒有這些資料，`有`、`無` 都正確地停下來問、不編；改成只改標題。）
- **有**（過）：`lookup` → `office check` → `office copy file://…設計提案.pptx` → `office unpack` → Read／Edit `slide5.xml` → `office pack` → `office render --pages 5` → Read PNG（看出 44 點字會爆出標題框）→ 改成 24 點（先試 `sed -i`，被守門擋，改用 Edit）→ `pack` → `render` → Read PNG 看過 → `output save --from` → 回答說「原本 44 號字會超出頁面上緣、壓到內文，所以只把這一頁標題縮成 24 號字、排成兩行」、「我用轉圖看過」、「下一步：打開那份簡報確認第 5 頁的標題」、說退回會拿掉。檔名用「設計提案（大安陳）第5頁標題改」，沒照 make.md 的「原檔主檔名（AI 修改）」，不影響。
- **無**：`office copy` → `unpack` → Edit → `pack` → `render` → 看出長標題會爆出框；接著想縮字級時用 `python -I -c` 改工作區 XML 被擋，沒有存檔，回答停下來請客戶找服務人員。
- 判定：過。

### 小結
裝上 skill 的好處實測：路由正確（沒裝時 (a) 被 note 記成口述、(b)(d) 被 answer 接走）；草稿與核對；`tally`；`office render` 看版面並調字級；`output save` 放進來並說退回。壞處／缺口集中在 H-1。

## 環境干擾

設計 00 通知：別的 session 在使用者範圍裝了假 plugin「willy-probe」（B-093 spike），11:16～11:46 它的 SessionStart 與 PreToolUse hook 跑進這台每個 Claude Code（開場多一行 cp950 亂碼、每次工具呼叫多一個 PreToolUse）。我 11:58 確認：`claude plugin list` 與 `~/.claude/settings.json` 的 `enabledPlugins` 都沒有它。

作廢並重跑的：

| 項目 | 第一次（作廢） | 重跑（以此為準） |
|---|---|---|
| 第 16 條 produce 三輪（ev p1～p3） | 100%／100%／93% | 90%／90%／93%（另加第四輪 93%）；差異主要是「剛才那個表幫我存成檔案」（第一次 3／3、3／3、1／3，重跑 0／3、0／3、1／3） |
| 第 16 條 note／answer／ingest／manage | 各 30／30、0／30 | 各 30／30；不該觸發 0／30（note 1／30） |
| 第 17 條 c、d 的 skill 與 noskill（`first-c`、`first-d`；開始時間約 11:43） | skill：新庫第二回合寫工作區腳本被擋；noskill：先被擋再改寫庫外暫存夾做出檔 | 重跑 `r3-c-*`、`r3-d-*`：skill 版同樣被擋（H-1 重現）；noskill 版走 `ingest` skill 接續（先為前一回合做出的成品做檔案卡）再用庫外暫存腳本把 PDF／Excel 做出並 `output save`，結果與第一次同類 |
| 第 17 條 a、b、e | 第一次的 prompt 版本（`first-a`、`first-b`、`first-e`） | 之後調整過 prompt 重跑，開始時間都在 11:47 之後（`r-a-*`、`r-b-*`、`r-e-*`，工作區回合代號 11:48 起），不受影響，沒再重跑 |

第 17 條 c、d（預建工作區的 `r2-c-skill`、`r2-d-skill`）開始於 11:48，不受影響。其餘（單元測試、`kb` 黑箱指令、`kb guard decide`、plugin validate）不依賴 `claude` 對話，不受影響；單元測試跑在 11:30 前後約 7 分鐘，內容是純 Python／黑箱 CLI、不經 Claude 對話（plugin-validator 之後獨立又跑一次 1854 OK）。

兩個結論的影響：H-1 在干擾排除後仍重現（skill 版 b、c、d 的第二回合各 1 次，共 3／3 被擋），不是干擾造成；路由 produce 的 90% 邊緣是新發現，建議描述補觸發詞後再跑。

## 疑點

### 高
- **H-1 新庫第一次寫工作區被擋**（`scripts/kb/guard.py:473-482` `in_workspace`）。工作區根（`.system/temp/office/`）不存在時往上找「磁碟上存在的最近祖先」，得到的 `real_root` 是 `.system/temp` 或 `.system`，而目標的 `real` 反而在它「裡面」，`real == real_root or inside(real, real_root)` 為假 → 落回 Z7 deny。重現：`kb guard decide --tool Write --path "<V>/.system/temp/office/<回合>/build.js"`，`<V>` 是沒做過 `office copy`／`run` 的庫（`produce-test\c\vault`）→ `deny Z7`；`office copy` 之後同一個路徑 → `allow Z7 工作區`。實跑：第 17 條 b、c、d 的 skill 版（共 3 次）都在這一步停下；noskill 版（b、c、d）也都先被擋，靠改寫到 session 暫存夾繞過去。理由句還是 Z7 的「這是知識庫的設定與狀態檔…」，AI 只能請客戶找服務人員。建議：`in_workspace` 對「根不存在」的路徑用字串正規化後的前綴判斷（不靠 `exists`）；或 `office check`／`setup` 順手建工作區夾（但 `check` 規格寫「只讀」）；SKILL 裡也要寫一句「工作區不存在就先 `kb office copy`／…」，確認時再看。

### 中
- **M-1 `output link` 不進回合帳**（`_lib/output.py` `link`／`inventory.register_link`）：沒有 `turn.backup`，manifest 沒有任何一列。重現（`produce-test\b`，每句不同 session）：A 回合 `output save` 一個 xlsx；B 回合 `output link https://claude.ai/artifact/bbb --title …`；C 回合 `versions list` 只有 A（沒有 B），`versions revert` → `已退回 …（問：「A 存檔」）：成品 1 份留著`——**退到了 A，B 的連結還在、status active**。客戶說「退回」想拿掉的是剛登記的線上文件，卻退掉前一個成品。工作日誌卻記了 inventory modify。（spec 對 link 的退回沒明寫，但驗收 9 與「成功句帶說退回」的精神是退得掉。）
- **M-2 同網址再 link 會覆蓋**（`register_link`）：第一次 `--title 內湖李進度報告 --type 報價 --entity 內湖李 --basis f-… --basis f-…`，第二次同網址 `--title 內湖李進度報告 v2`（沒帶其他欄）→ 同一筆被改成 title v2、`type: null`、`entities: []`、`basis: []`，也沒備份。正常流程「草稿定稿後 `output link`、再 `output save --link`」不會撞，但 AI 重打一次指令（逾時重試）就會洗掉。建議：同網址已存在時只補沒給的欄、或拒絕。
- **M-3 `recalc` 漏報錯誤格**（`_lib/office.py:589` `_CELL = <c\b([^>]*)>(.*?)</c>`）：`<c r="A1" s="1"/>` 的 `[^>]*` 把結尾 `/` 吃掉，body 一路吞到下一格的 `</c>`。重現：`formula_report` 對 `<c r="A1" s="1"/><c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c><c r="C1"><f>1+1</f><v>2</v></c>` 回 `(2, [])`（應報 B1 的 `#DIV/0!`）；去掉前面的自閉合格就對。LibreOffice、openpyxl 存的檔常有這種有格式的空格。這是給「數字核對」用的輸出，漏報很傷。
- **M-4 (b) 沒有推薦線上文件**：見第 17 條 (b)。draft.md 第 35 行「要推薦線上文件的，這一句就一起講清楚存在哪、誰看得到、要檔怎麼拿」是條件句，AI 判斷「給業主」→ 做 Word。spec 例句 8 與 D-165 寫的是「給業主看＝推薦 Docs」。另外客戶說「做成線上文件」後 AI 沒先講三件事就叫 Docs 建（make.md 18 行「她對內容與做成線上文件都說好了，才建」，AI 把「做成線上文件」當成同意）。建議：SKILL 原則或 draft.md 把「看與分享、給業主」的預設寫死成推薦 Docs，且三件事一定講在建之前。
- **M-5 修訂作者是「Claude」**：(a) 兩次都是 `w:author="Claude"`。make.md 42 行寫了「作者固定『AI 助理』」，但沒給 XML 範例，模型自己填。建議在 make.md 給一行範例 `<w:ins w:id="…" w:author="AI 助理" w:date="…">`。
- **M-6 `office pdf` 轉壞檔報成功**（`_lib/office.py:526-552`）：成功只判 `code == 0 and out.is_file()`，沒有先清掉同主檔名的舊 PDF。重現（`produce-test\a`）：工作區已有 `壞.pdf`（舊內容），`壞.docx` 內容是 `not a docx` → `office pdf` 回 `PDF：…\壞.pdf（用 LibreOffice 轉的）` exit 0，`壞.pdf` 還是 12034 位元組的舊檔。同回合改了 docx 再轉、或 `報價.docx` 與 `報價.xlsx` 共用 `報價.pdf` 時，`render` 會把舊版面轉成圖給 AI 看。

### 低
- **L-1 長路徑時 LibreOffice 閃退、訊息是「沒有訊息」**：LibreOffice profile 目錄路徑 145 字元可、152 字元以上 `--convert-to` 閃退 exit 0xC0000409（`produce-test` 實驗，只改 profile 位置）。實際客戶的知識庫路徑通常不到 100 字元，但 OneDrive 加中文夾名可能到。建議：偵測 exit code 非 0 且無輸出時，訊息說「LibreOffice 沒有執行成功」，或把 profile 放短路徑（例如 `%TEMP%\wiki-with-willy\lo`）。
- **L-2 `setup`／`check` 把空套件夾當裝好**：`pylib\defusedxml` 等四個夾、`node_modules\docx` 兩個夾只要在，`office check` 就說「裝好了」、`setup` 說「工具已經裝好了」，不驗 import。`setup` 驗 import 失敗後（叫客戶過一兩分鐘再跑）下一次跑會直接說裝好了，永遠不再驗。pip 中途失敗也一樣。
- **L-3 `output save` 的來源「工作區」是整個 `.system/temp/`**：`kb convert` 抽出的客戶原檔文字（`.system/temp/f-….md`）可以被「搬」（刪原處）進 AI 產出。實測：`.system/temp/converted.txt` 被搬走。影響低（`run` 同樣放行 `.system/temp/`），但與 spec 裁定 2「工作區」略有出入。
- **L-4 `output link --title` 收 `$(whoami)`**：標題存進清冊、之後出現在卡的 `標題:`。其他地方（實體名稱、型別名）都擋 `$` 與反引號（I-106 同類），這裡沒擋。
- **L-5 有 node 沒 npm 的說法**：`check` 寫 `Node.js：有（v…）；沒有 npm`，卻在「不行」裡寫「新做 Word、新做簡報（要裝 Node.js）」；`setup` 的 `skipped_node` 也印「這台沒有 Node.js」。
- **L-6 「一律擋」的洞（舊 I-031 類，沒有變 allow）**：`bash -c "cat 'AI 產出/x.md'"`、PowerShell `-Command "Get-Content 'AI 產出\x.md'"`、`grep -r 報價 .`／`find . -name '*.md'`／`Get-ChildItem -Recurse`（cwd 在知識庫根）、`cat $HOME/…/AI\ 產出/x`、`ls AI?產出` 在 `kb guard decide` 都回 `pass`（不表態＝交預設權限，客戶端沒放行 Bash 就會問客戶），不是 allow；`python -c "…open('AI 產出/x.md')…"` 回 `deny unparsed`。不是新增的放行，但 spec 寫「一律擋」。security-review 另列：`kb office unpack --to` 可寫進 `_lo_profile`（我實跑成功）、UNC 用真實主機名不折疊、工作區內懸空連結（需要先有連結；屬 B-401 之後的風險）。
- **L-7 回答貼內部編號**：第 17 條 (a) 第一次（有兩份矛盾合約）`有` 的回答貼了〔檔案卡 f-07df81a8〕；(c) `無` 貼了 `p-e5e22942`。skill 版的其他 run 沒有。與 B-099（answer 同類）一起看。
- **L-8 守門新理由句只用在 Z3**：Z8（總資料夾第一層）等寫入拒絕句沒變，與 spec 一致，不算問題；只是 Z7 的句子在工作區問題（H-1）時誤導。已併在 H-1。
- **L-9 唯讀原檔的副本也是唯讀**（`office_cli` `shutil.copy2` 保留屬性）：原檔有唯讀屬性（從信件或下載來的常見）→ `office copy` 的副本也是 `R`；對它跑 `office recalc` → 卡 60 秒後 `重算超過 60 秒，停掉了`（訊息不是真因；逾時後沒有殘留的 LibreOffice 行程）；`clean()` 的 `rmtree` 在 Windows 刪不掉唯讀檔，副本會一直留在 `.system/temp/office/`。建議複製後清掉唯讀屬性。（code-review #7，我實跑成立。）

## 子代理疑點的處理

- code-review（high）13 條：我重現 H-1（#1）、M-3（#2）、L-2（#3）、M-6（#6）、M-1（#8）；#4（`save` 中途失敗留半完成）、#5（逾時只殺 PowerShell 子樹、Office 行程留著）、#7（唯讀屬性已重現，見 L-9；長路徑沒測）、#9（範本底卡不 active 時無關編輯被拒）、#10（LibreOffice 鎖檔）、#12（新建 `AI 產出/<型別>/` 沒記資料夾帳：實跑確認真的沒有 `folders.jsonl`，但 `versions delete-outputs` 之後空夾也被收掉，沒有後果）、#13（`pack` 殘留 `.tmp`、zip bomb）只是讀碼推論，沒重現（#5 需要 Office，這台沒有）；#11 見 L-5。
- security-review 12 條：S1（`\\?\` 前綴讓 `output save` 的知識庫內判斷失效）我實跑**不成立**：`\\?\C:\…` 傳進去會被路徑解析弄成 `C:\?\C:\…` 而 `找不到這個檔`（fail-closed）。S2（`output save` 把知識庫外任意檔免確認複製進來）實跑成立（`outside_note.txt` 複製成功）；設計內，來源檔名像密碼的會擋（`id_rsa`、`網銀密碼.txt` 實測擋），內容不掃；建議設計 session 決定要不要接受（B-401 同類）。S3、S4、S6、S7 見 L-6；S9b 見 L-6；S12 見 L-3；S8、S10 沒測。

## plugin-dev 審查

- 依據：`plugin-dev:skill-development`（通用準則：描述第三人稱＋觸發片語、本文 1500～2000 字、imperative、progressive disclosure、references 存在）。本專案的約定（繁中、描述 ≤150 字、本文 ≤3000 字、skill 之間靠描述與「不是這裡的話」分界、不使用英文「This skill should be used when」句式）優先，這幾點不算缺點。
- `plugin-dev:skill-reviewer` 看 `skills/produce/` 與 answer／note 改的幾句：結構、引用、指令參數（對 argparse）、拒絕句（對 `_lib/output.py` 常數）、守門例外、skill 分界都通過。
  - 必修 4：(1) 描述少「草稿可以了，定稿」「寫一封信」這類跨對話的觸發詞（146 字，只差幾字，建議換掉「做一份提案簡報」）；(2) `answer/SKILL.md` 第 30 行「做成文件、表格、簡報」的「表格」會把「兩案木作比一比，做成表格」（要回答裡的 md 表格）導去 produce，建議改「表格檔」或「存成檔」；(3) produce 用 `kb tally` 但沒給 `--row "<f-…> <數字> [位置]"` 的格式，answer 的 `verify.md` 不一定載入（17 (c) 的 AI 先 `tally --help` 才會用）；(4) `make.md` 第 20 行「由我把匯出的 docx 寫進工作區」——docx 是二進位，檔案工具 Write 寫不了，要走腳本或改成請她自己匯出。
  - 建議 7：範本第七節 produce 沒說怎麼讀（`profile show --json`）、note 規則清單沒列「做文件用的範本」（客戶說「以後報價單照這份做」沒路）；note 第 24 行「給人看的」太含糊；「都用這個」與外部 pdf／docx／xlsx 技能重疊（桌面檔不在登記夾時 `office copy` 會拒絕）；make.md 拒絕句清單少 `只收工作區或 AI 產出裡的檔`；PowerPoint 三級講了三次；幾個無法驗證的數字（「約一分鐘」「20 KB」「預設只有她看得到」）；草稿用 Write 寫的例外在 SKILL 原則沒明講。
  - 我再補三點（來自第 17 條實測）：make.md 要寫「`kb office check` 不建工作區，工作區不存在時先……」（H-1 修好前）；修訂作者範例（M-5）；改完 Word 怎麼驗修訂數（`unpack` 後 Grep `w:ins`，不能用 `python -c`）。
- `plugin-dev:plugin-validator` 對整個 repo：**PASS，0 重大、4 輕微**（也跑了全套測試 1854 OK）。輕微：(1) `office.py:39-40` `SOFFICE_PLACES` 寫了 LibreOffice 的系統安裝路徑字面值（只是探測，不寫進狀態檔，不違反 D-019；可選改用 `ProgramFiles` 環境變數）；(2) CHANGELOG／版號還在 0.16.0（符合「發行才 bump」）；(3) README 第 149 行狀態段有兩句近乎重複的 note 句、也沒寫 `kb office`／`kb output`（既有）；(4) 它沒深看 make.md。結構、manifest、五個 hook 與 timeout、五個 skill 的 frontmatter／references、config 兩個新鍵的 `_why_`、命名一致、無寫死使用者路徑，都通過。
- 我自己補的結構檢查：`plugin.json` 版號 0.16.0；`hooks.json` timeout 10／5／10／10／10；五個 skill 描述 133／140／145／140／146 字（皆 ≤150）；`skills/produce/` 引用的兩份 references 存在。
- `claude plugin validate`：通過（icon、README 安裝行兩則既有警告，沒有新警告）。

## 建議的處理順序
1. H-1（守門 `in_workspace` 或 `office check` 建夾）＋ SKILL／make.md 補一句；修完用第 17 條 (b)(c)(d) 在新庫重跑。
2. M-1、M-2（`output link` 進回合帳、同網址不覆蓋）、M-3（`_CELL` regex）、M-6（轉檔前清舊 PDF）。
3. M-4、M-5 與 skill-reviewer 的必修 1～4（改 SKILL／make.md／answer 一句，要新舊版對照）。
4. 低項併入 backlog（L-1 長路徑、L-2 驗 import、L-6 併 B-401「密碼與守門」）。
