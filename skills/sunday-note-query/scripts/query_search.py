#!/usr/bin/env python3
"""Find Wiki documents relevant to literal query terms."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from vault_scope import wiki_access

WIKI_DIR = Path("知识库")
INDEX_PATH = WIKI_DIR / "索引.md"
MAINTENANCE_LOG_PATH = WIKI_DIR / "知识库维护日志.md"


@dataclass(frozen=True)
class Candidate:
    path: Path
    is_index: bool
    coverage: int
    signal_coverage: int
    score: int
    counts: Counter[str]


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="+", help="Literal search terms; each argument remains one term.")
    parser.add_argument("--vault-root", default=".", help="Vault root path.")
    parser.add_argument("--limit", type=positive_int, default=10)
    parser.add_argument("--mode", choices=("work", "personal"))
    return parser.parse_args()


def normalize_terms(raw_terms: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        term = raw_term.strip().lower()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    if not terms:
        raise SystemExit("No search terms provided")
    return terms


def wiki_markdown_files(wiki_root: Path, maintenance_log: Path) -> list[Path]:
    files: set[Path] = set()
    for candidate in ([wiki_root] if wiki_root.is_file() else wiki_root.rglob("*.md")):
        resolved = candidate.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(wiki_root):
            continue
        if resolved != maintenance_log:
            files.add(resolved)
    return sorted(files)


def rg_candidates(vault_root: Path, files: list[Path], terms: list[str]) -> set[Path] | None:
    if not shutil.which("rg"):
        return None

    command = ["rg", "-i", "-F", "-l", "--null", "--glob", "*.md"]
    for term in terms:
        command.extend(["-e", term])
    if not files:
        return set()
    command.extend(["--", *map(str, files)])
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        return None

    allowed = set(files)
    candidates = {
        Path(raw_path.decode("utf-8")).resolve()
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    }
    candidates &= allowed
    candidates.update(
        path for path in files if any(term in rel(path, vault_root).lower() for term in terms)
    )
    return candidates


def score_candidate(path: Path, terms: list[str], index: Path, vault_root: Path) -> Candidate | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    name = rel(path, vault_root)
    haystack = f"{name}\n{text}".lower()
    counts = Counter({term: haystack.count(term) for term in terms})
    coverage = sum(count > 0 for count in counts.values())
    if coverage == 0:
        return None

    headings = re.findall(r"^#{1,6} +(.+)$", text, re.MULTILINE)
    signal_text = "\n".join([name, *headings]).lower()
    signal_coverage = sum(term in signal_text for term in terms)
    return Candidate(path, path == index, coverage, signal_coverage, sum(counts.values()), counts)


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def main() -> int:
    args = parse_args()
    terms = normalize_terms(args.query)
    vault_root = Path(args.vault_root).resolve()
    if not vault_root.is_dir():
        raise SystemExit(f"vault root is not a directory: {vault_root}")
    wiki_root = (vault_root / WIKI_DIR).resolve()
    if (vault_root / WIKI_DIR).is_symlink() or not wiki_root.is_relative_to(vault_root):
        raise SystemExit("拒绝符号链接 Wiki")
    if not wiki_root.is_dir():
        raise SystemExit(f"fixed Wiki directory is missing: {wiki_root}")
    index_path = (vault_root / INDEX_PATH).resolve()
    maintenance_log = (vault_root / MAINTENANCE_LOG_PATH).resolve()

    try:
        mode, roots = wiki_access(vault_root, args.mode)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
    files = sorted({p for root in roots for p in wiki_markdown_files(root, maintenance_log)})
    discovered = rg_candidates(vault_root, files, terms)
    backend = "rg fixed-string + Python scoring"
    if discovered is None:
        discovered = set(files)
        backend = "Python literal fallback"

    candidates = [
        candidate
        for path in discovered
        if (candidate := score_candidate(path, terms, index_path, vault_root))
    ]
    candidates.sort(
        key=lambda item: (
            item.is_index,
            -item.coverage,
            -item.signal_coverage,
            -item.score,
            rel(item.path, vault_root),
        )
    )

    print("# Query Candidates\n")
    print("- Scope: `wiki`")
    print(f"- Mode: `{mode}`; approved roots: {', '.join(rel(p, vault_root) for p in roots)}")
    print(f"- Terms: `{', '.join(terms)}`")
    print(f"- Search: `{backend}`")
    print(f"- Markdown files in scope: {len(files)}")
    print(f"- Scored candidates: {len(candidates)}\n")

    if not candidates:
        print("No matching Wiki pages found. This is a Wiki coverage gap.")
        return 0

    print("## Files\n")
    for index, candidate in enumerate(candidates[: args.limit], start=1):
        count_text = ", ".join(
            f"{term}:{candidate.counts[term]}" for term in terms if candidate.counts[term]
        )
        print(
            f"{index}. `{rel(candidate.path, vault_root)}` "
            f"coverage={candidate.coverage}/{len(terms)} "
            f"signal={candidate.signal_coverage} score={candidate.score}"
        )
        print(f"   - matched: {count_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
