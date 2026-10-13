# wiki-with-willy

日期：2026-10-13
給誰看：服務人員與使用者。
什麼時候更新：發行時，隨 CHANGELOG 一起。

## 這是什麼

把客戶既有的資料夾建成 AI 維護的知識庫的 Claude Code plugin：問、答與出處、剛做了什麼、退回、要我決定什麼、資料去了哪。

## 怎麼載入

開發期用 `--plugin-dir` 直接指到這個 repo 的資料夾：

```
claude --plugin-dir "<repo 路徑>"
```

這是開發期的載法，不會安裝到 Claude Code 的 plugin 快取；正式的安裝與升級方式之後補。

## 狀態

建置中。0.3.0 有守門與事務層：知識庫裡開對話時，客戶原檔只讀、排除的夾不讀、`kb.py` 不用按允許；每句話一個回合，AI 改綜合卡、檔案索引、AI 產出前先備份，說「退回上一版」就退，AI 產出會登記；一句話改超過 10 張綜合卡會先停下來問。還沒有任何 skill。

## 環境需求

只支援 Windows。要有 Python 3.10 以上，而且 PATH 上打 `python` 要能跑到它（Windows 商店的空殼不算）：守門與備份是 hook，`python` 找不到或跑太久時 Claude Code 會當作沒有 hook，照常放行。
