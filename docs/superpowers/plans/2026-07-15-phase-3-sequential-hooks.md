# Phase 3 轻量顺序 Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `before_turn/after_turn`、`before_tool_call/after_tool_call`、`before_compact/after_compact` 三对可信进程内顺序 Hook，并把 CLI 压缩提示移出 Runtime。

**Architecture:** 状态机、AgentLoop、ToolExecutor 和 `.sessions` 持久化继续承担核心职责。Runtime 持有唯一 `HookManager`，同一个 manager 注入 AgentLoop；Hook 直接复用 `TurnContext`、`ToolCall` 和现有工具记录，不增加通用 Context/Result 协议。

**Tech Stack:** Python 3.11+、asyncio、dataclasses、现有 Runtime state machine、pytest。

## Global Constraints

- 第一版只实现 3 对顺序 Hook，不接入事件 Hook。
- 不增加 Context、LLM、SAVE、RESPOND Hook。
- 不执行 shell、HTTP、MCP、prompt、agent 或第三方插件 Hook。
- Hook 不替代状态机、核心安全校验和 `.sessions` JSON/JSONL 持久化。
- 所有新增文件和函数必须包含精简中文注释。
- 项目不使用 `uv`。
- 测试可以在本地创建和运行，但 `tests/` 已被 `.gitignore` 忽略，不提交到 GitHub。

---

### Task 1: 定义 Hook 基类和顺序管理器

**Files:**
- Create: `Turning-Good-Agent/hooks/base.py`
- Modify: `Turning-Good-Agent/hooks/manager.py`
- Local Test: `tests/test_hooks.py`

**Interfaces:**
- Produces: `AgentHook`、`HookManager.register()` 和 6 个同名生命周期触发方法。
- Consumes: `TurnContext`、`ToolCall`、现有工具记录 mapping。

- [ ] **Step 1: 编写本地失败测试**

覆盖以下行为：注册顺序稳定；`before_turn` 和 `before_tool_call` 返回第一个非空阻断原因；`after_*` 全部执行；单个 Hook 异常不阻断后续 Hook。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_hooks.py`

Expected: 因 `AgentHook` 或顺序触发接口尚不存在而失败。

- [ ] **Step 3: 创建最小 Hook 基类**

`AgentHook` 使用以下签名，每个方法默认不执行操作：

```python
class AgentHook:
    """定义第一版顺序 Hook 生命周期。"""

    async def before_turn(self, ctx: TurnContext) -> str | None:
        """在整轮执行前返回可选阻断原因。"""
        return None

    async def after_turn(self, ctx: TurnContext) -> None:
        """在整轮执行结束后读取最终结果。"""

    async def before_tool_call(self, call: ToolCall) -> str | None:
        """在工具执行前返回可选阻断原因。"""
        return None

    async def after_tool_call(self, record: Mapping[str, Any]) -> None:
        """在工具处理完成后读取工具记录。"""

    async def before_compact(self, ctx: TurnContext) -> None:
        """在真实压缩开始前执行扩展。"""

    async def after_compact(self, ctx: TurnContext) -> None:
        """在真实压缩完成后执行扩展。"""
```

使用 `TYPE_CHECKING` 导入 `TurnContext`，避免运行时循环导入。

- [ ] **Step 4: 重构 HookManager**

`HookManager` 保存 `list[AgentHook]`，提供 `register(hook)`。6 个触发方法按注册顺序调用对应方法；阻断方法返回第一个非空字符串；异常使用 `logging.getLogger(__name__).exception(...)` 记录后继续。

第一版删除 manager 中尚未接线的 `on/emit` 事件 API；`hooks/events.py` 保留为后续事件 Hook 候选定义，不接入 Runtime。

- [ ] **Step 5: 运行局部测试**

Run: `pytest -q tests/test_hooks.py`

Expected: PASS。

### Task 2: 接入 Turn Hook

**Files:**
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Local Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: Task 1 的 `HookManager.before_turn(ctx)`、`after_turn(ctx)`。
- Produces: Runtime 持有的 `hooks` 属性和覆盖快捷命令、正常轮次、错误轮次的 Turn 生命周期。

- [ ] **Step 1: 编写本地失败测试**

验证正常对话顺序为 `before_turn -> 状态机 -> after_turn`；`/history` 等快捷命令同样触发 Turn Hook；`before_turn` 返回原因后只进入 RESPOND，不创建或加载 Session、不调用 LLM、不产生孤立 Session 目录；`after_turn` 可以读取最终 `ctx.outbound` 和完整 trace。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_runtime.py -k hook`

Expected: Runtime 尚未触发 Hook，测试失败。

- [ ] **Step 3: 注入唯一 HookManager**

`AgentRuntime.__init__()` 增加 `hooks: HookManager`，`create_default()` 创建一个 manager，同时传给 Runtime 和 AgentLoop。

- [ ] **Step 4: 接入 before_turn**

在获得 session lock 后、状态循环前调用 `before_turn(ctx)`。收到非空原因时设置：

```python
ctx.error = reason
ctx.final_content = f"请求失败：{reason}"
ctx.state = TurnState.RESPOND
```

随后继续使用现有 RESPOND 状态生成错误 `OutboundMessage`。

同步修改 `save_remaining_traces()`：除快捷命令外，`ctx.session is None` 时也跳过 trace 落盘，避免 before hook 在 SESSION 前阻断后创建只有 `turn_traces.jsonl`、没有 `session.json` 的孤立目录。

- [ ] **Step 5: 接入 after_turn**

状态循环结束并执行 `save_remaining_traces()` 后调用 `after_turn(ctx)`。HookManager 已隔离普通 Hook 异常，因此 Hook 失败不能改变出站结果。

- [ ] **Step 6: 运行局部测试**

Run: `pytest -q tests/test_runtime.py -k hook`

Expected: PASS。

### Task 3: 接入 Tool Call Hook

**Files:**
- Modify: `Turning-Good-Agent/runtime/agent_loop.py`
- Local Test: `tests/test_tools_loop.py`

**Interfaces:**
- Consumes: Task 1 的 `before_tool_call(call)`、`after_tool_call(record)`。
- Produces: 可阻断的单次工具调用，以及成功、失败、阻断都会经过的 after hook。

- [ ] **Step 1: 编写本地失败测试**

覆盖：允许时仍调用 ToolExecutor；阻断时不执行工具；阻断结果作为 `role=tool` 消息注入 working messages；成功、失败、阻断都调用 `after_tool_call`；多个工具调用分别触发 Hook。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_tools_loop.py -k hook`

Expected: AgentLoop 尚未触发 Hook，测试失败。

- [ ] **Step 3: 在工具执行前处理阻断**

对每个 `ToolCall` 先调用 `before_tool_call(call)`。若返回原因，不调用 ToolExecutor，构造与现有记录兼容的结果：

```python
record = {
    "tool_name": call.name,
    "args": call.args,
    "content": f"工具 {call.name} 被 Hook 阻止：{reason}",
    "duration_ms": 0.0,
    "error": reason,
}
```

然后补充 `tool_call_id`，正常进入 `tool_records` 和 working messages。

- [ ] **Step 4: 在工具处理后触发 Hook**

无论 ToolExecutor 返回成功或错误记录，还是 before hook 阻断，都调用：

```python
await self.hooks.after_tool_call(dict(record))
```

传入浅拷贝，避免 after hook 直接改变即将注入模型和落盘的核心记录。

- [ ] **Step 5: 运行局部测试**

Run: `pytest -q tests/test_tools_loop.py -k hook`

Expected: PASS。

### Task 4: 接入 Compact Hook 并移出 CLI 提示

**Files:**
- Modify: `Turning-Good-Agent/runtime/state.py`
- Modify: `Turning-Good-Agent/cli.py`
- Local Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: Task 1 的 `before_compact(ctx)`、`after_compact(ctx)`。
- Produces: 只有真实压缩才触发的生命周期，以及 CLI 注册的压缩状态展示 Hook。

- [ ] **Step 1: 编写本地失败测试**

验证未达到阈值和 `compact_source` 为空时不触发；真实压缩顺序为 `before_compact -> LLM summary -> after_compact`；CLI Hook 输出现有两条中文提示；Runtime state 不再直接打印。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_compaction.py -k hook`

Expected: COMPACT 仍直接调用 `print_status()`，测试失败。

- [ ] **Step 3: 在 COMPACT 中触发 Hook**

保留现有阈值、recent window 和 `compact_source` 计算。仅在 `compact_source` 非空后调用 `before_compact(ctx)`；摘要成功、统计和 `true_token_usage` 更新完成后调用 `after_compact(ctx)`。

删除 `runtime/state.py` 中两次 `print_status()` 调用和 `print_status()` 函数。

- [ ] **Step 4: 在 CLI 注册状态 Hook**

在 `cli.py` 定义 `CliCompactStatusHook(AgentHook)`。`before_compact` 输出“正在压缩会话上下文...”；`after_compact` 使用 `ctx.compact_stats` 输出压缩消息数、压缩 token 和保留原文 token。创建 Runtime 后执行：

```python
runtime.hooks.register(CliCompactStatusHook())
```

- [ ] **Step 5: 运行局部测试**

Run: `pytest -q tests/test_compaction.py -k hook`

Expected: PASS。

### Task 5: 全量验证与文档收口

**Files:**
- Modify if behavior differs: `README.md`
- Modify if behavior differs: `docs/PROJECT_ARCHITECTURE.md`
- Modify if behavior differs: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify if behavior differs: `docs/phases/2026-06-15-phase-3-hooks.md`

- [ ] **Step 1: 运行 Hook 局部测试**

Run: `pytest -q tests/test_hooks.py tests/test_runtime.py tests/test_tools_loop.py tests/test_compaction.py`

Expected: PASS。

- [ ] **Step 2: 运行全量测试**

Run: `pytest -q`

Expected: 全部通过。

- [ ] **Step 3: 运行 CLI 冒烟**

Run: `printf '/exit\n' | python -m Turning-Good-Agent chat`

Expected: CLI 正常启动并退出，无 traceback。

- [ ] **Step 4: 检查格式和提交范围**

Run: `git diff --check`

Expected: 无输出。

Run: `git status -sb`

Expected: 只包含 Phase 3 代码和相关文档；`tests/` 不在待提交列表。

- [ ] **Step 5: 提交实现**

```bash
git add Turning-Good-Agent/hooks/base.py \
  Turning-Good-Agent/hooks/manager.py \
  Turning-Good-Agent/runtime/runtime.py \
  Turning-Good-Agent/runtime/agent_loop.py \
  Turning-Good-Agent/runtime/state.py \
  Turning-Good-Agent/cli.py \
  README.md docs
git commit -m "feat: add lightweight sequential hooks"
```

不要添加 `tests/`。
