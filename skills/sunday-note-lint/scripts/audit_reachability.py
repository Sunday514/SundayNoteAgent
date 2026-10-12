#!/usr/bin/env python3
"""Audit layered Wiki navigation and source links without modifying the vault."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sunday-note-query/scripts"))
from vault_scope import wiki_access, require_scope, no_symlink


WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
IGNORED_SUFFIXES = {
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".svg",
    ".webp",
}
IGNORED_SCHEMES = ("http://", "https://", "mailto:", "obsidian://")


def parse_args() -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Vault root used for paths and output.")
    parser.add_argument("--mode", choices=("work", "personal"))
    parser.add_argument("--wiki-entry", required=True, help="Wiki navigation entry Markdown file.")
    parser.add_argument("--wiki-scope", action="append", required=True, help="Wiki file or directory. Repeatable.")
    parser.add_argument("--raw-scope", action="append", default=[], help="Raw file or directory. Repeatable.")
    parser.add_argument("--routine-scope", action="append", default=[], help="Routine file or directory. Repeatable.")
    parser.add_argument("--exclude", action="append", default=[], help="Relative path, file name, or glob to exclude.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(), parser


def inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_input(raw: str, root: Path, parser: argparse.ArgumentParser | None = None) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    no_symlink(path, root)
    path = path.resolve()
    if not inside_root(path, root):
        message = f"path is outside --root: {raw}"
        if parser:
            parser.error(message)
        raise ValueError(message)
    return path


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def matches_any(path: Path, root: Path, patterns: list[str]) -> bool:
    relative = rel(path, root)
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip("/")
        if path.name == normalized or relative == normalized or Path(relative).match(normalized):
            return True
    return False


def collect_scope(raw_scopes: Iterable[str], root: Path, parser: argparse.ArgumentParser) -> set[Path]:
    files: set[Path] = set()
    for raw in raw_scopes:
        path = resolve_input(raw, root, parser)
        if not path.exists():
            parser.error(f"scope does not exist: {raw}")
        if path.is_file():
            if path.suffix.lower() != ".md":
                parser.error(f"scope file is not Markdown: {raw}")
            files.add(path)
        else:
            for item in path.rglob("*.md"):
                if item.is_symlink():
                    continue
                resolved = item.resolve()
                if not inside_root(resolved, root):
                    parser.error(f"scope contains a path outside --root: {item}")
                files.add(resolved)
    return files


def body_without_header(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[end + 4 :]


def clean_target(raw: str, markdown: bool = False) -> str:
    target = raw.strip()
    if markdown:
        if target.startswith("<") and ">" in target:
            target = target[1 : target.index(">")]
        else:
            target = re.split(r"\s+[\"']", target, maxsplit=1)[0]
    else:
        target = target.split("|", 1)[0].rstrip("\\")
    return unquote(target.split("#", 1)[0].strip()).replace("\\", "/")


def ignored_target(target: str) -> bool:
    lowered = target.lower()
    return (
        not target
        or lowered.startswith(IGNORED_SCHEMES)
        or Path(lowered).suffix in IGNORED_SUFFIXES
        or target.startswith("Pasted image")
    )


def aliases(path: Path, root: Path) -> set[str]:
    relative = rel(path, root)
    without_suffix = str(Path(relative).with_suffix("")).replace("\\", "/")
    return {relative, without_suffix, path.stem}


def build_alias_map(files: set[Path], root: Path) -> dict[str, set[Path]]:
    result: dict[str, set[Path]] = {}
    for path in files:
        for alias in aliases(path, root):
            result.setdefault(alias, set()).add(path)
    return result


def exact_candidates(target: str, source: Path, root: Path, markdown: bool) -> list[Path]:
    if markdown:
        base = root if target.startswith("/") else source.parent
        raw = target.lstrip("/") if target.startswith("/") else target
    elif target.startswith(("./", "../")):
        base, raw = source.parent, target
    else:
        base, raw = root, target
    candidate = (base / raw).resolve()
    candidates = [candidate]
    if candidate.suffix.lower() != ".md":
        candidates.append(Path(f"{candidate}.md"))
    return list(dict.fromkeys(candidates))


def exact_aliases(candidates: list[Path], root: Path) -> list[str]:
    result: list[str] = []
    for candidate in candidates:
        if not inside_root(candidate, root):
            continue
        relative = rel(candidate, root)
        result.append(relative)
        if candidate.suffix.lower() == ".md":
            result.append(str(Path(relative).with_suffix("")).replace("\\", "/"))
    return list(dict.fromkeys(result))


def resolve_target(
    target: str,
    source: Path,
    root: Path,
    markdown: bool,
    active_aliases: dict[str, set[Path]],
    excluded_aliases: dict[str, set[Path]],
    scope_roots: list[Path],
) -> tuple[str, Path | None, list[Path], bool]:
    is_bare_wikilink = not markdown and "/" not in target and not target.startswith(("./", "../"))
    if is_bare_wikilink:
        stem = Path(target).stem
        matches = active_aliases.get(stem, set())
        if len(matches) == 1:
            return "resolved", next(iter(matches)), [], True
        if len(matches) > 1:
            return "ambiguous", None, sorted(matches), True
        if excluded_aliases.get(stem):
            return "ignored", None, [], False
        return "unresolved", None, [], True

    candidates = exact_candidates(target, source, root, markdown)
    candidate_aliases = exact_aliases(candidates, root)
    matches: set[Path] = set()
    excluded_matches: set[Path] = set()
    for alias in candidate_aliases:
        matches.update(active_aliases.get(alias, set()))
        excluded_matches.update(excluded_aliases.get(alias, set()))
    if len(matches) == 1:
        return "resolved", next(iter(matches)), [], True
    if len(matches) > 1:
        return "ambiguous", None, sorted(matches), True
    if excluded_matches:
        return "ignored", None, [], False
    in_scope = any(inside_root(candidate, scope) for candidate in candidates for scope in scope_roots)
    return "unresolved", None, [], in_scope


def reachable(entry: Path, graph: dict[Path, set[Path]]) -> set[Path]:
    seen = {entry}
    stack = [entry]
    while stack:
        source = stack.pop()
        for target in graph.get(source, set()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def audit(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"--root is not a directory: {args.root}")

    try:
        mode, allowed = wiki_access(root, args.mode)
        for scope in [args.wiki_entry, *args.wiki_scope]:
            require_scope(Path(scope), root, allowed)
        domains = ["工作"] + (["个人"] if mode == "personal" else [])
        for scopes, layer in ((args.raw_scope, "资料"), (args.routine_scope, "记录")):
            for scope in scopes:
                require_scope(Path(scope), root, [root / (domain + layer) for domain in domains])
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    wiki_all = collect_scope(args.wiki_scope, root, parser)
    raw_all = collect_scope(args.raw_scope, root, parser)
    routine_all = collect_scope(args.routine_scope, root, parser)
    overlaps = (wiki_all & raw_all) | (wiki_all & routine_all) | (raw_all & routine_all)
    if overlaps:
        parser.error(f"layer scopes overlap: {rel(sorted(overlaps)[0], root)}")

    all_files = wiki_all | raw_all | routine_all
    excluded = {path for path in all_files if matches_any(path, root, args.exclude)}
    wiki = wiki_all - excluded
    raw = raw_all - excluded
    routine = routine_all - excluded
    entry = resolve_input(args.wiki_entry, root, parser)
    if not entry.is_file() or entry not in wiki:
        parser.error("--wiki-entry must be an included file inside --wiki-scope")

    active = wiki | raw | routine
    active_aliases = build_alias_map(active, root)
    excluded_aliases = build_alias_map(excluded, root)
    kinds = {path: "wiki" for path in wiki} | {path: "raw" for path in raw} | {path: "routine" for path in routine}
    scope_roots = [resolve_input(raw_scope, root, parser) for raw_scope in args.wiki_scope + args.raw_scope + args.routine_scope]

    graph = {path: set() for path in wiki}
    raw_backlinks: set[Path] = set()
    broken: list[dict] = []
    ambiguous: list[dict] = []

    for source in sorted(wiki):
        body = body_without_header(source.read_text(encoding="utf-8"))
        links = [(match.group(1), False) for match in WIKILINK_RE.finditer(body)]
        links += [(match.group(1), True) for match in MD_LINK_RE.finditer(body)]
        links = list(dict.fromkeys(links))
        for raw_link, markdown in links:
            target = clean_target(raw_link, markdown)
            if ignored_target(target):
                continue
            state, resolved, candidates, in_scope = resolve_target(
                target,
                source,
                root,
                markdown,
                active_aliases,
                excluded_aliases,
                scope_roots,
            )
            if state == "resolved" and resolved:
                if kinds[resolved] == "wiki":
                    graph[source].add(resolved)
                elif kinds[resolved] == "raw":
                    raw_backlinks.add(resolved)
            elif state == "ambiguous":
                ambiguous.append({
                    "source": rel(source, root),
                    "link": raw_link,
                    "candidates": [rel(path, root) for path in candidates],
                })
            elif state == "unresolved" and in_scope:
                broken.append({"source": rel(source, root), "link": raw_link})

    seen = reachable(entry, graph)
    return {
        "scanned": {"wiki": len(wiki), "raw": len(raw), "routine": len(routine)},
        "wiki_entry": rel(entry, root),
        "wiki_unreachable": sorted(rel(path, root) for path in wiki - seen),
        "raw_unlinked": sorted(rel(path, root) for path in raw - raw_backlinks),
        "broken_links": sorted(broken, key=lambda item: (item["source"], item["link"])),
        "ambiguous_links": sorted(ambiguous, key=lambda item: (item["source"], item["link"])),
    }


def print_markdown(report: dict) -> None:
    print("# Layered Reachability Audit\n")
    print(f"- Wiki entry: `{report['wiki_entry']}`")
    print(f"- Scanned: Wiki {report['scanned']['wiki']}, Raw {report['scanned']['raw']}, Routine {report['scanned']['routine']}")
    for key, heading in (
        ("wiki_unreachable", "Wiki Unreachable"),
        ("raw_unlinked", "Raw Unlinked"),
        ("broken_links", "Broken Links"),
        ("ambiguous_links", "Ambiguous Links"),
    ):
        values = report[key]
        print(f"\n## {heading} ({len(values)})")
        for item in values:
            print(f"- `{item}`")


def main() -> int:
    args, parser = parse_args()
    report = audit(args, parser)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
