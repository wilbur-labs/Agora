"""The work page (workspace CR-75 slice 1): which projects it lists, opening and closing
one, and getting it back after a restart — and saying only what the reports support."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api import terminal_router, work_router
from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry
from agora.terminal.auth import load_or_create_token
from agora.terminal.manager import TerminalLimitReached, TerminalManager, command_argv, shell_literal
from agora.work import hooks as hooks_module
from agora.work.projects import WorkScan, scan_work_projects, work_key
from agora.work.reports import read_journal
from agora.work.service import WorkService
from agora.work.store import WorkSessionStore


# --- fixtures ----------------------------------------------------------------------------
def _tool(path: Path, workroot: Path) -> Path:
    (path / "scripts").mkdir(parents=True)
    (path / "scripts" / "cw.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / ".claude").mkdir()
    (path / ".workroot").write_text(f"{workroot.as_posix()}\n", encoding="utf-8")
    return path


def _task(source: Path, name: str, text: str | None) -> None:
    d = source / "tasks" / name
    d.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (d / "task.yaml").write_text(text, encoding="utf-8")


@dataclass
class FakePty:
    pid: int


@dataclass
class FakeSession:
    terminal_id: str
    project_id: str
    argv: list[str]
    env: dict[str, str]
    cwd: str
    started_at: float = field(default_factory=time.time)
    exited_at: float | None = None
    _pty: FakePty = field(default_factory=lambda: FakePty(0))

    def view(self) -> dict:
        return {"terminal_id": self.terminal_id, "project_id": self.project_id, "alive": self.exited_at is None,
                "command": self.argv}


class FakeManager:
    """Records what would have been started; starts nothing."""

    def __init__(self, delay: float = 0.0, fail: Exception | None = None):
        self.sessions: list[FakeSession] = []
        self.delay, self.fail = delay, fail

    def list(self):
        return list(self.sessions)

    def get(self, terminal_id):
        return next((s for s in self.sessions if s.terminal_id == terminal_id), None)

    def create(self, *, project_id, label, cwd, argv, env_extra=None, rows=30, cols=120):
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        session = FakeSession(uuid.uuid4().hex[:12], project_id, argv, dict(env_extra or {}), str(cwd))
        self.sessions.append(session)
        return session

    def close(self, terminal_id):
        session = self.get(terminal_id)
        if session is None:
            return False
        session.exited_at = time.time()
        return True


@pytest.fixture
def world(tmp_path):
    work = tmp_path / "work"
    _tool(tmp_path / "tool", work)
    _task(work, "alpha", "task:\n  title: Alpha\n  status: active\n")
    _task(work, "beta", "task:\n  title: Beta\n")
    _task(work, "old", "task:\n  status: archived\n")
    _task(work, "_archive", None)
    _task(work, "empty", None)
    _task(work, "listy", "- a\n- b\n")
    _task(work, "broken", "task: [\n")
    claude = tmp_path / "claude-projects"
    claude.mkdir()
    return tmp_path, work, claude


def _service(tmp_path: Path, work: Path, claude: Path, manager=None) -> WorkService:
    return WorkService(store=WorkSessionStore(tmp_path / "data" / "agora.db"), manager=manager or FakeManager(),
                       data_dir=tmp_path / "data", sources=lambda: [work], claude_projects=claude)


def run(coro):
    return asyncio.run(coro)


def _report(service: WorkService, key: str, *, event="SessionStart", source="startup", session=None,
            transcript=None, reason=None, launch=None, raw: str | None = None) -> str:
    row = service.store.get(key)
    launch = launch or row.launch_id
    folder = service._hooks.sessions_dir / key
    folder.mkdir(parents=True, exist_ok=True)
    line = raw if raw is not None else json.dumps({
        "v": 1, "key": key, "launch_id": launch, "event": event, "source": source, "reason": reason,
        "session_id": session, "transcript_path": str(transcript) if transcript else None, "cwd": None, "at": time.time(),
    })
    with open(folder / f"{launch}.jsonl", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return launch


def _transcript(claude: Path, session: str, exists=True, spoken=True) -> Path:
    path = claude / "E--tool" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        lines = ['{"type": "permission-mode"}', '{"type": "attachment"}']
        if spoken:
            lines.append('{"type": "user", "message": {"role": "user", "content": "hi"}}')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- which projects ----------------------------------------------------------------------
def test_active_and_unmarked_projects_are_listed_and_unreadable_ones_are_said(world):
    tmp_path, work, _ = world
    scan = scan_work_projects([work])
    assert [p.task for p in scan.projects] == ["alpha", "beta"]
    assert scan.inactive == 1
    reasons = {u["task"]: u["reason"] for u in scan.unreadable}
    assert set(reasons) == {"empty", "listy", "broken"}
    assert reasons["empty"] == "no task.yaml"
    assert "not a mapping" in reasons["listy"]
    assert all(p.tool_dir == str(tmp_path / "tool") for p in scan.projects)


def test_the_key_is_stable_and_not_a_path(world):
    _, work, _ = world
    key = work_key(work, "alpha")
    assert key == work_key(str(work), "alpha") and len(key) == 16 and "/" not in key


# --- opening -----------------------------------------------------------------------------
def test_opening_starts_claude_with_the_task_and_the_reporter_and_records_the_intent(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    out = run(service.open(key))
    assert out["result"] == "fresh_no_record"
    session = service.manager.sessions[0]
    assert session.env["CW_TASK"] == "alpha" and session.env["AGORA_WORK_KEY"] == key
    assert session.env["AGORA_WORK_LAUNCH"] == service.store.get(key).launch_id
    assert "--settings" in " ".join(session.argv) and "--resume" not in " ".join(session.argv)
    assert service.store.get(key).want_open


def test_opening_twice_returns_the_same_terminal(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    assert run(service.open(key))["result"] == "already_open"
    assert len(service.manager.sessions) == 1


def test_concurrent_open_and_restore_start_one_terminal(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude, FakeManager(delay=0.2))
    key = work_key(work, "alpha")
    service.store.begin_launch(key=key, source=str(work), task="alpha", workspace="claude",
                               launch_id="0" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "0" * 32, 0, None)

    async def both():
        return await asyncio.gather(service.open(key), service.restore())

    run(both())
    assert len(service.manager.sessions) == 1


def test_a_cancelled_open_does_not_let_a_second_one_start(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude, FakeManager(delay=0.3))
    key = work_key(work, "alpha")

    async def scenario():
        first = asyncio.create_task(service.open(key))
        await asyncio.sleep(0.05)
        first.cancel()
        second = await service.open(key)
        await asyncio.sleep(0.4)
        return second

    run(scenario())
    assert len(service.manager.sessions) == 1


def test_an_unknown_key_is_not_found(world):
    tmp_path, work, claude = world
    from agora.work.service import WorkProjectNotFound

    with pytest.raises(WorkProjectNotFound):
        run(_service(tmp_path, work, claude).open("0123456789abcdef"))


def test_a_failed_start_is_said_and_keeps_the_intent(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude, FakeManager(fail=TerminalLimitReached("at most 8")))
    key = work_key(work, "alpha")
    out = run(service.open(key))
    assert out == {"result": "failed", "detail": "at most 8"}
    assert service.store.get(key).want_open


# --- closing -----------------------------------------------------------------------------
def test_the_persons_close_clears_the_intent_and_shutdown_does_not(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    a, b = work_key(work, "alpha"), work_key(work, "beta")
    run(service.open(a)); run(service.open(b))
    service.manager.close(service.manager.sessions[1].terminal_id)  # what close_all does
    run(service.close(a))
    assert not service.store.get(a).want_open
    assert service.store.get(b).want_open


def test_restore_does_not_bring_back_what_was_closed_meanwhile(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude, FakeManager(delay=0.2))
    key = work_key(work, "alpha")
    service.store.begin_launch(key=key, source=str(work), task="alpha", workspace="claude",
                               launch_id="0" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "0" * 32, 0, None)

    async def scenario():
        await service.close(key)
        return await service.restore()

    assert run(scenario()) == {}
    assert service.manager.sessions == []


def test_a_close_during_launch_preparation_is_not_undone(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    real = service._hook_setup

    async def slow():
        await asyncio.sleep(0.2)
        return await real()

    monkeypatch.setattr(service, "_hook_setup", slow)

    async def scenario():
        opening = asyncio.create_task(service.open(key))
        await asyncio.sleep(0.05)
        await service.close(key)
        return await opening

    assert run(scenario())["result"] == "closed"
    assert service.manager.sessions == []
    row = service.store.get(key)
    assert row is None or not row.want_open


def test_a_restore_replaced_by_the_persons_own_open_is_marked_superseded(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _seed(service, key, work)

    async def scenario():
        await service.restore()
        await service.close(key)
        await service.open(key)

    run(scenario())
    assert service.view()["restore"][key]["superseded"] is True


# --- restoring ---------------------------------------------------------------------------
def _seed(service: WorkService, key: str, work: Path, task: str = "alpha", workspace: str = "claude") -> None:
    service.store.begin_launch(key=key, source=str(work), task=task, workspace=workspace,
                               launch_id="1" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "1" * 32, 0, None)


def test_restore_resumes_the_last_recorded_conversation_and_confirms_it_from_the_report(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "x"))
    _report(service, key, session=sid, transcript=_transcript(claude, sid), source="clear")

    results = run(service.restore())
    assert results[key]["result"] == "resume"
    argv = " ".join(service.manager.sessions[0].argv)
    assert f"{shell_literal('--resume')} {shell_literal(sid)}" in argv  # the conversation after /clear
    project = next(p for p in service.view()["projects"] if p["key"] == key)
    assert project["launch"]["confirm"] == "pending"

    _report(service, key, session=sid, transcript=_transcript(claude, sid), source="resume")
    project = next(p for p in service.view()["projects"] if p["key"] == key)
    assert project["launch"]["confirm"] == "resumed"
    # A later /clear does not rewrite how the restore turned out.
    other = str(uuid.uuid4())
    _report(service, key, event="SessionEnd", reason="clear", session=sid)
    _report(service, key, session=other, transcript=_transcript(claude, other), source="clear")
    project = next(p for p in service.view()["projects"] if p["key"] == key)
    assert project["launch"]["confirm"] == "resumed" and not project["agent_exited"]
    assert service.store.get(key).ck_session == other


def test_a_first_report_that_is_not_a_resume_is_not_called_one(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.restore())
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "y"), source="startup")
    assert next(p for p in service.view()["projects"] if p["key"] == key)["launch"]["confirm"] == "not_resumed"


def test_a_resume_of_another_conversation_is_said(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.restore())
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "z"), source="resume")
    assert next(p for p in service.view()["projects"] if p["key"] == key)["launch"]["confirm"] == "resumed_other"


def test_no_report_within_the_window_is_unconfirmed_and_a_late_one_still_counts(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    run(service.restore())
    row = service.store.get(key)
    assert service.confirmation(row, None, now=time.time() + 120) == "unconfirmed"
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "late"))
    row = service.store.get(key)
    journal = service._read(row)
    assert service.confirmation(row, journal, now=time.time() + 120) == "started"


@pytest.mark.parametrize("case, expected", [
    ("missing", "fresh_missing"),
    ("empty", "fresh_empty"),
    ("outside", "fresh_untrusted"),
    ("none", "fresh_no_record"),
])
def test_restore_without_a_usable_conversation_starts_fresh_and_says_why(world, case, expected):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    if case == "missing":
        _report(service, key, session=sid, transcript=_transcript(claude, sid, exists=False))
    elif case == "empty":
        # Claude writes the file at startup; nobody spoke in this one (owner, 11-03).
        _report(service, key, session=sid, transcript=_transcript(claude, sid, spoken=False))
    elif case == "outside":
        # Recorded straight into the store: a report outside Claude's directory never gets there.
        service.store.advance_checkpoint(key, session=sid, transcript=str(tmp_path / "elsewhere.jsonl"),
                                         launch="1" * 32, stale=False)
    results = run(service.restore())
    assert results[key]["result"] == expected
    assert "--resume" not in " ".join(service.manager.sessions[0].argv)


def test_an_interrupted_restore_keeps_the_point_it_was_restoring_to(world):
    tmp_path, work, claude = world
    key = work_key(work, "alpha")
    first = _service(tmp_path, work, claude, FakeManager(fail=OSError("server died")))
    run(first._hook_setup())
    _seed(first, key, work)
    sid = str(uuid.uuid4())
    _report(first, key, session=sid, transcript=_transcript(claude, sid))
    assert run(first.restore())[key]["result"] == "failed"
    # The next server: the failed launch never reported, the recovery point is still there.
    second = _service(tmp_path, work, claude)
    assert run(second.restore())[key]["result"] == "resume"
    assert sid in " ".join(second.manager.sessions[0].argv)


def test_a_session_change_that_could_not_be_read_marks_the_point_stale(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    _report(service, key, session=None, source="clear")
    service._read(service.store.get(key))
    row = service.store.get(key)
    assert row.ck_session == sid and row.ck_stale


@pytest.mark.parametrize("line", [
    "not json",
    json.dumps({"v": 1, "key": "wrong", "event": "SessionStart"}),
])
def test_broken_or_foreign_lines_are_not_recovery_points(world, line):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    _report(service, key, raw=line)
    journal = read_journal(service._journal_path(key, "1" * 32), key=key, launch_id="1" * 32, base=claude)
    assert journal.last_valid is None and journal.stale_after


def test_a_non_uuid_session_never_reaches_the_command_line(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    _report(service, key, session="$(evil)", transcript=_transcript(claude, "evil"))
    run(service.restore())
    assert "evil" not in " ".join(service.manager.sessions[0].argv)


def test_a_previous_shell_that_may_still_run_is_not_started_again(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _seed(service, key, work)
    from agora.work.procs import creation_time

    service.store.set_shell(key, "1" * 32, os.getpid(), creation_time(os.getpid()))
    assert run(service.restore())[key]["result"] == "maybe_running"
    assert service.manager.sessions == []


def test_a_kiro_project_is_reopened_and_says_it_cannot_resume(tmp_path):
    kiro = tmp_path / "kiro"
    (kiro / ".kiro" / "agents").mkdir(parents=True)
    (kiro / ".kiro" / "agents" / "k1.json").write_text('{"name": "k1"}', encoding="utf-8")
    _task(kiro, "k1", "task:\n  status: active\n")
    service = _service(tmp_path, kiro, tmp_path)
    key = work_key(kiro, "k1")
    service.store.begin_launch(key=key, source=str(kiro), task="k1", workspace="kiro",
                               launch_id="1" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "1" * 32, 0, None)
    assert run(service.restore())[key]["result"] == "fresh_unsupported"
    assert "--agent k1" in " ".join(service.manager.sessions[0].argv)


def test_an_open_project_that_is_no_longer_active_is_reported_not_dropped(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "gone")
    service.store.begin_launch(key=key, source=str(work), task="gone", workspace="claude",
                               launch_id="1" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "1" * 32, 0, None)
    assert run(service.restore())[key]["result"] == "unavailable"
    assert service.view()["missing"] == [{"key": key, "task": "gone", "source": str(work)}]
    run(service.close(key))
    assert not service.store.get(key).want_open


def test_claude_leaving_is_said_only_when_it_was_not_a_clear(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    _report(service, key, event="SessionEnd", reason="clear", session=sid)
    assert not next(p for p in service.view()["projects"] if p["key"] == key)["agent_exited"]
    _report(service, key, event="SessionEnd", reason="prompt_input_exit", session=sid)
    assert next(p for p in service.view()["projects"] if p["key"] == key)["agent_exited"]


# --- the hook ----------------------------------------------------------------------------
def test_the_reporter_appends_what_claude_sends(world, monkeypatch, capsys):
    tmp_path, *_ = world
    import io
    import sys

    from agora.work import report_hook

    folder = tmp_path / "journal"
    folder.mkdir()
    monkeypatch.setenv("AGORA_WORK_DIR", str(folder))
    monkeypatch.setenv("AGORA_WORK_KEY", "a" * 16)
    monkeypatch.setenv("AGORA_WORK_LAUNCH", "b" * 32)
    for payload in ('{"hook_event_name": "SessionStart", "source": "startup", "session_id": "s"}', "garbage"):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload.encode("utf-8")), encoding="utf-8"))
        report_hook.main()
    lines = (folder / ("b" * 32 + ".jsonl")).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["session_id"] == "s"
    assert json.loads(lines[1])["session_id"] is None  # a failure is a line too
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("bad", ["C:/x/$(id)/python.exe", "C:/x/`id`/python.exe", "C:/x/it's/python.exe"])
def test_a_path_the_hook_shell_would_interpret_installs_no_hook(tmp_path, bad):
    setup = hooks_module.install(tmp_path / "data", python=bad)
    assert setup.settings is None and "characters" in setup.reason


def test_the_hook_settings_name_both_events(tmp_path):
    setup = hooks_module.install(tmp_path / "data")
    if setup.settings is None:
        pytest.skip(f"this checkout's path cannot carry a hook: {setup.reason}")
    hooks = json.loads(setup.settings.read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"SessionStart", "SessionEnd"}


def test_no_hook_when_the_directories_cannot_be_restricted(tmp_path, monkeypatch):
    def refuse(path):
        raise OSError("denied")

    monkeypatch.setattr(hooks_module, "_restrict_dir", refuse)
    setup = hooks_module.install(tmp_path / "data")
    assert setup.settings is None and "restrict" in setup.reason


# --- the shell boundary (CR-75 §4) -------------------------------------------------------
@pytest.mark.parametrize("value", ["C:/a b/x", "it's", "$(Get-Date)", "`whoami`", "数据 dir"])
def test_an_agent_argument_reaches_the_shell_literally(tmp_path, value):
    manager = TerminalManager()
    argv = command_argv("claude", agent_args=[value])
    # The same wrapping, with a program that prints its argument instead of claude.
    if os.name == "nt":
        argv = [argv[0], "-NoLogo", "-Command", f"Write-Output ('<' + {shell_literal(value)} + '>')"]
    else:
        argv = [argv[0], "-c", f"printf '<%s>\\n' {shell_literal(value)}"]
    session = manager.create(project_id="t", label="t", cwd=tmp_path, argv=argv)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and f"<{value}>" not in "".join(t for _, t in session._chunks):
            time.sleep(0.1)
        assert f"<{value}>" in "".join(t for _, t in session._chunks)
    finally:
        manager.close_all()


def test_a_double_quote_is_refused_before_the_shell():
    with pytest.raises(ValueError):
        command_argv("claude", agent_args=['a"b'])


# --- routes ------------------------------------------------------------------------------
@pytest.fixture
def api(world, monkeypatch):
    tmp_path, work, claude = world
    registry = ProjectRegistry({
        "projects": {
            "registry_path": str(tmp_path / "cfg" / "projects.yaml"),
            "default": "home",
            "projects": {"home": {"name": "Home", "root": str(tmp_path / "home")}},
        }
    })
    monkeypatch.setattr(registry, "workspace_sources", lambda: [str(work)])
    monkeypatch.setattr(terminal_router, "_data_dir", lambda: tmp_path / "data")
    service = _service(tmp_path, work, claude)
    monkeypatch.setattr(work_router, "get_work_service", lambda: service)
    app.dependency_overrides[get_project_registry] = lambda: registry
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            yield client, service, work
    finally:
        app.dependency_overrides.clear()


def test_every_work_route_needs_the_terminal_token(api, tmp_path):
    client, service, work = api
    key = work_key(work, "alpha")
    for method, path in [("get", "/api/work/projects"), ("post", f"/api/work/projects/{key}/open"),
                         ("post", f"/api/work/projects/{key}/close"), ("post", "/api/work/restore")]:
        for headers in ({}, {"X-Agora-Terminal-Token": "wrong"}):
            assert getattr(client, method)(path, headers=headers).status_code == 401, (method, path)
    assert service.manager.sessions == []
    assert not (tmp_path / "data" / "work-hooks").exists()


def test_the_page_round_trip(api, tmp_path):
    client, service, work = api
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    listed = client.get("/api/work/projects").json()
    assert [p["task"] for p in listed["projects"]] == ["alpha", "beta"]
    assert client.post(f"/api/work/projects/{key}/open").json()["result"] == "fresh_no_record"
    assert client.post("/api/work/projects/nope/open").status_code == 404
    assert client.post("/api/work/restore").json()["restore"][key]["result"] == "already_open"
    assert client.post(f"/api/work/projects/{key}/close").json()["closed"]
    assert not service.store.get(key).want_open


def test_closing_a_work_terminal_from_the_terminal_page_is_the_persons_close(api, tmp_path, monkeypatch):
    client, service, work = api
    monkeypatch.setattr(terminal_router, "get_terminal_manager", lambda: service.manager)
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    terminal = client.post(f"/api/work/projects/{key}/open").json()["terminal"]
    assert client.delete(f"/api/terminals/{terminal['terminal_id']}").status_code == 200
    assert not service.store.get(key).want_open


def test_a_terminal_does_not_inherit_the_agent_session_agora_was_started_from(tmp_path, monkeypatch):
    for name in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_TOKEN"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")  # the person's own setting stays
    manager = TerminalManager()
    if os.name == "nt":
        argv = [command_argv("shell")[0], "-NoLogo", "-Command",
                "Write-Output ('<' + [string]$env:CLAUDE_CODE_CHILD_SESSION + $env:CLAUDECODE + '|' + $env:CLAUDE_CODE_USE_BEDROCK + '>')"]
    else:
        argv = [command_argv("shell")[0], "-c", 'printf "<%s%s|%s>\n" "$CLAUDE_CODE_CHILD_SESSION" "$CLAUDECODE" "$CLAUDE_CODE_USE_BEDROCK"']
    session = manager.create(project_id="t", label="t", cwd=tmp_path, argv=argv)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and "|1>" not in "".join(t for _, t in session._chunks):
            time.sleep(0.1)
        assert "<|1>" in "".join(t for _, t in session._chunks)
    finally:
        manager.close_all()


def test_a_report_the_hook_could_not_read_makes_the_point_stale(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    _report(service, key, event=None, source=None)  # what report_hook writes for unreadable input
    service._read(service.store.get(key))
    assert service.store.get(key).ck_stale


def test_concurrent_launches_install_the_hook_once(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    calls = []
    real = hooks_module.install

    def counting(*args, **kwargs):
        calls.append(1)
        time.sleep(0.1)
        return real(*args, **kwargs)

    monkeypatch.setattr(hooks_module, "install", counting)

    async def both():
        await asyncio.gather(service.open(work_key(work, "alpha")), service.open(work_key(work, "beta")))

    run(both())
    assert len(calls) == 1



# --- kiro review (round 3) ---------------------------------------------------------------
def test_a_launch_that_never_recorded_its_shell_is_not_started_again(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    service.store.begin_launch(key=key, source=str(work), task="alpha", workspace="claude",
                               launch_id="1" * 32, launch_mode="fresh", expect_session=None)
    assert run(service.restore())[key]["result"] == "maybe_running"
    assert service.manager.sessions == []


def test_a_failed_spawn_records_that_nothing_is_running(world):
    tmp_path, work, claude = world
    key = work_key(work, "alpha")
    first = _service(tmp_path, work, claude, FakeManager(fail=OSError("no pty")))
    assert run(first.open(key))["result"] == "failed"
    second = _service(tmp_path, work, claude)
    assert run(second.restore())[key]["result"] != "maybe_running"


def test_an_unreadable_journal_is_not_pruned(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    launch = _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "q"))
    journal = service._hooks.sessions_dir / key / f"{launch}.jsonl"
    import agora.work.reports as reports_module

    real_open = open

    def locked(path, *args, **kwargs):
        if str(path) == str(journal):
            raise PermissionError("locked")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(reports_module, "open", locked, raising=False)
    run(service.open(key))
    assert journal.exists()


def test_a_clear_without_the_next_start_marks_the_point_stale(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    _report(service, key, event="SessionEnd", reason="clear", session=sid)
    service._read(service.store.get(key))
    assert service.store.get(key).ck_stale


def test_one_unprobeable_task_does_not_hide_the_rest_of_the_source(world, monkeypatch):
    tmp_path, work, _ = world
    real = Path.stat

    # The probe is ``stat`` itself: from Python 3.14 ``is_dir`` answers False for a read
    # that failed, which would hide the entry instead (kiro review round 10).
    def flaky(self, *args, **kwargs):
        if self.name == "alpha" and self.parent.name == "tasks":
            raise PermissionError("denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky)
    scan = scan_work_projects([work])
    assert "beta" in [p.task for p in scan.projects]
    assert any(u["task"] == "alpha" and u["reason"] == "PermissionError" for u in scan.unreadable)


def test_an_open_that_started_before_a_close_does_not_start_after_it(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    real = service._project

    def slow(k):
        time.sleep(0.3)
        return real(k)

    monkeypatch.setattr(service, "_project", slow)

    async def scenario():
        opening = asyncio.create_task(service.open(key))
        await asyncio.sleep(0.05)
        await service.close(key)
        return await opening

    assert run(scenario())["result"] == "closed"
    assert service.manager.sessions == []


def test_a_restore_that_failed_as_a_whole_is_tried_again(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    calls = []
    real = service.store.all

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("busy")
        return real()

    monkeypatch.setattr(service.store, "all", flaky)

    async def twice():
        with pytest.raises(OSError):
            await service.restore()
        return await service.restore()

    assert run(twice()) == {}


def test_a_session_id_with_a_trailing_newline_is_not_a_session_id(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid + "\n", transcript=_transcript(claude, sid))
    service._read(service.store.get(key))
    assert service.store.get(key).ck_session is None


def test_an_unreadable_project_is_not_called_inactive(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "broken")
    _seed(service, key, work, task="broken")
    view = service.view()
    assert all(m["task"] != "broken" for m in view["missing"])
    assert any(u["task"] == "broken" for u in view["unreadable"])


def test_a_grant_to_anyone_else_is_found():
    listing = "C:\\x DOM\\me:(OI)(CI)(F)\n    Everyone:(F)\n\nSuccessfully processed 1 files"
    assert hooks_module.foreign_grants(listing, "C:\\x", "me") == ["Everyone"]
    assert hooks_module.foreign_grants("C:\\x DOM\\me:(OI)(CI)(F)\n", "C:\\x", "me") == []
    # The same bare name under another domain is another account (codex review).
    two = "C:\\x DOM\\me:(OI)(CI)(F)\n    PC\\me:(OI)(CI)(F)\n"
    assert hooks_module.foreign_grants(two, "C:\\x", "DOM\\me") == ["PC\\me"]
    assert hooks_module.foreign_grants("C:\\x dom\\ME:(OI)(CI)(F)\n", "C:\\x", "DOM\\me") == []


# --- kiro review (round 4) ---------------------------------------------------------------
def test_a_terminal_being_closed_is_not_the_live_one(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    service.manager.sessions[0]._closing = True
    # Still running after its close: refused, not answered with it and not run twice
    # (kiro review round 9 — two agents would work on the same files).
    out = run(service.open(key))
    assert out["result"] == "failed" and "not ended" in out["detail"]
    assert len(service.manager.sessions) == 1


def test_a_journal_larger_than_is_read_stays_stale(world, monkeypatch):
    tmp_path, work, claude = world
    import agora.work.reports as reports_module

    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    path = service._journal_path(key, "1" * 32)
    monkeypatch.setattr(reports_module, "MAX_JOURNAL_BYTES", path.stat().st_size)
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "later"))
    journal = read_journal(path, key=key, launch_id="1" * 32, base=claude)
    assert journal.last_valid.session_id == sid and journal.stale_after


def test_claude_config_dir_moves_the_trusted_transcripts(tmp_path, monkeypatch):
    from agora.work.reports import claude_projects_dir, trusted_transcript

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "日本語 claude"))
    assert claude_projects_dir() == tmp_path / "日本語 claude" / "projects"
    assert trusted_transcript(str(tmp_path / "日本語 claude" / "projects" / "x" / "a.jsonl")) is not None


def test_projects_of_a_source_that_cannot_be_read_are_not_called_inactive(world):
    tmp_path, work, claude = world
    gone = tmp_path / "unplugged"
    service = WorkService(store=WorkSessionStore(tmp_path / "data" / "agora.db"), manager=FakeManager(),
                          data_dir=tmp_path / "data", sources=lambda: [work, gone], claude_projects=claude)
    key = work_key(gone, "t")
    service.store.begin_launch(key=key, source=str(gone), task="t", workspace="claude",
                               launch_id="1" * 32, launch_mode="fresh", expect_session=None)
    assert service.view()["missing"] == []


def test_a_workroot_that_is_not_utf8_does_not_break_the_page(tmp_path):
    work = tmp_path / "work"
    _task(work, "alpha", "task:\n  status: active\n")
    tool = _tool(tmp_path / "tool", work)
    (tool / ".workroot").write_bytes("作業".encode("shift_jis"))
    scan = scan_work_projects([work])
    assert [p.task for p in scan.projects] == ["alpha"] and scan.projects[0].tool_dir is None


def test_a_removed_hook_is_installed_again(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    setup = run(service._hook_setup())
    if setup.settings is None:
        pytest.skip(setup.reason)
    (setup.settings.parent / "report_hook.py").unlink()
    run(service._hook_setup())
    assert (setup.settings.parent / "report_hook.py").is_file()


def test_an_emptied_hook_is_installed_again(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    setup = run(service._hook_setup())
    if setup.settings is None:
        pytest.skip(setup.reason)
    (setup.settings.parent / "report_hook.py").write_bytes(b"")
    setup.settings.write_bytes(b"")
    run(service._hook_setup())
    assert (setup.settings.parent / "report_hook.py").stat().st_size > 0
    assert "SessionStart" in setup.settings.read_text(encoding="utf-8")


def test_a_link_in_the_work_data_is_refused(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    try:
        os.symlink(real, data / "work-hooks", target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    setup = hooks_module.install(data)
    assert setup.settings is None and "link" in setup.reason
    assert not (real / "report_hook.py").exists()


# --- kiro review (round 5) ---------------------------------------------------------------
def test_a_fresh_launch_is_started_only_on_a_startup_report(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "r"), source="resume")
    assert next(p for p in service.view()["projects"] if p["key"] == key)["launch"]["confirm"] == "unconfirmed"


def test_an_unreadable_first_report_is_latched_as_unreadable(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    _report(service, key, event=None, source=None)
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "s"), source="clear")
    assert next(p for p in service.view()["projects"] if p["key"] == key)["launch"]["confirm"] == "unreadable_report"


def test_an_unreadable_line_after_an_exit_does_not_keep_saying_claude_quit(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    _report(service, key, event="SessionEnd", reason="prompt_input_exit", session=sid)
    _report(service, key, raw="garbage")
    assert not next(p for p in service.view()["projects"] if p["key"] == key)["agent_exited"]


def test_a_running_terminal_of_a_project_no_longer_listed_stays_reachable(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    (work / "tasks" / "alpha" / "task.yaml").write_text("task: [\n", encoding="utf-8")
    view = service.view()
    assert [d["key"] for d in view["detached"]] == [key]
    run(service.close(key))
    assert service.view()["detached"] == []


# --- kiro review (round 6) ---------------------------------------------------------------
def test_the_reporter_reads_utf8_whatever_the_console_code_page(world, monkeypatch):
    tmp_path, *_ = world
    import io
    import sys

    from agora.work import report_hook

    folder = tmp_path / "journal"
    folder.mkdir()
    monkeypatch.setenv("AGORA_WORK_DIR", str(folder))
    monkeypatch.setenv("AGORA_WORK_KEY", "a" * 16)
    monkeypatch.setenv("AGORA_WORK_LAUNCH", "b" * 32)
    payload = json.dumps({"hook_event_name": "SessionStart", "transcript_path": "E:/日本語/x.jsonl"},
                         ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="cp932", errors="replace"))
    report_hook.main()
    line = json.loads((folder / ("b" * 32 + ".jsonl")).read_text(encoding="utf-8"))
    assert line["transcript_path"] == "E:/日本語/x.jsonl"


def test_a_journal_read_for_an_earlier_launch_confirms_nothing_about_a_later_one(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "o"))
    old = service._read(service.store.get(key))
    run(service.close(key)); run(service.open(key))
    assert service.confirmation(service.store.get(key), old) == "pending"


def test_a_failed_reopen_supersedes_the_restore(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _seed(service, key, work)
    run(service.restore())
    run(service.close(key))
    service.manager.fail = OSError("no pty")
    assert run(service.open(key))["result"] == "failed"
    assert service.view()["restore"][key]["superseded"] is True


def test_an_archived_running_project_is_listed_once(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    (work / "tasks" / "alpha" / "task.yaml").write_text("task:\n  status: archived\n", encoding="utf-8")
    view = service.view()
    assert [d["key"] for d in view["detached"]] == [key] and view["missing"] == []


# --- kiro review (round 7) ---------------------------------------------------------------
def test_a_clear_report_is_not_the_launchs_first(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    run(service._hook_setup())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.restore())
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "c"), source="clear")
    assert next(p for p in service.view()["projects"] if p["key"] == key)["launch"]["confirm"] == "unreadable_report"


def test_a_concurrent_open_keeps_its_terminal_while_restore_shares_the_launch(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude, FakeManager(delay=0.2))
    key = work_key(work, "alpha")
    _seed(service, key, work)

    async def both():
        return await asyncio.gather(service.restore(), service.open(key))

    _, opened = run(both())
    assert "terminal" in opened


def test_an_unreadable_project_still_wanted_open_can_be_closed(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "broken")
    _seed(service, key, work, task="broken")
    entry = next(u for u in service.view()["unreadable"] if u["task"] == "broken")
    assert entry["key"] == key and entry["want_open"]


# --- kiro review (round 8) ---------------------------------------------------------------
def test_a_junction_is_refused(tmp_path):
    if os.name != "nt":
        pytest.skip("junctions are Windows")
    import subprocess

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "j"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True, check=True)
    with pytest.raises(hooks_module.LinkRefused):
        hooks_module.refuse_link(link)


def test_closing_an_ended_work_terminal_again_does_not_close_the_new_one(api, tmp_path, monkeypatch):
    client, service, work = api
    monkeypatch.setattr(terminal_router, "get_terminal_manager", lambda: service.manager)
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    first = client.post(f"/api/work/projects/{key}/open").json()["terminal"]["terminal_id"]
    client.post(f"/api/work/projects/{key}/close")
    client.post(f"/api/work/projects/{key}/open")
    client.delete(f"/api/terminals/{first}")
    assert service.store.get(key).want_open
    assert service._live(key) is not None


def test_a_launch_that_left_no_report_makes_the_point_stale(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.open(key))          # launch B: reports nothing
    run(service.close(key))
    run(service.open(key))          # launch C reads B's empty journal first
    row = service.store.get(key)
    assert row.ck_session == sid and row.ck_stale


def test_an_open_after_a_close_starts_rather_than_inheriting_the_cancelled_launch(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    real = service._hook_setup

    async def slow():
        await asyncio.sleep(0.2)
        return await real()

    monkeypatch.setattr(service, "_hook_setup", slow)

    async def scenario():
        first = asyncio.create_task(service.open(key))
        await asyncio.sleep(0.05)
        await service.close(key)
        second = await service.open(key)
        await first
        return second

    assert run(scenario())["result"] != "closed"
    assert len(service.manager.sessions) == 1


def test_an_open_while_a_close_waits_is_not_answered_with_the_terminal_being_closed(world, monkeypatch):
    # The launch has registered its terminal but not finished; a close is waiting for it
    # when another open arrives. That open must outlive the close (codex review, CR-77).
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    real = service._launch

    async def slow_finish(*args, **kwargs):
        outcome = await real(*args, **kwargs)
        await asyncio.sleep(0.2)
        return outcome

    monkeypatch.setattr(service, "_launch", slow_finish)

    async def scenario():
        first = asyncio.create_task(service.open(key))
        while not service.manager.sessions:  # the terminal is registered, the launch not done
            await asyncio.sleep(0.01)
        closing = asyncio.create_task(service.close(key))
        await asyncio.sleep(0.05)
        second = await service.open(key)
        await asyncio.gather(first, closing)
        return second

    assert run(scenario())["result"] != "closed"
    assert service._live(key) is not None
    assert service.store.get(key).want_open


def test_a_cancelled_close_request_still_ends_the_terminal(world, monkeypatch):
    # The request waiting on a launch is cancelled (the browser went away); the close the
    # intent was already written for still ends what that launch started (codex review).
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    real = service._launch

    async def slow_finish(*args, **kwargs):
        outcome = await real(*args, **kwargs)
        await asyncio.sleep(0.2)
        return outcome

    monkeypatch.setattr(service, "_launch", slow_finish)

    async def scenario():
        first = asyncio.create_task(service.open(key))
        while not service.manager.sessions:
            await asyncio.sleep(0.01)
        request = asyncio.create_task(service.close(key))
        await asyncio.sleep(0.05)
        request.cancel()
        await asyncio.gather(first, return_exceptions=True)
        while service._close_tasks:
            await asyncio.sleep(0.01)

    run(scenario())
    assert service._live(key) is None
    assert not service.store.get(key).want_open


def test_a_close_while_an_open_waits_for_an_earlier_close_wins(world):
    # close -> open (waits for that close) -> close: the last word is the second close.
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    real = service.manager.close

    def slow_close(terminal_id):
        time.sleep(0.2)
        return real(terminal_id)

    service.manager.close = slow_close

    async def scenario():
        first_close = asyncio.create_task(service.close(key))
        await asyncio.sleep(0.05)
        waiting = asyncio.create_task(service.open(key))
        await asyncio.sleep(0.05)
        await service.close(key)
        out = await waiting
        await first_close
        return out

    assert run(scenario())["result"] == "closed"
    assert service._live(key) is None
    assert not service.store.get(key).want_open


def test_closing_a_work_terminal_that_ended_on_its_own_clears_the_intent(api, tmp_path, monkeypatch):
    client, service, work = api
    monkeypatch.setattr(terminal_router, "get_terminal_manager", lambda: service.manager)
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    first = client.post(f"/api/work/projects/{key}/open").json()["terminal"]["terminal_id"]
    service.manager.get(first).exited_at = time.time()  # claude quit, the shell ended
    assert client.delete(f"/api/terminals/{first}").status_code == 200
    assert not service.store.get(key).want_open


def test_an_agent_name_with_a_trailing_newline_is_refused():
    with pytest.raises(ValueError):
        command_argv("kiro", kiro_agent="agent\n")


@pytest.mark.skip(reason="CR-78 (defer): a project wanted open under a whole source that cannot be "
                         "read has no close on the page; restore says 'unavailable' each start "
                         "until the source is back. Remove this skip when CR-78 is fixed.")
def test_a_project_wanted_open_under_an_unreadable_source_can_be_closed(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    run(service.close(key))
    service.store.set_want_open(key, True)
    row = service.store.get(key)
    # The project's source as a whole cannot be read (a drive gone, access refused).
    service.scan = lambda: WorkScan([], [{"source": row.source, "task": "", "reason": "PermissionError"}], 0)
    view = service.view()
    rows = [*view["missing"], *view["unreadable"]]
    assert any(r.get("key") == key for r in rows)


# --- kiro review (round 10) --------------------------------------------------------------
def test_a_previous_journal_not_read_for_want_of_a_hook_makes_the_point_stale(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.open(key))          # launch B: may report after this, unread so far
    run(service.close(key))

    async def no_hook():
        service._hooks = hooks_module.HookSetup(None, None, "denied")
        return service._hooks

    monkeypatch.setattr(service, "_hook_setup", no_hook)
    run(service.open(key))          # launch C cannot read B's journal
    row = service.store.get(key)
    assert row.ck_session == sid and row.ck_stale


def test_a_transcript_that_cannot_be_checked_is_not_called_gone(world, monkeypatch):
    import agora.work.service as service_module

    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    sid = str(uuid.uuid4())
    _seed(service, key, work)
    service.store.advance_checkpoint(key, session=sid, transcript=str(_transcript(claude, sid)),
                                     launch="1" * 32, stale=False)

    class Unreadable:
        def stat(self):
            raise PermissionError("denied")

    monkeypatch.setattr(service_module, "trusted_transcript", lambda *_: Unreadable())
    setup = hooks_module.HookSetup(tmp_path / "s.json", tmp_path, None)
    assert service._decide(service.store.get(key), setup)[1] == "fresh_untrusted"


def test_settings_without_our_command_are_not_the_hook(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    setup = run(service._hook_setup())
    if setup.settings is None:
        pytest.skip(setup.reason)
    assert hooks_module.still_installed(setup)
    setup.settings.write_text('{"hooks": {"SessionStart": [], "SessionEnd": []}}', encoding="utf-8")
    assert not hooks_module.still_installed(setup)



# --- kiro review (round 11) --------------------------------------------------------------
def test_a_journal_with_an_end_but_no_start_makes_the_point_stale(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.open(key))          # launch B: only its SessionEnd got written
    _report(service, key, session=sid, transcript=_transcript(claude, sid), event="SessionEnd",
            reason="other")
    run(service.close(key))
    run(service.open(key))
    row = service.store.get(key)
    assert row.ck_session == sid and row.ck_stale


def test_closing_an_older_ended_terminal_again_keeps_the_newer_intent(api, tmp_path, monkeypatch):
    client, service, work = api
    monkeypatch.setattr(terminal_router, "get_terminal_manager", lambda: service.manager)
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    first = client.post(f"/api/work/projects/{key}/open").json()["terminal"]["terminal_id"]
    service.manager.get(first).exited_at = time.time()
    second = client.post(f"/api/work/projects/{key}/open").json()["terminal"]["terminal_id"]
    service.manager.get(second).exited_at = time.time()
    client.delete(f"/api/terminals/{first}")  # an old page, closing the first one again
    assert service.store.get(key).want_open



# --- kiro review (round 12) --------------------------------------------------------------
def test_a_previous_journal_that_failed_to_read_makes_the_point_stale(world, monkeypatch):
    import agora.work.service as service_module

    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    _report(service, key, session=sid, transcript=_transcript(claude, sid))
    run(service.open(key))          # launch B
    run(service.close(key))
    real = service_module.read_journal

    def locked(path, **kwargs):
        journal = real(path, **kwargs)
        journal.read_failed = True  # an antivirus holding B's journal at this moment
        return journal

    monkeypatch.setattr(service_module, "read_journal", locked)
    run(service.open(key))
    row = service.store.get(key)
    assert row.ck_session == sid and row.ck_stale


def test_a_terminal_whose_record_cannot_be_written_is_closed(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service.store, "set_shell", full)
    with pytest.raises(OSError):
        run(service.open(key))
    assert service._live(key) is None


def test_two_closes_at_once_end_the_terminal_once(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    real = service.manager.close
    calls = []

    def slow_close(terminal_id):
        calls.append(terminal_id)
        time.sleep(0.1)
        return real(terminal_id)

    monkeypatch.setattr(service.manager, "close", slow_close)

    async def scenario():
        return await asyncio.gather(service.close(key), service.close(key))

    assert run(scenario()) == [[], []]
    assert len(calls) == 1


def test_a_close_that_could_not_end_the_terminal_says_so(api, tmp_path, monkeypatch):
    client, service, work = api
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    client.post(f"/api/work/projects/{key}/open")
    monkeypatch.setattr(service.manager, "close", lambda terminal_id: True)  # it would not die
    out = client.post(f"/api/work/projects/{key}/close").json()
    assert out["closed"] is False and out["still_running"]
    assert not service.store.get(key).want_open


def test_a_close_whose_intent_cannot_be_written_leaves_no_pending_close(world, monkeypatch):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))

    def locked(*args, **kwargs):
        raise OSError("database is locked")

    monkeypatch.setattr(service.store, "set_want_open", locked)
    with pytest.raises(OSError):
        run(service.close(key))
    assert key not in service._pending_close
    assert run(service.open(key))["result"] == "already_open"


def test_the_shell_reentered_after_the_agent_is_quoted(monkeypatch):
    import agora.terminal.manager as manager_module

    if os.name == "nt":
        pytest.skip("POSIX command line")
    monkeypatch.setattr(manager_module, "default_shell", lambda: ["/opt/My Shell/bin/bash"])
    argv = command_argv("claude")
    assert argv[-1].endswith("exec '/opt/My Shell/bin/bash' -i")
