# Docker Hot Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Docker-only development workflow in which frontend and backend source changes are reflected by refreshing `http://localhost:8000`.

**Architecture:** The development Compose file runs Vite as the browser-facing service and Uvicorn with `--reload` as an internal backend service. Bind mounts deliver source changes; Vite proxies HTTP and WebSocket traffic to the backend service, while existing production Compose remains unchanged.

**Tech Stack:** Docker Compose, Node.js 22 Alpine, Vite, Python 3.12 Slim, Uvicorn reload, FastAPI.

## Global Constraints

- Publish only `127.0.0.1:8000:5173` from the frontend service.
- Do not publish the backend service to Windows or change existing API/WebSocket protocols.
- Keep `compose.yaml` as the production-style deployment entrypoint.
- Preserve sessions in a named volume and keep `settings.local.json` out of images.
- Use polling for Vite file watching because Docker Desktop bind mounts may not propagate filesystem events.

---

### Task 1: Add reloadable backend and development image stage

**Files:**
- Create: `Turning-Good-Agent/web/backend/dev.py`
- Modify: `Dockerfile`
- Test: Uvicorn factory import inside the development image

**Interfaces:**
- Produces `create_development_app() -> FastAPI` for `uvicorn Turning-Good-Agent.web.backend.dev:create_development_app --factory --reload`.

- [ ] **Step 1: Verify the development app factory is absent**

Run `docker build --target development -t turning-good-agent:dev .`.

Run `docker run --rm --entrypoint python turning-good-agent:dev -c "from importlib import import_module; print(hasattr(import_module('Turning-Good-Agent.web.backend.dev'), 'create_development_app'))"`.

Expected: FAIL because the `dev` stage and module do not exist.

- [ ] **Step 2: Add the factory without duplicating application setup**

Create `dev.py` that loads `Settings`, calls existing `build_llm(settings)`, creates `AgentRuntime.create_default(...)`, and returns existing `create_app(settings, runtime)` from `web.backend.app`.

```python
def create_development_app():
    settings = Settings.load()
    runtime = AgentRuntime.create_default(settings, build_llm(settings))
    return create_app(settings, runtime)
```

- [ ] **Step 3: Split the Dockerfile stages**

Place Python dependency installation in `python-base`; add a `development` stage from it that copies `Turning-Good-Agent/`; retain a final `runtime` stage that copies the Python source and Vite build output. The final stage must remain `runtime`, so ordinary `docker build` continues producing the production image.

- [ ] **Step 4: Verify factory import**

Run `docker build --target development -t turning-good-agent:dev .`.

Run `docker run --rm --entrypoint python turning-good-agent:dev -c "from importlib import import_module; print(hasattr(import_module('Turning-Good-Agent.web.backend.dev'), 'create_development_app'))"`.

Expected: both commands PASS and the second prints `True`.

### Task 2: Add Vite proxy configuration and development Compose services

**Files:**
- Modify: `web/frontend/vite.config.ts`
- Create: `compose.dev.yaml`
- Test: resolved Compose configuration and proxied browser request

**Interfaces:**
- Consumes `VITE_BACKEND_URL=http://backend:8000` in Docker; defaults to existing `http://127.0.0.1:8000` outside Docker.
- Produces frontend service at host `127.0.0.1:8000`, backend service on internal `backend:8000`, and named `sessions` plus `frontend_node_modules` volumes.

- [ ] **Step 1: Verify Vite currently hard-codes its proxy**

Run `rg -n '127\.0\.0\.1:8000' web/frontend/vite.config.ts`.

Expected: it finds both API and WebSocket proxy literals.

- [ ] **Step 2: Make the proxy target environment-aware**

Use `process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000"` for `/api`, derive the WebSocket target by replacing the leading `http` with `ws`, and configure `server.watch.usePolling` from `CHOKIDAR_USEPOLLING`.

- [ ] **Step 3: Add `compose.dev.yaml`**

Define `backend` with build target `development`, source/settings/session mounts, and command:

```text
uvicorn Turning-Good-Agent.web.backend.dev:create_development_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir /app/Turning-Good-Agent
```

Define `frontend` from `node:22-alpine`, mount `web/frontend` and named `frontend_node_modules`, set `VITE_BACKEND_URL=http://backend:8000` and `CHOKIDAR_USEPOLLING=true`, publish `127.0.0.1:8000:5173`, and run `npm ci && npm run dev -- --host 0.0.0.0`. Do not add a host port to `backend`.

- [ ] **Step 4: Validate the resolved boundaries**

Run `docker compose -f compose.dev.yaml config`.

Expected: only `frontend` has host IP `127.0.0.1`; `backend` has no `ports` entry; both named volumes are present.

### Task 3: Document and verify refresh behavior

**Files:**
- Modify: `README.md`
- Test: temporary source markers observed through Vite reload and Uvicorn restart logs

**Interfaces:**
- Consumes the development Compose service from Task 2.
- Produces documented commands for starting and stopping development mode.

- [ ] **Step 1: Add development-mode instructions**

Document `docker compose -f compose.dev.yaml up --build`, `http://localhost:8000`, log commands, normal code-change behavior, and the four change categories that require restarting with `--build`: `package.json`, `package-lock.json`, Python dependencies, and Dockerfile.

- [ ] **Step 2: Verify the running services**

With a temporary nonempty `settings.local.json`, run `docker compose -f compose.dev.yaml up --build -d` then `Invoke-WebRequest http://localhost:8000 -UseBasicParsing`.

Expected: HTTP returns `200`; Vite logs show readiness; Uvicorn logs show reload supervision.

- [ ] **Step 3: Verify file watching**

Append and remove a harmless marker in `web/frontend/src/styles.css`, then inspect frontend logs for Vite update activity. Touch `Turning-Good-Agent/web/backend/dev.py`, then inspect backend logs for Uvicorn reload activity.

Expected: both services report their respective update/reload without rebuilding the image.

- [ ] **Step 4: Clean temporary validation state**

Run `docker compose -f compose.dev.yaml down` and remove the temporary settings file only if it did not exist before validation. Keep named volumes intact.

## Plan Self-Review

- Task 1 creates the Uvicorn-compatible factory and a development image without changing production defaults.
- Task 2 covers proxying, WebSocket routing, source mounts, polling, loopback exposure, and volume isolation.
- Task 3 covers user instructions and confirms both frontend and backend refresh paths.
