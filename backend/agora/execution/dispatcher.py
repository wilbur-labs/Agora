"""Bounded async dispatch of durable execution runs."""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from agora.projects import ProjectRegistry
from agora.orchestration.runtime import RuntimeLaunchError, resolve_runtime_command
from agora.orchestration.provider_usage import RuntimeResultFormat, normalize_native_output
from agora.tasks.store import TaskNotFoundError, TaskStore
from agora.attention.store import AttentionStore
from agora.attention.models import AttentionKind, AttentionUrgency, CreateAttentionRequest, REVIEW_REQUESTER
from agora.attention.bridges.codex_app_server import CodexAppServerError, CodexAppServerRunner
from agora.attention.bridges.codex_broker import CodexApprovalBroker

from agora.workspaces.naming import worktree_branch

from .adapters import ExecutionAdapter
from .deliverables import is_ready_worktree, list_changes, read_file
from .models import CancelRunRequest, CreateRunRequest, ExecutionRun, RunState, OUTPUT_TAIL_LIMIT
from .store import ExecutionStore, RunConflictError, RunValidationError
from .security import redact_text


# Role tags carried in a run's metadata so the dispatcher can tell a developer
# run (which triggers cross-review when it succeeds) from a review run (which
# raises an approval for the human) — and never review a review (no loops).
ROLE_KEY = "agora_role"
ROLE_WORKER = "worker"
ROLE_REVIEW = "review"
REVIEWS_RUN_KEY = "reviews_run_id"
_REVIEW_PROMPT_BUDGET = 12_000
_REVIEW_FILE_CHARS = 1_500


class ExecutionDispatcher:
    # Only claude's CLI print mode yields parseable per-run token usage today.
    # codex runs through the app-server bridge and kiro emits plain text, so
    # neither surfaces usage on this path (see settings token capture notes).
    _USAGE_RESULT_FORMATS = {"claude": RuntimeResultFormat.CLAUDE_JSON_V1}

    def __init__(
        self,
        store: ExecutionStore,
        projects: ProjectRegistry,
        adapters: dict[str, ExecutionAdapter],
        *,
        max_concurrent_global: int = 4,
        max_concurrent_per_project: int = 2,
        allowed_workspace_roots: list[Path] | None = None,
        auto_review: bool = False,
    ):
        if max_concurrent_global < 1 or max_concurrent_per_project < 1:
            raise ValueError("execution concurrency limits must be positive")
        self.store = store
        self.projects = projects
        self.adapters = adapters
        # Opt-in: when true, a succeeded developer run auto-dispatches an
        # independent review run by the project's reviewer agent. Default off
        # keeps single-run behavior (and existing tests) unchanged.
        self.auto_review = auto_review
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
            # Role/target-run are set at create time (metadata) and still present
            # here (finish overwrites result_metadata later). Capture before start.
            role = str((run.result_metadata or {}).get(ROLE_KEY, ROLE_WORKER))
            reviews_run_id = (run.result_metadata or {}).get(REVIEWS_RUN_KEY)
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
            # Never launch an agent unless the workspace is exactly this project/
            # adapter's provisioned linked worktree: its own top-level, a linked
            # worktree sharing the project repo, on the expected branch, with a
            # HEAD. This binds execution to a provisioned workspace, so an agent
            # can never write into a repo it does not own (a repo root, the
            # project root, the outer Agora checkout) nor an unrelated worktree.
            # Off the event loop: the check shells out to git. Fail closed.
            expected_branch = worktree_branch(run.project_id, run.adapter)
            ready = await asyncio.to_thread(
                is_ready_worktree, workspace, project.root, expected_branch
            )
            if not ready:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message="workspace is not a provisioned worktree for this project/adapter; provision it before running",
                )
            if adapter.bridge_mode == "codex_app_server":
                return await self._execute_codex_app_server(
                    running, adapter, workspace, role, reviews_run_id,
                )
            # CLI adapters only: resolve the executable via PATHEXT and unwrap
            # Windows .cmd/.bat shims (npm's codex.cmd) into a CreateProcess-
            # launchable form. Only the command that will actually run is resolved.
            try:
                command = resolve_runtime_command(list(adapter.build_command(run.prompt)))
            except RuntimeLaunchError as exc:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=redact_text(f"process start failed: {exc}"),
                )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={
                        **os.environ,
                        "AGORA_TASK_ID": run.task_id,
                        "AGORA_RUN_ID": run.run_id,
                        "AGORA_PROJECT_ID": run.project_id,
                    },
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

                semantic, usage_metadata = self._observe_usage(
                    run.adapter, stdout.decode("utf-8", errors="replace"), run_id, run.prompt
                )
                out = redact_text(semantic)
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
                finished = self.store.finish(
                    run_id,
                    target,
                    expected_version=current.version,
                    exit_code=proc.returncode,
                    stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                    stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                    error_message=error,
                    result_metadata={**usage_metadata, ROLE_KEY: role, REVIEWS_RUN_KEY: reviews_run_id},
                )
                await self._after_run(finished, role, reviews_run_id)
                return finished
            finally:
                self._active.pop(run_id, None)

    async def _execute_codex_app_server(
        self, running: ExecutionRun, adapter: ExecutionAdapter, workspace: Path,
        role: str = ROLE_WORKER, reviews_run_id: str | None = None,
    ) -> ExecutionRun:
        attention = AttentionStore(self.store.tasks)
        broker = CodexApprovalBroker(attention, task_id=running.task_id, run_id=running.run_id)
        runner = CodexAppServerRunner(broker)
        attached = running

        async def on_process(proc: asyncio.subprocess.Process) -> None:
            nonlocal attached
            self._active[running.run_id] = proc
            try:
                attached = self.store.attach_pid(
                    running.run_id, expected_version=attached.version, pid=proc.pid,
                )
            except RunConflictError:
                await self._stop(proc)
                raise

        env = {
            **os.environ,
            "AGORA_TASK_ID": running.task_id,
            "AGORA_RUN_ID": running.run_id,
            "AGORA_PROJECT_ID": running.project_id,
        }
        out = b""
        err = b""
        timed_out = False
        failure: str | None = None
        try:
            # Resolve the executable via PATHEXT and unwrap Windows .cmd/.bat
            # shims (npm's codex.cmd) so the app-server launches on Windows.
            resolved = tuple(resolve_runtime_command(list(adapter.app_server_command)))
            result = await runner.run(
                command=resolved, prompt=running.prompt, cwd=workspace,
                timeout_seconds=running.timeout_seconds, env=env, on_process=on_process,
            )
            out, err = result.stdout, result.stderr
            timed_out = result.timed_out
            failure = (
                f"timeout after {running.timeout_seconds}s" if timed_out
                else (redact_text(f"app-server error: {result.error}") if result.error else None)
            )
        except (CodexAppServerError, RunConflictError, FileNotFoundError, OSError, RuntimeLaunchError) as exc:
            failure = redact_text(f"app-server error: {type(exc).__name__}: {exc}")
        finally:
            self._active.pop(running.run_id, None)

        current = self.store.require(running.run_id)
        safe_out = redact_text(out.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:]
        safe_err = redact_text(err.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:]
        if current.state == RunState.CANCELLED:
            return self.store.record_cancelled_output(
                running.run_id, expected_version=current.version, stdout_tail=safe_out,
                stderr_tail=safe_err, exit_code=None,
            )
        target = RunState.TIMED_OUT if timed_out else (RunState.SUCCEEDED if failure is None else RunState.FAILED)
        finished = self.store.finish(
            running.run_id, target, expected_version=current.version,
            exit_code=0 if target == RunState.SUCCEEDED else None,
            stdout_tail=safe_out, stderr_tail=safe_err, error_message=failure,
            result_metadata={ROLE_KEY: role, REVIEWS_RUN_KEY: reviews_run_id},
        )
        await self._after_run(finished, role, reviews_run_id)
        return finished

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

    def _observe_usage(
        self, adapter: str, raw_stdout: str, run_id: str, prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        """Return clean semantic text and token usage; parsing never breaks a run."""
        result_format = self._USAGE_RESULT_FORMATS.get(adapter)
        if result_format is None:
            return raw_stdout, {}
        try:
            semantic, observation = normalize_native_output(
                adapter=adapter,
                result_format=result_format,
                stdout=raw_stdout,
                run_id=run_id,
                prompt=prompt,
            )
        except Exception:
            # A malformed or oversized payload must not fail the run; fall back
            # to the raw output with no usage rather than raising.
            return raw_stdout, {}
        return semantic, self._usage_metadata(observation)

    @staticmethod
    def _usage_metadata(observation: Any) -> dict[str, Any]:
        if observation is None or observation.token_measurement == "unavailable":
            return {}
        # NOTE: keys must not contain "token" — sanitize_data() redacts any key
        # matching a secret marker (KEY/TOKEN/SECRET/...). These are token counts,
        # not secrets, so they live under neutral names inside the `usage` block.
        return {
            "usage": {
                "input": observation.input_tokens,
                "output": observation.output_tokens,
                "total": observation.total_tokens,
                "model": observation.model,
                "measurement": observation.token_measurement,
                "cost_usd": observation.cost_usd,
            }
        }

    async def _after_run(self, finished: ExecutionRun, role: str, reviews_run_id: str | None) -> None:
        """Cross-review orchestration. Never let it break the primary run."""
        try:
            if role == ROLE_WORKER and finished.state == RunState.SUCCEEDED:
                if self.auto_review:
                    await self._dispatch_review(finished)
            elif role == ROLE_REVIEW and finished.state == RunState.SUCCEEDED:
                self._raise_review_attention(finished, reviews_run_id, AttentionKind.APPROVAL)
            elif role == ROLE_REVIEW and finished.state in {RunState.FAILED, RunState.TIMED_OUT}:
                self._raise_review_attention(finished, reviews_run_id, AttentionKind.BLOCKER)
        except Exception:
            # Orchestration is best-effort; a review-dispatch failure must not
            # change the outcome of the run that just completed.
            pass

    async def _dispatch_review(self, worker_run: ExecutionRun) -> None:
        """Queue an independent review run by the project's reviewer agent."""
        try:
            project = self.projects.get(worker_run.project_id)
        except KeyError:
            return
        reviewer = project.reviewer_agent
        if reviewer not in self.adapters:
            return  # reviewer runtime not configured; skip silently
        task = self.store.tasks.get(worker_run.task_id)
        if task is None:
            return
        # The prompt build shells out to git (readiness + list_changes + reads);
        # keep it off the event loop.
        prompt = await asyncio.to_thread(self._review_prompt, worker_run)
        request = CreateRunRequest(
            task_id=worker_run.task_id,
            adapter=reviewer,
            prompt=prompt,
            timeout_seconds=worker_run.timeout_seconds,
            actor=REVIEW_REQUESTER,
            expected_task_version=task.version,
            metadata={ROLE_KEY: ROLE_REVIEW, REVIEWS_RUN_KEY: worker_run.run_id},
        )
        review_run = self.queue(request)
        self.schedule(review_run.run_id)

    def _review_prompt(self, worker_run: ExecutionRun) -> str:
        """Build a bounded review prompt from the developer run's deliverables."""
        workspace = Path(worker_run.workspace).expanduser().resolve()
        parts = [
            "You are an independent code reviewer. A developer agent produced the "
            "changes below. Review them for correctness, edge cases, and safety. "
            "List concrete findings (file + issue); if everything looks correct, say so clearly.",
            f"\n## Developer's own summary\n{worker_run.stdout_tail[:_REVIEW_FILE_CHARS]}",
        ]
        budget = _REVIEW_PROMPT_BUDGET
        # Only read the worker run's own provisioned worktree, never a surrounding
        # or unrelated repository.
        try:
            project_root = self.projects.get(worker_run.project_id).root
        except (KeyError, LookupError):
            project_root = None
        expected_branch = worktree_branch(worker_run.project_id, worker_run.adapter)
        ready = project_root is not None and is_ready_worktree(workspace, project_root, expected_branch)
        files = list_changes(workspace) if ready else []
        if not files:
            parts.append("\n## Changes\n(No changed files detected in the workspace.)")
        for entry in files:
            try:
                content = read_file(workspace, entry["path"])["content"]
            except (FileNotFoundError, PermissionError, OSError):
                continue
            block = f"\n## FILE: {entry['path']} ({entry['status']})\n{content[:_REVIEW_FILE_CHARS]}"
            if len(block) > budget:
                parts.append("\n[additional files omitted for length]")
                break
            parts.append(block)
            budget -= len(block)
        return "\n".join(parts)[:15_900]

    def _raise_review_attention(
        self, review_run: ExecutionRun, reviews_run_id: str | None, kind: AttentionKind,
    ) -> None:
        """Turn a completed review run into a human attention item on the task."""
        if kind == AttentionKind.APPROVAL:
            title = f"评审完成,待你批准(评审:{review_run.adapter})"
            body = review_run.stdout_tail or "(评审未产出文本)"
        else:
            title = f"评审运行未完成(评审:{review_run.adapter})"
            body = review_run.error_message or review_run.stderr_tail or "(无错误详情)"
        AttentionStore(self.store.tasks).create(
            CreateAttentionRequest(
                task_id=review_run.task_id,
                run_id=reviews_run_id,
                kind=kind,
                urgency=AttentionUrgency.NORMAL,
                title=title[:500],
                body=body[:32_000],
                requester=REVIEW_REQUESTER,
            )
        )

    def _workspace_is_allowed(self, workspace: Path, project_root: Path) -> bool:
        allowed_roots = [project_root.expanduser().resolve(), *self.allowed_workspace_roots]
        return any(workspace.is_relative_to(root) for root in allowed_roots)

    def is_workspace_readable(self, project_id: str, workspace: Path) -> bool:
        """Read-only confinement check for a run's workspace (delivery viewing)."""
        try:
            project = self.projects.get(project_id)
        except KeyError:
            return False
        if project is None:
            return False
        return self._workspace_is_allowed(workspace.expanduser().resolve(), project.root)

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
