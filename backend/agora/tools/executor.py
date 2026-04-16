"""Executor engine — tool-calling loop with human-in-the-loop confirmation."""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Callable, Awaitable

from agora.models.base import GenerateResult, Message, ModelProvider, ToolCall
from agora.tools.registry import ToolRegistry

_MAX_ITERATIONS = 20

# Tools that modify state — require user confirmation
_WRITE_TOOLS = {"write_file", "patch_file", "shell"}

# Shell patterns that are especially dangerous
_DANGEROUS_PATTERNS = [
    r"\brm\s+(-[rRf]+\s+|.*/)","sudo\b", r"\bchmod\b", r"\bchown\b",
    r"\bmkfs\b", r"\bdd\b", r"\b>\s*/", r"\bkill\b", r"\breboot\b",
    r"\bshutdown\b", r"\bcurl\b.*\|\s*(ba)?sh",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS))

# Callback type: receives (tool_name, description, is_dangerous) → returns True to proceed
ConfirmCallback = Callable[[str, str, bool], Awaitable[bool]]


def _is_dangerous(tool_name: str, args: dict) -> bool:
    if tool_name == "shell":
        cmd = args.get("command", "")
        return bool(_DANGEROUS_RE.search(cmd))
    return False


async def run_tool_loop(
    *,
    provider: ModelProvider,
    messages: list[Message],
    tools: ToolRegistry,
    max_iterations: int = _MAX_ITERATIONS,
    confirm: ConfirmCallback | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Run the tool-calling loop.

    Yields (event_type, content) tuples:
      ("text", "...")           — LLM text output
      ("tool_call", "...")      — tool being called
      ("tool_result", "...")    — tool output
      ("tool_skipped", "...")   — user rejected the tool call
      ("error", "...")          — error
      ("done", "")              — loop finished
    """
    schemas = tools.function_schemas()
    chat: list[Message] = list(messages)

    for _ in range(max_iterations):
        # Use streaming if available for real-time text output
        result: GenerateResult | None = None
        if hasattr(provider, 'stream_generate_with_tools'):
            async for item in provider.stream_generate_with_tools(chat, schemas):
                if isinstance(item, str):
                    yield ("text", item)
                elif isinstance(item, GenerateResult):
                    result = item
            if result is None:
                # Stream ended with no GenerateResult — text-only response
                yield ("done", "")
                return
            if not result.tool_calls:
                yield ("done", "")
                return
        else:
            result = await provider.generate_with_tools(chat, schemas)
            if result.content:
                yield ("text", result.content)
            if not result.tool_calls:
                yield ("done", "")
                return

        assistant_msg: Message = {"role": "assistant", "content": result.content or ""}
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function_name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in result.tool_calls
        ]
        chat.append(assistant_msg)

        for tc in result.tool_calls:
            tool = tools.get(tc.function_name)
            call_desc = f"{tc.function_name}({', '.join(f'{k}={v!r}' for k, v in tc.arguments.items())})"
            dangerous = _is_dangerous(tc.function_name, tc.arguments)

            yield ("tool_call", call_desc)

            # Ask for confirmation on write operations
            if tc.function_name in _WRITE_TOOLS and confirm:
                # Check if auto-approve is enabled
                is_auto = getattr(confirm, 'is_auto_approve', lambda: False)()
                if not is_auto:
                    yield ("confirm", json.dumps({"tool": tc.function_name, "desc": call_desc, "dangerous": dangerous}))
                approved = await confirm(tc.function_name, call_desc, dangerous)
                if not approved:
                    output = "User rejected this operation."
                    yield ("tool_skipped", call_desc)
                    chat.append({"role": "tool", "tool_call_id": tc.id, "content": output})
                    continue
            elif tc.function_name in _WRITE_TOOLS:
                yield ("confirm", json.dumps({"tool": tc.function_name, "desc": call_desc, "dangerous": dangerous}))

            if not tool:
                output = f"Unknown tool: {tc.function_name}"
                yield ("error", output)
            else:
                tr = await tool.execute(**tc.arguments)
                output = tr.output if tr.success else f"ERROR: {tr.error}\n{tr.output}".strip()
                yield ("tool_result", output[:2000])

            chat.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    yield ("error", f"Reached max iterations ({max_iterations})")
    yield ("done", "")
