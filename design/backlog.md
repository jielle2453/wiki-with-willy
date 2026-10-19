# 待辦與延後

日期：2026-10-11
給誰看：你與所有 session。
什麼時候更新：任何 session 都能新增一行；改狀態只在設計 session。

格式：編號｜日期｜狀態 open／deferred／done｜一句話｜來自。

## 第一批要做的（順序）

| # | 日期 | 狀態 | 事 | 來自 |
|---|---|---|---|---|
| B-001 | 2026-10-11 | done | repo 殼（v0.1.0，2026-10-12 發行；specs/2026-10-12-建殼.md）：plugin.json、LICENSE、CHANGELOG 格式、CLAUDE.md 地圖、測試 runner、.gitattributes、.gitignore、settings 放行 | 討論 |
| B-002 | 2026-10-11 | done | 測試資料生成與 `來源.zip`：2026-10-12 完成 44 份，腳本 llm-wiki-test\生成\generate.py。.doc 是手寫 Word 97 檔未經真 Word 驗證，密碼 PDF 密碼 lin2026 | conventions 六 |
| B-003 | 2026-10-11 | done | 事故清單 `incidents.md`：從兩邊 CHANGELOG、註解、skill 事故段採集 | 討論 |
| B-004 | 2026-10-11 | done | 量兩邊常駐成本與規則行數，含開場 hook 注入（版本、變動清單、健檢提醒、日程窗口、規則路徑），定 U-6 的預算 | decisions 未決 |
| B-005 | 2026-10-11 | open | 架構文件初稿審閱（本批結束前你看一次） | 討論 |
| B-006 | 2026-10-11 | done | spike：測試 session 中途改 `.claude/settings.json` 的 additionalDirectories 或 Read 放行規則，看對工作目錄外的 Read 會不會立刻生效、要不要重開。結果決 U-8。**等你說跑才跑**。同時驗 `.claude/settings.json` 的放行規則能不能寫 `${CLAUDE_PLUGIN_ROOT}`，不能的話 `kb upgrade` 要重寫規則（I-073） | decisions U-8 |
| B-008 | 2026-10-12 | done | 桌面版安裝與升級，方向在 D-072。spike 做完，報告 reports/B-008-spike.md，結論待設計 session 定；原題：桌面版 `/plugin marketplace add <本機路徑>` 可不可以、cache 路徑長相、換資料夾後要不要 `/plugin update`、hook 能不能聯網 | 討論 |
| B-009 | 2026-10-12 | done | 併入 B-008 測完：桌面版 additionalDirectories 寫入即熱載入、重開也生效（跟 VS Code extension 相反）；auto mode 下 hook deny 照擋 | B-006 |
| B-007 | 2026-10-12 | done | B-006 後續 spike：純 ASCII 路徑（`llm-wiki-test\spike-src`）重測 additionalDirectories、`Read(C:/...)`、`Read(${USERPROFILE}/...)` 三種，每種「關掉重開接回同一對話」與「全新 session」各一次；要排除中文路徑與 resume 兩個干擾。結果：ASCII 路徑下三種設定三種載入全部跳詢問，排除中文路徑與 resume；報告 reports/B-007-spike.md | B-006 |
| B-010 | 2026-10-12 | done | CHANGELOG 的 commit 欄無法寫入自己那個 commit 的 hash（建殼第一個 commit）；定：發行時由設計 session 填實作 commit 的 hash（process 六） | B-001 建殼實作 |
| B-011 | 2026-10-12 | deferred | 建殼測試觀察：tests/test_lib_io.py 用 sys.path.insert（驗收 15 只查 scripts/，下題統一用 subprocess 或 conftest 式匯入）；Windows 上 stdout 行尾 CRLF；Git Bash 下 unittest 的 skip 原因到 stderr 顯示亂碼（stderr 也要 reconfigure） | 建殼測試 |
| B-012 | 2026-10-12 | deferred | `claude plugin validate` 每次都警告「根目錄 CLAUDE.md 不會被當 plugin context 載入」，在 CLAUDE.md 註明可忽略；順手把 validate 加進 CLAUDE.md「改完要做的事」 | 建殼確認 |
| B-013 | 2026-10-12 | done（2026-10-16 manage 確認第 1 步人眼：/skills 列 wiki-with-willy:manage） | 下一題（第一個 skill 或 command）的確認情境必含「/help 或 /plugin 看得到 wiki-with-willy」，補建殼沒拿到的載入正面證據；順便弄清楚 --plugin-dir 載的 plugin 在 /plugin 裡列不列。守門確認第 1 步已給 hook 層證據（2026-10-13）；事務層確認人眼看到 /plugin Installed 列 `wiki-with-willy inline`（2026-10-13）；/help 可見留給第一個 skill | 建殼確認 |
| B-014 | 2026-10-12 | open | spike：CLI `claude plugin update wiki-with-willy@<mp>` 能不能由 `kb upgrade` 用 subprocess 代跑、桌面版重開後吃不吃；能就 D-072 維持一句「升級」，不能就修訂成「說升級後我帶你點四步」（I-102） | B-008 |
| B-015 | 2026-10-12 | done | 守門 spec 疑義待設計 session 拍板：(1) Bash 只提到登記來源或寫 Z1／Z3 時實作回 pass 不回 allow（spec 第 2 節「其餘不表態」vs 驗收 11「allow」vs D-073）；(2) Z8 寫擋的 reason_code 表上沒有，暫用 protected；(3) `kb guard decide` 例子 `cat a.txt > b.txt` 標 Z9，但無斜線 token 不抽路徑，實作會是 unparsed 或 Z8 | 守門實作 |
| B-016 | 2026-10-12 | open | 查 Claude Code hooks 設定有沒有「hook 失敗或逾時即 deny」的欄位（onFailure 之類）與最低版本；有就加進 hooks.json，沒有就靠開場偵測 python（I-103） | 守門實作 |
| B-017 | 2026-10-12 | deferred | 守門測試的低優先：guard-status 每次呼叫都讀改成每天一次快取；`.gitattributes` 對 tests/fixtures 的二進位檔標 binary；POSIX 上 CLAUDE.md 大小寫；型別夾根新檔理由句用 Z8；origin/HEAD 沒設讓 security-review skill 跑不起來（已設）；Z9 理由句結尾補句號；fixture a.txt 內容別用 hello；deny 在終端機顯示成「Error: PreToolUse:X hook error: <理由句>」，事務層確認已人眼看過（2026-10-13）：理由句完整但前綴嚇人，是 Claude Code 固定格式，只能靠理由句白話（併 B-110） | 守門測試、確認 |
| B-018 | 2026-10-13 | deferred | AI 產出的指紋先用位元組 sha256，`kb convert` 出來後 Office 檔改文字指紋（D-014 補、D-076） | 事務層設計 |
| B-020 | 2026-10-13 | done | v0.3.0 README 加「環境需求」一節（2026-10-13）。原題：發行 README 寫明只支援 Windows、Python 3.10 以上、PATH 上要有 `python`（hooks.json 是裸 python，沒有就 fail open，I-103）；plugin-dev 的 validate-hook-schema.sh 沒 jq 會誤報 Invalid JSON，不拿它當發行關卡 | 事務層 plugin-validator |
| B-021 | 2026-10-13 | deferred | ledger.tsv 的 path_or_command_head 只留前 120 字，深路徑看不到檔名；改成保留尾端或拉長；長 TEMP 下 test_ledger 會紅 | 事務層測試第二輪 |
| B-022 | 2026-10-13 | open | 守門：不帶磁碟代號的根路徑（`\案子\...`）normalize 後判 Z11 不表態；事務層 delete-outputs 刪前也不再查 zone。守門題補一條 | 事務層測試第三輪 |
| B-023 | 2026-10-13 | deferred | 事務層低項：T 建成品→退回→U 重寫→退掉 U 後 T 那筆仍 superseded 刪不到；未來時間回合夾在 list 排第一且每句記 log；delete-outputs 固定取第一個候選可能卡住；revert --delete-outputs 沒比 sha；CLI 鎖 30 秒等於殘留門檻；LOCK_TIMEOUT 改模組全域；`\\?\` 加結尾點檔名未驗。細節 reports/事務層-測試.md 第三輪；工作日誌第三輪補：同一句連退三次後原回合的 `reverted.json` 沒補回；再退一次「退回 delete-outputs」那次時成品不會再刪、也沒提示 | 事務層測試第三輪 |
| B-024 | 2026-10-13 | deferred | 守門誤擋：Bash heredoc 內文提到總資料夾第一層路徑（寫知識庫外的報告）被當寫入目標 deny Z8；引號內與 heredoc 內的路徑要不要算寫入目標，併 B-110 看誤擋率 | 事務層確認 |
| B-019 | 2026-10-13 | deferred | `kb.py` 由模型呼叫時拿不到 session_id，CLI 用 `turns/current.json`，兩個對話同開同一知識庫時可能記到另一個 session 的回合；collab（B-206）時解；2026-10-16 工作日誌補：對話答完後在終端機跑 `folders add`／`versions revert`，sources.json 的備份仍掛在那個已結束的對話回合（`turn.current` 讀 current.json），只有日誌程式段用新代號；`kb turn note`／`redact` 也讀 current.json，兩個對話同開時會改到別的對話那句（工作日誌測試 M5），collab 題要讓模型知道自己的回合 | 事務層設計 |
| B-025 | 2026-10-14 | deferred | 高風險子命令（restore、relocate、全部重做）的「問一句」由守門對那幾個 kb.py 呼叫回 ask，不寫 settings 的 permissions.ask：守門對 kb.py 回 allow 會蓋過 ask（D-079）；那些子命令出現的那題做 | 骨架與來源登記設計 |
| B-026 | 2026-10-15 | deferred | 骨架與來源登記測試低項：展不開的來源（`${NAS}/…`）不參與 add 的重疊比對；`_survey` 每份檔重比每層夾名；理由句 `_original_segment` 引號黏在段尾找不回原始大小寫；guard 重用 `cmdparse._tokens`，要公開改名；第二輪 N5：走訪遇到夾名命中憑證樣式就剪掉，`folders add` 漏掉藏在底下的另一個知識庫與份數；走訪跳過排除夾用字串比對，排除根經 junction 或 8.3 短名另寫時跳不過（只多擋）；第三輪低項：`cd -`／裸 `cd` 吞下一個字、`dir /s` 不走訪、junction 指向排除夾、`ls \| grep md` 被當 unparsed 寫入（守門題舊）；第四輪：`\"` 黏住子指令、行尾 `\` 續行、`rg.exe`；第五輪程式整理：guard 與 cmdparse 兩套切字器合一、`_tokens_q` 是複製、`token_hit` 測試對不到正式碼、`folder_scan` 每次重讀 config；`glob.glob(include_hidden)` 是 3.11 起，3.10 上 PowerShell 的 `*` 展不到點開頭檔、只靠第三道；第六輪：`$HOME/.aws/credentials` 屬 `$VAR` 界線、`tar czf … 案子/信義王` 被 entity_file 擋（守門題舊）；第七輪：旗標黏值 `7z x -o"<R>/…"` 抽不到、guard `WRITE_VERBS` 與 cmdparse `WRITE_WORDS` 兩份動詞清單（第八輪已退回只剩 cmdparse 一份）；第八輪舊洞：`git -C <raw> clean`、`sed -E -i`、`mv * ../archive/`、`[IO.File]::Delete`；第十輪 A4：`$nullX`、反引號、`r\\2`、`dd of=`、`-C../r2` 三版都放行 | 骨架與來源登記測試第一～十輪 |
| B-027 | 2026-10-15 | done | 工作日誌題：「問」那行用 `credentials.mask_secrets` 同一個函式遮值；加 AI 第二層 `kb turn redact`（客戶給帳密時 AI 把這回合的問改成它認出的遮法，補程式猜不到的寫法）；建置告知加一句「敏感帳密不要打在對話裡，Claude Code 自己的紀錄與雲端都有」（D-081） | 骨架與來源登記設計 |
| B-028 | 2026-10-15 | deferred | 骨架與來源登記確認觀察：(1) 檔名含「帳號」「密碼」時 prompt_head 多遮（「薪轉帳號███」），「剛做了什麼」看不出是哪個檔；(2) prompt_head 截到 80 字，`folders add ..\…` 的句子看不出退的是哪個夾；(3) 對話在知識庫上層開、再移進知識庫時，任何一次專案外的 cd 都把 cwd 拉回上層，之後回合不開、守門不表態——建置告知寫「對話要開在知識庫」 | 骨架與來源登記確認 |
| B-029 | 2026-10-16 | open | `io.FileLock`：Windows 上鎖檔剛被持有者刪掉（待刪除）時 O_EXCL 丟 PermissionError，回頭看鎖檔已不存在就當成「建不起來」直接丟（R2-1），兩個程序搶同一把鎖會偶發失敗；工作日誌在 `journal._lock` 包一層同逾時內重試繞過，`turn.backup`、`folders` 仍直接用 FileLock。io.py 該題修 | 工作日誌實作 |
| B-030 | 2026-10-16 | deferred | 工作日誌低項：flush 鎖逾時後舊回合那行永遠 done false（lint 題清）；md 渲染不擋 `![](…)`、`<img>`、`%%`（AI 本來就能寫 AI 產出，風險有限）；守門 ledger 的指令欄沒遮值，`turn note`／`redact` 的原文被記成 unparsed 時會進帳（L7、L9）；`versions revert --json` 的 `prompt_head` 原樣輸出，升級前沒遮好的回合會帶出來；第四輪：終端機 delete-outputs 的動作回合「問」寫成「退回 T」，再退時句子誤導（該用自己的句子）；redact 掃 versions/*/turn.json 遇壞檔丟英文 traceback 只改一半 | 工作日誌測試 |
| B-031 | 2026-10-16 | open（1、6、7、8 已在 v0.6.0 收；剩 2～5） | 工作日誌確認觀察：(1) 貼上的內容送到 UserPromptSubmit 時 prompt 是 `<pasted_content id=…>` 包住的原樣，日誌「問」會印出標籤，`prompt_head` 應剝掉包裝只留內文；(2) 「結果」保留 Markdown 記號（`##`、`**`、```` ``` ````），壓成一行後不好讀，渲染時可去掉；(3) 兩個 session 開在同一知識庫時另一個 session 的訊息也被記成客戶的一句（B-019 同根）；(4) 遮值一路遮到句尾會吃掉 AI 的說明段（D-086 接受多遮，量多再談）；(5) Esc 按得早時 Claude Code 把那句拿掉、AI 沒收到，日誌已記成中斷；(6) AI 沒拒絕代登入外部系統，R-09 等 core.md；(7) AI 不知道 kb.py 在哪，要點名完整路徑，開場注入題要給 | 工作日誌確認 |
| B-032 | 2026-10-16 | open | 子代理讀得到知識庫 CLAUDE.md 但沒有 SessionStart 注入，拿不到 core.md；哨兵句已標「子代理不適用」，但規則怎麼給子代理（SubagentStart hook 注入同一段，或 card-maker 本文指 `${CLAUDE_PLUGIN_ROOT}/rules/core.md`）等 agent 題定（D-053） | 開場注入與核心規則測試 |
| B-033 | 2026-10-16 | deferred | 開場注入低項：`prompt_head` 剝標籤的正規式對屬性值含 `>` 留殘渣；hook-errors.log 可能含 plugin 絕對路徑（診斷檔，不算 D-019）；session.py 的 2 秒速度測試在忙的機器可能偶發紅；test_budget 用裸 assert、不看子程序 exit code；`hooks/guard.py` 印 stdout 時 pipe 斷掉可能 exit 120（session.py 已用 os.dup2 處理，守門照做）；第二輪：session.py `emit` 吞 write 例外不記 log、devnull fd 沒關、關鍵詞被 pasted_content 標籤切開（`pass<…>word`）遮不到、第二段 try 的 BaseException 含 KeyboardInterrupt | 開場注入與核心規則測試 |
| B-034 | 2026-10-16 | open | `kb journal recent` 一段一行只印「做了」，不印「改了」哪些檔，AI 回答「最近改了什麼」時無法確認檔有沒有登記，又把「程式｜」標記誤讀成登記與否；answer 題定 recent 的輸出要不要帶改了與 note_by | 開場注入與核心規則確認 |
| B-035 | 2026-10-16 | deferred | manage 低項：巢狀排除時 `folders include` 外層後說「讀得到」但內層 hidden 仍擋，反方向（取消內層、外層仍擋）也一樣，句子要講；skill 文字守的「先問再做」沒有機器保證（同 B-025，守門對高風險 kb.py 子命令回 ask） | manage 測試 |
| B-036 | 2026-10-16 | open | skill-creator 的 `scripts/run_eval.py` 讀 evals 沒指定 encoding（cp950 讀不了中文，要 `PYTHONUTF8=1`）、用 `select.select` 讀子程序管線，Windows 上每次 query 都 WinError 10038、全部算未觸發；路由測試要用把 select 換成「永遠就緒」的 shim 跑（原檔不動）；另外預設 10 個 worker 會把同描述的暫時指令檔同時放進同一個 `.claude/commands/`，模型挑到別人那份就算沒觸發（實測該觸發只剩 10%），要 `--num-workers 1`；之後自己寫一支 `tests/evals/run.py`，D-091 的做法到時改 | manage 測試 |
| B-037 | 2026-10-16 | deferred（形狀已定 D-105、D-106，等日程題有資料） | Obsidian 視圖（D-097：選配不必要）：`kb` 在 `系統/` 產 Bases 的 `.base` 視圖檔（專案總表、看板），吃綜合卡檔頭；日程渲染成視圖吃得到的形狀；守門補 `.obsidian/` 一條；建置時服務人員可裝可不裝。等綜合卡（ingest）與日程（note）有資料再開題 | 設計討論 |
| B-038 | 2026-10-17 | open | 開場 scan 的 roots() 對斷線的網路磁碟做 isdir 可能卡 20 秒以上，標準函式庫沒有帶逾時的檢查；候選：按機器記上次找不到的來源、開場先跳過改由 kb changes 查，或子執行緒加逾時 | 清冊 |
| B-039 | 2026-10-17 | open | `inventory.apply` 只把 scan 期間新增的 id 合併回去，對既有條目的並行改動（`register_output` 追加指紋、`set_status`）會被蓋掉；要並行寫才會遇到 | 清冊測試觀察 B |
| B-040 | 2026-10-17 | open | Win32 邊角：名字結尾帶 `.` 或空白時 `long_path` 經 `abspath` 會被去掉，走訪列的名字與實際開的檔可能不同；`path_of` 不擋 `..` 與絕對路徑段（目前沒有呼叫端） | 清冊測試觀察 E |
| B-041 | 2026-10-17 | open | `hooks/hooks.json` SessionStart 的 description 沒提快速 scan 與寫 env.json；下次碰 hook 的題一起改並跑 validate | 清冊測試觀察 F |
| B-042 | 2026-10-17 | open | 既有測試 `test_two_processes_register_concurrently_both_kept` 偶爾因 `io.FileLock` 的 PermissionError 競態失敗（main 就有，io.py 清冊題沒動） | 清冊測試觀察 H |
| B-043 | 2026-10-17 | deferred | 低：v1→2 遷移從磁碟補 size／mtime 等於信任期間沒改過（C）；`_migrate` 每個位置 stat 不受時間預算、`env.clear_missing` 每個來源各讀一次 env.json（G） | 清冊測試觀察 C、G |
| B-044 | 2026-10-17 | open | 低：`content_fp` 的時間檢查「每 1000 個元素查一次」每個 zip 成員重新數，小成員很多的檔（幾千張投影片）不會停下來查；改成跨成員累計 | 清冊測試第二輪 |
| B-045 | 2026-10-17 | open | 低：hidden 排除只拿掉重複檔其中一個位置時第二行印「清冊：0 份已拿掉」，應該數位置或另印；`versions revert`／`--delete-outputs` 在清冊讀不到時最後那句與成品數是還原前狀態 | 清冊測試第二輪 |
| B-046 | 2026-10-17 | open | 低：`folders add` 第一行「N 份」看不出含不含憑證那幾份（AI 自己說看不懂）；`inventory scan` 的「同一份內容在多處 N 份」不列是哪份 | 清冊確認 ①② |
| B-047 | 2026-10-17 | open | 工作日誌遮值碰到檔名「網銀密碼.txt」會從「密碼」起把 note 後段整段遮掉（B-031 遮太多同類）；ingest 確認 G3：「讀不到：有密碼」那句也被遮成 ███；檔名裡的關鍵詞要不要豁免，守門題與日誌題一起看 | 清冊確認 ③ |
| B-048 | 2026-10-17 | open | AI 的 `kb turn note` 把上一回合的事也寫進來；建議下一步時提到還不存在的綜合卡（ingest 題定卡之前 core.md 要不要加一句） | 清冊確認 ④ |
| B-049 | 2026-10-17 | open | 守門擋了對含憑證檔的來源夾只看第一層的 `ls`（D-079 資料夾走訪 fail closed 的副作用）；要不要讓「列目錄不讀內容」放行 | 清冊確認 ⑤ |
| B-050 | 2026-10-17 | open | spike：Obsidian 三頁的假設（D-105、D-106），在 `llm-wiki-test` 總資料夾放假卡、`.base`、Mermaid gantt、`.obsidian/app.json`、CSS 片段，桌面 Obsidian 開一次驗：Bases 現在有哪些視圖、`.base` 能不能嵌進 md、巢狀清單摺疊與連結、Mermaid gantt 點 bar 開不開卡、`file:///` 中文與空白路徑與網路磁碟怎麼寫、「偵測所有副檔名」點 docx 是不是直接用 Word 開、CSS 片段藏得掉 `檔案索引/` 而 Bases 與搜尋照常、`.obsidian/` 由骨架出貨後 Obsidian 改寫會不會衝突。等你說跑才跑 | 設計討論 2026-10-17 |
| B-051 | 2026-10-18 | done（裁定 20，v0.9.0） | 卡寫錯名被 PostToolUse 打回時錯名檔已在 `檔案索引/`（Z5 指令寫擋、AI 刪不掉），重寫正名後同編號兩張都進 `broken`、條目再也不進 new／stale；stale 換標題同樣。要不要讓 `kb cards sync` 把檔名改成 `expected_name`、同編號多張時留檔頭過的那張，或給一個 `kb cards rename`；這版只在 carding.md 叮嚀寫前先對 | ingest 製卡實作 skill-reviewer |
| B-052 | 2026-10-18 | open | 總目錄：PostToolUse 每寫一張合法卡就整份重渲染，卡幾千張時留意 10 秒逾時（改成只在 ingest 收尾或 `kb cards index` 渲染、或增量）；`inventory scan` 程式段的 pending 兩檔第一次建檔也記 modify（低） | ingest 製卡實作回報 |
| B-053 | 2026-10-18 | deferred | ingest 製卡測試低項：convert 路徑沒經 `paths._text_forms`（Git Bash `/c/…`、`file:///C:/…`）；用路徑指到 missing 條目但檔已放回（未 scan）回「找不到原檔」；終端機沒回合時 sync／drop／pending 先開空回合；folders exclude／remove 後 open 的 missing_file 永遠 open、拆出新條目時舊 unreadable 不 auto_done、占位檔下載回來大小時間不變不算 modified（C8）；`output_log.py` `ensure_ascii` 與 session.py 不一致；`line_ingest_hint` 出錯 log 兩次 | ingest 製卡測試第一輪 |
| B-054 | 2026-10-19 | open | manage 路由 eval 從 10-16 的 30/30 掉到 26/30、25/30（描述與句子沒動，ingest 不在場），第一輪 0/30 是環境異常；接受發行（ingest 裁定 35）。下次動 manage 描述、或 skill 數量變時，跑三輪取中位數，仍低於 90% 才調描述；順便查 run_eval 整輪 0 觸發的環境原因 | ingest 製卡測試第二輪 |
| B-055 | 2026-10-19 | deferred | 低：`.system/temp` 是 junction 時，成功的 `kb convert` 仍把暫存檔寫進 junction 目標夾（裁定 16 的 realpath 兩邊都解到目標）；要先能寫 `.system/` 才做得到。lint 題加一項「`.system/` 底下不准有重解析點」 | ingest 製卡測試第二輪 |
| B-056 | 2026-10-19 | deferred | 低：`versions revert` 退掉 `cards drop` 那回合後，同一張卡同時出現在 todo 的 orphans 與 missing（G4）；AI 看到開場「變動：修改 1」會自己猜成「合約有變動」（G7，R-06）；加資料夾後收尾句與 spec 字句不同（G6，以 manage SKILL 為準） | ingest 製卡確認 |
| B-057 | 2026-10-19 | deferred | 低：`extract.render_pages` 沒有點陣圖尺寸上限，超大頁面（海報、圖紙）轉圖可能吃數 GB 記憶體；加頁面像素上限、超過降 scale 或標「部分」。另：`tests/fixtures/inventory` 的 `報價_v2.pdf` 是假 PDF，convert 會回「檔案壞了」，不能當可讀 PDF 用 | ingest 製卡測試第三輪 |
| B-058 | 2026-10-19 | open | 開場有變動時「先答客戶這句再整理」靠開場指示行做不到（三種寫法都被模型跳過答案直接整理，確認第 4 步三次）；「不問直接整理」有效。候選：Stop hook 在清冊有待製卡且這回合沒整理時 `decision: block` 一次，模型答完被擋才整理（程式保證、不靠指示）；或 answer 題定回答格式後再看。answer 題接 | ingest 製卡確認 G9 |
| B-059 | 2026-10-19 | open | `cards todo` 的 orphans 不分「條目 excluded（客戶說不讀）」與「編號不在清冊」，AI 把排除夾的孤兒卡說成「原檔不見了要確認是不是刪了」誤導客戶；todo 的 orphans 帶原因、SKILL 寫「排除夾的卡不跟客戶提」；manage 第二版清殘留時一起收。另：AI 對客戶用「您」（R-10 語氣，低） | ingest 製卡確認 G11 |
| B-060 | 2026-10-19 | open | `test_budget` 的開場 8 行 400 字量的是開發機 worktree 路徑下的 kb 那行；客戶機 plugin 裝在 `~/.claude/plugins/cache/...` 路徑更長（六行都在時開發機 356 字）。upgrade 題定下安裝路徑後，用那個長度重量一次，或讓 test_budget 用最長合理路徑算 kb 行 | 客戶設定檔與型別實作回報 C |
| B-061 | 2026-10-19 | deferred | 低：`profile apply` 建夾失敗那句把 OS 的 strerror 原樣接進去，句尾「存取被拒。；設定檔沒有套用」標點疊用；接 OS 錯誤字串的句子一律先去尾端句號（convert 的「讀不到」同類檢查一次） | 客戶設定檔與型別測試第二輪 |
| B-062 | 2026-10-19 | deferred | 低：`versions revert` 退掉 `kb entity rename` 那回合時，程式改名的實體夾（裡面有客戶原檔）不會改回來，清冊退回舊 uri，下次 scan 把那份原檔當搬動；夾的改名不在回合備份裡（同 profile apply 建的夾）。真要退得乾淨要讓回合記資料夾動作 | 實體與綜合卡實作疑義 12 |
| B-063 | 2026-10-19 | deferred | 實體與綜合卡測試第二輪低項 L1～L7：同回合新建後被打回重寫的綜合卡也算進 ai_synthesis；`_Undo.rollback` 沒逐步 try；改型別失敗時先建的空型別夾不拿掉；apply 補登記關於你時 `--json` 的 applied 仍 false；commands.md 那處 rules/synthesis.md 沒帶 ${CLAUDE_PLUGIN_ROOT}（已滿 150 行）；`modified_synthesis_count` 只剩測試在叫；clean 對含 .. 的一律拒（比裁定 14 嚴）。細節 reports/實體與綜合卡-測試.md 第二輪 | 實體與綜合卡測試第二輪 |

## 補漏洞型延伸（落在既有位置，不加分類）

| # | 日期 | 狀態 | 事 | 落在哪 |
|---|---|---|---|---|
| B-101 | 2026-10-11 | deferred | 掃描 PDF 做 OCR、圖片與照片製卡 | ingest/formats、kb convert |
| B-102 | 2026-10-11 | deferred | `.doc` `.ppt` 用客戶的 Office 自動轉、Excel 重算與轉 PDF；第一版 `kb office` 只偵測與讀，不開 COM（I-013） | ingest/formats、kb office |
| B-103 | 2026-10-11 | deferred | Google 文件捷徑、NAS、隨身碟路徑 | ingest/local |
| B-104 | 2026-10-11 | deferred | 規模大了之後的本地搜尋索引，索引是可拋棄衍生物 | kb search、answer |
| B-105 | 2026-10-11 | deferred | 索引過大拆年份 | kb reindex |
| B-106 | 2026-10-11 | deferred | 客戶設定檔加了格式調整後，舊卡補欄位 | lint 檢查項、kb batch 標重做 |
| B-107 | 2026-10-11 | deferred | API 製卡執行器：到時才加 config 鍵與 kb/executors/，規則檔不動（D-053） | kb/executors/ |
| B-108 | 2026-10-11 | deferred | 額度與時間估算更準、建置分天 | kb inventory、build-cards |
| B-109 | 2026-10-11 | deferred | 回答末尾建議問題、交付速查卡 | rules/next-step.md、build-finish |
| B-110 | 2026-10-11 | deferred | 守門誤擋率、失敗訊息白話化。骨架與來源登記第八輪起已知誤擋：整條指令被判寫入就全部路徑套寫入欄，`grep -r X . \| tee /tmp/h`、`cat CLAUDE.md > /tmp/c.md`、`curl … > /tmp/x`（網址當路徑）、`tar czf` 在總資料夾裡都被擋；憑證走訪的「範圍太大」；`ls C:/` 沒排除夾時被擋 | guard.py、rules/core.md |
| B-111 | 2026-10-11 | deferred | Mac | _lib/paths、envcheck |

## 選取型延伸（依客戶特質開關）

| # | 日期 | 狀態 | 事 | 槽位 |
|---|---|---|---|---|
| B-201 | 2026-10-11 | deferred | Email（Gmail、Outlook）來源，Claude 取腳本記 | 來源型別 |
| B-202 | 2026-10-11 | deferred | 行事曆讀寫 | 來源加輸出 |
| B-203 | 2026-10-11 | deferred | Line，做法待定 | 來源加輸出 |
| B-204 | 2026-10-11 | deferred | 線上版雲端硬碟、Notion | 來源型別 |
| B-205 | 2026-10-11 | deferred | 會議錄音轉文字 | 來源型別 |
| B-206 | 2026-10-11 | deferred | collab：多 session 主管制、佔用偵測、角色卡 | 模式 |
| B-207 | 2026-10-11 | deferred | 團隊共享、個人與團隊空間分開：一個知識庫當另一個的唯讀來源 `kb://`；對方的綜合卡不重 ingest，只建指標卡回正本讀 | 模式加來源型別 |
| B-208 | 2026-10-11 | deferred | 主動推送：會前背景、逾期承諾，外部排程跑 `kb agenda brief` | 模式加輸出 |
| B-209 | 2026-10-11 | deferred | 排程健檢 | 模式 |
| B-210 | 2026-10-11 | deferred | 起草 PowerPoint、輸出 Google 文件 | 輸出去處 |
| B-211 | 2026-10-11 | deferred | 既有 wiki 接入 | build-setup 分支 |

## 從 llm-wiki 延後的想法

| # | 日期 | 狀態 | 事 | 歸哪 |
|---|---|---|---|---|
| B-301 | 2026-10-11 | deferred | 開工與收工儀式 | collab，單人不需要 |
| B-303 | 2026-10-11 | deferred | 取用副本夾一致性檢查 | lint 檢查項 |
| B-304 | 2026-10-11 | deferred | 像素辦公室 | 獨立 plugin，不進本架構 |
| B-305 | 2026-10-11 | deferred | 教練開關設定 | 不做，見 D-007 |

## 不做

自建桌面應用、本機模型版本、知識圖譜視覺化。來自競品分析的決定。行業預設包（原 B-212，D-040）。日誌摺疊（原 B-302，D-065）。
