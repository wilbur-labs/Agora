"""Project value objects shared by the project store and the registry.

A Project's identity is its ``project_id``. Everything else — including where the
project lives on disk — is a *property* of that identity, not the identity
itself. See the environment note on ``root`` below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default cross-review pairing: an implementer works, a different agent reviews.
# codex declares code_change; claude declares correctness/safety review.
DEFAULT_WORKER_AGENT = "codex"
DEFAULT_REVIEWER_AGENT = "claude"
KNOWN_AGENTS = ("codex", "claude", "kiro")


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    # `root` and `workspaces` are this project's location *in the local
    # environment*. They are deliberately not part of its identity: W2 (WSL) and
    # W3 (remote host over SSH) will run the same project_id in an environment
    # where "where the project is" is not one local absolute path.
    #
    # When that environment dimension arrives, the change is additive:
    #   - add a `project_environments` table keyed (project_id, environment_id)
    #     holding kind ('local' | 'wsl' | 'ssh'), root, workspaces and whatever
    #     connection detail the kind needs;
    #   - migrate each existing projects row into one 'local' environment row and
    #     drop `root`/`workspaces` from `projects`, which then carries only
    #     identity, name, agents and charter;
    #   - resolve root/workspaces through the *active* environment instead of
    #     reading them off Project.
    # Nothing may key, join, or dedupe on `root`, or that migration turns into a
    # rewrite. project_id is the only key.
    root: Path
    workspaces: dict[str, Path]
    worker_agent: str = DEFAULT_WORKER_AGENT
    reviewer_agent: str = DEFAULT_REVIEWER_AGENT
    # Human-authored project definition (goal / non_goals / success_criteria).
    # Agents never write it; it answers "what is this project ultimately for".
    charter: dict[str, Any] = field(default_factory=dict)

    @property
    def agora_dir(self) -> Path:
        return self.root / ".agora"

    @property
    def research_dir(self) -> Path:
        return self.agora_dir / "research"


def validate_project_id(project_id: str) -> str:
    if not project_id or not project_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Project id must contain only letters, numbers, dashes, or underscores")
    return project_id


def validate_agents(worker_agent: str, reviewer_agent: str) -> None:
    for role, value in (("worker_agent", worker_agent), ("reviewer_agent", reviewer_agent)):
        if value not in KNOWN_AGENTS:
            raise ValueError(f"{role} must be one of: {', '.join(KNOWN_AGENTS)}")

@dataclass(frozen=True)
class TodayFocus:
    """Which projects are being pushed today, and what could not be resolved.

    `project_ids` is in promotion order and holds only ids that still exist.
    `unresolved_ids` holds promoted ids whose project is gone — kept separate rather
    than filtered away so a shrinking count can be explained instead of just observed.
    `limit` travels with them because the UI states it ("3/3 已满"), and a limit read
    from one place and rendered from another is how the two drift apart.
    """

    project_ids: list[str]
    unresolved_ids: list[str]
    limit: int

    @property
    def is_full(self) -> bool:
        """Whether another project can be promoted.

        Counts the unresolved ids too, because they hold their places: promotion is a
        human choice and an entry nobody can currently resolve is still a choice that was
        made. Excluding them made the API contradict itself -- two resolvable plus one
        unresolved reported `is_full=false` and then refused the next promotion with 409,
        so the screen would offer a slot it did not have.
        """
        return len(self.project_ids) + len(self.unresolved_ids) >= self.limit
