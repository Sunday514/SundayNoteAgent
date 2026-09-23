# Sunday Note 安装器

本目录用于把 SundayNoteAgent 安装到私人 Obsidian vault。

## Monitor（可选）

```bash
# 只安装/更新 Monitor，不刷新 Vault 的其他规则、模板或文档
bash SundayNoteAgent/install/install.sh --vault-root . --with-monitor --monitor-only

# 卸载自己的 Hook、桌面入口和托管副本，保留日志
bash SundayNoteAgent/install/install.sh --vault-root . --without-monitor --monitor-only
```

依赖：Linux、Python 3.11+、支持 `--ephemeral`、`--ignore-user-config` 和 permission profile 的 Codex CLI，以及 `rg`。反馈还需要 `codex queue` 和支持 MCP Apps 的客户端。连接由原生队列命令处理，不额外读取 App Server 或检查来源模型。

安装会导出 Monitor Skill、脚本和 widget，在用户级 `config.toml` 注册 `sunday_note_monitor` MCP 服务，合并 `hooks.json`、启用 Hooks，并清理旧弹窗脚本和桌面入口。其他 MCP 配置不变；未托管的同名服务会阻止安装。已有 inline `Stop` / `UserPromptSubmit` 配置会阻止安装，避免同层配置互相遮蔽。安装后必须通过 Codex `/hooks` 审阅信任 Hook，并重新加载客户端让来源会话获得渲染工具。不会绕过信任检查。一个用户配置绑定一个 Vault；不同设备分别安装验证。

`UserPromptSubmit` 只保存当前轮次的请求；`Stop` 快速入队，由后台执行器调用 Luna。全局只有一个执行器；没有定时器、常驻模型或新增聊天任务。结束或中断的父会话不会终止已经启动的 Monitor。

项目范围在 Vault 本地 `.logs/codex/config.json` 的 `project_roots` 数组管理，默认仅包含绑定的 Vault。修改该字段即可选择多个项目，例如：

```json
"project_roots": ["/path/to/vault", "/path/to/project"]
```

使用绝对目录路径，匹配会话 `cwd` 及其子目录，按符号链接解析后的真实路径判断。Git 项目还按公共 Git 目录识别仓库身份，自动覆盖同一仓库的所有 worktree（包括之后创建的工作树）；独立 clone 不视为同一仓库，不放行其他项目的 worktree。非 Git 目录仍只匹配目录范围。空数组停止所有项目采集，缺少字段时仅监控 Vault。更新安装保留已有选择。配置在下一次事件或排队任务处理时生效，范围外事件不登记、不调用模型、不通知；已排队的范围外任务会被移除，正在执行的检查不强行终止，历史日志保留。`reference_roots` 只用于证据读取，不会启用项目监控。

只监控带有非空 `transcript_path` 的可追溯持久会话。缺失、null 或空白路径的事件直接跳过，不登记、不调用 Luna、不通知；这也排除了当前桌面的临时侧对话，但不是精确的侧对话类型识别。入口不检查文件是否已存在，避免落盘延迟误过滤；已有历史日志不改写。

桌面 Hook 不一定继承终端代理。需要代理时，用安装器的 `--proxy-url http://127.0.0.1:<端口>` 设置：

```bash
python3 SundayNoteAgent/install/configure_monitor.py --vault-root /path/to/vault --proxy-url 'http://127.0.0.1:<端口>'
```

代理只写入 Vault 的 `.logs/codex/config.json`，重新安装会保留；传空字符串清除。它用于 Codex 服务连接，不开放模型命令的网络权限。

Skill 先阅读本轮对话并整理历史摘要；明确没有实质改动或信息增量时直接结束。很可能有改动、新决定、结论、失败或修复时，主动读取涉及对象及关联实现、测试、设计与知识，不要求先发现异常。代码、配置或工具结构改动包含冗余检查：有具体依据的精简候选可以先提议并标明风险，由用户决定是否采用。读取不限制文件数或扩展轮次，答案充分或不再获得有效信息时停止；约 64K tokens 是上下文软上限，不是读取目标。语义判断留在 Skill，不用脚本关键词预判；每轮记录与建议推送分开，保持只读。

运行控制（在 Vault 根目录执行）：

```bash
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json status
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json pause
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json resume
python3 .sunday-note-agent/monitor/monitor.py --config .logs/codex/config.json retry
```

暂停不强杀正在完成的检查；后续事件不采集。认证、沙箱、模型不可用、网络或额度不足时保留队列并暂停处理，后续会话事件或手动重试再次尝试，不自动转 API 或升级模型。单轮超时、一般执行失败或非法输出只记录失败及来源，清除该轮暂存原文并继续其他轮次；不伪造摘要，也不自动重复消耗额度。运行配置使用当前 ChatGPT 登录；不复制凭据。

完全重复的 Hook 不更新轮次版本，也不重跑检查。没有收到 `Stop` 的输入，在同一会话下一轮提交时，或超过 24 小时后的下一次事件到来时清理，只保留未完成登记与来源。没有定时清理进程，因此没有后续事件时不会主动清理。

范围内所有模型的会话统一分析和记录，无建议也登记。日志只进入 `.logs/codex/`：会话 JSONL 简记用户请求与决定、主任务声明、原会话证据和明确遗留问题；不保存 Luna 补查的推测作为原会话事实。findings 保存建议、完整证据和投递状态；queue 暂存待处理请求/回复，完成后移除原文。既有日志不改写，旧分析字段不再用于近期摘要路由。该目录包含私人运行记录，不提交到工具仓库。

模型使用继承只读策略、仅 scratch 可写的 permission profile；外部命令网络关闭，网页搜索使用 Codex 工具。每次启动先运行无损沙箱探针。全部模型输出均经过结构检查；文件证据需匹配短原文，并记录归档时内容 hash。网页阅读仍是模型声明，文件匹配不能替代领域正确性判断。Monitor 不承担正式 Review 或独立验收。

仅在有价值、引用校验通过且未重复的内容产生后投递反馈，不检查来源会话模型。每轮最多一份反馈，其 ID 经 `codex queue --thread … --message …` 送回会话 A，不覆盖模型与权限。短指令要求 A 只调用 `render_monitor_feedback`，不分析、解释、提问或执行建议；工具不可用时仅说明无法显示面板。A 活跃时使用原生队列，不强行中断。提示词约束不是客户端强制执行保证。

widget 仅显示一段简短摘要和至多一个决策，方案用紧凑单选列表展示。推荐、建议和背景是摘要中的可选参考内容，不按分类分栏，也不要求逐类覆盖。无决策时，确认仅记为已阅；有决策时，将确认的选择通过原生队列发回同一个 A，由 A 核实后执行。忽略只更新本地状态。处理成功后保留原面板和已选方案，仅将按钮与选项禁用置灰，不追加状态文字；重新渲染时同样保持禁用。队列结果不明时保留待核实状态，不自动重发。面板操作工具仅向应用暴露，不向模型暴露。

会话日志按 `.logs/codex/projects/<项目身份>/sessions/` 归类；Git 公共目录相同的工作树共用项目目录，非 Git 项目按真实路径区分。每个项目的 `context.json` 保存基本身份和带来源的关键事实，每轮注入 Monitor；只有目标、约束、入口、里程碑或阻塞状态实质变化时更新，普通进度不反复改写。它是上下文索引，不替代代码和正式文档。旧日志可用 Monitor 的 `migrate` 命令原样归位；全局队列、锁与建议 ID 存储仍共用，已有面板链接不变。

有选项时，widget 固定追加“其他”（不由 Monitor 生成）；选中后显示文本框，填写自己的判断或处理方式，不能为空，输入上限为 4000。确认后，自定义内容作为用户选择回传原会话，由主 Agent 核实后处理；面板保留输入并禁用控件。无选项时不显示“其他”。

反馈轮次使用 `[SundayNote Monitor]` 标记，保持登记但不再次调用 Luna，防止循环；用户确认后的任务正常检查。`status` 显示反馈状态计数：`queued` 仅表示队列接受，不代表已渲染或已决定；`skipped_scope` 表示已移出范围，`failed` 表示未获投递成功确认，`sending` 表示可能在投递期间中断。失败不影响原始摘要，`retry` 仅重试分析队列。不另起 `resume`、修改聊天数据库或使用弹窗兜底。真实客户端的工具加载、渲染和关闭需在安装后验证。

Codex 可执行文件由 Linux Hook 的进程祖先自动识别，按轮次保存；检查、建议展示及用户确认回传均沿用来源程序，不采用安装时 PATH 中的 CLI。App 与独立 CLI 会话可以使用不同版本。无法识别或来源程序不可执行时记录失败，不静默切换另一版本；没有来源路径的旧待处理记录也不猜测执行程序。这只统一可执行文件，不代表连接到同一个 App Server 实例。

实现借鉴 [memsearch](https://github.com/zilliztech/memsearch/blob/main/docs/platforms/codex/how-it-works.md) 的隔离调用、[codex-observational-memory](https://github.com/sovorn-c/codex-observational-memory) 的来源索引和 [honcho-codex](https://github.com/rafachavantes/honcho-codex) 的按轮采集，不依赖这些服务。Hook 契约以 [Codex 官方文档](https://learn.chatgpt.com/docs/hooks) 为准。

Monitor 默认使用 `gpt-6-luna` / `xhigh`，每轮硬上限 10 分钟。优先检查直接涉及材料和一跳关联来源，疑问已解决即停止；不会为凑建议重跑主任务。需要核对其他代码仓库时，可在本地 `.logs/codex/config.json` 的 `reference_roots` 数组中列出明确目录；它只限定检索指导和可接受的文件证据，不是操作系统级读取隔离。不要填用户主目录或文件系统根目录。未配置时仅接受当前项目和 Vault 的文件引用，引用必须是连续原文。

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
