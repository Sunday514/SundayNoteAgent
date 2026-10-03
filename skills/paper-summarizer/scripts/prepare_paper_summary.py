#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

IMPORT_DIR = ".import_files"
RAW_DIR = "10_原始材料"
FIGURES_DIR = "assets"
UNKNOWN = "未明确"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_filename(value: str, max_len: int = 120) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"\\(?:texttt|textbf|textit|textrm|mathrm|mathbf|mathit|mathtt)\{([^{}]*)\}", r"\1", cleaned)
    cleaned = cleaned.replace("$", "")
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " - ", cleaned)
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:max_len].strip(" .-") or "paper"


def slug_from_pdf(pdf_path: Path, title: str | None, explicit_slug: str | None) -> str:
    if explicit_slug:
        return clean_filename(explicit_slug)
    if title and title.strip() and title.strip() != UNKNOWN:
        return clean_filename(title)
    return clean_filename(pdf_path.stem)


def normalize_authors(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    names = [item.strip() for item in re.split(r"[,;，；]", value) if item.strip()]
    return [{"name": name} for name in names]


def merge_metadata(args: argparse.Namespace, metadata_path: Path | None, pdf_path: Path) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if metadata_path:
        loaded = read_json(metadata_path.expanduser().resolve())
        if not isinstance(loaded, dict):
            raise ValueError("metadata JSON must be an object")
        metadata = loaded
    if args.title:
        metadata["title"] = args.title
    if args.authors:
        metadata["authors"] = normalize_authors(args.authors)
    if args.published_at:
        metadata["published_at"] = args.published_at
    if args.paper_link:
        metadata["paper_link"] = args.paper_link
    if args.code_link:
        metadata["code_link"] = args.code_link
    if not metadata.get("title"):
        metadata["title"] = pdf_path.stem
    if metadata.get("authors") is None:
        metadata["authors"] = []
    for key in ("published_at", "paper_link", "code_link"):
        if not metadata.get(key):
            metadata[key] = UNKNOWN
    metadata["pdf_path"] = "paper.pdf"
    return metadata


def copy_pdf(pdf_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.resolve() != destination.resolve():
        shutil.copy2(pdf_path, destination)


def run_docling_parse(
    *,
    pdf_path: Path,
    parse_dir: Path,
    ocr: bool,
    device: str,
    artifacts_path: Path | None,
) -> None:
    from docling_parser import parse_pdf

    parse_pdf(
        pdf_path=pdf_path,
        output_dir=parse_dir,
        ocr=ocr,
        device=device,
        artifacts_path=artifacts_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare import workspace and Raw summary target for one PDF paper."
    )
    parser.add_argument("--vault-root", type=Path, default=Path("."), help="Vault root path")
    parser.add_argument("--pdf", type=Path, required=True, help="Input paper PDF")
    parser.add_argument("--metadata", type=Path, help="Optional metadata JSON")
    parser.add_argument("--title", help="Optional paper title")
    parser.add_argument("--authors", help="Optional author names separated by comma or semicolon")
    parser.add_argument("--published-at", help="Optional publication date")
    parser.add_argument("--paper-link", help="Optional paper link")
    parser.add_argument("--code-link", help="Optional code link")
    parser.add_argument("--slug", help="Optional output directory name")
    parser.add_argument("--scope", choices=("工作", "个人"), default="工作", help="材料所属范围")
    parser.add_argument("--device", default="auto", help="Docling accelerator device")
    parser.add_argument("--ocr", action="store_true", help="Enable OCR in docling")
    parser.add_argument(
        "--artifacts-path",
        type=Path,
        default=Path.home() / ".cache" / "docling" / "models",
        help="Docling model artifacts path",
    )
    parser.add_argument("--no-parse", action="store_true", help="Prepare workspace without running docling")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vault_root = args.vault_root.expanduser().resolve()
    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not vault_root.exists():
        raise FileNotFoundError(f"vault root not found: {vault_root}")

    import_dir = Path(IMPORT_DIR)
    output_dir = Path(RAW_DIR) / args.scope / "论文总结"
    figures_root = Path(FIGURES_DIR) / args.scope / "figures"
    metadata = merge_metadata(args, args.metadata, pdf_path)
    title = str(metadata.get("title") or "")
    slug = slug_from_pdf(pdf_path, title, args.slug)
    summary_name = f"{clean_filename(title or pdf_path.stem)}.md"

    raw_dir = (vault_root / output_dir).resolve()
    import_workspace = (vault_root / import_dir / slug).resolve()
    work_dir = import_workspace / "_work"
    parse_dir = work_dir / "parse"
    summarize_dir = work_dir / "summarize"
    figure_source_dir = parse_dir / "figures"
    figures_dir = (vault_root / figures_root / slug).resolve()
    summary_path = raw_dir / summary_name
    metadata_path = import_workspace / "metadata.json"
    workspace_pdf = import_workspace / "paper.pdf"

    raw_dir.mkdir(parents=True, exist_ok=True)
    summarize_dir.mkdir(parents=True, exist_ok=True)
    copy_pdf(pdf_path, workspace_pdf)
    write_json(metadata_path, metadata)

    status: dict[str, object] = {
        "status": "prepared",
        "step": "parse",
        "updated_at": utc_now_iso(),
        "import_dir": str(import_workspace),
        "raw_dir": str(raw_dir),
        "summary_path": str(summary_path),
        "metadata_path": str(metadata_path),
        "parse_dir": str(parse_dir),
        "figure_source_dir": str(figure_source_dir),
        "figures_dir": str(figures_dir),
        "warnings": [],
        "error": None,
    }

    if not args.no_parse:
        try:
            run_docling_parse(
                pdf_path=workspace_pdf,
                parse_dir=parse_dir,
                ocr=args.ocr,
                device=args.device,
                artifacts_path=args.artifacts_path,
            )
            status["status"] = "parsed"
            status["step"] = "summarize"
            parse_status = read_json(parse_dir / "status.json")
            health = parse_status.get("health", {})
            if isinstance(health, dict) and isinstance(health.get("warnings"), list):
                status["warnings"] = health["warnings"]
            status["extracted_figures"] = (
                [path.name for path in sorted(figure_source_dir.iterdir()) if path.is_file()]
                if figure_source_dir.exists()
                else []
            )
        except Exception as exc:
            status["status"] = "failed"
            status["step"] = "parse"
            status["error"] = {
                "code": "docling_parse_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }
            write_json(work_dir / "status.json", status)
            raise

    write_json(work_dir / "status.json", status)
    print(f"import_dir={import_workspace}")
    print(f"raw_dir={raw_dir}")
    print(f"summary_path={summary_path}")
    print(f"metadata_path={metadata_path}")
    print(f"work_dir={work_dir}")
    print(f"parse_dir={parse_dir}")
    print(f"figure_source_dir={figure_source_dir}")
    print(f"figures_dir={figures_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
