#!/usr/bin/env python3
"""Opt-in user-level monitor installation; preserves unrelated hook entries."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tomllib

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "automation" / "monitor"))
from monitor import atomic, read_json, root_for

MARKER = "SundayNoteAgent Monitor"


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
    old = read_json(install_state, {})
    if uninstall:
        atomic(hook_file, hooks)
        change = old.get("feature_change")
        if change and change["after"] in original:
            write_text(config_file, original.replace(change["after"], change["before"], 1))
        desktop.unlink(missing_ok=True)
        state = read_json(root / "state.json", {})
        atomic(root / "state.json", {**state, "enabled": False})
        for p in (runtime, skill):
            if p.exists():
                shutil.rmtree(p)
        print("Monitor 已停用并移除自身 Hook、面板入口和托管副本；日志保留。")
        return
    needed = ["codex", "zenity", "notify-send", "rg"]
    missing = [name for name in needed if not shutil.which(name)]
    if not any(shutil.which(x) for x in ("xclip", "xsel", "wl-copy")):
        missing.append("xclip / xsel / wl-copy")
    if missing:
        raise ValueError("缺少依赖：" + ", ".join(missing))
    updated, change = enable_hooks(original)
    tomllib.loads(updated)
    runtime.mkdir(parents=True, exist_ok=True)
    for p in (SOURCE / "automation" / "monitor").glob("*.py"):
        shutil.copy2(p, runtime / p.name)
    skill.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "skills" / "sunday-note-monitor" / "SKILL.md", skill / "SKILL.md")
    config_path = root / "config.json"
    local_config = read_json(config_path, {})
    if proxy_url is not None:
        local_config["proxy_url"] = proxy_url
    atomic(config_path, {**local_config, "vault": str(vault), "codex": shutil.which("codex"),
                        "skill": str(skill / "SKILL.md"),
                        "query": str(SOURCE / "skills" / "sunday-note-query" / "scripts" / "query_search.py")})
    argv = [sys.executable, str(runtime / "monitor.py"), "--config", str(config_path)]
    for event in ("UserPromptSubmit", "Stop"):
        hooks["hooks"][event].append({"hooks": [
            {"type": "command", "command": shlex.join([*argv, "hook"]), "timeout": 5}]})
    atomic(hook_file, hooks)
    write_text(config_file, updated)
    # Desktop Exec uses double-quoted arguments, not shell single-quote syntax.
    exec_line = " ".join('"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'
                         for s in [*argv, "panel"])
    write_text(desktop, "[Desktop Entry]\nType=Application\nName=Monitor 建议\nTerminal=false\nExec=" + exec_line + "\n")
    atomic(install_state, {"feature_change": old.get("feature_change") or change})
    state = read_json(root / "state.json", {})
    atomic(root / "state.json", {**state, "enabled": True})
    print("Monitor 已安装。请在 Codex /hooks 中审阅并信任两个 Hook；桌面客户端重新加载后验证。")
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
