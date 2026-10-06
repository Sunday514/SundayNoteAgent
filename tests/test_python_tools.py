#!/usr/bin/env python3
"""Dependency-free fixtures for layered Lint tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINT_HEADERS = ROOT / "skills/sunday-note-lint/scripts/lint_headers.py"
AUDIT = ROOT / "skills/sunday-note-lint/scripts/audit_reachability.py"


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def run(*args: str) -> str:
    return command(*args).stdout


def run_fail(*args: str) -> None:
    result = command(*args, check=False)
    assert result.returncode != 0, f"command unexpectedly succeeded: {args}"


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def wiki_header(
    topic: str,
    *,
    last_updated: str = "2026-07-12",
    update_count: str = "1",
    last_queried: str = '""',
    query_count: str = "0",
    sources: str = '["fixture"]',
    keywords: str = '["fixture"]',
) -> str:
    return f"""---
last_updated: {last_updated}
update_count: {update_count}
last_queried: {last_queried}
query_count: {query_count}
sources: {sources}
topic: {topic}
keywords: {keywords}
---
"""


def test_lint_headers() -> None:
    with tempfile.TemporaryDirectory() as temp:
        vault = Path(temp)
        wiki = vault / "30_知识库"
        wiki.mkdir()

        (wiki / "有效.md").write_text(
            wiki_header(
                '"有效主题"',
                last_updated="2020-01-01 # valid date",
                sources='["fixture"] # inline list comment',
                keywords="[user's guide] # inline list comment",
            )
            .replace('last_queried: ""\nquery_count: 0\n', '')
            + "\n本文主要介绍一个真实结论。\n",
            encoding="utf-8",
        )
        for name in ("重复一.md", "重复二.md"):
            (wiki / name).write_text(wiki_header('"重复主题"') + "\n内容。\n", encoding="utf-8")
        (wiki / "错误字段.md").write_text(
            wiki_header(
                "123",
                last_updated="2026/07/12",
                update_count="-1",
                last_queried='""',
                query_count="2",
                sources='"fixture"',
                keywords="[]",
            )
            + "\n内容。\n",
            encoding="utf-8",
        )
        (wiki / "零次错配.md").write_text(
            wiki_header('"零次错配"', last_queried="obsolete", query_count="unused") + "\n内容。\n",
            encoding="utf-8",
        )
        (wiki / "缺字段.md").write_text("---\ntopic: 缺字段\n---\n\n内容。\n", encoding="utf-8")
        (wiki / "缺header.md").write_text("# 缺 Header\n", encoding="utf-8")
        (wiki / "损坏header.md").write_text("---\ntopic: 损坏\n", encoding="utf-8")

        before = snapshot(vault)
        result = json.loads(
            run(str(LINT_HEADERS), "--root", str(vault), "--scope", "30_知识库", "--format", "json")
        )
        reports = {report["path"]: report for report in result["reports"]}
        assert result["scanned"] == 8
        assert result["issue_files"] == 6
        assert "30_知识库/有效.md" not in reports, "stale dates, body text, and topic keywords are not header errors"
        assert {issue["code"] for issue in reports["30_知识库/重复一.md"]["issues"]} == {"duplicate_topic"}
        bad_codes = {issue["code"] for issue in reports["30_知识库/错误字段.md"]["issues"]}
        assert {"bad_date", "bad_count", "bad_sources", "bad_topic", "empty_keywords"} <= bad_codes
        assert "30_知识库/零次错配.md" not in reports, "legacy query fields are ignored"
        assert any(issue["code"] == "missing_field" for issue in reports["30_知识库/缺字段.md"]["issues"])
        assert reports["30_知识库/缺header.md"]["issues"][0]["code"] == "missing_header"
        assert reports["30_知识库/损坏header.md"]["issues"][0]["code"] == "broken_header"

        limited = json.loads(
            run(
                str(LINT_HEADERS),
                "--root",
                str(vault),
                "--scope",
                "30_知识库",
                "--format",
                "json",
                "--limit",
                "1",
            )
        )
        assert limited["issue_files"] == 6 and len(limited["reports"]) == 1
        run_fail(str(LINT_HEADERS), "--root", str(vault), "--scope", str(vault.parent))
        assert snapshot(vault) == before, "header lint must not modify the vault"


def test_layered_audit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        vault = Path(temp)
        paths = [
            "30_知识库",
            "10_原始材料",
            "20_每日记录",
            "21_每周记录",
            "23_项目复盘",
            "outside",
        ]
        for raw in paths:
            (vault / raw).mkdir()
        (vault / "30_知识库/子目录").mkdir()

        (vault / "30_知识库/索引.md").write_text(
            wiki_header('"索引"')
            + "\n[[可达页]]\n[[30_知识库/同名]]\n[[30_知识库/子目录/来源]]\n[[知识库维护日志]]\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/可达页.md").write_text(
            wiki_header('"可达页"', sources='["[[10_原始材料/仅header]]"]')
            + """
[[10_原始材料/已承接\\|来源]]
[项目证据](../23_项目复盘/项目证据.md)
[[20_每日记录/2026-07-12]]
[[缺失页面]]
[[同名]]
[[外部页]]
[相对缺失](子目录/同名.md)
[[outside/私人]]
![[图片.png]]
""",
            encoding="utf-8",
        )
        (vault / "30_知识库/孤立页.md").write_text(wiki_header('"孤立页"') + "\n内容。\n", encoding="utf-8")
        (vault / "30_知识库/同名.md").write_text(wiki_header('"Wiki 同名"') + "\n内容。\n", encoding="utf-8")
        (vault / "30_知识库/目标.md").write_text(wiki_header('"相对目标"') + "\n内容。\n", encoding="utf-8")
        (vault / "30_知识库/子目录/来源.md").write_text(
            wiki_header('"相对来源"') + "\n[[../目标]]\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/知识库维护日志.md").write_text(wiki_header('"维护日志"') + "\n内容。\n", encoding="utf-8")
        for name in ("已承接.md", "仅header.md", "未承接.md", "同名.md", "目标.md"):
            (vault / "10_原始材料" / name).write_text(f"# {name}\n", encoding="utf-8")
        (vault / "20_每日记录/2026-07-12.md").write_text("# Daily\n", encoding="utf-8")
        (vault / "21_每周记录/未链接周.md").write_text("# Weekly\n", encoding="utf-8")
        (vault / "23_项目复盘/项目证据.md").write_text("# Project\n", encoding="utf-8")
        (vault / "23_项目复盘/同名.md").write_text("# 同名 Project\n", encoding="utf-8")
        (vault / "outside/私人.md").write_text("# 范围外\n", encoding="utf-8")
        (vault / "outside/外部页.md").write_text("# 同名范围外页面\n", encoding="utf-8")

        before = snapshot(vault)
        output = run(
            str(AUDIT),
            "--root",
            str(vault),
            "--wiki-entry",
            "30_知识库/索引.md",
            "--wiki-scope",
            "30_知识库",
            "--raw-scope",
            "10_原始材料",
            "--routine-scope",
            "20_每日记录",
            "--routine-scope",
            "21_每周记录",
            "--routine-scope",
            "23_项目复盘",
            "--exclude",
            "30_知识库/知识库维护日志.md",
            "--format",
            "json",
        )
        result = json.loads(output)
        assert result["scanned"] == {"wiki": 6, "raw": 5, "routine": 4}
        assert result["wiki_unreachable"] == ["30_知识库/孤立页.md"]
        assert result["raw_unlinked"] == [
            "10_原始材料/仅header.md",
            "10_原始材料/同名.md",
            "10_原始材料/未承接.md",
            "10_原始材料/目标.md",
        ]
        assert result["broken_links"] == [
            {"source": "30_知识库/可达页.md", "link": "外部页"},
            {"source": "30_知识库/可达页.md", "link": "子目录/同名.md"},
            {"source": "30_知识库/可达页.md", "link": "缺失页面"},
        ], result["broken_links"]
        assert len(result["ambiguous_links"]) == 1
        assert result["ambiguous_links"][0]["link"] == "同名"
        assert set(result["ambiguous_links"][0]["candidates"]) == {
            "10_原始材料/同名.md",
            "23_项目复盘/同名.md",
            "30_知识库/同名.md",
        }
        run_fail(
            str(AUDIT),
            "--root",
            str(vault),
            "--wiki-entry",
            "30_知识库/索引.md",
            "--wiki-scope",
            str(vault.parent),
        )
        assert snapshot(vault) == before, "reachability audit must not modify the vault"


def main() -> None:
    test_lint_headers()
    test_layered_audit()
    print("python tool fixture passed")


if __name__ == "__main__":
    main()
