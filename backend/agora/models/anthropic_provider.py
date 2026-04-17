"""Anthropic Claude API provider — supports function calling via Messages API."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from .base import GenerateResult, Message, ModelProvider, ToolCall

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0
_TIMEOUT = httpx.Timeout(connect=30, read=600, write=30, pool=30)


class AnthropicProvider(ModelProvider):
    """Anthropic Claude API with tool use support."""

    name = "anthropic"

    def __init__(self, *, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def _check_response(self, resp: httpx.Response, msgs: list[dict]) -> None:
        """Log error details and raise on non-200."""
        if resp.status_code != 200:
            body = await resp.aread() if hasattr(resp, 'aread') else b""
            logger.error("Anthropic %d: %s | roles=%s", resp.status_code, body.decode()[:500], [m["role"] for m in msgs])
        resp.raise_for_status()

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """Convert OpenAI-style messages to Anthropic format.
        Returns (system_prompt, messages_list).
        Anthropic requires strict user/assistant alternation, so consecutive
        same-role messages are merged.
        """
        system = ""
        converted = []
        for m in messages:
            role = m["role"]
            content = m.get("content", "")

            if role == "system":
                system += ("\n\n" + content) if system else content
                continue

            if role == "assistant":
                # Handle tool_calls in assistant messages
                tool_calls = m.get("tool_calls", [])
                if tool_calls:
                    blocks: list[dict] = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        try:
                            inp = json.loads(fn.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            inp = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": inp,
                        })
                    converted.append({"role": "assistant", "content": blocks})
                else:
                    # Merge with previous assistant message if consecutive
                    if converted and converted[-1]["role"] == "assistant" and isinstance(converted[-1]["content"], str):
                        converted[-1]["content"] += "\n\n" + (content or "")
                    else:
                        converted.append({"role": "assistant", "content": content or ""})

            elif role == "tool":
                # Tool result message
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": content or "",
                    }],
                })

            elif role == "user":
                # Merge with previous user message if consecutive
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], str):
                    converted[-1]["content"] += "\n\n" + (content or "")
                else:
                    converted.append({"role": "user", "content": content or ""})

        # Anthropic requires first message to be user role
        if converted and converted[0]["role"] != "user":
            converted.insert(0, {"role": "user", "content": "(context from prior discussion)"})

        # Anthropic rejects trailing whitespace in assistant messages
        for m in converted:
            if m["role"] == "assistant" and isinstance(m["content"], str):
                m["content"] = m["content"].strip()

        return system, converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool format to Anthropic tool format."""
        anthropic_tools = []
        for t in tools:
            if t.get("type") == "function":
                fn = t["function"]
                anthropic_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
        return anthropic_tools

    async def generate(self, messages: list[Message]) -> str:
        result = await self.generate_with_tools(messages, tools=[])
        return result.content

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        system, msgs = self._convert_messages(messages)
        logger.info("Anthropic stream: system=%d chars, msgs=%d, roles=%s",
                     len(system), len(msgs), [m["role"] for m in msgs])
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": 16384,
            "stream": True,
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{self.base_url}/messages",
                headers=self._headers(), json=body,
            ) as resp:
                await self._check_response(resp, msgs)
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def generate_with_tools(self, messages: list[Message], tools: list[dict]) -> GenerateResult:
        system, msgs = self._convert_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": 16384,
        }
        if system:
            body["system"] = system
        anthropic_tools = self._convert_tools(tools)
        if anthropic_tools:
            body["tools"] = anthropic_tools

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(
                        f"{self.base_url}/messages",
                        headers=self._headers(), json=body,
                    )
                    await self._check_response(resp, msgs)
                    return self._parse_response(resp.json())
            except (httpx.ConnectError, httpx.HTTPStatusError) as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    logger.warning("Error on generate attempt %d: %s, retrying...", attempt + 1, e)
                    await asyncio.sleep(_RETRY_DELAY)
        raise last_exc  # type: ignore[misc]

    async def stream_generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> AsyncIterator[GenerateResult | str]:
        """Stream text tokens, then yield a GenerateResult if tool_calls are present."""
        system, msgs = self._convert_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": 16384,
            "stream": True,
        }
        if system:
            body["system"] = system
        anthropic_tools = self._convert_tools(tools)
        if anthropic_tools:
            body["tools"] = anthropic_tools

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool: dict[str, Any] | None = None

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{self.base_url}/messages",
                headers=self._headers(), json=body,
            ) as resp:
                await self._check_response(resp, msgs)
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    try:
                        event = json.loads(data)
                        event_type = event.get("type", "")

                        if event_type == "content_block_start":
                            block = event.get("content_block", {})
                            if block.get("type") == "tool_use":
                                current_tool = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input_json": "",
                                }

                        elif event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    content_parts.append(text)
                                    yield text
                            elif delta.get("type") == "input_json_delta" and current_tool is not None:
                                current_tool["input_json"] += delta.get("partial_json", "")

                        elif event_type == "content_block_stop":
                            if current_tool is not None:
                                try:
                                    args = json.loads(current_tool["input_json"])
                                except (json.JSONDecodeError, TypeError):
                                    args = {}
                                tool_calls.append(ToolCall(
                                    id=current_tool["id"],
                                    function_name=current_tool["name"],
                                    arguments=args,
                                ))
                                current_tool = None

                    except (json.JSONDecodeError, KeyError):
                        continue

        if tool_calls:
            yield GenerateResult(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )

    @staticmethod
    def _parse_response(data: dict) -> GenerateResult:
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason = data.get("stop_reason", "end_turn")

        for block in data.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    function_name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        finish = "tool_calls" if tool_calls else "stop"
        return GenerateResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
        )
