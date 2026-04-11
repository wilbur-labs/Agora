"""Council — multi-agent discussion coordinator with routing, execution, and skill learning."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from agora.agents.agent import Agent
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore
from agora.skills.store import SkillStore

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
    context: SharedContext = field(default_factory=SharedContext)
    memory: MemoryStore = field(default_factory=MemoryStore)
    skill_store: SkillStore = field(default_factory=SkillStore)
    user_profile: str = ""
    last_route: Route = ""
    _last_user_input: str = ""

    def _get_injections(self) -> tuple[str, str]:
        """Return (memory_text, skills_text) for prompt injection."""
        mem = self.memory.get_injection_text()
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

    async def stream_execute(self, task: str | None = None) -> AsyncIterator[tuple[str, str, str]]:
        if not self.executor:
            return
        if task:
            self.context.add_user(task)
        mem, skills = self._get_injections()
        async for item in self._stream_agent(self.executor, mem, skills):
            yield item

    async def learn_skill(self) -> str | None:
        """Extract and save a skill from the current conversation. Returns skill name or None."""
        if not self.executor or not self.skill_store.enabled:
            return None
        try:
            from agora.skills.extractor import extract_and_save
            provider = self.executor.provider
            skill = await extract_and_save(self.context.messages, self.skill_store, provider)
            return skill.name if skill else None
        except Exception:
            return None

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
