# Turning-Good-Agent Phase 9 Multi-Agent 协作契约

> 状态：**实现已落在未提交工作树，最终交付与完整验收待收口。** 本文是 Phase 9 Multi-Agent 的唯一产品与接口契约；若本文与当前代码冲突，以当前代码为准，再按变更流程更新本文。

> Phase 9 复用 Phase 7 的单 Gateway、单 `AgentRuntime`、单 `AgentLoop`、共享 `AsyncMessageBus` 和主体隔离，复用 Phase 6 的 Web 会话观测与 Stop/审批语义，遵守 Phase 8 的 IM 最终纯文本契约。Phase 9 不创建第二套 Runtime、Bus、Channel Host 或主动服务。

## 1. 目标与设计定位

Phase 9 解决三个问题：

1. 根据任务是否适合拆分，选择一个经过约束的 Multi-Agent 协作拓扑；
2. 让用户在 CLI 和 Web 中看到每个 Agent/Worker 的安全运行状态、结果和 Token 用量；
3. 让复杂任务可以在有界的长运行中并行研究、串行加工或验证，而不破坏父 Agent 的安全与最终答复责任。

这里的“选择框架”是 **TGA 内部的协作策略/拓扑选择**，不是运行时安装、切换或混用 LangGraph、CrewAI、AutoGen 等外部 Runtime。外部成熟产品只作为模式依据：

- OpenAI Agents SDK 的 `agents-as-tools`：Manager 保持最终用户答复所有权，专家 Agent 执行有界任务；
- LangGraph 的 typed State、Node、Edge、显式依赖和事件流；
- CrewAI 的 manager/process、生命周期回调和用量聚合。

TGA 只吸收这些已验证的约定，不引入第二套 Memory、Tool、Approval、Checkpoint 或 Session 模型。

参考：

- <https://developers.openai.com/api/docs/guides/agents/orchestration/>
- <https://developers.openai.com/api/docs/guides/agents/integrations-observability/>
- <https://docs.langchain.com/oss/python/langgraph/graph-api>
- <https://docs.crewai.com/v1.15.14/en/concepts/flows>

## 2. v1 范围与明确不做的事

### 2.1 v1 实现

- 父 Agent 根据模型判断，选择固定拓扑并创建一个 `MultiAgentRun`；CLI/Web 的 `auto` 只提供委派能力，不改变模型的最终选择权；
- 深度为 1 的父 Agent + Worker 协作；
- `fan_out_fan_in`、`pipeline` 两种固定模板；单 Worker 验证使用单任务 `fan_out_fan_in` 表达；
- Worker 独立逻辑上下文、完整只读 `USER.md`/`SOUL.md`、自包含 task brief 和固定只读 Tool Profile；
- 父 Agent 最终汇总、普通 Tool 审批和副作用执行；
- 父 Session 结构化 trace、真实 Token 账本、CLI 文本树、Web 会话卡片与 Inspector；
- 运行中 Stop、等待审批、失败/超时/取消/重启中断和断线重连摘要。

### 2.2 v1 不实现

- 任意外部 Multi-Agent 框架的动态安装或运行时切换；
- handoff、群聊、debate、自由路由、递归子 Agent 或 Worker 自主创建 Worker；
- 任意模型提交的 DAG 边、循环、动态重规划或自动重试；
- 分布式 Worker、跨 Gateway 执行、独立后台服务或第二个 MessageBus；
- Worker 写文件、执行命令、调用审批 Tool、主动 Tool 或任意副作用；
- 子 Agent 原始 Prompt、完整 Transcript、逐 token 输出、reasoning 或原始 Tool 参数/返回展示；
- 独立 Run Store、子 Session 目录、durable resume、节点级重试/跳过/替换或 heartbeat；
- 飞书、微信及其他 IM 触发或展示 Multi-Agent。

## 3. Gateway 内部架构

### 3.1 单一运行时边界

一个 `<data_dir>` 仍只有一个 `tga gateway`，拥有：

```text
GatewayHost
  ├─ AgentRuntime / AgentLoop
  ├─ GatewayTurnCoordinator
  ├─ shared AsyncMessageBus
  ├─ ChannelManager（CLI、Web、IM）
  └─ MultiAgentCoordinator（Runtime 内部服务）
```

`MultiAgentCoordinator` 是父 turn 内部的编排器，不是新的 Channel、Host 或 Bus consumer：

- `GatewayTurnCoordinator` 只受理父入站消息并持有普通 Session 的执行槽；
- `AgentLoop` 在父 turn 内部只接收可选的 `multi_agent_invocation`，并把父 Agent 对唯一 `delegate_multi_agent` Tool 的调用交给它；Coordinator 直接创建内存中的 logical node context；
- `DelegateMultiAgentInvocation` 只保存当前父 turn 的桥接状态（是否受理、Run ID、schema 可见性和最终收口标记）；`MultiAgentCoordinator` 是 Run、节点、deadline、事件和错误状态的唯一权威，不把这些状态挂到 `TurnContext`；
- Worker 不重新进入 `AsyncMessageBus`、`ChannelRoute`、`GatewayTurnCoordinator` 或父 Session lock；
- Worker 不占用新的普通会话槽，也不创建普通 `Session`；
- 所有最终出站回复仍由父 route 的 `ChannelManager` 投递。

这样保持一个父 turn 占一个普通 Gateway 槽位，避免 child 重新入站造成死锁、重复路由或槽位放大。

### 3.2 父 Agent 所有权

父 Agent 是一个 `MultiAgentRun` 的唯一 Manager：

- 选择策略、生成 task brief、创建 Worker、接收结果、执行最终 synthesis；
- 负责最终用户答复、普通 Tool、写入/执行副作用和审批；
- 不能在一个父请求中创建第二个 Run；
- synthesis 阶段隐藏 `delegate_multi_agent`，禁止递归委派；
- 子 Agent 永远不能取代父 Agent 成为最终答复所有者。

### 3.3 `MultiAgentRun` 最小状态

运行时维护以下结构化字段（字段名可按现有类型风格调整，但语义不可改变）：

```text
run_id                 稳定 UUID
parent_session_id      父普通 Session ID
parent_request_id      触发该 Run 的原始请求 ID
strategy               fan_out_fan_in | pipeline
status                 queued | running | waiting | completed | failed |
                       timed_out | cancelled | interrupted
nodes                  节点状态、顺序、角色、耗时和结果摘要
created_at/started_at/finished_at
error                  Runtime 脱敏错误摘要或 null
```

Worker 深度固定为 1；其层级由父请求和非空 `node_id` 推导，不传输固定 parent/depth
字段。`off` 是一次请求的客户端模式，不会创建 `MultiAgentRun`；`mode` 不写入 Run。
`enabled=false` 时，所有模式都按单 Agent 能力处理；`auto` 不会强行创建 Run，也不会静默
绕过全局开关。fan-out 的部分完成状态仅由终态 Node 结果按需推导，不是 Run 字段。

状态机固定为：

- Run：`queued -> running -> waiting <-> running -> completed|failed|timed_out|cancelled|interrupted`；
- Node：`queued -> running -> completed|failed|timed_out|cancelled|interrupted`。

Node 没有 `waiting`；等待并发槽位或上游依赖时保持 `queued`，只有父 Agent 等待用户审批
时 Run 才进入 `waiting`。每个 Run/Node 只能写入一次终态，终态后不再产生 late event。

## 4. 启用、模式与入口

### 4.1 服务端配置

Multi-Agent 配置与现有 Settings 一样从项目根目录的 `settings.local.json` 读取，示例同步到 `settings.example.json`，并进入现有 Web `#settings` allowlist、字段校验和 Apply 流程：

```json
{
  "multi_agent": {
    "enabled": false,
    "run_timeout_seconds": 3600,
    "worker_timeout_seconds": 900,
    "max_workers_per_run": 8,
    "max_concurrent_workers_per_run": 4,
    "max_concurrent_workers_global": 8,
    "worker_result_token_limit": 60000,
    "parent_result_token_limit": 120000
  }
}
```

规则：

- `enabled=false` 是全局 kill switch，也是默认值；关闭时不向父 Agent 提供委派能力，`auto` 只执行单 Agent；`enabled=true` 只让工具可用，不会自行创建 Run；
- `run_timeout_seconds` 默认 60 分钟，服务端硬上限 4 小时；
- `worker_timeout_seconds` 默认 15 分钟，必须小于等于 Run deadline；
- 节点、每 Run 并发和 Gateway 全局并发分别默认 8、4、8；不能由单次请求提高；
- `worker_result_token_limit` 是单 Worker `content` 上限，默认 60,000 tokens；
- `parent_result_token_limit` 是父 synthesis 可接收的 Worker 结果总预算，默认 120,000 tokens；
- 两个结果预算都必须与 `runtime.max_context_tokens` 及当前父上下文共同校验；
- `max_concurrent_workers_per_run <= max_workers_per_run`，所有字段必须为正值；
- 具体硬上限由 `config/validate.py` 统一定义，CLI、Web Apply、Runtime 重载和测试共用该规则。

配置 Apply 遵守 Phase 6/7 的 `RuntimeSupervisor` 契约：先写 desired revision，当前普通 turn 和 Multi-AgentRun 继续使用启动时快照；全 Gateway 空闲后才原子替换 Runtime。配置不能中途改变当前 Run 的 deadline、Tool Profile 或并发。

### 4.2 两态单次模式

每条 CLI/Web 普通用户消息拥有一次性模式，默认 `auto`，不写入 Session，也不跨回合持久化：

| 模式 | 行为 |
| --- | --- |
| `auto` | `delegate_multi_agent` 可用；父 Agent 自行判断是否调用，简单任务保持单 Agent。 |
| `off` | 不提供委派能力；父 Agent 保持单 Agent。 |

`auto` 不保证一定创建 Run 或 Worker。不可合理拆分时保持单 Agent；模型真正调用工具后，
`fan_out_fan_in` 可使用一个或多个 Worker 任务，`pipeline` 至少需要两个顺序任务。

客户端伪造或省略 `multi_agent_mode` 以外的值时，Gateway 在派发前返回动作错误；不把无效值
当作 `auto`。

### 4.3 CLI、Web 与 IM

CLI 是认证 `/ws/cli` Client：

```text
/multi auto       # 下一条消息使用 auto
/multi off        # 下一条消息禁用 Multi-Agent 工具
/stop             # 停止整个当前 Run
```

`/multi auto|off` 由 CLI Client 暂存一次，发送下一条普通消息后立即清除。

Web 在现有 Composer 控件区提供 `Auto / Off` 两态选择器。发送时给现有 `/ws/web` `message.send` 增加字段：

```json
{
  "type": "message.send",
  "content": "分析这个项目",
  "multi_agent_mode": "auto"
}
```

发送后选择器恢复 `Auto`；Run 活跃时 Composer 和模式选择锁定，沿用现有 Stop。`enabled=false` 时选择器禁用并显示明确原因。

飞书、微信和未来 IM Adapter 固定 `off`：不注册委派能力，拒绝 `/multi*` 控制输入，不启动 Multi-Agent，不展示运行状态或 reasoning，只保留 Phase 8 的最终纯文本回复契约。

## 5. 父 Agent 委派 Tool Schema

`delegate_multi_agent` 是父 Agent 可调用的唯一 Runtime-owned 专用 Tool schema：只在
`enabled=true`、来源为 CLI/Web 且本次模式为 `auto` 的父 AgentLoop 中可见。它由当前父 turn 的
`DelegateMultiAgentInvocation` 暴露，不是通用 capability/plugin 注册机制；它不进入普通 Tool
Catalog、Web Slash Catalog、IM、Worker、审批列表或 `tool_calls.jsonl`。Invocation 接收固定的
`DelegateParentTurn`（父 Session/请求 ID、画像快照、初始上下文 token 和停止回调），但 Run
快照和生命周期状态仍由 Coordinator 保存。

模型只能提交以下字段：

```json
{
  "strategy": "fan_out_fan_in | pipeline",
  "tasks": [
    {
      "role": "简短角色标签",
      "brief": "独立、可验证且自包含的任务说明"
    }
  ]
}
```

Schema 约束：

- 每个 task 只包含 `role` 和 `brief`；Runtime 按 tasks 的固定数组顺序生成稳定的 `worker-1`、`worker-2` 等 `node_id`，模型不能指定节点身份；
- `role` 是短标签，`brief` 是受限任务输入；二者不能覆盖 Worker system prompt；
- 模型不能提交 `depends_on`、任意边、子 Agent、Tool 名称、模型、token、timeout、approval、Artifact 或 Patch 字段；
- `tasks` 必须非空；`fan_out_fan_in` 接受一个或多个任务，`pipeline` 至少两个任务，所有策略都不得超过 `max_workers_per_run`；
- Runtime 按 `strategy` 和任务顺序生成固定拓扑，并校验节点数量、brief 长度和禁止循环；brief/role 的字符上限由 validator 的固定常量定义，不新增用户配置；
- 一条父请求最多一个有效 Run；重复调用、递归调用或非法字段都明确拒绝；
- `delegate_multi_agent` 的受理、计划和节点状态写入 `multi_agent.*` trace，不当作普通副作用 Tool 执行。

`auto` 由父 Agent 正常调用该 Schema，不创建额外计划阶段、LLM 回合或 Tool。模型不调用该工具时保持单 Agent，不能因此失败或自动重规划。

## 6. 两种固定拓扑

模型只选择模板，系统生成实际边；不提供可编辑 DAG 画布、任意依赖、循环或 handoff。

### 6.1 `fan_out_fan_in`

- 一个或多个、最多 `max_workers_per_run` 个 Worker；
- Worker 只收到自己的 brief、USER/SOUL 和固定只读能力；
- Worker 之间完全并行、互不通信；
- 所有终态结果回到父 Agent，由父 Agent做一次 synthesis。

### 6.2 `pipeline`

- 至少两个、最多 `max_workers_per_run` 个 Worker，按 `tasks` 数组顺序执行；
- 第一个 Worker 只收到自己的 brief；
- 后续 Worker 收到前序 Worker 的有界最终 `content` 作为结构化依赖输入；
- 前序失败、超时或取消时，下游不启动并标记为 `cancelled`；
- 父 Agent 只在整个 pipeline 成功后进行正常 synthesis。

## 7. Worker 上下文、权限与模型

### 7.1 逻辑隔离

每个 Worker 是一次新的、无普通历史的 logical session，不是 TGA 普通 `Session`：

- 读取完整只读 `USER.md` 和 `SOUL.md`；
- 这两份画像只由现有 `ProfileMemory` 受控读取并作为固定 system 内容注入，不通过 Worker 的 `read_file` 暴露 `.sessions` 路径；
- 只读取父 Agent 生成的自包含 `brief` 和 `role`；
- 不自动接收父用户原始消息、父聊天 history、working messages、summary、MCP attachment、父已加载 Skill 正文或其他 Worker 内容；
- 可查看 active Runtime 的只读 Tool/Skill/MCP 元数据，并按需加载安全只读 Skill；
- 当前一条 Worker 任务结束即释放逻辑上下文，不创建普通 Session 目录或消息文件。

### 7.2 固定 Worker system prompt

所有 Worker 共用不可覆盖的系统指令，明确：只读执行、禁止副作用、禁止审批、禁止创建子 Agent、只返回受限最终结果、不得展示 reasoning。模型提供的 `role`/`brief` 只是低优先级任务输入，不能改变上述规则或权限。

### 7.3 固定只读 Tool Profile

Worker 工具能力由代码正向标记 `worker_read_only=true` 决定，不由父 Agent、模型或远端描述动态修改。
该标记不是唯一安全边界，Profile 还必须经过代码级路径和网络策略：默认只能读取项目源代码、
设计/规范文档和公开 Skill 原文，拒绝 `<data_dir>`、`.sessions`、`settings.local.json`、
`.env`、Channel 凭据、MCP headers/API keys、`.git` 和其他凭据/会话状态路径。v1 仅在固定
Profile 中额外提供现有内置 `web_search` 与 `web_fetch`；它们继续复用既有 URL 安全预检，阻断
本机、私网和文件 URL，并将外部内容视为不可信数据。Profile 可包含当前代码明确标记的内置
读取、搜索、时间、联网读取和只读 Skill 加载能力；以下能力始终排除：

- `write_file`、`edit_file`、`exec`、`write_stdin` 及任何文件/命令副作用；
- `approval_required` Tool、Skill 草稿/发布、主动 Tool 和 Channel 控制；
- 当前所有动态 MCP 远端 Tool、Resource/Prompt 附件；MCP `readOnlyHint` 等 annotation 不参与放行。

父 Agent 不能扩大或收紧该 Profile。未来加入一个只读 MCP Tool 必须先在 Adapter/策略层显式分类。

### 7.4 模型和工具并发

- Worker 统一复用 active Runtime 当前的 LLM Provider、Model、凭据和重试语义；不支持 per-worker 模型路由或密钥；
- Worker 内 Tool 调用串行，跨 Worker 节点才按 DAG 并行；父 Agent 保持现有并行 Tool 行为；
- Worker 的 Tool 调用使用现有安全预检，但不显示原始 Tool 生命周期或审批交互。
- 对要求工具续轮携带 thinking 字段的 OpenAI-compatible Provider，`reasoning_content` 只在当前
  AgentLoop 内存中随 assistant 工具调用回传给下一次模型请求；它不是 Worker 结果、上下文、
  trace、token 账本、Tool 记录或 CLI/Web 数据，绝不展示或落盘。

## 8. 生命周期、资源与停止

### 8.1 长运行与会话串行

Multi-AgentRun 不受普通单 Agent 120 秒 turn timeout 限制，使用 `multi_agent` 自己的长 deadline；但保持 TGA 简单的会话语义：

- 父请求进入 `waiting`，继续占用一个普通 Gateway Session 槽位；
- 同一父 Session 在 Run 活跃期间不启动新回合；新消息得到明确“当前协作仍在执行”的状态；
- 其他 Session 继续使用剩余 Gateway 槽位；
- Worker 不重新进入普通 turn 调度，不额外占用普通 Session 槽位。

### 8.2 资源上限

每个 Run 使用 `run_timeout_seconds` 和 `worker_timeout_seconds`；每 Run 和 Gateway 共享 worker semaphore 同时限制并发。无无限等待、无限重试或隐式扩容。LLM 客户端已有的有限请求重试可以继续生效，但 Multi-Agent coordinator 不新增重试策略。

### 8.3 状态与失败

运行时唯一写入状态。Worker 返回的 `status` 不具权威性；Worker 只产出有界 `content` 或受控错误，coordinator 根据事件写入状态。

| 场景 | Run 行为 |
| --- | --- |
| fan-out 至少一个 Worker 成功 | 继续 parent synthesis；读模型从终态 Node 推导是否部分完成，最终答复说明失败节点和缺口。 |
| fan-out 全部失败 | `failed`，不再调用 parent synthesis。 |
| pipeline 上游失败、超时或取消 | 下游不启动并标记 `cancelled`，Run 以对应失败终态收口。 |
| 用户 Stop | 级联取消 queued/running 子任务，并解除父 Run 的 approval wait，Run=`cancelled`。 |
| 单 Worker deadline 到期 | 节点=`timed_out`；fan-out 由终态 Node 推导部分完成，pipeline 阻断下游。 |
| Run 总 deadline 到期 | 尚未终态的节点=`timed_out`，Run=`timed_out`；不因单个 Worker 超时自动重试。 |
| parent synthesis 失败 | Run=`failed`；读模型仍可从已终态 Node 推导收集阶段的完成情况。 |
| Gateway 关闭/进程重启 | 已持久化的活跃 Run 启动时标记 `interrupted`，保留已完成结果，不自动重跑。 |
| Tool Schema/上下文预算非法 | Run=`failed`，给出脱敏确定性错误。 |

除用户 Stop、Gateway 重启和 Run 总 deadline 外，拓扑失败统一把 Run 收口为 `failed`：
pipeline 的上游 `failed|timed_out|cancelled` 会阻断并取消下游；fan-out 只要有成功 Worker
就可进入 synthesis。部分完成只由终态 Node 是否同时包含成功和非成功结果推导，与最终
`status` 独立，且不写入 Run 或事件。

### 8.4 Stop 和审批

CLI `/stop`、Web `task.stop` 都停止整个 Run，不提供节点级重试、跳过、替换或局部重启。停止遵守现有安全检查点：不再开始新的 LLM/Tool 调用，在飞的只读 Tool 允许有界收口，不产生 late event。

Parent synthesis 可以调用父 Agent 原有写入/执行 Tool；仍走现有 `security.py`、Tool Permission Hook 和 `ToolExecutor` 二次检查。需要人工决定时，Run 在同一父请求内进入 `waiting`，复用现有 CLI/Web approval；批准后继续，拒绝后以确定性取消/失败收口。不会新建用户回合。

### 8.5 进程重启

v1 不做 durable resume。启动时扫描父 Session 中最近的 Multi-Agent trace；任何未进入终态的 Run 追加 `interrupted` 记录。用户可查看摘要并重新提交任务，但不能隐式重放节点。

## 9. Worker 结果与上下文预算

### 9.1 最小结果契约

Runtime 持久化和展示的单个 Worker 结果形状为：

```json
{
  "status": "completed",
  "content": "子 Agent 的汇总结论、建议和必要的代码片段",
  "error": null
}
```

这是 Worker 对 coordinator 的唯一结果形状，字段不可扩展：`status` 由 Runtime 赋值，允许
`completed|failed|timed_out|cancelled|interrupted`；成功时 `content` 必须为非空文本，非成功时
`content=null` 且 `error` 必须为非空脱敏摘要。Runtime 可在 trace/event 中另存内部
`error_code`，但不加入 Worker 结果对象。`content` 先经过现有敏感信息脱敏和上限校验，不能包含
traceback、凭据、MCP headers、原始 Tool 输出、内部 Prompt 或敏感请求细节；超限或无法脱敏时以
确定性失败收口。

Worker 若达到 `runtime.max_tool_rounds` 且 AgentLoop 只能返回既有的上限降级文本，WorkerRunner
不把该文本伪装成正常结论：它只从本次已成功、已截断的 Tool 记录构造有界证据摘录，并作为
`completed.content` 交给父 Agent synthesis；摘录明确标识预算耗尽和未验证缺口。没有成功 Tool
结果时，Worker 以脱敏的“Worker 已达到工具调用轮数上限”失败收口。该规则不额外调用模型，
不预留最后一轮无工具总结，也不改变普通父 AgentLoop 的工具轮数语义。

### 9.2 结果大小与传递

- 单 Worker `content` 上限为 `worker_result_token_limit=60000`；超限明确失败，不静默截断；
- 父 synthesis 的 Worker 结果总预算为 `parent_result_token_limit=120000`；
- Runtime 还必须计算当前父上下文、系统/Tool Schema 和 Worker 结果的实际总量；超过 `runtime.max_context_tokens` 或 Run 级汇总预算时，Run=`failed`，错误码为 `result_context_exceeded`；
- pipeline 的前序 `content` 由 coordinator 作为有界结构化依赖输入注入；单任务验证仍使用 `fan_out_fan_in` 的普通 brief；不建立 Worker 私聊；
- v1 不定义 Artifact/Patch 对象或引用链；必要代码片段直接位于 `content`，副作用由父 Agent 重新调用既有 Tool 完成。

## 10. 父 Session 持久化与 Token 账本

Multi-AgentRun 归属于父 Agent 的普通 Session，复用现有会话目录、生命周期和清理周期：

Run 摘要在父 trace/read model 中保留 `parent_session_id` 和 `parent_request_id` 作为父会话
关联；它们不是实时 `multi_agent.*` event payload 字段。

- 父用户消息正常写入 `messages.jsonl`；最终父 Agent assistant 回复正常写入；
- 运行中的状态、节点、角色、耗时、用量、最终 `content` 和安全错误作为结构化 Multi-Agent trace 追加到父 Session 的 `turn_traces.jsonl`；不创建独立 Run Store；
- 不写 Worker 的普通消息、summary、working context、reasoning（包括仅用于 Provider 续轮的
  `reasoning_content`）或额外 transcript；不把“正在协作”伪 assistant 消息写入 `messages.jsonl`；
- `delegate_multi_agent`、Worker 原始 Tool 调用不写普通 `tool_calls.jsonl`；Inspector 只读取安全聚合字段；
- Worker 与父 Agent 的 LLM usage 继续写父 Session 的 `true_token_usage.jsonl`，通过 `run_id`、`node_id`、`scope=worker|parent` 区分；原始账本可以保留 Worker 明细行作为审计来源，但 Run 读模型只提供 `usage.worker`（Worker 总 Token）和 `usage.total`（Run 总 Token），不生成 Parent 分组或 `nodes[].usage` 详细聚合；父 Session trace 与原始账本仍保留；
- 重连从父 Session trace 恢复当前/最近 Run 摘要；已完成节点的最终 `content` 可在 CLI/Web Inspector 按需查看；
- 清理复用 `sessions.retention_days`，不另设 Run retention。

Trace 不保存完整 task brief、Worker system prompt、原始 Tool 参数/返回或逐 token 内容；UI 只展示安全的角色/任务标签、状态、指标和最终结果。

## 11. CLI/Web 观测与事件契约

### 11.1 事件通道

沿用现有 `/ws/web`、`/ws/cli` 和 Session snapshot，不新建 Multi-Agent WebSocket。新增 `multi_agent.*` 命名空间事件：

```text
multi_agent.run.started
multi_agent.node.updated
multi_agent.run.completed
multi_agent.run.failed
multi_agent.run.cancelled
```

事件沿用现有 SessionEvent envelope（Web 为 `event_id`、`session_id`、`request_id`、`type`、`payload`、`created_at`）；
CLI 使用现有终端事件 envelope。Coordinator 的内部安全 payload 固定只包含
`run_id`、`node_id|null`、安全 `task_label`、`strategy`、`status`、`duration_ms|null`、
`usage|null`、`error_code|null`、`error|null` 和终态 Worker `content|null`。`node_id=null`
表示 Run 事件，非空 `node_id` 表示 Worker 事件。父 Session trace 保留终态 `content` 供
Inspector 使用；实时 CLI/Web Channel 投影过滤 `content`，不发送中间自然语言、逐 token、
hidden Prompt 或原始 Tool 数据。fan-out 的部分完成由读模型从终态 Node 推导，不写入事件或
trace payload。Web 的
`event_id` 按父 Session 单调递增并作为重连游标；终态事件只发送一次，重复投影按
`(session_id,event_id)` 去重，重连先由 snapshot 恢复摘要。`node.updated` 只在真实阶段或指标变化时发送，
不是 heartbeat。

`run.started` 承载 `queued|running|waiting` 的 Run 阶段变化；`node.updated` 承载所有 Node
阶段和终态。`run.completed` 只表示成功，`run.cancelled` 只表示用户 Stop，超时、进程重启
中断和其他失败统一使用 `run.failed`，真实原因仍保留在 payload 的 `status`/错误字段中。
Gateway 重启时追加 `run.failed(status=interrupted)` 及未完成 Node 的
`node.updated(status=interrupted)`，不向已断开的连接补发历史自然语言。

现有 `session.snapshot` 和 `GET /api/sessions/{session_id}/observability` 增加有界 `multi_agent_runs` 摘要。历史读取不新增专用 REST；实时状态沿现有 WebSocket 增量事件。Stop 与审批继续使用 `task.stop`、`approval.resolve`。

### 11.2 Web 工作面

父会话时间线内显示一个实时 Multi-AgentRun 卡片：策略、总体状态、节点进度、耗时、Token 用量和 Stop。点击卡片复用右侧 Inspector 展示完整的确定性模板图和节点最终 `content`。

- fan-out 使用父→并列 Worker→父的固定布局；
- pipeline 使用顺序连接；
- 不提供拖拽、编辑边、节点重试或 force-layout；
- 深浅主题、背景层级、留白、轻阴影和圆角遵循 `DESIGN.md`/Phase 6 规则，不展示 reasoning；
- Run 活跃时 Composer 编辑、模式选择和普通新消息锁定；审批与 Stop 仍可用。

### 11.3 CLI 工作面

实时事件用现有 terminal renderer 输出缩进文本树，显示 Run/节点状态、策略、耗时、Token 用量和失败摘要。不引入 curses/TUI 或新依赖。`/multi auto|off` 只设置下一条消息模式。

## 12. 实施边界与分层计划

按 AGENTS.md 的渐进式原则，先完成可端到端验收的最小垂直切片，再扩展体验：

1. **契约与配置层**：`multi_agent` Settings/validator、模式字段、固定 Schema、Run/Node/Result 类型和只读 Profile；
2. **Runtime 层**：父 turn 内部 coordinator、两种固定拓扑、资源 semaphore、状态/Stop/审批/重启收口；禁止复用 Gateway `AsyncMessageBus` 作为内部队列；
3. **持久化层**：将结构化 Run 记录追加到现有 `turn_traces.jsonl`，将节点用量追加到现有 `true_token_usage.jsonl`，不创建子 Session 文件；
4. **Gateway/CLI/Web 层**：现有通道的 `multi_agent.*` 事件、snapshot、CLI 一次性模式、Web Composer 选择器、卡片和 Inspector；
5. **验证层**：先用确定性 fake LLM/Worker 测试生命周期与事件，再用真实页面验证两种布局、双主题、Stop、重连和审批。

模块职责必须保持清晰：coordinator 只负责计划/调度/状态，Worker 只负责只读 AgentLoop，Runtime 负责父状态机和安全边界，Session Store 负责现有文件落盘，Gateway/Web/CLI 只负责事件投影。

## 13. 验收标准

### 13.1 配置与安全

- `multi_agent.enabled` 默认关闭；CLI/Web `auto/off` 模式和 Web Settings Apply 经过字段级校验；
- `auto` 只让模型自行判断是否委派，不保证创建 Run；`off` 不提供委派能力，IM 永远不触发 Multi-Agent；
- Worker 只能看到固定 `worker_read_only` Profile，其中包括内置 `web_search` 与 `web_fetch`；
  写入、执行、审批、主动和动态 MCP Tool 均不可见；
- 现有安全预检、ToolExecutor 二次检查和审批链保持有效，委派能力不绕过安全。

### 13.2 Runtime 与状态

- 单 Gateway/单 Runtime/单 Bus 不变；Worker 不重新入站、不创建普通 Session、不获取父 Session lock；
- auto、off 和非法模式行为符合本文契约；模型未调用 `auto` 委派能力时保持单 Agent 成功，每父请求最多一个 Run；
- 两种固定拓扑的节点顺序、依赖、由 Node 状态推导的部分完成、失败、超时、取消、停止和 `interrupted` 均有确定性测试；
- Run/Worker deadline、每 Run/全局并发和结果上下文预算可验证；无无限重试、无自动降级、无静默截断。
- Worker 工具预算耗尽时，有成功 Tool 结果仅以有界证据摘录交给父 synthesis；无成功结果确定性失败；
  不增加模型总结调用，Provider thinking 续轮字段不进入任何持久化或展示面。

### 13.3 持久化与观测

- `messages.jsonl` 只有父用户消息和最终父回复；Run 状态只进入父 `turn_traces.jsonl`，usage 只进入父 `true_token_usage.jsonl`；
- CLI/Web 共享同一事件字段，重连 snapshot 可恢复当前/最近 Run；不展示 Prompt、Transcript、reasoning 或原始 Tool 数据；
- CLI `/multi auto|off`、`/stop` 和 Web Composer/卡片/Inspector/Stop/审批均可用；两种固定拓扑布局确定且不引入第二通道。

### 13.4 工程验证

- Python 单元/集成测试覆盖配置、Schema、上下文隔离、只读工具、DAG、资源、停止、重启中断、trace、Run/Worker token 汇总和 Gateway 单实例；
- CLI 冒烟覆盖 `/multi auto|off`、实时文本树和 Stop；
- Playwright/真实页面覆盖 Auto/Off、两种布局、节点结果、由 Node 状态推导的部分完成/failed、重连 snapshot、审批、Stop、深浅主题和长结果滚动；
- `compileall`、`pnpm run build`、默认 Playwright、真实页面测试、`git diff --check` 和现有 Python 回归保持通过。

## 14. 实现状态与验收记录

截至 2026-08-12，Phase 9 的 Settings、固定 Worker Profile、两种固定拓扑、父 Session trace/usage、
CLI/Web 既有通道事件、Composer 两态选择器、时间线卡片和 Inspector 已实现于当前工作树。父
Agent 只通过 `DelegateMultiAgentInvocation` 调用 `delegate_multi_agent`，`AgentLoop` 不再保留
通用 special capability 参数或注册表；实现继续复用单 Gateway、单 Runtime、单 AgentLoop 和
共享 AsyncMessageBus；IM 保持永久关闭。

当前工作树尚未暂存、提交或推送。以下为 2026-08-11 在同一工作树实际执行的验收记录：

- `python -m compileall Turning-Good-Agent -q`：通过；
- `pytest -q --basetemp=C:\\tga9pytest-final-20260811`：`479 passed, 19 skipped, 1 warning`；
- `pnpm run build`：通过；
- 默认 Playwright：`70 passed, 12 skipped`；
- `TGA_REAL_PAGE=1` 真实页面：`11 passed, 1 skipped`；
- `pnpm exec playwright test tests/phase9_visual.spec.ts`：`2 passed`；
- 两态收口聚焦回归：Python `91 passed, 1 warning`；`pnpm exec vitest run tests/multi_agent_view.spec.ts`：`6 passed`；相关 Playwright `34 passed`；`pnpm run build`：通过；
- `git diff --check`：无空白错误；现有 LF/CRLF 提示不构成 diff 错误。

前端命令使用 Codex bundled Node/Pnpm 运行，因为系统 Node 不在 `PATH`。上述结果是当前工作树
的验收证据；功能实现与验证已完成，后续只等待仓库负责人按范围确认后统一暂存和提交。

独立复审还记录了四项暂缓的 lifecycle/persistence 边界：终态 trace 持久化失败时失败状态尚未可靠传播到父
响应；竞争中的审批取消可能在重新获取 Gateway 槽位时延迟收口；不响应取消的 Worker 可能在并发槽释放后继续
运行并重建临时缓存；长 Worker `content` 的存储会规范化空白而不能保留原始排版。按仓库负责人决定，本轮不
扩展或修复这四项，也不将它们表述为已经通过验收；后续必须先补足端到端覆盖并完成修复，才能宣称完整验收。

最终交付前需进行范围审查并等待仓库负责人明确确认；不得自行暂存、提交或推送。该动作不改变本契约
中关于模型自主选择、Worker 逻辑隔离、父 Session 持久化、CLI/Web 可观测和 IM 永久关闭的边界。
