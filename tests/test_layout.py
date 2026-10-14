#!/usr/bin/env python3
"""布局迁移、权限与设备授权的脱敏集成测试。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import migrate_layout as ml
import sync_allowlist as sync


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.vault = self.home / "vault"
        self.vault.mkdir()

    def put(self, rel, text):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def make_plan(self, mapping):
        file = self.home / "mapping.json"
        file.write_text(json.dumps(mapping))
        state = self.home / "state"
        return state, ml.plan(self.vault, file, state)

    def test_links_identity_and_rollback(self):
        body = '---\nlast_updated: 2020-01-01\nupdate_count: 2\n---\n![图](<../../assets/a b.png>)\n![图](../../assets/a%20b.png)\n[[旧资料/A.md#节|别名]]\n[[不存在]]\n'
        self.put("旧资料/集合/A.md", body)
        self.put("旧资料/A.md", "来源")
        self.put("assets/a b.png", "binary-fixture")
        self.put(".import_files/preserved", "cache")
        self.put("个人写作/private.md", "[[不应解析或修改]]")
        state, data = self.make_plan({"旧资料": "工作资料", "assets/a b.png": "工作资料/集合/附件/a b.png"})
        self.assertFalse(data["conflicts"])
        self.assertNotIn("个人写作/private.md", data["files"])
        self.assertTrue(ml.apply(self.vault, state, data)["ok"])
        text = (self.vault / "工作资料/集合/A.md").read_text()
        self.assertIn("<附件/a b.png>", text)
        self.assertIn("附件/a%20b.png", text)
        self.assertIn("[[工作资料/A.md#节|别名]]", text)
        self.assertIn("update_count: 2", text)
        self.assertTrue(ml.rollback(self.vault, state, data)["ok"])
        self.assertEqual((self.vault / "旧资料/集合/A.md").read_text(), body)
        self.assertEqual((self.vault / "个人写作/private.md").read_text(), "[[不应解析或修改]]")

    def test_conflict_and_drift_refused(self):
        self.put("旧/A.md", "old")
        self.put("新/A.md", "new")
        state, data = self.make_plan({"旧": "新"})
        self.assertTrue(data["conflicts"])
        with self.assertRaises(ValueError):
            ml.apply(self.vault, state, data)

    def test_valid_short_links_keep_style(self):
        self.put("项目/总览.md", "[[专题#章节|名称]]\n[材料](../资料/a.md)\n")
        self.put("项目/专题.md", "# 章节")
        self.put("资料/a.md", "source")
        state, data = self.make_plan({"项目/总览.md": "项目/项目维护/总览.md"})
        self.assertTrue(ml.apply(self.vault, state, data)["ok"])
        text = (self.vault / "项目/项目维护/总览.md").read_text()
        self.assertIn("[[专题#章节|名称]]", text)
        self.assertIn("../../资料/a.md", text)

    def test_dotted_names_escaped_alias_and_false_baseline(self):
        original = '[[旧/A0.7]]\n| [[旧/甲\\|别名]] |\n'
        self.put("旧/index.md", original)
        self.put("旧/A0.7.md", "target")
        self.put("旧/甲.md", "target")
        state, data = self.make_plan({"旧": "新"})
        self.assertFalse(data["broken_before"])
        self.assertTrue(ml.apply(self.vault, state, data)["ok"])
        self.assertIn('[[新/甲.md\\|别名]]', (self.vault / "新/index.md").read_text())
        # 负对照：模拟旧解析器把有效链接记成断链并漏改，不能再通过验收。
        (self.vault / "新/index.md").write_text(original)
        data["broken_before"] = {"旧/index.md": ["旧/A0.7", "旧/甲\\"]}
        data["changes"]["旧/index.md"] = ml.digest(self.vault / "新/index.md")
        result = ml.verify(self.vault, data, state / "backup")
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["new_broken"]["新/index.md"]), 2)
        self.assertTrue(ml.rollback(self.vault, state, data)["ok"])

    def test_protected_links_and_root_context(self):
        self.put("40_个人写作/日记/a.md", "[[不读取正文]]")
        self.put("20_每日记录/个人/a.md", "[[40_个人写作/日记/a|日记]]")
        state, data = self.make_plan({"40_个人写作": "个人写作", "20_每日记录/个人": "个人记录/每日"})
        self.assertNotIn("40_个人写作/日记/a.md", data["files"])
        self.assertTrue(ml.apply(self.vault, state, data)["ok"])
        page = self.vault / "个人记录/每日/a.md"
        self.assertIn("[[个人写作/日记/a.md|日记]]", page.read_text())
        page.write_text("[[40_个人写作/日记/a|日记]]")
        self.assertTrue(ml.verify(self.vault, data, state / "backup")["new_broken"])
        isolated = self.home / "context-vault"
        isolated.mkdir()
        (isolated / "个人上下文.md").write_text("fixture")
        with self.assertRaises(ValueError):
            ml.check_layout(isolated)

    def test_old_layout_install_refused_before_writes(self):
        self.put("30_知识库/a.md", "old")
        r = subprocess.run(["bash", ROOT / "install/install.sh", "--vault-root", self.vault], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.vault / "AGENTS.md").exists())

    def test_destination_symlink_after_plan_refused(self):
        self.put("旧/a.md", "evidence")
        state, data = self.make_plan({"旧": "新"})
        outside = self.home / "outside"
        outside.mkdir()
        (self.vault / "新").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            ml.apply(self.vault, state, data)
        self.assertFalse((outside / "a.md").exists())

    def test_sync_default_deny_idempotent(self):
        sync.render(self.vault, [])
        self.assertNotIn("!/工作", (self.vault / ".stignore").read_text())
        sync.render(self.vault, ["工作项目/A", "知识库/主题"])
        text = (self.vault / ".stignore").read_text()
        sync.render(self.vault, ["工作项目/A", "知识库/主题"])
        self.assertEqual(text, (self.vault / ".stignore").read_text())
        self.assertNotIn("!/工作项目/B", text)
        self.assertLess(text.index("/.logs"), text.index("!/知识库"))
        for invalid in ("知识库", "工作项目", "工作项目/", "./工作项目", "", ".", "../secret", "/tmp", "工作项目/*"):
            with self.assertRaises(ValueError):
                sync.collection(invalid)
        self.assertEqual(sync.collection(".obsidian/daily-notes.json"), ".obsidian/daily-notes.json")
        for invalid in (".obsidian", ".obsidian/plugins", ".obsidian/../secret.json", ".codex/config.json"):
            with self.assertRaises(ValueError):
                sync.collection(invalid)


if __name__ == "__main__":
    unittest.main()
