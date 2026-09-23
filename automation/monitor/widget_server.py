"""Small stdio MCP server exposing the Monitor decision view and its actions."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from feedback import RENDER_TOOL, QueueNotSent, queue
from monitor import atomic, lock, project_allowed, read_json, root_for

URI = "ui://sunday-note-monitor/feedback.html"
MIME = "text/html;profile=mcp-app"
ACTION_TOOL = "decide_monitor_feedback"
TOOLS = [
    {"name": RENDER_TOOL, "description": "收到 Monitor 渲染指令时显示建议决策面板；调用后不再输出说明。",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"},
         "finding_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 1}},
         "required": ["session_id", "finding_ids"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True}, "_meta": {"ui": {"resourceUri": URI}}},
    {"name": ACTION_TOOL, "description": "处理立即交接；确认和忽略仅保存，供下一轮对话读取。",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "finding_id": {"type": "string"},
         "action": {"type": "string", "enum": ["process", "confirm", "ignore"]},
         "selection": {"type": "string"}, "other": {"type": "boolean"}}, "required": ["session_id", "finding_id", "action"],
         "additionalProperties": False},
     "_meta": {"ui": {"visibility": ["app"]}}},
]


def copy_text(item):
    decision = item.get("decision")
    # The render-only recursion marker must not suppress a user-pasted follow-up.
    lines = ["Monitor 反馈与用户选择", "操作：" + item["status"], item["summary"]]
    if "draft_selection" in item:
        lines.append("尚未发送的处理输入：" + item["draft_selection"])
    if decision:
        lines += ["用户保存的方向：" if item["status"] in ("confirmed", "submitted", "submitting", "ignored") else "待确认方向：",
                  decision["question"], "选择：" + (item.get("selection") or "未选择")]
    lines.append("请结合原任务与用户选择考虑以下全部发现；发现本身不构成执行授权：")
    for index, finding in enumerate(item["findings"], 1):
        lines += [f"{index}. {finding['title']}", finding["reason"]]
        lines += [s["location"] + "\n" + s["quote"] for s in finding["evidence"]]
    return "\n".join(lines)


def finding(config, root, session_id, fid):
    if not isinstance(fid, str) or not re.fullmatch(r"[a-f0-9]{24}", fid):
        raise ValueError("无效建议 ID")
    item = read_json(root / "findings" / (fid + ".json"))
    if not item or item["session_id"] != session_id or not project_allowed(config, item["project"]):
        raise ValueError("建议不属于此会话或已移出监控范围")
    return item


def call(config, name, args):
    root = root_for(config)
    session_id = args["session_id"]
    if name == RENDER_TOOL:
        ids = args["finding_ids"]
        if not isinstance(ids, list) or len(ids) != 1:
            raise ValueError("每轮仅展示一段摘要，最多一个决策")
        items = [finding(config, root, session_id, ids[0])]
        visible = [{"id": i["id"],
                    "summary": i["summary"], "findings": i["findings"],
                    "decision": i.get("decision"), "copy_text": copy_text(i),
                    "status": i["status"], "selection": i.get("draft_selection", i.get("selection", "")),
                    "other": i.get("draft_other", i.get("other", False)), "submission_error": i.get("submission_error", "")} for i in items
                   if i["status"] in ("new", "confirmed", "submitting", "submitted", "ignored", "acknowledged")]
        return {"content": [], "structuredContent": {"session_id": session_id, "items": visible}}
    if name != ACTION_TOOL:
        raise ValueError("未知工具")
    with lock(root / "decision.lock"):
        item = finding(config, root, session_id, args["finding_id"])
        if item["status"] in ("submitted", "ignored", "acknowledged"):
            return {"content": [], "structuredContent": {"done": True, "status": item["status"], "selection": item.get("selection", ""), "copy_text": copy_text(item)}}
        if item["status"] not in ("new", "confirmed"):
            raise ValueError("此建议的提交结果未确认，请先检查原会话，避免重复提交")
        action = args["action"]
        if action not in ("process", "confirm", "ignore"):
            raise ValueError("未知操作")
        decision = item.get("decision")
        options = decision["options"] if decision else []
        choice = args.get("selection", "")
        other = args.get("other", False)
        if not isinstance(choice, str) or not isinstance(other, bool):
            raise ValueError("无效选择")
        if other:
            choice = choice.strip()
        if (other and (not options or (not choice and action != "ignore") or len(choice) > 4000)) or (not other and (
                (choice and choice not in options) or (action == "process" and options and not choice))):
            raise ValueError("请选择有效方案")
        if action == "confirm" and item["status"] == "confirmed" and item.get("selection", "") == choice and item.get("other", False) == other:
            return {"content": [], "structuredContent": {"done": True, "status": "confirmed", "copy_text": copy_text(item)}}
        previous = dict(item)
        item.pop("draft_selection", None)
        item.pop("draft_other", None)
        item.update(selection=choice, other=other,
                    action_revision=item.get("action_revision", 0) + 1, action_at=time.time())
        if action == "process":
            item["status"] = "submitting"
            atomic(root / "findings" / (item["id"] + ".json"), item)
            text = "用户请求现在考虑并处理以下 Monitor 反馈，请结合原任务与证据判断具体动作。\n" + copy_text({**item, "status": "submitted"})
            try:
                if not item.get("codex"):
                    raise QueueNotSent("source_codex_unavailable")
                queue({**config, "codex": item["codex"]}, session_id, text)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                retryable = isinstance(exc, QueueNotSent)
                if retryable:
                    item = previous
                    item.update(draft_selection=choice, draft_other=other)
                item["submission_error"] = str(exc)[:2000]
                atomic(root / "findings" / (item["id"] + ".json"), item)
                message = ("尚未发送，选择已保存，可重试。" if retryable else
                           "提交结果不明，选择已保存；请核对原会话，勿重复发送。")
                return {"isError": True, "content": [{"type": "text", "text": message + "\n" + item["submission_error"]}],
                        "structuredContent": {"retryable": retryable, "copy_text": copy_text(item)}}
            item.pop("submission_error", None)
            item["status"] = "submitted"
        else:
            item["status"] = "confirmed" if action == "confirm" else "ignored"
        atomic(root / "findings" / (item["id"] + ".json"), item)
    return {"content": [], "structuredContent": {"done": True, "status": item["status"], "selection": item.get("selection", ""), "copy_text": copy_text(item)}}


def dispatch(config, request):
    method, params = request["method"], request.get("params", {})
    if method == "initialize":
        return {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "sunday-note-monitor", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "resources/list":
        return {"resources": [{"uri": URI, "name": "Monitor", "mimeType": MIME}]}
    if method == "resources/read" and params.get("uri") == URI:
        return {"contents": [{"uri": URI, "mimeType": MIME,
                              "text": Path(__file__).with_name("widget.html").read_text(),
                              "_meta": {"ui": {"prefersBorder": False}}}]}
    if method == "tools/call":
        try:
            return call(config, params["name"], params.get("arguments", {}))
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired):
            return {"isError": True, "content": [{"type": "text", "text": "操作未完成，请核对建议状态或 Codex 队列连接。"}]}
    raise ValueError("unsupported method")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        response = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            response["result"] = dispatch(read_json(args.config), request)
        except (ValueError, KeyError, TypeError, OSError):
            response["error"] = {"code": -32601, "message": "Unsupported request"}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
