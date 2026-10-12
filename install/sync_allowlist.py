#!/usr/bin/env python3
"""设备侧集合授权；生成默认拒绝的 Syncthing 规则。"""
import argparse
import json
import os
import sys
from pathlib import Path
import tempfile

BEGIN = "// BEGIN SundayNoteAgent managed ignores"
END = "// END SundayNoteAgent managed ignores"
SOURCE = Path(__file__).parent


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sunday-note-agent/sync-allowlist.json"


def collection(value):
    p = Path(value)
    obsidian_file = len(p.parts) > 1 and p.parts[0] == ".obsidian" and p.suffix == ".json"
    checked = p.parts[1:] if obsidian_file else p.parts
    if not p.parts or p.is_absolute() or any(x in (".", "..") or x.startswith(".") for x in checked) or any(c in value for c in "\n\r*?![]{}\\"):
        raise ValueError("需要不含通配符的 Vault 相对集合路径")
    if p.as_posix() in ("工作项目", "个人项目", "工作资料", "个人资料", "知识库"):
        raise ValueError("项目、资料和 Wiki 必须批准具体集合，不可批准整个父目录")
    return p.as_posix()


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("拒绝符号链接目标")
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def render(vault, approved):
    if not approved:
        print("警告：本 Vault 未批准任何同步内容；默认拒绝全部路径，请配置同步白名单后再恢复同步。", file=sys.stderr)
    target = vault / ".stignore"
    old = target.read_text() if target.exists() else ""
    outside = []
    inside = False
    blocks = 0
    for line in old.splitlines():
        if line == BEGIN:
            if inside or blocks:
                raise ValueError("重复托管段")
            inside = True
            blocks += 1
        elif line == END:
            if not inside:
                raise ValueError("无效托管段")
            inside = False
        elif not inside:
            outside.append(line)
    if inside:
        raise ValueError("未结束托管段")
    safety = (SOURCE / "scaffold/.stignore").read_text().rstrip()
    # 用户段保留在最终拒绝规则之后，不能隐式扩大设备授权。
    text = f"{BEGIN}\n{safety}\n\n// 本机明确批准的集合\n"
    text += "".join(f"!/{collection(p)}\n" for p in sorted(approved))
    text += f"\n// 其余全部忽略\n*\n{END}\n"
    if outside:
        text += "\n".join(outside) + "\n"
    atomic(target, text)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("list", "approve", "revoke", "render"))
    p.add_argument("--vault-root", type=Path, required=True)
    p.add_argument("collections", nargs="*")
    args = p.parse_args()
    vault = args.vault_root.resolve()
    if not vault.is_dir():
        raise ValueError("Vault 不存在")
    path = config_path()
    data = json.loads(path.read_text()) if path.exists() else {}
    approved = set(data.get(str(vault), []))
    values = {collection(v) for v in args.collections}
    if args.action in ("approve", "revoke"):
        if not values:
            raise ValueError("必须明确指定集合")
        approved = approved | values if args.action == "approve" else approved - values
        data[str(vault)] = sorted(approved)
        atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if args.action == "render":
        render(vault, approved)
    print(json.dumps(sorted(approved), ensure_ascii=False))


if __name__ == "__main__":
    main()
