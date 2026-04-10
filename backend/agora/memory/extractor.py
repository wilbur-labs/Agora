"""Memory extractor — after discussion, extract facts worth remembering."""
from __future__ import annotations

from agora.memory.store import MemoryStore
from agora.models.base import ModelProvider

EXTRACT_PROMPT = """You are a memory curator. Given a conversation between a user and AI agents, extract information worth remembering for future sessions.

Extract ONLY:
- User preferences and working style
- Project facts (models used, hardware, goals)
- Decisions made
- Technical constraints discovered

Output format — one fact per line, prefix with target:
memory: <fact about projects/environment/decisions>
user: <fact about user preferences/style>

If nothing worth remembering, output: NONE

Keep each fact under 100 characters. Max 5 facts per conversation.
Write facts in the same language the user used."""


async def extract_and_store(
    messages: list[dict],
    memory: MemoryStore,
    provider: ModelProvider,
) -> list[str]:
    """Extract memorable facts from conversation and store them."""
    # Build conversation summary for extraction
    conv = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in messages[-10:])

    response = await provider.generate([
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": conv},
    ])

    if "NONE" in response.upper():
        return []

    stored = []
    for line in response.strip().splitlines():
        line = line.strip()
        if line.startswith("memory:"):
            fact = line[7:].strip()
            ok, _ = memory.add("memory", fact)
            if ok:
                stored.append(f"[memory] {fact}")
        elif line.startswith("user:"):
            fact = line[5:].strip()
            ok, _ = memory.add("user", fact)
            if ok:
                stored.append(f"[user] {fact}")

    return stored
