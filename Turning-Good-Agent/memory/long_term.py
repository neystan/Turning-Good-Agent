class ProfileMemory:
    """保存用户长期偏好文本。"""

    def __init__(self) -> None:
        self.text = ""

    def read(self) -> str:
        """读取长期偏好。"""
        return self.text
