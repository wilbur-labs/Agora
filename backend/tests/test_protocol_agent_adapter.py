from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from agora.orchestration.protocol_adapter import adapt_runtime_result
from agora.orchestration.runtime import RuntimeResult
from agora.protocol.agent_adapter import (
    AdapterErrorCode,
    TerminalRunnerObservation,
    adapt_agent_output,
)
from agora.protocol.hashing import native_snapshot_id, seal_model_payload
from agora.protocol.models import ContextPack, HandoffPack, NativeStateSnapshot


def _context(
    *,
    max_output_bytes: int = 1_000_000,
    required_outputs: list[dict] | None = None,
) -> ContextPack:
    payload = {
        "schema_version": "1.0",
        "pack_id": "context-run-adapter-1",
        "project_id": "agora",
        "task_id": "task-adapter",
        "stage_key": "implementation",
        "run_id": "run-adapter-1",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "stage_contract": {
            "contract_id": "contract-adapter",
            "title": "Integrate the agent adapter",
            "objective": "Normalize runner facts and validate a Handoff Pack.",
            "completion_conditions": ["Protocol dimensions remain independent."],
        },
        "input_artifacts": [],
        "required_outputs": required_outputs or [],
        "forbidden_constraints": [
            "Do not infer semantic success from exit code.",
            "Do not let an Agent write authoritative Stage state.",
        ],
        "policies": [],
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": {
            "max_seconds": 600,
            "max_output_bytes": max_output_bytes,
            "max_model_tokens": 10_000,
            "max_cost_usd": None,
        },
    }
    return ContextPack.model_validate(seal_model_payload(ContextPack, payload))


def _handoff_payload(
    context: ContextPack,
    *,
    stage_result: str = "succeeded",
) -> dict:
    payload = {
        "schema_version": "1.0",
        "pack_id": "handoff-run-adapter-1",
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage_key": context.stage_key,
        "run_id": context.run_id,
        "producer": {
            "runtime": "codex",
            "run_id": context.run_id,
            "stage_key": context.stage_key,
        },
        "input_artifacts": [
            item.model_dump(mode="json") for item in context.input_artifacts
        ],
        "required_outputs": [
            item.model_dump(mode="json") for item in context.required_outputs
        ],
        "forbidden_constraints": list(context.forbidden_constraints),
        "stage_result": stage_result,
        "output_artifacts": [],
        "evidence": [],
        "unresolved_questions": [],
        "native_state_snapshot": None,
        "memory_candidates": [],
        "blocker_requirement_ids": (
            ["implementation-blocked"] if stage_result == "blocked" else []
        ),
        "suggested_next_action": (
            "Resolve the implementation blocker."
            if stage_result == "blocked"
            else "Evaluate the formal Gate."
        ),
    }
    return seal_model_payload(HandoffPack, payload)


def _observation(
    *,
    exit_code: int | None = 0,
    process_started: bool = True,
    timed_out: bool = False,
    cancelled: bool = False,
    interrupted: bool = False,
    transport_status: str = "completed",
) -> TerminalRunnerObservation:
    return TerminalRunnerObservation.model_validate(
        {
            "run_id": "run-adapter-1",
            "process_started": process_started,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "interrupted": interrupted,
            "transport_status": transport_status,
        }
    )


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def test_exit_zero_needs_a_valid_semantic_handoff_for_success():
    context = _context()

    accepted = adapt_agent_output(
        context,
        _observation(exit_code=0),
        _json(_handoff_payload(context)),
    )
    blocked = adapt_agent_output(
        context,
        _observation(exit_code=0),
        _json(_handoff_payload(context, stage_result="blocked")),
    )
    invalid = adapt_agent_output(context, _observation(exit_code=0), "not-json")

    assert accepted.protocol_state.semantic_stage_result.value == "succeeded"
    assert accepted.protocol_state.schema_status.value == "valid"
    assert blocked.protocol_state.semantic_stage_result.value == "blocked"
    assert invalid.protocol_state.semantic_stage_result.value == "blocked"
    assert invalid.protocol_state.schema_status.value == "protocol_failed"
    assert invalid.error_code == AdapterErrorCode.HANDOFF_JSON_INVALID
    assert invalid.attention_required is True


def test_process_exit_code_remains_independent_from_semantic_result():
    context = _context()
    result = adapt_agent_output(
        context,
        _observation(exit_code=9),
        _json(_handoff_payload(context)),
    )

    assert result.protocol_state.process_exit_code == 9
    assert result.protocol_state.semantic_stage_result.value == "succeeded"


def test_cli_runtime_result_bridge_preserves_facts_before_handoff_validation():
    context = _context()

    accepted = adapt_runtime_result(
        context,
        RuntimeResult(0, _json(_handoff_payload(context)), ""),
    )
    timed_out = adapt_runtime_result(
        context,
        RuntimeResult(
            1,
            _json(_handoff_payload(context)),
            "timeout",
            timed_out=True,
        ),
    )
    interrupted = adapt_runtime_result(context, RuntimeResult(None, "", "lost"))
    cancelled = adapt_runtime_result(
        context,
        RuntimeResult(None, "", "cancelled"),
        cancelled=True,
    )

    assert accepted.protocol_state.process_status.value == "exited"
    assert accepted.protocol_state.transport_status.value == "completed"
    assert accepted.protocol_state.semantic_stage_result.value == "succeeded"
    assert timed_out.protocol_state.process_status.value == "timed_out"
    assert timed_out.protocol_state.transport_status.value == "failed"
    assert timed_out.handoff_pack is None
    assert interrupted.protocol_state.process_status.value == "interrupted"
    assert interrupted.protocol_state.transport_status.value == "failed"
    assert interrupted.error_code == AdapterErrorCode.PROCESS_INTERRUPTED
    assert cancelled.protocol_state.process_status.value == "cancelled"
    assert cancelled.protocol_state.semantic_stage_result.value == "cancelled"
    assert cancelled.error_code == AdapterErrorCode.PROCESS_CANCELLED


@pytest.mark.parametrize(
    ("stage_result", "semantic", "error"),
    [
        ("failed", "failed", None),
        ("cancelled", "blocked", AdapterErrorCode.HANDOFF_PROCESS_MISMATCH),
    ],
)
def test_clean_exit_handles_failed_or_contradictory_cancelled_handoffs(
    stage_result,
    semantic,
    error,
):
    context = _context()

    result = adapt_agent_output(
        context,
        _observation(),
        _json(_handoff_payload(context, stage_result=stage_result)),
    )

    assert result.protocol_state.semantic_stage_result.value == semantic
    assert result.error_code == error
    if error:
        assert result.protocol_state.schema_status.value == "protocol_failed"
        assert result.attention_required is True
    else:
        assert result.protocol_state.schema_status.value == "valid"
        assert result.handoff_pack is not None


@pytest.mark.parametrize("opening", ["```json", "```"])
def test_one_whole_document_fence_repair_is_recorded(opening):
    context = _context()
    raw = f"{opening}\n{_json(_handoff_payload(context))}\n```"

    result = adapt_agent_output(context, _observation(), raw)

    assert result.protocol_state.schema_status.value == "repaired"
    assert result.protocol_state.repair_attempts == 1
    assert result.handoff_pack is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("prefix " + "{}" + " suffix", AdapterErrorCode.HANDOFF_JSON_INVALID),
        ("", AdapterErrorCode.HANDOFF_MISSING),
        (None, AdapterErrorCode.HANDOFF_MISSING),
        (b"\xff", AdapterErrorCode.HANDOFF_ENCODING_INVALID),
        ('{"value":NaN}', AdapterErrorCode.HANDOFF_JSON_INVALID),
    ],
)
def test_parser_fails_closed_without_extracting_from_prose(raw, expected):
    result = adapt_agent_output(_context(), _observation(), raw)

    assert result.handoff_pack is None
    assert result.error_code == expected
    assert result.protocol_state.schema_status.value == "protocol_failed"


def test_parser_rejects_duplicate_json_keys():
    context = _context()
    raw = _json(_handoff_payload(context)).replace(
        '"project_id":"agora",',
        '"project_id":"agora","project_id":"agora",',
        1,
    )

    result = adapt_agent_output(context, _observation(), raw)

    assert result.error_code == AdapterErrorCode.HANDOFF_JSON_INVALID
    assert result.protocol_state.repair_attempts == 1


def test_handoff_is_bound_to_context_identity_and_echoed_contract():
    context = _context()
    wrong_task = _handoff_payload(context)
    wrong_task["task_id"] = "another-task"
    wrong_task = seal_model_payload(
        HandoffPack,
        {key: value for key, value in wrong_task.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(wrong_task))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.protocol_state.semantic_stage_result.value == "blocked"
    assert result.handoff_pack is None


def test_handoff_must_preserve_echoed_context_list_order():
    context = _context()
    reordered = _handoff_payload(context)
    reordered["forbidden_constraints"] = list(
        reversed(reordered["forbidden_constraints"])
    )
    reordered = seal_model_payload(
        HandoffPack,
        {key: value for key, value in reordered.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(reordered))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.protocol_state.schema_status.value == "protocol_failed"


def test_handoff_rejects_output_artifact_scope_spoofing():
    context = _context()
    payload = _handoff_payload(context)
    content = "reviewed adapter output"
    payload["output_artifacts"] = [
        {
            "schema_version": "1.0",
            "artifact_id": "adapter-output",
            "project_id": "another-project",
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "producer": payload["producer"],
            "kind": "report",
            "storage": "managed",
            "version": 1,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "media_type": "text/plain",
            "content": content,
            "location": None,
            "created_at": "2026-07-20T00:01:00+00:00",
        }
    ]
    payload = seal_model_payload(
        HandoffPack,
        {key: value for key, value in payload.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(payload))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.attention_required is True


def test_handoff_rejects_evidence_scope_spoofing():
    context = _context()
    payload = _handoff_payload(context, stage_result="blocked")
    payload["evidence"] = [
        {
            "schema_version": "1.0",
            "evidence_id": "evidence-spoofed-project",
            "project_id": "another-project",
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "producer": payload["producer"],
            "repository_id": "agora-repository",
            "ref": "refs/heads/main",
            "commit_sha": "a" * 40,
            "requirement_id": "implementation-blocked",
            "kind": "test-result",
            "status": "failed_product",
            "artifact_versions": [],
            "summary": "The evidence belongs to another project.",
            "observed_at": "2026-07-20T00:02:00+00:00",
            "details": {},
        }
    ]
    payload = seal_model_payload(
        HandoffPack,
        {key: value for key, value in payload.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(payload))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.handoff_pack is None


def test_handoff_rejects_native_snapshot_project_spoofing():
    context = _context()
    payload = _handoff_payload(context)
    identity = {
        "project_id": "another-project",
        "repository_id": "agora-repository",
        "canonical_ref": "refs/heads/main",
        "commit_sha": "a" * 40,
        "native_state_sha256": "b" * 64,
        "reconciliation_rule_version": "v1",
        "methodology": "agora-aidlc-foundation",
    }
    payload["native_state_snapshot"] = seal_model_payload(
        NativeStateSnapshot,
        {
            "schema_version": "1.0",
            "snapshot_id": native_snapshot_id(identity),
            **identity,
            "declared_native_stage": "implementation",
            "verified_native_stage": "implementation",
            "reconciliation_status": "verified",
            "artifacts": [],
            "approval_ids": [],
            "conflicts": [],
            "gate_recommendation": {"decision": "pass", "reasons": []},
        },
    )
    payload = seal_model_payload(
        HandoffPack,
        {key: value for key, value in payload.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(payload))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.handoff_pack is None


def test_handoff_rejects_changed_required_output_echo():
    context = _context()
    changed = _handoff_payload(context)
    changed["required_outputs"] = [
        {
            "output_id": "unrequested-output",
            "kind": "report",
            "schema_uri": None,
            "required": True,
        }
    ]
    changed = seal_model_payload(
        HandoffPack,
        {key: value for key, value in changed.items() if key != "content_sha256"},
    )

    result = adapt_agent_output(context, _observation(), _json(changed))

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH


def test_succeeded_handoff_must_produce_every_required_output():
    context = _context(
        required_outputs=[
            {
                "output_id": "required-report",
                "kind": "report",
                "required": True,
            }
        ]
    )

    result = adapt_agent_output(
        context,
        _observation(),
        _json(_handoff_payload(context)),
    )

    assert result.error_code == AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH
    assert result.handoff_pack is None
    assert result.attention_required is True


@pytest.mark.parametrize(
    ("observation", "process", "semantic", "error"),
    [
        (
            {
                "process_started": False,
                "exit_code": None,
                "transport_status": "failed",
            },
            "launch_failed",
            "failed",
            AdapterErrorCode.PROCESS_LAUNCH_FAILED,
        ),
        (
            {
                "process_started": True,
                "exit_code": 1,
                "timed_out": True,
                "transport_status": "failed",
            },
            "timed_out",
            "failed",
            AdapterErrorCode.PROCESS_TIMED_OUT,
        ),
        (
            {
                "process_started": True,
                "exit_code": None,
                "cancelled": True,
                "transport_status": "failed",
            },
            "cancelled",
            "cancelled",
            AdapterErrorCode.PROCESS_CANCELLED,
        ),
        (
            {
                "process_started": True,
                "exit_code": None,
                "interrupted": True,
                "transport_status": "failed",
            },
            "interrupted",
            "failed",
            AdapterErrorCode.PROCESS_INTERRUPTED,
        ),
        (
            {
                "process_started": True,
                "exit_code": 0,
                "transport_status": "failed",
            },
            "exited",
            "failed",
            AdapterErrorCode.TRANSPORT_FAILED,
        ),
    ],
)
def test_abnormal_runner_facts_do_not_parse_or_promote_handoff(
    observation,
    process,
    semantic,
    error,
):
    facts = TerminalRunnerObservation.model_validate(
        {"run_id": "run-adapter-1", **observation}
    )

    result = adapt_agent_output(
        _context(),
        facts,
        _json(_handoff_payload(_context())),
    )

    assert result.protocol_state.process_status.value == process
    assert result.protocol_state.semantic_stage_result.value == semantic
    assert result.protocol_state.schema_status.value == "pending"
    assert result.error_code == error
    assert result.handoff_pack is None


def test_terminal_observation_rejects_contradictory_facts():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _observation(
            exit_code=None,
            timed_out=True,
            cancelled=True,
            transport_status="failed",
        )

    with pytest.raises(ValidationError, match="requires an exit code"):
        _observation(exit_code=None)


def test_context_output_budget_is_enforced_before_parsing():
    result = adapt_agent_output(
        _context(max_output_bytes=1024),
        _observation(),
        "x" * 1025,
    )

    assert result.error_code == AdapterErrorCode.HANDOFF_TOO_LARGE
    assert result.protocol_state.schema_status.value == "protocol_failed"


def test_hash_tampering_is_a_protocol_failure():
    context = _context()
    payload = _handoff_payload(context)
    payload["suggested_next_action"] = "Trust the tampered payload."

    result = adapt_agent_output(context, _observation(), _json(payload))

    assert result.error_code == AdapterErrorCode.HANDOFF_SCHEMA_INVALID
    assert result.protocol_state.semantic_stage_result.value == "blocked"
