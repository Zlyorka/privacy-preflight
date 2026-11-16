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
    # -z keeps a name with a space whole; a plain split() used to shred "my notes.txt" into two
    # names that then failed is_file() and disappeared without a word.
    listed = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "-z"],
                            cwd=str(ROOT), capture_output=True).stdout
    files = [chunk.decode("utf-8", "replace") for chunk in listed.split(b"\x00") if chunk]
    offending = 0
    for name in files:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if privacy_scan.is_binary(data):
            continue
        text = privacy_scan.to_utf8(data)
        if privacy_scan.term_hits(text.lower(), terms):
            offending += 1
            print(f"terms guard: {name} contains a listed identifier", file=sys.stderr)

    # History matters too: a name removed in a later commit is still in the object database.
    history_offenders = 0
    blobs: list[str] = []
    if subprocess.run(["git", "rev-list", "--all", "--max-count=1"], cwd=str(ROOT),
                      capture_output=True).stdout.strip():
        out = subprocess.run(["git", "-c", "core.quotePath=false", "rev-list", "--all", "--objects"],
                             cwd=str(ROOT), capture_output=True).stdout
        blob_paths: dict[str, str] = {}
        for line in out.splitlines():
            sha, _, path = line.partition(b" ")
            if path and not path.startswith(b'"'):
                blob_paths.setdefault(sha.decode(), path.decode("utf-8", "replace"))
        kinds = privacy_scan.batch_check(ROOT, list(blob_paths))
        blobs = [sha for sha in blob_paths if kinds.get(sha, ("", 0))[0] == "blob"
                 and kinds[sha][1] <= privacy_scan.MAX_TEXT_BYTES]
        for sha, data in privacy_scan.batch_blobs(ROOT, blobs).items():
            if privacy_scan.is_binary(data):
                continue
            if privacy_scan.term_hits(privacy_scan.to_utf8(data).lower(), terms):
                history_offenders += 1
                print(f"terms guard: history blob {sha[:12]} ({blob_paths[sha]}) holds a listed "
                      f"identifier", file=sys.stderr)

    print(f"terms guard: {count} term(s) checked against {len(files)} tracked file(s) and "
          f"{len(blobs)} history blob(s), "
          f"{offending} file(s) offending, {history_offenders} history blob(s) offending")
    return 1 if (offending or history_offenders) else 0


if __name__ == "__main__":
    sys.exit(main())
