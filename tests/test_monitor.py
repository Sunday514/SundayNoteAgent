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
            'XDG_DATA_HOME': str(self.root / 'data'),
            'CODEX_HOME': str(self.root / 'codex'), 'CODEX_THREAD_ID': 'maint'})
        self.env.start()
        interval = patch.object(m, 'DISPATCH_INTERVAL', 0)
        interval.start()
        self.addCleanup(interval.stop)
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

    def raw_files(self):
        folder = self.vault / '工作资料/P'
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('案例.md', '反例.md'):
            (folder / name).write_text('已有事实与限制\n')
        return ['工作资料/P/案例.md', '工作资料/P/反例.md']

    def next_claim(self, turn, session='dev'):
        m.collect(self.vault, self.event(turn, session))
        self.dispatch()
        batch = self.state()['active']['id']
        return batch, m.claim(self.vault, 'p', batch)

    def test_tidy_counts_project_writes_not_turns_or_files(self):
        files = self.raw_files()
        batch, report = self.next_claim('empty')
        m.complete(self.vault, 'p', batch, '无增量')
        self.assertEqual(m.tidy_status(self.state())['writes'], 0)
        for i in range(10):
            batch, report = self.next_claim(str(i), 'dev' if i % 2 else 'dev2')
            self.assertFalse(report['tidy']['due'])
            m.complete(self.vault, 'p', batch, '合并事实', files)
        self.assertEqual(m.tidy_status(self.state()), {'writes': 10, 'files': sorted(files), 'due': True})
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '重复', files)
        self.assertEqual(m.tidy_status(self.state())['writes'], 10)
        batch, report = self.next_claim('tidy')
        self.assertTrue(report['tidy']['due'])
        m.collect(self.vault, self.event('later'))
        self.assertEqual(m.claim(self.vault, 'p', batch)['tidy'], report['tidy'])
        m.complete(self.vault, 'p', batch, '已合并同条件对照', files, tidy_done=True)
        self.assertEqual(m.tidy_status(self.state()), {'writes': 0, 'files': [], 'due': False})
        self.assertTrue(self.state()['results'][batch]['tidy_done'])
        self.assertIn('dev/later', self.state()['pending'])
        self.assertIsNone(self.state()['active'])

    def test_tidy_incomplete_and_no_change_completion(self):
        files = self.raw_files()
        path = m.state_path(self.vault, 'p')
        state = self.state()
        state['tidy'] = {'writes': 10, 'files': files[:1]}
        m.binding.save(path, state)
        batch, _ = self.next_claim('partial')
        m.complete(self.vault, 'p', batch, '回流完成，整理待续', files[1:], issue='人工内容冲突')
        self.assertEqual(m.tidy_status(self.state())['writes'], 11)
        self.assertEqual(set(self.state()['tidy']['files']), set(files))
        batch, report = self.next_claim('resume')
        self.assertTrue(report['tidy']['due'])
        m.complete(self.vault, 'p', batch, '已核对，无需改动', tidy_done=True)
        self.assertFalse(m.tidy_status(self.state())['due'])
        self.assertEqual(self.state()['tidy']['files'], [])

    def test_tidy_old_history_starts_zero_and_is_project_local(self):
        path = m.state_path(self.vault, 'p')
        state = self.state()
        state['results'] = {str(i): {'files': ['工作资料/P/案例.md']} for i in range(20)}
        m.binding.save(path, state)
        batch, report = self.next_claim('first')
        self.assertEqual(report['tidy'], {'writes': 0, 'files': [], 'due': False})
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '未到期', tidy_done=True)
        m.complete(self.vault, 'p', batch, '写入', self.raw_files())
        self.assertEqual(m.tidy_status(self.state())['writes'], 1)
        self.assertEqual(m.tidy_status(m.load(m.state_path(self.vault, 'other'))),
                         {'writes': 0, 'files': [], 'due': False})

    def test_auto_turn_and_user_decision(self):
        m.collect(self.vault, self.event(session='maint', prompt=m.MARKER, event='UserPromptSubmit'))
        m.collect(self.vault, self.event(session='maint', prompt=''))
        self.assertFalse(self.state()['pending'])
        m.collect(self.vault, self.event('t2', 'maint', '同意调整'))
        self.assertEqual(len(self.state()['pending']), 1)

    def wiki_targets(self, names=('方法',)):
        pages = [f'知识库/P/{n}.md' for n in names]
        for page in pages:
            path = self.vault / page
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text('方法与限制\n')
        config = m.binding.read(m.binding.config_path())
        config[str(self.vault)]['p']['wiki_targets'] = pages
        m.binding.save(m.binding.config_path(), config)
        return pages

    def due(self):
        state = self.state()
        state['tidy'] = {'writes': 10, 'files': self.raw_files()}
        m.binding.save(m.state_path(self.vault, 'p'), state)

    def test_wiki_only_at_due_and_records_separately(self):
        pages = self.wiki_targets()
        batch, report = self.next_claim('ordinary')
        self.assertEqual(report['wiki_targets'], [])
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '越权', wiki_files=pages)
        m.complete(self.vault, 'p', batch, '无增量')
        self.due()
        batch, report = self.next_claim('due')
        self.assertEqual(report['wiki_targets'], pages)
        m.complete(self.vault, 'p', batch, 'Wiki 更新，Raw 整理未完', wiki_files=pages, issue='待续')
        state = self.state()
        self.assertEqual(state['tidy']['writes'], 10)
        self.assertEqual(state['results'][batch]['wiki_files'], pages)
        self.assertEqual(state['results'][batch]['files'], [])
        batch, report = self.next_claim('no-delta')
        m.complete(self.vault, 'p', batch, '全部核对，无需修改', tidy_done=True)
        self.assertFalse(m.tidy_status(self.state())['due'])

    def test_wiki_snapshot_addition_and_revocation(self):
        pages = self.wiki_targets()
        self.due()
        batch, report = self.next_claim('freeze')
        added = self.wiki_targets(('方法', '另一页'))
        self.assertEqual(m.claim(self.vault, 'p', batch)['wiki_targets'], pages)
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '新增越权', wiki_files=added)
        self.wiki_targets(())
        self.assertEqual(m.claim(self.vault, 'p', batch)['wiki_targets'], [])
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '撤销后写入', wiki_files=pages)
        self.assertEqual(self.state()['active']['id'], batch)
        m.complete(self.vault, 'p', batch, '授权撤销，未继续修改', issue='Wiki 未完成')
        self.assertTrue(m.tidy_status(self.state())['due'])

    def test_wiki_missing_symlink_and_legacy_claim(self):
        self.due()
        batch, report = self.next_claim('legacy')
        self.assertEqual(report['wiki_targets'], [])
        state = self.state()
        del state['active']['wiki_targets']
        m.binding.save(m.state_path(self.vault, 'p'), state)
        pages = self.wiki_targets()
        self.assertEqual(m.claim(self.vault, 'p', batch)['wiki_targets'], [])
        m.complete(self.vault, 'p', batch, '旧批次只处理 Raw')
        batch, report = self.next_claim('path')
        target = self.vault / pages[0]
        target.unlink()
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '已删除', wiki_files=pages)
        outside = self.root / 'outside.md'
        outside.write_text('不可修改')
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            m.claim(self.vault, 'p', batch)
        with self.assertRaises(ValueError):
            m.complete(self.vault, 'p', batch, '符号链接', wiki_files=pages)
        self.assertEqual(outside.read_text(), '不可修改')

    def test_wiki_complete_cli(self):
        pages = self.wiki_targets()
        self.due()
        batch, _ = self.next_claim('cli')
        result = subprocess.run([m.sys.executable, str(m.HERE), 'complete', '--vault-root',
                                 str(self.vault), '--project-id', 'p', '--batch', batch,
                                 '--result', 'Wiki 局部更新', '--wiki-file', pages[0], '--tidy-done'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()['results'][batch]['wiki_files'], pages)
        self.assertEqual(self.state()['tidy']['writes'], 0)

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
        installed = p.read_bytes()
        modified = p.stat().st_mtime_ns
        m.hooks(self.vault, True)
        self.assertEqual(p.read_bytes(), installed)
        self.assertEqual(p.stat().st_mtime_ns, modified)
        self.assertIn(str(m.runtime_root()), installed.decode())
        self.assertEqual(len(json.loads(p.read_text())['hooks']['Stop']), 2)
        m.hooks(self.vault, False)
        self.assertEqual(json.loads(p.read_text())['hooks']['Stop'], original['hooks']['Stop'])

    def test_runtime_survives_source_cache_removal(self):
        import shutil
        cache = self.root / 'versioned-cache/skills'
        for source in (m.HERE, Path(m.binding.__file__)):
            target = cache / source.relative_to(m.HERE.parents[2])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        source = cache / 'sunday-note-monitor/scripts/monitor.py'
        subprocess.run([m.sys.executable, str(source), 'install', '--vault-root', str(self.vault)], check=True)
        hook_file = self.root / 'codex/hooks.json'
        before = hook_file.read_bytes()
        shutil.rmtree(cache.parent)
        entry = m.runtime_root() / 'sunday-note-monitor/scripts/monitor.py'
        result = subprocess.run([m.sys.executable, str(entry), 'hook', '--vault-root', str(self.vault)],
                                input='{}', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        m.deploy_runtime()
        self.assertEqual(hook_file.read_bytes(), before)

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

    def test_cooldown_accumulates_and_delivers_without_new_hook(self):
        clock = [1000.0]
        calls = []
        def run(argv, **kw):
            calls.append(clock[0])
            return subprocess.CompletedProcess(argv, 0, '', '')
        def sleep(seconds):
            # Another Hook must not create another waiter or send early.
            m.collect(self.vault, self.event('t3'))
            m.dispatch(self.vault, 'p', run)
            clock[0] += seconds
        with patch.object(m, 'DISPATCH_INTERVAL', 600), \
                patch.object(m.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(m.time, 'sleep', side_effect=sleep):
            m.collect(self.vault, self.event())
            m.dispatch(self.vault, 'p', run)
            batch = self.state()['active']['id']
            m.claim(self.vault, 'p', batch)
            m.complete(self.vault, 'p', batch, '无增量')
            clock[0] += 1
            m.collect(self.vault, self.event('t2'))
            m.dispatch(self.vault, 'p', run)
            self.assertEqual(calls, [1000.0, 1600.0])
            batch = self.state()['active']['id']
            self.assertEqual(len(m.claim(self.vault, 'p', batch)['turns']), 2)

    def test_disable_during_cooldown_preserves_pending(self):
        m.collect(self.vault, self.event())
        state = self.state()
        state['last_dispatched_at'] = m.time.time()
        m.binding.save(m.state_path(self.vault, 'p'), state)
        def sleep(seconds):
            m.configure(self.vault, 'p', False)
        with patch.object(m, 'DISPATCH_INTERVAL', 600), \
                patch.object(m.time, 'sleep', side_effect=sleep):
            with self.assertRaises(ValueError):
                self.dispatch()
        self.assertIsNone(self.state()['active'])
        self.assertIn('dev/t1', self.state()['pending'])

    def test_uninstall_cancels_automatic_wait_but_preserves_manual_dispatch(self):
        m.hooks(self.vault, True)
        m.collect(self.vault, self.event())
        state = self.state()
        state['last_dispatched_at'] = m.time.time()
        m.binding.save(m.state_path(self.vault, 'p'), state)
        with patch.object(m, 'DISPATCH_INTERVAL', 600), \
                patch.object(m.time, 'sleep', side_effect=lambda _: m.hooks(self.vault, False)), \
                patch.object(m.subprocess, 'run') as run:
            m.dispatch(self.vault, 'p', run, automatic=True)
            run.assert_not_called()
            # A delayed Hook subprocess starting after uninstall must also stop.
            m.dispatch(self.vault, 'p', run, automatic=True)
            run.assert_not_called()
        self.assertIsNone(self.state()['active'])
        self.assertIn('dev/t1', self.state()['pending'])
        self.assertTrue(m.selected(self.vault, 'p')['raw_auto_enabled'])
        self.assertEqual(len(self.dispatch()), 1)

    def test_hook_marks_delivery_as_automatic(self):
        import io
        with patch.object(m.sys, 'argv', ['monitor', 'hook', '--vault-root', str(self.vault)]), \
                patch.object(m.sys, 'stdin', io.StringIO(json.dumps(self.event()))), \
                patch.object(m.sys, 'stdout', io.StringIO()), \
                patch.object(m, 'collect', return_value='p'), \
                patch.object(m.subprocess, 'Popen') as spawn:
            m.main()
        self.assertIn('--automatic', spawn.call_args.args[0])

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
