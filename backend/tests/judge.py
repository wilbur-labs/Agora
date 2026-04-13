"""LLM-as-Judge evaluator — uses GPT to assess agent output quality."""
from __future__ import annotations

from dataclasses import dataclass

from agora.models.base import ModelProvider


@dataclass
class JudgeResult:
    passed: bool
    score: int  # 1-5
    reason: str


_JUDGE_PROMPT = """You are a strict quality evaluator for a multi-agent AI council system.

Given:
- The user's original question
- An agent's name and role
- The agent's response

Evaluate whether the response:
1. RELEVANCE: Directly addresses the user's question (not off-topic)
2. ROLE FIT: Matches the agent's assigned role (e.g. Researcher should research, Critic should critique)
3. QUALITY: Provides substantive, useful content (not generic filler)
4. LANGUAGE: Responds in the same language the user used

Output format (STRICT):
SCORE: <1-5>
PASSED: <YES/NO>
REASON: <one sentence explanation>

Scoring:
5 = Excellent — relevant, role-appropriate, substantive, correct language
4 = Good — minor issues but overall useful
3 = Acceptable — addresses the question but weak role fit or generic
2 = Poor — partially relevant or wrong role behavior
1 = Fail — off-topic, wrong language, or empty"""


async def judge_response(
    provider: ModelProvider,
    user_question: str,
    agent_name: str,
    agent_role: str,
    agent_response: str,
) -> JudgeResult:
    prompt = (
        f"User question: {user_question}\n\n"
        f"Agent: {agent_name} ({agent_role})\n\n"
        f"Agent response:\n{agent_response[:2000]}"
    )
    result = await provider.generate([
        {"role": "system", "content": _JUDGE_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return _parse_judge(result)


_EXEC_JUDGE_PROMPT = """You are a strict evaluator for an AI executor agent.

Given:
- The user's task
- The sequence of tool calls the executor made
- The final state (files created, commands run, etc.)

Evaluate whether:
1. TASK COMPLETION: The task was actually completed (not just talked about)
2. CORRECTNESS: The result matches what the user asked for
3. EFFICIENCY: No unnecessary or redundant tool calls

Output format (STRICT):
SCORE: <1-5>
PASSED: <YES/NO>
REASON: <one sentence explanation>"""


async def judge_execution(
    provider: ModelProvider,
    user_task: str,
    tool_events: list[str],
    final_state: str,
) -> JudgeResult:
    prompt = (
        f"User task: {user_task}\n\n"
        f"Tool calls:\n" + "\n".join(tool_events[:20]) + "\n\n"
        f"Final state:\n{final_state}"
    )
    result = await provider.generate([
        {"role": "system", "content": _EXEC_JUDGE_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return _parse_judge(result)


_DISCUSSION_JUDGE_PROMPT = """You are a strict evaluator for a multi-agent council discussion.

Given:
- The user's question
- The full discussion (all agents' responses)

Evaluate the DISCUSSION AS A WHOLE:
1. MULTI-PERSPECTIVE: Did different agents provide genuinely different perspectives? (not repeating each other)
2. RELEVANCE: Does the discussion address the user's actual question?
3. COMPLETENESS: Are key aspects covered? (research, design, risks, conclusion)
4. ACTIONABILITY: Does the synthesizer produce concrete, actionable output?

Output format (STRICT):
SCORE: <1-5>
PASSED: <YES/NO>
REASON: <one sentence explanation>"""


async def judge_discussion(
    provider: ModelProvider,
    user_question: str,
    agent_responses: dict[str, str],
) -> JudgeResult:
    discussion = "\n\n".join(
        f"[{name}]\n{text[:500]}" for name, text in agent_responses.items()
    )
    prompt = f"User question: {user_question}\n\nDiscussion:\n{discussion}"
    result = await provider.generate([
        {"role": "system", "content": _DISCUSSION_JUDGE_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return _parse_judge(result)


def _parse_judge(text: str) -> JudgeResult:
    score = 1
    passed = False
    reason = text.strip()
    for line in text.strip().splitlines():
        line = line.strip()
        if line.upper().startswith("SCORE:"):
            try:
                score = int(line.split(":")[1].strip()[0])
            except (ValueError, IndexError):
                pass
        elif line.upper().startswith("PASSED:"):
            passed = "YES" in line.upper()
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return JudgeResult(passed=passed, score=score, reason=reason)
