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

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

from agora.fsprobe import PathProbe, probe_path

#: SIGTERM -> grace period -> SIGKILL. Shared by Local and Remote process handles
#: so timeout cleanup behaves identically wherever a process runs (Q1). Matches the
#: 5s the dispatcher's own ``_stop`` has always used.
GRACE_SECONDS = 5

#: Remote (SSH) connection tuning (U2). Named constants, not config — config-izing
#: the environment layer is deferred to a later unit (see CR-2). A long agent run can
#: sit idle on the SSH channel long enough for a NAT/firewall to drop it, so keepalive
#: probes keep it observable; connect is bounded-retried for transient flakiness.
CONNECT_TIMEOUT = 30.0        #: seconds to establish the SSH connection
KEEPALIVE_INTERVAL = 30.0     #: seconds between keepalive probes on an idle channel
KEEPALIVE_MAX_MISSED = 3      #: drop the connection after this many unanswered probes
CONNECT_MAX_RETRIES = 2       #: retries after the first attempt (3 attempts total)
CONNECT_RETRY_BACKOFF = 0.5   #: base seconds for exponential backoff (0.5s, 1.0s, ...)

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


@runtime_checkable
class _ByteReader(Protocol):
    """A stream that hands back bytes. Deliberately NOT bound to
    ``asyncio.StreamReader`` (Q1): a remote asyncssh channel and a local
    subprocess pipe both satisfy this, so the dispatcher's two-``_read_tail``-task
    consumer reads them the same way. Bytes, never str — the caller decodes as
    UTF-8, so a remote host's locale never gets to mangle the output."""

    async def read(self, n: int = -1) -> bytes:
        ...


@runtime_checkable
class ProcessHandle(Protocol):
    """A live process, not a result — the third capability ``launch`` returns.

    ``run`` hands back what a finished command produced; this hands back a handle to
    one still running, so the dispatcher can record its pid, tail its two streams
    while it lives, and kill it on timeout. The three the module docstring named.

    Neither ``terminate`` nor ``kill`` raises for an already-exited process: they are
    idempotent, matching the dispatcher's own ``_stop``. Timeout cleanup is
    ``wait(timeout)`` -> on timeout ``terminate()`` -> wait ``GRACE_SECONDS`` ->
    ``kill()`` (Q1); local sends real signals, remote sends them over the channel."""

    @property
    def pid(self) -> int | None:
        """The OS pid locally. ``None`` when it cannot be known — a remote process's
        pid is not this machine's, and is not needed to kill it (that goes over the
        channel), so a remote handle reports ``None`` rather than a fake sentinel."""
        ...

    @property
    def returncode(self) -> int | None:
        """Exit code once the process has exited, else ``None``. Part of the contract
        (not just LocalProcessHandle's convenience) because the dispatcher reads it
        directly on the timeout and cancel paths — every implementation must provide
        it, or a conforming handle would AttributeError there."""
        ...

    @property
    def stdout(self) -> _ByteReader:
        ...

    @property
    def stderr(self) -> _ByteReader:
        ...

    async def wait(self, timeout: float | None = None) -> int | None:
        """The exit code once it exits; ``None`` if it has not by ``timeout``."""
        ...

    async def terminate(self) -> None:
        """Ask it to stop (SIGTERM locally). Idempotent on an exited process."""
        ...

    async def kill(self) -> None:
        """Force it to stop (SIGKILL locally). Idempotent on an exited process."""
        ...


class Environment(Protocol):
    """Run a bounded command, look at a path, and launch a live process. See the
    module docstring for what W2 deliberately left out and W3 now adds.

    ``run``/``probe`` stay SYNCHRONOUS (W2's contract, unchanged): the existing
    call chains — provisioner, deliverables, handoff, router, store — call them
    synchronously and only ever run locally today, so async-ifying them now would
    break a large web of callers for no present benefit. ``launch`` is async because
    it is the one capability W3 adds and the dispatcher awaits it.

    The async ``run``/``probe`` a RemoteEnvironment needs (asyncssh on a single loop)
    is a SEPARATE, later contract introduced when U2 builds RemoteEnvironment — not
    folded into this one, so a local sync implementation and a remote async one never
    have to pretend to share a signature (CR-4, amending CR-1). ``LocalEnvironment``
    keeps its synchronous run/probe; callers on the async path wrap them in
    ``asyncio.to_thread`` exactly as the provisioner already does."""

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

    async def launch(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: "Mapping[str, str] | None" = None,
    ) -> ProcessHandle:
        """Start ``argv`` and hand back a live :class:`ProcessHandle` without waiting
        for it to finish. ``env`` is the environment the caller INTENDS for the child;
        how much of it actually reaches the process is the implementation's call,
        decided by the trust boundary it crosses (CR-8):
          - :class:`LocalEnvironment` runs on this same host, so it forwards ``env`` as
            given (the caller typically passes ``{**os.environ, AGORA_*}``).
          - A remote environment crosses a trust boundary, so it forwards only a safe
            allowlist (the AGORA_* the agent needs) and drops the SSH credential and
            any other local secrets — sending the full local environment to a remote
            host would leak them. The agent still gets AGORA_TASK_ID/RUN_ID/PROJECT_ID.
        The caller's deadline lives on ``ProcessHandle.wait``, not here."""
        ...


class AsyncExecutionEnvironment(Protocol):
    """The async ``run``/``probe`` a remote implementation needs — kept apart from the
    synchronous :class:`Environment` (CR-4). A remote environment talks over asyncssh,
    which is async and holds one connection on a single event loop; it cannot honor
    the sync ``Environment.run/probe`` signature, and bridging one through a
    short-lived loop cannot reuse that connection safely. So a remote impl provides
    THESE (async run/probe) plus the async ``Environment.launch`` (shared, already
    async) — never the sync run/probe. The local sync chains (provisioner,
    deliverables, ...) keep using :class:`Environment`; nothing has to pretend one
    signature fits both.

    NOT ``@runtime_checkable`` on purpose: a runtime protocol check verifies only that
    the attributes EXIST, not that ``run``/``probe`` are async — so a sync
    ``LocalEnvironment`` would structurally satisfy it and a caller could ``await`` its
    synchronous ``run`` (a CommandResult, not a coroutine). Callers pick this vs the
    sync Environment by the execution PATH they are on, not by isinstance.

    ``run``/``probe`` return the SAME :class:`CommandResult` / :class:`PathProbe` as
    the sync contract — only the call convention differs."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float,
        read_only: bool = False,
    ) -> CommandResult:
        """Run ``argv`` to completion over the remote transport. Same outcome contract
        as :meth:`Environment.run` (never raises for the command's own failure).

        ``read_only`` is the caller's DECLARATION that this run is idempotent and safe
        to retry on a transport error — decided by contract, not by parsing the command
        (``git status`` and ``git commit`` are both ``run(git ...)``). Default ``False``
        means a transport failure is not retried, so a side-effecting run is never run
        twice."""
        ...

    async def probe(self, path: Path, *, follow_symlinks: bool = True) -> PathProbe:
        """What is at ``path`` on the remote, absence and unknown kept apart. Inherently
        read-only, so it may be retried on a transport error without a flag."""
        ...


#: A remote file whose content is read back (U4's read_file, and a seed .gitignore's
#: absence check) must be bounded — an unbounded read of an attacker- or accident-
#: sized file over SFTP would exhaust memory. The provisioner only ever reads tiny
#: control files (.gitignore, git metadata); a real cap that is never legitimately hit
#: is the safe default. U4 reuses the deliverables MAX_FILE_BYTES for changed-file
#: reads; this is the floor for the filesystem seam itself.
MAX_FS_READ_BYTES = 1 << 20  # 1 MiB


class AsyncFilesystem(Protocol):
    """Look at and mutate a remote filesystem — the capabilities git-over-SSH needs
    that :class:`Environment` deliberately does not have.

    **Why a separate protocol, not more methods on Environment (CR-11, Q1=A).**
    The provisioner (U3) has to list a directory to tell empty from occupied, write a
    ``.gitignore``, make and remove directories, and — the one that matters for safety
    — resolve a path on the host git will actually touch. U4 additionally reads a
    changed file's bytes. ``Environment`` froze at run/probe/launch; adding six
    filesystem methods to it would force ``LocalEnvironment`` and every existing caller
    to grow with a surface they never use. A separate protocol, reached through a typed
    ``env.fs`` accessor on the environments that HAVE a remote filesystem, keeps the
    frozen contract untouched (U1/U2 unchanged) and lets a local environment opt out
    until a local consumer exists.

    **Why typed methods, not a raw SFTPClient.** Handing back ``asyncssh.SFTPClient``
    would leak the transport into the provisioner and make the safety semantics
    (below) unenforceable — each call site would re-decide them. The SFTP session,
    file handles and chunked transfer stay inside the implementation; what crosses the
    seam is six operations with stated meaning.

    **Error classification — the fsprobe rule, kept (see :mod:`agora.fsprobe`).**
    Only a definite ENOENT is absence. A permission error, ENOTDIR, a broken
    connection, a decode failure — none of these is "nothing is there"; each is a
    failure to observe and MUST raise, never be flattened into a negative answer that
    a caller reads as "safe to create/overwrite/delete". This is the async filesystem
    counterpart of ``PathProbe.absent`` vs ``.unknown``.

    All paths are remote POSIX paths. All methods are async (one asyncssh connection,
    one event loop — CR-4)."""

    async def realpath(self, path: Path) -> Path:
        """Canonicalize ``path`` on the REMOTE host (resolve symlinks + ``..``),
        returning an absolute remote path. The confinement check (U3 ``_assert_allowed``)
        depends on this being resolved where git will act, not on this client's host:
        a local ``Path.resolve()`` would apply the CLIENT's filesystem semantics
        (and ``C:\\`` drive rules on Windows) to a path that lives on Linux, and could
        not see a remote symlink that escapes the allowed root.

        For a path that does not yet exist, resolve the nearest existing ancestor and
        re-append the remaining components (a not-yet-created workspace dir is a normal
        case, not an error). Raises on a transport failure or an ambiguous result —
        never returns an unresolved path silently (that would let confinement pass on a
        path it never actually checked)."""
        ...

    async def is_empty_dir(self, path: Path) -> bool:
        """True IFF ``path`` is a directory containing nothing but ``.`` and ``..``.

        A COMPLETE PROOF, not a best effort (a false ``True`` unlocks provisioning over
        a directory that is not actually empty). Hidden entries count as content — only
        the two exact self/parent links are ignored. Return ``True`` only when the
        listing completed normally with no other entry; a permission error, a dropped
        connection, ENOTDIR, or a decode failure MUST raise, never return ``False`` (a
        caller reads ``False`` as "occupied, leave it alone" — acceptable — but must
        never read a failed listing as either answer). Not a directory → raise (the
        caller asked a question that has no yes/no answer for a file)."""
        ...

    async def read_file(self, path: Path, *, max_bytes: int = MAX_FS_READ_BYTES, truncate: bool = False) -> bytes:
        """Read ``path``'s bytes (caller decodes; the remote locale never decides it).

        Default (``truncate=False``): the WHOLE file, or raise if it exceeds
        ``max_bytes`` — a silently truncated read is a wrong answer, not a smaller one
        (config-file / provisioner semantics). ``truncate=True``: a bounded PREVIEW —
        return at most ``max_bytes`` bytes and never raise for size (the caller reports
        its own truncation). In both modes a missing file raises ``FileNotFoundError``;
        unreadable raises ``PermissionError``/``OSError`` — never absence-as-empty."""
        ...

    async def write_file(self, path: Path, content: bytes) -> None:
        """Write ``content`` to ``path``, creating it or REPLACING its contents whole
        (used only for the seed ``.gitignore``). Does NOT create parent directories —
        the caller makes the directory explicitly (:meth:`mkdir`) so directory creation
        stays one auditable step, not a side effect of a write. The caller checks
        absence with :meth:`probe` first: this method does not itself decide whether an
        existing file should be preserved (that is a provisioning rule, not a filesystem
        primitive). A transport/permission failure raises."""
        ...

    async def mkdir(self, path: Path) -> None:
        """Create the single directory ``path``. NON-RECURSIVE on purpose: a recursive
        ``mkdir -p`` would silently recreate a deleted project root, which D2 form-2
        forbids Agora from ever creating (the root is the user's to pre-provision). The
        caller creates only the workspace's immediate parent, having first confirmed the
        root still exists. Already-exists → raise (the caller's emptiness/probe logic,
        not a silent ``exist_ok``, decides what an existing directory means). A missing
        PARENT raises rather than being created."""
        ...

    async def rmdir(self, path: Path) -> None:
        """Remove the EMPTY directory ``path``. If it is not empty, raise and STOP —
        never escalate to a recursive delete. This method exists to reclaim a directory
        the provisioner itself just found empty; a non-empty result means the world
        changed under it (a race), and deleting content the provisioner did not put
        there is exactly the blast radius FR-5 fails closed against."""
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

    async def launch(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ProcessHandle:
        if not argv:
            raise ValueError("argv must name a program to run")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return LocalProcessHandle(proc)


class LocalProcessHandle:
    """A local subprocess as a :class:`ProcessHandle`. Wraps
    ``asyncio.subprocess.Process``; ``terminate``/``kill`` send real signals and
    tolerate an already-exited process (``ProcessLookupError``), the same contract
    the dispatcher's ``_stop`` has always honored."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        """Exit code once known, else ``None``. Present so the dispatcher's cancel
        path can ask ``handle.returncode is None`` the same way it asks a raw
        subprocess — the two are interchangeable in ``_active``."""
        return self._proc.returncode

    @property
    def stdout(self) -> _ByteReader:
        # asyncio.StreamReader satisfies _ByteReader (async read(n) -> bytes).
        assert self._proc.stdout is not None  # PIPE was requested in launch
        return self._proc.stdout

    @property
    def stderr(self) -> _ByteReader:
        assert self._proc.stderr is not None
        return self._proc.stderr

    async def wait(self, timeout: float | None = None) -> int | None:
        if timeout is None:
            return await self._proc.wait()
        try:
            return await asyncio.wait_for(self._proc.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return None

    async def terminate(self) -> None:
        if self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
        except ProcessLookupError:
            return

    async def kill(self) -> None:
        if self._proc.returncode is not None:
            return
        try:
            self._proc.kill()
        except ProcessLookupError:
            return


def _decoded(raw: str | bytes | None) -> str:
    """``TimeoutExpired`` carries whatever was buffered, as ``str`` under ``text=True``
    but as ``bytes`` on some paths; normalise without losing it."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw
