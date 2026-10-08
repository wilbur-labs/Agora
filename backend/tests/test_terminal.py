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
