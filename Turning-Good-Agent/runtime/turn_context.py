from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage
from ..channels.base import ChannelAdapter, SilentChannelAdapter
from .principal_context import PrincipalRuntimeContext
from .state import TurnState

if TYPE_CHECKING:
    from ..multi_agent.delegate_tool import DelegateMultiAgentInvocation


_current_route: ContextVar[ChannelRoute | None] = ContextVar("current_channel_route", default=None)
_current_inbound: ContextVar[InboundMessage | None] = ContextVar("current_inbound_message", default=None)
_current_turn_context: ContextVar["TurnContext | None"] = ContextVar(
    "current_turn_context", default=None
)
_current_principal_context: ContextVar[PrincipalRuntimeContext | None] = ContextVar(
    "current_principal_context", default=None
)


def current_route() -> ChannelRoute | None:
    """返回当前任务上下文的 ChannelRoute。"""
    return _current_route.get()


def set_current_route(route: ChannelRoute) -> Token[ChannelRoute | None]:
    """设置当前任务的路由，并返回供调用方复位的 token。"""
    return _current_route.set(route)


def reset_current_route(token: Token[ChannelRoute | None]) -> None:
    """复位当前任务的路由，避免跨 turn 泄漏。"""
    _current_route.reset(token)


def current_inbound() -> InboundMessage | None:
    """返回当前 turn 尚未保存的入站消息。"""
    return _current_inbound.get()


def set_current_inbound(message: InboundMessage) -> Token[InboundMessage | None]:
    """绑定当前 turn 的入站消息，并返回供调用方复位的 token。"""
    return _current_inbound.set(message)


def reset_current_inbound(token: Token[InboundMessage | None]) -> None:
    """复位当前 turn 的入站消息，避免跨 turn 泄漏。"""
    _current_inbound.reset(token)


# 返回当前异步任务绑定的父 TurnContext。
def current_turn_context() -> "TurnContext | None":
    return _current_turn_context.get()


# 绑定当前父 TurnContext 并返回供调用方复位的 token。
def set_current_turn_context(context: "TurnContext") -> Token["TurnContext | None"]:
    return _current_turn_context.set(context)


# 复位当前父 TurnContext，避免 Worker 任务泄漏上下文。
def reset_current_turn_context(token: Token["TurnContext | None"]) -> None:
    _current_turn_context.reset(token)


def current_principal_context() -> PrincipalRuntimeContext | None:
    """返回当前任务绑定的主体 Runtime Context。"""
    return _current_principal_context.get()


def set_current_principal_context(
    context: PrincipalRuntimeContext,
) -> Token[PrincipalRuntimeContext | None]:
    """设置当前任务主体 Context，并返回复位 token。"""
    return _current_principal_context.set(context)


def reset_current_principal_context(token: Token[PrincipalRuntimeContext | None]) -> None:
    """复位主体 Context，避免跨 turn 泄漏。"""
    _current_principal_context.reset(token)


@dataclass(slots=True)
class TurnContext:
    """保存一轮消息处理过程中的临时状态。"""

    inbound: InboundMessage
    state: TurnState = TurnState.COMMAND
    turn_id: str = field(default_factory=lambda: str(uuid4()))
    session: Any | None = None
    uncompacted_history: list[Any] = field(default_factory=list)
    model_messages: list[dict[str, Any]] = field(default_factory=list)
    final_content: str = ""
    shortcut_response: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    loaded_skill_names: list[str] = field(default_factory=list)
    loaded_skill_token_count: int = 0
    consumed_guidance: list[str] = field(default_factory=list)
    cancelled: bool = False
    llm_usage: Any | None = None
    trace: list[Any] = field(default_factory=list)
    saved_trace_count: int = 0
    true_token_usage: dict[str, Any] = field(default_factory=dict)
    context_tokens: dict[str, Any] = field(default_factory=dict)
    should_compact: bool = False
    compact_stats: dict[str, Any] = field(default_factory=dict)
    outbound: OutboundMessage | None = None
    error: str | None = None
    channel_adapter: ChannelAdapter = field(default_factory=SilentChannelAdapter)
    allowed_tool_names: frozenset[str] | None = None
    principal_context: PrincipalRuntimeContext | None = None
    multi_agent_invocation: DelegateMultiAgentInvocation | None = None
    attachment_tool: Any | None = None
