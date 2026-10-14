# 安装、更新与迁移

知识库能力使用 Codex 本地插件。源码可以在任意 vault 外目录；运行依赖 Linux、Python 3.11+、Codex 的插件支持。一个本机配置绑定一个 vault。

`--routine-templates preserve` 保留用户模板及模板引用；Daily Notes 和 Calendar 的记录目录仍按工作／个人模式更新为固定布局。

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

- `personal`：工作与个人资料、记录、项目及通用知识库；个人上下文位于 `个人记录/个人上下文.md`，独立写作为 `个人写作/`，未经明确授权不得读取。
- `work`：工作资料、工作记录（日/周/月，按需年度与专项）、工作项目、知识库及工作模板，不初始化个人内容。
- Paper 首次按参数选装，更新保留。远程可加 `--remote-app-id`，仅接受已核实的注册 App ID，不填写网址或凭据；不提供时不影响已有远程连接或本地功能。

插件通过官方 `codex plugin marketplace add` 和 `codex plugin add` 安装，使用目标 App 自带的运行时，不用 PATH 中另一份 CLI 推断 App 能力。首次安装后重载客户端并验证 Skills 发现。代码有独立内容版本，不直接写插件缓存。

插件不包含生命周期 Hooks，不要求 `SessionStart`。在 vault 项目中读取其根规则；跨项目使用时显式指定 vault，或按需读取本机绑定中的 `vault` 字段并确认目标。不要默认将当前代码仓库当作知识库。

## 项目接入（仅本地）

在选定的项目会话调用 `sunday-note-project`，说明 Vault、项目归属和关联仓库。Skill 建立或核对四文档；普通 Ingest 不要求注册。只有明确指定会话时才绑定维护入口，不依赖当前会话环境自动授权。

绑定保存在 `$XDG_CONFIG_HOME/sunday-note-agent/project-bindings.json`（默认 `~/.config/`），以规范化 Vault 绝对路径为键，下面按项目标识保存项目路径、Raw 路径、来源、维护入口和关闭的回流开关。该文件独立于插件事务，重装、卸载和回退不删除绑定；迁机后须核对来源、路径和会话，不能仅凭旧记录运行。

在源码根目录可以执行以下命令；插件使用自身 Project Skill 内同名脚本：

```bash
python3 skills/sunday-note-project/scripts/project_binding.py list --vault-root /path/to/vault
python3 skills/sunday-note-project/scripts/project_binding.py register --vault-root /path/to/vault --project-id project-a --project-path 工作项目/项目甲 --raw-path 工作资料/项目甲 --source /path/to/repo
python3 skills/sunday-note-project/scripts/project_binding.py bind --vault-root /path/to/vault --project-id project-a --client codex --session-id USER_SELECTED_SESSION
python3 skills/sunday-note-project/scripts/project_binding.py unbind --vault-root /path/to/vault --project-id project-a
```

`register` 的 `--source` 可重复，表示完整来源列表；重登记保留维护入口并关闭自动回流。未知来源先确认，冲突拒绝，不自动搬迁绑定。`bind` 保存用户选定的入口，`unbind` 恢复待绑定；这不发送消息。Raw 目录可尚未建立，脚本不创建它。绑定不授予读取、同步或自动写入权限；旧记录目标失效时先修正，不自动猜测新位置。

已接入项目可通过 Project 的[规则镜像](../skills/sunday-note-project/references/rule-mirror.md)提取根 AGENTS 与 `.agents/` 下的 Markdown，保存到 `项目维护/Agent规则/`。脚本随 Project 分发，不需要另配文件列表；显式运行才更新，保留版本清单、不覆盖人工修改、不调整设备同步范围。

## 文件职责

| 位置 | 内容 |
| --- | --- |
| 源码仓库 | Skills、脚本、安装源文件，唯一开发真值 |
| vault | 笔记、模板、根 AGENTS、`SundayNoteTools/quickadd/` |
| `$XDG_CONFIG_HOME/sunday-note-agent/` | vault/模式与项目绑定、独立的设备同步授权 |
| `$XDG_STATE_HOME/sunday-note-agent/` | 插件迁移检查点与备份 |
| `$XDG_DATA_HOME/sunday-note-agent/marketplace/` | 生成的插件发布源，由安装器刷新 |

未设置 XDG 时分别使用 `~/.config`、`~/.local/state`、`~/.local/share`。这些目录和 Codex 凭据都不进入 vault 或仓库。

QuickAdd 按现有 ID 更新本项目入口，保留用户 choices；个人模式提供每日记录、日记与周/月统计，工作模式仅提供工作每日记录。脚本位于可见托管目录，移动或停用 Agent 插件不影响它。缺少 Obsidian 插件时报告跳过，不自动启用。

根规则由 scaffold 与模式段生成，保留个人模式末尾唯一的“个性化响应”段。首页、Daily 模板和个人上下文仅在缺失时创建；迁移不覆盖已有 Routine 模板。需要刷新托管 Weekly/Monthly 时显式运行基础安装器，使用相同外置 `--install-state`。

Query、Ingest、Lint、Paper 通过环境工具在任务授权范围内工作，不依赖本机搜索脚本或授权配置。范围不明先确认；文件可见、链接与同步许可不等于读取授权。安装重建配置时不再保留已退出使用的字段。

`.stignore` 由独立设备批准列表生成，读取许可不自动授予同步。配置位于 `$XDG_CONFIG_HOME/sunday-note-agent/sync-allowlist.json`，JSON 以 Vault 绝对路径为键、获批相对集合路径数组为值。项目、资料、Wiki 均批准到具体集合；更新不扩大范围。安全排除优先，最终 `*` 默认拒绝，已有用户规则保留在其后但不能绕过默认拒绝。

```bash
python3 install/sync_allowlist.py list --vault-root /path/to/vault
python3 install/sync_allowlist.py approve --vault-root /path/to/vault 工作项目/项目甲 工作资料/论文总结 工作记录 知识库/基础知识/Agent工程
python3 install/sync_allowlist.py revoke --vault-root /path/to/vault 工作项目/项目甲
python3 install/sync_allowlist.py render --vault-root /path/to/vault
```

批准包含集合内新文件，但不包含新项目或新资料集合。`.obsidian` 只允许逐个批准 JSON 配置文件，不批准整个目录；凭据、插件数据和设备状态不默认同步，安全排除仍优先。空白名单会明确警告，恢复同步前须完成授权。引用不批准附件同步：设备未同步附件所属集合时，页面图片可能不可用。各设备独立批准与部署，不同步本机授权文件。

## Vault 布局迁移

先暂停同步、关闭 Obsidian，审查 Git 基线并备份。普通安装检测到旧编号布局会拒绝；仍使用旧布局的个人实例固定旧插件版本，不直接更新。

映射 JSON 保存旧相对路径到新相对路径，使用最长前缀匹配。旧个人 Vault 须显式包含 `个人上下文.md` → `个人记录/个人上下文.md`。通用工具不猜测项目归属，不合并同名文件、不改写知识内容；`个人写作/` 不扫描正文，但核对外部链接目标路径并随映射改写。`.import_files/` 不移动，既有引用参与断链检查。

```bash
# mapping.json 和 transaction 均在 Vault 外的本机状态目录
python3 install/migrate_layout.py dry-run --vault-root /path/to/copy --mapping /local/mapping.json --state /local/transaction
python3 install/migrate_layout.py apply --vault-root /path/to/copy --state /local/transaction
python3 install/migrate_layout.py verify --vault-root /path/to/copy --state /local/transaction
python3 install/migrate_layout.py rollback --vault-root /path/to/copy --state /local/transaction
```

`dry-run` 生成内容身份清单、冲突、既有断链及 `links.diff`，不修改 Vault。先在真实副本完整演练并确认清单，再为真实 Vault 重新生成计划。`apply` 核对源文件与改写快照，完整备份后迁移；不清理旧空目录。安装前按清单清理已验收的旧空目录与占位文件，不删除非空目录。

`verify` 比较全部受检文件身份与新增断链。断链不阻止回退；未完成迁移、文件内容漂移或出现额外文件时拒绝自动回退，使用备份与 completed 清单人工恢复，不覆盖未知修改。完整回退后再按对应插件事务恢复安装配置与旧 `.stignore`，全程保持同步暂停。

本机验收不等于多设备切换完成。恢复同步前逐设备备份独有改动、部署新白名单、明确旧副本清理或迁移方式，再在第二台设备核对；旧版设备未切换前保持该文件夹暂停。纯链接修复不更新 Wiki 日期和计数。

## 更新、停用与回退

在最终源码目录执行：

```bash
git pull --ff-only
python3 install/migrate_plugin.py apply --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py status --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py disable --vault-root /path/to/vault --source-target "$PWD"
python3 install/migrate_plugin.py rollback --vault-root /path/to/vault --source-target "$PWD"
```

`disable` 卸载本机插件注册，保留知识、QuickAdd 和发布源。插件安装、更新与停用不接管其他工具的配置或运行状态。

每次 `apply` 保存精确配置备份和分步 hash 检查点。中断后先查看状态并回退；不会覆盖旧检查点后直接重试。`rollback` 恢复最近一次迁移接管的配置；不属于中断步骤的新修改会阻止回退。中断步骤涉及的文件若内容未确认，先完整保存在备份目录的 `recovery/` 中再恢复原配置，需人工核查这些留存内容（包括可能的用户编辑）。正常迁移完成后的用户修改仍阻止自动回退。源码副本和备份保留。

真实验收后才考虑归档旧源码，先核对未提交改动和其他工具的路径依赖。不要整目录删除 vault 的 `.agents` 或 `.logs`。

## 远程边界

远程 MCP 源码与契约统一维护在 [remote/](../remote/README.md)，通过 rootless 容器执行受限工作区任务，VPS 独立部署。本机插件只通过 `--remote-app-id` 连接已有 App，不部署服务、扩大挂载或自动发布云端 Skills。服务端升级和客户端工具刷新需单独验收；旧文档接口的正文过滤授权不能直接转换成沙箱目录挂载。

基础安装器 `--deployment standalone` 仍可用于不使用插件的设备和兼容规则导出；不要在本机插件旁再导出同名知识库 Skills。

## 项目证据回流（可选）

安装会分发 Monitor Skill，但不自动安装 Hook 或启用项目。先在绑定维护会话核实 `gpt-6.1-sol / medium`、本项目 Raw 和 Vault 内回流状态目录的普通沙箱写权限，以及 App 原生队列在忙碌会话中延后执行。项目 source 变更不保证已有会话权限同步变化。能力验证未通过时不要启用。

以下 `MONITOR` 指本次安装中 `skills/sunday-note-monitor/scripts/monitor.py` 的实际路径。它与 Project Skill 一同分发，不使用旧 Monitor 的 MCP、日志目录或执行器。

```bash
python3 "$MONITOR" install --vault-root /path/to/vault
python3 "$MONITOR" enable --vault-root /path/to/vault --project-id example --codex /verified/app/codex
python3 "$MONITOR" status --vault-root /path/to/vault --project-id example
python3 "$MONITOR" disable --vault-root /path/to/vault --project-id example
python3 "$MONITOR" uninstall --vault-root /path/to/vault
```

`install` 合并本工具的两个用户级 Hook 并备份原文件；更新分发路径后重新执行，替换原注册。按客户端流程审阅并信任，不绕过信任检查。停用插件前先停用项目并卸载 Hook；状态和历史保留，不恢复同步。

停用停止新采集与投递，已领取批次仍可登记完成；未领取批次保留。卸载前等正在整理的批次完成。部署前人工核对旧 Monitor Hook／MCP，不自动删除其他版本配置。未加载的维护会话可能要打开后才执行排队消息，不承诺关闭状态下后台运行。

投递使用明确验收过的 App Codex 路径，不使用 PATH 中的系统 CLI。App 升级或连接行为改变后须复验，失败时不启动替代会话。脚本不调用模型，维护会话是唯一整理 Agent；写入范围由 Skill 授权约束，不宣称硬沙箱隔离。

只登记来源 Codex Home 的 `sessions/` 中可核对身份的持久主会话；side chat、ephemeral、子 Agent 和未知来源均不回流。显式临时标记直接拒绝，无持久转录也拒绝；不会因 cwd 属于项目或会话已绑定而绕过过滤。客户端新增来源格式须先验证，不默认放行。

状态位于 `<vault>/.import_files/sunday-note-agent/<project-id>/state.json`，不参与 Git 或设备同步，不作为 Raw 或知识来源；批次和去重记录不可按普通缓存清理。项目绑定仍在 Vault 外。每项目仅一个未完成批次，运行中新增轮次合并；超时或发送结果不明不自动重发。确认旧请求不在排队或执行后才用 `retry --confirm-stopped`。未解决事项由 `status` 查看，明确处理后用 `resolve --issue-id <id>` 关闭。完成或无增量均由 Skill 登记，成功后清除临时原文；用量无法观测时记为未知。

## 回归验证

每批的简短结果、修改文件与时间保留在状态的 `results` 中，不保留完整模型输出；`status` 显示最近结果。原文完成后清除，来源标识仅用于本机去重，不写入 Raw。

`bash tests/run.sh` 使用脱敏临时目录覆盖现有功能、插件构建、迁移、重复更新、停用与回退。KDI 的 `prepare/all --plugin-root <实际插件缓存目录>` 将实际部署与源码 hash 对照，并冻结对应 Skills；不填时检查 vault 内部署。

机械验证不代表客户端已发现 Skills、Obsidian GUI 已完成验收，也不证明知识库整体有效。
