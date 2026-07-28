# Local Docker Deployment Design

## Goal

在 Windows 的 Docker Desktop（WSL 2 后端）中运行 Turning Good Agent，并仅通过宿主机的 `http://localhost:8000` 访问 Web 工作台。

## Scope

- 使用一个 Docker 镜像部署前端构建产物与 Python/FastAPI 服务。
- 在镜像构建阶段使用 Node.js 编译 React/Vite 前端；最终运行镜像不包含 Node.js 或前端开发依赖。
- 容器内应用监听 `0.0.0.0:8000`，Docker 仅将其映射为宿主机回环地址 `127.0.0.1:8000`。
- 会话数据使用 Docker 命名卷持久化在容器的工作目录下。
- `settings.local.json` 由宿主机以只读绑定挂载提供，包含 LLM API Key 等运行配置且不进入镜像。

## Architecture

```text
Windows browser
    |
http://localhost:8000
    |
Docker published port: 127.0.0.1:8000
    |
Turning Good Agent container
    |- uvicorn / FastAPI
    |- web/static (Vite build output)
    |- /app/.sessions (Docker named volume)
    `- /app/settings.local.json (read-only bind mount)
```

Dockerfile 分为三个阶段：前端依赖与构建阶段、Python 依赖构建阶段、最小 Python 运行阶段。FastAPI 延续现有静态资源挂载逻辑，因此无需改变 API、WebSocket 或业务代码。

## Files

| File | Responsibility |
| --- | --- |
| `Dockerfile` | 构建前端、安装 Python 依赖、生成最终运行镜像并启动 Web Host。 |
| `compose.yaml` | 定义本机专用服务、回环端口、会话命名卷和只读配置挂载。 |
| `.dockerignore` | 排除 Git 元数据、会话、密钥、缓存和本地前端依赖，避免泄露且缩短构建时间。 |
| `README.md` | 说明准备配置、构建、启动、停止与重建流程。 |

## Runtime Behavior

1. 用户从 `settings.example.json` 创建 `settings.local.json` 并填写 LLM 配置。
2. `docker compose up --build` 构建镜像并启动服务。
3. 浏览器访问 `http://localhost:8000`；来自局域网的连接无法通过 Docker 端口映射进入。
4. 会话 JSON/JSONL 保存至命名卷，即使容器被重新创建仍保留；执行 `docker compose down -v` 才会删除这些会话。
5. UI 自动批准设置当前会写回 `settings.local.json`。因此 Compose 将默认使用可写配置挂载；若需要只读密钥配置，自动批准应保持关闭，或将该项配置迁移为独立可写文件（不在本次范围）。

## Error Handling

- 缺少 `settings.local.json` 时，Compose 启动应明确失败，不启动一个无法配置 LLM 的服务。
- 前端构建或 Python 依赖安装失败时，Docker build 应直接失败并输出对应阶段日志。
- `.sessions` 使用命名卷，不依赖 Windows 路径权限或文件监听行为。

## Verification

- 构建镜像：`docker compose build`。
- 启动服务：`docker compose up -d`。
- 本机检查：`curl http://localhost:8000` 返回前端入口。
- 容器检查：`docker compose ps` 显示端口为 `127.0.0.1:8000->8000/tcp`。
- 重启服务后检查 `.sessions` 命名卷中的会话仍存在。

## Non-goals

- 不提供局域网或公网访问。
- 不增加 TLS、反向代理、登录鉴权或数据库服务。
- 不改变现有 FastAPI、Runtime、Session、LLM 或 WebSocket 协议。
