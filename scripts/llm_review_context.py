"""Build a bounded review packet for a language-model pass.

The scanner in this repository finds literals: credentials, paths, addresses, listed terms. It cannot
find meaning — a paragraph that says where someone lives without using a comparable string, a comment
that names a client, a fixture that quietly mirrors a real support ticket. That judgement needs a
reader, and a reader needs a bounded pile of text, ranked by risk, not a whole repository dumped into
a context window.

This script does the gathering, never the judging:

    python scripts/llm_review_context.py --repo . > packet.md

It reads only tracked files and binary-skips them, prints line numbers so a finding can be cited, and
stops at a byte budget. Exit 0 when a packet was produced, 2 when the repository cannot be read.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Cues that a line is worth a human or model reading. Deliberately broad: this pass is not a gate,
# it decides what to put in front of the reviewer, and the reviewer decides what matters.
CUES_RAW = [
    ("self", r"我|我的|我們|本人|\bI\b|\bmy\b|\bme\b|\bwe\b|\bour\b"),
    ("place", r"住|地址|戶籍|新北|台北|臺北|桃園|台中|臺中|台南|臺南|高雄|板橋|中和|新莊|淡水"),
    ("school", r"大學|學院|研究所|高中|高職|國中|科大|系所|畢業|學號|指導教授"),
    ("employer", r"公司|任職|在職|實習|打工|客戶|廠商|統編|部門|主管|同事"),
    ("identity", r"生日|年次|身分證|身份證|居留證|護照|兵役|星座"),
    ("contact", r"電話|手機|分機|信箱|e-?mail|@|line\s*id|lineid"),
    ("loose", r"TODO|FIXME|XXX|暫時|先這樣|測試用|假設|示意|範例資料|real data|actual"),
    ("ticket", r"工單|ticket|issue\s*#|JIRA|#\d{3,}|會議|meeting|訪談"),
]
CUES: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in CUES_RAW
]
TEXT_EXT = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".ya?ml", ".yml", ".toml", ".ini",
    ".cfg", ".sh", ".bash", ".ps1", ".sql", ".csv", ".html", ".css", ".java", ".go", ".rs", ".rb",
    ".php", ".kt", ".swift", ".c", ".h", ".cpp", ".cs", ".env", ".conf", ".lock",
}
RISKY_PATH = re.compile(
    r"(test|spec|fixture|sample|demo|doc|readme|note|todo|changelog|about|profile|resume|履歷|"
    r"工作日誌|會議|紀錄|log)", re.IGNORECASE)
SMALL_FILE = 8_000     # whole file when it is small: the risk often lives in the framing, not a line
MAX_FILE = 400_000     # never read a file bigger than this; the scanner reports it separately


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=str(repo),
                          capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"llm packet: git {args[0]} failed: "
                         f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def score(name: str, size: int, hits: dict[str, int]) -> int:
    value = 0
    if RISKY_PATH.search(name):
        value += 3
    if size < SMALL_FILE:
        value += 1
    for cue, count in hits.items():
        value += {"self": 3, "place": 3, "employer": 3, "school": 2, "identity": 3,
                  "contact": 1, "loose": 1, "ticket": 1}[cue] * min(count, 3)
    return value


def excerpt(lines: list[str], index: int, context: int = 2) -> list[str]:
    start = max(0, index - context)
    stop = min(len(lines), index + context + 1)
    return [f"{number + 1:>5}| {lines[number]}" for number in range(start, stop)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a review packet for a model pass")
    parser.add_argument("--repo", default=".", help="repository root")
    parser.add_argument("--budget", type=int, default=45_000, help="output byte budget")
    parser.add_argument("--top", type=int, default=25, help="how many files to quote")
    parser.add_argument("--out", help="write the packet here instead of stdout "
                                      "(default: $PRIVACY_PREFLIGHT_HOME/packets/, never inside the repo)")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    try:
        names = [line.decode("utf-8", "replace") for line in git(repo, "ls-files").split(b"\n") if line]
    except SystemExit as error:
        print(error, file=sys.stderr)
        return 2

    records = []
    for name in names:
        path = repo / name
        suffix = path.suffix.lower()
        if suffix not in TEXT_EXT or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if is_binary(data) or len(data) > MAX_FILE:
            continue
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        hits = {}
        for cue, pattern in CUES:
            count = sum(1 for line in lines if pattern.search(line))
            if count:
                hits[cue] = count
        records.append({"name": name, "size": len(data), "lines": lines, "hits": hits,
                        "score": score(name, len(data), hits)})

    records.sort(key=lambda record: (-record["score"], record["name"]))
    chosen = [record for record in records if record["score"] > 0][:args.top]

    head: list[str] = ["# Review packet\n"]
    head.append(f"Repository: `{repo.name}` · {len(names)} tracked file(s) · "
                f"{len(records)} text file(s) · {len(chosen)} selected\n")
    head.append("## What this pass is for\n")
    head.append("The scanner already proved there are no credentials, machine paths, addresses or listed\n"
                "terms. Read the excerpts below for **meaning**: a place, a school, an employer, a client,\n"
                "a schedule, a person, a story that identifies the author without any literal a rule could\n"
                "match. Report only what you can cite by `path:line`.\n")
    head.append("## Selected files\n")
    head.append("| File | Bytes | Cues |\n|---|---|---|\n")
    for record in chosen:
        cues = ", ".join(f"{cue}×{count}" for cue, count in sorted(record["hits"].items())) or "size/path"
        head.append(f"| `{record['name']}` | {record['size']} | {cues} |\n")
    tail = ["\n## Report format\n",
            "```\n== review: <category>\n  <path>:<line>  <quote, up to 120 chars>\n"
            "  why: <what a stranger learns>\n  fix: <what to change>\n== summary\n"
            "  <n> finding(s) — <verdict>\n```\n"]

    # The report format is what makes the excerpts usable, so it is reserved before any excerpt gets
    # a byte. Cutting the assembled string at the end dropped it on any repository that filled the
    # budget — the reviewer then had examples but no instructions.
    fixed = len("".join(head + tail).encode("utf-8"))
    if fixed > args.budget:
        print(f"warning: the header and the report format alone need {fixed} bytes, more than the "
              f"{args.budget}-byte budget; no excerpt will fit", file=sys.stderr)
    remaining = max(0, args.budget - fixed)
    per_file = max(1_500, remaining // max(1, len(chosen)))

    blocks: list[str] = []
    skipped: list[str] = []
    for record in chosen:
        allowance = min(per_file, max(0, remaining))
        if allowance < 400:
            skipped.append(record["name"])
            continue
        if record["size"] < SMALL_FILE:
            body = "\n".join(f"{number + 1:>5}| {line}"
                             for number, line in enumerate(record["lines"]))
            if len(body) > allowance:
                cut = body[:allowance].rsplit("\n", 1)[0]
                body = cut + "\n   … rest of this file omitted at the packet budget …"
            block = f"\n### `{record['name']}`\n```\n{body}\n```\n"
        else:
            parts: list[str] = []
            shown: set[int] = set()
            used = 0
            for _cue, pattern in CUES:
                for number, line in enumerate(record["lines"]):
                    if not pattern.search(line):
                        continue
                    start = max(0, number - 2)
                    if start in shown:
                        continue
                    piece = "\n".join(excerpt(record["lines"], number))
                    if used + len(piece) > allowance:
                        break
                    shown.add(start)
                    parts.append(f"```\n{piece}\n```\n")
                    used += len(piece)
                if used >= allowance:
                    break
            if not parts:
                skipped.append(record["name"])
                continue
            block = f"\n### `{record['name']}`\n" + "".join(parts)
        blocks.append(block)
        remaining -= len(block.encode("utf-8"))

    if skipped:
        head.append(f"\n> {len(skipped)} selected file(s) did not fit the "
                    f"{args.budget}-byte budget and were not quoted: "
                    + ", ".join(f"`{name}`" for name in skipped)
                    + ". Raise `--budget` or use `--top` to narrow the selection.\n")
        print(f"warning: {len(skipped)} selected file(s) omitted at the budget: "
              + ", ".join(skipped), file=sys.stderr)

    packet = "".join(head) + "\n## Excerpts\n" + "".join(blocks) + "".join(tail)
    if args.out:
        target: Path | None = Path(os.path.expanduser(args.out))
    else:
        # A packet is a full-text dump of the repository. Written inside the repository it becomes one
        # more thing to leak, so the default location is outside every repo.
        home = Path(os.environ.get("PRIVACY_PREFLIGHT_HOME")
                    or os.path.expanduser("~/.privacy-preflight"))
        target = home / "packets" / f"{repo.name}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packet, encoding="utf-8", newline="\n")
    print(f"packet: {target} ({len(packet.encode('utf-8'))} bytes, {len(chosen)} files)")
    if repo in target.resolve().parents:
        print("warning: the packet sits inside the repository — add it to .gitignore", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
