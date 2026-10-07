"""Bringing the person's existing projects into Agora from their workspaces (CR-52).

The workspaces already record where each project lives — claude-workspace's
``primary_repo`` / ``repos[].path``, kiro-workspace's ``project_path`` — in the several
spellings people actually write. These tests drive the reader against both shapes and
the register endpoint against a real git repository.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.projects.import_candidates import find_candidates, local_path_of
from agora.tasks.router import get_project_registry


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("mine\n", encoding="utf-8")
    return path


def _task(source: Path, name: str, text: str) -> None:
    task_dir = source / "tasks" / name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.yaml").write_text(text, encoding="utf-8")


def _registry(tmp_path: Path) -> ProjectRegistry:
    config = {
        "projects": {
            "registry_path": str(tmp_path / "cfg" / "projects.yaml"),
            "default": "home",
            "projects": {"home": {"name": "Home", "root": str(tmp_path / "home")}},
        }
    }
    registry = ProjectRegistry(config)
    registry.set_projects_base(tmp_path / "base")
    return registry


# --- reading the paths --------------------------------------------------------------

@pytest.fixture
def on_windows(monkeypatch):
    from agora.projects import import_candidates
    monkeypatch.setattr(import_candidates, "_ON_WINDOWS", True)


@pytest.fixture
def on_posix(monkeypatch):
    from agora.projects import import_candidates
    monkeypatch.setattr(import_candidates, "_ON_WINDOWS", False)


@pytest.mark.skipif(not __import__("os").name == "nt", reason="Windows path semantics")
@pytest.mark.parametrize("raw,expected", [
    ("E:/Work/2026/Projects/x", "E:/Work/2026/Projects/x"),
    ("E:\\Work\\x", "E:\\Work\\x"),
    ("/e/Work/2026/Projects/x", "E:/Work/2026/Projects/x"),
    ('"E:/Files/仕事/x"', "E:/Files/仕事/x"),
])
def test_local_spellings_are_understood(raw, expected, on_windows):
    assert local_path_of(raw) == Path(expected)


@pytest.mark.parametrize("raw", ["~/Agora", "/workplace/ai-projects/x", "<edit-me>/src", "/path/to/example", ""])
def test_paths_that_cannot_be_here_are_not_local(raw, on_windows):
    assert local_path_of(raw) is None


def test_both_workspace_shapes_become_one_candidate_per_repository(tmp_path):
    repo = _git_repo(tmp_path / "Projects" / "signal-hunt")
    plain = tmp_path / "Projects" / "notes"
    plain.mkdir(parents=True)
    claude, kiro = tmp_path / "claude", tmp_path / "kiro"
    _task(claude, "signal-hunt", f"task:\n  title: Signal Hunt\nprimary_repo: {repo}\nrepos:\n  - path: {repo}\n")
    _task(claude, "on-linux", "task:\n  title: x\nprimary_repo: ~/Agora\nrepos:\n  - path: ~/Agora\n")
    _task(kiro, "sh-kiro", f"name: sh-kiro\nproject_path: {repo}\n")
    _task(kiro, "notes", f"name: notes\nproject_path: \"{plain.as_posix()}\"\n")  # YAML "" would read \U as an escape
    _task(kiro, "example", "name: example\nproject_path: /path/to/example-project\n")
    _task(kiro, "gone", f"name: gone\nproject_path: {tmp_path / 'nope'}\n")

    candidates, unreadable = find_candidates([claude, kiro], {})
    by = {(c.root or c.raw_path): c for c in candidates}
    ok = by[str(repo.resolve())]
    assert ok.status == "ok" and ok.suggested_id == "signal-hunt" and ok.suggested_name == "Signal Hunt"
    assert sorted(t.task for t in ok.tasks) == ["sh-kiro", "signal-hunt"]  # one repo, both records
    assert by[str(plain.resolve())].status == "not_git"
    assert by[str((tmp_path / "nope").resolve())].status == "missing"
    if os.name == "nt":  # on Linux / macOS ~/Agora is this machine's home
        assert by["~/Agora"].status == "not_this_machine"
        assert [c.raw_path for c in candidates].count("~/Agora") == 1  # primary_repo == repos[].path
    assert by["/path/to/example-project"].status == "placeholder"
    assert unreadable == []


def test_an_already_registered_root_says_so_and_ids_avoid_taken_ones(tmp_path):
    repo = _git_repo(tmp_path / "Projects" / "work-chat")
    other = _git_repo(tmp_path / "elsewhere" / "Home")
    src = tmp_path / "claude"
    _task(src, "work-chat", f"task:\n  title: w\nprimary_repo: {repo}\n")
    _task(src, "home2", f"task:\n  title: h\nprimary_repo: {other}\n")
    import os
    registered = {os.path.normcase(str(repo.resolve())): "chat"}
    candidates, _ = find_candidates([src], {**registered, "x": "home"})
    by = {c.root: c for c in candidates}
    assert by[str(repo.resolve())].status == "registered" and by[str(repo.resolve())].registered_as == "chat"
    assert by[str(other.resolve())].suggested_id == "home-2"  # "home" is taken


def test_an_unreadable_task_file_is_listed_not_skipped(tmp_path):
    src = tmp_path / "claude"
    (src / "tasks" / "broken").mkdir(parents=True)
    (src / "tasks" / "broken" / "task.yaml").write_bytes(b"task: [\xff\xfe")
    candidates, unreadable = find_candidates([src], {})
    assert candidates == [] and unreadable and unreadable[0]["task"] == "broken"


# --- the endpoints ------------------------------------------------------------------

@pytest.fixture
def api(tmp_path):
    registry = _registry(tmp_path)
    app.dependency_overrides[get_project_registry] = lambda: registry
    try:
        yield TestClient(app), registry
    finally:
        app.dependency_overrides.clear()


def test_candidates_endpoint_reads_the_registered_workspaces(api, tmp_path):
    client, registry = api
    repo = _git_repo(tmp_path / "Projects" / "pain-radar")
    src = tmp_path / "claude"
    _task(src, "pain-radar", f"task:\n  title: Pain Radar\nprimary_repo: {repo}\n")
    registry.register_workspace_source(src)
    body = client.get("/api/projects/import-candidates").json()
    (candidate,) = body["candidates"]
    assert candidate["status"] == "ok" and candidate["suggested_id"] == "pain-radar"


def test_register_adopts_an_existing_repository_without_touching_its_files(api, tmp_path):
    client, registry = api
    repo = _git_repo(tmp_path / "Projects" / "signal-hunt")
    before = sorted(p.name for p in repo.iterdir())
    resp = client.post("/api/projects/register", json={"project_id": "signal-hunt", "root": str(repo), "name": "Signal Hunt"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["worktrees_excluded"] is True
    assert registry.get("signal-hunt").root == repo.resolve()
    # Only Agora's own folder was added; the person's files are as they were.
    assert sorted(p.name for p in repo.iterdir()) == sorted(before + [".agora"])
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".agora/workspaces/" in exclude
    # git status stays clean of Agora's worktree folders.
    (repo / ".agora" / "workspaces" / "claude" / "f.txt").write_text("x", encoding="utf-8")
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert ".agora/workspaces" not in status


def test_register_refuses_what_is_not_a_repository_and_repeats(api, tmp_path):
    client, registry = api
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.post("/api/projects/register", json={"project_id": "p", "root": str(plain)}).status_code == 422
    assert client.post("/api/projects/register", json={"project_id": "q", "root": str(tmp_path / "nope")}).status_code == 422
    assert not (plain / ".git").exists()  # never git-initialised
    repo = _git_repo(tmp_path / "r")
    assert client.post("/api/projects/register", json={"project_id": "r", "root": str(repo)}).status_code == 201
    again = client.post("/api/projects/register", json={"project_id": "r2", "root": str(repo)})
    assert again.status_code == 409 and "Already registered as project r" in again.text


def test_on_linux_native_paths_are_local_and_drive_paths_are_not(on_posix):
    """review: Agora runs on the Linux machines too."""
    assert local_path_of("/workplace/ai-projects/x") == Path("/workplace/ai-projects/x")
    assert local_path_of("~/Agora") == Path("~/Agora").expanduser()
    assert local_path_of("E:/Work/x") is None
    assert local_path_of("/path/to/example") is None


def test_an_empty_or_stale_git_entry_is_not_a_repository(tmp_path):
    """review: `.git` existing proves nothing — git itself must say it is a root."""
    from agora.projects.import_candidates import git_worktree_root_status

    empty = tmp_path / "empty"
    (empty / ".git").mkdir(parents=True)
    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / ".git").write_text("gitdir: /nowhere/at/all\n", encoding="utf-8")
    inner = _git_repo(tmp_path / "outer") / "sub"
    inner.mkdir()
    assert git_worktree_root_status(empty) == "not_git"
    assert git_worktree_root_status(stale) == "not_git"
    assert git_worktree_root_status(inner) == "not_git"  # inside a repo, not its root
    assert git_worktree_root_status(tmp_path / "outer") == "ok"


def test_register_refuses_an_empty_git_folder(api, tmp_path):
    client, registry = api
    fake = tmp_path / "fake"
    (fake / ".git").mkdir(parents=True)
    resp = client.post("/api/projects/register", json={"project_id": "fake", "root": str(fake)})
    assert resp.status_code == 422 and "Not the root of a git repository" in resp.text
    assert "fake" not in registry.list_projects()


def test_an_uninspectable_manifest_is_listed_not_dropped(tmp_path, monkeypatch):
    from agora.fsprobe import PathProbe
    from agora.projects import import_candidates

    src = tmp_path / "claude"
    _task(src, "locked", "task:\n  title: x\n")
    real = import_candidates.probe_path

    def probe(path):
        if path.name == "task.yaml":
            return PathProbe("unknown", error=PermissionError(13, "denied"))
        return real(path)

    monkeypatch.setattr(import_candidates, "probe_path", probe)
    candidates, unreadable = find_candidates([src], {})
    assert candidates == [] and unreadable and unreadable[0]["task"] == "locked"


def test_registration_is_all_or_nothing_when_agora_folders_cannot_be_made(api, tmp_path):
    """review: a file named .agora blocks the folders; the project must not be stored."""
    client, registry = api
    repo = _git_repo(tmp_path / "blocked")
    (repo / ".agora").write_text("not a folder", encoding="utf-8")
    resp = client.post("/api/projects/register", json={"project_id": "blocked", "root": str(repo)})
    assert resp.status_code == 422 and "nothing was registered" in resp.text
    assert "blocked" not in registry.list_projects()
