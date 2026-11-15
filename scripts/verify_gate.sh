#!/usr/bin/env bash
# Prove the gate actually blocks a push — end to end, through git, not by reading the hook.
#
#   1. the detector passes its synthetic canary suite
#   2. core.hooksPath points at the installed hook, and the hook can find the scanner
#   3. a real `git push` of a repository that contains a fake credential is rejected
#   4. the same push succeeds once the credential is gone
#
# Nothing outside a temporary directory is touched, and the fake values are generated here, so this
# script contains no real identifier or machine path.
set -uo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scanner="$skill_dir/scripts/privacy_scan.py"
HOOK_DIR="${PRIVACY_PREFLIGHT_HOME:-$HOME/.hermes/privacy-hooks}"
pass=0
fail=0

# Native programs (python, git) do not accept MSYS paths such as /c/Users/x on this host.
to_native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

scan_py="$(to_native "$scanner")"
hook_dir_native="$(to_native "$HOOK_DIR")"
py=python3
command -v python3 >/dev/null 2>&1 || py=python

ok()   { printf '  PASS  %s\n' "$*"; pass=$((pass + 1)); }
bad()  { printf '  FAIL  %s\n' "$*"; fail=$((fail + 1)); }

printf '== 1. detector self-test\n'
if "$py" "$scan_py" --selftest; then ok "detector catches every canary class"; else bad "detector self-test"; fi

printf '\n== 2. hook wiring\n'
wired="$(git config --global --get core.hooksPath 2>/dev/null || true)"
if [ "$wired" = "$hook_dir_native" ]; then ok "core.hooksPath = $HOOK_DIR"; else bad "core.hooksPath is '${wired:-unset}'"; fi
for hook in pre-push pre-commit; do
  if [ -x "$HOOK_DIR/$hook" ]; then ok "$hook is installed"; else bad "$hook is missing"; fi
done
pointer="$(cat "$HOOK_DIR/scanner" 2>/dev/null || true)"
if [ -n "$pointer" ] && [ -f "$pointer" ]; then ok "hook resolves the scanner"; else bad "scanner pointer is stale: ${pointer:-empty}"; fi

printf '\n== 3. the commit gate blocks the leak\n'
work="$(mktemp -d "${TMPDIR:-/tmp}/privacy-verify-XXXXXX")"
trap 'rm -rf "$work"' EXIT
work_native="$(to_native "$work")"
fake_key="sk-$(printf 'verify%.0s' 1 2 3 4 5)"
ident=(-c user.name=verify -c user.email=dev@localhost)

git init -q --bare "$work_native/remote.git"
git init -q "$work_native/leaky"
git -C "$work_native/leaky" remote add origin "$work_native/remote.git"
printf '# demo\n' > "$work/leaky/README.md"
git -C "$work_native/leaky" add -A
git -C "$work_native/leaky" "${ident[@]}" commit -q -m init
printf 'KEY="%s"\n' "$fake_key" > "$work/leaky/config.py"
git -C "$work_native/leaky" add -A

if git -C "$work_native/leaky" "${ident[@]}" commit -q -m "add config" >"$work/commit.log" 2>&1; then
  bad "a commit with a fake credential was accepted"
elif grep -q "DO NOT PUBLISH" "$work/commit.log"; then
  ok "a commit with a fake credential was blocked by the scanner"
else
  bad "the commit failed, but not because the scanner reported the credential — read the log"
  sed -n '1,20p' "$work/commit.log"
fi

printf '\n== 4. the push gate blocks a leak that is already committed\n'
if git -C "$work_native/leaky" "${ident[@]}" commit -q --no-verify -m "add config" \
     >"$work/commit2.log" 2>&1; then
  :
else
  bad "could not create the leaky commit with --no-verify (setup problem)"
fi
if git -C "$work_native/leaky" push -q origin HEAD:main >"$work/push1.log" 2>&1; then
  bad "push of a committed fake credential succeeded — the gate is open"
elif grep -q "DO NOT PUBLISH" "$work/push1.log"; then
  ok "push of a committed fake credential was blocked by the scanner"
else
  bad "the push failed, but not because the scanner reported the credential — read the log"
  sed -n '1,20p' "$work/push1.log"
fi

printf '\n== 5. a clean repository pushes normally\n'
git init -q --bare "$work_native/clean-remote.git"
git init -q "$work_native/clean"
git -C "$work_native/clean" remote add origin "$work_native/clean-remote.git"
printf '# demo\n\nA generic example with no personal data.\n' > "$work/clean/README.md"
git -C "$work_native/clean" add -A
git -C "$work_native/clean" "${ident[@]}" commit -q -m init
if git -C "$work_native/clean" push -q origin HEAD:main >"$work/push2.log" 2>&1; then
  ok "clean commit and push went through"
else
  bad "clean push was blocked — the gate is a false alarm"
  sed -n '1,25p' "$work/push2.log"
fi

printf '\n== summary\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || printf '  Read the failing step before you trust a clean scan.\n'
exit $(( fail == 0 ? 0 : 1 ))
