"""Agora CLI — multi-perspective AI council."""
from __future__ import annotations

import asyncio
import unicodedata

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML
from pathlib import Path

from agora.api._state import get_council, save_user_profile
from agora.memory.extractor import extract_and_store

C = {
    "scout": "\033[36m", "architect": "\033[32m", "critic": "\033[33m",
    "sentinel": "\033[31m", "moderator": "\033[35m", "synthesizer": "\033[94m",
    "executor": "\033[95m",
}
B, D, R = "\033[1m", "\033[2m", "\033[0m"

COMMANDS = ["/agents", "/reset", "/memory", "/skills", "/profile", "/ask", "/exec", "/quit", "/help"]

ROUTE_LABELS = {"DISCUSS": "discuss", "EXECUTE": "execute", "QUICK": "quick"}


def _safe(text: str) -> str:
    """Replace characters that may render as garbled in some terminals."""
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def banner():
    print(f"""
{B}🏛  Agora{R} — Multi-Perspective AI Council
Discuss, design, execute, evolve.
Type /help for commands.
""")


def _print_agent(name: str, role: str, chunk: str, current: list):
    """Print streaming agent output with headers."""
    if name != current[0]:
        if current[0]:
            print()
        current[0] = name
        print(f"\n{B}{C.get(name, '')}◆ {name}{R} {D}({role}){R}")
    if not chunk:
        return
    chunk = _safe(chunk)
    # Format tool-calling events
    if chunk.startswith("[tool_call] "):
        print(f"\n  {D}🔧 {chunk[12:]}{R}")
    elif chunk.startswith("[tool_result] "):
        result = chunk[14:]
        if len(result) > 200:
            result = result[:200] + "..."
        print(f"  {D}   → {result}{R}")
    elif chunk.startswith("[error] "):
        print(f"  \033[31m✗ {chunk[8:]}{R}")
    elif chunk.startswith("[tool_skipped] "):
        print(f"  {D}   ⏭ skipped by user{R}")
    elif chunk.startswith("[done] "):
        pass
    else:
        print(chunk, end="", flush=True)


async def _extract_memories():
    """Best-effort memory extraction after discussion."""
    council = get_council()
    if len(council.context.messages) < 3:
        return
    try:
        provider = council.agents[0].provider
        stored = await extract_and_store(council.context.messages, council.memory, provider)
        if stored:
            print(f"{D}📝 Remembered {len(stored)} facts:{R}")
            for s in stored:
                print(f"  {D}{s}{R}")
            print()
    except Exception:
        pass


async def _learn_skill():
    """Best-effort skill extraction after execution."""
    council = get_council()
    try:
        skill_name = await council.learn_skill("execution")
        if skill_name:
            print(f"{D}🧠 Learned execution skill: {skill_name}{R}\n")
    except Exception:
        pass


async def _learn_discussion_skill():
    """Best-effort skill extraction after discussion."""
    council = get_council()
    try:
        skill_name = await council.learn_skill("discussion")
        if skill_name:
            print(f"{D}🧠 Learned discussion skill: {skill_name}{R}\n")
    except Exception:
        pass


_cli_session: PromptSession | None = None


async def _confirm_tool(tool_name: str, description: str, dangerous: bool) -> bool:
    """Ask user to confirm a tool call. Returns True to proceed."""
    if dangerous:
        label = f"\033[31m⚠ DANGEROUS\033[0m {D}{description}{R}"
    else:
        label = f"{D}{description}{R}"
    print(f"\n  {label}")
    if _cli_session:
        try:
            choice = await _cli_session.prompt_async(HTML("  <b>Allow? [Y/n/skip-all]: </b>"))
            choice = choice.strip().lower()
            if choice in ("n", "no"):
                return False
            if choice in ("skip-all", "s"):
                # Disable confirmation for rest of this session
                get_council().confirm_callback = None
                return True
        except (EOFError, KeyboardInterrupt):
            return False
    return True


async def _prompt_choice(session: PromptSession, options: str) -> str:
    """Prompt user for a choice."""
    try:
        choice = await session.prompt_async(HTML(f"<b>{options}: </b>"))
        return choice.strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


async def handle_input(user_input: str, session: PromptSession, force_mode: str = ""):
    """Main input handler with routing."""
    council = get_council()
    current = [""]

    if force_mode:
        # User forced a mode via /ask or /exec
        council.context.add_user(user_input)
        route = force_mode
    else:
        # Let moderator route
        mod_text = ""
        async for name, role, chunk in council.route(user_input):
            mod_text += chunk
        route = council.last_route

        if route == "CLARIFY":
            # Show moderator's clarification questions
            print(f"\n{B}{C.get('moderator', '')}◆ moderator{R} {D}(needs clarification){R}")
            # Strip ROUTE: prefix if present
            display = "\n".join(
                line for line in mod_text.strip().splitlines()
                if not line.strip().upper().startswith("ROUTE:")
            ).strip()
            if display:
                print(_safe(display))
            print()
            return

        # Show moderator's brief explanation (without ROUTE: line)
        display = "\n".join(
            line for line in mod_text.strip().splitlines()
            if not line.strip().upper().startswith("ROUTE:")
        ).strip()
        if display:
            print(f"\n{B}{C.get('moderator', '')}◆ moderator{R} {D}({route.lower()}){R}")
            print(f"{D}{_safe(display)}{R}\n")

        # Ask user to confirm or override
        suggested = ROUTE_LABELS.get(route, "discuss")
        choice = await _prompt_choice(
            session, f"[D]iscuss  [E]xecute  [Q]uick  (enter={suggested})"
        )
        if choice in ("d", "discuss"):
            route = "DISCUSS"
        elif choice in ("e", "exec", "execute"):
            route = "EXECUTE"
        elif choice in ("q", "quick"):
            route = "QUICK"
        elif choice == "":
            pass  # use moderator's suggestion

    # Execute the chosen route
    current = [""]
    if route == "QUICK":
        async for name, role, chunk in council.stream_quick():
            _print_agent(name, role, chunk, current)
        print("\n")

    elif route == "DISCUSS":
        discuss_fn = council.stream_discuss_concurrent if council.concurrent else council.stream_discuss
        async for name, role, chunk in discuss_fn():
            _print_agent(name, role, chunk, current)
        print("\n")

        # Learn from discussion
        await _learn_discussion_skill()

        # After discussion, offer to execute
        choice = await _prompt_choice(session, "Execute action items? [Y/n]")
        if choice in ("y", "yes", ""):
            current = [""]
            async for name, role, chunk in council.stream_execute():
                _print_agent(name, role, chunk, current)
            print("\n")
            await _learn_skill()

        await _extract_memories()

    elif route == "EXECUTE":
        async for name, role, chunk in council.stream_execute():
            _print_agent(name, role, chunk, current)
        print("\n")
        await _learn_skill()


async def command(cmd: str, session: PromptSession) -> bool:
    council = get_council()
    parts = cmd.split(maxsplit=3)

    if parts[0] == "/quit":
        print("Goodbye.")
        return False
    elif parts[0] == "/agents":
        for a in council.agents:
            print(f"  {C.get(a.name, '')}● {a.name}{R} ({a.role}) → {a.model_name}")
        if council.executor:
            print(f"  {C.get('executor', '')}● executor{R} ({council.executor.role}) → {council.executor.model_name}")
    elif parts[0] == "/reset":
        council.reset()
        print(f"{D}Context cleared.{R}")
    elif parts[0] == "/memory":
        print(council.memory.get_injection_text() or f"{D}(empty){R}")
    elif parts[0] == "/skills":
        skills = council.skill_store.skills
        if skills:
            for s in skills:
                print(f"  {B}🧠 {s.name}{R} — {s.trigger}")
        else:
            print(f"{D}(no skills learned yet){R}")
    elif parts[0] == "/profile":
        if len(parts) >= 4 and parts[1] == "set":
            save_user_profile(parts[2], parts[3])
            from agora.api._state import _load_user_profile
            council.user_profile = _load_user_profile()
            print(f"{D}Profile updated: {parts[2]}{R}")
        else:
            print(council.user_profile or f"{D}(empty — use: /profile set <key> <value>){R}")
    elif parts[0] == "/ask":
        text = cmd[len("/ask"):].strip()
        if text:
            await handle_input(text, session, force_mode="QUICK")
        else:
            print(f"{D}Usage: /ask <question>{R}")
    elif parts[0] == "/exec":
        text = cmd[len("/exec"):].strip()
        if text:
            await handle_input(text, session, force_mode="EXECUTE")
        else:
            print(f"{D}Usage: /exec <task>{R}")
    elif parts[0] == "/help":
        print(f"""
  {B}/agents{R}                    List council agents
  {B}/ask <question>{R}            Quick answer (skip discussion)
  {B}/exec <task>{R}               Direct execution (skip discussion)
  {B}/skills{R}                    List learned skills
  {B}/reset{R}                     Clear conversation context
  {B}/memory{R}                    View persistent memory
  {B}/profile{R}                   View user profile
  {B}/profile set <key> <val>{R}   Update profile field
  {B}/quit{R}                      Exit
""")
    else:
        print(f"{D}Unknown command: {cmd}. Type /help{R}")
    return True


async def main():
    global _cli_session
    banner()
    council = get_council()
    names = " ".join(f"{C.get(a.name, '')}{a.name}{R}" for a in council.agents)
    print(f"{D}Council:{R} {names}")

    mem = council.memory.get_injection_text()
    if mem:
        print(f"{D}📝 Memory loaded ({len(mem)} chars){R}")
    print()

    history_path = Path(council.memory.data_dir) / ".input_history"
    session: PromptSession = PromptSession(
        history=FileHistory(str(history_path)),
        completer=WordCompleter(COMMANDS, sentence=True),
        multiline=False,
    )
    _cli_session = session
    council.confirm_callback = _confirm_tool

    while True:
        try:
            text = await session.prompt_async(HTML("<b>You: </b>"))
            text = text.strip()
        except EOFError:
            print("\nGoodbye.")
            break
        except KeyboardInterrupt:
            print()  # newline after ^C
            continue  # first Ctrl+C just cancels current input
        if not text:
            continue
        if text.startswith("/"):
            if not await command(text, session):
                break
            continue
        try:
            await handle_input(text, session)
        except KeyboardInterrupt:
            print(f"\n{D}Interrupted. Type /quit to exit.{R}\n")


def cli_main():
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
