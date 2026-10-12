# B-007 spike：純 ASCII 路徑重測 Read 放行規則（B-006 對照組）

日期：2026-10-12
環境：Windows 11、Claude Code VS Code 擴充、permission mode = default
工作目錄：`C:\Users\Jie\Desktop\llm-wiki-test\spike-src`（純 ASCII）
目標目錄：`C:\Users\Jie\Desktop\llm-wiki-test\spike-ext`（純 ASCII，在工作目錄之外）

## 結論

**四種設定、三種載入情境（共 10 次 Read）全部跳出詢問，沒有一次被規則放行。**
換成純 ASCII 路徑後結果一樣，所以 B-006 的失敗**不是非 ASCII 路徑造成的**。問題出在規則本身的寫法或作用範圍，路徑字元集不是原因。

每次測試都先跑 deny canary `Bash(echo B007-canary)`，10 次全被 deny，所以可以排除「設定根本沒載入」。

## 方法

- 設定檔：`spike-src/.claude/settings.json`（專案層），一律用 `python set_settings.py <step>` 產生，每版都含 deny canary。
- 判定：Read 跳詢問 → 使用者照規則一律按 No → 記為「規則沒放行」。沒跳詢問而直接讀到 → 記為「放行」。
- 每步各測三種情境：寫完設定不重開／關 VS Code 重開並 resume 同一對話／開全新 session。每步換一個目標檔（a～d.txt），避免同檔被 session 記住。

## 結果

| 步 | permissions 內容（canary 之外） | 目標 | 不重開 | resume 重開 | 全新 session |
|---|---|---|---|---|---|
| 1 基線 | （無） | a.txt | 跳詢問 | — | — |
| 2 | `additionalDirectories: ["C:/Users/Jie/Desktop/llm-wiki-test/spike-ext"]` | b.txt | 跳詢問 | 跳詢問 | 跳詢問 |
| 3 | `allow: ["Read(C:/Users/Jie/Desktop/llm-wiki-test/spike-ext/**)"]` | c.txt | 跳詢問 | 跳詢問 | 跳詢問 |
| 4 | `allow: ["Read(${USERPROFILE}/Desktop/llm-wiki-test/spike-ext/**)"]` | d.txt | 跳詢問 | 跳詢問 | 跳詢問 |

canary 每格都被 deny，連第 1 步寫完、還沒重開時就已經被 deny，可見專案層 settings 會熱載入。

## 判讀（假設，未驗證）

- **第 3 步**：Claude Code 的路徑規則語法裡，`//path` 是檔案系統絕對路徑，`/path` 相對於 settings 檔，不加前綴的 `path` 相對於 cwd。`C:/...` 前面沒有 `//`，很可能被當成相對路徑，所以比對不到。下一輪可以測 `Read(//C:/Users/Jie/Desktop/llm-wiki-test/spike-ext/**)` 和 `Read(//c/Users/...)`。
- **第 4 步**：規則字串裡的 `${USERPROFILE}` 可能不會做環境變數展開。可以改測 `Read(~/Desktop/llm-wiki-test/spike-ext/**)`。
- **第 2 步**：照文件，`additionalDirectories` 應該會放行該目錄的讀取，這次卻沒有，跟預期不符。可能的原因包括：Windows 上 `C:/` 正斜線格式比對不到；VS Code 擴充對這個欄位的處理和 CLI 不同。目前無法確定是哪一個。
- B-006 報告這次沒有讀（Read 被拒），所以「兩次結果一致」是依任務描述（B-006 同樣沒放行）推得的，沒有逐項比對 B-006 的原始數據。

## 建議下一步

用同一套 canary 流程跑 B-008，候選規則依序是 `Read(//C:/…/**)`、`Read(//c/…/**)`、`Read(~/…/**)`、`additionalDirectories` 改用反斜線或 `//c/` 格式。每種規則先只測「不重開」情境，因為熱載入已經確認可用，等有規則放行了再補 resume 和全新 session。
