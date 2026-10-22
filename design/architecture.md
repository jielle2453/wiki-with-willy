# 架構

日期：2026-10-11
給誰看：你與所有 session。
什麼時候更新：只在設計 session 改。決策的理由在 `decisions.md`，這裡只寫結論。

**一句話：使用者只看到六個面，問、答與出處、剛做了什麼、退回、要我決定什麼、資料去了哪。所有元件存在的理由是讓這六個面由機器保證，不是靠規則文字保證。**

## 一、原則

1. 三種真相物理分開：客戶原檔只讀、AI 筆記回合備份、AI 成品登記。分開守的是物件與路徑規則（第九節路徑表），不是資料夾；檔案一旦離開 `AI 產出/` 或 `收件匣/` 就是客戶原檔。
2. AI 的寫入只有四條路：筆記必經備份、成品必經登記、原檔必經受控程式、外部動作必經確認與記帳。守門的工作是讓第五條路不存在。讀取與 kb.py 的放行也由同一支守門做，不靠 permissions（D-073）。
3. skill 是「誰應門」，不是「誰擁有工具」。一句話一個入口，交棒明寫，共用腳本誰都能呼叫。
4. 狀態由程式寫或現算，人不手維護。
5. 失敗在每一層明講：讀不到清單、查無條件、守門留痕、失聯。
6. 數字只住 config，規則有編號，理由住 decisions 與 incidents。
7. 判斷與執行分開：製卡規則一份住 `ingest/references/carding.md`，寫「製卡者」不寫名字；製卡一律派子代理 card-maker 在背景做（D-053、D-195、D-196）。
8. 兩種卡：檔案卡一檔一張，回答「這份檔案是什麼」，跟著檔案走；綜合卡一實體一張，回答「這件事現在怎樣」，跟著問題長。複利發生在綜合卡，不發生在檔案卡。

## 二、元件分類

skill 只給「使用者說一句話，要認出意圖再走需要判斷的程序」的事。

| 判準 | 是什麼 |
|---|---|
| 每題都要遵守 | `rules/core.md` |
| 事件觸發、不需判斷 | hook 加腳本 |
| 只是格式或存放處 | 範本、資料檔 |
| 要認意圖、要判斷 | skill |

| 件 | 類別 |
|---|---|
| 工作日誌 | hook：UserPromptSubmit 記「問」，Stop 讀 transcript 記「結果」，截斷照 D-061 |
| 回合白話「做了」、待處理落盤、退回 | 規則加腳本 |
| 回合、備份、大改動攔截、守門、AI 產出登記 | hook |
| 變動偵測、健檢到期提醒、日程窗口 | hook 加腳本；處理與檢查才是 skill |
| 守門留痕、失聯、骨架對帳、遷移 | 腳本 |
| 客戶設定檔、日程、裁示、待處理、清冊 | 資料檔，JSON 為真；待處理與日程欄位見 D-062 |
| 檔案卡、綜合卡 | 範本兩份：檔頭機器欄位加正文建議段落，沒內容的段不寫。卡型與實體型別是建議段落組，住客戶設定檔 |
| 製卡 | 規則 `carding.md`；少量主對話做，多量與建置派 `agents/card-maker.md`（D-053） |
| 對話收件（貼文字存檔、貼圖、附件） | ingest 判要不要存；`kb intake` 寫收件匣，存原文 |

## 三、六個 skill

用「真相來源是誰」切，跟出處規則同一條軸。

| skill | 真相來源 | 應門的話 | 判斷什麼 |
|---|---|---|---|
| **ingest** | 檔案 | 我加了、改了、刪了、我搬了；這是合約內容存起來；開場列出變動 | 哪些建卡、刪的要不要問、太多要不要分批、貼進來的算文件還是口述 |
| **answer** | 知識庫推導 | 問資料、找檔案、掌握背景、統計、這週要做什麼、最近改了什麼、有沒有要我確認的（D-152） | 範圍、找哪裡、數字回不回原檔、值不值得存綜合卡 |
| **note** | 使用者親口說 | 偏好、日程與承諾、糾正、決定、把這個記下來 | 改的是系統規則、個案裁示、還是知識（D-041）；寫到哪；糾正連帶修哪些頁 |
| **produce** | AI 為使用者做的 | 做成 Word、給我 PDF、參考舊的寫新的、改一份文件 | 以哪份範本為底（卡型範本，第一次問之後記設定檔，D-068）、格式、草稿定稿、定稿放哪（推薦去處兩步，D-045） |
| **lint** | 知識庫自己的健康 | 幫我檢查 | 矛盾怎麼列、推測刪不刪、哪些自動修 |
| **manage** | 環境 | 加資料夾、不讀某夾、幫我搬、改名、資料夾搬到 E 槽了 | 破壞性操作確認、共用夾提醒；全庫操作只說明指令不執行 |

**邊界：** ingest 與 note 的判準是「這句話說的事，重讀檔案能不能驗證」：能就是 ingest，不能就是 note。「我改了報價單」「我整理了一下資料夾」能；「那份報價的付款條件談好改 30 天了」「合約.docx 是舊的別信」不能。判錯有後盾：ingest 開頭必跑 `kb changes`，查無變動即交 note；誤走 note 的檔案變動由下次開場的變動偵測補上。answer 不寫知識，只在核對時自己發現資料問題才經 kb 標重做或記待處理（D-155）；note 存陳述；「記下來」一律 note，指的是剛才的答案就記成推算（D-151、D-154）。produce 永遠另存，manage 永遠不改內容。糾正歸 note，修頁照 `rules/synthesis.md`。退回不是 skill。搬動：ingest 收「我搬了、系統發現搬了」，manage 收「幫我搬」。

**交棒：**

| 從 | 到 | 性質 |
|---|---|---|
| manage 加來源 | ingest 製卡該區 | 機器觸發的 skill 交 skill：登記完成 |
| ingest `kb changes` 查無變動 | note | 機器觸發的 skill 交 skill：誤路由。只有這兩個 skill 交 skill |
| ingest 改卡、note 糾正、lint 重寫過期頁 | `rules/synthesis.md` | 讀共用規則；變動不論來自檔案或口述，對綜合卡的反查與連帶處理都在這裡，skill 本文只留指針 |
| produce 定稿、收件匣的檔要放到別處 | `kb source-edit move` | 呼叫腳本；移動不複製，目標是實體夾或已登記未排除的 raw 夾 |
| ingest 收件 | `kb intake` | 呼叫腳本，存原文進收件匣 |
| ingest 製卡抽日期 | 日程檔 | 呼叫腳本 |

**agenda 併入基礎：** 資料是 `agenda.json`，口述歸 note，查詢歸 answer，開場窗口歸 hook，匯出歸 produce。**coach 不獨立：** 交付建議問題在 build 收尾、回答末尾在 answer、健檢時在 lint，共用 `rules/next-step.md`。**collab 是唯一大項：** 旗標開關，先放基礎，量了常駐成本再決定拆不拆。

**路由驗收：** 每句只能對到一個入口，改描述要重對。

| 客戶說 | 入口 |
|---|---|
| 加班費怎麼算／上次那份報價在哪／今年報價幾次／王宅現在怎樣／這週要做什麼／最近改了什麼／有沒有要我確認的 | answer |
| 我加了新檔案／我改了報價單／舊備忘我刪了／我把報價搬到 2026 了／這是合約內容，存起來／（開場列出變動） | ingest |
| 以後報價一律附日期／9/25 下午有個會／不對，信義王是 ZZ 做的／信義王結案了／把這個記下來 | note |
| 做成 Word／給我 PDF／照去年的合約寫一份新的／王宅的合約付款條件改成 30 天 | produce |
| 幫我檢查一下 | lint |
| 把 D 槽的報價也加進來／這份幫我搬到 2026／檔名改成…／不要讀私人那夾／報價夾搬到 E 槽了／幫我重建 | manage |
| 退回上一版 | 規則加腳本 |
| 合約付款條件改成 30 天（沒有文件在場） | 先問一句 |

## 四、build 指令與 manage 的分界

build 指令 `disable-model-invocation`：模型不代叫，客戶或服務人員自己打。不鎖，客戶可以自己建置。manage 與 build 用**可逆性與範圍**分，不用誰來跑分。

| | manage | build |
|---|---|---|
| 怎麼進 | 對話一句話 | 打指令 |
| 範圍 | 一個資料夾、一個檔 | 整個知識庫 |
| 可逆 | 回合備份 | 快照 |
| 內容 | 加資料夾、不讀某夾、搬檔、改名、資料夾搬了 | 建置五步、升級、備份、還原、搬家、換電腦、換帳號 |

**build 五步的內容（D-051、D-052）：** 一個指令 `build` 照順序走這五步，程式記做到哪、下次接著做（D-190）；下表的 build-setup 等是步驟名，不是指令；環境檢查在 setup 之前（D-133、D-189）。

| 步 | 誰 | 做什麼 |
|---|---|---|
| build-setup | 服務人員或客戶 | 有填好的客戶設定檔.md 就讀、檢查、複述、缺的才問；沒有就逐題問。選總資料夾名與位置、既有夾、本機或同步夾；問不讀某夾、敏感種類、關於你、每類最想知道什麼、幾個人用（D-191）。建殼、登記來源 |
| build-inventory | 程式 | 掃清冊；推 Office 環境與常查文件種類；偵測加密與讀不到並標原因 |
| build-sample | 程式加客戶 | 抽樣製卡給客戶看；AI 看程式給的事實猜型別表、事類狀態詞、實體合併成一份「我猜的」清單讓客戶改（D-192）；問每類最想知道什麼，答不出先用四大類預設段落 |
| build-cards | 程式 | 全量製卡，派 card-maker 分批（D-053）；最後照 D-114 門檻、名字確認後全部寫綜合卡（D-198、D-201）（D-063） |
| build-finish | 程式 | 寫建置報告：前半白話（資料去了哪、怎麼用、推測了什麼、讀不到什麼、加密幾份要不要授權），後半數字 |

沒 raw 的客戶：setup 後直接 finish（D-044），訪談少「不讀某夾」、多「你平常管哪幾類」。

**build 的安全四層：** 建置本身只用入口、鎖檔、狀態機可續做與回合備份，AI 每步先講再做；`--check` 與整庫快照給升級、還原、搬家、換電腦（D-194）。

| 層 | 措施 |
|---|---|
| 入口 | 模型不代叫；客戶在對話裡要求全庫操作時，manage 只說明指令與後果，不執行 |
| 入口 | 還原、搬家、全部重做放 `permissions.ask` |
| 執行前 | 每個指令先 `--check`：信任、環境、是否另一個在跑、磁碟、估算、會改什麼。預設 dry-run，`--apply` 才做。會改既有東西的先整庫快照到 `.system/snapshots/` |
| 執行中與後 | 鎖檔防重入、狀態機可續做、進度逐行印；寫報告、工作日誌一段、升級史一條 |

安靜模式預設開，`--show-content` 才顯示內容。第一次建置建議服務人員在場，寫在 README 與交付告知。

**十個操作的結論：**

| 操作 | 入口 | 腳本 | 關鍵防線 |
|---|---|---|---|
| 加資料夾 | manage | `kb folders add` | `discover` 掃上層列候選夾與 Office 份數、只列不登記；目標是另一個知識庫（有 `.system/` 標記）就拒絕並指 B-207；預覽先印份數與估算；登記即可讀（守門重讀 sources.json，D-073）；問一句現在讀還是下次 |
| 不讀某夾 | manage | `kb folders exclude --reason` | 兩種理由；不想讓 AI 看到要連 `versions/` `temp/` `AI 產出/` 的草稿一起清；刪推測逐條複述確認；固定一句「之前讀過的已送過雲端」 |
| 搬檔 | manage | `kb source-edit move` | 目標必須在實體夾或已登記未排除的來源內；同名就問；共用夾提醒；占位檔先告知 |
| 改名 | manage | `kb source-edit rename` | 副檔名不准改；同名就問；舊名自動進別名 |
| 資料夾搬了 | manage | `kb folders relocate` | 新路徑存在且抽樣比指紋 |
| 升級 | build | `kb upgrade` | 開場 hook 每天一次抓公開 latest.json，有新版印一行（D-072）；`--fetch` 下載公開 zip 驗 sha256 換 marketplace 資料夾，或吃收件匣裡的 zip；`--check` 列遷移與骨架三組；先快照；已客製逐個問；記升級史；卡只遷檔頭不重做正文（D-054） |
| 備份 | build | `kb backup` | 排除暫存與快照；目的地是同步夾時說一句；先清排除夾殘留；不排程，開場距上次備份超門檻提醒一行（D-055） |
| 還原 | build | `kb restore` | `ask`；先快照現狀；還原後跑遷移再跑變動偵測 |
| 搬家 | build | `kb relocate` | `--check` 列出所有絕對路徑引用；改完重開 |
| 換電腦 | build | 引導式，組合備份、裝 plugin、還原、搬家、環境檢查、信任、變動偵測 | 每步 `--check` 過了才下一步 |
| 換帳號 | build | `kb env --account` | 逐項印 plugin、信任、synced 技能、MCP 授權、隱私設定；固定一句「舊帳號處理過的不會刪」 |

## 五、載入模型

不需要的不載，靠這四層，不靠拆 plugin。

| 層 | 內容 | 何時進 context |
|---|---|---|
| 常駐 | 六個 skill 描述、`rules/core.md`（由開場注入帶進來，D-087）、總資料夾 CLAUDE.md、開場狀態行（項目見 D-060） | 每題。有預算，測試守 |
| 觸發 | `SKILL.md` 本文，只做分派，有行數上限 | 該 skill 被叫到 |
| 按需 | `references/` 與 `rules/` 其他檔，本文依來源型別與設定決定讀哪些 | 遇到才讀 |
| 程式 | `kb.py` | 執行時只付輸出 |

總資料夾的 CLAUDE.md 不引用 plugin 檔案，`rules/core.md` 全文與狀態行由開場 hook 注入（D-087）；對話摺疊後 SessionStart 以 matcher compact 重跑同一段注入（I-036）。SKILL.md 直接用 `${CLAUDE_PLUGIN_ROOT}`。不寫 plugin 路徑進任何狀態檔。

## 六、四層怎麼紮實

**資料層。** 取 plugin 為主體。來源登記表只准程式改，位置存可攜 spec 不存絕對路徑（D-021）；排除分「不想讓 AI 看到」與「用不到」，另有程式層底線：憑證檔名樣式（`.env`、`*password*`、`密碼*`、`id_rsa*` 等）寫死不進設定；預設擋、個案裁示放行、總資料夾內部豁免、對話裡的帳密走憑證管理員（D-081）；清冊是狀態機，主鍵是第一次看到時產生的 id，位置是帶型別的定位字串清單（`file://`、之後 `mail://`、`kb://`），指紋有歷史，`doc_index` 併入；重複檔一張卡列多個位置；檔案卡檔頭帶 id、指紋、範本版本、出處類別（客戶原檔、對話收件、AI 產出）、對話收件另帶收件日期與回合代號、卡型、掛哪些實體，另渲染改自（清冊 derived_from）與掛在自己身上的裁示（rulings）；綜合卡檔頭帶 id、型別、大類、名稱、別名、狀態、範本版本與渲染快取（檔案卡清單、相關實體、日程、待處理）；實體清單在 `entities.json`，型別住客戶設定檔，各標四大類之一；出處與依據的卡 id 格式固定，程式可反查標待更新；索引由程式產生。客戶在檔案總管改實體夾名、改綜合卡名、刪實體夾，由開場變動偵測認出並問一句（D-057）；型別改名與刪型別走 note（D-058）。每個狀態檔帶 `schema_version`。`profile.json` 與 `系統/客戶設定檔.md` 雙向：同一支解析器，md 較新就列差異問客戶（D-052）。密碼與金鑰不進任何狀態檔，走憑證管理員（D-050）。

**事務層。** 取 plugin 幾乎不動。每句話一個回合，目前回合按 session 分（D-074），備份、日誌、摘要、大改動計數掛同一代號；守門按第九節路徑表決定備份、登記或擋，攔檔案工具與指令字串，認得 MCP 工具名，被擋留痕；大改動達門檻擋，客戶同意後 `kb turn approve` 解除這一回合（D-075）；退回只從最近一次沒退過的開始，退回本身走同一個備份所以可再退；併入讀檔守門；待處理為 JSON；退回是規則加腳本；備份有保留期由 lint 清。第一版不以 git 為前提。

**規則層。** plugin 給結構，llm-wiki 給紀律。三種規則物理分開：plugin 通用規則隨 plugin 升級；客戶設定每條標日期與來源（訪談、推測、確認、糾正），規則不論建置時或使用中加的都住這；裁示是個案不是規則，掛具體檔或實體，帶日期與原因，數量封頂，同類到門檻由 lint 提議升格成設定檔一條（D-041）；事實的更正寫綜合卡不寫這兩處。`rules/core.md` 唯一常駐，十二條編號 R-01 到 R-12（清單在 D-059），其中出處優先序：檔案高於使用者陳述高於推導，衝突時兩者並存各標出處、回答同時講兩邊；被兩個以上 skill 讀的放 `rules/` 按需。帶頻率的規則必有上次執行欄位。對話歧義寫原則加例子，不寫案例表。事故不住規則。核心規則裡不出現任何功能名。

**維運層。** 取 llm-wiki 機制，接 plugin 的內容健檢。`kb lint` 一個 runner 加可插檢查項，兩種輸出：客戶一句白話、服務人員數字。守門留痕，太久沒留痕報失聯，趨勢只報變差。升級走 `kb upgrade`，`lint/checks/skeleton.py` 只唯讀呼叫同一函式；`drift.py` 查的是客戶總資料夾的檔案引用了不存在的東西。環境檢查缺什麼降級並明講。測試案例從 `incidents.md` 來。CHANGELOG 是版本唯一真相。

## 七、使用者特質

訪談由服務人員建置前填客戶設定檔，或 build-setup 現場問（D-052）；能從資料推的程式推、標推測（D-051）。答案寫進設定，決定開哪些 references 與旗標。程式都一樣，差的是設定。

| 特質 | 決定什麼 |
|---|---|
| 資料在本機、雲端同步、隨身碟 | ingest 讀哪份 reference、占位檔、路徑可攜 |
| 從頭、既有資料夾、本來就有 wiki | build 走哪條路：從頭的 setup 後直接 finish，實體夾就是他的 raw（D-044） |
| 單人、共用夾、多人、多 session | collab 旗標、擁有者欄位、共用夾提醒 |
| Office、Google 文件、掃描、舊格式、加密 | 轉換器、環境檢查項、事先告知讀不到的；加密檔客戶授權才解，密碼進憑證管理員（D-050） |
| M365 桌面、網頁版、LibreOffice、無 | produce 的 Office 能力與降級 |
| 哪一行 | 實體型別表（先猜再改）與建議段落組（訪談一句），住客戶設定檔 |
| 要排除的、額外敏感種類、可記的公司資料 | 客戶設定檔的內容，不是架構 |

## 八、延伸

新功能先問：帶來新的真相來源嗎？新的輸出去處嗎？還是只改變誰在用、怎麼用？答案落在六個槽位。

| 槽位 | 加什麼 | 常駐成本 |
|---|---|---|
| 新來源型別（Email、行事曆、Line、Drive、錄音、另一個知識庫 `kb://`） | `kb/sources/<型別>.py`、`ingest/references/<型別>.md`、envcheck 一項、測試資料一種情況、路由表加句 | 零 |
| 新輸出去處（寄信、建事件、推 Line、PowerPoint、Google 文件） | `kb/sinks/<型別>.py`、`produce/references/<型別>.md`、登記規則 | 零 |
| 新模式（collab、團隊共享、排程健檢、主動推送） | config 旗標、開場 hook 一段、資料檔、lint 一項，必要時一個 skill | 旗標關就零 |
| 新意圖群 | 先塞既有 skill 的 reference；塞不進才開第七個 skill | 一行描述 |
| 新外部動作（任何改外部狀態的 MCP 工具） | 守門第四條路、回合帳 | 零 |

MCP 來源的邊界：**Claude 取、腳本記**。MCP 工具只有 Claude 叫得到，skill 教 Claude 抓內容，再交 `kb ingest push` 做指紋與登記。主動推送在 session 外沒有 hook，只能由外部排程跑 `kb agenda brief` 產文字再送。

補漏洞型的延伸（OCR、圖片製卡、舊格式自動轉、本地搜尋索引、索引拆年份、舊卡補欄位、API 製卡、Mac）全部落在既有位置，不加分類。清單在 `backlog.md`。

**什麼時候加 skill：** 有一組客戶會說的話對不到六個描述、進來後要判斷、常駐預算還有餘額，三個都成立才加。**該拆：** 本文超過上限、描述要列兩組不相關的話、出現兩種真相來源。**該併：** 兩個描述在路由表上撞、本文只剩「去讀某份 reference」。**該搬 `rules/`：** 一份 reference 被兩個以上 skill 讀。

**什麼時候加文件：** 出現一種內容沒有家。目前沒有預見的。給客戶看的文件走 skeleton 或 build 生成，不進 design。

**加功能的步驟：** 判定槽位寫進 decisions；查路由表加句並確認只對一個入口；查六個面要不要補告知；加轉接器或旗標，不改核心規則；加 envcheck、lint、測試資料；量常駐成本。

## 九、目錄

```
llm-wiki-j/
├─ .claude-plugin/plugin.json
├─ .claude/settings.json              放行測試與 kb.py
├─ README.md  CLAUDE.md  CHANGELOG.md  LICENSE  .gitattributes  .gitignore
├─ rules/
│  ├─ core.md                         唯一常駐，每條有編號
│  ├─ synthesis.md                    綜合卡連帶處理，ingest／answer／note／lint 共讀
│  └─ next-step.md                    建議下一步，build／answer／lint 共讀
├─ config/defaults.json
├─ skills/
│  ├─ ingest/SKILL.md + references/{local,external,formats,changes,carding,versioning}.md
│  ├─ answer/SKILL.md + references/{find,verify}.md
│  ├─ note/SKILL.md + references/{preference,agenda,correction,ruling}.md
│  ├─ produce/SKILL.md + references/{draft,office,placement,export}.md
│  ├─ lint/SKILL.md + references/{content,system,questions}.md
│  └─ manage/SKILL.md + references/{sources,files,ops}.md
├─ commands/                          全部 disable-model-invocation
│  ├─ build.md                        建置五步一個指令（D-190）
│  └─ upgrade.md  backup.md  restore.md  relocate.md  migrate.md  account.md
├─ hooks/{hooks.json,session.py,turn.py,guard.py,output_log.py}
├─ agents/card-maker.md
├─ scripts/
│  ├─ kb.py                           唯一入口
│  └─ kb/
│     ├─ cli.py
│     ├─ init.py env.py inventory.py changes.py update.py batch.py convert.py reindex.py
│     ├─ versions.py source_edit.py folders.py ai_output.py intake.py office.py agenda.py profile.py entity.py
│     ├─ upgrade.py backup.py relocate.py guard_status.py search.py unlock.py
│     ├─ sources/{file.py,...}  sinks/{...}
│     ├─ lint/runner.py + checks/   十七項見 D-064
│     └─ _lib/{paths,io,hash,config,sources,turn,journal,migrate}.py
├─ templates/{card,synthesis,build-report,health-report}.md + selfcheck/{check.docx,check.xlsx}   卡範本兩份固定（D-037）、報告範本兩份（D-066）；卡型、實體型別、客戶格式調整都是客戶設定檔裡的建議段落組
├─ skeleton/
│  ├─ .skeleton-policy
│  ├─ CLAUDE.md
│  ├─ .claude/settings.json           只有 deny 憑證樣式、ask 高風險子命令；不放 additionalDirectories、不放 plugin 路徑（D-073）
│  ├─ AI 產出/  收件匣/  檔案索引/    空夾；型別夾由 build-setup 依設定檔建
│  ├─ 總目錄.md                        我的檔案：可摺清單連到檔案卡（D-105）
│  └─ 系統/{待處理,日程,客戶設定檔,裁示,建置報告}.md + 工作日誌/ 健檢報告/
├─ tests/
│  ├─ test_lib_*.py  test_hooks_*.py  test_scripts_*.py  test_lint_checks.py
│  ├─ test_skeleton_check.py  test_budget.py  test_coverage.py  test_version_sync.py  test_guard_cli.py
│  ├─ evals/<skill>.json                 每個 skill 20 句觸發測試（D-091）
│  └─ fixtures/                       含每份渲染檔的範例輸出（D-067）
└─ design/
```

客戶端：

```
知識庫/                               總資料夾。客戶打得開，名字建置時客戶取，預設「知識庫」。開對話開在這
├─ <型別>/                            型別夾，名字訪談填，各標四大類之一：對象、事、物、主題
│  └─ <名稱>/                         一實體一夾
│     ├─ <名稱>.md                    綜合卡，實體夾裡唯一 AI 可寫的檔
│     └─ <其他檔>                     客戶說放這才有；放進來就是原檔，AI 只讀
├─ AI 產出/<型別>/<日期> <實體> <標題>.*   草稿 md 與定稿都先落這；沒實體的放主題子夾
├─ 收件匣/                            對話收到的、客戶自己丟的；問要放哪，沒說留著
├─ 檔案索引/<來源別名>/<鏡射相對路徑>/<標題-id>.md   檔案卡，一檔一張，位置程式排；來源別名含知識庫、AI 產出、收件匣；檔頭一行 file:/// 開原檔
├─ 總目錄.md                          我的檔案：程式渲染的巢狀可摺清單，每項連到檔案卡（D-105）
├─ 系統/                              程式渲染，客戶可開
│  ├─ 待處理.md  日程.md  工作日誌/YYYY-MM.md
│  └─ 客戶設定檔.md  裁示.md  建置報告.md  健檢報告/
└─ 隱藏屬性：CLAUDE.md  .claude/settings.json  .system/
   .system/                           程式獨占，每個 json 帶 schema_version
   ├─ sources.json  inventory.json  pending.json  profile.json  agenda.json  rulings.json  entities.json
   ├─ config.json  env.json  skeleton.json  upgrade-history.json
   ├─ turns/<session>.json  versions/<回合>/  snapshots/<時間>/
   ├─ guard-status/<機器>.json  ledger.tsv  hook-errors.log
   └─ temp/  node/

客戶原檔區：在外面，位置是客戶原本的，只讀
```

守門路徑表（D-046）：

| 路徑 | AI 寫入 |
|---|---|
| `<型別>/<名稱>/<名稱>.md` | 回合備份；指令寫擋 |
| `<型別>/<名稱>/` 其他檔 | 擋；只能由 `kb source-edit move` 搬入，登記 |
| `AI 產出/` | 回合備份加登記；指令寫擋 |
| `收件匣/` | 擋；只有 `kb intake` 能寫 |
| `檔案索引/` | 回合備份；指令寫擋 |
| `系統/` | 程式渲染；手改由 lint 比對（D-015） |
| 總資料夾 `總目錄.md` | 擋；只有 `kb cards index` 等程式渲染（D-105） |
| `.obsidian/` | 擋；只有 `kb init` 與 `kb upgrade` 寫，骨架對帳只補缺不覆蓋（D-105） |
| `.system/` | 擋；只有 kb 腳本 |
| `.claude/settings.json` | 擋；只有 `kb folders` |
| 總資料夾 CLAUDE.md | 擋；只有 `kb upgrade` |
| 客戶原檔區（登記未排除） | 寫擋；讀 allow |
| 客戶原檔區（排除） | 讀寫都 deny |
| 其他路徑 | 不表態，交回預設 |
