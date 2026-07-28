# Local Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Turning Good Agent in Docker Desktop on Windows at `http://localhost:8000` only.

**Architecture:** Node builds the current Vite frontend in a Docker build stage. A slim Python image holds the application and static bundle; Compose publishes the container port only to loopback, persists sessions in a named volume, and bind-mounts local LLM configuration.

**Tech Stack:** Docker Desktop WSL 2 backend, Node.js 22 Alpine, Python 3.12 Slim, npm, FastAPI, Uvicorn, Docker Compose.

## Global Constraints

- Publish only `127.0.0.1:8000:8000`; do not enable LAN access.
- Build `web/frontend` with `npm ci` and copy `web/static` into the final image.
- Install the five runtime dependencies declared in `pyproject.toml`.
- Exclude `.sessions`, `settings.local.json`, Git metadata, and frontend dependencies from the image context.
- Mount a named Docker volume at `/app/.sessions`.
- Do not modify application routes, WebSocket behavior, FastAPI static mounting, or Agent Runtime code.

---

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `Dockerfile` | Create | Build static frontend and run FastAPI in a Python image. |
| `.dockerignore` | Create | Prevent local data, credentials, and generated files entering the context. |
| `compose.yaml` | Create | Loopback port, settings mount, session volume, restart policy. |
| `README.md` | Modify | Docker Desktop setup and lifecycle instructions. |

### Task 1: Build a runtime image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Test: `docker build -t turning-good-agent:local .`

**Interfaces:**
- Consumes: frontend lockfile/source and the `Turning-Good-Agent/` Python source.
- Produces: an image with the command `python -m Turning-Good-Agent web --host 0.0.0.0 --port 8000`.

- [ ] **Step 1: Check the missing image recipe fails**

Run `docker build -t turning-good-agent:local .`.

Expected: FAIL because the root has no `Dockerfile`.

- [ ] **Step 2: Create the build-context exclusions**

Create `.dockerignore`:

```text
.git
.sessions
settings.local.json
__pycache__
**/__pycache__
*.pyc
.pytest_cache
web/frontend/node_modules
web/frontend/*.tsbuildinfo
web/static
```

- [ ] **Step 3: Create the multi-stage image recipe**

Create `Dockerfile`:

```dockerfile
FROM node:22-alpine AS frontend-build
WORKDIR /build/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "openai>=1.0.0" "tiktoken>=0.7.0" "mcp>=1.26.0,<2.0.0" "fastapi>=0.115.0" "uvicorn>=0.30.0"
COPY Turning-Good-Agent/ ./Turning-Good-Agent/
COPY --from=frontend-build /build/web/static ./web/static/
EXPOSE 8000
CMD ["python", "-m", "Turning-Good-Agent", "web", "--host", "0.0.0.0", "--port", "8000"]
```

Do not copy `settings.example.json`; Compose supplies the real local configuration at runtime.

- [ ] **Step 4: Build and inspect the image**

Run `docker build -t turning-good-agent:local .`.

Expected: PASS after the Vite static bundle is copied to `/app/web/static`.

Run `docker image inspect turning-good-agent:local --format '{{json .Config.Cmd}}'`.

Expected: the command includes `0.0.0.0` and `8000`.

- [ ] **Step 5: Commit the build files**

```bash
git add Dockerfile .dockerignore
git commit -m "build: add local Docker image"
```

### Task 2: Compose the local-only service

**Files:**
- Create: `compose.yaml`
- Test: `docker compose config` and a localhost HTTP request

**Interfaces:**
- Consumes: Task 1 image and a root-level `settings.local.json` created by the user.
- Produces: `turning-good-agent` on `127.0.0.1:8000` with named volume `sessions` at `/app/.sessions`.

- [ ] **Step 1: Check missing Compose configuration**

Run `docker compose config`.

Expected: FAIL because the root has no Compose file.

- [ ] **Step 2: Create the Compose service**

Create `compose.yaml`:

```yaml
services:
  turning-good-agent:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - sessions:/app/.sessions
      - ./settings.local.json:/app/settings.local.json
    restart: unless-stopped

volumes:
  sessions:
```

The configuration mount remains writable because the existing Web UI writes `tool_permissions.auto_approve_tools` to `settings.local.json`.

- [ ] **Step 3: Validate the resolved port and volume**

Run `docker compose config`.

Expected: PASS; output contains host IP `127.0.0.1` and named volume `sessions`.

- [ ] **Step 4: Run the service locally**

Create configuration with `Copy-Item settings.example.json settings.local.json`, then set valid `llm.api_key` and `llm.model` values.

Run `docker compose up --build -d`.

Run `docker compose ps`.

Expected: the service lists `127.0.0.1:8000->8000/tcp`.

Run `Invoke-WebRequest http://localhost:8000 -UseBasicParsing`.

Expected: PASS with the SPA HTML document.

- [ ] **Step 5: Verify session persistence**

Create a session in the Web UI, run `docker compose restart`, then run `docker compose exec turning-good-agent sh -c "find /app/.sessions -name session.json -print -quit"`.

Expected: the command prints a persisted `session.json` path.

- [ ] **Step 6: Commit the Compose file**

```bash
git add compose.yaml
git commit -m "build: add local Docker Compose service"
```

### Task 3: Document Docker Desktop use

**Files:**
- Modify: `README.md`
- Test: documented Compose lifecycle commands

**Interfaces:**
- Consumes: Task 1 image and Task 2 service.
- Produces: Chinese Docker setup and cleanup guidance matching the actual configuration.

- [ ] **Step 1: Add the usage documentation**

Add `## Docker 本机部署` after `## 运行` in `README.md`, including:

```powershell
Copy-Item settings.example.json settings.local.json
docker compose up --build -d
Start-Process http://localhost:8000
docker compose logs -f
docker compose down
```

Explain that Docker Desktop must use WSL 2; the user must configure `llm.api_key` and `llm.model`; only the local Windows machine can access the service; sessions survive `docker compose down`; and `docker compose down -v` permanently removes them.

- [ ] **Step 2: Verify the documented lifecycle**

Run `docker compose down`, `docker compose up -d`, `Invoke-WebRequest http://localhost:8000 -UseBasicParsing`, and `docker compose down`.

Expected: HTTP returns `200`; the final `down` stops the service but leaves its named volume intact.

- [ ] **Step 3: Commit the documentation**

```bash
git add README.md
git commit -m "docs: explain local Docker deployment"
```

## Plan Self-Review

- Task 1 covers the required multi-stage frontend and Python build, with no runtime Node.js dependency.
- Task 2 covers loopback-only exposure, writable local configuration, named-volume persistence, and restart verification.
- Task 3 covers prerequisite, configuration, lifecycle, persistence, and destructive-cleanup guidance.
- Container address `0.0.0.0:8000`, host address `127.0.0.1:8000`, `/app/web/static`, and `/app/.sessions` are consistent with the existing CLI, FastAPI, and settings behavior.
