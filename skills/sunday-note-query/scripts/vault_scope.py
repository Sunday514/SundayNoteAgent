"""本机模式与明确的 Wiki 读取边界；不推测内容归属。"""
import json
import os
from pathlib import Path


def no_symlink(path, root):
    if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError(f"拒绝越界或符号链接路径: {path}")
    return path


def wiki_access(root, requested_mode=None):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sunday-note-agent/config.json"
    binding = json.loads(config.read_text()) if config.is_file() else {}
    if Path(binding.get("vault", "/")).resolve() != root:
        binding = {}
    local = root / ".sunday-note-agent/install-mode"
    installed = binding.get("mode") or (local.read_text().strip() if local.is_file() else None)
    mode = requested_mode or installed or "work"
    if mode not in ("work", "personal") or (mode == "personal" and installed != "personal"):
        raise ValueError("当前安装不允许该读取模式")
    wiki = no_symlink(root / "知识库", root)
    if mode == "personal":
        return mode, [wiki]
    values = binding.get("work_wiki_roots")
    if not isinstance(values, list) or not values:
        raise ValueError("未配置 work_wiki_roots；请在本机绑定中明确批准 Wiki 目录，不遍历知识库")
    roots = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("work_wiki_roots 必须是相对目录数组")
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 2 or rel.parts[0] != "知识库":
            raise ValueError(f"无效 Wiki 授权目录: {value}")
        roots.append(no_symlink(root / rel, root))
    return mode, roots


def require_scope(path, root, allowed):
    path = root / path
    if ".." in path.parts:
        raise ValueError(f"拒绝非规范路径: {path}")
    no_symlink(path, root)
    if not any(path.is_relative_to(base) for base in allowed):
        raise ValueError(f"路径未获读取授权: {path}")
    return path
