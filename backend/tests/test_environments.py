"""LocalEnvironment: a command that did not run is not a command that failed."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agora.environments import CommandResult, LocalEnvironment
from agora.fsprobe import PathProbe


@pytest.fixture
def env() -> LocalEnvironment:
    return LocalEnvironment()


def test_a_command_that_exits_zero_is_ok_and_hands_back_its_output(env, tmp_path):
    result = env.run([sys.executable, "-c", "print('hello')"], cwd=tmp_path, timeout=30)
    assert result.outcome == "completed"
    assert result.returncode == 0
    assert result.ok
    assert result.ran
    assert "hello" in result.stdout


def test_a_nonzero_exit_ran_but_is_not_ok(env, tmp_path):
    result = env.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('no'); sys.exit(3)"],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.outcome == "completed"
    assert result.returncode == 3
    assert not result.ok
    # It *did* run. A caller that treats this as "the tool is missing" would send
    # the operator to install something that is already installed.
    assert result.ran
    assert "no" in result.stderr
    assert result.reason() == "exit 3"


def test_a_missing_executable_never_ran_and_carries_no_exit_status(env, tmp_path):
    result = env.run(["agora-no-such-program-8f3c1d"], cwd=tmp_path, timeout=30)
    assert result.outcome == "unstartable"
    assert not result.ran
    assert not result.ok
    # The distinction this class exists for: no exit status may be invented for a
    # process that never started.
    assert result.returncode is None
    assert isinstance(result.error, OSError)
    assert result.reason() == "FileNotFoundError"


def test_a_command_that_outlives_its_deadline_is_a_timeout_not_a_failure(env, tmp_path):
    result = env.run(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.5
    )
    assert result.outcome == "timeout"
    assert result.returncode is None
    assert not result.ok
    # It started, so "install git" is the wrong advice here too.
    assert result.ran
    assert result.reason() == "timeout"


def test_the_command_runs_in_the_given_directory(env, tmp_path):
    result = env.run(
        [sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path, timeout=30
    )
    assert result.ok
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


def test_empty_argv_is_the_callers_mistake_and_raises(env):
    # Not an outcome: there is no command whose result this could be.
    with pytest.raises(ValueError):
        env.run([], timeout=30)


def test_probe_answers_present_and_absent(env, tmp_path):
    present = env.probe(tmp_path)
    assert present.is_dir() is True
    absent = env.probe(tmp_path / "nothing-here")
    assert absent.absent
    assert absent.exists() is False


def test_probe_hands_back_an_unknown_unaltered(env, tmp_path, monkeypatch):
    # The previous test's name claimed this and could not fail on it: a mutation
    # turning every unknown into `absent` passed. `probe_path` has its own tests for
    # producing an unknown; what is this class's job is not to flatten one on the way
    # through -- which is the same "unreadable read as absent" that the whole module
    # exists to refuse.
    import agora.environments as environments

    unknown = PathProbe("unknown", error=PermissionError(13, "denied"))
    monkeypatch.setattr(environments, "probe_path", lambda path, **kw: unknown)

    result = env.probe(tmp_path)
    assert result is unknown
    assert result.unknown
    assert result.exists() is None
    assert not result.absent


def test_a_result_never_reports_ok_without_having_completed():
    # Guards the property itself: every non-completed outcome is not ok, whatever
    # else it carries.
    assert not CommandResult("unstartable").ok
    assert not CommandResult("timeout").ok
    assert not CommandResult("completed", returncode=1).ok
    assert CommandResult("completed", returncode=0).ok


class _LocalProcessStarted(BaseException):
    """Raised in place of a spawn. Not an `Exception`, so ordinary handlers let it out."""


@pytest.mark.skip(
    reason="CR-14 (defer, OPEN): orchestration/protocol_context.resolve_git_revision is "
    "a fourth git call site and does not go through Environment. It is inside the tree "
    "adr/0001 froze, so W2 did not touch it. Delete this skip when W3 introduces a "
    "remote Environment — at that point it runs local git against a remote path."
)
def test_revision_resolution_delegates_its_reads_and_starts_no_process_we_can_see(
    tmp_path, monkeypatch
):
    """CR-14's acceptance artifact: the hazard, as something that fails rather than prose.

    `resolve_git_revision` runs `git -C <project root>` directly, and
    `TaskOrchestrationService` reaches it through its default revision resolver. Give
    provisioning a remote Environment and a project that exists only on the far side,
    and this one executes on the near side against a path that is not there.

    Skipped rather than failing because W2 deliberately did not modify the frozen
    orchestration tree, and a red suite everyone learns to ignore protects nothing
    either. `pytest -rs` prints this reason every run and the CR row it names is injected
    into every session, so the hazard reappears on its own instead of resting on a
    paragraph — which is what adr/0015 asks for.

    **Exactly what this establishes**, and no more:

    * each of the three reads the resolver performs — `--verify`,
      `--symbolic-full-name`, `status` — was made through the injected Environment;
    * the resolution returned the values that Environment supplied;
    * no process was started through any spawn path this test refuses, which are
      `subprocess.Popen.__init__` (measured to be where `subprocess.run`,
      `from subprocess import run`, `check_output`, `os.popen` and
      `asyncio.create_subprocess_exec` all arrive), `os.system`, `os.startfile`,
      `os.posix_spawn`/`posix_spawnp`, and **every** `os.spawn*` — taken from `dir(os)`
      rather than listed, because listing them is how `spawnve` was missed while
      `spawnv` was covered.

    **Two things it does not establish, stated because six earlier versions of this
    guard each claimed something wider than they checked.** A native call through a C
    extension is invisible to an in-process test, so "no process was started at all" is
    not provable here and is not claimed. And an implementation that wraps a spawn in
    `except BaseException` swallows the refusal; `except Exception` does not, which is
    the case that actually defeated version five and the reason the refusal is a
    `BaseException`.

    **If the injection parameter is named something other than a name containing "env"**
    — `runner`, `executor` — this fails rather than passes, and whoever removes the skip
    adapts one line. That is the safe direction for a guard: it blocks and asks, it does
    not wave a rename through.
    """
    import agora.orchestration.protocol_context as protocol_context

    sha = "3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f40"
    used: list[list[str]] = []

    class Recording:
        def run(self, argv, *, cwd=None, timeout):
            used.append(list(argv))
            if "--verify" in argv:
                return CommandResult("completed", returncode=0, stdout=sha)
            if "--symbolic-full-name" in argv:
                return CommandResult("completed", returncode=0, stdout="refs/heads/main")
            return CommandResult("completed", returncode=0, stdout="")

        def probe(self, path, *, follow_symlinks=True):
            return LocalEnvironment().probe(path, follow_symlinks=follow_symlinks)

    def refuse(*args, **kwargs):
        raise _LocalProcessStarted(
            "revision resolution started a local process; it must go through the "
            "Environment it was given"
        )

    monkeypatch.setattr(subprocess.Popen, "__init__", refuse)
    # Every `os.spawn*` by family rather than by name: `spawnv` was patched and
    # `spawnve` was not, which is the enumeration problem this guard keeps relearning.
    # `dir(os)` is the complete list on whatever platform this runs.
    for name in [n for n in dir(os) if n.startswith("spawn")] + [
        "system", "posix_spawn", "posix_spawnp", "startfile"
    ]:
        if hasattr(os, name):  # platform-dependent; patch the ones that are here
            monkeypatch.setattr(os, name, refuse)

    accepted = [
        name
        for name in inspect.signature(protocol_context.resolve_git_revision).parameters
        if "env" in name.lower()
    ]
    assert accepted, (
        "resolve_git_revision takes no Environment (no parameter whose name contains "
        "'env'), so it cannot be running git anywhere but this host"
    )

    missing_root = tmp_path / "project-that-is-not-there"
    assert not missing_root.exists(), (
        "this guard depends on the root being absent; it is not, so the test would "
        "prove nothing"
    )

    revision = protocol_context.resolve_git_revision(
        missing_root, repository_id="alpha", **{accepted[0]: Recording()}
    )

    assert revision.commit_sha == sha
    assert revision.ref == "refs/heads/main"
    for expected in ("--verify", "--symbolic-full-name", "status"):
        assert any(expected in call for call in used), (
            f"`{expected}` was not read through the environment; recorded: {used}"
        )
