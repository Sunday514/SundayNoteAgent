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
AUDIT = ROOT / "skills/sunday-note-lint/scripts/audit_reachability.py"


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, *args, "--mode", "personal"],
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
    sources: str = '["fixture"]',
) -> str:
    return f"""---
sources: {sources}
topic: {topic}
---
"""


def test_layered_audit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        vault = Path(temp)
        (vault / ".sunday-note-agent").mkdir()
        (vault / ".sunday-note-agent/install-mode").write_text("personal\n")
        paths = [
            "知识库",
            "工作资料",
            "工作记录/每日",
            "工作记录/每周",
            "工作记录/专项",
            "outside",
        ]
        for raw in paths:
            (vault / raw).mkdir(parents=True)
        (vault / "知识库/子目录").mkdir()

        (vault / "知识库/索引.md").write_text(
            wiki_header('"索引"')
            + "\n[[可达页]]\n[[知识库/同名]]\n[[知识库/子目录/来源]]\n[[知识库维护日志]]\n",
            encoding="utf-8",
        )
        (vault / "知识库/可达页.md").write_text(
            wiki_header('"可达页"', sources='["[[工作资料/仅header]]"]')
            + """
[[工作资料/已承接\\|来源]]
[项目证据](../工作记录/专项/项目证据.md)
[[工作记录/每日/2026-07-12]]
[[缺失页面]]
[[同名]]
[[外部页]]
[相对缺失](子目录/同名.md)
[[outside/私人]]
![[图片.png]]
""",
            encoding="utf-8",
        )
        (vault / "知识库/孤立页.md").write_text(wiki_header('"孤立页"') + "\n内容。\n", encoding="utf-8")
        (vault / "知识库/同名.md").write_text(wiki_header('"Wiki 同名"') + "\n内容。\n", encoding="utf-8")
        (vault / "知识库/目标.md").write_text(wiki_header('"相对目标"') + "\n内容。\n", encoding="utf-8")
        (vault / "知识库/子目录/来源.md").write_text(
            wiki_header('"相对来源"') + "\n[[../目标]]\n",
            encoding="utf-8",
        )
        (vault / "知识库/知识库维护日志.md").write_text(wiki_header('"维护日志"') + "\n内容。\n", encoding="utf-8")
        for name in ("已承接.md", "仅header.md", "未承接.md", "同名.md", "目标.md"):
            (vault / "工作资料" / name).write_text(f"# {name}\n", encoding="utf-8")
        (vault / "工作记录/每日/2026-07-12.md").write_text("# Daily\n", encoding="utf-8")
        (vault / "工作记录/每周/未链接周.md").write_text("# Weekly\n", encoding="utf-8")
        (vault / "工作记录/专项/项目证据.md").write_text("# Project\n", encoding="utf-8")
        (vault / "工作记录/专项/同名.md").write_text("# 同名 Project\n", encoding="utf-8")
        (vault / "outside/私人.md").write_text("# 范围外\n", encoding="utf-8")
        (vault / "outside/外部页.md").write_text("# 同名范围外页面\n", encoding="utf-8")

        before = snapshot(vault)
        output = run(
            str(AUDIT),
            "--root",
            str(vault),
            "--wiki-entry",
            "知识库/索引.md",
            "--wiki-scope",
            "知识库",
            "--raw-scope",
            "工作资料",
            "--routine-scope",
            "工作记录/每日",
            "--routine-scope",
            "工作记录/每周",
            "--routine-scope",
            "工作记录/专项",
            "--exclude",
            "知识库/知识库维护日志.md",
            "--format",
            "json",
        )
        result = json.loads(output)
        assert result["scanned"] == {"wiki": 6, "raw": 5, "routine": 4}
        assert result["wiki_unreachable"] == ["知识库/孤立页.md"]
        assert result["raw_unlinked"] == [
            "工作资料/仅header.md",
            "工作资料/同名.md",
            "工作资料/未承接.md",
            "工作资料/目标.md",
        ]
        assert result["broken_links"] == [
            {"source": "知识库/可达页.md", "link": "外部页"},
            {"source": "知识库/可达页.md", "link": "子目录/同名.md"},
            {"source": "知识库/可达页.md", "link": "缺失页面"},
        ], result["broken_links"]
        assert len(result["ambiguous_links"]) == 1
        assert result["ambiguous_links"][0]["link"] == "同名"
        assert set(result["ambiguous_links"][0]["candidates"]) == {
            "工作资料/同名.md",
            "工作记录/专项/同名.md",
            "知识库/同名.md",
        }
        run_fail(
            str(AUDIT),
            "--root",
            str(vault),
            "--wiki-entry",
            "知识库/索引.md",
            "--wiki-scope",
            str(vault.parent),
        )
        assert snapshot(vault) == before, "reachability audit must not modify the vault"


def main() -> None:
    test_layered_audit()
    print("python tool fixture passed")


if __name__ == "__main__":
    main()
