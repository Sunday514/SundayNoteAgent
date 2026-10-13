#!/usr/bin/env python3
"""镜像项目根规则及 .agents 下的 Markdown，不执行副本或改变同步授权。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import project_binding as binding


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(value):
    if not isinstance(value, str):
        raise ValueError('文件路径必须是字符串')
    path = Path(value)
    if (not value or value == '.' or path.is_absolute()
            or '..' in path.parts or '.git' in path.parts or path.as_posix() != value):
        raise ValueError(f'需要规范的仓库相对文件路径: {value}')
    return path


def repository(value):
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)?', value):
        raise ValueError('仓库标识使用可迁移的名称或 owner/name，不使用网址或本机路径')
    return value


def git(source, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(LC_ALL='C', GIT_OPTIONAL_LOCKS='0')
    return subprocess.run(['git', '-C', str(source), *args], env=env,
                          capture_output=True, check=True, timeout=10).stdout


def atomic_write(path, data):
    binding.safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_manifest(root):
    path = binding.safe_path(root / 'manifest.json')
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get('format') != 1 or not isinstance(data.get('files'), dict)
            or not re.fullmatch(r'[0-9a-f]{40,64}', data.get('commit', ''))):
        raise ValueError('无效镜像清单')
    repository(data.get('repository', ''))
    for name, entry in data['files'].items():
        relative(name)
        if (not isinstance(entry, dict) or not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', ''))
                or not isinstance(entry.get('matches_head'), bool)
                or entry.get('mirror_path') != copy_name(name) or copy_name(name) == 'manifest.json'):
            raise ValueError(f'无效文件身份: {name}')
    if len({copy_name(n) for n in data['files']}) != len(data['files']):
        raise ValueError('镜像清单路径冲突')
    return data


def copy_name(name):
    path = relative(name)
    if path.name in {'AGENTS.md', 'AGENTS.override.md', 'SKILL.md'}:
        path = path.with_name(path.stem + '.mirror.md')
    return path.as_posix()


def copy_path(root, name):
    return binding.safe_path(root / copy_name(name))


def verify(root):
    root = binding.safe_path(root)
    manifest = read_manifest(root)
    if manifest is None:
        raise ValueError('镜像清单不存在')
    errors = []
    for name, entry in manifest['files'].items():
        path = copy_path(root, name)
        if not path.is_file() or digest(path.read_bytes()) != entry['sha256']:
            errors.append(name)
    return {'ok': not errors, 'repository': manifest['repository'], 'commit': manifest['commit'],
            'files': len(manifest['files']), 'identity_errors': errors,
            'freshness': '仅核验副本与清单一致；不证明源码当前仍为此版本'}


def documents(source):
    result = {}

    def visit(path, ancestors):
        resolved = path.resolve()
        if not resolved.is_relative_to(source):
            raise ValueError(f'工具入口指向仓库外: {path}')
        if path.is_symlink() and not path.exists():
            raise ValueError(f'工具入口失效: {path}')
        if path.is_dir():
            if resolved in ancestors:
                raise ValueError(f'工具入口循环: {path}')
            for child in sorted(path.iterdir()):
                visit(child, ancestors | {resolved})
        elif path.suffix == '.md':
            if not path.is_file():
                raise ValueError(f'文档入口失效: {path}')
            result[path.relative_to(source).as_posix()] = resolved

    root_rule = source / 'AGENTS.override.md'
    if not root_rule.exists() and not root_rule.is_symlink():
        root_rule = source / 'AGENTS.md'
    if root_rule.exists() or root_rule.is_symlink():
        visit(root_rule, set())
    if (source / '.agents').exists() or (source / '.agents').is_symlink():
        visit(source / '.agents', set())
    return result


def prepare(vault, project_id, source, repo):
    entries = binding.read(binding.config_path()).get(str(vault), {})
    if project_id not in entries:
        raise ValueError('项目尚未登记')
    entry = entries[project_id]
    project = binding.relative(vault, entry['project_path'],
                               {'工作项目', '个人项目', 'SundayNoteAgent项目'})
    if not (vault / project).is_dir():
        raise ValueError('项目文档目录不存在')
    source = binding.safe_path(source)
    identity = binding.source_identity(source)
    if identity['kind'] != 'git' or not any(
            s['kind'] == 'git' and s['identity'] == identity['identity'] for s in entry['sources']):
        raise ValueError('需要已绑定的 Git 仓库或其 worktree')
    if Path(git(source, 'rev-parse', '--show-toplevel').decode().strip()) != source:
        raise ValueError('来源必须是仓库根目录')
    repo = repository(repo)
    root = binding.safe_path(vault / project / '项目维护/Agent规则' / repo)
    old = read_manifest(root)
    if old and old['repository'] != repo:
        raise ValueError('镜像仓库标识不一致')
    paths = documents(source)
    targets = [copy_name(name) for name in paths]
    if len(targets) != len(set(targets)):
        raise ValueError('改名后的副本路径存在冲突')
    commit = git(source, 'rev-parse', 'HEAD').decode().strip()
    bodies, files = {}, {}
    for name, path in paths.items():
        body = path.read_bytes()
        body.decode('utf-8')
        origin = path.relative_to(source).as_posix()
        try:
            head = git(source, 'show', f'{commit}:{origin}')
        except subprocess.CalledProcessError:
            head = None
        files[name] = {'source_path': origin, 'mirror_path': copy_name(name),
                       'sha256': digest(body), 'matches_head': body == head}
        bodies[name] = body
    desired = {'format': 1, 'repository': repo, 'commit': commit, 'files': files}
    changes, conflicts = [], []
    for name in sorted(set((old or {}).get('files', {})) | set(files)):
        path = copy_path(root, name)
        previous = (old or {}).get('files', {}).get(name)
        current = digest(path.read_bytes()) if path.is_file() else None
        if any(p.exists() and not p.is_dir() for p in path.parents):
            conflicts.append(name)
        elif path.exists() and not path.is_file():
            conflicts.append(name)
        elif previous and current != previous['sha256']:
            conflicts.append(name)
        elif not previous and path.exists():
            conflicts.append(name)
        elif name not in files:
            changes.append({'path': name, 'action': 'delete'})
        elif current != files[name]['sha256']:
            changes.append({'path': name, 'action': 'update' if previous else 'add'})
    return root, old, desired, bodies, {'changes': changes, 'conflicts': conflicts,
                                      'manifest_changed': old != desired}


def mirror(vault, project_id, source, repo, apply=False):
    vault = binding.safe_path(vault)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', project_id):
        raise ValueError('无效项目标识')

    def execute():
        root, old, desired, bodies, report = prepare(vault, project_id, source, repo)
        if not apply or report['conflicts']:
            return report
        # 先核对全部目标，冲突时不做部分同步。锁不跨 Agent 执行持有。
        for change in report['changes']:
            path = copy_path(root, change['path'])
            if change['action'] == 'delete':
                path.unlink()
                parent = path.parent
                while parent != root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            else:
                atomic_write(path, bodies[change['path']])
        if old != desired:
            atomic_write(root / 'manifest.json',
                         (json.dumps(desired, ensure_ascii=False, indent=2) + '\n').encode())
        report['verification'] = verify(root)
        return report

    if not apply:
        return execute()
    lock = binding.safe_path(vault / '.import_files/sunday-note-agent' / project_id / 'mirror.lock')
    with binding.locked(lock):
        return execute()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('plan', 'sync'):
        p = sub.add_parser(command)
        p.add_argument('--vault-root', required=True)
        p.add_argument('--project-id', required=True)
        p.add_argument('--source', type=Path, required=True)
        p.add_argument('--repository', required=True)
    p = sub.add_parser('verify')
    p.add_argument('--mirror-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (verify(args.mirror_root) if args.command == 'verify' else
                  mirror(args.vault_root, args.project_id, args.source, args.repository,
                         args.command == 'sync'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('conflicts') or result.get('ok') is False:
            parser.exit(1)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'规则镜像失败: {exc}\n')


if __name__ == '__main__':
    main()
