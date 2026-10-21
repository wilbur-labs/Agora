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

    def _finish(self, code: int | None) -> None:
        with self._lock:
            if self.exited_at is None:
                self.exited_at = time.time()
                self.exit_code = code
            self._publish(("exit", self._seq, code))

    # --- windows ------------------------------------------------------------------------
    def attach(self) -> tuple[list[tuple[int, str]], asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        loop = asyncio.get_running_loop()
        with self._lock:
            replay = list(self._chunks)
            self._subscribers[queue] = loop
            if self.exited_at is not None:
                queue.put_nowait(("exit", self._seq, self.exit_code))
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

    def create(self, *, project_id: str, label: str, cwd: Path, argv: list[str],
               env_extra: dict[str, str] | None = None, rows: int = 30, cols: int = 120) -> TerminalSession:
        with self._lock:
            self._prune()
            if sum(1 for s in self._sessions.values() if s.exited_at is None) + self._starting >= MAX_TERMINALS:
                raise TerminalLimitReached(f"at most {MAX_TERMINALS} terminals are open at once; close one first")
            self._starting += 1
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
        session._finish(code)
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
        if session.exited_at is None:
            from agora.execution.dispatcher import ExecutionDispatcher

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
                session._finish(_exit_status(session._pty))
                self._release(session)
        return True

    def close_all(self) -> None:
        for session in self.list():
            self.close(session.terminal_id)
