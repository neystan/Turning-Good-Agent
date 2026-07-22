from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..bus.messages import InboundMessage, OutboundMessage
from ..channels.output import ChannelOutput, SilentChannelOutput
from .state import TurnState


@dataclass(slots=True)
class TurnContext:
    """保存一轮消息处理过程中的临时状态。"""

    inbound: InboundMessage
    state: TurnState = TurnState.COMMAND
    turn_id: str = field(default_factory=lambda: str(uuid4()))
    session: Any | None = None
    full_history: list[Any] = field(default_factory=list)
    uncompacted_history: list[Any] = field(default_factory=list)
    history: list[Any] = field(default_factory=list)
    model_messages: list[dict[str, Any]] = field(default_factory=list)
    final_content: str = ""
    shortcut_response: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_usage: Any | None = None
    trace: list[Any] = field(default_factory=list)
    saved_trace_count: int = 0
    true_token_usage: dict[str, Any] = field(default_factory=dict)
    context_tokens: dict[str, Any] = field(default_factory=dict)
    should_compact: bool = False
    compact_stats: dict[str, Any] = field(default_factory=dict)
    outbound: OutboundMessage | None = None
    error: str | None = None
    output: ChannelOutput = field(default_factory=SilentChannelOutput)
