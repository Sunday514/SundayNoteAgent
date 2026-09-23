"""Read the source desktop's state without a model or a second app server."""
import json
import socket
import struct
import uuid


def read_thread(pipe, session_id):
    if not pipe:
        raise RuntimeError("source_status_unavailable")
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(10)
        stream.connect(pipe)

        def read(size):
            data = b""
            while len(data) < size:
                part = stream.recv(size - len(data))
                if not part:
                    raise RuntimeError("status_connection_closed")
                data += part
            return data

        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "namespace": "codex_app", "tool": "read_thread",
            "arguments": {"threadId": session_id, "turnLimit": 1,
                          "includeOutputs": False, "maxOutputCharsPerItem": 0},
            "threadId": session_id, "turnId": "monitor-status",
            "callId": str(uuid.uuid4())}}
        data = json.dumps(request).encode()
        stream.sendall(struct.pack("<I", len(data)) + data)
        size = struct.unpack("<I", read(4))[0]
        if size > 8 * 1024 * 1024:
            raise RuntimeError("status_response_too_large")
        response = json.loads(read(size)).get("result", {})
        if not response.get("success"):
            raise RuntimeError("status_query_failed")
        for item in response.get("contentItems", []):
            if item.get("type") == "inputText":
                value = json.loads(item["text"])
                if value.get("thread", {}).get("id") != session_id:
                    raise RuntimeError("status_session_mismatch")
                turns = value.get("turns", [])
                return {"status": value["thread"]["status"]["type"],
                        "turn_id": turns[0]["id"] if turns else None,
                        "turn_status": turns[0]["status"] if turns else None}
        raise RuntimeError("status_response_missing")
