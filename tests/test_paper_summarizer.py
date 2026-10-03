#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "paper-summarizer"
SCRIPTS_DIR = SKILL_DIR / "scripts"
PARSER_PATH = SCRIPTS_DIR / "docling_parser.py"
PREPARE_PATH = SCRIPTS_DIR / "prepare_paper_summary.py"
VALIDATE_PATH = SCRIPTS_DIR / "validate_summary.py"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PARSER = load_module("paper_summarizer_docling_parser", PARSER_PATH)
VALIDATOR = load_module("paper_summarizer_validate_summary", VALIDATE_PATH)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FakeDocument:
    def __init__(self, markdown: str, document_dict: dict[str, object]) -> None:
        self.markdown = markdown
        self.document_dict = document_dict

    def export_to_markdown(self) -> str:
        return self.markdown

    def export_to_dict(self) -> dict[str, object]:
        return self.document_dict


class FakeConverter:
    def __init__(self, document: FakeDocument) -> None:
        self.document = document

    def convert(self, _path: str) -> object:
        return type("ConversionResult", (), {"document": self.document})()


class ParserHealthTests(unittest.TestCase):
    def test_health_reports_only_actionable_warnings(self) -> None:
        health = PARSER.build_parse_health(
            document_dict={
                "pictures": [{}, {}],
                "tables": [],
                "texts": [
                    {"label": "formula", "text": "short"},
                    {"label": "code", "text": "text"},
                ],
            },
            pages=2,
            exported_figures=1,
            ocr=False,
        )
        self.assertEqual(health["pdf_pages"], 2)
        self.assertEqual(health["tables_detected"], 0)
        self.assertEqual(health["formulas_detected"], 1)
        self.assertEqual(health["code_items_detected"], 1)
        self.assertEqual(
            health["warnings"],
            ["low_text_density", "ocr_recommended", "figure_export_incomplete"],
        )

    def test_parse_writes_only_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_path = root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF fixture")
            output_dir = root / "parse"
            output_dir.mkdir()
            for legacy_name in (
                "run_manifest.json",
                "main_text.md",
                "figure_index.md",
            ):
                (output_dir / legacy_name).write_text("legacy", encoding="utf-8")
            document = FakeDocument(
                "A" * 500,
                {"texts": [{"text": "A" * 500}], "pictures": [], "tables": []},
            )

            def fake_export(
                _pdf_path: Path,
                _document_dict: dict[str, object],
                destination: Path,
            ) -> list[dict[str, object]]:
                (destination / "figures").mkdir(parents=True)
                return []

            with (
                mock.patch.object(PARSER, "pdf_page_count", return_value=2),
                mock.patch.object(PARSER, "export_figures", side_effect=fake_export),
            ):
                PARSER.parse_pdf(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    converter=FakeConverter(document),
                )

            self.assertTrue((output_dir / "parsed.md").is_file())
            self.assertTrue((output_dir / "parsed.json").is_file())
            self.assertTrue((output_dir / "figure_index.json").is_file())
            self.assertTrue((output_dir / "status.json").is_file())
            self.assertTrue((output_dir / "figures").is_dir())
            for legacy_name in (
                "run_manifest.json",
                "main_text.md",
                "figure_index.md",
            ):
                self.assertFalse((output_dir / legacy_name).exists())

    def test_empty_parse_result_fails_with_health_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_path = root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF fixture")
            output_dir = root / "parse"
            document = FakeDocument(
                "![page image](figures/page.png)",
                {"texts": [], "pictures": [{}], "tables": []},
            )
            with (
                mock.patch.object(PARSER, "pdf_page_count", return_value=3),
                mock.patch.object(PARSER, "export_figures", return_value=[]),
                self.assertRaisesRegex(RuntimeError, "no usable page or text"),
            ):
                PARSER.parse_pdf(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    converter=FakeConverter(document),
                )
            status = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))
            self.assertFalse(status["ok"])
            self.assertEqual(status["health"]["text_chars"], 0)

    def test_post_conversion_failure_writes_failed_parse_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_path = root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF fixture")
            output_dir = root / "parse"
            document = FakeDocument(
                "usable text",
                {"texts": [{"text": "usable text"}], "pictures": [], "tables": []},
            )
            with (
                mock.patch.object(
                    PARSER,
                    "export_figures",
                    side_effect=RuntimeError("figure export failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "parse or export"),
            ):
                PARSER.parse_pdf(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    converter=FakeConverter(document),
                )
            status = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))
            self.assertFalse(status["ok"])
            self.assertEqual(status["error_type"], "RuntimeError")


class SummaryFixture:
    excerpts = {
        "core_conclusion": "统一动作流显著提升了机器人跨任务控制能力。",
        "background": "现有机器人策略难以同时处理不同形态和不同任务。",
        "method": "该方法通过流匹配目标学习连续动作生成过程。",
        "experiments": "实验在三个基准上均超过了对应的强基线模型。",
    }

    def __init__(self, root: Path) -> None:
        self.vault = root / "vault"
        self.summary_path = self.vault / "10_原始材料" / "Flow Control.md"
        self.import_dir = self.vault / ".import_files" / "Flow Control"
        self.work_dir = self.import_dir / "_work"
        self.parse_dir = self.work_dir / "parse"
        self.summarize_dir = self.work_dir / "summarize"
        self.figures_dir = self.vault / "assets" / "figures" / "Flow Control"
        self.metadata_path = self.import_dir / "metadata.json"
        self.evidence_path = self.summarize_dir / "summary_evidence.json"
        self.status_path = self.work_dir / "status.json"
        self.validation_path = self.summarize_dir / "validation.json"
        self._write()

    def _write(self) -> None:
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.figures_dir.mkdir(parents=True, exist_ok=True)
        (self.parse_dir / "figures").mkdir(parents=True, exist_ok=True)
        (self.parse_dir / "figures" / "figure-01.png").write_bytes(b"png")
        (self.figures_dir / "figure-01.png").write_bytes(b"png")
        write_json(
            self.metadata_path,
            {
                "title": "Flow Control",
                "authors": [{"name": "Alice"}, {"name": "Bob"}],
                "published_at": "2025-01-02",
                "paper_link": "https://example.com/paper",
                "code_link": "未明确",
            },
        )
        write_json(
            self.parse_dir / "status.json",
            {
                "ok": True,
                "health": {
                    "pdf_pages": 3,
                    "text_chars": 1000,
                    "text_chars_per_page": 333.33,
                    "pictures_detected": 1,
                    "figures_exported": 1,
                    "tables_detected": 0,
                    "formulas_detected": 0,
                    "code_items_detected": 0,
                    "warnings": ["fixture_parse_warning"],
                },
            },
        )
        write_json(
            self.parse_dir / "parsed.json",
            {
                "texts": [
                    {
                        "text": (
                            self.excerpts["core_conclusion"]
                            + " "
                            + self.excerpts["background"]
                        ),
                        "prov": [{"page_no": 1}],
                    },
                    {
                        "text": self.excerpts["method"],
                        "prov": [{"page_no": 2}],
                    },
                    {
                        "text": self.excerpts["experiments"],
                        "prov": [{"page_no": 3}],
                    },
                ],
                "tables": [],
                "pictures": [],
            },
        )
        write_json(
            self.parse_dir / "figure_index.json",
            [
                {
                    "figure_id": "figure-01",
                    "page_no": 2,
                    "image_path": "figures/figure-01.png",
                    "caption": "Method overview",
                }
            ],
        )
        write_json(
            self.evidence_path,
            {
                "version": 1,
                "evidence": {
                    "core_conclusion": [
                        {"page": 1, "excerpt": self.excerpts["core_conclusion"]}
                    ],
                    "background": [{"page": 1, "excerpt": self.excerpts["background"]}],
                    "method": [
                        {
                            "page": 2,
                            "excerpt": self.excerpts["method"],
                            "figure_id": "figure-01",
                        }
                    ],
                    "experiments": [
                        {"page": 3, "excerpt": self.excerpts["experiments"]}
                    ],
                },
            },
        )
        self.summary_path.write_text(
            "\n".join(
                [
                    "# Flow Control",
                    "",
                    "- 作者：Alice、Bob",
                    "- 发布时间：2025-01-02",
                    "- 论文链接：https://example.com/paper",
                    "",
                    "> 核心结论：统一动作流提高了跨任务控制能力。",
                    "",
                    "## 1. 研究背景与动机",
                    "",
                    "现有策略缺少跨形态泛化能力。",
                    "",
                    "## 2. 技术方法",
                    "",
                    "方法学习连续动作生成过程。",
                    "",
                    "![方法总览](../assets/figures/Flow Control/figure-01.png)",
                    "",
                    "## 3. 实验结果与分析",
                    "",
                    "三个基准上的结果支持核心设计。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        write_json(
            self.status_path,
            {
                "status": "parsed",
                "step": "summarize",
                "summary_path": str(self.summary_path),
                "metadata_path": str(self.metadata_path),
                "parse_dir": str(self.parse_dir),
                "figures_dir": str(self.figures_dir),
                "sentinel": "preserve-me",
                "warnings": ["fixture_parse_warning"],
                "error": None,
            },
        )

    def run_validation(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATE_PATH),
                str(self.summary_path),
                "--work-dir",
                str(self.work_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )


class SummaryValidationTests(unittest.TestCase):
    def fixture(self, root: Path) -> SummaryFixture:
        return SummaryFixture(root)

    def test_markdown_image_parser_preserves_parenthesized_paths(self) -> None:
        self.assertEqual(
            VALIDATOR.markdown_image_targets(
                "![架构图](../assets/figures/Flow Control (VLA)/figure-01.png)"
            ),
            ["../assets/figures/Flow Control (VLA)/figure-01.png"],
        )

    def test_valid_summary_updates_status_and_preserves_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            result = fixture.run_validation()
            self.assertEqual(result.returncode, 0, result.stderr)
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            status = json.loads(fixture.status_path.read_text(encoding="utf-8"))
            self.assertTrue(validation["valid"])
            self.assertEqual(status["status"], "succeeded")
            self.assertIsNone(status["step"])
            self.assertEqual(status["sentinel"], "preserve-me")
            self.assertIn("fixture_parse_warning", status["warnings"])

    def test_wrapper_expression_is_warning_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            text = fixture.summary_path.read_text(encoding="utf-8")
            fixture.summary_path.write_text(text + "\n下面整理实验细节。\n", encoding="utf-8")
            result = fixture.run_validation()
            status = json.loads(fixture.status_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(item.startswith("wrapper_expression:") for item in status["warnings"]))

    def test_invalid_evidence_is_rejected(self) -> None:
        mutations = {
            "page": lambda data: data["evidence"]["method"][0].update(page=9),
            "excerpt": lambda data: data["evidence"]["method"][0].update(
                excerpt="这段虚构的证据没有出现在对应解析页面中。"
            ),
            "excerpt_type": lambda data: data["evidence"]["method"][0].update(
                excerpt=123456789012
            ),
            "missing": lambda data: data["evidence"].update(method=[]),
            "figure": lambda data: data["evidence"]["method"][0].update(
                figure_id="figure-99"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                fixture = self.fixture(Path(temp))
                evidence = json.loads(fixture.evidence_path.read_text(encoding="utf-8"))
                mutate(evidence)
                write_json(fixture.evidence_path, evidence)
                result = fixture.run_validation()
                validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
                status = json.loads(fixture.status_path.read_text(encoding="utf-8"))
                self.assertEqual(result.returncode, 1)
                self.assertFalse(validation["valid"])
                self.assertEqual(status["status"], "failed")
                self.assertEqual(status["step"], "validate")

    def test_cross_page_item_cannot_support_a_single_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            document = json.loads(
                (fixture.parse_dir / "parsed.json").read_text(encoding="utf-8")
            )
            document["texts"][1]["prov"] = [{"page_no": 1}, {"page_no": 2}]
            write_json(fixture.parse_dir / "parsed.json", document)
            evidence = json.loads(fixture.evidence_path.read_text(encoding="utf-8"))
            evidence["evidence"]["method"][0]["page"] = 1
            evidence["evidence"]["method"][0].pop("figure_id")
            write_json(fixture.evidence_path, evidence)
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("excerpt was not found on page 1" in error for error in validation["errors"])
            )

    def test_excerpt_cannot_join_independent_parse_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            document = json.loads(
                (fixture.parse_dir / "parsed.json").read_text(encoding="utf-8")
            )
            document["texts"][1]["text"] = "该方法通过流匹配目标学习"
            document["texts"].append(
                {
                    "text": "连续动作生成过程能够稳定生成动作。",
                    "prov": [{"page_no": 2}],
                }
            )
            write_json(fixture.parse_dir / "parsed.json", document)
            evidence = json.loads(fixture.evidence_path.read_text(encoding="utf-8"))
            evidence["evidence"]["method"][0]["excerpt"] = (
                "该方法通过流匹配目标学习 连续动作生成过程"
            )
            write_json(fixture.evidence_path, evidence)
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("excerpt was not found on page 2" in error for error in validation["errors"])
            )

    def test_malformed_evidence_is_reported_as_failed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            fixture.evidence_path.write_text("{invalid", encoding="utf-8")
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            status = json.loads(fixture.status_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("invalid JSON" in error for error in validation["errors"])
            )
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["step"], "validate")

    def test_metadata_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            text = fixture.summary_path.read_text(encoding="utf-8")
            fixture.summary_path.write_text(
                text.replace("- 发布时间：2025-01-02", "- 发布时间：2024-01-02"),
                encoding="utf-8",
            )
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("发布时间 does not match metadata" in error for error in validation["errors"])
            )

    def test_missing_and_excess_images_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            text = fixture.summary_path.read_text(encoding="utf-8")
            fixture.summary_path.write_text(
                text.replace("figure-01.png", "missing.png"),
                encoding="utf-8",
            )
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(any("does not exist" in error for error in validation["errors"]))

        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            text = fixture.summary_path.read_text(encoding="utf-8")
            images = []
            index = []
            for number in range(1, 5):
                name = f"figure-{number:02d}.png"
                (fixture.parse_dir / "figures" / name).write_bytes(b"png")
                (fixture.figures_dir / name).write_bytes(b"png")
                images.append(f"![图{number}](../assets/figures/Flow Control/{name})")
                index.append(
                    {
                        "figure_id": f"figure-{number:02d}",
                        "page_no": 2,
                        "image_path": f"figures/{name}",
                        "caption": "fixture",
                    }
                )
            write_json(fixture.parse_dir / "figure_index.json", index)
            original_image = "![方法总览](../assets/figures/Flow Control/figure-01.png)"
            fixture.summary_path.write_text(
                text.replace(original_image, "\n".join(images)),
                encoding="utf-8",
            )
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("summary references more than 3 images", validation["errors"])

    def test_summary_image_must_match_indexed_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            (fixture.figures_dir / "figure-01.png").write_bytes(b"different")
            result = fixture.run_validation()
            validation = json.loads(fixture.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("differs from indexed source" in error for error in validation["errors"])
            )

    def test_prepare_without_docling_keeps_prepared_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            vault.mkdir()
            pdf = root / "input.pdf"
            pdf.write_bytes(b"%PDF fixture")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PREPARE_PATH),
                    "--vault-root",
                    str(vault),
                    "--pdf",
                    str(pdf),
                    "--title",
                    "Fixture Paper",
                    "--no-parse",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            status_path = vault / ".import_files" / "Fixture Paper" / "_work" / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "prepared")
            self.assertEqual(status["step"], "parse")
            self.assertEqual(status["warnings"], [])
            self.assertEqual(Path(status["summary_path"]), vault / "10_原始材料/工作/论文总结/Fixture Paper.md")
            self.assertEqual(Path(status["figures_dir"]), vault / "assets/工作/figures/Fixture Paper")
            personal = subprocess.run(
                [*result.args, "--scope", "个人"], capture_output=True, text=True,
            )
            self.assertEqual(personal.returncode, 0, personal.stderr)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(Path(status["summary_path"]), vault / "10_原始材料/个人/论文总结/Fixture Paper.md")
            self.assertEqual(Path(status["figures_dir"]), vault / "assets/个人/figures/Fixture Paper")


if __name__ == "__main__":
    unittest.main()
