#!/usr/bin/env python3
"""临时项目中的回流边界；不调用模型或真实队列。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('monitor', ROOT / 'skills/sunday-note-monitor/scripts/monitor.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_STATE_HOME': str(self.root / 'state'),
            'CODEX_HOME': str(self.root / 'codex'), 'CODEX_THREAD_ID': 'maint'})
        self.env.start()
        self.parent = patch.object(m, 'parent_codex', return_value='/usr/bin/true')
        self.parent.start()
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.vault / '工作项目/P').mkdir(parents=True)
        m.binding.config_path().parent.mkdir(parents=True)
        m.binding.save(m.binding.config_path(), {str(self.vault): {'p': {
            'project_path': '工作项目/P', 'raw_path': '工作资料/P',
            'sources': [{'kind': 'directory', 'path': str(self.repo), 'identity': str(self.repo)}],
            'maintenance': {'client': 'codex', 'session_id': 'maint'}, 'raw_auto_enabled': False}}})
        m.configure(self.vault, 'p', True, '/usr/bin/true')

    def tearDown(self):
        self.parent.stop()
        self.env.stop()
        self.tmp.cleanup()

    def event(self, turn='t1', session='dev', prompt='完成任务', event='Stop'):
        p = self.root / 'codex/sessions' / (session + '.jsonl')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': session, 'source': 'cli'}}) + '\n')
        return {'session_id': session, 'turn_id': turn, 'cwd': str(self.repo), 'prompt': prompt,
                'last_assistant_message': '测试通过（主 Agent 声明）',
                'transcript_path': str(p), 'hook_event_name': event}

    def state(self):
        return m.load(m.state_path(self.vault, 'p'))

    def test_state_is_vault_local_and_scoped(self):
        self.assertEqual(m.state_path(self.vault, 'p'),
                         self.vault / '.import_files/sunday-note-agent/p/state.json')
        self.assertNotEqual(m.state_path(self.vault, 'p'), m.state_path(self.root / 'other', 'p'))
        with self.assertRaises(ValueError):
            m.state_path(self.vault, '../escape')
        target = self.vault / '.import_files/sunday-note-agent/linked'
        target.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            m.state_path(self.vault, 'linked')

    def dispatch(self):
        calls = []
        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        m.dispatch(self.vault, 'p', run)
        return calls

    def test_pair_dedup_and_complete(self):
        m.collect(self.vault, self.event(event='UserPromptSubmit'))
        self.assertEqual(self.dispatch(), [])
        e = self.event()
        m.collect(self.vault, e)
        m.collect(self.vault, e)
        self.assertEqual(len(self.dispatch()), 1)
        self.assertEqual(self.dispatch(), [])
        batch = self.state()['active']['id']
        result = m.claim(self.vault, 'p', batch)
        self.assertEqual(len(result['turns']), 1)
        m.complete(self.vault, 'p', batch, '无增量')
        self.assertFalse(self.state()['pending'])
        self.assertNotIn('prompt', self.state()['history']['dev/t1'])
        m.collect(self.vault, e)
        self.assertFalse(self.state()['pending'])
        with self.assertRaises(ValueError): m.claim(self.vault, 'p', batch)

    def test_backlog_and_stable_claim(self):
        m.collect(self.vault, self.event())
        self.dispatch()
        m.collect(self.vault, self.event('t2'))
        batch = self.state()['active']['id']
        self.assertEqual(len(m.claim(self.vault, 'p', batch)['turns']), 2)
        m.collect(self.vault, self.event('t3'))
        self.assertEqual(len(m.claim(self.vault, 'p', batch)['turns']), 2)
        m.complete(self.vault, 'p', batch, '整理完成')
        self.assertEqual(list(self.state()['pending']), ['dev/t3'])
        self.assertEqual(len(self.dispatch()), 1)

    def test_auto_turn_and_user_decision(self):
        m.collect(self.vault, self.event(session='maint', prompt=m.MARKER, event='UserPromptSubmit'))
        m.collect(self.vault, self.event(session='maint', prompt=''))
        self.assertFalse(self.state()['pending'])
        m.collect(self.vault, self.event('t2', 'maint', '同意调整'))
        self.assertEqual(len(self.state()['pending']), 1)

    def test_source_filters(self):
        for extra in ({'agent_id': 'child'}, {'transcript_path': ''}, {'cwd': str(self.root)}):
            self.assertIsNone(m.collect(self.vault, {**self.event(), **extra}))
        m.configure(self.vault, 'p', False)
        self.assertIsNone(m.collect(self.vault, self.event()))

    def test_only_persistent_main_sessions(self):
        e = self.event()
        self.assertIsNone(m.collect(self.vault, {**e, 'ephemeral': True}))
        p = Path(e['transcript_path'])
        base = {'id': 'dev', 'source': 'cli'}
        for change in ({'source': None}, {'source': 'unknown'}, {'source': {'subagent': {}}},
                       {'ephemeral': True}, {'thread_source': 'side_chat'},
                       {'thread_source': 'side_conversation'}, {'thread_source': 'ephemeral'}):
            p.write_text(json.dumps({'type': 'session_meta', 'payload': {**base, **change}}) + '\n')
            self.assertIsNone(m.collect(self.vault, e), change)
        p.write_text(json.dumps({'type': 'session_meta', 'payload': base}) + '\n')
        outside = self.root / 'ephemeral.jsonl'
        outside.write_text(p.read_text())
        self.assertIsNone(m.collect(self.vault, {**e, 'transcript_path': str(outside)}))
        self.assertFalse(self.state()['pending'])
        p.write_text(json.dumps({'type': 'session_meta', 'payload': {
            **base, 'source': 'vscode', 'thread_source': 'agent_created_thread'}}) + '\n')
        self.assertEqual(m.collect(self.vault, e), 'p')

    def test_uncertain_not_resent(self):
        m.collect(self.vault, self.event())
        def fail(*a, **kw): raise subprocess.TimeoutExpired('queue', 20)
        m.dispatch(self.vault, 'p', fail)
        self.assertEqual(self.state()['active']['status'], 'uncertain')
        self.assertEqual(self.dispatch(), [])

    def test_scope_and_issue_preserved(self):
        m.collect(self.vault, self.event())
        self.dispatch()
        batch = self.state()['active']['id']
        m.claim(self.vault, 'p', batch)
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, 'x', ['工作资料/Other/x.md'])
        m.complete(self.vault, 'p', batch, '有冲突未写入', issue='请确认合并')
        self.assertIn(batch, self.state()['issues'])

    def test_hook_install_preserves_others(self):
        p = self.root / 'codex/hooks.json'
        p.parent.mkdir(parents=True)
        original = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'other'}]}]}}
        m.binding.save(p, original)
        m.hooks(self.vault, True)
        m.hooks(self.vault, True)
        self.assertEqual(len(json.loads(p.read_text())['hooks']['Stop']), 2)
        m.hooks(self.vault, False)
        self.assertEqual(json.loads(p.read_text())['hooks']['Stop'], original['hooks']['Stop'])

    def test_redaction_and_oversize(self):
        e = self.event(prompt='api_key=secret')
        m.collect(self.vault, e)
        self.assertNotIn('secret', json.dumps(self.state()))
        m.collect(self.vault, self.event('t2', prompt='x' * (m.LIMIT + 1)))
        self.assertTrue(self.state()['pending']['dev/t2']['oversized'])

    def test_concurrent_dispatch_only_once(self):
        m.collect(self.vault, self.event())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.dispatch(), range(4)))
        self.assertEqual(sum(map(len, results)), 1)

    def test_worktree_identity(self):
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        wt = self.root / 'worktree'
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'add', '-q', '--detach', str(wt)], check=True)
        p = m.binding.config_path()
        config = m.binding.read(p)
        config[str(self.vault)]['p']['sources'] = [m.binding.source_identity(self.repo)]
        m.binding.save(p, config)
        self.assertEqual(m.collect(self.vault, {**self.event(), 'cwd': str(wt)}), 'p')

    def test_material_exact_turn(self):
        e = self.event()
        with Path(e['transcript_path']).open('a') as f:
            for r in [
                {'type': 'turn_context', 'payload': {'turn_id': 'other'}},
                {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'wrong'}},
                {'type': 'turn_context', 'payload': {'turn_id': 't1'}},
                {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'right'}}]:
                f.write(json.dumps(r) + '\n')
        evidence = m.material(e)['evidence']
        self.assertEqual(len(evidence), 1)
        self.assertIn('right', evidence[0])

    def test_stop_only_automatic_source(self):
        e = self.event(session='maint', prompt='')
        with Path(e['transcript_path']).open('a') as f:
            f.write(json.dumps({'type': 'turn_context', 'payload': {'turn_id': 't1'}}) + '\n')
            f.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': '<environment_context>fixture</environment_context>'}]}}) + '\n')
            f.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': m.MARKER}]}}) + '\n')
            f.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'UserMessage', 'id': 'u', 'content': [{'type': 'text', 'text': m.MARKER}]}}}) + '\n')
        self.assertEqual(m.collect(self.vault, e), 'p')
        self.assertFalse(self.state()['pending'])
        self.assertEqual(self.state()['history']['maint/t1']['kind'], 'automatic')
        self.assertEqual(m.collect(self.vault, e), 'p')

    def test_completed_user_message_and_unknown_origin(self):
        e = self.event(session='maint', prompt=None)
        self.assertIsNone(m.collect(self.vault, e))
        self.assertFalse(self.state()['pending'])
        self.assertIn('maint/t1', self.state()['issues'])
        with Path(e['transcript_path']).open('a') as f:
            for row in [
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 't1'}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                    'type': 'UserMessage', 'id': 'u', 'content': [{'type': 'text', 'text': m.MARKER}]}}}]:
                f.write(json.dumps(row) + '\n')
        m.collect(self.vault, e)
        self.assertFalse(self.state()['pending'])

    def test_real_tools_redaction_and_dedup(self):
        e = self.event(prompt='export DB_PASSWORD=hunter2 OPENAI_API_KEY=abc')
        rows = [
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 't1'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'x',
                'input': 'Authorization: Bearer fake-secret'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'x',
                'output': {'api_key': 'secretval'}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'CommandExecution', 'id': 'exec-different', 'aggregated_output': 'duplicate'}}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'other'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'y', 'input': 'wrong-turn'}}]
        with Path(e['transcript_path']).open('a') as f:
            for row in rows: f.write(json.dumps(row) + '\n')
        m.collect(self.vault, e)
        self.dispatch()
        report = m.claim(self.vault, 'p', self.state()['active']['id'])
        self.assertEqual(len(report['turns'][0]['evidence']), 2)
        text = json.dumps(report) + json.dumps(self.state())
        for secret in ('hunter2', 'abc', 'secretval', 'fake-secret', 'wrong-turn', 'duplicate'):
            self.assertNotIn(secret, text)

    def test_coverage_and_null(self):
        e = self.event(prompt=None)
        self.assertIn('未找到', m.material(e)['coverage_limit'])
        with Path(e['transcript_path']).open('a') as f:
            f.write(json.dumps({'type': 'turn_context', 'payload': {'turn_id': 't1'}}) + '\n')
        self.assertIn('未观察到', m.material(e)['coverage_limit'])
        self.assertEqual(m.collect(self.vault, e), 'p')

    def test_disable_allows_completion_and_retains_results(self):
        m.collect(self.vault, self.event())
        self.dispatch()
        batch = self.state()['active']['id']
        m.claim(self.vault, 'p', batch)
        m.configure(self.vault, 'p', False)
        m.complete(self.vault, 'p', batch, '完成')
        self.assertIn(batch, self.state()['results'])
        self.assertNotIn('last_result', self.state())
        self.assertIsNone(m.collect(self.vault, self.event('t2')))

    def test_failed_enable_remains_disabled(self):
        m.configure(self.vault, 'p', False)
        original = m.binding.save
        def fail_state(path, value):
            if path.name == 'state.json': raise OSError('read only')
            original(path, value)
        with patch.object(m.binding, 'save', side_effect=fail_state):
            with self.assertRaises(OSError): m.configure(self.vault, 'p', True, '/usr/bin/true')
        self.assertFalse(m.projects(self.vault)['p']['raw_auto_enabled'])

    def test_cli_large_stop_and_retry_guard(self):
        e = self.event(prompt='x' * (m.LIMIT * 3))
        with patch.object(m.sys, 'argv', ['monitor', 'hook', '--vault-root', str(self.vault)]), \
             patch.object(m.sys, 'stdin', __import__('io').StringIO(json.dumps(e))), \
             patch.object(m.binding, 'source_identity', return_value={
                 'kind': 'directory', 'path': str(self.repo), 'identity': str(self.repo)}), \
             patch.object(m.subprocess, 'Popen'), patch('builtins.print'):
            m.main()
        self.assertTrue(self.state()['pending']['dev/t1']['ready'])
        self.assertTrue(self.state()['pending']['dev/t1']['oversized'])
        result = subprocess.run([m.sys.executable, str(m.HERE), 'retry', '--vault-root', str(self.vault),
                                 '--project-id', 'p'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('confirm-stopped', result.stderr)
        state = self.state()
        state['issues']['test'] = 'issue'
        m.binding.save(m.state_path(self.vault, 'p'), state)
        subprocess.run([m.sys.executable, str(m.HERE), 'resolve', '--vault-root', str(self.vault),
                        '--project-id', 'p', '--issue-id', 'test'], check=True)
        self.assertNotIn('test', self.state()['issues'])


if __name__ == '__main__':
    unittest.main()
