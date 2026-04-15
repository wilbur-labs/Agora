"""Tests for Council — routing, discussion flow, execution flow, context management."""
import tempfile
import shutil

import pytest

from agora.agents.agent import Agent
from agora.agents.council import Council, _parse_route
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore
from agora.skills.store import Skill, SkillStore
from agora.tools.registry import ToolRegistry
from tests.conftest import MockProvider, MockProviderWithToolSequence


# ── Route parsing ──

class TestParseRoute:
    def test_discuss(self):
        assert _parse_route("ROUTE:DISCUSS\nLet me think...") == "DISCUSS"

    def test_quick(self):
        assert _parse_route("ROUTE:QUICK\nSimple question.") == "QUICK"

    def test_execute(self):
        assert _parse_route("ROUTE:EXECUTE\nClear task.") == "EXECUTE"

    def test_clarify_when_no_route(self):
        assert _parse_route("I need more information. What do you mean?") == "CLARIFY"

    def test_case_insensitive(self):
        assert _parse_route("route:discuss") == "DISCUSS"

    def test_route_in_middle_of_text(self):
        assert _parse_route("Let me check... ROUTE:EXECUTE ok") == "EXECUTE"


# ── SharedContext ──

class TestSharedContext:
    def test_add_and_get(self):
        ctx = SharedContext()
        ctx.add_user("hello")
        ctx.add_agent("scout", "response")
        msgs = ctx.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert "[scout]" in msgs[1]["content"]

    def test_clear(self):
        ctx = SharedContext()
        ctx.add_user("hello")
        ctx.clear()
        assert len(ctx.get_messages()) == 0

    def test_get_returns_copy(self):
        ctx = SharedContext()
        ctx.add_user("hello")
        msgs = ctx.get_messages()
        msgs.append({"role": "user", "content": "injected"})
        assert len(ctx.get_messages()) == 1  # original unchanged


# ── Council with mock providers ──

def _make_council(
    agent_responses: dict[str, str] | None = None,
    moderator_response: str = "ROUTE:DISCUSS",
    synthesizer_response: str = "## Goal\nTest",
    skill_store: SkillStore | None = None,
) -> Council:
    """Create a Council with mock providers for testing."""
    agent_responses = agent_responses or {
        "scout": "Scout found evidence about X.",
        "architect": "Architect proposes solution Y.",
        "critic": "Critic identifies risk Z.",
    }

    agents = []
    for name, resp in agent_responses.items():
        a = Agent.__new__(Agent)
        a.name = name
        a.role = {"scout": "Researcher", "architect": "System Designer", "critic": "Quality Reviewer"}.get(name, name)
        a.perspective = ""
        a.model_name = "mock"
        a._mock_provider = MockProvider([resp])
        # Monkey-patch provider property
        type(a).provider = property(lambda self: self._mock_provider)
        agents.append(a)

    mod = Agent.__new__(Agent)
    mod.name = "moderator"
    mod.role = "Discussion Router"
    mod.perspective = ""
    mod.model_name = "mock"
    mod._mock_provider = MockProvider([moderator_response])
    type(mod).provider = property(lambda self: self._mock_provider)

    synth = Agent.__new__(Agent)
    synth.name = "synthesizer"
    synth.role = "Discussion Synthesizer"
    synth.perspective = ""
    synth.model_name = "mock"
    synth._mock_provider = MockProvider([synthesizer_response])
    type(synth).provider = property(lambda self: self._mock_provider)

    tmpdir = tempfile.mkdtemp()
    import agora.config.settings as cfg
    cfg._config = {
        "memory": {"data_dir": tmpdir, "memory_char_limit": 500, "user_char_limit": 300},
        "skills": {"enabled": True, "paths": [tmpdir]},
    }

    return Council(
        agents=agents,
        moderator=mod,
        synthesizer=synth,
        context=SharedContext(),
        memory=MemoryStore(data_dir=tmpdir),
        skill_store=skill_store or SkillStore(),
        tool_registry=ToolRegistry(),
    )


class TestCouncilRouting:
    @pytest.mark.asyncio
    async def test_route_discuss(self):
        council = _make_council(moderator_response="ROUTE:DISCUSS\nComplex question.")
        async for _ in council.route("Design a caching strategy"):
            pass
        assert council.last_route == "DISCUSS"

    @pytest.mark.asyncio
    async def test_route_quick(self):
        council = _make_council(moderator_response="ROUTE:QUICK\nSimple.")
        async for _ in council.route("What is Python?"):
            pass
        assert council.last_route == "QUICK"

    @pytest.mark.asyncio
    async def test_route_execute(self):
        council = _make_council(moderator_response="ROUTE:EXECUTE\nClear task.")
        async for _ in council.route("Add a /health endpoint"):
            pass
        assert council.last_route == "EXECUTE"

    @pytest.mark.asyncio
    async def test_route_clarify(self):
        council = _make_council(moderator_response="I need more info. What project?")
        async for _ in council.route("Fix it"):
            pass
        assert council.last_route == "CLARIFY"

    @pytest.mark.asyncio
    async def test_route_adds_to_context(self):
        council = _make_council()
        async for _ in council.route("test input"):
            pass
        msgs = council.context.get_messages()
        assert msgs[0]["content"] == "test input"
        assert "[moderator]" in msgs[1]["content"]


class TestCouncilDiscussion:
    @pytest.mark.asyncio
    async def test_all_agents_speak(self):
        council = _make_council()
        agents_seen = []
        async for name, role, chunk in council.stream_discuss():
            if chunk == "" and name not in agents_seen:
                agents_seen.append(name)
        assert "scout" in agents_seen
        assert "architect" in agents_seen
        assert "critic" in agents_seen
        assert "synthesizer" in agents_seen

    @pytest.mark.asyncio
    async def test_agents_speak_in_order(self):
        council = _make_council()
        order = []
        async for name, role, chunk in council.stream_discuss():
            if chunk == "" and name not in order:
                order.append(name)
        assert order == ["scout", "architect", "critic", "synthesizer"]

    @pytest.mark.asyncio
    async def test_context_accumulates(self):
        council = _make_council()
        council.context.add_user("test")
        async for _ in council.stream_discuss():
            pass
        msgs = council.context.get_messages()
        # user + scout + architect + critic + synthesizer = 5
        assert len(msgs) == 5
        assert "[scout]" in msgs[1]["content"]
        assert "[architect]" in msgs[2]["content"]
        assert "[critic]" in msgs[3]["content"]
        assert "[synthesizer]" in msgs[4]["content"]

    @pytest.mark.asyncio
    async def test_each_agent_has_unique_content(self):
        council = _make_council()
        council.context.add_user("test")
        contents = {}
        current_name = ""
        current_text = ""
        async for name, role, chunk in council.stream_discuss():
            if chunk == "":
                if current_name:
                    contents[current_name] = current_text
                current_name = ""
                current_text = ""
            else:
                current_name = name
                current_text += chunk
        # Each agent should have different content
        values = list(contents.values())
        assert len(set(values)) == len(values), "Agents should not all say the same thing"


class TestCouncilQuick:
    @pytest.mark.asyncio
    async def test_only_first_agent(self):
        council = _make_council()
        council.context.add_user("quick question")
        agents_seen = set()
        async for name, role, chunk in council.stream_quick():
            agents_seen.add(name)
        assert agents_seen == {"scout"}  # only first agent


class TestCouncilExecution:
    @pytest.mark.asyncio
    async def test_tool_calling_loop(self):
        council = _make_council()
        council.context.add_user("create a file")
        council.executor_provider = MockProviderWithToolSequence(
            tool_calls=[("write_file", {"path": "/tmp/agora_test_council.txt", "content": "test"})],
            final_response="File created.",
        )

        events = []
        async for name, event_type, chunk in council.stream_execute():
            if chunk:
                events.append((event_type, chunk))

        has_tool_call = any(et == "tool_call" for et, _ in events)
        has_tool_result = any(et == "tool_result" for et, _ in events)
        has_text = any("File created" in c for et, c in events if et == "text")
        assert has_tool_call, f"Expected tool_call event, got: {events}"
        assert has_tool_result, f"Expected tool_result event, got: {events}"
        assert has_text, f"Expected text with 'File created', got: {events}"

        # Cleanup
        import os
        if os.path.exists("/tmp/agora_test_council.txt"):
            os.unlink("/tmp/agora_test_council.txt")

    @pytest.mark.asyncio
    async def test_fallback_to_cli_agent(self):
        """When no executor_provider, falls back to CLI-style agent."""
        council = _make_council()
        council.executor_provider = None
        # Create a mock executor agent
        exe = Agent.__new__(Agent)
        exe.name = "executor"
        exe.role = "Task Executor"
        exe.perspective = ""
        exe.model_name = "mock"
        exe._mock_provider = MockProvider(["I would do X, Y, Z."])
        type(exe).provider = property(lambda self: self._mock_provider)
        council.executor = exe

        council.context.add_user("do something")
        text = ""
        async for name, role, chunk in council.stream_execute():
            text += chunk
        assert "X, Y, Z" in text


class TestCouncilReset:
    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        council = _make_council()
        async for _ in council.route("test"):
            pass
        assert len(council.context.messages) > 0
        assert council.last_route != ""

        council.reset()
        assert len(council.context.messages) == 0
        assert council.last_route == ""
        assert council._last_user_input == ""
