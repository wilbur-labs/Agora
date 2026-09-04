"""Box 8 handoff: commit the deliverable, bind the commit, mark the task done."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.handoff import HandoffError, hand_off
from agora.execution.models import CreateRunRequest, RunState
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.workspaces.naming import worktree_branch

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required for handoff")


def _git(*args, cwd) -> None:
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, capture_output=True, timeout=15)


def _config(tmp_path: Path, command: list[str]) -> dict:
    # The project root is a real repo; the codex workspace is a linked worktree of
    # it — the shape the provisioner produces and that handoff/deliverables require.
    root = tmp_path / "alpha"
    root.mkdir(parents=True)
    _git("init", "-q", cwd=root)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed", cwd=root)
    workspace = tmp_path / "workspaces" / "codex"
    _git("worktree", "add", "-q", "-b", worktree_branch("alpha", "codex"), str(workspace), "HEAD", cwd=root)
    return {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(tmp_path / "alpha"),
                    "workspaces": {
                        "codex": str(workspace),
                        "claude": str(tmp_path / "workspaces" / "claude"),
                        "kiro": str(tmp_path / "workspaces" / "kiro"),
                    },
                }
            },
        },
        "execution": {
            "allowed_workspace_roots": [str(tmp_path / "workspaces")],
            "adapters": {
                "codex": {"command": [*command, "{prompt}"], "workspace_key": "codex"},
                "claude": {"enabled": False},
                "kiro": {"enabled": False},
            },
        },
    }


def _system(tmp_path: Path, command: list[str]):
    config = _config(tmp_path, command)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, build_adapter_registry(config),
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    return tasks, store, dispatcher


def _planned_task(tasks: TaskStore):
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Ship the widget"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )
    return tasks.get(task.task_id)


def _verified_task_with_worker(tmp_path: Path):
    """A verified task whose succeeded worker run left a file in the workspace."""
    tasks, store, dispatcher = _system(
        tmp_path,
        [sys.executable, "-c", "open('deliver.txt', 'w').write('hello')"],
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="build it",
        timeout_seconds=30, expected_task_version=2,
    ))
    finished = asyncio.run(dispatcher.execute(run.run_id))
    assert finished.state == RunState.SUCCEEDED
    # queue moved planned -> running; walk running -> review -> verified.
    current = tasks.get(task.task_id)
    current = tasks.transition(task.task_id, TaskState.REVIEW, actor="t", expected_version=current.version)
    current = tasks.transition(task.task_id, TaskState.VERIFIED, actor="t", expected_version=current.version)
    return tasks, store, dispatcher.projects, current


def test_handoff_commits_deliverable_and_completes(tmp_path):
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)

    result = hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.committed is True
    assert result.commit and len(result.commit) == 40
    assert [f["path"] for f in result.files] == ["deliver.txt"]

    # The commit really landed and the worktree is clean.
    workspace = Path(result.workspace)
    head = subprocess.run([GIT, "-C", str(workspace), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    assert head.stdout.strip() == result.commit
    status = subprocess.run([GIT, "-C", str(workspace), "status", "--porcelain"],
                            capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""

    # An append-only handoff record binds the commit to the task.
    events = [e for e in tasks.events(task.task_id) if e.event_type == "handoff.completed"]
    assert len(events) == 1
    assert events[0].payload["commit"] == result.commit
    assert events[0].payload["files"] == ["deliver.txt"]


def test_handoff_requires_verified_task(tmp_path):
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    hand_off(tasks, store, projects, task.task_id, actor="wilbur")  # now DONE

    with pytest.raises(HandoffError, match="verified"):
        hand_off(tasks, store, projects, task.task_id, actor="wilbur")


def test_handoff_without_worker_run_still_completes(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "pass"])
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="No runs"))
    # Force straight to verified without any run.
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
                   (TaskState.VERIFIED.value, task.task_id))

    result = hand_off(tasks, store, dispatcher.projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.commit is None and result.committed is False
    assert result.note and "no succeeded worker run" in result.note


def test_handoff_skips_commit_when_worker_worktree_is_wrong_branch(tmp_path):
    """If the worker's workspace is not this project/adapter's provisioned
    worktree (here: right repo, wrong branch), handoff commits nothing."""
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = projects.get("alpha").workspaces["codex"]
    _git("checkout", "-q", "-b", "some/wrong-branch", cwd=workspace)

    result = hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.commit is None and result.committed is False
    assert result.note and "provisioned worktree" in result.note


def test_commit_workspace_never_touches_a_repo_root(tmp_path):
    """Safety: a workspace that is a repo root / project root (not a linked
    worktree) must never be staged or committed, so an outer repository can
    never be swept into a handoff commit."""
    from agora.execution.handoff import _commit_workspace

    repo = tmp_path / "outer"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed", cwd=repo)
    (repo / "unmanaged.txt").write_text("must not be committed", encoding="utf-8")
    before = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()

    commit, committed, note = _commit_workspace(repo, "should not happen", "wilbur")

    assert commit is None and committed is False
    assert note and "worktree" in note
    after = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert after == before  # outer repo HEAD untouched
    # The index is empty — nothing was staged — and the file is still untracked.
    assert subprocess.run([GIT, "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0
    status = subprocess.run([GIT, "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert status == "?? unmanaged.txt"


def test_a_missing_git_is_not_recorded_as_a_verdict_about_the_workspace(tmp_path):
    """The handoff note is read by a person, so "git is not available" and
    "workspace is not a provisioned worktree" must not collapse into one line: the
    second sends them to fix a workspace that is fine."""
    from agora.environments import CommandResult
    from agora.execution.handoff import _commit_workspace
    from agora.fsprobe import probe_path

    class _NoGit:
        def run(self, argv, *, cwd=None, timeout):
            return CommandResult("unstartable", error=FileNotFoundError(2, "no git"))

        def probe(self, path, *, follow_symlinks=True):
            return probe_path(path, follow_symlinks=follow_symlinks)

    commit, committed, note = _commit_workspace(
        tmp_path, "Deliver: anything", "tester", env=_NoGit()
    )
    assert commit is None and committed is False
    assert note is not None
    assert "git is not available" in note
    assert "provisioned worktree" not in note


def test_the_production_path_does_not_record_a_git_failure_as_a_workspace_verdict(
    tmp_path, monkeypatch
):
    """The same distinction as the test above, but on the path that actually runs.

    `hand_off` used to call the boolean gate, which collapses "git never ran",
    "git timed out" and "this is not a provisioned worktree" into one False, and then
    wrote the third of those into a permanent record on a task moving to DONE. That
    the direct `_commit_workspace` test passed proved nothing about it.
    """
    import agora.execution.handoff as handoff_module

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    monkeypatch.setattr(
        handoff_module,
        "ready_worktree_check",
        lambda *a, **kw: (False, "git is not available (FileNotFoundError)"),
    )

    result = hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    events = [e for e in tasks.events(task.task_id) if e.event_type == "handoff.completed"]
    note = events[-1].payload["note"]
    assert "git is not available" in note
    assert "is not a provisioned worktree" not in note
    assert result.committed is False


def test_a_workspace_that_really_is_not_a_worktree_is_still_said_carefully(
    tmp_path, monkeypatch
):
    """The other branch. Nothing established it *is* the worktree; that is not the
    same as establishing it is not one, and exit 128 covers both."""
    import agora.execution.handoff as handoff_module

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    monkeypatch.setattr(handoff_module, "ready_worktree_check", lambda *a, **kw: (False, None))

    hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    events = [e for e in tasks.events(task.task_id) if e.event_type == "handoff.completed"]
    assert "could not be confirmed" in events[-1].payload["note"]


def test_a_head_that_cannot_be_read_is_not_recorded_as_having_no_commits(tmp_path):
    """`rev-parse HEAD` failing to *run* says nothing about whether a commit exists,
    and this note is permanent. The old wording could follow a successful commit with
    "workspace has no commits"."""
    from agora.environments import CommandResult
    from agora.execution.handoff import _commit_workspace

    class _HeadUnreadable:
        """Everything works until the HEAD read, which times out."""

        def __init__(self):
            from agora.environments import LocalEnvironment

            self._inner = LocalEnvironment()

        def run(self, argv, *, cwd=None, timeout):
            if "rev-parse" in argv and "HEAD" in argv:
                return CommandResult("timeout")
            return self._inner.run(argv, cwd=cwd, timeout=timeout)

        def probe(self, path, *, follow_symlinks=True):
            return self._inner.probe(path, follow_symlinks=follow_symlinks)

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = Path(store.require(_latest_run_id(store, task.task_id)).workspace)

    commit, committed, note = _commit_workspace(
        workspace, "Deliver: anything", "tester", env=_HeadUnreadable()
    )
    assert commit is None
    # The worktree had a deliverable, so a commit was made and then the HEAD read
    # failed. Saying "no commits" here contradicts a fact this call established one
    # step earlier -- and the record is permanent.
    assert committed is True
    assert note is not None
    assert "committed" in note and "could not be read" in note
    assert "no commits" not in note


def _latest_run_id(store, task_id: str) -> str:
    return store.list(task_id=task_id)[0].run_id


def test_a_completed_nonzero_head_read_after_a_commit_is_not_no_commits(tmp_path):
    """The gap round 1's fix left: only a *timeout* was handled.

    `rev-parse HEAD` exiting non-zero is not proof of an unborn branch either -- the
    same exit 128 that `provisioned_worktree_check` refuses to classify. When a commit
    has just succeeded it is proof of nothing at all, because the HEAD certainly exists.
    """
    from agora.environments import CommandResult, LocalEnvironment
    from agora.execution.handoff import _commit_workspace

    class _HeadRefuses:
        def __init__(self):
            self._inner = LocalEnvironment()

        def run(self, argv, *, cwd=None, timeout):
            if "rev-parse" in argv and "HEAD" in argv:
                return CommandResult("completed", returncode=128, stderr="ambiguous")
            return self._inner.run(argv, cwd=cwd, timeout=timeout)

        def probe(self, path, *, follow_symlinks=True):
            return self._inner.probe(path, follow_symlinks=follow_symlinks)

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = Path(store.require(_latest_run_id(store, task.task_id)).workspace)

    commit, committed, note = _commit_workspace(
        workspace, "Deliver: anything", "tester", env=_HeadRefuses()
    )
    assert committed is True
    assert commit is None
    assert "no commits" not in note


def test_staging_that_fails_stops_the_handoff_instead_of_committing_the_index(tmp_path):
    """`git add -A` exits zero even with nothing to add, so a non-zero exit means
    staging did not finish. Continuing would commit whatever the index held and record
    it as this run's delivery."""
    from agora.environments import CommandResult, LocalEnvironment
    from agora.execution.handoff import _commit_workspace

    class _AddFails:
        def __init__(self):
            self._inner = LocalEnvironment()
            self.committed_anything = False

        def run(self, argv, *, cwd=None, timeout):
            if "add" in argv:
                return CommandResult("completed", returncode=128, stderr="index locked")
            if "commit" in argv:
                self.committed_anything = True
            return self._inner.run(argv, cwd=cwd, timeout=timeout)

        def probe(self, path, *, follow_symlinks=True):
            return self._inner.probe(path, follow_symlinks=follow_symlinks)

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = Path(store.require(_latest_run_id(store, task.task_id)).workspace)
    env = _AddFails()

    commit, committed, note = _commit_workspace(
        workspace, "Deliver: anything", "tester", env=env
    )
    assert commit is None and committed is False
    assert "git add failed" in note
    assert env.committed_anything is False


def test_a_deleted_project_says_so_rather_than_pointing_at_the_workspace(
    tmp_path, monkeypatch
):
    """The fourth state. No worktree check was attempted at all, and the cause is
    already known -- so "could not be confirmed" sends the operator to inspect a
    workspace that may be entirely fine."""
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)

    def gone(project_id):
        raise KeyError(project_id)

    monkeypatch.setattr(projects, "get", gone)
    hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    events = [e for e in tasks.events(task.task_id) if e.event_type == "handoff.completed"]
    note = events[-1].payload["note"]
    assert "no longer registered" in note
    assert "could not be confirmed" not in note


class _GitLeavesAfterCall:
    """Real git up to and including call `n`, then gone (or hung) from call `n+1`."""

    def __init__(self, n: int, outcome):
        from agora.environments import LocalEnvironment

        self._inner = LocalEnvironment()
        self._n = n
        self._outcome = outcome
        self.calls = 0

    def run(self, argv, *, cwd=None, timeout):
        self.calls += 1
        if self.calls <= self._n:
            return self._inner.run(argv, cwd=cwd, timeout=timeout)
        return self._outcome()

    def probe(self, path, *, follow_symlinks=True):
        return self._inner.probe(path, follow_symlinks=follow_symlinks)


def _unstartable():
    from agora.environments import CommandResult

    return CommandResult("unstartable", error=FileNotFoundError(2, "no git"))


def _timed_out():
    from agora.environments import CommandResult

    return CommandResult("timeout")


@pytest.mark.parametrize("after_call", list(range(0, 8)))
@pytest.mark.parametrize(
    "outcome, expected",
    [(_unstartable, "git is not available"), (_timed_out, "git did not answer")],
)
def test_git_going_away_at_any_point_still_reports_git(
    tmp_path, after_call, outcome, expected
):
    """Round 1 fixed the *first* git call's reason and round 2 threaded it through the
    rest. Pinning only one call would leave the same hole one step further along, and
    the comment claiming "every later call" would again be wider than its coverage --
    so every position in the sequence is exercised, for both ways git can fail to
    answer.

    `ready_worktree_check` makes **eight** reads on the fully-successful path (counted,
    not assumed): the combined workspace shape; the project root's top-level, git dir
    and common dir; the workspace common dir; the project common dir again; the branch;
    HEAD. `after_call` 0..7 therefore makes each of calls 1..8 in turn the one where git
    stops answering -- including the very first, which an earlier version of this test
    left uncovered while its name claimed otherwise.
    """
    from agora.execution.deliverables import ready_worktree_check

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    run = store.require(_latest_run_id(store, task.task_id))
    workspace = Path(run.workspace)
    project_root = projects.get(run.project_id).root
    env = _GitLeavesAfterCall(after_call, outcome)

    ready, unavailable = ready_worktree_check(
        workspace, project_root, worktree_branch(run.project_id, run.adapter), env=env,
    )

    assert ready is False
    # Only meaningful if git was actually still being called at that point; the check
    # short-circuits once it has an answer.
    assert env.calls > after_call, (
        f"the check stopped before call {after_call + 1}, so this parameter exercises "
        f"nothing -- it made only {env.calls} calls"
    )
    assert unavailable is not None and expected in unavailable, (
        f"reason lost when git stopped answering after call {after_call}"
    )


def test_a_head_read_that_git_refuses_is_never_called_no_commits(tmp_path):
    """The round-3 finding, and the reviewer was right twice over.

    First I claimed the preceding successful `add`/`status` licensed a "workspace has
    no commits" verdict on a non-zero `rev-parse HEAD`; they establish only that git
    could read the repo at *those* instants. Then I tried to establish it positively
    with `symbolic-ref`, which does not work either: it succeeds whenever HEAD names a
    branch, including one whose ref is corrupt. So the claim is gone. This pins that
    it stays gone -- with nothing committed, which is the branch where the wrong
    verdict used to be reachable.
    """
    from agora.environments import CommandResult, LocalEnvironment
    from agora.execution.handoff import _commit_workspace

    class _HeadRefusesOnACleanTree:
        def __init__(self):
            self._inner = LocalEnvironment()

        def run(self, argv, *, cwd=None, timeout):
            if "rev-parse" in argv and "HEAD" in argv:
                return CommandResult("completed", returncode=128, stderr="bad ref")
            if "status" in argv:
                # Clean: nothing to commit, so `committed` stays False.
                return CommandResult("completed", returncode=0, stdout="")
            return self._inner.run(argv, cwd=cwd, timeout=timeout)

        def probe(self, path, *, follow_symlinks=True):
            return self._inner.probe(path, follow_symlinks=follow_symlinks)

    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = Path(store.require(_latest_run_id(store, task.task_id)).workspace)

    commit, committed, note = _commit_workspace(
        workspace, "Deliver: anything", "tester", env=_HeadRefusesOnACleanTree()
    )
    assert commit is None and committed is False
    assert "no commits" not in note
    assert "could not read the workspace HEAD" in note
