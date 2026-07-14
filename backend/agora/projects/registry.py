"""Multi-project registry for Agora control-plane state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    root: Path
    workspaces: dict[str, Path]

    @property
    def agora_dir(self) -> Path:
        return self.root / ".agora"

    @property
    def research_dir(self) -> Path:
        return self.agora_dir / "research"


class ProjectRegistry:
    def __init__(self, config: dict[str, Any] | None = None, *, project_root: Path | None = None):
        self.config = config or {}
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        cfg = self.config.get("projects", {})
        self.registry_path = self._resolve_path(
            cfg.get("registry_path", self.project_root / ".agora" / "projects.yaml"),
            self.project_root,
        )
        self.default_project = cfg.get("default", "agora")
        self._configured_projects = cfg.get("projects", {})
        self.ensure_exists()

    def ensure_exists(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        if self.registry_path.exists():
            return
        data = {
            "current": self.default_project,
            "projects": self._default_projects(),
        }
        self._write(data)
        for project in data["projects"].values():
            self._ensure_project_dirs(project)

    def list_projects(self) -> dict[str, Project]:
        data = self._read()
        return {pid: self._project(pid, value) for pid, value in data.get("projects", {}).items()}

    def current_project_id(self) -> str:
        data = self._read()
        return data.get("current") or self.default_project

    def current_project(self) -> Project:
        return self.get(self.current_project_id())

    def get(self, project_id: str) -> Project:
        data = self._read()
        projects = data.get("projects", {})
        if project_id not in projects:
            raise KeyError(f"Unknown project: {project_id}")
        return self._project(project_id, projects[project_id])

    def use(self, project_id: str) -> Project:
        data = self._read()
        if project_id not in data.get("projects", {}):
            raise KeyError(f"Unknown project: {project_id}")
        data["current"] = project_id
        self._write(data)
        return self.get(project_id)

    def add(self, project_id: str, root: str | Path, name: str | None = None) -> Project:
        if not project_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Project id must contain only letters, numbers, dashes, or underscores")
        project_root = Path(root).expanduser().resolve()
        data = self._read()
        projects = data.setdefault("projects", {})
        if project_id in projects:
            raise ValueError(f"Project already exists: {project_id}")
        projects[project_id] = {
            "name": name or project_id,
            "root": str(project_root),
            "workspaces": self._default_workspaces(project_root),
        }
        self._ensure_project_dirs(projects[project_id])
        self._write(data)
        return self._project(project_id, projects[project_id])

    def research_config(self, project: Project | None = None) -> dict[str, Any]:
        p = project or self.current_project()
        return {
            "artifact_dir": str(p.research_dir),
            "workspaces": {name: str(path) for name, path in p.workspaces.items()},
        }

    def _read(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            self.ensure_exists()
        return yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}

    def _write(self, data: dict[str, Any]) -> None:
        self.registry_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def _project(self, project_id: str, data: dict[str, Any]) -> Project:
        root = self._resolve_path(data["root"], self.project_root)
        return Project(
            project_id=project_id,
            name=data.get("name", project_id),
            root=root,
            workspaces={k: self._resolve_path(v, root) for k, v in data.get("workspaces", {}).items()},
        )

    def _default_projects(self) -> dict[str, Any]:
        if self._configured_projects:
            return self._configured_projects
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

    def _ensure_project_dirs(self, data: dict[str, Any]) -> None:
        project = self._project("_setup", data)
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
