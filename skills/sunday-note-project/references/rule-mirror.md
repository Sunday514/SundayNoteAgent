# 规则镜像

只提取项目根 `AGENTS.md`（存在 override 时以其为准）及 `.agents/` 下的 `.md` 文档。沿本项目工具入口读取仓库内链接，不跟随到仓库外，不扫描其他文档或 Skill 目录。

核对已绑定项目与来源，先预览，再同步：

```bash
python3 scripts/mirror_rules.py plan --vault-root /path/to/vault --project-id project-a --source /path/to/repo --repository owner/project
python3 scripts/mirror_rules.py sync --vault-root /path/to/vault --project-id project-a --source /path/to/repo --repository owner/project
python3 scripts/mirror_rules.py verify --mirror-root /path/to/vault/工作项目/项目甲/项目维护/Agent规则/owner/project
```

副本保留目录与原文，`AGENTS.md`、`AGENTS.override.md`、`SKILL.md` 改名为对应的 `.mirror.md`，不成为 Vault 执行入口。清单记录来源路径、commit 和内容身份，区分未提交修改；离线可核对副本，不保证源码仍为该版本。

仓库是真值，只单向更新；删除仅作用于清单托管文件。人工修改或删除的副本有冲突时停止，不覆盖。源码不可用时保留副本。项目总览链接规则入口和清单；不新增后台任务、不改变同步授权。
