"""Skill extractor — extract reusable skills from executions and discussions."""
from __future__ import annotations

from agora.models.base import ModelProvider
from agora.skills.store import Skill, SkillStore

_EXEC_PROMPT = """You are a skill curator. Given a conversation where a task was executed, extract a reusable skill.

Output format (STRICT YAML):
```yaml
name: short_snake_case_name
trigger: "one sentence describing when this skill applies"
steps:
  - "step 1"
  - "step 2"
lessons:
  - "lesson learned"
```

Rules:
- Steps should be generic enough to reuse, not project-specific
- Keep trigger broad enough to match similar future tasks
- If nothing worth extracting, output: NONE"""

_DISCUSS_PROMPT = """You are a skill curator. Given a multi-agent council discussion, extract the decision pattern as a reusable skill.

Output format (STRICT YAML):
```yaml
name: short_snake_case_name
trigger: "one sentence describing when this discussion pattern applies"
steps:
  - "key finding or decision made"
  - "another key finding"
lessons:
  - "what to watch out for next time"
```

Rules:
- Capture the DECISION PATTERN, not the specific details
- Include what each agent perspective contributed (research findings, design choices, risks found)
- Include disagreements and how they were resolved
- If the discussion was too shallow or generic, output: NONE"""


def _parse_yaml(response: str) -> Skill | None:
    if "NONE" in response.upper():
        return None
    text = response
    if "```yaml" in response:
        text = response.split("```yaml")[1].split("```")[0]
    elif "```" in response:
        text = response.split("```")[1].split("```")[0]
    try:
        return Skill.from_yaml(text)
    except Exception:
        return None


async def extract_execution_skill(messages: list[dict], provider: ModelProvider) -> Skill | None:
    conv = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in messages[-12:])
    response = await provider.generate([
        {"role": "system", "content": _EXEC_PROMPT},
        {"role": "user", "content": conv},
    ])
    skill = _parse_yaml(response)
    if skill:
        skill.type = "execution"
    return skill


async def extract_discussion_skill(messages: list[dict], provider: ModelProvider) -> Skill | None:
    conv = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in messages[-12:])
    response = await provider.generate([
        {"role": "system", "content": _DISCUSS_PROMPT},
        {"role": "user", "content": conv},
    ])
    skill = _parse_yaml(response)
    if skill:
        skill.type = "discussion"
    return skill


async def extract_and_save(
    messages: list[dict],
    skill_store: SkillStore,
    provider: ModelProvider,
    skill_type: str = "execution",
) -> Skill | None:
    if not skill_store.enabled:
        return None
    if skill_type == "discussion":
        skill = await extract_discussion_skill(messages, provider)
    else:
        skill = await extract_execution_skill(messages, provider)
    if skill:
        skill_store.save(skill)
    return skill
