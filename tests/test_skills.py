#!/usr/bin/env python3
"""Dependency-free packaging checks for the core Sunday Note skills."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_SKILLS = (
    "sunday-note-context",
    "sunday-note-ingest",
    "sunday-note-lint",
    "sunday-note-query",
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

        if skill_name == "sunday-note-context":
            # These headings are the exported document structure.
            sections = ("兴趣与经验", "价值取舍", "证据与知识演化", "判断与协作", "表达偏好")
            assert re.findall(r"^### (.+)$", body, re.MULTILINE) == list(sections), skill_file
            template = skill_dir / "assets/个人上下文.md"
            template_body = template.read_text(encoding="utf-8")
            assert re.findall(r"^## (.+)$", template_body, re.MULTILINE) == list(sections), template
            # The installer uses this exact heading to preserve personal content.
            assert len(re.findall(r"^## 个性化响应$", body, re.MULTILINE)) == 1, skill_file
            assert "[[个人上下文]]" in body, skill_file

        # Verify documented resources even when the command includes arguments.
        for relative_path in re.findall(r"`((?:scripts|assets)/[^`\s]+)", body):
            assert (skill_dir / relative_path).is_file(), f"missing resource: {relative_path}"

    paper_skill = ROOT / "skills" / "paper-summarizer" / "SKILL.md"
    paper_frontmatter, paper_body = parse_skill(paper_skill)
    assert paper_frontmatter == {
        "name": "paper-summarizer",
        "description": PAPER_DESCRIPTION,
    }, paper_skill
    assert "summary_evidence.json" in paper_body, paper_skill
    assert "conda run -n papers python" in paper_body, paper_skill
    assert "已长期授权" in paper_body, paper_skill
    assert "无需确认" in paper_body, paper_skill
    assert "write_summary_status.py" not in paper_body, paper_skill

    print("skill packaging fixture passed")


if __name__ == "__main__":
    main()
