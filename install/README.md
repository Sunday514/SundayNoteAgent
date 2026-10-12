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

## 文件职责

| 位置 | 内容 |
| --- | --- |
| 源码仓库 | Skills、脚本、安装源文件，唯一开发真值 |
| vault | 笔记、模板、根 AGENTS、`SundayNoteTools/quickadd/` |
| `$XDG_CONFIG_HOME/sunday-note-agent/` | vault/模式绑定、Wiki 读取授权与独立的设备同步授权 |
| `$XDG_STATE_HOME/sunday-note-agent/` | 插件迁移检查点与备份 |
| `$XDG_DATA_HOME/sunday-note-agent/marketplace/` | 生成的插件发布源，由安装器刷新 |

未设置 XDG 时分别使用 `~/.config`、`~/.local/state`、`~/.local/share`。这些目录和 Codex 凭据都不进入 vault 或仓库。

QuickAdd 按现有 ID 更新本项目入口，保留用户 choices；个人模式提供每日记录、日记与周/月统计，工作模式仅提供工作每日记录。脚本位于可见托管目录，移动或停用 Agent 插件不影响它。缺少 Obsidian 插件时报告跳过，不自动启用。

根规则由 scaffold 与模式段生成，保留个人模式末尾唯一的“个性化响应”段。首页、Daily 模板和个人上下文仅在缺失时创建；迁移不覆盖已有 Routine 模板。需要刷新托管 Weekly/Monthly 时显式运行基础安装器，使用相同外置 `--install-state`。

工作 Wiki 读取范围在本机 `config.json` 的 `work_wiki_roots` 数组中明确配置，例如 `["知识库/基础知识/Agent工程"]`。值为知识库下的相对目录或具体 Markdown 文件，不批准整个知识库；未配置时 Query／Lint 不遍历 Wiki，准确路径、全局索引及符号链接也不能绕过。未绑定且无安装模式记录的 Vault 不允许用 `--mode personal` 扩大范围。个人模式支持工作与个人材料共存，工作请求仍使用 work 范围。重复安装保留同一 Vault 的批准列表，不将其复制给其他 Vault。

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

## 验证

`bash tests/run.sh` 使用脱敏临时目录覆盖现有功能、插件构建、迁移、重复更新、停用与回退。KDI 的 `prepare/all --plugin-root <实际插件缓存目录>` 将实际部署与源码 hash 对照，并冻结对应 Skills；不填时检查 vault 内部署。

机械验证不代表客户端已发现 Skills、Obsidian GUI 已完成验收，也不证明知识库整体有效。
