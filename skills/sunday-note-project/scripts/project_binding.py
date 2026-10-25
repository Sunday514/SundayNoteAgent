#!/usr/bin/env python3
"""登记本机项目来源与维护入口；不创建文档、不授予回流权限。"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def safe_path(value):
    path = Path(os.path.abspath(value))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"拒绝符号链接: {path}")
    return path


def relative(vault, value, roots):
    p = Path(value)
    if p.is_absolute() or not p.parts or '..' in p.parts:
        raise ValueError("需要 Vault 内相对路径")
    if p.parts[0] not in roots or (len(p.parts) < 2 and p.parts[0] != 'SundayNoteAgent项目'):
        raise ValueError("目标不属于项目或 Raw 集合")
    safe_path(vault / p)
    return p.as_posix()


def source_identity(value):
    path = safe_path(value)
    if not path.is_dir():
        raise ValueError(f"来源目录不存在: {path}")
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    env['LC_ALL'] = 'C'
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute',
                             '--git-common-dir'], env=env, capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        return {'kind': 'git', 'path': str(path), 'identity': str(safe_path(result.stdout.strip()))}
    if 'not a git repository' not in result.stderr.lower():
        raise ValueError(f"无法核实 Git 来源: {path}")
    return {'kind': 'directory', 'path': str(path), 'identity': str(path)}


def wiki_page(vault, value):
    if any(c in value for c in '*?[]'):
        raise ValueError('Wiki 授权不接受通配符')
    rel = relative(vault, value, {'知识库'})
    path = vault / rel
    if path.suffix.lower() != '.md' or not path.is_file():
        raise ValueError('Wiki 授权只接受已存在的 Markdown 页面')
    return rel


def config_path():
    return safe_path(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) /
                     'sunday-note-agent/project-bindings.json')


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = safe_path(path.with_suffix('.lock'))
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def read(path):
    safe_path(path)
    value = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(value, dict) or any(not isinstance(v, dict) for v in value.values()):
        raise ValueError('无效项目绑定文件')
    return value


def save(path, value):
    safe_path(path)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def overlaps(left, right):
    if left['kind'] == right['kind'] == 'git':
        return left['identity'] == right['identity']
    a, b = Path(left['path']), Path(right['path'])
    return a.is_relative_to(b) or b.is_relative_to(a)


def prepare_runtime(vault, project, runtime):
    runtime = Path(runtime)
    if not runtime.is_absolute() or not safe_path(runtime).is_file() or not os.access(runtime, os.X_OK):
        raise ValueError('需要已核实的 App Codex 可执行文件绝对路径')
    target = safe_path(vault / '.import_files/sunday-note-agent' / project / 'state.json')
    with locked(target):
        state = json.loads(target.read_text()) if target.exists() else {
            'pending': {}, 'history': {}, 'active': None, 'issues': {}, 'results': {}}
        state['runtime'] = {'codex': str(runtime), 'codex_home': str(safe_path(
            Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))))}
        save(target, state)


def run(args):
    vault = safe_path(args.vault_root)
    if not vault.is_dir():
        raise ValueError('Vault 不存在')
    path = config_path()
    if args.command == 'list':
        return read(path).get(str(vault), {})
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.project_id):
        raise ValueError('项目标识须为简短字母数字、下划线或连字符')
    with locked(path):
        data = read(path)
        projects = data.setdefault(str(vault), {})
        old = projects.get(args.project_id)
        if args.command == 'register':
            project = relative(vault, args.project_path, {'工作项目', '个人项目', 'SundayNoteAgent项目'})
            raw = relative(vault, args.raw_path, {'工作资料', '个人资料'})
            if not (vault / project).is_dir():
                raise ValueError('请先建立或确认项目文档目录')
            if (project.startswith('个人项目/') != raw.startswith('个人资料/')):
                raise ValueError('项目与 Raw 归属不一致')
            sources = []
            for value in args.source:
                src = source_identity(value)
                if not any(s['identity'] == src['identity'] for s in sources):
                    sources.append(src)
            for vid, entries in data.items():
                for pid, entry in entries.items():
                    if (vid, pid) == (str(vault), args.project_id):
                        continue
                    if vid == str(vault) and entry['project_path'] == project:
                        raise ValueError('项目目录已绑定其他标识')
                    if any(overlaps(a, b) for a in sources for b in entry['sources']):
                        raise ValueError('来源已绑定其他项目或 Vault')
            session = getattr(args, 'session_id', None)
            runtime = getattr(args, 'codex', None)
            if bool(session) != bool(runtime):
                raise ValueError('完整接入需同时提供维护会话和 App Codex 路径')
            if session and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session):
                raise ValueError('无效会话 ID')
            value = {**(old or {}), 'project_path': project, 'raw_path': raw, 'sources': sources,
                     'maintenance': (old or {}).get('maintenance'),
                     'raw_auto_enabled': (old or {}).get('raw_auto_enabled', False)}
            if session:
                prepare_runtime(vault, args.project_id, runtime)
                value['maintenance'] = {'client': 'codex', 'session_id': session}
                # 已有开关代表用户选择，重登记不能擅自恢复暂停。
                value['raw_auto_enabled'] = old.get('raw_auto_enabled', False) if old else True
        else:
            if old is None:
                raise ValueError('项目尚未登记')
            value = dict(old)
            if args.command == 'wiki-targets':
                value['wiki_targets'] = sorted({wiki_page(vault, p) for p in (args.page or [])})
            elif args.command == 'bind':
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', args.session_id):
                    raise ValueError('无效会话 ID')
                if not (vault / relative(vault, old['project_path'], {'工作项目', '个人项目', 'SundayNoteAgent项目'})).is_dir():
                    raise ValueError('项目目录已失效')
                value['maintenance'] = {'client': args.client, 'session_id': args.session_id}
            else:
                value['maintenance'] = None
        projects[args.project_id] = value
        save(path, data)
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('list', 'register', 'bind', 'unbind', 'wiki-targets'):
        p = sub.add_parser(command)
        p.add_argument('--vault-root', required=True)
        if command != 'list':
            p.add_argument('--project-id', required=True)
        if command == 'register':
            p.add_argument('--project-path', required=True)
            p.add_argument('--raw-path', required=True)
            p.add_argument('--source', action='append', default=[])
            p.add_argument('--session-id')
            p.add_argument('--codex', help='已核实的 App Codex 绝对路径')
        if command == 'bind':
            p.add_argument('--client', choices=['codex'], required=True)
            p.add_argument('--session-id', required=True)
        if command == 'wiki-targets':
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument('--page', action='append')
            group.add_argument('--clear', action='store_true')
    try:
        print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
