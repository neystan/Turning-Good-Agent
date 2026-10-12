# Turning Good Agent 只读代码审查

审查日期：2026-10-12。对象：`X:\download\claude\workspace\TGA`，基准提交 `71a165b`。本文面向后续重构智能体；路径均相对这个仓库，冒号后的数字是该提交中的一基行号，范围表示同一实现块。修改后请重新定位函数，不要机械沿用旧行号。

“已核实”表示已阅读实现及有关调用者，确认本文描述的代码行为；不等于已在真实平台重现。“推测”表示影响依赖尚未验证的环境或时序。拆除表中的处理方式是建议实施方案，不表示本次已经修改产品。遵循 `AGENTS.md:3–10` 的简单实现、职责分离、直接删除废弃路径原则；本次用户的只读约束优先于 `AGENTS.md:14,26–47` 的修改、更新状态及提交流程。未读取或输出 `settings.local.json`，未启动/停止服务、未访问本机 Gateway、未操作 Docker。

## 1. 总体评价

1. **已核实**：Gateway 已把四种渠道收口到同一前台 Runtime 和入出站总线，值得保留这条可运行的主路径（`gateway/host.py:81–187`、`gateway/turns.py:202–244`；本节省略的包前缀为 `Turning-Good-Agent/`）。
2. **已核实**：主要复杂性来自可选功能侵入核心：普通 AgentLoop 专门识别委派，Session Store 解析协作拓扑，Channel 基础协议包含协作回调（`runtime/agent_loop.py:313–416`、`sessions/store.py:290–573`、`channels/base.py:53–55`）。
3. **已核实**：关闭多智能体仍会构造 WorkerRunner 和第二个 AgentLoop，因此配置开关没有形成装配边界（`runtime/runtime.py:106–109`、`multi_agent/coordinator.py:74–75`、`multi_agent/worker_runner.py:49–85`）。
4. **已核实**：Skill 沉淀同时绑定 SAVE 事件、主体 workspace、后台调度、通知、REST 和前端领域页面，必须按第 5 节整体拆除（`proactive/service.py:232–250,415–438,528–574,676–703`）。
5. **已核实**：主体隔离没有覆盖共享文件及网络工具，非所有者微信主体可能绕过会话读取器读取所有者数据，这是首要安全问题（`gateway/principals.py:93–103`、`channels/policy.py:55–59`、`tools/security.py:107–111,132–156`）。
6. **已核实**：另有 Windows 误终止进程、配置应用撤销默认审批、压缩集合错误等高风险缺陷，不能把它们仅归因于架构“不够优雅”（第 6 节 H1–H6）。
7. **已核实**：前端已有历史请求版本闸门，但快照恢复、终态全文、附件归属和部分检查器请求没有贯彻同一规则（`web/frontend/src/App.tsx:188–278,465–496`、`web/frontend/src/state/session_state.ts:64–72,233–237,354–361`）。
8. **已核实**：测试量大，但包含不存在模块的导入、旧入口契约、硬编码临时编译产物和已跳过的旧绑定方案，不能用一个总通过率衡量当前产品（第 6 节 M15、L2）。
9. **推测**：对单人维护最有收益的顺序是先修复高风险边界，再删除两项功能，再收紧现有装配和存储职责；本报告不假设需要新渠道、分布式执行、插件平台或迁移兼容层（依据 `AGENTS.md:3–17` 与上述实际耦合）。

## 2. 模块依赖

### 2.1 依赖关系表

**已核实**：对全部 Python 源文件做 AST 相对导入检查，再核对运行时装配、函数内导入和 `TYPE_CHECKING`。下表箭头表示“左侧使用右侧”；同包内部依赖和第三方库不展开。`web` 分为后端控制面和附件，避免把附件误当作 HTTP 服务。代表位置给出实际导入行，不以架构文档替代代码。

| 包或入口 | 直接依赖的项目包 | 代表位置 |
| --- | --- | --- |
| `__main__` / `cli.py` | cli；config；运行 Gateway 时 gateway、web；CLI 模式解析还依赖 multi_agent | `Turning-Good-Agent/__main__.py:1`；`Turning-Good-Agent/cli.py:12,128,463–464` |
| `bus` | 无其他项目包 | `Turning-Good-Agent/bus/messages.py:1–9`、`bus/queue.py:3` |
| `config` | 无其他业务包；settings 函数内调用自身 validate | `Turning-Good-Agent/config/settings.py:247–249` |
| `gateway` | bus、channels、config、llm、runtime、sessions、memory、proactive、web；Catalog 动作另用 context、observability | `Turning-Good-Agent/gateway/host.py:11–42`、`gateway/principals.py:7–14`、`gateway/catalog_actions.py:11–14` |
| `channels` | bus、config、llm、tools；具体实现还用 gateway.routing、multi_agent.events、web.backend.coordinator | `Turning-Good-Agent/channels/policy.py:5–7`、`channels/cli_gateway.py:10–15`、`channels/web.py:10–14` |
| `runtime` | bus、channels、config、context、hooks、llm、mcp、memory、observability、sessions、skills、proactive、multi_agent、gateway；state 直接用 web.attachments | `Turning-Good-Agent/runtime/runtime.py:6–38`、`runtime/state.py:7–18`、`runtime/agent_loop.py:6–17` |
| `context` | memory、sessions.types、sessions.token_counter | `Turning-Good-Agent/context/builder.py:7–9`、`context/token_budget.py:7–9` |
| `sessions` | bus、config、context.session_context；store 还依赖 multi_agent.events | `Turning-Good-Agent/sessions/manager.py:1–6`、`sessions/store.py:12–15` |
| `memory` | config、llm、sessions | `Turning-Good-Agent/memory/long_term.py:8–9`、`memory/short_term.py:3–5` |
| `tools` | config、sessions.token_counter；ToolLoader 动态扫描本包工具 | `Turning-Good-Agent/tools/builtin_tools.py:9`、`tools/context_attachment.py:4`、`tools/loader.py:25–60` |
| `skills` | config、context.system_prompt、sessions.token_counter、tools | `Turning-Good-Agent/skills/manager.py:6–7`、`skills/load_skill_tool.py:3–6`、`skills/skill_draft_tools.py:3` |
| `mcp` | config、sessions.token_counter、tools；无 runtime 反向调用 | `Turning-Good-Agent/mcp/manager.py:6–12`、`mcp/adapter.py:5`、`mcp/control_tools.py:4` |
| `hooks` | channels、llm、tools、sessions.token_counter；turn_monitor 实际依赖 runtime.turn_context，其余部分只作类型引用 | `Turning-Good-Agent/hooks/base.py:4–8`、`hooks/turn_monitor.py:1`、`hooks/tool_result_truncation.py:4–5` |
| `llm` | factory 用 config，其余为提供者协议、类型和 SDK 实现 | `Turning-Good-Agent/llm/factory.py:3`、`llm/openai_compatible.py:1–11` |
| `observability` | llm.types | `Turning-Good-Agent/observability/token_monitor.py:1` |
| `proactive` | bus、channels、llm、memory、runtime.principal_context / turn_context、sessions、skills、tools | `Turning-Good-Agent/proactive/service.py:12–44`、`proactive/workspace.py:6–15`、`proactive/executor.py:7–9` |
| `multi_agent` | channels、config、context、hooks、llm、memory、runtime、sessions、skills、tools | `Turning-Good-Agent/multi_agent/coordinator.py:14–27`、`multi_agent/worker_profile.py:8–13`、`multi_agent/worker_runner.py:7–18,50` |
| `web/backend` | bus、channels、config、context、gateway、llm、memory、sessions、proactive、multi_agent、web.attachments | `Turning-Good-Agent/web/backend/app.py:14–40`、`web/backend/coordinator.py:11–17`、`web/backend/proactive_control.py:7–12` |
| `web/attachments` | tools.base；解析器使用 pypdf/docx/openpyxl/pptx | `Turning-Good-Agent/web/attachments/tool.py:6`、`web/attachments/documents.py:100,124,130,140` |
| `web/frontend/src` | App 组装 api、组件、session/socket/proactive 状态；组件和状态依赖 types；HTTP/WS 使用既有 Gateway 接口 | `web/frontend/src/App.tsx:1–30`、`api.ts:1–15`、`state/socket_client.ts:1–10`、`proactive_api.ts:1–10` |

表内短路径均相对 `Turning-Good-Agent/`，前端行除外。以下结论均已核实。

### 2.2 循环依赖

1. **函数内导入形成的真实模块环**：`runtime.agent_loop:12 → multi_agent.delegate_tool:9 → multi_agent.coordinator:27 → multi_agent.worker_runner:50 → runtime.agent_loop`。最后一条是 `_build_execution()` 内的延迟导入，当前避免了顶层初始化报错，但并未消除相互依赖；删除多智能体后整环消失。
2. **Web 协议与实现的模块环**：`channels/web.py:14 → web/backend/coordinator.py`，后者 `register_runtime():168` 再导入 `channels.web`。同样通过函数内导入规避顶层问题。建议将当前 `WebTurnControl` 的定义和 Web Adapter 放在同一个 Web Channel 实现文件，Coordinator 只调度已有对象；不为两个使用者新建通用控制框架。
3. **包层相互依赖**：`runtime ↔ gateway`（`runtime/runtime.py:37,160–168`、`gateway/host.py:36`）、`runtime ↔ proactive`（`runtime/runtime.py:28`、`proactive/service.py:18`）、`runtime ↔ hooks`（`runtime/runtime.py:11–15`、`hooks/turn_monitor.py:1`）、`sessions ↔ context`（`sessions/manager.py:3`、`context/session_context.py:3`）。这些是包层职责环，不能全部称作运行时循环导入错误。
4. **更大的包强连通分量**包含 channels、context、gateway、hooks、mcp、memory、multi_agent、proactive、runtime、sessions、skills、tools、web。路径例如 `sessions → multi_agent → runtime → gateway → web → context → sessions`，可由上表行号逐边核对。聚合后的环说明目录分层已不能作为独立边界；不代表这 13 个包同时初始化失败。
5. 排除 `TYPE_CHECKING` 和函数内导入后，AST 检查未发现顶层模块强连通分量；该结论的范围是静态相对导入，不包括第三方内部导入和任意动态字符串导入。`ToolLoader` 的动态扫描另已阅读（`tools/loader.py:25–60`）。

### 2.3 不合理的依赖方向及最小处理

| 位置及核实状态 | 问题 | 当前需求下的处理 |
| --- | --- | --- |
| 已核实：`runtime/runtime.py:18–23,106–109`、`runtime/agent_loop.py:12,313–416` | 核心循环知道可选协作的工具名、deadline、审批包装和状态；禁用仍装配 | 按第 4 节直接删除，不加空 Coordinator、旧参数适配器或兼容模式 |
| 已核实：`sessions/store.py:13,290–573,799–862` | 底层存储解释可选功能的 Run/Node 状态、恢复和脱敏 | 拆除后 Store 只保留现有消息/trace/token 的读写，删除这些领域投影 |
| 已核实：`runtime/runtime.py:37,160–168` | Runtime 默认工厂主动创建 Gateway 的具体主体解析器 | GatewayHost 负责构造并注入已有 resolver；Runtime 只使用现有 `RuntimePrincipalResolver` 协议，不新增依赖注入容器 |
| 已核实：`proactive/service.py:18`、`proactive/cron.py:11`、`proactive/dream.py:13` | 领域工具通过 Runtime 的 ContextVar 获取可信 route，反向依赖运行时 | 保留可信 route 约束；将已有 route/inbound 访问器移到现有消息边界或一个具体上下文文件，不新建泛化事件总线；这是现有主体隔离需求，不是假设未来插件 |
| 已核实：`tools/context_attachment.py:4`、`mcp/manager.py:7`、`skills/manager.py:7` | 通用 tokenizer 放在 sessions 包，造成广泛底层向会话包依赖 | 如分层重构，直接移动现有 tokenizer 函数到独立工具文件并改导入；不增加 tokenizer 服务接口 |
| 已核实：`runtime/state.py:17–18,156–173` | 状态机具体知道 Web 附件类型、owner 和工具构造 | 现有附件仅 Web 是有意范围；可让 Web 入站边界完成解析及当前轮工具构造，Runtime 继续使用已有受控附件数据；不要为尚未要求的 IM/OCR 扩展造平台 |
| 已核实：`web/backend/proactive_control.py:324–347` | HTTP 读模型调用领域模块的多个私有校验函数，并重复快照 schema | 复用现有领域快照解析函数作为明确的公用函数，删除重复字段判断；不增加 repository/service/DTO 多层 |

## 3. 数据流

以下路径均为**已核实**。先列渠道特有入口，然后汇入同一主链。阶段文档中的旧“Web 自己消费 Bus”流程不代表当前实现。

### 3.1 各渠道进入 Gateway

| 渠道 | 按顺序经过的文件及函数 |
| --- | --- |
| CLI | `Turning-Good-Agent/cli.py:206` 的 `chat()` 读取终端输入 → `GatewayCliClient.send_message():113–125` 发送认证 WebSocket 动作 → `web/backend/app.py:603–721` 的 `cli_websocket()` 校验 Bearer、绑定 conversation、解析 `message.send` → `channels/cli_gateway.py:444–488` 的 `CliSessionCoordinator.send()` 建立 `CliTurnControl` 和规范入站消息 → `GatewayTurnCoordinator.submit()`；其 dispatch 写入 Bus |
| Web | `web/frontend/src/App.tsx:376–430` 的 `send()` 生成 `client_action_id`、乐观消息和附件 ID → `web/frontend/src/state/socket_client.ts:74–81` 的 `send()` → `Turning-Good-Agent/web/backend/app.py:724–808` 的 `websocket_endpoint()` → `web/backend/coordinator.py:210–269` 的 `WebSessionCoordinator.send()` 校验会话 route、解析 owner-scope 附件、生成 request ID，dispatch 建立控制对象并写入 Bus |
| 飞书 | `Turning-Good-Agent/channels/feishu_ws.py:200–254` 的 `_pump_events()` 接收 SDK 子进程事件 → `channels/feishu.py:467–521` 的 `_on_event()` / `_handle_event()` 过滤私聊/纯文本、锁定所有者、去重、派生 route → `channels/im.py:166–201` 的 `ImSessionCoordinator.accept()` → submit，其 dispatch 建立 `ImTurnControl` 并写入 Bus |
| 微信 | `Turning-Good-Agent/channels/weixin.py:382–419` 的 `poll_once()` → `weixin_ilink.py:137–157` 的 `poll()` 获取事件 → `weixin.py:689–752` 的 `_handle_event()` 校验文本/绑定对端、去重、带入 binding epoch、构造 route → `channels/im.py:166–201` 的 `accept()` → submit / dispatch / Bus |

route 使用 `principal_id + channel + conversation_id`；`Turning-Good-Agent/gateway/routing.py:11–17` 的 `derive_session_id()` 返回 opaque SHA-256 ID。主微信 Binding 和飞书 Bot 先确定可信主体，模型不能选择数据根（`channels/feishu.py:513–520`、`channels/weixin.py:730–750`）。

### 3.2 共用前台处理和会话读写

1. `Turning-Good-Agent/gateway/turns.py:84–100` 的 `submit()` 按 request ID 去重，同一路由运行时把后续普通消息放入 FIFO；`_dispatch():144–153` 调用渠道 dispatch；`bus/queue.py:13–19` 的 `publish_inbound()` / `consume_inbound()` 是同一入站队列。
2. `gateway/turns.py:202–215` 的 `_consume_inbound()` 创建处理任务，`_run_message()` 获得全 Gateway 执行 semaphore，取得当前 Runtime，调用 `run_turn()`；这里**没有普通 turn 总超时**，见 M1。
3. `runtime/runtime.py:171–214` 的 `run_turn()` / `_run_turn()` 绑定 route、inbound、主体上下文及 ChannelAdapter；`gateway/principals.py:69–109` 的 `resolve()` 将所有者放在配置 data_dir，其他主体放在 `data_dir/principals/<hash>/`，复用 Provider/工具表，使用各自主体的 ProfileMemory 和 SessionManager。
4. `runtime/runtime.py:224–260` 获得主体会话锁，循环调用 `runtime/state.py:78–92` 的 `run_state()`。正常顺序是 COMMAND → SESSION → BUILD → RUN → COMPACT → SAVE → RESPOND；任意状态异常转 RESPOND（`state.py:58–75`）。
5. **COMMAND**：`state.py:95–108` → `sessions/manager.py:51–80` 的 `handle_inbound_command()`。命令快捷路径不创建会话、不调用模型、不写普通消息/token。`/clear` 在 `manager.py:73–75` 直接删除目录；`/new` 目前只返回文本，没有完成 CLI route 切换，见 M2。
6. **SESSION，第一次读取/创建会话**：`state.py:111–117` → `PrincipalSessionReader.cleanup_expired_sessions()` / `load_or_create()`（`runtime/principal_context.py:63–66,95–97`）→ `sessions/manager.py:29–49` 校验已有 route → `sessions/store.py:30–62` 的 `load_session()` / `create_session()`。创建时立即落盘 `session.json`；所以 SAVE 是消息结果的主要提交边界，但不是整个系统唯一写入点。
7. **BUILD，读取原文历史和画像**：`state.py:126–173` 调用 `sessions.all_messages()` 读取 `messages.jsonl`，`context/session_context.py:16–27` 决定用 `session.uncompacted_history` 还是完整原文。`memory/long_term.py` 的 `ProfileMemory.read()` 读取本主体 USER/SOUL。`context/token_budget.py` 估算根提示词、画像、摘要、历史、输入和工具 schema；超限拒绝。`context/builder.py:48–85` 按 system → SOUL → USER → summary → history → current input 构造模型消息。
8. **当前 Web 附件**：`state.py:156–164` 检查 active Provider 视觉能力并创建当前轮 `ReadAttachmentTool`；文档只有 descriptor，按需读；图片由 `context/builder.py:70–82` 临时编码为 image blocks。只计算正文的 BUILD 预算没有完整覆盖这段附加输入，见 M4。历史中的公开 metadata 不会让下一轮自动得到附件访问权。
9. **RUN**：`state.py:177–201` → `runtime/agent_loop.py:60–283` 的 `AgentLoop.run()` → `_complete():491–526` → `llm/openai_compatible.py:47–126` 的 `complete()` / `stream()` / `_create_completion()` → AsyncOpenAI Chat Completions。多智能体是当前父循环内的特殊工具分支，删除步骤见第 4 节。
10. **工具循环**：`AgentLoop` 追加 assistant tool-call 消息 → `runtime/tool_call_runner.py:40–59,61–75` 的 `execute_calls()` / `_prepare_call()` → `tools/registry.py` 的 `prepare_call()` → `tool_call_runner.py:128–143` 执行硬安全检查、`hooks/tool_permission.py` 审批、状态 Hook → `tools/executor.py:23–49` 二次安全检查并执行 → 结果截断/状态 Hook → `agent_loop.py:215–237` 将结果和受控 Skill/MCP attachment 加回本轮 working messages。中间工具对话不作为聊天消息持久化。
11. **COMPACT**：`state.py:232–281` 将本轮用户、已消费 guidance、最终 assistant 加入临时历史，`memory/short_term.py:32–69,71–96` 的 `plan_compaction()` / `compact()` 生成摘要及近期窗口，摘要 usage 并入本轮 usage。压缩标识缺陷见 H4。
12. **SAVE，结果持久化**：`state.py:284–355` 顺序更新标题、保存 user、guidance、assistant，更新 summary 和 uncompacted_history，再保存 tool_calls、真实 token usage。分别落在 `session.json`、`messages.jsonl`、`tool_calls.jsonl`、`true_token_usage.jsonl`；这组写入没有整体原子提交，属于第7节讨论的明确非目标，单快照写入问题见 M5。工具正文/参数已作为 tool-call 记录持久化，而工具对话消息只在 working messages 中。
13. **SAVE 后主动事实**：`state.py:347–354` 发 `CONVERSATION_COMPLETED` → `proactive/manager.py:9–18` 的 `emit()` → `proactive/service.py:415–438` 的 `handle()` 仅派后台 observation 任务。Skill 沉淀删除后这条现有消费者可整体注销；Cron/Dream/Breakbeat 的计时和手动运行不依赖这个消费者。
14. **RESPOND 和 trace**：`state.py:358–383` 的 `respond()` 创建明确 recipient 的 `OutboundMessage`；`runtime/runtime.py:246–260` 加终态监控及 trace，并由 `state.py:386–393` 批量保存 `turn_traces.jsonl`。`runtime.py:263–266` 的 completed/error Adapter 回调本身不负责重复发送最终文本。

### 3.3 回复发出及浏览器恢复

`Turning-Good-Agent/gateway/turns.py:233–244` 把 request ID、微信 epoch 放入出站 metadata，再 `bus.publish_outbound()`；`channels/manager.py:102–105,72–99` 是唯一出站消费者，按 recipient.channel 找 Transport 并限时投递。

| 目标 | 最终投递和收尾 |
| --- | --- |
| CLI | `channels/cli_gateway.py` 的 Transport 将消息发给匹配的认证连接；`cli.py:300–311,329–376` 接收和展示。`CliSessionCoordinator.on_delivery()` 释放该 route 的 FIFO。delta/status/approval 也沿共享 outbound Bus，不保存为额外聊天消息 |
| Web | `channels/web.py` 的 Transport 调用 `web/backend/coordinator.py:327–360` 的 `handle_outbound()` → `SessionEventHub.publish():49–66` → `app.py:731–735` 的 `forward_events()` → 前端 `App.tsx:188–219` / `state/session_state.ts` reducer；终态生成 task.completed/failed/cancelled，控制器移除并推进 FIFO |
| 飞书 | `channels/feishu.py:182–213,235–253` 的 `send()` / `_send_final_text()` → `feishu_ws.py` 的 `LarkFeishuClient.send_text()` 调平台 API；只发最终纯文本 |
| 微信 | `channels/weixin.py:313–369` 的 `send()` 校验 Binding 和 epoch、分段 → `weixin_ilink.py:159–186` 的 `send_text()`；只发最终纯文本 |

IM 终态无论平台是否接受，`channels/im.py:203–224` 的 `on_delivery()` 都释放当前控制对象并调用 `complete_route_turn()`，符合在线即时投递、不补发的既有设计。Web 初始历史来自 `web/backend/app.py:467–471` 的 GET messages，重连事件来自 `web/backend/events.py:68–80` 的游标回放或 snapshot；快照无法完整恢复普通审批/历史的问题见 M9。

## 4. 多智能体拆除清单

### 4.1 后端和配置

本节表格每行对应一个引用或职责块，均标记**已核实**。先修改调用方再删除包，或在同一小步中同时修改，避免断开的 import。`multi_agent/` 内部 8 个文件可最终整体删除：`__init__.py:1`、`coordinator.py:1`、`delegate_tool.py:1`、`events.py:1`、`schema.py:1`、`types.py:1`、`worker_profile.py:1`、`worker_runner.py:1`；以下着重列包外引用。测试列中的路径均位于 `tests/`，前端测试另注明。

| 文件和行号 | 这段代码的作用 | 拆除后的处理方式 | 受影响的测试 |
| --- | --- | --- | --- |
| 已核实：`Turning-Good-Agent/runtime/runtime.py:18–23,106–109` | 导入并无条件构造 Coordinator、事件白名单 | 删除导入和实例属性，默认 Runtime 不再构造 WorkerRunner | `test_phase9_integration.py:1`、`test_runtime_mcp_lifecycle.py:1`、`test_runtime_flow.py:1` |
| 已核实：`runtime/runtime.py:210–214`、`runtime/turn_context.py:19–21,57–69` | 绑定父 TurnContext，供即时协作事件落盘取用 | 删除协作事件方法后，删除这个仅为协作事件使用的 ContextVar 和绑定/复位；保留 route、inbound、principal ContextVar | `test_phase9_persistence.py:1`、`test_principal_context.py:1` |
| 已核实：`runtime/runtime.py:269–320` | 在 SAVE 前追加协作 trace、Worker usage，调用 Channel 专用回调 | 整体删除 `_persist_multi_agent_event()`；普通 trace/token 写入不变 | `test_phase9_persistence.py:1` |
| 已核实：`runtime/state.py:15–16,132–147,165` | BUILD 构造 provisional/final invocation，将委派 schema 计入预算 | 删除协作专用构造/错误分支；直接对本轮实际普通工具及附件工具计算预算，不能漏掉 `read_attachment` | `test_runtime_state.py:26–57`、`test_phase9_contract.py:1`、`test_web_attachment_runtime.py:41` |
| 已核实：`runtime/state.py:181–193,204–229` | RUN 传 invocation，辅助函数根据渠道/模式/配置生成父委派对象 | 删除参数和 `_multi_agent_invocation()`；保留 Channel 工具 allowlist、Stop/guidance、当前轮附件工具 | `test_phase9_integration.py:1`、`test_agent_loop.py:282–406` |
| 已核实：`runtime/state.py:335–345` | 为父 usage 附加 run/node/agent/scope | 删除协作标签，仅写普通单轮 usage；不要再解析旧 Run 字段 | `test_phase9_persistence.py:1`、`test_runtime_flow.py:1` |
| 已核实：`runtime/turn_context.py:13–14,118` | invocation 类型和单轮字段 | 删除类型导入及字段 | `test_runtime_state.py:26–57`、`test_phase9_integration.py:1` |
| 已核实：`runtime/agent_loop.py:12,71,96,107–148` | invocation 参数、finish 收口、协作 deadline 包装 | 删除协作导入、参数、闭包；`finish()` 仅组装普通结果 | `test_agent_loop.py:282–406`、`test_phase9_integration.py:1` |
| 已核实：`runtime/agent_loop.py:153–179,185,199–214,243–275` | 每个 LLM/工具/总结安全点检查协作预算和终态、收口失败 | 删除协作判断和专用超时文本；保留普通异常传播、usage 校验、Stop；另修 M1/M4 的普通超时和预算，不能把唯一预算检查随包删除 | `test_agent_loop.py:282–406`、`test_phase9_integration.py:1` |
| 已核实：`runtime/agent_loop.py:285–311` | 合并普通工具 schema 与特殊委派 schema | 直接使用现有 ToolRegistry 的 schema；保留当前轮附件工具注册 | `test_agent_loop.py:282–406`、`test_phase9_contract.py:1` |
| 已核实：`runtime/agent_loop.py:313–395` | 按工具名切分普通批次/委派调用，包装父审批 | 用已有 `ToolCallRunner.execute_calls()` 执行整个普通批次；删除 persist_record 区分和父审批包装，不保留特殊工具名拒绝分支 | `test_agent_loop.py:282–406`、`test_phase9_integration.py:1`；保留普通串并行工具回归 |
| 已核实：`runtime/agent_loop.py:397–416` | 委派后的父上下文和 deadline 辅助函数 | 删除两函数；普通请求预算检查应独立于被删除的 invocation | `test_phase9_integration.py:1` |
| 已核实：`runtime/agent_loop.py:69–72,79–90,218,290` | 为替代工具表/Runner及不记录调用预留运行参数；生产Worker另建Loop，没有使用这些替代入口 | 删除无当前产品调用的 `tool_registry`、`tool_runner`、`persist_tool_calls` 参数及分支，测试改用已有构造注入；保留Proactive正在使用的 `llm_override`、`streaming_enabled` 和Web附件工具参数 | `tests/test_agent_loop.py:130–133`；`test_phase9_worker_security.py:1` |
| 已核实：`runtime/principal_context.py:157–164` | 主体限定的 Run 摘要读取 | 删除 `read_multi_agent_runs()`；保留主体限定消息/trace/token 读取 | `test_phase9_persistence.py:1`、`test_principal_context.py:1` |
| 已核实：`sessions/store.py:13,231–247` | 事件类型导入，worker/parent usage 标签及协作累计 | 删除协作导入、标签校验/序列化和 scoped 累计分支；保留普通累计 token 逻辑 | `test_phase9_persistence.py:1`、`test_sessions.py:1` |
| 已核实：`sessions/store.py:508–521` | 仅协作usage调用的run/node/agent标识校验辅助函数 | 删除scope序列化后删除两个无消费者的辅助函数；不删通用写锁 `:503–506` | `test_phase9_persistence.py:1` |
| 已核实：`sessions/store.py:289–394` | 将 trace 还原为 Run/Node/usage 摘要 | 删除整个 `read_multi_agent_runs()` | `test_phase9_persistence.py:1` |
| 已核实：`sessions/store.py:396–549` | 重启扫描并追加 interrupted Run/Node 记录 | 删除 `recover_interrupted_runs()` 和只被它调用的恢复行构造函数 | `test_phase9_persistence.py:1` |
| 已核实：`sessions/store.py:551–573` | 按 run_id 汇总 Worker 和整 Run usage | 删除 `_multi_agent_usage_summary()` | `test_phase9_persistence.py:1` |
| 已核实：`sessions/store.py:802–803,813–862` | 协作 trace 专用字段过滤/文本规整 | 删除特殊 state 分支和 `_safe_multi_agent_metadata()`；保留普通 trace 序列化 | `test_phase9_persistence.py:1` |
| 已核实：`gateway/host.py:202` | Gateway 启动恢复协作中断状态 | 删除调用，不替换为空实现 | `test_phase9_persistence.py:1`、`test_gateway_foundation.py:1` |
| 已核实：`context/builder.py:6,9,12–13,87–116` | Worker 专用 dependency 上限、上下文构造 | 删除 `build_worker()`、两个常量及仅用于它的 `render_skill_catalog` / tokenizer 导入；保留普通 Skill Catalog 注入、ProfileMemory 和 Web 多模态 build | `test_phase9_contract.py:1`、`test_phase9_worker_security.py:1` |
| 已核实：`tools/base.py:33`、`tools/builtin_tools.py:35`、`skills/load_skill_tool.py:14` | Worker 正向只读标志声明 | 删除 `worker_read_only` 字段/标志；普通 Tool 的 `parallel_safe` 与审批声明保留 | `test_phase9_worker_security.py:1`、`test_skills.py:1` |
| 已核实：`tools/filesystem_tools.py:90,100–110,124,177,224,350` | 文件工具 Worker 路径策略和只读标志 | 删除仅 Worker 的策略属性/分支/标志；遍历函数调用中的 `_path_allowed` 要同时调整（`:154–156,201,388`），保留并补齐普通 workspace 安全检查（H7） | `test_phase9_worker_security.py:1`、`test_basic_tools.py:1` |
| 已核实：`tools/executor.py:14–19` | 执行前调用 Worker 路径策略 | 删除 Worker 专用预检；保留通用硬安全检查。非 Owner 的当前安全缺口不能以“没有 Worker 了”忽略 | `test_phase9_worker_security.py:1`、`test_tool_permission_validation.py:1` |
| 已核实：`tools/web_tools.py:136,170`、`web/attachments/tool.py:16` | 网络工具可给 Worker、附件工具不可给 Worker | 删除这些 Worker 标志；Web 附件仍仅按当前 Web turn 注入，保留 IM/主动任务隔离 | `test_phase9_worker_security.py:1`、`test_web_attachment_tool.py:1` |
| 已核实：`channels/base.py:53–55,115–117` | ChannelAdapter 协作事件协议及 Silent 实现 | 删除回调；保留普通 tool/status/response/approval 协议 | `test_phase9_contract.py:1`、`test_channel_output.py:1` |
| 已核实：`channels/im.py:83–84` | IM 显式忽略协作事件 | 删除方法，IM 无协作能力通过不装配实现 | `test_phase9_contract.py:1`、`test_im_channel.py:1` |
| 已核实：`channels/policy.py:24–25` | IM 拒绝 `/multi` 命令 | 删除这个已经不存在的命令分支，不保留提示兼容层；未知 slash 走普通命令错误 | `test_phase9_contract.py:1`、`test_channel_policy.py:1` |
| 已核实：`channels/cli_gateway.py:14,35,40–46` | CLI 事件白名单扩展、模式 metadata 校验 | 删除协作事件字段展开及模式辅助函数；普通事件白名单保留 | `test_cli_gateway_endpoint.py:225–312` |
| 已核实：`channels/cli_gateway.py:352–359,449,461` | Adapter 转发协作事件、send 接收模式 | 删除回调和参数；入站 metadata 不再携带协作模式 | `test_cli_gateway_endpoint.py:225–312`、`test_cli_gateway_transport.py:1` |
| 已核实：`channels/web.py:13,103–110` | Web Adapter 过滤/发布协作实时事件 | 删除导入和回调，普通 Bus 输出保留 | `test_phase9_integration.py:1`、`test_gateway_web_coordinator.py:204–285` |
| 已核实：`cli.py:50,120–130` | CLI 暂存一次性模式、写入消息、解析模式 | 删除属性、metadata 字段、setter 和延迟导入 | `test_cli_gateway_client.py:178–216` |
| 已核实：`cli.py:229,277–285` | CLI 帮助与 `/multi auto\|off` 本地命令 | 删除帮助片段和整个命令分支；保留 `/stop` 并修审批优先级 | `test_cli_gateway_client.py:178–216`、`test_cli_terminal_mode.py:1` |
| 已核实：`cli.py:357–370,380–401` | 协作树、指标、错误摘要渲染 | 删除分支及两个专用辅助函数 | `test_cli_gateway_client.py:178–216` |
| 已核实：`web/backend/coordinator.py:14,27–33,215,224` | 协作事件字段/模式校验，Web send 的模式 | 删除导入、辅助函数、参数和metadata；保留 client_action_id 幂等及附件归属 | `test_gateway_web_coordinator.py:204–285` |
| 已核实：`web/backend/coordinator.py:322–323,339–340` | snapshot 读取 Run、出站协作事件白名单 | 删除 Run 读取和投影分支；普通 snapshot 恢复仍需修 M9 | `test_gateway_web_coordinator.py:204–285`、`test_phase9_persistence.py:1` |
| 已核实：`web/backend/app.py:511–513` | observability 增加 multi_agent_runs | 删除响应字段，不返回空数组维持旧合同 | `test_phase9_persistence.py:1`、`test_web_host.py:1` |
| 已核实：`web/backend/app.py:669–671,782–786` | CLI/Web 动作提取并传递模式 | 删除模式参数组装；现有消息、guidance、Stop、approval 动作继续用原通道 | `test_cli_gateway_endpoint.py:225–312`、`test_gateway_web_coordinator.py:204–285` |
| 已核实：`config/settings.py:22–33,167,210,264–282` | 八项协作配置的默认值、加载和字段 | 删除 `MultiAgentSettings`、Settings字段及解析函数 | `test_phase9_contract.py:1`、`test_gateway_settings.py:1` |
| 已核实：`config/validate.py:11–13,42,122–162` | 协作超时/role/brief常量及跨字段校验 | 删除常量、调用和校验函数；其他配置校验保留 | `test_phase9_contract.py:1` |
| 已核实：`web/backend/config_control.py:42–53,309` | Web Apply allowlist 和 active/desired公开配置 | 删除配置组及输出字段，同步前端类型和测试fixture | `test_phase9_contract.py:1`、`web/frontend/tests/control_plane_mock.spec.ts:115–126` |
| 已核实：`settings.example.json:12–21` | 协作配置模板 | 删除整个 `multi_agent` 对象；后续实施者同步所有者自己的配置，本文没有读取其私有文件 | `test_phase9_contract.py:1`；前端配置fixture |

本表后端短路径均相对 `Turning-Good-Agent/`，`cli.py` 亦在该包内，`settings.example.json` 在仓库根。删除的八项配置是 `enabled`、`run_timeout_seconds`、`worker_timeout_seconds`、`max_workers_per_run`、`max_concurrent_workers_per_run`、`max_concurrent_workers_global`、`worker_result_token_limit`、`parent_result_token_limit`。MCP 的 `server_worker.py`、McpServerWorker、后台 Executor 并发池都是其他功能，不属于这个删除范围。

### 4.2 前端引用

| 文件和行号 | 这段代码的作用 | 拆除后的处理方式 | 受影响的测试 |
| --- | --- | --- | --- |
| 已核实：`web/frontend/src/types.ts:53–126` | 模式、策略、Run/Node/usage/event 类型和事件字段 | 删除全部协作类型、常量、协议字段；保留普通 TaskEvent | `web/frontend/tests/multi_agent_view.spec.ts:1`、`phase9_visual.spec.ts:1` |
| 已核实：`src/types.ts:162,188–197,226` | TurnState Run、可编辑配置和Apply字段 | 删除这些字段；后续序列化不再发送协作组 | `tests/web_session_state.test.cjs:1`、前端 `control_plane_mock.spec.ts:115–126` |
| 已核实：`src/App.tsx:23,57–59,171–178,185–186,225` | 类型/状态、读取全局协作开关、返回聊天刷新 | 删除专用状态和配置读取；返回聊天仍需刷新真实 active模型能力（M12） | 前端 `control_plane_mock.spec.ts:113–142`、`phase9_visual.spec.ts:1` |
| 已核实：`src/App.tsx:269,403,419,427,430,470,482–496,502` | 发送模式/重置、选中Run检查器、专用详情读取 | 删除模式参数、依赖和专用检查器函数，保留普通消息发送、重试和检查器 | 前端 `phase9_visual.spec.ts:1`；`tests/web_session_state.test.cjs:1` |
| 已核实：`src/App.tsx:599–600,649,653–654,661–663` | Run活跃判断、Composer锁定、卡片回调及Inspector选择 | 删除协作判断/props，普通 `sessionState.running` 与 Catalog锁仍保留；不要删通用 Stop | 前端 `phase9_visual.spec.ts:1`、`workbench_attachments.spec.ts:1` |
| 已核实：`src/components/Composer.tsx:6,9,23–26,35,46,198` | 模式选择器import、props、解构、渲染及禁用原因 | 删除协作属性与组件；保留发送、视觉门禁、附件、权限菜单、运行中guidance | 前端 `phase9_visual.spec.ts:1`、`workbench_attachments.spec.ts:1` |
| 已核实：`src/components/MultiAgentModeSelector.tsx:1` | Auto/Off选择器 | 删除整文件 | 前端 `phase9_visual.spec.ts:1` |
| 已核实：`src/components/MultiAgentRunCard.tsx:1` | 时间线Run卡片、进度和Stop按钮 | 删除整文件；普通任务 Stop 仍在 Composer | 前端 `multi_agent_view.spec.ts:1`、`phase9_visual.spec.ts:1` |
| 已核实：`src/components/ActivityCluster.tsx:5,7,12,18,42–48` | 读取turn Run并渲染卡片 | 删除协作类型/prop/卡片，`hasActivity`只判断真实普通活动；保留审批栏和普通活动步骤 | 前端 `phase9_visual.spec.ts:1`、`observability_view.spec.ts:1` |
| 已核实：`src/components/SessionInspector.tsx:5,8,13–14,17–58` | 协作详情、模板拓扑、Worker最终内容和指标辅助函数 | 删除专用props、选择和函数，保留普通上下文/工具/观测Inspector | 前端 `phase9_visual.spec.ts:1`、`observability_view.spec.ts:1` |
| 已核实：`src/state/multi_agent_view.ts:1` | 事件/快照归并Run、节点、usage | 删除整文件，避免留下无消费者的协议解析 | 前端 `multi_agent_view.spec.ts:1` |
| 已核实：`src/state/session_state.ts:1–2,236,270–278,283–317` | 导入协作归并、snapshot Run挂载、协作事件特殊reducer、Run锚点补齐 | 删除专用分支和辅助函数；普通Turn/event/retry逻辑保留 | `tests/web_session_state.test.cjs:1`、前端 `multi_agent_view.spec.ts:1` |
| 已核实：`src/components/ConfigEditor.tsx:34–41,69,82` | 八个字段、配置分组、diff枚举 | 删除字段、分组和diff中的组名；其他Skill/Proactive配置保留 | 前端 `control_plane_mock.spec.ts:115–126` |
| 已核实：`src/styles/components/composer.css:93–109` | 模式选择器样式 | 删除仅 `.multi-agent-*` 规则 | 前端 `phase9_visual.spec.ts:1` |
| 已核实：`src/styles/components/timeline.css:278–310` | Run卡片样式和断点规则 | 删除专属规则 | 前端 `phase9_visual.spec.ts:1` |
| 已核实：`src/styles/components/inspector.css:369–419` | 协作详情/拓扑/Worker结果样式 | 删除专属规则 | 前端 `phase9_visual.spec.ts:1` |

本表 `src/` 相对 `web/frontend/`。普通 `api.ts` 和 HTTP transport 没有独立协作 endpoint，保留。`ChatTimeline.tsx` 也不直接导入协作包，不应整组件删除。

### 4.3 测试和文档

| 文件和行号 | 这段代码的作用 | 拆除后的处理方式 | 受影响的测试 |
| --- | --- | --- | --- |
| 已核实：`tests/test_phase9_contract.py:1`、`test_phase9_coordinator.py:1`、`test_phase9_integration.py:1`、`test_phase9_persistence.py:1`、`test_phase9_worker_security.py:1` | 协作专属契约、调度、集成、存储、安全测试 | 删除这五个专属文件；先把其中仍适用于单Agent的权限/预算/持久化回归移到已有对应测试文件，尤其H1/H4/M4；不要用空模块使旧导入通过 | 五文件自身 |
| 已核实：`tests/test_agent_loop.py:282–406`、`test_runtime_state.py:26–57`、`test_web_attachment_runtime.py:41` | 混合文件内的委派分支、假Coordinator和配置fixture | 仅删协作断言/fixture字段；保留普通循环、状态机、Web附件测试 | 三文件自身 |
| 已核实：`tests/test_cli_gateway_client.py:178–216`、`test_cli_gateway_endpoint.py:225–312`、`test_gateway_web_coordinator.py:204–285` | CLI/Web模式、事件和snapshot集成合同 | 删除专用用例；保留认证、route、去重、FIFO、Stop和审批用例 | 三文件自身 |
| 已核实：`web/frontend/tests/multi_agent_view.spec.ts:1`、`phase9_visual.spec.ts:1` | 协作状态及视觉专项 | 删除两文件；通用权限/会话切换验证保留在现有工作台测试 | 两文件自身 |
| 已核实：前端测试 `control_plane_mock.spec.ts:26,115–126`、`desktop_surface_refinement.spec.ts:18`、`im_settings_workspace.spec.ts:9`、`observability_view.spec.ts:31`、`proactive_workspace.spec.ts:17`、`workbench_attachments.spec.ts:10` | 公用页面fixture包含协作配置；控制面用例测试开关 | 删除fixture组/开关用例，保持fixture满足真实剩余合同 | 六文件自身，均在 `web/frontend/tests/` |
| 已核实：`README.md:3,9,17,28,38,61–63,89,150–152,164–171,185,211,218–219` | 产品宣称、配置/CLI说明、附件Worker边界及专题 | 删除协作条目/配置/命令/专题，附件叙述改为当前普通Web Agent；保留MCP Worker术语 | 文档一致性检查 |
| 已核实：`docs/TURNING_GOOD_AGENT_SPEC.md:4,23,75,382,569,584–604,616,630` | 当前范围、目标图、Worker附件、路线和更新项 | 删除Phase9当前功能/专题及过时未来计划；保留MCP后台Worker描述 | 文档一致性检查 |
| 已核实：`docs/PROJECT_ARCHITECTURE.md:7,376,398` | 总体定位及附件对Worker的约束 | 删除协作范围和Worker附件约束；保留Web/IM/后台边界 | 文档一致性检查 |
| 已核实：`docs/STARTUP.md:26`、`docs/README.md:27` | 开关操作及阶段索引 | 删除开关及Phase9有效功能索引 | 文档一致性检查 |
| 已核实：`docs/phases/2026-06-15-phase-9-multi-agent.md:1–506` | 全部专属设计合同 | 当前功能删除后按工程规则移除这份已废弃阶段文档，不为旧合同留兼容实现；删除前把仍有效的安全约束转入现有规格 | 文档一致性检查 |
| 已核实：`docs/phases/2026-08-20-phase-10-web-attachments-multimodal.md:5,26,48–49,151,154,258,268,286` | 附件计划中引用协作/Worker范围 | 删除协作引用；保留CLI/IM/主动任务不扩展、Web权限及持久化要求 | Web附件相关测试 |

### 4.4 拆除验收

**已核实的检查依据**是以上每个调用块。后续实施时应按顺序完成：删除专用前端和协议字段 → 删除Runtime委派桥接及Store投影 → 移除装配/配置 → 删除包/专属测试/文档 → 运行剩余普通聊天、CLI/Web认证、审批、Stop、FIFO、Skill加载、MCP和附件回归。全仓搜索 `multi_agent`、`MultiAgent`、`delegate_multi_agent`、`/multi`、`worker_read_only`、`worker_path_policy`、`build_worker` 应无产品残留；不要把MCP的Worker和Proactive的后台执行器作为误命中删除。原有历史JSONL无需迁移、回写、清理或恢复旧Run；文件可以继续作为原始日志留在数据目录，运行时代码不为它们维护兼容投影。

## 5. Skill 沉淀拆除清单

### 5.1 保留和删除的边界

**已核实**：自动生成路径是 `proactive/skill_evolution.py:115–267`：成功轮次/明确学习信号 → Observation → 定时/手动选择证据 → 回读原文 → 自动生成 Draft。手动运行 `run_skill_evolution` 仍是让后台模型生成草稿，属于要拆除的功能。

**已核实**：Skill 本身是另一条路径：`runtime/runtime.py:118–122,374–378` 装配/扫描 → `skills/manager.py:24–70` 管理Catalog和正文 → `skills/load_skill_tool.py:1–43` 当前轮加载 → `runtime/attachment_manager.py:73–95` 检查预算。`skills/creator.py:13–31`、`skills/manager.py:72–115`、`skills/skill_draft_tools.py:1`、`skills/installer.py:1` 支持用户明确请求创建、发布、安装，按 `docs/TURNING_GOOD_AGENT_SPEC.md:485–489` 保留。H3中的安全修复仍必须做，不能因自动沉淀删除而跳过手工Skill工具。

建议的具体保留范围：保留整个 `skills/`、正式 `.skills/`、手工 `.drafts/` 生命周期和 `skills.*` 配置；删除自动Observation/演进、私有自动草稿workspace及Proactive中的Skill领域页面。现有 `list_skill_drafts` / `delete_skill_draft` 是可用的手工草稿管理能力，迁到 `skills/skill_draft_tools.py` 并直接注入SkillManager，删除仍走审批；仅向当前Owner的CLI/Web开放，避免把全局草稿泄露给非Owner。这里保持的是现存手工创建/审阅能力，不是假设未来新Skill管理产品；不新增Web草稿管理面。

### 5.2 后端、共享代码和配置

| 文件和行号 | 这段代码的作用 | 拆除后的处理方式 | 受影响的测试 |
| --- | --- | --- | --- |
| 已核实：`Turning-Good-Agent/proactive/skill_evolution.py:1–465,536–845` | Observation/信号判定、每日演进、JSON解析、原文证据与usage | 删除自动沉淀Manager、Outcome、协议、提示词及辅助函数 | `tests/test_proactive_skill_evolution.py:1` |
| 已核实：`proactive/skill_evolution.py:466–490` | `run_skill_evolution` 手动触发自动Draft生成 | 删除Tool，不保留旧名字或空结果 | `tests/test_proactive_skill_evolution.py:1`、`test_proactive_service.py:487–574` |
| 已核实：`proactive/skill_evolution.py:493–533` | 列出/删除未发布草稿 | 在删除文件前，将两Tool移入 `skills/skill_draft_tools.py`，使用明确的SkillManager；删除沿用approval_required，列表限定Owner；移除动态Proactive workspace解析 | `test_skills.py:130–149`、`test_proactive_skill_evolution.py:1`；移入现有Skill测试 |
| 已核实：`proactive/service.py:14,29,33–38,134–144` | 自动Skill imports、Owner别名、任务索引及SAVE事件注册 | 删除自动imports/别名、`_skill_evolution_tasks`、`_observation_tasks`、注册；保留Review Provider和后台semaphore供Breakbeat/Dream/Cron | `test_proactive_service.py:24,119–256`、`test_gateway_web_injection.py:92` |
| 已核实：`proactive/service.py:189–198,232–250` | 为Owner/独立主体装配自动Draft SkillManager和EvolutionManager | 删除自动manager构造及非Owner自动草稿管理器；保留已有正式Runtime.skills和主体memory/sessions；不删除整个ProactiveWorkspace | `test_proactive_principals.py:219–260`、`test_proactive_service.py:1` |
| 已核实：`proactive/service.py:286–287,304–305,401–402` | 主体忙碌/退役/停止时管理两类Skill任务 | 删除对应条件和清理项；Cron、Breakbeat、Dream任务和主体删除锁保留 | `test_proactive_principals.py:1`、`test_gateway_binding_lifecycle.py:1` |
| 已核实：`proactive/service.py:415–438,555–574,718–735` | 消费会话完成事实、派观察、按主体跟踪观察任务 | 删除 `handle()`、`_observe_session()`、只为它服务的 `_spawn_for_principal()`；一般 `_spawn()` 与后台空闲通知保留 | `test_proactive_principals.py:219–269`、`test_proactive_service.py:1` |
| 已核实：`proactive/service.py:346–351` | 注册演进/列表/删除三个Tool | 删除演进注册；列表/删除迁由 `AgentRuntime.create_default()` 与其他Skill工具一同注册，不由Proactive服务启停 | `test_proactive_service.py:207,487–574`、`test_skills.py:1` |
| 已核实：`proactive/service.py:501,528–535,551–552` | 初始化每日deadline、到期启动、最早deadline计算 | 删除Skill演进分支；保留三项计时能力 | `test_proactive_service.py:240–241`、`test_proactive_scheduler.py:1` |
| 已核实：`proactive/service.py:676–703,816–826,877–889` | 定时/手动结果、恢复/失败Incident、Catalog动作 | 删除三个专属处理块和通知event；保留其他Incident领域 | `test_proactive_service.py:487–574`、`test_gateway_catalog_actions.py:27` |
| 已核实：`proactive/workspace.py:9,14,25,30` | 自动草稿SkillManager及EvolutionManager字段 | 删除两字段和仅用于它们的imports；memory/sessions/Cron/Dream/Breakbeat/Incident保留 | `test_proactive_principals.py:1` |
| 已核实：`proactive/types.py:9–11,168–186` | 四种Observation kind及领域数据类型 | 删除常量和Observation；保留 UsageTotals、CronJob、BreakbeatItem、Incident | `test_proactive_skill_evolution.py:28,687`、`test_proactive_store.py:1` |
| 已核实：`proactive/executor.py:151–153` | 后台拒绝调用演进与草稿控制Tool，防递归/副作用 | 删除演进名称；手工草稿Tool仍应排除后台，保留或改为由现有批准/显式名称过滤保证，不能因迁包让Cron看到它们 | `test_proactive_executor.py:1` |
| 已核实：`runtime/state.py:12,319,327–328,347–354` | SAVE为Observation准备assistant边界并发布完成事件 | 删除事件import、仅用于Observation的ID变量/赋值、payload/emit；保存assistant动作必须保留 | `test_runtime_flow.py:1`、`test_proactive_principals.py:227,269` |
| 已核实：`runtime/runtime.py:28,80,92,155` | 核心持有只用于完成事件转发的ProactiveManager | 删除该import/构造/参数/属性；Gateway直接管理ProactiveService生命周期，不让Runtime持有空事件处理器 | `test_proactive_service.py:24,119–256`、`test_gateway_web_injection.py:92` |
| 已核实：`proactive/manager.py:1–18`、`proactive/base.py:1–8`、`proactive/events.py:1` | 唯一消费者为Observation的处理器集合、Protocol、完成事件名 | 上述注册/emit删除后整体删除三文件；不为“将来主动扩展”保留空壳 | `test_proactive_service.py:24`、`test_proactive_principals.py:33,79` |
| 已核实：`gateway/catalog_actions.py:21,131,283,297,326` | 动作类型、参数、解析、显示名特殊分支 | 删除该动作及特例；剩余Dream/Breakbeat统一用现有scope形状 | `test_gateway_catalog_actions.py:27` |
| 已核实：`gateway/host.py:539–540,549,562` | 事件→Skill领域、hash目标、通知标题 | 删除三个映射项；不把旧Skill通知回退成Incident | `test_gateway_host_notifications.py:1`、`test_notification_fanout.py:73,112` |
| 已核实：`web/backend/read_models.py:41–50` | Slash `/skill-evolution` Catalog条目 | 删除此条；`:92–104` 的正式Skill Catalog必须保留 | `test_web_read_models.py:1`、`test_gateway_catalog_actions.py:27` |
| 已核实：`web/backend/app.py:143–145,170–172` | Skill领域快照及Draft删除REST | 删除Proactive专属两路由，不新增旧路径兼容alias；手工Draft继续通过Skill工具管理 | 前端 `proactive_workspace.spec.ts:315–330,425`；`test_web_host.py:1` |
| 已核实：`web/backend/proactive_control.py:9,14–15,65,156–162` | Skill模块、domain/锁、Proactive中的Draft删除 | 删除Skill域、锁与方法；通用controller保留供其他领域 | 前端 `proactive_workspace.spec.ts:315–330` |
| 已核实：`web/backend/proactive_control.py:244–246,282,287–296,339–348` | 读取skill.json、附加Draft视图、找EvolutionManager、校验Observation | 删除全部Skill专属块及私有校验调用；不要继续读取自动状态文件 | `test_proactive_store.py:1`、前端 `proactive_workspace.spec.ts:1` |
| 已核实：`web/backend/proactive_events.py:8,76–81` | 根据controller枚举广播所有领域快照 | 广播机制保留，controller移除Skill域后不会再广播；删除硬编码的测试五域假设 | 前端 `proactive_notifications.spec.ts:68`、`proactive_workspace.spec.ts:1` |
| 已核实：`config/settings.py:137–140,380–383` | 四个Observation/演进配置字段与解析 | 删除这四项，保留Review Provider、review_window、画像配额及Breakbeat/Dream周期 | `test_proactive_skill_evolution.py:40–41`、`test_gateway_settings.py:1` |
| 已核实：`config/validate.py:59–62` | 四项配置正整数校验 | 删除四行；其他Proactive校验保留 | `test_proactive_skill_evolution.py:1`、`test_gateway_settings.py:1` |
| 已核实：`web/backend/config_control.py:75–78,331–334` | 四字段Apply/public配置 | 删除四字段，与前端类型同步 | 前端 `control_plane_mock.spec.ts:46–47` |
| 已核实：`settings.example.json:87–90` | 配置模板 | 删除四项，不删除整个proactive或skills配置对象 | 配置及Proactivefixture测试 |

本表后端短路径仍相对 `Turning-Good-Agent/`，根配置与测试除外。四项删除字段是 `skill_observation_turn_interval`、`skill_observation_token_limit`、`skill_evolution_batch_token_limit`、`skill_evolution_batches_per_kind`。`review_*` 同时被Breakbeat使用，不能一起删；`review_window_token_limit` 也被Dream/Breakbeat使用。`skills/creator.py` 的名字包含creator，实际属于用户手工创建，必须保留。

### 5.3 前端、测试和文档

| 文件和行号 | 这段代码的作用 | 拆除后的处理方式 | 受影响的测试 |
| --- | --- | --- | --- |
| 已核实：`web/frontend/src/components/ProactiveSkillsPage.tsx:1–49` | 展示Observation、自动Draft、生成usage/deadline和删除 | 删除整页面；不删除普通Skill Slash条目和加载指导 | 前端 `proactive_workspace.spec.ts:315–330` |
| 已核实：`src/components/ProactiveWorkspace.tsx:9,24,71` | 导入、领域标题、页面分发 | 删除Skill自进化域；其他四域保留 | 前端 `proactive_workspace.spec.ts:315–330,425` |
| 已核实：`src/components/SessionSidebar.tsx:38` | 主动导航中的Skill自进化入口 | 删除入口及仅因此使用的图标import | 前端 `desktop_surface_refinement.spec.ts:38–39`、`proactive_workspace.spec.ts:1` |
| 已核实：`src/App.tsx:605` | Skill领域健康状态 | 删除此项，Record由剩余域确定 | 前端 `proactive_workspace.spec.ts:1` |
| 已核实：`src/proactive_types.ts:1–2,11,19,27,94–109,173` | route/wire域、映射、Observation/Draft快照类型和hash路由 | 删除自动Skill域及类型，route正则只保留现有四域；通用snapshot/notice类型保留 | 前端 `proactive_workspace.spec.ts:1`、`proactive_notifications.spec.ts:68` |
| 已核实：`src/proactive_api.ts:19,33` | Skill领域URL和Draft删除方法 | 删除自动域映射和这个Proactive API方法 | 前端 `proactive_workspace.spec.ts:315–330` |
| 已核实：`src/types.ts:215–218,263,269` | 配置四字段、沉淀图标、Catalog动作 | 删除四字段和 `skill_deposit` / `run_skill_evolution`；保留 kind=skill、icon=skill及create/publish/install所需类型 | 前端配置/Slash测试 |
| 已核实：`src/components/ConfigEditor.tsx:60–63` | 四项沉淀配置表单 | 删除四项；普通Skill加载预算表单保留 | 前端 `control_plane_mock.spec.ts:46–47` |
| 已核实：`src/components/SlashCommandMenu.tsx:11,120` | 沉淀SVG导入及图标映射 | 删除导入、映射和 `src/assets/slash-icons/skill-deposit.svg:1`；`public/slash-icons/skill-deposit.png` 是同名二进制遗留资产，没有行号，依据该import及资产清单删除；普通skill图标保留 | 前端 `workbench_visual.spec.ts:117` |
| 已核实：`src/styles/components/proactive.css:121–129` | 仅自动Draft页面使用的Markdown样式 | 页面删后核对无消费者再删；其余通用卡片、来源链接、删除对话框仍用于其他领域，保留 | 前端 `proactive_workspace.spec.ts:1` |
| 已核实：`tests/test_proactive_skill_evolution.py:1` | 自动Observation/演进专项及混在其中的Draft工具测试 | 删除专项文件；先将仍有效的手工List/Delete审批/路径测试移到 `test_skills.py`，保留H3安全回归 | 文件自身与 `test_skills.py:130–149` |
| 已核实：`tests/test_proactive_service.py:24,119–256,487–574` | ProactiveManager装配fixture、Skill计划/手动通知 | 删除Skill相关用例和事件managerfixture；保留其他三计时能力、关闭/替换、Incident测试 | 文件自身 |
| 已核实：`tests/test_proactive_principals.py:33,79,219–269,317` | 私人Skill workspace、完成事件隔离和演进Catalog | 删除自动观察/演进测试和旧eventManagerfixture；保留主体私有memory、Cron/Breakbeat/Dream和删除边界 | 文件自身 |
| 已核实：`tests/test_gateway_web_injection.py:92` | 断言Runtime事件handler持有服务 | 删除这个事件handler断言；保留唯一Gateway和注入服务的生命周期断言 | 文件自身 |
| 已核实：`tests/test_gateway_catalog_actions.py:27`、`test_notification_fanout.py:73,112`、`test_session_store_layout.py:105` | 演进动作名、通知fixture、自动状态布局 | 删除演进动作；Fanout测试换用仍存在的Dream/Cron事件；布局测试删skill.json预置，保留其它独立状态 | 三文件自身 |
| 已核实：前端测试 `control_plane_mock.spec.ts:46–47`、`desktop_surface_refinement.spec.ts:38–39`、`im_settings_workspace.spec.ts:13`、`phase9_visual.spec.ts:88`、`proactive_notifications.spec.ts:68`、`proactive_workspace.spec.ts:21,39,315–330,425`、`workbench_visual.spec.ts:117` | 配置/领域快照/导航/图标fixture和自进化页面验收 | 删除专属字段与页面用例；共用通知/领域测试改用剩余真实领域；Phase9文件按第4节整体删 | 七文件均在 `web/frontend/tests/` |
| 已核实：`README.md:16,37,162` | 自动Skill演进产品说明 | 删除自动沉淀说明，保留Skill加载、安装、用户手工创建/发布 | 文档一致性检查 |
| 已核实：`docs/TURNING_GOOD_AGENT_SPEC.md:72,336,579` | Phase7范围、Observation状态、自动演进 | 删除这三处自动能力；`:485–489` 的Skill机制和手工流程保留 | 文档一致性检查 |
| 已核实：`docs/PROJECT_ARCHITECTURE.md:436,439,442` | Evolution文件、SAVE事件、Observation/Draft主动域职责 | 删除自动模块行和仅供其使用的事件manager说明；保留手工Skill章节及其他主动状态 | 文档一致性检查 |
| 已核实：`docs/phases/2026-06-15-phase-7-proactive-memory.md:14,76,129–132,173,206–207,229,416–449,498–499,520,549,563,573` | 自动Skill目标、配置、状态、Tool、计划与测试 | 删自动沉淀内容；保留USER/SOUL、Cron、Dream、Breakbeat、Incident及Review Provider用途 | 文档一致性检查 |
| 已核实：`docs/phases/2026-06-15-phase-6-web-observability.md:8,212–214` | 主动工作面范围引用自进化 | 删自动Skill工作面引用；通用通知/其它领域保持 | 文档一致性检查 |

前端短路径 `src/` 相对 `web/frontend/`。`docs/README.md:25` 的Phase7说明也应同步为剩余能力；这行没有直接Evolution import但属于阶段范围说明。`docs/phases/2026-06-15-phase-5-skills.md` 和整个 `tests/test_skills.py` 不能因“Draft”关键词被删，它们主要验证保留的机制。

### 5.4 状态和拆除验收

**已核实**：自动状态是各主体 `proactive/skill.json`，由 `skill_evolution.py:432–463` 读写；Observation去重边界还在其内存字典（`:78–80`）。后续运行时应彻底停止读取、生成、调度这个文件，不新增旧状态迁移/恢复/备份工具，也不在本次或默认启动时删除所有者已有数据。保留用户正式Skill和已有草稿目录；无法区分手工/自动来源时尤其不能整目录删除。

后续验收应确认：普通聊天SAVE不触发学习信号模型请求；启动不创建/读取skill.json；Proactive仍正常调度Cron/Dream/Breakbeat；手工Skill扫描、load/install/create/publish/list/delete仍按当前权限运行。搜索 `SkillEvolution`、`skill_evolution`、`skill_observation`、`OBSERVATION_KINDS`、`Observation`、`skill_deposit`、`skill.json` 应无该功能产品残留；搜索draft仍会有合法的Skill机制引用，不应清零。拆除后不应剩下无消费者的ProactiveManager、完成事件或私人自动Draft管理器。

## 6. 问题清单

本节后端短路径均相对 `Turning-Good-Agent/`，前端短路径以 `web/frontend/` 开头。严重度根据可能越权、丢失事实、终止进程、错误执行或阻塞现有功能排序，不根据文件大小排序。安全复现只描述构造条件，本次没有读取真实凭据、攻击服务或操作活进程。

### 高

**H1 非Owner主体可绕过会话隔离读取Owner数据。**

- **位置**：`runtime/runtime.py:117`、`tools/path_utils.py:9–12`、`gateway/principals.py:93–103`、`channels/policy.py:55–59`、`tools/security.py:107–120,132–156`、`tools/web_tools.py:122`；HTTP Owner会话校验在 `web/backend/app.py:103–111,447–471`。
- **现象和影响**：所有主体复用以项目cwd为根的工具实例，非Owner只被移除审批工具；read_file/grep等仍可读取workspace内的私有配置、Owner会话/画像/Channel状态，绕过PrincipalSessionReader。非Owner的web_fetch还允许本机URL，从Gateway进程访问无调用者认证的Owner Web GET读模型，文件工具封堵后仍存在绕过路径。`docs/phases/2026-06-15-phase-8-im-channel-adapters.md:34,41,47,55` 要求独立主体状态完全私有，未授权这种开放。
- **核实状态**：已核实权限和调用链；未向真实私有文件或本机接口发送请求。
- **建议及验证**：先从非Owner工具面移除通用文件读取和可访问本机网络的工具；如现有非Owner需求确实包含文件读取，再按其允许根目录绑定当前轮实例并进入硬预检。网络校验必须覆盖解析后的IP、重定向及最终连接目标，不能只看URL字符串。补真实guest route经AgentLoop访问Owner文件、Owner HTTP读模型的拒绝测试；`tests/test_principal_context.py:73–88` 的SessionReader隔离测试不足以证明工具安全。

**H2 Windows二维码清理可能终止任意对应PID的进程。**

- **位置**：`channels/weixin_qr.py:37,214–230,244–254`、`gateway/host.py:148`。
- **现象和影响**：Presenter构造时扫描临时二维码目录，用 `os.kill(pid, 0)` 探测存活。Windows上除特殊控制信号外会调用TerminateProcess，0也会终止目标；残留目录对应活Gateway或被复用的PID时可能误杀无关进程。该平台语义已查证 [Python 3.12 os.kill 官方文档](https://docs.python.org/3.12/library/os.html#os.kill)。
- **核实状态**：已核实代码及官方平台语义；没有执行该探测。
- **建议及验证**：Windows改用只读进程查询，POSIX才使用信号0；不能把PermissionError/不存在PID测试当作活进程安全证明。`tests/test_weixin_qr_presenter.py:88–102` 只测不存在的大PID；加平台API mock，断言Windows路径绝不调用os.kill。修好前不要通过启动Gateway或跑该构造路径验证。

**H3 手工/自动Skill草稿创建和发布可越过Skill目录。**

- **位置**：`skills/creator.py:15–22,31`、`skills/validator.py:61–67`、`skills/manager.py:72–92`、`tools/security.py:142–156`。
- **现象和影响**：name在写入前未校验单路径段；description直接拼frontmatter，重复name会覆盖前值。构造 `name="../../outside-name"`、description内换行加 `name: outside-name`，可在 `.skills` 外留下通过校验的Skill。`publish_draft("../foo")` 可把正式 `.skills/foo` 移到workspace其他目录，之后才因scan不含该名称报错。通用Tool预检不覆盖Skill路径，审批不能补上硬边界。
- **核实状态**：已核实路径构造、解析覆盖、移动顺序；未执行文件写入复现。
- **建议及验证**：任何mkdir/write/move前验证合法单名称、拒绝绝对路径和分隔符，检查规范化源/目标在各自根内且拒绝符号链接。frontmatter正确编码标量并拒绝重复键。补穿越、绝对路径、换行键注入、发布源是正式Skill、校验失败无残留测试；`tests/test_skills.py:130–149` 只覆盖正常创建/冲突。删除自动沉淀后此问题仍存在于保留的手工路径。

**H4 保存后历史标识丢失，压缩集合错误，可漏掉旧事实。**

- **位置**：`sessions/store.py:781–786,877–888`、`memory/short_term.py:44–46,102–114`、`context/session_context.py:18–21`、`runtime/state.py:268–277,419–446`。
- **现象和影响**：uncompacted_history只保存role/content，重载后的旧记录id全部是空字符串，算法却按id集合排除recent_window。无guidance且近期窗口含旧记录时compact_source常为空，保留全部历史，压缩失效并最终BUILD超限；有多个guidance user时，未配对的新user可能成为唯一摘要来源，旧记录被错误排除，随后应用近期窗口会漏掉窗外旧事实。不是每次压缩都必然丢失。
- **核实状态**：已核实保存→重载→窗口/集合→状态更新链。
- **建议及验证**：按连续尾窗口的实际位置切分，而不是按并不持久化的id作集合差；把一轮原输入及guidance作为完整组处理。补实际SAVE后重载多个旧回合，再压缩，分别有/无guidance，确认摘要来源及保留窗口覆盖全部应保留事实。`tests/test_compaction.py:42–57` 的人工唯一id和 `test_runtime_flow.py:795–856` 的首次压缩没有覆盖该条件。

**H5 配置Apply可无意撤销默认副作用工具审批。**

- **位置**：`web/backend/config_control.py:199–206`、`config/settings.py:55–57,220–221`、`channels/policy.py:55–59`。
- **现象和影响**：原始JSON省略approval_required_tools时，Settings采用四项安全默认值；任意Apply用previous=[]合并，即使只改模型也写出显式空列表。重载后write/edit/exec/write_stdin不再默认审批，并进入IM可见工具面。
- **核实状态**：已核实省略字段配置的分支；未读取实际私有配置判断当前机器是否触发。
- **建议及验证**：以解析后的有效默认列表合并add/remove，无差异Apply保持原含义。补省略工具权限组、只改LLM、无差异Apply和IM工具集合回归；`tests/test_gateway_settings.py:58–91` 的显式列表fixture不能覆盖。

**H6 WebSocket发送可绕过每条消息附件数量/总量限制。**

- **位置**：`web/attachments/validation.py:87–95`、`web/attachments/store.py:70–93`、`web/backend/app.py:787–791`、`web/backend/coordinator.py:231–235`、`context/builder.py:70–76`。
- **现象和影响**：5图/5文档/100MB仅校验单次上传批次。发送可组合多个批次，或重复同一图片ID上千次；resolve逐项接受，Builder逐项读文件/编码，生成大量image blocks，绕过既有消息级上限并放大内存/API请求。
- **核实状态**：已核实校验缺口和重复引用处理；峰值消耗未测量。
- **建议及验证**：权威send边界按归属已验证的metadata去重并复验数量/总字节；保留上传校验。补绕过前端的重复ID、跨批组合和超量消息测试（现有 `tests/test_web_attachment_http.py:1`、`test_web_attachments_store.py:1` 主要检查上传/归属）。

**H7 grep遍历文件符号链接后可读workspace外内容。**

- **位置**：`tools/filesystem_tools.py:55–79,388–397`、`tools/path_utils.py:15–27`。
- **现象和影响**：只解析初始root；os.walk返回文件链接后直接read_bytes，普通工具的worker策略为空，未对每个候选作canonical workspace检查。指向workspace外的文件链接可泄露外部文本；这是实际文件工具边界缺口，不能只删除Worker策略。
- **核实状态**：已核实遍历/读取链；没有创建链接或读取外部文件。
- **建议及验证**：每个候选读取前复用已有resolve_workspace_path及读路径校验，拒绝越界/设备；也应在读取大文件前检查限额，而不是完整read后检查。补临时workspace内链接到临时外部文件的测试；Windows以具备链接权限的隔离测试或路径mock验证，勿使用真实敏感文件。

### 中

| 编号及位置 | 现象、影响与核实状态 | 建议及应补验证 |
| --- | --- | --- |
| **M1 前台总超时配置无效**。`gateway/turns.py:209–215`、`runtime/runtime.py:224–260`、`runtime/agent_loop.py:150–283,491–526`；配置 `config/settings.py:17` | **已核实**：普通前台未使用turn_timeout_seconds；只有Catalog和已受理协作有wait_for。工具循环/审批/多次重试可远超120秒，持续占路由和容量。Provider单请求timeout不是整轮deadline | 用现有配置建立单轮deadline，安全点拒绝新LLM/Tool并使当前等待有界，明确处理审批和已经发生的副作用；不新增超时配置。补普通多轮LLM/慢工具/审批等待总时限测试。Phase9文档`:294` 声称普通120秒已存在，当前代码不符 |
| **M2 CLI `/new`未切换**。`cli.py:263–287`、`web/backend/app.py:661–721`、`sessions/manager.py:76–77`、`channels/cli_gateway.py:100–105` | **已核实**：命令只得到成功文本，客户端socket仍绑定原conversation/session，后续继续旧历史 | 用现有route绑定能力实现明确切换，保持新会话首次普通输入才建目录；在现有Gateway CLI集成测试验证两次输入落不同session。不能恢复被删除的本地CLI Runtime路径 |
| **M3 CLI错误/审批控制失真**。`cli.py:247–251,265–276,338–343,374–375` | **已核实**：已收到delta时response.error仅换行，隐藏失败；审批答案优先于/stop，使它只拒绝一个工具而不停止任务；approval.resolved被忽略，旧审批可能吞掉后续输入 | error终态始终显示；先识别Stop，按approval_id清理resolved/terminal。补delta后usage失败、审批中Stop、外部解除审批后继续输入。旧 `tests/test_cli.py:79–88` 已有部分意图但无法收集 |
| **M4 普通RUN和Web附件预算不完整**。`runtime/state.py:138–164`、`context/builder.py:65–82`、`runtime/attachment_manager.py:64–65,98–102`、`runtime/agent_loop.py:225–254,399–406,428–441` | **已核实**：BUILD只计原正文/全局工具，尚未包含附件descriptor、当前轮read_attachment schema及图像输入；普通工具结果/guidance增长没有每次请求总量检查，attachment=None直接通过。只有受理协作后的父请求另有实际预算校验；删除协作会去掉这部分保护 | 普通每次LLM请求前复用统一预算，对实际messages/schema检查；图像不能按base64文本长度当模型token，应明确采用现有Provider能力所需的图像预算方法。补大量普通工具结果、guidance、临界附件/加载Skill预算。不要求新的动态裁剪/RAG策略 |
| **M5 单个Session快照直接覆盖**。`sessions/store.py:662–666,681–720`、`runtime/state.py:330–331` | **已核实**：session.json直接覆盖，中断可能留下截断JSON；坏快照被读取器忽略，让已有会话目录不可用。**推测**：崩溃/磁盘故障实际频度未测量。多文件SAVE没有整体事务是明确非目标，单列第7节，不作为另一项实现缺陷 | 对单个快照使用临时文件+原子替换，合并summary/window更新为一次快照写入；注入单文件替换失败，检查原有效快照仍能读取。不要因此引入数据库、跨JSONL事务、日志迁移或兼容恢复层 |
| **M6 Tool业务失败记成成功**。`tools/executor.py:35–48`、`tools/filesystem_tools.py:30–32`、`web/attachments/tool.py:37–48`、`mcp/client.py:110–113`、`mcp/adapter.py:30–31`、`proactive/executor.py:96–110` | **已核实**：metadata.error/error_code及MCP isError未归一到record.error；工具失败计数、UI完成状态、Cron执行success因此失真。非零exec退出也只有文本说明 | 让现有ToolResult明确传递业务失败，在唯一执行边界转为record.error；MCP业务失败不得误触发连接重试。补文件不存在、坏文档、isError、非零退出的端到端失败断言 |
| **M7 MCP超时不重连**。`mcp/client.py:19–34,201–206`、`mcp/server_worker.py:214–217` | **已核实**：TimeoutError包装成RuntimeError后不再被连接错误分类器识别，真实请求超时失去连接级恢复 | 保留类型或识别受控cause；组合 `_request`→Worker分类/重试测试。`tests/test_mcp_client.py:137–149,195–208` 分开测试两处，未验证组合 |
| **M8 MCP关闭遗留Future及名称碰撞**。`mcp/server_worker.py:88–110,204–219,300–304`；`mcp/manager.py:141,183,218–220`、`tools/registry.py:16,29–33` | **已核实**：关闭取消已dequeue的执行命令，CancelledError未完成其Future，finally只拒绝队列内命令；另，刷新server a按mcp_a_前缀会删除a_b工具，(a,b_c)/(a_b,c)同名覆盖 | 分别修复：所有退出路径完成当前Future；按Server实际拥有的工具精确注销，注册时拒绝名称碰撞。分别加已连接调用阻塞时close、a/a_b共存刷新和同名注册测试；不要以改名兼容层解决 |
| **M9 snapshot恢复不完整且游标吞迟到事件**。`web/backend/events.py:68–78`、`web/backend/coordinator.py:306–324`、`web/frontend/src/App.tsx:190–192,211–219,253–254,274–277`、`src/state/session_state.ts:233–237` | **已核实**：过期游标只给运行状态/审批数量，缺request和审批ID/参数；前端不补读历史。新页审批无法操作，长断线缺终态；App还推进被丢弃的非当前会话事件游标，回来后跳过未消费事件 | 在现有snapshot补齐可恢复控制信息，fallback补读持久化历史并保留滚动；游标只确认已经应用/缓存的事件。补超过event_buffer_size、新标签页审批、非当前会话迟到终态的集成回归；已有 `tests/web_socket_client.test.cjs:79–92` 只测独立Socket |
| **M10 消息身份合并与终态全文错误**。`web/frontend/src/state/session_state.ts:64–81,354–361` | **已核实，另作两次实际reducer内存验证**：同文/空正文消息按role+content重复匹配同一历史项，request_id被覆盖；已有partial时completed全文被忽略。活动簇/重试归属错误，缺delta后回复无法修正 | 用持久化消息ID/请求关联一对一匹配，禁止正文作身份；终态全文作为权威。补两条合法同文、附件-only空正文、delta丢失和error纠正临时文本。见 `tests/web_session_state.test.cjs:86–99,134–155` 的缺口 |
| **M11 受理回执丢失后发送卡住**。`web/frontend/src/App.tsx:194–205,318–320,400–426`、`src/state/session_state.ts:124–134,200–207` | **已核实**：send完成但accepted未到就断线，动作尚无turn，断线只中断已有turn；消息保持sending，messageActionIds不清理，原ID重试也被has阻止 | 将待回执动作在断线时转为可重试，复用原client_action_id查询/重发已有有限幂等回执；补“发送成功、accepted丢失”的集成测试。有限回执缓存本身是有意设计，不要求永久幂等账本 |
| **M12 视觉能力配置陈旧**。`web/frontend/src/App.tsx:180–186,222–227,653`、`src/components/Composer.tsx:195–198` | **已核实**：supportsVision只在启动读取；Apply切换active模型后，前端可能继续禁止合法图片或允许后端会拒绝的图片 | 根据active revision刷新现有capability端点，保留服务端门禁；补支持↔不支持模型切换。`web/frontend/tests/control_plane_mock.spec.ts:113–142` 只验证协作开关刷新 |
| **M13 附件会话/重试事务丢失**。`web/frontend/src/App.tsx:235–278,359–430,529–542` | **已核实**：切换会话不清pendingAttachments；旧metadata送入新owner被拒；上传迟到结果按当前列表顺序映射，无会话/文件版本闸门。附件-only因无正文无法重试，刷新后重试不恢复原附件ID | 附件草稿绑定会话；上传请求快照绑定文件ID和会话版本，迟到拒绝；重试保留原session、正文、公开附件IDs，允许纯附件。补切换/移除/增加附件时迟到返回和刷新重试，现有 `web/frontend/tests/workbench_attachments.spec.ts:40–124` 没覆盖 |
| **M14 会话外部响应/Hook关联缺少作用域**。`web/frontend/src/App.tsx:465–495`；`hooks/channel_status.py:17,21–28` | **已核实**：普通Inspector的A慢响应可覆盖B检查器；共享状态Hook仅以call.id为键，不同会话同ID会把完成状态发给另一Adapter；取消还可能遗留条目 | 分别复用已有前端请求版本/会话闸门；Hook关联限制在单轮或包含turn/session键，并清理取消路径。补A/B交错Inspector和跨会话相同tool_call_id测试，不新增通用事件平台 |
| **M15 测试入口/fixture已过期**。详见下方6.1；工程配置 `pyproject.toml:6–10`、`web/frontend/package.json:5–9`、`tsconfig.json:19` | **已核实**：测试导入被删除入口/缺失evals，Node测试靠手工/tmp编译产物，fixture不满足真实服务校验；当前依赖没有pytest。无法由干净仓库重现一个可信测试集合。未实际跑pytest，不宣称失败数量 | 先清理旧合同，直接引用当前源码；有效前端断言纳入已安装runner，统一测试命令；明确测试依赖。不要改产品恢复旧接口，不把已知/bin/bash平台失败算回归 |
| **M16 Cron保存失败后仍改内存/启动**。`proactive/cron.py:140–142,187–196,227–238` | **已核实**：create保存失败留下可执行任务；delete保存失败已pop内存Job；run_due先create_task再写快照。违背Phase7`:290,294` 的先持久化后执行/删除失败保留 | 先保存候选状态，再替换索引/启动Task；失败保持原内存事实。补create/delete/run_due的每个持久化失败点；不要新增回退兼容状态 |
| **M17 Cron两年逐分钟搜索拒绝合法闰日**。`proactive/scheduler.py:48–57` | **已核实**：刚过闰年2月29日时 `0 0 29 2 *` 下次超过两年，被报无可执行时刻；标准五字段规格未给该限制。**推测**：同步约百万次循环会明显影响共享事件循环，耗时未测量 | 评估成熟Cron计算实现；现有依赖未提供该计算能力，若新增库只服务现有五字段/DST需求。补闰日、稀疏计划、不可能日期及事件循环响应验证，不扩展调度框架 |
| **M18 命令进程/输出回收无界**。`tools/exec_sessions.py:31–52,75–88,116–159,163`、`tools/security.py:19`、`tools/shell_tools.py:113–118` | **已核实**：deadline仅poll检查，未再poll则进程不自动超时；已退出也不及时释放8个槽；输出_chunks无界；没有Gateway关闭回收。普通communicate先收全文再截断。/bin/bash是既有Linux范围，不是本问题 | 启动时安排自主deadline和完成清理，输出用有界缓冲；关闭时收回所属进程及读取Task。补无poll超时/退出、输出洪水、关闭清理，不要求跨平台命令功能 |
| **M19 附件先读/解析全文才截断**。`web/attachments/store.py:30–36`、`web/attachments/tool.py:45`、`web/attachments/documents.py:101–109,125–148` | **已核实**：上传先无界read后验总量；PDF内容流/Office全表抽取同步在async路径，全文列表/join后才截断。**推测**：压缩内容流/超大稀疏维度可耗尽内存或阻塞其他聊天，未实际构造攻击样本 | 上传按上限增量读，解析中限制实际展开工作量/迭代/输出并设置时间界限，将阻塞解析移出事件循环；补压缩PDF、稀疏XLSX和普通聊天并发。Phase10`:69` 已要求安全限制，不能只限制返回字符串 |
| **M20 Provider/SDK双重重试**。`llm/openai_compatible.py:41–45,103–126`；安装SDK `_constants.py:10`、`_client.py:747` | **已核实**：未给SDK传max_retries，其默认2次又叠加外层重试；默认一次逻辑请求最多9次底层尝试，设置0也不关闭SDK重试，耗时/费用上限失真 | 由SDK一个位置负责重试，传入现有配置，删除重复循环；若必须保留自定延迟，先说明现有需求并禁用SDK内层。`tests/test_openai_compatible_llm.py:460–503` 的FakeClient没覆盖真实SDK内层 |
| **M21 微信旧请求故障覆盖新Binding**。`channels/weixin.py:331–338,354–365,394–410,790–825` | **已核实**：send/poll成功路径有epoch，旧凭据等待期间重扫提交后，旧请求异常直接expire/disconnect当前记录，可清空新凭据或标离线 | 错误回写也核对请求epoch并用现有Binding锁；注意锁内_send_notice调用，避免重复加锁。补旧send/poll故障与重扫提交交错；`tests/test_weixin_transport.py:412–463,814–893` 各测单独场景 |
| **M22 飞书先断线再退出不重连**。`channels/feishu_ws.py:227–234,256–277` | **已核实**：disconnected置_ready=False，后续进程退出误当从未ready直接返回，不安排重连 | 用现有_ever_ready区分从未成功与成功后失联；补ready→disconnected→failed/EOF。已有 `tests/test_feishu_transport.py:1624–1676` 只测ready后直接退出 |
| **M23 普通文本中的tokenizer标记抛错**。`sessions/token_counter.py:11`、`mcp/manager.py:202`；安装tiktoken `core.py:87,110–124` | **已核实**：默认encode禁止特殊标记；用户讨论 `<\|endoftext\|>` 等字面文本时可能在BUILD/SAVE/工具计量抛ValueError，普通文本被当协议特殊token | 对自然文本用encode_ordinary或disallowed_special=()；统一现有入口，补用户输入/ToolResult/Skill正文的特殊标记字面量 |
| **M24 Web事件队列无界**。`web/backend/events.py:44–47,64–70`、`web/backend/proactive_events.py:63–70`、`web/backend/app.py:731–735,548–556` | **已核实**：回放deque有界但订阅Queue无限；慢连接持续积压，删除会话不清hub的events/id/locks。**推测**：长期内存峰值未测量 | 限定单连接队列，溢出要求snapshot/重连；删除/过期时清实际会话缓存。不新增持久化事件库，补慢客户端和反复创建删除场景 |
| **M25 Dream失败退避仍依赖失败的磁盘**。`proactive/dream.py:84–115,135–136`、`proactive/service.py:465–483,642–674,547–549` | **已核实**：defer写同一文件失败时旧到期deadline不变；调度重新算等待可为0，重复启动并失败。若状态读取本身抛错，deadline计算又在领域try块之外，scheduler Task可直接退出。实际磁盘故障未复现 | 复用Breakbeat现有进程内deadline先推进、持久化尽力提交的方式；调度deadline读取也隔离故障，避免零等待。补磁盘持续写失败/坏JSON而其他领域继续运行 |
| **M26 上下文读模型和手动压缩读数失真**。`web/backend/read_models.py:166–175`、`gateway/catalog_actions.py:236–275`、`web/backend/app.py:925–935` | **已核实**：Inspector的token_breakdown传空ProfileMemory和skills，计数不含这些实际根上下文来源；手动压缩只写COMPACT，圆环只读最近SAVE，直到下次聊天仍显示压缩前值。不返回画像/Skill正文是Phase6`:252` 的有意隐私范围，正文缺失本身不是缺陷 | 只补真实来源的聚合计数，继续不返回其正文；手动压缩提交时更新现有观测事实，不新增JSONL。补含画像/Skill和压缩后的context-window API断言 |
| **M27 中文输入法Enter触发发送**。`web/frontend/src/components/Composer.tsx:138–143`、`SlashCommandMenu.tsx:56–77`、`SessionSearchDialog.tsx:42–54` | **已核实**：Enter快捷键无isComposing/composition检查。**推测**：具体输入法的候选确认触发方式未在真实GUI复现 | composition期间不发送/选择；补真实composition事件及必要平台分支。需求就是现有中文输入，不新增快捷键体系 |
| **M28 账号旧响应复活已删行，测试没有模拟旧值**。`web/frontend/src/components/ChannelAccountSettings.tsx:79–85`、`web/frontend/tests/control_plane_mock.spec.ts:276–282,323–328` | **已核实**：load返回无版本闸门；测试在等待结束后按最新deleted生成数组，没抓取删除前快照，所以不能发现旧响应覆盖已删账号行 | 应用请求版本/删除修订闸门；mock在请求开始就固定旧响应再延迟返回。补浏览器无复活断言，不改变服务器删除语义 |

### 6.1 过期测试与覆盖缺口的具体处理

以下均为**已核实的静态合同不一致**；没有运行测试套件，因此没有“通过/失败数量”。修复对象是测试入口或需求，不能为了让它们通过恢复旧兼容接口。

| 位置 | 判定及处理 |
| --- | --- |
| `tests/test_cli.py:6`、`test_web_host.py:33,45–46,79,140` | 导入已删除channels.cli，或调用旧create_app(settings,runtime)、/ws、旧Coordinator；转向现有Gateway端到端合同，保留仍有意义的错误/流式验证 |
| `tests/test_openai_compatible_llm.py:156,552,567,574–575`、`test_channel_output.py:31,76` | 旧cli.build_llm/resolve_provider；旧两参数on_tool_started；直接用llm.factory和当前三参数事件，不恢复旧函数 |
| `tests/test_basic_tools.py:85,315–416`、`test_tools_loop.py:832` | Loader给Path/缺Settings导致NowTool配置不符；搜索仍断言DDG JSON/单Yahoo。更新当前Settings和HTML搜索链fixture，删除无消费者的旧搜索断言 |
| `tests/test_sessions.py:68–85,333–361`、`test_runtime_flow.py:80–84` | 期待旧缺route/pinned/archived快照恢复或旧Outbound channel字符串；当前合同明确拒绝/使用Recipient，修fixture而非回退产品 |
| `tests/test_eval_cases.py:1`、`test_eval_metrics.py:5`、`test_gaia_classify.py:3`、`test_gaia_runner.py:1`、`test_gaia_sample.py:1` | 导入本仓库不存在的evals。若所有者没有当前评估需求，删除这五份过期测试；若保留，先纳入真实被测源码，不能加空模块/假实现 |
| 根 `tests/web_*.test.cjs/mjs`，例如 `web_overlay_position.test.mjs:4`；`web_socket_client.test.cjs:45`、`web_observability_view.test.mjs:18` | 依赖手工/tmp编译文件，部分还期望旧/ws、旧观测标题；placeOverlay已无产品实现。有效用例直接进入现有runner并读取源码，废弃用例删除 |
| `tests/web_activity_steps.test.mjs:19–25`、`web/frontend/src/state/activity_steps.ts:49,72–78` | 旧“工具完成不展示”断言与当前更新真实步骤合同不符；保留新版活动簇含义，不制造旧编译夹具 |
| `tests/test_weixin_transport.py:1029,1136,1189,1241,1322,1372,1390`；`test_gateway_binding_lifecycle.py:219,245,296,391,443,483,525,619,670` | 7+9处skip留下旧重扫空闲预约/Lease合同；移除废弃用例，保留当前epoch/永久删除边界测试，不新增skip以隐藏真实缺陷 |
| 前端 `control_plane_mock.spec.ts:26`、`phase9_visual.spec.ts:84`、`proactive_workspace.spec.ts:56` | 协作timeout=60000超过服务端14400硬上限；context-window假字段used_tokens/max_tokens不符实际类型。按拆除清单删协作fixture，剩余fixture对真实类型/validator验证；tsconfig只检查src，不能以build证明tests类型正确 |
| `tests/test_web_attachments_store.py:29–37`、`test_web_attachment_runtime.py:1` | “atomic”只在落盘前失败；未覆盖写/移动中途失败、跨轮引用、原图重放及全部通道隔离。补故障点和实际SAVE→重载验证，未实现的历史重放先按第8节定合同，不能凭测试名宣称覆盖 |
| `tests/test_phase9_coordinator.py:1000–1039,438–499`、`test_phase9_persistence.py:271–291` | 终态sink故障只到Coordinator，不到最终回复；Stop仅合作取消；长正文只连续x不测代码排版。Phase9文档`:503–506` 自己列为暂缓，拆除前不要宣称已端到端验证；删除功能后无需补协作专属测试 |

### 低

| 编号、位置和核实状态 | 现象及影响 | 建议 |
| --- | --- | --- |
| **L1 已核实**：`bus/messages.py:56–85,141–150,165–168`、`channels/base.py:153–156`、`gateway/principals.py:37–41`、`sessions/manager.py:195–209`、`sessions/store.py:668–679,765–779` | 显式legacy构造/旧目录/TypeError后无参factory回退；后者还可能把factory内部真正TypeError误当签名问题再次调用。与AGENTS直接删除废弃路径原则相悖 | 现存调用方一次改为唯一route/recipient/有参factory接口，然后删分支；不加新兼容层。旧目录是否仍在真实数据中未读取，本文不承诺数据迁移 |
| **L2 已核实**：`hooks/events.py:1–4`、`memory/event_memory.py:1–9`；全产品/测试调用搜索 | 无消费者的Hook事件常量、预留EventMemory；不提供当前功能却增加架构说明/维护范围 | 直接删除，并更新架构职责；ProactiveManager空壳按第5节同步删除，不能以未来扩展保留 |
| **L3 已核实**：`skills/validator.py:48–95`、`skills/installer.py:47–55` | 自写YAML子集不能可靠解析常见多行description/嵌套值；clone无总timeout、交互禁止及取消子进程清理，可能长期占轮 | 评估成熟安全YAML解析器并拒绝重复键，删除旧手写解析，不叠加兼容分支；clone复用现有超时需求、关闭交互并清理取消。补标准frontmatter和阻塞clone测试 |
| **L4 已核实**：`llm/openai_compatible.py:160–172`、`web/backend/app.py:216–224,635–637,751–767` | 缺失/空/错误类型arguments静默{}，不符严格协议；部分JSON动作不是object或字段形状错误直接抛TypeError/AttributeError，连接中断或500 | 现有入口显式验证JSON object和字段类型、返回稳定错误，禁止非法Tool参数执行；不用新协议框架 |
| **L5 已核实**：`web/frontend/src/components/SessionSidebar.tsx:119–166`、`web/frontend/package.json:14` | 手工Portal菜单重复实现已安装Radix能力，缺打开焦点、方向键、关闭焦点归还，注释仍称Radix | 使用现有Radix DropdownMenu实现当前菜单合同，删除手工层；补键盘/焦点验证 |
| **L6 已核实**：`web/frontend/src/styles/components/composer.css:177–216,219–303`、`sidebar.css:135–173,223–294`、`styles/components.css:5–13,89–123` | 多轮“最终覆盖”叠加旧DOM规则；共享层仍有领域规则，不符 `DESIGN.md:72` | 只保留各组件最终有效规则，删除不存在DOM选择器/重复覆盖；按现有双主题/宽度做视觉验收，不新增CSS抽象 |
| **L7 已核实实现，部分显示影响为推测**：`web/frontend/src/api.ts:13–35`、`channel_control_api.ts:36–57`、`proactive_api.ts:4–17` | 三套fetch/JSON/错误归一化，支持field_errors/detail.code不一致；Proactive真实结构化错误是否显示原JSON未动态确认 | 在现有api.ts直接复用基础请求及错误处理，领域文件保留endpoint/类型，不增加通用SDK层 |
| **L8 已核实**：`tools/web_tools.py:42–73`、`web/frontend/src/components/slash_command.ts:1–3`、`state/composer_segments.ts:11–12`、`proactive_api.ts:28–29`、`App.tsx:21` | 无消费者的DDG即时答案辅助实现、removeTrailingSlashToken/composerPlainText/allSnapshots及未用import | 按全仓调用搜索删除死入口/辅助函数；不据无前端调用便删除后端公共REST |
| **L9 已核实**：`web/frontend/package.json:14–20,25–27`、`pnpm-lock.yaml:20–25,42–53`、`package-lock.json:593,1899,1908,2270,3257,4764` | 多个latest，npm/pnpm锁出不同直接依赖，构建/部署环境不一致。**推测**：`pnpm-workspace.yaml:1–2` 占位值的当前运行影响未验证 | 以实际Docker/本地流程选一种包管理器和锁文件，统一验证命令、去另一套；不宣称任何具体版本有漏洞，不引入依赖升级平台 |
| **L10 已核实**：`runtime/agent_loop.py:69–72,79–90,218,290`、`tests/test_agent_loop.py:130–133` | tool_registry/tool_runner运行时覆盖仅在测试中使用；生产Worker本来另建Loop，persist_tool_calls生产调用只有默认true。保留这些入口扩大安全执行面，缺乏当前产品需求 | 按第4节使用已有构造注入测试独立Loop，删除无生产调用的参数；保留Proactive的真实Provider/streaming覆盖，不新建能力或插件注册层 |

## 7. 认为不合理的有意设计

本节的现状均**已核实**，评价和改法是**推测/建议**。这些没有放入“代码违背规格”的缺陷清单；若所有者不改变对应需求，重构智能体不应自行实施新行为。

1. **本机Web无认证且无同源边界**。Phase6明确不提供认证/跨域开放（`docs/phases/2026-06-15-phase-6-web-observability.md:51–58`）；`Turning-Good-Agent/web/backend/app.py:558–560,723–726` 的Web/主动WS直接accept，没有Origin/Host验证。loopback只能限制监听网卡，不能证明请求来自可信页面。**推测**：恶意网站跨站连接本机WS并读取主动快照/发消息的可达性取决于实际浏览器本机网络保护，未动态验证。建议保护现有本机工作台的同源Origin/Host，明确开发代理允许源；该需求是保住当前本机控制面，无需账号、多租户或公网部署系统。
2. **审批Hook异常后继续**。`docs/TURNING_GOOD_AGENT_SPEC.md:522` 和Phase3明确Hook故障隔离；`hooks/manager.py` 的前置Hook异常捕获后继续是按设计实现。把权限检查也纳入同一种可忽略扩展点，会令审批异常成为放行动作的条件。建议所有者确认“状态显示/监控失败可继续，强制权限失败拒绝”这一现有安全职责区别，再直接改现有顺序链，不造Hook插件平台；位置 `Turning-Good-Agent/hooks/manager.py:17–44`、`runtime/tool_call_runner.py:135–142`。
3. **自动审批是跨会话全局持久策略**。`docs/TURNING_GOOD_AGENT_SPEC.md:532–536` 明确CLI/Web共用一个开关；`config/settings.py:252–261` 持久化，`runtime/state.py:188–192` 每轮使用全局值。单会话选择“完全访问”会影响其他Owner会话的后续工具；这是方便与权限范围之间的产品取舍。本文不假设所有者需要会话级权限；若不接受全局效应，需先确认实际作用域，再改现有策略，不能留“全局旧模式”的兼容开关。
4. **自动学习信号检查几乎每轮发生**。`docs/TURNING_GOOD_AGENT_SPEC.md:579` 允许明确学习信号提前触发；`proactive/skill_evolution.py:137–151,273–295` 对非第N轮也调用模型检测最新用户消息，再可能调用观察/选择/Draft模型。成本、私人会话审阅和无效Observation的维护负担与token节省目标不协调；每日演进还持续依赖保留期内原文。建议按第5节拆除，保留用户明确请求的手工Skill流程；不替换为另一种自动自进化系统。
5. **协作以同模型、固定只读模板实现，但让核心循环承担全部细节**。Phase9明确只用fan-out/pipeline、深度1、同Provider、无动态DAG/重试/恢复（`docs/phases/2026-06-15-phase-9-multi-agent.md:30–51,281–288`）。这些约束本身不算bug；不合理处是未提供独立专家能力就为每次委派增加多份prompt、状态投影、预算/取消/审批组合，单人维护成本很高。建议按第4节删除；没有新复杂任务需求依据，不能建议引入外部Multi-Agent框架替代。
6. **Cron的delivery_channels保留但不影响Fanout**。规格已经明确它只是兼容字段（`docs/TURNING_GOOD_AGENT_SPEC.md:582`）；实际模型类型仍必填并保存它（`proactive/types.py:88,134–138`），通知按origin/all_subscribed和在线收件人派发（`proactive/notifications.py:1–204`）。这会让配置看起来能选择渠道却无效，且违反AGENTS不保兼容原则。建议删这个无效输入/快照/UI字段，剩余通知按已有真实规则工作。离线不重试/不补发也是明确设计，本文不把它当缺陷，不建议新建durable delivery queue。
7. **跨文件事务和Windows直接写回退**。Phase7明确不做消息/游标/SAVE跨文件崩溃事务（`docs/phases/2026-06-15-phase-7-proactive-memory.md:606–607`）；`proactive/store.py:20–28,55–57`、`memory/long_term.py:58–65` 也不能保证多文件一起提交。原子替换被阻塞时直接写回退尤其削弱单文件抗中断保证；应诚实记录持久化能力。建议先保证单文件安全失败及M5中的有效快照，若确需多文件事务，由所有者明确“进程/电源中断也不能丢一轮”这一目前未要求的需求后再决定存储，不默认扩展方案。
8. **大上下文先完整BUILD，超限直接拒绝**。这是规格明确规则（`docs/TURNING_GOOD_AGENT_SPEC.md:369–375`），COMPACT影响下一轮（`:324`），不是“没有自动预压缩”的缺陷。当前入口一旦因长原文超限，恢复途径有限，且默认窗口很大。是否增加提前压缩/新的恢复交互取决于所有者当前长会话需求；本报告只建议先修H4和M4，不假设RAG、自动裁剪或更复杂记忆层。
9. **Docker要求手工创建被忽略的Skill目录**。`docs/STARTUP.md:100–104` 已明确这一步，`Dockerfile:28,43` 的COPY依赖未跟踪 `.skills/`，因此不能报成“未文档化构建失败”。这个构建前置补丁不符合长期简化原则；建议镜像不复制不存在的私人目录，或只跟踪确属产品的内置Skill文件，私人Skill由已有运行目录提供。选哪一项取决于所有者确认内置Skill是否仍是产品交付物，不新增迁移/同步服务。

## 8. 文档与代码不一致之处

以下每行均为**已核实**，以“当前代码行为”为准。阶段文档的实现记录和前面的设计计划有冲突时，也应清理旧计划，不能靠恢复旧代码消除不一致。

| 文档位置 | 当前代码证据和真实行为 | 后续同步方式 |
| --- | --- | --- |
| `docs/PROJECT_ARCHITECTURE.md:40` 称tests被忽略、不上传 | `.gitignore:1–17` 没有tests忽略规则；`tests/test_eval_cases.py:1` 等均被当前Git跟踪，最近提交 `19b0f8c` 已纳入测试 | 改成已入库测试，区分历史旧用例，不继续沿用“不上传”描述 |
| `docs/TURNING_GOOD_AGENT_SPEC.md:453`、架构`:198` 称搜索用Yahoo | `Turning-Good-Agent/tools/web_tools.py:191–219` 是DDG HTML→Bing→Yahoo顺序 | 写真实检索链；不修产品以满足旧单Yahoo测试 |
| `docs/PROJECT_ARCHITECTURE.md:361` / Phase3称CliChannelAdapter在channels/cli.py | 当前 `Turning-Good-Agent/channels/cli_gateway.py:301` 为CliGatewayAdapter，终端渲染在 `cli.py:329`；旧文件不存在 | 更新入口和职责，删除旧路径 |
| 架构`:280` 称多渠道流式后置 | `Turning-Good-Agent/channels/web.py:68–71` 已把delta交给Web；IM仍只有最终文本 | 分别注明Web已支持、IM纯文本终态 |
| 规格`:105,216` 要求/new切新session且不建空目录 | `Turning-Good-Agent/sessions/manager.py:76–77` 仅响应文本，`cli.py:263–287` 无切换（M2） | 保留现有需求，修实现；不是删掉规格掩盖缺陷 |
| Phase1 `docs/phases/2026-06-15-phase-1-runtime-mvp.md:88` 默认新隔离会话 | `Turning-Good-Agent/cli.py:199` 默认default，`tests/test_cli_gateway_client.py:297–308` 明确测试当前行为 | 更新旧阶段计划的默认CLI会话描述 |
| Phase3 `docs/phases/2026-06-15-phase-3-hooks.md:65` 批准接受y/yes/允许 | `Turning-Good-Agent/cli.py:415–416` 只接受y/Y，`tests/test_cli_terminal_mode.py:56–60` 已确认 | 按当前合同改文字，勿增加旧批准词兼容 |
| 架构`:379`、Phase6`:65–68` 称首条非空消息才建session | `Turning-Good-Agent/web/backend/app.py:456–460`、`coordinator.py:271–275` 为上传附件先建空Web会话 | 同步Phase10已有设计例外；不报告空会话创建本身为bug |
| 规格`:469`、架构`:276` 称Tool参数严格JSON object | `Turning-Good-Agent/llm/openai_compatible.py:164–165` 缺失/空/错误类型回退{}（L4） | 修代码的严格协议验证，并保留这一有效需求 |
| Phase9 `docs/phases/2026-06-15-phase-9-multi-agent.md:294` 普通turn有120秒总时限 | `Turning-Good-Agent/gateway/turns.py:209–215` / Runtime无普通总deadline（M1） | 修现有前台配置；拆协作也不应继续宣传不存在的保证 |
| Phase9`:5,71`、架构`:7` 写单一AgentLoop | `Turning-Good-Agent/multi_agent/worker_runner.py:49–85` 实际创建另一AgentLoop实例复用Provider，且Coordinator无条件构造它 | 删除协作后自然只剩前台实例；删前应说明“同一实现类”与“单实例”的区别，不当作第二Gateway |
| 规格`:590–604` 仍描绘独立Planner | 当前 `Turning-Good-Agent/multi_agent/delegate_tool.py:61–90` 直接接受父模型任务数组；Phase9`:227` 明确没有额外计划LLM阶段 | 按删除后的单Agent功能更新目标图；不要新增Planner |
| Phase9`:269–272` 要求Worker联网读拒绝本机/私网 | `Turning-Good-Agent/multi_agent/worker_profile.py:185–195` 复制WebFetch但不注入网络策略，`tools/security.py:132–139` 只校验协议；Worker也可访问本机HTTP | 删除协作消除该Worker路径；H1的非Owner普通联网路径仍单独修。不能把文档声明当安全证据 |
| Phase9`:3,489–501` 写实现尚未提交 | 当前HEAD已跟踪实现，开工git status干净；历史验收结果是旧时点记录 | 删除功能后移除阶段文档；保留时应更正状态，不推断已推送 |
| Phase10 `docs/phases/2026-08-20-phase-10-web-attachments-multimodal.md:181,268` 称read_attachment结果持久化为文本消息并带读取metadata | `Turning-Good-Agent/runtime/state.py:309–332` 只存user/assistant及工具明细；`sessions/store.py:864–875` 工具明细存content/args等，不保存ToolResult metadata。正文在tool_calls.jsonl，不在聊天历史；规格`:376,538` 又明确工具/attachment不跨轮 | 当前以代码为准，明确工具明细与消息历史区别；所有者未确认跨轮引用需求前不新增隐藏全文消息 |
| Phase10`:196` 称历史按原附件重放图片；`:226` 称重连后read_attachment仍可读旧文档 | `Turning-Good-Agent/context/builder.py:62–63,70–82` 历史只有字符串，图片只来自当前消息；`runtime/state.py:163–164`、`web/attachments/tool.py:37` 只授当前IDs访问权 | 同步为“卡片可展示，模型访问需当前消息重新引用”；若所有者确认原文续读/历史图片是现有必需需求，再另行实施，不宣称已经支持 |
| Phase10`:179` 无文本应结构化Tool Error | `Turning-Good-Agent/web/attachments/documents.py:109,148` 返回成功占位文本 | 按当前行为记录差异；如保留该需求，返回明确业务失败并通过M6归一化 |
| Phase7 `docs/phases/2026-06-15-phase-7-proactive-memory.md:375` / 架构`:166` 称画像成对原子写入 | `Turning-Good-Agent/memory/long_term.py:58–65` 是两文件顺序替换和异常回滚，不是崩溃事务；Phase7`:606` 又声明不做跨文件崩溃事务 | 准确写出单文件替换、异常回滚和崩溃边界，不将有意非目标列成另一个缺陷 |
| Phase7`:140` 称画像配额固定，`:594` 又可编辑 | `Turning-Good-Agent/config/settings.py:134–136`、`web/backend/config_control.py:72–74,328–330`、`config/validate.py:206–218` 可编辑且校验 | 以最新行为统一，删除早期“固定不可编辑”说法 |
| Phase6`:384` 和架构`:379` 称Radix会话菜单 | `web/frontend/src/components/SessionSidebar.tsx:119–166` 是自绘Portal；只有其他对话框使用Radix | 修L5并同步真实组件职责；不要保留两套菜单 |
| Phase6`:345` 称用户消息纯文本 | `web/frontend/src/components/ChatTimeline.tsx:96` 对user也用ReactMarkdown | 记录当前显示行为，由所有者确认是否还需纯文本，不擅改产品需求 |
| Phase6`:212,214`、架构`:442` 称#proactive概览/通知顶部居中 | `web/frontend/src/proactive_types.ts:171–174` / `App.tsx:304–305` 将概览重定向Cron；`App.tsx:652`、`styles/components/overlays.css:14–16` 在聊天区把通知放Composer上方，新测试也按此验收 | 更新范围和位置，不为旧概览/位置恢复无需求UI |
| 规格`:485` / 架构`:230–233` 称有skill-creator、skill-installer、grilling内置文件 | 本工作树 `.skills/` 无SKILL.md且全目录被 `.gitignore:11` 忽略；`Turning-Good-Agent/skills/manager.py:32–45` 只扫描现存文件，不生成内置Skill | 明确这些是否仍属交付物，若是则纳入真实产品源；若不是删过时宣称。不要生成空兼容Skill |
| `docs/TURNING_GOOD_AGENT_SPEC.md:560` 的SAVE唯一可靠持久化边界表述过强 | `Turning-Good-Agent/sessions/store.py:59–61` 创建session立即写；`runtime/runtime.py:297–314` 协作trace/usage也提前写；手动Catalog压缩另写snapshot/usage（`gateway/catalog_actions.py:236–275`） | 限定为普通聊天结果主要持久化阶段，列出当前创建/控制/协作例外；协作删除后其例外删除 |

`docs/STATUS.md:1` 和 `docs/DECISIONS.md:1` 都只有标题，无法提供可用的待办/取舍记录。最近10条提交涉及Gateway/附件、测试入库和工作规则，这两份空文档没有反映当前进度；按本次只读要求未修正。后续重构应把所有者新决定记入现有DECISIONS，并更新STATUS，不以本文的建议冒充已批准的新产品需求。

## 9. 没有覆盖到的范围

### 9.1 阅读覆盖和抽查方式

**已核实的覆盖**：团队合计通读 `Turning-Good-Agent/` 的133个Python源文件，26,209行；`web/frontend/src/` 的72个文本源文件（22 TS、28 TSX、14 CSS、8 SVG），7,481行；根tests的107个测试/辅助源文件（93 Python、14 JS/TS）及前端tests的12个文件，合计28,243行。主审完成模块依赖、数据流及两份拆除表；最多三个子智能体仅分片阅读代码、测试、设计并交带行号问题摘要。写入问题前主审抽查了报告所引用的关键代码、调用方与测试位置，没有仅凭搜索词将有意设计报成缺陷。

前置文档AGENTS、完整规格/架构、十份阶段文档由团队通读；同时读取STATUS、DECISIONS、CLAUDE、DESIGN、README/文档索引/STARTUP、最近10条提交和工程配置。工程配置包括pyproject、公开settings.example、Dockerfile、两份compose、git/dockerignore、前端package/tsconfig/vite/playwright/index/pnpm-workspace。没有CI配置文件可供本仓库内核查。AST导入图及引用搜索覆盖全部后端源，拆除搜索也包括前端、测试、README和阶段文档；未把MCP后台Worker误归为多智能体。

### 9.2 明确未读或未完整人工审阅的文件

- **未读取** `settings.local.json`；未核对真实密钥、服务URL、实际开关或现场配置是否触发H5。报告仅描述条件，不判断当前机器已被攻击。
- **未读取** `.sessions/` 内真实会话、画像、附件、Channel账户/凭据等运行数据；未核对历史数据能否按当前schema加载。已列出的存储问题来自源码和测试，而非检查所有者数据。
- **未通读** `.venv/`、node_modules、第三方完整源码、Python字节码、生成的web/static、.git对象和其他生成缓存；只查阅安装OpenAI SDK的 `_constants.py:10` / `_client.py:747` 重试默认值，以及tiktoken `core.py:87,110–124` 的encode语义。没有做全量依赖漏洞或许可证审计。
- **两份锁文件只审阅** 根元数据、直接依赖解析和相关包记录，未逐行人工检查所有传递依赖的integrity/平台条目；具体锁版本差异已记录L9，不推断安全漏洞。
- **未检查像素内容** `assets/tga-workspace.png`、前端public的PNG/图标等二进制资产；src里的SVG文本已读。二进制没有源码行号，删除资产建议以对应import/引用行作为定位证据。
- `.skills/` 在本次工作树没有SKILL.md可读；没有把环境里的其他Skill库当作这个项目的交付内容。`evals/` 被测源码不在本仓库，不能通读其实现，已记录M15。

### 9.3 未执行的验证和仍不确定的影响

没有运行pytest、Playwright、Vitest套件、前端构建、compileall或安装依赖；没有修改.venv/pyproject，没有启动/停止Gateway、二维码GUI或任何8000端口进程，没有访问本机HTTP/WS，没有操作Docker容器/卷。唯一动态辅助检查是子智能体在Node内存中载入实际前端reducer，验证同文历史错绑与partial后终态全文不替换；未写编译文件或修改仓库。

因此：未测量坏附件/输出洪水/稀疏Cron的耗时、内存和实际频度；未复现真实微信重扫/飞书断线交错、MCP服务关闭竞态、Git clone阻塞、磁盘满/Windows文件锁/电源中断、真实中文输入法或浏览器跨站本机访问。相关源码确定的分支标“已核实”，环境依赖和资源影响标“推测”。也没有验证模型遵循提示词所声称的脱敏/画像质量；提示词不能作为硬安全证明。

审查开始时Git工作树干净。此次唯一允许写入的仓库文件是本文；未改STATUS/DECISIONS/产品/测试/配置，未创建Git提交或推送。后续实施者需要按风险逐步验证，不应把历史阶段文档中的通过数字或本文静态核查当作当前分支已通过测试。




