"""Deterministic naming for provisioned agent worktrees (single source of truth).

Kept as a dependency-free leaf so both the provisioner (which creates worktrees)
and the execution layer (which validates a run's workspace against the expected
branch) can import it without an import cycle.
"""
from __future__ import annotations

import hashlib


def worktree_branch(project_id: str, adapter: str) -> str:
    """The branch a project/adapter's linked worktree must be checked out on."""
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8]
    return f"agora/workspace/{project_id.lower()}-{digest}/{adapter}"
