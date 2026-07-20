from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from agora.control_plane.models import ArtifactInventory
from agora.control_plane.schema import initialize_control_plane_schema
from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)
from agora.protocol.invalidation import ArtifactChange
from agora.protocol.models import (
    Approval,
    ApprovalStatus,
    Artifact,
    Evidence,
    GateRequirement,
)
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
COMMIT_1 = "1" * 40
COMMIT_2 = "2" * 40
REPOSITORY = "agora-repository"
REF = "refs/heads/main"


def _stores(
    tmp_path,
    *,
    project_id: str = "agora",
) -> tuple[TaskStore, ControlPlaneStore, str]:
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id=project_id,
            title="Persist the v2 registry",
            kind="architecture",
        )
    )
    return tasks, ControlPlaneStore(tasks), task.task_id


def _requirement(
    requirement_id: str = "review-approved",
    *,
    commit_sha: str = COMMIT_1,
) -> GateRequirement:
    return GateRequirement(
        requirement_id=requirement_id,
        title="Independent review is approved",
        repository_id=REPOSITORY,
        ref=REF,
        commit_sha=commit_sha,
        evidence_kind="independent-review",
        priority=10,
        failure_action="Run independent review and record passing Evidence.",
    )


def _artifact(
    task_id: str,
    *,
    artifact_id: str = "artifact-requirements",
    path: str = "docs/requirements.md",
    sha256: str = "a" * 64,
    commit_sha: str = COMMIT_1,
    project_id: str = "agora",
) -> Artifact:
    return Artifact.model_validate(
        {
            "schema_version": "1.0",
            "artifact_id": artifact_id,
            "project_id": project_id,
            "task_id": task_id,
            "stage_key": "requirements",
            "producer": {
                "runtime": "codex",
                "run_id": "run-requirements",
                "stage_key": "requirements",
            },
            "kind": "requirements",
            "storage": "referenced",
            "version": 1,
            "sha256": sha256,
            "media_type": "text/markdown",
            "content": None,
            "location": {
                "repository_id": REPOSITORY,
                "ref": REF,
                "commit_sha": commit_sha,
                "path": path,
            },
            "created_at": NOW.isoformat(),
        }
    )


def _evidence(
    task_id: str,
    *,
    evidence_id: str = "evidence-review-pass",
    status: str = "passed",
    commit_sha: str = COMMIT_1,
    project_id: str = "agora",
) -> Evidence:
    return Evidence.model_validate(
        {
            "schema_version": "1.0",
            "evidence_id": evidence_id,
            "project_id": project_id,
            "task_id": task_id,
            "stage_key": "requirements",
            "producer": {
                "runtime": "claude",
                "run_id": "run-review",
                "stage_key": "requirements",
            },
            "repository_id": REPOSITORY,
            "ref": REF,
            "commit_sha": commit_sha,
            "requirement_id": "review-approved",
            "kind": "independent-review",
            "status": status,
            "artifact_versions": [],
            "summary": f"Review result: {status}.",
            "observed_at": NOW.isoformat(),
            "details": {},
        }
    )


def _approval(
    task_id: str,
    *,
    approval_id: str = "approval-requirements",
    path: str = "docs/requirements.md",
    sha256: str = "a" * 64,
    project_id: str = "agora",
) -> Approval:
    return Approval.model_validate(
        {
            "schema_version": "1.0",
            "approval_id": approval_id,
            "project_id": project_id,
            "task_id": task_id,
            "stage_key": "requirements",
            "gate_key": "requirements-gate",
            "repository_id": REPOSITORY,
            "ref": REF,
            "commit_sha": COMMIT_1,
            "artifact_versions": [
                {
                    "repository_id": REPOSITORY,
                    "ref": REF,
                    "commit_sha": COMMIT_1,
                    "path": path,
                    "sha256": sha256,
                }
            ],
            "status": "active",
            "approved_by": "user",
            "approved_at": NOW.isoformat(),
            "stale_reason": None,
        }
    )


def _configure_requirements_gate(
    store: ControlPlaneStore,
    task_id: str,
    *,
    stage_status: StageStatus = StageStatus.READY,
) -> None:
    store.ensure_stage(
        task_id=task_id,
        stage_key="requirements",
        gate_key="requirements-gate",
        status=stage_status,
    )
    store.configure_gate(
        task_id=task_id,
        gate_key="requirements-gate",
        stage_key="requirements",
        requirements=[_requirement()],
    )


def _pass_requirements_gate(store: ControlPlaneStore, task_id: str) -> None:
    evidence = _evidence(task_id)
    store.register_evidence(evidence)
    gate = store.get_gate(task_id, "requirements-gate")
    assert gate is not None
    gate = store.set_active_evidence(
        task_id=task_id,
        gate_key="requirements-gate",
        evidence_ids=[evidence.evidence_id],
        expected_gate_version=gate.version,
        actor="system",
        operation_key="requirements-evidence-v1",
    )
    gate = store.evaluate(
        task_id=task_id,
        gate_key="requirements-gate",
        expected_gate_version=gate.version,
        actor="system",
        operation_key="requirements-evaluate-v1",
    )
    assert gate.status == GateStatus.PASSED


def test_schema_initialization_is_additive_for_a_legacy_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    db = sqlite3.connect(db_path)
    try:
        db.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                legacy_payload TEXT NOT NULL
            );
            CREATE TABLE legacy_runtime_state (
                state_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            INSERT INTO tasks VALUES ('task_legacy', 'agora', 'unchanged');
            INSERT INTO legacy_runtime_state VALUES ('state_1', 'keep-me');
            """
        )
        before_tasks_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()[0]
        before_rows = db.execute("SELECT * FROM tasks").fetchall()
        before_legacy = db.execute("SELECT * FROM legacy_runtime_state").fetchall()

        initialize_control_plane_schema(db)
        db.commit()

        assert db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()[0] == before_tasks_sql
        assert db.execute("SELECT * FROM tasks").fetchall() == before_rows
        assert db.execute("SELECT * FROM legacy_runtime_state").fetchall() == before_legacy
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'control_%'"
        ).fetchone()[0] >= 5
    finally:
        db.close()


def test_stage_gate_configuration_is_idempotent_and_survives_restart(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)

    first = store.get_gate(task_id, "requirements-gate")
    assert first is not None
    assert first.status == GateStatus.PENDING
    assert first.version == 1
    assert [item.requirement_id for item in first.requirements] == ["review-approved"]

    second = store.configure_gate(
        task_id=task_id,
        gate_key="requirements-gate",
        stage_key="requirements",
        requirements=[_requirement()],
    )
    assert second == first

    restarted = ControlPlaneStore(TaskStore(tasks.db_path))
    assert restarted.get_gate(task_id, "requirements-gate") == first
    assert restarted.get_stage(task_id, "requirements").status == StageStatus.READY

    with pytest.raises(ControlPlaneConflictError):
        restarted.configure_gate(
            task_id=task_id,
            gate_key="requirements-gate",
            stage_key="requirements",
            requirements=[_requirement(commit_sha=COMMIT_2)],
        )


def test_gate_configuration_rolls_back_stage_creation_on_gate_failure(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            """
            CREATE TRIGGER fail_gate_configuration
            BEFORE INSERT ON control_gates
            BEGIN
                SELECT RAISE(ABORT, 'forced gate failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced gate failure"):
        store.configure_gate(
            task_id=task_id,
            gate_key="requirements-gate",
            stage_key="requirements",
            requirements=[_requirement()],
        )

    assert store.get_stage(task_id, "requirements") is None
    assert store.get_gate(task_id, "requirements-gate") is None
    assert all(
        event.event_type not in {"stage.created", "gate.configured"}
        for event in store.events(task_id)
    )


def test_immutable_registration_is_idempotent_and_conflicting_duplicates_fail(tmp_path):
    _, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)

    artifact = _artifact(task_id)
    assert store.register_artifact(artifact).created is True
    assert store.register_artifact(artifact).created is False
    assert store.get_artifact(artifact.artifact_id, 1) == artifact

    changed = _artifact(task_id, sha256="b" * 64)
    with pytest.raises(ControlPlaneConflictError):
        store.register_artifact(changed)

    evidence = _evidence(task_id)
    assert store.register_evidence(evidence).created is True
    assert store.register_evidence(evidence).created is False
    assert store.get_evidence(evidence.evidence_id) == evidence

    changed_evidence = _evidence(task_id, status="failed_product")
    with pytest.raises(ControlPlaneConflictError):
        store.register_evidence(changed_evidence)

    approval = _approval(task_id)
    assert store.register_approval(approval).created is True
    assert store.register_approval(approval).created is False
    assert store.get_approval(approval.approval_id) == approval

    event_types = [event.event_type for event in store.events(task_id)]
    assert event_types.count("artifact.registered") == 1
    assert event_types.count("evidence.registered") == 1
    assert event_types.count("approval.registered") == 1


def test_approval_requires_a_registered_artifact_from_the_same_task(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)
    approval = _approval(task_id)

    with pytest.raises(ControlPlaneValidationError, match="not registered"):
        store.register_approval(approval)

    other = tasks.create(
        CreateTaskRequest(project_id="agora", title="Other task", kind="architecture")
    )
    store.ensure_stage(
        task_id=other.task_id,
        stage_key="requirements",
        gate_key="other-gate",
    )
    store.register_artifact(_artifact(other.task_id))

    with pytest.raises(ControlPlaneValidationError, match="not registered"):
        store.register_approval(approval)


def test_concurrent_evidence_registration_creates_one_row_and_event(tmp_path):
    _, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)
    evidence = _evidence(task_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: store.register_evidence(evidence), range(2)))

    assert sorted(item.created for item in receipts) == [False, True]
    assert [event.event_type for event in store.events(task_id)].count(
        "evidence.registered"
    ) == 1


def test_gate_evidence_and_evaluation_operations_are_restart_idempotent(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)
    passing = _evidence(task_id)
    store.register_evidence(passing)

    selected = store.set_active_evidence(
        task_id=task_id,
        gate_key="requirements-gate",
        evidence_ids=[passing.evidence_id],
        expected_gate_version=1,
        actor="system",
        operation_key="select-pass",
    )
    assert selected.version == 2
    assert selected.active_evidence_ids == [passing.evidence_id]

    passed = store.evaluate(
        task_id=task_id,
        gate_key="requirements-gate",
        expected_gate_version=2,
        actor="system",
        operation_key="evaluate-pass",
    )
    assert passed.status == GateStatus.PASSED
    assert passed.version == 4

    failing = _evidence(
        task_id,
        evidence_id="evidence-review-fail",
        status="failed_product",
    )
    store.register_evidence(failing)
    with pytest.raises(ControlPlaneConflictError, match="different input"):
        store.set_active_evidence(
            task_id=task_id,
            gate_key="requirements-gate",
            evidence_ids=[failing.evidence_id],
            expected_gate_version=4,
            actor="system",
            operation_key="select-pass",
        )
    stale = store.set_active_evidence(
        task_id=task_id,
        gate_key="requirements-gate",
        evidence_ids=[failing.evidence_id],
        expected_gate_version=4,
        actor="system",
        operation_key="select-fail",
    )
    assert stale.status == GateStatus.STALE
    assert stale.version == 5

    restarted = ControlPlaneStore(TaskStore(tasks.db_path))
    replayed_pass = restarted.evaluate(
        task_id=task_id,
        gate_key="requirements-gate",
        expected_gate_version=2,
        actor="system",
        operation_key="evaluate-pass",
    )
    assert replayed_pass.status == GateStatus.PASSED
    assert replayed_pass.version == 4
    assert restarted.get_gate(task_id, "requirements-gate").version == 5

    with pytest.raises(ControlPlaneConflictError, match="different input"):
        restarted.evaluate(
            task_id=task_id,
            gate_key="requirements-gate",
            expected_gate_version=5,
            actor="system",
            operation_key="evaluate-pass",
        )

    blocked = restarted.evaluate(
        task_id=task_id,
        gate_key="requirements-gate",
        expected_gate_version=5,
        actor="system",
        operation_key="evaluate-fail",
    )
    assert blocked.status == GateStatus.BLOCKED
    assert blocked.version == 7
    assert blocked.last_evaluation is not None
    assert blocked.last_evaluation.blocker_requirement_ids == ["review-approved"]


def test_active_evidence_must_match_the_configured_gate_scope(tmp_path):
    _, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)
    foreign = _evidence(
        task_id,
        evidence_id="evidence-foreign-commit",
        commit_sha=COMMIT_2,
    )
    store.register_evidence(foreign)

    with pytest.raises(ControlPlaneValidationError, match="does not match"):
        store.set_active_evidence(
            task_id=task_id,
            gate_key="requirements-gate",
            evidence_ids=[foreign.evidence_id],
            expected_gate_version=1,
            actor="system",
            operation_key="select-foreign",
        )
    assert store.get_gate(task_id, "requirements-gate").version == 1


def test_active_evidence_cannot_cross_task_boundaries(tmp_path):
    tasks, store, source_task_id = _stores(tmp_path)
    _configure_requirements_gate(store, source_task_id)
    evidence = _evidence(source_task_id)
    store.register_evidence(evidence)

    target = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Independent target task",
            kind="architecture",
        )
    )
    _configure_requirements_gate(store, target.task_id)

    with pytest.raises(ControlPlaneValidationError, match="does not belong"):
        store.set_active_evidence(
            task_id=target.task_id,
            gate_key="requirements-gate",
            evidence_ids=[evidence.evidence_id],
            expected_gate_version=1,
            actor="system",
            operation_key="select-cross-task",
        )
    target_gate = store.get_gate(target.task_id, "requirements-gate")
    assert target_gate is not None
    assert target_gate.version == 1
    assert target_gate.active_evidence_ids == []


@pytest.mark.parametrize(
    "operation_key",
    [
        "contains whitespace",
        "starts-with-$",
        "x" * 201,
    ],
)
def test_replayable_operations_reject_invalid_operation_keys(
    tmp_path,
    operation_key,
):
    _, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(store, task_id)

    with pytest.raises(ControlPlaneValidationError, match="Invalid operation_key"):
        store.set_active_evidence(
            task_id=task_id,
            gate_key="requirements-gate",
            evidence_ids=[],
            expected_gate_version=1,
            actor="system",
            operation_key=operation_key,
        )
    assert store.get_gate(task_id, "requirements-gate").version == 1


def test_inventory_invalidation_is_atomic_idempotent_and_restart_safe(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(
        store,
        task_id,
        stage_status=StageStatus.COMPLETED,
    )
    store.ensure_stage(
        task_id=task_id,
        stage_key="design",
        gate_key="design-gate",
        status=StageStatus.COMPLETED,
    )
    store.ensure_stage(
        task_id=task_id,
        stage_key="build",
        gate_key="build-gate",
        status=StageStatus.RUNNING,
    )
    store.ensure_stage(
        task_id=task_id,
        stage_key="blocked",
        gate_key="blocked-gate",
        status=StageStatus.BLOCKED,
    )
    store.ensure_stage(
        task_id=task_id,
        stage_key="review",
        gate_key="review-gate",
        status=StageStatus.NEEDS_REVIEW,
    )
    artifact = _artifact(task_id)
    store.register_artifact(artifact)
    _pass_requirements_gate(store, task_id)
    store.register_approval(_approval(task_id))

    inventory = ArtifactInventory(
        repository_id=REPOSITORY,
        ref=REF,
        commit_sha=COMMIT_2,
        artifacts=[],
    )
    receipt = store.invalidate_inventory(
        inventory,
        stage_dependents={
            "requirements": {"blocked", "design"},
            "design": {"build", "review"},
        },
        actor="reconciler",
        operation_key="invalidate-main-v2",
    )

    assert receipt.stale_approval_ids == ["approval-requirements"]
    assert receipt.stale_gate_keys == [f"{task_id}:requirements-gate"]
    assert receipt.reopened_stage_keys == [
        f"{task_id}:blocked",
        f"{task_id}:design",
        f"{task_id}:requirements",
        f"{task_id}:review",
    ]
    assert receipt.reconciliation_stage_keys == [f"{task_id}:build"]
    assert len(receipt.attention_item_ids) == 1
    attention = store.projection(task_id)["attention"]
    assert [item.item_id for item in attention] == receipt.attention_item_ids
    assert attention[0].context["stale_approval_ids"] == [
        "approval-requirements"
    ]
    assert attention[0].context["affected_stage_count"] == 5
    assert store.get_approval("approval-requirements").status.value == "stale"
    assert store.get_gate(task_id, "requirements-gate").status == GateStatus.STALE
    assert store.get_stage(task_id, "requirements").status == StageStatus.READY
    assert store.get_stage(task_id, "design").status == StageStatus.READY
    assert store.get_stage(task_id, "blocked").status == StageStatus.READY
    assert store.get_stage(task_id, "review").status == StageStatus.READY
    assert (
        store.get_stage(task_id, "build").status
        == StageStatus.RECONCILIATION_REQUIRED
    )

    event_count = len(store.events(task_id))
    restarted = ControlPlaneStore(TaskStore(tasks.db_path))
    replay = restarted.invalidate_inventory(
        inventory,
        stage_dependents={
            "requirements": {"blocked", "design"},
            "design": {"build", "review"},
        },
        actor="reconciler",
        operation_key="invalidate-main-v2",
    )
    assert replay.replayed is True
    assert replay.event_ids == receipt.event_ids
    assert replay.attention_item_ids == receipt.attention_item_ids
    assert len(restarted.events(task_id)) == event_count

    with pytest.raises(ControlPlaneConflictError, match="different input"):
        restarted.invalidate_inventory(
            ArtifactInventory(
                repository_id=REPOSITORY,
                ref=REF,
                commit_sha=COMMIT_1,
                artifacts=[],
            ),
            stage_dependents={
                "requirements": {"blocked", "design"},
                "design": {"build", "review"},
            },
            actor="reconciler",
            operation_key="invalidate-main-v2",
        )


def test_inventory_invalidation_isolates_artifacts_across_projects(tmp_path):
    tasks, store, alpha_task_id = _stores(tmp_path, project_id="alpha")
    _configure_requirements_gate(
        store,
        alpha_task_id,
        stage_status=StageStatus.COMPLETED,
    )
    beta = tasks.create(
        CreateTaskRequest(
            project_id="beta",
            title="Separate project on the same repository",
            kind="architecture",
        )
    )
    store.ensure_stage(
        task_id=beta.task_id,
        stage_key="requirements",
        gate_key="requirements-gate",
        status=StageStatus.COMPLETED,
    )
    store.configure_gate(
        task_id=beta.task_id,
        gate_key="requirements-gate",
        stage_key="requirements",
        requirements=[_requirement()],
    )

    alpha_artifact = _artifact(
        alpha_task_id,
        artifact_id="artifact-alpha",
        path="docs/alpha.md",
        sha256="a" * 64,
        project_id="alpha",
    )
    beta_artifact = _artifact(
        beta.task_id,
        artifact_id="artifact-beta",
        path="docs/beta.md",
        sha256="b" * 64,
        project_id="beta",
    )
    store.register_artifact(alpha_artifact)
    store.register_artifact(beta_artifact)
    store.register_approval(
        _approval(
            alpha_task_id,
            approval_id="approval-alpha",
            path="docs/alpha.md",
            sha256="a" * 64,
            project_id="alpha",
        )
    )
    store.register_approval(
        _approval(
            beta.task_id,
            approval_id="approval-beta",
            path="docs/beta.md",
            sha256="b" * 64,
            project_id="beta",
        )
    )

    receipt = store.invalidate_inventory(
        ArtifactInventory(
            repository_id=REPOSITORY,
            ref=REF,
            commit_sha=COMMIT_1,
            artifacts=[
                ArtifactChange(
                    repository_id=REPOSITORY,
                    ref=REF,
                    commit_sha=COMMIT_1,
                    path="docs/alpha.md",
                    sha256="c" * 64,
                ),
                ArtifactChange(
                    repository_id=REPOSITORY,
                    ref=REF,
                    commit_sha=COMMIT_1,
                    path="docs/beta.md",
                    sha256="b" * 64,
                ),
            ],
        ),
        stage_dependents={},
        actor="reconciler",
        operation_key="invalidate-shared-repository",
    )

    assert receipt.stale_approval_ids == ["approval-alpha"]
    assert len(receipt.attention_item_ids) == 1
    assert store.get_approval("approval-alpha").status == ApprovalStatus.STALE
    assert store.get_approval("approval-beta").status == ApprovalStatus.ACTIVE
    assert store.get_stage(alpha_task_id, "requirements").status == StageStatus.READY
    assert store.get_stage(beta.task_id, "requirements").status == StageStatus.COMPLETED
    assert len(store.projection(alpha_task_id)["attention"]) == 1
    assert store.projection(beta.task_id)["attention"] == []
    invalidation_events = [
        event
        for event in store.events(alpha_task_id)
        if event.event_key.startswith("invalidate-shared-repository:")
    ]
    assert invalidation_events
    assert {event.project_id for event in invalidation_events} == {"alpha"}


def test_inventory_invalidation_rolls_back_every_projection_on_event_failure(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    _configure_requirements_gate(
        store,
        task_id,
        stage_status=StageStatus.COMPLETED,
    )
    store.register_artifact(_artifact(task_id))
    _pass_requirements_gate(store, task_id)
    store.register_approval(_approval(task_id))

    with tasks._transaction() as db:
        db.execute(
            """
            CREATE TRIGGER fail_gate_invalidation
            BEFORE INSERT ON control_events
            WHEN NEW.event_type = 'gate.approval_invalidated'
            BEGIN
                SELECT RAISE(ABORT, 'forced event failure');
            END;
            """
        )

    inventory = ArtifactInventory(
        repository_id=REPOSITORY,
        ref=REF,
        commit_sha=COMMIT_2,
        artifacts=[],
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced event failure"):
        store.invalidate_inventory(
            inventory,
            stage_dependents={},
            actor="reconciler",
            operation_key="invalidate-rollback",
        )

    assert store.get_approval("approval-requirements").status.value == "active"
    assert store.get_gate(task_id, "requirements-gate").status == GateStatus.PASSED
    assert store.get_stage(task_id, "requirements").status == StageStatus.COMPLETED
    assert all(
        event.event_key != "invalidate-rollback:approval:approval-requirements"
        for event in store.events(task_id)
    )
    with tasks._connect() as db:
        assert db.execute(
            "SELECT 1 FROM control_operations WHERE operation_key = 'invalidate-rollback'"
        ).fetchone() is None


def test_inventory_rejects_partial_scope_entries():
    with pytest.raises(ValueError, match="must match"):
        ArtifactInventory(
            repository_id=REPOSITORY,
            ref=REF,
            commit_sha=COMMIT_2,
            artifacts=[
                ArtifactChange(
                    repository_id=REPOSITORY,
                    ref="refs/heads/feature",
                    commit_sha=COMMIT_2,
                    path="docs/requirements.md",
                    sha256=hashlib.sha256(b"new").hexdigest(),
                )
            ],
        )
