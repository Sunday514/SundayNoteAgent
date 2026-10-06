"""无模型宿主验收；安装成功不等于 Hooks/MCP 被宿主发现。"""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time

from build_plugin import build


class Client:
    def __init__(self, env, cwd):
        self.process = subprocess.Popen(["codex", "app-server", "--stdio"], env=env, cwd=cwd,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buffer = b""
        self.sequence = 0

    def call(self, method, params):
        self.sequence += 1
        p = self.process
        p.stdin.write((json.dumps({"id": self.sequence, "method": method, "params": params}) + "\n").encode())
        p.stdin.flush()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("id") == self.sequence:
                    if "error" in value:
                        raise ValueError(f"{method}: {value['error']}")
                    return value["result"]
            if select.select([p.stdout], [], [], 1)[0]:
                data = os.read(p.stdout.fileno(), 65536)
                if not data:
                    raise ValueError("宿主在验收期间退出")
                self.buffer += data
        raise ValueError(f"宿主验收超时：{method}")

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def require_capabilities(detail, hooks):
    required = {"sessionStart"}
    found = {h["eventName"] for h in hooks if h.get("pluginId") == "sunday-note-agent@sunday-note-local"}
    if not required <= found:
        raise ValueError("当前 Codex 未发现插件 Hooks（缺少 " + ", ".join(sorted(required - found)) +
                         "）；未切换真实配置。需兼容的宿主或经确认的用户级 Hook 桥接方案。")


def preflight(paper):
    # 不带远程 App 绑定，不复制认证，不运行模型、Hook 或连接远程服务。
    with tempfile.TemporaryDirectory(prefix="sunday-plugin-preflight-") as temp:
        root = Path(temp)
        home = root / "codex"
        home.mkdir()
        market = root / "market"
        build(market, paper)
        env = {**os.environ, "CODEX_HOME": str(home)}
        for args in (("marketplace", "add", str(market)), ("add", "sunday-note-agent@sunday-note-local")):
            subprocess.run(["codex", "plugin", *args, "--json"], env=env, cwd=root,
                           check=True, capture_output=True, timeout=30)
        client = Client(env, root)
        try:
            client.call("initialize", {"clientInfo": {"name": "sundaynote-preflight", "version": "1.0.0"},
                                       "capabilities": {"experimentalApi": True}})
            client.process.stdin.write(b'{"method":"initialized"}\n')
            client.process.stdin.flush()
            detail = client.call("plugin/read", {"marketplacePath": str(market / ".agents/plugins/marketplace.json"),
                                                 "pluginName": "sunday-note-agent"})["plugin"]
            hooks = client.call("hooks/list", {"cwds": [str(root)]})["data"][0]["hooks"]
            require_capabilities(detail, hooks)
        finally:
            client.close()
