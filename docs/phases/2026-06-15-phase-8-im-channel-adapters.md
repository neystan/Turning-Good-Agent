# Turning-Good-Agent Phase 8 IM Channel 接入契约

> 状态：**契约已确认，尚未实施。** 本阶段只接入飞书与个人微信；若本文与当前代码冲突，以代码为准。
>
> Phase 7 的 Gateway 是唯一 Host。IM 只能是高内聚、低耦合的收发 Adapter，绝不创建第二套 Runtime、AgentLoop、MessageBus、Scheduler、ProactiveService 或持久化领域服务。

## 1. 目标与范围

一个本机 `<data_dir>` 仍只有一个 `tga gateway`。它复用同一 `AgentRuntime`、`AgentLoop`、共享 `AsyncMessageBus`、`ChannelManager` 与主动能力服务，同时提供：

- 本机 CLI 与仅 loopback 的 Web；
- 一个多实例飞书 Transport，供 Gateway Owner 的多个官方 Bot 使用；
- 一个多绑定个人微信 Transport，供 Owner 和已批准的微信用户各自的 Bot 使用。

Phase 8 v1 只支持一对一文本私聊。图片、文件、语音、视频的描述字段可以保留在协议边界，但不会下载、转写、进入模型或写入会话。群聊、公共 Webhook、公网部署、浏览器 Push、跨 Gateway 共享、远程设置管理、自动创建飞书应用、微信媒体协议与多 Agent 均不属于本阶段。

## 2. 固定拓扑与职责

```text
飞书官方 WebSocket / 个人微信 iLink HTTP long-poll
        -> 各平台 Transport 的凭据、鉴权、去重、绑定与协议解析
        -> 规范 InboundMessage
        -> Gateway 共享 Inbound MessageBus
        -> 唯一 AgentRuntime + AgentLoop
        -> 明确 Recipient 的 OutboundMessage
        -> ChannelManager
        -> 同一平台 Transport.send()
```

- `feishu` 与 `weixin` 各是一个长期存活的 `ChannelTransport`；一个 Transport 在内部管理多个 Bot/Binding，而不是每个账号创建 Gateway 或 Bus。
- `ChannelRouter` 只按稳定的 `channel` 创建单轮 `ChannelAdapter`。Adapter 不直接调用 `AgentRuntime.run_turn()`、`ProactiveExecutor` 或任何领域 Manager。
- `ChannelManager` 仍是唯一出站消费者；Transport 的 `send()` 有界且返回 `True` 仅表示平台接受发送请求，绝不表示用户已读。
- 外部协议的 token、cursor、`context_token`、签名、App Secret 和二维码状态都停留在 Transport/账号注册表中，不进入 Bus、LLM、会话、Trace、Tool 参数或日志正文。

## 3. 主体、会话与本地可见性

| 场景 | `principal_id` | 普通聊天会话 | 主动能力、设置与长期画像 |
| --- | --- | --- | --- |
| Owner 的 CLI、Web、飞书 Bot、Owner 微信 Bot | 现有 Gateway Owner | Channel 与 conversation 各自隔离 | 全部共享 Owner 的状态 |
| 一个非 Owner 微信 Binding | 新生成的稳定本地主体 | 仅该 Binding 的微信私聊 | 只属于该主体；不能进入 CLI、Web 或飞书 |

飞书的每个 Bot 相当于 Owner 的一个独立会话：使用 `channel="feishu"`，并以 `<bot_instance_id>:<chat_id>` 作为 `conversation_id`。线程相关的 `root_id`、`parent_id`、`thread_id` 只保留在 metadata，v1 不拆成新的 Session。

微信一对一 Binding 使用 `channel="weixin"` 和稳定的内部 `binding_id` 作为 `conversation_id`；外部 `from_user_id` 仅保存在绑定私有状态，不能成为 Session 文件名。Gateway 必须继续通过 `(principal_id, channel, conversation_id)` 派生不泄露外部标识的 `session_id`，并把 `GatewayTurnCoordinator` 的串行路由键扩大为同一三元组，不能只按 `(channel, conversation_id)` 串行。

为避免迁移现有 Owner 数据，Owner 继续使用现有数据根；新的非 Owner 主动状态、画像、Cron、Breakbeat、Skill Observation/Draft、Incident 与订阅放在独立的 opaque principal 命名空间。单一 `ProactiveService` 与 Scheduler 必须通过按 `principal_id` 解析状态根的 Resolver 处理全部主体；每条主动持久化记录都显式归属一个主体，`run_catalog_action()` 必须把来源路由传入解析器，Dream/Breakbeat 的会话遍历必须带主体过滤。不得继续把全局 `memory/`、`proactive/` 快照或无过滤的 `list_sessions()` 当作所有用户共享的状态，更不得为每个账号创建第二个主动服务或调度器。逻辑隔离是运行时边界，不是对本机文件所有者的新 ACL：Gateway Owner 本来就能直接查看自己的 `data_dir`、源码和会话记录，不新增“Owner 只读其他用户数据”的 API 层。

## 4. Channel Account Registry

账号与 Binding 是独立于 `settings.local.json` allowlist 的本地 Registry。它由本机 Web/CLI 的 Owner 控制面读写；状态接口只返回脱敏值，IM 用户不能修改它。建议每个账号单独存放在 `<data_dir>/channels/<platform>/<account_or_binding_id>.json`，不引入数据库。

通用字段为 `id`、`platform`、`principal_id`、`status`、`created_at`、`updated_at`、`enabled`、订阅状态与私有协议状态。凭据更新或撤销必须保留稳定的 `id` 与对应聊天/主体数据：撤销只停止 Transport；重新扫码或轮换凭据复用原 `principal_id` 与会话。

| 平台 | Registry 私有字段 | 生命周期 |
| --- | --- | --- |
| 个人微信 | `bot_token`、base URL、`ilink_bot_id`、轮询 cursor、已锁定 `from_user_id`、最近 `context_token` 与时间戳 | `pending_qr` → `awaiting_first_dm` → `active`；另有 `expired`、`failed`、`revoked` |
| 飞书 | `instance_id`、`app_id`、`app_secret`、域、已锁定 Owner `open_id`、CardKit 能力与连接状态 | `awaiting_owner_code` → `active`；另有 `disabled`、`failed`、`revoked` |

重新扫码失败时不得删除仍在工作的微信凭据；飞书 App Secret 轮换失败时也保留旧连接。凭据本身、二维码和完整平台响应永不返回浏览器或写进聊天历史。

## 5. 规范消息契约

所有被接受的文本私聊都必须构造现有的 `InboundMessage`：

| 字段 | IM 规则 |
| --- | --- |
| `id` | 平台、内部 Binding/实例和平台 event/message ID 组合出的全局稳定 ID；用于 Adapter 去重，不使用易变的显示名。 |
| `route` | 完整 `principal_id`、`channel`、`conversation_id` 与 Gateway 派生的 `session_id`。 |
| `content` | 原始文本内容；空文本不进入 Runtime。 |
| `attachments` | v1 始终为空；未来只放已验证、已下载的描述符。 |
| `metadata` | 仅放不敏感的协议字段、平台时间、message/event ID、会话类型、thread/mention 摘要和内部实例/Binding ID。 |

`OutboundMessage` 必须携带一个明确 `Recipient(principal_id, channel, conversation_id)`。Transport 只能使用 Recipient 查本地 Registry，不得从 `session_id`、文本或模型 metadata 反推出外部账号。普通回复使用 `disposition="chat_reply"`；主动通知使用 `"proactive_notification"`，两者都不写回普通聊天历史。

每个 Adapter 对重复 event/message ID 做持久化的常规去重，并持久化必要 cursor。Gateway 不提供崩溃后的外部入站 exactly-once 承诺；测试必须证明重复轮询或平台重投时不会在正常运行中创建第二个 Agent 回合。

## 6. 个人微信 Adapter

个人微信使用 iLink HTTP long-poll，二维码登录获取 Bot 凭据，不依赖本机微信桌面客户端。该协议不是官方 Bot API，因此是**实验性、显式启用**的 Adapter；它不承诺账户长期可用或平台级可靠送达。

1. Owner 在本机 Web/CLI 为一个既定主体创建一次性邀请。非 Owner 会得到新主体；Owner 自己的 Binding 使用 Owner 主体。
2. 指定用户扫描本机展示的二维码。二维码过期、取消或失败不会创建 Agent 回合；多用户独立 Binding 在实现前必须先通过双二维码、双 long-poll 的可行性验证。
3. 扫码成功后 Binding 处于 `awaiting_first_dm`。`ilink_user_id` 只作诊断，不能假定等于未来消息的 `from_user_id`。
4. 用户向新 Bot 发出的第一条**文本私聊**锁定该 `from_user_id`；未锁定、其他发件人、群聊和非文本消息均不能进入共享 Bus。非文本只返回“当前仅支持文本”。
5. 原始入站至少读取 `message_id`/`seq`、`from_user_id`、`message_type`、`item_list`、`create_time_ms` 与 `context_token`。`context_token` 按 Binding 私有缓存，绝不进入 metadata。

微信没有原生增量输出：忽略 `response.delta`、工具状态和 reasoning，只发送最终文本；超过平台上限时按不破坏 UTF-8 的文本边界分片。发送需要当前有效的 `context_token`。缺失、过期、未知或发送失败即视为该 Binding 离线：不重跑任务、不重试、不补发；下一条有效入站可以恢复可投递状态。Bot 凭据本身失效时标记 Binding `expired` 并要求重新扫码。

## 7. 飞书 Adapter

Owner 先在飞书开放平台创建并配置官方 Bot，再从本机 Web/CLI 登记它的 `app_id`、`app_secret`、`instance_id` 与域。一个 Gateway 可登记多个不同 App 身份的 Bot；Gateway 不自动创建、审核或发布飞书应用。

- 接收采用官方 WebSocket 长连接，不开放公网 Webhook。每个 Bot 的连接、重连与凭据只在 `feishu` Transport 内部管理。
- 原始入站保留 `event_id`/`message_id`、`sender.open_id`、`chat_id`、`chat_type`、`message_type`、`parent_id`、`root_id`、`thread_id` 和 mentions 的非敏感摘要。只接受 Owner 的 `p2p` 文本私聊。
- 新 Bot 先处于 `awaiting_owner_code`。本机控制面生成一次性验证码，Owner 从飞书私聊该 Bot 发送验证码后锁定 `sender.open_id`；在此之前不进入 LLM，其他私聊和所有群聊一律拒绝。
- 每个 Bot 使用自己的 `instance_id`，凭据轮换或重连复用该 ID 与对应会话。不同实例不得复用同一个飞书 App 身份。
- 有 CardKit 写入权限时，用一张更新式 CardKit 卡流式展示普通回复；卡片不展示模型 reasoning、Tool 参数或 Tool 状态。没有该权限时，该 Bot 单独降级为最终文本，不影响其他 Bot。平台 API 接受发送即为 Transport 成功；失败不重跑 Agent 或补发。

## 8. 工具、主动能力与投递

IM v1 不提供 Web 的运行中 guidance、停止控制或人工 Tool 审批。任何配置为 `approval_required` 或声明 `approval_required=True` 的 Tool 都必须在飞书与微信的可用 Catalog 中隐藏，和 Cron 的审批过滤一样；不能因为消息文本提到 Tool 名称而绕过该规则。Gateway 必须先按可信 Binding/Route Tool Policy 过滤 `AgentLoop` 可见的 Tool schema，再把同一 allowlist 传给执行层二次校验；不能依赖 `SilentChannelAdapter`、审批 Hook 或全局自动审批开关来实现隐藏。非审批 Tool 仍经过既有安全预检和 `ToolExecutor` 二次检查。

Owner 从任一 Owner Channel 调用主动 Tool 时，使用 Owner 共享的 Cron、Breakbeat、Dream、Skill 与 Incident 状态；飞书每个 Bot 的普通聊天上下文仍独立。非 Owner 微信用户只能运行其自身主体范围的主动能力，`scope=global` 也只表示该用户自己的全部数据。主动 Tool 不因为接入 IM 自动进入 Slash 菜单。

- 手动主动任务固定 `origin`：仅向触发它的同一 Bot/Binding 回送结果。
- Cron、自动任务和 Incident 固定 `all_subscribed`：只在同一 `principal_id` 内向已显式订阅且当前可投递的 Bot/Binding Fanout；任务只执行一次。
- 飞书的已订阅 Bot 可直接尝试投递；微信必须同时具有有效 Binding 和 `context_token`。目标离线、未知、超时或发送失败只跳过该目标，不影响其他目标。
- 所有主动结果保持在 `ProactiveResultEvent` → `OutboundMessage` 路径，不写入 `messages.jsonl`、摘要、上下文、画像或 reasoning 展示。

## 9. 与 CLI/Web 的差异

| Channel | 连接与身份 | 普通输出 | 审批与控制 | 主动接收条件 |
| --- | --- | --- | --- | --- |
| CLI | 本机认证 WebSocket Client；Owner | 终端文本 delta | 现有 CLI `y/N`、停止语义保持不变 | 当前在线路由 |
| Web | 仅 loopback；Owner | WebSocket 流与工作台 | 现有审批卡、停止、guidance 保持不变 | `#proactive` 与网页内 notice |
| 飞书 | 官方多 Bot WebSocket；每 Bot 锁定 Owner | CardKit 流式；缺权限降级最终文本 | 隐藏审批 Tool；无远程设置/审批 | 已显式订阅且平台可发送 |
| 个人微信 | 每 Binding 的 iLink long-poll；首条私聊锁定用户 | 仅最终文本，按上限分片 | 隐藏审批 Tool；无远程设置/审批 | 已订阅且有有效 `context_token` |

## 10. 最小实现边界与验收

实现只允许扩展 Channel、Gateway 装配、规范路由、按主体的主动状态解析和本机账号控制面。优先新增平台私有 Transport/Registry；复用 `channels/base.py`、`channels/manager.py`、`bus/messages.py`、`gateway/routing.py` 与现有 `NotificationFanout`，不得把平台业务塞进 Runtime 或 Proactive Manager。`gateway/host.py` 仍是唯一装配点，且它的订阅查询必须从 Binding Registry 为所有主体解析目标，不能只返回当前 Gateway Owner 的 Web/CLI 目标。

IM 入站必须经 `GatewayTurnCoordinator.submit()` 再进入共享 Bus，不能直接发布入站消息。每个 IM 的 `InboundMessage.id` 都需要平台、内部实例/Binding 和平台 ID 的命名空间；每次 `response.completed` 或 `response.error` 的投递尝试结束后（包括发送失败）都必须调用 `complete_route_turn()` 或等价的 `ChannelManager` listener，确保同一路由的后续消息不会永久排队。

在任何功能被标记完成前，至少需要证明：

1. 两个个人微信二维码可并发完成登录，并各自 long-poll、发消息、保留 cursor/`context_token` 且不串凭据；若此门槛失败，微信多 Binding 保持未实现，不能退化成共享 Bot。
2. 飞书两个 Bot 与微信两个 Binding 都只产生一个 Gateway Runtime/Bus/ChannelManager；第二个 Gateway 仍被锁拒绝。
3. 未绑定身份、错误验证码、群聊、重复 event/message、非法 payload 和非文本输入都不进入 AgentRuntime。
4. Owner 的跨 Channel 主动状态共享、普通聊天隔离；两个非 Owner 微信用户的普通聊天、画像、Cron 和通知完全隔离。
5. `approval_required` Tool 不在 IM Catalog；最终文本不会泄露 reasoning、凭据或平台私有字段。
6. 正常回复定向回原 Bot/Binding；主动 Fanout 每次只执行一次；微信无有效 `context_token`、飞书/微信发送失败都不导致重跑或补发。
7. Gateway、CLI、Web 既有回归继续通过，并补充 Registry 生命周期、身份锁定、去重、路由、投递失败和多账号隔离测试。

## 11. Phase 9 边界

Phase 9 才讨论用户显式开启的 Multi-Agent planner/worker 协作。它不得改变 Phase 8 的单 Gateway、单 Runtime、消息路由、主体隔离或 IM Transport 所有权。
