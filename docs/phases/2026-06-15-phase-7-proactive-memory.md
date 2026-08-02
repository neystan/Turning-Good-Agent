# Phase 7：主动能力与长期记忆

> 状态：已实现，并在 2026-08-02 合并 Phase 7.1 优化结果
> 本文是 Phase 7 的唯一规范来源；实际代码是最终行为依据。

## 1. 目标

Phase 7 为 Agent 增加四类部署级主动能力：

- Cron：在指定时间执行后台任务；
- Breakbeat：从会话中维护未完成事项；
- Dream：从会话中提炼并写入长期画像；
- Skill 自进化与异常提醒：沉淀可复用流程，并以规则追踪后台异常。

Phase 7.1 不继续扩张抽象层，而是收敛 Phase 7 的数据模型、时间语义、后台安全边界、持久化与 CLI 交付行为。它不新增 Web 后台中心、IM 主动投递、画像合并模型或通用任务队列。

## 2. 核心原则

1. 主动数据属于当前部署或工作区，不属于某个聊天会话。
2. 后台模型只看到允许后台执行的 Tool；执行层仍进行第二次拒绝检查。
3. 创建任务、显示时间和触发任务使用同一个 `proactive.timezone`。
4. 持久化只保存恢复业务所需的最小状态，运行状态优先由内存中的真实执行情况推导。
5. 用户当前消息只在手动 Dream/Breakbeat 审阅期间临时补入，不提前写入 `messages.jsonl`。
6. Cron 与 Breakbeat 删除均为硬删除，不保留 tombstone。
7. 主动输出先可靠持久化，再经已有消息总线投递；不为 `OutboundMessage` 增加新字段。
8. 旧格式不做静默猜测或自动迁移。检测到不兼容数据时明确报错，要求用户备份并清理对应主动数据。

## 3. 运行架构

`ProactiveService` 由 `tga chat` 启动和停止，负责装配与调度，不承载各领域的具体业务逻辑。

主要职责分工如下：

- `proactive/service.py`：生命周期、全局调度、周期触发和组件装配；
- `proactive/scheduler.py`：持久化下一运行时间与可唤醒 deadline 调度；
- `proactive/cron.py`：Cron Job、触发计算、运行与 Tool；
- `proactive/breakbeat.py`：未完成事项审阅、状态更新与 Tool；
- `proactive/dream.py`：单阶段长期记忆提炼与 Tool；
- `proactive/skill_evolution.py`：Observation 与 Skill Draft；
- `proactive/incidents.py`：规则化异常状态与查询；
- `proactive/delivery.py`：主动结果的可靠投递；
- `proactive/executor.py`：后台 Agent 执行和 Tool 安全边界；
- `proactive/store.py`：部署级主动数据的原子 JSON/JSONL 存储；
- `proactive/types.py`：主动能力的结构化记录；
- `proactive/review_window.py`：有界会话读取、游标与批次切分。

`ProactiveStore`、`ProactiveExecutor` 与 `ProactiveService` 保持分离：它们分别对应持久化、安全执行和调度装配，不能因文件数量而合并职责。会话存储与主动数据生命周期不同，因此主动数据不并入 `sessions/store.py`。

## 4. 持久化布局

默认 `settings.data_dir` 为项目根目录下的 `.sessions`。主动数据与长期画像使用固定的部署级目录：

```text
<data_dir>/
├── memory/
│   ├── USER.md
│   └── SOUL.md
└── proactive/
    ├── cron_jobs.json
    ├── cron_audit.jsonl
    ├── breakbeat_items.jsonl
    ├── breakbeat_state.json
    ├── dream_state.json
    ├── observations.jsonl
    ├── skill_evolution_state.json
    ├── incidents.jsonl
    ├── deliveries.jsonl
    └── executions.jsonl
```

Skill Draft 仍写入现有 `.skills/.drafts/`。

`.sessions/` 被 Git 忽略，以避免 USER/SOUL 画像和运行数据进入版本控制。创建新会话或清理会话时，只能删除能够严格解析出有效 `session.json` 的会话目录；不得删除 `proactive/`、`memory/`、未知目录或损坏目录。

主动存储中的关键 JSON 文件采用临时文件替换，成组写入在失败时回滚。Breakbeat 批次和 USER/SOUL 双文件不得出现部分落盘。

### 4.1 旧数据处理

以下内容属于已废弃格式：

- Cron 中的 `status`、`last_triggered_at`、`last_completed_at`、`last_error`；
- 用 `recurring=false` 与 Cron 表达式模拟的一次性任务；
- Breakbeat 的 `title`、`next_action`、`due_hint`、`source_message_ids`、`open`、`closed`、`deleted`；
- `dream_evidence.jsonl`、`dream_memories.jsonl`、`dream_revisions.jsonl`。

发现旧格式时，启动或读取必须返回包含实际文件路径的清晰错误，且不得修改文件。用户应先备份，再清理 `<data_dir>/proactive/` 中对应旧数据。长期画像文件不因此删除。

## 5. 配置与时间

`proactive` 配置保留以下字段：

```json
{
  "proactive": {
    "enabled": true,
    "timezone": "Asia/Shanghai",
    "review_provider": null,
    "review_api_key": null,
    "review_base_url": null,
    "review_model": null,
    "background_max_concurrency": 4,
    "breakbeat_refresh_minutes": 60,
    "dream_refresh_hours": 24,
    "review_window_token_limit": 100000,
    "profile_total_token_limit": 16000,
    "user_profile_token_limit": 12000,
    "soul_profile_token_limit": 4000,
    "skill_observation_turn_interval": 10,
    "skill_observation_token_limit": 160,
    "skill_evolution_batch_token_limit": 100000,
    "skill_evolution_batches_per_kind": 3,
    "skill_evolution_daily_draft_limit": 10
  }
}
```

`timezone` 接受运行环境可用的 IANA 时区名称，例如 `Asia/Shanghai`、`UTC`、`Europe/London`、`America/New_York`，默认 `Asia/Shanghai`。配置解析必须严格校验 boolean；字符串 `"false"` 不能被当作 `true`。

审阅模型的 `review_provider`、`review_api_key`、`review_base_url`、`review_model` 必须同时为空或同时填写。全部为空时复用主 LLM；全部填写时创建独立审阅 AgentLoop，但继续复用主 Runtime 的工具基础设施和 Hook，不创建第二套 Runtime。

`background_max_concurrency` 是内存并发上限。超限任务在 semaphore 前等待，等待状态为 `queued`，取得执行名额后才是 `running`；它不是持久化任务队列。

`NowTool` 使用 `proactive.timezone`，返回包含 UTC offset 的 ISO 本地时间及时区信息。Cron 的创建、列表含义、一次性 `run_at` 解析、下一次触发计算和实际触发全部使用同一时区。运行期间修改全局时区后，所有尚未触发的 Cron deadline 立即重算，并唤醒调度器。

## 6. 后台执行安全

后台执行采用双重防御：

1. 生成后台 LLM Tool schema 时，隐藏所有需要审批的 Tool、主动控制 Tool，以及配置显式排除的 Tool；
2. `ToolCallRunner` 执行前再次拒绝这些 Tool，防止旧 schema、模型伪造调用或错误配置绕过隐藏层。

Tool 的 `approval_required=True` 和全局审批配置都参与排除计算。后台 Channel 不等待用户输入，任何审批请求都直接拒绝。`allow_tools=false` 时传入空 Tool schema，同时执行层拒绝全部 Tool。

主动控制 Tool 本身不能被后台 Agent 递归调用，例如后台 Cron 不得创建 Cron、运行 Dream 或触发 Skill Evolution。

`executions.jsonl` 只保存能力、结果状态、时间、token 用量、调用计数和安全错误摘要，不保存完整提示词、消息正文、Tool 参数、API Key 或其他秘密。

MCP 连接在取消期间必须关闭已创建的客户端，避免后台任务取消导致连接泄漏。

## 7. 主动消息投递

Phase 7.1 不修改 `OutboundMessage` 结构。后台消息通过现有 `event_type` 的 `proactive.*` 前缀识别，持久化时使用全局 `session_id="proactive"`。

投递流程：

1. 后台能力生成结果；
2. 先写入 `deliveries.jsonl`，状态为 `pending`；
3. 仅在前台空闲且 outbound 队列为空时尝试投递；
4. CLI 在真正 flush 时查询最新 `active_session_id`，将消息显示到当前运行会话；
5. 投递成功后追加完成记录。

CLI 展示的后台消息不写入该会话的 `messages.jsonl`，因此不会污染聊天历史。没有 CLI 活跃会话回调时，消息继续保持全局 `proactive` 目标，为后续 Web/IM 消费者保留接口。

Web 后台中心与 IM 主动推送不在本阶段实现；Web 也不把全局后台结果强行投递到某条聊天会话。

## 8. Cron

### 8.1 数据模型

CronJob 只持久化：

```text
id
prompt
recurring
cron
run_at
delivery_channels
created_at
updated_at
```

- 周期任务：`recurring=true`，必须有五字段 `cron`，不得有 `run_at`；
- 一次性任务：`recurring=false`，必须有 `run_at`，不得有 `cron`；
- `run_at` 是 `proactive.timezone` 下不含 offset 的 ISO 本地时间；
- 相对时间由 `delay_seconds` 在 Tool 层转换为 `run_at`；
- `updated_at` 记录 Job 本体最近修改时间，不承担运行状态语义。

CronJob 不持久化 `status`。列表中的 `active`、`queued`、`running` 根据任务是否存在、是否等待并发名额、是否正在执行动态推导。每次运行的 `queued`、`running`、`completed`、`failed`、`cancelled`、`skipped_overlap` 写入 `cron_audit.jsonl`。

### 8.2 调度

调度器不为每个 Cron 建立独立监听器，也不每秒遍历全部 Job。CronManager 在内存维护每个任务的下一 deadline，Service 等待最早 deadline 或 schedule-changed event：

- 创建、删除或修改时区会唤醒等待；
- 同一 Job 尚在执行时不重入，记录 `skipped_overlap`；
- 进程启动后从当前时间计算下一次，不补跑停机期间错过的周期；
- 一次性任务成功、失败或取消后都从 Job 存储移除；
- 关闭服务时取消执行任务，清理 task bookkeeping，不遗留虚假 `running`。

### 8.3 删除

`delete_cron` 是完整硬删除：取消正在运行或排队的该 Job，删除 `cron_jobs.json` 中的 Job，并删除其 audit、incident 和 delivery 相关记录。删除后 `list_crons` 和底层文件中都不得再出现该 ID。

## 9. Breakbeat

BreakbeatItem 只包含：

```text
id
todo
deadline
source_session_id
status
created_at
updated_at
```

`deadline` 可为空。`status` 只有 `in_progress` 和 `completed`。`source_session_id` 由系统填写，LLM 不负责猜测会话 ID，也不需要填写消息 ID。

Breakbeat 审阅模型返回 `create`、`update`、`complete` 或 `no_change` 动作。一个批次的动作先在内存完整校验，再一次性持久化；失败时既不部分写入，也不推进 review cursor，重试不得产生重复事项。

完成事项有两个确定入口：

- 审阅到用户明确表达“已完成”时执行 `complete`；
- 用户通过 `complete_breakbeat` 指定唯一 ID 完成。

不依据用户沉默或模糊推断自动完成。`delete_breakbeat` 为硬删除，并移除该事项相关历史。

自动 Breakbeat 按 `breakbeat_refresh_minutes` 执行。手动成功后将下一次运行时间重排为 `finished_at + interval` 并持久化；手动失败不改变原 deadline；同一时间只允许一个 Breakbeat 运行。

## 10. Dream 与长期画像

Dream 是单阶段、追加式长期记忆提炼，不再使用 Evidence、Memory Registry、Revision 或第二段审阅。

审阅模型只返回：

```json
{
  "memories": [
    {"target": "user", "content": "稳定的用户偏好或事实"},
    {"target": "soul", "content": "稳定的助手行为原则"}
  ]
}
```

`target` 只能是 `user` 或 `soul`。Dream 忽略临时任务、闲聊、未经证实的推断和秘密；出现多余协议字段或疑似秘密时拒绝该输出。已存在的完全相同记忆视为无变化。

USER/SOUL 作为一对事务性写入：任一文件写入失败时恢复二者原值，不能留下半更新画像。`run_dream` 直接返回本轮实际追加的记忆，`read_profile_memory` 读取当前 USER.md 与 SOUL.md，因此无需 `list_dream`。

长期画像在每个前台回合完整注入系统上下文，限制为：

- USER：最多 12,000 tokens；
- SOUL：最多 4,000 tokens；
- 合计：最多 16,000 tokens。

超过限制时拒绝写入，不静默截断。自动 Dream 按 `dream_refresh_hours` 执行；成功手动运行后将下一次重排为 `finished_at + interval`，失败不改变原 deadline，并禁止重入。

本阶段不实现画像合并、替换或专用维护 Dream。

## 11. 审阅窗口、scope 与当前消息

Dream 与 Breakbeat 的 `run_*` Tool 必须显式接收 `scope`：

- `global`：后台周期或用户明确要求全局审阅时，扫描可审阅的全部会话；
- `session`：用户明确要求只处理当前会话时，仅审阅当前活动会话。

Agent 回合中的当前用户消息通常要到 SAVE 阶段才进入 `messages.jsonl`。为让“请在当前会话运行 Dream/Breakbeat，并处理这条消息”立即生效，运行 Tool 时将当前 inbound `MessageRecord` 临时补到审阅输入：

- 不提前持久化；
- 只补当前活动会话的当前消息；
- 与已持久化消息按时间和 ID 去重、排序；
- 回合正常完成后仍由原 SAVE 流程持久化一次；
- Tool 失败也不会在会话文件中留下半条消息。

审阅窗口只读取 `user` 和 `assistant` 消息，并固定本轮开始时的快照上界，避免审阅运行期间新增消息造成游标漂移。消息按 token 上限切分为批次，不拆分单条消息；单条消息超过批次上限时明确失败。批次成功后才推进游标。

首次运行或游标过旧时，从当前 refresh window 内最早的可见消息开始；窗口外的更旧缺口不回填，以控制手动运行时延和模型成本。

## 12. Skill 自进化

每成功保存 10 个完整 user/assistant 回合，执行一轮 Observation；取消或未完成回合不计数。明确的学习信号可以提前触发。

Observation 字段为：

```text
id
created_at
kind
observation
source_session_id
source_message_ids
```

`source_session_id` 由系统填写；`kind`、`observation` 和 `source_message_ids` 由审阅模型从本轮原文中产生。`kind` 只允许：

- `workflow`
- `tool_procedure`
- `failure_recovery`
- `interaction_protocol`

Observation 批次完整校验并原子持久化后才推进 signal cursor。Skill Draft 生成按 kind 聚合候选 Observation，必须回读并找到全部引用的原始消息；任何消息缺失都不得沉淀 Draft。

Draft 使用主模型生成到 `.skills/.drafts/<name>/SKILL.md`，受每日数量限制，不自动发布。用户可以运行、列出或删除 Draft；发布继续使用既有审批流程。

## 13. 异常提醒

异常提醒完全由规则实现，不引入 LLM。后台执行从成功转为失败时创建或更新 `open` Incident；同一 fingerprint 重复失败只增加次数和更新时间，不重复创建。后续恢复成功时写入 `resolved` 状态和 `resolved_at`。

`list_incidents` 支持列出全部事件，或按 `open` / `resolved` 过滤。读取 JSONL 时按 Incident ID 合并为最新状态。

## 14. Tool 契约

| Tool | 作用 | 关键约束 |
| --- | --- | --- |
| `now` | 获取当前时间 | 使用 `proactive.timezone`，返回 offset 与时区 |
| `create_cron` | 创建周期或一次性任务 | `cron`、`run_at`、`delay_seconds` 按模式互斥 |
| `list_crons` | 列出 Cron | 状态由运行时推导 |
| `delete_cron` | 删除 Cron | 完整硬删除 |
| `run_breakbeat` | 立即审阅事项 | 用户明确要求；`scope` 必填 |
| `list_breakbeat` | 查看事项 | 展示当前持久化状态 |
| `complete_breakbeat` | 完成事项 | 必须指定唯一 ID |
| `delete_breakbeat` | 删除事项 | 完整硬删除 |
| `run_dream` | 立即提炼长期记忆 | 用户明确要求；`scope` 必填；返回实际新增内容 |
| `read_profile_memory` | 查看长期画像 | 直接读取 USER.md 与 SOUL.md |
| `run_skill_evolution` | 立即执行 Skill 演进 | 不自动发布 Draft |
| `list_skill_drafts` | 查看 Draft | 读取草稿目录 |
| `delete_skill_draft` | 删除 Draft | 需要审批 |
| `list_incidents` | 查看异常 | 可按 open/resolved 过滤 |

后台 Agent 看不到上述主动控制 Tool，避免递归触发。

## 15. CLI 与 Channel

CLI 仅保留一个 `CliChannelAdapter`。它同时负责：

- 将输入输出接入 `AsyncMessageBus`；
- 使用共享 `PromptSession` 显示 Tool 审批问题；
- 没有 PromptSession 时回退到线程中的 `input`。

`ToolPermissionHook` 仍是多 Channel 的统一审批策略入口，决定是否需要审批；Channel Adapter 只负责该 Channel 如何与用户交互。`cli.py` 不再自行实现一套重复审批函数。

`cli.py` 同时包含 `chat` 与 `web` 子命令入口不代表 CLI 聊天混入 Web Channel。Web 使用自己的 Adapter；本阶段不要求把命令入口继续拆文件。

## 16. 验收标准

### 16.1 时间与 Cron

- `now`、Cron 创建与触发在任意系统本地时区下含义一致；
- 一次性任务只保存 `run_at`，执行后移除；
- 周期任务使用可唤醒的下一 deadline，不进行每秒全表扫描；
- 热修改时区后未触发任务立即重算；
- 取消、重叠和并发等待的状态均准确；
- 删除后 Job 及全部相关记录消失。

### 16.2 Dream 与 Breakbeat

- 新 CLI 会话能继续读取之前的 Cron、事项与 USER/SOUL；
- 手动 session scope 能看见尚未 SAVE 的当前用户消息，但消息只持久化一次；
- global/session scope 不串会话；
- 手动成功后按完成时间延后下一轮，失败不延后；
- Breakbeat 批次失败不部分写入、不推进游标；
- Dream 双文件写入失败能完整回滚；
- 用户能直接查看本轮 Dream 新增内容和当前画像。

### 16.3 安全、投递与演进

- 后台 schema 不包含审批 Tool，伪造调用也被执行层拒绝；
- 日志和执行审计不保存提示正文、Tool 参数或秘密；
- 主动消息先持久化，CLI 空闲后投递到最新活动会话且不污染历史；
- Observation 引用不完整时不能生成 Draft；
- Incident 无需 LLM 即可列出、去重和恢复；
- MCP 连接在取消路径被关闭。

### 16.4 工程验证

提交前必须至少完成：

- Phase 7 聚焦测试；
- 全量测试；
- `compileall`；
- `git diff --check`；
- CLI 启动并 `/exit` 的烟雾测试。

## 17. 非目标

以下内容留待后续阶段：

- Web 全局后台中心界面；
- IM 当前会话主动推送；
- 修改 `OutboundMessage` 的 origin/scope 字段；
- 通用 durable background queue；
- Breakbeat `expired` 状态和截止时间提醒；
- Dream 合并、替换及画像维护模型；
- 自动发布 Skill Draft；
- 多用户或跨工作区的全局记忆 tenancy。

## 18. Phase 7.1 合并说明

本规范已吸收 Phase 7.1 优化计划、运行时修复设计、执行计划与 review handoff 中的最终决定。合并后这些中间文档不再作为独立事实来源；任何旧描述与本文或实际代码冲突时，以实际代码和本文最终契约为准。
