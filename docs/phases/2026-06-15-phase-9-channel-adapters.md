# Turning-Good-Agent Phase 9 多 Channel 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 CLI 之外接入 Web、微信、飞书三类 channel，并通过统一 MessageBus 与 AgentRuntime 通信。

**Architecture:** Channel adapter 只做协议适配，不直接调用 AgentLoop。所有 channel 都生产 `InboundMessage`，消费 `OutboundMessage`。Runtime 不关心消息来自 CLI、Web、微信还是飞书。

**Tech Stack:** Python 3.11+、asyncio、HTTP/WebSocket、Webhook、JSON、MessageBus。

---

## Scope

本阶段实现：

- Web chat channel
- 微信 channel adapter 骨架
- 飞书 channel adapter 骨架
- channel 到 MessageBus 的统一接线
- outbound message 路由

本阶段不实现：

- 完整公网部署
- 企业级鉴权
- 多租户权限
- 文件附件深度处理

## Target File Map

Create: `Turning-Good-Agent/channels/base.py`

定义 channel adapter 协议。

Create: `Turning-Good-Agent/channels/cli.py`

把现有 CLI 输入输出逐步迁移成标准 channel adapter。

Create: `Turning-Good-Agent/channels/web.py`

提供 Web chat 的 HTTP/WebSocket adapter。

Create: `Turning-Good-Agent/channels/wechat.py`

微信 adapter 骨架，处理 webhook payload 到 `InboundMessage` 的转换。

Create: `Turning-Good-Agent/channels/feishu.py`

飞书 adapter 骨架，处理 event callback 到 `InboundMessage` 的转换。

Modify: `Turning-Good-Agent/bus/queue.py`

明确 inbound/outbound queue 的消费接口：

```python
async def consume_inbound() -> InboundMessage:
    ...

async def consume_outbound() -> OutboundMessage:
    ...
```

Modify: `Turning-Good-Agent/runtime/runtime.py`

保持 Runtime 只接收 `InboundMessage`，不接触 channel 细节。

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

## Task 1: Channel Base Protocol

- [ ] **Step 1: 定义 adapter 协议**

建议接口：

```python
class ChannelAdapter(Protocol):
    name: str

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def send(self, message: OutboundMessage) -> None:
        ...
```

- [ ] **Step 2: CLI 迁移**

保留现有 CLI 行为，但把输入输出路径改成标准 adapter。

## Task 2: Web Channel

- [ ] **Step 1: 提供本地 Web chat**

第一版只要求本地运行，能发送文本、显示回复。

- [ ] **Step 2: 接入 MessageBus**

Web 输入写入 inbound queue，Runtime 输出写入 outbound queue。

## Task 3: Office Channel Skeletons

- [ ] **Step 1: 微信 payload 转换**

先实现纯转换函数，不直接接公网。

- [ ] **Step 2: 飞书 payload 转换**

先实现纯转换函数，不直接接公网。

## Completion Criteria

- CLI 仍可用。
- Web chat 可以和 Runtime 对话。
- 微信/飞书 adapter 至少能把示例 payload 转成 `InboundMessage`。
- MessageBus 命名使用 `consume_inbound` 和 `consume_outbound`。
