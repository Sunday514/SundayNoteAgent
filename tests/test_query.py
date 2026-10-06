#!/usr/bin/env python3
"""Dependency-free fixtures for read-only Query search."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / "skills/sunday-note-query/scripts/query_search.py"


def command(*args: str, path: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [sys.executable, *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def run(*args: str, path: str | None = None) -> str:
    return command(*args, path=path).stdout


def run_fail(*args: str) -> None:
    result = command(*args, check=False)
    assert result.returncode != 0, f"command unexpectedly succeeded: {args}"


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def wiki_header(
    topic: str,
    *,
    sources: str = '["fixture"]',
    keywords: str = '["fixture"]',
) -> str:
    return f"""---
last_updated: 2026-07-12
update_count: 1
sources: {sources}
topic: {topic}
keywords: {keywords}
---
"""


def candidate_rows(output: str) -> list[tuple[str, int, int, int]]:
    rows = []
    pattern = re.compile(r"^\d+\. `([^`]+)` coverage=(\d+)/\d+ signal=(\d+) score=(\d+)$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            rows.append((match.group(1), int(match.group(2)), int(match.group(3)), int(match.group(4))))
    return rows


def test_literal_search() -> None:
    with tempfile.TemporaryDirectory() as temp:
        vault = Path(temp)
        for relative in ("30_知识库", "20_每日记录", "10_原始材料", "outside"):
            (vault / relative).mkdir()

        (vault / "30_知识库/C++ 与 .NET.md").write_bytes(
            (
                wiki_header('"C++ 与 .NET"', keywords='["C++", ".NET", "符号 ["]')
                + "\nC++ 可与 .NET 配合，字面符号 [ 应保持原样。\n"
            ).replace("\n", "\r\n").encode("utf-8")
        )
        (vault / "30_知识库/完整短语.md").write_text(
            wiki_header('"World Model"', keywords='["world model"]') + "\nworld model 是完整短语。\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/拆词噪声.md").write_text(
            wiki_header('"英文拆词噪声"').replace("update_count: 1", "update_count: 1\nlast_queried: 2026-07-12\nquery_count: 8") + "\nworld 出现在这里，model 出现在另一处。\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/多词覆盖.md").write_text(
            wiki_header('"机器人控制"', keywords='["机器人", "控制"]') + "\n机器人需要可靠控制。\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/单词重复.md").write_text(
            wiki_header('"机器人重复"', keywords='["机器人"]') + ("\n机器人" * 20) + "\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/独特项目.md").write_text(wiki_header('"无正文命中"') + "\n内容。\n", encoding="utf-8")
        (vault / "30_知识库/索引.md").write_text(
            wiki_header('"索引"') + "\n机器人控制导航入口词，机器人控制机器人控制。\n",
            encoding="utf-8",
        )
        (vault / "30_知识库/知识库维护日志.md").write_text("C++ 机器人控制\n", encoding="utf-8")
        (vault / "20_每日记录/2026-07-12.md").write_text("C++ 机器人控制\n", encoding="utf-8")
        (vault / "10_原始材料/来源.md").write_text("world model\n", encoding="utf-8")
        outside = vault / "outside/越界.md"
        outside.write_text("C++ 机器人控制\n", encoding="utf-8")
        try:
            (vault / "30_知识库/越界链接.md").symlink_to(outside)
        except OSError:
            pass

        before = snapshot(vault)

        symbols = run(str(QUERY), "C++", ".NET", "[", "--vault-root", str(vault))
        symbol_rows = candidate_rows(symbols)
        assert symbol_rows[0][0] == "30_知识库/C++ 与 .NET.md"
        assert symbol_rows[0][1:3] == (3, 3)
        assert "20_每日记录" not in symbols
        assert "10_原始材料" not in symbols
        assert "知识库维护日志" not in symbols
        assert "越界链接" not in symbols

        phrase = run(str(QUERY), "world model", "--vault-root", str(vault))
        assert [row[0] for row in candidate_rows(phrase)] == ["30_知识库/完整短语.md"]

        ranked = run(str(QUERY), "机器人", "控制", "--vault-root", str(vault))
        ranked_rows = candidate_rows(ranked)
        assert ranked_rows[0][0] == "30_知识库/多词覆盖.md"
        assert ranked_rows[0][1] == 2
        assert ranked_rows[1][0] == "30_知识库/单词重复.md"
        assert ranked_rows[-1][0] == "30_知识库/索引.md"

        filename = run(str(QUERY), "独特项目", "--vault-root", str(vault))
        assert candidate_rows(filename)[0][0] == "30_知识库/独特项目.md"

        duplicate = run(str(QUERY), "C++", "c++", " C++ ", "--vault-root", str(vault))
        assert "- Terms: `c++`" in duplicate
        assert "coverage=1/1" in duplicate

        index = run(str(QUERY), "导航入口词", "--vault-root", str(vault))
        assert candidate_rows(index)[0][0] == "30_知识库/索引.md"

        missing = run(str(QUERY), "不存在的精确查询词", "--vault-root", str(vault))
        assert "Wiki coverage gap" in missing
        assert not candidate_rows(missing)

        fallback = run(str(QUERY), "机器人", "控制", "--vault-root", str(vault), path="")
        assert candidate_rows(fallback) == ranked_rows
        assert snapshot(vault) == before, "Query search must not modify the vault"


def test_fixed_layout_requires_wiki() -> None:
    with tempfile.TemporaryDirectory() as temp:
        vault = Path(temp)
        run_fail(str(QUERY), "test", "--vault-root", str(vault))


def main() -> None:
    test_literal_search()
    test_fixed_layout_requires_wiki()
    print("query fixture passed")


if __name__ == "__main__":
    main()
