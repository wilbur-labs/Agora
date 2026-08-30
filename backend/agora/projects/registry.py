"""Multi-project registry for Agora control-plane state.

The registry is the single reader and writer of projects for the whole product:
the API routers, workspace provisioning, the execution dispatcher, the
orchestration service, doctor and the research orchestrator all resolve projects
through it. It used to keep them in `.agora/projects.yaml`; it now keeps them in
the control-plane database and treats that YAML as an import source only. No code
path outside `agora.projects.importer` reads or writes the file.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agora.config.settings import resolve_control_plane_db_path
from agora.fsprobe import probe_path

from .importer import (
    ProjectImportError,
    ProjectImportResult,
    ProjectImportSourceMissing,
    import_projects_from_yaml,
)
from .models import (
    DEFAULT_REVIEWER_AGENT,
    DEFAULT_WORKER_AGENT,
    KNOWN_AGENTS,
    Project,
    validate_agents,
    validate_project_id,
)
from .store import (
    ORIGIN_AGORA,
    SETTING_LAST_IMPORT_ERROR,
    SETTING_PROJECTS_BASE,
    ProjectExistsError,
    ProjectStore,
)

__all__ = [
    "DEFAULT_REVIEWER_AGENT",
    "DEFAULT_WORKER_AGENT",
    "KNOWN_AGENTS",
    "Project",
    "ProjectImportError",
    "ProjectImportSourceMissing",
    "ProjectRegistry",
]


class ProjectRegistry:
    def __init__(self, config: dict[str, Any] | None = None, *, project_root: Path | None = None):
        self.config = config or {}
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        cfg = self.config.get("projects", {})
        # Kept as `registry_path` for every existing caller, but it now names the
        # *import source*, not the live store.
        self.registry_path = self._resolve_path(
            cfg.get("registry_path", self.project_root / ".agora" / "projects.yaml"),
            self.project_root,
        )
        self.default_project = cfg.get("default", "agora")
        self._configured_projects = cfg.get("projects", {})
        self.store = ProjectStore(
            resolve_control_plane_db_path(self.config, project_root=self.project_root)
        )
        self._import_result: ProjectImportResult | None = None
        # What is known about the import source: "present" (read it), "absent"
        # (definitely no such file) or "unknown" (the open failed some other way).
        self._source_state = "unknown"
        self.ensure_ready()

    # ------------------------------------------------------------------- setup

    def ensure_ready(self) -> None:
        """Make the store usable: import the YAML, then seed configured projects.

        Never creates or modifies the import file — the old ``ensure_exists`` wrote
        it, and this deliberately does not. If the file exists but cannot be
        understood, the failure is raised when the store has no projects to serve
        instead of returning an empty list; when the store does hold projects it
        stays authoritative and the failure is recorded and reported through
        :meth:`import_status`.

        The source is opened, not probed. Only a definite ``FileNotFoundError``
        counts as "there is no import source" and unlocks seeding of configured or
        default projects. A permission error or any other stat/open failure is
        unknown, and unknown fails closed: seeding defaults there would hand the
        caller a normal-looking project list that is indistinguishable from a
        machine that genuinely has no source.

        Consequence this change deepened: constructing a registry runs this
        method, so it writes project state into whatever control-plane database
        ``resolve_control_plane_db_path`` resolves to — including from the API's
        startup path, which builds a registry via the execution dispatcher. Tests
        that construct a registry with a configuration naming no data root
        therefore write projects into the same file their tasks and runs use. That
        was already true of task and run state; this change extends it to projects
        and to the current-project pointer, and the working-directory-relative
        default makes "which database" depend on where the process was started.
        See the same note on ``resolve_control_plane_db_path``.
        """
        try:
            self._import_result = self.import_from_yaml()
            self._source_state = "present"
        except ProjectImportSourceMissing:
            # A recorded failure from a file that is not there would be a claim
            # about nothing, so it is cleared — but only for definite absence.
            self._source_state = "absent"
            self.store.clear_setting(SETTING_LAST_IMPORT_ERROR)
            if self.store.count() == 0:
                self._seed_configured_projects()
        except ProjectImportError as exc:
            self._source_state = exc.source_state
            self.store.set_setting(SETTING_LAST_IMPORT_ERROR, str(exc))
            if self.store.count() == 0:
                raise
        self._ensure_current_pointer()

    def import_from_yaml(self, path: str | Path | None = None) -> ProjectImportResult:
        """Import projects from the YAML import source into the store.

        Idempotent and keyed by project_id. Raises :class:`ProjectImportError` if
        the file cannot be read or understood.
        """
        source = Path(path) if path is not None else self.registry_path
        result = import_projects_from_yaml(
            self.store,
            source,
            project_root=self.project_root,
            on_project_added=self._ensure_project_dirs,
        )
        self.store.clear_setting(SETTING_LAST_IMPORT_ERROR)
        self._import_result = result
        return result

    def import_status(self) -> dict[str, Any]:
        """What the last import of the YAML source did, for human-facing reporting.

        ``error`` is set when the source was reached but could not be read or
        understood. It is never conflated with "there are no projects".

        ``source_present`` reports what the last open actually established: True if
        the source was read, False only for a definite "no such file", and None
        when the open failed in a way that does not distinguish the two. It is not
        a fresh existence probe, because ``Path.exists()`` returns False for
        failures that are not absence.
        """
        error = self.store.get_setting(SETTING_LAST_IMPORT_ERROR)
        return {
            "source": str(self.registry_path),
            "source_present": {"present": True, "absent": False}.get(self._source_state),
            "error": error,
            "last_import": self._import_result.as_dict() if self._import_result else None,
        }

    def _seed_configured_projects(self) -> None:
        """Seed from the inline `projects.projects` config block.

        Only reached when the import source is definitely absent and the store is
        empty, which is the first run of a fresh database on a machine that has no
        import file. An unreadable source must never get here: seeding over one
        would present invented defaults as the configuration.
        """
        for project_id, entry in (self._configured_projects or self._default_projects()).items():
            if self.store.exists(project_id):
                continue
            root = self._resolve_path(entry["root"], self.project_root)
            workspaces = {
                key: self._resolve_path(value, root)
                for key, value in (entry.get("workspaces") or {}).items()
            }
            project = self.store.insert(
                project_id,
                name=entry.get("name", project_id),
                root=root,
                # Whatever the configuration says, including nothing: seeding must
                # not invent workspaces the configured project did not ask for.
                workspaces=workspaces,
                worker_agent=entry.get("worker_agent") or DEFAULT_WORKER_AGENT,
                reviewer_agent=entry.get("reviewer_agent") or DEFAULT_REVIEWER_AGENT,
                charter=dict(entry.get("charter") or {}),
                origin=ORIGIN_AGORA,
            )
            self._ensure_project_dirs(project)

    def _ensure_current_pointer(self) -> None:
        """Keep the pointer resolvable, and say so when it had to be moved.

        The pointer may never name a missing project. A *stored* pointer that does
        cannot be produced through this store — :meth:`ProjectStore.set_current_project_id`
        fails closed and nothing deletes projects — so finding one means the
        database was changed from outside, and repairing it without a word would
        hide that the current project is no longer the one that was selected. A
        *file* pointer naming a project the file does not list is a malformed
        source and is rejected by the importer instead of being repaired here.

        stderr is the operator's channel for a fact with nowhere durable to go:
        this runs during startup and registry construction, before any request.
        """
        current = self.store.current_project_id()
        if current is not None and self.store.exists(current):
            return
        for candidate in (self.default_project, *sorted(self.store.list_projects())):
            if self.store.exists(candidate):
                self.store.set_current_project_id(candidate)
                if current is not None:
                    print(
                        f"agora: the stored current-project pointer named '{current}', which "
                        f"the project store does not hold; selected '{candidate}' instead",
                        file=sys.stderr,
                    )
                return

    # ------------------------------------------------------------------- reads

    def list_projects(self) -> dict[str, Project]:
        return self.store.list_projects()

    def current_project_id(self) -> str:
        return self.store.current_project_id() or self.default_project

    def current_project(self) -> Project:
        return self.get(self.current_project_id())

    def get(self, project_id: str) -> Project:
        return self.store.require(project_id)

    def use(self, project_id: str) -> Project:
        self.store.set_current_project_id(project_id)
        return self.get(project_id)

    def projects_base(self) -> Path:
        """Single root under which new projects get their own subfolder."""
        raw = self.store.get_setting(SETTING_PROJECTS_BASE)
        if raw:
            return self._resolve_path(raw, self.project_root)
        return (self.project_root / "projects").resolve()

    def set_projects_base(self, path: str | Path) -> Path:
        """Persist the projects root; create it if missing. Returns the resolved path."""
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = self.project_root / resolved
        resolved = resolved.resolve()
        if resolved.exists() and not resolved.is_dir():
            raise ValueError("Projects root must be a directory")
        resolved.mkdir(parents=True, exist_ok=True)
        self.store.set_setting(SETTING_PROJECTS_BASE, str(resolved))
        return resolved

    # ------------------------------------------------------------------ writes

    def create_project(
        self,
        project_id: str,
        name: str | None = None,
        *,
        worker_agent: str | None = None,
        reviewer_agent: str | None = None,
    ) -> Project:
        """Create a new project as a subfolder of the configured projects root."""
        root = self.projects_base() / project_id
        # Never adopt (and later git-init) a pre-existing non-empty directory:
        # it may hold unmanaged files the control plane must not touch. This was
        # ``root.exists() and root.is_dir() and any(root.iterdir())``, which let an
        # *inaccessible* directory through the guard: exists() answers False for a
        # stat this process may not complete, so a directory full of unmanaged files
        # behind a denying ACL read as a safely empty path and was adopted. A guard
        # that protects unknown contents cannot treat "could not look" as "nothing
        # there", so an unobservable path refuses instead.
        probe = probe_path(root)
        if probe.unknown:
            raise ValueError(
                f"Project directory cannot be inspected, so it may not be empty: {root} ({probe.reason()})"
            )
        if probe.is_dir():
            try:
                occupied = any(root.iterdir())
            except OSError as exc:
                raise ValueError(
                    f"Project directory cannot be inspected, so it may not be empty: {root} "
                    f"({type(exc).__name__})"
                ) from exc
            if occupied:
                raise ValueError(f"Project directory already exists and is not empty: {root}")
        return self.add(
            project_id,
            root,
            name,
            worker_agent=worker_agent,
            reviewer_agent=reviewer_agent,
        )

    def add(
        self,
        project_id: str,
        root: str | Path,
        name: str | None = None,
        *,
        worker_agent: str | None = None,
        reviewer_agent: str | None = None,
    ) -> Project:
        validate_project_id(project_id)
        worker = worker_agent or DEFAULT_WORKER_AGENT
        reviewer = reviewer_agent or DEFAULT_REVIEWER_AGENT
        validate_agents(worker, reviewer)
        project_root = Path(root).expanduser().resolve()
        try:
            project = self.store.insert(
                project_id,
                name=name or project_id,
                root=project_root,
                workspaces={
                    key: Path(value)
                    for key, value in self._default_workspaces(project_root).items()
                },
                worker_agent=worker,
                reviewer_agent=reviewer,
                origin=ORIGIN_AGORA,
            )
        except ProjectExistsError as exc:
            raise ValueError(str(exc)) from None
        self._ensure_project_dirs(project)
        return project

    def set_project_agents(
        self, project_id: str, *, worker_agent: str, reviewer_agent: str
    ) -> Project:
        """Update a project's worker/reviewer agent assignment."""
        validate_agents(worker_agent, reviewer_agent)
        return self.store.set_agents(
            project_id, worker_agent=worker_agent, reviewer_agent=reviewer_agent
        )

    def set_charter(
        self,
        project_id: str,
        *,
        goal: str,
        non_goals: list[str],
        success_criteria: list[str],
    ) -> Project:
        """Set a project's human-authored charter. This is the human-facing path
        to define the goal; no agent code path writes the charter. (Endpoint-level
        authentication is a 1.3 concern — see the charter route.)"""
        return self.store.set_charter(
            project_id,
            {
                "goal": goal,
                "non_goals": list(non_goals),
                "success_criteria": list(success_criteria),
            },
        )

    def research_config(self, project: Project | None = None) -> dict[str, Any]:
        p = project or self.current_project()
        return {
            "artifact_dir": str(p.research_dir),
            "workspaces": {name: str(path) for name, path in p.workspaces.items()},
        }

    # ---------------------------------------------------------------- internals

    def _default_projects(self) -> dict[str, Any]:
        return {
            self.default_project: {
                "name": "Agora",
                "root": str(self.project_root),
                "workspaces": self._default_workspaces(self.project_root),
            }
        }

    @staticmethod
    def _default_workspaces(project_root: Path) -> dict[str, str]:
        return {
            "claude": str(project_root / ".agora" / "workspaces" / "claude"),
            "codex": str(project_root / ".agora" / "workspaces" / "codex"),
            "kiro": str(project_root / ".agora" / "workspaces" / "kiro"),
        }

    @staticmethod
    def _ensure_project_dirs(project: Project) -> None:
        root = project.root
        (root / ".agora" / "research").mkdir(parents=True, exist_ok=True)
        (root / ".agora" / "decisions").mkdir(parents=True, exist_ok=True)
        (root / ".agora" / "specs").mkdir(parents=True, exist_ok=True)
        for path in project.workspaces.values():
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_path(value: str | Path, base: Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
