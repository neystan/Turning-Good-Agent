# 启动流程

项目有三种启动方式。三者都占用 8000 端口，同一时间只能运行一种。

| 方式 | 适合 | 访问地址 | 改代码后 |
| --- | --- | --- | --- |
| 一、本地 Gateway | 日常使用 | http://localhost:8000 | 后端要重启，前端要重新构建 |
| 二、本地开发模式 | 改代码 | http://127.0.0.1:5173 | 自动生效 |
| 三、Docker | 长期常驻、需要隔离 | http://localhost:8000 | 要重新构建镜像 |

本地方式（一、二）的命令都在项目根目录 `X:\download\claude\workspace\TGA` 的 PowerShell 中执行。程序从当前目录读取 `settings.local.json`，换了目录会找不到配置。

## 前置：配置文件

本地方式使用项目根目录的 `settings.local.json`。没有的话从模板复制：

```powershell
Copy-Item settings.example.json settings.local.json
```

必须填写 `llm` 段的 `api_key`、`base_url`、`model`。常用的可选项：

- `llm.supports_vision`：模型支持看图时设为 `true`，图片附件才可用。
- `llm.streaming_enabled`：设为 `true` 开启逐字输出。
- `proactive.timezone`：定时任务使用的时区，美国东部写 `America/New_York`。
- `multi_agent.enabled`：设为 `true` 开启多智能体。

## 一、本地 Gateway（日常使用）

```powershell
.\.venv\Scripts\python.exe -m Turning-Good-Agent gateway
```

浏览器打开 http://localhost:8000 。停止时在该终端按 `Ctrl+C`。

需要命令行对话时，另开一个终端：

```powershell
.\.venv\Scripts\python.exe -m Turning-Good-Agent chat --session main
```

改了代码之后：

- 后端：`Ctrl+C` 停掉再重新启动。
- 前端：重新构建后刷新页面。

```powershell
cd web\frontend
npm run build
```

## 二、本地开发模式（改代码）

先停掉方式一的 Gateway，然后开两个终端。

终端 1，后端，改 Python 代码后自动重启：

```powershell
.\.venv\Scripts\python.exe -m uvicorn Turning-Good-Agent.web.backend.dev:create_development_app --factory --host 127.0.0.1 --port 8000 --reload --reload-dir Turning-Good-Agent
```

终端 2，前端，保存后浏览器立刻热更新：

```powershell
cd web\frontend
npm run dev
```

浏览器打开终端 2 显示的地址，通常是 http://127.0.0.1:5173 。前端开发服务器会把 `/api` 和 `/ws` 请求转发给 8000 端口的后端。

只改后端时可以不开终端 2，照常访问 http://localhost:8000 ，前端用的是已构建好的那份。

后端这条命令是照 `compose.dev.yaml` 的写法换成本地路径的，尚未在本机实际验证。

## 三、Docker

需要先启动 Docker Desktop。

### 启动已有的容器

机器上已有 2026-08-24 构建的镜像和容器，代码与当前 `main` 一致，无需下载：

```powershell
docker start turning-good-agent-turning-good-agent-1
docker stop turning-good-agent-turning-good-agent-1
docker logs -f turning-good-agent-turning-good-agent-1
```

这个容器的特点：

- 配置读取的是 `D:\桌面\Turning-Good-Agent-main\settings.local.json`，不是本工作区的那份。
- 会话保存在数据卷 `turning-good-agent_sessions` 中，里面有 8 月以来的历史会话。
- 重启策略为 `unless-stopped`：启动后，每次打开 Docker Desktop 它都会自动运行，直到手动 `docker stop`。

### 从本工作区重新构建

代码有改动、需要更新镜像时使用。此流程尚未在本工作区实际执行过。

```powershell
New-Item -ItemType Directory -Force .skills
docker compose -p turning-good-agent up --build -d
```

- `Dockerfile` 会复制 `.skills/` 目录，而它被 `.gitignore` 排除，新 clone 里不存在，所以要先建一个空目录，否则构建失败。
- `-p turning-good-agent` 让新容器沿用原有的会话数据卷。不加的话项目名会变成 `tga`，得到一个空的新数据卷。
- 重建后容器改为读取本工作区的 `settings.local.json`。

停止并移除容器：

```powershell
docker compose -p turning-good-agent down
```

不要加 `-v`，那会删除会话数据卷。

## 数据存放位置

- 本地方式：项目根目录的 `.sessions/`。
- Docker：数据卷 `turning-good-agent_sessions`。

两者互相独立，会话不互通。

## 重建本地环境

`.venv` 或 `node_modules` 被删除后，按下面的命令恢复：

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
cd web\frontend
npm ci
npm run build
```

README 中的 `python -m pip install -e .` 在本仓库会失败，并且会把依赖装进系统 Python，不要使用。
