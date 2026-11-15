# privacy-preflight

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

把 repo 公開出去，不會順手把自己也公開。

唯讀稽核一個 git repo —— 工作區、索引、每一顆可達的 commit、commit 的身分與訊息、annotated tag，
以及 `.git/config` —— 找出絕不該公開的東西：憑證、機器路徑、真實 email、電話、身分證號，
還有你自己那張條款清單上的字（姓名、學校、公司、專案代號）。

兩條設計規則讓它跟金鑰掃描器不一樣：

1. **工具本身不含它要檢查的資料。** 識別字一律來自 repo 之外的條款清單，或 `PRIVACY_TERMS`。
   把要查的名字寫進自己的檢查器，本身就是洩漏 —— 所以清單若落在被稽核的 repo 裡，工具直接拒絕執行。
2. **關卡在 push，不在 commit。** rebase、改寫分支、重加 remote 都會把 commit hook 沒看過的歷史帶回來。
   `pre-push` 掃每一顆 commit；`pre-commit` 只掃 staged 的內容，為了速度。

## 安裝

```bash
# 1. 條款清單 —— 唯一要你自己準備的東西，放在任何 repo 之外
cat > ~/.hermes/privacy-terms.txt <<'EOF'
# 一行一個，井號開頭是註解
<你的真名>
<你的帳號或英文名>
<學校或公司>
<專案代號>
EOF

# 2. 裝一次機器層級的關卡，專案裡一個檔案都不多
bash scripts/install_global_hook.sh

# 3. 用真的 git 指令證明它會擋
bash scripts/verify_gate.sh
```

`install_global_hook.sh` 把 `core.hooksPath` 指到 `~/.hermes/privacy-hooks`，並以絕對路徑指向
這個目錄的掃描器。各 repo 原本的 hook 會被串接執行，不會被蓋掉。既有的 `core.hooksPath` 要用
`--force` 才能接管；`--uninstall` 還原原值。

## 使用

```bash
python scripts/privacy_scan.py --repo .                     # 稽核
python scripts/privacy_scan.py --repo . --report out.json    # 機器可讀報告
python scripts/privacy_scan.py --repo . --require-terms      # 沒有條款清單就拒絕執行
python scripts/privacy_scan.py --selftest                    # 21 個合成 canary
```

退出碼：`0` 乾淨、`1` 有 finding、`2` 掃不起來。

## 設定留在 git config，不進專案

```bash
git config --global --add privacy.dataDirs cases,work,probe   # 永不進 repo 的目錄
git config --global --add privacy.allow tests/test_hygiene.py # 偵測器本身：只跳過路徑檢查
git config --global --add privacy.ignore 'tests/fixtures/.*canary' # 你確認過是假資料的行
```

專案裡不寫任何檔案，所以專案不會多出一個要公開的設定檔。

這三個鍵只從**全域**設定讀。被掃 repo 自己的 `.git/config` 不採信：否則惡意 repo 只要寫一行
`privacy.ignore = .*` 就能讓自己回報乾淨。真的要讓某個 repo 覆寫，加 `--trust-repo-config`。

## 它檢查什麼

| 類別 | 內容 |
|---|---|
| credential | AWS、GitHub、OpenAI、Stripe、Slack、Google、Telegram、GitLab、npm 的 token；私鑰；JWT；帶密碼的連線字串與 URL |
| sensitive-file | `.env`、`state.db`、`*.db`、`*.sqlite`、`*.har`、`*.pem`、`id_rsa`、`*.pub`、`authorized_keys`、shell 歷史、`credentials*`、`secrets*`、`mcp.json` |
| machine-path | `<磁碟>:\Users\<帳號>\`、`/home/<帳號>/`、`/Users/<帳號>/`、UNC `<主機>` |
| email | 不是明顯佔位值的位址（RFC 2606 網域、noreply、git@） |
| ip-address | 公網與內網 IPv4 |
| phone | `09xx-xxx-xxx` |
| national-id | 一碼英文 + 1/2 + 八碼數字 |
| identity-term | 你條款清單裡的每一個字 |
| commit-identity | 每顆 commit 的 author 與 committer，加上 annotated tag 的 tagger 與 tag 訊息 |
| commit-message | 每顆 commit 的訊息本文 |
| remotes | `.git/config` 裡帶密碼或 token 的 remote URL |
| untracked-file | 沒被 ignore、下一次 `git add .` 就會進去的敏感檔 |

文件裡的 `<account>`、`<host>`、`{name}` 這類佔位符不算 finding —— 範例不是機器。被刪掉的檔案
仍會被報出來，因為刪除也是一顆 commit，舊 blob 依然可達。UTF-16 的文字（PowerShell 輸出、`.reg`）
會先解碼再掃，不會因為 NUL 位元組被當成二進位跳過。

## 刻意失敗得安全

| 情況 | 行為 |
|---|---|
| 條款清單放在被稽核的 repo 裡 | 退出碼 2 —— 檢查器會攜帶它要查的名字 |
| hook 執行時沒有條款清單 | 退出碼 2 —— 缺清單不能默默降低關卡等級 |
| `--max-objects` 讓歷史掃描中斷 | 退出碼 2 —— 部分掃描不可以回報 `clean` |
| `--ignore` 不是合法正則 | 退出碼 2，而不是 traceback |
| 掃描器檔案被移走或刪掉 | hook 拒絕 commit 與 push |

每一次 git 呼叫都帶 `-c core.quotePath=false -c core.fsmonitor=false`：前者讓非 ASCII 檔名在任何
git 版本都原樣輸出，後者避免 `git status` 去執行被掃 repo 自己 `.git/config` 指名的程式。

## 它抓不到什麼

- 圖片、PDF 與其他二進位檔裡的文字與 EXIF —— 不解析。
- 執行時才組出來的資料（從資料庫、API 讀進來）。
- 你沒列進條款清單的名字。工具不知道誰是誰。
- 語意：一句話描述你，卻不含任何可比對的字串。
- 已經在遠端上的東西。本機掃描看不到伺服器的歷史；見 `references/leak-recovery.md`。

## 證據，不是保證

`--selftest` 為每個類別造一個合成 canary，漏抓任何一個就失敗：

```bash
python scripts/privacy_scan.py --selftest    # selftest: 21/21 passed
```

`verify_gate.sh` 建一個臨時 repo、commit 一個假憑證，並要求 commit 是**因為掃描器報出來**才被擋 ——
hook 自己崩掉不算關卡：

```bash
bash scripts/verify_gate.sh                  # 8 passed, 0 failed
```

`tests/canary_battery.py` 在這個 repo 的臨時匯出副本裡，每類種一個 canary，要求掃描器逐一抓到，
再移除。它不會動到你的索引：

```bash
python tests/canary_battery.py               # battery: 11/11
```

`tests/terms_guard.py` 證明另一半 —— 這個 repo 不含你任何一個識別字。它從 repo 外讀條款清單，
用掃描器自己的 matcher 比對每一個被追蹤的檔案，只印條數不印字：

```bash
python tests/terms_guard.py                  # 14 term(s) checked, 0 offending
```

`tests/docs_sync.py` 讓三個語言版本不會走鐘：每一份 README 必須寫同樣的版本、同樣十二個類別名、
同樣的指令。

```bash
python tests/docs_sync.py                    # docs sync: 3 file(s) consistent
```

沒看過紅燈的綠燈，不算通過。

## 授權

MIT，見 [LICENSE](LICENSE)。
