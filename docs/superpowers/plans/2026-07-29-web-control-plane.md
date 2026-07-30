# Web Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Web control plane defined in 2026-07-29-web-control-plane-design.md: field-level configuration application, globally idle Runtime replacement, Command Catalog, and read-only Context, Tool and MCP APIs.

**Architecture:** `config/settings.py` parses settings and `config/validate.py` owns all validation rules. Web-only configuration application lives in web/backend/config_control.py; Runtime lifecycle is centralized in web/backend/runtime_supervisor.py, which waits for coordinator-wide idleness and atomically replaces the Runtime. Web read models are built in web/backend/read_models.py from active Runtime managers and existing Session files; they never become a second persistence system.

**Tech Stack:** Python 3.11+, asyncio, FastAPI, OpenAI Python SDK, existing JSON/JSONL Session store, pytest.

## Global Constraints

- No new database, JSONL file, WebSocket command action, cloud dependency, authentication or frontend visual implementation.
- Existing Session files remain the only persisted message, trace, token and Tool record source.
- Never return, log or snapshot API Keys, MCP headers or MCP env.
- Editable fields are limited to llm, runtime, memory, sessions.retention_days, Skill limits and Tool permissions.
- MCP and deployment or identity fields are read-only.
- A Runtime is swapped only when every Web turn is terminal.
- Context and Tool panel actions never call message.send and never enter the Runtime Slash command state.
- Do not commit during execution; the user owns Git history.

---

### Task 1: Create centralized configuration validation and Web control service

**Files:**
- Create: Turning-Good-Agent/config/validate.py
- Create: Turning-Good-Agent/llm/factory.py
- Create: Turning-Good-Agent/web/backend/config_control.py
- Modify: Turning-Good-Agent/config/settings.py
- Modify: Turning-Good-Agent/cli.py
- Create: tests/test_config_control.py

**Interfaces:**
- WebConfigControlService(config_path, tool_names) exposes read_desired(), apply(request) and candidate_llm(request).
- RuntimeSupervisor.configuration_view() composes desired_revision, active_revision, state, last_apply_error, desired and active from WebConfigControlService and the active Runtime.
- config.validate.validate_settings(settings, *, available_tool_names, unavailable_approval_names) is the only configuration-rule entrypoint and raises SettingsValidationError with field_errors as a dotted-path map.
- ConfigValidationError is the control-plane transport wrapper for SettingsValidationError and preserves its field_errors map.
- llm.factory.build_llm(settings) is the sole Provider construction entrypoint for CLI and Web.
- Settings.load() invokes config.validate for static rules; WebConfigControlService and Runtime bootstrap provide dynamic Tool-name context to the same validator.

- [ ] **Step 1: Write failing configuration service tests**

Create tests/test_config_control.py using a temporary settings.local.json and a tool callback returning write_file, edit_file, exec, write_stdin and web_fetch.

~~~python
def test_apply_merges_scalars_and_approval_members(tmp_path):
    service = service_for(tmp_path)
    view = service.apply(
        ConfigApplyRequest(
            changes={"runtime": {"max_tool_rounds": 6}},
            approval_required_tools=ApprovalToolChanges(add=["web_fetch"], remove=["exec"]),
        )
    )
    payload = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert payload["runtime"]["max_tool_rounds"] == 6
    assert "web_fetch" in payload["tool_permissions"]["approval_required_tools"]
    assert "exec" not in payload["tool_permissions"]["approval_required_tools"]
    assert view.revision.startswith("sha256:")

def test_apply_rejects_entire_invalid_candidate(tmp_path):
    service = service_for(tmp_path)
    before = (tmp_path / "settings.local.json").read_text(encoding="utf-8")
    with pytest.raises(ConfigValidationError) as raised:
        service.apply(ConfigApplyRequest(changes={"skills": {
            "max_skill_tokens": 9000, "max_loaded_skill_tokens_per_turn": 8000
        }}))
    assert "skills.max_loaded_skill_tokens_per_turn" in raised.value.field_errors
    assert (tmp_path / "settings.local.json").read_text(encoding="utf-8") == before

def test_read_redacts_and_apply_replaces_key(tmp_path):
    service = service_for(tmp_path)
    view = service.apply(ConfigApplyRequest(changes={"llm": {"api_key": "replacement-key"}}))
    assert view.desired["llm"]["api_key_configured"] is True
    assert "api_key" not in view.desired["llm"]
    assert "replacement-key" not in json.dumps(view.desired, ensure_ascii=False)

def test_validation_reports_dotted_fields_for_cli_and_web(tmp_path):
    service = service_for(tmp_path)
    with pytest.raises(ConfigValidationError) as raised:
        service.apply(ConfigApplyRequest(changes={"memory": {
            "compact_token_threshold": 1000,
            "recent_window_token_limit": 1001,
        }}))
    assert raised.value.field_errors == {
        "memory.recent_window_token_limit": "不能大于 compact_token_threshold"
    }
~~~

- [ ] **Step 2: Run the focused test**

Run: pytest tests/test_config_control.py -q

Expected: failure because web.backend.config_control, config.validate and their request types do not yet exist.

- [ ] **Step 3: Create centralized configuration validation**

Create `config/validate.py` with `SettingsValidationError(ValueError)` and `validate_settings`. Move all Settings value, type, range and cross-field rules from `settings.py` into this module, including Web local-host restrictions, MCP and Skill numeric limits, Runtime and Memory limits, LLM retry/timeout limits, `recent_window_token_limit <= compact_token_threshold`, and Tool approval membership rules. The function receives known native/canonical MCP Tool names and unavailable persisted MCP names instead of importing Runtime classes.

- [ ] **Step 4: Make Settings loading call the centralized validator**

Reduce `config/settings.py` to parsing, defaults and type conversion. Call `validate_settings` just before `Settings.load` returns for static configuration rules. The CLI and WebConfigControlService pass their available Tool-name context to the same validator before Runtime creation or atomic replacement; do not retain independent validators in `_load_web_settings`, `_load_mcp_settings` or Runtime Hooks.

~~~python
raise SettingsValidationError({
    "memory.recent_window_token_limit": "不能大于 compact_token_threshold"
})
~~~

- [ ] **Step 5: Move Provider construction into llm/factory.py**

Copy current build_llm behavior from cli.py into llm/factory.py. It must accept only openai-compatible, require Key and model, and return OpenAICompatibleLLM. Change cli.py to import it and remove the duplicate constructor. The Web supervisor must not import CLI code.

- [ ] **Step 6: Render the shared validation error at each boundary**

In `cli.py`, catch `SettingsValidationError` before starting a Runtime, print `配置错误：` followed by one `field: message` line per dotted field to stderr, and return a nonzero process exit without a traceback. In FastAPI, map `ConfigValidationError.field_errors` to the documented HTTP 422 body. A valid but unreachable Provider remains a sanitized HTTP 502 only in the explicit LLM test endpoint.

- [ ] **Step 7: Implement WebConfigControlService**

Implement the following exact behavior.

1. Read raw JSON from the target file; a missing file is an empty object.
2. Accept only the approved editable nested scalar maps.
3. Treat llm.api_key as write-only: omission preserves it, a non-empty value replaces it, and clear_api_key=true removes it. Reject empty Key and Key-plus-clear.
4. Apply Tool approval add/remove against the current list; reject duplicates and intersections. Adds must be in the live Tool Catalog; removes may target an unavailable persisted name.
5. Serialize the candidate to a temporary sibling file, parse it through Settings.load(local_config_path=temp_path), then call config.validate with live native/canonical MCP names plus unavailable persisted names before atomically replacing settings.local.json only after success.
6. Compute revision as sha256 plus SHA-256 of canonical UTF-8 JSON.
7. Return only redacted editable maps; provider is read-only and API Key becomes api_key_configured.

- [ ] **Step 8: Run focused validation**

Run:

~~~powershell
pytest tests/test_config_control.py -q
python -m compileall Turning-Good-Agent/config Turning-Good-Agent/llm
~~~

Expected: test passes and compileall has no errors.

---

### Task 2: Centralize Web Runtime replacement in RuntimeSupervisor

**Files:**
- Create: Turning-Good-Agent/web/backend/runtime_supervisor.py
- Modify: Turning-Good-Agent/web/backend/coordinator.py
- Modify: Turning-Good-Agent/web/backend/app.py
- Create: tests/test_runtime_supervisor.py

**Interfaces:**
- RuntimeSupervisor(initial_runtime, runtime_factory, config_service, idle_probe) exposes async start(), request_reload(revision), acquire_runtime(), notify_idle(), status(), configuration_view() and close().
- WebSessionCoordinator exposes is_globally_idle(), set_runtime_supervisor(supervisor) and activate_runtime(runtime).
- Coordinator obtains the Runtime through await supervisor.acquire_runtime immediately before runtime.run_turn.

- [ ] **Step 1: Write failing supervisor tests**

Create tests/test_runtime_supervisor.py with a fake Runtime counting async start and close calls.

~~~python
@pytest.mark.asyncio
async def test_reload_waits_for_idle_then_replaces_runtime():
    old, replacement = FakeRuntime(), FakeRuntime()
    supervisor = RuntimeSupervisor(old, factory_returning(replacement), idle_probe=lambda: False)
    await supervisor.request_reload("sha256:new")
    assert supervisor.status().state == "pending"
    await supervisor.notify_idle()
    assert supervisor.current_runtime is replacement
    assert old.close_calls == 1
    assert replacement.start_calls == 1

@pytest.mark.asyncio
async def test_failed_replacement_keeps_old_runtime_active():
    old = FakeRuntime()
    supervisor = RuntimeSupervisor(old, factory_raising(RuntimeError("bad config")), idle_probe=lambda: True)
    await supervisor.request_reload("sha256:bad")
    assert supervisor.current_runtime is old
    assert supervisor.status().state == "failed"
    assert supervisor.status().last_apply_error == "bad config"
~~~

- [ ] **Step 2: Run the focused test**

Run: pytest tests/test_runtime_supervisor.py -q

Expected: failure because RuntimeSupervisor does not exist.

- [ ] **Step 3: Implement the supervisor gate**

Use one async replacement lock and a readiness event.

1. request_reload records desired revision and reloads immediately only if idle; otherwise state becomes pending.
2. Reload loads fresh Settings, builds LLM through llm.factory.build_llm, creates AgentRuntime.create_default, registers the Web adapter factory on that replacement, then awaits runtime.start.
3. Publish the replacement Runtime to the coordinator only after all previous steps succeed, then close the old Runtime.
4. On any failure keep the old Runtime active, set failed with sanitized error and restore readiness.
5. acquire_runtime waits for readiness but never holds the replacement lock while a turn runs.

- [ ] **Step 4: Make coordinator idleness explicit**

Modify WebSessionCoordinator.

- is_globally_idle returns not self.controls; controls already cover queued, running, stopping and approval-waiting turns.
- activate_runtime publishes a replacement whose Web adapter factory was registered before `runtime.start()`; it must not itself start or close a Runtime.
- At terminal _finish, remove the control and await supervisor.notify_idle.
- In _run_message, acquire the existing concurrency slot, then await supervisor.acquire_runtime and run the message.
- Keep the constructor's initial Runtime only for startup compatibility; after `set_runtime_supervisor(supervisor)`, all turn execution reads the Runtime through the supervisor.

- [ ] **Step 5: Wire FastAPI lifecycle**

Create WebConfigControlService, RuntimeSupervisor and WebSessionCoordinator in create_app, and save all three on app.state. Lifespan must start the supervisor and coordinator, then close coordinator followed by supervisor. Remove direct Runtime start and close calls from FastAPI lifespan; supervisor is its sole Web lifecycle owner. Preserve `GET` and `PATCH /api/settings/ui` as the Composer-only immediate global auto-approval policy; it updates the active Runtime and local file without a reload, while Config Apply owns only per-Tool membership.

- [ ] **Step 6: Run supervisor and CLI validation**

Run:

~~~powershell
pytest tests/test_runtime_supervisor.py -q
python -m compileall Turning-Good-Agent/web/backend Turning-Good-Agent/runtime
"/exit" | python -m Turning-Good-Agent chat
~~~

Expected: supervisor tests pass, compilation succeeds and CLI still exits without creating a Web supervisor.

---

### Task 3: Add Command Catalog and Read Model services

**Files:**
- Create: Turning-Good-Agent/web/backend/read_models.py
- Modify: Turning-Good-Agent/web/backend/app.py
- Create: tests/test_web_read_models.py

**Interfaces:**
- build_command_catalog(runtime)
- build_tool_catalog(runtime, active_revision)
- build_context_read_model(runtime, session_id, active_revision)
- page_tool_calls(store, session_id, limit, cursor)
- build_mcp_server_list(runtime) and build_mcp_server_detail(runtime, name)

- [ ] **Step 1: Write failing Read Model tests**

Create tests/test_web_read_models.py with fake Skill and MCP catalogs plus temporary Session data.

~~~python
def test_command_catalog_has_only_supported_entries(runtime):
    entries = build_command_catalog(runtime)["entries"]
    ids = {item["id"] for item in entries}
    assert {"inspect.context", "inspect.tools", "skill.release-review", "mcp.connected"} <= ids
    assert "mcp.failed" not in ids
    assert all(item["slash"] not in {"/history", "/new", "/clear", "/exit", "/help"} for item in entries)

def test_tool_catalog_exposes_live_tools_and_retains_offline_rule(runtime):
    runtime.agent_loop.tools.register(FakeTool("exec", "执行受控命令"))
    runtime.mcp.statuses["offline"] = McpServerStatus(name="offline", state="failed")
    runtime.settings.tool_permissions.approval_required_tools = ["exec", "mcp_offline_run"]
    catalog = build_tool_catalog(runtime, "sha256:active")
    exec_entry = next(item for item in catalog["tools"] if item["name"] == "exec")
    assert exec_entry["effective_approval"] == "manual"
    assert catalog["unavailable_approval_required"] == ["mcp_offline_run"]
    assert all(item["source"].get("server_name") != "offline" for item in catalog["tools"])

def test_context_read_model_excludes_internal_material(runtime, session_id):
    model = build_context_read_model(runtime, session_id, "sha256:active")
    encoded = json.dumps(model, ensure_ascii=False)
    assert "SYSTEM_PROMPT" not in encoded
    assert "api_key" not in encoded
    assert model["uncompacted_history_count"] == len(model["uncompacted_messages"])

def test_tool_cursor_keeps_first_snapshot_stable(store, session_id):
    first = page_tool_calls(store, session_id, limit=1, cursor=None)
    append_later_tool_call(store, session_id)
    second = page_tool_calls(store, session_id, limit=1, cursor=first["next_cursor"])
    assert second["items"][0]["tool_call_id"] != "later-row"
~~~

- [ ] **Step 2: Run the focused test**

Run: pytest tests/test_web_read_models.py -q

Expected: failure because web.backend.read_models does not exist.

- [ ] **Step 3: Build backend-only Command Catalog**

Build the Catalog from active skills.list_skills(), mcp.statuses and mcp.catalogs.

- Emit only fixed inspect entries for /context and /tools.
- Emit valid Skills as id `skill.<name>`, kind skill, icon skill and insert_text using 请优先参考 Skill.
- Emit only completed live MCP connections as id `mcp.<name>`, kind mcp, icon mcp and insert_text using 请参考 MCP Server.
- Add source_label only on visible-name collisions.
- Do not provide Skill or MCP execution endpoints. Their selected text is editable browser text only.

- [ ] **Step 4: Build Tool Catalog, Context, Tool cursor and MCP views**

- Tool Catalog enumerates the active Tool Registry and returns only `name`, existing human-readable description, `source`, persisted `approval_required` and calculated `effective_approval`. Classify a Tool as `mcp` only when its active registered name belongs to a connected MCP Server; otherwise classify it as `core`.
- Return disconnected or otherwise unregistered persisted approval names only through `unavailable_approval_required`, never as editable Tools. Config apply may remove these entries but may add only names returned in `tools`.
- Preserve unavailable MCP approval entries through reload. Validate native names and canonical names for enabled MCP Tools from the loaded configuration before publishing a replacement Runtime; reject typos rather than treating every offline Server as an error.

- Context uses build_session_context and build_context_token_breakdown. It returns summary, counts, uncompacted message fields, breakdown, active max-context and revision. It never returns system prompt, profile text, Skill bodies, MCP attachments or schema bodies.
- Tool paging validates one through one hundred. The base64url JSON cursor contains snapshot_created_at, snapshot_tool_call_id, before_created_at and before_tool_call_id; records sort descending by created_at and tool_call_id, and later appended records never enter an existing snapshot.
- MCP views return only name, state, sanitized error, transport label, catalog names/descriptions/counts and enabled Tool names. They exclude command, args, cwd, URL query credentials, env and headers.

- [ ] **Step 5: Add REST routes without WebSocket changes**

Add these routes to app.py:

~~~text
GET  /api/control/config
POST /api/control/config/apply
POST /api/control/config/test-llm
GET  /api/control/commands
GET  /api/control/tools
GET  /api/control/sessions/{session_id}/context
GET  /api/control/sessions/{session_id}/tool-calls?limit=&cursor=
GET  /api/control/mcp/servers
GET  /api/control/mcp/servers/{name}
~~~

Map ConfigValidationError and stale Tool-Catalog additions to HTTP 422 with field_errors, missing Session or Server to 404 and sanitized Provider transport failures to 502. Keep existing session routes and every WebSocket action unchanged.

- [ ] **Step 6: Run focused validation**

Run:

~~~powershell
pytest tests/test_web_read_models.py -q
pytest tests/test_config_control.py tests/test_runtime_supervisor.py tests/test_web_read_models.py -q
python -m compileall Turning-Good-Agent/web/backend
git diff --check
~~~

Expected: focused tests pass, compilation succeeds and diff check has no output.

---

### Task 4: Synchronize project documentation and run complete validation

**Files:**
- Modify: docs/phases/2026-06-15-phase-6-web-observability.md
- Modify: docs/TURNING_GOOD_AGENT_SPEC.md
- Modify: docs/PROJECT_ARCHITECTURE.md
- Modify: README.md
- Test: tests/test_config_control.py
- Test: tests/test_runtime_supervisor.py
- Test: tests/test_web_read_models.py

**Interfaces:**
- Documents the routes and lifecycle from Tasks 1-3 without changing Runtime or frontend visual requirements.

- [ ] **Step 1: Update Phase 6 authority**

Add a Web Control Plane subsection with editable fields, centralized `config/validate.py` error behavior, secret redaction, browser-local unapplied values, atomic apply, global-idle Runtime swap, Command and Tool Catalog semantics, and Read Model persistence boundary.

- [ ] **Step 2: Update overall spec and architecture**

Document control-plane boundaries under Web in TURNING_GOOD_AGENT_SPEC.md. Add config/validate.py, llm/factory.py, web/backend/config_control.py, web/backend/runtime_supervisor.py and web/backend/read_models.py to PROJECT_ARCHITECTURE.md with one responsibility each. Do not claim profiles, MCP editing, new providers or WebSocket command events.

- [ ] **Step 3: Update README operational guidance**

Document editable configuration boundaries, no-secret behavior, global-idle application, explicit LLM connection test and file-administered host/port/storage/MCP boundaries.

- [ ] **Step 4: Execute complete validation**

Run:

~~~powershell
pytest -q
python -m compileall Turning-Good-Agent
"/exit" | python -m Turning-Good-Agent chat
git diff --check
~~~

Expected: full suite passes, Python compilation succeeds, CLI exits normally and diff check produces no output. Record unrelated failures exactly; do not repair unrelated code.

## Self-Review

### Spec coverage

- Field-level editing, centralized validation, redaction, atomic apply, Tool membership merge and no risk acknowledgement: Task 1.
- Candidate LLM test with no persistence/accounting: Tasks 1 and 3.
- Global idle desired/active revisions, replacement and failure preservation: Task 2.
- Latest Catalog, only connected MCP, Skill/MCP insertion and no CLI lifecycle commands: Task 3.
- Live Tool Catalog, Context privacy, persisted-only cursor paging and MCP redaction: Task 3.
- Documentation and complete validation: Task 4.

### Placeholder scan

This plan contains no TODO, TBD, implicit validation step or undefined cross-task interface.

### Type consistency

WebConfigControlService, RuntimeSupervisor, WebSessionCoordinator, build_command_catalog, build_tool_catalog, build_context_read_model and page_tool_calls use the same names throughout. The route list matches the companion design specification.
