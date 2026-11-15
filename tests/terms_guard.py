"""Guard: this repository must never contain an identifier from the local terms list.

The terms file lives outside every repository (see the skill's README). This test reads it at run
time, checks every tracked file with **the scanner's own matcher** — never a second copy of the
rule — and prints counts only, so running it never echoes the identifiers it looked for.

Run from the repository root:  python tests/terms_guard.py
Exit 0 when nothing matched or when no terms file exists; 1 when a term is present.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_scanner():
    """Import the scanner by path: the repository root is not a package, and inserting a path
    into sys.path is invisible to type checkers."""
    spec = importlib.util.spec_from_file_location("privacy_scan",
                                                  ROOT / "scripts" / "privacy_scan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


privacy_scan = load_scanner()


def main() -> int:
    terms_file = privacy_scan.resolve_terms_file(None)
    if terms_file is None:
        print("terms guard: no terms file on this machine — skipped (nothing to compare against)")
        return 0
    try:
        terms_file.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        print("terms guard: FAIL — the terms file sits inside the repository", file=sys.stderr)
        return 1

    terms, count = privacy_scan.load_terms(terms_file, [])
    files = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                           text=True).stdout.split()
    offending = 0
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if privacy_scan.is_binary(data):
            continue
        if privacy_scan.term_hits(data.lower(), terms):
            offending += 1
            print(f"terms guard: {name} contains a listed identifier", file=sys.stderr)

    print(f"terms guard: {count} term(s) checked against {len(files)} tracked file(s), "
          f"{offending} file(s) offending")
    return 1 if offending else 0


if __name__ == "__main__":
    sys.exit(main())
