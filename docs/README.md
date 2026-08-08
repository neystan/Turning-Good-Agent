# Turning-Good-Agent 文档入口

本文档目录用于维护 Turning-Good-Agent 的当前设计、阶段计划和历史记录。

## 当前权威文档

| 文档 | 作用 |
| --- | --- |
| [TURNING_GOOD_AGENT_SPEC.md](./TURNING_GOOD_AGENT_SPEC.md) | 持续更新的完整产品与技术规格说明，描述最终目标、当前状态和模块边界。 |
| [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md) | 当前仓库的真实代码结构说明，用于快速理解每个目录和文件职责。 |

## 阶段实施计划

| 阶段 | 文档 | 状态 |
| --- | --- | --- |
| Phase 1 | [Runtime MVP](./phases/2026-06-15-phase-1-runtime-mvp.md) | 已基本完成 |
| Phase 2 | [真实 LLM、Tool Calling、CLI 流式输出与基础工具](./phases/2026-06-15-phase-2-real-llm-tool-calling.md) | 已完成 |
| Phase 3 | [四个轻量 Hooks](./phases/2026-06-15-phase-3-hooks.md) | 已完成；含 Channel 输出、会话工具权限和只读 Turn Monitor |
| Phase 4 | [MCP Client、审批与 Runtime 收口](./phases/2026-06-15-phase-4-mcp-client.md) | 已完成；支持 stdio、Streamable HTTP、Catalog、全局审批、当前轮附件、后台 Worker 与连接级重试 |
| Phase 5 | [Skills 机制](./phases/2026-06-15-phase-5-skills.md) | 已完成 |
| Phase 6 | [Web Channel 与会话观测工作台](./phases/2026-06-15-phase-6-web-observability.md) | 已完成；含稳定重连与单次重试、上下文占用环、按轮次收拢的结构化检查器、真实事件活动流、全局权限菜单与双主题桌面验收 |
| Phase 7 | [主动能力与长期记忆](./phases/2026-06-15-phase-7-proactive-memory.md) | 已实现；含唯一 Gateway、共享 MessageBus、ChannelManager 与主动通知 Fanout；CLI 是 `tga chat --session ...` Client，Web 为 Gateway 内服务 |
| Phase 8 | [飞书与个人微信 IM Channel](./phases/2026-06-15-phase-8-im-channel-adapters.md) | 契约已确认，计划中；复用唯一 Gateway，首轮只支持一对一文本私聊 |
| Phase 9 | [Multi-Agent 协作模式](./phases/2026-06-15-phase-9-multi-agent.md) | 计划中；在 IM Channel 之后实现 |

## 历史文档

| 文档 | 说明 |
| --- | --- |
| [archive/2026-06-11-phase-1-runtime-mvp-design.md](./archive/2026-06-11-phase-1-runtime-mvp-design.md) | Phase 1 Runtime MVP 的设计快照，已归档为历史记录。 |

## 维护规则

1. 代码边界发生变化时，先更新 `PROJECT_ARCHITECTURE.md`。
2. 产品目标、模块职责或阶段路线变化时，更新 `TURNING_GOOD_AGENT_SPEC.md`。
3. 每进入一个新阶段，先更新对应 phase 文档，再开始实现。
4. 已完成阶段不要删除，改为记录“已完成范围”和“遗留问题”。
