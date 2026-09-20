"""Small native Linux suggestion UI; all actions only update monitor state."""
import os
from html import escape
import shutil
import subprocess

from monitor import atomic, evidence_path, lock, read_json, spawn


def zenity(*args, input_text=None):
    r = subprocess.run(["zenity", "--title=Monitor 建议", "--width=780", "--height=440", *args],
                       input=input_text, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def update(root, fid, **changes):
    with lock(root / "queue.lock"):
        path = root / "findings" / (fid + ".json")
        item = read_json(path)
        item.update(changes)
        atomic(path, item)


def items(root):
    return sorted((read_json(p) for p in (root / "findings").glob("*.json")),
                  key=lambda x: x["created"], reverse=True)


def copy_text(text):
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        command = ["wl-copy"]
    elif shutil.which("xclip"):
        command = ["xclip", "-selection", "clipboard"]
    elif shutil.which("xsel"):
        command = ["xsel", "--clipboard", "--input"]
    else:
        zenity("--error", "--text=缺少剪贴板工具，请安装 xclip 或 wl-clipboard。")
        return False
    subprocess.run(command, input=text, text=True, check=True, timeout=5)
    return True


def copy_instruction(item):
    evidence = "\n".join(f"- {s['location']}：{s['quote']}" for s in item["evidence"])
    choice = f"\n用户选择：{item['selection']}" if item.get("selection") else ""
    return (f"项目：{item['project']}\n来源会话：{item['session_id']}，轮次：{item['turn_id']}\n"
            f"待核查：{item['title']}\n依据：\n{evidence}\n理由：{item['reason']}\n"
            f"建议：{item['instruction']}{choice}\n请先核对当前文件及原始证据；以上是只读 Monitor 的建议，尚未实施。")


def brief(text, limit=160):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def detail(root, item):
    item = dict(item)
    while True:
        text = brief(item["title"], 80) + "\n" + brief(item["reason"])
        if item.get("selection"):
            text += "\n已选：" + brief(item["selection"], 100)
        text += "\n选择只记录意向；点击复制后可交给主 Agent。"
        options = item.get("options", [])
        rows = []
        for i, option in enumerate(options):
            rows += ["option:" + str(i), brief(option, 100)]
        rows += ["other", "其他选择…", "copy", "复制完整处理指令",
                 "expand", "展开完整内容", "source", "查看依据",
                 "ignore", "忽略", "save", "暂存"]
        action = zenity("--list", "--no-markup", "--text=" + text,
                        "--column=ID", "--column=方案 / 操作", "--hide-column=1",
                        "--print-column=1", *rows)
        if action is None:
            return
        if action.startswith("option:") or action == "other":
            choice = (options[int(action.split(":")[1])] if action != "other"
                      else zenity("--entry", "--text=补充选择（可取消）："))
            if choice:
                item["selection"] = choice
                update(root, item["id"], selection=choice, status="selected")
        elif action == "copy":
            if copy_text(copy_instruction(item)):
                update(root, item["id"], status="copied")
                return
        elif action in ("ignore", "save"):
            update(root, item["id"], status="ignored" if action == "ignore" else "saved")
            return
        elif action == "expand":
            full = copy_instruction(item)
            if options:
                full += "\n\n可选方案：\n" + "\n".join(options)
            zenity("--text-info", input_text=full)
        elif action == "source":
            location = zenity("--list", "--no-markup", "--column=来源",
                              *[s["location"] for s in item["evidence"]])
            if location:
                path = evidence_path(location)
                if location.startswith(("https://", "http://")):
                    subprocess.Popen(["xdg-open", location], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif path.is_absolute() and path.is_file():
                    # Display as text, never execute a local script.
                    zenity("--text-info", "--filename=" + str(path))


def panel(config_path, config, root):
    with lock(root / "panel.lock", blocking=False) as acquired:
        if not acquired:
            return
        while True:
            rows = [i for i in items(root) if i["status"] != "ignored"]
            state = read_json(root / "state.json", {})
            args = ["--list", "--column=ID", "--column=状态", "--column=建议", "--hide-column=1",
                    "--print-column=1", "--text=" + ("运行状态：" + state.get("error", "正常"))]
            args += ["retry", "操作", "重试待处理轮次", "toggle", "操作", "暂停 / 恢复 Monitor"]
            for item in rows:
                args += [item["id"], item["status"], item["title"]]
            fid = zenity(*args)
            if fid is None:
                return
            if fid == "retry":
                spawn(config_path, "retry")
            elif fid == "toggle":
                spawn(config_path, "pause" if state.get("enabled", True) else "resume")
                return
            else:
                item = next((i for i in rows if i["id"] == fid), None)
                if item:
                    update(root, fid, notified=True)
                    detail(root, item)


def notify(config_path, config, root):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    with lock(root / "notify.lock", blocking=False) as acquired:
        if not acquired:
            return
        rows = [i for i in items(root) if i["status"] == "new" and not i.get("notified")]
        if not rows:
            return
        base = ["notify-send", "--app-name=SundayNoteAgent", "--expire-time=10000"]
        body = escape(brief(rows[0]["title"], 80))
        if len(rows) > 1:
            body += f"（共 {len(rows)} 条）"
        body += "\n可从应用菜单打开 Monitor 建议。"
        help_text = subprocess.run(["notify-send", "--help"], capture_output=True, text=True).stdout
        if "--action" in help_text:
            try:
                r = subprocess.run([*base, "--action=open=查看建议", "--wait", "Monitor",
                                    body],
                                   capture_output=True, text=True, timeout=15)
                if r.stdout.strip() == "open":
                    spawn(config_path, "panel")
            except subprocess.TimeoutExpired:
                pass
        else:
            subprocess.run([*base, "Monitor", body], timeout=5)
        for item in rows:
            update(root, item["id"], notified=True)
