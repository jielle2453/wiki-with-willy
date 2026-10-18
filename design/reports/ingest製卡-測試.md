# ingest 製卡 測試報告

日期：2026-10-18
給誰看：設計 session（llm-wiki-j-30）與你。
什麼時候更新：重測時另寫一段，不改舊段。

對象：worktree `C:\Users\Jie\Desktop\llm-wiki-j-ingest製卡`，分支 `spec/ingest製卡`，commit e87c248。spec `design/specs/2026-10-17-ingest製卡.md`，含「實作疑義裁定」1～8，裁定優先於正文。測試 session 沒改任何程式。

## 結論

- 驗收準則 21 條：**20 條過；16 的路由 eval 待判**——ingest 該觸發 90%（剛好過線）、不該 0%；manage 該觸發 0/30 異常，已重跑（見「驗收 16 路由」）。
- 但 code-review 與 security-review 找到要先處理的問題，**建議不要直接進確認**：
  - **高 2 項**：S1 `kb convert --vault` 指向假總資料夾，就能繞過排除與憑證（裁定 8 的守門被換掉信任根），還能接著寫穿或刪檔（S2、S3）；C1 原檔被 Office 開著（檔被鎖住）時 `kb convert` 丟 traceback、exit 1。
  - **中 5 項**：來源搬家後 `cards sync` 修不了開原檔；待處理「先留著／先跳過」之後又問一次（設計缺口）；`編號: [a]` 讓 PostToolUse 漏驗；同編號兩張卡收斂不了（B-051 之外多一種卡死）；ingest SKILL.md 有三處流程寫法會讓 AI 繞圈或誤答。
- 全部測試 `python -m unittest discover tests`：918 個，exit 0（skipped 1）。

## 驗收準則

做法：除了看實作 session 的單元測試，我另寫一支驗收腳本（`<scratchpad>\accept.py`，不在 repo），每條都從 `tests/fixtures/inventory` 複製出新的 `<V>`，用 CLI 子程序跑 `python "<ROOT>/scripts/kb.py" …`，檔案用 `tests/_ooxml.py` 組。

腳本觀察（不是本分支的問題）：終端機跑的 kb 指令都用 `turn.current`，會接進目前那個回合。tmp 從頭到尾只有一個回合時，`versions revert` 會連第一次 scan 建的清冊一起退掉。這是既有的回合語意（`inventory_cli`、`folders` 在 main 上也一樣）。實際對話每句話開新回合，所以 8、12 我在每一步前先 `turn.start` 一個新回合，比照真實情境。

| # | 結果 | 證據 |
|---|---|---|
| 1 | 過 | docx 抽出 `第一段／# 大標／甲 \| 乙／1 \| 2／第二段`；xlsx 兩個 `## 工作表「…」`、共用字串與 inlineStr 都在、`360000.0`→`360000`、公式快取值 `720000`、`=SUM(A1:A3)（無快取值）`、每行 `第 N 列：`（裁定 1）；pptx `## 投影片 1／2`、`（備忘稿）備註內容`。同三份檔用 main 的 `content_fp.text_fingerprint` 與分支版算，結果相同；`tests.test_lib_content_fp` 綠 |
| 2 | 過 | utf-8-sig 去 BOM；cp950 檔回 `cp950` 且內容逐字相同；解不了的位元組丟 `Unreadable`，沒有 `\ufffd` |
| 3 | 過 | `.doc`+CFB→`legacy_office`、`.docx`+CFB→`encrypted`、含 `/Encrypt` 的 PDF→`encrypted`、`.zip`→`unsupported`、`.PNG`→`image` |
| 4 | 過 | 會議紀錄_1.docx 用編號、uri、路徑各跑一次：第一行 `暫存檔：…`，暫存檔 UTF-8、無 CRLF，stdout 沒有文件文字；報價_v2.pdf 印 `PDF：…用 Read 讀原檔：<絕對路徑>`，`.system/temp/` 沒新檔；網銀密碼.txt exit 3 `讀不到：檔名像密碼或金鑰，預設不讀`、不寫待處理；x.doc（CFB）exit 3 `讀不到：舊格式（.doc），請另存成新格式`，第二行 `已記進待處理（p-…）`，pending 一條 unreadable、md 有「讀不到（1）」，再跑一次不多一條。**裁定 8**：排除夾（unused 與 hidden 都試）裡的檔，用路徑、編號、uri、大寫路徑、`..` 繞路寫法都 exit 3「這個位置你說過不讀」；不在清冊的 `id_rsa` →「檔名像密碼或金鑰」；總資料夾外 →「不在登記的資料夾裡」；`.system/inventory.json` →「這不是要製卡的檔」；四種都沒有新暫存、沒有新待處理。（但見 S1：換 `--vault` 就繞得過） |
| 5 | 過 | 舊暫存與 `old.md` 的 mtime 設到一小時前，`.system/config.json` 設 `temp_keep_hours: 0` 後跑一次 convert（PDF），兩檔都被清掉 |
| 6 | 過 | `new` 12 份 = 清冊 active 且 read ok 的條目數；每項有 `dir`、`filename_pattern`、`header`；header 解析後 `編號`／`出處`／`原檔`／`指紋` 等於清冊、`標題` 是 `（填）`；`skipped.credential` 1；`--limit 2` 人讀只列 2 行加 `……還有 10 份`；跑前後 `.system/`（temp 除外）每個檔 mtime 不變、`versions/` 沒新回合。裁定 6：第二行 `不製卡：檔名像密碼或金鑰 1` |
| 7 | 過 | 照 header 寫「大安陳會議一-<八碼>.md」後 `new` 少一份、stale／moved／broken 空；只改 docx 的 `docProps/core.xml` 再 scan，不進 stale；改內容再 scan，該卡進 stale |
| 8 | 過 | 檔搬到信義王夾、scan → `moved` 一項、`to` 是 `檔案索引/客戶案件/信義王`；sync 後卡在新夾、檔名不變、`原檔` 是新 uri、`開原檔` 等於 `open_link(新路徑)`、其他行不變；該回合 manifest 有舊路徑 delete、新路徑 create；當月 jsonl 多一段 program `cards sync`；再跑 todo `moved` 空。同夾改名：`moved` 一項且 `to` 等於原夾，sync 後卡路徑不變、`原檔` 是新名，manifest 的卡只有一筆 modify |
| 9 | 過 | 手放 `孤兒-00000000.md` → 在 `orphans`；合法卡 `出處` 改「別的」→ `broken` 一項、reason 含「出處該是」；人讀第一行 `待製卡 11 份…`（兩張都不算）並多印「檔頭有問題 1 張」 |
| 10 | 過 | 用 `test_hooks_output_log.run_hook` 餵 PostToolUse：合法卡無輸出、exit 0，`總目錄.md` 存在且列了它；指紋改一碼、檔名八碼不同、放錯夾、`開原檔` 改一字、`標題: （填）`、少 `---`，各回一行 JSON `decision: block`，reason 分別含「指紋不是現行的」「檔名該是」「卡該放在」「開原檔那行」「標題」「檔頭解析不了」；exit 一律 0。AI 產出照舊登記：用 `write_via_tools`（先過 PreToolUse 備份）寫 `AI 產出/新草稿.md`，無輸出、清冊有它。（只跑 PostToolUse 不跑 PreToolUse 時不登記，main 也一樣，屬既有規則） |
| 11 | 過 | 刪 raw 檔、scan → pending 一條 missing_file open，md 有「原檔不見（1）」與固定問法；這段 program 的 changed 有 `.system/pending.json` 與 `系統/待處理.md`，前一次沒建條目的 scan 則沒有；todo `missing` 一項帶 `pending_id` 與 `question`；再 scan 不多開；原位元組放回去再 scan → 條目 done、how「找回來了」、md 不列。pending.json 寫壞再 scan：exit 0、清冊照寫成（條目 missing）、hook-errors.log 多一行 |
| 12 | 過 | active 條目 drop → exit 3 `原檔還在：…`；寫卡、刪原檔、scan，在新回合 drop → exit 0 印「已拿掉「大安陳會議一」的卡；說「退回上一版」救得回來」，卡不在、manifest 有 delete、清冊 deleted、待處理 done 且 how 含「說刪了」、md 不列；`versions revert` 後卡回來、清冊 missing、待處理 open、md 又列 |
| 13 | 過 | `list` 列出兩條 open；`resolve <id> --how "先留著"` → done、how「先留著」、md 不列、印「已記下」、終端機多一段程式段；`dismiss` → dismissed；不存在的 id，resolve 與 dismiss 都 exit 3 |
| 14 | 過 | x.doc 換成同名不同內容（仍 CFB）、scan → 原條目 done、how「檔案換了，重新讀」；todo 把它列回 `new`、不在 `unreadable`；再 convert 仍是舊格式，開新的一條 |
| 15 | 過 | 照 `test_hooks_session` 餵 SessionStart：無變動時沒有「有變動：」行；新增一檔後「變動：新增 1、修改 0、不見 0」下一行正是 `有變動：先回答客戶這句，再照 ingest skill 整理；超過 5 份先問。`（狀態段 3 行 151 字）；`ingest_auto_max` 設 3 後那行是「超過 3 份」；`test_budget` 綠 |
| 16 | 待判（單元測試過、eval 見下節） | `test_skill_ingest`、`test_budget`、`test_skill_manage` 綠（description 140 字、本文 62 行、carding 74 行、commands 82 行）。路由 eval 見下面「驗收 16 路由」 |
| 17 | 過 | `git diff main...HEAD -- skills/manage/SKILL.md` 只改「加資料夾」那一列（收尾句換成「從現在起讀得到；要我現在整理成卡嗎？」客戶說要就照 ingest skill 做）；`tests/evals/manage.json` 無 diff；`test_skill_manage` 綠 |
| 18 | 過 | `claude plugin validate` exit 0「Validation passed with warnings」（唯一 warning 是根目錄 CLAUDE.md 不會被當 plugin context 載入，這是給開發者的檔，預期內）；agent `plugin-dev:plugin-validator` critical 0；agent `plugin-dev:skill-reviewer` 看過 `skills/ingest/`（意見見「plugin-dev 審查」） |
| 19 | 過 | `python -m unittest discover tests`：Ran 918、OK (skipped=1)、exit 0；`git diff main...HEAD -- tests/test_budget.py` 空 |
| 20 | 過 | 乾淨 `<V>` 跑 `cards index` → 印「總目錄：0 張卡」，檔頭四行對、有「還沒有卡；加資料夾後說「整理」就會有。」、最後一行「共 0 張卡」；兩個來源寫三張卡（客戶案件/大安陳 兩張、報價與合約 一張）後：第一層別名照名稱排、`客戶案件 > 大安陳 > 卡` 縮排對、同層夾在前卡在後、三條連結 unquote 後都解析到檔、標題「大安陳 會議 (一)」編成 `%20`／`%28`／`%29`、「草稿（修）」全形括號不編（裁定 3）、「共 3 張卡」、終端機多一段 `cards index`；drop 一張後少一項、「共 2 張卡」；把一張卡寫成沒有檔頭後 index 仍 exit 0、該項標題是檔名 |
| 21 | 過 | repo `CLAUDE.md` 地圖加了 extract、cards、pending、convert、cards CLI、pending_cli、skills/ingest、templates、fixtures/cards；README 加「整理成卡」一段（含 PDF 由 AI 用 Read 讀、20 頁上限）與 kb 指令；`templates/card.md` 檔頭的鍵、順序與 `carding.md` 第二節的表、`cards.KEYS` 三者一致（編號 出處 原檔 指紋 內容指紋 標題 卡型 文件日期 讀取 製卡日期 範本版本 敏感資料種類 開原檔），值域一致。小差異：carding.md 說「檔頭之後空一行」，範本 `---` 與 `# <標題>` 之間沒空行 |

### 驗收 16 路由

前置：`claude plugin list` 確認 `llm-wiki@llm-wiki` ✔ enabled（設計 session 通知後；開始時是 disabled）。做法照 B-036：`select.select` 換成永遠就緒的 shim（放 scratchpad，skill-creator 原檔不動）、`PYTHONUTF8=1`、`--num-workers 1`、`--runs-per-query 3`，cwd 在 worktree，ingest、manage 各跑一次；`query failed` 0 次，exit 0；跑完 `.claude/commands/` 沒有殘留檔。

**ingest.json（描述 140 字）：不過門檻邊緣——該觸發 27/30 = 90%（門檻 ≥ 90%，剛好），不該觸發 0/30 = 0%（門檻 ≤ 10%）。** 20 句中 19 句過。唯一失敗：「資料夾裡的東西都整理好了嗎？還有沒做的嗎」0/3（SKILL-reviewer 也指出這句描述沒涵蓋，建議描述加「整理好了嗎」；描述只剩 10 字餘裕）。其餘 9 句該觸發都是 3/3，10 句不該觸發都是 0/3（含 manage 句、answer 句、note 句、produce 句、「這是合約內容，存起來」）。

**manage.json（描述 145 字，沒改）：該觸發 0/30 = 0%、不該觸發 0/30 = 0%——該觸發全數落空，20 句只過 10 句（不該觸發那 10 句）。** 這跟 manage 測試報告的 30/30 完全相反，而 ingest 的同一輪卻正常，所以我不把它當成路由退步的結論，先當作需要重跑的異常：manage 這輪只花約 3 分鐘（manage 報告是 7 分鐘），可能是模型呼叫或 timeout 的問題，也可能是 ingest 加入後兩個 skill 互搶（但 ingest 的 manage 句在 ingest eval 裡是 0/3，沒有搶走的證據）。已在 f67b538 上重跑，結果寫在「第二輪」一節。第一輪這項**判不過，等重跑**。

## code-review（high）

做法：派 agent 人工逐檔看完 spec 與 39 檔 diff（code-review skill 只看 cwd 的 diff，指不到 worktree），每條都在 `git archive` 匯出的複本上實跑。C1、C2 我在 worktree 上親自重現。

| # | 嚴重度 | 位置 | 問題與情境 | 驗證 |
|---|---|---|---|---|
| C1 | 高 | `scripts/kb/convert.py:199-236`、`_lib/extract.py:151`、`:358-360` | 原檔被鎖住時 `kb convert` 丟 traceback、exit 1。`run` 只接 `Refused`／`ValueError`，`_open`、`_head`、`plain_text`、算暫存檔名的指紋、`write_text_atomic` 的 `OSError` 都沒接。客戶最常見的情況是 Excel／Word 正開著那份檔：製卡者拿不到 commands.md 表上任何一句原因，也沒記進待處理；commands.md 說 exit 只有 0 或 3 | 親自重現：`msvcrt.locking` 鎖住 docx 後 convert → rc 1、stdout 空、stderr `PermissionError: [Errno 13]` |
| C2 | 中 | `_lib/cards.py:319-324`、`scripts/kb/cards.py:106` | 原檔路徑沒變、只有開原檔連結對不上時，sync 修不了。來源整夾搬家（`sources.json` 的 spec 跟著改）後，卡進 `broken`（「開原檔那行照 kb cards todo 印的抄」），sync 只處理 `moved`，印「搬了 0 張卡、更新 0 張卡」。這跟 spec 第 29 行不合：spec 寫明來源搬家由 sync 重寫開原檔，B-050 改連結形式後也要靠 sync 整批重寫，照現在的分桶會讓所有卡變 broken 而 sync 一張都不碰。同一個總資料夾經 OneDrive 在兩台使用者名稱不同的電腦上用也會整批 broken | 親自重現 |
| C3 | 中 | `_lib/cards.py:183-199` | 同一編號兩張卡時 todo／sync 收斂不了（B-051 已記）。另外多一種卡死：卡放錯夾後在對的夾重寫，todo 把錯夾那張列成 moved，sync 因目的地已有同名檔每次跳過，SKILL 第 4 步「sync 完再跑 todo」永遠看到同一筆 moved。錯名那種情形則是錯名卡排前面被當正本，叫製卡者寫回錯名路徑，永遠過不了 | agent 實跑 |
| C4 | 中低 | `convert.py:173`、`extract.py:269` | xlsx 抽到上限前就停，`chars` 常常不超過 `card_source_big_chars`，所以不印「大檔」、`--json` 的 `big` 是 false，只有暫存檔最後一行有「（只抽到…）」。製卡者容易把讀取標成「完整」 | agent 實跑（每列 300 字，上限 1000～6000，字數 957／5880，big False） |
| C5 | 中低 | `_lib/cards.py:391-426` | `render_index` 沒上鎖。一則訊息平行寫多張卡時，各 PostToolUse 是不同 process，先走訪的那支可能後寫，蓋掉含新卡的版本。下次寫卡或 `cards index` 會修好 | plugin-dev 審查 agent 看程式 |
| C6 | 低中 | `extract.py:171-183` | docx 文字方塊（`w:txbxContent`）的巢狀段落文字重複：`Outer`+`Inner` 抽成 `OuterInnerInner`；`w:br` 被忽略，「A↵B」抽成 `AB`。只影響暫存文字，內容指紋不走這段 | agent 實跑 |
| C7 | 低中 | `_lib/cards.py:292-297` | `cards todo` 對每份 new／stale 跑 `extract.probe`，PDF 每份讀前 2 MB；上千份 PDF 時每次 todo 讀好幾 GB，已被 OneDrive 釋放成占位檔的會被觸發下載（I-020） | 看程式 |
| C8 | 低 | `inventory.reconcile`、`_sync_pending`、`cards.py:339` | 待處理留下關不掉的 open 條目：folders exclude／remove 後已 open 的 missing_file 永遠 open（todo 也不列）；內容拆出新條目時舊編號的 unreadable 不被 auto_done；占位檔下載回來大小與時間常不變，不算 modified，開著的「只在雲端」條目擋住它進 new | 看程式 |
| C9 | 低 | `_lib/cards.py` todo | `read == "error"` 的條目哪一桶都不出現，客戶與 AI 都看不到（合 spec 字面，是盲點） | 看程式 |
| C10 | 低 | 多處 | 雜項：convert 的 `--json` 走 `ValueError` 那條印的不是 JSON（`convert.py:229-231`）；用路徑指定、對到 missing 條目但檔已放回（還沒 scan）時回「找不到原檔」；路徑沒經 `paths._text_forms`，Git Bash `/c/…`、`file:///C:/…` 可能被當成 uri 或錯路徑；`cards drop` 遇鎖逾時時卡已刪、清冊已改卻印「沒有寫入」、沒記程式段（`cards.py:175-179`）；沒有回合時 sync／drop／pending 會先開一個空回合；xlsx 第一列就截斷時印「第 0 列」 | 看程式 |

看過沒問題的：content_fp 共用函式原樣搬、對外行為不變；D-022 各路徑不印內容；待處理失敗只記 log、在清冊鎖外；裁定 1～8 都照做（8 的限制見 S1）；sync、drop、index、hook 總目錄、pending 都先備份再原子寫，sync 先寫新卡再刪舊卡；hook 全段包 BaseException、一定 exit 0；沒動到不可動的檔，`design/` 只在 backlog 新增 B-051 一行。

## security-review

做法：派 agent 在 scratchpad 的 fixture 複本上實跑各種繞路寫法。S1、S4 我親自重現，並用 `kb guard decide` 確認守門判斷。

| # | 嚴重度 | 位置 | 問題與重現 | 驗證 |
|---|---|---|---|---|
| S1 | 高 | `scripts/kb/convert.py:200`（`find_vault(args)` 收 `--vault`）、`:98-99` | **`kb convert --vault <假總資料夾>` 繞過排除與憑證**。守門對單一 kb.py 呼叫一律 allow，不看 `--vault` 指向哪；任何有 `.system/sources.json` 的夾都算總資料夾，底下的檔都歸 Z2，Z2 不擋排除也不查憑證（D-081 內部豁免）。準備動作只要在來源的上層寫一份 `{"schema_version":1,"sources":[]}`，那個位置是 Z11，守門回 `pass`（交給一般權限，acceptEdits／auto 模式不會問）。重現：大安陳夾 hidden 排除後，`kb convert <raw>\客戶案件\大安陳\[草稿]…txt` → exit 3「你說過不讀」；同指令加 `--vault <raw 的上層>` → exit 0 `暫存檔：…`；網銀密碼.txt 加 `--vault` 也 exit 0 抽成文字。`kb guard decide --tool Bash --command "…convert … --vault …"` → `allow 知識庫腳本`；`--tool Write --path C:\Users\Jie\Desktop\x\.system\sources.json` → `pass` | 親自重現 |
| S2 | 高（要先有 S1） | `convert.py:119-121`、`:170-171` | 暫存檔名用清冊 id 組，沒驗格式，可以路徑穿越。只有直接打的編號經 `_ID` 正規式，用路徑或 uri 反查到的條目 id 原樣當檔名，而假總資料夾的 `inventory.json` 由攻擊者自己寫。重現：假清冊一筆 `id = "../../vault/系統/待處理"`，convert 該檔 → `暫存檔：<T>\vault\系統\待處理.md`，真總資料夾的 `系統/待處理.md` 被蓋掉。能蓋 Z6／Z7（客戶端 CLAUDE.md）、來源裡的 `.md`、plugin 的 `rules/core.md`，不經回合備份，等於能種常駐的 prompt injection | agent 實跑 |
| S3 | 高（要先有 S1） | `convert.py:54-66` | `clean_temp` 不查 `.system/temp` 是不是 junction，`temp_keep_hours` 又可被假總資料夾的 config 覆寫成 0；清理在 gate 之前，所以就算 convert 結果是拒絕也會清。重現：假總資料夾的 `.system/temp` 做成 junction 指到 `raw\公司行政\舊檔`，跑一次 convert → 目標夾從 `['Big5備忘.txt']` 變成 `[]`，客戶原檔被刪且沒有備份 | agent 實跑 |
| S4 | 中 | `_lib/cards.py:215-216` | 檔頭寫 `編號: [a]` 時 `entries_by_id.get(cid)` 丟 `TypeError`，PostToolUse 吞掉、不印 block，壞卡被靜默接受 | 親自重現：hook exit 0、stdout 空，hook-errors.log `TypeError: cannot use 'list' as a dict key` |
| S5 | 低 | `_lib/cards.py:43-47` | `parse_front` 用遞迴解析巢狀 `[`，一行 5000 個 `[` 加 5000 個 `]` 讓 `cards todo`／`index` exit 1 印 `RecursionError` traceback（`scan_cards`、`render_index` 只接 ValueError／OSError／UnicodeDecodeError） | agent 實跑 |
| S6 | 低 | `_lib/cards.py:415`、`:387-388`、`:419` | 總目錄 markdown 注入：夾名原樣輸出，夾叫 `[x](javascript%3Aalert(1)) #h` 時總目錄出現可點連結；`_link_text` 不跳脫反斜線，標題 `a\` 讓連結斷掉；標題裡的 HTML 原樣輸出。Windows 檔名不能有 `: / \ < >`，夾名注入不了絕對 URL；標題由 AI 寫、可能受文件內容影響。連結目標編碼照裁定 3 正確 | agent 實跑 |
| S7 | 低 | `extract.py` | 抽字沒有大小上限：`plain_text` 整檔讀、sharedStrings 不限、zip bomb 可吃光記憶體；gate 不擋 `read == "too_large"`（只看 credential、placeholder）；MemoryError 沒接 | 看程式 |

確實擋住的（cwd 在真總資料夾、不加 `--vault`，全部 exit 3、沒有暫存檔）：排除夾裡的檔用相對路徑 `../raw/…`、段尾加點或空白、Git Bash `/c/…`、大小寫不同、磁碟相對 `C:…`、`\\?\C:\…`、`\\.\C:\…`、`\\localhost\c$\…`、`\\?\UNC\…`、ADS `::$DATA`、uri 各種 `..`／`//`／ADS 寫法、來源內與總資料夾內指向排除夾的 junction；憑證檔 `.env`、`db_password.txt`、8.3 短名、ADS、長路徑前綴、憑證名的夾與它的短名和 junction；總資料夾外與 `file:///C:/…`、穿出總資料夾的 uri →「不在登記的資料夾裡」；`.system/inventory.json` 各寫法 →「這不是要製卡的檔」。PostToolUse 各種輸入都 exit 0、block 輸出是合法 JSON、非 Z3／Z5 寫入不擋。`open_link` 的 pathname2url 把空白、括號、換行都編碼，開原檔欄沒有注入空間。

修法方向（給設計 session 參考，不是裁定）：convert 不收 `--vault`，或要求它等於從 cwd 找到的總資料夾；`_temp_name` 先驗 `_ID.fullmatch`、寫前確認父夾是 temp；temp 夾是 reparse point 就不清；`problems()` 先查 `編號` 是字串。其他收 `--vault` 的子命令（cards、pending、folders、versions）有沒有同類問題，這次範圍外沒測。

## 設計缺口：待處理「先留著／先跳過」不會留住

兩位 skill-reviewer 都指出，我親自重現：

- 原檔不見的條目 `pending resolve --how "先留著"` 後，清冊條目仍是 missing，todo 的 `missing` 照列、`pending_id` 變 null；下次 `inventory scan` 的 `sync_missing` 看到沒有 open 的條目就再開一條（實測：p-0ff450c3 done 之後多一條 p-be805da0 open）。SKILL.md:41 還寫「`pending_id` 是 null 就先跑 `inventory scan` 再看」，等於主動重開。客戶每次開對話都被問一次。
- 讀不到的條目 `pending dismiss`（客戶說「先跳過」）後，條目 read 仍是 ok、沒有卡，todo 列回 `new`，下次整理 convert 又失敗、又開一條（實測：dismiss 後 x.doc 在 new，convert 再印 `已記進待處理（p-09a93d09）`）。確認情境第 8 步「加密那份先跳過」之後，下一次整理就會回來。

這兩點跟 spec 字面一致（`sync_missing` 寫的是「沒有 open 的就開」），所以不算實作偏離，要設計決定：todo／sync_missing 是否尊重已 done／dismissed 的條目。

## plugin-dev 審查

依據 `plugin-dev:hook-development` 與 `plugin-dev:skill-development`，審 `hooks/output_log.py`、`hooks/session.py`、`skills/ingest/`；另跑兩次 agent `plugin-dev:skill-reviewer`、一次 `plugin-dev:plugin-validator`。

- **hooks：符合規範。** PostToolUse 用 stdout 一行 `{"decision":"block","reason":…}` 加 exit 0，正是官方文件定義、會把 reason 回給 Claude 的格式（plugin-dev 的 skill 這段寫得比官方少，實作照官方）。stdin 以位元組讀再解 UTF-8、整段包 BaseException、一律 exit 0、`sys.path` 只在 hooks 裡動、`${CLAUDE_PLUGIN_ROOT}` 加引號、timeout 合理；hooks.json 只改 description。小點：`output_log.py` 用 `ensure_ascii=False`，跟 `session.py` 為 I-001 選的 True 不一致（可選）；`line_ingest_hint` 再叫一次 `line_changes`，出錯時 log 記兩次；比對沒做完（「來源很大還沒比完」）時下一行仍說「有變動」，spec 第 7 點明定要印，只是字頭可考慮中性一點。
- **plugin-validator：critical 0。** `claude plugin validate` exit 0，一個預期內的 warning（見驗收 18）。五支 hook 在總資料夾外與 fixture 複本裡煙霧測試都 exit 0、輸出格式正確、沒有 hook-errors。
- **skill：結構、預算、指令一致性都好；有四處流程寫法要改。** description 140 字、本文 62～63 行、兩份 reference 74／82 行；指令名稱、旗標、exit code、程式印的字跟 `convert.py`、`cards.py`、`pending_cli.py` 逐條對過一致；漸進揭露清楚（SKILL 管分辨與順序，carding 管怎麼寫卡，commands 管指令與轉述）。要改的：
  1. `SKILL.md:41`：`pending_id` 是 null 就跑 scan 會重開已答過的條目（見上一節）；skill 層可改成「null 的不問、不跑 scan」。
  2. `SKILL.md:37`、`:40`：被打回的原因是「檔名該是…」時，「同一個路徑整張重寫」與「照 reason 寫回 path」都會永遠過不了（carding.md:36 寫對了，SKILL 沒跟上）。
  3. `SKILL.md:23` 與 `:29` 衝突：「我改了」但 `kb changes` 查無變動就「不做別的事」，可是 `changes` 比的是上次 scan，不是卡；上次已入冊但延後製卡的檔在 `stale`／`new`，AI 卻會說「是不是還沒存檔」。建議查無變動也先跑 `cards todo --json`，該檔不在 new／stale 才那樣回。
  4. 路由重疊：description 有「檔案我搬了」，manage 把「搬到…了」當資料夾的事（manage:21、:33），兩邊都沒說怎麼分「來源根」與「來源裡的子夾／單一檔」。
  - 小點：`SKILL.md:32` 判斷上限的方法繞路（印了「……還有 N 份」就是超過）；`SKILL.md:59`「轉述第一行」跟 commands.md 表上「照下面說法／不用跟客戶說」衝突；commands.md 少 cards／pending 的共通拒絕句（狀態檔讀不到、等不到鎖、沒有寫入）；SKILL 與 carding 有幾段重複；carding.md:40「檔頭之後空一行」與範本不一致；description 沒有用 plugin-dev 的第三人稱句型、內文用「你」稱製卡者，這是專案中文慣例（manage 同），不算要修。

## 環境與清理

- 舊 `llm-wiki@llm-wiki`：開始時是 disabled，照指示停下 eval、通知設計 session；設計 session 回報已 enable 後（`claude plugin list` 確認 ✔ enabled）才跑 eval。我沒改 plugin 設定。
- 驗收腳本、eval shim、各 agent 的驗證腳本都在 scratchpad；tmp 知識庫在 `%TEMP%\kbinv-*`，不在 repo。worktree 除本報告外沒有改動。

## 第二輪（2026-10-18，對 f67b538：d43c860 程式、f67b538 文件）

範圍照裁定 33：只驗「測試後修正裁定」15～32，加無退步。裁定 34 在我這份 worktree 的 spec 裡不存在（spec 只到 33），所以沒驗。

### 結論

- 全部測試 `python -m unittest discover tests`：**exit 0**（918 個，skipped 1）。
- 修正項 18 條（15～32）：**17 條過，1 條有一個我自己加的附帶觀察（17）**，沒有不過的。
- 無退步：驗收 4／9／10／11／13 重跑全過。
- **路由 eval：ingest 過線；manage 沒過線（該觸發 83～87%，門檻 90%），需要設計 session 判斷。** 詳見最後一節。

### 修正項

做法同第一輪：自己寫的 `accept2.py`（scratchpad，不在 repo），每條從 fixture 複本建新知識庫、用 CLI 子程序跑；S1～S3 用第一輪的重現步驟。

| 裁定 | 結果 | 實跑證據 |
|---|---|---|
| 15 S1 | 過 | `kb convert <排除夾裡的檔／網銀密碼.txt> --vault <假總資料夾>` → exit ≠ 0、假總資料夾裡沒有 `.system/temp`；不加 `--vault` 照擋（exit 3「你說過不讀」）；cwd 在總資料夾外 → exit 3「這裡不是知識庫；到知識庫裡執行」 |
| 16 S2 | 過 | 把清冊裡一筆 id 改成 `../../vault/系統/待處理`，用路徑 convert → 暫存檔在 `.system/temp/` 底下、檔名是 12 碼指紋；真的 `系統/待處理.md` 位元組沒變 |
| 17 S3 | 過（附觀察） | `.system/temp` 做成 junction 指到 `victim\`，`temp_keep_hours: 0`、檔 mtime 兩小時前：convert 後 `victim\客戶檔.txt` **還在**，`hook-errors.log` 多「是連結，不清暫存」；拒絕（exit 3，網銀密碼）的呼叫不清舊暫存、成功的呼叫才清。**觀察（低，不影響裁定 17 的要求）**：temp 是 junction 時，成功的 convert 會把暫存檔寫進 junction 的目標夾（`victim\f-xxxx.md`）；裁定 16 的「父夾 realpath 等於 `.system/temp` 的 realpath」在這種情況下兩邊都解到目標夾，所以擋不住。能做成 junction 要先能寫 `.system/`（Z7 守門擋），第一輪的假總資料夾路線又已被 15 封掉，所以風險低；要補的話是寫之前也檢查 temp 不是重解析點。 |
| 18 C1 | 過 | `msvcrt.locking` 鎖住 docx：exit 3、第一行 `讀不到：檔案被別的程式開著或沒有權限，關掉 Word／Excel 再試`、無第二行、`pending.json` 沒新條目、stderr 沒 traceback；`--json` 被鎖與找不到原檔都印 JSON（帶 `unreadable`）；壞 zip 的 docx → `讀不到：檔案壞了…` |
| 19 C2 | 過 | 整個 raw 搬到 `raw2`、`sources.json` 跟著改、scan → `moved` 一項（`to` 同夾）、`broken` 空；`cards sync` 後 `開原檔` = 新路徑、manifest 一筆 modify、再 todo 全空；出處改錯的卡仍進 `broken`（「出處該是」） |
| 20 C3＋B-051 | 過 | 同編號三張（正本、`A錯名-…`、放錯夾同名）：`todo --json` 的 `duplicates` 一組 `{id, keep: 正本, drop: 2 張}`，人讀第一行含「同編號多張 1 組（跑 kb cards sync）」；`sync` 後只剩正本、兩張被刪的 manifest 都是先 `delete` 備份；再 todo 全空。兩張都不過時留修改時間最新的一張 |
| 21 S4 | 過 | `編號: [a]`、`標題: [x, y]`、`原檔` 寫成字串、`敏感資料種類` 寫成字串 → 各 block 且 reason 含「格式不對」；讀不了的位元組檔 → block；合法卡仍無輸出 |
| 22 S5 | 過 | 巢狀 `[` 丟 `ValueError`、一層清單照常；標題塞 5000 個 `[` 加 5000 個 `]`：`cards todo`、`cards index` 都 exit 0、沒有 traceback |
| 23 S6 | 過 | 總目錄：標題 `a\` → `[a\](…)`；標題 `[x]<a href=1>點</a>` → `[\[x\]＜a href=1＞點＜/a＞](…)`；夾名 `夾[1]` → `夾\[1\]` |
| 24 S7 | 過 | 清冊 `read=too_large` → exit 3 `讀不到：檔案太大`；`fingerprint_max_bytes` 設 100，500 字元的 txt → `檔案太大`；3 MB 解壓後的單一 docx 成員（壓縮檔只有 3 KB）超過上限 → `檔案太大` |
| 25 C4 | 過 | `card_source_big_chars` 1000、20 列每列 300 字的 xlsx：`--json` 的 `big` 為 true、人讀印 `大檔：…`、暫存檔尾保留「（只抽到工作表「總表」第 N 列）」；`carding.md` 有讀取標「部分」與沒讀到的說明 |
| 26 C5 | 過 | 程式 `with FileLock(Path(vault) / INDEX_POSIX)` 包住；六個 `cards index` 平行跑，全 exit 0、總目錄完整（共 4 張卡） |
| 27 C6 | 過 | 文字方塊（含 `mc:AlternateContent` 的 Choice／Fallback）的 `Inner` 只出現一次；`w:br` 換成換行（`A\nB`，不再黏成 `AB`）；無 mc 的巢狀段落也不重複 |
| 28 C7 | 過 | 加密 PDF 在 `todo` 的 `kind` 是 `pdf`（只看副檔名）；PDF 被 `msvcrt.locking` 鎖住 `todo` 照常列、不報錯；`convert` 才查 `/Encrypt` → `讀不到：有密碼` |
| 29 C9 | 過 | 清冊 `read=error` 的條目：`skipped.error` 為 1、不在 `new`，人讀第二行含「讀不到 1」 |
| 30 C10 | 過 | xlsx 第一列就截斷：暫存檔有「（只抽到…）」、沒有「第 0 列」；`cards drop` 在清冊鎖被別人佔著時：卡還在、`inventory.json` 位元組沒變、輸出含「等不到鎖」、exit ≠ 0；放開鎖後 drop 正常 |
| 31 設計缺口 | 過 | `missing`：resolve「先留著」後 `todo.missing` 不列，再 scan 兩次仍一條（done）；dismiss 的 `how` 是「客戶說不用管」，之後 scan 不重開、todo 不列；找回來再次不見 → 開新的一條 open。`unreadable`：dismiss 後 todo 不再把它列進 `new`；檔換了 → dismissed 也 `auto_done`「檔案換了，重新讀」、todo 列回 `new`。SKILL.md 已刪「`pending_id` 是 null 就先跑 scan」 |
| 32 SKILL 四處＋小點 | 過 | SKILL.md：被打回「檔名該是／卡該放在」寫新路徑、舊檔交 sync；查無變動先跑 `cards todo --json`；分辨節有「來源根 vs 來源裡面」；manage SKILL.md 有對稱一句；「轉述第一行」改照 commands.md；carding.md 沒有「檔頭之後空一行」；commands.md 補了「狀態檔讀不到」「等不到鎖」「沒有寫入」；SKILL 本文 ≤120 行、兩份 reference ≤150 行；兩個 skill 的 description 與 `9d084f2` 相比沒動 |

### 無退步

驗收 4（含裁定 8 的四種拒絕、`..` 繞路、編號寫法、排除夾）、9、11、13 重跑全過。驗收 10：六種檔頭錯都 block、合法卡無輸出且總目錄更新；「AI 產出 照舊登記」我的腳本只跑 PostToolUse、沒先跑 PreToolUse 備份所以顯示不過，改用 `write_via_tools`（PreToolUse → 寫 → PostToolUse）重測：無輸出、清冊有 `file://AI 產出/新草稿.md`，過。

### 路由 eval（B-036 做法、f67b538、llm-wiki@llm-wiki ✔ enabled、`query failed` 0）

| 次 | skill | 該觸發 | 不該觸發 | 句子 |
|---|---|---|---|---|
| 第一輪 | ingest（e87c248） | 27/30 = 90% | 0/30 = 0% | 19/20 |
| 第一輪 | manage | 0/30 = **0%（異常）** | 0/30 | 10/20 |
| 重跑 1 | manage | 26/30 = 86.7% | 0/30 = 0% | 19/20 |
| 重跑 1 | ingest | 29/30 = 96.7% | 0/30 = 0% | 20/20 |
| 重跑 2 | manage | 25/30 = 83.3% | 0/30 = 0% | 18/20 |

- **ingest：過。** 重跑 96.7% / 0%，只有「資料夾裡的東西都整理好了嗎？還有沒做的嗎」2/3。
- **manage：沒到 90%。** 兩次重跑 86.7%、83.3%，不該觸發都是 0%；漏的是「我改名叫行政總務」（1～2/3）、「搬到王宅那夾」（0～1/3）、「不要讀…薪資」（2/3）。第一輪的 0/30 整輪沒觸發，應該是當時的呼叫環境問題（只花 3 分鐘，ingest 同一時段正常），不是描述的結果，我不把它當資料。
- 這不能歸給這個分支：manage 的 `description` 與 `9d084f2`、`manage.json` 都沒動；eval 只載入被測 skill 的暫時指令檔，ingest 不在場，所以不是互搶；manage 的 10-16 基準是 30/30。差在環境（模型、已裝的其他 plugin 那邊的 skill 描述）或單純變異，我沒辦法在不動使用者設定下分離。「搬到…」「改名」這類句子本來就在 manage 與 ingest 的邊界（裁定 32 剛把來源裡的搬動劃給 ingest），若要保留 manage.json 這兩句為該觸發，要設計 session 判斷是否調 eval 句或描述；測試 session 不改。

## 第三輪（2026-10-19，對 07eab14：PDF 改走選配 pypdfium2，裁定 36、37）

### 結論

- 全部測試 `python -m unittest discover tests`：**exit 0**（這台有 pypdfium2，PDF 測試真的跑）。
- 裁定 36 的每個子項：**全過**，各有一個實跑證據。
- 無退步：驗收 4 重跑，裁定 8 的四種拒絕與三種寫法全過；有 5 項舊檢查不過，原因都是裁定 36 本身改了行為（見下），不是退步。
- security：PNG 仍受裁定 16 的父夾檢查；假模組只活在該子程序的 `PYTHONPATH`，不影響別的。沒有新問題；一個既有的 junction 限制（B-055）同樣適用 PNG。

### 裁定 36 子項

做法：fixture `tests/fixtures/pdf/` 兩份（`文字兩頁.pdf`、`只有圖.pdf`）加我自己組的 25 頁空白 PDF、4000×4000 pt 大頁 PDF、`/Encrypt` 假 PDF，複製進 `tests/fixtures/inventory` 複本的 raw 後 scan，用 `kb.py` 子程序跑。「沒裝」用 scratchpad 的假套件夾（`pypdfium2/__init__.py` 內容 `raise ImportError`）放進子程序的 `PYTHONPATH`。

| 子項 | 結果 | 實跑證據 |
|---|---|---|
| 文字層抽字、頁碼註解 | 過 | `文字兩頁.pdf` 的暫存檔：`<!-- 第 1 頁 -->`、兩行英文內文（`Quote v2 total 1280000 NTD`、`Delivery 2026-11-30`）、`<!-- 第 2 頁 -->`；第二行印 `格式：pdf；2 頁；掃描頁 1（已轉圖）`；暫存檔 UTF-8 |
| 掃描頁轉 PNG | 過 | 第 2 頁轉成 `f-<編號>_p2.png`（stem 是驗過的編號，照裁定 16）；檔案是有效 PNG（簽名、IHDR 1224×1584、8 位元 RGB = 612×792 pt × scale 2）；我用 Read 看那張圖：白底、左上角「p2」，內容對。`只有圖.pdf` 暫存檔只有 `<!-- 第 1 頁 -->`（整份掃描頁仍寫暫存），圖一行 `圖：…_p1.png（第 1 頁）`；`--json` 帶 `scanned_pages: [2]`、`images: […]`、`pages: 2`、`kind: pdf`；掃描頁 0 的檔第二行沒有「掃描頁 0」 |
| 上限 `pdf_read_pages_max` | 過 | 25 頁全空白：預設只轉 20 張（`_p1`～`_p20`）並印 `掃描頁超過 20 頁：只轉前 20 頁，卡的讀取標「部分」`；`.system/config.json` 設 `pdf_read_pages_max: 3`、`pdf_render_scale: 1` 後：只轉 3 張、`掃描頁超過 3 頁…`、`--json` 的 `images` 3 筆、`scanned_pages` 仍列全部 25 頁、PNG 尺寸 200×200（scale 1，頁面 200 pt）→ 兩個 config 鍵都有效；`pdf_page_min_chars` 隨預設值 10 生效（兩頁 PDF 的文字頁 76 字、空白頁判為掃描頁） |
| 加密 PDF「有密碼」 | 過 | `/Encrypt` 假 PDF：exit 3、`讀不到：有密碼`、第二行 `已記進待處理（p-…）`、`--json` 的 `unreadable: 有密碼`。（pypdfium2 自己丟的「password」分支我造不出真加密 PDF，沒走到；實際上前置的 `/Encrypt` 掃描會先攔） |
| 沒裝 → exit 3、待處理、新問法 | 過 | 假模組下 convert 文字兩頁與只有圖：exit 3、第一行 `讀不到：還沒裝 PDF 元件（pip install pypdfium2）`、第二行 `已記進待處理（p-…）`、`.system/temp/` 沒新檔；`pending.json` 兩條 `unreadable`，reason 同句；`系統/待處理.md`「讀不到（1）」那條末句是新問法「請服務人員在這台電腦跑 pip install pypdfium2。」；假模組下 `cards todo`：`unreadable` 兩份 PDF、不在 `new`，第一行「讀不到 2」 |
| 裝回後 todo 自動 done、印那一行、回到 new | 過 | 一份 dismissed、一份 open 的狀況下，改用真環境跑 `cards todo`：最前面印 `PDF 元件裝好了，2 份 PDF 回到待製卡`；兩條待處理都變 done、`how` 是「PDF 元件裝好了」；該兩檔回到 `new`（`待製卡 14 份`）；`系統/待處理.md` 變「目前沒有等你決定的事」；那個回合的 manifest 先備了 `.system/pending.json` 與 `系統/待處理.md`；再跑一次 todo 不再印那一行 |
| config 兩鍵與 `_why_` | 過 | `defaults.json` 有 `pdf_page_min_chars` 10、`pdf_render_scale` 2，各帶 `_why_`；`_why_pdf_read_pages_max` 改為「PDF 掃描頁轉圖最多幾頁…」；上面兩項實測都由 config 覆寫生效 |
| README／carding／commands／SKILL 第 6 步 | 過 | README：PDF 要 `pip install pypdfium2`（選配，沒裝會列在待處理）、有文字的頁由程式抽字、掃描頁轉圖；carding.md：PDF 讀暫存檔、頁碼照 `<!-- 第 N 頁 -->`、圖行用 Read 看 PNG、「讀不到：還沒裝 PDF 元件」的回答說法、掃描頁超過上限讀取標「部分」；commands.md：PDF 輸出格式、`--json` 的 `scanned_pages`／`images`、還沒裝那列的轉述、todo 的「PDF 元件裝好了」那行；SKILL.md 第 6 步改成「暫存檔用 Read 分段讀（PDF 也是暫存檔，掃描頁另外印「圖：…」，用 Read 看那些 PNG）；圖片檔用 Read 看原檔」，跟程式輸出一致；`test_skill_ingest`、`test_budget` 綠（含在全部測試裡）；`CLAUDE.md` 地圖 `extract.py`、`convert.py`、`defaults.json` 三列都補了 PDF |
| fixture 無開發機路徑、無 CRLF | 過 | `tests/fixtures/pdf/` 兩份各 1074、896 位元組（< 20 KB），沒有 `\r\n`，沒有 `Users`、`Desktop`、`C:`、使用者名稱字串 |

### 無退步：驗收 4 重跑

- 過：三種目標寫法（編號、uri、路徑）第一行 `暫存檔：…`、UTF-8、LF、無內容；x.doc exit 3 與第二行；裁定 8 全部（排除夾、編號、`..`、不在清冊像金鑰、總資料夾外、`.system/inventory.json`、四種都沒新暫存與待處理）。網銀密碼.txt 本身仍是 exit 3、第一行 `讀不到：檔名像密碼或金鑰，預設不讀`。
- 5 項舊檢查不過，**原因是裁定 36 改了行為，不是退步**：
  - 「PDF 印 `PDF：…用 Read 讀原檔：<路徑>`」：該行已被裁定 36 取代。
  - 「`pending.json` 沒有新條目／只有一條 unreadable／md 讀不到（1）／再跑不多一條」四項：`tests/fixtures/inventory` 的 `報價_v2.pdf` 是假 PDF（`%PDF-1.4\n\0\1fake quote v2…`，打不開），舊版只數頁數不開檔，現在 pypdfium2 會真的開，得到 `讀不到：檔案壞了：PDF 打不開` 並寫一條待處理，把我腳本裡的條數算亂。pending 裡那一條確實是 `報價_v2.pdf`。這是合理的新行為（真壞 PDF 就該進待處理）。
  - 影響到確認階段的只有：fixture 裡的 PDF 不再能當「可讀 PDF」用。

### security

- **PNG 寫入路徑**：`convert._convert_pdf` 先呼叫 `_temp_target`（裁定 16 的 `_temp_name` 驗編號、否則 12 碼指紋；寫前比對父夾 realpath）再 `render_pages(…, d, temp.stem, …)`，PNG 路徑是 `d / f"{stem}_p{n}.png"`，stem 是驗過的，頁碼是整數，`.tmp` 加 `os.replace` 原子寫。路徑檢查沒有被繞過。junction 的既有限制（B-055）對 PNG 同樣成立，沒有更糟。
- **假模組**：只放在被測子程序的 `PYTHONPATH`；`kb.py` 的 `sys.path[0]` 是 `scripts/`，不是 cwd，所以知識庫夾或來源夾放一個叫 `pypdfium2` 的東西不會被 import（我沒有另外造這個案例，看程式與 Python 行為）。`_pdfium()` 只認 `ImportError`。
- **大頁面**：4000×4000 pt 的單頁 PDF，scale 2 轉 8000×8000 px，0.6 秒完成、PNG 202 KB（白頁壓縮得很小）；再大的頁面（pdfium 上限 14400 pt）或滿是內容的大頁，點陣圖在記憶體裡可到數 GB，`render_pages` 沒有尺寸上限，也沒接 `MemoryError` 之外的保護。這次沒測，列為低風險觀察，建議跟 S7 一樣以 `pdf_render_scale` 或像素上限處理。

## 第四輪（2026-10-19，對 6b30ee9：裁定 38、39）

**過。**

- `hooks/session.py` 的 `INGEST_HINT` 一字不差是「有變動：先回答客戶這句，接著不用問、直接照 ingest skill 整理；只有超過 {n} 份才問。」；開場實跑（fixture 複本加一個新檔）印出 `【wiki-with-willy】kb 指令：…`、`變動：新增 1、修改 0、不見 0`、`有變動：先回答客戶這句，接著不用問、直接照 ingest skill 整理；只有超過 5 份才問。`，第三行與預期字串逐字相等，n = 5；這段 3 行 160 字，守 8 行 400 字。
- `skills/ingest/SKILL.md` 第 5 步有「≤ 上限就不問客戶要不要、不說「要不要接著整理」，直接做完在回答末尾一句告知」與「一份抓 10 秒」；舊的「一份抓 1 分鐘」已不在。
- `test_hooks_session`、`test_budget`、`test_skill_ingest` 綠；全部測試 `python -m unittest discover tests` exit 0。
- 範圍：`git show --stat 6b30ee9` 只動 `hooks/session.py`、`skills/ingest/SKILL.md`、`tests/test_hooks_session.py` 三個檔（`git diff 07eab14 6b30ee9` 另外出現 `design/backlog.md` 與 spec，是合併 main 帶進來的設計文件，不是這個 commit 的改動）。
