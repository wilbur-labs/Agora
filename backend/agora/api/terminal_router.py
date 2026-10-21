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

from agora.api.settings_router import get_project_registry
from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.projects.registry import ProjectRegistry
from agora.terminal.auth import TOKEN_HEADER, LaunchCodes, load_or_create_token, token_matches
from agora.terminal.manager import PtyUnavailable, TerminalLimitReached, TerminalManager, command_argv
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
def terminal_targets(project_id: str, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    project = _project(registry, project_id)
    tasks = task_targets([Path(p) for p in registry.workspace_sources()], Path(project.root))
    return {"project_id": project_id, "root": str(project.root), "tasks": [t.payload() for t in tasks]}


class TaskRef(BaseModel):
    source: str = Field(min_length=1, max_length=4096)
    task: str = Field(min_length=1, max_length=256)


class CreateTerminalRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    command: Literal["shell", "claude", "codex", "kiro"] = "shell"
    #: Open as this workspace task (claude: ``cw open``; kiro: its agent) instead of in
    #: the repository.
    task: TaskRef | None = None
    rows: int = Field(default=30, ge=2, le=500)
    cols: int = Field(default=120, ge=10, le=1000)


@router.get("/terminals", dependencies=[Depends(require_terminal_session)])
def list_terminals() -> dict[str, Any]:
    return {"terminals": [s.view() for s in get_terminal_manager().list()]}


@router.post("/terminals", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_terminal_session)])
async def create_terminal(request: CreateTerminalRequest, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    project = _project(registry, request.project_id)
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
                await socket.send_text(json.dumps({"type": "exit", "code": payload}))
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
