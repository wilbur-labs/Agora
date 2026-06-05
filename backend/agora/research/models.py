"""Data contracts for research orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


TASK_TYPES = {
    "paper_survey",
    "technical_report",
    "official_docs_comparison",
    "library_evaluation",
    "repo_deep_dive",
    "architecture_decision",
    "poc_or_benchmark",
    "migration_plan",
    "spec_generation",
    "code_implementation",
}


@dataclass
class ResearchTask:
    task_id: str
    question: str
    task_dir: Path
    decision: dict[str, Any]
    created_date: str = field(default_factory=lambda: date.today().isoformat())


@dataclass
class WorkerResult:
    worker: str
    status: str
    notes: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    workspace: str | None = None
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    reason: str | None = None
    stdout_chars: int = 0
    stderr_chars: int = 0
    notes_file: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "workspace": self.workspace,
            "command": self.command,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "notes_file": self.notes_file,
            "stdout_chars": self.stdout_chars,
            "stderr_chars": self.stderr_chars,
            "artifacts": self.artifacts,
        }
