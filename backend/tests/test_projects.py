from __future__ import annotations

import asyncio

from agora.projects import ProjectRegistry
from agora.research import ResearchOrchestrator


def _config(tmp_path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    return {
        "projects": {
            "registry_path": str(tmp_path / "registry" / "projects.yaml"),
            "default": "project-a",
            "projects": {
                "project-a": {
                    "name": "Project A",
                    "root": str(project_a),
                    "workspaces": {
                        "claude": str(tmp_path / "claude" / "project-a"),
                        "codex": str(tmp_path / "codex" / "project-a"),
                        "kiro": str(tmp_path / "kiro" / "project-a"),
                    },
                },
                "project-b": {
                    "name": "Project B",
                    "root": str(project_b),
                    "workspaces": {
                        "claude": str(tmp_path / "claude" / "project-b"),
                        "codex": str(tmp_path / "codex" / "project-b"),
                        "kiro": str(tmp_path / "kiro" / "project-b"),
                    },
                },
            },
        }
    }


def test_project_registry_lists_and_switches_projects(tmp_path):
    cfg = _config(tmp_path)
    registry = ProjectRegistry(cfg)

    assert registry.current_project_id() == "project-a"
    assert sorted(registry.list_projects()) == ["project-a", "project-b"]

    project = registry.use("project-b")

    assert project.project_id == "project-b"
    assert registry.current_project_id() == "project-b"
    assert project.research_dir == tmp_path / "project-b" / ".agora" / "research"


def test_research_orchestrator_uses_current_project_artifact_dir(tmp_path):
    cfg = _config(tmp_path)
    ProjectRegistry(cfg).use("project-b")

    task = asyncio.run(ResearchOrchestrator(cfg).run("调研当前项目架构决策。"))

    expected_root = tmp_path / "project-b" / ".agora" / "research"
    assert task.task_dir.parent == expected_root
    assert task.decision["project"]["id"] == "project-b"
    assert (task.task_dir / "router-decision.json").exists()
    assert (task.task_dir / "notes").exists()
