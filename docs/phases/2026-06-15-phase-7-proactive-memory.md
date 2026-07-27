# Turning-Good-Agent Phase 7 主动能力与长期记忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立主动能力第一版，包括 dream、breakbeat 和事件记忆，为后续 cron 和 agent 自进化打基础。

**Architecture:** 主动能力放在 `proactive/` 内，长期记忆放在 `memory/` 内。Runtime 只发事件，不直接把主动逻辑写死在状态机里。

**Tech Stack:** Python 3.11+、JSON/JSONL、LLM summarization provider、asyncio。

---

## Scope

本阶段实现：

- conversation completed 事件消费
- dream：从会话提取长期记忆候选
- breakbeat：识别未完成任务
- 事件记忆 JSONL 存储
- 主动提醒候选记录

本阶段不实现：

- 真正定时调度
- 跨 channel 推送
- 自动写入不可撤销长期记忆
- 自动生成 skill

## Target File Map

Modify: `Turning-Good-Agent/proactive/manager.py`

支持注册多个 handler，并按事件异步执行。

Create: `Turning-Good-Agent/proactive/dream.py`

从会话中提取长期记忆候选。

Create: `Turning-Good-Agent/proactive/breakbeat.py`

识别用户未完成任务、承诺、待办。

Modify: `Turning-Good-Agent/memory/event_memory.py`

持久化事件记忆和提醒候选。

Modify: `Turning-Good-Agent/config/settings.py`

增加 proactive 开关。

## Event Types

第一版事件：

```text
conversation_completed
memory_compacted
tool_failed
```

## Dream Output

建议结构：

```json
{
  "type": "user_preference",
  "content": "用户偏好中文回答。",
  "source_session_id": "...",
  "confidence": 0.8,
  "created_at": "..."
}
```

## Breakbeat Output

建议结构：

```json
{
  "type": "unfinished_task",
  "content": "用户想继续实现 MCP client。",
  "source_session_id": "...",
  "status": "candidate",
  "created_at": "..."
}
```

## Completion Criteria

- 会话完成后会触发 proactive handler。
- dream 能生成长期记忆候选。
- breakbeat 能生成未完成任务候选。
- 候选结果写入可读 JSONL。
- 默认不主动打扰用户，只记录候选。
