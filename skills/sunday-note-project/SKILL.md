---
name: sunday-note-project
description: 用户在本地要求创建或接入 Vault 项目、建立项目维护文档、绑定维护会话，或镜像项目 Agent 规则时使用。
---

# Project

仅在本地执行项目接入、绑定与规则镜像；远程环境不启动本工作流。

镜像仓库规则时使用 [规则镜像](references/rule-mirror.md)，不重新登记项目或修改回流开关。

1. 确认目标 Vault、工作／个人归属、项目名称、关联仓库与目标。读取 Vault 根规则；已有项目先读总览并核对四文档映射，保留人工内容，不重复创建或自动重命名。
2. 新项目使用 `assets/项目维护/` 四份模板。填入已知事实，删除占位示例和无意义栏目；无任务或交付时写“暂无”，不虚构依赖图。保留简短维护注释。Raw／Wiki 未建立时只注明约定位置，不创建空目录、空链接或扩大授权。
3. 项目侧只需文档位置、关键证据输出、验证结果和限制等交付提示；不解释后台调度，不机械新增 AGENTS.md。修改代码仓库规则须单独取得授权并合并已有内容。
4. 用 `scripts/project_binding.py` 登记明确确认的项目与来源。标识首次由用户可理解的简短名称确定，后续沿用；先 `list` 核对已有绑定。`register` 的来源列表是完整列表，变更已有绑定须先说明差异。脚本不创建或整理文档。
5. 接入项目包含绑定维护会话和开启 Raw 回流。确认用户选定的会话、当前 App Codex 绝对路径、Raw 与运行状态写权限及队列能力，再向 `register` 同时传入 `--session-id` 和 `--codex`，首次接入一并完成；不从最近会话猜测身份，不以文件存在代替运行验证。资料未齐时可只登记，但须说明待接入、未启用。重登记保留原有开启或暂停状态；已有关闭项目要完成接入或恢复回流时，明确确认后使用 Monitor 的 `enable`，不擅自恢复暂停。
6. 核对文档入口、维护会话与回流状态；Wiki 页面与同步仍独立授权。`bind` / `unbind` 用于后续更换或解除入口，不另建用户接入流程。普通资料整理使用 Ingest，不要求先注册项目。

```bash
python3 scripts/project_binding.py list --vault-root /path/to/vault
python3 scripts/project_binding.py register --vault-root /path/to/vault --project-id project-a --project-path 工作项目/项目甲 --raw-path 工作资料/项目甲 --source /path/to/repo --session-id <明确指定的会话ID> --codex /verified/app/codex
python3 scripts/project_binding.py bind --vault-root /path/to/vault --project-id project-a --client codex --session-id <明确指定的会话ID>
python3 scripts/project_binding.py unbind --vault-root /path/to/vault --project-id project-a
```

多个仓库重复传入 `--source`；无代码来源的项目可不传。非 Git 来源传入明确目录。项目特有约定放总览，不复制根规则。证据记录区分用户决定、助手声明、实际验证与推断，不复制完整日志。

用户明确批准到期 Wiki 维护时，用 `wiki-targets --vault-root <vault> --project-id <id> --page <知识库内已有页面.md>` 设置完整页面名单；多页重复 `--page`，撤销全部使用 `--clear`。先 `list` 核对并说明名单变化，不从文档链接推导授权。默认名单为空；只允许现存 Markdown 页面，不接受目录、通配符或符号链接。设置名单不启用 Monitor，不授予同步权限；Monitor 仅在到期批次使用领取时与当前仍获准的页面交集。
