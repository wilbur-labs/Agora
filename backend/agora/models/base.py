"""Model provider base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, TypedDict


class Message(TypedDict, total=False):
    role: str
    content: str
    tool_calls: list[dict]
    tool_call_id: str
    name: str


@dataclass
class ToolCall:
    id: str
    function_name: str
    arguments: dict[str, Any]


@dataclass
class GenerateResult:
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str = "stop"


class ModelProvider(ABC):
    name: str

    @abstractmethod
    async def generate(self, messages: list[Message]) -> str: ...

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        yield await self.generate(messages)

    async def generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> GenerateResult:
        """Generate with function calling support. Override in providers that support it."""
        # Fallback: ignore tools, return plain text
        text = await self.generate(messages)
        return GenerateResult(content=text, tool_calls=[])

    async def stream_generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> AsyncIterator[GenerateResult | str]:
        """Streaming generate with tool support. Yields str chunks for text, then a final GenerateResult if tool_calls present.

        Default: falls back to non-streaming generate_with_tools.
        """
        result = await self.generate_with_tools(messages, tools)
        if result.content:
            yield result.content
        if result.tool_calls:
            yield result
