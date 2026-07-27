# Turning-Good-Agent Phase 8 Multi-Agent 协作模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现用户手动开启的 Multi-Agent 协作模式，由 planner 拆解任务，workers 执行子任务，main agent 汇总结果。

**Architecture:** 默认仍是单 agent。Multi-Agent 作为 runtime 的可选执行模式，只在用户显式开启时启用。Planner 和 workers 通过内部 message bus 交换结构化任务，不共享完整上下文。

**Tech Stack:** Python 3.11+、asyncio、dataclasses、JSON/JSONL trace。

---

## Scope

本阶段实现：

- 手动开启 multi-agent
- planner 生成任务计划
- worker 执行子任务
- main agent 汇总结果
- 多 agent trace
- token 成本记录

本阶段不实现：

- 自动开启 multi-agent
- 分布式 worker
- 长时间后台任务
- worker 自主创建新 worker

## Target File Map

Create: `Turning-Good-Agent/multi_agent/types.py`

定义 `PlanStep`、`WorkerTask`、`WorkerResult`。

Create: `Turning-Good-Agent/multi_agent/planner.py`

负责把用户任务拆成可执行步骤。

Create: `Turning-Good-Agent/multi_agent/worker.py`

执行单个 worker task。

Create: `Turning-Good-Agent/multi_agent/coordinator.py`

协调 planner、workers 和汇总。

Modify: `Turning-Good-Agent/runtime/runtime.py`

在 RUN 阶段根据配置选择单 agent 或 multi-agent。

Modify: `Turning-Good-Agent/config/settings.py`

增加 multi-agent 开关和 worker 数限制。

## Activation

第一版只允许手动开启：

```text
/multi on
/multi off
```

或配置：

```json
{
  "multi_agent": {
    "enabled": false,
    "max_workers": 3
  }
}
```

## Planner Contract

Planner 输出：

```json
{
  "goal": "实现 MCP client",
  "steps": [
    {
      "id": "step-1",
      "title": "设计 MCP 配置",
      "worker_type": "coding",
      "required_context": ["settings.py", "spec"]
    }
  ]
}
```

## Worker Contract

Worker 输入：

```json
{
  "task_id": "step-1",
  "instruction": "设计 MCP 配置结构",
  "context": []
}
```

Worker 输出：

```json
{
  "task_id": "step-1",
  "status": "completed",
  "result": "建议新增 McpSettings...",
  "artifacts": []
}
```

## Completion Criteria

- 用户可以手动开启 multi-agent。
- planner 能生成结构化计划。
- workers 能各自执行任务。
- main agent 能汇总结果。
- trace 中可以看出 planner 和 worker 的执行过程。
- 默认单 agent 行为不变。
