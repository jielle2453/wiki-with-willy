# B-006 spike：session 開著時改 .claude/settings.json，讀取放行會不會生效

日期：2026-10-12
執行 session：C:\Users\Jie\Desktop\llm-wiki-test\spike（Claude Code VS Code extension，Windows 11）
影響決策：U-8、U-15、I-073

## 方法

- 設定檔：`spike\.claude\settings.json`（project settings）。user settings 是 `{}`，沒有 settings.local.json，也沒有 managed settings。
- 目標檔：`spike-來源\a.txt`～`d.txt`，在工作目錄外。
- 每次改設定都用 python 腳本寫入，不用 Edit。
- 權限模式：manual。一開始是 auto mode，auto mode 會自動放行工作目錄外的讀取，基線無效，那次結果已作廢。
- 判讀方式：使用者遇到 Read 的詢問一律按 No。拿到內容 = 直接讀到；回「user doesn't want to proceed」= 跳出詢問；回「Permission ... has been denied」= 被規則拒絕。
- 重開方式：關掉 VS Code，重開後接回同一個對話。
- 載入驗證（canary）：重開後的每一步，設定裡都多加一條 `deny: ["Bash(echo B006-canary)"]`，重開後先跑 `echo B006-canary`。被規則拒絕，就代表這一版 settings 確實有載入。重開後的四步 canary 都被拒絕，所以下表的「重開後」結果都是在新設定已載入的狀態下測到的。
  - 第一版 canary 是 `allow: ["Bash(echo B006-canary)"]`，但 echo 本來就可能不需要詢問，證明不了 settings 有載入，所以作廢，第 2 步因此重測。

## 結果

| 步驟 | settings.json 的 permissions | Read 目標 | 重開前（同 session 即時改） | 重開後（canary 確認已載入） |
|---|---|---|---|---|
| 1 基線 | `{}` | a.txt | 跳出詢問 | — |
| 2 | `additionalDirectories: ["C:/Users/Jie/Desktop/llm-wiki-test/spike-來源"]` | b.txt | 跳出詢問 | 跳出詢問 |
| 3a | `allow: ["Read(//c/Users/Jie/Desktop/llm-wiki-test/spike-來源/**)"]` | c.txt | 跳出詢問 | 跳出詢問 |
| 3b | `allow: ["Read(C:/Users/Jie/Desktop/llm-wiki-test/spike-來源/**)"]` | c.txt | 跳出詢問 | 跳出詢問 |
| 4 | `allow: ["Read(${USERPROFILE}/Desktop/llm-wiki-test/spike-來源/**)"]` | d.txt | 跳出詢問 | 跳出詢問 |

附帶觀察：
- `deny: ["Bash(echo B006-canary)"]` 寫入後不重開，馬上跑 echo，照常執行。重開後才被拒絕。所以 deny 規則也不會即時生效。
- 第 2 步第一次重開時沒有有效的 canary，結果也是跳出詢問，和之後有 canary 驗證的結果一致。

## 未排除的干擾因素

重開後連直接寫死的路徑（3a、3b）都沒有放行，所以這次測不出「哪種寫法有效」。下面幾個可能原因都還沒排除：
1. 路徑含非 ASCII 字元（`來源`），規則比對或路徑正規化可能因此失敗。
2. Windows 上大小寫或分隔符號的比對問題。執行時工作目錄的大小寫在 `c:\` 和 `C:\` 之間來回跳。
3. project settings 裡的 additionalDirectories 和 Read allow 規則，在 VS Code extension 的 manual 模式下可能另有處理方式。

建議下一個 spike 用純 ASCII 路徑當對照組（例如 `C:/Users/Jie/Desktop/llm-wiki-test/spike-src`），把 3b 和第 4 步各重開測一次。這次任務規定只能碰 spike\ 和 spike-來源\，所以沒有做。

## 結論

(a) **改 additionalDirectories 要不要重開才生效：至少要重開，而且這次重開後也沒生效。** 不重開就改，完全不會生效；deny 規則也一樣，settings.json 不會熱重載。重開後 canary 證明新設定已經載入，但 `spike-來源` 的讀取還是跳出詢問。所以不能指望在 session 中途寫 additionalDirectories 來放行讀取；就算重開，至少在含中文的路徑上也沒有效果。

(b) **Read 規則哪種路徑寫法有效、要不要重開：這次測到的寫法都無效。** `Read(//c/...)`（舊 plugin 寫法）和 `Read(C:/...)` 不重開、重開後都跳出詢問。可以確定的有兩點：規則一定要重開才可能生效；舊 plugin 的 `//c/` 寫法在這個環境下沒有放行效果。至於哪種寫法有效，因為路徑含中文這個干擾因素還沒排除，要等 ASCII 路徑對照測完才能判定。

(c) **規則裡的 ${VAR} 會不會展開：沒有證據顯示會展開，但這次的結果不能當作定論。** `Read(${USERPROFILE}/...)` 不重開、重開後都跳出詢問。不過同一個路徑寫死成 `C:/...` 也一樣無效，所以分不出是「沒展開」還是「路徑本身就比對不到」。在 ASCII 對照測出一種有效的寫死路徑之前，plugin 不應該假設規則裡可以用 `${CLAUDE_PLUGIN_ROOT}`。
