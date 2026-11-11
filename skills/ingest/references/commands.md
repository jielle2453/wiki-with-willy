# 整理成卡用到的指令

前綴一律 `python "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py"`。exit 0 是做完；exit 3 是程式拒絕，第一行是原因，照下面的做法接著做完，不用念給客戶。

## changes、inventory scan

- `changes`：跟清冊上次掃描比，只看不改。沒變動印「跟上次掃描時相同，沒有變動。」；有變動先一行「跟上次掃描比（還沒套用）：」，每種一行「- 新增 N 份：…」「- 修改 N 份：…」「- 搬動或改名 N 份：…」「- 不見 N 份：…」「- 之前就不見、還沒處理 N 份：…」「- 這台電腦找不到來源：…」。最後一行「要套用請跑 kb inventory scan。」出現才跑 `inventory scan`（只剩之前就不見、找不到來源時不印這行，scan 也不會變）。客戶說「我改了報價單」但查無變動：跑 `cards todo --json`，`plan` 有 `dispatch` 或 `cards` 就照骨架做（之前可能沒做完）；沒有就問「是不是還沒存檔？」；說的是口頭的事（付款條件談好了、會改期）照 note 做。`changes --background` 是電腦排程定時跑的（結果給每句開頭讀），AI 不用跑；`changes`、`inventory scan` 跑完也會更新那份結果（D-536）。
- `inventory scan`：全掃並記進清冊；原檔不見會自動記進待處理，找回來會自動關掉。

## convert <目標>

目標寫清冊編號（`f-3f9a1b2c`，最常用，照 todo 給的 `id`）、uri（`file://客戶案件/信義王/報價_v3.pdf`）或磁碟路徑都可以。

成功（exit 0）第一行三種：

```
暫存檔：<絕對路徑>
格式：xlsx；工作表 3；字數 4120
```
- text 多一行 `編碼：<utf-8｜utf-8-sig｜cp950｜big5hkscs｜utf-16>`；字數超過上限、或 xlsx 抽到上限停下時多一行 `大檔：照 carding.md 摘要加定位`；字數 0 多一行 `轉換結果是空的`。
- PDF（選配元件 pypdfium2）：第一行一樣是 `暫存檔：…`（文字層，每頁前面 `<!-- 第 N 頁 -->`），第二行 `格式：pdf；12 頁；掃描頁 2（已轉圖）`（沒有掃描頁就沒有後半），接著每張圖一行 `圖：<PNG 絕對路徑>（第 N 頁）`；掃描頁超過上限多一行 `掃描頁超過 20 頁：只轉前 20 頁，卡的讀取標「部分」`。
- `圖片：用 Read 看：<絕對路徑>`。

讀不到（exit 3）第一行「讀不到：<原因>」；記進待處理的第二行 `已記進待處理（p-…）`。記進待處理的原因（只在雲端、舊格式、有密碼、還沒裝 PDF 元件、不支援的格式、檔案壞了、抽到一半才發現太大、編碼）重跑 `cards todo --json` 後由 `tell` 帶「怎麼辦」，講法以它為準；第二行沒有「已記進待處理」時 `tell` 不會帶這份，直接用「讀不到：」後面那句原因告訴客戶，這份先略過。下表是不記待處理的情況。`convert` 不收 `--vault`，總資料夾一律從目前目錄往上找。
加密的 PDF（只解 PDF，加密的 Word、Excel 這版打不開）：`--password "<她剛給的>"` 用那組開，成功多一行 `這組密碼還沒記住：回答末尾問她要不要記住`（`--json` 的 `ask_save` 是 true），照問、她說記住才照 note 的 `references/password.md` 存；`--saved <名稱>` 用憑證管理員存的那組，要授權過的種類（plan 的 `unlock_same_kind`），或她這句明說要試才加 `--explicit`。密碼錯印一行 `密碼不對`、不記待處理；`這組密碼沒說過可以用在別的檔`、`這台電腦沒有記「X」`、`「X」不是 PDF 密碼` 照 note 的 `references/password.md` 說。開成功會關掉那份的「讀不到：有密碼」。她給的密碼不放進卡、`turn note`，回答裡也不複述。「可以讀」的密碼檔暫存叫 `<編號>.secret.md`，那一句答完程式就刪。

| 原因（不記待處理） | 跟客戶說 |
|---|---|
| 檔名像密碼或金鑰，預設不讀 | 「檔名像密碼或金鑰的預設不讀；要讓我讀哪一份跟我說「X 可以讀」。」她說了照 note 的 `references/ruling.md` 第三節記，再重跑 |
| 檔案太大（清冊標太大，沒記待處理） | 「這份太大，這版讀不了，跟我說先跳過。」 |
| 檔案被別的程式開著或沒有權限，關掉 Word／Excel 再試 | 「這份正被 Word／Excel 開著（或沒有權限），關掉後跟我說，我再整理。」 |
| 暫存位置不對 | 「知識庫的暫存夾不對勁，我先停下來；說「檢查」我看原因。」 |
| （第一行沒有「讀不到：」）這裡不是知識庫；到知識庫裡執行 | 不用跟客戶說；對話要開在知識庫裡 |
| 找不到原檔 | 「找不到這份檔，可能剛被搬走或刪掉；我重新比一次。」接著跑 `inventory scan` |
| 這個位置你說過不讀 | 「這個夾你說過不讀，我不整理它。」 |
| 不在登記的資料夾裡 | 「這份不在我讀的資料夾裡；要的話先把它所在的資料夾加進來。」 |
| 這不是要製卡的檔 | 不用跟客戶說，是你給錯目標；照 todo 給的編號重跑 |
| 清冊沒有 f-… | 不用跟客戶說；重跑 `cards todo --json` 拿現在的編號 |
| 狀態檔壞了（…） | 「知識庫的狀態檔讀不了，我先停下來；說「檢查」我看原因。」 |

`--json`：`{"id","uri","path","kind","temp","chars","parts","encoding","pages","big","unreadable","pending_id","scanned_pages","images","ask_save","unlocked"}`（`unlocked` 是 password｜saved｜null）（`scanned_pages` 是掃描頁的頁碼清單、`images` 是轉出的 PNG 路徑清單，PDF 才有），沒有的欄是 null，失敗時也印這份（`unreadable` 是原因）；`kind` 是 docx｜xlsx｜pptx｜text｜pdf｜image｜legacy_office｜encrypted｜unsupported。

## cards todo [--json] [--limit N] [--full]

不寫任何檔。人讀第一行：

```
待製卡 N 份（新 N、重做 N）；搬動 N；讀不到 N、原檔不見 N
```
PDF 元件剛裝好時最前面多一行 `PDF 元件裝好了，N 份 PDF 回到待製卡`（之前記成「還沒裝 PDF 元件」的待處理自動關掉）。全部 0 印 `卡都是最新的`；有檔頭壞的卡第一行尾多「；檔頭有問題 N 張」，同編號多張多「；同編號多張 N 組（跑 kb cards sync）」。實體與綜合卡有事時第一行尾再接「；待掛實體 N」「；綜合卡 待建 N（待確認 N）、待更新 N、有問題 N」「；實體夾有變 N」「；日程待重對 N」「；日程待抽 N」（0 的項不印；日程待抽不為 0 時不會印「卡都是最新的」）；`missing_types` 有值時多一行（卡型在設定檔沒有的份數；照 carding.md 直接加進設定檔）。不製卡的份數不為 0 時第二行 `不製卡：檔名像密碼或金鑰 N、只在雲端 N、太大 N、讀不到 N、依設定不整理 N`（0 的項不印）。之後每份一行 `- 新 f-… 客戶案件/信義王/報價_v3.pdf → 檔案索引/客戶案件/信義王/<標題>-3f9a1b2c.md`（重做的是 `- 重做 f-… <原檔> → <舊卡路徑>`），`--limit` 預設是開場的份數上限，多的印 `……還有 N 份`。

`--json`：`new`、`stale`、`broken`、`unlinked`、`synthesis_*`、日程這些是製卡的材料，欄位說明在 carding.md、agenda-scan.md、synthesis-batch.md、synthesis-broken.md 的「材料的欄位」（製卡交給 card-maker，主對話只在 `plan` 有 `cards` 那步時用）。製作中另有 `cards wip [--json]`（只讀，D-525）：todo 的 `new`、`stale` 去掉 md、線上文件與標了重做的，`[{"id","path","name","mtime","entities"}]`，新改的在前；`path` 是原檔，`entities` 是算在哪個實體下（實體夾，或檔名有名稱、別名），都沒有是 `[]`；人讀 `製作中 N 份：` 接每份「- <檔名>（M/D 改）（實體）」；她說整理就照骨架整理全部，收工照核心規則 R-13 帶一句。預設精簡：`moved` 超過 10 筆只給前 10 筆加 `moved_total`（搬動照 `cards sync` 做，不用看每筆），`profile` 只給 `about`；要整份加 `--full`。

`--json` 另有 `plan`、`asks`、`tell`、`announce` 四樣程式算好的，照著做（`new`、`stale` 等是做事的材料，這四樣才是順序與問法；第一條是日程的指引）：
- 日程（`kb agenda`、`kb entity progress`、`kb asks`）的指令與逾期題的答法看 note skill 的 `references/agenda.md`；日程有出入、結案兩種題照 `asks` 的結果做；`plan` 的 `agenda_recheck`、`agenda_unscanned` 兩步照 agenda-scan.md 做（`kb agenda scanned` 的說明在 note 的 `references/agenda.md`）。
- `plan`：照順序要做的事，每步 `{"step","items","do","read"}`；`read` 是先讀的那份（null 不用讀；`references/…` 相對本 skill 資料夾，`rules/synthesis.md` 是實際路徑）。全空是 `[]`；份數超過 `ingest_auto_max` 時只剩 sync，其餘在 `asks` 第一題「現在做」的 `then` 裡（這個對話已經在整理、或說過停，就不再問這題）。說過停的對話最前面一步 `resume`（`do` 是 `kb cards dispatch --json`，帶 `note`）：客戶說「整理」「繼續整理」才跑，沒說不做（D-200）。做卡、抽日程、寫綜合卡是一步 `{"step":"dispatch","items":45,"do":"kb cards dispatch --json","read":null}`；線上文件、檔名像密碼的、有密碼的那幾份留一步 `cards`（多 `files`：編號清單，材料在 `new`、`stale`），主對話自己照 `read` 做。`unlock_same_kind` 一步（排在寫卡之前）多 `files`（每份 `{"id","name","folder","size","date"}`，有密碼讀不到的 PDF）與 `kinds`（每種 `{"label","credential","examples":[{"name","folder"}]}`），照 carding.md 第十節判斷與開。到門檻還沒確認的新名字時最前面一步 `names`（多 `names`：每個 `{"entity_id","name","type","cards"}`，`cards` 是掛的卡標題），照 carding.md 第十一節判斷，有把握的 `entity accept`。
- `asks`：要問客戶的題，已排好序（份數多先問、實體夾或綜合卡不見、其他實體夾有變、原檔不見、同一種的密碼檔（`credential_same_kind`，多 `file`、`labels`、`note`，先判斷再問，note 的 `references/ruling.md` 第三節）、裁示蓋的檔改版了（`ruling_recheck`）、逾期、日程有出入、結案、名字確認），每題 `{"id","key","about","urgent","kind","question","results":[{"n","label","do","strict","fill"}]}`（`key` 是穩定的鍵、`about` 是對象的實體與檔、`urgent` 是有時間性；她先放著的不列、數在 `set_aside`，D-226）。`n` 從 1 一路編下去，是程式用來對結果的內部編號，不必給客戶看；`do` 是選了這個結果要依序執行的指令（`kb ` 開頭，空是不跑指令），`fill` 列 `do` 裡 `{新名字}` 這類空格，每個空格都是 `{"name","rule"}`，照 `rule` 寫（自由文字寫成一句白話，名字有空白就加雙引號）；`strict` 是會刪東西的；結果可帶 `note`（給你的一句話）或 `then`（接著要做的 `plan` 步驟）。例：`{"n":2,"label":"沒刪，先留著","do":["kb pending resolve p-1a2b3c4d --how \"{客戶的話}\""],"strict":false,"fill":[{"name":"客戶的話","rule":"一句白話，不含 \" $ ` \\ 這四種字元"}]}`。客戶回了、對到一個結果照 `do` 做完之後：有 `note` 照做；`do` 空又沒有 `note`，不跑指令，白話複述這個結果；帶 `then`（份數多時客戶說「現在做」）就照 `then` 從「照計畫做」做，份數仍多而 `plan` 只剩搬卡、或同一題又出現，不用再問；清單沒有的事用現有指令或自己的方法組出做法做完，真的沒路才白話說；其餘結果執行完回「拿計畫」，確認名字這類結果會帶出新的事。
- `tell`：回答末尾要講的句子（讀不到，每份帶原因與怎麼辦、不製卡的份數、依設定不整理、PDF 元件裝好、加進日程、照新版改了日期），順序排好、0 的不列，照講。例：`["合約_加密.pdf 讀不到：有密碼；給我密碼就能打開，要我記住也可以","請款類 4 份依你的設定不整理"]`；用存好的密碼開了同一種的有一句「「X」我判斷跟之前的保險單是同一種，用那組密碼打開了」。
- `announce`：要整理的份數（新、重做、待掛實體、待抽日期）大於 0、不超過 `ingest_auto_max`（預設 20）時，開始整理前的一句；不報份數、不報時間（客戶問才講），直接在背景做；0 份或超過門檻是 null（超過時 `asks` 第一題是「現在做嗎」：問「這批有 {n} 份，全部整理會用掉不少額度：要全部做、只做某幾個夾，還是問到再整理？」，順便問「以後這種就這樣做嗎」，說好記成偏好、之後同一種不再問，客戶說「不用問」就記偏好照做；0 份就照 `plan` 剩下的做）。

## cards dispatch [--source <別名>] [--ids f-…,f-…] [--adopt] [--json]（背景整理，只在對話裡）

`{"job","turn","batches":[{"batch","kind","items","agent","description","prompt"}|{"batch","kind","items","self","do"}],"running","left":{"cards","agenda","synthesis"},"approval":{"question","do"}|null,"done","note_cmd","stopped","failed":[{"id","kind","reason"}],"say"}`；常走的照 SKILL 骨架「讀派工結果」；其他幾種：`approval` 有帶就直接跑它的 `do` 再 dispatch，不停下來問（一次改很多張綜合卡也照做，寫前程式會先留一份）；`stopped` 是這一句說了停，不派，帶 `taken_over` 是整理交給了另一個對話，照 `say` 講一次，之後收到通知也不再派；`done` 是都做完了，到骨架「回答」，最後的回答用 `tell`；`say` 沒句子就不說話，`announce` 講過的也不再講；回報有字典外那行，照 D-231 問她要不要加，不自己加（留到「回答」問）。做卡份數在 `card_self_max`（預設 2）以內時整次整理的批次都帶 `self`（D-531）：不派製卡助手，跑 `do` 第一條 `cards batch <b> --json` 拿材料、照 `read` 自己做，做完跑第二條 `cards batch <b> --done`（只收這個對話 self 的批次），再 dispatch 拿下一種，直到 `done`；`say` 在做完前是 null。同時在跑的批數上限是 `card_parallel`（預設 3），做卡 → 日程 → 綜合卡依序；`--source` 只整理那個來源，job 記著，之後再派不用再加。製卡助手回報沒做成的記在 `failed`，同一次整理不再派；同一份兩次整理都沒做成，程式照讀不到記進待處理，檔案改了才再派。製卡助手自己跑 `cards batch` 拿材料，主對話不跑。`batches` 空、沒有 `approval`、`done`、`stopped` 是還在跑，等通知；每收到一個製卡助手做完或停了的通知再 dispatch（範圍程式記著，不用再加 `--source`）；`say` 以外不另外加話。這期間客戶問別的照常回答、末尾一句還在整理；問進度跑 `cards status` 照講。人讀 `派了 3 批（做卡 20、20、20）；還在跑 0 批；還剩 做卡 176、綜合卡 6`（有沒做成的後面接「；沒做成 N 份」）、`沒有新派的；…`、`整理完了`、`這一句說了停，不再派；客戶說整理再派`。拒絕（exit 3）：`整理要在對話裡派`（終端機跑的）、`沒有登記這個來源：X`（`--source` 打錯，照 `folders list` 的別名重跑）。建置用的兩個照 build skill 做：`--ids` 只做那幾份（抽樣，job 記著；主對話也用它整理每句開頭那行列的 md（她那句沒說整理時），派做卡與那幾份卡的日程重對，綜合卡等她說整理再做；她說整理一律不帶 --ids，這批 `done`、job 關掉前不派全部，關掉後照骨架派全部（不帶 --ids），D-525 裁定 3、5），不在可派清單裡的拒絕 `這幾份不在要整理的清單裡：…`；`--adopt` 派讀舊 wiki 的段（`left` 是 `{"adopt"}`），還沒切段拒絕 `還沒切段，先跑 kb build adopt …`。 多人用 git 時：`offline` 是 true 就說「連不到 git，等連上再整理」，先用原檔答；`claimed_elsewhere` 有人名就說「小王那台在整理，我先照原檔答」，那幾份這台不做。**沒有派子代理的工具時**：這次 `batches` 每一批都自己照 `prompt` 做完（每批各跑一次 `--done`）才再 dispatch，沒有通知可等；製卡是你自己讀原檔，「自己不讀原檔」那條這時不適用。card-maker.md 的「不跟客戶說話」「不跑 dispatch」「最後一則只回回報格式」是寫給子代理的：你照它做卡，回報只拿來看沒做成與字典外，不貼給客戶，做完照骨架往下。

## cards status [--json]、cards stop [--json]

- `status`（只讀）：`正在整理：還在做 36 份（2 批），做好 58 份；還沒輪到的 126 份`，沒在整理 `現在沒有在整理；停下做好 11 份；還有 61 份沒整理完（49 份還沒做卡、1 份我自己做（線上文件或有密碼的）、11 份卡做好了還沒抽日程）`；照講。`--json` 的 `jobs` 是這個對話的每次整理（舊到新 `{"job","turn","created","closed","status","cards"}`，`cards` 是寫好了的卡），`other_jobs` 是別的對話的，整批重做照 `references/redo.md`。
- `stop`：`{"agents":[…],"done","left","waiting_parts"}`；你派的子代理都中斷（Codex：`agents` 是空的，Codex 沒有 SubagentStart 記它們），停完再跑 `cards status` 照它講（停下前在寫的卡會寫完，stop 那一刻的數字會少算）。人讀 `已停：這次做好 58 份；還有 158 份沒整理完；這個對話不再自動整理，說「整理」再接著做`；沒在整理印 `現在沒有在整理`（exit 0）。

## cards unreadable <編號> --reason "<原因>"

線上文件（`plan` 的 `cards` 那步）讀不到時記進待處理：`--reason "線上文件打不開"`，印 `已記進待處理（p-…）`；之後不再算進待整理，`tell` 帶怎麼辦。原因跟口述同一套檢查：`原因要一句話，最多 200 字`、`原因裡不能有 " $ ` \ 這四種字元`、`原因裡不要放密碼；…`（exit 3），照改。其他拒絕（exit 3）：`這個指令只收線上文件；檔案讀不到由 convert 記`、`清冊沒有 f-…` → 是你給錯編號，重跑 `cards todo --json`。

## cards sync [--json]

先刪 `duplicates` 的 `drop`，再把 `moved` 的卡搬到原檔現在的位置（檔名不變；同夾只改檔頭的原檔清單與開原檔）。印 `搬了 N 張卡、更新 N 張卡的原檔位置`（有刪重複卡時後面接「；刪掉同編號的重複卡 N 張」）；新位置已有同編號的卡就收斂成那張，已有別張卡才跳過並印一行原因，照原因告訴客戶。

## cards drop <編號> [--json]

原檔不見才能用（原檔不見當作客戶刪的，直接跑）。印 `已拿掉「<標題>」的卡；說「退回上一版」救得回來`；沒有卡的印 `已記下「<檔名>」刪了（這份沒有卡）…`。拒絕：

- `原檔還在：<路徑>，不能拿掉卡`（exit 3）→ 檔還在，不刪卡，不必提。
- `只能拿掉原檔不見的卡（…）`、`清冊沒有 f-…`（exit 3）→ 重跑 `cards todo --json` 看 `missing`。

## cards skip <編號> [--kind <種類>] [--json]

讀完判斷這份是 `profile.exclusions` 裡 `no_card` 的種類才用：清冊記成依設定不整理，有卡的拿掉。印 `已記成不整理（<種類>）：file://…；有卡的已拿掉，說「退回上一版」救得回來`；下一次 `tell` 會帶「請款類 4 份依你的設定不整理」。`--json`：`{"id","kind","card"}`（`card` 是刪掉的卡或 null）。拒絕（exit 3）：

- `「X」不在客戶設定檔的不整理種類裡，…` → 種類不在設定檔就 `--kind` 照內容取個名（省略 `--kind` 記成 AI 看過、不用卡）；真的判錯了才照常做卡。
- `這份不是待製卡的檔（…）`、`清冊沒有 f-…` → 重跑 `cards todo --json` 拿現在的編號。
- `等不到鎖，沒有寫入` → 等一下再跑一次。

## cards index [--json]
手動重寫 `總目錄.md`，印 `總目錄：N 張卡`；寫卡、搬卡、拿掉卡之後程式會自動重寫，平常不用叫。

## pending list|resolve|dismiss

- `pending list [--json]`：列還沒處理的，每行 `- p-… 原檔不見 「會議紀錄_1.docx」（客戶案件/大安陳）不見了`；沒有就印 `目前沒有等你決定的事`。
- `pending resolve <p-…> --how "<一句>"`：記下客戶說的處理；`pending dismiss <p-…>`：不再列這一條（讀不到的檔客戶說先跳過，檔案換了程式會自動重讀）；都印 `已記下`。
- 拒絕（exit 3）：`沒有這條待處理：p-…`、`這條已經處理過了：p-…` → 重跑 `pending list` 看現在的。

## profile check|apply [--json]
另有 `show`、`add`、`remove`、`type rename|merge|remove`：客戶在對話裡說要改設定檔時用，怎麼問、怎麼說看 note skill 的 `references/preference.md`。建置時事先填好的設定檔用 `apply --from "<路徑>"`（build skill 帶）：先驗、讀不懂一樣回節與列、什麼都沒寫。
開場有「設定檔有改（…）」那行、或客戶說設定檔改好了要套用時用；同時有「有變動」就先處理設定檔再整理，卡型才會是新的。先跑 `profile check`：

- `客戶設定檔有改：…` 底下每行一項差異 → 那是客戶的意思，直接跑 `profile apply`；做完講發現什麼（「多了 工班（對象）」）、推測客戶的意思、做了什麼。
- `客戶設定檔有改，但沒有內容上的差異` → 不用套用，也不必提。
- `客戶設定檔沒有改` → 沒事。`還沒有客戶設定檔，跑 kb init 補`（`check` 是 exit 0，`apply` 是 exit 3）→ 自己跑 `kb init` 補。
- 最後一行 `第一層夾不在型別表：X（…）` → 看夾名與裡面的檔，自己判斷是不是新的一類；是就照 note skill 用 `kb profile add 型別` 加（大類推一個），說一聲加了什麼，不是就留著。
- 開場是「設定檔有改但讀不懂：…」→ 照冒號後那句自己修那一列再 `apply`，不用跟客戶提。

`apply` 印 `已套用客戶設定檔：…；建了資料夾 X；N 份回到待製卡`（拿掉的型別夾是空的會一起拿掉，印在「型別 -1（拿掉了空夾 X）」），第五節有改多一行 `關於你：登記了 X`（或「「X」改記成你自己；…」），照著講；有「回到待製卡」就照 ingest 骨架，從 `cards todo --json` 起做。改設定檔：一列的小改用上面的 `add`／`remove`／`type`，大改用 Edit 改 `系統/客戶設定檔.md` 再跑 `kb profile apply`；錯了自己修，改不好退上一版（`kb versions`）。`pending_types`：表上拿掉、實體還在的型別，先留著。`--json`：`{"state","diff","error","unlisted","to_create","removed_empty","removed_nonempty"}`，`apply` 多 `applied`、`created_dirs`、`removed_dirs`、`unskipped`。拒絕（exit 3）：

- `客戶設定檔讀不懂：第 N 節第 M 列…`（`check` 也是 exit 3）→ 照句子自己修那一列，再 `apply`。
- `「X」已經有同名的檔，不能建成型別夾；先改名或搬走` → 用 `kb source-edit move` 把那個檔改名（`--name`）再 `apply`，說一聲。`等不到鎖，沒有寫入` → 等一下再跑一次。
- `設定檔已改好，但後面沒做完（…）；跑 kb profile apply 會補上` → 設定檔已經改了，再跑一次 `profile apply` 補完。
- `建不了資料夾「X」：…；設定檔沒有套用`（下一行列已建的夾）→ 看原因自己處理（檔名重複、路徑太長就換名），再 `apply`；講一句白話。
- `apply` 印 `客戶設定檔沒有改，沒有套用` 是 exit 0，不是拒絕 → 沒事，不必提。

## entity …（都收 `--vault`、`--json`）
實體只經這些指令改，你不碰 `.system/entities.json`。名稱或編號都可以指定，寫別名也找得到。

| 指令 | 成功印 | 什麼時候 |
|---|---|---|
| `entity add --type 案子 --name 王宅` | `已登記：王宅（案子，待確認）`；已有就 `已登記過：王宅（案子）` | 寫卡前，實體不在 `entities` 裡（這個是你自己叫） |
| `entity confirm 王宅` | `已確認：王宅（案子）` | 名字對 |
| `entity accept 王宅` | `已登記：王宅（AI 判斷）`；客戶確認過的印 `已經確認過了：王宅`（exit 0） | `plan` 的 `names`：看掛的卡有把握（carding.md 第十一節） |
| `entity rename 信義王 --to 王宅 [--type 業主]` | `已改名：信義王 → 王宅（舊名記成別名）`；搬到夾裡的檔多一行 `資料夾裡的 N 個檔跟著搬了…`；改回原名印 `已照「信義王」把資料夾與綜合卡改回來` | 改名、改回原名、改型別；`asks` 帶 |
| `entity merge 王宅 --into 信義王` | `已合併：王宅 併進 信義王（王宅記成別名）` | 兩個是同一個；`asks` 帶 |
| `entity remove 大安陳` | `已移除：大安陳（知識不刪，檔案卡上的掛載留著）` | 不要這個實體；`asks` 帶 |
| `entity suggest-close 信義王 --evidence f-…` | `已記下，會問客戶「信義王」要不要記成結案`；依據問過印 `這份依據問過了，不再問`（exit 0）；實體不是進行中印 `「X」目前是擱置，不用問結案`（exit 0） | 讀到結束證據（agenda-scan.md）；問客戶的是 `asks`，你不問 |
| `entity rebuild 大安陳` | `好，「大安陳」會照檔案卡重寫一張` | 綜合卡被刪、回收筒也沒有；`asks` 帶 |
| `entity clean 01 案子/王宅/王宅.md`（相對總資料夾，型別夾有編號；有空白照加引號） | `已清掉放錯的綜合卡：…` | 綜合卡寫到正確路徑之後，清掉寫錯的那張 |
| `entity list`／`entity render` | 每行 `- 信義王（案子，確認）掛 12 張，有綜合卡`（確認、待確認、AI 判斷三種）／`重寫了 N 張綜合卡的檔頭` | 查看；製卡助手改完待更新的綜合卡、沒有紀事要加時跑 `kb entity render <名稱> --synced`（有紀事要加就由 `entity log … --synced` 標，主對話不補），程式換整理到、更新日、檔案卡、相關實體；不帶名稱與 `--synced` 是重寫全部綜合卡的檔案卡、相關實體兩欄，平常不用 |
| `entity log 台船 '2026-10-25 開會〔f-…〕' --synced` | `台船的紀事加了 1 行，已標記整理到`；一字不差已有的多一行 `已經有了：<行>`；`--json` 給 `{"name","added","skipped","synced"}`；找不到、沒綜合卡、綜合卡有錯、行的格式或出處不對 exit 3、第一行是原因、不寫檔 | 紀事一律用它加（日期倒序插進 `## 紀事`）：製卡助手照 `rules/synthesis.md` S-22 加待更新綜合卡的；主對話處理指路卡的 `pointer_changed` 也用它（carding.md 第十三節） |

拒絕（exit 3），照做：
- `型別「X」不在型別表；…` → 照內容直接 `kb profile add 型別`（大類推一個）加進去，說一聲，再登記。
- `已有「信義王」（案子）；是同一件就掛它，不是就加區分詞（例：…）` → 是同一件就掛既有的名稱；不是就換個帶區分詞的名稱再 add。
- `「林小姐」是你自己（關於你），掛「林小姐室內設計」` → 照掛那個名稱。`名稱「X」不能用（…）` → 換名稱。
- `型別不同：先 kb entity rename …` → 照那句先跑再合併；`entity merge` 會把舊夾的檔搬進目標的夾；同名的列在 `conflict`，由你判斷取代還是另取名。
- `實體清單沒有「X」`、`實體清單沒有：X`（accept）、`「X」的綜合卡還在，不用重寫`、`這張綜合卡位置是對的，不用清`、`這不是綜合卡，不能用 clean` → 是你判錯，重跑 `cards todo --json`。
- `等不到鎖，沒有寫入` → 等一下再跑一次。

## cards、pending、profile、entity 共通的拒絕（exit 3）

- `這裡不是知識庫；到知識庫裡執行，或加 --vault` → 不用跟客戶說；對話要開在知識庫裡。
- `狀態檔讀不到：…`、`待處理讀不到：…` → 自己再試一次；還是讀不到照核心規則 R-12。
- `等不到鎖（別的指令正在改），沒有寫入；請稍後再試`、`等不到待處理的鎖…` → 等一下再跑一次；還是一樣就跟客戶說「另一個對話正在改知識庫，等它做完再整理」。
- `沒有寫入：…` → 跑 `kb selftest` 看原因，自己能修的修；講一句白話這次沒改到什麼。
