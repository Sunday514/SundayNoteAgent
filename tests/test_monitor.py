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
    return {"summary": {k: [] for k in m.RESULT_SCHEMA["properties"]["summary"]["properties"]},
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
        self.event = {"session_id": "s", "turn_id": "t", "cwd": str(self.project),
                      "transcript_path": str(self.base / "transcript.jsonl")}

    def collect(self, kind, **kw):
        return m.collect(self.config, {**self.event, "hook_event_name": kind, **kw})

    def rows(self):
        return [json.loads(l) for p in (self.root / "sessions").glob("*.jsonl") for l in p.read_text().splitlines()]

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
        self.assertFalse(list((self.root / "queue").glob("*.json")))
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
        self.assertFalse(m.project_allowed(self.config, str(clone)))
        self.assertFalse(m.project_allowed({**self.config, "project_roots": []}, str(linked)))
        self.assertTrue(m.project_allowed({**self.config, "project_roots": [str(linked)]}, str(self.project)))
        with patch.dict(os.environ, {"GIT_DIR": str(self.project / ".git")}):
            self.assertFalse(m.project_allowed(self.config, str(clone)))
        self.assertTrue(self.collect("Stop", cwd=str(linked), last_assistant_message="changed code"))
        calls = []
        m.worker(self.cp, lambda c, e: (calls.append(e) or empty(), 0))
        self.assertEqual(calls[0]["cwd"], str(linked))

    def test_git_scope_probe_failure(self):
        with patch.object(m.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 0.5)):
            self.assertFalse(m.project_allowed(self.config, str(self.vault)))
            self.assertTrue(m.project_allowed(self.config, str(self.project)))

    def test_removed_project_is_not_evaluated(self):
        self.collect("Stop", last_assistant_message="answer")
        m.atomic(self.cp, {**self.config, "project_roots": []})
        m.worker(self.cp, lambda c, e: self.fail("excluded project evaluated"))
        self.assertFalse(list((self.root / "queue").glob("*.json")))
        self.assertFalse(any(r["kind"] == "summary" for r in self.rows()))

    def test_dedup_pair_and_raw_cleanup(self):
        self.collect("UserPromptSubmit", prompt="question")
        self.collect("Stop", last_assistant_message="answer")
        self.collect("Stop", last_assistant_message="answer")
        calls = []
        def evaluate(c, e):
            calls.append(e)
            return empty(), 1
        m.worker(self.cp, evaluate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prompt"], "question")
        self.assertEqual([r["kind"] for r in self.rows()], ["registered", "summary"])
        self.assertFalse(list((self.root / "queue").glob("*.json")))
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
        self.assertFalse(list((self.root / "queue").glob("*.json")))
        self.assertFalse((self.root / "state.json").exists())
        self.assertTrue(self.collect("Stop", last_assistant_message="answer"))

    def test_missing_context_no_other_session_fallback(self):
        self.collect("UserPromptSubmit", prompt="other", session_id="other")
        self.collect("Stop", last_assistant_message="answer")
        m.worker(self.cp, lambda c,e: (empty(), 0))
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
        m.worker(self.cp, fail)
        m.worker(self.cp, fail)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(list((self.root / "queue").glob("*.json"))), 1)
        self.collect("Stop", turn_id="next", last_assistant_message="y")
        m.worker(self.cp, lambda c,e: (empty(), 0))
        self.assertFalse(list((self.root / "queue").glob("*.json")))

    def test_new_event_during_evaluation_not_lost(self):
        self.collect("Stop", last_assistant_message="x")
        calls = []
        def evaluate(c,e):
            if not calls:
                self.collect("UserPromptSubmit", prompt="late prompt")
            calls.append(e)
            return empty(), 0
        m.worker(self.cp, evaluate)
        self.assertEqual(len(calls), 2)
        self.assertFalse(list((self.root / "queue").glob("*.json")))

    def test_identical_stop_during_evaluation_is_noop(self):
        self.collect("Stop", last_assistant_message="answer")
        calls = []
        def evaluate(c, e):
            calls.append(e)
            self.assertFalse(self.collect("Stop", last_assistant_message="answer"))
            return empty(), 0
        m.worker(self.cp, evaluate)
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
                m.worker(self.cp, evaluate)
                self.assertEqual(calls, [bad, good])
                self.assertFalse(list((self.root / "queue").glob("*.json")))
                self.assertFalse(self.collect("Stop", turn_id=bad))
        self.assertNotIn("private raw", json.dumps(self.rows()))

    def test_abandoned_input_cleanup(self):
        self.collect("UserPromptSubmit", prompt="private raw")
        self.collect("UserPromptSubmit", turn_id="next", prompt="next")
        self.assertEqual([r["kind"] for r in self.rows()], ["registered", "abandoned", "registered"])
        self.assertFalse(self.collect("Stop", last_assistant_message="late"))
        with patch.object(m.time, "time", return_value=time.time() + 86401):
            self.collect("UserPromptSubmit", session_id="other", turn_id="other")
        queued = [m.read_json(p) for p in (self.root / "queue").glob("*.json")]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["session_id"], "other")
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
        m.validate(empty(), m.RESULT_SCHEMA)
        with self.assertRaises(ValueError):
            m.validate({"findings": []}, m.RESULT_SCHEMA)
        file = self.project / "README.md"
        file.write_text("old entry")
        item = {"title":"entry drift", "reason":"comparison", "instruction":"check entry",
                "options":["update document","check code"], "evidence":[{"location":str(file),"quote":"old entry"}]}
        result = empty()
        result["findings"] = [item]
        m.validate(result, m.RESULT_SCHEMA)
        new = m.finalize(self.root, self.config, self.event, result, 1)
        self.assertEqual(len(new), 1)
        self.assertEqual(m.finalize(self.root,self.config,self.event,result,1), [])
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
                self.assertEqual(m.finalize(self.root, self.config, self.event, result, 0), [])

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
 assert a[a.index('-m')+1]=='gpt-5.6-luna'
 text=sys.stdin.read()
 assert 'context_complete' in text
 Path(a[a.index('-o')+1]).write_text(json.dumps({'summary':{k:[] for k in ['user_requests','user_decisions','assistant_claims','observations','inferences','open_questions']},'checked':[],'findings':[]}))
else: raise SystemExit(1)
''')
        fake.chmod(0o700)
        m.atomic(self.cp, {**self.config,"codex":str(fake)})
        self.collect("UserPromptSubmit",prompt="thanks")
        r = subprocess.run([sys.executable,str(ROOT/"automation/monitor/monitor.py"),"--config",str(self.cp),"hook"],
                           input=json.dumps({**self.event,"hook_event_name":"Stop","last_assistant_message":"ok"}),
                           capture_output=True,text=True,timeout=3)
        self.assertEqual(json.loads(r.stdout),{"continue":True})
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and self.rows()[-1]["kind"]!="summary":
            time.sleep(.02)
        self.assertEqual(self.rows()[-1]["kind"],"summary")
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
        self.assertNotIn(item["instruction"], argv[-1])
        self.assertLess(len(argv[-1]), 1000)
        self.assertEqual(item["feedback"], "queued")

    def test_feedback_failure_and_scope(self):
        item = self.finding()
        with patch.object(feedback.subprocess, "run") as send:
            feedback.deliver({**self.config, "project_roots": []}, self.root, [item])
        send.assert_not_called()
        self.assertEqual(item["feedback"], "skipped_scope")
        self.assertTrue(any(r["kind"] == "summary" for r in self.rows()))

    def test_feedback_queue_failure_and_bounded_message(self):
        item = self.finding()
        item.update(title="标题" * 1000, reason="原因" * 1000,
                    instruction="建议" * 1000, options=["选项" * 1000] * 3)
        self.assertLess(len(feedback.message([item])), 1000)
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
        m.validate(result, m.RESULT_SCHEMA)
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
        m.worker(self.cp, lambda c, e: self.fail("feedback was evaluated again"))
        self.assertEqual(self.rows()[-1]["kind"], "summary")
        self.assertFalse(list((self.root / "queue").glob("*.json")))

    def test_single_worker(self):
        self.collect("Stop",last_assistant_message="x")
        with m.lock(self.root/"worker.lock"):
            with patch.object(m,"evaluate",side_effect=AssertionError("must not execute")):
                m.worker(self.cp,lambda c,e: self.fail("concurrent worker"))
        self.assertTrue(list((self.root/"queue").glob("*.json")))

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
