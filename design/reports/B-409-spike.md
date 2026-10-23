# B-409 spike 報告

開始：2026-10-23（機器時間顯示 release 建於 2026-10-22T18:23Z）。測試夾：`C:\Users\Jie\Desktop\llm-wiki-test\upgrade-spike\`。

## 第 0 段：開始前

### 0-2 環境
| 項目 | 值 |
|---|---|
| 桌面版（Appx） | `Claude_3.6556.1.0_x64__pzs8sxrjxfjjc`（`Get-AppxPackage *Claude*`） |
| 內建 claude.exe（Glob/Get-ChildItem 找到兩個版本夾） | `C:\Users\Jie\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\2.1.304\aed62990b91e\claude.exe`（2026-10-20）<br>`C:\Users\Jie\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\2.1.308\c17a819afb40\claude.exe`（2026-10-22，最新） |
| PATH 上 `claude` | `C:\Users\Jie\.local\bin\claude.exe`，`claude --version` → `2.1.308 (Claude Code)` |
| `python --version` | `Python 3.14.7` |
| `gh auth status` | 帳號 jielle2453，token scopes：`gist`、`read:org`、`repo`、`workflow`（**沒有 `delete_repo`**，收尾要請使用者自己刪 repo） |

注意：Glob 工具直接掃 `AppData/Local/Claude-Data/**/claude.exe` 在 20 秒內逾時（Ripgrep search timed out）；改用 PowerShell `Get-ChildItem …\claude_*\claude-code -Recurse -Filter claude.exe` 才找到。

### 0-3 基準（使用者範圍）
- `~\.claude\settings.json` SHA256：`AC9C560C49FA969DE683A36AFBC0BE98440C5ADCBE224A1332350F1662657A1C`
- `settings.json` 原文已原樣存在 `upgrade-spike\baseline\settings.json`（同夾另存 `installed_plugins.json`、`known_marketplaces.json`、`cache-dirs.txt`）。要點：`"model": "sonnet"`；`enabledPlugins` 內 llm-wiki、superpowers、frontend-design、ui-ux-pro-max 為 false，plugin-dev、skill-creator、code-review 為 true；`extraKnownMarketplaces` 有 llm-wiki、ui-ux-pro-max-skill、impeccable；`autoUpdatesChannel: "latest"`；`tui: "fullscreen"`；`theme: "dark"`；`agentPushNotifEnabled: true`。
- `installed_plugins.json`（version 2）基準共 8 筆：`llm-wiki@llm-wiki`（user，4877fdd4e3ef）、`superpowers@claude-plugins-official`（user，7.0.0）、`frontend-design@…`（user，af59ee3a1051）、`ui-ux-pro-max@ui-ux-pro-max-skill`（user，2.13.0）、`impeccable@impeccable`（**project**，`C:\Users\Jie\Desktop\APP`，4.5.2）、`plugin-dev@…`、`skill-creator@…`、`code-review@…`（皆 user，af59ee3a1051）。**沒有 willy-probe**。
- `known_marketplaces.json` 基準共 4 個：`claude-plugins-official`、`llm-wiki`、`ui-ux-pro-max-skill`、`impeccable`。**沒有 willy-spike2**。
- `~\.claude\plugins\cache\` 基準有：claude-plugins-official、impeccable、llm-wiki、ui-ux-pro-max-skill、willy-cm、以及 19 個 `temp_git_*` 夾；**沒有 willy-spike2**。
- 測試夾基準：只有 `B-409-任務.md`；沒有 `.claude\`。

### 準備（做在使用者同意範圍內）
- 0.1.2：複製 `install-spike\probe-plugin\` → `upgrade-spike\probe-0.1.2\`，`plugin.json` 與 `probe.py` 的 `VERSION` 由 0.1.0 改 0.1.2（SessionStart 那句用 `VERSION` 變數，所以自動變 0.1.2）。zip 內容 `.claude-plugin/plugin.json`、`hooks/hooks.json`、`hooks/probe.py`（與 0.1.0 zip 同結構）。
- `upgrade-spike\dist\sha256.json`：
  - 0.1.0：`9aad75d63331cd1cc37cf0626e3541be5a360513695c517a2bea59e5d40e20fd`（與 install-spike 相同）
  - 0.1.1：`bf742557390b8ecf45573987bbf2b054d6a1336ee5ce614b582bb653c5973f15`（與 install-spike 相同）
  - 0.1.2：`f40bce7226cb5dca3213082115067f4a0c32795855c9e7ea1ff91e320985a664`
- 公開 repo `https://github.com/jielle2453/wiki-with-willy-spike2` 已建（使用者已在設計 session 同意），`main` 上有 `marketplace.json`（`willy-spike2`、0.1.0）與 README；Release v0.1.0、v0.1.1、v0.1.2 已各上傳對應 zip。清單網址 `https://raw.githubusercontent.com/jielle2453/wiki-with-willy-spike2/main/marketplace.json` 以 curl 確認可取得。本機 repo 工作目錄在 `upgrade-spike\repo\`。



### 0-1 使用者確認
使用者回覆：「確認好了，外掛畫面沒有啟用舊的 llm-wiki、wiki-with-willy、willy-probe」。

### 0-4 local 安裝（PATH 上的 claude 2.1.308，在 upgrade-spike 夾內）
1. `claude plugin marketplace add https://raw.githubusercontent.com/jielle2453/wiki-with-willy-spike2/main/marketplace.json --scope local`（2.4 秒）輸出原文：
   ```
   Adding marketplace…Downloading marketplace from https://raw.githubusercontent.com/jielle2453/wiki-with-willy-spike2/main/marketplace.json
   Validating marketplace data
   Saving marketplace to cache
   Cleaning up old marketplace cache…
   √ Successfully added marketplace: willy-spike2 (declared in local settings)
   ```
2. `claude plugin install willy-probe@willy-spike2 --scope local`（2.3 秒）輸出原文：`Installing plugin "willy-probe@willy-spike2"...√ Successfully installed plugin: willy-probe@willy-spike2 (scope: local)`
3. 結果：
   - 測試夾 `.claude\settings.local.json` 被建出來，內容：`extraKnownMarketplaces.willy-spike2.source = {source:"url", url:<清單網址>}`、`enabledPlugins["willy-probe@willy-spike2"] = true`。
   - `~\.claude\settings.json` SHA256 仍為 `AC9C560C…57A1C`（沒變）。
   - **`installed_plugins.json` 與 `known_marketplaces.json`（使用者目錄裡）仍會多一筆**：`willy-probe@willy-spike2` 的 `scope: "local"`、`projectPath` = 測試夾、`installPath` = `~\.claude\plugins\cache\willy-spike2\willy-probe\0.1.0`；`known_marketplaces.json` 多 `willy-spike2`（`installLocation` = `~\.claude\plugins\marketplaces\willy-spike2`）。這兩個檔是全域登記簿，local 範圍的安裝也會寫進去，但只有 `scope: local` + `projectPath`，沒有 `scope: user`；其他 8 筆 plugin 與 4 個市集內容不變。收尾時以「這兩筆都被移除、其餘不變」為判準。
   - `known_marketplaces.json` 裡 `claude-plugins-official.lastUpdated` 從基準的 2026-10-22T17:53:10Z 變成 18:26:09Z，不是我們的指令造成的（App 或其他 session 的背景更新）。
   - `cache\willy-spike2\willy-probe\0.1.0\` 內有 `.claude-plugin\plugin.json`、`hooks\hooks.json`、`hooks\probe.py`，另有 `.in_use` 標記檔。


## 第一段：AI 在對話裡代跑更新

### 1-1 新對話確認（0.1.0）
- 新對話開場 hook 輸出：`【willy-probe 0.1.0】SessionStart 跑了`（開場訊息裡的原文，中文在 hook 輸出顯示為亂碼，是 hook 的 stdout 編碼問題，不影響版本資訊）。
- `probe.jsonl`（`~\.claude\plugins\data\willy-probe-willy-spike2\probe.jsonl`）第一行 SessionStart：`"probe":"0.1.0"`、`"source":"startup"`、`"plugin_root":"C:/Users/Jie/.claude/plugins/cache/willy-spike2/willy-probe/0.1.0"`、`"claude_version":null`、`"python":"3.14.7"`；payload `keys` = `cwd, hook_event_name, scratchpad_dir, session_id, source, transcript_path`（**沒有任何版本欄位**）。
- 到讀檔時已記到的事件：SessionStart 1、UserPromptSubmit 2、PreToolUse 6、Stop 1；PostToolUse 要等第一次 Write/Edit（寫這份報告）才會出現。
- `installed_plugins.json`：`willy-probe@willy-spike2` scope `local`、version `0.1.0`、installPath `…\cache\willy-spike2\willy-probe\0.1.0`。
- 這個 session 的 `permission_mode` 是 `auto`（見 UserPromptSubmit/PreToolUse 記錄）。

### 1-2 push 清單換成 0.1.1
- 先問使用者，回覆「可以，push」。
- `repo\marketplace.json` 改三處（`version` → 0.1.1、`url` → `…/releases/download/v0.1.1/willy-probe-0.1.1.zip`、`sha256` → `bf742557…973f15`），`git commit -m "marketplace: 0.1.1"` → `git push origin main`，2.7 秒，`4a6e3ff..b00768d  main -> main`（PowerShell 把 git 的 stderr 進度行當成錯誤顯示，實際 push 成功）。

### 1-3 只用內建 claude.exe 更新
- **找 exe 的方法**：`Get-ChildItem "$env:LOCALAPPDATA\Claude-Data\claude_*\claude-code\*\*\claude.exe"`，約 40–70 毫秒，找到兩支（2.1.304、2.1.308）；依資料夾版本號排序取最大 → `…\claude-code\2.1.308\c17a819afb40\claude.exe`。這支剛好也是目前這個桌面版 session 正在跑的那支（process 2480、30608）。穩定性：路徑中 `claude_pzs8sxrjxfjjc`（Appx 套件家族名）與雜湊夾名都不固定，必須用萬用字元找；兩個版本夾並存時要自己排序挑最大的（舊的 2.1.304 是更早的版本殘留）。Glob 工具掃這個資料夾會逾時，要用 PowerShell。
- **踩到的坑 A：GitHub raw 的 CDN 快取**。push 後約 1 分鐘（02:39:14）跑 `plugin marketplace update willy-spike2`：輸出 `Successfully updated marketplace: willy-spike2`，但寫進 `~\.claude\plugins\marketplaces\willy-spike2` 的內容**仍是 0.1.0**（同時間 curl／Invoke-WebRequest 已拿到 0.1.1，回應標頭 `Cache-Control: max-age=300`、`X-Cache: HIT`、`Source-Age: 67`）；接著 `plugin update willy-probe@willy-spike2 --scope local` 回 `√ willy-probe is already at the latest version (0.1.0).`（1.8 秒，exit 0）。第二次 marketplace update（02:39:41）仍是 0.1.0。到 02:40:51（push 後約 2.5 分鐘）第三次 marketplace update 才拿到 0.1.1。→ **清單網址放在 raw.githubusercontent.com，換版後最多要等約 5 分鐘（max-age=300）客戶端才抓得到；`marketplace update` 不會報錯，只會悄悄抓到舊內容，`plugin update` 會說「已是最新」。**
- **踩到的坑 B（我的失誤）**：想看除錯輸出，跑了 `claude.exe --debug plugin marketplace update willy-spike2`；`--debug` 把後面的 `plugin` 當成篩選字，剩下的 `marketplace update willy-spike2` 變成一個提示詞，內建 claude 直接開了一個 print 模式的對話（02:40:00–02:40:28，在這個夾、掛著 willy-probe）。它只讀了報告、回覆「我不確定你想做什麼」並再問一次是否 push；沒有改任何檔（檢查過測試夾與 repo，`git status` 乾淨）。副作用：`probe.jsonl` 多了一個 session 的 SessionStart/UserPromptSubmit/PreToolUse/Stop（02:40:00–02:40:28），分析事件時要排除。
- **正式指令與輸出**（cwd = `upgrade-spike`，用 `& "<內建 claude.exe>"` 執行，沒有跳出任何權限詢問或對話框）：
  1. `plugin marketplace update willy-spike2`（1.3 秒，exit 0）：
     ```
     Updating marketplace: willy-spike2...Downloading marketplace from https://raw.githubusercontent.com/jielle2453/wiki-with-willy-spike2/main/marketplace.json
     Validating marketplace data
     Saving marketplace to cache
     √ Successfully updated marketplace: willy-spike2
     ```
  2. `plugin update willy-probe@willy-spike2 --scope local`（2.1 秒，exit 0）：
     ```
     Checking for updates for plugin "willy-probe@willy-spike2" at local scope…
     √ Plugin "willy-probe" updated from 0.1.0 to 0.1.1 for scope local (C:\Users\Jie\Desktop\llm-wiki-test\upgrade-spike). Restart to apply changes.
     ```
- **沒有**出現像畫面那樣的「會覆寫本機變更」警告，也沒有跳權限詢問（是在 auto 模式的 session 裡跑，且指令寫在我自己的 PowerShell 工具呼叫裡；要注意在非 auto 模式下 PowerShell 呼叫本身會被問權限）。
- **cache 變化**：`cache\willy-spike2\willy-probe\` 多了 `0.1.1\`（`.claude-plugin\plugin.json`、`hooks\hooks.json`、`hooks\probe.py`）；舊的 `0.1.0\` 還在，多了 `.orphaned_at` 標記檔，`.in_use\2480`、`.in_use\30608`（正在使用它的兩個 claude process id）仍在。`installed_plugins.json`：version `0.1.1`、installPath 指向 `0.1.1`、`lastUpdated` = 2026-10-22T18:41:02Z、scope `local` 與 projectPath 不變。測試夾 `settings.local.json` 沒變。
- 輸出自帶一句「Restart to apply changes」。

### 1-4 同對話與新對話
- 同一個對話（更新後繼續）：`probe.jsonl` 之後的 PreToolUse／PostToolUse 仍記 `probe: 0.1.0`、`plugin_root …\0.1.0`（02:41:31–02:41:32 確認）。→ 已開的對話不會換版。
- 新對話（使用者在同夾開，不重開 App）：使用者回報開場「0.1.1」；`probe.jsonl` 02:42:36 SessionStart `probe: 0.1.1`、`source: startup`、`plugin_root …\0.1.1`。**不用重開 App，新開對話即生效。**
- 畫面（使用者截圖，外掛詳細頁）：標題「Willy probe」＋藍色標籤「新」；副標「來自 willy-spike2 · 0.1.1 · 於 4 分鐘前 更新」；右上有「⋮」、灰掉不能按的「更新」按鈕、開關（開）；分頁「總覽」「掛鉤 · 5」；描述「spike 用的假 plugin：五個 hook 各記一行，不做任何事。驗完刪除。」。→ 畫面顯示 0.1.1，且 App 不用重開就同步（「4 分鐘前更新」對應 CLI 更新的時間）；「更新」按鈕因已最新而是灰的。

### 1-5 沒先 marketplace update、直接 plugin update
- 使用者同意「0.1.2 → 0.1.0 → 0.1.1 → 0.1.2」這一串 push（之後不再逐次詢問，每次都記時間）。
- 02:46:53 push 0.1.2（`b00768d..9b0da88`）；02:46:58（5 秒後）**不跑 marketplace update**，直接 `plugin update willy-probe@willy-spike2 --scope local`（3.1 秒，exit 0）：
  ```
  Checking for updates for plugin "willy-probe@willy-spike2" at local scope…
  √ Plugin "willy-probe" updated from 0.1.1 to 0.1.2 for scope local (C:\Users\Jie\Desktop\llm-wiki-test\upgrade-spike). Restart to apply changes.
  ```
  `marketplaces\willy-spike2` 內容也變成 0.1.2、cache 多 `0.1.2\`。→ **`plugin update` 會自己先重抓清單，只要一個指令就夠，不必先跑 marketplace update。**
- 注意：這次 5 秒就拿到新版，坑 A 的 GitHub raw 5 分鐘快取沒發作；兩次結果不一致 = 快取是否命中看運氣（不同邊緣節點），設計上不能假設 push 後立刻抓得到。

### 1-6 回舊版（0.1.0）
- 02:47:10 push 0.1.0（`9b0da88..80a5fdb`），馬上跑 `marketplace update`（成功，但寫入的仍是 0.1.2）→ `plugin update`：`√ willy-probe is already at the latest version (0.1.2).`（又是 raw 快取：再次證實 push 後立刻抓可能拿到舊清單，而且完全沒報錯）。
- 等到 02:52 raw 已回 0.1.0 後重跑（`marketplace update` + `plugin update`，共 3.5 秒，exit 0）：
  ```
  √ Plugin "willy-probe" updated from 0.1.2 to 0.1.0 for scope local (C:\Users\Jie\Desktop\llm-wiki-test\upgrade-spike). Restart to apply changes.
  ```
  `installed_plugins.json` version `0.1.0`、installPath `…\0.1.0`。→ **會降版**（版本字串不同就視為更新，不比大小），不必 uninstall/install。cache 目錄 0.1.0、0.1.1、0.1.2 都留著。
- 回到 0.1.1（第二段前置）：02:53:03 push 0.1.1（`80a5fdb..95b81ce`），每 10 秒跑一次 `marketplace update` 檢查，直到 02:57:50（push 後 4 分 47 秒、第 25 次重試）才拿到 0.1.1，與 `max-age=300` 吻合；接著 `plugin update` → `updated from 0.1.0 to 0.1.1`，installed_plugins.json version 0.1.1。**同一個網址換版，客戶端實際看到新內容最慢要近 5 分鐘。**

## 第二段：設定檔開自動更新

### 2-1 改 settings.local.json
- 使用者同意後，在測試夾 `.claude\settings.local.json` 的 `extraKnownMarketplaces.willy-spike2` 加 `"autoUpdate": true`。改之前原文存 `baseline\settings.local.before-autoupdate.json`、之後存 `baseline\settings.local.after-autoupdate.json`（改後內容：`extraKnownMarketplaces.willy-spike2 = { source:{source:"url",url:…}, autoUpdate:true }`，`enabledPlugins` 不變）。
- 起點狀態：已安裝 willy-probe 0.1.1（cache 內有 0.1.0、0.1.1、0.1.2 三個夾，是前面測試留下的；判斷「出現 0.1.2」要看 `installed_plugins.json` 的 version／installPath，不能只看夾存不存在）。
- 2-1 後，使用者回覆時機器時鐘已是 09:02（前面最後一筆是 02:57，中間使用者離開了約 6 小時；這段期間沒有任何新的 SessionStart，installed_plugins.json 仍是 0.1.1）。
- **T0 = 2026-10-23 09:02:13 push 清單換成 0.1.2**（`95b81ce..bdaf837`）。這之後不再手動跑任何 `claude plugin` 指令，只觀察自動更新。

### 2-2 新對話後等自動更新（第一次，不重開 App）
- 使用者在 T0（09:02:13）之後約 29 分鐘才開新對話（09:31:18 SessionStart，`probe: 0.1.1`、`source: startup`；09:31:18 UserPromptSubmit 即「第一句話」）。T0 已過 29 分鐘，raw 快取早已更新。
- 每 20 秒檢查 `installed_plugins.json` 的 willy-probe 版本與 `marketplaces\willy-spike2` 內容，從 09:31:44 檢查到 09:46:10（第一句話後 14.9 分鐘，超過任務的 15 分鐘上限）：**version 一直是 0.1.1、`marketplaces\willy-spike2` 一直是 0.1.1 內容（沒被重抓，LastWriteTime 仍是 02:57:50）、`known_marketplaces.json` 的 `lastUpdated` 仍是 2026-10-22T18:57:50Z、cache 夾沒變**；期間沒有新的 SessionStart；畫面提示請使用者另說（尚未問）。
- `known_marketplaces.json` 裡 `willy-spike2` 的記錄只有 `source`、`installLocation`、`lastUpdated`，**沒有 `autoUpdate`**（測試夾 `settings.local.json` 的 `autoUpdate: true` 沒有被帶進這個全域登記簿）。
- 結論（暫）：local 範圍 settings 的 `extraKnownMarketplaces.<name>.autoUpdate: true` 在 15 分鐘內**沒有觸發背景更新**。依任務下一步：關掉 App 重開再等一次。

### 2-3 重開 App 前的狀態檢查（2026-10-23 09:48）
- 09:47:40 使用者開了新對話（SessionStart `probe: 0.1.1`、`source: startup`），但用 `Get-CimInstance Win32_Process` 看：桌面版主程式 `Claude_3.6556.1.0…\app\claude.exe`（PID 31184）啟動時間仍是 2026-10-23 01:05:16，**App 沒有真的關掉重開**，只是開新對話。
- 此刻 `installed_plugins.json` 仍是 willy-probe 0.1.1（`lastUpdated` 2026-10-22T18:57:52Z）、`marketplaces\willy-spike2` 的 LastWriteTime 仍是 02:57:50、`known_marketplaces.json` 的 `lastUpdated` 仍是 18:57:50Z；測試夾 `settings.local.json` 仍有 `autoUpdate: true`；清單 push 的是 0.1.2（T0 = 09:02:13）。
- 下一步：請使用者完全結束桌面版（工作列右下角圖示 → 結束，確認主程式 PID 31184 消失）再重開，開新對話後記第一句話時間，從那刻起每 2 分鐘看一次，最多 15 分鐘。

### 2-4 重開 App 後再等一次（2-2 的重做）
- 09:49:20 桌面版主程式（PID 12568）重新啟動（舊的 31184 已消失）。使用者 09:49:48 打第一句話（對話是 resume 接續，09:49:38 的 SessionStart `source: resume`，`probe: 0.1.1`）。
- 背景監看每 20 秒一次，09:50:06 → 10:04:46（第一句話後約 15 分鐘）：`installed_plugins.json` 的 willy-probe **一直是 0.1.1**、`known_marketplaces.json` 的 willy-spike2 `lastUpdated` 一直是 2026-10-22T18:57:50Z、`marketplaces\willy-spike2` LastWriteTime 仍是 02:57:50、cache 一直只有 0.1.0／0.1.1／0.1.2 三夾（都是舊的）。畫面上有沒有提示：未詢問使用者（無新 SessionStart 以外的事件）。
- 清單當時是 0.1.2（T0 09:02:13 push），距 push 已超過 1 小時，raw 快取不是原因。
- **結論：測試夾 `.claude\settings.local.json` 裡 `extraKnownMarketplaces.willy-spike2.autoUpdate: true`，不論有沒有重開 App，兩次共約 30 分鐘都沒有觸發自動更新。**可能原因（未驗證）：(a) local 範圍的設定沒有被帶進全域登記簿 `known_marketplaces.json`（那邊沒有 `autoUpdate` 欄位），背景更新只看登記簿；(b) 官方說的是「官方市集預設開、第三方預設關」，開關可能只在 `/plugin` 畫面或使用者範圍設定才有效。要驗 (a) 得改使用者範圍的檔，任務規定不碰，所以沒試。

### 2-3/2-4（步驟 3、4）
- 步驟 3「沒有的話：關掉 App 重開再等一次」：已做，沒有出現 0.1.2，所以沒有同對話／新對話／`/reload-plugins` 的觀察。
- 步驟 4 用手動更新（第一段）的資料代替：換版後第一次開場（1-4，0.1.0→0.1.1）的 SessionStart payload keys = `cwd, hook_event_name, scratchpad_dir, session_id, source, transcript_path`，`source: startup`；resume 時多 `context_tokens, estimated_cache_write_usd, prompt_cache_likely_expired, seconds_since_last_response, session_title`，`source: resume`。**沒有任何 plugin 版本欄位。** hook 自己讀得到：環境變數 `CLAUDE_PLUGIN_ROOT`（路徑結尾就是版本夾，如 `…/willy-probe/0.1.1`）、`CLAUDE_PLUGIN_DATA`；hook 自己的版本常數 `VERSION`；`transcript_path` 檔內的 `version` 欄位是 Claude Code 版本（`claude_version` 在 Windows 這邊記到 null，未深究）。所以「換版後第一次」要自己在 `CLAUDE_PLUGIN_DATA` 記上次看到的版本（或 root 路徑）再比對。

### 2-5 還原 autoUpdate
- 2026-10-23 10:05 把 `settings.local.json` 還原成改之前的內容（沒有 `autoUpdate`），存檔 `baseline\settings.local.after-revert.json`。目前狀態：willy-probe 0.1.1 已安裝，清單在 GitHub 上是 0.1.2。

## 收尾

### 收尾-1 解除安裝與比對（2026-10-23 10:08，PATH 上 claude 2.1.308，在 upgrade-spike 夾）
- `claude plugin uninstall willy-probe@willy-spike2 --scope local` → `√ Successfully uninstalled plugin: willy-probe (scope: local)`（1.0 秒，exit 0）。
- `claude plugin marketplace remove willy-spike2 --scope local` → `√ Successfully removed marketplace: willy-spike2 (from local settings)`（0.8 秒，exit 0）。
- 測試夾 `.claude\settings.local.json` 變成 `{"enabledPlugins": {}, "extraKnownMarketplaces": {}}`。
- `~\.claude\settings.json` SHA256 = `AC9C560C…57A1C`，與基準**相同**（沒被改）。
- `installed_plugins.json`：willy-probe 那筆已移除，8 筆基準 key 都在。內容差異只有 4 筆官方 plugin（frontend-design、plugin-dev、skill-creator、code-review）的 `version`／`installPath`／`gitCommitSha` 從 `af59ee3a1051` 變 `465f09344424`、`lastUpdated` = 2026-10-23T01:08:25Z（本機時間 09:08）。**這不是我們的指令**：是 App 對官方市集的背景自動更新，剛好落在第二段等待期間。
- `known_marketplaces.json`：willy-spike2 已移除，4 個基準市集都在；只有 `claude-plugins-official.lastUpdated` 變成 2026-10-23T01:59:27Z（本機 09:59），同為背景更新。
- 殘留（沒有刪，留作證據；要清可直接刪）：`~\.claude\plugins\cache\willy-spike2\`（0.1.0／0.1.1／0.1.2 三夾）、`~\.claude\plugins\data\willy-probe-willy-spike2\probe.jsonl`。`marketplaces\willy-spike2` 已不存在。
- **意外的旁證**：同一段時間內，官方市集的 4 個 plugin 在背景被自動更新到新版，而我們的第三方市集（`autoUpdate: true` 只寫在 local settings）沒有。與官方文件「官方市集預設開、第三方預設關」一致，也再次說明 local 設定的 `autoUpdate` 沒有被採用。

### 收尾-2 GitHub repo
- 使用者同意後，`gh repo edit jielle2453/wiki-with-willy-spike2 --visibility private --accept-visibility-change-consequences`（exit 0），`gh repo view` 確認 `visibility: PRIVATE`。token 沒有 `delete_repo`，**repo 還在，要請使用者到 GitHub → repo Settings → Danger Zone 自己刪**。

## 結論

### 做得到的
**路一：AI 在對話裡代跑更新（B-014 / B-409 第 1 條）— 做得到，建議用這條**
1. 找內建 exe：PowerShell `Get-ChildItem "$env:LOCALAPPDATA\Claude-Data\claude_*\claude-code\*\*\claude.exe"`（約 50 毫秒），多個版本夾時依版本號取最大。不要用 Glob 工具掃（會逾時）。
2. 跑 `"<exe>" plugin update <名稱>@<市集> --scope <範圍>`，**一個指令就夠**（會自己先重抓清單），約 2–3 秒，exit 0。輸出 `updated from X to Y … Restart to apply changes.`
3. 生效：已開的對話不會換版；**新開對話立即是新版，不用重開 App**；外掛詳細頁同步顯示新版（「於 N 分鐘前 更新」）。
4. 客戶要點的東西：**零**（不用點畫面的更新鈕）。只要在 AI 的工具呼叫被允許的前提下（auto 模式沒跳詢問；非 auto 模式 PowerShell 呼叫本身會問一次權限）。
5. 沒出現「會覆寫本機變更」警告；cache 會留舊版夾（舊版標 `.orphaned_at`），不會自動清。
6. 版本字串不同就更新，不比大小：回舊版（降版）也行，不必 uninstall/install。

### 要在設計裡處理的
- **清單網址的快取**：放在 raw.githubusercontent.com，push 後最慢約 5 分鐘（`max-age=300`）客戶端才抓得到新內容；`marketplace update` 不報錯，只會悄悄寫入舊內容，`plugin update` 說「已是最新」。要嘛換版後等 5 分鐘再公告，要嘛清單改放沒這種快取的地方（如 Release asset 網址或自己的網域）。兩次測試結果不一致（有一次 5 秒就拿到），不能假設立即生效。
- **找不到 exe 的備案**：路徑含 Appx 套件家族名與雜湊夾名，必須用萬用字元；版本夾並存時要排序；客戶若沒有桌面版內建 exe（純 CLI 使用者）要退回 PATH 上的 `claude`。
- **舊版夾殘留**：cache 一直累積，要不要清、誰來清要決定。
- **更新提醒**：`claude` 本身會輸出「Restart to apply changes」，但實際上新開對話就生效；對客戶的話術要講「新開一個對話」而不是「重開 App」。
- **指令誤用**：`claude --debug plugin …` 會把 `plugin` 當 debug 篩選字、剩下變成提示詞，開出一個意外的對話。AI 代跑時不要在 `plugin` 前面加任何旗標；要看除錯輸出的做法本次沒試出來。
- **換版後第一次**：SessionStart payload 沒有任何 plugin 版本欄位（`source` 為 `startup`／`resume`）；hook 自己靠 `CLAUDE_PLUGIN_ROOT`（路徑結尾即版本）或內建版本常數，並自行在 `CLAUDE_PLUGIN_DATA` 記上次版本來判斷。
- **使用者範圍登記簿會被 local 安裝寫入**：local 範圍的 install/uninstall 會在 `~\.claude\plugins\installed_plugins.json` 與 `known_marketplaces.json`（全域檔）多／少一筆（帶 `scope: local`＋`projectPath`），不影響其他範圍，但「使用者範圍乾淨」的驗證判準要寫成「這兩筆出現又消失」，不是「檔案不變」。
- **自動更新（路二）不跑**：見下。

**路二：設定檔開 `autoUpdate`（D-171 / B-409 第 2 條）— 本次測不出效果**
- 在測試夾 `settings.local.json` 的 `extraKnownMarketplaces.willy-spike2` 加 `"autoUpdate": true`，清單換成 0.1.2：不重開 App 等 14.9 分鐘、重開 App 後再等約 15 分鐘，**版本都沒變、市集沒被重抓、畫面沒有提示**。`known_marketplaces.json` 的 willy-spike2 沒有 `autoUpdate` 欄位。同一段時間官方市集的 4 個 plugin 卻被背景更新，說明背景更新機制本身有在跑，只是不採用我們的 local 設定。
- 沒試的替代做法（要碰使用者範圍檔，任務規定不碰）：把 `autoUpdate` 寫進使用者範圍 `settings.json` 或 `known_marketplaces.json`、或在 `/plugin` 畫面開市集的自動更新。若設計想靠路二，需要另一輪、允許改使用者範圍的 spike 才能下結論。

### 兩條路哪條適合給客戶
**路一（AI 在對話裡代跑 `plugin update`）。** 它實測穩定、約 3 秒、客戶零點擊、不用重開 App，降版也行；路二在客戶電腦上最理想的寫法（local/專案設定）沒有生效，又只是「某個時間點的背景更新」（官方說第一句話後隨機 10 分鐘內），客戶無法確認更新了沒。路一要處理的只有清單快取延遲與找 exe 的備案。

### 收尾-3 本機殘留清除（使用者同意）
- 已刪 `~\.claude\plugins\cache\willy-spike2\`（12 個檔）與 `~\.claude\plugins\data\willy-probe-willy-spike2\`（`probe.jsonl`，1 個檔）。`marketplaces\willy-spike2` 先前已不存在；兩個登記簿檔裡已無 `willy` 字樣。本報告引用的 probe.jsonl 內容已摘在前面各段。
- **GitHub repo 狀態**：使用者說已刪，但清除當下 `gh repo view`／`gh api repos/jielle2453/wiki-with-willy-spike2` 仍回傳該 repo（`visibility: PRIVATE`）。可能是使用者刪的是別處或還沒完成；需再確認。
