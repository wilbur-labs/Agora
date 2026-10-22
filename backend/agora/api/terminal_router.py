"""The embedded terminal's HTTP and WebSocket routes (CR-30).

Every route that creates, lists, drives or closes a terminal requires the install's
terminal token (``agora/terminal/auth.py``) in the ``X-Agora-Terminal-Token`` header, or —
for the stream — in the WebSocket's first message. Web pages are already kept out by the
local-origin guard (CR-34); the token keeps out other accounts on the same machine.
"""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from agora.api.settings_router import get_project_registry, get_remote_environment_store
from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.execution.environment_registry_store import RemoteEnvironmentStore
from agora.execution.security import redact_text
from agora.projects.registry import ProjectRegistry
from agora.terminal.auth import TOKEN_HEADER, LaunchCodes, load_or_create_token, token_matches
from agora.terminal.manager import PtyUnavailable, TerminalLimitReached, TerminalManager, command_argv, quietly_close
from agora.terminal.targets import task_targets

router = APIRouter()


@lru_cache(maxsize=1)
def get_terminal_manager() -> TerminalManager:
    return TerminalManager()


@lru_cache(maxsize=1)
def get_launch_codes() -> LaunchCodes:
    return LaunchCodes()


def _data_dir() -> Path:
    return resolve_control_plane_db_path(get_config()).parent


def _token() -> str:
    return load_or_create_token(_data_dir())


def _authorized(cookie: str | None) -> bool:
    return token_matches(_token(), cookie)


def require_terminal_session(request: Request) -> None:
    if not _authorized(request.headers.get(TOKEN_HEADER)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, {
            "code": "terminal_auth_required",
            "message": "Open Agora from its launcher or desktop shortcut to use terminals on this machine.",
        })


@router.post("/terminal-launch-code", include_in_schema=False)
def terminal_launch_code(request: Request) -> dict[str, str]:
    """The launcher, which can read the token file, sends the token in a header and gets
    a one-time code to put in the browser's URL instead of the token itself."""
    if not token_matches(_token(), request.headers.get(TOKEN_HEADER)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That terminal token is not this install's.")
    return {"code": get_launch_codes().issue()}


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200)


@router.post("/terminal-session", include_in_schema=False)
def terminal_redeem(request: RedeemRequest) -> dict[str, str]:
    """The page redeems the launcher's one-time code for the token, which it keeps in
    origin-scoped storage. A used, expired or made-up code gets nothing."""
    if not get_launch_codes().redeem(request.code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That launch code is not valid (it works once, for 60 seconds).")
    return {"token": _token()}


@router.get("/terminals/session")
def terminal_session(request: Request) -> dict[str, bool]:
    return {"authorized": _authorized(request.headers.get(TOKEN_HEADER))}


@router.get("/projects/{project_id}/terminal-targets")
def terminal_targets(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
    environments: RemoteEnvironmentStore = Depends(get_remote_environment_store),
) -> dict[str, Any]:
    project = _project(registry, project_id)
    tasks = task_targets([Path(p) for p in registry.workspace_sources()], Path(project.root))
    try:
        remote = [_environment_view(e) for e in environments.list_environments() if e.project_id == project_id]
        remote_error = None
    except Exception as exc:  # noqa: BLE001 - "could not read" is said, never "none registered"
        remote, remote_error = [], type(exc).__name__
    return {"project_id": project_id, "root": str(project.root), "tasks": [t.payload() for t in tasks],
            "environments": remote, "environments_error": remote_error}


def _environment_view(record) -> dict[str, Any]:
    return {"environment_id": record.environment_id, "label": record.label,
            "target": _target_of(record), "root": record.remote_project_root,
            # Whether "open as task" can be asked for at all (CR-30 slice 3). The tasks
            # themselves come from remote-task-targets, which needs the terminal token.
            "workspace_tool_dir": record.workspace_tool_dir}


def _environment_for(record):
    """A fresh RemoteEnvironment for one use: it is one connection on one loop."""
    from agora.environments_remote import RemoteEnvironment

    return RemoteEnvironment(
        host=record.ssh_host, port=record.ssh_port, user=record.ssh_user, secret_ref=record.secret_ref,
        host_key_mode=record.host_key_mode, known_hosts_path=record.known_hosts_path,
        credential_source=record.credential_source, environment_id=record.environment_id,
    )


def _bound_record(store: RemoteEnvironmentStore, environment_id: str, project_id: str):
    record = store.get(environment_id)
    # Only an environment bound to this project — the same rule a remote run obeys.
    if record is None or record.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such remote environment for this project.")
    return record


def _local_repo_facts(registry: ProjectRegistry) -> list[dict[str, Any]]:
    """git facts of this machine's claude-workspace work roots, for the person to compare
    with the remote one. Facts only: no verdict on whether the task records agree."""
    import subprocess

    from agora.terminal.targets import _is_kiro

    out: list[dict[str, Any]] = []
    for source in registry.workspace_sources():
        path = Path(source)
        if _is_kiro(path):
            continue

        def git(*args: str) -> str | None:
            try:
                done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                return None
            return done.stdout if done.returncode == 0 else None

        head, status = git("rev-parse", "--short", "HEAD"), git("status", "--porcelain")
        out.append({"source": str(path), "head": head.strip() if head else None,
                    "dirty": None if status is None else bool(status.strip())})
    return out


@router.get("/projects/{project_id}/remote-task-targets", dependencies=[Depends(require_terminal_session)])
async def remote_task_targets(
    project_id: str,
    environment_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
    environments: RemoteEnvironmentStore = Depends(get_remote_environment_store),
) -> dict[str, Any]:
    """The claude-workspace tasks on this environment's host that name its project root
    (CR-30 slice 3). It connects with the person's SSH credentials, so it takes the
    terminal token like every other terminal route."""
    from agora.terminal.remote_tasks import scan_remote_tasks

    _project(registry, project_id)
    record = _bound_record(environments, environment_id, project_id)
    environment = _environment_for(record)
    try:
        scan = await scan_remote_tasks(environment, record.workspace_tool_dir, record.remote_project_root)
    finally:
        await quietly_close(environment)
    local = await asyncio.to_thread(_local_repo_facts, registry)
    return {"environment_id": environment_id, **scan.payload(), "local_repos": local}


def _target_of(record) -> str:
    port = "" if record.ssh_port == 22 else f":{record.ssh_port}"
    return f"{record.ssh_user}@{record.ssh_host}{port}"


#: What a remote terminal runs, by command. Only these: the line goes to the remote shell.
#: Quitting an agent leaves a login shell, as on this machine.
_REMOTE_SHELL = 'exec "${SHELL:-/bin/sh}" -l'
_REMOTE_LINES = {
    "shell": _REMOTE_SHELL,
    "claude": f"claude; {_REMOTE_SHELL}",
    "codex": f"codex; {_REMOTE_SHELL}",
    "kiro": f"kiro-cli chat; {_REMOTE_SHELL}",
}


class TaskRef(BaseModel):
    source: str = Field(min_length=1, max_length=4096)
    task: str = Field(min_length=1, max_length=256)


class CreateTerminalRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    command: Literal["shell", "claude", "codex", "kiro"] = "shell"
    #: Open as this workspace task (claude: ``cw open``; kiro: its agent) instead of in
    #: the repository.
    task: TaskRef | None = None
    #: Open on this registered remote environment (CR-30 slice 2), in its project root.
    environment_id: str | None = Field(default=None, min_length=1, max_length=128)
    #: With ``environment_id``: open as this claude-workspace task on that host (slice 3).
    remote_task: str | None = Field(default=None, min_length=1, max_length=128)
    rows: int = Field(default=30, ge=2, le=500)
    cols: int = Field(default=120, ge=10, le=1000)


@router.get("/terminals", dependencies=[Depends(require_terminal_session)])
def list_terminals() -> dict[str, Any]:
    return {"terminals": [s.view() for s in get_terminal_manager().list()]}


@router.post("/terminals", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_terminal_session)])
async def create_terminal(
    request: CreateTerminalRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
    environments: RemoteEnvironmentStore = Depends(get_remote_environment_store),
) -> dict[str, Any]:
    project = _project(registry, request.project_id)
    if request.remote_task is not None and request.environment_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "remote_task needs environment_id.")
    if request.environment_id is not None:
        if request.task is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A remote terminal opens in the project root, not as a task.")
        return await _create_remote(request, project.project_id, environments)
    root = Path(project.root)
    env: dict[str, str] = {}
    if request.task is None:
        if not root.is_dir():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Project root is not a directory here: {root}")
        cwd, label, argv = root, f"{project.project_id} · {request.command}", command_argv(request.command)
    else:
        # Only a task this project's records actually name, opened from the tool
        # directory found for it — never a path taken from the request.
        found = next((t for t in task_targets([Path(p) for p in registry.workspace_sources()], root)
                      if t.task == request.task.task and Path(t.source) == Path(request.task.source)), None)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such task names this project's repository.")
        if found.tool_dir is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, found.reason or "No tool directory.")
        if found.workspace == "kiro":
            cwd, label = Path(found.tool_dir), f"{found.task} · kiro (kiro-workspace)"
            argv = command_argv("kiro", kiro_agent=found.agent)
        else:
            cwd, label, argv = Path(found.tool_dir), f"{found.task} · claude (claude-workspace)", command_argv("claude")
            env["CW_TASK"] = found.task
    try:
        # Spawning blocks (pywinpty waits on a socket accept): off the event loop (review).
        session = await asyncio.to_thread(
            get_terminal_manager().create,
            project_id=project.project_id, label=label, cwd=cwd, argv=argv, env_extra=env,
            rows=request.rows, cols=request.cols,
        )
    except TerminalLimitReached as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except (PtyUnavailable, OSError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Could not start a terminal: {exc}") from None
    return session.view()


async def _create_remote(request: CreateTerminalRequest, project_id: str, store: RemoteEnvironmentStore) -> dict[str, Any]:
    from agora.environments_remote import RemoteHostKeyError
    from agora.terminal.remote_tasks import SCAN_TIMEOUT, RemoteTaskRefused, open_line, verify_task

    record = _bound_record(store, request.environment_id, project_id)
    target = _target_of(record)
    # One connection per terminal: RemoteEnvironment is one connection on one loop.
    environment = _environment_for(record)
    cwd, line, label = record.remote_project_root, _REMOTE_LINES[request.command], f"{project_id} · {request.command} · {target}"
    try:
        if request.remote_task is not None:
            # The menu was a list of candidates: everything is checked again here.
            try:
                tool, work = await asyncio.wait_for(
                    verify_task(environment, record.workspace_tool_dir, record.remote_project_root, request.remote_task),
                    timeout=SCAN_TIMEOUT,
                )
            except RemoteTaskRefused as exc:
                await quietly_close(environment)
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
            except (asyncio.TimeoutError, TimeoutError):
                await quietly_close(environment)
                raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                                    f"Checking the task on {target} took over {int(SCAN_TIMEOUT)} s.") from None
            except BaseException:
                await quietly_close(environment)
                raise
            cwd, line = str(tool), open_line(work, request.remote_task)
            label = f"{request.remote_task} · claude (claude-workspace) · {target}"
        session = await get_terminal_manager().create_remote(
            project_id=project_id, label=label, environment=environment,
            target=target, cwd=cwd, line=line, rows=request.rows, cols=request.cols,
        )
    except HTTPException:
        raise
    except TerminalLimitReached as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except RemoteHostKeyError as exc:
        # The host key or the credential refused the connection: said as it is, never
        # turned into a prompt inside the terminal.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, redact_text(str(exc))) from None
    except Exception as exc:  # noqa: BLE001 - asyncssh / OSError: the host could not be reached
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"Could not open a terminal on {target}: {type(exc).__name__}: {redact_text(str(exc))}") from None
    return session.view()


@router.delete("/terminals/{terminal_id}", dependencies=[Depends(require_terminal_session)])
def close_terminal(terminal_id: str) -> dict[str, Any]:
    if not get_terminal_manager().close(terminal_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such terminal.")
    return {"terminal_id": terminal_id, "closing": True}


@router.websocket("/terminals/{terminal_id}/stream")
async def terminal_stream(socket: WebSocket, terminal_id: str) -> None:
    # The token comes in the first message, not the URL or a cookie: nothing is sent
    # until it has (CR-30 review).
    await socket.accept()
    try:
        first = json.loads(await asyncio.wait_for(socket.receive_text(), timeout=10))
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await socket.close(code=4401, reason="terminal_auth_required")
        return
    if first.get("type") != "auth" or not _authorized(first.get("token")):
        await socket.close(code=4401, reason="terminal_auth_required")
        return
    session = get_terminal_manager().get(terminal_id)
    if session is None:
        await socket.close(code=4404, reason="no such terminal")
        return
    replay, queue = session.attach()

    async def pump_out() -> None:
        for seq, text in replay:
            await socket.send_text(json.dumps({"type": "output", "seq": seq, "data": text}))
        while True:
            kind, seq, payload = await queue.get()
            if kind == "output":
                await socket.send_text(json.dumps({"type": "output", "seq": seq, "data": payload}))
            elif kind == "exit":
                await socket.send_text(json.dumps({"type": "exit", **payload}))
            elif kind == "overflow":
                await socket.send_text(json.dumps({"type": "overflow"}))
                await socket.close(code=4408, reason="window too slow")
                return

    async def pump_in() -> None:
        while True:
            message = json.loads(await socket.receive_text())
            if message.get("type") == "input" and isinstance(message.get("data"), str):
                # Off the event loop and one at a time: a program not reading its input
                # must stall this window's typing, not the whole server (review).
                await asyncio.to_thread(session.write, message["data"])
            elif message.get("type") == "resize":
                session.resize(int(message.get("rows", session.rows)), int(message.get("cols", session.cols)))

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        session.detach(queue)


def _project(registry: ProjectRegistry, project_id: str):
    try:
        return registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
