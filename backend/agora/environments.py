"""Where a command runs and where a path is looked at — one seam, two capabilities.

**Why this module exists.** Agora has to work against more than the machine the
server runs on: a project can live in WSL, and W3 will put one behind SSH. "Run git"
was written separately in ``workspaces/provisioner.py`` (two methods),
``execution/deliverables.py`` (three call sites) and ``execution/handoff.py`` (one),
and "does this path exist" had two different answers in one codebase
(:mod:`agora.fsprobe` in the dispatcher, router and project registry; a bare
``Path.exists()`` in the provisioner). Both have to become one thing before either
can become remote.

**What an Environment is.** Exactly two capabilities:

``run(argv, cwd=..., timeout=...)``
    Run a *short, bounded* command to completion and hand back what it produced.

``probe(path)``
    What is at a path, with absence and unknown kept apart — one ``lstat``, plus a
    stat of the target when a symlink is followed. :func:`agora.fsprobe.probe_path`.

**What an Environment is NOT.** It does not launch agent processes. The dispatcher
needs a pid to record, two streams to tail while the process is alive, and a way to
kill it on timeout; that is a live process *handle*, not a result. Making ``run``
serve both would let a local implementation look like it supports remote execution
right up to the point where someone writes the remote one and finds the stream
semantics do not line up. W3 adds a third capability when there is a real remote
implementation to check it against — an interface designed now, with nothing to
check it against, would be a guess.

**And the seam is not the whole system.** Counted, not estimated:

* Every git call site *outside* the frozen ``orchestration/`` tree goes through it —
  the provisioner, deliverables and handoff.
* ``orchestration/protocol_context.resolve_git_revision`` is a fourth git call site and
  does **not**. It sits inside the tree ``tasks/agora/adr/0001`` froze, it is reached
  from ``orchestration/service.py``, and it will run *local* git against a path a
  remote Environment says lives somewhere else. Tracked as a ``defer`` CR in that
  task's RESUME.
* **Ten** modules under ``agora/`` can start a process at all (counted on the parsed
  tree, every spelling, nothing excluded). One of them is this file, which is the
  implementation. Of the other nine:

  - five outside the frozen tree, spawning for their own reasons —
    ``execution/dispatcher.py`` (launching agents; deliberate, see above),
    ``attention/bridges/codex_app_server.py``, ``research/workers.py``,
    ``tasks/clarify.py``, ``api/settings_router.py``;
  - four inside it — ``orchestration/protocol_context.py`` (the git one above),
    ``orchestration/runtime.py``, ``orchestration/runtime_auth.py``,
    ``orchestration/runtime_capabilities.py``.

  This count has been wrong twice. It said "four" because the search excluded
  ``orchestration/`` — the same filter that hid the fourth git call site, reapplied in
  the very paragraph written to record that mistake — and then "nine" because it
  silently left out this module. Both times the exclusion was correct and unstated,
  which is the part that makes it a defect rather than a shorthand.

Say that whenever you say "W2 added an Environment abstraction". An earlier version of
this paragraph said "three of the four places that run commands" — a sentence written
to mark exactly this boundary, which got the boundary wrong, because the search behind
it excluded the frozen tree while the question was "what runs git", not "what runs git
that I am allowed to change". A claim wider than the thing it describes is the failure
this project keeps repeating, and it does not stop being one when it appears in the
disclaimer.

``run`` is synchronous. Callers on the async request path wrap it in
``asyncio.to_thread``, which is what the provisioner already does for its blocking
git inspections — one implementation per environment rather than two that can drift.

Like :mod:`agora.fsprobe`, it never raises for the thing it is reporting on: "the
command could not be started" and "the command ran too long" are *outcomes*, not
exceptions a caller has to remember to catch. Only a caller's own mistake (a bad
argument) raises.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from agora.fsprobe import PathProbe, probe_path

#: ``completed`` — the command ran and exited; ``returncode`` says how.
#: ``unstartable`` — it never ran (executable missing, permission, spawn failure).
#: ``timeout`` — it ran and was killed for exceeding the deadline.
CommandOutcome = Literal["completed", "unstartable", "timeout"]


@dataclass(frozen=True)
class CommandResult:
    """What one command attempt established. "Did not run" is kept apart from "failed".

    A caller that only checks ``returncode`` would read a missing git and a git that
    said no as the same thing, and report one as the other — the same conflation
    :mod:`agora.fsprobe` exists to refuse for paths.
    """

    outcome: CommandOutcome
    #: Only when ``outcome`` is ``completed``. ``None`` otherwise, so an exit status
    #: can never be read off an attempt that produced none.
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    #: The ``OSError`` behind an ``unstartable`` outcome, kept so a caller can name
    #: the cause rather than guess at it.
    error: OSError | None = None

    @property
    def ok(self) -> bool:
        """The command ran and exited zero. Nothing else is success."""
        return self.outcome == "completed" and self.returncode == 0

    @property
    def ran(self) -> bool:
        """The process started. A non-zero exit still counts; an unstartable one does not."""
        return self.outcome != "unstartable"

    def reason(self) -> str:
        """A short cause for a message: ``FileNotFoundError``, ``timeout``, ``exit 128``."""
        if self.outcome == "unstartable":
            return type(self.error).__name__ if self.error is not None else "could not start"
        if self.outcome == "timeout":
            return "timeout"
        return f"exit {self.returncode}"


class Environment(Protocol):
    """Run a bounded command, and look at a path. See the module docstring for the
    two things this deliberately does not cover."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float,
    ) -> CommandResult:
        """Run ``argv`` to completion and capture its output. Never raises for the
        command's own failure to start or finish — see :class:`CommandResult`.

        ``timeout`` has no default on purpose. "Short, bounded" was documented and not
        enforced, so a caller could get an unbounded wait without violating the
        interface — a contract only prose upholds is the shape this codebase keeps
        being bitten by. Every caller already passed one; now they have to."""
        ...

    def probe(self, path: Path, *, follow_symlinks: bool = True) -> PathProbe:
        """What is at ``path``, with absence and unknown kept apart."""
        ...


class LocalEnvironment:
    """The machine the server itself runs on."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float,
    ) -> CommandResult:
        if not argv:
            # A caller's own mistake, not an outcome of running anything.
            raise ValueError("argv must name a program to run")
        try:
            completed = subprocess.run(
                list(argv),
                cwd=str(cwd) if cwd is not None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Killed at the deadline. Whatever it managed to emit is still worth
            # handing back — a git that hangs after printing its error is the case
            # where that output is the only clue.
            return CommandResult(
                "timeout",
                stdout=_decoded(exc.stdout),
                stderr=_decoded(exc.stderr),
            )
        except OSError as exc:
            # FileNotFoundError (not on PATH), PermissionError, and every other
            # spawn failure. The command did not run, so it has no exit status and
            # must never be given one.
            return CommandResult("unstartable", error=exc)
        return CommandResult(
            "completed",
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def probe(self, path: Path, *, follow_symlinks: bool = True) -> PathProbe:
        return probe_path(path, follow_symlinks=follow_symlinks)


def _decoded(raw: str | bytes | None) -> str:
    """``TimeoutExpired`` carries whatever was buffered, as ``str`` under ``text=True``
    but as ``bytes`` on some paths; normalise without losing it."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw
