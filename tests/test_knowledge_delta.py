#!/usr/bin/env python3
"""Deterministic tests for the KDI-01 diagnostic harness."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/knowledge_delta"
SPEC = importlib.util.spec_from_file_location("knowledge_delta", ROOT / "validation/knowledge_delta.py")
assert SPEC and SPEC.loader
kdv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kdv
SPEC.loader.exec_module(kdv)


def text_tree(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )


def prepared_run(temp: str) -> tuple[Path, dict, dict]:
    run_dir = Path(temp) / "run"
    run_dir.mkdir()
    manifest = kdv.prepare_run(
        FIXTURE / "smoke-suite.json",
        FIXTURE / "vault",
        run_dir,
        FIXTURE / "agent-source",
    )
    group_map = kdv.active_group_map(run_dir)
    return run_dir, manifest, group_map


def candidate_dir(run_dir: Path, group_map: dict, scenario: str, group: str) -> Path:
    candidate_id = next(
        candidate_id
        for candidate_id, identity in group_map.items()
        if identity == {"scenario_id": scenario, "group": group}
    )
    return kdv.active_attempt(run_dir) / "scenarios" / scenario / "candidates" / candidate_id


def test_prepare_and_visibility() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        before = kdv.hash_vault_scope(FIXTURE / "vault")
        run_dir, manifest, group_map = prepared_run(temp)
        assert len(group_map) == 9
        assert manifest["vault_hash_before"] == before == kdv.hash_vault_scope(FIXTURE / "vault")

        query_g = candidate_dir(run_dir, group_map, "query-topic", "G") / "workspace"
        query_o = candidate_dir(run_dir, group_map, "query-topic", "O") / "workspace"
        query_s = candidate_dir(run_dir, group_map, "query-topic", "S") / "workspace"
        assert not (query_g / "30_知识库").exists()
        assert (query_o / "30_知识库/主题甲.md").is_file()
        assert not (query_o / "个人上下文.md").exists()
        assert (query_s / "个人上下文.md").is_file()
        assert (query_s / ".agents/skills/sunday-note-query/SKILL.md").is_file()
        assert not (query_s / ".agents/skills/unrelated-helper").exists()
        query_s_run = kdv.load_json(candidate_dir(run_dir, group_map, "query-topic", "S") / "run.json")
        ingest_run = kdv.load_json(candidate_dir(run_dir, group_map, "ingest-no-delta", "S_ingest") / "run.json")
        assert query_s_run["writable"] is False
        assert ingest_run["writable"] is True
        assert ingest_run["write_authorization_turn"] == 1
        for scenario in manifest["suite"]["scenarios"]:
            kdv.assert_input_contract(run_dir, scenario)

        hidden = "检验系统是否使用主题甲的稳定边界。"
        for path in kdv.active_attempt(run_dir).glob("scenarios/*/candidates/*/workspace"):
            assert hidden not in text_tree(path)
        assert not (kdv.active_attempt(run_dir) / "scenarios/query-topic/judge").exists()


def test_schema_and_path_rejections() -> None:
    suite = json.loads((FIXTURE / "smoke-suite.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        temp_path = Path(temp)
        bad = json.loads(json.dumps(suite))
        bad["scenarios"][0]["oracle_sources"] = ["../secret.md"]
        try:
            kdv.validate_suite(bad, FIXTURE / "vault")
        except kdv.DiagnosticError:
            pass
        else:
            raise AssertionError("path traversal must be rejected")

        vault = temp_path / "vault"
        kdv.copy_tree_strict(FIXTURE / "vault", vault)
        outside = temp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        link = vault / "30_知识库/link.md"
        link.symlink_to(outside)
        bad = json.loads(json.dumps(suite))
        bad["scenarios"][0]["oracle_sources"] = ["30_知识库/link.md"]
        try:
            kdv.validate_suite(bad, vault)
        except kdv.DiagnosticError:
            pass
        else:
            raise AssertionError("symbolic-link sources must be rejected")


def test_system_identity_binding() -> None:
    identity = kdv.bind_system_identity(FIXTURE / "agent-source", FIXTURE / "vault")
    assert set(identity["skills"]) == set(kdv.CORE_SKILLS)
    assert identity["personal_context_hash"]
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        vault = Path(temp) / "vault"
        kdv.copy_tree_strict(FIXTURE / "vault", vault)
        target = vault / ".agents/skills/sunday-note-query/SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\n不一致。\n", encoding="utf-8")
        try:
            kdv.bind_system_identity(FIXTURE / "agent-source", vault)
        except kdv.DiagnosticError as exc:
            assert "sunday-note-query" in str(exc)
        else:
            raise AssertionError("deployed Skill mismatch must stop preparation")


def test_command_plans_and_anonymity() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        run_dir, manifest, group_map = prepared_run(temp)
        codex_bin = Path("/usr/bin/true")
        kdv.run_candidates(
            run_dir,
            model="fixture-model",
            effort="medium",
            auth_file=Path(temp) / "missing-auth.json",
            codex_bin=codex_bin,
            scenario_filter=None,
            dry_run=True,
            resume=False,
        )
        for candidate_id, identity in group_map.items():
            path = candidate_dir(run_dir, group_map, identity["scenario_id"], identity["group"])
            commands = kdv.load_json(path / "command-plan.json")
            command_text = json.dumps(commands)
            assert "--ignore-user-config" in command_text
            assert "--ignore-rules" in command_text
            assert 'web_search=\\\"live\\\"' in command_text
            assert "sandbox_workspace_write.network_access=false" in command_text
            assert "--remount-ro" in command_text
            assert "/opt/kdv/codex" in command_text

        resume_dir = candidate_dir(run_dir, group_map, "query-topic", "O")
        (resume_dir / "command-plan.json").unlink()
        kdv.write_json(resume_dir / "status.json", {"status": "completed"})
        kdv.run_candidates(
            run_dir,
            model="fixture-model",
            effort="medium",
            auth_file=Path(temp) / "missing-auth.json",
            codex_bin=codex_bin,
            scenario_filter=None,
            dry_run=True,
            resume=True,
        )
        assert not (resume_dir / "command-plan.json").exists()

        query_g_dir = candidate_dir(run_dir, group_map, "query-topic", "G")
        kdv.write_json(query_g_dir / "transcript.json", [{"turn": 1, "user": "请求", "assistant": "回答", "web_events": []}])
        kdv.write_json(
            query_g_dir / "status.json",
            {"status": "completed", "thread_id": "private-session", "turn_count": 1, "web_event_count": 0},
        )

        kdv.judge_scenarios(
            run_dir,
            model="fixture-model",
            effort="medium",
            auth_file=Path(temp) / "missing-auth.json",
            codex_bin=codex_bin,
            scenario_filter=None,
            dry_run=True,
            tiebreak=False,
            resume=False,
        )
        package = kdv.active_attempt(run_dir) / "scenarios/query-topic/judge/package"
        package_text = text_tree(package)
        assert "query-topic" in package_text
        assert not (package / "comparison-map.json").exists()
        judge_root = package.parent / "judge-01"
        assert not (judge_root / "workspace/comparison-map.json").exists()
        assert (judge_root / "reveal/comparison-map.json").is_file()
        assert len(kdv.load_json(judge_root / "run.json")["turns"]) == 2
        for label in kdv.QUERY_GROUPS + kdv.INGEST_GROUPS:
            assert f'"group": "{label}"' not in package_text
        assert "vault_root" not in package_text
        assert "thread_id" not in package_text

def test_auth_lifecycle() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        package = Path(temp) / "workspace"
        package.mkdir()
        codex_bin = Path("/usr/bin/true")
        isolated_home = Path(temp) / "isolated-home"
        isolated_home.mkdir()
        source_auth = Path(temp) / "source-auth.json"
        source_auth.write_text("{}", encoding="utf-8")
        temporary_auth = kdv.install_session_auth(isolated_home, source_auth)
        wrapped = kdv.bwrap_args(
            workspace=package,
            codex_home=isolated_home,
            codex_bin=codex_bin,
            command=[
                "/bin/sh",
                "-c",
                "test -r /codex-home/auth.json; echo '{\"type\":\"thread.started\",\"thread_id\":\"fixture\"}'; "
                "sleep 0.05; test ! -e /codex-home/auth.json",
            ],
        )
        lifecycle = kdv.run_streamed_command(wrapped, temporary_auth)
        assert lifecycle.returncode == 0
        assert not temporary_auth.exists()

def test_rerun_preserves_old_attempt() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        run_dir, _, _ = prepared_run(temp)
        previous = kdv.active_attempt(run_dir)
        marker = previous / "preserved.txt"
        marker.write_text("原产物", encoding="utf-8")
        next_attempt = kdv.create_next_attempt(run_dir)
        assert previous.is_dir() and next_attempt.name == "attempt-002"
        assert kdv.active_attempt(run_dir) == next_attempt
        assert marker.read_text(encoding="utf-8") == "原产物"


def test_judge_evidence_package() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        run_dir, manifest, group_map = prepared_run(temp)
        source = candidate_dir(run_dir, group_map, "ingest-no-delta", "S_ingest")
        page = source / "workspace/30_知识库/主题甲.md"
        page.write_text(page.read_text(encoding="utf-8") + "\n新增测试行。\n", encoding="utf-8")
        kdv.write_diff_evidence(run_dir / "snapshot", source / "workspace", source, ["30_知识库"])
        kdv.write_json(
            source / "turn-diffs.json",
            [{"turn": 1, "authorized": True, "added": [], "removed": [], "modified": ["30_知识库/主题甲.md"]}],
        )
        kdv.write_json(source / "transcript.json", [{"turn": 1, "user": "请求", "assistant": "完成", "web_events": []}])
        kdv.write_json(source / "status.json", {"status": "completed", "turn_count": 1, "web_event_count": 0})
        events = source / "events"
        events.mkdir()
        (events / "turn-01.jsonl").write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "sed -n '1,20p' /workspace/30_知识库/主题甲.md",
                        "aggregated_output": "证据",
                        "exit_code": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        scenario = next(item for item in manifest["suite"]["scenarios"] if item["id"] == "ingest-no-delta")
        package = kdv.prepare_judge_package(run_dir, scenario)
        candidate = package / "candidates" / source.name
        assert "新增测试行" in (candidate / "diff.patch").read_text(encoding="utf-8")
        assert (candidate / "changes/before/30_知识库/主题甲.md").is_file()
        assert (candidate / "changes/after/30_知识库/主题甲.md").is_file()
        assert kdv.load_json(candidate / "tool-trace.json")[0]["output"] == "证据"
        assert not (package / "comparison-map.json").exists()


def test_jsonl_and_diff() -> None:
    events = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "web_search_call", "query": "fixture"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "answer"},
                }
            ),
        ]
    )
    parsed = kdv.parse_jsonl(events)
    assert parsed["thread_id"] == "thread-1"
    assert parsed["answer"] == "answer"
    assert len(parsed["web_events"]) == 1

    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        before = Path(temp) / "before"
        after = Path(temp) / "after"
        before.mkdir()
        after.mkdir()
        (before / "same.md").write_text("same", encoding="utf-8")
        (after / "same.md").write_text("same", encoding="utf-8")
        (before / "changed.md").write_text("old", encoding="utf-8")
        (after / "changed.md").write_text("new", encoding="utf-8")
        (after / "added.md").write_text("new", encoding="utf-8")
        assert kdv.tree_diff(before, after) == {
            "added": ["added.md"],
            "removed": [],
            "modified": ["changed.md"],
        }
        diff = {"added": ["30_知识库/new.md", "10_原始材料/raw.md"], "removed": [], "modified": []}
        assert kdv.diff_scope_violations(diff, ["30_知识库"]) == ["10_原始材料/raw.md"]

        evidence_root = Path(temp) / "evidence"
        kdv.write_diff_evidence(before, after, evidence_root, ["changed.md", "added.md"])
        evidence = kdv.load_json(evidence_root / "diff.json")
        assert evidence["files"][0]["before_hash"] or evidence["files"][0]["after_hash"]
        assert "-old" in (evidence_root / "diff.patch").read_text(encoding="utf-8")

        copied = Path(temp) / "copied.md"
        kdv.copy_file(before / "same.md", copied)
        copied.write_text("changed copy", encoding="utf-8")
        assert (before / "same.md").read_text(encoding="utf-8") == "same"

        events = Path(temp) / "events"
        events.mkdir()
        (events / "turn-01.jsonl").write_text(
            json.dumps({"type": "thread.started", "thread_id": "secret"})
            + "\n"
            + json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "sed -n '1,20p' /workspace/note.md",
                        "aggregated_output": "evidence",
                        "exit_code": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        trace = kdv.sanitized_tool_trace(events)
        assert trace[0]["output"] == "evidence"
        assert "secret" not in json.dumps(trace)


def judge_result(
    scenario_id: str,
    verdict: str,
    *,
    role: str = "target",
    web: bool = False,
    loss: str | None = None,
    protocol: list[str] | None = None,
    system_failures: list[str] | None = None,
) -> dict:
    finding = {
        "content": ("none", "none", "none"),
        "mechanism": ("clear", "none", "none"),
        "effective": ("clear", "clear", "clear"),
    }[verdict]
    comparisons = {
        field: {
            "finding": finding[index],
            "web_confounded": web,
            "protocol_failures": [],
            "evidence": ["fixture"],
        }
        for index, field in enumerate(kdv.COMPARISON_FIELDS)
    }
    return {
        "scenario_id": scenario_id,
        "role": role,
        "candidate_reviews": [],
        **comparisons,
        "primary_loss": loss or ("content" if verdict == "content" else "retrieval" if verdict == "mechanism" else "none"),
        "protocol_failures": protocol or [],
        "system_behavior_failures": system_failures or [],
        "verdict": verdict,
        "evidence": ["fixture"],
    }


def test_aggregation_and_tiebreak() -> None:
    results = [judge_result(f"target-{index}", "mechanism") for index in range(3)]
    results.append(judge_result("target-4", "content"))
    results.append(judge_result("negative", "effective", role="negative_control"))
    aggregate = kdv.aggregate_results(results)
    assert aggregate["recommendation"] == "create_mechanism_package"
    assert aggregate["primary_bottleneck"] == "retrieval"

    split = [
        judge_result("split-1", "mechanism", loss="routing"),
        judge_result("split-2", "mechanism", loss="retrieval"),
        judge_result("split-3", "mechanism", loss="source_reading"),
    ]
    assert kdv.aggregate_results(split)["recommendation"] == "mixed_or_uncertain"

    s_failed = judge_result("negative", "mechanism", role="negative_control", system_failures=["越界写入"])
    assert kdv.aggregate_results([*results[:3], s_failed])["negative_control_hard_failure"] is True

    invalid = judge_result("invalid", "effective", protocol=["输入不一致"])
    assert kdv.aggregate_results([invalid])["valid_target_count"] == 0

    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        root = Path(temp)
        left = judge_result("scenario", "content")
        right = judge_result("scenario", "mechanism")
        for item in (left, right):
            item.pop("role")
            item.pop("system_behavior_failures")
        kdv.write_json(root / "judge-02/reveal/comparison-map.json", {})
        kdv.write_json(root / "result-02.json", left)
        kdv.write_json(root / "result-03.json", right)
        resolved = kdv.resolve_tiebreak(root, "scenario")
        assert resolved["verdict"] == "uncertain"
        assert "反向展示顺序" in resolved["evidence"][-1]


def test_cli_all_dry_run() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        run_dir = Path(temp) / "run"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "validation/knowledge_delta.py"),
                "all",
                "--suite",
                str(FIXTURE / "smoke-suite.json"),
                "--vault-root",
                str(FIXTURE / "vault"),
                "--run-dir",
                str(run_dir),
                "--agent-source",
                str(FIXTURE / "agent-source"),
                "--model",
                "fixture-model",
                "--codex-bin",
                "/usr/bin/true",
                "--dry-run",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "all stages complete" in result.stdout
        assert len(list(run_dir.glob("attempts/attempt-001/scenarios/*/candidates/*/command-plan.json"))) == 9
        assert len(list(run_dir.glob("attempts/attempt-001/scenarios/*/judge/judge-01/command-plan.json"))) == 2


def test_cleanup_guard() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        root = Path(temp)
        run_dir = root / "run"
        run_dir.mkdir()
        kdv.prepare_run(
            FIXTURE / "smoke-suite.json",
            FIXTURE / "vault",
            run_dir,
            FIXTURE / "agent-source",
        )
        try:
            kdv.cleanup_run(run_dir, "wrong")
        except kdv.DiagnosticError:
            pass
        else:
            raise AssertionError("cleanup must require the exact suite ID")
        assert run_dir.is_dir()
        link = root / "run-link"
        link.symlink_to(run_dir)
        try:
            kdv.cleanup_run(link, "smoke")
        except kdv.DiagnosticError:
            pass
        else:
            raise AssertionError("cleanup must reject symbolic links")
        try:
            kdv.cleanup_run(ROOT, "smoke")
        except kdv.DiagnosticError:
            pass
        else:
            raise AssertionError("cleanup must reject paths outside /tmp")
        kdv.cleanup_run(run_dir, "smoke")
        assert not run_dir.exists()


def test_report_output() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temp:
        run_dir, _, group_map = prepared_run(temp)
        attempt = kdv.active_attempt(run_dir)
        for scenario_id in ("query-topic", "ingest-no-delta"):
            result = judge_result(scenario_id, "effective")
            result.pop("role")
            result.pop("system_behavior_failures")
            result["candidate_reviews"] = [
                {
                    "candidate_id": candidate_id,
                    "scores": {
                        "correctness": 3,
                        "personalization": 3,
                        "depth": 3,
                        "framework_fit": 3,
                        "efficiency": 3,
                    },
                    "behavior_failures": [],
                    "notes": ["fixture"],
                }
                for candidate_id, identity in group_map.items()
                if identity["scenario_id"] == scenario_id
            ]
            kdv.write_json(attempt / "scenarios" / scenario_id / "judge/result.json", result)
        report = kdv.write_report(run_dir)
        assert report["parent_vault_unchanged"] is True
        assert (attempt / "report/result.json").is_file()
        report_text = (attempt / "report/report.md").read_text(encoding="utf-8")
        assert "Recommendation" in report_text
        assert "total score" not in report_text.lower()


class ContractTests(unittest.TestCase):
    def test_multiturn_execution_records_write_timing_and_failure(self):
        for fail_second in (False, True):
            with self.subTest(fail_second=fail_second), tempfile.TemporaryDirectory(dir="/tmp") as temp:
                root = Path(temp)
                workspace = root / "workspace"
                workspace.mkdir()
                home = root / "session-home"
                home.mkdir()
                auth = root / "auth.json"
                auth.write_text("{}", encoding="utf-8")
                kdv.write_json(root / "run.json", {
                    "candidate_id": "fixture", "turns": ["先分析", "确认写入"],
                    "writable": True, "write_authorization_turn": 2,
                })
                commands = []

                def execute(command, temporary_auth):
                    commands.append(command)
                    (workspace / "note.md").write_text(str(len(commands)), encoding="utf-8")
                    events = [{"type": "thread.started", "thread_id": "test-thread"},
                              {"type": "item.completed", "item": {"type": "agent_message", "text": "完成"}}]
                    return subprocess.CompletedProcess(command, int(fail_second and len(commands) == 2),
                                                       "\n".join(map(json.dumps, events)), "")

                with patch.object(kdv, "session_home", return_value=home), \
                     patch.object(kdv, "bwrap_args", side_effect=lambda **kwargs: kwargs["command"]), \
                     patch.object(kdv, "run_streamed_command", side_effect=execute):
                    kwargs = dict(model="fixture", effort="medium", auth_file=auth,
                                  codex_bin=Path("/usr/bin/true"), dry_run=False)
                    if fail_second:
                        with self.assertRaises(kdv.DiagnosticError):
                            kdv.execute_candidate(root, **kwargs)
                        self.assertFalse((root / "status.json").exists())
                    else:
                        kdv.execute_candidate(root, **kwargs)
                        diffs = kdv.load_json(root / "turn-diffs.json")
                        self.assertFalse(diffs[0]["authorized"])
                        self.assertEqual(diffs[0]["added"], ["note.md"])
                        self.assertTrue(diffs[1]["authorized"])
                        self.assertEqual(diffs[1]["modified"], ["note.md"])
                    self.assertIn("resume", commands[1])
                    self.assertIn("test-thread", commands[1])
                    self.assertFalse(home.exists())

    def test_failure_attribution_through_group_mapping(self):
        for failed_group in kdv.INGEST_GROUPS:
            with self.subTest(group=failed_group), tempfile.TemporaryDirectory(dir="/tmp") as temp:
                run_dir, _, group_map = prepared_run(temp)
                result = judge_result("ingest-no-delta", "effective")
                result.pop("role")
                result.pop("system_behavior_failures")
                result["candidate_reviews"] = [
                    {
                        "candidate_id": candidate_id,
                        "scores": dict.fromkeys(kdv.SCORE_FIELDS, 3),
                        "behavior_failures": ["虚假来源"] if identity["group"] == failed_group else [],
                        "notes": [],
                    }
                    for candidate_id, identity in group_map.items()
                    if identity["scenario_id"] == "ingest-no-delta"
                ]
                kdv.write_json(kdv.active_attempt(run_dir) / "scenarios/ingest-no-delta/judge/result.json", result)
                collected = kdv.collect_judge_results(run_dir)
                system_failed = failed_group.startswith("S")
                self.assertEqual(bool(collected[0]["system_behavior_failures"]), system_failed)
                self.assertEqual(kdv.aggregate_results(collected)["negative_control_hard_failure"], system_failed)
                collected[0]["role"] = "target"
                self.assertEqual(kdv.aggregate_results(collected)["valid_target_count"], 1)

    def test_comparison_local_failure_preserves_other_evidence(self):
        for failure in ("web", "protocol"):
            with self.subTest(failure=failure):
                result = judge_result("target", "mechanism")
                comparison = result["knowledge_potential"]
                if failure == "web":
                    comparison["web_confounded"] = True
                else:
                    comparison["protocol_failures"] = ["缺失来源"]
                summary = kdv.aggregate_results([result])
                self.assertEqual(summary["comparisons"]["knowledge_potential"]["valid_count"], 0)
                self.assertEqual(summary["comparisons"]["system_realization"]["valid_count"], 1)
                self.assertEqual(summary["comparisons"]["end_to_end_advantage"]["valid_count"], 1)
                self.assertEqual(summary["recommendation"], "mixed_or_uncertain")

    def test_recommendation_boundaries(self):
        cases = [(2, 0, "mixed_or_uncertain"), (2, 2, "mixed_or_uncertain"),
                 (3, 1, "create_mechanism_package")]
        for mechanism, content, expected in cases:
            with self.subTest(mechanism=mechanism, content=content):
                results = [judge_result(f"m-{i}", "mechanism") for i in range(mechanism)]
                results += [judge_result(f"c-{i}", "content") for i in range(content)]
                self.assertEqual(kdv.aggregate_results(results)["recommendation"], expected)
                self.assertEqual(kdv.aggregate_results(results, expected_negative_controls=1)["recommendation"], "mixed_or_uncertain")

    def test_invalid_jsonl_is_not_a_success(self):
        started = {"type": "thread.started", "thread_id": "fixture"}
        answer = {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}
        for events in ([answer], [started], [{"type": "thread.started"}, answer],
                       [started, answer, {"type": "turn.failed", "error": {"message": "failed"}}],
                       [started, answer, {"type": "error", "message": "failed"}], [[]]):
            with self.subTest(events=events), self.assertRaises(kdv.DiagnosticError):
                kdv.parse_jsonl("\n".join(json.dumps(event) for event in events))

    def test_input_tampering_is_rejected(self):
        for scenario_id, group, key, value in (
            ("query-topic", "G", "turns", ["替换问题"]),
            ("ingest-no-delta", "S_after", "turns", ["依赖其他组的追问"]),
            ("ingest-no-delta", "S_ingest", "write_authorization_turn", 2),
        ):
            with self.subTest(group=group), tempfile.TemporaryDirectory(dir="/tmp") as temp:
                run_dir, manifest, group_map = prepared_run(temp)
                path = candidate_dir(run_dir, group_map, scenario_id, group) / "run.json"
                run = kdv.load_json(path)
                run[key] = value
                kdv.write_json(path, run)
                scenario = next(s for s in manifest["suite"]["scenarios"] if s["id"] == scenario_id)
                with self.assertRaises(kdv.DiagnosticError):
                    kdv.assert_input_contract(run_dir, scenario)

    def test_comparison_cannot_replace_frozen_reviews(self):
        result = judge_result("example", "effective")
        for field in ("role", "system_behavior_failures", "candidate_reviews"):
            result.pop(field)
        kdv.validate_comparison_result(result, "example")
        result["candidate_reviews"] = [{"scores": dict.fromkeys(kdv.SCORE_FIELDS, 4)}]
        with self.assertRaises(kdv.DiagnosticError):
            kdv.validate_comparison_result(result, "example")

    def test_reflink_failure_falls_back_to_independent_copy(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            source, target = Path(temp) / "source", Path(temp) / "target"
            source.write_text("原文", encoding="utf-8")
            with patch.object(kdv.fcntl, "ioctl", side_effect=OSError("unsupported")):
                kdv.copy_file(source, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "原文")
            target.write_text("修改", encoding="utf-8")
            self.assertEqual(source.read_text(encoding="utf-8"), "原文")


def main() -> None:
    suite = unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                               if name.startswith("test_") and callable(value))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
