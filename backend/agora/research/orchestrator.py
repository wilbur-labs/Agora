"""High-level research orchestration entrypoint."""
from __future__ import annotations

from pathlib import Path

from agora.config.settings import get_config

from .audit import AuditLogger
from .router import classify
from .store import ResearchStore
from .workers import build_worker_registry


class ResearchOrchestrator:
    def __init__(self, config: dict | None = None, project_id: str | None = None):
        self.config = config or get_config()
        self.project = None
        self.audit = AuditLogger(None)
        research_cfg = dict(self.config.get("research", {}))

        if "projects" in self.config:
            from agora.projects import ProjectRegistry

            registry = ProjectRegistry(self.config)
            self.project = registry.get(project_id) if project_id else registry.current_project()
            research_cfg.update(registry.research_config(self.project))
            self.audit = AuditLogger(self.project.agora_dir / "audit.log")

        root = research_cfg.get("artifact_dir", "research")
        self.research_cfg = research_cfg
        self.store = ResearchStore(Path(root).expanduser())

    async def run(self, question: str, dispatch: bool = False, only_worker: str | None = None):
        decision = classify(question)
        decision["dispatch"] = dispatch
        if only_worker:
            decision["only_worker"] = only_worker
        if self.project:
            decision["project"] = {
                "id": self.project.project_id,
                "name": self.project.name,
                "root": str(self.project.root),
            }
        task = self.store.create_task(question, decision)
        self.audit.write(
            "research_task_created",
            task_id=task.task_id,
            dispatch=dispatch,
            project=decision.get("project"),
            task_type=decision["task_type"],
        )

        worker_config = dict(self.config)
        worker_config["research"] = self.research_cfg
        workers = build_worker_registry(worker_config, dispatch=dispatch)
        selected = self._selected_workers(decision, only_worker)

        for worker_name in selected:
            adapter = workers.get(worker_name)
            if not adapter:
                continue
            self.audit.write(
                "worker_dispatch_started",
                task_id=task.task_id,
                worker=worker_name,
                dispatch=dispatch,
                workspace=str(adapter.workspace) if adapter.workspace else None,
            )
            result = await adapter.run(task)
            self.store.write_worker_result(task, result)
            self.audit.write(
                "worker_dispatch_finished",
                task_id=task.task_id,
                worker=worker_name,
                status=result.status,
                exit_code=result.exit_code,
                reason=result.reason,
                notes_file=result.notes_file,
            )

        return task

    def _selected_workers(self, decision: dict, only_worker: str | None) -> list[str]:
        if only_worker:
            return [only_worker]
        return [decision["primary_worker"], *decision.get("secondary_workers", [])]
