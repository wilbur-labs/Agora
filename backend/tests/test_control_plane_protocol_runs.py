from __future__ import annotations

import hashlib
import json

import pytest

from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)
from agora.protocol.agent_adapter import TerminalRunnerObservation, adapt_agent_output
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import (
    ContextPack,
    Evidence,
    GateRequirement,
    HandoffPack,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


COMMIT = "1" * 40
REPOSITORY = "agora-repository"
REF = "refs/heads/main"


def _stores(
    tmp_path,
    *,
    extra_requirements: list[GateRequirement] | None = None,
) -> tuple[TaskStore, ControlPlaneStore, str]:
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Run one frozen protocol Stage",
            kind="implementation",
        )
    )
    store = ControlPlaneStore(tasks)
    store.ensure_task_state(task.task_id)
    inventory = StageInventory.model_validate(
        seal_model_payload(
            StageInventory,
            {
                "schema_version": "1.0",
                "inventory_id": f"inventory:{task.task_id}",
                "task_id": task.task_id,
                "project_id": task.project_id,
                "plan_id": f"plan:{task.task_id}",
                "methodology_id": "protocol_test",
                "methodology_version": "1.0",
                "methodology_sha256": "a" * 64,
                "provisional": True,
                "contract": None,
                "groups": [
                    {
                        "group_key": "protocol_test",
                        "sequence": 1,
                        "title": "Protocol test",
                        "stages": [
                            {
                                "stage_key": "implementation",
                                "gate_key": "implementation-gate",
                                "sequence": 1,
                                "title": "Implementation",
                                "role": "implementer",
                                "runtime": "codex",
                            }
                        ],
                    }
                ],
            },
        )
    )
    store.ensure_stage_inventory(inventory, actor="agora")
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="implementation",
        actor="agora",
        operation_key=f"activate:{task.task_id}",
    )
    store.configure_gate(
        task_id=task.task_id,
        gate_key="implementation-gate",
        stage_key="implementation",
        requirements=[
            GateRequirement(
                requirement_id="tests-pass",
                title="Required tests pass",
                repository_id=REPOSITORY,
                ref=REF,
                commit_sha=COMMIT,
                evidence_kind="test-result",
                priority=10,
                failure_action="Run the required tests and record passing Evidence.",
            )
        ]
        + (extra_requirements or []),
    )
    return tasks, store, task.task_id


def _context(
    task_id: str,
    *,
    run_id: str = "run-protocol-1",
    input_artifacts: list[dict] | None = None,
) -> ContextPack:
    payload = {
        "schema_version": "1.0",
        "pack_id": f"context-{run_id}",
        "project_id": "agora",
        "task_id": task_id,
        "stage_key": "implementation",
        "run_id": run_id,
        "generated_at": "2026-07-20T00:00:00+00:00",
        "stage_contract": {
            "contract_id": "contract-implementation",
            "title": "Implement the bounded change",
            "objective": "Produce a versioned implementation and test Evidence.",
            "completion_conditions": ["The formal Gate passes."],
        },
        "input_artifacts": input_artifacts or [],
        "required_outputs": [
            {
                "output_id": "artifact-implementation",
                "kind": "implementation",
                "required": True,
            }
        ],
        "forbidden_constraints": [
            "Do not advance the Stage from the Agent Handoff directly."
        ],
        "policies": [],
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": {
            "max_seconds": 600,
            "max_output_bytes": 1_000_000,
            "max_model_tokens": 10_000,
            "max_cost_usd": None,
        },
    }
    return ContextPack.model_validate(seal_model_payload(ContextPack, payload))


def _handoff_payload(
    context: ContextPack,
    *,
    stage_result: str = "succeeded",
    evidence_status: str | None = "passed",
    evidence_ref: str = REF,
) -> dict:
    content = "reviewed implementation output"
    artifact = {
        "schema_version": "1.0",
        "artifact_id": "artifact-implementation",
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage_key": context.stage_key,
        "producer": {
            "runtime": "codex",
            "run_id": context.run_id,
            "stage_key": context.stage_key,
        },
        "kind": "implementation",
        "storage": "managed",
        "version": 1,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "media_type": "text/plain",
        "content": content,
        "location": None,
        "created_at": "2026-07-20T00:01:00+00:00",
    }
    evidence = []
    if evidence_status is not None:
        evidence.append(
            {
                "schema_version": "1.0",
                "evidence_id": "evidence-tests",
                "project_id": context.project_id,
                "task_id": context.task_id,
                "stage_key": context.stage_key,
                "producer": artifact["producer"],
                "repository_id": REPOSITORY,
                "ref": evidence_ref,
                "commit_sha": COMMIT,
                "requirement_id": "tests-pass",
                "kind": "test-result",
                "status": evidence_status,
                "artifact_versions": [],
                "summary": f"Required tests: {evidence_status}.",
                "observed_at": "2026-07-20T00:02:00+00:00",
                "details": {},
            }
        )
    blocker_ids = []
    if stage_result == "blocked":
        blocker_ids = ["agent-blocker"]
    payload = {
        "schema_version": "1.0",
        "pack_id": f"handoff-{context.run_id}",
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage_key": context.stage_key,
        "run_id": context.run_id,
        "producer": artifact["producer"],
        "input_artifacts": [
            item.model_dump(mode="json") for item in context.input_artifacts
        ],
        "required_outputs": [
            item.model_dump(mode="json") for item in context.required_outputs
        ],
        "forbidden_constraints": list(context.forbidden_constraints),
        "stage_result": stage_result,
        "output_artifacts": [artifact],
        "evidence": evidence,
        "unresolved_questions": [],
        "native_state_snapshot": None,
        "memory_candidates": [],
        "blocker_requirement_ids": blocker_ids,
        "suggested_next_action": "Agent-supplied suggestion must not be authoritative.",
    }
    return seal_model_payload(HandoffPack, payload)


def _adapt(context: ContextPack, handoff: dict | str, *, exit_code: int = 0):
    raw = handoff if isinstance(handoff, str) else json.dumps(handoff)
    return adapt_agent_output(
        context,
        TerminalRunnerObservation(
            run_id=context.run_id,
            process_started=True,
            exit_code=exit_code,
            transport_status="completed",
        ),
        raw,
    )


def _start(store: ControlPlaneStore, context: ContextPack):
    return store.start_protocol_run(
        context,
        gate_key="implementation-gate",
        actor="agora",
        operation_key=f"start:{context.run_id}",
    )


def _previous_evidence(
    task_id: str,
    *,
    evidence_id: str,
    requirement_id: str,
    kind: str,
    status: str = "passed",
) -> Evidence:
    return Evidence.model_validate(
        {
            "schema_version": "1.0",
            "evidence_id": evidence_id,
            "project_id": "agora",
            "task_id": task_id,
            "stage_key": "implementation",
            "producer": {
                "runtime": "claude",
                "run_id": "run-prior-review",
                "stage_key": "implementation",
            },
            "repository_id": REPOSITORY,
            "ref": REF,
            "commit_sha": COMMIT,
            "requirement_id": requirement_id,
            "kind": kind,
            "status": status,
            "artifact_versions": [],
            "summary": f"Prior {kind}: {status}.",
            "observed_at": "2026-07-19T23:59:00+00:00",
            "details": {},
        }
    )


def test_context_start_is_sealed_restart_safe_and_operation_idempotent(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    context = _context(task_id)

    started = _start(store, context)
    replayed = _start(store, context)
    restarted = ControlPlaneStore(TaskStore(tasks.db_path))

    assert started == replayed
    assert started.context_pack == context
    assert started.protocol_state is None
    assert restarted.get_protocol_run(context.run_id) == started
    assert restarted.get_stage(task_id, "implementation").status == StageStatus.RUNNING
    assert [event.event_type for event in restarted.events(task_id)].count(
        "run.context_sealed"
    ) == 1


def test_context_start_rejects_unregistered_input_without_partial_state(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(
        task_id,
        input_artifacts=[
            {
                "artifact_id": "artifact-missing",
                "version": 1,
                "sha256": "a" * 64,
                "kind": "requirements",
                "location": None,
            }
        ],
    )

    with pytest.raises(ControlPlaneValidationError, match="not registered"):
        _start(store, context)

    assert store.get_protocol_run(context.run_id) is None
    assert store.get_stage(task_id, "implementation").status == StageStatus.READY


def test_passing_handoff_registers_ledger_and_gate_before_completing_stage(tmp_path):
    tasks, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context), exit_code=7)

    receipt = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:run-protocol-1",
    )
    restarted = ControlPlaneStore(TaskStore(tasks.db_path))

    assert receipt.run.protocol_state.process_exit_code == 7
    assert receipt.run.handoff_pack == result.handoff_pack
    assert receipt.gate.status == GateStatus.PASSED
    assert receipt.stage.status == StageStatus.COMPLETED
    assert receipt.active_evidence_ids == ["evidence-tests"]
    assert restarted.get_artifact("artifact-implementation", 1) is not None
    assert restarted.get_evidence("evidence-tests") is not None
    assert restarted.get_protocol_run(context.run_id) == receipt.run
    event_types = [event.event_type for event in restarted.projection(task_id)["events"]]
    assert event_types.index("run.context_sealed") < event_types.index(
        "artifact.registered"
    )
    assert event_types.index("artifact.registered") < event_types.index(
        "run.settled"
    )


def test_semantic_success_cannot_bypass_missing_formal_gate_evidence(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context, evidence_status=None))

    receipt = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:missing-evidence",
    )

    assert result.protocol_state.semantic_stage_result.value == "succeeded"
    assert receipt.gate.status == GateStatus.BLOCKED
    assert receipt.stage.status == StageStatus.BLOCKED
    assert receipt.gate.last_evaluation.next_safe_action == (
        "Run the required tests and record passing Evidence."
    )
    assert receipt.gate.last_evaluation.next_safe_action != (
        result.handoff_pack.suggested_next_action
    )


def test_agent_blocker_prevents_stage_completion_even_when_gate_passes(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context, stage_result="blocked"))

    receipt = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:agent-blocked",
    )

    assert receipt.gate.status == GateStatus.PASSED
    assert receipt.stage.status == StageStatus.BLOCKED


def test_settlement_preserves_active_evidence_from_other_gate_sources(tmp_path):
    review_requirement = GateRequirement(
        requirement_id="review-pass",
        title="Independent review passes",
        repository_id=REPOSITORY,
        ref=REF,
        commit_sha=COMMIT,
        evidence_kind="independent-review",
        priority=20,
        failure_action="Run independent review.",
    )
    _, store, task_id = _stores(
        tmp_path,
        extra_requirements=[review_requirement],
    )
    review = _previous_evidence(
        task_id,
        evidence_id="evidence-review-prior",
        requirement_id="review-pass",
        kind="independent-review",
    )
    store.register_evidence(review)
    gate = store.get_gate(task_id, "implementation-gate")
    gate = store.set_active_evidence(
        task_id=task_id,
        gate_key="implementation-gate",
        evidence_ids=[review.evidence_id],
        expected_gate_version=gate.version,
        actor="agora",
        operation_key="select:prior-review",
    )
    assert gate.active_evidence_ids == [review.evidence_id]
    context = _context(task_id)
    _start(store, context)

    receipt = store.settle_protocol_run(
        _adapt(context, _handoff_payload(context)),
        actor="agora",
        operation_key="settle:preserve-review",
    )

    assert receipt.active_evidence_ids == [
        "evidence-review-prior",
        "evidence-tests",
    ]
    assert receipt.gate.status == GateStatus.PASSED
    assert receipt.stage.status == StageStatus.COMPLETED


def test_current_handoff_replaces_prior_evidence_for_same_requirement(tmp_path):
    _, store, task_id = _stores(tmp_path)
    prior = _previous_evidence(
        task_id,
        evidence_id="evidence-tests-prior",
        requirement_id="tests-pass",
        kind="test-result",
        status="failed_product",
    )
    store.register_evidence(prior)
    gate = store.get_gate(task_id, "implementation-gate")
    store.set_active_evidence(
        task_id=task_id,
        gate_key="implementation-gate",
        evidence_ids=[prior.evidence_id],
        expected_gate_version=gate.version,
        actor="agora",
        operation_key="select:prior-test",
    )
    context = _context(task_id)
    _start(store, context)

    receipt = store.settle_protocol_run(
        _adapt(context, _handoff_payload(context)),
        actor="agora",
        operation_key="settle:replace-test",
    )

    assert receipt.active_evidence_ids == ["evidence-tests"]
    assert receipt.gate.status == GateStatus.PASSED


def test_failed_handoff_is_recorded_without_churning_formal_gate(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context, stage_result="failed"))

    receipt = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:semantic-failed",
    )

    assert receipt.run.handoff_pack == result.handoff_pack
    assert receipt.gate.status == GateStatus.PENDING
    assert receipt.gate.version == 1
    assert receipt.active_evidence_ids == []
    assert receipt.stage.status == StageStatus.FAILED
    assert store.get_evidence("evidence-tests") is not None


def test_protocol_failure_is_durable_and_does_not_rewrite_gate_evidence(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, "not-json")

    receipt = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:protocol-failed",
    )

    assert receipt.run.handoff_pack is None
    assert receipt.run.attention_required is True
    assert receipt.run.attention_item_id is not None
    assert receipt.run.adapter_error_code.value == "handoff_json_invalid"
    assert receipt.gate.status == GateStatus.PENDING
    assert receipt.stage.status == StageStatus.BLOCKED
    projection = store.projection(task_id)
    assert [item.item_id for item in projection["attention"]] == [
        receipt.run.attention_item_id
    ]
    assert projection["attention"][0].context["protocol_run_id"] == context.run_id


def test_handoff_scope_failure_rolls_back_artifacts_run_gate_and_stage(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context, evidence_ref="refs/heads/other"))

    with pytest.raises(ControlPlaneValidationError, match="requirement scope"):
        store.settle_protocol_run(
            result,
            actor="agora",
            operation_key="settle:scope-mismatch",
        )

    assert store.get_protocol_run(context.run_id).settled_at is None
    assert store.get_artifact("artifact-implementation", 1) is None
    assert store.get_evidence("evidence-tests") is None
    assert store.get_gate(task_id, "implementation-gate").status == GateStatus.PENDING
    assert store.get_stage(task_id, "implementation").status == StageStatus.RUNNING


def test_settlement_operation_replays_but_conflicting_input_fails_closed(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    result = _adapt(context, _handoff_payload(context))

    first = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:replay",
    )
    replay = store.settle_protocol_run(
        result,
        actor="agora",
        operation_key="settle:replay",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.run == first.run
    with pytest.raises(ControlPlaneConflictError, match="different input"):
        store.settle_protocol_run(
            result.model_copy(update={"attention_required": True}),
            actor="agora",
            operation_key="settle:replay",
        )


def test_protocol_retry_reopens_stage_and_stales_a_passed_gate(tmp_path):
    _, store, task_id = _stores(tmp_path)
    context = _context(task_id)
    _start(store, context)
    settled = store.settle_protocol_run(
        _adapt(
            context,
            _handoff_payload(
                context,
                stage_result="blocked",
                evidence_status="passed",
            ),
        ),
        actor="agora",
        operation_key="settle:blocked-with-passed-gate",
    )
    assert settled.stage.status == StageStatus.BLOCKED
    assert settled.gate.status == GateStatus.PASSED

    retried = store.prepare_protocol_retry(
        task_id=task_id,
        stage_key="implementation",
        actor="user",
        operation_key="retry:run-protocol-1",
    )
    replay = store.prepare_protocol_retry(
        task_id=task_id,
        stage_key="implementation",
        actor="user",
        operation_key="retry:run-protocol-1",
    )

    assert retried.status == StageStatus.READY
    assert replay == retried
    assert store.get_gate(task_id, "implementation-gate").status == GateStatus.STALE
