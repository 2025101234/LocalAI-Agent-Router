"""记忆/上下文管理。"""

from __future__ import annotations


class MemoryManager:
    """管理当前会话的短期记忆（上下文窗口）。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._messages: list[dict[str, str]] = []

    def add(self, role: str, content: str, model: str | None = None) -> None:
        entry: dict[str, str] = {"role": role, "content": content}
        if model:
            entry["model"] = model
        self._messages.append(entry)
        if len(self._messages) > self.max_messages:
            # 保留 system 消息（如果存在），移除最旧的用户/助手消息
            system_messages = [m for m in self._messages if m.get("role") == "system"]
            others = [m for m in self._messages if m.get("role") != "system"]
            excess = len(self._messages) - self.max_messages
            others = others[excess:]
            self._messages = system_messages + others

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages = []

    def set_system_prompt(self, prompt: str) -> None:
        """设置或更新 system prompt。"""
        self._messages = [m for m in self._messages if m.get("role") != "system"]
        self._messages.insert(0, {"role": "system", "content": prompt})
