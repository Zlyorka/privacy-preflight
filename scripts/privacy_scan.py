#!/usr/bin/env python3
"""Is this repository safe to publish? Read-only audit of tree, index and every reachable commit.

This tool holds no identifiers of its own. The words that would expose you (your name, school,
employer, account names, project codenames, real emails) are read from a terms file outside every
repository, or from the environment, or from repeated --term flags. A checker that contains the
data it checks is itself the leak.

    python privacy_scan.py --repo .                      # audit the repository you are in
    python privacy_scan.py --repo . --report out.json    # same, plus machine-readable report
    python privacy_scan.py --selftest                    # prove the gate can fail, on synthetic data

Exit codes: 0 clean, 1 findings (do not publish), 2 the scan could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "1.3.2"

# ---------------------------------------------------------------- detection patterns

# A sample of the credential shapes that actually reach public repositories. Each entry is
# (category, pattern). Matched values are never printed in full.
SECRET_PATTERNS = [
    ("aws-access-key", rb"AKIA[0-9A-Z]{16}"),
    ("github-token", rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,}"),
    ("model-provider-key", rb"sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}"),
    ("stripe-key", rb"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("slack-token", rb"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    ("google-api-key", rb"AIza[0-9A-Za-z_\-]{30,}"),
    ("google-oauth-token", rb"ya29\.[0-9A-Za-z_\-]{20,}"),
    ("telegram-bot-token", rb"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    ("gitlab-token", rb"glpat-[A-Za-z0-9_\-]{20,}"),
    ("npm-token", rb"npm_[A-Za-z0-9]{30,}"),
    ("private-key", rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("connection-string", rb"(?:postgres|postgresql|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|amqps)://[^\s:@/]+:[^\s@/]+@"),
    ("url-credentials", rb"[a-z][a-z0-9+.\-]{1,10}://[^\s/:@]+:[^\s/@]+@"),
    ("jwt", rb"eyJ[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}"),
]
SECRETS = [(name, re.compile(pattern)) for name, pattern in SECRET_PATTERNS]

# Absolute paths reveal the machine, the account name and often the employer.
PATH_PATTERNS = [
    ("windows-path", re.compile(rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[A-Za-z0-9._$ -]+[\\/])")),
    ("home-path", re.compile(rb"/(?:home|Users)/[A-Za-z0-9._-]+/")),
    ("unc-path", re.compile(rb"\\\\[A-Za-z0-9._$-]+\\")),
]
# A path whose next component is a documented placeholder reveals nothing. Without this the
# scanner reports its own README for the example it exists to explain, and every clone starts red.
PLACEHOLDER_NEXT = b"<{["  # a path whose next component opens with one of these is an example
PLACEHOLDER_HOSTS = {b"host", b"server", b"hostname", b"example", b"your-host", b"yourhost"}

EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
IPV4_RE = re.compile(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Things that identify one person directly rather than by credential shape.
PHONE_RE = re.compile(rb"(?<![\d\-])09\d{2}-?\d{3}-?\d{3}(?![\d\-])")
NATIONAL_ID_RE = re.compile(rb"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![0-9])")
# A password or token embedded in a remote URL, read from .git/config rather than a file.
REMOTE_CRED_RE = re.compile(rb"://[^\s/:@]+:[^\s/@]+@")

PLACEHOLDER_EMAIL_DOMAINS = (b"example.com", b"example.org", b"example.net", b"localhost",
                             b"localhost.localdomain", b"test", b"invalid", b"example")
PLACEHOLDER_IPS = {b"0.0.0.0", b"127.0.0.1", b"255.255.255.0", b"255.255.255.255"}

# Files that must never be in a public repository at all.
SENSITIVE_FILE = re.compile(
    r"(^|/)(?:"
    r"\.env(?:\.[^/]*)?|\.netrc|\.npmrc|\.pgpass|\.htpasswd|"
    r"\.bash_history|\.zsh_history|\.python_history|"
    r"authorized_keys|known_hosts|"
    r"id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?|"
    r"state\.db|[^/]*\.(?:db|sqlite|sqlite3)|[^/]*\.har|"
    r"(?:credentials|secrets?|auth|mcp)(?:\.(?:json|ya?ml|txt|ini|env))?|"
    r"[^/]*\.(?:pem|key|p12|pfx|jks|keystore|ppk|kdbx|ovpn|pub)"
    r")$",
    re.IGNORECASE,
)
# Only a name that *is* a template is exempt: `.env.dist` yes, `dist.pem` no.
PLACEHOLDER_FILE_HINT = re.compile(
    r"(?:[._-](?:example|sample|template|placeholder)|"
    r"\.(?:dist|example|sample|template|placeholder))$",
    re.IGNORECASE,
)

# Words used as a file-extensions whitelist for `--staged` speed; anything else is still scanned
# by size only.
MAX_TEXT_BYTES = 2_000_000
LARGE_BLOB_BYTES = 2_000_000

CATEGORY_ORDER = [
    "credential", "sensitive-file", "machine-path", "email", "ip-address", "phone",
    "national-id", "identity-term", "commit-identity", "commit-message", "remotes",
    "untracked-file",
]


# ---------------------------------------------------------------- helpers

def git(repo: Path, *args: str, stdin: bytes | None = None, check: bool = True
        ) -> bytes:
    # core.quotePath: make non-ASCII paths come out raw on every git build and config.
    # core.fsmonitor: `git status` would otherwise execute a command named in the scanned
    # repository's own .git/config — a hostile repository could run code through the scan.
    proc = subprocess.run(["git", "-c", "core.quotePath=false", "-c", "core.fsmonitor=false",
                           *args], cwd=str(repo), capture_output=True, input=stdin)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def line_of(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def normalize_path(value: str) -> str:
    """Strip a leading ./ only. `lstrip('./')` would eat the dot of `.github/x`."""
    value = value.strip()
    return value[2:] if value.startswith("./") else value


def add(findings: list[dict], category: str, where: str, detail: str, line: int | None = None) -> None:
    findings.append({"category": category, "where": where, "line": line, "detail": detail})


# ---------------------------------------------------------------- term list

def resolve_terms_file(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(os.path.expanduser(explicit))
        if not path.is_file():
            print(f"error: terms file not found: {path}", file=sys.stderr)
            raise SystemExit(2)
        return path
    # Neutral locations only: no agent runtime owns the tool, and nothing is looked up under a
    # vendor directory.
    candidates = [os.environ.get("PRIVACY_TERMS_FILE"),
                  "~/.config/privacy-preflight/terms.txt",
                  "~/.privacy-preflight/terms.txt"]
    for candidate in candidates:
        if candidate:
            path = Path(os.path.expanduser(candidate))
            if path.is_file():
                return path
    return None


def load_terms(path: Path | None, extra: list[str]) -> tuple[list[bytes], int]:
    raw: list[str] = []
    if path is not None:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                raw.append(line)
    raw.extend(os.environ.get("PRIVACY_TERMS", "").split())
    raw.extend(extra)

    terms: list[bytes] = []
    for term in raw:
        value = term.strip().encode("utf-8")
        if len(value) >= 2 and value not in terms:
            terms.append(value)
    return terms, len(raw)


def term_hits(text_lower: bytes, terms: list[bytes]) -> list[bytes]:
    hits = []
    for term in terms:
        needle = term.lower()
        if needle not in text_lower:
            continue
        # For ASCII terms require a boundary so a short word does not match inside a longer one.
        if needle.isascii() and needle.isalpha() and len(needle) < 6:
            if not re.search(rb"(?<![a-z0-9])" + re.escape(needle) + rb"(?![a-z0-9])", text_lower):
                continue
        hits.append(term)
    return hits


# ---------------------------------------------------------------- content scan

def to_utf8(data: bytes) -> bytes:
    """Legacy Chinese text (Big5/CP950) is not UTF-8, and decoded as UTF-8 it becomes mojibake no
    term can match — a repository full of old `.txt` files scanned clean. Try the encodings a
    Taiwanese or Windows user actually has, in order, and keep the first that decodes."""
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError:
        pass
    for encoding in ("cp950", "big5", "cp1252"):
        try:
            return data.decode(encoding).encode("utf-8")
        except (UnicodeDecodeError, LookupError):
            continue
    return data


def scan_content(data: bytes, where: str, terms: list[bytes], findings: list[dict],
                 *, skip_paths: bool = False) -> None:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # UTF-16 text is common on Windows (PowerShell output, .reg files). Its NUL bytes used to
        # make is_binary() skip the whole file, so every one of them escaped the scan.
        try:
            data = data.decode("utf-16", "replace").encode("utf-8")
        except (UnicodeDecodeError, LookupError):
            return
    if is_binary(data):
        return
    data = to_utf8(data)
    seen: set[tuple[int, str]] = set()
    for name, pattern in SECRETS:
        for match in pattern.finditer(data):
            if any(abs(match.start() - offset) < 8 for offset, _ in seen):
                continue
            seen.add((match.start(), name))
            sample = match.group(0)[:4].decode("utf-8", "replace")
            add(findings, "credential", where, f"{name} starting {sample}…",
                line_of(data, match.start()))
    if not skip_paths:
        for name, pattern in PATH_PATTERNS:
            for match in pattern.finditer(data):
                value = match.group(0)
                # `C:\Users\<account>\` and `\\host\` are documentation, not a machine.
                nxt = data[match.end():match.end() + 1]
                if nxt and nxt[0] in PLACEHOLDER_NEXT:
                    continue
                if name == "unc-path" and value.strip(b"\\").split(b"\\")[0].lower() in PLACEHOLDER_HOSTS:
                    continue
                add(findings, "machine-path", where,
                    f"{name} {value.decode('utf-8', 'replace')}",
                    line_of(data, match.start()))
    for match in EMAIL_RE.finditer(data):
        value = match.group(0)
        low = value.lower()
        if low.startswith(b"git@"):
            continue
        if b"noreply@" in low or b"no-reply@" in low:
            continue
        domain = low.rsplit(b"@", 1)[-1]
        if domain in PLACEHOLDER_EMAIL_DOMAINS:
            continue
        if domain.startswith(b"example.") or domain.endswith((b".example", b".test", b".invalid")):
            continue
        if any(hint in low for hint in (b"@users.noreply.github", b"@types/", b"@users.noreply")):
            continue
        add(findings, "email", where, value.decode("utf-8", "replace"), line_of(data, match.start()))
    for match in IPV4_RE.finditer(data):
        value = match.group(0)
        if value in PLACEHOLDER_IPS:
            continue
        if any(int(part) > 255 for part in value.split(b".")):
            continue
        add(findings, "ip-address", where, value.decode("utf-8", "replace"), line_of(data, match.start()))
    lowered = data.lower()
    for term in term_hits(lowered, terms):
        offset = lowered.find(term.lower())
        add(findings, "identity-term", where, term.decode("utf-8", "replace"),
            line_of(data, offset) if offset >= 0 else None)
    for match in PHONE_RE.finditer(data):
        add(findings, "phone", where, match.group(0).decode("utf-8", "replace"),
            line_of(data, match.start()))
    for match in NATIONAL_ID_RE.finditer(data):
        add(findings, "national-id", where, match.group(0).decode("utf-8", "replace"),
            line_of(data, match.start()))


# ---------------------------------------------------------------- git sources

def batch_blobs(repo: Path, shas: list[str]) -> dict[str, bytes]:
    if not shas:
        return {}
    request = "".join(sha + "\n" for sha in shas).encode()
    out = git(repo, "cat-file", "--batch", stdin=request)
    result: dict[str, bytes] = {}
    index, total = 0, len(out)
    while index < total:
        newline = out.find(b"\n", index)
        if newline < 0:
            break
        header = out[index:newline].decode("utf-8", "replace").split()
        index = newline + 1
        if len(header) < 3:
            continue
        sha, kind, size = header[0], header[1], int(header[2])
        payload = out[index:index + size]
        index += size + 1
        if kind == "blob":
            result[sha] = payload
    return result


def batch_check(repo: Path, shas: list[str]) -> dict[str, tuple[str, int]]:
    """sha -> (type, size)"""
    if not shas:
        return {}
    request = "".join(sha + "\n" for sha in shas).encode()
    out = git(repo, "cat-file", "--batch-check", stdin=request)
    result: dict[str, tuple[str, int]] = {}
    for line in out.splitlines():
        parts = line.decode("utf-8", "replace").split()
        if len(parts) == 3 and parts[1] != "missing":
            result[parts[0]] = (parts[1], int(parts[2]))
    return result


def index_entries(repo: Path) -> list[tuple[str, str, int]]:
    """(path, blob sha, mode) for everything git would commit."""
    out = git(repo, "ls-files", "-s", "-z")
    entries = []
    for record in out.split(b"\x00"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        parts = meta.split()
        if len(parts) != 3:
            continue
        mode, sha, _stage = (part.decode() for part in parts)
        entries.append((path.decode("utf-8", "replace"), sha, int(mode, 8)))
    return entries


def check_path_name(path: str, findings: list[dict], where: str) -> None:
    base = path.rsplit("/", 1)[-1]
    if SENSITIVE_FILE.search("/" + path) and not PLACEHOLDER_FILE_HINT.search(base):
        add(findings, "sensitive-file", where, path)
        return
    if path.startswith(".") or "/." in path:
        return


def scan_index(repo: Path, terms: list[bytes], findings: list[dict], allow: set[str],
               data_dirs: list[str], only_paths: list[str] | None = None) -> int:
    entries = index_entries(repo)
    if only_paths is not None:
        wanted = set(only_paths)
        entries = [entry for entry in entries if entry[0] in wanted]
    selected = []
    for path, sha, mode in entries:
        if mode == 0o160000:  # submodule pointer
            continue
        selected.append((path, sha))
        where = f"tree: {path}"
        check_path_name(path, findings, where)
        for directory in data_dirs:
            if path.startswith(directory.rstrip("/") + "/"):
                add(findings, "sensitive-file", where, f"inside data directory {directory}")
                break
    shas = sorted({sha for _path, sha in selected})
    kinds = batch_check(repo, shas)
    scannable = [sha for sha in shas if kinds.get(sha, ("", 0))[1] <= MAX_TEXT_BYTES]
    contents = batch_blobs(repo, scannable)
    for path, sha in selected:
        data = contents.get(sha)
        where = f"tree: {path}"
        size = kinds.get(sha, ("", 0))[1]
        if data is None:
            if size > MAX_TEXT_BYTES:
                add(findings, "sensitive-file", where, f"{size} bytes, too large to scan")
            continue
        if len(data) > 200_000:
            add(findings, "sensitive-file", where, f"large file {len(data)} bytes, review by hand")
        scan_content(data, where, terms, findings, skip_paths=path in allow)
    return len(selected)


def scan_history(repo: Path, terms: list[bytes], findings: list[dict], allow: set[str],
                 data_dirs: list[str], max_objects: int) -> tuple[int, int]:
    if not git(repo, "rev-list", "--all", "--max-count=1").strip():
        return 0, 0
    out = git(repo, "rev-list", "--all", "--objects")
    blob_paths: dict[str, set[str]] = {}
    for line in out.splitlines():
        sha, _, path = line.partition(b" ")
        if not path:
            continue
        if path.startswith(b'"'):
            continue  # quoted C-style path: handled by the tree scan, rare in history
        blob_paths.setdefault(sha.decode(), set()).add(path.decode("utf-8", "replace"))
    if len(blob_paths) > max_objects:
        print(f"warning: {len(blob_paths)} objects exceed --max-objects {max_objects}; "
              f"history scan is partial", file=sys.stderr)
    all_shas = sorted(blob_paths)
    kinds = batch_check(repo, all_shas)
    blobs_all = [sha for sha in all_shas if kinds.get(sha, ("", 0))[0] == "blob"]
    truncated = len(blobs_all) > max_objects
    blobs = blobs_all[:max_objects]
    wanted = [sha for sha in blobs if kinds[sha][1] <= LARGE_BLOB_BYTES]
    for sha in blobs:
        size = kinds[sha][1]
        if size > LARGE_BLOB_BYTES:
            for path in sorted(blob_paths[sha])[:3]:
                add(findings, "sensitive-file", f"history: {path}",
                    f"{size} bytes, too large to scan, present in history")
    contents = batch_blobs(repo, wanted)
    for sha, data in contents.items():
        paths = sorted(blob_paths[sha])
        path = paths[0]
        extra = f" (+{len(paths) - 1} more path(s))" if len(paths) > 1 else ""
        where = f"history: {path}{extra}"
        for candidate in paths:
            check_path_name(candidate, findings, where)
            for directory in data_dirs:
                if candidate.startswith(directory.rstrip("/") + "/"):
                    add(findings, "sensitive-file", where, f"inside data directory {directory}")
                    break
        scan_content(data, where, terms, findings,
                     skip_paths=all(candidate in allow for candidate in paths))
    return len(blobs), truncated


def scan_identities(repo: Path, terms: list[bytes], findings: list[dict]) -> int:
    out = git(repo, "log", "--all", "--format=%an%x1f%ae%x1f%cn%x1f%ce")
    pairs: set[tuple[str, str]] = set()
    for line in out.decode("utf-8", "replace").splitlines():
        fields = line.split("\x1f")
        if len(fields) == 4:
            pairs.add((fields[0], fields[1]))
            pairs.add((fields[2], fields[3]))
    for name, email in sorted(pairs):
        low = email.lower().encode()
        if low and not (low.endswith(b"@localhost") or b"noreply" in low
                        or any(low.endswith(b"@" + domain) for domain in PLACEHOLDER_EMAIL_DOMAINS)):
            add(findings, "commit-identity", "commit", f"{name} <{email}> is not a placeholder")
        for term in terms:
            if term.lower() in name.lower().encode() or term.lower() in low:
                add(findings, "commit-identity", "commit",
                    f"author identity contains the term {term.decode('utf-8', 'replace')}")
    return len(pairs)


def scan_commit_messages(repo: Path, terms: list[bytes], findings: list[dict],
                         allow: set[str]) -> int:
    """Messages are published with the repo and are the last place people put a real name,
    a ticket URL or a copied key."""
    out = git(repo, "log", "--all", "--format=%h%x1f%B%x1e")
    count = 0
    for record in out.split(b"\x1e"):
        record = record.strip(b"\n")
        if not record:
            continue
        short, _, message = record.partition(b"\x1f")
        count += 1
        if not message.strip():
            continue
        where = f"commit-message: {short.decode('utf-8', 'replace')}"
        scan_content(message, where, terms, findings)
    return count


def scan_remote_urls(repo: Path, findings: list[dict]) -> int:
    """A token baked into a remote URL lives in .git/config, so the file scan never sees it."""
    out = git(repo, "config", "--get-regexp", r"^remote\..*\.url", check=False)
    count = 0
    for line in out.decode("utf-8", "replace").splitlines():
        count += 1
        _key, _, url = line.partition(" ")
        if REMOTE_CRED_RE.search(url.encode()):
            add(findings, "remotes", "git-config", "remote URL carries a password or token")
    return count


def config_values(repo: Path, key: str, *, scope: str = "--global") -> list[str]:
    """Settings that decide what to *ignore* must not be readable from the repository being
    scanned: a hostile repo could ship `privacy.ignore = .*` in its own .git/config and silence
    every finding. Only the user's global config is trusted unless --trust-repo-config is given."""
    out = git(repo, "config", scope, "--get-all", key, check=False)
    return [line.strip() for line in out.decode("utf-8", "replace").splitlines() if line.strip()]


def scan_tags(repo: Path, terms: list[bytes], findings: list[dict]) -> int:
    """An annotated tag carries its own tagger identity and message, separate from any commit.
    `git log` never shows them, so a tag made before the author email was anonymised still
    publishes the real name on the repository's Tags page."""
    out = git(repo, "for-each-ref", "refs/tags",
              "--format=%(refname:short)%1f%(taggername)%1f%(taggeremail)%1f%(contents)")
    count = 0
    for line in out.decode("utf-8", "replace").splitlines():
        name, _, rest = line.partition("\x1f")
        tagger_name, _, rest = rest.partition("\x1f")
        tagger_email, _, message = rest.partition("\x1f")
        if not tagger_name and not message.strip():
            continue  # lightweight tag: only points at a commit, nothing of its own to leak
        count += 1
        where = f"tag: {name}"
        if tagger_name or tagger_email:
            email = tagger_email.strip("<> ")
            low = email.lower().encode()
            if email and not (low.endswith(b"@localhost") or b"noreply" in low
                              or any(low.endswith(b"@" + d) for d in PLACEHOLDER_EMAIL_DOMAINS)):
                add(findings, "commit-identity", where, f"tagger {tagger_name} <{email}> is not a placeholder")
            for term in terms:
                if term.lower() in tagger_name.lower().encode() or term.lower() in low:
                    add(findings, "commit-identity", where,
                        f"tagger contains the term {term.decode('utf-8', 'replace')}")
        if message.strip():
            scan_content(message.encode(), f"tag-message: {name}", terms, findings)
    return count


def scan_untracked(repo: Path, findings: list[dict]) -> int:
    out = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    count = 0
    for record in out.split(b"\x00"):
        if not record.startswith(b"?? "):
            continue
        path = record[3:].decode("utf-8", "replace")
        count += 1
        check_path_name(path, findings, f"untracked: {path}")
    return count


def check_gitignore(repo: Path, notes: list[str]) -> None:
    path = repo / ".gitignore"
    if not path.exists():
        notes.append("no .gitignore: nothing stops a stray .env or key file from being added")
        return
    if path.is_symlink() or not path.is_file():
        # A directory or a symlink here used to raise, and the traceback exited 1 — a crash
        # reported as findings. Say what happened instead of guessing.
        notes.append(".gitignore is a symlink or not a regular file: skipped the ignore review")
        return
    try:
        if path.stat().st_size > 1_000_000:
            notes.append(".gitignore is over 1 MB: skipped the ignore review")
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        notes.append(f".gitignore could not be read ({exc.__class__.__name__}): skipped the ignore review")
        return
    for pattern, why in [(".env", "credentials file"), ("*.pem", "private key"),
                         ("*.key", "private key")]:
        if pattern not in text:
            notes.append(f".gitignore does not mention {pattern} ({why})")


# ---------------------------------------------------------------- report

def report(findings: list[dict], notes: list[str], terms_count: int, terms_source: str,
           stats: dict[str, int]) -> int:
    unique: list[dict] = []
    seen: set[tuple] = set()
    for finding in findings:
        key = (finding["category"], finding["where"], finding["line"], finding["detail"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    findings = unique

    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        grouped.setdefault(finding["category"], []).append(finding)

    print(f"privacy-preflight {VERSION}")
    print(f"  terms: {terms_count} from {terms_source}")
    print(f"  tree: {stats.get('tree', 0)} files, history: {stats.get('history', 0)} blobs, "
          f"identities: {stats.get('identities', 0)}, untracked: {stats.get('untracked', 0)}")
    for category in CATEGORY_ORDER + [key for key in grouped if key not in CATEGORY_ORDER]:
        items = grouped.get(category)
        if not items:
            continue
        print(f"\n== {category}: {len(items)}")
        for finding in items[:40]:
            location = finding["where"]
            if finding["line"]:
                location = f"{location}:{finding['line']}"
            print(f"  {location}  {finding['detail']}")
        if len(items) > 40:
            print(f"  … and {len(items) - 40} more")
    if notes:
        print("\n== notes")
        for note in notes:
            print(f"  {note}")

    print("\n== summary")
    if findings:
        print(f"  {len(findings)} finding(s) — DO NOT PUBLISH")
        print("  Each line is a literal that would become public. Remove it and commit the removal.")
        return 1
    print("  clean — index, every reachable commit, identities, staging")
    if terms_count == 0:
        print("  WARNING: the terms list was empty. This proves only that no credentials, paths,")
        print("  emails or IPs are present, not that no name, school or employer is.")
    return 0


# ---------------------------------------------------------------- selftest

def selftest() -> int:
    script = str(Path(__file__).resolve())
    results: list[tuple[bool, str]] = []

    def run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, script, *args], capture_output=True, text=True,
                              cwd=cwd)

    def make_repo(root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        for command in (("init", "-q"),):
            subprocess.run(["git", *command], cwd=str(root), capture_output=True, check=True)
        # A machine-wide core.hooksPath would otherwise block these canary commits and turn the
        # self-test into a test of the installed gate rather than of the detector.
        subprocess.run(["git", "config", "core.hooksPath", str(root / ".no-hooks")],
                       cwd=str(root), capture_output=True, check=True)
        return root

    def commit(root: Path, message: str, email: str = "dev@localhost") -> None:
        subprocess.run(["git", "-c", "user.name=canary", "-c", f"user.email={email}",
                        "commit", "-q", "-m", message], cwd=str(root), capture_output=True,
                       check=True)

    def write(root: Path, name: str, text: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def expect(label: str, condition: bool, proc: subprocess.CompletedProcess) -> None:
        results.append((bool(condition), label))
        if not condition:
            print(f"  FAIL {label}\n{proc.stdout}\n{proc.stderr}", file=sys.stderr)

    # Synthetic canaries, assembled at run time so this file contains no literal that looks like
    # real data or a real machine path.
    drive = "C" + ":" + "\\" + "Users" + "\\" + "canary" + "\\"
    fake_key = "sk-" + "canary" * 6
    fake_email = "canary" + "@" + "canarymail.dev"
    fake_term = "Canary" + "Person"
    host_email = "canary" + "@" + "nowhere-canary.dev"

    with tempfile.TemporaryDirectory(prefix="privacy-preflight-") as tmp:
        base = Path(tmp)

        clean = make_repo(base / "clean")
        write(clean, "README.md", "# demo\n\nA generic example.\n")
        write(clean, "src/app.py", "def main():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=str(clean), capture_output=True, check=True)
        commit(clean, "init")
        proc = run("--repo", str(clean))
        expect("clean repository passes", proc.returncode == 0, proc)

        leaker = make_repo(base / "leaky")
        write(leaker, "config.py", f'KEY = "{fake_key}"\n')
        subprocess.run(["git", "add", "-A"], cwd=str(leaker), capture_output=True, check=True)
        commit(leaker, "init")
        proc = run("--repo", str(leaker))
        expect("credential is caught", proc.returncode == 1 and "credential" in proc.stdout, proc)

        pathy = make_repo(base / "paths")
        write(pathy, "notes.md", f"built at {drive}work\\\n")
        subprocess.run(["git", "add", "-A"], cwd=str(pathy), capture_output=True, check=True)
        commit(pathy, "init")
        proc = run("--repo", str(pathy))
        expect("machine path is caught", proc.returncode == 1 and "machine-path" in proc.stdout,
               proc)

        mailer = make_repo(base / "mail")
        write(mailer, "tests/test_contact.py", f'EMAIL = "{fake_email}"\n')
        subprocess.run(["git", "add", "-A"], cwd=str(mailer), capture_output=True, check=True)
        commit(mailer, "init")
        proc = run("--repo", str(mailer))
        expect("email in a test fixture is caught",
               proc.returncode == 1 and "email" in proc.stdout and "tests/test_contact.py" in proc.stdout,
               proc)

        placeholder = make_repo(base / "placeholders")
        bs = chr(92)
        fake_path = "C" + ":" + bs + "Users" + bs + "<account>" + bs
        fake_unc = bs + bs + "host" + bs
        write(placeholder, "docs/examples.md", f"written as {fake_path} and {fake_unc} in docs\n")
        subprocess.run(["git", "add", "-A"], cwd=str(placeholder), capture_output=True, check=True)
        commit(placeholder, "init")
        proc = run("--repo", str(placeholder))
        expect("documented placeholders are not reported as machine paths",
               proc.returncode == 0, proc)

        utf16 = make_repo(base / "utf16")
        (base / "utf16" / "notes.txt").write_bytes(f'KEY = "{fake_key}"\n'.encode("utf-16"))
        subprocess.run(["git", "add", "-A"], cwd=str(utf16), capture_output=True, check=True)
        commit(utf16, "init")
        proc = run("--repo", str(utf16))
        expect("a UTF-16 text file is decoded instead of skipped as binary",
               proc.returncode == 1 and "credential" in proc.stdout, proc)

        tagged = make_repo(base / "tags")
        write(tagged, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(tagged), capture_output=True, check=True)
        commit(tagged, "init")
        subprocess.run(["git", "-c", f"user.name={fake_term}", "-c", "user.email=dev@localhost",
                        "tag", "-a", "v1", "-m", "release"], cwd=str(tagged),
                       capture_output=True, check=True)
        proc = run("--repo", str(tagged), "--term", fake_term)
        expect("an annotated tag's tagger name is caught",
               proc.returncode == 1 and "tag: v1" in proc.stdout, proc)

        keyfile = make_repo(base / "keyfile")
        write(keyfile, "certs/dist.pem", "placeholder\n")
        write(keyfile, ".env.dist", "TOKEN=placeholder\n")
        subprocess.run(["git", "add", "-A"], cwd=str(keyfile), capture_output=True, check=True)
        commit(keyfile, "init")
        proc = run("--repo", str(keyfile))
        expect("dist.pem is not mistaken for a template file",
               proc.returncode == 1 and "certs/dist.pem" in proc.stdout, proc)
        expect(".env.dist is treated as a template", ".env.dist" not in proc.stdout, proc)

        legacy = make_repo(base / "legacy")
        (base / "legacy" / "about.txt").write_bytes(
            f"作者：{fake_term}\n".encode("cp950"))
        subprocess.run(["git", "add", "-A"], cwd=str(legacy), capture_output=True, check=True)
        commit(legacy, "init")
        proc = run("--repo", str(legacy), "--term", fake_term)
        expect("a Big5/CP950 file is decoded instead of read as mojibake",
               proc.returncode == 1 and "identity-term" in proc.stdout, proc)

        weird = make_repo(base / "weird")
        write(weird, "a.txt", "nothing to see\n")
        subprocess.run(["git", "add", "-A"], cwd=str(weird), capture_output=True, check=True)
        commit(weird, "init")
        (base / "weird" / ".gitignore").mkdir()  # a crash used to exit 1, reading as "findings"
        proc = run("--repo", str(weird))
        expect("an unreadable .gitignore is a note, not a traceback",
               proc.returncode == 0 and "Traceback" not in proc.stderr, proc)

        silenced = make_repo(base / "silenced")
        write(silenced, "config.py", f'KEY = "{fake_key}"\n')
        subprocess.run(["git", "add", "-A"], cwd=str(silenced), capture_output=True, check=True)
        commit(silenced, "init")
        subprocess.run(["git", "config", "privacy.ignore", ".*"], cwd=str(silenced),
                       capture_output=True, check=True)
        proc = run("--repo", str(silenced))
        expect("a repository cannot silence the scan through its own .git/config",
               proc.returncode == 1 and "credential" in proc.stdout, proc)

        named = make_repo(base / "terms")
        write(named, "docs/about.md", f"{fake_term} wrote this.\n")
        subprocess.run(["git", "add", "-A"], cwd=str(named), capture_output=True, check=True)
        commit(named, "init")
        proc = run("--repo", str(named))
        expect("without a terms list the name is missed", proc.returncode == 0, proc)
        proc = run("--repo", str(named), "--term", fake_term)
        expect("the terms list catches the name",
               proc.returncode == 1 and "identity-term" in proc.stdout, proc)

        deleted = make_repo(base / "deleted")
        write(deleted, ".env", "TOKEN=placeholder\n")
        subprocess.run(["git", "add", "-A"], cwd=str(deleted), capture_output=True, check=True)
        commit(deleted, "add env")
        subprocess.run(["git", "rm", "-q", ".env"], cwd=str(deleted), capture_output=True,
                       check=True)
        commit(deleted, "remove env")
        proc = run("--repo", str(deleted))
        expect("a .env deleted in a later commit is still caught",
               proc.returncode == 1 and "history" in proc.stdout, proc)

        identity = make_repo(base / "identity")
        write(identity, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(identity), capture_output=True, check=True)
        commit(identity, "init", email=host_email)
        proc = run("--repo", str(identity))
        expect("a real commit email is caught",
               proc.returncode == 1 and "commit-identity" in proc.stdout, proc)

        message = make_repo(base / "message")
        write(message, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(message), capture_output=True, check=True)
        commit(message, f"pair with {fake_term}")
        proc = run("--repo", str(message), "--term", fake_term)
        expect("a name in a commit message is caught",
               proc.returncode == 1 and "commit-message" in proc.stdout, proc)

        personal = make_repo(base / "personal")
        # Assembled at run time so this file never contains a matching phone or id itself.
        phone = "09" + "12-345-678"
        tw_id = "A1" + "23456789"
        write(personal, "tests/fixture.py", f'PHONE = "{phone}"\nID = "{tw_id}"\n')
        subprocess.run(["git", "add", "-A"], cwd=str(personal), capture_output=True, check=True)
        commit(personal, "init")
        proc = run("--repo", str(personal))
        expect("a phone number and a national id are caught",
               proc.returncode == 1 and "phone" in proc.stdout and "national-id" in proc.stdout,
               proc)

        remote = make_repo(base / "remote")
        write(remote, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(remote), capture_output=True, check=True)
        commit(remote, "init")
        subprocess.run(["git", "remote", "add", "origin",
                        "https://user:" + "tok3n" + "@example.com/x.git"],
                       cwd=str(remote), capture_output=True, check=True)
        proc = run("--repo", str(remote))
        expect("a credential in a remote URL is caught",
               proc.returncode == 1 and "remotes" in proc.stdout, proc)

        inline_url = make_repo(base / "inline-url")
        write(inline_url, "docs/setup.md",
              "clone with https://deploy:" + "tok3n" + "@example.com/x.git\n")
        subprocess.run(["git", "add", "-A"], cwd=str(inline_url), capture_output=True, check=True)
        commit(inline_url, "init")
        proc = run("--repo", str(inline_url))
        expect("a credential inside a URL in a file is caught",
               proc.returncode == 1 and "url-credentials" in proc.stdout, proc)

        inside = make_repo(base / "terms-inside")
        write(inside, "README.md", "# demo\n")
        write(inside, "terms.txt", "canary\n")
        subprocess.run(["git", "add", "-A"], cwd=str(inside), capture_output=True, check=True)
        commit(inside, "init")
        proc = run("--repo", str(inside), "--terms-file", str(inside / "terms.txt"))
        expect("a terms file inside the repo is refused", proc.returncode == 2, proc)

        staged = make_repo(base / "staged")
        write(staged, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(staged), capture_output=True, check=True)
        commit(staged, "init")
        write(staged, ".env", "TOKEN=placeholder\n")
        subprocess.run(["git", "add", "-A"], cwd=str(staged), capture_output=True, check=True)
        proc = run("--repo", str(staged), "--staged")
        expect("--staged catches what is about to be committed",
               proc.returncode == 1 and ".env" in proc.stdout, proc)

        dirty = make_repo(base / "untracked")
        write(dirty, "README.md", "# demo\n")
        subprocess.run(["git", "add", "-A"], cwd=str(dirty), capture_output=True, check=True)
        commit(dirty, "init")
        write(dirty, "id_rsa", "placeholder\n")
        proc = run("--repo", str(dirty))
        expect("an untracked key file is caught",
               proc.returncode == 1 and "untracked" in proc.stdout, proc)

    failed = [label for ok, label in results if not ok]
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    for label in failed:
        print(f"  FAILED: {label}")
    return 0 if not failed else 1


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a repository for anything that must not become public.")
    parser.add_argument("--repo", default=".", help="repository path (default: current directory)")
    parser.add_argument("--terms-file", default=os.environ.get("PRIVACY_TERMS_FILE"),
                        help="newline-separated identifiers to look for; must live OUTSIDE the repo")
    parser.add_argument("--term", action="append", default=[], help="extra identifier (repeatable)")
    parser.add_argument("--allow", action="append", default=[],
                        help="path whose machine-path literals are known detectors (repeatable); "
                             "credentials, emails, IPs and terms are still checked there")
    parser.add_argument("--ignore", action="append", default=[],
                        help="regex matched against '<where> <detail>' to drop a finding you have "
                             "read and confirmed is a canary or a documented example (repeatable)")
    parser.add_argument("--data-dirs", default="",
                        help="comma-separated directory names that must never be in the repo, "
                             "e.g. cases,work,probe")
    parser.add_argument("--trust-repo-config", action="store_true",
                        help="also read privacy.allow / privacy.ignore / privacy.dataDirs from the "
                             "scanned repository's own .git/config; a hostile repository can use "
                             "that to silence itself, so it is off by default")
    parser.add_argument("--require-terms", action="store_true",
                        help="exit 2 when no terms list is available; the hooks use this so a "
                             "missing list cannot silently downgrade the gate")
    parser.add_argument("--staged", action="store_true",
                        help="scan only what is staged (fast; for a pre-commit hook)")
    parser.add_argument("--no-history", action="store_true", help="skip the all-commit scan")
    parser.add_argument("--max-objects", type=int, default=200_000,
                        help="cap the number of history objects scanned (default 200000)")
    parser.add_argument("--report", help="write a JSON report to this path")
    parser.add_argument("--selftest", action="store_true",
                        help="run the synthetic canary tests that prove the gate can fail")
    parser.add_argument("--version", action="version", version=f"privacy-preflight {VERSION}")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    repo = Path(args.repo).resolve()
    try:
        git(repo, "rev-parse", "--is-inside-work-tree")
    except (RuntimeError, OSError) as exc:
        print(f"error: not a usable git repository ({exc})", file=sys.stderr)
        return 2

    terms_file = resolve_terms_file(args.terms_file)
    if terms_file is not None:
        try:
            terms_file.resolve().relative_to(repo)
        except ValueError:
            pass
        else:
            print(f"error: the terms file lives inside the repository ({terms_file}).\n"
                  f"       a checker that carries the names it checks is itself the leak",
                  file=sys.stderr)
            return 2
    terms, terms_count = load_terms(terms_file, args.term)
    if terms_count == 0:
        print("warning: no terms file and no --term given — names, schools and employers cannot "
              "be detected", file=sys.stderr)
        if args.require_terms:
            print("error: --require-terms was given and no terms list was found; refusing to run",
                  file=sys.stderr)
            return 2
    allow = {normalize_path(path) for path in args.allow}
    scope = None if args.trust_repo_config else "--global"
    if scope:
        allow.update(normalize_path(path) for path in config_values(repo, "privacy.allow"))
        args.ignore = list(args.ignore) + config_values(repo, "privacy.ignore")
    else:
        allow.update(normalize_path(path) for path in config_values(repo, "privacy.allow", scope="--local"))
        args.ignore = list(args.ignore) + config_values(repo, "privacy.ignore", scope="--local")
    data_dirs = [part.strip() for part in args.data_dirs.split(",") if part.strip()]
    for value in config_values(repo, "privacy.dataDirs", scope=scope or "--local"):
        data_dirs.extend(part.strip() for part in value.split(",") if part.strip())

    findings: list[dict] = []
    notes: list[str] = []
    stats: dict[str, int] = {}

    truncated = False
    if args.staged:
        staged_out = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        staged_paths = [record.decode("utf-8", "replace")
                        for record in staged_out.split(b"\x00") if record]
        stats["tree"] = scan_index(repo, terms, findings, allow, data_dirs, staged_paths)
        notes.append("staged only: history, identities, tags, messages and remotes are NOT scanned")
    else:
        stats["tree"] = scan_index(repo, terms, findings, allow, data_dirs)
        if not args.no_history:
            stats["history"], truncated = scan_history(repo, terms, findings, allow, data_dirs,
                                                       args.max_objects)
        stats["identities"] = scan_identities(repo, terms, findings)
        stats["tags"] = scan_tags(repo, terms, findings)
        stats["messages"] = scan_commit_messages(repo, terms, findings, allow)
        stats["remotes"] = scan_remote_urls(repo, findings)
        stats["untracked"] = scan_untracked(repo, findings)
        check_gitignore(repo, notes)

    if args.ignore:
        try:
            patterns = [re.compile(pattern) for pattern in args.ignore]
        except re.error as exc:
            print(f"error: an --ignore pattern is not a valid regex: {exc}", file=sys.stderr)
            return 2
        before = len(findings)
        findings = [finding for finding in findings
                    if not any(pattern.search(f"{finding['category']} {finding['where']} {finding['detail']}")
                               for pattern in patterns)]
        dropped = before - len(findings)
        if dropped:
            notes.append(f"{dropped} finding(s) dropped by --ignore")
            if dropped == before and before:
                # Everything was suppressed: say so loudly rather than reporting a silent clean.
                notes.append("WARNING: --ignore removed every finding of this run")

    source = str(terms_file) if terms_file else "no terms file"
    exit_code = report(findings, notes, terms_count, source, stats)
    if truncated:
        print("error: the history scan stopped at --max-objects, so it cannot say 'clean'; "
              "raise the limit and run again", file=sys.stderr)
        exit_code = 2

    if args.report:
        payload = {
            "tool": f"privacy-preflight {VERSION}",
            "repo": str(repo),
            "terms_file": str(terms_file) if terms_file else None,
            "terms_count": terms_count,
            "stats": stats,
            "findings": findings,
            "notes": notes,
            "exit_code": exit_code,
        }
        Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        print(f"  report: {args.report}")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - the exit code matters more than the exception type
        # A crash used to surface as exit 1, which reads as "findings", and --report never got
        # written. Exit 2 means "could not scan", which is what a traceback actually is.
        print(f"error: the scan could not finish: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print("error: exit 2 means the scan did not run to completion — do not read this as clean",
              file=sys.stderr)
        sys.exit(2)
