from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace


def test_run_passes_context_allowlist_to_agent_loop() -> None:
    """Would fail if an IM turn exposed a filtered schema but executed without its allowlist."""

    async def run() -> None:
        state_module = importlib.import_module("Turning-Good-Agent.runtime.state")
        result = SimpleNamespace(
            final_content="done",
            tool_calls=[],
            usage=object(),
            loaded_skill_names=[],
            loaded_skill_token_count=0,
            consumed_guidance=[],
            cancelled=False,
        )

        class RecordingLoop:
            def __init__(self) -> None:
                self.allowed_tool_names: frozenset[str] | None = None
                self.multi_agent_invocation = object()

            async def run(
                self,
                messages,
                adapter,
                auto_approve,
                *,
                allowed_tool_names,
                multi_agent_invocation,
            ):
                del messages, adapter, auto_approve
                self.allowed_tool_names = allowed_tool_names
                self.multi_agent_invocation = multi_agent_invocation
                return result

        loop = RecordingLoop()
        runtime = SimpleNamespace(
            agent_loop=loop,
            settings=SimpleNamespace(tool_permissions=SimpleNamespace(auto_approve_tools=True)),
        )
        context = SimpleNamespace(
            session=object(),
            model_messages=[{"role": "user", "content": "hello"}],
            channel_adapter=object(),
            allowed_tool_names=frozenset({"safe_tool"}),
            multi_agent_invocation=None,
        )

        assert await state_module.run(runtime, context) == "ok"
        assert loop.allowed_tool_names == frozenset({"safe_tool"})
        assert loop.multi_agent_invocation is None

    asyncio.run(run())
