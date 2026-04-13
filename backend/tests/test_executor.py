"""Tests for the tool-calling executor loop."""
import os

import pytest

from agora.tools.executor import run_tool_loop
from agora.tools.registry import ToolRegistry
from tests.conftest import MockProvider, MockProviderWithToolSequence


@pytest.mark.asyncio
async def test_simple_text_response():
    """Provider returns text only, no tool calls."""
    provider = MockProvider(["Just a text answer."])
    tools = ToolRegistry()
    events = []
    async for event_type, content in run_tool_loop(provider=provider, messages=[], tools=tools):
        events.append((event_type, content))
    assert ("text", "Just a text answer.") in events
    assert ("done", "") in events
    assert not any(e[0] == "tool_call" for e in events)


@pytest.mark.asyncio
async def test_single_tool_call():
    """Provider calls one tool then returns text."""
    provider = MockProviderWithToolSequence(
        tool_calls=[("shell", {"command": "echo hello"})],
        final_response="Command executed.",
    )
    tools = ToolRegistry()
    events = []
    async for event_type, content in run_tool_loop(provider=provider, messages=[], tools=tools):
        events.append((event_type, content))

    types = [e[0] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "text" in types
    assert "done" in types

    # Verify tool_call content
    tool_call_event = next(e for e in events if e[0] == "tool_call")
    assert "shell" in tool_call_event[1]
    assert "echo hello" in tool_call_event[1]

    # Verify tool_result contains actual output
    tool_result_event = next(e for e in events if e[0] == "tool_result")
    assert "hello" in tool_result_event[1]


@pytest.mark.asyncio
async def test_multi_tool_sequence():
    """Provider calls multiple tools in sequence."""
    provider = MockProviderWithToolSequence(
        tool_calls=[
            ("write_file", {"path": "/tmp/agora_exec_test.txt", "content": "test123"}),
            ("read_file", {"path": "/tmp/agora_exec_test.txt"}),
        ],
        final_response="File written and verified.",
    )
    tools = ToolRegistry()
    events = []
    async for event_type, content in run_tool_loop(provider=provider, messages=[], tools=tools):
        events.append((event_type, content))

    tool_calls = [e for e in events if e[0] == "tool_call"]
    tool_results = [e for e in events if e[0] == "tool_result"]
    assert len(tool_calls) == 2
    assert len(tool_results) == 2

    # Second tool_result should contain the file content
    assert "test123" in tool_results[1][1]

    # Cleanup
    if os.path.exists("/tmp/agora_exec_test.txt"):
        os.unlink("/tmp/agora_exec_test.txt")


@pytest.mark.asyncio
async def test_unknown_tool():
    """Provider calls a tool that doesn't exist."""
    provider = MockProviderWithToolSequence(
        tool_calls=[("nonexistent_tool", {"arg": "val"})],
        final_response="Done.",
    )
    tools = ToolRegistry()
    events = []
    async for event_type, content in run_tool_loop(provider=provider, messages=[], tools=tools):
        events.append((event_type, content))

    errors = [e for e in events if e[0] == "error"]
    assert len(errors) >= 1
    assert "unknown" in errors[0][1].lower()


@pytest.mark.asyncio
async def test_max_iterations():
    """Loop should stop after max_iterations."""
    # Provider always returns tool calls, never stops
    provider = MockProviderWithToolSequence(
        tool_calls=[("shell", {"command": "echo loop"})] * 100,
        final_response="never reached",
    )
    tools = ToolRegistry()
    events = []
    async for event_type, content in run_tool_loop(
        provider=provider, messages=[], tools=tools, max_iterations=3,
    ):
        events.append((event_type, content))

    tool_calls = [e for e in events if e[0] == "tool_call"]
    assert len(tool_calls) == 3  # stopped at max
    errors = [e for e in events if e[0] == "error"]
    assert any("max iterations" in e[1].lower() for e in errors)


@pytest.mark.asyncio
async def test_messages_passed_to_provider():
    """Verify conversation history is passed correctly."""
    provider = MockProvider(["Done."])
    tools = ToolRegistry()
    messages = [
        {"role": "system", "content": "You are an executor."},
        {"role": "user", "content": "Create a file."},
    ]
    async for _ in run_tool_loop(provider=provider, messages=messages, tools=tools):
        pass
    # Provider should have received the messages
    assert len(provider.last_messages) >= 2
    assert provider.last_messages[0]["content"] == "You are an executor."
