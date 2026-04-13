"""OpenAI-compatible API provider — supports function calling (OpenAI + Azure)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .base import GenerateResult, Message, ModelProvider, ToolCall


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

    async def generate_with_tools(self, messages: list[Message], tools: list[dict]) -> GenerateResult:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(self._url(), headers=self._headers(), json=self._body(messages, tools))
            resp.raise_for_status()
            return self._parse_response(resp.json())

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
        body: dict[str, Any] = {"messages": messages}
        if tools:
            body["tools"] = tools
        return body
