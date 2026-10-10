from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace


def _attachment(tmp_path: Path):
    models = importlib.import_module("Turning-Good-Agent.web.attachments.models")
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    owner = models.AttachmentOwner("local-user", "web", "conversation")
    metadata = models.AttachmentMetadata("att_image", "image", "photo.png", "image/png", path.stat().st_size)
    return models.ResolvedAttachment(metadata, path, "digest", owner)


def _document_attachment(tmp_path: Path):
    models = importlib.import_module("Turning-Good-Agent.web.attachments.models")
    path = tmp_path / "resume.txt"
    path.write_text("private document text", encoding="utf-8")
    owner = models.AttachmentOwner("local-user", "web", "conversation")
    metadata = models.AttachmentMetadata("att_document", "document", "resume.txt", "text/plain", path.stat().st_size)
    return models.ResolvedAttachment(metadata, path, "digest", owner)


def test_runtime_rejects_image_before_context_or_model_when_vision_is_disabled(tmp_path: Path) -> None:
    async def run() -> None:
        state = importlib.import_module("Turning-Good-Agent.runtime.state")
        messages = importlib.import_module("Turning-Good-Agent.bus.messages")
        builder = importlib.import_module("Turning-Good-Agent.context.builder")
        route = messages.ChannelRoute("local-user", "web", "conversation", "session")
        inbound = messages.InboundMessage("request", route=route, content="分析图片", attachments=[_attachment(tmp_path)])
        calls: list[str] = []

        class Sessions:
            async def all_messages(self, _session_id):
                return []

        runtime = SimpleNamespace(
            settings=SimpleNamespace(
                multi_agent=SimpleNamespace(enabled=False),
                runtime=SimpleNamespace(max_context_tokens=100_000),
            ),
            sessions=Sessions(),
            profile_memory=SimpleNamespace(read=lambda: ""),
            skills=SimpleNamespace(list_skills=lambda: []),
            agent_loop=SimpleNamespace(
                llm=SimpleNamespace(supports_vision=False),
                openai_tools=lambda *_args, **_kwargs: calls.append("tools") or [],
            ),
            context_builder=SimpleNamespace(build=lambda **_kwargs: calls.append("context") or []),
        )
        ctx = SimpleNamespace(inbound=inbound, session=SimpleNamespace(id="session", summary="", uncompacted_history=[]), principal_context=None, allowed_tool_names=None)

        assert await state.build(runtime, ctx) == "rejected"
        assert "不支持图片理解" in ctx.final_content
        assert calls == ["tools"]

    import asyncio

    asyncio.run(run())


def test_context_builder_keeps_image_data_transient_in_provider_message(tmp_path: Path) -> None:
    builder = importlib.import_module("Turning-Good-Agent.context.builder").ContextBuilder()
    attachment = _attachment(tmp_path)

    messages = builder.build("", [], "请描述", "", attachments=[attachment])

    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith("请描述")
    assert "att_image" in content[0]["text"]
    assert str(attachment.path) not in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert "base64," in content[1]["image_url"]["url"]


def test_context_builder_exposes_document_id_for_on_demand_read_without_path(tmp_path: Path) -> None:
    builder = importlib.import_module("Turning-Good-Agent.context.builder").ContextBuilder()
    attachment = _document_attachment(tmp_path)

    messages = builder.build("", [], "请总结", "", attachments=[attachment])

    content = messages[-1]["content"]
    assert isinstance(content, str)
    assert content.startswith("请总结")
    assert '"attachment_id": "att_document"' in content
    assert "调用 read_attachment" in content
    assert "private document text" not in content
    assert str(attachment.path) not in content
