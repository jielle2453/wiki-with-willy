# 流程

日期：2026-10-11
給誰看：你、設計 session、實作 session、測試 session、確認 session。
什麼時候更新：只在設計 session 改。

**一句話：討論與決策在設計 session，實作、測試、確認各開新 session，發行回到設計 session。**

只保留五個概念，不保留表單：驗收準則先於實作；驗證的人不是實作的人；驗證（測試綠）與確認（真實環境跑過）是兩件事；決定要有字和日期；發行要有明確授權，結案要查類似問題。

## 一、角色

| 角色 | 是誰 | 做什麼 | 看得到什麼 |
|---|---|---|---|
| 決策者 | 你 | 拍板、核可 spec、授權發行 | 全部 |
| 設計 session | 跟你討論的那個 session | 討論、寫決策、寫 spec、發行、結案 | 全部 |
| 實作 session | 新開，在 repo | 照 spec 改程式、補測試、commit | 只有 spec |
| 測試 session | 新開，在 repo | 對驗收準則逐條驗、跑測試 | 只有 spec，不看實作過程 |
| 確認 session | 新開，桌面版，在測試 vault 的總資料夾 | 用客戶會說的話實際操作 | 只有 spec 的確認情境 |

三個新 session 沒有設計 session 的記憶。**spec 是它們唯一的輸入，必須能單獨讀懂。**

## 二、位置

| 東西 | 放哪 |
|---|---|
| repo | `C:\Users\Jie\Desktop\llm-wiki-j\` |
| 設計文件 | `repo\design\`（本檔、conventions、architecture、decisions、incidents、backlog、specs\、reports\） |
| 產品依據 | `repo\design\research\`：研究報告、行銷文件、Karpathy 原文。唯讀 |
| 舊程式 | `C:\Users\Jie\Desktop\third party\`：舊的兩個 plugin。唯讀，路徑固定 |
| 測試環境 | `C:\Users\Jie\Desktop\llm-wiki-test\`，repo 之外 |

測試環境：

```
llm-wiki-test\
├─ 來源\            假客戶資料夾
├─ 來源.zip         乾淨備份，每次確認前還原
└─ 總資料夾\        由 plugin 建出來，名字建置時取
```

plugin 在 CLI 與 VS Code 用 `claude --plugin-dir "C:\Users\Jie\Desktop\llm-wiki-j"` 載入；確認 session 用**桌面版**，plugin 由本機 marketplace 載入（做法在 B-008 定）。確認 session 開在 `總資料夾\`。不放在同步資料夾底下，除非那一題就是要測占位檔。

## 三、步驟

1. **討論與決策**（設計）。每個抉擇當場寫進 `decisions.md`。你說「可以」才算定；只在對話裡講過的不算。
2. **寫 spec**（設計）。一題的大小以實作 session 一次對話做得完為準。**驗收準則沒寫完，spec 不算完。** 你看過說可以，狀態改 `approved` 才交出去。
3. **實作**（新 session，在 repo）。只給 spec。只改「可以動」列的檔。有疑義停下來回報，不自己決定。做完跑全部測試，commit 不 push。回報改了哪些檔、commit hash、測試結果、疑義。
4. **測試**（新 session，在 repo）。只給 spec。逐條驗，每條寫過或不過附證據。不改程式。報告存 `reports/<題目>-測試.md`。
5. **確認**（新 session，在測試 vault；也可以你親自做）。先還原來源。照「確認情境」操作，記實際輸出與落差。不改程式。報告存 `reports/<題目>-確認.md`。
6. **發行**（設計）。測試與確認都過，**你說「發行」才做**：併回 main、bump、同 commit 寫 CHANGELOG、tag、push。
7. **結案**（設計）。把實作回報的 commit hash、測試與確認報告路徑填進 spec 紀錄節。對 `incidents.md` 與舊程式碼查類似問題，有就進 backlog，沒有就在 spec 寫「已查，無」。spec 改 `done` 附版號。

## 四、不過關時

| 情況 | 怎麼走 |
|---|---|
| 測試不過 | 報告交給實作 session，只修報告列的項目，回步驟 4 |
| 確認不過 | 設計 session 判斷是 spec 錯還是實作錯。spec 錯回 1；實作錯回 3 |
| 實作中發現別的 bug | 不順手修，記進 backlog，另起一題 |
| 實作 session 對 spec 有疑義 | 回設計 session 決定，更新 spec 再繼續 |
| 任何 session 想改 spec | 不行，只有設計 session 能改 |

## 五、spec 範本

```markdown
# <題目>
日期：
狀態：draft ｜ approved ｜ implemented ｜ verified ｜ confirmed ｜ released ｜ done
版本：（發行後填）

## 目的
解決什麼、對應哪個使用者時刻。

## 決策
引用 decisions.md 的編號，或列：選了什麼、為什麼、放棄了什麼。

## 要做的事
具體到檔案與函式。

## 來源程式對照
舊程式在 `C:\Users\Jie\Desktop\third party\`。列出這一題要參考的舊檔與函式，標：照搬、改介面、不要。沒有對照的寫「全新」。

## 資料與介面格式
這一題碰到的 JSON 欄位與狀態值、`kb.py` 子命令的參數與 `--json` 輸出、hook 的輸入輸出。寫到能直接當測試用。

## 可以動 / 不可以動
兩份檔案清單。

## 驗收準則
逐條、可打勾，每條寫怎麼驗：指令、預期輸出。

## 確認情境
在測試 vault 上的操作與預期結果，客戶視角。

## 不做
明確排除。

## 紀錄（設計 session 結案時填）
- 實作 commit：
- 測試報告：
- 確認報告：
- 發行版號：
- 類似問題查核：
```

## 六、開場訊息

實作 session：

```
你在 C:\Users\Jie\Desktop\llm-wiki-j。讀 design/specs/<題目>.md 並照它實作。
- 只改 spec「可以動」列的檔。
- spec 有疑義或做不到時停下來回報，不要自己決定。
- 寫完跑 python -m unittest discover tests。
- commit 訊息開頭寫 spec 題目。不要 push。
回報：改了哪些檔、commit hash、測試結果、疑義清單。
```

測試 session：

```
你在 C:\Users\Jie\Desktop\llm-wiki-j。讀 design/specs/<題目>.md。你的工作是驗證，不是修改。
- 對「驗收準則」逐條執行，每條寫 過／不過 與證據。
- 跑 python -m unittest discover tests。
- 不改任何程式。不看 git log 以外的實作過程。
報告存成 design/reports/<題目>-測試.md，最後給我一段結論。
```

確認 session：

```
你在 C:\Users\Jie\Desktop\llm-wiki-test\總資料夾，plugin 由 --plugin-dir 載入。
- 先確認「來源」已從 來源.zip 還原。
- 讀 C:\Users\Jie\Desktop\llm-wiki-j\design\specs\<題目>.md 的「確認情境」，用客戶會說的話實際操作，記每一步的實際輸出與落差。
- 不改 plugin 的程式。
報告存成 design/reports/<題目>-確認.md，最後給我一段結論。
```

## 七、硬規矩

1. 驗收準則先於實作。
2. 測試與確認 session 只看 spec。
3. 不 push、不 bump，除非你在設計 session 說「發行」。
4. 發現別的 bug 另起一題。
5. 決策有字有日期，寫進檔案。
6. 只有設計 session 能改 spec。
