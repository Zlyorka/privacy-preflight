#!/usr/bin/env bash
# Install one machine-wide privacy gate for every git repository you push from.
#
#   bash install_global_hook.sh            # install (refuses to overwrite another hooksPath)
#   bash install_global_hook.sh --force    # take over an existing core.hooksPath
#   bash install_global_hook.sh --uninstall
#
# Nothing is written into any project. The gate lives in $HOME/.privacy-preflight/hooks and points at
# this skill's scanner by absolute path, so there is one copy of the rules, not one per repository.
set -uo pipefail

# Neutral by default: no agent runtime owns the gate. PRIVACY_PREFLIGHT_HOME overrides it.
PRIVACY_HOME="${PRIVACY_PREFLIGHT_HOME:-$HOME/.privacy-preflight}"
HOOK_DIR="$PRIVACY_HOME/hooks"
STATE="$HOOK_DIR/previous-hooks-path"
POINTER="$HOOK_DIR/scanner"
skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scanner="$skill_dir/scripts/privacy_scan.py"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Native programs (python, git) do not accept MSYS paths such as /c/Users/x on this host.
to_native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

HOOK_DIR_NATIVE="$(to_native "$HOOK_DIR")"

[ -f "$scanner" ] || die "scanner not found at $scanner"

if [ "${1:-}" = "--uninstall" ]; then
  previous=""
  [ -f "$STATE" ] && previous="$(cat "$STATE")"
  if [ -n "$previous" ]; then
    git config --global core.hooksPath "$previous"
  else
    git config --global --unset core.hooksPath 2>/dev/null || true
  fi
  rm -f "$HOOK_DIR/pre-push" "$HOOK_DIR/pre-commit" "$STATE" "$POINTER"
  printf 'removed the global privacy gate; core.hooksPath=%s\n' "${previous:-unset}"
  exit 0
fi

force=0
[ "${1:-}" = "--force" ] && force=1

current="$(git config --global --get core.hooksPath 2>/dev/null || true)"
if [ -n "$current" ] && [ "$current" != "$HOOK_DIR_NATIVE" ] && [ "$force" -ne 1 ]; then
  printf 'error: core.hooksPath is already set to %s\n' "$current" >&2
  printf '       re-run with --force to take it over; --uninstall restores the old value\n' >&2
  exit 1
fi

mkdir -p "$HOOK_DIR" || die "cannot create $HOOK_DIR"
if [ -n "$current" ] && [ "$current" != "$HOOK_DIR_NATIVE" ] && [ ! -f "$STATE" ]; then
  printf '%s\n' "$current" > "$STATE"
fi
printf '%s\n' "$(to_native "$scanner")" > "$POINTER"
git config --global core.hooksPath "$HOOK_DIR_NATIVE" || die "cannot set core.hooksPath"

cat > "$HOOK_DIR/pre-push" <<'HOOK'
#!/usr/bin/env bash
# Machine-wide privacy gate (privacy-preflight). Reads the scanner path recorded at install time.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"

root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
fail=0

# The repository's own hook, which core.hooksPath makes invisible to git still has to run.
common="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
case "$common" in /*) ;; *) common="$root/$common" ;; esac
own="$common/hooks/pre-push"
if [ -x "$own" ] && ! cmp -s "$own" "$HOOK_DIR/pre-push" 2>/dev/null; then
  "$own" "$@" || fail=1
fi

scanner="$(cat "$HOOK_DIR/scanner" 2>/dev/null || true)"
if [ -z "$scanner" ] || [ ! -f "$scanner" ]; then
  printf 'privacy-preflight: scanner missing (%s) — refusing to push.\n' "${scanner:-unset}" >&2
  printf '  re-run the skill'"'"'s scripts/install_global_hook.sh\n' >&2
  exit 1
fi

py=python3
command -v python3 >/dev/null 2>&1 || py=python

# The scanner reads privacy.allow / privacy.ignore from the *global* git config itself.
# Re-reading them here would let a repository's own .git/config silence the gate.
args=(--require-terms)

if ! "$py" "$scanner" --repo "$root" ${args[@]+"${args[@]}"}; then
  printf '\nprivacy-preflight: push blocked. Fix each line above, or --no-verify to override.\n' >&2
  fail=1
fi

exit "$fail"
HOOK

cat > "$HOOK_DIR/pre-commit" <<'HOOK'
#!/usr/bin/env bash
# Machine-wide privacy gate, staged-content only: fast enough to run on every commit.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"

root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
fail=0

common="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
case "$common" in /*) ;; *) common="$root/$common" ;; esac
own="$common/hooks/pre-commit"
if [ -x "$own" ] && ! cmp -s "$own" "$HOOK_DIR/pre-commit" 2>/dev/null; then
  "$own" "$@" || fail=1
fi

scanner="$(cat "$HOOK_DIR/scanner" 2>/dev/null || true)"
if [ -z "$scanner" ] || [ ! -f "$scanner" ]; then
  printf 'privacy-preflight: scanner missing (%s) — refusing to commit.\n' "${scanner:-unset}" >&2
  exit 1
fi

py=python3
command -v python3 >/dev/null 2>&1 || py=python

# The scanner reads privacy.allow / privacy.ignore from the *global* git config itself.
# Re-reading them here would let a repository's own .git/config silence the gate.
args=(--require-terms)

if ! "$py" "$scanner" --repo "$root" --staged ${args[@]+"${args[@]}"}; then
  printf '\nprivacy-preflight: commit blocked. Fix each line above, or --no-verify to override.\n' >&2
  fail=1
fi

exit "$fail"
HOOK

chmod +x "$HOOK_DIR/pre-push" "$HOOK_DIR/pre-commit" 2>/dev/null || true

printf 'installed: %s\n' "$HOOK_DIR/pre-push, $HOOK_DIR/pre-commit"
printf 'core.hooksPath = %s\n' "$HOOK_DIR_NATIVE"
printf 'scanner        = %s\n' "$scanner"
terms="$(python3 -c 'import os
from pathlib import Path
for c in (os.environ.get("PRIVACY_TERMS_FILE"), "~/.config/privacy-preflight/terms.txt",
          "~/.privacy-preflight/terms.txt"):
    if c and Path(os.path.expanduser(c)).is_file():
        print(os.path.expanduser(c)); break' 2>/dev/null || true)"
printf 'terms list     = %s\n' "${terms:-NONE}"
if [ -z "$terms" ]; then
  printf '\n  The gate is closed until that file exists: every commit and push is refused with exit 2,\n'
  printf '  because a scan without a terms list cannot prove there is no name in the repository.\n'
  printf '  Create it, then run this installer again (or just commit — the check runs every time):\n'
  printf '    mkdir -p ~/.config/privacy-preflight\n'
  printf '    printf "%%s\\n" "<your real name>" "<your handle>" > ~/.config/privacy-preflight/terms.txt\n'
fi
printf '\nNothing was added to any project. Prove the gate works: bash scripts/verify_gate.sh\n'
