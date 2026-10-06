#!/usr/bin/env python3
"""Dependency-free packaging checks for the core Sunday Note skills."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_SKILLS = (
    "sunday-note-ingest",
    "sunday-note-lint",
    "sunday-note-query",
    "sunday-note-monitor",
)
LINT_DESCRIPTION = "仅在用户显式调用 `$sunday-note-lint` 时，对整个 Wiki 执行检查与维护。"
PAPER_DESCRIPTION = "用户要求精读或总结本地 PDF 论文时使用。"


def parse_skill(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"missing frontmatter: {path}"
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError(f"unterminated frontmatter: {path}") from exc

    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        assert separator and key.strip(), f"invalid frontmatter line in {path}: {line}"
        key = key.strip()
        assert key not in frontmatter, f"duplicate frontmatter key in {path}: {key}"
        frontmatter[key] = value.strip()
    return frontmatter, "\n".join(lines[end + 1 :])


def main() -> None:
    for skill_name in CORE_SKILLS:
        skill_dir = ROOT / "skills" / skill_name
        skill_file = skill_dir / "SKILL.md"
        frontmatter, body = parse_skill(skill_file)
        assert set(frontmatter) == {"name", "description"}, skill_file
        assert frontmatter["name"] == skill_name, skill_file
        assert frontmatter["description"], skill_file
        assert body.strip(), skill_file

        if skill_name == "sunday-note-lint":
            assert frontmatter["description"] == LINT_DESCRIPTION, skill_file

        # Verify documented resources even when the command includes arguments.
        for relative_path in re.findall(r"`((?:scripts|assets)/[^`\s]+)", body):
            if relative_path.endswith("/"):
                continue
            assert (skill_dir / relative_path).is_file(), f"missing resource: {relative_path}"

    paper_skill = ROOT / "skills" / "paper-summarizer" / "SKILL.md"
    paper_frontmatter, _ = parse_skill(paper_skill)
    assert paper_frontmatter == {
        "name": "paper-summarizer",
        "description": PAPER_DESCRIPTION,
    }, paper_skill

    print("skill packaging fixture passed")


if __name__ == "__main__":
    main()
