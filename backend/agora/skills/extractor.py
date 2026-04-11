"""Skill extractor — extract reusable skills from successful executions."""
from __future__ import annotations

from agora.models.base import ModelProvider
from agora.skills.store import Skill, SkillStore

EXTRACT_PROMPT = """You are a skill curator. Given a conversation where a task was successfully executed, extract a reusable skill.

A skill captures: what triggered the task, what steps were taken, and what lessons were learned.

Output format (STRICT YAML):
```yaml
name: short_snake_case_name
trigger: "one sentence describing when this skill applies"
steps:
  - "step 1"
  - "step 2"
lessons:
  - "lesson learned (optional)"
```

Rules:
- Only extract if the task was actually completed successfully
- Steps should be generic enough to reuse, not project-specific
- Keep trigger broad enough to match similar future tasks
- If nothing worth extracting, output: NONE
"""


async def extract_skill(
    messages: list[dict],
    provider: ModelProvider,
) -> Skill | None:
    """Extract a skill from conversation history."""
    conv = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in messages[-12:])

    response = await provider.generate([
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": conv},
    ])

    if "NONE" in response.upper():
        return None

    # Extract YAML block
    yaml_text = response
    if "```yaml" in response:
        yaml_text = response.split("```yaml")[1].split("```")[0]
    elif "```" in response:
        yaml_text = response.split("```")[1].split("```")[0]

    try:
        return Skill.from_yaml(yaml_text)
    except Exception:
        return None


async def extract_and_save(
    messages: list[dict],
    skill_store: SkillStore,
    provider: ModelProvider,
) -> Skill | None:
    """Extract a skill and save it."""
    if not skill_store.enabled:
        return None
    skill = await extract_skill(messages, provider)
    if skill:
        skill_store.save(skill)
    return skill
