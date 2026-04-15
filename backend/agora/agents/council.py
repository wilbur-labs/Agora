"""Council — multi-agent discussion coordinator with routing, execution, and skill learning."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from agora.agents.agent import Agent
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore
from agora.models.base import ModelProvider
from agora.skills.store import SkillStore
from agora.tools.registry import ToolRegistry

Route = str  # "QUICK" | "DISCUSS" | "EXECUTE" | "CLARIFY"


@dataclass
class AgentResponse:
    agent_name: str
    role: str
    content: str


def _parse_route(response: str) -> Route:
    m = re.search(r"ROUTE:(QUICK|DISCUSS|EXECUTE)", response.upper())
    return m.group(1) if m else "CLARIFY"


@dataclass
class Council:
    agents: list[Agent] = field(default_factory=list)
    moderator: Agent | None = None
    synthesizer: Agent | None = None
    executor: Agent | None = None
    executor_provider: ModelProvider | None = None
    context: SharedContext = field(default_factory=SharedContext)
    memory: MemoryStore = field(default_factory=MemoryStore)
    skill_store: SkillStore = field(default_factory=SkillStore)
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    user_profile: str = ""
    concurrent: bool = False
    confirm_callback: Any = None  # async (tool_name, desc, dangerous) -> bool
    last_route: Route = ""
    _last_user_input: str = ""

    def _get_injections(self) -> tuple[str, str]:
        mem = self.memory.get_injection_text()
        skills = self.skill_store.get_injection_text(self._last_user_input)
        return mem, skills

    async def _get_injections_async(self) -> tuple[str, str]:
        """Async version that uses semantic skill matching when possible."""
        mem = self.memory.get_injection_text()
        provider = self.executor_provider or (self.agents[0].provider if self.agents else None)
        if provider and self._last_user_input:
            matched = await self.skill_store.match_semantic(self._last_user_input, provider)
            if matched:
                parts = []
                for s in matched[:3]:
                    lines = [f"Skill: {s.name} ({s.type})", f"Trigger: {s.trigger}"]
                    if s.success_count or s.fail_count:
                        lines.append(f"Track record: {s.success_count} successes, {s.fail_count} failures")
                    if s.steps:
                        lines.append("Steps:\n" + "\n".join(f"  - {st}" for st in s.steps))
                    if s.lessons:
                        lines.append("Lessons:\n" + "\n".join(f"  - {ls}" for ls in s.lessons))
                    parts.append("\n".join(lines))
                skills = "RELEVANT SKILLS (from past experience):\n\n" + "\n---\n".join(parts)
            else:
                skills = ""
        else:
            skills = self.skill_store.get_injection_text(self._last_user_input)
        return mem, skills

    async def _stream_agent(self, agent: Agent, mem: str, skills: str) -> AsyncIterator[tuple[str, str, str]]:
        full = ""
        try:
            async for chunk in agent.stream_respond(
                self.context.get_messages(), self.user_profile, mem, skills
            ):
                full += chunk
                yield (agent.name, agent.role, chunk)
        except Exception as e:
            full = f"[Error: {e}]"
            yield (agent.name, agent.role, full)
        yield (agent.name, agent.role, "")
        self.context.add_agent(agent.name, full)

    async def route(self, user_input: str) -> AsyncIterator[tuple[str, str, str]]:
        self._last_user_input = user_input
        self.context.add_user(user_input)
        mem, skills = self._get_injections()

        if not self.moderator:
            self.last_route = "DISCUSS"
            return

        mod_response = ""
        async for chunk in self.moderator.stream_respond(
            self.context.get_messages(), self.user_profile, mem, skills
        ):
            mod_response += chunk
            yield (self.moderator.name, self.moderator.role, chunk)
        yield (self.moderator.name, self.moderator.role, "")
        self.context.add_agent(self.moderator.name, mod_response)
        self.last_route = _parse_route(mod_response)

    async def stream_quick(self) -> AsyncIterator[tuple[str, str, str]]:
        if not self.agents:
            return
        mem, skills = self._get_injections()
        async for item in self._stream_agent(self.agents[0], mem, skills):
            yield item

    async def stream_discuss(self) -> AsyncIterator[tuple[str, str, str]]:
        mem, skills = self._get_injections()
        for agent in self.agents:
            async for item in self._stream_agent(agent, mem, skills):
                yield item
        if self.synthesizer:
            async for item in self._stream_agent(self.synthesizer, mem, skills):
                yield item

    async def stream_discuss_concurrent(self) -> AsyncIterator[tuple[str, str, str]]:
        """Concurrent discussion — all agents respond in parallel, results yielded in order."""
        import asyncio
        mem, skills = self._get_injections()

        async def _collect(agent: Agent) -> list[tuple[str, str, str]]:
            chunks: list[tuple[str, str, str]] = []
            full = ""
            try:
                async for chunk in agent.stream_respond(
                    self.context.get_messages(), self.user_profile, mem, skills
                ):
                    full += chunk
                    chunks.append((agent.name, agent.role, chunk))
            except Exception as e:
                full = f"[Error: {e}]"
                chunks.append((agent.name, agent.role, full))
            chunks.append((agent.name, agent.role, ""))
            self.context.add_agent(agent.name, full)
            return chunks

        # Run all agents concurrently
        results = await asyncio.gather(*[_collect(a) for a in self.agents])

        # Yield results in agent order
        for chunks in results:
            for item in chunks:
                yield item

        # Synthesizer runs after all agents (needs their output)
        if self.synthesizer:
            async for item in self._stream_agent(self.synthesizer, mem, skills):
                yield item

    async def stream_execute(self, task: str | None = None) -> AsyncIterator[tuple[str, str, str]]:
        """Execute using tool-calling loop if API provider available, else fallback to CLI agent."""
        if task:
            self.context.add_user(task)

        provider = self.executor_provider
        if provider and hasattr(provider, 'generate_with_tools'):
            full_text = ""
            async for event_type, content in self._tool_execute():
                # Yield with event_type prefix so API layer can emit proper SSE events
                yield ("executor", event_type, content)
                if event_type == "text":
                    full_text += content
            if full_text:
                self.context.add_agent("executor", full_text)
            yield ("executor", "agent_done", "")
        elif self.executor:
            mem, skills = self._get_injections()
            async for item in self._stream_agent(self.executor, mem, skills):
                yield item

    async def _tool_execute(self) -> AsyncIterator[tuple[str, str]]:
        """Run the tool-calling execution loop."""
        from agora.tools.executor import run_tool_loop

        # Build system prompt for executor
        mem, skills = self._get_injections()
        system_parts = [
            "You are the Executor. You execute tasks using the provided tools.",
            "Break down the task into concrete steps and execute each one.",
            "If a step fails, analyze the error and retry with a corrected approach. Do NOT give up after one failure.",
            "Do NOT add unnecessary features beyond what was asked.",
            "Verify your work — if the task asks you to create and run something, confirm the output is correct.",
        ]
        if self.user_profile:
            system_parts.append(f"<user_profile>\n{self.user_profile}\n</user_profile>")
        if mem:
            system_parts.append(f"<memory>\n{mem}\n</memory>")
        if skills:
            system_parts.append(f"<skills>\n{skills}\n</skills>")

        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        messages.extend(self.context.get_messages())

        async for event_type, content in run_tool_loop(
            provider=self.executor_provider,
            messages=messages,
            tools=self.tool_registry,
            confirm=self.confirm_callback,
        ):
            yield (event_type, content)
            # Record tool actions in context for learning
            if event_type == "text" and content:
                self.context.add_agent("executor", content)

    async def learn_skill(self, skill_type: str = "execution") -> str | None:
        if not self.skill_store.enabled:
            return None
        try:
            from agora.skills.extractor import extract_and_save
            provider = self.executor_provider or (self.executor.provider if self.executor else None)
            if not provider:
                return None
            skill = await extract_and_save(
                self.context.messages, self.skill_store, provider, skill_type=skill_type,
            )
            return skill.name if skill else None
        except Exception:
            return None

    def record_skill_outcome(self, skill_name: str, success: bool):
        self.skill_store.record_outcome(skill_name, success)

    async def discuss_and_return(self, user_input: str) -> list[AgentResponse]:
        responses = []
        current_name, current_content = "", ""
        async for name, role, chunk in self.route(user_input):
            if chunk == "":
                if current_name:
                    responses.append(AgentResponse(current_name, role, current_content))
                current_name, current_content = "", ""
            else:
                current_name, current_content = name, current_content + chunk
        if self.last_route == "DISCUSS":
            async for name, role, chunk in self.stream_discuss():
                if chunk == "":
                    if current_name:
                        responses.append(AgentResponse(current_name, role, current_content))
                    current_name, current_content = "", ""
                else:
                    current_name, current_content = name, current_content + chunk
        return responses

    def reset(self):
        self.context.clear()
        self.last_route = ""
        self._last_user_input = ""
