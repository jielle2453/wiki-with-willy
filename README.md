# wiki-with-willy 安裝說明（OpenAI Codex 版）

日期：2026-11-09（2026-11-08～09 在 Codex CLI 0.167.0、Windows 11 實機裝過、測過，結果在第五節）
給誰看：替客戶安裝的服務人員。Claude Code 版的安裝看 README.md。
什麼時候更新：實機測過、Codex 改了外掛或 hook 的做法時。

## 零、請 Codex 幫你裝（最快）

在 Codex 開一個對話（哪個資料夾都可以），把下面這段整段貼給它：

```
請照 https://github.com/jielle2453/wiki-with-willy/blob/codex/安裝-Codex.md 幫我安裝 wiki-with-willy 的 Codex 版：
1. 先跑 python --version、git --version 確認都有（Python 要 3.10 以上），缺的告訴我怎麼裝，先不要往下。
2. 跑 codex plugin marketplace add https://github.com/jielle2453/wiki-with-willy --ref codex
3. 跑 codex plugin add wiki-with-willy@wiki-with-willy
4. 告訴我第二節第 4 步的沙盒設定要怎麼改，等我說好再改。
5. 最後提醒我：要我自己打 /hooks 信任 6 個 hook、再開新對話；這兩步你不能代做。
```

**AI 不能代做、一定要人做的**：在 `/hooks` 按信任（Codex 刻意設計成只能人按），以及裝好後開新對話。改沙盒設定是放寬隔離，AI 會先問過你。

## 一、事前確認

| 項目 | 怎麼確認 | 為什麼 |
|---|---|---|
| Windows 上的 Codex 桌面 App 或 Codex CLI | 打得開、登入得了 | 兩種都會跑外掛的 hook（CLI 2026-11-08 實測）；IDE 擴充不支援外掛 |
| **Python 3**（3.10 以上） | 命令列跑 `python --version` 有版本號；在 Codex 裡問「跑 python --version」也要有 | 🔴 **沒有 Python 就完全沒有守門**：Codex 的 hook 起不來時會照樣讓 AI 改檔（Claude 版會擋下，Codex 不會） |
| **Git**（從 GitHub 裝才要） | 命令列跑 `git --version` 有版本號；沒有就裝 Git for Windows | Codex 從 GitHub 加市集是呼叫 git clone，沒有 git 會報「failed to run git clone … program not found」（2026-11-09 用空的 CODEX_HOME、PATH 拿掉 git 實測）；沒網路或不想裝 git 就用第二節第 1 步的 zip 備用 |
| 建議裝 OpenAI 的文件技能 | 在 Codex 打 `$skill-installer`，裝 Word、Excel、PowerPoint 的技能 | 做文件時 AI 才有現成的技能可用；沒裝也能做，只是比較慢 |

## 二、安裝

1. **從 GitHub 加市集**（建議，2026-11-09 Codex CLI 0.167.0 實測）：`codex plugin marketplace add https://github.com/jielle2453/wiki-with-willy --ref codex`。不用下載、不用挑資料夾；之後更新跑 `codex plugin marketplace upgrade wiki-with-willy` 再做第 2 步。
   - 備用（沒網路或要固定版本）：把 zip 解在一個固定的資料夾（以下叫 `<外掛夾>`），不要放在同步資料夾（OneDrive、Google 雲端硬碟）底下；🔴 **解開後把 `<外掛夾>\hooks\codex.json` 改名成 `hooks.json`**（蓋掉 Claude 版的那份：zip 是兩版共用，Codex 只跑 `hooks/hooks.json`，不改名就沒有守門），再跑 `codex plugin marketplace add <外掛夾>`。🔴 **路徑裡不能有 `#`**：Codex 會把 `#` 後面當成 git 分支，報「--ref is only supported for git marketplace sources」；已經在這種夾裡就用 `mklink /J` 建一個不含 `#` 的目錄連結再加。
2. 裝外掛：`codex plugin add wiki-with-willy@wiki-with-willy`，或在 Codex 打 `/plugins`（桌面 App 是「外掛」分頁）找到 wiki-with-willy 安裝、打開。🔴 **裝的時候會複製一份到 `%USERPROFILE%\.codex\plugins\cache\`，實際跑的是那份**：市集更新了新版要再跑一次這步才生效（先 `codex plugin remove wiki-with-willy@wiki-with-willy`）。
3. 🔴 **信任 hook**：在知識庫資料夾開 Codex 打 `/hooks`（桌面 App 是「設定 > Hooks」），最上面會寫「N hooks need review」；清單一個事件一行，**要選到那一行按 Enter 進去**才看得到個別的 hook（Source: Plugin – wiki-with-willy@wiki-with-willy），逐個按信任，willy 共 6 個（每個事件一個）。沒按就整組不跑，等於沒守門。信任記在 `%USERPROFILE%\.codex\config.toml` 的 `[hooks.state."wiki-with-willy@…:hooks/hooks.json:<事件>:0:0"]`（`trusted_hash`）；**外掛重裝、升級、hook 指令或程式改了，雜湊變了就要再按一次。**（2026-11-08 實測）
4. 🔴 **沙盒**：Windows 的 Codex（桌面 App 預設）是 `elevated` 沙盒，AI 跑的指令讀不到 `%USERPROFILE%\.codex\` 底下任何東西，外掛的 kb 程式與技能說明都在那裡，整個 willy 不能用（2026-11-08 實測，OpenAI 自家外掛也一樣）。**必做**：在 `%USERPROFILE%\.codex\config.toml` 加上（已有 `[windows]` 就只加第二行）：
   ```toml
   [windows]
   sandbox = "unelevated"
   ```
   裝之前先跟客戶講清楚兩件事：**隔離會比較弱**（AI 跑的指令能讀的範圍變大）；**這台電腦上所有 Codex 對話都會套用**，不只 willy 的知識庫。有沒有只放行外掛夾的做法還在查（jacky 2026-11-09 定：先這樣寫、再查）。
5. 開客戶的知識庫資料夾當專案，**開一個新對話**（裝好外掛後要新對話才載入）。
6. 確認：新對話裡 AI 看得到一段「【wiki-with-willy】」開頭的開場段。問 AI「你有看到 wiki-with-willy 的開場段嗎」即可。

## 三、裝好之後的差別（跟 Claude 版比）

- 知識庫裡多一份 `AGENTS.md`（Codex 讀它，內容跟 `CLAUDE.md` 一樣）。舊知識庫沒有這份的話，跑一次 `kb init` 補骨架。
- 整理新檔：Codex 有開子代理的工具就每批派一個去做；沒有就由主對話自己一批一批做（等比較久、做的時候不好插話）。做法程式都一樣，卡的內容不受影響。
- 密碼相關的動作（顯示存好的密碼、用存好的密碼開檔、讓某一種檔自動用密碼）AI 會先問客戶，**客戶下一句回「好」「可以」「同意」才做**；回別的就不做。
- 升級（「升級」「有新版嗎」）AI 不會代做：由服務人員跑 `codex plugin marketplace upgrade wiki-with-willy`、照第二節第 2 步重裝，再照第 3 步重新信任 hook（每次重裝都要，2026-11-09 實測）。從 0.34.0（市集名 `wiki-with-willy-local`）升上來的那一次，`marketplace upgrade` 用新舊哪個名字都會報錯，改照這個順序：先 `codex plugin remove wiki-with-willy@wiki-with-willy-local`、`codex plugin marketplace remove wiki-with-willy-local`，再照第二節第 1 步重新 `marketplace add`（帶 `--ref codex`）、第 2 步 `plugin add`，最後照第 3 步重新信任 hook。
- 貼圖片、貼長文字給 AI 收進知識庫：這版可能收不到，AI 會照實說。
- claude.ai 的線上文件讀不到，會記成「線上文件打不開」。

## 四、守門跟 Claude 版不一樣的地方

| | 說明 |
|---|---|
| hook 出錯就放行 | Codex 的 hook 出錯、逾時、沒跑起來時，AI 的動作照樣執行。willy 的守門自己出錯會回「不准」，但 **Python 不在、hook 沒被信任時完全沒有守門**。知識庫的 `AGENTS.md` 有交代：沒看到開場段就先不改檔。 |
| 要先信任 | 第二節第 3 步。 |
| 密碼動作 | 第三節。 |
| 攔不到的 | Codex 的網路搜尋這類雲端工具（不碰本機檔）；客戶自己另外裝的 MCP 檔案工具（Claude 版也沒管）。 |

## 五、實機測過的結果（2026-11-08～09，Codex CLI 0.167.0、Windows 11）

| 項目 | 結果 |
|---|---|
| CLI 跑外掛 hook、`${PLUGIN_ROOT}` 代換、開場段整段進模型 | 過 |
| 守門：讀寫、`apply_patch`、`view_image`、kb 指令插 `--session`（`updatedInput`）、Stop 印 `{}` | 過；擋下時 Codex 會在理由後面接「. Command: <指令>」 |
| 信任 hook | 過；一個事件一行、按 Enter 進去逐個信任（第二節第 3 步）；外掛重裝後要再信任一次 |
| 整理（「整理一下」）：派子代理做卡、子代理自己 `--done` 收批、抽日程、最後問一題 | 過（11-09，5 份假資料，卡上數字與原檔一致） |
| 問資料：查卡、回原檔核對、對不上的記待處理、問一題 | 過（11-09） |
| 密碼動作的同意（客戶下一句回「好」） | 過（UserPromptSubmit 逾時已改 30 秒：Python 冷啟動約 7.5 秒） |
| 沙盒 | `elevated` 讀不到外掛，要改 `unelevated`（第二節第 4 步，必做） |
| SessionEnd | 有被叫到，但 `codex exec` 結束時會被中斷；批次 30 分鐘後自己過期，不影響 |
| 還沒測 | 附著客戶開著的 Word／Excel／PowerPoint；貼圖、貼長文字收進知識庫；桌面 App（CLI 已測） |
