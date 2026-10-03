# Sunday Note 安装器

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

先自行安装并启用需要的 Obsidian 插件。插件缺失时核心安装仍完成，并报告跳过的集成。保留模板时，QuickAdd 创建每日记录仍要求当前模式的固定路径下已有模板。论文总结使用 Agent 当前可用的 PDF 读取和页面渲染工具，不依赖 Docling 或指定 Conda 环境；缺少阅读能力时说明限制，不自动安装依赖。更新只清理已知废弃脚本和模板，保留旧论文工作区和用户额外文件。

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

```bash
# 只安装/更新 Monitor，不刷新 Vault 的其他规则、模板或文档
bash SundayNoteAgent/install/install.sh --vault-root . --with-monitor --monitor-only

# 卸载自己的 Hook、桌面入口和托管副本，保留日志
bash SundayNoteAgent/install/install.sh --vault-root . --without-monitor --monitor-only
```

依赖：Linux、Python 3.11+、支持 `--ephemeral`、`--ignore-user-config` 和 permission profile 的 Codex，以及 `rg`。自动反馈还需要来源 App 的 `CODEX_APP_TOOLS_PIPE_PATH` 状态接口、`codex queue` 和 MCP Apps；该本地接口随 App 版本变化可能失效，失败时暂存而不猜测空闲。

安装会导出 Monitor Skill、脚本和 widget，在用户级 `config.toml` 注册 `sunday_note_monitor` MCP 服务，合并 `hooks.json`、启用 Hooks，并清理旧弹窗脚本和桌面入口。其他 MCP 配置不变；未托管的同名服务会阻止安装。已有 inline `Stop` / `UserPromptSubmit` 配置会阻止安装，避免同层配置互相遮蔽。安装后必须通过 Codex `/hooks` 审阅信任 Hook，并重新加载客户端让来源会话获得渲染工具。不会绕过信任检查。一个用户配置绑定一个 Vault；不同设备分别安装验证。

`UserPromptSubmit` 登记请求并推进会话版本；`Stop` 快速入队。每个会话一个后台执行器，不同会话可并行，同一会话串行；没有常驻模型或新增聊天任务。执行器一次整理积压的已完成轮次，逐轮保存摘要，结合旧待发建议产生最新反馈。新请求到来后旧结果不能投递，下一轮完成时继续滚动复核。

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

暂停不强杀正在完成的检查；后续事件不采集。认证、沙箱、模型不可用、网络或额度不足时保留队列并暂停处理，后续会话事件或手动重试再次尝试，不自动转 API 或升级模型。部分分析保留有效检查点和待处理轮次，即使摘要已齐全也不清队列；等待下一事件或手动重试，不立即循环调用模型。完整分析成功后保存正式摘要并清除暂存原文；检查点不作为完成标记，不伪造缺失摘要。运行配置使用当前 ChatGPT 登录；不复制凭据。

完全重复的 Hook 不更新轮次版本，也不重跑检查。没有收到 `Stop` 的输入，在同一会话下一轮提交时，或超过 24 小时后该会话的新事件到来时清理，只保留未完成登记与来源。其他会话不清理它的队列。

范围内所有模型的会话统一分析和记录，无建议也登记。日志只进入 `.logs/codex/`：会话 JSONL 简记用户请求与决定、主任务声明、原会话证据和明确遗留问题；不保存 Luna 补查的推测作为原会话事实。findings 保存建议、完整证据和投递状态；queue 暂存待处理请求/回复，完成后移除原文。既有日志不改写，旧分析字段不再用于近期摘要路由。该目录包含私人运行记录，不提交到工具仓库。

模型使用继承只读策略、仅 scratch 可写的 permission profile；外部命令网络关闭，网页搜索使用 Codex 工具。每次启动先运行无损沙箱探针。全部模型输出均经过结构检查；文件证据需匹配短原文，并记录归档时内容 hash。网页阅读仍是模型声明，文件匹配不能替代领域正确性判断。Monitor 不承担正式 Review 或独立验收。

仅在有价值、引用校验通过且未重复的内容产生后准备反馈。通过来源 App 的 `read_thread` 两次确认 `idle`、最新轮次已完成且身份一致，两次间隔至少 2 秒；最终复查本地版本及待处理队列，再经 `codex queue` 回传，不覆盖模型与权限。活跃、等待审批、状态未知及连接失效均不投递；CLI 缺少状态通道时仍检查和记录。查询与入队并非原子操作，最后一瞬间仍存在小竞态。

有待发结果时每 5 秒进行轻量状态检查，最长 10 分钟，不调用模型；超时退出，下一次 Hook 或 `retry` 继续。没有待办时不轮询。展示消息只包含会话、建议 ID 和渲染指令，完整发现及证据由面板从本地存档读取。主会话只调用一次 `render_monitor_feedback`；工具不可用时只说明无法展示。用户主动确认后的回传不经过自动建议的空闲闸门。

首次收到用户请求时记录所在 Git 仓库的 HEAD，首次审查使用该基线覆盖后续多个提交及工作区改动；已有审查基线时继续累计。会话按仓库根路径分别保存基线，切换目标或采集失败不清除其他仓库的状态；旧单仓库状态在下次保存时归入对应仓库。旧记录缺失基线、目标仓库不同或历史改写导致基线失效时，提交范围标为未知，Review 不得声明完整。同一仓库的未知状态跨轮保留；当前 HEAD 和下一轮请求的 HEAD 均不能补证此前范围。恢复完整审查需要经过确认的原比较基线，当前安装器不自动补填。工作区差异仅作为背景证据，不归因于本轮。

widget 默认显示简短摘要、发现数量和至多一个方向问题；完整发现与证据折叠，可展开并复制。三个按钮为“处理／确认／忽略”：处理立即交接全部发现与用户输入；确认只标记问题有意义，可继续编辑方向并转为处理；忽略表示当前不处理。方向问题针对整份反馈，不绑定单条发现。处理成功或忽略后冻结操作，不清空面板，展开与复制仍可用。提交失败也更新复制文本；命令未启动时可重试，超时或非零退出等结果不明时保持待核实，不盲目重发。

确认和忽略的新增操作由下一次正常 `UserPromptSubmit` 通过 `additionalContext` 交接，不调用模型、不发送独立消息。按会话、操作版本和 turn 记录交接；正常完成证据出现后消费，中断或状态不明时允许带相同 ID 重交接。整份反馈分批，不截断用户输入；旧历史不批量回注，已立即处理的内容不重复注入。注入是历史反馈数据，不是自动执行授权。Hook 读取失败不阻断主任务。

会话日志按 `.logs/codex/projects/<项目身份>/sessions/` 归类，旁边的会话目录保存队列、状态和执行锁。Git 公共目录相同的工作树共用项目目录，非 Git 项目按真实路径区分。项目 `context.json` 只保存有来源的关键事实；按项目短时加锁合并更新，旧事件不能覆盖新决定。建议 ID 存储保持不变，已有面板链接保留。

安装更新先暂停并停止旧执行器，再一次性迁移旧待处理队列；保留日志和用户选择，旧待发建议需经新对话复核。`status` 按会话显示积压、阻塞原因、分析和入队时间、Codex 返回的 token 用量；未知用量为 null。批量分析用量只记一次，不按轮次重复累计。

有选项时，widget 固定追加“其他”（不由 Monitor 生成）；选中后显示文本框，输入上限为 4000。“处理”和“确认”要求其他文本非空；忽略可保留未完成输入。确认仅保存且仍可编辑，处理成功或忽略后冻结；展开和复制始终可用。无选项时不显示“其他”。

反馈轮次使用 `[SundayNote Monitor]` 标记，保持登记但不再次调用 Luna，防止循环；用户确认后的任务正常检查。`status` 显示反馈状态计数：`queued` 仅表示队列接受，不代表已渲染或已决定；`skipped_scope` 表示已移出范围，`failed` 表示未获投递成功确认，`sending` 表示可能在投递期间中断。失败不影响原始摘要，`retry` 仅重试分析队列。不另起 `resume`、修改聊天数据库或使用弹窗兜底。真实客户端的工具加载、渲染和关闭需在安装后验证。

Codex 可执行文件由 Linux Hook 的进程祖先自动识别，按轮次保存；检查、建议展示及用户确认回传均沿用来源程序，不采用安装时 PATH 中的 CLI。App 与独立 CLI 会话可以使用不同版本。无法识别或来源程序不可执行时记录失败，不静默切换另一版本；没有来源路径的旧待处理记录也不猜测执行程序。这只统一可执行文件，不代表连接到同一个 App Server 实例。

实现借鉴 [memsearch](https://github.com/zilliztech/memsearch/blob/main/docs/platforms/codex/how-it-works.md) 的隔离调用、[codex-observational-memory](https://github.com/sovorn-c/codex-observational-memory) 的来源索引和 [honcho-codex](https://github.com/rafachavantes/honcho-codex) 的按轮采集，不依赖这些服务。Hook 契约以 [Codex 官方文档](https://learn.chatgpt.com/docs/hooks) 为准。

Monitor 默认使用 `gpt-6-luna` / `xhigh`，整轮硬上限 10 分钟，预留最后一分钟汇总。事实包提供原轮次、工具来源、Git 目标快照、项目上下文和待复核反馈；工作区变化不自动归因于本轮。主 Agent 选择 0～3 个独立方向：关联一致性、冗余清理、知识资料（Luna/xhigh），或代码 Review（Sol/high）。普通确认不派发；持久代码或运行配置变化是否值得审查由模型判断，不用行数阈值。Review 不另起 `codex review`，也不声称与官方模式完全等价。角色禁止递归派发并继承只读边界；专项说明随 Skill 的 `references/` 安装。

宿主预生成委托材料；主 Agent 按整轮难度选择统一的建议上限：简单 2 分钟、中等 4 分钟、困难 6 分钟，包含分派、检查与汇总。它不是目标耗时，充分回答就结束；不为各子任务单独评级、计时或续期。接近建议上限时优先收敛，未覆盖范围如实保留；有效报告不因超过建议时间被降级。整轮 600 秒硬上限保持不变。

主 Agent 只记录历史、等待、去重和简短汇总，不重复已委托调查。子报告发现通过 direction/index 直接交接，由宿主原样装配已经校验的证据，不让主 Agent 重抄。阶段计时来自宿主观察，子任务事件缺失时保持未知；固定范围的串行／并行对照不以新增检查换取表面覆盖收益。

摘要检查点与专项报告只写 scratch，宿主校验后保存。失败或超时保留有效部分及未覆盖范围，没有最终汇总时不推送；下一轮复核。目标及证据变化时暂存，避免旧版本结论直接投递；同版本已完成 Review 可复用。父任务用量与子任务用量分开记录，不可观察时为未知，不把父用量当总量。旧建议安装时一次性转为单发现报告，保留 ID、证据、用户选择和处理状态；旧待发内容仍须复核。

优先检查直接涉及材料及真实关联来源，疑问已解决即停止，不为凑建议重跑主任务。需要核对其他代码仓库时，可在本地 `.logs/codex/config.json` 的 `reference_roots` 数组中列出明确目录；它只限定检索指导和可接受的文件证据，不是操作系统级读取隔离。不要填用户主目录或文件系统根目录。未配置时仅接受当前项目和 Vault 的文件引用，引用必须是连续原文。
