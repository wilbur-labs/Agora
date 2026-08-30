"""Project registry and selection for Agora.

Projects are stored in the control-plane database; `.agora/projects.yaml` is an
import source only. `ProjectRegistry` is the single access path for both.
"""

from .importer import ProjectImportError, ProjectImportResult, ProjectImportSourceMissing
from .models import (
    DEFAULT_REVIEWER_AGENT,
    DEFAULT_WORKER_AGENT,
    KNOWN_AGENTS,
    Project,
)
from .registry import ProjectRegistry
from .store import ProjectStore

__all__ = [
    "DEFAULT_REVIEWER_AGENT",
    "DEFAULT_WORKER_AGENT",
    "KNOWN_AGENTS",
    "Project",
    "ProjectImportError",
    "ProjectImportResult",
    "ProjectImportSourceMissing",
    "ProjectRegistry",
    "ProjectStore",
]
