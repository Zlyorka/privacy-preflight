---
name: privacy-preflight
version: 1.2.0
description: "Use before pushing a repo public: block personal leaks."
author: Hermes Agent
license: MIT
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [privacy, security, git, publishing, leak-prevention]
---

# Privacy preflight

推送任何 repo 到 GitHub 之前的隱私關卡。工具住在 skill 裡，不裝進專案；專案裡一個檔案都不會多。

需求邊界：這是機器層級的通用工具，不是每個專案貼一份腳本。任何要求把掃描檔放進專案的方案都不符合；一次安裝、所有 repo 生效，設定留在 `~/.hermes/` 與 git config。

```bash
python <skill>/scripts/privacy_scan.py --repo .          # 稽核：工作區、索引、每一顆 commit
bash   <skill>/scripts/install_global_hook.sh            # 一次安裝，之後每個 repo 都受管
bash   <skill>/scripts/verify_gate.sh                    # 端到端證明：洩漏的 push 真的被擋
```

## When to Use

- 要把任何 repo 從 private 改成 public、`gh repo create`、`git remote add`、交給客戶、放上 catalog。
- 專案裡有測試、fixture、文件範例，而它們常常含真名、學校、公司、email 或本機路徑。
- 不確定某個舊 commit 裡有沒有留下不該留的東西。
- 懷疑已經洩漏 → 先讀 `references/leak-recovery.md`，不要先 push 修正。

## 三條不變的規則

1. **工具本身不含任何要檢查的資料。** 姓名、學校、公司、帳號、專案代號一律從 repo **外**的條款清單讀取。會把要查的字寫進自己的檢查器，本身就是洩漏。
2. **憑證掃描器不是隱私檢查。** gitleaks / trufflehog 找的是金鑰，找不到你的名字。兩套都要跑，誰都不能取代誰。
3. **關卡在 push，不在 commit。** rebase、改寫歷史、重加 remote 都會把 commit hook 沒看過的歷史帶回來。

## 條款清單（唯一需要你自己準備的東西）

放在 repo 外，一行一個，`#` 開頭是註解：

```
# ~/.hermes/privacy-terms.txt
<你的真名>
<英文名或帳號>
<學校 / 公司>
<其他專案代號>
<常被寫進 fixture 的舊帳號>
```

解析順序：`--terms-file` → `$PRIVACY_TERMS_FILE` → `~/.hermes/privacy-terms.txt` →
`~/.hermes/prepublish-terms.txt` → `~/.config/privacy-preflight/terms.txt`。也可以用
`--term` 臨時加字，或 `PRIVACY_TERMS="a b"`。

清單是空的，那一輪只證明沒有金鑰、路徑、email、IP，**不證明**沒有名字。工具會印警告，退出碼仍然是 0。

## 它檢查什麼

| 類別 | 內容 |
|---|---|
| credential | AWS / GitHub / OpenAI / Stripe / Slack / Google / Telegram / GitLab / npm token、私鑰、JWT、帶密碼的連線字串、URL 內嵌帳密 |
| machine-path | `<磁碟>:\Users\<帳號>\`、`/home/<帳號>/`、`/Users/<帳號>/`、UNC `\\<主機>` |
| email | 非 `example.com` 之類佔位網域的地址 |
| ip-address | 公網與內網 IPv4 |
| phone | 台灣手機格式 `09xx-xxx-xxx` |
| national-id | 台灣身分證格式（一碼英文 + 1/2 + 8 碼數字） |
| identity-term | 條款清單裡的每個字 |
| sensitive-file | `.env`、`state.db`、`*.db`、`*.sqlite`、`*.har`、`*.pem`、`id_rsa`、`*.pub`、`authorized_keys`、`.bash_history`、`credentials*`、`secrets*`、`mcp.json` 等 |
| commit-identity | 每顆 commit 的 author / committer，加上 annotated tag 的 tagger 與訊息；email 不是佔位值就報 |
| commit-message | 每顆 commit 的訊息本文（最常被拿去寫真名或工單網址） |
| remotes | `.git/config` 裡帶帳密的 remote URL（檔案掃描看不到） |
| untracked-file | 沒被 ignore 的敏感檔（下一次 `git add .` 就會進去） |

掃描範圍：索引（也就是你真正會 commit 的內容）、`--all` 可達的每一個 blob（含已被刪除的檔案）、commit 身分、commit 訊息、`.git/config` 的 remote、untracked 檔。被刪掉的檔案仍會被報出來，因為刪除也是一顆 commit。

文件裡的 `<account>`、`<host>`、`{name}` 這類角括號佔位符不算 finding —— 那是範例，不是機器。

條款清單若被放進被掃的 repo 裡，工具會直接以退出碼 2 拒絕執行 —— 檢查器攜帶著它要查的名字，本身就是洩漏。

## 掃描放在流程的哪裡

五個位置，hook 只佔其中兩個：前面擋「不該進來的檔」，後面擋「已經進來的歷史」。

| 位置 | 掃什麼 | 誰負責 |
|---|---|---|
| 選檔與 `.gitignore` | 檔名 | 你自己；成本近零 |
| `commit` | staged | `pre-commit` hook |
| `push` | 索引 + 全部 commit | `pre-push` hook，真正的門 |
| PR 的 CI | PR 的內容 | 選用；只有它掃得到別人的 fork |
| 改成 public 前 | 完整 + 人工讀 | 你自己跑 `--repo .` |

- **PR 的 CI 那一層是選用的，此使用者選擇不做**：外部貢獻者的 code 由對方負責，按下 merge 就是那一道關卡。除非使用者自己要，不要替他加 `pull_request` workflow，也不要為了「五層才完整」把這層說成必要。
- 那一層唯一的存在理由是「本機 hook 看不到別人的 fork」。使用者不做時，把殘餘風險講清楚（合併之後那顆 commit 就進了他的歷史），而不是默默跳過。
- 執行紀錄不屬於這支工具：CI log 與錯誤訊息裡的資料是「輸出」，掃描器不解析執行輸出，那一層只能靠寫程式的紀律。

## 一次安裝，管住所有 repo

`install_global_hook.sh` 把 `core.hooksPath` 指向 `~/.hermes/privacy-hooks`，寫入兩個 hook：

| hook | 時機 | 掃什麼 |
|---|---|---|
| `pre-commit` | 每次 commit | 只掃 staged，秒級，`--staged` |
| `pre-push` | 每次 push | 全樹 + 全部 commit + 身分 |

- repo **裡**不加任何檔案、不改 `.git/hooks`。
- 已存在的 repo 自己的 hook 會被串接執行（`core.hooksPath` 會讓 git 看不到它們，所以 hook 自己叫回來）。
- 掃描器路徑以絕對路徑記錄；掃描器不見了就**拒絕推送**，不會靜默放行。
- `--force` 才能接管既有的 `core.hooksPath`（husky 之類）；`--uninstall` 還原原值。
- 需要繞過時用 git 自己的 `--no-verify`，工具不另外開後門。

## 每個 repo 的設定，留在 git config 裡

不要為此在專案放設定檔：

```bash
git config privacy.dataDirs cases,work,probe     # 這些目錄永不進 repo
git config --add privacy.allow tests/test_hygiene.py   # 只跳過路徑樣式（偵測器本身）
git config --add privacy.ignore 'tests/fixtures/.*canary'  # 你確認過是假資料的行
```

`--allow` 只跳過 machine-path；憑證、email、IP、條款字在那個檔案裡照樣檢查。`--ignore` 用在假金鑰、示範用的假 email，正則比對 `<位置> <內容>`。

**遮蔽設定只從全域 git config 讀。** `privacy.allow`、`privacy.ignore`、`privacy.dataDirs` 預設取自
`git config --global`；被掃 repo 自己的 `.git/config` 不採信 —— 否則惡意 repo 只要寫一行
`privacy.ignore = .*` 就能讓自己全數過關。真的要以 repo 設定為準時加 `--trust-repo-config`。

**缺條款清單就當場停手。** hook 一律帶 `--require-terms`：沒有清單時退出碼 2，而不是印個警告就說 clean。

**二進位與編碼。** UTF-16（Windows PowerShell 輸出、`.reg`）會先解碼再掃，不再因為 NUL 位元組被當成
二進位跳過。掃描時所有 git 呼叫都帶 `-c core.quotePath=false -c core.fsmonitor=false`：前者讓非 ASCII
檔名在任何 git 版本都原樣輸出，後者避免 `git status` 執行被掃 repo 自己 `.git/config` 指名的程式。

## 讀輸出

```
== credential: 1
  tree: config.py:12  model-provider-key starting sk-a…
== summary
  1 finding(s) — DO NOT PUBLISH
```

退出碼：`0` 乾淨、`1` 有問題、`2` 掃不起來（不是 git repo 等）。CI 或發布腳本要看退出碼，不要 grep 文字。`--report out.json` 出機器可讀報告。

## 證明它會失敗

綠燈只有在看過紅燈之後才有意義。

```bash
python <skill>/scripts/privacy_scan.py --selftest    # 15 個合成 canary，全部是假資料
bash   <skill>/scripts/verify_gate.sh                # 真的 commit 與 push 一次，確認被擋
```

`verify_gate.sh` 會建臨時 bare remote，實際跑 `git commit` 與 `git push`：洩漏的必須被拒，乾淨的必須通過。八步全過才算「這台機器上有關卡」。

## 發布順序

1. `git status` 乾淨、`privacy_scan.py --repo .` 退出碼 0。
2. 先推到 **private** repo，在 GitHub 的檔案清單裡用陌生人的眼睛看一遍路徑。
3. 再改 public。改完用匿名請求確認 `raw.githubusercontent.com` 讀得到（private 會讓 catalog 與安裝流程無聲失敗）。

## 它抓不到什麼

- 照片、截圖、PDF 裡的文字與 EXIF，二進位檔不解析。
- 執行時才組出來的資料（從資料庫、API 讀進來）——那是程式的行為，不是檔案內容。
- 你沒列進條款清單的名字。工具不知道誰是誰。
- 語意：一句話描述你的住址而不含任何可比對的字串。
- **已經在 GitHub 上的東西**：本機掃描器看不到遠端歷史。那是 `references/leak-recovery.md` 的事。

## 維護這個工具本身

- 偵測用的假資料一律在執行時組字串（見 selftest 的 `drive` / `fake_key`），檔案裡不出現任何看起來像真的值。拆字串要拆在**偵測器命中的位置**上：email 拆在 `@` 兩側、電話拆在數字中間、路徑拆在磁碟機代號之後。只把字串斷成前後兩截仍會留下完整的一半 —— 右半邊單獨看就是一筆完整 email，掃描器會抓到自己的測試檔。加完 canary 後拿掃描器掃一次自己的 repo，那是唯一能證明它沒攜帶資料的方法。
- 新增規則時同時加一個 selftest canary，讓它自己證明會紅。
- 這支工具若被放進某個 repo，它就是自己的偵測器：用 `--allow` 標它，但條款清單照樣套用。
- repo 內放一個守門測試（例：`tests/terms_guard.py`）：用**掃描器自己的 matcher** 讀 repo 外的條款清單、逐檔比對、只印條數不印字。它同時證明兩件事 —— 這個 repo 沒有任何一個識別字，而且比對規則沒有第二份實作可以漂移。

## 建置與驗證這類關卡的陷阱

- **全域 `core.hooksPath` 會讓 repo 自己的 hook 隱形。** 安裝的 hook 必須叫回
  `$(git rev-parse --git-common-dir)/hooks/pre-push`，並用 `cmp -s` 判斷那個檔案是不是自己。
  用字串比對路徑會判錯（同一個檔案同時以 `C:/…` 與 `/c/…` 出現），判錯就是自我遞迴或漏跑別人的 hook。
- **Windows/MSYS：native 程式不吃 `/c/…`。** `pwd` 給的是 MSYS 路徑，直接丟給 `git`、`python`
  會 not found 或 cannot change to。寫進指標檔的掃描器路徑、`--repo`、`git -C`、`GIT_CONFIG_GLOBAL`
  一律先 `cygpath -w`，存 native 形式。
- **selftest 要先關掉 hook。** 在臨時 repo 執行 `git config core.hooksPath <不存在的目錄>`；
  否則機器上的全域關卡會擋掉 canary commit，測試就變成在測關卡而不是偵測器
  （症狀：selftest 在 `git commit` 拋 CalledProcessError）。
- **驗證安裝器不要動真的全域設定。** `HOME` 與 `GIT_CONFIG_GLOBAL` 一起指到沙箱，兩者都要 native
  路徑。`git config --global` 讀 `GIT_CONFIG_GLOBAL`，而 python 的 `~` 走 `USERPROFILE`、
  不受 `HOME` 影響 —— 兩者不一致時，你會以為改了、其實掃的是真設定。
- **量退出碼時不要把輸出接管線。** `bash verify.sh | tail -5` 之後的 `$?` 是 `tail` 的；
  寫成 `bash verify.sh > out 2>&1; echo $?`，否則「通過」是自己製造的。
- **第一次掃描的三類誤報分開處理，不要放寬規則。** 偵測器檔案 → `--allow`；測試裡的假金鑰 →
  `--ignore`；同一檔案多個歷史版本造成重複 → 報告端去重。放寬規則等於把關卡拆掉。
- **驗證 push 關卡時，洩漏的 commit 要用 `--no-verify` 造出來。** 讓 pre-commit 先擋下，`git push`
  那一步就因為「沒有東西可推」而假通過，你測到的是 commit 關卡而不是 push 關卡。也別在同一個
  repo 裡測「移掉檔案再推」：歷史仍有那顆 commit，乾淨推送要用一個全新的 repo。
- **歷史掃描是秒級到分鐘級，不要用短 timeout。** 數十顆 commit 約數秒、數百顆更久；跑驗證或計時時
  把 timeout 開到 300 秒以上，兩個實例並行會互相拖到超時。

## 參考

- `references/leak-recovery.md`：已經洩漏之後的處理順序。
- 相關 skill：`pre-public-repo-review`（發布流程與歷史改寫）、`secret-hygiene`（金鑰輪替）。
