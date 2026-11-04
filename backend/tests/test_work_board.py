"""Todo / doing and the order of the work projects (workspace CR-81/82): Agora's own
board, never written into a task record."""
from __future__ import annotations

import pytest

from agora.terminal.auth import load_or_create_token
from agora.work.projects import work_key
from agora.work.service import WorkProjectBusy, WorkProjectNotFound

from tests.test_work import _service, api, run, world  # noqa: F401  (fixtures)


def _stages(service) -> dict[str, str]:
    return {p["task"]: p["stage"] for p in service.view()["projects"]}


def _launched(service, work, task: str) -> str:
    """A launch left by a previous server run, meant to be open, its shell gone."""
    key = work_key(work, task)
    service.store.begin_launch(key=key, source=str(work), task=task, workspace="claude",
                               launch_id="0" * 32, launch_mode="fresh", expect_session=None)
    service.store.set_shell(key, "0" * 32, 0, None)
    return key


def test_a_project_never_placed_is_todo_unless_it_was_meant_to_be_open(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    _launched(service, work, "beta")
    # What was open before the board existed stays on the work page.
    assert _stages(service) == {"alpha": "todo", "beta": "doing"}


def test_what_was_open_before_the_board_stays_doing_after_its_terminal_is_closed(world):
    """The stage is written down when the store starts, not inferred from want_open, which
    the close clears (codex review CR-81 round 1)."""
    tmp_path, work, claude = world
    first = _service(tmp_path, work, claude)
    key = _launched(first, work, "alpha")
    service = _service(tmp_path, work, claude)  # the next server run, same database
    run(service.restore())
    run(service.close(key))
    assert _stages(service)["alpha"] == "doing"


def test_opening_a_project_makes_it_doing_and_todo_waits_for_its_terminal_to_close(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service.open(key))
    assert _stages(service)["alpha"] == "doing"
    with pytest.raises(WorkProjectBusy):
        run(service.set_stage(key, "todo"))
    run(service.close(key))
    run(service.set_stage(key, "todo"))
    assert _stages(service)["alpha"] == "todo"


def test_the_board_does_not_touch_the_task_record(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    record = work / "tasks" / "alpha" / "task.yaml"
    before = record.read_bytes()
    key = work_key(work, "alpha")
    run(service.set_stage(key, "doing"))
    run(service.set_order([key]))
    assert record.read_bytes() == before


def test_a_restart_brings_back_only_what_is_doing(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    alpha, beta = _launched(service, work, "alpha"), _launched(service, work, "beta")
    # Not reachable through the routes (todo is refused while meant to be open), but a
    # board row that says todo is what decides.
    service.store.set_stage(beta, "todo")
    results = run(service.restore())
    assert results[alpha]["result"] != "closed"
    assert results[beta]["result"] == "closed"
    assert [s.project_id for s in service.manager.sessions] == ["work:" + alpha]


def test_the_order_is_kept_and_a_project_placed_by_it_keeps_the_stage_it_was_shown_under(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    alpha, beta = work_key(work, "alpha"), _launched(service, work, "beta")
    run(service.set_order([beta, alpha]))
    view = {p["task"]: (p["stage"], p["position"]) for p in service.view()["projects"]}
    assert view == {"beta": ("doing", 0), "alpha": ("todo", 1)}
    run(service.set_stage(alpha, "doing"))
    assert service.store.board()[alpha] == ("doing", 1)  # a stage change keeps the place


def test_an_order_naming_an_unlisted_or_repeated_project_is_refused(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    alpha = work_key(work, "alpha")
    with pytest.raises(WorkProjectNotFound):
        run(service.set_order([alpha, "nope"]))
    with pytest.raises(ValueError):
        run(service.set_order([alpha, alpha]))
    assert service.store.board() == {}


def test_stage_and_order_through_the_api(api, tmp_path):
    client, service, work = api
    alpha, beta = work_key(work, "alpha"), work_key(work, "beta")
    assert client.post(f"/api/work/projects/{alpha}/stage", json={"stage": "doing"}).status_code == 401
    assert client.put("/api/work/order", json={"keys": [beta, alpha]}).status_code == 401
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    assert client.post(f"/api/work/projects/{alpha}/stage", json={"stage": "doing"}).status_code == 200
    assert client.post(f"/api/work/projects/{alpha}/stage", json={"stage": "done"}).status_code == 422
    assert client.post("/api/work/projects/nope/stage", json={"stage": "todo"}).status_code == 404
    assert client.put("/api/work/order", json={"keys": [beta, alpha]}).status_code == 200
    assert client.put("/api/work/order", json={"keys": [beta, beta]}).status_code == 422
    assert client.put("/api/work/order", json={"keys": ["nope"]}).status_code == 404
    listed = {p["task"]: (p["stage"], p["position"]) for p in client.get("/api/work/projects").json()["projects"]}
    assert listed == {"alpha": ("doing", 1), "beta": ("todo", 0)}
    client.post(f"/api/work/projects/{alpha}/open")
    reply = client.post(f"/api/work/projects/{alpha}/stage", json={"stage": "todo"})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "terminal_open"
