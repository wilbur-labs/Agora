"""Opening, closing and restoring work projects (CR-75 §2, §5).

Every work operation goes through one ``WorkService``. Its lock covers the checks and
the bookkeeping; a launch itself is a task the service owns, so a request cancelled
while a terminal is spawning neither releases the "starting" mark nor lets a second
open start another terminal — concurrent callers wait on the same task.

What the page may say about a launch comes only from the journal the session reporter
wrote for that launch (``reports.py``): a terminal that started proves a shell started,
nothing more.
"""
from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agora.terminal.manager import PtyUnavailable, TerminalLimitReached, TerminalManager, TerminalSession, command_argv
from agora.work import hooks as hooks_module
from agora.projects import workflow_selection
from agora.projects.workspace_tasks import workflow_workspace
from agora.projects.importer import StrictLoader
from agora.work import shelf
from agora.work.procs import creation_time, liveness
from agora.work.projects import WorkProject, WorkScan, scan_work_projects, work_key
from agora.work.reports import SESSION_ID_RE, Journal, has_conversation, read_journal, trusted_transcript
from agora.work.store import WorkSession, WorkSessionStore

#: The terminal's project id for a work project: ``work:<key>``.
PREFIX = "work:"
#: How long after a launch a missing first report is shown as "not confirmed yet".
CONFIRM_SECONDS = 60.0


class WorkProjectNotFound(LookupError):
    pass


class WorkProjectBusy(RuntimeError):
    """Its terminal is open or opening: closed first, then paused or archived."""


@dataclass
class LaunchInfo:
    launch_id: str
    #: ``resume`` or ``fresh``.
    mode: str
    #: What was decided before the launch: resume, fresh_no_record, fresh_missing,
    #: fresh_empty, fresh_untrusted, fresh_unsupported, no_hook.
    result: str
    detail: str | None = None
    restored: bool = False


#: A card name is one short line (CR-85).
MAX_NAME_LENGTH = 80


def normalise_name(name: str | None) -> str | None:
    """Blank means no name. Control characters (line breaks included) are refused, not
    stripped: the name is shown as typed or not at all."""
    if name is None:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"at most {MAX_NAME_LENGTH} characters")
    if any(ord(c) < 32 or 127 <= ord(c) < 160 or c in "\u2028\u2029" for c in cleaned):
        raise ValueError("one line, no control characters")
    return cleaned


@dataclass
class WorkService:
    store: WorkSessionStore
    manager: TerminalManager
    data_dir: Path
    sources: Callable[[], list[Path]]
    python: str | None = None
    #: Where Claude keeps transcripts; tests point it elsewhere.
    claude_projects: Path | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _starting: dict[str, asyncio.Task] = field(default_factory=dict)
    _launches: dict[str, LaunchInfo] = field(default_factory=dict)
    _hooks: hooks_module.HookSetup | None = None
    _restore_task: asyncio.Task | None = None
    #: Keys the person closed while their launch was still being prepared: the launch
    #: must not write its intent back over that close (code review P1).
    _closed_while_starting: set[str] = field(default_factory=set)
    #: Journal reads and the checkpoint writes they cause, one at a time: two overlapping
    #: polls must not commit an older recovery point after a newer one (code review P2).
    _checkpoint_lock: threading.Lock = field(default_factory=threading.Lock)
    #: One installation of the shared hook files at a time (code review round 2).
    _hook_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: How many times each key has been closed. An open that began before a close it did
    #: not see (it was still scanning) does not start afterwards (kiro review).
    _close_seq: dict[str, int] = field(default_factory=dict)
    #: A close still running, per project: an open arriving meanwhile waits for it to end,
    #: and a second close joins it rather than ending the same terminal twice.
    _pending_close: dict[str, asyncio.Task] = field(default_factory=dict)
    #: The closes themselves, held here so a cancelled request cannot abandon one.
    _close_tasks: set[asyncio.Task] = field(default_factory=set)
    #: How many times each key has been paused or archived (CR-80). An open that scanned
    #: before one of those does not launch from that scan (codex review CR-80 round 1).
    _shelve_seq: dict[str, int] = field(default_factory=dict)
    #: Shelf changes under way, held so a cancelled request cannot abandon one.
    _shelf_tasks: set[asyncio.Task] = field(default_factory=set)
    #: Keys (as listed while active) the person paused or archived from this server since
    #: it started. The restore banner drops those: it has nothing left to tell about a
    #: project put away — and only those, never one that merely shares a name.
    _put_away: set[str] = field(default_factory=set)

    # --- lookups -------------------------------------------------------------------------
    def scan(self) -> WorkScan:
        return scan_work_projects(self.sources())

    def _project(self, key: str) -> WorkProject | None:
        return next((p for p in self.scan().projects if p.key == key), None)

    def _terminals(self, key: str) -> list[TerminalSession]:
        return [s for s in self.manager.list() if s.project_id == PREFIX + key]

    def _live(self, key: str) -> TerminalSession | None:
        # One being closed is not live: an open arriving during the close starts a new one
        # rather than being answered with a terminal about to end (kiro review).
        return next((s for s in reversed(self._terminals(key))
                     if s.exited_at is None and not getattr(s, "_closing", False)), None)

    async def _hook_setup(self) -> hooks_module.HookSetup:
        async with self._hook_lock:
            if self._hooks is None or self._hooks.settings is None \
                    or not await asyncio.to_thread(hooks_module.still_installed, self._hooks):
                self._hooks = await asyncio.to_thread(hooks_module.install, self.data_dir, self.python)
            return self._hooks

    def _journal_path(self, key: str, launch_id: str) -> Path | None:
        if self._hooks is None or self._hooks.sessions_dir is None:
            return None
        return self._hooks.sessions_dir / key / f"{launch_id}.jsonl"

    def _read(self, row: WorkSession) -> Journal | None:
        """The current launch's journal, and the recovery point moved forward from it."""
        with self._checkpoint_lock:
            fresh = self.store.get(row.key)  # read again inside the lock: a newer launch may have begun
            return self._read_locked(fresh) if fresh is not None else None

    def _read_locked(self, row: WorkSession) -> Journal | None:
        if row.workspace != "claude" or not row.launch_id:
            return None
        path = self._journal_path(row.key, row.launch_id)
        if path is None:
            return None
        journal = read_journal(path, key=row.key, launch_id=row.launch_id, base=self.claude_projects)
        last = journal.last_valid
        if last is not None:
            if (last.session_id, str(last.transcript), row.launch_id, journal.stale_after) != \
                    (row.ck_session, row.ck_transcript, row.ck_launch, row.ck_stale):
                self.store.advance_checkpoint(row.key, session=last.session_id, transcript=str(last.transcript),
                                              launch=row.launch_id, stale=journal.stale_after)
        elif journal.stale_after and not row.ck_stale:
            self.store.advance_checkpoint(row.key, session=None, transcript=None, launch=None, stale=True)
        return journal

    # --- open / close --------------------------------------------------------------------
    async def open(self, key: str, *, restoring: bool = False, _seen_closes: int | None = None) -> dict[str, Any]:
        # A retry keeps the count this open first saw: a close arriving while it waited
        # came after it, and wins (codex review).
        seen_closes = self._close_seq.get(key, 0) if _seen_closes is None else _seen_closes
        seen_shelves = self._shelve_seq.get(key, 0)
        project = await asyncio.to_thread(self._project, key)
        async with self._lock:
            if self._close_seq.get(key, 0) != seen_closes:
                return {"result": "closed"}
            if self._shelve_seq.get(key, 0) != seen_shelves:
                # The scan above saw it active; it has been paused or archived since.
                return {"result": "unavailable", "detail": "paused or archived meanwhile"}
            if restoring and self._stage_of(key) != "doing":
                # Only what the person is doing comes back after a restart (CR-81).
                return {"result": "closed"}
            if not restoring and project is not None:
                # Opening a project is working on it: it is "doing" from here (CR-81). Under
                # the lock, so a "todo" set meanwhile is either before this or refused by it.
                self.store.set_stage(key, "doing")
            # A close still under way would end whatever is live now, or what this open
            # joined: wait for it, then open afresh (codex review, CR-77).
            pending = self._pending_close.get(key)
            live = None if pending is not None else self._live(key)
            if live is not None:
                return {"result": "already_open", "terminal": live.view()}
            if pending is None and self._starting.get(key) is None and any(
                    s.exited_at is None for s in self._terminals(key)):
                # Closed, yet still running: the close could not end it in time. A second
                # agent beside it would work on the same files (kiro review round 9).
                return {"result": "failed", "detail": "the previous terminal of this project has not ended yet"}
            task = None if pending is not None else self._starting.get(key)
            superseded = task is not None and key in self._closed_while_starting
            if task is None and pending is None:
                if project is None:
                    if restoring:
                        return {"result": "unavailable", "detail": "not an active project any more, or unreadable"}
                    raise WorkProjectNotFound(key)
                if project.tool_dir is None:
                    return {"result": "unavailable", "detail": project.reason}
                if restoring:
                    row = self.store.get(key)
                    if row is None or not row.want_open:
                        return {"result": "closed"}
                    # A launch that began but never recorded its shell: the server stopped
                    # between the spawn and the record, so the process may be running.
                    state = "unknown" if (row.launch_id and row.shell_pid is None) \
                        else liveness(row.shell_pid, row.shell_created)
                    if state != "gone":
                        return {"result": "maybe_running", "detail": f"pid {row.shell_pid} ({state})"}
                self._closed_while_starting.discard(key)
                task = asyncio.create_task(self._launch(project, restoring))
                self._starting[key] = task
                task.add_done_callback(lambda done, k=key: self._starting.pop(k, None)
                                       if self._starting.get(k) is done else None)
        if pending is not None:
            try:
                await asyncio.shield(pending)
            except Exception:  # noqa: BLE001 - the close's outcome is not this open's
                pass
            return await self.open(key, restoring=restoring, _seen_closes=seen_closes)
        if superseded:
            # That launch was cancelled by a close this open came after (kiro round 8).
            try:
                await asyncio.shield(task)
            except Exception:  # noqa: BLE001 - its outcome is not this open's
                pass
            return await self.open(key, restoring=restoring, _seen_closes=seen_closes)
        return await asyncio.shield(task)

    async def _launch(self, project: WorkProject, restoring: bool) -> dict[str, Any]:
        key = project.key
        row = self.store.get(key)
        env: dict[str, str] = {}
        launch_id = uuid.uuid4().hex
        previous: Journal | None = None
        if project.workspace == "kiro":
            mode, result, detail = "fresh", "fresh_unsupported", None
            argv = command_argv("kiro", kiro_agent=project.agent)
        else:
            setup = await self._hook_setup()
            if row is not None:
                previous = self._read(row)  # commit the recovery point before the launch columns change
                row = self.store.get(key)
                if row is not None and row.launch_id and row.launch_id != row.ck_launch \
                        and previous is not None and previous.first_start is None and not previous.read_failed \
                        and row.ck_session and not row.ck_stale:
                    # The last launch reported no start (no hook then, it never started, or
                    # only its end got written): whatever it ran after the recovery point
                    # went unrecorded (kiro rounds 8, 11).
                    self.store.advance_checkpoint(key, session=None, transcript=None, launch=None, stale=True)
                    row = self.store.get(key)
                elif row is not None and (previous is None or previous.read_failed) \
                        and row.launch_id and row.launch_id != row.ck_launch \
                        and row.ck_session and not row.ck_stale:
                    # The last launch's journal could not be read now (no hook this time):
                    # what it recorded after the recovery point is not known (kiro round 10).
                    self.store.advance_checkpoint(key, session=None, transcript=None, launch=None, stale=True)
                    row = self.store.get(key)
            folder = None
            if setup.settings is not None:
                try:
                    folder = await asyncio.to_thread(hooks_module.journal_dir, setup.sessions_dir, key)
                except OSError as exc:  # a link in its place, or it cannot be made: no recording
                    setup = hooks_module.HookSetup(None, None, f"{type(exc).__name__}: {exc}")
            mode, result, detail, resume = self._decide(row, setup)
            args: list[str] = []
            if setup.settings is not None and folder is not None:
                env.update({"AGORA_WORK_DIR": str(folder), "AGORA_WORK_KEY": key, "AGORA_WORK_LAUNCH": launch_id})
                args += ["--settings", str(setup.settings)]
                if resume:
                    args += ["--resume", resume]
                # Only once the previous journal was read: one that could not be read, or
                # was not read at all, may hold a newer recovery point (kiro review, round 10).
                if row is None or (previous is not None and not previous.read_failed):
                    self._prune_journals(folder, keep={launch_id, row.ck_launch if row else None})
            env["CW_TASK"] = project.task
            argv = command_argv("claude", agent_args=args)
        async with self._lock:
            # A close that came in while this launch was being prepared wins: the intent is
            # not written back, and nothing starts.
            if key in self._closed_while_starting:
                self._closed_while_starting.discard(key)
                return {"result": "closed"}
            with self._checkpoint_lock:
                self.store.begin_launch(key=key, source=project.source, task=project.task,
                                        workspace=project.workspace, launch_id=launch_id, launch_mode=mode,
                                        expect_session=row.ck_session if (row and mode == "resume") else None)
        try:
            session = await asyncio.to_thread(
                self.manager.create, project_id=PREFIX + key, label=f"{project.task} · {project.workspace}",
                cwd=Path(project.tool_dir), argv=argv, env_extra=env,
            )
        except TerminalLimitReached as exc:
            self.store.set_shell(key, launch_id, 0, None)  # nothing started: no process to wait for
            self._launches[key] = LaunchInfo(launch_id, mode, "failed", str(exc), restored=restoring)
            return {"result": "failed", "detail": str(exc)}
        except (PtyUnavailable, OSError, ValueError) as exc:
            self.store.set_shell(key, launch_id, 0, None)
            detail = f"{type(exc).__name__}: {exc}"
            self._launches[key] = LaunchInfo(launch_id, mode, "failed", detail, restored=restoring)
            return {"result": "failed", "detail": detail}
        pid = getattr(session._pty, "pid", None)
        try:
            self.store.set_shell(key, launch_id, pid, creation_time(pid) if pid else None)
        except Exception:
            # Running, but with no record a restart could check: it does not stay running
            # behind an error (kiro review round 12).
            await asyncio.to_thread(self.manager.close, session.terminal_id)
            raise
        self._launches[key] = LaunchInfo(launch_id, mode, result, detail, restored=restoring)
        return {"result": result, "detail": detail, "terminal": session.view()}

    def _decide(self, row: WorkSession | None, setup: hooks_module.HookSetup) -> tuple[str, str, str | None, str | None]:
        """(mode, result, detail, session to resume)."""
        if setup.settings is None:
            return "fresh", "no_hook", setup.reason, None
        if row is None or not row.ck_session:
            return "fresh", "fresh_no_record", None, None
        if not SESSION_ID_RE.fullmatch(row.ck_session):
            return "fresh", "fresh_untrusted", None, None
        transcript = trusted_transcript(row.ck_transcript, self.claude_projects)
        if transcript is None:
            return "fresh", "fresh_untrusted", None, None
        # ``stat`` itself, not ``is_file``: from Python 3.14 that answers False for a read
        # that failed, which would say "gone" about one that could not be checked (kiro round 10).
        try:
            present = stat.S_ISREG(transcript.stat().st_mode)
        except FileNotFoundError:
            return "fresh", "fresh_missing", None, None
        except OSError:
            return "fresh", "fresh_untrusted", None, None
        if not present:
            return "fresh", "fresh_untrusted", None, None
        # Claude writes the transcript at startup: one nobody spoke in is there and empty,
        # and resuming it would bring back nothing (owner, 11-03).
        try:
            if not has_conversation(transcript):
                return "fresh", "fresh_empty", None, None
        except OSError:
            return "fresh", "fresh_untrusted", None, None
        return "resume", "resume", None, row.ck_session

    @staticmethod
    def _prune_journals(folder: Path, keep: set[str | None]) -> None:
        try:
            for path in folder.glob("*.jsonl"):
                if path.stem not in keep:
                    path.unlink(missing_ok=True)
        except OSError:
            pass  # an old journal left behind is harmless; it is never read

    async def close(self, key: str) -> list[str]:
        """The person's close: the intent is written first, then the terminal ends. Returns
        the terminals still running afterwards — a close that could not end one says so
        rather than answer "closed" (kiro review round 12)."""
        async with self._lock:
            self._close_seq[key] = self._close_seq.get(key, 0) + 1
            # Written before anything is registered: a write that fails leaves no pending
            # close behind for every later open to wait on (kiro review round 12).
            if self.store.get(key) is not None:
                self.store.set_want_open(key, False)
            closing = self._pending_close.get(key)
            if closing is None:
                task = self._starting.get(key)
                if task is not None:
                    self._closed_while_starting.add(key)
                # The intent is written: from here the close is the service's, not the
                # request's, so a request cancelled meanwhile cannot leave the agent running
                # (codex review). A second close joins this one (kiro review round 12).
                closing = asyncio.create_task(self._finish_close(key, task))
                self._pending_close[key] = closing
                self._close_tasks.add(closing)
                closing.add_done_callback(self._close_tasks.discard)
        return await asyncio.shield(closing)

    async def _finish_close(self, key: str, starting: asyncio.Task | None) -> list[str]:
        try:
            if starting is not None:
                try:
                    await asyncio.shield(starting)
                except Exception:  # noqa: BLE001 - a failed start leaves nothing to close
                    pass
            for session in self._terminals(key):
                if session.exited_at is None:
                    await asyncio.to_thread(self.manager.close, session.terminal_id)
            return [s.terminal_id for s in self._terminals(key) if s.exited_at is None]
        finally:
            if self._pending_close.get(key) is asyncio.current_task():
                del self._pending_close[key]

    async def release_ended(self, key: str, terminal_id: str) -> None:
        """An ended terminal the person closed: they no longer want the project open — unless
        something newer is running or starting for it, or this is not the project's newest
        terminal (an older one closed again speaks for nothing now — kiro review round 11)."""
        async with self._lock:
            terminals = self._terminals(key)
            if key in self._pending_close or key in self._starting or not terminals \
                    or terminals[-1].terminal_id != terminal_id \
                    or any(s.exited_at is None for s in terminals):
                return
            self._close_seq[key] = self._close_seq.get(key, 0) + 1
            if self.store.get(key) is not None:
                self.store.set_want_open(key, False)

    # --- restore -------------------------------------------------------------------------
    async def restore(self) -> dict[str, dict[str, Any]]:
        """Once per server lifetime: a page opened again later gets the same answer."""
        failed = self._restore_task is not None and self._restore_task.done() and (
            self._restore_task.cancelled() or self._restore_task.exception() is not None)
        if self._restore_task is None or failed:
            # A restore that failed as a whole is tried again, not replayed (kiro review).
            self._restore_task = asyncio.create_task(self._restore_all())
        return await asyncio.shield(self._restore_task)

    async def _restore_all(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for row in await asyncio.to_thread(self.store.all):
            if not row.want_open:
                continue
            try:
                outcome = await self.open(row.key, restoring=True)
            except Exception as exc:  # noqa: BLE001 - one project's failure is its own result
                outcome = {"result": "failed", "detail": f"{type(exc).__name__}: {exc}"}
            outcome = {k: v for k, v in outcome.items() if k != "terminal"}  # a copy: a concurrent open shares the dict
            results[row.key] = {"task": row.task, "source": row.source, **outcome}
        return results

    # --- what the page shows -------------------------------------------------------------
    def confirmation(self, row: WorkSession, journal: Journal | None, now: float | None = None) -> str | None:
        """What this server's launch of ``row`` turned out to be, from its first report."""
        info = self._launches.get(row.key)
        if info is None or info.launch_id != row.launch_id or row.workspace != "claude" \
                or info.result in ("no_hook", "failed"):
            return None
        if journal is not None and journal.launch_id != row.launch_id:
            journal = None  # read before a newer launch began: says nothing about this one
        first = journal.first_start if journal else None
        if first is not None:
            if info.mode == "resume":
                if first.source != "resume":
                    return "not_resumed"
                return "resumed" if first.session_id == row.expect_session else "resumed_other"
            return "started" if first.source == "startup" else "unconfirmed"
        if journal is not None and journal.first_start_invalid:
            return "unreadable_report"
        if (now or time.time()) - (row.launched_at or 0) > CONFIRM_SECONDS:
            return "unconfirmed"
        return "pending"

    def view(self) -> dict[str, Any]:
        scan = self.scan()
        rows = {r.key: r for r in self.store.all()}
        board = self.store.board()
        names = self.store.names()
        projects = []
        for project in scan.projects:
            row = rows.get(project.key)
            journal = self._read(row) if row else None
            row = self.store.get(project.key) if row else None
            terminals = self._terminals(project.key)
            latest = terminals[-1] if terminals else None
            info = self._launches.get(project.key)
            alive = latest is not None and latest.exited_at is None
            projects.append({
                **project.payload(),
                "want_open": bool(row and row.want_open),
                "terminal": latest.view() if latest else None,
                "starting": project.key in self._starting,
                "launch": None if info is None or row is None or info.launch_id != row.launch_id else {
                    "result": info.result, "detail": info.detail, "restored": info.restored,
                    "confirm": self.confirmation(row, journal),
                },
                "stale": bool(row and row.ck_stale),
                "agent_exited": bool(alive and journal is not None and journal.ended),
                "stage": self._stage_of(project.key, board, row),
                "position": board[project.key][1] if project.key in board else None,
                "name": names.get(project.key),
            })
        listed = {p.key for p in scan.projects}
        # A project whose terminal is still running but that the scan no longer lists (its
        # task.yaml became unreadable, it was archived): the terminal does not vanish from
        # the page while it runs — it is listed here, to be looked at or closed.
        detached = []
        for session in self.manager.list():
            key = session.project_id[len(PREFIX):] if session.project_id.startswith(PREFIX) else None
            if key and key not in listed and session.exited_at is None:
                row = rows.get(key)
                detached.append({"key": key, "task": row.task if row else key, "terminal": session.view()})
        unreadable = {work_key(u["source"], u["task"]) for u in scan.unreadable if u.get("task")}
        dark_sources = {os.path.normcase(u["source"]) for u in scan.unreadable if not u.get("task")}
        # Unreadable is not "no longer active": those are listed as unreadable, and only
        # what the scan read and found inactive or gone is offered for closing here.
        detached_keys = {d["key"] for d in detached}
        missing = [{"key": r.key, "task": r.task, "source": r.source}
                   for r in rows.values() if r.want_open and r.key not in listed and r.key not in unreadable
                   and r.key not in detached_keys and os.path.normcase(r.source) not in dark_sources]
        restore = None
        if self._restore_task is not None and self._restore_task.done() and not self._restore_task.cancelled() \
                and self._restore_task.exception() is None:
            restore = {}
            for key, entry in self._restore_task.result().items():
                info = self._launches.get(key)
                # A later launch the person started replaces what the restore did: its entry
                # no longer describes the terminal they are looking at (code review P2).
                restore[key] = {**entry, "superseded": bool(info is not None and not info.restored)}
        return {
            "projects": projects,
            "unreadable": [
                {**u, **({"key": work_key(u["source"], u["task"]),
                          "want_open": bool(rows.get(work_key(u["source"], u["task"]))
                                            and rows[work_key(u["source"], u["task"])].want_open)}
                         if u.get("task") else {})}
                for u in scan.unreadable
            ],
            "inactive": scan.inactive, "shelved": [p.payload() for p in scan.shelved],
            "put_away": sorted(self._put_away - {p.key for p in scan.projects}), "missing": missing,
            "detached": detached,
            "restore": restore, "restoring": self._restore_task is not None and not self._restore_task.done(),
            "hook_problem": self._hooks.reason if self._hooks is not None else None,
        }

    # --- pause / archive / restore (CR-80) -------------------------------------------------
    async def shelve(self, key: str, action: str) -> None:
        """``pause`` or ``archive`` an active project: its own task.yaml says so afterwards.
        Refused while its terminal is open or meant to be: a project put away with a
        terminal running would leave the page with a terminal it no longer lists."""
        if action not in ("pause", "archive"):
            raise ValueError(action)
        await self._owned(self._shelve(key, action))

    async def set_name(self, key: str, name: str | None) -> str | None:
        """The card's name in Agora (CR-85); blank or None removes it. Returns what is
        stored. Only a listed project: a name for a key nothing shows would never be seen."""
        cleaned = normalise_name(name)
        async with self._lock:
            project = await asyncio.to_thread(self._project, key)
            if project is None:
                raise WorkProjectNotFound(key)
            await asyncio.to_thread(self.store.set_name, key, cleaned)
        return cleaned

    async def set_workflow(self, key: str, features: dict[str, bool], base: str) -> str:
        """Write the project's workflow selection into its task.yaml (CR-75 slice 4); the
        new text's sha256 back. claude-workspace and kiro-workspace projects: the claude
        hook and the kiro steering file both read it; the defaults differ (``aidlc``). No
        terminal check — the session reads the file when it next starts.

        Run as a task the service owns, as the shelf changes are: a cancelled request must
        not release the lock while the write still runs in its thread (codex review round 1)."""
        task = asyncio.create_task(self._set_workflow(key, features, base))
        self._shelf_tasks.add(task)
        task.add_done_callback(self._shelf_tasks.discard)
        return await asyncio.shield(task)

    async def _set_workflow(self, key: str, features: dict[str, bool], base: str) -> str:
        async with self._lock:
            project = await asyncio.to_thread(self._project, key)
            if project is None:
                raise WorkProjectNotFound(key)
            if await asyncio.to_thread(workflow_workspace, Path(project.source)) != project.workspace:
                # The read would have offered no switch either: a kiro-workspace without the
                # steering file, or a workspace whose kind could not be established.
                raise workflow_selection.WorkflowRefused(
                    "this workspace's sessions are not known to read the workflow selection"
                    + (" (no .kiro/steering/workflow-selection.md)" if project.workspace == "kiro" else ""))
            path = Path(project.source) / "tasks" / project.task / "task.yaml"
            return await asyncio.to_thread(_write_workflow, path, features, base,
                                           workflow_selection.defaults_for(project.workspace))

    async def _owned(self, work) -> None:
        """Run a shelf change as a task the service holds: a cancelled request neither
        releases the lock nor moves the count while the file write is still running in
        its thread (codex review CR-80 round 3) — the change ends, then the lock goes."""
        task = asyncio.create_task(work)
        self._shelf_tasks.add(task)
        task.add_done_callback(self._shelf_tasks.discard)
        await asyncio.shield(task)

    async def _shelve(self, key: str, action: str) -> None:
        async with self._lock:
            project = await asyncio.to_thread(self._project, key)
            if project is None:
                raise WorkProjectNotFound(key)
            if self._busy(key):
                raise WorkProjectBusy(key)
            source = Path(project.source)
            # Counted before the write and again after it, whatever its outcome: an open
            # whose scan came before the write, or ran while it was under way, does not
            # launch from that scan (codex review CR-80 rounds 1 and 2).
            self._shelve_seq[key] = self._shelve_seq.get(key, 0) + 1
            try:
                if action == "pause":
                    await asyncio.to_thread(shelf.pause, source, project.task)
                else:
                    await asyncio.to_thread(shelf.archive, source, project.task,
                                            move=project.workspace == "claude")
                self._put_away.add(key)
            finally:
                self._shelve_seq[key] += 1

    def _busy(self, key: str) -> bool:
        """Its terminal is open, opening, closing, or meant to be open. Any terminal not
        ended counts, closing ones included: a close that timed out leaves one running
        with nothing else marking it (codex review CR-80 round 1). Under ``_lock``."""
        row = self.store.get(key)
        return (key in self._starting or key in self._pending_close
                or any(s.exited_at is None for s in self._terminals(key))
                or (row is not None and row.want_open))

    # --- todo / doing and the order (CR-81/82) ----------------------------------------------
    def _stage_of(self, key: str, board: dict[str, tuple[str, int | None]] | None = None,
                  row: WorkSession | None = None) -> str:
        """The person's stage for a project. One never placed is "doing" if it is meant to
        be open — so what was open before this existed stays on the work page — else "todo"."""
        board = self.store.board() if board is None else board
        if key in board:
            return board[key][0]
        row = self.store.get(key) if row is None else row
        return "doing" if row is not None and row.want_open else "todo"

    async def set_stage(self, key: str, stage: str) -> None:
        """``todo`` is refused while the terminal is open or meant to be: the work page
        lists only what is doing, and must never hide a running terminal."""
        if stage not in ("todo", "doing"):
            raise ValueError(stage)
        async with self._lock:
            if await asyncio.to_thread(self._project, key) is None:
                raise WorkProjectNotFound(key)
            if stage == "todo" and self._busy(key):
                raise WorkProjectBusy(key)
            self.store.set_stage(key, stage)

    async def set_order(self, keys: list[str]) -> None:
        """The work page's order, as the person dragged it: every key must be listed."""
        if len(set(keys)) != len(keys):
            raise ValueError("a project appears twice")
        async with self._lock:
            listed = {p.key for p in (await asyncio.to_thread(self.scan)).projects}
            unknown = [k for k in keys if k not in listed]
            if unknown:
                raise WorkProjectNotFound(unknown[0])
            board = self.store.board()
            self.store.set_order(keys, {k: self._stage_of(k, board) for k in keys})

    async def unshelve(self, key: str) -> None:
        """Back to ``status: active`` (out of ``_archive/`` first, where it is there)."""
        await self._owned(self._unshelve(key))

    async def _unshelve(self, key: str) -> None:
        async with self._lock:
            scan = await asyncio.to_thread(self.scan)
            held = next((p for p in scan.shelved if p.key == key), None)
            if held is None:
                raise WorkProjectNotFound(key)
            await asyncio.to_thread(shelf.restore, Path(held.source), held.task, archived_dir=held.archived_dir)
            self._put_away.discard(work_key(held.source, held.task))


def _write_workflow(path: Path, features: dict[str, bool], base: str,
                    defaults: dict[str, bool] = workflow_selection.DEFAULTS) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise workflow_selection.WorkflowRefused("task.yaml is not there") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise workflow_selection.WorkflowRefused(f"task.yaml could not be read ({type(exc).__name__})") from None
    # Compared here and replaced below with no lock across processes: a write landing in
    # between is overwritten. Workspace CR-84 (defer, OPEN) carries that.
    if workflow_selection.text_sha(text) != base:
        raise workflow_selection.WorkflowChanged("task.yaml changed since this page read it; reopen and choose again")
    edited = workflow_selection.edit_workflow(text, features, loader=StrictLoader, defaults=defaults)
    try:
        shelf._write(path, edited)
    except shelf.ShelfRefused as exc:
        raise workflow_selection.WorkflowRefused(str(exc)) from None
    return workflow_selection.text_sha(edited)
