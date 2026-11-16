# 已經洩漏了怎麼救

順序錯了會讓事情更糟。下面的每一步都假設「已經有人可能看到」。

## 1. 先停手

不要 push 任何「修正」。每次 push 都把字串複製進另一顆 commit，而且本機掃描看不到遠端歷史。

## 2. 分清是哪一類

| 洩漏的東西 | 第一件事 |
|---|---|
| 金鑰、token、私鑰 | 立刻**輪替**，改歷史排後面 |
| 姓名、學校、公司、路徑 | 記下哪些字串、哪些 commit |
| 個資（客戶、病歷、對話） | 當成事故處理：通知、法規義務、留存證據 |
| 資料庫檔、`.env` | 假設裡面每一個值都洩漏，全部輪替 |

## 3. 輪替金鑰的順序

從洩漏的那把往外：

1. 洩漏的金鑰本身先失效。
2. 它**能讀**的東西 → 稽核近期存取，再輪替。
3. 它**能寫**的東西 → 檢查有沒有未授權寫入，再輪替。
4. 同一個檔案裡的其他值（同一個 `.env` 就當全部洩漏）。
5. 依賴它的下游 webhook 與服務。

每次輪替記下：換了什麼、何時、誰換、通知了誰。

## 4. 改寫歷史

```bash
git clone --mirror <repo> repo-backup.git     # 先備份
git filter-repo --invert-paths --path .env     # 或 --replace-text rules.txt
```

改寫之後：重打所有 tag、重下 pin、`git reflog expire --expire=now --all && git gc --prune=now`、force-push 分支與 tag。

改寫只清掉可達物件，**沒有清掉遠端的舊物件**。

## 5. 刪掉 repo 重建

GitHub 對 force-push 之後失效的 commit，仍可以用 SHA 讀到（`api.github.com/repos/<owner>/<repo>/commits/<sha>` 還回 200）。只有刪除 repo 才真的消失。

刪除需要 `delete_repo` 權限；沒有就把 Settings → Danger Zone 的操作交給有權限的人，不要說「已經清乾淨」。

## 6. 清掉你自己做的副本

調查過程會產生第二批同樣的位元組：

- `git bundle` 備份、`--mirror` clone
- 另一個 worktree、舊的 clone
- 同步過的 plugin 資料夾、雲端備份
- `*.bak`、匯出的 JSON 報告（`--report`）

「清乾淨」的定義是這些全部檢查過，不是某一個 repo 看起來沒問題。

## 7. 驗證，而不是宣稱

```bash
python <skill>/scripts/privacy_scan.py --repo . --terms-file ~/.config/privacy-preflight/terms.txt
python <skill>/scripts/privacy_scan.py --selftest
```

一次改寫常常只處理了一個字串，其他還在：同一個檔案通常還帶著專案代號或學校名。改寫後**帶著條款清單**再掃，直到乾淨，然後把 tag 與 pin 重新做一次。

回報時說清楚：哪些字串被公開了、曝光多久、有沒有金鑰、輪替了什麼。名字與識別資訊無法「收回」。
