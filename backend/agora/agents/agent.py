"""Agent — loads identity from YAML profile, calls model provider."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import yaml

from agora.models.base import ModelProvider
from agora.models.registry import get_registry

PROFILES_DIR = Path(__file__).parent / "profiles"


class Agent:
    def __init__(self, name: str, profile: str, model_name: str):
        self.name = name
        self.model_name = model_name

        path = PROFILES_DIR / profile
        data = yaml.safe_load(path.read_text())
        self.role: str = data.get("role", name)
        self.perspective: str = data.get("perspective", "")

    @property
    def provider(self) -> ModelProvider:
        return get_registry().get(self.model_name)

    def system_prompt(self, user_profile: str = "", memory: str = "", skills: str = "") -> str:
        parts = [
            f"You are {self.name} ({self.role}).\n\n{self.perspective}",
            "RULES:\n"
            "- You are in a multi-agent council discussion. Other agents' messages are in the history.\n"
            "- Do NOT repeat what others already said. Only add new insights from your perspective.\n"
            "- Keep each response under 300 words.\n"
            "- LANGUAGE: Respond ENTIRELY in the same language as the user's LATEST message.\n"
            "- Detect language from the user's words, ignoring technical terms or proper nouns.",
        ]
        if user_profile:
            parts.insert(1, f"<user_profile>\n{user_profile}\n</user_profile>")
        if memory:
            parts.insert(1, f"<memory>\n{memory}\n</memory>")
        if skills:
            parts.insert(1, f"<skills>\n{skills}\n</skills>")
        return "\n\n".join(parts)

    async def respond(self, messages: list[dict], user_profile: str = "", memory: str = "", skills: str = "") -> str:
        full = [{"role": "system", "content": self.system_prompt(user_profile, memory, skills)}] + messages
        return await self.provider.generate(full)

    async def stream_respond(self, messages: list[dict], user_profile: str = "", memory: str = "", skills: str = "") -> AsyncIterator[str]:
        full = [{"role": "system", "content": self.system_prompt(user_profile, memory, skills)}] + messages
        async for chunk in self.provider.stream(full):
            yield chunk

    def __repr__(self) -> str:
        return f"Agent({self.name}, model={self.model_name})"
