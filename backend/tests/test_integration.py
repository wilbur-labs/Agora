"""Integration tests — LLM-as-Judge validates output quality against user's question.

Run: pytest tests/test_integration.py -v
Skip: pytest -m "not integration"
"""
import os
import shutil

import pytest

from agora.api._state import get_council, reset_council
from agora.config.settings import reset_config
from agora.models.registry import get_registry, reset_registry
from tests.judge import judge_response, judge_execution, judge_discussion, judge_language

pytestmark = pytest.mark.integration


def _has_api_key() -> bool:
    reset_config()
    try:
        from agora.config.settings import get_config
        return bool(get_config().get("models", {}).get("gpt4o", {}).get("api_key", ""))
    except Exception:
        return False


def _fresh():
    reset_config(); reset_registry(); reset_council()
    return get_council()


def _get_judge_provider():
    return get_registry().get("gpt4o")


SKIP = pytest.mark.skipif(not _has_api_key(), reason="No API key")


# ── Routing ──

class TestRouting:
    @SKIP
    @pytest.mark.asyncio
    async def test_complex_question_routes_discuss(self):
        c = _fresh()
        async for _ in c.route("我想重构认证模块，从 session 迁移到 JWT，需要考虑哪些问题？"):
            pass
        assert c.last_route == "DISCUSS"

    @SKIP
    @pytest.mark.asyncio
    async def test_simple_question_routes_quick(self):
        c = _fresh()
        async for _ in c.route("Python 的 list comprehension 语法是什么？"):
            pass
        assert c.last_route == "QUICK"

    @SKIP
    @pytest.mark.asyncio
    async def test_clear_task_routes_execute(self):
        c = _fresh()
        async for _ in c.route("在 /tmp 下创建一个 test.txt 文件，内容写 hello"):
            pass
        assert c.last_route == "EXECUTE"


# ── Discussion quality (LLM-as-Judge) ──

class TestDiscussionQuality:
    @SKIP
    @pytest.mark.asyncio
    async def test_each_agent_matches_role_and_question(self):
        """LLM judges whether each agent's response fits its role AND addresses the question."""
        question = "我的 Python 项目要不要从 REST 迁移到 GraphQL？"
        c = _fresh()
        c.context.add_user(question)

        agent_contents: dict[str, tuple[str, str]] = {}  # name -> (role, text)
        current_name, current_role, current_text = "", "", ""
        async for name, role, chunk in c.stream_discuss():
            if chunk == "":
                if current_name:
                    agent_contents[current_name] = (current_role, current_text)
                current_name, current_role, current_text = "", "", ""
            else:
                current_name, current_role = name, role
                current_text += chunk

        judge = _get_judge_provider()
        for name, (role, text) in agent_contents.items():
            result = await judge_response(judge, question, name, role, text)
            assert result.passed, f"{name} ({role}) failed: score={result.score}, reason={result.reason}"
            assert result.score >= 3, f"{name} ({role}) low quality: score={result.score}, reason={result.reason}"

    @SKIP
    @pytest.mark.asyncio
    async def test_discussion_as_whole(self):
        """LLM judges the entire discussion for multi-perspective coverage."""
        question = "我们的 Go 微服务要不要引入 service mesh？团队 5 人，目前 8 个服务。"
        c = _fresh()
        c.context.add_user(question)

        agent_texts: dict[str, str] = {}
        current_name, current_text = "", ""
        async for name, role, chunk in c.stream_discuss():
            if chunk == "":
                if current_name:
                    agent_texts[current_name] = current_text
                current_name, current_text = "", ""
            else:
                current_name = name
                current_text += chunk

        judge = _get_judge_provider()
        result = await judge_discussion(judge, question, agent_texts)
        assert result.passed, f"Discussion failed: score={result.score}, reason={result.reason}"
        assert result.score >= 3, f"Discussion low quality: score={result.score}, reason={result.reason}"

    @SKIP
    @pytest.mark.asyncio
    async def test_responds_in_user_language(self):
        """Chinese question should get Chinese responses."""
        question = "请分析一下 Redis 和 Memcached 的区别"
        c = _fresh()
        c.context.add_user(question)

        all_text = ""
        async for name, role, chunk in c.stream_discuss():
            all_text += chunk

        chinese_chars = sum(1 for ch in all_text if '\u4e00' <= ch <= '\u9fff')
        assert chinese_chars > 50, f"Expected Chinese response, got only {chinese_chars} Chinese chars"


# ── Execution quality (LLM-as-Judge) ──

class TestExecutionQuality:
    @SKIP
    @pytest.mark.asyncio
    async def test_file_creation_judged(self):
        """LLM judges whether executor correctly completed a file creation task."""
        task = "在 /tmp/agora_judge_test/ 创建 config.json，内容是 {\"name\": \"agora\", \"version\": \"0.1\"}"
        c = _fresh()
        c.context.add_user(task)

        tool_events = []
        async for name, role, chunk in c.stream_execute():
            if chunk and (role in ("tool_call", "tool_result")):
                tool_events.append(f"[{role}] {chunk}")

        # Check actual file state
        path = "/tmp/agora_judge_test/config.json"
        file_exists = os.path.exists(path)
        file_content = open(path).read() if file_exists else "(file not created)"

        final_state = f"File exists: {file_exists}\nFile content: {file_content}"

        judge = _get_judge_provider()
        result = await judge_execution(judge, task, tool_events, final_state)
        assert result.passed, f"Execution failed: score={result.score}, reason={result.reason}"
        assert result.score >= 4, f"Execution low quality: score={result.score}, reason={result.reason}"

        # Also verify programmatically
        assert file_exists, "File was not created"
        assert "agora" in file_content

        # Cleanup
        if os.path.exists("/tmp/agora_judge_test/"):
            shutil.rmtree("/tmp/agora_judge_test/")

    @SKIP
    @pytest.mark.asyncio
    async def test_multi_step_execution_judged(self):
        """LLM judges a multi-step task."""
        task = "在 /tmp/agora_multi_judge/ 下创建 main.py，写一个打印 hello world 的脚本，然后运行它确认输出正确"
        c = _fresh()
        c.context.add_user(task)

        tool_events = []
        text_output = ""
        async for name, role, chunk in c.stream_execute():
            if not chunk:
                continue
            if role in ("tool_call", "tool_result"):
                tool_events.append(f"[{role}] {chunk}")
            elif role == "text":
                text_output += chunk

        path = "/tmp/agora_multi_judge/main.py"
        file_exists = os.path.exists(path)
        file_content = open(path).read() if file_exists else "(not created)"

        # Check if hello world appeared in any tool result
        has_hello = any("hello" in e.lower() for e in tool_events)

        final_state = (
            f"File exists: {file_exists}\n"
            f"File content: {file_content}\n"
            f"Hello in output: {has_hello}\n"
            f"Agent summary: {text_output[:500]}"
        )

        judge = _get_judge_provider()
        result = await judge_execution(judge, task, tool_events, final_state)
        assert result.passed, f"Execution failed: score={result.score}, reason={result.reason}"

        if os.path.exists("/tmp/agora_multi_judge/"):
            shutil.rmtree("/tmp/agora_multi_judge/")


# ── Quick answer quality (LLM-as-Judge) ──

class TestQuickQuality:
    @SKIP
    @pytest.mark.asyncio
    async def test_factual_answer_judged(self):
        """LLM judges whether a factual answer is correct and relevant."""
        question = "Python 的 GIL 是什么？对多线程有什么影响？"
        c = _fresh()
        c.context.add_user(question)

        text = ""
        agent_name, agent_role = "", ""
        async for name, role, chunk in c.stream_quick():
            agent_name, agent_role = name, role
            text += chunk

        judge = _get_judge_provider()
        result = await judge_response(judge, question, agent_name, agent_role, text)
        assert result.passed, f"Quick answer failed: score={result.score}, reason={result.reason}"
        assert result.score >= 4, f"Quick answer low quality: score={result.score}, reason={result.reason}"


# ── Language consistency (LLM-as-Judge, 3 languages) ──

class TestLanguageConsistency:
    """Verify that agents respond in the same language as the user's input."""

    async def _collect_all_agent_text(self, question: str) -> dict[str, str]:
        c = _fresh()
        c.context.add_user(question)
        agent_texts: dict[str, str] = {}
        current_name, current_text = "", ""
        async for name, role, chunk in c.stream_discuss():
            if chunk == "":
                if current_name:
                    agent_texts[current_name] = current_text
                current_name, current_text = "", ""
            else:
                current_name = name
                current_text += chunk
        return agent_texts

    @SKIP
    @pytest.mark.asyncio
    async def test_english_input_english_output(self):
        """English question → all agents respond in English."""
        agent_texts = await self._collect_all_agent_text(
            "Should I use PostgreSQL or MongoDB for a social media app with 100k users?"
        )
        judge = _get_judge_provider()
        for name, text in agent_texts.items():
            result = await judge_language(judge, "English", text)
            assert result.score >= 4, f"{name} language fail (English): score={result.score}, reason={result.reason}"

    @SKIP
    @pytest.mark.asyncio
    async def test_chinese_input_chinese_output(self):
        """Chinese question → all agents respond in Chinese."""
        agent_texts = await self._collect_all_agent_text(
            "我的电商项目应该用微服务还是单体架构？日活大概 5 万用户。"
        )
        judge = _get_judge_provider()
        for name, text in agent_texts.items():
            result = await judge_language(judge, "Chinese", text)
            assert result.score >= 4, f"{name} language fail (Chinese): score={result.score}, reason={result.reason}"

    @SKIP
    @pytest.mark.asyncio
    async def test_japanese_input_japanese_output(self):
        """Japanese question → all agents respond in Japanese."""
        agent_texts = await self._collect_all_agent_text(
            "Reactとvueのどちらを使うべきですか？チームは5人で、全員TypeScript経験があります。"
        )
        judge = _get_judge_provider()
        for name, text in agent_texts.items():
            result = await judge_language(judge, "Japanese", text)
            assert result.score >= 4, f"{name} language fail (Japanese): score={result.score}, reason={result.reason}"
