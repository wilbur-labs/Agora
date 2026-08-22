"""Creating a project provisions its agent workspaces as linked git worktrees,
and provisioning never sources from an outer repository the project sits in."""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.execution.deliverables import _is_provisioned_worktree
from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry
from agora.workspaces.models import ProvisionRequest
from agora.workspaces.provisioner import WorkspaceProvisioner, WorkspaceValidationError

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required for provisioning")


def _git(*args, cwd) -> str:
    return subprocess.run([GIT, "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True, timeout=15).stdout.strip()


def _outer_repo(root):
    """An outer repository the project directories will live inside — the shape
    that made the earlier git-init approach dangerous."""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=root)
    (root / "OUTER.md").write_text("outer repo\n", encoding="utf-8")
    _git("-c", "user.email=o@o", "-c", "user.name=o", "add", "-A", cwd=root)
    _git("-c", "user.email=o@o", "-c", "user.name=o", "commit", "-q", "-m", "outer", cwd=root)


def _registry(tmp_path) -> ProjectRegistry:
    # The whole tmp tree is an outer git repo; projects live *inside* it.
    _outer_repo(tmp_path)
    config = {
        "projects": {
            "registry_path": str(tmp_path / ".agora" / "projects.yaml"),
            "default": "agora",
            "projects": {
                "agora": {
                    "name": "Agora", "root": str(tmp_path / "agora"),
                    "workspaces": {a: str(tmp_path / "agora" / ".agora" / "workspaces" / a)
                                   for a in ("codex", "claude", "kiro")},
                }
            },
        }
    }
    registry = ProjectRegistry(config)
    registry.set_projects_base(tmp_path / "base")
    return registry


def test_create_project_provisions_linked_worktrees_inside_outer_repo(tmp_path):
    registry = _registry(tmp_path)
    outer_head_before = _git("rev-parse", "HEAD", cwd=tmp_path)
    outer_worktrees_before = _git("worktree", "list", "--porcelain", cwd=tmp_path)

    app.dependency_overrides[get_project_registry] = lambda: registry
    client = TestClient(app)
    try:
        resp = client.post("/api/projects", json={"project_id": "demo", "name": "Demo"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["provisioning"]["ok"] is True
        assert set(body["provisioning"]["workspaces"].values()) == {"ready"}

        project = registry.get("demo")
        # The project root is its OWN repo, not the outer one.
        top = _git("rev-parse", "--show-toplevel", cwd=project.root)
        from pathlib import Path
        assert Path(top).resolve() == project.root.resolve()
        # Each workspace is a linked worktree of the project (not the outer repo).
        for adapter, workspace in project.workspaces.items():
            assert _is_provisioned_worktree(GIT, workspace), adapter
    finally:
        app.dependency_overrides.clear()

    # The outer repository is completely untouched: HEAD and worktrees unchanged.
    assert _git("rev-parse", "HEAD", cwd=tmp_path) == outer_head_before
    assert _git("worktree", "list", "--porcelain", cwd=tmp_path) == outer_worktrees_before


def test_create_project_rejects_preexisting_nonempty_dir(tmp_path):
    registry = _registry(tmp_path)
    squatter = registry.projects_base() / "demo"
    squatter.mkdir(parents=True)
    (squatter / "user-file.txt").write_text("do not touch", encoding="utf-8")

    app.dependency_overrides[get_project_registry] = lambda: registry
    client = TestClient(app)
    try:
        resp = client.post("/api/projects", json={"project_id": "demo", "name": "Demo"})
        assert resp.status_code == 422
        assert (squatter / "user-file.txt").read_text(encoding="utf-8") == "do not touch"
        assert not (squatter / ".git").exists()  # never git-inited an unmanaged dir
    finally:
        app.dependency_overrides.clear()


def test_direct_provision_on_nested_nonrepo_root_is_rejected(tmp_path):
    """Provisioning a project whose root is nested in an outer repo but is not its
    own repo must be rejected, never sourcing a worktree from the outer repo."""
    registry = _registry(tmp_path)
    # Register a project whose root exists inside the outer repo but was never
    # made its own git repo (i.e. ensure_source_repo was not run).
    root = tmp_path / "base" / "nested"
    root.mkdir(parents=True)
    registry.add("nested", root)
    provisioner = WorkspaceProvisioner(registry)

    with pytest.raises(WorkspaceValidationError, match="not a Git repository"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="nested", adapter="codex")))

    # No worktree was added to the outer repo.
    assert "nested" not in _git("worktree", "list", "--porcelain", cwd=tmp_path)


def test_ensure_source_repo_seeds_unborn_head(tmp_path):
    """A project root that is its own repo but has an unborn HEAD still becomes
    provisionable (a seed commit is created)."""
    registry = _registry(tmp_path)
    root = tmp_path / "base" / "unborn"
    root.mkdir(parents=True)
    _git("init", "-q", cwd=root)  # own repo, but no commit yet (unborn HEAD)
    registry.add("unborn", root)
    provisioner = WorkspaceProvisioner(registry)

    asyncio.run(provisioner.ensure_source_repo("unborn"))
    assert _git("rev-parse", "--verify", "HEAD", cwd=root)  # HEAD now exists

    result = asyncio.run(provisioner.provision(ProvisionRequest(project_id="unborn", adapter="codex")))
    assert result.status.state.value == "ready"


def test_ensure_source_repo_fails_closed_on_staged_files(tmp_path):
    """Seeding an unborn HEAD must never sweep pre-existing staged files into the
    initial commit; it fails closed instead."""
    registry = _registry(tmp_path)
    root = tmp_path / "base" / "staged"
    root.mkdir(parents=True)
    _git("init", "-q", cwd=root)
    (root / "user.txt").write_text("mine", encoding="utf-8")
    _git("add", "user.txt", cwd=root)  # staged, HEAD still unborn
    registry.add("staged", root)
    provisioner = WorkspaceProvisioner(registry)

    with pytest.raises(WorkspaceValidationError, match="staged"):
        asyncio.run(provisioner.ensure_source_repo("staged"))
    assert _git("diff", "--cached", "--name-only", cwd=root) == "user.txt"  # untouched


def test_provisioner_locks_are_process_shared(tmp_path):
    """Every provisioner instance shares one process-wide lock map, so create-time
    and explicit provisioning of the same workspace serialize (Invariant 6)."""
    from agora.workspaces.provisioner import _WORKSPACE_LOCKS

    registry = _registry(tmp_path)
    p1 = WorkspaceProvisioner(registry)
    p2 = WorkspaceProvisioner(registry)
    assert p1._locks is p2._locks is _WORKSPACE_LOCKS
