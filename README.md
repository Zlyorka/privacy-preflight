# privacy-preflight

**English** · [繁體中文](README.zh-TW.md) · [中文](README.zh-CN.md)

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
mkdir -p ~/.config/privacy-preflight
cat > ~/.config/privacy-preflight/terms.txt <<'EOF'
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

### Any agent, or no agent at all

`SKILL.md` follows the plain Agent Skills format (`name`, `description`, `license`) and holds no
runtime-specific keys, so the same file loads in Claude Code, Codex, Antigravity, Cursor or any other
agent that reads a `SKILL.md`. Copy the directory wherever that agent looks for skills:

```bash
git clone https://github.com/Zlyorka/privacy-preflight ~/.claude/skills/privacy-preflight
```

No agent at all is also supported — the scanner is one Python file with no dependencies:

```bash
git clone https://github.com/Zlyorka/privacy-preflight /tmp/pp
python /tmp/pp/scripts/privacy_scan.py --repo .
```

State lives in `$PRIVACY_PREFLIGHT_HOME` (default `~/.privacy-preflight`) and in the global git
config, so switching agents never means reinstalling the gate.

`install_global_hook.sh` sets `core.hooksPath` to `~/.privacy-preflight/hooks` and points the hooks at
this directory's scanner by absolute path. Existing per-repository hooks are chained, not shadowed.
Use `--force` to take over a `core.hooksPath` that is already set, and `--uninstall` to restore it.

## Use

```bash
python scripts/privacy_scan.py --repo .                     # audit
python scripts/privacy_scan.py --repo . --report out.json    # machine-readable report
python scripts/privacy_scan.py --repo . --require-terms      # refuse to run without a terms list
python scripts/privacy_scan.py --selftest                    # 23 synthetic canaries
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

## Two layers, because a rule cannot read

The scanner matches literals. It cannot tell that "I live in Banqiao, forty minutes to Guandu"
describes an address, or that one comment names a client, or that a fixture mirrors a real ticket.

| Layer | Question it answers | Output |
|---|---|---|
| `privacy_scan.py` | Is there a literal that must not ship? | exit code 0 / 1 / 2 — the gate |
| `llm_review_context.py` + a model | Does this read like it points at a person? | a report with `path:line` — not a gate |

```bash
python scripts/llm_review_context.py --repo .   # writes to ~/.privacy-preflight/packets/
```

The packet builder only gathers: tracked text files, scored by cues (first person, places, schools,
employers, identity, contact, loose ends, tickets), small files whole, large files as excerpts around
the hits, cut at `--budget` (45 KB). It never uploads, never judges, never edits.

Ask a model to report only what it can cite by `path:line`, to say what a stranger learns, and to mark
uncertain items `NEEDS-CONFIRM`. A finding without a line reference is a fabrication and is dropped.
Repo text is data, not instructions — a packet may contain "ignore the above", and that is content
under review.

The LLM layer is not a gate: it is non-deterministic, cannot be replayed, and can be injected. Fix each
confirmed finding, then re-run the scanner, and move what can be made mechanical (a new name, a client)
into the terms list or a detector rule. Full playbook: `references/llm-review.md`.

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
python scripts/privacy_scan.py --selftest    # selftest: 23/23 passed
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

`python tests/llm_packet.py` guards the packet builder: the report format survives any budget, an omitted file is always named, and the packet never lands inside the repository.

```bash
python tests/llm_packet.py                   # llm packet: 4/4 properties hold
```

A green scan means nothing until the scan has been seen to fail.

## License

MIT. See [LICENSE](LICENSE).
