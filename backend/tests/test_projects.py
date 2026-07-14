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


def test_relative_registry_project_and_workspace_paths_are_repo_anchored(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = {
        "projects": {
            "registry_path": ".agora/projects.yaml",
            "default": "agora",
            "projects": {
                "agora": {
                    "root": ".",
                    "workspaces": {"codex": ".agora/workspaces/codex"},
                }
            },
        }
    }

    registry = ProjectRegistry(config, project_root=repo)

    project = registry.current_project()
    assert registry.registry_path == repo / ".agora" / "projects.yaml"
    assert project.root == repo
    assert project.workspaces["codex"] == repo / ".agora" / "workspaces" / "codex"


def test_add_accepts_documented_project_id_separators(tmp_path):
    registry = ProjectRegistry(_config(tmp_path))

    dashed = registry.add("new-project", tmp_path / "new-project")
    underscored = registry.add("new_project_2", tmp_path / "new-project-2")

    assert dashed.project_id == "new-project"
    assert underscored.project_id == "new_project_2"
    assert dashed.workspaces["codex"] == dashed.root / ".agora" / "workspaces" / "codex"

    try:
        registry.add("bad/project", tmp_path / "bad")
    except ValueError as exc:
        assert "letters, numbers, dashes, or underscores" in str(exc)
    else:
        raise AssertionError("path separators must not be accepted in project ids")
