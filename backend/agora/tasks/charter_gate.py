"""The one door a new task comes through: a project takes tasks only once its goal is set.

A task is proposed *against the charter*; without a goal there is nothing to hold it to,
and any create path left without this check is the "new task" dialog again with a
different front. This module exists so the rule is stated once and every creator asks
it — the HTTP route (`tasks/router.py`) and the orchestration CLI's `task start`
(`orchestration/service.py`) both do, and a new creator should too, rather than
re-deriving "does this project have a goal" from the charter's shape.

The kickoff's own adoption writes the charter first and then creates its tasks through
the store; it is upstream of this door, not behind it. The store itself does not ask —
it has no view of the project — which is why the check lives at the callers that do.

What this gate is, exactly: a check at the moment of creation, read from the project
registry, followed by a write to the task store. The two are different stores with no
transaction across them, so a charter cleared through its editor *between* the read and
the write is not caught — the same shape as the adoption's own charter write (workspace
CR-57), and it closes the same way: once adr/0005 gives the charter a version, the check
becomes a compare against that version at write time (workspace CR-93, `defer`). Until
then this is what "the backend keeps the rule" means, no more.

CR-61 made the order a rule (the UI refused first; the backend now refuses too), and a
review of that change found the CLI path still open — the rule had been stated wider
than it was kept. Hence one gate, called from both.
"""
from __future__ import annotations

from typing import Any

#: The refusal's stable code, shared with the conversation's own refusal
#: (`api/kickoff_router.py`) so a client tells "no goal yet" from every other 409.
PROJECT_HAS_NO_CHARTER = "project_has_no_charter"


class ProjectHasNoCharter(ValueError):
    """The project's goal is not set, so it takes no tasks yet."""

    code = PROJECT_HAS_NO_CHARTER

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"Project '{project_id}' has no goal yet; settle it with the agent before creating tasks."
        )
        self.project_id = project_id


def charter_goal_of(charter: Any) -> str:
    """The goal a charter carries, stripped; empty when there is none to speak of."""
    raw = charter if isinstance(charter, dict) else {}
    return str(raw.get("goal") or "").strip()


def require_charter(project: Any) -> None:
    """Refuse a task for a project whose goal is not set.

    ``project`` is the registry's `Project`; only its ``charter`` and ``project_id`` are
    read, so a test double with those two fields is enough.
    """
    if not charter_goal_of(getattr(project, "charter", None)):
        raise ProjectHasNoCharter(str(getattr(project, "project_id", "") or "?"))
