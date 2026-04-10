"""Agora CLI — multi-perspective AI council."""
from __future__ import annotations

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML
from pathlib import Path

from agora.api._state import get_council, save_user_profile
from agora.memory.extractor import extract_and_store

C = {"scout": "\033[36m", "architect": "\033[32m", "critic": "\033[33m", "sentinel": "\033[31m", "moderator": "\033[35m"}
B, D, R = "\033[1m", "\033[2m", "\033[0m"

COMMANDS = ["/agents", "/reset", "/memory", "/profile", "/quit", "/help"]


def banner():
    print(f"""
{B}🏛  Agora{R} — Multi-Perspective AI Council
Discuss, design, execute, evolve.
Type /help for commands.
""")


async def command(cmd: str) -> bool:
    council = get_council()
    parts = cmd.split(maxsplit=3)

    if parts[0] == "/quit":
        print("Goodbye.")
        return False
    elif parts[0] == "/agents":
        for a in council.agents:
            print(f"  {C.get(a.name, '')}● {a.name}{R} ({a.role}) → {a.model_name}")
    elif parts[0] == "/reset":
        council.reset()
        print(f"{D}Context cleared.{R}")
    elif parts[0] == "/memory":
        print(council.memory.get_injection_text() or f"{D}(empty){R}")
    elif parts[0] == "/profile":
        if len(parts) >= 4 and parts[1] == "set":
            save_user_profile(parts[2], parts[3])
            from agora.api._state import _load_user_profile
            council.user_profile = _load_user_profile()
            print(f"{D}Profile updated: {parts[2]}{R}")
        else:
            print(council.user_profile or f"{D}(empty — use: /profile set <key> <value>){R}")
    elif parts[0] == "/help":
        print(f"""
  {B}/agents{R}                    List council agents
  {B}/reset{R}                     Clear conversation context
  {B}/memory{R}                    View persistent memory
  {B}/profile{R}                   View user profile
  {B}/profile set <key> <val>{R}   Update profile field
  {B}/quit{R}                      Exit
""")
    else:
        print(f"{D}Unknown command: {cmd}. Type /help{R}")
    return True


async def discuss(user_input: str):
    council = get_council()
    current = ""
    async for name, role, chunk in council.stream_discuss(user_input):
        if name != current:
            if current:
                print()
            current = name
            print(f"\n{B}{C.get(name, '')}◆ {name}{R} {D}({role}){R}")
        if chunk:
            print(chunk, end="", flush=True)
    print("\n")

    # Auto-extract memories after discussion (if agents actually responded)
    if len(council.context.messages) >= 3:
        try:
            provider = council.agents[0].provider  # reuse first agent's model
            stored = await extract_and_store(council.context.messages, council.memory, provider)
            if stored:
                print(f"{D}📝 Remembered {len(stored)} facts:{R}")
                for s in stored:
                    print(f"  {D}{s}{R}")
                print()
        except Exception:
            pass  # memory extraction is best-effort


async def main():
    banner()
    council = get_council()
    names = " ".join(f"{C.get(a.name, '')}{a.name}{R}" for a in council.agents)
    print(f"{D}Council:{R} {names}")

    # Show memory if any
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

    while True:
        try:
            text = await session.prompt_async(HTML("<b>You: </b>"))
            text = text.strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if not text:
            continue
        if text.startswith("/"):
            if not await command(text):
                break
            continue
        await discuss(text)


def cli_main():
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
