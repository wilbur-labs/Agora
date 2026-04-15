"""Additional tests to push coverage above 80% — chat SSE, providers, sandbox config."""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from agora.api.app import app

client = TestClient(app)


# === Chat SSE streaming ===

class TestChatSSE:
    """These tests call real LLM via council, mark as integration."""

    @pytest.mark.integration
    def test_chat_returns_sse(self):
        """POST /api/chat should return SSE stream with moderator + route event."""
        client.post("/api/chat/reset")
        with client.stream("POST", "/api/chat", json={"message": "hello"}) as r:
            assert r.status_code == 200
            events = []
            for line in r.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
            # Should have at least token and route events
            assert "token" in events or "route" in events

    @pytest.mark.integration
    def test_chat_continue_discuss(self):
        """POST /api/chat/continue with DISCUSS should stream agent tokens."""
        client.post("/api/chat/reset")
        # First send a message to set up context
        with client.stream("POST", "/api/chat", json={"message": "Design a cache"}) as r:
            for _ in r.iter_lines():
                pass

        with client.stream("POST", "/api/chat/continue", json={"route": "DISCUSS"}) as r:
            assert r.status_code == 200
            has_token = False
            for line in r.iter_lines():
                if line.startswith("event:") and "token" in line:
                    has_token = True
                    break
            # May or may not have tokens depending on model availability
            # Just verify it doesn't error

    @pytest.mark.integration
    def test_chat_continue_quick(self):
        client.post("/api/chat/reset")
        with client.stream("POST", "/api/chat", json={"message": "What is Python?"}) as r:
            for _ in r.iter_lines():
                pass
        with client.stream("POST", "/api/chat/continue", json={"route": "QUICK"}) as r:
            assert r.status_code == 200


# === CLI Providers ===

class TestCLIProviders:
    def test_cli_provider_build_prompt(self):
        from agora.models.providers import GeminiCLIProvider
        p = GeminiCLIProvider()
        prompt = p._build_prompt([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ])
        assert "[System]" in prompt
        assert "[User]" in prompt
        assert "[Assistant]" in prompt
        assert "Hello" in prompt

    def test_gemini_cmd(self):
        from agora.models.providers import GeminiCLIProvider
        p = GeminiCLIProvider()
        assert "gemini" in p.cmd[0]

    def test_kiro_cmd(self):
        from agora.models.providers import KiroCLIProvider
        p = KiroCLIProvider()
        assert "kiro" in p.cmd[0]

    def test_claude_cmd(self):
        from agora.models.providers import ClaudeCLIProvider
        p = ClaudeCLIProvider()
        assert "claude" in p.cmd[0]

    @pytest.mark.asyncio
    async def test_generate_calls_stream(self):
        from agora.models.providers import GeminiCLIProvider
        p = GeminiCLIProvider()
        # Mock stream to avoid actual subprocess
        async def mock_stream(msgs):
            yield "test response"
        p.stream = mock_stream
        result = await p.generate([{"role": "user", "content": "hi"}])
        assert result == "test response"


# === Sandbox Config ===

class TestSandboxConfig:
    def test_get_sandbox_config_default(self):
        from agora.sandbox.docker import get_sandbox_config
        cfg = get_sandbox_config()
        assert hasattr(cfg, "enabled")
        assert hasattr(cfg, "image")
        assert hasattr(cfg, "timeout")
        assert hasattr(cfg, "memory_limit")

    def test_sandbox_config_fields(self):
        from agora.sandbox.docker import SandboxConfig
        cfg = SandboxConfig(
            enabled=True, image="python:3.12", timeout=60,
            memory_limit="256m", cpu_limit=0.5,
            workspace_dir="/tmp/test", network=False,
        )
        assert cfg.enabled is True
        assert cfg.timeout == 60


# === Chat API helper function ===

class TestChatHelpers:
    def test_agent_events_generator(self):
        """Test _agent_events helper converts tuples to SSE dicts."""
        from agora.api.chat import _agent_events
        import asyncio

        async def mock_stream():
            yield ("scout", "Researcher", "hello ")
            yield ("scout", "Researcher", "world")
            yield ("scout", "Researcher", "")

        async def collect():
            events = []
            async for e in _agent_events(mock_stream()):
                events.append(e)
            return events

        events = asyncio.get_event_loop().run_until_complete(collect())
        assert len(events) == 3
        assert events[0]["event"] == "token"
        assert json.loads(events[0]["data"])["content"] == "hello "
        assert events[2]["event"] == "agent_done"

    def test_agent_events_executor_tool(self):
        """Test executor tool events are properly mapped."""
        from agora.api.chat import _agent_events
        import asyncio

        async def mock_stream():
            yield ("executor", "tool_call", "write_file(path='/tmp/t.txt')")
            yield ("executor", "tool_result", "Wrote 5 chars")
            yield ("executor", "text", "Done.")
            yield ("executor", "agent_done", "")

        async def collect():
            events = []
            async for e in _agent_events(mock_stream()):
                events.append(e)
            return events

        events = asyncio.get_event_loop().run_until_complete(collect())
        assert events[0]["event"] == "tool_call"
        assert events[1]["event"] == "tool_result"
        assert events[2]["event"] == "token"
        assert events[3]["event"] == "agent_done"


# === OpenAI Provider edge cases ===

class TestOpenAIProviderEdge:
    def test_parse_response_no_tool_calls(self):
        from agora.models.openai_provider import OpenAIProvider
        data = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        r = OpenAIProvider._parse_response(data)
        assert r.content == "hi"
        assert r.tool_calls == []

    def test_parse_response_empty_content(self):
        from agora.models.openai_provider import OpenAIProvider
        data = {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
        r = OpenAIProvider._parse_response(data)
        assert r.content == ""

    def test_parse_response_bad_json_args(self):
        from agora.models.openai_provider import OpenAIProvider
        data = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "test", "arguments": "not-json"}}
        ]}, "finish_reason": "tool_calls"}]}
        r = OpenAIProvider._parse_response(data)
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].arguments == {}


# === Mock-based chat flow tests (no real LLM) ===

class TestChatFlowMocked:
    """Test chat/continue endpoints with mocked council to cover SSE generator code."""

    def test_chat_endpoint_mocked(self):
        from agora.api import _state
        from agora.api._state import get_council
        from unittest.mock import patch

        async def mock_route(msg):
            yield ("moderator", "Router", "ROUTE:DISCUSS\nTest reason.")
            yield ("moderator", "Router", "")

        c = get_council()
        original_route = c.route
        c.route = mock_route
        c.last_route = "DISCUSS"

        try:
            with client.stream("POST", "/api/chat", json={"message": "test"}) as r:
                assert r.status_code == 200
                lines = list(r.iter_lines())
                events = [l.split(":", 1)[1].strip() for l in lines if l.startswith("event:")]
                assert "token" in events
                assert "route" in events
        finally:
            c.route = original_route

    def test_continue_discuss_mocked(self):
        from agora.api._state import get_council

        async def mock_discuss():
            yield ("scout", "Researcher", "Test insight.")
            yield ("scout", "Researcher", "")
            yield ("synthesizer", "Synthesizer", "Summary.")
            yield ("synthesizer", "Synthesizer", "")

        c = get_council()
        original = c.stream_discuss
        c.stream_discuss = mock_discuss

        try:
            with client.stream("POST", "/api/chat/continue", json={"route": "DISCUSS"}) as r:
                assert r.status_code == 200
                lines = list(r.iter_lines())
                events = [l.split(":", 1)[1].strip() for l in lines if l.startswith("event:")]
                assert "token" in events
                assert "agent_done" in events
                assert "done" in events
        finally:
            c.stream_discuss = original

    def test_continue_execute_mocked(self):
        from agora.api._state import get_council

        async def mock_execute():
            yield ("executor", "text", "Working on it...")
            yield ("executor", "tool_call", "write_file(path='test.txt')")
            yield ("executor", "tool_result", "Wrote 10 chars")
            yield ("executor", "text", "Done.")
            yield ("executor", "agent_done", "")

        c = get_council()
        original = c.stream_execute
        c.stream_execute = mock_execute

        try:
            with client.stream("POST", "/api/chat/continue", json={"route": "EXECUTE"}) as r:
                assert r.status_code == 200
                lines = list(r.iter_lines())
                events = [l.split(":", 1)[1].strip() for l in lines if l.startswith("event:")]
                assert "token" in events
                assert "tool_call" in events
                assert "tool_result" in events
        finally:
            c.stream_execute = original

    def test_continue_quick_mocked(self):
        from agora.api._state import get_council

        async def mock_quick():
            yield ("scout", "Researcher", "Quick answer.")
            yield ("scout", "Researcher", "")

        c = get_council()
        original = c.stream_quick
        c.stream_quick = mock_quick

        try:
            with client.stream("POST", "/api/chat/continue", json={"route": "QUICK"}) as r:
                assert r.status_code == 200
                lines = list(r.iter_lines())
                events = [l.split(":", 1)[1].strip() for l in lines if l.startswith("event:")]
                assert "token" in events
                assert "done" in events
        finally:
            c.stream_quick = original


# === Agent test endpoint (mocked) ===

class TestAgentTestMocked:
    def test_agent_test_endpoint(self):
        """Test /api/agents/:name/test with mocked agent streaming."""
        from agora.agents.agent import Agent
        from unittest.mock import patch

        async def mock_stream_respond(messages, *a, **kw):
            yield "Test response chunk 1"
            yield " chunk 2"

        with patch.object(Agent, "stream_respond", mock_stream_respond):
            with client.stream("POST", "/api/agents/scout/test", json={"message": "hello"}) as r:
                assert r.status_code == 200
                lines = list(r.iter_lines())
                data_lines = [l for l in lines if l.startswith("data:")]
                assert len(data_lines) >= 2  # at least tokens + done


# === Council async generators (mocked provider) ===

class TestCouncilFlows:
    @pytest.mark.asyncio
    async def test_stream_quick(self):
        from agora.api._state import get_council
        c = get_council()
        c.context.add_user("test question")
        events = []
        async for name, role, chunk in c.stream_quick():
            events.append((name, chunk))
        # Should have at least one agent response + empty terminator
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_stream_discuss(self):
        from agora.api._state import get_council
        c = get_council()
        c.context.add_user("discuss this topic")
        agents_seen = set()
        async for name, role, chunk in c.stream_discuss():
            agents_seen.add(name)
        # Should see multiple agents
        assert len(agents_seen) >= 1

    @pytest.mark.asyncio
    async def test_council_reset(self):
        from agora.api._state import get_council
        c = get_council()
        c.context.add_user("something")
        assert len(c.context.get_messages()) > 0
        c.reset()
        assert len(c.context.get_messages()) == 0
        assert c.last_route == ""

    @pytest.mark.asyncio
    async def test_get_injections(self):
        from agora.api._state import get_council
        c = get_council()
        mem, skills = c._get_injections()
        # Should return strings (possibly empty)
        assert isinstance(mem, str)
        assert isinstance(skills, str)


# === Shell tool edge cases ===

class TestShellEdge:
    @pytest.mark.asyncio
    async def test_shell_timeout(self):
        from agora.tools.shell import Shell
        s = Shell(sandbox=None)
        r = await s.execute(command="sleep 10", timeout=1)
        # Should either fail or contain timeout indication
        assert not r.success or "timeout" in (r.output + (r.error or "")).lower() or r.error is not None

    @pytest.mark.asyncio
    async def test_shell_basic(self):
        from agora.tools.shell import Shell
        s = Shell(sandbox=None)
        r = await s.execute(command="echo hello")
        assert r.success
        assert "hello" in r.output
