#!/usr/bin/env python3
"""插件运行入口：代码随插件更新，绑定与运行状态留在本机。"""
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def binding_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "sunday-note-agent" / "config.json"


def main():
    command = sys.argv[1]
    if command != "context":
        raise SystemExit("插件仅提供 context 入口；Monitor 使用独立本地安装。")
    path = binding_path()
    if not path.is_file():
        return
    binding = json.loads(path.read_text())
    if command == "context":
        # 只提供路由信息，不自动读取私人文件或覆盖当前开发目录。
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
            f"SundayNoteAgent 绑定 vault：{binding['vault']}；模式：{binding['mode']}。"
            "仅在使用知识库 Skills 时，以此为知识路径根，先读取其 AGENTS.md；"
            "不要把当前开发工作区当作 vault。工作请求不读取个人来源。"}}, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
