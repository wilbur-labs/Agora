"""Executor engine — tool-calling loop that actually does work."""
from __future__ import annotations

import json
from typing import AsyncIterator

from agora.models.base import GenerateResult, Message, ModelProvider
from agora.tools.registry import ToolRegistry

_MAX_ITERATIONS = 20


async def run_tool_loop(
    *,
    provider: ModelProvider,
    messages: list[Message],
    tools: ToolRegistry,
    max_iterations: int = _MAX_ITERATIONS,
) -> AsyncIterator[tuple[str, str]]:
    """Run the tool-calling loop.

    Yields (event_type, content) tuples:
      ("text", "some text")           — LLM text output
      ("tool_call", "shell(cmd=ls)")  — tool being called
      ("tool_result", "file1\\nfile2") — tool output
      ("error", "something broke")    — error
      ("done", "")                    — loop finished
    """
    schemas = tools.function_schemas()
    chat: list[Message] = list(messages)

    for _ in range(max_iterations):
        result: GenerateResult = await provider.generate_with_tools(chat, schemas)

        if result.content:
            yield ("text", result.content)

        if not result.tool_calls:
            yield ("done", "")
            return

        # Build assistant message with tool_calls
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

        # Execute each tool call
        for tc in result.tool_calls:
            tool = tools.get(tc.function_name)
            call_desc = f"{tc.function_name}({', '.join(f'{k}={v!r}' for k, v in tc.arguments.items())})"
            yield ("tool_call", call_desc)

            if not tool:
                output = f"Unknown tool: {tc.function_name}"
                yield ("error", output)
            else:
                tr = await tool.execute(**tc.arguments)
                output = tr.output if tr.success else f"ERROR: {tr.error}\n{tr.output}".strip()
                yield ("tool_result", output[:2000])  # truncate for display

            # Append tool result message
            chat.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    yield ("error", f"Reached max iterations ({max_iterations})")
    yield ("done", "")
