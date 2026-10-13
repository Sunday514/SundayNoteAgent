"""规则镜像的路径、范围、版本与更新边界。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/sunday-note-project/scripts'))
import mirror_rules as mirror
import project_binding as binding


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.base / 'config')})
        self.env.start()
        self.vault = self.base / 'vault'
        self.project = self.vault / '工作项目/示例'
        self.project.mkdir(parents=True)
        self.source = self.base / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        (self.source / 'AGENTS.md').write_text('根规则')
        self.skill = self.source / '.agents/skills/demo'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('按需核查来源')
        (self.skill / 'reference.md').write_text('不扩大授权')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')
        config = binding.config_path()
        config.parent.mkdir(parents=True)
        binding.save(config, {str(self.vault): {'demo': {
            'project_path': '工作项目/示例', 'sources': [binding.source_identity(self.source)]}}})
        self.target = self.project / '项目维护/Agent规则/example/demo'

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def sync(self, apply=True, source=None):
        return mirror.mirror(self.vault, 'demo', source or self.source, 'example/demo', apply)

    def test_lifecycle_and_scope(self):
        (self.source / 'README.md').write_text('其他文档不复制')
        (self.skill / 'script.py').write_text('不复制代码')
        self.assertEqual(len(self.sync(False)['changes']), 3)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.sync()['verification']['ok'])
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertFalse((self.target / 'README.md').exists())
        self.assertFalse((self.target / '.agents/skills/demo/script.py').exists())
        manifest = self.target / 'manifest.json'
        stamp = manifest.stat().st_mtime_ns
        self.assertEqual(self.sync()['changes'], [])
        self.assertEqual(manifest.stat().st_mtime_ns, stamp)
        (self.source / 'AGENTS.md').write_text('已更新')
        (self.skill / 'reference.md').unlink()
        (self.target / '人工.md').write_text('不得删除')
        self.assertEqual({c['action'] for c in self.sync()['changes']}, {'update', 'delete'})
        self.assertEqual(self.sync()['changes'], [])
        self.assertEqual((self.target / '人工.md').read_text(), '不得删除')
        self.assertFalse(json.loads(manifest.read_text())['files']['AGENTS.md']['matches_head'])

    def test_conflict_prevents_entire_sync(self):
        self.sync()
        (self.target / 'AGENTS.mirror.md').write_text('人工修改')
        (self.skill / 'SKILL.md').write_text('新版本')
        target = self.target / '.agents/skills/demo/SKILL.mirror.md'
        before = target.read_bytes()
        self.assertEqual(self.sync()['conflicts'], ['AGENTS.md'])
        self.assertEqual(target.read_bytes(), before)
        (self.source / 'AGENTS.md').unlink()
        self.assertEqual(self.sync()['conflicts'], ['AGENTS.md'])

    def test_local_deletion_and_unmanaged_collision(self):
        self.target.mkdir(parents=True)
        file = self.target / 'AGENTS.mirror.md'
        file.write_text('人工作品')
        self.assertEqual(self.sync()['conflicts'], ['AGENTS.md'])
        file.unlink()
        self.sync()
        file.unlink()
        self.assertEqual(self.sync()['conflicts'], ['AGENTS.md'])

    def test_offline_copy_and_portable_manifest(self):
        self.sync()
        moved = self.base / 'another-device'
        shutil.copytree(self.target, moved)
        shutil.rmtree(self.source)
        self.assertTrue(mirror.verify(moved)['ok'])
        self.assertNotIn(str(self.base), (moved / 'manifest.json').read_text())
        with self.assertRaises(ValueError):
            self.sync()
        (moved / 'AGENTS.mirror.md').write_text('漂移')
        self.assertFalse(mirror.verify(moved)['ok'])

    def test_worktree_and_wrong_repository(self):
        wt = self.base / 'worktree'
        self.git('worktree', 'add', '--detach', str(wt))
        self.assertTrue(self.sync(source=wt)['verification']['ok'])
        other = self.base / 'unbound'
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        with self.assertRaises(ValueError):
            self.sync(source=other)

    def test_internal_tool_link_and_version(self):
        tools = self.source / 'tools'
        self.skill.rename(tools)
        self.skill.symlink_to(tools, target_is_directory=True)
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'tool entry')
        self.sync()
        item = json.loads((self.target / 'manifest.json').read_text())['files']['.agents/skills/demo/SKILL.md']
        self.assertEqual(item['source_path'], 'tools/SKILL.md')
        self.assertTrue(item['matches_head'])

    def test_external_and_loop_links_rejected(self):
        link = self.source / '.agents/link'
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.sync()
        link.unlink()
        link.symlink_to(self.source / '.agents', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.sync()
        link.unlink()
        link.symlink_to(self.source / 'missing', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.sync()

    def test_override_and_new_markdown(self):
        (self.source / 'AGENTS.override.md').write_text('覆盖规则')
        (self.skill / 'new.md').write_text('工具内新增说明')
        self.sync()
        files = json.loads((self.target / 'manifest.json').read_text())['files']
        self.assertNotIn('AGENTS.md', files)
        self.assertFalse(files['.agents/skills/demo/new.md']['matches_head'])

    def test_invalid_manifest(self):
        self.sync()
        manifest = self.target / 'manifest.json'
        value = json.loads(manifest.read_text())
        value['files']['../outside'] = value['files'].pop('AGENTS.md')
        manifest.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.sync()
        manifest.write_text('[]')
        with self.assertRaises(ValueError):
            self.sync()

    def test_cli(self):
        script = ROOT / 'skills/sunday-note-project/scripts/mirror_rules.py'
        p = subprocess.run([sys.executable, str(script), 'sync', '--vault-root', str(self.vault),
                            '--project-id', 'demo', '--source', str(self.source),
                            '--repository', 'example/demo'], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == '__main__':
    unittest.main()
