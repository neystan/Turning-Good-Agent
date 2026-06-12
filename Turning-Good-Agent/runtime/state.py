from enum import Enum, auto


class TurnState(Enum):
    """定义单轮 Agent 执行的 6 个状态。"""

    PREPARE = auto()
    RUN = auto()
    SAVE = auto()
    COMPACT = auto()
    RESPOND = auto()
    DONE = auto()


_TRANSITIONS: dict[tuple[TurnState, str], TurnState] = {
    (TurnState.PREPARE, "ok"): TurnState.RUN,
    (TurnState.RUN, "ok"): TurnState.SAVE,
    (TurnState.SAVE, "ok"): TurnState.COMPACT,
    (TurnState.COMPACT, "ok"): TurnState.RESPOND,
    (TurnState.RESPOND, "ok"): TurnState.DONE,
}


def next_state(state: TurnState, event: str) -> TurnState:
    """根据当前状态和事件返回下一状态。"""
    if event == "error":
        return TurnState.RESPOND
    return _TRANSITIONS[(state, event)]
