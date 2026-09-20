"""Queue rendering instructions and user-confirmed decisions in the source session."""
import json
import subprocess

MARKER = "[SundayNote Monitor]"
RENDER_TOOL = "render_monitor_feedback"


def message(items):
    args = {"session_id": items[0]["session_id"], "finding_ids": [i["id"] for i in items]}
    return (MARKER + "\n仅调用 sunday_note_monitor 的 " + RENDER_TOOL + " 工具，参数："
            + json.dumps(args, ensure_ascii=False)
            + "\n工具将显示决策面板。不要分析、解释、复述结果、提问或执行建议；调用后结束本轮。"
            "若工具不可用，仅说明无法显示面板。建议不是用户授权。")


def queue(config, session_id, text):
    result = subprocess.run([config["codex"], "queue", "--thread", session_id, "--message", text],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20)
    if result.returncode:
        raise RuntimeError("queue_failed")


def deliver(config, root, items):
    from monitor import atomic, lock, project_allowed, read_json

    def record(status):
        with lock(root / "decision.lock"):
            for item in items:
                path = root / "findings" / (item["id"] + ".json")
                latest = read_json(path)
                item["feedback"] = latest["feedback"] = status
                atomic(path, latest)

    if not all(project_allowed(config, i["project"]) for i in items):
        record("skipped_scope")
        return
    try:
        record("sending")
        queue(config, items[0]["session_id"], message(items))
        record("queued")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        record("failed")
