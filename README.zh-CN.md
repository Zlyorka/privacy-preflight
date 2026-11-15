# privacy-preflight

[English](README.md) · [繁體中文](README.zh-TW.md) · **中文**

把仓库公开出去，不会顺手把自己也公开。

只读审计一个 git 仓库 —— 工作区、索引、每一颗可达的 commit、commit 的身份与信息、annotated tag，
以及 `.git/config` —— 找出绝不该公开的东西：凭证、机器路径、真实邮箱、电话、身份证号，
还有你自己那张条款清单上的词（姓名、学校、公司、项目代号）。

两条设计规则让它和金标扫描器不一样：

1. **工具本身不含它要检查的数据。** 识别词一律来自仓库之外的条款清单，或 `PRIVACY_TERMS`。
   把要查的名字写进自己的检查器，本身就是泄露 —— 所以清单若落在被审计的仓库里，工具直接拒绝运行。
2. **关卡在 push，不在 commit。** rebase、改写分支、重新添加 remote 都会把 commit hook 没看过的历史带回来。
   `pre-push` 扫每一颗 commit；`pre-commit` 只扫已暂存的内容，为了速度。

## 安装

```bash
# 1. 条款清单 —— 唯一要你自己准备的东西，放在任何仓库之外
cat > ~/.hermes/privacy-terms.txt <<'EOF'
# 一行一个，井号开头是注释
<你的真名>
<你的账号或英文名>
<学校或公司>
<项目代号>
EOF

# 2. 装一次机器级关卡，项目里一个文件都不多
bash scripts/install_global_hook.sh

# 3. 用真的 git 命令证明它会拦
bash scripts/verify_gate.sh
```

`install_global_hook.sh` 把 `core.hooksPath` 指到 `~/.hermes/privacy-hooks`，并以绝对路径指向
这个目录的扫描器。各仓库原本的 hook 会被串联执行，不会被覆盖。已有的 `core.hooksPath` 要用
`--force` 才能接管；`--uninstall` 还原原值。

## 使用

```bash
python scripts/privacy_scan.py --repo .                     # 审计
python scripts/privacy_scan.py --repo . --report out.json    # 机器可读报告
python scripts/privacy_scan.py --repo . --require-terms      # 没有条款清单就拒绝运行
python scripts/privacy_scan.py --selftest                    # 21 个合成 canary
```

退出码：`0` 干净、`1` 有 finding、`2` 扫不起来。

## 设置留在 git config，不进项目

```bash
git config --global --add privacy.dataDirs cases,work,probe   # 永不进仓库的目录
git config --global --add privacy.allow tests/test_hygiene.py # 检测器本身：只跳过路径检查
git config --global --add privacy.ignore 'tests/fixtures/.*canary' # 你确认过是假数据的行
```

项目里不写任何文件，所以项目不会多出一个要公开的配置文件。

这三个键只从**全局**设置读取。被扫仓库自己的 `.git/config` 不采信：否则恶意仓库只要写一行
`privacy.ignore = .*` 就能让自己回报干净。真的要某个仓库覆写，加 `--trust-repo-config`。

## 它检查什么

| 类别 | 内容 |
|---|---|
| credential | AWS、GitHub、OpenAI、Stripe、Slack、Google、Telegram、GitLab、npm 的 token；私钥；JWT；带密码的连接串与 URL |
| sensitive-file | `.env`、`state.db`、`*.db`、`*.sqlite`、`*.har`、`*.pem`、`id_rsa`、`*.pub`、`authorized_keys`、shell 历史、`credentials*`、`secrets*`、`mcp.json` |
| machine-path | `<磁盘>:\Users\<账号>\`、`/home/<账号>/`、`/Users/<账号>/`、UNC `<主机>` |
| email | 不是明显占位值的地址（RFC 2606 域名、noreply、git@） |
| ip-address | 公网与内网 IPv4 |
| phone | `09xx-xxx-xxx` |
| national-id | 一码英文 + 1/2 + 八码数字 |
| identity-term | 你条款清单里的每一个词 |
| commit-identity | 每颗 commit 的 author 与 committer，加上 annotated tag 的 tagger 与 tag 信息 |
| commit-message | 每颗 commit 的信息正文 |
| remotes | `.git/config` 里带密码或 token 的 remote URL |
| untracked-file | 没被 ignore、下一次 `git add .` 就会进去的敏感文件 |

文档里的 `<account>`、`<host>`、`{name}` 这类占位符不算 finding —— 示例不是机器。被删掉的文件
仍会被报出来，因为删除也是一颗 commit，旧 blob 依然可达。UTF-16 的文本（PowerShell 输出、`.reg`）
会先解码再扫，不会因为 NUL 字节被当成二进制跳过。

## 刻意失败得安全

| 情况 | 行为 |
|---|---|
| 条款清单放在被审计的仓库里 | 退出码 2 —— 检查器会携带它要查的名字 |
| hook 运行时没有条款清单 | 退出码 2 —— 缺清单不能默默降低关卡等级 |
| `--max-objects` 让历史扫描中断 | 退出码 2 —— 部分扫描不可以回报 `clean` |
| `--ignore` 不是合法正则 | 退出码 2，而不是 traceback |
| 扫描器文件被移走或删除 | hook 拒绝 commit 与 push |

每一次 git 调用都带 `-c core.quotePath=false -c core.fsmonitor=false`：前者让非 ASCII 文件名在任何
git 版本都原样输出，后者避免 `git status` 去执行被扫仓库自己 `.git/config` 指名的程序。

## 它抓不到什么

- 图片、PDF 与其他二进制文件里的文本与 EXIF —— 不解析。
- 运行时才组装出来的数据（从数据库、API 读进来）。
- 你没列进条款清单的名字。工具不知道谁是谁。
- 语义：一句话描述你，却不含任何可比对的字符串。
- 已经在远端上的东西。本机扫描看不到服务器的历史；见 `references/leak-recovery.md`。

## 证据，不是保证

`--selftest` 为每个类别造一个合成 canary，漏抓任何一个就失败：

```bash
python scripts/privacy_scan.py --selftest    # selftest: 21/21 passed
```

`verify_gate.sh` 建一个临时仓库、commit 一个假凭证，并要求 commit 是**因为扫描器报出来**才被拦 ——
hook 自己崩掉不算关卡：

```bash
bash scripts/verify_gate.sh                  # 8 passed, 0 failed
```

`tests/canary_battery.py` 在这个仓库的临时导出副本里，每类种一个 canary，要求扫描器逐一抓到，
再移除。它不会动到你的索引：

```bash
python tests/canary_battery.py               # battery: 11/11
```

`tests/terms_guard.py` 证明另一半 —— 这个仓库不含你任何一个识别词。它从仓库外读条款清单，
用扫描器自己的 matcher 比对每一个被跟踪的文件，只印条数不印字：

```bash
python tests/terms_guard.py                  # 14 term(s) checked, 0 offending
```

`tests/docs_sync.py` 让三个语言版本不会走样：每一份 README 必须写同样的版本、同样十二个类别名、
同样的指令。

```bash
python tests/docs_sync.py                    # docs sync: 3 file(s) consistent
```

没看过红灯的绿灯，不算通过。

## 授权

MIT，见 [LICENSE](LICENSE)。
