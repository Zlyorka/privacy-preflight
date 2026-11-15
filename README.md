# privacy-preflight

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

Publish a repository without leaking yourself.

A read-only audit of a git repository — working tree, index, every reachable commit, commit
identities and messages, annotated tags, and `.git/config` — for the things that must never become
public: credentials, machine paths, real email addresses, phone numbers, national IDs, and the words
from your own terms list (name, school, employer, project codenames).

Two design rules make it different from a secret scanner:

1. **The tool carries none of the data it checks.** Identifiers come from a terms file outside
   every repository, or from `PRIVACY_TERMS`. A checker that holds the names it looks for is
   itself the leak, so the scanner refuses to run when the terms file sits inside the audited repo.
2. **The gate sits on push, not commit.** A rebase, a rewritten branch or a re-added remote brings
   back history the commit hook never saw. `pre-push` scans every commit; `pre-commit` scans only
   what is staged, for speed.

## Install

```bash
# 1. the terms list — the one thing you supply, kept outside every repo
cat > ~/.hermes/privacy-terms.txt <<'EOF'
# one identifier per line; # starts a comment
<your real name>
<your handle or account name>
<school or employer>
<project codename>
EOF

# 2. one machine-wide gate, nothing added to any project
bash scripts/install_global_hook.sh

# 3. prove it can fail, end to end, through real git commands
bash scripts/verify_gate.sh
```

`install_global_hook.sh` sets `core.hooksPath` to `~/.hermes/privacy-hooks` and points the hooks at
this directory's scanner by absolute path. Existing per-repository hooks are chained, not shadowed.
Use `--force` to take over a `core.hooksPath` that is already set, and `--uninstall` to restore it.

## Use

```bash
python scripts/privacy_scan.py --repo .                     # audit
python scripts/privacy_scan.py --repo . --report out.json    # machine-readable report
python scripts/privacy_scan.py --repo . --require-terms      # refuse to run without a terms list
python scripts/privacy_scan.py --selftest                    # 21 synthetic canaries
```

Exit codes: `0` clean, `1` findings, `2` the scan could not run.

## Settings live in git config, never in the project

```bash
git config --global --add privacy.dataDirs cases,work,probe   # directories that must never ship
git config --global --add privacy.allow tests/test_hygiene.py # a detector file: skip path checks only
git config --global --add privacy.ignore 'tests/fixtures/.*canary' # a finding you read and confirmed
```

Nothing is written into the project, so the project never gains a config file to publish.

These three keys are read from the **global** config only. The scanned repository's own
`.git/config` is not trusted: a hostile repository could otherwise ship `privacy.ignore = .*` and
report itself clean. Pass `--trust-repo-config` when you really want one repository to override.

## What it checks

| Category | Contents |
|---|---|
| credential | AWS, GitHub, OpenAI, Stripe, Slack, Google, Telegram, GitLab, npm tokens; private keys; JWTs; connection strings and URLs carrying a password |
| sensitive-file | `.env`, `state.db`, `*.db`, `*.sqlite`, `*.har`, `*.pem`, `id_rsa`, `*.pub`, `authorized_keys`, shell history, `credentials*`, `secrets*`, `mcp.json` |
| machine-path | `<drive>:\Users\<account>\`, `/home/<account>/`, `/Users/<account>/`, UNC `<host>` |
| email | any address that is not an obvious placeholder (RFC 2606 domains, noreply, git@) |
| ip-address | public and LAN IPv4 |
| phone | `09xx-xxx-xxx` |
| national-id | one letter + 1/2 + eight digits |
| identity-term | every word in your terms list |
| commit-identity | author and committer of every commit, plus annotated tag taggers and tag messages |
| commit-message | the message body of every commit |
| remotes | a remote URL in `.git/config` that carries a password or token |
| untracked-file | sensitive files that are not ignored and would be added by `git add .` |

Documented placeholders such as `<account>`, `<host>` or `{name}` are not reported: an example is
not a machine. Deleted files are still reported — deleting a file is another commit, and the old
blob stays reachable. UTF-16 text (PowerShell output, `.reg` files) is decoded before the scan
instead of being skipped as binary.

## Fails closed, on purpose

| Situation | Behaviour |
|---|---|
| The terms file is inside the audited repository | exit 2 — the checker would carry the names it checks |
| The hooks run and no terms list exists | exit 2 — a missing list cannot silently downgrade the gate |
| `--max-objects` stops the history scan | exit 2 — a partial scan may not report `clean` |
| An `--ignore` pattern is not a valid regex | exit 2 instead of a traceback |
| The scanner file has been moved or deleted | the hook refuses to commit and to push |

Every git call is made with `-c core.quotePath=false -c core.fsmonitor=false`: the first keeps
non-ASCII paths raw on any git build, the second stops `git status` from running a command named in
the scanned repository's own `.git/config`.

## What it cannot catch

- Text and EXIF inside images, PDFs and other binaries — they are not parsed.
- Data assembled at runtime from a database or an API.
- A name that is not in your terms list. The tool does not know who you are.
- Meaning: a sentence that describes you without containing a comparable literal.
- Anything already on the remote. Local scanning cannot see the server's history; see
  `references/leak-recovery.md`.

## Evidence, not assurances

`--selftest` builds synthetic canaries for every category and fails if one is missed:

```bash
python scripts/privacy_scan.py --selftest    # selftest: 21/21 passed
```

`verify_gate.sh` creates a temporary repository, commits a fake credential, and asserts that the
commit is blocked **because the scanner reported it** — a hook that crashes does not count as a gate:

```bash
bash scripts/verify_gate.sh                  # 8 passed, 0 failed
```

`tests/canary_battery.py` plants one canary per category in a temporary export of this repository,
requires the scanner to report each one, then removes it again. It never touches your index:

```bash
python tests/canary_battery.py               # battery: 11/11
```

`tests/terms_guard.py` proves the other half — that this repository carries none of your own
identifiers. It reads the terms file from outside the repo, compares every tracked file with the
scanner's own matcher, and prints counts only:

```bash
python tests/terms_guard.py                  # 14 term(s) checked, 0 offending
```

`tests/docs_sync.py` keeps the three language versions honest: every README must state the same
version, the same twelve category names and the same commands.

```bash
python tests/docs_sync.py                    # docs sync: 3 file(s) consistent
```

A green scan means nothing until the scan has been seen to fail.

## License

MIT. See [LICENSE](LICENSE).
