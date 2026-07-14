from __future__ import annotations

import asyncio
import subprocess

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.workspaces.models import ProvisionRequest, WorkspaceState
from agora.workspaces.provisioner import (
    WorkspaceConflictError,
    WorkspaceProvisioner,
    WorkspaceValidationError,
    WorkspaceUnavailableError,
)
from agora.workspaces.router import get_workspace_provisioner


def _git(*args: str, cwd=None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _git("init", str(root))
    _git("config", "user.email", "agora-tests@example.invalid", cwd=root)
    _git("config", "user.name", "Agora Tests", cwd=root)
    (root / "README.md").write_text("Agora workspace fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "initial", cwd=root)
    return root


def _system(tmp_path, root):
    config = {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(root),
                    "workspaces": {
                        adapter: str(root / ".agora" / "workspaces" / adapter)
                        for adapter in ("codex", "claude", "kiro")
                    },
                }
            },
        }
    }
    registry = ProjectRegistry(config)
    return registry, WorkspaceProvisioner(registry, timeout_seconds=20)


def test_missing_workspace_status_and_non_git_source(tmp_path, git_project):
    _, provisioner = _system(tmp_path, git_project)
    status = provisioner.status("alpha", "codex")
    assert status.state == WorkspaceState.MISSING
    assert status.source_is_git is True

    non_git = tmp_path / "plain"
    non_git.mkdir()
    _, plain = _system(tmp_path / "plain-state", non_git)
    plain_status = plain.status("alpha", "codex")
    assert plain_status.state == WorkspaceState.MISSING
    assert plain_status.source_is_git is False
    with pytest.raises(WorkspaceValidationError, match="not a Git"):
        asyncio.run(plain.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


def test_provision_creates_real_idempotent_linked_worktree(tmp_path, git_project):
    _, provisioner = _system(tmp_path, git_project)

    async def provision_twice():
        first = await provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex"))
        second = await provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex"))
        return first, second

    first, second = asyncio.run(provision_twice())
    workspace = git_project / ".agora" / "workspaces" / "codex"
    assert first.created is True and first.status.state == WorkspaceState.READY
    assert second.created is False and second.status.state == WorkspaceState.READY
    assert (workspace / ".git").is_file()
    assert (workspace / "README.md").is_file()
    assert _git("branch", "--show-current", cwd=workspace) == WorkspaceProvisioner._branch("alpha", "codex")
    assert _git("rev-parse", "HEAD", cwd=workspace) == _git("rev-parse", "HEAD", cwd=git_project)


def test_concurrent_provision_serializes_to_one_creation(tmp_path, git_project):
    _, provisioner = _system(tmp_path, git_project)
    request = ProvisionRequest(project_id="alpha", adapter="claude")

    async def provision_both():
        return await asyncio.gather(provisioner.provision(request), provisioner.provision(request))

    results = asyncio.run(provision_both())
    assert sorted(result.created for result in results) == [False, True]
    assert all(result.status.state == WorkspaceState.READY for result in results)


def test_foreign_directory_and_path_escape_are_rejected(tmp_path, git_project):
    registry, provisioner = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["kiro"]
    (workspace / "user-file.txt").write_text("do not overwrite", encoding="utf-8")
    assert provisioner.status("alpha", "kiro").state == WorkspaceState.FOREIGN
    with pytest.raises(WorkspaceConflictError, match="unmanaged"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="kiro")))
    assert (workspace / "user-file.txt").read_text(encoding="utf-8") == "do not overwrite"

    outside = tmp_path / "outside"
    project = registry.get("alpha")
    escaped_config = {
        "projects": {
            "registry_path": str(tmp_path / "escaped.yaml"),
            "default": "alpha",
            "projects": {"alpha": {"root": str(project.root), "workspaces": {"codex": str(outside)}}},
        }
    }
    escaped = WorkspaceProvisioner(ProjectRegistry(escaped_config))
    with pytest.raises(WorkspaceValidationError, match="allowed roots"):
        escaped.status("alpha", "codex")


def test_real_worktree_on_wrong_branch_is_foreign(tmp_path, git_project):
    registry, provisioner = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    workspace.rmdir()
    _git("worktree", "add", "-b", "manual/wrong-branch", str(workspace), "HEAD", cwd=git_project)
    assert provisioner.status("alpha", "codex").state == WorkspaceState.FOREIGN
    with pytest.raises(WorkspaceConflictError):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


def test_rmdir_race_is_mapped_to_safe_conflict(tmp_path, git_project, monkeypatch):
    registry, provisioner = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    original = type(workspace).rmdir

    def race(path):
        if path.resolve() == workspace.resolve():
            raise OSError("simulated race with sensitive path")
        return original(path)

    monkeypatch.setattr(type(workspace), "rmdir", race)
    with pytest.raises(WorkspaceConflictError, match="changed unexpectedly: OSError"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


def test_missing_git_is_reported_as_unavailable(tmp_path, git_project):
    registry, _ = _system(tmp_path, git_project)
    provisioner = WorkspaceProvisioner(registry, git_command="agora-definitely-missing-git")
    with pytest.raises(WorkspaceUnavailableError, match="not installed"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


def test_existing_branch_reused_but_checked_out_branch_conflicts(tmp_path, git_project):
    _, provisioner = _system(tmp_path, git_project)
    _git("branch", WorkspaceProvisioner._branch("alpha", "kiro"), cwd=git_project)
    result = asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="kiro")))
    assert result.status.state == WorkspaceState.READY

    branch = WorkspaceProvisioner._branch("alpha", "claude")
    other = tmp_path / "other-worktree"
    _git("worktree", "add", "-b", branch, str(other), "HEAD", cwd=git_project)
    with pytest.raises(WorkspaceConflictError, match="already checked out"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="claude")))


def test_workspace_api_status_provision_and_errors(tmp_path, git_project):
    _, provisioner = _system(tmp_path, git_project)
    app.dependency_overrides[get_workspace_provisioner] = lambda: provisioner
    client = TestClient(app)
    try:
        status = client.get("/api/workspaces/alpha/codex")
        assert status.status_code == 200 and status.json()["state"] == "missing"
        created = client.post("/api/workspaces/provision", json={"project_id": "alpha", "adapter": "codex"})
        assert created.status_code == 200 and created.json()["status"]["state"] == "ready"
        assert client.get("/api/workspaces/missing").status_code == 404
        assert client.get("/api/workspaces/alpha/unknown").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_workspace_list_api_maps_path_escape_to_422(tmp_path, git_project):
    config = {
        "projects": {
            "registry_path": str(tmp_path / "escape-api.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "root": str(git_project),
                    "workspaces": {"codex": str(tmp_path / "outside")},
                }
            },
        }
    }
    provisioner = WorkspaceProvisioner(ProjectRegistry(config))
    app.dependency_overrides[get_workspace_provisioner] = lambda: provisioner
    try:
        assert TestClient(app).get("/api/workspaces/alpha").status_code == 422
    finally:
        app.dependency_overrides.clear()
