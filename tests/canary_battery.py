"""Adversarial battery: plant one canary per category in the repo and require the scanner to catch it.

Run from the repository root:  python <this file>
Every canary is assembled at run time so this script contains no literal that matches a detector.
"""
import subprocess
import sys
from pathlib import Path

BS = chr(92)  # backslash, kept out of literals
SCAN = ["python", "scripts/privacy_scan.py", "--repo", "."]


def run(args):
    proc = subprocess.run(SCAN + args, capture_output=True, text=True)
    return proc.returncode, proc.stdout


STAGED_CASES = [
    ("openai key", "cfg.py", 'K = "sk-' + 'A1b2C3d4E5f6G7h8I9j0K1l2"\n', "credential"),
    ("dot env", ".env", "TOKEN=placeholder\n", "sensitive-file"),
    ("windows path", "notes.md", "built at C:" + BS + "Users" + BS + "x" + BS + "app\n", "machine-path"),
    ("email", "tests/t_mail.py", 'E = "person' + '@' + 'real-host.dev"\n', "email"),
    ("phone", "tests/t_phone.py", 'P = "09' + '12-345-678"\n', "phone"),
    ("national id", "tests/t_id.py", 'I = "A1' + '23456789"\n', "national-id"),
    ("terms", "docs/about.md", "written by CanaryPerson\n", "identity-term"),
    ("url credential", "docs/setup.md",
     "https://deploy:" + "tok3n" + "@example.com/x\n", "url-credentials"),
    ("private key", "keys/id.txt",
     "-----BEGIN " + "RSA PRIVATE KEY-----placeholder\n", "credential"),
]


def main() -> int:
    failures = 0
    for label, path, content, category in STAGED_CASES:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "-f", "-A"], capture_output=True)
        args = ["--staged"]
        if category == "identity-term":
            args += ["--term", "Canary" + "Person"]
        code, out = run(args)
        ok = code == 1 and category in out
        print(f"  {'PASS' if ok else 'FAIL'}  {label} -> {category}")
        failures += 0 if ok else 1
        subprocess.run(["git", "reset", "-q"], capture_output=True)
        target.unlink()
        for folder in ("tests", "docs", "keys"):
            directory = Path(folder)
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()

    # commit message and remote URL are not visible to --staged
    print("  -- full scan cases --")
    subprocess.run(["git", "remote", "add", "canary",
                    "https://user:" + "tok3n" + "@example.com/x.git"], capture_output=True)
    code, out = run([])
    ok = code == 1 and "remotes" in out
    print(f"  {'PASS' if ok else 'FAIL'}  remote URL credential -> remotes")
    failures += 0 if ok else 1
    subprocess.run(["git", "remote", "remove", "canary"], capture_output=True)

    # a terms file inside the repository must be refused
    Path("terms.txt").write_text("canary\n", encoding="utf-8")
    proc = subprocess.run(SCAN + ["--terms-file", "terms.txt"], capture_output=True, text=True)
    ok = proc.returncode == 2
    print(f"  {'PASS' if ok else 'FAIL'}  terms file inside repo is refused -> exit 2")
    failures += 0 if ok else 1
    Path("terms.txt").unlink()

    total = len(STAGED_CASES) + 2
    print(f"battery: {total - failures}/{total}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
