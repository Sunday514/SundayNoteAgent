#!/usr/bin/env python3
"""Opt-in user-level monitor installation; preserves unrelated hook entries."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sys
import time
import tomllib

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "automation" / "monitor"))
from monitor import atomic, read_json, root_for, migrate

MARKER = "SundayNoteAgent Monitor"
MCP_BEGIN = "# BEGIN " + MARKER + " MCP\n"
MCP_END = "# END " + MARKER + " MCP\n"


def stop_workers(runtime, root):
    state = read_json(root / "state.json", {})
    atomic(root / "state.json", {**state, "enabled": False})
    processes = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            args = (proc / "cmdline").read_bytes().decode().split("\0")
            if str(runtime / "monitor.py") in args and "work" in args:
                os.kill(int(proc.name), signal.SIGTERM)
                processes.append(proc)
        except (OSError, UnicodeError):
            continue
    deadline = time.monotonic() + 10
    def alive(proc):
        try:
            return (proc / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    while any(alive(p) for p in processes):
        if time.monotonic() >= deadline:
            raise ValueError("旧 Monitor 执行器尚未退出，已暂停；请检查后重新安装")
        time.sleep(.1)


def configure_mcp(text, runtime, config_path, uninstall=False):
    start = text.find(MCP_BEGIN)
    if start >= 0:
        end = text.find(MCP_END, start)
        if end < 0:
            raise ValueError("Monitor MCP 配置标记不完整")
        text = text[:start] + text[end + len(MCP_END):]
    elif "sunday_note_monitor" in tomllib.loads(text).get("mcp_servers", {}):
        if not uninstall:
            raise ValueError("已有非托管 sunday_note_monitor MCP 配置，请先处理命名冲突")
    if not uninstall:
        text = text.rstrip() + "\n\n" + MCP_BEGIN + "[mcp_servers.sunday_note_monitor]\n"
        text += "command = " + json.dumps(sys.executable) + "\n"
        text += "args = " + json.dumps([str(runtime / "widget_server.py"), "--config", str(config_path)]) + "\n"
        text += MCP_END
    tomllib.loads(text)
    return text


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("refuse symlink installation target: " + str(path))


def enable_hooks(text):
    parsed = tomllib.loads(text)
    if parsed.get("features", {}).get("hooks") is True:
        return text, None
    lines = text.splitlines(keepends=True)
    section = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("["):
            section = line.strip()
        if (section == "[features]" and re.match(r"\s*hooks\s*=", line)) or (
                not section and re.match(r"\s*features\.hooks\s*=", line)):
            replacement = ("hooks" if section else "features.hooks") + " = true # " + MARKER + "\n"
            lines[i] = replacement
            return "".join(lines), {"before": line, "after": replacement}
    for i, line in enumerate(lines):
        if line.strip() == "[features]":
            addition = "hooks = true # " + MARKER + "\n"
            lines.insert(i + 1, addition)
            return "".join(lines), {"before": "", "after": addition}
    addition = "features.hooks = true # " + MARKER + "\n"
    return addition + text, {"before": "", "after": addition}


def write_text(path, text):
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def configure(vault, codex_home, applications, uninstall=False, proxy_url=None):
    vault, codex_home, applications = map(lambda p: Path(p).absolute(), (vault, codex_home, applications))
    hook_file, config_file = codex_home / "hooks.json", codex_home / "config.toml"
    runtime = vault / ".sunday-note-agent" / "monitor"
    skill = vault / ".agents" / "skills" / "sunday-note-monitor"
    desktop = applications / "sunday-note-monitor.desktop"
    for p in (hook_file, config_file, runtime, skill, desktop, vault / ".logs" / "codex"):
        safe_path(p)
    hooks = read_json(hook_file, {"hooks": {}})
    if not isinstance(hooks.get("hooks"), dict):
        raise ValueError("unsupported hooks.json")
    if not uninstall:
        for groups in hooks["hooks"].values():
            for group in groups:
                for handler in group.get("hooks", []):
                    cmd = handler.get("command", "")
                    if "/.sunday-note-agent/monitor/monitor.py" in cmd and str(runtime / "monitor.py") not in cmd:
                        raise ValueError("Monitor 已绑定其他 Vault；请先卸载原绑定。")
    original = config_file.read_text() if config_file.exists() else ""
    parsed = tomllib.loads(original)
    if not uninstall and any(parsed.get("hooks", {}).get(e) for e in ("Stop", "UserPromptSubmit")):
        raise ValueError("已有 inline Hook；请先统一到 hooks.json，避免同层优先级遮蔽。")
    for event in ("Stop", "UserPromptSubmit"):
        groups = hooks["hooks"].get(event, [])
        preserved = []
        for group in groups:
            handlers = [h for h in group.get("hooks", [])
                        if str(runtime / "monitor.py") not in h.get("command", "")]
            if handlers:
                preserved.append({**group, "hooks": handlers})
        hooks["hooks"][event] = preserved
    root = root_for({"vault": str(vault)})
    install_state = root / "install.json"
    config_path = root / "config.json"
    old = read_json(install_state, {})
    updated = configure_mcp(original, runtime, config_path, uninstall)
    if uninstall:
        atomic(hook_file, hooks)
        change = old.get("feature_change")
        if change and change["after"] in updated:
            updated = updated.replace(change["after"], change["before"], 1)
        write_text(config_file, updated)
        desktop.unlink(missing_ok=True)
        state = read_json(root / "state.json", {})
        atomic(root / "state.json", {**state, "enabled": False})
        for p in (runtime, skill):
            if p.exists():
                shutil.rmtree(p)
        print("Monitor 已停用并移除自身 Hook、MCP 注册和托管副本；日志保留。")
        return
    needed = ["rg"]
    missing = [name for name in needed if not shutil.which(name)]
    if missing:
        raise ValueError("缺少依赖：" + ", ".join(missing))
    updated, change = enable_hooks(updated)
    tomllib.loads(updated)
    stop_workers(runtime, root)
    runtime.mkdir(parents=True, exist_ok=True)
    for p in (SOURCE / "automation" / "monitor").glob("*.py"):
        shutil.copy2(p, runtime / p.name)
    shutil.copy2(SOURCE / "automation" / "monitor" / "widget.html", runtime / "widget.html")
    (runtime / "panel.py").unlink(missing_ok=True)
    desktop.unlink(missing_ok=True)
    skill.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "skills" / "sunday-note-monitor" / "SKILL.md", skill / "SKILL.md")
    local_config = read_json(config_path, {})
    local_config.setdefault("project_roots", [str(vault.resolve())])
    if proxy_url is not None:
        local_config["proxy_url"] = proxy_url
    local_config.pop("codex", None)
    atomic(config_path, {**local_config, "vault": str(vault),
                        "skill": str(skill / "SKILL.md"),
                        "query": str(SOURCE / "skills" / "sunday-note-query" / "scripts" / "query_search.py")})
    argv = [sys.executable, str(runtime / "monitor.py"), "--config", str(config_path)]
    for event in ("UserPromptSubmit", "Stop"):
        hooks["hooks"][event].append({"hooks": [
            {"type": "command", "command": shlex.join([*argv, "hook"]), "timeout": 5}]})
    atomic(hook_file, hooks)
    write_text(config_file, updated)
    atomic(install_state, {"feature_change": old.get("feature_change") or change})
    migrate(root)
    atomic(root / "state.json", {"enabled": True})
    print("Monitor 已安装。请在 Codex /hooks 中审阅并信任两个 Hook，重新加载客户端的 MCP 工具后验证 widget。")
    print("日志及配置：" + str(root))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vault-root", required=True)
    p.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    p.add_argument("--applications-dir", default=str(Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "applications"))
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--proxy-url", help="仅保存在 Vault 本地配置的 Codex 连接代理；空字符串清除")
    a = p.parse_args()
    configure(a.vault_root, a.codex_home, a.applications_dir, a.uninstall, a.proxy_url)


if __name__ == "__main__":
    main()
