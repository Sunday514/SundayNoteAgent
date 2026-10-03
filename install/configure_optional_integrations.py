#!/usr/bin/env python3
"""为已安装并启用的 Obsidian 插件合并项目拥有的最小配置。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plugin_is_ready(vault_root: Path, enabled: set[str], plugin_id: str) -> bool:
    plugin_dir = vault_root / ".obsidian" / "plugins" / plugin_id
    return plugin_id in enabled and (plugin_dir / "manifest.json").is_file()


def plugin_data_path(vault_root: Path, plugin_id: str) -> Path:
    vault = vault_root.resolve()
    plugin_dir = vault_root / ".obsidian" / "plugins" / plugin_id
    target = plugin_dir / "data.json"
    if plugin_dir.is_symlink() or target.is_symlink():
        raise RuntimeError(f"拒绝写入符号链接：{target}")
    if (
        not plugin_dir.resolve().is_relative_to(vault)
        or not target.resolve().is_relative_to(vault)
    ):
        raise RuntimeError(f"插件配置目标超出 vault：{target}")
    return target


def configure_calendar(vault_root: Path, config_root: Path, enabled: set[str]) -> None:
    if not plugin_is_ready(vault_root, enabled, "calendar"):
        print("可选工作流未配置：Calendar Weekly 创建（插件未安装并启用）。")
        return
    target = plugin_data_path(vault_root, "calendar")
    current = load_json(target) if target.exists() else {}
    current.update(load_json(config_root / "calendar.json"))
    write_json(target, current)
    print("已配置可选工作流：Calendar Weekly 创建。")


def configure_quickadd(vault_root: Path, config_root: Path, enabled: set[str]) -> None:
    if not plugin_is_ready(vault_root, enabled, "quickadd"):
        print("可选工作流未配置：QuickAdd Routine 自动化（插件未安装并启用）。")
        return
    target = plugin_data_path(vault_root, "quickadd")
    current = load_json(target) if target.exists() else {}
    desired_choices = load_json(config_root / "quickadd.json")["choices"]
    owned_ids = {choice["id"] for choice in desired_choices}
    owned_names = {choice["name"] for choice in desired_choices}
    current["choices"] = [
        choice
        for choice in current.get("choices", [])
        if choice.get("id") not in owned_ids and choice.get("name") not in owned_names
    ] + desired_choices
    write_json(target, current)
    print("已配置可选工作流：QuickAdd Routine 自动化。")


def configure_daily_notes(vault_root: Path, config_root: Path) -> None:
    core_path = vault_root / ".obsidian" / "core-plugins.json"
    if not core_path.exists() or not load_json(core_path).get("daily-notes", False):
        return
    target = vault_root / ".obsidian" / "daily-notes.json"
    if target.is_symlink():
        raise RuntimeError(f"拒绝写入符号链接：{target}")
    current = load_json(target) if target.exists() else {}
    current.update(load_json(config_root / "daily-notes.json"))
    write_json(target, current)
    print("已配置 Daily Notes 个人记录目录和模板。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument(
        "--preserve-templates",
        action="store_true",
        help="保留 Calendar 和 Daily Notes 的模板字段",
    )
    args = parser.parse_args()

    config_root = Path(__file__).resolve().parent.parent / "config" / "obsidian"
    enabled_path = args.vault_root / ".obsidian" / "community-plugins.json"
    enabled = set(load_json(enabled_path)) if enabled_path.is_file() else set()
    if not args.preserve_templates:
        configure_daily_notes(args.vault_root, config_root)
        configure_calendar(args.vault_root, config_root, enabled)
    configure_quickadd(args.vault_root, config_root, enabled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
