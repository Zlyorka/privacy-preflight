"""Keep the three language versions honest.

The READMEs are translated prose, so their sentences differ on purpose. What must not differ is the
machine-checkable part: the twelve category names, the flag names, and the result numbers the docs
quote. A translation that quietly drops a category is worse than no translation, because a reader of
that language believes the tool covers less or more than it does.

Run from the repository root:  python tests/docs_sync.py
Exit 0 when every document states the same facts; 1 otherwise.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CATEGORIES = [
    "credential", "sensitive-file", "machine-path", "email", "ip-address", "phone",
    "national-id", "identity-term", "commit-identity", "commit-message", "remotes",
    "untracked-file",
]
FLAGS = ["--require-terms", "--trust-repo-config", "--report", "--selftest"]
TOKENS = ["llm_review_context.py", "references/llm-review.md"]  # the second layer must be documented
NUMBERS = ["23", "11/11", "8 passed"]

DOCS = ["README.md", "README.zh-TW.md", "README.zh-CN.md"]
SPECS = ["SKILL.md", "docs/SKILL.en.md", "docs/SKILL.zh-CN.md"]


def read(name: str) -> str:
    path = ROOT / name
    if not path.is_file():
        raise SystemExit(f"docs sync: {name} is missing")
    return path.read_text(encoding="utf-8")


def main() -> int:
    problems: list[str] = []
    checked = 0

    for name in DOCS:
        text = read(name)
        checked += 1
        for category in CATEGORIES:
            if not re.search(rf"\|\s*{re.escape(category)}\s*\|", text):
                problems.append(f"{name}: category '{category}' is missing from the table")
        for flag in FLAGS:
            if flag not in text:
                problems.append(f"{name}: flag '{flag}' is not mentioned")
        for token in TOKENS:
            if token not in text:
                problems.append(f"{name}: '{token}' (the second layer) is not documented")
        for number in NUMBERS:
            if number not in text:
                problems.append(f"{name}: expected result '{number}' is not stated")
        for other in DOCS:
            if other != name and other not in text:
                problems.append(f"{name}: no link to {other}")

    for name in SPECS:
        text = read(name)
        checked += 1
        for category in CATEGORIES:
            if category not in text:
                problems.append(f"{name}: category '{category}' is missing")
        for flag in FLAGS:
            if flag not in text:
                problems.append(f"{name}: flag '{flag}' is not mentioned")
        for token in TOKENS:
            if token not in text:
                problems.append(f"{name}: '{token}' (the second layer) is not documented")

    scanner = read("scripts/privacy_scan.py")
    version = re.search(r'VERSION = "([^"]+)"', scanner)
    if not version:
        problems.append("scripts/privacy_scan.py: no VERSION line")
    else:
        for name in ["SKILL.md"]:
            if f"version: {version.group(1)}" not in read(name):
                problems.append(f"{name}: frontmatter version does not match {version.group(1)}")

    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"docs sync: {len(problems)} problem(s) in {checked} file(s)")
        return 1
    print(f"docs sync: {checked} file(s) consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
