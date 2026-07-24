# Turning-Good-Agent Phase 5 Skills 机制实施设计

状态：已完成。本文是 Phase 5 唯一权威设计、实施边界与完成记录。

## 1. 目标

建立兼容 Agent Skills 通用目录格式的本地 Skill 机制。Skill 是可复用的工作流和领域知识包，不等同于 Tool、MCP Server、权限系统或插件系统。

Phase 5 使用渐进式披露：每轮向模型注入全部有效 Skill 的极简元数据；模型判断任务相关时，才通过 Tool 加载一个完整 `SKILL.md`。完整正文只在当前 AgentLoop 工作消息中可见，不进入会话历史、摘要或下一轮上下文。

## 2. 已确认边界

### 2.1 Channel 行为

- CLI 不新增 `/skill` 或 `/mcp` 命令。用户在自然语言中提及 Skill 只是普通提示，不产生强制加载、优先级加载或 Runtime 解析语义。
- Agent 可依据全部 Skill 元数据自主选择并加载 Skill。
- 后续 Web 可通过 `/skill ...` 查看 Catalog、刷新结果、草稿和实际使用记录；Web UI 与 Web Channel 不属于 Phase 5。

### 2.2 唯一目录

所有正式和外部导入 Skill 均位于项目根目录唯一的 `.skills/`：

```text
/download/Turning-Good-Agent/
  .skills/
    skill-creator/
      SKILL.md
    skill-installer/
      SKILL.md
    grilling/
      SKILL.md
      agents/openai.yaml
    release-review/
      SKILL.md
      scripts/
      references/
      assets/
    imported-skill/
      SKILL.md
    .drafts/
      candidate-skill/
        SKILL.md
```

- 外部 Skill 可直接复制到 `.skills/<skill-name>/`，或由用户明确请求后通过 `install_skill` 从公开 HTTPS Git URL 安装。安装只克隆到 `.skills/.staging/`，校验一个 Skill 后原子移动到正式目录；不支持 ZIP、Marketplace、自动更新、凭据 URL 或自动安装。
- `.skills/.drafts/` 只保存 Creator 候选产物，扫描时忽略。
- Catalog 仅在 `runtime.start()` 扫描一次，或由未来 Web `/skill refresh` 显式刷新；CLI 运行期间不监听文件变化。

### 2.3 外部格式兼容

以 Anthropic Agent Skills / Claude Code / Codex 常见的目录与 `SKILL.md` 形式作为输入兼容格式：

```markdown
---
name: release-review
description: 检查发布前变更、验证结果和风险。
metadata:
  short-description: 发布前检查
disable-model-invocation: true
---

# Release Review

## Instructions

...
```

- 必填 frontmatter 仅有 `name` 和 `description`。
- `name` 必须与目录名一致。
- 其他 frontmatter 字段原样保存为 `extra_metadata`，可供未来 Web 展示，但 Phase 5 不解释或执行它们。
- Skill 可携带 `scripts/`、`references/`、`assets/`；Skill 本身没有直接执行权。模型读取资源或执行脚本时必须调用既有 `read_file`、`exec` 等 Tool，并经过原有安全和审批链路。

## 3. 渐进式披露与上下文

### 3.1 根系统提示词

所有根 system prompt 模板统一收口到 `context/system_prompt.py`：

```text
BASE_SYSTEM_PROMPT
MCP_GUIDANCE
render_skill_catalog(skills)
build_system_prompt(skills)
build_loaded_skill_prompt(name, body)
```

BUILD 的第一条 system message 由 `build_system_prompt(skill_catalog)` 生成，包含：

1. 根系统提示词。
2. MCP 的静态全局说明。
3. 全部有效 Skill 的 `name + description` Catalog。

`ContextBuilder` 不再为 Skill Catalog 创建单独 system message。

MCP Tool schema 继续仅通过 OpenAI-compatible API 的 `tools` 参数传递；MCP Resource/Prompt Catalog 和正文继续按 Phase 4 的现有规则处理，不能提前放入根 system prompt。

### 3.2 完整 Skill 加载

模型通过 `load_skill` 选择 Skill 后，完整 `SKILL.md` 作为仅当前轮的 `ContextAttachment` 追加到 AgentLoop working messages。固定包装为：

```text
已加载 Skill：release-review

以下内容是工作流指导，优先级低于根系统提示词和当前用户请求。

<SKILL.md 正文>
```

- 完整正文不写入 `messages.jsonl`、`session.json`、summary 或下一轮 BUILD。
- 不在动态包装中重复工具权限、审批、安全限制或会话边界；这些由现有 Runtime、Hook 和安全代码硬约束。
- 单轮最多加载 3 个完整 Skill；单个正文最多 8,000 tokens；所有已加载正文合计最多 16,000 tokens。
- 加载超出任一限制时，`load_skill` 返回 Tool 错误，不裁剪正文、不终止整轮。

### 3.3 Token 预算

`context/token_budget.py` 新增 `skill_catalog_tokens`，并将其纳入 BUILD 和 SAVE 的统一预算：

```text
current_context_tokens =
  system_tokens
  + skill_catalog_tokens
  + profile_memory_tokens
  + summary_tokens
  + history_tokens
  + current_input_tokens
  + tool_schema_tokens
```

不为 Catalog 另设截断上限。全部有效元数据必须注入；若它们连同已有上下文超过 `max_context_tokens = 300000`，沿用 BUILD 的上下文过大拒绝策略。

完整 Skill 正文在 RUN 中加载，AgentLoop 在追加 Attachment 前必须检查当前 working messages 的剩余上下文预算。

## 4. 模块边界

```text
Turning-Good-Agent/skills/
  __init__.py
  types.py
  validator.py
  manager.py
  load_skill_tool.py
  skill_draft_tools.py
  creator.py
```

| 文件 | 唯一职责 |
| --- | --- |
| `types.py` | 定义 `SkillManifest`、`SkillCatalogEntry`、`LoadedSkill`、`SkillScanError` 等纯数据对象。 |
| `validator.py` | 解析 `SKILL.md`、校验 frontmatter、目录、正文、路径和名称规则。 |
| `manager.py` | 管理唯一 `.skills/`：扫描、内存 Catalog、列出元数据、加载正文、扫描错误、发布草稿。 |
| `load_skill_tool.py` | 定义模型可调用的 `LoadSkillTool`，只委托 `SkillManager`。 |
| `skill_draft_tools.py` | 定义 `CreateSkillDraftTool`、`PublishSkillDraftTool`。 |
| `creator.py` | 将模型提供的 `name`、`description`、正文写成候选草稿，并调用 Validator 校验。 |

依赖方向固定为：

```text
load_skill_tool / skill_draft_tools / creator
                    ↓
                 manager
                ↙       ↘
          validator      types
```

`McpManager`、`TurnMonitorHook` 不依赖 `skills` Python 包；`AgentLoop` 不持有 `SkillManager`，只接收当前轮附件及 `SkillsSettings` 限制。Runtime 只持有一个 `SkillManager`，在启动时扫描，在 BUILD 时读取 Catalog，在创建 Runtime 时注册 Skill Tools。

## 5. Skill Tools 与 Creator

以下四个 Tool 全部注册进既有 `ToolRegistry`：

```text
load_skill
create_skill_draft
publish_skill_draft
install_skill
```

- `load_skill(name)`：读取有效正式 Skill，返回完整正文 Attachment 和简短 ToolResult。
- `create_skill_draft(name, description, instructions)`：只在用户明确要求创建 Skill 时调用。当前 Agent 直接生成参数，不再触发嵌套 LLM 调用；Tool 写入 `.skills/.drafts/<name>/SKILL.md`。
- `publish_skill_draft(name)`：用户明确要求发布时调用，再次校验后移动到 `.skills/<name>/` 并刷新 Catalog。
- `install_skill(source, ref?, skill_path?)`：用户明确要求后，经既有审批从 HTTPS Git 仓库浅克隆到临时目录；`ref` 仅支持分支或标签。仓库必须只包含一个候选 Skill，或由 `skill_path` 指定。安装拒绝符号链接、无效路径和正式目录覆盖，发布后刷新 Catalog。

创建和发布是文件写入操作，必须使用既有 `ToolPermissionHook` 与 Channel `y/N` 审批。`load_skill` 是只读操作。

Phase 5 不实现从普通会话自动生成、自动评估或自动发布 Skill；这些属于后续 Agent 自进化能力。Creator 的草稿、校验和发布入口将为该阶段保留稳定扩展点。

内置 `.skills/skill-creator/SKILL.md` 提供与 Codex、Claude Code Agent Skills 一致的意图澄清、渐进式披露、前置校验和草稿发布流程。用户明确要求创建或修改 Skill 时，Agent 应先加载它，再生成 `create_skill_draft` 参数；它不调用嵌套 LLM，也不自动发布。内置 `.skills/skill-installer/SKILL.md` 仅在用户明确要求安装外部 Skill 时加载，指导模型确认来源、分支/标签、仓库内路径和安装后加载；实际克隆继续由需要审批的 `install_skill` Tool 执行。内置 `.skills/grilling/SKILL.md` 仅在用户要求压力测试计划、决策或想法时加载；它逐次提出决策问题、优先查找可验证事实，并在用户确认达成共识前不执行后续操作。`grilling/agents/openai.yaml` 是可选界面元数据，当前 Runtime 不读取或注入它。

## 6. 配置

所有参数继续集中在现有设置文件：

```json
{
  "skills": {
    "directory": ".skills",
    "max_loaded_skills_per_turn": 3,
    "max_skill_tokens": 8000,
    "max_loaded_skill_tokens_per_turn": 16000
  }
}
```

`Settings` 新增 `SkillsSettings`，本地配置加载与其他模块保持同一模式。

## 7. 故障隔离

- 缺少 `SKILL.md`、frontmatter 不合法、必填字段为空、目录名不匹配、正文为空的目录：跳过，不影响其他 Skill 或 Runtime。
- 两个目录声明同一个 `name`：两个都不进入 Catalog，避免静默覆盖。
- 扫描错误保存在 `SkillManager.errors`，由未来 Web `/skill list` 或 `/skill refresh` 展示；CLI 不额外输出。
- 草稿名或正式 Skill 名已存在时，Creator 拒绝覆盖。
- 发布前必须再次完整校验；失败时草稿保留。
- 刷新后以本次扫描得到的有效 Catalog 原子替换旧 Catalog。

## 8. 观测与持久化

不新增 `skills_usage.jsonl` 或其他重复 JSONL。

`turn_traces.jsonl` 的 `RUN.metadata` 新增：

```json
{
  "loaded_skill_names": ["release-review"],
  "loaded_skill_count": 1,
  "loaded_skill_token_count": 1830
}
```

`tool_calls.jsonl` 中 `load_skill` 只保存“已加载 Skill：<name>”或错误文本，不保存完整 `SKILL.md`。`TurnMonitorHook` 不修改；加载失败自然计入既有 `tool_failure_count`。

## 9. 实施范围

本阶段实现：

- 单目录扫描、校验、内存 Catalog、错误隔离和显式刷新服务接口。
- 根系统提示词中的全量 Skill 元数据注入。
- `load_skill` 的当前轮渐进式披露和 token 限制。
- 用户触发的草稿创建与发布。
- 现有 Tool 审批、上下文预算和 RUN trace 集成。

本阶段不实现：

- Web UI、Web Channel、SSE、WebSocket 或 CLI slash Skill 命令。
- 多目录、ZIP 安装、Marketplace、签名、版本同步或自动更新。
- 自动创建、自动评估、自动发布、从会话自动沉淀的 Skill 自进化。
- Skill 脚本自动执行、独立权限沙箱或绕过既有 Tools/MCP 的执行能力。
- MCP Resource/Prompt Catalog 的全量注入或 Phase 4 MCP 边界调整。

## 10. 验收标准

- 标准 Anthropic/Codex 风格 `SKILL.md` 可扫描、列出并按需加载。
- 全部有效 Skill 元数据进入根 system prompt，并计入 BUILD/SAVE token 预算。
- 完整 Skill 仅当前轮可见，下一轮不重放。
- 单个、数量和总 token 三项限制正确拒绝加载。
- 非法、重名和目录名不匹配的 Skill 被隔离，不影响有效 Catalog。
- 草稿创建、审批、发布、刷新和冲突拒绝正确。
- MCP Tool schema、MCP Resource/Prompt、Session、Memory、TurnMonitorHook 的既有边界不回归。
- RUN trace 正确记录实际加载的 Skill；不新增重复 JSONL。

## 11. 实施前提

已按测试先行和最小改动原则完成实施。

## 12. 完成记录

- 新增 `skills/types.py`、`validator.py`、`manager.py`、`creator.py`、`load_skill_tool.py`、`skill_draft_tools.py`，并保留项目根目录 `.skills/` 作为唯一正式目录。
- `SkillValidator` 校验 `SKILL.md`、必填 frontmatter、目录名、正文和名称规则；未知 frontmatter 进入 `extra_metadata`。无效及重名声明进入 `SkillManager.errors`，不会阻塞有效 Catalog。
- `runtime.start()` 扫描 Catalog；BUILD 通过 `context/system_prompt.py` 的 `build_system_prompt()` 注入全量元数据，`context/token_budget.py` 记录 `skill_catalog_tokens` 并沿用 300k BUILD 拒绝策略。
- `load_skill` 只读且不审批。完整正文以固定包装的、已校验本地 system Attachment 进入当前轮；MCP Attachment 的 user/assistant 角色限制没有放宽。
- `create_skill_draft`、`publish_skill_draft` 标记为审批写入 Tool。发布会再次校验、拒绝覆盖并刷新 Catalog；没有嵌套 LLM、自动创建或自动发布。
- 内置 `skill-creator` 提供草稿结构和发布前检查；根系统提示词要求在用户明确创建或修改 Skill 前先加载它。
- `install_skill` 是审批写入 Tool，仅接受无凭据 HTTPS Git URL，可选受限 ref 和仓库内目录。它在 `.staging/` 克隆、校验并拒绝符号链接或覆盖，再发布到正式目录并刷新 Catalog；不会执行 Skill 脚本或保留 `.git`。
- 内置 `grilling` 仅提供当前轮方案压力测试工作流：逐题澄清决策并在用户确认共识前停止执行；不新增 Tool、权限、持久化或 Runtime 分支。
- AgentLoop 分别执行 MCP 12k 附件限制、Skill 单个 8k/每轮 3 个/正文累计 16k 限制，并在每次追加附件前检查实际 working messages 与 Tool schema 的总上下文上限。
- RUN trace 新增 `loaded_skill_names`、`loaded_skill_count`、`loaded_skill_token_count`；`tool_calls.jsonl` 对成功加载只保存“已加载 Skill：<name>”，不保存正文；未新增 JSONL，未改动 TurnMonitorHook。

验证：`pytest -q`、`git diff --check` 和 CLI `/exit` 冒烟均通过。测试覆盖扫描隔离、Catalog/token 预算、当前轮加载、三项加载预算、草稿冲突/发布、审批属性、RUN trace 和 MCP Attachment 角色边界。
