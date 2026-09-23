"""Bounded, read-only Git and transcript facts; no semantic routing rules."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

MAX_SNAPSHOT = 8 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.fsmonitor=false", *args],
                          env=env, capture_output=True, timeout=10, check=True).stdout


def head(cwd):
    try:
        return git(cwd, "rev-parse", "HEAD").decode().strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_target(cwd, scratch, previous_head=""):
    """Freeze a cumulative target. Workspace changes are never claimed as turn edits."""
    try:
        repo = Path(git(cwd, "rev-parse", "--show-toplevel").decode().strip())
        current = head(repo)
        base = current
        scope = "workspace_background_not_turn_attribution"
        if not previous_head and current and not git(repo, "status", "--porcelain", "-z"):
            try:
                base = git(repo, "rev-parse", "HEAD^").decode().strip()
                scope = "latest_commit_candidate_not_turn_attribution"
            except subprocess.SubprocessError:
                pass  # Initial commit: no supported parent range.
        if previous_head and previous_head != current:
            try:
                git(repo, "merge-base", "--is-ancestor", previous_head, current)
                base = previous_head
            except subprocess.SubprocessError:
                pass
        if not base:
            return {"available": False, "reason": "repository_has_no_commit"}
        patch = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--")
        staged = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", "--cached", "--")
        unstaged = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", "--")
        names = git(repo, "diff", "--name-only", "-z", base, "--").split(b"\0")
        staged_names = git(repo, "diff", "--cached", "--name-only", "-z", "--").split(b"\0")
        untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        names = sorted({os.fsdecode(n) for n in names + staged_names + untracked if n})
        directory = Path(scratch) / "target"
        directory.mkdir()
        total = len(patch) + len(staged) + len(unstaged)
        complete = total <= MAX_SNAPSHOT
        if complete:
            (directory / "change.patch").write_bytes(patch)
            (directory / "staged.patch").write_bytes(staged)
            (directory / "unstaged.patch").write_bytes(unstaged)
        files = []
        for name in names:
            path = repo / name
            entry = {"path": str(path), "relative": name, "untracked": os.fsencode(name) in untracked}
            if os.fsencode(name) in staged_names:
                try:
                    data = git(repo, "show", ":" + name)
                    total += len(data)
                    if total <= MAX_SNAPSHOT:
                        frozen = directory / (str(len(files)) + ".index")
                        frozen.write_bytes(data)
                        entry.update(index_sha256=sha(data), index_snapshot=str(frozen))
                    else:
                        complete = False
                except subprocess.SubprocessError:
                    entry.update(index_sha256="missing", index_snapshot=None)
            if path.is_symlink() or not path.resolve().is_relative_to(repo.resolve()):
                entry.update(sha256="unavailable", reason="symlink")
                complete = False
            elif not path.exists():
                entry.update(sha256="missing", snapshot=None)
            elif not path.is_file() or path.stat().st_size + total > MAX_SNAPSHOT:
                entry.update(sha256="unavailable", reason="snapshot_size_limit")
                complete = False
            else:
                data = path.read_bytes()
                total += len(data)
                frozen = directory / (str(len(files)) + ".source")
                frozen.write_bytes(data)
                entry.update(sha256=sha(data), snapshot=str(frozen))
            files.append(entry)
        identity = sha(json.dumps([str(repo), base, current, sha(patch), sha(staged), sha(unstaged),
                                  [(f["relative"], f["sha256"]) for f in files]], sort_keys=True).encode())
        stable = head(repo) == current and git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--") == patch
        return {"available": True, "repository": str(repo), "base": base, "head": current,
                "target_id": identity, "scope": scope,
                "patch_sha256": sha(patch), "untracked": sorted(os.fsdecode(n) for n in untracked if n),
                "staged_patch_sha256": sha(staged), "unstaged_patch_sha256": sha(unstaged),
                "complete": complete and stable, "files": files,
                "patch": str(directory / "change.patch") if (directory / "change.patch").exists() else None,
                "staged_patch": str(directory / "staged.patch") if (directory / "staged.patch").exists() else None,
                "unstaged_patch": str(directory / "unstaged.patch") if (directory / "unstaged.patch").exists() else None,
                "stat": git(repo, "diff", "--stat", base, "--").decode(errors="replace"),
                "documentation_entries": [str(repo / n) for n in ("AGENTS.md", "README.md", "docs", "install/README.md") if (repo / n).exists()]}
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return {"available": False, "reason": type(exc).__name__}


def target_current(target):
    if not target.get("available"):
        return True
    if head(target["repository"]) != target["head"]:
        return False
    try:
        if sha(git(target["repository"], "diff", "--no-ext-diff", "--no-textconv", "--binary", target["base"], "--")) != target["patch_sha256"]:
            return False
        for key, args in (("staged_patch_sha256", ("--cached",)), ("unstaged_patch_sha256", ())):
            if key in target and sha(git(target["repository"], "diff", "--no-ext-diff", "--no-textconv", "--binary", *args, "--")) != target[key]:
                return False
        if sorted(os.fsdecode(n) for n in git(target["repository"], "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if n) != target["untracked"]:
            return False
        for file in target.get("files", []):
            if file["sha256"] != "unavailable" and not version_current(file):
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def related_repositories(turns):
    """Only explicit tool paths; no keyword-based importance or change attribution."""
    paths = set()
    for turn in turns:
        for tool in turn.get("tool_sources", []):
            text = tool["excerpt"]
            paths.update(tool.get("declared_edit_paths", []))
            for value in re.findall(r'"(?:workdir|cwd)"\s*:\s*("(?:[^"\\]|\\.)*")', text):
                try:
                    paths.add(json.loads(value))
                except ValueError:
                    pass
            paths.update(re.findall(r'^\*\*\* (?:Update|Add|Delete) File: (/.+)$', text, re.M))
    repos = set()
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            continue
        try:
            repos.add(git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel").decode().strip())
        except (OSError, subprocess.SubprocessError):
            continue
    return sorted(repos)


def version_current(version):
    try:
        path = Path(version["path"])
        return path.is_absolute() and not path.is_symlink() and (
            (not path.exists() and version["sha256"] == "missing") or
            (path.is_file() and path.stat().st_size <= MAX_SNAPSHOT and sha(path.read_bytes()) == version["sha256"]))
    except (OSError, KeyError):
        return False


def turn_tools(path, session_id, turn_ids):
    """Small source-indexed tool excerpts, not an inferred list of successful edits."""
    result = {turn: [] for turn in turn_ids}
    try:
        source = Path(path)
        active, valid, calls = None, False, {}
        with source.open() as stream:
            for number, line in enumerate(stream, 1):
                item = json.loads(line)
                p = item.get("payload", {})
                if item.get("type") == "session_meta":
                    valid = p.get("id") == session_id
                if item.get("type") == "turn_context" or (item.get("type") == "event_msg" and p.get("type") == "task_started"):
                    active = p.get("turn_id")
                if not valid or active not in result or item.get("type") != "response_item":
                    continue
                if p.get("type") in ("function_call", "custom_tool_call"):
                    calls[p.get("call_id")] = p.get("name", "")
                if p.get("type") in ("function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"):
                    text = str(p.get("arguments", p.get("input", p.get("output", ""))))
                    if sum(len(x["excerpt"]) for x in result[active]) < 16000:
                        result[active].append({"source": f"{source}:{number}", "kind": p["type"],
                            "tool": p.get("name", calls.get(p.get("call_id"), "unknown")),
                            "declared_edit_paths": re.findall(r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', text.replace('\\n', '\n'), re.M)
                                if p["type"] in ("function_call", "custom_tool_call") else [],
                            "excerpt": text[:2000], "truncated": len(text) > 2000})
    except (OSError, ValueError, TypeError):
        pass
    return result


if __name__ == "__main__":
    # Read evidence once; return a digest for the child report's read_versions.
    import sys
    p = Path(sys.argv[1]).resolve()
    if not p.is_file() or p.stat().st_size > MAX_SNAPSHOT:
        raise SystemExit("not a bounded regular file")
    data = p.read_bytes()
    print(json.dumps({"path": str(p), "sha256": sha(data)}, ensure_ascii=False))
    print(data.decode(errors="replace"))
