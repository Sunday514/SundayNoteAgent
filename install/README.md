# 安装、更新与迁移

知识库能力使用 Codex 本地插件，Monitor 独立在本机安装。源码可以在任意 vault 外目录；运行依赖 Linux、Python 3.11+、Codex 的插件支持，Monitor 另需 Hooks、rg、原生队列和 MCP Apps。一个本机配置绑定一个 vault。

## 安装

关闭 Obsidian。新 vault 先初始化目录与模板；已有 vault 跳过初始化，迁移默认保留全部模板：

```bash
bash install/install.sh --vault-root /path/to/vault --mode personal --deployment plugin --install-state /path/to/local/install-mode
# 独立工作 vault 使用 --mode work
python3 install/migrate_plugin.py plan --vault-root /path/to/vault --source-target /path/to/SundayNoteAgent
python3 install/migrate_plugin.py apply --vault-root /path/to/vault --source-target /path/to/SundayNoteAgent --mode personal --with-paper-summarizer
```

已在最终源码目录时，`--source-target "$PWD"`。搬迁时目标目录必须不存在，迁移工具复制完整 Git 和本地文件，保留旧工作区至验收。不要手工删除旧源码再尝试启动新入口。

个人与工作共用一套插件。`--mode` 只决定 vault 布局、模板、同步范围和本机绑定，不参与插件构建或版本计算。

- `personal`：工作与个人 Raw、日/周/月记录、项目和 assets，统一 Wiki，个人模板、写作与可选个人上下文。
- `work`：仅工作分区及 Wiki、工作日/周/月记录与模板，不创建个人内容。严格隔离使用独立工作 vault。
- Paper 首次按参数选装，更新保留。远程可加 `--remote-app-id`，仅接受已核实的注册 App ID，不填写网址或凭据；不提供时不影响已有远程连接或本地功能。Monitor 的 Skill、代码、Hooks 和 MCP 均不进入插件。

插件通过官方 `codex plugin marketplace add` 和 `codex plugin add` 安装，使用目标 App 自带的运行时，不用 PATH 中另一份 CLI 推断 App 能力。首次安装后重载客户端并验证 Skills 发现。代码有独立内容版本，不直接写插件缓存。

插件不包含生命周期 Hooks，不要求 `SessionStart`。在 vault 项目中读取其根规则；跨项目使用时显式指定 vault，或按需读取本机绑定中的 `vault` 字段并确认目标。不要默认将当前代码仓库当作知识库。

## 文件职责

| 位置 | 内容 |
| --- | --- |
| 源码仓库 | Skills、脚本、安装源文件，唯一开发真值 |
| vault | 笔记、模板、根 AGENTS、`SundayNoteTools/quickadd/` |
| `$XDG_CONFIG_HOME/sunday-note-agent/` | vault/模式绑定 |
| `$XDG_STATE_HOME/sunday-note-agent/` | 插件迁移检查点与备份 |
| `$XDG_DATA_HOME/sunday-note-agent/marketplace/` | 生成的插件发布源，由安装器刷新 |

未设置 XDG 时分别使用 `~/.config`、`~/.local/state`、`~/.local/share`。这些目录和 Codex 凭据都不进入 vault 或仓库。

QuickAdd 按现有 ID 更新本项目入口，保留用户 choices；个人模式提供每日记录、日记与周/月统计，工作模式仅提供工作每日记录。脚本位于可见托管目录，移动或停用 Agent 插件不影响它。缺少 Obsidian 插件时报告跳过，不自动启用。

根规则由 scaffold 与模式段生成，保留个人模式末尾唯一的“个性化响应”段。首页、Daily 模板和个人上下文仅在缺失时创建；迁移不覆盖已有 Routine 模板。需要刷新托管 Weekly/Monthly 时显式运行基础安装器，使用相同外置 `--install-state`。

`.stignore` 排除源码、根规则、Skills、缓存、日志和本机配置。QuickAdd 小型脚本与其配置可以同步；各设备分别安装规则和插件。工作模式额外排除个人分区、个人 assets、写作、模板和个人上下文。忽略不删除已同步的文件，开启工作设备同步前先配置范围。

## 更新、停用与回退

在最终源码目录执行：

```bash
git pull --ff-only
python3 install/migrate_plugin.py apply --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py status --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py disable --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py rollback --vault-root /path/to/vault --source-target "$PWD"
```

`disable` 卸载本机插件注册，保留知识、QuickAdd 和发布源。插件安装、更新与停用均不修改独立 Monitor 的注册、配置、暂停状态或队列。

每次 `apply` 保存精确配置备份和分步 hash 检查点。中断后先查看状态并回退；不会覆盖旧检查点后直接重试。`rollback` 恢复最近一次迁移接管的配置；不属于中断步骤的新修改会阻止回退。中断步骤涉及的文件若内容未确认，先完整保存在备份目录的 `recovery/` 中再恢复原配置，需人工核查这些留存内容（包括可能的用户编辑）。正常迁移完成后的用户修改仍阻止自动回退。源码副本和备份保留，独立 Monitor 不变。

真实验收后才考虑归档旧源码，并先确认本地 Monitor 的 query 路径不再依赖它。不要随插件清理 `.sunday-note-agent/monitor`、`.logs/codex` 或 Monitor Skill；不要删除 VPS 仍读取的远程规则副本，也不要整目录删除 vault 的 `.agents` 或 `.logs`。

## Monitor 与远程边界

Monitor 是仅本地使用的可选只读旁路，不代替正式 Review，不自动执行建议。使用现有本地安装器单独管理：

```bash
bash install/install.sh --vault-root /path/to/vault --with-monitor --monitor-only
bash install/install.sh --vault-root /path/to/vault --without-monitor --monitor-only
# 已安装实例：status / pause / resume
python3 /path/to/vault/.sunday-note-agent/monitor/monitor.py --config /path/to/vault/.logs/codex/config.json status
```

本地部署沿用 `.sunday-note-agent/monitor`、`.agents/skills/sunday-note-monitor` 和 `.logs/codex`，不参与同步。配置中的 `project_roots` 控制监控项目，`reference_roots` 仅控制补充证据；代理保存在本地，不复制认证。独立安装器会启用 Monitor，需要暂停时显式执行 `pause`。队列接受不代表面板已渲染；投递结果不明时不要重复发送。新客户端必须验证工具发现、用户级 Hook 信任和面板。

远程 MCP 源码与契约统一维护在 [remote/](../remote/README.md)，通过 rootless 容器执行受限工作区任务，VPS 独立部署。本机插件只通过 `--remote-app-id` 连接已有 App，不部署服务、扩大挂载或自动发布云端 Skills。服务端升级和客户端工具刷新需单独验收；旧文档接口的正文过滤授权不能直接转换成沙箱目录挂载。

基础安装器 `--deployment standalone` 仍可用于不使用插件的设备和兼容规则导出；不要在本机插件旁再导出同名知识库 Skills。`configure_monitor.py` 负责独立本地 Monitor，不经插件入口。

## 验证

`bash tests/run.sh` 使用脱敏临时目录覆盖现有功能、插件构建、迁移、重复更新、停用与回退。KDI 的 `prepare/all --plugin-root <实际插件缓存目录>` 将实际部署与源码 hash 对照，并冻结对应 Skills；不填时检查 vault 内部署。

机械验证不代表客户端已发现 Skills、Obsidian GUI 已完成验收，也不证明知识库整体有效。
## Monitor（可选）

Monitor 是只读会话检查旁路，不参与正常 Query/Ingest，也不替代正式 Review。子报告方向和目标由宿主绑定，阅读索引不要求摘录或模型计算 hash；单条坏证据或单方向失败不阻塞其他有效发现，过滤后取消可能依赖它的决策。无最终汇总时不推送，同版本有限覆盖不因基线未知反复检查。

Vault 布局和项目入口由 Skill 的 `references/vault.md` 维护，不放入动态项目上下文；检查实际矛盾及关键变化尚未承接，普通实现细节不要求更新文档。主会话处理前核实当前证据及适用性。

在独立源码目录安装与停用：

```bash
bash install/install.sh --vault-root /path/to/vault --with-monitor --monitor-only
bash install/install.sh --vault-root /path/to/vault --without-monitor --monitor-only
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
python3 install/configure_monitor.py --vault-root /path/to/vault --proxy-url 'http://127.0.0.1:<端口>'
```

### 反馈与故障处理

- 只采集范围内、有持久 transcript 的会话。后台检查沿用来源 Codex 程序；无法识别来源、认证或沙箱失败时保留状态，不自动换模型或程序。
- 新用户请求使旧待发结果失效；只在来源会话明确空闲、轮次一致时投递。缺少状态通道仍可分析和记录，但不自动推送。
- 面板“处理”交接任务；“确认”只保存选择、可继续转为处理；“忽略”当前不执行。确认与忽略在后续正常请求中作为上下文交接，不构成自动执行授权。
- `queued` 仅说明队列接受，不代表面板已渲染；`sending` 或失败需检查状态，不重复投递。工具缺失时重新加载 MCP，不能靠复制完整私人证据到提示词兜底。
- Git 审查范围未知时报告不完整，不以新 HEAD 补证旧范围；切换仓库不清除原仓库的未知状态。Monitor 建议仍需用户判断。

日常先查看 `status` 的积压和阻塞原因，再检查本地日志。实际模型、委派与证据检查流程以 [Monitor Skill](../skills/sunday-note-monitor/SKILL.md) 和实现为准；此处不复制内部状态机。客户端工具发现、面板渲染和关闭行为需在实际安装后验证。
