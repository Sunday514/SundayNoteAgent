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

    def test_register_connects_and_preserves_pause_and_history(self):
        args = ('register', '--project-id', 'a', '--project-path', '工作项目/甲',
                '--raw-path', '工作资料/甲', '--source', str(self.src),
                '--session-id', 'chosen', '--codex', '/usr/bin/true')
        value = self.command(*args)
        self.assertTrue(value['raw_auto_enabled'])
        self.assertEqual(value['maintenance']['session_id'], 'chosen')
        state = self.vault / '.import_files/sunday-note-agent/a/state.json'
        data = json.loads(state.read_text())
        data['results']['previous'] = {'result': 'keep'}
        state.write_text(json.dumps(data))
        self.assertTrue(self.command(*args)['raw_auto_enabled'])
        self.assertTrue(self.register()['raw_auto_enabled'])
        bindings = json.loads(self.config.read_text())
        bindings[str(self.vault)]['a']['raw_auto_enabled'] = False
        self.config.write_text(json.dumps(bindings))
        self.assertFalse(self.command(*args)['raw_auto_enabled'])
        self.assertEqual(json.loads(state.read_text())['results'], data['results'])
        before = self.config.read_bytes()
        self.command(*args[:-1], '/missing/runtime', ok=False)
        self.assertEqual(self.config.read_bytes(), before)
        self.command(*args[:-2], ok=False)

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

    def test_wiki_targets_replace_clear_and_preserve_binding(self):
        self.register()
        self.command('bind', '--project-id', 'a', '--client', 'codex', '--session-id', 'maint')
        folder = self.vault / '知识库/主题'
        folder.mkdir(parents=True)
        pages = ['知识库/主题/方法.md', '知识库/主题/边界.md']
        for page in pages:
            (self.vault / page).write_text('人工内容')
        value = self.command('wiki-targets', '--project-id', 'a', '--page', pages[0], '--page', pages[1])
        self.assertEqual(value['wiki_targets'], sorted(pages))
        self.assertEqual(value['maintenance']['session_id'], 'maint')
        self.assertFalse(value['raw_auto_enabled'])
        self.assertEqual(self.register()['wiki_targets'], sorted(pages))
        self.assertEqual(self.command('wiki-targets', '--project-id', 'a', '--page', pages[0])['wiki_targets'], pages[:1])
        self.assertEqual(self.command('wiki-targets', '--project-id', 'a', '--clear')['wiki_targets'], [])
        self.assertEqual((self.vault / pages[0]).read_text(), '人工内容')

    def test_wiki_targets_reject_invalid_without_changing_authorization(self):
        self.register()
        folder = self.vault / '知识库/主题'
        folder.mkdir(parents=True)
        (folder / '有效.md').write_text('原文')
        (folder / 'link.md').symlink_to(folder / '有效.md')
        (folder / 'sub').symlink_to(folder, target_is_directory=True)
        (folder / 'data.txt').write_text('文本')
        (folder / 'wild*.md').write_text('不能作为通配授权')
        self.command('wiki-targets', '--project-id', 'a', '--page', '知识库/主题/有效.md')
        before = self.config.read_bytes()
        for page in ('/tmp/other.md', '../other.md', '知识库/../工作资料/a.md',
                     '工作资料/a.md', '知识库/主题', '知识库/主题/missing.md',
                     '知识库/主题/link.md', '知识库/主题/sub/有效.md',
                     '知识库/主题/data.txt', '知识库/主题/wild*.md'):
            self.command('wiki-targets', '--project-id', 'a', '--page', page, ok=False)
            self.assertEqual(self.config.read_bytes(), before)
        self.command('wiki-targets', '--project-id', 'a', ok=False)
        self.command('wiki-targets', '--project-id', 'a', '--clear', '--page', '知识库/主题/有效.md', ok=False)


if __name__ == '__main__':
    unittest.main()
