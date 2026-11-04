"""Pausing, archiving and restoring a work project (workspace CR-80): the project's own
task.yaml says so afterwards, and nothing a task.yaml layout cannot take is written."""
from __future__ import annotations

import asyncio
import threading
from datetime import date
from pathlib import Path

import pytest
import yaml

from agora.work import shelf
from agora.work.projects import scan_work_projects, work_key
from agora.work.shelf import ShelfRefused, edit_status

from tests.test_work import FakeSession, _service, _task, _tool, api, run, world  # noqa: F401  (fixtures)
from agora.terminal.auth import load_or_create_token


TEMPLATE = """# task.yaml
task:
  name: alpha
  title: Alpha
  goal: |
    Do the thing.
    status: this line is goal text, not a key
  status: active      # set by cw
  type: dev

milestones:
  - id: M1
    status: done
"""


def _doc(text: str) -> dict:
    return yaml.safe_load(text)


# --- the edit itself ----------------------------------------------------------------------
def test_pausing_changes_only_the_status_line_and_keeps_everything_else():
    out = edit_status(TEMPLATE, "paused").text
    assert "  status: paused\n" in out
    assert "    status: this line is goal text, not a key\n" in out
    assert "    status: done\n" in out  # a milestone's status is not the task's
    assert out.count("\n") == TEMPLATE.count("\n")
    before, after = _doc(TEMPLATE), _doc(out)
    assert after["task"]["status"] == "paused"
    after["task"]["status"] = "active"
    assert after == before


def test_a_task_without_a_status_gets_one_inside_its_task_mapping():
    text = "task:\n  title: Beta\n  goal: g\nmilestones: []\n"
    out = edit_status(text, "paused").text
    assert _doc(out) == {"task": {"title": "Beta", "goal": "g", "status": "paused"}, "milestones": []}
    assert out.index("status: paused") > out.index("goal: g")


def test_a_document_without_a_task_mapping_gets_a_top_level_status():
    assert _doc(edit_status("title: Kiro\nstatus: active\n", "paused").text) == {"title": "Kiro", "status": "paused"}
    assert _doc(edit_status("title: Kiro\n", "archived").text) == {"title": "Kiro", "status": "archived"}


def test_line_endings_are_kept():
    out = edit_status("task:\r\n  title: A\r\n  status: active\r\n", "paused").text
    assert out == "task:\r\n  title: A\r\n  status: paused\r\n"


@pytest.mark.parametrize("text", [
    "task: {title: A, status: active}\n",   # flow style: the line edit cannot place it
    "- a\n- b\n",
    "task: [\n",
])
def test_a_layout_the_edit_cannot_change_safely_is_refused(text):
    with pytest.raises(ShelfRefused):
        edit_status(text, "paused")


# --- archive / restore on disk ------------------------------------------------------------
def test_archive_matches_cw_archive_layout(tmp_path):
    """What ``cw archive`` (claude-workspace scripts/cw.sh) produces: status archived,
    completed_at, the directory under tasks/_archive/ — and unarchive undoes it."""
    _task(tmp_path, "alpha", TEMPLATE)
    shelf.archive(tmp_path, "alpha", move=True, today=date(2026, 11, 4))
    assert not (tmp_path / "tasks" / "alpha").exists()
    moved = _doc((tmp_path / "tasks" / "_archive" / "alpha" / "task.yaml").read_text(encoding="utf-8"))
    assert moved["task"]["status"] == "archived" and moved["task"]["completed_at"] == date(2026, 11, 4)
    shelf.restore(tmp_path, "alpha", archived_dir=True)
    assert (tmp_path / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8") == TEMPLATE.replace(
        "status: active      # set by cw", "status: active")


def test_a_kiro_archive_only_changes_the_status(tmp_path):
    _task(tmp_path, "k", "task:\n  title: K\n")
    shelf.archive(tmp_path, "k", move=False)
    assert _doc((tmp_path / "tasks" / "k" / "task.yaml").read_text(encoding="utf-8")) == \
        {"task": {"title": "K", "status": "archived"}}


def test_the_workspaces_current_task_is_not_archived(tmp_path):
    _task(tmp_path, "alpha", TEMPLATE)
    (tmp_path / ".current-task").write_text("alpha\n", encoding="utf-8")
    with pytest.raises(ShelfRefused, match="current task"):
        shelf.archive(tmp_path, "alpha", move=True)
    assert (tmp_path / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8") == TEMPLATE


def test_an_existing_archive_entry_is_not_overwritten(tmp_path):
    _task(tmp_path, "alpha", TEMPLATE)
    (tmp_path / "tasks" / "_archive" / "alpha").mkdir(parents=True)
    with pytest.raises(ShelfRefused, match="already exists"):
        shelf.archive(tmp_path, "alpha", move=True)
    assert (tmp_path / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8") == TEMPLATE


def test_a_move_that_fails_puts_the_record_back(tmp_path, monkeypatch):
    _task(tmp_path, "alpha", TEMPLATE)

    def fail(*_a, **_k):
        raise PermissionError("in use")
    monkeypatch.setattr(shelf.os, "rename", fail)
    with pytest.raises(ShelfRefused, match="nothing changed"):
        shelf.archive(tmp_path, "alpha", move=True)
    assert (tmp_path / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8") == TEMPLATE


def test_shelved_projects_are_listed_with_where_they_are(tmp_path):
    _task(tmp_path, "alpha", "task:\n  status: active\n")
    _task(tmp_path, "paused-one", "task:\n  title: P\n  status: paused\n")
    _task(tmp_path, "_archive/done-one", "task:\n  status: archived\n")
    (tmp_path / "tasks" / "_archive" / "no-yaml").mkdir()
    scan = scan_work_projects([tmp_path])
    assert [p.task for p in scan.projects] == ["alpha"]
    assert {(p.task, p.status, p.archived_dir) for p in scan.shelved} == {
        ("paused-one", "paused", False), ("done-one", "archived", True)}
    assert {"source": str(tmp_path), "task": "_archive/no-yaml", "reason": "no task.yaml"} in scan.unreadable
    assert scan.inactive == 2


# --- the service and the routes -----------------------------------------------------------
def test_a_project_with_its_terminal_open_is_not_put_away(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    from agora.work.service import WorkProjectBusy
    with pytest.raises(WorkProjectBusy):
        run(service.shelve(key, "pause"))
    run(service.close(key))
    run(service.shelve(key, "pause"))
    assert [p.task for p in service.scan().shelved if p.status == "paused"] == ["alpha"]


def test_pause_archive_and_restore_round_trip_through_the_api(api, tmp_path):
    client, service, work = api
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    alpha, beta = work_key(work, "alpha"), work_key(work, "beta")
    assert client.post(f"/api/work/projects/{alpha}/shelve", json={"action": "pause"}).status_code == 200
    assert client.post(f"/api/work/projects/{beta}/shelve", json={"action": "archive"}).status_code == 200
    listed = client.get("/api/work/projects").json()
    assert [p["task"] for p in listed["projects"]] == []
    shelved = {p["task"]: (p["status"], p["archived_dir"]) for p in listed["shelved"]}
    assert shelved["alpha"] == ("paused", False) and shelved["beta"] == ("archived", True)
    assert client.post(f"/api/work/projects/{alpha}/shelve", json={"action": "delete"}).status_code == 422
    assert client.post("/api/work/projects/nope/shelve", json={"action": "pause"}).status_code == 404
    assert client.post(f"/api/work/shelved/{alpha}/restore").status_code == 200
    archived_key = next(p["key"] for p in listed["shelved"] if p["task"] == "beta")
    assert archived_key != beta  # an archived entry has its own key
    assert client.post(f"/api/work/shelved/{archived_key}/restore").status_code == 200
    assert [p["task"] for p in client.get("/api/work/projects").json()["projects"]] == ["alpha", "beta"]


def test_the_shelf_routes_need_the_terminal_token(api):
    client, service, work = api
    key = work_key(work, "alpha")
    assert client.post(f"/api/work/projects/{key}/shelve", json={"action": "pause"}).status_code == 401
    assert client.post(f"/api/work/shelved/{key}/restore").status_code == 401
    assert [p.task for p in service.scan().projects] == ["alpha", "beta"]


def test_a_terminal_that_outlived_its_close_keeps_the_project_from_being_put_away(world):
    """A close that timed out leaves the terminal running, marked closing, with the intent
    already cleared (codex review CR-80 round 1)."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    stuck = FakeSession("t1", "work:" + key, [], {}, str(work))
    stuck._closing = True
    service.manager.sessions.append(stuck)
    from agora.work.service import WorkProjectBusy
    with pytest.raises(WorkProjectBusy):
        run(service.shelve(key, "archive"))
    assert (work / "tasks" / "alpha" / "task.yaml").exists()


def test_an_open_that_scanned_before_a_pause_does_not_launch(world, monkeypatch):
    """The open's scan saw the project active; a pause landed before the open took the
    lock (codex review CR-80 round 1)."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    scanned = service._project

    def scan_then_paused(k):
        project = scanned(k)
        service._shelve_seq[k] = service._shelve_seq.get(k, 0) + 1
        return project
    monkeypatch.setattr(service, "_project", scan_then_paused)
    assert run(service.open(key))["result"] == "unavailable"
    assert service.manager.sessions == []


def test_an_open_that_scans_while_the_write_is_under_way_does_not_launch(world, monkeypatch):
    """The open read the count after the pause began and scanned the file before it was
    written (codex review CR-80 round 2): the count moves again once the write ends."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    during = {}
    real_pause = shelf.pause

    def pause_observed(source, task):
        during["seq"] = service._shelve_seq.get(key, 0)  # what such an open would have seen
        real_pause(source, task)
    monkeypatch.setattr(shelf, "pause", pause_observed)
    run(service.shelve(key, "pause"))
    assert service._shelve_seq[key] != during["seq"]


def test_restoring_an_archived_project_does_not_touch_a_same_named_paused_one(world):
    """``tasks/alpha`` paused and ``tasks/_archive/alpha`` both there (codex review CR-80
    round 3): each has its own key, and restoring one acts on that one."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    _task(work, "_archive/alpha", "task:\n  title: Old Alpha\n  status: archived\n")
    run(service.shelve(work_key(work, "alpha"), "pause"))
    shelved = service.scan().shelved
    keys = {(p.task, p.archived_dir): p.key for p in shelved}
    assert keys[("alpha", False)] != keys[("alpha", True)]
    # The archived one cannot come back while tasks/alpha exists: refused, nothing changed.
    with pytest.raises(ShelfRefused, match="already exists"):
        run(service.unshelve(keys[("alpha", True)]))
    assert "status: paused" in (work / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8")
    run(service.unshelve(keys[("alpha", False)]))
    assert "status: active" in (work / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8")
    assert (work / "tasks" / "_archive" / "alpha" / "task.yaml").exists()


def test_a_cancelled_request_keeps_the_lock_until_the_write_has_ended(world, monkeypatch):
    """The request goes away while the write runs in its thread (codex review CR-80
    round 3): the change still finishes before anything else takes the lock."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    started, release = threading.Event(), threading.Event()
    real_pause = shelf.pause

    def slow_pause(source, task):
        started.set()
        release.wait(5)
        real_pause(source, task)
    monkeypatch.setattr(shelf, "pause", slow_pause)

    async def scenario():
        request = asyncio.create_task(service.shelve(key, "pause"))
        await asyncio.to_thread(started.wait, 5)
        request.cancel()
        await asyncio.sleep(0)
        assert service._lock.locked()  # the write is still running: the lock is held
        release.set()
        await asyncio.gather(*service._shelf_tasks)
        assert not service._lock.locked()

    run(scenario())
    from agora.work.service import WorkProjectNotFound
    with pytest.raises(WorkProjectNotFound):  # paused: no longer opened as active
        run(service.open(key))
    assert service.manager.sessions == []
    assert "status: paused" in (work / "tasks" / "alpha" / "task.yaml").read_text(encoding="utf-8")


def test_put_away_names_only_what_was_paused_or_archived_here(world):
    """The banner drops a restore line for these keys only: a same-named archive that was
    not put away from here is not among them."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    _task(work, "_archive/beta", "task:\n  status: archived\n")
    alpha = work_key(work, "alpha")
    run(service.shelve(alpha, "archive"))
    assert service.view()["put_away"] == [alpha]
    archived_key = next(p.key for p in service.scan().shelved if p.task == "alpha")
    run(service.unshelve(archived_key))
    assert service.view()["put_away"] == []
