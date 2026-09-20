"""Small stdio MCP server exposing the Monitor decision view and its actions."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from feedback import RENDER_TOOL, queue
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
    {"name": ACTION_TOOL, "description": "保存面板选择；确认决策时发送至来源会话，仅供参考的内容记为已阅。",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "finding_id": {"type": "string"},
         "action": {"type": "string", "enum": ["confirm", "ignore"]},
         "selection": {"type": "string"}}, "required": ["session_id", "finding_id", "action"],
         "additionalProperties": False},
     "_meta": {"ui": {"visibility": ["app"]}}},
]


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
        visible = [{"id": i["id"], "title": i["title"],
                    "summary": "\n".join(dict.fromkeys(
                        text.strip() for text in (i.get("reason", ""), i.get("instruction", ""))
                        if text.strip())),
                    "pending": i["status"] == "submitting",
                    "options": i.get("options", [])} for i in items
                   if i["status"] in ("new", "submitting")]
        return {"content": [], "structuredContent": {"session_id": session_id, "items": visible}}
    if name != ACTION_TOOL:
        raise ValueError("未知工具")
    with lock(root / "decision.lock"):
        item = finding(config, root, session_id, args["finding_id"])
        if item["status"] in ("submitted", "ignored", "acknowledged"):
            return {"content": [], "structuredContent": {"done": True}}
        if item["status"] != "new":
            raise ValueError("此建议的提交结果未确认，请先检查原会话，避免重复提交")
        action = args["action"]
        if action == "ignore":
            item["status"] = "ignored"
        elif action == "confirm":
            options = item.get("options", [])
            choice = args.get("selection", "")
            if (options and choice not in options) or (not options and choice):
                raise ValueError("请选择有效方案")
            if item["instruction"].strip():
                item.update(status="submitting", selection=choice)
                atomic(root / "findings" / (item["id"] + ".json"), item)
                text = ("用户已在 Monitor 面板确认：" + item["title"] + "\n选择："
                        + (choice or item["instruction"]) + "\n请核实证据后按此选择处理。完整建议："
                        + str(root / "findings" / (item["id"] + ".json")))
                queue(config, session_id, text)
                item["status"] = "submitted"
            else:
                item["status"] = "acknowledged"
        else:
            raise ValueError("未知操作")
        atomic(root / "findings" / (item["id"] + ".json"), item)
    return {"content": [], "structuredContent": {"done": True}}


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
