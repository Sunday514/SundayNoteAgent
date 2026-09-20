# Sunday Note 安装器

本目录用于把 SundayNoteAgent 安装到私人 Obsidian vault。

同一脚本同时用于首次安装和更新：补建缺失的 vault 骨架和本地基线文件，并用当前 checkout 覆盖安装器托管内容。它不移动、重命名或整理已有个人内容，也不自动执行 Git 操作。

安装器设置工具入口和本地默认配置：

```text
.agents/skills/sunday-note-ingest                  # 安装器托管副本
.agents/skills/sunday-note-lint                    # 安装器托管副本
.agents/skills/sunday-note-query                   # 安装器托管副本
.agents/skills/sunday-note-context                 # 安装器托管副本
.agents/skills/paper-summarizer                    # 安装器托管副本，可选
.sunday-note-agent/config/quickadd-rollups.json
.obsidian/plugins/calendar/data.json               # 已预装启用时合并项目字段
.obsidian/plugins/quickadd/data.json               # 已预装启用时合并项目 choices
```

## 使用

先在 Obsidian 中安装并启用需要的 Calendar、QuickAdd，然后关闭 Obsidian。运行安装器并看到完成提示后再启动 Obsidian，确保插件从更新后的 `data.json` 加载配置。

在知识库根目录拉下本项目，然后运行安装：

```bash
cd ~/Notes/Sunday-note
git clone git@github.com:Sunday514/SundayNoteAgent.git SundayNoteAgent
bash SundayNoteAgent/install/install.sh
```

本地验证或使用 fork 时：

```bash
mkdir -p /tmp/Sunday-note-test
cd /tmp/Sunday-note-test
git clone /path/to/SundayNoteAgent SundayNoteAgent
bash SundayNoteAgent/install/install.sh
```

如果已经在已有 vault 的 `SundayNoteAgent/` 目录下，可以在外部 vault 根目录运行：

```bash
bash SundayNoteAgent/install/install.sh --vault-root .
```

论文总结是可选组件，依赖可运行 Docling 的 Python 环境。需要启用时增加参数：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --with-paper-summarizer
```

当前 Python 缺少 Docling 时，Skill 已长期授权使用 `conda run -n papers python`，无需确认；两者均不可用则停止。

已有自己的 Routine 模板和 Calendar 创建规则时，使用保留模式：

```bash
bash SundayNoteAgent/install/install.sh --vault-root . --routine-templates preserve
```

`--routine-templates managed` 是默认值：部署 Daily、Weekly、Monthly 模板并维护 Calendar Weekly 字段。`preserve` 不读取、创建或刷新这些模板，也不修改 Calendar 配置；其他核心安装与 QuickAdd 可选集成照常执行。

## 生成内容

安装器会创建缺失的：

- `AGENTS.md`：安装后的私人 vault 根规则。
- `首页.md`：vault 首页。
- `.import_files/`：隐藏导入工作目录。
- `10_原始材料/`、`20_每日记录/`、`21_每周记录/`、`22_每月记录/`、`23_项目复盘/`、`30_知识库/`、`40_个人写作/`、`个人模板/`。
- `个人模板/每日记录.md`：无具体打卡项的最小 Routine 骨架，只在缺失时创建。
- `个人模板/每周记录.md`、`每月记录.md`：包含统计刷新链接的 Weekly 和 month pack 骨架，每次安装刷新。
- `个人上下文.md`：按兴趣与经验、价值取舍、证据与知识演化、判断与协作、表达偏好组织的根目录空页面。
- `.stignore`：保留已有规则并补充 `/SundayNoteAgent` 和 `/.import_files`，避免工具仓库与导入中间产物进入 Syncthing 同步。
- `SundayNoteAgent/` 工具层目录。

其中 `.import_files/` 是 PDF、docx、网页导出和解析中间产物的临时导入目录；`40_个人写作/` 只是空目录骨架，安装器不定义其中内容，也不维护其内部结构。

四个核心 Skills、固定一级目录和最小 Routine 模板不依赖社区插件。Calendar、QuickAdd 缺失或未启用时，核心安装照常完成，安装结果会明确列出未配置的可选工作流。

不论是新 vault 还是已有 vault，安装器都会补建缺失的标准一级目录和 `.import_files/`，但不创建二级结构，也不整理已有内容。工具入口的维护方式是：

- 每次刷新父 vault 的 `AGENTS.md` 托管规则和四个基础 skill；`managed` 模式同时刷新 Weekly 和 month pack 模板。
- 传入 `--with-paper-summarizer` 时首次导出 `paper-summarizer`；已导出时，普通重跑也会刷新它。
- 托管目录中不与源仓库同名的额外文件会保留。
- 父 vault `.sunday-note-agent/config/quickadd-rollups.json` 下的 QuickAdd 统计配置只在缺失时创建，已有配置保持不变。
- 已安装并启用 Calendar 且模板模式为 `managed` 时，维护 `showWeeklyNote`、`weeklyNoteFormat`、`weeklyNoteTemplate`、`weeklyNoteFolder`；`preserve` 模式保持 Calendar 配置不变。
- 已安装并启用 QuickAdd 时，按稳定 ID 或名称维护“统计本周打卡”和“刷新每月统计”两个 Routine choices。
- Calendar、QuickAdd 的其他字段、其他 choices 和 `.obsidian/community-plugins.json` 保持不变。
- 父 vault `.stignore` 保留已有内容，每次安装确保包含根目录规则 `/SundayNoteAgent` 和 `/.import_files`。

根目录 `个人上下文.md` 缺失时创建空 scaffold，已有文件保留。根 `AGENTS.md` 以唯一末尾章节 `## 个性化响应` 保留个人响应段，兼容 LF 和 CRLF 标题；无该章节时部署托管根规则，标题重复时停止覆盖。段内换行保留，缺少末尾换行时补换行。

安装完成后建议用户主动要求 agent“初始化个人上下文”，或显式调用 `$sunday-note-context`。该流程逐题询问缺失信息，整个访谈最多追加两题澄清，再生成完整个人上下文、个性化响应 prompt 和入口链接；两份完整草案经一次明确确认后写入。根规则不预留该段，安装器也不自动触发或提醒该流程。

项目模板只保存稳定结构和自动块标记，不包含具体打卡类别或个人正文。Daily 模板只在缺失时创建，Weekly 和 month pack 模板由安装器刷新；自动化脚本和统计配置使用固定的 Routine 与模板路径。论文总结导入工作目录为 `.import_files`，摘要目录为 `10_原始材料`。

## 知识流

- Ingest 从用户指定的 Raw、Routine 或已确认对话中提炼稳定知识，只写入 Wiki，并保留实际来源链接。
- Query 搜索 Wiki，并在个性化任务需要时读取根目录个人上下文；Wiki 证据不足时，只沿页面中的直接链接按需读取 Raw / Routine。
- Lint 仅在用户显式调用 `$sunday-note-lint` 时触发，逐页检查整个 Wiki，并用 `lint_headers.py` 和 `audit_reachability.py` 建立机械基线。默认按唯一全局计划委派 Wiki 维护，子任务继承已授权范围；只读请求只报告。默认展示范围和结果摘要，完整任务明细按需展开，阻塞、失败和未完成事项必须报告。

安装器始终覆盖四个核心 skill；`managed` 模式覆盖 Weekly 和 month pack 模板，`preserve` 模式不触碰任何 Routine 模板。父 vault 的 Daily 模板、QuickAdd 统计配置、个人上下文和其他知识内容不进入托管覆盖范围。

普通 Routine 改写及 Ingest 多页写入，用户已明确操作和全部目标时直接执行；新增目标或操作再确认。删除、归档、未确认结论和个人上下文草案继续遵守 Skill 的专门确认规则。

根规则集中维护公共表达要求，各 Skill 保留产物约定。论文总结保持三个主章节和证据校验，小节随实际方法组织。

## 验证

在工具仓库根目录运行：

```bash
bash tests/run.sh
```

该命令使用临时 vault 验证首次安装、托管文件更新、重复安装、核心 skill 导出和安装后脚本运行，不读取实际父 vault。

## 可选 Obsidian 集成

- Calendar 的 Weekly 格式为 `gggg-[W]ww`，创建目录为 `21_每周记录`，模板为 `个人模板/每周记录.md`。
- QuickAdd 提供“统计本周打卡”和“刷新每月统计”两个 choices，统一执行可见的 `SundayNoteAgent/automation/quickadd/rollup.js`；目标不存在时由同一脚本创建。
- `automation/quickadd/rollup.js` 是通用统计入口；具体统计项由 `.sunday-note-agent/config/quickadd-rollups.json` 决定。
- 默认统计配置中，周统计按 ISO week 自动推导 7 天 Daily；month pack 包含周日落在该自然月的 ISO weeks。
- 周或 month pack 目标缺失时，统计脚本先从配置的最小模板创建文档，再更新自动块；Daily 创建流程由父 vault 本地维护。
- 如果你需要隐藏运行产物目录，可选安装并启用 `OA-file-hider`（不作为安装器硬依赖）。
