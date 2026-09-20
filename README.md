# SundayNoteAgent

SundayNoteAgent 是一套用于 Obsidian 知识库的 agent 工具层。它提供安装器、Codex / agent skills、QuickAdd 自动化脚本、固定 vault 布局和最小 Routine 模板，适合放在私人知识库中的 `SundayNoteAgent/` 目录下作为工具层独立 repo 使用。

个人笔记、带具体条目的个人模板、附件、图片和本地运行状态由父知识库管理，不属于本仓库。

## 安装

在 vault 根目录拉下工具层，然后安装配置和目录骨架：

```bash
mkdir -p ~/Notes/MyVault
cd ~/Notes/MyVault
git clone git@github.com:Sunday514/SundayNoteAgent.git SundayNoteAgent
bash SundayNoteAgent/install/install.sh
```

在已有知识库中安装：

```bash
cd ~/Notes/MyVault
git clone git@github.com:Sunday514/SundayNoteAgent.git SundayNoteAgent
bash SundayNoteAgent/install/install.sh --vault-root .
```

需要启用论文总结时，在安装命令中增加可选组件：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --with-paper-summarizer
```

已有自己的 Daily、Weekly、Monthly 模板或 Calendar 创建规则时，保留它们并跳过托管模板部署：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --routine-templates preserve
```

本地验证或使用 fork 时：

```bash
mkdir -p /tmp/my-vault-test
cd /tmp/my-vault-test
git clone /path/to/SundayNoteAgent SundayNoteAgent
bash SundayNoteAgent/install/install.sh
```

## 安装后内容

安装器会创建父知识库骨架并设置 agent 工具入口：

```text
.agents/skills/sunday-note-ingest                  # 安装器托管副本
.agents/skills/sunday-note-lint                    # 安装器托管副本
.agents/skills/sunday-note-query                   # 安装器托管副本
.agents/skills/sunday-note-context                 # 安装器托管副本
.agents/skills/paper-summarizer                    # 安装器托管副本，可选
.sunday-note-agent/config/quickadd-rollups.json
.stignore                                          # 保留已有规则并补充工具层和导入目录
个人模板/每日记录.md、每周记录.md、每月记录.md          # Routine 最小骨架
个人上下文.md                                     # 根目录五章空页面，只在缺失时创建
```

默认的 `--routine-templates managed` 会刷新托管的 Weekly 和 month pack 模板，并在缺失时创建 Daily 模板。`--routine-templates preserve` 不创建或刷新模板，也不修改 Calendar 的 Weekly 模板设置。两种模式都会刷新托管的根规则和 skills，并保留个人上下文与已生成的个性化响应段。论文总结 skill 首次启用时传入 `--with-paper-summarizer`；启用后普通重跑也会继续更新。当前 Python 缺少 Docling 时，Skill 已长期授权使用 `conda run -n papers python`，无需确认。

安装完成后可主动要求 agent“初始化个人上下文”，具体流程见[安装器说明](install/README.md)。

用户先安装并启用需要的 Obsidian 插件，然后关闭 Obsidian、运行安装器，完成后再启动 Obsidian。Calendar 可用且模板模式为 `managed` 时，安装器维护 Weekly 创建格式、目录和模板字段；`preserve` 模式保留 Calendar 配置。QuickAdd 可用时，安装器维护“统计本周打卡”和“刷新每月统计”两个 Routine choice，并从可见的 `SundayNoteAgent/automation/quickadd/rollup.js` 加载脚本。其他插件字段、choices 和社区插件启用列表保持不变。缺失的可选插件不会阻断核心安装，安装结果会列出未配置的工作流。

## 更新

更新工具层后，在父知识库中重新运行安装器即可刷新导出内容：

```bash
cd ~/Notes/MyVault/SundayNoteAgent
git pull --ff-only
cd ..
bash SundayNoteAgent/install/install.sh --vault-root .
```

安装器只部署当前 checkout，不自动执行 Git 操作。

## 目录结构

父 vault 使用固定一级布局，安装器和 skills 直接依赖这些路径：

| 路径 | 角色 | 默认维护边界 |
|---|---|---|
| `首页.md` | 知识库导航入口 | 由父 vault 维护 |
| `个人上下文.md` | 个性化任务所需的稳定上下文 | 只在用户明确初始化或校准后更新 |
| `.import_files/` | PDF、docx、网页导出、解析产物和临时日志 | 只由导入流程管理 |
| `10_原始材料/` | 论文、书籍、课程等长期来源总结 | 默认只读 |
| `20_每日记录/` | Daily Routine | 明确操作和目标后写入 |
| `21_每周记录/` | Weekly Routine | 明确操作和目标后写入 |
| `22_每月记录/` | Monthly Routine | 明确操作和目标后写入 |
| `23_项目复盘/` | Project Routine | 明确操作和目标后写入 |
| `30_知识库/` | agent 可维护的长期 Wiki | 按 skills 和根规则维护 |
| `40_个人写作/` | 可选 Journal | 仅用户明确要求时读写 |
| `assets/figures/` | 长期引用图像 | 文档使用相对路径引用 |
| `个人模板/` | 父 vault 本地模板 | 个人内容不回写工具仓库 |
| `SundayNoteAgent/` | 可公开的工具层源码 | 由 Git 和安装器维护 |
| `.agents/` | 安装后的 agent skills | 由安装器托管 |
| `.sunday-note-agent/` | QuickAdd 统计配置 | 缺失时由安装器创建，之后由父 vault 维护 |
| `.obsidian/` | Obsidian 插件配置和运行状态 | 仅合并已预装可选插件的项目字段 |

`.import_files/` 是导入流程的临时目录，不属于知识分层。完成整理的长期来源总结进入 `10_原始材料/`，长期引用的图像进入 `assets/figures/`。

工具仓库结构：

```text
AGENTS.md                 # 子项目开发规则
automation/               # QuickAdd 等自动化脚本源文件
config/                   # QuickAdd 自动化与可选 Obsidian 集成配置
install/                  # 安装器和父知识库 scaffold
migration/                # 可复用知识库迁移辅助工具
skills/                   # Codex / agent skills
templates/                # 无具体条目的 Routine 最小模板
tests/                    # 脱敏 fixture 和统一回归入口
```

`templates/` 保存 Daily、Weekly 和 month pack 的最小结构契约；Daily 模板只在缺失时创建，Weekly 和 month pack 模板由安装器刷新。`config/obsidian/` 只保存 Calendar 和 QuickAdd 的最小项目字段，不包含插件启用列表、workspace、设备路径、环境变量、代理、sessions 或权限运行状态。

## Monitor（可选）

Monitor 在本机任意 Codex 主会话每轮回复后，用订阅内的 `gpt-5.6-luna` 做只读关联检查。每轮登记，只有信息增量才写摘要，有可行动发现才通知。建议可在原生面板中复制、暂存或点选；不会修改项目、Wiki 或自动发送到其他会话。

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --with-monitor --monitor-only
```

安装后在 Codex `/hooks` 中审阅并信任两个 Hook；应用菜单打开 **Monitor 建议**。日志、建议及状态集中在 `.logs/codex/`。需要 Linux、Python 3.11+、Codex CLI、Zenity、notify-send、rg 和剪贴板工具。只使用 ChatGPT 订阅登录，不回退 API 或其他模型。

详见 [Monitor 安装和使用](install/README.md#monitor可选)。

## 相关入口

- [安装器说明](install/README.md)
- [子项目开发规则](AGENTS.md)

## 回归检查

修改安装器、QuickAdd 自动化或 query / lint 脚本后，运行：

```bash
bash tests/run.sh
```

检查只使用 Bash、Node 和 Python 标准运行时，在临时目录中生成脱敏 fixture，验证核心 skill、安装导出、Query/Lint 脚本和 QuickAdd，不读取父 vault。

## 隐私边界

本仓库只保存可复用工具层，不保存个人知识库正文。不要把以下内容提交到 SundayNoteAgent：

- 个人笔记正文
- 带具体条目的个人模板正文
- 私有附件、截图或图片
- token、API key、本机绝对路径
- Obsidian workspace 运行状态
- 一次性调试输出或临时工作记录
