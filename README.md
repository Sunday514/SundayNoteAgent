# SundayNoteAgent

利用用户已有知识与个人上下文改善回答，并将有长期价值的新信息沉淀为可复用知识。Markdown vault 保存内容，Skills 定义工作流，脚本执行确定性操作。

## 功能

| 功能 | 入口与边界 |
| --- | --- |
| 使用知识 | Query 只读检索并回答；个人上下文按需参与路由与取舍，不改变来源事实 |
| 积累知识 | Ingest 分别判断知识增量和来源维护，无变化不写；论文总结生成单篇 Raw 与必要原图 |
| 显式维护 | Lint 全面深入审读 Wiki 与相关证据，先写整理计划，再执行和复核 |
| 日常记录 | Routine 使用“计划—记录”；QuickAdd 创建每日记录/日记，更新已有周/月打卡统计 |
| 可选旁路 | Monitor 只读检查开发会话、展示建议，不替代正式审查，不自动执行建议 |
| 开发诊断 | KDI 使用隔离工作区和匿名评审比较知识收益；不是日常使用前置条件 |

工作流以 [Skills](skills/) 和安装后的根规则为准。查询不维护计数；旧页面的 `last_queried`、`query_count` 保留为历史字段，不再生成、更新或校验。

## 安装与更新

源码可位于 vault 外。Codex 插件分发知识库 Skills；Monitor 仅支持独立本地安装，不随插件打包。vault 保留知识、规则、模板和 QuickAdd 托管脚本。首次安装或旧部署迁移：

```bash
python3 install/migrate_plugin.py plan --vault-root /path/to/vault --source-target /path/to/SundayNoteAgent
python3 install/migrate_plugin.py apply --vault-root /path/to/vault --source-target /path/to/SundayNoteAgent
# 新工作 vault 加 --mode work；论文能力选装 --with-paper-summarizer
```

- `personal`：工作与个人分区、个人 Routine、写作和个人上下文。
- `work`：工作 Raw、日/周/月记录、项目、附件及统一 Wiki，不创建个人内容。
- 后续更新保留模式、选装能力和自定义模板；首次模板初始化见安装说明。

个人与工作共用统一插件，差异仅在 vault 安装配置与本机绑定，不维护两套插件包。

关闭 Obsidian 后更新，完成后重新打开：

```bash
git pull --ff-only
python3 install/migrate_plugin.py apply --vault-root /path/to/vault --source-target "$PWD"
```

完整选项、同步范围、停用与回退见[安装说明](install/README.md)。迁移保留 Git 和本地文件，不迁移知识正文。客户端重载与 Hook 信任通过前，不清理旧源码。

## 内容与源码

Raw 保存外部来源总结，Routine 保存活动与项目证据，Wiki 提炼稳定知识；Journal 仅在明确要求时读写。未链接的 Raw 是检查线索，不要求每份材料都产生 Wiki 修改。

Raw、日/周/月记录、项目与长期图片按工作/个人分区，Wiki 统一；工作任务不补读个人来源。目录过滤不是完整隐私隔离，严格工作隔离应使用明确的共享集合或独立 vault。

`.import_files/` 是不参与同步的导入缓存，不作为长期来源链接。论文只引用稳定在线来源或 vault 内长期文件。Syncthing 同步知识和可迁移设置，工具及设备状态由各端部署。

源码职责：

- `skills/`：语义工作流与其必要脚本；`automation/`：QuickAdd 和 Monitor。
- `install/`：安装、插件构建、迁移与 scaffold；`plugin/`：薄运行入口；`templates/`：无个人条目的 Routine 结构。
- `migration/`：外部资料导入辅助工具；`tests/` 与 `validation/`：脱敏回归和开发诊断。

个人正文、模板条目、附件、设备路径、凭据和运行日志不提交本仓库。开发规则见 [AGENTS.md](AGENTS.md)。

## 验证

```bash
bash tests/run.sh
```

回归使用 Bash、Node、Python 和临时脱敏 fixture，不读取真实 vault。机械检查通过不代表模型判断或知识库整体有效。

KDI 的 `validation/knowledge_delta.py` 提供 `prepare/run/judge/report/all/cleanup`；参数见各子命令 `--help`，suite 示例见 `tests/fixtures/knowledge_delta/smoke-suite.json`。真实运行会调用模型及 Web，必须先冻结输入并检查隔离与被测版本；`--dry-run` 不调用模型。产物只放 `/tmp`，不将私人诊断材料提交仓库。

## 部署边界

插件由现有源文件生成，安装后通过官方 marketplace 更新，不编辑缓存。代码、绑定和运行状态各自独立；插件停用不删除笔记或 QuickAdd。KDI 使用 `--plugin-root` 校验实际插件版本并冻结其 Skills，不把源码当成已安装版本。

VPS 服务独立运行；远程连接可通过已核实的 App ID 打包，未提供时保留已有连接，不猜测 ID 或复制认证。远程仍依赖的规则副本单独维护，不因本机迁移删除。备份、同步与 iOS 阅读不属于插件业务。
