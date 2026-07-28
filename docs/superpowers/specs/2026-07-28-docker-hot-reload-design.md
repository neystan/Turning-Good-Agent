# Docker Development Hot Reload Design

## Goal

提供与稳定部署配置分离的 Docker 开发模式：修改 React、CSS、TypeScript 或 Python 源码后，无需重新构建镜像，只需刷新浏览器即可使用最新版本。

## Architecture

`compose.dev.yaml` 定义两个服务。`frontend` 运行 Vite 开发服务器并发布到 `127.0.0.1:8000`；它将 `/api` 和 `/ws` 代理给 Docker 内部的 `backend` 服务。`backend` 使用 Uvicorn factory 与 `--reload`，监听 Docker 内部端口 `8000`，不直接暴露给 Windows。

两个服务都通过绑定挂载读取本地源码。前端容器使用命名卷保存 `node_modules`，避免 Windows 文件系统挂载影响依赖目录；后端服务继续使用命名卷持久化 `.sessions`，并挂载用户的 `settings.local.json`。

## Components

| File | Responsibility |
| --- | --- |
| `Dockerfile` | 拆分共享 Python 依赖层、生产运行阶段与不构建前端的开发阶段。 |
| `compose.dev.yaml` | 定义 Vite、重载后端、源码挂载、命名卷、回环端口和内部网络。 |
| `Turning-Good-Agent/web/backend/dev.py` | 提供给 Uvicorn `--factory` 使用的应用工厂，创建 Settings、LLM、Runtime 和 FastAPI app。 |
| `web/frontend/vite.config.ts` | 从环境变量读取开发代理目标，默认保持现有本机开发行为。 |
| `README.md` | 说明开发启动、实时更新范围和需要重新构建的依赖变更。 |

## Data Flow

```text
Browser http://localhost:8000
  -> frontend (Vite, host port 8000 -> container port 5173)
  -> /api and /ws proxy
  -> backend (Uvicorn reload, Docker internal port 8000)
  -> Runtime / session named volume / settings.local.json
```

## Runtime Behavior

1. 用户执行 `docker compose -f compose.dev.yaml up --build`。
2. Vite 监听前端源码变化，并在浏览器中自动刷新或由用户手动刷新加载新资源。
3. Uvicorn 检测 Python 文件变化后重启后端进程；浏览器刷新后建立新的 HTTP/WebSocket 连接。
4. `package.json`、`package-lock.json`、Python 依赖或 Dockerfile 改变时，用户重新运行带 `--build` 的启动命令。
5. `docker compose down` 不删除 `.sessions` 或前端 `node_modules` 命名卷；`down -v` 会删除它们。

## Constraints

- 所有 Windows 暴露端口都必须限制为 `127.0.0.1`。
- `compose.yaml` 继续是稳定部署入口，不包含开发服务器、源码绑定挂载或自动重载。
- 不改变现有 FastAPI API、WebSocket 协议、Session 格式或 LLM 配置格式。
- 不将 API Key、会话数据或本地依赖目录复制进开发镜像。

## Verification

- `docker compose -f compose.dev.yaml config` 显示前端回环端口和未发布的后端端口。
- 开发服务启动后，`http://localhost:8000` 返回 Vite 页面，`/api/sessions` 经代理可达。
- 修改一个前端源码文件后，Vite 日志显示更新；修改 Python 文件后，后端日志显示 reload。
- 停止再启动开发服务后，`.sessions` 和前端 `node_modules` 命名卷仍存在。
