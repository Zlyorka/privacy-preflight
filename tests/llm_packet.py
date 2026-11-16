"""Guard the second layer's packet builder.

Three properties matter and each one broke once while the builder was being written:

1. the report format survives any budget — cutting the assembled string at the end dropped it,
   leaving the reviewer with examples but no instructions,
2. a file that does not fit is named, never silently missing,
3. the packet never lands inside the repository it describes.

Run from the repository root:  python tests/llm_packet.py
Exit 0 when all three hold; 1 otherwise.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "llm_review_context.py"


def run(args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def build_repo(base: Path) -> Path:
    repo = base / "sample"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "core.hooksPath", str(repo / ".no-hooks")], cwd=str(repo),
                   check=True, capture_output=True)
    (repo / "about.md").write_text(
        "\n".join(f"我住某地，通勤到某處，第 {number} 天" for number in range(400)),
        encoding="utf-8")
    (repo / "notes.md").write_text("筆記：客戶在某建設\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-q",
                    "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    return repo


def main() -> int:
    problems = []
    with tempfile.TemporaryDirectory(prefix="llm-packet-") as tmp:
        base = Path(tmp)
        repo = build_repo(base)
        home = base / "home"
        out = base / "packet.md"

        args = ["--repo", str(repo), "--budget", "6000", "--out", str(out)]
        proc = run(args, {"PRIVACY_PREFLIGHT_HOME": str(home)})
        if proc.returncode != 0:
            problems.append(f"the builder failed: {proc.stderr.strip()[:200]}")
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
        if "## Report format" not in text:
            problems.append("the report format was cut by the budget")
        if "did not fit" not in text and "omitted at the packet budget" not in text:
            problems.append("a file was dropped without naming it in the packet")

        # No --out: the default must be outside the repository.
        proc = run(["--repo", str(repo)], {"PRIVACY_PREFLIGHT_HOME": str(home)})
        produced = list((home / "packets").glob("*.md")) if (home / "packets").is_dir() else []
        if not produced:
            problems.append("no packet was written to $PRIVACY_PREFLIGHT_HOME/packets")
        for path in produced:
            if repo in path.resolve().parents:
                problems.append(f"the packet landed inside the repository: {path}")
        inside = [path.name for path in repo.iterdir() if path.name.endswith(".md")]
        unexpected = [name for name in inside if name not in {"about.md", "notes.md"}]
        if unexpected:
            problems.append(f"the builder wrote into the repository: {unexpected}")

    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"llm packet: {len(problems)} problem(s)")
        return 1
    print("llm packet: 3/3 properties hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
