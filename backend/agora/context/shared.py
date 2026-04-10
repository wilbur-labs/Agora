"""Shared context — single conversation history visible to all agents."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SharedContext:
    messages: list[dict] = field(default_factory=list)

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_agent(self, agent_name: str, content: str):
        self.messages.append({"role": "assistant", "content": f"[{agent_name}] {content}"})

    def get_messages(self) -> list[dict]:
        return list(self.messages)

    def clear(self):
        self.messages.clear()
