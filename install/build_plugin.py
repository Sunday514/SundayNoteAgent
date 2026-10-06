#!/usr/bin/env python3
"""从唯一源码生成无凭据的本地插件 marketplace。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parents[1]
CORE = ("sunday-note-query", "sunday-note-ingest", "sunday-note-lint")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build(output, paper=False, remote_app_id=None):
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("拒绝符号链接输出")
    if output.exists() and any(output.iterdir()):
        raise ValueError("输出目录必须为空")
    package = output / "plugins" / "sunday-note-agent"
    names = [*CORE]
    if paper:
        names.append("paper-summarizer")
    for name in names:
        tree = SOURCE / "skills" / name
        if any(p.is_symlink() for p in tree.rglob("*")):
            raise ValueError("插件源码不可包含符号链接")
        shutil.copytree(tree, package / "skills" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tree = SOURCE / "plugin"
    if any(p.is_symlink() for p in (tree, *tree.rglob("*"))):
        raise ValueError("插件源码不可包含符号链接")
    shutil.copytree(SOURCE / "plugin", package / "plugin", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    extension = {"hooks": "./hooks/hooks.json", "interface": {"displayName": "SundayNoteAgent",
                 "shortDescription": "个性化知识检索与积累", "category": "Productivity"}}
    if remote_app_id:
        if not remote_app_id.startswith(("plugin_asdk_app_", "connector_")):
            raise ValueError("必须提供已核实的远程 App ID")
        write(package / ".app.json", {"apps": {"sundaynote-vps": {"id": remote_app_id}}})
        extension["apps"] = "./.app.json"
    write(package / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
          "name": "sunday-note-agent", "version": "1.0.0", "description": "使用与维护个人或工作知识库，复用现有本地与远程能力。",
          "extensions": {"com.openai": extension}})
    def handler(command):
        return [{"hooks": [{"type": "command", "command": f'python3 "${{PLUGIN_ROOT}}/plugin/entry.py" {command}', "timeout": 5}]}]
    hooks = {"SessionStart": handler("context")}
    write(package / "hooks" / "hooks.json", {"hooks": hooks})
    hashes = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(package.rglob("*")) if p.is_file()}
    version = "1.0.0+" + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:12]
    manifest = json.loads((package / "plugin.json").read_text())
    manifest["version"] = version
    write(package / "plugin.json", manifest)
    hashes["plugin.json"] = hashlib.sha256((package / "plugin.json").read_bytes()).hexdigest()
    write(package / "build.json", {"paper": paper, "files": hashes})
    write(output / ".agents" / "plugins" / "marketplace.json", {"name": "sunday-note-local",
          "plugins": [{"name": "sunday-note-agent", "source": {"source": "local", "path": "./plugins/sunday-note-agent"},
          "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]})
    return package


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--with-paper-summarizer", action="store_true")
    p.add_argument("--remote-app-id")
    a = p.parse_args()
    print(build(a.output, a.with_paper_summarizer, a.remote_app_id))
