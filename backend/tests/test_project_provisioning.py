"""Creating a project provisions its agent workspaces as linked git worktrees,
and provisioning never sources from an outer repository the project sits in."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.api.settings_router import _projects_overview
from agora.execution.deliverables import _is_provisioned_worktree
from agora.fsprobe import PathProbe
from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry
from agora.workspaces.models import ProvisionRequest
from agora.workspaces.provisioner import WorkspaceProvisioner, WorkspaceValidationError

from tests.acl_support import deny_self_traverse, restore_traverse

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


def _owned_project(registry: ProjectRegistry, root: Path, project_id: str):
    """A project the calling test builds for itself.

    A test that asserts "this root is not a repository" or "this directory is
    refused" must own the directory it is talking about. The project the registry
    seeds from configuration is not owned by the test: it is whatever the store
    already held, and on a shared control-plane database that is the real Agora
    project, whose root *is* a git repository. Asserting a definite negative about
    it pins nothing and turns the outcome into a function of which tests ran first.
    """
    return registry.add(project_id, root)


def _owned_id(prefix: str) -> str:
    """A project id no other test can already have taken.

    The store a test's registry resolves to is not guaranteed to be empty, so a
    fixed id can collide with a row another test wrote — which would refuse the
    creation for the wrong reason and read as a failure of what is being pinned.
    """
    return f"{prefix}{uuid4().hex[:8]}"


def test_charter_endpoint_sets_and_returns(tmp_path):
    from agora.tasks.router import get_project_registry

    registry = _registry(tmp_path)
    app.dependency_overrides[get_project_registry] = lambda: registry
    client = TestClient(app)
    try:
        resp = client.put("/api/projects/agora/charter", json={
            "goal": "Ship a small tools library",
            "non_goals": ["no general-purpose library"],
            "success_criteria": ["every function tested", "reviewed by a different agent"],
        })
        assert resp.status_code == 200, resp.text
        charter = resp.json()["charter"]
        assert charter["goal"] == "Ship a small tools library"
        assert charter["success_criteria"] == ["every function tested", "reviewed by a different agent"]

        # blank items are stripped
        resp2 = client.put("/api/projects/agora/charter", json={
            "goal": "G2", "non_goals": ["  ", ""], "success_criteria": [],
        })
        assert resp2.json()["charter"]["non_goals"] == []

        # it shows up in the projects listing too
        listing = client.get("/api/projects").json()
        agora = next(p for p in listing["items"] if p["project_id"] == "agora")
        assert agora["charter"]["goal"] == "G2"

        assert client.put("/api/projects/nope/charter", json={"goal": "x"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_project_dashboard_progress_vs_verified(tmp_path):
    from agora.tasks.router import get_project_registry, get_task_store
    from agora.tasks.store import TaskStore
    from agora.tasks.models import CreateTaskRequest, TaskState
    from agora.execution.router import get_execution_store
    from agora.execution.store import ExecutionStore
    from agora.execution.models import CreateRunRequest, RunState
    from agora.requirements.store import RequirementStore
    from agora.requirements.models import CreateSpecRequest, RequirementItem, AcceptanceScenario

    registry = _registry(tmp_path)
    tasks = TaskStore(tmp_path / "dash.db")
    runs = ExecutionStore(tasks)
    specs = RequirementStore(tasks)

    # one task carried to DONE with a 2-criterion approved spec -> fully verified
    t = tasks.create(CreateTaskRequest(project_id="agora", title="done task", created_by="t"))
    t = tasks.transition(t.task_id, TaskState.REQUIREMENTS, actor="t", expected_version=t.version)
    spec = specs.create(t.task_id, CreateSpecRequest(
        title="s", summary="s",
        functional=[RequirementItem(requirement_id="FR-1", statement="does a thing")],
        acceptance_scenarios=[
            AcceptanceScenario(scenario_id="AC-1", requirement_ids=["FR-1"], given="g", when="w", then="t"),
            AcceptanceScenario(scenario_id="AC-2", requirement_ids=["FR-1"], given="g", when="w", then="t"),
        ], created_by="t"))
    specs.approve(spec.spec_id, actor="t", expected_revision=spec.revision, reason="ok")
    # Verified weight requires evidence, not just a lifecycle state: give the
    # task a real succeeded worker run before carrying it to DONE. Walking it to
    # DONE with no run at all is the false-confidence case covered below.
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state=?, version=version+1 WHERE task_id=?",
                   (TaskState.PLANNED.value, t.task_id))
    planned = tasks.get(t.task_id)
    run = runs.create(
        CreateRunRequest(task_id=t.task_id, adapter="codex", prompt="do the thing",
                         expected_task_version=planned.version),
        project_id="agora", workspace=tmp_path, stored_command=["codex", "exec"],
    )
    run = runs.start(run.run_id, expected_version=run.version)
    runs.finish(run.run_id, RunState.SUCCEEDED, expected_version=run.version, exit_code=0,
                stdout_tail="done", stderr_tail="")
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state=? WHERE task_id=?", (TaskState.DONE.value, t.task_id))

    # a second, unspecified task still in backlog -> pending, counts as one unit
    tasks.create(CreateTaskRequest(project_id="agora", title="backlog task", created_by="t"))

    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: runs
    client = TestClient(app)
    try:
        resp = client.get("/api/projects/agora/dashboard")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 3 units total (2 verified criteria + 1 pending unspecified task):
        assert body["metrics"]["verified_pct"] == 67   # 2/3
        assert body["metrics"]["progress_pct"] == 67    # produced = same 2/3 here
        assert body["metrics"]["health"] == "normal"
        done = next(tk for tk in body["tasks"] if tk["title"] == "done task")
        assert len(done["criteria"]) == 2 and all(c["status"] == "verified" for c in done["criteria"])
        assert done["next_action"]["action"] == "none"
        backlog = next(tk for tk in body["tasks"] if tk["title"] == "backlog task")
        assert backlog["next_action"]["action"] == "start_requirements"
        # Project-wide: the only actionable task is the backlog one.
        assert body["next_action"]["task_id"] == backlog["task_id"]
        assert body["next_action"]["action"] == "start_requirements"
        assert body["next_action"]["attention_count"] == 0
        assert body["next_action"]["partial"] is False
        assert client.get("/api/projects/nope/dashboard").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_dashboard_next_action_routes_rejected_spec_to_revise(tmp_path):
    from agora.tasks.router import get_project_registry, get_task_store
    from agora.tasks.store import TaskStore
    from agora.tasks.models import CreateTaskRequest, TaskState
    from agora.execution.router import get_execution_store
    from agora.execution.store import ExecutionStore
    from agora.requirements.store import RequirementStore
    from agora.requirements.models import CreateSpecRequest, RequirementItem, AcceptanceScenario

    registry = _registry(tmp_path)
    tasks = TaskStore(tmp_path / "reject.db")
    runs = ExecutionStore(tasks)
    specs = RequirementStore(tasks)

    t = tasks.create(CreateTaskRequest(project_id="agora", title="rej", created_by="t"))
    t = tasks.transition(t.task_id, TaskState.REQUIREMENTS, actor="t", expected_version=t.version)
    spec = specs.create(t.task_id, CreateSpecRequest(
        title="s", summary="s",
        functional=[RequirementItem(requirement_id="FR-1", statement="does a thing")],
        acceptance_scenarios=[
            AcceptanceScenario(scenario_id="AC-1", requirement_ids=["FR-1"], given="g", when="w", then="t"),
        ], created_by="t"))
    specs.reject(spec.spec_id, actor="t", expected_revision=spec.revision, reason="no")
    # current() now returns None (only draft/approved), so the next action must
    # still recover the rejected latest spec and route to revise, not draft.
    assert specs.current(t.task_id) is None

    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: runs
    client = TestClient(app)
    try:
        body = client.get("/api/projects/agora/dashboard").json()
        task_view = next(tk for tk in body["tasks"] if tk["task_id"] == t.task_id)
        assert task_view["next_action"]["action"] == "revise_spec"
    finally:
        app.dependency_overrides.clear()


def test_dashboard_next_action_never_hides_a_blocker_past_the_page(tmp_path, monkeypatch):
    from agora.api import settings_router
    from agora.tasks.router import get_project_registry, get_task_store
    from agora.tasks.store import TaskStore
    from agora.tasks.models import CreateTaskRequest, TaskState
    from agora.execution.router import get_execution_store
    from agora.execution.store import ExecutionStore

    # Shrink the page cap so a small fixture triggers truncation deterministically.
    monkeypatch.setattr(settings_router, "_DASHBOARD_TASK_LIMIT", 3)

    registry = _registry(tmp_path)
    tasks = TaskStore(tmp_path / "trunc.db")
    runs = ExecutionStore(tasks)

    # High-priority routine tasks fill the page; a low-priority blocked task sorts
    # last (priority DESC) and falls off it.
    for i in range(3):
        tasks.create(CreateTaskRequest(project_id="agora", title=f"routine-{i}", priority=100, created_by="t"))
    b = tasks.create(CreateTaskRequest(project_id="agora", title="late-blocker", priority=0, created_by="t"))
    b = tasks.transition(b.task_id, TaskState.REQUIREMENTS, actor="t", expected_version=b.version)
    tasks.transition(b.task_id, TaskState.BLOCKED, actor="t", expected_version=b.version)

    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: runs
    client = TestClient(app)
    try:
        body = client.get("/api/projects/agora/dashboard").json()
        assert body["truncated"] is True
        # The blocker is not on the page, yet attention must still surface.
        assert b.task_id not in {tk["task_id"] for tk in body["tasks"]}
        na = body["next_action"]
        assert na["task_id"] == b.task_id and na["action"] == "resolve_blocker"
        assert na["attention_count"] == 1 and na["partial"] is True
    finally:
        app.dependency_overrides.clear()


def test_dashboard_carries_the_progress_snapshot_as_its_own_claim(tmp_path):
    """The progress bar's source travels with the dashboard, and says which of the
    three answers it is. The bar renders no number unless `available` is true, so a
    field that were merely missing would be read as "no snapshot" — the reason this
    key is always present."""
    from agora.projects.progress_snapshot import SNAPSHOT_RELATIVE_PATH
    from agora.tasks.router import get_project_registry, get_task_store
    from agora.tasks.store import TaskStore
    from agora.execution.router import get_execution_store
    from agora.execution.store import ExecutionStore

    registry = _registry(tmp_path)
    tasks = TaskStore(tmp_path / "snap.db")
    runs = ExecutionStore(tasks)

    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: runs
    client = TestClient(app)
    try:
        # No snapshot on disk yet: absent, with no counts to render.
        body = client.get("/api/projects/agora/dashboard").json()
        assert body["progress_snapshot"]["available"] is False
        assert body["progress_snapshot"]["reason"] == "absent"
        assert body["progress_snapshot"]["done"] is None

        path = registry.get("agora").root / SNAPSHOT_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- \u2705 a\n- \u2705 b\n- \U0001f535 c\n- \u2b1c d\n", encoding="utf-8")
        body = client.get("/api/projects/agora/dashboard").json()
        snapshot = body["progress_snapshot"]
        assert snapshot["available"] is True
        assert (snapshot["done"], snapshot["in_progress"], snapshot["todo"]) == (2, 1, 1)
        assert snapshot["source"] == SNAPSHOT_RELATIVE_PATH.as_posix()
    finally:
        app.dependency_overrides.clear()


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


def test_create_project_refuses_a_directory_it_cannot_inspect(tmp_path, monkeypatch):
    """An unreadable directory is not an empty one, and this guard protects contents.

    The check was `root.exists() and root.is_dir() and any(root.iterdir())`, and
    `exists()` answers False for a stat this process may not complete. So a directory
    full of unmanaged files behind a denying ACL passed the guard and was adopted and
    git-inited — the exact outcome the guard exists to prevent. "Could not look" must
    refuse, not proceed.
    """
    registry = _registry(tmp_path)
    monkeypatch.setattr(
        "agora.projects.registry.probe_path",
        lambda path, **kwargs: PathProbe("unknown", error=PermissionError(13, "denied")),
    )
    with pytest.raises(ValueError, match="cannot be inspected"):
        registry.create_project("demo", "Demo")
    # Nothing was created on the way to the refusal.
    assert not (registry.projects_base() / "demo" / ".git").exists()


def test_create_project_refuses_a_directory_denied_by_the_filesystem(tmp_path):
    """The same refusal with the OS enforcing the denial, where the platform allows it."""
    registry = _registry(tmp_path)
    # Base and project id are this test's own, created here: what is asserted is the
    # refusal of a directory this test made and denied itself, never a directory the
    # registry was configured with or that another test left in the store.
    base = registry.set_projects_base(tmp_path / "denied-base")
    project_id = _owned_id("denied")
    squatter = base / project_id
    squatter.mkdir(parents=True)
    (squatter / "user-file.txt").write_text("do not touch", encoding="utf-8")
    if not deny_self_traverse(base):
        pytest.skip("this platform/account cannot deny itself traversal")
    try:
        try:
            registry.create_project(project_id, "Demo")
        except ValueError as exc:
            assert "cannot be inspected" in str(exc) or "not empty" in str(exc)
        else:
            pytest.skip("the deny did not take effect for this token (elevated or bypassed)")
    finally:
        # Restored before the assertions below and before pytest tears the temporary
        # tree down: a denial left in place would make the directory unreadable and
        # undeletable, so the test would leave garbage behind in the temp root.
        restore_traverse(base)
    # The unmanaged file survived and the directory was never turned into a repo.
    assert (squatter / "user-file.txt").read_text(encoding="utf-8") == "do not touch"
    assert not (squatter / ".git").exists()


def test_projects_overview_reports_unknown_rather_than_missing(tmp_path, monkeypatch):
    """Settings must not tell the operator a path is missing when it could not be read.

    `root_exists`, `root_is_git` and each workspace's `exists` were `is_dir()`/`exists()`
    booleans, so an ACL-denied root rendered as "missing" — a settled fact that sends
    someone to recreate a directory that is already there. They are tri-state now, and
    `writable` follows `exists`: the old code probed the *parent* whenever `is_dir()` was
    False, which could report an unreadable workspace as writable.
    """
    registry = _registry(tmp_path)
    monkeypatch.setattr(
        "agora.api.settings_router.probe_path",
        lambda path, **kwargs: PathProbe("unknown", error=PermissionError(13, "denied")),
    )
    item = next(i for i in _projects_overview(registry)["items"] if i["project_id"] == "agora")
    assert item["root_exists"] is None
    assert item["root_is_git"] is None
    assert item["workspaces"], "the fixture project has workspaces to report on"
    for workspace in item["workspaces"]:
        assert workspace["exists"] is None
        assert workspace["writable"] is None


def test_projects_overview_still_answers_definitely_when_it_can(tmp_path):
    # The tri-state must not make everything unknown: the UI keeps both of its confident
    # renderings, so a real absence is still False and a real directory still True.
    # The project asserted about is built here: "there is no `.git` under this root" is
    # only a fact about a root this test owns, and the seeded configured project's root
    # may well be a real repository.
    registry = _registry(tmp_path)
    project_id = _owned_id("overview")
    project = _owned_project(registry, tmp_path / "base" / project_id, project_id)

    def item() -> dict:
        return next(
            i
            for i in _projects_overview(registry)["items"]
            if i["project_id"] == project.project_id
        )

    # Nothing at `<root>/.git` yet, and that is a definite negative rather than unknown.
    assert item()["root_is_git"] is False
    (project.root / ".git").mkdir(parents=True, exist_ok=True)
    after = item()
    assert after["root_is_git"] is True
    assert after["root_exists"] is True
    # The registry creates each adapter's workspace directory, so these are observed.
    assert after["workspaces"], "the fixture project has workspaces to report on"
    assert all(workspace["exists"] is True for workspace in after["workspaces"])


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
