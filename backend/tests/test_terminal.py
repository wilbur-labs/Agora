"""The embedded terminal (CR-30): who may open one, where it opens, and that it works."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api import terminal_router
from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry
from agora.terminal.auth import load_or_create_token, token_path
from agora.terminal.manager import command_argv
from agora.terminal.targets import claude_tool_dir_for, task_targets


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _task(source: Path, name: str, text: str) -> None:
    d = source / "tasks" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.yaml").write_text(text, encoding="utf-8")


def _tool(path: Path, workroot: Path) -> Path:
    (path / "scripts").mkdir(parents=True)
    (path / "scripts" / "cw.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / ".claude").mkdir()
    (path / ".workroot").write_text(f"{workroot.as_posix()}\n", encoding="utf-8")
    return path


@pytest.fixture
def api(tmp_path, monkeypatch):
    registry = ProjectRegistry({
        "projects": {
            "registry_path": str(tmp_path / "cfg" / "projects.yaml"),
            "default": "home",
            "projects": {"home": {"name": "Home", "root": str(tmp_path / "home")}},
        }
    })
    monkeypatch.setattr(terminal_router, "_data_dir", lambda: tmp_path / "data")
    terminal_router.get_terminal_manager.cache_clear()
    app.dependency_overrides[get_project_registry] = lambda: registry
    try:
        yield TestClient(app, base_url="http://127.0.0.1:8000"), registry
        # headers are set on the client by _login
    finally:
        app.dependency_overrides.clear()
        terminal_router.get_terminal_manager().close_all()
        terminal_router.get_terminal_manager.cache_clear()


def _code(client, tmp_path) -> str:
    token = load_or_create_token(tmp_path / "data")
    resp = client.post("/api/terminal-launch-code", headers={"X-Agora-Terminal-Token": token})
    assert resp.status_code == 200, resp.text
    return resp.json()["code"]


def _login(client, tmp_path) -> str:
    """The page's part: redeem the launcher's one-time code, then send the token."""
    resp = client.post("/api/terminal-session", json={"code": _code(client, tmp_path)})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    client.headers["X-Agora-Terminal-Token"] = token
    return token


# --- who ---------------------------------------------------------------------------------

def test_terminals_need_the_install_token(api, tmp_path):
    client, _ = api
    assert client.get("/api/terminals").status_code == 401
    assert client.post("/api/terminals", json={"project_id": "home"}).status_code == 401
    assert client.get("/api/terminals/session").json() == {"authorized": False}
    assert client.post("/api/terminal-session", json={"code": "guess"}).status_code == 403
    assert client.post("/api/terminal-launch-code", headers={"X-Agora-Terminal-Token": "guess"}).status_code == 403
    _login(client, tmp_path)
    assert client.get("/api/terminals/session").json() == {"authorized": True}
    assert client.get("/api/terminals").json() == {"terminals": []}


def test_a_launch_code_works_once(api, tmp_path):
    client, _ = api
    code = _code(client, tmp_path)
    assert client.post("/api/terminal-session", json={"code": code}).status_code == 200
    assert client.post("/api/terminal-session", json={"code": code}).status_code == 403


def test_no_cookie_carries_the_credential(api, tmp_path):
    """review: cookies are not port-scoped; the token must never be one."""
    client, _ = api
    _login(client, tmp_path)
    assert not client.cookies


def test_the_token_file_is_created_once_and_kept(tmp_path):
    first = load_or_create_token(tmp_path)
    assert load_or_create_token(tmp_path) == first and len(first) >= 32
    if os.name == "nt":
        acl = subprocess.run(["icacls", str(token_path(tmp_path))], capture_output=True, text=True).stdout
        grants = [line for line in acl.splitlines() if ":(" in line]
        assert len(grants) == 1 and os.environ["USERNAME"].lower() in grants[0].lower(), acl  # the owner only


# --- where -------------------------------------------------------------------------------

def test_the_claude_workspace_tool_is_found_beside_its_work_root(tmp_path):
    work = tmp_path / "Work"
    (work / "tasks").mkdir(parents=True)
    kiro = tmp_path / "AITools" / "kiro-workspace"
    kiro.mkdir(parents=True)
    tool = _tool(tmp_path / "AITools" / "claude-workspace", work)
    assert claude_tool_dir_for(work, [work, kiro]) == tool
    assert claude_tool_dir_for(work, [work]) is None  # nothing beside it says so


def test_task_targets_open_as_the_task_from_the_tool(tmp_path):
    work = tmp_path / "Work"
    repo = _git_repo(tmp_path / "repo")
    _task(work, "work-chat", f"task:\n  title: WC\n  goal: g\nprimary_repo: {repo.as_posix()}\n")
    tool = _tool(tmp_path / "claude-workspace", work)
    (target,) = task_targets([work], repo)
    assert target.task == "work-chat" and Path(target.tool_dir) == tool


def test_only_the_four_commands(api):
    with pytest.raises(ValueError):
        command_argv("rm -rf /")
    for command in ("shell", "claude", "codex", "kiro"):
        assert command_argv(command)


def test_a_task_that_does_not_name_the_project_is_refused(api, tmp_path):
    client, registry = api
    _login(client, tmp_path)
    resp = client.post("/api/terminals", json={"project_id": "home", "task": {"source": str(tmp_path), "task": "x"}})
    assert resp.status_code == 404


# --- that it works -----------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="ConPTY round trip; ptyprocess is covered by its own suite")
def test_a_shell_round_trip_through_the_websocket(api, tmp_path):
    client, _ = api
    (tmp_path / "home").mkdir(exist_ok=True)
    _login(client, tmp_path)
    created = client.post("/api/terminals", json={"project_id": "home", "command": "shell"})
    assert created.status_code == 201, created.text
    terminal_id = created.json()["terminal_id"]
    token = client.headers["X-Agora-Terminal-Token"]
    with client.websocket_connect(f"ws://127.0.0.1:8000/api/terminals/{terminal_id}/stream") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        ws.send_text(json.dumps({"type": "resize", "rows": 30, "cols": 100}))
        ws.send_text(json.dumps({"type": "input", "data": "Write-Output ('agora' + '-terminal-' + '中文')\r\n"}))
        seen = ""
        deadline = time.time() + 20
        while "agora-terminal-中文" not in seen and time.time() < deadline:
            message = json.loads(ws.receive_text())
            if message["type"] == "output":
                seen += message["data"]
        assert "agora-terminal-中文" in seen
        ws.send_text(json.dumps({"type": "input", "data": "exit\r\n"}))
        while True:
            message = json.loads(ws.receive_text())
            if message["type"] == "exit":
                break
    (row,) = client.get("/api/terminals").json()["terminals"]
    assert row["alive"] is False


def test_the_stream_refuses_without_the_token(api, tmp_path):
    client, _ = api
    from starlette.websockets import WebSocketDisconnect

    (tmp_path / "home").mkdir(exist_ok=True)
    _login(client, tmp_path)
    terminal_id = client.post("/api/terminals", json={"project_id": "home", "command": "shell"}).json()["terminal_id"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"ws://127.0.0.1:8000/api/terminals/{terminal_id}/stream") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "guess"}))
            ws.receive_text()


def test_uvicorn_can_serve_the_stream():
    """uvicorn refuses WebSocket upgrades without a WebSocket library, and TestClient never
    shows it (found end to end against a real server)."""
    import importlib.util

    assert importlib.util.find_spec("websockets") is not None


@pytest.mark.skipif(os.name != "nt", reason="ConPTY")
def test_closing_a_terminal_records_that_it_ended(api, tmp_path):
    client, _ = api
    (tmp_path / "home").mkdir(exist_ok=True)
    _login(client, tmp_path)
    terminal_id = client.post("/api/terminals", json={"project_id": "home", "command": "shell"}).json()["terminal_id"]
    assert client.delete(f"/api/terminals/{terminal_id}").status_code == 200
    (row,) = client.get("/api/terminals").json()["terminals"]
    assert row["alive"] is False


def _kiro_workspace(path: Path) -> Path:
    (path / ".kiro" / "agents").mkdir(parents=True)
    return path


def test_a_kiro_task_opens_on_its_agent_in_the_kiro_workspace(tmp_path):
    kiro = _kiro_workspace(tmp_path / "kiro-workspace")
    repo = _git_repo(tmp_path / "repo")
    _task(kiro, "wekan-server", f"name: Wekan\nproject_path: {repo.as_posix()}\n")
    (kiro / ".kiro" / "agents" / "wekan-server.json").write_text(json.dumps({"name": "wekan-server"}), encoding="utf-8")

    (target,) = task_targets([kiro], repo)

    assert target.workspace == "kiro" and target.agent == "wekan-server"
    assert Path(target.tool_dir) == kiro and target.reason is None
    argv = command_argv("kiro", kiro_agent=target.agent)
    assert "kiro-cli chat --agent wekan-server" in " ".join(argv)


def test_a_kiro_task_without_an_agent_is_listed_with_why(tmp_path):
    kiro = _kiro_workspace(tmp_path / "kiro-workspace")
    repo = _git_repo(tmp_path / "repo")
    _task(kiro, "no-agent", f"name: X\nproject_path: {repo.as_posix()}\n")

    (target,) = task_targets([kiro], repo)

    assert target.tool_dir is None and target.agent is None
    assert "no-agent.json" in (target.reason or "")


def test_an_agent_name_that_could_reach_the_shell_is_refused(tmp_path):
    kiro = _kiro_workspace(tmp_path / "kiro-workspace")
    repo = _git_repo(tmp_path / "repo")
    _task(kiro, "evil", f"name: X\nproject_path: {repo.as_posix()}\n")
    (kiro / ".kiro" / "agents" / "evil.json").write_text(json.dumps({"name": "x; rm -rf ~"}), encoding="utf-8")

    (target,) = task_targets([kiro], repo)

    assert target.tool_dir is None and "command line" in (target.reason or "")
    with pytest.raises(ValueError):
        command_argv("kiro", kiro_agent="x; rm -rf ~")
    with pytest.raises(ValueError):
        command_argv("claude", kiro_agent="wekan-server")


# --- remote (CR-30 slice 2) --------------------------------------------------------------

import asyncio as _asyncio
from types import SimpleNamespace

from agora.terminal.manager import RemoteTerminalSession, TerminalManager


class _FakeStdout:
    def __init__(self) -> None:
        self.chunks: _asyncio.Queue = _asyncio.Queue()

    async def read(self, n: int) -> str:
        return await self.chunks.get()


class _FakeProc:
    """The parts of an asyncssh SSHClientProcess a remote terminal uses."""

    def __init__(self) -> None:
        self.stdout = _FakeStdout()
        self.stdin = SimpleNamespace(written=[], write=lambda data: self.stdin.written.append(data))
        self.sizes: list[tuple[int, int]] = []
        self.exit_status: int | None = None
        self.closed = False

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    def close(self) -> None:
        self.closed = True
        self.stdout.chunks.put_nowait("")

    async def wait_closed(self) -> None:
        return None


class _FakeEnvironment:
    def __init__(self, proc=None, error: Exception | None = None) -> None:
        self.proc, self.error, self.closed, self.opened = proc, error, False, None

    async def open_terminal(self, line, cwd, *, rows, cols):
        self.opened = (line, cwd, rows, cols)
        if self.error:
            raise self.error
        return self.proc

    async def aclose(self) -> None:
        self.closed = True


async def _settle(session) -> None:
    for _ in range(50):
        if session.exited_at is not None:
            return
        await _asyncio.sleep(0.01)


def _open(manager, environment):
    return manager.create_remote(project_id="p", label="p · shell · u@h", environment=environment,
                                 target="u@h", cwd="/srv/p", line="exec sh", rows=24, cols=80)


def test_a_remote_terminal_carries_output_input_and_size_and_ends_as_exited():
    async def scenario():
        manager, proc = TerminalManager(), _FakeProc()
        environment = _FakeEnvironment(proc)
        session = await _open(manager, environment)
        assert isinstance(session, RemoteTerminalSession) and session.remote == "u@h"
        assert environment.opened == ("exec sh", "/srv/p", 24, 80)
        proc.stdout.chunks.put_nowait("hello 中文\r\n")
        await _asyncio.sleep(0.02)
        session.write("ls\r")
        session.resize(30, 100)
        await _asyncio.sleep(0.02)
        assert proc.stdin.written == ["ls\r"] and proc.sizes == [(100, 30)]
        proc.exit_status = 0
        proc.stdout.chunks.put_nowait("")
        await _settle(session)
        return session, environment

    session, environment = _asyncio.run(scenario())
    assert "hello 中文" in "".join(text for _, text in session._chunks)
    view = session.view()
    assert view["end_reason"] == "exited" and view["exit_code"] == 0 and view["remote"] == "u@h"
    assert environment.closed


def test_a_channel_that_ends_without_an_exit_status_is_a_dropped_connection_not_an_exit():
    async def scenario():
        manager, proc = TerminalManager(), _FakeProc()
        session = await _open(manager, _FakeEnvironment(proc))
        proc.stdout.chunks.put_nowait("")  # EOF, no exit status: the connection went
        await _settle(session)
        return session

    session = _asyncio.run(scenario())
    assert session.end_reason == "disconnected" and session.exit_code is None


def test_closing_a_remote_terminal_is_recorded_as_closed():
    async def scenario():
        manager, proc = TerminalManager(), _FakeProc()
        session = await _open(manager, _FakeEnvironment(proc))
        assert manager.close(session.terminal_id) is True  # posts, does not wait
        await _settle(session)
        return session, proc

    session, proc = _asyncio.run(scenario())
    assert proc.closed and session.end_reason == "closed" and session.exit_code is None


def test_a_remote_terminal_that_cannot_open_leaves_nothing_behind():
    async def scenario():
        manager = TerminalManager()
        environment = _FakeEnvironment(error=OSError("unreachable"))
        with pytest.raises(OSError):
            await _open(manager, environment)
        return manager, environment

    manager, environment = _asyncio.run(scenario())
    assert environment.closed and manager.list() == [] and manager._starting == 0


def test_the_remote_command_enters_the_project_directory_first_and_stops_if_it_cannot():
    from agora.environments_remote import RemoteEnvironment

    environment = RemoteEnvironment(host="h", user="u", secret_ref="", credential_source="ssh_default")
    seen = {}

    class _Conn:
        async def create_process(self, command, **kwargs):
            seen["command"], seen["kwargs"] = command, kwargs
            return "proc"

    async def scenario():
        async def _conn():
            return _Conn()

        async def _login(conn):
            return "/usr/bin:/bin"

        environment._ensure_conn = _conn  # type: ignore[method-assign]
        environment._login_path_for = _login  # type: ignore[method-assign]
        return await environment.open_terminal("claude; exec sh", "/srv/my 项目", rows=24, cols=80)

    assert _asyncio.run(scenario()) == "proc"
    command = seen["command"]
    assert command.startswith('"${SHELL:-/bin/sh}" -lc ')
    import shlex as _shlex
    script = _shlex.split(command)[2]
    assert script.startswith("cd '/srv/my 项目' || { echo ")
    assert "exit 1; }; export PATH=" in script and script.endswith("; claude; exec sh")
    assert seen["kwargs"]["term_type"] == "xterm-256color" and seen["kwargs"]["term_size"] == (80, 24)


def test_a_remote_terminal_only_opens_on_an_environment_of_this_project(api, tmp_path):
    from agora.api.settings_router import get_remote_environment_store

    client, _ = api
    _login(client, tmp_path)
    other = SimpleNamespace(project_id="someone-else", environment_id="e1", label="E1", ssh_user="u",
                            ssh_host="h", ssh_port=22, remote_project_root="/srv/x", workspace_tool_dir=None)
    store = SimpleNamespace(get=lambda environment_id: other, list_environments=lambda: [other])
    app.dependency_overrides[get_remote_environment_store] = lambda: store
    resp = client.post("/api/terminals", json={"project_id": "home", "command": "shell", "environment_id": "e1"})
    assert resp.status_code == 404
    targets = client.get("/api/projects/home/terminal-targets").json()
    assert targets["environments"] == [] and targets["environments_error"] is None
    mine = SimpleNamespace(**{**vars(other), "project_id": "home", "ssh_port": 2222})
    app.dependency_overrides[get_remote_environment_store] = lambda: SimpleNamespace(
        get=lambda environment_id: mine, list_environments=lambda: [mine])
    (listed,) = client.get("/api/projects/home/terminal-targets").json()["environments"]
    assert listed == {"environment_id": "e1", "label": "E1", "target": "u@h:2222", "root": "/srv/x", "workspace_tool_dir": None}


def test_a_remote_host_that_stalls_on_the_channel_gives_its_slot_back(monkeypatch):
    from agora.terminal import manager as manager_module

    monkeypatch.setattr(manager_module, "REMOTE_OPEN_TIMEOUT", 0.05)

    class _Stalls(_FakeEnvironment):
        async def open_terminal(self, line, cwd, *, rows, cols):
            await _asyncio.sleep(10)

    async def scenario():
        manager, environment = TerminalManager(), _Stalls()
        with pytest.raises(_asyncio.TimeoutError):
            await _open(manager, environment)
        return manager, environment

    manager, environment = _asyncio.run(scenario())
    assert environment.closed and manager._starting == 0 and manager.list() == []


@pytest.mark.skipif(os.name != "nt", reason="ConPTY; the close path is the same on POSIX")
def test_closing_a_local_terminal_is_recorded_as_closed_not_as_an_exit(tmp_path):
    import sys as _sys

    manager = TerminalManager()
    session = manager.create(project_id="p", label="sleep", cwd=tmp_path,
                             argv=[_sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(1)
    assert manager.close(session.terminal_id)
    deadline = time.time() + 10
    while session.exited_at is None and time.time() < deadline:
        time.sleep(0.1)
    assert session.end_reason == "closed"


def test_a_remote_close_that_does_not_finish_is_forced_within_the_grace(monkeypatch):
    from agora.terminal import manager as manager_module

    monkeypatch.setattr(manager_module, "REMOTE_CLOSE_GRACE", 0.05)

    class _Stuck(_FakeProc):
        def close(self) -> None:
            self.closed = True  # a graceful close still flushing: no EOF comes

    aborted = []

    async def scenario():
        manager, proc = TerminalManager(), _Stuck()
        environment = _FakeEnvironment(proc)
        environment._conn = SimpleNamespace(abort=lambda: aborted.append(True))
        session = await _open(manager, environment)
        manager.close(session.terminal_id)
        await _settle(session)
        return session, environment

    session, environment = _asyncio.run(scenario())
    assert aborted and session.end_reason == "closed" and environment.closed


def test_listing_remote_tasks_needs_the_terminal_token(api, tmp_path):
    """It connects with the person's SSH credentials, so it is a terminal route (kiro, CR-30 slice 3 design)."""
    client, _ = api
    assert client.get("/api/projects/home/remote-task-targets", params={"environment_id": "e1"}).status_code == 401


def test_a_full_terminal_list_still_closes_the_connection_a_task_check_opened():
    from agora.terminal import manager as manager_module

    async def scenario():
        manager = TerminalManager()
        manager._starting = manager_module.MAX_TERMINALS
        environment = _FakeEnvironment(_FakeProc())
        with pytest.raises(manager_module.TerminalLimitReached):
            await _open(manager, environment)
        return environment

    assert _asyncio.run(scenario()).closed


def test_a_remote_task_without_an_environment_is_refused(api, tmp_path):
    client, _ = api
    _login(client, tmp_path)
    resp = client.post("/api/terminals", json={"project_id": "home", "remote_task": "wanted"})
    assert resp.status_code == 422


def test_a_close_that_stalls_is_aborted_within_the_grace(monkeypatch):
    from agora.terminal import manager as manager_module

    monkeypatch.setattr(manager_module, "REMOTE_CLOSE_GRACE", 0.05)
    aborted = []

    class _Stalls:
        _conn = SimpleNamespace(abort=lambda: aborted.append(True))

        async def aclose(self):
            await _asyncio.sleep(10)

    _asyncio.run(manager_module.quietly_close(_Stalls()))
    assert aborted


def test_testing_a_connection_and_trusting_a_key_need_the_terminal_token(api):
    client, _ = api
    assert client.post("/api/remote-environments/e1/test").status_code == 401
    assert client.post("/api/remote-environments/e1/trust-host-key", json={"fingerprint": "SHA256:abcdefghij"}).status_code == 401
