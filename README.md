# wiki-with-willy

日期：2026-10-12
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

建置中，尚無功能。目前只有殼：plugin 載得進、測試跑得綠、`python scripts/kb.py version` 印得出版號。
