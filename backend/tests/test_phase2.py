"""Tests for Phase 2 features — web tools, entity extraction, artifacts, workspace, multi-session."""
import asyncio
import tempfile
import os

import pytest

from agora.agents.council import Council, _parse_route
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore
from agora.skills.store import SkillStore
from agora.tools.registry import ToolRegistry
from agora.tools.web import WebSearch, WebFetch
from agora.tools.file_ops import WriteFile, ReadFile, PatchFile, ListDir, _resolve
from agora.api.artifacts import track_artifact, get_artifacts, clear_artifacts
from tests.conftest import MockProvider


# ── Entity extraction ──

class TestExtractEntities:
    def test_camelcase(self):
        entities = Council._extract_entities("比较DeerFlow和LangChain")
        assert "DeerFlow" in entities
        assert "LangChain" in entities

    def test_hyphenated(self):
        entities = Council._extract_entities("hermes-agent vs auto-gpt")
        assert "hermes-agent" in entities
        assert "auto-gpt" in entities

    def test_capitalized(self):
        entities = Council._extract_entities("Redis vs Memcached")
        assert "Redis" in entities
        assert "Memcached" in entities

    def test_skip_common_words(self):
        entities = Council._extract_entities("The best way to use multi-agent systems")
        assert "multi-agent" not in entities

    def test_empty(self):
        entities = Council._extract_entities("这是一个简单的问题")
        assert entities == []

    def test_limit(self):
        entities = Council._extract_entities("FastAPI Django Flask Tornado Sanic Starlette Uvicorn")
        assert len(entities) <= 5

    def test_mixed_chinese_english(self):
        entities = Council._extract_entities("帮我用FastAPI写一个REST接口")
        assert "FastAPI" in entities


# ── Route parsing with agents ──

class TestParseRouteWithAgents:
    def test_discuss_with_agents(self):
        route, agents = _parse_route("ROUTE:DISCUSS\nAGENTS:scout,architect\nComplex.")
        assert route == "DISCUSS"
        assert agents == ["scout", "architect"]

    def test_discuss_all_agents(self):
        route, agents = _parse_route("ROUTE:DISCUSS\nAGENTS:scout,architect,critic\nFull review.")
        assert agents == ["scout", "architect", "critic"]

    def test_quick_no_agents(self):
        route, agents = _parse_route("ROUTE:QUICK\nSimple question.")
        assert route == "QUICK"
        assert agents == []

    def test_execute_no_agents(self):
        route, agents = _parse_route("ROUTE:EXECUTE\nClear task.")
        assert route == "EXECUTE"
        assert agents == []

    def test_clarify(self):
        route, agents = _parse_route("I need more info.")
        assert route == "CLARIFY"
        assert agents == []


# ── Workspace path resolution ──

class TestWorkspaceResolve:
    def test_relative_with_workspace(self):
        p = _resolve("test.py", "/home/user/workspace")
        assert str(p) == "/home/user/workspace/test.py"

    def test_absolute_ignores_workspace(self):
        p = _resolve("/tmp/test.py", "/home/user/workspace")
        assert str(p) == "/tmp/test.py"

    def test_no_workspace(self):
        p = _resolve("test.py", "")
        assert str(p) == "test.py"

    def test_nested_relative(self):
        p = _resolve("src/main.py", "/workspace")
        assert str(p) == "/workspace/src/main.py"


# ── File ops with workspace ──

class TestFileOpsWorkspace:
    @pytest.mark.asyncio
    async def test_write_read_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clear_artifacts()
            wf = WriteFile(workspace=tmpdir)
            rf = ReadFile(workspace=tmpdir)

            result = await wf.execute(path="hello.txt", content="Hello World")
            assert result.success
            assert os.path.exists(os.path.join(tmpdir, "hello.txt"))

            result = await rf.execute(path="hello.txt")
            assert result.success
            assert "Hello World" in result.output

    @pytest.mark.asyncio
    async def test_write_tracks_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clear_artifacts()
            wf = WriteFile(workspace=tmpdir)
            await wf.execute(path="test.py", content="print('hi')")
            arts = get_artifacts()
            assert len(arts) == 1
            assert "test.py" in arts[0]

    @pytest.mark.asyncio
    async def test_patch_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wf = WriteFile(workspace=tmpdir)
            pf = PatchFile(workspace=tmpdir)
            await wf.execute(path="app.py", content="name = 'old'")
            result = await pf.execute(path="app.py", old_str="old", new_str="new")
            assert result.success
            rf = ReadFile(workspace=tmpdir)
            result = await rf.execute(path="app.py")
            assert "new" in result.output

    @pytest.mark.asyncio
    async def test_list_dir_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wf = WriteFile(workspace=tmpdir)
            ld = ListDir(workspace=tmpdir)
            await wf.execute(path="a.txt", content="a")
            await wf.execute(path="b.txt", content="b")
            result = await ld.execute(path=".")
            assert result.success
            assert "a.txt" in result.output
            assert "b.txt" in result.output


# ── Artifacts tracking ──

class TestArtifacts:
    def test_track_and_list(self):
        clear_artifacts()
        track_artifact("/tmp/test1.py")
        track_artifact("/tmp/test2.py")
        assert len(get_artifacts()) == 2

    def test_no_duplicates(self):
        clear_artifacts()
        track_artifact("/tmp/test.py")
        track_artifact("/tmp/test.py")
        assert len(get_artifacts()) == 1

    def test_clear(self):
        clear_artifacts()
        track_artifact("/tmp/test.py")
        clear_artifacts()
        assert len(get_artifacts()) == 0


# ── Web tools ──

class TestWebTools:
    @pytest.mark.asyncio
    async def test_web_search_returns_results(self):
        ws = WebSearch()
        result = await ws.execute(query="Python programming language")
        assert result.success
        assert len(result.output) > 0

    @pytest.mark.asyncio
    async def test_web_fetch_returns_content(self):
        wf = WebFetch()
        result = await wf.execute(url="https://example.com")
        assert result.success
        assert "Example Domain" in result.output

    @pytest.mark.asyncio
    async def test_web_fetch_bad_url(self):
        wf = WebFetch()
        result = await wf.execute(url="https://thisdomaindoesnotexist12345.com")
        assert not result.success


# ── Multi-session ──

class TestMultiSession:
    def test_independent_contexts(self):
        from agora.api._state import get_council, reset_council, reset_all_councils
        reset_all_councils()

        c1 = get_council("session-1")
        c2 = get_council("session-2")

        c1.context.add_user("Hello from session 1")
        c2.context.add_user("Hello from session 2")

        assert len(c1.context.get_messages()) == 1
        assert len(c2.context.get_messages()) == 1
        assert "session 1" in c1.context.get_messages()[0]["content"]
        assert "session 2" in c2.context.get_messages()[0]["content"]

        reset_all_councils()

    def test_same_session_returns_same_council(self):
        from agora.api._state import get_council, reset_all_councils
        reset_all_councils()

        c1 = get_council("test-session")
        c1.context.add_user("test")
        c2 = get_council("test-session")

        assert len(c2.context.get_messages()) == 1
        reset_all_councils()

    def test_reset_one_session(self):
        from agora.api._state import get_council, reset_council, reset_all_councils
        reset_all_councils()

        c1 = get_council("s1")
        c2 = get_council("s2")
        c1.context.add_user("msg1")
        c2.context.add_user("msg2")

        reset_council("s1")
        # s1 is gone, s2 still has its message
        c2_again = get_council("s2")
        assert len(c2_again.context.get_messages()) == 1

        reset_all_councils()


# ── Tool registry with workspace ──

class TestToolRegistryWorkspace:
    def test_registry_has_web_tools(self):
        reg = ToolRegistry()
        assert reg.get("web_search") is not None
        assert reg.get("web_fetch") is not None

    def test_registry_passes_workspace(self):
        reg = ToolRegistry(workspace="/test/workspace")
        wf = reg.get("write_file")
        assert hasattr(wf, "_workspace")
        assert wf._workspace == "/test/workspace"

    def test_registry_schema_count(self):
        reg = ToolRegistry()
        assert len(reg.function_schemas()) == 7
