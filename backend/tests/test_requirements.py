from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agora.api.app import app
from agora.requirements.models import (
    CreateSpecRequest,
    ReviewChangeRequest,
    SubmitChangeRequest,
    UpdateSpecRequest,
)
from agora.requirements.router import get_requirement_store
from agora.requirements.store import RequirementConflictError, RequirementStore
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.router import get_task_store
from agora.tasks.store import InvalidTransitionError, TaskStore


def _task_store(tmp_path) -> tuple[TaskStore, str]:
    store = TaskStore(tmp_path / "agora.db")
    task = store.create(CreateTaskRequest(project_id="alpha", title="Tenant-aware cache"))
    store.transition(task.task_id, TaskState.REQUIREMENTS, actor="kiro", expected_version=1)
    return store, task.task_id


def _spec_request(*, unresolved: bool = False) -> CreateSpecRequest:
    return CreateSpecRequest(
        title="Tenant-aware semantic cache requirements",
        summary="Cache results without crossing tenant boundaries.",
        functional=[
            {
                "requirement_id": "FR-1",
                "statement": "Cache keys must include the tenant identity.",
            }
        ],
        non_functional=[
            {
                "requirement_id": "NFR-1",
                "statement": "A cache hit must complete within 50 ms at p95.",
            }
        ],
        constraints=["Existing API responses remain backward compatible."],
        acceptance_scenarios=[
            {
                "scenario_id": "AC-1",
                "requirement_ids": ["FR-1"],
                "given": "two tenants submit the same query",
                "when": "both requests use the semantic cache",
                "then": "each tenant only receives its own cached result",
            }
        ],
        out_of_scope=["Cross-region cache replication"],
        glossary={"tenant": "An isolated customer security boundary."},
        assumptions=["Tenant identity is available before cache lookup."],
        open_questions=[
            {
                "question_id": "Q-1",
                "question": "Which cache backend is used?",
                "resolution": None if unresolved else "Redis for the MVP.",
            }
        ],
        links=[
            {
                "requirement_id": "FR-1",
                "target_type": "test",
                "target_id": "tests/test_tenant_cache.py",
            }
        ],
        created_by="kiro",
    )


def test_requirement_references_are_validated():
    payload = _spec_request().model_dump(mode="json")
    payload["links"][0]["requirement_id"] = "MISSING"

    with pytest.raises(ValidationError, match="unknown requirement references"):
        CreateSpecRequest.model_validate(payload)


def test_spec_approval_gates_task_design_transition(tmp_path):
    tasks, task_id = _task_store(tmp_path)
    requirements = RequirementStore(tasks)
    draft = requirements.create(task_id, _spec_request())

    assert draft.version == 1
    assert draft.state.value == "draft"
    with pytest.raises(InvalidTransitionError, match="approved spec"):
        tasks.transition(task_id, TaskState.DESIGN, actor="claude", expected_version=2)

    approved = requirements.approve(
        draft.spec_id,
        actor="user",
        expected_revision=1,
        reason="Reviewed with product owner",
    )
    assert approved.state.value == "approved"
    assert approved.approved_by == "user"

    designed = tasks.transition(task_id, TaskState.DESIGN, actor="claude", expected_version=2)
    assert designed.state == TaskState.DESIGN
    event_types = [event.event_type for event in tasks.events(task_id)]
    assert event_types == [
        "task_created",
        "state_changed",
        "spec.created",
        "spec.approved",
        "state_changed",
    ]


def test_every_path_into_design_requires_approved_spec(tmp_path):
    tasks, task_id = _task_store(tmp_path)
    blocked = tasks.transition(task_id, TaskState.BLOCKED, actor="user", expected_version=2)

    assert blocked.state == TaskState.BLOCKED
    with pytest.raises(InvalidTransitionError, match="approved spec"):
        tasks.transition(task_id, TaskState.DESIGN, actor="claude", expected_version=3)


def test_unresolved_questions_block_approval(tmp_path):
    tasks, task_id = _task_store(tmp_path)
    requirements = RequirementStore(tasks)
    draft = requirements.create(task_id, _spec_request(unresolved=True))

    with pytest.raises(RequirementConflictError, match="Q-1"):
        requirements.approve(draft.spec_id, actor="user", expected_revision=1)

    updated = requirements.update(
        draft.spec_id,
        UpdateSpecRequest(
            expected_revision=1,
            actor="kiro",
            open_questions=[
                {
                    "question_id": "Q-1",
                    "question": "Which cache backend is used?",
                    "resolution": "Redis for the MVP.",
                }
            ],
        ),
    )
    assert updated.open_questions[0].resolution == "Redis for the MVP."
    assert updated.revision == 2
    with pytest.raises(RequirementConflictError, match="Expected revision 1"):
        requirements.update(
            updated.spec_id,
            UpdateSpecRequest(expected_revision=1, actor="claude", summary="Stale edit"),
        )
    assert requirements.approve(
        updated.spec_id, actor="user", expected_revision=2
    ).state.value == "approved"


def test_change_request_creates_new_draft_version_atomically(tmp_path):
    tasks, task_id = _task_store(tmp_path)
    requirements = RequirementStore(tasks)
    v1 = requirements.create(task_id, _spec_request())
    requirements.approve(v1.spec_id, actor="user", expected_revision=1)
    tasks.transition(task_id, TaskState.DESIGN, actor="claude", expected_version=2)

    cr = requirements.submit_change_request(
        v1.spec_id,
        SubmitChangeRequest(
            title="Add cache invalidation requirement",
            impact_notes="Design and integration tests must change.",
            affected_targets=["design/cache.md", "tests/test_tenant_cache.py"],
            submitted_by="user",
        ),
    )
    second_cr = requirements.submit_change_request(
        v1.spec_id,
        SubmitChangeRequest(title="Alternative wording", submitted_by="user"),
    )
    with pytest.raises(RequirementConflictError, match="Return task to requirements"):
        requirements.review_change_request(
            cr.cr_id, ReviewChangeRequest(action="accept", actor="user")
        )

    tasks.transition(task_id, TaskState.REQUIREMENTS, actor="user", expected_version=3)
    accepted = requirements.review_change_request(
        cr.cr_id,
        ReviewChangeRequest(action="accept", actor="user", reason="Approved scope change"),
    )
    assert accepted.state.value == "accepted"
    assert accepted.resulting_spec_id

    versions = requirements.list_for_task(task_id)
    assert [(item.version, item.state.value) for item in versions] == [
        (2, "draft"),
        (1, "superseded"),
    ]
    assert versions[0].functional == versions[1].functional
    declined = requirements.review_change_request(
        second_cr.cr_id,
        ReviewChangeRequest(action="decline", actor="user", reason="Superseded by accepted CR"),
    )
    assert declined.state.value == "declined"


def test_requirements_api_create_approve_and_change_request(tmp_path):
    tasks, task_id = _task_store(tmp_path)
    requirements = RequirementStore(tasks)
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_requirement_store] = lambda: requirements
    client = TestClient(app)
    try:
        created = client.post(
            f"/api/tasks/{task_id}/specs",
            json=_spec_request().model_dump(mode="json"),
        )
        assert created.status_code == 201
        spec = created.json()
        assert spec["state"] == "draft"

        current = client.get(f"/api/tasks/{task_id}/specs/current")
        assert current.status_code == 200
        assert current.json()["spec_id"] == spec["spec_id"]

        invalid_update = client.patch(
            f"/api/specs/{spec['spec_id']}",
            json={
                "expected_revision": 1,
                "actor": "kiro",
                "links": [
                    {
                        "requirement_id": "MISSING",
                        "target_type": "test",
                        "target_id": "tests/missing.py",
                    }
                ],
            },
        )
        assert invalid_update.status_code == 422

        approved = client.post(
            f"/api/specs/{spec['spec_id']}/approve",
            json={"actor": "user", "expected_revision": 1, "reason": "Approved"},
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"

        cr = client.post(
            f"/api/specs/{spec['spec_id']}/change-requests",
            json={
                "title": "Clarify cache invalidation",
                "impact_notes": "Update design and tests",
                "submitted_by": "user",
            },
        )
        assert cr.status_code == 201
        assert cr.json()["state"] == "open"

        listed = client.get(f"/api/specs/{spec['spec_id']}/change-requests")
        assert listed.status_code == 200
        assert [item["cr_id"] for item in listed.json()] == [cr.json()["cr_id"]]

        missing = client.get("/api/specs/missing")
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
