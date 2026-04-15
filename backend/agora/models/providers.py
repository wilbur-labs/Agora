"""CLI-based model providers — Claude Code, Gemini, Kiro."""
from __future__ import annotations

import asyncio
import re

from .base import Message, ModelProvider

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _CLIProvider(ModelProvider):
    """Base for CLI subprocess providers."""

    cmd: list[str]  # set by subclass

    def _build_prompt(self, messages: list[Message]) -> str:
        parts = []
        for m in messages:
            prefix = {"system": "[System]", "user": "[User]", "assistant": "[Assistant]"}.get(m["role"], "")
            parts.append(f"{prefix}\n{m['content']}")
        return "\n\n".join(parts)

    async def generate(self, messages: list[Message]) -> str:
        chunks = []
        async for c in self.stream(messages):
            chunks.append(c)
        return "".join(chunks)

    async def stream(self, messages: list[Message]):
        prompt = self._build_prompt(messages)
        proc = await asyncio.create_subprocess_exec(
            *self.cmd, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        while True:
            chunk = await proc.stdout.read(64)
            if not chunk:
                break
            text = _ANSI_RE.sub("", chunk.decode("utf-8", errors="replace"))
            if text:
                yield text
        await proc.wait()
        if proc.returncode != 0:
            err = (await proc.stderr.read()).decode()
            if err:
                yield f"\n[Error: {_ANSI_RE.sub('', err.strip())}]"


class ClaudeCLIProvider(_CLIProvider):
    name = "claude-cli"
    cmd = ["claude", "-p", "--output-format", "text"]

    async def stream(self, messages):
        prompt = self._build_prompt(messages)
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--output-format", "text",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        while True:
            chunk = await proc.stdout.read(64)
            if not chunk:
                break
            yield chunk.decode("utf-8", errors="replace")
        await proc.wait()
        if proc.returncode != 0:
            err = (await proc.stderr.read()).decode()
            if err:
                yield f"\n[Error: {err.strip()}]"


class GeminiCLIProvider(_CLIProvider):
    name = "gemini-cli"
    cmd = ["gemini", "-p"]


class KiroCLIProvider(_CLIProvider):
    name = "kiro-cli"
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
