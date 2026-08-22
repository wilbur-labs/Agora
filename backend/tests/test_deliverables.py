from __future__ import annotations

import pytest

from agora.execution.deliverables import list_changes, read_file


def test_read_file_returns_confined_bounded_content(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("hello world", encoding="utf-8")
    result = read_file(tmp_path, "docs/note.md")
    assert result["path"] == "docs/note.md"
    assert result["content"] == "hello world"
    assert result["truncated"] is False


def test_read_file_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises((PermissionError, FileNotFoundError)):
        read_file(tmp_path, "../outside-secret.txt")


def test_read_file_missing_is_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path, "does-not-exist.md")


def _git(git, *args, cwd):
    import subprocess
    subprocess.run([git, "-C", str(cwd), *args], check=True, capture_output=True, timeout=10)


def _source_repo(git, root):
    """A git repo with one commit — a valid `git worktree add` source."""
    root.mkdir(parents=True, exist_ok=True)
    _git(git, "init", "-q", cwd=root)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git(git, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=root)
    _git(git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed", cwd=root)


def _require_git():
    import shutil
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    return git


def test_list_changes_reports_deliverable_in_linked_worktree(tmp_path):
    git = _require_git()
    source = tmp_path / "project"
    _source_repo(git, source)
    workspace = tmp_path / "ws"
    _git(git, "worktree", "add", "-q", "-b", "agora/codex", str(workspace), "HEAD", cwd=source)
    (workspace / "deliverable.txt").write_text("x", encoding="utf-8")

    files = list_changes(workspace)
    assert any(f["path"] == "deliverable.txt" and f["status"] == "untracked" for f in files)


def test_list_changes_fails_closed_inside_outer_repo(tmp_path):
    """A workspace nested in an outer repo (no worktree of its own) reports
    nothing, never the outer repo's changes."""
    git = _require_git()
    _source_repo(git, tmp_path)  # outer repo with its own history
    (tmp_path / "outer-noise.txt").write_text("outer", encoding="utf-8")
    workspace = tmp_path / "nested" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "deliverable.txt").write_text("x", encoding="utf-8")

    assert list_changes(workspace) == []


def test_list_changes_fails_closed_on_repo_root(tmp_path):
    """A plain repo root / main worktree (e.g. a project root) is rejected, so a
    misconfigured workspace can never leak a whole repository's changes."""
    git = _require_git()
    _source_repo(git, tmp_path)
    (tmp_path / "root-file.txt").write_text("y", encoding="utf-8")
    assert list_changes(tmp_path) == []


def test_list_changes_on_non_repo_returns_empty(tmp_path):
    """A workspace in no git repository at all reports nothing (fail closed)."""
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")
    assert list_changes(tmp_path) == []


def test_is_ready_worktree_binds_to_project_and_branch(tmp_path):
    """is_ready_worktree accepts only the project's own worktree on the expected
    branch — rejecting a repo root, a plain dir, an unrelated repo's worktree,
    and the right repo on the wrong branch."""
    from agora.execution.deliverables import is_ready_worktree

    git = _require_git()
    project = tmp_path / "project"
    _source_repo(git, project)
    expected = "agora/workspace/proj-x/codex"

    good = tmp_path / "good"
    _git(git, "worktree", "add", "-q", "-b", expected, str(good), "HEAD", cwd=project)
    assert is_ready_worktree(good, project, expected) is True

    # Right repo, wrong branch.
    wrong_branch = tmp_path / "wrongbranch"
    _git(git, "worktree", "add", "-q", "-b", "some/other-branch", str(wrong_branch), "HEAD", cwd=project)
    assert is_ready_worktree(wrong_branch, project, expected) is False

    # A worktree of an UNRELATED repository, even on the expected branch name.
    other = tmp_path / "other"
    _source_repo(git, other)
    unrelated = tmp_path / "unrelated"
    _git(git, "worktree", "add", "-q", "-b", expected, str(unrelated), "HEAD", cwd=other)
    assert is_ready_worktree(unrelated, project, expected) is False

    # A repo root and a plain dir are never ready.
    assert is_ready_worktree(project, project, expected) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_ready_worktree(plain, project, expected) is False


def test_is_ready_worktree_rejects_when_project_root_nested_in_outer_repo(tmp_path):
    """If the project root is not its OWN repo (nested inside an outer checkout),
    an outer-owned worktree on the expected branch must not pass."""
    from agora.execution.deliverables import is_ready_worktree

    git = _require_git()
    _source_repo(git, tmp_path)  # outer repo
    project = tmp_path / "proj"
    project.mkdir()  # a plain subdir, NOT its own repo
    expected = "agora/workspace/proj-x/codex"
    ws = tmp_path / "ws"
    _git(git, "worktree", "add", "-q", "-b", expected, str(ws), "HEAD", cwd=tmp_path)  # worktree of OUTER

    assert is_ready_worktree(ws, project, expected) is False


def test_is_ready_worktree_rejects_linked_worktree_as_project_root(tmp_path):
    """A project root that is itself a LINKED worktree of another repo (not a
    main-worktree root) is rejected, even for a sibling worktree of that repo on
    the expected branch — otherwise the other repo's common dir would be trusted."""
    from agora.execution.deliverables import is_ready_worktree

    git = _require_git()
    main = tmp_path / "main"
    _source_repo(git, main)
    expected = "agora/workspace/proj-x/codex"
    root_wt = tmp_path / "root_wt"  # a linked worktree, wrongly used as project_root
    _git(git, "worktree", "add", "-q", "-b", "some/root-branch", str(root_wt), "HEAD", cwd=main)
    sibling = tmp_path / "sibling"  # a sibling worktree of the same repo, expected branch
    _git(git, "worktree", "add", "-q", "-b", expected, str(sibling), "HEAD", cwd=main)

    assert is_ready_worktree(sibling, root_wt, expected) is False
