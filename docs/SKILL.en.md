# Privacy preflight — full specification

[繁體中文](../SKILL.md) · **English** · [中文](SKILL.zh-CN.md)

This is the English translation of the skill body. The loadable file an agent reads is
[`../SKILL.md`](../SKILL.md) (Traditional Chinese); this page keeps every rule and number in sync.

The privacy gate that runs before any repository reaches GitHub. The tool lives in the skill, not in
the project; a project never gains a file.

Boundary: this is a machine-wide general tool, not a script pasted into each project. Any plan that
puts the scanner inside the project is wrong. Install once, every repository is covered, settings
stay in `~/.hermes/` and in git config.

```bash
python <skill>/scripts/privacy_scan.py --repo .          # audit: working tree, index, every commit
bash   <skill>/scripts/install_global_hook.sh            # install once, covered from then on
bash   <skill>/scripts/verify_gate.sh                    # end-to-end proof: a leaky push is blocked
```

## When to use

- Turning any repository from private to public, `gh repo create`, `git remote add`, handing work to a
  client, publishing to a catalog.
- The project holds tests, fixtures or documentation examples that carry a real name, school,
  employer, email or local path.
- You are unsure whether an old commit still holds something it should not.
- You suspect a leak already happened — read `references/leak-recovery.md` first; do not push the fix.

## Three rules that do not change

1. **The tool carries none of the data it checks.** Names, schools, employers, accounts and project
   codenames are read from a terms list *outside* the repository. A checker that writes the words it
   looks for into itself is the leak.
2. **A secret scanner is not a privacy check.** gitleaks and trufflehog find keys, not your name.
   Run both; neither replaces the other.
3. **The gate sits on push, not commit.** A rebase, a rewritten history or a re-added remote brings
   back history the commit hook never saw.

## The terms list (the only thing you supply)

Kept outside every repository, one entry per line, `#` starts a comment:

```
# ~/.hermes/privacy-terms.txt
<your real name>
<English name or account>
<school / employer>
<other project codenames>
<old accounts that keep appearing in fixtures>
```

Resolution order: `--terms-file` → `$PRIVACY_TERMS_FILE` → `~/.hermes/privacy-terms.txt` →
`~/.hermes/prepublish-terms.txt` → `~/.config/privacy-preflight/terms.txt`. `--term` adds one word
for a single run; `PRIVACY_TERMS="a b"` does the same.

An empty list proves only that there are no keys, paths, emails or IPs. It does **not** prove there is
no name. The tool prints a warning and still exits 0; the hooks pass `--require-terms` so that in
their case an empty list is exit 2.

## What it checks

| Category | Contents |
|---|---|
| credential | AWS, GitHub, OpenAI, Stripe, Slack, Google, Telegram, GitLab, npm tokens; private keys; JWTs; connection strings and URLs carrying a password |
| machine-path | `<drive>:\Users\<account>\`, `/home/<account>/`, `/Users/<account>/`, UNC `\\<host>` |
| email | any address that is not a placeholder domain such as `example.com` |
| ip-address | public and LAN IPv4 |
| phone | Taiwan mobile form `09xx-xxx-xxx` |
| national-id | Taiwan ID form: one letter + 1/2 + eight digits |
| identity-term | every word in the terms list |
| sensitive-file | `.env`, `state.db`, `*.db`, `*.sqlite`, `*.har`, `*.pem`, `id_rsa`, `*.pub`, `authorized_keys`, `.bash_history`, `credentials*`, `secrets*`, `mcp.json` |
| commit-identity | author and committer of every commit, plus annotated tag taggers and tag messages; an email that is not a placeholder is reported |
| commit-message | the message body of every commit (the usual home of real names and ticket URLs) |
| remotes | a remote URL in `.git/config` that carries a password (a file scan cannot see it) |
| untracked-file | a sensitive file that is not ignored, so the next `git add .` takes it |

Scope: the index (what you will actually commit), every blob reachable from `--all` (including
deleted files), commit identities, commit messages, the remote in `.git/config`, untracked files.
A deleted file is still reported, because deleting it is another commit.

Documented placeholders such as `<account>`, `<host>` or `{name}` are not findings — an example is not
a machine.

If the terms list sits inside the audited repository the tool exits 2 — a checker carrying the names
it checks is itself the leak.

## Where the scan sits in the flow

Five places, the hooks own two of them: the front stops files that must not enter, the back stops
history that already entered.

| Place | What is scanned | Who |
|---|---|---|
| Choosing files and `.gitignore` | file names | you; near-zero cost |
| `commit` | staged | the `pre-commit` hook |
| `push` | index + every commit | the `pre-push` hook, the real gate |
| CI on a PR | the PR content | optional; only this layer sees someone else's fork |
| Before flipping to public | everything + human reading | you run `--repo .` |

- **The PR CI layer is optional; this user chose not to use it.** An outside contributor's code is
  their responsibility, and pressing merge is that gate. Unless the user asks, do not add a
  `pull_request` workflow, and do not call that layer necessary for completeness.
- Its only reason to exist is that a local hook cannot see someone else's fork. When the user skips
  it, state the residual risk (that commit enters the history at merge) instead of silently dropping it.
- Run output is not this tool's business: data in CI logs and error messages is output, and the
  scanner does not parse output. That layer rests on discipline while writing code.

## One install covers every repository

`install_global_hook.sh` points `core.hooksPath` at `~/.hermes/privacy-hooks` and writes two hooks:

| Hook | When | Scans |
|---|---|---|
| `pre-commit` | every commit | staged only, seconds, `--staged` |
| `pre-push` | every push | full tree + every commit + identities |

- No file is added inside the repository and `.git/hooks` is not touched.
- A repository's own existing hook is chained: `core.hooksPath` hides it from git, so the installed
  hook calls it back.
- The scanner path is recorded as an absolute path. If the scanner is gone, the push is **refused**,
  never silently allowed.
- `--force` is needed to take over an existing `core.hooksPath` (husky and friends); `--uninstall`
  restores the previous value.
- To bypass, use git's own `--no-verify`. The tool opens no other back door.

## Per-repository settings live in git config

Do not add a config file to the project for this:

```bash
git config --global --add privacy.dataDirs cases,work,probe   # these directories never ship
git config --global --add privacy.allow tests/test_hygiene.py # the detector itself: path checks only
git config --global --add privacy.ignore 'tests/fixtures/.*canary' # a line you confirmed is fake
```

`--allow` skips machine-path checks only; credentials, emails, IPs and terms in that file are still
checked. `--ignore` is for fake keys and demo addresses, matched as a regex against
`<category> <where> <detail>`.

**Masking settings are read from the global config only.** `privacy.allow`, `privacy.ignore` and
`privacy.dataDirs` come from `git config --global`; the scanned repository's own `.git/config` is not
trusted, because otherwise a hostile repository writes one line `privacy.ignore = .*` and passes
itself. Add `--trust-repo-config` when one repository really must override.

**No terms list, no run.** The hooks always pass `--require-terms`: without a list the exit code is 2
instead of a warning followed by `clean`.

**Binary and encoding.** UTF-16 text (Windows PowerShell output, `.reg` files) is decoded before the
scan instead of being skipped as binary because of its NUL bytes. Every git call carries
`-c core.quotePath=false -c core.fsmonitor=false`: the first makes non-ASCII paths come out raw on any
git version, the second stops `git status` from running a program named in the scanned repository's
own `.git/config`.

## Reading the output

```
== credential: 1
  tree: config.py:12  model-provider-key starting sk-a…
== summary
  1 finding(s) — DO NOT PUBLISH
```

Exit codes: `0` clean, `1` findings, `2` the scan could not run (not a git repository, and so on).
CI and release scripts read the exit code, never grep the text. `--report out.json` writes a
machine-readable report.

## Prove it can fail

A green light means nothing until the red one has been seen.

```bash
python <skill>/scripts/privacy_scan.py --selftest    # 21 synthetic canaries, all fake
bash   <skill>/scripts/verify_gate.sh                # a real commit and push, and both are blocked
```

`verify_gate.sh` creates a temporary bare remote and runs real `git commit` and `git push`: the leaky
one must be refused **and the log must say `DO NOT PUBLISH`** (a crashing hook is not a gate), the
clean one must pass. Eight steps green means the machine has a gate.

## Publishing order

1. `git status` clean, `privacy_scan.py --repo .` exits 0.
2. Push to a **private** repository first and read the file list on GitHub with a stranger's eyes.
3. Then change to public and confirm with an anonymous request that `raw.githubusercontent.com` can
   read it (a private repo makes catalogs and installs fail silently).

## What it cannot catch

- Text and EXIF inside photos, screenshots and PDFs — binaries are not parsed.
- Data assembled at run time from a database or an API: that is program behaviour, not file content.
- A name you did not put in the terms list. The tool does not know who anyone is.
- Meaning: a sentence that describes your address without a comparable literal.
- **Anything already on GitHub**: a local scanner cannot see the remote history. That is
  `references/leak-recovery.md`.

## Maintaining the tool itself

- Fake data for detectors is assembled at run time (see `drive` and `fake_key` in the selftest); no
  file holds a value that looks real. Split the string where the detector matches: an email across
  the `@`, a phone number in the middle, a path after the drive letter. Splitting it merely in two
  halves leaves one half complete — and that half is a whole email to the scanner, which then reports
  its own test file. After adding a canary, scan this repository with its own scanner; that is the
  only proof it carries no data.
- Add a selftest canary with every new rule, so the rule proves it can go red.
- When this tool is placed inside a repository it is its own detector: mark it with `--allow`, but the
  terms list still applies.
- Keep a guard test inside the repo (for example `tests/terms_guard.py`): it reads the terms file from
  outside the repo, compares every file with **the scanner's own matcher**, and prints counts, never
  words. That proves two things at once — no identifier in this repository, and no second
  implementation of the matching rules to drift.

## Pitfalls when building and verifying this kind of gate

- **A global `core.hooksPath` hides a repository's own hooks.** The installed hook must call back
  `$(git rev-parse --git-common-dir)/hooks/pre-push` and use `cmp -s` to tell whether that file is
  itself. Comparing path strings gets it wrong (the same file appears as `C:/…` and `/c/…`), and the
  wrong answer is recursion or a skipped hook.
- **Windows/MSYS: native programs do not take `/c/…`.** `pwd` prints an MSYS path, and handing it to
  `git` or `python` gives not found or cannot change to. Convert with `cygpath -w` first: the scanner
  path written into the pointer file, `--repo`, `git -C`, `GIT_CONFIG_GLOBAL`.
- **The selftest must switch hooks off first.** In the temporary repository run
  `git config core.hooksPath <a directory that does not exist>`; otherwise the machine's global gate
  blocks the canary commit and the test measures the gate instead of the detector (symptom:
  CalledProcessError from `git commit`).
- **Never test the installer against the real global config.** Point `HOME` and `GIT_CONFIG_GLOBAL` at
  a sandbox, both as native paths. `git config --global` reads `GIT_CONFIG_GLOBAL`, while python's `~`
  follows `USERPROFILE` and ignores `HOME` — when they disagree you think you changed something while
  the real config is what got scanned.
- **Do not pipe when measuring an exit code.** After `bash verify.sh | tail -5`, `$?` is `tail`'s.
  Write `bash verify.sh > out 2>&1; echo $?`, or the pass is one you made yourself.
- **Handle the three kinds of first-scan false positive separately; never loosen a rule.** Detector
  file → `--allow`; fake key in a test → `--ignore`; the same file appearing in several historical
  versions → dedupe in the report. Loosening a rule dismantles the gate.
- **When testing the push gate, create the leaky commit with `--no-verify`.** If `pre-commit` blocks
  it, the `git push` step passes only because there is nothing to push, and you measured the commit
  gate instead of the push gate. Also, do not test "remove the file, then push" in the same
  repository: the history still holds that commit. A clean push needs a fresh repository.
- **The history scan costs seconds to minutes; do not use a short timeout.** Tens of commits take
  seconds, hundreds take longer; give verification runs 300 seconds or more, and do not run two
  instances in parallel.

## References

- `references/leak-recovery.md`: what to do, in order, after a leak.
- Related skills: `pre-public-repo-review` (publishing flow and history rewriting), `secret-hygiene`
  (key rotation).
