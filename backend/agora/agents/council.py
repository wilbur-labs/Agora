"""Council — multi-agent discussion coordinator with clarify-first flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from agora.agents.agent import Agent
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore


@dataclass
class AgentResponse:
    agent_name: str
    role: str
    content: str


@dataclass
class Council:
    agents: list[Agent] = field(default_factory=list)
    moderator: Agent | None = None
    context: SharedContext = field(default_factory=SharedContext)
    memory: MemoryStore = field(default_factory=MemoryStore)
    user_profile: str = ""

    async def stream_discuss(self, user_input: str) -> AsyncIterator[tuple[str, str, str]]:
        """Yields (agent_name, role, chunk). Empty chunk = agent done."""
        self.context.add_user(user_input)
        mem = self.memory.get_injection_text()

        # Phase 1: Moderator checks if we need clarification
        if self.moderator:
            mod_response = ""
            async for chunk in self.moderator.stream_respond(
                self.context.get_messages(), self.user_profile, mem
            ):
                mod_response += chunk
                yield (self.moderator.name, self.moderator.role, chunk)
            yield (self.moderator.name, self.moderator.role, "")

            self.context.add_agent(self.moderator.name, mod_response)

            # If moderator asks questions, stop here — wait for user to answer
            if "PROCEED" not in mod_response.upper():
                return

        # Phase 2: Council discussion
        for agent in self.agents:
            full = ""
            try:
                async for chunk in agent.stream_respond(
                    self.context.get_messages(), self.user_profile, mem
                ):
                    full += chunk
                    yield (agent.name, agent.role, chunk)
            except Exception as e:
                full = f"[Error: {e}]"
                yield (agent.name, agent.role, full)
            yield (agent.name, agent.role, "")
            self.context.add_agent(agent.name, full)

    async def discuss(self, user_input: str) -> list[AgentResponse]:
        responses = []
        current_name = ""
        current_content = ""
        async for name, role, chunk in self.stream_discuss(user_input):
            if chunk == "":
                if current_name:
                    responses.append(AgentResponse(current_name, role, current_content))
                current_name = ""
                current_content = ""
            else:
                current_name = name
                current_content += chunk
        return responses

    def reset(self):
        self.context.clear()
