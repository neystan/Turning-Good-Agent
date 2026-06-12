class EventMemory:
    """预留事件记忆写入接口。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    def append(self, event: str) -> None:
        """追加一条事件记忆。"""
        self.events.append(event)
