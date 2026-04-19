"""OpenAI-compatible API provider — supports function calling (OpenAI + Azure)."""
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


class OpenAIProvider(ModelProvider):
    """Standard OpenAI API."""

    name = "openai-api"

    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1", model: str = "gpt-4o"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _body(self, messages: list[Message], tools: list[dict]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        return body

    async def generate(self, messages: list[Message]) -> str:
        result = await self.generate_with_tools(messages, tools=[])
        return result.content

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        body = self._body(messages, tools=[])
        body["stream"] = True
        logger.info("Stream request body keys: %s, max_completion_tokens=%s", list(body.keys()), body.get("max_completion_tokens"))
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    async with client.stream(
                        "POST", self._url(), headers=self._headers(), json=body,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                choice = chunk["choices"][0]
                                delta = choice.get("delta", {})
                                content = delta.get("content")
                                if content:
                                    yield content
                                finish = choice.get("finish_reason")
                                if finish and finish == "length":
                                    logger.warning("Stream response truncated: finish_reason=length")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                return  # success
            except httpx.ConnectError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    logger.warning("ConnectError on stream attempt %d, retrying...", attempt + 1)
                    await asyncio.sleep(_RETRY_DELAY)
        raise last_exc  # type: ignore[misc]

    async def generate_with_tools(self, messages: list[Message], tools: list[dict]) -> GenerateResult:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(self._url(), headers=self._headers(), json=self._body(messages, tools))
                    resp.raise_for_status()
                    return self._parse_response(resp.json())
            except httpx.ConnectError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    logger.warning("ConnectError on generate attempt %d, retrying...", attempt + 1)
                    await asyncio.sleep(_RETRY_DELAY)
        raise last_exc  # type: ignore[misc]

    async def stream_generate_with_tools(
        self, messages: list[Message], tools: list[dict],
    ) -> AsyncIterator[GenerateResult | str]:
        """Stream text tokens, then yield a GenerateResult if tool_calls are present."""
        body = self._body(messages, tools)
        body["stream"] = True
        content_parts: list[str] = []
        tool_calls_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("POST", self._url(), headers=self._headers(), json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})

                        # Text content
                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            yield text

                        # Tool call deltas
                        for tc_delta in delta.get("tool_calls") or []:
                            idx = tc_delta["index"]
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": tc_delta.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                            entry = tool_calls_map[idx]
                            if tc_delta.get("id"):
                                entry["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                entry["name"] = fn["name"]
                            if fn.get("arguments"):
                                entry["arguments"] += fn["arguments"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        # If tool calls were accumulated, yield a GenerateResult
        if tool_calls_map:
            tcs: list[ToolCall] = []
            for idx in sorted(tool_calls_map):
                entry = tool_calls_map[idx]
                try:
                    args = json.loads(entry["arguments"])
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tcs.append(ToolCall(id=entry["id"], function_name=entry["name"], arguments=args))
            yield GenerateResult(
                content="".join(content_parts),
                tool_calls=tcs,
                finish_reason="tool_calls",
            )

    @staticmethod
    def _parse_response(data: dict) -> GenerateResult:
        choice = data["choices"][0]
        msg = choice["message"]
        content = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(ToolCall(id=tc["id"], function_name=fn["name"], arguments=args))
        return GenerateResult(content=content, tool_calls=tool_calls, finish_reason=choice.get("finish_reason", "stop"))


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI API — different URL format and auth header."""

    name = "azure-openai"

    def __init__(self, *, api_key: str, base_url: str, deployment: str, api_version: str = "2024-02-01"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.deployment = deployment
        self.api_version = api_version
        self.model = deployment  # Azure uses deployment name

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _url(self) -> str:
        return f"{self.base_url}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"

    def _body(self, messages: list[Message], tools: list[dict]) -> dict[str, Any]:
        body: dict[str, Any] = {"messages": messages, "max_completion_tokens": 16384}
        if tools:
            body["tools"] = tools
        return body
