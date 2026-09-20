#!/usr/bin/env python3
"""Prepare and run isolated knowledge-delta diagnostics.

The CLI is development-only.  It keeps private suites and all run artifacts in
temporary directories and uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 2
ALLOWED_SOURCE_ROOTS = {
    "10_原始材料",
    "20_每日记录",
    "21_每周记录",
    "22_每月记录",
    "23_项目复盘",
    "30_知识库",
    "assets",
}
SYSTEM_FILES = ("AGENTS.md", "首页.md", "个人上下文.md")
SYSTEM_DIRS = tuple(sorted(ALLOWED_SOURCE_ROOTS))
KNOWLEDGE_SKILL_DIRS = tuple(
    f".agents/skills/{name}"
    for name in ("sunday-note-context", "sunday-note-ingest", "sunday-note-lint", "sunday-note-query")
)
CORE_SKILLS = tuple(Path(path).name for path in KNOWLEDGE_SKILL_DIRS)
QUERY_GROUPS = ("G", "O", "S")
INGEST_GROUPS = ("G", "O_before", "O_after", "S_before", "S_ingest", "S_after")
VALID_ROLES = {"target", "negative_control"}
VALID_KINDS = {"query", "ingest"}
GROUP_SAFE_AGENTS = """# 诊断工作区规则

- 只使用当前 `/workspace` 中可见的文件和原生 web search。
- 不读取或写入 `/workspace` 之外的路径，不调用额外 Skill、MCP 或 subagent。
- 直接处理用户请求；只有证据不足且无法给出可靠判断时才说明缺口。
"""
JUDGE_AGENTS = """# 匿名评审规则

- 只读取当前 `/workspace` 中的匿名评审包。
- 禁止 web、额外 Skill、MCP、subagent 和工作区外访问。
- 第一轮独立评审候选；第二轮收到 comparison map 后再做归因。
"""
SCORE_FIELDS = ("correctness", "personalization", "depth", "framework_fit", "efficiency")
COMPARISON_FIELDS = ("knowledge_potential", "system_realization", "end_to_end_advantage")
FINDINGS = ("none", "clear", "partial", "uncertain", "invalid")


BLIND_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenario_id", "candidate_reviews"],
    "properties": {
        "scenario_id": {"type": "string"},
        "candidate_reviews": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_id", "scores", "behavior_failures", "notes"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["correctness", "personalization", "depth", "framework_fit", "efficiency"],
                        "properties": {
                            name: {"type": "integer", "minimum": 0, "maximum": 4} for name in SCORE_FIELDS
                        },
                    },
                    "behavior_failures": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


COMPARISON_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenario_id", *COMPARISON_FIELDS, "primary_loss", "protocol_failures", "verdict", "evidence"],
    "properties": {
        "scenario_id": {"type": "string"},
        **{
            name: {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding", "web_confounded", "protocol_failures", "evidence"],
                "properties": {
                    "finding": {"enum": list(FINDINGS)},
                    "web_confounded": {"type": "boolean"},
                    "protocol_failures": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            }
            for name in COMPARISON_FIELDS
        },
        "primary_loss": {
            "enum": [
                "content",
                "routing",
                "retrieval",
                "source_reading",
                "synthesis",
                "personalization",
                "expression",
                "none",
                "uncertain",
            ]
        },
        "protocol_failures": {"type": "array", "items": {"type": "string"}},
        "verdict": {"enum": ["content", "mechanism", "effective", "uncertain"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}


class DiagnosticError(RuntimeError):
    """Raised for invalid input or an unsafe/incomplete diagnostic run."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"cannot read JSON {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiagnosticError(f"{label} must be a non-empty string")
    return value.strip()


def require_turns(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise DiagnosticError(f"{label} must contain 1-3 frozen user messages")
    return [require_string(item, f"{label}[{index}]") for index, item in enumerate(value)]


def safe_relative_path(value: Any, label: str, *, write_scope: bool = False) -> str:
    raw = require_string(value, label).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts:
        raise DiagnosticError(f"{label} must be a normalized relative path: {raw}")
    root = path.parts[0]
    allowed = {"30_知识库"} if write_scope else ALLOWED_SOURCE_ROOTS
    if root not in allowed:
        raise DiagnosticError(f"{label} is outside fixed vault layers: {raw}")
    return path.as_posix()


def path_list(value: Any, label: str, *, write_scope: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise DiagnosticError(f"{label} must be an array")
    result = [safe_relative_path(item, f"{label}[{index}]", write_scope=write_scope) for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise DiagnosticError(f"{label} contains duplicate paths")
    return result


def validate_suite(raw: Any, vault_root: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DiagnosticError("suite must be a JSON object")
    allowed_top = {"version", "suite_id", "web", "scenarios"}
    unknown = set(raw) - allowed_top
    if unknown:
        raise DiagnosticError(f"unknown suite keys: {sorted(unknown)}")
    if raw.get("version") != SCHEMA_VERSION:
        raise DiagnosticError(f"suite version must be {SCHEMA_VERSION}")
    suite_id = require_string(raw.get("suite_id"), "suite_id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", suite_id):
        raise DiagnosticError("suite_id must use lowercase letters, digits, and hyphens")
    if raw.get("web") != "live":
        raise DiagnosticError('web must be "live" in KDI-01')
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise DiagnosticError("scenarios must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise DiagnosticError(f"scenarios[{index}] must be an object")
        common = {"id", "role", "kind", "intent", "expected_delta"}
        kind = scenario.get("kind")
        if kind not in VALID_KINDS:
            raise DiagnosticError(f"scenarios[{index}].kind must be query or ingest")
        allowed = common | (
            {"turns", "oracle_sources"}
            if kind == "query"
            else {
                "ingest_turns",
                "reuse_turns",
                "write_authorization_turn",
                "oracle_before_sources",
                "candidate_sources",
                "write_scope",
            }
        )
        unknown = set(scenario) - allowed
        missing = allowed - set(scenario)
        if unknown or missing:
            raise DiagnosticError(
                f"scenarios[{index}] schema mismatch; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        scenario_id = require_string(scenario["id"], f"scenarios[{index}].id")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", scenario_id) or scenario_id in seen_ids:
            raise DiagnosticError(f"invalid or duplicate scenario id: {scenario_id}")
        seen_ids.add(scenario_id)
        role = scenario["role"]
        if role not in VALID_ROLES:
            raise DiagnosticError(f"{scenario_id}.role must be target or negative_control")
        item: dict[str, Any] = {
            "id": scenario_id,
            "role": role,
            "kind": kind,
            "intent": require_string(scenario["intent"], f"{scenario_id}.intent"),
            "expected_delta": require_string(scenario["expected_delta"], f"{scenario_id}.expected_delta"),
        }
        if kind == "query":
            item["turns"] = require_turns(scenario["turns"], f"{scenario_id}.turns")
            item["oracle_sources"] = path_list(scenario["oracle_sources"], f"{scenario_id}.oracle_sources")
            source_fields = ("oracle_sources",)
        else:
            item["ingest_turns"] = require_turns(scenario["ingest_turns"], f"{scenario_id}.ingest_turns")
            item["reuse_turns"] = require_turns(scenario["reuse_turns"], f"{scenario_id}.reuse_turns")
            authorization = scenario["write_authorization_turn"]
            if (
                not isinstance(authorization, int)
                or isinstance(authorization, bool)
                or not 1 <= authorization <= len(item["ingest_turns"])
            ):
                raise DiagnosticError(
                    f"{scenario_id}.write_authorization_turn must identify an ingest turn"
                )
            item["write_authorization_turn"] = authorization
            item["oracle_before_sources"] = path_list(
                scenario["oracle_before_sources"], f"{scenario_id}.oracle_before_sources"
            )
            item["candidate_sources"] = path_list(
                scenario["candidate_sources"], f"{scenario_id}.candidate_sources"
            )
            item["write_scope"] = path_list(scenario["write_scope"], f"{scenario_id}.write_scope", write_scope=True)
            if not item["write_scope"]:
                raise DiagnosticError(f"{scenario_id}.write_scope must not be empty")
            source_fields = ("oracle_before_sources", "candidate_sources")
        for field in source_fields:
            for relative in item[field]:
                resolve_source(vault_root, relative)
        normalized.append(item)
    return {"version": SCHEMA_VERSION, "suite_id": suite_id, "web": "live", "scenarios": normalized}


def resolve_source(vault_root: Path, relative: str) -> Path:
    root = vault_root.resolve(strict=True)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    if candidate.is_symlink():
        raise DiagnosticError(f"symbolic link sources are forbidden: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DiagnosticError(f"missing source: {relative}") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise DiagnosticError(f"source must be a regular file inside the vault: {relative}")
    for parent in candidate.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise DiagnosticError(f"source crosses a symbolic link: {relative}")
    return resolved


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise DiagnosticError(f"snapshot contains symbolic link: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            data = path.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()


def hash_vault_scope(vault_root: Path) -> str:
    """Hash only the fixed vault layers that KDI snapshots and protects."""
    digest = hashlib.sha256()
    for name in sorted(set(SYSTEM_FILES) | set(SYSTEM_DIRS) | set(KNOWLEDGE_SKILL_DIRS)):
        root = vault_root / name
        if not root.exists():
            continue
        if root.is_symlink():
            raise DiagnosticError(f"vault scope contains symbolic link: {root}")
        paths: Iterable[Path] = [root] if root.is_file() else root.rglob("*")
        for path in sorted(paths, key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise DiagnosticError(f"vault scope contains symbolic link: {path}")
            if path.is_file():
                relative = path.relative_to(vault_root).as_posix().encode()
                digest.update(len(relative).to_bytes(8, "big"))
                digest.update(relative)
                data = path.read_bytes()
                digest.update(len(data).to_bytes(8, "big"))
                digest.update(data)
    return digest.hexdigest()


def copy_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise DiagnosticError(f"cannot copy unsafe source: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as left, target.open("wb") as right:
            fcntl.ioctl(right.fileno(), 0x40049409, left.fileno())  # Linux FICLONE
        shutil.copystat(source, target)
    except OSError:
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)


def copy_tree_strict(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise DiagnosticError(f"cannot snapshot symbolic link: {source}")
    if source.is_file():
        copy_file(source, target)
        return
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        if child.is_symlink():
            raise DiagnosticError(f"cannot snapshot symbolic link: {child}")
        copy_tree_strict(child, target / child.name)


def snapshot_vault(vault_root: Path, snapshot: Path) -> None:
    for name in SYSTEM_FILES:
        source = vault_root / name
        if source.exists():
            copy_tree_strict(source, snapshot / name)
    for name in SYSTEM_DIRS:
        source = vault_root / name
        if source.exists():
            copy_tree_strict(source, snapshot / name)
    for relative in KNOWLEDGE_SKILL_DIRS:
        source = vault_root / relative
        if source.exists():
            copy_tree_strict(source, snapshot / relative)


def copy_selected(snapshot: Path, workspace: Path, relatives: Iterable[str]) -> None:
    for relative in sorted(set(relatives)):
        copy_file(snapshot / relative, workspace / relative)


def groups_for(scenario: dict[str, Any]) -> tuple[str, ...]:
    return QUERY_GROUPS if scenario["kind"] == "query" else INGEST_GROUPS


def turns_for(scenario: dict[str, Any], group: str) -> tuple[str, ...]:
    if scenario["kind"] == "query":
        return tuple(scenario["turns"])
    return tuple(scenario["ingest_turns"] if group == "S_ingest" else scenario["reuse_turns"])


def sources_for(scenario: dict[str, Any], group: str) -> list[str]:
    if scenario["kind"] == "query":
        return list(scenario["oracle_sources"]) if group == "O" else []
    before = list(scenario["oracle_before_sources"])
    if group == "O_before":
        return before
    if group == "O_after":
        return sorted(set(before + list(scenario["candidate_sources"])))
    return []


def ensure_run_dir(path: Path, *, resume: bool) -> Path:
    if path.is_symlink():
        raise DiagnosticError("run-dir must not be a symbolic link")
    resolved_parent = path.parent.resolve(strict=True)
    resolved = resolved_parent / path.name
    if not resolved.is_relative_to(Path("/tmp")):
        raise DiagnosticError("run-dir must be inside /tmp")
    if resume:
        if not (resolved / "control/manifest.json").is_file():
            raise DiagnosticError("resume requires a prepared run-dir")
        return resolved
    if resolved.exists() and any(resolved.iterdir()):
        raise DiagnosticError("run-dir must not exist or must be empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def cleanup_run(path: Path, confirm_suite_id: str) -> None:
    if path.is_symlink():
        raise DiagnosticError("cleanup refuses symbolic links")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or not resolved.is_relative_to(Path("/tmp")) or resolved == Path("/tmp"):
        raise DiagnosticError("cleanup only accepts a diagnostic directory below /tmp")
    manifest_path = resolved / "control/manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("format") != "knowledge-delta-run-v2":
        raise DiagnosticError("cleanup target is not a knowledge-delta run")
    suite_id = manifest.get("suite", {}).get("suite_id")
    if suite_id != confirm_suite_id:
        raise DiagnosticError("cleanup suite ID confirmation does not match")
    shutil.rmtree(resolved)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_identity(agent_source: Path) -> dict[str, Any]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=agent_source, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=agent_source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        revision, dirty = "unavailable", None
    return {"revision": revision, "dirty": dirty}


def bind_system_identity(agent_source: Path, vault_root: Path) -> dict[str, Any]:
    agent_source = agent_source.resolve(strict=True)
    if not agent_source.is_dir():
        raise DiagnosticError("agent-source must be a directory")
    skills: dict[str, str] = {}
    mismatches: list[str] = []
    for name in CORE_SKILLS:
        source = agent_source / "skills" / name
        deployed = vault_root / ".agents" / "skills" / name
        if not source.is_dir() or not deployed.is_dir():
            raise DiagnosticError(f"missing source or deployed Skill: {name}")
        source_hash = hash_tree(source)
        deployed_hash = hash_tree(deployed)
        skills[name] = source_hash
        if source_hash != deployed_hash:
            mismatches.append(name)
    scaffold = agent_source / "install" / "scaffold" / "AGENTS.md"
    deployed_agents = vault_root / "AGENTS.md"
    if not scaffold.is_file() or not deployed_agents.is_file():
        raise DiagnosticError("missing managed AGENTS.md source or deployment")
    managed_text = scaffold.read_text(encoding="utf-8")
    deployed_text = deployed_agents.read_text(encoding="utf-8")
    if not deployed_text.startswith(managed_text):
        mismatches.append("AGENTS.md managed section")
    if mismatches:
        raise DiagnosticError(f"deployed agent identity differs from agent-source: {', '.join(mismatches)}")
    personal = vault_root / "个人上下文.md"
    return {
        "git": git_identity(agent_source),
        "skills": skills,
        "managed_agents_hash": sha256_file(scaffold),
        "deployed_agents_hash": sha256_file(deployed_agents),
        "personal_context_hash": sha256_file(personal) if personal.is_file() else None,
    }


def prepare_run(
    suite_path: Path,
    vault_root: Path,
    run_dir: Path,
    agent_source: Path | None = None,
) -> dict[str, Any]:
    vault_root = vault_root.resolve(strict=True)
    if not vault_root.is_dir():
        raise DiagnosticError("vault-root must be a directory")
    suite = validate_suite(load_json(suite_path), vault_root)
    source_root = agent_source or Path(__file__).resolve().parents[1]
    system_identity = bind_system_identity(source_root, vault_root)
    before_hash = hash_vault_scope(vault_root)
    snapshot = run_dir / "snapshot"
    snapshot_vault(vault_root, snapshot)
    if hash_vault_scope(vault_root) != before_hash:
        raise DiagnosticError("vault changed while the input snapshot was being frozen")
    snapshot_hash = hash_tree(snapshot)
    if snapshot_hash != before_hash:
        raise DiagnosticError("frozen snapshot does not match the protected vault scope")
    create_attempt(run_dir, suite, snapshot, "attempt-001")
    manifest = {
        "format": "knowledge-delta-run-v2",
        "suite": suite,
        "vault_root": str(vault_root),
        "vault_hash_before": before_hash,
        "snapshot_hash": snapshot_hash,
        "system_identity": system_identity,
        "active_attempt": "attempt-001",
    }
    write_json(run_dir / "control/manifest.json", manifest)
    return manifest


def create_attempt(run_dir: Path, suite: dict[str, Any], snapshot: Path, name: str) -> Path:
    attempt = run_dir / "attempts" / name
    if attempt.exists():
        raise DiagnosticError(f"attempt already exists: {name}")
    group_map: dict[str, dict[str, str]] = {}
    for scenario in suite["scenarios"]:
        for group in groups_for(scenario):
            candidate_id = f"c-{secrets.token_hex(6)}"
            candidate = attempt / "scenarios" / scenario["id"] / "candidates" / candidate_id
            workspace = candidate / "workspace"
            if group.startswith("S"):
                copy_tree_strict(snapshot, workspace)
            else:
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / "AGENTS.md").write_text(GROUP_SAFE_AGENTS, encoding="utf-8")
                copy_selected(snapshot, workspace, sources_for(scenario, group))
            group_map[candidate_id] = {"scenario_id": scenario["id"], "group": group}
            write_json(
                candidate / "run.json",
                {
                    "candidate_id": candidate_id,
                    "turns": list(turns_for(scenario, group)),
                    "writable": group == "S_ingest",
                    "write_authorization_turn": (
                        scenario.get("write_authorization_turn") if group == "S_ingest" else None
                    ),
                },
            )
    write_json(attempt / "control/group-map.json", group_map)
    return attempt


def create_next_attempt(run_dir: Path) -> Path:
    manifest_path = run_dir / "control/manifest.json"
    manifest = load_json(manifest_path)
    numbers = []
    for path in (run_dir / "attempts").glob("attempt-*"):
        match = re.fullmatch(r"attempt-(\d+)", path.name)
        if match:
            numbers.append(int(match.group(1)))
    name = f"attempt-{max(numbers, default=0) + 1:03d}"
    attempt = create_attempt(run_dir, manifest["suite"], run_dir / "snapshot", name)
    manifest["active_attempt"] = name
    write_json(manifest_path, manifest)
    return attempt


def parse_jsonl(text: str) -> dict[str, Any]:
    thread_id: str | None = None
    final_message: str | None = None
    web_events: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DiagnosticError(f"invalid JSONL event at line {number}") from exc
        if not isinstance(event, dict):
            raise DiagnosticError(f"JSONL event at line {number} is not an object")
        if event.get("type") in {"turn.failed", "error"}:
            raise DiagnosticError(f"JSONL execution failed at line {number}")
        if event.get("type") == "thread.started":
            value = event.get("thread_id")
            if not isinstance(value, str) or not value:
                raise DiagnosticError("thread.started event is missing thread_id")
            thread_id = value
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                final_message = item["text"]
            if item.get("type") in {"web_search", "web_search_call"}:
                web_events.append(item)
        if "web" in str(event.get("type", "")).lower() and event not in web_events:
            web_events.append(event)
    if thread_id is None:
        raise DiagnosticError("JSONL output did not contain thread.started.thread_id")
    if final_message is None:
        raise DiagnosticError("JSONL output did not contain a completed agent message")
    return {"thread_id": thread_id, "answer": final_message, "web_events": web_events}


def codex_args(
    *,
    model: str,
    effort: str,
    writable: bool,
    resume_thread: str | None,
    prompt: str,
    judge_schema: str | None = None,
) -> list[str]:
    args = ["/opt/kdv/codex", "exec"]
    if resume_thread:
        args.append("resume")
    args.extend(
        [
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--skip-git-repo-check",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            "sandbox_workspace_write.network_access=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.memories=false",
            "-c",
            f'web_search="{"disabled" if judge_schema else "live"}"',
        ]
    )
    if not resume_thread:
        args.extend(["-s", "workspace-write" if writable else "read-only", "-C", "/workspace"])
    if judge_schema:
        args.extend(["--output-schema", f"/workspace/{judge_schema}"])
    if resume_thread:
        args.append(resume_thread)
    args.append(prompt)
    return args


def bwrap_args(*, workspace: Path, codex_home: Path, codex_bin: Path, command: Sequence[str]) -> list[str]:
    bundled_bwrap = codex_bin.parent.parent / "codex-resources" / "bwrap"
    if not bundled_bwrap.is_file():
        system_bwrap = shutil.which("bwrap")
        if not system_bwrap:
            raise DiagnosticError("bwrap is required")
        bundled_bwrap = Path(system_bwrap).resolve(strict=True)
    args = [
        "bwrap",
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--tmpfs",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/opt",
        "--dir",
        "/opt/kdv",
        "--dir",
        "/opt/kdv/codex-resources",
        "--ro-bind",
        str(bundled_bwrap.resolve(strict=True)),
        "/opt/kdv/codex-resources/bwrap",
        "--dir",
        "/home",
        "--dir",
        "/home/agent",
    ]
    for path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    resolv_conf = Path("/etc/resolv.conf").resolve(strict=True)
    if resolv_conf != Path("/etc/resolv.conf"):
        args.extend(
            [
                "--dir",
                "/run",
                "--dir",
                "/run/systemd",
                "--dir",
                "/run/systemd/resolve",
                "--ro-bind",
                str(resolv_conf),
                "/run/systemd/resolve/stub-resolv.conf",
            ]
        )
    else:
        args.extend(["--ro-bind", str(resolv_conf), "/etc/resolv.conf"])
    args.extend(
        [
            "--ro-bind",
            str(codex_bin),
            "/opt/kdv/codex",
            "--bind",
            str(workspace),
            "/workspace",
            "--bind",
            str(codex_home),
            "/codex-home",
            "--setenv",
            "HOME",
            "/home/agent",
            "--setenv",
            "CODEX_HOME",
            "/codex-home",
            "--setenv",
            "PATH",
            "/opt/kdv:/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--remount-ro",
            "/",
            "--chdir",
            "/workspace",
        ]
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        value = os.environ.get(name) or os.environ.get(name.lower())
        if value:
            args.extend(["--setenv", name, value])
    args.extend(command)
    return args


def find_codex_binary(value: str) -> Path:
    located = shutil.which(value) if "/" not in value else value
    if not located:
        raise DiagnosticError(f"codex executable not found: {value}")
    return Path(located).resolve(strict=True)


def basic_preflight(codex_bin: Path) -> None:
    if platform.system() != "Linux":
        raise DiagnosticError("KDI-01 supports Linux only")
    if shutil.which("bwrap") is None:
        raise DiagnosticError("bwrap is required")
    with tempfile.TemporaryDirectory(prefix="kdi-preflight-", dir="/tmp") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        home = root / "codex-home"
        workspace.mkdir()
        home.mkdir()
        host_only = root / "host-only"
        host_only.write_text("hidden", encoding="utf-8")
        check = f"test ! -e {host_only} && touch /workspace/preflight-write && ! touch /outside"
        command = bwrap_args(
            workspace=workspace,
            codex_home=home,
            codex_bin=codex_bin,
            command=["/bin/sh", "-c", check],
        )
        subprocess.run(command, check=True, capture_output=True, text=True)
        if not (workspace / "preflight-write").is_file():
            raise DiagnosticError("bwrap preflight could not write the isolated workspace")
    subprocess.run([str(codex_bin), "--version"], check=True, capture_output=True, text=True)


def session_home(candidate_dir: Path, auth_file: Path) -> Path:
    home = candidate_dir / "session-home"
    home.mkdir(mode=0o700, exist_ok=True)
    cache = auth_file.parent / "models_cache.json"
    if cache.is_file() and not cache.is_symlink():
        copy_file(cache, home / "models_cache.json")
    return home


def install_session_auth(home: Path, auth_file: Path) -> Path:
    target = home / "auth.json"
    copy_file(auth_file, target)
    target.chmod(0o400)
    return target


def run_streamed_command(command: Sequence[str], auth_path: Path) -> subprocess.CompletedProcess[str]:
    """Remove the temporary auth file as soon as Codex has created its thread."""
    stdout: list[str] = []
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                stdout.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    auth_path.unlink(missing_ok=True)
            returncode = process.wait()
        finally:
            auth_path.unlink(missing_ok=True)
        stderr_file.seek(0)
        stderr = stderr_file.read()
    return subprocess.CompletedProcess(list(command), returncode, "".join(stdout), stderr)


def security_preflight(codex_bin: Path, auth_file: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="kdi-security-", dir="/tmp") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        home = session_home(root, auth_file)
        temporary_auth = install_session_auth(home, auth_file)
        check = (
            "test ! -r /codex-home/auth.json && "
            "! touch /forbidden && "
            "/usr/bin/python3 -c 'import socket"
            "\ntry: s=socket.socket(); s.settimeout(0.2); s.connect((\"1.1.1.1\", 53)); raise SystemExit(1)"
            "\nexcept OSError: raise SystemExit(0)'"
        )
        sandbox_command = [
            "/opt/kdv/codex",
            "sandbox",
            "-P",
            ":workspace",
            "--sandbox-state-disable-network",
            "-C",
            "/workspace",
            "/bin/sh",
            "-c",
            check,
        ]
        bootstrap = (
            "test -r /codex-home/auth.json || exit 10; "
            "echo '{\"type\":\"thread.started\",\"thread_id\":\"preflight\"}'; "
            "sleep 0.05; exec \"$@\""
        )
        command = ["/bin/sh", "-c", bootstrap, "preflight", *sandbox_command]
        wrapped = bwrap_args(workspace=workspace, codex_home=home, codex_bin=codex_bin, command=command)
        result = run_streamed_command(wrapped, temporary_auth)
        if result.returncode != 0:
            raise DiagnosticError("security preflight could not protect auth, filesystem, and shell network")


def execute_candidate(
    candidate_dir: Path,
    *,
    model: str,
    effort: str,
    auth_file: Path,
    codex_bin: Path,
    dry_run: bool,
    judge: bool = False,
) -> dict[str, Any]:
    run = load_json(candidate_dir / "run.json")
    workspace = candidate_dir / "workspace"
    turns = run["turns"]
    commands: list[list[str]] = []
    if dry_run:
        fake_home = candidate_dir / "session-home"
        for index, prompt in enumerate(turns):
            command = codex_args(
                model=model,
                effort=effort,
                writable=bool(run["writable"]),
                resume_thread="<thread-id>" if index else None,
                prompt=prompt,
                judge_schema=(run.get("schemas") or [None] * len(turns))[index] if judge else None,
            )
            commands.append(bwrap_args(workspace=workspace, codex_home=fake_home, codex_bin=codex_bin, command=command))
        write_json(candidate_dir / "command-plan.json", commands)
        return {"status": "planned", "turns": len(turns)}

    home = session_home(candidate_dir, auth_file)
    started_at = time.monotonic()
    thread_id: str | None = None
    transcript: list[dict[str, Any]] = []
    turn_diffs: list[dict[str, Any]] = []
    events_dir = candidate_dir / "events"
    events_dir.mkdir(exist_ok=True)
    try:
        for index, prompt in enumerate(turns, start=1):
            if judge and index == 2:
                blind = json.loads(transcript[-1]["assistant"])
                validate_blind_result(blind, run["scenario_id"], set(run["candidate_ids"]))
                write_json(workspace / "blind-result.json", blind)
                copy_tree_strict(candidate_dir / "reveal", workspace)
            before_manifest = tree_manifest(workspace)
            temporary_auth = install_session_auth(home, auth_file)
            command = codex_args(
                model=model,
                effort=effort,
                writable=bool(run["writable"]),
                resume_thread=thread_id,
                prompt=prompt,
                judge_schema=(run.get("schemas") or [None] * len(turns))[index - 1] if judge else None,
            )
            wrapped = bwrap_args(workspace=workspace, codex_home=home, codex_bin=codex_bin, command=command)
            result = run_streamed_command(wrapped, temporary_auth)
            (events_dir / f"turn-{index:02d}.jsonl").write_text(result.stdout, encoding="utf-8")
            (events_dir / f"turn-{index:02d}.stderr").write_text(result.stderr, encoding="utf-8")
            if result.returncode != 0:
                raise DiagnosticError(f"candidate {run['candidate_id']} turn {index} failed")
            parsed = parse_jsonl(result.stdout)
            thread_id = parsed["thread_id"]
            after_manifest = tree_manifest(workspace)
            turn_diffs.append(
                {
                    "turn": index,
                    "authorized": bool(
                        run.get("write_authorization_turn")
                        and index >= run["write_authorization_turn"]
                    ),
                    **manifest_diff(before_manifest, after_manifest),
                }
            )
            transcript.append(
                {"turn": index, "user": prompt, "assistant": parsed["answer"], "web_events": parsed["web_events"]}
            )
        write_json(candidate_dir / "transcript.json", transcript)
        write_json(candidate_dir / "turn-diffs.json", turn_diffs)
        write_json(
            candidate_dir / "status.json",
            {
                "status": "completed",
                "turn_count": len(transcript),
                "web_event_count": sum(len(item["web_events"]) for item in transcript),
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            },
        )
        return {"status": "completed", "turns": len(turns)}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def active_attempt(run_dir: Path) -> Path:
    manifest = load_json(run_dir / "control/manifest.json")
    return run_dir / "attempts" / manifest["active_attempt"]


def active_group_map(run_dir: Path) -> dict[str, dict[str, str]]:
    return load_json(active_attempt(run_dir) / "control/group-map.json")


def assert_input_contract(run_dir: Path, scenario: dict[str, Any]) -> None:
    attempt = active_attempt(run_dir)
    group_map = active_group_map(run_dir)
    for candidate_id, identity in group_map.items():
        if identity["scenario_id"] != scenario["id"]:
            continue
        run = load_json(attempt / "scenarios" / scenario["id"] / "candidates" / candidate_id / "run.json")
        expected_turns = list(turns_for(scenario, identity["group"]))
        is_ingest = identity["group"] == "S_ingest"
        if run.get("turns") != expected_turns:
            raise DiagnosticError(f"candidate input contract changed: {scenario['id']} {identity['group']}")
        expected_authorization = scenario.get("write_authorization_turn") if is_ingest else None
        if run.get("write_authorization_turn") != expected_authorization:
            raise DiagnosticError(f"write authorization contract changed: {scenario['id']} {identity['group']}")


def tree_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise DiagnosticError(f"workspace contains symbolic link: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def tree_diff(before: Path, after: Path) -> dict[str, list[str]]:
    return manifest_diff(tree_manifest(before), tree_manifest(after))


def manifest_diff(left: dict[str, str], right: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(right) - set(left)),
        "removed": sorted(set(left) - set(right)),
        "modified": sorted(path for path in set(left) & set(right) if left[path] != right[path]),
    }


def content_diff(before: Path, after: Path) -> tuple[dict[str, Any], str]:
    summary = tree_diff(before, after)
    files: list[dict[str, Any]] = []
    patch_lines: list[str] = []
    for status in ("added", "removed", "modified"):
        for relative in summary[status]:
            left_path = before / relative
            right_path = after / relative
            left_bytes = left_path.read_bytes() if left_path.is_file() else b""
            right_bytes = right_path.read_bytes() if right_path.is_file() else b""
            record: dict[str, Any] = {
                "path": relative,
                "status": status,
                "before_hash": hashlib.sha256(left_bytes).hexdigest() if left_path.is_file() else None,
                "after_hash": hashlib.sha256(right_bytes).hexdigest() if right_path.is_file() else None,
                "before_bytes": len(left_bytes),
                "after_bytes": len(right_bytes),
                "text": False,
            }
            try:
                left_text = left_bytes.decode("utf-8").splitlines(keepends=True)
                right_text = right_bytes.decode("utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                pass
            else:
                record["text"] = True
                patch_lines.extend(
                    difflib.unified_diff(
                        left_text,
                        right_text,
                        fromfile=f"before/{relative}",
                        tofile=f"after/{relative}",
                    )
                )
            files.append(record)
    return {**summary, "files": files}, "".join(patch_lines)


def write_diff_evidence(before: Path, after: Path, target: Path, scopes: Sequence[str]) -> None:
    evidence, patch = content_diff(before, after)
    evidence["scope_violations"] = diff_scope_violations(evidence, scopes)
    write_json(target / "diff.json", evidence)
    (target / "diff.patch").write_text(patch, encoding="utf-8")


def diff_scope_violations(diff: dict[str, list[str]], scopes: Sequence[str]) -> list[str]:
    changed = set(diff["added"]) | set(diff["removed"]) | set(diff["modified"])
    return sorted(
        path
        for path in changed
        if not any(path == scope or path.startswith(scope.rstrip("/") + "/") for scope in scopes)
    )


def assert_parent_unchanged(run_dir: Path) -> None:
    manifest = load_json(run_dir / "control/manifest.json")
    if hash_vault_scope(Path(manifest["vault_root"])) != manifest["vault_hash_before"]:
        raise DiagnosticError("parent vault hash changed during the run")


def replace_workspace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    copy_tree_strict(source, target)


def run_candidates(
    run_dir: Path,
    *,
    model: str,
    effort: str,
    auth_file: Path,
    codex_bin: Path,
    scenario_filter: str | None,
    dry_run: bool,
    resume: bool,
) -> None:
    basic_preflight(codex_bin)
    if not dry_run:
        security_preflight(codex_bin, auth_file)
    attempt = active_attempt(run_dir)
    group_map = active_group_map(run_dir)
    manifest = load_json(run_dir / "control/manifest.json")
    scenario_ids = {item["id"] for item in manifest["suite"]["scenarios"]}
    if scenario_filter and scenario_filter not in scenario_ids:
        raise DiagnosticError(f"unknown scenario: {scenario_filter}")
    for scenario in manifest["suite"]["scenarios"]:
        if scenario_filter and scenario["id"] != scenario_filter:
            continue
        assert_input_contract(run_dir, scenario)
        by_group = {
            identity["group"]: candidate_id
            for candidate_id, identity in group_map.items()
            if identity["scenario_id"] == scenario["id"]
        }
        for group in groups_for(scenario):
            candidate_id = by_group[group]
            candidate_dir = attempt / "scenarios" / scenario["id"] / "candidates" / candidate_id
            status_path = candidate_dir / "status.json"
            if group == "S_after" and scenario["kind"] == "ingest":
                ingest_dir = attempt / "scenarios" / scenario["id"] / "candidates" / by_group["S_ingest"]
                ingest_status = ingest_dir / "status.json"
                if not dry_run and (not ingest_status.is_file() or load_json(ingest_status).get("status") != "completed"):
                    raise DiagnosticError(f"{scenario['id']} S_after requires completed S_ingest")
                if not dry_run:
                    replace_workspace(ingest_dir / "workspace", candidate_dir / "workspace")
            if resume and status_path.is_file() and load_json(status_path).get("status") == "completed":
                if group == "S_ingest" and not dry_run and not (candidate_dir / "diff.json").is_file():
                    write_diff_evidence(
                        run_dir / "snapshot", candidate_dir / "workspace", candidate_dir, scenario["write_scope"]
                    )
                continue
            execute_candidate(
                candidate_dir,
                model=model,
                effort=effort,
                auth_file=auth_file,
                codex_bin=codex_bin,
                dry_run=dry_run,
            )
            if group == "S_ingest" and not dry_run:
                write_diff_evidence(
                    run_dir / "snapshot", candidate_dir / "workspace", candidate_dir, scenario["write_scope"]
                )
                turn_diffs = load_json(candidate_dir / "turn-diffs.json")
                for item in turn_diffs:
                    item["scope_violations"] = diff_scope_violations(item, scenario["write_scope"])
                    item["unauthorized_changes"] = sorted(
                        set(item["added"] + item["removed"] + item["modified"])
                    ) if not item["authorized"] else []
                write_json(candidate_dir / "turn-diffs.json", turn_diffs)
    assert_parent_unchanged(run_dir)


def prepare_judge_package(run_dir: Path, scenario: dict[str, Any]) -> Path:
    attempt = active_attempt(run_dir)
    group_map = active_group_map(run_dir)
    package = attempt / "scenarios" / scenario["id"] / "judge/package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "AGENTS.md").write_text(JUDGE_AGENTS, encoding="utf-8")
    write_json(
        package / "intent.json",
        {
            "scenario_id": scenario["id"],
            "role": scenario["role"],
            "kind": scenario["kind"],
            "intent": scenario["intent"],
            "expected_delta": scenario["expected_delta"],
        },
    )
    source_fields = ["oracle_sources"] if scenario["kind"] == "query" else ["oracle_before_sources", "candidate_sources"]
    snapshot = run_dir / "snapshot"
    for field in source_fields:
        copy_selected(snapshot, package / "sources", scenario[field])
    for candidate_id, identity in group_map.items():
        if identity["scenario_id"] != scenario["id"]:
            continue
        source = attempt / "scenarios" / scenario["id"] / "candidates" / candidate_id
        target = package / "candidates" / candidate_id
        target.mkdir(parents=True, exist_ok=True)
        if (source / "transcript.json").is_file():
            copy_file(source / "transcript.json", target / "transcript.json")
        if (source / "status.json").is_file():
            status = load_json(source / "status.json")
            write_json(
                target / "metrics.json",
                {
                    key: status[key]
                    for key in ("status", "turn_count", "web_event_count", "elapsed_seconds")
                    if key in status
                },
            )
        trace = sanitized_tool_trace(source / "events")
        if trace:
            write_json(target / "tool-trace.json", trace)
        if identity["group"] == "S_ingest":
            for name in ("diff.json", "diff.patch", "turn-diffs.json"):
                if (source / name).is_file():
                    copy_file(source / name, target / name)
            if (source / "diff.json").is_file():
                for changed in load_json(source / "diff.json").get("files", []):
                    if not changed.get("text"):
                        continue
                    relative = changed["path"]
                    before_file = snapshot / relative
                    after_file = source / "workspace" / relative
                    if before_file.is_file():
                        copy_file(before_file, target / "changes/before" / relative)
                    if after_file.is_file():
                        copy_file(after_file, target / "changes/after" / relative)
    write_json(package / "blind-schema.json", BLIND_JUDGE_SCHEMA)
    return package


def sanitized_tool_trace(events_dir: Path) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    if not events_dir.is_dir():
        return trace
    for event_file in sorted(events_dir.glob("turn-*.jsonl")):
        turn = int(event_file.stem.split("-")[-1])
        for line in event_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") if isinstance(event, dict) else None
            if event.get("type") != "item.completed" or not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "command_execution":
                trace.append(
                    {
                        "turn": turn,
                        "type": kind,
                        "command": item.get("command", ""),
                        "output": item.get("aggregated_output", ""),
                        "exit_code": item.get("exit_code"),
                    }
                )
            elif kind in {"web_search", "web_search_call"}:
                trace.append(
                    {
                        "turn": turn,
                        "type": kind,
                        "query": item.get("query", ""),
                        "url": item.get("url", ""),
                    }
                )
    serialized = json.dumps(trace, ensure_ascii=False)
    if "thread_id" in serialized or "/tmp/" in serialized or "/home/" in serialized:
        raise DiagnosticError("tool trace contains a forbidden host or session identifier")
    return trace


def blind_judge_prompt(reverse: bool) -> str:
    order = "逆序" if reverse else "正序"
    return (
        f"按 candidate-order.json 的{order}逐个独立评分。当前工作区没有组别映射，不要猜测组别。"
        "依据冻结意图、允许来源、完整对话、正文 diff、逐轮 diff 和工具轨迹评分。"
        "无来源关键事实、错误归因、越界写入、虚假增量或知识损坏记入 behavior_failures。"
        "证据包不足只能标记未验证，不能据此断言来源虚假。live Web 是允许输入，不因使用 Web 记失败。"
        "不得以篇幅、术语数量或格式复杂度取胜。"
    )


def comparison_judge_prompt() -> str:
    return (
        "独立评分已经冻结在 blind-result.json，禁止修改。现在读取 comparison-map.json 和 input-contract.json，"
        "分别判断 O 对 G 的知识潜力、S 对 O 的系统兑现、S 对 G 的端到端优势。S_ingest 接收写入请求，"
        "其他 Ingest 候选接收独立复用追问，这是规定阶段，不是输入不一致。"
        "protocol_failures 仅记录隔离或盲态泄漏、同一比较内消息不一致、必要证据缺失；候选回答或写入失败"
        "属于 behavior_failures，不得提升为协议失败。Web 差异只有实质影响某项比较时才标记该比较。"
    )


def judge_scenarios(
    run_dir: Path,
    *,
    model: str,
    effort: str,
    auth_file: Path,
    codex_bin: Path,
    scenario_filter: str | None,
    dry_run: bool,
    tiebreak: bool,
    resume: bool,
) -> None:
    basic_preflight(codex_bin)
    if not dry_run:
        security_preflight(codex_bin, auth_file)
    manifest = load_json(run_dir / "control/manifest.json")
    scenario_ids = {item["id"] for item in manifest["suite"]["scenarios"]}
    if scenario_filter and scenario_filter not in scenario_ids:
        raise DiagnosticError(f"unknown scenario: {scenario_filter}")
    for scenario in manifest["suite"]["scenarios"]:
        if scenario_filter and scenario["id"] != scenario_filter:
            continue
        assert_input_contract(run_dir, scenario)
        package = prepare_judge_package(run_dir, scenario)
        if tiebreak:
            initial_result = package.parent / "result.json"
            if not initial_result.is_file() or load_json(initial_result).get("verdict") != "uncertain":
                raise DiagnosticError(f"{scenario['id']} tiebreak requires an uncertain initial result")
        runs = [(1, False)] if not tiebreak else [(2, False), (3, True)]
        for index, reverse in runs:
            judge_dir = package.parent / f"judge-{index:02d}"
            if judge_dir.exists():
                result_path = package.parent / f"result-{index:02d}.json"
                if resume and result_path.is_file():
                    continue
                if resume:
                    shutil.rmtree(judge_dir)
                else:
                    raise DiagnosticError(f"judge output already exists: {judge_dir}")
            copy_tree_strict(package, judge_dir / "workspace")
            group_map = active_group_map(run_dir)
            candidate_ids = sorted(
                candidate_id
                for candidate_id, identity in group_map.items()
                if identity["scenario_id"] == scenario["id"]
            )
            write_json(judge_dir / "workspace/candidate-order.json", candidate_ids)
            comparison_map = {
                candidate_id: {
                    "group": group_map[candidate_id]["group"],
                    "stage": (
                        "ingest"
                        if group_map[candidate_id]["group"] == "S_ingest"
                        else "answer"
                    ),
                    "turn_set": (
                        "ingest"
                        if group_map[candidate_id]["group"] == "S_ingest"
                        else ("query" if scenario["kind"] == "query" else "reuse")
                    ),
                }
                for candidate_id in candidate_ids
            }
            write_json(judge_dir / "reveal/comparison-map.json", comparison_map)
            write_json(
                judge_dir / "reveal/input-contract.json",
                {
                    "query_groups_share_query_turns": scenario["kind"] == "query",
                    "answer_groups_share_reuse_turns": scenario["kind"] == "ingest",
                    "ingest_stage_uses_ingest_turns": scenario["kind"] == "ingest",
                    "write_authorization_turn": scenario.get("write_authorization_turn"),
                },
            )
            write_json(judge_dir / "reveal/comparison-schema.json", COMPARISON_JUDGE_SCHEMA)
            write_json(
                judge_dir / "run.json",
                {
                    "candidate_id": f"judge-{index:02d}",
                    "scenario_id": scenario["id"],
                    "candidate_ids": candidate_ids,
                    "turns": [blind_judge_prompt(reverse), comparison_judge_prompt()],
                    "schemas": ["blind-schema.json", "comparison-schema.json"],
                    "writable": False,
                },
            )
            result = execute_candidate(
                judge_dir,
                model=model,
                effort=effort,
                auth_file=auth_file,
                codex_bin=codex_bin,
                dry_run=dry_run,
                judge=True,
            )
            if not dry_run and result["status"] == "completed":
                transcript = load_json(judge_dir / "transcript.json")
                try:
                    blind = json.loads(transcript[0]["assistant"])
                    comparison = json.loads(transcript[1]["assistant"])
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise DiagnosticError(f"judge {index} did not return JSON for {scenario['id']}") from exc
                validate_blind_result(blind, scenario["id"], set(candidate_ids))
                validate_comparison_result(comparison, scenario["id"])
                structured = {**comparison, "candidate_reviews": blind["candidate_reviews"]}
                write_json(package.parent / f"result-{index:02d}.json", structured)
                if index == 1:
                    write_json(package.parent / "result.json", structured)
        if tiebreak and not dry_run:
            resolve_tiebreak(package.parent, scenario["id"])
    assert_parent_unchanged(run_dir)


def resolve_tiebreak(judge_root: Path, scenario_id: str) -> dict[str, Any]:
    expected_ids = set(load_json(judge_root / "judge-02/reveal/comparison-map.json"))
    left = validate_judge_result(load_json(judge_root / "result-02.json"), scenario_id, expected_ids)
    right = validate_judge_result(load_json(judge_root / "result-03.json"), scenario_id, expected_ids)
    compared = (*COMPARISON_FIELDS, "primary_loss", "protocol_failures", "verdict")
    if all(left[key] == right[key] for key in compared):
        resolved = left
    else:
        resolved = dict(left)
        for field in COMPARISON_FIELDS:
            resolved[field] = {
                "finding": "uncertain",
                "web_confounded": bool(left[field]["web_confounded"] or right[field]["web_confounded"]),
                "protocol_failures": sorted(
                    set(left[field]["protocol_failures"]) | set(right[field]["protocol_failures"])
                ),
                "evidence": list(left[field]["evidence"]) + list(right[field]["evidence"]),
            }
        resolved.update({"primary_loss": "uncertain", "verdict": "uncertain"})
        resolved["protocol_failures"] = sorted(
            set(left["protocol_failures"]) | set(right["protocol_failures"])
        )
        resolved["evidence"] = list(left["evidence"]) + ["反向展示顺序改变了评审结论。"]
    write_json(judge_root / "result.json", resolved)
    return resolved


def validate_blind_result(value: Any, scenario_id: str, expected_candidate_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("scenario_id") != scenario_id:
        raise DiagnosticError(f"invalid judge result for {scenario_id}")
    if set(value) != {"scenario_id", "candidate_reviews"} or not isinstance(value["candidate_reviews"], list):
        raise DiagnosticError(f"invalid blind judge fields for {scenario_id}")
    review_ids: list[str] = []
    for review in value["candidate_reviews"]:
        if not isinstance(review, dict) or set(review) != {"candidate_id", "scores", "behavior_failures", "notes"}:
            raise DiagnosticError(f"invalid candidate review for {scenario_id}")
        candidate_id = review["candidate_id"]
        scores = review["scores"]
        if not isinstance(candidate_id, str) or not isinstance(scores, dict) or set(scores) != set(SCORE_FIELDS):
            raise DiagnosticError(f"invalid candidate identity or scores for {scenario_id}")
        if not all(isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 4 for score in scores.values()):
            raise DiagnosticError(f"candidate scores must be integers from 0 to 4 for {scenario_id}")
        if not isinstance(review["behavior_failures"], list) or not isinstance(review["notes"], list):
            raise DiagnosticError(f"invalid candidate review notes for {scenario_id}")
        if not all(isinstance(item, str) for item in review["behavior_failures"] + review["notes"]):
            raise DiagnosticError(f"candidate review notes must be strings for {scenario_id}")
        review_ids.append(candidate_id)
    if len(review_ids) != len(set(review_ids)):
        raise DiagnosticError(f"duplicate candidate reviews for {scenario_id}")
    if set(review_ids) != expected_candidate_ids:
        raise DiagnosticError(f"judge did not review every anonymous candidate for {scenario_id}")
    return value


def validate_comparison_result(value: Any, scenario_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("scenario_id") != scenario_id:
        raise DiagnosticError(f"invalid comparison result for {scenario_id}")
    if set(value) != set(COMPARISON_JUDGE_SCHEMA["required"]):
        raise DiagnosticError(f"comparison result keys do not match schema for {scenario_id}")
    for field in COMPARISON_FIELDS:
        item = value[field]
        if not isinstance(item, dict) or set(item) != {"finding", "web_confounded", "protocol_failures", "evidence"}:
            raise DiagnosticError(f"invalid {field} comparison for {scenario_id}")
        if item["finding"] not in FINDINGS or not isinstance(item["web_confounded"], bool):
            raise DiagnosticError(f"invalid {field} finding for {scenario_id}")
        if not all(isinstance(text, str) for text in item["protocol_failures"] + item["evidence"]):
            raise DiagnosticError(f"invalid {field} evidence for {scenario_id}")
    if value["verdict"] not in COMPARISON_JUDGE_SCHEMA["properties"]["verdict"]["enum"]:
        raise DiagnosticError(f"invalid verdict for {scenario_id}")
    if value["primary_loss"] not in COMPARISON_JUDGE_SCHEMA["properties"]["primary_loss"]["enum"]:
        raise DiagnosticError(f"invalid primary loss for {scenario_id}")
    if not all(isinstance(text, str) for text in value["protocol_failures"] + value["evidence"]):
        raise DiagnosticError(f"invalid protocol evidence for {scenario_id}")
    return value


def validate_judge_result(
    value: Any,
    scenario_id: str,
    expected_candidate_ids: set[str],
) -> dict[str, Any]:
    expected_keys = set(COMPARISON_JUDGE_SCHEMA["required"]) | {"candidate_reviews"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise DiagnosticError(f"invalid combined judge result for {scenario_id}")
    comparison = {key: value[key] for key in COMPARISON_JUDGE_SCHEMA["required"]}
    validate_comparison_result(comparison, scenario_id)
    validate_blind_result(
        {"scenario_id": scenario_id, "candidate_reviews": value["candidate_reviews"]},
        scenario_id,
        expected_candidate_ids,
    )
    return value


def collect_judge_results(run_dir: Path) -> list[dict[str, Any]]:
    manifest = load_json(run_dir / "control/manifest.json")
    attempt = active_attempt(run_dir)
    group_map = active_group_map(run_dir)
    results: list[dict[str, Any]] = []
    for scenario in manifest["suite"]["scenarios"]:
        result_path = attempt / "scenarios" / scenario["id"] / "judge/result.json"
        if result_path.is_file():
            expected_ids = {
                candidate_id
                for candidate_id, identity in group_map.items()
                if identity["scenario_id"] == scenario["id"]
            }
            result = validate_judge_result(load_json(result_path), scenario["id"], expected_ids)
            result = dict(result)
            result["role"] = scenario["role"]
            system_ids = {
                candidate_id
                for candidate_id, identity in group_map.items()
                if identity["scenario_id"] == scenario["id"] and identity["group"].startswith("S")
            }
            result["system_behavior_failures"] = [
                failure
                for review in result["candidate_reviews"]
                if review["candidate_id"] in system_ids
                for failure in review["behavior_failures"]
            ]
            results.append(result)
    return results


def aggregate_results(
    results: list[dict[str, Any]],
    expected_negative_controls: int | None = None,
) -> dict[str, Any]:
    observed_negative_controls = sum(item["role"] == "negative_control" for item in results)
    if expected_negative_controls is None:
        expected_negative_controls = observed_negative_controls
    negative_controls_complete = observed_negative_controls == expected_negative_controls
    negative_failure = any(
        item["role"] == "negative_control"
        and (item["protocol_failures"] or item["system_behavior_failures"])
        for item in results
    )
    comparisons = {}
    for field in COMPARISON_FIELDS:
        valid = [
            item[field] for item in results
            if item["role"] == "target" and not item["protocol_failures"]
            and item[field]["finding"] != "invalid"
            and not item[field]["protocol_failures"]
            and not item[field]["web_confounded"]
        ]
        comparisons[field] = {
            "valid_count": len(valid),
            "finding_counts": {
                finding: sum(item["finding"] == finding for item in valid)
                for finding in FINDINGS if finding != "invalid"
            },
        }
    targets = [
        item
        for item in results
        if item["role"] == "target"
        and not item["protocol_failures"]
        and all(
            comparison["finding"] != "invalid"
            and not comparison["protocol_failures"]
            and not comparison["web_confounded"]
            for comparison in (item[field] for field in COMPARISON_FIELDS)
        )
        and item["verdict"] != "uncertain"
    ]
    counts = {name: sum(item["verdict"] == name for item in targets) for name in ("content", "mechanism", "effective")}
    loss_names = (
        "content",
        "routing",
        "retrieval",
        "source_reading",
        "synthesis",
        "personalization",
        "expression",
    )
    bottleneck_counts = {name: sum(item["primary_loss"] == name for item in targets) for name in loss_names}
    recommendation = "mixed_or_uncertain"
    primary_bottleneck = "uncertain"
    if len(targets) >= 3 and negative_controls_complete and not negative_failure:
        winner = max(bottleneck_counts, key=bottleneck_counts.get)
        if bottleneck_counts[winner] > len(targets) / 2:
            primary_bottleneck = winner
            recommendation = (
                "create_content_increment_package" if winner == "content" else "create_mechanism_package"
            )
    return {
        "valid_target_count": len(targets),
        "comparisons": comparisons,
        "negative_controls_complete": negative_controls_complete,
        "negative_control_hard_failure": negative_failure,
        "verdict_counts": counts,
        "bottleneck_counts": bottleneck_counts,
        "primary_bottleneck": primary_bottleneck,
        "recommendation": recommendation,
    }


def write_report(run_dir: Path) -> dict[str, Any]:
    manifest = load_json(run_dir / "control/manifest.json")
    current_hash = hash_vault_scope(Path(manifest["vault_root"]))
    parent_unchanged = current_hash == manifest["vault_hash_before"]
    results = collect_judge_results(run_dir)
    aggregate = aggregate_results(
        results,
        expected_negative_controls=sum(
            scenario["role"] == "negative_control" for scenario in manifest["suite"]["scenarios"]
        ),
    )
    value = {
        "suite_id": manifest["suite"]["suite_id"],
        "attempt": manifest["active_attempt"],
        "system_identity": manifest["system_identity"],
        "parent_vault_unchanged": parent_unchanged,
        "scenarios": results,
        "aggregate": aggregate,
    }
    output = active_attempt(run_dir) / "report"
    write_json(output / "result.json", value)
    lines = [
        f"# Knowledge Delta Report: {value['suite_id']}",
        "",
        f"- Attempt: `{value['attempt']}`",
        f"- Parent vault unchanged: `{str(parent_unchanged).lower()}`",
        f"- Valid targets: `{aggregate['valid_target_count']}`",
        f"- Negative controls complete: `{str(aggregate['negative_controls_complete']).lower()}`",
        f"- Primary bottleneck: `{aggregate['primary_bottleneck']}`",
        f"- Recommendation: `{aggregate['recommendation']}`",
        "",
        "| Scenario | Verdict | Confounded comparisons | Protocol failures | System behavior failures |",
        "| --- | --- | --- | --- | --- |",
    ]
    by_id = {item["scenario_id"]: item for item in results}
    for scenario in manifest["suite"]["scenarios"]:
        item = by_id.get(scenario["id"])
        if item is None:
            lines.append(f"| {scenario['id']} | incomplete | - | - | - |")
        else:
            confounded = sum(item[field]["web_confounded"] for field in COMPARISON_FIELDS)
            lines.append(
                f"| {scenario['id']} | {item['verdict']} | {confounded} | {len(item['protocol_failures'])} | {len(item['system_behavior_failures'])} |"
            )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not parent_unchanged:
        raise DiagnosticError("parent vault hash changed during the run")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and run isolated knowledge-delta diagnostics")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="validate a suite and freeze isolated workspaces")
    prepare.add_argument("--suite", type=Path, required=True)
    prepare.add_argument("--vault-root", type=Path, required=True)
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--agent-source", type=Path, default=Path(__file__).resolve().parents[1])

    for name in ("run", "judge"):
        stage = subparsers.add_parser(name, help=f"{name} the prepared diagnostic")
        stage.add_argument("--run-dir", type=Path, required=True)
        stage.add_argument("--model", required=True)
        stage.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
        stage.add_argument("--auth-file", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json")
        stage.add_argument("--codex-bin", default="codex")
        stage.add_argument("--scenario")
        stage.add_argument("--dry-run", action="store_true")
        stage.add_argument("--resume", action="store_true")
        if name == "run":
            stage.add_argument("--rerun", action="store_true")
        if name == "judge":
            stage.add_argument("--tiebreak", action="store_true")

    report = subparsers.add_parser("report", help="aggregate structured judge results")
    report.add_argument("--run-dir", type=Path, required=True)

    cleanup = subparsers.add_parser("cleanup", help="delete a confirmed temporary diagnostic run")
    cleanup.add_argument("--run-dir", type=Path, required=True)
    cleanup.add_argument("--confirm-suite-id", required=True)

    all_cmd = subparsers.add_parser("all", help="prepare, run, judge, and report in order")
    all_cmd.add_argument("--suite", type=Path, required=True)
    all_cmd.add_argument("--vault-root", type=Path, required=True)
    all_cmd.add_argument("--run-dir", type=Path, required=True)
    all_cmd.add_argument("--agent-source", type=Path, default=Path(__file__).resolve().parents[1])
    all_cmd.add_argument("--model", required=True)
    all_cmd.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    all_cmd.add_argument("--auth-file", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json")
    all_cmd.add_argument("--codex-bin", default="codex")
    all_cmd.add_argument("--scenario")
    all_cmd.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            run_dir = ensure_run_dir(args.run_dir, resume=False)
            prepare_run(args.suite, args.vault_root, run_dir, args.agent_source)
            print(f"prepared: {run_dir}")
        elif args.command == "run":
            run_dir = ensure_run_dir(args.run_dir, resume=True)
            if args.rerun:
                create_next_attempt(run_dir)
            codex_bin = find_codex_binary(args.codex_bin)
            run_candidates(
                run_dir,
                model=args.model,
                effort=args.reasoning_effort,
                auth_file=args.auth_file.resolve(strict=not args.dry_run),
                codex_bin=codex_bin,
                scenario_filter=args.scenario,
                dry_run=args.dry_run,
                resume=args.resume,
            )
            print(f"run stage complete: {run_dir}")
        elif args.command == "judge":
            run_dir = ensure_run_dir(args.run_dir, resume=True)
            codex_bin = find_codex_binary(args.codex_bin)
            judge_scenarios(
                run_dir,
                model=args.model,
                effort=args.reasoning_effort,
                auth_file=args.auth_file.resolve(strict=not args.dry_run),
                codex_bin=codex_bin,
                scenario_filter=args.scenario,
                dry_run=args.dry_run,
                tiebreak=args.tiebreak,
                resume=args.resume,
            )
            print(f"judge stage complete: {run_dir}")
        elif args.command == "report":
            run_dir = ensure_run_dir(args.run_dir, resume=True)
            write_report(run_dir)
            print(f"report written: {active_attempt(run_dir) / 'report'}")
        elif args.command == "cleanup":
            target = args.run_dir
            cleanup_run(target, args.confirm_suite_id)
            print(f"cleaned: {target}")
        else:
            run_dir = ensure_run_dir(args.run_dir, resume=False)
            prepare_run(args.suite, args.vault_root, run_dir, args.agent_source)
            codex_bin = find_codex_binary(args.codex_bin)
            run_candidates(
                run_dir,
                model=args.model,
                effort=args.reasoning_effort,
                auth_file=args.auth_file.resolve(strict=not args.dry_run),
                codex_bin=codex_bin,
                scenario_filter=args.scenario,
                dry_run=args.dry_run,
                resume=False,
            )
            judge_scenarios(
                run_dir,
                model=args.model,
                effort=args.reasoning_effort,
                auth_file=args.auth_file.resolve(strict=not args.dry_run),
                codex_bin=codex_bin,
                scenario_filter=args.scenario,
                dry_run=args.dry_run,
                tiebreak=False,
                resume=False,
            )
            if not args.dry_run:
                write_report(run_dir)
            print(f"all stages complete: {run_dir}")
        return 0
    except (DiagnosticError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
