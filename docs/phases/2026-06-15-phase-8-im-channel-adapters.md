# Turning-Good-Agent Phase 8 IM Channel 接入契约

> 状态：**已实施。** 当前实现基线为 `bd9b110`（`refactor: simplify IM binding lifecycle`）。若本文与当前代码冲突，以代码为准。
>
> 一个本机 `<data_dir>` 只允许一个 `tga gateway`。飞书和个人微信只是接入同一 Gateway 的 Transport，绝不创建第二套 `AgentRuntime`、`AgentLoop`、`AsyncMessageBus`、Scheduler、`ProactiveService` 或持久化领域服务。

## 1. 目标与范围

Phase 8 在现有 Gateway 上接入飞书与个人微信，使它们与 CLI、Web 复用同一消息总线、Runtime、主动能力和出站 `ChannelManager`。

- 飞书使用官方 WebSocket 长连接，支持一个 Gateway 下的多个 Owner Bot。
- 个人微信使用 iLink HTTP long-poll，支持 Owner Binding 和独立主体 Binding。
- v1 仅接受一对一文本私聊。图片、文件、语音和视频不下载、不转写、不进入模型或聊天历史；群聊不进入 Runtime。
- 飞书与微信只发送最终文本。没有流式卡片、CardKit 写入、工具状态、模型 reasoning 或思维链展示。
- 公网 Webhook、远程控制面、浏览器 Push、跨 Gateway 共享、微信媒体协议、自动创建飞书应用和多 Agent 不属于本阶段。

## 2. 固定拓扑与职责

```text
飞书官方 WebSocket / 个人微信 iLink HTTP long-poll
        -> 平台 Transport（凭据、鉴权、去重、Binding、协议解析）
        -> 规范 InboundMessage
        -> Gateway 共享 AsyncMessageBus
        -> 唯一 AgentRuntime + AgentLoop
        -> 明确 Recipient 的 OutboundMessage
        -> ChannelManager
        -> 原平台 Transport.send()
```

- `FeishuTransport` 和 `WeixinTransport` 各是一个长期存活的多账号 Transport；账号或 Binding 不会各自启动 Gateway、Bus 或 Runtime。
- `ImGatewayCoordinator` 先将已验证 IM 输入提交给 `GatewayTurnCoordinator`，再进入共享 Bus；同一路由按 `(principal_id, channel, conversation_id)` 串行。
- `ChannelManager` 是唯一出站消费者。`Transport.send()` 返回 `True` 仅代表平台接受发送请求，不代表用户已读。
- IM Adapter 丢弃 `response.delta`、运行状态和工具生命周期，只让终态 `response.completed` 或 `response.error` 投递到平台。
- token、cursor、`context_token`、签名、App Secret、二维码内容和外部用户标识只留在 Transport 或本地账号记录，不进入 LLM、聊天正文、Trace、Tool 参数或日志正文。

## 3. 主体、会话与数据隔离

| 场景 | `principal_id` | 普通聊天会话 | 画像和主动状态 |
| --- | --- | --- | --- |
| Owner 的 CLI、Web、飞书 Bot、我的微信 | Gateway Owner（默认 `local-user`） | 仍按 Channel 和 conversation 隔离 | 共享 Owner 数据 |
| 一个独立微信用户 | 新生成的稳定本地主体 | 只属于该微信 Binding | 完全私有 |

- Owner 可以被多个 Channel/飞书 Bot 共享；当前 Registry 同时限制只能有一个 Owner 微信 Binding，避免重复“我的微信”账号。
- 独立主体必须与单一微信 Binding 一一对应，不能被其他 Binding 复用。它不能进入 Owner 的 CLI、Web 或飞书。
- 飞书会话使用 `channel="feishu"` 和 `<bot_instance_id>:<chat_id>` 作为 `conversation_id`；线程字段只作为 metadata，不拆分 Session。
- 微信会话使用 `channel="weixin"` 和稳定内部 `binding_id` 作为 `conversation_id`。外部 `from_user_id` 绝不成为 Session 文件名。
- Gateway 从完整三元组派生 opaque `session_id`。Owner 继续使用原数据根；独立主体使用 `<data_dir>/principals/<opaque-hash>/` 下的独立会话、画像和主动状态根。

## 4. Channel Account Registry 与本机控制面

账号和 Binding 存在 `<data_dir>/channels/<platform>/<id>.json`，独立于 `settings.local.json` allowlist。Registry 使用原子文件替换和本地锁保存稳定 `id`、`principal_id`、生命周期状态、订阅状态和平台私有状态。

- 公共状态包含 `pending_qr`、`awaiting_first_dm`、`awaiting_owner_code`、`connecting`、`active`、`disabled`、`expired`、`failed`、`revoked`。
- 微信私有状态可包含 Bot 凭据、cursor、已锁定的 `from_user_id`、`context_token`、`binding_epoch` 和入站去重 ID；飞书私有状态可包含 App 凭据、Owner `open_id`、验证码哈希和连接状态。
- `GET /api/control/channels` 只返回脱敏视图：账号 ID、平台、主体类别、状态、启用/订阅/连接状态及飞书 App ID 掩码。它不返回凭据、外部用户标识、cursor、二维码或候选重扫状态。
- 二维码只存在微信 Transport 内存和本机控制面 `GET /api/control/channels/weixin/{id}/qr`，响应带 `Cache-Control: no-store`；它不会写入 Registry 或聊天历史。

## 5. 规范消息与投递契约

所有被接受的 IM 私聊文本都构造既有 `InboundMessage`：

| 字段 | 规则 |
| --- | --- |
| `id` | 平台、内部 Binding/实例和平台 event/message ID 组成的稳定 ID。 |
| `route` | 完整 `principal_id`、`channel`、`conversation_id` 和派生 `session_id`。 |
| `content` | 原始非空文本。 |
| `attachments` | v1 始终为空。 |
| `metadata` | 仅非敏感平台字段、时间、消息 ID、会话类型、线程/mention 摘要及内部实例 ID。 |

`OutboundMessage` 必须携带明确的 `Recipient(principal_id, channel, conversation_id)`。普通回复使用 `disposition="chat_reply"`，主动通知使用 `"proactive_notification"`；两者都不回写普通聊天历史。

- 每个平台通过 Registry 持久化入站 event/message ID 去重。正常运行中重复投递不会创建第二个 Agent 回合，但不承诺崩溃后的外部 exactly-once。
- 终态投递无论成功或失败都会释放该路由的 FIFO，避免后续同路由消息永久排队。
- Transport 只能用 Recipient 查询本地 Registry，不能由文本、模型 metadata 或 `session_id` 反推外部账号。

## 6. 个人微信 Binding 与重扫

个人微信是实验性、显式启用的 iLink Adapter，不依赖本机微信桌面客户端，也不承诺平台级可靠送达。

### 初始绑定

1. Owner 在本机控制面创建“我的微信”，或创建“邀请独立用户”。
2. Binding 进入 `pending_qr`，本机 Web 轮询短时内存二维码。
3. 扫码登录成功后进入 `awaiting_first_dm`。`ilink_user_id` 仅作诊断，不能代替消息的 `from_user_id`。
4. 第一条有效文本私聊原子写入 `from_user_id` 和 `context_token`，Binding 变为 `active`。首信前的其他消息、群聊、空文本和非文本均不进入共享 Bus。
5. 锁定后，其他微信用户的后续消息直接忽略；非文本可收到“当前仅支持文本”的平台提示，但不触发 Runtime。

### 重扫与归属权转移

重扫只适用于 `active`、`expired` 或 `revoked` 微信 Binding，且由 `WeixinTransport` 自己管理：

1. 点击“重新扫码”只创建内存候选登录和候选二维码。旧用户、旧凭据和旧 long-poll 在候选成功前继续工作。
2. 候选二维码关闭、取消或候选凭据失败时，候选被丢弃；二维码过期时可继续请求下一张二维码。无论哪种候选失败路径，旧 Binding 都不变。
3. 候选凭据到达 `awaiting_first_dm` 后立即原子切换，不等待旧回合空闲，也不经 Gateway Host callback 或 Lease。
4. 切换保留 Binding ID、`principal_id`、既有聊天、画像、主动状态和 Draft Skills，但清除旧 `from_user_id`、`context_token`、cursor、conversation 私有状态和入站去重记录，并递增 `binding_epoch`。
5. 新用户的第一条有效文本私聊才锁定新的 `from_user_id`。仅完成扫码不算归属转移。
6. 旧用户的后续入站会被忽略；旧回合的终态回复携带旧 epoch，Transport 会丢弃它，绝不能发送给新用户。

停用、撤销或永久删除会取消当前候选重扫。重扫候选本身不写入 Registry，因此控制面账号列表始终显示旧 Binding 的持久化状态；二维码端点会在候选期间临时报告 `pending_qr`。

微信只发送最终文本，并按 UTF-8 边界分片。发送需要当前有效 `context_token`；缺失、过期、未知或发送失败只把该 Binding 视为不可投递，不重跑任务、不补发。Bot 凭据失效时 Binding 标记为 `expired`，可重新扫码。

## 7. 飞书 Bot

Owner 先在飞书开放平台创建官方 Bot，再在本机控制面登记 `app_id`、`app_secret` 与域。一个 Gateway 可登记多个不同 App 身份的 Bot；Gateway 不会自动创建、审核或发布飞书应用。

- 接收使用官方 WebSocket 长连接。每个 Bot 的连接、重连与凭据只在 `FeishuTransport` 内部管理。
- 只接受 Owner 的 `p2p` 文本私聊。新 Bot 先为 `awaiting_owner_code`，Owner 在本机设置六位一次性验证码后，从飞书私聊该验证码以锁定 `sender.open_id`；此前所有消息都不进入 LLM。
- 每个 Bot 使用稳定的 `instance_id`。轮换 App 凭据先验证候选连接；失败时保留旧连接。若 App 身份变化，Owner 验证需要重新完成。
- 飞书没有 CardKit 或流式卡片表面。增量被忽略，普通回复和主动通知都以一次最终纯文本发送；不显示 reasoning、工具名称、参数或状态。

## 8. 工具、主动能力与安全

IM v1 不提供 Web 的运行中 guidance、停止控制或人工 Tool 审批。

- 飞书和微信在 Runtime 命令入口拒绝 `/new`、`/exit` 与所有 `/approve*` 变体；拒绝提示不创建或清理会话、不修改全局审批设置，也不写入聊天历史。需要清空当前 IM 话题时，用户可显式发送既有 `/clear`。
- `DefaultChannelToolPolicy` 对飞书和微信从本轮 Tool Catalog 中移除所有配置为 `approval_required` 或声明 `approval_required=True` 的 Tool。
- 执行层仍受既有安全预检和 `ToolExecutor` 二次检查约束；`ImChannelAdapter` 对意外审批请求作防御性拒绝，不能被全局自动审批开关绕过。
- Owner 从任一 Owner Channel 运行主动 Tool 时共享 Owner 的 Cron、Breakbeat、Dream、Skill 与 Incident 状态，普通聊天上下文仍隔离。
- 独立微信主体只能访问其自身范围的主动能力；`scope=global` 仅代表该独立主体的全部数据。
- 手动主动任务固定回送到 origin Bot/Binding；Cron、自动任务和 Incident 按 `all_subscribed` 在相同 `principal_id` 内对已订阅、当前可投递的目标 Fanout。任务只执行一次，离线或发送失败目标不重跑、不补发。
- 主动结果保持在 `ProactiveResultEvent -> OutboundMessage` 路径，不进入 `messages.jsonl`、摘要、上下文、画像或 reasoning 展示。

## 9. 停用、撤销与永久删除

“停用并保留”只停止账号的收发，保留 Binding、凭据和现有数据；“撤销”将账号变为不可启用/订阅的 `revoked` 记录。两者均不等同于永久删除。

永久删除通过 `DELETE /api/control/channels/{platform}/{id}` 执行，并先对精确 IM 路由取得空闲保留：

- 若该 Binding 正在处理消息、同路由有待处理消息，或等待最终回复投递，后端返回 `409 Conflict`，不删除任何账号、凭据或聊天。错误码为 `binding_busy`，前端确认弹窗保持打开并显示“该 Binding 正在处理消息或等待回复投递，暂时不能删除。请等待本轮完成后再试”。
- 删除 Owner Binding 时，只删除该 Binding 的凭据和精确匹配的 IM 聊天记录。Owner 的共享画像、主动状态、CLI/Web 数据和其他 Channel/Binding 保留。
- 删除独立主体 Binding 前必须验证该主体只被此 Binding 使用；若完整性异常，返回 `409 independent_principal_integrity_error`，不做任何持久化改动。
- 成功删除独立主体 Binding 时，会退休其后台工作区，并删除其全部私有数据：聊天、画像、主动状态和 Draft Skills。不存在“最后一个 Binding”判断。

永久删除使用路由空闲门保护删除原子性；这套机制不参与微信重扫，也不会把旧用户切换阻塞在 `waiting_for_idle`。

## 10. 本机 REST 与 Web 工作面

Channel 控制面仅由本机 Owner 使用，现有接口包括：

- `GET /api/control/channels`
- `GET /api/control/channels/{platform}/{account_id}`
- `POST /api/control/channels/weixin/invitations`
- `POST /api/control/channels/weixin/{account_id}/rescan`
- `GET /api/control/channels/weixin/{account_id}/qr`
- `POST /api/control/channels/feishu`
- `POST /api/control/channels/feishu/{account_id}/owner-code`
- `POST /api/control/channels/feishu/{account_id}/credentials`
- `POST /api/control/channels/{platform}/{account_id}/enable`
- `POST /api/control/channels/{platform}/{account_id}/disable`
- `POST /api/control/channels/{platform}/{account_id}/revoke`
- `POST /api/control/channels/{platform}/{account_id}/subscribe`
- `POST /api/control/channels/{platform}/{account_id}/unsubscribe`
- `DELETE /api/control/channels/{platform}/{account_id}`

设置页将微信入口称为“我的微信”和“邀请独立用户”，并明确 Owner 共享状态与独立主体私有状态的边界。二维码轮询只在初始扫码或重扫期间运行；候选二维码失败或过期不会清除旧 Binding。

## 11. 验收基线

截至 `bd9b110`，已记录的聚焦验收包括：

1. Python 聚焦测试：`88 passed, 17 skipped, 1 deselected, 1 warning`。
2. Python `compileall` 通过。
3. `pnpm run build` 通过。
4. 微信二维码相关 Playwright 用例：`2 passed`。
5. Docker 后端与前端可启动，`/api/control/channels` 仅返回脱敏状态且不暴露 `rebind_state`。

关键回归覆盖多 Binding 隔离、首条私聊锁定、候选失败保留旧账号、epoch 丢弃旧终态、飞书纯文本终态、IM Tool 审批过滤、主动通知、Owner/独立主体删除以及忙碌删除 409。本次文档同步未重新执行完整测试套件。

## 12. 已废弃的中间方案

以下描述不再是实现依据：

- `gateway/binding_lifecycle.py`、Binding Lease、Host/Runtime 重扫回调和候选提交预约；
- 微信 `waiting_for_idle` 状态、旧用户切换前等待回合结束、候选重试空闲预约；
- 飞书 CardKit、流式卡片、`cardkit_enabled` 和任何卡片更新 API。

当前实现只保留一个 Gateway 的 Route FIFO 和永久删除空闲门；微信候选切换完全收敛在 `WeixinTransport`。

## 13. 后续阶段边界

Phase 9 如讨论显式开启的 Multi-Agent planner/worker 协作，也不得改变本阶段的单 Gateway、单 Runtime、共享消息总线、主体隔离、IM Transport 所有权或无 reasoning 展示边界。
