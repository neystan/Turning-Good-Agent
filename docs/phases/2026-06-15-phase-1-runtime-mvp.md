# Turning-Good-Agent Phase 1 Runtime MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立最小可运行的 CLI-first Agent Runtime，完成会话、状态机、JSON 存储、基础 tools、短期压缩和可观测性。

**Architecture:** Phase 1 以 `AgentRuntime` 为中心，channel 只负责收发消息。Runtime 当前使用 `SESSION -> COMMAND -> BUILD -> RUN -> COMPACT -> SAVE -> RESPOND` 状态机，所有会话数据写入本地 JSON/JSONL 文件。

**Tech Stack:** Python 3.11+、asyncio、argparse、JSON/JSONL、OpenAI-compatible 接口、pytest 本地验证。

---

## Status

当前阶段已基本完成。

已完成范围：

- CLI 入口
- `InboundMessage` / `OutboundMessage`
- 显式 Runtime 状态机
- OpenAI-compatible 纯文本对话
- `ToolRegistry` / `ToolExecutor`
- 内置 `echo` / `now`
- session 独立目录
- `/history`、`/new`、`/clear`、`/exit`
- token 驱动短期压缩
- COMPACT 独立状态
- trace 和 token usage 记录
- `settings.local.json`

遗留问题：

- 真实 LLM tool calling 未实现
- Web / 微信 / 飞书 channel 未实现
- MCP / skills / proactive 仍是骨架

## Target File Map

| 路径 | 职责 |
| --- | --- |
| `Turning-Good-Agent/cli.py` | CLI channel 和交互入口。 |
| `Turning-Good-Agent/runtime/state.py` | Runtime 状态机。 |
| `Turning-Good-Agent/runtime/runtime.py` | 单轮消息处理总控。 |
| `Turning-Good-Agent/runtime/agent_loop.py` | LLM + tools 循环。 |
| `Turning-Good-Agent/sessions/store.py` | JSON/JSONL 文件存储。 |
| `Turning-Good-Agent/memory/short_term.py` | 短期记忆压缩。 |
| `Turning-Good-Agent/observability/trace.py` | 状态 trace。 |
| `Turning-Good-Agent/observability/token_monitor.py` | token 记录。 |

## Verification

当前阶段的手动验证命令：

```bash
cd /download/Turning-Good-Agent
python -m Turning-Good-Agent chat
```

建议手动检查：

```text
/history
/new
/clear
/exit
```

检查数据目录：

```bash
find .sessions -maxdepth 2 -type f | sort
```

预期可以看到：

```text
session.json
messages.jsonl
turn_traces.jsonl
token_usage.jsonl
```

## Completion Criteria

- CLI 可以连续对话。
- 默认不传 `--session` 时新开隔离会话。
- 显式传 `--session` 时可恢复历史。
- `/clear` 删除当前 session 目录。
- 压缩统计只暴露 5 个字段。
- `settings.local.json` 可以配置真实 LLM。
