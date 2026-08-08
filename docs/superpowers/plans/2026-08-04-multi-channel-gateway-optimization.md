# Multi-Channel Gateway Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Plan status:** This is an execution plan, not a second source of product truth. Until each batch passes its acceptance gate, the authoritative contracts remain `docs/phases/2026-06-15-phase-6-web-observability.md`, `docs/phases/2026-06-15-phase-7-proactive-memory.md`, and `docs/phases/2026-06-15-phase-8-im-channel-adapters.md`.

**Goal:** Correct the Gateway's proactive execution and notification behavior, make CLI/Web channel behavior explicit and reliable, and simplify the Web proactive workspace without changing the single-Gateway Runtime-first architecture.

**Architecture:** Deliver three independently testable batches. Batch 1 changes only proactive execution, notifications, and foreground tool-progress projection. Batch 2 moves normal-message ordering into the Gateway, hardens the CLI/Web protocol, and removes the unused durable-delivery implementation. Batch 3 reuses the existing proactive REST and `/ws/proactive` read models to give each proactive domain a direct workspace entry and render short-lived, non-blocking notices.

**Tech Stack:** Python 3.11, FastAPI, asyncio, existing `AgentRuntime`/`AgentLoop`/`AsyncMessageBus`, React 18, TypeScript, Vite, Playwright, pytest.

## Global Constraints

- Keep one `GatewayHost`, one `AgentRuntime`, one `AgentLoop`, one inbound/outbound `AsyncMessageBus`, and one `ProactiveService` per `<data_dir>`.
- Keep ordinary chat isolated by `(principal_id, channel, conversation_id)`; proactive notifications must never enter `messages.jsonl`, summaries, chat context, model prompts, or chat WebSocket events.
- Do not create a state migration, delete existing data, import existing `pending_deliveries.json`, or automatically remove it. After Batch 2, runtime code must neither read nor write it.
- Reuse the current Session, MCP, Skill, Runtime, and proactive JSON snapshot boundaries. Do not add a database, a second scheduler, or an external Channel implementation.
- Background work may never inherit foreground "full access", prompt for HITL, or expose model reasoning. `ToolExecutor` remains the final security check.
- `tga chat` defaults to the human-readable conversation `default`; `--session <name>` only selects that CLI conversation.
- Web remains the only Channel with guidance. CLI and future Channels are server-declared non-guidance Channels, not client-controlled variants.
- Web chat uses only `/ws/web`; `/ws` is removed rather than retained as an alias. `/ws/cli` and `/ws/proactive` remain separate.
- Follow `DESIGN.md`: quiet dual-theme workbench, one primary work surface, background/space/shadow hierarchy rather than fine-border stacking, real-state motion only, predictable scrolling, and no reasoning display.
- Preserve every pre-existing user worktree change. Do not reset, overwrite, or clean unrelated files.

## Locked Decisions

| Topic | Execution decision | Why it is the narrowest safe change |
| --- | --- | --- |
| Cron Tool catalog | Snapshot the active Runtime catalog at each Cron trigger, then apply the existing background safety filter plus a dynamic set of ProactiveService-installed Tool names. | A newly registered safe Tool such as `weather` becomes usable without turning Cron into a privileged foreground turn. |
| Breakbeat retry | Persist `next_run_at = finished_at + breakbeat_refresh_minutes` after every completed run, including `partial` and `failed`. | It is a non-zero, persisted backoff with no new JSON field, failure counter, or restart-dependent exponential state. |
| Breakbeat result | Model every run as exactly `success`, `partial`, or `failed`; preserve successful batches and cursors, and retain only safe failure categories. | The user receives one meaningful outcome while the existing snapshot schema remains intact. |
| Breakbeat Incidents | Record failure/recovery state for Breakbeat without emitting a second Incident notification; the run outcome is the sole user-facing notification for that run. | Prevents the current failure plus recovery pair while keeping the health panel accurate. |
| No-op Breakbeat | A fully successful run with no created item remains silent. Created, partial, and failed runs publish one result notification. | Avoids periodic notification spam while ensuring errors are visible once. |
| Manual proactive Tools | A foreground `ToolResult` is always the current chat turn result. A best-effort `proactive_notification` is independent and cannot turn that ToolResult into an error. | Keeps chat semantics and the proactive workspace separate. |
| Route FIFO | The Gateway owns one pending queue per `(channel, conversation_id)`, dispatching the next message only after the prior route's terminal delivery releases its control. | Avoids duplicated Web/CLI queues and preserves the ChannelRouter's single active-control assumption. |
| Durable delivery | Remove Outbox and DeliveryWorker code. All currently supported notification targets are online immediate targets; unknown future Channels are skipped. | There is no implemented durable external Adapter whose platform acknowledgement could define reliable delivery correctly. |
| Proactive navigation | `#proactive` is an overview. Cron, Breakbeat, Memory/Dream, Skill evolution, and Incidents each get a direct sidebar entry and keep their existing deep links. | Removes the five-tab mixed workbench without rewriting the existing domain pages or read models. |

## File Map

### Batch 1 - Proactive execution correctness

| File | Change |
| --- | --- |
| `Turning-Good-Agent/proactive/cron.py` | Obtain a Tool-name snapshot at trigger time; remove the fixed empty whitelist; remove obsolete Outbox cleanup calls. |
| `Turning-Good-Agent/proactive/executor.py` | Apply the active catalog, configured approval list, Tool-level `approval_required`, and dynamically installed proactive Tool exclusions in one background-only filter. |
| `Turning-Good-Agent/proactive/breakbeat.py` | Introduce typed `success`/`partial`/`failed` outcomes, safe failure categories, retained successful batches, and non-zero schedule advancement after every run. |
| `Turning-Good-Agent/proactive/service.py` | Inject the active catalog policy, publish one Breakbeat outcome, update Breakbeat Incidents silently, and make manual notice publication best-effort. |
| `Turning-Good-Agent/proactive/incidents.py` | Add an explicit no-notify state-transition path while preserving normal Incident edge notifications for other domains. |
| `Turning-Good-Agent/hooks/channel_status.py` | Stop converting `run_dream`/`run_breakbeat` into special status strings; use regular Tool lifecycle callbacks. |
| `Turning-Good-Agent/channels/base.py`, `Turning-Good-Agent/channels/web.py`, `Turning-Good-Agent/channels/cli_gateway.py` | Carry a normalized Tool argument summary with `tool.started`; keep background `SilentChannelAdapter` silent. |
| `web/frontend/src/state/activity_steps.ts` | Render Tool start, completion, and failure as real activity steps, including `run_dream` and `run_breakbeat`. |
| `tests/test_proactive_cron.py`, `tests/test_proactive_executor.py`, `tests/test_proactive_breakbeat.py`, `tests/test_proactive_service.py`, `tests/test_proactive_incidents.py` | Add focused backend regression coverage. |
| `tests/test_gateway_web_coordinator.py`, `web/frontend/tests/observability_view.spec.ts` | Preserve proactive-notification/chat separation and foreground Tool activity projection. |

### Batch 2 - Gateway channel contract and delivery simplification

| File | Change |
| --- | --- |
| `Turning-Good-Agent/gateway/turns.py` | Own per-route queued normal turns, delayed Bus dispatch, terminal release, deduplication, and CLI-route pending-turn discard. |
| `Turning-Good-Agent/channels/base.py` | Add server-side `ChannelCapabilities`; make unsupported guidance rejectable by default. |
| `Turning-Good-Agent/web/backend/coordinator.py`, `Turning-Good-Agent/channels/cli_gateway.py` | Use Gateway route-queue callbacks instead of treating ordinary messages as guidance; keep Web guidance and remove CLI guidance. |
| `Turning-Good-Agent/cli.py` | Make `--session` optional with `default`, flatten CLI event consumption, show Tool lifecycle/argument summaries, and serialize terminal input/output through one UI event queue. |
| `Turning-Good-Agent/web/backend/app.py` | Move chat WebSocket to `/ws/web`; reject unsupported CLI `guidance.send`; retain high-priority Stop and approval actions. |
| `web/frontend/src/state/socket_client.ts` and related WebSocket tests/types | Connect only to `/ws/web` and retain event-cursor behavior. |
| `Turning-Good-Agent/channels/manager.py` | Remove durable receipt waiting while retaining one outbound consumer, exact-recipient delivery, and delivery listeners. |
| `Turning-Good-Agent/proactive/notifications.py` | Make `NotificationFanout` publish only current online recipients; remove durable plans, Outbox constructor dependencies, and source-ID outbox cleanup. |
| `Turning-Good-Agent/gateway/host.py` | Remove `DeliveryOutbox`, DeliveryWorker flush/loop/task lifecycle, and unknown-Channel durable classification. |
| `Turning-Good-Agent/proactive/delivery.py` | Delete after all imports and tests are removed. |
| `tests/test_gateway_turns.py`, `tests/test_gateway_web_injection.py`, `tests/test_gateway_web_session_scope.py` | Test route FIFO, cross-route concurrency, high-priority control actions, and `/ws/web` only. |
| `tests/test_cli_gateway_client.py`, `tests/test_cli_gateway_endpoint.py`, `tests/test_cli_gateway_transport.py`, `tests/test_cli_terminal_mode.py`, `tests/test_gateway_cli_e2e.py` | Test default conversation, flat protocol, Tool/HITL terminal behavior, no CLI guidance, and disconnect cleanup. |
| `tests/test_channel_manager.py`, `tests/test_notification_fanout.py`, `tests/test_gateway_host_notifications.py`, `tests/test_session_store_layout.py` | Cover online-only immediate Fanout and prove an existing `pending_deliveries.json` is untouched. |
| `tests/test_delivery_outbox.py`, `tests/test_proactive_delivery.py` | Delete with the removed Outbox implementation. |

### Batch 3 - Web proactive workspace and notices

| File | Change |
| --- | --- |
| `web/frontend/src/proactive_types.ts` | Add the overview route while retaining the five existing domain routes and wire-domain mapping. |
| `web/frontend/src/App.tsx` | Route `#proactive` to overview, track an active proactive workspace entry, and retain separate chat inspector state. |
| `web/frontend/src/components/SessionSidebar.tsx` | Replace the single proactive button with overview plus five direct domain entries; preserve collapsed-sidebar accessible names. |
| `web/frontend/src/components/ProactiveWorkspace.tsx` | Remove the tablist, render concise overview or one selected domain page, and reuse existing page components/read models. |
| `web/frontend/src/components/ProactiveOverview.tsx` | Create a compact status-and-navigation overview without duplicating the five detailed card pages. |
| `web/frontend/src/components/NoticeRegion.tsx` | Add severity-aware 4-second/7-second timers, hover/focus pause, click navigation, and non-focus-stealing behavior. |
| `web/frontend/src/styles/components/proactive.css`, `web/frontend/src/styles/components/sidebar.css`, `web/frontend/src/styles/components/overlays.css` | Implement direct-entry navigation and top-center, background-layered notifications in both themes. |
| `web/frontend/tests/proactive_workspace.spec.ts`, `web/frontend/tests/proactive_notifications.spec.ts`, `web/frontend/tests/workbench_visual.spec.ts`, `web/frontend/tests/desktop_surface_refinement.spec.ts` | Replace tab expectations with direct-entry/overview coverage and add notice timing, focus, navigation, and visual checks. |

---

## Batch 1: Proactive Execution Correctness

### Task 1: Define the dynamic Cron background Tool policy

**Files:**

- Modify: `Turning-Good-Agent/proactive/cron.py`
- Modify: `Turning-Good-Agent/proactive/executor.py`
- Modify: `Turning-Good-Agent/proactive/service.py`
- Test: `tests/test_proactive_cron.py`
- Test: `tests/test_proactive_executor.py`
- Test: `tests/test_proactive_service.py`

**Interfaces:**

- `CronManager` receives `available_tool_names: Callable[[], frozenset[str]]` from `ProactiveService`.
- `ProactiveService` returns a fresh copy of the active `runtime.agent_loop.tools.tool_names` and the Tool names owned by its `_installed_tools` collection.
- `ProactiveExecutor._safe_tool_names(requested)` remains the only background Tool-name intersection and excludes configured approval names, `approval_required=True`, static proactive control names, and dynamically installed proactive names.

- [ ] **Step 1: Write failing Cron policy tests.** Create a Runtime catalog containing `weather`, one configured approval Tool, one Tool with `approval_required=True`, and an installed proactive Tool. Trigger a Cron job and assert that only `weather` reaches the background AgentLoop.

- [ ] **Step 2: Assert the no-HITL invariant.** In the same test double, fail if the background adapter is asked for approval. Verify the executor calls `SilentChannelAdapter` and `auto_approve_tools=False` even when the foreground config permits full access.

- [ ] **Step 3: Implement trigger-time catalog capture.** In `CronManager._run_job`, call the injected catalog function immediately before `executor.run(...)`; pass the snapshot to the executor instead of `frozenset()`.

- [ ] **Step 4: Make proactive exclusions dynamic.** Preserve the explicit static control-name defense, add the live installed-name subtraction supplied by `ProactiveService`, and retain the existing ToolExecutor security path unchanged.

- [ ] **Step 5: Run focused tests.** Run `pytest tests/test_proactive_cron.py tests/test_proactive_executor.py tests/test_proactive_service.py -q` and confirm safe Tools run while approval-required and proactive Tools never reach the background loop.

### Task 2: Make every Breakbeat run have one safe outcome

**Files:**

- Modify: `Turning-Good-Agent/proactive/breakbeat.py`
- Modify: `Turning-Good-Agent/proactive/service.py`
- Modify: `Turning-Good-Agent/proactive/incidents.py`
- Test: `tests/test_proactive_breakbeat.py`
- Test: `tests/test_proactive_service.py`
- Test: `tests/test_proactive_incidents.py`

**Interfaces:**

```python
BreakbeatStatus = Literal["success", "partial", "failed"]
BreakbeatFailureCategory = Literal[
    "llm_call_failed", "invalid_json", "action_validation_failed",
]

@dataclass(frozen=True, slots=True)
class BreakbeatOutcome:
    status: BreakbeatStatus
    created: int
    successful_batches: int
    failure_categories: tuple[BreakbeatFailureCategory, ...]
    summary: str
```

- `IncidentMonitor.report_failure(..., notify: bool = True)` and `report_recovery(..., notify: bool = True)` retain current default behavior. Breakbeat calls them with `notify=False`.
- `next_run_at` is always set to `finished_at + breakbeat_refresh_minutes` after the manager completes its locked review, including partial and failed outcomes.

- [ ] **Step 1: Write failing outcome tests.** Cover all-success/no-change, all-success/created, an LLM failure, invalid JSON, invalid action payload, and a mixed run where one successful batch persists items/cursor before another batch fails.

- [ ] **Step 2: Classify failures at their boundary.** Map `ProactiveExecutionResult.success=False` to `llm_call_failed`, JSON decoding errors to `invalid_json`, and schema/action validation errors to `action_validation_failed`. Do not store exception text, model output, or reasoning in outcome summaries or Incidents.

- [ ] **Step 3: Preserve partial state and advance schedule once.** Continue eligible sessions after a failed batch, retain each already written item/cursor/usage update, compute status from successful versus failed batches, then write one `next_run_at` based on the completion clock.

- [ ] **Step 4: Publish exactly one user-facing Breakbeat result.** Send a completed result when items were created, a partial result for partial execution, and a failure result for failed execution. Suppress fully successful no-op runs. Update the Incident record silently so its `open`/`resolved` history remains correct without a second opened/resolved notification.

- [ ] **Step 5: Decouple manual ToolResult from notification errors.** Keep `RunBreakbeatTool` and `RunDreamTool` returning their foreground result after the domain run. Catch/log notification publication failures in the service's manual notification helper so no foreground turn is changed to an error.

- [ ] **Step 6: Run focused tests.** Run `pytest tests/test_proactive_breakbeat.py tests/test_proactive_service.py tests/test_proactive_incidents.py -q`; assert one published Breakbeat result per meaningful run, no immediate retry after a failed deadline, and no duplicate Incident notice.

### Task 3: Restore standard foreground Tool lifecycle events

**Files:**

- Modify: `Turning-Good-Agent/hooks/channel_status.py`
- Modify: `Turning-Good-Agent/channels/base.py`
- Modify: `Turning-Good-Agent/channels/web.py`
- Modify: `Turning-Good-Agent/channels/cli_gateway.py`
- Modify: `web/frontend/src/state/activity_steps.ts`
- Test: `tests/test_agent_loop.py`
- Test: `tests/test_gateway_web_coordinator.py`
- Test: `web/frontend/tests/observability_view.spec.ts`

**Interfaces:**

```python
class ChannelAdapter(Protocol):
    async def on_tool_started(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> None: ...
```

- All foreground Tools, including `run_dream` and `run_breakbeat`, emit `tool.started` followed by `tool.finished` with `tool_call_id`, `tool_name`, `args`, and `failed` where applicable.
- `SilentChannelAdapter` remains the background implementation and emits nothing.

- [ ] **Step 1: Write failing hook and projection tests.** Assert that Dream and Breakbeat no longer produce a special `task.status`, and that their Tool start and finish payloads contain the normalized name, argument object, and failure flag.

- [ ] **Step 2: Remove the proactive special branch.** Delete `_proactive_start_status` behavior from `ChannelStatusHook`; pass the original `ToolCall.args` to the standard adapter method.

- [ ] **Step 3: Keep the Web activity timeline stateful.** Map `tool.started` to a running Tool step and map `tool.finished` to the same Tool's done or failed state. Keep `latestActivityStep` neutral after finish and never emit model reasoning.

- [ ] **Step 4: Run focused tests.** Run `pytest tests/test_agent_loop.py tests/test_gateway_web_coordinator.py -q` and `pnpm exec playwright test tests/observability_view.spec.ts` from `web/frontend`; verify the foreground activity cluster shows `run_dream` and `run_breakbeat` by name.

### Batch 1 acceptance gate

- [ ] A scheduled `weather` Cron can call the Tool without a background approval prompt.
- [ ] Configured approval Tools, Tool-level approval Tools, and all active proactive Tools are absent from the Cron background Tool catalog.
- [ ] Breakbeat cannot zero-wait retry after a failure; a partial run retains successful state and emits one partial result.
- [ ] A manual Dream/Breakbeat ToolResult remains a chat-turn result even if its proactive notification publication fails.
- [ ] Foreground Dream/Breakbeat show only Tool lifecycle state, not reasoning.

---

## Batch 2: Gateway Channel Contract and Delivery Simplification

### Task 4: Centralize normal-message FIFO in the Gateway

**Files:**

- Modify: `Turning-Good-Agent/gateway/turns.py`
- Modify: `Turning-Good-Agent/web/backend/coordinator.py`
- Modify: `Turning-Good-Agent/channels/cli_gateway.py`
- Test: `tests/test_gateway_turns.py`
- Test: `tests/test_gateway_web_injection.py`
- Test: `tests/test_gateway_web_session_scope.py`
- Test: `tests/test_gateway_cli_e2e.py`

**Interfaces:**

```python
TurnDispatch = Callable[[InboundMessage], Awaitable[None]]

async def submit(self, message: InboundMessage, *, dispatch: TurnDispatch) -> bool: ...
async def complete_route_turn(self, route: ChannelRoute, request_id: str) -> None: ...
async def discard_pending_route(self, route: ChannelRoute) -> None: ...
```

- `GatewayTurnCoordinator` owns pending normal-message deques keyed by `(route.channel, route.conversation_id)`.
- A Channel coordinator creates its `WebTurnControl` or `CliTurnControl` only in its `dispatch` callback, immediately before the message enters the shared inbound Bus.
- `complete_route_turn` is invoked by the Channel delivery listener only after it clears the terminal control, then dispatches the next pending message for that route.

- [ ] **Step 1: Write failing ordering tests.** Submit two normal messages to one Web route and one to a distinct route. Assert the first same-route message enters the Bus first, the second same-route message does not enter until terminal delivery, and the other route can use an available global execution slot.

- [ ] **Step 2: Replace guidance-on-send with queue-on-send.** In Web and CLI coordinators, every `message.send` receives its own request ID and queued receipt. It must never append its text to `control.guidance`.

- [ ] **Step 3: Preserve high-priority controls.** Keep Stop and approval resolution directed to the active control without joining the normal-message deque. Web guidance remains direct to an active Web control.

- [ ] **Step 4: Define CLI disconnect cleanup.** On CLI disconnect, request cooperative Stop for the active control and call `discard_pending_route(route)`. Do not force-cancel the Runtime task, and do not affect other routes or background work.

- [ ] **Step 5: Run focused tests.** Run `pytest tests/test_gateway_turns.py tests/test_gateway_web_injection.py tests/test_gateway_web_session_scope.py tests/test_gateway_cli_e2e.py -q`; confirm no route starts a second normal turn early and global idle includes queued work.

### Task 5: Make capabilities and CLI protocol explicit

**Files:**

- Modify: `Turning-Good-Agent/channels/base.py`
- Modify: `Turning-Good-Agent/channels/cli_gateway.py`
- Modify: `Turning-Good-Agent/cli.py`
- Modify: `Turning-Good-Agent/web/backend/app.py`
- Test: `tests/test_cli_gateway_client.py`
- Test: `tests/test_cli_gateway_endpoint.py`
- Test: `tests/test_cli_gateway_transport.py`
- Test: `tests/test_cli_terminal_mode.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    supports_guidance: bool = False

CLI_EVENT_FIELDS = frozenset({
    "request_id", "content", "tool_call_id", "tool_name", "args",
    "failed", "approval_id", "approved", "outcome",
})
```

- Web declares `supports_guidance=True`; CLI and an unregistered future Channel default to `False`.
- The CLI transport serializes only the controlled fields above at top level. It must not expose arbitrary `OutboundMessage.metadata`.

- [ ] **Step 1: Write failing protocol tests.** Confirm `tga chat` parses without `--session`, connects using `default`, preserves an explicitly supplied conversation, and rejects CLI `guidance.send` at the Gateway endpoint.

- [ ] **Step 2: Flatten the controlled event contract.** Have `CliGatewayTransport._payload_for` copy only `request_id`, Tool fields, approval fields, completion outcome, and content to the top-level payload. Keep Web `event_id` replay cursor semantics unchanged.

- [ ] **Step 3: Carry Tool argument summaries.** Include normalized `args` on `tool.started`, display start/completed/failed Tool lines in the CLI, and render a bounded JSON argument summary rather than raw metadata.

- [ ] **Step 4: Replace direct terminal rendering with one UI event queue.** The socket receiver only enqueues gateway events; an input task enqueues user lines; one renderer/state loop owns status output, streaming deltas, pending approval selection, and prompt-safe output. A `y`/`Y` approves the oldest pending approval; every other response is rejection.

- [ ] **Step 5: Remove CLI guidance surface.** Delete `GatewayCliClient.send_guidance`, the `/guide` command/help text, and CLI control guidance buffering. Keep `consume_guidance()` as an empty adapter-compatible implementation where the shared runtime protocol requires it.

- [ ] **Step 6: Run focused tests.** Run `pytest tests/test_cli_gateway_client.py tests/test_cli_gateway_endpoint.py tests/test_cli_gateway_transport.py tests/test_cli_terminal_mode.py -q`; verify Tool start/finish/error output, immediate HITL decision handling, and prompt-safe streamed output.

### Task 6: Rename the Web chat WebSocket without an alias

**Files:**

- Modify: `Turning-Good-Agent/web/backend/app.py`
- Modify: `web/frontend/src/state/socket_client.ts`
- Modify: WebSocket endpoint tests and mock URLs in `tests/test_gateway_web_session_scope.py`, `tests/test_gateway_web_injection.py`, `web/frontend/tests/proactive_notifications.spec.ts`, `web/frontend/tests/proactive_workspace.spec.ts`, and `web/frontend/tests/workbench_visual.spec.ts`

- [ ] **Step 1: Write the route contract test.** Assert `/ws/web` accepts the existing session subscribe/message/guide/stop/approval protocol and `/ws` has no WebSocket route.

- [ ] **Step 2: Change backend and React connection endpoints together.** Replace the chat decorator and the `SessionSocketClient` URL in the same change; do not modify `/ws/cli` or `/ws/proactive`.

- [ ] **Step 3: Update all mocks and reconnect assertions.** Preserve `after_event_id` behavior and prove reconnect remains scoped to the active Web session.

- [ ] **Step 4: Run focused tests.** Run `pytest tests/test_gateway_web_session_scope.py tests/test_gateway_web_injection.py -q` and the affected Playwright mock tests.

### Task 7: Remove the unimplemented durable Outbox path

**Files:**

- Delete: `Turning-Good-Agent/proactive/delivery.py`
- Modify: `Turning-Good-Agent/proactive/notifications.py`
- Modify: `Turning-Good-Agent/proactive/cron.py`
- Modify: `Turning-Good-Agent/channels/manager.py`
- Modify: `Turning-Good-Agent/gateway/host.py`
- Delete: `tests/test_delivery_outbox.py`
- Delete: `tests/test_proactive_delivery.py`
- Modify: `tests/test_notification_fanout.py`
- Modify: `tests/test_channel_manager.py`
- Modify: `tests/test_gateway_host_notifications.py`
- Modify: `tests/test_session_store_layout.py`

**Interfaces:**

```python
class NotificationFanout:
    async def dispatch(
        self,
        event: ProactiveResultEvent,
        *,
        publish_outbound: OutboundPublisher,
    ) -> tuple[OutboundMessage, ...]: ...
```

- A subscription is expanded to one exact `Recipient` message only when that recipient is online now.
- `ChannelManager.deliver()` and delivery listeners remain the only outbound acceptance observation. `publish_and_wait()` and receipt bookkeeping are removed.

- [ ] **Step 1: Write failing online-only fanout tests.** Assert online Web and active CLI recipients receive one `proactive_notification`, offline CLI is skipped, and an unknown future Channel is skipped instead of becoming durable.

- [ ] **Step 2: Remove durable types and calls.** Delete `DeliveryClassification.durable`, durable `NotificationPlan` entries, Outbox construction/dispatch, retry worker lifecycle, `ChannelManager.publish_and_wait`, and Cron source-ID delivery cleanup.

- [ ] **Step 3: Preserve existing local data.** Do not open, migrate, delete, or rewrite `<data_dir>/proactive/pending_deliveries.json`. The session cleanup test must prove it remains an unknown preserved file.

- [ ] **Step 4: Delete obsolete implementation tests.** Remove tests whose only subject is the deleted Outbox/DeliveryWorker, then update remaining notification tests to assert immediate targeted delivery.

- [ ] **Step 5: Run focused tests.** Run `pytest tests/test_notification_fanout.py tests/test_channel_manager.py tests/test_gateway_host_notifications.py tests/test_session_store_layout.py -q` and verify no import references `proactive.delivery`.

### Batch 2 acceptance gate

- [ ] Two normal messages for one Channel conversation run FIFO, while another route can run independently.
- [ ] Stop and approval resolve an active turn immediately; only Web can send guidance.
- [ ] `tga chat` defaults to `default`, prints Tool lifecycle/arguments, and accepts `y/N` without a stuck approval.
- [ ] `/ws/web` works and `/ws` has no compatibility alias.
- [ ] No runtime path reads or writes `pending_deliveries.json`; the existing file is preserved unchanged.

---

## Batch 3: Web Proactive Workspace and Notices

### Task 8: Replace the five-tab workspace with direct domain entries and overview

**Files:**

- Modify: `web/frontend/src/proactive_types.ts`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/SessionSidebar.tsx`
- Modify: `web/frontend/src/components/ProactiveWorkspace.tsx`
- Create: `web/frontend/src/components/ProactiveOverview.tsx`
- Modify: `web/frontend/src/styles/components/proactive.css`
- Modify: `web/frontend/src/styles/components/sidebar.css`
- Test: `web/frontend/tests/proactive_workspace.spec.ts`
- Test: `web/frontend/tests/workbench_visual.spec.ts`

**Interfaces:**

```ts
export type ProactiveRoute =
  | "overview"
  | "cron"
  | "breakbeat"
  | "memory"
  | "skills"
  | "incidents";

proactiveRouteFromHash("#proactive") === "overview";
routeDomain("overview") === "#proactive";
```

- Existing detailed domain pages and `/api/proactive/*` calls remain unchanged.
- `ProactiveOverview` consumes the app-lifetime `ProactiveState` and presents only concise per-domain state, counts, and direct navigation actions.

- [ ] **Step 1: Write failing navigation tests.** Assert `#proactive` renders overview, each of the five sidebar entries selects its deep link, and the tablist/roving-tab keyboard contract no longer exists.

- [ ] **Step 2: Add the overview route without changing domain wire mapping.** Keep `dream -> memory`, `skill -> skills`, and `incident -> incidents` mappings for `/ws/proactive` snapshots.

- [ ] **Step 3: Turn sidebar navigation into five independent entries.** Render Cron, Breakbeat, Dream/long-term memory, Skill evolution, and Incidents below the overview entry. In collapsed mode retain clear accessible labels and avoid tooltip-only navigation.

- [ ] **Step 4: Recompose `ProactiveWorkspace`.** Remove the mixed tab strip and tab-panel roles. Render either the overview or exactly one existing detailed domain page in the existing predictable `ScrollArea`; Context/Tools inspectors stay outside this workspace.

- [ ] **Step 5: Apply the existing design system.** Keep low-saturation dual themes, three existing radius tiers, background and whitespace hierarchy, native `ScrollArea`, concise loading/empty/error states, and no new dependency or marketing-style grid.

- [ ] **Step 6: Run focused visual tests.** Run `pnpm exec playwright test tests/proactive_workspace.spec.ts tests/workbench_visual.spec.ts` from `web/frontend`; inspect both themes, long cards, narrow viewports, sidebar collapse, and direct route reloads.

### Task 9: Make proactive notices short-lived and non-blocking

**Files:**

- Modify: `web/frontend/src/components/NoticeRegion.tsx`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/styles/components/overlays.css`
- Test: `web/frontend/tests/proactive_notifications.spec.ts`
- Test: `web/frontend/tests/desktop_surface_refinement.spec.ts`

**Interfaces:**

```ts
export type Notice = {
  id: string;
  title?: string;
  message: string;
  target?: string;
  severity: "info" | "warning" | "error";
};

const noticeLifetimeMs = { info: 4_000, warning: 4_000, error: 7_000 } as const;
```

- A notice never calls `.focus()` when received. Its container uses polite live-region semantics and is not a dialog.
- Each visible notice owns a pause/resume timer. Pointer hover or focus within pauses its remaining lifetime; leaving or losing focus resumes it.

- [ ] **Step 1: Write failing timing and focus tests.** Emit info and error proactive notices, assert 4-second/7-second removal windows, hover pause, keyboard-focus pause, and preserved Composer focus on arrival.

- [ ] **Step 2: Carry severity from `/ws/proactive`.** Preserve the existing API error notices as `error`; map success/informational proactive notices to `info` without changing chat state.

- [ ] **Step 3: Implement top-center notice layout.** Use fixed top-center placement, compact max width, background surface, semantic text/icon treatment, and a restrained shadow. Do not use a blocking overlay, modal focus trap, fine border, browser notification, or Composer/Stop/HITL focus movement.

- [ ] **Step 4: Make click navigation intentional.** Clicking a targeted notice routes to its proactive page and dismisses that notice; the close control only dismisses it. Keep at most three visible notices and leave browser-close behavior as non-persistent.

- [ ] **Step 5: Run focused frontend tests.** Run `pnpm exec playwright test tests/proactive_notifications.spec.ts tests/desktop_surface_refinement.spec.ts` and verify light/dark contrast, hover/focus timer behavior, and no compositor obstruction.

### Task 10: Update canonical documentation after verified behavior

**Files:**

- Modify: `README.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/phases/2026-06-15-phase-6-web-observability.md`
- Modify: `docs/phases/2026-06-15-phase-7-proactive-memory.md`
- Modify: `docs/phases/2026-06-15-phase-8-im-channel-adapters.md`

- [ ] **Step 1: Update Phase 7 facts.** Replace fixed-empty Cron Tool behavior, Breakbeat failure scheduling, Outbox semantics, CLI `/guide`, mixed proactive tabs, and persistent right-bottom notices with the verified implementation contract.

- [ ] **Step 2: Update Phase 6 Web contract.** Document `/ws/web`, server-only guidance capability, route FIFO presentation, direct proactive navigation, and top-center non-blocking notifications while retaining independent Context/Tools inspectors.

- [ ] **Step 3: Update Phase 8 IM-adapter boundary.** State that future Adapter reliability is undecided and must be designed with a real platform acknowledgment; unknown Channels are never automatically durable.

- [ ] **Step 4: Update overview documents.** Remove Outbox nodes/references from architecture diagrams and current-state summaries. Keep the no-migration/no-automatic-deletion rule explicit.

- [ ] **Step 5: Verify documentation consistency.** Search for stale `/ws`, `/guide`, `DeliveryOutbox`, `DeliveryWorker`, and `pending_deliveries.json` runtime claims. Preserve only the explicit statement that existing local files are untouched.

### Batch 3 acceptance gate

- [ ] `#proactive` is concise overview, and all five detailed domains are direct sidebar destinations without a tablist.
- [ ] Context and Tool Calls inspectors remain separate from proactive pages.
- [ ] Info/success notices dismiss after about 4 seconds, errors after about 7 seconds, and hover/focus pauses each timer.
- [ ] Notice arrival does not move focus, block the Composer, Stop, or HITL, or write a chat message.

---

## Test Matrix and Final Verification

| Layer | Required validation |
| --- | --- |
| Batch 1 Python | `pytest tests/test_proactive_cron.py tests/test_proactive_executor.py tests/test_proactive_breakbeat.py tests/test_proactive_service.py tests/test_proactive_incidents.py tests/test_agent_loop.py tests/test_gateway_web_coordinator.py -q` |
| Batch 2 Python | `pytest tests/test_gateway_turns.py tests/test_gateway_web_injection.py tests/test_gateway_web_session_scope.py tests/test_cli_gateway_client.py tests/test_cli_gateway_endpoint.py tests/test_cli_gateway_transport.py tests/test_cli_terminal_mode.py tests/test_gateway_cli_e2e.py tests/test_notification_fanout.py tests/test_channel_manager.py tests/test_gateway_host_notifications.py tests/test_session_store_layout.py -q` |
| Batch 3 frontend | `pnpm exec playwright test tests/proactive_workspace.spec.ts tests/proactive_notifications.spec.ts tests/observability_view.spec.ts tests/workbench_visual.spec.ts tests/desktop_surface_refinement.spec.ts` |
| Web build | `pnpm run build` from `web/frontend` |
| Broader regression | `pytest -q` from repository root, then default Playwright suite and `TGA_REAL_PAGE=1` real-page suite when the Docker/API environment is available |
| Runtime hygiene | `python -m compileall Turning-Good-Agent tests` and `git diff --check` |
| Manual CLI | Start `tga gateway`; verify `tga chat` joins `default`, `tga chat --session named` joins `named`, Tool lifecycle prints, an approval accepts `y` and rejects any other response, and a CLI disconnect cancels only its active/pending route work |
| Manual Web | Verify `/ws/web` traffic, no `/ws` fallback, normal same-session messages queue visibly, Web guidance still works, and proactive notice arrival preserves the currently focused Composer/HITL control |
| Data preservation | Seed an old `<data_dir>/proactive/pending_deliveries.json`; start/stop the Gateway and assert its bytes are unchanged and no code path reports it as a retry queue |

## Documentation and Commit Discipline

- Do not claim a batch completed in phase documents until its tests and manual acceptance gate pass.
- Keep Batch 1, Batch 2, and Batch 3 as separate reviewable commits when implementation is approved. Do not mix refactors from a later batch into an earlier batch.
- Because `docs/` is ignored by the repository, explicitly stage changed documentation with `git add -f` only when the user asks to commit it.
