"""Generate requirement-clarifying questions for a task via a one-shot agent call.

This powers the in-app requirements interview: the agent proposes the questions,
the user answers them in the UI, and the answers converge the requirements before
any design/mockup is produced.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from agora.orchestration.runtime import RuntimeLaunchError, resolve_runtime_command

CLARIFY_TIMEOUT = 150
MAX_QUESTIONS = 8


class ClarifyError(RuntimeError):
    pass


_PROMPT = (
    "You are a requirements analyst. For the software task below, produce 4-6 concise "
    "clarifying questions whose answers will converge the design and requirements before "
    "any UI/mockup is built. Output ONLY a JSON array (no prose, no markdown fence). Each "
    "element must be an object with keys: \"id\" (short kebab-case slug), \"question\" (the "
    "question, in Simplified Chinese), \"why\" (one short reason it matters, in Chinese), "
    "\"suggestions\" (an array of 2-4 short candidate answers, in Chinese). "
    "Task title: {title}\nTask description: {description}"
)


def _parse_questions(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ClarifyError("agent output contained no JSON array")
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ClarifyError(f"agent output was not valid JSON: {exc}") from None
    if not isinstance(raw, list):
        raise ClarifyError("agent output was not a JSON array")
    questions: list[dict] = []
    for index, item in enumerate(raw[:MAX_QUESTIONS]):
        if not isinstance(item, dict) or "question" not in item:
            continue
        suggestions = item.get("suggestions") or []
        questions.append({
            "id": str(item.get("id") or f"q{index + 1}"),
            "question": str(item["question"]),
            "why": str(item.get("why") or ""),
            "suggestions": [str(s) for s in suggestions if isinstance(s, (str, int, float))][:4],
        })
    if not questions:
        raise ClarifyError("agent produced no usable questions")
    return questions


def generate_questions(*, title: str, description: str, cwd: Path) -> list[dict]:
    """Run the Claude CLI once to propose clarifying questions; parse and return them."""
    prompt = _PROMPT.format(title=title, description=description or "(none)")
    try:
        command = resolve_runtime_command(["claude", "-p", prompt, "--output-format", "text"])
    except RuntimeLaunchError as exc:
        raise ClarifyError(f"agent is unavailable: {exc}") from None
    try:
        result = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CLARIFY_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClarifyError(f"agent call failed: {type(exc).__name__}") from None
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise ClarifyError(f"agent exited {result.returncode}: {detail[-1] if detail else 'unknown'}"[:300])
    return _parse_questions(result.stdout)
