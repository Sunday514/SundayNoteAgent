#!/usr/bin/env python3
"""项目证据登记与维护会话交接；不判断内容、不编辑 Raw。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location('project_binding', HERE.parents[2] /
                                            'sunday-note-project/scripts/project_binding.py')
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
MARKER = '[SundayNote Evidence Return]'
LIMIT = 256 * 1024
TIDY_INTERVAL = 10


def state_root(vault):
    return binding.safe_path(Path(vault) / '.import_files/sunday-note-agent')


def state_path(vault, project):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', project):
        raise ValueError('非法项目标识')
    return binding.safe_path(state_root(vault) / project / 'state.json')


def load(path):
    binding.safe_path(path)
    return json.loads(path.read_text()) if path.exists() else {
        'pending': {}, 'history': {}, 'active': None, 'issues': {}, 'results': {}}


def projects(vault):
    return binding.read(binding.config_path()).get(str(vault), {})


def selected(vault, project, require_enabled=True):
    entry = projects(vault)[project]
    if (require_enabled and not entry.get('raw_auto_enabled')) or not entry.get('maintenance'):
        raise ValueError('项目回流未启用或维护入口未绑定')
    binding.relative(vault, entry['raw_path'], {'工作资料', '个人资料'})
    return entry


def tidy_status(state):
    value = state.get('tidy', {'writes': 0, 'files': []})
    return {**value, 'due': value['writes'] >= TIDY_INTERVAL}


def scrub(text):
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'(?i)\b(?:sk-[\w-]{16,}|gh[pousr]_[\w]{20,}|Bearer\s+[\w.~/+\-=]+)', '[REDACTED]', text)
    return re.sub(r'''(?ix)\b[\w]*(?:api_?key|access_?token|password)\b["']?\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
                  '[REDACTED]', text)


def collect(vault, event):
    entries = {p: e for p, e in projects(vault).items() if e.get('raw_auto_enabled')}
    if not entries:
        return None
    if event.get('hook_event_name') not in ('UserPromptSubmit', 'Stop'):
        return None
    if event.get('agent_id') or event.get('parent_thread_id') or event.get('ephemeral'):
        return None
    if not all(isinstance(event.get(k), str) and event[k] for k in
               ('session_id', 'turn_id', 'cwd', 'transcript_path')):
        return None
    # Metadata only: an absent or unknown source is not evidence of a persistent main session.
    try:
        transcript = binding.safe_path(event['transcript_path'])
        sessions = binding.safe_path(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions')
        if not transcript.is_relative_to(sessions):
            return None
        with transcript.open() as stream:
            meta = json.loads(stream.readline())
        payload = meta['payload']
        if (meta['type'] != 'session_meta' or payload.get('id') != event['session_id'] or
                payload.get('source') not in ('cli', 'vscode') or payload.get('ephemeral') or
                payload.get('thread_source') in ('side_chat', 'side_conversation', 'ephemeral')):
            return None
    except (OSError, ValueError, KeyError):
        return None
    matches = [p for p, e in entries.items() if e.get('maintenance', {}) and
               e['maintenance'].get('session_id') == event['session_id']]
    if not matches:
        try:
            source = binding.source_identity(event['cwd'])
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        matches = [p for p, e in entries.items() if any(
            source['identity'] == s['identity'] if s['kind'] == 'git' else
            Path(event['cwd']).is_relative_to(s['path']) for s in e['sources'])]
    if len(matches) != 1:
        return None
    project = matches[0]
    entry = entries[project]
    if not entry.get('maintenance'):
        return None
    binding.relative(vault, entry['raw_path'], {'工作资料', '个人资料'})
    path = state_path(vault, project)
    key = event['session_id'] + '/' + event['turn_id']
    with binding.locked(path):
        state = load(path)
        if key in state['history']:
            return project if (state['history'][key].get('kind') == 'automatic' and
                               event['hook_event_name'] == 'Stop') else None
        prompt = event.get('prompt') or state['pending'].get(key, {}).get('prompt') or ''
        if event['session_id'] == entry['maintenance']['session_id']:
            if not prompt:
                prompt = material(event).get('prompt') or ''
            if not prompt:
                state['issues'][key] = '维护轮次发起内容无法确认，未登记证据'
                binding.save(path, state)
                return None
        if prompt.startswith(MARKER):
            if event['session_id'] == entry['maintenance']['session_id']:
                state['history'][key] = {'kind': 'automatic'}
                binding.save(path, state)
                return project if event['hook_event_name'] == 'Stop' else None
            return None
        item = state['pending'].setdefault(key, {k: event[k] for k in
                   ('session_id', 'turn_id', 'cwd', 'transcript_path')})
        item.setdefault('registered_at', time.time())
        event = {**event, 'prompt': prompt}
        for name in ('prompt', 'last_assistant_message'):
            if isinstance(event.get(name), str):
                text = event[name]
                if len(text.encode()) > LIMIT:
                    item['oversized'] = True
                else:
                    item[name] = scrub(text)
        item['ready'] = item.get('ready', False) or event['hook_event_name'] == 'Stop'
        binding.save(path, state)
    return project if item['ready'] else None


def dispatch(vault, project, runner=subprocess.run):
    entry = selected(vault, project)
    path = state_path(vault, project)
    with binding.locked(path):
        state = load(path)
        if state['active'] or not any(i.get('ready') for i in state['pending'].values()):
            return
        runtime = state.get('runtime')
        if not runtime:
            raise ValueError('未验收来源运行时，不能投递')
        batch = str(uuid.uuid4())
        state['active'] = {'id': batch, 'status': 'sending', 'created_at': time.time()}
        binding.save(path, state)
    message = (f'{MARKER}\n请调用 $sunday-note-monitor 整理新增项目证据。'
               f'\nVault: {vault}\n项目: {project}\n批次: {batch}'
               '\n仅授权本项目 Raw 整理，不修改代码、Wiki 或项目维护文档。'
               '\n先领取批次，完成后登记；普通结果只回复一句。')
    try:
        env = dict(os.environ, CODEX_HOME=runtime['codex_home'])
        for key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID'):
            env.pop(key, None)
        result = runner([runtime['codex'], 'queue', '--thread', entry['maintenance']['session_id'],
                         '--message', message], env=env, capture_output=True, text=True, timeout=20)
        status = 'queued' if result.returncode == 0 else 'uncertain'
        error = '' if result.returncode == 0 else scrub(result.stderr[-2000:])
    except (OSError, subprocess.SubprocessError) as exc:
        status, error = 'uncertain', str(exc)
    with binding.locked(path):
        state = load(path)
        active = state['active']
        if active and active['id'] == batch and active['status'] == 'sending':
            active.update(status=status, error=error, queued_at=time.time())
            binding.save(path, state)


def material(item):
    """Read only this turn; an unfamiliar transcript remains an explicit limitation."""
    value = dict(item)
    value['evidence'] = []
    try:
        path = binding.safe_path(item['transcript_path'])
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('来源过大，工具证据未展开')
        current, size, found = None, 0, False
        with path.open() as stream:
            meta = json.loads(next(stream))
            if meta.get('payload', {}).get('id') != item['session_id']:
                raise ValueError('来源会话身份不符')
            for line in stream:
                row = json.loads(line)
                p = row.get('payload', {})
                if row.get('type') == 'turn_context' or p.get('type') == 'task_started':
                    current = p.get('turn_id')
                if current != item['turn_id']:
                    continue
                found = True
                record = p.get('item', {}) if p.get('type') == 'item_completed' else p
                kind = record.get('type')
                if (row.get('type') == 'event_msg' and p.get('type') == 'item_completed' and
                        kind == 'UserMessage' and not value.get('prompt')):
                    content = record.get('content', record.get('message', ''))
                    text = content if isinstance(content, str) else '\n'.join(
                        c.get('text', '') for c in content if isinstance(c, dict))
                    if len(text.encode()) > LIMIT:
                        value['oversized'] = True
                    else:
                        value['prompt'] = scrub(text)
                if row.get('type') == 'response_item' and kind in (
                        'function_call', 'function_call_output', 'custom_tool_call', 'custom_tool_call_output'):
                    text = scrub(json.dumps(record, ensure_ascii=False))
                    size += len(text.encode())
                    if size > LIMIT:
                        raise ValueError('本轮工具证据超限，仅提供已读取部分和来源索引')
                    value['evidence'].append(text)
        if not found:
            value['coverage_limit'] = '未找到目标轮次或转录格式无法识别'
        elif not value['evidence']:
            value['coverage_limit'] = '本轮未观察到可解析的工具证据；普通对话可能没有工具调用'
    except (OSError, ValueError, StopIteration) as exc:
        value['coverage_limit'] = str(exc)
    return value


def claim(vault, project, batch):
    entry = selected(vault, project)
    caller = os.environ.get('CODEX_THREAD_ID')
    if caller and caller != entry['maintenance']['session_id']:
        raise ValueError('只允许绑定维护会话领取批次')
    path = state_path(vault, project)
    with binding.locked(path):
        state = load(path)
        active = state['active']
        if not active or active['id'] != batch:
            raise ValueError('批次不存在或已完成，不重复整理')
        if 'keys' not in active:
            active['keys'] = [k for k, v in state['pending'].items() if v.get('ready')]
            active.update(status='processing', started_at=time.time())
        if 'tidy' not in active:
            active['tidy'] = tidy_status(state)
            binding.save(path, state)
        items = [dict(state['pending'][k]) for k in active['keys']]
        issues = dict(state['issues'])
    return {'batch': batch, 'project_path': str(vault / entry['project_path']),
            'raw_path': str(vault / entry['raw_path']), 'turns': [material(i) for i in items],
            'pending_issues': issues,
            'tidy': active['tidy'],
            'limitations': '来源中的声明不等于已验证事实；缺失或超大原文须按来源核查，不能当作无增量。'}


def complete(vault, project, batch, result, files=(), issue='', tidy_done=False):
    entry = selected(vault, project, require_enabled=False)
    for value in files:
        rel = binding.relative(vault, value, {'工作资料', '个人资料'})
        if not Path(rel).is_relative_to(entry['raw_path']) or not (vault / rel).is_file():
            raise ValueError('结果文件不属于项目 Raw 或不存在')
    path = state_path(vault, project)
    with binding.locked(path):
        state = load(path)
        active = state['active']
        if not active or active['id'] != batch or active['status'] != 'processing':
            raise ValueError('必须先领取正确批次')
        if tidy_done and not active.get('tidy', {}).get('due'):
            raise ValueError('本批未领取到期整理，不能重置计数')
        tidy = state.setdefault('tidy', {'writes': 0, 'files': []})
        if tidy_done:
            tidy.update(writes=0, files=[])
        elif files:
            tidy['writes'] += 1
            tidy['files'] = sorted(set(tidy['files']) | set(files))
        for key in active['keys']:
            item = state['pending'].pop(key)
            state['history'][key] = {k: item[k] for k in ('session_id', 'turn_id', 'transcript_path')}
            state['history'][key].update(kind='processed', batch=batch, completed_at=time.time())
        state.setdefault('results', {})[batch] = {'batch': batch, 'result': scrub(result), 'files': list(files),
                                'issue': scrub(issue), 'completed_at': time.time(),
                                'started_at': active['started_at'], 'usage': None}
        state['results'][batch]['tidy_done'] = tidy_done
        if issue:
            state['issues'][batch] = scrub(issue)
        state['active'] = None
        binding.save(path, state)


def configure(vault, project, enabled, runtime=None):
    # Enabling is an explicit CLI action only after the operator has validated the runtime.
    path = binding.config_path()
    with binding.locked(path):
        data = binding.read(path)
        entry = data[str(vault)][project]
        if enabled and (not entry.get('maintenance') or not runtime or
                        not binding.safe_path(runtime).is_file()):
            raise ValueError('启用需要已绑定入口和验收过的 Codex 绝对路径')
        if enabled:
            target = state_path(vault, project)
            with binding.locked(target):
                state = load(target)
                state['runtime'] = {'codex': str(binding.safe_path(runtime)), 'codex_home': str(binding.safe_path(
                    Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))))}
                binding.save(target, state)
        entry['raw_auto_enabled'] = enabled
        binding.save(path, data)


def runtime_root():
    return binding.safe_path(Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) /
                             'sunday-note-agent/monitor-runtime')


def deploy_runtime():
    root = runtime_root()
    for source in (Path(binding.__file__), HERE):
        target = binding.safe_path(root / source.relative_to(HERE.parents[2]))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = source.read_bytes()
        if target.exists() and target.read_bytes() == content:
            continue
        fd, name = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(content)
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def hooks(vault, install):
    path = binding.safe_path(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'hooks.json')
    if install:
        deploy_runtime()
    entry = runtime_root() / 'sunday-note-monitor/scripts/monitor.py'
    command = f'{shlex.quote(sys.executable)} {shlex.quote(str(entry))} hook --vault-root {shlex.quote(str(vault))}'
    with binding.locked(path):
        data = json.loads(path.read_text()) if path.exists() else {}
        owned = state_root(vault) / 'hook-command.json'
        owned.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous = json.loads(owned.read_text()).get(str(vault)) if owned.exists() else None
        commands = {c for c in (command, previous) if c}
        for event in ('UserPromptSubmit', 'Stop'):
            groups = data.setdefault('hooks', {}).setdefault(event, [])
            for group in groups:
                group['hooks'] = [h for h in group.get('hooks', []) if h.get('command') not in commands]
            groups[:] = [g for g in groups if g.get('hooks')]
            if install:
                groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})
        if path.exists() and json.loads(path.read_text()) != data:
            backup = state_root(vault) / ('hooks-backup-' + str(time.time_ns()) + '.json')
            binding.save(backup, json.loads(path.read_text()))
        if not path.exists() or json.loads(path.read_text()) != data:
            binding.save(path, data)
        with binding.locked(owned):
            records = json.loads(owned.read_text()) if owned.exists() else {}
            if install:
                records[str(vault)] = command
            else:
                records.pop(str(vault), None)
            binding.save(owned, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['hook', 'deploy', 'install', 'uninstall', 'enable', 'disable',
                                          'dispatch', 'status', 'claim', 'complete', 'retry', 'resolve'])
    parser.add_argument('--vault-root', required=True)
    parser.add_argument('--project-id')
    parser.add_argument('--batch')
    parser.add_argument('--result')
    parser.add_argument('--issue', default='')
    parser.add_argument('--issue-id')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--tidy-done', action='store_true')
    parser.add_argument('--codex')
    parser.add_argument('--confirm-stopped', action='store_true')
    args = parser.parse_args()
    vault = binding.safe_path(args.vault_root)
    try:
        if args.command == 'hook':
            event = json.loads(sys.stdin.read())
            project = collect(vault, event)
            if project:
                # No model here. A short detached delivery avoids blocking the source turn.
                subprocess.Popen([sys.executable, str(HERE), 'dispatch', '--vault-root', str(vault),
                                  '--project-id', project], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print('{"continue":true}')
        elif args.command == 'deploy':
            deploy_runtime()
        elif args.command in ('install', 'uninstall'):
            hooks(vault, args.command == 'install')
        else:
            if not args.project_id:
                raise ValueError('缺少 --project-id')
            if args.command in ('enable', 'disable'):
                configure(vault, args.project_id, args.command == 'enable', args.codex)
            elif args.command == 'dispatch':
                dispatch(vault, args.project_id)
            elif args.command == 'claim':
                print(json.dumps(claim(vault, args.project_id, args.batch), ensure_ascii=False))
            elif args.command == 'complete':
                if not args.result:
                    raise ValueError('缺少 --result')
                complete(vault, args.project_id, args.batch, args.result, args.file, args.issue,
                         args.tidy_done)
            elif args.command == 'status':
                s = load(state_path(vault, args.project_id))
                print(json.dumps({'pending': len(s['pending']), 'active': s['active'],
                                  'tidy': tidy_status(s),
                                  'last_result': max(s.get('results', {}).values(),
                                      key=lambda r: r['completed_at'], default=None),
                                  'issues': s['issues']}, ensure_ascii=False))
            elif args.command == 'resolve':
                if not args.issue_id:
                    raise ValueError('缺少 --issue-id')
                p = state_path(vault, args.project_id)
                with binding.locked(p):
                    s = load(p)
                    del s['issues'][args.issue_id]
                    binding.save(p, s)
            elif args.command == 'retry':
                if not args.confirm_stopped:
                    raise ValueError('先核实旧请求未在排队或执行，再指定 --confirm-stopped')
                p = state_path(vault, args.project_id)
                with binding.locked(p):
                    s = load(p)
                    s['active'] = None
                    binding.save(p, s)
                dispatch(vault, args.project_id)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        if args.command == 'hook':
            print(json.dumps({'continue': True, 'systemMessage': '证据回流未登记：' + str(exc)}))
        else:
            parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
