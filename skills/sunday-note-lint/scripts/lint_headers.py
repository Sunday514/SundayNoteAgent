#!/usr/bin/env python3
"""Check deterministic Wiki header requirements without modifying files."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REQUIRED_FIELDS = (
    "last_updated",
    "update_count",
    "sources",
    "topic",
    "keywords",
)
LIST_FIELDS = {"sources", "keywords"}


@dataclass(frozen=True)
class Issue:
    code: str
    field: str
    detail: str


@dataclass
class Report:
    path: str
    topic: str
    issues: list[Issue]


def parse_args() -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Vault root used for paths and output.")
    parser.add_argument("--scope", action="append", required=True, help="Wiki file or directory. Repeatable.")
    parser.add_argument("--exclude", action="append", default=[], help="Relative path, file name, or glob to exclude.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--limit", type=int, default=0, help="Maximum issue files to output. 0 means all.")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be a non-negative integer")
    return args, parser


def inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_input(raw: str, root: Path, parser: argparse.ArgumentParser) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not inside_root(path, root):
        parser.error(f"path is outside --root: {raw}")
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


def collect_files(scopes: Iterable[str], root: Path, parser: argparse.ArgumentParser) -> list[Path]:
    files: set[Path] = set()
    for raw in scopes:
        path = resolve_input(raw, root, parser)
        if not path.exists():
            parser.error(f"scope does not exist: {raw}")
        if path.is_file():
            if path.suffix.lower() != ".md":
                parser.error(f"scope file is not Markdown: {raw}")
            files.add(path)
        else:
            for item in path.rglob("*.md"):
                resolved = item.resolve()
                if not inside_root(resolved, root):
                    parser.error(f"scope contains a path outside --root: {item}")
                files.add(resolved)
    return sorted(files)


def extract_header(text: str) -> tuple[str | None, bool]:
    if not text.startswith("---\n"):
        return None, False
    end = text.find("\n---", 4)
    if end == -1:
        return None, True
    return text[4:end], False


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if " #" in value and not value.startswith(("\"", "'", "[")):
        value = value.split(" #", 1)[0].rstrip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in "\"'":
            if quote == char:
                quote = None
            elif quote is None and (
                index == 0 or value[index - 1].isspace() or value[index - 1] in "[,"
            ):
                quote = char
        elif char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def parse_inline_list(value: str) -> list[Any] | None:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [parse_scalar(item.strip()) for item in inner.split(",")]


def parse_header(header_text: str) -> dict[str, Any]:
    header: dict[str, Any] = {}
    current_list: str | None = None
    for line in header_text.splitlines():
        key_match = re.match(r"^([A-Za-z_]+):(?:\s*(.*))?$", line)
        if key_match:
            key = key_match.group(1)
            value = strip_inline_comment((key_match.group(2) or "").strip())
            current_list = None
            if key in LIST_FIELDS:
                inline = parse_inline_list(value)
                if inline is not None:
                    header[key] = inline
                elif value == "":
                    header[key] = []
                    current_list = key
                else:
                    header[key] = parse_scalar(value)
            else:
                header[key] = parse_scalar(value)
            continue
        if current_list and line.strip().startswith("- "):
            header[current_list].append(parse_scalar(line.strip()[2:]))
    return header


def valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def inspect_header(header: dict[str, Any], duplicate_topics: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    for field in REQUIRED_FIELDS:
        if field not in header:
            issues.append(Issue("missing_field", field, f"missing {field}"))

    if "last_updated" in header and not valid_date(header["last_updated"]):
        issues.append(Issue("bad_date", "last_updated", "last_updated must be YYYY-MM-DD"))
    if "update_count" in header and not valid_count(header["update_count"]):
        issues.append(Issue("bad_count", "update_count", "update_count must be a non-negative integer"))

    for field in ("sources", "keywords"):
        if field not in header:
            continue
        value = header[field]
        if not isinstance(value, list):
            issues.append(Issue(f"bad_{field}", field, f"{field} must be a list"))
        elif not value:
            issues.append(Issue(f"empty_{field}", field, f"{field} is empty"))

    topic = header.get("topic")
    if "topic" in header:
        if not isinstance(topic, str):
            issues.append(Issue("bad_topic", "topic", "topic must be a string"))
        elif not topic.strip():
            issues.append(Issue("empty_topic", "topic", "topic is empty"))
        elif topic in duplicate_topics:
            issues.append(Issue("duplicate_topic", "topic", "topic appears in multiple files"))

    return issues


def duplicate_topics(headers: dict[Path, dict[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    for header in headers.values():
        topic = header.get("topic")
        if isinstance(topic, str) and topic.strip():
            counts[topic] = counts.get(topic, 0) + 1
    return {topic for topic, count in counts.items() if count > 1}


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"--root is not a directory: {args.root}")
    files = [path for path in collect_files(args.scope, root, parser) if not matches_any(path, root, args.exclude)]
    headers: dict[Path, dict[str, Any]] = {}
    initial_issues: dict[Path, list[Issue]] = {}

    for path in files:
        text = path.read_text(encoding="utf-8")
        header_text, unterminated = extract_header(text)
        if unterminated:
            headers[path] = {}
            initial_issues[path] = [Issue("broken_header", "", "unterminated YAML header")]
        elif header_text is None:
            headers[path] = {}
            initial_issues[path] = [Issue("missing_header", "", "missing YAML header")]
        else:
            headers[path] = parse_header(header_text)
            initial_issues[path] = []

    duplicates = duplicate_topics(headers)
    reports: list[Report] = []
    for path in files:
        issues = initial_issues[path]
        if not issues:
            issues = inspect_header(headers[path], duplicates)
        if issues:
            topic = headers[path].get("topic")
            reports.append(Report(rel(path, root), topic if isinstance(topic, str) else "", issues))
    reports.sort(key=lambda report: report.path)
    issue_files = len(reports)
    if args.limit:
        reports = reports[: args.limit]
    return {
        "scanned": len(files),
        "issue_files": issue_files,
        "reports": [
            {"path": report.path, "topic": report.topic, "issues": [asdict(issue) for issue in report.issues]}
            for report in reports
        ],
    }


def print_markdown(result: dict) -> None:
    print("# Wiki Header Lint\n")
    print(f"- Scanned: {result['scanned']}")
    print(f"- Files with issues: {result['issue_files']}\n")
    print("| 文件 | topic | 问题 |")
    print("|---|---|---|")
    for report in result["reports"]:
        details = "; ".join(issue["detail"] for issue in report["issues"])
        print(f"| `{report['path']}` | {report['topic']} | {details.replace('|', '\\|')} |")


def main() -> int:
    args, parser = parse_args()
    result = run(args, parser)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
