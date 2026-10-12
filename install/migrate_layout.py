#!/usr/bin/env python3
"""按显式路径映射迁移 Vault；先演练、确认清单，再 apply。"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, quote

LEGACY = ("10_原始材料", "20_每日记录", "21_每周记录", "22_每月记录", "23_项目复盘", "30_知识库", "40_个人写作", "个人上下文.md")
PROTECTED = {"个人写作", "40_个人写作"}
WIKI = re.compile(r"\[\[([^\]\n]+)\]\]")
MARKDOWN = re.compile(r"(?P<prefix>!?\[[^\]\n]*\]\()(?P<target><[^>\n]+>|[^\s)]+)(?P<tail>(?:\s+\"[^\"\n]*\")?\))")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def relative(value):
    p = Path(value)
    if p.is_absolute() or not p.parts or any(x in ("..", ".") or x.startswith(".") for x in p.parts):
        raise ValueError(f"无效相对路径: {value}")
    return p.as_posix()


def check_layout(vault):
    old = [name for name in LEGACY if (vault / name).exists()]
    if old:
        raise ValueError("检测到旧布局，请先运行 migrate_layout.py：" + ", ".join(old))


def inventory(root):
    """只收集正文和附件；不进入工具、状态、导入缓存或独立写作。"""
    files = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in PROTECTED
                   and d not in ("SundayNoteTools", "SundayNoteAgent")
                   and not (Path(directory) / d).is_symlink()]
        for name in names:
            p = Path(directory) / name
            if not name.startswith(".") and p.is_file() and not p.is_symlink():
                files[p.relative_to(root).as_posix()] = digest(p)
    return files


def mapped(path, mappings):
    for old, new in sorted(mappings.items(), key=lambda pair: -len(pair[0])):
        if path == old or path.startswith(old + "/"):
            return new + path[len(old):]
    return path


def resolve_target(root, source, value, wiki, files):
    value = unquote(value)
    if not value or value.startswith(("#", "//")) or re.match(r"^[a-zA-Z][\w+.-]*:", value):
        return None
    if wiki:
        candidates = [root / value, root / Path(source).parent / value]
    else:
        candidates = [root / Path(source).parent / value, root / value]
    for candidate in candidates:
        for p in (candidate, Path(str(candidate) + ".md")):
            try:
                rel = p.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in files or any(name.startswith(rel + "/") for name in files):
                return rel
            # 外部页面可核对写作目标路径，但不读取、索引或改写其正文。
            if Path(rel).parts and Path(rel).parts[0] in PROTECTED and p.is_file() and not any(
                parent.is_symlink() for parent in (p, *p.parents) if parent.is_relative_to(root)
            ):
                return rel
    if wiki and "/" not in value:
        matches = [p for p in files if Path(p).name == value or Path(p).stem == value]
        if len(matches) == 1:
            return matches[0]
    return ""


def rewrite(root, source, text, mappings, files):
    broken = []
    new_files = {mapped(name, mappings): identity for name, identity in files.items()}
    def target(value, wiki):
        angle = value.startswith("<") and value.endswith(">")
        raw = value[1:-1] if angle else value
        path, mark, anchor = raw.partition("#")
        resolved = resolve_target(root, source, path, wiki, files)
        if resolved is None:
            return value
        if resolved == "":
            broken.append(raw)
            return value
        destination = mapped(resolved, mappings)
        new_source = mapped(source, mappings)
        if destination == resolved and new_source == source:
            return value
        if resolve_target(root, new_source, path, wiki, new_files) == destination:
            return value
        result = destination if wiki else os.path.relpath(destination, Path(new_source).parent).replace(os.sep, "/")
        if not wiki and not angle:
            result = re.sub(r"[\s%()<>#]", lambda m: quote(m[0], safe=""), result)
        result += mark + anchor
        return f"<{result}>" if angle else result
    def wiki_replace(m):
        value, separator, alias = m[1].partition("|")
        if separator and value.endswith("\\"):
            value, separator = value[:-1], "\\|"
        return "[[" + target(value, True) + separator + alias + "]]"
    text = WIKI.sub(wiki_replace, text)
    text = MARKDOWN.sub(lambda m: m["prefix"] + target(m["target"], False) + m["tail"], text)
    # sources 的非 wikilink 路径只在 frontmatter 内更新，不改正文中的历史叙述。
    if text.startswith("---\n") and "\n---" in text[4:]:
        end = text.index("\n---", 4)
        header = text[:end]
        for old, new in sorted(mappings.items(), key=lambda x: -len(x[0])):
            header = header.replace('"' + old + '/', '"' + new + '/').replace("'" + old + '/', "'" + new + '/')
        text = header + text[end:]
    return text, broken


def plan(root, mapping_file, state):
    if state.exists():
        raise ValueError("状态目录已存在，使用新的演练目录")
    mappings = {relative(k): relative(v) for k, v in json.loads(mapping_file.read_text()).items()}
    # 独立写作只允许整目录不解析内容的搬迁，必须在映射中显式指定。
    files = inventory(root)
    moves = {old: mapped(old, mappings) for old in files}
    conflicts = []
    destinations = {}
    for old, new in moves.items():
        if new in destinations and destinations[new] != old:
            conflicts.append(f"多个来源: {new}")
        destinations[new] = old
        if old != new and (root / new).exists():
            conflicts.append(f"目标已存在: {new}")
        if any(p.is_symlink() for p in (root / new, *(root / new).parents)):
            conflicts.append(f"符号链接目标: {new}")
    protected = {old: new for old, new in mappings.items() if Path(old).parts[0] in PROTECTED and (root / old).exists()}
    for old, new in protected.items():
        if old != new and (root / new).exists():
            conflicts.append(f"独立写作目标已存在: {new}")
    state.mkdir(parents=True, mode=0o700)
    changes, broken, diff = {}, {}, []
    for old in files:
        if Path(old).suffix != ".md":
            continue
        text = (root / old).read_bytes().decode("utf-8")
        result, missing = rewrite(root, old, text, mappings, files)
        if missing:
            broken[old] = missing
        if result != text:
            changes[old] = hashlib.sha256(result.encode()).hexdigest()
            out = state / "rewritten" / old
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(result)
            diff.extend(difflib.unified_diff(text.splitlines(True), result.splitlines(True), old, moves[old]))
    data = {"vault": str(root), "mapping": mappings, "files": files, "moves": moves,
            "changes": changes, "protected": protected, "conflicts": conflicts, "broken_before": broken,
            "phase": "planned"}
    write(state / "manifest.json", data)
    (state / "links.diff").write_text("".join(diff))
    return data


def apply(root, state, data):
    if data["phase"] != "planned" or data["conflicts"]:
        raise ValueError("计划非待执行状态或存在冲突")
    if inventory(root) != data["files"]:
        raise ValueError("源文件已漂移，请重新生成清单")
    for old, new in {**data["moves"], **data["protected"]}.items():
        dst = root / new
        if any(p.is_symlink() for p in (dst, *dst.parents)) or (old != new and dst.exists()):
            raise ValueError(f"目标已出现或路径被替换: {new}")
    for old, sha in data["changes"].items():
        if digest(state / "rewritten" / old) != sha:
            raise ValueError("改写快照已漂移")
    # 完整备份，包括不解析的个人写作、配置与导入缓存，不依赖 Git 跟踪范围。
    shutil.copytree(root, state / "backup", symlinks=True)
    data["phase"] = "applying"
    data["completed"] = []
    write(state / "manifest.json", data)
    for old, new in data["moves"].items():
        if old == new and old not in data["changes"]:
            continue
        dst = root / new
        dst.parent.mkdir(parents=True, exist_ok=True)
        if old != new:
            (root / old).rename(dst)
        if old in data["changes"]:
            shutil.copyfile(state / "rewritten" / old, dst)
        data["completed"].append(old)
        write(state / "manifest.json", data)
    for old, new in data["protected"].items():
        if old != new:
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            (root / old).rename(root / new)
    data["phase"] = "applied"
    write(state / "manifest.json", data)
    return verify(root, data, state / "backup")


def verify(root, data, before):
    current = inventory(root)
    expected = {data["moves"][old]: data["changes"].get(old, sha) for old, sha in data["files"].items()}
    errors = [p for p, sha in expected.items() if current.get(p) != sha]
    extra = sorted(set(current) - set(expected))
    broken = {}
    for old, new in data["moves"].items():
        if Path(new).suffix != ".md" or not (root / new).is_file():
            continue
        _, missing = rewrite(root, new, (root / new).read_bytes().decode("utf-8"), {}, current)
        # 从原始正文重新解析基线，不信任旧解析器记录的 broken_before。
        _, original_missing = rewrite(before, old, (before / old).read_bytes().decode("utf-8"), {}, data["files"])
        added = [v for v in missing if v not in original_missing]
        if added:
            broken[new] = added
    return {"ok": not (errors or extra or broken), "identity_errors": errors, "unexpected_files": extra, "new_broken": broken}


def rollback(root, state, data):
    if data["phase"] != "applied":
        raise ValueError("仅完整迁移可自动回退；中断请核查 backup 与 completed 清单")
    result = verify(root, data, state / "backup")
    if result["identity_errors"] or result["unexpected_files"]:
        raise ValueError("迁移后出现漂移或损坏，拒绝覆盖；请使用 backup 人工恢复")
    for old, new in reversed(list(data["moves"].items())):
        if old != new:
            (root / old).parent.mkdir(parents=True, exist_ok=True)
            (root / new).rename(root / old)
        if old in data["changes"]:
            shutil.copy2(state / "backup" / old, root / old)
    for old, new in data["protected"].items():
        if old != new:
            (root / new).rename(root / old)
    data["phase"] = "rolled_back"
    write(state / "manifest.json", data)
    return {"ok": inventory(root) == data["files"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("check", "dry-run", "apply", "verify", "rollback"))
    p.add_argument("--vault-root", type=Path, required=True)
    p.add_argument("--mapping", type=Path)
    p.add_argument("--state", type=Path)
    a = p.parse_args()
    root = a.vault_root.resolve()
    if a.action == "check":
        check_layout(root)
        return
    if not a.state or a.state.resolve().is_relative_to(root):
        raise ValueError("状态目录必须在 Vault 外")
    state = a.state.resolve()
    if a.action == "dry-run":
        if not a.mapping:
            raise ValueError("需要显式 --mapping")
        data = plan(root, a.mapping, state)
        result = {"files": len(data["files"]), "moves": sum(k != v for k, v in data["moves"].items()),
                  "rewrites": len(data["changes"]), "conflicts": data["conflicts"], "state": str(state)}
    else:
        data = json.loads((state / "manifest.json").read_text())
        if data["vault"] != str(root):
            raise ValueError("清单不属于此 Vault")
        result = apply(root, state, data) if a.action == "apply" else rollback(root, state, data) if a.action == "rollback" else verify(root, data, state / "backup")
        write(state / (a.action + "-result.json"), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok") is False or result.get("conflicts"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
