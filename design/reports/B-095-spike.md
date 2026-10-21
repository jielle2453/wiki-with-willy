# B-095 OneDrive 只在雲端的檔 spike 報告

日期：2026-10-22
環境：Windows 11 Home 10.0.26200、個人 OneDrive（`C:\Users\Jie\OneDrive`，Files On-Demand 開著）、Python 3.14.7、wiki-with-willy repo 目前的 main（只讀，只跑 `scripts/kb.py`）。
測試資料：全部是隨機產生的假檔（`os.urandom`、隨機英數字文字、python zipfile 產生的最小 docx／xlsx、假 PDF），沒有真資料。

## 一、結論先講

| 問題 | 結果 |
|---|---|
| 1. 開場快速比對、全掃、`kb folders add` 會不會下載只在雲端的檔 | **不會**。15 份只在雲端的檔，跑完 `folders add --check`、`folders add`、`changes --quick`、`changes`、SessionStart hook、`inventory scan` 之後，仍然 15 份 `RECALL_ON_DATA_ACCESS`、`on_disk=0`。清冊記成 `read: placeholder`、不算指紋。每支指令 0.15～0.25 秒 |
| 1b. 附帶：`kb convert` | 清冊記成 placeholder 的檔，convert 拒絕（exit 3，不下載）。**但清冊過時時（上次掃描是本機檔、之後被設成只在雲端），convert 照讀、整份下載下來**。convert 只看清冊的 `read`，不看現場屬性 |
| 2. 同一個 OneDrive 裡 `os.rename` 檔與整夾 | **留在雲端、不下載**。同夾改名、跨夾搬、整夾改名、整夾跨夾搬都是 1～2 毫秒，前後屬性一樣（`0x00500020`、`on_disk=0`）；釘選的檔搬完還是 PINNED |
| 2b. 附帶：kb 怎麼看這些搬動 | **只在雲端的檔搬了，kb 認不出是搬動**，報成「新增 3、不見 3」；本機檔（有指紋）才報成「搬動 1」。因為占位檔沒有指紋，搬動配對靠指紋 |
| 3. 送回收筒 | 本機檔：進**本機 Windows 回收筒**（看得到原位置、大小 1048576）。只在雲端的檔：`SHFileOperationW` 回 0、檔不見了，但**本機回收筒沒有這一項**（總數 123 → 124，只多了本機那份）；刪的過程沒有下載（每 20ms 看一次，只看到 `0x500020, on_disk=0`）。照微軟說明，它直接從 OneDrive 各裝置與線上一起刪，只能從 **OneDrive 網頁版回收筒**救（個人版 30 天） |
| 4. 只在雲端的檔搬出 OneDrive | **會下載**。`os.rename` 與 `shutil.move` 都成功，各約 0.3 秒；過程中看到來源 `0x400020, on_disk=1048576`（先下載完再搬），目的地是完整的本機檔（256 種位元組值，不是空殼） |
| 5. 只讀中繼資料、只開不讀、讀一個位元組 | `os.stat`、`os.lstat`、`os.scandir` 的 `entry.stat()`、`getsize`、`isfile`、`getmtime`：**不下載**。`open(..., "rb")` 開著不讀、再關：**不下載**。**讀 1 個位元組就整份下載**（1 MB 全部落地，屬性變 `0x20`） |
| 附帶 | **Python 看到的屬性是「偽裝過」的**：PowerShell 看同一個檔是 `0x501620`（含 REPARSE、SPARSE、OFFLINE），Python 看是 `0x500020`。若 process 改成「露出占位檔」模式，kb 的走訪**一份都掃不到**（total 0、18 份全報不見）。見第五節 |

## 二、做法

1. 在 `C:\Users\Jie\OneDrive\willy-onedrive-spike\` 建 `案子\信義王`、`案子\大安陳`、`私人`、`測試搬動\整夾\子夾`，放 20 份假檔（`make_files.py`）。
2. 剛建好時屬性都是 `0x20 [ARC]`。等 5 分鐘（`poll.py synced 300`）想看 OneDrive 接手，屬性一直沒變（我原本以為上傳後會出現 REPARSE，結果 Python 根本看不到 REPARSE，見第五節）；直接 `attrib +U -P` 一份，10 秒內變成 `0x00500020 [ARC|UNPINNED|RECALL_ON_DATA_ACCESS] on_disk=0`，表示已上傳、可以去水化。
3. 15 份 `attrib +U -P`（10 秒內全部變只在雲端）；`會議記錄.txt`、`mv-local.bin` `attrib +P -U`（`0x00080020 [ARC|PINNED]`）；`rb-local.bin` 不動（`0x20`，一般本機檔）。
4. 每一步前後用 `attrs.py` 印每個檔的屬性。它只用 `GetFileAttributesW`、`GetFileAttributesExW`（大小）、`GetCompressedFileSizeW`（實際占用 = `on_disk`），都不開檔內容。全部紀錄在 `attrs-log.txt`。
5. 測試知識庫：`kb init C:\Users\Jie\Desktop\llm-wiki-test\onedrive-spike\知識庫`，在裡面跑 kb 指令。

屬性旗標：`0x400000` RECALL_ON_DATA_ACCESS、`0x100000` UNPINNED、`0x80000` PINNED、`0x40000` RECALL_ON_OPEN、`0x1000` OFFLINE、`0x400` REPARSE、`0x200` SPARSE。`on_disk=0` 表示內容不在電腦上。

## 三、證據

### 1. kb 的掃描（基線：15 份只在雲端、3 份本機）

```
folders add --check：會登記「willy-onedrive-spike」（../../../../OneDrive/willy-onedrive-spike，18 份）  0.21 秒
folders add：       已登記…18 份；清冊：入冊 18 份                                                  0.23 秒  → CLOUD-ONLY 15
changes --quick：    跟上次掃描時相同，沒有變動。                                                     0.20 秒  → CLOUD-ONLY 15
changes：            跟上次掃描時相同，沒有變動。                                                     0.19 秒  → CLOUD-ONLY 15
hooks/session.py（SessionStart，startup）                                                            0.20 秒  → CLOUD-ONLY 15
inventory scan：     清冊已更新：總計 18 份…不算指紋：…占位檔 15…                                      0.25 秒  → CLOUD-ONLY 15
```

清冊（`.system/inventory.json`）：15 份 `read: placeholder`、指紋 0 筆；3 份本機檔 `read: ok`、指紋 1 筆。
（SessionStart 第一次用 bash 餵 payload 時 JSON 跳脫錯了，hook 記了一行 `JSONDecodeError` 到 `hook-errors.log` 就不輸出；改用 python 產 payload 後正常。這是我測試操作的錯，不是 hook 的問題。）

為什麼不下載：`inventory.walk` 用 `os.scandir` 取屬性，`read_kind` 遇到 `PLACEHOLDER_ATTRS = 0x40000|0x400000|0x1000` 直接回 `placeholder`，不進 `sha256_of`／`content_fp`。`kb folders add` 的憑證掃描 `credentials._scan` 只用 `os.walk` 看名字。

### 1b. `kb convert`

```
random-c.txt（清冊 placeholder）：讀不到：只在雲端、還沒下載到電腦；已記進待處理；exit=3
  之後 CLOUD-ONLY attrs=0x00500020 on_disk=0
會議記錄.txt：先 attrib -P +U → CLOUD-ONLY 0x00500020 on_disk=0（不重掃，清冊仍是 ok）
  kb convert → 暫存檔 …\.system\temp\f-22c8f1d4.md，字數 970950，exit=0
  之後 LOCAL attrs=0x00000020 on_disk=1048626   ← 整份下載了
```

### 2. 同一個 OneDrive 裡 os.rename（`rename-test-out.txt`）

```
同夾改名 只在雲端檔   前 0x00500020 on_disk=0 → 後 0x00500020 on_disk=0   0.0008s
跨夾搬 只在雲端檔     前 0x00500020 on_disk=0 → 後 0x00500020 on_disk=0   0.0009s
跨夾搬 釘選本機檔     前 0x00080020 PINNED   → 後 0x00080020 PINNED       0.0012s
整夾改名（f1、子夾\f2 只在雲端）  兩檔前後都 0x00500020 on_disk=0           0.0014s
整夾跨夾搬（測試搬動\整夾改名 → 案子\整夾搬來）  兩檔前後都 0x00500020 on_disk=0  0.0019s
```

約 1 分鐘後再看一次（`attrs-log.txt` 01:48:51），搬過的檔仍是只在雲端。

kb 怎麼看（`kb changes --all`，`--quick` 結果相同）：

```
- 新增 3 份：案子/整夾搬來/f1.bin、案子/整夾搬來/子夾/f2.bin、私人/搬來-mv-cloud.bin
- 搬動或改名 1 份：測試搬動/mv-local.bin → 私人/搬來-mv-local.bin
- 不見 3 份：測試搬動/mv-cloud.bin、測試搬動/整夾/f1.bin、測試搬動/整夾/子夾/f2.bin
```

### 3. 送回收筒（`recycle-test-out.txt`）

`SHFileOperationW`，`FO_DELETE`、`FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT|FOF_NOERRORUI`；刪的過程另一條執行緒每 20ms 看屬性。

```
rb-cloud.bin  前 0x00500020 on_disk=0   rc=0 aborted=False 0.087s  過程只看到 ('0x500020', 0)  後 不存在
rb-local.bin  前 0x00000020 on_disk=1048576  rc=0 aborted=False 0.039s  過程 ('0x20', 1048576)  後 不存在
本機回收筒（Shell.Application Namespace(10)）：前 123 項 → 後 124 項
  只找到 rb-local.bin | 原位置 C:\Users\Jie\OneDrive\willy-onedrive-spike\測試搬動 | 大小 1048576 | C:\$Recycle.Bin\…\$RI9SAF4.bin
  rb-cloud.bin 不在本機回收筒
```

沒試 IFileOperation；SHFileOperationW 已經回答了問題。我看不到螢幕，有沒有跳 OneDrive 的「刪除的檔案會從所有地方移除」提示沒辦法確認；這次 API 沒有被擋（rc=0）。

微軟說明（[Save disk space with OneDrive Files On-Demand for Windows](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows)）：
- "When you delete an online-only file from your device, you delete it from your OneDrive on all devices and online."
- "If you delete a locally available file, it also appears in your device recycle bin."
- "You can restore deleted files and folders from your OneDrive recycle bin on the web for up to 30 days"（個人版；公司或學校帳號 93 天）
- 另見 [Restore files deleted from your OneDrive](https://support.microsoft.com/en-us/office/restore-deleted-files-or-folders-in-onedrive-949ada80-0026-4db3-a953-c99083e6a84f)。

意思是：kb 刪本機檔，本機回收筒拿得回來；刪只在雲端的檔，本機回收筒拿不回來，要去 OneDrive 網頁版回收筒；而且兩種都會同步刪掉其他裝置跟線上那份。

### 4. 搬出 OneDrive（`moveout-test-out.txt`）

```
out-cloud.bin  os.rename    前 0x00500020 on_disk=0  0.319s 沒錯誤
   過程來源：('0x400020', 0)、('0x400020', 1048576)、('0x500020', 0)   ← UNPINNED 拿掉、內容下載到 1 MB
   目的 LOCAL 0x00000020 on_disk=1048576
meta-stat.bin  shutil.move  同上，0.351s
目的檔內容：兩份都 1048576 位元組、256 種位元組值（是真的內容，不是空殼）
```

微軟說明（同一頁）："When you move files outside that OneDrive folder, the files download to the new folder and are removed from your OneDrive." 和觀察一致。

### 5. 中繼資料與開檔（`meta-test-out.txt`，每步之後等 3 秒再看）

```
os.stat / os.lstat / scandir entry.stat(兩種) / getsize / isfile / getmtime  之後都 0x00500020 on_disk=0
open("rb") 開著不讀、close                                                   之後都 0x00500020 on_disk=0
open("rb").read(1)                                                           之後 0x00000020 on_disk=1048576
```

讀 1 位元組就整份下載：OneDrive 是整份下載的同步引擎（微軟 cfapi 文件：檔案的下載策略取 app 跟同步引擎兩者中較強的那個）。

## 四、對設計的意思

1. **掃描可以放心掃 OneDrive**：開場快速比對、全掃、`folders add` 都不會下載，時間也不受雲端檔影響。
2. **convert（和 AI 用 Read 讀原檔）要看現場屬性，不能只信清冊**：清冊過時時 convert 會把整份檔下載下來。建議 convert 讀之前用 `os.stat` 的 `st_file_attributes & PLACEHOLDER_ATTRS` 再看一次（實測 stat 不會下載）。AI 的 Read 工具、守門放行的讀取也一樣：只要讀到內容就整份下載。
3. **kb 自己搬檔、改名（同一個 OneDrive 裡 `os.rename`）是安全的**：不下載、釘選狀態跟著走、很快。
4. **但使用者自己搬只在雲端的檔，kb 認不出是搬動**（報成新增加不見）。kb 自己做的搬動要自己改清冊的位置，不能靠下一次掃描配對；使用者在檔案總管搬的，對占位檔可能要另想配對方法（例如大小加 mtime 加檔名，或 OneDrive 的檔案 ID），否則卡會被當成原檔不見。
5. **刪檔送回收筒，只在雲端的檔不會進本機回收筒**。「刪了可以從回收筒拿回來」這句話對只在雲端的檔不成立，要改成「OneDrive 網頁版的回收筒，30 天內」；而且刪除會同步到其他裝置。設計上可以考慮：刪之前看屬性，只在雲端的檔換一句提醒，或先問。
6. **跨出 OneDrive 的搬動會下載整份檔**，大檔會花時間與空間，而且檔案會從 OneDrive 移除（其他裝置也沒了）。kb 若有「搬到 OneDrive 外面」的動作，要先講清楚。
7. **背景下載可能跳通知**：微軟 cfapi 文件說，不是使用者主動開的下載會跳通知，使用者可以在通知裡擋掉這個 app（之後可在設定的「自動下載檔案」頁解除）。如果使用者擋了 python，之後讀取會失敗，kb 要能把它當成「讀不到」處理。這次沒辦法看螢幕，沒確認有沒有跳。

## 五、附帶發現：Python 看到的是偽裝過的屬性

同一個檔，兩邊看到的不一樣：

```
PowerShell (Get-Item).Attributes：案子 = ReadOnly, Directory, Archive, ReparsePoint；random-b.bin = 5248544 (0x501620)
Python os.lstat：               案子 = 0x31；                                 random-b.bin = 0x500020
```

微軟 cfapi 文件（[Build a Cloud Sync Engine…](https://learn.microsoft.com/en-us/windows/win32/cfapi/build-a-cloud-file-sync-engine)）："the cloud files API always hides its reparse points from all applications except for sync engines and processes whose main image resides under %systemroot%"。Python 不在 `%systemroot%` 底下，所以預設是偽裝模式（`RtlQueryProcessPlaceholderCompatibilityMode` 實測回 1 = `PHCM_DISGUISE_PLACEHOLDER`）：REPARSE、SPARSE、OFFLINE 都藏起來，只剩 `RECALL_ON_DATA_ACCESS` 和 PINNED／UNPINNED。

`expose_test.py` 把 process 改成 2（`PHCM_EXPOSE_PLACEHOLDERS`）再跑 `inventory.scan`（純函式，不寫檔）：

```
預設（1）：total 14, unchanged 10, missing 7, added 3, placeholder 11   ← 正常（當時搬動還沒套用）
露出（2）：資料夾 0x431、雲端檔 0x501620、本機檔 0x420
          total 0, unchanged 0, missing 18, added 0                    ← 一份都沒掃到
```

原因是 `inventory.walk`：資料夾帶 REPARSE 就不往下走（第 419 行）；檔帶 REPARSE 但沒有占位屬性的（已下載的 OneDrive 檔）直接跳過（第 422 行）。在露出模式下 OneDrive 裡每個夾、每個已下載的檔都帶 REPARSE。

目前 Python 預設是偽裝模式，所以沒事。風險：如果哪天 kb 由 `%systemroot%` 底下的程式執行、某個函式庫把模式改成露出、或 Windows 改了預設，kb 會把整個 OneDrive 來源報成「全部不見」。建議設計 session 決定要不要：開場明確把 process 設成 1（偽裝），或在 walk 裡分辨雲端重解析點（tag `IO_REPARSE_TAG_CLOUD_*`）跟連結。`convert.py` 的 `_is_reparse` 也有同樣的假設。

## 六、清理與留下的東西

- 已刪 `C:\Users\Jie\OneDrive\willy-onedrive-spike\` 整夾（`shutil.rmtree`，之後 `Test-Path` 是 False）。OneDrive 其他內容沒碰。
- 本機回收筒裡只有 `rb-local.bin` 是這次放的，已經刪掉它的 `$RI9SAF4.bin`、`$II9SAF4.bin`；回收筒回到 123 項，原位置含 `willy-onedrive-spike` 的項目 0 個。
- **還留著**：OneDrive 網頁版回收筒會有這次刪掉的東西（`willy-onedrive-spike` 整夾、`rb-cloud.bin`，可能還有搬出 OneDrive 的 `out-cloud.bin`、`meta-stat.bin`），30 天後自動清掉；要馬上清得在 onedrive.live.com 的回收筒手動清空。全部是隨機假檔。
- 工作夾 `C:\Users\Jie\Desktop\llm-wiki-test\onedrive-spike\` 留著：測試腳本（`attrs.py`、`poll.py`、`make_files.py`、`meta_test.py`、`rename_test.py`、`recycle_test.py`、`moveout_test.py`、`expose_test.py`）、輸出（`attrs-log.txt`、`*-out.txt`、`session-out.json`）、測試知識庫 `知識庫\`（登記的來源已經不存在；有一條 convert 記的待處理、一份 temp 暫存檔，都是假資料）、`搬出來\`（兩份 1 MB 隨機檔）。
- repo `C:\Users\Jie\Desktop\llm-wiki-j` 沒改任何檔（只跑了 `scripts/kb.py`、`hooks/session.py`，`expose_test.py` 在自己的 process 裡 import `kb._lib`）。
