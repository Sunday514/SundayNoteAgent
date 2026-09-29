# Sunday Note 安装器

本文描述当前已实现的安装方式。插件化尚未实现：QuickAdd 仍引用 vault 内的 `SundayNoteAgent/automation/quickadd/`，Monitor 仍由安装器配置。迁移完成前不要直接移动源码目录；后续将保留小型 vault 初始化/更新入口，插件负责工具分发，VPS 服务独立部署。

在 vault 根目录保留 `SundayNoteAgent/` 源码目录，首次安装选择模式：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --mode personal
# 独立工作知识库
bash SundayNoteAgent/install/install.sh --vault-root . --mode work
```

## 两种模式

| 内容 | personal（默认） | work |
| --- | --- | --- |
| Raw、日/周/月记录、项目复盘 | `工作/` 与 `个人/` 分区 | 仅 `工作/` |
| 长期图像 | `assets/工作/figures/`、`assets/个人/figures/` | 仅 `assets/工作/figures/` |
| Wiki | `30_知识库/`，按主题统一组织 | 同左 |
| Routine 模板 | `个人模板/`，含日记入口、周/月统计 | `工作模板/`，计划、记录、周/月总结 |
| 个性化 | context Skill、缺失时创建个人上下文 | 不安装 context Skill、不创建个人上下文 |
| 写作 | 创建 `40_个人写作/` | 不创建 |
| QuickAdd | 每日记录、日记、周/月统计 | 仅创建工作每日记录 |
| Daily Notes / Calendar | 个人日/周记录目录及模板 | 工作日/周记录目录及模板 |

两种模式都创建 `.import_files/`、首页和同步忽略规则，并部署 Ingest、Query、Lint。工作模式的原始材料位于 `10_原始材料/工作/`，项目位于 `23_项目复盘/工作/`。月记录由 Agent 或 Obsidian 模板创建。

模式保存在 vault 本地 `.sunday-note-agent/install-mode`，更新时可省略 `--mode`；没有记录的旧安装默认为个人模式。显式指定模式只改变本次托管配置和之后的安装范围，不删除或迁移已有笔记、模板、首页和额外 Skill。需要纯工作环境时使用独立 vault；已有 `AGENTS.md` 个性化响应段会阻止工作模式安装，以免覆盖或混入个人指令。

## 更新与选项

关闭 Obsidian，更新源码并重新安装，完成后再打开 Obsidian：

```bash
git -C SundayNoteAgent pull --ff-only
bash SundayNoteAgent/install/install.sh --vault-root .
```

| 选项 | 行为 |
| --- | --- |
| `--routine-templates managed`（默认） | 创建缺失的 Daily 模板，刷新 Weekly/Monthly 模板；合并已启用 Daily Notes、Calendar 的目录和模板字段 |
| `--routine-templates preserve` | 不创建或更新模板，保留 Daily Notes、Calendar 设置；QuickAdd 仍按安装模式更新 |
| `--with-paper-summarizer` | 首次安装论文总结 Skill；已安装后普通更新继续刷新 |
| `--with-monitor` | 安装或更新 Monitor，包括用户级 Hook/MCP 配置 |
| `--with-monitor --monitor-only` | 只更新 Monitor，不改变 vault 安装模式、模板和其他工具 |
| `--without-monitor --monitor-only` | 卸载 Monitor 托管入口，保留日志 |

先自行安装并启用需要的 Obsidian 插件。插件缺失时核心安装仍完成，并报告跳过的集成。保留模板时，QuickAdd 创建每日记录仍要求当前模式的固定路径下已有模板。论文总结使用 Agent 当前可用的 PDF 读取和页面渲染工具，不依赖 Docling 或指定 Conda 环境；缺少阅读能力时说明限制，不自动安装依赖。更新只清理已知废弃脚本和模板（包括 Query 计数脚本），保留旧论文工作区和用户额外文件。查询脚本清理目标或其容器是符号链接、目标是异常目录时，安装停止；先检查本地布局，不强制覆盖。

## 托管边界

- 根规则由公共 scaffold 与模式规则组合生成；个人模式保留唯一末尾 `## 个性化响应` 章节，重复标题时停止覆盖。
- Skills 从源码复制，额外文件保留；工作模式根规则限制只使用工作来源和 Wiki。
- 首页、Daily 模板、个人上下文和 `.gitignore` 只在缺失时创建；Weekly/Monthly 按所选模板模式更新。已有正文不迁移。
- QuickAdd 按稳定 ID/名称替换本项目入口，工作模式移除本项目的个人日记和统计入口；用户自有 choices 与其他配置保留。安装器不启用插件。
- `.stignore` 文件开头的托管段按模式刷新，用户规则保留在后；源码、凭据、日志、导入中间产物和设备状态留在本地。工作模式另外排除所有固定层的 `个人/` 分区、`assets/个人/`、`40_个人写作/`、`个人模板/` 和 `个人上下文.md`；个人模式允许工作与个人内容同步。托管排除优先于用户的包含规则，切换模式只替换托管段。
- 个人模式需要个性化时，可显式调用 `$sunday-note-context` 初始化；安装器不启动访谈。

个人周/月统计仅修改已有记录的自动块；打卡项读取 Daily 模板。周统计覆盖 ISO 周的 7 天，月统计汇总周日落在该月的完整周。工作模式不部署这些入口。

Syncthing 的 `.stignore` 是设备本地文件，各设备需按自身模式安装。排除规则不删除此前已同步的文件；若要求设备从未接收个人资料，应先安装工作模式再启用同步。语法及首条匹配规则见 [Syncthing 官方说明](https://docs.syncthing.net/users/ignoring.html)。

## 验证

在工具仓库运行 `bash tests/run.sh`。测试使用脱敏临时 vault，覆盖两种模式、重复安装、模板保留、插件配置和导出脚本，不读取真实 vault。

## Monitor（可选）

Monitor 是只读会话检查旁路，不参与正常 Query/Ingest，也不替代正式 Review。子报告方向和目标由宿主绑定，阅读索引不要求摘录或模型计算 hash；单条坏证据或单方向失败不阻塞其他有效发现，过滤后取消可能依赖它的决策。无最终汇总时不推送，同版本有限覆盖不因基线未知反复检查。

Vault 布局和项目入口由 Skill 的 `references/vault.md` 维护，不放入动态项目上下文；检查实际矛盾及关键变化尚未承接，普通实现细节不要求更新文档。主会话处理前核实当前证据及适用性。

安装与停用：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --with-monitor --monitor-only
bash SundayNoteAgent/install/install.sh --vault-root . --without-monitor --monitor-only
```

依赖 Linux、Python 3.11+、rg，以及支持临时会话、忽略用户配置和 permission profile 的 Codex。自动反馈还需要来源 App 状态接口、原生队列和 MCP Apps。只使用 ChatGPT 订阅认证，不复制凭据或回退 API。

安装器合并用户级 Hooks/MCP，部署本项目脚本和面板；其他配置保留。同名未托管 MCP 或冲突的 inline Hooks 会阻止安装。安装后在 Codex `/hooks` 中审阅并信任 Hooks，重新加载客户端 MCP，并验证一次无副作用反馈。卸载移除托管入口、保留日志。当前一个用户配置绑定一个 vault，各设备分别安装。

### 范围与运行状态

本地配置位于 `.logs/codex/config.json`：

- `project_roots`：监控项目，默认绑定 vault；空数组停止采集。同仓库 worktree 按 Git 公共目录识别，独立 clone 不自动放行。
- `reference_roots`：允许作为补充文件证据的目录，不启用监控，也不是操作系统读取隔离。不要填主目录或文件系统根目录。
- 配置使用明确绝对路径，更新保留已有选择；日志、建议、队列和状态均属私人数据，不提交或同步。

在 vault 根目录操作：

```bash
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json status
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json pause
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json resume
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json retry
```

`pause` 不强杀当前检查；`retry` 重试分析队列，不盲目重发投递结果不明的消息。更新会停止旧执行器并迁移待处理队列，保留日志与用户选择。

桌面 Hook 未继承终端代理时，通过安装器设置本地代理；重新安装保留，传空字符串清除：

```bash
python3 SundayNoteAgent/install/configure_monitor.py --vault-root /path/to/vault --proxy-url 'http://127.0.0.1:<端口>'
```

### 反馈与故障处理

- 只采集范围内、有持久 transcript 的会话。后台检查沿用来源 Codex 程序；无法识别来源、认证或沙箱失败时保留状态，不自动换模型或程序。
- 新用户请求使旧待发结果失效；只在来源会话明确空闲、轮次一致时投递。缺少状态通道仍可分析和记录，但不自动推送。
- 面板“处理”交接任务；“确认”只保存选择、可继续转为处理；“忽略”当前不执行。确认与忽略在后续正常请求中作为上下文交接，不构成自动执行授权。
- `queued` 仅说明队列接受，不代表面板已渲染；`sending` 或失败需检查状态，不重复投递。工具缺失时重新加载 MCP，不能靠复制完整私人证据到提示词兜底。
- Git 审查范围未知时报告不完整，不以新 HEAD 补证旧范围；切换仓库不清除原仓库的未知状态。Monitor 建议仍需用户判断。

日常先查看 `status` 的积压和阻塞原因，再检查本地日志。实际模型、委派与证据检查流程以 [Monitor Skill](../skills/sunday-note-monitor/SKILL.md) 和实现为准；此处不复制内部状态机。客户端工具发现、面板渲染和关闭行为需在实际安装后验证。
