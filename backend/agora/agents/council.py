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


def _parse_route(response: str) -> tuple[Route, list[str]]:
    """Parse route and optional agent list from moderator response."""
    m = re.search(r"ROUTE:(QUICK|DISCUSS|EXECUTE)", response.upper())
    route = m.group(1) if m else "CLARIFY"
    agents: list[str] = []
    am = re.search(r"AGENTS:\s*([\w,]+)", response, re.IGNORECASE)
    if am:
        agents = [a.strip().lower() for a in am.group(1).split(",") if a.strip()]
    return route, agents


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
    workspace: str = ""
    last_route: Route = ""
    _last_user_input: str = ""
    _active_agents: list[str] = field(default_factory=list)  # agents selected by moderator

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
        # Track the language of the first user message in the session
        if not hasattr(self, '_session_language') or not self._session_language:
            self._session_language = user_input[:200]  # store first message for language reference
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
        self.last_route, self._active_agents = _parse_route(mod_response)

    async def stream_quick(self) -> AsyncIterator[tuple[str, str, str]]:
        if not self.agents:
            return
        mem, skills = self._get_injections()
        async for item in self._stream_agent(self.agents[0], mem, skills):
            yield item

    async def stream_discuss(self) -> AsyncIterator[tuple[str, str, str]]:
        import asyncio
        mem, skills = self._get_injections()

        # Filter agents based on moderator's selection (fallback to all)
        active = self.agents
        if self._active_agents:
            active = [a for a in self.agents if a.name in self._active_agents]
            if not active:
                active = self.agents  # fallback if no match

        # Phase 1: Scout research (serial — other agents need the results)
        remaining_agents = []
        for agent in active:
            if agent.name == "scout" and self.executor_provider and self.tool_registry.get("web_search"):
                async for item in self._research_phase(agent):
                    yield item
            elif agent.name == "scout":
                async for item in self._stream_agent(agent, mem, skills):
                    yield item
            else:
                remaining_agents.append(agent)

        # Phase 2: Remaining agents in parallel
        if remaining_agents:
            mem, skills = self._get_injections()  # refresh after scout

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

            results = await asyncio.gather(*[_collect(a) for a in remaining_agents])
            for chunks in results:
                for item in chunks:
                    yield item

        # Phase 3: Synthesizer (needs all agent output)
        if self.synthesizer:
            mem, skills = self._get_injections()
            async for item in self._stream_agent(self.synthesizer, mem, skills):
                yield item

    async def _research_phase(self, agent: Agent) -> AsyncIterator[tuple[str, str, str]]:
        """Scout uses web_search/web_fetch to gather info, then provides a brief summary.
        
        Full research details are stored in context for other agents.
        User only sees a concise summary.
        """
        from agora.tools.executor import run_tool_loop
        from agora.tools.registry import ToolRegistry

        # Build a research-only tool registry (web tools only)
        research_tools = ToolRegistry.__new__(ToolRegistry)
        research_tools._tools = {
            name: tool for name, tool in self.tool_registry._tools.items()
            if name in ("web_search", "web_fetch")
        }

        msgs = self.context.get_messages()
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        # Extract entities for targeted search
        entities = self._extract_entities(last_user)
        search_plan = ""
        if entities:
            search_plan = (
                "\n\nMANDATORY SEARCH PLAN (do these searches FIRST):\n"
                + "\n".join(f"- web_search(query='{e} GitHub') or web_search(query='{e} official site')" for e in entities)
                + "\nAfter these mandatory searches, you may do additional searches if needed.\n"
            )

        system = (
            f"You are {agent.name}, a research specialist.\n\n"
            "RESEARCH STRATEGY:\n"
            "1. Search for specific projects/technologies mentioned — find their GitHub or official pages FIRST\n"
            "2. Use web_fetch to read their README or overview\n"
            "3. Then search for comparisons, reviews, or alternatives\n\n"
            "RULES:\n"
            "- Do at most 3 web_search calls and 2 web_fetch calls\n"
            "- Focus on architecture, features, pros/cons of mentioned projects\n"
            f"{search_plan}\n"
            "OUTPUT FORMAT (strict):\n"
            "After research, output EXACTLY this format:\n"
            "---SUMMARY---\n"
            "(2-3 bullet points of key findings, max 100 words total)\n"
            "---DETAILS---\n"
            "(Full organized research notes for other team members)\n\n"
            "CRITICAL: Respond in the same language as the user's question."
        )
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": last_user},
        ]

        full_text = ""
        async for event_type, content in run_tool_loop(
            provider=self.executor_provider,
            messages=messages,
            tools=research_tools,
            max_iterations=5,
        ):
            if event_type == "text":
                full_text += content
            elif event_type == "tool_call":
                yield (agent.name, "tool_call", content)
            elif event_type == "tool_result":
                yield (agent.name, "tool_result", content)

        # Split output: show summary to user, store full details in context
        if "---SUMMARY---" in full_text and "---DETAILS---" in full_text:
            summary = full_text.split("---SUMMARY---")[1].split("---DETAILS---")[0].strip()
            details = full_text.split("---DETAILS---")[1].strip()
            # Yield only the summary to the user
            yield (agent.name, agent.role, summary)
            yield (agent.name, agent.role, "")
            # Store full details in context for other agents
            self.context.add_agent(agent.name, f"Research Summary:\n{summary}\n\nDetailed Findings:\n{details}")
        else:
            # Fallback: show everything
            yield (agent.name, agent.role, full_text)
            yield (agent.name, agent.role, "")
            if full_text:
                self.context.add_agent(agent.name, full_text)

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
        """Execute using tool-calling loop if API provider available, else fallback to CLI agent.

        When the task contains multiple action items (bullet list), each item is
        executed in its own tool-calling loop so that (1) no single LLM call is
        overwhelmingly large, (2) the frontend can show per-item progress, and
        (3) a failure in one item does not block the rest.
        """
        import asyncio
        if task:
            self.context.add_user(task)

        provider = self.executor_provider
        if provider and hasattr(provider, 'generate_with_tools'):
            items = self._parse_action_items()
            if len(items) > 1:
                # Sequential per-item execution
                for i, item in enumerate(items, 1):
                    # Rate limit protection: pause between steps
                    if i > 1:
                        await asyncio.sleep(15)
                    header = f"[{i}/{len(items)}] {item}"
                    yield ("executor", "text", f"\n### {header}\n")
                    full_text = ""
                    try:
                        async for event_type, content in self._tool_execute(override_task=item):
                            yield ("executor", event_type, content)
                            if event_type == "text":
                                full_text += content
                    except Exception as e:
                        yield ("executor", "error", f"Failed: {e}")
                    if full_text:
                        self.context.add_agent("executor", f"[{i}/{len(items)}] {item}\n{full_text}")
            else:
                # Single task — execute directly
                full_text = ""
                try:
                    async for event_type, content in self._tool_execute():
                        yield ("executor", event_type, content)
                        if event_type == "text":
                            full_text += content
                except Exception as e:
                    yield ("executor", "error", f"Failed: {e}")
                if full_text:
                    self.context.add_agent("executor", full_text)
            yield ("executor", "agent_done", "")
        elif self.executor:
            mem, skills = self._get_injections()
            async for item in self._stream_agent(self.executor, mem, skills):
                yield item

    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        """Extract project names, tools, and technologies from user input."""
        entities: list[str] = []
        seen = set()

        def _add(name: str):
            low = name.lower()
            if low not in seen and len(name) >= 2:
                seen.add(low)
                entities.append(name)

        # Quoted strings
        for m in re.finditer(r'["\u201c]([^"\u201d]+)["\u201d]', text):
            _add(m.group(1))
        # CamelCase / PascalCase (DeerFlow, FastAPI, LangChain, CrewAI)
        for m in re.finditer(r'([A-Z][a-z]+(?:[A-Z][a-zA-Z]*)+)', text):
            _add(m.group(1))
        # Hyphenated project names (hermes-agent, deer-flow)
        skip = {"built-in", "real-time", "open-source", "self-learning", "multi-agent"}
        for m in re.finditer(r'([a-zA-Z]+-[a-zA-Z]+(?:-[a-zA-Z]+)*)', text):
            if m.group(1).lower() not in skip:
                _add(m.group(1))
        # Standalone capitalized tech words surrounded by non-alpha (Redis, Memcached, Django, Python, etc.)
        for m in re.finditer(r'(?:^|[^a-zA-Z])([A-Z][a-z]{2,}(?:[A-Z][a-z]*)*)', text):
            word = m.group(1)
            if word not in ("The", "This", "That", "What", "How", "Why", "Which"):
                _add(word)
        return entities[:5]

    def _parse_action_items(self) -> list[str]:
        """Extract individual action items from the last user message."""
        msgs = self.context.get_messages()
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        items = []
        for line in last_user.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ", "• ")):
                item = stripped.lstrip("-*• ").strip()
                if item:
                    items.append(item)
        return items if len(items) > 5 else [last_user]

    async def _tool_execute(self, override_task: str | None = None) -> AsyncIterator[tuple[str, str]]:
        """Run the tool-calling execution loop.

        If *override_task* is given, only that single task is sent to the LLM
        (used by the sequential per-item executor).
        """
        from agora.tools.executor import run_tool_loop

        # Build system prompt for executor
        mem, skills = self._get_injections()
        system_parts = [
            "You are the Executor. You MUST use the provided tools to complete tasks.",
            "NEVER output code or file contents as text. ALWAYS use write_file to create files and shell to run commands.",
            "Break down the task into concrete steps and execute each one using tools.",
            "If a step fails, analyze the error and retry with a corrected approach. Do NOT give up after one failure.",
            "Do NOT add unnecessary features beyond what was asked.",
            "Verify your work — if the task asks you to create and run something, confirm the output is correct.",
            "SHELL ENVIRONMENT: The shell is /bin/sh (POSIX), NOT bash. Use '. venv/bin/activate' instead of 'source'. "
            "For long-running servers (uvicorn, flask, etc.), start them in background with '&' and use 'sleep 2 && curl ...' to verify.",
            "PORT CONFLICT: Port 8000 is already in use by this system. When starting web servers, ALWAYS use a different port (e.g. 8080, 9000). "
            "NEVER run 'pkill uvicorn' or 'kill' commands that could affect the host system.",
            "Keep your text responses concise. Focus on executing, not explaining.",
        ]
        # Enforce language consistency based on the first user message
        if hasattr(self, '_session_language') and self._session_language:
            system_parts.append(
                f"CRITICAL: Respond ENTIRELY in the same language as this original user request: \"{self._session_language}\". "
                "Do NOT switch languages even if later messages are in a different language."
            )
        if self.workspace:
            system_parts.append(f"IMPORTANT: Create all files and projects under {self.workspace}. Use absolute paths.")
        if self.user_profile:
            system_parts.append(f"<user_profile>\n{self.user_profile}\n</user_profile>")
        if mem:
            system_parts.append(f"<memory>\n{mem}\n</memory>")
        if skills:
            system_parts.append(f"<skills>\n{skills}\n</skills>")

        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]

        if override_task:
            # Only include prior context summary + the single item
            messages.append({"role": "user", "content": override_task})
        else:
            messages.extend(self.context.get_messages())

        async for event_type, content in run_tool_loop(
            provider=self.executor_provider,
            messages=messages,
            tools=self.tool_registry,
            confirm=self.confirm_callback,
        ):
            yield (event_type, content)

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
        self._session_language = ""
        self._active_agents = []
