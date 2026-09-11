"""LocalEnvironment: a command that did not run is not a command that failed.

W3: ``run``/``probe`` stay SYNCHRONOUS (W2's contract, unchanged); only ``launch`` is
async — the one capability W3 adds (CR-4, amending CR-1: the async run/probe a remote
asyncssh impl needs is a separate later contract in U2, not folded in here). So
run/probe tests call directly; launch/handle tests drive the coroutine with
``asyncio.run`` (via ``_run``), the same way ``test_dispatch`` drives the dispatcher —
the suite does not use ``@pytest.mark.asyncio``."""
from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agora.environments import (
    CommandResult,
    GRACE_SECONDS,
    LocalEnvironment,
    LocalProcessHandle,
    ProcessHandle,
)
from agora.fsprobe import PathProbe


@pytest.fixture
def env() -> LocalEnvironment:
    return LocalEnvironment()


def _run(coro):
    """Drive one async call (launch/handle) to completion, ``test_dispatch`` style.
    run/probe are synchronous (CR-4) and do not use this."""
    return asyncio.run(coro)


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


# --- W3: launch() -> ProcessHandle (the third capability) ---

def test_launch_returns_a_handle_that_waits_for_a_normal_exit(env, tmp_path):
    async def scenario():
        handle = await env.launch(
            [sys.executable, "-c", "print('hi')"], cwd=tmp_path
        )
        assert isinstance(handle, ProcessHandle)  # runtime_checkable Protocol
        assert isinstance(handle, LocalProcessHandle)
        assert handle.pid is not None  # a local process has a real OS pid
        out = await handle.stdout.read()
        code = await handle.wait(timeout=30)
        return code, out

    code, out = _run(scenario())
    assert code == 0
    assert b"hi" in out


def test_launch_wait_returns_none_when_the_process_outlives_the_timeout(env, tmp_path):
    async def scenario():
        handle = await env.launch(
            [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path
        )
        try:
            # Not exited within the deadline -> None, not a fabricated exit code.
            code = await handle.wait(timeout=0.5)
            assert code is None
            # A plain sleeper stops on SIGTERM, so this is the terminate() path.
            await handle.terminate()
            final = await handle.wait(timeout=GRACE_SECONDS)
            return final
        finally:
            # Never leave the 30s process running, on any path (assertion failure,
            # or a final wait that still returned None): force it down and reap it.
            await handle.kill()
            await handle.wait(timeout=GRACE_SECONDS)

    final = _run(scenario())
    assert final is not None  # the process is gone after terminate


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no real SIGTERM semantics: terminate() already hard-kills and a "
    "SIGTERM handler is a no-op, so the ignore-then-escalate path cannot exist there. "
    "The escalation contract is exercised on POSIX CI.",
)
def test_kill_escalates_when_terminate_is_ignored(env, tmp_path):
    # The real escalation path (POSIX): a child that installs a SIGTERM handler and
    # keeps running survives terminate(); only kill() ends it. We read its "up" line
    # first so the handler is provably installed before we signal (otherwise a kill
    # could land before the handler and the test would prove nothing).
    prog = (
        "import signal, time, sys\n"
        "signal.signal(signal.SIGTERM, lambda *a: None)\n"
        "sys.stdout.write('up\\n'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    async def scenario():
        handle = await env.launch([sys.executable, "-c", prog], cwd=tmp_path)
        try:
            ready = await handle.stdout.readline()
            assert ready.strip() == b"up"  # handler is installed now
            await handle.terminate()
            # Ignored SIGTERM: still running after a short grace -> wait() is None.
            survived = await handle.wait(timeout=1.0)
            assert survived is None, "child should have ignored SIGTERM and kept running"
            await handle.kill()
            return await handle.wait(timeout=GRACE_SECONDS)
        finally:
            await handle.kill()
            await handle.wait(timeout=GRACE_SECONDS)

    final = _run(scenario())
    assert final is not None  # kill() brought down what terminate() could not


def test_terminate_and_kill_are_idempotent_on_an_exited_process(env, tmp_path):
    async def scenario():
        handle = await env.launch([sys.executable, "-c", "pass"], cwd=tmp_path)
        # Confirm it has actually exited BEFORE the idempotency calls, or the test
        # would pass by terminating/killing a still-running process instead.
        code = await handle.wait(timeout=30)
        assert code == 0
        # Already exited: neither raises (ProcessLookupError tolerated), matching _stop.
        await handle.terminate()
        await handle.kill()

    _run(scenario())  # no exception == pass


def test_launch_empty_argv_is_the_callers_mistake_and_raises(env):
    with pytest.raises(ValueError):
        _run(env.launch([]))


# --- PBT (Partial, Q8=B): serialization / decoding round-trip on the seam ---

@pytest.mark.parametrize(
    "text",
    ["ascii only", "日本語テキスト", "emoji 🧭 mix", "混合 mixed 文字列", ""],
)
def test_output_round_trips_through_utf8_regardless_of_content(env, tmp_path, text):
    # NFR-5 / cp932 lesson: when the child emits UTF-8 bytes, output is captured and
    # decoded as UTF-8, so non-ASCII survives whatever the platform codec would
    # otherwise have done to it.
    # The child writes raw UTF-8 BYTES via stdout.buffer on purpose: that is the shape
    # the seam promises to handle (a remote/child hands us bytes, we decode UTF-8).
    # Writing through the text layer instead would encode with the platform locale
    # (cp932 on this host) and the test would be measuring Windows' codec, not the seam
    # — and would abort outright on characters cp932 cannot represent (emoji).
    result = env.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({text.encode('utf-8')!r})",
        ],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.ok
    assert result.stdout == text


def test_output_that_is_not_utf8_is_replaced_rather_than_raising(env, tmp_path):
    # The other half of the decoding contract: decoding uses errors="replace". A remote
    # host is free to emit bytes that are not valid UTF-8 at all — a latin-1 log line, a
    # truncated multi-byte sequence — and that must degrade to U+FFFD while keeping the
    # surrounding text, rather than raising UnicodeDecodeError out at a caller who has no
    # way to go back and re-read the output.
    result = env.run(
        [
            sys.executable,
            "-c",
            r"import sys; sys.stdout.buffer.write(b'before \xff\xfe after')",
        ],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.ok
    assert result.stdout.startswith("before ")
    assert result.stdout.endswith(" after")
    assert "\ufffd" in result.stdout


class _LocalProcessStarted(BaseException):
    """Raised in place of a spawn. Not an `Exception`, so ordinary handlers let it out."""


@pytest.mark.skip(
    reason="W3/D5 addressed the hazard in the minimal boundary-respecting form (task "
    "decision C): resolve_git_revision now takes an optional `env` and FAILS CLOSED for a "
    "non-local environment (raises rather than running local git against a remote path). "
    "It does NOT yet delegate each read THROUGH env — full remote revision resolution is "
    "deferred to the remote-deliverables unit (handoff commit binding). This test asserts "
    "the stronger 'every read goes through the injected Environment' (form B), which that "
    "later unit will satisfy; kept skipped until then. The active D5 guard (local works, "
    "non-local env fails closed) is test_d5_resolve_git_revision_fails_closed_for_non_local_env below."
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


# --- D5 (W3, form C): resolve_git_revision fails closed for a non-local env ---

def test_d5_resolve_git_revision_fails_closed_for_non_local_env(tmp_path, monkeypatch):
    """The active D5 guard: given a non-local Environment, revision resolution must
    refuse BEFORE running any git, rather than run local git against a path this host
    does not own. We monkeypatch subprocess.run to prove no process is started and to
    distinguish the guard's refusal from an ordinary git-level ValueError (tmp_path is
    not a repo, which would also raise ValueError — so asserting the type alone would
    pass without the guard)."""
    import agora.orchestration.protocol_context as protocol_context

    def refuse_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called for a non-local env")

    monkeypatch.setattr(protocol_context.subprocess, "run", refuse_run)

    class _NotLocalEnv:  # a stand-in remote env: anything that is not LocalEnvironment
        def run(self, argv, *, cwd=None, timeout): ...      # sync per CR-4 contract
        def probe(self, path, *, follow_symlinks=True): ...  # sync per CR-4 contract
        async def launch(self, argv, *, cwd=None, env=None, timeout): ...

    with pytest.raises(ValueError, match="non-local environment is not supported"):
        protocol_context.resolve_git_revision(
            tmp_path, repository_id="alpha", env=_NotLocalEnv()
        )


def test_d5_local_env_and_none_run_git_and_do_not_hit_the_guard(tmp_path, monkeypatch):
    """A LocalEnvironment and the default env=None both reach the local synchronous
    git path — proven by observing subprocess.run is actually called with
    ``git -C <root> ...`` — and never hit the non-local guard."""
    import subprocess as _subprocess
    import agora.orchestration.protocol_context as protocol_context
    from agora.environments import LocalEnvironment

    sha = "3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f40"

    for env in (None, LocalEnvironment()):
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            out = ""
            if "--verify" in argv:
                out = sha
            elif "--symbolic-full-name" in argv:
                out = "refs/heads/main"
            # status --porcelain: empty output == clean worktree
            return _subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        monkeypatch.setattr(protocol_context.subprocess, "run", fake_run)

        revision = protocol_context.resolve_git_revision(
            tmp_path, repository_id="alpha", env=env
        )
        assert revision.commit_sha == sha
        assert revision.ref == "refs/heads/main"
        # It really went through local git (not short-circuited, not the guard).
        assert calls, "local/None env must reach the local git path"
        assert all(call[:3] == ["git", "-C", str(tmp_path)] for call in calls)