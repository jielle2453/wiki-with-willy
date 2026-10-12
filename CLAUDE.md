# wiki-with-willy — 給改 repo 的 Claude

日期：2026-10-12
給誰看：改這個 repo 的 Claude 與人。怎麼用 plugin 看 README.md，設計與決策看 design/。
什麼時候更新：目錄或流程變了的那個 commit 一起改。

## 一、原始碼地圖

本清單會漂，以 `git ls-files` 為準。「尚未」是 design/architecture.md 第九節計畫中、這版還沒有的目錄。

| 路徑 | 用途 |
|---|---|
| `.claude-plugin/plugin.json` | plugin manifest：名稱、版號、描述 |
| `.claude/settings.json` | repo 內的權限：只放行測試與 `kb.py` |
| `README.md` `CHANGELOG.md` `LICENSE` | 給使用者的說明、一版一段的變更、MIT |
| `.gitattributes` `.gitignore` | 行尾一律 LF；不進 repo 的東西 |
| `config/defaults.json` | 門檻與預設數字，每個鍵旁一個 `_why_<鍵>`；目前只有 `schema_version` |
| `scripts/kb.py` | 唯一 CLI 入口（D-013）。不動 `sys.path` |
| `scripts/kb/cli.py` | argparse 主程式；子命令註冊表 `COMMANDS` |
| `scripts/kb/version.py` | 子命令 `version` |
| `scripts/kb/_lib/paths.py` | `plugin_root()`、`read_manifest()`：從 `__file__` 往上找，不寫死路徑 |
| `scripts/kb/_lib/io.py` | UTF-8、LF、原子寫入的讀寫函式 |
| `tests/` | `unittest`；`test_budget.py` 守常駐預算（D-070）、`test_version_sync.py` 守版號對齊、`test_repo_hygiene.py` 守編碼與行尾 |
| `tests/fixtures/` | 測試用範例檔 |
| `design/` | 流程、約定、架構、決策、spec。實作 session 只能在 `backlog.md` 新增一行 |
| `rules/` | 尚未。常駐規則 `core.md` 等 |
| `skills/` | 尚未。六個 skill |
| `commands/` | 尚未。build 與 manage 指令 |
| `hooks/` | 尚未。`hooks.json` 與守門腳本 |
| `agents/` | 尚未 |
| `templates/` | 尚未。卡與報告範本 |
| `skeleton/` | 尚未。客戶端總資料夾骨架 |
| `scripts/kb/sources/` `sinks/` `lint/` | 尚未。其餘子命令與 `_lib` 模組 |

## 二、改完要做的事

1. 跑 `python -m unittest discover tests`，全綠才算改完。
2. 動過 `.claude-plugin/plugin.json` 或 `hooks/` 要跑 plugin 驗證（agent `plugin-dev:plugin-validator`）。
3. bump 版號的那個 commit 要同時寫 `CHANGELOG.md`，不事後補。

## 三、不准

- 不寫 plugin 的絕對路徑進任何狀態檔或設定（D-019）；路徑一律從 `__file__` 或 `${CLAUDE_PLUGIN_ROOT}` 推。
- 核心只用標準函式庫。
- 不 push、不 bump，除非設計 session 說發行。
