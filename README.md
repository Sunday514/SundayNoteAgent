# SundayNoteAgent

利用用户已有知识与个人上下文改善回答，并将有长期价值的新信息沉淀为可复用知识。Markdown vault 保存内容，Skills 定义工作流，脚本执行确定性操作。

## 功能

| 功能 | 入口与边界 |
| --- | --- |
| 使用知识 | Query 只读检索并回答；个人上下文按需参与路由与取舍，不改变来源事实 |
| 积累知识 | Ingest 分别判断知识增量和来源维护，无变化不写；论文总结生成单篇 Raw 与必要原图 |
| 显式维护 | Lint 全面深入审读 Wiki 与相关证据，先写整理计划，再执行和复核 |
| 项目回流 | 安装插件一并配置 Hook；接入项目时绑定维护会话并开启 Raw 回流。到期 Wiki 维护须独立授权具体页面 |
| 项目规则 | Project 镜像根 AGENTS 与 `.agents/` 下的 Markdown，供阅读与版本对照；不回写、不自动执行 |
| 日常记录 | Routine 使用“计划—记录”；QuickAdd 创建每日记录/日记，更新已有周/月打卡统计 |
| 开发诊断 | KDI 使用隔离工作区和匿名评审比较知识收益；不是日常使用前置条件 |

工作流以 [Skills](skills/) 和安装后的根规则为准。Wiki 使用普通 Markdown，来源放在相关正文附近；不要求固定 header 或计数维护，已有元数据保留。

Query、Ingest、Lint、Paper 支持本地与远程，由环境提供搜索、读写和 PDF／图片工具；Paper 按需选择安装。Project、Monitor 仅本地。云端只安装四项通用 Skill，不搬运本机绑定、Hook 或回流工作流；MCP 使用与验收见 [远程说明](remote/README.md)。

正式项目入口在绑定 Vault 的 `SundayNoteAgent项目/项目维护/项目总览.md`，架构、计划、记录位于同目录；源码不保留副本。知识整理显式触发，已授权项目可单独启用 Raw 回流。

## 安装与更新

源码可位于 vault 外。Codex 插件分发知识库 Skills。vault 保留知识、规则、模板和 QuickAdd 托管脚本。首次安装或旧部署迁移：

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

完整选项、同步范围、停用与回退见[安装说明](install/README.md)。插件迁移与 Vault 布局迁移是两个独立入口；旧编号布局必须先演练并迁移，普通安装不会建立第二套目录。客户端重载并验证 Skills 发现前，不清理旧源码。

## 内容与源码

Raw 承载可迁移、可独立理解的事实，Wiki 提炼方法，原始证据留在来源系统按需兜底。Raw 随经历积累，Wiki 随理解收敛，以修订、合并和淘汰为主；三层不是强制生成文档的流水线。Routine 保留时间记录用途；Journal 仅在明确要求时读写。

资料层的 `<项目>/` 保存项目任务 Raw，外部论文等保留资料类型集合；`知识库/<项目或主题>/` 集中保存 Wiki。项目目录保存面向人的文档，Wiki 主要面向 Agent，受众不构成读取限制；路径授权独立约束读写与同步。

每个项目的 `项目维护/` 固定包含总览、架构、计划、记录四份文档。总览只作简短定位和入口，记录链接详细 Raw、不复制过程。维护约定由安装后的根规则定义；安装器不创建或重命名项目正文。

项目创建与接入使用 [Project Skill](skills/sunday-note-project/SKILL.md) 和其[项目维护模板](skills/sunday-note-project/assets/项目维护/)，不覆盖已有正文。接入包含登记、绑定维护会话和开启 Raw 回流；条件未齐时仅登记为待接入，重登记保留暂停选择。Ingest 负责资料和知识整理；Wiki 页面与同步另行授权，见[安装说明](install/README.md#项目接入)。

工作任务不补读个人来源，按任务范围和环境授权检索 Wiki，范围不明先确认，不以文件可见当作获准。个人记录可按需检索，`个人写作/` 须明确授权才能读写；链接不授予权限。设备同步按主题集合单独批准，不由读取授权推导。

`.import_files/` 是不参与同步的导入缓存，不作为长期来源链接。论文只引用稳定在线来源或 vault 内长期文件。Syncthing 同步知识和可迁移设置，工具及设备状态由各端部署。

源码职责：

- `skills/`：语义工作流与其必要脚本；`automation/`：QuickAdd。
- `remote/`：目录受限的远程开发沙箱 MCP；服务在 VPS 独立部署，不进入插件包。
- `install/`：安装、插件构建、迁移与 scaffold；`templates/`：无个人条目的 Routine 结构。
- `migration/`：外部资料导入辅助工具；`tests/` 与 `validation/`：脱敏回归和开发诊断。

个人正文、模板条目、附件、设备路径、凭据和运行日志不提交本仓库。开发规则见 [AGENTS.md](AGENTS.md)。

## 验证

```bash
npm --prefix remote ci
bash tests/run.sh
```

回归使用 Bash、Node、Python 和临时脱敏 fixture，不读取真实 vault。机械检查通过不代表模型判断或知识库整体有效。

KDI 的 `validation/knowledge_delta.py` 提供 `prepare/run/judge/report/all/cleanup`；参数见各子命令 `--help`，suite 示例见 `tests/fixtures/knowledge_delta/smoke-suite.json`。真实运行会调用模型及 Web，必须先冻结输入并检查隔离与被测版本；`--dry-run` 不调用模型。产物只放 `/tmp`，不将私人诊断材料提交仓库。

## 部署边界

插件由现有源文件生成，安装后通过官方 marketplace 更新，不编辑缓存。代码、绑定和运行状态各自独立；插件停用不删除笔记或 QuickAdd。KDI 使用 `--plugin-root` 校验实际插件版本并冻结其 Skills，不把源码当成已安装版本。

源码统一维护在 SundayNoteAgent，VPS 服务独立部署。本地插件提供 Skills，并可通过已核实的 App ID 连接远程 MCP；这不等于云端已安装同一套 Skills。远程执行只开放部署端批准的容器工作区，不暴露宿主 Shell。服务部署、权限与接口契约见 [remote/README.md](remote/README.md)，地址、认证、权限配置和运行状态由部署端管理。备份、同步与 iOS 阅读不属于插件业务。
