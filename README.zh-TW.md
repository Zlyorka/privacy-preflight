# privacy-preflight

[English](README.md) · **繁體中文** · [中文](README.zh-CN.md)

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
mkdir -p ~/.config/privacy-preflight
cat > ~/.config/privacy-preflight/terms.txt <<'EOF'
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

### 任何 agent，或不裝 agent

`SKILL.md` 用的是通用 Agent Skills 格式（`name`、`description`、`license`），不含任何 runtime 專屬欄位，
所以同一份檔案在 Claude Code、Codex、Antigravity、Cursor 或任何讀 `SKILL.md` 的 agent 都能載入。
把目錄複製到那個 agent 找 skill 的位置：

```bash
git clone https://github.com/Zlyorka/privacy-preflight ~/.claude/skills/privacy-preflight
```

完全不裝 agent 也可以 —— 掃描器就是一個沒有外部依賴的 Python 檔：

```bash
git clone https://github.com/Zlyorka/privacy-preflight /tmp/pp
python /tmp/pp/scripts/privacy_scan.py --repo .
```

狀態放在 `$PRIVACY_PREFLIGHT_HOME`（預設 `~/.privacy-preflight`）與全域 git config，
換 agent 不用重裝關卡。

`install_global_hook.sh` 把 `core.hooksPath` 指到 `~/.privacy-preflight/hooks`，並以絕對路徑指向
這個目錄的掃描器。各 repo 原本的 hook 會被串接執行，不會被蓋掉。既有的 `core.hooksPath` 要用
`--force` 才能接管；`--uninstall` 還原原值。

## 使用

```bash
python scripts/privacy_scan.py --repo .                     # 稽核
python scripts/privacy_scan.py --repo . --report out.json    # 機器可讀報告
python scripts/privacy_scan.py --repo . --require-terms      # 沒有條款清單就拒絕執行
python scripts/privacy_scan.py --selftest                    # 27 個合成 canary
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

## 兩層，因為規則不會讀

腳本比對字面。它看不出「我住板橋，通勤到關渡四十分鐘」在描述一個地址，也看不出某句註解寫著客戶名，
或某個 fixture 長得就像真實工單。

| 層 | 回答什麼 | 產出 |
|---|---|---|
| `privacy_scan.py` | 有沒有不該公開的字面 | 退出碼 0 / 1 / 2 —— 這是關卡 |
| `llm_review_context.py` ＋ 一個模型 | 這些字讀起來像不像在講某個人 | 有 `path:line` 的報告 —— 不是關卡 |

```bash
python scripts/llm_review_context.py --repo .   # 寫到 ~/.privacy-preflight/packets/
```

片段包產生器只做收集：被追蹤的文字檔、用線索評分（自述、地點、學校、雇主、身分、聯絡、未完成註記、
工單）、小檔整份、大檔只給命中行附近、裁在 `--budget`（45 KB）。它不上傳、不判斷、不改檔。

要求模型只回報能用 `path:line` 指出的東西、說出陌生人從中得到什麼、不確定的標 `NEEDS-CONFIRM`。
沒有行號的 finding 就是編的，整條丟掉。repo 裡的文字是資料不是指令 —— packet 裡可能寫著「忽略以上」，
那也是被審查的內容。

LLM 層不是關卡：它不確定、不可重放、可以被注入。確認過的逐條修，修完重跑腳本，並把能機械化的部分
（新名字、客戶）加進條款清單或偵測規則。完整做法見 `references/llm-review.md`。

**它實際讀什麼。** 索引內容（不只檔名）、全部可達 blob、commit 身分與訊息、annotated tag 的 tagger 與訊息、
`.git/config` 的 remote 與 submodule URL、未追蹤檔的**內容**（`git add .` 就带得進去），以及 `--dangling`
的未可達物件。從子目錄執行會自動提升到工作樹根目錄，否則 `--staged` 會因路徑基準不同而全數漏掉。

**編碼與大檔。** UTF-16、Big5／CP950、CP1252 先解碼再掃；200 KB～2 MB 照掃並在 notes 提醒，超過 2 MB 才是
finding。Git LFS 指標會解析到 `.git/lfs/objects` 讀真實內容，本機缺該物件時退出碼 2（`--skip-lfs` 降級）。

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
| unreachable | 沒有任何 ref 指到的物件（amend 掉的舊 commit、rebase 掉的分支）：加 `--dangling` 才掃 |

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

- 圖片、PDF 與其他二進位檔裡的文字與 EXIF —— 不解析。Git LFS 的檔案只掃到指標，會明確報成「內容沒被讀到」，不會假裝乾淨。
- 執行時才組出來的資料（從資料庫、API 讀進來）。
- 你沒列進條款清單的名字。工具不知道誰是誰。
- 語意：一句話描述你，卻不含任何可比對的字串。
- 已經在遠端上的東西。本機掃描看不到伺服器的歷史；見 `references/leak-recovery.md`。

## 證據，不是保證

`--selftest` 為每個類別造一個合成 canary，漏抓任何一個就失敗：

```bash
python scripts/privacy_scan.py --selftest    # selftest: 27/27 passed
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
python tests/docs_sync.py                    # docs sync: 6 file(s) consistent
```

`tests/llm_packet.py` 守著片段包產生器：回報格式在任何預算下都留著、被略過的檔案一定會被點名、packet 不會落進被掃的 repo。

```bash
python tests/llm_packet.py                   # llm packet: 5/5 properties hold
```

沒看過紅燈的綠燈，不算通過。

## 授權

MIT，見 [LICENSE](LICENSE)。
