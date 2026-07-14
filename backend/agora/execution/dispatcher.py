"""Bounded async dispatch of durable execution runs."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from agora.projects import ProjectRegistry
from agora.tasks.store import TaskNotFoundError, TaskStore

from .adapters import ExecutionAdapter
from .models import CancelRunRequest, CreateRunRequest, ExecutionRun, RunState, OUTPUT_TAIL_LIMIT
from .store import ExecutionStore, RunConflictError, RunValidationError
from .security import redact_text


class ExecutionDispatcher:
    def __init__(
        self,
        store: ExecutionStore,
        projects: ProjectRegistry,
        adapters: dict[str, ExecutionAdapter],
        *,
        max_concurrent_global: int = 4,
        max_concurrent_per_project: int = 2,
        allowed_workspace_roots: list[Path] | None = None,
    ):
        if max_concurrent_global < 1 or max_concurrent_per_project < 1:
            raise ValueError("execution concurrency limits must be positive")
        self.store = store
        self.projects = projects
        self.adapters = adapters
        self.allowed_workspace_roots = [root.expanduser().resolve() for root in (allowed_workspace_roots or [])]
        self._global_limit = asyncio.Semaphore(max_concurrent_global)
        self._project_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(max_concurrent_per_project)
        )
        self._active: dict[str, asyncio.subprocess.Process] = {}
        self._scheduled: set[asyncio.Task[Any]] = set()

    def queue(self, request: CreateRunRequest) -> ExecutionRun:
        task = self.store.tasks.get(request.task_id)
        if task is None:
            raise TaskNotFoundError(request.task_id)
        adapter = self.adapters.get(request.adapter)
        if adapter is None:
            raise RunValidationError(f"Unknown or disabled execution adapter: {request.adapter}")
        try:
            project = self.projects.get(task.project_id)
            workspace = adapter.workspace(project.workspaces)
        except KeyError as exc:
            raise RunValidationError(str(exc)) from None
        workspace = workspace.resolve()
        if not self._workspace_is_allowed(workspace, project.root):
            raise RunValidationError("Execution workspace is outside the configured allowed roots")
        return self.store.create(
            request,
            project_id=project.project_id,
            workspace=workspace,
            stored_command=adapter.stored_command(),
        )

    def schedule(self, run_id: str) -> None:
        task = asyncio.create_task(self._execute_guarded(run_id), name=f"agora-run-{run_id}")
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled_done)

    def resume_queued(self) -> None:
        for run in self.store.list(state=RunState.QUEUED, limit=500):
            self.schedule(run.run_id)

    async def _execute_guarded(self, run_id: str) -> ExecutionRun:
        try:
            return await self.execute(run_id)
        except asyncio.CancelledError:
            proc = self._active.get(run_id)
            if proc is not None:
                await self._stop(proc)
            current = self.store.require(run_id)
            if current.state == RunState.RUNNING:
                self.store.abandon(
                    run_id,
                    expected_version=current.version,
                    reason="dispatcher stopped before the process completed",
                )
            raise
        except Exception as exc:
            current = self.store.require(run_id)
            if current.state in {RunState.QUEUED, RunState.RUNNING}:
                return self.store.finish(
                    run_id,
                    RunState.FAILED,
                    expected_version=current.version,
                    exit_code=None,
                    stdout_tail=current.stdout_tail,
                    stderr_tail=current.stderr_tail,
                    error_message=redact_text(f"dispatcher error: {exc}"),
                )
            return current

    def _scheduled_done(self, task: asyncio.Task[Any]) -> None:
        self._scheduled.discard(task)
        if not task.cancelled():
            task.exception()

    async def execute(self, run_id: str) -> ExecutionRun:
        initial = self.store.require(run_id)
        if initial.state != RunState.QUEUED:
            return initial
        async with self._global_limit, self._project_limits[initial.project_id]:
            run = self.store.require(run_id)
            if run.state != RunState.QUEUED:
                return run
            adapter = self.adapters.get(run.adapter)
            workspace = Path(run.workspace).expanduser().resolve()
            if adapter is None:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="adapter is no longer configured",
                )
            try:
                project = self.projects.get(run.project_id)
            except KeyError:
                project = None
            if project is None or not self._workspace_is_allowed(workspace, project.root):
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace is no longer allowed",
                )
            if not workspace.is_dir():
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace not found",
                )

            command = adapter.build_command(run.prompt)
            try:
                running = self.store.start(run_id, expected_version=run.version)
            except RunConflictError:
                return self.store.require(run_id)

            # Resolve again immediately before launch. A local writer can still swap a
            # component after this check on platforms without directory-handle cwd APIs.
            workspace = workspace.resolve()
            if not self._workspace_is_allowed(workspace, project.root) or not workspace.is_dir():
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace changed before process start",
                )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, OSError) as exc:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=f"process start failed: {type(exc).__name__}",
                )

            self._active[run_id] = proc
            try:
                try:
                    running = self.store.attach_pid(
                        run_id, expected_version=running.version, pid=proc.pid
                    )
                except RunConflictError:
                    await self._stop(proc)
                    return self.store.require(run_id)

                timed_out = False
                stdout_reader = asyncio.create_task(self._read_tail(proc.stdout))
                stderr_reader = asyncio.create_task(self._read_tail(proc.stderr))
                try:
                    await asyncio.wait_for(proc.wait(), timeout=running.timeout_seconds)
                except (TimeoutError, asyncio.TimeoutError):
                    timed_out = True
                    await self._stop(proc)
                except asyncio.CancelledError:
                    await self._stop(proc)
                    await asyncio.gather(stdout_reader, stderr_reader, return_exceptions=True)
                    raise

                stdout, stderr = await asyncio.gather(stdout_reader, stderr_reader)

                out = redact_text(stdout.decode("utf-8", errors="replace"))
                err = redact_text(stderr.decode("utf-8", errors="replace"))
                current = self.store.require(run_id)
                if current.state == RunState.CANCELLED:
                    return self.store.record_cancelled_output(
                        run_id,
                        expected_version=current.version,
                        stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                        stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                        exit_code=proc.returncode,
                    )
                target = RunState.TIMED_OUT if timed_out else (
                    RunState.SUCCEEDED if proc.returncode == 0 else RunState.FAILED
                )
                error = (
                    f"timeout after {running.timeout_seconds}s" if timed_out
                    else (None if proc.returncode == 0 else "process exited with a non-zero status")
                )
                return self.store.finish(
                    run_id,
                    target,
                    expected_version=current.version,
                    exit_code=proc.returncode,
                    stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                    stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                    error_message=error,
                )
            finally:
                self._active.pop(run_id, None)

    async def cancel(self, run_id: str, request: CancelRunRequest) -> ExecutionRun:
        cancelled = await asyncio.to_thread(self.store.cancel, run_id, request)
        proc = self._active.get(run_id)
        if proc is not None and proc.returncode is None:
            await self._stop(proc)
        return cancelled

    async def shutdown(self) -> None:
        tasks = list(self._scheduled)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _workspace_is_allowed(self, workspace: Path, project_root: Path) -> bool:
        allowed_roots = [project_root.expanduser().resolve(), *self.allowed_workspace_roots]
        return any(workspace.is_relative_to(root) for root in allowed_roots)

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()

    @staticmethod
    async def _read_tail(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        tail = bytearray()
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > OUTPUT_TAIL_LIMIT * 2:
                del tail[:-OUTPUT_TAIL_LIMIT]
        return bytes(tail[-OUTPUT_TAIL_LIMIT:])


def redact_output(value: str) -> str:
    """Compatibility wrapper for callers that imported the original helper."""
    return redact_text(value)
