# Turning-Good-Agent Phase 9 多 Channel 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已完成的 CLI 与本机 Web 之外接入微信、飞书两类办公软件 channel，并通过统一 MessageBus 与 AgentRuntime 通信。

**Architecture:** CLI 与 Web 已完成各自 Host。Phase 9 只为微信、飞书增加 Host；它们通过既有 `AsyncMessageBus`、`ChannelRouter` 和 `ChannelAdapter` 接入 Runtime，不直接调用 AgentLoop。Runtime 不关心消息来自 CLI、Web、微信还是飞书。

**Tech Stack:** Python 3.11+、asyncio、HTTP/WebSocket、Webhook、JSON、MessageBus。

---

## Scope

本阶段实现：

- 微信 channel Host 与 adapter 骨架
- 飞书 channel Host 与 adapter 骨架
- 到既有 MessageBus 的统一接线
- 办公软件 outbound message 路由

本阶段不实现：

- 完整公网部署
- 企业级鉴权
- 多租户权限
- 文件附件深度处理

## Target File Map

Existing: `Turning-Good-Agent/channels/base.py`

复用当前 `ChannelAdapter`、`TurnControl` 和接收 `InboundMessage` 的 `ChannelRouter`，不重新定义第二套协议。

Existing: `Turning-Good-Agent/channels/cli.py` / `Turning-Good-Agent/channels/web.py`

保留已完成的 CLI 与 Web 行为，不因办公软件接入重构它们。

Create: `Turning-Good-Agent/channels/wechat.py`

微信 adapter 骨架，处理 webhook payload 到 `InboundMessage` 的转换。

Create: `Turning-Good-Agent/channels/feishu.py`

飞书 adapter 骨架，处理 event callback 到 `InboundMessage` 的转换。

Existing: `Turning-Good-Agent/bus/queue.py`

复用已存在的 inbound/outbound 消费接口：

```python
async def consume_inbound() -> InboundMessage:
    ...

async def consume_outbound() -> OutboundMessage:
    ...
```

Existing: `Turning-Good-Agent/runtime/runtime.py`

保持 Runtime 只接收 `InboundMessage`，不接触办公软件 channel 细节。

## Message Mapping

`InboundMessage.metadata` 应保存 channel 特有字段。

Web 示例：

```json
{
  "thread_id": "web-session-id",
  "ip": "127.0.0.1"
}
```

微信示例：

```json
{
  "openid": "...",
  "conversation_type": "private",
  "raw_event_id": "..."
}
```

飞书示例：

```json
{
  "tenant_key": "...",
  "chat_id": "...",
  "message_id": "..."
}
```

## Task 1: Office Channel Skeletons

- [ ] **Step 1: 微信 payload 转换**

先实现纯转换函数，不直接接公网。

- [ ] **Step 2: 飞书 payload 转换**

先实现纯转换函数，不直接接公网。

## Completion Criteria

- CLI 仍可用。
- Web 仍可用，且不被 Phase 9 修改行为。
- 微信/飞书 adapter 至少能把示例 payload 转成 `InboundMessage`。
- MessageBus 命名使用 `consume_inbound` 和 `consume_outbound`。
