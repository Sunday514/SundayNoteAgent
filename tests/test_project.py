"""项目绑定边界；全部使用临时目录，不修改真实配置。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/sunday-note-project/scripts/project_binding.py'


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'vault'
        self.project = self.vault / '工作项目/甲'
        self.project.mkdir(parents=True)
        (self.project / '人工.md').write_text('保留正文')
        self.env = {**os.environ, 'XDG_CONFIG_HOME': str(self.root / 'config')}
        self.config = self.root / 'config/sunday-note-agent/project-bindings.json'
        self.src = self.root / 'repo'
        self.src.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(SCRIPT), *args, '--vault-root', str(self.vault)],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode == 0, ok, p.stderr)
        return json.loads(p.stdout) if ok else p.stderr

    def register(self, pid='a', source=None, **kwargs):
        return self.command('register', '--project-id', pid, '--project-path', '工作项目/甲',
                            '--raw-path', '工作资料/甲', '--source', str(source or self.src), **kwargs)

    def test_register_idempotent_bind_and_unbind(self):
        first = self.register()
        self.assertEqual(first, self.register())
        self.assertIsNone(first['maintenance'])
        self.assertFalse(first['raw_auto_enabled'])
        self.assertFalse((self.vault / '工作资料').exists())
        self.assertFalse((self.config.parent / 'config.json').exists())
        self.command('bind', '--project-id', 'a', '--client', 'codex', '--session-id', 'chosen')
        self.assertEqual(self.register()['maintenance']['session_id'], 'chosen')
        self.command('bind', '--project-id', 'a', '--client', 'codex', '--session-id', 'replacement')
        self.assertIsNone(self.command('unbind', '--project-id', 'a')['maintenance'])
        self.assertEqual((self.project / '人工.md').read_text(), '保留正文')

    def test_git_worktree_and_conflict(self):
        subprocess.run(['git', 'init', '-q', str(self.src)], check=True)
        subprocess.run(['git', '-C', str(self.src), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'init'], check=True)
        wt = self.root / 'worktree'
        subprocess.run(['git', '-C', str(self.src), 'worktree', 'add', '--detach', str(wt)],
                       check=True, capture_output=True)
        first = self.register()
        second = self.register(source=wt)
        self.assertEqual(first['sources'][0]['identity'], second['sources'][0]['identity'])
        self.register(pid='b', ok=False)
        (self.vault / '工作项目/乙').mkdir()
        self.command('register', '--project-id', 'b', '--project-path', '工作项目/乙',
                     '--raw-path', '工作资料/乙', '--source', str(self.src), ok=False)

    def test_invalid_paths_and_missing(self):
        self.command('bind', '--project-id', 'missing', '--client', 'codex', '--session-id', 'x', ok=False)
        self.command('register', '--project-id', 'a', '--project-path', '../outside',
                     '--raw-path', '工作资料/甲', ok=False)
        link = self.vault / '工作项目/link'
        link.symlink_to(self.project, target_is_directory=True)
        self.command('register', '--project-id', 'a', '--project-path', '工作项目/link',
                     '--raw-path', '工作资料/甲', ok=False)
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.symlink_to(self.root / 'elsewhere')
        self.register(ok=False)

    def test_parallel_and_vault_isolation(self):
        processes = []
        for i in range(6):
            vault = self.root / f'vault-{i}'
            (vault / '工作项目/甲').mkdir(parents=True)
            processes.append(subprocess.Popen([sys.executable, str(SCRIPT), 'register', '--vault-root',
                str(vault), '--project-id', 'a', '--project-path', '工作项目/甲', '--raw-path',
                '工作资料/甲'], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for p in processes:
            _, err = p.communicate()
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(len(json.loads(self.config.read_text())), 6)
        self.assertEqual(self.command('list'), {})


if __name__ == '__main__':
    unittest.main()
