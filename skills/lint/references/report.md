# 健檢：輸出怎麼讀、每種事怎麼講

## 一、`kb lint --json`

- `summary`：一句話（跟上個月比），照講。`month`、`report`：這個月的報告寫在哪（`系統/健檢報告/YYYY-MM.md`，同月重寫）。
- `done`：這次替她做的（清回合備份、收起被取代的口述、標綜合卡要更新、關掉不成立的待處理、收起放太久的），挑一兩件講。
- `decide`：要她決定的件數（待處理加整理時要問的）；`most_urgent`：最急的那一件的問句，講這件就好。`set_aside`：先放著的件數。
- `ingest`：整理擱著的件數。整理做得了的是 `unsorted` 待整理、`unlinked` 沒掛實體、`synthesis_stale` 綜合卡待更新、
  `duplicates` 同一份檔在兩處，有大於 0 的就交 ingest 整理；`missing`、`unreadable`、`overdue`、`conflicts` 是要她決定的，
  已經在待處理，不交整理。她問「檢查要多久」時，交整理的那段照整理的估法講。
- `failed_checks`：這次沒查成的項目名，說一句、不展開（記在 hook-errors.log，自己讀 log 看原因，講一句白話）。
- 人讀輸出第一行同 `summary`，接「替你做了：…」、「要你決定的 N 件（最急：…）」、「整理還有：…」。

## 二、看健檢報告

報告每月一份，段是：一句話、這個月替你做了什麼、要你決定的（摺疊，寫的是當時的狀況）、先放著超過一個月的、建議下一步、數字。
講法：先一句話；再挑「建議下一步」的第一件；要她決定的只說件數與最急的一件（現在的狀況以待處理為準，不念報告那份舊的）。
「跟上次比有變好嗎」照一句話講；「這些是什麼意思」一件一件用白話講，不念數字段。服務人員要數字才講數字段。
報告不用改，也不會被手改比對；看完那件待處理（`pending_report`）照結果 `kb pending resolve`。

## 三、每種健檢的事怎麼講（`kb asks --all` 裡 `kind` 是 `pending_lint` 的，`key` 開頭分種類）

- `guard_stale`：守門好像沒在跑。請服務人員看，她不用做什麼；「你直接修」時也只能交服務人員。
- `versions_unwritable`：回合備份夾寫不進去，改了東西沒辦法退回。請服務人員看，在那之前先別大改。
- `system_link`：系統夾裡有捷徑。先自己看是什麼（`kb selftest`），講給她聽；是什麼說不準再問她。
- `inferred:agenda`、`inferred:entity`、`inferred:profile`：我猜的放太久沒確認。照結果的 `note` 一條一條唸給她聽，對的確認（日程是 `kb agenda edit <編號>` 不帶欄位，印「記成確認了：…」）、
  不對的照她說的改（日程照 note 的 `references/agenda.md`、名字 `kb entity confirm`、設定檔照 note 的 `references/preference.md`）。
- `skeleton:<檔>:…`：她改過那份系統檔。要留她的跑結果裡的 `kb upgrade skeleton <檔> --keep`（新版這個檔又改了才會再問），再 resolve；換回程式的照 `do` 跑
  `kb upgrade skeleton <檔> --replace`（她那份備進回合，說「退回」就回來），再 resolve。
- `redo_by_profile:<卡型>:<日期>`：設定檔那類改過之後，之前做的卡。她說好跑 `do`（`kb cards redo --ids …`），
  再照 ingest 整理（交製卡助手背景做），跟她說在背景做，她說停就停、做好的留著（D-200）。
- `promote:<種類>:<卡型>`：同一種裁示說了很多次，提議升格。見第四節。
- `pending_handedit`：她手改了待處理、日程或裁示。見第五節。
- 做完一件跑那件的 resolve；她說不用管跑 dismiss（條件沒變下週不會再開，變了才會）；她說先不要跑 `kb asks set-aside <key>`（逾期的先放著就不再拿出來，除非她問）。條件消失的（推測確認了、捷徑拿掉了）下次健檢自己關。

## 四、升格怎麼擬

`data.texts` 是她說過的那幾條原話、`card_type` 是卡型、`kind` 是 spoken_wins（以你說的為準）或 ruled_out（別信）。
擬一句用她的字的偏好，例：「報價單的金額以我口頭說的為準」；問她「要改成一條規則嗎？以後的報價單都這樣」（偏好照記）。
她說好：`kb profile add 偏好 偏好=<那句>`（被擋照 note 的 `references/preference.md` 接著做），成功後照 `data.rulings` 逐條
`kb ruling remove <編號>`，最後 resolve。她要改字就照她的字再擬一次。只提議，不自動做（D-150）。

## 五、`handedit` 怎麼處理

- `kb lint handedit --json` 每件：`pending`（這件待處理的編號，`--done` 給它）、`file`（哪份）、`saved`（她那份存在哪）、`checked`（打勾的條目）、`removed`（刪掉的條目）、
  `changed_date`（改了日期的，`to` 是她寫的日期）、`other`（看不出對到哪條的行），條目帶編號。
- 待處理（編號 p-）：打勾的當成處理好了 `kb pending resolve <編號> --how "她在待處理.md 打勾"`；刪掉的當成不用管 `kb pending dismiss`。
- 日程（編號 a-）：打勾或刪掉的照意思 `kb agenda done` 或 `kb agenda cancel`；改日期的 `kb agenda edit <編號> --date <她寫的>`
  （日期不完整就問）。裁示（編號 r-）：刪掉的 `kb ruling remove`。
- `other` 才問她（「你在待處理加了一行『…』，要我記成什麼？」）；做完 `kb lint handedit --done <那件的編號>`，
  回答列「照你改的做了哪些」（`--done` 給的是那件的 `pending`，不是條目的編號）。總目錄、工作日誌被手改只留她那份、照樣重寫，報告講一句，不用處理（D-223）。

## 六、「你直接修」的清單

每件一行：做了什麼、一句結果、怎麼反悔。例：
- 換回系統檔 CLAUDE.md（你改過的版本已經備份，說「退回」就回來）
- 舊的 120 張報價卡交給製卡助手重做，大約 15 分鐘；說「停」就停，做好的留著
- 守門沒在跑：這件要服務人員看，我先記著
會蓋掉她改的（骨架檔、手改的檔）照舊經 kb 寫，換之前她那份會備進回合，清單寫明說「退回」就回來（D-222）。
