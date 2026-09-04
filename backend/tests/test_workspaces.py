from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.environments import CommandResult, LocalEnvironment
from agora.fsprobe import PathProbe
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
    # A separate control-plane store: projects live in the database now, so a
    # second registry sharing the first one's state directory would resolve
    # "alpha" from the first registry's row and never see the escaped workspace.
    escaped_config = {
        "projects": {
            "registry_path": str(tmp_path / "escaped-state" / "projects.yaml"),
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


# --- what the Environment seam is for: an unreadable workspace is not a missing one ---
#
# Every one of these injects an Environment rather than monkeypatching `pathlib`,
# which is the point of the seam: the same fake is what a remote implementation
# will be swapped in through.


class _ProbeOverride:
    """A LocalEnvironment whose probe of one path is replaced. Everything else real."""

    def __init__(self, target, probe_result):
        self._inner = LocalEnvironment()
        self._target = Path(target).resolve()
        self._probe_result = probe_result

    def run(self, argv, *, cwd=None, timeout):
        return self._inner.run(argv, cwd=cwd, timeout=timeout)

    def probe(self, path, *, follow_symlinks=True):
        if Path(path).resolve() == self._target:
            return self._probe_result
        return self._inner.probe(path, follow_symlinks=follow_symlinks)


def test_an_unreadable_workspace_is_error_with_a_reason_not_missing(tmp_path, git_project):
    registry, _ = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    env = _ProbeOverride(workspace, PathProbe("unknown", error=PermissionError(13, "denied")))
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=env)

    status = provisioner.status("alpha", "codex")

    # MISSING would tell the operator to provision a worktree that may well be
    # sitting right there; the fix is a permission, not a `git worktree add`.
    assert status.state == WorkspaceState.ERROR
    assert status.error is not None and "PermissionError" in status.error


def test_a_workspace_that_cannot_be_listed_is_error_not_missing(tmp_path, git_project):
    registry, _ = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    workspace.mkdir(parents=True, exist_ok=True)
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20)

    original = type(workspace).iterdir

    def denied(path):
        if Path(path).resolve() == workspace.resolve():
            raise PermissionError(13, "denied")
        return original(path)

    # The stat succeeds and the listing does not -- the second half of the same
    # question, which an empty-directory check would have read as "empty".
    import pytest as _pytest  # local: this monkeypatch is scoped to one assertion
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(workspace), "iterdir", denied)
        status = provisioner.status("alpha", "codex")

    assert status.state == WorkspaceState.ERROR
    assert status.error is not None and "PermissionError" in status.error


def test_provisioning_onto_an_unreadable_path_says_so_rather_than_blaming_a_race(tmp_path, git_project):
    registry, _ = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    env = _ProbeOverride(workspace, PathProbe("unknown", error=PermissionError(13, "denied")))
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=env)

    with pytest.raises(WorkspaceConflictError, match="could not be inspected"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


class _GitTimesOut:
    """Git starts and never finishes. Not the same as git being absent."""

    def __init__(self):
        self._inner = LocalEnvironment()

    def run(self, argv, *, cwd=None, timeout):
        return CommandResult("timeout")

    def probe(self, path, *, follow_symlinks=True):
        return self._inner.probe(path, follow_symlinks=follow_symlinks)


def test_git_that_times_out_is_unavailable_not_a_validation_failure(tmp_path, git_project):
    registry, _ = _system(tmp_path, git_project)
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=_GitTimesOut())

    with pytest.raises(WorkspaceUnavailableError, match="timed out"):
        asyncio.run(provisioner.ensure_source_repo("alpha"))


def test_git_that_hangs_is_reported_as_git_not_as_a_verdict_on_the_project(
    tmp_path, git_project
):
    """`.ran` is true for a timeout, so a hanging git used to pass the availability
    check and surface later as "Project root is not a Git repository" -- a statement
    about the operator's project, drawn from a fact about our tooling."""
    registry, _ = _system(tmp_path, git_project)
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=_GitTimesOut())

    with pytest.raises(WorkspaceUnavailableError, match="git is not usable"):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))


def test_a_stored_provisioning_error_does_not_swallow_the_present_one(tmp_path, git_project):
    """Two different facts: why a past attempt failed, and what cannot be established
    now. Reporting only the first sends the operator after a cause that may be long
    gone; reporting only the second loses why the workspace is in this state."""
    registry, _ = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    env = _ProbeOverride(workspace, PathProbe("unknown", error=PermissionError(13, "denied")))
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=env)
    # Stand in for a failed earlier provision, which is what fills this map.
    provisioner._errors[("alpha", "codex")] = "Workspace branch is already checked out elsewhere"

    status = provisioner.status("alpha", "codex")

    assert status.state == WorkspaceState.ERROR
    # Stored first, current second: the order is the sentence, "this is why it was
    # left like this; this is what is in the way now".
    assert status.error.startswith("Workspace branch is already checked out elsewhere; ")
    assert "PermissionError" in status.error


def test_the_same_obstacle_is_not_reported_as_two_facts(tmp_path, git_project):
    """A provisioning attempt that failed *because* the path was unreadable stores
    that cause; the next inspection hits the same wall. Joining them would present one
    obstacle as a stale fact plus a current one."""
    registry, _ = _system(tmp_path, git_project)
    workspace = registry.get("alpha").workspaces["codex"]
    env = _ProbeOverride(workspace, PathProbe("unknown", error=PermissionError(13, "denied")))
    provisioner = WorkspaceProvisioner(registry, timeout_seconds=20, environment=env)

    # Let `provision()` actually fail and store its own message, rather than planting
    # the string the assertion wants. Planting it would pass even if the two call
    # sites drifted back to two different wordings -- which is the whole invariant
    # `_uninspectable` exists to hold.
    with pytest.raises(WorkspaceConflictError):
        asyncio.run(provisioner.provision(ProvisionRequest(project_id="alpha", adapter="codex")))

    status = provisioner.status("alpha", "codex")

    assert status.state == WorkspaceState.ERROR
    assert status.error.count("PermissionError") == 1
