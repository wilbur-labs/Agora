"""Shared test fixtures and mock providers."""
from __future__ import annotations

import json
from typing import AsyncIterator

from agora.models.base import GenerateResult, Message, ModelProvider, ToolCall


class MockProvider(ModelProvider):
    """Deterministic mock provider for unit tests."""

    name = "mock"

    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or ["Mock response."])
        self._call_count = 0
        self.last_messages: list[Message] = []

    async def generate(self, messages: list[Message]) -> str:
        self.last_messages = messages
        resp = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return resp

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        text = await self.generate(messages)
        # Yield word by word to simulate streaming
        for word in text.split():
            yield word + " "

    async def generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> GenerateResult:
        self.last_messages = messages
        resp = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1

        # If response looks like a tool call instruction, parse it
        if resp.startswith("TOOL_CALL:"):
            parts = resp.split(":", 2)  # TOOL_CALL:func_name:{"arg":"val"}
            func_name = parts[1]
            args = json.loads(parts[2]) if len(parts) > 2 else {}
            return GenerateResult(
                content="",
                tool_calls=[ToolCall(id=f"call_{self._call_count}", function_name=func_name, arguments=args)],
                finish_reason="tool_calls",
            )
        return GenerateResult(content=resp, tool_calls=[], finish_reason="stop")


class MockProviderWithToolSequence(MockProvider):
    """Mock provider that executes a sequence of tool calls then returns text."""

    def __init__(self, tool_calls: list[tuple[str, dict]], final_response: str = "Done."):
        self._tool_sequence = list(tool_calls)
        self._final = final_response
        self._call_count = 0
        self.last_messages: list[Message] = []

    async def generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> GenerateResult:
        self.last_messages = messages
        idx = self._call_count
        self._call_count += 1

        if idx < len(self._tool_sequence):
            func_name, args = self._tool_sequence[idx]
            return GenerateResult(
                content="",
                tool_calls=[ToolCall(id=f"call_{idx}", function_name=func_name, arguments=args)],
                finish_reason="tool_calls",
            )
        return GenerateResult(content=self._final, tool_calls=[], finish_reason="stop")

    async def generate(self, messages: list[Message]) -> str:
        self.last_messages = messages
        return self._final
