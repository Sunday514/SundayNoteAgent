# SundayNoteAgent

利用用户已有知识与个人上下文改善回答，并将有长期价值的新信息沉淀为可复用知识。Markdown vault 保存内容，Skills 定义工作流，脚本执行确定性操作。

## 功能

| 功能 | 入口与边界 |
| --- | --- |
| 使用知识 | Query 只读检索并回答；个人上下文按需参与路由与取舍，不改变来源事实 |
| 积累知识 | Ingest 分别判断知识增量和来源维护，无变化不写；论文总结生成单篇 Raw 与必要原图 |
| 显式维护 | Lint 全库检查与维护，写入由子 Agent 执行、主 Agent 复核；Context 经确认校准个人上下文 |
| 日常记录 | Routine 使用“计划—记录”；QuickAdd 创建每日记录/日记，更新已有周/月打卡统计 |
| 可选旁路 | Monitor 只读检查开发会话、展示建议，不替代正式审查，不自动执行建议 |
| 开发诊断 | KDI 使用隔离工作区和匿名评审比较知识收益；不是日常使用前置条件 |

工作流以 [Skills](skills/) 和安装后的根规则为准。查询不维护计数；旧页面的 `last_queried`、`query_count` 保留为历史字段，不再生成、更新或校验。

## 安装与更新

当前要求仓库位于 vault 的 `SundayNoteAgent/`；QuickAdd 依赖此位置。在 vault 根目录执行：

```bash
git clone git@github.com:Sunday514/SundayNoteAgent.git SundayNoteAgent
bash SundayNoteAgent/install/install.sh --vault-root . --mode personal
# 独立工作 vault 改用 --mode work
```

- `personal`：工作与个人分区、个人 Routine、写作和个人上下文。
- `work`：工作 Raw、日/周/月记录、项目、附件及统一 Wiki，不创建个人内容。
- 后续更新模式自动保留；已有自定义模板可用 `--routine-templates preserve`。
- 论文和 Monitor 分别通过 `--with-paper-summarizer`、`--with-monitor` 启用。

关闭 Obsidian 后更新，完成后重新打开：

```bash
git -C SundayNoteAgent pull --ff-only
bash SundayNoteAgent/install/install.sh --vault-root .
```

完整选项、同步范围、升级保留行为和 Monitor 操作见[安装说明](install/README.md)。安装器不迁移知识正文，也不执行 Git 操作。

## 内容与源码

Raw 保存外部来源总结，Routine 保存活动与项目证据，Wiki 提炼稳定知识；Journal 仅在明确要求时读写。未链接的 Raw 是检查线索，不要求每份材料都产生 Wiki 修改。

Raw、日/周/月记录、项目与长期图片按工作/个人分区，Wiki 统一；工作任务不补读个人来源。目录过滤不是完整隐私隔离，严格工作隔离应使用明确的共享集合或独立 vault。

`.import_files/` 是不参与同步的导入缓存，不作为长期来源链接。论文只引用稳定在线来源或 vault 内长期文件。Syncthing 同步知识和可迁移设置，工具及设备状态由各端部署。

源码职责：

- `skills/`：语义工作流与其必要脚本；`automation/`：QuickAdd 和 Monitor。
- `install/`：安装、scaffold 与集成；`templates/`：无个人条目的 Routine 结构。
- `migration/`：外部资料导入辅助工具；`tests/` 与 `validation/`：脱敏回归和开发诊断。

个人正文、模板条目、附件、设备路径、凭据和运行日志不提交本仓库。开发规则见 [AGENTS.md](AGENTS.md)。

Monitor 提供待核实线索，实际处理前由主会话确认当前证据；单项交接失败不阻塞其他有效发现。Vault 文档入口由 Skill 参考文档维护。

## 验证

```bash
bash tests/run.sh
```

回归使用 Bash、Node、Python 和临时脱敏 fixture，不读取真实 vault。机械检查通过不代表模型判断或知识库整体有效。

KDI 的 `validation/knowledge_delta.py` 提供 `prepare/run/judge/report/all/cleanup`；参数见各子命令 `--help`，suite 示例见 `tests/fixtures/knowledge_delta/smoke-suite.json`。真实运行会调用模型及 Web，必须先冻结输入并检查隔离与被测版本；`--dry-run` 不调用模型。产物只放 `/tmp`，不将私人诊断材料提交仓库。

## 插件化方向（待实现）

先收敛核心行为，再解除仓库位置依赖、封装插件、验证迁移与回退。插件分发 Skills、可选 Monitor 和远程文档 MCP 连接；VPS 服务独立运行，vault 继续保存知识及必要的 Obsidian 资源。

当前不能直接移动源码目录。插件化不扩大文件权限，不把备份、同步或 iOS 阅读服务纳入核心工作流，也不重写 Monitor/KDI。
