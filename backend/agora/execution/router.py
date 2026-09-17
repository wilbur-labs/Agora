"""REST API for durable delivery execution runs."""
from __future__ import annotations

import asyncio
import hashlib

import mimetypes

from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from pydantic import BaseModel

from agora.config.settings import get_config
from agora.fsprobe import probe_path
from agora.projects import ProjectRegistry
from agora.tasks.models import TaskManifest
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import (
    InvalidTransitionError,
    StaleTaskVersionError,
    TaskNotFoundError,
    TaskStore,
)

from agora.workspaces.naming import worktree_branch

from .adapters import build_adapter_registry
from .deliverables import (
    is_ready_worktree,
    list_changes,
    preview_of,
    read_bytes_verified,
    read_file,
    resolve_confined,
)
from . import deliverables as _deliverables_module
from .dispatcher import ExecutionDispatcher, workspace_unavailable_reason
from .remote_wiring import RemoteTargetMismatch, adapter_for_recorded_target, remote_components_for, remote_target_of
from .handoff import HandoffError, hand_off
from .models import (
    ACTIVE_RUN_STATES,
    ROLE_CHAT,
    ROLE_WORKER,
    AdapterCapability,
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    RunState,
    RunSummary,
    TERMINAL_RUN_STATES,
)
from .runlog import RunLogError
from .security import redact_text
from .sessions import AgentSession, SessionClearRefused
from .store import (
    OUTPUT_TAIL_LIMIT,
    ExecutionStore,
    RunConflictError,
    RunGateError,
    RunNotFoundError,
    RunValidationError,
)


class HandoffResponse(BaseModel):
    task: TaskManifest
    run_id: str | None = None
    workspace: str | None = None
    commit: str | None = None
    committed: bool = False
    files: list[dict] = []
    note: str | None = None


router = APIRouter(tags=["execution"])

def _ready_worktree_for_run(registry: ProjectRegistry, run: ExecutionRun) -> bool:
    """Whether a run's workspace is exactly its project/adapter's provisioned
    worktree (own top-level, linked, same repo as the project, expected branch,
    HEAD). Fail closed on anything else."""
    try:
        project = registry.get(run.project_id)
    except (KeyError, LookupError):
        return False
    workspace = Path(run.workspace).expanduser().resolve()
    return is_ready_worktree(workspace, project.root, worktree_branch(run.project_id, run.adapter))


def _require_ready_worktree(registry: ProjectRegistry, run: ExecutionRun) -> None:
    """Fail closed: only serve deliverable content from a run's own provisioned
    worktree, never from a repo root, an unrelated worktree, or a plain dir."""
    if not _ready_worktree_for_run(registry, run):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")


def _delivered_entry(store: ExecutionStore, run: ExecutionRun, path: str) -> dict | None:
    """The snapshot entry proving this run delivered ``path``.

    Returns None only when the run has no snapshot at all — runs recorded before
    snapshots existed keep the previous behavior (the worktree gate alone).
    Otherwise it either returns a verifiable entry or refuses: a later run in the
    same worktree can rewrite or recreate a path, and serving that under the
    earlier run's card is exactly the untruth the snapshot exists to remove.
    """
    snapshot = store.deliverable_snapshot(run.run_id)
    if snapshot is None:
        if run.state in TERMINAL_RUN_STATES:
            # The list endpoint already says this run's files are unrecoverable.
            # Reading from the live worktree here would contradict it, and would
            # hand back a later run's file under this run's id.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This run ended without a deliverable record, so no file can be "
                "attributed to it",
            )
        return None  # active run: the live worktree is the answer
    files = snapshot.get("files") or []
    entry = next(
        (item for item in files if isinstance(item, dict) and item.get("path") == path),
        None,
    )
    if entry is None:
        if snapshot.get("truncated"):
            # The recorded list stopped at MAX_FILES, so absence from it is not
            # proof the run did not deliver this file — it is simply unknown.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This run changed more files than were recorded, so this one cannot be "
                "attributed to it",
            )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This run did not deliver that file")
    if entry.get("sha256") is None:
        # No digest means no verifiable identity — deleted, not a regular file, or
        # above the hash limit. A later run can recreate the same path, so serving
        # whatever is there now would present its bytes as this run's delivery.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run recorded no verifiable content for that file, so it cannot be opened",
        )
    return entry


def _verified_bytes(workspace: Path, path: str, entry: dict) -> bytes:
    """The file's bytes, read once and proven to be what the run delivered.

    Digesting one read and then serving a second (or handing a path to a response
    that opens it later) leaves a window for the file to change in between, so
    the caller must build its response from exactly these bytes.
    """
    try:
        raw = read_bytes_verified(workspace, path, str(entry["sha256"]))
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
    if raw is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This file changed after the run ended, so it no longer holds what the run delivered",
        )
    return raw


@lru_cache(maxsize=1)
def get_execution_store() -> ExecutionStore:
    store = ExecutionStore(get_task_store())
    store.recover_abandoned()
    return store


@lru_cache(maxsize=1)
def get_execution_dispatcher() -> ExecutionDispatcher:
    config = get_config()
    limits = config.get("execution", {})
    projects = ProjectRegistry(config)
    allowed_roots = []
    for value in limits.get("allowed_workspace_roots", []):
        root = Path(value).expanduser()
        if not root.is_absolute():
            root = projects.project_root / root
        allowed_roots.append(root.resolve())
    return ExecutionDispatcher(
        get_execution_store(),
        projects,
        build_adapter_registry(config),
        max_concurrent_global=int(limits.get("max_concurrent_global", 4)),
        max_concurrent_per_project=int(limits.get("max_concurrent_per_project", 2)),
        allowed_workspace_roots=allowed_roots,
        auto_review=bool(limits.get("auto_review", False)),
    )


@router.get("/execution-adapters", response_model=list[AdapterCapability])
def list_execution_adapters(
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    return [adapter.capability() for adapter in dispatcher.adapters.values()]


@router.post("/runs", response_model=ExecutionRun, status_code=status.HTTP_201_CREATED)
async def create_run(
    request: CreateRunRequest,
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    try:
        # `kind` is the client's word; the role is the server's. A question is filed as
        # ROLE_CHAT so nothing downstream reviews it, hands it off, or counts it as owed.
        role = ROLE_CHAT if request.kind == "chat" else ROLE_WORKER
        run = await run_in_threadpool(dispatcher.queue, request, role=role)
        dispatcher.schedule(run.run_id)
        return run
    except (TaskNotFoundError, RunNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except RunGateError as exc:
        # Still a 409, but with a machine-readable body: "wait for the run that is
        # already going" and "settle the gate that is waiting for you" need
        # different words and a different action, and a client cannot tell them
        # apart from prose. Must precede the RunConflictError arm — it is a
        # subclass, so the broad arm would swallow it and drop the code.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": exc.code, "message": str(exc), "items": exc.items},
        ) from None
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except RunValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    task_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    state_filter: RunState | None = Query(default=None, alias="state"),
    # Several states in one request, e.g. ?states=queued&states=running. Additive:
    # `state` keeps working unchanged. It exists because "is any run of this task
    # unsettled?" asked as two requests is not one snapshot — a run moving
    # queued -> running between them is in neither answer.
    states_filter: list[RunState] | None = Query(default=None, alias="states"),
    adapter: str | None = Query(default=None, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ExecutionStore = Depends(get_execution_store),
):
    try:
        return store.list(
            task_id=task_id, project_id=project_id, state=state_filter, states=states_filter,
            adapter=adapter, limit=limit, offset=offset,
        )
    except RunValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/runs/{run_id}", response_model=ExecutionRun)
def get_run(
    run_id: Annotated[str, PathParam(max_length=128)],
    store: ExecutionStore = Depends(get_execution_store),
):
    try:
        return store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None


class RunOutputSlice(BaseModel):
    """A window into a run's transcript, read from ``offset`` bytes in.

    ``reachable`` is the honesty flag: false means this process could not read
    the transcript, which a client must render as "读不到", never as a run that
    has said nothing. ``complete`` says the run has settled, so a client knows
    that reaching the end of the text is the end of the conversation turn rather
    than a pause.
    """

    run_id: str
    text: str
    offset: int
    next_offset: int
    reachable: bool
    unreachable_reason: str | None = None
    more_available: bool = False
    #: Output the agent produced that could not be saved to the transcript.
    #: The text reads fine and is incomplete, which is why it needs its own
    #: field rather than being folded into `reachable`.
    lossy: bool = False
    complete: bool = False
    state: RunState
    #: True when this text was rebuilt from the run's own stored record
    #: because no transcript file exists — every run from before transcripts
    #: existed, and any that failed before its first line was written. The
    #: window says so rather than presenting it as a live transcript.
    from_record: bool = False


@router.get("/runs/{run_id}/output", response_model=RunOutputSlice)
def run_output(
    run_id: Annotated[str, PathParam(max_length=128)],
    offset: int = Query(default=0, ge=0),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """The run's transcript so far — the conversation window's live feed.

    Polled with the ``next_offset`` from the previous read, which is the same
    contract a remote environment serves with ``tail -c +<offset>``. That is why
    this is a plain GET and not a socket: when runs move to a remote host (W3-a),
    the transport changes and this endpoint's shape does not.
    """
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    # Asked **before** the log is read, and it has to stay that way. A drain that
    # finishes in between appends its tail and then leaves `_active`; asking
    # afterwards returned "not draining" for a read taken before that tail
    # existed, so the window was told the turn was complete and stopped polling
    # with the last of the output still on disk. Reading it first makes the race
    # cost one extra poll instead of a permanently truncated turn.
    draining_before_read = dispatcher.is_draining(run_id)
    try:
        chunk = dispatcher.run_logs.read(run_id, offset)
    except RunLogError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run id is not readable") from None
    # Settled *and* nothing was still reading the process. A cancel marks the row
    # terminal first and drains stdout afterwards; saying "complete" inside that
    # window stopped the window polling, and the output the kill produced was
    # written after the last read.
    settled = run.state in TERMINAL_RUN_STATES and not draining_before_read
    text = chunk.text
    from_record = False
    record_truncated = False
    if chunk.reachable and not text and offset == 0 and settled and not _has_transcript(dispatcher, run_id):
        # A settled run with no transcript file at all. Every run from before this
        # slice existed is in that state, and so is one that failed before its
        # prompt was echoed — and "reachable and empty" rendered them as turns
        # that finished having said nothing, hiding the prompt and output their own
        # record still holds. Reconstructed from that record, and flagged, so the
        # window can say where the text came from rather than implying a live
        # transcript.
        text = _transcript_from_record(run)
        from_record = True
        # The record's own stdout is a **bounded tail**: everything before it was
        # dropped when the run settled. Marked lossy, or the window presents the
        # last few kilobytes of a long answer as the whole of it — complete, no
        # notice, and no way for the reader to tell.
        record_truncated = len(run.stdout_tail or "") >= OUTPUT_TAIL_LIMIT
    return RunOutputSlice(
        run_id=run_id,
        # Redacted again here, over the contiguous slice being served. The writer
        # redacts what it appends, but it sees the stream in pieces; a credential
        # split across two rendered events is whole only in a window like this
        # one. Cheap, and the last place before the text leaves the process.
        text=redact_text(text),
        offset=chunk.offset,
        next_offset=chunk.next_offset if not from_record else 0,
        reachable=chunk.reachable,
        unreachable_reason=chunk.unreachable_reason,
        more_available=chunk.more_available,
        lossy=chunk.lossy or record_truncated,
        complete=settled,
        state=run.state,
        from_record=from_record,
    )


def _has_transcript(dispatcher: ExecutionDispatcher, run_id: str) -> bool:
    """Whether a transcript file exists for this run, as far as we can tell.

    An unreadable answer counts as *present*: the caller uses this to decide
    whether to substitute the run's own record, and substituting over a
    transcript that is merely unreadable would replace "could not be read" with
    text that looks complete.
    """
    try:
        probe = probe_path(dispatcher.run_logs.path_for(run_id))
    except RunLogError:
        return False
    return not probe.absent


def _transcript_from_record(run: ExecutionRun) -> str:
    """The best transcript available for a run that never wrote one.

    Its prompt and its settled output, in the same two-part shape the live
    transcript has, so one renderer draws both.
    """
    # Every line marked, exactly as _prompt_echo does it. Marking only the first
    # line put the rest of a multi-line message into the terminal pane, where it
    # read as something the agent had said — the same defect, in the fallback path.
    parts = [f"› {line}" for line in run.prompt.strip().splitlines() if run.prompt.strip()]
    if run.stdout_tail.strip():
        parts.append(run.stdout_tail.rstrip())
    if run.error_message:
        parts.append(f"✖ {run.error_message}")
    # stderr as well, for the reason the live failure note gives: the error
    # message says what Agora concluded, and stderr says what the CLI actually
    # complained about. Omitting it here left an upgraded installation's failed
    # runs showing a generic "non-zero status" with the real cause — an expired
    # login, a rejected flag — nowhere on screen.
    stderr = (run.stderr_tail or "").strip()
    if stderr:
        parts.extend(stderr.splitlines()[-20:])
    return "\n".join(parts) + ("\n" if parts else "")


@router.get("/tasks/{task_id}/sessions", response_model=list[AgentSession])
def list_task_sessions(
    task_id: Annotated[str, PathParam(max_length=128)],
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """Which conversation each adapter is holding for this task.

    An empty list means no adapter has started one. A row whose ``state`` is
    ``unreadable`` means a session exists that this configuration could not
    continue — the window must say so rather than presenting a fresh start.
    """
    if store.tasks.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return dispatcher.sessions.list_for_task(task_id)


@router.delete("/tasks/{task_id}/sessions/{adapter}", response_model=AgentSession)
def forget_task_session(
    task_id: Annotated[str, PathParam(max_length=128)],
    adapter: Annotated[str, PathParam(max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")],
    # Sessions are keyed by role, so a reset has to name one. Without it this
    # endpoint always cleared the worker's session, and a review session stuck in
    # `lost` blocked every later review of that task with no way to recover it.
    role: str = Query(default=ROLE_WORKER, pattern=r"^[a-z][a-z0-9_-]*$", max_length=64),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """Start over: drop the recorded session so the next turn opens a new one.

    The explicit act that adr/0007 requires. Nothing else clears a session id —
    an unreachable session keeps its id precisely so this stays a decision the
    user makes rather than a fallback the dispatcher takes on their behalf.
    """
    if store.tasks.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    # Refused while a run of this adapter is still going. Clearing then appeared
    # to work — the response said "none" — and the live process's next session
    # event wrote the old session straight back through `record_observed`, so the
    # fresh start the person asked for silently did not happen.
    # Asked as its own filtered question, not by scanning a page of recent
    # history. A page fails toward *allowing* the clear — a long-running turn can
    # be pushed off it by newer runs of other adapters — and the comment that
    # used to sit here claimed the opposite.
    live = store.list(
        task_id=task_id, adapter=adapter,
        states=sorted(ACTIVE_RUN_STATES, key=lambda item: item.value),
        limit=1,
    )
    # A cancelled run is terminal in the database while its stdout is still
    # being drained, and the events in that buffer go through `record_observed` —
    # so a clear accepted in that window was written straight back, and the next
    # turn resumed the context the person had just asked to drop.
    # Asked of the dispatcher, whose `_active` set *is* the answer and is bounded
    # by the concurrency limit. The previous version paged through the task's runs
    # and tested each one — capped, and a cap fails toward allowing the clear.
    draining = dispatcher.draining_for(task_id, adapter)
    conflict = (
        "这个适配器还有一轮在跑，现在清除会话不会生效——那个进程报出的下一条会话事件会把它写回来。"
        "先等它结束或停止它，再新开会话。"
    )
    if live or draining:
        raise HTTPException(status.HTTP_409_CONFLICT, conflict)
    try:
        # The same question again, this time **inside** the clearing transaction.
        # The two checks above are reads: a run created between them and the write
        # was already carrying the old session id, so the response said "none"
        # while that turn continued the conversation the person had just dropped.
        # A run row exists before its process starts, so this closes the window.
        dispatcher.sessions.forget(
            task_id=task_id, adapter=adapter, role=role,
            blocking_run_states=[state.value for state in ACTIVE_RUN_STATES],
        )
    except SessionClearRefused:
        raise HTTPException(status.HTTP_409_CONFLICT, conflict) from None
    # The state after the act, not a bare 204: the caller is a window that has to
    # redraw the session badge, and "none" is the answer it needs.
    return dispatcher.sessions.get(task_id, adapter, role)


@router.post(
    "/tasks/{task_id}/sessions/{adapter}/retry",
    response_model=AgentSession,
)
def retry_task_session(
    task_id: Annotated[str, PathParam(max_length=128)],
    adapter: Annotated[str, PathParam(max_length=64)],
    role: str = Query(default=ROLE_WORKER, pattern=r"^[a-z][a-z0-9_-]*$", max_length=64),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """Try the recorded session once more, instead of dropping it.

    The counterpart to clearing. A session recorded as `lost` refuses every turn
    — correctly, because a resume of that id was observed to fail — but the
    reason it shows says to fix the cause and carry on, and until this endpoint
    existed the only button on that state was the one that throws the context
    away.
    """
    if store.tasks.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    dispatcher.sessions.retry(task_id=task_id, adapter=adapter, role=role)
    return dispatcher.sessions.get(task_id, adapter, role)


@router.get("/runs/{run_id}/deliverables")
def run_deliverables(
    run_id: Annotated[str, PathParam(max_length=128)],
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """What files this run delivered (read-only).

    A terminal run is answered from the snapshot frozen when it ended, so its
    card keeps showing that run's files after a handoff commit or a later run in
    the same worktree. A terminal run with no snapshot says so — it never falls
    back to the live worktree, which is what made historical file lists wrong.
    An active run has no snapshot yet, and there the live worktree *is* the
    answer.
    """
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    remote_adapter = _remote_adapter_of(dispatcher, run)
    if remote_adapter is not None:
        return asyncio.run(_remote_run_deliverables(dispatcher, store, run, remote_adapter))
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")

    base = {"run_id": run_id, "workspace": str(workspace)}
    snapshot = store.deliverable_snapshot(run_id)
    live_workspace = probe_path(workspace)
    if snapshot is not None:
        # Serving stored data, so the worktree-shape checks below do not gate this
        # list — they gate filesystem reads, and there is no filesystem read here.
        # The project-scoped readability check above still gates access, and the
        # content/raw endpoints keep their own gate.
        #
        # The worktree can be gone or replaced by now, in which case the file list
        # is still the truth about this run but no file can be opened. Report that
        # instead of leaving every "view" to fail with a 404.
        return {
            **base,
            "files": snapshot.get("files") or [],
            "truncated": bool(snapshot.get("truncated")),
            "source": "snapshot",
            "captured_at": run.finished_at,
            # A capability, not an existence claim: when the worktree cannot even be
            # stat'd, content genuinely cannot be served, so False stays true here.
            "content_available": bool(live_workspace.is_dir()) and _ready_worktree_for_run(registry, run),
        }
    if run.state in TERMINAL_RUN_STATES:
        return {
            **base, "files": [], "source": "none",
            "unavailable": "this run ended without a deliverable snapshot; its files are not recoverable",
        }

    if not live_workspace.is_dir():
        # Absence and "could not be stat'd" are different answers, and the operator's
        # next move differs: reprovision, versus fix a permission. `is_dir()` collapsed
        # them into "workspace not found".
        return {
            **base, "files": [], "source": "live",
            "unavailable": workspace_unavailable_reason(live_workspace),
        }

    if not _ready_worktree_for_run(registry, run):
        return {**base, "files": [], "source": "live", "unavailable": "workspace is not a provisioned worktree"}
    return {**base, "files": list_changes(workspace), "source": "live"}


@router.get("/runs/{run_id}/deliverables/content")
def run_deliverable_content(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """Return a bounded, redacted text preview of one deliverable file."""
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    remote_adapter = _remote_adapter_of(dispatcher, run)
    if remote_adapter is not None:
        return asyncio.run(_remote_content(dispatcher, store, run, remote_adapter, path))
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    _require_ready_worktree(registry, run)
    entry = _delivered_entry(store, run, path)
    if entry is None:  # pre-snapshot run: nothing recorded to verify against
        try:
            return read_file(workspace, path)
        except (FileNotFoundError, PermissionError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
    return preview_of(path, _verified_bytes(workspace, path, entry))


@router.get("/runs/{run_id}/deliverables/raw")
def run_deliverable_raw(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """Serve a deliverable file so the UI can open/render it (e.g. an HTML page).

    Sandboxed via CSP so a rendered deliverable runs in an opaque origin and
    cannot touch the app's own origin, cookies, or storage.
    """
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    remote_adapter = _remote_adapter_of(dispatcher, run)
    if remote_adapter is not None:
        raw = asyncio.run(_remote_raw(dispatcher, store, run, remote_adapter, path))
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return Response(content=raw, media_type=media_type, headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals;",
            "X-Content-Type-Options": "nosniff",
        })
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    _require_ready_worktree(registry, run)
    sandbox_headers = {
        "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals;",
        "X-Content-Type-Options": "nosniff",
    }
    entry = _delivered_entry(store, run, path)
    if entry is None:  # pre-snapshot run: keep streaming from disk
        try:
            target = resolve_confined(workspace, path)
        except (FileNotFoundError, PermissionError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
        return FileResponse(str(target), headers=sandbox_headers)
    # Serve the very bytes that were verified. FileResponse opens the path after
    # this function returns, which would reopen a file a later run may have
    # rewritten in between. Bounded by SNAPSHOT_HASH_LIMIT, since anything larger
    # has no digest and was already refused above.
    raw = _verified_bytes(workspace, path, entry)
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=raw, media_type=media_type, headers=sandbox_headers)


@router.post("/runs/{run_id}/cancel", response_model=ExecutionRun)
async def cancel_run(
    run_id: Annotated[str, PathParam(max_length=128)],
    request: CancelRunRequest,
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    try:
        return await dispatcher.cancel(run_id, request)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/tasks/{task_id}/runs", response_model=list[RunSummary])
def list_task_runs(
    task_id: Annotated[str, PathParam(max_length=128)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ExecutionStore = Depends(get_execution_store),
):
    if store.tasks.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return store.list(task_id=task_id, limit=limit, offset=offset)


@router.post("/tasks/{task_id}/handoff", response_model=HandoffResponse)
async def handoff_task(
    task_id: Annotated[str, PathParam(max_length=128)],
    actor: str = Query(default="user", min_length=1, max_length=128),
    store: ExecutionStore = Depends(get_execution_store),
    task_store: TaskStore = Depends(get_task_store),
    registry: ProjectRegistry = Depends(get_project_registry),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """Commit a verified task's deliverable, bind the commit, and mark it done."""
    try:
        result = await run_in_threadpool(
            hand_off, task_store, store, registry, task_id, actor=actor,
            adapters=dispatcher.adapters,
        )
    except HandoffError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except (InvalidTransitionError, StaleTaskVersionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return HandoffResponse(
        task=result.task, run_id=result.run_id, workspace=result.workspace,
        commit=result.commit, committed=result.committed, files=result.files,
        note=result.note,
    )


# --- deliverables of a run that executed on another host (W3-a U5) ----------------------
#
# Read there, on a fresh remote environment per request (these endpoints are synchronous
# and run in a worker thread; the remote collaborators hold one connection on one loop).
# The local confinement checks (`is_workspace_readable`, `_require_ready_worktree`) read
# this machine's filesystem and say nothing about a remote path; their remote
# counterparts are asked on the host.

def _remote_adapter_of(dispatcher: ExecutionDispatcher, run: ExecutionRun):
    """The adapter, pinned to the place this run's own record says it executed — or
    None for a run that executed here. An adapter that no longer reaches that place
    is a 409, never a read from wherever it points today."""
    target = remote_target_of(run)
    if target is None:
        return None
    try:
        return adapter_for_recorded_target(dispatcher.adapters.get(run.adapter), target)
    except RemoteTargetMismatch as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


async def _remote_ready(components, run: ExecutionRun) -> bool:
    try:
        return await components.provisioner.is_ready_worktree(
            Path(run.workspace), Path(components.config.remote_project_root),
            worktree_branch(run.project_id, run.adapter),
        )
    except Exception:  # noqa: BLE001 - "could not check" reads as not available, never as ready
        return False


async def _remote_run_deliverables(dispatcher: ExecutionDispatcher, store: ExecutionStore, run: ExecutionRun, adapter) -> dict:
    base = {"run_id": run.run_id, "workspace": run.workspace}
    snapshot = store.deliverable_snapshot(run.run_id)
    if snapshot is None and run.state in TERMINAL_RUN_STATES:
        return {
            **base, "files": [], "source": "none",
            "unavailable": "this run ended without a deliverable snapshot; its files are not recoverable",
        }
    components = remote_components_for(adapter, dispatcher.projects)
    try:
        ready = await _remote_ready(components, run)
        if snapshot is not None:
            return {
                **base,
                "files": snapshot.get("files") or [],
                "truncated": bool(snapshot.get("truncated")),
                "source": "snapshot",
                "captured_at": run.finished_at,
                "content_available": ready,
            }
        if not ready:
            return {**base, "files": [], "source": "live", "unavailable": "remote workspace is not a provisioned worktree"}
        return {**base, "files": await components.deliverables.list_changes(Path(run.workspace)), "source": "live"}
    finally:
        await components.aclose()


async def _remote_content(dispatcher: ExecutionDispatcher, store: ExecutionStore, run: ExecutionRun, adapter, path: str) -> dict:
    return preview_of(path, await _remote_raw(dispatcher, store, run, adapter, path))


async def _remote_raw(dispatcher: ExecutionDispatcher, store: ExecutionStore, run: ExecutionRun, adapter, path: str) -> bytes:
    """The bytes of one delivered file on the host, proven to be the run's.

    A settled run's entry carries the content digest frozen at its snapshot; the file is
    read once and served only if it still hashes to that — a later run in the same
    worktree that rewrote the path gets a 409, never its bytes under the old run's card.
    An entry with no digest (a deletion, "no longer differs", a directory) has nothing
    to prove against and is not served. Only an *active* run, which has no snapshot
    yet, is read live.
    """
    entry = _delivered_entry(store, run, path)
    if entry is None and run.state in TERMINAL_RUN_STATES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")
    expected = entry.get("sha256") if entry is not None else None
    if entry is not None and not expected:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This entry recorded no content to serve (deleted, or not a file the run left behind)",
        )
    components = remote_components_for(adapter, dispatcher.projects)
    try:
        if not await _remote_ready(components, run):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")
        fs = components.filesystem
        try:
            base = PurePosixPath(Path(str(await fs.realpath(Path(run.workspace)))).as_posix())
            target = PurePosixPath(Path(str(await fs.realpath(Path(run.workspace) / path))).as_posix())
            if base != target and base not in target.parents:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")
            raw = await fs.read_file(Path(str(target)), max_bytes=_deliverables_module.MAX_FILE_BYTES)
        except (FileNotFoundError, PermissionError, OSError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
        if expected is not None and hashlib.sha256(raw).hexdigest() != str(expected):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This file changed after the run ended, so it no longer holds what the run delivered",
            )
        return raw
    finally:
        await components.aclose()
