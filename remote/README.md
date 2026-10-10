# 远程开发沙箱 MCP

MCP 管理已授权工作区和任务。Shell、Python、Git、编译器及文件操作都在同一个 rootless Podman 容器内执行，不提供宿主 Shell 或绕过容器的文件接口。VPS 独立部署，不随本地插件安装或停用。

## 接口

- list_workspaces：工作区 ID、容器路径、读写权限及限制，不返回宿主路径。
- execute：提交工作区、Shell 命令和时限，立即返回任务 ID；接受不等于成功。
- list_tasks：查询保留的任务，包括 MCP 重启前启动的任务。
- task_output：运行状态、退出码及最后 200 行、最多 64000 字符的输出。
- cancel_task：停止整个任务容器及子进程，不撤销已发生的写入。

读写、搜索、补丁、二进制处理和测试使用容器工具完成。只有可写挂载中的文件跨任务保留；临时目录不共享。首版不开放任务联网、依赖下载或网页预览。

## 授权与隔离

部署端提供 SANDBOX_CONFIG，结构见 workspaces.example.json。image 必须是本机不可变 sha256 镜像 ID；workspaces 是部署端定义的列表，例如：

```json
{
  "id": "project",
  "mounts": [
    {"source": "/srv/authorized-project", "target": "/workspace/project", "mode": "rw"},
    {"source": "/srv/authorized-reference", "target": "/workspace/reference", "mode": "ro"}
  ]
}
```

模型不能修改挂载、镜像或权限配置。目录必须存在且不经过符号链接；禁止宿主根目录、整个 HOME、凭据及服务配置目录。获准目录内不得存放密钥、socket 或其他不应暴露的内容。

所有任务固定禁网、只读镜像、私有 PID/IPC、移除 capabilities、禁止提权；一次一个任务，256 MiB 内存、64 个进程、0.5 CPU，最长 300 秒。必须使用 rootless Podman 和具备 CPU/内存/进程限制的 cgroups v2；预检失败停止，不回退宿主执行。临时目录 64 MiB，单文件写入上限 64 MiB；挂载的总磁盘用量仍需部署端配额或监控。

每个任务日志约 1 MiB，旧输出可能丢弃。新任务淘汰过量已退出容器，保留最多 20 个完成记录。超时由容器运行时执行，MCP 退出不会导致任务无限运行。

**可写挂载允许覆盖、删除，没有正文过滤、hash 更新保护或自动版本备份。** 不得直接挂载含私人段落的 Routine，需排除或提供脱敏副本。真实文档先建立备份；项目使用独立工作副本，不与同步端并发改同一文件。

## 部署

要求 Linux、Node.js 20+、rootless Podman、uidmap、cgroups v2。以服务普通用户构建并记录镜像 ID：

```bash
podman build -t localhost/sundaynote-sandbox:3.0.0 -f Containerfile .
podman image inspect localhost/sundaynote-sandbox:3.0.0 --format '{{.Id}}'
npm ci --omit=dev
```

默认配置示例没有工作区或可运行镜像。真实配置留在部署端，不入库。工作区只包含显式挂载的目录；部署 scratch 不会自动提供 vault 访问，增加文档目录前须确认各目录的只读或可写权限。

服务环境：
- SANDBOX_CONFIG：工作区配置绝对路径。
- FS_API_KEY：沿用 Bearer 凭据，至少 16 字符。
- BRIEFING_PORT：沿用现有端口变量，默认 8787，只监听 127.0.0.1。
- HOME、XDG_RUNTIME_DIR、DBUS_SESSION_BUS_ADDRESS：普通用户的 rootless 运行环境。

执行 npm start，沿用现有隧道与认证。控制进程需要运行 uidmap 辅助程序及创建用户级 cgroup。systemd 用户服务使用以下配置；不要直接继承旧文档服务的 PrivateTmp 或 NoNewPrivileges，否则 rootless Podman 可能无法创建命名空间：

```ini
[Service]
Delegate=yes
NoNewPrivileges=no
PrivateTmp=no
MemoryMax=192M
CPUQuota=50%
TasksMax=64
```

这些是控制进程的运行条件，不是任务权限；容器任务自身仍强制禁止提权、独立临时目录及前述资源限制。不启用 root Podman API socket，不挂载容器管理 socket。

先通过临时工作区验证，再切换服务并检查隧道连接状态。旧文档接口已移除，依赖它的自动任务需先迁移。旧正文过滤授权不能自动转换为挂载授权。

## 验证

```bash
npm ci
npm test
SANDBOX_TEST_IMAGE=sha256:实际镜像ID npm test
```

默认测试检查配置与命令契约；设置镜像 ID 后还运行真实 HTTP/MCP 测试，覆盖认证、宿主路径/凭据隔离、只读挂载、禁网、实际 cgroup 限额、工具链、二进制读写、并发拒绝、取消及超时。只使用临时 fixture，不读取真实 vault。

## 客户端边界

本地 SundayNoteAgent 可通过 --remote-app-id 引用现有远程 App，但不会把本地 Skills 发布到云端。Add custom MCP server 连接与本地 Skills 插件仍是不同入口。

接口切换后在 ChatGPT 连接详情执行 Refresh，确认上述五个工具出现，再开新会话验收。服务可用不代表客户端目录已刷新。
