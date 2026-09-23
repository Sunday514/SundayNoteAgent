#!/usr/bin/env python3
"""Monitor regression fixtures; no real vault, credentials or model calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import socket
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation" / "monitor"))
import monitor as m
import feedback
import widget_server as widget

spec = importlib.util.spec_from_file_location("installer", ROOT / "install" / "configure_monitor.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def empty():
    return {"context_updates": [], "summary": {k: [] for k in m.SUMMARY_SCHEMA["properties"]},
            "checked": [], "findings": []}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()
        self.project = self.base / "project"
        self.project.mkdir()
        self.config = {"vault": str(self.vault), "codex": "codex", "skill": str(ROOT / "skills/sunday-note-monitor/SKILL.md"), "query": "unused"}
        self.config["project_roots"] = [str(self.project)]
        self.cp = self.base / "config.json"
        m.atomic(self.cp, self.config)
        self.root = m.root_for(self.config)
        self.event = {"session_id": "s", "turn_id": "t", "cwd": str(self.project), "codex": "codex",
                      "transcript_path": str(self.base / "transcript.jsonl")}
        self.runtime = m.session_runtime(self.root, self.event)

    def work(self, evaluator):
        def batch(c, e):
            value, elapsed = evaluator(c, e)
            value["summaries"] = [{"turn_id": t["turn_id"], "summary": value["summary"]} for t in e["_turns"]]
            del value["summary"]
            return value, elapsed
        return m.worker(self.cp, self.runtime, batch, wait_seconds=0)

    def collect(self, kind, **kw):
        return m.collect(self.config, {**self.event, "hook_event_name": kind, **kw})

    def rows(self):
        return [json.loads(l) for p in self.root.glob("projects/*/sessions/*.jsonl") for l in p.read_text().splitlines()]

    def test_source_codex_ancestry(self):
        proc = self.base / "proc"
        app = self.base / "app" / "codex"
        app.parent.mkdir()
        app.touch()
        shell = self.base / "sh"
        shell.touch()
        for pid, parent, exe in ((30, 20, shell), (20, 1, app)):
            directory = proc / str(pid)
            directory.mkdir(parents=True)
            (directory / "exe").symlink_to(exe)
            (directory / "status").write_text(f"PPid:\t{parent}\n")
        self.assertEqual(m.parent_codex(proc, 30), str(app))
        self.assertEqual(m.parent_codex(proc, 99), "")

    def test_source_codex_persisted_and_used(self):
        self.config["codex"] = "/source/app/codex"
        self.collect("Stop", last_assistant_message="ok")
        event = m.read_json(next((self.runtime / "queue").glob("*.json")))
        self.assertEqual(event["codex"], "/source/app/codex")
        self.event["codex"] = event["codex"]
        item = self.finding()
        with patch.object(feedback.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as send:
            feedback.deliver({**self.config, "codex": "/wrong/codex"}, self.root, [item])
        self.assertEqual(send.call_args.args[0][0], "/source/app/codex")
        with self.assertRaisesRegex(RuntimeError, "source_codex_unavailable"):
            m.evaluate(self.config, {**event, "codex": ""})

    def test_project_scope(self):
        alias = self.base / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        for cwd in (self.project, self.project / "src", alias):
            self.assertTrue(m.project_allowed(self.config, str(cwd)))
        outside = self.project / "outside"
        outside.symlink_to(self.vault, target_is_directory=True)
        for cwd in (self.vault, outside, str(self.project) + "-other", "relative"):
            for kind in ("UserPromptSubmit", "Stop"):
                self.assertFalse(self.collect(kind, cwd=str(cwd)))
        self.assertEqual(self.rows(), [])
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))
        self.assertFalse((self.root / "state.json").exists())
        self.assertTrue(m.project_allowed({"vault": str(self.vault)}, str(self.vault)))
        self.assertFalse(m.project_allowed({"vault": str(self.vault)}, str(self.project)))
        self.config["project_roots"] = []
        self.assertFalse(self.collect("Stop"))
        self.config["project_roots"] = [str(self.project), str(self.vault)]
        self.assertTrue(m.project_allowed(self.config, str(self.vault)))

    def test_git_worktree_scope(self):
        def git(*args):
            return subprocess.run(["git", *map(str, args)], check=True, capture_output=True)
        git("init", self.project)
        git("-C", self.project, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-m", "fixture")
        linked = self.base / "linked"
        git("-C", self.project, "worktree", "add", "--detach", linked)
        (linked / "src").mkdir()
        clone = self.base / "clone"
        git("clone", self.project, clone)
        for cwd in (linked, linked / "src"):
            self.assertTrue(m.project_allowed(self.config, str(cwd)))
            self.assertEqual(m.project_dir(self.root, str(cwd)), m.project_dir(self.root, str(self.project)))
        self.assertFalse(m.project_allowed(self.config, str(clone)))
        self.assertFalse(m.project_allowed({**self.config, "project_roots": []}, str(linked)))
        self.assertTrue(m.project_allowed({**self.config, "project_roots": [str(linked)]}, str(self.project)))
        with patch.dict(os.environ, {"GIT_DIR": str(self.project / ".git")}):
            self.assertFalse(m.project_allowed(self.config, str(clone)))
        self.assertTrue(self.collect("Stop", cwd=str(linked), last_assistant_message="changed code"))
        self.runtime = m.session_runtime(self.root, {**self.event, "cwd": str(linked)})
        calls = []
        self.work(lambda c, e: (calls.append(e) or empty(), 0))
        self.assertEqual(calls[0]["cwd"], str(linked))

    def test_git_scope_probe_failure(self):
        with patch.object(m.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 0.5)):
            self.assertFalse(m.project_allowed(self.config, str(self.vault)))
            self.assertTrue(m.project_allowed(self.config, str(self.project)))

    def test_project_context_updates_only_on_change(self):
        event = {**self.event, "prompt": "目标已确认为只读检查", "created": 10}
        result = empty()
        result["context_updates"] = [{"key": "目标", "value": "只读检查", "source": {
            "location": "s/t", "quote": "目标已确认为只读检查"}}]
        m.finalize(self.root, self.config, event, result, 0)
        path = m.project_dir(self.root, str(self.project)) / "context.json"
        before = path.stat().st_mtime_ns
        m.finalize(self.root, self.config, event, result, 0)
        self.assertEqual(before, path.stat().st_mtime_ns)
        self.assertEqual(m.project_context(self.root, event)["facts"]["目标"]["value"], "只读检查")
        result["context_updates"][0]["value"] = "过时目标"
        m.finalize(self.root, self.config, {**event, "created": 9}, result, 0)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_removed_project_is_not_evaluated(self):
        self.collect("Stop", last_assistant_message="answer")
        m.atomic(self.cp, {**self.config, "project_roots": []})
        self.work(lambda c, e: self.fail("excluded project evaluated"))
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))
        self.assertFalse(any(r["kind"] == "summary" for r in self.rows()))

    def test_dedup_pair_and_raw_cleanup(self):
        self.collect("UserPromptSubmit", prompt="question")
        self.collect("Stop", last_assistant_message="answer")
        self.collect("Stop", last_assistant_message="answer")
        calls = []
        def evaluate(c, e):
            calls.append(e)
            return empty(), 1
        self.work(evaluate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prompt"], "question")
        self.assertEqual([r["kind"] for r in self.rows()], ["registered", "summary", "analysis"])
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))
        self.assertFalse(self.collect("Stop", last_assistant_message="answer"))
        self.assertNotIn('"answer"', json.dumps(self.rows()))

    def test_untraceable_events_are_not_registered(self):
        for kind in ("UserPromptSubmit", "Stop"):
            for value in (None, "", "  ", 123):
                self.assertFalse(self.collect(kind, transcript_path=value))
            payload = {**self.event, "hook_event_name": kind}
            del payload["transcript_path"]
            self.assertFalse(m.collect(self.config, payload))
        self.assertEqual(self.rows(), [])
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))
        self.assertFalse((self.root / "state.json").exists())
        self.assertTrue(self.collect("Stop", last_assistant_message="answer"))

    def test_missing_context_no_other_session_fallback(self):
        self.collect("UserPromptSubmit", prompt="other", session_id="other")
        self.collect("Stop", last_assistant_message="answer")
        self.work(lambda c,e: (empty(), 0))
        summary = next(r for r in self.rows() if r["kind"] == "summary")
        self.assertFalse(summary["context_complete"])

    def test_history_log_excludes_monitor_analysis(self):
        result = empty()
        result["summary"]["user_requests"] = ["inspect entry"]
        result["summary"]["assistant_claims"] = ["main agent reports tests passed"]
        result["summary"]["inferences"] = ["monitor suspects architecture drift"]
        result["checked"] = [{"location":"/unverified", "quote":"later inspection"}]
        self.assertEqual(m.finalize(self.root,self.config,self.event,result,1), [])
        row = self.rows()[-1]
        self.assertEqual(row["summary_scope"], "source_conversation")
        self.assertNotIn("inferences",row["summary"])
        self.assertNotIn("checked",row)
        self.assertEqual(row["summary"]["user_requests"],["inspect entry"])
        m.append_record(self.root,{**self.event,"turn_id":"old"},{"kind":"summary",
            "source":{},"summary":{"assistant_claims":["old claim"],
            "observations":["old monitor observation"],"inferences":["old speculation"],
            "open_questions":["monitor question"]}})
        context=m.recent_context(self.config,self.event)
        self.assertIn("old claim",context)
        self.assertNotIn("old monitor observation",context)
        self.assertNotIn("old speculation",context)
        self.assertNotIn("monitor question",context)

    def test_recursion_and_subagents(self):
        with patch.dict(os.environ, {"SUNDAY_MONITOR_ACTIVE": "1"}):
            self.assertFalse(self.collect("Stop"))
        self.assertFalse(self.collect("Stop", agent_id="child"))
        self.assertFalse(self.collect("SubagentStop"))

    def test_failure_waits_for_new_event(self):
        self.collect("Stop", last_assistant_message="x")
        calls = []
        def fail(c,e):
            calls.append(e)
            raise RuntimeError("quota_exhausted")
        self.work(fail)
        self.work(fail)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(list((self.runtime / "queue").glob("*.json"))), 1)
        self.collect("Stop", turn_id="next", last_assistant_message="y")
        self.work(lambda c,e: (empty(), 0))
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))

    def test_new_event_during_evaluation_not_lost(self):
        self.collect("Stop", last_assistant_message="x")
        calls = []
        def evaluate(c,e):
            if not calls:
                self.collect("UserPromptSubmit", prompt="late prompt")
            calls.append(e)
            return empty(), 0
        self.work(evaluate)
        self.assertEqual(len(calls), 2)
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))

    def test_identical_stop_during_evaluation_is_noop(self):
        self.collect("Stop", last_assistant_message="answer")
        calls = []
        def evaluate(c, e):
            calls.append(e)
            self.assertFalse(self.collect("Stop", last_assistant_message="answer"))
            return empty(), 0
        self.work(evaluate)
        self.assertEqual(len(calls), 1)

    def test_local_failure_does_not_block_other_turns(self):
        for error in (ValueError("invalid object"), RuntimeError("timeout"), RuntimeError("codex_failed")):
            with self.subTest(error=str(error)):
                bad, good = "bad-" + str(error), "good-" + str(error)
                self.collect("Stop", turn_id=bad, last_assistant_message="private raw")
                self.collect("Stop", turn_id=good, last_assistant_message="answer")
                calls = []
                def evaluate(c, e):
                    calls.append(e["turn_id"])
                    if e["turn_id"] == bad:
                        raise error
                    return empty(), 0
                self.work(evaluate)
                self.assertEqual(calls, [good])
                self.assertFalse(list((self.runtime / "queue").glob("*.json")))
                self.assertFalse(self.collect("Stop", turn_id=bad))
        self.assertNotIn("private raw", json.dumps(self.rows()))

    def test_abandoned_input_cleanup(self):
        self.collect("UserPromptSubmit", prompt="private raw")
        self.collect("UserPromptSubmit", turn_id="next", prompt="next")
        self.assertEqual([r["kind"] for r in self.rows()], ["registered", "abandoned", "registered"])
        self.assertFalse(self.collect("Stop", last_assistant_message="late"))
        with patch.object(m.time, "time", return_value=time.time() + 86401):
            self.collect("UserPromptSubmit", session_id="other", turn_id="other")
        queued = [m.read_json(p) for p in (self.runtime / "queue").glob("*.json")]
        self.assertEqual(len(queued), 1)  # Another session cannot mutate this session's queue.
        self.assertEqual(queued[0]["session_id"], "s")
        self.assertNotIn("private raw", json.dumps(self.rows()))

    def test_transcript_session_and_turn(self):
        path = self.base / "transcript.jsonl"
        rows = [
            {"type":"session_meta", "payload":{"id":"s"}},
            {"type":"turn_context", "payload":{"turn_id":"wrong"}},
            {"type":"event_msg", "payload":{"type":"user_message","message":"wrong"}},
            {"type":"turn_context", "payload":{"turn_id":"t"}},
            {"type":"event_msg", "payload":{"type":"user_message","message":"correct"}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows))
        event = {**self.event, "transcript_path": str(path)}
        self.assertEqual(m.transcript_exchange(event)["prompt"], "correct")
        self.assertNotIn("prompt", m.transcript_exchange({**event, "session_id":"other"}))
        path.write_text("invalid")
        self.assertEqual(m.transcript_exchange(event), event)

    def test_output_validation_and_evidence(self):
        value = empty()
        value["summaries"] = [{"turn_id": "t", "summary": value.pop("summary")}]
        m.validate(value, m.RESULT_SCHEMA)
        with self.assertRaises(ValueError):
            m.validate({"findings": []}, m.RESULT_SCHEMA)
        file = self.project / "README.md"
        file.write_text("old entry")
        item = {"title":"entry drift", "reason":"comparison", "instruction":"check entry",
                "options":["update document","check code"], "evidence":[{"location":str(file),"quote":"old entry"}]}
        result = empty()
        result["findings"] = [item]
        m.validate({**{k:v for k,v in result.items() if k != "summary"},
                    "summaries": [{"turn_id":"t", "summary":result["summary"]}]}, m.RESULT_SCHEMA)
        new = m.finalize(self.root, self.config, self.event, result, 1)
        self.assertEqual(len(new), 1)
        self.assertEqual(m.finalize(self.root,self.config,self.event,result,1), new)
        item["evidence"][0]["quote"] = "nonexistent"
        self.assertEqual(m.finalize(self.root,self.config,self.event,result,1), [])

    def test_feedback_dedup_uses_content_with_empty_title(self):
        file = self.project / "source.md"
        file.write_text("shared evidence")
        original = {"title": "", "reason": "background", "instruction": "check source",
                    "options": ["check", "compare"],
                    "evidence": [{"location": str(file), "quote": "shared evidence"}]}
        result = empty()
        result["findings"] = [original]
        first = m.finalize(self.root, self.config, self.event, result, 0)
        self.assertEqual(len(first), 1)
        for field, value in (("reason", "another insight"), ("instruction", "update source"),
                             ("options", ["update", "compare"])):
            with self.subTest(field=field):
                result["findings"] = [{**original, field: value}]
                ids = m.finalize(self.root, self.config, self.event, result, 0)
                self.assertEqual(len(ids), 1)
                self.assertNotEqual(ids, first)
                self.assertEqual(m.finalize(self.root, self.config, self.event, result, 0), ids)

    def test_evidence_line_ranges(self):
        file = self.project / "module.py"
        file.write_text("first\nsecond\n")
        for suffix in ("", ":1", ":1-2"):
            location = str(file) + suffix
            self.assertEqual(m.evidence_path(location), file)
            source = m.evidence_snapshot({"location": location, "quote": "first\nsecond"},
                                         self.event, str(self.vault))
            self.assertEqual(source["verification"], "quote_matched_at_finalize")

    def test_snapshot_outside_scope(self):
        file = self.base / "private.txt"
        file.write_text("secret")
        self.assertEqual(m.evidence_snapshot({"location":str(file),"quote":"secret"}, self.event,
                                            str(self.vault))["verification"], "unverified")

    def test_explicit_reference_scope_and_exact_quote(self):
        repo = self.base / "reference"
        repo.mkdir()
        file = repo / "spec.md"
        file.write_text("Use `fixed` inputs.\n")
        source = {"location":str(file), "quote":"Use `fixed` inputs."}
        self.assertEqual(m.evidence_snapshot(source,self.event,str(self.vault),[str(repo)])["verification"],
                         "quote_matched_at_finalize")
        self.assertEqual(m.evidence_snapshot({**source,"quote":"Use fixed inputs."},self.event,
                         str(self.vault),[str(repo)])["verification"],"quote_not_matched")
        secret = self.base / "outside.md"
        secret.write_text("outside")
        (repo / "link.md").symlink_to(secret)
        self.assertEqual(m.evidence_snapshot({"location":str(repo/"link.md"),"quote":"outside"},
                         self.event,str(self.vault),[str(repo)])["verification"],"unverified")

    def test_conversation_evidence_identity(self):
        event = {**self.event,"prompt":"confirm choice A","last_assistant_message":"recorded"}
        self.assertEqual(m.evidence_snapshot({"location":"s/t","quote":"choice A"},event,
                         str(self.vault))["verification"],"conversation")
        self.assertEqual(m.evidence_snapshot({"location":"s/t","quote":"choice B"},event,
                         str(self.vault))["verification"],"quote_not_matched")

    def test_policy(self):
        args = m.policy(self.base)
        text = " ".join(args)
        self.assertIn('extends=":read-only"', text)
        self.assertIn("features.hooks=false", text)
        self.assertIn("agents.enabled=false", text)
        self.assertIn('forced_login_method="chatgpt"', text)
        self.assertIn('model_reasoning_effort="xhigh"', text)
        with patch.dict(os.environ, {"OPENAI_API_KEY":"test", "ANOTHER_SECRET":"x"}):
            env = m.child_env(self.base)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANOTHER_SECRET", env)

    def test_timeout(self):
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            m.run_process([sys.executable,"-c","import time; time.sleep(5)"], timeout=.05)

    def test_proxy_and_feedback_context(self):
        env = m.child_env(self.base, {"proxy_url": "http://127.0.0.1:12345"})
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            self.assertEqual(env[name], "http://127.0.0.1:12345")
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")
        m.atomic(self.root / "findings" / "one.json", {
            "project": str(self.project), "title": "already reviewed", "status": "ignored", "created": 1})
        m.atomic(self.root / "findings" / "other.json", {
            "project": "/other", "title": "unrelated", "status": "new", "created": 2})
        context = m.recent_findings(self.config, self.event)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["status"], "ignored")

    def test_sandbox_fail_closed(self):
        with patch.object(m, "run_process", return_value=(1,"denied")):
            with self.assertRaisesRegex(RuntimeError,"sandbox_unverified"):
                m.sandbox_probe(self.config,self.base,m.child_env(self.base))

    def test_mock_cli_end_to_end(self):
        fake = self.base / "codex"
        fake.write_text('#!' + sys.executable + '\n' + '''import sys,json
from pathlib import Path
a=sys.argv[1:]
if a[:2]==['login','status']:
 print('Logged in using ChatGPT')
elif a[0]=='sandbox':
 print('MONITOR_SANDBOX_OK')
elif a[0]=='exec':
 assert '--ephemeral' in a and '--ignore-user-config' in a
 assert 'features.hooks=false' in a and 'agents.enabled=false' in a
 assert a[a.index('-m')+1]=='gpt-6-luna'
 text=sys.stdin.read()
 assert 'context_complete' in text
 Path(a[a.index('-o')+1]).write_text(json.dumps({'context_updates':[],'summaries':[{'turn_id':'t','summary':{k:[] for k in ['user_requests','user_decisions','assistant_claims','observations','inferences','open_questions']}}],'checked':[],'findings':[]}))
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':123,'output_tokens':45}}))
else: raise SystemExit(1)
''')
        fake.chmod(0o700)
        self.config["codex"] = str(fake)
        m.atomic(self.cp, self.config)
        self.collect("UserPromptSubmit",prompt="thanks")
        r = subprocess.run([sys.executable,str(ROOT/"automation/monitor/monitor.py"),"--config",str(self.cp),"hook"],
                           input=json.dumps({**self.event,"hook_event_name":"Stop","last_assistant_message":"ok"}),
                           capture_output=True,text=True,timeout=3)
        self.assertEqual(json.loads(r.stdout),{"continue":True})
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and self.rows()[-1]["kind"]!="analysis":
            time.sleep(.02)
        self.assertEqual(self.rows()[-1]["kind"],"analysis")
        self.assertEqual(self.rows()[-1]["usage"]["input_tokens"], 123)
        self.assertFalse(list((self.root/"findings").glob("*.json")))

    def finding(self):
        f = self.project / "a.md"
        f.write_text("evidence")
        result=empty()
        result["findings"]=[{"title":"new relation " + str(len(list((self.root / "findings").glob("*.json")))),"reason":"reusable",
                             "instruction":"check and ingest","options":["ingest","skip"],
                             "evidence":[{"location":str(f),"quote":"evidence"}]}]
        fid=m.finalize(self.root,self.config,self.event,result,0)[0]
        item=m.read_json(self.root/"findings"/(fid+".json"))
        return item

    def test_feedback_native_queue(self):
        item = self.finding()
        with patch.object(feedback.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as send:
            feedback.deliver(self.config, self.root, [item])
        argv = send.call_args.args[0]
        self.assertEqual(argv[:5], ["codex", "queue", "--thread", "s", "--message"])
        self.assertIn(feedback.RENDER_TOOL, argv[-1])
        self.assertIn("不是用户授权", argv[-1])
        self.assertIn(item["id"], argv[-1])
        context = json.loads(argv[-1].splitlines()[-1])["findings"][0]
        for key in ("instruction", "evidence", "options", "project", "turn_id"):
            self.assertEqual(context[key], item[key])
        self.assertIn("无需读取文件", argv[-1])
        self.assertNotIn("feedback", context)
        self.assertEqual(item["feedback"], "queued")

    def test_feedback_failure_and_scope(self):
        item = self.finding()
        with patch.object(feedback.subprocess, "run") as send:
            feedback.deliver({**self.config, "project_roots": []}, self.root, [item])
        send.assert_not_called()
        self.assertEqual(item["feedback"], "skipped_scope")
        self.assertTrue(any(r["kind"] == "summary" for r in self.rows()))

    def test_feedback_queue_failure_and_complete_message(self):
        item = self.finding()
        item.update(title="标题" * 1000, reason="原因" * 1000,
                    instruction="建议" * 1000, options=["选项" * 1000] * 3)
        context = json.loads(feedback.message([item]).splitlines()[-1])["findings"][0]
        self.assertEqual(context["reason"], item["reason"])
        self.assertEqual(context["options"], item["options"])
        for failure in (subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired("codex", 20)):
            with patch.object(feedback.subprocess, "run") as send:
                if isinstance(failure, Exception):
                    send.side_effect = failure
                else:
                    send.return_value = failure
                feedback.deliver(self.config, self.root, [item])
            self.assertEqual(item["feedback"], "failed")
            argv = send.call_args.args[0]
            self.assertNotIn("--remote", argv)
            self.assertNotIn("--model", argv)

    def test_widget_render_and_scope(self):
        item = self.finding()
        args = {"session_id": "s", "finding_ids": [item["id"]]}
        path = self.root / "findings" / (item["id"] + ".json")
        before = path.read_bytes()
        result = widget.call(self.config, feedback.RENDER_TOOL, args)
        self.assertEqual(len(result["structuredContent"]["items"]), 1)
        self.assertEqual(path.read_bytes(), before)
        item.update(reason="", options=[])
        m.atomic(path, item)
        view = widget.call(self.config, feedback.RENDER_TOOL, args)["structuredContent"]["items"][0]
        self.assertEqual(view["summary"], item["instruction"])
        self.assertEqual(view["options"], [])
        for invalid in ({**args, "session_id": "other"}, {**args, "finding_ids": ["../config"]}):
            with self.assertRaises(ValueError):
                widget.call(self.config, feedback.RENDER_TOOL, invalid)
        with self.assertRaises(ValueError):
            widget.call({**self.config, "project_roots": []}, feedback.RENDER_TOOL, args)

    def test_one_feedback_and_reference_only_acknowledgment(self):
        item = self.finding()
        result = empty()
        source = {k: item[k] for k in m.RESULT_SCHEMA["properties"]["findings"]["items"]["properties"]}
        source["evidence"] = [{k: e[k] for k in ("location", "quote")} for e in item["evidence"]]
        result["findings"] = [source, source]
        with self.assertRaises(ValueError):
            m.validate(result, m.RESULT_SCHEMA)
        with self.assertRaises(ValueError):
            widget.call(self.config, feedback.RENDER_TOOL,
                        {"session_id": "s", "finding_ids": [item["id"], item["id"]]})
        source.update(title="", instruction="", options=[])
        result["findings"] = [source]
        m.validate({**{k:v for k,v in result.items() if k != "summary"},
                    "summaries": [{"turn_id":"t", "summary":result["summary"]}]}, m.RESULT_SCHEMA)
        fid = m.finalize(self.root, self.config, self.event, result, 0)[0]
        with patch.object(widget, "queue") as send:
            widget.call(self.config, widget.ACTION_TOOL,
                        {"session_id": "s", "finding_id": fid, "action": "confirm"})
        send.assert_not_called()
        self.assertEqual(m.read_json(self.root / "findings" / (fid + ".json"))["status"], "acknowledged")

    def test_widget_decisions(self):
        for action in ("confirm", "ignore"):
            item = self.finding()
            args = {"session_id": "s", "finding_id": item["id"], "action": action}
            if action == "confirm":
                with self.assertRaises(ValueError):
                    widget.call(self.config, widget.ACTION_TOOL, args)
                args["selection"] = item["options"][0]
            with patch.object(widget, "queue") as send:
                for _ in range(2):
                    self.assertTrue(widget.call(self.config, widget.ACTION_TOOL, args)["structuredContent"]["done"])
            self.assertEqual(send.call_count, int(action == "confirm"))
            if action == "confirm":
                self.assertEqual(send.call_args.args[1], "s")
                self.assertIn(args["selection"], send.call_args.args[2])
                self.assertFalse(send.call_args.args[2].startswith(feedback.MARKER))
            rendered = widget.call(self.config, feedback.RENDER_TOOL,
                                   {"session_id": "s", "finding_ids": [item["id"]]})
            outcome = rendered["structuredContent"]["items"][0]
            self.assertEqual(outcome["status"], "submitted" if action == "confirm" else "ignored")
            self.assertEqual(outcome["selection"], args.get("selection", ""))

    def test_widget_other_choice(self):
        item = self.finding()
        args = {"session_id": "s", "finding_id": item["id"], "action": "confirm", "other": True}
        for value in ("", "  ", "x" * 4001):
            with self.assertRaises(ValueError):
                widget.call(self.config, widget.ACTION_TOOL, {**args, "selection": value})
        with patch.object(widget, "queue") as send:
            widget.call(self.config, widget.ACTION_TOOL, {**args, "selection": "  先核查调用方  "})
        self.assertIn("先核查调用方", send.call_args.args[2])
        view = widget.call(self.config, feedback.RENDER_TOOL,
                           {"session_id": "s", "finding_ids": [item["id"]]})["structuredContent"]["items"][0]
        self.assertTrue(view["other"])
        self.assertEqual(view["selection"], "先核查调用方")
        self.assertEqual(view["options"], item["options"])

    def test_widget_uncertain_submission_is_not_repeated(self):
        item = self.finding()
        args = {"session_id": "s", "finding_id": item["id"], "action": "confirm", "selection": "ingest"}
        with patch.object(widget, "queue", side_effect=RuntimeError("queue_failed")) as send:
            for _ in range(2):
                with self.assertRaises((ValueError, RuntimeError)):
                    widget.call(self.config, widget.ACTION_TOOL, args)
        self.assertEqual(send.call_count, 1)
        result = widget.call(self.config, feedback.RENDER_TOOL,
                             {"session_id": "s", "finding_ids": [item["id"]]})
        self.assertTrue(result["structuredContent"]["items"][0]["pending"])

    def test_widget_confirmation_without_options_and_delivery_race(self):
        item = self.finding()
        item["options"] = []
        path = self.root / "findings" / (item["id"] + ".json")
        m.atomic(path, item)
        def decide(*args):
            with patch.object(widget, "queue"):
                widget.call(self.config, widget.ACTION_TOOL,
                            {"session_id": "s", "finding_id": item["id"], "action": "confirm"})
        with patch.object(feedback, "queue", side_effect=decide):
            feedback.deliver(self.config, self.root, [item])
        saved = m.read_json(path)
        self.assertEqual(saved["status"], "submitted")
        self.assertEqual(saved["feedback"], "queued")

    def test_mcp_registration_preserves_other_servers(self):
        original = '[mcp_servers.other]\ncommand = "keep"\n'
        result = installer.configure_mcp(original, self.base, self.cp)
        result = installer.configure_mcp(result, self.base, self.cp)
        self.assertEqual(result.count(installer.MCP_BEGIN), 1)
        removed = installer.configure_mcp(result, self.base, self.cp, uninstall=True)
        self.assertEqual(installer.tomllib.loads(removed), installer.tomllib.loads(original))
        with self.assertRaises(ValueError):
            installer.configure_mcp('[mcp_servers.sunday_note_monitor]\ncommand = "user"\n', self.base, self.cp)

    def test_widget_stdio_protocol(self):
        requests = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params}
                    for i, (method, params) in enumerate([
                        ("initialize", {}), ("tools/list", {}),
                        ("resources/read", {"uri": widget.URI})])]
        result = subprocess.run([sys.executable, str(ROOT / "automation/monitor/widget_server.py"),
                                 "--config", str(self.cp)], input="\n".join(map(json.dumps, requests)) + "\n",
                                text=True, capture_output=True, timeout=5, check=True)
        responses = [json.loads(line)["result"] for line in result.stdout.splitlines()]
        self.assertIn("tools", responses[0]["capabilities"])
        self.assertEqual(responses[1]["tools"][0]["_meta"]["ui"]["resourceUri"], widget.URI)
        self.assertEqual(responses[1]["tools"][1]["_meta"]["ui"]["visibility"], ["app"])
        self.assertEqual(responses[2]["contents"][0]["mimeType"], widget.MIME)

    def test_feedback_turn_does_not_recurse(self):
        self.collect("UserPromptSubmit", prompt=feedback.MARKER + " test")
        self.collect("Stop", last_assistant_message="question")
        self.work(lambda c, e: self.fail("feedback was evaluated again"))
        self.assertEqual(self.rows()[-1]["kind"], "analysis")
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))

    def test_single_worker(self):
        self.collect("Stop",last_assistant_message="x")
        with m.lock(self.runtime/"worker.lock"):
            with patch.object(m,"evaluate",side_effect=AssertionError("must not execute")):
                self.work(lambda c,e: self.fail("concurrent worker"))
        self.assertTrue(list((self.runtime/"queue").glob("*.json")))

    def batch_result(self, event, findings=None):
        return {"context_updates": [], "checked": [], "findings": findings or [],
                "summaries": [{"turn_id": t["turn_id"], "summary": empty()["summary"]}
                              for t in event["_turns"]]}

    def test_sessions_run_concurrently(self):
        self.collect("Stop", last_assistant_message="one")
        other = {**self.event, "session_id": "second"}
        m.collect(self.config, {**other, "hook_event_name": "Stop", "last_assistant_message": "two"})
        runtimes = [self.runtime, m.session_runtime(self.root, other)]
        barrier = threading.Barrier(2, timeout=3)
        errors = []
        calls = []
        def evaluate(c, event):
            calls.append(event["session_id"])
            barrier.wait()
            return self.batch_result(event), .1
        def run(runtime):
            try:
                m.worker(self.cp, runtime, evaluate, wait_seconds=0)
            except BaseException as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=(r,)) for r in runtimes]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(set(calls), {"s", "second"})
        self.assertEqual(sum(r["kind"] == "summary" for r in self.rows()), 2)

    def test_roll_forward_retracts_pending_finding(self):
        item = self.finding()
        source = {k: item[k] for k in m.RESULT_SCHEMA["properties"]["findings"]["items"]["properties"]}
        self.collect("Stop", turn_id="first", last_assistant_message="first")
        def initial(c, event):
            self.collect("UserPromptSubmit", turn_id="next", prompt="already fixed")
            return self.batch_result(event, [source]), 1
        with patch.object(m, "deliver") as send:
            m.worker(self.cp, self.runtime, initial, wait_seconds=0)
            send.assert_not_called()
        self.collect("Stop", turn_id="next", last_assistant_message="fixed")
        def updated(c, event):
            self.assertEqual(len(event["pending_feedback"]), 1)
            self.assertEqual(event["_turns"][0]["turn_id"], "next")
            return self.batch_result(event), 1
        m.worker(self.cp, self.runtime, updated, wait_seconds=0)
        self.assertEqual(m.read_json(self.runtime / "state.json")["pending_feedback"], [])
        self.assertEqual(m.read_json(self.root / "findings" / (item["id"] + ".json"))["feedback"], "superseded")

    def test_batch_summaries_must_cover_exact_turns(self):
        for turn in ("a", "b"):
            self.collect("Stop", turn_id=turn, last_assistant_message=turn)
        def invalid(c, event):
            result = self.batch_result(event)
            result["summaries"] = result["summaries"][:1] * 2
            return result, 0
        m.worker(self.cp, self.runtime, invalid, wait_seconds=0)
        self.assertEqual(sum(r["kind"] == "failed" for r in self.rows()), 2)
        self.assertFalse(any(r["kind"] == "summary" for r in self.rows()))

    def gate_fixture(self):
        item = self.finding()
        m.atomic(self.runtime / "state.json", {"session_id": "s", "cwd": str(self.project),
            "revision": 2, "analysis_revision": 2, "latest_turn": "t", "app_pipe": "/fake",
            "pending_feedback": [item["id"]]})
        return item

    def test_idle_gate_and_unknown_delivery(self):
        item = self.gate_fixture()
        idle = lambda *_: {"status": "idle", "turn_id": "t", "turn_status": "completed"}
        with patch.object(feedback, "queue") as send:
            self.assertTrue(m.try_deliver(self.config, self.root, self.runtime, 2, idle, lambda _: None))
            send.assert_called_once()
        self.assertEqual(m.read_json(self.runtime / "state.json")["pending_feedback"], [])
        # A process interrupted after sending must not retry an ambiguous delivery.
        m.atomic(self.runtime / "state.json", {"session_id": "s", "revision": 2, "analysis_revision": 2,
            "latest_turn": "t", "app_pipe": "/fake", "pending_feedback": [item["id"]]})
        with patch.object(feedback, "queue") as send:
            self.assertFalse(m.try_deliver(self.config, self.root, self.runtime, 2, idle, lambda _: None))
            send.assert_not_called()

    def test_gate_rejects_active_unknown_and_new_revision(self):
        self.gate_fixture()
        for status in ("active", "notLoaded", "systemError"):
            with patch.object(feedback, "queue") as send:
                self.assertFalse(m.try_deliver(self.config, self.root, self.runtime, 2,
                    lambda *_: {"status": status, "turn_id": "t", "turn_status": "completed"}, lambda _: None))
                send.assert_not_called()
        def new_turn(_):
            self.collect("UserPromptSubmit", turn_id="next", prompt="new request")
        with patch.object(feedback, "queue") as send:
            self.assertFalse(m.try_deliver(self.config, self.root, self.runtime, 2,
                lambda *_: {"status": "idle", "turn_id": "t", "turn_status": "completed"}, new_turn))
            send.assert_not_called()

    def test_shared_context_parallel_updates(self):
        barrier = threading.Barrier(2)
        def update(key):
            event = {**self.event, "session_id": key, "prompt": key}
            result = empty()
            result["context_updates"] = [{"key": key, "value": key,
                "source": {"location": key + "/t", "quote": key}}]
            barrier.wait()
            m.finalize(self.root, self.config, event, result, 0)
        threads = [threading.Thread(target=update, args=(key,)) for key in ("a", "b")]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertEqual(set(m.project_context(self.root, self.event)["facts"]), {"a", "b"})

    def test_app_status_wire_and_identity(self):
        pipe = str(self.base / "app.sock")
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(pipe)
            server.listen(1)
            received = []
            def reply():
                with server.accept()[0] as stream:
                    def read(n):
                        value = b""
                        while len(value) < n: value += stream.recv(n-len(value))
                        return value
                    received.append(json.loads(read(struct.unpack("<I", read(4))[0])))
                    value = {"thread": {"id": "s", "status": {"type": "idle"}},
                             "turns": [{"id": "t", "status": "completed"}]}
                    data = json.dumps({"result": {"success": True, "contentItems": [
                        {"type": "inputText", "text": json.dumps(value)}]}}).encode()
                    frame = struct.pack("<I", len(data)) + data
                    for start in range(0, len(frame), 7): stream.sendall(frame[start:start+7])
            thread = threading.Thread(target=reply)
            thread.start()
            self.assertEqual(m.read_thread(pipe, "s"), {"status":"idle", "turn_id":"t", "turn_status":"completed"})
            thread.join(3)
            self.assertEqual(received[0]["params"]["tool"], "read_thread")
        with self.assertRaisesRegex(RuntimeError, "source_status_unavailable"):
            m.read_thread("", "s")

    def test_new_app_pipe_invalidates_gate(self):
        self.gate_fixture()
        def restart(_):
            state = m.read_json(self.runtime / "state.json")
            state["app_pipe"] = "/new-app"
            m.atomic(self.runtime / "state.json", state)
        with patch.object(feedback, "queue") as send:
            self.assertFalse(m.try_deliver(self.config, self.root, self.runtime, 2,
                lambda *_: {"status":"idle", "turn_id":"t", "turn_status":"completed"}, restart))
            send.assert_not_called()

    def test_stop_during_gate_is_not_lost(self):
        self.gate_fixture()
        calls = []
        def status(*_):
            self.collect("Stop", turn_id="next", last_assistant_message="new evidence")
            return {"status":"idle", "turn_id":"t", "turn_status":"completed"}
        def evaluate(c, event):
            calls.append(event["turn_id"])
            return self.batch_result(event), 0
        with patch.object(feedback, "queue") as send:
            m.worker(self.cp, self.runtime, evaluate, status, lambda _: None, wait_seconds=0)
            send.assert_not_called()
        self.assertEqual(calls, ["next"])

    def test_interrupted_finalization_does_not_reanalyze(self):
        self.collect("Stop", last_assistant_message="done")
        event = m.read_json(next((self.runtime / "queue").glob("*.json")))
        m.finalize(self.root, self.config, event, empty(), 1)
        m.worker(self.cp, self.runtime, lambda *_: self.fail("completed turn reanalyzed"), wait_seconds=0)
        self.assertFalse(list((self.runtime / "queue").glob("*.json")))

    def test_changed_evidence_blocks_delivery(self):
        self.gate_fixture()
        (self.project / "a.md").write_text("updated")
        with patch.object(feedback, "queue") as send:
            self.assertFalse(m.try_deliver(self.config, self.root, self.runtime, 2,
                lambda *_: {"status":"idle", "turn_id":"t", "turn_status":"completed"}, lambda _: None))
            send.assert_not_called()
        self.assertEqual(m.read_json(self.runtime / "state.json")["blocked"], "evidence_changed")

    def test_migration_is_one_shot_and_cannot_send_old_feedback(self):
        item = self.finding()
        old = self.root / "queue" / "old.json"
        m.atomic(old, {**self.event, "turn_id":"old", "created":1, "revision":1, "ready":True})
        m.migrate(self.root)
        self.assertFalse(old.exists())
        self.assertTrue((self.runtime / "queue" / "old.json").exists())
        state = m.read_json(self.runtime / "state.json")
        self.assertEqual(state["pending_feedback"], [item["id"]])
        self.assertNotIn("analysis_revision", state)
        m.migrate(self.root)
        self.assertEqual(m.read_json(self.runtime / "state.json"), state)

    def test_real_render_and_confirmation_messages(self):
        item = self.finding()
        self.collect("UserPromptSubmit", prompt=feedback.message([item]))
        self.collect("Stop", last_assistant_message="")
        self.work(lambda *_: self.fail("render invokes model"))
        with patch.object(widget, "queue") as send:
            widget.call(self.config, widget.ACTION_TOOL, {"session_id": "s", "finding_id": item["id"],
                "action": "confirm", "selection": item["options"][0]})
        text = send.call_args.args[2]
        self.collect("UserPromptSubmit", turn_id="decision", prompt=text)
        self.collect("Stop", turn_id="decision", last_assistant_message="handled")
        calls = []
        self.work(lambda c,e: (calls.append(e) or empty(), 0))
        self.assertEqual(calls[0]["prompt"], text)

    def test_install_update_uninstall_preserves_user(self):
        home, apps = self.base / "codex", self.base / "apps"
        home.mkdir()
        (home / "config.toml").write_text('[features]\nhooks = false\n[unrelated]\nvalue = 4\n')
        existing = {"hooks":{"Stop":[{"hooks":[{"type":"command","command":"user-command"}]}]}}
        m.atomic(home / "hooks.json", existing)
        runtime = self.vault / ".sunday-note-agent" / "monitor"
        runtime.mkdir(parents=True)
        (runtime / "panel.py").write_text("old panel")
        (runtime / "personal.txt").write_text("preserve")
        apps.mkdir()
        (apps / "sunday-note-monitor.desktop").write_text("old launcher")
        with patch.object(installer.shutil, "which", side_effect=lambda x:"/usr/bin/" + x):
            installer.configure(self.vault,home,apps,proxy_url="http://127.0.0.1:12345")
            installed = m.read_json(self.root / "config.json")
            self.assertEqual(installed["project_roots"], [str(self.vault)])
            m.atomic(self.root / "config.json", {**installed, "project_roots": []})
            installer.configure(self.vault,home,apps)
        self.assertEqual(m.read_json(self.root / "config.json")["project_roots"], [])
        self.assertFalse((runtime / "panel.py").exists())
        self.assertTrue((runtime / "feedback.py").exists())
        self.assertTrue((runtime / "widget_server.py").exists())
        self.assertTrue((runtime / "widget.html").exists())
        config = installer.tomllib.loads((home / "config.toml").read_text())
        self.assertIn(str(runtime / "widget_server.py"), config["mcp_servers"]["sunday_note_monitor"]["args"])
        self.assertEqual((runtime / "personal.txt").read_text(), "preserve")
        self.assertFalse((apps / "sunday-note-monitor.desktop").exists())
        self.assertEqual(m.read_json(self.root / "config.json")["proxy_url"], "http://127.0.0.1:12345")
        hook = m.read_json(home / "hooks.json")
        self.assertEqual(len(hook["hooks"]["Stop"]), 2)
        self.assertEqual(hook["hooks"]["Stop"][0], existing["hooks"]["Stop"][0])
        installer.configure(self.vault,home,apps,uninstall=True)
        self.assertEqual(m.read_json(home/"hooks.json")["hooks"]["Stop"], existing["hooks"]["Stop"])
        self.assertIn("hooks = false", (home/"config.toml").read_text())
        self.assertIn("value = 4", (home/"config.toml").read_text())
        self.assertNotIn("sunday_note_monitor", (home/"config.toml").read_text())
        self.assertTrue(self.root.exists())

    def test_symlink_log_rejected(self):
        other = self.base / "other"
        other.mkdir()
        (other / ".logs").symlink_to(self.root.parent)
        with self.assertRaises(ValueError):
            m.root_for({"vault":str(other)})


if __name__ == "__main__":
    unittest.main()
