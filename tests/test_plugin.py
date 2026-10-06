"""插件构建、迁移和停用的脱敏回归；不调用模型或真实用户配置。"""
import importlib.util
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
sys.path.insert(0, str(ROOT / "install"))
import build_plugin as builder
import migrate_plugin as migration
from plugin_host import require_capabilities
from monitor import read_json
from configure_monitor import stop_workers, configure_mcp


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sunday-plugin-")
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"), "XDG_STATE_HOME": str(self.root / "state"),
            "CODEX_HOME": str(self.root / "codex")})
        self.env.start()
        self.host = patch.object(migration, "preflight")
        self.host.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.host.stop()
        self.temp.cleanup()

    def install_old(self):
        subprocess.run(["bash", str(ROOT / "install/install.sh"), "--vault-root", str(self.vault),
                        "--with-paper-summarizer"], check=True, capture_output=True)
        codex = self.root / "codex"
        codex.mkdir()
        (codex / "config.toml").write_text(configure_mcp('model = "example"\n',
            self.vault / ".sunday-note-agent/monitor", self.vault / ".logs/codex/config.json"))
        for directory in (".sunday-note-agent/monitor", ".agents/skills/sunday-note-monitor"):
            target = self.vault / directory
            target.mkdir(parents=True, exist_ok=True)
            (target / "fixture.txt").write_text("independent local monitor")
        (codex / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"command": "echo custom"}, {"command": f'python3 {self.vault}/.sunday-note-agent/monitor/monitor.py hook'}]}]}}))
        root = self.vault / ".logs/codex"
        root.mkdir(parents=True)
        (root / "config.json").write_text(json.dumps({"vault": str(self.vault), "project_roots": [str(ROOT)]}))
        (root / "state.json").write_text('{"enabled": false}')
        queue = root / "projects/example/sessions/session/queue"
        queue.mkdir(parents=True)
        (queue / "pending.json").write_text('{}')
        (queue.parent / "state.json").write_text(json.dumps({"repository_baselines": {str(ROOT): {"baseline_known": False}}}))

    def fake_run(self, *args):
        self.calls.append([str(a) for a in args])
        if args[0] == "codex":
            return
        subprocess.run([str(a) for a in args], check=True, capture_output=True)

    def test_package_options_and_no_private_material(self):
        for paper in (False, True):
            output = self.root / str(paper)
            package = builder.build(output, paper, "plugin_asdk_app_example")
            self.assertEqual((package / "skills/paper-summarizer").exists(), paper)
            self.assertNotIn("mode", read_json(package / "build.json"))
            self.assertFalse((package / "skills/sunday-note-context").exists())
            self.assertFalse((package / "skills/skill-validation").exists())
            self.assertEqual((package / "skills/sunday-note-query/SKILL.md").read_bytes(),
                             (ROOT / "skills/sunday-note-query/SKILL.md").read_bytes())
            hooks = read_json(package / "hooks/hooks.json")["hooks"]
            self.assertEqual(set(hooks), {"SessionStart"})
            self.assertFalse((package / "automation").exists())
            self.assertFalse((package / "skills/sunday-note-monitor").exists())
            self.assertFalse((package / "mcp.json").exists())
            self.assertIn("${PLUGIN_ROOT}", hooks["SessionStart"][0]["hooks"][0]["command"])
            self.assertFalse((package / ".git").exists())
            with self.assertRaises(ValueError):
                builder.build(output)

    def test_identical_content_has_one_package_version(self):
        first = builder.build(self.root / "first")
        second = builder.build(self.root / "second")
        self.assertEqual(migration.fingerprint(first), migration.fingerprint(second))

    def test_missing_plugin_hooks_stops_before_mutation(self):
        self.install_old()
        plan = migration.inspect(self.vault, ROOT)
        before = migration.fingerprint(self.vault)
        migration.preflight.side_effect = ValueError("missing plugin hooks")
        with self.assertRaisesRegex(ValueError, "missing plugin hooks"):
            migration.apply(plan)
        self.assertEqual(before, migration.fingerprint(self.vault))
        self.assertFalse((self.root / "state/sunday-note-agent/migration.json").exists())
        with self.assertRaisesRegex(ValueError, "未发现插件 Hooks"):
            require_capabilities({}, [])
        require_capabilities({}, [{"pluginId": migration.PLUGIN_ID, "eventName": "sessionStart"}])

    def test_reject_symlink_and_nested_source(self):
        link = self.root / "link"
        link.symlink_to(self.vault, target_is_directory=True)
        with self.assertRaises(ValueError):
            builder.build(link / "out")
        with self.assertRaises(ValueError):
            migration.inspect(self.vault, self.vault / "source")

    def test_real_file_migration_update_rollback(self):
        self.install_old()
        plan = migration.inspect(self.vault, ROOT)
        old_hooks = (self.root / "codex/hooks.json").read_bytes()
        old_monitor = migration.fingerprint(self.vault / ".logs/codex")
        local_paths = [self.vault / ".sunday-note-agent/monitor", self.vault / ".agents/skills/sunday-note-monitor",
                       self.root / "codex/config.toml"]
        local_hashes = {p: migration.fingerprint(p) for p in local_paths}
        note = self.vault / "30_知识库/fixture.md"
        note.write_text("human knowledge")
        with patch.object(migration, "run", self.fake_run):
            migration.apply(plan)
            binding = read_json(self.root / "config/sunday-note-agent/config.json")
            self.assertFalse((self.vault / ".agents/skills/sunday-note-query").exists())
            self.assertTrue((self.vault / "SundayNoteTools/quickadd/create_daily.js").is_file())
            self.assertFalse((Path(binding["state_dir"]) / "monitor").exists())
            self.assertEqual(migration.fingerprint(self.vault / ".logs/codex"), old_monitor)
            self.assertEqual(note.read_text(), "human knowledge")
            self.assertEqual((self.root / "codex/hooks.json").read_bytes(), old_hooks)
            self.assertEqual({p: migration.fingerprint(p) for p in local_paths}, local_hashes)
            migration.rollback(Path(plan["state_dir"]))
            self.assertEqual((self.root / "codex/hooks.json").read_bytes(), old_hooks)
            self.assertEqual({p: migration.fingerprint(p) for p in local_paths}, local_hashes)
            self.assertTrue((self.vault / ".agents/skills/sunday-note-query/SKILL.md").is_file())
            self.assertEqual(note.read_text(), "human knowledge")
            migration.apply(migration.inspect(self.vault, ROOT))
            migration.apply(migration.inspect(self.vault, ROOT))
            self.assertEqual(migration.fingerprint(self.vault / ".logs/codex"), old_monitor)
            self.assertEqual((self.root / "codex/hooks.json").read_bytes(), old_hooks)
        self.assertTrue(any(c[:3] == ["codex", "plugin", "add"] for c in self.calls))

    def test_rollback_does_not_overwrite_later_user_edit(self):
        self.install_old()
        plan = migration.inspect(self.vault, ROOT)
        with patch.object(migration, "run", self.fake_run):
            migration.apply(plan)
            (self.vault / "AGENTS.md").write_text("new user edit")
            with self.assertRaisesRegex(ValueError, "新修改"):
                migration.rollback(Path(plan["state_dir"]))

    def test_prepared_and_interrupted_rollback(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                config, state, _ = migration.locations()
                target = self.root / "existing.txt"
                target.write_text("original")
                transaction = state / ("interrupted" if interrupted else "prepared")
                plan = {"targets": [str(target)], "config_dir": str(config),
                        "codex_home": str(self.root / "codex"), "binding": {}}
                journal = migration.save_backup(plan, transaction)
                migration.atomic(state / "migration.json", {"transaction": str(transaction)})
                if interrupted:
                    def crash():
                        target.write_text("unconfirmed content")
                        raise SystemExit("simulate process termination before checkpoint")
                    with self.assertRaises(SystemExit):
                        migration.step(transaction, journal, "write", crash, [target])
                with patch.object(migration, "run"):
                    migration.rollback(state)
                    migration.rollback(state)
                self.assertEqual(target.read_text(), "original")
                if interrupted:
                    self.assertEqual((transaction / "recovery/0/existing.txt").read_text(),
                                     "unconfirmed content")

    def test_scope_preserved_on_install_and_update(self):
        self.install_old()
        old = self.vault / ".logs/codex/config.json"
        migration.atomic(old, {"project_roots": []})
        with patch.object(migration, "run", self.fake_run):
            migration.apply(migration.inspect(self.vault, ROOT))
            config = self.root / "config/sunday-note-agent/config.json"
            binding = read_json(config)
            self.assertNotIn("monitor_config", binding)
            self.assertEqual(read_json(old)["project_roots"], [])
            migration.atomic(old, {"project_roots": [str(self.root / "other-project")]})
            migration.apply(migration.inspect(self.vault, ROOT))
            self.assertEqual(read_json(old)["project_roots"],
                             [str(self.root / "other-project")])

    def test_interrupted_rollback_rejects_unrelated_edit(self):
        config, state, _ = migration.locations()
        target, unrelated = self.root / "target", self.root / "unrelated"
        target.write_text("before")
        unrelated.write_text("before")
        transaction = state / "interrupted"
        plan = {"targets": [str(target), str(unrelated)], "config_dir": str(config),
                "codex_home": str(self.root / "codex"), "binding": {}}
        journal = migration.save_backup(plan, transaction)
        migration.atomic(state / "migration.json", {"transaction": str(transaction)})
        def crash():
            target.write_text("partial")
            raise SystemExit()
        with self.assertRaises(SystemExit):
            migration.step(transaction, journal, "write", crash, [target])
        unrelated.write_text("user edit")
        with patch.object(migration, "run"), self.assertRaisesRegex(ValueError, "新修改"):
            migration.rollback(state)
        self.assertEqual(unrelated.read_text(), "user edit")

    def test_stop_source_worker_by_bound_runtime(self):
        _, state, _ = migration.locations()
        runtime = state / "monitor"
        runtime.mkdir(parents=True)
        script = self.root / "source/monitor.py"
        script.parent.mkdir()
        script.write_text("import time\ntime.sleep(60)\n")
        own = subprocess.Popen([sys.executable, str(script), "--config", str(runtime / "runtime.json"), "work"])
        other = subprocess.Popen([sys.executable, str(script), "--config", str(self.root / "other.json"), "work"])
        try:
            stop_workers(None, runtime, config_path=runtime / "runtime.json")
            own.wait(timeout=5)
            self.assertIsNone(other.poll())
            self.assertFalse(read_json(runtime / "state.json")["enabled"])
        finally:
            for proc in (own, other):
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)

    def test_old_bundled_monitor_requires_explicit_separation(self):
        plan = migration.inspect(self.vault, ROOT)
        plan["binding"] = {"monitor": True}
        with self.assertRaisesRegex(ValueError, "独立安装本地 Monitor"):
            migration.apply(plan)
        self.assertFalse((self.root / "state/sunday-note-agent/migration.json").exists())

    def test_runtime_stdio_and_context(self):
        package = builder.build(self.root / "package")
        config_dir, state_dir, _ = migration.locations()
        for mode in ("personal", "work"):
            migration.atomic(config_dir / "config.json", {"vault": str(self.vault), "mode": mode,
                             "state_dir": str(state_dir)})
            result = subprocess.run([sys.executable, str(package / "plugin/entry.py"), "context"],
                                    capture_output=True, text=True, check=True)
            self.assertIn(str(self.vault), result.stdout)
            self.assertIn(mode, result.stdout)
        result = subprocess.run([sys.executable, str(package / "plugin/entry.py"), "mcp"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("独立本地安装", result.stderr)
        self.assertFalse((self.vault / ".logs").exists())

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI unavailable")
    def test_codex_install_update_remove_without_model(self):
        home = self.root / "codex"
        home.mkdir()
        market = self.root / "market"
        package = builder.build(market)
        def cli(*args):
            result = subprocess.run(["codex", "plugin", *args, "--json"], check=True,
                                    capture_output=True, text=True, timeout=30)
            return json.loads(result.stdout)
        cli("marketplace", "add", str(market))
        first = cli("add", migration.PLUGIN_ID)
        self.assertTrue((Path(first["installedPath"]) / "plugin/entry.py").is_file())
        shutil.rmtree(market)
        builder.build(market, paper=True)
        second = cli("add", migration.PLUGIN_ID)
        self.assertNotEqual(first["version"], second["version"])
        self.assertTrue((Path(second["installedPath"]) / "skills/paper-summarizer").exists())
        self.assertFalse((Path(second["installedPath"]) / "skills/sunday-note-context").exists())
        cli("remove", migration.PLUGIN_ID)
        listing = cli("list", "--marketplace", "sunday-note-local")
        self.assertFalse(listing["installed"])


if __name__ == "__main__":
    unittest.main()
