import asyncio
from importlib import import_module


SkillManager = import_module("Turning-Good-Agent.skills.manager").SkillManager
SkillsSettings = import_module("Turning-Good-Agent.config.settings").SkillsSettings
build_system_prompt = import_module("Turning-Good-Agent.context.system_prompt").build_system_prompt
render_skill_catalog = import_module("Turning-Good-Agent.context.system_prompt").render_skill_catalog
build_context_token_breakdown = import_module(
    "Turning-Good-Agent.context.token_budget"
).build_context_token_breakdown
ContextAttachment = import_module("Turning-Good-Agent.tools.context_attachment").ContextAttachment
validate_context_attachment = import_module(
    "Turning-Good-Agent.tools.context_attachment"
).validate_context_attachment
count_content_tokens = import_module("Turning-Good-Agent.sessions.token_counter").count_content_tokens
AgentLoop = import_module("Turning-Good-Agent.runtime.agent_loop").AgentLoop
RuntimeSettings = import_module("Turning-Good-Agent.config.settings").RuntimeSettings
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry
LoadSkillTool = import_module("Turning-Good-Agent.skills.load_skill_tool").LoadSkillTool
CreateSkillDraftTool = import_module("Turning-Good-Agent.skills.skill_draft_tools").CreateSkillDraftTool
PublishSkillDraftTool = import_module("Turning-Good-Agent.skills.skill_draft_tools").PublishSkillDraftTool
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
Settings = import_module("Turning-Good-Agent.config.settings").Settings
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage


def write_skill(root, directory, name, description, body="执行检查。"):
    """写入一个最小 Skill 测试夹具。"""
    path = root / directory
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nmetadata:\n  short-description: 简介\n---\n{body}\n",
        encoding="utf-8",
    )


def test_scan_isolates_invalid_and_duplicate_skills(tmp_path):
    """验证非法目录和重名 Skill 不影响有效 Catalog。"""
    write_skill(tmp_path, "valid", "valid", "有效 Skill")
    write_skill(tmp_path, "mismatch", "other", "目录不匹配")
    write_skill(tmp_path, "first", "same", "重名一")
    write_skill(tmp_path, "second", "same", "重名二")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    write_skill(tmp_path / ".drafts", "candidate", "candidate", "草稿不扫描")
    write_skill(tmp_path / ".staging", "repository", "staging-skill", "临时安装内容不扫描")

    manager = SkillManager(tmp_path, SkillsSettings())
    manager.scan()

    assert [item.name for item in manager.list_skills()] == ["valid"]
    assert {error.directory for error in manager.errors} == {"broken", "mismatch", "first", "second"}
    assert manager.list_skills()[0].extra_metadata == {"metadata": {"short-description": "简介"}}


def test_catalog_is_injected_and_counted_in_budget(tmp_path):
    """验证所有有效元数据进入根提示词并可统计 token。"""
    write_skill(tmp_path, "review", "review", "发布检查")
    manager = SkillManager(tmp_path, SkillsSettings())
    manager.scan()

    prompt = build_system_prompt(manager.list_skills())

    assert "review：发布检查" in prompt
    assert manager.catalog_token_count == count_content_tokens("review：发布检查")
    breakdown = build_context_token_breakdown(
        summary="",
        history=[],
        current_input="问题",
        output="",
        profile_memory="",
        openai_tools=[],
        include_current_turn=True,
        skills=manager.list_skills(),
    )
    assert breakdown["skill_catalog_tokens"] == count_content_tokens(render_skill_catalog(manager.list_skills()))


def test_load_skill_returns_current_turn_system_attachment_and_enforces_limits(tmp_path):
    """验证完整正文只作为受限 system 附件返回。"""
    write_skill(tmp_path, "review", "review", "发布检查", "# 指南\n逐项检查发布结果。")
    manager = SkillManager(tmp_path, SkillsSettings(max_skill_tokens=100, max_loaded_skill_tokens_per_turn=100))
    manager.scan()

    loaded = manager.load("review")

    assert loaded.token_count == count_content_tokens(loaded.body)
    result = asyncio.run(LoadSkillTool(manager).run({"name": "review"}))
    assert result.context_attachment is not None
    assert result.context_attachment.messages[0]["role"] == "system"
    assert "已加载 Skill：review" in result.context_attachment.messages[0]["content"]
    assert "# 指南" in result.context_attachment.messages[0]["content"]

    huge = "词 " * 200
    write_skill(tmp_path, "huge", "huge", "过长", huge)
    manager.scan()
    try:
        manager.load("huge")
    except RuntimeError as exc:
        assert "超过 100 tokens" in str(exc)
    else:
        raise AssertionError("过长 Skill 必须被拒绝")


def test_only_skill_attachment_may_use_system_role():
    """验证 MCP 附件仍不能放宽到 system role。"""
    content = "临时内容"
    skill = ContextAttachment(
        "skill:review",
        [{"role": "system", "content": content}],
        count_content_tokens(content),
        kind="skill",
        verified=True,
    )
    mcp = ContextAttachment(
        "MCP Prompt：demo/review",
        [{"role": "system", "content": content}],
        count_content_tokens(content),
        kind="mcp",
    )

    assert validate_context_attachment(skill, 0, 100) is None
    assert validate_context_attachment(mcp, 0, 100) == "本轮上下文附件只允许 user 或 assistant 文本消息"


def test_draft_create_and_publish_refuse_conflicts(tmp_path):
    """验证草稿创建、发布和重名拒绝。"""
    manager = SkillManager(tmp_path, SkillsSettings())
    manager.scan()

    assert LoadSkillTool(manager).approval_required is False
    assert CreateSkillDraftTool(manager).approval_required is True
    assert PublishSkillDraftTool(manager).approval_required is True

    asyncio.run(manager.create_draft("review", "发布检查", "检查发布步骤。"))
    assert (tmp_path / ".drafts" / "review" / "SKILL.md").exists()
    asyncio.run(manager.publish_draft("review"))
    assert [item.name for item in manager.list_skills()] == ["review"]

    try:
        asyncio.run(manager.create_draft("review", "重复", "不会覆盖。"))
    except RuntimeError as exc:
        assert "已存在" in str(exc)
    else:
        raise AssertionError("已有正式 Skill 时不应覆盖")


def test_agent_loop_rejects_skill_total_budget_without_truncation(tmp_path):
    """验证多次加载会按正文总 token 限制返回工具错误。"""
    write_skill(tmp_path, "one", "one", "一", "内容一。")
    write_skill(tmp_path, "two", "two", "二", "内容二。")
    settings = SkillsSettings(max_skill_tokens=100, max_loaded_skill_tokens_per_turn=5)
    manager = SkillManager(tmp_path, settings)
    manager.scan()
    registry = ToolRegistry()
    registry.register(LoadSkillTool(manager))

    class Llm:
        """按顺序请求两个 Skill。"""

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools):
            del messages, tools
            self.calls += 1
            if self.calls == 1:
                return LLMResponse("", [ToolCall("one", "load_skill", {"name": "one"})], LLMUsage(1, 1, 2))
            if self.calls == 2:
                return LLMResponse("", [ToolCall("two", "load_skill", {"name": "two"})], LLMUsage(1, 1, 2))
            return LLMResponse("完成", usage=LLMUsage(1, 1, 2))

    result = asyncio.run(AgentLoop(Llm(), registry, RuntimeSettings(), skills=settings).run([{"role": "user", "content": "请求"}]))

    assert result.loaded_skill_names == ["one"]
    assert "本轮已加载 Skill 总量超过 5 tokens 限制" in result.tool_calls[1]["content"]
    assert "内容二" not in result.tool_calls[1]["content"]


def test_agent_loop_records_loaded_skill_trace_metadata(tmp_path):
    """验证 RUN 所需的加载观测来自实际成功附件。"""
    write_skill(tmp_path, "review", "review", "发布检查", "检查发布。")
    settings = SkillsSettings()
    manager = SkillManager(tmp_path, settings)
    manager.scan()
    registry = ToolRegistry()
    registry.register(LoadSkillTool(manager))

    class Llm:
        """先加载 Skill，再返回回答。"""

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse("", [ToolCall("review", "load_skill", {"name": "review"})], LLMUsage(1, 1, 2))
            assert messages[-1]["role"] == "system"
            assert "已加载 Skill：review" in messages[-1]["content"]
            assert tools
            return LLMResponse("完成", usage=LLMUsage(1, 1, 2))

    result = asyncio.run(AgentLoop(Llm(), registry, RuntimeSettings(), skills=settings).run([{"role": "user", "content": "请求"}]))

    assert result.loaded_skill_names == ["review"]
    assert result.loaded_skill_token_count == count_content_tokens("检查发布。")
    assert result.tool_calls[0]["content"] == "已加载 Skill：review"


def test_runtime_records_skill_trace_without_replaying_next_turn(tmp_path, monkeypatch):
    """验证完整 Skill 只在加载当轮可见并写入 RUN trace。"""
    monkeypatch.chdir(tmp_path)
    write_skill(tmp_path / ".skills", "review", "review", "发布检查", "检查发布。")

    class Llm:
        """记录每次 Runtime 模型上下文。"""

        def __init__(self):
            self.calls = []

        async def complete(self, messages, tools):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return LLMResponse("", [ToolCall("review", "load_skill", {"name": "review"})], LLMUsage(1, 1, 2))
            if len(self.calls) == 2:
                return LLMResponse("已完成", usage=LLMUsage(1, 1, 2))
            return LLMResponse("下一轮", usage=LLMUsage(1, 1, 2))

    async def run():
        settings = Settings(data_dir=tmp_path / ".sessions")
        llm = Llm()
        runtime = AgentRuntime.create_default(settings, llm)
        await runtime.start()
        await runtime.run_turn(InboundMessage.new("执行检查", "demo", "user", "cli"))
        trace = next(item for item in runtime.last_trace if item.state == "RUN")
        await runtime.run_turn(InboundMessage.new("继续", "demo", "user", "cli"))
        await runtime.close()
        return llm, trace

    llm, trace = asyncio.run(run())

    assert trace.metadata["loaded_skill_names"] == ["review"]
    assert trace.metadata["loaded_skill_count"] == 1
    assert trace.metadata["loaded_skill_token_count"] == count_content_tokens("检查发布。")
    assert all("已加载 Skill：review" not in item["content"] for item in llm.calls[2])
