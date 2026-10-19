# CHANGELOG

一版一段、最新在上。每段四件事：改了什麼／為什麼／commit／spec。
bump `plugin.json` 版號的那個 commit 要同時寫這裡，不事後補；事後補就會變成第二份會漂的清單。
為什麼只寫 spec 或決策裡有的，查不到寫「待補」，不推測。

## 0.11.0 — 2026-10-19
- **改了什麼**：實體與綜合卡。新狀態檔 `.system/entities.json`（schema 1）記客戶管的每個案子、業主、廠商：編號 `e-`、型別、名稱、別名、來源（推測／確認…）、有沒有綜合卡、active／removed／merged。檔案卡範本第二版多「實體」（掛登記的名稱）與「待補型別」兩欄；第一版的舊卡不重做，進 `kb cards todo` 的「待掛實體」，製卡者讀卡補檔頭；原檔在實體夾裡的預填那個實體。同一個實體掛到 `synthesis_min_cards`（2）張檔案卡、客戶確認過名字，製卡者在 `<型別>/<名稱>/<名稱>.md` 寫綜合卡（範本 `templates/synthesis.md`、寫法 `rules/synthesis.md`：現況改寫、紀事只增、推算帶依據與日期、每點〔編號〕出處、兩份檔寫法不同兩句並列）；檔頭「整理到」記讀過哪些檔案卡，新檔掛上來、依據卡重做或拿掉就進「待更新綜合卡」同一次整理改；「檔案卡」「相關實體」兩欄程式重寫。新子命令 `kb entity add|confirm|rename|merge|remove|rebuild|clean|list|render`：改名與合併連所有檔案卡的實體欄、綜合卡檔頭與夾一起改，先驗完才動手、中途失敗還原。PostToolUse 驗綜合卡檔頭（未確認、客戶自己、路徑錯…打回）。客戶在檔案總管改實體夾名、改卡名、刪夾：開場變動行多「實體夾有變 N」，AI 問改名還是改回來、刪錯了請從回收筒還原或照檔案卡重寫。設定檔 schema 2：第四節「每類最想知道什麼」（新型別套用時填大類預設段落）、第五節「關於你」（客戶自己的公司與本人，不建綜合卡、不算相關實體）；`kb init` 對既有知識庫把設定檔升級；第一節拿掉的型別它在第四節的列一起丟。型別表沒有的實體不登記、記進待補型別，AI 請客戶補列，補了再掛。程式重寫綜合卡不算 D-075 大改動，數的是 AI 寫過的綜合卡。回答末尾一次最多問 3 個問題。`rules/core.md` R-08 改成兩份檔寫法不同時現況並列。新 config `synthesis_min_cards`。1163 測試。
- **為什麼**：檔案卡回答「這份檔是什麼」，客戶問「信義王現在怎樣」要一頁講清楚、每句點得回出處（D-036、D-038）；只出現一次的不建、免得長出跟檔案卡重複的薄卡（D-114）；新實體先登記、建卡前才問名字，客戶答得出來、開場自動整理不被打斷（D-115）；升級不重做卡（D-054、D-116）；AI 沒有退得回來的刪除與改名，錯的夾交 `kb entity` 修、守門不動（D-117）；快取只放有真資料的兩欄（D-118）；預設段落住 JSON 一份客戶看得到（D-119）；客戶自己的公司出現在每張卡，比例分不出它跟最大的案子，改問「你的公司叫什麼」（D-120、I-079）；這版沒有 answer，只標待更新不改會一直停在舊現況（D-121）；事類狀態與期間跟日程一起定（D-122）；型別表只由人定（D-124）。測試兩輪：第一輪 19 條全過，實跑抓到程式先備份讓大改動門檻失效、改名被擋留一半、合併後卡變 broken 等 8 項，第二輪 9 項全過；確認 10 步 8 過、2 步是 spec 預期跟環境對不上；英文自言自語（B-064）不是這題的退步，留日程第一題處理。
- **commit**：fd2e6b6、3fe5844、b49aa32、b7d027e、b076dce、b5f58a5（實作，分支 spec/實體與綜合卡，merge fc9886a）
- **spec**：design/specs/2026-10-19-實體與綜合卡.md

## 0.10.0 — 2026-10-19
- **改了什麼**：客戶設定檔與型別。`.system/profile.json`（schema 1）與 `系統/客戶設定檔.md` 一份三用（D-052）：`kb init` 寫預設 JSON 並渲染出三張表（一、型別：名稱、大類四選一、一句說明；二、卡型與段落：八種預設卡型各四段，必出現打 ✓ 的段沒內容卡上寫「未知」；三、不整理與敏感：九種個資預設只記種類），人用記事本改、程式讀回。新子命令 `kb profile check`（列差異、不在型別表的第一層夾、讀不懂的節與列）與 `apply`（驗證型別名：保留字、Windows 保留名、不能用的字元、40 字；先建型別夾再寫檔、兩檔備進回合；表少了一列而夾空就連空夾拿掉、夾裡有東西就整份拒絕；第三節拿掉的種類把跳過的檔放回待製卡）。開場多一行「設定檔有改（型別 +1）：先 kb profile check 複述，客戶說好才 apply」或「第一層夾「X」不在型別表」、「有改但讀不懂（第 N 節第 M 列…）」；md 改沒改用內容指紋比，不用 mtime。`carding.md` 不再寫死卡型、段落與個資種類，製卡者從 `kb cards todo --json` 的 `profile` 塊拿；設定成「不整理」的種類由製卡者讀後 `kb cards skip <編號> --kind <種類>`（清冊 `skip_kind`、有卡就拿掉、todo 不再列）。表格中間夾非表格行報錯不靜默切斷；`versions revert` 退掉 apply 時提醒建的夾還在。1052 測試。
- **為什麼**：型別是實體與綜合卡（B 題）的上層，先定下來（D-108）；只收這版有人讀的三節，沒人讀的格式等於先猜一次再改一次（D-109）；AI 不能寫 `系統/`，「人改、程式讀、AI 只問」是現成的路（D-110）；三節同形狀一支解析器、預設值住 JSON 不住規則檔（D-111）；客戶自己建夾是正常使用，程式只能認出來問、不能替他定大類（D-112）；不整理的檔不該出現在知識庫任何地方，假卡會進總目錄（D-113）。測試三輪：第一輪 15 條全過但 code-review 抓到 apply 先寫檔才建夾、Windows 保留名、skip 沒鎖；確認 11 步 1～9 過，改名留下的空夾每次開場提醒（I-045 同類）改成一起拿掉，殘尾行靜默切表改成報錯；第 10 步是 spec 排了退不到的回合（D-074），不算不過。
- **commit**：cdb4ae2、436fa21、d10610a、f3311ba（實作，分支 spec/客戶設定檔與型別，merge 36f3db5）
- **spec**：design/specs/2026-10-19-客戶設定檔與型別.md

## 0.9.0 — 2026-10-19
- **改了什麼**：ingest 製卡。第二個 skill `ingest`（`/wiki-with-willy:ingest`）：開場有「變動」行時 AI 不用客戶開口就整理（份數在 `ingest_auto_max` 5 以內直接做、超過先問）、客戶說「我加了」「我改了」「我刪了」「搬了」也走它；原檔不見的問一句，客戶說刪了就 `kb cards drop` 連卡一起拿掉（回合備份可退）。新子命令 `kb convert`（docx／xlsx／pptx 用 zipfile 加 xml.etree 抽成純文字，xlsx 每列帶列號、公式沒快取值標出來；txt／csv 整份偵測編碼；PDF 走唯一的選配套件 `pypdfium2`：逐頁抽文字層、掃描頁轉 PNG 給 AI 看圖，沒裝就「讀不到：還沒裝 PDF 元件」進待處理、裝好自動回到待製卡；舊格式、加密、不支援、被 Office 鎖住各有一句原因；開檔前用守門同一組函式自己擋排除夾、憑證檔名、不在登記來源、不是原檔區，不收 `--vault`）、`kb cards todo|sync|drop|index`（程式算哪些檔沒有卡或該重做、搬動的卡跟著搬、同編號多張收斂、根目錄 `總目錄.md` 渲染成巢狀可摺清單連到每張卡）、`kb pending list|resolve|dismiss`（`.system/pending.json` 待處理最小版：原檔不見與讀不到兩種，渲染 `系統/待處理.md`，客戶答過的不重問）。檔案卡：範本 `templates/card.md`，檔頭中文鍵名（編號、出處、原檔、指紋、內容指紋、標題、卡型、文件日期、讀取、製卡日期、範本版本、敏感資料種類、開原檔 `file:///` 絕對路徑），正文建議段落每點帶〔位置〕，放 `檔案索引/<來源別名>/<鏡射路徑>/<標題-編號>.md`；PostToolUse 驗檔頭（編號在清冊、指紋是現行、檔名與夾照規則、型別對），錯了 `decision: block` 把原因回給 AI。開場狀態行多一行只給模型的整理指示。`skills/manage` 加資料夾收尾改問「要我現在整理成卡嗎」。新 config `ingest_auto_max`、`pdf_read_pages_max`、`pdf_page_min_chars`、`pdf_render_scale`、`card_source_big_chars`、`card_title_max_chars`、`temp_keep_hours`。README 環境需求加 `pip install pypdfium2`（選配）。964 測試。
- **為什麼**：六個面裡「答與出處」要先有檔案卡（D-098）；核心只用標準函式庫、PDF 原想交給 Claude 的 Read，確認時踩到 Read 讀 PDF 要本機 poppler、客戶裝不了（I-008），改成 pypdfium2 一個選配（D-099 修訂）；日常增量主對話讀得起、開場 skill 要一句指示接起來（D-100）；卡是客戶打得開的東西，鍵名中文（D-101）；「要我決定什麼」要有客戶看得到的地方（D-102）；刪檔就是不要了（D-103）；哪些檔沒卡由程式算、檔頭由 hook 驗才是機器保證（D-104）；總目錄與開原檔接 D-105。測試五輪：第一輪抓到 `kb convert --vault` 指到假總資料夾能繞過排除與憑證、配假清冊做路徑穿越、假 temp 做 junction 刪原檔（I-111 補：kb 腳本信任的根只能從 cwd 推），與原檔被 Office 鎖住就炸；第二輪修正 18 項過；第三輪 PDF；第四、五輪開場指示字串。確認 14 步：13 步過，「先答客戶這句再整理」三種指示行都沒用、記 B-058 交 answer 題；manage 路由 eval 83～87% 描述沒動照 B-054 接受。
- **commit**：8c26ef4、e87c248、d43c860、f67b538、07eab14、6b30ee9、b75536a（實作，分支 spec/ingest製卡，merge d712fba）
- **spec**：design/specs/2026-10-17-ingest製卡.md

## 0.8.0 — 2026-10-17
- **改了什麼**：清冊。`.system/inventory.json` 從只記 AI 產出的最小版長成 schema 2（位置是物件帶大小時間、`read` 與 `status` 分開、同指紋多位置一筆、舊檔讀到時自動遷移）。新子命令 `kb inventory scan`（全量掃描登記的來源與總資料夾三區，把新增、修改、搬動、重複、不見套進清冊；寫前備進回合、終端機跑記程式段）與 `kb changes`（跟上次掃描比、只看不改、`--quick` 走開場同一條路）。開場狀態行多「變動：新增 N、修改 N、不見 N」（搬動與重複不印、全 0 不印）與「這台電腦找不到來源「X」」（按機器記進 `.system/env.json` 只報一次、找回就清）；快速模式只比大小時間、指紋受讀取與時間預算、算到一半也停得下來。docx／xlsx／pptx 多算內容指紋（標準函式庫 zipfile 加 xml.etree）：打開再存、LibreOffice 重存不報修改。登記與清冊同一筆交易：`folders add` 登記完就入冊、`exclude --kind hidden` 整筆拿掉不留檔名、`unused` 與 `remove` 標 excluded、`include` 恢復或當新增，`sources.json` 與 `inventory.json` 一起備進回合、退回一起回，清冊那段失敗會把 `sources.json` 寫回。走訪跳隱藏與系統屬性、暫存名、重解析點與連結區，開不了的夾記錯誤繼續走；憑證樣式檔與占位檔入冊但不算指紋；超過 `fingerprint_max_bytes` 只比大小時間。`versions list|revert` 在清冊讀不到時仍能退。新 config `fingerprint_max_bytes`、`changes_quick_hash_budget_bytes`、`changes_quick_seconds`。791 測試。
- **為什麼**：ingest 開頭必跑 `kb changes`，沒有清冊製卡沒得起頭（D-092）；位置與狀態、在不在與讀不讀得到是不同的問題（D-093）；開場有逾時，只比大小時間便宜，指紋留給真的要判的，找不到的來源天天報會訓練客戶不看（D-094、I-045）；一件事一筆交易（D-095）；客戶「打開看一下再存」是日常，不處理開場天天報修改（D-096、I-015）。測試兩輪：第一輪 17 條驗收全過但實跑抓到開場算指紋停不下來（11.7 MB xlsx 改一格開場 14 秒超過 hook 逾時）與壞 OOXML 炸掉兩個高，第二輪修正後 5.3 秒、無退步；確認 12 步全過，Microsoft 365 重存那半未驗。
- **commit**：4305e7d、df5a78b、82145e6（實作，分支 spec/清冊，merge 11a9bd7）
- **spec**：design/specs/2026-10-16-清冊.md

## 0.7.0 — 2026-10-16
- **改了什麼**：第一個 skill `manage`（`/wiki-with-willy:manage`）：客戶在對話裡一句話加資料夾、不讀某夾、拿掉來源、取消排除、看現在讀哪些；搬檔、改名、搬家、重建這版做不到就明說怎麼自己做。本文是「客戶說／AI 做法」表（沒路徑請客戶貼不猜、先 `--check` 報數字再加、不讀某夾先查登記再問兩種、拿掉只認明確確認、搬家先 check 新路徑再換），`references/sources.md` 放子命令與拒絕原因。新子命令 `kb folders remove <別名>`、`include <路徑>`（只改 sources.json、先備進回合、記程式段）；`include` 的拒絕句分三種。`tests/evals/manage.json` 20 句路由測試，兩套 plugin 都在的機器上該觸發 30/30、不該 0/30。客戶端 CLAUDE.md 加「回答客戶一律繁體中文」。624 測試。
- **為什麼**：常駐層齊了客戶還是碰不到任何功能，manage 程式幾乎現成、能第一次驗證「skill 只做分派、描述 150 字能路由」（D-089～D-091）。確認時 AI 全程英文，core.md 的 R-10 送到了沒照做，CLAUDE.md 是專案指示比注入強（D-080 補）。測試四輪、確認一輪加補驗。
- **commit**：bf34ed9、c245597、7d74891、759a353、f38382f（實作，分支 spec/manage，merge 6074bb6）
- **spec**：design/specs/2026-10-16-manage.md

## 0.6.0 — 2026-10-16
- **改了什麼**：開場注入與核心規則。新 `rules/core.md` 十四條（R-01～R-14：三種真相分開、出處優先序、每句帶出處、不改原檔、退回走 `kb versions`、不猜、全庫操作不代跑、綜合卡寫法、外部動作先確認且帳密不落檔、對人中文對程式英文、建議下一步、被擋不繞路且同一回答做完、工作日誌的 `kb turn note`／`redact`／`journal recent`、kb 程式用 Bash 一次一個指令）。新 SessionStart hook `hooks/session.py`（startup｜resume｜clear｜compact）把 core.md 全文與狀態行塞進 additionalContext，狀態行第一版只有 `kb.py` 完整呼叫前綴與摺疊提示；hook 找到知識庫後出錯會印一行「開場程式出錯」。`skeleton/CLAUDE.md` 加哨兵句：沒看到開場段就是程式沒跑起來，AI 可查 python、客戶同意才裝、期間不改檔。回合的問剝掉 `<pasted_content>` 標籤。592 測試。
- **為什麼**：常駐層一直缺規則與程式位置，AI 要被點名才會跑 `kb.py`、沒拒絕代登入（B-031）；規則在對話裡才是機器保證，給路徑靠模型記得讀（I-036、D-087）；python 不在時偵測它的 hook 自己跑不起來，能活在外面的只有 CLAUDE.md（I-103、D-088）；D-059 補 R-13、R-14。測試兩輪，第一輪 review 抓到剝標籤黏字讓短關鍵詞遮不到、子代理會誤判哨兵句、hook 自己出錯被當成 python 壞掉。
- **commit**：e29c949、6b5e83c（實作，分支 spec/開場注入與核心規則，merge efb4422）
- **spec**：design/specs/2026-10-16-開場注入與核心規則.md

## 0.5.0 — 2026-10-16
- **改了什麼**：工作日誌。每句話一段記進 `系統/工作日誌/YYYY-MM.md`（真相是 `.system/journal/YYYY-MM.jsonl`，md 整月重渲染、最新在上）：問在 UserPromptSubmit 寫、結果由新的 Stop hook 從 payload 取（300 字、結論或摘要段優先、「…全文見對話」）、做了是程式地板句或 `kb turn note` 的 AI 一句、改了從回合帳抓；按 Esc 中斷的由下一句補成「（回答被中斷）」。程式自己做的事（`kb init`、`folders add|exclude`、`versions revert|delete-outputs`）在終端機跑時各記一段，對話裡叫的併進那句。新子命令 `kb turn note|redact`、`kb journal recent|render`。退回動作一律開新回合並成為目前回合，對話裡退回自己這句時新回合繼承問、做了、同意並帶 `journal_turn` 指回那句的日誌行。遮值一律先壓空白再遮，全文有遮到就不抽結論段；`prompt_head` 長度進 config（200）。573 測試。
- **為什麼**：六個面的「剛做了什麼」要有客戶看得懂、永久留的地方（D-065、D-082～D-086）；Stop 的 payload 直接帶最後回答，不解析 transcript（D-083）；規則不是保證，做了要有程式地板（D-084）。測試四輪，三輪在修退回動作回合與目前回合的糾纏（I-107）。
- **commit**：e36bbc4、e3834fb、270459e、161578e、464bd5a、0defa7d、0c793f7、1d633af（實作，分支 spec/工作日誌）
- **spec**：design/specs/2026-10-15-工作日誌.md

## 0.4.0 — 2026-10-15
- **改了什麼**：骨架與來源登記。`skeleton/`（CLAUDE.md 只寫身分與五區、settings.json 空殼）；`kb init <路徑>` 建五區、`.system/sources.json`、隱藏屬性，既有檔不覆寫；`kb folders add|exclude|list` 登記來源與排除（拒絕磁碟根、系統夾、互相包含、另一個知識庫、保留別名；spec 壓成相對→`~/`→絕對；排除對不到就拒絕；寫前備份進回合可退）。守門加憑證檔名樣式三道：目標路徑的段、指令裸 token、讀取範圍是資料夾時走訪（上限 `credential_scan_max_entries`，超過擋）；總資料夾內部豁免；理由句「客戶沒說可以讀」。回合 `prompt_head` 程式遮值（密碼、帳號、token 等關鍵詞後整個子句）。守門舊洞順手補：不帶斜線的 `.`／`..` 經區判斷、`cmd /c` 的 `/c` 不當路徑、大括號與字元類的前綴夾保底、寫入動詞清單補。439 測試。
- **為什麼**：ingest、第一個 skill、桌面版確認都要一個 plugin 自己建出來的總資料夾（D-077、D-078、D-080）；一登記來源就立刻可讀，raw 裡的密碼表會跟著開放（I-032、D-079）；密碼是客戶一件一件決定的事，不是開關（D-081）。測試十二輪，八輪是上一輪 shell 字串解析的修正帶出的退步，最後把區判斷的寫入判定退回守門題原版（I-106）。
- **commit**：ff46b8e、30241f4、fd50114、0e6a2c9、79d7c5e、76e61d1、fdff607、1b973ac、2b1ad4f、0ef6ac4、ccb8979、bb98026、0eff88b、18b6b8c、783f82b、d8892f6（實作，merge 9b7af0b）
- **spec**：design/specs/2026-10-14-骨架與來源登記.md

## 0.3.0 — 2026-10-13
- **改了什麼**：事務層。UserPromptSubmit 每句話開一個回合（按 session 存 `.system/turns/`）；PreToolUse 寫綜合卡、AI 產出、檔案索引前先備份到 `.system/versions/<回合>/`，指令寫這三區改擋；PostToolUse 把 AI 產出登記進最小版 `.system/inventory.json`；`kb versions list|revert|delete-outputs`、`kb turn current|approve`；同回合修改既有綜合卡達 10 張就停（`config/defaults.json`）；`_lib/io` 加檔案鎖與 os.replace 重試；298 測試。
- **為什麼**：守門已放行寫入卻沒備份，等於能寫不能退（D-074）；成品要有登記才查得到、退得掉（D-076、D-011）；一句話改太多張要先問（D-075、I-028）。測試三輪補了退回與刪成品的語意洞（H1、R2-1～R2-4）。
- **commit**：fb7041a、b82aae5、472dc7a、f94f37d、2d64c9c（實作）
- **spec**：design/specs/2026-10-13-事務層.md

## 0.2.0 — 2026-10-13
- **改了什麼**：PreToolUse 守門 `hooks/guard.py`：客戶原檔讀放行寫擋、排除夾讀寫都擋、總資料夾五區照路徑表、`kb.py` 呼叫免詢問；`kb guard decide` 子命令；`.system/ledger.tsv` 事件帳、`guard-status/<主機>.json`、`hook-errors.log`；sources.json schema；路徑正規化與指令字串解析；142 測試。
- **為什麼**：讀客戶 raw 不能靠 permissions 規則（B-006～B-008 實測），守門 hook 回 allow／deny 是唯一在 auto mode 也有效的機制（D-073）；三輪測試補了七個高中嚴重的繞法。
- **commit**：7f816c5、2436d2d、583ae05、a34d57a（實作）
- **spec**：design/specs/2026-10-12-守門.md

## 0.1.0 — 2026-10-12
- **改了什麼**：repo 與 plugin 最小殼：manifest、LICENSE、README、CLAUDE.md、`kb.py` 入口與 `version`、`_lib/io`、五個測試檔、git 初始化。
- **為什麼**：後面每一題都在這個殼上長；預算與版號對齊從第一天就由測試守。
- **commit**：773954d（實作）
- **spec**：design/specs/2026-10-12-建殼.md
