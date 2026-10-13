---
name: sunday-note-project
description: 用户要求创建或接入 Vault 项目、建立项目维护文档、绑定维护会话，或镜像项目 Agent 规则时使用。
---

# Project

镜像仓库规则时使用 [规则镜像](references/rule-mirror.md)，不重新登记项目或修改回流开关。

1. 确认目标 Vault、工作／个人归属、项目名称、关联仓库与目标。读取 Vault 根规则；已有项目先读总览并核对四文档映射，保留人工内容，不重复创建或自动重命名。
2. 新项目使用 `assets/项目维护/` 四份模板。填入已知事实，删除占位示例和无意义栏目；无任务或交付时写“暂无”，不虚构依赖图。保留简短维护注释。Raw／Wiki 未建立时只注明约定位置，不创建空目录、空链接或扩大授权。
3. 项目侧只需文档位置、关键证据输出、验证结果和限制等交付提示；不解释后台调度，不机械新增 AGENTS.md。修改代码仓库规则须单独取得授权并合并已有内容。
4. 用 `scripts/project_binding.py` 登记明确确认的项目与来源。标识首次由用户可理解的简短名称确定，后续沿用；先 `list` 核对已有绑定。`register` 的来源列表是完整列表，变更已有绑定须先说明差异。脚本不创建或整理文档。
5. 默认维护入口待绑定、回流关闭。只有用户明确指定当前或某个 Codex 会话为维护入口时，才用可核实的会话 ID 执行 `bind`；环境变量只能提供身份，不能代替授权。身份不明时保留待绑定，不查找最近会话猜测。`unbind` 仅解除维护入口。
6. 核对文档入口与登记结果，说明待确认事项；项目接入不批准 Wiki 读取、同步、自动写入或任务派发。普通资料整理使用 Ingest，不要求先注册项目。

```bash
python3 scripts/project_binding.py list --vault-root /path/to/vault
python3 scripts/project_binding.py register --vault-root /path/to/vault --project-id project-a --project-path 工作项目/项目甲 --raw-path 工作资料/项目甲 --source /path/to/repo
python3 scripts/project_binding.py bind --vault-root /path/to/vault --project-id project-a --client codex --session-id <明确指定的会话ID>
python3 scripts/project_binding.py unbind --vault-root /path/to/vault --project-id project-a
```

多个仓库重复传入 `--source`；无代码来源的项目可不传。非 Git 来源传入明确目录。项目特有约定放总览，不复制根规则。证据记录区分用户决定、助手声明、实际验证与推断，不复制完整日志。
