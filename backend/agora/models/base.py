"""Model provider base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, TypedDict


class Message(TypedDict, total=False):
    role: str
    content: str


class ModelProvider(ABC):
    name: str

    @abstractmethod
    async def generate(self, messages: list[Message]) -> str: ...

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        yield await self.generate(messages)
