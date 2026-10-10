#!/usr/bin/env python3
"""本机插件安装/迁移；只接管列出的工具文件，不迁移笔记正文。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True
from build_plugin import CORE
from configure_monitor import safe_path
from monitor import atomic, read_json

SOURCE = Path(__file__).resolve().parents[1]
PLUGIN_ID = "sunday-note-agent@sunday-note-local"


def locations():
    def xdg(key, default):
        return Path(os.environ.get(key, str(Path.home() / default))) / "sunday-note-agent"
    return (xdg("XDG_CONFIG_HOME", ".config"), xdg("XDG_STATE_HOME", ".local/state"),
            xdg("XDG_DATA_HOME", ".local/share"))


def fingerprint(path):
    safe_path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    for p in sorted(path.rglob("*")) if path.is_dir() else [path]:
        if p.is_symlink():
            raise ValueError(f"拒绝符号链接：{p}")
        if p.is_file():
            digest.update(str(p.relative_to(path) if path.is_dir() else p.name).encode())
            digest.update(b"\0" + p.read_bytes())
    return digest.hexdigest()


def copy(source, target):
    safe_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def remove_owned(path):
    safe_path(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def run(*argv):
    subprocess.run([str(a) for a in argv], check=True)


def targets(vault, codex_home, config_dir, data_dir):
    return [config_dir / "config.json", config_dir / "install-mode", data_dir / "marketplace",
            codex_home / "config.toml",
            vault / "AGENTS.md", vault / ".stignore", vault / "SundayNoteTools",
            vault / ".obsidian/plugins/quickadd/data.json",
            *[vault / ".agents/skills" / name for name in (*CORE, "sunday-note-context", "paper-summarizer")]]


def inspect(vault, source_target):
    config_dir, state_dir, data_dir = locations()
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).absolute()
    for path in (vault, source_target, config_dir, state_dir, data_dir, codex_home):
        safe_path(path)
    if not vault.is_dir():
        raise ValueError("vault 不存在")
    if source_target.is_relative_to(vault):
        raise ValueError("目标源码必须位于 vault 外")
    current = read_json(config_dir / "config.json", {})
    if current and current.get("vault") != str(vault):
        raise ValueError("已绑定其他 vault；请先停用并回退原绑定")
    if source_target != SOURCE and source_target.exists():
        raise ValueError("目标源码目录已存在；从该目录运行更新，或先核查冲突")
    codex_config = codex_home / "config.toml"
    if codex_config.exists():
        config = tomllib.loads(codex_config.read_text())
        market = config.get("marketplaces", {}).get("sunday-note-local")
        if market and market.get("source") != str(data_dir / "marketplace"):
            raise ValueError("同名 marketplace 指向非托管目录")
    paths = targets(vault, codex_home, config_dir, data_dir)
    for path in paths:
        fingerprint(path)
    mode_file = config_dir / "install-mode" if current else vault / ".sunday-note-agent/install-mode"
    mode = mode_file.read_text().strip() if mode_file.exists() else "personal"
    if mode not in ("personal", "work"):
        raise ValueError("未知安装模式")
    return {"vault": str(vault), "source": str(SOURCE), "source_target": str(source_target),
            "mode": mode, "config_dir": str(config_dir), "state_dir": str(state_dir),
            "data_dir": str(data_dir), "codex_home": str(codex_home),
            "targets": [str(p) for p in paths], "binding": current}


def save_backup(plan, transaction):
    transaction.mkdir(parents=True, mode=0o700)
    entries = []
    for i, name in enumerate(plan["targets"]):
        path = Path(name)
        before = fingerprint(path)
        if before is not None:
            copy(path, transaction / "files" / str(i))
        entries.append({"path": name, "before": before, "after": before, "backup": str(i)})
    journal = {"plan": plan, "entries": entries, "phase": "prepared"}
    atomic(transaction / "journal.json", journal)
    return journal


def seal(transaction, journal, phase, paths=()):
    for entry in journal["entries"]:
        if entry["path"] in paths:
            entry["after"] = fingerprint(Path(entry["path"]))
    journal["phase"] = phase
    atomic(transaction / "journal.json", journal)


def step(transaction, journal, name, action, paths):
    # 执行前落盘；中途退出时不得把未知内容当作已确认的迁移结果。
    journal.update(phase="applying", active_step=name, active_paths=[str(p) for p in paths])
    atomic(transaction / "journal.json", journal)
    action()
    journal.pop("active_step", None)
    paths = journal.pop("active_paths")
    seal(transaction, journal, "applying", paths)


def apply(plan, paper=False, remote_app_id=None):
    vault, source_target = Path(plan["vault"]), Path(plan["source_target"])
    config_dir, state_dir, data_dir = map(Path, (plan["config_dir"], plan["state_dir"], plan["data_dir"]))
    codex_home = Path(plan["codex_home"])
    latest = state_dir / "migration.json"
    prior = read_json(latest, {})
    if prior:
        old = read_json(Path(prior["transaction"]) / "journal.json")
        if old["phase"] not in ("installed", "rolled_back"):
            raise ValueError("上次迁移未完成；先按 status 核查并 rollback，不覆盖检查点")
    previous = plan["binding"]
    options = {"paper": paper or previous.get("paper", False) or (vault / ".agents/skills/paper-summarizer").exists(),
               "remote_app_id": remote_app_id or previous.get("remote_app_id")}
    if source_target != SOURCE:
        if source_target.exists():
            raise ValueError("目标源码目录已存在；请从该目录执行更新，或核查冲突")
        # 搬迁前保留完整 Git 与本地文件；不在活跃聊天中删除原 cwd。
        shutil.copytree(SOURCE, source_target, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    transaction = state_dir / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    journal = save_backup(plan, transaction)
    atomic(latest, {"transaction": str(transaction)})
    try:
        market = data_dir / "marketplace"
        staging = transaction / "marketplace"
        # 使用新源码的构建器，避免真实搬迁后仍依赖旧 cwd。
        args = [sys.executable, source_target / "install/build_plugin.py", "--output", staging]
        if options["paper"]:
            args.append("--with-paper-summarizer")
        if options["remote_app_id"]:
            args.extend(["--remote-app-id", options["remote_app_id"]])
        run(*args)
        def publish_market():
            remove_owned(market)
            copy(staging, market)
        step(transaction, journal, "marketplace", publish_market, [market])
        binding = {"vault": str(vault), "source": str(source_target), "state_dir": str(state_dir),
                   "mode": plan["mode"], **options}
        step(transaction, journal, "binding", lambda: atomic(config_dir / "config.json", binding), [config_dir / "config.json"])
        step(transaction, journal, "vault-tools", lambda: run("bash", source_target / "install/install.sh", "--vault-root", vault, "--deployment", "plugin",
            "--install-state", config_dir / "install-mode", "--mode", plan["mode"], "--routine-templates", "preserve"),
             [p for p in map(Path, plan["targets"]) if p.is_relative_to(vault)] + [config_dir / "install-mode"])
        for name in (*CORE, "sunday-note-context", "paper-summarizer"):
            step(transaction, journal, "remove-" + name, lambda: remove_owned(vault / ".agents/skills" / name),
                 [vault / ".agents/skills" / name])
        # Codex 拥有插件注册和缓存，不手工模拟安装。
        codex_home.mkdir(parents=True, exist_ok=True)
        step(transaction, journal, "register-market", lambda: run("codex", "plugin", "marketplace", "add", market, "--json"), [codex_home / "config.toml"])
        step(transaction, journal, "register-plugin", lambda: run("codex", "plugin", "add", PLUGIN_ID, "--json"), [codex_home / "config.toml"])
        seal(transaction, journal, "installed")
    except BaseException:
        journal["phase"] = "interrupted"
        atomic(transaction / "journal.json", journal)
        raise
    print(json.dumps({"phase": "installed", "transaction": str(transaction),
          "pending": ["客户端重载与 Skills 发现", "新工作区与 QuickAdd 实测", "旧目录归档"]}, ensure_ascii=False))


def rollback(state_dir):
    latest = read_json(state_dir / "migration.json", {})
    transaction = Path(latest["transaction"])
    journal = read_json(transaction / "journal.json")
    if journal["phase"] == "rolled_back":
        return
    incomplete = journal["phase"] in ("prepared", "applying", "interrupted", "rolling_back")
    for entry in journal["entries"]:
        actual = fingerprint(Path(entry["path"]))
        expected = entry.get("after", entry["before"])
        if actual != expected and not (incomplete and actual == entry["before"]):
            if incomplete and entry["path"] in journal.get("active_paths", []):
                # 无法区分中断写入和用户编辑：完整留存，不冒充已验证结果。
                recovery = transaction / "recovery" / entry["backup"] / Path(entry["path"]).name
                if actual is not None and not recovery.exists():
                    pending = recovery.parent / (recovery.name + ".pending")
                    remove_owned(pending)
                    copy(Path(entry["path"]), pending)
                    pending.rename(recovery)
                elif actual is not None and fingerprint(recovery) != actual:
                    raise ValueError(f"恢复副本之后又有新修改：{entry['path']}")
                entry["recovery_hash"] = actual
                atomic(transaction / "journal.json", journal)
                continue
            raise ValueError(f"迁移后有新修改，不自动覆盖：{entry['path']}")
    plan = journal["plan"]
    journal["phase"] = "rolling_back"
    atomic(transaction / "journal.json", journal)
    run("codex", "plugin", "remove", PLUGIN_ID)
    for entry in reversed(journal["entries"]):
        path = Path(entry["path"])
        remove_owned(path)
        if entry["before"] is not None:
            copy(transaction / "files" / entry["backup"], path)
    if plan["binding"]:
        run("codex", "plugin", "add", PLUGIN_ID, "--json")
    journal["phase"] = "rolled_back"
    atomic(transaction / "journal.json", journal)
    print("已恢复迁移前配置；源码副本和备份保留，独立 Monitor 不变，需重载客户端。")
    if (transaction / "recovery").exists():
        print(f"中断期间的未确认内容已保留，请核查：{transaction / 'recovery'}（编号对应 journal entries 的 backup）")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("plan", "apply", "status", "rollback", "disable"))
    p.add_argument("--vault-root", type=Path, required=True)
    p.add_argument("--source-target", type=Path, required=True)
    p.add_argument("--with-paper-summarizer", action="store_true")
    p.add_argument("--remote-app-id")
    p.add_argument("--mode", choices=("personal", "work"))
    a = p.parse_args()
    plan = inspect(a.vault_root.absolute(), a.source_target.absolute())
    if a.mode:
        if plan["binding"] and a.mode != plan["mode"]:
            raise ValueError("已绑定 vault 不跨模式迁移；请使用独立工作 vault")
        plan["mode"] = a.mode
    if a.command == "plan":
        print(json.dumps({k: v for k, v in plan.items() if k != "binding"}, ensure_ascii=False, indent=2))
    elif a.command == "apply":
        apply(plan, a.with_paper_summarizer, a.remote_app_id)
    elif a.command == "status":
        latest = read_json(Path(plan["state_dir"]) / "migration.json", {})
        journal = read_json(Path(latest["transaction"]) / "journal.json", {}) if latest else {}
        print(json.dumps({"bound": bool(plan["binding"]), "phase": journal.get("phase", "not_installed"), **latest}, ensure_ascii=False))
    elif a.command == "rollback":
        rollback(Path(plan["state_dir"]))
    else:
        run("codex", "plugin", "remove", PLUGIN_ID)
        print("插件已卸载；知识、QuickAdd 与独立 Monitor 保留。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
