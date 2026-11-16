"""Adversarial battery: plant one canary per category and require the scanner to catch it.

The canaries are planted in a **temporary export** of this repository, never in the working tree,
so running the test cannot touch the index, the stash or a staged change of the person running it.

Every canary is assembled at run time, so this file contains no literal that matches a detector.

Run from the repository root:  python tests/canary_battery.py
Exit 0 when every class is caught; 1 when one is missed.
"""
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BS = chr(92)  # backslash, kept out of literals

STAGED_CASES = [
    ("openai key", "cfg.py", 'K = "sk-' + 'A1b2C3d4E5f6G7h8I9j0K1l2"\n', "credential"),
    ("dot env", ".env", "TOKEN=placeholder\n", "sensitive-file"),
    ("windows path", "notes.md", "built at C:" + BS + "Users" + BS + "x" + BS + "app\n", "machine-path"),
    ("email", "tests/t_mail.py", 'E = "person' + '@' + 'real-host.dev"\n', "email"),
    ("phone", "tests/t_phone.py", 'P = "09' + '12-345-678"\n', "phone"),
    ("national id", "tests/t_id.py", 'I = "A1' + '23456789"\n', "national-id"),
    ("terms", "docs/about.md", "written by CanaryPerson\n", "identity-term"),
    ("url credential", "docs/setup.md", "https://deploy:" + "tok3n" + "@example.com/x\n", "url-credentials"),
    ("private key", "keys/id.txt", "-----BEGIN " + "RSA PRIVATE KEY-----placeholder\n", "credential"),
    ("ip address", "docs/host.md", "server at 198.51.100.7\n", "ip-address"),
]
# Cases that need git metadata rather than a file on disk.
METADATA_CASES = [
    ("commit identity", "commit-identity", "RealName" + " Person", "person" + "@" + "real-host.dev"),
    ("commit message", "commit-message", "fix for CanaryPerson", None),
]


def export(destination: Path) -> None:
    """Copy the tracked files only, then make it a repository of its own."""
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT), capture_output=True)
    if archive.returncode != 0:
        raise SystemExit("canary battery: git archive failed — commit something first")
    # tarfile instead of an external `tar`: on Windows an MSYS tar reads `D:` as a remote host.
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(destination)
    subprocess.run(["git", "init", "-q"], cwd=str(destination), capture_output=True, check=True)
    subprocess.run(["git", "config", "core.hooksPath", str(destination / ".no-hooks")],
                   cwd=str(destination), capture_output=True, check=True)
    # A real HEAD, so `git reset` after each case actually clears the index and the cases stay
    # independent of each other.
    subprocess.run(["git", "add", "-A"], cwd=str(destination), capture_output=True, check=True)
    subprocess.run(["git", "-c", "user.name=battery", "-c", "user.email=battery@localhost",
                    "commit", "-q", "-m", "export"], cwd=str(destination),
                   capture_output=True, check=True)


def scan(work: Path, args):
    proc = subprocess.run([sys.executable, str(work / "scripts" / "privacy_scan.py"),
                           "--repo", str(work)] + args, capture_output=True, text=True)
    return proc.returncode, proc.stdout


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="privacy-battery-") as tmp:
        work = Path(tmp) / "repo"
        work.mkdir()
        export(work)

        for label, path, content, category in STAGED_CASES:
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "-f", "-A"], cwd=str(work), capture_output=True)
            args = ["--staged"]
            if category == "identity-term":
                args += ["--term", "Canary" + "Person"]
            code, out = scan(work, args)
            ok = code == 1 and category in out
            print(f"  {'PASS' if ok else 'FAIL'}  {label} -> {category}")
            failures += 0 if ok else 1
            subprocess.run(["git", "reset", "-q"], cwd=str(work), capture_output=True)
            target.unlink()

        # An untracked file is not in the index, so --staged cannot see it: a full scan must read it.
        (work / "notes.tmp").write_text("written by Canary" + "Person\n", encoding="utf-8")
        code, out = scan(work, ["--term", "Canary" + "Person"])
        # Content hits keep their own category (`identity-term` here); the location marks it as
        # untracked, which is what tells the reader it is not committed yet.
        ok = code == 1 and "untracked: notes.tmp" in out
        print(f"  {'PASS' if ok else 'FAIL'}  untracked file content is read -> untracked: location")
        failures += 0 if ok else 1
        (work / "notes.tmp").unlink()

        print("  -- metadata cases --")
        for label, category, message, email in METADATA_CASES:
            commit_args = ["git", "-c", "user.name=canary"]
            commit_args += ["-c", "user.email=" + (email or "dev@localhost")]
            subprocess.run(commit_args + ["commit", "-q", "--allow-empty", "--no-verify", "-m", message],
                           cwd=str(work), capture_output=True)
            args = [] if category == "commit-identity" else ["--term", "Canary" + "Person"]
            code, out = scan(work, args)
            ok = code == 1 and category in out
            print(f"  {'PASS' if ok else 'FAIL'}  {label} -> {category}")
            failures += 0 if ok else 1

        print("  -- full scan cases --")
        subprocess.run(["git", "remote", "add", "canary",
                        "https://user:" + "tok3n" + "@example.com/x.git"],
                       cwd=str(work), capture_output=True)
        code, out = scan(work, [])
        ok = code == 1 and "remotes" in out
        print(f"  {'PASS' if ok else 'FAIL'}  remote URL credential -> remotes")
        failures += 0 if ok else 1
        subprocess.run(["git", "remote", "remove", "canary"], cwd=str(work), capture_output=True)

        (work / "terms.txt").write_text("canary\n", encoding="utf-8")
        code, _ = scan(work, ["--terms-file", str(work / "terms.txt")])
        ok = code == 2
        print(f"  {'PASS' if ok else 'FAIL'}  terms file inside repo is refused -> exit 2")
        failures += 0 if ok else 1
        (work / "terms.txt").unlink()

    total = len(STAGED_CASES) + len(METADATA_CASES) + 3
    print(f"battery: {total - failures}/{total}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
