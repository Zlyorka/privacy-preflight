# privacy-preflight

Publish a repository without leaking yourself.

A read-only audit of a git repository — working tree, index, every reachable commit, commit
identities and messages, and `.git/config` — for the things that must never become public:
credentials, machine paths, real email addresses, phone numbers, national IDs, and the words from
your own terms list (name, school, employer, project codenames).

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
python scripts/privacy_scan.py --repo .                    # audit
python scripts/privacy_scan.py --repo . --report out.json  # machine-readable report
python scripts/privacy_scan.py --selftest                  # 15 synthetic canaries
```

Exit codes: `0` clean, `1` findings, `2` the scan could not run.

## Per-repository settings stay in git config

```bash
git config privacy.dataDirs cases,work,probe              # directories that must never ship
git config --add privacy.allow tests/test_hygiene.py      # a detector file: skip path checks only
git config --add privacy.ignore 'tests/fixtures/.*canary' # a finding you read and confirmed
```

Nothing is written into the project, so the project never gains a config file to publish.

## What it checks

| Category | Contents |
|---|---|
| credential | AWS, GitHub, OpenAI, Stripe, Slack, Google, Telegram, GitLab, npm tokens; private keys; JWTs; connection strings and URLs carrying a password |
| sensitive-file | `.env`, `state.db`, `*.pem`, `id_rsa`, `credentials*`, `secrets*`, `mcp.json` |
| machine-path | `<drive>:\Users\<account>\`, `/home/<account>/`, `/Users/<account>/`, UNC `<host>` |
| email | any address that is not an obvious placeholder (RFC 2606 domains, noreply, git@) |
| ip-address | public and LAN IPv4 |
| phone | `09xx-xxx-xxx` |
| national-id | one letter + 1/2 + eight digits |
| identity-term | every word in your terms list |
| commit-identity | author and committer of every commit |
| commit-message | the message body of every commit |
| remotes | a remote URL in `.git/config` that carries a password or token |
| untracked-file | sensitive files that are not ignored and would be added by `git add .` |

Documented placeholders such as `<account>`, `<host>` or `{name}` are not reported: an example
is not a machine. Deleted files are still reported — deleting a file is another commit, and the
old blob stays reachable.

## What it cannot catch

- Text and EXIF inside images, PDFs and other binaries — they are not parsed.
- Data assembled at runtime from a database or an API.
- A name that is not in your terms list. The tool does not know who you are.
- Meaning: a sentence that describes you without containing a comparable literal.
- Anything already on the remote. Local scanning cannot see the server's history; see
  `references/leak-recovery.md`.

## Evidence, not assurances

`--selftest` builds synthetic canaries for every category and fails if one is missed.
`verify_gate.sh` creates a temporary repository, commits a fake credential, and asserts that both
the commit and the push are blocked, then that a clean repository pushes normally:

```
8 passed, 0 failed
```

`tests/canary_battery.py` goes further: it plants one canary per category **inside this repository**,
requires the scanner to report each one, then removes it again:

```bash
python tests/canary_battery.py     # battery: 11/11
```

`tests/terms_guard.py` proves the other half — that this repository carries none of your own
identifiers. It reads the terms file from outside the repo, compares every tracked file with the
scanner's own matcher, and prints counts only:

```bash
python tests/terms_guard.py        # 14 term(s) checked against 9 tracked file(s), 0 offending
```

A green scan means nothing until the scan has been seen to fail.

## License

MIT. See [LICENSE](LICENSE).
