# Task 9 Fix Round 2 Report: Web proactive Tool registration

## Baseline and root cause

- Baseline commit: `5c44b373d15ec4dfa612fa472b1eb78e4b9998bd`.
- The writable Web lifecycle created and started `ProactiveService`, but never called its existing `install_tools()` method. CLI did so before scheduler start. Consequently `/api/control/tools` omitted the interactive proactive tools even while the Web Host owned the lease.

## Changed files

- `Turning-Good-Agent/tools/registry.py`: adds identity-safe `unregister(tool)` cache-invalidating removal.
- `Turning-Good-Agent/proactive/service.py`: tracks only the concrete proactive Tool objects it successfully registered, and removes only those objects.
- `Turning-Good-Agent/web/backend/proactive_lifecycle.py`: installs before every Web service start, removes before every stop, and restores old tools before rollback restart.
- `tests/test_web_proactive_lifecycle.py`: covers the real service plus registry, writable/readonly/takeover lifecycle behavior, replacement/rollback ordering, and FastAPI lifespan `/api/control/tools` visibility.
- `docs/phases/2026-06-15-phase-7-proactive-memory.md`: records Web chat availability while owning the service and confirms the Slash catalog remains unchanged.

## TDD evidence

### RED

Command:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_web_proactive_lifecycle.py tests\test_proactive_service.py -q
```

Meaningful baseline result: `5 failed, 16 passed`. Failures showed that Web lifecycle events did not contain `install_tools`/`uninstall_tools`, the real writable Web registry was empty, replacement ordering lacked tool removal, and `ToolRegistry.unregister` did not exist.

### GREEN

Commands and results:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_web_proactive_lifecycle.py tests\test_proactive_service.py -q
# 22 passed, 1 warning in 1.58s

& .\.venv\Scripts\python.exe -m pytest tests\test_web_proactive_ownership.py tests\test_web_proactive_control.py -q
# 37 passed, 1 warning in 2.83s

& .\.venv\Scripts\python.exe -m pytest tests\test_web_proactive_lifecycle.py -q
# 17 passed, 1 warning in 1.39s
```

The warning is the existing Starlette `TestClient` deprecation warning for the installed `httpx` combination. The lifespan test verifies a writable Web Host returns both `run_breakbeat` and `create_cron` from `GET /api/control/tools` and removes the registered tools during shutdown.

`git diff --check` completed without whitespace errors before commit.

## Review findings

- Tool removal uses object identity, so a pre-existing or later-replaced non-proactive tool with the same name remains registered.
- No second Runtime or Scheduler was added; Web continues to use its existing `AgentRuntime` and lifecycle.
- No slash commands, deterministic REST authorization, proactive JSON layout, or `pending_deliveries.json` behavior changed.

## Commit and concerns

- Implementation commit: `572623f346720ab82105e18e3a1344a3f91d9eee` (`fix(web): register proactive tools for owner`).
- The user-owned `web/frontend/tests/workbench_real_page.spec.ts` remained modified and unstaged throughout.
- `tests/test_web_proactive_events.py` exceeded the 124-second command timeout with no result in this shared environment; its ownership/control neighbors passed. The timeout-created pytest children were stopped, while other agents' processes were left untouched.
