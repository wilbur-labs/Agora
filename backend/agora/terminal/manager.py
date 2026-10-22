"""Live terminals, held by this server process (CR-30).

One ``TerminalSession`` per pseudo-terminal. A reader thread copies the PTY's output into
a bounded ring buffer (replayed to a window that attaches later, so closing a browser tab
does not lose the screen) and to every attached window's queue. Order is kept by one
lock and one sequence number: an attach takes the buffer snapshot and registers its
queue under the same lock the reader appends under, so nothing is lost or repeated
between replay and live output.

Lifetime: a terminal lives until its process exits, it is closed, or this server stops.
On Windows each terminal's process tree is placed in a job object that kills its members
when the handle closes — so an agent started inside a terminal does not outlive Agora.
After a restart the list is empty, which is true: those processes are gone.

A remote terminal (CR-30 slice 2, ``RemoteTerminalSession``) is the same shape over an
SSH channel with a PTY. Its process and connection belong to the server's event loop;
``write``/``resize``/``close`` may be called from any thread and only post to that loop.
How a terminal ended is recorded, not inferred from the exit code: ``exited`` (the
program ended), ``closed`` (the person closed it), ``disconnected`` (the SSH connection
dropped — the remote program may still be running) or ``failed`` (it never started).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Characters of output kept per terminal for replay.
BUFFER_CHARS = 256 * 1024
#: Live terminals per server. A cap, not a quota: past it, close one first.
MAX_TERMINALS = 8
#: Messages queued for one window before it is treated as too slow and dropped.
QUEUE_LIMIT = 2000
#: How long an exited terminal stays in the list.
KEEP_EXITED_SECONDS = 24 * 3600
#: How long opening a remote terminal may take — connect, PATH probe, channel, PTY. A host
#: that accepts the connection and then stalls must not hold a terminal slot forever.
REMOTE_OPEN_TIMEOUT = 45.0
#: How long a remote close may wait for the channel to close gracefully. A program that
#: stopped reading its input can hold a graceful close forever (it flushes first).
REMOTE_CLOSE_GRACE = 5.0


class TerminalLimitReached(RuntimeError):
    pass


class PtyUnavailable(RuntimeError):
    pass


def _spawn_pty(argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int):
    if os.name == "nt":
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise PtyUnavailable(f"pywinpty is not installed: {exc}") from exc
        return PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
    from ptyprocess import PtyProcessUnicode
    return PtyProcessUnicode.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))


@dataclass
class TerminalSession:
    terminal_id: str
    project_id: str
    label: str
    cwd: str
    argv: list[str]
    started_at: float
    rows: int
    cols: int
    exited_at: float | None = None
    exit_code: int | None = None
    #: How it ended (see the module docstring); None while it runs.
    end_reason: str | None = None
    end_detail: str | None = None
    #: ``user@host`` for a remote terminal; None on this machine.
    remote: str | None = None
    #: Set before a close ends the process, so whoever records the end — the close or
    #: the reader it unblocked — records it as closed, not as the program exiting.
    _closing: bool = False
    _pty: Any = None
    _job: Any = None
    _chunks: list[tuple[int, str]] = field(default_factory=list)
    _chars: int = 0
    _seq: int = 0
    #: Each attached window's queue and the event loop that reads it. Per window, not per
    #: terminal: the loop that created a terminal is not necessarily the one a window
    #: attaches from, and a closed loop must not kill the reader thread.
    _subscribers: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _write_lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "project_id": self.project_id,
            "label": self.label,
            "cwd": self.cwd,
            "command": self.argv,
            "started_at": self.started_at,
            "exited_at": self.exited_at,
            "exit_code": self.exit_code,
            "end_reason": self.end_reason,
            "end_detail": self.end_detail,
            "remote": self.remote,
            "alive": self.exited_at is None,
        }

    # --- output -------------------------------------------------------------------------
    def _publish(self, item: tuple) -> None:
        for queue, loop in list(self._subscribers.items()):
            try:
                loop.call_soon_threadsafe(_offer, self, queue, item)
            except RuntimeError:  # that window's loop is closed: it is gone
                self._subscribers.pop(queue, None)

    def _append(self, text: str) -> None:
        with self._lock:
            self._seq += 1
            self._chunks.append((self._seq, text))
            self._chars += len(text)
            while self._chars > BUFFER_CHARS and len(self._chunks) > 1:
                _, old = self._chunks.pop(0)
                self._chars -= len(old)
            self._publish(("output", self._seq, text))

    def _finish(self, code: int | None, reason: str = "exited", detail: str | None = None) -> None:
        with self._lock:
            if self.exited_at is None:
                self.exited_at = time.time()
                self.exit_code = code
                self.end_reason = reason
                self.end_detail = detail
            self._publish(("exit", self._seq, self.end_payload()))

    def end_payload(self) -> dict[str, Any]:
        return {"code": self.exit_code, "reason": self.end_reason, "detail": self.end_detail}

    # --- windows ------------------------------------------------------------------------
    def attach(self) -> tuple[list[tuple[int, str]], asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        loop = asyncio.get_running_loop()
        with self._lock:
            replay = list(self._chunks)
            self._subscribers[queue] = loop
            if self.exited_at is not None:
                queue.put_nowait(("exit", self._seq, self.end_payload()))
        return replay, queue

    def detach(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    # --- input --------------------------------------------------------------------------
    def write(self, data: str) -> None:
        with self._write_lock:
            if self.exited_at is None and self._pty is not None:
                self._pty.write(data)

    def resize(self, rows: int, cols: int) -> None:
        rows, cols = max(2, min(rows, 500)), max(10, min(cols, 1000))
        if self.exited_at is None and self._pty is not None:
            self._pty.setwinsize(rows, cols)
            self.rows, self.cols = rows, cols


_OVERFLOW = ("overflow", 0, None)


def _offer(session: TerminalSession, queue: asyncio.Queue, item: tuple) -> None:
    """A window that cannot keep up is cut off (told why), never allowed to grow memory."""
    if queue.full():
        session.detach(queue)
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(_OVERFLOW)
        return
    queue.put_nowait(item)


def default_shell() -> list[str]:
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        return [shell, "-NoLogo"]
    return [os.environ.get("SHELL") or "/bin/bash", "-i"]


def command_argv(command: str, *, kiro_agent: str | None = None) -> list[str]:
    """The program a terminal runs: a shell, or an agent CLI inside one so that quitting
    the agent leaves the shell rather than closing the window. Only these four.

    ``kiro_agent`` opens kiro on that agent profile (a kiro-workspace task). It goes into
    the shell's command line, so only a name matching ``AGENT_NAME_RE`` is accepted."""
    from agora.terminal.targets import AGENT_NAME_RE

    agents = {"claude": "claude", "codex": "codex", "kiro": "kiro-cli chat"}
    shell = default_shell()
    if command == "shell":
        return shell
    if command not in agents:
        raise ValueError(f"unknown terminal command {command!r}")
    line = agents[command]
    if kiro_agent is not None:
        if command != "kiro" or not AGENT_NAME_RE.match(kiro_agent):
            raise ValueError(f"unusable kiro agent {kiro_agent!r}")
        line = f"{line} --agent {kiro_agent}"
    if os.name == "nt":
        return [shell[0], "-NoLogo", "-NoExit", "-Command", line]
    return [shell[0], "-ic", f"{line}; exec {shell[0]} -i"]


def _kill_group(pty) -> None:
    """POSIX: ptyprocess starts the shell as a session leader. Job control gives each job
    its own process group, but they all stay in that SESSION — so end every process whose
    session id is the shell's (Linux, read from /proc), then the shell's own group
    (review). On Windows the job object does this."""
    if os.name == "nt":
        return
    sid = pty.pid
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                fields = stat[stat.rindex(")") + 2:].split()
                if int(fields[3]) == sid and int(entry.name) != os.getpid():
                    os.kill(int(entry.name), signal.SIGKILL)
            except (OSError, ValueError, IndexError):
                continue
    try:
        os.killpg(sid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _alive(pty) -> bool:
    try:
        return bool(pty.isalive())
    except Exception:
        return False


def _exit_status(pty):
    try:
        return pty.exitstatus
    except Exception:
        return None


class TerminalManager:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        #: Starts in progress, counted against the cap so concurrent creates cannot pass it.
        self._starting = 0

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_EXITED_SECONDS
        for tid, s in list(self._sessions.items()):
            if s.exited_at is not None and s.exited_at < cutoff:
                del self._sessions[tid]

    def list(self) -> list[TerminalSession]:
        with self._lock:
            self._prune()
            return sorted(self._sessions.values(), key=lambda s: s.started_at)

    def get(self, terminal_id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(terminal_id)

    def _reserve(self) -> None:
        with self._lock:
            self._prune()
            if sum(1 for s in self._sessions.values() if s.exited_at is None) + self._starting >= MAX_TERMINALS:
                raise TerminalLimitReached(f"at most {MAX_TERMINALS} terminals are open at once; close one first")
            self._starting += 1

    async def create_remote(self, *, project_id: str, label: str, environment: Any, target: str,
                            cwd: str, line: str, rows: int = 30, cols: int = 120) -> "RemoteTerminalSession":
        """Open a PTY on ``environment`` (a ``RemoteEnvironment`` owned by this terminal
        alone) and start copying its output. Runs on the server's event loop, which then
        owns the channel. A failure closes the connection and raises: no half-open entry."""
        self._reserve()
        try:
            try:
                proc = await asyncio.wait_for(
                    environment.open_terminal(line, cwd, rows=rows, cols=cols), timeout=REMOTE_OPEN_TIMEOUT,
                )
            except BaseException:
                await _quietly_close(environment)
                raise
            session = RemoteTerminalSession(
                terminal_id=uuid.uuid4().hex[:12], project_id=project_id, label=label, cwd=cwd,
                argv=[line], started_at=time.time(), rows=rows, cols=cols, remote=target,
                _loop=asyncio.get_running_loop(), _proc=proc, _environment=environment,
            )
            with self._lock:
                self._sessions[session.terminal_id] = session
            session._task = asyncio.create_task(session._pump())
            return session
        finally:
            with self._lock:
                self._starting -= 1

    def create(self, *, project_id: str, label: str, cwd: Path, argv: list[str],
               env_extra: dict[str, str] | None = None, rows: int = 30, cols: int = 120) -> TerminalSession:
        self._reserve()
        try:
            return self._start(project_id=project_id, label=label, cwd=cwd, argv=argv,
                               env_extra=env_extra, rows=rows, cols=cols)
        finally:
            with self._lock:
                self._starting -= 1

    def _start(self, *, project_id: str, label: str, cwd: Path, argv: list[str],
               env_extra: dict[str, str] | None, rows: int, cols: int) -> TerminalSession:
        env = dict(os.environ)
        # The terminal is the person's own shell, not Agora's: none of the server's
        # private settings follow it in.
        for key in ("AGORA_CONFIG_PATH", "AGORA_INSTANCE", "AGORA_KEYRING_SERVICE"):
            env.pop(key, None)
        env.update(env_extra or {})
        env.setdefault("TERM", "xterm-256color")
        pty = _spawn_pty(argv, str(cwd), env, rows, cols)
        session = TerminalSession(
            terminal_id=uuid.uuid4().hex[:12], project_id=project_id, label=label, cwd=str(cwd),
            argv=argv, started_at=time.time(), rows=rows, cols=cols, _pty=pty,
        )
        from agora.execution.dispatcher import ExecutionDispatcher

        session._job = ExecutionDispatcher._adopt_process_tree(pty)
        with self._lock:
            self._sessions[session.terminal_id] = session
        threading.Thread(target=self._read_loop, args=(session,), daemon=True,
                         name=f"terminal-{session.terminal_id}").start()
        return session

    def _read_loop(self, session: TerminalSession) -> None:
        pty = session._pty
        while True:
            try:
                data = pty.read(4096)
            except EOFError:
                break
            except Exception:
                break
            if data:
                try:
                    session._append(data if isinstance(data, str) else data.decode("utf-8", "replace"))
                except Exception:
                    pass  # never let delivery end the terminal; the buffer still has it
            elif not pty.isalive():
                break
        code = None
        try:
            if os.name != "nt":
                pty.wait()
            code = pty.exitstatus
        except Exception:
            pass
        session._finish(code, "closed" if session._closing else "exited")
        self._release(session)

    @staticmethod
    def _release(session: TerminalSession) -> None:
        from agora.execution.dispatcher import ExecutionDispatcher

        job, session._job = session._job, None
        ExecutionDispatcher._release_process_tree(job)
        _kill_group(session._pty)  # a background job left after the shell exited
        # Give the transport back (and unblock a read that never saw EOF); the output
        # and the record stay for replay and the list (review).
        try:
            session._pty.close(force=True)
        except TypeError:
            try:
                session._pty.close()
            except Exception:
                pass
        except Exception:
            pass

    def close(self, terminal_id: str) -> bool:
        session = self.get(terminal_id)
        if session is None:
            return False
        if isinstance(session, RemoteTerminalSession):
            session.request_close()
            return True
        if session.exited_at is None:
            from agora.execution.dispatcher import ExecutionDispatcher

            session._closing = True
            ExecutionDispatcher._kill_process_tree(session._job)
            _kill_group(session._pty)
            try:
                session._pty.terminate(force=True)
            except Exception:
                pass
            # The reader may stay blocked in a read that never sees EOF after a forced
            # end (measured with ConPTY), so the close records the end itself rather than
            # leave the list saying "running" about a process that is gone.
            deadline = time.time() + 3
            while time.time() < deadline and _alive(session._pty):
                time.sleep(0.1)
            if not _alive(session._pty):
                session._finish(_exit_status(session._pty), "closed")
                self._release(session)
        return True

    def close_all(self) -> None:
        for session in self.list():
            self.close(session.terminal_id)


async def _quietly_close(environment: Any) -> None:
    try:
        await environment.aclose()
    except Exception:  # noqa: BLE001 - closing must not raise over the real error
        pass


@dataclass
class RemoteTerminalSession(TerminalSession):
    """A terminal whose PTY is an SSH channel (CR-30 slice 2). Its channel and
    connection belong to ``_loop``; every other thread only posts to it."""

    _loop: Any = None
    _proc: Any = None
    _environment: Any = None
    _task: Any = None

    async def _pump(self) -> None:
        proc = self._proc
        reason, detail = "exited", None
        try:
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                try:
                    self._append(data)
                except Exception:
                    pass  # delivery must not end the terminal; the buffer still has it
            await proc.wait_closed()
        except asyncio.CancelledError:
            reason = "closed"
        except Exception as exc:  # noqa: BLE001 - a dropped connection, said as such
            reason, detail = "disconnected", type(exc).__name__
        code = getattr(proc, "exit_status", None)
        if self._closing:
            reason = "closed"
        elif reason == "exited" and code is None:
            # The channel ended without an exit status: the connection went, not the
            # program. Whether it is still running over there is not known.
            reason = "disconnected"
        self._finish(code if reason == "exited" else None, reason, detail)
        await _quietly_close(self._environment)

    def write(self, data: str) -> None:
        if self.exited_at is None and self._proc is not None:
            self._post(self._proc.stdin.write, data)

    def resize(self, rows: int, cols: int) -> None:
        rows, cols = max(2, min(rows, 500)), max(10, min(cols, 1000))
        if self.exited_at is None and self._proc is not None:
            self.rows, self.cols = rows, cols
            self._post(self._proc.change_terminal_size, cols, rows)

    def request_close(self) -> None:
        """Post the close and return: nothing here waits on the loop, so a close from a
        worker thread or from shutdown cannot deadlock against it."""
        if self.exited_at is not None:
            return
        self._closing = True
        self._post(self._close_on_loop)

    def _close_on_loop(self) -> None:
        try:
            self._proc.close()
        except Exception:
            pass
        self._loop.call_later(REMOTE_CLOSE_GRACE, self._force_close)

    def _force_close(self) -> None:
        """The graceful close did not finish: drop the connection and stop the pump, so
        the slot and the connection are given back whatever the channel is doing."""
        if self.exited_at is not None:
            return
        conn = getattr(self._environment, "_conn", None)
        try:
            if conn is not None:
                conn.abort()
        except Exception:
            pass
        if self._task is not None:
            self._task.cancel()

    def _post(self, fn, *args) -> None:
        def run() -> None:
            try:
                fn(*args)
            except Exception:
                pass  # a channel already gone: the pump records how it ended
        try:
            self._loop.call_soon_threadsafe(run)
        except RuntimeError:
            pass  # the loop is closed: the server is stopping and the channel with it
