"""Removing a project from Agora, and keeping the tools out of the import list (CR-56).

The person: "Agora 不应该再出现在项目里了" — Agora itself and the two workspace tools
should not be projects. Removing is the registration only; it never touches the
repository. It is refused, with the reason and the counts, whenever it would leave
something pointing at nothing or would be undone at the next start.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.execution.environment_registry_models import RemoteEnvironmentRecord
from agora.execution.environment_registry_store import RemoteEnvironmentStore
from agora.projects import ProjectRegistry
from agora.projects.import_candidates import find_candidates, tool_directory_check
from agora.projects.registry import (
    LastProjectError,
    ProjectInUseError,
    ProjectListedInImportSourceError,
)
from agora.projects.store import SETTING_CURRENT_PROJECT
from agora.tasks.models import CreateTaskRequest
from agora.tasks.router import get_project_registry
from agora.tasks.store import TaskStore


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("mine\n", encoding="utf-8")
    return path


def _task(source: Path, name: str, text: str) -> None:
    task_dir = source / "tasks" / name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.yaml").write_text(text, encoding="utf-8")


def _registry(tmp_path: Path, *, source: Path | None = None) -> ProjectRegistry:
    config = {
        "projects": {
            "registry_path": str(source or tmp_path / "cfg" / "projects.yaml"),
            "default": "home",
            "projects": {"home": {"name": "Home", "root": str(tmp_path / "home")}},
        }
    }
    registry = ProjectRegistry(config)
    registry.set_projects_base(tmp_path / "base")
    return registry


def _snapshot(root: Path) -> list[tuple[str, bytes | None]]:
    return sorted(
        (str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
        for p in root.rglob("*")
    )


# --- the registry ---------------------------------------------------------------------

def test_removal_drops_the_registration_and_touches_no_file(tmp_path):
    registry = _registry(tmp_path)
    repo = _git_repo(tmp_path / "Projects" / "gone")
    project = registry.add("gone", repo)
    before = _snapshot(repo)
    assert (repo / ".agora" / "research").is_dir()  # created by registering

    current = registry.remove_project("gone")

    assert "gone" not in registry.list_projects()
    assert current == "home"
    # The repository, including the .agora/ folders Agora itself created, is untouched.
    assert _snapshot(repo) == before
    assert project.root == repo.resolve()


def test_removal_clears_today_focus_and_moves_the_pointer_without_a_warning(tmp_path, capsys):
    registry = _registry(tmp_path)
    registry.add("alpha", _git_repo(tmp_path / "alpha"))
    registry.add("beta", _git_repo(tmp_path / "beta"))
    registry.promote_to_focus("alpha")
    registry.promote_to_focus("beta")
    registry.use("beta")
    registry.store.set_setting("kickoff_adopted:beta", "fingerprint:wrote")
    registry.store.set_setting("kickoff_adopted:alpha", "fingerprint:wrote")
    capsys.readouterr()

    assert registry.remove_project("beta") == "home"  # the configured default survives

    focus = registry.today_focus()
    assert focus.project_ids == ["alpha"] and focus.unresolved_ids == []
    assert registry.current_project_id() == "home"
    assert registry.store.get_setting(SETTING_CURRENT_PROJECT) == "home"
    # Its per-project marker goes with it; another project's stays.
    assert registry.store.get_setting("kickoff_adopted:beta") is None
    assert registry.store.get_setting("kickoff_adopted:alpha") == "fingerprint:wrote"
    # The startup repair's stderr warning is for out-of-band edits, not this.
    assert capsys.readouterr().err == ""
    # And a fresh registry over the same database does not find anything to repair.
    ProjectRegistry(registry.config)
    assert capsys.readouterr().err == ""


def test_removing_a_project_that_is_not_current_leaves_the_pointer(tmp_path):
    registry = _registry(tmp_path)
    registry.add("alpha", _git_repo(tmp_path / "alpha"))
    registry.use("alpha")
    registry.add("beta", _git_repo(tmp_path / "beta"))
    assert registry.remove_project("beta") == "alpha"
    assert registry.current_project_id() == "alpha"


def test_the_last_project_is_never_removed(tmp_path):
    registry = _registry(tmp_path)
    assert list(registry.list_projects()) == ["home"]
    with pytest.raises(LastProjectError) as raised:
        registry.remove_project("home")
    assert "only project left" in str(raised.value) and "nothing was removed" in str(raised.value)
    assert list(registry.list_projects()) == ["home"]


def test_an_unknown_project_is_a_key_error(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(KeyError):
        registry.remove_project("nope")


def test_a_project_still_referenced_is_refused_with_the_counts(tmp_path):
    registry = _registry(tmp_path)
    registry.add("busy", _git_repo(tmp_path / "busy"))
    tasks = TaskStore(registry.store.db_path)
    tasks.create(CreateTaskRequest(project_id="busy", title="one"))
    tasks.create(CreateTaskRequest(project_id="busy", title="two"))
    RemoteEnvironmentStore(registry.store.db_path).insert(RemoteEnvironmentRecord(
        environment_id="ai-server", label="AI server", ssh_host="h", ssh_user="u",
        remote_workspace="/w", remote_project_root="/w/busy", secret_ref="AGORA_X",
        project_id="busy",
    ))

    with pytest.raises(ProjectInUseError) as raised:
        registry.remove_project("busy")

    assert raised.value.counts == {"remote_environments": 1, "tasks": 2}
    message = str(raised.value)
    assert "2 task(s)" in message and "1 remote environment binding(s)" in message
    assert message.endswith("nothing was removed")
    assert "busy" in registry.list_projects()


def test_tables_are_discovered_not_listed(tmp_path):
    """A table no one told the store about still blocks the removal."""
    registry = _registry(tmp_path)
    registry.add("busy", _git_repo(tmp_path / "busy"))
    with sqlite3.connect(registry.store.db_path) as db:
        db.execute("CREATE TABLE some_future_table (id INTEGER PRIMARY KEY, project_id TEXT)")
        db.execute("INSERT INTO some_future_table (project_id) VALUES ('busy')")
    with pytest.raises(ProjectInUseError) as raised:
        registry.remove_project("busy")
    assert raised.value.counts == {"some_future_table": 1}
    assert "1 row(s) in some_future_table" in str(raised.value)


def test_a_project_the_import_file_lists_is_refused(tmp_path):
    source = tmp_path / "cfg" / "projects.yaml"
    source.parent.mkdir(parents=True)
    alpha = _git_repo(tmp_path / "alpha")
    beta = _git_repo(tmp_path / "beta")
    source.write_text(
        f"projects:\n  alpha:\n    root: {alpha.as_posix()}\n  beta:\n    root: {beta.as_posix()}\n",
        encoding="utf-8",
    )
    registry = _registry(tmp_path, source=source)
    with pytest.raises(ProjectListedInImportSourceError) as raised:
        registry.remove_project("beta")
    assert str(source) in str(raised.value)
    assert "beta" in registry.list_projects()


def test_an_unreadable_import_file_is_not_read_as_not_listing_it(tmp_path):
    source = tmp_path / "cfg" / "projects.yaml"
    source.parent.mkdir(parents=True)
    alpha = _git_repo(tmp_path / "alpha")
    source.write_text(f"projects:\n  alpha:\n    root: {alpha.as_posix()}\n", encoding="utf-8")
    registry = _registry(tmp_path, source=source)
    registry.add("beta", _git_repo(tmp_path / "beta"))
    source.write_text("projects: [", encoding="utf-8")  # broken after the fact
    with pytest.raises(ProjectListedInImportSourceError) as raised:
        registry.remove_project("beta")
    assert raised.value.readable is False and "unknown" in str(raised.value)
    assert "beta" in registry.list_projects()


# --- the endpoint ---------------------------------------------------------------------

@pytest.fixture
def api(tmp_path):
    registry = _registry(tmp_path)
    app.dependency_overrides[get_project_registry] = lambda: registry
    try:
        yield TestClient(app), registry
    finally:
        app.dependency_overrides.clear()


def test_delete_endpoint_removes_and_reports_the_current_project(api, tmp_path):
    client, registry = api
    registry.add("gone", _git_repo(tmp_path / "gone"))
    registry.use("gone")
    resp = client.delete("/api/projects/gone")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"removed": "gone", "current_project_id": "home"}
    assert client.get("/api/projects").json()["current"] == "home"


def test_delete_endpoint_refusals(api, tmp_path):
    client, registry = api
    assert client.delete("/api/projects/nope").status_code == 404

    last = client.delete("/api/projects/home")
    assert last.status_code == 409
    assert last.json()["detail"]["code"] == "last_project"

    registry.add("busy", _git_repo(tmp_path / "busy"))
    TaskStore(registry.store.db_path).create(CreateTaskRequest(project_id="busy", title="t"))
    busy = client.delete("/api/projects/busy")
    assert busy.status_code == 409
    detail = busy.json()["detail"]
    assert detail["code"] == "project_in_use" and detail["counts"] == {"tasks": 1}
    assert "1 task(s)" in detail["message"] and "nothing was removed" in detail["message"]
    assert "busy" in registry.list_projects()


def test_delete_endpoint_is_closed_to_other_web_pages(api, tmp_path):
    """CR-34: a page on another origin cannot remove a project from the person's browser."""
    client, registry = api
    registry.add("gone", _git_repo(tmp_path / "gone"))
    resp = client.delete("/api/projects/gone", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert "gone" in registry.list_projects()


# --- keeping the tools out of the import list -----------------------------------------

def _agora_checkout(path: Path) -> Path:
    _git_repo(path)
    (path / "backend" / "agora").mkdir(parents=True)
    (path / "backend" / "agora" / "__init__.py").write_text("", encoding="utf-8")
    return path


def _claude_tool(path: Path) -> Path:
    _git_repo(path)
    (path / "scripts").mkdir()
    (path / "scripts" / "workroot.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def _templated_workspace(path: Path) -> Path:
    """A claude-workspace tool directory in the split layout: its CLI, no resolver."""
    _git_repo(path)
    (path / "scripts").mkdir()
    (path / "scripts" / "cw.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / ".workroot").write_text("E:/Work\n", encoding="utf-8")
    return path


def _repo_with_own_task_template(path: Path) -> Path:
    """An ordinary project that happens to keep a tasks/_template of its own."""
    _git_repo(path)
    (path / "tasks" / "_template").mkdir(parents=True)
    return path


def test_tool_directories_are_listed_but_never_offered(tmp_path):
    agora = _agora_checkout(tmp_path / "Agora-private")
    tool = _claude_tool(tmp_path / "claude-workspace")
    templated = _templated_workspace(tmp_path / "templated")
    plain = _git_repo(tmp_path / "work")
    own_template = _repo_with_own_task_template(tmp_path / "has-template")
    src = _git_repo(tmp_path / "kiro-workspace")  # a workspace source that is itself a repo
    for name, root in (("a", agora), ("t", tool), ("tm", templated), ("p", plain), ("ot", own_template), ("s", src)):
        _task(src, name, f"task:\n  title: {name}\nprimary_repo: {root.as_posix()}\n")

    candidates, unreadable = find_candidates([src], {})
    by = {Path(c.root).name: c for c in candidates}
    assert by["Agora-private"].status == "agora_tool"
    assert by["claude-workspace"].status == "agora_tool"
    assert by["templated"].status == "agora_tool"
    assert by["kiro-workspace"].status == "agora_tool"
    assert all(by[n].suggested_id is None for n in ("Agora-private", "claude-workspace", "templated", "kiro-workspace"))
    assert by["work"].status == "ok"
    assert by["has-template"].status == "ok"
    assert unreadable == []


def test_an_already_registered_agora_is_reported_as_the_project_it_is(tmp_path):
    """So it can be found and removed, rather than hidden behind the tool status."""
    import os

    agora = _agora_checkout(tmp_path / "Agora")
    src = tmp_path / "claude"
    _task(src, "a", f"task:\n  title: a\nprimary_repo: {agora.as_posix()}\n")
    (candidate,), _ = find_candidates([src], {os.path.normcase(str(agora.resolve())): "agora"})
    assert candidate.status == "registered" and candidate.registered_as == "agora"


def test_a_marker_that_cannot_be_probed_is_not_read_as_not_a_tool(tmp_path, monkeypatch):
    from agora.fsprobe import PathProbe
    from agora.projects import import_candidates

    repo = _git_repo(tmp_path / "r")
    real = import_candidates.probe_path

    def probe(path):
        if path.name == "workroot.sh":
            return PathProbe("unknown", error=PermissionError(13, "denied"))
        return real(path)

    monkeypatch.setattr(import_candidates, "probe_path", probe)
    check = tool_directory_check(repo, [])
    assert check.kind is None and check.undecided
    src = tmp_path / "claude"
    _task(src, "r", f"task:\n  title: r\nprimary_repo: {repo.as_posix()}\n")
    (candidate,), _ = find_candidates([src], {})
    assert candidate.status == "unreadable" and candidate.suggested_id is None


def test_register_refuses_tool_directories(api, tmp_path):
    client, registry = api
    agora = _agora_checkout(tmp_path / "Agora-stable")
    tool = _claude_tool(tmp_path / "claude-workspace")
    templated = _templated_workspace(tmp_path / "templated")
    source = _git_repo(tmp_path / "kiro-workspace")
    registry.register_workspace_source(source)
    for pid, root in (("a", agora), ("t", tool), ("tm", templated), ("s", source)):
        resp = client.post("/api/projects/register", json={"project_id": pid, "root": str(root)})
        assert resp.status_code == 422, (pid, resp.text)
        assert "workspace tool" in resp.text and "nothing was registered" in resp.text
        assert pid not in registry.list_projects()
        assert not (root / ".agora").exists()  # refused before anything was created
    ok = client.post("/api/projects/register", json={"project_id": "w", "root": str(_git_repo(tmp_path / "w"))})
    assert ok.status_code == 201, ok.text
