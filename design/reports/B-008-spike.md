日期：2026-10-12
給誰看：設計 session（llm-wiki-j-8e）、之後寫安裝／升級說明與守門 hook 的人
何時更新：Claude Code 桌面版改了 plugin 安裝／更新 GUI，或 hook 權限行為改了，就重測後更新

# B-008 spike：桌面版安裝、升級、hook 放行讀取（含 B-009 併測）

環境：Windows 11、Claude Code 桌面版（Code 分頁）、本機 CLI `claude` 2.1.289
工作目錄：`C:\Users\Jie\Desktop\llm-wiki-test\desk`
暫時 marketplace：`llm-wiki-test\mp\`（name `wwtest`，plugin `wiki-with-willy` 是 llm-wiki-j 的複本，加了 probe hook）
原始逐步紀錄：`llm-wiki-test\desk\B-008-紀錄.md`

## 結果表

| 題 | 做法 | 結果 |
|---|---|---|
| 1 本機 marketplace | ① 桌面版 `/plugin marketplace add C:\…\mp` ② 同一個對話框改填 `file:///C:/…/mp` ③ CLI `claude plugin marketplace add "C:\…\mp"` | ①② 桌面版跳出 GUI「新增市集」對話框，只收 https 或 GitHub `owner/repo`，紅字「URL 必須以 https:// 開頭」，同步按鈕是灰的。③ 成功，登記成 `directory` 型，直接指向 mp 原路徑，不會複製。要重開桌面版，`/plugin install` 才看得到，而且是導到 GUI 外掛目錄去安裝 |
| 2 cache 路徑 | 安裝後開全新 session，看 SessionStart probe 印的內容，再列 `~\.claude\plugins\` | `CLAUDE_PLUGIN_ROOT=C:/Users/Jie/.claude/plugins/cache/wwtest/wiki-with-willy/0.1.0`（正斜線）。安裝時會把 mp **複製**到 `cache\<marketplace>\<plugin>\<version>\` |
| 4 hook 聯網 | probe.py 用 urllib GET raw.githubusercontent.com，timeout 5 秒 | `net=200`，每次開場都是 |
| 5 守門 hook | default 模式下 Read 三種路徑，再對照 guard_probe.log | spike-ext 回 allow：**不跳詢問，直接讀到**。spike-來源 回 deny：**被擋**。shell-check（hook 不輸出）：跳詢問，使用者按 No 之後沒有讀 |
| 5b auto 模式下 deny | 切 auto，Read `spike-來源\b.txt` | **照擋**，log 的 `permission_mode` 是 `"auto"` |
| 3 升級 | mp 版號改成 0.1.1 → 只重開 → `/plugin update` → GUI「更新」→ 重開 | 只重開：仍是 0.1.0。`/plugin update`：只打開 GUI 管理頁，沒有更新。按詳情頁「更新」（tooltip 寫「更新至 0.1.1」）：跳出假警報，按「仍要更新」後完成。再重開：`version=0.1.1`，ROOT 變成 `…/0.1.1` |
| B-009 additionalDirectories | `desk\.claude\settings.json` 寫入 shell-check | 寫入後**同一個 session 立刻熱載入**（收到 Environment update），讀取不跳詢問。重開後仍生效，也不跳詢問 |

## 結論

### 1. 本機路徑 marketplace 能不能加、怎麼寫
- **桌面版 GUI 不能加。** `/plugin marketplace add` 在桌面版會開 GUI 對話框，這個對話框只收 `https://` 或 GitHub `owner/repo`；本機路徑和 `file://` 都會被擋。
- **CLI 可以加：** `claude plugin marketplace add "C:\path\to\mp"`。這樣會登記成 `{source: "directory", path: …}`，同時寫進 `~\.claude\settings.json` 的 `extraKnownMarketplaces` 和 `plugins\known_marketplaces.json`。
- 加完要**重開桌面版**才看得到。之後打 `/plugin install <plugin>@<mp>` 一樣會導到 GUI 外掛目錄，按「安裝 → 為我安裝」就裝好，不用重開也不會提示重開，但要下一個 session 才會載入。
- 對客戶的意思：正式發佈走 GitHub（https / owner/repo）在桌面版最順。本機路徑只適合開發測試，而且要另外會用 CLI。

### 2. cache 路徑長相、`CLAUDE_PLUGIN_ROOT` 指哪
- 安裝時會**複製一份**到 `%USERPROFILE%\.claude\plugins\cache\<marketplace>\<plugin>\<version>\`，就算來源是本機 directory 也一樣。
- `CLAUDE_PLUGIN_ROOT` 指向這份 cache 複本（不是 mp），格式是**正斜線**：`C:/Users/Jie/.claude/plugins/cache/wwtest/wiki-with-willy/0.1.0`。
- 附帶出現的東西：
  - `plugins\data\wiki-with-willy-inline\`：每個 plugin 一份資料夾，不分版本，升級後沿用。
  - `.install-manifests\<plugin>@<mp>.json`：記錄各檔案的 sha256。
  - 每個版本資料夾裡的 `.in_use\<pid>`：標記執行中的 claude.exe 正在用這個版本。
- **plugin 寫入資料不要放在 `CLAUDE_PLUGIN_ROOT` 底下**。升級會換資料夾，舊資料夾也會被清掉。

### 3. 升級換資料夾後要不要 `/plugin update`
- **要有一個「更新」動作，光重開沒用。** 就算 marketplace 是本機 directory、來源已經改了，重開後 `installed_plugins.json` 和 cache 都不會動。
- 桌面版打 `/plugin update <plugin>@<mp>` **本身不會更新**，只會打開 GUI「外掛程式」管理頁。客戶要的實際步驟是：
  1. `/plugin`（或 `/plugin update …`）打開管理頁
  2. 點該 plugin 進詳情頁（列表上的「⋮」只有關閉／移除，沒有更新）
  3. 按右上「更新」（tooltip 會顯示「更新至 x.y.z」，代表 GUI 有偵測到新版號）
  4. 確認視窗按「仍要更新」
  5. 重開桌面版
- **假警報**：只要還有任何 session 開著，就會跳「更新將覆寫本機變更：有 2 個檔案已在本機修改」，列出的是 `.in_use\<pid>`。原因是 install manifest 沒排除這些佔用標記。實際上舊資料夾不會被覆寫：新版會另外建一個 `0.1.1\`，舊的 `0.1.0\` 會留著並標上 `.orphaned_at`，執行中的 session 繼續用舊版。**升級說明要先寫清楚這個警告可以放心按，或請客戶先關掉其他 session。**
- 更新完成時只出現小訊息「已更新至 0.1.1」，**不會提示要重開**，所以說明裡要明寫。
- 舊版清理：寫報告時 0.1.0 還在，因為它的 `.in_use` 指向的兩個舊 session 程序都還活著。`.orphaned_at` 什麼時候真的生效，這次沒有觀察到。
- 用 CLI 的話 `claude plugin update` 應該也可以，這次沒有測（GUI 路線已經成功）。

### 4. hook 能不能聯網
- **能。** SessionStart hook 用 Python urllib GET `https://raw.githubusercontent.com/`，裝好後三次開場都是 `net=200`。所以 hook 裡做「開場檢查新版」是可行的，要記得設 timeout。
- SessionStart 的 stdout 會原樣進到模型開場的 context，標頭是 `SessionStart:startup hook success:`。多個 plugin 的輸出會併在同一個區塊。

### 5. PreToolUse 守門
- **回 allow 能免詢問放行讀取**：spike-ext 在工作目錄外，平常會跳詢問，hook 回 allow 後直接讀到，使用者確認沒有跳詢問。（對照 B-006／B-007：settings 的 Read allow 規則一直放不行，hook 這條路可以。）
- **回 deny 能擋**：模型看到的原文是 `PreToolUse:Read hook error: <permissionDecisionReason>`。請注意，這裡會出現「hook error」字樣，reason 要寫得讓模型看得懂，例如寫明「這是來源資料夾，請改用 xxx」。
- **auto 模式下 deny 照擋**，hook payload 的 `permission_mode` 會如實帶 `"auto"`／`"default"`，hook 可以據此分流。
- hook 不輸出（exit 0）的話，就走一般權限流程（例如跳詢問）。
- **hook 收到的路徑格式**：`tool_input.file_path` 和模型傳入的字串**一模一樣**，不會正規化：反斜線、大寫磁碟代號、中文原樣（stdin JSON 也沒有轉成 `\u` 跳脫）。payload 欄位有 session_id、transcript_path、cwd、scratchpad_dir、prompt_id、permission_mode、effort、hook_event_name、tool_name、tool_input、tool_use_id。
  - 守門 script 一定要自己正規化：`\` 換成 `/`、轉小寫，最好再 resolve。模型也可能傳 `/` 或小寫路徑。
- **缺口（沒有實測，但很確定）**：matcher 只有 `Read`，Bash `cat`／`type`、Grep、Glob 都不會經過這個 hook。auto 模式的系統提示還會**主動鼓勵**模型用 Bash 的 cat／grep 讀檔，所以「擋來源資料夾」不能只靠 Read hook。至少要把 Grep、Glob 一起納入 matcher，Bash 另外解析指令或靠 `permissions.deny`。

### 6. additionalDirectories 在桌面版重開後生效嗎
- **生效，而且不用重開。** 寫進專案 `.claude\settings.json` 的 `permissions.additionalDirectories`（正斜線路徑）後，同一個 session 立刻收到「Environment update — Additional working directories added」，讀取 shell-check 不跳詢問。重開後開場的環境資訊也有列出，讀取一樣不跳詢問。
- 這次的放行來自 additionalDirectories，不是 hook：guard log 有記到那兩次讀取，但 hook 沒有輸出。

## 實驗留下的東西（要清的話）
- 全域：`~\.claude\settings.json` 的 `extraKnownMarketplaces.wwtest`、`enabledPlugins["wiki-with-willy@wwtest"]`；`plugins\known_marketplaces.json`、`installed_plugins.json` 裡的 wwtest 項目；`plugins\cache\wwtest\`、`plugins\data\wiki-with-willy-inline\`、`.install-manifests\wiki-with-willy@wwtest.json`。
  - 清法：`claude plugin uninstall wiki-with-willy@wwtest`，再 `claude plugin marketplace remove wwtest`（或在 GUI 移除）。
- 測試夾：`llm-wiki-test\mp\`、`desk\.claude\settings.json`、`desk\guard_probe.log`、`shell-check\x.txt`。
