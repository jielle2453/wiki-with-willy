# 建置的每一步

步名對照（`kb build done|redo <步>` 用左邊的）：env 環境、setup 訪談、inventory 盤點、sample 抽樣、cards 全量整理、names 確認名字、synthesis 綜合卡、adopt 收舊 wiki、finish 交付報告。跟她說話只用中文的步名。

每一步都是：先講要做什麼、問好不好 → 說好才做 → 做完跑 `kb build done <步>` → 講下一步、問要不要繼續（D-194）。

`build status` 印「另一個對話正在建置（…）」：照那句講；客戶或服務人員說「接手建置」才跑 `kb build status --takeover --json`，不自己接手（那個對話還開著的話兩邊會一起改）。

## 環境

- 跑 `kb build env --json`，八項逐項講：`ok` 的一句帶過；`warn`、`fail` 講是什麼、會怎樣、怎麼辦。
- 信任（trust）`fail`：請服務人員在這個資料夾開對話時按「信任」；沒按的話每一步都會被問、自動模式也會被擋。`warn` 是看不出來，問服務人員按過沒有。
- 智慧型應用程式控制（sac）：她問「要關嗎」→ 白話講：這是 Windows 擋沒有數位簽章的程式的保護，關了以後可能要重灌才開得回來；我們不建議關，被擋到的那項先不用或換方法，寫進報告；關不關她決定，我們不代關（D-189）。
- PDF 元件（pdf）帶 `fix`：講缺了的話 PDF 都讀不到、要不要現在裝（大約一分鐘、要網路、裝在這台電腦使用者自己的工具夾，不動系統）；說好才跑 `kb office setup`，裝完再跑一次 `kb build env --json` 看那一項。「被擋」的：照 sac 那段講，寫進報告。
- Office（office）：`warn` 講做不到的事（轉 PDF、重算 Excel），讀檔不受影響；帶 `fix` 的照 PDF 那段問要不要裝做文件的工具。
- 磁碟（disk）`warn`：講剩多少、建議先清出空間。
- 舊 llm-wiki（old_wiki）`fail`：兩套同時開會注入兩份規則、互搶；請服務人員到外掛頁停用 llm-wiki（舊的 vault 留著不動，只是沒有 AI 在顧），停好再跑一次 env 確認；`warn` 請服務人員到外掛頁看一眼是停用的。
- 還沒建知識庫時 `after` 是 `kb build start`：先講「要在這個資料夾建知識庫（幾個夾與設定檔）」，說好才跑。已經建好、又回到這步的，`after` 照它給的跑。

## 訪談

只問用得到的（D-191）。有事先填好的設定檔照 D-052 只問缺的。

- 先問有沒有事先填好的客戶設定檔：有就 `kb profile apply --from "<那份>"`；讀不懂照它回的節與列講，改好再跑一次。套好後複述讀到什麼，下面只問缺的。
- 資料夾在哪：每個 `kb folders add "<路徑>"`，先照 `--check` 講份數與有密碼的份數，說好才登記（拒絕怎麼講看 `${CLAUDE_PLUGIN_ROOT}/skills/manage/references/sources.md`）。
- 哪些不讀與理由：不想讓 AI 看到的 `kb folders exclude "<別名/相對路徑>" --kind hidden --reason "<她的話>"`；只是用不到的 `--kind unused`。
- 關於你（第五節）：公司或她自己叫什麼、有沒有別的叫法 → `kb profile add 關於你 名稱=<名> 是什麼=公司|本人 別名=<…>`。
- 只記種類不記值的（第三節）：預設已有身分證、帳號、密碼這些；問有沒有別的不想讓我抄下來的 → `kb profile add 不整理與敏感 種類=<名> 處理=只記種類`（整份不要整理的寫 `處理=不整理`）。
- 幾個人用：照她的原話 `kb build set people "<她的話>"`。
- 以前有沒有用過別的 wiki 或筆記：舊 llm-wiki 的照 `adopt.md` 第一節；其他筆記照一般資料夾登記。
- 不問：型別表與每類最想知道什麼（抽樣時問）、提醒幾天前（用預設）。
- 沒有資料夾的客戶：不用登記；`done setup` 後直接到報告（D-044），之後她說的話記在綜合卡，檔案放收件匣。

## 盤點

- 跑 `kb build facts --json`，講：每個來源幾份、主要是什麼格式、分成哪些夾；讀不到幾份與原因（有密碼的之後給密碼或說哪一份可以讀、舊格式另存新格式、只在雲端的按右鍵保留在這台電腦）；`estimate` 的時間照講，`usage` 有句子就加這一句。不講 token、金額、換算幾次問答（D-199）。
- 她說要做才 `kb build done inventory`。

## 抽樣

- 從 facts 挑 10 份左右：跨不同的夾與格式，避開讀不到的；從 `kb cards todo --json` 的 `new` 拿編號，跑 `kb cards dispatch --ids <f-…,f-…> --json`，照 ingest 的「讀派工結果」等它做完。
- 做完用 `kb cards list --json` 找到那幾張卡讀過，配上 facts，列一份「我猜的」給她看（D-192），每項一句：型別表（第一層夾像什麼：案子、業主、廠商…）、事類用的狀態詞、常見的卡型、哪幾個名字看起來是同一個。
- 她改的經 kb 寫：型別 `kb profile add 型別 …`、改名或合併 `kb profile type rename|merge … --check` 先看影響再做；實體 `kb entity rename|merge`；卡型 `kb profile add 卡型 …`。
- 再問每類最想知道什麼（第四節）→ `kb profile add 每類段落 型別=<型別> 段落=<段落> 提示=<她的話>`。
- 她說「重新抽樣」→ `kb build redo sample` 再從頭做這步。

## 全量整理

- `kb cards todo --json` 看 `plan`；份數多那題她在盤點時已經說好，直接做 `then` 的步驟。
- `kb cards dispatch --json` 照 ingest 的「讀派工結果」做到 `done`；`plan` 有 `cards` 那步（線上文件、密碼檔）照 ingest 自己做。
- 今天做不完：她說「先到這裡」照 ingest 的「客戶說停」做；隔天再打 build 就從這步接著做（D-190）。
- `done cards` 被擋「還有 N 份」：多半是還在跑或剛放進來的，再跑一次 dispatch。

## 確認名字

- `kb cards todo --json`：`plan` 的 `names` 每個照 ingest 的 carding.md 第十一節判斷，有把握的跑 `kb entity accept <名>`（記成 AI 判斷，D-202）。
- 有疑慮的：重跑 `kb cards todo --json`，`asks` 裡名字那幾題建置時一次全列（不受三題上限），每題一行：名字、掛幾份、你的疑慮（可能跟哪個是同一個、分不清是人還是公司）；她一題一題答，照那題的結果跑。
- 最後一句講直接登記了幾個、問了幾個，要看名單或有不對的跟我說（`tell` 有那句就照貼）。

## 綜合卡

- 到門檻的全部寫（D-201）：`kb cards dispatch --json` 照 ingest 做到 `done`；`approval` 那題照問她。

## 收舊 wiki

照 `adopt.md`。

## 交付報告

- `kb build report --facts --json` 拿數字與清單。
- 照 `${CLAUDE_PLUGIN_ROOT}/templates/build-report.md` 寫成白話：六個 `##` 標題都留著，沒內容的留空（程式會拿掉），「數字」那段留空（程式接上）；不寫檔案內容細節、英文或內部編號。用 Write 存到 `build status` 給的工作區路徑（`.system/temp/office/<回合>/建置報告.md`）。
- 跑 `kb build report --from "<那份>"`：說少了或多了哪一段就照改再跑。
- 對話裡講三句重點：資料去了哪、怎麼開始問、還有什麼要她決定；問她要不要現在試問一個問題。最後 `kb build done finish`。
