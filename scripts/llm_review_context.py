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
    # `\bI\b` must stay case-sensitive: with IGNORECASE it matched `for i in range(...)`, so every
    # code file scored as first-person writing and crowded out the documents.
    ("self", r"我|我的|我們|本人|\bI\b|(?i:\b(?:my|me|we|our)\b)"),
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
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".sh", ".bash", ".ps1", ".sql", ".csv", ".html", ".css", ".java", ".go", ".rs", ".rb",
    ".php", ".kt", ".swift", ".c", ".h", ".cpp", ".cs", ".env", ".conf", ".lock",
}
# `@` means a decorator in code, not a contact detail; letting it score code files pushed the
# scripts ahead of the documents a reviewer actually needs.
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php", ".kt",
            ".swift", ".c", ".h", ".cpp", ".cs", ".sh", ".bash", ".ps1", ".sql"}
RISKY_PATH = re.compile(
    r"(?:^|[/._-])(?:tests?|spec|fixtures?|samples?|demos?|docs?|readmes?|notes?|todo|changelog|"
    r"about|profile|resume|履歷|工作日誌|會議|紀錄|logs?)(?:$|[/._-])", re.IGNORECASE)
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
    risky = bool(RISKY_PATH.search(name))
    if risky:
        value += 3
    if (hits or risky) and size < SMALL_FILE:
        value += 1
    for cue, count in hits.items():
        value += {"self": 3, "place": 3, "employer": 3, "school": 2, "identity": 3,
                  "contact": 1, "loose": 1, "ticket": 1}[cue] * min(count, 3)
    # Diversity beats repetition: a file that hits five different cues is more interesting than one
    # that says "我" forty times, which is what a long document does.
    if len(hits) > 2:
        value += 2 * (len(hits) - 2)
    # A very large file costs the packet a lot of budget for the same evidence.
    value -= min(4, size // 40_000)
    return value


def chosen_names_from_blocks(block: str) -> str:
    """The `### \`name\`` header identifies which file a dropped block belonged to."""
    for line in block.splitlines():
        if line.startswith("### `") and line.endswith("`"):
            return line[5:-1]
    return "?"


def byte_len(text: str) -> int:
    """Budgets are bytes. Counting characters let a Chinese packet run to ~3x the limit."""
    return len(text.encode("utf-8"))


def clamp_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    cut = encoded[:limit].decode("utf-8", "ignore")
    return cut.rsplit("\n", 1)[0] + "\n   … rest of this file omitted at the packet budget …"


def fence(text: str) -> str:
    """Fence longer than the longest backtick run inside the content, so a file containing ```
    cannot break out of its block and fake a section of the packet."""
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}\n"


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
        # -z: a name with a space or a newline would otherwise split into pieces and vanish.
        names = [chunk.decode("utf-8", "replace")
                 for chunk in git(repo, "ls-files", "-z").split(b"\x00") if chunk]
    except SystemExit as error:
        print(error, file=sys.stderr)
        return 2

    records = []
    too_large: list[str] = []
    for name in names:
        path = repo / name
        suffix = path.suffix.lower()
        if suffix not in TEXT_EXT:
            continue
        # is_file() follows a symlink, so check the link first: a repo could otherwise point a
        # reviewed file at ~/.ssh/id_rsa and the packet would carry it to whoever reads it.
        if path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.lstat()
        except OSError:
            continue
        if stat.st_size > MAX_FILE and suffix in TEXT_EXT:
            too_large.append(f"{name} ({stat.st_size // 1024} KB)")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_FILE and suffix in TEXT_EXT:
            too_large.append(f"{name} ({len(data) // 1024} KB)")
            continue
        if is_binary(data):
            continue
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        hits = {}
        for cue, pattern in CUES:
            if cue == "contact" and suffix in CODE_EXT:
                continue
            count = sum(1 for line in lines if pattern.search(line))
            if count:
                hits[cue] = count
        records.append({"name": name, "size": len(data), "lines": lines, "hits": hits,
                        "score": score(name, len(data), hits)})

    records.sort(key=lambda record: (-record["score"], record["name"]))
    ranked = [record for record in records if record["score"] > 0]
    chosen = ranked[:args.top]

    # Code is easy to score and hard to read for meaning; documents and fixtures are the opposite.
    # Keep at least a third of the slots for non-code files, or a script-heavy repository crowds
    # its own README out of the packet.
    if chosen:
        wanted_docs = max(1, len(chosen) // 3)
        docs_now = sum(1 for record in chosen if Path(record["name"]).suffix.lower() not in CODE_EXT)
        if docs_now < wanted_docs:
            extra = [record for record in ranked
                     if record not in chosen
                     and Path(record["name"]).suffix.lower() not in CODE_EXT][:wanted_docs - docs_now]
            chosen = sorted(chosen + extra, key=lambda record: (-record["score"], record["name"]))

    head: list[str] = ["# Review packet\n"]
    head.append(f"Repository: `{repo.name}` · {len(names)} tracked file(s) · "
                f"{len(records)} text file(s) · {len(chosen)} selected\n")
    head.append("## What this pass is for\n")
    head.append("The scanner already proved there are no credentials, machine paths, addresses or listed\n"
                "terms. Read the excerpts below for **meaning**: a place, a school, an employer, a client,\n"
                "a schedule, a person, a story that identifies the author without any literal a rule could\n"
                "match. Report only what you can cite by `path:line`.\n")
    if too_large:
        shown = ", ".join(f"`{item}`" for item in too_large[:5])
        more = f" and {len(too_large) - 5} more" if len(too_large) > 5 else ""
        head.append(f"\n> {len(too_large)} text file(s) exceed {MAX_FILE // 1000} KB and were not "
                    f"read for this pass: {shown}{more}. Review them by hand if they are "
                    f"documentation or notes.\n")
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
        is_code = Path(record["name"]).suffix.lower() in CODE_EXT

        # One pass over the lines, in line order: the old loop went cue by cue, so the first cue
        # spent the whole allowance and the file came out unsorted.
        hit_lines: list[int] = []
        for number, line in enumerate(record["lines"]):
            for cue, pattern in CUES:
                if is_code and cue == "contact":
                    continue    # a decorator is not a contact detail
                if pattern.search(line):
                    hit_lines.append(number)
                    break

        if record["size"] < SMALL_FILE or not hit_lines:
            # A short file, or a long one picked only by its path: show the start of it.
            limit = len(record["lines"]) if record["size"] < SMALL_FILE else 40
            body = "\n".join(f"{number + 1:>5}| {line}"
                             for number, line in enumerate(record["lines"][:limit]))
            body = clamp_bytes(body, allowance)
            blocks.append(f"\n### `{record['name']}`\n" + fence(body))
            remaining -= byte_len(blocks[-1])
            continue

        ranges: list[tuple[int, int]] = []
        for number in hit_lines:
            start, stop = max(0, number - 2), min(len(record["lines"]), number + 3)
            if ranges and start <= ranges[-1][1] + 5:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], stop))
            else:
                ranges.append((start, stop))

        parts: list[str] = []
        used = 0
        for start, stop in ranges:
            piece = "\n".join(f"{number + 1:>5}| {record['lines'][number]}"
                               for number in range(start, stop))
            piece_bytes = byte_len(piece)
            if used + piece_bytes > allowance:
                break
            parts.append(piece)
            used += piece_bytes
        if not parts:
            skipped.append(record["name"])
            continue
        block = f"\n### `{record['name']}`\n" + "".join(fence(piece) for piece in parts)
        blocks.append(block)
        remaining -= byte_len(block)

    # Assemble, then drop whole excerpts from the end until the result really fits. The note that
    # names the dropped files is part of the size, so it cannot be appended afterwards and hoped for.
    def assemble(dropped: list[str]) -> str:
        note = ""
        if dropped:
            note = (f"\n> {len(dropped)} selected file(s) did not fit the {args.budget}-byte budget "
                    f"and were not quoted: " + ", ".join(f"`{name}`" for name in dropped)
                    + ". Raise `--budget` or use `--top` to narrow the selection.\n")
        return "".join(head) + note + "\n## Excerpts\n" + "".join(blocks) + "".join(tail)

    while blocks and byte_len(assemble(skipped)) > args.budget:
        skipped.append(chosen_names_from_blocks(blocks.pop()))
    packet = assemble(skipped)
    if byte_len(packet) > args.budget:
        packet = clamp_bytes(packet, args.budget)
    if skipped:
        print(f"warning: {len(skipped)} selected file(s) omitted at the budget: "
              + ", ".join(skipped), file=sys.stderr)
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
