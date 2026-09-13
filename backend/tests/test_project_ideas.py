"""The project conversation after the charter is adopted (CR-63).

The owner says something; one analyst call decides what it is — work now, a second
version, or a remark — and proposes a card with a place in the development order.
Proposing writes nothing. Placing writes without asking the agent again, and reports
where the card *landed* rather than repeating the position that was asked for.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.projects import kickoff
from agora.projects.kickoff import KickoffError, parse_idea
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore


IDEA_TASK = json.dumps({
    "verdict": "task",
    "echo": "把日文来信翻成中文，并标注语气",
    "why": "和 charter 同一条线，但不在第一版里",
    "task": {"title": "反向翻译并标注语气", "description": "输入日文邮件，输出中文译文加一行语气判断。验收：20 封里认可 18 封。", "kind": "feature"},
    "position": 2,
})

IDEA_NOTE = json.dumps({
    "verdict": "note", "echo": "你觉得敬语等级的说明不够清楚", "why": "这是一条意见，不是一件要做的事",
    "task": None, "position": None,
})


# --- parsing ------------------------------------------------------------------------------

def test_parse_idea_takes_the_object_out_of_prose():
    out = parse_idea("好的：\n```json\n" + IDEA_TASK + "\n```", order_length=3)
    assert out["verdict"] == "task"
    assert out["task"]["kind"] == "feature"
    assert out["position"] == 2
    assert out["echo"].startswith("把日文来信")


def test_parse_idea_accepts_a_verdict_with_no_card():
    out = parse_idea(IDEA_NOTE, order_length=3)
    assert (out["verdict"], out["task"], out["position"]) == ("note", None, None)


@pytest.mark.parametrize("bad", [
    # not a verdict this build knows
    {"verdict": "maybe", "echo": "e"},
    # a verdict of "task" with no card, or no place for it, is half a proposal
    {"verdict": "task", "echo": "e", "task": None, "position": 1},
    {"verdict": "task", "echo": "e", "task": {"title": "t", "description": "d", "kind": "feature"}, "position": None},
    # a kind outside the contract is refused, not filed as "custom"
    {"verdict": "task", "echo": "e", "task": {"title": "t", "description": "d", "kind": "mock-up"}, "position": 1},
    # a card with no acceptance text at all
    {"verdict": "task", "echo": "e", "task": {"title": "t", "description": "", "kind": "feature"}, "position": 1},
    # no restatement: the person could not correct what was never said back
    {"verdict": "note", "echo": "", "task": None, "position": None},
    # a verdict that says "not work" while proposing work contradicts itself; the card
    # must not be dropped silently behind the remark
    {"verdict": "note", "echo": "e", "task": {"title": "t", "description": "d", "kind": "feature"}, "position": None},
    {"verdict": "later", "echo": "e", "task": None, "position": 2},
])
def test_parse_idea_fails_closed(bad):
    with pytest.raises(KickoffError):
        parse_idea(json.dumps(bad), order_length=3)


@pytest.mark.parametrize("position", [0, -1, 5, True])
def test_parse_idea_refuses_a_position_outside_this_order(position):
    # 1..len+1 is every gap plus the end; outside it the agent is not naming a place in
    # *this* order, and clamping would move the card somewhere it never proposed.
    with pytest.raises(KickoffError, match="position"):
        parse_idea(json.dumps({
            "verdict": "task", "echo": "e",
            "task": {"title": "t", "description": "d", "kind": "feature"}, "position": position,
        }), order_length=3)


def test_parse_idea_allows_the_end_of_the_order():
    out = parse_idea(json.dumps({
        "verdict": "task", "echo": "e",
        "task": {"title": "t", "description": "d", "kind": "feature"}, "position": 4,
    }), order_length=3)
    assert out["position"] == 4


def test_parse_idea_refuses_a_restatement_longer_than_it_shows():
    with pytest.raises(KickoffError, match="echo"):
        parse_idea(json.dumps({"verdict": "note", "echo": "x" * (kickoff.IDEA_LINE_CHARS + 1)}), order_length=1)


# --- the routes ---------------------------------------------------------------------------

def _registry(tmp_path):
    project_id = f"idea{uuid4().hex[:8]}"
    root = tmp_path / project_id
    root.mkdir()
    registry = ProjectRegistry({
        "projects": {
            "registry_path": str(tmp_path / ".agora" / "projects.yaml"),
            "default": project_id,
            "projects": {project_id: {"name": "NichiMate", "root": str(root), "workspaces": {}}},
        }
    })
    registry.set_projects_base(tmp_path / "base")
    return registry, project_id


def _client(tmp_path, monkeypatch, *, answer: str = IDEA_TASK, charter: bool = True):
    registry, project_id = _registry(tmp_path)
    if charter:
        registry.set_charter(project_id, goal="做一个日语职场邮件助手", non_goals=["不做账号"], success_criteria=["连用五天不用人改"])
    tasks = TaskStore(tmp_path / "ideas-tasks.db")
    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: answer)
    return TestClient(app), registry, tasks, project_id


def _plan(tasks: TaskStore, project_id: str, title: str, priority: int) -> str:
    created = tasks.create(
        CreateTaskRequest(project_id=project_id, title=title, description="d", kind="feature", priority=priority),
        state=TaskState.PLANNED,
    )
    return created.task_id


def test_an_idea_is_judged_against_the_charter_and_writes_nothing(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        _plan(tasks, pid, "画 mockup", 100)
        _plan(tasks, pid, "搭骨架", 96)
        got = client.post(f"/api/projects/{pid}/ideas", json={"idea": "还想要反向翻译"})
        assert got.status_code == 200
        body = got.json()
        assert body["verdict"] == "task"
        assert body["task"]["title"] == "反向翻译并标注语气"
        assert body["position"] == 2
        # The order the position is expressed in comes back with it.
        assert [t["title"] for t in body["order"]] == ["画 mockup", "搭骨架"]
        assert body["order_truncated"] is False
        # Proposing is not accepting: the board is untouched.
        assert len(tasks.list(project_id=pid, limit=10)) == 2
    finally:
        app.dependency_overrides.clear()


def test_the_conversation_refuses_to_propose_work_for_a_project_with_no_goal(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch, charter=False)
    try:
        refused = client.post(f"/api/projects/{pid}/ideas", json={"idea": "随便做点什么"})
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "project_has_no_charter"
        placed = client.post(f"/api/projects/{pid}/ideas/place", json={
            "task": {"title": "t", "description": "d", "kind": "feature"}, "position": 1,
        })
        assert placed.status_code == 409
        assert len(tasks.list(project_id=pid, limit=10)) == 0
    finally:
        app.dependency_overrides.clear()


def test_an_unusable_agent_answer_is_a_bad_gateway_not_a_crash(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch, answer="I would rather not")
    try:
        assert client.post(f"/api/projects/{pid}/ideas", json={"idea": "x"}).status_code == 502
    finally:
        app.dependency_overrides.clear()


def test_a_blank_idea_is_refused_before_the_agent_is_spent(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        assert client.post(f"/api/projects/{pid}/ideas", json={"idea": "   "}).status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("position, expected", [
    (1, ["新卡", "画 mockup", "搭骨架", "写清单"]),
    (2, ["画 mockup", "新卡", "搭骨架", "写清单"]),
    (3, ["画 mockup", "搭骨架", "新卡", "写清单"]),
    (4, ["画 mockup", "搭骨架", "写清单", "新卡"]),
])
def test_placing_a_card_puts_it_where_the_person_said(tmp_path, monkeypatch, position, expected):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        _plan(tasks, pid, "画 mockup", 100)
        _plan(tasks, pid, "搭骨架", 96)
        _plan(tasks, pid, "写清单", 92)
        body = client.post(f"/api/projects/{pid}/ideas/place", json={
            "task": {"title": "新卡", "description": "d", "kind": "feature"}, "position": position,
        }).json()
        assert [t["title"] for t in body["order"]] == expected
        # Where it landed, read back from the board — not the position that was asked for.
        assert body["landed_at"] == position
        assert body["asked_position"] == position
        # Planned, so it can be dispatched the moment it appears.
        assert body["state"] == "planned"
        assert tasks.get(body["task_id"]).state is TaskState.PLANNED
    finally:
        app.dependency_overrides.clear()


def test_placing_the_same_card_twice_creates_one_task(tmp_path, monkeypatch):
    # The failure this closes: the task is created, the answer is lost on the way back,
    # the panel shows an error, the person clicks again — and a second planned task
    # appears on the board with no way to tell which one is theirs.
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        _plan(tasks, pid, "画 mockup", 100)
        card = {"title": "新卡", "description": "d", "kind": "feature"}
        first = client.post(f"/api/projects/{pid}/ideas/place", json={"task": card, "position": 1}).json()
        again = client.post(f"/api/projects/{pid}/ideas/place", json={"task": card, "position": 2}).json()
        assert again["task_id"] == first["task_id"]
        assert len([t for t in tasks.list(project_id=pid, limit=10) if t.title == "新卡"]) == 1
        # And the answer still describes where it actually is, not where the retry asked.
        assert again["landed_at"] == 1
        # A different card is a different task.
        other = client.post(f"/api/projects/{pid}/ideas/place", json={
            "task": {**card, "title": "另一张"}, "position": 1,
        }).json()
        assert other["task_id"] != first["task_id"]
    finally:
        app.dependency_overrides.clear()


def test_two_identical_places_arriving_together_still_create_one_task(tmp_path, monkeypatch):
    # The sequential retry above is not this: two requests can both read "not there"
    # before either INSERT commits, and the store's transaction does not cover the scan.
    # The project lock does. Driven through the real routes, with the scan made slow so
    # the window is wide rather than hoped for.
    import threading
    import time

    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    real_list = tasks.list

    def slow_list(*args, **kwargs):
        rows = real_list(*args, **kwargs)
        time.sleep(0.05)
        return rows

    monkeypatch.setattr(tasks, "list", slow_list)
    try:
        card = {"title": "同一张卡", "description": "d", "kind": "feature"}
        answers: list[Any] = []

        def place():
            answers.append(client.post(f"/api/projects/{pid}/ideas/place", json={"task": card, "position": 1}))

        threads = [threading.Thread(target=place) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert [a.status_code for a in answers] == [200, 200]
        ids = {a.json()["task_id"] for a in answers}
        assert len(ids) == 1
        assert len([t for t in tasks.list(project_id=pid, limit=10) if t.title == "同一张卡"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_the_order_is_filtered_before_it_is_paged(tmp_path, monkeypatch):
    # The failure this closes: the live filter used to run on the page, so a project whose
    # highest-priority rows are all finished reported an empty development order — and the
    # agent was then shown, and the card placed into, an order that is not the board's.
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        for index in range(kickoff.ORDER_SHOWN + 1):
            done = _plan(tasks, pid, f"已完成 {index}", 100)
            for state in (TaskState.RUNNING, TaskState.REVIEW, TaskState.VERIFIED, TaskState.DONE):
                tasks.transition(done, state, actor="user")
        _plan(tasks, pid, "唯一在做的", 0)
        body = client.post(f"/api/projects/{pid}/ideas", json={"idea": "x"}).json()
        assert [t["title"] for t in body["order"]] == ["唯一在做的"]
        assert body["order_truncated"] is False
    finally:
        app.dependency_overrides.clear()


def test_a_position_past_the_end_is_the_end_not_an_error(tmp_path, monkeypatch):
    # The person clicked "put it last" on the order they were shown; a task created
    # meanwhile must not turn that click into a refusal.
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        _plan(tasks, pid, "画 mockup", 100)
        body = client.post(f"/api/projects/{pid}/ideas/place", json={
            "task": {"title": "新卡", "description": "d", "kind": "feature"}, "position": 9,
        }).json()
        assert [t["title"] for t in body["order"]] == ["画 mockup", "新卡"]
        assert (body["asked_position"], body["landed_at"]) == (9, 2)
    finally:
        app.dependency_overrides.clear()


def test_placing_into_the_first_position_of_an_empty_board_works(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        body = client.post(f"/api/projects/{pid}/ideas/place", json={
            "task": {"title": "第一张", "description": "d", "kind": "mockup"}, "position": 1,
        }).json()
        assert body["landed_at"] == 1
        assert [t["title"] for t in body["order"]] == ["第一张"]
    finally:
        app.dependency_overrides.clear()


def test_finished_tasks_are_not_part_of_the_order_a_card_is_placed_into(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        done = _plan(tasks, pid, "已完成", 100)
        tasks.transition(done, TaskState.RUNNING, actor="user")
        tasks.transition(done, TaskState.REVIEW, actor="user")
        tasks.transition(done, TaskState.VERIFIED, actor="user")
        tasks.transition(done, TaskState.DONE, actor="user")
        _plan(tasks, pid, "在做的", 96)
        body = client.post(f"/api/projects/{pid}/ideas", json={"idea": "x"}).json()
        assert [t["title"] for t in body["order"]] == ["在做的"]
    finally:
        app.dependency_overrides.clear()


def test_a_task_may_not_be_created_past_planned(tmp_path):
    # The chain is the way in for ordinary work; only the kickoff and this conversation
    # create a task already planned, and nothing creates one further along than that.
    tasks = TaskStore(tmp_path / "states.db")
    request = CreateTaskRequest(project_id="p", title="t")
    for state in (TaskState.RUNNING, TaskState.DONE, TaskState.REQUIREMENTS):
        with pytest.raises(ValueError):
            tasks.create(request, state=state)
    # And the state is not on the wire model at all: the public POST /api/tasks takes
    # this body, and a state field on it would let any client skip the lifecycle.
    assert "state" not in CreateTaskRequest.model_fields
    assert tasks.create(request).state is TaskState.BACKLOG


def test_the_public_create_route_cannot_ask_for_a_dispatchable_task(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        created = client.post("/api/tasks", json={
            "project_id": pid, "title": "偷偷派活", "description": "d", "kind": "feature",
            "state": "planned",
        })
        assert created.status_code in {200, 201}, created.text
        # The field is ignored, not honoured: the task starts where the lifecycle starts.
        assert created.json()["state"] == "backlog"
    finally:
        app.dependency_overrides.clear()
